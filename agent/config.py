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
    return {
        "token": secrets.token_urlsafe(32),
        "stream_secret": secrets.token_urlsafe(32),
        "host": "127.0.0.1",
        "port": 8765,
        # Host headers accepted (DNS-rebinding guard). Add your MagicDNS name if not auto-detected.
        "allowed_hosts": ["127.0.0.1", "localhost", *filter(None, _tailscale_names())],
        # Exact origins allowed by CORS, e.g. "https://<project>.web.app", "chrome-extension://<id>".
        "cors_origins": [],
        # Only files under these folders can be streamed.
        "roots": [str(Path.home() / "Videos")],
    }


def load():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(_defaults(), indent=2), encoding="utf-8")
        print(f"Created {CONFIG_PATH} - edit cors_origins/roots, then copy the token to your clients.")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if "stream_secret" not in cfg:  # configs from before signed stream links
        cfg["stream_secret"] = secrets.token_urlsafe(32)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg
