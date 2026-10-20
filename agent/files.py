"""3.1 AirDrop drop folder, 3.3 drive browser + clipboard, 2.8 public share links."""
import http.server
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

from . import config
from .paths import resolve_allowed
from .util import NO_WINDOW, safe_name, set_clipboard_image, set_clipboard_text, unique_path

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"}


def list_dir(path=None):
    """No path -> the configured roots. Otherwise entries of an allowed directory."""
    if not path:
        return {"path": None, "parent": None,
                "entries": [{"name": r, "path": r, "dir": True} for r in config.load()["roots"]]}
    real = resolve_allowed(path)
    if not real.is_dir():
        raise NotADirectoryError(path)
    entries = []
    with os.scandir(real) as it:
        for e in it:
            try:
                st = e.stat()
                is_dir = e.is_dir()
            except OSError:
                continue
            entries.append({"name": e.name, "path": str(real / e.name), "dir": is_dir,
                            "size": None if is_dir else st.st_size, "mtime": int(st.st_mtime),
                            "video": not is_dir and Path(e.name).suffix.lower() in VIDEO_EXT})
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    try:
        resolve_allowed(real.parent)
        parent = str(real.parent) if real.parent != real else None
    except (PermissionError, OSError):
        parent = None  # at a root: go back to the roots list
    return {"path": str(real), "parent": parent, "entries": entries}


def drop_dir():
    d = Path(config.load()["phone_drop_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


async def save_upload(upload):
    """Stream an UploadFile into the drop folder without loading it in RAM."""
    dest = unique_path(drop_dir() / safe_name(Path(upload.filename or "file").name))
    with open(dest, "wb") as f:
        while chunk := await upload.read(1 << 20):
            f.write(chunk)
    return {"name": dest.name, "path": str(dest), "size": dest.stat().st_size}


def set_clipboard(text=None, image_bytes=None):
    if image_bytes:
        import io
        from PIL import Image
        set_clipboard_image(Image.open(io.BytesIO(image_bytes)))
        return {"clipboard": "image"}
    set_clipboard_text(text or "")
    return {"clipboard": "text", "chars": len(text or "")}


# --- 2.8 Share one file publicly via a cloudflared quick tunnel ---
# The tunnel points at a throwaway server that serves only this file, never at the agent API.
_shares = {}


class _OneFileHandler(http.server.BaseHTTPRequestHandler):
    share = None  # set per subclass

    def do_GET(self):
        s = self.share
        if self.path.split("?")[0] != s["url_path"] or time.time() > s["expires"]:
            self.send_error(404)
            return
        size = s["file"].stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(s['file'].name)}")
        self.end_headers()
        sent = 0
        with open(s["file"], "rb") as f:
            while chunk := f.read(1 << 20):
                try:
                    self.wfile.write(chunk)
                except (ConnectionError, OSError):
                    return
                sent += len(chunk)
        if sent == size:
            s["downloads"] += 1
            threading.Timer(5, stop_share, [s["id"]]).start()  # one complete download, then close

    def log_message(self, *args):
        pass


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_share(path, check_roots=True):
    cfg = config.load()
    real = resolve_allowed(path) if check_roots else Path(path).resolve(strict=True)
    if not real.is_file():
        raise FileNotFoundError(path)
    candidates = [cfg["cloudflared_exe"], r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                  r"C:\Program Files\cloudflared\cloudflared.exe",
                  str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe")]
    exe = next((shutil.which(c) or (c if Path(c).is_file() else None) for c in candidates
                if shutil.which(c) or Path(c).is_file()), None)
    if not exe:
        raise RuntimeError("cloudflared not found. Install: winget install Cloudflare.cloudflared")
    sid = secrets.token_urlsafe(16)
    share = {"id": sid, "file": real, "url_path": f"/{sid}/{quote(real.name)}", "downloads": 0,
             "expires": time.time() + cfg["share_ttl_minutes"] * 60}
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), type("H", (_OneFileHandler,), {"share": share}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    share.update(server=server, proc=proc)
    _shares[sid] = share
    deadline = time.time() + 40
    public = None
    while time.time() < deadline and proc.poll() is None:
        line = proc.stderr.readline()
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            public = m.group(0)
            break
    if not public:
        stop_share(sid)
        raise RuntimeError("cloudflared did not return a public URL")
    threading.Thread(target=proc.stderr.read, daemon=True).start()  # keep draining the pipe
    threading.Timer(share["expires"] - time.time(), stop_share, [sid]).start()
    return {"id": sid, "url": public + share["url_path"], "expires": int(share["expires"]), "name": real.name}


def stop_share(sid):
    s = _shares.pop(sid, None)
    if not s:
        return False
    s["proc"].terminate()
    s["server"].shutdown()
    return True


def active_shares():
    return [{"id": s["id"], "name": s["file"].name, "expires": int(s["expires"]), "downloads": s["downloads"]}
            for s in _shares.values()]
