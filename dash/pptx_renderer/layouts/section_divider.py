"""Section divider layout (mirrors build.js layoutSectionDivider)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ._common import section_divider, set_speaker_notes

if TYPE_CHECKING:  # pragma: no cover
    from pptx.slide import Slide
    from dash.pptx_renderer.themes import Theme


def render(slide: "Slide", data: dict, theme: "Theme") -> None:
    """Render a section-divider slide.

    data keys: part, title, subtitle?
    """
    data = data or {}
    section_divider(
        slide,
        str(data.get("part") or "PART"),
        str(data.get("title") or ""),
        str(data.get("subtitle") or ""),
        theme,
    )
    set_speaker_notes(slide, data.get("speaker_notes"))
