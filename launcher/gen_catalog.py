"""Catalogue des modèles d'image et de vidéo pour stable-diffusion.cpp.

Un modèle de diffusion moderne n'est pas un seul fichier : il faut le « transformer »
de diffusion, un encodeur de texte (souvent un petit LLM) et un VAE qui transforme
l'image latente en pixels. Chaque entrée liste ces fichiers (dépôt Hugging Face,
chemin, taille en octets) et les réglages conseillés par leurs auteurs. Les tailles
sont figées ici pour afficher l'encombrement sans interroger le réseau."""

# Rôle du fichier → option de sd-server
ROLE_FLAGS = {
    "model": "-m",                          # checkpoint « tout-en-un » (SD 1.5, SDXL)
    "diffusion_model": "--diffusion-model",
    "high_noise": "--high-noise-diffusion-model",
    "vae": "--vae",
    "audio_vae": "--audio-vae",
    "llm": "--llm",
    "llm_vision": "--llm_vision",
    "t5xxl": "--t5xxl",
}
ROLE_LABELS = {
    "model": "checkpoint", "diffusion_model": "modèle de diffusion", "high_noise": "modèle « bruit fort »",
    "vae": "VAE", "audio_vae": "VAE audio", "llm": "encodeur de texte", "llm_vision": "encodeur d'images",
    "t5xxl": "encodeur de texte (UMT5)",
}

GB = 1000 ** 3
WAN_NEG = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，"
           "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，"
           "形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")

CATALOG = [
    # ------------------------------------------------------------------ image
    {"id": "z-image-turbo", "family": "zimage", "kind": "image", "name": "Z-Image Turbo", "license": "Apache 2.0",
     "tags": ["rapide", "photo", "texte dans l'image"],
     "blurb": "Le meilleur point de départ : 6 milliards de paramètres distillés pour produire une image "
              "en 8 étapes, photoréaliste, qui sait écrire du texte (anglais et chinois). Rapide même sur "
              "une carte moyenne. Pas de prompt négatif (CFG 1).",
     "files": {"diffusion_model": ("leejet/Z-Image-Turbo-GGUF", "z_image_turbo-Q8_0.gguf", 6.58 * GB),
               "llm": ("unsloth/Qwen3-4B-Instruct-2507-GGUF", "Qwen3-4B-Instruct-2507-Q8_0.gguf", 4.28 * GB),
               "vae": ("Comfy-Org/z_image_turbo", "split_files/vae/ae.safetensors", 0.34 * GB)},
     "defaults": {"width": 1024, "height": 1024, "steps": 8, "cfg": 1.0, "sampler": "", "negative": False}},
    {"id": "flux2-klein-9b", "family": "flux2klein9", "kind": "image", "name": "FLUX.2 klein 9B", "license": "FLUX Non-Commercial",
     "tags": ["rapide", "retouche"],
     "blurb": "Black Forest Labs, 9 milliards de paramètres distillés en 4 étapes. Très bon respect du "
              "prompt, et sait RETOUCHER une image : joignez une image de référence et décrivez la "
              "modification (« remplace le ciel par un coucher de soleil »). Usage non commercial.",
     "files": {"diffusion_model": ("leejet/FLUX.2-klein-9B-GGUF", "flux-2-klein-9b-Q8_0.gguf", 9.98 * GB),
               "llm": ("unsloth/Qwen3-8B-GGUF", "Qwen3-8B-Q8_0.gguf", 8.71 * GB),
               "vae": ("Comfy-Org/flux2-klein-9B", "split_files/vae/flux2-vae.safetensors", 0.34 * GB)},
     "defaults": {"width": 1024, "height": 1024, "steps": 4, "cfg": 1.0, "sampler": "euler", "negative": False},
     "features": ["ref_images"]},
    {"id": "qwen-image-2.1", "family": "qwen21", "kind": "image", "name": "Qwen-Image 2.1", "license": "Qwen Research",
     "tags": ["qualité", "texte dans l'image", "retouche"],
     "blurb": "Le plus complet : excellente composition, texte long et multilingue dans l'image, "
              "retouche par image de référence. Plus lent (20 milliards de paramètres, CFG réel donc "
              "prompt négatif actif). Licence de recherche Qwen.",
     "files": {"diffusion_model": ("leejet/Qwen-Image-2.1-GGUF", "qwen_image_2.1-Q8_0.gguf", 7.69 * GB),
               "llm": ("Qwen/Qwen3-VL-8B-Instruct-GGUF", "Qwen3VL-8B-Instruct-Q8_0.gguf", 8.71 * GB),
               "llm_vision": ("Qwen/Qwen3-VL-8B-Instruct-GGUF", "mmproj-Qwen3VL-8B-Instruct-F16.gguf", 1.16 * GB),
               "vae": ("Comfy-Org/Qwen-Image-2.1", "vae/qwen_image_2.1_vae_bf16.safetensors", 0.68 * GB)},
     "defaults": {"width": 1024, "height": 1024, "steps": 30, "cfg": 6.0, "sampler": "euler", "negative": True},
     "features": ["ref_images"]},
    {"id": "anima", "family": "anima", "kind": "image", "name": "Anima", "license": "CircleStone Non-Commercial",
     "tags": ["anime", "léger"],
     "blurb": "Modèle d'illustration anime (2 milliards de paramètres) : la famille la plus téléchargée sur "
              "Civitai en septembre 2026. Très léger (3 Go en tout). Usage non commercial. Entraîné sur des "
              "images de fans : il glisse facilement vers du contenu suggestif, d'où « nsfw » dans le prompt "
              "négatif par défaut.",
     "files": {"diffusion_model": ("Bedovyy/Anima-GGUF", "anima-preview3-base-Q8_0.gguf", 2.28 * GB),
               "llm": ("mradermacher/Qwen3-0.6B-Base-GGUF", "Qwen3-0.6B-Base.Q8_0.gguf", 0.64 * GB),
               "vae": ("circlestone-labs/Anima", "split_files/vae/qwen_image_vae.safetensors", 0.25 * GB)},
     "defaults": {"width": 1024, "height": 1024, "steps": 30, "cfg": 6.0, "sampler": "euler", "negative": True,
                  "negative_prompt": "nsfw, nude, underwear, worst quality, low quality, blurry"}},
    {"id": "krea2-turbo", "family": "krea2", "kind": "image", "name": "Krea 2 Turbo", "license": "Krea 2 Community",
     "tags": ["rapide", "photo"],
     "blurb": "Krea 2, version distillée : photoréalisme et esthétique soignée en peu d'étapes. Deuxième famille "
              "la plus téléchargée sur Civitai en septembre 2026.",
     "files": {"diffusion_model": ("realrebelai/KREA-2_GGUFs", "TURBO/Krea-2-Turbo-Q8_0.gguf", 13.63 * GB),
               "llm": ("Qwen/Qwen3-VL-4B-Instruct-GGUF", "Qwen3VL-4B-Instruct-Q8_0.gguf", 4.28 * GB),
               "vae": ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/vae/wan_2.1_vae.safetensors", 0.25 * GB)},
     "defaults": {"width": 1024, "height": 1024, "steps": 8, "cfg": 1.0, "sampler": "", "negative": False}},
    # ------------------------------------------------------------------ vidéo
    {"id": "wan2.2-ti2v-5b", "family": "wan5b", "kind": "video", "name": "Wan 2.2 TI2V 5B", "license": "Apache 2.0",
     "tags": ["rapide", "image→vidéo"],
     "blurb": "Le modèle vidéo léger d'Alibaba : texte→vidéo ET image→vidéo (joignez une image de "
              "départ). 24 images/s. Le bon choix pour apprendre et itérer vite.",
     "files": {"diffusion_model": ("QuantStack/Wan2.2-TI2V-5B-GGUF", "Wan2.2-TI2V-5B-Q8_0.gguf", 5.40 * GB),
               "t5xxl": ("city96/umt5-xxl-encoder-gguf", "umt5-xxl-encoder-Q8_0.gguf", 6.04 * GB),
               "vae": ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/vae/wan2.2_vae.safetensors", 1.41 * GB)},
     "defaults": {"width": 832, "height": 480, "steps": 30, "cfg": 5.0, "sampler": "euler", "negative": True,
                  "negative_prompt": WAN_NEG, "frames": 49, "fps": 24, "flow_shift": 3.0},
     "features": ["init_image"]},
    {"id": "wan2.2-t2v-a14b", "family": "wan14b", "kind": "video", "name": "Wan 2.2 T2V A14B", "license": "Apache 2.0",
     "tags": ["qualité"],
     "blurb": "La version « 14 milliards » : deux experts qui se relaient, l'un pose la composition "
              "(bruit fort), l'autre les détails (bruit faible). Nettement meilleur mouvement que le 5B, "
              "mais plusieurs minutes par clip. 16 images/s.",
     "files": {"diffusion_model": ("QuantStack/Wan2.2-T2V-A14B-GGUF", "LowNoise/Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf", 9.65 * GB),
               "high_noise": ("QuantStack/Wan2.2-T2V-A14B-GGUF", "HighNoise/Wan2.2-T2V-A14B-HighNoise-Q4_K_M.gguf", 9.65 * GB),
               "t5xxl": ("city96/umt5-xxl-encoder-gguf", "umt5-xxl-encoder-Q8_0.gguf", 6.04 * GB),
               "vae": ("QuantStack/Wan2.2-T2V-A14B-GGUF", "VAE/Wan2.1_VAE.safetensors", 0.25 * GB)},
     "defaults": {"width": 832, "height": 480, "steps": 10, "high_noise_steps": 8, "cfg": 3.5, "sampler": "euler",
                  "negative": True, "negative_prompt": WAN_NEG, "frames": 33, "fps": 16, "flow_shift": 3.0}},
    {"id": "minimax-h3", "family": "minimax", "kind": "video", "name": "MiniMax-H3", "license": "MiniMax H3 Community",
     "tags": ["qualité", "son", "image→vidéo", "très lourd"],
     "blurb": "Le meilleur modèle vidéo ouvert (août 2026) : vidéo ET son stéréo générés ensemble. "
              "33 milliards de paramètres + encodeur Qwen3-VL-32B : ≈ 36 Go de fichiers, déchargés en RAM "
              "pendant le calcul. Lent sur une carte de 24 Go. Image de départ (et de fin) possibles.",
     "notice": "Licence MiniMax H3 Community : l'usage est EXCLU dans l'Union européenne, au Royaume-Uni, "
               "aux États-Unis et en Corée du Sud sans autorisation écrite de MiniMax (formulaire sur "
               "huggingface.co/MiniMaxAI/MiniMax-H3). Ailleurs : afficher « MiniMax H3 » dans le produit, "
               "autorisation requise au-delà de 20 M$ de chiffre d'affaires. Vérifiez votre droit d'usage "
               "avant de télécharger.",
     "files": {"diffusion_model": ("leejet/MiniMax-H3-GGUF", "minimax_h3_fl2va_pruned-Q4_K_M.gguf", 11.42 * GB),
               "llm": ("leejet/MiniMax-H3-GGUF", "qwen3vl_32b_minimax_h3-Q4_K_M.gguf", 18.22 * GB),
               "vae": ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_video_vae_fp16.safetensors", 5.21 * GB),
               "audio_vae": ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_audio_vae_fp32.safetensors", 0.61 * GB)},
     "defaults": {"width": 864, "height": 480, "steps": 20, "cfg": 1.0, "sampler": "", "negative": False,
                  "frames": 56, "fps": 24},
     "server_args": ["--rng", "cpu"],
     "features": ["init_image", "end_image"]},
]

# Checkpoints « tout-en-un » trouvés sur le disque (Stable Diffusion WebUI, Forge…)
LOCAL_DEFAULTS = {
    "sdxl": {"width": 1024, "height": 1024, "steps": 30, "cfg": 6.0, "sampler": "dpm++2m", "scheduler": "karras",
             "negative": True, "clip_skip": -1},
    "sd15": {"width": 512, "height": 768, "steps": 30, "cfg": 7.0, "sampler": "dpm++2m", "scheduler": "karras",
             "negative": True, "clip_skip": -1},
    "sd3": {"width": 1024, "height": 1024, "steps": 28, "cfg": 4.5, "sampler": "euler", "negative": True},
}
# Le VAE de SDXL déborde en fp16 (images noires) : version corrigée, 0,33 Go.
SDXL_VAE_FIX = ("madebyollin/sdxl-vae-fp16-fix", "sdxl.vae.safetensors", 0.33 * GB)

TAG_HELP = {
    "rapide": "Modèle distillé : quelques étapes suffisent.",
    "qualité": "Meilleur résultat, au prix du temps de calcul.",
    "photo": "Excellent en photoréalisme.",
    "texte dans l'image": "Sait écrire un texte lisible dans l'image (panneau, affiche…).",
    "retouche": "Accepte une ou plusieurs images de référence à modifier par le prompt.",
    "image→vidéo": "Peut animer une image de départ.",
    "son": "Génère aussi la bande son.",
    "très lourd": "Plus gros que la VRAM : tourne en déchargeant les poids en RAM, donc lentement.",
    "anime": "Spécialisé dans l'illustration de style anime / manga.",
    "léger": "Tourne même avec peu de VRAM.",
}


# Familles de modèles : le « modèle de base » de Civitai → ce que le launcher sait en faire.
# Les checkpoints SD 1.5 / SDXL sont « tout-en-un » (-m) ; pour les familles récentes, un
# checkpoint Civitai ne contient que le modèle de diffusion : l'encodeur de texte et le VAE
# sont empruntés au modèle de base du catalogue (« companion »).
FAMILIES = {
    "sd15": {"label": "SD 1.5", "civitai": ["SD 1.4", "SD 1.5", "SD 1.5 LCM", "SD 1.5 Hyper"]},
    "sdxl": {"label": "SDXL / Pony / Illustrious", "civitai": ["SDXL 0.9", "SDXL 1.0", "SDXL 1.0 LCM", "SDXL Lightning",
                                                               "SDXL Hyper", "SDXL Turbo", "SDXL Distilled", "Pony",
                                                               "Illustrious", "NoobAI"]},
    "zimage": {"label": "Z-Image", "civitai": ["ZImageTurbo", "ZImageBase"], "companion": "z-image-turbo"},
    "flux2klein9": {"label": "FLUX.2 klein 9B", "civitai": ["Flux.2 Klein 9B", "Flux.2 Klein 9B-base"], "companion": "flux2-klein-9b"},
    "qwen21": {"label": "Qwen-Image 2.1", "civitai": ["Qwen 2.1"], "companion": "qwen-image-2.1"},
    "anima": {"label": "Anima", "civitai": ["Anima"], "companion": "anima"},
    "krea2": {"label": "Krea 2", "civitai": ["Krea 2"], "companion": "krea2-turbo"},
    "wan5b": {"label": "Wan 2.2 5B", "civitai": ["Wan Video 2.2 TI2V-5B"], "companion": "wan2.2-ti2v-5b", "lora_only": True},
    "wan14b": {"label": "Wan 2.2 A14B", "civitai": ["Wan Video 2.2 T2V-A14B"], "companion": "wan2.2-t2v-a14b", "lora_only": True},
    "minimax": {"label": "MiniMax-H3", "civitai": ["MiniMax H3"], "companion": "minimax-h3", "lora_only": True},
}
# Réglages des variantes non distillées (« base ») : vrai CFG, plus d'étapes
BASE_OVERRIDES = {"ZImageBase": {"steps": 30, "cfg": 5.0, "negative": True},
                  "Flux.2 Klein 9B-base": {"steps": 20, "cfg": 4.0, "negative": True}}


def family_of_civitai(base_model):
    return next((k for k, f in FAMILIES.items() if base_model in f["civitai"]), None)


def find(model_id):
    return next((e for e in CATALOG if e["id"] == model_id), None)
