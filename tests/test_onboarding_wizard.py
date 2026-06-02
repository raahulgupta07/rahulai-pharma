"""Onboarding wizard endpoint tests — POST /api/projects/{slug}/onboard-industry."""
from __future__ import annotations

import asyncio
import json
import pytest


def _setup(monkeypatch):
    pytest.importorskip("fastapi")
    try:
        from app import projects as projects_mod
    except Exception as e:
        pytest.skip(f"app.projects not importable: {e}")

    monkeypatch.setattr(projects_mod, "_get_user",
                        lambda r: {"user_id": 1, "id": 1, "username": "creator"})

    state = {"saved_policy": None, "saved_version": 0,
             "roles": [], "scopes": [], "user_role_assignments": []}

    class _FakeConn:
        def __init__(self, st): self.st = st
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if "FROM public.dash_projects" in sql:
                class _R:
                    def fetchone(self_inner):
                        return (1,)
                return _R()
            if "INSERT INTO public.dash_user_scopes" in sql:
                key = (params["uid"], params["p"], params["sid"])
                existing = [s for s in self.st["scopes"]
                            if (s["uid"], s["p"], s["sid"]) == key]
                if existing:
                    existing[0].update(params)
                else:
                    self.st["scopes"].append(dict(params))
            return None
        def commit(self): pass

    class _FakeEngine:
        def __init__(self, st): self.st = st
        def connect(self): return _FakeConn(self.st)

    monkeypatch.setattr(projects_mod, "_engine", _FakeEngine(state))

    from dash import policy as policy_pkg
    def _fake_save(slug, pol, user_id=None):
        state["saved_policy"] = pol.model_dump() if hasattr(pol, "model_dump") else pol
        state["saved_version"] += 1
        return state["saved_version"]
    monkeypatch.setattr(policy_pkg, "save_policy", _fake_save)

    from dash.policy import roles as roles_mod
    monkeypatch.setattr(roles_mod, "upsert_role",
                        lambda slug, name, intents, desc="": state["roles"].append(
                            {"slug": slug, "name": name, "intents": intents}))
    monkeypatch.setattr(roles_mod, "assign_user_role",
                        lambda uid, slug, name: state["user_role_assignments"].append(
                            {"uid": uid, "slug": slug, "name": name}))

    return projects_mod, state


class _FakeRequest:
    """Minimal stand-in for FastAPI Request — only needs .json() and .state.user."""
    def __init__(self, body: dict):
        self._body = body
        class _S: pass
        self.state = _S()
        self.state.user = {"user_id": 1, "id": 1, "username": "creator"}
    async def json(self):
        return self._body


def _call(projects_mod, slug: str, body: dict):
    """Invoke the async endpoint directly and return (status, json_dict)."""
    from fastapi import HTTPException
    try:
        result = asyncio.run(projects_mod.onboard_industry(slug, _FakeRequest(body)))
        return 200, result
    except HTTPException as e:
        return e.status_code, {"detail": e.detail}


def test_onboard_with_valid_template_saves_policy_and_roles(monkeypatch):
    projects_mod, state = _setup(monkeypatch)
    code, body = _call(projects_mod, "acme", {"template_name": "pharmacy"})
    assert code == 200, body
    assert body["version"] == 1
    assert body["scope_keyword"]
    assert body["roles_seeded"] >= 1
    assert state["saved_policy"] is not None
    assert len(state["roles"]) >= 1


def test_onboard_with_seed_scopes_inserts_rows(monkeypatch):
    projects_mod, state = _setup(monkeypatch)
    code, body = _call(projects_mod, "acme", {
        "template_name": "retail",
        "seed_scopes": [
            {"scope_id": "store_001", "scope_label": "Store 001", "role": "manager"},
            {"scope_id": "store_002", "scope_label": "Store 002", "role": "staff"},
        ],
        "default_role": "manager",
    })
    assert code == 200, body
    assert body["scopes_seeded"] == 2
    assert len(state["scopes"]) == 2
    assert {s["sid"] for s in state["scopes"]} == {"store_001", "store_002"}
    assert any(a["name"] == "manager" for a in state["user_role_assignments"])


def test_onboard_bad_template_returns_404(monkeypatch):
    projects_mod, _ = _setup(monkeypatch)
    code, body = _call(projects_mod, "acme", {"template_name": "no_such_industry"})
    assert code == 404


def test_onboard_idempotent_no_duplicate_scopes(monkeypatch):
    projects_mod, state = _setup(monkeypatch)
    payload = {
        "template_name": "hotel",
        "seed_scopes": [
            {"scope_id": "prop_a", "scope_label": "Property A", "role": "staff"},
        ],
    }
    code1, _ = _call(projects_mod, "acme", payload)
    code2, _ = _call(projects_mod, "acme", payload)
    assert code1 == 200 and code2 == 200
    # Despite two calls, only ONE scope row exists (ON CONFLICT dedup).
    assert len(state["scopes"]) == 1
    assert state["scopes"][0]["sid"] == "prop_a"


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-v"])
