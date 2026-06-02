"""MDL Editor Admin API.

Endpoints (prefix ``/api/mdl``) for browsing + editing installed MDL
semantic models, virtual columns, relationships, and metric definitions
per project.

Surfaces:
* ``GET    /installed?project_slug=...``       list installed packs + models
* ``GET    /model/{model_id}``                 single model w/ vcols + rels
* ``PATCH  /model/{model_id}``                 update model description / vcols / rels
* ``GET    /metric/{metric_id}``               full metric definition
* ``PATCH  /metric/{metric_id}``               update metric fields
* ``POST   /metric``                           create new metric definition
* ``DELETE /metric/{metric_id}``               soft-delete (status=deprecated) or hard delete
* ``GET    /packs/available``                  list pack names installable
* ``POST   /install``                          install pack into project

Read engine: ``get_sql_engine`` (public schema read-only listener safe for SELECT).
Write engine: ``get_write_engine`` (mandatory for ``public.dash_*`` writes per
``db.session`` guard — CLAUDE.md rule).

JSONB writes use ``CAST(:x AS jsonb)`` (NEVER ``:x::jsonb`` — PgBouncer + SQLAlchemy
named-param collision documented in CLAUDE.md).

Validation: best-effort sqlglot parse_one() on filter SQL when present.

Fail-soft on missing tables → 503 w/ explanatory detail.

Auth: matches ``actions_api.py`` pattern — Bearer token via
``request.state.user`` populated by ``AuthMiddleware``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mdl", tags=["mdl-editor"])


# ── Auth helpers (mirror actions_api.py) ──────────────────────────────────

def _get_user(request: Request) -> dict[str, Any]:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        try:
            from app.auth import get_current_user  # type: ignore
            user = get_current_user(request)
        except Exception:
            user = None
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _username(user: dict[str, Any]) -> str:
    return (
        user.get("username")
        or user.get("name")
        or user.get("email")
        or "admin"
    )


# ── Engine accessors ──────────────────────────────────────────────────────

def _read_engine():
    from db.session import get_sql_engine  # type: ignore
    return get_sql_engine()


def _write_engine():
    """Public-schema writes MUST use get_write_engine — get_sql_engine() has
    transaction_read_only=on listener; INSERT/UPDATE silently rolls back.
    Documented in CLAUDE.md (rule has bitten 4+ sessions)."""
    from db.session import get_write_engine  # type: ignore
    return get_write_engine()


# ── Pydantic models ───────────────────────────────────────────────────────

class VirtualColumn(BaseModel):
    name: str
    expression: str
    type: str = "string"
    bounds: Optional[dict[str, Any]] = None


class Relationship(BaseModel):
    model: str
    on: str
    type: str = "many_to_one"
    optional: bool = False


class ModelPatch(BaseModel):
    description: Optional[str] = None
    virtual_columns: Optional[list[VirtualColumn]] = None
    relationships: Optional[list[Relationship]] = None


class FilterSpec(BaseModel):
    col: str
    op: str
    value: Any = None
    trim: bool = True


class MetricCreate(BaseModel):
    project_slug: str
    name: str
    description: Optional[str] = None
    kind: str = "count"
    source_tables: list[str] = Field(default_factory=list)
    measure_col: Optional[str] = None
    filters: list[FilterSpec] = Field(default_factory=list)
    denom_filters: list[FilterSpec] = Field(default_factory=list)
    group_dims: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    status: str = "draft"


class MetricPatch(BaseModel):
    description: Optional[str] = None
    kind: Optional[str] = None
    source_tables: Optional[list[str]] = None
    measure_col: Optional[str] = None
    filters: Optional[list[FilterSpec]] = None
    denom_filters: Optional[list[FilterSpec]] = None
    group_dims: Optional[list[str]] = None
    synonyms: Optional[list[str]] = None
    status: Optional[str] = None


class InstallRequest(BaseModel):
    project_slug: str
    pack_name: str


# ── Validation helpers ────────────────────────────────────────────────────

def _validate_sql_expr(expr: str) -> tuple[bool, str]:
    """Best-effort sqlglot parse of a virtual-column or filter expression.

    Returns ``(ok, error_msg)``. Fail-soft: if sqlglot missing → ok=True
    (don't block save on infra problem).
    """
    if not expr or not expr.strip():
        return True, ""
    try:
        import sqlglot  # type: ignore
    except ImportError:
        logger.debug("sqlglot missing; skipping SQL validation")
        return True, ""
    try:
        # Wrap in SELECT so bare expressions parse
        sqlglot.parse_one(f"SELECT {expr}", dialect="postgres")
        return True, ""
    except Exception as e:
        msg = str(e).split("\n")[0][:200]
        return False, msg


# ── 1. List installed packs + models ──────────────────────────────────────

@router.get("/installed")
def list_installed(project_slug: str = Query(...)) -> dict[str, Any]:
    """Return all installed MDL packs + models for a project.

    Reads ``dash_metric_definitions`` rows where ``model_name IS NOT NULL``
    (set by ``install_mdl()``). Pack name parsed from name prefix ``mdl_<model>``
    + description suffix ``(pack: <pack_name>)``.
    """
    try:
        eng = _read_engine()
        with eng.connect() as cn:
            rows = cn.execute(text(
                "SELECT id, name, description, model_name, raw_table_ref, "
                "       virtual_columns, relationships, status, version, "
                "       updated_at "
                "  FROM public.dash_metric_definitions "
                " WHERE project_slug = :s "
                "   AND model_name IS NOT NULL "
                " ORDER BY model_name"
            ), {"s": project_slug}).mappings().fetchall()
    except ProgrammingError as e:
        raise HTTPException(503, f"dash_metric_definitions missing: {e}")
    except Exception as e:
        logger.warning("list_installed failed: %s", e)
        raise HTTPException(500, str(e))

    models = []
    packs_seen: dict[str, dict[str, Any]] = {}
    for r in rows:
        desc = r["description"] or ""
        pack_name = "unknown"
        if "pack:" in desc:
            try:
                pack_name = desc.split("pack:")[-1].split(")")[0].strip()
            except Exception:
                pass
        vcs = r["virtual_columns"] or []
        rels = r["relationships"] or []
        models.append({
            "id": r["id"],
            "name": r["name"],
            "model_name": r["model_name"],
            "raw_table_ref": r["raw_table_ref"],
            "description": desc,
            "pack_name": pack_name,
            "vcol_count": len(vcs) if isinstance(vcs, list) else 0,
            "rel_count": len(rels) if isinstance(rels, list) else 0,
            "status": r["status"],
            "version": r["version"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        })
        if pack_name not in packs_seen:
            packs_seen[pack_name] = {"name": pack_name, "models": 0}
        packs_seen[pack_name]["models"] += 1

    return {
        "project_slug": project_slug,
        "packs": list(packs_seen.values()),
        "models": models,
        "total_models": len(models),
    }


# ── 2. Full model detail ──────────────────────────────────────────────────

@router.get("/model/{model_id}")
def get_model(model_id: int) -> dict[str, Any]:
    """Return full MDL model row including raw vcols + rels JSONB."""
    try:
        eng = _read_engine()
        with eng.connect() as cn:
            row = cn.execute(text(
                "SELECT id, project_slug, name, description, kind, "
                "       model_name, raw_table_ref, virtual_columns, "
                "       relationships, status, version, "
                "       created_at, updated_at "
                "  FROM public.dash_metric_definitions "
                " WHERE id = :id"
            ), {"id": model_id}).mappings().fetchone()
    except ProgrammingError as e:
        raise HTTPException(503, f"dash_metric_definitions missing: {e}")
    except Exception as e:
        logger.warning("get_model failed: %s", e)
        raise HTTPException(500, str(e))

    if not row:
        raise HTTPException(404, f"model id={model_id} not found")

    # Fetch live column info from raw_table_ref if available
    columns: list[dict[str, Any]] = []
    raw_table = row["raw_table_ref"]
    project_slug = row["project_slug"]
    if raw_table and project_slug:
        try:
            with eng.connect() as cn:
                col_rows = cn.execute(text(
                    "SELECT column_name, data_type "
                    "  FROM information_schema.columns "
                    " WHERE table_schema = :s "
                    "   AND table_name = :t "
                    " ORDER BY ordinal_position"
                ), {"s": project_slug, "t": raw_table}).fetchall()
            columns = [{"name": c[0], "type": c[1]} for c in col_rows]
        except Exception as e:
            logger.debug("columns lookup failed: %s", e)

    return {
        "id": row["id"],
        "project_slug": row["project_slug"],
        "name": row["name"],
        "description": row["description"],
        "kind": row["kind"],
        "model_name": row["model_name"],
        "raw_table_ref": raw_table,
        "virtual_columns": row["virtual_columns"] or [],
        "relationships": row["relationships"] or [],
        "raw_columns": columns,
        "status": row["status"],
        "version": row["version"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


# ── 3. PATCH model (description / vcols / rels) ───────────────────────────

@router.patch("/model/{model_id}")
def patch_model(
    model_id: int,
    patch: ModelPatch = Body(...),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Update model description / virtual_columns / relationships.

    Validates virtual column expressions via sqlglot before save.
    Bumps version. Returns validation report per vcol.
    """
    user = _get_user(request) if request else {"username": "system"}
    actor = _username(user)

    # Validate vcol expressions if provided
    validation_report: list[dict[str, Any]] = []
    if patch.virtual_columns is not None:
        for vc in patch.virtual_columns:
            ok, err = _validate_sql_expr(vc.expression)
            validation_report.append({
                "name": vc.name,
                "expression": vc.expression,
                "valid": ok,
                "error": err if not ok else None,
            })
        # Reject if any invalid
        bad = [v for v in validation_report if not v["valid"]]
        if bad:
            return {
                "ok": False,
                "error": "virtual_column SQL validation failed",
                "validation": validation_report,
            }

    # Build dynamic SET clause
    sets: list[str] = []
    params: dict[str, Any] = {"id": model_id, "actor": actor}

    if patch.description is not None:
        sets.append("description = :description")
        params["description"] = patch.description
    if patch.virtual_columns is not None:
        sets.append("virtual_columns = CAST(:virtual_columns AS jsonb)")
        params["virtual_columns"] = json.dumps([vc.model_dump(exclude_none=True) for vc in patch.virtual_columns])
    if patch.relationships is not None:
        sets.append("relationships = CAST(:relationships AS jsonb)")
        params["relationships"] = json.dumps([r.model_dump() for r in patch.relationships])

    if not sets:
        raise HTTPException(400, "no fields to update")

    sets.append("updated_at = now()")
    sets.append("updated_by = :actor")
    sets.append("version = version + 1")

    sql = f"UPDATE public.dash_metric_definitions SET {', '.join(sets)} WHERE id = :id RETURNING id, version, project_slug"

    try:
        eng = _write_engine()
        with eng.begin() as cn:
            row = cn.execute(text(sql), params).fetchone()
    except Exception as e:
        logger.warning("patch_model failed: %s", e)
        raise HTTPException(500, str(e))

    if not row:
        raise HTTPException(404, f"model id={model_id} not found")

    # Invalidate MDL compiler cache so next compile sees fresh
    try:
        from dash.semantic import invalidate as _inv  # type: ignore
        _inv(row[2])  # project_slug
    except Exception:
        pass

    return {
        "ok": True,
        "id": row[0],
        "version": row[1],
        "validation": validation_report,
    }


# ── 4. Single metric definition ───────────────────────────────────────────

@router.get("/metric/{metric_id}")
def get_metric(metric_id: int) -> dict[str, Any]:
    """Return full metric definition row (all JSONB fields)."""
    try:
        eng = _read_engine()
        with eng.connect() as cn:
            row = cn.execute(text(
                "SELECT * FROM public.dash_metric_definitions WHERE id = :id"
            ), {"id": metric_id}).mappings().fetchone()
    except ProgrammingError as e:
        raise HTTPException(503, f"dash_metric_definitions missing: {e}")
    except Exception as e:
        raise HTTPException(500, str(e))

    if not row:
        raise HTTPException(404, f"metric id={metric_id} not found")

    out = dict(row)
    # Coerce datetime to ISO
    for k in ("created_at", "updated_at"):
        if out.get(k):
            out[k] = out[k].isoformat()
    return out


# ── 5. PATCH metric definition ────────────────────────────────────────────

@router.patch("/metric/{metric_id}")
def patch_metric(
    metric_id: int,
    patch: MetricPatch = Body(...),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Update editable metric fields. Bumps version."""
    user = _get_user(request) if request else {"username": "system"}
    actor = _username(user)

    validation_report: list[dict[str, Any]] = []

    sets: list[str] = []
    params: dict[str, Any] = {"id": metric_id, "actor": actor}

    if patch.description is not None:
        sets.append("description = :description")
        params["description"] = patch.description
    if patch.kind is not None:
        sets.append("kind = :kind")
        params["kind"] = patch.kind
    if patch.measure_col is not None:
        sets.append("measure_col = :measure_col")
        params["measure_col"] = patch.measure_col
    if patch.status is not None:
        sets.append("status = :status")
        params["status"] = patch.status
    if patch.source_tables is not None:
        sets.append("source_tables = CAST(:source_tables AS jsonb)")
        params["source_tables"] = json.dumps(patch.source_tables)
    if patch.group_dims is not None:
        sets.append("group_dims = CAST(:group_dims AS jsonb)")
        params["group_dims"] = json.dumps(patch.group_dims)
    if patch.synonyms is not None:
        sets.append("synonyms = CAST(:synonyms AS jsonb)")
        params["synonyms"] = json.dumps(patch.synonyms)
    if patch.filters is not None:
        # Validate each filter's value if it looks like SQL expression
        for f in patch.filters:
            if isinstance(f.value, str) and any(c in f.value for c in "()+-*/<>="):
                ok, err = _validate_sql_expr(f.value)
                validation_report.append({"col": f.col, "valid": ok, "error": err if not ok else None})
        sets.append("filters = CAST(:filters AS jsonb)")
        params["filters"] = json.dumps([f.model_dump() for f in patch.filters])
    if patch.denom_filters is not None:
        sets.append("denom_filters = CAST(:denom_filters AS jsonb)")
        params["denom_filters"] = json.dumps([f.model_dump() for f in patch.denom_filters])

    if not sets:
        raise HTTPException(400, "no fields to update")

    sets.append("updated_at = now()")
    sets.append("updated_by = :actor")
    sets.append("version = version + 1")

    sql = f"UPDATE public.dash_metric_definitions SET {', '.join(sets)} WHERE id = :id RETURNING id, version, project_slug"

    try:
        eng = _write_engine()
        with eng.begin() as cn:
            row = cn.execute(text(sql), params).fetchone()
    except Exception as e:
        logger.warning("patch_metric failed: %s", e)
        raise HTTPException(500, str(e))

    if not row:
        raise HTTPException(404, f"metric id={metric_id} not found")

    try:
        from dash.semantic import invalidate as _inv  # type: ignore
        _inv(row[2])
    except Exception:
        pass

    return {"ok": True, "id": row[0], "version": row[1], "validation": validation_report}


# ── 6. Create new metric definition ───────────────────────────────────────

@router.post("/metric")
def create_metric(
    spec: MetricCreate = Body(...),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Create new metric definition (Phase 0 — non-MDL, model_name NULL)."""
    user = _get_user(request) if request else {"username": "system"}
    actor = _username(user)

    sql = text(
        "INSERT INTO public.dash_metric_definitions "
        "  (project_slug, name, synonyms, description, kind, "
        "   source_tables, measure_col, filters, denom_filters, "
        "   group_dims, status, version, created_by, updated_by, "
        "   created_at, updated_at) "
        "VALUES (:slug, :name, CAST(:synonyms AS jsonb), :description, :kind, "
        "        CAST(:source_tables AS jsonb), :measure_col, "
        "        CAST(:filters AS jsonb), CAST(:denom_filters AS jsonb), "
        "        CAST(:group_dims AS jsonb), :status, 1, "
        "        :actor, :actor, now(), now()) "
        "ON CONFLICT (project_slug, name) DO NOTHING "
        "RETURNING id"
    )
    params = {
        "slug": spec.project_slug,
        "name": spec.name,
        "synonyms": json.dumps(spec.synonyms),
        "description": spec.description,
        "kind": spec.kind,
        "source_tables": json.dumps(spec.source_tables),
        "measure_col": spec.measure_col,
        "filters": json.dumps([f.model_dump() for f in spec.filters]),
        "denom_filters": json.dumps([f.model_dump() for f in spec.denom_filters]),
        "group_dims": json.dumps(spec.group_dims),
        "status": spec.status,
        "actor": actor,
    }

    try:
        eng = _write_engine()
        with eng.begin() as cn:
            row = cn.execute(sql, params).fetchone()
    except Exception as e:
        logger.warning("create_metric failed: %s", e)
        raise HTTPException(500, str(e))

    if not row:
        raise HTTPException(409, f"metric '{spec.name}' already exists in {spec.project_slug}")

    return {"ok": True, "id": row[0], "name": spec.name}


# ── 7. DELETE metric ──────────────────────────────────────────────────────

@router.delete("/metric/{metric_id}")
def delete_metric(metric_id: int, hard: bool = Query(False)) -> dict[str, Any]:
    """Soft-delete by default (status='deprecated'). hard=true → DELETE row."""
    try:
        eng = _write_engine()
        with eng.begin() as cn:
            if hard:
                row = cn.execute(text(
                    "DELETE FROM public.dash_metric_definitions WHERE id = :id RETURNING id, project_slug"
                ), {"id": metric_id}).fetchone()
            else:
                row = cn.execute(text(
                    "UPDATE public.dash_metric_definitions SET status='deprecated', updated_at=now() "
                    "WHERE id = :id RETURNING id, project_slug"
                ), {"id": metric_id}).fetchone()
    except Exception as e:
        raise HTTPException(500, str(e))

    if not row:
        raise HTTPException(404, f"metric id={metric_id} not found")

    try:
        from dash.semantic import invalidate as _inv  # type: ignore
        _inv(row[1])
    except Exception:
        pass

    return {"ok": True, "id": row[0], "mode": "hard" if hard else "soft"}


# ── 8. List available packs ───────────────────────────────────────────────

@router.get("/packs/available")
def packs_available(project_slug: Optional[str] = Query(None)) -> dict[str, Any]:
    """List installable MDL packs (from dash/workflows/verticals/__init__.py).

    When project_slug provided, marks each pack as installed/not-installed
    based on whether any model from that pack exists in dash_metric_definitions.
    """
    try:
        from dash.workflows.verticals import list_packs  # type: ignore
        all_packs = list_packs()
    except Exception as e:
        raise HTTPException(503, f"verticals registry unavailable: {e}")

    # Filter MDL-only packs (legacy packs install differently)
    mdl_packs = [p for p in all_packs if p.get("format") == "mdl"]

    installed_packs: set[str] = set()
    if project_slug:
        try:
            eng = _read_engine()
            with eng.connect() as cn:
                rows = cn.execute(text(
                    "SELECT DISTINCT description "
                    "  FROM public.dash_metric_definitions "
                    " WHERE project_slug = :s AND model_name IS NOT NULL"
                ), {"s": project_slug}).fetchall()
            for (desc,) in rows:
                if desc and "pack:" in desc:
                    pname = desc.split("pack:")[-1].split(")")[0].strip()
                    installed_packs.add(pname)
        except Exception as e:
            logger.debug("installed packs lookup failed: %s", e)

    for p in mdl_packs:
        p["installed"] = p["name"] in installed_packs

    return {"packs": mdl_packs, "total": len(mdl_packs)}


# ── 9. Install pack ───────────────────────────────────────────────────────

@router.post("/install")
def install_pack(
    req: InstallRequest = Body(...),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Install MDL pack into project. Wraps ``install_mdl(slug, pack_name, owner_id)``."""
    user = _get_user(request) if request else {"username": "system"}
    owner_user_id = user.get("id") if isinstance(user, dict) else None

    try:
        from dash.workflows.verticals import install_mdl  # type: ignore
    except Exception as e:
        raise HTTPException(503, f"install_mdl unavailable: {e}")

    try:
        result = install_mdl(req.project_slug, req.pack_name, owner_user_id=owner_user_id)
    except Exception as e:
        logger.warning("install_mdl failed: %s", e)
        raise HTTPException(500, str(e))

    return result
