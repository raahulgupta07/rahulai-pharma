"""Tests for /api/learning/* endpoints. Mocked dependencies.

Tests the inner module functions called by each endpoint, not the FastAPI HTTP
layer. Mocks SQL engine, LLM, and file I/O via unittest.mock.
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub db.session before any subsystem import (Python 3.9 compatibility —
# real module uses 3.10+ union syntax in annotations).
# ---------------------------------------------------------------------------
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

# Stub app.auth so endpoint helpers can `from app.auth import
# check_project_permission` without pulling the real (heavy) module.
# Don't stub `app` itself — let the real package (containing learning_api)
# load. Just register a fake `app.auth` submodule.
if "app.auth" not in sys.modules:
    import importlib
    _app_pkg = importlib.import_module("app")  # real package
    _auth_stub = ModuleType("app.auth")
    _auth_stub.check_project_permission = MagicMock(
        name="check_project_permission_stub", return_value={"role": "viewer"})
    _app_pkg.auth = _auth_stub  # type: ignore[attr-defined]
    sys.modules["app.auth"] = _auth_stub


from fastapi import HTTPException


def _make_engine_with_rows(rows):
    """Helper: build a mock engine whose connect().__enter__().execute() returns rows."""
    fake_engine = MagicMock()
    fake_conn = MagicMock()
    fake_engine.connect.return_value.__enter__.return_value = fake_conn
    fake_engine.connect.return_value.__exit__.return_value = False
    result = MagicMock()
    result.fetchall.return_value = rows
    result.fetchone.return_value = rows[0] if rows else None
    fake_conn.execute.return_value = result
    return fake_engine, fake_conn


def _make_request(user=None):
    """Build a fake FastAPI Request with .state.user."""
    req = MagicMock()
    req.state.user = user
    return req


# ---------------------------------------------------------------------------
# _get_user / _require_super / _check_project helpers
# ---------------------------------------------------------------------------

class TestAuthHelpers:
    def test_get_user_raises_401_when_missing(self):
        from app.learning_api import _get_user

        req = MagicMock()
        req.state.user = None
        with pytest.raises(HTTPException) as ei:
            _get_user(req)
        assert ei.value.status_code == 401

    def test_get_user_returns_user_dict(self):
        from app.learning_api import _get_user

        u = {"user_id": 1, "is_super": True}
        assert _get_user(_make_request(u)) == u

    def test_require_super_raises_403_when_not_super(self):
        from app.learning_api import _require_super
        with pytest.raises(HTTPException) as ei:
            _require_super({"user_id": 1, "is_super": False})
        assert ei.value.status_code == 403

    def test_require_super_passes_when_super(self):
        from app.learning_api import _require_super
        # No exception
        _require_super({"is_super": True})


# ---------------------------------------------------------------------------
# Cycle endpoint
# ---------------------------------------------------------------------------

class TestCycleEndpoint:
    def test_cycle_invalid_user_raises_401(self):
        import asyncio
        from app.learning_api import run_project_cycle

        with pytest.raises(HTTPException) as ei:
            asyncio.run(run_project_cycle("slug", _make_request(None)))
        assert ei.value.status_code == 401

    def test_cycle_requires_editor_perm(self):
        import asyncio
        from app.learning_api import run_project_cycle

        req = _make_request({"user_id": 1, "is_super": False})
        with patch("app.auth.check_project_permission", return_value=None):
            with pytest.raises(HTTPException) as ei:
                asyncio.run(run_project_cycle("slug", req))
        assert ei.value.status_code == 403

    def test_cycle_returns_sse_stream_when_authorized(self):
        import asyncio
        from app.learning_api import run_project_cycle

        req = _make_request({"user_id": 1, "is_super": True})
        req.json = MagicMock(side_effect=Exception("no body"))

        async def empty_run(*a, **kw):
            if False:
                yield

        with patch("app.auth.check_project_permission",
                   return_value={"role": "editor"}):
            with patch("dash.learning.cycle.LearningCycle") as MC:
                MC.return_value.run = empty_run
                resp = asyncio.run(run_project_cycle("slug", req))
        # StreamingResponse from FastAPI
        assert resp.media_type == "text/event-stream"


# ---------------------------------------------------------------------------
# Runs endpoints
# ---------------------------------------------------------------------------

class TestRunsEndpoint:
    def test_runs_requires_super(self):
        from app.learning_api import list_runs

        req = _make_request({"is_super": False})
        with pytest.raises(HTTPException) as ei:
            list_runs(req)
        assert ei.value.status_code == 403

    def test_runs_returns_recent(self):
        from app.learning_api import list_runs

        rows = [(1, "slug-a", 5, "completed", 10, 8, 6, 4, 1, 3, 2,
                 0.05, 30, "2026-05-05T10:00", "2026-05-05T10:30", None)]
        eng, _ = _make_engine_with_rows(rows)
        req = _make_request({"is_super": True})
        with patch("db.session.get_sql_engine", return_value=eng):
            out = list_runs(req, limit=10)
        assert "runs" in out
        assert len(out["runs"]) == 1
        assert out["runs"][0]["project_slug"] == "slug-a"

    def test_runs_filters_by_project(self):
        from app.learning_api import list_project_runs

        rows = [(1, "slug-a", 1, "completed", 5, 4, 3, 2, 0, 1, 1,
                 0.01, 10, None, None, None)]
        eng, conn = _make_engine_with_rows(rows)
        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("db.session.get_sql_engine", return_value=eng):
            out = list_project_runs("slug-a", req, limit=5)
        assert len(out["runs"]) == 1
        # Verify slug param passed
        call_kwargs = conn.execute.call_args[0][1]
        assert call_kwargs["s"] == "slug-a"

    def test_runs_handles_db_error_gracefully(self):
        from app.learning_api import list_runs
        req = _make_request({"is_super": True})
        with patch("db.session.get_sql_engine",
                   side_effect=RuntimeError("db down")):
            out = list_runs(req)
        assert out["runs"] == []
        assert "db down" in out.get("error", "")


# ---------------------------------------------------------------------------
# Questions endpoint
# ---------------------------------------------------------------------------

class TestQuestionsEndpoint:
    def test_questions_returns_pending_only(self):
        from app.learning_api import list_questions

        rows = [(1, "Why X?", "topic", "kg_hole", 80, "pending", 1,
                 "2026-05-05", None)]
        eng, _ = _make_engine_with_rows(rows)
        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("db.session.get_sql_engine", return_value=eng):
            out = list_questions("slug", req)
        assert out["questions"][0]["question"] == "Why X?"
        assert out["questions"][0]["status"] == "pending"

    def test_questions_limit_param_clamped(self):
        from app.learning_api import list_questions

        eng, conn = _make_engine_with_rows([])
        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("db.session.get_sql_engine", return_value=eng):
            list_questions("slug", req, limit=99999)
        # Clamped to 500
        kwargs = conn.execute.call_args[0][1]
        assert kwargs["n"] == 500


# ---------------------------------------------------------------------------
# Hypotheses endpoint
# ---------------------------------------------------------------------------

class TestHypothesesEndpoint:
    def test_hypotheses_returned_with_lineage_fields(self):
        from app.learning_api import list_hypotheses

        rows = [(1, "stmt", "causal", 0.9, "verified", "ml",
                 3, True, "2026-05-05", "2026-05-05")]
        eng, _ = _make_engine_with_rows(rows)
        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("db.session.get_sql_engine", return_value=eng):
            out = list_hypotheses("slug", req)
        h = out["hypotheses"][0]
        assert h["type"] == "causal"
        assert h["confidence"] == 0.9
        assert h["promoted_to_central"] is True
        assert h["triangulation_count"] == 3

    def test_hypotheses_handles_db_error(self):
        from app.learning_api import list_hypotheses

        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("db.session.get_sql_engine", side_effect=Exception("oops")):
            out = list_hypotheses("slug", req)
        assert out["hypotheses"] == []


# ---------------------------------------------------------------------------
# Stats endpoint
# ---------------------------------------------------------------------------

class TestStatsEndpoint:
    def test_stats_returns_cycle_summary(self):
        from app.learning_api import project_stats

        rows = [(5, 30, 10, 8, 4, 0.25, "2026-05-05")]
        eng, _ = _make_engine_with_rows(rows)
        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("db.session.get_sql_engine", return_value=eng), \
             patch("dash.learning.forgetting.stats", return_value={"alive": 100}):
            out = project_stats("slug", req)
        assert out["slug"] == "slug"
        assert out["cycles"]["count"] == 5
        assert out["cycles"]["cost_usd_total"] == 0.25
        assert out["forgetting"] == {"alive": 100}

    def test_stats_handles_forgetting_error(self):
        from app.learning_api import project_stats

        rows = [(0, 0, 0, 0, 0, 0.0, None)]
        eng, _ = _make_engine_with_rows(rows)
        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("db.session.get_sql_engine", return_value=eng), \
             patch("dash.learning.forgetting.stats",
                   side_effect=RuntimeError("nope")):
            out = project_stats("slug", req)
        assert "forgetting_error" in out


# ---------------------------------------------------------------------------
# Cost endpoint
# ---------------------------------------------------------------------------

class TestCostEndpoint:
    def test_cost_returns_status_and_history(self):
        from app.learning_api import get_cost

        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("dash.learning.cost_guard.get_status",
                   return_value={"spent_today": 0.10, "cap": 1.0}), \
             patch("dash.learning.cost_guard.history_7d",
                   return_value=[{"day": "2026-05-04", "spent": 0.05}]):
            out = get_cost("slug", req)
        assert out["slug"] == "slug"
        assert out["status"]["spent_today"] == 0.10
        assert len(out["history_7d"]) == 1

    def test_set_cap_requires_editor(self):
        import asyncio
        from app.learning_api import set_cap

        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission", return_value=None):
            with pytest.raises(HTTPException) as ei:
                asyncio.run(set_cap("slug", req))
        assert ei.value.status_code == 403

    def test_set_cap_writes_value(self):
        import asyncio
        from app.learning_api import set_cap

        req = _make_request({"is_super": False})
        req.json = MagicMock(return_value={"daily_cost_cap_usd": 2.5})
        async def jbody():
            return {"daily_cost_cap_usd": 2.5}
        req.json = jbody

        eng, conn = _make_engine_with_rows([])
        with patch("app.auth.check_project_permission",
                   return_value={"role": "editor"}), \
             patch("db.session.get_sql_engine", return_value=eng):
            out = asyncio.run(set_cap("slug", req))
        assert out["daily_cost_cap_usd"] == 2.5
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Goals endpoint
# ---------------------------------------------------------------------------

class TestGoalsEndpoint:
    def test_get_goals_returns_content(self):
        from app.learning_api import get_goals

        req = _make_request({"is_super": False})
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("dash.learning.goals.read_goals",
                   return_value="# Goals\nsome content"):
            out = get_goals("slug", req)
        assert out["slug"] == "slug"
        assert "Goals" in out["content"]

    def test_post_goals_writes_content(self):
        import asyncio
        from app.learning_api import post_goals

        req = _make_request({"is_super": False})
        async def jbody():
            return {"content": "new goals"}
        req.json = jbody

        with patch("app.auth.check_project_permission",
                   return_value={"role": "editor"}), \
             patch("dash.learning.goals.write_goals",
                   return_value=True) as mw:
            out = asyncio.run(post_goals("slug", req))
        assert out["saved"] is True
        mw.assert_called_once_with("slug", "new goals")


# ---------------------------------------------------------------------------
# IQ endpoint
# ---------------------------------------------------------------------------

class TestIqEndpoint:
    def test_iq_returns_current_and_history(self):
        from app.learning_api import get_iq

        req = _make_request({"is_super": False})
        fake_iq = MagicMock()
        fake_iq.components = {"score": 75}
        with patch("app.auth.check_project_permission",
                   return_value={"role": "viewer"}), \
             patch("dash.learning.agent_iq.compute", return_value=fake_iq), \
             patch("dash.learning.agent_iq.history",
                   return_value=[{"day": "2026-05-05", "score": 75}]):
            out = get_iq("slug", req, days=7)
        assert out["slug"] == "slug"
        assert out["current"]["score"] == 75
        assert out["days"] == 7

    def test_iq_central_requires_super(self):
        from app.learning_api import get_iq_central

        req = _make_request({"is_super": False})
        with pytest.raises(HTTPException) as ei:
            get_iq_central(req)
        assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# Domain endpoints
# ---------------------------------------------------------------------------

class TestDomainEndpoints:
    def test_get_domain_returns_loaded(self):
        from app.learning_api import get_domain

        req = _make_request({"is_super": True})
        with patch("dash.learning.domain_detector.load",
                   return_value={
                       "primary": "retail",
                       "secondaries": ["ecommerce"],
                       "confidence": 0.92,
                       "all_scores": [],
                       "manual_override": False,
                   }):
            out = get_domain("slug", 1, req)
        assert out["detected"] is True
        assert out["primary"] == "retail"

    def test_get_domain_returns_undetected_when_none(self):
        from app.learning_api import get_domain

        req = _make_request({"is_super": True})
        with patch("dash.learning.domain_detector.load", return_value=None):
            out = get_domain("slug", 1, req)
        assert out["detected"] is False
        assert out["primary"] is None

    def test_redetect_calls_detector(self):
        from app.learning_api import redetect_domain

        req = _make_request({"is_super": False})
        fake = MagicMock()
        fake.primary = "finance"
        fake.secondaries = []
        fake.confidence = 0.88
        with patch("app.auth.check_project_permission",
                   return_value={"role": "editor"}), \
             patch("dash.learning.domain_detector.detect", return_value=fake) as md:
            out = redetect_domain("slug", 7, req)
        assert out["primary"] == "finance"
        md.assert_called_once_with("slug", 7)

    def test_list_domains_returns_list(self):
        from app.learning_api import list_domains

        req = _make_request({"is_super": True})
        domains = ["retail", "finance", "healthcare"]
        with patch("dash.learning.domain_detector.all_domains",
                   return_value=domains):
            out = list_domains(req)
        assert out["domains"] == domains


# ---------------------------------------------------------------------------
# Optin projects + decay endpoints
# ---------------------------------------------------------------------------

class TestOptinAndDecay:
    def test_list_optin_returns_slugs(self):
        from app.learning_api import list_optin_projects

        rows = [("p1",), ("p2",), ("p3",)]
        eng, _ = _make_engine_with_rows(rows)
        req = _make_request({"is_super": True})
        with patch("db.session.get_sql_engine", return_value=eng):
            out = list_optin_projects(req)
        assert out["slugs"] == ["p1", "p2", "p3"]

    def test_decay_calls_job(self):
        from app.learning_api import trigger_decay

        req = _make_request({"is_super": True})
        result = MagicMock(decayed_count=12, archived_count=3,
                           unarchived_count=1, deleted_count=0)
        with patch("dash.learning.forgetting.daily_decay_job",
                   return_value=result):
            out = trigger_decay(req)
        assert out["status"] == "ok"
        assert out["decayed"] == 12
        assert out["archived"] == 3

    def test_decay_requires_super(self):
        from app.learning_api import trigger_decay
        req = _make_request({"is_super": False})
        with pytest.raises(HTTPException) as ei:
            trigger_decay(req)
        assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# Scheduler endpoints
# ---------------------------------------------------------------------------

class TestSchedulerEndpoints:
    def test_state_requires_super(self):
        from app.learning_api import scheduler_state
        req = _make_request({"is_super": False})
        with pytest.raises(HTTPException):
            scheduler_state(req)

    def test_disable_calls_scheduler_disable(self):
        from app.learning_api import scheduler_disable
        req = _make_request({"is_super": True})
        with patch("dash.learning.scheduler.disable") as md:
            out = scheduler_disable(req)
        assert out == {"enabled": False}
        md.assert_called_once()

    def test_enable_calls_scheduler_enable(self):
        from app.learning_api import scheduler_enable
        req = _make_request({"is_super": True})
        with patch("dash.learning.scheduler.enable") as me:
            out = scheduler_enable(req)
        assert out == {"enabled": True}
        me.assert_called_once()
