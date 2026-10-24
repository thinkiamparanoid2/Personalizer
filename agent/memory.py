"""Farvis long-term memory: short facts about the user that survive restarts and new chats.
Every fact is put into the assistant's system prompt, so Farvis can use them without a lookup.
Run `python -m agent.memory` for the self-check.
"""
import json
import threading
import time
import uuid
from pathlib import Path

PATH = Path(__file__).with_name("memory.json")
MAX_FACTS = 200   # ponytail: all facts go into every prompt; past ~200, rank by relevance to the message instead
MAX_LEN = 300
_lock = threading.Lock()


def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(facts):
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(facts, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def _norm(text):
    return " ".join(str(text).lower().split()).rstrip(".")


def facts():
    return _load()


def remember(text):
    text = " ".join(str(text).split())[:MAX_LEN]
    if not text:
        raise ValueError("nothing to remember")
    with _lock:
        all_facts = _load()
        same = next((f for f in all_facts if _norm(f["text"]) == _norm(text)), None)
        if same:
            return {"ok": True, "remembered": same, "already_known": True}
        fact = {"id": uuid.uuid4().hex[:6], "text": text, "created": int(time.time())}
        all_facts = (all_facts + [fact])[-MAX_FACTS:]  # oldest facts drop off first
        _save(all_facts)
    return {"ok": True, "remembered": fact}


def forget(fact_id):
    with _lock:
        all_facts = _load()
        gone = next((f for f in all_facts if f["id"] == fact_id), None)
        if not gone:
            return {"error": f"no memory with id {fact_id}"}
        _save([f for f in all_facts if f["id"] != fact_id])
    return {"ok": True, "forgot": gone["text"]}


def prompt_block():
    """Facts as system-prompt lines, with ids so the model can forget or replace one."""
    lines = [f"- [{f['id']}] {f['text']}" for f in _load()]
    return "\n".join(lines) if lines else "(nothing yet)"


def _self_check():
    import tempfile
    global PATH
    real = PATH
    PATH = Path(tempfile.mkdtemp()) / "memory.json"
    try:
        assert prompt_block() == "(nothing yet)"
        a = remember("  CSE330 lab is in room 7A ")["remembered"]
        assert a["text"] == "CSE330 lab is in room 7A"
        again = remember("cse330 lab is in   room 7A.")
        assert again["already_known"] and again["remembered"]["id"] == a["id"] and len(facts()) == 1
        b = remember("Likes lofi while studying")["remembered"]
        assert prompt_block() == f"- [{a['id']}] CSE330 lab is in room 7A\n- [{b['id']}] Likes lofi while studying"
        assert forget(a["id"]) == {"ok": True, "forgot": "CSE330 lab is in room 7A"}
        assert "error" in forget(a["id"]) and [f["id"] for f in facts()] == [b["id"]]
        assert len(remember("x" * 999)["remembered"]["text"]) == MAX_LEN
        try:
            remember("   ")
            raise AssertionError("empty fact accepted")
        except ValueError:
            pass
        for i in range(MAX_FACTS + 5):
            remember(f"fact {i}")
        assert len(facts()) == MAX_FACTS and facts()[-1]["text"] == f"fact {MAX_FACTS + 4}"
        print("memory self-check OK")
    finally:
        PATH = real


if __name__ == "__main__":
    _self_check()
