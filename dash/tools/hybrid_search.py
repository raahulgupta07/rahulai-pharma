"""Hybrid search over ``dash_vectors`` — pgvector cosine + Postgres BM25 (tsvector).

Combines the two ranked lists with Reciprocal Rank Fusion (RRF):

    score = alpha / (60 + vec_rank) + (1 - alpha) / (60 + bm_rank)

Both queries respect Postgres RLS via session GUCs ``app.project_slug`` and
``app.user_attrs`` (JSON), set on the same connection that runs them.

The ``dash_vectors`` table is expected to have at least::

    project_slug TEXT
    namespace    TEXT
    source_id    TEXT
    text         TEXT
    embedding    vector(1536)
    tsv          tsvector       -- generated from `text`
    scope_attrs  JSONB DEFAULT '{}'::jsonb
    metadata     JSONB DEFAULT '{}'::jsonb
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import text

from dash.tools.embeddings_helper import embed_text, vec_to_pg

_RRF_K = 60  # standard RRF smoothing constant


def _get_sql_engine():
    """Lazy import — keeps module importable without the full app on path."""
    from db.session import get_sql_engine as _g  # type: ignore

    return _g()


def _set_session_vars(conn, slug: str, user_attrs: dict | None) -> None:
    """SET LOCAL the project_slug + user_attrs GUCs used by RLS policies."""
    conn.execute(text("SELECT set_config('app.project_slug', :v, true)"), {"v": slug})
    conn.execute(
        text("SELECT set_config('app.user_attrs', :v, true)"),
        {"v": json.dumps(user_attrs or {})},
    )


_VEC_SQL = text(
    """
    SELECT source_id, text, namespace, scope_attrs, metadata,
           ROW_NUMBER() OVER () AS rank
    FROM dash_vectors
    WHERE project_slug = :p
      AND (CAST(:ns_is_null AS BOOLEAN) OR namespace = ANY(CAST(:ns AS TEXT[])))
    ORDER BY embedding <=> CAST(:q AS vector)
    LIMIT 50
    """
)

_BM_SQL = text(
    """
    SELECT source_id, text, namespace, scope_attrs, metadata,
           ts_rank(tsv, plainto_tsquery('english', :q)) AS s,
           ROW_NUMBER() OVER (
               ORDER BY ts_rank(tsv, plainto_tsquery('english', :q)) DESC
           ) AS rank
    FROM dash_vectors
    WHERE project_slug = :p
      AND tsv @@ plainto_tsquery('english', :q)
      AND (CAST(:ns_is_null AS BOOLEAN) OR namespace = ANY(CAST(:ns AS TEXT[])))
    ORDER BY s DESC
    LIMIT 50
    """
)


def _run_vec(slug: str, q_vec_lit: str, namespaces: list[str] | None,
             user_attrs: dict | None) -> list[dict]:
    eng = _get_sql_engine()
    with eng.connect() as conn:
        with conn.begin():
            _set_session_vars(conn, slug, user_attrs)
            rows = conn.execute(
                _VEC_SQL,
                {
                    "p": slug,
                    "q": q_vec_lit,
                    "ns": namespaces,
                    "ns_is_null": namespaces is None,
                },
            ).mappings().all()
    return [dict(r) for r in rows]


def _run_bm(slug: str, query: str, namespaces: list[str] | None,
            user_attrs: dict | None) -> list[dict]:
    eng = _get_sql_engine()
    with eng.connect() as conn:
        with conn.begin():
            _set_session_vars(conn, slug, user_attrs)
            rows = conn.execute(
                _BM_SQL,
                {
                    "p": slug,
                    "q": query,
                    "ns": namespaces,
                    "ns_is_null": namespaces is None,
                },
            ).mappings().all()
    return [dict(r) for r in rows]


async def hybrid_search(
    slug: str,
    query: str,
    k: int = 10,
    alpha: float = 0.5,
    namespaces: list[str] | None = None,
    user_attrs: dict | None = None,
) -> list[dict]:
    """Reciprocal Rank Fusion of pgvector cosine + Postgres BM25 (tsvector).

    Args:
        slug: Project slug. Used both for the WHERE filter and for the
            ``app.project_slug`` GUC consumed by RLS policies.
        query: Natural-language query string.
        k: Maximum results to return (after fusion).
        alpha: Weight on the vector half of RRF; 0.5 = even split.
        namespaces: Optional list of namespaces to restrict to. ``None``
            means "all namespaces".
        user_attrs: Per-request user attributes (e.g. ``{"store_id": 1}``)
            propagated into ``app.user_attrs`` for RLS.

    Returns:
        List of dicts with keys: ``source_id``, ``text``, ``score_fused``,
        ``vec_rank``, ``bm_rank``, ``namespace``, ``scope_attrs``, ``metadata``.
        Sorted by ``score_fused`` descending.
    """
    if not query or not query.strip():
        return []

    # Embed query (sync — fast, 1 LLM call or hash fallback) before forking.
    q_vec_lit = vec_to_pg(embed_text(query))

    # Run the two queries in parallel threads; each opens its own connection.
    vec_task = asyncio.to_thread(_run_vec, slug, q_vec_lit, namespaces, user_attrs)
    bm_task = asyncio.to_thread(_run_bm, slug, query, namespaces, user_attrs)
    vec_rows, bm_rows = await asyncio.gather(vec_task, bm_task)

    # Fuse by source_id.
    fused: dict[str, dict[str, Any]] = {}

    def _key(r: dict) -> str:
        return f"{r.get('namespace') or ''}::{r['source_id']}"

    for r in vec_rows:
        sid = _key(r)
        fused[sid] = {
            "source_id": r["source_id"],
            "text": r["text"],
            "namespace": r.get("namespace"),
            "scope_attrs": r.get("scope_attrs") or {},
            "metadata": r.get("metadata") or {},
            "vec_rank": int(r["rank"]),
            "bm_rank": None,
        }
    for r in bm_rows:
        sid = _key(r)
        if sid in fused:
            fused[sid]["bm_rank"] = int(r["rank"])
        else:
            fused[sid] = {
                "source_id": r["source_id"],
                "text": r["text"],
                "namespace": r.get("namespace"),
                "scope_attrs": r.get("scope_attrs") or {},
                "metadata": r.get("metadata") or {},
                "vec_rank": None,
                "bm_rank": int(r["rank"]),
            }

    out: list[dict] = []
    for sid, hit in fused.items():
        vr = hit["vec_rank"]
        br = hit["bm_rank"]
        score = 0.0
        if vr is not None:
            score += alpha / (_RRF_K + vr)
        if br is not None:
            score += (1.0 - alpha) / (_RRF_K + br)
        hit["score_fused"] = score
        out.append(hit)

    out.sort(key=lambda h: h["score_fused"], reverse=True)
    return out[:k]


__all__ = ["hybrid_search"]
