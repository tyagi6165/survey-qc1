"""
embeddings.py — Multilingual semantic similarity for survey QC.

Model: paraphrase-multilingual-MiniLM-L12-v2
  - 50+ languages, including cross-lingual pairs
  - "Quel âge avez-vous?" vs "What is your age?" → 0.85+
  - ~130MB, lazy-loaded once per process

Thresholds (based on cosine similarity 0.0–1.0):
  MATCH        0.85  → same meaning, not a bug
  LIKELY_MATCH 0.70  → probably same, used for option matching
  MISMATCH     0.60  → below this is a confirmed mismatch
"""

import hashlib
import numpy as np

# ─── Thresholds ──────────────────────────────────────────────
MATCH        = 0.85
LIKELY_MATCH = 0.70
MISMATCH     = 0.60

# ─── Singleton model + per-run cache ─────────────────────────
_model = None
_cache: dict[str, np.ndarray] = {}


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    return _model


def clear_cache() -> None:
    """Drop all cached embeddings. Call once at the start of each QC run."""
    global _cache
    _cache = {}


def _key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()


def get_embedding(text: str) -> np.ndarray:
    """Return a normalised embedding vector for text (cached)."""
    k = _key(text)
    if k not in _cache:
        _cache[k] = _get_model().encode(text, normalize_embeddings=True)
    return _cache[k]


def batch_embeddings(texts: list[str]) -> list[np.ndarray]:
    """
    Encode a list of texts, re-using cached vectors where available.
    Returns a list of normalised numpy vectors in the same order.
    """
    model = _get_model()
    keys = [_key(t) for t in texts]
    missing_idx = [i for i, k in enumerate(keys) if k not in _cache]

    if missing_idx:
        missing_texts = [texts[i] for i in missing_idx]
        vecs = model.encode(missing_texts, normalize_embeddings=True, batch_size=64)
        for i, vec in zip(missing_idx, vecs):
            _cache[keys[i]] = vec

    return [_cache[k] for k in keys]


def semantic_similarity(text_a: str, text_b: str) -> float:
    """
    Cosine similarity between two texts in any language, 0.0–1.0.

    Cross-lingual: French ↔ English, German ↔ Italian, etc. all work because
    the model projects all languages into a shared embedding space.

    Edge cases:
      - Empty/very-short string → 0.0
      - Identical strings       → 1.0 (fast path, no encoding)
    """
    a = (text_a or "").strip()
    b = (text_b or "").strip()

    if not a or not b:
        return 0.0
    if a.lower() == b.lower():
        return 1.0
    if len(a) < 4 or len(b) < 4:
        return 1.0 if a.lower() == b.lower() else 0.0

    vec_a = get_embedding(a)
    vec_b = get_embedding(b)
    # Vectors are already L2-normalised → dot product = cosine similarity
    score = float(np.dot(vec_a, vec_b))
    return max(0.0, min(1.0, score))
