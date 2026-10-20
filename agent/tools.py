"""Media & document tools shared by the HTTP API and the Explorer right-click CLI.

1.2 YouTube download, 2.4 convert, 2.5 background removal, 2.6 subtitles, 4.2 PDF merge/compress/stamp.
"""
import io
import json
import re
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

from . import config
from .util import NO_WINDOW, media_duration, run_ffmpeg, unique_path, ytdlp_opts

IMAGE_EXT = {".webp", ".heic", ".heif", ".avif", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif"}
STATE_PATH = Path(__file__).with_name("state.json")


def _open_image(path):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return ImageOps.exif_transpose(Image.open(path))


# ---------- 1.2 YouTube / social video download ----------
def ytdl(job, url, fmt="mp4"):
    import yt_dlp
    out_dir = Path(config.load()["youtube_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    def hook(d):
        if d.get("status") == "downloading":
            job.progress(f"downloading {d.get('_percent_str', '').strip()}")
        elif d.get("status") == "finished":
            job.progress("processing")

    opts = ytdlp_opts(outtmpl=str(out_dir / "%(title).150B [%(id)s].%(ext)s"), windowsfilenames=True,
                      progress_hooks=[hook])
    if fmt == "mp3":
        opts |= {"format": "bestaudio/best",
                 "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]}
    else:  # prefer H.264/AAC so the mp4 plays everywhere
        opts |= {"format": "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
                 "merge_output_format": "mp4"}

    def download(o):
        with yt_dlp.YoutubeDL(o) as ydl:
            return ydl.extract_info(url, download=True)

    try:
        info = download(opts)
    except yt_dlp.utils.DownloadError as e:
        # Cookies only when needed: reading Brave's cookie DB often fails while Brave is open.
        if not re.search(r"sign in|cookies|age|private|members", str(e), re.I):
            raise
        info = download(opts | {"cookiesfrombrowser": ("brave",)})
    files = [d.get("filepath") for d in info.get("requested_downloads", []) if d.get("filepath")]
    return {"title": info.get("title"), "file": files[0] if files else str(out_dir)}


def maybe_update_ytdlp():
    """YouTube changes break old yt-dlp; upgrade at most weekly (takes effect next restart)."""
    try:
        state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    except ValueError:
        state = {}
    if time.time() - state.get("ytdlp_updated", 0) < 7 * 86400:
        return

    def upgrade():
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"],
                           capture_output=True, creationflags=NO_WINDOW)
        if p.returncode == 0:
            state["ytdlp_updated"] = time.time()
            STATE_PATH.write_text(json.dumps(state))

    threading.Thread(target=upgrade, daemon=True).start()


# ---------- 2.4 Instant convert ----------
def convert_image(path, to="png"):
    path = Path(path)
    img = _open_image(path)
    out = unique_path(path.with_suffix(f".{to}"))
    if to == "jpg":
        img.convert("RGB").save(out, "JPEG", quality=92)
    else:
        img.save(out, "PNG")
    return out


def docx_to_pdf(path):
    """Word COM automation (Word must be installed)."""
    import pythoncom
    import win32com.client
    path = Path(path).resolve()
    out = unique_path(path.with_suffix(".pdf"))
    pythoncom.CoInitialize()
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(str(path), ReadOnly=True)
        doc.SaveAs2(str(out), FileFormat=17)  # wdFormatPDF
        doc.Close(False)
    finally:
        word.Quit()
        pythoncom.CoUninitialize()
    return out


def to_mp3(path):
    path = Path(path)
    out = unique_path(path.with_suffix(".mp3"))
    run_ffmpeg("-i", path, "-vn", "-c:a", "libmp3lame", "-q:a", "2", out)
    return out


def shrink_mp4(path, target_mb=25):
    """Re-encode to fit under target_mb (e.g. Discord/email limits). NVENC first, x264 fallback."""
    path = Path(path)
    out = unique_path(path.with_name(f"{path.stem}_{target_mb}MB.mp4"))
    audio_k = 96
    total_k = target_mb * 8192 * 0.93 / media_duration(path)  # 7% headroom for container overhead
    video_k = int(total_k - audio_k)
    if video_k < 80:
        raise ValueError(f"Too long to fit in {target_mb} MB at watchable quality")
    common = ["-b:v", f"{video_k}k", "-maxrate", f"{video_k}k", "-bufsize", f"{video_k * 2}k",
              "-c:a", "aac", "-b:a", f"{audio_k}k", "-movflags", "+faststart"]
    try:
        run_ffmpeg("-i", path, "-c:v", "h264_nvenc", "-preset", "p5", *common, out)
    except RuntimeError:
        run_ffmpeg("-i", path, "-c:v", "libx264", "-preset", "medium", *common, out)
    return out


# ---------- 2.5 Background removal ----------
_rembg_session = None


def remove_bg(path):
    global _rembg_session
    from rembg import new_session, remove
    if _rembg_session is None:  # model load is the slow part; keep it warm in the agent
        _rembg_session = new_session(config.load()["rembg_model"])
    path = Path(path)
    out = unique_path(path.with_name(f"{path.stem}_nobg.png"))
    remove(_open_image(path), session=_rembg_session).save(out)
    return out


# ---------- 2.6 Subtitles ----------
def fetch_subtitles(path, open_vlc=True):
    from babelfish import Language
    from subliminal import download_best_subtitles, region, save_subtitles, scan_video
    if not region.is_configured:
        region.configure("dogpile.cache.memory")
    cfg = config.load()
    providers, provider_configs = ["podnapisi", "gestdown", "tvsubtitles"], {}
    if cfg["opensubtitles_username"]:
        providers.insert(0, "opensubtitlescom")
        provider_configs["opensubtitlescom"] = {"username": cfg["opensubtitles_username"],
                                                "password": cfg["opensubtitles_password"]}
    path = Path(path)
    video = scan_video(str(path))  # computes the OpenSubtitles hash for exact-sync matches
    found = download_best_subtitles({video}, {Language("eng")}, providers=providers,
                                    provider_configs=provider_configs)
    if not found.get(video):
        raise RuntimeError("No English subtitles found")
    save_subtitles(video, found[video])  # -> <name>.en.srt next to the video
    if open_vlc and Path(cfg["vlc_exe"]).exists():
        subprocess.Popen([cfg["vlc_exe"], str(path)])
    return path.with_suffix(".en.srt")


# ---------- 4.2 PDF engine ----------
def pdf_merge(paths, out_dir=None):
    from pypdf import PdfWriter
    paths = [Path(p) for p in paths]
    writer = PdfWriter()
    for p in paths:
        if p.suffix.lower() == ".pdf":
            writer.append(str(p))
        else:
            buf = io.BytesIO()
            _open_image(p).convert("RGB").save(buf, "PDF", resolution=150)
            buf.seek(0)
            writer.append(buf)
    out = unique_path(Path(out_dir or paths[0].parent) / "Merged.pdf")
    with open(out, "wb") as f:
        writer.write(f)
    return out


def _compress_image(path, target):
    img = _open_image(path).convert("RGB")
    best = None
    while True:
        for q in (85, 75, 65, 55, 45, 35):
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=q, optimize=True)
            data = buf.getvalue()
            if best is None or len(data) < len(best):
                best = data
            if len(data) <= target:
                return data
        if img.width < 300:
            return best
        img = img.resize((int(img.width * 0.8), int(img.height * 0.8)))


def _compress_pdf(path, target):
    from pypdf import PdfReader, PdfWriter
    best = None
    for quality, scale in ((80, 1), (60, 1), (45, 0.75), (35, 0.5), (25, 0.35)):
        writer = PdfWriter(clone_from=PdfReader(path))
        for page in writer.pages:
            for img in page.images:
                try:
                    im = img.image
                    if im.mode in ("RGBA", "LA", "P"):  # keep transparency intact
                        continue
                    if scale < 1:
                        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
                    img.replace(im, quality=quality)
                except Exception:
                    continue  # unusual image encodings: leave as-is
            page.compress_content_streams()
        if hasattr(writer, "compress_identical_objects"):
            writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
        buf = io.BytesIO()
        writer.write(buf)
        data = buf.getvalue()
        if best is None or len(data) < len(best):
            best = data
        if len(data) <= target:
            return data
    return best


def compress(path, target_kb=2048):
    """PDF or image -> as close under target_kb as possible. Returns (out_path, fits)."""
    path = Path(path)
    target = int(target_kb) * 1024
    is_pdf = path.suffix.lower() == ".pdf"
    data = _compress_pdf(path, target) if is_pdf else _compress_image(path, target)
    out = unique_path(path.with_name(f"{path.stem}_{target_kb}KB{'.pdf' if is_pdf else '.jpg'}"))
    out.write_bytes(data)
    return out, len(data) <= target


def pdf_stamp(path, signature_png=None, page_index=-1):
    """Overlay the saved signature PNG + today's date at the bottom-right of a page."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    sig_path = signature_png or config.load()["signature_png"]
    if not sig_path or not Path(sig_path).exists():
        raise FileNotFoundError("Set signature_png in agent/config.json (a transparent PNG)")
    path = Path(path)
    writer = PdfWriter(clone_from=PdfReader(path))
    page = writer.pages[page_index]
    box = page.mediabox
    left, bottom, width, height = float(box.left), float(box.bottom), float(box.width), float(box.height)
    sig = _open_image(sig_path)
    sw = width * 0.22
    sh = sw * sig.height / sig.width
    x, y = left + width - sw - 40, bottom + 40
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(left + width, bottom + height))
    c.drawImage(ImageReader(sig), x, y + 14, sw, sh, mask="auto")
    c.setFont("Helvetica", 10)
    c.drawString(x, y, date.today().strftime("%d %B %Y"))
    c.save()
    buf.seek(0)
    page.merge_page(PdfReader(buf).pages[0])
    out = unique_path(path.with_name(f"{path.stem}_signed.pdf"))
    with open(out, "wb") as f:
        writer.write(f)
    return out
