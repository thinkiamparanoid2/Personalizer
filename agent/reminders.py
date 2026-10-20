"""Deadline reminders, morning brief and custom reminders, delivered as Windows toasts (and optionally
to a phone through ntfy). The web app syncs the planner here whenever it is open, so reminders still
fire when the app is closed. Run `python -m agent.reminders` for the self-check.
"""
import json
import threading
import time
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config
from .util import notify

STATE_PATH = Path(__file__).with_name("reminders.json")
_lock = threading.Lock()

WEATHER = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy",
           51: "drizzle", 53: "drizzle", 55: "drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
           66: "freezing rain", 67: "freezing rain", 71: "snow", 73: "snow", 75: "heavy snow", 80: "showers",
           81: "showers", 82: "heavy showers", 95: "thunderstorms", 96: "thunderstorms", 99: "thunderstorms"}


# ---------- state ----------
def _load():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _update(fn):
    with _lock:
        state = _load()
        result = fn(state)
        _save(state)
        return result


def sync(data):
    """Planner snapshot from the web app: {academic: [{type,title,date}], shopping_pending, name, app_url}."""
    def apply(state):
        state["data"] = {
            "academic": [{k: e.get(k) for k in ("type", "title", "date")} for e in data.get("academic", [])
                         if e.get("date") and e.get("title")][:500],
            "shopping_pending": int(data.get("shopping_pending") or 0),
            "name": str(data.get("name") or "")[:40],
            "app_url": str(data.get("app_url") or "")[:200],
            "synced": time.time(),
        }
    _update(apply)
    return {"synced": True}


def add(text, at):
    """at: local 'YYYY-MM-DDTHH:MM' (or with seconds)."""
    when = datetime.fromisoformat(at)
    if when < datetime.now() - timedelta(minutes=1):
        raise ValueError(f"{at} is in the past")
    item = {"id": uuid.uuid4().hex[:8], "text": str(text)[:300], "at": when.isoformat(timespec="minutes")}
    _update(lambda s: s.setdefault("custom", []).append(item))
    return item


def list_custom():
    return sorted(_load().get("custom", []), key=lambda r: r["at"])


def cancel(rid):
    def apply(state):
        before = len(state.get("custom", []))
        state["custom"] = [r for r in state.get("custom", []) if r["id"] != rid]
        return before != len(state["custom"])
    return {"cancelled": _update(apply)}


# ---------- content ----------
def weather():
    cfg = config.load()
    lat, lon = cfg.get("weather_lat"), cfg.get("weather_lon")
    if lat is None or lon is None:
        return None
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&timezone=auto&forecast_days=1"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.loads(r.read())["daily"]
        return (f"{WEATHER.get(d['weather_code'][0], 'mixed')}, {round(d['temperature_2m_max'][0])}°/"
                f"{round(d['temperature_2m_min'][0])}°, {d['precipitation_probability_max'][0]}% rain")
    except Exception as e:
        print("weather failed:", e)
        return None


def _events(state, today):
    out = []
    for e in state.get("data", {}).get("academic", []):
        try:
            days = (date.fromisoformat(e["date"]) - today).days
        except ValueError:
            continue
        if days >= 0:
            out.append((days, e))
    return sorted(out, key=lambda x: (x[0], x[1]["title"]))


def brief(state=None, today=None, include_weather=True):
    """Morning brief text: (title, body)."""
    state = state if state is not None else _load()
    today = today or date.today()
    data = state.get("data", {})
    name = data.get("name") or ""
    events = _events(state, today)
    due_today = [e for d, e in events if d == 0]
    week = [(d, e) for d, e in events if 1 <= d <= 7]
    lines = []
    if due_today:
        lines.append("Today: " + ", ".join(f"{e['title']} ({e['type']})" for e in due_today))
    if week:
        lines.append("This week: " + ", ".join(
            f"{e['title']} {'tomorrow' if d == 1 else (today + timedelta(days=d)).strftime('%a')}" for d, e in week[:5]))
    if not due_today and not week:
        lines.append("Nothing due this week.")
    todays = [r for r in state.get("custom", []) if r["at"][:10] == today.isoformat()]
    if todays:
        lines.append("Reminders: " + ", ".join(f"{r['at'][11:16]} {r['text']}" for r in todays))
    if data.get("shopping_pending"):
        lines.append(f"{data['shopping_pending']} item(s) on the shopping list.")
    if include_weather and (w := weather()):
        lines.append(f"Weather: {w}.")
    return f"Good morning{', ' + name if name else ''}", "\n".join(lines)


def _hhmm(value, default):
    try:
        h, m = map(int, str(value).split(":"))
        return h, m
    except ValueError:
        return default


def _deliver(title, body, sound=False):
    cfg = config.load()
    url = _load().get("data", {}).get("app_url") or ""
    notify(title, body, launch=url, long=True, sound=sound)
    if topic := cfg.get("ntfy_topic"):
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                                         headers={"Title": title.encode("utf-8"), "Click": url or ""})
            urllib.request.urlopen(req, timeout=8).close()
        except Exception as e:
            print("ntfy failed:", e)
    if cfg.get("speak_brief") and title.startswith("Good morning"):
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            win32com.client.Dispatch("SAPI.SpVoice").Speak(f"{title}. {body}")
        except Exception as e:
            print("speak failed:", e)


# ---------- scheduler ----------
def tick(now=None, deliver=_deliver):
    """One scheduler pass. Sends at most once per day for the brief and the evening heads-up."""
    now = now or datetime.now()
    cfg = config.load()
    today = now.date()
    state = _load()
    sent = state.setdefault("sent", {})

    bh, bm = _hhmm(cfg.get("brief_time", "08:00"), (8, 0))
    brief_at = now.replace(hour=bh, minute=bm, second=0, microsecond=0)
    # Late PC start: still brief until early afternoon, but never an evening "good morning".
    if brief_at <= now < brief_at.replace(hour=14, minute=0) and sent.get("brief") != today.isoformat():
        sent["brief"] = today.isoformat()
        _save(state)
        deliver(*brief(state, today))

    eh, em = _hhmm(cfg.get("evening_reminder_time", "20:00"), (20, 0))
    if now >= now.replace(hour=eh, minute=em, second=0, microsecond=0) and sent.get("evening") != today.isoformat():
        sent["evening"] = today.isoformat()
        _save(state)
        tomorrow = [e for d, e in _events(state, today) if d == 1]
        if tomorrow:
            deliver("Due tomorrow", "\n".join(f"{e['title']} ({e['type']})" for e in tomorrow), True)

    due = [r for r in state.get("custom", []) if datetime.fromisoformat(r["at"]) <= now]
    if due:
        state["custom"] = [r for r in state["custom"] if r not in due]
        _save(state)
        for r in due:
            deliver("Reminder", r["text"], True)


def start():
    def loop():
        while True:
            try:
                tick()
            except Exception as e:
                print("reminder tick failed:", e)
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name="reminders").start()


def _self_check():
    import tempfile
    global STATE_PATH
    real = STATE_PATH
    STATE_PATH = Path(tempfile.mkdtemp()) / "reminders.json"
    try:
        today = date(2026, 10, 21)
        _save({"data": {"name": "Rafsan", "shopping_pending": 2, "academic": [
            {"type": "Quiz", "title": "CSE330 Quiz", "date": "2026-10-21"},
            {"type": "Assignment", "title": "CSE421 Report", "date": "2026-10-22"},
            {"type": "Lab Test", "title": "PHY112 Final", "date": "2026-11-20"}]}})
        title, body = brief(today=today, include_weather=False)
        assert title == "Good morning, Rafsan" and "Today: CSE330 Quiz (Quiz)" in body and "CSE421 Report tomorrow" in body
        assert "PHY112" not in body and "2 item(s)" in body, body

        sent = []
        deliver = lambda t, b, sound=False: sent.append(t)  # noqa: E731
        tick(datetime(2026, 10, 21, 7, 59), deliver)
        assert sent == []
        cfg = config.load()
        cfg_backup = dict(cfg)
        cfg.update(weather_lat=None, weather_lon=None)
        tick(datetime(2026, 10, 21, 8, 0), deliver)
        tick(datetime(2026, 10, 21, 9, 0), deliver)          # once per day
        assert sent == ["Good morning, Rafsan"], sent
        tick(datetime(2026, 10, 21, 20, 5), deliver)
        assert sent[-1] == "Due tomorrow", sent
        state = _load()
        state["custom"] = [{"id": "x", "text": "call Ammu", "at": "2026-10-21T21:00"}]
        _save(state)
        tick(datetime(2026, 10, 21, 20, 59), deliver)
        assert sent[-1] == "Due tomorrow"
        tick(datetime(2026, 10, 21, 21, 0), deliver)
        assert sent[-1] == "Reminder" and _load()["custom"] == [], sent
        cfg.clear(); cfg.update(cfg_backup)
        print("reminders self-check OK")
    finally:
        STATE_PATH = real


if __name__ == "__main__":
    _self_check()
