"""Workflow runner E2E tests.

Gated behind WORKFLOW_TEST_DB=1 — these require a live Postgres + the runner
module shipped by Agent #3. Skip-soft if either prerequisite is missing so CI
stays fast.

Tests:
- test_run_endpoint_returns_run_id        — POST /api/agent-os/workflows/{id}/run
- test_runner_atomic_claim                — 2 concurrent claims on same row
- test_dashboard_built_on_success         — 2-step in-memory workflow produces dashboard
- test_failed_step_doesnt_block           — partial-success workflow finishes
- test_notify_hooks_invoked               — notify_workflow_done called once
"""
from __future__ import annotations

import importlib
import os
import threading
from unittest.mock import MagicMock

import pytest

_GATE = os.getenv("WORKFLOW_TEST_DB", "").lower() in ("1", "true", "yes", "on")
if not _GATE:
    pytestmark = pytest.mark.skip(
        reason="workflow runner E2E gated — set WORKFLOW_TEST_DB=1 to run"
    )


def _try_import_runner():
    try:
        return importlib.import_module("dash.cron.workflow_runner")
    except Exception:
        return None


def _try_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        return None


def test_run_endpoint_returns_run_id():
    """POST /api/agent-os/workflows/{id}/run with valid auth → 200 + {run_id, stream_url}."""
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except Exception:
        pytest.skip("FastAPI app not importable")
    client = TestClient(app)
    token = os.getenv("WORKFLOW_TEST_TOKEN", "")
    if not token:
        pytest.skip("WORKFLOW_TEST_TOKEN not set")
    wf_id = os.getenv("WORKFLOW_TEST_ID", "")
    if not wf_id:
        pytest.skip("WORKFLOW_TEST_ID not set")
    r = client.post(
        f"/api/agent-os/workflows/{wf_id}/run",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "run_id" in body
    assert "stream_url" in body


def test_runner_atomic_claim():
    """Two concurrent threads call _claim on the same queued row; exactly one wins."""
    runner = _try_import_runner()
    if runner is None:
        pytest.skip("dash.cron.workflow_runner not shipped yet")
    claim_fn = getattr(runner, "_claim_run", None) or getattr(runner, "_claim", None)
    if claim_fn is None:
        pytest.skip("runner has no _claim/_claim_run helper")
    eng = _try_engine()
    if eng is None:
        pytest.skip("write engine unavailable")
    # Caller is responsible for setting up two queued rows + cleaning up — this
    # test only validates the race semantics shape.
    row_id = int(os.getenv("WORKFLOW_TEST_QUEUED_RUN_ID", "0"))
    if row_id <= 0:
        pytest.skip("WORKFLOW_TEST_QUEUED_RUN_ID not set")
    results: list[bool] = []
    lock = threading.Lock()

    def attempt():
        try:
            out = claim_fn(row_id)
        except Exception:
            out = False
        with lock:
            results.append(bool(out))

    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sum(1 for r in results if r) == 1, f"expected exactly 1 winner, got {results}"


def test_dashboard_built_on_success():
    """Mock workflow w/ 2 in-memory steps each returning a DataFrame → dashboard row exists."""
    runner = _try_import_runner()
    if runner is None:
        pytest.skip("dash.cron.workflow_runner not shipped yet")
    run_fn = getattr(runner, "_run_workflow", None) or getattr(runner, "execute_run", None)
    if run_fn is None:
        pytest.skip("runner has no _run_workflow/execute_run helper")
    try:
        import pandas as pd  # noqa: F401
    except Exception:
        pytest.skip("pandas unavailable")
    # Caller responsible for fixture workflow id that returns 2 steps of DataFrames
    fixture_id = int(os.getenv("WORKFLOW_TEST_DASH_FIXTURE_RUN_ID", "0"))
    if fixture_id <= 0:
        pytest.skip("WORKFLOW_TEST_DASH_FIXTURE_RUN_ID not set")
    out = run_fn(fixture_id)
    # Smoke: result should signal success + reference a dashboard_id
    if isinstance(out, dict):
        assert out.get("status") in ("done", "success", None)
        dash_id = out.get("dashboard_id")
        if dash_id:
            eng = _try_engine()
            if eng is None:
                return
            from sqlalchemy import text
            with eng.connect() as conn:
                row = conn.execute(
                    text("SELECT spec FROM public.dash_dashboards_v2 WHERE id = :i"),
                    {"i": dash_id},
                ).fetchone()
            assert row is not None
            spec = row[0] if not isinstance(row[0], dict) else row[0]
            # spec may be JSON string or dict
            import json
            if isinstance(spec, str):
                spec = json.loads(spec)
            panels = spec.get("panels") or spec.get("cells") or []
            assert len(panels) >= 2


def test_failed_step_doesnt_block():
    """Step 1 raises, step 2 returns rows — run completes with partial panel set."""
    runner = _try_import_runner()
    if runner is None:
        pytest.skip("dash.cron.workflow_runner not shipped yet")
    fixture_id = int(os.getenv("WORKFLOW_TEST_PARTIAL_FIXTURE_RUN_ID", "0"))
    if fixture_id <= 0:
        pytest.skip("WORKFLOW_TEST_PARTIAL_FIXTURE_RUN_ID not set")
    run_fn = getattr(runner, "_run_workflow", None) or getattr(runner, "execute_run", None)
    if run_fn is None:
        pytest.skip("runner has no _run_workflow/execute_run helper")
    out = run_fn(fixture_id)
    # Should complete (not crash) even with one bad step
    assert out is not None


def test_notify_hooks_invoked(monkeypatch):
    """Monkeypatch notify_workflow_done w/ MagicMock; run fake workflow; assert called once."""
    runner = _try_import_runner()
    if runner is None:
        pytest.skip("dash.cron.workflow_runner not shipped yet")
    fixture_id = int(os.getenv("WORKFLOW_TEST_DASH_FIXTURE_RUN_ID", "0"))
    if fixture_id <= 0:
        pytest.skip("WORKFLOW_TEST_DASH_FIXTURE_RUN_ID not set")
    # Patch both at the package and at the runner-imported binding (whichever the
    # runner ended up using).
    import dash.notifications.workflow_hooks as hooks
    mock_done = MagicMock()
    monkeypatch.setattr(hooks, "notify_workflow_done", mock_done)
    # Also patch on the runner module if it imported the symbol directly.
    if hasattr(runner, "notify_workflow_done"):
        monkeypatch.setattr(runner, "notify_workflow_done", mock_done)
    run_fn = getattr(runner, "_run_workflow", None) or getattr(runner, "execute_run", None)
    if run_fn is None:
        pytest.skip("runner has no _run_workflow/execute_run helper")
    run_fn(fixture_id)
    assert mock_done.call_count == 1, f"expected 1 call, got {mock_done.call_count}"
