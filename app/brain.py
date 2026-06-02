"""
Company Brain API
=================

Stores abstract business knowledge (formulas, glossary, aliases, patterns,
org structure, thresholds) that ALL project agents can read.

NO actual data values allowed — only definitions, formulas, and structure.
"""

import json
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brain", tags=["Brain"])

_engine = _sa_create_engine(db_url, poolclass=NullPool)

VALID_CATEGORIES = {"glossary", "formula", "alias", "pattern", "org", "threshold", "calendar", "benchmark"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class BrainEntryCreate(BaseModel):
    category: str
    name: str
    definition: str
    metadata: dict = {}
    project_slug: str | None = None
    user_id: int | None = None


class BrainEntryUpdate(BaseModel):
    category: str | None = None
    name: str | None = None
    definition: str | None = None
    metadata: dict | None = None


# ---------------------------------------------------------------------------
# Data leak validation
# ---------------------------------------------------------------------------

def _validate_no_data_leak(text_val: str) -> tuple[bool, str]:
    """Check if text contains specific data values. Returns (is_safe, reason)."""
    # Block specific numbers with units (28.6M, 35M, 204%, 39.6M)
    if re.search(r'\b\d+\.?\d*[MBK]\b', text_val):
        return False, "Contains specific amount (e.g. 28.6M)"
    # Block date-specific data statements
    if re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}.*\d+[%MBK]', text_val):
        return False, "Contains date-specific data"
    # Block "is/was X" data statements (but allow "target is 35%")
    if re.search(r'\b(revenue|sales|profit|loss|growth)\s+(is|was|are|were)\s+\d', text_val, re.IGNORECASE):
        return False, "Contains specific data value"
    # Block SQL
    if re.search(r'\bSELECT\s+.*\bFROM\b', text_val, re.IGNORECASE):
        return False, "Contains SQL query"
    # Block project references
    if re.search(r'\bproj_\w+\b', text_val):
        return False, "Contains project reference"
    # Allow targets/thresholds with % (these are goals, not data)
    # Allow formulas
    return True, ""


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super_admin(request: Request) -> dict:
    user = _get_user(request)
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Super admin only")
    return user


def _get_user_project_slugs(user_id: int | None) -> list[str]:
    """Return project slugs the user owns or has been shared access to."""
    if not user_id:
        return []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT slug FROM public.dash_projects WHERE owner_id = :uid "
                "UNION "
                "SELECT project_slug FROM public.dash_project_shares WHERE user_id = :uid"
            ), {"uid": user_id}).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logger.warning(f"Failed to get user project slugs: {e}")
        return []


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _bootstrap_brain_tables():
    """Create brain tables if not exists."""
    with _engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_company_brain (
                id SERIAL PRIMARY KEY,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                definition TEXT NOT NULL,
                metadata JSONB DEFAULT '{}',
                project_slug TEXT DEFAULT NULL,
                user_id INTEGER DEFAULT NULL,
                created_by TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        # Add columns if they don't exist (migration)
        conn.execute(text("""
            ALTER TABLE public.dash_company_brain ADD COLUMN IF NOT EXISTS project_slug TEXT DEFAULT NULL
        """))
        conn.execute(text("""
            ALTER TABLE public.dash_company_brain ADD COLUMN IF NOT EXISTS user_id INTEGER DEFAULT NULL
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_brain_access_log (
                id SERIAL PRIMARY KEY,
                project_slug TEXT,
                agent_name TEXT,
                category TEXT,
                items_accessed INTEGER DEFAULT 0,
                accessed_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        conn.commit()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/entries")
def list_entries(request: Request, category: str = "", project_slug: str | None = None, scope: str | None = None):
    """List brain entries, optionally filter by category, project_slug, and/or scope."""
    user = _get_user(request)
    uid = user.get("user_id") or user.get("id")

    q = "SELECT id, category, name, definition, metadata, created_by, created_at, updated_at, project_slug, user_id FROM public.dash_company_brain"
    conditions = []
    params = {}

    if category:
        if category not in VALID_CATEGORIES:
            raise HTTPException(400, f"Invalid category. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}")
        conditions.append("category = :cat")
        params["cat"] = category

    # Scope filtering: global, personal, or project
    if scope == "global":
        conditions.append("project_slug IS NULL AND user_id IS NULL")
    elif scope == "personal":
        conditions.append("user_id = :uid")
        params["uid"] = uid
    elif project_slug:
        # Return project-scoped + global entries
        conditions.append("(project_slug = :slug OR project_slug IS NULL)")
        params["slug"] = project_slug
    else:
        # No scope filter — return all accessible
        from app.auth import SUPER_ADMIN
        if user.get("username") != SUPER_ADMIN:
            user_projects = _get_user_project_slugs(uid)
            if user_projects:
                placeholders = ", ".join(f":proj_{i}" for i in range(len(user_projects)))
                for i, p in enumerate(user_projects):
                    params[f"proj_{i}"] = p
                conditions.append(f"(user_id = :uid OR project_slug IN ({placeholders}) OR project_slug IS NULL)")
            else:
                conditions.append("(user_id = :uid OR project_slug IS NULL)")
            params["uid"] = uid

    if conditions:
        q += " WHERE " + " AND ".join(conditions)
    q += " ORDER BY category, name"

    with _engine.connect() as conn:
        rows = conn.execute(text(q), params).fetchall()

    entries = []
    for r in rows:
        meta = r[4] if isinstance(r[4], dict) else json.loads(r[4]) if r[4] else {}
        ps = r[8]
        uid = r[9]
        if ps is not None:
            scope = "project"
        elif uid is not None:
            scope = "personal"
        else:
            scope = "global"
        entries.append({
            "id": r[0], "category": r[1], "name": r[2], "definition": r[3],
            "metadata": meta, "created_by": r[5],
            "created_at": str(r[6]) if r[6] else None,
            "updated_at": str(r[7]) if r[7] else None,
            "project_slug": ps, "user_id": uid, "scope": scope,
        })

    return {"entries": entries, "total": len(entries)}


@router.get("/entries/{entry_id}")
def get_entry(entry_id: int, request: Request):
    """Get a single brain entry by ID."""
    _get_user(request)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, category, name, definition, metadata, created_by, created_at, updated_at "
            "FROM public.dash_company_brain WHERE id = :id"
        ), {"id": entry_id}).fetchone()

    if not row:
        raise HTTPException(404, "Entry not found")

    meta = row[4] if isinstance(row[4], dict) else json.loads(row[4]) if row[4] else {}
    return {
        "id": row[0], "category": row[1], "name": row[2], "definition": row[3],
        "metadata": meta, "created_by": row[5],
        "created_at": str(row[6]) if row[6] else None,
        "updated_at": str(row[7]) if row[7] else None,
    }


@router.post("/entries")
def create_entry(req: BrainEntryCreate, request: Request):
    """Create a brain entry (super admin only). Validates no data leak."""
    user = _require_super_admin(request)

    if req.category not in VALID_CATEGORIES:
        raise HTTPException(400, f"Invalid category. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}")

    # Validate no data leak in definition
    safe, reason = _validate_no_data_leak(req.definition)
    if not safe:
        raise HTTPException(422, f"Data leak detected: {reason}. Brain stores abstract knowledge only.")

    # Also check metadata values
    for val in req.metadata.values():
        if isinstance(val, str):
            safe, reason = _validate_no_data_leak(val)
            if not safe:
                raise HTTPException(422, f"Data leak in metadata: {reason}. Brain stores abstract knowledge only.")

    with _engine.begin() as conn:
        row = conn.execute(text(
            "INSERT INTO public.dash_company_brain (category, name, definition, metadata, created_by, project_slug, user_id) "
            "VALUES (:cat, :name, :def, :meta, :by, :project_slug, :user_id) RETURNING id"
        ), {
            "cat": req.category, "name": req.name, "def": req.definition,
            "meta": json.dumps(req.metadata), "by": user.get("username", ""),
            "project_slug": req.project_slug, "user_id": req.user_id,
        }).fetchone()
        try:
            from app.brain_versions import snapshot_version
            snapshot_version(
                conn,
                brain_id=int(row[0]),
                change_type="create",
                changed_by=user.get("user_id") or user.get("id"),
            )
        except Exception as e:
            logger.warning(f"Brain version snapshot (create) failed: {e}")

    return {"id": row[0], "status": "created"}


@router.put("/entries/{entry_id}")
def update_entry(entry_id: int, req: BrainEntryUpdate, request: Request):
    """Update a brain entry (super admin only)."""
    user = _require_super_admin(request)

    # Check entry exists
    with _engine.connect() as conn:
        existing = conn.execute(text(
            "SELECT id FROM public.dash_company_brain WHERE id = :id"
        ), {"id": entry_id}).fetchone()
    if not existing:
        raise HTTPException(404, "Entry not found")

    # Validate no data leak
    if req.definition:
        safe, reason = _validate_no_data_leak(req.definition)
        if not safe:
            raise HTTPException(422, f"Data leak detected: {reason}. Brain stores abstract knowledge only.")
    if req.metadata:
        for val in req.metadata.values():
            if isinstance(val, str):
                safe, reason = _validate_no_data_leak(val)
                if not safe:
                    raise HTTPException(422, f"Data leak in metadata: {reason}. Brain stores abstract knowledge only.")

    # Build dynamic update
    updates = []
    params = {"id": entry_id}
    if req.category is not None:
        if req.category not in VALID_CATEGORIES:
            raise HTTPException(400, f"Invalid category. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}")
        updates.append("category = :cat")
        params["cat"] = req.category
    if req.name is not None:
        updates.append("name = :name")
        params["name"] = req.name
    if req.definition is not None:
        updates.append("definition = :def")
        params["def"] = req.definition
    if req.metadata is not None:
        updates.append("metadata = :meta")
        params["meta"] = json.dumps(req.metadata)

    if not updates:
        raise HTTPException(400, "No fields to update")

    updates.append("updated_at = NOW()")

    with _engine.begin() as conn:
        conn.execute(text(
            f"UPDATE public.dash_company_brain SET {', '.join(updates)} WHERE id = :id"
        ), params)
        try:
            from app.brain_versions import snapshot_version
            snapshot_version(
                conn,
                brain_id=int(entry_id),
                change_type="update",
                changed_by=user.get("user_id") or user.get("id"),
            )
        except Exception as e:
            logger.warning(f"Brain version snapshot (update) failed: {e}")

    return {"status": "updated"}


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, request: Request):
    """Delete a brain entry (super admin only)."""
    user = _require_super_admin(request)

    with _engine.begin() as conn:
        # Capture state BEFORE delete so we can snapshot it.
        pre_row = conn.execute(text(
            "SELECT id, category, name, definition, metadata, project_slug, user_id "
            "FROM public.dash_company_brain WHERE id = :id"
        ), {"id": entry_id}).fetchone()
        if not pre_row:
            raise HTTPException(404, "Entry not found")

        pre_meta = pre_row[4] if isinstance(pre_row[4], dict) else (json.loads(pre_row[4]) if pre_row[4] else {})
        snap_row = {
            "category": pre_row[1], "name": pre_row[2], "definition": pre_row[3],
            "metadata": pre_meta, "project_slug": pre_row[5], "user_id": pre_row[6],
        }

        result = conn.execute(text(
            "DELETE FROM public.dash_company_brain WHERE id = :id"
        ), {"id": entry_id})
        if result.rowcount == 0:
            raise HTTPException(404, "Entry not found")

        try:
            from app.brain_versions import snapshot_version
            snapshot_version(
                conn,
                brain_id=int(entry_id),
                change_type="delete",
                changed_by=user.get("user_id") or user.get("id"),
                row=snap_row,
            )
        except Exception as e:
            logger.warning(f"Brain version snapshot (delete) failed: {e}")

    return {"status": "deleted"}


@router.get("/stats")
def brain_stats(request: Request):
    """Count entries by category + total."""
    _get_user(request)

    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT category, COUNT(*) FROM public.dash_company_brain GROUP BY category ORDER BY category"
        )).fetchall()

    by_category = {r[0]: r[1] for r in rows}
    total = sum(by_category.values())

    return {"by_category": by_category, "total": total}


@router.get("/log")
def access_log(request: Request):
    """Access log — who accessed what."""
    _get_user(request)

    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, project_slug, agent_name, category, items_accessed, accessed_at "
            "FROM public.dash_brain_access_log ORDER BY accessed_at DESC LIMIT 200"
        )).fetchall()

    logs = []
    for r in rows:
        logs.append({
            "id": r[0], "project_slug": r[1], "agent_name": r[2],
            "category": r[3], "items_accessed": r[4],
            "accessed_at": str(r[5]) if r[5] else None,
        })

    return {"logs": logs}


@router.get("/graph")
def brain_graph(request: Request):
    """Return entries formatted as knowledge graph nodes + edges."""
    _get_user(request)

    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, category, name, definition, metadata "
            "FROM public.dash_company_brain ORDER BY category, name"
        )).fetchall()

    nodes = []
    edges = []
    node_ids = set()

    for r in rows:
        meta = r[4] if isinstance(r[4], dict) else json.loads(r[4]) if r[4] else {}
        node_id = r[2].lower().replace(" ", "_")
        category = r[1]

        # Determine node type from category
        type_map = {
            "glossary": "metric", "formula": "formula", "alias": "entity",
            "pattern": "pattern", "org": "org", "threshold": "threshold", "calendar": "calendar",
        }
        node_type = type_map.get(category, "other")

        node = {"id": node_id, "name": r[2], "type": node_type, "category": category}
        if meta.get("formula"):
            node["formula"] = meta["formula"]
        nodes.append(node)
        node_ids.add(node_id)

        # Build edges from metadata
        if category == "formula":
            # Extract input metrics from formula text
            formula_text = meta.get("formula", r[3])
            for other in rows:
                other_id = other[2].lower().replace(" ", "_")
                if other_id != node_id and other[2].lower() in formula_text.lower():
                    edges.append({"source": other_id, "target": node_id, "relation": "input_to"})

        elif category == "alias":
            aliases = meta.get("aliases", [])
            for alias in aliases:
                alias_id = alias.lower().replace(" ", "_")
                if alias_id not in node_ids:
                    nodes.append({"id": alias_id, "name": alias, "type": "alias", "category": "alias"})
                    node_ids.add(alias_id)
                edges.append({"source": alias_id, "target": node_id, "relation": "alias_of"})

        elif category == "org":
            parent = meta.get("parent")
            if parent:
                parent_id = parent.lower().replace(" ", "_")
                edges.append({"source": node_id, "target": parent_id, "relation": "subsidiary_of"})
            for child in meta.get("children", []):
                child_id = child.lower().replace(" ", "_")
                edges.append({"source": child_id, "target": node_id, "relation": "part_of"})

        elif category == "threshold":
            # Link threshold to its metric if a matching glossary/formula entry exists
            for other in rows:
                if other[1] in ("glossary", "formula") and other[2].lower() == r[2].lower():
                    other_id = other[2].lower().replace(" ", "_")
                    edges.append({"source": node_id, "target": other_id, "relation": "threshold_for"})

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Project-scoped brain endpoints
# ---------------------------------------------------------------------------

@router.get("/projects/{slug}/brain")
def list_project_brain(request: Request, slug: str, category: str = ""):
    """List brain entries for a specific project + inherited global."""
    return list_entries(request, category=category, project_slug=slug)


@router.post("/projects/{slug}/brain")
def create_project_brain_entry(request: Request, slug: str, req: BrainEntryCreate):
    """Create a brain entry scoped to a project."""
    req.project_slug = slug
    req.user_id = None
    return create_entry(req, request)


# ---------------------------------------------------------------------------
# Personal brain endpoint
# ---------------------------------------------------------------------------

@router.post("/brain/personal")
def create_personal_brain_entry(request: Request, req: BrainEntryCreate):
    """Create a personal brain entry for Dash Agent (any authenticated user)."""
    user = _get_user(request)
    req.user_id = user.get("id")
    req.project_slug = None

    if req.category not in VALID_CATEGORIES:
        raise HTTPException(400, f"Invalid category. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}")

    safe, reason = _validate_no_data_leak(req.definition)
    if not safe:
        raise HTTPException(422, f"Data leak detected: {reason}. Brain stores abstract knowledge only.")

    with _engine.begin() as conn:
        row = conn.execute(text(
            "INSERT INTO public.dash_company_brain (category, name, definition, metadata, created_by, project_slug, user_id) "
            "VALUES (:cat, :name, :def, :meta, :by, :project_slug, :user_id) RETURNING id"
        ), {
            "cat": req.category, "name": req.name, "def": req.definition,
            "meta": json.dumps(req.metadata), "by": user.get("username", ""),
            "project_slug": None, "user_id": req.user_id,
        }).fetchone()
        try:
            from app.brain_versions import snapshot_version
            snapshot_version(
                conn,
                brain_id=int(row[0]),
                change_type="create",
                changed_by=user.get("user_id") or user.get("id"),
            )
        except Exception as e:
            logger.warning(f"Brain version snapshot (create personal) failed: {e}")

    return {"id": row[0], "status": "created"}


# ---------------------------------------------------------------------------
# Context builder — called by instructions.py
# ---------------------------------------------------------------------------

def get_brain_context(for_agent: str = "analyst", project_slug: str = "", user_id: int | None = None) -> str:
    """Build formatted brain context for injection into agent prompts.
    Also logs the access to dash_brain_access_log.

    Scoping:
    - If project_slug: return project-scoped + global entries (project wins on name conflict).
    - If user_id (Dash Agent): return personal + user's projects + global entries.
    - Otherwise: return all entries.
    """
    # Phase 3: source-scoped merge order: source > project > global.
    try:
        from dash.providers import get_registry
        _provider_source_ids = [
            getattr(p, "source_id", None)
            for p in get_registry().list_for_project(project_slug or "")
            if getattr(p, "source_id", None) not in (None, 0)
        ]
    except Exception:
        _provider_source_ids = []

    try:
        with _engine.connect() as conn:
            q = ("SELECT category, name, definition, metadata, project_slug, user_id, source_id "
                 "FROM public.dash_company_brain")
            params: dict = {}

            if project_slug:
                # Source-scoped row > project row > global row. ORDER ensures we
                # see source rows first so the dedupe-by-name keeps highest priority.
                q += (
                    " WHERE (source_id = ANY(:sids))"
                    " OR (project_slug = :slug)"
                    " OR (project_slug IS NULL AND source_id IS NULL)"
                )
                params["slug"] = project_slug
                params["sids"] = _provider_source_ids
            elif user_id:
                user_projects = _get_user_project_slugs(user_id)
                if user_projects:
                    placeholders = ", ".join(f":proj_{i}" for i in range(len(user_projects)))
                    for i, p in enumerate(user_projects):
                        params[f"proj_{i}"] = p
                    q += f" WHERE (user_id = :uid OR project_slug IN ({placeholders}) OR project_slug IS NULL)"
                else:
                    q += " WHERE (user_id = :uid OR project_slug IS NULL)"
                params["uid"] = user_id

            # Highest-priority rows first: source > project > global. Within a
            # tier, sort by category, name for stable output.
            if project_slug:
                q += (
                    " ORDER BY (source_id IS NOT NULL) DESC,"
                    " (project_slug IS NOT NULL) DESC, category, name"
                )
            else:
                q += " ORDER BY category, name"
            rows = conn.execute(text(q), params).fetchall()
    except Exception as e:
        logger.warning(f"Brain context load failed: {e}")
        return ""

    if not rows:
        return ""

    # Merge: dedupe by (category, name) — first occurrence wins. ORDER BY
    # already sorts highest priority (source > project > global) first.
    seen_names: dict[str, dict] = {}
    all_entries: list[dict] = []
    for r in rows:
        cat = r[0]
        name = r[1]
        meta = r[3] if isinstance(r[3], dict) else json.loads(r[3]) if r[3] else {}
        ps = r[4]
        uid_val = r[5]
        sid_val = r[6] if len(r) > 6 else None

        if sid_val is not None:
            scope = "source"
        elif ps is not None:
            scope = "project"
        elif uid_val is not None:
            scope = "personal"
        else:
            scope = "global"

        entry = {"name": name, "definition": r[2], "metadata": meta, "category": cat, "scope": scope}
        key = f"{cat}:{name.lower()}"

        if key in seen_names:
            # Higher-priority row already kept — skip lower-priority duplicate.
            continue
        seen_names[key] = {"scope": scope, "idx": len(all_entries)}
        all_entries.append(entry)

    # Group by category
    grouped: dict[str, list] = {}
    for e in all_entries:
        grouped.setdefault(e["category"], []).append(e)

    def _scope_tag(entry: dict) -> str:
        s = entry.get("scope", "global").upper()
        return f"[{s}]"

    parts = ["COMPANY KNOWLEDGE (Central Brain):"]

    # Glossary
    if "glossary" in grouped:
        parts.append("\nGLOSSARY:")
        for e in grouped["glossary"]:
            parts.append(f"  {_scope_tag(e)} {e['name']} = {e['definition']}")

    # Formulas
    if "formula" in grouped:
        parts.append("\nFORMULAS:")
        for e in grouped["formula"]:
            formula = e["metadata"].get("formula", e["definition"])
            parts.append(f"  {_scope_tag(e)} {e['name']} = {formula}")

    # Aliases
    if "alias" in grouped:
        parts.append("\nENTITY ALIASES:")
        for e in grouped["alias"]:
            aliases = e["metadata"].get("aliases", [])
            if aliases:
                parts.append(f'  {_scope_tag(e)} "{e["name"]}" = {", ".join(aliases)}')
            else:
                parts.append(f'  {_scope_tag(e)} "{e["name"]}" = {e["definition"]}')

    # Thresholds
    if "threshold" in grouped:
        parts.append("\nTHRESHOLDS:")
        for e in grouped["threshold"]:
            meta = e["metadata"]
            target = meta.get("target", "")
            alert_below = meta.get("alert_below")
            alert_above = meta.get("alert_above")
            line = f"  {_scope_tag(e)} {e['name']} target: {target}"
            if alert_below:
                line += f", alert if < {alert_below}"
            if alert_above:
                line += f", alert if > {alert_above}"
            parts.append(line)

    # Patterns / Rules
    if "pattern" in grouped:
        parts.append("\nRULES:")
        for e in grouped["pattern"]:
            parts.append(f"  {_scope_tag(e)} - {e['definition']}")

    # Calendar
    if "calendar" in grouped:
        parts.append("\nCALENDAR:")
        for e in grouped["calendar"]:
            parts.append(f"  {_scope_tag(e)} {e['name']}: {e['definition']}")

    # Industry benchmarks (web-synced via dash.learning.benchmark_sync).
    # Capped at 5 rows for context budget.
    if "benchmark" in grouped:
        parts.append("\nINDUSTRY BENCHMARK:")
        for e in grouped["benchmark"][:5]:
            meta = e.get("metadata") or {}
            ind = meta.get("industry") or ""
            pct = meta.get("percentile") or ""
            src = meta.get("source") or ""
            tag = _scope_tag(e)
            line = f"  {tag} {e['name']} = {e['definition']}"
            extras = []
            if ind:
                extras.append(f"industry={ind}")
            if pct:
                extras.append(f"percentile={pct}")
            if src:
                extras.append(f"source={src}")
            if extras:
                line += "  (" + ", ".join(extras) + ")"
            parts.append(line)

    # Org
    if "org" in grouped:
        parts.append("\nORG:")
        for e in grouped["org"]:
            meta = e["metadata"]
            children = meta.get("children", [])
            if children:
                parts.append(f"  {_scope_tag(e)} {e['name']} -> {' + '.join(children)}")
            else:
                parent = meta.get("parent", "")
                parts.append(f"  {_scope_tag(e)} {e['name']} (part of {parent})" if parent else f"  {_scope_tag(e)} {e['name']}")

    context = "\n".join(parts)

    # Log the access
    total_items = len(all_entries)
    try:
        with _engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_brain_access_log (project_slug, agent_name, category, items_accessed) "
                "VALUES (:slug, :agent, :cat, :items)"
            ), {"slug": project_slug, "agent": for_agent, "cat": "all", "items": total_items})
            conn.commit()
    except Exception as e:
        logger.warning(f"Brain access log failed: {e}")

    return context


# ---------------------------------------------------------------------------
# Init — called on app startup
# ---------------------------------------------------------------------------

def init_brain():
    """Initialize brain tables. Call during app lifespan."""
    try:
        _bootstrap_brain_tables()
    except Exception as e:
        logger.warning(f"Brain init skipped: {e}")
