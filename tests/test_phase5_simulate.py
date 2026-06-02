"""Phase 5A — simulator + validation tests."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="dash.policy.simulator removed in Phase I sim refactor")

try:
    from dash.policy import simulator as sim
    from dash.policy.schema import AudienceRules, FieldRule, VisibilityPolicy
except Exception:  # module removed in Phase I sim refactor
    sim = AudienceRules = FieldRule = VisibilityPolicy = None


# ---- helpers --------------------------------------------------------------


class _StubResult:
    def __init__(self, cols, rows):
        self._cols = cols
        self._rows = rows
    def keys(self):
        return self._cols
    def __iter__(self):
        return iter(self._rows)


class _StubConn:
    def __init__(self, cols, rows):
        self._cols = cols
        self._rows = rows
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, *a, **k):
        return _StubResult(self._cols, self._rows)


class _StubEngine:
    def __init__(self, cols, rows):
        self._cols = cols
        self._rows = rows
    def connect(self):
        return _StubConn(self._cols, self._rows)


def _patch_common(monkeypatch, cols, rows, role=None, intents=None):
    monkeypatch.setattr(sim, "get_user_role", lambda u, s: role)
    monkeypatch.setattr(sim, "get_role_intents", lambda s, r: intents or ["private", "network", "public"])
    monkeypatch.setattr(sim, "_scope_user_attrs", lambda u, s, sid: {})
    monkeypatch.setattr(sim, "load_policy", lambda s: None)
    # neutralize RLS rewrite
    import dash.rls.rewriter as rew
    monkeypatch.setattr(rew, "rewrite", lambda sql, slug, attrs: sql)
    # neutralize set_request_context
    import dash.tools.skill_refinery as skr
    monkeypatch.setattr(skr, "set_request_context", lambda **kw: None)
    # stub project engine
    import db
    monkeypatch.setattr(db, "get_project_readonly_engine", lambda s: _StubEngine(cols, rows))


# ---- tests ----------------------------------------------------------------


def test_simulate_no_policy_returns_rows_unchanged(monkeypatch):
    _patch_common(monkeypatch, ["a", "b"], [(1, 2), (3, 4)])
    res = sim.simulate("acme", 1, "", "private", "SELECT a, b FROM t")
    assert res["error"] is None
    assert res["rewritten_sql"] == "SELECT a, b FROM t"
    assert res["rows"] == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert res["downgraded_fields"] == []


def test_simulate_band_policy_network_produces_case(monkeypatch):
    _patch_common(monkeypatch, ["band_revenue"], [("low",)])
    draft = {
        "network": {
            "fields": {
                "revenue": {"mode": "band", "bands": [{"name": "low", "max": 100}, {"name": "high"}]}
            }
        }
    }
    res = sim.simulate("acme", 1, "", "network", "SELECT revenue FROM t", draft_policy=draft)
    assert res["error"] is None
    assert "CASE" in res["rewritten_sql"]
    assert "revenue" in res["downgraded_fields"]
    assert res["allowed_intent"] == "network"


def test_validate_policy_no_tables_returns_ok(monkeypatch):
    monkeypatch.setattr(sim, "_first_table", lambda s: None)
    res = sim.validate_policy("acme", VisibilityPolicy())
    assert res["ok"] is True
    assert res["failures"] == []
    assert any("no tables" in w for w in res["warnings"])


def test_validate_policy_no_roles_returns_ok(monkeypatch):
    monkeypatch.setattr(sim, "_first_table", lambda s: "sales")
    monkeypatch.setattr(sim, "list_roles", lambda s: [])
    monkeypatch.setattr(sim, "list_user_roles", lambda s: [])
    res = sim.validate_policy("acme", VisibilityPolicy())
    assert res["ok"] is True
    assert res["failures"] == []


def test_capped_intent_forces_lower_permissive(monkeypatch):
    _patch_common(monkeypatch, ["a"], [(1,)], role="staff", intents=["private"])
    res = sim.simulate("acme", 1, "", "public", "SELECT a FROM t")
    assert res["allowed_intent"] == "private"
    assert res["capped_intent"] is True


# ---- PUT endpoint tests (via FastAPI TestClient if importable) -----------


def test_put_validation_failure_returns_422(monkeypatch):
    """Mock validate_policy to return failures; PUT without force=true → 422."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    try:
        from app import learning as learning_mod
    except Exception as e:
        pytest.skip(f"app.learning not importable in this env: {e}")

    monkeypatch.setattr(learning_mod, "_get_user", lambda r: {"id": 1, "username": "admin"})
    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "check_project_permission",
                        lambda user, slug, role=None: True)

    # Force validate_policy to fail
    from dash import policy as policy_pkg
    monkeypatch.setattr(policy_pkg, "validate_policy",
                        lambda slug, pol: {"ok": False, "failures": [{"role": "x", "scope_id": "", "intent": "private", "sql": "SELECT 1", "error": "bad"}], "warnings": []})

    # Stub save_policy so the force-true path can succeed without DB
    saved = {}
    def _fake_save(slug, pol, user_id=None):
        saved["v"] = 1
        return 1
    monkeypatch.setattr(policy_pkg, "save_policy", _fake_save)

    app = FastAPI()
    app.include_router(learning_mod.router)
    client = TestClient(app)

    # bad policy → 422
    r = client.put("/api/projects/acme/visibility-policy",
                   json={"policy": {}})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["ok"] is False
    assert "failures" in body

    # force=true → 200
    r2 = client.put("/api/projects/acme/visibility-policy?force=true",
                    json={"policy": {}})
    assert r2.status_code == 200, r2.text
    assert r2.json()["version"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-v"])
