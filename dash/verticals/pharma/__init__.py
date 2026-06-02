"""PHARMACY vertical SKU bundle — visibility template + brain seed + workflows + sample data."""
from __future__ import annotations

from .brain_seed import all_entries as _brain_entries
from .workflows import WORKFLOWS as _workflows

# Optional sample data SQL (for demo projects). Path-relative to repo root.
SAMPLE_DATA_SQL_PATH = "pharma_seed_data/pharma_seed.sql"

BUNDLE: dict = {
    "label": "Pharmacy",
    "description": "Multi-store pharmacy network — visibility, drug knowledge, expiry/stockout/margin workflows",
    "icon": "💊",
    "visibility_template": "pharmacy",
    "brain_entries": _brain_entries(),
    "workflows": _workflows,
    "sample_data_sql_path": SAMPLE_DATA_SQL_PATH,
}
