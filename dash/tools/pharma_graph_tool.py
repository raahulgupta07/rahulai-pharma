"""Pharma drug-relationship tools for the CityPharma Analyst — RELATIONAL.

Originally backed by an Apache AGE graph 'citypharma'. AGE is GONE (cp-db was
recreated without the baked-AGE image — durability landmine), so these are now
pure relational over the catalog (drugs sharing generic_name = substitutes;
indication ILIKE = therapeutic alternatives). Survives any cp-db recreate, no
AGE dependency. Table names auto-detected (data lands as *_07052026).

Store-scope masking preserved: when a key is store-locked, stock is forced to
the bound store (Tier-1 own-branch only) + mask_row belt-and-suspenders.

Own read-only direct connection to cp-db (service 'dash-db'). Disable with
PHARMA_GRAPH_DISABLED=1.
"""
from __future__ import annotations

import os
import logging

from dash.api_scope import is_store_locked, bound_store, mask_row

log = logging.getLogger("dash.pharma_graph")

SCHEMA = "citypharma"

# resolved lazily by _resolve_tables() on first connection
ART: str | None = None
STOCK: str | None = None


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


def _resolve_tables(cur):
    """Point ART/STOCK at the CURRENT uploaded tables (latest upload wins).

    Shared resolver (TTL-cached, ordered by dash_table_metadata.updated_at) so a
    new upload is picked up within seconds without a process restart.
    """
    global ART, STOCK
    from dash.tools.table_sync import latest_table, STOCK_COLS, CATALOG_COLS
    art = latest_table(cur, SCHEMA, CATALOG_COLS)
    stock = latest_table(cur, SCHEMA, STOCK_COLS)
    ART = f'"{SCHEMA}"."{art}"' if art else f'"{SCHEMA}"."articles_list_07052026"'
    STOCK = f'"{SCHEMA}"."{stock}"' if stock else f'"{SCHEMA}"."balance_stock_07052026"'


def _stock_for(cur, codes: list, site_code: str = "") -> dict:
    if not codes:
        return {}
    codes = [c for c in codes if c is not None][:200]
    if not codes:
        return {}
    ph = ",".join("%s" for _ in codes)
    # STOCK.article_code is TEXT; pass codes as text so IN matches (ART side is bigint).
    scodes = [str(c) for c in codes]
    if site_code:
        cur.execute(
            f"SELECT article_code, COALESCE(SUM(stock_qty),0) FROM {STOCK} "
            f"WHERE article_code IN ({ph}) AND site_code = %s GROUP BY 1",
            (*scodes, site_code))
    else:
        cur.execute(
            f"SELECT article_code, COALESCE(SUM(stock_qty),0) FROM {STOCK} "
            f"WHERE article_code IN ({ph}) GROUP BY 1", tuple(scodes))
    # keys may come back as text — normalise to int so callers keying by int match
    out = {}
    for r in cur.fetchall():
        try: out[int(r[0])] = int(r[1] or 0)
        except (TypeError, ValueError): out[r[0]] = int(r[1] or 0)
    return out


def find_substitutes(article_code: int = 0, brand_name: str = "", site_code: str = "", in_stock_only: bool = False) -> dict:
    """Find substitute drugs (same generic molecule) for an article, with current stock.

    Provide article_code OR brand_name. site_code optional (filter stock to one site).
    Relational: drugs sharing the same generic_name. Returns source article_codes.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "pharma tools disabled"}
    try:
        c, cur = _conn()
        try:
            if article_code:
                cur.execute(f"SELECT generic_name, brand_name FROM {ART} WHERE article_code = %s LIMIT 1", (int(article_code),))
            elif brand_name:
                cur.execute(f"SELECT generic_name, brand_name FROM {ART} WHERE brand_name ILIKE %s "
                            f"AND generic_name IS NOT NULL AND generic_name <> '' "
                            f"ORDER BY (brand_name ILIKE %s) DESC LIMIT 1",
                            (f"%{brand_name.strip()}%", f"{brand_name.strip()}%"))
            else:
                return {"ok": False, "error": "provide article_code or brand_name"}
            row = cur.fetchone()
            if not row or not row[0]:
                # Input article not found (or has no generic to match on) →
                # explicit Not Found, not a bare empty list.
                from dash.tools.pharma_resolve import MSG_NOT_FOUND
                return {"ok": True, "count": 0, "substitutes": [],
                        "state": "not_found", "message": MSG_NOT_FOUND}
            generic, self_brand = row[0], (row[1] or "")
            if is_store_locked():
                site_code = bound_store() or site_code
            cur.execute(
                f"SELECT article_code, brand_name, generic_name FROM {ART} "
                f"WHERE generic_name = %s AND brand_name <> %s LIMIT 100",
                (generic, self_brand))
            subs = [{"code": r[0], "brand": (r[1] or "").strip(), "generic": (r[2] or "").strip(),
                     "_source": f"article_code={r[0]}"} for r in cur.fetchall()]
            stock = _stock_for(cur, [s["code"] for s in subs], site_code)
            for s in subs:
                s["stock_qty"] = stock.get(s["code"], 0)
                mask_row(s, site_code)
            if in_stock_only:
                subs = [s for s in subs if (s.get("stock_qty") or 0) > 0]
            subs.sort(key=lambda x: -(x.get("stock_qty") or 0))
            if not subs:
                # Article found but no substitutes (no other brand shares its
                # generic, or none in stock when in_stock_only) → explicit.
                from dash.tools.pharma_resolve import MSG_NOT_FOUND
                return {"ok": True, "count": 0, "generic": generic,
                        "site": site_code or "all", "substitutes": [],
                        "state": "not_found", "message": MSG_NOT_FOUND}
            return {"ok": True, "count": len(subs), "generic": generic,
                    "site": site_code or "all", "substitutes": subs[:50]}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"find_substitutes failed: {e}")
        return {"ok": False, "error": str(e)[:300]}


def alternatives_for_indication(indication: str = "", site_code: str = "", in_stock_only: bool = False) -> dict:
    """Find all articles that treat a given indication (condition), with stock.

    Relational: indication column ILIKE. Returns source article_codes.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "pharma tools disabled"}
    if not indication:
        return {"ok": False, "error": "provide indication"}
    try:
        c, cur = _conn()
        try:
            if is_store_locked():
                site_code = bound_store() or site_code
            # indication column is Burmese — expand English symptom terms to
            # Burmese synonyms and OR-match (shared map with chemist tool).
            try:
                from dash.tools.pharma_chemist_tool import _expand_symptom_terms
                _terms = _expand_symptom_terms(indication.strip())
            except Exception:
                _terms = [indication.strip()]
            _where = " OR ".join(["indication ILIKE %s"] * len(_terms))
            cur.execute(
                f"SELECT article_code, brand_name, generic_name FROM {ART} "
                f"WHERE {_where} LIMIT 200", tuple(f"%{t}%" for t in _terms))
            arts = [{"code": r[0], "brand": (r[1] or "").strip(), "generic": (r[2] or "").strip(),
                     "_source": f"article_code={r[0]}"} for r in cur.fetchall()]
            stock = _stock_for(cur, [a["code"] for a in arts], site_code)
            for a in arts:
                a["stock_qty"] = stock.get(a["code"], 0)
                mask_row(a, site_code)
            if in_stock_only:
                arts = [a for a in arts if (a.get("stock_qty") or 0) > 0]
            arts.sort(key=lambda x: -(x.get("stock_qty") or 0))
            if not arts:
                # No article treats this indication (or none in stock when
                # in_stock_only) → explicit Not Found, not a bare empty list.
                from dash.tools.pharma_resolve import MSG_NOT_FOUND
                return {"ok": True, "count": 0, "indication": indication,
                        "site": site_code or "all", "articles": [],
                        "state": "not_found", "message": MSG_NOT_FOUND}
            return {"ok": True, "count": len(arts), "indication": indication,
                    "site": site_code or "all", "articles": arts[:50]}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"alternatives_for_indication failed: {e}")
        return {"ok": False, "error": str(e)[:300]}


def drug_relationships(article_code: int = 0, brand_name: str = "") -> dict:
    """Show the neighbourhood of one article: generic, category, indication,
    composition, side effects, and substitute drugs (same generic). Relational.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "pharma tools disabled"}
    try:
        c, cur = _conn()
        try:
            if article_code:
                cur.execute(f"SELECT article_code, brand_name, generic_name, category, "
                            f"indication, composition, side_effect FROM {ART} "
                            f"WHERE article_code = %s LIMIT 1", (int(article_code),))
            elif brand_name:
                cur.execute(f"SELECT article_code, brand_name, generic_name, category, "
                            f"indication, composition, side_effect FROM {ART} "
                            f"WHERE brand_name ILIKE %s ORDER BY (brand_name ILIKE %s) DESC LIMIT 1",
                            (f"%{brand_name.strip()}%", f"{brand_name.strip()}%"))
            else:
                return {"ok": False, "error": "provide article_code or brand_name"}
            b = cur.fetchone()
            if not b:
                return {"ok": True, "found": False}
            code, brand, generic, cat, ind, comp, se = b
            subs = []
            if generic:
                cur.execute(f"SELECT brand_name FROM {ART} WHERE generic_name = %s "
                            f"AND brand_name <> %s LIMIT 30", (generic, brand or ""))
                subs = [(r[0] or "").strip() for r in cur.fetchall()]
            return {"ok": True, "found": True, "article": {
                "code": code, "brand": (brand or "").strip(), "generic": (generic or "").strip(),
                "category": (cat or "").strip(), "indication": (ind or "").strip(),
                "composition": (comp or "").strip(), "side_effect": (se or "").strip(),
                "substitutes": subs, "_source": f"article_code={code}",
            }}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"drug_relationships failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
