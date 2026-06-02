"""Dash custom Prometheus metrics.

Exposed at `/metrics` via `prometheus-fastapi-instrumentator` (wired in
`app/main.py`). All counters live in the default `prometheus_client`
registry so the instrumentator picks them up automatically.

Import is fail-soft — if `prometheus_client` is missing, every helper
becomes a no-op so the rest of the app keeps working.

Call sites:
    chat_requests_total{project,status}    app/projects.py chat endpoint
    sse_events_total{type}                 dash/utils/agno_sse_wrap.py
    verified_pass_total                    dash/learning/verified_reward.py
    upload_bytes_total                     app/upload.py /upload endpoint
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter

    chat_requests_total = Counter(
        "dash_chat_requests_total",
        "Chat requests handled by /api/projects/{slug}/chat.",
        labelnames=("project", "status"),
    )
    sse_events_total = Counter(
        "dash_sse_events_total",
        "SSE events emitted on team.run streams (post audit).",
        labelnames=("type",),
    )
    verified_pass_total = Counter(
        "dash_verified_pass_total",
        "Answers graded against ground-truth (pass/fail/unknown).",
        labelnames=("verdict",),
    )
    upload_bytes_total = Counter(
        "dash_upload_bytes_total",
        "Bytes accepted on /api/upload (data file uploads).",
        labelnames=("ext",),
    )
    _HAS = True
except Exception as exc:  # noqa: BLE001
    logger.warning("prometheus_client unavailable, metrics disabled: %s", exc)
    chat_requests_total = None  # type: ignore
    sse_events_total = None  # type: ignore
    verified_pass_total = None  # type: ignore
    upload_bytes_total = None  # type: ignore
    _HAS = False


def inc_chat(project: str | None, status: str) -> None:
    """Increment chat_requests_total. Status: started|ok|error|timeout."""
    if not _HAS:
        return
    try:
        chat_requests_total.labels(  # type: ignore[union-attr]
            project=(project or "unknown")[:64],
            status=status[:32],
        ).inc()
    except Exception:
        pass


def inc_sse(event_type: str) -> None:
    if not _HAS:
        return
    try:
        sse_events_total.labels(type=(event_type or "unknown")[:64]).inc()  # type: ignore[union-attr]
    except Exception:
        pass


def inc_verified(verdict: str) -> None:
    """Verdict: pass | fail | unknown."""
    if not _HAS:
        return
    try:
        verified_pass_total.labels(verdict=verdict[:16]).inc()  # type: ignore[union-attr]
    except Exception:
        pass


def add_upload_bytes(ext: str, n: int) -> None:
    if not _HAS or n <= 0:
        return
    try:
        upload_bytes_total.labels(ext=(ext or "unknown").lstrip(".")[:16]).inc(n)  # type: ignore[union-attr]
    except Exception:
        pass
