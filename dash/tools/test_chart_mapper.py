"""Unit tests for chart_mapper. Skipped if pytest not installed."""
from __future__ import annotations

try:
    import pytest  # noqa: F401
except ImportError:  # pragma: no cover
    import sys
    print("pytest not installed; skipping.")
    sys.exit(0)

from dash.tools.chart_mapper import (
    build_chart_slide,
    detect_column_types,
    map_sql_result_to_chart,
)


def test_line_branch_temporal_numeric():
    rows = [
        {"month": "2026-01", "revenue": 1000},
        {"month": "2026-02", "revenue": 1200},
        {"month": "2026-03", "revenue": 1100},
        {"month": "2026-04", "revenue": 1400},
    ]
    out = map_sql_result_to_chart(rows, {"source_table": "sales", "rowcount": 4})
    assert out is not None
    assert out["chart_type"] == "line"
    assert out["chart_data"]["labels"] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert out["chart_data"]["series"][0]["values"] == [1000.0, 1200.0, 1100.0, 1400.0]


def test_bar_branch_categorical_numeric():
    rows = [
        {"region": "North", "sales": 500},
        {"region": "South", "sales": 700},
        {"region": "East", "sales": 600},
        {"region": "West", "sales": 800},
    ]
    out = map_sql_result_to_chart(rows, {})
    assert out is not None
    assert out["chart_type"] == "bar"
    assert len(out["chart_data"]["series"]) == 1
    assert out["chart_data"]["series"][0]["values"] == [500.0, 700.0, 600.0, 800.0]


def test_pie_branch_share_like():
    rows = [
        {"channel": "Web", "share_pct": 45},
        {"channel": "Mobile", "share_pct": 35},
        {"channel": "Store", "share_pct": 20},
    ]
    out = map_sql_result_to_chart(rows, {})
    assert out is not None
    assert out["chart_type"] == "pie"


def test_multi_series_bar():
    rows = [
        {"quarter": "Q1", "revenue": 100, "cost": 60},
        {"quarter": "Q2", "revenue": 120, "cost": 70},
        {"quarter": "Q3", "revenue": 140, "cost": 75},
    ]
    out = map_sql_result_to_chart(rows, {})
    assert out is not None
    assert out["chart_type"] == "bar"
    assert len(out["chart_data"]["series"]) == 2
    names = {s["name"] for s in out["chart_data"]["series"]}
    assert names == {"revenue", "cost"}


def test_returns_none_for_wide_long():
    rows = [
        {"a": i, "b": "x", "c": "y", "d": "z", "e": i * 2}
        for i in range(10)
    ]
    out = map_sql_result_to_chart(rows, {})
    assert out is None


def test_empty_rows():
    assert map_sql_result_to_chart([], {}) is None


def test_detect_column_types():
    rows = [{"m": "2026-01", "n": 10, "label": "foo"}]
    t = detect_column_types(rows)
    assert "m" in t["temporal"]
    assert "n" in t["numeric"]
    assert "label" in t["categorical"]


def test_build_chart_slide_wraps_with_source():
    rows = [
        {"month": "2026-01", "revenue": 1000},
        {"month": "2026-02", "revenue": 1200},
        {"month": "2026-03", "revenue": 1100},
    ]
    slide = build_chart_slide(
        rows,
        {"source_table": "sales_fact", "rowcount": 3},
        "TREND",
        "Revenue trend",
    )
    assert slide is not None
    assert slide["layout"] == "chart"
    assert slide["eyebrow"] == "TREND"
    assert slide["title"] == "Revenue trend"
    assert slide["source"] == "Source: sales_fact · n=3"
