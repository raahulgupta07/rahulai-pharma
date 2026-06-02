"""Vertical workflow pack API — detect/list/install.

Lightweight wrapper around `dash.workflows.verticals` resolver. Used by
project settings UI (workflow pack picker) + auto-install hook on /train-all.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vertical-packs"])


@router.get("/api/vertical-packs")
def list_all_packs(request: Request):
    """List every available pack (no schema match — just catalog)."""
    _ = _get_user(request)
    try:
        from dash.workflows.verticals import list_packs
        return {"packs": list_packs()}
    except Exception as e:
        logger.exception("list_packs failed")
        raise HTTPException(500, f"list_packs failed: {e}")


@router.get("/api/projects/{slug}/vertical-packs/detect")
def detect_packs(slug: str, request: Request):
    """Score all packs against project schema. Returns ranked list."""
    _ = _get_user(request)
    try:
        from dash.workflows.verticals import detect
        return {"matches": detect(slug)}
    except Exception as e:
        logger.exception("detect failed")
        raise HTTPException(500, f"detect failed: {e}")


@router.post("/api/projects/{slug}/vertical-packs/install")
async def install_pack(slug: str, request: Request):
    """Install workflows from named pack into project. Idempotent."""
    user = _get_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    pack_name = (body.get("pack") or "").strip()
    if not pack_name:
        raise HTTPException(400, "pack name required")
    # MDL packs use suffix `_mdl` by convention (e.g., crm_calls_mdl).
    # Route to install_mdl when name matches; else legacy install.
    use_mdl = pack_name.endswith("_mdl") or bool(body.get("mdl"))
    try:
        if use_mdl:
            from dash.workflows.verticals import install_mdl
            result = install_mdl(slug, pack_name, owner_user_id=user.get("user_id"))
        else:
            from dash.workflows.verticals import install
            result = install(slug, pack_name, owner_user_id=user.get("user_id"))
        if not result.get("ok"):
            raise HTTPException(400, result.get("error") or "install failed")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("install failed")
        raise HTTPException(500, f"install failed: {e}")
