"""Branding API — serves company info + logo + theme based on active tenant.

Tenants are folders under BRANDING_ROOT (default "branding/"). Each contains:
  - company.json (name, theme, etc.)
  - logo.svg
  - favicon.ico
  - theme.css

Active tenant is persisted in `dash_company_brain` (category='_system',
name='active_branding_tenant') so that the choice survives restarts.

Super-admin only for /tenants, /active (GET/POST). The base GET /api/branding
and asset endpoints stay public (consumed by the layout on boot).
"""
from __future__ import annotations
import io
import json
import logging
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/branding", tags=["branding"])

# Root that contains tenant folders. Default points at "branding/" so the
# legacy single-tenant `branding/default` still works.
BRANDING_ROOT = Path(os.environ.get("BRANDING_ROOT", "branding"))
# Legacy var: a direct path to a tenant folder. If set we still honour it.
LEGACY_BRANDING_DIR = os.environ.get("BRANDING_DIR")
DEFAULT_TENANT = os.environ.get("BRANDING_DEFAULT_TENANT", "default")

_engine = _sa_create_engine(db_url, poolclass=NullPool)

_SETTING_CATEGORY = "_system"
_SETTING_NAME = "active_branding_tenant"


# ---------------------------------------------------------------------------
# Auth helper (mirrors brain._require_super_admin to avoid circular import)
# ---------------------------------------------------------------------------

def _require_super_admin(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        # /api/branding is in AuthMiddleware SKIP_PREFIXES so request.state.user
        # is not populated. Manually resolve the token.
        try:
            from app.auth import get_current_user
            user = get_current_user(request)
        except Exception:
            user = None
    if not user:
        raise HTTPException(401, "Not authenticated")
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Super admin only")
    return user


# ---------------------------------------------------------------------------
# Active tenant persistence
# ---------------------------------------------------------------------------

def _read_active_tenant() -> str:
    """Return the active tenant slug. Falls back to legacy env or default."""
    try:
        with _engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT definition FROM public.dash_company_brain "
                    "WHERE category = :c AND name = :n LIMIT 1"
                ),
                {"c": _SETTING_CATEGORY, "n": _SETTING_NAME},
            ).fetchone()
        if row and row[0]:
            return str(row[0]).strip()
    except Exception as e:  # table may not exist on first boot
        logger.debug(f"active tenant read failed: {e}")
    if LEGACY_BRANDING_DIR:
        # Map legacy path → folder name
        return Path(LEGACY_BRANDING_DIR).name or DEFAULT_TENANT
    return DEFAULT_TENANT


def _write_active_tenant(tenant: str) -> None:
    """Upsert active tenant marker into dash_company_brain."""
    with _engine.begin() as conn:
        # ensure table exists (brain bootstrap normally handles this)
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS public.dash_company_brain ("
                " id SERIAL PRIMARY KEY, category TEXT, name TEXT, "
                " definition TEXT, metadata JSONB DEFAULT '{}'::jsonb, "
                " created_by TEXT, created_at TIMESTAMP DEFAULT NOW(), "
                " updated_at TIMESTAMP DEFAULT NOW(), "
                " project_slug TEXT DEFAULT NULL, user_id INTEGER DEFAULT NULL)"
            )
        )
        existing = conn.execute(
            text(
                "SELECT id FROM public.dash_company_brain "
                "WHERE category = :c AND name = :n LIMIT 1"
            ),
            {"c": _SETTING_CATEGORY, "n": _SETTING_NAME},
        ).fetchone()
        if existing:
            conn.execute(
                text(
                    "UPDATE public.dash_company_brain "
                    "SET definition = :d, updated_at = NOW() WHERE id = :id"
                ),
                {"d": tenant, "id": existing[0]},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO public.dash_company_brain "
                    "(category, name, definition, created_by) "
                    "VALUES (:c, :n, :d, 'branding')"
                ),
                {"c": _SETTING_CATEGORY, "n": _SETTING_NAME, "d": tenant},
            )


def _tenant_dir(tenant: Optional[str] = None) -> Path:
    """Return path to a tenant folder, falling back through legacy env."""
    tenant = (tenant or _read_active_tenant() or DEFAULT_TENANT).strip()
    # Legacy: if BRANDING_DIR is set and tenant is the legacy name, use it
    if LEGACY_BRANDING_DIR and tenant == Path(LEGACY_BRANDING_DIR).name:
        return Path(LEGACY_BRANDING_DIR)
    return BRANDING_ROOT / tenant


def _safe_tenant_name(name: str) -> str:
    """Reject path traversal."""
    if not name or "/" in name or ".." in name or name.startswith("."):
        raise HTTPException(400, "invalid tenant name")
    return name


# ---------------------------------------------------------------------------
# Asset loaders
# ---------------------------------------------------------------------------

def _load_company(tenant: Optional[str] = None) -> dict:
    p = _tenant_dir(tenant) / "company.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception as e:
            logger.warning(f"company.json parse failed for {p}: {e}")
    return {
        "name": "Dash",
        "domain": os.environ.get("DOMAIN", "localhost"),
        "theme": {"primary_color": "#00fc40", "background_color": "#0a0a0a"},
    }


# ---------------------------------------------------------------------------
# Public asset endpoints (used by /+layout.svelte on boot)
# ---------------------------------------------------------------------------

@router.get("")
def get_branding():
    payload = _load_company()
    payload["_active_tenant"] = _read_active_tenant()
    return payload


@router.get("/logo.svg")
def get_logo(t: Optional[str] = None):
    tenant = _safe_tenant_name(t) if t else None
    tdir = _tenant_dir(tenant)
    for fname, mtype in (("logo.png", "image/png"), ("logo.jpg", "image/jpeg"), ("logo.webp", "image/webp"), ("logo.svg", "image/svg+xml")):
        p = tdir / fname
        if p.exists():
            return FileResponse(str(p), media_type=mtype)
    bundled = Path(__file__).parent.parent / "frontend" / "build" / "brand" / "cityagent.png"
    if bundled.exists():
        return FileResponse(str(bundled), media_type="image/png")
    return Response(
        content='<svg xmlns="http://www.w3.org/2000/svg" width="160" height="32">'
                '<text x="0" y="22" font-family="Inter, sans-serif" font-size="18" '
                'font-weight="600" fill="#c96342">CityAgent Insights</text></svg>',
        media_type="image/svg+xml",
    )


@router.get("/favicon.ico")
def get_favicon(t: Optional[str] = None):
    tenant = _safe_tenant_name(t) if t else None
    p = _tenant_dir(tenant) / "favicon.ico"
    if not p.exists():
        raise HTTPException(404, "no favicon")
    return FileResponse(str(p), media_type="image/x-icon")


@router.get("/theme.css")
def get_theme_css(t: Optional[str] = None):
    tenant = _safe_tenant_name(t) if t else None
    p = _tenant_dir(tenant) / "theme.css"
    if not p.exists():
        return Response(content="/* default theme */", media_type="text/css")
    return FileResponse(str(p), media_type="text/css")


# ---------------------------------------------------------------------------
# Tenant management (super-admin)
# ---------------------------------------------------------------------------

class ActivateBody(BaseModel):
    tenant: str


def _scan_tenants() -> list[dict]:
    """Return list of tenants present on disk."""
    if not BRANDING_ROOT.exists() or not BRANDING_ROOT.is_dir():
        return []
    tenants = []
    for child in sorted(BRANDING_ROOT.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        company_path = child / "company.json"
        company_name = child.name
        if company_path.exists():
            try:
                data = json.loads(company_path.read_text())
                company_name = data.get("name") or data.get("full_name") or child.name
            except Exception:
                pass
        tenants.append({
            "name": child.name,
            "company_name": company_name,
            "has_logo": (child / "logo.svg").exists(),
            "has_theme": (child / "theme.css").exists(),
            "has_favicon": (child / "favicon.ico").exists(),
            "has_company_json": company_path.exists(),
        })
    return tenants


@router.get("/tenants")
def list_tenants(request: Request):
    _require_super_admin(request)
    tenants = _scan_tenants()
    if not tenants:
        return {
            "tenants": [],
            "active": _read_active_tenant(),
            "branding_root": str(BRANDING_ROOT),
            "message": (
                f"No tenants found. Create folder {BRANDING_ROOT}/<name>/ "
                "with logo.svg, theme.css, company.json, favicon.ico."
            ),
        }
    return {
        "tenants": tenants,
        "active": _read_active_tenant(),
        "branding_root": str(BRANDING_ROOT),
    }


@router.get("/active")
def get_active(request: Request):
    _require_super_admin(request)
    active = _read_active_tenant()
    return {"active": active, "exists": (_tenant_dir(active)).exists()}


@router.post("/active")
def set_active(body: ActivateBody, request: Request):
    _require_super_admin(request)
    tenant = _safe_tenant_name(body.tenant.strip())
    if not (BRANDING_ROOT / tenant).is_dir():
        # Allow legacy mapping
        if not (LEGACY_BRANDING_DIR and Path(LEGACY_BRANDING_DIR).name == tenant):
            raise HTTPException(404, f"tenant folder not found: {BRANDING_ROOT}/{tenant}")
    _write_active_tenant(tenant)
    return {"active": tenant, "ok": True}


# ---------------------------------------------------------------------------
# Tenant CRUD + assets (super-admin)
# ---------------------------------------------------------------------------

_ALLOWED_UPLOAD_MIME = {
    "logo": {"image/svg+xml"},
    "favicon": {"image/x-icon", "image/vnd.microsoft.icon", "image/png"},
}
_MAX_UPLOAD_BYTES = 500 * 1024  # 500KB


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


class CreateTenantBody(BaseModel):
    slug: str
    display_name: str
    clone_from: Optional[str] = None
    activate_after: Optional[bool] = False


@router.post("/tenants")
def create_tenant(body: CreateTenantBody, request: Request):
    _require_super_admin(request)
    slug = _safe_tenant_name(body.slug.strip())
    dest = BRANDING_ROOT / slug
    if dest.exists():
        raise HTTPException(409, f"tenant already exists: {slug}")

    source_slug = body.clone_from or DEFAULT_TENANT
    source_slug = _safe_tenant_name(source_slug.strip())
    source_dir = BRANDING_ROOT / source_slug
    if not source_dir.is_dir():
        # fall back to DEFAULT_TENANT silently when clone_from omitted; error if explicit
        if body.clone_from:
            raise HTTPException(404, f"clone_from tenant not found: {source_slug}")
        source_dir = None  # type: ignore[assignment]

    dest.mkdir(parents=True, exist_ok=False)

    # Seed company.json: copy from source, override name
    base_company: dict = {}
    if source_dir is not None:
        src_company = source_dir / "company.json"
        if src_company.exists():
            try:
                base_company = json.loads(src_company.read_text())
            except Exception:
                base_company = {}
    base_company["name"] = body.display_name
    _atomic_write(dest / "company.json", json.dumps(base_company, indent=2).encode("utf-8"))

    # Copy assets from source if present
    if source_dir is not None:
        for asset in ("logo.svg", "theme.css", "favicon.ico"):
            sp = source_dir / asset
            if sp.exists():
                try:
                    shutil.copyfile(sp, dest / asset)
                except Exception as e:
                    logger.warning(f"copy {asset} failed: {e}")

    if body.activate_after:
        _write_active_tenant(slug)

    logger.info(f"branding tenant created: {slug} (clone_from={source_slug})")
    return {"ok": True, "slug": slug}


@router.put("/tenants/{slug}")
def update_tenant_company(slug: str, body: dict, request: Request):
    _require_super_admin(request)
    slug = _safe_tenant_name(slug.strip())
    tdir = BRANDING_ROOT / slug
    if not tdir.is_dir():
        raise HTTPException(404, f"tenant not found: {slug}")
    if not isinstance(body, dict):
        raise HTTPException(400, "company.json body must be an object")
    payload = json.dumps(body, indent=2).encode("utf-8")
    _atomic_write(tdir / "company.json", payload)
    logger.info(f"branding company.json updated: {slug}")
    return {"ok": True, "slug": slug}


@router.post("/tenants/{slug}/upload")
async def upload_tenant_asset(
    slug: str,
    request: Request,
    asset_type: str = Form(...),
    file: UploadFile = File(...),
):
    _require_super_admin(request)
    slug = _safe_tenant_name(slug.strip())
    tdir = BRANDING_ROOT / slug
    if not tdir.is_dir():
        raise HTTPException(404, f"tenant not found: {slug}")

    asset_type = asset_type.strip().lower()
    if asset_type not in _ALLOWED_UPLOAD_MIME:
        raise HTTPException(400, "asset_type must be 'logo' or 'favicon'")

    mime = (file.content_type or "").lower()
    if mime not in _ALLOWED_UPLOAD_MIME[asset_type]:
        raise HTTPException(
            400,
            f"invalid mime for {asset_type}: {mime} (allowed: "
            f"{sorted(_ALLOWED_UPLOAD_MIME[asset_type])})",
        )

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file too large (>{_MAX_UPLOAD_BYTES} bytes)")
    if not data:
        raise HTTPException(400, "empty file")

    filename = "logo.svg" if asset_type == "logo" else "favicon.ico"
    _atomic_write(tdir / filename, data)
    logger.info(f"branding asset uploaded: {slug}/{filename} ({len(data)} bytes)")

    ts = int(time.time())
    url_path = "/api/branding/logo.svg" if asset_type == "logo" else "/api/branding/favicon.ico"
    return {"ok": True, "url": f"{url_path}?v={ts}"}


class ThemeBody(BaseModel):
    css: str


@router.put("/tenants/{slug}/theme")
def update_tenant_theme(slug: str, body: ThemeBody, request: Request):
    _require_super_admin(request)
    slug = _safe_tenant_name(slug.strip())
    tdir = BRANDING_ROOT / slug
    if not tdir.is_dir():
        raise HTTPException(404, f"tenant not found: {slug}")
    _atomic_write(tdir / "theme.css", body.css.encode("utf-8"))
    logger.info(f"branding theme.css updated: {slug} ({len(body.css)} chars)")
    return {"ok": True, "slug": slug}


@router.delete("/tenants/{slug}")
def delete_tenant(slug: str, request: Request):
    _require_super_admin(request)
    slug = _safe_tenant_name(slug.strip())
    if slug == "default":
        raise HTTPException(400, "cannot delete default tenant")
    active = _read_active_tenant()
    if slug == active:
        raise HTTPException(400, f"cannot delete active tenant: {slug}")
    tdir = BRANDING_ROOT / slug
    if not tdir.is_dir():
        raise HTTPException(404, f"tenant not found: {slug}")
    shutil.rmtree(tdir)
    logger.info(f"branding tenant deleted: {slug}")
    return {"ok": True, "slug": slug}


@router.get("/tenants/{slug}/export")
def export_tenant(slug: str, request: Request):
    _require_super_admin(request)
    slug = _safe_tenant_name(slug.strip())
    tdir = BRANDING_ROOT / slug
    if not tdir.is_dir():
        raise HTTPException(404, f"tenant not found: {slug}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for child in tdir.rglob("*"):
            if child.is_file():
                zf.write(child, arcname=child.relative_to(tdir))
    buf.seek(0)
    filename = f"branding-{slug}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tenants/{slug}/preview")
def preview_tenant(slug: str, request: Request):
    _require_super_admin(request)
    slug = _safe_tenant_name(slug.strip())
    tdir = BRANDING_ROOT / slug
    if not tdir.is_dir():
        raise HTTPException(404, f"tenant not found: {slug}")
    company = _load_company(slug)
    theme_path = _tenant_dir(slug) / "theme.css"
    theme_css = ""
    if theme_path.exists():
        try:
            theme_css = theme_path.read_text()
        except Exception as e:
            logger.warning(f"theme.css read failed for {slug}: {e}")
    return {
        "company": company,
        "theme_css": theme_css,
        "_preview_tenant": slug,
    }
