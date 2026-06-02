"""Sign-off workflow — 2-admin approval gate before policy publish."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from dash.policy import signoff


# ---------------------------------------------------------------------------
# In-memory engine stub — mimics SQLAlchemy connect/execute/commit pattern.
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, mapping: dict):
        self._mapping = mapping
    def __getitem__(self, i):
        return list(self._mapping.values())[i]


class _Result:
    def __init__(self, rows):
        self._rows = rows
    def fetchone(self):
        return self._rows[0] if self._rows else None
    def fetchall(self):
        return list(self._rows)


class FakeEngine:
    def __init__(self):
        self.drafts: dict[int, dict] = {}
        self.policies: dict[str, dict] = {}  # project_slug -> latest policy snapshot
        self._next_id = 1

    def connect(self):
        return _FakeConn(self)


class _FakeConn:
    def __init__(self, eng):
        self.eng = eng
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def commit(self):
        pass

    def execute(self, stmt, params=None):
        sql = str(getattr(stmt, "text", stmt)).strip()
        params = params or {}

        if sql.startswith("INSERT INTO public.dash_visibility_policy_drafts"):
            did = self.eng._next_id
            self.eng._next_id += 1
            self.eng.drafts[did] = {
                "id": did,
                "project_slug": params["s"],
                "policy_json": json.loads(params["p"]),
                "status": "draft",
                "created_by": params["u"],
                "created_at": datetime.now(timezone.utc),
                "submitted_at": None,
                "approvals": [],
                "required_approvals": 2,
                "comment": params.get("c") or "",
            }
            return _Result([_Row({"id": did})])

        if sql.startswith("UPDATE public.dash_visibility_policy_drafts"):
            did = params.get("id")
            d = self.eng.drafts.get(did)
            if d is None:
                return _Result([])
            if "status='pending'" in sql:
                if d["status"] in ("draft", "rejected"):
                    d["status"] = "pending"
                    d["submitted_at"] = datetime.now(timezone.utc)
            elif "status='published'" in sql:
                d["status"] = "published"
            elif "status='rejected'" in sql:
                d["status"] = "rejected"
            elif "approvals = CAST" in sql:
                d["approvals"] = json.loads(params["a"])
            return _Result([])

        if sql.startswith("SELECT id, project_slug, policy_json, status, created_by,\n                   approvals, required_approvals"):
            d = self.eng.drafts.get(params["id"])
            if not d:
                return _Result([])
            return _Result([_Row({
                "id": d["id"], "project_slug": d["project_slug"],
                "policy_json": d["policy_json"], "status": d["status"],
                "created_by": d["created_by"],
                "approvals": d["approvals"],
                "required_approvals": d["required_approvals"],
            })])

        if sql.startswith("SELECT id, project_slug, policy_json, status, created_by, created_at"):
            slug = params.get("s")
            st = params.get("st")
            rows = []
            for d in sorted(self.eng.drafts.values(), key=lambda x: -x["id"]):
                if "WHERE id=:id" in sql:
                    if d["id"] == params["id"]:
                        rows.append(_Row(d))
                        break
                else:
                    if d["project_slug"] != slug:
                        continue
                    if st and d["status"] != st:
                        continue
                    rows.append(_Row(d))
            return _Result(rows)

        return _Result([])


# ---------------------------------------------------------------------------


@pytest.fixture
def fake_engine(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(signoff, "_engine", lambda: eng)
    monkeypatch.setattr(signoff, "_ensure_visibility_policy_table", lambda: None)
    # save_policy stub — record publish as new policy version
    def _fake_save(slug, pol, user_id=None):
        cur = eng.policies.get(slug, {"version": 0})
        v = int(cur.get("version", 0)) + 1
        eng.policies[slug] = {"version": v, "policy": pol.model_dump()}
        return v
    monkeypatch.setattr(signoff, "save_policy", _fake_save)
    return eng


SAMPLE_POLICY = {
    "version": 1,
    "private": {"fields": {}},
    "network": {"fields": {}},
    "public": {"fields": {}},
}


def test_create_and_list_draft(fake_engine):
    did = signoff.create_draft("acme", SAMPLE_POLICY, user_id=10, comment="initial")
    assert did is not None
    drafts = signoff.list_drafts("acme")
    assert len(drafts) == 1
    assert drafts[0]["id"] == did
    assert drafts[0]["status"] == "draft"
    assert drafts[0]["comment"] == "initial"


def test_submit_then_two_approvals_publishes(fake_engine):
    did = signoff.create_draft("acme", SAMPLE_POLICY, user_id=10)
    signoff.submit_draft(did, user_id=10)
    d = signoff.get_draft(did)
    assert d["status"] == "pending"

    r1 = signoff.approve_draft(did, approver_user_id=20, comment="lgtm")
    assert r1["status"] == "pending"
    assert len(r1["approvals"]) == 1

    pre_version = fake_engine.policies.get("acme", {}).get("version", 0)
    r2 = signoff.approve_draft(did, approver_user_id=30, comment="ok")
    assert r2["status"] == "published"
    post_version = fake_engine.policies.get("acme", {}).get("version", 0)
    assert post_version == pre_version + 1


def test_one_approval_keeps_pending(fake_engine):
    did = signoff.create_draft("acme", SAMPLE_POLICY, user_id=10)
    signoff.submit_draft(did, user_id=10)
    r = signoff.approve_draft(did, approver_user_id=20)
    assert r["status"] == "pending"
    assert "acme" not in fake_engine.policies


def test_reject_sets_rejected_and_no_publish(fake_engine):
    did = signoff.create_draft("acme", SAMPLE_POLICY, user_id=10)
    signoff.submit_draft(did, user_id=10)
    r = signoff.reject_draft(did, approver_user_id=20, comment="wrong")
    assert r["status"] == "rejected"
    assert "acme" not in fake_engine.policies


def test_self_approval_blocked(fake_engine):
    did = signoff.create_draft("acme", SAMPLE_POLICY, user_id=10)
    signoff.submit_draft(did, user_id=10)
    r = signoff.approve_draft(did, approver_user_id=10)
    assert r is not None
    assert r.get("_error") and "self" in r["_error"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-v"])
