"""FK Inferer (Agent B — 2026-05-26)
================================================

Scan all tables in a project schema, find candidate foreign-key relationships
via NAME MATCH + NAME ALIAS + VALUE OVERLAP, persist the top-N matches into
``public.dash_column_meta.relationships`` (JSONB).

Schema (Agent A's migration 154) of ``public.dash_column_meta``:
    project_slug TEXT
    table_name   TEXT
    column_name  TEXT
    semantic_type TEXT
    description  TEXT
    relationships JSONB DEFAULT '[]'::jsonb
    -- + (project_slug, table_name, column_name) unique

Relationship JSON shape:
    {target_table, target_column, overlap_pct, kind}
    kind ∈ {"exact_name_match", "name_alias", "value_overlap"}

Rules:
- Reads via ``get_sql_engine()`` (information_schema is global + safe).
- Writes via ``db.session.get_write_engine()`` for ``public.dash_column_meta``.
- JSONB binding uses ``CAST(:x AS jsonb)``.
- Fail-soft per table; one bad table doesn't kill the batch.
- Skip self-joins (src table == target table).
- Skip lineage columns from ``dash.utils.column_metadata.LINEAGE_COLUMNS``
  (and ``is_lineage_column`` helper for ``_source_*`` / ``_period*``).
- Cap rows scanned to 100,000 per table via LIMIT subquery.
- Keep matches with ``overlap_pct >= 0.5``.
- Persist up to 50 relationships per column (sorted by overlap_pct DESC).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from db.session import get_sql_engine, get_write_engine
from dash.utils.column_metadata import LINEAGE_COLUMNS, is_lineage_column

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MAX_ROWS_PER_TABLE = 100_000
OVERLAP_THRESHOLD = 0.5
MAX_RELS_PER_COLUMN = 50

# Plural/singular alias helpers for name_alias matching.
_PLURAL_SUFFIXES = ("ies", "es", "s")


def _singularize(name: str) -> str:
    n = name.lower()
    if n.endswith("ies") and len(n) > 3:
        return n[:-3] + "y"
    if n.endswith("ses") and len(n) > 3:
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss") and len(n) > 1:
        return n[:-1]
    return n


def _pluralize_candidates(name: str) -> list[str]:
    n = name.lower()
    cands = {n}
    if n.endswith("y") and len(n) > 1:
        cands.add(n[:-1] + "ies")
    cands.add(n + "s")
    cands.add(n + "es")
    return list(cands)


def _strip_id_suffix(col: str) -> str | None:
    """For column ``customer_id`` → return ``customer`` (the inferred entity).

    Returns None if no recognizable id-suffix pattern.
    """
    c = col.lower()
    for suf in ("_id", "_uid", "_uuid", "_code", "_key", "_no", "_num"):
        if c.endswith(suf) and len(c) > len(suf):
            return c[: -len(suf)]
    return None


def _candidate_target_tables_for_alias(col_name: str, all_tables: list[str]) -> list[tuple[str, str]]:
    """For a column like ``customer_id``, find target tables named ``customer``
    or ``customers``, and yield (target_table, target_pk_column) guesses.

    PK guesses: ``id``, ``<entity>_id``, plus the exact col_name itself.
    """
    base = _strip_id_suffix(col_name)
    if not base:
        return []
    base_singular = _singularize(base)
    name_candidates = set()
    for n in (base, base_singular):
        for p in _pluralize_candidates(n):
            name_candidates.add(p)

    out: list[tuple[str, str]] = []
    tables_lower = {t.lower(): t for t in all_tables}
    for cand in name_candidates:
        if cand in tables_lower:
            tgt = tables_lower[cand]
            # Guess PK columns
            for pk_guess in ("id", f"{base_singular}_id", col_name.lower()):
                out.append((tgt, pk_guess))
    # Dedupe preserving order
    seen: set[tuple[str, str]] = set()
    uniq: list[tuple[str, str]] = []
    for pair in out:
        if pair not in seen:
            seen.add(pair)
            uniq.append(pair)
    return uniq


def _safe_ident(name: str) -> str:
    """Quote an identifier safely (schema/table/column)."""
    # Only allow [a-zA-Z0-9_]; raise on anything else
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""):
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def _load_project_schema(conn: Connection, project_slug: str) -> dict[str, list[dict[str, Any]]]:
    """Return {table_name: [{column_name, data_type, ordinal_position}]}.

    Skips lineage columns and obvious skip-by-type.
    """
    sql = text(
        """
        SELECT table_name, column_name, data_type, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = :schema
        ORDER BY table_name, ordinal_position
        """
    )
    rows = conn.execute(sql, {"schema": project_slug}).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        tbl = r[0]
        col = r[1]
        dtype = r[2]
        if not col:
            continue
        if is_lineage_column(col) or col in LINEAGE_COLUMNS:
            continue
        out.setdefault(tbl, []).append(
            {"column_name": col, "data_type": dtype, "ordinal_position": r[3]}
        )
    return out


def _build_name_match_candidates(
    schema_map: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, str], list[tuple[str, str, str]]]:
    """For each (src_table, src_col), list candidate (target_table, target_col, kind).

    kind ∈ {"exact_name_match", "name_alias"}.
    """
    by_colname: dict[str, list[tuple[str, str]]] = {}
    for tbl, cols in schema_map.items():
        for c in cols:
            by_colname.setdefault(c["column_name"].lower(), []).append((tbl, c["column_name"]))

    all_tables = list(schema_map.keys())

    candidates: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for src_table, cols in schema_map.items():
        for c in cols:
            src_col = c["column_name"]
            key = (src_table, src_col)
            out: list[tuple[str, str, str]] = []

            # (a) Exact same column name in OTHER tables
            for tgt_table, tgt_col in by_colname.get(src_col.lower(), []):
                if tgt_table == src_table:
                    continue
                out.append((tgt_table, tgt_col, "exact_name_match"))

            # (b) Name alias: customer_id -> customers.id / customer.id / customers.customer_id
            for tgt_table, tgt_col_guess in _candidate_target_tables_for_alias(src_col, all_tables):
                if tgt_table == src_table:
                    continue
                # Only keep if target actually has the guessed column
                tgt_cols = {col_def["column_name"].lower(): col_def["column_name"] for col_def in schema_map.get(tgt_table, [])}
                if tgt_col_guess.lower() in tgt_cols:
                    real_tgt_col = tgt_cols[tgt_col_guess.lower()]
                    out.append((tgt_table, real_tgt_col, "name_alias"))

            if out:
                # Dedupe by (tgt_table, tgt_col), preferring "exact_name_match"
                best: dict[tuple[str, str], str] = {}
                for tgt_table, tgt_col, kind in out:
                    pair = (tgt_table, tgt_col)
                    if pair not in best or (
                        kind == "exact_name_match" and best[pair] != "exact_name_match"
                    ):
                        best[pair] = kind
                candidates[key] = [(t, c2, k) for (t, c2), k in best.items()]
    return candidates


def _measure_overlap(
    conn: Connection,
    schema: str,
    src_table: str,
    src_col: str,
    tgt_table: str,
    tgt_col: str,
) -> float | None:
    """Compute overlap_pct = distinct(src.col ∈ tgt.col) / distinct(src.col).

    Caps rows scanned to MAX_ROWS_PER_TABLE per side via LIMIT subqueries.
    Returns None on error.
    """
    try:
        s = _safe_ident(schema)
        st = _safe_ident(src_table)
        sc = _safe_ident(src_col)
        tt = _safe_ident(tgt_table)
        tc = _safe_ident(tgt_col)
    except ValueError:
        return None

    sql = text(
        f"""
        WITH src AS (
            SELECT {sc} AS v
            FROM {s}.{st}
            WHERE {sc} IS NOT NULL
            LIMIT {MAX_ROWS_PER_TABLE}
        ),
        tgt AS (
            SELECT DISTINCT {tc} AS v
            FROM {s}.{tt}
            WHERE {tc} IS NOT NULL
            LIMIT {MAX_ROWS_PER_TABLE}
        ),
        src_d AS (
            SELECT DISTINCT v FROM src
        )
        SELECT
            COUNT(*) FILTER (WHERE v IN (SELECT v FROM tgt))::float
              / NULLIF(COUNT(*), 0)::float AS overlap_pct
        FROM src_d
        """
    )
    try:
        row = conn.execute(sql).fetchone()
        if row is None:
            return None
        val = row[0]
        if val is None:
            return None
        return float(val)
    except Exception as e:
        logger.debug(
            "fk_inferer overlap measure failed src=%s.%s tgt=%s.%s: %s",
            src_table, src_col, tgt_table, tgt_col, e,
        )
        return None


def _upsert_relationships(
    write_conn: Connection,
    project_slug: str,
    src_table: str,
    src_col: str,
    rels: list[dict[str, Any]],
) -> None:
    """Upsert relationships JSONB into public.dash_column_meta.

    Replaces the entire ``relationships`` array for the (project, table, column).
    """
    if not rels:
        return
    payload = json.dumps(rels)
    sql = text(
        """
        INSERT INTO public.dash_column_meta
            (project_slug, table_name, column_name, relationships)
        VALUES
            (:slug, :tbl, :col, CAST(:rels AS jsonb))
        ON CONFLICT (project_slug, table_name, column_name)
        DO UPDATE SET relationships = EXCLUDED.relationships
        """
    )
    write_conn.execute(
        sql,
        {"slug": project_slug, "tbl": src_table, "col": src_col, "rels": payload},
    )


def infer_fks(project_slug: str) -> dict[str, Any]:
    """Scan project schema and persist inferred FK relationships.

    Returns:
        {tables_scanned, columns_scanned, relationships_found, errors}
    """
    t0 = time.time()
    result: dict[str, Any] = {
        "project_slug": project_slug,
        "tables_scanned": 0,
        "columns_scanned": 0,
        "relationships_found": 0,
        "errors": [],
        "elapsed_ms": 0,
    }

    if not project_slug or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", project_slug):
        result["errors"].append(f"invalid project_slug: {project_slug!r}")
        return result

    read_engine: Engine = get_sql_engine()
    try:
        write_engine: Engine = get_write_engine()
    except Exception as e:
        result["errors"].append(f"get_write_engine failed: {e}")
        return result

    # Step 1: load schema via shared read engine
    try:
        with read_engine.connect() as rconn:
            schema_map = _load_project_schema(rconn, project_slug)
    except Exception as e:
        result["errors"].append(f"load schema failed: {e}")
        return result

    if not schema_map:
        result["errors"].append(f"no tables found in schema {project_slug!r}")
        return result

    result["tables_scanned"] = len(schema_map)
    result["columns_scanned"] = sum(len(cols) for cols in schema_map.values())

    # Step 2: build name-match + alias candidates (cheap, in-memory)
    name_candidates = _build_name_match_candidates(schema_map)

    # Step 3: per src column → measure overlap for each candidate
    # Use a fresh autocommit connection on the READ engine for cross-table queries.
    rels_per_column: dict[tuple[str, str], list[dict[str, Any]]] = {}
    try:
        with read_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as rconn:
            for (src_table, src_col), cands in name_candidates.items():
                col_results: list[dict[str, Any]] = []
                for tgt_table, tgt_col, kind in cands:
                    if tgt_table == src_table:
                        continue
                    overlap = _measure_overlap(
                        rconn, project_slug, src_table, src_col, tgt_table, tgt_col
                    )
                    if overlap is None:
                        continue
                    if overlap < OVERLAP_THRESHOLD:
                        # Demote: skip unless an exact_name_match w/ marginal overlap?
                        # Per spec keep matches >= 0.5 only.
                        continue
                    final_kind = kind
                    # If the only signal we have is overlap (e.g. kind was name match but
                    # overlap is very strong), keep the more specific source.
                    col_results.append(
                        {
                            "target_table": tgt_table,
                            "target_column": tgt_col,
                            "overlap_pct": round(overlap, 4),
                            "kind": final_kind,
                        }
                    )
                if col_results:
                    col_results.sort(key=lambda r: r["overlap_pct"], reverse=True)
                    if len(col_results) > MAX_RELS_PER_COLUMN:
                        col_results = col_results[:MAX_RELS_PER_COLUMN]
                    rels_per_column[(src_table, src_col)] = col_results
    except Exception as e:
        result["errors"].append(f"overlap scan failed: {e}")
        # fall through — persist whatever we have

    # Step 4: persist via write engine (one autocommit conn for the batch)
    persisted = 0
    try:
        with write_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as wconn:
            for (src_table, src_col), rels in rels_per_column.items():
                try:
                    _upsert_relationships(wconn, project_slug, src_table, src_col, rels)
                    persisted += len(rels)
                except Exception as e:
                    result["errors"].append(
                        f"persist failed for {src_table}.{src_col}: {e}"
                    )
    except Exception as e:
        result["errors"].append(f"open write conn failed: {e}")

    result["relationships_found"] = persisted
    result["elapsed_ms"] = int((time.time() - t0) * 1000)
    return result


__all__ = ["infer_fks"]
