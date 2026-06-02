"""Embeddings helper — async batch embedding via OpenRouter with deterministic fallback.

Public surface:
    async embed_batch(texts, model=None) -> list[list[float]]
    async embed_text(text, model=None)   -> list[float]
    vec_to_pg(v: list[float]) -> str   # pgvector literal "[0.1,0.2,...]"
    text_hash(s: str) -> str           # sha256 hex
    EMBED_DIM (int)

Provider: OpenRouter (`OPENROUTER_API_KEY`), default model `text-embedding-3-small`
routed as `openai/text-embedding-3-small`. Output dim = 1536. Batches of 96.
Single 1s backoff retry on HTTP 429. Deterministic sha256 → 1536 normalized
floats fallback when provider unavailable (tests / offline dev).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import struct
from typing import Iterable, Optional

import httpx

logger = logging.getLogger(__name__)

# Public constants
EMBED_DIM = 1536
BATCH_SIZE = 96
DEFAULT_MODEL = "text-embedding-3-small"
OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"


def text_hash(s: str) -> str:
    """sha256 hex of text — used for content-dedupe (`text_hash` column)."""
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def vec_to_pg(v: Iterable[float]) -> str:
    """Format a vector as a pgvector literal: ``[0.1,0.2,...]``.

    Suitable for SQLAlchemy parameter binding to a ``vector(N)`` column when
    the bound parameter is a string literal (pgvector accepts the textual form).
    """
    items = list(v) if not isinstance(v, list) else v
    if not items:
        return "[]"
    return "[" + ",".join(f"{float(x):.7g}" for x in items) + "]"


def _provider_model_id(model: str) -> str:
    """Map short model name to OpenRouter provider-prefixed id."""
    if "/" in model:
        return model
    if model.startswith("text-embedding-"):
        return f"openai/{model}"
    return model


def _deterministic_pseudo_vector(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Stable, L2-normalized pseudo-vector from sha256 of text.

    Same input → same output, so cosine similarity stays consistent across
    test runs. Not semantically meaningful — only a fallback when no API.
    """
    seed = (text or "__empty__").encode("utf-8")
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        h = hashlib.sha256(seed + counter.to_bytes(4, "little")).digest()
        # 32 bytes → 8 float values via 4-byte unsigned ints mapped to [-1, 1).
        for i in range(0, 32, 4):
            (u,) = struct.unpack("<I", h[i : i + 4])
            out.append((u / 2**32) * 2.0 - 1.0)
            if len(out) >= dim:
                break
        counter += 1
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


async def _embed_chunk(
    client: httpx.AsyncClient,
    api_key: str,
    model_id: str,
    chunk: list[str],
) -> Optional[list[list[float]]]:
    """Embed one ≤BATCH_SIZE chunk via OpenRouter. Returns None on failure."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model_id, "input": chunk}

    for attempt in (0, 1):
        try:
            resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        except Exception as e:
            logger.warning("embed http error (attempt %d): %s", attempt, e)
            if attempt == 0:
                await asyncio.sleep(1.0)
                continue
            return None

        if resp.status_code == 429 and attempt == 0:
            await asyncio.sleep(1.0)
            continue
        if resp.status_code != 200:
            logger.warning("embed non-200 %s: %s", resp.status_code, resp.text[:200])
            return None

        try:
            data = resp.json()
            items = data.get("data") or []
            sorted_items = sorted(items, key=lambda x: x.get("index", 0))
            vectors: list[list[float]] = []
            for it in sorted_items:
                emb = it.get("embedding") or []
                if len(emb) > EMBED_DIM:
                    emb = emb[:EMBED_DIM]
                elif len(emb) < EMBED_DIM:
                    emb = list(emb) + [0.0] * (EMBED_DIM - len(emb))
                vectors.append([float(x) for x in emb])
            if len(vectors) != len(chunk):
                logger.warning(
                    "embed count mismatch: got %d for %d inputs",
                    len(vectors),
                    len(chunk),
                )
                return None
            return vectors
        except Exception as e:
            logger.warning("embed parse error: %s", e)
            return None
    return None


async def embed_batch(
    texts: list[str], model: Optional[str] = None
) -> list[list[float]]:
    """Embed a list of texts. Returns 1536-dim float vectors, one per input.

    - Splits into chunks of 96.
    - Uses OpenRouter when ``OPENROUTER_API_KEY`` is set.
    - Falls back to deterministic pseudo-vectors per-chunk on failure or when
      no API key is configured.
    """
    if not texts:
        return []
    model_name = model or DEFAULT_MODEL
    provider_model = _provider_model_id(model_name)
    api_key = os.getenv("OPENROUTER_API_KEY", "")

    if not api_key:
        return [_deterministic_pseudo_vector(t) for t in texts]

    out: list[Optional[list[float]]] = [None] * len(texts)
    async with httpx.AsyncClient(timeout=30.0) as client:
        for start in range(0, len(texts), BATCH_SIZE):
            chunk = texts[start : start + BATCH_SIZE]
            vectors = await _embed_chunk(client, api_key, provider_model, chunk)
            if vectors is None:
                vectors = [_deterministic_pseudo_vector(t) for t in chunk]
            for i, v in enumerate(vectors):
                out[start + i] = v

    # Safety: any leftover None → deterministic fallback.
    return [
        v if v is not None else _deterministic_pseudo_vector(texts[i])
        for i, v in enumerate(out)
    ]


async def embed_text(text: str, model: Optional[str] = None) -> list[float]:
    """Single-text wrapper around :func:`embed_batch`."""
    vecs = await embed_batch([text or ""], model=model)
    return vecs[0] if vecs else _deterministic_pseudo_vector(text or "")


__all__ = [
    "EMBED_DIM",
    "BATCH_SIZE",
    "DEFAULT_MODEL",
    "embed_batch",
    "embed_text",
    "vec_to_pg",
    "text_hash",
]
