"""Pure helpers for the per-source training pipeline.

These functions intentionally take no engines and perform no I/O so they
can be unit-tested without a database. They emit dialect-correct SQL
fragments and assemble LLM prompts the :class:`ProviderTrainer`
orchestrator dispatches.

Dialects supported: ``postgresql``, ``mysql``, ``tsql`` (Microsoft Fabric
/ SQL Server). Other dialects fall through to ANSI SQL.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Sequence


# ---------------------------------------------------------------------------
# Dialect helpers
# ---------------------------------------------------------------------------

_QUOTE = {
    "postgresql": ('"', '"'),
    "mysql": ("`", "`"),
    "tsql": ("[", "]"),
}


def quote_ident(dialect: str, ident: str) -> str:
    """Quote an identifier for the given dialect."""
    lq, rq = _QUOTE.get(dialect, ('"', '"'))
    return f"{lq}{ident}{rq}"


def qualified(dialect: str, table: str) -> str:
    """Quote a possibly schema-qualified ``schema.table`` identifier."""
    return ".".join(quote_ident(dialect, p) for p in table.split("."))


def dialect_top(dialect: str, n: int) -> tuple[str, str]:
    """Return ``(prefix, suffix)`` SQL fragments for a top-N selection.

    For T-SQL we emit ``SELECT TOP N ...`` (prefix), for the other two we
    use a trailing ``LIMIT N`` (suffix). Caller composes them around the
    base SELECT body.
    """
    if dialect == "tsql":
        return (f"TOP {int(n)} ", "")
    return ("", f" LIMIT {int(n)}")


def dialect_count_distinct(dialect: str, col: str) -> str:
    """COUNT(DISTINCT col) — identical across the three dialects."""
    return f"COUNT(DISTINCT {quote_ident(dialect, col)})"


def dialect_percentile_sql(dialect: str, col: str) -> str | None:
    """Return a percentile (median) SQL fragment, or None if unsupported.

    MySQL pre-8 lacks ``PERCENTILE_CONT``; we return None and let the
    profile step fall back to AVG/MIN/MAX only.
    """
    quoted = quote_ident(dialect, col)
    if dialect == "postgresql":
        return f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {quoted})"
    if dialect == "tsql":
        # T-SQL needs OVER(); caller wraps appropriately.
        return f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {quoted}) OVER ()"
    return None


def numeric_profile_sql(dialect: str, table: str, col: str) -> str:
    """Compose a profile query for a numeric column.

    Returns a single SELECT projecting count, distinct count, min, max,
    avg, and (when available) median. Stddev uses STDDEV_SAMP — present in
    all three target dialects under a compatible name.
    """
    qcol = quote_ident(dialect, col)
    qtbl = qualified(dialect, table)
    pct = dialect_percentile_sql(dialect, col)
    pct_clause = f", {pct} AS p50" if pct else ""
    stddev = "STDEV" if dialect == "tsql" else "STDDEV_SAMP"
    return (
        f"SELECT COUNT(*) AS n, {dialect_count_distinct(dialect, col)} AS ndv, "
        f"MIN({qcol}) AS min_v, MAX({qcol}) AS max_v, AVG({qcol}) AS avg_v, "
        f"{stddev}({qcol}) AS sd_v{pct_clause} "
        f"FROM {qtbl}"
    )


def categorical_profile_sql(dialect: str, table: str, col: str) -> str:
    """Cheap profile for non-numeric columns: row count + NDV only."""
    return (
        f"SELECT COUNT(*) AS n, {dialect_count_distinct(dialect, col)} AS ndv "
        f"FROM {qualified(dialect, table)}"
    )


def dimension_value_sql(dialect: str, table: str, col: str, limit: int = 500) -> str:
    """SELECT col, COUNT(*) GROUP BY col — top N by frequency."""
    qcol = quote_ident(dialect, col)
    qtbl = qualified(dialect, table)
    prefix, suffix = dialect_top(dialect, limit)
    return (
        f"SELECT {prefix}{qcol} AS value, COUNT(*) AS freq "
        f"FROM {qtbl} GROUP BY {qcol} ORDER BY COUNT(*) DESC{suffix}"
    )


def sample_rows_sql(dialect: str, table: str, n: int = 20) -> str:
    """Top-N sample. Trainer fetches twice (head + tail) to mix order."""
    prefix, suffix = dialect_top(dialect, n)
    return f"SELECT {prefix}* FROM {qualified(dialect, table)}{suffix}"


def watermark_max_sql(dialect: str, table: str, col: str) -> str:
    """SELECT MAX(col) — used by watermark register."""
    return (
        f"SELECT MAX({quote_ident(dialect, col)}) AS max_v "
        f"FROM {qualified(dialect, table)}"
    )


# ---------------------------------------------------------------------------
# Hierarchy / relationship SQL builders
# ---------------------------------------------------------------------------


def hierarchy_pair_sql(dialect: str, table: str, parent: str, child: str) -> str:
    """Test if every value of ``child`` maps to exactly one ``parent``.

    Result is two integers; caller compares ``ndv_child_in_pairs`` vs
    ``ndv_child_total``. Equal means a clean parent->child hierarchy.
    """
    qparent = quote_ident(dialect, parent)
    qchild = quote_ident(dialect, child)
    qtbl = qualified(dialect, table)
    return (
        f"SELECT (SELECT COUNT(*) FROM (SELECT {qparent}, {qchild} "
        f"FROM {qtbl} GROUP BY {qparent}, {qchild}) sub) AS pair_rows, "
        f"(SELECT COUNT(DISTINCT {qchild}) FROM {qtbl}) AS ndv_child"
    )


def overlap_count_sql(dialect: str, table_a: str, col_a: str, table_b: str, col_b: str) -> str:
    """Count distinct value overlap between two columns from two tables.

    Returns ``(overlap, ndv_a, ndv_b)``.
    """
    qa_col = quote_ident(dialect, col_a)
    qb_col = quote_ident(dialect, col_b)
    qa_tbl = qualified(dialect, table_a)
    qb_tbl = qualified(dialect, table_b)
    return (
        f"SELECT (SELECT COUNT(*) FROM (SELECT DISTINCT {qa_col} AS v FROM {qa_tbl}) a "
        f"INNER JOIN (SELECT DISTINCT {qb_col} AS v FROM {qb_tbl}) b ON a.v = b.v) AS overlap, "
        f"(SELECT COUNT(DISTINCT {qa_col}) FROM {qa_tbl}) AS ndv_a, "
        f"(SELECT COUNT(DISTINCT {qb_col}) FROM {qb_tbl}) AS ndv_b"
    )


def fuzzy_match_colname(name: str) -> str:
    """Strip common suffixes (``_id``, ``_key``, ``_code``) for fuzzy join match."""
    n = name.lower()
    for suffix in ("_id", "_key", "_code", "_no", "_num"):
        if n.endswith(suffix) and len(n) > len(suffix):
            return n[: -len(suffix)]
    return n


def qa_prompt(
    table: str,
    cols: list[dict[str, Any]],
    sample: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    dims: dict[str, list[Any]] | None,
    dialect: str,
) -> str:
    """Prompt asking the model for 5 verifiable Q&A pairs in JSON form."""
    cols_lines = [
        f"  - {c.get('name')} ({c.get('type', 'unknown')})"
        for c in cols[:60]
    ]
    sample_json = json.dumps(sample[:6], default=str)[:2500]
    profile_json = json.dumps(profile or {}, default=str)[:1200]
    dims_json = json.dumps(dims or {}, default=str)[:1200]

    return (
        "You are generating verifiable analytical Q&A pairs for a database.\n"
        f"DIALECT: {dialect}\n"
        f"TABLE: {table}\n"
        "COLUMNS:\n" + "\n".join(cols_lines) + "\n\n"
        f"SAMPLE ROWS:\n{sample_json}\n\n"
        f"PROFILE:\n{profile_json}\n\n"
        f"DIMENSION VALUES:\n{dims_json}\n\n"
        "Return STRICT JSON only — a list of EXACTLY 5 objects.\n"
        "Each: {\"question\": \"...\", \"sql\": \"<valid SQL for this dialect>\", "
        "\"expected_answer_summary\": \"...\"}\n"
        "Constraints: SQL must reference only the table above. Use SELECT only.\n"
        "Avoid ORDER BY without LIMIT, avoid window functions, prefer GROUP BY summaries.\n"
    )


def relationship_prompt(
    from_table: str, from_col: str, to_table: str, to_col: str, overlap_pct: float
) -> str:
    """Ask the LLM to confirm or reject an implicit join in 1 sentence."""
    return (
        "Two columns share a high distinct-value overlap. Decide whether they form a "
        "meaningful business JOIN (e.g., FK-like).\n"
        f"COLUMN A: {from_table}.{from_col}\n"
        f"COLUMN B: {to_table}.{to_col}\n"
        f"OVERLAP: {overlap_pct:.0%}\n\n"
        "Return STRICT JSON: {\"valid\": true|false, \"reason\": \"<one short sentence>\"}\n"
        "Reject if names suggest unrelated concepts (e.g., user_id vs order_id) or pure coincidence.\n"
    )


# ---------------------------------------------------------------------------
# Sample / row helpers
# ---------------------------------------------------------------------------

def flatten_sample_rows(
    rows: Sequence[Sequence[Any]], cols: Sequence[str]
) -> list[dict[str, Any]]:
    """Convert raw cursor rows into a list of dicts keyed by column name.

    Values that aren't JSON-serialisable (datetimes, Decimal, bytes) are
    coerced to strings so the result feeds straight into LLM prompts.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        rec: dict[str, Any] = {}
        for col, val in zip(cols, row):
            if val is None or isinstance(val, (str, int, float, bool)):
                rec[col] = val
            else:
                rec[col] = str(val)
        out.append(rec)
    return out


def diversify_sample(rows: list[dict[str, Any]], target: int = 20) -> list[dict[str, Any]]:
    """Pick a head/middle/tail mix (~3 each) plus filler.

    Replaces the temptation of always sampling the head — the agent then
    sees seasonality and trailing edge cases.
    """
    if len(rows) <= target:
        return rows
    third = max(3, target // 3)
    head = rows[:third]
    tail = rows[-third:]
    mid_start = max(third, len(rows) // 2 - third // 2)
    middle = rows[mid_start : mid_start + third]
    chosen = head + middle + tail
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for r in chosen:
        marker = id(r)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(r)
    return out[:target]


# ---------------------------------------------------------------------------
# LLM prompt assembly
# ---------------------------------------------------------------------------

# Heuristics for watermark column detection — ordered by preference.
WATERMARK_HINTS = (
    "updated_at",
    "modified_at",
    "_etag",
    "last_modified",
    "last_updated",
    "modified_on",
    "updated_on",
    "_ts",
    "row_version",
)


def detect_watermark_column(columns: Iterable[dict[str, Any]]) -> str | None:
    """Pick the best 'last updated' column from a list of column dicts.

    ``columns`` are the entries from ``introspect()['columns'][table]`` —
    each has at least a ``name`` key. Matching is case-insensitive and
    ranked by the order of :data:`WATERMARK_HINTS`.
    """
    names = {c.get("name", "").lower(): c.get("name") for c in columns}
    for hint in WATERMARK_HINTS:
        if hint in names:
            return names[hint]
    # Fallback: any column ending in _at / _on with a timestamp-y type.
    for low, original in names.items():
        if low.endswith(("_at", "_on")):
            return original
    return None


def _codex_prompt(
    table: str,
    cols: list[dict[str, Any]],
    sample: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    dims: dict[str, list[Any]] | None,
    fks: list[dict[str, Any]] | None,
) -> str:
    """Assemble the Codex enrichment prompt for one table.

    The prompt asks for a strict JSON object the trainer will parse and
    persist verbatim. Sample rows are truncated aggressively so we stay
    inside the 8KB-ish budget recommended for ``deep_analysis``.
    """
    cols_lines = [
        f"  - {c.get('name')} ({c.get('type', 'unknown')})"
        for c in cols[:60]
    ]
    sample_json = json.dumps(sample[:8], default=str)[:3000]
    profile_json = json.dumps(profile or {}, default=str)[:1500]
    dims_json = json.dumps(dims or {}, default=str)[:1500]
    fks_json = json.dumps(fks or [], default=str)[:800]

    return (
        "You are a senior data architect enriching a database catalog.\n"
        f"TABLE: {table}\n"
        f"COLUMNS:\n" + "\n".join(cols_lines) + "\n\n"
        f"SAMPLE ROWS (truncated):\n{sample_json}\n\n"
        f"COLUMN PROFILE:\n{profile_json}\n\n"
        f"DIMENSION VALUES:\n{dims_json}\n\n"
        f"FOREIGN KEYS:\n{fks_json}\n\n"
        "Return STRICT JSON only (no commentary) with this shape:\n"
        "{\n"
        '  "purpose": "<one sentence on why the table exists>",\n'
        '  "grain": "<what one row represents>",\n'
        '  "primary_key": ["col1", "col2"],\n'
        '  "foreign_keys": [{"column": "x", "references": "other.y"}],\n'
        '  "usage_patterns": ["common query 1", "common query 2"],\n'
        '  "freshness": "<refresh cadence guess>"\n'
        "}\n"
    )


# ---------------------------------------------------------------------------
# Drift hash
# ---------------------------------------------------------------------------

def schema_fingerprint(catalog: dict[str, Any]) -> str:
    """Stable SHA-256 over (sorted) tables+columns. Excludes FK ordering."""
    import hashlib

    tables = catalog.get("tables") or []
    columns = catalog.get("columns") or {}
    payload: list[tuple[str, list[tuple[str, str]]]] = []
    for tbl in sorted(tables):
        cols = columns.get(tbl) or []
        flat = sorted(
            (c.get("name", ""), c.get("type", "")) for c in cols
        )
        payload.append((tbl, flat))
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()
