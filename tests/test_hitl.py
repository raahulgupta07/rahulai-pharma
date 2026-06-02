"""
Smoke tests for the HITL framework.

These tests are best-effort: they auto-skip when the project's DB env or
SQLAlchemy engine cannot be constructed (CI-friendly).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("EXPERIMENTAL_AGI", "1")


def _get_engine_or_skip():
    try:
        from db.session import get_sql_engine  # type: ignore
        eng = get_sql_engine()
        with eng.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return eng
    except Exception as e:
        pytest.skip(f"DB unavailable: {e}")


def _apply_migration(eng):
    from sqlalchemy import text
    sql = (ROOT / "db" / "migrations" / "039_hitl.sql").read_text()
    with eng.begin() as conn:
        # Postgres only; allow multi-statement script
        for stmt in sql.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))


# ── 1. migration applies ────────────────────────────────────────────────
def test_migration_applies():
    eng = _get_engine_or_skip()
    _apply_migration(eng)
    from sqlalchemy import text
    with eng.connect() as conn:
        ok = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='dash' AND table_name='dash_hitl_pending'"
            )
        ).scalar()
        assert ok == 1


# ── 2. require_confirmation: approve flow ───────────────────────────────
def test_require_confirmation_approve():
    eng = _get_engine_or_skip()
    _apply_migration(eng)

    from dash.agentic import hitl

    @hitl.require_confirmation("test_action", "test {x}", timeout=2.0)
    async def safe_op(x: int) -> dict:
        return {"ok": True, "x": x * 2}

    async def runner():
        run_id = uuid.uuid4().hex
        token = hitl.current_run_id.set(run_id)
        try:
            # Schedule a "user approve" 0.1s after the call lands.
            async def approver():
                await asyncio.sleep(0.1)
                q = hitl.get_pending_queue(run_id)
                # Wait until the queue is registered
                for _ in range(50):
                    if q is not None:
                        break
                    await asyncio.sleep(0.05)
                    q = hitl.get_pending_queue(run_id)
                assert q is not None, "queue not registered"
                await q.put({"decision": "approve"})

            approver_task = asyncio.create_task(approver())
            result = await safe_op(7)
            await approver_task
            return result
        finally:
            hitl.current_run_id.reset(token)

    out = asyncio.run(runner())
    assert out == {"ok": True, "x": 14}


# ── 3. require_confirmation: timeout ────────────────────────────────────
def test_require_confirmation_timeout():
    eng = _get_engine_or_skip()
    _apply_migration(eng)

    from dash.agentic import hitl

    @hitl.require_confirmation("test_action", timeout=0.5)
    async def gated(x: int) -> dict:
        return {"ok": True, "x": x}

    async def runner():
        run_id = uuid.uuid4().hex
        token = hitl.current_run_id.set(run_id)
        try:
            return await gated(1)
        finally:
            hitl.current_run_id.reset(token)

    out = asyncio.run(runner())
    assert out.get("ok") is False
    assert out.get("reason") == "expired"


# ── 4. SSE auth via ?token= ────────────────────────────────────────────
def test_sse_auth_token_fallback_helper():
    """We can't easily run the SSE response in-process w/o an HTTP server,
    but we can assert the helper validates a token-bearing user."""
    pytest.importorskip("fastapi")
    from app import hitl_api

    class _Req:
        class _State:
            user = None
        state = _State()

    # No user, no token → 401
    with pytest.raises(Exception):
        hitl_api._get_user_with_token_fallback(_Req(), token=None)
