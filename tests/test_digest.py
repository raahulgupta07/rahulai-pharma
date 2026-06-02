"""Tests for dash/learning/digest.py — today's discoveries summary."""
from __future__ import annotations

import os
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


from dash.learning.digest import (
    Digest,
    _fallback_summary,
    _llm_summary,
    _notify_slack,
    generate,
    list_recent,
)


class TestFallbackSummary:
    def test_no_findings(self):
        d = Digest(
            project_slug="x", cycle_num=1, run_id=1,
            verified_count=0, hypotheses_count=0, cost_usd=0.0,
        )
        s = _fallback_summary(d)
        assert "No new findings" in s

    def test_with_findings_uses_top_hypothesis(self):
        d = Digest(
            project_slug="x", cycle_num=1, run_id=1,
            verified_count=2, hypotheses_count=5, cost_usd=0.05,
        )
        d.highlights = [
            {"statement": "X drives Y", "type": "causal", "confidence": 0.9},
        ]
        s = _fallback_summary(d)
        assert "verified 2" in s.lower()
        assert "X drives Y" in s

    def test_includes_cost(self):
        d = Digest(
            project_slug="x", cycle_num=1, run_id=1,
            verified_count=0, hypotheses_count=0, cost_usd=0.123,
        )
        s = _fallback_summary(d)
        assert "0.123" in s


class TestLlmSummary:
    def test_returns_fallback_when_no_llm_response(self):
        d = Digest(project_slug="x", cycle_num=1, run_id=1)
        d.highlights = [
            {"statement": "test", "type": "pattern", "confidence": 0.9}
        ]
        d.verified_count = 1
        d.hypotheses_count = 1
        result = _llm_summary(d, lambda p, task=None: "")
        # falls through to _fallback_summary, so we get a non-empty string
        assert result
        assert isinstance(result, str)

    def test_uses_llm_when_response_present(self):
        d = Digest(project_slug="x", cycle_num=1, run_id=1)
        d.highlights = [
            {"statement": "test", "type": "pattern", "confidence": 0.9}
        ]
        d.verified_count = 1
        d.hypotheses_count = 1
        result = _llm_summary(d, lambda p, task=None: "Today we learned X.")
        assert "Today we learned" in result

    def test_handles_llm_exception(self):
        d = Digest(project_slug="x", cycle_num=1, run_id=1)
        d.highlights = [
            {"statement": "test", "type": "pattern", "confidence": 0.9}
        ]

        def bad_llm(p, task=None):
            raise RuntimeError("api down")

        result = _llm_summary(d, bad_llm)
        # Should fall through to fallback rather than raise
        assert isinstance(result, str)


class TestGenerate:
    def test_persists_digest(self):
        eng = MagicMock()
        conn = MagicMock()
        eng.connect.return_value.__enter__.return_value = conn
        eng.connect.return_value.__exit__.return_value = False
        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        result_mock.fetchone.return_value = (5, 3, 0.05, {})
        conn.execute.return_value = result_mock

        with patch("db.session.get_sql_engine", return_value=eng):
            digest = generate("test_slug", cycle_num=1, run_id=1, llm_call_fn=None)
        assert digest.project_slug == "test_slug"
        assert digest.cycle_num == 1

    def test_handles_db_failure_gracefully(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("db down")
        with patch("db.session.get_sql_engine", return_value=eng):
            digest = generate("test", 1, 1)
        assert digest.project_slug == "test"


class TestListRecent:
    def test_returns_empty_on_db_error(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("boom")
        with patch("db.session.get_sql_engine", return_value=eng):
            result = list_recent("test")
        assert result == []

    def test_returns_serialized_digests(self):
        from datetime import datetime

        rows = [
            (1, 5, "Today we learned X", 10, 7, 0.5, 250.0, datetime.utcnow()),
        ]
        eng = MagicMock()
        conn = MagicMock()
        eng.connect.return_value.__enter__.return_value = conn
        eng.connect.return_value.__exit__.return_value = False
        rmock = MagicMock()
        rmock.fetchall.return_value = rows
        conn.execute.return_value = rmock
        with patch("db.session.get_sql_engine", return_value=eng):
            result = list_recent("test", limit=10)
        assert len(result) == 1
        assert result[0]["cycle_num"] == 5
        assert result[0]["agent_iq"] == 250.0


class TestSlackNotify:
    def test_skipped_when_no_webhook_env(self):
        d = Digest(project_slug="x", cycle_num=1, run_id=1, summary="test")
        os.environ.pop("SLACK_LEARNING_WEBHOOK", None)
        _notify_slack(d)
        assert "slack" not in d.notified_via

    def test_skipped_when_summary_empty(self):
        d = Digest(project_slug="x", cycle_num=1, run_id=1, summary="")
        os.environ["SLACK_LEARNING_WEBHOOK"] = "https://example/webhook"
        try:
            _notify_slack(d)
            assert "slack" not in d.notified_via
        finally:
            os.environ.pop("SLACK_LEARNING_WEBHOOK", None)
