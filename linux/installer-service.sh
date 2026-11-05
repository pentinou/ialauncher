#!/bin/sh
# Démarre IA Launcher avec la machine (service systemd) : c'est le portier (ondemand.py)
# qui tourne en permanence ; il lance le launcher à la première visite et l'arrête après
# 10 min sans usage (--idle 0 : jamais).
#
#   sudo sh linux/installer-service.sh                       # portail sur cette machine
#   sudo sh linux/installer-service.sh --allow 192.168.1.10  # portail sur une autre machine
#   sh linux/installer-service.sh --print …                  # montrer le service sans l'installer
#
# Options : --allow IP[,IP…] (adresses du portail ; 127.0.0.1 toujours accepté)
#           --port 8765      --idle 600 (secondes, 0 = jamais)      --user NOM
# Enlever : sudo systemctl disable --now ialauncher && sudo rm /etc/systemd/system/ialauncher.service
set -e
ICI=$(cd "$(dirname "$0")/.." && pwd)
ALLOW=""; PORT=8765; IDLE=600; U=${SUDO_USER:-$(id -un)}; PRINT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --allow) ALLOW="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --idle) IDLE="$2"; shift 2 ;;
        --user) U="$2"; shift 2 ;;
        --print) PRINT=1; shift ;;
        *) echo "option inconnue : $1"; exit 1 ;;
    esac
done
AUTORISEES="127.0.0.1${ALLOW:+,$ALLOW}"
PY=$(command -v python3) || { echo "python3 introuvable"; exit 1; }

UNIT="[Unit]
Description=IA Launcher (portier à la demande, port $PORT)
After=network-online.target
Wants=network-online.target

[Service]
User=$U
WorkingDirectory=$ICI
ExecStart=$PY \"$ICI/ondemand.py\" --port $PORT --idle $IDLE --allow $AUTORISEES
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target"

if [ "$PRINT" = 1 ]; then echo "$UNIT"; exit 0; fi
[ "$(id -u)" = 0 ] || { echo "à lancer avec sudo (ou --print pour voir le service)"; exit 1; }
command -v systemctl >/dev/null && [ -d /run/systemd/system ] || {
    echo "systemd n'est pas actif ici. Sous WSL : utilisez windows/installer-portier.ps1"
    echo "(ou ajoutez [boot] systemd=true dans /etc/wsl.conf, puis wsl --shutdown)."; exit 1; }
id "$U" >/dev/null
echo "$UNIT" > /etc/systemd/system/ialauncher.service
systemctl daemon-reload
systemctl enable --now ialauncher
sleep 2
systemctl --no-pager --lines=3 status ialauncher || true
echo "OK : http://127.0.0.1:$PORT (adresses acceptées : $AUTORISEES)"
