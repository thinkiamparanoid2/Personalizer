import secrets
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import command as cmdbar, config, files, jobs, media, notes, osctl, signing, tools
from .paths import resolve_allowed
from .util import ffmpeg, media_codecs, safe_name, unique_path

cfg = config.load()
TOKEN = cfg["token"].encode()
if len(TOKEN) < 32:
    raise SystemExit("config.json token missing or too short; delete it to regenerate.")
STREAM_SECRET = cfg["stream_secret"]


def _bearer_ok(request: Request):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(token.encode(), TOKEN)


def _unauthorized():
    return HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})


def require_token(request: Request):
    if not _bearer_ok(request):
        raise _unauthorized()


def _resolve_file(path):
    try:
        real = resolve_allowed(path)
    except PermissionError:
        raise HTTPException(403, "Forbidden")
    except (OSError, ValueError):
        raise HTTPException(404, "Not found")
    if not real.is_file():
        raise HTTPException(404, "Not found")
    return real


@asynccontextmanager
async def lifespan(_app):
    # Background helpers; a failure in one must not stop the API.
    from . import snip, watcher
    import threading
    warm = lambda: threading.Thread(target=cmdbar.warm_up, daemon=True).start()  # noqa: E731
    for name, start in (("downloads watcher", watcher.start if cfg["watch_downloads"] else None),
                        ("snip hotkey", snip.start), ("yt-dlp update", tools.maybe_update_ytdlp),
                        ("voice model warm-up", warm)):
        if start:
            try:
                start()
            except Exception as e:
                print(f"{name} not started: {e}")
    yield


app = FastAPI(title="Personalizer Agent", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

for exc, status in ((PermissionError, 403), (FileNotFoundError, 404), (NotADirectoryError, 404), (ValueError, 400),
                    (RuntimeError, 500)):  # RuntimeError = tool failure with a user-facing message
    app.add_exception_handler(exc, lambda _r, e, status=status: JSONResponse({"detail": str(e)}, status))
# Starlette runs the last-added middleware first: Host check, then CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg["cors_origins"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Range"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg["allowed_hosts"], www_redirect=False)

api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


class MediaCmd(BaseModel):
    action: Literal["volume_up", "volume_down", "mute", "play_pause", "next", "prev"]


@api.post("/media")
async def media_cmd(cmd: MediaCmd):
    # async on purpose: runs on the main thread, which comtypes COM-initialised at import.
    return media.run(cmd.action)


class StreamLink(BaseModel):
    path: str


# Browsers play H.264/VP9/AV1(/HEVC on most GPUs) video and AAC/MP3/Opus/Vorbis/FLAC audio.
BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1", "hevc"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac"}


@api.post("/stream-link")
def stream_link(body: StreamLink):
    path = str(_resolve_file(body.path))
    exp = int(time.time()) + signing.TTL
    sig = signing.sign(path, exp, STREAM_SECRET)
    query = urlencode({"path": path, "exp": exp, "sig": sig})
    video, audio = media_codecs(path)
    return {"url": "/api/stream?" + query, "compat_url": "/api/stream-compat?" + query, "exp": exp,
            "video": video, "audio": audio,
            # e.g. x265 + AC3/DTS movie rips: picture plays but the browser has no sound
            "transcode": bool(video) and (video not in BROWSER_VIDEO or (audio is not None and audio not in BROWSER_AUDIO))}


class OpenVlc(BaseModel):
    path: str


@api.post("/open-vlc")
def open_vlc(body: OpenVlc):
    """On the PC itself vlc:// links don't work (Windows VLC doesn't register them); launch VLC directly."""
    import subprocess
    real = _resolve_file(body.path)
    subprocess.Popen([cfg["vlc_exe"], str(real)])
    return {"opened": str(real)}


# ---------- 2.1 OS control ----------
class PlayYt(BaseModel):
    query: str


@api.post("/play-yt")
def play_yt(body: PlayYt):
    return osctl.play_yt(body.query)


@api.get("/audio-devices")
async def audio_devices():  # async: COM objects on the main thread (see media_cmd)
    return osctl.audio_devices()


class AudioDevice(BaseModel):
    id: str


@api.post("/audio-device")
async def audio_device(body: AudioDevice):
    return osctl.set_audio_device(body.id)


class Power(BaseModel):
    action: Literal["lock", "sleep", "shutdown", "cancel"]
    delay_s: int = 0


@api.post("/power")
def power(body: Power):
    return osctl.power(body.action, body.delay_s)


# ---------- 2.1 voice / natural-language command bar ----------
class CommandBody(BaseModel):
    text: str | None = None
    command: cmdbar.Command | None = None  # a confirmed command sent back by the client
    confirmed: bool = False


@api.post("/command")
async def run_command(body: CommandBody):
    if body.command:
        return await cmdbar.dispatch(body.command, body.confirmed)
    if not (body.text or "").strip():
        raise ValueError("text required")
    cmd = await run_in_threadpool(cmdbar.interpret, body.text)
    return await cmdbar.dispatch(cmd, False, body.text)


@api.post("/command/voice")
async def run_voice(audio: UploadFile = File(...)):
    suffix = Path(audio.filename or "voice.webm").suffix or ".webm"
    heard = await run_in_threadpool(cmdbar.transcribe_bytes, await audio.read(), suffix)
    if not heard:
        return {"heard": "", "did": "Didn't catch that - try again."}
    cmd = await run_in_threadpool(cmdbar.interpret, heard)
    return await cmdbar.dispatch(cmd, False, heard)


# ---------- 3.x files, AirDrop, clipboard, share ----------
@api.get("/fs/list")
def fs_list(path: str | None = None):
    return files.list_dir(path)


@api.get("/drop")
def drop_list():
    return files.list_dir(str(files.drop_dir()))


@api.post("/drop")
async def drop_upload(items: list[UploadFile] = File(...)):
    return [await files.save_upload(f) for f in items]


class Clip(BaseModel):
    text: str


@api.post("/clipboard")
def clipboard_text(body: Clip):
    return files.set_clipboard(text=body.text)


@api.post("/clipboard/image")
async def clipboard_image(image: UploadFile = File(...)):
    return files.set_clipboard(image_bytes=await image.read())


class PathBody(BaseModel):
    path: str


@api.post("/share")
def share(body: PathBody):
    return files.start_share(body.path)


@api.get("/shares")
def shares():
    return files.active_shares()


class ShareId(BaseModel):
    id: str


@api.post("/share/stop")
def share_stop(body: ShareId):
    return {"stopped": files.stop_share(body.id)}


# ---------- long-running jobs ----------
class Ytdl(BaseModel):
    url: str
    format: Literal["mp4", "mp3"] = "mp4"


@api.post("/ytdl")
def ytdl(body: Ytdl):
    return jobs.submit("ytdl", tools.ytdl, body.url, body.format)


class Notes(BaseModel):
    url: str | None = None
    path: str | None = None
    force_whisper: bool = False
    frames: bool = True


@api.post("/notes")
def make_notes(body: Notes):
    if body.url:
        return jobs.submit("notes", notes.notes_from_url, body.url, body.force_whisper, body.frames)
    if body.path:
        return jobs.submit("notes", notes.notes_from_file, str(resolve_allowed(body.path)), body.frames)
    raise ValueError("url or path required")


@api.post("/reveal")
def reveal(body: PathBody):
    """Open Explorer with the file selected (e.g. a finished download)."""
    import subprocess
    real = resolve_allowed(body.path)
    subprocess.Popen(["explorer", f"/select,{real}"])
    return {"revealed": str(real)}


@api.get("/jobs")
def job_list():
    return jobs.recent()


@api.get("/jobs/{job_id}")
def job_get(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return job


# ---------- 4.2 PDF engine (Web UI: upload -> processed file back) ----------
async def _save_uploads(uploads, workdir):
    saved = []
    for up in uploads:
        dest = unique_path(workdir / safe_name(Path(up.filename or "file").name))
        with open(dest, "wb") as f:
            while chunk := await up.read(1 << 20):
                f.write(chunk)
        saved.append(dest)
    return saved


def _send(path, workdir):
    return FileResponse(path, filename=path.name, background=BackgroundTask(shutil.rmtree, workdir, True))


@api.post("/pdf/merge")
async def pdf_merge(items: list[UploadFile] = File(...)):
    workdir = Path(tempfile.mkdtemp(prefix="jarvis_"))
    return _send(tools.pdf_merge(await _save_uploads(items, workdir), workdir), workdir)


@api.post("/pdf/compress")
async def pdf_compress(item: UploadFile = File(...), target_kb: int = Form(2048)):
    workdir = Path(tempfile.mkdtemp(prefix="jarvis_"))
    out, _fits = tools.compress((await _save_uploads([item], workdir))[0], target_kb)
    return _send(out, workdir)


@api.post("/convert")
async def convert(item: UploadFile = File(...), target: str = Form(...)):
    workdir = Path(tempfile.mkdtemp(prefix="jarvis_"))
    src = (await _save_uploads([item], workdir))[0]
    return _send(await run_in_threadpool(tools.convert, src, target), workdir)


@api.post("/pdf/stamp")
async def pdf_stamp(item: UploadFile = File(...)):
    workdir = Path(tempfile.mkdtemp(prefix="jarvis_"))
    return _send(tools.pdf_stamp((await _save_uploads([item], workdir))[0]), workdir)


app.include_router(api)


@app.get("/api/stream-compat")
def stream_compat(request: Request, path: str, exp: int | None = None, sig: str | None = None, t: float = 0):
    """Live re-encode for the browser: copies browser-playable video, converts audio to stereo AAC.
    ponytail: no seeking within the stream (restart with ?t=seconds); VLC handles everything natively."""
    import subprocess
    from starlette.responses import StreamingResponse
    if not (_bearer_ok(request) or (exp is not None and sig is not None
                                    and signing.verify(path, exp, sig, STREAM_SECRET))):
        raise _unauthorized()
    real = _resolve_file(path)
    video, _audio = media_codecs(real)
    vcodec = ["-c:v", "copy"] if video in BROWSER_VIDEO else ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
    proc = subprocess.Popen(
        [ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", str(max(0.0, t)), "-i", str(real),
         "-map", "0:v:0", "-map", "0:a:0?", *vcodec, "-c:a", "aac", "-ac", "2", "-b:a", "192k",
         "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

    def chunks():
        try:
            while data := proc.stdout.read(1 << 16):
                yield data
        finally:  # client closed the player
            if proc.poll() is None:
                proc.kill()

    return StreamingResponse(chunks(), media_type="video/mp4")


# Outside the Bearer-only router: <video src> and vlc:// can't send headers, so a signed URL also works.
@app.get("/api/stream")
def stream(request: Request, path: str, exp: int | None = None, sig: str | None = None):
    if not (_bearer_ok(request) or (exp is not None and sig is not None
                                    and signing.verify(path, exp, sig, STREAM_SECRET))):
        raise _unauthorized()
    return FileResponse(_resolve_file(path))  # handles Range -> 206
