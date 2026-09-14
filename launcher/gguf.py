"""Lecture de l'en-tête GGUF (métadonnées + liste des tenseurs) SANS charger les
poids. Fonctionne sur un fichier local ou sur une URL (requêtes Range : on ne
télécharge que les premiers Mo). Sert à calculer où va la mémoire.

Format (https://github.com/ggml-org/ggml/blob/master/docs/gguf.md) :
  magic "GGUF", version u32, n_tensors u64, n_kv u64,
  n_kv × (clé string, type u32, valeur), puis
  n_tensors × (nom string, n_dims u32, dims u64[], type u32, offset u64),
  puis les données alignées (general.alignment, 32 par défaut).
"""
import os
import struct

from . import download

GGUF_MAGIC = b"GGUF"
T_U8, T_I8, T_U16, T_I16, T_U32, T_I32, T_F32, T_BOOL, T_STR, T_ARR, T_U64, T_I64, T_F64 = range(13)
_SCALAR = {T_U8: ("<B", 1), T_I8: ("<b", 1), T_U16: ("<H", 2), T_I16: ("<h", 2), T_U32: ("<I", 4),
           T_I32: ("<i", 4), T_F32: ("<f", 4), T_BOOL: ("<?", 1), T_U64: ("<Q", 8), T_I64: ("<q", 8),
           T_F64: ("<d", 8)}

# (octets par bloc, éléments par bloc) des types ggml — pour la taille des tenseurs
# quand on ne peut pas la déduire des offsets. Les types inconnus tombent dans le
# calcul par offsets, qui est exact et ne dépend pas de cette table.
GGML_TYPES = {
    0: ("F32", 4, 1), 1: ("F16", 2, 1), 2: ("Q4_0", 18, 32), 3: ("Q4_1", 20, 32),
    6: ("Q5_0", 22, 32), 7: ("Q5_1", 24, 32), 8: ("Q8_0", 34, 32), 9: ("Q8_1", 40, 32),
    10: ("Q2_K", 84, 256), 11: ("Q3_K", 110, 256), 12: ("Q4_K", 144, 256), 13: ("Q5_K", 176, 256),
    14: ("Q6_K", 210, 256), 15: ("Q8_K", 292, 256), 16: ("IQ2_XXS", 66, 256), 17: ("IQ2_XS", 74, 256),
    18: ("IQ3_XXS", 98, 256), 19: ("IQ1_S", 50, 256), 20: ("IQ4_NL", 18, 32), 21: ("IQ3_S", 110, 256),
    22: ("IQ2_S", 82, 256), 23: ("IQ4_XS", 136, 256), 24: ("I8", 1, 1), 25: ("I16", 2, 1),
    26: ("I32", 4, 1), 27: ("I64", 8, 1), 28: ("F64", 8, 1), 29: ("IQ1_M", 56, 256), 30: ("BF16", 2, 1),
    34: ("TQ1_0", 54, 256), 35: ("TQ2_0", 66, 256), 39: ("MXFP4", 17, 32),
}
# Nom lisible du type de fichier (general.file_type) — uniquement informatif
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
              11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S",
              17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS",
              23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S",
              29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4"}

# Clés dont on garde la valeur (les tableaux du tokenizer sont volumineux et inutiles ici)
SKIP_ARRAY_KEYS = ("tokenizer.ggml.tokens", "tokenizer.ggml.merges", "tokenizer.ggml.scores",
                   "tokenizer.ggml.token_type")


class _Source:
    """Lecture séquentielle bufferisée, locale ou distante (Range)."""
    def __init__(self, path=None, url=None):
        self.path, self.url = path, url
        self.pos = 0
        self.buf = b""
        self.buf_start = 0
        self.chunk = 4 * 1024 * 1024
        if path:
            self.size = os.path.getsize(path)
            self.f = open(path, "rb")
        else:
            self.size = download.content_length(url)
            self.f = None

    def close(self):
        if self.f:
            self.f.close()

    def read(self, n):
        end = self.pos + n
        if self.f:
            self.f.seek(self.pos)
            data = self.f.read(n)
        else:
            while self.buf_start + len(self.buf) < end:
                # on rallonge le tampon distant, par blocs de plus en plus gros
                start = self.buf_start + len(self.buf)
                stop = min(start + self.chunk - 1, self.size - 1)
                if stop < start:
                    break
                self.buf += download.fetch_range(self.url, start, stop)
                self.chunk = min(self.chunk * 2, 64 * 1024 * 1024)
            data = self.buf[self.pos - self.buf_start:end - self.buf_start]
        if len(data) < n:
            raise EOFError("en-tête GGUF tronqué")
        self.pos = end
        return data

    def skip(self, n):
        self.pos += n


def _read_str(src):
    (n,) = struct.unpack("<Q", src.read(8))
    return src.read(n).decode("utf-8", "replace")


def _read_value(src, t, key=""):
    if t in _SCALAR:
        fmt, size = _SCALAR[t]
        return struct.unpack(fmt, src.read(size))[0]
    if t == T_STR:
        return _read_str(src)
    if t == T_ARR:
        (et,) = struct.unpack("<I", src.read(4))
        (n,) = struct.unpack("<Q", src.read(8))
        if key in SKIP_ARRAY_KEYS or n > 4096:
            # on saute sans stocker ; les scalaires ont une taille fixe, les chaînes non
            if et in _SCALAR:
                src.skip(_SCALAR[et][1] * n)
            else:
                for _ in range(n):
                    _read_value(src, et)
            return {"__array_len__": n}
        return [_read_value(src, et) for _ in range(n)]
    raise ValueError(f"type GGUF inconnu {t}")


def read_header(path=None, url=None):
    """Renvoie {"meta": {...}, "tensors": [{name, dims, type, type_name, offset, bytes}],
    "file_size", "data_offset"}. Les tailles de tenseurs viennent des offsets
    (exactes) ; repli sur la table des types pour le dernier tenseur si besoin."""
    src = _Source(path, url)
    try:
        if src.read(4) != GGUF_MAGIC:
            raise ValueError("pas un fichier GGUF")
        (version,) = struct.unpack("<I", src.read(4))
        if version < 2:
            raise ValueError(f"GGUF v{version} trop ancien")
        n_tensors, n_kv = struct.unpack("<QQ", src.read(16))
        meta = {}
        for _ in range(n_kv):
            key = _read_str(src)
            (t,) = struct.unpack("<I", src.read(4))
            meta[key] = _read_value(src, t, key)
        tensors = []
        for _ in range(n_tensors):
            name = _read_str(src)
            (nd,) = struct.unpack("<I", src.read(4))
            dims = list(struct.unpack("<" + "Q" * nd, src.read(8 * nd)))
            t, off = struct.unpack("<IQ", src.read(12))
            tensors.append({"name": name, "dims": dims, "type": t,
                            "type_name": GGML_TYPES.get(t, ("?%d" % t,))[0], "offset": off})
        align = meta.get("general.alignment", 32) or 32
        data_offset = (src.pos + align - 1) // align * align
    finally:
        src.close()
    data_size = src.size - data_offset
    by_off = sorted(tensors, key=lambda x: x["offset"])
    for i, t in enumerate(by_off):
        nxt = by_off[i + 1]["offset"] if i + 1 < len(by_off) else data_size
        t["bytes"] = max(0, nxt - t["offset"])
        if t["bytes"] == 0 or i + 1 == len(by_off):
            # dernier tenseur : vérifie avec la table des types quand on la connaît
            info = GGML_TYPES.get(t["type"])
            if info:
                n = 1
                for d in t["dims"]:
                    n *= d
                calc = n // info[2] * info[1]
                if t["bytes"] == 0 or abs(calc - t["bytes"]) > info[1] * 64:
                    t["bytes"] = calc
    return {"version": version, "meta": meta, "tensors": tensors,
            "file_size": src.size, "data_offset": data_offset}


def quant_label(meta):
    ft = meta.get("general.file_type")
    return FILE_TYPES.get(ft, f"type {ft}" if ft is not None else "?")
