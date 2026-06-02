"""Tests for parallel federated executor."""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dash.providers.federation.executor import (  # noqa: E402
    ExecutionResult,
    _add_limit,
    execute_split_plan,
)
from dash.providers.federation.splitter import (  # noqa: E402
    SourceSubquery,
    SplitPlan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(
        self,
        pid: str,
        *,
        dialect: str = "postgres",
        degraded: bool = False,
        last_error: str = "",
        sleep_s: float = 0.0,
        df=None,
        raise_exc: Exception | None = None,
    ):
        self.id = pid
        self.dialect = dialect
        self.degraded = degraded
        self.last_error = last_error
        self.sleep_s = sleep_s
        self.df = df
        self.raise_exc = raise_exc
        self.engine_ro = MagicMock(name=f"engine_{pid}")
        self.last_sql: str | None = None


def _patch_registry(monkeypatch, providers):
    fake_registry = MagicMock()
    fake_registry.list_for_project.return_value = providers

    def _get_registry():
        return fake_registry

    # Patch the import target the executor uses.
    import dash.providers as providers_mod

    monkeypatch.setattr(providers_mod, "get_registry", _get_registry, raising=True)
    return fake_registry


def _patch_execute_sync(monkeypatch):
    """Make _execute_sync use our fake provider attributes instead of real DBs."""

    def _fake_exec(provider, sql):
        provider.last_sql = sql
        if provider.sleep_s:
            time.sleep(provider.sleep_s)
        if provider.raise_exc:
            raise provider.raise_exc
        return provider.df

    import dash.providers.federation.executor as ex_mod

    monkeypatch.setattr(ex_mod, "_execute_sync", _fake_exec, raising=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_executor_empty_plan_returns_empty_result():
    plan = SplitPlan()  # no subqueries
    res = asyncio.run(execute_split_plan(plan, project_slug="proj"))
    assert isinstance(res, ExecutionResult)
    assert res.per_source == {}
    assert res.errors == {}
    assert res.total_rows == 0
    assert res.truncated is False


def test_executor_unknown_provider_logs_error(monkeypatch):
    plan = SplitPlan(subqueries=[SourceSubquery(provider_id="nope", sql="SELECT 1")])
    _patch_registry(monkeypatch, [])  # no providers registered

    res = asyncio.run(execute_split_plan(plan, project_slug="proj"))
    assert "nope" in res.errors
    assert "unknown provider" in res.errors["nope"]
    assert res.per_source == {}


def test_executor_degraded_provider_logs_error(monkeypatch):
    p = _FakeProvider("a", degraded=True, last_error="conn refused")
    plan = SplitPlan(subqueries=[SourceSubquery(provider_id="a", sql="SELECT 1")])
    _patch_registry(monkeypatch, [p])

    res = asyncio.run(execute_split_plan(plan, project_slug="proj"))
    assert "a" in res.errors
    assert "degraded" in res.errors["a"]
    assert res.per_source == {}


def test_executor_translates_to_provider_dialect(monkeypatch):
    pd = pytest.importorskip("pandas")
    p = _FakeProvider("a", dialect="tsql", df=pd.DataFrame({"x": [1]}))
    plan = SplitPlan(subqueries=[SourceSubquery(provider_id="a", sql="SELECT x FROM t")])
    _patch_registry(monkeypatch, [p])
    _patch_execute_sync(monkeypatch)

    res = asyncio.run(execute_split_plan(plan, project_slug="proj"))
    assert "a" in res.per_source
    # T-SQL: row cap should be added as TOP N
    assert "TOP" in (p.last_sql or "").upper()


def test_executor_adds_limit_when_missing(monkeypatch):
    pd = pytest.importorskip("pandas")
    p = _FakeProvider("a", dialect="postgres", df=pd.DataFrame({"x": [1, 2]}))
    plan = SplitPlan(subqueries=[SourceSubquery(provider_id="a", sql="SELECT x FROM t")])
    _patch_registry(monkeypatch, [p])
    _patch_execute_sync(monkeypatch)

    asyncio.run(
        execute_split_plan(plan, project_slug="proj", per_source_row_cap=42)
    )
    assert "LIMIT 42" in (p.last_sql or "")


def test_executor_does_not_double_limit(monkeypatch):
    pd = pytest.importorskip("pandas")
    p = _FakeProvider("a", dialect="postgres", df=pd.DataFrame({"x": [1]}))
    plan = SplitPlan(
        subqueries=[SourceSubquery(provider_id="a", sql="SELECT x FROM t LIMIT 5")]
    )
    _patch_registry(monkeypatch, [p])
    _patch_execute_sync(monkeypatch)

    asyncio.run(execute_split_plan(plan, project_slug="proj"))
    # Should preserve the user's LIMIT 5 and not append another LIMIT
    assert (p.last_sql or "").count("LIMIT") == 1


def test_executor_runs_in_parallel(monkeypatch):
    pd = pytest.importorskip("pandas")
    p1 = _FakeProvider("a", df=pd.DataFrame({"x": [1]}), sleep_s=0.2)
    p2 = _FakeProvider("b", df=pd.DataFrame({"x": [2]}), sleep_s=0.2)
    plan = SplitPlan(
        subqueries=[
            SourceSubquery(provider_id="a", sql="SELECT 1"),
            SourceSubquery(provider_id="b", sql="SELECT 1"),
        ]
    )
    _patch_registry(monkeypatch, [p1, p2])
    _patch_execute_sync(monkeypatch)

    t0 = time.time()
    res = asyncio.run(execute_split_plan(plan, project_slug="proj"))
    elapsed = time.time() - t0
    # Sequential would be ~0.4s; parallel should comfortably be < 0.35s
    assert elapsed < 0.35, f"executor not parallel: {elapsed:.2f}s"
    assert set(res.per_source.keys()) == {"a", "b"}


def test_executor_per_source_timeout(monkeypatch):
    pd = pytest.importorskip("pandas")
    slow = _FakeProvider("a", df=pd.DataFrame({"x": [1]}), sleep_s=1.0)
    plan = SplitPlan(subqueries=[SourceSubquery(provider_id="a", sql="SELECT 1")])
    _patch_registry(monkeypatch, [slow])
    _patch_execute_sync(monkeypatch)

    res = asyncio.run(
        execute_split_plan(plan, project_slug="proj", per_source_timeout_s=0.1)
    )
    assert "a" in res.errors
    assert "timeout" in res.errors["a"]
    assert "a" not in res.per_source


def test_executor_truncated_flag_set_on_cap_hit(monkeypatch):
    pd = pytest.importorskip("pandas")
    big = pd.DataFrame({"x": list(range(10))})
    p = _FakeProvider("a", df=big)
    plan = SplitPlan(subqueries=[SourceSubquery(provider_id="a", sql="SELECT 1")])
    _patch_registry(monkeypatch, [p])
    _patch_execute_sync(monkeypatch)

    res = asyncio.run(
        execute_split_plan(plan, project_slug="proj", per_source_row_cap=10)
    )
    assert res.truncated is True


def test_executor_handles_provider_exception(monkeypatch):
    p = _FakeProvider("a", raise_exc=RuntimeError("boom"))
    plan = SplitPlan(subqueries=[SourceSubquery(provider_id="a", sql="SELECT 1")])
    _patch_registry(monkeypatch, [p])
    _patch_execute_sync(monkeypatch)

    res = asyncio.run(execute_split_plan(plan, project_slug="proj"))
    assert "a" in res.errors
    assert "boom" in res.errors["a"]


# ---------------------------------------------------------------------------
# _add_limit unit tests
# ---------------------------------------------------------------------------


def test_add_limit_postgres_appends_limit():
    out = _add_limit("SELECT * FROM t", "postgres", 100)
    assert out.endswith("LIMIT 100")


def test_add_limit_tsql_uses_top():
    out = _add_limit("SELECT * FROM t", "tsql", 50)
    assert "TOP 50" in out
    assert "LIMIT" not in out.upper()


def test_add_limit_skips_when_already_limited():
    out = _add_limit("SELECT * FROM t LIMIT 5", "postgres", 100)
    assert "LIMIT 100" not in out
