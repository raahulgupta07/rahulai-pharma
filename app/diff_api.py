"""Diff API — version diffs for skills / metrics / models (MDL) + recent audit feed.

Endpoints (prefix /api/diff):
    GET /api/diff/entity?type=skill|metric|model&id=N
    GET /api/diff/compare?type=...&id=N&from_version=A&to_version=B
    GET /api/diff/recent?project_slug=...&limit=20

Diff strategy:
  - JSON fields → recursive field-level diff (same/added/removed/changed)
  - SQL/text fields → difflib.unified_diff (whitespace-aware)

History sources (per entity type):
  - skill   → dash.dash_skills.updated_at + dash.dash_skill_audit_log + dash_traces (kind='task' name LIKE 'skill.%')
  - metric  → public.dash_metric_versions (snapshot JSONB, append-only)
  - model   → public.dash_brain_versions (snapshot per row, includes change_type)

Fail-soft:
  - If a history table is missing → 503 w/ message (NOT 500).
  - If entity has only one "current" row + no history → return single-version response.
"""
from __future__ import annotations

import difflib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, OperationalError

logger = logging.getLogger("dash.diff_api")

router = APIRouter(prefix="/api/diff", tags=["diff"])


# ──────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────
def _engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception as e:  # noqa: BLE001
        logger.exception("diff_api: cannot acquire engine: %s", e)
        raise HTTPException(status_code=503, detail=f"db_unavailable: {e}")


def _table_exists(conn, schema: str, table: str) -> bool:
    try:
        row = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=:s AND table_name=:t LIMIT 1"
            ),
            {"s": schema, "t": table},
        ).first()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


# ──────────────────────────────────────────────────────────────────────────
# Diff primitives
# ──────────────────────────────────────────────────────────────────────────
def _is_sql_field(name: str) -> bool:
    n = (name or "").lower()
    return "sql" in n or n.endswith("_template") or n == "query_template"


def sql_unified_diff(a: str, b: str, left_label: str = "before", right_label: str = "after") -> str:
    """Whitespace-aware unified diff for SQL/text fields."""
    a_lines = (a or "").splitlines(keepends=False)
    b_lines = (b or "").splitlines(keepends=False)
    return "\n".join(
        difflib.unified_diff(a_lines, b_lines, fromfile=left_label, tofile=right_label, lineterm="")
    )


def json_field_diff(
    left: Optional[Dict[str, Any]],
    right: Optional[Dict[str, Any]],
    sql_fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Field-level diff between two dicts. Returns list of:
        {field, status: same|added|removed|changed, left, right, sql_diff?}
    SQL fields get unified_diff inline.
    """
    sql_fields = set(sql_fields or [])
    left = left or {}
    right = right or {}
    out: List[Dict[str, Any]] = []
    all_keys = sorted(set(left.keys()) | set(right.keys()))
    for k in all_keys:
        in_l = k in left
        in_r = k in right
        lv = left.get(k)
        rv = right.get(k)
        is_sql = (k in sql_fields) or _is_sql_field(k)
        if in_l and not in_r:
            out.append({"field": k, "status": "removed", "left": lv, "right": None})
        elif in_r and not in_l:
            out.append({"field": k, "status": "added", "left": None, "right": rv})
        else:
            # both present
            if lv == rv:
                out.append({"field": k, "status": "same", "left": lv, "right": rv})
            else:
                entry: Dict[str, Any] = {"field": k, "status": "changed", "left": lv, "right": rv}
                if is_sql and isinstance(lv, str) and isinstance(rv, str):
                    entry["sql_diff"] = sql_unified_diff(lv, rv, "before", "after")
                out.append(entry)
    return out


# ──────────────────────────────────────────────────────────────────────────
# Per-entity loaders
# ──────────────────────────────────────────────────────────────────────────
def _load_skill_versions(conn, skill_id: str) -> List[Dict[str, Any]]:
    """Skills have no formal history table. Strategy:
       1. Current row from dash.dash_skills.
       2. Patches from public.dash_tool_patches WHERE tool=skill_id (if exists).
       3. Audit invocations from dash.dash_skill_audit_log (read-only snapshots).
    Returns versions newest-first with version=N..1.
    """
    versions: List[Dict[str, Any]] = []

    # Current row
    has_skills = _table_exists(conn, "dash", "dash_skills")
    if not has_skills:
        raise HTTPException(status_code=503, detail="dash_skills_table_missing")

    current = conn.execute(
        text("SELECT id, name, description, instructions, project_slug, "
             "trigger_keywords, tools, runtime_role, updated_at, created_at, is_builtin "
             "FROM dash.dash_skills WHERE id = :id"),
        {"id": skill_id},
    ).mappings().first()
    if not current:
        raise HTTPException(status_code=404, detail=f"skill_not_found:{skill_id}")

    cur_snap = dict(current)
    if cur_snap.get("updated_at"):
        cur_snap["updated_at"] = cur_snap["updated_at"].isoformat()
    if cur_snap.get("created_at"):
        cur_snap["created_at"] = cur_snap["created_at"].isoformat()
    versions.append({
        "version": "current",
        "ts": cur_snap.get("updated_at"),
        "label": "Current",
        "source": "dash_skills",
        "snapshot": cur_snap,
    })

    # Patches (most recent first), if patches table exists
    if _table_exists(conn, "public", "dash_tool_patches"):
        try:
            rows = conn.execute(
                text(
                    "SELECT id, tool, new_description, default_args, applied_at, status, reason "
                    "FROM public.dash_tool_patches WHERE tool = :t "
                    "ORDER BY COALESCE(applied_at, created_at) DESC LIMIT 50"
                ),
                {"t": skill_id},
            ).mappings().all()
            for i, r in enumerate(rows, start=1):
                snap = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}
                versions.append({
                    "version": f"patch_{r['id']}",
                    "ts": snap.get("applied_at"),
                    "label": f"Patch #{r['id']} ({r.get('status') or 'unknown'})",
                    "source": "dash_tool_patches",
                    "snapshot": snap,
                })
        except Exception as e:  # noqa: BLE001
            logger.debug("skill patches load failed: %s", e)

    return versions


def _load_metric_versions(conn, metric_id: int) -> List[Dict[str, Any]]:
    """Metric versioning lives in public.dash_metric_versions (append-only snapshots)."""
    if not _table_exists(conn, "public", "dash_metric_versions"):
        raise HTTPException(status_code=503, detail="dash_metric_versions_table_missing")

    # Pull current from dash_metric_definitions
    versions: List[Dict[str, Any]] = []
    cur = None
    if _table_exists(conn, "public", "dash_metric_definitions"):
        cur = conn.execute(
            text("SELECT * FROM public.dash_metric_definitions WHERE id = :id"),
            {"id": metric_id},
        ).mappings().first()
    if cur:
        snap = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(cur).items()}
        versions.append({
            "version": f"v{cur.get('version', 1)}",
            "ts": snap.get("updated_at"),
            "label": f"Current (v{cur.get('version', 1)})",
            "source": "dash_metric_definitions",
            "snapshot": snap,
        })

    rows = conn.execute(
        text(
            "SELECT id, metric_id, project_slug, name, snapshot, change_type, "
            "changed_by, change_reason, created_at "
            "FROM public.dash_metric_versions WHERE metric_id = :mid "
            "ORDER BY created_at DESC LIMIT 100"
        ),
        {"mid": metric_id},
    ).mappings().all()
    for r in rows:
        snap = r.get("snapshot") or {}
        if isinstance(snap, str):
            try:
                snap = json.loads(snap)
            except Exception:  # noqa: BLE001
                snap = {"_raw": snap}
        ts = r.get("created_at")
        versions.append({
            "version": f"hist_{r['id']}",
            "ts": ts.isoformat() if ts else None,
            "label": f"{r.get('change_type') or 'change'} @ {ts.isoformat() if ts else '?'}",
            "source": "dash_metric_versions",
            "change_type": r.get("change_type"),
            "changed_by": r.get("changed_by"),
            "change_reason": r.get("change_reason"),
            "snapshot": snap,
        })

    if not versions:
        raise HTTPException(status_code=404, detail=f"metric_not_found:{metric_id}")
    return versions


def _load_model_versions(conn, brain_id: int) -> List[Dict[str, Any]]:
    """Model/MDL versioning piggybacks on public.dash_brain_versions for entries
    that represent semantic models (brain_id refers to a dash_company_brain row).
    """
    if not _table_exists(conn, "public", "dash_brain_versions"):
        raise HTTPException(status_code=503, detail="dash_brain_versions_table_missing")

    rows = conn.execute(
        text(
            "SELECT id, brain_id, version, category, name, definition, "
            "project_slug, user_id, metadata, change_type, changed_by, "
            "change_reason, created_at "
            "FROM public.dash_brain_versions WHERE brain_id = :bid "
            "ORDER BY version DESC LIMIT 100"
        ),
        {"bid": brain_id},
    ).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"model_not_found:{brain_id}")

    versions: List[Dict[str, Any]] = []
    for r in rows:
        snap = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}
        versions.append({
            "version": f"v{r['version']}",
            "ts": snap.get("created_at"),
            "label": f"v{r['version']} ({r.get('change_type') or 'change'})",
            "source": "dash_brain_versions",
            "change_type": r.get("change_type"),
            "changed_by": r.get("changed_by"),
            "change_reason": r.get("change_reason"),
            "snapshot": snap,
        })
    return versions


# ──────────────────────────────────────────────────────────────────────────
# Endpoint 1: list all versions for an entity
# ──────────────────────────────────────────────────────────────────────────
class EntityVersionsResponse(BaseModel):
    type: str
    id: Any
    versions: List[Dict[str, Any]]
    source: str
    note: Optional[str] = None


@router.get("/entity", response_model=EntityVersionsResponse)
def get_entity_versions(
    type: str = Query(..., description="skill | metric | model"),
    id: str = Query(..., description="Entity id (TEXT for skill, INT for metric/model)"),
):
    """Return all versions of an entity newest-first."""
    type_lc = (type or "").strip().lower()
    if type_lc not in {"skill", "metric", "model"}:
        raise HTTPException(status_code=400, detail="type must be one of: skill|metric|model")

    eng = _engine()
    try:
        with eng.connect() as conn:
            if type_lc == "skill":
                vs = _load_skill_versions(conn, id)
                src = "dash_skills + dash_tool_patches"
            elif type_lc == "metric":
                try:
                    mid = int(id)
                except ValueError:
                    raise HTTPException(status_code=400, detail="metric id must be integer")
                vs = _load_metric_versions(conn, mid)
                src = "dash_metric_versions"
            else:  # model
                try:
                    bid = int(id)
                except ValueError:
                    raise HTTPException(status_code=400, detail="model id must be integer")
                vs = _load_model_versions(conn, bid)
                src = "dash_brain_versions"
    except HTTPException:
        raise
    except (ProgrammingError, OperationalError) as e:
        logger.exception("diff_api entity DB error: %s", e)
        raise HTTPException(status_code=503, detail=f"history_unavailable: {e}")
    except Exception as e:  # noqa: BLE001
        logger.exception("diff_api entity failed: %s", e)
        raise HTTPException(status_code=500, detail=f"diff_api_error: {e}")

    note = None
    if len(vs) <= 1:
        note = "only current state available; no historical versions found"
    return EntityVersionsResponse(type=type_lc, id=id, versions=vs, source=src, note=note)


# ──────────────────────────────────────────────────────────────────────────
# Endpoint 2: compare two versions
# ──────────────────────────────────────────────────────────────────────────
class CompareResponse(BaseModel):
    type: str
    id: Any
    from_version: str
    to_version: str
    from_ts: Optional[str] = None
    to_ts: Optional[str] = None
    diff: List[Dict[str, Any]]
    summary: Dict[str, int]


def _resolve_version(versions: List[Dict[str, Any]], wanted: str) -> Optional[Dict[str, Any]]:
    if not wanted:
        return None
    w = wanted.strip()
    for v in versions:
        if str(v.get("version")) == w:
            return v
    return None


@router.get("/compare", response_model=CompareResponse)
def compare_versions(
    type: str = Query(...),
    id: str = Query(...),
    from_version: str = Query(..., description="Version id from /entity response"),
    to_version: str = Query(..., description="Version id from /entity response"),
):
    """Field-level diff between two versions. SQL fields get unified_diff."""
    # Reuse loader
    resp = get_entity_versions(type=type, id=id)
    versions = resp.versions

    left = _resolve_version(versions, from_version)
    right = _resolve_version(versions, to_version)
    if left is None or right is None:
        raise HTTPException(
            status_code=404,
            detail=f"version_not_found: from={from_version} to={to_version}",
        )

    left_snap = left.get("snapshot") or {}
    right_snap = right.get("snapshot") or {}

    diff = json_field_diff(left_snap, right_snap)
    summary = {"same": 0, "added": 0, "removed": 0, "changed": 0}
    for d in diff:
        summary[d["status"]] = summary.get(d["status"], 0) + 1

    return CompareResponse(
        type=resp.type,
        id=id,
        from_version=from_version,
        to_version=to_version,
        from_ts=left.get("ts"),
        to_ts=right.get("ts"),
        diff=diff,
        summary=summary,
    )


# ──────────────────────────────────────────────────────────────────────────
# Endpoint 3: recent changes across entity types (audit feed)
# ──────────────────────────────────────────────────────────────────────────
class RecentChange(BaseModel):
    type: str
    id: Any
    name: Optional[str] = None
    ts: str
    source: str
    label: Optional[str] = None
    change_type: Optional[str] = None
    project_slug: Optional[str] = None


@router.get("/recent")
def recent_changes(
    project_slug: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
) -> Dict[str, Any]:
    """Recent changes across skills (updated_at), metrics (dash_metric_versions),
    models (dash_brain_versions). Sorted newest first.
    """
    eng = _engine()
    events: List[Dict[str, Any]] = []
    sources_used: List[str] = []
    sources_missing: List[str] = []

    try:
        with eng.connect() as conn:
            # Skills
            if _table_exists(conn, "dash", "dash_skills"):
                sources_used.append("dash_skills")
                params: Dict[str, Any] = {"lim": limit}
                where = ""
                if project_slug:
                    where = "WHERE (project_slug = :slug OR project_slug IS NULL)"
                    params["slug"] = project_slug
                try:
                    rows = conn.execute(
                        text(
                            f"SELECT id, name, project_slug, updated_at "
                            f"FROM dash.dash_skills {where} "
                            f"ORDER BY updated_at DESC NULLS LAST LIMIT :lim"
                        ),
                        params,
                    ).mappings().all()
                    for r in rows:
                        ts = r.get("updated_at")
                        if not ts:
                            continue
                        events.append({
                            "type": "skill",
                            "id": r["id"],
                            "name": r.get("name"),
                            "ts": ts.isoformat(),
                            "source": "dash_skills",
                            "label": f"updated: {r.get('name')}",
                            "change_type": "update",
                            "project_slug": r.get("project_slug"),
                        })
                except Exception as e:  # noqa: BLE001
                    logger.debug("recent skills failed: %s", e)
            else:
                sources_missing.append("dash_skills")

            # Metric versions
            if _table_exists(conn, "public", "dash_metric_versions"):
                sources_used.append("dash_metric_versions")
                params = {"lim": limit}
                where = ""
                if project_slug:
                    where = "WHERE project_slug = :slug"
                    params["slug"] = project_slug
                try:
                    rows = conn.execute(
                        text(
                            f"SELECT id, metric_id, project_slug, name, change_type, created_at "
                            f"FROM public.dash_metric_versions {where} "
                            f"ORDER BY created_at DESC LIMIT :lim"
                        ),
                        params,
                    ).mappings().all()
                    for r in rows:
                        ts = r.get("created_at")
                        if not ts:
                            continue
                        events.append({
                            "type": "metric",
                            "id": r["metric_id"],
                            "name": r.get("name"),
                            "ts": ts.isoformat(),
                            "source": "dash_metric_versions",
                            "label": f"{r.get('change_type') or 'change'}: {r.get('name')}",
                            "change_type": r.get("change_type"),
                            "project_slug": r.get("project_slug"),
                        })
                except Exception as e:  # noqa: BLE001
                    logger.debug("recent metric_versions failed: %s", e)
            else:
                sources_missing.append("dash_metric_versions")

            # Brain (model) versions
            if _table_exists(conn, "public", "dash_brain_versions"):
                sources_used.append("dash_brain_versions")
                params = {"lim": limit}
                where = ""
                if project_slug:
                    where = "WHERE project_slug = :slug"
                    params["slug"] = project_slug
                try:
                    rows = conn.execute(
                        text(
                            f"SELECT id, brain_id, version, name, project_slug, "
                            f"change_type, created_at "
                            f"FROM public.dash_brain_versions {where} "
                            f"ORDER BY created_at DESC LIMIT :lim"
                        ),
                        params,
                    ).mappings().all()
                    for r in rows:
                        ts = r.get("created_at")
                        if not ts:
                            continue
                        events.append({
                            "type": "model",
                            "id": r["brain_id"],
                            "name": r.get("name"),
                            "ts": ts.isoformat(),
                            "source": "dash_brain_versions",
                            "label": f"{r.get('change_type') or 'change'} v{r.get('version')}: {r.get('name')}",
                            "change_type": r.get("change_type"),
                            "project_slug": r.get("project_slug"),
                        })
                except Exception as e:  # noqa: BLE001
                    logger.debug("recent brain_versions failed: %s", e)
            else:
                sources_missing.append("dash_brain_versions")

    except Exception as e:  # noqa: BLE001
        logger.exception("recent_changes failed: %s", e)
        raise HTTPException(status_code=503, detail=f"recent_unavailable: {e}")

    # Sort newest first, trim to limit
    events.sort(key=lambda x: x.get("ts") or "", reverse=True)
    events = events[:limit]

    return {
        "events": events,
        "count": len(events),
        "sources_used": sources_used,
        "sources_missing": sources_missing,
        "note": (
            "missing history tables → 503 will fire on /entity for those types"
            if sources_missing else None
        ),
    }


# ──────────────────────────────────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────────────────────────────────
@router.get("/_health")
def diff_health() -> Dict[str, Any]:
    eng = _engine()
    state: Dict[str, bool] = {}
    try:
        with eng.connect() as conn:
            state["dash.dash_skills"] = _table_exists(conn, "dash", "dash_skills")
            state["public.dash_tool_patches"] = _table_exists(conn, "public", "dash_tool_patches")
            state["public.dash_metric_definitions"] = _table_exists(conn, "public", "dash_metric_definitions")
            state["public.dash_metric_versions"] = _table_exists(conn, "public", "dash_metric_versions")
            state["public.dash_brain_versions"] = _table_exists(conn, "public", "dash_brain_versions")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "tables": state}
