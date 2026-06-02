# Workflow notification hooks (Task #6 — feed notifications).
#
# Called by dash/cron/workflow_runner.py (Agent #3 wires) and
# dash/cron/workflow_scheduler.py (Agent #4 wires) at start/done/failed transitions.
# If those modules don't import these hooks yet, see TODO.md "[wf-notify]".
#
# Notes on app.auth.notify_user signature:
#   notify_user(user_id: int, title: str, message: str = "", ntype: str = "info")
#   The shipped `dash_notifications` schema only has (user_id, type, title, message)
#   — no `link` column. We inline the deep link into the message body so the UI
#   can render it as plain text or detect/format on parse.
"""Feed-notification helpers for workflow lifecycle events.

All three functions are fail-soft: they log on failure and never raise. Wire them
at the queued→running claim, post-build commit, and run-level except branch in
`dash/cron/workflow_runner.py` and the cron-triggered run path in
`dash/cron/workflow_scheduler.py`.
"""
from __future__ import annotations

import logging

__all__ = [
    "notify_workflow_started",
    "notify_workflow_done",
    "notify_workflow_failed",
]

_log = logging.getLogger(__name__)


def notify_workflow_started(
    owner_user_id: int,
    workflow_name: str,
    run_id: int,
    source: str,
) -> None:
    """Notify owner that a workflow run was claimed and is now running."""
    try:
        from app.auth import notify_user  # local import — fail-soft if unavailable
        link = f"/ui/agent-os/workflows/runs/{run_id}"
        notify_user(
            int(owner_user_id),
            f"Workflow started: {workflow_name}",
            f"Run #{run_id} via {source}. Watch live: {link}",
            "info",
        )
    except Exception:
        _log.warning("notify_workflow_started failed", exc_info=True)


def notify_workflow_done(
    owner_user_id: int,
    workflow_name: str,
    run_id: int,
    dashboard_id: str | None,
    duration_s: float,
    project_slug: str | None,
) -> None:
    """Notify owner that a workflow run completed successfully.

    type='success' when a dashboard was produced, else 'info'. Link points to
    the studio when both dashboard_id + project_slug are present, otherwise the
    run-detail page.
    """
    try:
        from app.auth import notify_user
        ntype = "success" if dashboard_id else "info"
        if dashboard_id and project_slug:
            link = f"/ui/project/{project_slug}/studio/{dashboard_id}"
        else:
            link = f"/ui/agent-os/workflows/runs/{run_id}"
        try:
            dur = float(duration_s)
        except Exception:
            dur = 0.0
        message = (
            f"Run #{run_id} completed in {dur:.1f}s. "
            f"{('Dashboard ready' if dashboard_id else 'No dashboard produced')}. "
            f"Open: {link}"
        )
        notify_user(
            int(owner_user_id),
            f"Workflow done: {workflow_name}",
            message,
            ntype,
        )
    except Exception:
        _log.warning("notify_workflow_done failed", exc_info=True)


def notify_workflow_failed(
    owner_user_id: int,
    workflow_name: str,
    run_id: int,
    error_msg: str,
) -> None:
    """Notify owner that a workflow run failed."""
    try:
        from app.auth import notify_user
        link = f"/ui/agent-os/workflows/runs/{run_id}"
        err = (error_msg or "unknown error")[:500]
        notify_user(
            int(owner_user_id),
            f"Workflow failed: {workflow_name}",
            f"Run #{run_id} failed: {err}. Details: {link}",
            "error",
        )
    except Exception:
        _log.warning("notify_workflow_failed failed", exc_info=True)
