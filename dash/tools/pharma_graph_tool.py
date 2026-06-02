"""Apache AGE pharma graph tools for the CityPharma Analyst.

Graph 'citypharma' holds drug-relationship structure (substitutes, generics,
categories, indications, compositions). Stock (106k rows) stays relational in
proj_demo_citypharma.citypharma_balance_stock and is joined by article_code.

These tools open their OWN read-only direct connection to Postgres (cp-db / service
'dash-db'), so AGE's required `search_path = ag_catalog` is set cleanly per call
without fighting the analyst's pooled engine. shared_preload_libraries='age' means
no LOAD is needed.

Disable with PHARMA_GRAPH_DISABLED=1.
"""
from __future__ import annotations

import os
import logging

log = logging.getLogger("dash.pharma_graph")

GRAPH = "citypharma"
SCHEMA = "proj_demo_citypharma"
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
    cur.execute('SET search_path = ag_catalog, "$user", public;')
    cur.execute("SET statement_timeout = '20s';")
    return c, cur


def _q(s: str) -> str:
    return str(s or "").replace("'", "’")


def _cypher(cur, query: str, cols: int):
    coldef = ", ".join(f"c{i} agtype" for i in range(cols))
    cur.execute(f"SELECT * FROM cypher('{GRAPH}', $$ {query} $$) AS ({coldef});")
    return cur.fetchall()


def _agt(v):
    # agtype values come back as JSON-ish strings; strip quotes for scalars
    s = str(v)
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def _stock_for(cur, codes: list[int], site_code: str = ""):
    if not codes:
        return {}
    codes = [int(c) for c in codes][:200]
    ph = ",".join(str(c) for c in codes)
    if site_code:
        cur.execute(
            f"SELECT article_code, SUM(stock_qty) FROM {STOCK} "
            f"WHERE article_code IN ({ph}) AND site_code = %s GROUP BY 1", (site_code,))
    else:
        cur.execute(
            f"SELECT article_code, SUM(stock_qty) FROM {STOCK} "
            f"WHERE article_code IN ({ph}) GROUP BY 1")
    return {int(r[0]): int(r[1] or 0) for r in cur.fetchall()}


def find_substitutes(article_code: int = 0, brand_name: str = "", site_code: str = "", in_stock_only: bool = False) -> dict:
    """Find substitute drugs (same generic molecule) for an article, with current stock.

    Use when a brand is out of stock and the user needs alternatives. Provide
    article_code OR brand_name. site_code optional (filter stock to one site).
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "graph disabled"}
    try:
        c, cur = _conn()
        try:
            if article_code:
                match = f"(a:Article {{code: {int(article_code)}}})"
            elif brand_name:
                match = f"(a:Article) WHERE a.brand =~ '(?i).*{_q(brand_name)}.*'"
            else:
                return {"ok": False, "error": "provide article_code or brand_name"}
            rows = _cypher(cur,
                f"MATCH {match}-[:SUBSTITUTE_OF]->(b:Article) "
                f"RETURN b.code, b.brand, b.generic LIMIT 100", 3)
            subs = [{"code": int(_agt(r[0])), "brand": _agt(r[1]), "generic": _agt(r[2])} for r in rows]
            stock = _stock_for(cur, [s["code"] for s in subs], site_code)
            for s in subs:
                s["stock_qty"] = stock.get(s["code"], 0)
            if in_stock_only:
                subs = [s for s in subs if s["stock_qty"] > 0]
            subs.sort(key=lambda x: -x["stock_qty"])
            return {"ok": True, "count": len(subs), "site": site_code or "all", "substitutes": subs[:50]}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"find_substitutes failed: {e}")
        return {"ok": False, "error": str(e)[:300]}


def alternatives_for_indication(indication: str = "", site_code: str = "", in_stock_only: bool = False) -> dict:
    """Find all articles that treat a given indication (condition), with stock.

    Use for 'what do we have for <condition>' or therapeutic-alternative questions.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "graph disabled"}
    if not indication:
        return {"ok": False, "error": "provide indication"}
    try:
        c, cur = _conn()
        try:
            rows = _cypher(cur,
                f"MATCH (a:Article)-[:TREATS]->(i:Indication) "
                f"WHERE i.name =~ '(?i).*{_q(indication)}.*' "
                f"RETURN a.code, a.brand, a.generic LIMIT 200", 3)
            arts = [{"code": int(_agt(r[0])), "brand": _agt(r[1]), "generic": _agt(r[2])} for r in rows]
            stock = _stock_for(cur, [a["code"] for a in arts], site_code)
            for a in arts:
                a["stock_qty"] = stock.get(a["code"], 0)
            if in_stock_only:
                arts = [a for a in arts if a["stock_qty"] > 0]
            arts.sort(key=lambda x: -x["stock_qty"])
            return {"ok": True, "count": len(arts), "indication": indication, "site": site_code or "all", "articles": arts[:50]}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"alternatives_for_indication failed: {e}")
        return {"ok": False, "error": str(e)[:300]}


def drug_relationships(article_code: int = 0, brand_name: str = "") -> dict:
    """Show the graph neighbourhood of one article: generic, category, indications,
    compositions, and substitute drugs. Use for 'tell me about <drug> and related'.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "graph disabled"}
    try:
        c, cur = _conn()
        try:
            if article_code:
                match = f"(a:Article {{code: {int(article_code)}}})"
            elif brand_name:
                match = f"(a:Article) WHERE a.brand =~ '(?i).*{_q(brand_name)}.*'"
            else:
                return {"ok": False, "error": "provide article_code or brand_name"}
            base = _cypher(cur, f"MATCH {match} RETURN a.code, a.brand, a.generic, a.category LIMIT 1", 4)
            if not base:
                return {"ok": True, "found": False}
            b = base[0]
            code = int(_agt(b[0]))
            ind = _cypher(cur, f"MATCH (a:Article {{code:{code}}})-[:TREATS]->(i:Indication) RETURN i.name LIMIT 20", 1)
            comp = _cypher(cur, f"MATCH (a:Article {{code:{code}}})-[:HAS_COMPOSITION]->(c:Composition) RETURN c.name LIMIT 20", 1)
            subs = _cypher(cur, f"MATCH (a:Article {{code:{code}}})-[:SUBSTITUTE_OF]->(s:Article) RETURN s.brand LIMIT 30", 1)
            return {"ok": True, "found": True, "article": {
                "code": code, "brand": _agt(b[1]), "generic": _agt(b[2]), "category": _agt(b[3]),
                "indications": [_agt(r[0]) for r in ind],
                "compositions": [_agt(r[0]) for r in comp],
                "substitutes": [_agt(r[0]) for r in subs],
            }}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"drug_relationships failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
