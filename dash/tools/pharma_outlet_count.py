"""Outlet coverage for a medicine — "how many shops have X / where is X stocked".

Counter staff and owners ask "how many outlets have Paracetamol", "which shops
carry X". This answers with the COUNT of distinct outlets holding the medicine in
stock plus the shop list (display labels "Shop 1, 2, …"). Reads the denormalized
`citypharma.shop_flat` (no runtime join).

Count + presence are NON-SENSITIVE (no quantity, no cost) → safe for store-locked
keys too: a store key still learns how widely a drug is stocked across the chain,
never another branch's exact qty. Disable with PHARMA_GRAPH_DISABLED=1.
"""
from __future__ import annotations

import os
import logging

log = logging.getLogger("dash.pharma_outlet_count")

SCHEMA = "citypharma"
FLAT = f'"{SCHEMA}"."shop_flat"'


def _conn():
    from dash.tools._direct_db import direct_connect
    c = direct_connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8,
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("SET statement_timeout = '15s';")
    cur.execute(f'SET search_path = "{SCHEMA}", public;')
    return c, cur


def outlets_carrying(query: str = "", limit: int = 60) -> dict:
    """How many outlets stock a medicine, and which ones.

    query: brand name or salt (generic), e.g. 'paracetamol', 'panadol'.
    Returns: total distinct outlets in the chain, how many currently hold it in
    stock, the per-medicine outlet count, and the shop list (display labels).
    Use for 'how many shops have X', 'which outlets carry X', 'how widely is X
    stocked'. NON-SENSITIVE — count + presence only, never quantity.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "shop tools disabled"}
    if not query or not query.strip():
        return {"ok": False, "error": "provide a medicine name or salt"}
    q = f"%{query.strip()}%"
    try:
        from dash.tools.shop_labels import shop_label
        c, cur = _conn()
        try:
            # total live outlets in the chain (denominator)
            cur.execute(
                f"SELECT COUNT(DISTINCT site_code) FROM {FLAT} "
                f"WHERE site_code IS NOT NULL AND site_code <> ''")
            total_outlets = int((cur.fetchone() or [0])[0] or 0)

            # outlets holding the matched medicine IN STOCK (qty > 0)
            cur.execute(
                f"""SELECT site_code, COUNT(DISTINCT art_key) AS skus
                    FROM {FLAT}
                    WHERE (brand ILIKE %s OR generic ILIKE %s) AND stock_qty > 0
                    GROUP BY site_code
                    ORDER BY skus DESC, site_code
                    LIMIT %s""",
                (q, q, int(limit)))
            rows = cur.fetchall()
            if not rows:
                # Is it even in the catalog? Distinguish "stocked nowhere" from
                # "not a product we carry".
                cur.execute(
                    f"SELECT COUNT(*) FROM {FLAT} WHERE brand ILIKE %s OR generic ILIKE %s",
                    (q, q))
                in_catalog = int((cur.fetchone() or [0])[0] or 0) > 0
                return {
                    "ok": True, "query": query,
                    "total_outlets": total_outlets,
                    "outlets_with_stock": 0,
                    "in_catalog": in_catalog,
                    "outlets": [],
                    "message": ("In the catalog but out of stock at every outlet."
                                if in_catalog else
                                "Not found in the catalog under that name."),
                }
            outlets = [
                {"site": sc, "shop": shop_label(sc), "skus_in_stock": int(skus)}
                for sc, skus in rows
            ]
            return {
                "ok": True, "query": query,
                "total_outlets": total_outlets,
                "outlets_with_stock": len(outlets),
                "in_catalog": True,
                "outlets": outlets,
            }
        finally:
            c.close()
    except Exception as e:
        log.warning(f"outlets_carrying failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
