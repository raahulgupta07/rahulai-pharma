"""
Column Metadata Registry
========================

Single source of truth for column-name patterns that classify columns as
lineage breadcrumbs or free-text content. Used by the knowledge-graph
cell-value extractor, training QA generator, business-rules engine, and
dashboard builders.

New lineage col? Add it to ``LINEAGE_COLUMNS`` (or rely on the
``_source_*`` / ``_period`` prefix convention) and every consumer
(KG, QA gen, business rules, dashboards) picks it up automatically — no
per-consumer edits needed.

Likewise ``SKIP_REGEX_NAMES`` consolidates the free-text-by-name patterns
that used to live inline in ``dash/tools/knowledge_graph.py``.
"""

from __future__ import annotations

import re


# Auto-injected lineage columns. Added by the upload pipeline; never
# semantic dimensions. Any column whose name appears here (case-sensitive,
# since these are platform-controlled) is treated as lineage breadcrumb.
LINEAGE_COLUMNS: frozenset[str] = frozenset({
    "_source_file",
    "_source_sheet",
    "_period",
    "_batch_id",
    "_content_hash",
    "_row_key",
    "_ingested_at",
    "_uploaded_at",
    "_load_id",
    "_run_id",
})


# Column-NAME patterns that imply free-text / non-dimension content even
# without inspecting sample values. Combines:
#   - date/time/timestamp/created/updated/modified suffixes
#   - _at$, _on$, _id$, _uuid, password, token, hash, secret, email
#   - _label$, _desc(ription)?, _note, _comment, _text, _body
#   - narrative, instruction, warning, contraindication, caution
#   - address, url, link, filename, filepath
SKIP_REGEX_NAMES: re.Pattern[str] = re.compile(
    r'(date|time|timestamp|created|updated|modified|_at$|_on$|_id$|_uuid|'
    r'password|token|hash|secret|email|'
    r'_label$|_desc(ription)?$|_note$|_comment$|_text$|_body$|'
    r'narrative|instruction|warning|contraindication|caution|'
    r'address|url|link|filename|filepath)',
    re.IGNORECASE,
)


def is_lineage_column(name: str) -> bool:
    """Return True if the column name is a lineage breadcrumb.

    Matches against ``LINEAGE_COLUMNS`` membership OR the ``_source_`` /
    ``_period`` prefix convention so future lineage columns following
    that convention are auto-detected.
    """
    if not name:
        return False
    if name in LINEAGE_COLUMNS:
        return True
    if name.startswith("_source_"):
        return True
    if name.startswith("_period"):
        return True
    return False


def should_skip_by_name(col_name: str) -> bool:
    """Return True if the column name matches a free-text pattern.

    Pure name-based check. Sample-value inspection lives in
    ``dash.utils.column_classifier.classify_text_column``.
    """
    if not col_name:
        return False
    return bool(SKIP_REGEX_NAMES.search(col_name))


__all__ = [
    "LINEAGE_COLUMNS",
    "SKIP_REGEX_NAMES",
    "is_lineage_column",
    "should_skip_by_name",
]
