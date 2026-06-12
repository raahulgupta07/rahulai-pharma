"""Data Source API — read-only aggregation for the redesigned Data Source page.

One call, everything the UI needs:
  GET /api/projects/{slug}/datasource
    -> { summary, tables:[ {meta, trained, quality, store, links, preview} ] }

Fail-soft: every sub-query is wrapped; a broken table never 500s the page.
Mirrors app/training_api.py auth + NullPool engine pattern.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, inspect, text
from sqlalchemy.pool import NullPool

from db import db_url

router = APIRouter(prefix="/api/projects", tags=["DataSource"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)
logger = logging.getLogger(__name__)

# columns whose negative / zero values are a validity smell (pharma stock/price)
_NONNEG_HINTS = ("qty", "quantity", "stock", "cost", "price", "amount", "balance", "value")
# columns that look like a store/site key (3-tier scope)
_STORE_HINTS = ("site_code", "store_id", "store_code", "site_id", "branch", "outlet")


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


def _iso(v):
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else (str(v) if v else None)


def _q(name: str) -> str:
    """Quote an identifier safely."""
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# per-table builders (each fail-soft)
# ---------------------------------------------------------------------------
def _columns(insp, schema: str, table: str) -> list[dict]:
    try:
        cols = insp.get_columns(table, schema=schema)
        return [{"name": c["name"], "type": str(c.get("type", "")).lower()} for c in cols]
    except Exception:
        return []


def _row_count(conn, schema: str, table: str) -> int:
    try:
        return int(conn.execute(text(f'SELECT COUNT(*) FROM {_q(schema)}.{_q(table)}')).scalar() or 0)
    except Exception:
        return 0


def _quality(conn, schema: str, table: str, columns: list[dict], rows: int) -> dict:
    """Compute a 5-dimension quality scorecard with one or two scans."""
    out = {"score": 0, "completeness": 0, "validity": 0, "uniqueness": 0,
           "consistency": 100, "notes": []}
    if rows == 0 or not columns:
        return out
    colnames = [c["name"] for c in columns]
    numeric = [c["name"] for c in columns
               if any(t in c["type"] for t in ("int", "numeric", "double", "real", "float", "decimal"))]
    try:
        # completeness — null counts per column in one pass
        null_exprs = ", ".join(
            f'COUNT(*) FILTER (WHERE {_q(c)} IS NULL) AS {_q("n_" + str(i))}'
            for i, c in enumerate(colnames))
        nrow = conn.execute(text(
            f"SELECT {null_exprs} FROM {_q(schema)}.{_q(table)}")).fetchone()
        total_cells = rows * len(colnames)
        nulls = sum(int(x or 0) for x in (nrow or []))
        completeness = round(100 * (1 - nulls / total_cells)) if total_cells else 100
        out["completeness"] = max(0, min(100, completeness))
        # name the worst null offenders
        worst = sorted(
            [(colnames[i], int(nrow[i] or 0)) for i in range(len(colnames))],
            key=lambda x: -x[1])[:2]
        for cn, nn in worst:
            if nn:
                out["notes"].append(f"{nn:,} null {cn}")
    except Exception as e:
        logger.debug("quality completeness %s.%s: %s", schema, table, e)
        out["completeness"] = 100

    try:
        # uniqueness — duplicate full rows
        dup = conn.execute(text(
            f"SELECT COUNT(*) - COUNT(*) FILTER (WHERE rn = 1) FROM ("
            f"SELECT ROW_NUMBER() OVER (PARTITION BY {', '.join(_q(c) for c in colnames)}) rn "
            f"FROM {_q(schema)}.{_q(table)}) z")).scalar()
        dup = int(dup or 0)
        out["uniqueness"] = round(100 * (1 - dup / rows)) if rows else 100
        if dup:
            out["notes"].append(f"{dup:,} duplicate rows")
    except Exception:
        out["uniqueness"] = 100

    try:
        # validity — negative values in non-negative numeric columns
        neg_cols = [c for c in numeric if any(h in c.lower() for h in _NONNEG_HINTS)]
        bad = 0
        for c in neg_cols:
            n = conn.execute(text(
                f"SELECT COUNT(*) FROM {_q(schema)}.{_q(table)} WHERE {_q(c)} < 0")).scalar()
            n = int(n or 0)
            bad += n
            if n:
                out["notes"].append(f"{n:,} negative {c}")
        out["validity"] = round(100 * (1 - bad / rows)) if rows else 100
    except Exception:
        out["validity"] = 100

    try:
        # P2 — an ID/code column collapsed to ≤1 distinct value across many rows is
        # a destroyed join key (e.g. article_code exported as "1E+12" → all identical).
        id_cols = [c for c in colnames
                   if re.search(r'(^|_)(id|code|barcode|sku|ean|upc|gtin)($|_|no$)', c, re.I)]
        for c in id_cols:
            nd = int(conn.execute(text(
                f"SELECT COUNT(DISTINCT {_q(c)}) FROM {_q(schema)}.{_q(table)}")).scalar() or 0)
            if rows >= 50 and nd <= 1:
                out["notes"].append(
                    f"⚠ '{c}' has only {nd} distinct value across {rows:,} rows — "
                    f"likely a corrupt/lost key (check for scientific-notation in the source file)")
                out["consistency"] = min(out["consistency"], 40)
    except Exception as e:
        logger.debug("quality id-collapse %s.%s: %s", schema, table, e)

    dims = [out["completeness"], out["validity"], out["uniqueness"], out["consistency"]]
    out["score"] = round(sum(dims) / len(dims))
    return out


def _preview(conn, schema: str, table: str, columns: list[dict], limit: int = 5) -> list[list]:
    try:
        rows = conn.execute(text(
            f"SELECT * FROM {_q(schema)}.{_q(table)} LIMIT :n"), {"n": limit}).fetchall()
        def _cell(v):
            if v is None:
                return ""
            s = str(v)[:40]
            # strip control chars (newlines/tabs in data break the JSON client + UI)
            return "".join(ch if ch >= " " else " " for ch in s)
        return [[_cell(v) for v in r] for r in rows]
    except Exception:
        return []


def _store_scope(conn, schema: str, table: str, columns: list[dict]) -> dict:
    colnames = {c["name"].lower(): c["name"] for c in columns}
    key = next((colnames[h] for h in _STORE_HINTS if h in colnames), None)
    if not key:
        return {"keyed": False, "column": None, "stores": []}
    try:
        rows = conn.execute(text(
            f"SELECT DISTINCT {_q(key)}::text FROM {_q(schema)}.{_q(table)} "
            f"WHERE {_q(key)} IS NOT NULL LIMIT 12")).fetchall()
        try:
            n = int(conn.execute(text(
                f"SELECT COUNT(DISTINCT {_q(key)}) FROM {_q(schema)}.{_q(table)} "
                f"WHERE {_q(key)} IS NOT NULL")).scalar() or 0)
        except Exception:
            n = len(rows)
        return {"keyed": True, "column": key, "stores": [r[0] for r in rows], "site_count": n}
    except Exception:
        return {"keyed": True, "column": key, "stores": [], "site_count": 0}


# ---------------------------------------------------------------------------
# training signals
# ---------------------------------------------------------------------------
def _trained_map(conn, slug: str) -> dict[str, dict]:
    """Per-table trained status. A table is 'trained' once the pipeline has
    written its profile to dash_table_metadata (the per-table training artifact).
    qa_count comes from dash_training_qa. (dash_training_steps only holds global
    tail steps — KG/vectors — never per-table scope rows, so it's not usable here.)"""
    out: dict[str, dict] = {}
    try:
        rows = conn.execute(text(
            "SELECT table_name, updated_at, metadata FROM public.dash_table_metadata "
            "WHERE project_slug=:s"), {"s": slug}).fetchall()
        for r in rows:
            out.setdefault(r[0], {})["trained"] = True
            out[r[0]]["last_at"] = _iso(r[1])
            meta = r[2]
            if isinstance(meta, str):
                try:
                    import json as _json
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            out[r[0]]["meta"] = meta if isinstance(meta, dict) else {}
    except Exception as e:
        logger.debug("trained_map meta %s: %s", slug, e)
    try:
        rows = conn.execute(text(
            "SELECT table_name, count(*) FROM public.dash_training_qa "
            "WHERE project_slug=:s GROUP BY table_name"), {"s": slug}).fetchall()
        for r in rows:
            out.setdefault(r[0], {})["qa_count"] = int(r[1] or 0)
    except Exception as e:
        logger.debug("trained_map qa %s: %s", slug, e)
    return out


# ---------------------------------------------------------------------------
# table origin — uploaded vs AI/LLM-created vs derived
# ---------------------------------------------------------------------------
# Derived = pre-joined/denormalized tables the pipeline builds (never uploaded,
# never trained as a source). AI = tables an LLM produced (catalog enrichment:
# carries model + suggested_value columns). Everything else = uploaded source.
_DERIVED_NAMES = {"shop_flat"}


def _classify_origin(name: str, meta: dict, cols: list[dict]) -> str:
    """Return 'uploaded' | 'ai' | 'derived'.

    Explicit metadata.origin wins (set at creation, durable). Falls back to a
    heuristic so existing tables classify with no retrain."""
    o = (meta or {}).get("origin")
    if o in ("uploaded", "ai", "derived"):
        return o
    if name in _DERIVED_NAMES:
        return "derived"
    colnames = {c["name"].lower() for c in cols}
    if name == "catalog_enrichment" or ({"model", "suggested_value"} <= colnames):
        return "ai"
    return "uploaded"


def _counts(conn, slug: str) -> dict:
    def one(sql, **kw):
        try:
            return int(conn.execute(text(sql), {"s": slug, **kw}).scalar() or 0)
        except Exception:
            return 0
    return {
        "qa": one("SELECT COUNT(*) FROM public.dash_training_qa WHERE project_slug=:s"),
        "vectors": one("SELECT COUNT(*) FROM dash.dash_vectors WHERE project_slug=:s"),
        "triples": one("SELECT COUNT(*) FROM public.dash_knowledge_triples WHERE project_slug=:s"),
    }


def _active_run(conn, slug: str) -> dict | None:
    try:
        r = conn.execute(text(
            "SELECT id, status, current_step, stage_progress, started_at "
            "FROM public.dash_training_runs WHERE project_slug=:s "
            "AND status IN ('running','queued') ORDER BY started_at DESC LIMIT 1"
        ), {"s": slug}).fetchone()
        if not r:
            return None
        return {"id": r[0], "status": r[1], "current_step": r[2],
                "stage_progress": r[3], "started_at": _iso(r[4])}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# endpoint
# ---------------------------------------------------------------------------
@router.get("/{slug}/datasource")
def datasource(slug: str, request: Request, quality: bool = True, preview: bool = True):
    """Aggregated Data Source view. quality/preview can be disabled for speed."""
    user = _get_user(request)
    _check_access(user, slug)  # raises 401/403 — auth stays strict

    schema = _schema_for(slug)
    # Past auth: never 500 this page. The frontend leaves the table list on
    # "loading..." forever when this endpoint errors, so any build failure (fresh
    # DB, missing optional table, etc.) returns an empty-but-valid payload and the
    # view falls back to its empty state instead of sticking.
    try:
        return _build_datasource(slug, schema, quality, preview)
    except Exception:
        logger.exception("datasource: build failed for %s — returning empty payload", slug)
        return {"slug": slug, "schema": schema, "tables": [], "degraded": True,
                "summary": {"tables": 0, "rows": 0,
                            "origins": {"uploaded": 0, "ai": 0, "derived": 0},
                            "sites": 0, "join_warnings": [], "trained_tables": 0,
                            "trained_pct": 0, "qa": 0, "vectors": 0, "triples": 0,
                            "issues": 0, "is_training": False, "active_run": None}}


def _build_datasource(slug: str, schema: str, quality: bool, preview: bool):
    engine = _sa_create_engine(db_url, poolclass=NullPool)
    insp = inspect(engine)
    try:
        table_names = [t for t in insp.get_table_names(schema=schema)]
    except Exception:
        table_names = []

    tables: list[dict] = []
    col_index: dict[str, set] = {}  # table -> set(columns) for link detection
    total_rows = 0

    # AUTOCOMMIT so a missing optional table (e.g. dash_knowledge_triples) that
    # errors one counter query doesn't abort the shared transaction and silently
    # zero out every subsequent COUNT(*).
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        trained = _trained_map(conn, slug)
        counts = _counts(conn, slug)
        active = _active_run(conn, slug)

        for t in table_names:
            cols = _columns(insp, schema, t)
            rows = _row_count(conn, schema, t)
            total_rows += rows
            col_index[t] = {c["name"].lower() for c in cols}
            tr = trained.get(t, {})
            entry: dict[str, Any] = {
                "name": t,
                "rows": rows,
                "cols": len(cols),
                "columns": cols,
                "origin": _classify_origin(t, tr.get("meta", {}), cols),
                "trained": bool(tr.get("trained")),
                "last_trained": tr.get("last_at"),
                "qa_count": tr.get("qa_count", 0),
                "store": _store_scope(conn, schema, t, cols),
                "quality": _quality(conn, schema, t, cols, rows) if quality else None,
                "preview": _preview(conn, schema, t, cols) if preview else [],
            }
            tables.append(entry)

        # links — tables sharing a column name (cheap, in-memory)
        _join_warnings: list[str] = []
        _overlap_cache: dict = {}
        def _join_overlap(ta: str, ca: str, tb: str, cb: str) -> int:
            """Sample distinct-value overlap on a shared key. -1 on error."""
            k = tuple(sorted([(ta, ca), (tb, cb)]))
            if k in _overlap_cache:
                return _overlap_cache[k]
            try:
                n = int(conn.execute(text(
                    f"SELECT COUNT(*) FROM (SELECT DISTINCT {_q(ca)}::text v FROM {_q(schema)}.{_q(ta)} "
                    f"WHERE {_q(ca)} IS NOT NULL LIMIT 5000) x "
                    f"JOIN (SELECT DISTINCT {_q(cb)}::text v FROM {_q(schema)}.{_q(tb)} "
                    f"WHERE {_q(cb)} IS NOT NULL) y ON x.v = y.v")).scalar() or 0)
            except Exception:
                n = -1
            _overlap_cache[k] = n
            return n
        _id_re = re.compile(r'(^|_)(id|code|barcode|sku|ean|upc|gtin)($|_|no$)', re.I)
        for e in tables:
            mine = col_index.get(e["name"], set())
            links = []
            for other, ocols in col_index.items():
                if other == e["name"]:
                    continue
                shared = mine & ocols
                # ignore generic id/created columns
                shared = {c for c in shared if c not in ("id", "created_at", "updated_at")}
                if shared:
                    link = {"table": other, "on": sorted(shared)[:3]}
                    # P2 — for an ID-like shared key, confirm the values actually overlap.
                    # 0 overlap = a broken join (the whole "by drug/by site" class fails).
                    for sc in sorted(shared):
                        if _id_re.search(sc):
                            ov = _join_overlap(e["name"], sc, other, sc)
                            if ov == 0:
                                link["join"] = "broken"
                                _join_warnings.append(
                                    f"⚠ '{e['name']}' and '{other}' share key '{sc}' but 0 values match — "
                                    f"join broken (e.g. scientific-notation/type mismatch in the source)")
                            break
                    links.append(link)
            e["links"] = links[:4]

    trained_tables = sum(1 for e in tables if e["trained"])
    _join_warnings = list(dict.fromkeys(locals().get("_join_warnings") or []))
    issues = sum(1 for e in tables if e.get("quality") and e["quality"]["score"] < 80) + len(_join_warnings)

    sites = max((e.get("store", {}).get("site_count", 0) for e in tables), default=0)
    origins = {"uploaded": 0, "ai": 0, "derived": 0}
    for e in tables:
        origins[e.get("origin", "uploaded")] = origins.get(e.get("origin", "uploaded"), 0) + 1
    summary = {
        "tables": len(tables),
        "rows": total_rows,
        "origins": origins,
        "sites": sites,
        "join_warnings": _join_warnings,
        "trained_tables": trained_tables,
        "trained_pct": round(100 * trained_tables / len(tables)) if tables else 0,
        "qa": counts["qa"],
        "vectors": counts["vectors"],
        "triples": counts["triples"],
        "issues": issues,
        "is_training": active is not None,
        "active_run": active,
    }
    return {"slug": slug, "schema": schema, "summary": summary, "tables": tables}
