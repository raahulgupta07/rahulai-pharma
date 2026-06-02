"""Phase 2B integration tests for visibility policy.

We stub `dash.policy` (Track 2A's module) so these tests exercise the
Phase 2B integration glue without depending on the policy implementation.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub `dash.policy` once for the whole module.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _stub_policy_module():
    if "dash.policy" not in sys.modules:
        mod = types.ModuleType("dash.policy")
        _store: dict = {}

        class VisibilityPolicy:
            def __init__(self, **kw):
                self.rules = kw.get("rules", [])
                self.version = kw.get("version", 0)

            def model_dump(self):
                return {"rules": self.rules, "version": self.version}

        def load_policy(slug):
            return _store.get(slug)

        def save_policy(slug, policy):
            ver = (policy.version or 0) + 1
            policy.version = ver
            _store[slug] = policy
            return ver

        def apply_policy(sql, project_slug, intent):
            pol = _store.get(project_slug)
            if not pol or intent == "private":
                return sql, []
            for rule in (pol.rules or []):
                col = rule.get("field")
                if col and col in sql:
                    sql = sql.replace(
                        col,
                        f"CASE WHEN TRUE THEN '<band>' ELSE {col} END AS {col}",
                        1,
                    )
                    return sql, [col]
            return sql, []

        mod.VisibilityPolicy = VisibilityPolicy
        mod.load_policy = load_policy
        mod.save_policy = save_policy
        mod.apply_policy = apply_policy
        sys.modules["dash.policy"] = mod
    yield


# ---------------------------------------------------------------------------
# Lazy import of app.learning — guard against env-only failures (pgvector etc).
# ---------------------------------------------------------------------------

@pytest.fixture
def learning_mod(monkeypatch):
    pytest.importorskip("fastapi")
    # Pre-stub dash.settings to dodge pgvector bootstrap in dev envs.
    if "dash.settings" not in sys.modules:
        s = types.ModuleType("dash.settings")
        s.TRAINING_MODEL = "stub"
        s.DEEP_MODEL = "stub"
        s.LITE_MODEL = "stub"
        sys.modules["dash.settings"] = s
    try:
        from app import learning as mod
    except Exception as e:  # pragma: no cover
        pytest.skip(f"app.learning import failed in this env: {e}")
    monkeypatch.setattr(mod, "_get_user", lambda req: {"id": 1, "username": "t"})
    monkeypatch.setattr(mod, "_check_access", lambda u, s: None)
    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "check_project_permission",
                        lambda u, s, role=None: True)
    return mod


def _fake_request(body: dict | None = None):
    """Minimal Request stub exposing async .json()."""
    req = MagicMock()

    async def _json():
        return body or {}
    req.json = _json
    return req


def test_put_then_get_visibility_policy(learning_mod):
    body = {"policy": {"rules": [{"field": "salary", "rule": "band"}]}}
    res = asyncio.run(learning_mod.visibility_policy_put("demo", _fake_request(body)))
    assert res["status"] == "ok"
    v = res["version"]
    assert v >= 1

    got = learning_mod.visibility_policy_get("demo", _fake_request())
    assert got["version"] == v
    assert got["policy"]["rules"][0]["field"] == "salary"


def test_policy_test_endpoint_returns_case(learning_mod):
    asyncio.run(learning_mod.visibility_policy_put(
        "demo2", _fake_request({"policy": {"rules": [{"field": "salary", "rule": "band"}]}})))
    out = asyncio.run(learning_mod.visibility_policy_test(
        "demo2",
        _fake_request({"sql": "SELECT salary FROM emp",
                       "intent": "network", "scope_id": "x"}),
    ))
    assert "CASE" in out["rewritten_sql"]
    assert "salary" in out["downgraded_fields"]


def test_rewrite_listener_invokes_policy(monkeypatch):
    pytest.importorskip("sqlalchemy")

    from dash.tools import skill_refinery
    from dash.tools import build as build_mod
    from dash.rls import audit as audit_mod

    import dash.rls.rewriter as rls_rewriter
    monkeypatch.setattr(rls_rewriter, "rewrite", lambda sql, slug, attrs: sql)

    captured: dict = {}

    def fake_log(project_slug, original_sql, **kw):
        captured["slug"] = project_slug
        captured["fields_downgraded"] = kw.get("fields_downgraded")
        captured["mode"] = kw.get("mode")

    monkeypatch.setattr(audit_mod, "log_rls_event", fake_log)

    import dash.policy as pol_mod
    pol = pol_mod.VisibilityPolicy(rules=[{"field": "salary", "rule": "band"}])
    pol_mod.save_policy("demo", pol)

    holder: dict = {}

    class _FakeEvent:
        @staticmethod
        def listens_for(target, name, retval=False):
            def deco(fn):
                holder["fn"] = fn
                return fn
            return deco

    monkeypatch.setattr(build_mod, "_sa_event", _FakeEvent)

    class _FakeEng:
        pass

    build_mod._attach_rls_rewrite(_FakeEng(), "demo")
    listener = holder["fn"]

    skill_refinery.set_request_context(query_intent="network")

    new_sql, params = listener(None, None, "SELECT salary FROM emp", {}, None, False)
    assert "CASE" in new_sql
    assert captured.get("fields_downgraded") == ["salary"]
    assert captured.get("slug") == "demo"
