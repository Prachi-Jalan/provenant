"""Text -> vector. Two interchangeable embedders, picked by EMBEDDER in .env.

  openai : text-embedding-3-small, 1536 dims. Understands meaning (best quality, needs OPENAI_API_KEY).
  hash   : 512 dims, no API, no downloads. Hashes words + word pairs into buckets.
           Only catches overlapping wording, but works offline and in tests.

Default: openai if OPENAI_API_KEY is set, otherwise hash.
IMPORTANT: TigerGraph stores vectors with a fixed size, so the embedder used at
setup time must be the one used at query time (setup_attack_vectors.py checks this).
"""
from __future__ import annotations

import hashlib
import math
import os
import re

ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")
NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
WORD_RE = re.compile(r"[a-z]+")
HASH_DIM = 512
OPENAI_MODEL, OPENAI_DIM = "text-embedding-3-small", 1536


def normalise(text: str) -> str:
    """Replace addresses and numbers with placeholders so they don't dominate the meaning."""
    return NUM_RE.sub(" num ", ADDR_RE.sub(" walletaddress ", text)).lower()


def embedder_name() -> str:
    return os.getenv("EMBEDDER") or ("openai" if os.getenv("OPENAI_API_KEY") else "hash")


def dim() -> int:
    return OPENAI_DIM if embedder_name() == "openai" else HASH_DIM


def _hash_embed(text: str) -> list[float]:
    words = WORD_RE.findall(normalise(text))
    feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    v = [0.0] * HASH_DIM
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)  # stable across runs (unlike hash())
        v[h % HASH_DIM] += 1.0 if (h >> 9) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts: list[str]) -> list[list[float]]:
    if embedder_name() == "openai":
        from openai import OpenAI
        resp = OpenAI().embeddings.create(model=OPENAI_MODEL, input=[normalise(t) for t in texts])
        return [d.embedding for d in resp.data]
    return [_hash_embed(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# How similar is "similar enough to call it an attack"? Different embedders give
# different score ranges, so each needs its own threshold. Tune these in the eval.
THRESHOLDS = {"hash": 0.25, "openai": 0.55}  # hash: attacks scored 0.30-0.55, normal chat 0.14-0.19


def threshold() -> float:
    return float(os.getenv("ATTACK_SIM_THRESHOLD") or THRESHOLDS[embedder_name()])
