"""Tests for pandas merge fallback (merge_pandas.py)."""
from __future__ import annotations
import sys
import types
from dataclasses import dataclass, field
from typing import Optional

import pytest

pd = pytest.importorskip("pandas")

from dash.providers.federation import merge_pandas
from dash.providers.federation.merge_pandas import (
    merge,
    MergeResult,
    _prefix_columns,
    _sql_where_to_pandas,
    _parse_select_cols,
)


# ---- Test fixtures ---------------------------------------------------------


@dataclass
class _JoinKey:
    left_provider: str
    left_column: str
    right_provider: str
    right_column: str
    op: str = "="


@dataclass
class _Plan:
    join_keys: list = field(default_factory=list)
    final_select: str = "*"
    final_where: str = ""
    final_order_by: str = ""
    final_limit: Optional[int] = None


@dataclass
class _ExecResult:
    per_source: dict = field(default_factory=dict)


# ---- Tests -----------------------------------------------------------------


def test_merge_single_source_passthrough():
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    plan = _Plan()
    er = _ExecResult(per_source={"prov_a": df})
    result = merge(plan, er)
    assert result.error is None
    assert result.row_count == 3
    assert result.engine_used == "single_source_passthrough"


def test_merge_two_source_inner_join_via_pandas():
    df_a = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    df_b = pd.DataFrame({"uid": [2, 3, 4], "score": [20, 30, 40]})
    plan = _Plan(
        join_keys=[_JoinKey("prov_a", "id", "prov_b", "uid")],
    )
    er = _ExecResult(per_source={"prov_a": df_a, "prov_b": df_b})
    result = merge(plan, er)
    assert result.error is None
    assert result.row_count == 2  # ids 2, 3 match
    assert "prov_a__id" in result.df.columns
    assert "prov_b__uid" in result.df.columns
    assert result.engine_used == "pandas"


def test_prefix_columns_avoids_collisions():
    df = pd.DataFrame({"name": [1, 2], "id": [10, 20]})
    out = _prefix_columns(df, "src-1")
    assert list(out.columns) == ["src_1__name", "src_1__id"]


def test_merge_handles_missing_join_column():
    df_a = pd.DataFrame({"id": [1, 2]})
    df_b = pd.DataFrame({"other": [3, 4]})
    plan = _Plan(
        join_keys=[_JoinKey("prov_a", "id", "prov_b", "missing_col")],
    )
    er = _ExecResult(per_source={"prov_a": df_a, "prov_b": df_b})
    result = merge(plan, er)
    # Join couldn't apply -> falls through cross-join + warning emitted
    assert any("join column missing" in w or "cross-join" in w for w in result.warnings)


def test_merge_no_join_keys_cross_joins():
    df_a = pd.DataFrame({"a": [1, 2]})
    df_b = pd.DataFrame({"b": [10, 20, 30]})
    plan = _Plan(join_keys=[])
    er = _ExecResult(per_source={"prov_a": df_a, "prov_b": df_b})
    result = merge(plan, er)
    assert result.error is None
    # Cross join: 2 * 3 = 6 rows
    assert result.row_count == 6
    assert any("cross-join" in w for w in result.warnings)


def test_sql_where_to_pandas_basic_translation():
    out = _sql_where_to_pandas("a = 1 AND b > 5")
    assert "==" in out
    assert "&" in out

    out2 = _sql_where_to_pandas("col IS NULL")
    assert ".isna()" in out2

    out3 = _sql_where_to_pandas("col IS NOT NULL")
    assert ".notna()" in out3

    out4 = _sql_where_to_pandas("a = 1 OR b = 2")
    assert "|" in out4
    # != preserved (not converted to ==)
    out5 = _sql_where_to_pandas("a != 1")
    assert "!=" in out5


def test_parse_select_cols():
    cols = _parse_select_cols("a.id, b.name AS user_name, qty")
    assert "id" in cols
    assert "user_name" in cols
    assert "qty" in cols

    cols2 = _parse_select_cols("SELECT col1, col2")
    assert cols2 == ["col1", "col2"]


def test_merge_respects_max_rows():
    df_a = pd.DataFrame({"id": list(range(100))})
    plan = _Plan()
    er = _ExecResult(per_source={"prov_a": df_a})
    result = merge(plan, er, max_final_rows=10)
    assert result.row_count == 10


def test_merge_returns_error_without_pandas(monkeypatch):
    """Simulate pandas import failure."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pandas":
            raise ImportError("simulated: pandas not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    plan = _Plan()
    er = _ExecResult(per_source={"prov_a": object()})
    result = merge(plan, er)
    assert result.error is not None
    assert "pandas not installed" in result.error


def test_merge_emits_warnings_on_imperfect():
    """Cross-join falls back when no join key links the second source."""
    df_a = pd.DataFrame({"id": [1, 2]})
    df_b = pd.DataFrame({"x": [9, 8]})
    plan = _Plan(join_keys=[])
    er = _ExecResult(per_source={"prov_a": df_a, "prov_b": df_b})
    result = merge(plan, er)
    assert result.error is None
    assert len(result.warnings) >= 1


def test_merge_applies_final_where():
    df_a = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
    plan = _Plan(final_where="prov_a__x > 2")
    er = _ExecResult(per_source={"prov_a": df_a, "prov_b": pd.DataFrame({"y": [9]})},)
    # Need >1 source so it goes through merge path; cross join then where
    result = merge(plan, er)
    # After cross-join with single-row df_b -> 5 rows; where x>2 -> 3 rows
    assert result.error is None
    assert result.row_count == 3


def test_merge_applies_final_select_projection():
    df_a = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    df_b = pd.DataFrame({"uid": [1, 2], "score": [10, 20]})
    plan = _Plan(
        join_keys=[_JoinKey("prov_a", "id", "prov_b", "uid")],
        final_select="name, score",
    )
    er = _ExecResult(per_source={"prov_a": df_a, "prov_b": df_b})
    result = merge(plan, er)
    assert result.error is None
    cols = list(result.df.columns)
    # Should contain prefixed columns matching name/score
    assert any(c.endswith("__name") for c in cols)
    assert any(c.endswith("__score") for c in cols)


def test_merge_result_interface_compatible():
    """MergeResult has the documented fields: df, row_count, duration_ms, engine_used, error, warnings."""
    r = MergeResult()
    for attr in ("df", "row_count", "duration_ms", "engine_used", "error", "warnings"):
        assert hasattr(r, attr), f"MergeResult missing attribute: {attr}"
