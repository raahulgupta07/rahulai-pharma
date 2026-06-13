"""Drug network — multi-hop relationships in ONE Apache AGE graph walk.

Relational SQL answers "substitutes" (same generic) fine, but a richer "what else
is related to X" — direct substitutes PLUS drugs that treat the same condition
PLUS same-category neighbours — is a multi-path graph question that's awkward in
SQL (three UNIONs + self-joins). This tool walks the `citypharma_kg` AGE graph
once and returns the three relationship buckets, then joins live stock relationally.

Graph built by scripts/build_pharma_graph.py (Article/Generic/Category/Indication
/Composition nodes; HAS_GENERIC/IN_CATEGORY/TREATS/SUBSTITUTE_OF edges).

FAIL-SOFT: opens its own direct cp-db connection, LOADs age per-session (no
shared_preload needed). If AGE or the graph is missing, returns a soft error so
the agent falls back to the relational substitutes tool — the graph is an
enhancement, never a hard dependency. Disable with PHARMA_GRAPH_DISABLED=1.

Store-scope: stock numbers honour the same Tier-2 masking as the other pharma
tools (own branch full, other branches availability only).
"""
from __future__ import annotations

import os
import json
import logging

from dash.api_scope import is_store_locked, bound_stores, mask_row

log = logging.getLogger("dash.pharma_network")

SCHEMA = "citypharma"
GRAPH = "citypharma_kg"
_FLAT = f'"{SCHEMA}"."shop_flat"'


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
    cur.execute("SET statement_timeout = '25s';")
    cur.execute("LOAD 'age';")
    cur.execute('SET search_path = ag_catalog, "$user", public;')
    return c, cur


def _ag(v):
    """Parse an agtype scalar string ('123' / '\"BRAND\"') to a Python value."""
    if v is None:
        return None
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return str(v).strip('"')


def _esc(s: str) -> str:
    return (s or "").replace("\\", " ").replace("'", " ").strip()


def _cypher(cur, q: str):
    cur.execute(f"SELECT * FROM cypher('{GRAPH}', $$ {q} $$) AS (code agtype, brand agtype, generic agtype);")
    out = []
    for code, brand, generic in cur.fetchall():
        c = _ag(code)
        try:
            c = int(c)
        except (TypeError, ValueError):
            pass
        out.append({"code": c, "brand": _ag(brand) or "", "generic": _ag(generic) or ""})
    return out


def _stock(cur, codes: list, my_sites: list, locked: bool) -> dict:
    """{code: {your_qty, other_branches[]}} from shop_flat. Tier-2 masked."""
    if not codes:
        return {}
    keys = [str(c) for c in codes]
    out: dict = {c: {"your_qty": 0, "other_branches": []} for c in codes}
    # own stock
    if my_sites:
        cur.execute(
            f"SELECT art_key, COALESCE(SUM(stock_qty),0) FROM {_FLAT} "
            f"WHERE art_key = ANY(%s) AND site_code = ANY(%s) GROUP BY art_key",
            (keys, my_sites))
        for ak, qty in cur.fetchall():
            try:
                out[int(ak)]["your_qty"] = int(qty or 0)
            except (KeyError, ValueError):
                pass
    # other branches
    try:
        from dash.tools.shop_labels import shop_label
    except Exception:
        shop_label = lambda x: x  # noqa: E731
    if my_sites:
        cur.execute(
            f"SELECT art_key, site_code, stock_qty FROM {_FLAT} "
            f"WHERE art_key = ANY(%s) AND NOT (site_code = ANY(%s)) AND stock_qty>0 "
            f"ORDER BY stock_qty DESC", (keys, my_sites))
    else:
        cur.execute(
            f"SELECT art_key, site_code, stock_qty FROM {_FLAT} "
            f"WHERE art_key = ANY(%s) AND stock_qty>0 ORDER BY stock_qty DESC", (keys,))
    for ak, sc, qty in cur.fetchall():
        try:
            k = int(ak)
        except (TypeError, ValueError):
            continue
        if k not in out or len(out[k]["other_branches"]) >= 5:
            continue
        if locked:
            out[k]["other_branches"].append({"site": sc, "shop": shop_label(sc), "available": True})
        else:
            out[k]["other_branches"].append({"site": sc, "shop": shop_label(sc), "qty": int(qty)})
    return out


def drug_network(brand_name: str = "", site_code: str = "", limit: int = 8) -> dict:
    """Related drugs for a brand via the knowledge graph: direct substitutes (same
    molecule), drugs that treat the same condition, and same-category neighbours —
    each with live stock. Use for 'what's related to X', 'broader alternatives to
    X', 'what else could I offer instead of X'. brand_name=the drug; site_code=the
    staff branch (own stock). Falls back to relational substitutes if the graph is
    unavailable.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "pharma tools disabled"}
    if not brand_name or not brand_name.strip():
        return {"ok": False, "error": "provide a brand_name"}
    bn = _esc(brand_name)
    locked = is_store_locked()
    owned = bound_stores() if locked else []
    my_sites = owned if locked else ([site_code] if site_code else [])
    try:
        c, cur = _conn()
        try:
            # anchor regex (case-insensitive contains)
            rx = f"(?i).*{bn}.*"
            subs = _cypher(cur, (
                f"MATCH (a:Article)-[:SUBSTITUTE_OF]->(s:Article) "
                f"WHERE a.brand =~ '{rx}' AND s.brand <> a.brand "
                f"RETURN s.code, s.brand, s.generic LIMIT {int(limit)}"))
            same_ind = _cypher(cur, (
                f"MATCH (a:Article)-[:TREATS]->(i:Indication)<-[:TREATS]-(t:Article) "
                f"WHERE a.brand =~ '{rx}' AND t.code <> a.code "
                f"RETURN DISTINCT t.code, t.brand, t.generic LIMIT {int(limit)}"))
            same_cat = _cypher(cur, (
                f"MATCH (a:Article)-[:IN_CATEGORY]->(cat:Category)<-[:IN_CATEGORY]-(t:Article) "
                f"WHERE a.brand =~ '{rx}' AND t.code <> a.code "
                f"RETURN DISTINCT t.code, t.brand, t.generic LIMIT {int(limit)}"))

            # dedup same_ind / same_cat against direct substitutes
            sub_codes = {d["code"] for d in subs}
            same_ind = [d for d in same_ind if d["code"] not in sub_codes][:limit]
            ind_codes = sub_codes | {d["code"] for d in same_ind}
            same_cat = [d for d in same_cat if d["code"] not in ind_codes][:limit]

            if not subs and not same_ind and not same_cat:
                return {"ok": True, "brand": brand_name, "found": False,
                        "message": "No graph relationships found for that drug."}

            # live stock for everything, masked
            all_codes = [d["code"] for d in (subs + same_ind + same_cat)
                         if isinstance(d["code"], int)]
            stock = _stock(cur, all_codes, my_sites, locked)

            def _attach(items):
                out = []
                for d in items:
                    st = stock.get(d["code"], {"your_qty": 0, "other_branches": []})
                    row = {"article_code": d["code"], "brand": d["brand"],
                           "salt": d["generic"], "your_stock": int(st["your_qty"]),
                           "in_stock": int(st["your_qty"]) > 0,
                           "other_branches": st["other_branches"]}
                    mask_row(row, "" if locked else site_code)
                    out.append(row)
                out.sort(key=lambda r: -(r.get("your_stock") or 0))
                return out

            return {
                "ok": True, "brand": brand_name, "found": True, "source": "graph",
                "direct_substitutes": _attach(subs),
                "same_indication": _attach(same_ind),
                "same_category": _attach(same_cat),
            }
        finally:
            c.close()
    except Exception as e:
        log.warning(f"drug_network failed (AGE?): {e}")
        # fail-soft: signal the agent to use the relational substitutes tool
        return {"ok": False, "error": "graph unavailable", "fallback": "find_substitutes",
                "detail": str(e)[:200]}
