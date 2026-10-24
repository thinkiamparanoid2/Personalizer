"""Farvis routines: a prompt Farvis runs on a schedule by itself ("Sunday 8pm: plan my week"),
delivering the answer like a reminder (Windows toast, plus the phone through ntfy).
Run `python -m agent.routines` for the self-check.
"""
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

PATH = Path(__file__).with_name("routines.json")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
LATE_OK_MIN = 120  # PC started late: still run a routine up to 2 h after its time, then skip that day
MAX_ROUTINES = 20
_lock = threading.Lock()


def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(items):
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def add(prompt, at, days=None, name=None):
    """at: 'HH:MM' local time; days: subset of DAYS (default every day); name: short notification title."""
    prompt = " ".join(str(prompt).split())[:500]
    name = " ".join(str(name or "").split())[:40] or prompt[:40]
    if not prompt:
        raise ValueError("routine needs a prompt")
    h, m = (int(x) for x in str(at).split(":"))
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"bad time {at}")
    days = [d.lower()[:3] for d in (days or DAYS)]
    if not days or any(d not in DAYS for d in days):
        raise ValueError(f"days must be from {DAYS}")
    item = {"id": uuid.uuid4().hex[:6], "name": name, "prompt": prompt, "at": f"{h:02d}:{m:02d}",
            "days": [d for d in DAYS if d in days], "last": None}
    with _lock:
        items = _load()
        if len(items) >= MAX_ROUTINES:
            raise ValueError(f"at most {MAX_ROUTINES} routines; cancel one first")
        _save(items + [item])
    return item


def list_all():
    return [{k: r[k] for k in ("id", "name", "prompt", "at", "days")} for r in _load()]


def cancel(rid):
    with _lock:
        items = _load()
        _save([r for r in items if r["id"] != rid])
    return {"cancelled": len(items) != len(_load())}


def due(now):
    """Routines to run now; each is marked as run for today so it fires once per day."""
    today = now.date().isoformat()
    with _lock:
        items = _load()
        ready = []
        for r in items:
            h, m = map(int, r["at"].split(":"))
            late = (now - now.replace(hour=h, minute=m, second=0, microsecond=0)).total_seconds() / 60
            if DAYS[now.weekday()] in r["days"] and 0 <= late <= LATE_OK_MIN and r["last"] != today:
                r["last"] = today
                ready.append(r)
        if ready:
            _save(items)
    return ready


def run(r, deliver=None):
    from . import assistant, reminders
    deliver = deliver or reminders._deliver
    title = f"Farvis: {r['name']}"
    try:
        reply = assistant.run_routine(r["prompt"])
    except Exception as e:  # Gemini down or out of quota: say so instead of failing silently
        reply = f"Couldn't run this routine: {str(e)[:200]}"
    deliver(title, reply)


def start():
    def loop():
        while True:
            try:
                for r in due(datetime.now()):
                    threading.Thread(target=run, args=(r,), daemon=True, name="routine").start()
            except Exception as e:
                print("routine check failed:", e)
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name="routines").start()


def _self_check():
    import tempfile
    global PATH
    real = PATH
    PATH = Path(tempfile.mkdtemp()) / "routines.json"
    try:
        r = add("Look at my deadlines and plan the week", "20:00", ["Sun"], name="Weekly plan")
        assert r["at"] == "20:00" and r["days"] == ["sun"] and r["name"] == "Weekly plan"
        daily = add("  Brief   me ", "7:5")
        assert daily["prompt"] == daily["name"] == "Brief me" and daily["at"] == "07:05" and daily["days"] == DAYS
        for bad in (("", "10:00"), ("x", "25:00"), ("x", "10:00", ["someday"])):
            try:
                add(*bad)
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass
        sun = datetime(2026, 10, 25, 19, 59)                       # a Sunday
        assert due(sun) == []
        assert [x["id"] for x in due(sun.replace(hour=20, minute=0))] == [r["id"]]
        assert due(sun.replace(hour=20, minute=30)) == []          # once per day
        assert [x["id"] for x in due(datetime(2026, 10, 26, 9, 0))] == [daily["id"]]  # Monday, ran late
        assert due(datetime(2026, 10, 27, 9, 6)) == []             # more than 2 h late: skipped
        assert cancel(r["id"]) == {"cancelled": True} and cancel(r["id"]) == {"cancelled": False}
        sent = []
        import agent.assistant as a
        real_run, a.run_routine = a.run_routine, lambda p: f"done: {p}"
        try:
            run(daily, deliver=lambda t, b: sent.append((t, b)))
            a.run_routine = lambda p: (_ for _ in ()).throw(RuntimeError("quota"))
            run(daily, deliver=lambda t, b: sent.append((t, b)))
        finally:
            a.run_routine = real_run
        assert sent == [("Farvis: Brief me", "done: Brief me"),
                        ("Farvis: Brief me", "Couldn't run this routine: quota")], sent
        print("routines self-check OK")
    finally:
        PATH = real


if __name__ == "__main__":
    _self_check()
