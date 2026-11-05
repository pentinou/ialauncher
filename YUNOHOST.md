# IA Launcher derrière YunoHost (branche `yunohost`)

Cette branche = `main` + `ynh/`, le paquet YunoHost de la tuile (générique : voir le README,
« Installation et démarrage », étape 3) + ce fichier, qui note **les réglages de chez moi**.

## Mon installation

- **Serveur de calcul** : le PC Windows (192.168.1.20), launcher dans WSL (`Ubuntu-24.04`,
  mode réseau `mirrored`). Le portier tourne depuis un dossier de travail git dédié, toujours
  sur cette branche : `git worktree add ~/ialauncher-portier yunohost` (on développe sur `main`
  dans `~/ialauncher` sans le perturber). Démarrage automatique, dans `~/ialauncher-portier` :

      powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w windows/installer-portier.ps1)" \
        -Dossier "$PWD" -Distro Ubuntu-24.04 -Utilisateur pentinou -Autorisees 192.168.1.179,192.168.1.20

  (192.168.1.20 : le PC lui-même, pour l'ouvrir par son adresse réseau.)
- **Portail** : YunoHost `gingerfox.run` (192.168.1.179) ; tuile `ia.gingerfox.run` →
  `http://192.168.1.20:8765`, avec le bouton « Réveiller le PC » (MAC de la carte Marvell AQtion).
  Installée à l'origine avec le paquet `pcproxy` de mon dépôt homelab, dont `ynh/` est la
  version générique.
- **Journaux** : `/var/log/ialauncher` (`sudo sh linux/installer-logs.sh`), onglet Journaux.

## Déployer une modification

Commit + push de `main`, puis `cd ~/ialauncher-portier && git merge main` + push de
`yunohost` ; vérifier `curl -s http://127.0.0.1:8765/api/activity` (`"occupe": false`), puis
arrêter le launcher (`kill -TERM` du `ialauncher.py --port 8770`) : le portier le relance avec
le nouveau code à la visite suivante. Un changement de `ondemand.py` demande de relancer la
tâche planifiée (ou le PC).

## Sécurité

Ni le portier ni le launcher n'ont d'authentification : la connexion est celle du SSO
YunoHost, et le portier n'accepte que les adresses de `--allow`. Le pare-feu Hyper-V de WSL
laisse entrer le port 8765 (règle `WSL-projets-YunoHost` créée à la main le 2026-10-03 ; le
script crée `IA-Launcher-portier`, équivalente, si on le relance avec `-Autorisees` ; filtrer
l'adresse source dans le pare-feu bloquait aussi Windows → WSL, d'où le filtrage dans le portier).
