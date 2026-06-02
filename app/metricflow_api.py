"""MetricFlow Import API — Phase 4.

REST endpoints for the MetricFlow → MDL loader (built in parallel by another
agent at ``dash.semantic.metricflow_loader``). If the loader module is not yet
importable, every endpoint returns 503.

Endpoints (prefix ``/api/metricflow``):

* ``POST /import``        multipart upload of one-or-more MetricFlow YAML files
* ``POST /import-text``   JSON body with raw YAML text (paste-in workflow)
* ``GET  /example``       a small reference YAML for users to copy/paste

Auth: matches accuracy_api / actions_api — uses ``_get_user(request)``.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metricflow", tags=["metricflow"])


# ── Auth helper (mirrors accuracy_api / actions_api) ──────────────────────

def _get_user(request: Request) -> Dict[str, Any]:
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


# ── Loader import (fail-soft → 503) ───────────────────────────────────────

def _loader():
    """Return the metricflow_loader module or raise 503 if unavailable."""
    try:
        from dash.semantic import metricflow_loader as ml  # type: ignore
    except Exception as e:  # pragma: no cover — parallel agent in-flight
        logger.warning("metricflow_loader unavailable: %s", e)
        raise HTTPException(
            503,
            "MetricFlow loader not available yet (dash.semantic.metricflow_loader missing).",
        )
    return ml


# ── Schemas ───────────────────────────────────────────────────────────────

class ImportTextBody(BaseModel):
    project_slug: str
    yaml_text: str
    dry_run: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────

_ALLOWED_EXT = (".yaml", ".yml")
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file (MetricFlow defs are small)


def _safe_name(name: str) -> str:
    base = os.path.basename(name or "")
    # strip anything that looks like a path traversal attempt
    return base.replace("..", "_").replace("/", "_").replace("\\", "_") or "file.yaml"


async def _stage_uploads(files: List[UploadFile], tmpdir: str) -> List[str]:
    written: List[str] = []
    for f in files:
        name = _safe_name(f.filename or "")
        if not name.lower().endswith(_ALLOWED_EXT):
            raise HTTPException(400, f"Only .yaml/.yml files allowed (got '{f.filename}')")
        dest = os.path.join(tmpdir, name)
        size = 0
        with open(dest, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise HTTPException(413, f"File '{name}' exceeds {_MAX_BYTES} bytes")
                out.write(chunk)
        written.append(dest)
    if not written:
        raise HTTPException(400, "No YAML files provided")
    return written


# ── POST /import ──────────────────────────────────────────────────────────

@router.post("/import")
async def import_files(
    request: Request,
    project_slug: str = Form(...),
    files: List[UploadFile] = File(...),
    dry_run: bool = Form(False),
) -> Dict[str, Any]:
    """Upload MetricFlow YAML files and install (or preview) them.

    * If ``dry_run`` is true → call ``load_metricflow_dir`` + ``metricflow_to_mdl``
      and return the MDL preview WITHOUT touching the database.
    * Otherwise → call ``install_metricflow`` to persist into the project's MDL store.

    Files are written to a temp dir and removed when the request completes.
    """
    _get_user(request)
    ml = _loader()

    slug = (project_slug or "").strip()
    if not slug:
        raise HTTPException(400, "project_slug is required")

    tmpdir = tempfile.mkdtemp(prefix="metricflow_")
    try:
        await _stage_uploads(files, tmpdir)

        if dry_run:
            try:
                data = ml.load_metricflow_dir(tmpdir)
                mdl = ml.metricflow_to_mdl(data)
            except Exception as e:
                logger.exception("dry-run failed: %s", e)
                raise HTTPException(400, f"YAML parse / transform failed: {e}")
            return {
                "ok": True,
                "dry_run": True,
                "project_slug": slug,
                "mdl_preview": mdl,
                "models_in_preview": len(mdl.get("models", []) or []),
                "metrics_in_preview": len(mdl.get("metrics", []) or []),
            }

        try:
            result = ml.install_metricflow(slug, tmpdir)
        except Exception as e:
            logger.exception("install_metricflow failed for %s: %s", slug, e)
            raise HTTPException(500, f"install_metricflow failed: {e}")

        return {"dry_run": False, "project_slug": slug, **(result or {})}
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


# ── POST /import-text ─────────────────────────────────────────────────────

@router.post("/import-text")
def import_text(request: Request, body: ImportTextBody) -> Dict[str, Any]:
    """Same as /import, but accepts a single YAML blob via JSON body."""
    _get_user(request)
    ml = _loader()

    slug = (body.project_slug or "").strip()
    if not slug:
        raise HTTPException(400, "project_slug is required")
    text = (body.yaml_text or "").strip()
    if not text:
        raise HTTPException(400, "yaml_text is empty")
    if len(text.encode("utf-8")) > _MAX_BYTES:
        raise HTTPException(413, f"yaml_text exceeds {_MAX_BYTES} bytes")

    tmpdir = tempfile.mkdtemp(prefix="metricflow_text_")
    try:
        fp = os.path.join(tmpdir, "paste.yaml")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)

        if body.dry_run:
            try:
                data = ml.load_metricflow_dir(tmpdir)
                mdl = ml.metricflow_to_mdl(data)
            except Exception as e:
                logger.exception("dry-run (text) failed: %s", e)
                raise HTTPException(400, f"YAML parse / transform failed: {e}")
            return {
                "ok": True,
                "dry_run": True,
                "project_slug": slug,
                "mdl_preview": mdl,
                "models_in_preview": len(mdl.get("models", []) or []),
                "metrics_in_preview": len(mdl.get("metrics", []) or []),
            }

        try:
            result = ml.install_metricflow(slug, tmpdir)
        except Exception as e:
            logger.exception("install_metricflow (text) failed for %s: %s", slug, e)
            raise HTTPException(500, f"install_metricflow failed: {e}")
        return {"dry_run": False, "project_slug": slug, **(result or {})}
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


# ── GET /example ──────────────────────────────────────────────────────────

_EXAMPLE_YAML = """\
# MetricFlow example — paste, edit, then upload via /api/metricflow/import.
# One YAML file may contain multiple `semantic_models:` and `metrics:` entries.

semantic_models:
  - name: orders
    description: "One row per customer order."
    model: ref('orders')          # or: source('shop', 'orders')
    entities:
      - name: order_id
        type: primary
      - name: customer_id
        type: foreign
    dimensions:
      - name: order_date
        type: time
        type_params:
          time_granularity: day
      - name: status
        type: categorical
    measures:
      - name: order_count
        agg: count
        expr: "1"
      - name: order_total
        agg: sum
        expr: amount

metrics:
  - name: total_orders
    description: "Total number of orders placed."
    type: simple
    type_params:
      measure: order_count

  - name: gross_revenue
    description: "Sum of order totals, gross of refunds."
    type: simple
    type_params:
      measure: order_total
"""


@router.get("/example")
def example_yaml() -> Dict[str, Any]:
    """Return a small reference MetricFlow YAML for copy/paste workflows."""
    return {
        "ok": True,
        "filename": "example_metricflow.yaml",
        "yaml": _EXAMPLE_YAML,
    }
