#!/usr/bin/env python3
"""IA Launcher — point d'entrée.  python3 ialauncher.py [--port 8765] [--no-browser]"""
import argparse
import sys

if sys.version_info < (3, 10):
    sys.exit("Python 3.10 ou plus récent est nécessaire.")

from launcher import web  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Launcher d'IA locales (llama.cpp)")
    ap.add_argument("--port", type=int, default=8765, help="port de l'interface (défaut 8765)")
    ap.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    ap.add_argument("--host", default="127.0.0.1",
                    help="adresse d'écoute (défaut 127.0.0.1 ; 0.0.0.0 = réseau local, sans authentification)")
    a = ap.parse_args()
    web.serve(a.port, not a.no_browser, a.host)
