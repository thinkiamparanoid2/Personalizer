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
    action: Literal["media", "play_yt", "power", "audio_device", "ytdl", "none"]
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
    m = re.search(r"\b(?:in|after)\s+(\d+)\s*(hours?|hr|h|minutes?|mins?|m|seconds?|secs?|sec|s)\b", t)
    return int(m.group(1)) * UNITS[m.group(2)] if m else 0


def parse_rules(text):
    """Return a Command for common phrasings, else None."""
    t = re.sub(r"[^\w\s:/.?=&-]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip(" .?")
    t = re.sub(r"^(hey |ok |okay )?(jarvis|computer)\s*", "", t).strip()
    if not t:
        return None
    if re.search(r"\b(cancel|abort|stop)\b.*\b(shut ?down|sleep|timer)\b", t):
        return Command(action="power", power="cancel")
    if re.fullmatch(r"lock( the)?( (pc|computer|screen|it))?", t):
        return Command(action="power", power="lock")
    if re.search(r"\bshut ?(down|off)\b|\bturn off( the)? (pc|computer)\b", t):
        return Command(action="power", power="shutdown", delay_s=_delay(t))
    if re.search(r"\b(go to sleep|sleep)\b", t) and not t.startswith("play"):
        return Command(action="power", power="sleep", delay_s=_delay(t))
    if re.search(r"\b(volume up|louder|turn (it |the volume )?up|(increase|raise)( the)? volume)\b", t):
        return Command(action="media", media="volume_up")
    if re.search(r"\b(volume down|quieter|softer|turn (it |the volume )?down|(lower|decrease|reduce)( the)? volume)\b", t):
        return Command(action="media", media="volume_down")
    if re.fullmatch(r"(un)?mute( (it|sound|audio|the pc))?", t):
        return Command(action="media", media="mute")
    if re.fullmatch(r"(next|skip)( (song|track|video|one))?", t):
        return Command(action="media", media="next")
    if re.fullmatch(r"(previous|prev|back|go back|last)( (song|track|video|one))?", t):
        return Command(action="media", media="prev")
    if re.fullmatch(r"(pause|resume|play|stop|unpause|continue)( (it|music|the music|song|the song|video|the video))?", t):
        return Command(action="media", media="play_pause")
    if m := re.search(r"(https?://\S+)", text):
        if re.search(r"\b(download|save|get)\b", t):
            fmt = "mp3" if re.search(r"\b(mp3|audio|song)\b", t) else "mp4"
            return Command(action="ytdl", query=m.group(1), format=fmt)
    if re.fullmatch(r"(what('s| is) the )?time( is it)?( now)?|what time is it( now)?", t):
        from datetime import datetime
        return Command(action="none", say=datetime.now().strftime("It's %I:%M %p.").replace(" 0", " "))
    if m := re.fullmatch(r"(?:play|put on|open)\s+(.+?)(?:\s+(?:on|from) youtube)?", t):
        return Command(action="play_yt", query=m.group(1))
    if m := re.search(r"\b(?:switch|change|swap)\b.*?\bto\s+(?:the\s+|my\s+)?(.+)", t):
        return Command(action="audio_device", query=m.group(1))
    return None


PROMPT = """You turn a spoken request into one command for a Windows PC. Reply with JSON only:
{{"action": "media|play_yt|power|audio_device|ytdl|none", "media": "volume_up|volume_down|mute|play_pause|next|prev",
"query": "...", "power": "lock|sleep|shutdown|cancel", "delay_s": 0, "format": "mp4|mp3", "say": "..."}}
- media: playback/volume keys.
- play_yt: query = what to search and play on YouTube.
- audio_device: query = output device hint, e.g. "headphones" or "speakers".
- power: delay_s = seconds from now (0 = now).
- ytdl: query = the video URL to download; format mp3 for audio only.
- Anything else: action "none" and a one-sentence reply in "say".
The request may be transcribed speech (English, Bangla or Banglish) with recognition errors.
Request: {text}"""


def interpret(text):
    cmd = parse_rules(text)
    if cmd:
        return cmd
    from google.genai import types
    from .util import gemini_generate
    try:
        resp = gemini_generate(PROMPT.format(text=text), fast=True,
                               config=types.GenerateContentConfig(response_mime_type="application/json"))
    except Exception as e:  # every model overloaded, timeout, quota...
        print("Gemini failed for command:", e)
        return Command(action="none", say="Gemini is busy right now. Try again, or use a direct command "
                                          "like 'volume down', 'pause' or 'play <song> on YouTube'.")
    data = json.loads(resp.text)
    if isinstance(data, list):  # occasionally wrapped in a list
        data = data[0] if data else {}
    return Command.model_validate({k: v for k, v in data.items() if v is not None})  # unused fields come back null


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
