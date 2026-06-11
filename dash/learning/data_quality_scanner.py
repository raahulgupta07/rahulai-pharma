"""
Data Quality Scanner
====================

Surfaces data-quality issues across a project's uploaded tables. Read-only.

Issue types
-----------
- text_dates         : TEXT column with date-shaped values (>70% match) — should be TIMESTAMPTZ
- opaque_columns     : short (<4 chars) or undocumented column name (e.g. `mmreg`, `other`)
- high_null_pct      : column > 50% NULL
- duplicate_tables   : table pair with >80% column overlap
- missing_fk         : column matches `<name>_id` / `<name>id` but no FK in pg_constraint
- unicode_non_latin  : column contains non-ASCII chars (Myanmar / Chinese / etc.)
- tiny_table         : <100 rows (low statistical value)
- cast_artifact      : table name ends in `_casted`
- enum_no_value_map  : bigint/int column with <10 distinct values + no value-map in brain
- low_codex_confidence: table missing purpose / grain / PK in semantic_model
- catalog_gap         : article in catalog with no stock (shop_flat link_status='catalog_only') — LEGIT gap
- orphan_stock        : stock row with no catalog match (shop_flat link_status='stock_only') — LEGIT gap

Each issue: {type, severity, table, column?, message, suggestion, count}
severity ∈ {'high', 'medium', 'low', 'info'}
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

# Regex: scientific notation as produced by Excel for large integer IDs
# Matches e.g. "1E+12", "1.23456E+11", "9.87e+12"
_SCI_NOTATION_RE = re.compile(r'^\d(\.\d+)?[eE]\+?\d+$')

# Column names that are likely ID/code columns and must NOT be collapsed
_ID_COL_NAME_RE = re.compile(r'code|_id$|^id$|article|sku|barcode', re.IGNORECASE)

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Simple in-process cache: slug -> (ts_epoch, payload)
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_S = 300  # 5 min

_DATE_REGEXES = [
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}"),          # DD/MM/YYYY
    re.compile(r"^\d{4}-\d{1,2}-\d{1,2}"),          # YYYY-MM-DD
    re.compile(r"^\d{1,2}-\d{1,2}-\d{4}"),          # DD-MM-YYYY
    re.compile(r"^\d{4}/\d{1,2}/\d{1,2}"),          # YYYY/MM/DD
]

_SEVERITY_WEIGHTS = {"high": 12, "medium": 5, "low": 2, "info": 0}


def _looks_like_date(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if len(s) < 6 or len(s) > 32:
        return False
    return any(rgx.match(s) for rgx in _DATE_REGEXES)


def _has_non_ascii(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    try:
        s.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


# Plain-words consequence per issue type — shown as the IMPACT line in the UI.
_IMPACT_BY_TYPE = {
    "sci_notation_id": "Catalog↔stock join returns 0 rows — every stock/quantity answer shows 'unavailable'.",
    "type_mismatch": "Cross-table join on this column fails with a type error — dependent tools crash.",
    "text_dates": "Date filters, sorting and time-range questions don't work (compared as text).",
    "high_null_pct": "Aggregates and filters on this column are unreliable — most rows are blank.",
    "duplicate_tables": "Agent may pick the wrong/stale copy — answers can disagree run to run.",
    "missing_fk": "No referential integrity — orphan rows and broken joins go undetected.",
    "unicode_non_latin": "English keyword search misses these values (stored in another script).",
    "enum_no_value_map": "Coded values shown raw — agent can't explain what each code means.",
    "opaque_columns": "Agent may misuse or ignore this column — its meaning is undocumented.",
    "low_codex_confidence": "Weak semantic model — routing and SQL grounding are less accurate.",
    "tiny_table": "Too few rows for reliable statistics — treat results as illustrative.",
    "cast_artifact": "Leftover type-cast table clutters the schema and can be picked by mistake.",
    "catalog_gap": "These articles show to users as 'in catalog, not currently stocked' — expected, not an error.",
    "orphan_stock": "Stock code is searchable but catalog details (brand/generic) are missing until the article is added.",
}


def _make_issue(
    issue_type: str,
    severity: str,
    table: str,
    message: str,
    suggestion: str,
    column: Optional[str] = None,
    count: int = 1,
    sample: Optional[list] = None,
    total_rows: int = 0,
    impact: str = "",
    root_cause: str = "",
    recoverable: Optional[bool] = None,
) -> dict:
    return {
        "type": issue_type,
        "severity": severity,
        "table": table,
        "column": column,
        "message": message,
        "suggestion": suggestion,
        "count": count,
        "sample": sample or [],
        "total_rows": total_rows,
        "impact": impact or _IMPACT_BY_TYPE.get(issue_type, ""),
        "root_cause": root_cause,
        "recoverable": recoverable,  # None = n/a, True = fixable in-place, False = re-source needed
    }


def _load_semantic_model(eng: Engine, slug: str) -> dict[str, dict]:
    """Pull dash_table_metadata for this project, keyed by table_name."""
    out: dict[str, dict] = {}
    try:
        with eng.connect() as c:
            rows = c.execute(text(
                "SELECT table_name, metadata FROM public.dash_table_metadata "
                "WHERE project_slug = :s"
            ), {"s": slug}).fetchall()
            for r in rows:
                name = r[0]
                meta = r[1]
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                out[name] = meta or {}
    except Exception:
        logger.exception("semantic_model load failed for %s", slug)
    return out


def _load_value_maps(eng: Engine, slug: str) -> set[str]:
    """Set of column names that already have a value-map in company brain."""
    out: set[str] = set()
    try:
        with eng.connect() as c:
            rows = c.execute(text(
                "SELECT name FROM public.dash_company_brain "
                "WHERE category IN ('value_map', 'alias', 'enum') "
                "  AND (project_slug = :s OR project_slug IS NULL)"
            ), {"s": slug}).fetchall()
            for r in rows:
                if r[0]:
                    out.add(str(r[0]).lower())
    except Exception:
        pass
    return out


def _list_tables(eng: Engine, slug: str) -> list[str]:
    with eng.connect() as c:
        rows = c.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :s AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        ), {"s": slug}).fetchall()
    return [r[0] for r in rows]


def _list_columns(eng: Engine, slug: str, table: str) -> list[tuple[str, str]]:
    with eng.connect() as c:
        rows = c.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t "
            "ORDER BY ordinal_position"
        ), {"s": slug, "t": table}).fetchall()
    return [(r[0], r[1]) for r in rows]


def _row_count(eng: Engine, slug: str, table: str) -> int:
    try:
        with eng.connect() as c:
            return int(c.execute(text(
                f'SELECT COUNT(*) FROM "{slug}"."{table}"'
            )).scalar() or 0)
    except Exception:
        return 0


def _foreign_key_columns(eng: Engine, slug: str, table: str) -> set[str]:
    """Columns that participate in a FK constraint on this table."""
    out: set[str] = set()
    try:
        with eng.connect() as c:
            rows = c.execute(text(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                " AND tc.table_schema = kcu.table_schema "
                "WHERE tc.constraint_type = 'FOREIGN KEY' "
                "  AND tc.table_schema = :s AND tc.table_name = :t"
            ), {"s": slug, "t": table}).fetchall()
            for r in rows:
                out.add(r[0])
    except Exception:
        pass
    return out


def _sample_text_values(eng: Engine, slug: str, table: str, column: str, limit: int = 200) -> list[str]:
    try:
        with eng.connect() as c:
            rows = c.execute(text(
                f'SELECT "{column}" FROM "{slug}"."{table}" '
                f'WHERE "{column}" IS NOT NULL LIMIT {int(limit)}'
            )).fetchall()
        return [str(r[0]) for r in rows if r[0] is not None]
    except Exception:
        return []


def _null_pct(eng: Engine, slug: str, table: str, column: str, total_rows: int) -> float:
    if total_rows == 0:
        return 0.0
    try:
        with eng.connect() as c:
            n_null = int(c.execute(text(
                f'SELECT COUNT(*) FROM "{slug}"."{table}" WHERE "{column}" IS NULL'
            )).scalar() or 0)
        return (n_null / total_rows) * 100.0
    except Exception:
        return 0.0


def _distinct_count(eng: Engine, slug: str, table: str, column: str, cap: int = 50) -> int:
    try:
        with eng.connect() as c:
            return int(c.execute(text(
                f'SELECT COUNT(*) FROM (SELECT DISTINCT "{column}" '
                f'FROM "{slug}"."{table}" LIMIT {int(cap)}) sub'
            )).scalar() or 0)
    except Exception:
        return 0


def _is_text_type(data_type: str) -> bool:
    dt = (data_type or "").lower()
    return dt in ("text", "character varying", "varchar", "character", "char")


def _is_int_type(data_type: str) -> bool:
    dt = (data_type or "").lower()
    return dt in ("bigint", "integer", "smallint", "int", "int4", "int8", "int2")


def _looks_like_fk_name(col: str) -> bool:
    c = col.lower()
    return c.endswith("_id") or (c.endswith("id") and len(c) > 2 and not c.startswith("id"))


def _scan_shop_flat_link_gaps(eng: Engine, slug: str) -> list[dict]:
    """
    Schema-level checks over citypharma.shop_flat (the FULL OUTER join of catalog
    and balance-stock). These are LEGITIMATE business gaps, NOT corruption:

      - catalog_gap  (link_status='catalog_only') : article in catalog, no stock row
                       (frozen / advance-entry / supplier-out — COMMON, expected).
      - orphan_stock (link_status='stock_only')   : stock row, no catalog match
                       (delayed catalog update — RARE).

    Fail-soft: if shop_flat doesn't exist yet, or has no link_status column, return
    []. 0 rows for a status → no issue emitted (no noise).
    """
    out: list[dict] = []
    try:
        with eng.connect() as c:
            # Guard 1: table must exist.
            reg = c.execute(text(
                "SELECT to_regclass('citypharma.shop_flat')"
            )).scalar()
            if reg is None:
                return out
            # Guard 2: link_status column must exist.
            has_col = c.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'citypharma' AND table_name = 'shop_flat' "
                "  AND column_name = 'link_status'"
            )).scalar()
            if not has_col:
                return out

            catalog_only = int(c.execute(text(
                "SELECT count(*) FROM citypharma.shop_flat "
                "WHERE link_status = 'catalog_only'"
            )).scalar() or 0)
            stock_only = int(c.execute(text(
                "SELECT count(*) FROM citypharma.shop_flat "
                "WHERE link_status = 'stock_only'"
            )).scalar() or 0)
    except Exception:
        # Never raise from a data-quality check.
        return out

    if catalog_only > 0:
        out.append(_make_issue(
            "catalog_gap", "medium", "shop_flat",
            f"{catalog_only:,} article(s) exist in the catalog but have no stock "
            "record (frozen / advance-entry / supplier-out). Expected — these are "
            "shown to users as 'in catalog, not currently stocked', not as errors.",
            "No action required — this is a legitimate catalog/stock gap, not "
            "corruption. If a specific article should be stocked, check the "
            "balance-stock feed for that branch.",
            count=catalog_only,
            recoverable=None,
        ))

    if stock_only > 0:
        out.append(_make_issue(
            "orphan_stock", "medium", "shop_flat",
            f"{stock_only:,} stock row(s) have no matching catalog article (delayed "
            "catalog update). The code is searchable, but catalog details "
            "(brand/generic) are missing — add the article to the catalog to enrich.",
            "Add the missing article(s) to the catalog so brand/generic details "
            "appear. The stock code stays searchable in the meantime.",
            count=stock_only,
            recoverable=None,
        ))

    return out


def scan_project(eng: Engine, slug: str, force: bool = False) -> dict:
    """Run the full scan. Returns the payload (also caches it)."""
    now = time.time()
    if not force:
        cached = _CACHE.get(slug)
        if cached and (now - cached[0]) < _CACHE_TTL_S:
            return cached[1]

    issues: list[dict] = []
    sem_model = _load_semantic_model(eng, slug)
    value_maps = _load_value_maps(eng, slug)

    try:
        tables = _list_tables(eng, slug)
    except Exception:
        logger.exception("data-quality: list_tables failed for %s", slug)
        tables = []

    # ------------------------------------------------------------------
    # Per-table checks
    # ------------------------------------------------------------------
    table_columns: dict[str, list[str]] = {}
    for tbl in tables:
        # cast_artifact (cheap, name-based)
        if tbl.endswith("_casted"):
            issues.append(_make_issue(
                "cast_artifact", "low", tbl,
                f"Table `{tbl}` is a type-cast artifact left over from training.",
                "Drop after verifying the canonical table is current: "
                f"`DROP TABLE \"{slug}\".\"{tbl}\";`",
            ))

        # tiny_table
        n_rows = _row_count(eng, slug, tbl)
        if 0 < n_rows < 100:
            issues.append(_make_issue(
                "tiny_table", "low", tbl,
                f"Table has only {n_rows} row(s) — low statistical value.",
                "Verify the upload completed, or treat results from this table as illustrative only.",
                count=n_rows,
            ))

        # low_codex_confidence (from semantic_model)
        meta = sem_model.get(tbl, {})
        missing_fields = [k for k in ("purpose", "grain", "primary_keys")
                          if not meta.get(k)]
        if missing_fields:
            issues.append(_make_issue(
                "low_codex_confidence", "medium", tbl,
                f"Semantic model missing: {', '.join(missing_fields)}.",
                "Re-run TRAIN ALL to regenerate Codex-enriched metadata, "
                "or hand-edit dash_table_metadata.",
            ))

        # Columns
        try:
            cols = _list_columns(eng, slug, tbl)
        except Exception:
            cols = []
        col_names = [c[0] for c in cols]
        table_columns[tbl] = col_names
        fk_cols = _foreign_key_columns(eng, slug, tbl) if cols else set()
        col_metas = {c.get("name"): c for c in (meta.get("table_columns") or [])}

        for col, dtype in cols:
            cmeta = col_metas.get(col, {})
            cdesc = (cmeta.get("description") or "").strip()

            # opaque_columns
            if len(col) < 4 or col.lower() in {"other", "misc", "tmp", "x", "y", "z"} or not cdesc:
                if len(col) < 4 or not cdesc:
                    sev = "low" if cdesc else "medium"
                    issues.append(_make_issue(
                        "opaque_columns", sev, tbl,
                        f"Column `{col}` is short or undocumented — meaning is unclear.",
                        "Add a description in dash_table_metadata.metadata.table_columns "
                        "or rename to a self-describing name.",
                        column=col,
                    ))

            # missing_fk
            if _looks_like_fk_name(col) and col not in fk_cols:
                issues.append(_make_issue(
                    "missing_fk", "medium", tbl,
                    f"Column `{col}` looks like a foreign key but no FK constraint exists.",
                    "Add a FK: `ALTER TABLE \"{s}\".\"{t}\" ADD CONSTRAINT fk_{t}_{c} "
                    "FOREIGN KEY (\"{c}\") REFERENCES <parent>(<pk>);`".format(
                        s=slug, t=tbl, c=col),
                    column=col,
                ))

            # text_dates (sample-based)
            if _is_text_type(dtype):
                sample = _sample_text_values(eng, slug, tbl, col, limit=200)
                if sample:
                    hits = sum(1 for v in sample if _looks_like_date(v))
                    if hits / len(sample) > 0.7:
                        issues.append(_make_issue(
                            "text_dates", "high", tbl,
                            f"Column `{col}` is TEXT but {int(100 * hits / len(sample))}% "
                            "of values look like dates.",
                            "Convert to TIMESTAMPTZ: "
                            f"`ALTER TABLE \"{slug}\".\"{tbl}\" "
                            f"ALTER COLUMN \"{col}\" TYPE TIMESTAMPTZ "
                            f"USING to_timestamp(\"{col}\", 'DD/MM/YYYY HH24:MI');`",
                            column=col,
                            count=hits,
                        ))

                # sci_notation_id — detect Excel-collapsed ID columns (e.g. "1E+12")
                # Only fires for columns whose name looks like an ID/code column.
                # Two triggers: (a) >50% of sampled values match sci-notation regex, OR
                # (b) the table has >1000 rows but the column has ≤2 distinct values
                # (requires a separate distinct-count query — capped at 3 to keep it cheap).
                if _ID_COL_NAME_RE.search(col):
                    sci_hits = sum(1 for v in sample if _SCI_NOTATION_RE.match(v.strip()))
                    sci_frac = sci_hits / len(sample) if sample else 0
                    sci_distinct = None
                    if sci_frac <= 0.5 and n_rows > 1000:
                        # cheap check: cap at 3 distinct → if ≤2 returned, collapsed
                        sci_distinct = _distinct_count(eng, slug, tbl, col, cap=3)
                    sci_collapsed = sci_frac > 0.5 or (sci_distinct is not None and sci_distinct <= 2)
                    if sci_collapsed:
                        sample_val = sample[0] if sample else "?"
                        n_distinct_display = sci_distinct if sci_distinct is not None else (
                            _distinct_count(eng, slug, tbl, col, cap=3))
                        # Effectively dead = the column is overwhelmingly collapsed:
                        # ≤2 distinct (all rounded to one value) OR >90% of sampled
                        # values are sci-notation. A few real rows don't make the
                        # originals recoverable for the corrupted majority.
                        _dead = n_distinct_display <= 2 or sci_frac >= 0.9
                        issues.append(_make_issue(
                            "sci_notation_id", "high", tbl,
                            f"Column `{col}` contains Excel scientific-notation values "
                            f"(e.g. '{sample_val}') — large IDs have been collapsed "
                            f"(only {n_distinct_display} distinct value(s) over {n_rows:,} rows). "
                            f"The catalog↔stock join on `{col}` WILL return 0 matches; "
                            f"every stock answer will show 'unavailable'.",
                            (f"The original codes are GONE — every value rounded to '{sample_val}', "
                             f"so they cannot be recovered from this file. Re-export the data from the "
                             f"SOURCE system (POS / ERP / database) with `{col}` as TEXT, and do NOT open "
                             f"it in Excel before upload (Excel re-collapses large numbers). "
                             f"Re-uploading this same file will NOT fix it.")
                            if _dead else
                            (f"Re-export from source with `{col}` as TEXT (not General/Number in Excel), "
                             f"or export CSV directly from the source system without opening in Excel."),
                            column=col,
                            count=sci_hits if sci_frac > 0.5 else n_distinct_display,
                            root_cause="Excel auto-converted large ID numbers to scientific notation "
                                       "(e.g. 1000000131948 → 1E+12) before/during CSV export — the file "
                                       "arrived already corrupted, not damaged by the loader.",
                            recoverable=(not _dead),
                        ))

                # unicode_non_latin (cheap regex pass on the same sample)
                nonascii_hits = sum(1 for v in sample if _has_non_ascii(v))
                if nonascii_hits > 0:
                    issues.append(_make_issue(
                        "unicode_non_latin", "info", tbl,
                        f"Column `{col}` contains non-ASCII characters in "
                        f"{nonascii_hits}/{len(sample)} sampled rows.",
                        "Ensure the agent's prompt + downstream pipelines handle UTF-8 correctly. "
                        "Consider adding a Latin-script alias column for search.",
                        column=col,
                        count=nonascii_hits,
                    ))

            # high_null_pct (skip if tiny — noise)
            if n_rows >= 100:
                pct = _null_pct(eng, slug, tbl, col, n_rows)
                if pct > 50.0:
                    sev = "high" if pct > 80 else "medium"
                    issues.append(_make_issue(
                        "high_null_pct", sev, tbl,
                        f"Column `{col}` is {pct:.0f}% NULL.",
                        "Verify the upstream extract — column may be unused or extracted incorrectly. "
                        "Drop or backfill.",
                        column=col,
                        count=int(pct),
                    ))

            # enum_no_value_map
            if _is_int_type(dtype) and n_rows >= 50:
                ndistinct = _distinct_count(eng, slug, tbl, col, cap=12)
                if 1 < ndistinct < 10 and col.lower() not in value_maps:
                    issues.append(_make_issue(
                        "enum_no_value_map", "low", tbl,
                        f"Column `{col}` is an integer with only {ndistinct} distinct "
                        "values but has no value-map in Company Brain.",
                        "Add a value-map entry so agents render meaningful labels "
                        "(e.g. 1→'Active', 2→'Inactive').",
                        column=col,
                        count=ndistinct,
                    ))

    # ------------------------------------------------------------------
    # Cross-table: duplicate_tables (>80% column overlap)
    # ------------------------------------------------------------------
    tbl_list = list(table_columns.items())
    for i in range(len(tbl_list)):
        a_name, a_cols = tbl_list[i]
        a_set = set(c.lower() for c in a_cols)
        if not a_set:
            continue
        for j in range(i + 1, len(tbl_list)):
            b_name, b_cols = tbl_list[j]
            b_set = set(c.lower() for c in b_cols)
            if not b_set:
                continue
            overlap = len(a_set & b_set) / max(len(a_set), len(b_set))
            if overlap > 0.8:
                issues.append(_make_issue(
                    "duplicate_tables", "medium", a_name,
                    f"`{a_name}` and `{b_name}` share "
                    f"{int(overlap * 100)}% of columns — likely duplicate.",
                    f"Pick one as canonical and drop the other "
                    f"(`DROP TABLE \"{slug}\".\"{b_name}\";`), "
                    "or rename to clarify their distinct purpose.",
                    count=int(overlap * 100),
                ))

    # ------------------------------------------------------------------
    # Schema/table-level checks (run once over derived tables, not per-column).
    # catalog_gap / orphan_stock — LEGITIMATE catalog↔stock gaps on shop_flat
    # (distinct from the per-column sci_notation_id corruption check). Fail-soft.
    # ------------------------------------------------------------------
    issues.extend(_scan_shop_flat_link_gaps(eng, slug))

    # ------------------------------------------------------------------
    # Enrich each issue with total_rows + a few real sample values so the UI
    # can show "AFFECTED n / total" and "SAMPLE …" without extra round-trips.
    # ------------------------------------------------------------------
    _rows_cache: dict[str, int] = {}

    def _rows_for(t: str) -> int:
        if t not in _rows_cache:
            _rows_cache[t] = _row_count(eng, slug, t)
        return _rows_cache[t]

    _SAMPLE_TYPES = {"sci_notation_id", "text_dates", "unicode_non_latin",
                     "enum_no_value_map", "opaque_columns", "type_mismatch"}
    for it in issues:
        if not it.get("total_rows"):
            it["total_rows"] = _rows_for(it["table"])
        if not it.get("sample") and it.get("column") and it["type"] in _SAMPLE_TYPES:
            try:
                it["sample"] = _sample_text_values(
                    eng, slug, it["table"], it["column"], limit=5)[:5]
            except Exception:
                it["sample"] = []

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------
    by_severity: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "info": 0}
    by_table: dict[str, int] = {}
    by_type: dict[str, int] = {}
    score_penalty = 0
    for it in issues:
        sev = it["severity"]
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_table[it["table"]] = by_table.get(it["table"], 0) + 1
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1
        score_penalty += _SEVERITY_WEIGHTS.get(sev, 0)

    score = max(0, 100 - score_penalty)
    payload = {
        "issues": issues,
        "by_severity": by_severity,
        "by_table": by_table,
        "by_type": by_type,
        "score": score,
        "table_count": len(tables),
        "issue_count": len(issues),
        "last_scanned": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }

    _CACHE[slug] = (now, payload)
    return payload


def invalidate_cache(slug: str) -> None:
    _CACHE.pop(slug, None)
