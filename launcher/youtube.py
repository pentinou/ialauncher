"""Résumé de vidéos YouTube : récupération du texte prononcé.

yt-dlp (version « zipapp » officielle : un seul fichier, lancé avec le Python du
launcher) lit les métadonnées et la liste des sous-titres ; YouTube exige un
moteur JavaScript pour ses défis, d'où Deno (binaire unique). Les deux vivent dans
engines/youtube/ et se mettent à jour en les retéléchargeant.
"""
import hashlib
import json
import platform
import os
import re
import shutil
import subprocess
import tarfile
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import paths, download, engine

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-{}.zip"
# Transcription des vidéos sans sous-titres : whisper.cpp (même famille ggml que llama.cpp),
# compilé ici (pas de binaire Linux publié), modèle large-v3-turbo quantifié q5_0 (~1 Go de VRAM).
WHISPER_API = "https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest"
WHISPER_SRC = "https://github.com/ggml-org/whisper.cpp/archive/refs/tags/{}.tar.gz"
WHISPER_MODEL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin"
# ffmpeg (audio YouTube → WAV 16 kHz) : celui du système s'il existe, sinon une version autonome.
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-{}"
SPONSORBLOCK = "https://sponsor.ajay.app/api/skipSegments/{}?categories={}"
# Plages retirées avant le résumé : sponsor, autopromotion, « abonnez-vous », intro, générique
# de fin, aperçu de la suite, digression, musique hors sujet.
SKIP = ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "filler", "music_offtopic"]


def tools_dir():
    d = paths.engines_dir() / "youtube"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ytdlp_path():
    return tools_dir() / "yt-dlp"


def deno_path():
    return tools_dir() / ("deno.exe" if sys.platform == "win32" else "deno")


def _deno_triple():
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin":
        return "aarch64-apple-darwin" if arm else "x86_64-apple-darwin"
    return "aarch64-unknown-linux-gnu" if arm else "x86_64-unknown-linux-gnu"


def whisper_dir():
    return tools_dir() / "whisper"


def whisper_cli():
    return whisper_dir() / ("whisper-cli.exe" if sys.platform == "win32" else "whisper-cli")


def whisper_model():
    return whisper_dir() / WHISPER_MODEL.rsplit("/", 1)[1]


def ffmpeg_bin():
    own = tools_dir() / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    return str(own) if own.is_file() else shutil.which("ffmpeg")


def status():
    return {"ytdlp": ytdlp_path().is_file(), "deno": deno_path().is_file(),
            "whisper": whisper_cli().is_file() and whisper_model().is_file() and bool(ffmpeg_bin()),
            "nvcc": bool(engine.toolchain()["nvcc"])}


def versions():
    """Versions installées (« » si absent) : pour l'onglet Mises à jour."""
    def out(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.split()
        except OSError:
            return []
    v = {"ytdlp": "", "deno": "", "whisper": ""}
    if ytdlp_path().is_file():
        v["ytdlp"] = (out([sys.executable, str(ytdlp_path()), "--version"]) or [""])[0]
    if deno_path().is_file():
        v["deno"] = (out([str(deno_path()), "--version"]) + ["", ""])[1]   # « deno 2.9.8 (stable…) »
    if whisper_cli().is_file():
        f = whisper_dir() / "VERSION"
        v["whisper"] = f.read_text().strip() if f.is_file() else "version inconnue"
    return v


def install(job):
    """Télécharge (ou remplace par la dernière version) yt-dlp et Deno."""
    job.set(0.02, "yt-dlp…")
    download.download(YTDLP_URL, str(ytdlp_path()), job, None, "yt-dlp")
    if job.cancel_requested:
        return None
    job.set(0.2, "Deno…")
    z = paths.cache_dir() / "deno.zip"
    download.download(DENO_URL.format(_deno_triple()), str(z), job, None, "Deno")
    if job.cancel_requested:
        return None
    new = tools_dir() / "deno-nouveau"
    engine._extract(str(z), new)
    os.replace(new / deno_path().name, deno_path())   # pas de réécriture d'un binaire en cours d'exécution
    shutil.rmtree(new, ignore_errors=True)
    z.unlink(missing_ok=True)
    job.set(1.0, "yt-dlp et Deno installés")
    return status()


def install_whisper(job):
    """Compile whisper-cli (CUDA si nvcc est là), télécharge le modèle et, au besoin, ffmpeg."""
    tc = engine.toolchain()
    if not (tc["cmake"] and tc["cc"]):
        raise RuntimeError("il faut cmake et un compilateur C++ (g++ ou clang++) pour compiler whisper.cpp")
    job.set(0.02, "recherche de la dernière version de whisper.cpp…")
    tag = download.fetch_json(WHISPER_API)["tag_name"]
    src_dir = paths.cache_dir() / f"whisper.cpp-{tag.lstrip('v')}"
    if not src_dir.exists():
        tgz = paths.cache_dir() / f"whisper.cpp-{tag}.tar.gz"
        download.download(WHISPER_SRC.format(tag), str(tgz), job, None, "sources")
        with tarfile.open(tgz) as t:
            t.extractall(paths.cache_dir())  # noqa: S202 — archive officielle
        tgz.unlink(missing_ok=True)
    if job.cancel_requested:
        return None
    env = dict(os.environ)
    cfg = [tc["cmake"], "-S", str(src_dir), "-B", str(src_dir / "build"), "-DCMAKE_BUILD_TYPE=Release",
           "-DBUILD_SHARED_LIBS=OFF", "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_SDL2=OFF"]
    if tc["nvcc"]:
        env["PATH"] = str(Path(tc["nvcc"]).parent) + os.pathsep + env.get("PATH", "")
        cfg += ["-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=native"]
    job.set(0.05, "configuration cmake…")
    engine._stream(cfg, env, job, 0.05, 0.08)
    n = max(1, (os.cpu_count() or 2) - 1)
    job.set(0.08, f"compilation de whisper.cpp{' (CUDA)' if tc['nvcc'] else ''} (-j{n})… plusieurs minutes")
    engine._stream([tc["cmake"], "--build", str(src_dir / "build"), "--config", "Release", "-j", str(n),
                    "--target", "whisper-cli"], env, job, 0.08, 0.6)
    if job.cancel_requested:
        return None
    built = next((p for p in (src_dir / "build").rglob(whisper_cli().name) if p.is_file()), None)
    if not built:
        raise RuntimeError("compilation terminée mais whisper-cli introuvable")
    whisper_dir().mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, whisper_dir() / "whisper-cli.nouveau")   # remplacement d'un coup, comme Deno
    os.replace(whisper_dir() / "whisper-cli.nouveau", whisper_cli())
    shutil.rmtree(src_dir, ignore_errors=True)
    if not whisper_model().is_file():
        job.set(0.6, "modèle large-v3-turbo…")
        download.download(WHISPER_MODEL, str(whisper_model()), job, None, "modèle Whisper")
        if job.cancel_requested:
            return None
    if not ffmpeg_bin():
        _install_ffmpeg(job)
    (whisper_dir() / "VERSION").write_text(tag)
    job.set(1.0, f"Whisper installé ({tag}{', CUDA' if tc['nvcc'] else ', processeur seul'})")
    return status()


def _install_ffmpeg(job):
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        name = "win64-gpl.zip"
    elif sys.platform.startswith("linux"):
        name = "linuxarm64-gpl.tar.xz" if arm else "linux64-gpl.tar.xz"
    else:
        raise RuntimeError("ffmpeg introuvable : installez-le (macOS : brew install ffmpeg)")
    arc = paths.cache_dir() / f"ffmpeg-{name}"
    download.download(FFMPEG_URL.format(name), str(arc), job, None, "ffmpeg")
    tmp = paths.cache_dir() / "ffmpeg-extrait"
    engine._extract(str(arc), tmp)
    exe = next(p for p in tmp.rglob("ffmpeg*") if p.is_file() and p.name in ("ffmpeg", "ffmpeg.exe"))
    shutil.copy2(exe, tools_dir() / exe.name)
    shutil.rmtree(tmp, ignore_errors=True)
    arc.unlink(missing_ok=True)


def _ytdlp(args, timeout=120):
    if not ytdlp_path().is_file() or not deno_path().is_file():
        raise RuntimeError("yt-dlp et Deno ne sont pas installés")
    cmd = [sys.executable, str(ytdlp_path()), "--no-warnings", "--no-playlist",
           "--js-runtimes", f"deno:{deno_path()}", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        err = [line for line in r.stderr.splitlines() if "ERROR" in line] or r.stderr.splitlines()[-1:]
        raise RuntimeError("yt-dlp : " + (err[0] if err else f"code {r.returncode}"))
    return r.stdout


def _original_lang(info):
    """Langue parlée dans la vidéo. Vidéo doublée automatiquement par YouTube :
    `language` est celle du doublage ; la piste audio d'origine est marquée
    « original » dans format_note. Repli : les sous-titres automatiques d'origine
    (suffixe -orig ; les autres sont des traductions)."""
    dub = next((f.get("language") for f in info.get("formats") or []
                if "original" in (f.get("format_note") or "") and f.get("language")), None)
    if dub or info.get("language"):
        return dub or info["language"]
    auto_orig = [k for k in info.get("automatic_captions") or {} if k.endswith("-orig")]
    return auto_orig[0][:-len("-orig")] if auto_orig else ""


def _pick_track(info, orig):
    """Sous-titres écrits par l'auteur, jamais ceux que YouTube génère (trop d'erreurs :
    Whisper prendra le relais). Vidéo en français ou en anglais : ceux de sa langue. Autre
    langue : la traduction française de l'auteur, sinon l'anglaise, sinon ceux de la langue
    d'origine. Renvoie la clé yt-dlp de la piste, ou None."""
    manual = info.get("subtitles") or {}
    base = orig.split("-")[0]
    for lang in ([base] if base in ("fr", "en") else ["fr", "en", base]):
        for k in manual:
            if lang and k.split("-")[0] == lang:
                return k
    return None


def _parse_json3(data):
    """Événements json3 → [[secondes, texte]], une entrée par ligne affichée."""
    out = []
    for ev in data.get("events", []):
        if ev.get("aAppend") or "segs" not in ev:
            continue
        text = "".join(s.get("utf8", "") for s in ev["segs"]).replace("\n", " ").strip()
        if text and (not out or out[-1][1] != text):
            out.append([round(ev.get("tStartMs", 0) / 1000, 1), text])
    return out


def fetch(url):
    """Métadonnées et sous-titres de l'auteur d'une vidéo. `transcript` vaut [] s'il n'y en a pas."""
    info = json.loads(_ytdlp(["-J", "--skip-download", url]))
    orig = _original_lang(info)
    meta = {k: info.get(k) for k in ("id", "title", "channel", "duration", "webpage_url")}
    meta["language"] = orig
    meta["chapters"] = [{"start": c.get("start_time"), "title": c.get("title")} for c in info.get("chapters") or []]
    meta["subs"] = None
    transcript = []
    key = _pick_track(info, orig)
    if key:
        # Le fichier passe par yt-dlp : l'URL brute des métadonnées répond vite 429.
        with tempfile.TemporaryDirectory() as tmp:
            _ytdlp(["--skip-download", "--write-subs", "--sub-langs", key, "--sub-format", "json3", "-o", str(Path(tmp) / "v"), info["webpage_url"]])
            f = Path(tmp) / f"v.{key}.json3"
            if f.is_file():
                transcript = _parse_json3(json.loads(f.read_text(encoding="utf-8")))
        meta["subs"] = {"lang": key, "kind": "auteur"}
        meta["language"] = meta["language"] or meta["subs"]["lang"]
    return {"meta": meta, "transcript": transcript}


SYSTEM = """Tu résumes des vidéos YouTube pour quelqu'un qui ne veut pas perdre de temps à les regarder.
Tu reçois le titre, la transcription (sous-titres de l'auteur, ou transcription automatique : fautes et noms propres écorchés possibles), avec des repères [mm:ss] et, s'il y en a, les chapitres marqués « ## ».

Règles :
- Chaque point a une « phrase » qui donne l'information elle-même : la réponse, la conclusion, le conseil, le chiffre clé. Jamais le simple sujet. Une seule ligne : 25 mots au plus.
  Mauvais : « La vidéo parle de pâte à pizza. » Bon : « Une pâte laissée 48 h au frigo a plus de goût qu'une pâte levée 2 h à température ambiante. »
- « detail » : 4 à 6 phrases qui expliquent le pourquoi et le comment, avec les chiffres, noms et exemples importants de la vidéo.
- Un seul point par défaut. Plusieurs points (5 au maximum) seulement si la vidéo traite de sujets indépendants (revue d'actualité, plusieurs nouvelles sans lien) : un point par sujet, dans l'ordre de la vidéo, sans mélanger deux sujets dans un même point. Un tutoriel, un test ou un comparatif reste un seul point, même s'il a des chapitres.
- Ignore les sponsors, les demandes d'abonnement, les introductions et les anecdotes sans rapport.
- N'invente rien qui ne soit pas dans la transcription. Si la vidéo ne donne pas vraiment de réponse, dis-le dans la phrase.
- Réponds toujours en {langue}, même si la vidéo est dans une autre langue ; garde les termes techniques usuels."""

SCHEMA = {"type": "object", "required": ["points"], "properties": {"points": {
    "type": "array", "minItems": 1, "maxItems": 5, "items": {
        "type": "object", "required": ["phrase", "detail"],
        "properties": {"phrase": {"type": "string"}, "detail": {"type": "string"}}}}}}


def _prompt(data):
    """Titre + transcription en texte suivi : un titre « ## » à chaque chapitre et un
    repère [mm:ss] environ toutes les 30 s (pour pouvoir citer un moment)."""
    meta = data["meta"]
    chapters = sorted(meta["chapters"], key=lambda c: c["start"] or 0)
    parts, i, last = [], 0, -30
    for start, text in data["transcript"]:
        while i < len(chapters) and (chapters[i]["start"] or 0) <= start:
            parts.append(f"\n\n## {chapters[i]['title']}")
            i += 1
        if start - last >= 30:
            parts.append(f"\n[{int(start) // 60:02d}:{int(start) % 60:02d}] ")
            last = start
        parts.append(text + " ")
    return f"Titre : {meta['title']}\nChaîne : {meta.get('channel') or '?'}\n\nTranscription :{''.join(parts)}"


# Langue des résumés et des réponses (réglage du launcher, commun à tous les navigateurs) :
# le modèle traduit en résumant, quelle que soit la langue de la vidéo.
LANGS = {"fr": "français", "en": "anglais", "es": "espagnol", "de": "allemand", "it": "italien", "pt": "portugais"}


def summary_lang():
    code = engine.load_config().get("youtube_lang", "fr")
    return code if code in LANGS else "fr"


def set_summary_lang(code):
    if code not in LANGS:
        raise ValueError(f"langue inconnue : {code}")
    cfg = engine.load_config()
    cfg["youtube_lang"] = code
    engine.save_config(cfg)


def _in_lang(prompt):
    return prompt.replace("{langue}", LANGS[summary_lang()])


def _chat(base_url, body, timeout):
    body = {"temperature": 0.3, "chat_template_kwargs": {"enable_thinking": False}, **body}
    req = urllib.request.Request(f"{base_url}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rep = json.loads(r.read().decode("utf-8"))
    return rep["choices"][0]["message"]["content"], {"usage": rep.get("usage"), "timings": rep.get("timings")}


def summarize(data, base_url, timeout=600):
    """Résumé par le llama-server de `base_url` : {"points": [{"phrase", "detail"}]}."""
    text, stats = _chat(base_url, {
        "messages": [{"role": "system", "content": _in_lang(SYSTEM)}, {"role": "user", "content": _prompt(data)}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "resume", "schema": SCHEMA}}}, timeout)
    return {**json.loads(text), **stats}


SYSTEM_QUESTION = """Tu réponds aux questions d'une personne sur une vidéo YouTube qu'elle n'a pas regardée.
Tu disposes de sa transcription (sous-titres de l'auteur, ou transcription automatique : fautes et noms propres écorchés possibles).
Réponds en {langue}, brièvement, en t'appuyant uniquement sur la transcription ; si la vidéo n'aborde pas la question, dis-le.
Quand c'est utile, indique le moment de la vidéo (mm:ss) où le passage se trouve.
Écris en texte simple, sans Markdown (ni **, ni #) ; des tirets pour une liste si besoin."""


def ask(data, history, question, base_url, timeout=600):
    """Question libre sur la vidéo. `history` : [{"role": "user"|"assistant", "content"}] des
    échanges précédents. Le début du message (consignes + transcription) ne change pas d'une
    question à l'autre : llama-server le garde en cache et ne relit que la fin."""
    messages = [{"role": "system", "content": _in_lang(SYSTEM_QUESTION) + "\n\n" + _prompt(data)},
                *history, {"role": "user", "content": question}]
    text, stats = _chat(base_url, {"messages": messages}, timeout)
    return {"answer": text, **stats}


# ---------------------------------------------------------------- résumés enregistrés
# Un fichier par vidéo dans <home>/youtube/<id>.json : métadonnées, transcription,
# résumé et questions posées. Revenir sur une vidéo ne coûte rien.

def video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{11})", url) or re.fullmatch(r"\s*([\w-]{11})\s*", url)
    return m.group(1) if m else None


def _file(vid, ext="json"):
    # l'identifiant arrive de l'URL : 11 caractères YouTube, rien d'autre (pas de « ../ »)
    if not re.fullmatch(r"[\w-]{11}", vid or ""):
        raise ValueError(f"identifiant de vidéo invalide : {vid!r}")
    return paths.sub("youtube") / f"{vid}.{ext}"


def load(vid):
    try:
        return json.loads(_file(vid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(rec):
    _file(rec["meta"]["id"]).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def thumb(vid):
    """Miniature de la vidéo (320 × 180), téléchargée une fois puis gardée à côté de son résumé."""
    f = _file(vid, "jpg")
    if not f.is_file():
        download.download(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", str(f))
    return f.read_bytes()


def delete(ids):
    """Supprime résumés et miniatures ; renvoie le nombre de vidéos supprimées."""
    n = 0
    for vid in ids:
        f = _file(vid)
        if f.is_file():
            n += 1
        f.unlink(missing_ok=True)
        _file(vid, "jpg").unlink(missing_ok=True)
    return n


def public(rec):
    """Ce que l'interface affiche : tout sauf la transcription (lourde), dont on donne la taille."""
    return {**{k: v for k, v in rec.items() if k != "transcript"},
            "words": sum(len(t.split()) for _, t in rec["transcript"])}


def history():
    out = []
    for f in paths.sub("youtube").glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m = rec["meta"]
        out.append({"id": m["id"], "title": m["title"], "channel": m.get("channel"), "duration": m.get("duration"),
                    "at": rec.get("at", 0), "phrase": (rec.get("summary") or {}).get("points", [{}])[0].get("phrase", "")})
    return sorted(out, key=lambda x: x["at"], reverse=True)


def _skip_segments(vid):
    """Plages [début, fin] à ignorer d'après SponsorBlock (saisies par la communauté). Seuls
    les 4 premiers caractères du SHA-256 de l'identifiant partent : le serveur renvoie toutes
    les vidéos de ce préfixe. [] si la vidéo n'a pas de données ou si l'API ne répond pas."""
    prefix = hashlib.sha256(vid.encode()).hexdigest()[:4]
    try:
        d = download.fetch_json(SPONSORBLOCK.format(prefix, urllib.parse.quote(json.dumps(SKIP))), timeout=10)
    except (OSError, ValueError):   # 404 = aucune donnée ; réseau ou API en panne : on s'en passe
        return []
    return sorted(s["segment"] for v in d if v.get("videoID") == vid for s in v.get("segments", []))


def transcribe(job, data, p0, p1):
    """Audio de la vidéo → WAV 16 kHz mono → whisper-cli. Renvoie ([[secondes, texte]], langue)."""
    with tempfile.TemporaryDirectory() as tmp:
        job.set(p0, "téléchargement de l'audio…")
        # piste d'origine si la vidéo est doublée automatiquement
        _ytdlp(["-f", "ba[format_note*=original]/ba", "-o", str(Path(tmp) / "audio.%(ext)s"),
                data["meta"]["webpage_url"]], timeout=900)
        src = next(Path(tmp).glob("audio.*"))
        wav = Path(tmp) / "audio.wav"
        subprocess.run([ffmpeg_bin(), "-nostdin", "-loglevel", "error", "-i", str(src),
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                       check=True, capture_output=True, timeout=900)
        lang = (data["meta"].get("language") or "auto").split("-")[0]
        cmd = [str(whisper_cli()), "-m", str(whisper_model()), "-f", str(wav), "-l", lang,
               "-oj", "-of", str(Path(tmp) / "out"), "-pp", "-np"]
        job.logline("$ " + " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace")
        for line in proc.stderr:   # « whisper_print_progress_callback: progress =  42% »
            m = re.search(r"progress\s*=\s*(\d+)%", line)
            if m:
                job.set(p0 + (p1 - p0) * int(m.group(1)) / 100, f"transcription par Whisper : {m.group(1)} %")
            else:
                job.logline(line)
            if job.cancel_requested:
                proc.terminate()
                break
        proc.wait()
        if proc.returncode != 0 and not job.cancel_requested:
            raise RuntimeError(f"whisper-cli a échoué (code {proc.returncode}) — voir le journal de la tâche")
        out = json.loads((Path(tmp) / "out.json").read_text(encoding="utf-8", errors="replace"))
    lines = [[round(seg["offsets"]["from"] / 1000, 1), seg["text"].strip()]
             for seg in out.get("transcription", []) if seg["text"].strip()]
    return lines, out.get("result", {}).get("language") or lang


def _drop_skipped(data):
    """Retire de la transcription les lignes qui commencent dans une plage SponsorBlock."""
    segs = _skip_segments(data["meta"]["id"])
    if not segs:
        return
    seconds, end = 0, 0   # durée de l'union des plages (elles se chevauchent souvent)
    for a, b in segs:
        seconds += max(0, b - max(a, end))
        end = max(end, b)
    kept = [x for x in data["transcript"] if not any(a <= x[0] < b for a, b in segs)]
    data["meta"]["skipped"] = {"seconds": round(seconds), "lines": len(data["transcript"]) - len(kept)}
    data["transcript"] = kept


def run(job, url, base_url):
    """Tâche « Résumer » : sous-titres de l'auteur, sinon transcription par Whisper, puis
    résumé, enregistrés ensemble."""
    job.set(0.05, "lecture de la vidéo et de ses sous-titres…")
    data = fetch(url)
    if not data["transcript"]:
        if not status()["whisper"]:
            raise RuntimeError("cette vidéo n'a pas de sous-titres écrits par son auteur : installez Whisper (bouton en haut de l'onglet) pour la transcrire")
        data["transcript"], lang = transcribe(job, data, 0.1, 0.6)
        data["meta"]["subs"] = {"lang": lang, "kind": "whisper"}
        data["meta"]["language"] = data["meta"]["language"] or lang
    _drop_skipped(data)
    words = sum(len(t.split()) for _, t in data["transcript"])
    job.set(max(job.progress, 0.6) if data["meta"]["subs"]["kind"] == "whisper" else 0.3, f"résumé par le modèle ({words} mots à lire)…")
    t0 = time.time()
    summary = summarize(data, base_url)
    rec = {**data, "summary": summary, "questions": [], "at": time.time(), "seconds": round(time.time() - t0)}
    _save(rec)
    return public(rec)


def ask_saved(vid, question, base_url):
    """Question libre sur une vidéo déjà résumée ; l'échange est enregistré avec elle."""
    rec = load(vid)
    if not rec:
        raise RuntimeError("vidéo inconnue : résumez-la d'abord")
    history_ = [m for q in rec["questions"] for m in ({"role": "user", "content": q["q"]},
                                                      {"role": "assistant", "content": q["a"]})]
    r = ask(rec, history_, question, base_url)
    rec["questions"].append({"q": question, "a": r["answer"]})
    _save(rec)
    return {"answer": r["answer"], "timings": r.get("timings")}
