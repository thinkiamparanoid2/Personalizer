"""2.1: play YouTube by name, switch default audio output, power actions."""
import ctypes
import os
import subprocess
import threading
from ctypes import HRESULT, c_int, c_wchar_p
from pathlib import Path

from comtypes import CLSCTX_ALL, GUID, IUnknown, STDMETHOD, CoCreateInstance

from . import config
from .util import NO_WINDOW


def open_url(url):
    brave = config.load()["brave_exe"]
    if brave and Path(brave).exists():
        subprocess.Popen([brave, url], creationflags=NO_WINDOW)
    else:
        os.startfile(url)


def play_yt(query):
    """Open the top YouTube result for `query` (or the URL itself) in Brave."""
    query = query.strip()
    if query.startswith(("http://", "https://")):
        open_url(query)
        return {"url": query}
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
    entries = info.get("entries") or []
    if not entries:
        raise ValueError("No YouTube results")
    url = f"https://www.youtube.com/watch?v={entries[0]['id']}"
    open_url(url)
    return {"title": entries[0].get("title"), "url": url}


# --- Open installed apps by name (Start menu entries: desktop and Store apps) ---
_apps_cache = {"t": 0, "apps": []}


def installed_apps():
    import json
    import time
    if time.time() - _apps_cache["t"] > 600:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             creationflags=NO_WINDOW, timeout=30)
        apps = json.loads(out.stdout or "[]")
        _apps_cache.update(t=time.time(), apps=apps if isinstance(apps, list) else [apps])
    return _apps_cache["apps"]


VENDORS = {"microsoft", "ms", "google", "adobe"}  # shared prefixes that say nothing about which app


def match_app(name, apps):
    """Best Start-menu entry for a spoken name; None if nothing is close enough."""
    import difflib
    import re
    norm = lambda s: re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()  # noqa: E731
    q = [w for w in norm(name) if w not in {"the", "app", "application", "program", "my", "please"}]
    if not q:
        return None
    qs = " ".join(q)
    q_core = " ".join(w for w in q if w not in VENDORS)
    best, best_score = None, 0.0
    for app in apps:
        words = norm(app.get("Name", ""))
        n = " ".join(words)
        if not n or "uninstall" in n:
            continue
        core = " ".join(w for w in words if w not in VENDORS)
        if n == qs:
            score = 3.0
        elif n.startswith(qs) or all(w in words for w in q):
            score = 2.0 - len(n) / 1000  # prefer the shortest name ("VLC media player" over its variants)
        elif core and all(w in q for w in words):
            # The app's whole name is in the request: "Word" for "microsoft word document"
            score = 1.5 + len(words) / 100
        else:
            # Fuzzy, for misheard names ("valorent"), without vendor words so "microsoft word" isn't
            # "Microsoft Store"; same first letter so anagram-ish names ("teams"/"steam") don't match.
            score = (difflib.SequenceMatcher(None, q_core, core).ratio()
                     if q_core and core and q_core[0] == core[0] else 0.0)
        if score > best_score:
            best, best_score = app, score
    return best if best_score >= 0.75 else None


def open_app(name):
    app = match_app(name, installed_apps())
    if not app:
        if "." in name and " " not in name.strip():  # looks like a website
            url = name if name.startswith("http") else f"https://{name}"
            open_url(url)
            return {"opened": url}
        raise ValueError(f"No installed app called '{name}'")
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"])
    return {"opened": app["Name"]}


# --- Uninstall: open the program's own uninstaller (never silent; the user confirms in it) ---
def installed_programs():
    import winreg
    keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
    out = []
    for root, path in keys:
        try:
            base = winreg.OpenKey(root, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(base)[0]):
            try:
                sub = winreg.OpenKey(base, winreg.EnumKey(base, i))
                vals = {}
                for name in ("DisplayName", "UninstallString", "SystemComponent"):
                    try:
                        vals[name] = winreg.QueryValueEx(sub, name)[0]
                    except OSError:
                        pass
                if vals.get("DisplayName") and vals.get("UninstallString") and not vals.get("SystemComponent"):
                    out.append({"Name": vals["DisplayName"], "AppID": vals["UninstallString"]})
            except OSError:
                continue
    return out


def uninstall_app(name):
    prog = match_app(name, installed_programs())
    if not prog:
        raise ValueError(f"No installed program called '{name}'")
    subprocess.Popen(prog["AppID"])  # the program's uninstaller shows its own confirmation
    return {"uninstaller_opened": prog["Name"]}


# --- Close apps: graceful first, force only background leftovers ---
PROTECTED = {"explorer", "svchost", "csrss", "winlogon", "lsass", "services", "smss", "wininit", "dwm",
             "system", "registry", "python", "pythonw", "conhost", "powershell", "cmd", "taskmgr"}


def _processes():
    import json
    ps = ("Get-Process | Where-Object { $_.Path } | Select-Object Id, ProcessName, Description, MainWindowTitle, Path, "
          "@{n='Win';e={[int64]$_.MainWindowHandle}} | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=30)
    procs = json.loads(out.stdout or "[]")
    return procs if isinstance(procs, list) else [procs]


def select_targets(name, apps, procs):
    """(label, processes) to close for a spoken app name. Scoped to the app's install folder when known,
    otherwise to processes whose description/name matches; Windows and protected processes are never picked."""
    import re
    procs = [p for p in procs if p.get("Path") and not p["Path"].lower().startswith("c:\\windows")
             and p.get("ProcessName", "").lower() not in PROTECTED]
    app = match_app(name, apps)
    if app and re.match(r"^[a-zA-Z]:\\", app.get("AppID", "")):
        folder = str(Path(app["AppID"]).parent).lower().rstrip("\\")
        # skip folders that are just a drive or a generic Program Files root
        if folder.count("\\") >= 1 and not re.fullmatch(r"[a-z]:\\program files( \(x86\))?", folder):
            hits = [p for p in procs if p["Path"].lower().startswith(folder + "\\")]
            if hits:
                return app["Name"], hits
    named = [{"Name": p.get("Description") or p["ProcessName"], "AppID": str(p["Id"])} for p in procs]
    best = match_app(name, named)
    if not best:
        return None, []
    return best["Name"], [p for p in procs if (p.get("Description") or p["ProcessName"]) == best["Name"]]


def close_app(name):
    import time
    label, targets = select_targets(name, installed_apps(), _processes())
    if not targets:
        raise ValueError(f"'{name}' doesn't seem to be running")
    ids = ",".join(str(p["Id"]) for p in targets)
    # 1) ask windows to close (apps with unsaved work show their own save prompt)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Get-Process -Id {ids} -ErrorAction SilentlyContinue | ForEach-Object {{ [void]$_.CloseMainWindow() }}"],
                   creationflags=NO_WINDOW, timeout=30)
    time.sleep(4)
    alive = {p["Id"]: p for p in _processes()}
    left = [alive[p["Id"]] for p in targets if p["Id"] in alive]
    if any(p.get("Win") for p in left):
        return {"closing": label, "note": "a window is still open (maybe asking to save)"}
    # 2) only background/tray leftovers remain: stop them
    if left:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {','.join(str(p['Id']) for p in left)} -Force -ErrorAction SilentlyContinue"],
                       creationflags=NO_WINDOW, timeout=30)
    return {"closed": label}


# --- Default audio endpoint: undocumented-but-stable IPolicyConfig (what the Sound panel uses). ---
class IPolicyConfig(IUnknown):
    _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
    # Only SetDefaultEndpoint (11th method) is called; the first 10 are vtable placeholders.
    _methods_ = [STDMETHOD(HRESULT, f"_unused{i}", []) for i in range(10)] + [
        STDMETHOD(HRESULT, "SetDefaultEndpoint", [c_wchar_p, c_int]),
    ]


CLSID_POLICY_CONFIG = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")


def audio_devices():
    """Active playback endpoints. Call on a COM-initialised thread (like media.run)."""
    from pycaw.pycaw import AudioUtilities
    from pycaw.constants import AudioDeviceState
    default_id = AudioUtilities.GetSpeakers().id
    return [
        {"id": d.id, "name": d.FriendlyName, "default": d.id == default_id}
        for d in AudioUtilities.GetAllDevices()
        if d.id.startswith("{0.0.0.") and d.state == AudioDeviceState.Active  # {0.0.0.*} = render
    ]


def set_audio_device(device_id):
    if device_id not in {d["id"] for d in audio_devices()}:
        raise ValueError("Unknown audio device")
    policy = CoCreateInstance(CLSID_POLICY_CONFIG, IPolicyConfig, CLSCTX_ALL)
    for role in (0, 1, 2):  # eConsole, eMultimedia, eCommunications
        policy.SetDefaultEndpoint(device_id, role)
    return audio_devices()


# --- Power ---
_sleep_timer = None


def _suspend():
    ctypes.windll.powrprof.SetSuspendState(False, True, False)  # sleep (not hibernate)


def power(action, delay_s=0):
    global _sleep_timer
    delay_s = max(0, int(delay_s))
    if action == "lock":
        ctypes.windll.user32.LockWorkStation()
    elif action == "sleep":
        if _sleep_timer:
            _sleep_timer.cancel()
        _sleep_timer = threading.Timer(delay_s, _suspend) if delay_s else None
        _sleep_timer.start() if _sleep_timer else _suspend()
    elif action == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", str(delay_s)], creationflags=NO_WINDOW, check=True)
    elif action == "cancel":
        if _sleep_timer:
            _sleep_timer.cancel()
            _sleep_timer = None
        subprocess.run(["shutdown", "/a"], creationflags=NO_WINDOW)  # fails harmlessly if none pending
    else:
        raise ValueError(action)
    return {"action": action, "delay_s": delay_s}
