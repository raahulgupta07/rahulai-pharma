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
    try:
        c, cur = _conn()
        try:
            # matched articles (by brand OR salt), with this key's owned stock
            if _locked:
                # Tier-1 stock = SUM across ALL owned outlets (ANY of the set).
                cur.execute(
                    f"""SELECT a.article_code, a.brand_name, a.generic_name, a.category,
                               COALESCE(SUM(b.stock_qty),0) AS your_qty,
                               COALESCE(MAX(b.weighted_cost_price),0) AS cost
                        FROM {ART} a
                        LEFT JOIN {STOCK} b
                          ON b.article_code = a.article_code::text AND b.site_code = ANY(%s)
                        WHERE a.brand_name ILIKE %s OR a.generic_name ILIKE %s
                        GROUP BY 1,2,3,4
                        ORDER BY your_qty DESC, a.brand_name
                        LIMIT %s""",
                    (allowed, q, q, int(limit)))
            elif site_code:
                cur.execute(
                    f"""SELECT a.article_code, a.brand_name, a.generic_name, a.category,
                               COALESCE(b.stock_qty,0) AS your_qty, COALESCE(b.weighted_cost_price,0) AS cost
                        FROM {ART} a
                        LEFT JOIN {STOCK} b
                          ON b.article_code = a.article_code::text AND b.site_code = %s
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
                        LEFT JOIN {STOCK} b ON b.article_code = a.article_code::text
                        WHERE a.brand_name ILIKE %s OR a.generic_name ILIKE %s
                        GROUP BY 1,2,3,4
                        ORDER BY your_qty DESC, a.brand_name
                        LIMIT %s""",
                    (q, q, int(limit)))
            rows = cur.fetchall()
            if not rows:
                return {"ok": True, "site": _site_label, "query": query, "count": 0, "results": []}

            codes = [int(r[0]) for r in rows]
            # P3 — linkage guard: if the matched catalog codes don't appear in the
            # stock table AT ALL, the article_code join is broken (e.g. stock exported
            # as scientific notation). Report "can't link" — never a false "out of stock".
            if codes:
                # STOCK.article_code is TEXT (source CSV scientific-notation safety);
                # ART.article_code is bigint. Quote literals so IN matches the text col.
                _ph0 = ",".join("'" + str(x) + "'" for x in codes)
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {STOCK} WHERE article_code IN ({_ph0})")
                    _linked = int(cur.fetchone()[0] or 0)
                except Exception:
                    _linked = 1  # fail-open: assume linkable
                if _linked == 0:
                    return {
                        "ok": True, "site": _site_label, "query": query,
                        "count": len(rows), "stock_linkable": False,
                        # USER-FACING guidance — short, no internal/file/Excel details.
                        "linkage_warning": (
                            "Live stock for these products is temporarily unavailable for this "
                            "branch (inventory sync issue). Reply in ONE short, friendly sentence: "
                            "you can't confirm the exact quantity right now — ask the user to try "
                            "again shortly or have staff refresh the branch inventory. Do NOT say "
                            "'out of stock'. Do NOT mention file names, Excel, article codes, "
                            "scientific notation, re-uploading, or any technical/data details."),
                        # ADMIN/LOG detail — root cause + fix, NOT for the end user.
                        "admin_note": (
                            "Stock article_code corrupted by Excel scientific-notation ('1E+12') — "
                            "catalog↔stock join returns 0. Fix: re-upload balance_stock with "
                            "article_code as TEXT, or export CSV from source without opening in Excel."),
                        "results": [{
                            "brand": (r[1] or "").strip(),
                            "salt": (r[2] or "").strip(),
                            "category": (r[3] or "").strip(),
                            "stock": "temporarily unavailable",
                        } for r in rows],
                    }
            # other branches holding each matched article (top few)
            other = {}      # Tier-2 / availability hint for un-owned outlets
            owned_bd = {}   # per-owned-outlet breakdown (Tier-1), multi-outlet keys
            if codes:
                ph = ",".join("'" + str(x) + "'" for x in codes)  # text literals (STOCK.article_code is text)
                if _locked:
                    # Tier-1 per-outlet breakdown across the owned set
                    cur.execute(
                        f"""SELECT article_code, site_code, stock_qty FROM {STOCK}
                            WHERE article_code IN ({ph}) AND site_code = ANY(%s) AND stock_qty > 0
                            ORDER BY stock_qty DESC""", (allowed,))
                    for ac, sc, qty in cur.fetchall():
                        owned_bd.setdefault(int(ac), [])
                        if len(owned_bd[int(ac)]) < 8:
                            owned_bd[int(ac)].append({"site": sc, "qty": int(qty)})
                    # Tier-2: outlets NOT in the owned set = availability only
                    cur.execute(
                        f"""SELECT article_code, site_code, stock_qty FROM {STOCK}
                            WHERE article_code IN ({ph}) AND NOT (site_code = ANY(%s)) AND stock_qty > 0
                            ORDER BY stock_qty DESC""", (allowed,))
                    for ac, sc, qty in cur.fetchall():
                        other.setdefault(int(ac), [])
                        if len(other[int(ac)]) < 4:
                            other[int(ac)].append({"site": sc, "available": True})
                elif site_code:
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
                _row = {
                    "article_code": ac,
                    "brand": (brand or "").strip(),
                    "salt": (salt or "").strip(),
                    "category": (cat or "").strip(),
                    "your_stock": int(your_qty),
                    "in_stock": int(your_qty) > 0,
                    "cost": int(cost or 0),
                    "other_branches": other.get(ac, []),
                }
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
    try:
        c, cur = _conn()
        try:
            # WHERE fragment: forced owned-store SET (locked) + optional category filter.
            where = []
            params: list = []
            if allowed:
                where.append("b.site_code = ANY(%s)")
                params.append(allowed)
            if category and category.strip():
                where.append("a.category ILIKE %s")
                params.append(f"%{category.strip()}%")
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""

            # ── low-stock list ──────────────────────────────────────────────
            if low_stock_threshold and int(low_stock_threshold) > 0:
                thr = int(low_stock_threshold)
                cur.execute(
                    f"""SELECT a.article_code, a.brand_name, a.generic_name,
                               a.category, b.stock_qty
                        FROM {STOCK} b
                        JOIN {ART} a ON a.article_code::text = b.article_code
                        {where_sql}{' AND' if where_sql else 'WHERE'}
                              b.stock_qty > 0 AND b.stock_qty <= %s
                        ORDER BY b.stock_qty ASC, a.brand_name
                        LIMIT %s""",
                    (*params, thr, int(limit)))
                low = [
                    {
                        "article_code": int(r[0]),
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
                    f"""SELECT a.category,
                               COALESCE(SUM(b.stock_qty),0) AS total_qty,
                               COUNT(DISTINCT a.article_code) AS articles
                        FROM {STOCK} b
                        LEFT JOIN {ART} a ON a.article_code::text = b.article_code
                        {where_sql}
                        GROUP BY a.category
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
                f"""SELECT COALESCE(SUM(b.stock_qty),0) AS total_qty,
                           COUNT(DISTINCT a.article_code) AS articles,
                           COALESCE(SUM(b.stock_qty * b.weighted_cost_price),0) AS inv_value
                    FROM {STOCK} b
                    -- LEFT JOIN: stock_qty lives in {STOCK} alone; the article
                    -- join only enriches unique_articles. An INNER join here
                    -- drops EVERY stock row when article_code is corrupt
                    -- (1E+12 scientific-notation text ≠ bigint::text) → total 0.
                    LEFT JOIN {ART} a ON a.article_code::text = b.article_code
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
