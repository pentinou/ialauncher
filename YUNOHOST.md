# IA Launcher derrière YunoHost (branche `yunohost`)

Cette branche = `main` + ce qu'il faut pour ouvrir IA Launcher depuis le portail
YunoHost, à la demande :

- `ondemand.py` — **portier** : écoute sur le port 8765 (réseau local), démarre le
  launcher à la première visite, lui transmet le trafic et l'arrête (avec llama-server,
  sd-server, ACE-Step) après 10 min sans activité. « Activité » : une requête reçue
  (l'interface ouverte en envoie toutes les 3 s), une connexion en cours, une tâche
  (génération, téléchargement…) ou une requête que llama-server traite pour un agent —
  voir la route `/api/activity` du launcher.
- `windows/installer-portier.ps1` — tâche planifiée Windows qui démarre WSL et le portier
  au démarrage du PC, sans ouvrir de session (à lancer une fois en administrateur).
- Côté serveur : la tuile est l'app maison `pcproxy` (dépôt homelab), qui proxifie
  `ia.gingerfox.run` vers `http://192.168.1.20:8765` derrière le SSO, sans transmettre
  d'identifiants, et propose « Réveiller le PC » (Wake-on-LAN) quand le PC ne répond pas.

Sécurité : ni le portier ni le launcher n'ont d'authentification. Le pare-feu Hyper-V de
WSL (mode réseau `mirrored`) n'ouvre le port 8765 qu'à l'adresse du serveur YunoHost :

    New-NetFirewallHyperVRule -Name "WSL-projets-YunoHost" -Direction Inbound `
      -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8765 `
      -RemoteAddresses 192.168.1.179 -Action Allow

Le portier tourne depuis un dossier de travail git dédié, toujours sur cette branche
(`git worktree add ~/ialauncher-portier yunohost`) : on peut développer sur `main` dans
`~/ialauncher` sans le perturber. Mettre à jour : `cd ~/ialauncher-portier && git merge main`.
