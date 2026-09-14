"""Pilotage du processus llama-server : construction de la ligne de commande à
partir de la configuration, lancement, journal, santé, arrêt. Le journal détaillé
(-lv 4) est analysé pour retrouver les tailles RÉELLES des tampons et les comparer
à l'estimation."""
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
import json
import socket
from pathlib import Path

from . import engine, paths, hardware

MiB = 1024 ** 2


def port_free(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(preferred, host="127.0.0.1"):
    """Le port demandé s'il est libre, sinon le premier libre qui suit (8080 est
    souvent déjà pris par un autre service)."""
    for p in range(int(preferred), int(preferred) + 20):
        if port_free(p, host):
            return p
    raise RuntimeError(f"aucun port libre entre {preferred} et {int(preferred) + 19}")


def build_args(cfg, model_path, mmproj=None, alias=None):
    """Traduit la configuration en arguments llama-server. Seuls les réglages qui
    s'écartent du défaut sont écrits, pour une commande lisible."""
    a = ["-m", model_path, "--host", cfg.get("host", "127.0.0.1"), "--port", str(cfg.get("port", 8080))]
    if alias:
        a += ["--alias", alias]
    a += ["-c", str(cfg.get("ctx", 8192))]
    ngl = cfg.get("ngl", "all")
    a += ["-ngl", "all" if ngl in ("all", -1, "-1", "", None) else str(ngl)]
    fa = cfg.get("flash_attn", "auto")
    if fa in ("on", "off"):
        a += ["-fa", fa]
    if cfg.get("type_k", "f16") != "f16":
        a += ["-ctk", cfg["type_k"]]
    if cfg.get("type_v", "f16") != "f16":
        a += ["-ctv", cfg["type_v"]]
    if cfg.get("cpu_moe_all"):
        a += ["--cpu-moe"]
    elif int(cfg.get("n_cpu_moe") or 0) > 0:
        a += ["--n-cpu-moe", str(int(cfg["n_cpu_moe"]))]
    if int(cfg.get("n_cpu_ffn") or 0) > 0:
        a += ["--n-cpu-ffn", str(int(cfg["n_cpu_ffn"]))]
    if int(cfg.get("threads") or 0) > 0:
        a += ["-t", str(int(cfg["threads"]))]
    if int(cfg.get("threads_batch") or 0) > 0:
        a += ["-tb", str(int(cfg["threads_batch"]))]
    if int(cfg.get("batch") or 2048) != 2048:
        a += ["-b", str(int(cfg["batch"]))]
    if int(cfg.get("ubatch") or 512) != 512:
        a += ["-ub", str(int(cfg["ubatch"]))]
    a += ["-np", str(int(cfg.get("parallel") or 1))]
    if not cfg.get("kv_offload", True):
        a += ["--no-kv-offload"]
    if cfg.get("load_mode", "auto") != "auto":
        a += ["--load-mode", cfg["load_mode"]]
    if cfg.get("swa_full"):
        a += ["--swa-full"]
    if "cache_ram" in cfg and int(cfg["cache_ram"]) != 8192:
        a += ["--cache-ram", str(int(cfg["cache_ram"]))]
    if int(cfg.get("cache_reuse") or 0) > 0:
        a += ["--cache-reuse", str(int(cfg["cache_reuse"]))]
    spec = cfg.get("spec_type", "none")
    if spec and spec != "none":
        a += ["--spec-type", spec]
        if spec.startswith("draft"):
            if cfg.get("draft_model"):
                a += ["--model-draft", cfg["draft_model"]]
            if cfg.get("draft_ngl", "all") not in ("all", "", None):
                a += ["-ngld", str(cfg["draft_ngl"])]
            if int(cfg.get("spec_n_max") or 0) > 0:
                a += ["--spec-draft-n-max", str(int(cfg["spec_n_max"]))]
    if cfg.get("reasoning", "auto") in ("on", "off"):
        a += ["--reasoning", cfg["reasoning"]]
    if int(cfg.get("reasoning_budget", -1)) >= 0:
        a += ["--reasoning-budget", str(int(cfg["reasoning_budget"]))]
    if mmproj:
        a += ["--mmproj", mmproj]
        if not cfg.get("mmproj_offload", True):
            a += ["--no-mmproj-offload"]
    if cfg.get("devices"):
        a += ["--device", ",".join(cfg["devices"])]
    if cfg.get("tensor_split"):
        a += ["-ts", str(cfg["tensor_split"])]
    if cfg.get("split_mode"):
        a += ["-sm", cfg["split_mode"]]
    if cfg.get("fit", "on") == "off":
        a += ["--fit", "off"]
    if cfg.get("extra_args"):
        a += shlex.split(cfg["extra_args"])
    return a


def model_alias(model):
    """Nom sous lequel le serveur annonce le modèle (/v1/models, en-têtes) : un slug
    sans « / » ni « : », que Claude Code, Codex et OpenCode acceptent tel quel."""
    base = Path(model.get("path") or "").stem
    if not base or base.startswith("sha256-"):   # blob Ollama : le nom lisible vaut mieux que l'empreinte
        base = model.get("name") or "modele"
    base = re.sub(r"-\d{5}-of-\d{5}$", "", base)
    slug = re.sub(r"[^a-z0-9._-]+", "-", base.lower()).strip("-.")
    return slug[:80] or "modele"


def command_line(bin_path, args):
    return " ".join(shlex.quote(x) for x in [bin_path, *args])


# Lignes du journal qui donnent les tailles réelles
RE_MEM = [
    ("weights", re.compile(r"load_tensors:\s+(\S+) model buffer size =\s+([\d.]+) MiB")),
    ("kv", re.compile(r"llama_kv_cache(?:_\w+)?:\s+(\S+) KV buffer size =\s+([\d.]+) MiB")),
    ("rs", re.compile(r"llama_memory_recurrent:\s+(\S+) RS buffer size =\s+([\d.]+) MiB")),
    ("compute", re.compile(r"sched_reserve:\s+(\S+) compute buffer size =\s+([\d.]+) MiB")),
    ("output", re.compile(r"llama_context:\s+(\S+)\s+output buffer size =\s+([\d.]+) MiB")),
]
RE_OFFLOAD = re.compile(r"load_tensors: offloaded (\d+)/(\d+) layers to GPU")
RE_PROJ = re.compile(r"projected to use (\d+) MiB of device memory vs\. (\d+) MiB of free")


class Server:
    def __init__(self):
        self.proc = None
        self.log = []
        self.state = "stopped"     # stopped | starting | ready | error
        self.error = ""
        self.started = 0
        self.model = None
        self.cfg = None
        self.cmd = ""
        self.mem = {}              # tailles réelles relevées dans le journal
        self.offloaded = None
        self.projected = None
        self.port_note = ""
        self.alias = ""
        self.baseline = None       # VRAM/RAM libres juste avant le lancement (pour estimer sans se compter soi-même)
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- cycle de vie
    def start(self, cfg, model, mmproj=None):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                raise RuntimeError("un serveur tourne déjà — arrêtez-le d'abord")
            bin_path = engine.current_bin()
            if not bin_path:
                raise RuntimeError("aucun moteur llama-server installé")
            self.baseline = hardware.live()
            cfg = dict(cfg)
            wanted = int(cfg.get("port", 8080))
            cfg["port"] = pick_port(wanted, cfg.get("host", "127.0.0.1"))
            self.port_note = f"port {wanted} occupé → {cfg['port']}" if cfg["port"] != wanted else ""
            self.alias = model_alias(model)
            args = build_args(cfg, model["path"], mmproj or model.get("mmproj") or None, alias=self.alias)
            args += ["-lv", "4", "--log-timestamps"]
            self.cmd = command_line(bin_path, args)
            self.log = ["$ " + self.cmd]
            self.mem, self.offloaded, self.projected = {}, None, None
            self.state, self.error = "starting", ""
            self.started = time.time()
            self.model, self.cfg = model, cfg
            kw = {}
            if sys.platform == "win32":
                kw["creationflags"] = 0x08000000
            logf = open(paths.logs_dir() / "llama-server.log", "w", encoding="utf-8", errors="replace")
            self.proc = subprocess.Popen([bin_path, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace",
                                         env=engine.env_for(bin_path), cwd=str(paths.home()), **kw)
            try:
                _pidfile().write_text(str(self.proc.pid))
            except OSError:
                pass
            threading.Thread(target=self._pump, args=(self.proc, logf), daemon=True).start()
            threading.Thread(target=self._watch_health, args=(self.proc,), daemon=True).start()

    def _pump(self, proc, logf):
        for line in proc.stdout:
            line = line.rstrip("\n")
            logf.write(line + "\n")
            self.log.append(line)
            del self.log[:-600]
            self._parse(line)
        logf.close()
        code = proc.wait()
        if self.proc is proc:
            if self.state != "stopped":
                self.state = "error" if code not in (0, None, -15) else "stopped"
                if self.state == "error":
                    self.error = self._guess_error()

    def _parse(self, line):
        for key, rx in RE_MEM:
            m = rx.search(line)
            if m:
                dev, mib = m.group(1), float(m.group(2))
                self.mem.setdefault(key, {})[dev] = self.mem.get(key, {}).get(dev, 0) + mib
                return
        m = RE_OFFLOAD.search(line)
        if m:
            self.offloaded = (int(m.group(1)), int(m.group(2)))
            return
        m = RE_PROJ.search(line)
        if m:
            self.projected = (int(m.group(1)), int(m.group(2)))

    def _guess_error(self):
        for line in reversed(self.log):
            low = line.lower()
            if "error" in low or "failed" in low or "out of memory" in low or "cannot" in low:
                return line[-300:]
        return "le serveur s'est arrêté (voir le journal)"

    def _watch_health(self, proc):
        url = f"http://{self.cfg.get('host', '127.0.0.1')}:{self.cfg.get('port', 8080)}/health"
        while proc.poll() is None and self.proc is proc:
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if json.loads(r.read().decode()).get("status") == "ok":
                        self.state = "ready"
                        return
            except Exception:
                pass
            time.sleep(1)

    def stop(self):
        with self.lock:
            proc, self.proc = self.proc, None
            self.state = "stopped"
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        _pidfile().unlink(missing_ok=True)

    # ---------------------------------------------------------------- état
    def status(self):
        alive = self.proc is not None and self.proc.poll() is None
        if not alive and self.state in ("starting", "ready"):
            self.state = "error" if self.proc is not None else "stopped"
            if self.state == "error" and not self.error:
                self.error = self._guess_error()
        rss = _rss(self.proc.pid) if alive else 0
        return {"state": self.state, "error": self.error, "pid": self.proc.pid if alive else None,
                "port": self.cfg.get("port", 8080) if self.cfg else None,
                "model": self.model, "cfg": self.cfg, "cmd": self.cmd,
                "uptime": time.time() - self.started if alive else 0,
                "mem": self.mem, "offloaded": self.offloaded, "projected": self.projected,
                "rss": rss, "log": self.log[-120:], "port_note": self.port_note, "alias": self.alias}

    def base_url(self):
        return f"http://{self.cfg.get('host', '127.0.0.1')}:{self.cfg.get('port', 8080)}" if self.cfg else ""


def _pidfile():
    return paths.home() / "llama-server.pid"


def kill_orphan():
    """Au démarrage : si un llama-server lancé par une instance précédente (tuée
    brutalement) tourne encore, on l'arrête — sinon il garde la VRAM."""
    pf = _pidfile()
    try:
        pid = int(pf.read_text().strip())
    except (OSError, ValueError):
        return None
    pf.unlink(missing_ok=True)
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            return pid
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace")
        if "llama-server" in cmd:
            import signal
            import os
            os.kill(pid, signal.SIGTERM)
            return pid
    except (OSError, ValueError):
        pass
    return None


def _rss(pid):
    """Mémoire résidente du processus (RAM réellement occupée), Linux seulement."""
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


SERVER = Server()
