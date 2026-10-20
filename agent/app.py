import secrets
import time
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, media, signing
from .paths import resolve_allowed

cfg = config.load()
TOKEN = cfg["token"].encode()
if len(TOKEN) < 32:
    raise SystemExit("config.json token missing or too short; delete it to regenerate.")
STREAM_SECRET = cfg["stream_secret"]


def _bearer_ok(request: Request):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(token.encode(), TOKEN)


def _unauthorized():
    return HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})


def require_token(request: Request):
    if not _bearer_ok(request):
        raise _unauthorized()


def _resolve_file(path):
    try:
        real = resolve_allowed(path)
    except PermissionError:
        raise HTTPException(403, "Forbidden")
    except (OSError, ValueError):
        raise HTTPException(404, "Not found")
    if not real.is_file():
        raise HTTPException(404, "Not found")
    return real


app = FastAPI(title="Personalizer Agent", docs_url=None, redoc_url=None, openapi_url=None)
# Starlette runs the last-added middleware first: Host check, then CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg["cors_origins"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Range"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg["allowed_hosts"], www_redirect=False)

api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


class MediaCmd(BaseModel):
    action: Literal["volume_up", "volume_down", "mute", "play_pause", "next", "prev"]


@api.post("/media")
async def media_cmd(cmd: MediaCmd):
    # async on purpose: runs on the main thread, which comtypes COM-initialised at import.
    return media.run(cmd.action)


class StreamLink(BaseModel):
    path: str


@api.post("/stream-link")
def stream_link(body: StreamLink):
    path = str(_resolve_file(body.path))
    exp = int(time.time()) + signing.TTL
    sig = signing.sign(path, exp, STREAM_SECRET)
    return {"url": "/api/stream?" + urlencode({"path": path, "exp": exp, "sig": sig}), "exp": exp}


app.include_router(api)


# Outside the Bearer-only router: <video src> and vlc:// can't send headers, so a signed URL also works.
@app.get("/api/stream")
def stream(request: Request, path: str, exp: int | None = None, sig: str | None = None):
    if not (_bearer_ok(request) or (exp is not None and sig is not None
                                    and signing.verify(path, exp, sig, STREAM_SECRET))):
        raise _unauthorized()
    return FileResponse(_resolve_file(path))  # handles Range -> 206
