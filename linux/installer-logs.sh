#!/bin/sh
# Range les journaux d'IA Launcher dans /var/log/ialauncher, avec roulement quotidien par
# logrotate (7 jours, compressés, au plus 20 Mo par fichier avant roulement).
# À lancer une fois, en root :   sudo sh linux/installer-logs.sh [utilisateur]
# Puis relancer le launcher : il écrit dans /var/log/ialauncher dès qu'il le trouve.
set -e
U=${1:-${SUDO_USER:-$(id -un)}}
[ "$(id -u)" = 0 ] || { echo "à lancer avec sudo"; exit 1; }
id "$U" >/dev/null
command -v logrotate >/dev/null || { echo "logrotate absent : sudo apt install logrotate"; exit 1; }
install -d -o "$U" -g "$U" -m 755 /var/log/ialauncher
sed "s/@USER@/$U/g" "$(dirname "$0")/logrotate-ialauncher" > /etc/logrotate.d/ialauncher
chmod 644 /etc/logrotate.d/ialauncher
logrotate -d /etc/logrotate.d/ialauncher >/dev/null 2>&1 || { echo "configuration logrotate refusée :"; logrotate -d /etc/logrotate.d/ialauncher; exit 1; }
echo "OK : /var/log/ialauncher (propriétaire $U), roulement dans /etc/logrotate.d/ialauncher"
