"""Phase 7 Memory + State + RunContext smoke tests."""
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── RunContext ──────────────────────────────────────────────────────────
def test_runcontext_default_run_id():
    from dash.agentic.run_context import RunContext
    rc = RunContext()
    assert rc.run_id.startswith("run_")
    assert len(rc.run_id) > 10


def test_runcontext_as_dict():
    from dash.agentic.run_context import RunContext
    rc = RunContext(project_slug="p1", user_id=42, agent_name="Analyst",
                    trigger_kind="schedule")
    d = rc.as_dict()
    assert d["project_slug"] == "p1"
    assert d["user_id"] == 42
    assert d["trigger_kind"] == "schedule"


def test_set_context_propagates_to_legacy_vars():
    from dash.agentic.run_context import RunContext, set_context, get_context
    from dash.agentic.hooks import current_project_slug, current_user_id, current_run_id
    rc = RunContext(project_slug="proj-x", user_id=99, agent_name="A")
    assert get_context() is None
    with set_context(rc):
        assert get_context() is rc
        assert current_project_slug.get() == "proj-x"
        assert current_user_id.get() == 99
        assert current_run_id.get() == rc.run_id
    # After exit, legacy vars reset
    assert current_project_slug.get() is None
    assert get_context() is None


def test_set_context_resets_on_exception():
    from dash.agentic.run_context import RunContext, set_context, get_context
    rc = RunContext()
    try:
        with set_context(rc):
            raise ValueError("boom")
    except ValueError:
        pass
    assert get_context() is None


# ── Entity memory ───────────────────────────────────────────────────────
def test_entity_remember_no_db(monkeypatch):
    from dash.memory import entity
    monkeypatch.setattr(entity, "_get_engine", lambda: None)
    out = entity.remember("customer", "C1", "prefers email", project_slug="p")
    assert out["ok"] is False
    assert out["error"] == "db_unavailable"


def test_entity_recall_no_db(monkeypatch):
    from dash.memory import entity
    monkeypatch.setattr(entity, "_get_engine", lambda: None)
    assert entity.recall("customer", "C1") == []


def test_entity_semantic_recall_no_db(monkeypatch):
    from dash.memory import entity
    monkeypatch.setattr(entity, "_get_engine", lambda: None)
    assert entity.semantic_recall("customer", "loyal") == []


def test_entity_tool_wrappers_callable():
    from dash.memory.entity import remember_entity_fact, recall_entity_facts
    # Just confirm they're callable (decorated or not)
    fn1 = remember_entity_fact
    fn2 = recall_entity_facts
    for attr in ("__wrapped__", "entrypoint", "fn"):
        if hasattr(fn1, attr):
            fn1 = getattr(fn1, attr)
        if hasattr(fn2, attr):
            fn2 = getattr(fn2, attr)
    assert callable(fn1)
    assert callable(fn2)


# ── Agentic state ───────────────────────────────────────────────────────
def test_agentic_state_no_db_returns_false(monkeypatch):
    from dash.memory import agentic_state
    monkeypatch.setattr(agentic_state, "_get_engine", lambda: None)
    assert agentic_state.set_state("sess1", "Analyst", "k", "v") is False
    assert agentic_state.get_state("sess1", "Analyst", "k") is None
    assert agentic_state.list_state("sess1") == {}
    assert agentic_state.delete_state("sess1", "Analyst", "k") is False
    assert agentic_state.clear_session("sess1") == 0


def test_agentic_state_tools_callable():
    from dash.memory.agentic_state import state_set, state_get, state_list, state_delete
    for fn in (state_set, state_get, state_list, state_delete):
        f = fn
        for attr in ("__wrapped__", "entrypoint", "fn"):
            if hasattr(f, attr):
                f = getattr(f, attr)
        assert callable(f)


# ── Module loads ─────────────────────────────────────────────────────────
def test_memory_api_imports():
    from app import memory_api
    assert memory_api.router is not None


def test_run_context_module_imports():
    from dash.agentic import run_context
    assert hasattr(run_context, "RunContext")
    assert hasattr(run_context, "set_context")
    assert hasattr(run_context, "get_context")
    assert hasattr(run_context, "audit")


def test_run_context_audit_no_db(monkeypatch):
    from dash.agentic.run_context import audit, RunContext
    # Should not raise even when DB unavailable
    audit(RunContext(project_slug="p", user_id=1))
