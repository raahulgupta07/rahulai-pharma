"""Phase 4 — visibility roles + audit diff tests."""
from __future__ import annotations

import pytest

from dash.policy import roles as roles_mod
from dash.policy.loader import diff_policies
from dash.policy.schema import AudienceRules, FieldRule, VisibilityPolicy


# ---------------------------------------------------------------------------
# Fake engine — minimal in-memory simulation of the two role tables.
# ---------------------------------------------------------------------------

class _FakeStore:
    def __init__(self):
        self.roles: list[dict] = []   # {project_slug, role_name, allowed_intents, description}
        self.user_roles: list[dict] = []  # {user_id, project_slug, role_name}
        self.users = {1: "alice", 2: "bob"}


class _Result:
    def __init__(self, rows):
        self._rows = rows
    def fetchone(self):
        return self._rows[0] if self._rows else None
    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, store: _FakeStore):
        self.store = store
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def commit(self): pass

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).lower().split())
        p = params or {}
        s = self.store

        if "select role_name, allowed_intents, description" in sql:
            rows = [(r["role_name"], r["allowed_intents"], r["description"])
                    for r in s.roles if r["project_slug"] == p["s"]]
            rows.sort(key=lambda r: r[0])
            return _Result(rows)

        if "select allowed_intents from public.dash_visibility_roles" in sql:
            for r in s.roles:
                if r["project_slug"] == p["s"] and r["role_name"] == p["r"]:
                    return _Result([(r["allowed_intents"],)])
            return _Result([])

        if "insert into public.dash_visibility_roles" in sql:
            for r in s.roles:
                if r["project_slug"] == p["s"] and r["role_name"] == p["r"]:
                    r["allowed_intents"] = p["i"]
                    r["description"] = p["d"]
                    return _Result([])
            s.roles.append({
                "project_slug": p["s"], "role_name": p["r"],
                "allowed_intents": p["i"], "description": p["d"],
            })
            return _Result([])

        if "delete from public.dash_visibility_roles" in sql:
            if "role_name" in sql:
                s.roles[:] = [r for r in s.roles
                              if not (r["project_slug"] == p["s"] and r["role_name"] == p["r"])]
            else:
                s.roles[:] = [r for r in s.roles if r["project_slug"] != p["s"]]
            return _Result([])

        if "select role_name from public.dash_user_roles" in sql:
            rows = [(ur["role_name"],) for ur in s.user_roles
                    if ur["user_id"] == p["u"] and ur["project_slug"] == p["s"]]
            return _Result(rows)

        if "insert into public.dash_user_roles" in sql:
            for ur in s.user_roles:
                if (ur["user_id"] == p["u"] and ur["project_slug"] == p["s"]
                        and ur["role_name"] == p["r"]):
                    return _Result([])
            s.user_roles.append({"user_id": p["u"], "project_slug": p["s"], "role_name": p["r"]})
            return _Result([])

        if "delete from public.dash_user_roles" in sql:
            if "role_name" in sql:
                s.user_roles[:] = [ur for ur in s.user_roles
                                    if not (ur["user_id"] == p["u"]
                                            and ur["project_slug"] == p["s"]
                                            and ur["role_name"] == p["r"])]
            else:
                s.user_roles[:] = [ur for ur in s.user_roles if ur["project_slug"] != p["s"]]
            return _Result([])

        if "select ur.user_id" in sql:
            rows = [(ur["user_id"], s.users.get(ur["user_id"], ""), ur["role_name"])
                    for ur in s.user_roles if ur["project_slug"] == p["s"]]
            rows.sort(key=lambda r: (r[1], r[2]))
            return _Result(rows)

        return _Result([])


class _FakeEngine:
    def __init__(self, store): self.store = store
    def connect(self): return _Conn(self.store)
    def begin(self): return _Conn(self.store)


@pytest.fixture
def fake_engine(monkeypatch):
    store = _FakeStore()
    eng = _FakeEngine(store)
    monkeypatch.setattr(roles_mod, "_engine", lambda: eng)
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_upsert_and_get_role_intents(fake_engine):
    roles_mod.upsert_role("acme", "analyst", ["private", "network"], "data team")
    intents = roles_mod.get_role_intents("acme", "analyst")
    assert "private" in intents and "network" in intents

    # Missing role defaults to private-only.
    assert roles_mod.get_role_intents("acme", "ghost") == ["private"]
    assert roles_mod.get_role_intents("acme", None) == ["private"]


def test_assign_user_role_and_list(fake_engine):
    roles_mod.upsert_role("acme", "viewer", ["private"], "")
    roles_mod.assign_user_role(1, "acme", "viewer")
    rows = roles_mod.list_user_roles("acme")
    assert len(rows) == 1
    assert rows[0]["user_id"] == 1
    assert rows[0]["username"] == "alice"
    assert rows[0]["role_name"] == "viewer"

    assert roles_mod.get_user_role(1, "acme") == "viewer"
    assert roles_mod.get_user_role(99, "acme") is None


def test_diff_policies_added_removed_modified():
    old = VisibilityPolicy(
        network=AudienceRules(fields={
            "revenue": FieldRule(mode="band"),
            "ssn": FieldRule(mode="mask"),
        }),
    )
    new = VisibilityPolicy(
        network=AudienceRules(fields={
            "revenue": FieldRule(mode="hide"),  # modified
            "email": FieldRule(mode="mask"),    # added
            # ssn removed
        }),
        public=AudienceRules(fields={"name": FieldRule(mode="full")}),  # added on new audience
    )
    d = diff_policies(old, new)
    assert d["added"].get("network") == ["email"]
    assert d["removed"].get("network") == ["ssn"]
    assert d["modified"].get("network") == ["revenue"]
    assert d["added"].get("public") == ["name"]


def test_diff_policies_old_none():
    new = VisibilityPolicy(
        private=AudienceRules(fields={"a": FieldRule(mode="hide")}),
    )
    d = diff_policies(None, new)
    assert d["added"]["private"] == ["a"]
    assert d["removed"] == {} and d["modified"] == {}


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-v"])
