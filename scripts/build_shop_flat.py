"""Build / refresh citypharma.shop_flat — the denormalized stock table.

Kills the runtime ART<->STOCK join. Once, here, we normalize article_code on
BOTH sides (fixing the Excel '1E+12' scientific-notation corruption) and fold
stock + catalog attributes into one flat row per (art_key, site_code). All
pharma tools then read this table directly: no JOIN, no ::text cast, no
corruption surprise mid-query.

Rows whose stock code can't be linked to a catalog product still land (brand
NULL, linked=false) so corruption is VISIBLE, never silently dropped.

Idempotent: TRUNCATE + re-INSERT. Cheap (~articles x stores rows).

Runs INSIDE cp-api (DB env):

    docker exec cp-api python /app/scripts/build_shop_flat.py

Exposes run() -> dict for the training-complete hook in app/upload.py.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("scripts.build_shop_flat")

SCHEMA = "citypharma"


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
    cur.execute("SET statement_timeout = '180s';")
    cur.execute(f'SET search_path = "{SCHEMA}", public;')
    return c, cur


def _norm(v) -> str | None:
    """Canonical article key. Same normalize on BOTH sides => exact match,
    no ::text cast at read time. Excel-corrupt '1E+12' collapses to a single
    junk key that matches no catalog row (stays unlinked) — by design."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return str(int(float(s)))   # '1000000308523', '1.0e12', 1000000308523 -> '...'
    except (ValueError, OverflowError):
        return s.upper()


def run() -> dict:
    """(Re)build citypharma.shop_flat. Returns a count dict."""
    from dash.tools.table_sync import latest_table, CATALOG_COLS, STOCK_COLS

    c, cur = _conn()
    try:
        art = latest_table(cur, SCHEMA, CATALOG_COLS) or "articles_list_07052026"
        stock = latest_table(cur, SCHEMA, STOCK_COLS) or "balance_stock_07052026"
        ART = f'"{SCHEMA}"."{art}"'
        STOCK = f'"{SCHEMA}"."{stock}"'

        # 1. catalog attrs keyed by normalized article_code
        cur.execute(
            f"""SELECT article_code, brand_name, generic_name, composition, category
                FROM {ART} WHERE article_code IS NOT NULL""")
        catalog: dict[str, tuple] = {}
        for ac, brand, generic, comp, cat in cur.fetchall():
            k = _norm(ac)
            if k:
                catalog[k] = (
                    (brand or "").strip() or None,
                    (generic or "").strip() or None,
                    (comp or "").strip() or None,
                    (cat or "").strip() or None,
                )

        # 2. stock aggregated per (raw code, site); normalize + fold in Python so
        #    two raw codes that normalize equal collapse correctly.
        cur.execute(
            f"""SELECT article_code, site_code,
                       COALESCE(SUM(stock_qty),0),
                       COALESCE(MAX(weighted_cost_price),0)
                FROM {STOCK}
                WHERE site_code IS NOT NULL
                GROUP BY article_code, site_code""")
        agg: dict[tuple, list] = {}   # (art_key, site) -> [qty, cost]
        for ac, site, qty, cost in cur.fetchall():
            k = _norm(ac)
            if not k:
                continue
            key = (k, str(site).strip())
            slot = agg.setdefault(key, [0, 0])
            slot[0] += float(qty or 0)
            slot[1] = max(slot[1], float(cost or 0))

        # 3. (re)create table
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS "{SCHEMA}".shop_flat (
                art_key      text NOT NULL,
                site_code    text NOT NULL,
                brand        text,
                generic      text,
                composition  text,
                category     text,
                stock_qty    numeric NOT NULL DEFAULT 0,
                cost         numeric NOT NULL DEFAULT 0,
                is_in_stock  boolean NOT NULL DEFAULT false,
                linked       boolean NOT NULL DEFAULT false,
                updated_at   timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (art_key, site_code)
            );""")
        cur.execute(f'TRUNCATE "{SCHEMA}".shop_flat;')

        # 4. rows
        rows = []
        linked_n = 0
        for (art_key, site), (qty, cost) in agg.items():
            attrs = catalog.get(art_key)
            linked = attrs is not None
            if linked:
                linked_n += 1
            brand, generic, comp, cat = attrs if linked else (None, None, None, None)
            rows.append((
                art_key, site, brand, generic, comp, cat,
                qty, cost, qty > 0, linked,
            ))

        if rows:
            cur.executemany(
                f'INSERT INTO "{SCHEMA}".shop_flat '
                '(art_key,site_code,brand,generic,composition,category,'
                'stock_qty,cost,is_in_stock,linked) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                rows)

        # 5. indexes (after load)
        cur.execute(f'CREATE INDEX IF NOT EXISTS shop_flat_site_idx ON "{SCHEMA}".shop_flat (site_code);')
        cur.execute(f'CREATE INDEX IF NOT EXISTS shop_flat_brand_trgm ON "{SCHEMA}".shop_flat USING gin (brand gin_trgm_ops);')
        cur.execute(f'CREATE INDEX IF NOT EXISTS shop_flat_generic_trgm ON "{SCHEMA}".shop_flat USING gin (generic gin_trgm_ops);')
        cur.execute(f'CREATE INDEX IF NOT EXISTS shop_flat_instock_idx ON "{SCHEMA}".shop_flat (site_code) WHERE is_in_stock;')

        cur.execute(f'SELECT count(*), count(*) FILTER (WHERE linked) FROM "{SCHEMA}".shop_flat;')
        total, linked_total = cur.fetchone()
        return {
            "ok": True, "art_table": art, "stock_table": stock,
            "catalog_keys": len(catalog), "rows": int(total or 0),
            "linked": int(linked_total or 0),
            "unlinked": int((total or 0) - (linked_total or 0)),
        }
    finally:
        c.close()


if __name__ == "__main__":
    out = run()
    print(out)
