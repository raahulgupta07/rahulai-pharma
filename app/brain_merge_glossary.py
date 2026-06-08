"""
Brain merge — glossary
=======================

Single-Brain merge for the "glossary" category.

COMPANY side  = dash_company_brain WHERE category IN ('glossary','alias')
                (name + definition)
AGENT side    = column-level terms harvested from
                dash_table_metadata.metadata->'table_columns'
                (distinct column name + description per table)

Both sides are unioned by the dedup key:
    key = category + "::" + lower(trim(name))

The caller passes a live SQLAlchemy connection (same engine/session pattern the
app already uses — see app/brain.py). This module is pure stdlib + sqlalchemy.text.
"""

from sqlalchemy import text

CATEGORY = "glossary"


def _key(name: str) -> str:
    """Dedup key: category + '::' + lower(trim(name))."""
    return f"{CATEGORY}::{(name or '').strip().lower()}"


def _norm(v):
    """Lower/trim for case-insensitive equality compare; None-safe."""
    if v is None:
        return None
    return str(v).strip().lower()


def _status(agent_value, company_value) -> str:
    a = agent_value is not None and str(agent_value).strip() != ""
    c = company_value is not None and str(company_value).strip() != ""
    if a and c:
        return "synced" if _norm(agent_value) == _norm(company_value) else "conflict"
    if a:
        return "agent_only"
    if c:
        return "company_only"
    # neither present — treat as company_only by convention (shouldn't happen)
    return "company_only"


def merge_glossary(conn, project_slug: str = "citypharma") -> list[dict]:
    """
    Union company-brain glossary/alias entries with agent column-level terms.

    Returns a list of dicts, each matching the shared merged-item contract.
    """
    merged: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    # COMPANY side: dash_company_brain (category IN glossary, alias)
    # ------------------------------------------------------------------ #
    company_rows = conn.execute(
        text(
            """
            SELECT id, category, name, definition
            FROM dash_company_brain
            WHERE category IN ('glossary', 'alias')
              AND (project_slug = :slug OR project_slug IS NULL)
            """
        ),
        {"slug": project_slug},
    ).fetchall()

    for row in company_rows:
        cid = row[0]
        ccat = row[1]
        name = row[2]
        definition = row[3]
        if not name:
            continue
        k = _key(name)
        item = merged.get(k)
        if item is None:
            item = {
                "category": CATEGORY,
                "name": name,
                "key": k,
                "agent_value": None,
                "company_value": definition,
                "agent_id": None,
                "company_id": cid,
                "status": "company_only",
                "meta": {"category": ccat},
            }
            merged[k] = item
        else:
            # company side wins for an existing key
            if item["company_value"] is None:
                item["company_value"] = definition
                item["company_id"] = cid
                item["meta"]["category"] = ccat
            item["status"] = _status(item["agent_value"], item["company_value"])

    # ------------------------------------------------------------------ #
    # AGENT side: distinct columns from table_metadata->'table_columns'
    # ------------------------------------------------------------------ #
    agent_rows = conn.execute(
        text(
            """
            SELECT m.id           AS source_id,
                   m.table_name   AS source_table,
                   col->>'name'   AS col_name,
                   col->>'description' AS col_desc,
                   col->>'type'   AS col_type
            FROM dash_table_metadata m
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(m.metadata->'table_columns', '[]'::jsonb)
            ) AS col
            WHERE m.project_slug = :slug
              AND col->>'name' IS NOT NULL
            """
        ),
        {"slug": project_slug},
    ).fetchall()

    for row in agent_rows:
        source_id = row[0]
        source_table = row[1]
        col_name = row[2]
        col_desc = row[3]
        col_type = row[4]
        if not col_name:
            continue
        k = _key(col_name)
        item = merged.get(k)
        if item is None:
            item = {
                "category": CATEGORY,
                "name": col_name,
                "key": k,
                "agent_value": col_desc,
                "company_value": None,
                "agent_id": source_id,
                "company_id": None,
                "status": "agent_only",
                "meta": {"source_table": source_table, "type": col_type},
            }
            merged[k] = item
        else:
            # first agent term for this key wins; otherwise keep existing
            if item["agent_value"] is None and item["agent_id"] is None:
                item["agent_value"] = col_desc
                item["agent_id"] = source_id
                item["meta"]["source_table"] = source_table
                if col_type is not None:
                    item["meta"]["type"] = col_type
            item["status"] = _status(item["agent_value"], item["company_value"])

    # finalize status for every item
    for item in merged.values():
        item["status"] = _status(item["agent_value"], item["company_value"])

    return list(merged.values())
