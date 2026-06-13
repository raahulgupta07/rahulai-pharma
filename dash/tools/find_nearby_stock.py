"""Cross-store medicine locator for the CityPharma Analyst.

Counter staff ask: "where can I find X", "which branch has X", "X is out/low at
my store — who has it", "transfer X". This tool reads the DENORMALIZED
`citypharma.shop_flat` table (one row per article+store, NO runtime join) and
returns: the medicine's stock at YOUR branch, whether it's low, and the OTHER
branches that hold it ranked by quantity (most stock first).

Own read-only direct connection to cp-db (service 'dash-db'), same pattern as
pharma_shop_tool. Disable with PHARMA_GRAPH_DISABLED=1.

Tiering: a store-locked API key NEVER sees another branch's exact quantity — for
un-owned branches we return availability only ({"site":..., "available": True}).
Un-locked (human UI / global key) callers get the quantity.
"""
from __future__ import annotations

import os
import logging

from dash.api_scope import is_store_locked, bound_stores, mask_row

log = logging.getLogger("dash.find_nearby_stock")

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
    cur.execute("SET statement_timeout = '20s';")
    cur.execute(f'SET search_path = "{SCHEMA}", public;')
    return c, cur


def _to_article_code(art_key):
    """art_key is text; return int() when numeric, else the raw string."""
    try:
        return int(art_key)
    except (TypeError, ValueError):
        return art_key


def find_nearby_stock(query: str = "", my_store: str = "", low_threshold: int = 5) -> dict:
    """Find which OTHER branches have a medicine when it's out or low at your branch. query=brand or salt; my_store=the staff branch (from SHOP CONTEXT). Returns your own qty, whether it's low, and other branches that hold it ranked by quantity (most stock first)."""
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "shop tools disabled"}
    if not query or not query.strip():
        return {"ok": False, "error": "provide a medicine name or salt"}

    q = "%" + query.strip() + "%"
    # Scope: when locked, the staff branch set = owned outlets (ignore my_store arg);
    # when not locked, use the caller-supplied my_store.
    _locked = is_store_locked()
    owned = bound_stores() if _locked else []
    my_sites = owned if _locked else ([my_store] if my_store else [])
    my_label = ",".join(owned) if _locked else (my_store or "all")

    try:
        c, cur = _conn()
        try:
            # 1) matched articles in shop_flat (brand OR generic), distinct art_keys + labels
            cur.execute(
                f"""SELECT art_key,
                           MAX(brand)   AS brand,
                           MAX(generic) AS generic
                    FROM {FLAT}
                    WHERE brand ILIKE %s OR generic ILIKE %s
                    GROUP BY art_key""",
                (q, q))
            arts = cur.fetchall()
            if not arts:
                # Nothing matched by name. Use the shared 3-state resolver to tell
                # a genuine not_found from a stock_only code (NULL brand → name
                # ILIKE can't reach it).
                from dash.tools.pharma_resolve import resolve_article
                res = resolve_article(cur, SCHEMA, query)
                if res["state"] == "stock_only":
                    return {"ok": True, "query": query, "my_branch": my_label,
                            "results": [], "count": 0, "state": "stock_only",
                            "message": res["message"],
                            "article_codes": res["art_keys"]}
                return {"ok": True, "query": query, "my_branch": my_label,
                        "results": [], "count": 0, "state": "not_found",
                        "message": res["message"]}

            labels = {a[0]: {"brand": (a[1] or "").strip(), "salt": (a[2] or "").strip()}
                      for a in arts}
            art_keys = list(labels.keys())

            # link_status per matched art_key — a catalog_only item is in the
            # catalog but stocked at NO branch (honest framing, NOT "out of stock
            # everywhere"). NULL/missing → 'both' (fail-soft pre-build).
            link_status = {}
            cur.execute(
                f"""SELECT art_key,
                           MAX(COALESCE(NULLIF(link_status, ''), 'both')) AS ls
                    FROM {FLAT}
                    WHERE art_key = ANY(%s)
                    GROUP BY art_key""", (art_keys,))
            for ak, ls in cur.fetchall():
                link_status[ak] = ls

            # 2) YOUR stock = SUM(stock_qty) per art_key at your branch(es)
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

            # 3) OTHER branches: sites NOT in your branch(es), stock_qty>0,
            #    ranked stock_qty DESC, top 5 per article.
            other: dict = {}
            if my_sites:
                cur.execute(
                    f"""SELECT art_key, site_code, stock_qty
                        FROM {FLAT}
                        WHERE art_key = ANY(%s) AND NOT (site_code = ANY(%s))
                              AND stock_qty > 0
                        ORDER BY stock_qty DESC""",
                    (art_keys, my_sites))
            else:
                cur.execute(
                    f"""SELECT art_key, site_code, stock_qty
                        FROM {FLAT}
                        WHERE art_key = ANY(%s) AND stock_qty > 0
                        ORDER BY stock_qty DESC""",
                    (art_keys,))
            from dash.tools.shop_labels import shop_label
            for ak, sc, qty in cur.fetchall():
                other.setdefault(ak, [])
                if len(other[ak]) < 5:
                    if _locked:
                        other[ak].append({"site": sc, "shop": shop_label(sc),
                                          "available": True})
                    else:
                        other[ak].append({"site": sc, "shop": shop_label(sc),
                                          "qty": int(qty)})

            # 4) assemble
            results = []
            for ak in art_keys:
                yq = int(your_qty.get(ak, 0))
                _ls = link_status.get(ak, "both")
                _row = {
                    "brand": labels[ak]["brand"],
                    "salt": labels[ak]["salt"],
                    "article_code": _to_article_code(ak),
                    "your_stock": yq,
                    "is_low": yq <= int(low_threshold),
                    "in_stock": yq > 0,
                    "other_branches": other.get(ak, []),
                    "in_catalog": True,
                }
                if _ls == "catalog_only" and not _row["other_branches"] and yq == 0:
                    # In the catalog but not stocked at any branch — honest.
                    _row["state"] = "catalog_only"
                    _row["note"] = "In catalog, not stocked at any branch."
                # belt-and-suspenders, same as pharma_shop_tool
                mask_row(_row, "" if _locked else my_store)
                results.append(_row)

            return {
                "ok": True, "query": query, "my_branch": my_label,
                "results": results, "count": len(results),
            }
        finally:
            c.close()
    except Exception as e:
        log.warning(f"find_nearby_stock failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
