"""Tâches de fond (téléchargement, compilation…) suivies par l'interface.
Chaque tâche a un id, un état, une progression 0-1 et les dernières lignes de log."""
import threading
import time
import traceback
import uuid

_jobs = {}
_lock = threading.Lock()


class Job:
    def __init__(self, kind, label):
        self.id = uuid.uuid4().hex[:8]
        self.kind = kind
        self.label = label
        self.state = "running"      # running | done | error | cancelled
        self.progress = 0.0
        self.detail = ""
        self.result = None
        self.error = ""
        self.log = []
        self.started = time.time()
        self.cancel_requested = False

    def logline(self, s):
        self.log.append(s.rstrip())
        del self.log[:-400]

    def set(self, progress=None, detail=None):
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if detail is not None:
            self.detail = detail

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state,
                "progress": self.progress, "detail": self.detail, "error": self.error,
                "result": self.result, "log": self.log[-60:], "elapsed": time.time() - self.started}


def start(kind, label, fn):
    """Lance fn(job) dans un thread. fn renvoie le résultat ou lève une exception."""
    job = Job(kind, label)
    with _lock:
        _jobs[job.id] = job

    def run():
        try:
            job.result = fn(job)
            job.state = "cancelled" if job.cancel_requested else "done"
            job.progress = 1.0
        except Exception as e:  # noqa: BLE001 — on veut tout remonter à l'UI
            job.state = "error"
            job.error = str(e) or e.__class__.__name__
            job.logline(traceback.format_exc())
    threading.Thread(target=run, daemon=True).start()
    return job


def get(job_id):
    return _jobs.get(job_id)


def running(kind=None):
    return [j for j in _jobs.values() if j.state == "running" and (kind is None or j.kind == kind)]


def all_jobs():
    return sorted(_jobs.values(), key=lambda j: j.started, reverse=True)
