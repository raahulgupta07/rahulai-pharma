"""Workflow scheduler tests (croniter + schedule API).

Gated behind WORKFLOW_TEST_DB=1. Skip-soft when scheduler module / migration
125 absent so CI stays fast and parallel to Agent #4.
"""
from __future__ import annotations

import importlib
import os
from datetime import datetime, timedelta, timezone

import pytest

_GATE = os.getenv("WORKFLOW_TEST_DB", "").lower() in ("1", "true", "yes", "on")
if not _GATE:
    pytestmark = pytest.mark.skip(
        reason="workflow scheduler E2E gated — set WORKFLOW_TEST_DB=1 to run"
    )


def _try_import_scheduler():
    try:
        return importlib.import_module("dash.cron.workflow_scheduler")
    except Exception:
        return None


def _try_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        return None


def test_croniter_imports():
    """croniter must be installed for cron-string parsing."""
    croniter = pytest.importorskip("croniter")
    assert hasattr(croniter, "croniter")


def test_create_schedule_valid_cron():
    """POST /schedules with cron='*/5 * * * *' → 200 + next_run_at within 5min."""
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except Exception:
        pytest.skip("FastAPI app not importable")
    client = TestClient(app)
    token = os.getenv("WORKFLOW_TEST_TOKEN", "")
    wf_id = os.getenv("WORKFLOW_TEST_ID", "")
    if not (token and wf_id):
        pytest.skip("WORKFLOW_TEST_TOKEN/ID not set")
    r = client.post(
        "/api/agent-os/workflows/schedules",
        headers={"Authorization": f"Bearer {token}"},
        json={"workflow_id": wf_id, "cron": "*/5 * * * *"},
    )
    if r.status_code == 404:
        pytest.skip("schedule endpoint not mounted yet (Agent #4 pending)")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "next_run_at" in body
    nxt = datetime.fromisoformat(body["next_run_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    delta = (nxt - now).total_seconds()
    assert 0 <= delta <= 360, f"next_run_at {nxt} not within 5min of now {now}"


def test_create_schedule_invalid_cron():
    """POST with cron='not a cron' → 400."""
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except Exception:
        pytest.skip("FastAPI app not importable")
    client = TestClient(app)
    token = os.getenv("WORKFLOW_TEST_TOKEN", "")
    wf_id = os.getenv("WORKFLOW_TEST_ID", "")
    if not (token and wf_id):
        pytest.skip("WORKFLOW_TEST_TOKEN/ID not set")
    r = client.post(
        "/api/agent-os/workflows/schedules",
        headers={"Authorization": f"Bearer {token}"},
        json={"workflow_id": wf_id, "cron": "not a cron"},
    )
    if r.status_code == 404:
        pytest.skip("schedule endpoint not mounted yet")
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"


def test_next_run_at_advances():
    """INSERT schedule w/ past next_run_at; run one tick; next_run_at moves forward."""
    sched = _try_import_scheduler()
    if sched is None:
        pytest.skip("dash.cron.workflow_scheduler not shipped yet")
    tick_fn = getattr(sched, "_tick", None) or getattr(sched, "tick_once", None)
    if tick_fn is None:
        pytest.skip("scheduler has no _tick/tick_once helper")
    eng = _try_engine()
    if eng is None:
        pytest.skip("write engine unavailable")
    sched_id = int(os.getenv("WORKFLOW_TEST_PAST_SCHEDULE_ID", "0"))
    if sched_id <= 0:
        pytest.skip("WORKFLOW_TEST_PAST_SCHEDULE_ID not set")
    from sqlalchemy import text
    with eng.connect() as conn:
        before = conn.execute(
            text(
                "SELECT next_run_at FROM public.dash_workflow_schedules WHERE id = :i"
            ),
            {"i": sched_id},
        ).scalar()
    tick_fn()
    with eng.connect() as conn:
        after = conn.execute(
            text(
                "SELECT next_run_at FROM public.dash_workflow_schedules WHERE id = :i"
            ),
            {"i": sched_id},
        ).scalar()
    assert after is not None
    assert before is None or after > before


def test_paused_not_fired():
    """INSERT schedule status='paused' w/ past next_run_at; run tick; no run created."""
    sched = _try_import_scheduler()
    if sched is None:
        pytest.skip("dash.cron.workflow_scheduler not shipped yet")
    tick_fn = getattr(sched, "_tick", None) or getattr(sched, "tick_once", None)
    if tick_fn is None:
        pytest.skip("scheduler has no _tick/tick_once helper")
    eng = _try_engine()
    if eng is None:
        pytest.skip("write engine unavailable")
    sched_id = int(os.getenv("WORKFLOW_TEST_PAUSED_SCHEDULE_ID", "0"))
    wf_id = os.getenv("WORKFLOW_TEST_ID", "")
    if sched_id <= 0 or not wf_id:
        pytest.skip("WORKFLOW_TEST_PAUSED_SCHEDULE_ID / WORKFLOW_TEST_ID not set")
    from sqlalchemy import text
    with eng.connect() as conn:
        before_runs = conn.execute(
            text(
                "SELECT COUNT(*) FROM public.dash_workflow_runs WHERE workflow_id = :w"
            ),
            {"w": wf_id},
        ).scalar() or 0
    tick_fn()
    with eng.connect() as conn:
        after_runs = conn.execute(
            text(
                "SELECT COUNT(*) FROM public.dash_workflow_runs WHERE workflow_id = :w"
            ),
            {"w": wf_id},
        ).scalar() or 0
    assert after_runs == before_runs, (
        f"paused schedule fired: runs {before_runs} -> {after_runs}"
    )
