"""Configuration conseillée pour UN modèle sur CETTE machine, avec les raisons.
Stratégie : partir de l'idéal (tout sur le GPU, contexte confortable, cache KV en
f16) et ne sacrifier que dans l'ordre le moins douloureux :
  1. cache KV en q8_0 (perte imperceptible, −47 % de mémoire de contexte)
  2. contexte réduit (jusqu'à 8k)
  3. MoE : experts déportés en RAM, couche par couche (--n-cpu-moe)
     dense : moins de couches sur le GPU (-ngl), ce qui coûte vite en vitesse
  4. en dernier recours : tout sur CPU
"""
from . import estimate as est_mod

MiB = 1024 ** 2
GiB = 1024 ** 3


def defaults(hw):
    cores = hw.get("cpu", {}).get("cores") or 4
    ram_avail = hw.get("ram", {}).get("available", 0)
    return {
        "ngl": "all", "ctx": 32768, "type_k": "f16", "type_v": "f16", "flash_attn": "on",
        "n_cpu_moe": 0, "cpu_moe_all": False, "n_cpu_ffn": 0,
        "threads": cores, "threads_batch": cores, "batch": 2048, "ubatch": 512, "parallel": 1,
        "kv_offload": True, "load_mode": "auto", "swa_full": False,
        "cache_ram": int(min(8192, max(0, ram_avail // 4) // MiB)), "cache_reuse": 0,
        "spec_type": "none", "spec_n_max": 16, "draft_model": "", "draft_ngl": "all",
        "reasoning": "auto", "reasoning_budget": -1, "mmproj_offload": True,
        "devices": [], "tensor_split": "", "split_mode": "", "fit": "on",
        "port": 8080, "host": "127.0.0.1", "extra_args": "",
    }


def _n(x):
    """12 345 → « 12 345 » (espace fine insécable, usage français)."""
    return f"{int(x):,}".replace(",", "\u202f")


MARGIN = 768 * MiB   # marge de VRAM exigée en plus du surcoût pilote déjà compté
CONTEXT_KEYS = ("mmproj_size", "model_present", "models_dir_bytes", "port", "extra_args", "devices", "tensor_split")


def _fits(e):
    return all(g["margin"] >= MARGIN for g in e["vram"]) and e["ram"]["streamed"] == 0 \
        and not any(w["level"] == "error" for w in e["warnings"])


def recommend(p, hw, goal="balanced", draft_profile=None, base=None):
    cfg = defaults(hw)
    for k in CONTEXT_KEYS:  # informations qui ne sont pas des choix (mmproj, disque…)
        if base and k in base:
            cfg[k] = base[k]
    why = {}   # param → explication (la dernière l'emporte, l'ordre d'insertion est gardé)
    gpus = hw.get("gpus", [])
    has_gpu = bool(gpus)
    n_layer = p["n_layer"]
    train = p["n_ctx_train"] or 32768
    est = lambda: est_mod.estimate(p, cfg, hw, draft_profile)  # noqa: E731

    # -- objectif de contexte selon le but
    agent = goal == "agent"
    if agent:
        target = min(train, 131072)
        cfg["cache_reuse"] = 256
        why["ctx"] = ("Agent de code : le prompt système de Claude Code ou Codex pèse déjà 15-20k jetons et "
                      "chaque tour renvoie tout l'historique — on vise 128k, et jamais moins de 64k.")
        why["cache_reuse"] = ("--cache-reuse 256 : quand l'agent renvoie la conversation avec un début modifié "
                              "(résumé, message effacé), llama-server recycle le cache KV par blocs de 256 jetons "
                              "au lieu de tout recalculer.")
        why["kv"] = ("Cache KV en q8_0 d'emblée : avec 64-128k de contexte, c'est ce qui libère le plus de VRAM "
                     "pour une perte imperceptible.")
        cfg["type_k"] = cfg["type_v"] = "q8_0"
    elif goal == "context":
        target = min(train, 131072)
        why["ctx"] = ("Objectif « contexte » : on vise le plus long contexte possible sans dépasser "
                      f"celui de l'entraînement ({_n(train)}).")
    elif goal == "speed":
        target = min(train, 16384)
        why["ctx"] = ("Objectif « vitesse » : contexte modeste (16k) pour garder la VRAM aux poids et "
                      "au tampon de calcul.")
    else:
        target = min(train, 32768)
        why["ctx"] = (f"{_n(target)} jetons ≈ {_n(int(target * 0.75))} mots : confortable pour une longue conversation "
                      "ou un document, sans gaspiller de mémoire (le cache KV grandit avec le contexte).")
    cfg["ctx"] = target

    cores = hw["cpu"]["cores"]
    why["threads"] = (f"{cores} threads = vos cœurs physiques. Les threads « hyperthreading » en plus "
                      "n'aident pas : la génération est limitée par la bande passante mémoire, pas le calcul.")
    why["cache_ram"] = (f"Cache de prompts plafonné à {cfg['cache_ram']} Mio (≈ ¼ de la RAM libre) : il "
                        "garde les conversations récentes pour ne pas tout recalculer en y revenant.")
    why["parallel"] = ("1 conversation à la fois : tout le contexte va à cette conversation. Montez à 2-4 "
                       "seulement si plusieurs clients interrogent le serveur en même temps.")

    if not has_gpu:
        cfg["ngl"] = 0
        cfg["flash_attn"] = "auto"
        cfg["ctx"] = min(target, 8192)
        why["ngl"] = "Pas de GPU : tout s'exécute sur le CPU. Comptez quelques jetons/s pour un 7B en Q4."
        why["ctx"] = "Sur CPU, le traitement du prompt est lent : 8k de contexte est un bon plafond."
        return _finish(cfg, why, est(), p, hw)

    why["flash_attn"] = ("Flash-attention activée : même calcul, tampon d'attention bien plus petit (et "
                         "obligatoire pour quantifier le cache KV).")

    # -- 1. l'idéal : tout sur le GPU, KV f16
    e = est()
    if _fits(e):
        why["ngl"] = (f"Tout le modèle ({n_layer} couches + sortie) tient dans la VRAM avec ce contexte : "
                      "c'est la configuration la plus rapide.")
        return _finish(cfg, why, e, p, hw)

    # -- 2. KV en q8_0
    if not agent:
        cfg["type_k"] = cfg["type_v"] = "q8_0"
        why["kv"] = ("Cache KV en q8_0 : la mémoire de contexte est divisée par ≈ 1,9 pour une perte de "
                     "qualité imperceptible (c'est le premier levier à actionner quand la VRAM manque).")
        e = est()
        if _fits(e):
            why["ngl"] = "Avec le cache KV compressé, tout le modèle tient sur le GPU."
            return _finish(cfg, why, e, p, hw)
    floor = 65536 if agent else 8192

    # -- 3. MoE : experts en RAM (le contexte est conservé, les experts coûtent peu en vitesse)
    if p["is_moe"] and p["expert_bytes"]:
        for ctx in _ctx_ladder(target, floor):
            cfg["ctx"] = ctx
            best = _bisect(lambda n: _set_and_fit(cfg, "n_cpu_moe", n, est), 0, n_layer, smallest=True)
            if best is not None:
                cfg["n_cpu_moe"] = best
                e = est()
                if ctx != target:
                    why["ctx"] = (f"Contexte ramené à {_n(ctx)} : même avec tous les experts en RAM, l'attention "
                                  "et le cache KV ne tenaient pas avec un contexte plus grand.")
                why["n_cpu_moe"] = (f"Modèle MoE : les experts des {best} premières couches restent en RAM, le "
                                    "reste (attention, experts des autres couches) sur le GPU. Comme seuls "
                                    f"{p['n_expert_used']}/{p['n_expert']} experts servent par jeton, la RAM "
                                    "suffit en débit : on garde une bonne vitesse avec un modèle trop gros pour la VRAM.")
                why["ngl"] = "Toutes les couches restent sur le GPU : seuls des experts sont déportés."
                return _finish(cfg, why, e, p, hw)
        cfg["n_cpu_moe"] = 0
        cfg["ctx"] = target

    # -- 4. dense : contexte réduit jusqu'à 16k, puis moins de couches sur le GPU
    for ctx in _ctx_ladder(target, max(floor, 16384)):
        cfg["ctx"] = ctx
        e = est()
        if _fits(e):
            why["ctx"] = (f"Contexte ramené à {_n(ctx)} : c'est ce qui permet de garder TOUT le modèle sur "
                          "le GPU. Un modèle entier sur GPU avec moins de contexte est bien plus rapide "
                          "qu'un modèle à moitié en RAM avec un grand contexte.")
            why["ngl"] = "Tout le modèle sur le GPU."
            return _finish(cfg, why, e, p, hw)
    for ctx in _ctx_ladder(min(target, max(floor, 16384)), floor):
        cfg["ctx"] = ctx
        best = _bisect(lambda n: _set_and_fit(cfg, "ngl", n, est), 0, n_layer + 1, smallest=False)
        if best:
            cfg["ngl"] = best
            e = est()
            pct = 100 * best / (n_layer + 1)
            if ctx != target:
                why["ctx"] = f"Contexte ramené à {_n(ctx)} pour garder un maximum de couches sur le GPU."
            why["ngl"] = (f"{best} couches sur {n_layer} vont sur le GPU ({pct:.0f} %), le reste en RAM. La "
                          "vitesse chute avec chaque couche laissée au CPU : si c'est trop lent, prenez une "
                          "quantification plus petite (Q3, IQ2) ou un modèle plus petit.")
            if pct < 50:
                why["model"] = ("Moins de la moitié du modèle sur le GPU : ce modèle est vraiment trop gros "
                                "pour cette carte. Une quantification plus petite ou un modèle MoE sera bien plus agréable.")
            return _finish(cfg, why, e, p, hw)
    # -- 5. rien ne tient : meilleur effort, le verdict et les avertissements disent pourquoi
    if agent:
        why["agent"] = ("Impossible de garantir 64k de contexte avec ce modèle sur cette machine : un agent de "
                        "code saturera vite. Prenez un modèle plus petit (ex. Qwen3 Coder 30B-A3B en Q4) ou une "
                        "quantification plus légère.")
    cfg["ctx"] = 8192
    if p["is_moe"] and p["expert_bytes"]:
        cfg["ngl"], cfg["cpu_moe_all"] = "all", True
        why["ngl"] = ("Rien ne tient vraiment : attention sur le GPU, tous les experts en RAM. Lisez les "
                      "avertissements — une quantification plus petite est probablement nécessaire.")
    else:
        cfg["ngl"] = 0
        why["ngl"] = "Même une seule couche ne tient pas avec le contexte minimal : exécution sur CPU."
    e = est()
    return _finish(cfg, why, e, p, hw)


def _ctx_ladder(start, floor):
    """start, start/2, … jusqu'à floor (inclus)."""
    out, c = [], start
    while c >= floor:
        out.append(c)
        c //= 2
    if not out:
        out.append(start)
    return out


def _set_and_fit(cfg, key, value, est):
    cfg[key] = value
    return _fits(est())


def _bisect(ok, lo, hi, smallest):
    """Plus petite (ou plus grande) valeur de [lo, hi] pour laquelle ok(v) est vrai ;
    suppose ok monotone. None si aucune."""
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if ok(mid):
            best = mid
            if smallest:
                hi = mid - 1
            else:
                lo = mid + 1
        elif smallest:
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _finish(cfg, why, e, p, hw):
    verdict = _verdict(e, p, hw)
    order = ("agent", "ngl", "n_cpu_moe", "ctx", "kv", "cache_reuse", "flash_attn", "threads", "cache_ram", "parallel", "model")
    items = sorted(why.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
    return {"cfg": cfg, "why": [{"param": k, "text": t} for k, t in items], "estimate": e, "verdict": verdict}


def _verdict(e, p, hw):
    if e["vram"]:
        g = e["vram"][0]
        if g["fits"] and e["ram"]["streamed"] == 0:
            return (f"Ça tient : {g['need'] / GiB:.1f} Gio de VRAM utilisés sur {g['name']}, "
                    f"marge {g['margin'] / GiB:.1f} Gio.")
        if not g["fits"]:
            return f"Ne tient pas en VRAM (il manque {g['over'] / GiB:.1f} Gio)."
    if e["ram"]["streamed"]:
        return f"La RAM ne suffit pas : {e['ram']['streamed'] / GiB:.1f} Gio seraient relus depuis le SSD."
    return "Exécution sur CPU : ça fonctionne, mais lentement."
