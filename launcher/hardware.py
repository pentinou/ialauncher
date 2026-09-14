"""Inventaire de la machine : GPU (VRAM), RAM, CPU, disque, OS. Tout passe par des
outils système déjà présents (nvidia-smi, /proc, sysctl, ctypes sous Windows) :
aucune dépendance Python. Les valeurs sont en octets.

Deux fonctions publiques :
    inventory()  — instantané complet (appelé au démarrage, puis à la demande)
    live()       — juste ce qui bouge (VRAM/RAM utilisées, charge GPU) pour les jauges
"""
import ctypes
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths

GiB = 1024 ** 3
MiB = 1024 ** 2


def _run(cmd, timeout=8):
    """Exécute une commande, renvoie stdout ou '' si absente / en erreur."""
    try:
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW : pas de console qui clignote
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _nvidia_smi():
    """Chemin de nvidia-smi : PATH, dossier WSL, ou emplacement Windows standard."""
    p = shutil.which("nvidia-smi")
    if p:
        return p
    for cand in ("/usr/lib/wsl/lib/nvidia-smi",
                 r"C:\Windows\System32\nvidia-smi.exe",
                 r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if os.path.exists(cand):
            return cand
    return None


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


_HOST_RAM = {"done": False, "total": 0}


def wsl_host_ram():
    """RAM totale du PC Windows vue depuis WSL (PowerShell via l'interop), en octets.
    0 si indisponible. Calculé une seule fois : PowerShell met ~2 s à démarrer."""
    if _HOST_RAM["done"]:
        return _HOST_RAM["total"]
    _HOST_RAM["done"] = True
    for ps in ("/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe", "powershell.exe"):
        out = _run([ps, "-NoProfile", "-c", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"], timeout=15)
        if out.strip().isdigit():
            _HOST_RAM["total"] = int(out.strip())
            break
    return _HOST_RAM["total"]


# ---------------------------------------------------------------- GPU

def gpus_nvidia():
    smi = _nvidia_smi()
    if not smi:
        return []
    out = _run([smi, "--query-gpu=index,name,memory.total,memory.used,memory.free,"
                     "utilization.gpu,temperature.gpu,driver_version,compute_cap",
               "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 9:
            continue
        try:
            gpus.append({
                "vendor": "nvidia",
                "index": int(f[0]),
                "name": f[1],
                "vram_total": int(float(f[2])) * MiB,
                "vram_used": int(float(f[3])) * MiB,
                "vram_free": int(float(f[4])) * MiB,
                "util": _int_or(f[5]),
                "temp": _int_or(f[6]),
                "driver": f[7],
                "compute_cap": f[8],
            })
        except ValueError:
            continue
    return gpus


def gpus_amd():
    """AMD sous Linux : sysfs suffit (pas besoin de rocm-smi)."""
    gpus = []
    for dev in sorted(Path("/sys/class/drm").glob("card[0-9]*/device")) if os.path.isdir("/sys/class/drm") else []:
        tot = dev / "mem_info_vram_total"
        if not tot.exists():
            continue
        try:
            total = int(tot.read_text())
            used = int((dev / "mem_info_vram_used").read_text()) if (dev / "mem_info_vram_used").exists() else 0
        except (OSError, ValueError):
            continue
        if total < 512 * MiB:  # iGPU sans VRAM dédiée : peu utile pour l'inférence
            continue
        name = "GPU AMD"
        try:
            name = (dev / "product_name").read_text().strip() or name
        except OSError:
            pass
        gpus.append({"vendor": "amd", "index": len(gpus), "name": name,
                     "vram_total": total, "vram_used": used, "vram_free": total - used,
                     "util": None, "temp": None, "driver": "", "compute_cap": ""})
    return gpus


def gpus_apple(ram_total):
    """Apple Silicon : mémoire unifiée. macOS laisse par défaut ~75 % de la RAM au GPU
    (ajustable via iogpu.wired_limit_mb). On expose ce plafond comme « VRAM »."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return []
    chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or "Apple Silicon"
    limit = int(ram_total * 0.75)
    return [{"vendor": "apple", "index": 0, "name": f"{chip} (mémoire unifiée)",
             "vram_total": limit, "vram_used": 0, "vram_free": limit,
             "util": None, "temp": None, "driver": "", "compute_cap": "", "unified": True}]


def _int_or(s, default=None):
    try:
        return int(float(s))
    except ValueError:
        return default


# ---------------------------------------------------------------- RAM

def ram():
    """(total, disponible) en octets."""
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullTotalPhys, st.ullAvailPhys
    if sys.platform == "darwin":
        total = _int_or(_run(["sysctl", "-n", "hw.memsize"]).strip(), 0)
        # vm_stat : pages libres + inactives ≈ disponibles
        vm = _run(["vm_stat"])
        page = 4096
        m = re.search(r"page size of (\d+)", vm)
        if m:
            page = int(m.group(1))
        free = 0
        for key in ("Pages free", "Pages inactive", "Pages speculative"):
            m = re.search(key + r":\s+(\d+)", vm)
            if m:
                free += int(m.group(1)) * page
        return total, free
    # Linux
    info = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            info[k.strip()] = int(v.strip().split()[0]) * 1024
    except (OSError, ValueError):
        return 0, 0
    return info.get("MemTotal", 0), info.get("MemAvailable", 0)


# ---------------------------------------------------------------- CPU

def cpu():
    name = ""
    if sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
        except OSError:
            name = platform.processor()
    elif sys.platform == "darwin":
        name = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    else:
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    threads = os.cpu_count() or 1
    cores = _physical_cores() or max(1, threads // 2)
    flags = ""
    try:
        if sys.platform == "linux":
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("flags"):
                    flags = line
                    break
    except OSError:
        pass
    return {"name": name or platform.processor() or "CPU", "threads": threads, "cores": cores,
            "avx2": " avx2 " in flags + " ", "avx512": " avx512f " in flags + " ",
            "arch": platform.machine()}


def _physical_cores():
    if sys.platform == "linux":
        out = _run(["lscpu"])
        sockets = cores = None
        for line in out.splitlines():
            if line.startswith("Core(s) per socket"):
                cores = _int_or(line.split(":")[1].strip())
            elif line.startswith("Socket(s)"):
                sockets = _int_or(line.split(":")[1].strip())
        if cores:
            return cores * (sockets or 1)
    elif sys.platform == "darwin":
        return _int_or(_run(["sysctl", "-n", "hw.physicalcpu"]).strip())
    return None


# ---------------------------------------------------------------- disque

def disk(path=None):
    p = Path(path) if path else paths.models_dir()
    try:
        u = shutil.disk_usage(p)
        return {"path": str(p), "total": u.total, "used": u.used, "free": u.free}
    except OSError:
        return {"path": str(p), "total": 0, "used": 0, "free": 0}


# ---------------------------------------------------------------- assemblage

def inventory():
    total, avail = ram()
    gpus = gpus_nvidia() or gpus_amd() or gpus_apple(total)
    osname = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(sys.platform, sys.platform)
    wsl = is_wsl()
    notes = []
    host_ram = wsl_host_ram() if wsl else 0
    if wsl:
        seen = f"{total / GiB:.0f} Go"
        if host_ram > total * 1.15:
            notes.append(f"WSL2 ne voit que {seen} de RAM sur les {host_ram / GiB:.0f} Go du PC. Pour les gros "
                         "modèles MoE (experts en RAM), créez %UserProfile%\\.wslconfig avec\n[wsl2]\n"
                         f"memory={int(host_ram / GiB * 0.8)}GB\npuis « wsl --shutdown ».")
        else:
            notes.append("Vous êtes dans WSL2 : la RAM affichée est celle allouée à WSL (clé memory= de "
                         "%UserProfile%\\.wslconfig).")
    if not gpus:
        notes.append("Aucun GPU dédié détecté : tout tournera sur le CPU. Les petits modèles "
                     "(≤ 4-8 milliards de paramètres, quantifiés en Q4) restent utilisables.")
    return {
        "os": osname, "wsl": wsl, "arch": platform.machine(),
        "python": platform.python_version(),
        "gpus": gpus,
        "ram": {"total": total, "available": avail, "host_total": host_ram},
        "cpu": cpu(),
        "disk": disk(),
        "notes": notes,
    }


def live():
    """Ce qui change en continu, pour rafraîchir les jauges sans tout re-détecter."""
    total, avail = ram()
    gpus = gpus_nvidia() or gpus_apple(total) or gpus_amd()
    return {"gpus": [{"index": g["index"], "vram_used": g["vram_used"], "vram_free": g["vram_free"],
                      "util": g["util"], "temp": g["temp"]} for g in gpus],
            "ram": {"total": total, "available": avail},
            "disk": disk()}


if __name__ == "__main__":
    import json
    print(json.dumps(inventory(), indent=1, ensure_ascii=False))
