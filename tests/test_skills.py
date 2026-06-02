"""Phase 4 Skills system smoke tests."""
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_builtins_count_ten():
    from dash.skills.builtin import BUILTIN_SKILLS
    assert len(BUILTIN_SKILLS) == 10


def test_builtins_have_required_fields():
    from dash.skills.builtin import BUILTIN_SKILLS
    required = {"id", "name", "category", "trigger_keywords", "instructions"}
    for s in BUILTIN_SKILLS:
        missing = required - set(s.keys())
        assert not missing, f"{s.get('name')} missing fields: {missing}"
        assert s["instructions"].strip(), f"{s['name']} has empty instructions"
        assert len(s["trigger_keywords"]) >= 2, f"{s['name']} needs ≥2 trigger keywords"


def test_register_skill_in_proc_when_no_db(monkeypatch):
    from dash.skills import registry
    monkeypatch.setattr(registry, "_get_engine", lambda: None)
    sid = registry.register_skill({
        "name": "test-skill", "category": "meta",
        "trigger_keywords": ["test"], "instructions": "do the thing",
    })
    assert sid.startswith("skl_")
    assert sid in registry._in_proc_skills


def test_find_skills_for_keyword_match(monkeypatch):
    from dash.skills import registry
    monkeypatch.setattr(registry, "_get_engine", lambda: None)
    registry._in_proc_skills.clear()
    registry.register_skill({
        "name": "sql-skill", "category": "engineering",
        "trigger_keywords": ["slow query", "explain"],
        "instructions": "optimize",
    })
    registry.register_skill({
        "name": "chart-skill", "category": "analytics",
        "trigger_keywords": ["chart", "visualize"],
        "instructions": "design",
    })
    out = registry.find_skills_for("My slow query is killing perf", top_k=3)
    assert len(out) >= 1
    assert any(s["name"] == "sql-skill" for s in out)


def test_load_skill_off_returns_stub(monkeypatch):
    from dash.skills import registry
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    monkeypatch.setattr(registry, "_get_engine", lambda: None)
    registry._in_proc_skills.clear()
    sid = registry.register_skill({
        "name": "x", "instructions": "long text " * 100,
    })
    result = registry.load_skill(sid)
    assert result["ok"]
    assert result["stub"] is True
    assert result["loaded_chars"] == 0


def test_load_skill_on_returns_full_body(monkeypatch):
    from dash.skills import registry
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")
    monkeypatch.setattr(registry, "_get_engine", lambda: None)
    registry._in_proc_skills.clear()
    sid = registry.register_skill({
        "name": "y", "instructions": "DO X THEN Y",
    })
    result = registry.load_skill(sid)
    assert result["ok"]
    assert result.get("stub") is None
    assert "DO X" in result["instructions"]
    assert result["loaded_chars"] > 0


def test_skill_decorator_registers():
    from dash.skills.registry import skill, _in_proc_skills

    @skill(name="decorated-skill", category="meta", trigger_keywords=["foo"])
    def my_skill():
        """Skill body via docstring."""
        return None

    assert any(s["name"] == "decorated-skill" for s in _in_proc_skills.values())


def test_skills_api_imports():
    from app import skills_api
    assert skills_api.router is not None


def test_discover_skills_tool(monkeypatch):
    from dash.skills import registry
    monkeypatch.setattr(registry, "_get_engine", lambda: None)
    registry._in_proc_skills.clear()
    registry.register_skill({
        "name": "test-prompt", "category": "meta",
        "trigger_keywords": ["prompt engineering"],
        "instructions": "x",
    })
    # discover_skills is decorated with @tool — may be wrapped; call the inner
    fn = registry.discover_skills
    if hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    if hasattr(fn, "entrypoint"):
        fn = fn.entrypoint
    if hasattr(fn, "fn"):
        fn = fn.fn
    result = fn(question="help with prompt engineering", top_k=3)
    assert result["ok"]
    assert any(s["name"] == "test-prompt" for s in result["skills"])
