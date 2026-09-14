"""Catalogue de modèles conseillés. Les descriptions sont statiques ; la liste des
fichiers (quantifications, tailles) est lue en direct sur Hugging Face pour rester
à jour. Seuls des dépôts de quantiseurs reconnus (unsloth, ggml-org, bartowski)
sont proposés : leurs GGUF sont fiables et complets (mmproj inclus)."""

# Chaque entrée : repo HF, nom court, résumé didactique, étiquettes.
# « moe » = mélange d'experts (seule une fraction des paramètres travaille par jeton),
# « vision » = accepte des images (fichier mmproj), « code », « raisonnement ».
CATALOG = [
    {"repo": "unsloth/Qwen3.5-4B-GGUF", "name": "Qwen 3.5 4B", "tags": ["vision", "petit"],
     "blurb": "Petit modèle généraliste très rapide, lit les images. Idéal pour un PC sans gros GPU "
              "ou pour tester : 4 milliards de paramètres, ≈ 3 Go en Q4."},
    {"repo": "unsloth/gemma-4-E4B-it-qat-GGUF", "name": "Gemma 4 E4B (QAT)", "tags": ["vision", "audio", "petit"],
     "blurb": "Gemma « E4B » de Google : un 8B dont 5,6 Go d'embeddings par couche sont lus à la "
              "demande sur le SSD — en mémoire de calcul il se comporte comme un 4B. Version QAT : "
              "quantifiée pendant l'entraînement, donc peu de perte en Q4."},
    {"repo": "unsloth/Qwen3.5-9B-GGUF", "name": "Qwen 3.5 9B", "tags": ["vision"],
     "blurb": "Le bon compromis pour 8-12 Go de VRAM : généraliste solide, vision, contexte long "
              "grâce à une attention hybride (¾ des couches sont récurrentes : cache KV réduit)."},
    {"repo": "unsloth/gemma-4-12B-it-qat-GGUF", "name": "Gemma 4 12B (QAT)", "tags": ["vision"],
     "blurb": "Dense 12B de Google, très bon en rédaction et en langues. Fenêtre glissante sur la "
              "plupart des couches : le cache KV reste petit même avec un long contexte."},
    {"repo": "unsloth/gpt-oss-20b-GGUF", "name": "gpt-oss 20B", "tags": ["moe", "raisonnement"],
     "blurb": "Modèle ouvert d'OpenAI : 21 milliards de paramètres dont 3,6 actifs par jeton (MoE), "
              "poids natifs en 4 bits (MXFP4) ≈ 12 Go. Raisonnement réglable (low/medium/high)."},
    {"repo": "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", "name": "Qwen3 Coder 30B-A3B", "tags": ["moe", "code"],
     "blurb": "Spécialiste du code : 30 milliards de paramètres mais 3 actifs par jeton, donc rapide "
              "même avec les experts déportés en RAM. Recommandé pour les assistants de programmation."},
    {"repo": "unsloth/Qwen3.6-35B-A3B-GGUF", "name": "Qwen 3.6 35B-A3B", "tags": ["moe", "vision", "raisonnement"],
     "blurb": "MoE généraliste : la qualité d'un gros modèle avec la vitesse d'un 3B. Avec 16-24 Go "
              "de VRAM, tout tient en Q4 ; sinon, déportez les experts en RAM (--n-cpu-moe)."},
    {"repo": "unsloth/gemma-4-26B-A4B-it-qat-GGUF", "name": "Gemma 4 26B-A4B (QAT)", "tags": ["moe", "vision"],
     "blurb": "Le MoE de Google : 26 milliards de paramètres, 4 actifs. Bon polyvalent multilingue, "
              "vision incluse."},
    {"repo": "unsloth/Qwen3.8-27B-GGUF", "name": "Qwen 3.8 27B", "tags": ["raisonnement", "populaire"],
     "blurb": "Le modèle dense le plus téléchargé du moment : excellent en raisonnement, code et "
              "rédaction. 27B dense = il faut ≈ 16 Go en Q4 ; attention hybride (KV compact)."},
    {"repo": "unsloth/Qwen3.6-27B-GGUF", "name": "Qwen 3.6 27B", "tags": ["vision", "raisonnement"],
     "blurb": "Dense 27B avec vision. Même gabarit que le 3.8 27B, avec la lecture d'images."},
    {"repo": "unsloth/gemma-4-31B-it-qat-GGUF", "name": "Gemma 4 31B (QAT)", "tags": ["vision"],
     "blurb": "Le plus gros Gemma 4 dense : ≈ 18 Go en Q4. Pour 24 Go de VRAM avec un contexte modéré."},
    {"repo": "unsloth/gpt-oss-120b-GGUF", "name": "gpt-oss 120B", "tags": ["moe", "raisonnement", "grand"],
     "blurb": "117 milliards de paramètres, 5 actifs : ≈ 60 Go de poids. Ne tient pas en VRAM, mais "
              "avec 64 Go de RAM et les experts déportés (--n-cpu-moe) il tourne à une vitesse correcte."},
    {"repo": "unsloth/Qwen3.5-122B-A10B-GGUF", "name": "Qwen 3.5 122B-A10B", "tags": ["moe", "vision", "grand"],
     "blurb": "Gros MoE (122B, 10 actifs). Demande beaucoup de RAM en plus du GPU : 64 Go minimum en Q4."},
    {"repo": "unsloth/Qwen3.8-Flash-Next-GGUF", "name": "Qwen 3.8 Flash Next", "tags": ["moe", "vision", "grand"],
     "blurb": "Très gros MoE (177B) nouvelle génération. Réservé aux machines avec beaucoup de RAM "
              "(96 Go et plus) : la VRAM ne porte que l'attention, les experts vivent en RAM."},
    {"repo": "unsloth/GLM-5.3-Flash-GGUF", "name": "GLM 5.3 Flash", "tags": ["moe", "raisonnement", "grand"],
     "blurb": "MoE de 321B avec contexte d'un million de jetons. Pour stations de travail (128 Go+ de RAM)."},
]

TAG_HELP = {
    "moe": "MoE (mélange d'experts) : seule une fraction des paramètres travaille à chaque jeton. "
           "Gros sur le disque, mais rapide ; les experts peuvent vivre en RAM.",
    "vision": "Accepte des images (nécessite le fichier mmproj, téléchargé automatiquement).",
    "audio": "Accepte de l'audio (via mmproj).",
    "code": "Entraîné spécialement pour la programmation.",
    "raisonnement": "Sait « réfléchir » avant de répondre (mode thinking), au prix de jetons en plus.",
    "petit": "Tourne sur presque tout, y compris sur CPU seul.",
    "grand": "Ne tient pas dans la VRAM d'une carte grand public : il faut beaucoup de RAM.",
    "populaire": "Parmi les plus téléchargés sur Hugging Face ce mois-ci.",
}
