"""PHARMACY vertical SKU bundle — visibility template + brain seed + workflows."""
from __future__ import annotations

from .brain_seed import all_entries as _brain_entries
from .workflows import WORKFLOWS as _workflows

# Sample/synthetic data seeding has been removed from the product — a vertical
# pack seeds config only (brain + workflows + visibility template), never data.

BUNDLE: dict = {
    "label": "Pharmacy",
    "description": "Multi-store pharmacy network — visibility, drug knowledge, expiry/stockout/margin workflows",
    "icon": "💊",
    "visibility_template": "pharmacy",
    "brain_entries": _brain_entries(),
    "workflows": _workflows,
}
