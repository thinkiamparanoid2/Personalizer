# PC Agent

Local FastAPI hub for the PWA and Brave extension (`central.md` Pillars 1-4): OS control, files/AirDrop/streaming,
YouTube download, GPU study notes, snip-to-code, Downloads router, Explorer right-click tools, PDF engine.

## Run

From the repo root (Windows, Python 3.12):

```bash
python -m venv agent/.venv
agent/.venv/Scripts/python -m pip install -r agent/requirements.txt
agent/.venv/Scripts/python -m pip uninstall -y onnxruntime   # rembg may pull the CPU build next to onnxruntime-gpu
agent/.venv/Scripts/python -m agent
```

ffmpeg comes from `imageio-ffmpeg` if none is on PATH; Node (or Deno) on PATH lets yt-dlp see all YouTube formats.
Optional tools: `winget install Cloudflare.cloudflared` (share links), `winget install 7zip.7zip` (.rar/.7z).

First run writes `agent/config.json` (gitignored) with a random token. Edit it, then restart:

| key | meaning |
|---|---|
| `token` | Clients send `Authorization: Bearer <token>` on every `/api/*` call. Delete the file to rotate. |
| `stream_secret` | HMAC key for signed stream URLs (added automatically if missing). Change it to revoke all outstanding links. |
| `host` / `port` | `127.0.0.1:8765`. Non-loopback hosts are refused. |
| `allowed_hosts` | Accepted `Host` headers (DNS-rebinding guard; others get 400). Add your MagicDNS name (`pc.tailnet.ts.net`) if not auto-detected. |
| `cors_origins` | Exact origins, e.g. `https://<project>.web.app`, `chrome-extension://<extension-id>`. |
| `roots` | Folders `/api/stream` may serve from (e.g. `D:\\MOvies`). Symlinks/`..` out of them are rejected. |

| `phone_drop_dir` | AirDrop uploads land here (`Desktop\PhoneDrop`). |
| `youtube_dir`, `papers_dir`, `notes_dir`, `downloads_dir` | Output/input folders for downloads, arXiv papers, study notes, and the watched Downloads folder. |
| `brave_exe`, `qbittorrent_exe`, `vlc_exe`, `sevenzip_exe`, `cloudflared_exe` | Program paths. |
| `gemini_api_key`, `gemini_model` | Needed for study notes and snip (Google AI Studio key). |
| `whisper_model`, `rembg_model` | `large-v3-turbo`, `birefnet-general` (downloaded on first use). |
| `signature_png` | Transparent PNG used by "Stamp Signature". |
| `opensubtitles_username/password` | Optional OpenSubtitles.com account (podnapisi/gestdown work without). |
| `snip_hotkey`, `watch_downloads`, `share_ttl_minutes` | `alt+s`, `true`, `60`. |

`roots` also limits browsing and sharing; add `D:\\` etc. for whole drives. Missing keys are added with defaults on start; restart the agent after editing.

Self-checks: `python -m agent.paths`, `python -m agent.signing`, `python -m agent.notes`.

## Endpoints

- `POST /api/media` `{"action": "volume_up|volume_down|mute|play_pause|next|prev"}`
- `GET /api/stream?path=D:\MOvies\x.mp4` - supports `Range` (206) for seeking. Needs the Bearer header **or** a valid `exp`+`sig` from `/api/stream-link`.
- `POST /api/stream-link` `{"path": "D:\\MOvies\\x.mp4"}` -> `{"url": "/api/stream?path=...&exp=<unix>&sig=<hmac>", "exp": <unix>}`.
  For `<video src>` and `vlc://`, which can't send headers. `sig` = HMAC-SHA256(`stream_secret`, `<path>\n<exp>`); valid 6h, for that one file only.
  Prefix the agent origin: `https://<pc>.<tailnet>.ts.net/api/stream?...`, or `vlc://https://<pc>.<tailnet>.ts.net/api/stream?...`.
  The URL grants access to that file until `exp`, so don't share or log it.

- `POST /api/play-yt` `{"query"}` - top YouTube result (or a URL) opens in Brave.
- `GET /api/audio-devices`, `POST /api/audio-device` `{"id"}` - switch default output (speakers/headphones).
- `POST /api/power` `{"action": "lock|sleep|shutdown|cancel", "delay_s"}`.
- `GET /api/fs/list?path=` - roots when no path; entries of an allowed folder.
- `GET /api/drop`, `POST /api/drop` (multipart `items`) - AirDrop folder.
- `POST /api/clipboard` `{"text"}`, `POST /api/clipboard/image` (multipart `image`) - lands on the PC clipboard.
- `POST /api/share` `{"path"}` -> public `trycloudflare.com` link to that one file (separate throwaway server, closes after one download or `share_ttl_minutes`); `GET /api/shares`, `POST /api/share/stop` `{"id"}`.
- `POST /api/ytdl` `{"url", "format": "mp4|mp3"}`, `POST /api/notes` `{"url"}` or `{"path"}` -> job; `GET /api/jobs`, `GET /api/jobs/{id}`.
- `POST /api/pdf/merge` (multipart `items`), `/api/pdf/compress` (`item`, `target_kb`), `/api/pdf/stamp` (`item`) -> processed file.

```bash
curl -H "Authorization: Bearer $TOKEN" -X POST -H "Content-Type: application/json" -d '{"action":"play_pause"}' http://127.0.0.1:8765/api/media
```

## Background helpers (start with the agent)

- **Snip (Alt+S):** opens the Windows snipping overlay; pick Code / LaTeX / Translate; Gemini's answer is put on the clipboard.
- **Downloads router:** `.torrent` -> qBittorrent (then deleted); `.zip/.rar/.7z` -> "extract & delete?" prompt; arXiv PDFs (`2401.12345.pdf`) -> `papers_dir/[Year] Title.pdf`.
- **yt-dlp** self-updates weekly (effective after restart).

## Explorer right-click + Send To

```bash
agent/.venv/Scripts/python -m agent.contextmenu install
```

Adds a "Jarvis" submenu (per-user, no admin): convert to PNG/JPG, DOCX -> PDF, extract MP3, shrink under 25 MB, remove background (GPU),
fetch subtitles + play in VLC, transcribe to study notes (GPU), compress under 2 MB / 200 KB, stamp signature, share public link.
Send To gets Merge into PDF (multi-select), Compress, Remove Background, Share. Undo with `... -m agent.contextmenu uninstall`.

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
