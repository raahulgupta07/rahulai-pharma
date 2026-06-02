"""
Schema Contract Registry for the staged data-ingest pipeline.
==============================================================

Locks each logical dataset's expected column set so that silent schema drift
(renamed / added / retyped columns across monthly file drops) is caught before
it can corrupt a destination table.

Public API
----------
pg_type(series)                      → str
detect_load_key(df, filename)        → dict
infer_contract(df, project, dataset, filename) → dict
get_contract(project, dataset)       → dict | None
save_contract(project, dataset, contract) → int
check_against_contract(contract, df) → dict
evolve_contract(project, dataset, df, mapping, filename) → dict
set_load_key(project, dataset, strategy, columns) → dict

CRITICAL: get_sql_engine() is the CACHED SHARED engine — never .dispose() it.
All DB access is fail-soft (try/except, log, sensible default).
Pure-pandas functions (pg_type, detect_load_key, infer_contract,
check_against_contract) never hit the DB.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any

import pandas as pd
from sqlalchemy import text

_log = logging.getLogger("dash.ingest.contract")

# ---------------------------------------------------------------------------
# Internal sentinel columns that must never appear in a load key
# ---------------------------------------------------------------------------
_SYSTEM_COLS = {"_source_file", "_period", "_batch_id", "_content_hash", "_row_key"}

# ---------------------------------------------------------------------------
# Regex to detect period-ish column names
# ---------------------------------------------------------------------------
_PERIOD_COL_RE = re.compile(r"period|month|date|yyyy|ym", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Regex to detect id-ish column names (for single-key tier — loose, used in
# composite candidate sorting only)
# ---------------------------------------------------------------------------
_ID_COL_RE = re.compile(r"id|code|key|no|number", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Regex for STRONG single-pk detection: whole-word anchored names that are
# genuine dataset-level primary keys, not just incidentally unique fields.
# Must match the entire normalised column name.
# ---------------------------------------------------------------------------
_STRONG_PK_RE = re.compile(
    r"^(id|.*_id|code|.*_code|key|.*_key|uuid|guid|pk)$",
    re.IGNORECASE,
)


# ===========================================================================
# 1. pg_type — dtype → postgres type string
# ===========================================================================

def pg_type(series: pd.Series) -> str:
    """Map a pandas Series dtype to a PostgreSQL type string.

    Rules (first match wins):
      bool            → boolean
      int32/int16/… → integer
      int64           → bigint
      float*/decimal  → double precision
      datetime*/…     → timestamp
      default         → text
    """
    dtype = series.dtype
    kind = dtype.kind          # 'b' bool, 'i' int, 'u' uint, 'f' float, 'M' datetime, 'O' object …

    if kind == "b":
        return "boolean"
    if kind == "u":
        # unsigned ints — use bigint to be safe
        return "bigint"
    if kind == "i":
        # int8/16/32 → integer, int64 → bigint
        if dtype.itemsize <= 4:
            return "integer"
        return "bigint"
    if kind == "f":
        return "double precision"
    if kind == "M":
        return "timestamp"
    # object / string / other
    # Check if it looks like datetime by name (last resort)
    return "text"


# ===========================================================================
# 2. detect_load_key
# ===========================================================================

def detect_load_key(df: pd.DataFrame, filename: str = "") -> dict:
    """Detect the unique-key strategy for *df*.

    Four tiers attempted in order; first that fits wins.

    Tier 1 — single (STRONG):
        One column is 100% unique AND its normalised name is a genuine primary-key
        name matching ``^(id|.*_id|code|.*_code|key|.*_key|uuid|guid|pk)$``.
        A coincidentally-unique column whose name does NOT match that pattern is
        NOT promoted to single — this prevents a monthly file's customer_id (unique
        in month-1, non-unique across months) from becoming the dataset key.

    Tier 2 — period:
        ``has_period`` is True when detect_period(filename) returns non-None OR
        any candidate column name matches /period|month|date|yyyy|ym/i.
        Preferred over composite for transactional monthly files because DELETE
        WHERE _period then reload is idempotent and safe; a composite of
        coincidentally-unique columns in one month's file is not.

    Tier 3 — composite:
        Smallest 2-4 column combo with zero duplicates.

    Tier 4 — fallback:
        content_hash.

    Guardrails:
    - System columns (_source_file etc.) are excluded from all key candidates.
    - Combo search caps at 4 columns.
    - Float / long-text columns excluded from composite candidates.

    Returns a dict like::

        {"strategy": "single|composite|period|content_hash", "columns": [...]}
    """
    from dash.tools.ingest_router import detect_period as _detect_period

    n = len(df)
    if n == 0:
        return {"strategy": "content_hash", "columns": []}

    # Candidate columns — exclude system cols
    candidates = [c for c in df.columns if str(c).lower().strip() not in _SYSTEM_COLS]

    # ------------------------------------------------------------------
    # Pre-compute has_period flag
    # ------------------------------------------------------------------
    _fp = _detect_period(filename)
    has_period = _fp is not None
    period_col: str | None = None
    if not has_period:
        for col in candidates:
            if _PERIOD_COL_RE.search(str(col)):
                has_period = True
                period_col = col
                break

    # ------------------------------------------------------------------
    # Tier 0: month-level period IN THE FILENAME → transactional monthly drop.
    # This beats single, because a column that merely LOOKS like a pk
    # (e.g. customer_id) is unique in one month but repeats across months —
    # it's a foreign key, not the dataset key. A dimension snapshot named with
    # only a year ("customers_2025") falls through to single/composite instead.
    # ------------------------------------------------------------------
    if _fp is not None and len(_fp) == 7 and "-" in _fp:
        return {"strategy": "period", "columns": []}

    # ------------------------------------------------------------------
    # Tier 1: STRONG single unique pk column
    # ------------------------------------------------------------------
    for col in candidates:
        name = str(col).lower().strip()
        if not _STRONG_PK_RE.match(name):
            continue
        if df[col].nunique(dropna=False) == n:
            return {"strategy": "single", "columns": [col]}

    # ------------------------------------------------------------------
    # Tier 2: period — preferred over composite for transactional files
    # ------------------------------------------------------------------
    if has_period:
        # Resolve the best period column: prefer an explicit period-ish column
        # found by name; fall back to empty list (period derived from filename).
        col_list = [period_col] if period_col is not None else []
        return {"strategy": "period", "columns": col_list}

    # ------------------------------------------------------------------
    # Tier 3: composite — smallest combo 2..4 with zero dupes
    # ------------------------------------------------------------------
    combo_candidates = _filter_composite_candidates(df, candidates)

    if len(combo_candidates) >= 2:
        best_combo = _find_best_composite(df, combo_candidates)
        if best_combo is not None:
            return {"strategy": "composite", "columns": list(best_combo)}

    # ------------------------------------------------------------------
    # Tier 4: content_hash fallback
    # ------------------------------------------------------------------
    return {"strategy": "content_hash", "columns": []}


def _filter_composite_candidates(df: pd.DataFrame, candidates: list) -> list:
    """Return columns suitable for composite key candidates.

    Excludes:
    - columns whose names start with '_'
    - float/decimal columns
    - free-text columns (avg str len > 40 OR >70% unique long strings)

    Prefers id-ish + date-ish + low-cardinality categorical.
    """
    good = []
    n = len(df)
    for col in candidates:
        name = str(col).lower().strip()
        if name.startswith("_"):
            continue
        dtype_kind = df[col].dtype.kind
        if dtype_kind == "f":
            continue
        # Free-text guard
        if dtype_kind == "O":
            try:
                non_null = df[col].dropna()
                if len(non_null) > 0:
                    avg_len = non_null.astype(str).str.len().mean()
                    uniq_ratio = df[col].nunique() / max(n, 1)
                    long_frac = (non_null.astype(str).str.len() > 40).mean()
                    if avg_len > 40 or (uniq_ratio > 0.7 and long_frac > 0.7):
                        continue
            except Exception:
                pass
        good.append(col)

    # Sort: id-ish first, then date-ish, then low-cardinality
    def _priority(c):
        name = str(c).lower()
        if _ID_COL_RE.search(name):
            return 0
        if _PERIOD_COL_RE.search(name):
            return 1
        cardinality = df[c].nunique() if n > 0 else 0
        return 2 + min(cardinality, 1000)

    good.sort(key=_priority)
    return good


def _find_best_composite(df: pd.DataFrame, candidates: list) -> tuple | None:
    """Return the smallest combo (size 2..4) with zero duplicates, or None."""
    from itertools import combinations as _combos

    n = len(df)
    for size in range(2, min(5, len(candidates) + 1)):
        for combo in _combos(candidates, size):
            try:
                dupe_count = df.duplicated(subset=list(combo)).sum()
                if dupe_count == 0:
                    return combo
            except Exception:
                continue
    return None


# ===========================================================================
# 3. infer_contract
# ===========================================================================

def infer_contract(
    df: pd.DataFrame,
    project: str,
    dataset: str,
    filename: str = "",
) -> dict:
    """Infer a fresh contract dict from *df*.

    Always returns version=1, active=True.
    period_source is set to "filename" when detect_period(filename) returns a
    non-None value; otherwise to the first period-ish column found; else None.
    """
    from dash.tools.ingest_router import detect_period as _detect_period

    # Normalize column names (lower + strip)
    norm_cols = {str(c).lower().strip(): c for c in df.columns}
    columns: dict[str, str] = {}
    for norm_name, orig_col in norm_cols.items():
        if norm_name in _SYSTEM_COLS:
            continue
        try:
            columns[norm_name] = pg_type(df[orig_col])
        except Exception:
            columns[norm_name] = "text"

    load_key = detect_load_key(df, filename)

    # Determine period_source
    period_source: str | None = None
    if _detect_period(filename) is not None:
        period_source = "filename"
    else:
        for norm_name in columns:
            if _PERIOD_COL_RE.search(norm_name):
                period_source = norm_name
                break

    return {
        "project": project,
        "dataset": dataset,
        "version": 1,
        "active": True,
        "columns": columns,
        "load_key": load_key,
        "period_source": period_source,
    }


# ===========================================================================
# 4. get_contract — DB read (fail-soft)
# ===========================================================================

def get_contract(project: str, dataset: str) -> dict | None:
    """Fetch the active contract for (project, dataset) from the DB.

    Returns None if not found or on any DB error.
    """
    try:
        from db.session import get_write_engine
        engine = get_write_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, project_slug, dataset, version, active, "
                    "       columns, load_key, period_source "
                    "FROM public.dash_ingest_contracts "
                    "WHERE project_slug = :p AND dataset = :d AND active = TRUE "
                    "ORDER BY version DESC LIMIT 1"
                ),
                {"p": project, "d": dataset},
            ).fetchone()
        if row is None:
            return None
        return {
            "project": row.project_slug,
            "dataset": row.dataset,
            "version": row.version,
            "active": row.active,
            "columns": dict(row.columns) if row.columns else {},
            "load_key": dict(row.load_key) if row.load_key else {"strategy": "content_hash", "columns": []},
            "period_source": row.period_source,
        }
    except Exception as exc:
        _log.warning("get_contract(%s, %s) failed: %s", project, dataset, exc)
        return None


# ===========================================================================
# 5. save_contract — DB write (fail-soft)
# ===========================================================================

def save_contract(project: str, dataset: str, contract: dict) -> int:
    """Persist *contract* to the DB.

    Steps:
    1. Deactivate all prior active rows for (project, dataset).
    2. Determine next version = max(existing version) + 1 (or contract['version']).
    3. Insert new row with active=True.

    Returns the version number actually saved.
    JSONB params use CAST(:x AS jsonb) — never :x::jsonb.
    """
    try:
        from db.session import get_write_engine
        engine = get_write_engine()
        import json as _json

        with engine.begin() as conn:
            # Find max existing version
            row = conn.execute(
                text(
                    "SELECT COALESCE(MAX(version), 0) AS max_ver "
                    "FROM public.dash_ingest_contracts "
                    "WHERE project_slug = :p AND dataset = :d"
                ),
                {"p": project, "d": dataset},
            ).fetchone()
            max_ver: int = row.max_ver if row else 0
            version = max(contract.get("version", 1), max_ver + 1)

            # Deactivate prior active rows
            conn.execute(
                text(
                    "UPDATE public.dash_ingest_contracts "
                    "SET active = FALSE "
                    "WHERE project_slug = :p AND dataset = :d AND active = TRUE"
                ),
                {"p": project, "d": dataset},
            )

            # Insert new row
            conn.execute(
                text(
                    "INSERT INTO public.dash_ingest_contracts "
                    "(project_slug, dataset, version, active, columns, load_key, period_source) "
                    "VALUES (:p, :d, :v, TRUE, "
                    "        CAST(:cols AS jsonb), "
                    "        CAST(:lk AS jsonb), "
                    "        :ps)"
                ),
                {
                    "p": project,
                    "d": dataset,
                    "v": version,
                    "cols": _json.dumps(contract.get("columns", {})),
                    "lk": _json.dumps(contract.get("load_key", {"strategy": "content_hash", "columns": []})),
                    "ps": contract.get("period_source"),
                },
            )
        return version
    except Exception as exc:
        _log.error("save_contract(%s, %s) failed: %s", project, dataset, exc)
        return contract.get("version", 1)


# ===========================================================================
# 6. check_against_contract
# ===========================================================================

def check_against_contract(contract: dict | None, df: pd.DataFrame) -> dict:
    """Compare *df*'s columns to the registered contract.

    Returns a verdict dict::

        {
            "verdict": "exact|drift|new",
            "diff": {
                "added":   [str],
                "removed": [str],
                "retyped": [{"col": str, "from": str, "to": str}],
                "renamed": [{"from": str, "to": str}],
            },
            "proposed_mapping": {new_col: existing_col},
        }

    Rules:
    - Columns starting with '_' are ignored on both sides.
    - "exact" when the column sets match exactly (order ignored, types checked).
    - "drift" when columns differ.
    - "new" when contract is None/empty.
    - Renames are detected by difflib name similarity (ratio > 0.7) on
      (removed, added) pairs.
    """
    empty_diff: dict = {"added": [], "removed": [], "retyped": [], "renamed": []}

    if not contract or not contract.get("columns"):
        return {"verdict": "new", "diff": empty_diff, "proposed_mapping": {}}

    # Normalize DF columns (lower+strip), exclude system cols
    df_cols: dict[str, str] = {}
    for c in df.columns:
        norm = str(c).lower().strip()
        if norm.startswith("_") or norm in _SYSTEM_COLS:
            continue
        try:
            df_cols[norm] = pg_type(df[c])
        except Exception:
            df_cols[norm] = "text"

    # Contract columns (already normalized, but re-filter _ just in case)
    ct_cols: dict[str, str] = {
        k: v
        for k, v in contract["columns"].items()
        if not k.startswith("_") and k not in _SYSTEM_COLS
    }

    df_set = set(df_cols)
    ct_set = set(ct_cols)

    added = sorted(df_set - ct_set)
    removed = sorted(ct_set - df_set)

    # Check retyped (same name, different pg_type)
    retyped = []
    for col in df_set & ct_set:
        if df_cols[col] != ct_cols[col]:
            retyped.append({"col": col, "from": ct_cols[col], "to": df_cols[col]})

    # Rename detection: match removed → added by name similarity
    renamed: list[dict] = []
    proposed_mapping: dict[str, str] = {}
    if added and removed:
        for rem in removed:
            best_score = 0.0
            best_add: str | None = None
            for add in added:
                ratio = difflib.SequenceMatcher(None, rem, add).ratio()
                if ratio > best_score:
                    best_score = ratio
                    best_add = add
            if best_add is not None and best_score > 0.7:
                renamed.append({"from": rem, "to": best_add})
                proposed_mapping[best_add] = rem

    # Verdict
    no_diff = (not added and not removed and not retyped)
    verdict = "exact" if no_diff else "drift"

    return {
        "verdict": verdict,
        "diff": {
            "added": added,
            "removed": removed,
            "retyped": retyped,
            "renamed": renamed,
        },
        "proposed_mapping": proposed_mapping,
    }


# ===========================================================================
# 7. evolve_contract
# ===========================================================================

def evolve_contract(
    project: str,
    dataset: str,
    df: pd.DataFrame,
    mapping: dict | None = None,
    filename: str = "",
) -> dict:
    """Apply *mapping* renames to *df*, merge new columns, bump version, save.

    Steps:
    1. If *mapping* is provided, rename DF columns accordingly.
    2. Load existing contract (if any).
    3. Merge new columns into existing contract['columns']
       (existing types preserved; genuinely new columns appended).
    4. Re-detect load_key from the renamed DF (fresh eyes each time).
    5. Bump version (existing max + 1).
    6. save_contract → DB.
    7. Return the new contract dict.
    """
    if mapping:
        # mapping = {new_col_in_df: existing_col_in_contract}
        # We rename df columns from new_col → existing_col so they unify
        rename_map = {v: k for k, v in mapping.items()}  # df already has new names
        # Actually mapping is {new_df_col: existing_contract_col} so rename df
        try:
            df = df.rename(columns=mapping)
        except Exception as exc:
            _log.warning("evolve_contract rename failed: %s", exc)

    existing = get_contract(project, dataset)

    # Infer a fresh contract from the (possibly renamed) df
    fresh = infer_contract(df, project, dataset, filename)

    if existing is None:
        new_contract = fresh
        new_contract["version"] = 1
    else:
        # Accept = the (renamed) df IS the new canonical shape: adopt its columns
        # AND types fully so the file clears as 'exact' against the evolved
        # contract (a retyped/renamed accept must not stay in drift). Preserve the
        # prior load_key (a manual override shouldn't be lost) unless none exists.
        new_contract = {
            "project": project,
            "dataset": dataset,
            "version": existing["version"] + 1,
            "active": True,
            "columns": dict(fresh["columns"]),
            "load_key": existing.get("load_key") or fresh["load_key"],
            "period_source": fresh["period_source"],
        }

    saved_version = save_contract(project, dataset, new_contract)
    new_contract["version"] = saved_version
    return new_contract


# ===========================================================================
# 8. set_load_key — manual load-key override (#5)
# ===========================================================================

_VALID_STRATEGIES = {"single", "composite", "period", "content_hash"}


def set_load_key(
    project: str,
    dataset: str,
    strategy: str,
    columns: list[str] | None = None,
) -> dict:
    """Override the load_key on the active contract for (project, dataset).

    Parameters
    ----------
    project:  project slug
    dataset:  dataset name
    strategy: one of "single", "composite", "period", "content_hash"
    columns:  list of column names relevant to the strategy (default [])

    Returns
    -------
    The updated contract dict, or ``{"error": "<reason>"}`` on failure.
    Strategy must be in the valid set; unknown strategies are rejected.
    Fail-soft on any DB error.
    """
    try:
        if strategy not in _VALID_STRATEGIES:
            return {
                "error": (
                    f"invalid strategy {strategy!r}; "
                    f"must be one of {sorted(_VALID_STRATEGIES)}"
                )
            }

        contract = get_contract(project, dataset)
        if contract is None:
            return {"error": "no contract"}

        cols: list[str] = columns if columns is not None else []
        contract["load_key"] = {"strategy": strategy, "columns": cols}
        # Bump version so callers can detect the override
        contract["version"] = contract.get("version", 1) + 1

        saved_version = save_contract(project, dataset, contract)
        contract["version"] = saved_version
        return contract
    except Exception as exc:
        _log.error(
            "set_load_key(%s, %s, %s) failed: %s", project, dataset, strategy, exc
        )
        return {"error": str(exc)}
