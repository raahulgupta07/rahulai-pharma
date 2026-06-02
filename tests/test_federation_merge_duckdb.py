"""Tests for DuckDB-backed federation merge engine."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dash.providers.federation.executor import ExecutionResult  # noqa: E402
from dash.providers.federation.merge_duckdb import (  # noqa: E402
    _build_merge_sql,
    _sanitize_identifier,
    merge,
)
from dash.providers.federation.splitter import (  # noqa: E402
    JoinKey,
    SourceSubquery,
    SplitPlan,
)

duckdb = pytest.importorskip("duckdb")
pd = pytest.importorskip("pandas")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _exec_result(per_source: dict) -> ExecutionResult:
    er = ExecutionResult()
    er.per_source = per_source
    er.total_rows = sum(len(df) for df in per_source.values())
    return er


# ---------------------------------------------------------------------------
# Sanitize / SQL build unit tests
# ---------------------------------------------------------------------------


def test_sanitize_identifier_replaces_special_chars():
    assert _sanitize_identifier("source-a.table") == "source_a_table"
    assert _sanitize_identifier("my source!") == "my_source_"


def test_sanitize_identifier_prefixes_digit_starts():
    assert _sanitize_identifier("123abc").startswith("t_")


def test_sanitize_identifier_handles_empty():
    assert _sanitize_identifier("").startswith("t_")


def test_build_merge_sql_correctness():
    plan = SplitPlan(
        subqueries=[
            SourceSubquery(provider_id="a", sql="SELECT id FROM t1"),
            SourceSubquery(provider_id="b", sql="SELECT id FROM t2"),
        ],
        join_keys=[JoinKey("a", "id", "b", "id", op="=")],
        final_select="a.id",
    )
    er = _exec_result({
        "a": pd.DataFrame({"id": [1]}),
        "b": pd.DataFrame({"id": [1]}),
    })
    sql = _build_merge_sql(plan, er, max_rows=100)
    assert "JOIN b" in sql
    assert "a.id = b.id" in sql
    assert "LIMIT 100" in sql


def test_build_merge_sql_no_join_uses_cross_join():
    plan = SplitPlan(join_keys=[])
    er = _exec_result({
        "a": pd.DataFrame({"x": [1]}),
        "b": pd.DataFrame({"y": [2]}),
    })
    sql = _build_merge_sql(plan, er, max_rows=10)
    assert "CROSS JOIN" in sql


# ---------------------------------------------------------------------------
# merge() integration tests (real DuckDB)
# ---------------------------------------------------------------------------


def test_merge_single_source_passthrough():
    plan = SplitPlan()
    er = _exec_result({"a": pd.DataFrame({"x": [1, 2, 3]})})
    res = merge(plan, er)
    assert res.error is None
    assert res.engine_used == "single_source_passthrough"
    assert res.row_count == 3


def test_merge_two_source_inner_join():
    df_a = pd.DataFrame({"id": [1, 2, 3], "name": ["x", "y", "z"]})
    df_b = pd.DataFrame({"id": [1, 2], "score": [10, 20]})
    plan = SplitPlan(
        join_keys=[JoinKey("a", "id", "b", "id", op="=")],
        final_select="a.id, a.name, b.score",
    )
    er = _exec_result({"a": df_a, "b": df_b})

    res = merge(plan, er)
    assert res.error is None, res.error
    assert res.row_count == 2
    assert set(res.df.columns) == {"id", "name", "score"}


def test_merge_three_source_chain_join():
    df_a = pd.DataFrame({"id": [1, 2], "v": ["a1", "a2"]})
    df_b = pd.DataFrame({"id": [1, 2], "bid": [10, 20]})
    df_c = pd.DataFrame({"bid": [10, 20], "label": ["L1", "L2"]})
    plan = SplitPlan(
        join_keys=[
            JoinKey("a", "id", "b", "id", op="="),
            JoinKey("b", "bid", "c", "bid", op="="),
        ],
        final_select="a.id, a.v, c.label",
    )
    er = _exec_result({"a": df_a, "b": df_b, "c": df_c})

    res = merge(plan, er)
    assert res.error is None, res.error
    assert res.row_count == 2
    assert "label" in res.df.columns


def test_merge_handles_no_join_keys_cross_join():
    df_a = pd.DataFrame({"x": [1, 2]})
    df_b = pd.DataFrame({"y": [10, 20, 30]})
    plan = SplitPlan(join_keys=[], final_select="*")
    er = _exec_result({"a": df_a, "b": df_b})

    res = merge(plan, er)
    assert res.error is None, res.error
    assert res.row_count == 6  # 2 * 3 cartesian


def test_merge_respects_max_rows():
    df_a = pd.DataFrame({"x": list(range(50))})
    df_b = pd.DataFrame({"y": list(range(50))})
    plan = SplitPlan(join_keys=[], final_select="*")
    er = _exec_result({"a": df_a, "b": df_b})

    res = merge(plan, er, max_final_rows=10)
    assert res.error is None
    assert res.row_count == 10


def test_merge_respects_plan_final_limit():
    df_a = pd.DataFrame({"id": list(range(20))})
    df_b = pd.DataFrame({"id": list(range(20))})
    plan = SplitPlan(
        join_keys=[JoinKey("a", "id", "b", "id", op="=")],
        final_select="a.id",
        final_limit=5,
    )
    er = _exec_result({"a": df_a, "b": df_b})
    res = merge(plan, er, max_final_rows=1000)
    assert res.error is None
    assert res.row_count == 5


def test_merge_empty_per_source_returns_error():
    res = merge(SplitPlan(), _exec_result({}))
    assert res.error is not None
    assert "no per-source" in res.error


def test_merge_returns_error_when_duckdb_missing(monkeypatch):
    """If duckdb cannot be imported, merge() should return an error not raise."""
    import builtins

    real_import = builtins.__import__

    def _no_duckdb(name, *a, **kw):
        if name == "duckdb":
            raise ImportError("simulated: duckdb missing")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_duckdb)

    res = merge(
        SplitPlan(),
        _exec_result({"a": pd.DataFrame({"x": [1]})}),
    )
    assert res.error is not None
    assert "duckdb" in res.error.lower()


def test_merge_sanitizes_provider_ids_with_dashes():
    df_a = pd.DataFrame({"id": [1, 2]})
    df_b = pd.DataFrame({"id": [1, 2]})
    plan = SplitPlan(
        join_keys=[JoinKey("src-a", "id", "src-b", "id", op="=")],
        final_select="*",
    )
    er = _exec_result({"src-a": df_a, "src-b": df_b})

    res = merge(plan, er)
    assert res.error is None, res.error
    assert res.row_count == 2
