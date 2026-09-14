"""Profil mémoire d'un modèle, calculé depuis l'en-tête GGUF : taille de chaque
couche (et de ses experts / FFN), nature de chaque couche (attention complète,
fenêtre glissante, récurrente), coût du cache KV par jeton… C'est la matière
première de l'estimateur (estimate.py)."""
import re

from . import gguf

GiB = 1024 ** 3

# Octets par élément des types de cache KV autorisés par llama-server
KV_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32, "q4_1": 20 / 32,
            "iq4_nl": 18 / 32, "q5_0": 22 / 32, "q5_1": 24 / 32}


def _per_layer(meta, key, n_layer, i, default=None):
    """Une clé peut être un scalaire ou un tableau par couche."""
    v = meta.get(key, default)
    if isinstance(v, list):
        return v[i] if i < len(v) else default
    return v


def build(header, source_name=""):
    meta = header["meta"]
    arch = meta.get("general.architecture", "?")
    k = lambda name, d=None: meta.get(f"{arch}.{name}", d)  # noqa: E731
    n_layer = int(k("block_count", 0) or 0)
    n_embd = int(k("embedding_length", 0) or 0)
    n_head = k("attention.head_count", 0) or 0
    n_head_kv_raw = k("attention.head_count_kv", n_head)
    if isinstance(n_head, list):
        n_head = max(n_head)
    n_head_kv_default = max(n_head_kv_raw) if isinstance(n_head_kv_raw, list) else (n_head_kv_raw or n_head)
    key_len = k("attention.key_length", (n_embd // n_head) if n_head else 0) or 0
    val_len = k("attention.value_length", key_len) or key_len
    key_len_swa = k("attention.key_length_swa", key_len) or key_len
    val_len_swa = k("attention.value_length_swa", val_len) or val_len
    n_vocab = 0
    tok = meta.get("tokenizer.ggml.tokens")
    if isinstance(tok, dict):
        n_vocab = tok.get("__array_len__", 0)
    elif isinstance(tok, list):
        n_vocab = len(tok)
    n_ff = int(k("feed_forward_length", 0) or 0)
    if isinstance(n_ff, list):
        n_ff = max(n_ff)
    n_expert = int(k("expert_count", 0) or 0)
    n_expert_used = int(k("expert_used_count", 0) or 0)
    swa = int(k("attention.sliding_window", 0) or 0)
    pattern = k("attention.sliding_window_pattern")
    shared_kv = int(k("attention.shared_kv_layers", 0) or 0)
    full_interval = int(k("full_attention_interval", 0) or 0)
    kv_lora = int(k("attention.kv_lora_rank", 0) or 0)
    rope_dim = int(k("rope.dimension_count", 0) or 0)
    # état récurrent (Mamba / DeltaNet), par couche et par séquence, en f32
    ssm_conv = int(k("ssm.conv_kernel", 0) or 0)
    ssm_inner = int(k("ssm.inner_size", 0) or 0)
    ssm_state = int(k("ssm.state_size", 0) or 0)
    ssm_group = int(k("ssm.group_count", 1) or 1)
    rec_state = 0
    if ssm_inner and ssm_state:
        rec_state = (max(ssm_conv - 1, 0) * (ssm_inner + 2 * ssm_group * ssm_state) + ssm_inner * ssm_state) * 4

    layers = [{"i": i, "bytes": 0, "expert_bytes": 0, "ffn_bytes": 0, "has_attn": False,
               "has_ssm": False} for i in range(n_layer)]
    token_embd = output = per_layer_embd = mmproj = other = 0
    re_blk = re.compile(r"^blk\.(\d+)\.(.+)$")
    for t in header["tensors"]:
        name, b = t["name"], t["bytes"]
        m = re_blk.match(name)
        if m:
            i = int(m.group(1))
            if i >= n_layer:
                other += b
                continue
            L = layers[i]
            L["bytes"] += b
            rest = m.group(2)
            if re.search(r"_(ch)?exps\b", rest):  # experts MoE (« chexps » = experts par morceaux ; les _shexp partagés restent denses)
                L["expert_bytes"] += b
            elif re.match(r"ffn_(up|down|gate)(\.weight|\.bias)?$", rest):
                L["ffn_bytes"] += b
            if rest.startswith(("attn_k.", "attn_kv_a_mqa", "attn_kv_b", "attn_qkv.")) and not rest.startswith("attn_k_norm"):
                L["has_attn"] = True
            if rest.startswith("ssm_"):
                L["has_ssm"] = True
        elif name.startswith("token_embd"):
            token_embd += b
        elif name.startswith(("output.", "output_norm")):
            output += b
        elif name.startswith("per_layer_token_embd"):
            per_layer_embd += b
        elif name.startswith(("v.", "a.", "mm.")):
            mmproj += b
        else:
            other += b

    # Nature de chaque couche et coût KV par jeton (en f16, ajusté ensuite par type)
    n_attn = n_swa = n_rec = 0
    for i, L in enumerate(layers):
        if L["has_ssm"]:
            kind = "recurrent"
        elif full_interval and (i + 1) % full_interval != 0 and not L["has_attn"]:
            kind = "recurrent"
        elif not L["has_attn"] and L["bytes"] and not (L["expert_bytes"] or L["ffn_bytes"]):
            kind = "other"
        else:
            kind = "attn"
            is_swa = False
            if swa:
                if isinstance(pattern, list):
                    is_swa = bool(pattern[i]) if i < len(pattern) else False
                elif isinstance(pattern, int) and pattern > 0:
                    is_swa = (i + 1) % pattern != 0
                elif arch == "gpt-oss":
                    is_swa = i % 2 == 0
                elif arch in ("gemma2",):
                    is_swa = i % 2 == 0
                elif arch in ("gemma3", "gemma3n"):
                    is_swa = (i + 1) % 6 != 0
                elif arch.startswith("gemma4"):
                    is_swa = (i + 1) % 6 != 0
                else:
                    is_swa = False  # fenêtre déclarée mais motif inconnu : prudence = complet
            if is_swa:
                kind = "swa"
        L["kind"] = kind
        nhkv = _per_layer(meta, f"{arch}.attention.head_count_kv", n_layer, i, n_head_kv_default) or n_head_kv_default
        if kind == "attn" or kind == "swa":
            kl, vl = (key_len_swa, val_len_swa) if kind == "swa" else (key_len, val_len)
            if kv_lora:  # MLA (DeepSeek) : une seule entrée compressée par jeton
                L["k_elems"], L["v_elems"] = kv_lora + rope_dim, 0
            else:
                L["k_elems"], L["v_elems"] = nhkv * kl, nhkv * vl
            if shared_kv and i >= n_layer - shared_kv:
                L["k_elems"] = L["v_elems"] = 0   # réutilise le cache d'une couche précédente
                L["shares_kv"] = True
        else:
            L["k_elems"] = L["v_elems"] = 0
        L["rec_state"] = rec_state if kind == "recurrent" else 0
        if kind == "attn":
            n_attn += 1
        elif kind == "swa":
            n_swa += 1
        elif kind == "recurrent":
            n_rec += 1

    weights = sum(L["bytes"] for L in layers) + token_embd + output + per_layer_embd + other
    n_params = meta.get("general.parameter_count") or 0
    return {
        "name": meta.get("general.name") or source_name,
        "arch": arch, "quant": gguf.quant_label(meta),
        "file_size": header["file_size"], "weights_bytes": weights, "mmproj_bytes": mmproj,
        "n_params": n_params, "n_layer": n_layer, "n_embd": n_embd, "n_head": n_head,
        "n_head_kv": n_head_kv_default, "n_vocab": n_vocab, "n_ff": n_ff,
        "n_ctx_train": int(k("context_length", 0) or 0),
        "n_expert": n_expert, "n_expert_used": n_expert_used, "is_moe": n_expert > 1,
        "expert_bytes": sum(L["expert_bytes"] for L in layers),
        "sliding_window": swa, "n_attn": n_attn, "n_swa": n_swa, "n_rec": n_rec,
        "token_embd": token_embd, "output": output, "per_layer_embd": per_layer_embd, "other": other,
        "layers": layers,
        "vision": bool(k("vision.block_count")) or mmproj > 0,
        "ngram_embd": bool(k("ple.ngram_size")),   # embeddings n-gram (Qwen 3.8 Flash Next)
        "n_params_active": 0,
        "chat_template": bool(meta.get("tokenizer.chat_template")),
        # Le gabarit de chat sait-il présenter des outils au modèle ? (appels de fonctions,
        # indispensables aux agents de code)
        "tools_template": "tools" in str(meta.get("tokenizer.chat_template", ""))
                          or any(k.startswith("tokenizer.chat_template.tool") for k in meta),
        "size_label": meta.get("general.size_label", ""),
    }


def kv_bytes_per_token(profile, type_k="f16", type_v="f16"):
    bk, bv = KV_BYTES.get(type_k, 2.0), KV_BYTES.get(type_v, 2.0)
    return sum(L["k_elems"] * bk + L["v_elems"] * bv for L in profile["layers"] if L["kind"] == "attn")
