"""Content grid layout (mirrors prior Node renderer (now native python-pptx) layoutContentGrid + gridCards)."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ._common import (
    PALETTE,
    add_rect, add_text_box, apply_theme_background, header, hex_to_rgbcolor,
    set_speaker_notes, subtitle_color, title_color, _theme_font,
)
from dash.pptx_renderer.markdown_runs import render_markdown_text

if TYPE_CHECKING:  # pragma: no cover
    from pptx.slide import Slide
    from dash.pptx_renderer.themes import Theme


def _grid_cards(slide, cards, opts, theme):
    """Layout cards in a grid. Each card: {title, body, rail_color?}.

    Mirrors prior Node renderer (now native python-pptx) gridCards() — without icon support (pptx pictures
    handled separately if needed).
    """
    x0 = opts.get("x", 0.5)
    y0 = opts.get("y", 1.4)
    w = opts.get("w", 9.0)
    h = opts.get("h", 3.8)
    cols = max(1, opts.get("cols", 2))
    gap = opts.get("gap", 0.15)
    n = len(cards)
    if n == 0:
        return

    rows = math.ceil(n / cols)
    card_w = (w - gap * (cols - 1)) / cols
    card_h = (h - gap * (rows - 1)) / rows if rows > 0 else h

    # Card surface uses theme.bg_alt so cards stand out on both dark + light
    # backgrounds. Falls back to paper for backwards compat.
    card_bg_hex = (
        getattr(theme, "bg_alt", None)
        or getattr(theme, "paper", None)
        or PALETTE["paper"]
    )
    divider_hex = getattr(theme, "divider", None) or PALETTE["divider"]
    accent_hex = getattr(theme, "accent", None) or PALETTE["teal"]
    title_rgb = title_color(theme)
    # Body text color as hex string for markdown_runs. Uses theme.ink_soft on
    # light bg, muted_light on dark bg — falls back to steel.
    from ._common import bg_is_dark as _bg_is_dark
    if _bg_is_dark(theme):
        # On dark cards, ink_soft (A8A29A) is too dim → use bright ink for readability.
        body_hex = getattr(theme, "ink", None) or PALETTE["white"]
    else:
        body_hex = (
            getattr(theme, "ink_soft", None)
            or getattr(theme, "steel", None)
            or PALETTE["steel"]
        )

    title_font = _theme_font(theme, "title_font", "Georgia")
    body_font = _theme_font(theme, "body_font", "Calibri")

    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        col = i % cols
        row = i // cols
        cx = x0 + col * (card_w + gap)
        cy = y0 + row * (card_h + gap)
        rail = card.get("rail_color") or accent_hex
        rail = str(rail).lstrip("#")

        # Card body
        add_rect(slide, cx, cy, card_w, card_h, card_bg_hex,
                 line_hex=divider_hex, line_width_pt=1)
        # Left rail accent
        add_rect(slide, cx, cy, 0.08, card_h, rail)

        title_x = cx + 0.20
        title_w = card_w - 0.30

        # Title
        add_text_box(
            slide, title_x, cy + 0.18, title_w, 0.36,
            str(card.get("title") or ""),
            font=title_font, size=14, bold=True,
            color=title_rgb,
        )
        # Body — use markdown_runs for inline bold/italic/code parsing
        body_text = card.get("body")
        body_text = "" if body_text is None else str(body_text)
        if body_text:
            from pptx.util import Inches as _In
            from pptx.enum.text import MSO_ANCHOR as _ANCHOR
            tb = slide.shapes.add_textbox(
                _In(cx + 0.20), _In(cy + 0.62),
                _In(card_w - 0.30), _In(card_h - 0.72),
            )
            tf = tb.text_frame
            tf.margin_left = tf.margin_right = _In(0)
            tf.margin_top = tf.margin_bottom = _In(0)
            tf.word_wrap = True
            tf.vertical_anchor = _ANCHOR.TOP
            render_markdown_text(
                tf,
                body_text,
                base_font=body_font,
                base_size=10,
                base_color=body_hex,
                mono_font=_theme_font(theme, "mono_font", "JetBrains Mono"),
            )


def render(slide: "Slide", data: dict, theme: "Theme") -> None:
    """Render a content-grid slide.

    Accepts both shapes:
      - {title, eyebrow?, action_line?, bullets: [str]}  (spec from this file)
      - {title, eyebrow?, action_line?, cards: [{title, body, rail_color?}]}
        (richer shape from prior python-pptx renderer)
    """
    data = data or {}
    apply_theme_background(slide, theme)
    header(slide, data.get("eyebrow") or "", data.get("title") or "", theme)

    body_font = _theme_font(theme, "body_font", "Calibri")
    action_color = subtitle_color(theme)

    if data.get("action_line"):
        add_text_box(
            slide, 0.5, 1.20, 9, 0.3,
            str(data["action_line"]),
            font=body_font, size=11, italic=True,
            color=action_color,
        )

    # Normalize bullets → cards
    cards = data.get("cards")
    if not (isinstance(cards, list) and cards):
        bullets = data.get("bullets")
        if isinstance(bullets, list) and bullets:
            cards = []
            for b in bullets:
                if isinstance(b, dict):
                    cards.append(b)
                else:
                    cards.append({"title": "", "body": str(b)})
        else:
            cards = []

    if not cards:
        return

    cnt = len(cards)
    if cnt == 1:
        cols = 1
    elif cnt <= 3:
        cols = cnt
    elif cnt == 4:
        cols = 2
    elif cnt <= 6:
        cols = 3
    else:
        cols = 4

    y_start = 1.6 if data.get("action_line") else 1.45
    _grid_cards(
        slide, cards,
        {"x": 0.5, "y": y_start, "w": 9, "h": 5.1 - y_start,
         "cols": cols, "gap": 0.15},
        theme,
    )

    set_speaker_notes(slide, data.get("speaker_notes"))
