"""Civitai : chercher et télécharger des checkpoints et des LoRA sans quitter le launcher.

Recherche par l'API publique (/api/v1/models). Le téléchargement exige une clé API
Civitai (compte gratuit, civitai.com/user/account) : elle est gardée dans config.json
et passée en paramètre `token` de l'URL. Chaque fichier est rangé avec un .json de
métadonnées (famille, mots déclencheurs…) :
    diffusion/checkpoints/   checkpoints
    diffusion/loras/         LoRA (dossier de LoRA commun, voir sdcpp.lora_dir)"""
import hashlib
import json
import shutil
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import urllib.parse

from . import paths, download, engine, gen_catalog

API = "https://civitai.com/api/v1"


def token():
    return engine.load_config().get("civitai_token", "")


def set_token(t):
    cfg = engine.load_config()
    cfg["civitai_token"] = t.strip()
    engine.save_config(cfg)


def new_bases():
    """{modèle de base Civitai: doc sd.cpp} des familles que stable-diffusion.cpp sait faire tourner
    et que le launcher n'a pas encore (« Modèles de base » de la recherche). Une variante d'une
    famille déjà connue (« Krea 2 Turbo » à côté de « Krea 2 ») n'en est pas une nouvelle."""
    from . import updates     # import tardif : updates importe sdcpp, qui n'a pas besoin de Civitai
    cache = paths.cache_dir() / "civitai-enums.json"
    try:
        if time.time() - cache.stat().st_mtime > 86400:
            raise OSError
        names = json.loads(cache.read_text())
    except (OSError, ValueError):
        names = download.fetch_json(f"{API}/enums", timeout=40)["BaseModel"]
        cache.write_text(json.dumps(names))
    known = [b for f in gen_catalog.FAMILIES.values() for b in f["civitai"]]
    kstems = [updates._stem(b) for b in known]
    kdocs = set(updates.base_docs(known).values())
    out = {}
    for b, doc in updates.base_docs([b for b in names if b not in known]).items():
        st = updates._stem(b)
        if doc in kdocs and any(st.startswith(k) or k.startswith(st) for k in kstems):
            continue
        out[b] = doc
    return out


def search(query="", kind="Checkpoint", family="", sort="Most Downloaded", nsfw=False, cursor=""):
    """kind : Checkpoint | LORA | Base (modèles de base pas encore au catalogue) ; family : clé de
    gen_catalog.FAMILIES, ou "" = toutes celles que le launcher sait utiliser."""
    bases = new_bases() if kind == "Base" else {}
    if kind == "Base" and not cursor:
        # une carte par modèle de base (son checkpoint le plus téléchargé), sinon les dérivés
        # de FLUX.1 remplissent toute la page ; un texte filtre sur le nom du modèle de base
        norm = lambda t: "".join(ch for ch in t.lower() if ch.isalnum())     # noqa: E731
        from . import updates
        names = [b for b in bases if (not query or norm(query) in norm(b))
                 and (family not in ("image", "video") or updates.doc_kind(bases[b]) == family)]
        if names:
            def top(b):
                try:
                    items = search("", "Base1", b, sort, nsfw)["items"][:1]
                except Exception:  # noqa: BLE001
                    return []
                for i in items:     # la version faite pour ce modèle de base d'abord
                    i["versions"].sort(key=lambda v: v["baseModel"] != b)
                return items
            with ThreadPoolExecutor(8) as ex:
                items = [i for r in ex.map(top, names) for i in r]
            items.sort(key=lambda i: -i["downloads"])
            return {"items": items, "next": "", "has_token": bool(token())}
    if kind == "Base1":     # le premier checkpoint d'un modèle de base donné (family = son nom Civitai)
        bases = {family: new_bases().get(family, "")}
    q = [("limit", "1" if kind == "Base1" else "24"), ("types", "Checkpoint" if kind in ("Base", "Base1") else kind),
         ("sort", sort), ("nsfw", "true" if nsfw else "false")]
    if kind in ("Base", "Base1"):
        q += [("baseModels", b) for b in bases]
    else:
        fams = [family] if family else [k for k, f in gen_catalog.FAMILIES.items() if kind == "LORA" or not f.get("lora_only")]
        q += [("baseModels", b) for k in fams for b in gen_catalog.FAMILIES[k]["civitai"]]
    if query:
        q.append(("query", query))
    if cursor:
        q.append(("cursor", cursor))
    d = download.fetch_json(f"{API}/models?{urllib.parse.urlencode(q)}", timeout=40)
    out = []
    for m in d.get("items", []):
        versions = []
        for v in m.get("modelVersions", [])[:6]:
            fam = gen_catalog.family_of_civitai(v.get("baseModel", ""))
            files = [{"id": f["id"], "name": f["name"], "size": int(f.get("sizeKB", 0) * 1024), "type": f.get("type"),
                      "primary": bool(f.get("primary")), "format": (f.get("metadata") or {}).get("format"),
                      "fp": (f.get("metadata") or {}).get("fp"), "url": f["downloadUrl"]}
                     for f in v.get("files", []) if f.get("type") in ("Model", "Pruned Model")
                     and (f.get("metadata") or {}).get("format") in ("SafeTensor", "GGUF", None)]
            pickle = any((f.get("metadata") or {}).get("format") == "PickleTensor" for f in v.get("files", []))
            versions.append({"id": v["id"], "name": v.get("name", ""), "baseModel": v.get("baseModel", ""), "family": fam,
                             "doc": bases.get(v.get("baseModel", ""), ""),
                             "files": files, "pickle_only": pickle and not files, "words": v.get("trainedWords") or [],
                             "image": _image(v.get("images") or [], nsfw)})
        if not versions:
            continue
        out.append({"id": m["id"], "name": m["name"], "type": m["type"], "nsfw": m.get("nsfw", False),
                    "creator": (m.get("creator") or {}).get("username", ""),
                    "downloads": (m.get("stats") or {}).get("downloadCount", 0),
                    "likes": (m.get("stats") or {}).get("thumbsUpCount", 0),
                    "url": f"https://civitai.com/models/{m['id']}", "versions": versions,
                    "commercial": m.get("allowCommercialUse")})
    return {"items": out, "next": (d.get("metadata") or {}).get("nextCursor") or "", "has_token": bool(token())}


def _image(images, nsfw):
    """Première image d'aperçu (pas de vidéo), sans contenu adulte sauf demande."""
    for im in images:
        if im.get("type", "image") == "image" and (nsfw or (im.get("nsfwLevel") or 1) <= 1):
            # redimensionnée côté Civitai : ne pas charger l'original de plusieurs Mo
            return im["url"].replace("/original=true/", "/width=360/")
    return ""


def download_file(job, kind, info):
    """info : {model, version (dict renvoyé par search), file (id)}"""
    t = token()
    if not t:
        raise RuntimeError("clé API Civitai manquante : Civitai exige un compte pour télécharger "
                           "(civitai.com/user/account → API Keys), collez-la dans le launcher")
    v = info["version"]
    f = next(x for x in v["files"] if x["id"] == info["file"])
    d = paths.diffusion_dir() / ("loras" if kind == "LORA" else "checkpoints")
    d.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(d).free
    if f["size"] > free - 2e9:
        raise RuntimeError(f"pas assez de place : {f['size'] / 1e9:.1f} Go à télécharger, {free / 1e9:.1f} Go libres")
    dest = d / f["name"]
    url = f["url"] + ("&" if "?" in f["url"] else "?") + "token=" + urllib.parse.quote(t)
    try:
        download.download(url, str(dest), job, f["size"] or None, f["name"] + " —")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("Civitai refuse le téléchargement : clé API invalide, ou modèle réservé "
                               "(accès anticipé payant, contenu restreint)") from None
        raise
    if job.cancel_requested:
        return None
    meta = {"source": "civitai", "type": kind, "name": info["model"]["name"], "version": v["name"],
            "model_id": info["model"]["id"], "version_id": v["id"], "baseModel": v["baseModel"], "family": v["family"],
            "words": v["words"], "image": v["image"], "url": info["model"]["url"]}
    dest.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return {"path": str(dest)}


# ---------------------------------------------------------------- aperçus de l'arborescence

IDS_MISS_TTL = 7 * 86400        # fichier inconnu de Civitai : on réessaie au bout d'une semaine
BASE_TTL = 7 * 86400
_ids_lock = threading.Lock()


def _ids_file():
    return paths.cache_dir() / "civitai-ids.json"


def _images(lst):
    """Images d'exemple (pas de vidéo), réduites par le CDN de Civitai, avec leur niveau
    (1 = tout public) : l'interface choisit selon la case « contenu adulte »."""
    out = []
    for im in lst:
        if im.get("type", "image") == "image" and im.get("url"):
            out.append({"url": im["url"].replace("/original=true/", "/width=360/"), "nsfw": im.get("nsfwLevel") or 1})
        if len(out) >= 12:
            break
    return out


def _autov1(p):
    """Empreinte courte « AutoV1 » de la WebUI : 64 Ko lus à 1 Mo du début (instantané,
    même pour un checkpoint de 7 Go sur un disque Windows)."""
    with open(p, "rb") as f:
        f.seek(0x100000)
        return hashlib.sha256(f.read(0x10000)).hexdigest()[:8]


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _lookup(p):
    """Version Civitai d'un fichier local, d'après son empreinte. None = réseau en panne
    (ne rien mémoriser), {"found": False} = inconnu de Civitai."""
    try:
        meta = json.loads(p.with_suffix(".json").read_text())
        if meta.get("version_id"):         # téléchargé depuis le launcher : déjà décrit
            return {"found": True, "name": meta.get("name", ""), "version": meta.get("version", ""),
                    "baseModel": meta.get("baseModel", ""), "family": meta.get("family") or "", "url": meta.get("url", ""),
                    "words": meta.get("words") or [], "images": [{"url": meta["image"], "nsfw": 1}] if meta.get("image") else []}
    except (OSError, ValueError):
        pass
    size = p.stat().st_size
    tries = [_autov1] if size > 0x110000 else []
    if size < 1.5e9:            # petit fichier (LoRA) : l'empreinte complète, au cas où
        tries.append(_sha256)
    for fn in tries:
        try:
            v = download.fetch_json(f"{API}/model-versions/by-hash/{fn(p)}", timeout=30)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            return None
        except Exception:  # noqa: BLE001
            return None
        # l'empreinte courte peut se tromper : la taille du fichier doit concorder
        if not any(abs(f.get("sizeKB", 0) * 1024 - size) < max(2e6, size * 0.01) for f in v.get("files", [])):
            continue
        return {"found": True, "name": (v.get("model") or {}).get("name", ""), "version": v.get("name", ""),
                "baseModel": v.get("baseModel", ""), "family": gen_catalog.family_of_civitai(v.get("baseModel", "")) or "",
                "url": f"https://civitai.com/models/{v.get('modelId')}?modelVersionId={v.get('id')}",
                "words": v.get("trainedWords") or [], "images": _images(v.get("images") or [])}
    return {"found": False}


def _base_lookup(base):
    """Images d'exemple d'un modèle de base : celles de ses modèles les plus téléchargés, le
    modèle qui porte son nom d'abord. Deux requêtes : en mode « tout public », Civitai ne
    renvoie que des images de niveau 1 ; l'autre complète pour la case « contenu adulte »."""
    norm = lambda t: "".join(ch for ch in t.lower() if ch.isalnum())     # noqa: E731
    imgs = []
    for nsfw in ("false", "true"):
        q = [("limit", "5"), ("sort", "Most Downloaded"), ("nsfw", nsfw), ("baseModels", base)]
        try:
            d = download.fetch_json(f"{API}/models?{urllib.parse.urlencode(q)}", timeout=40)
        except Exception:  # noqa: BLE001
            return None
        items = sorted(d.get("items", []), key=lambda m: norm(base) not in norm(m.get("name", "")))
        for m in items:
            for im in _images(((m.get("modelVersions") or [{}])[0]).get("images") or []):
                if im["url"] not in {i["url"] for i in imgs}:
                    imgs.append(im if nsfw == "true" else {**im, "nsfw": 1})
    return {"found": bool(imgs), "images": imgs[:24]}


def previews(files, bases):
    """{"files": {chemin: info}, "bases": {modèle de base: info}} pour l'arborescence, avec un
    cache disque : chaque fichier n'est cherché sur Civitai qu'une fois (tant qu'il ne change pas)."""
    with _ids_lock:
        try:
            cache = json.loads(_ids_file().read_text())
        except (OSError, ValueError):
            cache = {}
    now = time.time()
    todo = []
    for f in files:
        p = Path(f)
        try:
            st = p.stat()
        except OSError:
            continue
        c = cache.get(f)
        if not c or c.get("key") != [st.st_size, int(st.st_mtime)] or (not c.get("found") and now - c.get("ts", 0) > IDS_MISS_TTL):
            todo.append(("f", f, [st.st_size, int(st.st_mtime)]))
    for b in bases:
        c = cache.get("base:" + b)
        if not c or now - c.get("ts", 0) > BASE_TTL:
            todo.append(("b", b, None))

    def run(t):
        kind, k, key = t
        return t, (_lookup(Path(k)) if kind == "f" else _base_lookup(k))
    if todo:
        with ThreadPoolExecutor(6) as ex:
            res = list(ex.map(run, todo))
        with _ids_lock:
            try:
                cache = json.loads(_ids_file().read_text())
            except (OSError, ValueError):
                cache = {}
            for (kind, k, key), info in res:
                if info is not None:
                    cache[k if kind == "f" else "base:" + k] = {**info, "key": key, "ts": now}
            _ids_file().write_text(json.dumps(cache, ensure_ascii=False))
    return {"files": {f: cache[f] for f in files if f in cache},
            "bases": {b: cache["base:" + b] for b in bases if "base:" + b in cache}}
