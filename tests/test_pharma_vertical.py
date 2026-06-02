"""Pharma vertical SKU bundle tests."""
from __future__ import annotations

import asyncio
import pytest


# ---------------------------------------------------------------------------
# Test 1: BUNDLE shape
# ---------------------------------------------------------------------------

def test_pharma_bundle_shape():
    from dash.verticals import get_vertical, list_verticals
    bundle = get_vertical("pharma")
    assert bundle is not None
    assert bundle["label"] == "Pharmacy"
    assert bundle["icon"]
    assert bundle["description"]
    assert bundle.get("visibility_template") == "pharmacy"
    # Brain: aliases (>=30) + glossary (10) + pattern (5+5=10) + formula (5) → >=55
    entries = bundle["brain_entries"]
    assert isinstance(entries, list) and len(entries) >= 50
    cats = {e["category"] for e in entries}
    assert {"alias", "glossary", "pattern", "formula"}.issubset(cats)
    aliases = [e for e in entries if e["category"] == "alias"]
    assert len(aliases) >= 30
    forms = [e for e in entries if e["category"] == "glossary"]
    assert len(forms) == 10
    # Workflows: 8, each with steps
    wfs = bundle["workflows"]
    assert len(wfs) == 8
    for wf in wfs:
        assert wf["name"]
        assert wf["description"]
        assert isinstance(wf["steps"], list) and len(wf["steps"]) >= 3
        for step in wf["steps"]:
            assert step["type"] and step["prompt"]
    # list_verticals exposes pharma
    listed = list_verticals()
    names = {v["name"] for v in listed}
    assert "pharma" in names


# ---------------------------------------------------------------------------
# Shared fixture for endpoint tests
# ---------------------------------------------------------------------------

class _FakeRequest:
    def __init__(self, body: dict):
        self._body = body
        class _S: pass
        self.state = _S()
        self.state.user = {"user_id": 1, "id": 1, "username": "creator"}

    async def json(self):
        return self._body


def _setup(monkeypatch):
    pytest.importorskip("fastapi")
    try:
        from app import projects as projects_mod
    except Exception as e:
        pytest.skip(f"app.projects not importable: {e}")

    monkeypatch.setattr(projects_mod, "_get_user",
                        lambda r: {"user_id": 1, "id": 1, "username": "creator"})

    state = {"brain": [], "workflows": []}

    class _FakeConn:
        def __init__(self, st): self.st = st
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def execute(self, stmt, params=None):
            sql = str(stmt)
            params = params or {}

            if "FROM public.dash_projects WHERE slug" in sql:
                class _R:
                    def fetchone(self_inner): return (1,)
                return _R()

            if "INSERT INTO public.dash_company_brain" in sql:
                key = (params.get("slug"), params.get("name"))
                # Simulate ON CONFLICT (project_slug, name) DO NOTHING
                if any((b["slug"], b["name"]) == key for b in self.st["brain"]):
                    class _R:
                        def fetchone(self_inner): return None
                    return _R()
                self.st["brain"].append(dict(params))
                class _R:
                    def fetchone(self_inner): return (len(self.st["brain"]),)
                return _R()

            if "FROM public.dash_workflows_db" in sql and "SELECT 1" in sql:
                key = (params.get("s"), params.get("n"), params.get("src"))
                hit = any((w["s"], w["n"], w["src"]) == key for w in self.st["workflows"])
                class _R:
                    def fetchone(self_inner): return (1,) if hit else None
                return _R()

            if "INSERT INTO public.dash_workflows_db" in sql:
                self.st["workflows"].append(dict(params))
                class _R:
                    def fetchone(self_inner): return None
                return _R()

            class _R:
                def fetchone(self_inner): return None
            return _R()

        def commit(self): pass

    class _FakeEngine:
        def __init__(self, st): self.st = st
        def connect(self): return _FakeConn(self.st)

    monkeypatch.setattr(projects_mod, "_engine", _FakeEngine(state))

    # Stub auth helpers
    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "SUPER_ADMIN", "creator", raising=False)
    monkeypatch.setattr(auth_mod, "check_project_permission",
                        lambda u, s, r: True, raising=False)

    # Stub policy module so visibility template apply is a no-op
    try:
        from dash import policy as policy_pkg
        monkeypatch.setattr(policy_pkg, "save_policy",
                            lambda slug, pol, user_id=None: 1)
    except Exception:
        pass

    return projects_mod, state


def _call(projects_mod, slug: str, body: dict):
    from fastapi import HTTPException
    try:
        result = asyncio.run(projects_mod.apply_vertical(slug, _FakeRequest(body)))
        return 200, result
    except HTTPException as e:
        return e.status_code, {"detail": e.detail}


# ---------------------------------------------------------------------------
# Test 2: apply-vertical inserts brain + workflows
# ---------------------------------------------------------------------------

def test_apply_vertical_seeds_brain_and_workflows(monkeypatch):
    projects_mod, state = _setup(monkeypatch)
    code, body = _call(projects_mod, "acme", {"vertical_name": "pharma"})
    assert code == 200, body
    assert body["vertical_label"] == "Pharmacy"
    assert body["brain_seeded"] >= 50
    assert body["workflows_seeded"] == 8
    assert len(state["brain"]) >= 50
    assert len(state["workflows"]) == 8
    # All marked with vertical_pharma source in metadata
    for b in state["brain"]:
        assert "vertical_pharma" in b["meta"]
    for w in state["workflows"]:
        assert w["src"] == "vertical_pharma"


# ---------------------------------------------------------------------------
# Test 3: idempotent — call twice, count stays same
# ---------------------------------------------------------------------------

def test_apply_vertical_idempotent(monkeypatch):
    projects_mod, state = _setup(monkeypatch)
    code1, body1 = _call(projects_mod, "acme", {"vertical_name": "pharma"})
    brain_after_1 = len(state["brain"])
    wf_after_1 = len(state["workflows"])
    code2, body2 = _call(projects_mod, "acme", {"vertical_name": "pharma"})
    assert code1 == 200 and code2 == 200
    # Second call: nothing new inserted
    assert len(state["brain"]) == brain_after_1
    assert len(state["workflows"]) == wf_after_1
    assert body2["brain_seeded"] == 0
    assert body2["workflows_seeded"] == 0


# ---------------------------------------------------------------------------
# Test 4: bad vertical name → 404
# ---------------------------------------------------------------------------

def test_apply_vertical_bad_name_returns_404(monkeypatch):
    projects_mod, _ = _setup(monkeypatch)
    code, body = _call(projects_mod, "acme", {"vertical_name": "not_a_real_vertical"})
    assert code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-v"])
