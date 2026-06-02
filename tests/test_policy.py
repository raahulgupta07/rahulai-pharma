"""Phase 2A — visibility policy core tests."""
from __future__ import annotations

import pytest

from dash.policy.engine import PolicyEngine
from dash.policy.schema import AudienceRules, FieldRule, VisibilityPolicy
from dash.policy.transforms import band_expr
from dash.policy import loader as policy_loader


def test_load_policy_unknown_slug_returns_none(monkeypatch):
    # Stub engine so we never touch a real DB; mimic empty result.
    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k):
            class _R:
                def fetchone(self_inner): return None
            return _R()

    class _Eng:
        def connect(self): return _Conn()

    monkeypatch.setattr(policy_loader, "_engine", lambda: _Eng())
    policy_loader.invalidate_cache("ghost-slug-xyz")
    assert policy_loader.load_policy("ghost-slug-xyz") is None


def test_save_then_load_roundtrip(monkeypatch):
    # In-memory fake of the policy table.
    store: dict[str, dict] = {}

    class _Result:
        def __init__(self, row): self._row = row
        def fetchone(self): return self._row

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, stmt, params=None):
            sql = str(stmt).lower()
            params = params or {}
            if "select policy_json" in sql:
                row = store.get(params.get("s"))
                return _Result((row["policy_json"], row["version"]) if row else None)
            if "select version from public.dash_visibility_policy" in sql:
                row = store.get(params.get("s"))
                return _Result((row["version"],) if row else None)
            if "insert into public.dash_visibility_policy_history" in sql:
                return _Result(None)
            if "insert into public.dash_visibility_policy" in sql:
                store[params["s"]] = {"version": params["v"], "policy_json": params["p"]}
                return _Result(None)
            return _Result(None)
        def commit(self): pass

    class _Eng:
        def connect(self): return _Conn()

    monkeypatch.setattr(policy_loader, "_engine", lambda: _Eng())
    policy_loader.invalidate_cache("acme")

    pol = VisibilityPolicy(
        network=AudienceRules(fields={"revenue": FieldRule(mode="band", bands=[{"name": "low", "max": 100}])}),
    )
    v = policy_loader.save_policy("acme", pol, user_id=1)
    assert v == 1
    loaded = policy_loader.load_policy("acme")
    assert loaded is not None
    assert "revenue" in loaded.network.fields
    assert loaded.network.fields["revenue"].mode == "band"


def test_band_transform_produces_valid_case():
    out = band_expr("revenue", [{"name": "low", "max": 10}, {"name": "med", "max": 100}, {"name": "high"}])
    assert out.startswith("CASE")
    assert "WHEN revenue <= 10 THEN 'low'" in out
    assert "WHEN revenue <= 100 THEN 'med'" in out
    assert "ELSE 'high' END AS revenue" in out


def test_hide_drops_col_from_select():
    eng = PolicyEngine()
    pol = VisibilityPolicy(
        public=AudienceRules(fields={"b": FieldRule(mode="hide")}),
    )
    out, downgraded = eng.apply("SELECT a, b, c FROM t", pol, "public")
    assert "b" in downgraded
    assert " b" not in (" " + out.split("FROM")[0])
    assert "a" in out and "c" in out


def test_private_intent_passes_through_unchanged():
    eng = PolicyEngine()
    pol = VisibilityPolicy(
        network=AudienceRules(fields={"x": FieldRule(mode="mask")}),
    )
    sql = "SELECT x, y FROM t WHERE z=1"
    out, downgraded = eng.apply(sql, pol, "private")
    assert out == sql
    assert downgraded == []


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-v"])
