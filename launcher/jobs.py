"""Tâches de fond (téléchargement, compilation…) suivies par l'interface.
Chaque tâche a un id, un état, une progression 0-1 et les dernières lignes de log."""
import itertools
import threading
import time
import traceback
import uuid

from . import journal

_jobs = {}
_lock = threading.Lock()
_seq = itertools.count()


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
        self.queue = None           # file d'attente partagée (ex. « gen » : une génération à la fois)
        self.seq = next(_seq)

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


def start(kind, label, fn, queue=None, who=""):
    """Lance fn(job) dans un thread. fn renvoie le résultat ou lève une exception.
    Avec `queue`, les tâches de la même file passent l'une après l'autre, dans l'ordre
    des demandes (plusieurs navigateurs peuvent demander une génération en même temps)."""
    job = Job(kind, label)
    job.queue = queue
    with _lock:
        _jobs[job.id] = job
    journal.log(f"tâche {job.id} demandée{' par ' + who if who else ''} : {label}")

    def run():
        try:
            _wait_turn(job)
            t0 = time.time()
            job.result = fn(job)
            job.state = "cancelled" if job.cancel_requested else "done"
            job.progress = 1.0
            journal.log(f"tâche {job.id} {'annulée' if job.cancel_requested else 'terminée'} en {time.time() - t0:.0f} s")
        except Exception as e:  # noqa: BLE001 — on veut tout remonter à l'UI
            job.state = "cancelled" if job.cancel_requested else "error"
            job.error = str(e) or e.__class__.__name__
            job.logline(traceback.format_exc())
            journal.log(f"tâche {job.id} {'annulée' if job.cancel_requested else 'en erreur : ' + job.error}")
    threading.Thread(target=run, daemon=True).start()
    return job


def ahead(job):
    """Tâches de la même file demandées avant celle-ci et pas encore finies."""
    return [j for j in list(_jobs.values())
            if job.queue and j.queue == job.queue and j.state == "running" and j.seq < job.seq]


def _wait_turn(job):
    n0 = None
    while True:
        n = len(ahead(job))
        if not n:
            break
        if job.cancel_requested:
            raise RuntimeError("annulé avant son tour")
        if n != n0:
            job.set(detail=f"en attente : {n} tâche{'s' if n > 1 else ''} avant celle-ci")
            if n0 is None:
                journal.log(f"tâche {job.id} en attente derrière {n} autre(s)")
            n0 = n
        time.sleep(0.5)
    if n0 is not None:
        job.started = time.time()
        job.set(detail="à votre tour…")


def get(job_id):
    return _jobs.get(job_id)


def running(kind=None):
    return [j for j in _jobs.values() if j.state == "running" and (kind is None or j.kind == kind)]


def all_jobs():
    return sorted(_jobs.values(), key=lambda j: j.started, reverse=True)
