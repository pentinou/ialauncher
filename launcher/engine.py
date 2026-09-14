"""Le moteur = llama-server (projet ggml-org/llama.cpp). Trois façons de l'obtenir :
  • prebuilt : binaires officiels de la release stable (Windows CUDA/Vulkan/CPU,
               macOS Metal, Linux Vulkan/ROCm/CPU) — quelques minutes, sans outil.
  • build    : compilation locale avec CUDA (Linux + NVIDIA : il n'existe pas de
               binaire CUDA officiel pour Linux). Nécessite nvcc, cmake, un compilateur.
  • custom   : un llama-server déjà présent sur la machine.
"""
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from . import paths, download, hardware

RELEASES = "https://github.com/ggml-org/llama.cpp/releases"
API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
SRC_TARBALL = "https://github.com/ggml-org/llama.cpp/archive/refs/tags/{tag}.tar.gz"


# ---------------------------------------------------------------- config

def load_config():
    try:
        return json.loads(paths.config_file().read_text())
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    paths.config_file().write_text(json.dumps(cfg, indent=1, ensure_ascii=False))


def current_bin():
    """Binaire configuré, sinon le plus récent installé, sinon un llama-server du PATH."""
    cfg = load_config()
    b = cfg.get("engine_bin")
    if b and os.path.exists(b):
        return b
    found = installed_bins()
    if found:
        return found[-1]
    return shutil.which("llama-server")


def installed_bins():
    want = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    out = []
    for p in paths.engines_dir().rglob(want):
        if p.is_file():
            out.append(str(p))
    return sorted(out)


def set_bin(path):
    cfg = load_config()
    cfg["engine_bin"] = path
    save_config(cfg)


def env_for(bin_path):
    """Variables d'environnement pour que le binaire trouve ses bibliothèques."""
    env = dict(os.environ)
    d = str(Path(bin_path).parent)
    if sys.platform == "linux":
        env["LD_LIBRARY_PATH"] = d + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    elif sys.platform == "darwin":
        env["DYLD_LIBRARY_PATH"] = d + os.pathsep + env.get("DYLD_LIBRARY_PATH", "")
    return env


def run_bin(bin_path, args, timeout=30):
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000
    try:
        r = subprocess.run([bin_path, *args], capture_output=True, text=True, timeout=timeout,
                           env=env_for(bin_path), cwd=str(Path(bin_path).parent), **kw)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return f"erreur: {e}"


def version_of(bin_path):
    out = run_bin(bin_path, ["--version"], timeout=15)
    m = re.search(r"version:\s*(\S+)\s*\(build\s*(\d+)", out)
    if m:
        build = "b" + m.group(2)
        if m.group(2) == "0":  # compilé depuis l'archive source : pas de numéro git
            build = _marker(bin_path).get("tag", build)
        return f"{m.group(1)} ({build})"
    m = re.search(r"version:\s*(.+)", out)
    return m.group(1).strip() if m else out.strip()[:80]


def list_devices(bin_path):
    """Analyse « --list-devices » :
         CUDA0: NVIDIA GeForce RTX 3090 (24151 MiB, 23088 MiB free)
    Renvoie [{id, name, total, free}] (octets). Vide = CPU seulement."""
    out = run_bin(bin_path, ["--list-devices"], timeout=40)
    devs = []
    for line in out.splitlines():
        m = re.match(r"\s*(\w+?\d+):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)", line)
        if m:
            devs.append({"id": m.group(1), "name": m.group(2), "total": int(m.group(3)) * hardware.MiB,
                         "free": int(m.group(4)) * hardware.MiB})
    return devs


def _marker(bin_path):
    d = Path(bin_path).parent
    for cand in (d / "VERSION", d.parent / "VERSION"):
        try:
            return json.loads(cand.read_text())
        except (OSError, ValueError):
            continue
    return {}


def variant_of(bin_path):
    """Étiquette du variant lue dans le marqueur VERSION du dossier, sinon « personnalisé »."""
    return _marker(bin_path).get("label", "personnalisé")


def status():
    b = current_bin()
    st = {"bin": b, "installed": bool(b), "version": "", "variant": "", "devices": [],
          "plan": plan(), "installed_bins": installed_bins()}
    if b:
        st["version"] = version_of(b)
        st["variant"] = variant_of(b)
        st["devices"] = list_devices(b)
    return st


# ---------------------------------------------------------------- choix du variant

def stable_tag():
    """Tag de la release stable : le fichier nightly-tag.txt de « latest » le donne.
    Repli : la prérelease la plus récente qui a des binaires."""
    try:
        t = download.fetch_text(f"{RELEASES}/latest/download/nightly-tag.txt").strip()
        if re.fullmatch(r"b\d+", t):
            return t
    except Exception:
        pass
    rels = download.fetch_json(f"{API}?per_page=10")
    for r in rels:
        if r.get("assets"):
            return r["tag_name"]
    raise RuntimeError("impossible de déterminer la dernière release de llama.cpp")


def driver_cuda_version():
    smi = hardware._nvidia_smi()
    if not smi:
        return 0.0
    m = re.search(r"CUDA (?:UMD )?Version:\s*([0-9]+\.[0-9]+)", hardware._run([smi]))
    return float(m.group(1)) if m else 0.0


def toolchain():
    """Outils de compilation présents (pour la voie « build »)."""
    nvcc = shutil.which("nvcc") or next((p for p in ("/usr/local/cuda/bin/nvcc",) if os.path.exists(p)), None)
    return {"nvcc": nvcc, "cmake": shutil.which("cmake"),
            "cc": shutil.which("g++") or shutil.which("clang++") or shutil.which("cl"),
            "make": shutil.which("make") or shutil.which("ninja")}


def plan():
    """Propose la meilleure façon d'installer le moteur SUR CETTE MACHINE, avec la
    raison. Renvoie {method, variant, label, reason, alternatives}."""
    hw = hardware.inventory()
    nvidia = any(g["vendor"] == "nvidia" for g in hw["gpus"])
    amd = any(g["vendor"] == "amd" for g in hw["gpus"])
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    osn = sys.platform
    alts = []
    if osn == "win32":
        if nvidia:
            drv = driver_cuda_version()
            cuda = "13.3" if (drv == 0 or drv >= 13.3) else "12.4"
            if arch == "arm64":
                cuda = "13.4"
            main = {"method": "prebuilt", "variant": f"win-cuda-{cuda}-{arch}",
                    "label": f"CUDA {cuda} (Windows {arch})",
                    "reason": "GPU NVIDIA détecté : le binaire CUDA officiel est le plus rapide."}
            alts.append({"method": "prebuilt", "variant": f"win-vulkan-{arch}", "label": "Vulkan (Windows)"})
        elif arch == "arm64":
            main = {"method": "prebuilt", "variant": "win-cpu-arm64", "label": "CPU (Windows arm64)",
                    "reason": "Pas de GPU dédié reconnu."}
        else:
            main = {"method": "prebuilt", "variant": "win-vulkan-x64", "label": "Vulkan (Windows)",
                    "reason": "Vulkan fonctionne avec les GPU AMD, Intel et NVIDIA sans SDK propriétaire."}
            alts.append({"method": "prebuilt", "variant": "win-cpu-x64", "label": "CPU (Windows)"})
    elif osn == "darwin":
        main = {"method": "prebuilt", "variant": f"macos-{arch}", "label": f"Metal (macOS {arch})",
                "reason": "Sur Mac, le binaire officiel utilise Metal et la mémoire unifiée."}
    else:
        tc = toolchain()
        can_build = bool(tc["nvcc"] and tc["cmake"] and tc["cc"])
        if nvidia and can_build:
            main = {"method": "build", "variant": "cuda-local", "label": "CUDA (compilé ici)",
                    "reason": "GPU NVIDIA sous Linux : il n'existe pas de binaire CUDA officiel, mais "
                              "nvcc + cmake sont présents, donc on compile (≈ 5-15 min, une seule fois)."}
            alts.append({"method": "prebuilt", "variant": f"ubuntu-vulkan-{arch}", "label": "Vulkan (Linux)"})
        elif nvidia:
            main = {"method": "prebuilt", "variant": f"ubuntu-vulkan-{arch}", "label": "Vulkan (Linux)",
                    "reason": "GPU NVIDIA mais pas de nvcc/cmake : on prend le binaire Vulkan officiel. "
                              "Pour du CUDA natif (plus rapide), installez le CUDA Toolkit puis choisissez « compiler »."}
            if hw["wsl"]:
                main["reason"] += " ATTENTION : sous WSL2, Vulkan ne voit généralement pas le GPU ; " \
                                  "installez le CUDA Toolkit (apt install cuda-toolkit) et compilez."
            alts.append({"method": "build", "variant": "cuda-local", "label": "CUDA (compiler — outils manquants)"})
        elif amd:
            main = {"method": "prebuilt", "variant": f"ubuntu-rocm-", "label": "ROCm (Linux)",
                    "reason": "GPU AMD détecté : binaire ROCm officiel (le runtime ROCm doit être installé)."}
            alts.append({"method": "prebuilt", "variant": f"ubuntu-vulkan-{arch}", "label": "Vulkan (Linux)"})
        elif shutil.which("vulkaninfo"):
            main = {"method": "prebuilt", "variant": f"ubuntu-vulkan-{arch}", "label": "Vulkan (Linux)",
                    "reason": "Pas de GPU NVIDIA/AMD identifié, mais Vulkan est présent."}
        else:
            main = {"method": "prebuilt", "variant": f"ubuntu-{arch}", "label": "CPU (Linux)",
                    "reason": "Aucun GPU exploitable détecté : binaire CPU."}
        alts.append({"method": "prebuilt", "variant": f"ubuntu-{arch}", "label": "CPU (Linux)"})
    main["alternatives"] = [a for a in alts if a["variant"] != main["variant"]]
    main["toolchain"] = toolchain()
    return main


# ---------------------------------------------------------------- installation prebuilt

def _pick_asset(assets, variant):
    """Asset principal + éventuel cudart (Windows CUDA) pour un variant."""
    names = {a["name"]: a for a in assets}
    main = None
    for n, a in names.items():
        if n.startswith("cudart"):
            continue
        if n.startswith("llama-") and f"-bin-{variant}" in n:
            main = a
            break
    if main is None:  # rocm : version dans le nom → préfixe
        for n, a in names.items():
            if n.startswith("llama-") and f"-bin-{variant}" in n and not n.startswith("cudart"):
                main = a
                break
    if main is None:
        raise RuntimeError(f"aucun binaire « {variant} » dans cette release")
    cudart = None
    m = re.search(r"win-cuda-([\d.]+)-(\w+)", variant)
    if m:
        cudart = names.get(f"cudart-llama-bin-win-cuda-{m.group(1)}-{m.group(2)}.zip")
    return main, cudart


def _extract(archive, dest):
    dest.mkdir(parents=True, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            t.extractall(dest)  # noqa: S202 — archive officielle
    # droits d'exécution (les zip n'en ont pas)
    for p in dest.rglob("*"):
        if p.is_file() and (p.suffix == "" or p.suffix == ".exe" or ".so" in p.name or p.suffix == ".dylib"):
            try:
                p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass


def _find_server(root):
    want = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    for p in root.rglob(want):
        if p.is_file():
            return p
    raise RuntimeError("llama-server introuvable dans l'archive")


def install_prebuilt(job, variant, label):
    job.set(0.02, "recherche de la dernière release stable…")
    tag = stable_tag()
    rel = download.fetch_json(f"{API}/tags/{tag}")
    main, cudart = _pick_asset(rel.get("assets", []), variant)
    job.logline(f"release {tag} — {main['name']}")
    target = paths.engines_dir() / f"llama-{tag}-{variant}"
    if target.exists():
        shutil.rmtree(target)
    tmp = paths.cache_dir() / main["name"]
    download.download(main["browser_download_url"], str(tmp), job, main.get("size"), "binaires")
    if job.cancel_requested:
        return None
    job.set(0.85, "extraction…")
    _extract(str(tmp), target)
    tmp.unlink(missing_ok=True)
    server = _find_server(target)
    if cudart:
        job.set(0.86, "runtime CUDA…")
        tmp2 = paths.cache_dir() / cudart["name"]
        download.download(cudart["browser_download_url"], str(tmp2), job, cudart.get("size"), "runtime CUDA")
        _extract(str(tmp2), server.parent)  # les DLL doivent être à côté de l'exe
        tmp2.unlink(missing_ok=True)
    (target / "VERSION").write_text(json.dumps({"tag": tag, "variant": variant, "label": label}))
    set_bin(str(server))
    job.set(1.0, f"installé : {label} ({tag})")
    return {"bin": str(server), "tag": tag, "label": label}


# ---------------------------------------------------------------- compilation CUDA

def build_cuda(job):
    tc = toolchain()
    if not (tc["nvcc"] and tc["cmake"] and tc["cc"]):
        raise RuntimeError("outils manquants : il faut nvcc (CUDA Toolkit), cmake et g++/clang++")
    job.set(0.02, "recherche de la dernière release stable…")
    tag = stable_tag()
    src_tgz = paths.cache_dir() / f"llama.cpp-{tag}.tar.gz"
    src_dir = paths.cache_dir() / f"llama.cpp-{tag}"
    if not src_dir.exists():
        download.download(SRC_TARBALL.format(tag=tag), str(src_tgz), job, None, "sources")
        if job.cancel_requested:
            return None
        job.set(0.1, "extraction des sources…")
        with tarfile.open(src_tgz) as t:
            t.extractall(paths.cache_dir())  # noqa: S202
        src_tgz.unlink(missing_ok=True)
    build_dir = src_dir / "build"
    env = dict(os.environ)
    env["PATH"] = str(Path(tc["nvcc"]).parent) + os.pathsep + env.get("PATH", "")
    cfg = [tc["cmake"], "-S", str(src_dir), "-B", str(build_dir),
           "-DCMAKE_BUILD_TYPE=Release", "-DGGML_CUDA=ON", "-DGGML_NATIVE=ON",
           "-DCMAKE_CUDA_ARCHITECTURES=native", "-DLLAMA_BUILD_TESTS=OFF",
           "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_TOOLS=ON", "-DLLAMA_BUILD_SERVER=ON",
           "-DLLAMA_CURL=OFF", "-DBUILD_SHARED_LIBS=OFF"]
    job.set(0.12, "configuration cmake…")
    _stream(cfg, env, job, 0.12, 0.2)
    if job.cancel_requested:
        return None
    jobs_n = max(1, (os.cpu_count() or 2) - 1)
    job.set(0.2, f"compilation (-j{jobs_n})… c'est long, patience")
    # llama-fit-params : le « second avis » mémoire de l'interface (même moteur, même règles)
    _stream([tc["cmake"], "--build", str(build_dir), "--config", "Release", "-j", str(jobs_n),
             "--target", "llama-server", "llama-fit-params"], env, job, 0.2, 0.97)
    if job.cancel_requested:
        return None
    exe = build_dir / "bin" / "llama-server"
    if not exe.exists():
        raise RuntimeError("compilation terminée mais llama-server introuvable")
    target = paths.engines_dir() / f"llama-{tag}-cuda-local"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for p in (build_dir / "bin").iterdir():
        if p.is_file():
            shutil.copy2(p, target / p.name)
    (target / "VERSION").write_text(json.dumps({"tag": tag, "variant": "cuda-local", "label": "CUDA (compilé ici)"}))
    shutil.rmtree(src_dir, ignore_errors=True)  # sources + objets (≈ 0,5 Go) : inutiles une fois copié
    set_bin(str(target / "llama-server"))
    job.set(1.0, f"compilé : CUDA ({tag})")
    return {"bin": str(target / "llama-server"), "tag": tag, "label": "CUDA (compilé ici)"}


def _stream(cmd, env, job, p0, p1):
    """Exécute cmd en remontant les lignes dans job.log ; la progression avance
    d'après les « [ 42%] » de cmake."""
    job.logline("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    for line in proc.stdout:
        job.logline(line)
        m = re.search(r"\[\s*(\d+)%\]", line)
        if m:
            job.set(p0 + (p1 - p0) * int(m.group(1)) / 100)
        if job.cancel_requested:
            proc.terminate()
            break
    proc.wait()
    if proc.returncode not in (0, None) and not job.cancel_requested:
        raise RuntimeError(f"échec ({' '.join(cmd[:2])} → code {proc.returncode}) — voir le journal")


def install(job, method, variant, label):
    if method == "build":
        return build_cuda(job)
    return install_prebuilt(job, variant, label)
