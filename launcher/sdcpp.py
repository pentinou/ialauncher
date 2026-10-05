"""Image et vidéo : stable-diffusion.cpp (sd-server), le « llama.cpp de la diffusion ».

Même logique que pour le texte : un binaire compilé pour la carte (CUDA, ROCm,
Vulkan, Metal), des poids GGUF quantifiés, un serveur HTTP local. Le launcher
installe le moteur, télécharge les fichiers d'un modèle, lance sd-server avec les
bonnes options, puis lui soumet les générations par son API native asynchrone
(/sdcpp/v1/img_gen, /sdcpp/v1/vid_gen) et range les résultats dans outputs/."""
import base64
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import paths, download, engine, hardware, gen_catalog
from .services import SD, claim_gpu

REPO = "leejet/stable-diffusion.cpp"
API = f"https://api.github.com/repos/{REPO}/releases"
HF = "https://huggingface.co"
EXE = "sd-server.exe" if sys.platform == "win32" else "sd-server"


# ---------------------------------------------------------------- moteur

def current_bin():
    b = engine.load_config().get("sd_bin")
    if b and os.path.exists(b):
        return b
    found = sorted(str(p) for p in paths.engines_dir().glob(f"sd-*/**/{EXE}") if p.is_file())
    return found[-1] if found else shutil.which("sd-server")


def plan():
    """Meilleure façon d'obtenir sd-server sur cette machine (même raisonnement que llama.cpp)."""
    hw = hardware.inventory()
    vendors = {g["vendor"] for g in hw["gpus"]}
    tc = engine.toolchain()
    git = shutil.which("git")
    if sys.platform == "darwin":
        return {"method": "prebuilt", "variant": "Darwin-macOS", "label": "Metal (macOS)",
                "reason": "Sur Mac, le binaire officiel utilise Metal."}
    if sys.platform == "win32":
        if "nvidia" in vendors:
            return {"method": "prebuilt", "variant": "win-cuda12-x64", "label": "CUDA 12 (Windows)",
                    "reason": "GPU NVIDIA : binaire CUDA officiel."}
        if "amd" in vendors:
            return {"method": "prebuilt", "variant": "win-rocm", "label": "ROCm (Windows)",
                    "reason": "GPU AMD : binaire ROCm officiel."}
        return {"method": "prebuilt", "variant": "win-vulkan-x64", "label": "Vulkan (Windows)",
                "reason": "Vulkan fonctionne avec toutes les cartes récentes."}
    if "nvidia" in vendors and tc["nvcc"] and tc["cmake"] and tc["cc"] and git:
        return {"method": "build", "variant": "cuda-local", "label": "CUDA (compilé ici)",
                "reason": "GPU NVIDIA sous Linux : pas de binaire CUDA officiel, mais nvcc, cmake et git "
                          "sont présents, donc on compile (≈ 10-20 min, une seule fois)."}
    if "amd" in vendors:
        return {"method": "prebuilt", "variant": "x86_64-rocm", "label": "ROCm (Linux)",
                "reason": "GPU AMD : binaire ROCm officiel (le runtime ROCm doit être installé)."}
    reason = "Binaire Vulkan officiel."
    if "nvidia" in vendors:
        reason = ("GPU NVIDIA mais il manque nvcc, cmake ou git pour compiler en CUDA : binaire Vulkan. "
                  + ("ATTENTION : sous WSL2, Vulkan ne voit généralement pas le GPU." if hw["wsl"] else ""))
    return {"method": "prebuilt", "variant": "x86_64-vulkan", "label": "Vulkan (Linux)", "reason": reason}


def status():
    b = current_bin()
    return {"bin": b, "installed": bool(b), "version": engine._marker(b).get("tag", "") if b else "",
            "variant": engine.variant_of(b) if b else "", "plan": plan(), "service": SD.status()}


def install(job, method, variant, label):
    job.set(0.02, "recherche de la dernière version…")
    tag = download.fetch_json(f"{API}/latest")["tag_name"]
    target = paths.engines_dir() / f"sd-{tag}-{variant}"
    if method == "build":
        _build_cuda(job, tag, target)
    else:
        rel = download.fetch_json(f"{API}/tags/{tag}")
        assets = rel.get("assets", [])
        main = next((a for a in assets if a["name"].startswith("sd-") and variant in a["name"]), None)
        if not main:
            raise RuntimeError(f"aucun binaire « {variant} » dans la release {tag}")
        if target.exists():
            shutil.rmtree(target)
        todo = [main] + ([a for a in assets if a["name"].startswith("cudart-sd")] if "cuda" in variant else [])
        for a in todo:
            tmp = paths.cache_dir() / a["name"]
            download.download(a["browser_download_url"], str(tmp), job, a.get("size"), a["name"])
            if job.cancel_requested:
                return None
            engine._extract(str(tmp), target)
            tmp.unlink(missing_ok=True)
    exe = next((p for p in target.rglob(EXE) if p.is_file()), None)
    if not exe:
        raise RuntimeError("sd-server introuvable après installation")
    (target / "VERSION").write_text(json.dumps({"tag": tag, "variant": variant, "label": label}))
    cfg = engine.load_config()
    cfg["sd_bin"] = str(exe)
    engine.save_config(cfg)
    job.set(1.0, f"installé : {label} ({tag})")
    return {"bin": str(exe), "tag": tag}


def _build_cuda(job, tag, target):
    tc = engine.toolchain()
    src = paths.cache_dir() / f"sdcpp-{tag}"
    if src.exists():
        shutil.rmtree(src)
    # les sources de ggml sont un sous-module git : l'archive .tar.gz de GitHub ne les contient pas
    job.set(0.03, "téléchargement des sources (git)…")
    engine._stream([shutil.which("git"), "clone", "--depth", "1", "--branch", tag, "--recurse-submodules",
                    "--shallow-submodules", f"https://github.com/{REPO}", str(src)], dict(os.environ), job, 0.03, 0.08)
    if job.cancel_requested:
        return
    env = dict(os.environ)
    env["PATH"] = str(Path(tc["nvcc"]).parent) + os.pathsep + env.get("PATH", "")
    build = src / "build"
    job.set(0.08, "configuration cmake…")
    engine._stream([tc["cmake"], "-S", str(src), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release", "-DSD_CUDA=ON",
                    "-DCMAKE_CUDA_ARCHITECTURES=native", f"-DCMAKE_CUDA_COMPILER={tc['nvcc']}"], env, job, 0.08, 0.12)
    if job.cancel_requested:
        return
    n = max(1, (os.cpu_count() or 2) - 1)
    job.set(0.12, f"compilation (-j{n})… c'est long, patience")
    engine._stream([tc["cmake"], "--build", str(build), "--config", "Release", "-j", str(n), "--target", "sd-server"],
                   env, job, 0.12, 0.98)
    if job.cancel_requested:
        return
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for p in (build / "bin").iterdir():
        if p.is_file():
            shutil.copy2(p, target / p.name)
    shutil.rmtree(src, ignore_errors=True)


# ---------------------------------------------------------------- modèles

def _dest(repo, file):
    return paths.diffusion_dir() / repo.replace("/", "__") / Path(file).name


def _files_of(entry):
    """[(rôle, chemin local, présent, taille)] pour une entrée du catalogue."""
    out = []
    for role, (repo, file, size) in entry["files"].items():
        d = _dest(repo, file)
        out.append((role, str(d), d.exists(), int(size)))
    return out


def webui_installs():
    """Dossiers de Stable Diffusion WebUI / Forge sur cette machine (et sous Windows via WSL)."""
    bases = [Path.home()]
    if hardware.is_wsl():
        bases += [Path(f"/mnt/{d}") for d in "cdefgh"]
        users = Path("/mnt/c/Users")
        if users.is_dir():
            bases += [u for u in users.iterdir() if u.is_dir() and u.name not in ("Public", "Default", "All Users", "Default User")]
    elif sys.platform == "win32":
        bases += [Path(f"{d}:/") for d in "CDEFGH"]
    found = []
    for b in bases:
        try:
            for p in b.iterdir():
                low = p.name.lower()
                if ("webui" in low or "forge" in low) and (p / "models" / "Stable-diffusion").is_dir():
                    found.append(p)
        except OSError:
            continue
    return found


def local_checkpoints():
    """Checkpoints « tout-en-un » (SD 1.5 / SDXL / Pony / Illustrious) déjà présents."""
    dirs = [(w.name, w / "models" / "Stable-diffusion", w / "models" / "Lora") for w in webui_installs()]
    dirs += [(Path(d).name, Path(d), None) for d in engine.load_config().get("sd_dirs", [])]
    out = _civitai_checkpoints()
    for label, d, lora in dirs:
        try:
            files = sorted(p for p in d.rglob("*") if p.suffix.lower() in (".safetensors", ".ckpt", ".gguf"))
        except OSError:
            continue
        for p in files:
            size = p.stat().st_size
            if size < 1.5e9:        # un LoRA rangé au mauvais endroit, pas un checkpoint
                continue
            arch = _arch(p)
            if arch is None:
                continue
            out.append({"id": "local:" + hashlib.sha1(str(p).encode()).hexdigest()[:10], "kind": "image",
                        "name": p.stem, "source": label, "path": str(p), "size": size, "arch": arch,
                        "family": arch if arch in ("sd15", "sdxl") else "",
                        "defaults": gen_catalog.LOCAL_DEFAULTS.get({"sd2": "sd15"}.get(arch, arch), gen_catalog.LOCAL_DEFAULTS["sdxl"]),
                        "installed": True, "local": True,
                        "blurb": ARCH_LABELS.get(arch, "Architecture non reconnue : sd.cpp la détectera au chargement.")})
    return out


def _civitai_checkpoints():
    """Checkpoints téléchargés depuis Civitai (diffusion/checkpoints/, avec leur .json).
    Pour les familles récentes, seul le modèle de diffusion est dans le fichier : il
    faut le modèle de base du catalogue pour l'encodeur de texte et le VAE."""
    d = paths.diffusion_dir() / "checkpoints"
    out = []
    for p in sorted(d.iterdir()) if d.is_dir() else []:
        if p.suffix.lower() not in (".safetensors", ".gguf", ".ckpt"):
            continue
        try:
            meta = json.loads(p.with_suffix(".json").read_text())
        except (OSError, ValueError):
            meta = {}
        fam = meta.get("family") or {"sd15": "sd15", "sdxl": "sdxl"}.get(_arch(p) or "", "")
        famd = gen_catalog.FAMILIES.get(fam, {})
        comp = gen_catalog.find(famd.get("companion", "")) if famd.get("companion") else None
        if comp:
            comp_ok = all(ok for role, _, ok, _ in _files_of(comp) if role not in ("diffusion_model", "high_noise"))
            defaults = dict(comp["defaults"], **gen_catalog.BASE_OVERRIDES.get(meta.get("baseModel", ""), {}))
        else:
            comp_ok = True
            defaults = gen_catalog.LOCAL_DEFAULTS.get(fam, gen_catalog.LOCAL_DEFAULTS["sdxl"])
        out.append({"id": "local:" + hashlib.sha1(str(p).encode()).hexdigest()[:10], "kind": comp["kind"] if comp else "image",
                    "name": meta.get("name") or p.stem, "version": meta.get("version", ""), "source": "Civitai",
                    "path": str(p), "size": p.stat().st_size, "arch": fam, "family": fam,
                    "companion": comp["id"] if comp else "", "defaults": defaults, "installed": comp_ok, "local": True,
                    "features": comp.get("features", []) if comp else [], "image": meta.get("image", ""), "url": meta.get("url", ""),
                    "blurb": (f"{meta.get('baseModel', '')} — " if meta else "") + (
                        f"utilise l'encodeur de texte et le VAE de « {comp['name']} »" + ("" if comp_ok else
                        f" : téléchargez d'abord {comp['name']} dans le catalogue") if comp else "checkpoint tout-en-un")})
    return out


ARCH_LABELS = {"sd15": "Checkpoint Stable Diffusion 1.5", "sd2": "Checkpoint Stable Diffusion 2",
               "sdxl": "Checkpoint SDXL (Pony, Illustrious…)", "sd3": "Stable Diffusion 3.5 tout-en-un",
               "flux": "FLUX tout-en-un"}
_ARCH = {}


def _arch(p):
    """Architecture d'après les noms de tenseurs de l'en-tête safetensors (quelques Ko à
    lire). None = pas un checkpoint (LoRA, VAE…), "" = inconnue."""
    st = p.stat()
    k = (str(p), st.st_mtime, st.st_size)
    if k in _ARCH:
        return _ARCH[k]
    arch = ""
    if p.suffix.lower() == ".safetensors":
        try:
            with open(p, "rb") as f:
                n = int.from_bytes(f.read(8), "little")
                keys = json.loads(f.read(min(n, 50_000_000)).decode("utf-8", "replace")).keys()
            blob = "\n".join(keys)
            if "conditioner.embedders.1" in blob:
                arch = "sdxl"
            elif "joint_blocks" in blob:
                arch = "sd3"
            elif "double_blocks" in blob:
                arch = "flux"
            elif "cond_stage_model.transformer" in blob:
                arch = "sd15"
            elif "cond_stage_model.model" in blob:
                arch = "sd2"
            elif "model.diffusion_model" not in blob:
                arch = None
        except (OSError, ValueError):
            pass
    _ARCH[k] = arch
    return arch


def models(kind):
    out = []
    for e in gen_catalog.CATALOG:
        if e["kind"] != kind:
            continue
        files = _files_of(e)
        out.append({**{k: v for k, v in e.items() if k != "files"},
                    "files": [{"role": r, "label": gen_catalog.ROLE_LABELS[r], "path": p, "present": ok, "size": s}
                              for r, p, ok, s in files],
                    "size": sum(s for *_, s in files), "missing": sum(s for _, _, ok, s in files if not ok),
                    "installed": all(ok for _, _, ok, _ in files)})
    if kind == "image":
        out += local_checkpoints()
    return out


def find(model_id):
    if model_id.startswith("local:"):
        return next((m for m in local_checkpoints() if m["id"] == model_id), None)
    return gen_catalog.find(model_id)


def lora_dir(m=None):
    """Un seul dossier de LoRA pour sd-server : diffusion/loras/ (LoRA venus de Civitai)
    + des liens symboliques vers les LoRA des WebUI (sous-dossier webui-<nom>/). Ainsi les
    LoRA de la WebUI restent où ils sont et servent aussi aux checkpoints Civitai."""
    d = paths.diffusion_dir() / "loras"
    d.mkdir(exist_ok=True)
    for w in webui_installs():
        src = w / "models" / "Lora"
        if not src.is_dir():
            continue
        dst = d / f"webui-{w.name}"
        try:
            for f in src.rglob("*"):
                if f.suffix.lower() in (".safetensors", ".ckpt") and f.is_file():
                    link = dst / f.relative_to(src)
                    if not link.is_symlink():
                        link.parent.mkdir(parents=True, exist_ok=True)
                        link.symlink_to(f)
            for link in dst.rglob("*"):
                if link.is_symlink() and not link.exists():     # LoRA supprimé côté WebUI
                    link.unlink()
        except OSError:
            pass    # liens impossibles (Windows sans mode développeur) : LoRA de la WebUI non visibles
    return str(d)


def _lora_family(p):
    """Famille d'un LoRA : .json Civitai, sinon métadonnées kohya / modelspec de l'en-tête."""
    try:
        return json.loads(p.with_suffix(".json").read_text()).get("family") or "", \
            json.loads(p.with_suffix(".json").read_text()).get("words") or []
    except (OSError, ValueError):
        pass
    try:
        with open(p, "rb") as f:
            n = int.from_bytes(f.read(8), "little")
            meta = (json.loads(f.read(min(n, 50_000_000)).decode("utf-8", "replace")).get("__metadata__") or {})
    except (OSError, ValueError):
        return "", []
    txt = (meta.get("ss_base_model_version", "") + " " + meta.get("modelspec.architecture", "")).lower()
    words = []
    try:     # mots les plus fréquents du jeu d'entraînement (kohya) : utile pour déclencher le LoRA
        freq = json.loads(meta.get("ss_tag_frequency", "{}"))
        tags = {}
        for d in freq.values():
            for t, c in d.items():
                tags[t.strip()] = tags.get(t.strip(), 0) + c
        words = [t for t, _ in sorted(tags.items(), key=lambda x: -x[1])[:3]]
    except (ValueError, AttributeError):
        pass
    for key, fam in (("sdxl", "sdxl"), ("stable-diffusion-xl", "sdxl"), ("sd_v1", "sd15"), ("stable-diffusion-v1", "sd15"),
                     ("flux", "flux"), ("z-image", "zimage"), ("zimage", "zimage")):
        if key in txt:
            return fam, words
    return "", words


def loras(model_id):
    """LoRA disponibles, avec leur famille : un LoRA ne marche qu'avec un modèle de la même
    famille (un LoRA SDXL ne fait rien sur SD 1.5)."""
    m = find(model_id)
    d = Path(lora_dir())
    items = []
    for p in sorted(d.rglob("*"), key=lambda x: x.stem.lower()):
        if p.suffix.lower() in (".safetensors", ".ckpt", ".gguf") and p.is_file():
            fam, words = _lora_family(p)
            items.append({"name": p.stem, "family": fam, "words": words,
                          "source": p.relative_to(d).parts[0] if len(p.relative_to(d).parts) > 1 else "Civitai"})
    return {"dir": str(d), "loras": items, "family": (m or {}).get("family", "")}


def download_model(job, model_id):
    e = gen_catalog.find(model_id)
    if not e:
        raise RuntimeError("modèle inconnu")
    todo = [(r, f) for r, f in e["files"].items() if not _dest(f[0], f[1]).exists()]
    need, free = sum(f[2] for _, f in todo), shutil.disk_usage(paths.diffusion_dir()).free
    if need > free - 2e9:
        raise RuntimeError(f"pas assez de place : {need / 1e9:.1f} Go à télécharger, {free / 1e9:.1f} Go libres "
                           "(l'onglet Stockage permet de faire le ménage)")
    for i, (role, (repo, file, size)) in enumerate(todo, 1):
        dest = _dest(repo, file)
        dest.parent.mkdir(parents=True, exist_ok=True)
        job.logline(f"{repo}/{file}")
        download.download(f"{HF}/{repo}/resolve/main/{file}", str(dest), job, None,
                          f"[{i}/{len(todo)}] {gen_catalog.ROLE_LABELS[role]} —")
        if job.cancel_requested:
            return None
    return {"id": model_id}


def add_dir(d):
    cfg = engine.load_config()
    dirs = cfg.setdefault("sd_dirs", [])
    if d not in dirs:
        dirs.append(d)
    engine.save_config(cfg)


# ---------------------------------------------------------------- serveur

def _server_args(m, opts, port, job=None):
    """Ligne de commande de sd-server pour le modèle m (entrée du catalogue ou checkpoint local)."""
    args = []
    if m.get("companion"):
        comp = gen_catalog.find(m["companion"])
        args += ["--diffusion-model", m["path"]]
        for role, path, ok, _ in _files_of(comp):
            if role in ("diffusion_model", "high_noise"):
                continue
            if not ok:
                raise RuntimeError(f"il manque {Path(path).name} : téléchargez « {comp['name']} » dans le catalogue")
            args += [gen_catalog.ROLE_FLAGS[role], path]
        args += comp.get("server_args", [])
    elif m.get("local"):
        args += ["-m", m["path"]]
        if m["arch"] == "sdxl":
            repo, file, _ = gen_catalog.SDXL_VAE_FIX
            vae = _dest(repo, file)
            if not vae.exists() and job:
                vae.parent.mkdir(parents=True, exist_ok=True)
                job.set(detail="téléchargement du VAE SDXL corrigé (0,33 Go, une seule fois)…")
                download.download(f"{HF}/{repo}/resolve/main/{file}", str(vae), job, None, "VAE SDXL —")
            if vae.exists():
                args += ["--vae", str(vae)]
    else:
        for role, path, ok, size in _files_of(m):
            if not ok:
                raise RuntimeError(f"fichier manquant : {Path(path).name} — téléchargez d'abord le modèle")
            args += [gen_catalog.ROLE_FLAGS[role], path]
        args += m.get("server_args", [])
    args += ["--lora-model-dir", lora_dir()]
    # Par défaut sd.cpp place lui-même les poids (--auto-fit : GPU, puis RAM, puis disque)
    offload = opts.get("offload", "auto")
    if offload == "cpu":
        args += ["--offload-to-cpu"]
    elif offload == "disk":
        args += ["--params-backend", "disk"]
    if opts.get("flash_attn", True):
        args += ["--diffusion-fa"]
    args += ["--listen-ip", "127.0.0.1", "--listen-port", str(port)]
    return args


def ensure_server(m, opts, job):
    """Démarre sd-server sur ce modèle s'il ne tourne pas déjà avec la même configuration."""
    b = current_bin()
    if not b:
        raise RuntimeError("stable-diffusion.cpp n'est pas installé")
    key = json.dumps([m["id"], opts.get("offload", "auto"), opts.get("flash_attn", True)])
    if SD.alive() and SD.key == key and SD.state in ("starting", "ready"):
        SD.wait_ready(job)
        return
    stopped = claim_gpu("sd-server")
    if stopped:
        job.logline("arrêté pour libérer la carte graphique : " + ", ".join(stopped))
    from .server import pick_port
    port = pick_port(8190)
    args = _server_args(m, opts, port, job)
    job.set(0.01, "démarrage de sd-server…")
    SD.start([b, *args], engine.env_for(b), str(paths.home()), f"http://127.0.0.1:{port}", "/sdcpp/v1/capabilities",
             key, {"model": m["name"], "id": m["id"], "kind": m.get("kind", "image")})
    SD.wait_ready(job)
    SD.info["caps"] = _get(SD.base_url + "/sdcpp/v1/capabilities")


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"sd-server : {e.read().decode('utf-8', 'replace')[:400]}") from None


# ---------------------------------------------------------------- génération

RE_LORA = re.compile(r"<lora:([^:>]+)(?::([-\d.]+))?>")


def _loras(prompt, caps):
    """Traduit les balises <lora:nom:poids> (habitude WebUI) en liste structurée : l'API
    de sd-server refuse volontairement les balises dans le prompt."""
    known = {l["name"].lower(): l["path"] for l in caps.get("loras", [])}
    out, missing = [], []
    for name, w in RE_LORA.findall(prompt):
        path = known.get(name.lower()) or known.get(Path(name).stem.lower())
        if path:
            out.append({"path": path, "multiplier": float(w or 1)})
        else:
            missing.append(name)
    if missing:
        raise RuntimeError("LoRA introuvable : " + ", ".join(missing) + " (voir la liste des LoRA disponibles)")
    return RE_LORA.sub("", prompt).strip(" ,"), out


def _b64(data_url):
    return data_url.split(",", 1)[1] if data_url and "," in data_url else data_url


def generate(job, req):
    """req : {model, opts, prompt, negative, width, height, steps, cfg, sampler, scheduler, seed,
    batch, clip_skip, hires…, init_image, strength, ref_images, end_image, frames, fps, flow_shift…}"""
    m = find(req["model"])
    if not m:
        raise RuntimeError("modèle introuvable")
    kind = m.get("kind", "image")
    t0 = time.time()
    ensure_server(m, req.get("opts") or {}, job)
    caps = SD.info.get("caps") or {}
    prompt, loras = _loras(req.get("prompt", ""), caps)
    seed = int(req.get("seed", -1))
    if seed < 0:
        seed = random.randint(0, 2 ** 31 - 1)
    sp = {"sample_steps": int(req["steps"]), "guidance": {"txt_cfg": float(req["cfg"])}}
    if req.get("sampler"):
        sp["sample_method"] = req["sampler"]
    if req.get("scheduler"):
        sp["scheduler"] = req["scheduler"]
    if req.get("flow_shift"):
        sp["flow_shift"] = float(req["flow_shift"])
    body = {"prompt": prompt, "negative_prompt": req.get("negative", ""), "width": int(req["width"]),
            "height": int(req["height"]), "seed": seed, "sample_params": sp, "lora": loras,
            "vae_tiling_params": {"enabled": bool(req.get("vae_tiling"))}}
    if req.get("clip_skip") not in (None, "", -1):
        body["clip_skip"] = int(req["clip_skip"])
    if req.get("init_image"):
        body["init_image"] = _b64(req["init_image"])
        body["strength"] = float(req.get("strength", 0.75))
    if kind == "image":
        body["batch_count"] = int(req.get("batch", 1))
        body["output_format"] = "png"
        if req.get("ref_images"):
            body["ref_images"] = [_b64(x) for x in req["ref_images"]]
        if req.get("hires"):
            body["hires"] = {"enabled": True, "upscaler": req.get("hires_upscaler", "Latent"),
                             "scale": float(req.get("hires_scale", 1.5)), "steps": int(req.get("hires_steps", 0)),
                             "denoising_strength": float(req.get("hires_denoise", 0.5))}
        endpoint = "/sdcpp/v1/img_gen"
    else:
        fmts = (caps.get("output_formats_by_mode") or {}).get("vid_gen") or ["webm"]
        body.update({"video_frames": int(req["frames"]), "fps": int(req["fps"]),
                     "output_format": "webm" if "webm" in fmts else fmts[0]})
        if req.get("end_image"):
            body["end_image"] = _b64(req["end_image"])
        if req.get("high_noise_steps"):
            body["high_noise_sample_params"] = {"sample_steps": int(req["high_noise_steps"]),
                                                "guidance": {"txt_cfg": float(req["cfg"])}, **(
                                                    {"sample_method": req["sampler"]} if req.get("sampler") else {})}
        endpoint = "/sdcpp/v1/vid_gen"
    job.set(0.02, "envoi à sd-server…")
    SD.step = None
    sub = _post(SD.base_url + endpoint, body)
    jid = sub["id"]
    t_gen = time.time()
    while True:
        time.sleep(0.5)
        if job.cancel_requested:
            try:
                _post(f"{SD.base_url}/sdcpp/v1/jobs/{jid}/cancel", {})
            except Exception:
                pass
            return None
        if not SD.alive():
            raise RuntimeError(SD.error or "sd-server s'est arrêté pendant la génération (mémoire ?)")
        st = _get(f"{SD.base_url}/sdcpp/v1/jobs/{jid}")
        if st["status"] == "completed":
            break
        if st["status"] in ("failed", "cancelled"):
            raise RuntimeError((st.get("error") or {}).get("message") or st["status"])
        s = SD.step
        if s:
            stage = "chargement" if "B/s" in s[2] else "calcul"
            job.set(s[0] / max(1, s[1]), f"{stage} {s[0]}/{s[1]}" + (f" · {s[2]}" if s[2] else "")
                    + f" · {int(time.time() - t_gen)} s")
        else:
            job.set(detail=f"{'en file' if st['status'] == 'queued' else 'préparation'}… {int(time.time() - t_gen)} s")
    res = st["result"]
    meta = {k: v for k, v in req.items() if k not in ("init_image", "ref_images", "end_image")}
    meta.update({"seed": seed, "model_name": m["name"], "kind": kind, "seconds": round(time.time() - t0, 1),
                 "gen_seconds": round(time.time() - t_gen, 1)})
    if kind == "image":
        files = [_save("image", base64.b64decode(im["b64_json"]), "png", dict(meta, seed=seed + im.get("index", 0)))
                 for im in res.get("images", [])]
    else:
        ext = {"video/webm": "webm", "video/x-msvideo": "avi"}.get(res.get("mime_type"), res.get("output_format", "webm"))
        files = [_save("video", base64.b64decode(res["b64_json"]), ext, meta)]
    job.set(1.0, f"terminé en {meta['seconds']} s")
    return {"files": files, "seed": seed, "seconds": meta["seconds"]}


# ---------------------------------------------------------------- sorties

def _save(kind, data, ext, meta):
    d = paths.outputs_dir() / kind
    d.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("%Y%m%d-%H%M%S") + f"-{meta.get('seed', 0)}"
    p = d / f"{stem}.{ext}"
    n = 1
    while p.exists():
        p = d / f"{stem}-{n}.{ext}"
        n += 1
    p.write_bytes(data)
    p.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return {"name": p.name, "url": f"/outputs/{kind}/{p.name}", "meta": meta}


def outputs(kind, limit=60):
    d = paths.outputs_dir() / kind
    if not d.is_dir():
        return []
    items = sorted((p for p in d.iterdir() if p.suffix != ".json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in items[:limit]:
        try:
            meta = json.loads(p.with_suffix(".json").read_text())
        except (OSError, ValueError):
            meta = {}
        out.append({"name": p.name, "url": f"/outputs/{kind}/{p.name}", "meta": meta})
    return out


def _wslpath(flag, p):
    """Conversion de chemin Linux ↔ Windows sous WSL (None ailleurs ou en cas d'échec)."""
    if not shutil.which("wslpath"):
        return None
    r = subprocess.run(["wslpath", flag, str(p)], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def outputs_info():
    d = paths.outputs_dir()
    return {"path": str(d), "windows": _wslpath("-w", d), "custom": bool(engine.load_config().get("outputs_dir"))}


def set_outputs_dir(path):
    """Change le dossier de la galerie ; vide = retour au dossier par défaut. Sous WSL,
    un chemin Windows (C:\\Users\\…) est accepté. Les fichiers déjà générés ne bougent pas."""
    path = (path or "").strip()
    if path and (":" in path or "\\" in path):
        path = _wslpath("-u", path) or path
    if path:
        Path(path).expanduser().mkdir(parents=True, exist_ok=True)
    cfg = engine.load_config()
    if path:
        cfg["outputs_dir"] = str(Path(path).expanduser())
    else:
        cfg.pop("outputs_dir", None)
    engine.save_config(cfg)
    return outputs_info()


def open_outputs(kind):
    """Ouvre le dossier dans l'explorateur de fichiers de la machine qui fait tourner le launcher."""
    if kind not in ("image", "video", "music"):
        raise RuntimeError("type inconnu")
    d = paths.outputs_dir() / kind
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(d)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(d)])
    elif shutil.which("explorer.exe") and _wslpath("-w", d):
        subprocess.Popen(["explorer.exe", _wslpath("-w", d)])
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(d)])
    else:
        raise RuntimeError("aucun explorateur de fichiers trouvé")


def delete_output(kind, name):
    p = (paths.outputs_dir() / kind / name).resolve()
    if p.parent != (paths.outputs_dir() / kind).resolve():
        raise RuntimeError("chemin invalide")
    p.unlink(missing_ok=True)
    p.with_suffix(".json").unlink(missing_ok=True)
