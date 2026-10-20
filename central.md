# Personalizer -> "JARVIS" Final Technical Specification (`central.md`)

> **Core Philosophy:** Zero manual logging. Zero fragile scrapers. One unified **Local Python FastAPI Hub** on your PC + **Brave Extension** + **Web/Mobile PWA** connected via **Tailscale / Local LAN** and **Firebase**.

---

## System Architecture Overview

```
┌───────────────────────────┐       ┌───────────────────────────┐
│   Brave Browser Extension │       │    Phone / PC Web PWA     │
│  • IMDb JSON-LD button    │       │  • Identity & Vault UI    │
│  • YT Download & Notes btn│       │  • Bedside Remote & Stream│
│  • Tab Workspace Freeze   │       │  • Drive Browser & AirDrop│
└─────────────┬─────────────┘       └──────┬──────────────┬─────┘
              │                            │              │
              │ HTTP (127.0.0.1:8765)      │ LAN /        │ Offline / Cloud Sync
              ▼                            │ Tailscale    ▼
┌──────────────────────────────────────────┴───┐   ┌─────────────────────────┐
│     PC PYTHON AGENT (FastAPI :8765)          │   │   Firebase (Firestore)  │
│  • OS Control (Volume, Media, Audio, Power)  │   │  • Firebase Auth        │
│  • Media/Docs (ffmpeg, yt-dlp, pypdf, rembg) │   │  • AES-256-GCM Vault    │
│  • GPU AI (faster-whisper, Gemini Vision/LLM)│   │  • Movies / Academic    │
│  • Explorer Right-Click & Downloads Watcher  │   │  • Frozen Tab Bundles   │
└──────────────────────────────────────────────┘   └─────────────────────────┘
```

### Network Layer: Local LAN + Tailscale + Cloudflare Tunnel
1. **Home Wi-Fi (`192.168.x.x:8765`):** Direct 30–60 MB/s transfer and movie streaming between PC and phone with zero internet usage.
2. **Away at University (`100.x.y.z:8765` via Tailscale):** Free private WireGuard mesh between your PC and phone. Lets you browse `C:/D:/E:/F:`, pull files, and control your PC from BRACU over mobile data without exposing your PC to the public internet.
3. **Sharing with Friends (`cloudflared` tunnel):** Ephemeral, single-file temporary URL (`https://*.trycloudflare.com/<token>`) only when sharing a specific file with a friend/teammate.

---

## 0. Current Codebase Security & Bug Fixes (`F:\Personalizer`)

- [x] **BUG-1: Replace Fake Auth (`script.js#L99-L138`)**
  - *Current:* Queries `users` collection from client JS with `btoa(pass)` Base64 passwords and `localStorage.setItem('loggedInUser', email)`.
  - *Fix:* Use `firebase/auth` (`signInWithEmailAndPassword` / Google Auth) and lock Firestore rules to `request.auth.uid == resource.data.uid`.
- [x] **BUG-2 & BUG-3: True Zero-Knowledge Encryption for Vault & Credentials (`script.js#L180`, `#L314-L500`)**
  - *Current:* `listenToVault()` injects NID/Passport data into DOM before PIN entry; credentials are stored in plaintext.
  - *Fix:* Derive a 256-bit `CryptoKey` in browser RAM from Master PIN via `crypto.subtle.deriveKey` (`PBKDF2`, 100,000 iterations, SHA-256). Encrypt/decrypt sensitive fields with `AES-256-GCM` (random 12-byte IV per record). Firestore stores only ciphertext.
- [x] **BUG-4: Real Biometric Unlock (`script.js#L312`)**
  - *Current:* `unlockVault()` only calls `alert("Biometrics initialization failed...")`.
  - *Fix:* Implement `navigator.credentials.create()` / `.get()` (`WebAuthn` platform authenticator for Windows Hello / Android fingerprint).
- [x] **BUG-5: Safe DOM Rendering (`script.js#L288`, `#L385`, `#L483`, `#L616`, `#L671`)**
  - *Current:* `container.innerHTML += ...` inside `snapshot.forEach()` (vulnerable to XSS and $O(N^2)$ DOM re-parsing).
  - *Fix:* Central `escapeHTML()` helper + single-pass `innerHTML` or `DocumentFragment` rendering.
- [x] **BUG-6: Firestore Listener Cleanup (`script.js#L167-L185`)**
  - *Current:* `onSnapshot()` unsubscribe callbacks are discarded on logout.
  - *Fix:* Store unsubscribe handles in an array and invoke all on `handleLogout()`.
- [x] **BUG-7: Fix Dead Clearbit Logo API (`script.js#L418`)**
  - *Current:* `https://logo.clearbit.com/...` is dead.
  - *Fix:* Use Google S2 Favicon API (`https://www.google.com/s2/favicons?domain=${hostname}&sz=128`) with local icon fallback.

---

## 1. Pillar 1 — Brave Browser Companion Extension (Manifest V3)

*(Note: Shopping site scraper removed per decision to avoid fragile store DOM selectors.)*

- [x] **1.1 IMDb In-Page Auto-Popup (`*://*.imdb.com/title/tt*`)**
  - **Mechanism:** Content script parses `<script type="application/ld+json">` directly from IMDb's DOM (`name`, `image`, `aggregateRating.ratingValue`, `duration`, `genre`, `description`).
  - **UI:** Injects **`[ + Plan to Watch ]`** and **`[ ✓ Watched ]`** buttons next to the movie title.
  - **Benefit:** Zero link copy-pasting, zero OMDB API key required.
- [x] **1.2 YouTube & Social Video In-Page Downloader**
  - **Mechanism:** Injects **`[ Download 1080p ]`** | **`[ Download MP3 ]`** buttons below the YouTube player.
  - **Flow:** Sends `POST http://127.0.0.1:8765/api/ytdl` with `{ url, format }`.
  - **Backend:** PC Agent executes `yt-dlp --cookies-from-browser brave --merge-output-format mp4` (with `ffmpeg`) and saves to `D:\YouTube\`. Auto-runs `yt-dlp -U` weekly on startup so YouTube changes never break it.
- [x] **1.3 YouTube Lecture -> Structured Study Notes Engine**
  - **Mechanism:** Injects **`[ Generate Study Notes ]`** below YouTube videos.
  - **Pipeline:**
    1. **Fast Path (~2s):** Pulls existing subtitles via `yt-dlp --write-auto-sub --skip-download`.
    2. **GPU Path (~30s/hr):** If captions are missing or inaccurate (e.g., heavy accents), downloads audio stream (`-f bestaudio`) and transcribes locally on RTX GPU using `faster-whisper (large-v3-turbo, float16)` (92–99% accuracy across English, South Asian accents, German, and Banglish).
    3. **Slide Frame Extractor:** Runs `ffmpeg -vf "select='gt(scene,0.3)',showinfo"` to capture slide/whiteboard changes as images.
    4. **Note Synthesis:** Sends transcript + timestamps to Gemini 2.5 Flash API to produce a structured `.md` study guide (Headings, $\LaTeX$ math `$$...$$`, code blocks, embedded slide images, and clickable `[mm:ss]` timestamps).
- [x] **1.4 1-Click Browser Workspace Freeze (RAM Saver)**
  - **Mechanism:** Extension popup button `chrome.tabs.query({ currentWindow: true })` saves `{ bundleName, urls[], titles[], createdAt }` to Firestore/local storage and closes the tabs to free RAM. 1-click restores all tabs via `chrome.tabs.create()`.

---

## 2. Pillar 2 — Windows PC Agent & File Explorer Power Tools

### 2A. OS, Media & Hotkey Controller
- [x] **2.1 Natural Language / Voice Command Bar & Bedside Remote**
  - **Endpoints:**
    - `POST /api/play-yt`: Queries YouTube for song/video title and launches `brave.exe https://www.youtube.com/watch?v=...`
    - `POST /api/media`: Controls system volume (`pycaw`), mute, and media keys (`play/pause`, `next`, `prev`).
    - `POST /api/audio-device`: Switches default Windows audio endpoint between Speakers and Headphones (`AudioDeviceCmdlets` / `NirCmd` / `pycaw`).
    - `POST /api/power`: Executes `lock` (`rundll32.exe user32.dll,LockWorkStation`), `sleep`, `shutdown`, or schedules a countdown sleep timer (`shutdown /s /t <seconds>` or timed sleep).
- [x] **2.2 Screen-Region Snipping (`Alt+S`) -> Code / LaTeX / Translate**
  - **Mechanism:** Global hotkey (`keyboard` + `mss` / `tkinter` overlay) captures selected screen region PNG into memory and sends to Gemini 2.5 Flash Vision:
    - **Mode 1 (`Copy Code`):** Extracts clean, indented code to clipboard.
    - **Mode 2 (`Copy LaTeX`):** Converts math equation image to $\LaTeX$ string (`\frac{...}{...}`).
    - **Mode 3 (`Translate`):** Translates German/foreign subtitle or image text + brief grammar breakdown.
- [x] **2.3 `Downloads` Folder Auto-Router (`watchdog`)**
  - Monitors `C:\Users\Rafsan\Downloads` in real time:
    - `*.torrent` -> Launches in `E:\qBittorrent\qbittorrent.exe` and deletes the `.torrent` file after load.
    - `*.zip` / `*.rar` / `*.7z` -> Triggers desktop toast notification with 1-click **`[ Extract to Subfolder & Delete Archive ]`**.
    - Research PDFs (`^\d{4}\.\d{4,5}.*\.pdf`) -> Queries arXiv API for title/year, renames to `[Year] Paper Title.pdf`, and moves to `D:\Papers\`.

### 2B. Windows File Explorer Right-Click & `Send To` Power Tools
*(Registered in both `HKCU\Software\Classes\*\shell\Jarvis` AND `shell:sendto` so Windows 11 never buries them)*

- [x] **2.4 Right-Click: "Instant Convert"**
  - `.webp` / `.heic` / `.avif` -> `.png` or `.jpg` (`Pillow` + `pillow-heif`).
  - `.docx` -> `.pdf` (`docx2pdf` / Word COM automation).
  - `.mkv` / `.mp4` / `.mov` -> `.mp3` audio or `<25 MB` target-bitrate `.mp4` (`ffmpeg`).
- [x] **2.5 Right-Click: "Remove Background (Local GPU)"**
  - Runs `rembg` (`bria-rmbg-1.4` or `birefnet-general` ONNX on CUDA) to output `<filename>_nobg.png` at full original resolution in `<1s` (for signatures, passport photos, and diagrams).
- [x] **2.6 Right-Click: "Auto-Fetch Subtitles"**
  - Computes 64-bit video file hash and queries OpenSubtitles via `subliminal` (~20 free downloads/day limit). Saves exact-sync `<videoname>.en.srt` in the same folder and launches `E:\VLC\vlc.exe`.
- [x] **2.7 Right-Click: "Transcribe Video / Audio to Study Notes (Local GPU)"**
  - Runs the same `faster-whisper (large-v3-turbo)` + `ffmpeg` slide-extractor + Markdown/LaTeX study note pipeline on any local `.mp4`/`.mkv`/`.mp3` file on disk.
- [x] **2.8 Right-Click: "Share File with Friend (Public Link)"**
  - Serves the single selected file on a random one-time token path and spawns `cloudflared tunnel --url http://127.0.0.1:8765/share/<token>`. Copies the public `https://*.trycloudflare.com/...` URL to clipboard and auto-terminates when download finishes or expires.

---

## 3. Pillar 3 — Phone <-> PC LAN / Tailscale Bridge

- [x] **3.1 High-Speed Wi-Fi "AirDrop" (PC <-> Phone)**
  - Drag-and-drop multi-file upload/download endpoint (`POST /api/drop`, `GET /api/drop/<file>`) over local Wi-Fi (`192.168.x.x`) at 30–60 MB/s with zero cloud size limit. Saves incoming phone files directly to `C:\Users\Rafsan\Desktop\PhoneDrop\`.
- [x] **3.2 Bedside LAN Movie Streamer (`D:\MOvies` -> Phone)**
  - Lists video directories (`D:\MOvies`, `D:\Steins;Gate [Complete]`, etc.) in the mobile PWA.
  - Streams via `GET /api/stream?path=...` supporting **HTTP `206 Partial Content` (Byte-Range Requests)** for instant seeking.
  - Provides both an in-browser `<video>` player (for `.mp4`) and a 1-tap **`[ Open in VLC Android ]`** button (`vlc://http://<pc-ip>:8765/api/stream?...`) for 10-bit `.mkv` / `x265` / multi-subtitle anime & movies.
- [x] **3.3 Phone Photo/Text -> PC `Ctrl+V` & Remote Drive Browser**
  - Sending text or an image from phone calls `POST /api/clipboard` -> injects text or bitmap directly into the Windows System Clipboard (`pyperclip` / `win32clipboard`) so you immediately press `Ctrl+V` on PC.
  - Full directory browser for `C:`, `D:`, `E:`, `F:` accessible over local Wi-Fi or Tailscale when at BRACU.

---

## 4. Pillar 4 — Zero-Knowledge Identity Vault & Unified PDF Engine

- [x] **4.1 Quick-Copy Identity Chips**
  - Pinned 1-tap copy pills on the dashboard for high-frequency personal strings: BRACU ID (`22201161`), Passport No & Expiry, NID, Bank Account/Routing No, Permanent Address, CGPA, GitHub, LinkedIn, Google Scholar.
- [x] **4.2 Unified PDF & Signature Engine (Shared Between Explorer Right-Click & Web UI)**
  - Single backend module (`pypdf` + `Pillow` + `pdf-lib` fallback) supporting both Windows right-click and Web UI drag-and-drop:
    1. **Merge selected PDFs / Images** into a single `Merged.pdf` in 0.5s.
    2. **Compress PDF / Image** under strict portal upload limits (`<2 MB` or `<200 KB`).
    3. **1-Click Stamp Signature:** Overlay your saved transparent signature PNG + today's date onto any PDF page.
