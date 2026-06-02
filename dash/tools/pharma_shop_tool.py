"""Shop-counter stock lookup for the CityPharma Analyst.

Pharmacy counter staff ask: "is X in stock", "find <salt>", "<brand> out — alternatives".
This tool answers in shop shape: matched medicines with stock AT THEIR BRANCH first,
other branches as a transfer hint, cost, and an in-stock flag. Substitutes come from
the AGE graph tool (find_substitutes).

Own read-only direct connection to cp-db (service 'dash-db'). Disable with
PHARMA_GRAPH_DISABLED=1.
"""
from __future__ import annotations

import os
import logging

log = logging.getLogger("dash.pharma_shop")

SCHEMA = "proj_demo_citypharma"
ART = f"{SCHEMA}.citypharma_articles"
STOCK = f"{SCHEMA}.citypharma_balance_stock"


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
    return c, cur


def stock_check(query: str = "", site_code: str = "", limit: int = 15) -> dict:
    """Look up medicines by brand name OR salt (generic), scoped to a branch.

    query: brand name, partial name, or salt (e.g. 'panadol', 'paracetamol').
    site_code: the staff member's branch — stock is reported FOR THIS BRANCH first.
               (Comes from SHOP CONTEXT in the prompt; pass it through.)
    Returns matches with: brand, salt, category, your_stock, in_stock, cost,
    other branches that have it, and the article_code (for find_substitutes).
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "shop tools disabled"}
    if not query or not query.strip():
        return {"ok": False, "error": "provide a medicine name or salt to search"}
    q = f"%{query.strip()}%"
    try:
        c, cur = _conn()
        try:
            # matched articles (by brand OR salt), with this branch's stock
            if site_code:
                cur.execute(
                    f"""SELECT a.article_code, a.brand_name, a.generic_name, a.category,
                               COALESCE(b.stock_qty,0) AS your_qty, COALESCE(b.weighted_cost_price,0) AS cost
                        FROM {ART} a
                        LEFT JOIN {STOCK} b
                          ON b.article_code = a.article_code AND b.site_code = %s
                        WHERE a.brand_name ILIKE %s OR a.generic_name ILIKE %s
                        ORDER BY (COALESCE(b.stock_qty,0) > 0) DESC, COALESCE(b.stock_qty,0) DESC,
                                 a.brand_name
                        LIMIT %s""",
                    (site_code, q, q, int(limit)))
            else:
                cur.execute(
                    f"""SELECT a.article_code, a.brand_name, a.generic_name, a.category,
                               COALESCE(SUM(b.stock_qty),0) AS your_qty,
                               COALESCE(MAX(b.weighted_cost_price),0) AS cost
                        FROM {ART} a
                        LEFT JOIN {STOCK} b ON b.article_code = a.article_code
                        WHERE a.brand_name ILIKE %s OR a.generic_name ILIKE %s
                        GROUP BY 1,2,3,4
                        ORDER BY your_qty DESC, a.brand_name
                        LIMIT %s""",
                    (q, q, int(limit)))
            rows = cur.fetchall()
            if not rows:
                return {"ok": True, "site": site_code or "all", "query": query, "count": 0, "results": []}

            codes = [int(r[0]) for r in rows]
            # other branches holding each matched article (top few), only when site-scoped
            other = {}
            if site_code and codes:
                ph = ",".join(str(x) for x in codes)
                cur.execute(
                    f"""SELECT article_code, site_code, stock_qty FROM {STOCK}
                        WHERE article_code IN ({ph}) AND site_code <> %s AND stock_qty > 0
                        ORDER BY stock_qty DESC""", (site_code,))
                for ac, sc, qty in cur.fetchall():
                    other.setdefault(int(ac), [])
                    if len(other[int(ac)]) < 4:
                        other[int(ac)].append({"site": sc, "qty": int(qty)})

            results = []
            for ac, brand, salt, cat, your_qty, cost in rows:
                ac = int(ac)
                results.append({
                    "article_code": ac,
                    "brand": (brand or "").strip(),
                    "salt": (salt or "").strip(),
                    "category": (cat or "").strip(),
                    "your_stock": int(your_qty),
                    "in_stock": int(your_qty) > 0,
                    "cost": int(cost or 0),
                    "other_branches": other.get(ac, []),
                })
            in_stock_n = sum(1 for r in results if r["in_stock"])
            return {
                "ok": True, "site": site_code or "all", "query": query,
                "count": len(results), "in_stock_count": in_stock_n,
                "results": results,
            }
        finally:
            c.close()
    except Exception as e:
        log.warning(f"stock_check failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
