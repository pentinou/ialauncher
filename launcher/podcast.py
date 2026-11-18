"""Résumé d'épisodes de podcast : flux RSS (ou lien Apple Podcasts, ou page qui annonce son
flux), audio de l'épisode transcrit par Whisper, puis résumé et questions libres exactement
comme dans l'onglet YouTube (mêmes fonctions, même modèle, même langue des résumés).

Podcasts suivis : check() (appelé au démarrage du launcher puis toutes les heures, voir
web.py) résume tout seul les épisodes parus depuis qu'on suit le podcast.
"""
import email.utils
import hashlib
import html
import json
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from . import paths, download, youtube

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
LOOKUP = "https://itunes.apple.com/lookup?id={}"
NEW_PER_CHECK = 2   # épisodes nouveaux résumés au plus par podcast et par vérification
_LOCK = threading.Lock()

_FIRST = "Tu résumes des vidéos YouTube pour quelqu'un qui ne veut pas perdre de temps à les regarder."
_FIRST_Q = "Tu réponds aux questions d'une personne sur une vidéo YouTube qu'elle n'a pas regardée."
assert _FIRST in youtube.SYSTEM and _FIRST_Q in youtube.SYSTEM_QUESTION
# Sans la consigne « une puce par chapitre », le résumé d'un épisode de 1 h 46 ne parlait que de
# sa première demi-heure (mesuré sur le podcast Hardware Unboxed, 0 essai sur 2 → 2 sur 3).
_IGNORE = "\n- Ignore les sponsors"
assert _IGNORE in youtube.SYSTEM
SYSTEM = youtube.SYSTEM.replace(_FIRST, "Tu résumes des épisodes de podcast pour quelqu'un qui ne veut pas perdre de temps "
                                "à les écouter. Les consignes disent « vidéo » : c'est ici l'épisode.").replace(
    _IGNORE, "\n- Un épisode de podcast est long : le « detail » suit l'ordre de l'épisode, avec au moins une puce par "
    "chapitre marqué « ## » (sauf introduction et générique), jusqu'aux conclusions. Si le titre de l'épisode pose une "
    "question, la « phrase » donne la réponse qu'apportent les intervenants.\n- Qui parle : la transcription "
    "d'un podcast ne le dit jamais, et ses animateurs habituels ne se distinguent pas à la lecture. Leurs avis : « qui » = "
    "« les animateurs », sauf si la phrase de la transcription nomme elle-même la personne (« Tim pense que… »). Un "
    "invité présenté nommément peut être nommé pour ses propres avis." + _IGNORE)
SYSTEM_QUESTION = youtube.SYSTEM_QUESTION.replace(_FIRST_Q, "Tu réponds aux questions d'une personne sur un épisode de "
                                                  "podcast qu'elle n'a pas écouté. Les consignes disent « vidéo » : c'est ici l'épisode.")


def _dir():
    return paths.sub("podcasts")


def _hid(s):
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _file(xid, ext="json"):
    # identifiant venu du navigateur : 16 caractères hexadécimaux, rien d'autre (pas de « ../ »)
    if not re.fullmatch(r"[0-9a-f]{16}", xid or ""):
        raise ValueError(f"identifiant invalide : {xid!r}")
    return _dir() / f"{xid}.{ext}"


# ---------------------------------------------------------------- flux RSS

def feed_url(url):
    """Adresse du flux RSS d'après ce que l'utilisateur a collé."""
    url = url.strip()
    if "open.spotify.com" in url:
        raise RuntimeError("Spotify ne publie pas le flux de ses podcasts : collez plutôt le lien Apple Podcasts "
                           "du même podcast, ou son flux RSS (souvent en bas de son site)")
    m = re.search(r"podcasts\.apple\.com/.*/id(\d+)", url)
    if m:
        res = download.fetch_json(LOOKUP.format(m.group(1))).get("results") or []
        if not res or not res[0].get("feedUrl"):
            raise RuntimeError("Apple Podcasts ne donne pas le flux RSS de ce podcast")
        return res[0]["feedUrl"]
    if not re.match(r"https?://", url):
        raise RuntimeError("collez une adresse : flux RSS, lien Apple Podcasts ou page du podcast")
    text = download.fetch_text(url)
    if re.search(r"<rss[\s>]", text[:5000]):
        return url
    # page web du podcast : <link rel="alternate" type="application/rss+xml" href="…">
    for tag in re.findall(r"<link\b[^>]*>", text, flags=re.I):
        if "application/rss+xml" in tag:
            href = re.search(r'href=["\']([^"\']+)', tag)
            if href:
                return html.unescape(href.group(1))
    raise RuntimeError("aucun flux RSS à cette adresse : collez le flux du podcast ou son lien Apple Podcasts")


def _duration(s):
    """« 01:46:33 », « 46:33 » ou « 6393 » → secondes."""
    try:
        n = 0
        for x in (s or "").strip().split(":"):
            n = n * 60 + float(x)
        return int(n) or None
    except ValueError:
        return None


def _text(h):
    """Description HTML → texte, une ligne par paragraphe."""
    t = re.sub(r"<br\s*/?>|</p>|</li>", "\n", h or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", t)).strip()


_CHAP = re.compile(r"^\s*\(?((?:\d{1,2}:)?\d{1,2}:\d{2})\)?\s*[-–—:]?\s*(\S.*)$", re.M)


def _chapters(desc):
    """Chapitres écrits dans la description (« 10:47 - Our Initial Thoughts »), s'il y en a."""
    out = []
    for ts, title in _CHAP.findall(_text(desc)):
        n = 0
        for x in ts.split(":"):
            n = n * 60 + int(x)
        out.append({"start": n, "title": title.strip()})
    return out if len(out) >= 2 else []


def fetch_feed(url):
    """Le podcast et ses épisodes (les plus récents d'abord)."""
    root = ET.fromstring(download.fetch_text(url, timeout=60).encode("utf-8"))
    ch = root.find("channel")
    if ch is None:
        raise RuntimeError("ce flux n'est pas un flux RSS de podcast")
    img = ch.find(ITUNES + "image")
    pod = {"url": url, "title": (ch.findtext("title") or url).strip(), "author": (ch.findtext(ITUNES + "author") or "").strip(),
           "image": img.get("href") if img is not None else ch.findtext("image/url"),
           "language": (ch.findtext("language") or "").strip().lower(), "link": ch.findtext("link") or url}
    eps = []
    for it in ch.findall("item"):
        enc = it.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        guid = (it.findtext("guid") or enc.get("url")).strip()
        try:
            date = email.utils.parsedate_to_datetime(it.findtext("pubDate")).timestamp()
        except (TypeError, ValueError):
            date = 0
        im = it.find(ITUNES + "image")
        desc = it.findtext("description") or it.findtext(ITUNES + "summary") or ""
        eps.append({"id": _hid(guid), "title": (it.findtext("title") or "").strip(), "date": date,
                    "duration": _duration(it.findtext(ITUNES + "duration")), "audio": enc.get("url"),
                    "link": it.findtext("link") or enc.get("url"),
                    "image": im.get("href") if im is not None else pod["image"], "chapters": _chapters(desc)})
    pod["episodes"] = sorted(eps, key=lambda e: e["date"], reverse=True)
    return pod


# ---------------------------------------------------------------- podcasts enregistrés

def _feeds_path():
    return _dir() / "feeds.json"


def feeds():
    try:
        return json.loads(_feeds_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_feeds(lst):
    tmp = _feeds_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(lst, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _feeds_path())


def _find(lst, fid):
    f = next((x for x in lst if x["id"] == fid), None)
    if not f:
        raise RuntimeError("podcast inconnu")
    return f


def _public_feed(f):
    return {k: f.get(k) for k in ("id", "url", "title", "author", "follow", "checked", "error")}


def episodes(pod, n=12):
    """Les n derniers épisodes, avec ce qui est déjà résumé."""
    return [{k: e[k] for k in ("id", "title", "date", "duration")} | {"done": _file(e["id"]).is_file()}
            for e in pod["episodes"][:n]]


def add(url, follow):
    """Ajoute (ou retrouve) un podcast. Suivi : seuls les épisodes publiés après seront résumés tout seuls."""
    pod = fetch_feed(feed_url(url))
    with _LOCK:
        lst = feeds()
        f = next((x for x in lst if x["url"] == pod["url"]), None)
        if not f:
            f = {"id": _hid(pod["url"]), "url": pod["url"], "added": time.time()}
            lst.insert(0, f)
        f.update(title=pod["title"], author=pod["author"], image=pod["image"])
        if follow and not f.get("follow"):
            _follow(f, pod)
        _save_feeds(lst)
    return {"feed": _public_feed(f), "episodes": episodes(pod)}


def _follow(f, pod):
    f.update(follow=True, since=time.time(), seen=[e["id"] for e in pod["episodes"]])


def set_follow(fid, follow):
    with _LOCK:
        lst = feeds()
        f = _find(lst, fid)
        if follow:
            _follow(f, fetch_feed(f["url"]))
        else:
            for k in ("follow", "since", "seen"):
                f.pop(k, None)
        _save_feeds(lst)
    return _public_feed(f)


def remove(fid):
    with _LOCK:
        _save_feeds([x for x in feeds() if x["id"] != fid])
    _file(fid, "jpg").unlink(missing_ok=True)


def feed_episodes(fid):
    return episodes(fetch_feed(_find(feeds(), fid)["url"]))


def check(start):
    """Podcasts suivis : `start(feed, episode)` pour chaque épisode paru depuis le début du
    suivi et pas encore vu (NEW_PER_CHECK au plus par podcast : un flux réécrit ne déclenche pas
    cent résumés). Renvoie le nombre de résumés lancés."""
    started = 0
    for fid in [x["id"] for x in feeds() if x.get("follow")]:
        try:
            pod = fetch_feed(_find(feeds(), fid)["url"])
            err = ""
        except Exception as e:  # noqa: BLE001 — flux injoignable : on réessaiera
            pod, err = None, str(e)[:200]
        with _LOCK:
            lst = feeds()
            f = next((x for x in lst if x["id"] == fid and x.get("follow")), None)
            if not f:
                continue
            f.update(checked=time.time(), error=err)
            if pod:
                seen = set(f.get("seen") or [])
                new = [e for e in pod["episodes"] if e["id"] not in seen and e["date"] >= f.get("since", 0) - 86400
                       and not _file(e["id"]).is_file()][:NEW_PER_CHECK]
                for e in new:
                    start(f, e)
                started += len(new)
                f["seen"] = [e["id"] for e in pod["episodes"]]
            _save_feeds(lst)
    return started


# ---------------------------------------------------------------- épisodes résumés
# Un fichier par épisode dans <home>/podcasts/<id>.json, même forme que ceux de l'onglet YouTube.

def load(eid):
    try:
        return json.loads(_file(eid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(rec):
    _file(rec["meta"]["id"]).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def thumb(xid):
    """Pochette d'un épisode ou d'un podcast, réduite à 320 px (les originales font souvent
    3000 px) et gardée à côté."""
    f = _file(xid, "jpg")
    if not f.is_file():
        rec = load(xid)
        src = rec["meta"].get("image") if rec else next((x.get("image") for x in feeds() if x["id"] == xid), None)
        if not src:
            raise RuntimeError("pas d'image")
        raw = _file(xid, "img")
        download.download(src, str(raw))
        try:
            subprocess.run([youtube.ffmpeg_bin() or "ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(raw),
                            "-vf", "scale=320:-2", "-q:v", "4", str(f)], check=True, capture_output=True, timeout=60)
            raw.unlink()
        except (OSError, subprocess.SubprocessError):
            os.replace(raw, f)   # sans ffmpeg : l'image d'origine
    return f.read_bytes()


def delete(ids):
    n = 0
    for eid in ids:
        f = _file(eid)
        if f.is_file():
            n += 1
        for ext in ("json", "jpg", "part"):
            _file(eid, ext).unlink(missing_ok=True)
    return n


def history():
    out = []
    for f in _dir().glob("*.json"):
        if f.name == "feeds.json":
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m = rec["meta"]
        out.append({"id": m["id"], "title": m["title"], "channel": m.get("channel"), "duration": m.get("duration"),
                    "date": m.get("date"), "at": rec.get("at", 0),
                    "phrase": (rec.get("summary") or {}).get("points", [{}])[0].get("phrase", "")})
    return sorted(out, key=lambda x: x["at"], reverse=True)


def run(job, fid, eid, llm):
    """Tâche « Résumer » d'un épisode : audio → Whisper → résumé. Comme pour YouTube, la
    transcription est gardée à part (<id>.part) jusqu'au résumé, et « refaire le résumé »
    reprend celle déjà enregistrée (et les questions posées)."""
    part = _file(eid, "part")
    old = load(eid) or {}
    try:
        data = json.loads(part.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"meta": old["meta"], "transcript": old["transcript"]} if old else None
    if data:
        job.label = f"Résumé podcast — {data['meta']['title']}"
        base_url = llm(job)
    else:
        job.set(0.02, "lecture du flux du podcast…")
        pod = fetch_feed(_find(feeds(), fid)["url"])
        e = next((x for x in pod["episodes"] if x["id"] == eid), None)
        if not e:
            raise RuntimeError("cet épisode n'est plus dans le flux du podcast")
        job.label = f"Résumé podcast — {e['title']}"
        if not youtube.status()["whisper"]:
            raise RuntimeError("il faut Whisper pour transcrire les podcasts : installez-le (bouton en haut de l'onglet)")
        data = {"meta": {"id": eid, "title": e["title"], "channel": pod["title"], "duration": e["duration"],
                         "webpage_url": e["link"], "audio": e["audio"], "image": e["image"], "date": e["date"],
                         "feed": fid, "language": pod["language"], "chapters": e["chapters"], "subs": None},
                "transcript": []}
        base_url = llm(job)   # avant Whisper : libère la carte graphique (image, musique)
        data["transcript"], lang = youtube.transcribe(job, data, 0.05, 0.6)
        data["meta"]["subs"] = {"lang": lang, "kind": "whisper"}
        data["meta"]["language"] = data["meta"]["language"] or lang
        part.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    words = sum(len(t.split()) for _, t in data["transcript"])
    job.set(max(job.progress, 0.6), f"résumé par le modèle ({words} mots à lire)…")
    t0 = time.time()
    summary = youtube.summarize(job, data, llm, base_url, system=SYSTEM)
    rec = {**data, "summary": summary, "questions": old.get("questions", []), "at": time.time(), "seconds": round(time.time() - t0)}
    _save(rec)
    part.unlink(missing_ok=True)
    return youtube.public(rec)


def ask_saved(job, eid, question, llm):
    rec = load(eid)
    if not rec:
        raise RuntimeError("épisode inconnu : résumez-le d'abord")
    history_ = [m for q in rec["questions"] for m in ({"role": "user", "content": q["q"]},
                                                      {"role": "assistant", "content": q["a"]})]
    r = youtube.ask(job, rec, history_, question, llm, llm(job), system=SYSTEM_QUESTION)
    rec["questions"].append({"q": question, "a": r["answer"]})
    _save(rec)
    return {"answer": r["answer"], "timings": r.get("timings")}
