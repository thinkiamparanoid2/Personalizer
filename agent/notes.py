"""1.3 / 2.7: lecture video (YouTube URL or local file) -> Markdown study notes.

captions (fast) or faster-whisper on the GPU -> ffmpeg slide frames -> Gemini writes the guide.
Output: <notes_dir>/<title>/notes.md + frames/*.jpg
"""
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import config
from .util import gemini_generate, run_ffmpeg, safe_name, unique_path, ytdlp_opts

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}
MAX_FRAMES = 40

PROMPT = """You are writing a study guide from a lecture transcript.

Title: {title}

Write well-structured Markdown:
- `#` title, then `##`/`###` sections that follow the lecture's logical structure (not just chronology).
- Explain concepts clearly and concisely; keep definitions, formulas, examples, and key takeaways.
- Math in LaTeX: inline `$...$`, display `$$...$$`.
- Code in fenced blocks with a language tag.
- Cite where each point is covered with timestamps exactly in the form [mm:ss] or [h:mm:ss], taken from the transcript.
- Slide/whiteboard captures are available (file and time below). Embed the relevant ones next to the matching content as `![short caption](frames/FILE)`; skip duplicates or irrelevant ones.
- End with a short "Key Takeaways" list and 3-5 self-test questions.
Output only the Markdown.

Slide captures:
{frames}

Transcript:
{transcript}
"""


def mmss(t):
    t = int(t)
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _to_seconds(stamp):
    secs = 0
    for part in stamp.split(":"):
        secs = secs * 60 + int(part)
    return secs


def parse_vtt(text):
    """[(seconds, line)]; drops the repeated lines of YouTube's rolling auto-captions."""
    out, last = [], None
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"(?:(\d+):)?(\d{2}):(\d{2})\.\d{3}\s+-->", line)
            if not m:
                continue
            start = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            for cue in lines[i + 1:]:
                cue = re.sub(r"<[^>]+>", "", cue).replace("&nbsp;", " ").strip()
                if cue and cue != last:
                    out.append((start, cue))
                    last = cue
            break
    return out


def paragraphs(segments, every=30):
    """Merge (t, text) segments into ~30 s paragraphs: '[mm:ss] text'."""
    out, start, buf = [], None, []
    for t, text in segments:
        if start is None:
            start = t
        buf.append(text)
        if t - start >= every:
            out.append(f"[{mmss(start)}] {' '.join(buf)}")
            start, buf = None, []
    if buf:
        out.append(f"[{mmss(start)}] {' '.join(buf)}")
    return out


# ---------- transcription ----------
_whisper = {}
_whisper_lock = threading.Lock()


def transcribe(audio_path, job=None, model=None, **options):
    """[(start_seconds, text)]. audio_path: file path or 16 kHz float32 numpy array.
    options are passed to faster-whisper (e.g. language, initial_prompt, vad_filter, beam_size)."""
    options = {"vad_filter": True, "beam_size": 5, **options}
    from faster_whisper import WhisperModel
    name = model or config.load()["whisper_model"]

    def run(device, compute):
        key = (name, device, compute)
        with _whisper_lock:  # parallel jobs share one loaded model
            if key not in _whisper:
                if job:
                    job.progress(f"loading Whisper {name} (first use downloads it, ~1.6 GB for large)")
                _whisper[key] = WhisperModel(name, device=device, compute_type=compute)
        audio = str(audio_path) if isinstance(audio_path, (str, Path)) else audio_path
        segments, info = _whisper[key].transcribe(audio, **options)
        out = []
        for seg in segments:
            out.append((seg.start, seg.text.strip()))
            if job:
                job.progress(f"transcribing {mmss(seg.end)} / {mmss(info.duration)} ({device})")
        return out

    try:
        return run("cuda", "float16")
    except Exception as e:  # missing CUDA/cuDNN DLLs etc.
        print("Whisper on GPU failed, falling back to CPU:", e)
        return run("cpu", "int8")


# ---------- slide frames ----------
def extract_frames(video, out_dir):
    """Scene-change frames -> [(seconds, filename)], at most MAX_FRAMES evenly spread."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        log = run_ffmpeg("-i", video, "-vf", "select='gt(scene,0.3)',showinfo,scale='min(1280,iw)':-2",
                         "-fps_mode", "vfr", "-q:v", "4", out_dir / "f_%04d.jpg")
    except RuntimeError as e:
        if "received no packets" in str(e):  # no scene changes (talking head, static slide)
            return []
        raise
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", log)]
    files = sorted(out_dir.glob("f_*.jpg"))
    pairs = list(zip(times, files))
    if len(pairs) > MAX_FRAMES:
        step = len(pairs) / MAX_FRAMES
        keep = {int(i * step) for i in range(MAX_FRAMES)}
        for i, (_, f) in enumerate(pairs):
            if i not in keep:
                f.unlink()
        pairs = [p for i, p in enumerate(pairs) if i in keep]
    result = []
    for t, f in pairs:
        name = f"{mmss(t).replace(':', '-')}.jpg"
        f.rename(out_dir / name)
        result.append((t, name))
    return result


# ---------- Gemini ----------
def synthesize(title, segments, frames, link=None):
    prompt = PROMPT.format(
        title=title,
        frames="\n".join(f"- {name} at [{mmss(t)}]" for t, name in frames) or "(none)",
        transcript="\n".join(paragraphs(segments)))
    md = gemini_generate(prompt).text or ""
    md = re.sub(r"^```(?:markdown)?\s*\n|\n```\s*$", "", md.strip())  # stray outer fence
    if link:  # [mm:ss] -> clickable link to that moment
        md = re.sub(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\](?!\()",
                    lambda m: f"[{m.group(1)}]({link(_to_seconds(m.group(1)))})", md)
    return md


def _youtube_link(url):
    def link(sec):
        p = urlparse(url)
        q = [(k, v) for k, v in parse_qsl(p.query) if k != "t"] + [("t", f"{sec}s")]
        return urlunparse(p._replace(query=urlencode(q)))
    return link


def _out_dir(title):
    root = Path(config.load()["notes_dir"])
    root.mkdir(parents=True, exist_ok=True)
    return unique_path(root / safe_name(title, 80))


def _write(out_dir, md):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "notes.md"
    path.write_text(md, encoding="utf-8")
    return path


# ---------- entry points ----------
def notes_from_url(job, url, force_whisper=False, frames=True):
    import yt_dlp
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        job.progress("fetching captions")
        base = ytdlp_opts()
        try:
            with yt_dlp.YoutubeDL(base | {"skip_download": True, "writesubtitles": True,
                                          "writeautomaticsub": True, "subtitleslangs": ["en", "en-orig"],
                                          "subtitlesformat": "vtt", "outtmpl": str(tmp / "cap.%(ext)s")}) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            if "subtitle" not in str(e).lower():
                raise
            # YouTube rate-limits caption downloads (HTTP 429); transcribe the audio instead.
            job.progress("captions blocked by YouTube, using Whisper")
            with yt_dlp.YoutubeDL(base | {"skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
        title = info.get("title") or "Lecture"
        manual = "en" in (info.get("subtitles") or {})
        english = (info.get("language") or "en").startswith("en")
        vtts = sorted(tmp.glob("cap*.vtt"))
        segments = []
        # Auto-captions on non-English videos are machine translations; Whisper does better.
        if vtts and not force_whisper and (manual or english):
            segments = parse_vtt(vtts[0].read_text(encoding="utf-8", errors="replace"))
        if not segments:
            job.progress("downloading audio")
            with yt_dlp.YoutubeDL(base | {"format": "bestaudio/best", "outtmpl": str(tmp / "audio.%(ext)s")}) as ydl:
                ydl.download([url])
            segments = transcribe(next(tmp.glob("audio.*")), job)
        out_dir = _out_dir(title)
        shots = []
        if frames:
            job.progress("downloading video for slides")
            with yt_dlp.YoutubeDL(base | {"format": "bv*[height<=720]/b[height<=720]/b",
                                          "outtmpl": str(tmp / "video.%(ext)s")}) as ydl:
                ydl.download([url])
            job.progress("extracting slides")
            shots = extract_frames(next(tmp.glob("video.*")), out_dir / "frames")
        job.progress("writing notes (Gemini)")
        md = synthesize(title, segments, shots, link=_youtube_link(info.get("webpage_url") or url))
        return {"title": title, "file": str(_write(out_dir, f"> Source: {url}\n\n{md}"))}


def notes_from_file(job, path, frames=True):
    path = Path(path)
    title = path.stem
    job.progress("transcribing")
    segments = transcribe(path, job)
    out_dir = _out_dir(title)
    shots = []
    if frames and path.suffix.lower() in VIDEO_EXT:
        job.progress("extracting slides")
        shots = extract_frames(path, out_dir / "frames")
    job.progress("writing notes (Gemini)")
    md = synthesize(title, segments, shots)
    return {"title": title, "file": str(_write(out_dir, f"> Source: {path}\n\n{md}"))}


def _self_check():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
hello <c>world</c>

00:00:03.000 --> 00:00:05.000
hello world
this is new

01:02:03.500 --> 01:02:05.000
this is new
last line
"""
    assert parse_vtt(vtt) == [(1, "hello world"), (3, "this is new"), (3723, "last line")], parse_vtt(vtt)
    assert mmss(3723) == "1:02:03" and mmss(65) == "01:05" and _to_seconds("1:02:03") == 3723
    assert paragraphs([(0, "a"), (31, "b"), (40, "c")]) == ["[00:00] a b", "[00:40] c"]
    link = _youtube_link("https://www.youtube.com/watch?v=abc&t=5s")
    assert link(90) == "https://www.youtube.com/watch?v=abc&t=90s", link(90)
    print("notes self-check OK")


if __name__ == "__main__":
    _self_check()
