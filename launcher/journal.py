"""Journaux : celui du launcher (launcher.log : actions demandées et par qui, refus, erreurs,
tâches de fond) et ceux des serveurs (sd-server, llama-server, ace-step), dans
/var/log/ialauncher (roulement par logrotate) ou <home>/logs (taille plafonnée ici).
Lisibles en direct dans l'onglet Journaux."""
import logging
import os
import re
import threading
import time
from logging.handlers import RotatingFileHandler

from . import paths

_log = None
_lock = threading.Lock()


def log(msg):
    global _log
    with _lock:
        if _log is None:
            _log = logging.getLogger("ialauncher")
            _log.setLevel(logging.INFO)
            _log.propagate = False
            p = paths.logs_dir() / "launcher.log"
            h = (logging.FileHandler(p, encoding="utf-8") if paths.logs_rotated()   # logrotate : copytruncate
                 else RotatingFileHandler(p, maxBytes=2_000_000, backupCount=2, encoding="utf-8"))
            h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
            _log.addHandler(h)
    _log.info(msg)


def open_service_log(name):
    """Journal d'un serveur, en binaire : chaque lancement s'ajoute à la suite, après une ligne
    de séparation. Hors /var/log, il passe en .prev.log au-delà de 5 Mo."""
    p = paths.logs_dir() / f"{name}.log"
    if not paths.logs_rotated() and p.exists() and p.stat().st_size > 5_000_000:
        os.replace(p, p.with_suffix(".prev.log"))
    f = open(p, "ab", buffering=0)
    f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} — lancement\n".encode())
    return f


def files():
    """Les journaux présents, du plus récent au plus ancien."""
    d = paths.logs_dir()
    out = [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime}
           for p in d.iterdir() if p.is_file() and (p.name.endswith(".log") or re.search(r"\.log\.\d+$", p.name))]
    return {"dir": str(d), "rotated": paths.logs_rotated(),
            "files": sorted(out, key=lambda f: f["mtime"], reverse=True)}


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def tail(name, lines=500):
    """Dernières lignes d'un journal ; une barre de progression réécrite par « \\r » ne garde
    que son dernier état."""
    if name not in {f["name"] for f in files()["files"]}:
        raise ValueError("journal inconnu")
    p = paths.logs_dir() / name
    size = p.stat().st_size
    with open(p, "rb") as f:
        f.seek(max(0, size - 400_000))
        text = f.read().decode("utf-8", "replace")
    out = [ANSI.sub("", ln.rstrip("\r").split("\r")[-1]) for ln in text.split("\n")]
    if size > 400_000:
        out = out[1:]        # première ligne coupée
    return {"name": name, "size": size, "lines": out[-lines:]}
