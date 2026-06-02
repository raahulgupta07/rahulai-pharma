"""Phase 7A — cross-store visibility read audit + time-travel tests."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from dash.policy import read_audit
from dash.policy.schema import AudienceRules, FieldRule, VisibilityPolicy


# ---------------------------------------------------------------------------
# Fake DB: capture inserted rows in-memory; query/count/csv read from same store.
# ---------------------------------------------------------------------------

class _Store:
    def __init__(self):
        self.rows: list[dict] = []
        self._next_id = 1

    def insert(self, batch: list[dict]):
        for r in batch:
            r2 = dict(r)
            r2["id"] = self._next_id
            r2["created_at"] = datetime.now(timezone.utc)
            self._next_id += 1
            self.rows.append(r2)


class _FakeConn:
    def __init__(self, store: _Store):
        self.store = store
        self._last_op: str | None = None

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "INSERT INTO public.dash_visibility_read_log" in sql:
            if isinstance(params, list):
                self.store.insert(params)
            elif isinstance(params, dict):
                self.store.insert([params])
            return _FakeResult([])
        if "COUNT(*)" in sql:
            rows = self._filter(params or {})
            return _FakeResult([(len(rows),)])
        if "SELECT id, project_slug" in sql:
            rows = self._filter(params or {})
            limit = (params or {}).get("limit", 5000)
            offset = (params or {}).get("offset", 0)
            sliced = sorted(rows, key=lambda r: r["created_at"], reverse=True)[offset:offset + limit]
            return _FakeResult([self._row_tuple(r) for r in sliced])
        return _FakeResult([])

    def _filter(self, params):
        slug = params.get("slug")
        out = [r for r in self.store.rows if r["project_slug"] == slug]
        if params.get("tgt"):
            out = [r for r in out if r.get("target_scope_id") == params["tgt"]]
        if params.get("vuid") is not None:
            out = [r for r in out if r.get("viewer_user_id") == params["vuid"]]
        return out

    @staticmethod
    def _row_tuple(r):
        return (
            r["id"], r["project_slug"], r.get("viewer_user_id"),
            r.get("viewer_scope_id"), r.get("target_scope_id"),
            r.get("intent"), r.get("policy_version"), r.get("sql_excerpt"),
            r.get("fields_downgraded"), r.get("row_count"), r["created_at"],
        )


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return list(self._rows)
    def first(self): return self._rows[0] if self._rows else None


class _FakeEngine:
    def __init__(self, store: _Store):
        self.store = store
    def connect(self): return _FakeConn(self.store)
    def begin(self): return _FakeConn(self.store)
    def dispose(self): pass


@pytest.fixture
def fake_store(monkeypatch):
    store = _Store()
    monkeypatch.setattr(read_audit, "_engine", lambda: _FakeEngine(store))
    return store


def _flush_now(store: _Store):
    """Drain queue → store directly (bypass background thread)."""
    while True:
        try:
            payload = read_audit._QUEUE.get_nowait()
        except Exception:
            break
        store.insert([payload])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_log_read_inserts_when_intent_network(fake_store):
    read_audit.log_read(
        project_slug="proj1", viewer_user_id=42, viewer_scope_id="MUM01",
        target_scope_id="DEL07", intent="network", policy_version=3,
        sql="SELECT * FROM sales", fields_downgraded=["amount"], row_count=10,
    )
    _flush_now(fake_store)
    assert len(fake_store.rows) == 1
    r = fake_store.rows[0]
    assert r["intent"] == "network"
    assert r["viewer_user_id"] == 42
    assert r["target_scope_id"] == "DEL07"
    assert r["fields_downgraded"] == ["amount"]


def test_log_read_skips_when_intent_private(fake_store):
    read_audit.log_read(
        project_slug="proj1", viewer_user_id=42, viewer_scope_id="MUM01",
        target_scope_id="MUM01", intent="private", policy_version=3,
        sql="SELECT * FROM sales", fields_downgraded=[], row_count=10,
    )
    _flush_now(fake_store)
    assert fake_store.rows == []


def test_log_read_truncates_sql(fake_store):
    long_sql = "SELECT " + "x," * 5000
    read_audit.log_read(
        project_slug="proj1", viewer_user_id=1, viewer_scope_id="A",
        target_scope_id="B", intent="public", policy_version=1,
        sql=long_sql, fields_downgraded=None,
    )
    _flush_now(fake_store)
    assert len(fake_store.rows[0]["sql_excerpt"]) <= 2000


def test_query_audit_filters_by_target_scope(fake_store):
    for tgt in ["S1", "S2", "S1"]:
        read_audit.log_read(
            project_slug="proj1", viewer_user_id=1, viewer_scope_id="V",
            target_scope_id=tgt, intent="network", policy_version=1,
            sql="SELECT 1", fields_downgraded=[],
        )
    _flush_now(fake_store)
    s1 = read_audit.query_audit("proj1", target_scope="S1")
    s2 = read_audit.query_audit("proj1", target_scope="S2")
    assert len(s1) == 2
    assert len(s2) == 1
    assert read_audit.count_audit("proj1") == 3
    assert read_audit.count_audit("proj1", target_scope="S1") == 2


def test_export_audit_csv_has_header_and_rows(fake_store):
    read_audit.log_read(
        project_slug="proj1", viewer_user_id=7, viewer_scope_id="V",
        target_scope_id="T", intent="network", policy_version=2,
        sql="SELECT a", fields_downgraded=["a"],
    )
    _flush_now(fake_store)
    csv_text = read_audit.export_audit_csv("proj1")
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    assert rows, "csv should not be empty"
    header = rows[0]
    for col in ["id", "project_slug", "viewer_user_id", "intent", "fields_downgraded"]:
        assert col in header
    assert len(rows) >= 2  # header + at least one data row


# ---------------------------------------------------------------------------
# Time-travel — pure in-process test of historical policy lookup + apply.
# ---------------------------------------------------------------------------

def test_time_travel_returns_historical_policy_at_date():
    pol_v1 = VisibilityPolicy(
        version=1,
        network=AudienceRules(fields={"amount": FieldRule(mode="hide")}),
    )
    pol_v2 = VisibilityPolicy(
        version=2,
        network=AudienceRules(fields={"amount": FieldRule(mode="full")}),
    )
    history = [
        {"version": 1, "policy_json": pol_v1.model_dump(),
         "changed_at": datetime(2025, 1, 1, tzinfo=timezone.utc)},
        {"version": 2, "policy_json": pol_v2.model_dump(),
         "changed_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
    ]
    target_dt = datetime(2025, 6, 1, tzinfo=timezone.utc)
    matching = [h for h in history if h["changed_at"] <= target_dt]
    matching.sort(key=lambda h: h["changed_at"], reverse=True)
    assert matching, "expected at least one historical policy"
    chosen = matching[0]
    assert chosen["version"] == 1

    from dash.policy import PolicyEngine
    pol = VisibilityPolicy(**chosen["policy_json"])
    rewritten, dropped = PolicyEngine().apply(
        "SELECT amount FROM sales", pol, "network"
    )
    assert "amount" in dropped
    assert rewritten != "SELECT amount FROM sales"
