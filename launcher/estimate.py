"""Estimation de la mémoire : pour une configuration donnée, où vont les octets ?
Trois « réservoirs » : VRAM (par GPU), RAM, SSD. Chaque réservoir reçoit des
segments nommés que l'interface dessine et explique.

Règles reprises de llama.cpp :
  • -ngl N : les N DERNIÈRES couches vont sur le GPU ; la couche de sortie y va
    seulement si N > nombre de couches. L'embedding d'entrée reste toujours en RAM.
  • --n-cpu-moe N : les experts des N PREMIÈRES couches restent en RAM (le reste de
    la couche — attention, normes — suit la règle -ngl).
  • cache KV : alloué là où vit la couche (GPU ou RAM) ; --no-kv-offload = tout en RAM.
    Couches à fenêtre glissante : cache borné à (fenêtre + ubatch) jetons.
  • mmap (défaut) : les poids côté CPU sont « mappés » : ils occupent de la RAM
    tant qu'il y en a, sinon ils sont relus depuis le SSD (très lent, mais ça marche).
  • tampon de sortie (logits) : n_vocab × batch × 4 octets, en RAM.
  • tampon de calcul : dépend de ubatch et, sans flash-attention, du contexte.
"""
from . import profile as prof

MiB = 1024 ** 2
GiB = 1024 ** 3

OVERHEAD = {"nvidia": 520 * MiB, "amd": 400 * MiB, "apple": 0, "other": 300 * MiB}


def _int(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def estimate(p, cfg, hw, draft_profile=None):
    n_layer = p["n_layer"]
    gpus = [g for g in hw.get("gpus", []) if not cfg.get("devices") or str(g["index"]) in cfg["devices"]]
    has_gpu = bool(gpus)
    ngl_raw = cfg.get("ngl", "all")
    ngl = n_layer + 1 if ngl_raw in ("all", "", None, -1, "-1") else max(0, _int(ngl_raw))
    if not has_gpu:
        ngl = 0
    ngl = min(ngl, n_layer + 1)
    ctx = max(256, _int(cfg.get("ctx"), 8192))
    n_par = max(1, _int(cfg.get("parallel"), 1))
    ubatch = max(32, _int(cfg.get("ubatch"), 512))
    batch = max(ubatch, _int(cfg.get("batch"), 2048))
    type_k = cfg.get("type_k") or "f16"
    type_v = cfg.get("type_v") or "f16"
    fa = cfg.get("flash_attn", "auto")
    fa_on = fa != "off"   # « auto » = activée sur tous les backends actuels
    n_cpu_moe = n_layer if cfg.get("cpu_moe_all") else min(n_layer, max(0, _int(cfg.get("n_cpu_moe"))))
    n_cpu_ffn = min(n_layer, max(0, _int(cfg.get("n_cpu_ffn"))))
    kv_offload = cfg.get("kv_offload", True)
    load_mode = cfg.get("load_mode", "auto")
    mmap = load_mode in ("auto", "mmap", "mmap+mlock", "dio")
    swa_full = bool(cfg.get("swa_full"))
    cache_ram = _int(cfg.get("cache_ram"), 8192)
    spec = cfg.get("spec_type", "none") or "none"

    warnings = []
    vram = {"weights": 0, "experts": 0, "kv": 0, "compute": 0, "overhead": 0, "mmproj": 0, "draft": 0}
    ram = {"weights": 0, "experts": 0, "ffn": 0, "kv": 0, "compute": 0, "output": 0, "cache": 0,
           "ngram": 0, "lazy": 0, "mmproj": 0, "draft": 0}

    # ---- poids, couche par couche
    gpu_start = n_layer - min(ngl, n_layer)
    bk, bv = prof.KV_BYTES.get(type_k, 2.0), prof.KV_BYTES.get(type_v, 2.0)
    swa_tokens = ctx
    if p["sliding_window"] and not swa_full:
        swa_tokens = min(ctx, (p["sliding_window"] + ubatch + 255) // 256 * 256)
    n_gpu_layers = 0
    for L in p["layers"]:
        i = L["i"]
        on_gpu = has_gpu and i >= gpu_start
        exp = L["expert_bytes"] if i < n_cpu_moe else 0
        ffn = L["ffn_bytes"] if (i < n_cpu_ffn and not L["expert_bytes"]) else 0
        rest = L["bytes"] - exp - ffn
        if on_gpu:
            n_gpu_layers += 1
            vram["weights"] += rest
            ram["experts"] += exp
            ram["ffn"] += ffn
        else:
            ram["weights"] += rest
            ram["experts"] += exp
            ram["ffn"] += ffn
        # cache KV / état récurrent de la couche
        if L["kind"] in ("attn", "swa"):
            toks = swa_tokens if L["kind"] == "swa" else ctx
            if L["kind"] == "swa":
                toks *= n_par  # une fenêtre par conversation
            kv = toks * (L["k_elems"] * bk + L["v_elems"] * bv)
        elif L["kind"] == "recurrent":
            kv = L["rec_state"] * n_par
        else:
            kv = 0
        if on_gpu and kv_offload:
            vram["kv"] += kv
        else:
            ram["kv"] += kv
    ram["weights"] += p["token_embd"] + p["other"]
    if has_gpu and ngl > n_layer:
        vram["weights"] += p["output"]
    else:
        ram["weights"] += p["output"]
    # per_layer_token_embd (gemma « E ») : > 4 Gio et mmap → lu à la demande sur le SSD
    if p["per_layer_embd"]:
        if mmap and p["per_layer_embd"] > 4 * GiB and cfg.get("lazy_mode", "auto") != "off":
            ram["lazy"] = p["per_layer_embd"]
        else:
            ram["weights"] += p["per_layer_embd"]
    # projecteur multimodal
    mm = p["mmproj_bytes"] + _int(cfg.get("mmproj_size"))
    if mm:
        if has_gpu and cfg.get("mmproj_offload", True):
            vram["mmproj"] = mm
        else:
            ram["mmproj"] = mm

    # ---- tampons (calibrés sur la projection de llama.cpp, --fit)
    # Le tampon de calcul est dominé par les logits d'un micro-batch (ubatch × vocab
    # × 4 octets). Sans flash-attention s'ajoute la matrice d'attention
    # (ubatch × contexte × têtes × 4 octets), que l'allocateur recycle en partie.
    n_vocab = p["n_vocab"] or 32000
    ram["output"] = n_vocab * n_par * 4
    base = ubatch * (n_vocab + 4 * p["n_embd"]) * 4
    longest = ctx if p["n_attn"] else (swa_tokens if p["n_swa"] else 0)
    if fa_on:
        compute = base + ubatch * longest * 2
    else:
        compute = max(base, int(ubatch * longest * (p["n_head"] or 1) * 4 * 1.08))
    if has_gpu and ngl > 0:
        vram["compute"] = compute
        ram["compute"] = ubatch * p["n_embd"] * 20
        vram["overhead"] = OVERHEAD.get(gpus[0]["vendor"], OVERHEAD["other"]) * len(gpus)
    else:
        ram["compute"] = compute
    if cache_ram > 0:
        ram["cache"] = cache_ram * MiB
    elif cache_ram < 0:
        ram["cache"] = 0
    if spec.startswith("ngram"):
        ram["ngram"] = 64 * MiB

    # ---- modèle brouillon (décodage spéculatif)
    if spec.startswith("draft") and draft_profile:
        d = draft_profile
        dk = prof.kv_bytes_per_token(d, cfg.get("draft_type_k", "f16"), cfg.get("draft_type_v", "f16")) * ctx
        if has_gpu and cfg.get("draft_ngl", "all") != 0:
            vram["draft"] = d["weights_bytes"] + dk
        else:
            ram["draft"] = d["weights_bytes"] + dk

    # ---- répartition multi-GPU (proportionnelle à la VRAM, ou --tensor-split)
    vram_by_gpu = []
    total_vram_need = sum(vram.values())
    shares = _shares(gpus, cfg.get("tensor_split"))
    for g, s in zip(gpus, shares):
        segs = {k: v * s for k, v in vram.items() if k != "overhead"}
        segs["overhead"] = vram["overhead"] / max(1, len(gpus))
        need = sum(segs.values())
        free = g["vram_total"] - g["vram_used"]
        vram_by_gpu.append({"index": g["index"], "name": g["name"], "total": g["vram_total"],
                            "used_other": g["vram_used"], "segments": segs, "need": need,
                            "fits": need <= free, "over": max(0, need - free),
                            "margin": free - need})
        if need > free:
            warnings.append({"level": "error", "key": "vram",
                             "text": f"Dépasse la VRAM de {g['name']} de {(need - free) / GiB:.1f} Gio : "
                                     "le chargement échouera (ou llama.cpp ira chercher de la RAM, ce qui est très lent). "
                                     "Réduisez le contexte, quantifiez le cache KV, ou gardez moins de couches sur le GPU."})
        elif free - need < 512 * MiB:
            warnings.append({"level": "warn", "key": "vram",
                             "text": f"Marge VRAM très faible sur {g['name']} ({(free - need) / MiB:.0f} Mio). "
                                     "Les pilotes et l'affichage grignotent aussi : gardez ≈ 0,5-1 Gio de marge."})

    # ---- RAM : ce qui doit être résident vs ce que mmap peut renvoyer au SSD
    ram_total = hw["ram"]["total"]
    ram_avail = hw["ram"]["available"]
    resident = ram["kv"] + ram["compute"] + ram["output"] + ram["mmproj"] + ram["draft"] + ram["ngram"]
    weights_cpu = ram["weights"] + ram["experts"] + ram["ffn"]
    streamed = 0
    if mmap:
        room = ram_avail - resident - 1 * GiB
        if 0 < weights_cpu <= room and weights_cpu > 0.85 * room:
            warnings.append({"level": "warn", "key": "ram",
                             "text": f"Marge RAM faible : {weights_cpu / GiB:.1f} Gio de poids pour {room / GiB:.1f} Gio "
                                     "disponibles. Le cache de prompts et le cache disque (embeddings lus à la demande) se "
                                     "partageront le reste ; fermez les autres programmes gourmands, ou allouez plus de RAM."})
        if weights_cpu > room:
            streamed = weights_cpu - max(0, room)
            warnings.append({"level": "error" if streamed > 2 * GiB else "warn", "key": "ram",
                             "text": f"{weights_cpu / GiB:.1f} Gio de poids doivent tenir en RAM mais il n'y a que "
                                     f"{max(0, room) / GiB:.1f} Gio de libres : environ {streamed / GiB:.1f} Gio seront "
                                     "relus depuis le SSD à chaque jeton (mmap). Ça fonctionne, mais à quelques jetons "
                                     "par seconde au mieux. Choisissez une quantification plus petite ou un modèle plus petit."})
    else:
        if weights_cpu + resident > ram_avail:
            warnings.append({"level": "error", "key": "ram",
                             "text": f"Sans mmap, tout doit être copié en RAM : il manque "
                                     f"{(weights_cpu + resident - ram_avail) / GiB:.1f} Gio. Le chargement échouera."})
    if ram["lazy"]:
        what = "d'embeddings n-gram" if p.get("ngram_embd") else "d'embeddings par couche"
        warnings.append({"level": "info", "key": "lazy",
                         "text": f"Ce modèle a {ram['lazy'] / GiB:.1f} Gio {what} lus à la demande depuis le SSD "
                                 "(mode « lazy » de llama.cpp, tenseurs > 4 Gio) : ils n'occupent ni VRAM ni RAM, seule "
                                 "la ligne du jeton courant est lue. Un SSD NVMe rend ça indolore ; sur disque dur, ce serait lent."})
    if ram["cache"] and weights_cpu + resident + ram["cache"] > ram_total:
        warnings.append({"level": "info", "key": "cache",
                         "text": "Le cache de prompts (--cache-ram) peut grossir jusqu'à "
                                 f"{ram['cache'] / GiB:.1f} Gio : au-delà de la RAM disponible il n'apportera rien, réduisez-le."})

    # ---- SSD
    disk = hw.get("disk", {})
    model_on_disk = bool(cfg.get("model_present", True))
    ssd = {"total": disk.get("total", 0), "free": disk.get("free", 0),
           "segments": {"other": max(0, disk.get("used", 0) - _int(cfg.get("models_dir_bytes"))),
                        "models": max(0, _int(cfg.get("models_dir_bytes")) - (p["file_size"] if model_on_disk else 0)),
                        "this_model": p["file_size"] if model_on_disk else 0,
                        "download": 0 if model_on_disk else p["file_size"]},
           "streamed": streamed, "lazy": ram["lazy"]}
    if not model_on_disk and p["file_size"] > disk.get("free", 0):
        warnings.append({"level": "error", "key": "ssd",
                         "text": f"Pas assez d'espace disque pour télécharger ce modèle ({p['file_size'] / GiB:.1f} Gio)."})

    # ---- conseils généraux
    if ctx > p["n_ctx_train"] > 0:
        warnings.append({"level": "warn", "key": "ctx",
                         "text": f"Contexte demandé ({ctx:,}) supérieur à celui de l'entraînement "
                                 f"({p['n_ctx_train']:,}) : le modèle divaguera au-delà."})
    if type_v not in ("f16", "f32", "bf16") and not fa_on:
        warnings.append({"level": "error", "key": "kv",
                         "text": "Un cache V quantifié exige flash-attention activé."})
    if p["is_moe"] and has_gpu and ngl < n_layer and n_cpu_moe == 0:
        warnings.append({"level": "tip", "key": "moe",
                         "text": "Modèle MoE : plutôt que d'enlever des couches du GPU, gardez-les toutes et déportez "
                                 "les experts en RAM (--n-cpu-moe). Seuls quelques experts servent à chaque jeton, "
                                 "la RAM suffit alors largement en vitesse."})
    cpu = hw.get("cpu", {})
    if _int(cfg.get("threads")) > cpu.get("cores", 99):
        warnings.append({"level": "tip", "key": "threads",
                         "text": f"Au-delà des {cpu.get('cores')} cœurs physiques, les threads supplémentaires "
                                 "(hyperthreading) ralentissent souvent la génération."})
    if n_par > 1:
        warnings.append({"level": "info", "key": "parallel",
                         "text": f"{n_par} conversations en parallèle : le contexte total ({ctx:,}) est partagé, "
                                 f"soit ≈ {ctx // n_par:,} jetons par conversation."})

    return {
        "resolved": {"ngl": ngl, "n_gpu_layers": n_gpu_layers, "n_layer": n_layer, "ctx": ctx,
                     "fa": fa_on, "swa_tokens": swa_tokens, "n_cpu_moe": n_cpu_moe, "mmap": mmap,
                     "kv_per_token": prof.kv_bytes_per_token(p, type_k, type_v)},
        "vram": vram_by_gpu, "vram_need": total_vram_need,
        "ram": {"total": ram_total, "available": ram_avail, "used_other": ram_total - ram_avail,
                "segments": ram, "resident": resident, "weights_cpu": weights_cpu, "streamed": streamed,
                "fits": streamed == 0},
        "ssd": ssd,
        "warnings": warnings,
    }


def _shares(gpus, tensor_split):
    if not gpus:
        return []
    vals = None
    if tensor_split:
        try:
            vals = [float(x) for x in str(tensor_split).split(",")]
        except ValueError:
            vals = None
    if not vals or len(vals) != len(gpus):
        vals = [g["vram_total"] for g in gpus]
    s = sum(vals) or 1
    return [v / s for v in vals]
