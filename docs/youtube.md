# Onglet YouTube : choix techniques

Objectif : donner une adresse de vidéo YouTube et obtenir **l'idée principale en une
phrase**, puis, à la demande, **un paragraphe d'explication** ; une vidéo à plusieurs sujets
indépendants donne un point par sujet. Puis pouvoir **poser ses propres questions** sur la
vidéo quand le résumé ne suffit pas. Mode d'emploi : [README](../README.md#résumé-de-vidéos-youtube).

## La chaîne

```
adresse ──► 1. métadonnées (yt-dlp -J)   titre, durée, chapitres, langue, pistes de sous-titres
              │
              ├─► 2. sous-titres (yt-dlp, format json3)            ~10 s pour les deux appels
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
- **Choix de la piste** : sous-titres de l'auteur dans la langue de la vidéo, sinon de l'auteur
  en français ou en anglais, sinon automatiques **d'origine** (clé `<langue>-orig`). Jamais une
  traduction automatique : un résumé fait sur une traduction perd les nuances.
- **Doublage automatique de YouTube** : sur une vidéo doublée, le champ `language` de yt-dlp
  est celui du doublage (ex. `en-US` pour une vidéo française). La langue d'origine est celle
  de la piste audio dont `format_note` contient « original ».
- **Le fichier de sous-titres passe par yt-dlp**, pas par l'URL brute des métadonnées :
  télécharger cette URL soi-même déclenche vite des erreurs 429 (trop de requêtes).
- Les sous-titres automatiques sont aujourd'hui ponctués ; il reste des noms propres écorchés
  (« RTX 570 » pour 5070), que le modèle corrige de lui-même.

## Résumé : un seul LLM, celui de l'onglet Texte

Pas besoin d'un « petit » modèle plus un « gros » : quand les sous-titres existent, il n'y a
rien à transcrire, et résumer est une seule tâche. Le launcher pilote déjà `llama-server` :
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
plus petit gagnerait quelques secondes sur un total dominé par la lecture des sous-titres,
au risque de rater les corrections de sous-titres abîmés.

## Pistes pour la suite

- **SponsorBlock** : l'API publique `sponsor.ajay.app/api/skipSegments?videoID=…` donne les
  plages sponsor / intro / rappel d'abonnement ; retirer ces lignes avant le résumé.
- **Vidéos sans sous-titres** : transcription par whisper.cpp (`whisper-server`, même famille
  ggml que llama.cpp et sd.cpp) avec `large-v3-turbo` (~1 Go de VRAM, cohabite avec le LLM),
  plutôt que faster-whisper (pile Python + CTranslate2 de plus). Il faut ffmpeg pour convertir
  l'audio en WAV 16 kHz.
- **Mises à jour** : proposer yt-dlp et Deno dans l'onglet Mises à jour.
- Vidéos de plus de ~1 h : découper par chapitres puis résumer les résumés (non prévu : le
  besoin visé est la vidéo de 10 à 30 min).

Écartés : `youtube-transcript-api` (sous-titres seulement, et il faudrait yt-dlp de toute
façon pour l'audio) ; un service en ligne (tout reste local) ; un programme séparé (le launcher
sait déjà choisir, charger et décharger le modèle).
