"""Tests for the generalized @approval framework.

Uses an in-memory SQLAlchemy stub so no live Postgres is required. The stub
mimics the small subset of SQL/JSONB semantics exercised by
``dash/agentic/approval.py``.

Covers:
  * @approval inserts a pending row and returns ApprovalPending
  * sign() adds a signature
  * self-approval blocked
  * 1-of-1 approval triggers execution and stores result
  * 2-of-2 approval requires both signatures
  * reject sets status='rejected', no execution
  * expired sweeper marks status='expired'
  * pass-through when EXPERIMENTAL_AGI is unset
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# In-memory SQL stub
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=None, rowcount: int = 0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    """Tiny SQL interpreter — recognises only the statements approval.py uses."""

    def __init__(self, store: dict):
        self.store = store
        self._tx_open = False

    # context-manager support
    def __enter__(self):
        self._tx_open = True
        return self

    def __exit__(self, *args):
        self._tx_open = False

    def begin(self):
        return self  # supports `with eng.begin() as conn`

    def connect(self):
        return self

    def commit(self):
        pass

    def execute(self, sql, params=None):  # noqa: C901
        params = params or {}
        text = sql.text if hasattr(sql, "text") else str(sql)
        norm = " ".join(text.split())

        # INSERT request
        if "INSERT INTO dash.dash_approval_requests" in norm:
            req = {
                "id": params["id"],
                "project_slug": params.get("slug"),
                "action_type": params["at"],
                "resource_id": params.get("rid"),
                "payload": json.loads(params["pl"]),
                "requested_by": params["uid"],
                "required_approvers": params["req"],
                "allowed_roles": json.loads(params["roles"]),
                "status": "pending",
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=int(params["hrs"])),
                "resolved_at": None,
                "execution_result": None,
            }
            self.store["requests"][req["id"]] = req
            return _Result(rowcount=1)

        # SELECT id, status, expires_at after insert
        if "SELECT id, status, expires_at FROM dash.dash_approval_requests" in norm:
            r = self.store["requests"].get(params["id"])
            if not r:
                return _Result()
            return _Result([(r["id"], r["status"], r["expires_at"])])

        # SELECT full request
        if "SELECT id, project_slug, action_type, resource_id, payload" in norm and "execution_result" in norm:
            r = self.store["requests"].get(params["id"])
            if not r:
                return _Result()
            return _Result([(
                r["id"], r["project_slug"], r["action_type"], r["resource_id"],
                json.dumps(r["payload"]), r["requested_by"], r["required_approvers"],
                json.dumps(r["allowed_roles"]), r["status"], r["created_at"],
                r["expires_at"], r["resolved_at"],
                json.dumps(r["execution_result"]) if r["execution_result"] is not None else None,
            )])

        # SELECT signatures
        if "FROM dash.dash_approval_signatures" in norm and "WHERE request_id" in norm:
            sigs = [s for s in self.store["sigs"] if s["request_id"] == params["id"]]
            sigs.sort(key=lambda s: s["signed_at"])
            return _Result([(s["id"], s["request_id"], s["approver_id"], s["decision"],
                             s["reason"], s["signed_at"]) for s in sigs])

        # INSERT signature
        if "INSERT INTO dash.dash_approval_signatures" in norm:
            sigs = self.store["sigs"]
            existing = next((s for s in sigs
                             if s["request_id"] == params["r"]
                             and s["approver_id"] == params["a"]), None)
            if existing:
                existing["decision"] = params["d"]
                existing["reason"] = params.get("why")
                existing["signed_at"] = datetime.now(timezone.utc)
            else:
                sigs.append({
                    "id": len(sigs) + 1,
                    "request_id": params["r"],
                    "approver_id": params["a"],
                    "decision": params["d"],
                    "reason": params.get("why"),
                    "signed_at": datetime.now(timezone.utc),
                })
            return _Result(rowcount=1)

        # UPDATE status='rejected'
        if "SET status='rejected'" in norm:
            r = self.store["requests"].get(params["id"])
            if r and r["status"] == "pending":
                r["status"] = "rejected"
                r["resolved_at"] = datetime.now(timezone.utc)
            return _Result(rowcount=1)

        # UPDATE status='cancelled'
        if "SET status='cancelled'" in norm:
            r = self.store["requests"].get(params["id"])
            if r and r["status"] == "pending":
                r["status"] = "cancelled"
                r["resolved_at"] = datetime.now(timezone.utc)
            return _Result(rowcount=1)

        # UPDATE expired (single)
        if "SET status='expired'" in norm and ":id" in text:
            r = self.store["requests"].get(params["id"])
            if r and r["status"] == "pending":
                r["status"] = "expired"
                r["resolved_at"] = datetime.now(timezone.utc)
            return _Result(rowcount=1)

        # UPDATE expired (sweeper, RETURNING id)
        if "SET status='expired'" in norm and "RETURNING id" in norm:
            now = datetime.now(timezone.utc)
            expired_ids = []
            for rid, r in self.store["requests"].items():
                if r["status"] == "pending" and r["expires_at"] < now:
                    r["status"] = "expired"
                    r["resolved_at"] = now
                    expired_ids.append((rid,))
            return _Result(expired_ids, rowcount=len(expired_ids))

        # UPDATE executed
        if "SET status='executed'" in norm:
            r = self.store["requests"].get(params["id"])
            if r:
                r["status"] = "executed"
                r["resolved_at"] = datetime.now(timezone.utc)
                r["execution_result"] = json.loads(params["r"])
            return _Result(rowcount=1)

        # UPDATE approved (no executor)
        if "SET status='approved'" in norm:
            r = self.store["requests"].get(params["id"])
            if r:
                if r["status"] == "pending":
                    r["status"] = "approved"
                r["resolved_at"] = datetime.now(timezone.utc)
                if "r" in params:
                    try:
                        r["execution_result"] = json.loads(params["r"])
                    except Exception:
                        pass
            return _Result(rowcount=1)

        # INSERT audit
        if "INSERT INTO dash.dash_approval_audit" in norm:
            self.store["audit"].append({
                "id": len(self.store["audit"]) + 1,
                "request_id": params.get("r"),
                "event": params["e"],
                "actor_id": params.get("a"),
                "metadata": json.loads(params.get("m") or "{}"),
                "created_at": datetime.now(timezone.utc),
            })
            return _Result(rowcount=1)

        # SELECT pending list
        if "FROM dash.dash_approval_requests" in norm and "ORDER BY created_at DESC" in norm:
            rows = []
            for r in self.store["requests"].values():
                if r["status"] != "pending":
                    continue
                if "slug" in params and r["project_slug"] != params["slug"]:
                    continue
                if "at" in params and r["action_type"] != params["at"]:
                    continue
                rows.append((
                    r["id"], r["project_slug"], r["action_type"], r["resource_id"],
                    json.dumps(r["payload"]), r["requested_by"],
                    r["required_approvers"], json.dumps(r["allowed_roles"]),
                    r["status"], r["created_at"], r["expires_at"],
                ))
            rows.sort(key=lambda t: t[9], reverse=True)
            return _Result(rows[: params.get("lim", 50)])

        # SELECT audit
        if "FROM dash.dash_approval_audit" in norm:
            rows = []
            for a in self.store["audit"]:
                rows.append((a["id"], a["request_id"], a["event"], a["actor_id"],
                             json.dumps(a["metadata"]), a["created_at"]))
            rows.sort(key=lambda t: t[5], reverse=True)
            return _Result(rows[: params.get("lim", 100)])

        # Fallback: silently no-op (covers SET / DDL).
        return _Result()


class _Engine:
    def __init__(self):
        self.store = {"requests": {}, "sigs": [], "audit": []}

    def connect(self):
        return _Conn(self.store)

    def begin(self):
        return _Conn(self.store)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fresh_module(monkeypatch):
    """Reload approval.py with EXPERIMENTAL_AGI=1 + a fresh in-memory engine."""
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")
    # Drop any cached modules so registration restarts.
    for name in list(sys.modules):
        if name.startswith("dash.agentic"):
            del sys.modules[name]
    import dash.agentic.approval as ap
    importlib.reload(ap)
    eng = _Engine()
    monkeypatch.setattr(ap, "_engine", lambda: eng)
    return ap, eng


@pytest.fixture()
def offmodule(monkeypatch):
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    for name in list(sys.modules):
        if name.startswith("dash.agentic"):
            del sys.modules[name]
    import dash.agentic.approval as ap
    importlib.reload(ap)
    eng = _Engine()
    monkeypatch.setattr(ap, "_engine", lambda: eng)
    return ap, eng


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_migration_sql_present():
    """041 migration file exists with the documented constraints."""
    here = os.path.dirname(__file__)
    candidates = [
        os.path.normpath(os.path.join(here, "..", "db", "migrations", "041_approval_log.sql")),
        os.path.normpath(os.path.join(here, "..", "..", "..", "..", "db", "migrations", "041_approval_log.sql")),
    ]
    found = next((p for p in candidates if os.path.exists(p)), None)
    assert found, f"041_approval_log.sql not found in {candidates}"
    sql = open(found).read()
    assert "dash.dash_approval_requests" in sql
    assert "dash.dash_approval_signatures" in sql
    assert "dash.dash_approval_audit" in sql
    assert "UNIQUE(request_id, approver_id)" in sql


def test_approval_inserts_pending_row(fresh_module):
    ap, eng = fresh_module

    @ap.approval("brain_delete", min_approvers=1, allowed_roles=["admin"])
    def delete_brain(ctx, entry_id):
        return {"deleted": entry_id, "ctx": ctx.request_id}

    res = delete_brain(entry_id=99, requested_by=1)
    assert isinstance(res, ap.ApprovalPending)
    assert res.action_type == "brain_delete"
    assert res.status == "pending"
    assert res.request_id.startswith("apr_")

    req = ap.get_request(res.request_id)
    assert req is not None
    assert req["status"] == "pending"
    assert req["action_type"] == "brain_delete"
    assert req["required_approvers"] == 1
    assert req["allowed_roles"] == ["admin"]


def test_self_approval_blocked(fresh_module):
    ap, _ = fresh_module

    @ap.approval("rls_apply", min_approvers=1)
    def apply_rls(ctx, slug):
        return {"applied": slug}

    res = apply_rls(slug="acme", requested_by=42)
    state = ap.sign(res.request_id, approver_id=42, decision="approve")
    assert state.error == "self_approval_blocked"
    # Status remains pending.
    assert ap.get_request(res.request_id)["status"] == "pending"


def test_one_of_one_approval_executes(fresh_module):
    ap, _ = fresh_module

    @ap.approval("brain_delete", min_approvers=1)
    def delete_brain(ctx, entry_id):
        return {"deleted": int(entry_id), "ctx_request_id": ctx.request_id}

    res = delete_brain(entry_id=7, requested_by=1)
    state = ap.sign(res.request_id, approver_id=2, decision="approve")
    assert state.status == "executed", state
    final = ap.get_request(res.request_id)
    assert final["status"] == "executed"
    er = final["execution_result"]
    assert er["result"]["deleted"] == 7
    assert er["result"]["ctx_request_id"] == res.request_id


def test_two_of_two_requires_both(fresh_module):
    ap, _ = fresh_module

    @ap.approval("rls_apply", min_approvers=2, allowed_roles=["admin"])
    def apply_rls(ctx, slug, policy_json):
        return {"slug": slug, "keys": sorted(list((policy_json or {}).keys()))}

    res = apply_rls(slug="acme", policy_json={"a": 1}, requested_by=10)
    s1 = ap.sign(res.request_id, approver_id=20, decision="approve")
    assert s1.status == "pending"
    assert ap.get_request(res.request_id)["status"] == "pending"
    s2 = ap.sign(res.request_id, approver_id=30, decision="approve")
    assert s2.status == "executed"
    assert ap.get_request(res.request_id)["execution_result"]["result"]["slug"] == "acme"


def test_reject_does_not_execute(fresh_module):
    ap, _ = fresh_module
    executed = []

    @ap.approval("brain_delete", min_approvers=1)
    def delete_brain(ctx, entry_id):
        executed.append(entry_id)
        return {"deleted": entry_id}

    res = delete_brain(entry_id=1, requested_by=1)
    state = ap.sign(res.request_id, approver_id=2, decision="reject", reason="no")
    assert state.status == "rejected"
    assert ap.get_request(res.request_id)["status"] == "rejected"
    assert executed == []


def test_expired_sweeper(fresh_module):
    ap, eng = fresh_module

    @ap.approval("brain_delete", min_approvers=1)
    def delete_brain(ctx, entry_id):
        return {"deleted": entry_id}

    res = delete_brain(entry_id=1, requested_by=1)
    # Force-expire by rewriting expires_at in the in-memory store.
    eng.store["requests"][res.request_id]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    n = ap.expire_overdue()
    assert n == 1
    assert ap.get_request(res.request_id)["status"] == "expired"


def test_pass_through_when_flag_off(offmodule):
    ap, eng = offmodule

    @ap.approval("brain_delete", min_approvers=1)
    def delete_brain(ctx_or_kwargs=None, entry_id=None, **kw):
        # When the decorator is a pass-through, the original function runs
        # with whatever args the caller supplied — there's no ctx.
        return {"deleted": entry_id, "passthrough": True}

    out = delete_brain(entry_id=55)
    assert isinstance(out, dict)
    assert out["deleted"] == 55
    assert out["passthrough"] is True
    # No row should have been written.
    assert eng.store["requests"] == {}
