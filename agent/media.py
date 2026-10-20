"""System volume (pycaw) and media keys (keybd_event)."""
import ctypes

from pycaw.pycaw import AudioUtilities

VK = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1}
KEYEVENTF_KEYUP = 0x2
STEP = 0.05


def _tap(vk):
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def run(action):
    """Must run on a COM-initialised thread (comtypes initialises the importing thread)."""
    if action in VK:
        _tap(VK[action])
        return {"action": action}
    vol = AudioUtilities.GetSpeakers().EndpointVolume
    if action == "mute":
        vol.SetMute(not vol.GetMute(), None)
    else:
        level = vol.GetMasterVolumeLevelScalar() + (STEP if action == "volume_up" else -STEP)
        vol.SetMasterVolumeLevelScalar(min(1.0, max(0.0, level)), None)
    return {"action": action, "volume": round(vol.GetMasterVolumeLevelScalar(), 2), "muted": bool(vol.GetMute())}
