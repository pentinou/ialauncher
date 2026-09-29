"""Emplacements sur disque. Tout vit sous un seul dossier (IALAUNCHER_HOME) :
    engines/   binaires llama.cpp (précompilés ou compilés ici)
    models/    fichiers .gguf téléchargés par le launcher
    diffusion/ modèles d'image et de vidéo (stable-diffusion.cpp)
    apps/      programmes installés à part (ACE-Step pour la musique)
    outputs/   images, vidéos et musiques générées
    presets/   configurations enregistrées (JSON)
    cache/     métadonnées GGUF déjà lues, catalogue HF…
    logs/      sortie de llama-server
"""
import os
import sys
from pathlib import Path


def home() -> Path:
    env = os.environ.get("IALAUNCHER_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ialauncher"
    return Path.home() / ".ialauncher"


def sub(name: str) -> Path:
    p = home() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def engines_dir() -> Path: return sub("engines")
def models_dir() -> Path: return sub("models")
def presets_dir() -> Path: return sub("presets")
def cache_dir() -> Path: return sub("cache")
def logs_dir() -> Path: return sub("logs")
def diffusion_dir() -> Path: return sub("diffusion")
def apps_dir() -> Path: return sub("apps")
def outputs_dir() -> Path: return sub("outputs")
def config_file() -> Path: return sub("") / "config.json"
