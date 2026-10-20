"""Load agent/config.json, creating it (with a fresh token) on first run."""
import json
import secrets
import shutil
import subprocess
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.json")


def _tailscale_names():
    """Best effort: this machine's Tailscale IPs and MagicDNS name."""
    exe = shutil.which("tailscale")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, timeout=5, check=True)
        me = json.loads(out.stdout)["Self"]
        return [*me.get("TailscaleIPs", []), me.get("DNSName", "").rstrip(".")]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return []


def _defaults():
    home = Path.home()
    phone_drop = str(home / "Desktop" / "PhoneDrop")
    return {
        "token": secrets.token_urlsafe(32),
        "stream_secret": secrets.token_urlsafe(32),
        "host": "127.0.0.1",
        "port": 8765,
        # Host headers accepted (DNS-rebinding guard). Add your MagicDNS name if not auto-detected.
        "allowed_hosts": ["127.0.0.1", "localhost", *filter(None, _tailscale_names())],
        # Exact origins allowed by CORS, e.g. "https://<project>.web.app", "chrome-extension://<id>".
        "cors_origins": [],
        # Only files under these folders can be browsed/streamed/shared (add "D:\\" etc. for full drives).
        "roots": [str(home / "Videos"), phone_drop],
        "phone_drop_dir": phone_drop,
        "downloads_dir": str(home / "Downloads"),
        "youtube_dir": r"D:\YouTube",
        "papers_dir": r"D:\Papers",
        "notes_dir": r"D:\StudyNotes",
        "brave_exe": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        "qbittorrent_exe": r"E:\qBittorrent\qbittorrent.exe",
        "vlc_exe": r"E:\VLC\vlc.exe",
        "sevenzip_exe": r"C:\Program Files\7-Zip\7z.exe",
        "cloudflared_exe": "cloudflared",
        "gemini_api_key": "",
        "gemini_model": "gemini-3.8-flash",
        "whisper_model": "large-v3-turbo",
        "command_whisper_model": "small",  # short voice commands: fast, stays loaded
        "rembg_model": "birefnet-general",
        "signature_png": "",
        # OpenSubtitles.com account (optional; podnapisi works without one).
        "opensubtitles_username": "",
        "opensubtitles_password": "",
        "snip_hotkey": "alt+s",
        "share_ttl_minutes": 60,
        "watch_downloads": True,
    }


_cache = None


def load():
    """Read once per process (restart the agent after editing config.json)."""
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def _load():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(_defaults(), indent=2), encoding="utf-8")
        print(f"Created {CONFIG_PATH} - edit cors_origins/roots, then copy the token to your clients.")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    missing = {k: v for k, v in _defaults().items() if k not in cfg}  # keys added in later versions
    if missing:
        cfg.update(missing)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg
