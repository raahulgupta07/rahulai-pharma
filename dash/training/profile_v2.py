"""
Advanced 10M-row SQL profiler for Dash (v2).
================================================

Replacement for `app/upload.py::_sql_profile_columns` per-column loop. Uses:
  * ONE combined SELECT scan for COUNT/MIN/MAX/non-null per column (batched at 200 cols)
  * `pg_stats` system view for n_distinct + top_values (most_common_vals/freqs)
  * TABLESAMPLE SYSTEM(1) REPEATABLE(42) for percentile computation on >1M-row tables
  * PERCENTILE_DISC (cheaper than CONT) for measure stats
  * Cheap variant detection (lower(trim(col::text))) on low-cardinality dimension cols

Target: profile 10M rows × 50 cols in <10s (down from ~250s sequential).

Public API — FROZEN CONTRACT (other agent depends on this shape):

    profile_table_v2(slug: str, table: str) -> dict

Returns:
    {
      "total_rows": int,
      "scan_elapsed_ms": int,
      "scanned_at": iso_timestamp,
      "columns": [
        {
          "name": str,
          "pg_type": str,
          "role": "id"|"state"|"dimension"|"measure"|"temporal"|"text",
          "n_distinct_approx": int,
          "n_distinct_exact": int | None,
          "null_pct": float,             # 0-100
          "top_values": [{"v": str, "freq_pct": float}],
          "stats": {                       # only for measure/temporal, else None
            "min": str|None, "max": str|None,
            "mean": float|None, "stddev": float|None,
            "p25": float|None, "p50": float|None,
            "p75": float|None, "p99": float|None,
          } | None,
          "variants_detected": bool,
          "variant_warning": str | None,
          "unit": str | None,
        }, ...
      ]
    }

Kill switch: env `PROFILE_V2_DISABLED=1` → returns `{"disabled": True}`.

Persistence: `_persist_profile` upserts into `public.dash_table_metadata.metadata`
under JSONB key `profile_v2`. Uses `get_write_engine()` (public-schema write rule)
and `CAST(:p AS jsonb)` (PgBouncer/SQLAlchemy named-param rule).

All steps fail-soft: any sub-step exception is logged and a partial profile is
returned rather than raising.

Style mirrors `dash/ingest/copy_stream.py` (helpers, logger, sqlalchemy `text()`).
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────────────
# Tunables
# ───────────────────────────────────────────────────────────────────────────

MAX_COLS_PER_SCAN = 200                  # PG planner guard — batch beyond this
TOP_VALUES_CAP = 10                      # frontend renders ≤10
LOW_CARD_THRESHOLD = 500                 # below = candidate for variant detect
TABLESAMPLE_ROW_THRESHOLD = 1_000_000    # use TABLESAMPLE above this
TABLESAMPLE_PCT = 1                      # SYSTEM(1) ≈ 1% pages
TABLESAMPLE_SEED = 42                    # deterministic
NUMERIC_PG_TYPES = {
    "integer", "bigint", "smallint",
    "numeric", "double precision", "real",
}
TEMPORAL_PG_TYPES = {
    "date", "timestamp without time zone", "timestamp with time zone",
    "time without time zone", "time with time zone",
}
STATE_NAME_RE = re.compile(
    r"(status|state|stage|phase|kind|type|outcome|verdict|"
    r"category|tier|level|grade|severity|priority)",
    re.IGNORECASE,
)
LINEAGE_PREFIX = "_source_"

_SAFE_IDENT_RE = re.compile(r"[^a-z0-9_]")


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _safe_ident(name: str, maxlen: int = 63) -> str:
    """Postgres-safe identifier: lowercase, alnum + underscore, ≤63 chars.

    NOTE: do NOT strip("_"). Project slugs end in '_' by convention and must
    match dash_projects.schema_name verbatim. Stripping caused silent schema
    mismatch (table not found, 0 rows profiled). Bug class — fixed 2026-05-27.
    """
    s = _SAFE_IDENT_RE.sub("_", (name or "").lower())
    if not s or s.strip("_") == "":
        s = "col"
    if s[0].isdigit():
        s = f"c_{s}"
    return s[:maxlen]


def _quote_ident(name: str) -> str:
    """Quote a Postgres identifier (handles embedded double quotes)."""
    return '"' + (name or "").replace('"', '""') + '"'


def _qualified(slug: str, table: str) -> str:
    """Return fully-quoted `\"schema\".\"table\"` for the given project."""
    schema = _safe_ident(slug)
    tbl = _safe_ident(table)
    return f'{_quote_ident(schema)}.{_quote_ident(tbl)}'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ───────────────────────────────────────────────────────────────────────────
# pg_stats lookups
# ───────────────────────────────────────────────────────────────────────────

def _read_pg_stats(slug: str, table: str) -> dict[str, dict]:
    """Query `pg_stats` and return per-column stats dict.

    Returns: `{col_name: {n_distinct: int|float, most_common_vals: list, most_common_freqs: list}}`

    Notes:
      - PG omits most_common_vals/freqs for very high-cardinality columns → None.
      - n_distinct can be negative (negative fraction of total rows) — caller
        converts via abs(n) * total_rows.
    """
    schema = _safe_ident(slug)
    tbl = _safe_ident(table)
    out: dict[str, dict] = {}
    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT attname, n_distinct, most_common_vals, most_common_freqs "
                    "FROM pg_stats WHERE schemaname = :s AND tablename = :t"
                ),
                {"s": schema, "t": tbl},
            ).fetchall()
            for r in rows:
                col = r[0]
                # most_common_vals comes back as anyarray text repr like '{a,b,c}'
                mcv_raw = r[2]
                mcv: list = []
                if mcv_raw is not None:
                    if isinstance(mcv_raw, list):
                        mcv = list(mcv_raw)
                    else:
                        # parse '{a,b,c}' text repr conservatively
                        s = str(mcv_raw)
                        if s.startswith("{") and s.endswith("}"):
                            inner = s[1:-1]
                            mcv = [x.strip().strip('"') for x in inner.split(",") if x.strip()]
                mcf_raw = r[3]
                mcf: list[float] = []
                if mcf_raw is not None:
                    if isinstance(mcf_raw, list):
                        mcf = [float(x) for x in mcf_raw]
                    else:
                        s = str(mcf_raw)
                        if s.startswith("{") and s.endswith("}"):
                            inner = s[1:-1]
                            for x in inner.split(","):
                                x = x.strip()
                                if not x:
                                    continue
                                try:
                                    mcf.append(float(x))
                                except Exception:
                                    pass
                out[col] = {
                    "n_distinct": float(r[1]) if r[1] is not None else 0.0,
                    "most_common_vals": mcv,
                    "most_common_freqs": mcf,
                }
    except Exception as e:
        logger.warning("profile_v2: pg_stats read failed for %s.%s: %s", schema, tbl, e)
    return out


def _run_analyze_if_empty(
    slug: str, table: str, pg_stats_result: dict[str, dict],
    column_names: list[str],
) -> bool:
    """If pg_stats has zero coverage for the table's columns, run `ANALYZE`
    once (one-time cost) and signal the caller to re-read pg_stats.

    Returns True iff ANALYZE was executed.
    """
    # Coverage check: do we have stats for at least one of the table's columns?
    have_any = any(c in pg_stats_result for c in column_names)
    if have_any:
        return False

    qualified = _qualified(slug, table)
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.begin() as conn:
            conn.execute(text(f"ANALYZE {qualified}"))
        logger.info("profile_v2: ran ANALYZE on %s (pg_stats was empty)", qualified)
        return True
    except Exception as e:
        logger.warning("profile_v2: ANALYZE failed for %s: %s — proceeding without pg_stats", qualified, e)
        return False


# ───────────────────────────────────────────────────────────────────────────
# Combined scan (one SQL, batched if >MAX_COLS_PER_SCAN)
# ───────────────────────────────────────────────────────────────────────────

def _build_combined_query(qualified_table: str, columns: list[tuple[str, str]]) -> str:
    """Build ONE SQL statement that returns per-column COUNT + MIN + MAX.

    `columns` is `[(name, pg_type), ...]`. Skips lineage cols (caller already filtered).
    Caller is responsible for batching at MAX_COLS_PER_SCAN.

    Output column order (per input col `c`):
        nn_{c}    — COUNT(c) non-null
        min_{c}   — MIN(c)::text
        max_{c}   — MAX(c)::text
    Plus a single leading `total_rows = COUNT(*)`.
    """
    parts: list[str] = ["COUNT(*) AS total_rows"]
    for (name, _pg_type) in columns:
        safe = _quote_ident(name)
        alias = _safe_ident(name)
        parts.append(f"COUNT({safe}) AS nn_{alias}")
        parts.append(f"MIN({safe})::text AS min_{alias}")
        parts.append(f"MAX({safe})::text AS max_{alias}")
    select_list = ",\n  ".join(parts)
    return f"SELECT\n  {select_list}\nFROM {qualified_table}"


def _run_combined_scan(
    qualified_table: str, columns: list[tuple[str, str]]
) -> tuple[int, dict[str, dict]]:
    """Run combined scan in batches; return (total_rows, {col → {non_null, min_text, max_text}})."""
    total_rows = 0
    per_col: dict[str, dict] = {}
    if not columns:
        return 0, per_col

    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            for start in range(0, len(columns), MAX_COLS_PER_SCAN):
                batch = columns[start:start + MAX_COLS_PER_SCAN]
                sql = _build_combined_query(qualified_table, batch)
                try:
                    row = conn.execute(text(sql)).fetchone()
                except Exception as e:
                    logger.warning(
                        "profile_v2: combined scan batch failed (cols %d..%d) for %s: %s",
                        start, start + len(batch), qualified_table, e,
                    )
                    continue
                if row is None:
                    continue
                mapping = row._mapping  # type: ignore[attr-defined]
                total_rows = int(mapping.get("total_rows") or 0)
                for (name, _pg_type) in batch:
                    alias = _safe_ident(name)
                    per_col[name] = {
                        "non_null": int(mapping.get(f"nn_{alias}") or 0),
                        "min_text": mapping.get(f"min_{alias}"),
                        "max_text": mapping.get(f"max_{alias}"),
                    }
    except Exception as e:
        logger.warning("profile_v2: combined scan failed for %s: %s", qualified_table, e)
    return total_rows, per_col


# ───────────────────────────────────────────────────────────────────────────
# Percentiles (TABLESAMPLE on big tables, PERCENTILE_DISC, batched)
# ───────────────────────────────────────────────────────────────────────────

def _compute_percentiles(
    slug: str, qualified_table: str, numeric_cols: list[str], total_rows: int,
) -> dict[str, dict]:
    """Compute percentiles + mean + stddev per numeric column in batches.

    Uses TABLESAMPLE SYSTEM(1) REPEATABLE(42) when total_rows > 1M (~50× speedup).
    Uses PERCENTILE_DISC (cheaper than CONT).
    Returns `{col: {p25, p50, p75, p99, mean, stddev}}` (values are floats or None).
    """
    out: dict[str, dict] = {}
    if not numeric_cols:
        return out

    use_sample = total_rows > TABLESAMPLE_ROW_THRESHOLD
    source = qualified_table
    if use_sample:
        source = f"{qualified_table} TABLESAMPLE SYSTEM({TABLESAMPLE_PCT}) REPEATABLE({TABLESAMPLE_SEED})"

    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            for start in range(0, len(numeric_cols), 50):  # smaller batch — percentiles are heavier
                batch = numeric_cols[start:start + 50]
                parts: list[str] = []
                for name in batch:
                    safe = _quote_ident(name)
                    alias = _safe_ident(name)
                    expr = f"({safe})::double precision"
                    parts.append(
                        f"PERCENTILE_DISC(0.25) WITHIN GROUP (ORDER BY {expr}) AS p25_{alias}, "
                        f"PERCENTILE_DISC(0.50) WITHIN GROUP (ORDER BY {expr}) AS p50_{alias}, "
                        f"PERCENTILE_DISC(0.75) WITHIN GROUP (ORDER BY {expr}) AS p75_{alias}, "
                        f"PERCENTILE_DISC(0.99) WITHIN GROUP (ORDER BY {expr}) AS p99_{alias}, "
                        f"AVG({expr}) AS mean_{alias}, "
                        f"STDDEV_SAMP({expr}) AS sd_{alias}"
                    )
                select_list = ",\n  ".join(parts)
                sql = f"SELECT\n  {select_list}\nFROM {source}"
                try:
                    row = conn.execute(text(sql)).fetchone()
                except Exception as e:
                    logger.warning(
                        "profile_v2: percentile batch failed (cols %d..%d) for %s: %s",
                        start, start + len(batch), qualified_table, e,
                    )
                    continue
                if row is None:
                    continue
                mapping = row._mapping  # type: ignore[attr-defined]
                for name in batch:
                    alias = _safe_ident(name)
                    def _f(k: str):
                        v = mapping.get(k)
                        if v is None:
                            return None
                        try:
                            return float(v)
                        except Exception:
                            return None
                    out[name] = {
                        "p25": _f(f"p25_{alias}"),
                        "p50": _f(f"p50_{alias}"),
                        "p75": _f(f"p75_{alias}"),
                        "p99": _f(f"p99_{alias}"),
                        "mean": _f(f"mean_{alias}"),
                        "stddev": _f(f"sd_{alias}"),
                    }
    except Exception as e:
        logger.warning("profile_v2: percentile compute failed for %s: %s", qualified_table, e)
    return out


# ───────────────────────────────────────────────────────────────────────────
# Variant detection (case/whitespace) for low-cardinality cols
# ───────────────────────────────────────────────────────────────────────────

def _detect_variants(
    slug: str, qualified_table: str, low_card_cols: list[str],
) -> dict[str, str]:
    """For each low-cardinality column, detect case/whitespace variants.

    PERF (2026-05-27): replaced per-col loop (N round trips) with 2-pass:
      Pass 1 — ONE batched SELECT: per col COUNT(DISTINCT col) - COUNT(DISTINCT lower(trim(col)))
               flag cols where raw_n > norm_n (variants present)
      Pass 2 — only for flagged cols, fetch sample variant strings (cheap, few cols)

    Cuts 30 queries → 1 + N_flagged. On 21K rows × 30 dim cols: 30s → ~2s.
    """
    out: dict[str, str] = {}
    if not low_card_cols:
        return out

    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            # ── PASS 1: batched flag query (one scan, returns delta per col) ──
            flagged: list[str] = []
            for start in range(0, len(low_card_cols), MAX_COLS_PER_SCAN):
                batch = low_card_cols[start:start + MAX_COLS_PER_SCAN]
                parts = []
                for c in batch:
                    safe = _quote_ident(c)
                    alias = _safe_ident(c)
                    parts.append(
                        f"COUNT(DISTINCT {safe}::text) "
                        f"- COUNT(DISTINCT lower(trim({safe}::text))) AS vd_{alias}"
                    )
                sql = f"SELECT {', '.join(parts)} FROM {qualified_table}"
                try:
                    row = conn.execute(text(sql)).fetchone()
                except Exception as e:
                    logger.debug("profile_v2: variant batch flag failed: %s", e)
                    continue
                if row is None:
                    continue
                mapping = row._mapping  # type: ignore[attr-defined]
                for c in batch:
                    delta = mapping.get(f"vd_{_safe_ident(c)}")
                    try:
                        if delta is not None and int(delta) > 0:
                            flagged.append(c)
                    except Exception:
                        continue

            # ── PASS 2: sample variants only for flagged cols ──
            for name in flagged:
                safe = _quote_ident(name)
                sql = (
                    f"SELECT array_agg(DISTINCT {safe}::text) AS variants "
                    f"FROM {qualified_table} WHERE {safe} IS NOT NULL "
                    f"GROUP BY lower(trim({safe}::text)) "
                    f"HAVING COUNT(DISTINCT {safe}::text) > 1 LIMIT 1"
                )
                try:
                    rows = conn.execute(text(sql)).fetchall()
                except Exception as e:
                    logger.debug("profile_v2: variant sample failed for %s: %s", name, e)
                    continue
                if not rows:
                    continue
                variants = rows[0][0]
                if isinstance(variants, list) and len(variants) > 1:
                    sample = "/".join(str(v) for v in variants[:4])
                    out[name] = f"case-variants: {sample}"
    except Exception as e:
        logger.warning("profile_v2: variant scan failed for %s: %s", qualified_table, e)
    return out


# ───────────────────────────────────────────────────────────────────────────
# Role + unit classifiers
# ───────────────────────────────────────────────────────────────────────────

def _classify_role(
    name: str, pg_type: str, n_distinct: int, total_rows: int, null_pct: float,
) -> str:
    """Classify column role per rules in the spec.

    Order matters — id check first (requires *_id suffix + near-unique),
    then temporal, then measure (numeric + high cardinality),
    then state (small enum + state-ish name), then dimension, else text.
    """
    name_lc = (name or "").lower()
    pg_type_lc = (pg_type or "").lower()
    nd_ratio = (n_distinct / total_rows) if total_rows > 0 else 0.0

    if name_lc.endswith("_id") and nd_ratio >= 0.95:
        return "id"
    if pg_type_lc in TEMPORAL_PG_TYPES:
        return "temporal"
    if pg_type_lc in NUMERIC_PG_TYPES and nd_ratio > 0.5:
        return "measure"
    if 2 <= n_distinct <= 20 and STATE_NAME_RE.search(name_lc):
        return "state"
    if 20 < n_distinct <= 500:
        return "dimension"
    return "text"


def _detect_unit(name: str, pg_type: str) -> str | None:
    """Heuristic unit detection from column name."""
    name_lc = (name or "").lower()
    if any(k in name_lc for k in ("price", "amount", "revenue", "cost", "spend", "sales")):
        return "USD"
    if name_lc.endswith("_pct") or "percent" in name_lc or name_lc.endswith("_rate"):
        return "%"
    if "qty" in name_lc or name_lc.endswith("_count") or name_lc.endswith("_cnt"):
        return None
    return None


# ───────────────────────────────────────────────────────────────────────────
# Schema reader
# ───────────────────────────────────────────────────────────────────────────

def _read_columns(slug: str, table: str) -> list[tuple[str, str]]:
    """Return list of (column_name, pg_type) from information_schema, skipping lineage cols."""
    schema = _safe_ident(slug)
    tbl = _safe_ident(table)
    cols: list[tuple[str, str]] = []
    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"
                ),
                {"s": schema, "t": tbl},
            ).fetchall()
            for r in rows:
                name = r[0]
                if name and not name.startswith(LINEAGE_PREFIX):
                    cols.append((name, r[1]))
    except Exception as e:
        logger.warning("profile_v2: column read failed for %s.%s: %s", schema, tbl, e)
    return cols


# ───────────────────────────────────────────────────────────────────────────
# Top values from pg_stats most_common_vals/freqs
# ───────────────────────────────────────────────────────────────────────────

def _top_values_from_pg_stats(pg_stats_row: dict | None) -> list[dict]:
    """Build top_values list from pg_stats most_common_vals + most_common_freqs."""
    if not pg_stats_row:
        return []
    vals = pg_stats_row.get("most_common_vals") or []
    freqs = pg_stats_row.get("most_common_freqs") or []
    out: list[dict] = []
    for i, v in enumerate(vals[:TOP_VALUES_CAP]):
        freq = freqs[i] if i < len(freqs) else None
        if freq is None:
            continue
        out.append({"v": str(v), "freq_pct": round(float(freq) * 100.0, 2)})
    return out


# ───────────────────────────────────────────────────────────────────────────
# Persistence (public-schema write — get_write_engine() + CAST(:p AS jsonb))
# ───────────────────────────────────────────────────────────────────────────

def _persist_profile(slug: str, table: str, profile: dict) -> None:
    """UPSERT profile into `public.dash_table_metadata.metadata['profile_v2']`.

    Schema (per CLAUDE.md):
        dash_table_metadata(project_slug, table_name, metadata jsonb, ...)
        conflict key: (project_slug, table_name)

    Uses get_write_engine() (NOT get_sql_engine — public-schema write rule)
    and CAST(:p AS jsonb) (NOT :p::jsonb — PgBouncer rule).
    """
    import json as _json
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        payload = _json.dumps(profile, default=str)
        with eng.begin() as conn:
            # Merge into existing metadata jsonb under key 'profile_v2'.
            # If row doesn't exist, INSERT a new metadata blob containing only profile_v2.
            conn.execute(
                text(
                    "INSERT INTO public.dash_table_metadata (project_slug, table_name, metadata) "
                    "VALUES (:s, :t, jsonb_build_object('profile_v2', CAST(:p AS jsonb))) "
                    "ON CONFLICT (project_slug, table_name) DO UPDATE "
                    "SET metadata = COALESCE(public.dash_table_metadata.metadata, '{}'::jsonb) "
                    "             || jsonb_build_object('profile_v2', CAST(:p AS jsonb)), "
                    "    updated_at = now()"
                ),
                {"s": _safe_ident(slug), "t": _safe_ident(table), "p": payload},
            )
    except Exception as e:
        logger.warning("profile_v2: persist failed for %s.%s: %s", slug, table, e)


# ───────────────────────────────────────────────────────────────────────────
# Main entrypoint
# ───────────────────────────────────────────────────────────────────────────

def profile_table_v2(slug: str, table: str) -> dict:
    """Profile a project table using ONE combined scan + pg_stats.

    See module docstring for return contract. Fail-soft: any sub-step failure
    yields a partial profile rather than raising.
    """
    # Kill switch
    if os.environ.get("PROFILE_V2_DISABLED") == "1":
        return {"disabled": True}

    t0 = time.time()
    qualified = _qualified(slug, table)

    # Step 1 — column list
    columns: list[tuple[str, str]] = []
    try:
        columns = _read_columns(slug, table)
    except Exception as e:
        logger.warning("profile_v2: schema read raised for %s.%s: %s", slug, table, e)

    if not columns:
        return {
            "total_rows": 0,
            "scan_elapsed_ms": int((time.time() - t0) * 1000),
            "scanned_at": _now_iso(),
            "columns": [],
        }

    column_names = [c[0] for c in columns]
    pg_type_by_name = {c[0]: (c[1] or "") for c in columns}

    # Step 2 — pg_stats (with ANALYZE fallback if empty)
    pg_stats: dict[str, dict] = {}
    try:
        pg_stats = _read_pg_stats(slug, table)
        if _run_analyze_if_empty(slug, table, pg_stats, column_names):
            pg_stats = _read_pg_stats(slug, table)
    except Exception as e:
        logger.warning("profile_v2: pg_stats stage raised: %s", e)

    # Step 3 — combined scan (COUNT + MIN + MAX per col)
    total_rows = 0
    per_col_basic: dict[str, dict] = {}
    try:
        total_rows, per_col_basic = _run_combined_scan(qualified, columns)
    except Exception as e:
        logger.warning("profile_v2: combined scan stage raised: %s", e)

    # Step 4 — derive n_distinct + top values + null pct
    derived: dict[str, dict] = {}
    for (name, pg_type) in columns:
        basic = per_col_basic.get(name, {})
        non_null = int(basic.get("non_null") or 0)
        null_pct = round((1.0 - (non_null / total_rows)) * 100.0, 2) if total_rows > 0 else 0.0

        stats_row = pg_stats.get(name)
        n_distinct_raw = (stats_row or {}).get("n_distinct", 0.0)
        try:
            n_distinct_raw = float(n_distinct_raw)
        except Exception:
            n_distinct_raw = 0.0
        # Negative pg_stats n_distinct = -fraction-of-rows
        if n_distinct_raw < 0:
            n_distinct_approx = int(round(abs(n_distinct_raw) * total_rows))
        else:
            n_distinct_approx = int(round(n_distinct_raw))
        # Lower-bound sanity: distinct can't exceed non_null
        if non_null > 0 and n_distinct_approx > non_null:
            n_distinct_approx = non_null

        top_values = _top_values_from_pg_stats(stats_row)

        derived[name] = {
            "pg_type": pg_type,
            "non_null": non_null,
            "null_pct": null_pct,
            "n_distinct_approx": n_distinct_approx,
            "top_values": top_values,
            "min_text": basic.get("min_text"),
            "max_text": basic.get("max_text"),
        }

    # Step 5 — exact n_distinct for low-cardinality columns (best-effort, cheap)
    low_card_cols: list[str] = [
        n for n, d in derived.items()
        if d["n_distinct_approx"] and d["n_distinct_approx"] < LOW_CARD_THRESHOLD
    ]
    n_distinct_exact: dict[str, int] = {}
    if low_card_cols:
        try:
            from db.session import get_sql_engine
            eng = get_sql_engine()
            with eng.connect() as conn:
                # batch — one query with multiple COUNT(DISTINCT col) (caps at MAX_COLS_PER_SCAN)
                for start in range(0, len(low_card_cols), MAX_COLS_PER_SCAN):
                    batch = low_card_cols[start:start + MAX_COLS_PER_SCAN]
                    parts = [
                        f"COUNT(DISTINCT {_quote_ident(c)}) AS nd_{_safe_ident(c)}"
                        for c in batch
                    ]
                    sql = f"SELECT {', '.join(parts)} FROM {qualified}"
                    try:
                        row = conn.execute(text(sql)).fetchone()
                    except Exception as e:
                        logger.debug("profile_v2: exact distinct batch failed: %s", e)
                        continue
                    if row is None:
                        continue
                    mapping = row._mapping  # type: ignore[attr-defined]
                    for c in batch:
                        v = mapping.get(f"nd_{_safe_ident(c)}")
                        if v is not None:
                            try:
                                n_distinct_exact[c] = int(v)
                            except Exception:
                                pass
        except Exception as e:
            logger.warning("profile_v2: exact distinct stage raised: %s", e)

    # Step 6 — percentiles for measure/temporal cols
    measure_cols: list[str] = []
    for (name, pg_type) in columns:
        pg_type_lc = (pg_type or "").lower()
        if pg_type_lc in NUMERIC_PG_TYPES:
            measure_cols.append(name)
    percentiles: dict[str, dict] = {}
    try:
        percentiles = _compute_percentiles(slug, qualified, measure_cols, total_rows)
    except Exception as e:
        logger.warning("profile_v2: percentile stage raised: %s", e)

    # Step 7 — variant detection on low-card cols
    variants: dict[str, str] = {}
    try:
        variants = _detect_variants(slug, qualified, low_card_cols)
    except Exception as e:
        logger.warning("profile_v2: variant stage raised: %s", e)

    # Step 8 — assemble final column objects
    out_cols: list[dict] = []
    for (name, pg_type) in columns:
        d = derived[name]
        n_distinct_approx = d["n_distinct_approx"]
        n_distinct_exact_val = n_distinct_exact.get(name) if name in n_distinct_exact else None
        # Prefer exact when present
        n_distinct_for_role = n_distinct_exact_val if n_distinct_exact_val is not None else n_distinct_approx

        role = _classify_role(name, pg_type, n_distinct_for_role, total_rows, d["null_pct"])

        stats: dict | None = None
        pg_type_lc = (pg_type or "").lower()
        if role == "measure":
            pcs = percentiles.get(name, {})
            stats = {
                "min": d.get("min_text"),
                "max": d.get("max_text"),
                "mean": pcs.get("mean"),
                "stddev": pcs.get("stddev"),
                "p25": pcs.get("p25"),
                "p50": pcs.get("p50"),
                "p75": pcs.get("p75"),
                "p99": pcs.get("p99"),
            }
        elif role == "temporal":
            stats = {
                "min": d.get("min_text"),
                "max": d.get("max_text"),
                "mean": None, "stddev": None,
                "p25": None, "p50": None, "p75": None, "p99": None,
            }

        variant_warning = variants.get(name)
        out_cols.append({
            "name": name,
            "pg_type": pg_type,
            "role": role,
            "n_distinct_approx": int(n_distinct_approx),
            "n_distinct_exact": (int(n_distinct_exact_val) if n_distinct_exact_val is not None else None),
            "null_pct": float(d["null_pct"]),
            "top_values": d["top_values"],
            "stats": stats,
            "variants_detected": bool(variant_warning),
            "variant_warning": variant_warning,
            "unit": _detect_unit(name, pg_type),
        })

    elapsed_ms = int((time.time() - t0) * 1000)
    profile = {
        "total_rows": int(total_rows),
        "scan_elapsed_ms": elapsed_ms,
        "scanned_at": _now_iso(),
        "columns": out_cols,
    }

    # Step 9 — persist (fail-soft)
    try:
        _persist_profile(slug, table, profile)
    except Exception as e:
        logger.warning("profile_v2: persist raised: %s", e)

    logger.info(
        "profile_v2: %s.%s rows=%d cols=%d elapsed=%dms",
        _safe_ident(slug), _safe_ident(table),
        total_rows, len(out_cols), elapsed_ms,
    )
    return profile


__all__ = ["profile_table_v2"]
