#!/usr/bin/env python3
"""Portier à la demande d'IA Launcher.

Écoute à la place du launcher (par défaut 0.0.0.0:8765), le démarre à la première
requête, lui transmet tout le trafic, et l'arrête — avec llama-server, sd-server et
ACE-Step — après --idle secondes sans activité : aucune requête reçue (l'interface
ouverte en envoie toutes les 3 s), aucune connexion en cours, aucune tâche
(génération, téléchargement, compilation) et aucune requête que llama-server serait
en train de traiter pour un agent (voir /api/activity).

    python3 ondemand.py [--listen 0.0.0.0] [--port 8765] [--idle 600]

Pensé pour être joint par un seul proxy authentifié (SSO YunoHost) : comme le
launcher, il n'a PAS d'authentification. --allow limite les adresses acceptées (les
autres reçoivent un refus 403) ; un pare-feu peut s'y ajouter.
"""
import argparse
import http.client
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ICI = Path(__file__).resolve().parent
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}

PAGE_DEMARRAGE = """<!doctype html><meta charset="utf-8"><title>IA Launcher : démarrage</title>
<meta http-equiv="refresh" content="2">
<body style="font:16px system-ui;max-width:34em;margin:4em auto;padding:0 1em;color:#ddd;background:#0f1115">
<h1>Démarrage d'IA Launcher…</h1><p>Le launcher s'éteint après {idle} minutes sans utilisation pour
libérer le PC ; il redémarre à la demande. Cette page se recharge toute seule.</p>"""


class Launcher:
    """Le processus IA Launcher, démarré à la demande et arrêté après inactivité."""

    def __init__(self, idle):
        self.idle = idle
        self.proc = None
        self.port = None
        self.pret = False
        self.verrou = threading.Lock()
        self.derniere = time.time()
        self.connexions = 0
        self.verrou_cx = threading.Lock()

    def compter(self, delta):
        with self.verrou_cx:
            self.connexions += delta

    def vivant(self):
        return self.proc is not None and self.proc.poll() is None

    def demarrer(self):
        with self.verrou:
            if self.vivant():
                return
            self.pret, self.port = False, None
            print(f"[{time.strftime('%T')}] démarrage d'IA Launcher", flush=True)
            # port 0 impossible (il choisit le premier libre à partir de celui demandé) :
            # on lit le port réel dans sa première ligne « IA Launcher : http://127.0.0.1:NNNN »
            self.proc = subprocess.Popen(
                [sys.executable, str(ICI / "ialauncher.py"), "--port", "8770", "--no-browser"],
                cwd=str(ICI), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                start_new_session=True)
            threading.Thread(target=self._lire_sortie, args=(self.proc,), daemon=True).start()

    def _lire_sortie(self, proc):
        for ligne in proc.stdout:
            sys.stdout.write("  │ " + ligne)
            sys.stdout.flush()
            m = re.search(r"IA Launcher : http://127\.0\.0\.1:(\d+)", ligne)
            if m and proc is self.proc:
                self.port = int(m.group(1))

    def attendre(self, delai=60):
        """Attend que le launcher réponde ; False si toujours pas prêt."""
        fin = time.time() + delai
        while time.time() < fin and self.vivant():
            if self.port:
                try:
                    c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
                    c.request("GET", "/api/activity")
                    ok = c.getresponse().status == 200
                    c.close()
                    if ok:
                        self.pret = True
                        return True
                except OSError:
                    pass
            time.sleep(0.5)
        return False

    def arreter(self, raison):
        with self.verrou:
            if not self.vivant():
                return
            print(f"[{time.strftime('%T')}] arrêt d'IA Launcher ({raison})", flush=True)
            # SIGTERM : le launcher arrête lui-même llama-server, sd-server et ACE-Step
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc, self.pret, self.port = None, False, None

    def occupe(self):
        """Le launcher signale-t-il une activité récente ou en cours ?"""
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", "/api/activity")
            a = json.loads(c.getresponse().read())
            c.close()
        except (OSError, ValueError):
            return True   # dans le doute, on n'arrête pas
        return a.get("occupe") or time.time() - a.get("derniere_requete", 0) < self.idle

    def surveiller(self):
        while True:
            time.sleep(30)
            if (self.vivant() and self.pret and self.connexions == 0
                    and time.time() - self.derniere > self.idle and not self.occupe()):
                self.arreter(f"{self.idle} s sans activité")


def fabriquer_handler(lanceur, autorisees):
    class Portier(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _relayer(self):
            if autorisees and self.client_address[0] not in autorisees:
                print(f"[{time.strftime('%T')}] refusé : {self.client_address[0]}", flush=True)
                return self._repondre(403, "text/plain; charset=utf-8", "Accès refusé".encode())
            lanceur.derniere = time.time()
            if not lanceur.vivant() or not lanceur.pret:
                lanceur.demarrer()
                navigateur = self.command == "GET" and "text/html" in self.headers.get("Accept", "")
                # une page affichée tout de suite plutôt qu'une attente muette ; les appels
                # d'API, eux, attendent que le launcher soit prêt
                if navigateur and not lanceur.attendre(delai=1):
                    return self._repondre(200, "text/html; charset=utf-8",
                                          PAGE_DEMARRAGE.format(idle=lanceur.idle // 60).encode())
                if not lanceur.attendre():
                    return self._repondre(502, "text/plain; charset=utf-8", "IA Launcher ne démarre pas".encode())
            n = int(self.headers.get("Content-Length") or 0)
            corps = self.rfile.read(n) if n else None
            entetes = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            lanceur.compter(+1)
            try:
                c = http.client.HTTPConnection("127.0.0.1", lanceur.port, timeout=3600)
                c.request(self.command, self.path, body=corps, headers=entetes)
                r = c.getresponse()
                self.send_response(r.status, r.reason)
                for k, v in r.getheaders():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                flux = r.getheader("Content-Length") is None
                if flux:   # SSE, réponses découpées : relayées au fil de l'eau
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while True:
                        bloc = r.read1(65536)
                        if not bloc:
                            break
                        self.wfile.write(f"{len(bloc):x}\r\n".encode() + bloc + b"\r\n")
                        self.wfile.flush()
                        lanceur.derniere = time.time()
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    donnees = r.read()
                    self.send_header("Content-Length", str(len(donnees)))
                    self.end_headers()
                    self.wfile.write(donnees)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except OSError as e:
                self._repondre(502, "text/plain; charset=utf-8", f"IA Launcher injoignable : {e}".encode())
            finally:
                lanceur.compter(-1)
                lanceur.derniere = time.time()

        def _repondre(self, code, ctype, data):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _relayer

    return Portier


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", default="0.0.0.0", help="adresse d'écoute (défaut 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8765, help="port d'écoute (défaut 8765)")
    ap.add_argument("--idle", type=int, default=600, help="secondes d'inactivité avant l'arrêt (défaut 600)")
    ap.add_argument("--allow", default="",
                    help="adresses IP acceptées, séparées par des virgules (défaut : toutes)")
    a = ap.parse_args()
    autorisees = {x.strip() for x in a.allow.split(",") if x.strip()}

    lanceur = Launcher(a.idle)
    threading.Thread(target=lanceur.surveiller, daemon=True).start()
    httpd = ThreadingHTTPServer((a.listen, a.port), fabriquer_handler(lanceur, autorisees))
    httpd.daemon_threads = True
    print(f"portier IA Launcher : http://{a.listen}:{a.port} (arrêt après {a.idle} s d'inactivité ; "
          f"adresses acceptées : {', '.join(sorted(autorisees)) or 'toutes'})", flush=True)

    def quitter(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, quitter)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        lanceur.arreter("arrêt du portier")


if __name__ == "__main__":
    main()
