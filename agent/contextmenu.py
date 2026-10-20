"""Register the Explorer right-click "Jarvis" submenu (HKCU, no admin) and Send To shortcuts.

python -m agent.contextmenu install | uninstall
"""
import os
import sys
import winreg
from pathlib import Path

PYTHONW = Path(sys.executable).with_name("pythonw.exe")
LAUNCHER = Path(__file__).with_name("jarvis.pyw")
BASE = r"Software\Classes\*\shell\Jarvis"
SENDTO_DIR = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "SendTo"

IMG = ".webp .heic .heif .avif .png .jpg .jpeg .bmp"
VID = ".mp4 .mkv .mov .webm .avi .m4v"
# (action, label, extensions it applies to or None for every file)
MENU = [
    ("to-png", "Convert to PNG", ".webp .heic .heif .avif .jpg .jpeg .bmp"),
    ("to-jpg", "Convert to JPG", ".webp .heic .heif .avif .png .bmp"),
    ("docx-pdf", "Convert to PDF", ".docx .doc"),
    ("to-mp3", "Extract MP3", VID),
    ("shrink-mp4", "Shrink under 25 MB", VID),
    ("rembg", "Remove Background (GPU)", IMG),
    ("subs", "Fetch Subtitles + Play", VID),
    ("notes", "Transcribe to Study Notes (GPU)", VID + " .mp3 .m4a .wav"),
    ("compress-2mb", "Compress under 2 MB", ".pdf " + IMG),
    ("compress-200kb", "Compress under 200 KB", ".pdf " + IMG),
    ("pdf-stamp", "Stamp Signature + Date", ".pdf"),
    ("share", "Share with Friend (Public Link)", None),
]
# Send To passes every selected file in one call (needed for merging).
SENDTO = [("pdf-merge", "Merge into PDF"), ("compress-2mb", "Compress under 2 MB"),
          ("rembg", "Remove Background"), ("share", "Share Public Link")]


def _set(key, name, value):
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def install():
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, BASE) as k:
        _set(k, "MUIVerb", "Jarvis")
        _set(k, "SubCommands", "")  # static cascading submenu from the shell\* subkeys
        _set(k, "Icon", str(PYTHONW))
    for i, (action, label, exts) in enumerate(MENU):
        verb = rf"{BASE}\shell\{i:02d}_{action}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, verb) as k:
            _set(k, "MUIVerb", label)
            if exts:
                _set(k, "AppliesTo", " OR ".join(f"System.FileExtension:={e}" for e in exts.split()))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, verb + r"\command") as k:
            _set(k, "", f'"{PYTHONW}" "{LAUNCHER}" {action} "%1"')

    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    for action, label in SENDTO:
        lnk = shell.CreateShortCut(str(SENDTO_DIR / f"Jarvis - {label}.lnk"))
        lnk.TargetPath = str(PYTHONW)
        lnk.Arguments = f'"{LAUNCHER}" {action}'
        lnk.WorkingDirectory = str(LAUNCHER.parent)
        lnk.save()
    print("Installed Jarvis context menu and Send To shortcuts.")


def _delete_tree(root, path):
    try:
        with winreg.OpenKey(root, path) as k:
            while True:
                try:
                    _delete_tree(root, path + "\\" + winreg.EnumKey(k, 0))
                except OSError:
                    break
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def uninstall():
    _delete_tree(winreg.HKEY_CURRENT_USER, BASE)
    for lnk in SENDTO_DIR.glob("Jarvis - *.lnk"):
        lnk.unlink()
    print("Removed Jarvis context menu and Send To shortcuts.")


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
