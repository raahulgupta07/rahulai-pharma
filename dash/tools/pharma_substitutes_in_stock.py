"""Substitutes that are IN STOCK — "alternative to <brand>, and where can I get it".

The killer counter question: a brand is out, what else (same molecule) can I sell
RIGHT NOW, and which shop has it. Today the agent must chain find_substitutes →
stock_check → find_nearby_stock across 3 tool calls and stitch the result (and can
drop a hop). This tool does the whole chain server-side, deterministically, over
the denormalized `citypharma.shop_flat` (one row per article×store).

Store-scope preserved 1:1 with the other pharma tools: when the key is store-
locked the outlet is FORCED to the bound store set (own qty Tier-1), other
branches return availability only (no qty), mask_row belt-and-suspenders. Disable
with PHARMA_GRAPH_DISABLED=1.
"""
from __future__ import annotations

import os
import logging

from dash.api_scope import is_store_locked, bound_stores, mask_row

log = logging.getLogger("dash.substitutes_in_stock")

SCHEMA = "citypharma"
FLAT = f'"{SCHEMA}"."shop_flat"'


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


def _ac(art_key):
    try:
        return int(art_key)
    except (TypeError, ValueError):
        return art_key


def substitutes_in_stock(drug: str = "", outlet: str = "", limit: int = 20) -> dict:
    """Substitutes (same generic molecule) for a drug that are IN STOCK, with where.

    drug: brand name or salt of the (possibly out-of-stock) medicine.
    outlet: the staff branch (from SHOP CONTEXT) — own stock reported for this
            branch first. Ignored for store-locked keys (forced to the bound set).
    Returns each substitute brand with your-branch stock, in-stock flag, and which
    OTHER shops hold it (display labels). Use for 'alternative to X in stock',
    'X is out — what else can I sell and where', 'substitute for X nearby'.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "shop tools disabled"}
    if not drug or not drug.strip():
        return {"ok": False, "error": "provide a medicine name or salt"}

    q = f"%{drug.strip()}%"
    _locked = is_store_locked()
    owned = bound_stores() if _locked else []
    my_sites = owned if _locked else ([outlet] if outlet else [])
    my_label = ",".join(owned) if _locked else (outlet or "all")

    try:
        from dash.tools.shop_labels import shop_label
        c, cur = _conn()
        try:
            # 1) generic molecule(s) of the queried drug
            cur.execute(
                f"""SELECT DISTINCT generic FROM {FLAT}
                    WHERE (brand ILIKE %s OR generic ILIKE %s)
                          AND generic IS NOT NULL AND generic <> ''""",
                (q, q))
            generics = [r[0] for r in cur.fetchall() if r[0]]
            if not generics:
                from dash.tools.pharma_resolve import MSG_NOT_FOUND
                return {"ok": True, "drug": drug, "outlet": my_label,
                        "count": 0, "substitutes": [],
                        "state": "not_found", "message": MSG_NOT_FOUND}

            # 2) substitute articles = same generic, EXCLUDING the queried brand
            cur.execute(
                f"""SELECT art_key, MAX(brand) AS brand, MAX(generic) AS generic
                    FROM {FLAT}
                    WHERE generic = ANY(%s) AND NOT (brand ILIKE %s)
                    GROUP BY art_key""",
                (generics, q))
            subs = {r[0]: {"brand": (r[1] or "").strip(),
                           "salt": (r[2] or "").strip()} for r in cur.fetchall()}
            if not subs:
                from dash.tools.pharma_resolve import MSG_NOT_FOUND
                return {"ok": True, "drug": drug, "outlet": my_label,
                        "generic": generics[0], "count": 0, "substitutes": [],
                        "state": "not_found",
                        "message": "No other brand shares this molecule."}
            art_keys = list(subs.keys())

            # 3) your-branch stock per substitute (SUM across owned sites)
            your_qty = {ak: 0 for ak in art_keys}
            if my_sites:
                cur.execute(
                    f"""SELECT art_key, COALESCE(SUM(stock_qty),0)
                        FROM {FLAT}
                        WHERE art_key = ANY(%s) AND site_code = ANY(%s)
                        GROUP BY art_key""",
                    (art_keys, my_sites))
                for ak, qty in cur.fetchall():
                    your_qty[ak] = int(qty or 0)

            # 4) other branches holding each substitute (qty>0), top 5
            other: dict = {}
            if my_sites:
                cur.execute(
                    f"""SELECT art_key, site_code, stock_qty FROM {FLAT}
                        WHERE art_key = ANY(%s) AND NOT (site_code = ANY(%s))
                              AND stock_qty > 0
                        ORDER BY stock_qty DESC""",
                    (art_keys, my_sites))
            else:
                cur.execute(
                    f"""SELECT art_key, site_code, stock_qty FROM {FLAT}
                        WHERE art_key = ANY(%s) AND stock_qty > 0
                        ORDER BY stock_qty DESC""",
                    (art_keys,))
            for ak, sc, qty in cur.fetchall():
                other.setdefault(ak, [])
                if len(other[ak]) < 5:
                    if _locked:
                        other[ak].append({"site": sc, "shop": shop_label(sc),
                                          "available": True})
                    else:
                        other[ak].append({"site": sc, "shop": shop_label(sc),
                                          "qty": int(qty)})

            # 5) assemble — only substitutes available SOMEWHERE (your branch or
            #    another shop), ranked by your-branch stock then reach.
            results = []
            for ak in art_keys:
                yq = int(your_qty.get(ak, 0))
                others = other.get(ak, [])
                if yq == 0 and not others:
                    continue  # truly unavailable substitute — drop
                _row = {
                    "article_code": _ac(ak),
                    "brand": subs[ak]["brand"],
                    "salt": subs[ak]["salt"],
                    "your_stock": yq,
                    "in_stock": yq > 0,
                    "other_branches": others,
                }
                mask_row(_row, "" if _locked else outlet)
                results.append(_row)
            results.sort(key=lambda r: (-(r.get("your_stock") or 0),
                                        -len(r.get("other_branches") or [])))

            return {
                "ok": True, "drug": drug, "outlet": my_label,
                "generic": generics[0],
                "count": len(results),
                "in_stock_here": sum(1 for r in results if r.get("in_stock")),
                "substitutes": results[:int(limit)],
            }
        finally:
            c.close()
    except Exception as e:
        log.warning(f"substitutes_in_stock failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
