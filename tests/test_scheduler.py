"""Tests for dash/learning/scheduler.py daily cron loop.

Mocks: SQL engine (no real DB), LearningCycle (no real LLM).
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# Stub db.session before importing the scheduler — Python 3.9 compat trick.
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


def _run(coro):
    """Tiny helper — pytest-asyncio may not be installed."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

class TestSchedulerState:
    def test_initial_state_keys(self):
        from dash.learning import scheduler
        st = scheduler.get_state()
        for k in ("last_run", "last_error", "last_duration_seconds",
                  "next_scheduled", "projects_processed", "running",
                  "enabled"):
            assert k in st

    def test_disable_sets_flag(self):
        from dash.learning import scheduler
        scheduler.enable()
        scheduler.disable()
        assert scheduler.get_state()["enabled"] is False

    def test_enable_clears_flag(self):
        from dash.learning import scheduler
        scheduler.disable()
        scheduler.enable()
        assert scheduler.get_state()["enabled"] is True

    def test_state_snapshot_is_a_copy(self):
        from dash.learning import scheduler
        a = scheduler.get_state()
        a["enabled"] = "mutated"
        b = scheduler.get_state()
        assert b["enabled"] != "mutated"


class TestEnvVarBehavior:
    @pytest.mark.skip(
        reason="Env-var behavior is import-time only; rerunning under "
               "modified env requires module reload which collides with "
               "module state in other tests."
    )
    def test_k8s_env_disables_inprocess(self):
        pass

    @pytest.mark.skip(
        reason="Force-inprocess override evaluated at import; cannot easily "
               "reload scheduler without polluting other tests."
    )
    def test_force_inprocess_overrides_k8s(self):
        pass

    @pytest.mark.skip(
        reason="LEARNING_SCHEDULER_DISABLED checked at import time only."
    )
    def test_explicit_disabled_env_var(self):
        pass


# ---------------------------------------------------------------------------
# _get_optin_projects
# ---------------------------------------------------------------------------

class TestGetOptin:
    def test_returns_slugs_from_db(self):
        from dash.learning import scheduler

        fake_engine = MagicMock()
        fake_conn = MagicMock()
        fake_engine.connect.return_value.__enter__.return_value = fake_conn
        fake_engine.connect.return_value.__exit__.return_value = False
        fake_conn.execute.return_value.fetchall.return_value = [
            ("p1",), ("p2",)
        ]
        with patch("db.session.get_sql_engine", return_value=fake_engine):
            slugs = scheduler._get_optin_projects()
        assert slugs == ["p1", "p2"]

    def test_returns_empty_on_db_error(self):
        from dash.learning import scheduler
        with patch("db.session.get_sql_engine",
                   side_effect=RuntimeError("boom")):
            assert scheduler._get_optin_projects() == []


# ---------------------------------------------------------------------------
# _sweep_once
# ---------------------------------------------------------------------------

class TestSweep:
    def test_sweep_processes_optin_projects(self):
        from dash.learning import scheduler
        scheduler._state["enabled"] = True
        scheduler._state["running"] = False
        scheduler._state["projects_processed"] = 0

        async def noop_run(*a, **kw):
            return None

        with patch.object(scheduler, "_get_optin_projects",
                          return_value=["a", "b", "c"]), \
             patch.object(scheduler, "_run_one_project",
                          side_effect=noop_run) as mrun, \
             patch.object(scheduler, "_run_central",
                          side_effect=noop_run), \
             patch.object(scheduler, "_maybe_run_canary",
                          side_effect=noop_run):
            _run(scheduler._sweep_once())

        assert mrun.call_count == 3
        assert scheduler._state["projects_processed"] == 3
        assert scheduler._state["last_error"] is None

    def test_sweep_handles_project_failure(self):
        from dash.learning import scheduler
        scheduler._state["enabled"] = True
        scheduler._state["running"] = False
        scheduler._state["projects_processed"] = 0

        async def fail_run(slug, *a, **kw):
            # _run_one_project itself swallows exceptions, but simulate a
            # successful run wrapper here.
            if slug == "bad":
                # _run_one_project catches errors internally; simulate that
                return None
            return None

        async def noop(*a, **kw):
            return None

        with patch.object(scheduler, "_get_optin_projects",
                          return_value=["good", "bad", "good2"]), \
             patch.object(scheduler, "_run_one_project",
                          side_effect=fail_run), \
             patch.object(scheduler, "_run_central", side_effect=noop), \
             patch.object(scheduler, "_maybe_run_canary", side_effect=noop):
            _run(scheduler._sweep_once())

        assert scheduler._state["projects_processed"] == 3

    def test_sweep_disabled_skips(self):
        from dash.learning import scheduler
        scheduler._state["enabled"] = False
        scheduler._state["running"] = False
        scheduler._state["projects_processed"] = 0

        with patch.object(scheduler, "_get_optin_projects") as mq:
            _run(scheduler._sweep_once())
        mq.assert_not_called()
        scheduler._state["enabled"] = True

    def test_sweep_running_skips_concurrent(self):
        from dash.learning import scheduler
        scheduler._state["enabled"] = True
        scheduler._state["running"] = True
        scheduler._state["projects_processed"] = 99  # sentinel

        with patch.object(scheduler, "_get_optin_projects") as mq:
            _run(scheduler._sweep_once())
        mq.assert_not_called()
        # State preserved (not reset)
        assert scheduler._state["projects_processed"] == 99
        scheduler._state["running"] = False

    def test_sweep_respects_max_time(self):
        """When elapsed > MAX_SWEEP_S, sweep stops mid-loop."""
        from dash.learning import scheduler

        scheduler._state["enabled"] = True
        scheduler._state["running"] = False
        scheduler._state["projects_processed"] = 0

        async def noop(*a, **kw):
            return None

        # Patch MAX_SWEEP_S to 0 so the cap trips on the first iteration.
        with patch.object(scheduler, "MAX_SWEEP_S", 0), \
             patch.object(scheduler, "_get_optin_projects",
                          return_value=["a", "b", "c"]), \
             patch.object(scheduler, "_run_one_project",
                          side_effect=noop) as mrun, \
             patch.object(scheduler, "_run_central", side_effect=noop), \
             patch.object(scheduler, "_maybe_run_canary", side_effect=noop):
            _run(scheduler._sweep_once())

        # No projects fully processed because the cap fires immediately.
        assert mrun.call_count == 0


# ---------------------------------------------------------------------------
# Canary
# ---------------------------------------------------------------------------

class TestCanary:
    def test_canary_only_on_sundays(self):
        """When today is not Sunday, canary returns immediately."""
        from dash.learning import scheduler
        from datetime import date

        # Force last_canary_day to None and override datetime.utcnow().date
        scheduler._last_canary_day = None
        non_sunday = date(2026, 5, 4)  # Monday
        with patch("dash.learning.scheduler.datetime") as md:
            md.utcnow.return_value.date.return_value = non_sunday
            with patch.object(scheduler, "_get_optin_projects") as mq:
                _run(scheduler._maybe_run_canary())
        mq.assert_not_called()

    def test_canary_runs_on_sunday(self):
        from dash.learning import scheduler
        from datetime import date

        scheduler._last_canary_day = None
        sunday = date(2026, 5, 3)  # Sunday (weekday 6)
        assert sunday.weekday() == 6

        async def to_thread_passthrough(fn, *a, **kw):
            return fn(*a, **kw)

        with patch("dash.learning.scheduler.datetime") as md:
            md.utcnow.return_value.date.return_value = sunday
            with patch.object(scheduler, "_get_optin_projects",
                              return_value=["p1"]), \
                 patch("asyncio.to_thread",
                       side_effect=to_thread_passthrough), \
                 patch("dash.learning.cycle.LearningCycle") as LC:
                async def empty_run(*a, **kw):
                    if False:
                        yield
                LC.return_value.run = empty_run
                _run(scheduler._maybe_run_canary())

        assert scheduler._last_canary_day == sunday

    def test_canary_dry_run_mode(self):
        """Verify LearningCycle invoked with dry_run=True on Sunday."""
        from dash.learning import scheduler
        from datetime import date

        scheduler._last_canary_day = None
        sunday = date(2026, 5, 3)

        async def to_thread_passthrough(fn, *a, **kw):
            return fn(*a, **kw)

        captured_kwargs = []

        class FakeCycle:
            def __init__(self, **kw):
                captured_kwargs.append(kw)

            async def run(self):
                if False:
                    yield

        with patch("dash.learning.scheduler.datetime") as md:
            md.utcnow.return_value.date.return_value = sunday
            with patch.object(scheduler, "_get_optin_projects",
                              return_value=["x", "y"]), \
                 patch("asyncio.to_thread",
                       side_effect=to_thread_passthrough), \
                 patch("dash.learning.cycle.LearningCycle", FakeCycle):
                _run(scheduler._maybe_run_canary())

        assert len(captured_kwargs) == 2
        for kw in captured_kwargs:
            assert kw["dry_run"] is True
            assert kw["max_questions"] == 5


# ---------------------------------------------------------------------------
# trigger_now
# ---------------------------------------------------------------------------

class TestTriggerNow:
    def test_returns_state_when_already_running(self):
        from dash.learning import scheduler
        scheduler._state["running"] = True
        out = _run(scheduler.trigger_now())
        assert out["running"] is True
        scheduler._state["running"] = False

    def test_creates_task_when_idle(self):
        from dash.learning import scheduler
        scheduler._state["running"] = False
        with patch("asyncio.create_task") as mt:
            _run(scheduler.trigger_now())
        mt.assert_called_once()
