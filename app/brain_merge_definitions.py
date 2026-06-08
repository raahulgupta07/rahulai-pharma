"""
Brain merge — definitions
==========================

Merges the AGENT-side metric definitions (``dash_metric_definitions``: name +
description) with the COMPANY-side formula entries
(``dash_company_brain WHERE category='formula'``: name + definition) into a
single deduplicated list of "definitions" items.

Dedup key = ``category + "::" + lower(trim(name))``.

This module reuses the app's existing SQLAlchemy connection idiom (see
``app/brain.py``): plain ``text()`` queries on a live connection passed in by
the caller. It defines ONLY the merge function (+ small helpers) — no router.
"""

from sqlalchemy import text

CATEGORY = "definitions"


def _norm(value) -> str:
    """Normalize a name into the dedup-key form: lower + trimmed."""
    return (value or "").strip().lower()


def _make_key(name) -> str:
    return f"{CATEGORY}::{_norm(name)}"


def _status_for(agent_value, company_value) -> str:
    """Compute the merge status enum for a pair of (agent, company) values."""
    a_present = agent_value is not None and str(agent_value).strip() != ""
    c_present = company_value is not None and str(company_value).strip() != ""

    if a_present and c_present:
        if _norm(agent_value) == _norm(company_value):
            return "synced"
        return "conflict"
    if a_present:
        return "agent_only"
    if c_present:
        return "company_only"
    # Neither present — treat as company_only fallback (should not occur).
    return "company_only"


def merge_definitions(conn, project_slug: str = "citypharma") -> list[dict]:
    """Merge agent + company definitions into a deduped contract-shaped list.

    ``conn`` is a live SQLAlchemy connection passed by the caller.
    """
    merged: dict[str, dict] = {}

    # ----- AGENT side: dash_metric_definitions (name + description) -----
    agent_rows = conn.execute(
        text(
            """
            SELECT id, name, description, synonyms, kind, measure_col
            FROM dash_metric_definitions
            WHERE project_slug = :slug
            """
        ),
        {"slug": project_slug},
    ).mappings().all()

    for r in agent_rows:
        name = r["name"]
        key = _make_key(name)
        meta: dict = {}
        if r["synonyms"] is not None:
            meta["synonyms"] = r["synonyms"]
        if r["kind"] is not None:
            meta["kind"] = r["kind"]
        if r["measure_col"] is not None:
            meta["measure_col"] = r["measure_col"]

        merged[key] = {
            "category": CATEGORY,
            "name": name,
            "key": key,
            "agent_value": r["description"],
            "company_value": None,
            "agent_id": r["id"],
            "company_id": None,
            "status": "agent_only",
            "meta": meta,
        }

    # ----- COMPANY side: dash_company_brain WHERE category='formula' -----
    company_rows = conn.execute(
        text(
            """
            SELECT id, name, definition, metadata
            FROM dash_company_brain
            WHERE category = 'formula'
              AND (project_slug = :slug OR project_slug IS NULL)
            """
        ),
        {"slug": project_slug},
    ).mappings().all()

    for r in company_rows:
        name = r["name"]
        key = _make_key(name)
        company_value = r["definition"]
        company_id = r["id"]

        if key in merged:
            item = merged[key]
            item["company_value"] = company_value
            item["company_id"] = company_id
            if r["metadata"] is not None:
                item["meta"]["metadata"] = r["metadata"]
            item["status"] = _status_for(item["agent_value"], company_value)
        else:
            meta: dict = {}
            if r["metadata"] is not None:
                meta["metadata"] = r["metadata"]
            merged[key] = {
                "category": CATEGORY,
                "name": name,
                "key": key,
                "agent_value": None,
                "company_value": company_value,
                "agent_id": None,
                "company_id": company_id,
                "status": "company_only",
                "meta": meta,
            }

    return list(merged.values())
