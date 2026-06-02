"""Tests for dash/learning/lineage.py — hypothesis ancestor/descendant tree."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# Stub db.session before imports
if "db.session" not in sys.modules:
    _stub = ModuleType("db.session")
    _stub.get_sql_engine = MagicMock(name="get_sql_engine_stub")
    _db_pkg = sys.modules.setdefault("db", ModuleType("db"))
    _db_pkg.session = _stub
    sys.modules["db.session"] = _stub
    _url_stub = ModuleType("db.url")
    _url_stub.db_url = MagicMock(return_value="postgresql://stub")
    _db_pkg.url = _url_stub
    sys.modules["db.url"] = _url_stub


from dash.learning.lineage import get_lineage, get_root_trees


def _mk_engine_with_rows(rows_by_call: list) -> MagicMock:
    """Build mock engine where engine.connect().__enter__() yields conn,
    and conn.execute() returns results that fetch* per call sequence.

    For each item in rows_by_call:
      - if it's a list: result.fetchall() returns it
      - else: result.fetchone() returns it
    """
    eng = MagicMock(name="Engine")
    conn = MagicMock(name="Conn")
    eng.connect.return_value.__enter__.return_value = conn
    eng.connect.return_value.__exit__.return_value = False

    results = [MagicMock() for _ in rows_by_call]
    for r, row_data in zip(results, rows_by_call):
        if isinstance(row_data, list):
            r.fetchall.return_value = row_data
        else:
            r.fetchone.return_value = row_data
    conn.execute.side_effect = results
    return eng


class TestGetLineage:
    def test_returns_empty_when_id_missing(self):
        eng = _mk_engine_with_rows([None])
        with patch("db.session.get_sql_engine", return_value=eng):
            result = get_lineage(999)
        assert result["self"] is None
        assert result["ancestors"] == []
        assert result["descendants"] == []

    def test_returns_self_when_no_parent(self):
        # First call: SELECT self → row tuple matching schema
        self_row = (42, "test statement", "pattern", 0.85, "verified", None, 0)
        # Second call: SELECT descendants → []
        eng = _mk_engine_with_rows([self_row, []])
        with patch("db.session.get_sql_engine", return_value=eng):
            result = get_lineage(42)
        assert result["self"]["id"] == 42
        assert result["self"]["statement"].startswith("test")
        assert result["ancestors"] == []

    def test_walks_ancestor_chain(self):
        # self has parent_id=10
        self_row = (42, "child", "pattern", 0.7, "pending", 10, 1)
        # ancestor 10 has parent=5
        anc1 = (10, "parent", "pattern", 0.8, "verified", 5, 0)
        # ancestor 5 has no parent (depth 0)
        anc2 = (5, "grandparent", "pattern", 0.9, "verified", None, 0)
        # descendants: empty
        eng = _mk_engine_with_rows([self_row, anc1, anc2, []])
        with patch("db.session.get_sql_engine", return_value=eng):
            result = get_lineage(42)
        assert len(result["ancestors"]) == 2
        assert result["ancestors"][0]["id"] == 10
        assert result["ancestors"][1]["id"] == 5

    def test_caps_ancestor_walk_at_20(self):
        # Self points back to itself in a fake loop
        self_row = (1, "self", "pattern", 0.5, "pending", 1, 0)
        # All ancestor lookups return same row (would loop forever w/o cap)
        eng_rows = [self_row] + [self_row] * 25 + [[]]
        eng = _mk_engine_with_rows(eng_rows)
        with patch("db.session.get_sql_engine", return_value=eng):
            result = get_lineage(1)
        assert len(result["ancestors"]) <= 22  # cap is "> 20" check

    def test_descendants_via_recursive_cte(self):
        self_row = (1, "root", "pattern", 0.9, "verified", None, 0)
        descendants = [
            (2, "child", "pattern", 0.7, "pending", 1, 1, 1),  # 8 cols incl gen
            (3, "grandchild", "pattern", 0.6, "pending", 2, 2, 2),
        ]
        eng = _mk_engine_with_rows([self_row, descendants])
        with patch("db.session.get_sql_engine", return_value=eng):
            result = get_lineage(1)
        assert len(result["descendants"]) == 2
        assert result["descendants"][0]["generation"] == 1
        assert result["descendants"][1]["generation"] == 2

    def test_handles_db_exception(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("boom")
        with patch("db.session.get_sql_engine", return_value=eng):
            result = get_lineage(1)
        # Should not raise
        assert result["self"] is None

    def test_self_dict_truncates_long_statement(self):
        long = "x" * 1000
        self_row = (1, long, "pattern", 0.5, "pending", None, 0)
        eng = _mk_engine_with_rows([self_row, []])
        with patch("db.session.get_sql_engine", return_value=eng):
            result = get_lineage(1)
        assert len(result["self"]["statement"]) <= 300


class TestGetRootTrees:
    def test_returns_empty_for_missing_project(self):
        eng = _mk_engine_with_rows([[]])
        with patch("db.session.get_sql_engine", return_value=eng):
            roots = get_root_trees("missing_slug")
        assert roots == []

    def test_returns_root_hypotheses_with_child_count(self):
        rows = [
            (1, "root statement 1", 0.9, "verified", 3),
            (2, "root statement 2", 0.7, "pending", 0),
        ]
        eng = _mk_engine_with_rows([rows])
        with patch("db.session.get_sql_engine", return_value=eng):
            roots = get_root_trees("test_proj", limit=10)
        assert len(roots) == 2
        assert roots[0]["id"] == 1
        assert roots[0]["child_count"] == 3
        assert roots[1]["child_count"] == 0

    def test_truncates_long_statements(self):
        long_stmt = "x" * 500
        rows = [(1, long_stmt, 0.5, "pending", 0)]
        eng = _mk_engine_with_rows([rows])
        with patch("db.session.get_sql_engine", return_value=eng):
            roots = get_root_trees("test")
        assert len(roots[0]["statement"]) <= 200

    def test_returns_empty_on_db_error(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("nope")
        with patch("db.session.get_sql_engine", return_value=eng):
            roots = get_root_trees("test")
        assert roots == []

    def test_respects_limit_parameter(self):
        rows = [(i, f"stmt {i}", 0.5, "pending", 0) for i in range(2)]
        eng = _mk_engine_with_rows([rows])
        with patch("db.session.get_sql_engine", return_value=eng):
            roots = get_root_trees("test", limit=2)
        assert isinstance(roots, list)
        assert len(roots) == 2
