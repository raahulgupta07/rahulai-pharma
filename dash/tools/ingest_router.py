"""Ingest router — decide how to absorb each uploaded file/sheet.

Hybrid, two-tier (cheap deterministic first, LLM only on ambiguity):

  exact column-set match   → append   (no LLM, confidence 0.99)
  close match (>=70% cols) → LLM route → append / transform_then_append / new_table
  no close match           → new_table (no LLM)

Why combine instead of one-table-per-file: a CRM that drops one CSV per month
should land in ONE canonical table with a `_period` stamp, not 6 sibling tables
that every query must UNION (the source of the "1804 vs 1544" miscounts —
CRM feedback 2026-05-21). Determinism matters here, so the LLM task runs at
temperature 0 (see TRAINING_CONFIGS["ingest_router"]).

The router only *decides*. Appending is near-irreversible, so callers must:
  - auto-execute only when confidence >= AUTO_APPLY_CONFIDENCE,
  - else create a new table (reversible) and surface the suggestion,
  - always stamp `_source_file` (+ `_period` when detectable) so a bad merge
    can be undone with a single DELETE ... WHERE _source_file = ...
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

_lg = logging.getLogger("dash.ingest_router")

_STOP_TOKENS = {"_source_file", "_source_sheet", "_period"}


def _norm_cols(cols) -> list[str]:
    """Lowercase, strip, drop our own bookkeeping columns, keep order-stable."""
    out = []
    for c in cols:
        n = str(c).lower().strip()
        if n and n not in _STOP_TOKENS:
            out.append(n)
    return out


def col_fingerprint(cols) -> str:
    """Stable hash of a column SET (order-independent). Same cols → same hash."""
    norm = sorted(set(_norm_cols(cols)))
    return hashlib.md5("|".join(norm).encode()).hexdigest()


# Filename → period. Handles Jan2025 / 2025-01 / apr_25 / 2025_04 / Q1-2025.
_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def detect_period(name: str) -> str | None:
    """Best-effort YYYY-MM (or YYYY) extracted from a file/sheet name."""
    if not name:
        return None
    s = str(name).lower()
    # 2025-01 / 2025_01 / 202501
    m = re.search(r"(20\d{2})[-_]?(0[1-9]|1[0-2])", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # jan2025 / apr_25 / mar'25
    m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*['_ -]?(\d{2,4})", s)
    if m:
        mm = _MONTHS[m.group(1)]
        yr = m.group(2)
        yr = ("20" + yr) if len(yr) == 2 else yr
        return f"{yr}-{mm}"
    # bare year
    m = re.search(r"\b(20\d{2})\b", s)
    if m:
        return m.group(1)
    return None
