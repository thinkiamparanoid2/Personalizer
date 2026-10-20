import ipaddress
import sys

import uvicorn

from . import config

cfg = config.load()
host = cfg["host"]
if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
    sys.exit(f"Refusing to bind {host}: loopback only. Use `tailscale serve` for remote access.")

from .util import enable_cuda_dlls  # noqa: E402

enable_cuda_dlls()
from .app import app  # noqa: E402  (after the bind check)

uvicorn.run(app, host=host, port=cfg["port"])
