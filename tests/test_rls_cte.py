"""CTE / UNION / subquery scoping tests for the RLS rewriter.

Regression coverage for the documented "CTE double-injection" bug: when a
WITH-clause defines `x AS (SELECT * FROM sales)` and the outer SELECT
references `x`, the filter on `sales` must appear ONCE inside the CTE inner
SELECT — never re-injected on the outer SELECT (which references the CTE
alias, not a base table).
"""
import pytest
from unittest.mock import patch

sqlglot = pytest.importorskip("sqlglot")
from dash.rls import rewriter  # noqa: E402


def _cfg(filters, keys=None):
    return {
        "enabled": True,
        "mode": "rewrite",
        "user_attr_keys": keys or ["store_id"],
        "table_filters": filters,
        "default_deny": False,
    }


def _rw(filters, sql, attrs, keys=None):
    with patch.object(rewriter, "_load_config", return_value=_cfg(filters, keys)):
        return rewriter.rewrite(sql, "test", attrs)


def test_cte_filter_applied_once_on_inner():
    """Filter goes inside the CTE definition exactly once; outer SELECT
    referencing the alias is NOT re-filtered."""
    out = _rw(
        {"sales": "store_id = :store_id"},
        "WITH x AS (SELECT * FROM sales) SELECT * FROM x",
        {"store_id": 1},
    )
    assert out.lower().count("store_id = 1") == 1
    # Filter must live before the outer SELECT (i.e., inside the CTE body).
    assert out.lower().index("store_id = 1") < out.lower().rindex("from x")


def test_cte_alias_collision_with_filter_key_not_filtered():
    """If a CTE happens to be named the same as a filtered table, the outer
    reference to the alias must still be skipped (it's not the base table)."""
    out = _rw(
        {"sales": "store_id = :store_id"},
        "WITH sales AS (SELECT * FROM other) SELECT * FROM sales",
        {"store_id": 1},
    )
    # No filter at all: inner table is `other` (no filter); outer `sales` is
    # actually the CTE alias, not the base table.
    assert "store_id" not in out.lower()


def test_subquery_in_from_filtered_once():
    """Inline subquery in FROM: filter applied once, on the inner SELECT."""
    out = _rw(
        {"sales": "store_id = :store_id"},
        "SELECT * FROM (SELECT * FROM sales) _v",
        {"store_id": 1},
    )
    assert out.lower().count("store_id = 1") == 1


def test_union_each_branch_filtered():
    """UNION: each branch is its own SELECT, each gets the filter."""
    out = _rw(
        {"sales": "store_id = :store_id"},
        "SELECT id FROM sales UNION SELECT id FROM sales",
        {"store_id": 1},
    )
    assert out.lower().count("store_id = 1") == 2


def test_cte_with_multiple_inner_tables():
    """CTE inner uses two filtered tables; both filters land inside the CTE."""
    out = _rw(
        {"sales": "store_id = :store_id", "inv": "store_id = :store_id"},
        "WITH j AS (SELECT * FROM sales s JOIN inv i ON s.id=i.sid) SELECT * FROM j",
        {"store_id": 7},
    )
    low = out.lower()
    # Filters appear inside the CTE (before the outer FROM j).
    assert low.count("store_id = 7") == 2
    assert low.rindex("store_id = 7") < low.rindex("from j")
