"""Build / refresh the semantic catalog vectors for CityPharma.

Embeds every product in the live catalog into dash.dash_vectors
(namespace='catalog') so the `catalog_search` tool can do hybrid (vector +
keyword) advisory/find/substitute search. Idempotent: rows whose text blob is
unchanged (same text_hash) are skipped.

Runs INSIDE cp-api (has OPENROUTER_API_KEY + DB env). Standalone — no running
event loop, so embeddings are driven via asyncio.run().

    docker exec cp-api python /app/scripts/build_catalog_vectors.py

Exposes run() -> dict for the training-complete hook in app/upload.py.
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("scripts.build_catalog_vectors")

SCHEMA = "citypharma"
PROJECT = "citypharma"
NAMESPACE = "catalog"
_CHUNK = 96  # matches embeddings_helper BATCH_SIZE


def _conn():
    import psycopg
    c = psycopg.connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=10,
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("SET statement_timeout = '120s';")
    cur.execute(f'SET search_path = "{SCHEMA}", public;')
    return c, cur


def _blob(brand, generic, comp, indication, dosage, side, cat) -> str:
    parts = []
    if brand:
        parts.append(str(brand).strip())
    if generic:
        parts.append(f"generic: {str(generic).strip()}")
    if comp:
        parts.append(f"composition: {str(comp).strip()}")
    if indication:
        parts.append(f"treats: {str(indication).strip()}")
    if dosage:
        parts.append(f"dosage: {str(dosage).strip()}")
    if side:
        parts.append(f"side effects: {str(side).strip()}")
    if cat:
        parts.append(f"category: {str(cat).strip()}")
    return " | ".join(parts)


def run() -> dict:
    """Build/refresh catalog vectors. Returns a count dict."""
    from dash.tools.embeddings_helper import embed_batch, vec_to_pg, text_hash
    from dash.tools.table_sync import latest_table, CATALOG_COLS

    c, cur = _conn()
    try:
        art = latest_table(cur, SCHEMA, CATALOG_COLS) or "articles_list_07052026"
        tbl = f'"{SCHEMA}"."{art}"'
        cur.execute(
            f"""SELECT article_code, brand_name, generic_name, composition, category,
                       indication, dosage, side_effect, status
                FROM {tbl}
                WHERE article_code IS NOT NULL""")
        rows = cur.fetchall()

        # Build candidate (source_id, blob, hash, metadata) and skip unchanged.
        cur.execute(
            "SELECT source_id, text_hash FROM dash.dash_vectors "
            "WHERE project_slug=%s AND namespace=%s",
            (PROJECT, NAMESPACE))
        existing = {sid: th for sid, th in cur.fetchall()}

        import json
        pending = []  # (source_id, blob, hash, metadata_json)
        scanned = 0
        for (ac, brand, generic, comp, cat, ind, dosage, side, status) in rows:
            scanned += 1
            blob = _blob(brand, generic, comp, ind, dosage, side, cat)
            if not blob:
                continue
            sid = str(ac)
            th = text_hash(blob)
            if existing.get(sid) == th:
                continue  # idempotent skip
            meta = {
                "article_code": int(ac),
                "brand_name": (brand or "").strip(),
                "generic_name": (generic or "").strip(),
                "category": (cat or "").strip(),
                "indication": (ind or "").strip(),
            }
            pending.append((sid, blob, th, json.dumps(meta)))

        embedded = 0
        for start in range(0, len(pending), _CHUNK):
            batch = pending[start:start + _CHUNK]
            texts = [b[1] for b in batch]
            res = asyncio.run(embed_batch(texts))
            if isinstance(res, tuple):  # may return (list, tokens)
                res = res[0]
            for (sid, blob, th, meta_json), vec in zip(batch, res):
                cur.execute(
                    """INSERT INTO dash.dash_vectors
                         (project_slug, namespace, source_id, source_table,
                          text, text_hash, embedding, metadata)
                       VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb)
                       ON CONFLICT (project_slug, namespace, source_id) DO UPDATE
                         SET source_table = EXCLUDED.source_table,
                             text         = EXCLUDED.text,
                             text_hash    = EXCLUDED.text_hash,
                             embedding    = EXCLUDED.embedding,
                             metadata     = EXCLUDED.metadata,
                             updated_at   = NOW()""",
                    (PROJECT, NAMESPACE, sid, art, blob, th,
                     vec_to_pg(vec), meta_json))
                embedded += 1

        cur.execute(
            "SELECT count(*) FROM dash.dash_vectors WHERE project_slug=%s AND namespace=%s",
            (PROJECT, NAMESPACE))
        total = int((cur.fetchone() or [0])[0] or 0)
        return {
            "ok": True, "table": art, "scanned": scanned,
            "embedded": embedded, "skipped": scanned - len(pending),
            "total": total,
        }
    finally:
        c.close()


if __name__ == "__main__":
    out = run()
    print(out)
