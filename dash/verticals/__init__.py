"""Vertical SKU bundles — preset + brain seed + workflows + sample data per industry."""
from __future__ import annotations

from .pharma import BUNDLE as pharma_bundle

ALL: dict[str, dict] = {
    "pharma": pharma_bundle,
}


def list_verticals() -> list[dict]:
    return [
        {
            "name": k,
            "label": v["label"],
            "description": v["description"],
            "icon": v.get("icon", "🏷"),
            "brain_count": len(v.get("brain_entries", [])),
            "workflow_count": len(v.get("workflows", [])),
        }
        for k, v in ALL.items()
    ]


def get_vertical(name: str) -> dict | None:
    return ALL.get(name)
