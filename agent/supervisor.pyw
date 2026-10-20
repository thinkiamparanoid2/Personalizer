"""Keeps the agent running: starts it, and restarts it if it exits or crashes.
The Windows Startup shortcut points here. Crash traces (faulthandler) go to agent/agent.log."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
LOG = Path(__file__).with_name("agent.log")

quick_exits = 0
while True:
    started = time.time()
    with open(LOG, "a", encoding="utf-8") as log:
        code = subprocess.call([str(PYTHONW), "-X", "faulthandler", "-m", "agent"], cwd=ROOT,
                               stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        log.write(f"\n[supervisor] agent exited with code {code}; restarting\n")
    # Back off if it keeps dying right away (e.g. port 8765 already taken by another copy).
    quick_exits = quick_exits + 1 if time.time() - started < 30 else 0
    time.sleep(min(300, 3 * 2 ** min(quick_exits, 7)))
