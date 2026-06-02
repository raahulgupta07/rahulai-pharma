"""Tests for the LLM concurrency semaphore + async wrapper in dash/settings.py.

Validates:
  * Per-tier semaphores cap concurrent in-flight calls (chat=10, deep=3, lite=20).
  * Async wrapper proxies the sync `training_llm_call` (same return value).
  * Semaphores are lazy-init via `_get_sem` (no event-loop binding at import).
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


# Stub db.session before importing dash.settings (matches test_cost_guard.py pattern).
if "db.session" not in sys.modules:
    _stub = ModuleType("db.session")
    _stub.get_sql_engine = MagicMock(name="get_sql_engine_stub")
    _stub.get_write_engine = MagicMock(name="get_write_engine_stub")
    _stub.get_postgres_db = MagicMock(name="get_postgres_db_stub")
    _db_pkg = sys.modules.setdefault("db", ModuleType("db"))
    _db_pkg.session = _stub
    sys.modules["db.session"] = _stub
    _url_stub = ModuleType("db.url")
    _url_stub.db_url = MagicMock(return_value="postgresql://stub")
    _db_pkg.url = _url_stub
    sys.modules["db.url"] = _url_stub
    # Stub the top-level `db` exports used by dash.settings.
    _db_pkg.get_postgres_db = MagicMock(name="get_postgres_db_top")
    _db_pkg.create_knowledge = MagicMock(name="create_knowledge_top")


@pytest.fixture
def settings_module(monkeypatch):
    """Import dash.settings fresh per-test so env-var changes take effect."""
    # Set caps BEFORE importing so module-level constants pick them up.
    monkeypatch.setenv("LLM_PARALLEL_CAP", "5")
    monkeypatch.setenv("LLM_PARALLEL_CAP_CHAT", "10")
    monkeypatch.setenv("LLM_PARALLEL_CAP_DEEP", "3")
    monkeypatch.setenv("LLM_PARALLEL_CAP_LITE", "20")
    # Drop any cached module so caps re-read from env.
    sys.modules.pop("dash.settings", None)
    import dash.settings as _s
    # Clear per-loop semaphore cache so each test gets fresh sems.
    _s._LLM_SEM_CACHE.clear()
    return _s


def test_per_tier_caps(settings_module):
    """Each tier semaphore must use the configured cap from env."""
    s = settings_module

    async def _check():
        # qa_generation has no `model` key in TRAINING_CONFIGS → defaults to chat tier.
        sem_chat = s._get_sem("qa_generation")
        # deep_analysis has model=DEEP_MODEL → deep tier.
        sem_deep = s._get_sem("deep_analysis")
        # scoring has model=LITE_MODEL → lite tier.
        sem_lite = s._get_sem("scoring")
        return sem_chat._value, sem_deep._value, sem_lite._value

    chat_cap, deep_cap, lite_cap = asyncio.run(_check())
    assert chat_cap == 10, f"chat tier cap should be 10, got {chat_cap}"
    assert deep_cap == 3, f"deep tier cap should be 3, got {deep_cap}"
    assert lite_cap == 20, f"lite tier cap should be 20, got {lite_cap}"


def test_semaphore_blocks_beyond_cap(settings_module, monkeypatch):
    """Spawn 20 async calls against the chat tier (cap=10) and assert no more
    than 10 are in-flight at any moment."""
    s = settings_module

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()
    started = asyncio.Event()

    def _fake_sync_call(prompt, task, model=None):
        # Simulates a slow LLM call. Tracks how many are running concurrently.
        # Uses time.sleep so the threadpool can actually parallelize.
        import time as _t
        _t.sleep(0.05)
        return f"ok:{prompt}"

    # Patch training_llm_call (the sync function) so async wrapper offloads
    # our fake into the thread pool.
    monkeypatch.setattr(s, "training_llm_call", _fake_sync_call)

    async def _tracked_call(i):
        nonlocal in_flight, max_in_flight
        # Acquire sem manually so we can observe in-flight count from inside.
        sem = s._get_sem("qa_generation")  # chat tier, cap=10
        async with sem:
            async with lock:
                in_flight += 1
                if in_flight > max_in_flight:
                    max_in_flight = in_flight
                started.set()
            loop = asyncio.get_event_loop()
            # Mirror what the wrapper does (run_in_executor).
            result = await loop.run_in_executor(
                s._LLM_POOL, _fake_sync_call, f"p{i}", "qa_generation", None
            )
            async with lock:
                in_flight -= 1
            return result

    async def _drive():
        results = await asyncio.gather(*[_tracked_call(i) for i in range(20)])
        return results

    results = asyncio.run(_drive())
    assert len(results) == 20
    assert all(r.startswith("ok:p") for r in results)
    assert max_in_flight <= 10, (
        f"expected ≤10 concurrent (chat tier cap), saw {max_in_flight}"
    )
    # Sanity: we should actually have saturated the semaphore (otherwise the
    # test doesn't prove much).
    assert max_in_flight >= 2, (
        f"test didn't generate enough concurrency to prove the cap; saw {max_in_flight}"
    )


def test_async_wrapper_returns_same_as_sync(settings_module, monkeypatch):
    """Stub the sync `training_llm_call` and assert the async wrapper returns
    the identical value, with the same args forwarded."""
    s = settings_module

    captured = {}

    def _fake_sync_call(prompt, task, model=None):
        captured["prompt"] = prompt
        captured["task"] = task
        captured["model"] = model
        return "FIXED_RESPONSE_xyz"

    monkeypatch.setattr(s, "training_llm_call", _fake_sync_call)

    async def _go():
        return await s.training_llm_call_async(
            "hello world", task="qa_generation", model="some/model"
        )

    result = asyncio.run(_go())
    assert result == "FIXED_RESPONSE_xyz"
    assert captured["prompt"] == "hello world"
    assert captured["task"] == "qa_generation"
    assert captured["model"] == "some/model"
