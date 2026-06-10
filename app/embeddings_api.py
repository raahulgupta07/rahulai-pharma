"""Embeddings API — OpenAI-compatible embeddings + per-project vector search/ingest.

Endpoints:
- POST /v1/embeddings                                  OpenAI-compat embedding
- POST /api/projects/{slug}/vectors/search             semantic / hybrid search
- POST /api/projects/{slug}/vectors/ingest             bulk upsert (sha256 dedup)
- GET  /api/projects/{slug}/vectors/list               paginated browse
- DELETE /api/projects/{slug}/vectors/{source_id}      single delete

Bearer token auth (re-uses app.auth.get_current_user).
Vectors stored in `dash_vectors`. Audit rows go to `dash_vector_audit`.

Embedding generation is delegated to `dash.tools.embeddings_helper.embed_batch`
(imported lazily so this module loads even when the helper is missing).
Hybrid search is delegated to `dash.tools.hybrid_search.hybrid_search` when
`hybrid=true` (also imported lazily).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Embeddings"])


# ───────────────────── auth helpers ─────────────────────

def _user(request: Request) -> dict:
    from app.auth import get_current_user
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "auth required")
    return u


def _check(user: dict, slug: str, role: str = "viewer") -> None:
    from app.auth import check_project_permission
    res = check_project_permission(user, slug, role)
    if not res:
        raise HTTPException(403, "permission denied")


# ───────────────────── helper imports ─────────────────────

async def _embed_batch(texts: list[str], model: str | None = None) -> tuple[list[list[float]], int]:
    """Lazy import. Returns (vectors, prompt_tokens). embed_batch is async."""
    try:
        from dash.tools.embeddings_helper import embed_batch  # type: ignore
    except Exception as e:
        logger.error("embeddings_helper missing: %s", e)
        raise HTTPException(503, f"embeddings backend unavailable: {e}")
    out = await (embed_batch(texts, model=model) if model else embed_batch(texts))
    # Helper may return either list[list[float]] or (list[list[float]], tokens)
    if isinstance(out, tuple) and len(out) == 2:
        vectors, tokens = out
    else:
        vectors = out
        tokens = sum(len((t or "").split()) for t in texts)
    return vectors, int(tokens)


def _vec_to_pg(v: list[float]) -> str:
    try:
        from dash.tools.embeddings_helper import vec_to_pg  # type: ignore
        return vec_to_pg(v)
    except Exception:
        return "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"


# ───────────────────── DB helpers ─────────────────────

_TABLE = "dash_vectors"
_AUDIT = "dash_vector_audit"


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _write_engine():
    # get_sql_engine() has a read-only guard on the public/dash schema, so any
    # INSERT/UPDATE/DELETE silently rolls back. Writes MUST use get_write_engine().
    from db.session import get_write_engine
    return get_write_engine()


def _audit(conn, slug: str, user_id: int | None, action: str, details: dict | None = None) -> None:
    """Best-effort audit row. Runs in its OWN transaction so a failure can never
    poison the caller's write transaction (a failed statement aborts the whole
    PG txn → the final COMMIT silently rolls back the real inserts).
    Real schema: (project_slug, op, query, scope_attrs, rows_returned, latency_ms, ts).
    """
    from sqlalchemy import text as _t
    d = details or {}
    rows_returned = int(d.get("ingested") or d.get("deleted") or d.get("count") or 0)
    try:
        with _write_engine().begin() as c2:
            c2.execute(
                _t(
                    f"INSERT INTO {_AUDIT} (project_slug, op, query, scope_attrs, rows_returned, ts) "
                    "VALUES (:s, :op, :q, CAST(:sa AS jsonb), :rr, NOW())"
                ),
                {"s": slug, "op": action, "q": json.dumps(d)[:2000],
                 "sa": json.dumps({"user_id": user_id}), "rr": rows_returned},
            )
    except Exception as e:
        logger.debug("audit insert skipped: %s", e)


# ───────────────────── 1. OpenAI-compat /v1/embeddings ─────────────────────

@router.post("/v1/embeddings")
async def openai_embeddings(request: Request):
    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    inp = body.get("input")
    model = body.get("model") or "default"
    # "default" is a display label, NOT a real model id — pass None so the
    # helper resolves its real DEFAULT_MODEL (openai/text-embedding-3-small).
    real_model = None if model in (None, "", "default") else model
    if inp is None:
        raise HTTPException(400, "missing 'input'")
    if isinstance(inp, str):
        texts = [inp]
    elif isinstance(inp, list):
        texts = [str(x) for x in inp]
    else:
        raise HTTPException(400, "'input' must be str or list[str]")

    if len(texts) == 0:
        raise HTTPException(400, "empty input")
    if len(texts) > 2048:
        raise HTTPException(413, "max 2048 inputs per request")

    vectors, prompt_tokens = await _embed_batch(texts, model=real_model)

    data = [
        {"object": "embedding", "index": i, "embedding": v}
        for i, v in enumerate(vectors)
    ]
    logger.info("embeddings: user=%s n=%d model=%s tokens=%d",
                user.get("user_id"), len(texts), model, prompt_tokens)
    # Meter into the unified usage ledger (request_type='embedding'). Fail-soft.
    # input_preview = the FIRST embedded text (truncated), captured only when
    # EMBED_LOG_INPUT=1 (privacy + size opt-in, mirrors EMBED_LOG_BODIES). This
    # powers the "Embeddings — usage with the question" admin drill-down.
    try:
        import os as _os
        from app.api_gateway import _log_usage
        _prev = None
        if _os.getenv("EMBED_LOG_INPUT", "0").lower() in ("1", "true", "yes", "on"):
            _first = texts[0] if texts else ""
            _prev = (_first[:280] + (f"  …(+{len(texts)-1} more)" if len(texts) > 1 else "")) if _first else None
        _log_usage(user, real_model or "text-embedding-3-small",
                   int(prompt_tokens or 0), 0, streamed=False,
                   request_type="embedding", input_preview=_prev)
    except Exception:
        logger.debug("embeddings: usage meter failed (ignored)", exc_info=True)
    return {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }


# ───────────────────── 2. Vector search ─────────────────────

@router.post("/api/projects/{slug}/vectors/search")
async def vector_search(slug: str, request: Request):
    user = _user(request)
    _check(user, slug, "viewer")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "missing 'query'")
    namespaces = body.get("namespaces") or None
    top_k = int(body.get("top_k") or 10)
    if top_k < 1:
        top_k = 1
    if top_k > 200:
        top_k = 200
    hybrid = bool(body.get("hybrid", False))
    user_attrs = body.get("user_attrs") or {}

    # Hybrid path — delegate to hybrid_search if available
    if hybrid:
        try:
            from dash.tools.hybrid_search import hybrid_search  # type: ignore
            results = hybrid_search(
                project_slug=slug,
                query=query,
                namespaces=namespaces,
                top_k=top_k,
                user_attrs=user_attrs,
            )
            return {"results": results}
        except ImportError:
            logger.info("hybrid_search not available — falling back to vector path")
        except Exception as e:
            logger.warning("hybrid_search failed (%s) — falling back to vector path", e)

    # Vector path
    qvecs, _ = await _embed_batch([query])
    qvec_str = _vec_to_pg(qvecs[0])

    from sqlalchemy import text as _t

    sql = f"""
        SELECT source_id, namespace, text, scope_attrs, metadata,
               1 - (embedding <=> CAST(:qvec AS vector)) AS score
        FROM {_TABLE}
        WHERE project_slug = :slug
    """
    params: dict[str, Any] = {"qvec": qvec_str, "slug": slug, "k": top_k}
    if namespaces:
        sql += " AND namespace = ANY(:nss)"
        params["nss"] = list(namespaces)
    sql += " ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k"

    eng = _engine()
    out: list[dict] = []
    with eng.begin() as conn:
        # PG session vars used by RLS policies. SET LOCAL can't take bind params
        # under psycopg/PgBouncer — use set_config(..., is_local=true) instead.
        conn.execute(_t("SELECT set_config('app.project_slug', :v, true)"), {"v": slug})
        conn.execute(_t("SELECT set_config('app.user_attrs', :v, true)"),
                     {"v": json.dumps(user_attrs)})
        rows = conn.execute(_t(sql), params).mappings().all()
        for r in rows:
            out.append({
                "source_id": r["source_id"],
                "text": r["text"],
                "namespace": r["namespace"],
                "score": float(r["score"]) if r["score"] is not None else None,
                "scope_attrs": r["scope_attrs"] or {},
                "metadata": r["metadata"] or {},
            })
        _audit(conn, slug, user.get("user_id"), "search", {
            "query_chars": len(query),
            "top_k": top_k,
            "namespaces": namespaces,
            "result_count": len(out),
            "hybrid": hybrid,
        })

    return {"results": out}


# ───────────────────── 3. Vector ingest ─────────────────────

def _hash_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


@router.post("/api/projects/{slug}/vectors/ingest")
async def vector_ingest(slug: str, request: Request):
    user = _user(request)
    _check(user, slug, "editor")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "'rows' must be non-empty list")
    if len(rows) > 5000:
        raise HTTPException(413, "max 5000 rows per ingest")

    from sqlalchemy import text as _t
    eng = _engine()

    # Pre-compute hashes
    prepared = []
    for r in rows:
        ns = (r.get("namespace") or "").strip()
        sid = (r.get("source_id") or "").strip()
        txt = r.get("text") or ""
        if not ns or not sid or not txt:
            continue
        prepared.append({
            "namespace": ns,
            "source_id": sid,
            "source_table": r.get("source_table"),
            "text": txt,
            "text_hash": _hash_text(txt),
            "scope_attrs": r.get("scope_attrs") or {},
            "metadata": r.get("metadata") or {},
        })

    if not prepared:
        return {"ingested": 0, "skipped": len(rows)}

    # Look up existing hashes
    keys = [(p["namespace"], p["source_id"]) for p in prepared]
    existing: dict[tuple[str, str], str] = {}
    with eng.begin() as conn:
        ns_list = list({k[0] for k in keys})
        sid_list = list({k[1] for k in keys})
        sel = _t(
            f"SELECT namespace, source_id, text_hash FROM {_TABLE} "
            "WHERE project_slug = :slug "
            "AND namespace = ANY(:nss) AND source_id = ANY(:sids)"
        )
        for r in conn.execute(sel, {"slug": slug, "nss": ns_list, "sids": sid_list}).mappings():
            existing[(r["namespace"], r["source_id"])] = r["text_hash"]

    changed = [p for p in prepared if existing.get((p["namespace"], p["source_id"])) != p["text_hash"]]
    skipped = len(rows) - len(changed)

    if not changed:
        return {"ingested": 0, "skipped": skipped}

    # Embed only changed rows
    vectors, _tokens = await _embed_batch([p["text"] for p in changed])

    upsert = _t(
        f"""
        INSERT INTO {_TABLE}
            (project_slug, namespace, source_id, source_table, text, text_hash,
             embedding, scope_attrs, metadata, created_at, updated_at)
        VALUES
            (:slug, :namespace, :source_id, :source_table, :text, :text_hash,
             CAST(:embedding AS vector), CAST(:scope_attrs AS jsonb),
             CAST(:metadata AS jsonb), NOW(), NOW())
        ON CONFLICT (project_slug, namespace, source_id) DO UPDATE SET
            text = EXCLUDED.text,
            text_hash = EXCLUDED.text_hash,
            embedding = EXCLUDED.embedding,
            scope_attrs = EXCLUDED.scope_attrs,
            metadata = EXCLUDED.metadata,
            source_table = EXCLUDED.source_table,
            updated_at = NOW()
        """
    )

    with _write_engine().begin() as conn:
        for p, v in zip(changed, vectors):
            conn.execute(upsert, {
                "slug": slug,
                "namespace": p["namespace"],
                "source_id": p["source_id"],
                "source_table": p["source_table"],
                "text": p["text"],
                "text_hash": p["text_hash"],
                "embedding": _vec_to_pg(v),
                "scope_attrs": json.dumps(p["scope_attrs"]),
                "metadata": json.dumps(p["metadata"]),
            })
        _audit(conn, slug, user.get("user_id"), "ingest", {
            "ingested": len(changed),
            "skipped": skipped,
            "total_rows": len(rows),
        })

    return {"ingested": len(changed), "skipped": skipped}


# ───────────────────── 4. List ─────────────────────

@router.get("/api/projects/{slug}/vectors/list")
def vector_list(slug: str, request: Request, namespace: str | None = None,
                limit: int = 100, offset: int = 0):
    user = _user(request)
    _check(user, slug, "viewer")
    if limit < 1:
        limit = 1
    if limit > 1000:
        limit = 1000
    if offset < 0:
        offset = 0

    from sqlalchemy import text as _t
    sql = (
        f"SELECT namespace, source_id, source_table, text, scope_attrs, metadata, "
        f"created_at, updated_at FROM {_TABLE} WHERE project_slug = :slug"
    )
    params: dict[str, Any] = {"slug": slug, "lim": limit, "off": offset}
    if namespace:
        sql += " AND namespace = :ns"
        params["ns"] = namespace
    sql += " ORDER BY updated_at DESC NULLS LAST LIMIT :lim OFFSET :off"

    count_sql = f"SELECT COUNT(*) FROM {_TABLE} WHERE project_slug = :slug"
    count_params: dict[str, Any] = {"slug": slug}
    if namespace:
        count_sql += " AND namespace = :ns"
        count_params["ns"] = namespace

    eng = _engine()
    out = []
    with eng.connect() as conn:
        rows = conn.execute(_t(sql), params).mappings().all()
        total = conn.execute(_t(count_sql), count_params).scalar() or 0
        for r in rows:
            out.append({
                "namespace": r["namespace"],
                "source_id": r["source_id"],
                "source_table": r["source_table"],
                "text": r["text"],
                "scope_attrs": r["scope_attrs"] or {},
                "metadata": r["metadata"] or {},
                "created_at": str(r["created_at"]) if r["created_at"] else None,
                "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
            })
    return {"rows": out, "total": int(total), "limit": limit, "offset": offset}


# ───────────────────── 5. Delete ─────────────────────

@router.delete("/api/projects/{slug}/vectors/{source_id}")
def vector_delete(slug: str, source_id: str, request: Request, namespace: str | None = None):
    user = _user(request)
    _check(user, slug, "editor")

    from sqlalchemy import text as _t
    sql = f"DELETE FROM {_TABLE} WHERE project_slug = :slug AND source_id = :sid"
    params: dict[str, Any] = {"slug": slug, "sid": source_id}
    if namespace:
        sql += " AND namespace = :ns"
        params["ns"] = namespace

    with _write_engine().begin() as conn:
        res = conn.execute(_t(sql), params)
        deleted = int(res.rowcount or 0)
        _audit(conn, slug, user.get("user_id"), "delete", {
            "source_id": source_id,
            "namespace": namespace,
            "deleted": deleted,
        })
    if deleted == 0:
        raise HTTPException(404, "no matching vector row")
    return {"deleted": deleted}
