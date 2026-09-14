"""Configurations enregistrées : un fichier JSON par preset (modèle + réglages)."""
import json
import re
import time

from . import paths


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "preset"


def list_all():
    out = []
    for p in sorted(paths.presets_dir().glob("*.json")):
        try:
            d = json.loads(p.read_text())
            d["id"] = p.stem
            out.append(d)
        except ValueError:
            continue
    return sorted(out, key=lambda d: d.get("updated", 0), reverse=True)


def save(name, model, cfg, preset_id=None):
    pid = preset_id or _slug(name)
    d = {"name": name, "model": model, "cfg": cfg, "updated": time.time()}
    (paths.presets_dir() / f"{pid}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
    d["id"] = pid
    return d


def delete(preset_id):
    p = paths.presets_dir() / f"{preset_id}.json"
    if p.exists():
        p.unlink()
