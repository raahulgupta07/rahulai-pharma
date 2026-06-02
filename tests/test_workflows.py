"""Phase 3 workflow engine smoke tests."""
import asyncio
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_schema_validate_ok():
    from dash.workflows.schema import validate
    spec = {
        "steps": [
            {"id": "a", "kind": "agent", "agent": "Leader", "prompt": "hi"},
            {"id": "b", "kind": "agent", "agent": "Analyst", "depends_on": ["a"]},
        ]
    }
    ok, errors = validate(spec)
    assert ok
    assert errors == []


def test_schema_validate_missing_agent():
    from dash.workflows.schema import validate
    ok, errors = validate({"steps": [{"id": "a", "kind": "agent"}]})
    assert not ok
    assert any("requires 'agent'" in e for e in errors)


def test_schema_validate_duplicate_id():
    from dash.workflows.schema import validate
    spec = {"steps": [
        {"id": "a", "kind": "agent", "agent": "X"},
        {"id": "a", "kind": "agent", "agent": "Y"},
    ]}
    ok, errors = validate(spec)
    assert not ok
    assert any("duplicate" in e for e in errors)


def test_schema_router_requires_branches():
    from dash.workflows.schema import validate
    ok, errors = validate({"steps": [{"id": "r", "kind": "router"}]})
    assert not ok


def test_schema_loop_requires_until():
    from dash.workflows.schema import validate
    ok, errors = validate({"steps": [{"id": "l", "kind": "loop"}]})
    assert not ok


def test_interpolate_simple():
    from dash.workflows.runner import _interpolate
    out = _interpolate("hello {name}", {"name": "world"})
    assert out == "hello world"


def test_interpolate_nested():
    from dash.workflows.runner import _interpolate
    out = _interpolate("score={r.value}", {"r": {"value": 42}})
    assert "42" in out


def test_safe_eval():
    from dash.workflows.runner import _safe_eval
    assert _safe_eval("x > 5", {"x": 10}) is True
    assert _safe_eval("x > 5", {"x": 1}) is False


def test_runner_executes_minimal_dag(monkeypatch):
    from dash.workflows import runner

    # Stub DB writes
    monkeypatch.setattr(runner, "_insert_run", lambda *a, **kw: "wfr_test")
    monkeypatch.setattr(runner, "_update_run", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "_insert_step_row", lambda *a, **kw: 1)
    monkeypatch.setattr(runner, "_finalize_step_row", lambda *a, **kw: None)

    # Stub agent exec to echo interpolated prompt
    from dash.workflows.runner import _interpolate as _ip
    async def _stub_agent(step, ctx):
        return {"result": _ip(step.get("prompt") or "", ctx), "agent": step.get("agent")}

    monkeypatch.setattr(runner, "_exec_agent", _stub_agent)

    def_row = {
        "id": "wf_test", "project_slug": None,
        "spec": {
            "steps": [
                {"id": "a", "kind": "agent", "agent": "L1", "prompt": "hello"},
                {"id": "b", "kind": "agent", "agent": "L2",
                 "depends_on": ["a"], "prompt": "from {a.result}"},
            ],
            "outputs": ["b"],
        },
    }

    result = asyncio.run(runner.execute_workflow(def_row))
    assert result["ok"] is True
    assert "b" in result["output"]
    assert "hello" in str(result["output"]["b"])


def test_runner_parallel_group(monkeypatch):
    from dash.workflows import runner
    monkeypatch.setattr(runner, "_insert_run", lambda *a, **kw: "wfr_p")
    monkeypatch.setattr(runner, "_update_run", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "_insert_step_row", lambda *a, **kw: 1)
    monkeypatch.setattr(runner, "_finalize_step_row", lambda *a, **kw: None)

    call_order = []

    async def _stub(step, ctx):
        call_order.append(step["id"])
        await asyncio.sleep(0.01)
        return step["id"]

    monkeypatch.setattr(runner, "_exec_agent", _stub)
    def_row = {
        "id": "wf_par",
        "spec": {
            "steps": [
                {"id": "x", "kind": "agent", "agent": "A", "parallel_group": "g1"},
                {"id": "y", "kind": "agent", "agent": "A", "parallel_group": "g1"},
                {"id": "z", "kind": "agent", "agent": "A", "parallel_group": "g1"},
                {"id": "join", "kind": "agent", "agent": "A",
                 "depends_on": ["x", "y", "z"]},
            ]
        },
    }
    result = asyncio.run(runner.execute_workflow(def_row))
    assert result["ok"]
    assert "join" in call_order
    # parallel group should fire before join
    assert call_order.index("join") > 2


def test_runner_condition_skip(monkeypatch):
    from dash.workflows import runner
    monkeypatch.setattr(runner, "_insert_run", lambda *a, **kw: "wfr_c")
    monkeypatch.setattr(runner, "_update_run", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "_insert_step_row", lambda *a, **kw: 1)
    monkeypatch.setattr(runner, "_finalize_step_row", lambda *a, **kw: None)

    async def _stub(step, ctx):
        return {"ran": step["id"]}

    monkeypatch.setattr(runner, "_exec_agent", _stub)
    def_row = {
        "id": "wf_cond",
        "spec": {
            "inputs": {"flag": False},
            "steps": [
                {"id": "a", "kind": "agent", "agent": "A", "prompt": "x"},
                {"id": "skipped", "kind": "agent", "agent": "A",
                 "depends_on": ["a"], "condition": "flag"},
                {"id": "always", "kind": "agent", "agent": "A",
                 "depends_on": ["a"]},
            ]
        },
    }
    result = asyncio.run(runner.execute_workflow(def_row))
    assert result["ok"]
    # "skipped" should not appear in output (was skipped by condition)
    assert "skipped" not in (result["output"] or {}) or result["output"].get("skipped") is None


def test_hitl_auto_approve_when_flag_off(monkeypatch):
    from dash.workflows import runner
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    result = asyncio.run(runner._exec_hitl({"id": "h"}, {}, "wfr_h"))
    assert result["ok"]
    assert result.get("auto_approved")


def test_builtins_list_has_5():
    from dash.workflows.builtin import BUILTINS
    assert len(BUILTINS) == 5
    ids = {b["id"] for b in BUILTINS}
    assert ids == {
        "wf_morning_brief", "wf_daily_research", "wf_content_pipeline",
        "wf_doc_walkthrough", "wf_support_triage",
    }


def test_builtin_specs_validate():
    from dash.workflows.builtin import BUILTINS
    from dash.workflows.schema import validate
    for b in BUILTINS:
        ok, errors = validate(b["spec"])
        assert ok, f"{b['id']} invalid: {errors}"


def test_workflows_api_imports():
    from app import workflows_api
    assert workflows_api.router is not None
