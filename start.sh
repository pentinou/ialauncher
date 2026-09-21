#!/usr/bin/env bash
# start.sh — met à jour IA Launcher (code git) et le moteur llama.cpp, puis lance
# l'interface.   ./start.sh [--port 9000] [--no-browser]   (options transmises à ialauncher.py)
# Une mise à jour qui échoue (hors ligne, modifications locales…) est signalée et
# n'empêche jamais le lancement.
set -u
cd "$(dirname "$0")" || exit 1

echo "== Code du launcher"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git pull --ff-only || echo "!! git pull impossible (hors ligne ou modifications locales) : code actuel conservé"
else
    echo "-- pas un dépôt git : code non mis à jour"
fi

echo "== Moteur llama.cpp"
python3 - <<'EOF'
from launcher import engine, jobs


class Console(jobs.Job):
    """Job qui affiche la progression dans le terminal au lieu de l'interface."""
    def set(self, progress=None, detail=None):
        super().set(progress, detail)
        if detail:
            print(f"\r   [{self.progress * 100:3.0f}%] {detail[:70]:<70}", end="", flush=True)

    def logline(self, s):
        print("\r" + " " * 80 + "\r   " + s.rstrip(), flush=True)


def main():
    b = engine.current_bin()
    if not b:
        print("-- aucun moteur installé : l'onglet Moteur de l'interface propose l'installation")
        return
    m = engine._marker(b)
    if not m.get("tag"):
        print(f"-- moteur hors launcher ({b}) : pas de mise à jour automatique")
        return
    try:
        latest = engine.stable_tag()
    except Exception as e:  # noqa: BLE001
        print(f"!! GitHub injoignable ({e}) : moteur {m['tag']} conservé")
        return
    if int(latest[1:]) <= int(m["tag"][1:]):
        print(f"-- à jour : {m['tag']} — {m['label']}")
        return
    print(f"-- {m['tag']} → {latest} — {m['label']}")
    method = "build" if m["variant"] == "cuda-local" else "prebuilt"
    try:
        r = engine.install(Console("engine", "mise à jour"), method, m["variant"], m["label"])
    except Exception as e:  # noqa: BLE001
        print(f"\n!! mise à jour échouée ({e}) : moteur {m['tag']} conservé")
        return
    print(f"\n-- installé : {r['tag']} — {r['label']}")


main()
EOF

echo "== Lancement"
exec python3 ialauncher.py "$@"
