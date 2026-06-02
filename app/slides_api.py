"""Slides API — markdown-driven presentation build + template profiling.

Additive endpoints. Does NOT replace /api/export/slides-agent (Slide Agent v2).
Used by skl_pptx_builder skill workflow + Dash chat artifact panel.

Endpoints:
    POST /api/slides/from-markdown   - outline_md → pres_id (skill-driven path)
    POST /api/slides/templates/profile - upload .pptx → profile config + save
    GET  /api/slides/templates       - list available templates (project + global)
    GET  /api/slides/{pres_id}/inventory - JSON text inventory of pres
    POST /api/slides/{pres_id}/patch - patch single slide spec
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/slides", tags=["slides"])


def _get_user(request: Request) -> dict:
    """Reuse auth pattern from app/export.py."""
    try:
        from app.export import _get_user as _u
        return _u(request)
    except Exception:
        # Best-effort token check fallback
        from app.auth import validate_token
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return validate_token(auth[7:]) or {}
        return {}


# ── POST /from-markdown ─────────────────────────────────────────────────
@router.post("/from-markdown")
async def slides_from_markdown(request: Request) -> Dict[str, Any]:
    """Build presentation from pre-built markdown outline.

    Body:
        outline_md: str       (required, pptx-from-layouts syntax)
        project_slug: str     (optional)
        title: str            (optional)
        theme: str            (optional, e.g. "midnight_executive")
        template_id: int      (optional, dash_slide_templates row id)
    """
    user = _get_user(request)
    body = await request.json()
    outline_md = body.get("outline_md") or ""
    if not outline_md.strip():
        raise HTTPException(400, "outline_md required")

    try:
        from dash.tools.slides import build_slides_from_md
    except Exception as e:
        raise HTTPException(500, f"slides_tools_import_failed: {e}")

    result = build_slides_from_md(
        outline_md=outline_md,
        project_slug=body.get("project_slug") or "",
        title=body.get("title") or "Presentation",
        theme=body.get("theme"),
        template_id=body.get("template_id"),
    )

    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "build_failed"))

    return result


# ── POST /templates/profile ─────────────────────────────────────────────
@router.post("/templates/profile")
async def upload_template(
    request: Request,
    file: UploadFile = File(...),
    project_slug: str = Form(""),
    name: str = Form(""),
) -> Dict[str, Any]:
    """Upload corp .pptx → profile layouts + colors → save row in
    dash.dash_slide_templates. Returns template_id for future builds."""
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "auth_required")

    # Save to tmp file so python-pptx can read
    suffix = ".pptx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            content = await file.read()
            fh.write(content)

        try:
            from dash.tools.slides import profile_template
        except Exception as e:
            raise HTTPException(500, f"slides_tools_import_failed: {e}")

        result = profile_template(tmp_path)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "profile_failed"))

        # Persist row
        eng = _engine()
        if eng is None:
            raise HTTPException(500, "no_engine")

        import json as _json
        from sqlalchemy import text
        try:
            with eng.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        INSERT INTO dash.dash_slide_templates
                            (project_slug, name, pptx_bytes, config, created_by)
                        VALUES (:s, :n, :b, CAST(:c AS jsonb), :uid)
                        RETURNING id
                        """
                    ),
                    {
                        "s": project_slug or None,
                        "n": name or (file.filename or "template"),
                        "b": content,
                        "c": _json.dumps(result["config"]),
                        "uid": user.get("id"),
                    },
                )
                template_id = row.fetchone()[0]
                conn.commit()
        except Exception as e:
            logger.warning("template save failed: %s", e)
            raise HTTPException(500, f"db_save_failed: {e}")

        return {
            "ok": True,
            "template_id": int(template_id),
            "config": result["config"],
        }
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ── GET /templates ──────────────────────────────────────────────────────
@router.get("/templates")
def list_templates(request: Request, project_slug: str = "") -> Dict[str, Any]:
    """Return templates for project + global (project_slug IS NULL)."""
    _get_user(request)
    eng = _engine()
    if eng is None:
        return {"templates": []}
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_slug, name, created_at,
                           jsonb_array_length(config->'layouts') AS layout_count
                    FROM dash.dash_slide_templates
                    WHERE project_slug = :s OR project_slug IS NULL
                    ORDER BY created_at DESC
                    LIMIT 200
                    """
                ),
                {"s": project_slug or None},
            ).mappings().all()
        return {"templates": [dict(r) for r in rows]}
    except Exception as e:
        logger.warning("list_templates failed: %s", e)
        return {"templates": [], "error": str(e)}


# ── GET /{pres_id}/inventory ────────────────────────────────────────────
@router.get("/{pres_id}/inventory")
def slides_inventory(pres_id: int, request: Request) -> Dict[str, Any]:
    """JSON inventory of slide text/bullets/notes for the agent to patch."""
    _get_user(request)
    try:
        from dash.tools.slides import inventory_slides
    except Exception as e:
        raise HTTPException(500, f"slides_tools_import_failed: {e}")
    return inventory_slides(pres_id)


# ── POST /{pres_id}/patch ───────────────────────────────────────────────
@router.post("/{pres_id}/patch")
async def slides_patch(pres_id: int, request: Request) -> Dict[str, Any]:
    """Patch single slide.

    Body:
        slide_idx: int
        patches: [{key: str, value: Any}, ...]
    """
    _get_user(request)
    body = await request.json()
    slide_idx = body.get("slide_idx")
    patches = body.get("patches") or []
    if slide_idx is None or not isinstance(patches, list):
        raise HTTPException(400, "slide_idx + patches required")

    try:
        from dash.tools.slides import patch_slide
    except Exception as e:
        raise HTTPException(500, f"slides_tools_import_failed: {e}")
    result = patch_slide(pres_id, int(slide_idx), patches)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "patch_failed"))
    return result


# ── POST /{pres_id}/slides/{idx}/regenerate ─────────────────────────────
@router.post("/{pres_id}/slides/{idx}/regenerate")
async def regenerate_slide(pres_id: int, idx: int, request: Request) -> Dict[str, Any]:
    """Regenerate a SINGLE slide via LLM, keeping layout. Updates DB and
    returns new spec.

    Body (optional):
        prompt: str  - free-text user direction (e.g. "make this punchier").
                       Empty/missing → default "make it sharper".
    """
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "auth_required")
    try:
        body = await request.json()
    except Exception:
        body = {}
    user_prompt = (body.get("prompt") or "").strip() or "make it sharper, tighter, more punchy"

    # Load current slide spec
    eng = _engine()
    if eng is None:
        raise HTTPException(500, "no_engine")
    import json as _json
    from sqlalchemy import text
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT slides FROM public.dash_presentations WHERE id=:id"),
                {"id": pres_id},
            ).mappings().first()
    except Exception as e:
        raise HTTPException(500, f"db_error: {e}")
    if not row:
        raise HTTPException(404, "pres_not_found")
    slides = row["slides"]
    if isinstance(slides, str):
        slides = _json.loads(slides)
    if idx < 0 or idx >= len(slides):
        raise HTTPException(400, f"idx_out_of_range (deck has {len(slides)} slides)")

    orig = slides[idx]
    layout = orig.get("layout") or "title_bullets"

    # LLM rewrite — LITE model via training_llm_call
    try:
        from dash.settings import training_llm_call
    except Exception as e:
        raise HTTPException(500, f"llm_import_failed: {e}")

    prompt = (
        "Rewrite this single presentation slide. Keep the layout exactly the same.\n"
        f"Layout: {layout}\n"
        f"User direction: {user_prompt}\n\n"
        "Rules:\n"
        "- Only rewrite title, bullets, action_line. Keep all other fields unchanged.\n"
        "- 3-5 bullets, each ≤ 12 words.\n"
        "- Title ≤ 12 words, sentence case, declarative.\n"
        "- action_line: 1 sentence, the 'so what'.\n"
        "- DO NOT invent numbers. If original has numbers, keep them.\n"
        "- NEVER use placeholders like [X], $XM, [Y]%.\n"
        "- NEVER cite McKinsey, Gartner, BCG, Bain, Forrester, or made-up reports.\n\n"
        f"Original slide JSON:\n{_json.dumps(orig, default=str)[:3000]}\n\n"
        'Return ONLY JSON: {"title": "...", "bullets": ["...", "..."], "action_line": "..."}'
    )

    raw = None
    try:
        raw = training_llm_call(prompt, task="extraction")
    except Exception as e:
        logger.warning("regenerate_slide llm failed: %s", e)
        raise HTTPException(500, f"llm_failed: {e}")

    # Lenient JSON parse
    import re as _re
    parsed = None
    if raw:
        s = raw.strip()
        if s.startswith("```"):
            s = _re.sub(r"^```(?:json)?\s*", "", s)
            s = _re.sub(r"\s*```$", "", s).strip()
        try:
            parsed = _json.loads(s)
        except Exception:
            m = _re.search(r"\{.*\}", s, _re.DOTALL)
            if m:
                try:
                    parsed = _json.loads(m.group(0))
                except Exception:
                    parsed = None
    if not parsed or not isinstance(parsed, dict):
        raise HTTPException(500, "llm_parse_failed")

    new_title = (parsed.get("title") or orig.get("title") or "").strip()[:200]
    new_bullets = parsed.get("bullets") or orig.get("bullets") or []
    if not isinstance(new_bullets, list):
        new_bullets = []
    new_bullets = [str(b).strip()[:300] for b in new_bullets if str(b).strip()][:8]
    new_action = (parsed.get("action_line") or orig.get("action_line") or "").strip()[:400]

    # Run sanitizer over the new slide
    new_slide = dict(orig)
    new_slide["title"] = new_title
    new_slide["bullets"] = new_bullets
    if new_action:
        new_slide["action_line"] = new_action
    new_slide["layout"] = layout  # enforce layout preservation
    try:
        from dash.tools.slide_sanitizer import sanitize_slide
        new_slide = sanitize_slide(new_slide)
    except Exception as e:
        logger.warning("regenerate_slide sanitize failed: %s", e)

    # Patch via patch_slide for DB update
    try:
        from dash.tools.slides import patch_slide
    except Exception as e:
        raise HTTPException(500, f"slides_tools_import_failed: {e}")
    patches = [
        {"key": "title", "value": new_slide.get("title", "")},
        {"key": "bullets", "value": new_slide.get("bullets", [])},
        {"key": "action_line", "value": new_slide.get("action_line", "")},
    ]
    result = patch_slide(pres_id, idx, patches)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "patch_failed"))

    return {
        "ok": True,
        "slide_idx": idx,
        "slide": new_slide,
        "prompt_used": user_prompt,
    }


# ── Engine helper ───────────────────────────────────────────────────────
def _engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None
