"""Tests for federated_query self-correction retry loop."""
from __future__ import annotations
from unittest.mock import patch, MagicMock

import pytest

from dash.tools import federated_query as fq
from dash.tools.federated_query import (
    _relax_filter,
    _drop_problem_column,
    _strip_aliases,
    _try_alt_join_key,
    _self_correct,
)


# ---------- _relax_filter ----------

def test_relax_filter_removes_last_and():
    sql = "SELECT * FROM a.t WHERE x = 1 AND y = 2"
    out = _relax_filter(sql, 1)
    assert "AND y = 2" not in out
    assert "x = 1" in out


def test_relax_filter_no_change_when_single_clause():
    sql = "SELECT * FROM a.t WHERE x = 1"
    out = _relax_filter(sql, 1)
    assert out == sql


# ---------- _drop_problem_column ----------

def test_drop_problem_column_extracts_from_error():
    sql = "SELECT a.x, a.bogus, a.y FROM a.t"
    err = "column 'bogus' not found"
    out = _drop_problem_column(sql, err)
    assert "bogus" not in out
    assert "a.x" in out
    assert "a.y" in out


def test_drop_problem_column_no_match():
    sql = "SELECT a.x, a.y FROM a.t"
    err = "syntax error near WHERE"
    out = _drop_problem_column(sql, err)
    assert out == sql


# ---------- _strip_aliases ----------

def test_strip_aliases():
    sql = "SELECT * FROM a.t AS alias1 JOIN b.t AS alias2 ON 1=1"
    out = _strip_aliases(sql)
    assert " AS alias1" not in out
    assert " AS alias2" not in out
    assert "FROM a.t" in out


# ---------- _try_alt_join_key ----------

def test_try_alt_join_key_uses_semantic_union():
    sql = "SELECT * FROM a.t1 JOIN b.t2 ON t1.id = t2.fid"

    fake_js = MagicMock()
    fake_js.confidence = 0.9
    fake_js.left_table = "t1"
    fake_js.left_column = "uuid"
    fake_js.right_table = "t2"
    fake_js.right_column = "uuid"

    fake_catalog = MagicMock()
    fake_catalog.join_suggestions = [fake_js]

    with patch("dash.providers.federation.semantic_union.build",
               return_value=fake_catalog):
        out = _try_alt_join_key(sql, "myproj")
    assert out is not None
    assert "uuid" in out


def test_try_alt_join_key_returns_none_on_low_confidence():
    sql = "SELECT * FROM a.t1 JOIN b.t2 ON t1.id = t2.fid"

    fake_js = MagicMock()
    fake_js.confidence = 0.3
    fake_js.left_table = "t1"
    fake_js.left_column = "uuid"
    fake_js.right_table = "t2"
    fake_js.right_column = "uuid"

    fake_catalog = MagicMock()
    fake_catalog.join_suggestions = [fake_js]

    with patch("dash.providers.federation.semantic_union.build",
               return_value=fake_catalog):
        out = _try_alt_join_key(sql, "myproj")
    assert out is None


# ---------- _self_correct ----------

def _ok_result(text="ok-data"):
    return {
        "status": "ok",
        "result": text,
        "exec_errors": {},
        "merge_warnings": [],
        "row_count": 5,
        "sources": ["a", "b"],
        "fail_reason": "",
    }


def _zero_result():
    return {
        "status": "zero_rows",
        "result": "FEDERATION RESULT: 0 rows",
        "exec_errors": {},
        "merge_warnings": [],
        "row_count": 0,
        "sources": ["a", "b"],
        "fail_reason": "zero rows returned",
    }


def test_self_correct_returns_ok_on_first_attempt():
    sql = "SELECT * FROM a.t WHERE x = 1"
    with patch.object(fq, "_attempt_federated", return_value=_ok_result("hello")) as m:
        out = _self_correct(sql, "p", "analyst", None)
    assert out == "hello"
    assert m.call_count == 1


def test_self_correct_retries_on_zero_rows():
    sql = "SELECT * FROM a.t WHERE x = 1 AND y = 2"
    results = [_zero_result(), _ok_result("recovered")]

    with patch.object(fq, "_attempt_federated", side_effect=results) as m:
        out = _self_correct(sql, "p", "analyst", None)
    assert "recovered" in out
    assert "CORRECTED after 2 attempts" in out
    assert m.call_count == 2


def test_self_correct_returns_failure_after_3_attempts():
    sql = "SELECT * FROM a.t WHERE x = 1 AND y = 2 AND z = 3"
    # Always zero rows, _relax_filter will keep stripping until single clause.
    with patch.object(fq, "_attempt_federated", return_value=_zero_result()):
        out = _self_correct(sql, "p", "analyst", None)
    assert "FEDERATION FAILED" in out
    assert "final error" in out


def test_correction_log_appears_in_result():
    sql = "SELECT * FROM a.t WHERE x = 1 AND y = 2"
    results = [_zero_result(), _ok_result("data-here")]

    with patch.object(fq, "_attempt_federated", side_effect=results):
        out = _self_correct(sql, "p", "analyst", None)
    assert "relaxed filter" in out
    assert "data-here" in out


def test_self_correct_unrecoverable_status_breaks_immediately():
    sql = "SELECT * FROM a.t"
    bad = {
        "status": "parse_error",
        "result": "",
        "exec_errors": {},
        "merge_warnings": [],
        "row_count": 0,
        "sources": [],
        "fail_reason": "syntax error",
    }
    with patch.object(fq, "_attempt_federated", return_value=bad) as m:
        out = _self_correct(sql, "p", "analyst", None)
    assert "FEDERATION FAILED" in out
    assert "syntax error" in out
    assert m.call_count == 1


def test_self_correct_audit_log_only_on_success():
    sql = "SELECT * FROM a.t WHERE x = 1"
    ok = _ok_result("done")
    ok["_audit"] = {"sql": sql, "sources": ["a"], "row_count": 1, "duration_ms": 5}

    with patch.object(fq, "_attempt_federated", return_value=ok), \
         patch.object(fq, "_audit_federated") as audit_mock:
        _self_correct(sql, "p", "analyst", None)
    assert audit_mock.called

    # Failure path: no audit
    with patch.object(fq, "_attempt_federated", return_value=_zero_result()), \
         patch.object(fq, "_audit_federated") as audit_mock2:
        _self_correct("SELECT * FROM a.t", "p", "analyst", None)
    assert not audit_mock2.called
