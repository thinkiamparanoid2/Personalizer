"""Small shared helpers: ffmpeg location, notifications, file naming."""
import ctypes
import re
import shutil
import subprocess
import time
from pathlib import Path

BIN_DIR = Path(__file__).with_name("bin")
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def enable_cuda_dlls():
    """Make pip-installed CUDA/cuDNN DLLs (site-packages/nvidia/*/bin) visible to
    ctranslate2 (faster-whisper) and onnxruntime-gpu (rembg). No system CUDA install needed."""
    import os
    import site
    for sp in site.getsitepackages():
        for bin_dir in Path(sp, "nvidia").glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]


def ffmpeg():
    """System ffmpeg if on PATH, else the binary bundled with imageio-ffmpeg (copied as bin/ffmpeg.exe
    so yt-dlp's ffmpeg_location finds it by its standard name)."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    target = BIN_DIR / "ffmpeg.exe"
    if not target.exists():
        import imageio_ffmpeg
        BIN_DIR.mkdir(exist_ok=True)
        shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), target)
    return str(target)


def ytdlp_opts(**extra):
    """Shared yt-dlp options. A JS runtime (Deno or Node) is needed for YouTube's full format list."""
    return {"quiet": True, "noprogress": True, "noplaylist": True, "ffmpeg_location": ffmpeg(),
            "js_runtimes": {"deno": {}, "node": {}}, **extra}


def run_ffmpeg(*args):
    """Run ffmpeg; return stderr text. Raises RuntimeError with the tail of stderr on failure."""
    p = subprocess.run([ffmpeg(), "-hide_banner", "-y", *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW)
    if p.returncode:
        raise RuntimeError("ffmpeg failed: " + p.stderr[-800:])
    return p.stderr


def media_duration(path):
    """Seconds, parsed from `ffmpeg -i` (ffprobe isn't bundled)."""
    p = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
    if not m:
        raise RuntimeError(f"Could not read duration of {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def media_codecs(path):
    """(video_codec, audio_codec) of the first streams, from `ffmpeg -i`; None if absent."""
    p = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    v = re.search(r"Stream #\S+.*?: Video: (\w+)", p.stderr)
    a = re.search(r"Stream #\S+.*?: Audio: (\w+)", p.stderr)
    return (v.group(1) if v else None), (a.group(1) if a else None)


def safe_name(name, limit=150):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name)).strip(" .")
    return name[:limit] or "untitled"


def unique_path(path):
    """path, or 'name (2).ext' etc. if it exists."""
    path = Path(path)
    n = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


_gemini_bad = {}      # model -> earliest time to re-test it; a failed model is tried last until a test passes
_gemini_probing = set()
FAST_TRY_S = 12       # interactive calls: cap on each model except the last (a busy model can hang to the deadline)


def _bad_for(err):
    """Seconds before re-testing a failed model: quota resets daily, a retired model won't come back soon."""
    e = f" {err}"
    return 3600 if "RESOURCE_EXHAUSTED" in e or " 429" in e else 86400 if "NOT_FOUND" in e or " 404" in e else 1800


def _probe(client, model):
    """Background re-test of a failed model with a tiny request, so no user waits on it."""
    try:
        client.models.generate_content(model=model, contents="Reply with: ok")
        _gemini_bad.pop(model, None)
        print(f"Gemini {model} is answering again")
    except Exception as e:
        _gemini_bad[model] = time.time() + _bad_for(e)
    finally:
        _gemini_probing.discard(model)


def gemini_generate(contents, config=None, fast=False, timeout_s=None):
    """generate_content on the configured model, falling back to gemini_fallback_models when one is
    overloaded/unavailable. fast=True: no retry backoff, and models other than the last get FAST_TRY_S."""
    import threading
    from google import genai
    from google.genai import types
    from . import config as cfgmod
    cfg = cfgmod.load()
    if not cfg["gemini_api_key"]:
        raise RuntimeError("Set gemini_api_key in agent/config.json")
    timeout_s = timeout_s or (10 if fast else 120)  # Gemini minimum deadline is 10 s
    attempts = types.HttpRetryOptions(attempts=1 if fast else 2)
    client_for = lambda s: genai.Client(api_key=cfg["gemini_api_key"],  # noqa: E731
                                        http_options=types.HttpOptions(timeout=int(s * 1000), retry_options=attempts))
    models = list(dict.fromkeys([cfg["gemini_model"], *cfg.get("gemini_fallback_models", [])]))
    now = time.time()
    for m, retest_at in list(_gemini_bad.items()):
        if retest_at <= now and m in models and m not in _gemini_probing:
            _gemini_probing.add(m)
            threading.Thread(target=_probe, args=(client_for(10), m), daemon=True).start()
    models.sort(key=lambda m: m in _gemini_bad)  # failed models last, configured order otherwise
    last = None
    for i, model in enumerate(models):
        is_last = i == len(models) - 1
        client = client_for(min(timeout_s, FAST_TRY_S) if fast and not is_last else timeout_s)
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:  # 503 overloaded, 504 timeout, 404 retired model...
            print(f"Gemini {model} failed: {str(e)[:120]}")
            _gemini_bad[model] = time.time() + _bad_for(e)
            last = e
    if last is not None and ("RESOURCE_EXHAUSTED" in str(last) or "429" in str(last)):
        raise RuntimeError("Gemini quota used up: the free daily limit for these models is reached "
                           "(resets daily, around 1 PM Bangladesh time).")
    raise RuntimeError(f"All Gemini models failed: {str(last)[:200]}")


ICON = Path(__file__).resolve().parent.parent / "icon-192.png"


def notify(title, msg="", launch="", long=False, sound=False):
    """Windows toast (Farvis icon); clicking it opens `launch`. Silently skipped if winotify is unavailable."""
    try:
        from winotify import Notification, audio
        toast = Notification(app_id="Farvis", title=title, msg=str(msg)[:400], icon=str(ICON) if ICON.exists() else "",
                             duration="long" if long else "short", launch=launch)
        if sound:
            toast.set_audio(audio.Reminder, loop=False)
        toast.show()
    except Exception:
        print(f"[notify] {title}: {msg}")


MB_YESNO, MB_ICONERROR, MB_TOPMOST, MB_SETFOREGROUND, IDYES = 0x4, 0x10, 0x40000, 0x10000, 6


def ask(title, msg):
    return ctypes.windll.user32.MessageBoxW(0, msg, title, MB_YESNO | MB_TOPMOST | MB_SETFOREGROUND) == IDYES


def error_box(title, msg):
    ctypes.windll.user32.MessageBoxW(0, str(msg)[:1500], title, MB_ICONERROR | MB_TOPMOST | MB_SETFOREGROUND)


def set_clipboard_text(text):
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_image(img):
    """img: PIL.Image. Windows wants a DIB (BMP without its 14-byte file header)."""
    import io
    import win32clipboard
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, buf.getvalue()[14:])
    finally:
        win32clipboard.CloseClipboard()


def _gemini_self_check():
    """Fallback order, timeout caps and background re-tests, with a fake Gemini client."""
    from google import genai
    from . import config as cfgmod
    calls, busy = [], {"primary"}

    class FakeClient:
        def __init__(self, api_key, http_options):
            self.timeout_s = http_options.timeout / 1000
            self.models = self

        def generate_content(self, model, contents, config=None):
            calls.append((model, self.timeout_s, contents == "Reply with: ok"))
            if model in busy:
                raise RuntimeError("503 UNAVAILABLE")
            return model

    real_client, real_load = genai.Client, cfgmod.load
    genai.Client = FakeClient
    cfgmod.load = lambda: {"gemini_api_key": "k", "gemini_model": "primary", "gemini_fallback_models": ["second", "third"]}
    _gemini_bad.clear()
    try:
        assert gemini_generate("hi", fast=True, timeout_s=30) == "second"
        assert calls == [("primary", 12, False), ("second", 12, False)], calls   # non-last models capped
        calls.clear()
        assert gemini_generate("hi", fast=True, timeout_s=30) == "second" and calls[0][0] == "second"  # busy one waits
        _gemini_bad["primary"] = 0                     # re-test due: probed in the background, user not blocked
        calls.clear()
        assert gemini_generate("hi", fast=True, timeout_s=30) == "second"
        time.sleep(0.2)
        assert ("primary", 10, True) in calls and "primary" in _gemini_bad and _gemini_bad["primary"] > time.time()
        busy.clear()
        _gemini_bad["primary"] = 0
        gemini_generate("hi", fast=True, timeout_s=30)
        time.sleep(0.2)
        assert "primary" not in _gemini_bad            # answered the probe: back in first place
        calls.clear()
        assert gemini_generate("hi", fast=True, timeout_s=30) == "primary" and calls == [("primary", 12, False)]
        busy.update({"primary", "second", "third"})
        calls.clear()
        try:
            gemini_generate("hi", fast=True, timeout_s=30)
            raise AssertionError("expected failure")
        except RuntimeError as e:
            assert "All Gemini models failed" in str(e)
        assert calls[-1] == ("third", 30, False), calls   # the last model gets the full deadline
        assert _bad_for("429 RESOURCE_EXHAUSTED") == 3600 and _bad_for("404 NOT_FOUND") == 86400 and _bad_for("503") == 1800
        print("gemini fallback self-check OK")
    finally:
        genai.Client, cfgmod.load = real_client, real_load
        _gemini_bad.clear()


if __name__ == "__main__":
    _gemini_self_check()
