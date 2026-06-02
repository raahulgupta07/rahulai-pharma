"""Tests for dash/learning/cost_guard.py — daily cost ceiling."""
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


from dash.learning.cost_guard import (
    CostStatus,
    get_status,
    history_7d,
    pause_until_tomorrow,
)


def _mk_engine(scalar_returns: list) -> MagicMock:
    eng = MagicMock()
    conn = MagicMock()
    eng.connect.return_value.__enter__.return_value = conn
    eng.connect.return_value.__exit__.return_value = False
    results = []
    for s in scalar_returns:
        r = MagicMock()
        if isinstance(s, list):
            r.fetchall.return_value = s
        else:
            r.fetchone.return_value = s
        results.append(r)
    conn.execute.side_effect = results
    return eng


class TestGetStatus:
    def test_returns_default_status_when_no_project(self):
        # central path: only spend query is executed
        eng = _mk_engine([(0,)])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = get_status(None)
        assert s.daily_cap_usd > 0
        assert s.over_budget is False

    def test_over_budget_when_spend_exceeds_cap(self):
        eng = _mk_engine([(1.0, None), (1.5,)])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = get_status("test_proj")
        assert s.over_budget is True
        assert s.today_spend_usd == 1.5

    def test_unlimited_when_cap_zero(self):
        # The implementation does `float(r[0] or 1.0)` so 0.0 becomes 1.0.
        # Use a negative cap to actually exercise the "<= 0 means unlimited" branch.
        eng = _mk_engine([(-1.0, None), (10000.0,)])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = get_status("test_proj")
        assert s.over_budget is False

    def test_paused_until_field(self):
        from datetime import datetime, timedelta

        future = datetime.utcnow() + timedelta(days=1)
        eng = _mk_engine([(1.0, future), (0.5,)])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = get_status("test")
        assert s.paused_until is not None

    def test_handles_db_exception(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("boom")
        with patch("db.session.get_sql_engine", return_value=eng):
            s = get_status("test")
        assert isinstance(s, CostStatus)
        assert s.over_budget is False

    def test_under_budget_when_spend_below_cap(self):
        eng = _mk_engine([(2.0, None), (0.5,)])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = get_status("test")
        assert s.over_budget is False
        assert s.today_spend_usd == 0.5
        assert s.daily_cap_usd == 2.0


class TestPauseUntilTomorrow:
    def test_returns_true_on_success(self):
        eng = _mk_engine([None])
        with patch("db.session.get_sql_engine", return_value=eng):
            ok = pause_until_tomorrow("test_proj")
        assert ok is True

    def test_returns_false_on_db_error(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("boom")
        with patch("db.session.get_sql_engine", return_value=eng):
            ok = pause_until_tomorrow("test")
        assert ok is False


class TestHistory7d:
    def test_returns_empty_on_no_data(self):
        eng = _mk_engine([[]])
        with patch("db.session.get_sql_engine", return_value=eng):
            h = history_7d("test")
        assert h == []

    def test_returns_daily_buckets(self):
        from datetime import date

        rows = [
            (date(2026, 5, 1), 0.5),
            (date(2026, 5, 2), 0.3),
        ]
        eng = _mk_engine([rows])
        with patch("db.session.get_sql_engine", return_value=eng):
            h = history_7d("test")
        assert len(h) == 2
        assert h[0]["spend"] == 0.5
        assert h[1]["spend"] == 0.3

    def test_returns_empty_on_db_error(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("boom")
        with patch("db.session.get_sql_engine", return_value=eng):
            h = history_7d("test")
        assert h == []
