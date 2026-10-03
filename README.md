# IA Launcher

Un lanceur d'IA locales **didactique** : il installe le moteur ([llama.cpp](https://github.com/ggml-org/llama.cpp)),
propose des modèles, **montre où va la mémoire** (VRAM / RAM / SSD) à mesure que vous
changez les réglages, propose une **configuration optimale pour votre machine** et lance
le serveur — avec un petit chat pour vérifier que tout répond, et de quoi brancher
**Claude Code, Codex ou OpenCode** sur le modèle local.

Trois autres onglets génèrent des **images**, des **vidéos** et de la **musique** : le
launcher installe les moteurs ([stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)
et [ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5)), télécharge les modèles, et
tout se règle dans son interface — pas de ComfyUI, pas de nœuds.

Python 3.10+ **sans aucune dépendance** (bibliothèque standard uniquement). Interface web
vanilla (HTML/JS/CSS), servie uniquement sur `127.0.0.1`. Linux, WSL2, Windows, macOS.

```bash
git clone https://github.com/pentinou/ialauncher.git
cd ialauncher
python3 ialauncher.py          # ouvre http://127.0.0.1:8765 dans le navigateur
```

![Modèles détectés et répartition VRAM / RAM / SSD pour Qwen 3.8 Flash Next](docs/memoire.png)

Inspiré d'[ajean](https://github.com/nathaninline/ajean) (Go) pour l'organisation
générale ; le code est indépendant.

## Sommaire

- [Pourquoi ce projet](#pourquoi-ce-projet)
- [Prérequis](#prérequis)
- [Installation et démarrage](#installation-et-démarrage)
- [Ce que fait chaque étape](#ce-que-fait-chaque-étape)
- [Utiliser le modèle depuis d'autres applications](#utiliser-le-modèle-depuis-dautres-applications)
- [Image, vidéo, musique](#image-vidéo-musique)
- [Espace disque](#espace-disque)
- [Configurations enregistrées (presets)](#configurations-enregistrées-presets)
- [Où sont les fichiers](#où-sont-les-fichiers)
- [Précision de l'estimation](#précision-de-lestimation)
- [Exemple : Qwen 3.8 Flash Next sur 24 Go de VRAM](#exemple--qwen-38-flash-next-sur-24-go-de-vram--64-go-de-ram)
- [Agents de code : testé](#agents-de-code--testé)
- [Dépannage](#dépannage)
- [Limites connues](#limites-connues)
- [Structure du code](#structure-du-code)
- [API HTTP du launcher](#api-http-du-launcher)
- [Désinstallation](#désinstallation)
- [Licence](#licence)

## Pourquoi ce projet

Faire tourner un modèle de langage chez soi, c'est surtout une question de **mémoire** :
où mettre les poids, combien coûte le contexte, que sacrifier quand ça ne tient pas.
Les outils grand public (Ollama, LM Studio) cachent ces choix ; llama.cpp les expose
tous (`-ngl`, `--n-cpu-moe`, `-ctk/-ctv`, `-c`, `-fa`, décodage spéculatif…) mais sans
dire ce qu'ils coûtent. IA Launcher se place entre les deux : **chaque réglage est
expliqué, et son effet sur la mémoire est visible avant de lancer**.

Ce que le launcher ne fait pas (volontairement) : pas de mémoire de conversation, pas
d'outils/MCP, pas de gestion multi-utilisateurs. C'est un lanceur, pas une plateforme.

## Prérequis

| | Obligatoire | Pour le moteur GPU |
|---|---|---|
| **Tous** | Python ≥ 3.10, un navigateur, connexion internet (Hugging Face, GitHub) | — |
| **Windows** | — | Pilote NVIDIA à jour (binaire CUDA officiel), sinon Vulkan (AMD/Intel/NVIDIA) |
| **macOS** | — | Rien : le binaire officiel utilise Metal |
| **Linux + NVIDIA** | — | Il n'existe **pas de binaire CUDA officiel** : le launcher compile llama.cpp. Il faut `cmake`, `g++` (ou `clang++`) et le CUDA Toolkit (`nvcc`, cherché dans le PATH ou `/usr/local/cuda/bin`). Sans ces outils, il propose le binaire Vulkan. |
| **Linux + AMD** | — | Runtime ROCm installé (binaire ROCm officiel), sinon Vulkan |
| **WSL2 + NVIDIA** | — | Comme Linux + NVIDIA : **compilez en CUDA** (les builds Vulkan ne voient pas le GPU sous WSL2) |

Sur Debian/Ubuntu, pour la compilation CUDA :

```bash
sudo apt install cmake g++ cuda-toolkit    # cuda-toolkit depuis le dépôt NVIDIA
```

Pas de GPU ? Tout fonctionne sur CPU ; les petits modèles (≤ 4-8 milliards de
paramètres, quantifiés en Q4) restent utilisables.

## Installation et démarrage

```bash
git clone https://github.com/pentinou/ialauncher.git
cd ialauncher
python3 ialauncher.py
```

Aucun `pip install`. Sous Windows, `py ialauncher.py` ou `python ialauncher.py`.

Pour **mettre à jour puis lancer** en une commande (Linux, macOS, WSL2) :

```bash
./start.sh                     # git pull, moteur llama.cpp si une release plus récente existe, lancement
```

`start.sh` accepte les mêmes options qu'`ialauncher.py`. Le moteur est réinstallé par la
méthode d'origine (recompilation CUDA ou binaire officiel) et l'ancienne version reste
dans `engines/`. Une mise à jour qui échoue (hors ligne, modifications locales…) est
signalée et n'empêche pas le lancement.

Options :

```
python3 ialauncher.py --port 9000     # port de l'interface (défaut 8765 ; si occupé, le suivant libre)
python3 ialauncher.py --no-browser    # ne pas ouvrir le navigateur
python3 ialauncher.py --host 0.0.0.0  # écouter sur le réseau (défaut 127.0.0.1) — SANS authentification :
                                      # à réserver à un proxy qui en a une (pare-feu limité à son adresse)
IALAUNCHER_HOME=/data/ia python3 ialauncher.py   # changer le dossier de données (voir plus bas)
```

`Ctrl+C` (ou `SIGTERM`) arrête le launcher **et** le `llama-server` qu'il a lancé. Si un
`llama-server` d'une session précédente traîne encore (fichier PID), il est arrêté au
démarrage suivant.

Au premier lancement, suivez les onglets dans l'ordre : **Moteur → Modèle → Réglages →
Lancer → Chat**. Chaque écran explique ce qu'il fait ; les boutons « ? » détaillent
chaque réglage.

## Ce que fait chaque étape

1. **Moteur** — détecte GPU / OS et propose la bonne façon d'obtenir `llama-server` :
   binaires officiels précompilés (Windows CUDA/Vulkan/CPU, macOS Metal, Linux
   Vulkan/ROCm/CPU) ou compilation locale CUDA (Linux + NVIDIA, si `nvcc` et `cmake`
   sont présents — il n'existe pas de binaire CUDA officiel pour Linux). On peut aussi
   pointer un `llama-server` déjà installé. La version prise est la dernière release
   stable de llama.cpp ; la compilation produit aussi `llama-fit-params` (voir étape 4).
2. **Modèle** — liste les `.gguf` déjà sur le disque (dossier du launcher, Ollama, LM
   Studio, dossiers ajoutés), un catalogue commenté de modèles conseillés (Qwen 3.5/3.6/3.8,
   Gemma 4, gpt-oss, GLM… uniquement depuis des quantiseurs reconnus : unsloth, ggml-org,
   bartowski), et la recherche Hugging Face (dépôts GGUF = versions quantifiées prêtes pour
   llama.cpp). L'en-tête GGUF est lu **à distance** (quelques Mo par fragment) : les
   estimations et la configuration fonctionnent avant même de télécharger. Dans un dépôt,
   **« Analyser les versions pour ma machine »** profile chaque quantification, de la plus
   petite à la plus grosse, et dit laquelle est la meilleure qui tienne (tout en VRAM,
   experts en RAM, ou pas du tout). Les fichiers `mmproj` (vision/audio) et les brouillons
   MTP publiés à part (décodage spéculatif) sont proposés au téléchargement et activés
   automatiquement. Les téléchargements sont **reprenables** (fichier `.part` + `Range`).
3. **Réglages** — chaque réglage a un bouton « ? » qui explique ce qu'il fait, son
   effet sur la mémoire, la vitesse et la qualité. « Proposer la configuration
   optimale » part de l'idéal (tout sur le GPU, cache KV en f16, contexte 32k) et ne
   sacrifie que le nécessaire, dans l'ordre le moins coûteux : cache KV en q8_0, contexte
   (jusqu'à 8k), experts MoE déportés en RAM (`--n-cpu-moe`), puis couches sur CPU
   (`-ngl`), et en dernier recours tout sur CPU. Un champ « options supplémentaires »
   permet de passer n'importe quelle option de `llama-server`.

   ![Réglages : configuration proposée avec ses raisons, et explications dépliées](docs/reglages.png)

4. **Où va la mémoire ?** — trois jauges (VRAM par carte, RAM, SSD) découpées en
   segments : poids sur GPU, cache KV, tampon de calcul, pilote, poids côté CPU, experts
   en RAM, cache de prompts, tenseurs lus à la demande, poids relus depuis le SSD faute
   de RAM… Les avertissements disent quoi faire quand ça ne tient pas. Le bouton
   « vérifier avec llama.cpp » demande un second avis à `llama-fit-params` (projection
   en ~1,5 s sans charger les poids).
5. **Lancer** — affiche la commande `llama-server` exacte (copiable, pour la relancer à
   la main), lance le serveur, puis compare **prévu vs réel** en lisant les tailles de
   tampons dans le journal (`logs/llama-server.log`). Le port demandé est 8080 ; s'il est
   pris, le launcher prend le suivant libre et l'affiche.
6. **Outils de code** — branche **Claude Code, Codex ou OpenCode** sur le modèle local.
   llama-server expose nativement `/v1/messages` (API Anthropic), `/v1/responses` (API
   Responses) et `/v1/chat/completions` : chaque outil reçoit ses réglages au lancement
   (variables d'environnement, options `-c`, config inline), sans toucher à vos fichiers
   de configuration. Le launcher vérifie que le modèle sait appeler des outils (bouton de
   test), propose des réglages « agent » (≥ 64k de contexte, KV q8_0, `--cache-reuse`) et
   génère des scripts `~/.ialauncher/agents/*-local.sh|.cmd` à relancer plus tard sans
   passer par l'interface. Les requêtes de Claude Code passent par un relais du launcher
   (`/proxy/…`) qui remet en tête le message « system » qu'il glisse au milieu de la
   conversation — les gabarits Qwen le refusent sinon.

   ![Outils de code : vérifications, variables d'environnement et commandes générées](docs/agents.png)

7. **Chat** — flux avec raisonnement replié, tok/s. Sert à vérifier que le serveur
   répond ; ce n'est pas un client de chat complet.

## Utiliser le modèle depuis d'autres applications

Une fois lancé, `llama-server` est un serveur HTTP ordinaire compatible **OpenAI**
(`http://127.0.0.1:8080` par défaut, le port réel est affiché dans l'onglet Lancer).
Tout client qui parle l'API OpenAI s'y branche : Open WebUI, Continue, Cline, Jan,
scripts Python avec le paquet `openai`… La clé API est ignorée (mettez n'importe quoi).

```bash
curl http://127.0.0.1:8080/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Bonjour"}]}'
```

Endpoints utiles : `/v1/chat/completions`, `/v1/completions`, `/v1/messages`
(Anthropic), `/v1/responses`, `/health`, `/props`, et l'interface web intégrée de
llama.cpp sur `/`.

Par défaut le serveur n'écoute que sur `127.0.0.1`. Pour l'exposer sur le réseau local,
ajoutez `--host 0.0.0.0` (et de préférence `--api-key …`) dans les « options
supplémentaires » des réglages — sans clé, n'importe qui sur le réseau peut l'utiliser.

## Image, vidéo, musique

Les onglets **Image**, **Vidéo** et **Musique** suivent la même idée que l'onglet Texte :
le launcher installe un moteur, télécharge les modèles et le pilote par son API locale.
Le prompt et tous les réglages se font dans le launcher, jamais dans un autre programme.

**Une seule carte graphique pour tout.** Un modèle de diffusion ou de musique ne tient
pas dans la VRAM à côté d'un LLM : lancer une génération arrête `llama-server` (après
confirmation) et l'autre générateur ; lancer le serveur de texte arrête les générateurs.
Le bouton « libérer la carte graphique » décharge le modèle d'image / de musique ; le
bouton **⏏ tout décharger** de l'en-tête arrête tous les serveurs (texte, image / vidéo,
musique) et rend la VRAM et la RAM qu'ils occupaient (une génération en cours est annulée).

### Image et vidéo : stable-diffusion.cpp

Le « llama.cpp de la diffusion » : même bibliothèque ggml, fichiers GGUF quantifiés,
binaire compilé pour la carte, serveur HTTP (`sd-server`). Installation comme pour
llama.cpp : compilation CUDA sous Linux + NVIDIA (≈ 15 min, il n'existe pas de binaire
CUDA Linux officiel), binaires officiels ailleurs (CUDA Windows, ROCm, Vulkan, Metal).
Le launcher lance `sd-server` sur le modèle choisi et lui soumet les générations par son
API native asynchrone (`/sdcpp/v1/img_gen`, `/sdcpp/v1/vid_gen`) ; le modèle reste chargé
entre deux générations, changer de modèle relance le serveur.

Catalogue (un modèle = plusieurs fichiers, téléchargés ensemble) :

| Modèle | Usage | Fichiers | Licence |
|---|---|---|---|
| Z-Image Turbo | image, 8 étapes, photo, texte dans l'image | 11,2 Go | Apache 2.0 |
| FLUX.2 klein 9B | image, 4 étapes, retouche par image de référence | 19,0 Go | FLUX Non-Commercial |
| Qwen-Image 2.1 | image, qualité, texte long, retouche | 18,2 Go | Qwen Research |
| Anima | image, illustration anime, très léger | 3,2 Go | CircleStone Non-Commercial |
| Krea 2 Turbo | image, 8 étapes, photo | 18,2 Go | Krea 2 Community |
| Wan 2.2 TI2V 5B | vidéo 24 i/s, texte→vidéo et image→vidéo | 12,8 Go | Apache 2.0 |
| Wan 2.2 T2V A14B | vidéo 16 i/s, deux experts (bruit fort / faible) | 25,6 Go | Apache 2.0 |
| MiniMax-H3 | vidéo **avec son**, image de départ / de fin | 35,5 Go | MiniMax H3 Community ⚠ |

⚠ **MiniMax-H3** : la licence exclut l'usage dans l'**Union européenne**, au Royaume-Uni,
aux États-Unis et en Corée du Sud sans autorisation écrite de MiniMax (formulaire sur
[huggingface.co/MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3)). Le
launcher le rappelle et demande confirmation avant le téléchargement. Sur une carte de
24 Go, il tourne en déchargeant ses poids en RAM : c'est lent.

**Stable Diffusion WebUI n'est pas nécessaire** : le moteur est stable-diffusion.cpp, que
le launcher installe lui-même. Si une WebUI ou Forge est présente, ses fichiers sont
simplement réutilisés :

**Vos checkpoints Stable Diffusion WebUI / Forge** (SD 1.5, SDXL, Pony, Illustrious,
SD 3.5 tout-en-un) sont trouvés automatiquement — dossiers `*webui*` / `*forge*` du
dossier personnel et, sous WSL, des lecteurs Windows (`/mnt/c`, `/mnt/d`…) et des profils
utilisateurs — et utilisés en place. L'architecture est lue dans l'en-tête safetensors.
Les **LoRA** sont réunis dans `diffusion/loras/` : ceux venus de Civitai, plus des liens
symboliques vers ceux de la WebUI (`webui-<nom>/`), si bien que tous servent à tous les
modèles de la bonne famille. La famille d'un LoRA (SD 1.5, SDXL, Z-Image…) est lue dans
ses métadonnées ; l'interface met en avant ceux qui correspondent au modèle choisi et
ajoute leurs mots déclencheurs. La syntaxe `<lora:nom:0.8>` dans le prompt fonctionne
comme dans la WebUI (le launcher la traduit pour l'API de sd-server, qui refuse les
balises).

**Civitai.** L'onglet Image (et Vidéo, pour les LoRA) cherche sur
[civitai.com](https://civitai.com/models) : checkpoints ou LoRA, par famille, popularité
ou date, avec ou sans contenu adulte. Seules les familles que stable-diffusion.cpp sait
charger sont proposées : SD 1.5, SDXL / Pony / Illustrious / NoobAI (checkpoints
tout-en-un), Z-Image, FLUX.2 klein 9B, Qwen-Image 2.1, Anima, Krea 2 (le checkpoint
Civitai ne contient que le modèle de diffusion : l'encodeur de texte et le VAE viennent
du modèle de base du catalogue, à télécharger une fois), et les LoRA Wan 2.2 et
MiniMax-H3 pour la vidéo. Civitai exige une **clé API** (compte gratuit,
civitai.com/user/account → API Keys) pour tout téléchargement ; elle est gardée dans
`config.json`. Les fichiers au format pickle (`.ckpt`, `.pt`), qui peuvent exécuter du
code à l'ouverture, ne sont pas proposés. Pour SDXL, le launcher télécharge une
fois le VAE corrigé `sdxl-vae-fp16-fix` (0,33 Go), sans quoi le VAE d'origine peut
déborder en fp16 et donner des images noires. Réglages disponibles : taille (formats
prédéfinis), étapes, CFG, sampler, planning, graine, lot, clip skip, hires fix,
img2img, image de référence (retouche), image de départ / de fin (vidéo), durée et
cadence (vidéo), placement des poids (auto / RAM / disque), VAE par tuiles.

Mémoire : `sd-server` place lui-même les poids (`--auto-fit` : VRAM, puis RAM, puis
disque) ; « RAM » force `--offload-to-cpu`, « disque » `--params-backend disk` (le moins
de RAM, le plus lent).

### Musique : ACE-Step 1.5

Chansons avec paroles (50+ langues), de 10 s à 10 min, licence MIT. C'est un programme
Python : le launcher clone le dépôt dans `apps/ACE-Step-1.5`, installe
[uv](https://docs.astral.sh/uv/) s'il manque, puis `uv sync` crée l'environnement
(Python 3.12, PyTorch CUDA…, ≈ 6 Go) — rien n'est installé dans le Python du système.
Les poids (10 Go pour le modèle de base, 20 Go de plus pour un modèle XL) sont
téléchargés par le launcher lui-même avant le premier lancement : le téléchargement
intégré d'ACE-Step (huggingface_hub) s'est figé sans erreur lors des essais. Ensuite le
launcher démarre le serveur REST `acestep-api` et lui soumet style, paroles, langue,
durée, tempo, tonalité, mesure, étapes et graine.

Mesuré sur RTX 3090 (WSL2, Ryzen 3950X), temps de calcul hors premier chargement :

| Génération | Temps |
|---|---|
| Z-Image Turbo, 1024×1024, 8 étapes | ≈ 10 s par image (2 images en 20 s) ; 23 s à froid |
| SDXL (checkpoint Pony de la WebUI) + LoRA, 1024×1024, 25 étapes | 47 s à froid, checkpoint lu depuis `D:` |
| Anima, 1024×1024, 30 étapes | 35 s à froid |
| Krea 2 Turbo, 1024×1024, 8 étapes | 26 s à froid |
| Wan 2.2 TI2V 5B, 832×480, 49 images (2 s), 30 étapes | ≈ 3 min 10 s |
| ACE-Step Turbo + LM 1,7B, chanson d'1 min, 2 variantes | 13 s ; ≈ 1 min 50 au premier lancement (chargement du LM) |

Tout ce qui est généré est rangé dans `outputs/image`, `outputs/video` et
`outputs/music`, avec un `.json` des réglages à côté : la galerie permet de revoir un
résultat et de reprendre ses réglages (graine comprise).

## Espace disque

L'onglet **Stockage** mesure tout ce qu'occupe le launcher, groupé : modèles de texte,
modèles d'image / vidéo (un fichier partagé entre deux modèles n'est compté qu'une fois
et n'est supprimé qu'avec le dernier qui l'utilise), moteurs (les versions en service ne
sont pas supprimables), ACE-Step (programme et poids), créations, cache. Il affiche aussi,
sans permettre de les supprimer, les modèles lus en place (WebUI, Ollama, LM Studio), et
le cache de `uv` (partagé avec vos autres projets uv, vidable). Chaque téléchargement
vérifie d'abord l'espace libre.

## Configurations enregistrées (presets)

Un preset = un modèle + ses réglages, sauvegardé dans `presets/<nom>.json`. Enregistrez
depuis l'onglet Réglages, rechargez d'un clic. Les fichiers sont du JSON lisible, on
peut les copier d'une machine à l'autre (les chemins de modèles doivent exister).

## Où sont les fichiers

`~/.ialauncher` (Linux/macOS) ou `%LOCALAPPDATA%\ialauncher` (Windows), modifiable par
la variable `IALAUNCHER_HOME` :

```
engines/   binaires llama.cpp et stable-diffusion.cpp (un sous-dossier par version/variante, fichier VERSION)
models/    .gguf téléchargés par le launcher (+ mmproj, brouillons MTP)
diffusion/ modèles d'image et de vidéo (un sous-dossier par dépôt Hugging Face),
           checkpoints/ et loras/ (Civitai, avec un .json de métadonnées ; loras/webui-*/ = liens vers la WebUI)
apps/      ACE-Step-1.5 (code, environnement Python .venv, checkpoints/)
outputs/   image/, video/, music/ : les générations et leurs réglages (.json)
presets/   configurations enregistrées (JSON)
agents/    scripts générés pour Claude Code / Codex / OpenCode
cache/     en-têtes GGUF déjà lus, catalogue Hugging Face, sources llama.cpp
logs/      llama-server.log, sd-server.log, ace-step.log (sortie du dernier lancement)
config.json   moteur choisi, dossiers de modèles ajoutés
```

Les modèles trouvés dans les dossiers d'**Ollama** (`~/.ollama/models`,
`/usr/share/ollama/.ollama/models`, et les profils Windows vus depuis WSL) et de
**LM Studio** (`~/.lmstudio/models`, `~/.cache/lm-studio/models`) sont utilisés en place,
jamais copiés. On peut ajouter n'importe quel autre dossier depuis l'onglet Modèle.

## Précision de l'estimation

L'estimateur reprend les règles de llama.cpp (placement des couches, cache KV par type
de couche — attention complète, fenêtre glissante, récurrente —, tampons calibrés sur
la projection de `--fit`). Sur les modèles testés il est à ± 3 % des tailles réelles
pour les poids et le cache KV ; le tampon de calcul est volontairement prudent (le
tampon réellement réservé recycle ses intermédiaires).

En résumé, ce qui est compté :

| Segment | Calcul |
|---|---|
| Poids par couche | offsets exacts lus dans l'en-tête GGUF |
| Cache KV | `ctx × Σ(n_head_kv × key_len × octets)` sur les couches d'attention seulement (les couches récurrentes/SWA coûtent beaucoup moins) |
| Tampon de calcul | ≈ `ubatch × (n_vocab + 4·n_embd) × 4` (+ `ubatch × ctx × n_head × 4` sans flash-attention) |
| Surcoût CUDA | ≈ 520 Mio par carte (contexte + pilote) |
| Tenseurs « lazy » | embeddings par couche (Gemma E4B, Qwen Flash Next) lus à la demande sur le SSD : comptés sur la jauge SSD, pas en RAM |

## Exemple : Qwen 3.8 Flash Next sur 24 Go de VRAM + 64 Go de RAM

Ce modèle (125B, 6B actifs) embarque **51B d'embeddings n-gram** (26,8 Gio dans tous
les GGUF) que llama.cpp lit **à la demande depuis le SSD** (mode lazy) : ils ne
comptent ni en VRAM ni en RAM. Ce qui doit être résident, ce sont les experts (26 à
55 Gio selon la quantification) et l'attention (~3-4 Gio). Le launcher place
l'attention et une partie des experts sur le GPU, le reste des experts en RAM
(`--n-cpu-moe`) :

| Version | Experts | RAM nécessaire | Verdict (RTX 3090 + 31 Go WSL / 48 Go / 64 Go natif) |
|---|---|---|---|
| REAP-256 UD-Q3_K_XL (experts élagués) | 26 Gio | 14 Gio | ✅ / ✅ / ✅ |
| unsloth UD-IQ1_S | 37 Gio | 24 Gio | ✅ (juste) / ✅ / ✅ |
| unsloth UD-Q2_K_XL | 43 Gio | 30 Gio | ❌ / ✅ / ✅ |
| unsloth UD-IQ4_XS | 55 Gio | 41 Gio | ❌ / ❌ / ✅ |

Sous WSL2, la RAM vue par défaut est la moitié du PC : `%UserProfile%\.wslconfig`
avec `[wsl2]` / `memory=51GB` puis `wsl --shutdown` (le launcher le signale).

## Agents de code : testé

Avec Qwen 3.8 27B (IQ2_XXS) en contexte 131k sur la RTX 3090 : Claude Code (`claude -p`)
lit un fichier avec l'outil `Read` et répond en 26 s (dont ~20 s pour ingérer son prompt
système de ~15k jetons la première fois) ; OpenCode répond via la config inline. Codex
n'a pas pu être testé ici (non installé) : la commande générée suit le format documenté.

Ce que fait chaque script généré :

- **Claude Code** : `ANTHROPIC_BASE_URL` pointe sur le relais du launcher,
  `ANTHROPIC_AUTH_TOKEN` factice, `ANTHROPIC_MODEL` / `ANTHROPIC_DEFAULT_*_MODEL` /
  `CLAUDE_CODE_SUBAGENT_MODEL` sur le modèle local, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
  aligné sur le contexte.
- **Codex** : `codex -c model_provider=… -c model_providers.<x>.base_url=… -c
  model_providers.<x>.wire_api="responses"` + `OPENAI_API_KEY` factice.
- **OpenCode** : configuration inline via `OPENCODE_CONFIG_CONTENT`, avec les
  `modalities` du modèle (`image` ajouté quand un mmproj est chargé : sans cette
  déclaration, OpenCode remplace chaque image jointe par « this model does not support
  image input » et le modèle dit ne pas pouvoir lire les images).

Rien n'est écrit dans `~/.claude`, `~/.codex` ou `~/.config/opencode`.

## Dépannage

| Symptôme | Cause / remède |
|---|---|
| « port 8765 occupé » au démarrage | Une autre instance tourne, ou le port est pris : le launcher bascule sur le suivant et l'affiche. |
| Le GPU n'est pas détecté sous WSL2 | Vous avez un binaire Vulkan : compilez en CUDA (onglet Moteur → « compiler »). Installez d'abord `cuda-toolkit`, `cmake`, `g++`. |
| La VRAM « déjà utilisée » est élevée alors que rien ne tourne | Sous WSL2, `nvidia-smi` compte l'usage de Windows (bureau, navigateur…). Le launcher prend la valeur la plus prudente entre `nvidia-smi` et `llama-server --list-devices`. |
| La RAM affichée est la moitié de celle du PC | WSL2 ne donne que 50 % par défaut : `%UserProfile%\.wslconfig` → `[wsl2]` / `memory=…GB`, puis `wsl --shutdown`. |
| « wrong number of tensors » au chargement d'un blob Ollama | Le blob embarque un encodeur vision/audio dans le même fichier (gemma4, qwen3.5 du registre Ollama) : llama.cpp officiel ne sait pas le lire. Téléchargez le GGUF depuis Hugging Face. |
| Un modèle « tient » d'après les jauges mais le serveur échoue | Cliquez « vérifier avec llama.cpp » (`llama-fit-params`) et lisez `logs/llama-server.log` ; le tampon de calcul dépend de `-ub` (ubatch) : réduisez-le. |
| Claude Code : « System message must be at the beginning » | Lancez Claude Code via le script généré (il passe par `/proxy/`), pas directement sur le port de llama-server. |
| Réponses très lentes avec un MoE | Trop d'experts en RAM (`--n-cpu-moe`) ou trop de couches sur CPU : essayez une quantification plus petite, KV q8_0, ou un contexte réduit pour remonter des couches sur le GPU. |

## Limites connues

- Les blobs Ollama qui embarquent un encodeur vision/audio dans le même fichier
  (`gemma4`, `qwen3.5` du registre Ollama) ne sont pas chargeables par llama.cpp
  officiel : le launcher le signale ; les blobs texte seul (et leur projecteur séparé)
  fonctionnent.
- Sous WSL2, la VRAM « déjà utilisée » vue par `nvidia-smi` inclut l'usage Windows ;
  la RAM affichée est celle allouée à WSL (`.wslconfig`).
- Les builds Vulkan ne voient généralement pas le GPU sous WSL2 : compilez en CUDA.
- Détection GPU : NVIDIA via `nvidia-smi`, AMD via sysfs (Linux), Apple via la mémoire
  unifiée. Un GPU Intel ou AMD sous Windows n'est pas listé (le moteur Vulkan l'utilise
  quand même, mais sans jauge VRAM).
- Un seul `llama-server` à la fois, et un seul modèle sur la carte graphique à la fois
  (texte, image / vidéo ou musique).
- Image / vidéo : ControlNet seulement pour SD 1.5 (limite de stable-diffusion.cpp) ; pas
  d'inpainting par masque dans l'interface. Les checkpoints FLUX « tout-en-un » ne sont
  pas pris en charge (utilisez le catalogue).
- Musique : l'API d'ACE-Step n'a pas d'annulation ; une génération annulée se termine
  en arrière-plan.
- Interface et explications en français uniquement.

## Structure du code

```
ialauncher.py          point d'entrée (argparse, lance web.serve)
start.sh               mise à jour (git pull, moteur llama.cpp) puis lancement
launcher/
  web.py               serveur HTTP (ThreadingHTTPServer), routes /api/*, relais /proxy/*
  hardware.py          inventaire GPU / RAM / CPU / disque (nvidia-smi, sysfs, /proc, sysctl, ctypes)
  engine.py            obtention de llama-server : prebuilt (releases GitHub), build CUDA, custom
  download.py          HTTP (JSON, Range, téléchargement reprenable)
  gguf.py              lecteur d'en-tête GGUF (local ou à distance par Range)
  models.py            inventaire des .gguf (launcher, Ollama, LM Studio, dossiers ajoutés), Hugging Face
  catalog.py           catalogue statique de modèles conseillés
  profile.py           profil d'un modèle : architecture, couches, experts, tenseurs lazy
  estimate.py          estimation mémoire par segment (VRAM / RAM / SSD) pour une config
  recommend.py         configuration optimale pour la machine (ordre des sacrifices)
  server.py            cycle de vie de llama-server : ligne de commande, démarrage, journal, PID
  agents.py            scripts et relais pour Claude Code / Codex / OpenCode
  jobs.py              tâches longues (téléchargement, compilation) avec progression
  presets.py           presets JSON
  paths.py             dossiers de données (IALAUNCHER_HOME)
  services.py          serveurs de génération en arrière-plan (sd-server, ACE-Step), carte graphique partagée
  sdcpp.py             image / vidéo : installation de stable-diffusion.cpp, modèles, checkpoints WebUI, génération
  gen_catalog.py       catalogue des modèles d'image et de vidéo (fichiers, réglages conseillés)
  music.py             musique : installation d'ACE-Step (uv), poids, génération
  civitai.py           recherche et téléchargement de checkpoints / LoRA sur Civitai
  storage.py           espace disque : mesure et suppression
ui/
  index.html, app.js, style.css   interface, sans framework ni build
  gen.js               onglets Image, Vidéo, Musique
```

Pour vérifier l'inventaire matériel seul : `python3 -m launcher.hardware`.

## API HTTP du launcher

L'interface web n'utilise que ces routes (JSON), utilisables aussi en script :

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/hardware`, `/api/live` | inventaire matériel ; valeurs qui bougent (VRAM, RAM, disque) |
| GET/POST | `/api/engine`, `/api/engine/install`, `/api/engine/custom`, `/api/engine/select` | état et installation du moteur |
| GET/POST | `/api/jobs`, `/api/jobs/<id>`, `/api/jobs/<id>/cancel` | tâches longues |
| GET/POST | `/api/models`, `/api/models/profile`, `/api/models/dirs` | modèles locaux, profil, dossiers |
| GET/POST | `/api/catalog`, `/api/hf/search`, `/api/hf/repo`, `/api/hf/profile`, `/api/hf/download`, `/api/hf/analyze` | catalogue et Hugging Face |
| POST | `/api/estimate`, `/api/recommend`, `/api/fit`, `/api/cmdline` | estimation, config optimale, second avis llama-fit-params, ligne de commande |
| GET/POST | `/api/presets`, `/api/presets/delete` | presets |
| GET/POST | `/api/server`, `/api/server/start`, `/api/server/stop` | llama-server |
| POST | `/api/chat` | chat de test (flux) |
| GET/POST | `/api/agents`, `/api/agents/test`, `/api/agents/scripts`, `/api/agents/open` | outils de code |
| * | `/proxy/…` | relais vers llama-server (normalise les messages Anthropic) |
| GET | `/api/gen/status`, `/api/gen/models?kind=image\|video`, `/api/gen/loras?model=`, `/api/gen/outputs?kind=` | image / vidéo / musique : état, modèles, LoRA, galerie |
| POST | `/api/gen/engine/install`, `/api/gen/download`, `/api/music/install` | installation des moteurs et modèles |
| POST | `/api/gen/run`, `/api/gen/stop`, `/api/gen/dirs`, `/api/gen/outputs/delete` | générer (`kind` : image, video, music), libérer la carte, dossiers, galerie |
| GET/POST | `/api/storage`, `/api/storage/delete` | espace disque |
| GET/POST | `/api/civitai/search`, `/api/civitai/token`, `/api/civitai/download` | Civitai |
| GET | `/outputs/<kind>/<fichier>` | fichiers générés (requêtes Range pour les lecteurs audio / vidéo) |

## Désinstallation

Supprimez le dossier du dépôt et le dossier de données (`~/.ialauncher` ou
`%LOCALAPPDATA%\ialauncher`, ou `IALAUNCHER_HOME`). Rien d'autre n'est écrit sur la
machine ; les modèles Ollama / LM Studio ne sont pas touchés.

## Licence

[MIT](LICENSE).
