"""Téléchargement HTTP avec reprise, en bibliothèque standard uniquement."""
import os
import time
import urllib.request
import urllib.error

UA = "ialauncher/1.0 (+https://github.com/)"


def fetch_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        import json
        return json.loads(r.read().decode("utf-8"))


def fetch_text(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_range(url, start, end, timeout=60):
    """Octets [start, end] inclus (Range HTTP). Renvoie b'' si le serveur refuse."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status not in (200, 206):
            return b""
        return r.read()


def content_length(url, timeout=30):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return int(r.headers.get("Content-Length") or 0)


def download(url, dest, job=None, expected_size=None, label=""):
    """Télécharge url vers dest (fichier .part puis renommage). Reprend un .part
    existant. job.set() est appelé avec la progression ; job.cancel_requested arrête."""
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416:  # .part déjà complet
            os.replace(part, dest)
            return dest
        raise
    with resp:
        if have and resp.status != 206:   # le serveur ne reprend pas : on repart de zéro
            have = 0
            mode = "wb"
        else:
            mode = "ab" if have else "wb"
        total = expected_size or (have + int(resp.headers.get("Content-Length") or 0))
        done = have
        last = time.time()
        speed_ref = (time.time(), done)
        with open(part, mode) as f:
            while True:
                if job and job.cancel_requested:
                    return None
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if job and now - last > 0.3:
                    dt = now - speed_ref[0]
                    speed = (done - speed_ref[1]) / dt if dt > 0 else 0
                    if dt > 3:
                        speed_ref = (now, done)
                    job.set(done / total if total else 0,
                            f"{label} {done / 1e9:.2f} / {total / 1e9:.2f} Go · {speed / 1e6:.1f} Mo/s")
                    job.bytes_done = done
                    last = now
    if total and done < total:
        raise RuntimeError(f"téléchargement incomplet ({done} / {total} octets)")
    os.replace(part, dest)
    if job:
        job.set(1.0, f"{label} terminé")
    return dest
