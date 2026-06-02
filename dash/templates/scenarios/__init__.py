"""Bundled scenario packs per vertical, used by AutoSim W1 (template-apply)."""
from __future__ import annotations

from . import (
    pharmacy,
    retail,
    hotel,
    finance,
    hr,
    saas,
    supply_chain,
    healthcare,
    banking,
    distribution,
)

SCENARIO_PACKS: dict[str, list[dict]] = {
    "pharmacy": pharmacy.SCENARIOS,
    "retail": retail.SCENARIOS,
    "hotel_food": hotel.SCENARIOS,
    "hotel": hotel.SCENARIOS,
    "financial_services": finance.SCENARIOS,
    "finance": finance.SCENARIOS,
    "hr": hr.SCENARIOS,
    "people": hr.SCENARIOS,
    "saas": saas.SCENARIOS,
    "tech_saas": saas.SCENARIOS,
    "supply_chain": supply_chain.SCENARIOS,
    "operations": supply_chain.SCENARIOS,
    "healthcare": healthcare.SCENARIOS,
    "banking": banking.SCENARIOS,
    "distribution": distribution.SCENARIOS,
}


def get_pack(vertical: str) -> list[dict]:
    """Return the scenario pack for a vertical, or [] if not registered."""
    return SCENARIO_PACKS.get((vertical or "").strip(), [])


__all__ = ["SCENARIO_PACKS", "get_pack"]
