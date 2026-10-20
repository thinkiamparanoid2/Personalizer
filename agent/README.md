# PC Agent

Local FastAPI hub for the PWA and Brave extension (`central.md` Pillars 2 & 3). Secure skeleton only.

## Run

From the repo root (Windows, Python 3.12):

```bash
python -m venv agent/.venv
agent/.venv/Scripts/python -m pip install -r agent/requirements.txt
agent/.venv/Scripts/python -m agent
```

First run writes `agent/config.json` (gitignored) with a random token. Edit it, then restart:

| key | meaning |
|---|---|
| `token` | Clients send `Authorization: Bearer <token>` on every `/api/*` call. Delete the file to rotate. |
| `stream_secret` | HMAC key for signed stream URLs (added automatically if missing). Change it to revoke all outstanding links. |
| `host` / `port` | `127.0.0.1:8765`. Non-loopback hosts are refused. |
| `allowed_hosts` | Accepted `Host` headers (DNS-rebinding guard; others get 400). Add your MagicDNS name (`pc.tailnet.ts.net`) if not auto-detected. |
| `cors_origins` | Exact origins, e.g. `https://<project>.web.app`, `chrome-extension://<extension-id>`. |
| `roots` | Folders `/api/stream` may serve from (e.g. `D:\\MOvies`). Symlinks/`..` out of them are rejected. |

Self-checks: `python -m agent.paths`, `python -m agent.signing`.

## Endpoints

- `POST /api/media` `{"action": "volume_up|volume_down|mute|play_pause|next|prev"}`
- `GET /api/stream?path=D:\MOvies\x.mp4` - supports `Range` (206) for seeking. Needs the Bearer header **or** a valid `exp`+`sig` from `/api/stream-link`.
- `POST /api/stream-link` `{"path": "D:\\MOvies\\x.mp4"}` -> `{"url": "/api/stream?path=...&exp=<unix>&sig=<hmac>", "exp": <unix>}`.
  For `<video src>` and `vlc://`, which can't send headers. `sig` = HMAC-SHA256(`stream_secret`, `<path>\n<exp>`); valid 6h, for that one file only.
  Prefix the agent origin: `https://<pc>.<tailnet>.ts.net/api/stream?...`, or `vlc://https://<pc>.<tailnet>.ts.net/api/stream?...`.
  The URL grants access to that file until `exp`, so don't share or log it.

```bash
curl -H "Authorization: Bearer $TOKEN" -X POST -H "Content-Type: application/json" -d '{"action":"play_pause"}' http://127.0.0.1:8765/api/media
```

## Remote access (phone) - Tailscale HTTPS

The agent never binds `0.0.0.0`/LAN: the PWA is served over HTTPS, and browsers block an HTTPS page from calling plain `http://192.168.x.x` (mixed content). Instead let Tailscale terminate TLS and proxy to loopback:

1. Tailscale admin console -> DNS: enable MagicDNS and HTTPS Certificates.
2. `tailscale serve --bg 8765` -> `https://<pc>.<tailnet>.ts.net` now proxies to `http://127.0.0.1:8765`, reachable only from your tailnet.
3. Put `<pc>.<tailnet>.ts.net` in `allowed_hosts`. Stop with `tailscale serve --https=443 off`.

## Autostart (Task Scheduler)

Run at logon, as you, only while logged on (media keys and audio need your desktop session; don't use "run whether user is logged on or not"). `pythonw` avoids a console window:

```bash
schtasks /Create /TN "Personalizer Agent" /SC ONLOGON /RL LIMITED /TR "cmd /c cd /d F:\Personalizer && agent\.venv\Scripts\pythonw.exe -m agent"
```

Remove with `schtasks /Delete /TN "Personalizer Agent" /F`.
