"""Civitai : chercher et télécharger des checkpoints et des LoRA sans quitter le launcher.

Recherche par l'API publique (/api/v1/models). Le téléchargement exige une clé API
Civitai (compte gratuit, civitai.com/user/account) : elle est gardée dans config.json
et passée en paramètre `token` de l'URL. Chaque fichier est rangé avec un .json de
métadonnées (famille, mots déclencheurs…) :
    diffusion/checkpoints/   checkpoints
    diffusion/loras/         LoRA (dossier de LoRA commun, voir sdcpp.lora_dir)"""
import json
import shutil
import urllib.error
import urllib.parse

from . import paths, download, engine, gen_catalog

API = "https://civitai.com/api/v1"


def token():
    return engine.load_config().get("civitai_token", "")


def set_token(t):
    cfg = engine.load_config()
    cfg["civitai_token"] = t.strip()
    engine.save_config(cfg)


def search(query="", kind="Checkpoint", family="", sort="Most Downloaded", nsfw=False, cursor=""):
    """kind : Checkpoint | LORA ; family : clé de gen_catalog.FAMILIES, ou "" = toutes celles
    que le launcher sait utiliser."""
    fams = [family] if family else [k for k, f in gen_catalog.FAMILIES.items() if kind == "LORA" or not f.get("lora_only")]
    q = [("limit", "24"), ("types", kind), ("sort", sort), ("nsfw", "true" if nsfw else "false")]
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
