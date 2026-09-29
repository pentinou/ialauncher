"""Espace disque : tout ce qu'occupent le launcher et les programmes qu'il installe,
groupé et supprimable. Les modèles utilisés en place (WebUI, Ollama, LM Studio) sont
listés pour information mais jamais supprimés d'ici."""
import os
import shutil
import subprocess
from pathlib import Path

from . import paths, engine, gen_catalog, models, sdcpp, music
from .services import SD, MUSIC


def _size(p):
    """Place réellement occupée (blocs), liens durs comptés une fois : les environnements
    uv sont faits de liens vers le cache uv."""
    p = Path(p)
    seen, total = set(), 0
    try:
        if p.is_file():
            st = p.stat()
            return st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    st = os.lstat(os.path.join(root, f))
                except OSError:
                    continue
                if st.st_nlink > 1:
                    if (st.st_dev, st.st_ino) in seen:
                        continue
                    seen.add((st.st_dev, st.st_ino))
                total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
    except OSError:
        pass
    return total


def _rel(p):
    return str(Path(p).resolve().relative_to(paths.home().resolve()))


def _item(label, path, note="", deletable=True, **kw):
    return {"id": "path:" + _rel(path), "label": label, "size": _size(path), "note": note, "deletable": deletable, **kw}


def scan():
    home = paths.home()
    groups = []

    # ---- texte
    items = []
    md = paths.models_dir()
    for d in sorted(md.iterdir()) if md.is_dir() else []:
        items.append(_item(d.name.replace("__", "/"), d, ", ".join(sorted(f.name for f in d.glob("*.gguf")))[:200]))
    groups.append({"id": "text", "label": "Modèles de texte (llama.cpp)", "items": items})

    # ---- image / vidéo : un modèle du catalogue = plusieurs fichiers, parfois partagés
    items, owned = [], {}
    complete = {str(sdcpp._dest(r, f)) for e in gen_catalog.CATALOG
                if all(sdcpp._dest(r2, f2).exists() for r2, f2, _ in e["files"].values())
                for r, f, _ in e["files"].values()}
    for e in gen_catalog.CATALOG:
        files = [sdcpp._dest(r, f) for r, f, _ in e["files"].values()]
        present = [f for f in files if f.exists()]
        # un modèle incomplet dont tous les fichiers servent à un modèle complet n'est pas « installé »
        if not present or (len(present) < len(files) and all(str(f) in complete for f in present)):
            continue
        for f in present:
            owned.setdefault(str(f), []).append(e["name"])
        items.append({"id": "catalog:" + e["id"], "label": f"{e['name']} ({'vidéo' if e['kind'] == 'video' else 'image'})",
                      "size": sum(_size(f) for f in present), "deletable": True, "files": [str(f) for f in present],
                      "note": ("incomplet : " if len(present) < len(files) else "") + ", ".join(f.name for f in present)})
    shared = {f: n for f, n in owned.items() if len(n) > 1}
    for it in items:
        s = [Path(f).name + " (aussi " + ", ".join(x for x in shared[f] if not it["label"].startswith(x)) + ")"
             for f in it["files"] if f in shared]
        if s:
            it["note"] += " — partagé : " + ", ".join(s) + " (conservé tant qu'un autre modèle l'utilise)"
    for p in sorted((paths.diffusion_dir() / "checkpoints").glob("*")) if (paths.diffusion_dir() / "checkpoints").is_dir() else []:
        if p.suffix != ".json":
            items.append(_item(f"{p.stem} (Civitai)", p, "checkpoint téléchargé depuis Civitai"))
    lora = paths.diffusion_dir() / "loras"
    for p in sorted(lora.glob("*")) if lora.is_dir() else []:
        if p.is_file() and not p.is_symlink() and p.suffix != ".json":
            items.append(_item(f"LoRA {p.stem}", p, "LoRA"))
    known = set(owned) | {str(sdcpp._dest(*gen_catalog.SDXL_VAE_FIX[:2]))}
    for d in sorted(paths.diffusion_dir().glob("*__*")):
        for f in d.iterdir():
            if f.is_file() and str(f) not in known:
                items.append(_item(f.name, f, "téléchargement en cours ou interrompu (reprend au prochain essai)"
                                   if f.suffix == ".part" else "fichier qui n'appartient plus au catalogue"))
    vae = sdcpp._dest(*gen_catalog.SDXL_VAE_FIX[:2])
    if vae.exists():
        items.append(_item("VAE SDXL corrigé", vae, "retéléchargé automatiquement au besoin"))
    groups.append({"id": "diffusion", "label": "Modèles d'image et de vidéo (stable-diffusion.cpp)", "items": items})

    # ---- moteurs
    items = []
    in_use = {engine.current_bin(), sdcpp.current_bin()}
    for d in sorted(paths.engines_dir().iterdir()):
        if d.is_dir():
            used = any(b and str(Path(b)).startswith(str(d) + os.sep) for b in in_use)
            items.append(_item(d.name, d, "version utilisée" if used else "ancienne version", deletable=not used))
    groups.append({"id": "engines", "label": "Moteurs (llama.cpp, stable-diffusion.cpp)", "items": items})

    # ---- musique
    items = []
    app = music.app_dir()
    if app.is_dir():
        ck = app / "checkpoints"
        for d in sorted(ck.iterdir()) if ck.is_dir() else []:
            if d.is_dir() and not d.name.startswith("."):
                items.append(_item(f"poids {d.name}", d, "retéléchargés au besoin"))
        prog = _size(app) - _size(ck)
        items.append({"id": "path:" + _rel(app), "label": "ACE-Step (programme + environnement Python)", "size": prog,
                      "note": "supprime aussi les poids", "deletable": True})
    groups.append({"id": "music", "label": "Musique (ACE-Step)", "items": items})

    # ---- créations et divers
    items = []
    for kind, label in (("image", "Images"), ("video", "Vidéos"), ("music", "Musiques")):
        d = paths.outputs_dir() / kind
        if d.is_dir():
            n = len([p for p in d.iterdir() if p.suffix != ".json"])
            items.append(_item(f"{label} générées ({n})", d, "tout le dossier : préférez la galerie pour trier"))
    for name, note in (("cache", "en-têtes lus, sources de compilation : se reconstruit"), ("logs", "journaux des serveurs")):
        items.append(_item(name, home / name, note))
    groups.append({"id": "misc", "label": "Créations et divers", "items": items})

    for g in groups:   # un fichier partagé par deux modèles ne compte qu'une fois
        uniq = {f: _size(f) for it in g["items"] for f in it.get("files", [])}
        g["total"] = sum(uniq.values()) + sum(it["size"] for it in g["items"] if "files" not in it)
    total = _size(home)

    # ---- hors du dossier du launcher
    ext = []
    uvc = Path.home() / ".cache" / "uv"
    if uvc.is_dir():
        ext.append({"id": "uvcache", "label": "Cache de uv (~/.cache/uv)", "size": _size(uvc), "deletable": True,
                    "note": "copies des paquets Python (PyTorch…) : partagé avec vos autres projets uv ; se retélécharge si besoin"})
    for w in sdcpp.webui_installs():
        ext.append({"id": "", "label": f"{w.name} (checkpoints)", "size": _size(w / "models" / "Stable-diffusion"), "deletable": False,
                    "note": f"utilisés en place : {w}"})
    for label, d, kind in models.source_dirs():
        if kind != "launcher":
            ext.append({"id": "", "label": label, "size": _size(d), "deletable": False, "note": f"utilisés en place : {d}"})
    return {"home": str(home), "total": total, "groups": groups, "external": ext,
            "disk": {"free": shutil.disk_usage(home).free, "total": shutil.disk_usage(home).total}}


def delete(item_id):
    """Supprime un élément de scan(). Arrête d'abord le service qui pourrait l'utiliser."""
    if item_id == "uvcache":
        uv = music.uv_bin()
        if not uv:
            raise RuntimeError("uv introuvable")
        MUSIC.stop()
        subprocess.run([uv, "cache", "clean"], capture_output=True, timeout=600)
        return
    if item_id.startswith("catalog:"):
        e = gen_catalog.find(item_id.split(":", 1)[1])
        others = {str(sdcpp._dest(r, f)) for o in gen_catalog.CATALOG if o is not e
                  for r, f, _ in o["files"].values() if all(sdcpp._dest(r2, f2).exists() for r2, f2, _ in o["files"].values())}
        SD.stop()
        for r, f, _ in e["files"].values():
            p = sdcpp._dest(r, f)
            if p.exists() and str(p) not in others:
                p.unlink()
                if not any(p.parent.iterdir()):
                    p.parent.rmdir()
        return
    if not item_id.startswith("path:"):
        raise RuntimeError("élément inconnu")
    home = paths.home().resolve()
    p = (home / item_id[5:]).resolve()
    if home not in p.parents:
        raise RuntimeError("chemin hors du dossier du launcher")
    if p in {Path(b).resolve().parent for b in (engine.current_bin(), sdcpp.current_bin()) if b}:
        raise RuntimeError("moteur en service : installez-en un autre d'abord")
    from .server import SERVER
    if SERVER.proc is not None and SERVER.proc.poll() is None and SERVER.model and \
            str(Path(SERVER.model.get("path", "")).resolve()).startswith(str(p)):
        raise RuntimeError("ce modèle est chargé dans llama-server : arrêtez le serveur d'abord")
    if str(p).startswith(str(music.app_dir().resolve())):
        MUSIC.stop()
    if str(p).startswith(str(paths.diffusion_dir().resolve())) or str(p).startswith(str(paths.engines_dir().resolve())):
        SD.stop()
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
        p.with_suffix(".json").unlink(missing_ok=True)   # métadonnées Civitai
