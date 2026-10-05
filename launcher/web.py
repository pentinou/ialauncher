"""Serveur HTTP local : sert l'interface (dossier ui/) et une API JSON. Bibliothèque
standard uniquement (ThreadingHTTPServer). Le chat est relayé vers llama-server
en flux (SSE) pour éviter tout souci de CORS."""
import json
import mimetypes
import os
import sys
import threading
import time
import traceback
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import (hardware, engine, jobs, models, catalog, estimate, recommend, presets, paths, agents,
               sdcpp, music, gen_catalog, services, storage, civitai)
from .server import SERVER

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
_HW = {"data": None, "at": 0}
_LOCK = threading.Lock()
# Dernière requête reçue (hors /api/activity) : le portier à la demande (ondemand.py)
# s'en sert pour savoir si quelqu'un utilise encore le launcher avant de l'arrêter.
_ACTIVITE = {"derniere": time.time()}


def activite():
    """Ce qui empêche d'arrêter le launcher : tâches en cours (téléchargement,
    compilation, génération) et requêtes que llama-server est en train de traiter
    (agents qui lui parlent directement, sans passer par le launcher)."""
    taches = [j.label for j in jobs.running()]
    llm = 0
    if SERVER.state == "ready":
        try:
            with urllib.request.urlopen(SERVER.base_url() + "/slots", timeout=3) as r:
                llm = sum(1 for s in json.loads(r.read()) if s.get("is_processing"))
        except Exception:   # /slots désactivé ou serveur occupé à démarrer : on ne bloque pas
            pass
    return {"derniere_requete": _ACTIVITE["derniere"], "taches": taches, "llm_en_cours": llm,
            "occupe": bool(taches or llm)}


def hw_inventory(max_age=30):
    with _LOCK:
        if not _HW["data"] or time.time() - _HW["at"] > max_age:
            _HW["data"] = hardware.inventory()
            _HW["at"] = time.time()
        return _HW["data"]


def hw_for_estimate():
    """Inventaire + VRAM libre réellement vue par le moteur (--list-devices), qui
    peut différer de nvidia-smi (WSL2 notamment) : on garde la plus prudente."""
    hw = json.loads(json.dumps(hw_inventory()))
    live = hardware.live()
    # Serveur en cours : la mémoire qu'il occupe est celle du modèle qu'on estime — on
    # repart de l'état d'avant lancement, sinon tout « ne tient plus ».
    if SERVER.baseline and SERVER.proc is not None and SERVER.proc.poll() is None:
        live = SERVER.baseline
    for g in hw["gpus"]:
        for l in live["gpus"]:
            if l["index"] == g["index"]:
                g["vram_used"], g["vram_free"] = l["vram_used"], l["vram_free"]
    hw["ram"] = live["ram"]
    hw["disk"] = live["disk"]
    return hw


def _model_profile(spec):
    """spec = {"id": modèle local} ou {"repo", "file", "size"} (distant)."""
    if spec.get("id"):
        m = models.find(spec["id"])
        if not m:
            raise ValueError("modèle introuvable")
        p = models.profile_local(m["path"])
        return p, m, True
    if spec.get("repo") and spec.get("file"):
        p = models.profile_remote(spec["repo"], spec["file"], int(spec.get("size") or 0))
        m = {"name": spec["file"], "path": "", "size": p["file_size"], "repo": spec["repo"], "file": spec["file"]}
        return p, m, False
    raise ValueError("modèle non précisé")


def _draft_profile(cfg):
    d = cfg.get("draft_model")
    if cfg.get("spec_type", "").startswith("draft") and d and os.path.exists(d):
        try:
            return models.profile_local(d)
        except Exception:
            return None
    return None


def _estimate_ctx(spec, cfg):
    p, m, present = _model_profile(spec)
    hw = hw_for_estimate()
    cfg = dict(cfg)
    cfg["model_present"] = present
    cfg["models_dir_bytes"] = models.models_dir_bytes()
    if m.get("mmproj") and not p.get("mmproj_bytes"):
        try:
            cfg["mmproj_size"] = os.path.getsize(m["mmproj"])
        except OSError:
            pass
    return p, m, hw, cfg


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence
        pass

    # ---------------------------------------------------------------- utilitaires
    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _file(self, path):
        p = (UI_DIR / path).resolve()
        if not str(p).startswith(str(UI_DIR)) or not p.is_file():
            self.send_error(404)
            return
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(p))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path != "/api/activity":
            _ACTIVITE["derniere"] = time.time()
        try:
            self._get(u.path, q)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def _get(self, path, q):
        if path.startswith("/proxy/"):
            return self._proxy("GET", path[len("/proxy"):], b"")
        if path == "/":
            return self._file("index.html")
        if path.startswith("/static/"):
            return self._file(path[len("/static/"):])
        if path == "/api/hardware":
            return self._json(hw_inventory(max_age=0 if q.get("refresh") else 30))
        if path == "/api/activity":
            return self._json(activite())
        if path == "/api/live":
            return self._json(hardware.live())
        if path == "/api/engine":
            return self._json(engine.status())
        if path == "/api/jobs":
            return self._json([j.to_dict() for j in jobs.all_jobs()[:20]])
        if path.startswith("/api/jobs/"):
            j = jobs.get(path.split("/")[3])
            return self._json(j.to_dict() if j else {"error": "inconnu"}, 200 if j else 404)
        if path == "/api/models":
            return self._json({"models": models.scan(), "dirs": [{"label": l, "path": str(p), "kind": k} for l, p, k in models.source_dirs()],
                               "models_dir": str(paths.models_dir()), "models_dir_bytes": models.models_dir_bytes()})
        if path == "/api/models/profile":
            m = models.find(q.get("id", ""))
            if not m:
                return self._json({"error": "modèle introuvable"}, 404)
            p = models.profile_local(m["path"])
            return self._json({"profile": _slim(p), "model": m})
        if path == "/api/catalog":
            return self._json({"entries": catalog.CATALOG, "tags": catalog.TAG_HELP})
        if path == "/api/hf/repo":
            return self._json(models.hf_repo(q["repo"]))
        if path == "/api/hf/search":
            return self._json(models.hf_search(q.get("q", ""), int(q.get("limit", 20))))
        if path == "/api/hf/profile":
            p = models.profile_remote(q["repo"], q["file"], int(q.get("size") or 0))
            return self._json({"profile": _slim(p)})
        if path == "/api/presets":
            return self._json(presets.list_all())
        if path == "/api/server":
            return self._json(SERVER.status())
        if path == "/api/agents":
            st = SERVER.status()
            port = st["port"] or int(q.get("port", 8080))
            alias = st.get("alias") or q.get("alias") or "modele"
            ctx = int((st.get("cfg") or {}).get("ctx") or q.get("ctx") or 32768)
            base = f"http://127.0.0.1:{port}"
            proxy = f"http://127.0.0.1:{self.server.server_address[1]}/proxy"
            return self._json({"installed": agents.installed(), "base_url": base, "alias": alias, "ctx": ctx,
                               "specs": agents.specs(base, alias, ctx, proxy, st.get("vision", False)),
                               "ready": st["state"] == "ready"})
        if path.startswith("/outputs/"):
            return self._output(path[len("/outputs/"):])
        if path == "/api/gen/status":
            return self._json({"sd": sdcpp.status(), "music": music.status()})
        if path == "/api/gen/models":
            return self._json({"models": sdcpp.models(q.get("kind", "image")), "tags": gen_catalog.TAG_HELP,
                               "webui": [str(w) for w in sdcpp.webui_installs()]})
        if path == "/api/gen/loras":
            return self._json(sdcpp.loras(q.get("model", "")))
        if path == "/api/gen/outputs":
            return self._json(sdcpp.outputs(q.get("kind", "image")))
        if path == "/api/gen/outputs/dir":
            return self._json(sdcpp.outputs_info())
        if path == "/api/storage":
            return self._json(storage.scan())
        if path == "/api/civitai/search":
            return self._json(civitai.search(q.get("q", ""), q.get("type", "Checkpoint"), q.get("family", ""),
                                             q.get("sort", "Most Downloaded"), q.get("nsfw") == "1", q.get("cursor", "")))
        if path == "/api/paths":
            return self._json({"home": str(paths.home()), "models": str(paths.models_dir()),
                               "engines": str(paths.engines_dir()), "logs": str(paths.logs_dir())})
        self.send_error(404)

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        u = urlparse(self.path)
        _ACTIVITE["derniere"] = time.time()
        try:
            if u.path.startswith("/proxy/"):
                n = int(self.headers.get("Content-Length") or 0)
                return self._proxy("POST", u.path[len("/proxy"):] + ("?" + u.query if u.query else ""), self.rfile.read(n))
            body = self._body()
            self._post(u.path, body)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def _post(self, path, b):
        if path == "/api/engine/install":
            if jobs.running("engine"):
                return self._json({"error": "une installation est déjà en cours"}, 409)
            j = jobs.start("engine", f"Installation du moteur — {b.get('label', '')}",
                           lambda job: engine.install(job, b["method"], b.get("variant", ""), b.get("label", "")))
            return self._json({"job": j.to_dict()})
        if path == "/api/engine/custom":
            p = b.get("path", "")
            if not os.path.isfile(p):
                return self._json({"error": "fichier introuvable"}, 400)
            engine.set_bin(p)
            return self._json(engine.status())
        if path == "/api/engine/select":
            engine.set_bin(b["path"])
            return self._json(engine.status())
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            j = jobs.get(path.split("/")[3])
            if j:
                j.cancel_requested = True
            return self._json({"ok": bool(j)})
        if path == "/api/models/dirs":
            d = b.get("path", "")
            if not os.path.isdir(d):
                return self._json({"error": "dossier introuvable"}, 400)
            models.add_dir(d)
            return self._json({"ok": True})
        if path == "/api/hf/download":
            j = jobs.start("download", f"Téléchargement — {Path(b['file']).name}",
                           lambda job: models.download_hf(job, b["repo"], b["file"], int(b.get("size") or 0),
                                                          b.get("mmproj"), b.get("draft")))
            return self._json({"job": j.to_dict()})
        if path == "/api/hf/analyze":
            hw = hw_for_estimate()
            j = jobs.start("analyze", f"Analyse — {b['repo']}",
                           lambda job: models.analyze_repo(job, b["repo"], hw, b.get("goal", "balanced")))
            return self._json({"job": j.to_dict()})
        if path == "/api/estimate":
            p, m, hw, cfg = _estimate_ctx(b["model"], b.get("cfg") or {})
            e = estimate.estimate(p, cfg, hw, _draft_profile(cfg))
            return self._json({"estimate": e, "profile": _slim(p), "model": m})
        if path == "/api/recommend":
            p, m, hw, cfg = _estimate_ctx(b["model"], b.get("cfg") or {})
            r = recommend.recommend(p, hw, b.get("goal", "balanced"), _draft_profile(cfg), cfg)
            r["profile"] = _slim(p)
            return self._json(r)
        if path == "/api/fit":
            return self._json(_run_fit(b))
        if path == "/api/cmdline":
            from .server import build_args, command_line, model_alias
            m = models.find(b["model"].get("id", "")) if b.get("model") else None
            path_ = m["path"] if m else "<modèle.gguf>"
            mm = (m or {}).get("mmproj") or None
            return self._json({"cmd": command_line(engine.current_bin() or "llama-server",
                                                    build_args(b.get("cfg") or {}, path_, mm, alias=model_alias(m or {})))})
        if path == "/api/presets":
            return self._json(presets.save(b["name"], b["model"], b["cfg"], b.get("id")))
        if path == "/api/presets/delete":
            presets.delete(b["id"])
            return self._json({"ok": True})
        if path == "/api/server/start":
            m = models.find(b["model"]["id"]) if b.get("model", {}).get("id") else None
            if not m:
                return self._json({"error": "modèle introuvable (il doit être sur le disque)"}, 400)
            services.claim_gpu("llama")
            SERVER.start(b["cfg"], m, b.get("mmproj"))
            return self._json(SERVER.status())
        if path == "/api/server/stop":
            SERVER.stop()
            return self._json(SERVER.status())
        if path == "/api/unload":
            # tout décharger : les générations en cours sont annulées, sinon elles relanceraient leur serveur
            for j in jobs.running("gen"):
                j.cancel_requested = True
            return self._json({"stopped": services.claim_gpu("aucun")})
        if path == "/api/chat":
            return self._chat(b)
        # ---- image / vidéo / musique
        if path == "/api/gen/engine/install":
            if jobs.running("sd-engine"):
                return self._json({"error": "une installation est déjà en cours"}, 409)
            j = jobs.start("sd-engine", f"Installation de stable-diffusion.cpp — {b.get('label', '')}",
                           lambda job: sdcpp.install(job, b["method"], b.get("variant", ""), b.get("label", "")))
            return self._json({"job": j.to_dict()})
        if path == "/api/gen/download":
            e = gen_catalog.find(b["id"])
            if not e:
                return self._json({"error": "modèle inconnu"}, 404)
            j = jobs.start("gen-download", f"Téléchargement — {e['name']}", lambda job: sdcpp.download_model(job, b["id"]))
            return self._json({"job": j.to_dict()})
        if path == "/api/gen/run":
            if jobs.running("gen"):
                return self._json({"error": "une génération est déjà en cours"}, 409)
            fn = music.generate if b.get("kind") == "music" else sdcpp.generate
            labels = {"image": "Image", "video": "Vidéo", "music": "Musique"}
            j = jobs.start("gen", f"{labels.get(b.get('kind'), 'Génération')} — {b.get('prompt', '')[:50]}", lambda job: fn(job, b))
            return self._json({"job": j.to_dict()})
        if path == "/api/gen/stop":
            (services.MUSIC if b.get("service") == "music" else services.SD).stop()
            return self._json({"ok": True})
        if path == "/api/gen/dirs":
            if not os.path.isdir(b.get("path", "")):
                return self._json({"error": "dossier introuvable"}, 400)
            sdcpp.add_dir(b["path"])
            return self._json({"ok": True})
        if path == "/api/gen/outputs/dir":
            return self._json(sdcpp.set_outputs_dir(b.get("path", "")))
        if path == "/api/gen/outputs/open":
            sdcpp.open_outputs(b.get("kind", "image"))
            return self._json({"ok": True})
        if path == "/api/gen/outputs/delete":
            sdcpp.delete_output(b["kind"], b["name"])
            return self._json({"ok": True})
        if path == "/api/storage/delete":
            storage.delete(b["id"])
            return self._json({"ok": True})
        if path == "/api/civitai/token":
            civitai.set_token(b.get("token", ""))
            return self._json({"ok": True})
        if path == "/api/civitai/download":
            j = jobs.start("gen-download", f"Civitai — {b['model']['name']}", lambda job: civitai.download_file(job, b["type"], b))
            return self._json({"job": j.to_dict()})
        if path == "/api/music/install":
            if jobs.running("music-install"):
                return self._json({"error": "une installation est déjà en cours"}, 409)
            j = jobs.start("music-install", "Installation d'ACE-Step", music.install)
            return self._json({"job": j.to_dict()})
        if path == "/api/agents/test":
            if SERVER.state != "ready":
                return self._json({"ok": False, "detail": "le serveur n'est pas prêt"})
            return self._json(agents.test_tool_call(SERVER.base_url()))
        if path == "/api/agents/scripts":
            st = SERVER.status()
            base = SERVER.base_url() or f"http://127.0.0.1:{b.get('port', 8080)}"
            proxy = f"http://127.0.0.1:{self.server.server_address[1]}/proxy"
            return self._json(agents.write_scripts(base, st.get("alias") or b.get("alias", "modele"),
                                                   int((st.get("cfg") or {}).get("ctx") or b.get("ctx") or 32768), proxy,
                                                   st.get("vision", False)))
        if path == "/api/agents/open":
            st = SERVER.status()
            base = SERVER.base_url() or f"http://127.0.0.1:{b.get('port', 8080)}"
            proxy = f"http://127.0.0.1:{self.server.server_address[1]}/proxy"
            sc = agents.write_scripts(base, st.get("alias") or "modele", int((st.get("cfg") or {}).get("ctx") or 32768), proxy,
                                      st.get("vision", False))
            s = sc[b["tool"]]
            return self._json({"ok": True, "terminal": agents.open_terminal(s["sh"], s["cmd"], b.get("cwd"))})
        self.send_error(404)

    def _output(self, rel):
        """Fichier généré (image, vidéo, musique), avec les requêtes « Range » dont les
        lecteurs audio/vidéo des navigateurs ont besoin pour se déplacer dans le fichier."""
        root = paths.outputs_dir().resolve()
        p = (root / rel).resolve()
        if root not in p.parents or not p.is_file():
            self.send_error(404)
            return
        size = p.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, z = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(z))
            end = int(z) if a and z else size - 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", mimetypes.guess_type(str(p))[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ---------------------------------------------------------------- relais agents
    def _proxy(self, method, path, body):
        """Fait suivre la requête à llama-server en corrigeant ce que les outils envoient
        de non standard. Claude Code, par exemple, glisse un message « system » AU MILIEU
        de la conversation ; les gabarits Qwen exigent qu'il soit en tête et refusent la
        requête. On le fusionne dans le champ « system » de tête. Le flux (SSE) est
        renvoyé tel quel."""
        if SERVER.state != "ready":
            return self._json({"type": "error", "error": {"type": "overloaded_error", "message": "le serveur llama.cpp n'est pas prêt"}}, 503)
        if method == "POST" and path.split("?")[0] in ("/v1/messages", "/v1/messages/count_tokens"):
            body = agents.normalize_anthropic(body)
        hdrs = {k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "transfer-encoding", "connection", "accept-encoding")}
        hdrs["Content-Length"] = str(len(body))
        req = urllib.request.Request(SERVER.base_url() + path, data=body if method == "POST" else None,
                                     headers=hdrs, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        ctype = resp.headers.get("Content-Type", "application/json")
        self.send_response(resp.status)
        self.send_header("Content-Type", ctype)
        for k in ("x-request-id", "anthropic-version", "request-id"):
            if resp.headers.get(k):
                self.send_header(k, resp.headers[k])
        if "text/event-stream" in ctype:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")   # derrière nginx : pas de mise en tampon du flux
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                with resp:
                    while True:
                        chunk = resp.readline()
                        if not chunk:
                            break
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        data = resp.read()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------------- chat (relais SSE)
    def _chat(self, b):
        if SERVER.state != "ready":
            return self._json({"error": "le serveur n'est pas prêt"}, 409)
        payload = json.dumps({"messages": b.get("messages", []), "stream": True,
                              **{k: v for k, v in b.items() if k not in ("messages",)}}).encode()
        req = urllib.request.Request(SERVER.base_url() + "/v1/chat/completions", data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            return self._json({"error": e.read().decode("utf-8", "replace")[:500]}, e.code)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            with resp:
                while True:
                    chunk = resp.readline()
                    if not chunk:
                        break
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        self.wfile.write(b"0\r\n\r\n")


def _slim(p):
    """Le profil sans le détail des couches (lourd et inutile à l'interface)."""
    d = {k: v for k, v in p.items() if k != "layers"}
    d["layer_bytes"] = [L["bytes"] for L in p["layers"]]
    d["k_elems"] = sum(L["k_elems"] for L in p["layers"] if L["kind"] == "attn")
    d["v_elems"] = sum(L["v_elems"] for L in p["layers"] if L["kind"] == "attn")
    d["layer_kinds"] = [L["kind"] for L in p["layers"]]
    return d


def _run_fit(b):
    """Deuxième avis : llama-fit-params (livré avec le moteur) projette la mémoire
    avec les mêmes règles que llama-server, sans charger les poids (≈ 1-2 s)."""
    bin_path = engine.current_bin()
    if not bin_path:
        return {"error": "aucun moteur"}
    fit = Path(bin_path).with_name("llama-fit-params" + (".exe" if sys.platform == "win32" else ""))
    if not fit.exists():
        return {"error": "llama-fit-params absent de ce moteur"}
    m = models.find(b["model"].get("id", ""))
    if not m:
        return {"error": "le modèle doit être sur le disque"}
    from .server import build_args
    # on retire les options propres au serveur, inconnues de fit-params
    clean, skip = [], False
    for a in build_args(b.get("cfg") or {}, m["path"]):
        if skip:
            skip = False
            continue
        if a in ("--host", "--port", "--alias", "--cache-ram", "-np", "--reasoning", "--reasoning-budget"):
            skip = True
            continue
        clean.append(a)
    out = engine.run_bin(str(fit), [*clean, "-lv", "4"], timeout=60)
    proj = [l for l in out.splitlines() if "breakdown" in l or "projected" in l or "fit_impl" in l or "fitted" in l]
    fitted = [l for l in out.splitlines() if l.startswith("-")]
    return {"lines": [l.split(" I ", 1)[-1].split(" W ", 1)[-1] for l in proj], "fitted": fitted[-1] if fitted else "",
            "raw": out[-4000:]}


def _open_browser(url):
    """Sous WSL, xdg-open ne connaît pas le navigateur Windows : passer par cmd.exe."""
    import shutil
    import subprocess
    import webbrowser
    if "microsoft" in os.uname().release.lower() and shutil.which("cmd.exe"):
        subprocess.Popen(["cmd.exe", "/c", "start", "", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    webbrowser.open(url)


def serve(port=8765, open_browser=True, host="127.0.0.1"):
    from .server import pick_port
    free = pick_port(port, host)
    if free != port:
        print(f"port {port} occupé (une autre instance ?) → {free}", flush=True)
    port = free
    from .server import kill_orphan
    orphan = kill_orphan()
    if orphan:
        print(f"un llama-server d'une session précédente (pid {orphan}) a été arrêté", flush=True)
    for s in (services.SD, services.MUSIC):
        if s.kill_orphan():
            print(f"un {s.name} d'une session précédente a été arrêté", flush=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    print(f"IA Launcher : {url}   (Ctrl+C pour quitter)", flush=True)
    if host != "127.0.0.1":
        print(f"⚠ écoute sur {host} : le launcher n'a PAS d'authentification ; limitez l'accès à ce port "
              "(pare-feu) au seul proxy qui en a une, ex. le SSO YunoHost", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: _open_browser(url)).start()
    import signal

    def _quit(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _quit)   # kill « propre » → on arrête aussi llama-server
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SERVER.stop()
        services.SD.stop()
        services.MUSIC.stop()
