"""Layout registry for the python-pptx renderer.

Mirrors the 8 layouts in dash/render_js/build.js. Each module exposes a
`render(slide, data, theme)` function. The LAYOUT_REGISTRY dict + the
`render_layout()` helper give a stable API to the rest of the renderer.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict

from . import (
    chart,
    closing,
    content_grid,
    cover,
    section_divider,
    status_model,
    table,
    toc,
)

if TYPE_CHECKING:  # pragma: no cover
    from pptx.slide import Slide
    from dash.pptx_renderer.themes import Theme


LayoutFn = Callable[["Slide", dict, "Theme"], None]


LAYOUT_REGISTRY: Dict[str, LayoutFn] = {
    "cover":            cover.render,
    "toc":              toc.render,
    "section_divider":  section_divider.render,
    "content_grid":     content_grid.render,
    "table":            table.render,
    "chart":            chart.render,
    "status_model":     status_model.render,
    "closing":          closing.render,
}


def render_layout(name: str, slide: "Slide", data: dict, theme: "Theme") -> None:
    """Dispatch to the registered layout, defaulting to content_grid."""
    fn = LAYOUT_REGISTRY.get(name, content_grid.render)
    fn(slide, data or {}, theme)


__all__ = ["LAYOUT_REGISTRY", "render_layout"]
