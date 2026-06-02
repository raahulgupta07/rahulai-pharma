"""Phase 6A — visibility policy → multi-agent dashboard pipeline tests."""
from __future__ import annotations

import asyncio
import pytest

from dash.dashboards.agents.contracts import Finding
from dash.dashboards.agents import designer
from dash.dashboards.agents.text_guard import sanitize_narrative


def test_designer_picks_network_grid_for_network_tagged_finding():
    f = Finding(
        id="f1",
        headline="Stockouts across stores",
        sql="SELECT scope_id, sku, qty FROM stock GROUP BY scope_id, sku",
        data=[
            {"scope_id": "MUM01", "sku": "A1", "qty": 0},
            {"scope_id": "MUM02", "sku": "A1", "qty": 5},
            {"scope_id": "MUM01", "sku": "A2", "qty": 12},
        ],
        domain_tags=["network"],
    )
    decisions = asyncio.run(designer.design([f], persona="", prompt="across stores"))
    assert len(decisions) == 1
    d = decisions[0]
    assert d.cell_type == "network_grid"
    assert d.config.get("x_axis") == "scope_id"
    assert d.config.get("y_axis") == "sku"
    assert d.config.get("value_col") == "qty"
    assert d.config.get("band_legend") and len(d.config["band_legend"]) == 3
    # full width grid
    assert d.grid[2] == 12 and d.grid[3] == 4


def test_sanitize_strips_units_and_qty_when_network():
    text = "47 units sold; qty 1234 remaining; cost $5,678.90"
    out = sanitize_narrative(text, "demo", intent="network")
    assert "47 units" not in out
    assert "qty 1234" not in out
    assert "$5,678.90" not in out
    assert "[banded]" in out


def test_sanitize_passthrough_when_private():
    text = "47 units sold; qty 1234 remaining"
    out = sanitize_narrative(text, "demo", intent="private")
    assert out == text


def test_sanitize_handles_none_and_empty():
    assert sanitize_narrative("", "demo", "network") == ""
    assert sanitize_narrative(None, "demo", "network") == ""  # type: ignore[arg-type]
