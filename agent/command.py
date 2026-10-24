"""2.1 Natural-language / voice command bar: speech or text -> one PC action.

Common phrasings are matched by rules (instant, offline); anything else goes to Gemini, which returns
a structured command. Sleep/shutdown always come back for confirmation first.
Run `python -m agent.command` for the self-check.
"""
import json
import os
import re
import tempfile
from typing import Literal, Optional

from pydantic import BaseModel

from . import config

MEDIA = Literal["volume_up", "volume_down", "mute", "play_pause", "next", "prev"]


class Command(BaseModel):
    action: Literal["media", "play_yt", "open_app", "close_app", "uninstall_app", "power", "audio_device", "ytdl", "none"]
    media: Optional[MEDIA] = None
    query: Optional[str] = None  # YouTube search, audio device hint, or video URL
    power: Optional[Literal["lock", "sleep", "shutdown", "cancel"]] = None
    delay_s: int = 0
    format: Literal["mp4", "mp3"] = "mp4"
    say: str = ""  # reply when action == "none"


NEEDS_CONFIRM = {"sleep", "shutdown"}
MAIN_THREAD = {"media", "audio_device"}  # COM (pycaw) objects live on the event-loop thread
UNITS = {"h": 3600, "hr": 3600, "hour": 3600, "hours": 3600, "m": 60, "min": 60, "mins": 60,
         "minute": 60, "minutes": 60, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1}


def _delay(t):
    """Seconds in 'in 1 hour 30 minutes' / 'after 1.5 h' / 'in 10' (bare number = minutes)."""
    m = re.search(r"\b(?:in|after)\s+(.+)", t)
    if not m:
        return 0
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", m.group(1))
    if parts:
        return int(sum(float(n) * UNITS[u if u in UNITS else u.rstrip("s")] for n, u in parts))
    bare = re.match(r"(\d+(?:\.\d+)?)\s*$", m.group(1))
    return int(float(bare.group(1)) * 60) if bare else 0


def parse_rules(text):
    """Return a Command for common phrasings, else None."""
    t = re.sub(r"[^\w\s:/.?=&-]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip(" .?")
    t = re.sub(r"^(hey |ok |okay )?(jarvis|computer)\s*", "", t).strip()
    if not t:
        return None
    # Power commands only as a whole request: "remind me to sleep at 11", "put on sleep music" and
    # "don't shut down" are not power commands (Gemini reads those).
    pc, when = r"(?: the| my)?(?: (?:pc|computer|laptop|system))?", r"(?: (?:in|after) .+)?"
    if re.fullmatch(r"(?:cancel|abort|stop)(?: the)? (?:shut ?down|sleep|timer|sleep timer|shut ?down timer)", t):
        return Command(action="power", power="cancel")
    if re.fullmatch(r"lock" + pc + r"( (screen|it))?", t):
        return Command(action="power", power="lock")
    if re.fullmatch(r"(?:shut ?(?:down|off)" + pc + r"|(?:turn|power) off(?: the| my)? (?:pc|computer|laptop|system))" + when, t):
        return Command(action="power", power="shutdown", delay_s=_delay(t))
    if re.fullmatch(r"(?:go to sleep|sleep" + pc + r"|(?:put|send)" + pc + r" to sleep)" + when, t):
        return Command(action="power", power="sleep", delay_s=_delay(t))
    if re.search(r"\b(volume up|louder|turn (it |the volume )?up|(increase|raise)( the)? volume)\b", t):
        return Command(action="media", media="volume_up")
    if re.search(r"\b(volume down|quieter|softer|turn (it |the volume )?down|(lower|decrease|reduce)( the)? volume)\b", t):
        return Command(action="media", media="volume_down")
    if re.fullmatch(r"(un)?mute( (it|sound|audio|the pc))?", t):
        return Command(action="media", media="mute")
    if re.fullmatch(r"(?:play )?(?:the )?next( (song|track|video|one))?|skip( (this|the))?( (song|track|video|one))?", t):
        return Command(action="media", media="next")
    if re.fullmatch(r"(previous|prev|back|go back|last)( (song|track|video|one))?", t):
        return Command(action="media", media="prev")
    if re.fullmatch(r"(pause|resume|play|stop|unpause|continue)( (it|music|the music|song|the song|video|the video))?", t):
        return Command(action="media", media="play_pause")
    if m := re.search(r"(https?://\S+)", text):
        if re.search(r"\b(download|save|get)\b", t):
            fmt = "mp3" if re.search(r"\b(mp3|audio|song)\b", t) else "mp4"
            return Command(action="ytdl", query=m.group(1), format=fmt)
    if re.fullmatch(r"(what( s| is) the )?time( is it)?( now)?|what time is it( now)?", t):  # ' became a space
        from datetime import datetime
        return Command(action="none", say=datetime.now().strftime("It's %I:%M %p.").replace(" 0", " "))
    if m := re.fullmatch(r"(?:uninstall|remove|delete)\s+(.+?)(?:\s+from (?:my |the )?(?:pc|computer|laptop))?", t):
        return Command(action="uninstall_app", query=m.group(1))
    if m := re.fullmatch(r"(?:close|quit|exit|kill|terminate)\s+(.+)", t):
        return Command(action="close_app", query=m.group(1))
    if m := re.fullmatch(r"(?:open|launch|start|run)\s+(.+?)\s+(?:on|from) youtube", t):
        return Command(action="play_yt", query=m.group(1))
    if m := re.fullmatch(r"(?:open|launch|start|run|fire up)\s+(?:up\s+)?(.+)", t):
        return Command(action="open_app", query=m.group(1))
    if m := re.fullmatch(r"(?:play|put on)\s+(.+?)(?:\s+(?:on|from) youtube)?", t):
        return Command(action="play_yt", query=m.group(1))
    # Audio output only when it's clearly about sound: "switch to dark mode" / "change the song to X" aren't.
    if (m := re.search(r"\b(?:switch|change|swap|set)\b.*?\bto\s+(?:the\s+|my\s+)?(.+)", t)) and (
            re.search(r"\b(audio|sound|output)\b", t) or re.search(
                r"\b(head ?phones?|headset|ear ?phones?|ear ?buds|buds|airpods|speakers?|tv|monitor|bluetooth|hdmi)\b",
                m.group(1))):
        return Command(action="audio_device", query=m.group(1))
    return None


PROMPT = """You turn a spoken request into one command for a Windows PC. Reply with JSON only:
{{"action": "media|play_yt|open_app|close_app|uninstall_app|power|audio_device|ytdl|none", "media": "volume_up|volume_down|mute|play_pause|next|prev",
"query": "...", "power": "lock|sleep|shutdown|cancel", "delay_s": 0, "format": "mp4|mp3", "say": "..."}}
- media: playback/volume keys.
- play_yt: query = what to search and play on YouTube.
- open_app: query = name of an installed PC app or game to launch (e.g. "Riot Client", "Discord"), or a website
  ("youtube", "gmail", or a URL). Use the app's usual short name ("Word", not "Microsoft Word document").
- close_app: query = name of a running app to close.
- uninstall_app: query = name of an installed program to uninstall (opens its uninstaller for confirmation).
- audio_device: query = output device hint, e.g. "headphones" or "speakers".
- power: delay_s = seconds from now (0 = now).
- ytdl: query = the video URL to download; format mp3 for audio only.
- Anything else: action "none" and a one-sentence reply in "say".
The request may be transcribed speech (English, Bangla or Banglish) with recognition errors.
Request: {text}"""


def interpret(text):
    cmd = parse_rules(text)
    # A rule saw "open/start X" or "remove/delete X", but X isn't an app or program: let Gemini read it
    # ("start the music", "run a virus scan", "delete this file", "remove milk from the shopping list").
    from .osctl import installed_apps, installed_programs, match_app, site_url
    if cmd and cmd.action == "open_app" and not (match_app(cmd.query, installed_apps()) or site_url(cmd.query)):
        cmd = None
    if (cmd and cmd.action == "uninstall_app" and not text.lower().lstrip().startswith("uninstall")
            and not match_app(cmd.query, installed_programs())):
        cmd = None
    if cmd:
        return cmd
    from google.genai import types
    from .util import gemini_generate
    try:
        resp = gemini_generate(PROMPT.format(text=text), fast=True,
                               config=types.GenerateContentConfig(response_mime_type="application/json"))
    except Exception as e:  # every model overloaded, timeout, quota...
        print("Gemini failed for command:", e)
        why = ("Gemini's free daily quota is used up (resets around 1 PM)" if "quota" in str(e).lower()
               else "Gemini is busy right now")
        return Command(action="none", say=f"{why}. Direct commands still work: 'volume down', 'pause', "
                                          "'open/close <app>', 'play <song> on YouTube'.")
    data = json.loads(resp.text)
    if isinstance(data, list):  # occasionally wrapped in a list
        data = data[0] if data else {}
    try:  # unused fields come back null or "" (both mean "not set")
        return Command.model_validate({k: v for k, v in data.items() if v not in (None, "")})
    except ValueError:  # pydantic ValidationError: a value outside the allowed choices
        return Command(action="none", say="Sorry, I didn't understand that. Try rephrasing?")


def transcribe_bytes(data, suffix=".webm"):
    from .notes import transcribe
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # Keep the latest clip for troubleshooting misheard commands (overwritten each time).
        with open(os.path.join(os.path.dirname(__file__), "last_voice" + suffix), "wb") as f:
            f.write(data)
        segments = transcribe(path, **_command_stt_options())
        return " ".join(text for _, text in segments).strip()
    finally:
        os.unlink(path)


# Biases recognition toward the words commands actually use (short clips are easy to mishear).
COMMAND_HINT = ("Jarvis commands: play, pause, resume, next song, previous, volume up, volume down, mute, "
                "lock the PC, sleep in 30 minutes, shut down, cancel shutdown, switch to headphones, "
                "switch to speakers, play on YouTube, download.")


def _command_stt_options():
    cfg = config.load()
    # No VAD: it can clip the first word of a short clip. Fixed language: auto-detection on
    # 2-second clips often picks the wrong language and returns gibberish.
    return {"model": cfg["command_whisper_model"], "language": cfg["command_language"] or None,
            "initial_prompt": COMMAND_HINT, "vad_filter": False, "beam_size": 5,
            "condition_on_previous_text": False}


def warm_up():
    """Load the command speech model at startup so the first voice command isn't slow."""
    import numpy as np
    from .notes import transcribe
    transcribe(np.zeros(16000, dtype=np.float32), **_command_stt_options())


HEADPHONES = ("headphone", "headset", "earphone", "earbud", "airpod", "buds")


def _match_device(hint, devices):
    hint = (hint or "").lower()
    words = set(re.findall(r"\w+", hint))
    def score(d):
        name = d["name"].lower()
        s = sum(w in name for w in words if len(w) > 2)
        if any(h in hint for h in HEADPHONES) and any(h in name for h in HEADPHONES):
            s += 5
        if "speaker" in hint and "speaker" in name:
            s += 5
        return s
    best = max(devices, key=score, default=None)
    if not best or score(best) == 0:
        raise ValueError(f"No audio device matches '{hint}'. Devices: {', '.join(d['name'] for d in devices)}")
    return best


def execute(cmd):
    """Run the command; returns a short description. media/audio_device must run on the main thread."""
    from . import jobs, media, osctl, tools
    if cmd.action == "media":
        r = media.run(cmd.media)
        vol = f" (volume {round(r['volume'] * 100)}%)" if "volume" in r else ""
        return f"{cmd.media.replace('_', ' ')}{vol}"
    if cmd.action == "play_yt":
        r = osctl.play_yt(cmd.query or "")
        return f"Playing {r.get('title') or r['url']}"
    if cmd.action == "uninstall_app":
        r = osctl.uninstall_app(cmd.query or "")
        return f"Opened the {r['uninstaller_opened']} uninstaller - confirm there"
    if cmd.action == "close_app":
        r = osctl.close_app(cmd.query or "")
        return f"Closed {r['closed']}" if "closed" in r else f"Asked {r['closing']} to close ({r['note']})"
    if cmd.action == "open_app":
        return f"Opening {osctl.open_app(cmd.query or '')['opened']}"
    if cmd.action == "audio_device":
        dev = _match_device(cmd.query, osctl.audio_devices())
        osctl.set_audio_device(dev["id"])
        return f"Audio output: {dev['name']}"
    if cmd.action == "power":
        osctl.power(cmd.power, cmd.delay_s)
        when = f" in {cmd.delay_s // 60} min" if cmd.delay_s >= 60 else (f" in {cmd.delay_s}s" if cmd.delay_s else "")
        return {"cancel": "Cancelled pending sleep/shutdown"}.get(cmd.power, f"{cmd.power.capitalize()}{when}")
    if cmd.action == "ytdl":
        jobs.submit("ytdl", tools.ytdl, cmd.query, cmd.format)
        return f"Downloading {cmd.format.upper()}"
    return cmd.say or "Sorry, I can't do that yet."


async def dispatch(cmd, confirmed=False, heard=None):
    from starlette.concurrency import run_in_threadpool
    if cmd.action == "power" and cmd.power in NEEDS_CONFIRM and not confirmed:
        when = f"in {cmd.delay_s // 60} min" if cmd.delay_s >= 60 else "now"
        return {"heard": heard, "confirm": f"{cmd.power.capitalize()} the PC {when}?", "command": cmd.model_dump()}
    if cmd.action == "uninstall_app" and not confirmed:
        # Some uninstallers (e.g. Discord's) run silently, so always confirm here first.
        from .osctl import installed_programs, match_app
        prog = match_app(cmd.query or "", installed_programs())
        if not prog:
            return {"heard": heard, "did": f"No installed program called '{cmd.query}'", "command": cmd.model_dump()}
        return {"heard": heard, "confirm": f"Uninstall {prog['Name'].strip()} from this PC?", "command": cmd.model_dump()}
    did = execute(cmd) if cmd.action in MAIN_THREAD else await run_in_threadpool(execute, cmd)
    return {"heard": heard, "did": did, "command": cmd.model_dump()}


def _self_check():
    def r(text):
        c = parse_rules(text)
        return c and (c.action, c.media or c.power or c.query, c.delay_s)
    assert r("pause") == ("media", "play_pause", 0)
    assert r("Jarvis, play") == ("media", "play_pause", 0)
    assert r("play lofi hip hop on YouTube") == ("play_yt", "lofi hip hop", 0)
    assert r("turn the volume up") == ("media", "volume_up", 0)
    assert r("turn it down") == ("media", "volume_down", 0)
    assert r("make it louder") == ("media", "volume_up", 0)
    assert r("lower the volume") == ("media", "volume_down", 0)
    assert r("next song") == ("media", "next", 0)
    assert r("mute") == ("media", "mute", 0)
    assert r("lock the pc") == ("power", "lock", 0)
    assert r("sleep in 30 minutes") == ("power", "sleep", 1800)
    assert r("shut down after 1 hour") == ("power", "shutdown", 3600)
    assert r("cancel the shutdown") == ("power", "cancel", 0)
    assert r("switch to headphones") == ("audio_device", "headphones", 0)
    assert r("open riot client") == ("open_app", "riot client", 0)
    assert r("launch valorant") == ("open_app", "valorant", 0)
    assert r("open lofi beats on youtube") == ("play_yt", "lofi beats", 0)
    # Power words inside other requests are not power commands; delays add up
    for not_power in ("put on sleep music", "remind me to sleep at 11pm", "don't shut down", "do not sleep",
                      "remind me to shut down the pc at 5", "how much sleep do i need", "turn off the lights"):
        assert (r(not_power) or ("",))[0] != "power", not_power
    assert r("sleep in 1 hour 30 minutes") == ("power", "sleep", 5400)
    assert r("go to sleep in 1.5 hours") == ("power", "sleep", 5400)
    assert r("shut down in 10") == ("power", "shutdown", 600)
    assert r("put the pc to sleep") == ("power", "sleep", 0)
    assert r("turn off the computer") == ("power", "shutdown", 0)
    assert r("lock my pc") == ("power", "lock", 0)
    assert r("play next song") == ("media", "next", 0) and r("skip this song") == ("media", "next", 0)
    assert r("what's the time")[0] == "none"
    for not_audio in ("switch to dark mode", "change the song to despacito", "swap to tab 2"):
        assert r(not_audio) is None, not_audio
    assert r("switch the audio to my monitor") == ("audio_device", "monitor", 0)
    from .osctl import match_app, site_url
    assert site_url("youtube") == "https://www.youtube.com" and site_url("the github.com") == "https://github.com"
    assert site_url("https://x.com/a") == "https://x.com/a" and site_url("a virus scan") is None and site_url("word") is None
    apps = [{"Name": "Riot Client", "AppID": "riot"}, {"Name": "VALORANT", "AppID": "val"},
            {"Name": "VLC media player", "AppID": "vlc"}, {"Name": "VLC media player skinned", "AppID": "vlc2"},
            {"Name": "Uninstall Discord", "AppID": "x"}, {"Name": "Discord", "AppID": "dc"}]
    assert match_app("riot client", apps)["AppID"] == "riot"
    assert match_app("the riot app", apps)["AppID"] == "riot"
    assert match_app("valorant", apps)["AppID"] == "val"
    assert match_app("vlc", apps)["AppID"] == "vlc"
    assert match_app("discord", apps)["AppID"] == "dc"
    assert match_app("photoshop", apps) is None
    office = [{"Name": "Word", "AppID": "word"}, {"Name": "WordPad", "AppID": "pad"},
              {"Name": "Microsoft Store", "AppID": "store"}, {"Name": "Microsoft Edge", "AppID": "edge"},
              {"Name": "Steam", "AppID": "steam"}]
    for spoken in ("microsoft word", "microsoft word document", "ms word", "word document", "word"):
        assert match_app(spoken, office)["AppID"] == "word", spoken   # Office names it just "Word"
    assert match_app("microsoft store", office)["AppID"] == "store"
    assert match_app("store", office)["AppID"] == "store"
    assert match_app("edge", office)["AppID"] == "edge"
    assert match_app("microsoft teams", office) is None              # not "Microsoft Store", "Edge" or "Steam"
    assert match_app("valorent", apps)["AppID"] == "val"             # misheard names still match
    assert r("close riot client") == ("close_app", "riot client", 0)
    assert r("quit discord") == ("close_app", "discord", 0)
    assert r("uninstall valorant from my computer") == ("uninstall_app", "valorant", 0)
    assert r("remove discord") == ("uninstall_app", "discord", 0)
    from .osctl import select_targets
    apps_real = [{"Name": "Riot Client", "AppID": r"F:\Riot Games\Riot Client\RiotClientServices.exe"},
                 {"Name": "Discord", "AppID": "com.squirrel.Discord.Discord"}]
    procs = [{"Id": 1, "ProcessName": "Riot Client", "Description": "Riot Client", "Path": r"F:\Riot Games\Riot Client\RiotClientElectron\Riot Client.exe"},
             {"Id": 2, "ProcessName": "RiotClientServices", "Description": "Riot Client", "Path": r"F:\Riot Games\Riot Client\RiotClientServices.exe"},
             {"Id": 3, "ProcessName": "vgtray", "Description": "Vanguard Tray", "Path": r"C:\Program Files\Riot Vanguard\vgtray.exe"},
             {"Id": 4, "ProcessName": "Discord", "Description": "Discord", "Path": r"C:\Users\x\AppData\Local\Discord\app-1\Discord.exe", "Win": 77},
             {"Id": 6, "ProcessName": "Discord", "Description": "Discord", "Path": r"C:\Users\x\AppData\Local\Discord\app-1\Discord.exe"},
             {"Id": 7, "ProcessName": "GameInputRedistService", "Description": "GameInput", "Path": r"C:\Program Files\GameInput\svc.exe"},
             {"Id": 5, "ProcessName": "explorer", "Description": "Windows Explorer", "Path": r"C:\Windows\explorer.exe", "Win": 9}]
    assert select_targets("the game", apps_real, procs)[1] == []          # windowless services never match by name
    label, t = select_targets("riot client", apps_real, procs)
    assert label == "Riot Client" and sorted(p["Id"] for p in t) == [1, 2], (label, t)  # not Vanguard
    label, t = select_targets("discord", apps_real, procs)
    assert [p["Id"] for p in t] == [4, 6], t                    # by description: the window plus its helper
    assert select_targets("windows explorer", apps_real, procs)[1] == []   # never Windows processes
    assert select_targets("photoshop", apps_real, procs)[1] == []
    c = parse_rules("download https://www.youtube.com/watch?v=abc as mp3")
    assert (c.action, c.query, c.format) == ("ytdl", "https://www.youtube.com/watch?v=abc", "mp3")
    assert parse_rules("what's the weather tomorrow") is None
    devs = [{"id": "1", "name": "Speakers (High Definition Audio Device)"},
            {"id": "2", "name": "Headphones (WH-1000XM4)"}]
    assert _match_device("my headphones", devs)["id"] == "2"
    assert _match_device("speakers", devs)["id"] == "1"
    print("command self-check OK")


if __name__ == "__main__":
    _self_check()
