"""Deck template loader.

Provides access to vertical deck templates (QBR, Investor Update, Ops Review,
Customer Review) defined as YAML files alongside this module. Each template
combines verified metrics + KG context + Brain narration.

Fail-soft: invalid/missing YAML files are skipped rather than raising.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:
    import yaml  # pyyaml is in requirements
except Exception:  # pragma: no cover - extreme fail-soft
    yaml = None  # type: ignore

_TEMPLATE_DIR = os.path.dirname(os.path.abspath(__file__))


def _safe_load(path: str) -> Optional[Dict[str, Any]]:
    if yaml is None:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if isinstance(data, dict) and data.get("id"):
            return data
    except Exception:
        return None
    return None


def _iter_template_files() -> List[str]:
    try:
        return sorted(
            os.path.join(_TEMPLATE_DIR, fn)
            for fn in os.listdir(_TEMPLATE_DIR)
            if fn.endswith((".yaml", ".yml"))
        )
    except Exception:
        return []


def list_templates() -> List[Dict[str, Any]]:
    """Return summary dicts for all templates.

    Each entry has: id, title, audience, slide_count, required_metrics.
    """
    out: List[Dict[str, Any]] = []
    for path in _iter_template_files():
        data = _safe_load(path)
        if not data:
            continue
        slides = data.get("slides") or []
        required: List[str] = []
        seen = set()
        for slide in slides:
            for m in (slide or {}).get("metrics_needed", []) or []:
                if m and m not in seen:
                    seen.add(m)
                    required.append(m)
        out.append(
            {
                "id": data.get("id"),
                "title": data.get("title"),
                "audience": data.get("audience"),
                "slide_count": len(slides),
                "required_metrics": required,
            }
        )
    return out


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    """Return the full parsed YAML for one template, or None if not found."""
    if not template_id:
        return None
    for path in _iter_template_files():
        data = _safe_load(path)
        if data and data.get("id") == template_id:
            return data
    return None


__all__ = ["list_templates", "get_template"]
