"""Phase 8A — industry visibility policy templates."""
from __future__ import annotations

from .pharmacy import TEMPLATE as pharmacy
from .retail import TEMPLATE as retail
from .hotel import TEMPLATE as hotel
from .bank import TEMPLATE as bank
from .generic import TEMPLATE as generic

ALL = {
    "pharmacy": pharmacy,
    "retail": retail,
    "hotel": hotel,
    "bank": bank,
    "generic": generic,
}


def list_templates() -> list[dict]:
    return [
        {
            "name": k,
            "label": v["label"],
            "description": v["description"],
            "scope_keyword": v["scope_keyword"],
            "icon": v.get("icon", "📦"),
            "field_count": _count_fields(v["policy"]),
        }
        for k, v in ALL.items()
    ]


def get_template(name: str) -> dict | None:
    return ALL.get(name)


def _count_fields(policy: dict) -> int:
    return sum(len(policy.get(a, {}).get("fields", {})) for a in ("private", "network", "public"))
