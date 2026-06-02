"""Internal pack/extension registry API.

Promotes Phase 6 vertical packs (`dash/workflows/verticals/`) to first-class
DB-tracked objects so admins can browse manifests, install per-project, and
audit lineage.

Style mirrors `app/golden_api.py` — flat router, fail-soft, fcntl-free
(DB-backed), explicit CAST(:x AS jsonb) for PgBouncer + SQLAlchemy named-param
collision rule (see CLAUDE.md "Never-do list").

Migration: db/migrations/152_dash_packs.sql
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/packs", tags=["packs"])


def _write_engine():
    """Write-capable engine for `dash.dash_packs*` writes (public + dash schemas).

    CLAUDE.md rule: `public.dash_*` writes ALWAYS need get_write_engine().
    """
    from db.session import get_write_engine
    return get_write_engine()


def _read_engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _row_to_pack(row) -> dict[str, Any]:
    """Coerce DB row → public JSON shape."""
    m = row.manifest if isinstance(row.manifest, dict) else {}
    return {
        "id": str(row.id),
        "name": row.name,
        "version": row.version,
        "author": row.author,
        "source_path": row.source_path,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "description": m.get("description", ""),
        "vertical": m.get("vertical"),
        "skills": m.get("skills") or [],
        "golden_qa": m.get("golden_qa") or [],
        "mdl_fragments": m.get("mdl_fragments") or [],
        "workflow_count": m.get("workflow_count", 0),
        "model_count": m.get("model_count", 0),
        "format": m.get("format", "legacy"),
    }


# ---------- list / detail ----------


@router.get("")
def list_packs() -> dict[str, Any]:
    """List all registered packs (newest first)."""
    try:
        eng = _read_engine()
        with eng.connect() as cn:
            rows = cn.execute(text(
                "SELECT id, name, version, manifest, author, source_path, created_at "
                "  FROM dash.dash_packs ORDER BY created_at DESC"
            )).fetchall()
        packs = [_row_to_pack(r) for r in rows]
        return {"count": len(packs), "packs": packs}
    except Exception as e:
        logger.exception(f"list_packs failed: {e}")
        raise HTTPException(503, f"pack registry unavailable: {e}")


@router.get("/installed")
def list_installed(project_slug: str) -> dict[str, Any]:
    """List packs installed for a project (enabled and disabled).

    Declared BEFORE `/{pack_id}` so FastAPI matches the literal path first.
    """
    slug = (project_slug or "").strip()
    if not slug:
        raise HTTPException(400, "project_slug required")
    try:
        eng = _read_engine()
        with eng.connect() as cn:
            rows = cn.execute(text(
                "SELECT p.id, p.name, p.version, p.manifest, p.author, "
                "       p.source_path, p.created_at, "
                "       i.enabled, i.installed_at "
                "  FROM dash.dash_pack_installs i "
                "  JOIN dash.dash_packs p ON p.id = i.pack_id "
                " WHERE i.project_slug = :s "
                " ORDER BY i.installed_at DESC"
            ), {"s": slug}).fetchall()
    except Exception as e:
        logger.exception(f"list_installed failed: {e}")
        raise HTTPException(503, f"installed lookup failed: {e}")

    out = []
    for r in rows:
        pack = _row_to_pack(r)
        pack["enabled"] = bool(r.enabled)
        pack["installed_at"] = r.installed_at.isoformat() if r.installed_at else None
        out.append(pack)
    return {"project_slug": slug, "count": len(out), "installed": out}


@router.get("/{pack_id}")
def get_pack(pack_id: str) -> dict[str, Any]:
    """Detail incl. full manifest."""
    try:
        eng = _read_engine()
        with eng.connect() as cn:
            row = cn.execute(text(
                "SELECT id, name, version, manifest, author, source_path, created_at "
                "  FROM dash.dash_packs WHERE id = CAST(:i AS uuid)"
            ), {"i": pack_id}).fetchone()
    except Exception as e:
        logger.exception(f"get_pack failed: {e}")
        raise HTTPException(503, f"pack lookup failed: {e}")
    if not row:
        raise HTTPException(404, f"pack not found: {pack_id}")
    out = _row_to_pack(row)
    out["manifest"] = row.manifest if isinstance(row.manifest, dict) else {}
    return out


# ---------- sync (scan disk → upsert) ----------


@router.post("/sync")
def sync_packs() -> dict[str, Any]:
    """Scan `dash/workflows/verticals/` modules and upsert each MANIFEST.

    Falls back to an inferred manifest when a module doesn't define one.
    Idempotent on `name` UNIQUE.
    """
    import json
    try:
        from dash.workflows.verticals import iter_pack_modules
    except Exception as e:
        raise HTTPException(503, f"verticals module unavailable: {e}")

    eng = _write_engine()
    upserted: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        with eng.begin() as cn:
            for _mod, manifest, src in iter_pack_modules():
                name = manifest.get("name")
                if not name:
                    errors.append("module missing name; skipped")
                    continue
                try:
                    res = cn.execute(text(
                        "INSERT INTO dash.dash_packs "
                        "  (name, version, manifest, author, source_path) "
                        "VALUES (:n, :v, CAST(:m AS jsonb), :a, :s) "
                        "ON CONFLICT (name) DO UPDATE SET "
                        "  version = EXCLUDED.version, "
                        "  manifest = EXCLUDED.manifest, "
                        "  author = EXCLUDED.author, "
                        "  source_path = EXCLUDED.source_path "
                        "RETURNING id"
                    ), {
                        "n": name,
                        "v": manifest.get("version") or "1.0.0",
                        "m": json.dumps(manifest),
                        "a": manifest.get("author") or "internal",
                        "s": src,
                    })
                    rid = res.scalar()
                    upserted.append({"id": str(rid), "name": name,
                                     "version": manifest.get("version")})
                except Exception as e:
                    logger.exception(f"sync upsert failed for {name}: {e}")
                    errors.append(f"{name}: {e}")
    except Exception as e:
        logger.exception(f"sync_packs txn failed: {e}")
        raise HTTPException(500, f"sync failed: {e}")

    return {"ok": True, "synced": len(upserted), "packs": upserted, "errors": errors}


# ---------- install / uninstall ----------


@router.post("/{pack_id}/install")
def install_pack(pack_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Install pack into a project. Body: {project_slug}.

    Upserts `dash.dash_pack_installs (pack_id, project_slug)` w/ enabled=true,
    and (best-effort) inserts skill rows into `dash.dash_skills` if pack
    declares any. Tolerates missing dash_skills table.
    """
    import json
    slug = (body.get("project_slug") or "").strip()
    if not slug:
        raise HTTPException(400, "project_slug required")

    # Resolve pack
    eng_r = _read_engine()
    with eng_r.connect() as cn:
        row = cn.execute(text(
            "SELECT id, name, manifest FROM dash.dash_packs "
            "WHERE id = CAST(:i AS uuid)"
        ), {"i": pack_id}).fetchone()
    if not row:
        raise HTTPException(404, f"pack not found: {pack_id}")

    manifest = row.manifest if isinstance(row.manifest, dict) else {}
    skills = manifest.get("skills") or []

    eng_w = _write_engine()
    skills_written = 0
    skills_warn: str | None = None

    try:
        with eng_w.begin() as cn:
            cn.execute(text(
                "INSERT INTO dash.dash_pack_installs "
                "  (pack_id, project_slug, enabled) "
                "VALUES (CAST(:p AS uuid), :s, TRUE) "
                "ON CONFLICT (pack_id, project_slug) DO UPDATE SET "
                "  enabled = TRUE, installed_at = now()"
            ), {"p": pack_id, "s": slug})

            # Best-effort skill registration. Schema is unknown — try a
            # minimal common shape, swallow on failure.
            if skills:
                try:
                    for skill_name in skills:
                        if not skill_name:
                            continue
                        cn.execute(text(
                            "INSERT INTO dash.dash_skills "
                            "  (project_slug, name, source, metadata) "
                            "VALUES (:s, :n, :src, CAST(:m AS jsonb)) "
                            "ON CONFLICT DO NOTHING"
                        ), {
                            "s": slug,
                            "n": str(skill_name)[:200],
                            "src": f"pack:{row.name}",
                            "m": json.dumps({"pack_id": str(row.id),
                                             "pack_name": row.name}),
                        })
                        skills_written += 1
                except Exception as se:
                    # dash_skills shape may differ or table absent — tolerate.
                    skills_warn = f"dash_skills write skipped: {se}"
                    logger.info(skills_warn)
    except Exception as e:
        logger.exception(f"install_pack failed for {pack_id}/{slug}: {e}")
        raise HTTPException(500, f"install failed: {e}")

    return {
        "ok": True,
        "pack_id": pack_id,
        "pack_name": row.name,
        "project_slug": slug,
        "skills_registered": skills_written,
        "skills_warn": skills_warn,
    }


@router.post("/{pack_id}/uninstall")
def uninstall_pack(pack_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Soft-disable: set enabled=false (preserves install audit row)."""
    slug = (body.get("project_slug") or "").strip()
    if not slug:
        raise HTTPException(400, "project_slug required")
    try:
        eng = _write_engine()
        with eng.begin() as cn:
            res = cn.execute(text(
                "UPDATE dash.dash_pack_installs "
                "   SET enabled = FALSE "
                " WHERE pack_id = CAST(:p AS uuid) AND project_slug = :s"
            ), {"p": pack_id, "s": slug})
            affected = res.rowcount or 0
    except Exception as e:
        logger.exception(f"uninstall_pack failed: {e}")
        raise HTTPException(500, f"uninstall failed: {e}")
    return {"ok": True, "pack_id": pack_id, "project_slug": slug,
            "disabled": affected}


