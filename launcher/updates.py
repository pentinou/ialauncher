"""Mises à jour : moteurs (llama.cpp, stable-diffusion.cpp, ACE-Step) et nouvelles
versions des modèles.

Recherche de modèles : le nom de chaque dépôt de référence (catalogue texte, GGUF
téléchargés, catalogue image/vidéo) est découpé en famille (« qwen », « flux »…),
version (3.6, 2…) et autres mots (35b, a3b, klein…). On cherche ensuite sur Hugging
Face, chez le même publieur et quelques quantiseurs reconnus, les dépôts GGUF de la
même famille publiés après lui : version plus récente, ou même version avec un nom en
partie différent (variante, nouvelle branche). Rien ne change sans validation : le
modèle retenu remplace l'ancien dans le catalogue (fichier catalog-user.json), et les
fichiers déjà téléchargés restent sur le disque.

Pour l'image et la vidéo, un modèle a besoin d'un encodeur de texte, d'un VAE et de
réglages : la fiche est pré-remplie d'après la documentation de stable-diffusion.cpp
qui cite le dépôt (fichiers, ligne de commande d'exemple), puis vérifiée par l'utilisateur."""
import json
import re
import shlex
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.parse import quote

from . import paths, download, catalog, gen_catalog, engine, sdcpp, music, models

HF = "https://huggingface.co"
SD_RAW = "https://raw.githubusercontent.com/leejet/stable-diffusion.cpp"
SD_API = "https://api.github.com/repos/leejet/stable-diffusion.cpp"
LLM_OWNERS = ["unsloth", "ggml-org"]
GEN_OWNERS = ["leejet", "QuantStack", "unsloth", "city96"]
GEN_PIPES = {"text-to-image", "image-to-image", "text-to-video", "image-to-video", "image-text-to-image"}
SCAN_TTL = 6 * 3600

# ---------------------------------------------------------------- catalogue utilisateur

_BUILTIN = {"llm": list(catalog.CATALOG), "gen": list(gen_catalog.CATALOG)}


def _user_file():
    return paths.config_file().parent / "catalog-user.json"


def load_user():
    try:
        d = json.loads(_user_file().read_text())
    except (OSError, ValueError):
        d = {}
    return {"llm": d.get("llm", []), "gen": d.get("gen", []), "ignored": d.get("ignored", [])}


def _save_user(u):
    _user_file().write_text(json.dumps(u, ensure_ascii=False, indent=1))
    apply_user()


def apply_user():
    """Reconstruit les catalogues : intégré + choix validés. Un modèle texte remplacé
    disparaît ; un modèle image/vidéo remplacé reste connu (masqué), car des checkpoints
    Civitai peuvent encore emprunter son encodeur et son VAE."""
    u = load_user()
    llm = list(_BUILTIN["llm"])
    for e in u["llm"]:
        i = next((k for k, c in enumerate(llm) if c["repo"] == e.get("replaces")), None)
        if i is None:
            llm.append(e)
        else:
            llm[i] = e
    catalog.CATALOG[:] = llm
    gen = list(_BUILTIN["gen"])
    for e in u["gen"]:
        i = next((k for k, c in enumerate(gen) if c["id"] == e.get("replaces")), None)
        if i is None:
            gen.append(e)
        else:
            gen[i] = {**gen[i], "hidden": True, "replaced_by": e["id"]}
            gen.insert(i + 1, e)
    gen_catalog.CATALOG[:] = gen


apply_user()

# ---------------------------------------------------------------- moteurs


def _check_llama():
    it = {"id": "llama", "name": "llama.cpp", "role": "texte", "installed": "", "latest": "", "update": False}
    b = engine.current_bin()
    if not b:
        it["note"] = "non installé : section Moteur de l'onglet Texte"
        return it
    m = engine._marker(b)
    if not m.get("tag"):
        it.update(installed="installé hors launcher", note="pas de mise à jour automatique")
        return it
    it["installed"] = m["tag"]
    it["latest"] = engine.stable_tag()
    it["update"] = int(it["latest"][1:]) > int(m["tag"][1:])
    it["action"] = {"url": "/api/engine/install", "body": {
        "method": "build" if m.get("variant") == "cuda-local" else "prebuilt",
        "variant": m.get("variant", ""), "label": m.get("label", "")}}
    it["long"] = "compilation locale (5 à 15 min)" if m.get("variant") == "cuda-local" else "téléchargement"
    return it


def _sd_num(tag):
    m = re.search(r"master-(\d+)", tag or "")
    return int(m.group(1)) if m else 0


def _check_sd():
    it = {"id": "sd", "name": "stable-diffusion.cpp", "role": "image et vidéo", "installed": "", "latest": "", "update": False}
    b = sdcpp.current_bin()
    if not b:
        it["note"] = "non installé : onglet Image"
        return it
    m = engine._marker(b)
    it["installed"] = m.get("tag", "installé hors launcher")
    it["latest"] = download.fetch_json(f"{sdcpp.API}/latest")["tag_name"]
    if m.get("tag"):
        it["update"] = _sd_num(it["latest"]) > _sd_num(m["tag"])
        it["action"] = {"url": "/api/gen/engine/install", "body": {
            "method": "build" if m.get("variant") == "cuda-local" else "prebuilt",
            "variant": m.get("variant", ""), "label": m.get("label", "")}}
        it["long"] = "compilation locale (10 à 20 min)" if m.get("variant") == "cuda-local" else "téléchargement"
    return it


def _git(*args):
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git introuvable")
    return subprocess.run([git, *args], capture_output=True, text=True, timeout=30, check=True).stdout.strip()


def _check_ace():
    it = {"id": "ace", "name": "ACE-Step", "role": "musique", "installed": "", "latest": "", "update": False}
    d = music.app_dir()
    if not (d / ".git").is_dir():
        it["note"] = "non installé : onglet Musique" if not d.exists() else "installé sans git : réinstaller depuis l'onglet Musique"
        return it
    local = _git("-C", str(d), "rev-parse", "HEAD")
    remote = _git("ls-remote", music.REPO, "HEAD").split()[0]
    it.update(installed=local[:7], latest=remote[:7], update=local != remote,
              action={"url": "/api/music/install", "body": {}}, long="mise à jour du code et des dépendances Python")
    return it


def engines():
    names = {_check_llama: "llama.cpp", _check_sd: "stable-diffusion.cpp", _check_ace: "ACE-Step"}

    def safe(fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — hors ligne, GitHub limité…
            return {"id": fn.__name__, "name": names[fn], "error": str(e)[:200]}
    with ThreadPoolExecutor(3) as ex:
        return list(ex.map(safe, [_check_llama, _check_sd, _check_ace]))

# ---------------------------------------------------------------- noms de modèles


GENERIC = {"gguf", "ggufs", "it", "instruct", "hf", "ud"}
RE_SIZE = re.compile(r"^[a-z]?\d+(?:\.\d+)?[bmkt]$")         # 35b, a3b, e4b, 0.6b, 2.4t
RE_NUM = re.compile(r"^\d+(?:\.\d+)*$")
RE_ALNUM = re.compile(r"^([a-z]+)\.?(\d+(?:\.\d+)*)$")      # qwen3.6, flux.2, wan2.2, h3


def parse(repo):
    """« unsloth/Qwen3.6-35B-A3B-GGUF » → famille qwen, version (3, 6), mots [35b, a3b]."""
    base = re.sub(r"[-_.]?ggufs?$", "", repo.split("/")[-1], flags=re.I)
    fam, ver, rest, closed = [], None, [], False
    for p in re.split(r"[-_\s]+", QRE.sub("", base).lower()):
        if not p:
            continue
        if ver is not None or closed:
            rest.append(p)
        elif RE_SIZE.match(p):
            rest.append(p)
            closed = True
        elif RE_NUM.match(p):
            ver = p
        elif RE_ALNUM.match(p):
            m = RE_ALNUM.match(p)
            fam.append(m.group(1))
            ver = m.group(2)
        elif p.isalpha():
            fam.append(p)
        else:
            rest.append(p)
            closed = True
    return {"family": "".join(fam), "search": "-".join(fam[:2]), "label": base,
            "version": tuple(int(x) for x in ver.split(".")) if ver else None,
            "rest": [t for t in rest if t not in GENERIC]}


def _match(r, c):
    """Le dépôt c est-il une nouvelle version (ou variante) de r ? → (type, score) ou None."""
    if not r["family"] or not c["family"]:
        return None
    if r["family"] != c["family"]:
        # sans numéro de version, « z-image-turbo » et « z-image » sont de la même gamme
        if (r["version"] and c["version"]) or not (r["family"].startswith(c["family"]) or c["family"].startswith(r["family"])):
            return None
    rv, cv = r["version"] or (0,), c["version"] or (0,)
    if cv < rv:
        return None
    shared = set(r["rest"]) & set(c["rest"])
    sizes = {t for t in shared if RE_SIZE.match(t)}
    if not sizes and _sizes(r) and _sizes(c):
        return None      # autre taille (4B / 27B) : pas la même branche
    if cv > rv:
        kind = "version"
    elif r["family"] != c["family"]:
        kind = "variante"
    elif set(c["rest"]) == set(r["rest"]):
        return None      # même modèle chez un autre publieur
    elif not shared and not (set(r["rest"]) <= set(c["rest"]) or set(c["rest"]) <= set(r["rest"])):
        return None      # autre gamme (ex. 12B et E2B) : rien en commun
    else:
        kind = "variante"
    # le plus de mots en commun, le moins de différences ; une version plus récente départage
    score = 2 * len(sizes) + len(shared - sizes) - 0.5 * len(set(r["rest"]) ^ set(c["rest"])) + (kind == "version")
    return kind, score


def _sizes(p):
    return {t for t in p["rest"] if RE_SIZE.match(t)}


def _same_model(r, c):
    return (r["family"], r["version"], set(r["rest"])) == (c["family"], c["version"], set(c["rest"]))


def _superseded(c, refs):
    """Un modèle déjà en référence est plus récent que c, à la même taille (ex. Qwen3.5-35B-A3B
    alors que Qwen3.6-35B-A3B est au catalogue) : c n'apporte rien."""
    return any(r["p"]["family"] == c["family"] and (r["p"]["version"] or (0,)) > (c["version"] or (0,))
               and _sizes(c) and _sizes(c) <= _sizes(r["p"]) for r in refs)

# ---------------------------------------------------------------- recherche


def _refs():
    refs = [{"kind": "llm", "key": e["repo"], "repo": e["repo"], "name": e["name"], "source": "catalogue texte",
             "in_catalog": True} for e in catalog.CATALOG]
    cat = {e["repo"] for e in catalog.CATALOG}
    for d in sorted(paths.models_dir().iterdir()):
        if d.is_dir() and "__" in d.name and any(d.glob("*.gguf")):
            repo = d.name.replace("__", "/", 1)
            if repo not in cat:
                refs.append({"kind": "llm", "key": repo, "repo": repo, "name": repo.split("/")[-1],
                             "source": "téléchargé", "in_catalog": False})
    for e in gen_catalog.CATALOG:
        if e.get("hidden"):
            continue
        role = "diffusion_model" if "diffusion_model" in e["files"] else "model"
        refs.append({"kind": "gen", "key": e["id"], "repo": e["files"][role][0], "name": e["name"],
                     "source": "catalogue " + ("image" if e["kind"] == "image" else "vidéo"), "in_catalog": True})
    return refs


def _search(author, term):
    try:
        return download.fetch_json(f"{HF}/api/models?author={quote(author)}&search={quote(term)}"
                                   "&filter=gguf&sort=createdAt&direction=-1&limit=100")
    except Exception:  # noqa: BLE001 — un publieur injoignable ne bloque pas les autres
        return []


def _info(repo):
    try:
        return download.fetch_json(f"{HF}/api/models/{repo}")
    except Exception:  # noqa: BLE001
        return {}


def scan(force=False):
    cache = paths.cache_dir() / "updates.json"
    if not force and cache.exists() and time.time() - cache.stat().st_mtime < SCAN_TTL:
        try:
            return _filter(json.loads(cache.read_text()))
        except ValueError:
            pass
    refs = _refs()
    for r in refs:
        r["p"] = parse(r["repo"])
    queries = set()
    for r in refs:
        if r["p"]["search"]:
            for o in [r["repo"].split("/")[0]] + (GEN_OWNERS if r["kind"] == "gen" else LLM_OWNERS):
                queries.add((o, r["p"]["search"]))
    queries = sorted(queries)
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda q: _search(*q), queries))
    seen = {m["id"]: m for lst in results for m in lst}
    missing = sorted({r["repo"] for r in refs} - set(seen))
    with ThreadPoolExecutor(8) as ex:
        for repo, info in zip(missing, ex.map(_info, missing)):
            if info:
                seen[repo] = info
    created = {r["key"]: (seen.get(r["repo"]) or {}).get("createdAt", "") for r in refs}
    ref_repos = {r["repo"] for r in refs}
    found = {}
    for m in seen.values():
        if m["id"] in ref_repos:
            continue
        is_gen = m.get("pipeline_tag") in GEN_PIPES
        cp = parse(m["id"])
        if any(_same_model(r["p"], cp) for r in refs) or _superseded(cp, refs):
            continue
        best = None
        for r in refs:
            if (r["kind"] == "gen") != is_gen or not created[r["key"]] or m.get("createdAt", "") <= created[r["key"]]:
                continue
            res = _match(r["p"], cp)
            if res and (best is None or (res[1], r["p"]["version"] or ()) > (best[1][1], best[0]["p"]["version"] or ())):
                best = (r, res)
        if not best:
            continue
        r, (kind, score) = best
        lst = found.setdefault(r["key"], {})
        name_key = cp["label"].lower()
        cand = {"repo": m["id"], "type": kind, "score": score, "created": m.get("createdAt", "")[:10],
                "downloads": m.get("downloads", 0), "likes": m.get("likes", 0), "pipeline": m.get("pipeline_tag") or ""}
        old = lst.get(name_key)
        # même nom chez plusieurs publieurs : on garde celui de la référence, sinon le plus téléchargé
        mine = lambda c: c["repo"].split("/")[0] == r["repo"].split("/")[0]  # noqa: E731
        if old is None or (mine(cand), cand["downloads"]) > (mine(old), old["downloads"]):
            lst[name_key] = cand
    groups = []
    for r in refs:
        cands = sorted(found.get(r["key"], {}).values(),
                       key=lambda c: (c["type"] != "version", -c["score"], -c["downloads"]))[:6]
        if cands:
            groups.append({"kind": r["kind"], "ref": {k: r[k] for k in ("key", "repo", "name", "source", "in_catalog")},
                           "ref_created": created[r["key"]][:10], "candidates": cands})
    out = {"at": time.time(), "refs": len(refs), "queries": len(queries), "groups": groups}
    cache.write_text(json.dumps(out))
    return _filter(out)


def _filter(out):
    """Retire ce qui a été ignoré ou ajouté au catalogue depuis la recherche."""
    u = load_user()
    gone = set(u["ignored"]) | {e["repo"] for e in catalog.CATALOG} | {e.get("repo", "") for e in u["gen"]}
    groups = []
    for g in out["groups"]:
        cands = [c for c in g["candidates"] if c["repo"] not in gone]
        if cands and (g["kind"] == "llm" or gen_catalog.find(g["ref"]["key"]) and not gen_catalog.find(g["ref"]["key"]).get("hidden")):
            groups.append({**g, "candidates": cands})
    return {**out, "groups": groups}


def ignore(repo):
    u = load_user()
    if repo not in u["ignored"]:
        u["ignored"].append(repo)
    _save_user(u)

# ---------------------------------------------------------------- fiche image / vidéo

QRE = re.compile(r"(?i)(?<![a-z0-9])(?:ud-)?(?:iq\d_[a-z]+|q\d_k(?:_[a-z]+)?|q\d_\d|bf16|fp16|f16|f32|fp8(?:_[a-z0-9]+)?|mxfp4)(?![a-z0-9])")
EXT = re.compile(r"\.(gguf|safetensors|ckpt|sft|pt|bin)$", re.I)
FLAG_ROLE = {"-m": "model", "--model": "model", "--diffusion-model": "diffusion_model",
             "--high-noise-diffusion-model": "high_noise", "--vae": "vae", "--audio-vae": "audio_vae",
             "--llm": "llm", "--llm_vision": "llm_vision", "--llm-vision": "llm_vision", "--t5xxl": "t5xxl"}
FLAG_DEF = {"--cfg-scale": ("cfg", float), "--steps": ("steps", int), "--sampling-method": ("sampler", str),
            "--flow-shift": ("flow_shift", float), "--video-frames": ("frames", int), "--fps": ("fps", int),
            "--scheduler": ("scheduler", str), "--high-noise-steps": ("high_noise_steps", int)}
FLAG_FEAT = {"-r": "ref_images", "--ref-image": "ref_images", "-i": "init_image", "--init-img": "init_image",
             "--end-img": "end_image"}
# options sans incidence sur la fiche (le launcher gère lui-même mémoire, sortie, prompt…)
FLAG_SKIP = {"-p", "--prompt", "-n", "--negative-prompt", "-v", "--verbose", "-o", "--output", "-s", "--seed",
             "--offload-to-cpu", "--diffusion-fa", "--vae-tiling", "--vae-on-cpu", "--clip-on-cpu", "--color",
             "-b", "--batch-count", "--strength", "-W", "--width", "-H", "--height", "--high-noise-cfg-scale", "--high-noise-sampling-method", "-M", "--mode"}


def _quant(name):
    m = QRE.search(name)
    return m.group(0).upper() if m else ""


def _norm(name):
    base = QRE.sub("", EXT.sub("", re.split(r"[\\/]", name)[-1]))
    return re.sub(r"[^a-z0-9]", "", base.lower())


def _toks(name):
    return {t for t in re.split(r"[^a-z0-9]+", QRE.sub("", EXT.sub("", name)).lower()) if t}


def _sim(path, hint):
    if _norm(path) == _norm(hint):
        return 2.0
    a, b = _toks(path), _toks(re.split(r"[\\/]", hint)[-1])
    return len(a & b) / len(a | b) if a | b else 0.0


def _tree(repo):
    """[{path, size}] des fichiers d'un dépôt HF (cache 6 h)."""
    cache = paths.cache_dir() / "hf" / ("tree__" + repo.replace("/", "__") + ".json")
    if cache.exists() and time.time() - cache.stat().st_mtime < SCAN_TTL:
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    lst = download.fetch_json(f"{HF}/api/models/{repo}/tree/main?recursive=1")
    files = [{"path": x["path"], "size": (x.get("lfs") or {}).get("size") or x.get("size", 0)}
             for x in lst if x.get("type") == "file"]
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(files))
    return files


def _pick(files, hint, pref_quant, need_match=False):
    """Le fichier de poids qui ressemble le plus à hint, dans la quantification préférée."""
    mm = "mmproj" in hint.lower()
    cands = [f for f in files if f["path"].lower().endswith((".gguf", ".safetensors"))
             and ("mmproj" in f["path"].lower()) == mm]
    if not cands:
        return None
    scored = [(_sim(f["path"], hint), f) for f in cands]
    best = max(s for s, _ in scored)
    if need_match and best <= 0:
        return None
    same = [f for s, f in scored if s == best]
    for q in (pref_quant, _quant(hint), "Q8_0"):
        hit = next((f for f in same if q and _quant(f["path"]) == q), None)
        if hit:
            return hit
    return same[0]


def _sd_docs():
    """{nom: texte} des docs de stable-diffusion.cpp (branche master), cache 24 h."""
    cache = paths.cache_dir() / "sd-docs.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    names = [x["name"] for x in download.fetch_json(f"{SD_API}/contents/docs") if x["name"].endswith(".md")]

    def get(n):
        try:
            return download.fetch_text(f"{SD_RAW}/master/docs/{n}")
        except Exception:  # noqa: BLE001
            return ""
    with ThreadPoolExecutor(8) as ex:
        docs = dict(zip(names, ex.map(get, names)))
    cache.write_text(json.dumps(docs))
    return docs


def _commands(text):
    """Blocs de code de la doc → [{flags: {option: [valeurs]}, heading, full}]. full = ligne
    sd-cli complète ; sinon fragment d'options (« ajoutez --steps 8 … » d'une variante)."""
    out, heading = [], ""
    for m in re.finditer(r"^#+[ \t]*([^\n]+)$|```[^\n]*\n(.*?)```", text, re.M | re.S):
        if m.group(1) is not None:
            heading = m.group(1)
            continue
        block = re.sub(r"[\\^`]\s*\n", " ", m.group(2))      # continuations de ligne (bash, cmd, PowerShell)
        for line in block.splitlines():
            full = bool(re.search(r"\bsd(-cli)?(\.exe)?\s", line + " "))
            if not full and not line.lstrip().startswith("--"):
                continue
            try:
                toks = shlex.split(line, posix=False)
            except ValueError:
                toks = line.split()
            flags = {}
            for i, t in enumerate(toks):
                if t.startswith("-") and not RE_NUM.match(t.lstrip("-")):
                    nxt = toks[i + 1] if i + 1 < len(toks) else ""
                    val = nxt if nxt and not (nxt.startswith("-") and not RE_NUM.match(nxt.lstrip("-"))) else ""
                    flags.setdefault(t, []).append(val.strip("\"'"))
            if (full and any(f in flags for f in FLAG_ROLE)) or (not full and any(f in flags for f in FLAG_DEF)):
                out.append({"flags": flags, "heading": heading, "full": full})
    return out


def _main_file(cmd):
    for f in ("--diffusion-model", "-m", "--model"):
        if cmd["flags"].get(f):
            return cmd["flags"][f][0]
    return ""


def _apply_flags(flags, defaults, feats, ignored):
    for f, vals in flags.items():
        if f in FLAG_DEF:
            k, typ = FLAG_DEF[f]
            try:
                defaults[k] = typ(vals[0])
            except ValueError:
                pass
        elif f in FLAG_FEAT:
            feats.add(FLAG_FEAT[f])
        elif f not in FLAG_SKIP and f not in FLAG_ROLE:
            ignored.add(f)


def prefill(ref_id, repo, kind="version"):
    """Fiche du catalogue image/vidéo proposée pour remplacer ref_id par le dépôt repo
    (kind : « version » = nouvelle génération, « variante » = même modèle, autre branche)."""
    pred = gen_catalog.find(ref_id)
    if not pred:
        raise ValueError("modèle de référence inconnu")
    info = download.fetch_json(f"{HF}/api/models/{repo}")
    try:
        readme = download.fetch_text(f"{HF}/{repo}/raw/main/README.md")
    except Exception:  # noqa: BLE001
        readme = ""
    warnings, notes = [], []
    # 1. la documentation de sd.cpp qui parle de ce dépôt
    doc_name, doc = None, ""
    try:
        docs = _sd_docs()
        m = re.search(r"stable-diffusion\.cpp/(?:blob|tree)/[^/]+/docs/([\w.-]+\.md)", readme)
        if m and docs.get(m.group(1)):
            doc_name, doc = m.group(1), docs[m.group(1)]
        else:
            # le dépôt lui-même, sinon le nom du modèle (la doc cite souvent l'original, pas le GGUF)
            keys = [repo.lower(), repo.split("/")[-1].lower(), parse(repo)["label"].lower()]

            def score(item):
                n, t = item
                return (max((3 - i for i, k in enumerate(keys) if k in t.lower()), default=0), _sim(n, parse(repo)["label"]))
            best = max(docs.items(), key=score, default=None)
            if best and score(best)[0]:
                doc_name, doc = best
    except Exception as e:  # noqa: BLE001
        warnings.append(f"documentation de stable-diffusion.cpp illisible ({e})")
    if not doc_name:
        warnings.append("Aucune page de la documentation de stable-diffusion.cpp ne cite ce dépôt : fiche reprise de "
                        f"« {pred['name']} », à vérifier. Si l'architecture est nouvelle, sd.cpp ne la gère peut-être pas encore.")
    # 2. la commande d'exemple qui correspond le mieux à ce modèle
    label = parse(repo)["label"]
    pred_main = pred["files"].get("diffusion_model") or pred["files"].get("model")
    # mots propres à la nouvelle branche (« turbo », « base »…) : repérés dans les titres de la doc
    extra = set(parse(repo)["rest"]) - set(parse(pred_main[0])["rest"])
    all_cmds = _commands(doc)
    cmds = [c for c in all_cmds if c["full"]]
    roles, defaults, feats, ignored = {}, {}, set(), set()
    if cmds:
        cmd = max(cmds, key=lambda c: (_sim(_main_file(c), label), len(extra & _toks(c["heading"]))))
        main = _norm(_main_file(cmd))
        for c in cmds:      # même modèle dans plusieurs exemples (texte→image, retouche…) : on cumule
            if _norm(_main_file(c)) == main:
                for f, vals in c["flags"].items():
                    if f in FLAG_ROLE:
                        roles.setdefault(FLAG_ROLE[f], re.split(r"[\\/]", vals[0])[-1])
                _apply_flags({f: v for f, v in c["flags"].items() if f in FLAG_FEAT}, defaults, feats, ignored)
        _apply_flags(cmd["flags"], defaults, feats, ignored)
        for c in all_cmds:
            if not c["full"] and extra & _toks(c["heading"]):
                _apply_flags(c["flags"], defaults, feats, ignored)
                notes.append(f"Réglages de la section « {c['heading']} » de la doc appliqués.")
    else:
        roles = {r: v[1].split("/")[-1] for r, v in pred["files"].items()}
        if doc_name:
            warnings.append(f"Pas de ligne de commande d'exemple dans docs/{doc_name} : fichiers et réglages repris de « {pred['name']} ».")
    if "model" in roles and "diffusion_model" in roles:
        roles.pop("model")
    # 3. un fichier par rôle
    pref_q = _quant(pred_main[1]) if pred_main else "Q8_0"
    links = []
    for lr, sub in re.findall(r"https://huggingface\.co/([\w.-]+/[\w.-]+)(?:/(?:tree|blob|resolve)/main/?([^\s)\]>]*))?", doc):
        if (lr, sub) not in links and lr != repo:
            links.append((lr, sub))
    gated = {}

    def is_gated(r):
        if r not in gated:
            gated[r] = bool(_info(r).get("gated"))
        return gated[r]
    files = []
    cand_tree = _tree(repo)
    for role, hint in roles.items():
        f, src = None, ""
        if role in ("diffusion_model", "model", "high_noise"):
            f = _pick(cand_tree, hint or label, pref_q, need_match=role == "high_noise")
            if f:
                f, src = {"repo": repo, **f}, "nouveau dépôt"
            elif role != "high_noise":
                warnings.append(f"Aucun fichier de poids reconnu dans {repo}.")
        if not f and role not in ("diffusion_model", "model"):
            for e in gen_catalog.CATALOG:       # déjà utilisé ailleurs dans le catalogue : rien à retélécharger
                for r2, (rr, ff, ss) in e["files"].items():
                    if r2 == role and _norm(ff) == _norm(hint):
                        f, src = {"repo": rr, "path": ff, "size": ss}, f"déjà utilisé par « {e['name']} »"
                        break
                if f:
                    break
        if not f and kind == "variante" and role in pred["files"] and role not in ("diffusion_model", "model", "high_noise"):
            rr, ff, ss = pred["files"][role]
            f, src = {"repo": rr, "path": ff, "size": ss}, f"repris de « {pred['name']} » (même modèle)"
        if not f and role not in ("diffusion_model", "model"):
            best = None
            for lr, sub in links:
                if is_gated(lr):
                    continue
                try:
                    t = [x for x in _tree(lr) if x["path"].startswith(sub.strip("/"))]
                except Exception:  # noqa: BLE001
                    continue
                pf = pred["files"].get(role)
                hit = _pick(t, hint, _quant(pf[1]) if pf else "Q8_0", need_match=True)
                if hit and (best is None or _sim(hit["path"], hint) > _sim(best["path"], hint)):
                    best = {"repo": lr, **hit}
            if best:
                f, src = best, f"documentation sd.cpp ({doc_name})"
        if not f and role in pred["files"] and role not in ("diffusion_model", "model"):
            rr, ff, ss = pred["files"][role]
            f, src = {"repo": rr, "path": ff, "size": ss}, f"repris de « {pred['name']} »"
        files.append({"role": role, "label": gen_catalog.ROLE_LABELS.get(role, role), "hint": hint,
                      "repo": f["repo"] if f else "", "file": f["path"] if f else "",
                      "size": f["size"] if f else 0, "source": src or "à compléter"})
        if not f:
            warnings.append(f"{gen_catalog.ROLE_LABELS.get(role, role)} : aucun fichier trouvé pour « {hint} », à compléter.")
    if any(is_gated(lr) for lr, _ in links):
        notes.append("Dépôts protégés (connexion Hugging Face requise) écartés : "
                     + ", ".join(sorted(r for r, g in gated.items() if g)))
    if ignored:
        notes.append("Options de l'exemple non reprises par le launcher : " + ", ".join(sorted(ignored)))
    # 4. réglages : ceux de l'exemple, sinon ceux de l'ancien modèle
    d = dict(pred["defaults"])
    d.pop("negative_prompt", None)
    d.update(defaults)
    d["negative"] = float(d.get("cfg", 1)) > 1
    if "sampler" not in defaults and cmds:
        d["sampler"] = ""
    if not cmds:
        feats = set(pred.get("features", []))
    # 5. le moteur installé connaît-il ce modèle ?
    b = sdcpp.current_bin()
    tag = engine._marker(b).get("tag", "") if b else ""
    if doc_name and tag.count("-") >= 2:
        try:
            download.fetch_text(f"{SD_RAW}/{tag.rsplit('-', 1)[1]}/docs/{doc_name}")
        except HTTPError as e:
            if e.code == 404:
                warnings.append(f"Votre stable-diffusion.cpp ({tag}) est antérieur à ce modèle : mettez-le à jour "
                                "(section Moteurs ci-dessus) avant de l'utiliser.")
        except Exception:  # noqa: BLE001
            pass
    card = info.get("cardData") or {}
    lic = re.search(r"^\s*-\s*([^:\n]*Licen[sc]e[^:\n]*)", readme, re.M | re.I)
    intro = next((p.strip() for p in re.split(r"\n\s*\n", re.sub(r"^#.*$", "", doc, flags=re.M))
                  if p.strip() and not p.strip().startswith(("-", "!", "<", "`", "|"))), "")[:400]
    name = re.sub(r"[-_]+", " ", label).strip()
    return {"replaces": ref_id, "repo": repo, "name": name, "kind": pred["kind"],
            "license": (lic.group(1).strip() if lic else "") or card.get("license_name") or card.get("license") or "",
            "tags": pred.get("tags", []), "notice": pred.get("notice", ""),
            "blurb": f"Successeur de « {pred['name']} »." + (f" {intro}" if intro else ""),
            "files": files, "defaults": d, "features": sorted(feats), "server_args": pred.get("server_args", []) if not cmds else [],
            "doc": {"name": doc_name, "url": f"https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/{doc_name}"} if doc_name else None,
            "warnings": warnings, "notes": notes}

# ---------------------------------------------------------------- validation


def _slug(s):
    return re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-") or "modele"


def accept(b):
    u = load_user()
    if b.get("kind") == "llm":
        repo = b["repo"]
        old = next((e for e in catalog.CATALOG if e["repo"] == b.get("replaces")), None)
        r = models.hf_repo(repo)
        if not r["quants"]:
            raise ValueError("ce dépôt ne contient aucun GGUF chargeable")
        tags = [t for t in (old or {}).get("tags", []) if t not in ("populaire", "vision")]
        if r["mmprojs"]:
            tags.append("vision")
        when = time.strftime("%d/%m/%Y")
        entry = {"repo": repo, "name": parse(repo)["label"], "tags": tags, "added": when,
                 "replaces": old["repo"] if old else None,
                 "blurb": (f"Remplace « {old['name']} » au catalogue (validé le {when}). " if old else f"Ajouté le {when}. ")
                 + "Description et licence : voir la page Hugging Face du dépôt."}
        u["llm"] = [e for e in u["llm"] if e["repo"] != repo] + [entry]
        _save_user(u)
        return {"ok": True, "repo": repo}
    e = b["entry"]
    pred = gen_catalog.find(e.get("replaces", ""))
    files = {}
    for f in e["files"]:
        repo, path = f.get("repo", "").strip(), f.get("file", "").strip()
        if not repo or not path:
            raise ValueError(f"{f.get('label', f['role'])} : dépôt et fichier obligatoires")
        if _info(repo).get("gated"):
            raise ValueError(f"{repo} est protégé (connexion Hugging Face requise) : le launcher ne peut pas le télécharger")
        hit = next((x for x in _tree(repo) if x["path"] == path), None)
        if not hit:
            raise ValueError(f"fichier introuvable sur Hugging Face : {repo}/{path}")
        files[f["role"]] = [repo, path, hit["size"]]
    if not any(r in files for r in ("diffusion_model", "model")):
        raise ValueError("il faut au moins le modèle de diffusion")
    ids = {x["id"] for x in gen_catalog.CATALOG}
    nid, n = _slug(e["name"]), 2
    while nid in ids:
        nid, n = f"{_slug(e['name'])}-{n}", n + 1
    dflt = {}
    for k, v in (e.get("defaults") or {}).items():
        if k in ("sampler", "scheduler", "negative_prompt"):
            dflt[k] = str(v or "")
        elif k == "negative":
            dflt[k] = bool(v)
        elif v not in (None, ""):
            dflt[k] = float(v) if k in ("cfg", "flow_shift") else int(float(v))
    entry = {"id": nid, "family": nid, "kind": pred["kind"] if pred else e.get("kind", "image"), "name": e["name"].strip(),
             "license": e.get("license", "").strip(), "tags": e.get("tags", []), "blurb": e.get("blurb", "").strip(),
             "files": files, "defaults": dflt, "features": e.get("features", []), "repo": e.get("repo", ""),
             "added": time.strftime("%d/%m/%Y"), "replaces": pred["id"] if pred else None}
    if e.get("notice", "").strip():
        entry["notice"] = e["notice"].strip()
    if e.get("server_args"):
        entry["server_args"] = e["server_args"]
    u["gen"].append(entry)
    _save_user(u)
    return {"ok": True, "id": nid}


def revert(kind, key):
    """Annule un choix validé : l'ancien modèle revient au catalogue."""
    u = load_user()
    if kind == "llm":
        u["llm"] = [e for e in u["llm"] if e["repo"] != key]
    elif kind == "ignored":
        u["ignored"] = [r for r in u["ignored"] if r != key]
    else:
        u["gen"] = [e for e in u["gen"] if e["id"] != key]
    _save_user(u)
    return {"ok": True}
