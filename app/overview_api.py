"""Overview / Dashboard cockpit API — genuinely-new aggregation for the
operator landing page.

One call returns the data NOT already served by existing endpoints:
  GET /api/projects/{slug}/overview
    -> { kpis, pharma, top_questions, recent_chats }

Health / quality / insights / tool-health / training-runs / auto-train-log /
gateway are fetched by the page from their existing endpoints. This endpoint
only computes the KPI rail + live pharma signals + chat rollups.

Fail-soft: every sub-query wrapped; a missing table never 500s the page.
Mirrors app/datasource_api.py (AUTOCOMMIT + NullPool + _schema_for).
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

router = APIRouter(prefix="/api/projects", tags=["Overview"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)
logger = logging.getLogger(__name__)


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_access(user: dict, slug: str):
    from app.auth import check_project_permission
    if not check_project_permission(user, slug):
        raise HTTPException(403, "Access denied")


def _schema_for(slug: str) -> str:
    try:
        from db.session import create_project_schema
        return create_project_schema(slug)
    except Exception:
        return re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _find_table(conn, schema: str, has_col: str) -> str | None:
    """First table in the project schema that has the given column."""
    try:
        r = conn.execute(text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema=:sch AND column_name=:col "
            "ORDER BY table_name LIMIT 1"
        ), {"sch": schema, "col": has_col}).fetchone()
        return r[0] if r else None
    except Exception:
        return None


def _has_col(conn, schema: str, table: str, col: str) -> bool:
    try:
        r = conn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema=:sch AND table_name=:t AND column_name=:c LIMIT 1"
        ), {"sch": schema, "t": table, "c": col}).fetchone()
        return r is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# graph view (Obsidian-style force map) — Brain KG + Apache AGE pharma graph
# ---------------------------------------------------------------------------
def _age_conn():
    """Direct cp-db connection (NOT pgbouncer) with AGE search_path — mirrors
    dash/tools/pharma_graph_tool.py so cypher() resolves."""
    import os
    import psycopg
    c = psycopg.connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8, autocommit=True,
    )
    cur = c.cursor()
    cur.execute('SET search_path = ag_catalog, "$user", public;')
    cur.execute("SET statement_timeout = '20s';")
    return c, cur


def _agt(v):
    s = str(v)
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


_GROUP_COLORS = {
    "Generic": "#7c9cff", "Brand": "#c96342", "Category": "#3ec9a7",
    "Indication": "#e0a458", "Composition": "#b06dff", "kg": "#9aa0b5",
}


def _pack(nodes: dict, edges: list) -> dict:
    """nodes: {id: {label, group}}  edges: [(src,dst,rel)] -> sigma-ready payload
    with degree-driven val + group color."""
    deg: dict = {}
    for s, d, _ in edges:
        deg[s] = deg.get(s, 0) + 1
        deg[d] = deg.get(d, 0) + 1
    out_nodes = [{
        "id": nid,
        "label": (n.get("label") or nid)[:48],
        "group": n.get("group", "kg"),
        "color": _GROUP_COLORS.get(n.get("group", "kg"), "#9aa0b5"),
        "val": deg.get(nid, 1),
    } for nid, n in nodes.items()]
    seen = set()
    out_edges = []
    for s, d, rel in edges:
        k = tuple(sorted((s, d))) + (rel,)
        if k in seen:
            continue
        seen.add(k)
        out_edges.append({"source": s, "target": d, "rel": rel})
    return {"nodes": out_nodes, "edges": out_edges,
            "node_count": len(out_nodes), "edge_count": len(out_edges)}


@router.get("/{slug}/graph")
def graph_view(slug: str, request: Request, source: str = "pharma",
               focus: str = "", hops: int = 2, limit: int = 4000):
    """Force-graph data. source=brain (KG triples) | pharma (AGE drug web).
    pharma + focus=<brand> → ego-graph N hops around that drug."""
    user = _get_user(request)
    _check_access(user, slug)
    limit = max(1, min(limit, 20000))
    hops = max(1, min(hops, 3))

    if source == "brain":
        nodes: dict = {}
        edges: list = []
        try:
            with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                rows = conn.execute(text(
                    "SELECT subject, predicate, object FROM public.dash_knowledge_triples "
                    "WHERE project_slug=:s LIMIT :n"
                ), {"s": slug, "n": limit}).fetchall()
            for subj, pred, obj in rows:
                if not subj or not obj:
                    continue
                nodes.setdefault(subj, {"label": subj, "group": "kg"})
                nodes.setdefault(obj, {"label": obj, "group": "kg"})
                edges.append((subj, obj, pred or "rel"))
        except Exception:
            logger.exception("brain graph %s", slug)
        return {"source": "brain", **_pack(nodes, edges)}

    # ---- pharma drug-substitute web, derived RELATIONALLY ----
    # Two drugs are substitutes iff they share generic_name (same rule AGE used
    # to derive SUBSTITUTE_OF). Computed live from articles_list — no AGE
    # dependency, survives the cp-db AGE durability landmine.
    schema = _schema_for(slug)
    nodes = {}
    edges = []
    cat_color: dict = {}
    palette = ["#7c9cff", "#c96342", "#3ec9a7", "#e0a458", "#b06dff",
               "#5fb0d6", "#d65f9e", "#8bc34a", "#ff8a65", "#9aa0b5"]

    def color_for(cat):
        if cat not in cat_color:
            cat_color[cat] = palette[len(cat_color) % len(palette)]
        return cat_color[cat]

    try:
        with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            atbl = _find_table(conn, schema, "generic_name")
            if not atbl or not _has_col(conn, schema, atbl, "brand_name"):
                return {"source": "pharma", "focus": focus or None, **_pack(nodes, edges)}
            tq = _q(schema) + "." + _q(atbl)
            has_cat = _has_col(conn, schema, atbl, "category")
            cat_a = "a.category" if has_cat else "NULL"
            if focus:
                # ego: every drug sharing focus drug's generic
                rows = conn.execute(text(
                    f"SELECT a.brand_name, {cat_a}, b.brand_name "
                    f"FROM {tq} a JOIN {tq} b "
                    f"ON a.generic_name = b.generic_name AND a.brand_name < b.brand_name "
                    f"WHERE a.generic_name IS NOT NULL AND a.generic_name <> '' "
                    f"AND a.generic_name = (SELECT generic_name FROM {tq} "
                    f"WHERE brand_name = :f LIMIT 1) LIMIT :n"
                ), {"f": focus, "n": limit}).fetchall()
            else:
                rows = conn.execute(text(
                    f"SELECT a.brand_name, {cat_a}, b.brand_name "
                    f"FROM {tq} a JOIN {tq} b "
                    f"ON a.generic_name = b.generic_name AND a.brand_name < b.brand_name "
                    f"WHERE a.generic_name IS NOT NULL AND a.generic_name <> '' LIMIT :n"
                ), {"n": limit}).fetchall()
            for an, ac, bn in rows:
                a = (an or "").strip()
                b = (bn or "").strip()
                if not a or not b:
                    continue
                cat = (ac or "—") if has_cat else "—"
                nodes.setdefault(a, {"label": a, "group": cat, "color": color_for(cat)})
                nodes.setdefault(b, {"label": b, "group": cat, "color": color_for(cat)})
                edges.append((a, b, "substitute"))
    except Exception:
        logger.exception("pharma graph %s", slug)

    # color override from per-node category (override _pack default)
    packed = _pack(nodes, edges)
    for nd in packed["nodes"]:
        src = nodes.get(nd["id"])
        if src and src.get("color"):
            nd["color"] = src["color"]
    return {"source": "pharma", "focus": focus or None, **packed}


@router.get("/{slug}/graph/node")
def graph_node(slug: str, request: Request, id: str = ""):
    """Rich detail for one drug node (brand_name) in the pharma graph:
    identity + clinical + availability (stock) + substitute siblings.
    article_code is joined ::text both sides — stock stores it as text while
    articles stores bigint (cross-table raw compare crashes). See DEVLOG R4."""
    user = _get_user(request)
    _check_access(user, slug)
    brand = (id or "").strip()
    if not brand:
        raise HTTPException(400, "id (brand_name) required")
    schema = _schema_for(slug)
    out: dict = {"id": brand, "identity": {}, "clinical": {}, "stock": {},
                 "substitutes": [], "stores": []}
    try:
        with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            atbl = _find_table(conn, schema, "generic_name")
            if not atbl or not _has_col(conn, schema, atbl, "brand_name"):
                return out
            aq = _q(schema) + "." + _q(atbl)
            cols = {c: _has_col(conn, schema, atbl, c) for c in
                    ("composition", "category", "article_code", "status", "mmreg",
                     "indication", "dosage", "side_effect")}
            sel = ["generic_name", "brand_name"] + [c for c, ok in cols.items() if ok]
            row = conn.execute(text(
                f"SELECT {', '.join(_q(c) for c in sel)} FROM {aq} "
                f"WHERE brand_name = :b LIMIT 1"
            ), {"b": brand}).mappings().fetchone()
            if not row:
                return out
            generic = (row.get("generic_name") or "").strip()
            out["identity"] = {
                "generic": generic,
                "composition": row.get("composition"),
                "category": row.get("category"),
                "article_code": (str(row.get("article_code")) if row.get("article_code") is not None else None),
                "status": row.get("status"),
                "mmreg": row.get("mmreg"),
            }
            out["clinical"] = {
                "indication": row.get("indication"),
                "dosage": row.get("dosage"),
                "side_effect": row.get("side_effect"),
            }
            acode = row.get("article_code") if cols.get("article_code") else None

            # ---- availability from stock table (article_code ::text join) ----
            stk = _find_table(conn, schema, "stock_qty")
            if stk and acode is not None and _has_col(conn, schema, stk, "article_code"):
                sq = _q(schema) + "." + _q(stk)
                has_site = _has_col(conn, schema, stk, "site_code")
                has_cost = _has_col(conn, schema, stk, "weighted_cost_price")
                cost_sel = "AVG(NULLIF(weighted_cost_price,0))" if has_cost else "NULL"
                agg = conn.execute(text(
                    f"SELECT COALESCE(SUM(stock_qty),0) AS total, "
                    f"COUNT(DISTINCT {'site_code' if has_site else 'id'}) AS stores, "
                    f"{cost_sel} AS avg_cost FROM {sq} "
                    f"WHERE article_code::text = :c"
                ), {"c": str(acode)}).mappings().fetchone()
                out["stock"] = {
                    "total": int(agg["total"] or 0),
                    "stores": int(agg["stores"] or 0),
                    "avg_cost": (round(float(agg["avg_cost"]), 0) if agg["avg_cost"] is not None else None),
                }
                if has_site:
                    srows = conn.execute(text(
                        f"SELECT site_code, COALESCE(SUM(stock_qty),0) AS q FROM {sq} "
                        f"WHERE article_code::text = :c GROUP BY site_code "
                        f"ORDER BY q DESC LIMIT 8"
                    ), {"c": str(acode)}).fetchall()
                    out["stores"] = [{"site": s[0], "qty": int(s[1] or 0)} for s in srows]

            # ---- substitute siblings (same generic) + their stock ----
            if generic:
                subs = conn.execute(text(
                    f"SELECT brand_name"
                    f"{', ' + _q('article_code') if cols.get('article_code') else ''} "
                    f"FROM {aq} WHERE generic_name = :g AND brand_name <> :b "
                    f"ORDER BY brand_name LIMIT 40"
                ), {"g": generic, "b": brand}).mappings().fetchall()
                stock_by_code: dict = {}
                if stk and subs and cols.get("article_code") and _has_col(conn, schema, stk, "article_code"):
                    sq = _q(schema) + "." + _q(stk)
                    codes = [str(s["article_code"]) for s in subs if s.get("article_code") is not None]
                    if codes:
                        qr = conn.execute(text(
                            f"SELECT article_code::text AS c, COALESCE(SUM(stock_qty),0) AS q "
                            f"FROM {sq} WHERE article_code::text = ANY(:codes) GROUP BY 1"
                        ), {"codes": codes}).fetchall()
                        stock_by_code = {r[0]: int(r[1] or 0) for r in qr}
                out["substitutes"] = [{
                    "brand": s["brand_name"],
                    "qty": stock_by_code.get(str(s.get("article_code")), None),
                } for s in subs]
    except Exception:
        logger.exception("graph_node %s %s", slug, brand)
    return out


# ---------------------------------------------------------------------------
# EDA — exploratory per-column profiling for one table in the project schema
# ---------------------------------------------------------------------------
_NUMERIC_TYPES = {
    "integer", "bigint", "smallint", "numeric", "decimal",
    "double precision", "real",
}


def _num_safe(v):
    """Cast Decimal/None/anything to a JSON-safe float or None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@router.get("/{slug}/eda")
def eda(slug: str, request: Request, table: str = ""):
    """Exploratory per-column data profiling for ONE table in the project schema.

    Null %, distinct count, numeric min/max/mean, text avg-length, and the top-5
    most common values per column. Big tables (rows > 50000, e.g. balance_stock
    at ~102k) are SAMPLED to 20k rows (LIMIT 20000) for the per-column stats —
    so null_pct / distinct / top reflect the sample, while `rows` is the exact
    total. Numeric-ness is read from information_schema.columns.data_type — NEVER
    cast a text column to numeric (stock article_code is the literal "1E+12" and
    a raw numeric cast would crash). Fail-soft per column.
    """
    user = _get_user(request)
    _check_access(user, slug)
    schema = _schema_for(slug)

    SAMPLE_LIMIT = 20000
    SAMPLE_THRESHOLD = 50000
    MAX_COLS = 40

    with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # all base tables in the schema (for the frontend picker)
        try:
            trows = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=:sch AND table_type='BASE TABLE' "
                "ORDER BY table_name"
            ), {"sch": schema}).fetchall()
            tables = [r[0] for r in trows]
        except Exception:
            logger.exception("eda table list %s", slug)
            tables = []

        # resolve the target table
        tname = (table or "").strip()
        if not tname:
            tname = _find_table(conn, schema, "generic_name")
            if not tname:
                tname = tables[0] if tables else None
        if not tname or tname not in tables:
            raise HTTPException(404, "table not found in schema")

        tq = _q(schema) + "." + _q(tname)

        # exact total row count
        try:
            rows = int(conn.execute(text(f"SELECT COUNT(*) FROM {tq}")).scalar() or 0)
        except Exception:
            logger.exception("eda count %s.%s", schema, tname)
            rows = 0

        if rows == 0:
            return {"table": tname, "schema": schema, "rows": 0,
                    "dup_rows": 0, "tables": tables, "columns": []}

        # column list + data_type, ordered by ordinal_position
        try:
            crows = conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema=:sch AND table_name=:t "
                "ORDER BY ordinal_position"
            ), {"sch": schema, "t": tname}).fetchall()
        except Exception:
            logger.exception("eda columns %s.%s", schema, tname)
            crows = []

        all_cols = [(c[0], c[1]) for c in crows]
        cols = all_cols[:MAX_COLS]

        # sampling source: full table if small, else first 20k rows
        sampled = rows > SAMPLE_THRESHOLD
        src = f"(SELECT * FROM {tq} LIMIT {SAMPLE_LIMIT})" if sampled else tq
        sample_n = SAMPLE_LIMIT if sampled else rows

        columns: list = []
        for col, dtype in cols:
            try:
                qc = _q(col)
                is_num = dtype in _NUMERIC_TYPES
                # base stats: null count + distinct (over the profiling source)
                if is_num:
                    agg = conn.execute(text(
                        f"SELECT COUNT(*) FILTER (WHERE {qc} IS NULL) AS nulls, "
                        f"COUNT(DISTINCT {qc}) AS distinct_n, "
                        f"MIN({qc}) AS mn, MAX({qc}) AS mx, AVG({qc}) AS av "
                        f"FROM {src}"
                    )).mappings().fetchone()
                else:
                    agg = conn.execute(text(
                        f"SELECT COUNT(*) FILTER (WHERE {qc} IS NULL) AS nulls, "
                        f"COUNT(DISTINCT {qc}) AS distinct_n, "
                        f"AVG(length({qc}::text)) AS avg_len "
                        f"FROM {src}"
                    )).mappings().fetchone()

                nulls = int(agg["nulls"] or 0)
                distinct_n = int(agg["distinct_n"] or 0)
                null_pct = round(100.0 * nulls / sample_n, 1) if sample_n else 0.0

                cinfo: dict = {
                    "name": col,
                    "type": dtype,
                    "null_pct": null_pct,
                    "distinct": distinct_n,
                    "min": None,
                    "max": None,
                    "mean": None,
                    "avg_len": None,
                    "top": [],
                }
                if is_num:
                    cinfo["min"] = _num_safe(agg["mn"])
                    cinfo["max"] = _num_safe(agg["mx"])
                    m = _num_safe(agg["av"])
                    cinfo["mean"] = round(m, 2) if m is not None else None
                else:
                    al = _num_safe(agg["avg_len"])
                    cinfo["avg_len"] = round(al, 1) if al is not None else None

                # top values — skip when every value is unique
                if distinct_n and distinct_n < sample_n:
                    trows2 = conn.execute(text(
                        f"SELECT {qc}::text AS v, COUNT(*) AS c FROM {src} "
                        f"WHERE {qc} IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT 5"
                    )).fetchall()
                    cinfo["top"] = [
                        {"val": (r[0] if r[0] is not None else None),
                         "pct": round(100.0 * int(r[1] or 0) / sample_n, 1)}
                        for r in trows2
                    ]
                columns.append(cinfo)
            except Exception:
                logger.exception("eda column %s.%s.%s", schema, tname, col)
                continue

        # full-duplicate rows — only when cheap (small table, few columns)
        dup_rows = 0
        if rows <= SAMPLE_THRESHOLD and len(all_cols) <= 20 and all_cols:
            try:
                col_list = ", ".join(_q(c) for c, _ in all_cols)
                dup_rows = int(conn.execute(text(
                    f"SELECT COUNT(*) FROM (SELECT 1 FROM {tq} "
                    f"GROUP BY {col_list} HAVING COUNT(*) > 1) x"
                )).scalar() or 0)
            except Exception:
                logger.exception("eda dup_rows %s.%s", schema, tname)
                dup_rows = 0

    return {
        "table": tname,
        "schema": schema,
        "rows": rows,
        "dup_rows": dup_rows,
        "tables": tables,
        "columns": columns,
    }


@router.get("/{slug}/overview")
def overview(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    schema = _schema_for(slug)

    kpis: dict = {}
    pharma: dict = {}
    top_questions: list = []
    recent_chats: list = []

    def scalar(conn, sql, **kw):
        try:
            return conn.execute(text(sql), kw).scalar()
        except Exception:
            return None

    with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # ---- chat rollups (platform tables) ----
        try:
            row = conn.execute(text(
                "SELECT COUNT(*) AS total, "
                "COUNT(*) FILTER (WHERE created_at > now() - interval '24 hours') AS d1 "
                "FROM public.dash_chat_sessions WHERE project_slug=:s"
            ), {"s": slug}).fetchone()
            kpis["chats_total"] = int(row[0] or 0)
            kpis["chats_24h"] = int(row[1] or 0)
        except Exception:
            kpis["chats_total"] = 0
            kpis["chats_24h"] = 0

        try:
            rows = conn.execute(text(
                "SELECT first_message, COUNT(*) c "
                "FROM public.dash_chat_sessions "
                "WHERE project_slug=:s AND first_message IS NOT NULL "
                "AND created_at > now() - interval '30 days' "
                "GROUP BY first_message ORDER BY c DESC, MAX(created_at) DESC LIMIT 5"
            ), {"s": slug}).fetchall()
            top_questions = [{"q": (r[0] or "")[:90], "n": int(r[1] or 0)} for r in rows]
        except Exception:
            top_questions = []

        try:
            rows = conn.execute(text(
                "SELECT first_message, created_at FROM public.dash_chat_sessions "
                "WHERE project_slug=:s AND first_message IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 6"
            ), {"s": slug}).fetchall()
            recent_chats = [{"q": (r[0] or "")[:80],
                             "at": r[1].isoformat() if r[1] else None} for r in rows]
        except Exception:
            recent_chats = []

        # ---- catalog table (has generic_name) ----
        cat_tbl = _find_table(conn, schema, "generic_name") or _find_table(conn, schema, "brand_name")
        catalog_skus = 0
        top_category = None
        if cat_tbl:
            n = scalar(conn, f"SELECT COUNT(*) FROM {_q(schema)}.{_q(cat_tbl)}")
            catalog_skus = int(n or 0)
            if _has_col(conn, schema, cat_tbl, "category"):
                try:
                    r = conn.execute(text(
                        f"SELECT category, COUNT(*) c FROM {_q(schema)}.{_q(cat_tbl)} "
                        f"WHERE category IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT 1"
                    )).fetchone()
                    top_category = r[0] if r else None
                except Exception:
                    top_category = None
        kpis["catalog_skus"] = catalog_skus

        # ---- table count ----
        try:
            n = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=:sch AND table_type='BASE TABLE'"
            ), {"sch": schema}).scalar()
            kpis["tables"] = int(n or 0)
        except Exception:
            kpis["tables"] = 0

        # ---- stock table (has stock_qty + site_code) ----
        stk = _find_table(conn, schema, "stock_qty")
        if stk and _has_col(conn, schema, stk, "site_code"):
            tq = _q(schema) + "." + _q(stk)
            has_cost = _has_col(conn, schema, stk, "weighted_cost_price")
            val_expr = "SUM(stock_qty * weighted_cost_price)" if has_cost else "0"
            try:
                r = conn.execute(text(
                    f"SELECT COUNT(*) rows, "
                    f"COUNT(DISTINCT site_code) sites, "
                    f"COALESCE(SUM(stock_qty),0) units, "
                    f"COALESCE({val_expr},0) val, "
                    f"COUNT(*) FILTER (WHERE stock_qty <= 0) stockouts, "
                    f"COUNT(*) FILTER (WHERE stock_qty > 0 AND stock_qty < 10) low "
                    f"FROM {tq}"
                )).fetchone()
                pharma = {
                    "stock_rows": int(r[0] or 0),
                    "sites": int(r[1] or 0),
                    "total_units": int(r[2] or 0),
                    "stock_value": float(r[3] or 0),
                    "stockouts": int(r[4] or 0),
                    "low_stock": int(r[5] or 0),
                    "top_category": top_category,
                    "table": stk,
                }
                kpis["sites"] = pharma["sites"]
                kpis["stock_units"] = pharma["total_units"]
                kpis["stock_value"] = pharma["stock_value"]
            except Exception:
                logger.exception("pharma signals %s", slug)
                pharma = {"top_category": top_category}

    return {
        "slug": slug,
        "schema": schema,
        "kpis": kpis,
        "pharma": pharma,
        "top_questions": top_questions,
        "recent_chats": recent_chats,
    }


# ---------------------------------------------------------------------------
# chemist card — clinical-field coverage + substitute web stats (P4)
# ---------------------------------------------------------------------------
@router.get("/{slug}/chemist")
def chemist(slug: str, request: Request):
    """Pharma-chemist coverage stats for the Dashboard card.

    Clinical-field coverage (how much of the catalog has composition/indication/
    dosage/side_effect), substitute-web size (drugs sharing a generic), and a
    distinct-generics/categories count. Pure relational, fail-soft.
    """
    user = _get_user(request)
    _check_access(user, slug)
    schema = _schema_for(slug)
    out: dict = {"ok": True, "schema": schema}

    with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        cat = _find_table(conn, schema, "generic_name")
        if not cat:
            return {"ok": False, "error": "no catalog table (generic_name)"}
        out["catalog_table"] = cat
        tq = f"{_q(schema)}.{_q(cat)}"

        def sc(sql):
            try:
                return conn.execute(text(sql)).scalar()
            except Exception:
                return None

        total = int(sc(f"SELECT COUNT(*) FROM {tq}") or 0)
        out["total_skus"] = total

        # clinical-field coverage %
        cov = {}
        for col in ("composition", "indication", "dosage", "side_effect", "generic_name", "category"):
            if _has_col(conn, schema, cat, col):
                n = int(sc(f"SELECT COUNT(*) FROM {tq} WHERE {_q(col)} IS NOT NULL AND {_q(col)} <> ''") or 0)
                cov[col] = {"filled": n, "pct": round(100.0 * n / total, 1) if total else 0.0}
        out["coverage"] = cov

        out["distinct_generics"] = int(sc(
            f"SELECT COUNT(DISTINCT generic_name) FROM {tq} "
            f"WHERE generic_name IS NOT NULL AND generic_name <> ''") or 0)
        out["distinct_categories"] = int(sc(
            f"SELECT COUNT(DISTINCT category) FROM {tq} "
            f"WHERE category IS NOT NULL AND category <> ''") or 0)

        # substitute web: drugs that have >=1 same-generic sibling
        out["drugs_with_substitutes"] = int(sc(
            f"SELECT COUNT(*) FROM {tq} a WHERE generic_name IS NOT NULL "
            f"AND generic_name <> '' AND EXISTS (SELECT 1 FROM {tq} b "
            f"WHERE b.generic_name = a.generic_name AND b.brand_name <> a.brand_name)") or 0)

        # latest clinical-eval accuracy (P3), if any
        try:
            r = conn.execute(text(
                "SELECT passed, total, pct, ran_at FROM dash.dash_chemist_eval "
                "WHERE project_slug=:s ORDER BY ran_at DESC LIMIT 1"
            ), {"s": slug}).fetchone()
            if r:
                out["accuracy"] = {"passed": int(r[0]), "total": int(r[1]),
                                   "pct": float(r[2]), "ran_at": str(r[3])}
        except Exception:
            pass

    return out


# ---------------------------------------------------------------------------
# clinical golden eval (P3) — held-out forward + inverse checks, accuracy %
# ---------------------------------------------------------------------------
def _build_golden(conn, schema: str) -> list:
    """Derive deterministic golden checks from the real catalog (data-grounded)."""
    cat = _find_table(conn, schema, "generic_name")
    tq = f"{_q(schema)}.{_q(cat)}"
    checks: list = []
    # forward + generic: drugs with full clinical data
    rows = conn.execute(text(
        f"SELECT brand_name, generic_name FROM {tq} "
        f"WHERE composition<>'' AND indication<>'' AND generic_name<>'' "
        f"AND brand_name<>'' ORDER BY brand_name LIMIT 8")).fetchall()
    for brand, gen in rows:
        checks.append({"type": "forward", "name": brand,
                       "expect": "profile has composition+indication"})
        checks.append({"type": "generic", "name": brand, "expect_generic": gen})
    # substitute: brands whose generic has >=2 brands
    subs = conn.execute(text(
        f"SELECT a.brand_name FROM {tq} a WHERE a.generic_name<>'' AND a.brand_name<>'' "
        f"AND EXISTS (SELECT 1 FROM {tq} b WHERE b.generic_name=a.generic_name "
        f"AND b.brand_name<>a.brand_name) ORDER BY a.brand_name LIMIT 6")).fetchall()
    for (brand,) in subs:
        checks.append({"type": "substitute", "name": brand, "expect_min": 1})
    # inverse: most common indication tokens (Burmese-safe — uses the real value)
    inds = conn.execute(text(
        f"SELECT indication FROM {tq} WHERE indication<>'' "
        f"GROUP BY indication ORDER BY COUNT(*) DESC LIMIT 4")).fetchall()
    for (ind,) in inds:
        token = (ind or "")[:18].strip()
        if token:
            checks.append({"type": "inverse", "symptom": token, "expect_min": 1})
    return checks


def run_chemist_eval(slug: str) -> dict:
    """Run the clinical golden eval + persist accuracy (no Request — daemon-safe).
    Forward (drug->profile), generic (drug->generic), substitute (drug->siblings),
    inverse (symptom->drug). Deterministic, real chemist tools, fail-soft per check."""
    schema = _schema_for(slug)

    from dash.tools.pharma_chemist_tool import (
        drug_profile, substitutes, indication_search)

    with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        cat = _find_table(conn, schema, "generic_name")
        if not cat:
            return {"ok": False, "error": "no catalog table"}
        golden = _build_golden(conn, schema)

    detail = []
    passed = 0
    for chk in golden:
        ok = False
        note = ""
        try:
            if chk["type"] == "forward":
                r = drug_profile(chk["name"], 1)
                res = (r.get("results") or [{}])[0] if r.get("ok") else {}
                ok = bool(res.get("composition")) and bool(res.get("indication"))
                note = f"comp={'y' if res.get('composition') else 'n'} ind={'y' if res.get('indication') else 'n'}"
            elif chk["type"] == "generic":
                r = drug_profile(chk["name"], 1)
                res = (r.get("results") or [{}])[0] if r.get("ok") else {}
                ok = bool(res.get("generic"))
                note = f"generic={res.get('generic', '')[:30]}"
            elif chk["type"] == "substitute":
                r = substitutes(chk["name"], False, 20)
                cnt = r.get("count", 0) if r.get("ok") else 0
                ok = cnt >= chk["expect_min"]
                note = f"{cnt} subs"
            elif chk["type"] == "inverse":
                r = indication_search(chk["symptom"], False, 20)
                cnt = r.get("count", 0) if r.get("ok") else 0
                ok = cnt >= chk["expect_min"]
                note = f"{cnt} hits"
        except Exception as e:
            note = f"err {str(e)[:40]}"
        if ok:
            passed += 1
        detail.append({"type": chk["type"],
                       "subject": chk.get("name") or chk.get("symptom"),
                       "pass": ok, "note": note})

    total = len(detail)
    pct = round(100.0 * passed / total, 1) if total else 0.0

    # persist (dash schema, write engine)
    try:
        import json as _json
        from db.session import get_write_engine
        weng = get_write_engine()
        with weng.begin() as wc:
            wc.execute(text(
                "CREATE TABLE IF NOT EXISTS dash.dash_chemist_eval ("
                "id BIGSERIAL PRIMARY KEY, project_slug TEXT NOT NULL, "
                "ran_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "passed INT, total INT, pct DOUBLE PRECISION, detail JSONB)"))
            wc.execute(text(
                "INSERT INTO dash.dash_chemist_eval (project_slug, passed, total, pct, detail) "
                "VALUES (:s, :p, :t, :pc, CAST(:d AS jsonb))"),
                {"s": slug, "p": passed, "t": total, "pc": pct, "d": _json.dumps(detail)})
    except Exception as e:
        logger.warning("chemist_eval persist failed: %s", e)

    return {"ok": True, "passed": passed, "total": total, "pct": pct, "detail": detail}


@router.post("/{slug}/chemist/eval")
def chemist_eval(slug: str, request: Request):
    """Run the clinical golden eval and store accuracy."""
    user = _get_user(request)
    _check_access(user, slug)
    return run_chemist_eval(slug)
