"""2.3 Downloads auto-router: .torrent -> qBittorrent, archives -> extract prompt, arXiv PDFs -> renamed into Papers."""
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config
from .util import ask, error_box, notify, safe_name, unique_path

ARXIV = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?\.pdf$", re.I)
PARTIAL = {".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload"}
ARCHIVES = {".zip", ".rar", ".7z"}
_inflight = set()


def _wait_stable(p, timeout=600):
    """True once the file exists, is non-empty and its size stops changing (download finished)."""
    last, deadline = -1, time.time() + timeout
    while time.time() < deadline:
        try:
            size = p.stat().st_size
        except OSError:
            return False
        if size and size == last:
            return True
        last = size
        time.sleep(1.5)
    return False


def open_torrent(p):
    subprocess.Popen([config.load()["qbittorrent_exe"], str(p)])
    time.sleep(10)  # let qBittorrent read it before deleting
    p.unlink(missing_ok=True)


def extract_archive(p):
    dest = unique_path(p.with_suffix(""))
    if p.suffix.lower() == ".zip":
        shutil.unpack_archive(p, dest)  # zipfile strips ../ and absolute member paths
    else:
        exe = config.load()["sevenzip_exe"]
        if not Path(exe).exists():
            raise FileNotFoundError("7-Zip needed for .rar/.7z: winget install 7zip.7zip")
        subprocess.run([exe, "x", str(p), f"-o{dest}", "-y"], check=True, capture_output=True)
    p.unlink()
    os.startfile(dest)
    return dest


def rename_paper(p, arxiv_id):
    with urllib.request.urlopen(f"https://export.arxiv.org/api/query?id_list={arxiv_id}", timeout=20) as r:
        root = ET.fromstring(r.read())
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    title = " ".join((entry.findtext("a:title", "", ns) if entry is not None else "").split())
    year = (entry.findtext("a:published", "", ns) if entry is not None else "")[:4]
    if not title or title.lower() == "error":
        return None
    papers = Path(config.load()["papers_dir"])
    papers.mkdir(parents=True, exist_ok=True)
    dest = unique_path(papers / f"{safe_name(f'[{year}] {title}', 180)}.pdf")
    shutil.move(p, dest)
    return dest


def route(p):
    p = Path(p)
    ext = p.suffix.lower()
    if ext in PARTIAL or p.name.startswith("~$"):
        return
    key = str(p).lower()
    if key in _inflight:  # created + moved events for the same download
        return
    _inflight.add(key)
    try:
        if not _wait_stable(p):
            return
        if ext == ".torrent":
            open_torrent(p)
            notify("Torrent added", p.name)
        elif ext in ARCHIVES:
            if ask("Jarvis - Downloads", f"Extract '{p.name}' to a subfolder and delete the archive?"):
                notify("Extracted", extract_archive(p).name)
        elif m := ARXIV.match(p.name):
            dest = rename_paper(p, m.group(1))
            if dest:
                notify("Paper filed", dest.name)
    except Exception as e:
        error_box("Jarvis - Downloads", f"{p.name}: {e}")
    finally:
        _inflight.discard(key)


def start():
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class Handler(FileSystemEventHandler):
        def on_created(self, e):
            if not e.is_directory:
                threading.Thread(target=route, args=(e.src_path,), daemon=True).start()

        def on_moved(self, e):  # browsers rename x.crdownload -> x.zip when done
            if not e.is_directory:
                threading.Thread(target=route, args=(e.dest_path,), daemon=True).start()

    folder = config.load()["downloads_dir"]
    observer = Observer()
    observer.schedule(Handler(), folder, recursive=False)
    observer.daemon = True
    observer.start()
    return observer
