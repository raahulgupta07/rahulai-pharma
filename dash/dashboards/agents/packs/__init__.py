"""Domain skill packs for Scout agent."""
import logging
from . import generic, pharma, retail, finance

logger = logging.getLogger(__name__)

ALL_PACKS = {
    "generic": generic,
    "pharma": pharma,
    "retail": retail,
    "finance": finance,
}

def detect_pack(project_slug: str, persona: str = "") -> dict:
    """Pick best pack based on project tables + persona keywords."""
    persona_l = (persona or "").lower()

    # Strong signals from persona
    for name in ["pharma","retail","finance"]:
        if name in persona_l or name in (project_slug or "").lower():
            return ALL_PACKS[name].PACK

    # Try table heuristics
    try:
        from dash.dashboards.planner import _real_tables
        tables = _real_tables(project_slug)
        names = " ".join(t.get("name","").lower() for t in tables)
        if any(k in names for k in ["drug","medicine","pharmacy","sku","inventory","batch","lot"]):
            return ALL_PACKS["pharma"].PACK
        if any(k in names for k in ["sales","store","customer","cart","order"]):
            return ALL_PACKS["retail"].PACK
        if any(k in names for k in ["ledger","invoice","gl","journal","tax","revenue","cogs"]):
            return ALL_PACKS["finance"].PACK
    except Exception as e:
        logger.debug(f"pack detect failed: {e}")

    return ALL_PACKS["generic"].PACK
