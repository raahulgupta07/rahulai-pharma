"""Hybrid (vector + keyword) semantic catalog search for the CityPharma Analyst.

ADVISORY / FIND / SUBSTITUTE lane: "what do you have for fever", "alternatives to
X", fuzzy brand/generic search, "drugs for high blood pressure". This is SEMANTIC
catalog browse over the Tier-3 GLOBAL product catalog — it is NOT store stock.
Counts/totals stay on raw SQL; exact branch stock stays on `stock_check`.

Pipeline (Reciprocal-Rank-Fusion of two retrievers):
  (a) embed the query (existing OpenRouter cascade, dim 1536),
  (b) vector top-30 from dash.dash_vectors (namespace='catalog') by cosine,
  (c) keyword top-30 (tsv plainto_tsquery, ILIKE fallback),
  (d) RRF fuse (score = Σ 1/(k+rank), k=60) → top `limit`.

Fail-soft: if embedding fails → keyword-only. If the catalog namespace is empty
(vectors not built yet) → pure ILIKE on the LIVE catalog table, so the tool still
answers. Catalog is global — NO store scoping here.

Own read-only direct connection to cp-db (service 'dash-db'). Disable with
PHARMA_GRAPH_DISABLED=1 (registration gate lives in build.py).
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("dash.catalog_search")

SCHEMA = "citypharma"
PROJECT = "citypharma"
NAMESPACE = "catalog"
_RRF_K = 60


def _conn():
    import psycopg
    c = psycopg.connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8,
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("SET statement_timeout = '20s';")
    cur.execute(f'SET search_path = "{SCHEMA}", public;')
    return c, cur


def _embed_query(query: str):
    """Embed the query via the existing async cascade. Returns a pgvector literal
    string, or None on any failure (→ keyword-only fallback)."""
    try:
        from dash.tools.embeddings_helper import embed_batch, vec_to_pg
        # The tool runs inside team.run on a worker thread with NO running loop,
        # so asyncio.run is safe here.
        res = asyncio.run(embed_batch([query]))
        # embed_batch may return list OR (list, tokens) tuple — handle both.
        if isinstance(res, tuple):
            res = res[0]
        if not res:
            return None
        return vec_to_pg(res[0])
    except Exception as e:
        log.warning(f"catalog_search embed failed (keyword-only): {e}")
        return None


def _row_from_meta(meta: dict, score: float) -> dict:
    meta = meta or {}
    return {
        "article_code": meta.get("article_code"),
        "brand": (meta.get("brand_name") or "").strip(),
        "generic": (meta.get("generic_name") or "").strip(),
        "category": (meta.get("category") or "").strip(),
        "indication": (meta.get("indication") or "").strip(),
        "score": round(float(score), 5),
    }


def _ilike_catalog_fallback(cur, query: str, limit: int) -> dict:
    """Pure ILIKE over the LIVE catalog table — used when the vector namespace is
    empty (vectors not built yet). Keeps the tool answering."""
    from dash.tools.table_sync import latest_table, CATALOG_COLS
    art = latest_table(cur, SCHEMA, CATALOG_COLS) or "articles_list_07052026"
    tbl = f'"{SCHEMA}"."{art}"'
    q = f"%{query.strip()}%"
    cur.execute(
        f"""SELECT article_code, brand_name, generic_name, category, indication
            FROM {tbl}
            WHERE brand_name ILIKE %s OR generic_name ILIKE %s
               OR COALESCE(indication,'') ILIKE %s OR COALESCE(composition,'') ILIKE %s
            ORDER BY brand_name
            LIMIT %s""",
        (q, q, q, q, int(limit)))
    results = []
    for ac, brand, generic, cat, ind in cur.fetchall():
        results.append({
            "article_code": int(ac) if ac is not None else None,
            "brand": (brand or "").strip(),
            "generic": (generic or "").strip(),
            "category": (cat or "").strip(),
            "indication": (ind or "").strip(),
            "score": None,
        })
    return {
        "ok": True, "query": query, "count": len(results),
        "mode": "ilike_catalog_fallback", "results": results,
    }


def catalog_search(query: str, limit: int = 12) -> dict:
    """Hybrid vector+keyword semantic search over the global product catalog.

    query: a symptom/condition ("fever"), a fuzzy/partial name, or a "similar to X".
    limit: max results (default 12).
    Returns {ok, query, count, results:[{article_code, brand, generic, category,
    indication, score}]}. GLOBAL Tier-3 — no store scope.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "catalog search disabled"}
    if not query or not query.strip():
        return {"ok": False, "error": "provide a symptom, condition, or medicine name to search"}
    query = query.strip()
    try:
        c, cur = _conn()
        try:
            # Is the catalog namespace populated?
            cur.execute(
                "SELECT count(*) FROM dash.dash_vectors WHERE project_slug=%s AND namespace=%s",
                (PROJECT, NAMESPACE))
            n_vec = int((cur.fetchone() or [0])[0] or 0)
            if n_vec == 0:
                # Vectors not built yet → pure ILIKE on the live catalog table.
                return _ilike_catalog_fallback(cur, query, limit)

            # ── (a) embed ────────────────────────────────────────────────────
            qvec = _embed_query(query)

            # ── (b) vector top-30 (cosine) ───────────────────────────────────
            vec_rank: dict[str, int] = {}
            meta_by_id: dict[str, dict] = {}
            if qvec is not None:
                cur.execute(
                    """SELECT source_id, metadata
                       FROM dash.dash_vectors
                       WHERE project_slug=%s AND namespace=%s
                       ORDER BY embedding <=> %s::vector
                       LIMIT 30""",
                    (PROJECT, NAMESPACE, qvec))
                for i, (sid, meta) in enumerate(cur.fetchall()):
                    vec_rank[sid] = i
                    meta_by_id[sid] = meta or {}

            # ── (c) keyword top-30 (tsv, ILIKE fallback) ─────────────────────
            kw_rank: dict[str, int] = {}
            kw_rows = []
            try:
                cur.execute(
                    """SELECT source_id, metadata
                       FROM dash.dash_vectors
                       WHERE project_slug=%s AND namespace=%s
                         AND tsv @@ plainto_tsquery('english', %s)
                       ORDER BY ts_rank(tsv, plainto_tsquery('english', %s)) DESC
                       LIMIT 30""",
                    (PROJECT, NAMESPACE, query, query))
                kw_rows = cur.fetchall()
            except Exception:
                kw_rows = []
            if not kw_rows:
                # ILIKE fallback over the blob text + metadata fields.
                like = f"%{query}%"
                cur.execute(
                    """SELECT source_id, metadata
                       FROM dash.dash_vectors
                       WHERE project_slug=%s AND namespace=%s
                         AND (text ILIKE %s
                              OR COALESCE(metadata->>'brand_name','') ILIKE %s
                              OR COALESCE(metadata->>'generic_name','') ILIKE %s
                              OR COALESCE(metadata->>'indication','') ILIKE %s)
                       LIMIT 30""",
                    (PROJECT, NAMESPACE, like, like, like, like))
                kw_rows = cur.fetchall()
            for i, (sid, meta) in enumerate(kw_rows):
                kw_rank[sid] = i
                meta_by_id.setdefault(sid, meta or {})

            # ── (d) RRF fuse ─────────────────────────────────────────────────
            fused: dict[str, float] = {}
            for sid, r in vec_rank.items():
                fused[sid] = fused.get(sid, 0.0) + 1.0 / (_RRF_K + r + 1)
            for sid, r in kw_rank.items():
                fused[sid] = fused.get(sid, 0.0) + 1.0 / (_RRF_K + r + 1)

            ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[: int(limit)]
            results = [_row_from_meta(meta_by_id.get(sid, {}), sc) for sid, sc in ordered]

            return {
                "ok": True, "query": query, "count": len(results),
                "mode": "keyword_only" if qvec is None else "hybrid",
                "results": results,
            }
        finally:
            c.close()
    except Exception as e:
        log.warning(f"catalog_search failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
