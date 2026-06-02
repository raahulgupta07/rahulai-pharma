"""Table of Contents layout (mirrors build.js layoutToc)."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ._common import (
    PALETTE,
    add_rect, add_text_box, apply_theme_background, header, hex_to_rgbcolor,
    set_speaker_notes, subtitle_color, title_color, _theme_font,
)

if TYPE_CHECKING:  # pragma: no cover
    from pptx.slide import Slide
    from dash.pptx_renderer.themes import Theme


def render(slide: "Slide", data: dict, theme: "Theme") -> None:
    """Render a Table-of-Contents slide.

    data keys: title, eyebrow?, parts: [{num?, label?, title, pages?}]
    """
    data = data or {}
    apply_theme_background(slide, theme)
    header(
        slide,
        data.get("eyebrow") or "TABLE OF CONTENTS",
        data.get("title") or "What's inside",
        theme,
    )

    parts = data.get("parts") or []
    if not isinstance(parts, list) or not parts:
        return

    # Card surface uses theme.bg_alt so it contrasts on dark themes
    card_bg_hex = (
        getattr(theme, "bg_alt", None)
        or getattr(theme, "paper", None)
        or PALETTE["paper"]
    )
    divider_hex = getattr(theme, "divider", None) or PALETTE["divider"]
    accent_hex = getattr(theme, "accent", None) or PALETTE["teal"]
    part_title_rgb = title_color(theme)
    pages_rgb = subtitle_color(theme)
    # gray_hex retained for backwards-compat — not used post-fix
    _ = getattr(theme, "gray", None) or PALETTE["gray"]

    title_font = _theme_font(theme, "title_font", "Georgia")
    body_font = _theme_font(theme, "body_font", "Calibri")

    cols = 2
    gap = 0.2
    x0, y0, w, h = 0.5, 1.5, 9.0, 3.6
    rows = math.ceil(len(parts) / cols)
    card_w = (w - gap * (cols - 1)) / cols
    card_h = min(0.9, (h - gap * (rows - 1)) / rows) if rows > 0 else 0.9

    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            continue
        col = i % cols
        row = i // cols
        cx = x0 + col * (card_w + gap)
        cy = y0 + row * (card_h + gap)

        add_rect(slide, cx, cy, card_w, card_h, card_bg_hex,
                 line_hex=divider_hex, line_width_pt=1)
        add_rect(slide, cx, cy, 0.08, card_h, accent_hex)

        label = p.get("label") or p.get("num") or ""
        if label:
            add_text_box(
                slide, cx + 0.2, cy + 0.1, card_w - 1.4, 0.28,
                str(label).upper(),
                font=body_font, size=10, bold=True,
                color=hex_to_rgbcolor(accent_hex),
            )
        add_text_box(
            slide, cx + 0.2, cy + 0.38, card_w - 1.4, card_h - 0.42,
            str(p.get("title") or ""),
            font=title_font, size=14, bold=True,
            color=part_title_rgb,
            valign="top",
        )
        if p.get("pages"):
            add_text_box(
                slide, cx + card_w - 1.3, cy + (card_h - 0.3) / 2, 1.2, 0.3,
                f"pp. {p['pages']}",
                font=body_font, size=11, bold=True,
                color=pages_rgb,
                align="right",
            )

    set_speaker_notes(slide, data.get("speaker_notes"))
