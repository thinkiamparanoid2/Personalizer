"""Farvis chat assistant: multi-turn Gemini with tool calling.

PC tools run here in the agent. App-data tools (academic events, shopping, movies, power with a
confirmation) run in the web app, which owns the Firestore data: when the model calls one, the
pending calls go back to the browser, which executes them and posts the results.
Conversations live in memory per session id (lost on restart, which is fine for chat).
"""
import time
import uuid

from google.genai import types

from .util import gemini_generate

SESSION_TTL = 2 * 3600
MAX_TURNS = 40  # Content entries kept per session
_sessions = {}


def _fn(name, description, properties=None, required=()):
    return types.FunctionDeclaration(
        name=name, description=description,
        parameters_json_schema={"type": "object", "properties": properties or {}, "required": list(required)})


SERVER_TOOLS = [
    _fn("media_control", "Control music/video playback and system volume on the PC.",
        {"action": {"type": "string", "enum": ["volume_up", "volume_down", "mute", "play_pause", "next", "prev"]}},
        ["action"]),
    _fn("play_youtube", "Search YouTube and play the top result in the PC browser.",
        {"query": {"type": "string", "description": "Song, video or channel to search for"}}, ["query"]),
    _fn("set_audio_output", "Switch the PC's default audio output device.",
        {"device_hint": {"type": "string", "description": "e.g. 'headphones', 'speakers', 'TV'"}}, ["device_hint"]),
    _fn("download_video", "Download a YouTube/online video to the PC as MP4, or audio-only MP3.",
        {"url": {"type": "string"}, "format": {"type": "string", "enum": ["mp4", "mp3"]}}, ["url"]),
    _fn("make_study_notes", "Generate Markdown study notes from a lecture video URL (runs in the background).",
        {"url": {"type": "string"}}, ["url"]),
    _fn("check_jobs", "Status of recent downloads and study-notes jobs."),
    _fn("set_reminder", "Set a reminder that pops up on the PC (and phone if configured) at a given time.",
        {"text": {"type": "string", "description": "What to remind about"},
         "at": {"type": "string", "description": "Local date-time YYYY-MM-DDTHH:MM, resolved from relative "
                                                 "phrases like 'in 20 minutes' or 'tomorrow 9am' using the current time"}},
        ["text", "at"]),
    _fn("list_reminders", "List upcoming custom reminders."),
    _fn("cancel_reminder", "Cancel a custom reminder by its id (get ids from list_reminders).",
        {"id": {"type": "string"}}, ["id"]),
    _fn("get_briefing", "Today's briefing: deadlines today and this week, reminders, shopping, weather."),
    _fn("list_study_notes", "List the user's generated lecture study notes (titles and sources)."),
    _fn("read_study_notes", "Read the full Markdown of the study notes that best match a topic or title, "
                            "to answer questions about a lecture.",
        {"query": {"type": "string", "description": "Lecture title or topic words"}}, ["query"]),
]

CLIENT_TOOLS = [
    _fn("list_academic_events", "List the user's upcoming quizzes, assignments and lab tests.",
        {"days_ahead": {"type": "integer", "description": "How many days ahead to include (default 30)"}}),
    _fn("add_academic_event", "Add a quiz, assignment or lab test to the academic planner.",
        {"type": {"type": "string", "enum": ["Quiz", "Assignment", "Lab Test"]},
         "title": {"type": "string", "description": "Course and topic, e.g. 'CSE330 Midterm'"},
         "date": {"type": "string", "description": "YYYY-MM-DD, resolved from relative dates using today's date"}},
        ["type", "title", "date"]),
    _fn("list_shopping_items", "List shopping list items.",
        {"include_bought": {"type": "boolean"}}),
    _fn("add_shopping_item", "Add an item to the shopping list.",
        {"description": {"type": "string"}, "price": {"type": "string"}, "link": {"type": "string"}}, ["description"]),
    _fn("mark_shopping_bought", "Mark a pending shopping item as bought.",
        {"description": {"type": "string", "description": "Name of the item (fuzzy match)"}}, ["description"]),
    _fn("list_movies", "List the user's movie/series watchlist.",
        {"status": {"type": "string", "enum": ["watched", "plan-to-watch"]}}),
    _fn("power_action", "Lock, sleep or shut down the PC, or cancel a pending sleep/shutdown. "
                        "The user is asked to confirm sleep and shutdown.",
        {"action": {"type": "string", "enum": ["lock", "sleep", "shutdown", "cancel"]},
         "delay_minutes": {"type": "integer"}}, ["action"]),
]
CLIENT_NAMES = {f.name for f in CLIENT_TOOLS}

SYSTEM = """You are Farvis, {name}'s personal assistant, running on their Windows PC and web app.
Now: {now}. Timezone: {tz}. Use this to resolve times like "in 20 minutes" or "tomorrow 9am".
Be brief and friendly, like a capable human assistant: one to three sentences unless asked for detail.
Use the tools to act or to look things up; never invent the user's events, items or files.
Resolve relative dates ("Thursday", "next week") to YYYY-MM-DD from today's date before calling tools.
When several things are asked, call several tools. After acting, confirm what you did in plain words.
You cannot see the encrypted vault or saved passwords; say so if asked.
For general questions, answer from your own knowledge."""


def _run_server_tool(name, args):
    from . import command, jobs, media, notes, osctl, reminders, tools
    try:
        if name in ("media_control", "set_audio_output"):
            import comtypes
            comtypes.CoInitialize()  # pycaw needs COM on this worker thread
            if name == "media_control":
                return media.run(args["action"])
            dev = command._match_device(args.get("device_hint"), osctl.audio_devices())
            osctl.set_audio_device(dev["id"])
            return {"switched_to": dev["name"]}
        if name == "play_youtube":
            return osctl.play_yt(args["query"])
        if name == "download_video":
            job = jobs.submit("ytdl", tools.ytdl, args["url"], args.get("format", "mp4"))
            return {"started": True, "job_id": job["id"]}
        if name == "make_study_notes":
            job = jobs.submit("notes", notes.notes_from_url, args["url"])
            return {"started": True, "job_id": job["id"], "note": "takes a few minutes"}
        if name == "set_reminder":
            return {"ok": True, "reminder": reminders.add(args["text"], args["at"])}
        if name == "list_reminders":
            return {"reminders": reminders.list_custom()}
        if name == "cancel_reminder":
            return reminders.cancel(args["id"])
        if name == "get_briefing":
            title, body = reminders.brief()
            return {"briefing": f"{title}. {body}"}
        if name == "list_study_notes":
            lst = notes.list_notes()
            return {"available": lst["available"], "notes": [{"title": n["title"], "source": n["source"]}
                                                             for n in lst["notes"][:50]]}
        if name == "read_study_notes":
            best = notes.search_notes(args.get("query"))
            if not best:
                return {"error": "no matching study notes"}
            md = notes.get_note(best["id"])["markdown"]
            return {"title": best["title"], "markdown": md[:30000], "truncated": len(md) > 30000}
        if name == "check_jobs":
            return {"jobs": [{"kind": j["kind"], "status": j["status"], "progress": j["progress"],
                              "title": (j.get("result") or {}).get("title"), "error": j["error"]}
                             for j in jobs.recent()[:6]]}
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": str(e)[:300]}


def _session(sid):
    now = time.time()
    for k in [k for k, s in _sessions.items() if now - s["t"] > SESSION_TTL]:
        del _sessions[k]
    s = _sessions.setdefault(sid, {"contents": [], "pending": None, "actions": []})
    s["t"] = now
    return s


def _trim(contents):
    """Keep the latest turns, cutting only at a plain user message so call/response pairs stay intact."""
    if len(contents) <= MAX_TURNS:
        return contents
    for i in range(len(contents) - MAX_TURNS, len(contents)):
        c = contents[i]
        if c.role == "user" and all(p.text for p in c.parts or []):
            return contents[i:]
    return contents[-MAX_TURNS:]


def step(sid, message=None, tool_results=None, context=None):
    """Advance the conversation. Returns {"reply", "actions"} when done, or {"calls": [...]} when the
    browser must run app tools first (then call again with tool_results)."""
    s = _session(sid or uuid.uuid4().hex)
    ctx = context or {}
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM.format(name=ctx.get("name") or "the user", now=ctx.get("now") or time.ctime(),
                                         tz=ctx.get("tz") or "local"),
        tools=[types.Tool(function_declarations=SERVER_TOOLS + CLIENT_TOOLS)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(thinking_level="LOW"))  # snappier replies for chat

    if message:
        s["actions"] = []
        s["contents"].append(types.Content(role="user", parts=[types.Part(text=message)]))
    if tool_results is not None:
        pending = s.pop("pending", None) or []
        client_results = iter(tool_results)  # browser answers in the same order as the calls it got
        parts = []
        for name, result in pending:  # same order as the model's calls
            if name in CLIENT_NAMES:
                result = next(client_results, {}).get("response", {"error": "not executed"})
            parts.append(types.Part.from_function_response(name=name, response=result))
            s["actions"].append({"tool": name, "result": result})
        s["contents"].append(types.Content(role="user", parts=parts))

    for _ in range(6):  # tool round trips per user message
        s["contents"] = _trim(s["contents"])
        resp = gemini_generate(s["contents"], config=config, fast=True, timeout_s=30)
        content = resp.candidates[0].content if resp.candidates else None
        if content is None:
            return {"reply": "Sorry, I didn't get a response. Try again?", "actions": s["actions"]}
        s["contents"].append(content)
        calls = [p.function_call for p in content.parts or [] if p.function_call]
        if not calls:
            return {"reply": (resp.text or "").strip(), "actions": s["actions"]}
        results = [(c.name, None if c.name in CLIENT_NAMES else _run_server_tool(c.name, dict(c.args or {})))
                   for c in calls]
        client_calls = [{"name": c.name, "args": dict(c.args or {})} for c in calls if c.name in CLIENT_NAMES]
        if client_calls:
            s["pending"] = results
            return {"calls": client_calls}
        s["contents"].append(types.Content(role="user", parts=[
            types.Part.from_function_response(name=n, response=r) for n, r in results]))
        s["actions"].extend({"tool": n, "result": r} for n, r in results)
    return {"reply": "That took too many steps; could you rephrase?", "actions": s["actions"]}


def reset(sid):
    _sessions.pop(sid, None)
