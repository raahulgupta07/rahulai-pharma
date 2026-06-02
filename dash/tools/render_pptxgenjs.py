"""
render_pptxgenjs.py — native python-pptx renderer entry point.

Historically this talked to a Node sidecar (dash-pptx / build.js). That path is
removed. All decks now render in-process via ``dash.pptx_renderer`` (python-pptx,
~30ms vs the old ~100-2000ms Node round-trip).

The public function name ``render_pptx_via_js`` and its signature are preserved
so existing callers (deep_deck.py, app/export.py) keep working unchanged.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict

log = logging.getLogger(__name__)


def render_pptx_via_js(
    spec: Dict[str, Any],
    output_path: str | None = None,
    theme: str = "midnight_executive",
    *,
    render_js_path: str | None = None,  # kept for signature compat; ignored
    timeout_s: int | None = None,       # kept for signature compat; ignored
) -> str:
    """Render a deck spec to a .pptx file via the native python-pptx renderer.

    Args/Returns kept identical to the previous sidecar version so callers need
    no changes. ``render_js_path`` / ``timeout_s`` are accepted but ignored.
    """
    if not isinstance(spec, dict):
        raise ValueError("render_pptx_via_js: spec must be a dict")
    if not isinstance(spec.get("slides"), list):
        raise ValueError("render_pptx_via_js: spec['slides'] must be a list")

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        out = output_path
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".pptx", delete=False)
        out = tmp.name
        tmp.close()

    from dash.pptx_renderer.renderer import render_to_path

    native_theme = spec.get("theme") or theme or "midnight_executive"
    return render_to_path(spec, out, theme_name=native_theme)
