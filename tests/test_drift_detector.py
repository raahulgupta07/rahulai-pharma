"""Tests for dash/learning/drift_detector.py — per-source drift detection."""
from __future__ import annotations

import json
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


from dash.learning.drift_detector import (
    DriftEvent,
    SEVERITY_RANK,
    _check_ndv_drift,
    _check_row_count_drift,
    _check_watermark_stale,
    _diff_schema,
    _persist_event,
    _schema_hash,
    acknowledge,
    list_open_count,
    list_recent,
)


def _mk_engine_with_rows(rows_by_call: list) -> MagicMock:
    """Mock engine.connect() returns conn whose execute() yields per-call results.

    For each item:
      - list -> result.fetchall() returns it
      - else -> result.fetchone() returns it
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


# ---------------------------------------------------------------------------
# Schema hash + diff
# ---------------------------------------------------------------------------

class TestSchemaHash:
    def test_schema_hash_is_deterministic(self):
        cat = {"columns": {"orders": ["id", "total", "status"], "users": ["id", "email"]}}
        h1 = _schema_hash(cat)
        h2 = _schema_hash(cat)
        assert h1 == h2
        assert len(h1) == 16  # truncated sha256

    def test_schema_hash_order_invariant(self):
        a = {"columns": {"t": ["a", "b", "c"]}}
        b = {"columns": {"t": ["c", "b", "a"]}}
        assert _schema_hash(a) == _schema_hash(b)

    def test_schema_hash_handles_dict_cols(self):
        cat = {"columns": {"t": [{"name": "a"}, {"column_name": "b"}]}}
        h = _schema_hash(cat)
        assert isinstance(h, str) and len(h) == 16


class TestDiffSchema:
    def test_diff_schema_detects_added_cols(self):
        old = {"orders": ["id", "total"]}
        new = {"columns": {"orders": ["id", "total", "discount"]}}
        added, removed = _diff_schema(old, new)
        assert added == {"orders": ["discount"]}
        assert removed == {}

    def test_diff_schema_detects_removed_cols(self):
        old = {"orders": ["id", "total", "tax"]}
        new = {"columns": {"orders": ["id", "total"]}}
        added, removed = _diff_schema(old, new)
        assert removed == {"orders": ["tax"]}
        assert added == {}

    def test_diff_schema_no_changes(self):
        old = {"orders": ["id", "total"]}
        new = {"columns": {"orders": ["total", "id"]}}
        added, removed = _diff_schema(old, new)
        assert added == {} and removed == {}


# ---------------------------------------------------------------------------
# NDV drift
# ---------------------------------------------------------------------------

class TestNdvDrift:
    def test_check_ndv_drift_no_baseline_skips(self, tmp_path, monkeypatch):
        # No dimensions dir → no events
        monkeypatch.chdir(tmp_path)
        evs = _check_ndv_drift("slug", 1, baseline={}, threshold_pct=0.20)
        assert evs == []

    def test_check_ndv_drift_emits_when_threshold_exceeded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dim_dir = tmp_path / "knowledge" / "slug" / "source_1" / "dimensions"
        dim_dir.mkdir(parents=True)
        # current NDV = 200 (200 freq entries)
        (dim_dir / "orders.json").write_text(json.dumps({
            "status": [["s" + str(i), 1] for i in range(200)],
        }))
        baseline = {"ndv_snapshot": {"orders": {"status": 100}}}  # 100% jump
        evs = _check_ndv_drift("slug", 1, baseline=baseline, threshold_pct=0.20)
        assert len(evs) == 1
        e = evs[0]
        assert e.drift_type == "ndv"
        assert e.table_name == "orders"
        assert e.column_name == "status"
        assert e.details["old_ndv"] == 100
        assert e.details["new_ndv"] == 200

    def test_check_ndv_drift_below_threshold_skips(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dim_dir = tmp_path / "knowledge" / "slug" / "source_1" / "dimensions"
        dim_dir.mkdir(parents=True)
        (dim_dir / "orders.json").write_text(json.dumps({
            "status": [["s" + str(i), 1] for i in range(105)],
        }))
        baseline = {"ndv_snapshot": {"orders": {"status": 100}}}  # 5% jump
        evs = _check_ndv_drift("slug", 1, baseline=baseline, threshold_pct=0.20)
        assert evs == []


# ---------------------------------------------------------------------------
# Row count drift
# ---------------------------------------------------------------------------

class TestRowCountDrift:
    def test_check_row_count_drift_emits_critical(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        prof = tmp_path / "knowledge" / "slug" / "source_1" / "profile"
        prof.mkdir(parents=True)
        (prof / "orders.json").write_text(json.dumps({
            "id": {"count": 5000},  # 5x baseline → pct=4.0 > 2.0 → high
        }))
        baseline = {"row_counts": {"orders": 1000}}
        evs = _check_row_count_drift("slug", 1, baseline=baseline,
                                       threshold_pct=0.50)
        assert len(evs) == 1
        e = evs[0]
        assert e.drift_type == "row_count"
        assert e.severity == "high"
        assert e.details["old_count"] == 1000
        assert e.details["new_count"] == 5000

    def test_check_row_count_drift_below_threshold_skips(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        prof = tmp_path / "knowledge" / "slug" / "source_1" / "profile"
        prof.mkdir(parents=True)
        (prof / "orders.json").write_text(json.dumps({
            "id": {"count": 1100},  # 10% change
        }))
        baseline = {"row_counts": {"orders": 1000}}
        evs = _check_row_count_drift("slug", 1, baseline=baseline,
                                       threshold_pct=0.50)
        assert evs == []


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------

class TestWatermarkDrift:
    def test_check_watermark_stale_emits_when_old(self):
        # 30 days old → stale
        from datetime import datetime, timedelta
        old_ts = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
        watermark = {"observed_at": old_ts, "col": "updated_at", "value": "x"}
        evs = _check_watermark_stale("slug", 1, watermark, stale_days=7)
        assert len(evs) == 1
        assert evs[0].drift_type == "watermark"
        assert evs[0].severity == "critical"  # 30 >= 7*3
        assert evs[0].details["days_stale"] >= 29

    def test_check_watermark_fresh_no_event(self):
        from datetime import datetime
        watermark = {"observed_at": datetime.utcnow().isoformat() + "Z"}
        evs = _check_watermark_stale("slug", 1, watermark, stale_days=7)
        assert evs == []

    def test_check_watermark_no_observed_at_skips(self):
        evs = _check_watermark_stale("slug", 1, {}, stale_days=7)
        assert evs == []


# ---------------------------------------------------------------------------
# Persistence + queries
# ---------------------------------------------------------------------------

class TestPersistEvent:
    def test_persist_event_inserts_row(self):
        eng = _mk_engine_with_rows([None])
        ev = DriftEvent(
            project_slug="slug", source_id=1,
            drift_type="schema", severity="critical",
            table_name="orders", column_name="discount",
            details={"action": "removed"},
        )
        with patch("db.session.get_sql_engine", return_value=eng):
            _persist_event(ev)
        # Should have called execute once with INSERT
        conn = eng.connect.return_value.__enter__.return_value
        assert conn.execute.called
        args, _ = conn.execute.call_args
        # First positional is a TextClause; check params dict has slug
        params = conn.execute.call_args[0][1]
        assert params["slug"] == "slug"
        assert params["sev"] == "critical"
        assert params["type"] == "schema"


class TestListRecent:
    def test_list_recent_filters_by_status(self):
        rows = [
            (1, 5, "schema", "critical", "orders", "discount",
             {"action": "removed"}, "open", None),
        ]
        eng = _mk_engine_with_rows([rows])
        with patch("db.session.get_sql_engine", return_value=eng):
            out = list_recent("slug", status="open", limit=10)
        assert len(out) == 1
        assert out[0]["drift_type"] == "schema"
        assert out[0]["severity"] == "critical"
        # Verify status param was passed
        conn = eng.connect.return_value.__enter__.return_value
        params = conn.execute.call_args[0][1]
        assert params["status"] == "open"


class TestAcknowledge:
    def test_acknowledge_updates_status(self):
        eng = _mk_engine_with_rows([None])
        with patch("db.session.get_sql_engine", return_value=eng):
            ok = acknowledge(42, user_id=7)
        assert ok is True
        conn = eng.connect.return_value.__enter__.return_value
        params = conn.execute.call_args[0][1]
        assert params["uid"] == 7
        assert params["id"] == 42


class TestListOpenCount:
    def test_list_open_count_returns_int(self):
        eng = _mk_engine_with_rows([(7,)])
        with patch("db.session.get_sql_engine", return_value=eng):
            n = list_open_count("slug")
        assert n == 7

    def test_list_open_count_handles_none_row(self):
        eng = _mk_engine_with_rows([None])
        with patch("db.session.get_sql_engine", return_value=eng):
            n = list_open_count("slug")
        assert n == 0


class TestSeverityRank:
    def test_severity_rank_ordering(self):
        assert SEVERITY_RANK["low"] < SEVERITY_RANK["med"]
        assert SEVERITY_RANK["med"] < SEVERITY_RANK["high"]
        assert SEVERITY_RANK["high"] < SEVERITY_RANK["critical"]
