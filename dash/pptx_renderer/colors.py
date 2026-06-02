"""Color helpers for the pptx_renderer theme system.

All hex strings are stored WITHOUT the leading '#' prefix to match Office
Open XML's `<a:srgbClr val="RRGGBB"/>` convention.
"""

from __future__ import annotations


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Convert a hex color (with or without '#') to an (R, G, B) tuple.

    Examples:
        hex_to_rgb("1a1614")   -> (26, 22, 20)
        hex_to_rgb("#FAF9F5")  -> (250, 249, 245)
    """
    if not isinstance(hex_str, str):
        raise TypeError(f"hex_to_rgb expected str, got {type(hex_str).__name__}")
    s = hex_str.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"hex_to_rgb expected 3 or 6 hex digits, got {hex_str!r}")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError as e:
        raise ValueError(f"hex_to_rgb invalid hex {hex_str!r}: {e}") from e


def normalize_hex(hex_str: str) -> str:
    """Return a 6-char uppercase hex string without '#' prefix."""
    r, g, b = hex_to_rgb(hex_str)
    return f"{r:02X}{g:02X}{b:02X}"
