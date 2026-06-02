"""
Column Classifier
=================

Sample-value-driven classification of text columns into one of:

  - ``"lineage"``    — name matches the lineage-column registry
  - ``"skip"``        — empty, high-cardinality (IDs / unique names), or SQL error
  - ``"free_text"``   — long / multi-word values; multi-byte safe (Burmese / CJK)
  - ``"dimension"``   — short, low-cardinality categorical values

Uses a single SQL aggregate over ``WHERE col IS NOT NULL`` to compute total
rows, distinct count, avg/max char length, and avg byte length. Multi-byte
safe — Burmese / CJK sentences at 60 visual chars are ~180-240 bytes, so we
check ``avg_bytes`` alongside ``avg_chars`` / ``max_chars``.

Also exposes ``is_constant_column`` for the training QA generator to
suppress "trend by X" / "by X timestamp" templates over columns with ≤1
distinct value (e.g. CityPharma's ``created_at`` 1-row-trend bug).
"""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import text

from dash.utils.column_metadata import LINEAGE_COLUMNS


log = logging.getLogger(__name__)


Classification = Literal["dimension", "free_text", "skip", "lineage"]


def classify_text_column(conn, schema: str, table: str, col: str) -> Classification:
    """Classify a text column based on sample-value stats.

    Returns one of ``{"dimension", "free_text", "skip", "lineage"}``.

    Rules (in order):
      1. Lineage column (name in ``LINEAGE_COLUMNS``) → ``"lineage"``
      2. ``total == 0`` → ``"skip"``
      3. ``avg_chars > 25`` OR ``max_chars > 60`` OR ``avg_bytes > 50`` → ``"free_text"``
         (multi-byte safe — Burmese / CJK sentences)
      4. ``distinct_n > 50`` OR ``(distinct_n / total) > 0.5`` → ``"skip"``
         (high-cardinality, IDs / unique names)
      5. Otherwise → ``"dimension"``

    Fail-soft: any SQL error returns ``"skip"`` so the caller can safely
    continue iterating columns without try/except clutter at every call site.
    """
    if col in LINEAGE_COLUMNS:
        return "lineage"

    try:
        row = conn.execute(text(
            f'SELECT COUNT(*) AS total, '
            f'COUNT(DISTINCT "{col}") AS distinct_n, '
            f'AVG(LENGTH("{col}"))::float AS avg_chars, '
            f'MAX(LENGTH("{col}")) AS max_chars, '
            f'AVG(OCTET_LENGTH("{col}"))::float AS avg_bytes '
            f'FROM {schema}."{table}" '
            f'WHERE "{col}" IS NOT NULL'
        )).fetchone()
    except Exception as exc:
        log.debug("classify_text_column: SQL error on %s.%s.%s: %s",
                  schema, table, col, exc)
        return "skip"

    if row is None:
        return "skip"

    total = int(row[0] or 0)
    if total == 0:
        return "skip"

    distinct_n = int(row[1] or 0)
    avg_chars = float(row[2] or 0)
    max_chars = int(row[3] or 0)
    avg_bytes = float(row[4] or 0)

    # Free-text proxies — long avg / single huge value / multi-byte bytes
    if avg_chars > 25 or max_chars > 60 or avg_bytes > 50:
        return "free_text"

    # High-cardinality — IDs or unique names, not useful as dimensions
    if distinct_n > 50:
        return "skip"
    if total > 0 and (distinct_n / total) > 0.5:
        return "skip"

    return "dimension"


def is_constant_column(conn, schema: str, table: str, col: str) -> bool:
    """Return True if the column has ≤1 distinct non-null value.

    Used by training QA generator to skip "trend by X" / "by X timestamp"
    templates that would produce a meaningless 1-row trend (e.g. CityPharma
    ``created_at`` populated with a single ingestion timestamp).

    Fail-soft on SQL error → returns False so the caller emits the template
    rather than silently swallowing legitimate columns.
    """
    try:
        row = conn.execute(text(
            f'SELECT COUNT(DISTINCT "{col}") '
            f'FROM {schema}."{table}" '
            f'WHERE "{col}" IS NOT NULL'
        )).fetchone()
    except Exception as exc:
        log.debug("is_constant_column: SQL error on %s.%s.%s: %s",
                  schema, table, col, exc)
        return False

    if row is None:
        return False
    return int(row[0] or 0) <= 1


__all__ = [
    "classify_text_column",
    "is_constant_column",
    "Classification",
]
