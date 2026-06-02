"""Closing slide layout (mirrors build.js layoutClosing)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ._common import (
    PALETTE, SLIDE_H,
    add_rect, add_text_box, hex_to_rgbcolor,
    set_slide_background, set_speaker_notes, _theme_font,
    title_color, subtitle_color, bg_is_dark,
)

if TYPE_CHECKING:  # pragma: no cover
    from pptx.slide import Slide
    from dash.pptx_renderer.themes import Theme


def render(slide: "Slide", data: dict, theme: "Theme") -> None:
    """Render a closing slide.

    Accepts both shapes:
      - {title, message?, contact?, brand?}
      - {title, subtitle?, next_steps: [{title, body}]} (build.js shape)
    """
    data = data or {}

    bg_hex = getattr(theme, "bg", None) or PALETTE["ink"]
    accent_hex = getattr(theme, "accent", None) or PALETTE["teal"]
    slate_hex = getattr(theme, "slate", None) or PALETTE["slate"]

    title_font = _theme_font(theme, "title_font", "Georgia")
    body_font = _theme_font(theme, "body_font", "Calibri")

    set_slide_background(slide, bg_hex)
    add_rect(slide, 0, 0, 0.18, SLIDE_H, accent_hex)

    t_color = title_color(theme)
    sub_color = subtitle_color(theme)
    # Inner step-card numbers/title sit on slate tile bg — keep white for contrast
    step_text_color = hex_to_rgbcolor(PALETTE["white"])
    step_body_color = hex_to_rgbcolor(
        getattr(theme, "muted_light", None) or PALETTE["mutedLight"]
    )

    # Title — theme-aware
    add_text_box(
        slide, 0.6, 0.6, 8.8, 0.9,
        str(data.get("title") or "Next steps"),
        font=title_font, size=42, bold=True,
        color=t_color,
    )

    subtitle = data.get("subtitle") or data.get("message")
    if subtitle:
        add_text_box(
            slide, 0.6, 1.55, 8.8, 0.4,
            str(subtitle),
            font=body_font, size=14, italic=True,
            color=sub_color,
        )

    # Bullets shape (dashboard_to_deck path): list of strings → numbered cards
    bullets = data.get("bullets")
    if isinstance(bullets, list) and bullets and not data.get("next_steps"):
        data = dict(data)  # don't mutate caller
        data["next_steps"] = [{"title": str(b)[:60], "body": ""} for b in bullets[:3]]

    steps = data.get("next_steps") or []
    if isinstance(steps, list) and steps:
        steps = steps[:3]
        cx0 = 0.6
        cy = 2.4
        cw = (8.8 - 0.2 * (len(steps) - 1)) / len(steps)
        ch = 2.5
        for i, st in enumerate(steps):
            if not isinstance(st, dict):
                continue
            x = cx0 + i * (cw + 0.2)
            add_rect(slide, x, cy, cw, ch, slate_hex)
            add_rect(slide, x, cy, cw, 0.06, accent_hex)
            add_text_box(
                slide, x + 0.2, cy + 0.18, 0.5, 0.4,
                str(i + 1),
                font=title_font, size=16, bold=True,
                color=hex_to_rgbcolor(accent_hex),
            )
            add_text_box(
                slide, x + 0.2, cy + 0.65, cw - 0.4, 0.6,
                str(st.get("title") or ""),
                font=title_font, size=16, bold=True,
                color=step_text_color,
            )
            add_text_box(
                slide, x + 0.2, cy + 1.3, cw - 0.4, ch - 1.4,
                str(st.get("body") or ""),
                font=body_font, size=10,
                color=step_body_color,
                valign="top",
            )
    else:
        # Fallback simple contact / brand block — uses subtitle color to track theme
        contact = data.get("contact")
        if contact:
            add_text_box(
                slide, 0.6, 4.4, 8.8, 0.4,
                str(contact),
                font=body_font, size=14,
                color=sub_color,
            )
        brand = data.get("brand")
        if brand:
            add_text_box(
                slide, 0.6, 4.9, 8.8, 0.3,
                str(brand),
                font=body_font, size=11,
                color=hex_to_rgbcolor(accent_hex),
            )

    set_speaker_notes(slide, data.get("speaker_notes"))
