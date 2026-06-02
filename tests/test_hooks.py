"""Tests for dash/agentic/hooks.py — pre/post hook framework."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest


# ── Path bootstrap (worktree-aware) ────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ── Stub db.session BEFORE importing hooks (so flush_now is reachable) ────
_captured_inserts: list[list[dict]] = []


def _install_db_stubs() -> None:
    if "db" not in sys.modules:
        sys.modules["db"] = ModuleType("db")
    if "db.session" not in sys.modules:
        sess = ModuleType("db.session")

        def _make_engine():
            eng = MagicMock(name="engine")
            conn = MagicMock(name="conn")

            def _execute(stmt, params=None):
                if isinstance(params, list):
                    _captured_inserts.append(params)
                return MagicMock()

            conn.execute.side_effect = _execute
            cm = MagicMock()
            cm.__enter__.return_value = conn
            cm.__exit__.return_value = False
            eng.begin.return_value = cm
            eng.connect.return_value = cm
            return eng

        sess.get_sql_engine = MagicMock(side_effect=_make_engine)
        sys.modules["db.session"] = sess
    if "db.url" not in sys.modules:
        url = ModuleType("db.url")
        url.db_url = MagicMock(return_value="postgresql://stub")
        sys.modules["db.url"] = url


_install_db_stubs()

from dash.agentic import hooks as H  # noqa: E402
from dash.agentic.hooks import (  # noqa: E402
    HookContext,
    HookResult,
    apply_hooks,
    clear_hooks,
    flush_now,
    post_hook,
    pre_hook,
)


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    clear_hooks()
    _captured_inserts.clear()
    # Drain any buffered audit rows from prior tests so each test starts clean.
    H._audit_buffer.clear()
    # Default: flag enabled for most tests
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")
    yield
    clear_hooks()


# ── Migration applies (smoke check the SQL parses) ─────────────────────────
def test_migration_file_exists_and_is_valid():
    sql_path = _REPO_ROOT / "db" / "migrations" / "040_hook_audit.sql"
    assert sql_path.exists(), "040_hook_audit.sql missing"
    text = sql_path.read_text()
    assert "dash.dash_hook_audit" in text
    assert "BIGSERIAL PRIMARY KEY" in text
    assert "idx_hook_audit_block" in text


# ── Pre: block short-circuits ──────────────────────────────────────────────
def test_pre_hook_block_short_circuits_with_structured_error():
    called = {"fn": 0}

    @pre_hook("blocker", priority=10)
    def _block(ctx: HookContext) -> HookResult:
        return HookResult(decision="block", reason="nope")

    def my_tool(x):
        called["fn"] += 1
        return x * 2

    wrapped = apply_hooks(my_tool, tool_name="my_tool")
    out = wrapped(5)
    assert called["fn"] == 0
    assert isinstance(out, dict)
    assert out["blocked"] is True
    assert out["hook"] == "blocker"
    assert out["reason"] == "nope"
    assert out["tool"] == "my_tool"


# ── Pre: mutate replaces args ──────────────────────────────────────────────
def test_pre_hook_mutate_replaces_args():
    @pre_hook("doubler", priority=10)
    def _mutate(ctx: HookContext) -> HookResult:
        new_args = [a * 10 for a in ctx.args]
        return HookResult(decision="mutate", mutated_args=new_args)

    def my_tool(x):
        return x + 1

    wrapped = apply_hooks(my_tool, tool_name="my_tool")
    assert wrapped(3) == 31  # (3*10)+1


# ── Post: mutate replaces result ───────────────────────────────────────────
def test_post_hook_mutate_replaces_result():
    @post_hook("upper", priority=10)
    def _post(ctx: HookContext, result):
        return HookResult(decision="mutate", mutated_result=str(result).upper())

    def my_tool():
        return "hello"

    wrapped = apply_hooks(my_tool, tool_name="my_tool")
    assert wrapped() == "HELLO"


# ── Async tool wrapped correctly ───────────────────────────────────────────
def test_async_tool_wrapped():
    @pre_hook("p", priority=10)
    def _p(ctx):
        return HookResult(decision="mutate", mutated_args=[(ctx.args[0] or 0) + 1])

    async def my_async_tool(x):
        await asyncio.sleep(0)
        return x * 100

    wrapped = apply_hooks(my_async_tool, tool_name="my_async_tool")
    assert asyncio.iscoroutinefunction(wrapped)
    out = asyncio.run(wrapped(2))
    assert out == 300  # (2+1) * 100


# ── Priority ordering ──────────────────────────────────────────────────────
def test_priority_ordering_low_runs_first():
    order: list[str] = []

    @pre_hook("late", priority=90)
    def _late(ctx):
        order.append("late")
        return HookResult(decision="pass")

    @pre_hook("early", priority=10)
    def _early(ctx):
        order.append("early")
        return HookResult(decision="pass")

    def my_tool():
        return None

    wrapped = apply_hooks(my_tool, tool_name="my_tool")
    wrapped()
    assert order == ["early", "late"]


# ── Flag off: zero overhead, no DB write ───────────────────────────────────
def test_flag_off_returns_fn_unchanged_and_no_audit(monkeypatch):
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)

    @pre_hook("blocker", priority=10)
    def _block(ctx):
        return HookResult(decision="block", reason="should not run")

    def my_tool(x):
        return x * 2

    wrapped = apply_hooks(my_tool, tool_name="my_tool")
    # Identity: same callable returned.
    assert wrapped is my_tool
    assert wrapped(7) == 14
    # No audit rows enqueued -> nothing flushed
    written = flush_now()
    assert written == 0
    assert _captured_inserts == []


# ── Audit row written on block ─────────────────────────────────────────────
def test_audit_written_on_block_via_flush_now():
    @pre_hook("blocker", priority=10)
    def _block(ctx):
        return HookResult(decision="block", reason="cap reached")

    def my_tool():
        return 1

    wrapped = apply_hooks(my_tool, tool_name="cap_tool")
    wrapped()

    written = flush_now()
    assert written >= 1
    # Inspect the captured INSERT batch
    assert _captured_inserts, "no INSERT captured"
    rows = _captured_inserts[-1]
    blocks = [r for r in rows if r["decision"] == "block"]
    assert blocks, f"no block row in: {rows}"
    b = blocks[0]
    assert b["hook_name"] == "blocker"
    assert b["hook_kind"] == "pre"
    assert b["tool_name"] == "cap_tool"
    assert b["reason"] == "cap reached"
    assert isinstance(b["latency_ms"], int)
