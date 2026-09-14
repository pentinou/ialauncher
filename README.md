# IA Launcher

Un lanceur d'IA locales **didactique** : il installe le moteur (llama.cpp), propose des
modèles, **montre où va la mémoire** (VRAM / RAM / SSD) à mesure que vous changez les
réglages, propose une **configuration optimale pour votre machine** et lance le
serveur — avec un petit chat pour vérifier que tout répond.

Python 3.10+ sans aucune dépendance. Linux, WSL2, Windows, macOS.

```
python3 ialauncher.py          # ouvre http://127.0.0.1:8765 dans le navigateur
python3 ialauncher.py --port 9000 --no-browser
```

Inspiré d'[ajean](https://github.com/nathaninline/ajean) (Go) pour l'organisation
générale ; le code est indépendant.

## Ce que fait chaque étape

1. **Moteur** — détecte GPU / OS et propose la bonne façon d'obtenir `llama-server` :
   binaires officiels précompilés (Windows CUDA/Vulkan/CPU, macOS Metal, Linux
   Vulkan/ROCm/CPU) ou compilation locale CUDA (Linux + NVIDIA, si `nvcc` et `cmake`
   sont présents — il n'existe pas de binaire CUDA officiel pour Linux). On peut aussi
   pointer un `llama-server` déjà installé.
2. **Modèle** — liste les `.gguf` déjà sur le disque (dossier du launcher, Ollama, LM
   Studio, dossiers ajoutés), un catalogue commenté de modèles conseillés, et la
   recherche Hugging Face (dépôts GGUF = versions quantifiées prêtes pour llama.cpp).
   L'en-tête GGUF est lu **à distance** (quelques Mo par fragment) : les estimations et
   la configuration fonctionnent avant même de télécharger. Dans un dépôt, **« Analyser
   les versions pour ma machine »** profile chaque quantification, de la plus petite à
   la plus grosse, et dit laquelle est la meilleure qui tienne (tout en VRAM, experts
   en RAM, ou pas du tout). Les brouillons MTP publiés à part (décodage spéculatif)
   sont proposés au téléchargement et activés automatiquement.
3. **Réglages** — chaque réglage a un bouton « ? » qui explique ce qu'il fait, son
   effet sur la mémoire, la vitesse et la qualité. « Proposer la configuration
   optimale » part de l'idéal (tout sur le GPU, cache KV en f16, contexte confortable)
   et ne sacrifie que le nécessaire, dans l'ordre le moins coûteux : cache KV en q8_0,
   contexte, experts MoE déportés en RAM (`--n-cpu-moe`), puis couches sur CPU.
4. **Où va la mémoire ?** — trois jauges (VRAM par carte, RAM, SSD) découpées en
   segments : poids sur GPU, cache KV, tampon de calcul, pilote, poids côté CPU, experts
   en RAM, cache de prompts, tenseurs lus à la demande, poids relus depuis le SSD faute
   de RAM… Les avertissements disent quoi faire quand ça ne tient pas. Le bouton
   « vérifier avec llama.cpp » demande un second avis à `llama-fit-params`.
5. **Lancer** — affiche la commande `llama-server` exacte, lance le serveur, puis
   compare **prévu vs réel** en lisant les tailles de tampons dans le journal.
6. **Outils de code** — branche **Claude Code, Codex ou OpenCode** sur le modèle local.
   llama-server expose nativement `/v1/messages` (API Anthropic), `/v1/responses` (API
   Responses) et `/v1/chat/completions` : chaque outil reçoit ses réglages au lancement
   (variables d'environnement, options `-c`, config inline), sans toucher à vos fichiers
   de configuration. Le launcher vérifie que le modèle sait appeler des outils (bouton de
   test), propose des réglages « agent » (≥ 64k de contexte, KV q8_0, `--cache-reuse`) et
   génère des scripts `~/.ialauncher/agents/*-local.sh|.cmd`. Les requêtes de Claude Code
   passent par un relais du launcher (`/proxy/…`) qui remet en tête le message « system »
   qu'il glisse au milieu de la conversation — les gabarits Qwen le refusent sinon.
7. **Chat** — flux avec raisonnement replié, tok/s. L'API OpenAI de llama-server reste
   accessible à tout autre client (Open WebUI, Continue…).

## Où sont les fichiers

`~/.ialauncher` (Linux/macOS) ou `%LOCALAPPDATA%\ialauncher` (Windows), modifiable par
la variable `IALAUNCHER_HOME` :

```
engines/   binaires llama.cpp        presets/   configurations enregistrées
models/    .gguf téléchargés         cache/     en-têtes GGUF, catalogue, sources
logs/      llama-server.log          config.json
```

## Précision de l'estimation

L'estimateur reprend les règles de llama.cpp (placement des couches, cache KV par type
de couche — attention complète, fenêtre glissante, récurrente —, tampons calibrés sur
la projection de `--fit`). Sur les modèles testés il est à ± 3 % des tailles réelles
pour les poids et le cache KV ; le tampon de calcul est volontairement prudent (le
tampon réellement réservé recycle ses intermédiaires).

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

## Limites connues

- Les blobs Ollama qui embarquent un encodeur vision/audio dans le même fichier
  (`gemma4`, `qwen3.5` du registre Ollama) ne sont pas chargeables par llama.cpp
  officiel : le launcher le signale ; les blobs texte seul (et leur projecteur séparé)
  fonctionnent.
- Sous WSL2, la VRAM « déjà utilisée » vue par `nvidia-smi` inclut l'usage Windows ;
  la RAM affichée est celle allouée à WSL (`.wslconfig`).
- Les builds Vulkan ne voient généralement pas le GPU sous WSL2 : compilez en CUDA.
