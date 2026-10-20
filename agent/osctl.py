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
