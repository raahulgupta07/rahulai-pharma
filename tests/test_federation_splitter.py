"""Tests for federation SQL splitter — per-source subqueries + JOIN extraction."""
from __future__ import annotations

import pytest

from dash.providers.federation import parser as fed_parser
from dash.providers.federation import splitter as fed_splitter
from dash.providers.federation.parser import ParsedFederatedSQL, TableRef
from dash.providers.federation.splitter import (
    JoinKey,
    SourceSubquery,
    SplitPlan,
    split,
)

try:
    import sqlglot  # noqa: F401
    _HAS_SQLGLOT = True
except ImportError:
    _HAS_SQLGLOT = False


pytestmark_sqlglot = pytest.mark.skipif(
    not _HAS_SQLGLOT,
    reason="sqlglot not installed; AST-based splitting requires it",
)


# ---------------------------------------------------------------------------
# Tests that don't need sqlglot (use synthetic ParsedFederatedSQL)
# ---------------------------------------------------------------------------

def test_no_ast_falls_back_to_select_star():
    """When parser returned no AST (sqlglot missing/parse failed),
    splitter falls back to per-table SELECT *."""
    parsed = ParsedFederatedSQL(
        raw_sql="SELECT * FROM source_a.t1 JOIN source_b.t2 ON 1=1",
        table_refs=[
            TableRef(provider_id="source_a", table_name="t1", full="source_a.t1"),
            TableRef(provider_id="source_b", table_name="t2", full="source_b.t2"),
        ],
        provider_ids={"source_a", "source_b"},
        is_federated=True,
        ast=None,
    )
    plan = split(parsed)
    assert plan.error is None
    assert len(plan.subqueries) == 2
    for sq in plan.subqueries:
        assert sq.sql.startswith("SELECT * FROM ")
        assert sq.columns_needed == ["*"]
    assert any("fallback" in w.lower() for w in plan.warnings)


def test_propagates_parser_error():
    parsed = ParsedFederatedSQL(
        raw_sql="bogus",
        error="parse failed: syntax error",
    )
    plan = split(parsed)
    assert plan.error is not None
    assert "parse failed" in plan.error
    assert plan.subqueries == []


def test_dataclass_defaults():
    plan = SplitPlan()
    assert plan.subqueries == []
    assert plan.join_keys == []
    assert plan.final_select == ""
    assert plan.final_limit is None

    sq = SourceSubquery(provider_id="x", sql="SELECT 1")
    assert sq.columns_needed == []
    assert sq.pushed_filters == []
    assert sq.estimated_rows == 0

    jk = JoinKey(left_provider="a", left_column="x",
                 right_provider="b", right_column="y")
    assert jk.op == "="


# ---------------------------------------------------------------------------
# AST-based tests (require sqlglot)
# ---------------------------------------------------------------------------

@pytestmark_sqlglot
def test_simple_two_source_join_split():
    sql = (
        "SELECT a.x, b.y FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.x = b.y"
    )
    parsed = fed_parser.parse(sql)
    assert parsed.error is None
    plan = split(parsed)
    assert plan.error is None
    assert len(plan.subqueries) == 2

    by_pid = {sq.provider_id: sq for sq in plan.subqueries}
    assert "source_a" in by_pid
    assert "source_b" in by_pid
    assert by_pid["source_a"].sql.lower().startswith("select")
    assert "t1" in by_pid["source_a"].sql
    assert "t2" in by_pid["source_b"].sql


@pytestmark_sqlglot
def test_pushed_down_filter():
    """Single-source WHERE clause should be pushed into that source's subquery."""
    sql = (
        "SELECT a.x, b.y FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.x = b.y "
        "WHERE a.created > '2026-01-01'"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    assert plan.error is None

    by_pid = {sq.provider_id: sq for sq in plan.subqueries}
    a_sq = by_pid["source_a"]
    b_sq = by_pid["source_b"]

    # source_a should have the pushed filter
    assert len(a_sq.pushed_filters) == 1
    assert "created" in a_sq.pushed_filters[0]
    assert "WHERE" in a_sq.sql
    # source_b should not
    assert b_sq.pushed_filters == []
    assert "WHERE" not in b_sq.sql


@pytestmark_sqlglot
def test_cross_source_filter_kept_in_final():
    """Filter that references both sources stays in final WHERE."""
    sql = (
        "SELECT a.x, b.y FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.x = b.y "
        "WHERE a.amount > b.threshold"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    assert plan.error is None

    # No pushed filters
    for sq in plan.subqueries:
        assert sq.pushed_filters == []
    # Cross-source filter retained for in-memory merge
    assert plan.final_where != ""
    assert "amount" in plan.final_where
    assert "threshold" in plan.final_where


@pytestmark_sqlglot
def test_join_keys_extracted():
    sql = (
        "SELECT a.x, b.y FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.cid = b.id"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    assert len(plan.join_keys) == 1
    jk = plan.join_keys[0]
    providers = {jk.left_provider, jk.right_provider}
    assert providers == {"source_a", "source_b"}
    cols = {jk.left_column, jk.right_column}
    assert cols == {"cid", "id"}


@pytestmark_sqlglot
def test_columns_collected_per_source():
    """SELECT a.x, b.y → only `x` collected for source_a, only `y` for source_b
    (plus join columns)."""
    sql = (
        "SELECT a.x, b.y FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.k = b.k"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)

    by_pid = {sq.provider_id: sq for sq in plan.subqueries}
    a_cols = set(by_pid["source_a"].columns_needed)
    b_cols = set(by_pid["source_b"].columns_needed)
    assert "x" in a_cols
    assert "k" in a_cols  # join key
    assert "y" not in a_cols
    assert "y" in b_cols
    assert "k" in b_cols
    assert "x" not in b_cols


@pytestmark_sqlglot
def test_three_source_join():
    sql = (
        "SELECT a.x, b.y, c.z FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.k = b.k "
        "JOIN source_c.t3 c ON b.j = c.j"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    assert plan.error is None
    pids = {sq.provider_id for sq in plan.subqueries}
    assert pids == {"source_a", "source_b", "source_c"}
    # 2 cross-source joins
    assert len(plan.join_keys) == 2


@pytestmark_sqlglot
def test_aliases_resolved_correctly():
    """Aliases `a` and `b` map to their providers; columns prefixed with
    aliases are routed correctly."""
    sql = (
        "SELECT a.col1, b.col2 FROM source_a.orders AS a "
        "JOIN source_b.customers AS b ON a.cid = b.id "
        "WHERE a.status = 'ok'"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    assert plan.error is None

    by_pid = {sq.provider_id: sq for sq in plan.subqueries}
    a_sq = by_pid["source_a"]
    b_sq = by_pid["source_b"]
    assert "orders" in a_sq.sql
    assert "customers" in b_sq.sql
    # Pushed filter only on source_a
    assert any("status" in f for f in a_sq.pushed_filters)
    assert b_sq.pushed_filters == []


@pytestmark_sqlglot
def test_limit_extracted():
    sql = (
        "SELECT a.x FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.k = b.k LIMIT 100"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    assert plan.final_limit == 100


@pytestmark_sqlglot
def test_order_by_extracted():
    sql = (
        "SELECT a.x FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.k = b.k ORDER BY a.x DESC"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    assert plan.final_order_by != ""
    assert "x" in plan.final_order_by.lower()


@pytestmark_sqlglot
def test_pushdown_with_aliases_and_multiple_filters():
    """Multiple AND filters distributed correctly between sources."""
    sql = (
        "SELECT a.x, b.y FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.k = b.k "
        "WHERE a.created > '2026-01-01' AND b.region = 'NA' AND a.status = 'ok'"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    assert plan.error is None

    by_pid = {sq.provider_id: sq for sq in plan.subqueries}
    a_filters = by_pid["source_a"].pushed_filters
    b_filters = by_pid["source_b"].pushed_filters
    assert len(a_filters) == 2  # created, status
    assert len(b_filters) == 1  # region
    assert any("created" in f for f in a_filters)
    assert any("status" in f for f in a_filters)
    assert any("region" in f for f in b_filters)
    # No cross-source filters
    assert plan.final_where == ""


@pytestmark_sqlglot
def test_self_join_within_same_source():
    """Two refs from the same provider joined together: not a cross-source join,
    so no JoinKey emitted (handled by source's own engine)."""
    sql = (
        "SELECT a.x FROM source_a.t1 a "
        "JOIN source_a.t2 b ON a.k = b.k"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    # Same source on both sides: no cross-source join key
    assert plan.join_keys == []
    pids = {sq.provider_id for sq in plan.subqueries}
    assert pids == {"source_a"}


@pytestmark_sqlglot
def test_final_select_populated():
    sql = (
        "SELECT a.x, b.y FROM source_a.t1 a "
        "JOIN source_b.t2 b ON a.k = b.k"
    )
    parsed = fed_parser.parse(sql)
    plan = split(parsed)
    assert plan.final_select != ""
    # final select references both source columns
    assert "x" in plan.final_select
    assert "y" in plan.final_select
