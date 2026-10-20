import secrets
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, media
from .paths import resolve_allowed

cfg = config.load()
TOKEN = cfg["token"].encode()
if len(TOKEN) < 32:
    raise SystemExit("config.json token missing or too short; delete it to regenerate.")


def require_token(request: Request):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token.encode(), TOKEN):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})


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


@api.get("/stream")
def stream(path: str):
    try:
        real = resolve_allowed(path)
    except PermissionError:
        raise HTTPException(403, "Forbidden")
    except (OSError, ValueError):
        raise HTTPException(404, "Not found")
    if not real.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(real)  # handles Range -> 206


app.include_router(api)
