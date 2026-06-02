"""Status model layout (mirrors build.js layoutStatusModel)."""
from __future__ import annotations

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
    """Render a status model slide.

    Accepts both shapes:
      - {title, statuses: [{label, value, status}]}
      - {title, items:    [{label, description, status}]} (build.js shape)
    """
    data = data or {}
    apply_theme_background(slide, theme)
    header(slide, data.get("eyebrow") or "", data.get("title") or "", theme)

    items = data.get("items") or data.get("statuses") or []
    if not isinstance(items, list) or not items:
        return

    # Row background uses theme.bg_alt so it stays visible on dark themes
    row_bg_hex = (
        getattr(theme, "bg_alt", None)
        or getattr(theme, "paper", None)
        or PALETTE["paper"]
    )
    divider_hex = getattr(theme, "divider", None) or PALETTE["divider"]
    label_rgb = title_color(theme)
    desc_rgb = subtitle_color(theme)
    muted_hex = getattr(theme, "muted", None) or PALETTE["muted"]

    title_font = _theme_font(theme, "title_font", "Georgia")
    body_font = _theme_font(theme, "body_font", "Calibri")

    status_color = {
        "on":       getattr(theme, "emerald", None) or PALETTE["emerald"],
        "active":   getattr(theme, "emerald", None) or PALETTE["emerald"],
        "ok":       getattr(theme, "emerald", None) or PALETTE["emerald"],
        "degraded": getattr(theme, "amber",   None) or PALETTE["amber"],
        "warn":     getattr(theme, "amber",   None) or PALETTE["amber"],
        "warning":  getattr(theme, "amber",   None) or PALETTE["amber"],
        "off":      getattr(theme, "rose",    None) or PALETTE["rose"],
        "error":    getattr(theme, "rose",    None) or PALETTE["rose"],
        "fail":     getattr(theme, "rose",    None) or PALETTE["rose"],
        "planned":  muted_hex,
        "pending":  muted_hex,
    }
    status_glyph = {
        "on": "●", "active": "●", "ok": "●",
        "degraded": "●", "warn": "●", "warning": "●",
        "off": "●", "error": "●", "fail": "●",
        "planned": "○", "pending": "○",
    }

    x0, y0, w, h = 0.5, 1.4, 9.0, 3.6
    row_h = min(0.55, h / len(items))
    gap = 0.08

    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        y = y0 + i * (row_h + gap)
        st = str(it.get("status") or "planned").lower()
        col = status_color.get(st, muted_hex)
        glyph = status_glyph.get(st, "○")

        # Row background
        add_rect(slide, x0, y, w, row_h, row_bg_hex,
                 line_hex=divider_hex, line_width_pt=1)
        # Status dot
        add_text_box(
            slide, x0 + 0.1, y, 0.4, row_h,
            glyph,
            font=body_font, size=22, bold=True,
            color=hex_to_rgbcolor(col),
            align="center", valign="middle",
        )
        # Label
        add_text_box(
            slide, x0 + 0.55, y, 2.2, row_h,
            str(it.get("label") or ""),
            font=title_font, size=12, bold=True,
            color=label_rgb,
            valign="middle",
        )
        # Description / value
        desc = it.get("description")
        if desc is None:
            desc = it.get("value")
        add_text_box(
            slide, x0 + 2.85, y, w - 3.0, row_h,
            str(desc or ""),
            font=body_font, size=10,
            color=desc_rgb,
            valign="middle",
        )

    set_speaker_notes(slide, data.get("speaker_notes"))
