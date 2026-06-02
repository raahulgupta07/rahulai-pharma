"""
Admin Ops API
=============

Endpoints for surfacing operational health to super-admins:

- /api/admin/migrations/status      — Issue #12: list all *.sql vs dash_migrations
- /api/admin/migrations/apply-pending — Issue #12: force-apply pending files
- /api/health/daemons               — Issue #13: which background daemons run on this worker

All endpoints require super-admin auth.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["admin-ops"])


def _require_super(request: Request):
    """Lazy-import to avoid circular dep with app.auth at import time."""
    from app.auth import _require_super as _rs
    return _rs(request)


# ---------------------------------------------------------------------------
# Migration status + force-apply (Issue #12)
# ---------------------------------------------------------------------------
@router.get("/admin/migrations/status")
def admin_migrations_status(request: Request) -> dict[str, Any]:
    _require_super(request)
    try:
        from dash.db_runner.migrate import list_migrations_status
        return list_migrations_status()
    except Exception as e:
        log.exception("admin_migrations_status failed")
        raise HTTPException(500, f"migrations status failed: {e}")


@router.post("/admin/migrations/apply-pending")
def admin_migrations_apply_pending(request: Request) -> dict[str, Any]:
    """Force-apply any pending *.sql migration files (super-admin)."""
    _require_super(request)
    try:
        from dash.db_runner.migrate import run_migrations
        result = run_migrations()
        return {
            "ok": True,
            "applied": result.get("applied", []),
            "skipped": result.get("skipped", 0),
            "errors": result.get("errors", []),
        }
    except Exception as e:
        log.exception("admin_migrations_apply_pending failed")
        raise HTTPException(500, f"apply-pending failed: {e}")


# ---------------------------------------------------------------------------
# Daemon health (Issue #13)
# ---------------------------------------------------------------------------
def _worker_rank() -> str:
    """Best-effort worker rank.

    Uvicorn doesn't set a stable WORKER_RANK env var by default. We probe a
    few common ones (set by gunicorn / custom entrypoints / k8s). When
    nothing is set, we report 'unknown' — callers should set WORKER_RANK in
    their process manager (entrypoint.sh) for accurate gating.
    """
    for key in ("WORKER_RANK", "UVICORN_WORKER_RANK", "GUNICORN_WORKER_ID", "WORKER_ID"):
        v = os.environ.get(key)
        if v is not None and v != "":
            return v
    return "unknown"


def _daemons_should_run() -> tuple[bool, str]:
    """Mirror app/main.py gate logic + new WORKER_RANK gate (Issue #13)."""
    if os.environ.get("DAEMONS_DISABLED") in ("1", "true", "TRUE", "yes"):
        return False, "DAEMONS_DISABLED=1"
    if os.environ.get("K8S_DAEMON_MODE") == "cronjob":
        return False, "K8S_DAEMON_MODE=cronjob"
    rank = _worker_rank()
    # If WORKER_RANK is set and != 0, suppress daemons on this worker.
    if rank not in ("unknown", "0"):
        return False, f"WORKER_RANK={rank} (only rank 0 spawns daemons)"
    return True, f"rank={rank}"


@router.get("/health/daemons")
def health_daemons(request: Request) -> dict[str, Any]:
    """Report which background daemons run on THIS worker process.

    Public (no auth) by design so K8s probes and ops dashboards can poll.
    """
    should_run, reason = _daemons_should_run()
    rank = _worker_rank()
    # Per-daemon env flags (from app/main.py lifespan logic).
    per_daemon = {
        "vector_sync": os.environ.get("VECTOR_SYNC_DISABLED") not in ("1", "true", "TRUE", "yes"),
        "reembed_loop": os.environ.get("VECTOR_SYNC_DISABLED") not in ("1", "true", "TRUE", "yes"),
        "ontology_cluster": os.environ.get("ONTOLOGY_CLUSTER_DISABLED") not in ("1", "true", "TRUE", "yes"),
        "auto_campaign": os.environ.get("AUTO_CAMPAIGN_DAEMON_DISABLED") not in ("1", "true", "TRUE", "yes"),
        "sim_cleanup": os.environ.get("SIM_CLEANUP_DAEMON_DISABLED") not in ("1", "true", "TRUE", "yes"),
        "benchmark_sync": os.environ.get("BENCHMARK_SYNC_DISABLED") not in ("1", "true", "TRUE", "yes"),
        "mrr_snapshot": os.environ.get("MRR_SNAPSHOT_DISABLED") not in ("1", "true", "TRUE", "yes"),
        "brain_versions_purge": True,
        "learning_scheduler": os.environ.get("LEARNING_SCHEDULER_DISABLED") not in ("1", "true", "TRUE", "yes"),
        "digest_scheduler": True,
        "connector_scheduler": os.environ.get("CONNECTOR_SCHEDULER_DISABLED") not in ("1", "true", "TRUE", "yes"),
        "autonomous_workflow_runner": True,
    }
    effective = {name: (should_run and enabled) for name, enabled in per_daemon.items()}
    return {
        "pid": os.getpid(),
        "worker_rank": rank,
        "daemons_should_run_on_this_worker": should_run,
        "reason": reason,
        "per_daemon_env_enabled": per_daemon,
        "per_daemon_effective_on_this_worker": effective,
        "advice": (
            "Set WORKER_RANK env var per uvicorn worker (e.g. via entrypoint) so "
            "only rank 0 spawns daemons. In K8s set DAEMONS_DISABLED=1 on API "
            "pods and run daemons via CronJobs / a dedicated worker pod."
        ),
    }
