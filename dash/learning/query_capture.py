"""Continuous query learning — P1 capture + shadow.

The agent writes SQL on every analytical chat turn; today that SQL is ephemeral.
This module RECORDS it (question -> SQL) into public.dash_query_patterns
(source='chat', status='pending') so the system learns from real questions, and
runs a SHADOW match that logs "this live question WOULD have hit bank row X" —
WITHOUT serving anything. Lets us measure repeat-rate before flipping serve on.

Plan: docs/plans/continuous_query_learning.md. All functions fail-soft; nothing
here is allowed to affect the user-facing reply path.

Gated by env:
  QUERY_CAPTURE_DISABLED=1   -> capture off
  QUERY_SHADOW_DISABLED=1    -> shadow match off
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time

logger = logging.getLogger("dash.query_capture")

_NAMESPACE = "qbank"
# Serve threshold the shadow uses to decide "would_serve" (Mode-1 bypass would use
# this someday). Kept in sync with answer_cache's default; tunable via env.
_SERVE_SIM = float(os.getenv("QUERY_BANK_SERVE_SIM", "0.93"))  # tuned (Q4)
_SHADOW_SIM_FLOOR = float(os.getenv("QUERY_BANK_SHADOW_FLOOR", "0.80"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _vec_literal(emb: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in emb) + "]"


def _capture_disabled() -> bool:
    return os.getenv("QUERY_CAPTURE_DISABLED", "0") in ("1", "true", "True", "yes")


def _shadow_disabled() -> bool:
    return os.getenv("QUERY_SHADOW_DISABLED", "0") in ("1", "true", "True", "yes")


def _row_count_of(result) -> int | None:
    """Best-effort count of rows in a run_sql_query result (string/JSON/list)."""
    try:
        if result is None:
            return None
        if isinstance(result, list):
            return len(result)
        if isinstance(result, str):
            s = result.strip()
            if s.startswith("[") or s.startswith("{"):
                import json as _json
                obj = _json.loads(s)
                if isinstance(obj, list):
                    return len(obj)
                if isinstance(obj, dict):
                    for k in ("rows", "data", "result", "results"):
                        v = obj.get(k)
                        if isinstance(v, list):
                            return len(v)
                    if obj.get("ok") is False:
                        return None
                    return 1
            # plain text answer — unknown row shape
            return None
    except Exception:
        return None
    return None


# ── Capture (fire-and-forget) ────────────────────────────────────────────────

def capture_query_async(project_slug: str, question: str, sql: str,
                        result=None, latency_ms: int | None = None) -> None:
    """Spawn a daemon thread to record a successful (question, SQL). Never blocks
    the reply, never raises."""
    if _capture_disabled():
        return
    q = (question or "").strip()
    s = (sql or "").strip()
    if not project_slug or not q or not s:
        return
    # Skip trivial / non-learnable SQL.
    if len(s) < 12 or not re.search(r"\bselect\b", s, re.IGNORECASE):
        return
    rows = _row_count_of(result)
    # Errored / no-data runs are not learning material.
    if rows is None:
        # Could be a plain-text answer (no rows) OR a failure. Only skip clear
        # failures; ambiguous text answers are skipped too (no SQL value).
        try:
            if isinstance(result, str) and '"ok": false' in result.lower():
                return
        except Exception:
            pass

    def _work():
        try:
            _do_capture(project_slug, q, s, rows, latency_ms)
        except Exception as exc:  # noqa: BLE001
            logger.debug("capture_query failed for %s: %s", project_slug, exc)

    try:
        threading.Thread(target=_work, name="qbank-capture", daemon=True).start()
    except Exception:
        pass


def _embed_sync(text: str) -> list[float] | None:
    """Run the async embed_text on a private loop (we're in a worker thread)."""
    try:
        import asyncio
        from dash.tools.embeddings_helper import embed_text
        return asyncio.run(embed_text(text))
    except Exception as exc:  # noqa: BLE001
        logger.debug("embed failed: %s", exc)
        return None


def _do_capture(slug: str, question: str, sql: str,
                rows: int | None, latency_ms: int | None) -> None:
    from sqlalchemy import text as _text
    from db.session import get_write_engine

    norm = _norm(question)
    # Schema hash of the SQL's source tables (drift guard at serve time later).
    try:
        from dash.learning.schema_guard import sql_source_tables, live_schema_hash
        tables = sql_source_tables(sql)
        schema_hash = live_schema_hash(slug, tables) if tables else None
        tables_used = ",".join(tables) if tables else None
    except Exception:
        tables, schema_hash, tables_used = [], None, None

    eng = get_write_engine()
    with eng.begin() as conn:
        pid = conn.execute(_text(
            "INSERT INTO public.dash_query_patterns "
            "  (project_slug, question, question_norm, sql, source, status, "
            "   schema_hash, rows_returned, last_latency_ms, success, tables_used, "
            "   uses, last_used, created_at) "
            "VALUES (:s, :q, :n, :sql, 'chat', 'pending', :sh, :rc, :lat, TRUE, :tu, "
            "        1, now(), now()) "
            "ON CONFLICT (project_slug, question_norm) WHERE question_norm IS NOT NULL "
            "DO UPDATE SET uses = public.dash_query_patterns.uses + 1, "
            "  last_used = now(), sql = EXCLUDED.sql, schema_hash = EXCLUDED.schema_hash, "
            "  rows_returned = EXCLUDED.rows_returned, last_latency_ms = EXCLUDED.last_latency_ms, "
            "  success = TRUE "
            "RETURNING id"
        ), {"s": slug, "q": question, "n": norm, "sql": sql, "sh": schema_hash,
            "rc": rows, "lat": latency_ms, "tu": tables_used}).scalar()

    if pid is None:
        return
    # Embed the question into the qbank vector namespace (reuse dash.dash_vectors).
    emb = _embed_sync(question)
    if not emb:
        return
    vec = _vec_literal(emb)
    qhash = hashlib.sha256(question.encode()).hexdigest()
    with eng.begin() as conn:
        conn.execute(_text(
            "INSERT INTO dash.dash_vectors "
            "  (project_slug, namespace, source_id, text, text_hash, embedding, metadata) "
            "VALUES (:s, :ns, :sid, :t, :h, CAST(:v AS vector), '{}'::jsonb) "
            "ON CONFLICT (project_slug, namespace, source_id) DO UPDATE SET "
            "  text = EXCLUDED.text, text_hash = EXCLUDED.text_hash, "
            "  embedding = EXCLUDED.embedding, updated_at = now()"
        ), {"s": slug, "ns": _NAMESPACE, "sid": str(pid),
            "t": question, "h": qhash, "v": vec})
    logger.debug("captured query pattern id=%s slug=%s rows=%s", pid, slug, rows)


# ── Shadow match (measurement only — NEVER serves) ───────────────────────────

async def shadow_match(project_slug: str, question: str) -> None:
    """Embed the live question, find the nearest bank row, log whether it WOULD
    have hit. Serves nothing. Fail-soft. Call after shortcuts miss, before agent."""
    if _shadow_disabled():
        return
    q = (question or "").strip()
    if not project_slug or len(q) < 4:
        return
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine, get_write_engine

        # Cheap existence check — skip embedding when the bank is empty.
        eng = get_sql_engine()
        with eng.connect() as conn:
            has = conn.execute(_text(
                "SELECT 1 FROM dash.dash_vectors "
                "WHERE project_slug = :s AND namespace = :ns LIMIT 1"
            ), {"s": project_slug, "ns": _NAMESPACE}).first()
        if not has:
            return

        from dash.tools.embeddings_helper import embed_text
        emb = await embed_text(q)
        if not emb:
            return
        vec = _vec_literal(emb)
        with eng.connect() as conn:
            row = conn.execute(_text(
                "SELECT v.source_id, 1 - (v.embedding <=> CAST(:v AS vector)) AS sim, "
                "       p.status, p.schema_hash, p.tables_used "
                "FROM dash.dash_vectors v "
                "JOIN public.dash_query_patterns p ON p.id = v.source_id::int "
                "WHERE v.project_slug = :s AND v.namespace = :ns "
                "ORDER BY v.embedding <=> CAST(:v AS vector) LIMIT 1"
            ), {"s": project_slug, "ns": _NAMESPACE, "v": vec}).first()

        matched_id = sim = matched_status = schema_ok = would_serve = None
        if row:
            matched_id = int(row[0]) if row[0] is not None else None
            sim = float(row[1]) if row[1] is not None else None
            matched_status = row[2]
            # Schema-drift check (would the stored SQL still be valid?).
            try:
                if row[3]:  # has a stored schema_hash
                    from dash.learning.schema_guard import live_schema_hash
                    tabs = [t for t in (row[4] or "").split(",") if t]
                    schema_ok = bool(tabs) and (live_schema_hash(project_slug, tabs) == row[3])
                else:
                    schema_ok = None
            except Exception:
                schema_ok = None
            would_serve = bool(
                sim is not None and sim >= _SERVE_SIM
                and matched_status == "proven"
                and (schema_ok is not False)
            )

        # Only log meaningful near-matches (avoid noise from far-away nearest rows).
        if sim is None or sim < _SHADOW_SIM_FLOOR:
            matched_id = None  # record a miss row (still useful: "no near match")

        weng = get_write_engine()
        with weng.begin() as conn:
            conn.execute(_text(
                "INSERT INTO public.dash_query_bank_shadow "
                "  (project_slug, question, matched_id, sim, matched_status, "
                "   would_serve, schema_ok) "
                "VALUES (:s, :q, :mid, :sim, :ms, :ws, :ok)"
            ), {"s": project_slug, "q": q, "mid": matched_id, "sim": sim,
                "ms": matched_status, "ws": bool(would_serve), "ok": schema_ok})
    except Exception as exc:  # noqa: BLE001
        logger.debug("shadow_match failed for %s: %s", project_slug, exc)
