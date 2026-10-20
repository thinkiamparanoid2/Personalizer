"""Explorer right-click / Send To entry point: jarvis.pyw <action> <file> [file ...]."""
import time
from pathlib import Path

from . import files, jobs, notes, tools
from .util import enable_cuda_dlls, error_box, notify, set_clipboard_text


def _each(fn, done="Done"):
    def run(paths):
        for p in paths:
            out = fn(p)
            notify(done, Path(out).name)
    return run


def _compress(target_kb):
    def run(paths):
        for p in paths:
            out, fits = tools.compress(p, target_kb)
            size = Path(out).stat().st_size // 1024
            notify("Compressed" if fits else f"Could not reach {target_kb} KB", f"{Path(out).name} ({size} KB)")
    return run


def _notes(paths):
    for p in paths:
        notify("Study notes", f"Transcribing {Path(p).name} (this can take a while)")
        result = notes.notes_from_file(jobs.Job(), p)
        notify("Study notes ready", result["file"])


def _share(paths):
    share = files.start_share(paths[0], check_roots=False)  # the user picked this file explicitly
    set_clipboard_text(share["url"])
    notify("Public link copied", f"{share['name']} - expires in {(share['expires'] - time.time()) / 60:.0f} min "
                                 "or after one download")
    while share["id"] in files._shares:  # keep this process (and the tunnel) alive until it ends
        time.sleep(2)


ACTIONS = {
    "to-png": _each(lambda p: tools.convert_image(p, "png"), "Converted"),
    "to-jpg": _each(lambda p: tools.convert_image(p, "jpg"), "Converted"),
    "docx-pdf": _each(tools.docx_to_pdf, "Converted"),
    "to-pdf": _each(lambda p: tools.convert(p, "pdf"), "Converted to PDF"),
    "pdf-docx": _each(tools.pdf_to_docx, "Converted to Word"),
    "to-mp4": _each(tools.to_mp4, "Converted to MP4"),
    "to-mp3": _each(tools.to_mp3, "Audio extracted"),
    "shrink-mp4": _each(tools.shrink_mp4, "Shrunk under 25 MB"),
    "rembg": _each(tools.remove_bg, "Background removed"),
    "subs": _each(tools.fetch_subtitles, "Subtitles saved"),
    "notes": _notes,
    "share": _share,
    "pdf-merge": lambda paths: notify("Merged", tools.pdf_merge(sorted(paths)).name),
    "compress-2mb": _compress(2048),
    "compress-200kb": _compress(200),
    "pdf-stamp": _each(tools.pdf_stamp, "Signed"),
}


def main(argv):
    if len(argv) < 2 or argv[0] not in ACTIONS:
        error_box("Jarvis", f"Usage: jarvis.pyw <{'|'.join(ACTIONS)}> <file...>")
        return 2
    enable_cuda_dlls()
    try:
        ACTIONS[argv[0]]([str(Path(a)) for a in argv[1:]])
        return 0
    except Exception as e:
        error_box(f"Jarvis - {argv[0]}", e)
        return 1
