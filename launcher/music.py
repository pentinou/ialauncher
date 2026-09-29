"""Musique : ACE-Step 1.5 (MIT), chansons avec paroles en 50+ langues.

ACE-Step est un programme Python (PyTorch). Le launcher reste sans dépendance : il
clone le dépôt dans apps/, laisse `uv` créer SON environnement virtuel (Python, torch
CUDA…), lance le serveur REST `acestep-api` et lui soumet les demandes
(/release_task puis /query_result). Les poids se téléchargent au premier lancement."""
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import paths, download, engine
from .services import MUSIC, claim_gpu

REPO = "https://github.com/ace-step/ACE-Step-1.5"

# Modèles de diffusion (DiT) : « turbo » = distillé en 8 étapes, « sft » = qualité, 50 étapes
MODELS = [
    {"id": "acestep-v15-turbo", "name": "Turbo 2B", "steps": 8, "cfg": 7.0, "vram": 5, "download": 0,
     "blurb": "Rapide et léger (≈ 5 Go de VRAM) : quelques secondes par chanson."},
    {"id": "acestep-v15-xl-turbo", "name": "XL Turbo 4B", "steps": 8, "cfg": 7.0, "vram": 12, "download": 20.0,
     "blurb": "Deux fois plus gros : meilleure qualité audio, toujours en 8 étapes."},
    {"id": "acestep-v15-xl-sft", "name": "XL SFT 4B", "steps": 50, "cfg": 7.0, "vram": 12, "download": 20.0,
     "blurb": "Non distillé : le plus fidèle au prompt, réglable (CFG), mais ~6× plus lent."},
]
# Le « LM 5 Hz » planifie la chanson (structure, métadonnées) avant la diffusion
LMS = [
    {"id": "", "name": "aucun", "blurb": "Diffusion seule : plus rapide, moins structuré."},
    {"id": "acestep-5Hz-lm-0.6B", "name": "0,6B", "blurb": "Petit planificateur."},
    {"id": "acestep-5Hz-lm-1.7B", "name": "1,7B", "blurb": "Le défaut conseillé pour 16-24 Go."},
    {"id": "acestep-5Hz-lm-4B", "name": "4B", "blurb": "Le meilleur, pour 24 Go et plus."},
]


def app_dir():
    return paths.apps_dir() / "ACE-Step-1.5"


def uv_bin():
    for c in (shutil.which("uv"), Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv"):
        if c and Path(c).exists():
            return str(c)
    return None


def status():
    d = app_dir()
    ckpt = d / "checkpoints"
    have = sorted(p.name for p in ckpt.iterdir() if p.is_dir()) if ckpt.is_dir() else []
    return {"installed": (d / ".venv").is_dir(), "dir": str(d), "uv": uv_bin(), "downloaded": have,
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "models": MODELS, "lms": LMS, "service": MUSIC.status()}


def install(job):
    uv = uv_bin()
    if not uv:
        job.set(0.02, "installation de uv (gestionnaire Python d'Astral)…")
        if sys.platform == "win32":
            engine._stream(["powershell", "-ExecutionPolicy", "ByPass", "-c",
                            "irm https://astral.sh/uv/install.ps1 | iex"], dict(os.environ), job, 0.02, 0.05)
        else:
            script = paths.cache_dir() / "uv-install.sh"
            script.write_text(download.fetch_text("https://astral.sh/uv/install.sh"))
            engine._stream(["sh", str(script)], dict(os.environ), job, 0.02, 0.05)
        uv = uv_bin()
        if not uv:
            raise RuntimeError("uv n'a pas pu être installé (voir le journal)")
    d = app_dir()
    git = shutil.which("git")
    if (d / ".git").is_dir() and git:
        job.set(0.05, "mise à jour du code d'ACE-Step…")
        engine._stream([git, "-C", str(d), "pull", "--ff-only"], dict(os.environ), job, 0.05, 0.08)
    elif not d.exists():
        job.set(0.05, "téléchargement du code d'ACE-Step…")
        if git:
            engine._stream([git, "clone", "--depth", "1", REPO, str(d)], dict(os.environ), job, 0.05, 0.08)
        else:
            z = paths.cache_dir() / "ace-step.zip"
            download.download(f"{REPO}/archive/refs/heads/main.zip", str(z), job, None, "code")
            engine._extract(str(z), paths.apps_dir())
            (paths.apps_dir() / "ACE-Step-1.5-main").rename(d)
            z.unlink(missing_ok=True)
    if job.cancel_requested:
        return None
    # uv installe Python 3.12, PyTorch CUDA et le reste dans ACE-Step-1.5/.venv (plusieurs Go)
    job.set(0.1, "installation des dépendances Python (PyTorch…) — plusieurs Go, patience")
    _stream_count(job, [uv, "sync"], d, 0.1, 0.99)
    job.set(1.0, "ACE-Step installé — les poids se téléchargeront au premier lancement")
    return {"dir": str(d)}


def _stream_count(job, cmd, cwd, p0, p1):
    """uv n'affiche pas de pourcentage : on avance au fil des paquets installés."""
    job.logline("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env=dict(os.environ))
    n = 0
    for line in proc.stdout:
        job.logline(line)
        if line.strip().startswith(("+", "Downloaded", "Prepared", "Installed")):
            n += 1
            job.set(min(p1, p0 + (p1 - p0) * n / 250), line.strip()[:90])
        if job.cancel_requested:
            proc.terminate()
            break
    if proc.wait() not in (0, None) and not job.cancel_requested:
        raise RuntimeError(f"échec de « {' '.join(cmd)} » (voir le journal)")


# ---------------------------------------------------------------- poids

# Dépôt principal : VAE, encodeur de texte, DiT turbo et LM 1,7B ; les autres à part.
# (même table que acestep/model_downloader.py)
MAIN_REPO = "ACE-Step/Ace-Step1.5"
MAIN_PARTS = ("acestep-v15-turbo", "vae", "Qwen3-Embedding-0.6B", "acestep-5Hz-lm-1.7B")


def ensure_weights(job, model, lm):
    """Télécharge les poids manquants avec le téléchargeur du launcher (reprenable) plutôt
    que de laisser ACE-Step le faire au démarrage : son téléchargement huggingface_hub
    s'est figé en cours de route lors des essais, sans rien signaler."""
    ckpt = app_dir() / "checkpoints"
    todo = []
    for repo, sub in [(MAIN_REPO, "")] + [(f"ACE-Step/{n}", n) for n in (model, lm) if n and n not in MAIN_PARTS]:
        try:
            listing = download.fetch_json(f"https://huggingface.co/api/models/{repo}/tree/main?recursive=1")
        except Exception as e:  # hors ligne : on fait confiance à ce qui est déjà sur le disque
            if all((ckpt / n).is_dir() for n in (*MAIN_PARTS[1:3], model, lm) if n):
                job.logline(f"Hugging Face injoignable ({e}) : poids déjà présents utilisés tels quels")
                continue
            raise RuntimeError(f"impossible de lister les poids de {repo} : {e}") from None
        for f in listing:
            if f["type"] != "file" or f["path"] == ".gitattributes":
                continue
            dest = ckpt / sub / f["path"]
            if not (dest.exists() and dest.stat().st_size == f["size"]):
                todo.append((repo, f["path"], dest, f["size"]))
    total = sum(t[3] for t in todo)
    done = 0
    for i, (repo, path, dest, size) in enumerate(todo, 1):
        dest.parent.mkdir(parents=True, exist_ok=True)
        sub = _SubJob(job, done / total if total else 0, size / total if total else 1)
        download.download(f"https://huggingface.co/{repo}/resolve/main/{path}", str(dest), sub, size,
                          f"poids ACE-Step ({total / 1e9:.1f} Go), fichier {i}/{len(todo)} · {Path(path).name} —")
        if job.cancel_requested:
            raise RuntimeError("annulé")
        done += size


class _SubJob:
    """Vue d'une tâche limitée à une tranche de sa progression (un fichier parmi d'autres)."""
    def __init__(self, job, start, span):
        self.job, self.start, self.span = job, start, span

    @property
    def cancel_requested(self):
        return self.job.cancel_requested

    def set(self, progress=None, detail=None):
        self.job.set(None if progress is None else self.start + self.span * progress, detail)

    def logline(self, s):
        self.job.logline(s)


# ---------------------------------------------------------------- serveur

def ensure_server(model, lm, job):
    if not (app_dir() / ".venv").is_dir():
        raise RuntimeError("ACE-Step n'est pas installé")
    key = json.dumps([model, lm])
    if MUSIC.alive() and MUSIC.key == key and MUSIC.state in ("starting", "ready"):
        MUSIC.wait_ready(job, timeout=3600)
        return
    ensure_weights(job, model, lm)
    stopped = claim_gpu("ace-step")
    if stopped:
        job.logline("arrêté pour libérer la carte graphique : " + ", ".join(stopped))
    from .server import pick_port
    port = pick_port(8001)
    env = dict(os.environ, ACESTEP_API_HOST="127.0.0.1", ACESTEP_API_PORT=str(port), ACESTEP_CONFIG_PATH=model,
               ACESTEP_INIT_LLM="true" if lm else "false", PYTHONUNBUFFERED="1")
    if lm:
        env["ACESTEP_LM_MODEL_PATH"] = lm
    job.set(0.01, "démarrage d'ACE-Step…")
    MUSIC.start([uv_bin(), "run", "acestep-api"], env, str(app_dir()), f"http://127.0.0.1:{port}", "/health", key,
                {"model": model, "lm": lm})
    MUSIC.wait_ready(job, timeout=3600)


def _call(path, body=None):
    url = MUSIC.base_url + path
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ACE-Step : {e.read().decode('utf-8', 'replace')[:400]}") from None
    if d.get("error"):
        raise RuntimeError(f"ACE-Step : {d['error']}")
    return d.get("data")


def generate(job, req):
    """req : {model, lm, prompt, lyrics, language, duration, bpm, key, time_signature, steps,
    cfg, seed, batch, format (flac par défaut : mp3 exige ffmpeg)}"""
    t0 = time.time()
    ensure_server(req["model"], req.get("lm", ""), job)
    seed = int(req.get("seed", -1))
    if seed < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    body = {"prompt": req.get("prompt", ""), "lyrics": req.get("lyrics", ""), "model": req["model"],
            "vocal_language": req.get("language") or "en", "thinking": bool(req.get("lm")),
            "inference_steps": int(req.get("steps", 8)), "guidance_scale": float(req.get("cfg", 7.0)),
            "use_random_seed": False, "seed": seed, "batch_size": int(req.get("batch", 1)),
            "audio_format": req.get("format") or "flac"}
    if body["audio_format"] in ("mp3", "opus", "aac") and not shutil.which("ffmpeg"):
        # ACE-Step encode ces formats avec ffmpeg ; sans lui, il génère puis n'écrit aucun fichier
        job.logline("ffmpeg absent : export en FLAC au lieu de " + body["audio_format"])
        body["audio_format"] = "flac"
    if req.get("duration"):
        body["audio_duration"] = float(req["duration"])
    if req.get("bpm"):
        body["bpm"] = int(req["bpm"])
    if req.get("key"):
        body["key_scale"] = req["key"]
    if req.get("time_signature"):
        body["time_signature"] = str(req["time_signature"])
    MUSIC.step = None
    tid = _call("/release_task", body)["task_id"]
    t_gen = time.time()
    while True:
        time.sleep(1)
        if job.cancel_requested:
            return None      # l'API n'a pas d'annulation : la tâche se termine dans le vide
        if not MUSIC.alive():
            raise RuntimeError(MUSIC.error or "ACE-Step s'est arrêté pendant la génération")
        r = (_call("/query_result", {"task_id_list": [tid]}) or [{}])[0]
        if r.get("status") == 1:
            break
        if r.get("status") == 2:
            raise RuntimeError("échec de la génération : " + str(r.get("result") or "")[:300])
        try:
            cur = (json.loads(r.get("result") or "[]") or [{}])[0]
        except (ValueError, TypeError):
            cur = {}
        stage = {"queued": "en file (chargement / téléchargement du modèle)"}.get(cur.get("stage"), cur.get("stage") or "calcul")
        job.set(cur.get("progress") or None, f"{stage} · {int(time.time() - t_gen)} s")
    items = json.loads(r["result"]) if isinstance(r.get("result"), str) else r.get("result") or []
    from .sdcpp import _save
    meta = dict(req, seed=seed, kind="music", seconds=0)
    files = []
    for i, it in enumerate(items):
        if not it.get("file"):
            raise RuntimeError("ACE-Step a généré la musique mais n'a pas pu écrire le fichier (voir son journal)")
        with urllib.request.urlopen(MUSIC.base_url + it["file"], timeout=120) as f:
            data = f.read()
        ext = Path(urllib.parse.parse_qs(urllib.parse.urlparse(it["file"]).query).get("path", ["x.mp3"])[0]).suffix.lstrip(".") or "mp3"
        m = dict(meta, seconds=round(time.time() - t0, 1), gen_seconds=round(time.time() - t_gen, 1),
                 metas=it.get("metas"), seed=_seed_of(it, seed, i))
        files.append(_save("music", data, ext, m))
    job.set(1.0, f"terminé en {round(time.time() - t0, 1)} s")
    return {"files": files, "seed": seed}


def _seed_of(item, seed, i):
    try:
        return int(str(item.get("seed_value", "")).split(",")[i])
    except (ValueError, IndexError):
        return seed
