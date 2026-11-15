"""Résumé de vidéos YouTube : récupération du texte prononcé.

yt-dlp (version « zipapp » officielle : un seul fichier, lancé avec le Python du
launcher) lit les métadonnées et la liste des sous-titres ; YouTube exige un
moteur JavaScript pour ses défis, d'où Deno (binaire unique). Les deux vivent dans
engines/youtube/ et se mettent à jour en les retéléchargeant.
"""
import json
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import paths, download, engine

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-{}.zip"


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


def status():
    return {"ytdlp": ytdlp_path().is_file(), "deno": deno_path().is_file()}


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
    engine._extract(str(z), tools_dir())
    z.unlink(missing_ok=True)
    job.set(1.0, "yt-dlp et Deno installés")
    return status()


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
    """Choisit la piste de sous-titres : manuelle dans la langue de la vidéo, sinon
    manuelle en français ou anglais, sinon automatique dans la langue d'origine
    (jamais une traduction automatique). Renvoie (clé yt-dlp, « manuels »|« automatiques »)."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    base = orig.split("-")[0]
    for lang in [orig, base, "fr", "en"]:
        for k in manual:
            if lang and (k == lang or k.split("-")[0] == lang):
                return k, "manuels"
    for k in [f"{orig}-orig", orig, base]:
        if k in auto:
            return k, "automatiques"
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
    """Métadonnées et texte prononcé d'une vidéo. `transcript` vaut [] sans sous-titres."""
    info = json.loads(_ytdlp(["-J", "--skip-download", url]))
    orig = _original_lang(info)
    meta = {k: info.get(k) for k in ("id", "title", "channel", "duration", "webpage_url")}
    meta["language"] = orig
    meta["chapters"] = [{"start": c.get("start_time"), "title": c.get("title")} for c in info.get("chapters") or []]
    meta["subs"] = None
    transcript = []
    track = _pick_track(info, orig)
    if track:
        key, kind = track
        # Le fichier passe par yt-dlp : l'URL brute des métadonnées répond vite 429.
        with tempfile.TemporaryDirectory() as tmp:
            _ytdlp(["--skip-download", "--write-subs" if kind == "manuels" else "--write-auto-subs",
                    "--sub-langs", key, "--sub-format", "json3", "-o", str(Path(tmp) / "v"), info["webpage_url"]])
            f = Path(tmp) / f"v.{key}.json3"
            if f.is_file():
                transcript = _parse_json3(json.loads(f.read_text(encoding="utf-8")))
        meta["subs"] = {"lang": key.replace("-orig", ""), "kind": kind}
        meta["language"] = meta["language"] or meta["subs"]["lang"]
    return {"meta": meta, "transcript": transcript}


SYSTEM = """Tu résumes des vidéos YouTube pour quelqu'un qui ne veut pas perdre de temps à les regarder.
Tu reçois le titre, la transcription (sous-titres, souvent automatiques : fautes, noms propres écorchés), avec des repères [mm:ss] et, s'il y en a, les chapitres marqués « ## ».

Règles :
- Chaque point a une « phrase » qui donne l'information elle-même : la réponse, la conclusion, le conseil, le chiffre clé. Jamais le simple sujet. Une seule ligne : 25 mots au plus.
  Mauvais : « La vidéo parle de pâte à pizza. » Bon : « Une pâte laissée 48 h au frigo a plus de goût qu'une pâte levée 2 h à température ambiante. »
- « detail » : 4 à 6 phrases qui expliquent le pourquoi et le comment, avec les chiffres, noms et exemples importants de la vidéo.
- Un seul point par défaut. Plusieurs points (5 au maximum) seulement si la vidéo traite de sujets indépendants (revue d'actualité, plusieurs nouvelles sans lien) : un point par sujet, dans l'ordre de la vidéo, sans mélanger deux sujets dans un même point. Un tutoriel, un test ou un comparatif reste un seul point, même s'il a des chapitres.
- Ignore les sponsors, les demandes d'abonnement, les introductions et les anecdotes sans rapport.
- N'invente rien qui ne soit pas dans la transcription. Si la vidéo ne donne pas vraiment de réponse, dis-le dans la phrase.
- Réponds toujours en français, même si la vidéo est dans une autre langue ; garde les termes techniques usuels."""

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
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _prompt(data)}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "resume", "schema": SCHEMA}}}, timeout)
    return {**json.loads(text), **stats}


SYSTEM_QUESTION = """Tu réponds aux questions d'une personne sur une vidéo YouTube qu'elle n'a pas regardée.
Tu disposes de sa transcription (sous-titres, souvent automatiques : fautes, noms propres écorchés).
Réponds en français, brièvement, en t'appuyant uniquement sur la transcription ; si la vidéo n'aborde pas la question, dis-le.
Quand c'est utile, indique le moment de la vidéo (mm:ss) où le passage se trouve.
Écris en texte simple, sans Markdown (ni **, ni #) ; des tirets pour une liste si besoin."""


def ask(data, history, question, base_url, timeout=600):
    """Question libre sur la vidéo. `history` : [{"role": "user"|"assistant", "content"}] des
    échanges précédents. Le début du message (consignes + transcription) ne change pas d'une
    question à l'autre : llama-server le garde en cache et ne relit que la fin."""
    messages = [{"role": "system", "content": SYSTEM_QUESTION + "\n\n" + _prompt(data)},
                *history, {"role": "user", "content": question}]
    text, stats = _chat(base_url, {"messages": messages}, timeout)
    return {"answer": text, **stats}


# ---------------------------------------------------------------- résumés enregistrés
# Un fichier par vidéo dans <home>/youtube/<id>.json : métadonnées, transcription,
# résumé et questions posées. Revenir sur une vidéo ne coûte rien.

def video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{11})", url) or re.fullmatch(r"\s*([\w-]{11})\s*", url)
    return m.group(1) if m else None


def _file(vid):
    return paths.sub("youtube") / f"{vid}.json"


def load(vid):
    try:
        return json.loads(_file(vid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(rec):
    _file(rec["meta"]["id"]).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


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


def run(job, url, base_url):
    """Tâche « Résumer » : sous-titres puis résumé, enregistrés ensemble."""
    job.set(0.05, "lecture de la vidéo et de ses sous-titres…")
    data = fetch(url)
    if not data["transcript"]:
        raise RuntimeError("cette vidéo n'a pas de sous-titres exploitables (la transcription par Whisper n'est pas encore là)")
    words = sum(len(t.split()) for _, t in data["transcript"])
    job.set(0.3, f"résumé par le modèle ({words} mots à lire)…")
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
