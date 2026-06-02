"""Dash HITL (Human-in-the-Loop) framework.

Provides pause/resume primitives backed by ``public.dash_hitl_requests``:

    create_request(...)       -> request_id
    get_request(request_id)   -> dict | None
    approve(request_id, ...)  -> bool
    reject(request_id, ...)   -> bool
    wait_for_response(req_id) -> dict   (polls every 2s)

Separate from ``dash.dash_hitl_pending`` (SSE/stream-oriented HITL used by
``app/hitl_api.py``). This module backs the workflow framework's pause/resume
pattern on confirm gates.
"""

from .manager import (
    create_request,
    get_request,
    approve,
    reject,
    wait_for_response,
    list_pending,
)

__all__ = [
    "create_request",
    "get_request",
    "approve",
    "reject",
    "wait_for_response",
    "list_pending",
]
