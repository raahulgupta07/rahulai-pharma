"""Tests for dash/providers/federation/circuit_breaker.py."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from types import ModuleType
from unittest.mock import MagicMock, patch


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


from dash.providers.federation.circuit_breaker import (
    CircuitState,
    FAILURE_THRESHOLD,
    OPEN_DURATION_S,
    check,
    record_failure,
    record_success,
    reset,
)


def _mk_engine(scalar_returns: list) -> MagicMock:
    eng = MagicMock()
    conn = MagicMock()
    eng.connect.return_value.__enter__.return_value = conn
    eng.connect.return_value.__exit__.return_value = False
    results = []
    for s in scalar_returns:
        r = MagicMock()
        r.fetchone.return_value = s
        results.append(r)
    conn.execute.side_effect = results
    return eng


class TestCheck:
    def test_check_returns_closed_state_when_no_record(self):
        eng = _mk_engine([None])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = check("missing_proj")
        assert isinstance(s, CircuitState)
        assert s.project_slug == "missing_proj"
        assert s.consecutive_failures == 0
        assert s.is_open is False
        assert s.open_until is None

    def test_open_circuit_check_returns_is_open_true(self):
        future = datetime.utcnow() + timedelta(minutes=5)
        eng = _mk_engine([(3, future, "boom")])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = check("p1")
        assert s.is_open is True
        assert s.consecutive_failures == 3
        assert s.last_error == "boom"

    def test_open_until_in_future_blocks(self):
        future = datetime.utcnow() + timedelta(seconds=60)
        eng = _mk_engine([(5, future, "err")])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = check("p1")
        assert s.is_open is True

    def test_open_until_elapsed_auto_closes(self):
        past = datetime.utcnow() - timedelta(minutes=10)
        eng = _mk_engine([(3, past, "old_err")])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = check("p1")
        # Cooldown elapsed → not open anymore (half-open / closed)
        assert s.is_open is False
        assert s.consecutive_failures == 3
        assert s.last_error == "old_err"

    def test_check_handles_db_error_gracefully(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("boom")
        with patch("db.session.get_sql_engine", return_value=eng):
            s = check("p1")
        assert isinstance(s, CircuitState)
        assert s.is_open is False
        assert s.consecutive_failures == 0


class TestRecordFailure:
    def test_record_failure_increments_counter(self):
        # First call: check() returns no row (count=0). Then INSERT...UPDATE.
        eng = _mk_engine([None, None])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = record_failure("p1", "oops")
        assert s.consecutive_failures == 1
        assert s.is_open is False

    def test_threshold_opens_circuit(self):
        # check() returns 2 prior failures, this is the 3rd (== threshold)
        eng = _mk_engine([(2, None, None), None])
        with patch("db.session.get_sql_engine", return_value=eng):
            s = record_failure("p1", "err3", threshold=3, open_duration_s=300)
        assert s.consecutive_failures == 3
        assert s.is_open is True
        assert s.open_until is not None
        assert s.last_error == "err3"

    def test_record_failure_handles_db_error_gracefully(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("db down")
        with patch("db.session.get_sql_engine", return_value=eng):
            s = record_failure("p1", "x")
        assert isinstance(s, CircuitState)
        # No crash; counter at least reflects local increment
        assert s.consecutive_failures >= 1


class TestRecordSuccess:
    def test_record_success_resets_counter(self):
        eng = _mk_engine([None])
        with patch("db.session.get_sql_engine", return_value=eng):
            record_success("p1")
        # Verify execute was called with reset values
        conn = eng.connect.return_value.__enter__.return_value
        assert conn.execute.called
        assert conn.commit.called

    def test_record_success_handles_db_error_gracefully(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("db down")
        with patch("db.session.get_sql_engine", return_value=eng):
            # should not raise
            record_success("p1")


class TestReset:
    def test_reset_clears_state(self):
        eng = _mk_engine([None])
        with patch("db.session.get_sql_engine", return_value=eng):
            ok = reset("p1")
        assert ok is True


class TestConstants:
    def test_threshold_is_three(self):
        assert FAILURE_THRESHOLD == 3

    def test_open_duration_is_five_min(self):
        assert OPEN_DURATION_S == 300
