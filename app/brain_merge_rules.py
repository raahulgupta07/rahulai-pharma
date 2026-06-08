"""
Brain merge — rules
====================

Single-Brain merge for the "rules" category.

AGENT side   = dash_rules_db (name, value = definition)
COMPANY side = dash_company_brain WHERE category IN
               ('threshold','org','calendar','benchmark')  (name + definition)

Unioned by the shared dedup key:  category + "::" + lower(trim(name))

Caller passes a live SQLAlchemy connection (see app/brain.py idiom). This
module defines ONLY the merge function (+ small helpers) — no router.
"""

from sqlalchemy import text

CATEGORY = "rules"
_COMPANY_CATS = ("threshold", "org", "calendar", "benchmark")


def _key(name) -> str:
    return f"{CATEGORY}::{(name or '').strip().lower()}"


def _norm(v):
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
    return "company_only"


def merge_rules(conn, project_slug: str = "citypharma") -> list[dict]:
    """Union agent rules-db with company threshold/org/calendar/benchmark."""
    merged: dict[str, dict] = {}

    # ----- AGENT side: dash_rules_db (name -> definition) -----
    agent_rows = conn.execute(
        text(
            """
            SELECT id, name, definition, type, category, source
            FROM dash_rules_db
            WHERE project_slug = :slug
            """
        ),
        {"slug": project_slug},
    ).mappings().all()

    for r in agent_rows:
        name = r["name"]
        if not name:
            continue
        k = _key(name)
        meta: dict = {}
        if r["type"] is not None:
            meta["type"] = r["type"]
        if r["category"] is not None:
            meta["rule_category"] = r["category"]
        if r["source"] is not None:
            meta["source"] = r["source"]
        if k not in merged:
            merged[k] = {
                "category": CATEGORY,
                "name": name,
                "key": k,
                "agent_value": r["definition"],
                "company_value": None,
                "agent_id": r["id"],
                "company_id": None,
                "status": "agent_only",
                "meta": meta,
            }

    # ----- COMPANY side: dash_company_brain (threshold/org/calendar/benchmark) -----
    company_rows = conn.execute(
        text(
            """
            SELECT id, category, name, definition, metadata
            FROM dash_company_brain
            WHERE category = ANY(:cats)
              AND (project_slug = :slug OR project_slug IS NULL)
            """
        ),
        {"cats": list(_COMPANY_CATS), "slug": project_slug},
    ).mappings().all()

    for r in company_rows:
        name = r["name"]
        if not name:
            continue
        k = _key(name)
        item = merged.get(k)
        if item is None:
            meta: dict = {"company_category": r["category"]}
            if r["metadata"] is not None:
                meta["metadata"] = r["metadata"]
            merged[k] = {
                "category": CATEGORY,
                "name": name,
                "key": k,
                "agent_value": None,
                "company_value": r["definition"],
                "agent_id": None,
                "company_id": r["id"],
                "status": "company_only",
                "meta": meta,
            }
        else:
            if item["company_value"] is None:
                item["company_value"] = r["definition"]
                item["company_id"] = r["id"]
                item["meta"]["company_category"] = r["category"]
                if r["metadata"] is not None:
                    item["meta"]["metadata"] = r["metadata"]

    for item in merged.values():
        item["status"] = _status(item["agent_value"], item["company_value"])

    return list(merged.values())
