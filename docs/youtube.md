# Onglet YouTube : choix techniques

Objectif : donner une adresse de vidéo YouTube et obtenir **l'idée principale en une
phrase**, puis, à la demande, **un paragraphe d'explication** ; une vidéo à plusieurs sujets
indépendants donne un point par sujet. Puis pouvoir **poser ses propres questions** sur la
vidéo quand le résumé ne suffit pas. Mode d'emploi : [README](../README.md#résumé-de-vidéos-youtube).

## La chaîne

```
adresse ──► 1. métadonnées (yt-dlp -J)   titre, durée, chapitres, langue, pistes de sous-titres
              │
              ├─► 2a. sous-titres de l'auteur (yt-dlp, format json3)   ~10 s pour les deux appels
              ├─► 2b. sinon : audio → ffmpeg → whisper-cli               ~1 à 2 min pour 30 min
              │
              ├─► SponsorBlock : retrait des passages sponsor / intro / « abonnez-vous »
              │
              └─► 3. résumé par le llama-server lancé ──► JSON imposé {points: [{phrase, detail}]}
                        │
                    4. enregistré dans youtube/<id>.json ; questions sur la même transcription
```

## Récupération : yt-dlp + Deno

- **yt-dlp en commande externe**, version « zipapp » officielle (un fichier de ~3 Mo lancé
  avec le Python du launcher) : le launcher reste sans dépendance pip.
- **Deno** : depuis fin 2025, yt-dlp a besoin d'un moteur JavaScript externe pour les
  vérifications de YouTube ; les sous-titres sont parmi les premiers à casser sans lui. Binaire
  unique, passé à yt-dlp par `--js-runtimes deno:<chemin>`.
- **Choix de la piste** : sous-titres **écrits par l'auteur** seulement. Vidéo en français ou en
  anglais : ceux de sa langue. Autre langue : la traduction française de l'auteur, sinon l'anglaise,
  sinon ses sous-titres d'origine. Sinon Whisper. **Jamais les sous-titres générés par YouTube**
  (ni leurs traductions automatiques) : décision du 2026-11-15, après comparaison sur Gamers Nexus,
  où ils écrivaient « RTX 570 490 performance at 549 … Four Tops » quand Whisper entendait
  « RTX 5070, 4090 performance at $549 … four tera-ops ». Le modèle de texte corrige une partie de
  ces erreurs, mais pas un chiffre mal entendu.
- **Doublage automatique de YouTube** : sur une vidéo doublée, le champ `language` de yt-dlp
  est celui du doublage (ex. `en-US` pour une vidéo française). La langue d'origine est celle
  de la piste audio dont `format_note` contient « original ».
- **Le fichier de sous-titres passe par yt-dlp**, pas par l'URL brute des métadonnées :
  télécharger cette URL soi-même déclenche vite des erreurs 429 (trop de requêtes).

## Résumé : un seul LLM, celui de l'onglet Texte

Pas besoin d'un « petit » modèle de compréhension en plus du « gros » : Whisper transcrit, et
résumer (traduire compris) est une seule tâche pour le modèle de texte. Le launcher pilote déjà `llama-server` :
l'onglet lui envoie la transcription (`/v1/chat/completions`) avec :

- `response_format` + `json_schema` : la grammaire de llama-server garantit un JSON valide
  (1 à 5 points, chacun `phrase` + `detail`) ;
- `chat_template_kwargs.enable_thinking = false` : résumer n'a pas besoin de raisonnement ;
- température 0,3 ;
- la transcription en texte suivi, avec un titre `##` à chaque chapitre YouTube (indice pour
  découper les sujets) et un repère `[mm:ss]` toutes les 30 s (pour citer un moment).

Consignes clés du prompt : la phrase donne **la réponse, pas le sujet** (25 mots au plus) ;
un seul point par défaut, plusieurs seulement pour des sujets indépendants, sans en mélanger
deux ; ignorer sponsors et appels à s'abonner ; ne rien inventer ; toujours en français.
L'exemple du prompt est volontairement hors sujet (pâte à pizza) : un exemple sur le
matériel informatique déteignait sur les résumés de vidéos de matériel.

**Questions libres** : même transcription en tête de conversation, puis l'historique des
échanges. Ce début ne change pas d'une question à l'autre : `llama-server` le garde en cache
et ne relit que la fin (mesuré : 18 s pour la 1re question sur 35 min de vidéo, puis 2 à 5 s).
Les réponses sont demandées en texte simple, sans Markdown (l'interface affiche du texte brut).

**Taille du modèle** : mesuré avec Qwen 3.8 27B Q4_K_S (15,4 Go, tout en VRAM sur une
RTX 3090, ~38 jetons/s) sur 9 vidéos FR et EN de 10 à 39 min : 10 à 30 s par résumé. Un modèle
plus petit gagnerait quelques secondes sur un total dominé par la transcription.

## SponsorBlock

`GET https://sponsor.ajay.app/api/skipSegments/<4 premiers caractères du SHA-256 de l'id>?categories=[…]`
renvoie les plages de toutes les vidéos de ce préfixe : l'identifiant exact ne part pas. Catégories
retirées : sponsor, selfpromo, interaction, intro, outro, preview, filler, music_offtopic. Une ligne
de transcription est retirée si elle commence dans une plage. Mesuré : 7 vidéos de test sur 9 avaient
des données (1 à 2 min retirées sur des vidéos de 15 à 35 min). 404 = pas de données ; panne réseau :
on s'en passe.

## Whisper (toutes les vidéos sans sous-titres de l'auteur)

- **whisper.cpp** plutôt que faster-whisper : même famille ggml que llama.cpp et sd.cpp, pas de pile
  Python ni de CTranslate2. Pas de binaire Linux publié (v1.9.5) : compilé ici comme llama.cpp
  (`cmake --target whisper-cli`, CUDA si `nvcc` est trouvé, ~5 min sur 32 threads).
- **`whisper-cli` lancé à chaque vidéo**, pas de serveur à garder en vie : le modèle se charge en
  une seconde. Progression lue sur `-pp` (« progress = 42% »), sortie `-oj` (segments horodatés).
- **Modèle `large-v3-turbo` q5_0** (575 Mo, ~1 Go de VRAM) : il cohabite avec un modèle de texte de
  ~17 Go sur une carte de 24 Go (mesuré : 19,2 Go en tout).
- **ffmpeg** convertit l'audio YouTube (Opus / AAC, que whisper.cpp ne lit pas) en WAV 16 kHz mono :
  celui du système, sinon la version statique de BtbN/FFmpeg-Builds (Linux, Windows).
- **Piste audio d'origine** (`-f "ba[format_note*=original]/ba"`) pour ne pas transcrire un doublage.
- Mesuré sur RTX 3090 : 35 min de Gamers Nexus = 28 s d'audio + 70 s de transcription ; 15 min de
  Hardware Unboxed = 59 s de bout en bout, résumé compris. Une vidéo allemande sans sous-titres
  est transcrite en allemand puis résumée directement en français.
- C'est le cas le plus courant : peu d'auteurs écrivent leurs sous-titres (sur 12 vidéos de
  petites chaînes, 8 n'avaient que ceux de YouTube).

## Pistes pour la suite

- Vidéos de plus de ~1 h : découper par chapitres puis résumer les résumés (non prévu : le
  besoin visé est la vidéo de 10 à 30 min).

Écartés : `youtube-transcript-api` (sous-titres seulement, et il faudrait yt-dlp de toute
façon pour l'audio) ; un service en ligne (tout reste local) ; un programme séparé (le launcher
sait déjà choisir, charger et décharger le modèle).
