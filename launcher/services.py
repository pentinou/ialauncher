"""Serveurs de génération lancés en arrière-plan (sd-server pour l'image et la vidéo,
ACE-Step pour la musique) : processus, journal, attente de disponibilité, arrêt.

La carte graphique ne peut porter qu'un gros modèle à la fois : avant de démarrer un
service, claim_gpu() arrête les autres (llama-server compris)."""
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request

from . import paths

# Barres de progression : sd.cpp écrit « |====>   | 3/8 - 1.20s/it » (ou « MB/s » pendant
# le chargement), tqdm (ACE-Step) « 38%|███▊      | 3/8 [00:01<00:02, … »
RE_STEP = re.compile(r"\|\s*(\d+)/(\d+)\s*(?:-\s*([\d.]+)\s*(s/it|it/s|[MG]B/s)|\[)")


class Service:
    def __init__(self, name, marker):
        self.name = name
        self.marker = marker        # motif de la ligne de commande (pour reconnaître un orphelin)
        self.proc = None
        self.state = "stopped"      # stopped | starting | ready | error
        self.error = ""
        self.key = None             # configuration lancée (pour savoir s'il faut relancer)
        self.info = {}              # ce que l'appelant veut afficher (modèle…)
        self.base_url = ""
        self.cmd = ""
        self.log = []
        self.step = None            # (étape, total, vitesse) de la dernière barre de progression
        self.started = 0
        self.lock = threading.Lock()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, cmd, env, cwd, base_url, health_path, key, info=None):
        with self.lock:
            self._stop_locked()
            self.key, self.info, self.base_url = key, info or {}, base_url
            self.cmd = " ".join(cmd)
            self.log = ["$ " + self.cmd]
            self.state, self.error, self.step = "starting", "", None
            self.started = time.time()
            kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {"start_new_session": True}
            logf = open(paths.logs_dir() / f"{self.name}.log", "wb", buffering=0)
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         env=env, cwd=cwd, **kw)
            try:
                self._pidfile().write_text(str(self.proc.pid))
            except OSError:
                pass
            threading.Thread(target=self._pump, args=(self.proc, logf), daemon=True).start()
            threading.Thread(target=self._watch, args=(self.proc, base_url + health_path), daemon=True).start()

    def _pump(self, proc, logf):
        """Lit la sortie octet par octet : les barres de progression se réécrivent avec
        « \\r » sans jamais finir la ligne."""
        buf = b""
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1)
            if not chunk:
                break
            logf.write(chunk)
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for p in parts:
                self._line(p.decode("utf-8", "replace"))
            if buf:
                self._progress(buf.decode("utf-8", "replace"))
        logf.close()
        code = proc.wait()
        if self.proc is proc and self.state != "stopped":
            self.state = "error" if code not in (0, None, -15) else "stopped"
            if self.state == "error":
                self.error = self._guess_error()

    def _line(self, line):
        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).rstrip()
        if not line.strip():
            return
        if self._progress(line):
            # une barre remplace la précédente dans le journal au lieu de s'empiler
            if self.log and RE_STEP.search(self.log[-1]):
                self.log[-1] = line
                return
        self.log.append(line)
        del self.log[:-400]

    def _progress(self, text):
        m = None
        for m in RE_STEP.finditer(text):
            pass
        if not m:
            return False
        speed = f"{m.group(3)} {m.group(4)}" if m.group(3) else ""
        self.step = (int(m.group(1)), int(m.group(2)), speed)
        return True

    def _guess_error(self):
        for line in reversed(self.log):
            low = line.lower()
            if any(k in low for k in ("error", "failed", "out of memory", "cannot", "traceback")):
                return line[-300:]
        return f"{self.name} s'est arrêté (voir le journal)"

    def _watch(self, proc, url):
        while proc.poll() is None and self.proc is proc:
            try:
                with urllib.request.urlopen(url, timeout=3) as r:
                    if r.status == 200:
                        self.state = "ready"
                        return
            except Exception:
                pass
            time.sleep(1)

    def wait_ready(self, job=None, timeout=1800):
        """Attend que le service réponde ; lève une erreur s'il meurt en route."""
        t0 = time.time()
        while self.state == "starting":
            if not self.alive():
                self.state = "error"
                self.error = self.error or self._guess_error()
            if job:
                if job.cancel_requested:
                    self.stop()
                    raise RuntimeError("annulé")
                job.set(detail=f"chargement du modèle… {int(time.time() - t0)} s" + self._step_text())
            if time.time() - t0 > timeout:
                raise RuntimeError(f"{self.name} ne répond pas après {timeout} s")
            time.sleep(0.5)
        if self.state != "ready":
            raise RuntimeError(self.error or f"{self.name} ne démarre pas (voir le journal)")

    def _step_text(self):
        return f" · {self.step[0]}/{self.step[1]}" if self.step else ""

    def stop(self):
        with self.lock:
            self._stop_locked()

    def _stop_locked(self):
        proc, self.proc = self.proc, None
        self.state, self.key = "stopped", None
        if proc and proc.poll() is None:
            _terminate(proc.pid)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                _terminate(proc.pid, hard=True)
        self._pidfile().unlink(missing_ok=True)

    def _pidfile(self):
        return paths.home() / f"{self.name}.pid"

    def kill_orphan(self):
        """Au démarrage du launcher : arrête un service laissé par une session tuée."""
        pf = self._pidfile()
        try:
            pid = int(pf.read_text().strip())
        except (OSError, ValueError):
            return None
        pf.unlink(missing_ok=True)
        try:
            if sys.platform != "win32" and self.marker not in open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace"):
                return None     # PID réutilisé par un autre programme
        except OSError:
            return None
        _terminate(pid, hard=True)
        return pid

    def status(self):
        alive = self.alive()
        if not alive and self.state in ("starting", "ready"):
            self.state = "error"
            self.error = self.error or self._guess_error()
        return {"state": self.state, "error": self.error, "info": self.info, "cmd": self.cmd,
                "uptime": time.time() - self.started if alive else 0, "log": self.log[-80:],
                "step": self.step}


def _terminate(pid, hard=False):
    """Arrête le processus ET ses enfants (ACE-Step lance des sous-processus Python)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.killpg(pid, signal.SIGKILL if hard else signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL if hard else signal.SIGTERM)
        except OSError:
            pass


SD = Service("sd-server", "sd-server")
MUSIC = Service("ace-step", "acestep")


def claim_gpu(owner):
    """Libère la carte graphique pour `owner` : arrête llama-server et l'autre service
    de génération (owner inconnu, ex. "aucun" : arrête tout). Renvoie ce qui a été arrêté."""
    from .server import SERVER
    stopped = []
    if owner != "llama" and SERVER.proc is not None and SERVER.proc.poll() is None:
        SERVER.stop()
        stopped.append("llama-server")
    for s in (SD, MUSIC):
        if s.name != owner and s.alive():
            s.stop()
            stopped.append(s.name)
    return stopped
