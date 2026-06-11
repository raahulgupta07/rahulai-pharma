"""Repeated-common-question answer cache — semantic serve, zero LLM. [P1]

Stores the FULL rendered answer (AnswerCard tag string) for a question. An
incoming question is embedded and matched by cosine nearest-neighbor against
the cached questions (dash.dash_vectors, namespace='qcache'); on a close enough
match AND a fresh source-table schema, the saved answer is served verbatim with
no model call.

- Store:   dash.dash_answer_cache (migration 185)
- Vectors: dash.dash_vectors namespace='qcache', source_id = cache row id
- Freshness: schema_hash stamped at promote (dash/learning/schema_guard), the
  same drift guard as the P0 metric_shortcut gate. Row-count changes do NOT
  invalidate (the answer is the rendered card, not a live re-run — but the
  numbers inside were correct at promote and a *schema* move is what makes them
  untrustworthy; a future P-phase can add a row-fingerprint TTL for volatile
  aggregates, today the Curator only promotes stable ones).

Fail-soft everywhere: any error → None / no-op, never raises, never blocks chat.
Async (embedding is async); call sites await it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

_NAMESPACE = "qcache"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip().rstrip(".?!"))


def _vec_literal(emb: list[float]) -> str:
    """pgvector text literal: '[0.1,0.2,...]' for CAST(:v AS vector)."""
    return "[" + ",".join(f"{float(x):.6f}" for x in emb) + "]"


def _min_sim() -> float:
    try:
        return float(os.getenv("ANSWER_CACHE_MIN_SIM", "0.93"))
    except Exception:
        return 0.93


def _has_any(slug: str) -> bool:
    """Cheap existence check — avoid embedding on projects with an empty cache."""
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            r = conn.execute(_text(
                "SELECT 1 FROM dash.dash_answer_cache "
                "WHERE project_slug = :s AND status = 'live' LIMIT 1"
            ), {"s": slug}).fetchone()
        return bool(r)
    except Exception:
        return False


async def try_answer_cache(project_slug: str, question: str) -> dict | None:
    """Serve a cached full answer for `question`, or None.

    Steps: existence check → embed → cosine NN in qcache → similarity gate →
    load row → schema-drift gate → return stored content (hit_count++).
    """
    try:
        norm = _norm(question)
        if not norm or not _has_any(project_slug):
            return None
        t0 = time.time()
        from dash.tools.embeddings_helper import embed_text
        emb = await embed_text(question)
        vec = _vec_literal(emb)

        from sqlalchemy import text as _text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            nn = conn.execute(_text(
                "SELECT source_id, 1 - (embedding <=> CAST(:v AS vector)) AS sim "
                "FROM dash.dash_vectors "
                "WHERE project_slug = :s AND namespace = :ns "
                "ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1"
            ), {"v": vec, "s": project_slug, "ns": _NAMESPACE}).fetchone()
            if not nn:
                return None
            sim = float(nn[1] or 0.0)
            if sim < _min_sim():
                logger.debug("answer_cache miss: best sim=%.3f < %.3f", sim, _min_sim())
                return None
            cid = int(nn[0])
            row = conn.execute(_text(
                "SELECT question, answer_payload, source_tables, schema_hash "
                "FROM dash.dash_answer_cache "
                "WHERE id = :id AND project_slug = :s AND status = 'live'"
            ), {"id": cid, "s": project_slug}).fetchone()
        if not row:
            return None
        _q, payload, source_tables, schema_hash = row[0], row[1], row[2], row[3]
        content = (payload or {}).get("content") if isinstance(payload, dict) else None
        if not content:
            return None

        # Schema-drift gate (same as P0). On drift → mark stale + miss.
        if schema_hash and os.getenv("ANSWER_CACHE_SCHEMA_GUARD", "1") != "0":
            try:
                from dash.learning.schema_guard import live_schema_hash
                live = live_schema_hash(project_slug, list(source_tables or []))
                if live and live != schema_hash:
                    logger.info("answer_cache stale (schema drift) id=%d %s — falling to agent",
                                cid, project_slug)
                    _mark_stale(cid)
                    return None
            except Exception:
                pass  # fail-open

        _bump_hit(cid)
        return {
            "content": content,
            "similarity": sim,
            "id": cid,
            "matched_q": _q,
            "sql": (payload or {}).get("sql"),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("try_answer_cache failed for %s: %s", project_slug, exc)
        return None


async def promote_answer(
    project_slug: str,
    *,
    question: str,
    content: str,
    canonical_sql: str | None = None,
    source_tables: list[str] | None = None,
    confidence: float = 1.0,
    promoted_by: str = "admin",
) -> dict:
    """Pin a question → full rendered answer into the cache (+ embed the question).

    Idempotent on (project_slug, question_norm). Fail-soft.
    """
    try:
        question = (question or "").strip()
        content = (content or "").strip()
        if not question or not content:
            return {"ok": False, "error": "question and content required"}
        norm = _norm(question)

        from dash.learning.schema_guard import sql_source_tables, live_schema_hash
        if source_tables is None and canonical_sql:
            source_tables = sql_source_tables(canonical_sql)
        source_tables = [t for t in (source_tables or []) if t]
        schema_hash = live_schema_hash(project_slug, source_tables) if source_tables else ""

        import json as _json
        payload = _json.dumps({"content": content, "sql": canonical_sql})

        from sqlalchemy import text as _text
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.begin() as conn:
            cid = conn.execute(_text(
                "INSERT INTO dash.dash_answer_cache "
                "(project_slug, question, question_norm, canonical_sql, answer_payload, "
                " source_tables, schema_hash, confidence, promoted_by) "
                "VALUES (:s, :q, :n, :sql, CAST(:p AS jsonb), :st, :sh, :c, :by) "
                "ON CONFLICT (project_slug, question_norm) DO UPDATE SET "
                "  question = EXCLUDED.question, canonical_sql = EXCLUDED.canonical_sql, "
                "  answer_payload = EXCLUDED.answer_payload, source_tables = EXCLUDED.source_tables, "
                "  schema_hash = EXCLUDED.schema_hash, confidence = EXCLUDED.confidence, "
                "  promoted_by = EXCLUDED.promoted_by, status = 'live' "
                "RETURNING id"
            ), {"s": project_slug, "q": question, "n": norm, "sql": canonical_sql,
                "p": payload, "st": source_tables, "sh": schema_hash or None,
                "c": confidence, "by": promoted_by}).scalar()

        from dash.tools.embeddings_helper import embed_text
        emb = await embed_text(question)
        vec = _vec_literal(emb)
        qhash = hashlib.sha256(question.encode()).hexdigest()
        with eng.begin() as conn:
            conn.execute(_text(
                "INSERT INTO dash.dash_vectors "
                "(project_slug, namespace, source_id, text, text_hash, embedding, metadata) "
                "VALUES (:s, :ns, :sid, :t, :h, CAST(:v AS vector), '{}'::jsonb) "
                "ON CONFLICT (project_slug, namespace, source_id) DO UPDATE SET "
                "  text = EXCLUDED.text, text_hash = EXCLUDED.text_hash, "
                "  embedding = EXCLUDED.embedding, updated_at = now()"
            ), {"s": project_slug, "ns": _NAMESPACE, "sid": str(cid),
                "t": question, "h": qhash, "v": vec})
        return {"ok": True, "id": int(cid), "schema_hash": schema_hash}
    except Exception as exc:  # noqa: BLE001
        logger.warning("promote_answer failed for %s: %s", project_slug, exc)
        return {"ok": False, "error": str(exc)}


def demote_answer(project_slug: str, cache_id: int) -> dict:
    """Soft-delete a cache row + drop its vector. Fail-soft."""
    try:
        from sqlalchemy import text as _text
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.begin() as conn:
            conn.execute(_text(
                "UPDATE dash.dash_answer_cache SET status = 'demoted' "
                "WHERE id = :id AND project_slug = :s"
            ), {"id": cache_id, "s": project_slug})
            conn.execute(_text(
                "DELETE FROM dash.dash_vectors "
                "WHERE project_slug = :s AND namespace = :ns AND source_id = :sid"
            ), {"s": project_slug, "ns": _NAMESPACE, "sid": str(cache_id)})
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _bump_hit(cache_id: int) -> None:
    try:
        from sqlalchemy import text as _text
        from db.session import get_write_engine
        with get_write_engine().begin() as conn:
            conn.execute(_text(
                "UPDATE dash.dash_answer_cache "
                "SET hit_count = hit_count + 1, last_served_at = now() WHERE id = :id"
            ), {"id": cache_id})
    except Exception:
        pass


def _mark_stale(cache_id: int) -> None:
    try:
        from sqlalchemy import text as _text
        from db.session import get_write_engine
        with get_write_engine().begin() as conn:
            conn.execute(_text(
                "UPDATE dash.dash_answer_cache SET status = 'stale' WHERE id = :id"
            ), {"id": cache_id})
    except Exception:
        pass
