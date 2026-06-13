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

from dash.api_scope import is_store_locked, owns, bound_store, bound_stores, mask_row

log = logging.getLogger("dash.pharma_shop")

SCHEMA = "citypharma"

# resolved lazily by _resolve_tables() on first connection — data lands as
# *_07052026, the old citypharma_articles/balance_stock names are gone.
ART: str | None = None
STOCK: str | None = None


def _resolve_tables(cur):
    """Point ART/STOCK at the CURRENT uploaded tables (latest upload wins).

    Re-resolves on every connection via the shared TTL-cached resolver so a new
    data upload is picked up within seconds — no process restart. (Old bug: the
    module globals were cached for the whole process and the pick was unordered,
    so a re-upload was never seen.)
    """
    global ART, STOCK
    from dash.tools.table_sync import latest_table, STOCK_COLS, CATALOG_COLS
    art = latest_table(cur, SCHEMA, CATALOG_COLS)
    stock = latest_table(cur, SCHEMA, STOCK_COLS)
    ART = f'"{SCHEMA}"."{art}"' if art else f'"{SCHEMA}"."articles_list_07052026"'
    STOCK = f'"{SCHEMA}"."{stock}"' if stock else f'"{SCHEMA}"."balance_stock_07052026"'


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
    _resolve_tables(cur)
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
    # Store-scope (API gateway): when a key is bound to one store, force the
    # queried branch to the bound store so "your_stock"/cost are Tier-1 own-branch
    # only. Other branches (Tier 2) return existence, never qty/cost. Human UI
    # (no scope enforced) is unaffected.
    _locked = is_store_locked()
    # Owned-store SET (multi-outlet keys). When locked we ALWAYS restrict to this
    # set and ignore any caller-supplied site_code.
    allowed = bound_stores() if _locked else []
    _site_label = ",".join(allowed) if _locked else (site_code or "all")
    q = f"%{query.strip()}%"
    FLAT = f'"{SCHEMA}".shop_flat'
    try:
        c, cur = _conn()
        try:
            # matched medicines (by brand OR generic), with this key's owned stock.
            # Reads the denormalized citypharma.shop_flat (one row per article×store;
            # brand/generic/category folded in from the catalog) — NO runtime join,
            # NO article_code::text cast, no 1E+12 linkage guard needed.
            if _locked:
                # Tier-1 stock = SUM across ALL owned outlets (ANY of the set).
                cur.execute(
                    f"""SELECT art_key,
                               MAX(brand)        AS brand,
                               MAX(generic)      AS generic,
                               MAX(category)     AS category,
                               COALESCE(SUM(stock_qty) FILTER (WHERE site_code = ANY(%s)),0) AS your_qty,
                               COALESCE(MAX(cost)  FILTER (WHERE site_code = ANY(%s)),0)      AS cost
                        FROM {FLAT}
                        WHERE brand ILIKE %s OR generic ILIKE %s
                        GROUP BY art_key
                        ORDER BY your_qty DESC, brand
                        LIMIT %s""",
                    (allowed, allowed, q, q, int(limit)))
            elif site_code:
                cur.execute(
                    f"""SELECT art_key,
                               MAX(brand)    AS brand,
                               MAX(generic)  AS generic,
                               MAX(category) AS category,
                               COALESCE(SUM(stock_qty) FILTER (WHERE site_code = %s),0) AS your_qty,
                               COALESCE(MAX(cost)  FILTER (WHERE site_code = %s),0)      AS cost
                        FROM {FLAT}
                        WHERE brand ILIKE %s OR generic ILIKE %s
                        GROUP BY art_key
                        ORDER BY (COALESCE(SUM(stock_qty) FILTER (WHERE site_code = %s),0) > 0) DESC,
                                 your_qty DESC, brand
                        LIMIT %s""",
                    (site_code, site_code, q, q, site_code, int(limit)))
            else:
                cur.execute(
                    f"""SELECT art_key,
                               MAX(brand)    AS brand,
                               MAX(generic)  AS generic,
                               MAX(category) AS category,
                               COALESCE(SUM(stock_qty),0) AS your_qty,
                               COALESCE(MAX(cost),0)      AS cost
                        FROM {FLAT}
                        WHERE brand ILIKE %s OR generic ILIKE %s
                        GROUP BY art_key
                        ORDER BY your_qty DESC, brand
                        LIMIT %s""",
                    (q, q, int(limit)))
            rows = cur.fetchall()
            if not rows:
                # No stock-bearing match. Distinguish the 3 states via the shared
                # resolver: catalog_only (in catalog, 0 stock — the COMMON case,
                # NOT "out of stock everywhere"), stock_only, or genuine not_found.
                from dash.tools.pharma_resolve import resolve_article
                res = resolve_article(cur, SCHEMA, query,
                                      sites=(allowed or None))
                base = {"ok": True, "site": _site_label, "query": query,
                        "count": 0, "results": []}
                if res["state"] == "catalog_only":
                    base.update({"state": "catalog_only", "in_catalog": True,
                                 "in_stock_count": 0,
                                 "message": res["message"],
                                 "article_codes": res["art_keys"]})
                elif res["state"] == "stock_only":
                    base.update({"state": "stock_only",
                                 "message": res["message"],
                                 "article_codes": res["art_keys"]})
                else:
                    base.update({"state": "not_found",
                                 "message": res["message"]})
                return base

            # art_key is text; keep both the raw key (for the shop_flat lookups below)
            # and the int form (downstream find_substitutes expects an int code).
            art_keys = [r[0] for r in rows]

            def _ac(art_key):
                """int(art_key) when numeric, else the raw string."""
                try:
                    return int(art_key)
                except (TypeError, ValueError):
                    return art_key

            # other branches holding each matched article (top few), from shop_flat
            other = {}      # Tier-2 / availability hint for un-owned outlets
            owned_bd = {}   # per-owned-outlet breakdown (Tier-1), multi-outlet keys
            if art_keys:
                if _locked:
                    # Tier-1 per-outlet breakdown across the owned set
                    cur.execute(
                        f"""SELECT art_key, site_code, stock_qty FROM {FLAT}
                            WHERE art_key = ANY(%s) AND site_code = ANY(%s) AND stock_qty > 0
                            ORDER BY stock_qty DESC""", (art_keys, allowed))
                    for ak, sc, qty in cur.fetchall():
                        k = _ac(ak)
                        owned_bd.setdefault(k, [])
                        if len(owned_bd[k]) < 8:
                            owned_bd[k].append({"site": sc, "qty": int(qty)})
                    # Tier-2: outlets NOT in the owned set = availability only
                    cur.execute(
                        f"""SELECT art_key, site_code, stock_qty FROM {FLAT}
                            WHERE art_key = ANY(%s) AND NOT (site_code = ANY(%s)) AND stock_qty > 0
                            ORDER BY stock_qty DESC""", (art_keys, allowed))
                    for ak, sc, qty in cur.fetchall():
                        k = _ac(ak)
                        other.setdefault(k, [])
                        if len(other[k]) < 4:
                            other[k].append({"site": sc, "available": True})
                elif site_code:
                    cur.execute(
                        f"""SELECT art_key, site_code, stock_qty FROM {FLAT}
                            WHERE art_key = ANY(%s) AND site_code <> %s AND stock_qty > 0
                            ORDER BY stock_qty DESC""", (art_keys, site_code))
                    for ak, sc, qty in cur.fetchall():
                        k = _ac(ak)
                        other.setdefault(k, [])
                        if len(other[k]) < 4:
                            other[k].append({"site": sc, "qty": int(qty)})

            # link_status per matched art_key — so a catalog_only item (in catalog,
            # 0 stock chain-wide) is framed honestly, not as "out of stock". NULL/
            # missing (pre-build rows) → 'both' (fail-soft). Whole-chain catalog
            # presence, so this is NOT site-scoped.
            link_status = {}
            if art_keys:
                cur.execute(
                    f"""SELECT art_key,
                               MAX(COALESCE(NULLIF(link_status, ''), 'both')) AS ls
                        FROM {FLAT}
                        WHERE art_key = ANY(%s)
                        GROUP BY art_key""", (art_keys,))
                for ak, ls in cur.fetchall():
                    link_status[_ac(ak)] = ls

            results = []
            for art_key, brand, salt, cat, your_qty, cost in rows:
                ac = _ac(art_key)
                _ls = link_status.get(ac, "both")
                _catalog_only = (_ls == "catalog_only")
                _row = {
                    "article_code": ac,
                    "brand": (brand or "").strip(),
                    "salt": (salt or "").strip(),
                    "category": (cat or "").strip(),
                    "your_stock": int(your_qty),
                    "in_stock": int(your_qty) > 0,
                    "cost": int(cost or 0),
                    "other_branches": other.get(ac, []),
                    "in_catalog": True,
                }
                if _catalog_only:
                    # In the catalog but not stocked anywhere — honest framing.
                    from dash.tools.pharma_resolve import MSG_CATALOG_ONLY
                    _row["state"] = "catalog_only"
                    _row["note"] = MSG_CATALOG_ONLY
                if _locked:
                    # per-owned-outlet split (Tier-1) for multi-outlet keys
                    _row["your_stores"] = owned_bd.get(ac, [])
                # Belt-and-suspenders: your_stock/cost are owned-set Tier-1 data, so
                # this is a no-op when locked (owns("") is True). Single-site / global
                # paths still mask any cross-branch leakage.
                mask_row(_row, "" if _locked else site_code)
                results.append(_row)
            in_stock_n = sum(1 for r in results if r.get("in_stock"))
            return {
                "ok": True, "site": _site_label, "query": query,
                "count": len(results), "in_stock_count": in_stock_n,
                "results": results,
            }
        finally:
            c.close()
    except Exception as e:
        log.warning(f"stock_check failed: {e}")
        return {"ok": False, "error": str(e)[:300]}


def store_stock_summary(
    group_by: str = "",
    category: str = "",
    low_stock_threshold: int = 0,
    limit: int = 30,
) -> dict:
    """Own-branch aggregate analytics: totals, per-category breakdown, low-stock list.

    SAFE for store-locked API keys — when a key is bound to one store, the queried
    site is FORCED to the bound store (any caller-supplied site is ignored), so a
    store key can NEVER aggregate another branch. The bound site is passed as a
    parameterized `WHERE b.site_code = %s` on every query.

    group_by: "" → overall totals (total_stock_qty, unique_articles,
                   total_inventory_value) for the branch.
              "category" → per-category SUM(stock_qty) + article COUNT, top `limit`.
    category: filter to one category (ILIKE) — returns that category's totals.
    low_stock_threshold: >0 → list articles with 0 < stock_qty <= threshold
                         (brand + qty), up to `limit`.
    limit: cap on rows returned for the grouped / low-stock lists.

    Cost/price columns (total_inventory_value) are Tier-1 own-branch data, allowed.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "shop tools disabled"}
    # Store-scope (API gateway): force the bound site so a store key can only ever
    # aggregate ITS OWN branch. Global / human-UI keys (not locked) may aggregate
    # all stores (site filter omitted) or the caller-supplied `category`.
    _locked = is_store_locked()
    allowed = bound_stores() if _locked else []
    site_code = ",".join(allowed) if _locked else ""  # display label only
    FLAT = f'"{SCHEMA}".shop_flat'
    try:
        c, cur = _conn()
        try:
            # WHERE fragment: forced owned-store SET (locked) + optional category
            # filter. Reads the denormalized citypharma.shop_flat (brand/generic/
            # category folded in) — NO runtime join, NO article_code::text cast.
            where = []
            params: list = []
            if allowed:
                where.append("site_code = ANY(%s)")
                params.append(allowed)
            if category and category.strip():
                where.append("category ILIKE %s")
                params.append(f"%{category.strip()}%")
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""

            # ── low-stock list ──────────────────────────────────────────────
            if low_stock_threshold and int(low_stock_threshold) > 0:
                thr = int(low_stock_threshold)
                cur.execute(
                    f"""SELECT art_key, brand, generic, category, stock_qty
                        FROM {FLAT}
                        {where_sql}{' AND' if where_sql else 'WHERE'}
                              stock_qty > 0 AND stock_qty <= %s
                        ORDER BY stock_qty ASC, brand
                        LIMIT %s""",
                    (*params, thr, int(limit)))
                def _ac(art_key):
                    try:
                        return int(art_key)
                    except (TypeError, ValueError):
                        return art_key
                low = [
                    {
                        "article_code": _ac(r[0]),
                        "brand": (r[1] or "").strip(),
                        "salt": (r[2] or "").strip(),
                        "category": (r[3] or "").strip(),
                        "stock_qty": int(r[4]),
                    }
                    for r in cur.fetchall()
                ]
                return {
                    "ok": True, "site": site_code or "all",
                    "low_stock_threshold": thr, "count": len(low),
                    "low_stock": low,
                }

            # ── per-category breakdown ──────────────────────────────────────
            if group_by and group_by.strip().lower() == "category":
                cur.execute(
                    f"""SELECT category,
                               COALESCE(SUM(stock_qty),0) AS total_qty,
                               COUNT(DISTINCT art_key) AS articles
                        FROM {FLAT}
                        {where_sql}
                        GROUP BY category
                        ORDER BY total_qty DESC
                        LIMIT %s""",
                    (*params, int(limit)))
                cats = [
                    {
                        "category": (r[0] or "").strip(),
                        "total_stock_qty": int(r[1]),
                        "unique_articles": int(r[2]),
                    }
                    for r in cur.fetchall()
                ]
                return {
                    "ok": True, "site": site_code or "all",
                    "group_by": "category", "count": len(cats),
                    "categories": cats,
                }

            # ── overall totals (default) ────────────────────────────────────
            cur.execute(
                f"""SELECT COALESCE(SUM(stock_qty),0) AS total_qty,
                           COUNT(DISTINCT art_key) AS articles,
                           COALESCE(SUM(stock_qty * cost),0) AS inv_value
                    FROM {FLAT}
                    {where_sql}""",
                tuple(params))
            row = cur.fetchone() or (0, 0, 0)
            return {
                "ok": True, "site": site_code or "all",
                "category": category.strip() if category else None,
                "total_stock_qty": int(row[0] or 0),
                "unique_articles": int(row[1] or 0),
                "total_inventory_value": round(float(row[2] or 0), 2),
            }
        finally:
            c.close()
    except Exception as e:
        log.warning(f"store_stock_summary failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
