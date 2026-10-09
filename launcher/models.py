"""Modèles : ceux déjà sur le disque (dossier du launcher, Ollama, LM Studio, dossiers
ajoutés) et ceux qu'on peut télécharger depuis Hugging Face. Chaque modèle local ou
distant peut être « profilé » (profile.build) — le résultat est mis en cache."""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from . import paths, gguf, profile, download, engine, hardware

HF = "https://huggingface.co"
RE_SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")


# ---------------------------------------------------------------- dossiers sources

def _windows_users():
    """Sous WSL, les dossiers utilisateur Windows sont visibles dans /mnt/c/Users."""
    if hardware.is_wsl():
        base = Path("/mnt/c/Users")
        if base.is_dir():
            return [p for p in base.iterdir() if p.is_dir() and p.name not in ("Public", "Default", "All Users", "Default User")]
    return []


def source_dirs():
    """[(label, path, kind)] — kind ∈ launcher | ollama | lmstudio | custom."""
    out = [("Launcher", paths.models_dir(), "launcher")]
    home = Path.home()
    cands = [("Ollama", home / ".ollama" / "models", "ollama"),
             ("Ollama (service)", Path("/usr/share/ollama/.ollama/models"), "ollama"),
             ("LM Studio", home / ".lmstudio" / "models", "lmstudio"),
             ("LM Studio", home / ".cache" / "lm-studio" / "models", "lmstudio")]
    for u in _windows_users():
        cands += [(f"Ollama (Windows, {u.name})", u / ".ollama" / "models", "ollama"),
                  (f"LM Studio (Windows, {u.name})", u / ".lmstudio" / "models", "lmstudio")]
    if sys.platform == "win32":
        la = os.environ.get("LOCALAPPDATA", "")
        if la:
            cands.append(("Ollama", Path(la) / "Ollama" / "models", "ollama"))
    for label, p, kind in cands:
        if p.is_dir() and not any(p == q for _, q, _ in out):
            out.append((label, p, kind))
    for d in engine.load_config().get("model_dirs", []):
        p = Path(d).expanduser()
        if p.is_dir() and not any(p == q for _, q, _ in out):
            out.append((p.name, p, "custom"))
    return out


def add_dir(d):
    cfg = engine.load_config()
    dirs = cfg.setdefault("model_dirs", [])
    if d not in dirs:
        dirs.append(d)
    engine.save_config(cfg)


# ---------------------------------------------------------------- inventaire local

def _is_mmproj(name):
    return "mmproj" in name.lower()


def _is_draft(name):
    """Brouillon pour le décodage spéculatif : têtes MTP publiées à part, EAGLE, dFlash…"""
    n = Path(name).name.lower()
    return n.startswith(("mtp-", "mtp_", "draft-", "eagle")) or "-mtp-" in n or "draft" in n or "dflash" in n or "dspark" in n


def _is_aux(name):
    n = Path(name).name.lower()
    return "imatrix" in n


def scan():
    """Liste des modèles locaux : [{id, name, path, size, source, kind, mmproj, shards}].
    Les mmproj sont rattachés au modèle du même dossier. Les fichiers que llama.cpp ne
    peut pas charger (blobs Ollama combinés) ne sont pas listés."""
    models = []
    seen = set()
    for label, root, kind in source_dirs():
        if kind == "ollama":
            for m in _scan_ollama(root, label):
                digest = Path(m["path"]).name  # même empreinte = même fichier (WSL / Windows)
                if digest not in seen:
                    seen.add(digest)
                    models.append(m)
            continue
        files = []
        try:
            for p in root.rglob("*.gguf"):
                if p.is_file() and not p.name.endswith(".part"):
                    files.append(p)
        except OSError:
            continue
        by_dir = {}
        for p in files:
            by_dir.setdefault(p.parent, []).append(p)
        for d, fs in by_dir.items():
            mmprojs = [p for p in fs if _is_mmproj(p.name)]
            for p in fs:
                if _is_mmproj(p.name):
                    continue
                m = RE_SHARD.search(p.name)
                if m and m.group(1) != "00001":
                    continue  # seuls les premiers fragments représentent un modèle
                shards = sorted(d.glob(p.name[:m.start()] + "-*-of-" + m.group(2) + ".gguf")) if m else [p]
                size = sum(s.stat().st_size for s in shards)
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                rel = p.relative_to(root)
                name = str(rel.parent / re.sub(r"-\d{5}-of-\d{5}", "", p.stem)) if rel.parent != Path(".") else p.stem
                models.append({"id": _id(key), "name": name.replace("\\", "/"), "path": key, "size": size,
                               "source": label, "kind": kind, "mmproj": str(mmprojs[0]) if mmprojs else "",
                               "shards": len(shards),
                               "draft": _is_draft(p.name), "mtime": p.stat().st_mtime})
    models = [m for m in models if _launchable(m["path"])]
    return sorted(models, key=lambda m: (m["kind"] != "launcher", m["name"].lower()))


def _launchable(path):
    try:
        return profile_local(path)["compatible"]
    except Exception:  # noqa: BLE001 — en-tête illisible : l'erreur s'affichera à la sélection
        return True


def _scan_ollama(root, label):
    """Les modèles Ollama sont des blobs GGUF nommés par leur empreinte ; le manifeste
    donne le nom lisible et sépare poids (image.model) et projecteur (image.projector).
    Un blob qui embarque lui-même les encodeurs vision/audio (fichier combiné) n'est
    pas lisible par llama.cpp officiel : scan() l'écarte."""
    out = []
    man = root / "manifests"
    if not man.is_dir():
        return out
    for mf in man.rglob("*"):
        if not mf.is_file():
            continue
        try:
            m = json.loads(mf.read_text())
        except (OSError, ValueError):
            continue
        blob = proj = None
        size = 0
        for layer in m.get("layers", []):
            mt = layer.get("mediaType", "")
            p = root / "blobs" / layer["digest"].replace(":", "-")
            if mt.endswith("image.model"):
                blob, size = p, layer.get("size", 0)
            elif mt.endswith("image.projector"):
                proj = p
        if not blob or not blob.exists():
            continue
        rel = mf.relative_to(man).parts  # registry / lib / name / tag
        name = "/".join(rel[1:-1]) + ":" + rel[-1]
        name = name.replace("library/", "")
        out.append({"id": _id(str(blob)), "name": name, "path": str(blob), "size": size or blob.stat().st_size,
                    "source": label, "kind": "ollama", "mmproj": str(proj) if proj and proj.exists() else "",
                    "shards": 1, "mtime": blob.stat().st_mtime})
    return out


def _id(s):
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def find(model_id):
    for m in scan():
        if m["id"] == model_id:
            return m
    return None


def models_dir_bytes():
    total = 0
    for p in paths.models_dir().rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


# ---------------------------------------------------------------- profils (cache)

def _cache_key(path_or_url, size):
    return hashlib.sha1(f"{path_or_url}|{size}".encode()).hexdigest()


def profile_local(path):
    """Profil d'un modèle local (fragments fusionnés). Mis en cache par (chemin, taille)."""
    p = Path(path)
    m = RE_SHARD.search(p.name)
    shards = sorted(p.parent.glob(p.name[:m.start()] + "-*-of-" + m.group(2) + ".gguf")) if m else [p]
    size = sum(s.stat().st_size for s in shards)
    cache = paths.cache_dir() / "profiles" / (_cache_key(str(p), size) + ".json")
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    header = gguf.read_header(str(shards[0]))
    for s in shards[1:]:
        h2 = gguf.read_header(str(s))
        header["tensors"] += h2["tensors"]
        header["file_size"] += h2["file_size"]
    prof = profile.build(header, p.stem)
    prof["file_size"] = size
    # Un fichier texte qui embarque des encodeurs vision/audio (blobs Ollama combinés)
    # n'est pas chargeable par llama.cpp officiel : « wrong number of tensors ».
    prof["compatible"] = prof["mmproj_bytes"] == 0
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(prof))
    return prof


def shard_names(filename):
    """Tous les fragments d'un modèle découpé (…-00001-of-00003.gguf → 3 noms)."""
    m = RE_SHARD.search(filename)
    if not m:
        return [filename]
    n = int(m.group(2))
    return [filename[:m.start()] + f"-{i:05d}-of-{n:05d}.gguf" for i in range(1, n + 1)]


def profile_remote(repo, filename, size):
    """Profil d'un fichier HF sans le télécharger : les en-têtes suffisent (quelques
    Mo par fragment). Un modèle découpé est lu fragment par fragment — le premier ne
    contient parfois que les métadonnées, les poids sont dans les suivants."""
    url = f"{HF}/{repo}/resolve/main/{filename}"
    cache = paths.cache_dir() / "profiles" / (_cache_key(url, size) + ".json")
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    header = None
    for name in shard_names(filename):
        h = gguf.read_header(url=f"{HF}/{repo}/resolve/main/{name}")
        if header is None:
            header = h
        else:
            header["tensors"] += h["tensors"]
            header["file_size"] += h["file_size"]
    prof = profile.build(header, Path(filename).stem)
    prof["file_size"] = max(size, header["file_size"])
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(prof))
    return prof


# ---------------------------------------------------------------- Hugging Face

def hf_repo(repo):
    """Fichiers GGUF d'un dépôt, groupés par quantification, avec les mmproj à part.
    Mis en cache 6 h."""
    cache = paths.cache_dir() / "hf" / (repo.replace("/", "__") + ".json")
    if cache.exists() and time.time() - cache.stat().st_mtime < 6 * 3600:
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    d = download.fetch_json(f"{HF}/api/models/{repo}?blobs=true")
    files = [s for s in d.get("siblings", []) if s["rfilename"].lower().endswith(".gguf")]
    quants, mmprojs, drafts = {}, [], []
    for s in files:
        name, size = s["rfilename"], s.get("size") or 0
        if _is_mmproj(name):
            mmprojs.append({"file": name, "size": size})
            continue
        if _is_draft(name):
            drafts.append({"file": name, "size": size, "quant": _quant_from_name(name[:-5])})
            continue
        if _is_aux(name):
            continue
        m = RE_SHARD.search(name)
        base = name[:m.start()] if m else name[:-5]
        q = quants.setdefault(base, {"file": name if not m or m.group(1) == "00001" else "", "size": 0, "shards": 0,
                                     "quant": _quant_from_name(base)})
        q["size"] += size
        q["shards"] += 1
        if m and m.group(1) == "00001":
            q["file"] = name
    # Étiquette = ce qui reste du nom une fois le préfixe commun retiré
    # (« Qwen3.6-35B-A3B-UD-Q4_K_M » → « UD-Q4_K_M ») : fidèle aux noms du dépôt.
    bases = [Path(b).name for b in quants]
    if len(bases) > 1:
        prefix = os.path.commonprefix(bases)
        prefix = prefix[:max(prefix.rfind("-"), prefix.rfind("_"), prefix.rfind(".")) + 1]
        for b, q in zip(bases, quants.values()):
            label = b[len(prefix):].strip("-_.") if b.startswith(prefix) else ""
            if label:
                q["quant"] = label
    out = {"repo": repo, "fetched": time.time(), "downloads": d.get("downloads"), "likes": d.get("likes"),
           "gguf": {k: v for k, v in (d.get("gguf") or {}).items() if k != "chat_template"},
           "tags": d.get("tags", []), "lastModified": d.get("lastModified", ""),
           "license": (d.get("cardData") or {}).get("license", ""),
           "quants": sorted([q for q in quants.values() if q["file"]], key=lambda q: q["size"]),
           "mmprojs": sorted(mmprojs, key=lambda m: m["size"]),
           "drafts": sorted(drafts, key=lambda m: m["size"])}
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def _quant_from_name(base):
    m = re.search(r"(UD-)?(IQ\d_[A-Z]+|Q\d_K_[A-Z]+|Q\d_K|Q\d_\d|MXFP4|BF16|F16|F32|Q\d_[A-Z]+_XL)", base, re.I)
    return (m.group(0) if m else base.split("-")[-1]).upper()


def hf_search(query, limit=20):
    q = download.fetch_json(f"{HF}/api/models?search={query}&filter=gguf&sort=downloads&direction=-1&limit={limit}")
    return [{"repo": m["id"], "downloads": m.get("downloads", 0), "likes": m.get("likes", 0),
             "lastModified": m.get("lastModified", "")} for m in q]


def download_hf(job, repo, filename, size, mmproj=None, draft=None):
    """Télécharge un fichier (et ses fragments) + mmproj / brouillon optionnels dans models/<repo>/."""
    dest_dir = paths.models_dir() / repo.replace("/", "__")
    dest_dir.mkdir(parents=True, exist_ok=True)
    targets = shard_names(filename)
    if mmproj:
        targets.append(mmproj)
    if draft:
        targets.append(draft)
    for t in targets:
        url = f"{HF}/{repo}/resolve/main/{t}"
        dest = dest_dir / Path(t).name
        if dest.exists():
            job.logline(f"déjà présent : {dest.name}")
            continue
        job.logline(f"téléchargement {t}")
        try:
            fsize = download.content_length(url)
        except Exception:
            fsize = None
        download.download(url, str(dest), job, fsize, f"{Path(t).name} —")
        if job.cancel_requested:
            return None
    first = dest_dir / Path(targets[0]).name
    return {"path": str(first), "dir": str(dest_dir)}


def analyze_repo(job, repo, hw, goal="balanced"):
    """Pour chaque quantification d'un dépôt, du plus petit au plus gros : profil
    (en-têtes distants, mis en cache) + configuration conseillée → verdict. On
    s'arrête après deux versions consécutives qui ne tiennent pas : les suivantes,
    plus grosses, ne tiendront pas non plus."""
    from . import recommend
    r = hf_repo(repo)
    mm = next((m for m in r["mmprojs"] if "f16" in m["file"].lower()), r["mmprojs"][0] if r["mmprojs"] else None)
    out, misses = [], 0
    n = len(r["quants"])
    for i, q in enumerate(r["quants"]):
        job.set(i / max(1, n), f"{q['quant']} — lecture des en-têtes…")
        entry = {"quant": q["quant"], "file": q["file"], "size": q["size"]}
        try:
            p = profile_remote(repo, q["file"], q["size"])
            rec = recommend.recommend(p, hw, goal, None, {"mmproj_size": mm["size"] if mm else 0,
                                                          "model_present": False})
            e, c = rec["estimate"], rec["cfg"]
            g = e["vram"][0] if e["vram"] else None
            fits = (not g or g["fits"]) and e["ram"]["streamed"] == 0 and not any(w["level"] == "error" for w in e["warnings"])
            if not fits:
                verdict = "too_big"
            elif g and c["ngl"] == "all" and not c.get("n_cpu_moe") and not c.get("cpu_moe_all"):
                verdict = "vram"
            elif g and (c.get("n_cpu_moe") or c.get("cpu_moe_all")):
                verdict = "offload_moe"
            elif g and c["ngl"] != "all":
                verdict = "offload"
            else:
                verdict = "cpu"
            entry.update({"verdict": verdict, "ctx": c["ctx"], "ngl": c["ngl"], "n_cpu_moe": c.get("n_cpu_moe", 0),
                          "cpu_moe_all": bool(c.get("cpu_moe_all")), "kv": c["type_k"],
                          "vram": g["need"] if g else 0, "ram": e["ram"]["weights_cpu"] + e["ram"]["resident"],
                          "streamed": e["ram"]["streamed"], "lazy": e["ram"]["segments"]["lazy"],
                          "summary": rec["verdict"], "n_layer": p["n_layer"], "is_moe": p["is_moe"],
                          "expert_bytes": p["expert_bytes"], "per_layer_embd": p["per_layer_embd"]})
            misses = misses + 1 if verdict == "too_big" else 0
        except Exception as ex:  # noqa: BLE001 — un fichier illisible ne doit pas bloquer les autres
            entry.update({"verdict": "error", "summary": str(ex)[:200]})
        out.append(entry)
        job.logline(f"{q['quant']}: {entry.get('verdict')} — {entry.get('summary', '')[:80]}")
        if job.cancel_requested or misses >= 2:
            break
    job.set(1.0, "analyse terminée")
    return {"repo": repo, "results": out, "skipped": n - len(out)}
