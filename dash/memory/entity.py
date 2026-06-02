"""Per-entity memory: customer / SKU / employee / vendor / campaign.

Distinct from project-wide memory (dash_memories) — these facts are
scoped to a specific entity_id and surface in Customer 360, SKU drill,
campaign cards, etc.

Optional pgvector embedding for semantic recall. Falls back to keyword
match when embedding unavailable.

Behind EXPERIMENTAL_AGI=1: full recall + auto-promote.
Off: CRUD works but auto-extraction from chats disabled.
"""
from __future__ import annotations

import json as _json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


def _get_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _embed(text: str) -> Optional[List[float]]:
    """Best-effort embedding. Returns None on failure (recall falls back to keyword)."""
    try:
        from dash.tools.embeddings_helper import embed_text  # type: ignore
        v = embed_text(text)
        if isinstance(v, list) and len(v) == 1536:
            return v
    except Exception:
        pass
    return None


def remember(
    entity_type: str, entity_id: str, fact: str,
    project_slug: Optional[str] = None, fact_kind: str = "observation",
    confidence: float = 0.7, source: str = "agent",
    source_run_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None, dedupe: bool = True,
) -> Dict[str, Any]:
    """Save a fact about an entity. Returns {ok, id, deduped}."""
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "db_unavailable"}

    # Dedup: same project + entity + fact text within last 30 days = skip
    if dedupe:
        try:
            from sqlalchemy import text
            with eng.connect() as conn:
                existing = conn.execute(
                    text(
                        """
                        SELECT id FROM dash.dash_entity_memory
                        WHERE entity_type=:et AND entity_id=:eid
                          AND project_slug IS NOT DISTINCT FROM :ps
                          AND fact = :fact AND archived=false
                          AND created_at > now() - INTERVAL '30 days'
                        LIMIT 1
                        """
                    ),
                    {"et": entity_type, "eid": entity_id, "ps": project_slug, "fact": fact},
                ).first()
            if existing:
                return {"ok": True, "id": existing[0], "deduped": True}
        except Exception:
            pass

    embedding = _embed(fact) if _enabled() else None
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_entity_memory
                      (project_slug, entity_type, entity_id, fact, fact_kind,
                       confidence, source, source_run_id, embedding, metadata, created_by)
                    VALUES (:ps, :et, :eid, :f, :fk, :c, :s, :srid,
                            CAST(:emb AS vector), CAST(:md AS jsonb), :cb)
                    RETURNING id
                    """
                ),
                {
                    "ps": project_slug, "et": entity_type, "eid": entity_id,
                    "f": fact, "fk": fact_kind, "c": confidence,
                    "s": source, "srid": source_run_id,
                    "emb": str(embedding) if embedding else None,
                    "md": _json.dumps(metadata or {}), "cb": user_id,
                },
            )
            new_id = r.scalar()
        return {"ok": True, "id": new_id, "deduped": False}
    except Exception as e:
        # if vector extension or column missing, retry without embedding
        try:
            from sqlalchemy import text
            with eng.begin() as conn:
                r = conn.execute(
                    text(
                        """
                        INSERT INTO dash.dash_entity_memory
                          (project_slug, entity_type, entity_id, fact, fact_kind,
                           confidence, source, source_run_id, metadata, created_by)
                        VALUES (:ps, :et, :eid, :f, :fk, :c, :s, :srid,
                                CAST(:md AS jsonb), :cb)
                        RETURNING id
                        """
                    ),
                    {
                        "ps": project_slug, "et": entity_type, "eid": entity_id,
                        "f": fact, "fk": fact_kind, "c": confidence,
                        "s": source, "srid": source_run_id,
                        "md": _json.dumps(metadata or {}), "cb": user_id,
                    },
                )
                return {"ok": True, "id": r.scalar(), "deduped": False, "no_embedding": True}
        except Exception as e2:
            logger.warning("remember failed: %s / %s", e, e2)
            return {"ok": False, "error": str(e2)}


def recall(
    entity_type: str, entity_id: str,
    project_slug: Optional[str] = None, fact_kind: Optional[str] = None,
    limit: int = 50, since_days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return []
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, fact, fact_kind, confidence, source,
                           source_run_id, metadata, created_at
                    FROM dash.dash_entity_memory
                    WHERE entity_type=:et AND entity_id=:eid
                      AND project_slug IS NOT DISTINCT FROM :ps
                      AND archived=false
                      AND (:fk IS NULL OR fact_kind = :fk)
                      AND (:days IS NULL OR created_at > now() - (:days || ' days')::interval)
                    ORDER BY confidence DESC, created_at DESC
                    LIMIT :lim
                    """
                ),
                {
                    "et": entity_type, "eid": entity_id, "ps": project_slug,
                    "fk": fact_kind, "days": since_days, "lim": limit,
                },
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("recall failed: %s", e)
        return []


def semantic_recall(
    entity_type: str, query: str,
    project_slug: Optional[str] = None, top_k: int = 10,
) -> List[Dict[str, Any]]:
    """pgvector cosine similarity. Falls back to ILIKE keyword search."""
    eng = _get_engine()
    if eng is None:
        return []
    q_emb = _embed(query)
    try:
        from sqlalchemy import text
        if q_emb is not None:
            with eng.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, entity_id, fact, fact_kind, confidence, created_at,
                               1 - (embedding <=> CAST(:qe AS vector)) AS similarity
                        FROM dash.dash_entity_memory
                        WHERE entity_type=:et
                          AND project_slug IS NOT DISTINCT FROM :ps
                          AND archived=false AND embedding IS NOT NULL
                        ORDER BY embedding <=> CAST(:qe AS vector)
                        LIMIT :k
                        """
                    ),
                    {"et": entity_type, "ps": project_slug, "qe": str(q_emb), "k": top_k},
                ).mappings().all()
            return [dict(r) for r in rows]
        # keyword fallback
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, entity_id, fact, fact_kind, confidence, created_at
                    FROM dash.dash_entity_memory
                    WHERE entity_type=:et
                      AND project_slug IS NOT DISTINCT FROM :ps
                      AND archived=false
                      AND fact ILIKE :q
                    ORDER BY confidence DESC, created_at DESC
                    LIMIT :k
                    """
                ),
                {"et": entity_type, "ps": project_slug, "q": f"%{query}%", "k": top_k},
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("semantic_recall failed: %s", e)
        return []


def forget(memory_id: int) -> bool:
    eng = _get_engine()
    if eng is None:
        return False
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text("UPDATE dash.dash_entity_memory SET archived=true WHERE id=:id"),
                {"id": memory_id},
            )
        return r.rowcount > 0
    except Exception as e:
        logger.warning("forget failed: %s", e)
        return False


def promote_to_project(memory_id: int) -> Dict[str, Any]:
    """Copy entity fact into project-wide dash_memories. Best-effort."""
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "db_unavailable"}
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT project_slug, entity_type, entity_id, fact, confidence "
                    "FROM dash.dash_entity_memory WHERE id=:id"
                ),
                {"id": memory_id},
            ).mappings().first()
            if not row:
                return {"ok": False, "error": "not_found"}
            full_fact = f"{row['entity_type']}({row['entity_id']}): {row['fact']}"
            try:
                conn.execute(
                    text(
                        """
                        INSERT INTO dash.dash_memories
                          (project_slug, content, source, confidence)
                        VALUES (:ps, :c, 'entity_promoted', :conf)
                        """
                    ),
                    {"ps": row["project_slug"], "c": full_fact, "conf": row["confidence"]},
                )
            except Exception:
                # dash_memories may have different schema; try alt
                conn.execute(
                    text(
                        "INSERT INTO public.dash_memories (project_slug, content, source) "
                        "VALUES (:ps, :c, 'entity_promoted')"
                    ),
                    {"ps": row["project_slug"], "c": full_fact},
                )
            return {"ok": True, "promoted": full_fact[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Agno @tool wrappers ─────────────────────────────────────────────────
def _try_tool(fn):
    try:
        from agno.tools import tool
        return tool(fn)
    except Exception:
        return fn


def _ctx() -> Dict[str, Any]:
    try:
        from dash.agentic.hooks import (
            current_project_slug, current_user_id, current_run_id,
        )
        return {
            "project_slug": current_project_slug.get(),
            "user_id": current_user_id.get(),
            "run_id": current_run_id.get(),
        }
    except Exception:
        return {"project_slug": None, "user_id": None, "run_id": None}


@_try_tool
def remember_entity_fact(
    entity_type: str, entity_id: str, fact: str,
    fact_kind: str = "observation", confidence: float = 0.7,
) -> Dict[str, Any]:
    """Save a fact about a specific entity (customer/SKU/employee/vendor)."""
    ctx = _ctx()
    return remember(
        entity_type, entity_id, fact,
        project_slug=ctx["project_slug"], fact_kind=fact_kind,
        confidence=confidence, source="agent",
        source_run_id=ctx["run_id"], user_id=ctx["user_id"],
    )


@_try_tool
def recall_entity_facts(
    entity_type: str, entity_id: str, fact_kind: Optional[str] = None, limit: int = 20,
) -> Dict[str, Any]:
    """Fetch saved facts about an entity."""
    ctx = _ctx()
    facts = recall(entity_type, entity_id, ctx["project_slug"], fact_kind, limit)
    return {"ok": True, "facts": facts, "count": len(facts)}
