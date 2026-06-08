"""
Unified Brain API
=================

Single endpoint backing the merged "Brain" hub. Returns a category's items as
ONE deduplicated list across the AGENT side and the COMPANY side, each tagged
with a merge status (synced / conflict / agent_only / company_only).

GET /api/brain/unified?category=<cat>&scope=<scope>&project_slug=<slug>

  category ∈ definitions | glossary | patterns | rules | graph | schema | org
  scope    ∈ agent | company | personal | all   (default: all)

The merge modules are lazy-imported in try/except so a missing/broken merge
module degrades to an empty list rather than 500-ing the whole hub.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brain", tags=["BrainUnified"])

_engine = _sa_create_engine(db_url, poolclass=NullPool)

LOCKED_SLUG = "citypharma"
_MERGE_CATS = {"definitions", "glossary", "patterns", "rules"}
_PASSTHROUGH_CATS = {"graph", "schema", "org"}
_VALID_SCOPES = {"agent", "company", "personal", "all"}

# categories whose AGENT side is a real writable table (promote/pull/resolve work)
_ACTIONABLE_CATS = {"definitions", "patterns", "rules"}
# unified category -> dash_company_brain.category for personal-scope reads
_PERSONAL_COMPANY_CAT = {
    "definitions": "formula", "glossary": "glossary",
    "patterns": "pattern", "rules": "threshold",
}


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _personal_items(conn, category: str, user_id) -> list[dict]:
    """Personal-scope brain entries (dash_company_brain rows owned by user_id)."""
    cat = _PERSONAL_COMPANY_CAT.get(category)
    if not cat or user_id is None:
        return []
    rows = conn.execute(
        text(
            """
            SELECT id, name, definition, metadata
            FROM dash_company_brain
            WHERE category = :c AND user_id = :uid
            """
        ),
        {"c": cat, "uid": user_id},
    ).mappings().all()
    return [
        {
            "category": category,
            "name": r["name"],
            "key": f"{category}::{(r['name'] or '').strip().lower()}",
            "agent_value": None,
            "company_value": r["definition"],
            "agent_id": None,
            "company_id": r["id"],
            "status": "company_only",
            "meta": {"personal": True, "metadata": r["metadata"]},
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Merge dispatch (lazy, fail-soft)
# --------------------------------------------------------------------------- #
def _run_merge(category: str, conn, slug: str) -> list[dict]:
    try:
        if category == "definitions":
            from app.brain_merge_definitions import merge_definitions
            return merge_definitions(conn, slug)
        if category == "glossary":
            from app.brain_merge_glossary import merge_glossary
            return merge_glossary(conn, slug)
        if category == "patterns":
            from app.brain_merge_patterns import merge_patterns
            return merge_patterns(conn, slug)
        if category == "rules":
            from app.brain_merge_rules import merge_rules
            return merge_rules(conn, slug)
    except Exception as e:  # noqa: BLE001 — fail-soft per category
        logger.warning("brain merge '%s' failed: %s", category, e)
    return []


# --------------------------------------------------------------------------- #
# Passthrough builders (graph / schema / org)
# --------------------------------------------------------------------------- #
# predicates that produce one triple per distinct cell-value (value-spam).
# These are collapsed into a single grouped row instead of N junk rows.
_GRAPH_COLLAPSE_PREDICATES = {"found_in_column", "value_of", "appears_in", "distinct_value"}


def _passthrough_graph(conn, slug: str) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT id, subject, predicate, object, source_type, confidence
            FROM dash_knowledge_triples
            WHERE project_slug = :slug
            LIMIT 2000
            """
        ),
        {"slug": slug},
    ).mappings().all()

    out: list[dict] = []
    # group key -> {object, predicate, values[], first_id}
    collapsed: dict[tuple, dict] = {}

    for r in rows:
        pred = (r["predicate"] or "").strip()
        if pred in _GRAPH_COLLAPSE_PREDICATES:
            gk = (pred, r["object"])
            g = collapsed.setdefault(
                gk, {"predicate": pred, "object": r["object"], "values": [], "first_id": r["id"]}
            )
            g["values"].append(str(r["subject"]))
            continue
        # real relational triple — keep as-is
        out.append(
            {
                "category": "graph",
                "name": f"{r['subject']} → {r['predicate']} → {r['object']}",
                "key": f"graph::{r['id']}",
                "agent_value": f"{r['subject']} {r['predicate']} {r['object']}",
                "company_value": None,
                "agent_id": r["id"],
                "company_id": None,
                "status": "agent_only",
                "meta": {
                    "subject": r["subject"],
                    "predicate": r["predicate"],
                    "object": r["object"],
                    "source_type": r["source_type"],
                    "confidence": r["confidence"],
                },
            }
        )

    # emit one collapsed summary row per (predicate, object) group
    for (pred, obj), g in collapsed.items():
        vals = g["values"]
        sample = ", ".join(vals[:8]) + ("…" if len(vals) > 8 else "")
        out.append(
            {
                "category": "graph",
                "name": f"{pred} · {obj}",
                "key": f"graph::group::{pred}::{obj}",
                "agent_value": f"{len(vals)} values: {sample}",
                "company_value": None,
                "agent_id": g["first_id"],
                "company_id": None,
                "status": "agent_only",
                "meta": {"predicate": pred, "object": obj, "value_count": len(vals), "grouped": True},
            }
        )

    # relational rows first, then collapsed groups
    out.sort(key=lambda x: (1 if x["meta"].get("grouped") else 0, x["name"]))
    return out


def _passthrough_schema(conn, slug: str) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT id, table_name, row_count, updated_at
            FROM dash_table_metadata
            WHERE project_slug = :slug
            ORDER BY table_name
            """
        ),
        {"slug": slug},
    ).mappings().all()
    return [
        {
            "category": "schema",
            "name": r["table_name"],
            "key": f"schema::{(r['table_name'] or '').lower()}",
            "agent_value": f"{r['row_count'] or 0} rows",
            "company_value": None,
            "agent_id": r["id"],
            "company_id": None,
            "status": "agent_only",
            "meta": {
                "row_count": r["row_count"],
                "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
            },
        }
        for r in rows
    ]


def _passthrough_org(conn, slug: str) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT id, category, name, definition
            FROM dash_company_brain
            WHERE category IN ('org', 'threshold', 'calendar', 'benchmark')
              AND (project_slug = :slug OR project_slug IS NULL)
            ORDER BY category, name
            """
        ),
        {"slug": slug},
    ).mappings().all()
    return [
        {
            "category": "org",
            "name": r["name"],
            "key": f"org::{(r['name'] or '').lower()}",
            "agent_value": None,
            "company_value": r["definition"],
            "agent_id": None,
            "company_id": r["id"],
            "status": "company_only",
            "meta": {"company_category": r["category"]},
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Scope filter
# --------------------------------------------------------------------------- #
def _scope_filter(items: list[dict], scope: str) -> list[dict]:
    if scope == "all":
        return items
    if scope == "agent":
        return [i for i in items if i.get("agent_id") is not None]
    # company + personal both narrow to the company side for the skeleton
    return [i for i in items if i.get("company_id") is not None]


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@router.get("/unified")
def unified(
    request: Request,
    category: str = "definitions",
    scope: str = "all",
    project_slug: str = LOCKED_SLUG,
):
    user = _get_user(request)
    uid = user.get("user_id") or user.get("id")

    category = (category or "definitions").strip().lower()
    scope = (scope or "all").strip().lower()
    if scope not in _VALID_SCOPES:
        scope = "all"

    if category not in _MERGE_CATS and category not in _PASSTHROUGH_CATS:
        raise HTTPException(400, f"unknown category: {category}")

    slug = project_slug or LOCKED_SLUG
    personal = scope == "personal"

    with _engine.connect() as conn:
        if personal and category in _MERGE_CATS:
            items = _personal_items(conn, category, uid)
        elif category in _MERGE_CATS:
            items = _run_merge(category, conn, slug)
        elif category == "graph":
            items = _passthrough_graph(conn, slug)
        elif category == "schema":
            items = _passthrough_schema(conn, slug)
        else:  # org
            items = _passthrough_org(conn, slug)

    # tag which items support promote/pull/resolve in the UI
    actionable = (category in _ACTIONABLE_CATS) and not personal
    for it in items:
        it.setdefault("meta", {})
        if isinstance(it["meta"], dict):
            it["meta"]["actionable"] = actionable

    # personal items are already user-scoped; otherwise narrow by side
    if not personal:
        items = _scope_filter(items, scope)

    return {
        "category": category,
        "scope": scope,
        "count": len(items),
        "items": items,
    }
