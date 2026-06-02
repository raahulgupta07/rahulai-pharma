"""Hybrid retrieval over dash.dash_vectors.

Combines:
- BM25 via Postgres `tsvector` + `ts_rank_cd`
- Vector cosine via `pgvector` `<=>` operator
- Reciprocal Rank Fusion (RRF) to merge result lists
- LLM multi-query expansion (modes: conservative / balanced / tokenmax)

Target table is `dash.dash_vectors` (chunk text in `text`, embedding in
`embedding`, tsvector in `tsv`). Source field maps to `source_id`.
"""
from __future__ import annotations

import logging
import math
import re
import time
from typing import Any

from sqlalchemy import text as _t

logger = logging.getLogger(__name__)

# Cache the usage map briefly so we don't hammer the MV per query.
_USAGE_CACHE: dict[str, Any] = {"data": None, "expires": 0.0}
_USAGE_TTL_S = 300.0  # 5 min


def _load_table_usage_map() -> dict[str, int]:
    """Return {table_fqn(lower) -> query_count_30d}. Fail-soft to {}."""
    now = time.time()
    if _USAGE_CACHE["data"] is not None and _USAGE_CACHE["expires"] > now:
        return _USAGE_CACHE["data"]
    out: dict[str, int] = {}
    try:
        with _engine().connect() as conn:
            rows = conn.execute(_t(
                "SELECT table_fqn, query_count_30d FROM public.mv_table_usage"
            )).fetchall()
            for r in rows:
                try:
                    out[str(r[0]).lower()] = int(r[1] or 0)
                except Exception:
                    continue
    except Exception as e:
        logger.debug("table_usage boost: MV unavailable: %s", e)
    _USAGE_CACHE["data"] = out
    _USAGE_CACHE["expires"] = now + _USAGE_TTL_S
    return out


def _resolve_fqn(item: dict, project: str) -> str | None:
    """Try to derive table_fqn from a result item. Returns None if none."""
    # Common shapes: source_id might already be "schema.table" or
    # "schema.table#chunk1" or contain a "table:foo" hint in text.
    src = (item.get("source") or item.get("source_id") or "")
    if isinstance(src, str) and src:
        s = src.split("#", 1)[0].split("?", 1)[0]
        if "." in s and "/" not in s and "\\" not in s and " " not in s:
            return s.lower()
    # Try to spot first "<word>.<word>" token in the content snippet.
    content = item.get("content") or item.get("text") or ""
    if isinstance(content, str) and project:
        m = re.search(r"\b([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\b", content.lower())
        if m:
            return m.group(1)
    return None


def _apply_table_usage_boost(
    items: list[dict],
    project: str,
    enabled: bool,
) -> list[dict]:
    """Multiply score by (1 + log(q30 + 1) * 0.15) when item resolves to a
    known table_fqn. Fail-soft — never mutates score on error.
    """
    if not enabled or not items:
        return items
    try:
        usage = _load_table_usage_map()
        if not usage:
            return items
        for it in items:
            fqn = _resolve_fqn(it, project)
            if not fqn:
                continue
            q30 = usage.get(fqn)
            if q30 is None:
                # Try schema-stripped fallback ("public.foo" → "foo")
                if "." in fqn:
                    alt = fqn.split(".", 1)[1]
                    for k in usage:
                        if k.endswith("." + alt):
                            q30 = usage[k]
                            break
            if q30 is None or q30 <= 0:
                continue
            try:
                factor = 1.0 + math.log(q30 + 1) * 0.15
                if "score" in it and isinstance(it["score"], (int, float)):
                    it["score"] = float(it["score"]) * factor
                    it.setdefault("debug", {})
                    if isinstance(it["debug"], dict):
                        it["debug"]["table_usage_boost"] = {
                            "fqn": fqn, "q30": q30, "factor": round(factor, 4),
                        }
            except Exception:
                continue
    except Exception as e:
        logger.debug("table_usage boost skipped: %s", e)
    return items


# ───────────────────── helpers ─────────────────────

def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _embed_query(query: str) -> list[float] | None:
    """Embed a single query string. Returns None on failure (caller falls back)."""
    try:
        from dash.tools.embeddings_helper import embed_batch  # type: ignore
    except Exception as e:
        logger.warning("retrieval: embeddings_helper unavailable: %s", e)
        return None
    try:
        out = embed_batch([query])
        vectors = out[0] if isinstance(out, tuple) else out
        if not vectors:
            return None
        return list(vectors[0])
    except Exception as e:
        logger.warning("retrieval: embed_batch failed: %s", e)
        return None


def _vec_to_pg(v: list[float]) -> str:
    try:
        from dash.tools.embeddings_helper import vec_to_pg  # type: ignore
        return vec_to_pg(v)
    except Exception:
        return "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"


# ───────────────────── BM25 ─────────────────────

def bm25_search(project: str, query: str, k: int = 20) -> list[dict]:
    """BM25-style lexical search using ts_rank_cd over `dash.dash_vectors.tsv`."""
    if not query or not query.strip():
        return []
    sql = """
        SELECT id, source_id, text,
               ts_rank_cd(tsv, plainto_tsquery('english', :q)) AS score
        FROM dash.dash_vectors
        WHERE project_slug = :p
          AND tsv @@ plainto_tsquery('english', :q)
        ORDER BY score DESC
        LIMIT :k
    """
    out: list[dict] = []
    try:
        with _engine().begin() as conn:
            rows = conn.execute(
                _t(sql), {"q": query, "p": project, "k": int(k)}
            ).mappings().all()
            for r in rows:
                out.append({
                    "chunk_id": int(r["id"]),
                    "score": float(r["score"] or 0.0),
                    "content": r["text"] or "",
                    "source": r["source_id"] or "",
                })
    except Exception as e:
        logger.warning("bm25_search failed: %s", e)
    return out


# ───────────────────── Vector ─────────────────────

def vector_search(
    project: str,
    query_embedding: list[float] | None,
    k: int = 20,
) -> list[dict]:
    """Cosine vector search via pgvector `<=>`. Returns [] if embedding missing."""
    if not query_embedding:
        return []
    qvec = _vec_to_pg(query_embedding)
    sql = """
        SELECT id, source_id, text,
               1 - (embedding <=> CAST(:qv AS vector)) AS score
        FROM dash.dash_vectors
        WHERE project_slug = :p
        ORDER BY embedding <=> CAST(:qv AS vector)
        LIMIT :k
    """
    out: list[dict] = []
    try:
        with _engine().begin() as conn:
            rows = conn.execute(
                _t(sql), {"qv": qvec, "p": project, "k": int(k)}
            ).mappings().all()
            for r in rows:
                out.append({
                    "chunk_id": int(r["id"]),
                    "score": float(r["score"]) if r["score"] is not None else 0.0,
                    "content": r["text"] or "",
                    "source": r["source_id"] or "",
                })
    except Exception as e:
        logger.warning("vector_search failed: %s", e)
    return out


# ───────────────────── Multi-query expansion ─────────────────────

def multi_query_expand(
    query: str,
    n: int = 3,
    mode: str = "balanced",
) -> list[str]:
    """Use the project LLM to rewrite the query in n different ways.

    Modes:
      conservative — skip expansion, return [query] only
      balanced     — 3 variants
      tokenmax     — 5 variants
    """
    q = (query or "").strip()
    if not q:
        return []
    if mode == "conservative":
        return [q]
    if mode == "tokenmax":
        n = 5
    else:
        n = 3

    prompt = (
        f"Rewrite this query {n} different ways to improve retrieval. "
        f"One per line, no numbering, no quotes, no explanations.\n"
        f"Original: {q}"
    )
    variants: list[str] = []
    try:
        from dash.settings import training_llm_call
        raw = training_llm_call(prompt, task="extraction")
        if raw:
            for line in str(raw).splitlines():
                line = line.strip().lstrip("-*0123456789.) ").strip().strip('"').strip("'")
                if line and line.lower() != q.lower() and line not in variants:
                    variants.append(line)
                if len(variants) >= n:
                    break
    except Exception as e:
        logger.warning("multi_query_expand: LLM call failed: %s", e)

    # Always include the original query first so we never lose it.
    return [q] + variants


# ───────────────────── RRF fusion ─────────────────────

def rrf_fuse(result_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion across N ranked lists.

    score(doc) = Σ_lists 1 / (k + rank_in_list)

    Each result must carry a `chunk_id`. Other fields (content/source) are
    preserved from whichever list contributed them first.
    """
    scores: dict[Any, float] = {}
    canonical: dict[Any, dict] = {}
    for lst in result_lists or []:
        for rank, item in enumerate(lst or []):
            cid = item.get("chunk_id")
            if cid is None:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            if cid not in canonical:
                canonical[cid] = dict(item)
    fused: list[dict] = []
    for cid, s in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        row = dict(canonical[cid])
        row["score"] = float(s)
        fused.append(row)
    return fused


# ───────────────────── main entry ─────────────────────

def hybrid_search(
    project: str,
    query: str,
    mode: str = "balanced",
    k: int = 10,
) -> list[dict]:
    """Run multi-query expansion → BM25+vector per query → RRF → top-k.

    Returns list of {chunk_id, content, score, source, debug}.
    Also writes a row to dash.dash_search_log.
    """
    started = time.time()
    queries = multi_query_expand(query, mode=mode)
    if not queries:
        queries = [query or ""]

    per_query_k = max(20, k * 2)
    all_lists: list[list[dict]] = []
    debug_queries: list[dict] = []

    for q in queries:
        bm = bm25_search(project, q, k=per_query_k)
        qvec = _embed_query(q)
        vec = vector_search(project, qvec, k=per_query_k)
        all_lists.append(bm)
        all_lists.append(vec)
        debug_queries.append({
            "query": q,
            "bm25_hits": len(bm),
            "vector_hits": len(vec),
        })

    fused = rrf_fuse(all_lists)

    # Popularity boost — gated by feature_config.tools.table_usage_boost.
    # Re-sort after boost. Fail-soft on any error.
    try:
        from dash.feature_config import is_enabled as _fc_enabled
        boost_on = _fc_enabled(project, "tools", "table_usage_boost")
    except Exception:
        boost_on = False
    if boost_on:
        fused = _apply_table_usage_boost(fused, project, True)
        fused.sort(key=lambda r: r.get("score", 0.0), reverse=True)

    top = fused[: int(k)]

    debug = {
        "mode": mode,
        "queries": debug_queries,
        "rrf_k": 60,
        "fuse_lists": len(all_lists),
    }
    for r in top:
        r["debug"] = debug

    latency_ms = int((time.time() - started) * 1000)
    try:
        with _engine().begin() as conn:
            conn.execute(
                _t(
                    "INSERT INTO dash.dash_search_log "
                    "(project_slug, query, mode, n_results, latency_ms) "
                    "VALUES (:p, :q, :m, :n, :l)"
                ),
                {
                    "p": project,
                    "q": (query or "")[:2000],
                    "m": mode,
                    "n": len(top),
                    "l": latency_ms,
                },
            )
    except Exception as e:
        logger.debug("dash_search_log insert skipped: %s", e)

    return top
