"""Cover slide layout (mirrors build.js layoutCover)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ._common import (
    PALETTE, SLIDE_H,
    add_rect, add_text_box, hex_to_rgbcolor,
    set_slide_background, set_speaker_notes,
    _theme_font, title_color, subtitle_color, bg_is_dark,
)

if TYPE_CHECKING:  # pragma: no cover
    from pptx.slide import Slide
    from dash.pptx_renderer.themes import Theme


def render(slide: "Slide", data: dict, theme: "Theme") -> None:
    """Render a cover slide.

    data keys: title, subtitle?, tagline?, brand?, presenter?, date?,
               author?, eyebrow?, stats?: [{value, label}]
    """
    data = data or {}

    bg_hex = getattr(theme, "bg", None) or PALETTE["ink"]
    accent_hex = getattr(theme, "accent", None) or PALETTE["teal"]
    muted_hex = getattr(theme, "muted", None) or PALETTE["muted"]
    slate_hex = getattr(theme, "slate", None) or PALETTE["slate"]

    title_font = _theme_font(theme, "title_font", "Georgia")
    body_font = _theme_font(theme, "body_font", "Calibri")

    # Theme-aware background + accent left rail (rail stays brand color regardless of bg)
    set_slide_background(slide, bg_hex)
    add_rect(slide, 0, 0, 0.18, SLIDE_H, accent_hex)

    t_color = title_color(theme)
    sub_color = subtitle_color(theme)
    # Stat-tile inner numbers: keep contrast against slate tile bg (slate is always dark-ish)
    stat_value_color = hex_to_rgbcolor(PALETTE["white"])

    # Tagline / eyebrow
    tagline = data.get("tagline") or data.get("eyebrow")
    if tagline:
        add_text_box(
            slide, 0.6, 0.6, 6, 0.3,
            str(tagline).upper(),
            font=body_font, size=11, bold=True,
            color=hex_to_rgbcolor(accent_hex),
        )

    # Title — theme-aware (dark on light bg, white on dark bg)
    from ._common import responsive_size as _resp
    raw_title = str(data.get("title") or "")
    title_size = _resp(raw_title, base_size=54, min_size=22,
                       long_threshold=30, very_long_threshold=60, huge_threshold=90)
    add_text_box(
        slide, 0.6, 1.4, 6.2, 2.3,
        raw_title,
        font=title_font, size=title_size, bold=True,
        color=t_color,
        auto_shrink=True,
    )

    # Subtitle — theme-aware
    if data.get("subtitle"):
        add_text_box(
            slide, 0.6, 3.8, 6.2, 0.5,
            str(data["subtitle"]),
            font=body_font, size=18, italic=True,
            color=sub_color,
        )

    # Author / presenter / date
    author = (
        data.get("author")
        or data.get("presenter")
        or data.get("date")
    )
    if author:
        add_text_box(
            slide, 0.6, 5.0, 6, 0.3,
            str(author),
            font=body_font, size=11,
            color=hex_to_rgbcolor(muted_hex),
        )

    # Stat tiles on right — stylist-driven card colors with WCAG fallback
    stats = data.get("stats")
    if isinstance(stats, list) and stats:
        from dash.tools.deck_stylist import pick_readable_text, ensure_contrast
        stats = stats[:3]
        tile_x, tile_w, tile_h, tile_gap = 7.2, 2.4, 1.1, 0.15
        total_h = len(stats) * tile_h + (len(stats) - 1) * tile_gap
        start_y = (SLIDE_H - total_h) / 2

        # Stylist overrides (set by dashboard_to_deck)
        card_bg = (data.get("_card_bg") or "").lstrip("#") or slate_hex.lstrip("#")
        card_val = (data.get("_card_value_color") or "").lstrip("#")
        card_lbl = (data.get("_card_label_color") or "").lstrip("#")
        # Always enforce WCAG against the actual rendered bg
        card_val = ensure_contrast(card_val, card_bg, 4.5) if card_val else pick_readable_text(card_bg)
        card_lbl = ensure_contrast(card_lbl, card_bg, 4.5) if card_lbl else pick_readable_text(card_bg)
        card_val_rgb = hex_to_rgbcolor(card_val)
        card_lbl_rgb = hex_to_rgbcolor(card_lbl)

        for i, st in enumerate(stats):
            if not isinstance(st, dict):
                continue
            ty = start_y + i * (tile_h + tile_gap)
            add_rect(slide, tile_x, ty, tile_w, tile_h, card_bg,
                     line_hex=accent_hex, line_width_pt=1)
            add_text_box(
                slide, tile_x + 0.15, ty + 0.15, tile_w - 0.3, 0.55,
                str(st.get("value") or ""),
                font=title_font, size=26, bold=True,
                color=card_val_rgb,
            )
            label_raw = str(st.get("label") or "")
            if len(label_raw) > 24:
                label_raw = label_raw[:24].rstrip() + "…"
            add_text_box(
                slide, tile_x + 0.15, ty + 0.72, tile_w - 0.3, 0.34,
                label_raw,
                font=body_font, size=9,
                color=card_lbl_rgb,
                auto_shrink=True,
            )

    set_speaker_notes(slide, data.get("speaker_notes"))
