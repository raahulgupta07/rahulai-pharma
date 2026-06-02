"""8-theme system for the python-pptx renderer.

Each theme is an immutable dataclass with full palette + font pair. The
`apply_theme()` function patches the presentation's master `theme1.xml`
to replace `<a:clrScheme>` and `<a:fontScheme>` so all native PPTX text
and chart colors inherit from the chosen palette.

NOTE: the prior Node renderer (now replaced by native python-pptx) only
defined a single dark `C` palette. The 8 theme palettes here were
synthesized from theme names + CityAI brand defaults (coral `#c96342`
on cream `#faf9f5`). The dataclass + apply_theme API is stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lxml import etree

from .colors import hex_to_rgb, normalize_hex

# DrawingML namespace used in theme1.xml.
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NSMAP = {"a": NS_A}


@dataclass(frozen=True)
class Theme:
    """Immutable theme definition.

    All hex strings are stored WITHOUT the leading '#' prefix to match
    PPTX `<a:srgbClr val="RRGGBB"/>` convention.
    """

    name: str
    label: str
    bg: str
    bg_alt: str
    ink: str
    ink_soft: str
    accent: str
    accent_soft: str
    teal: Optional[str] = None
    coral: Optional[str] = None
    palette: list[str] = field(default_factory=list)
    title_font: str = "Source Serif 4"
    body_font: str = "Inter"
    mono_font: str = "JetBrains Mono"


# ---------------------------------------------------------------------------
# 8 themes
# ---------------------------------------------------------------------------

THEMES: dict[str, Theme] = {
    "midnight_executive": Theme(
        name="midnight_executive",
        label="Midnight Executive",
        bg="1A1614",
        bg_alt="2A2522",
        ink="F5F2EC",
        ink_soft="A8A29A",
        accent="14B8A6",
        accent_soft="0D9488",
        teal="14B8A6",
        palette=["14B8A6", "F59E0B", "F43F5E", "10B981", "06B6D4", "8B5CF6", "EAB308"],
    ),
    "forest_moss": Theme(
        name="forest_moss",
        label="Forest & Moss",
        bg="1B2A1F",
        bg_alt="263A2D",
        ink="EFEDE3",
        ink_soft="A8B3A2",
        accent="84A98C",
        accent_soft="52796F",
        teal="588157",
        palette=["84A98C", "B7B597", "CAD2C5", "52796F", "354F52", "A3B18A", "DDA15E"],
    ),
    "coral_energy": Theme(
        name="coral_energy",
        label="Coral Energy",
        bg="FAF9F5",
        bg_alt="F3EFE7",
        ink="2C2A26",
        ink_soft="7A7570",
        accent="C96342",
        accent_soft="E8B4A0",
        coral="C96342",
        palette=["C96342", "E8B4A0", "F4A261", "E76F51", "264653", "2A9D8F", "E9C46A"],
    ),
    "ocean_gradient": Theme(
        name="ocean_gradient",
        label="Ocean Gradient",
        bg="F0F7FA",
        bg_alt="DCEEF4",
        ink="0F2A3D",
        ink_soft="4A6B7C",
        accent="0EA5E9",
        accent_soft="06B6D4",
        teal="14B8A6",
        palette=["0EA5E9", "06B6D4", "14B8A6", "3B82F6", "6366F1", "0891B2", "0D9488"],
    ),
    "charcoal_minimal": Theme(
        name="charcoal_minimal",
        label="Charcoal Minimal",
        bg="FAFAFA",
        bg_alt="EDEDED",
        ink="111111",
        ink_soft="525252",
        accent="262626",
        accent_soft="737373",
        palette=["111111", "404040", "737373", "A3A3A3", "D4D4D4", "E5E5E5", "525252"],
    ),
    "teal_trust": Theme(
        name="teal_trust",
        label="Teal Trust",
        bg="F4F8F8",
        bg_alt="DEEBEC",
        ink="0F172A",
        ink_soft="475569",
        accent="0D9488",
        accent_soft="14B8A6",
        teal="0D9488",
        palette=["0D9488", "0F172A", "14B8A6", "1E40AF", "0EA5E9", "64748B", "94A3B8"],
    ),
    "berry_cream": Theme(
        name="berry_cream",
        label="Berry & Cream",
        bg="FAF7F2",
        bg_alt="F0E8DC",
        ink="3D1F2C",
        ink_soft="8A6373",
        accent="9D174D",
        accent_soft="DB2777",
        palette=["9D174D", "DB2777", "F472B6", "FBCFE8", "BE185D", "831843", "F9A8D4"],
    ),
    "cherry_bold": Theme(
        name="cherry_bold",
        label="Cherry Bold",
        bg="FFFFFF",
        bg_alt="F5F5F5",
        ink="0A0A0A",
        ink_soft="404040",
        accent="DC2626",
        accent_soft="EF4444",
        palette=["DC2626", "0A0A0A", "EF4444", "991B1B", "F87171", "525252", "FCA5A5"],
    ),
}

DEFAULT_THEME = "midnight_executive"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_theme(name: str | None) -> Theme:
    """Return Theme by name; fall back to DEFAULT_THEME on miss/None."""
    if not name:
        return THEMES[DEFAULT_THEME]
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def get_theme_with_overrides(
    name: str | None,
    accent_hex: str | None = None,
    palette: list[str] | None = None,
) -> Theme:
    """Return Theme with runtime accent/palette override.
    Used by AI deck stylist to recolor a registered theme per dashboard."""
    from dataclasses import replace as _dc_replace
    base = get_theme(name)
    patch: dict = {}
    if accent_hex:
        a = accent_hex.lstrip("#").upper()
        if len(a) == 6 and all(c in "0123456789ABCDEF" for c in a):
            patch["accent"] = a
    if palette:
        cleaned = []
        for c in palette:
            s = (c or "").lstrip("#").upper()
            if len(s) == 6 and all(ch in "0123456789ABCDEF" for ch in s):
                cleaned.append(s)
        if cleaned:
            patch["palette"] = cleaned
    if not patch:
        return base
    try:
        return _dc_replace(base, **patch)
    except Exception:
        return base


def palette_color(theme: Theme, index: int) -> str:
    """Return a hex from the theme palette by index (wraps modulo length)."""
    if not theme.palette:
        return theme.accent
    return theme.palette[index % len(theme.palette)]


# Re-export the colors helper for convenience.
__all__ = [
    "Theme",
    "THEMES",
    "DEFAULT_THEME",
    "get_theme",
    "get_theme_with_overrides",
    "palette_color",
    "hex_to_rgb",
    "apply_theme",
]


# ---------------------------------------------------------------------------
# XML patching: apply_theme
# ---------------------------------------------------------------------------

def _make_srgb_color(parent_tag: str, hex_val: str) -> etree._Element:
    """Build `<a:{parent_tag}><a:srgbClr val="RRGGBB"/></a:{parent_tag}>`."""
    parent = etree.SubElement(etree.Element("tmp"), f"{{{NS_A}}}{parent_tag}")
    srgb = etree.SubElement(parent, f"{{{NS_A}}}srgbClr")
    srgb.set("val", normalize_hex(hex_val))
    return parent


def _build_clr_scheme(theme: Theme) -> etree._Element:
    """Build a fresh <a:clrScheme> element from the theme palette.

    Color slot mapping:
      lt1 → white  (light bg slot, always white)
      dk1 → ink    (main text on light bg)
      lt2 → bg_alt
      dk2 → ink_soft
      accent1 → accent
      accent2 → teal or coral or palette[0]
      accent3..accent6 → palette[1..4]
      hlink → accent_soft
      folHlink → ink_soft
    """
    clr = etree.Element(f"{{{NS_A}}}clrScheme", nsmap=_NSMAP)
    clr.set("name", theme.label)

    def _sys(tag: str, system_val: str, last_clr: str) -> etree._Element:
        wrapper = etree.SubElement(clr, f"{{{NS_A}}}{tag}")
        sys = etree.SubElement(wrapper, f"{{{NS_A}}}sysClr")
        sys.set("val", system_val)
        sys.set("lastClr", normalize_hex(last_clr))
        return wrapper

    def _srgb(tag: str, hex_val: str) -> etree._Element:
        wrapper = etree.SubElement(clr, f"{{{NS_A}}}{tag}")
        srgb = etree.SubElement(wrapper, f"{{{NS_A}}}srgbClr")
        srgb.set("val", normalize_hex(hex_val))
        return wrapper

    # Mandatory order per ECMA-376: dk1, lt1, dk2, lt2, accent1..6, hlink, folHlink
    _sys("dk1", "windowText", theme.ink)
    _sys("lt1", "window", "FFFFFF")
    _srgb("dk2", theme.ink_soft)
    _srgb("lt2", theme.bg_alt)

    # accent1 = primary accent
    _srgb("accent1", theme.accent)

    # accent2 = teal/coral/palette[0] fallback chain
    accent2 = theme.teal or theme.coral or (theme.palette[0] if theme.palette else theme.accent)
    _srgb("accent2", accent2)

    # accent3..accent6 from palette[1..4]
    for i, slot in enumerate(("accent3", "accent4", "accent5", "accent6"), start=1):
        hex_val = palette_color(theme, i)
        _srgb(slot, hex_val)

    _srgb("hlink", theme.accent_soft)
    _srgb("folHlink", theme.ink_soft)
    return clr


def _build_font_scheme(theme: Theme) -> etree._Element:
    """Build a fresh <a:fontScheme> element from the theme fonts."""
    fs = etree.Element(f"{{{NS_A}}}fontScheme", nsmap=_NSMAP)
    fs.set("name", theme.label)

    def _font_set(role: str, latin: str) -> None:
        major = etree.SubElement(fs, f"{{{NS_A}}}{role}")
        latin_el = etree.SubElement(major, f"{{{NS_A}}}latin")
        latin_el.set("typeface", latin)
        # Empty east-asian + complex-script siblings (Office expects them).
        ea = etree.SubElement(major, f"{{{NS_A}}}ea")
        ea.set("typeface", "")
        cs = etree.SubElement(major, f"{{{NS_A}}}cs")
        cs.set("typeface", "")

    _font_set("majorFont", theme.title_font)
    _font_set("minorFont", theme.body_font)
    return fs


def apply_theme(prs, theme: Theme) -> None:
    """Patch the presentation's first slide-master theme XML.

    Walks `prs.slide_masters[0].element` for `<a:theme>` and replaces its
    `<a:clrScheme>` and `<a:fontScheme>` children with theme-derived ones.

    Safe to call multiple times — always replaces in place. Silently no-ops
    if no theme element is found (defensive: some templates may strip it).
    """
    if not isinstance(theme, Theme):
        raise TypeError(f"apply_theme expected Theme, got {type(theme).__name__}")
    if not prs.slide_masters:
        return

    master = prs.slide_masters[0]
    # python-pptx exposes the underlying lxml element via `.element`.
    master_el = master.element

    # The <a:theme> root is reached via the master's theme part — best to
    # walk the master_el tree first, then fall back to part-level lookup.
    theme_root = None
    # Try descendant search first (covers embedded references).
    for el in master_el.iter():
        tag = etree.QName(el).localname
        if tag == "theme" and el.tag.startswith(f"{{{NS_A}}}"):
            theme_root = el
            break

    # Fallback: dig into the related theme part via python-pptx internals.
    if theme_root is None:
        try:
            theme_part = master.part.part_related_by(
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
            )
            theme_root = theme_part.element
        except Exception:
            theme_root = None

    if theme_root is None:
        return

    # Locate <a:themeElements> — the parent of clrScheme + fontScheme.
    theme_elements = theme_root.find(f"{{{NS_A}}}themeElements")
    if theme_elements is None:
        return

    # Replace clrScheme
    old_clr = theme_elements.find(f"{{{NS_A}}}clrScheme")
    new_clr = _build_clr_scheme(theme)
    if old_clr is not None:
        theme_elements.replace(old_clr, new_clr)
    else:
        theme_elements.insert(0, new_clr)

    # Replace fontScheme (must follow clrScheme per schema).
    old_font = theme_elements.find(f"{{{NS_A}}}fontScheme")
    new_font = _build_font_scheme(theme)
    if old_font is not None:
        theme_elements.replace(old_font, new_font)
    else:
        # Insert after clrScheme (index 1 if clrScheme is at 0).
        clr_idx = list(theme_elements).index(new_clr)
        theme_elements.insert(clr_idx + 1, new_font)
