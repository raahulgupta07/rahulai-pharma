"""Query bank — serve + recall over captured/proven question->SQL patterns.

Two reuse modes over public.dash_query_patterns (embeddings in
dash.dash_vectors namespace='qbank'):

  • recall_similar()  — Mode 2 (LLM-driven): return the top-K proven/candidate
    SQL for similar past questions as a HINT. The agent adapts + runs it. The
    LLM stays in control — no hardcoded SQL, graceful when the bank is empty.

  • try_query_bank_serve() — Mode 1 (bypass): on an exact-enough hit
    (sim >= serve threshold, status='proven', schema-ok) re-run the stored SQL
    LIVE (fresh numbers) and format in code. ZERO LLM. Falls through otherwise.

Both reuse schema_guard (drift) + verified_reward._run_rows (exec) +
cache_curator._build_card (render). Capture/shadow live in query_capture.py.
Plan: docs/plans/continuous_query_learning.md.
"""
from __future__ import annotations

import logging
import os
import re
import time

logger = logging.getLogger("dash.query_bank")

_NAMESPACE = "qbank"


def _serve_sim() -> float:
    # Default tuned on dev (Q4): paraphrases cluster 0.81-0.95; 0.96 caught none.
    try:
        return float(os.getenv("QUERY_BANK_SERVE_SIM", "0.93"))
    except Exception:
        return 0.93


def _recall_sim() -> float:
    try:
        return float(os.getenv("QUERY_BANK_RECALL_SIM", "0.80"))
    except Exception:
        return 0.80


def _vec_literal(emb: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in emb) + "]"


# Two-level TTL cache for question embeddings. Collapses repeat/identical
# questions (e.g. 30 outlets asking the same thing at once) to ONE embed API
# call instead of N — the embed round-trip was the remaining serial cost on the
# Mode-1 bypass once team-build was removed.
#   L1 = per-worker dict (fastest, but each of the N gunicorn workers warms
#        independently → N cold embeds before fully warm).
#   L2 = Redis (cross-worker) → the FIRST worker to embed a question warms it
#        for ALL workers. Fail-soft: Redis down → L1-only, never breaks serve.
# Keyed on the normalized question (+ embedding model, so a model swap can't
# serve stale vectors).
_EMB_CACHE: dict[str, tuple[float, list[float]]] = {}
_EMB_TTL = float(os.getenv("QUERY_BANK_EMB_TTL", "300"))
_EMB_MAX = 512
_EMB_REDIS_PREFIX = "qbank:emb:"

_emb_redis = None
_emb_redis_tried = False


def _get_emb_redis():
    """Lazy Redis singleton for the embedding L2 cache. None on any error."""
    global _emb_redis, _emb_redis_tried
    if _emb_redis is not None or _emb_redis_tried:
        return _emb_redis
    _emb_redis_tried = True
    try:
        import redis  # type: ignore
        url = os.getenv("REDIS_URL", "redis://dash-redis:6379")
        c = redis.Redis.from_url(url, decode_responses=True,
                                 socket_connect_timeout=2, socket_timeout=2)
        c.ping()
        _emb_redis = c
    except Exception as exc:  # noqa: BLE001
        logger.debug("query_bank emb redis unavailable (%s) — L1-only", exc)
        _emb_redis = None
    return _emb_redis


def _emb_key(question: str) -> str:
    try:
        from dash.settings import get_embedding_model as _gm
        model = _gm() or ""
    except Exception:
        model = ""
    norm = re.sub(r"\s+", " ", (question or "").strip().lower())
    return f"{model}|{norm}"


def _l1_put(key: str, emb: list[float]) -> None:
    if len(_EMB_CACHE) >= _EMB_MAX:  # cheap bound — evict oldest ~half
        for k in sorted(_EMB_CACHE, key=lambda k: _EMB_CACHE[k][0])[: _EMB_MAX // 2]:
            _EMB_CACHE.pop(k, None)
    _EMB_CACHE[key] = (time.time(), emb)


async def _embed_cached(question: str) -> list[float]:
    """Embed with a two-level (in-proc + Redis) TTL cache. Fail-soft → []."""
    key = _emb_key(question)
    now = time.time()
    # L1
    hit = _EMB_CACHE.get(key)
    if hit and (now - hit[0]) < _EMB_TTL:
        return hit[1]
    # L2 (Redis, cross-worker)
    import json as _json
    rc = _get_emb_redis()
    if rc is not None:
        try:
            raw = rc.get(_EMB_REDIS_PREFIX + key)
            if raw:
                emb = _json.loads(raw)
                if emb:
                    _l1_put(key, emb)
                    return emb
        except Exception:
            pass
    # Miss → embed once, write both levels.
    from dash.tools.embeddings_helper import embed_text
    emb = await embed_text(question)
    if emb:
        _l1_put(key, emb)
        if rc is not None:
            try:
                rc.setex(_EMB_REDIS_PREFIX + key, int(_EMB_TTL), _json.dumps(emb))
            except Exception:
                pass
    return emb or []


def _has_bank(project_slug: str) -> bool:
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            return bool(conn.execute(_text(
                "SELECT 1 FROM dash.dash_vectors "
                "WHERE project_slug = :s AND namespace = :ns LIMIT 1"
            ), {"s": project_slug, "ns": _NAMESPACE}).first())
    except Exception:
        return False


def _schema_ok(project_slug: str, schema_hash: str | None, tables_csv: str | None) -> bool | None:
    """True/False if a stored schema_hash can be checked; None if unknown."""
    if not schema_hash:
        return None
    try:
        from dash.learning.schema_guard import live_schema_hash
        tabs = [t for t in (tables_csv or "").split(",") if t]
        if not tabs:
            return None
        return live_schema_hash(project_slug, tabs) == schema_hash
    except Exception:
        return None


async def _nn(project_slug: str, question: str, limit: int, statuses: tuple[str, ...]):
    """Embed the question, return nearest qbank rows (joined to patterns)."""
    from sqlalchemy import text as _text
    from db.session import get_sql_engine

    emb = await _embed_cached(question)
    if not emb:
        return []
    vec = _vec_literal(emb)
    placeholders = ",".join(f"'{s}'" for s in statuses)
    with get_sql_engine().connect() as conn:
        rows = conn.execute(_text(
            "SELECT p.id, p.question, p.sql, p.status, p.uses, p.schema_hash, "
            "       p.tables_used, 1 - (v.embedding <=> CAST(:v AS vector)) AS sim "
            "FROM dash.dash_vectors v "
            "JOIN public.dash_query_patterns p ON p.id = v.source_id::int "
            "WHERE v.project_slug = :s AND v.namespace = :ns "
            f"  AND p.status IN ({placeholders}) "
            "ORDER BY v.embedding <=> CAST(:v AS vector) LIMIT :k"
        ), {"s": project_slug, "ns": _NAMESPACE, "v": vec, "k": limit}).fetchall()
    return rows


# ── Mode 2 — recall as a hint (LLM adapts) ───────────────────────────────────

async def recall_similar(project_slug: str, question: str, limit: int = 3) -> list[dict]:
    """Top-K proven/candidate SQL for similar past questions, schema-ok only.
    Returned as hints — NOT executed. Fail-soft (empty list)."""
    q = (question or "").strip()
    if not project_slug or len(q) < 4 or not _has_bank(project_slug):
        return []
    try:
        rows = await _nn(project_slug, q, max(limit * 3, 6), ("proven", "candidate"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_similar nn failed for %s: %s", project_slug, exc)
        return []
    floor = _recall_sim()
    out: list[dict] = []
    for r in rows:
        sim = float(r[7]) if r[7] is not None else 0.0
        if sim < floor:
            continue
        ok = _schema_ok(project_slug, r[5], r[6])
        if ok is False:  # stored SQL no longer matches live schema — skip
            continue
        out.append({"id": int(r[0]), "question": r[1], "sql": r[2],
                    "status": r[3], "uses": int(r[4] or 0), "sim": round(sim, 3)})
        if len(out) >= limit:
            break
    return out


def recall_similar_sync(project_slug: str, question: str, limit: int = 3) -> list[dict]:
    """Thread-isolated sync wrapper for agent tool use (avoids 'loop already
    running'). Runs recall_similar on a private loop. Fail-soft (empty list)."""
    import concurrent.futures
    import asyncio

    def _run():
        return asyncio.run(recall_similar(project_slug, question, limit))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_run).result(timeout=8)
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_similar_sync failed: %s", exc)
        return []


# ── Mode 1 — bypass serve (exact hit, zero LLM) ──────────────────────────────

def try_query_bank_serve(project_slug: str, question: str) -> dict | None:
    """Exact-enough hit (sim >= serve, proven, schema-ok) → re-run SQL live +
    render an AnswerCard. ZERO LLM. None on miss/drift/disabled. Sync wrapper
    around the async NN (runs its own loop — call from the sync serve path)."""
    if os.getenv("QUERY_BANK_BYPASS_DISABLED", "0") in ("1", "true", "True", "yes"):
        return None
    q = (question or "").strip()
    if not project_slug or len(q) < 4 or not _has_bank(project_slug):
        return None
    try:
        import asyncio
        import concurrent.futures
        # The chat endpoint is async (a loop is already running) — asyncio.run()
        # would raise. Run the NN on a private loop in a worker thread.
        def _run():
            return asyncio.run(_nn(project_slug, q, 1, ("proven",)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
            rows = _ex.submit(_run).result(timeout=8)
    except Exception as exc:  # noqa: BLE001
        logger.debug("query_bank serve nn failed for %s: %s", project_slug, exc)
        return None
    if not rows:
        return None
    r = rows[0]
    sim = float(r[7]) if r[7] is not None else 0.0
    if sim < _serve_sim():
        return None
    if _schema_ok(project_slug, r[5], r[6]) is False:
        return None
    sql = (r[2] or "").strip()
    if not sql:
        return None
    # Re-run the stored SQL LIVE → fresh numbers.
    try:
        t0 = time.monotonic()
        from dash.learning import verified_reward as _vr
        run = _vr._run_rows(project_slug, sql, limit=20)
        if not run or run.get("value") is None:
            return None
        value = run.get("value")
        rows_out = run.get("rows") or []
        cols = run.get("columns") or []
        from dash.learning.schema_guard import sql_source_tables
        tables = sql_source_tables(sql)
        from dash.learning.cache_curator import _build_card
        card = _build_card(q, value, rows_out, cols, sql, tables,
                           row_count=run.get("row_count"))
        elapsed = int((time.monotonic() - t0) * 1000)
        # Bump hit telemetry on the pattern (fail-soft).
        try:
            from sqlalchemy import text as _text
            from db.session import get_write_engine
            with get_write_engine().begin() as conn:
                conn.execute(_text(
                    "UPDATE public.dash_query_patterns "
                    "SET uses = uses + 1, last_used = now() WHERE id = :id"
                ), {"id": int(r[0])})
        except Exception:
            pass
        logger.info("query_bank HIT slug=%s pattern=%s sim=%.3f %dms",
                    project_slug, r[0], sim, elapsed)
        return {
            "content": f"{card}\n[VERIFIED:{elapsed}ms · learned]",
            "sql": sql, "rows": rows_out, "columns": cols, "value": value,
            "row_count": run.get("row_count") or len(rows_out), "elapsed_ms": elapsed,
            "pattern_id": int(r[0]), "matched_q": r[1], "sim": round(sim, 3),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("query_bank serve exec failed for %s: %s", project_slug, exc)
        return None
