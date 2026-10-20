"""Background jobs for long tasks (downloads, transcription). In-memory; lost on restart."""
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

_pool = ThreadPoolExecutor(max_workers=2)
_jobs = {}
MAX_KEPT = 50


class Job(dict):
    def progress(self, text):
        self["progress"] = text


def submit(kind, fn, *args, **kwargs):
    """fn(job, *args) runs in a worker thread; its return value becomes job['result']."""
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, status="running", progress="queued",
              result=None, error=None, created=time.time())
    _jobs[job["id"]] = job
    for old in sorted(_jobs.values(), key=lambda j: j["created"])[:-MAX_KEPT]:
        _jobs.pop(old["id"], None)

    def run():
        try:
            job["result"] = fn(job, *args, **kwargs)
            job["status"] = "done"
        except Exception as e:
            traceback.print_exc()
            job["status"], job["error"] = "error", str(e)[:1000]
        job["progress"] = ""

    _pool.submit(run)
    return job


def get(job_id):
    return _jobs.get(job_id)


def recent():
    return sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)
