"""Notification helpers (workflow lifecycle hooks, etc).

All hooks are fail-soft — they log on failure and never raise. Safe to import
from cron loops and worker threads.
"""
from __future__ import annotations

from .workflow_hooks import (
    notify_workflow_done,
    notify_workflow_failed,
    notify_workflow_started,
)

__all__ = [
    "notify_workflow_started",
    "notify_workflow_done",
    "notify_workflow_failed",
]
