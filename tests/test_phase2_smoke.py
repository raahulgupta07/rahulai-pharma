"""Dash-OS Phase 2 smoke tests — import + EXPERIMENTAL_AGI gating.

Real DB / API calls mocked; verifies modules load and gate behaviors
behave when flag is off.
"""
import os
import sys
import pathlib

# Ensure flag is off for gate tests (per-test override below)
os.environ.pop("EXPERIMENTAL_AGI", None)

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_file_generation_imports():
    from dash.tools import file_generation
    assert callable(file_generation._make_json)
    assert callable(file_generation._make_md)


def test_make_json_writes_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("DASH_GENERATED_ROOT", str(tmp_path))
    # Force engine to None so DB write is skipped
    from dash.tools import file_generation
    monkeypatch.setattr(file_generation, "_get_engine", lambda: None)
    result = file_generation._make_json({"hello": "world"})
    assert result["ok"] is True
    assert result["file_type"] == "json"
    assert "/api/reporter/files/" in result["download_url"]


def test_make_csv_no_pandas(monkeypatch):
    from dash.tools import file_generation
    # Simulate missing pandas
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "pandas":
            raise ImportError("no pandas")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = file_generation._make_csv([{"a": 1}])
    assert result["ok"] is False
    assert "pandas" in result["error"]




def test_mcp_make_agno_tools_off_when_flag_off(monkeypatch):
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    from dash.tools import mcp_client
    row = {"id": "mcp_test", "name": "test", "discovered_tools": [{"name": "x"}]}
    assert mcp_client.make_agno_tools(row, "Analyst") == []


def test_mcp_make_agno_tools_on_when_flag_on(monkeypatch):
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")
    from dash.tools import mcp_client
    monkeypatch.setattr(mcp_client, "_enabled_tool_names", lambda sid, ag: None)
    row = {
        "id": "mcp_test", "name": "Test Srv",
        "discovered_tools": [{"name": "search", "description": "Search"}],
    }
    tools = mcp_client.make_agno_tools(row, "Analyst")
    assert len(tools) == 1


def test_compressor_off_passthrough(monkeypatch):
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    from dash.tools import compressor
    mgr = compressor.CompressionManager()
    import asyncio
    inputs = [{"url": "a", "body": "x" * 1000}]
    out = asyncio.run(mgr.compress_search_results(inputs))
    assert out == inputs


def test_compressor_dedup(monkeypatch):
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")
    from dash.tools import compressor

    # Mock cache + LLM to no-ops
    monkeypatch.setattr(compressor, "_cache_get", lambda k: None)
    monkeypatch.setattr(compressor, "_cache_put", lambda *a, **kw: None)
    monkeypatch.setattr(compressor, "_log_stats", lambda **kw: None)
    monkeypatch.setattr(compressor, "_llm_compress", lambda t, q, n: ("comp", "mock", 0.0))

    mgr = compressor.CompressionManager()
    import asyncio
    inputs = [
        {"url": "https://x.com/a", "body": "body1"},
        {"url": "https://x.com/a", "body": "body1"},  # dedup
    ]
    out = asyncio.run(mgr.compress_search_results(inputs, query_intent="q"))
    assert mgr.stats()["dedup_skipped"] == 1
    assert len(out) == 1


def test_scheduler_off_returns_disabled(monkeypatch):
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    from dash.tools import scheduler_tools
    result = scheduler_tools._schedule_recurring(
        name="test", cron="0 9 * * 1", prompt="hi",
    )
    assert result["ok"] is False
    assert result["reason"] == "scheduler_disabled"


def test_scheduler_invalid_cron_when_on(monkeypatch):
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")
    from dash.tools import scheduler_tools
    result = scheduler_tools._schedule_recurring(
        name="test", cron="not a cron", prompt="hi",
    )
    assert result["ok"] is False
    assert "invalid cron" in result["error"]


def test_scheduler_cron_validator():
    from dash.tools import scheduler_tools
    assert scheduler_tools._validate_cron("0 9 * * 1")
    assert scheduler_tools._validate_cron("*/5 * * * *")
    assert not scheduler_tools._validate_cron("garbage")


def test_routers_import():
    from app import reporter_api, mcp_api, agent_schedules_api
    assert reporter_api.router is not None
    assert mcp_api.router is not None
    assert agent_schedules_api.router is not None


def test_schedule_runner_disabled_paths(monkeypatch):
    from dash.cron import agent_schedule_runner
    monkeypatch.setenv("AGENT_SCHEDULE_RUNNER_DISABLED", "1")
    assert agent_schedule_runner._disabled() is True
    monkeypatch.delenv("AGENT_SCHEDULE_RUNNER_DISABLED")
    monkeypatch.setenv("K8S_DAEMON_MODE", "cronjob")
    assert agent_schedule_runner._disabled() is True
    monkeypatch.delenv("K8S_DAEMON_MODE")
    monkeypatch.setenv("DAEMONS_DISABLED", "1")
    assert agent_schedule_runner._disabled() is True
