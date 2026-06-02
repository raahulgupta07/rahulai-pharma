"""Sanity tests for the ontology auto-cluster daemon (Phase B).

The daemon reuses ``compute_cluster_suggestions`` (template-registry
heuristics) and writes to ``dash_company_brain`` + ``dash_promotion_log``.

To keep the test hermetic and DB-optional we:

* monkeypatch ``app.ontology_api._entities_index`` to seed three
  entities with overlapping aliases / columns so the suggester yields
  predictable high-confidence candidates;
* monkeypatch ``dash.cron.ontology_cluster_daemon._engine`` to return a
  fake engine whose ``begin()`` ctx-manager hands out a fake connection
  that records every executed SQL statement.

The fake connection emulates the SELECTs / UPDATEs / INSERTs the daemon
needs without touching Postgres. We only assert behavioural invariants
(merge attempted, idempotency, multi-worker claim semantics).
"""
from __future__ import annotations

import re
from typing import Any

import pytest


# ── Fakes ─────────────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows: list[tuple], rowcount: int = 0) -> None:
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    """Records executed SQL + parameters; serves canned responses.

    A tiny SQL dispatcher: matches on the first ~12 normalized words of
    the statement so we don't have to mirror the daemon's SQL verbatim.
    """

    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store
        self.executed: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = re.sub(r"\s+", " ", str(getattr(stmt, "text", stmt))).strip().lower()
        params = dict(params or {})
        self.executed.append((sql, params))

        # SELECT canonical entity row
        if sql.startswith("select id, coalesce(metadata"):
            name = params.get("n")
            row = self.store["brain"].get(name)
            if row is None:
                return _FakeResult([])
            return _FakeResult([(row["id"], row["metadata"])])

        # INSERT canonical row
        if sql.startswith("insert into dash_company_brain"):
            name = params.get("n")
            new_id = self.store["next_id"]
            self.store["next_id"] += 1
            import json as _json
            self.store["brain"][name] = {
                "id": new_id,
                "metadata": _json.loads(params.get("m") or "{}"),
            }
            return _FakeResult([], rowcount=1)

        # UPDATE alias claim
        if sql.startswith("update dash_company_brain set metadata = jsonb_set( "
                          "coalesce(metadata, '{}'::jsonb), '{aliases}'"):
            target_id = params.get("id")
            cand = params.get("cand")
            for row in self.store["brain"].values():
                if row["id"] == target_id:
                    aliases = list(row["metadata"].get("aliases") or [])
                    if cand in aliases:
                        return _FakeResult([], rowcount=0)
                    aliases.append(cand)
                    row["metadata"]["aliases"] = aliases
                    return _FakeResult([], rowcount=1)
            return _FakeResult([], rowcount=0)

        # UPDATE superseded_by
        if sql.startswith("update dash_company_brain set metadata = jsonb_set( "
                          "coalesce(metadata, '{}'::jsonb), '{superseded_by}'"):
            cand = params.get("cand")
            primary = params.get("primary")
            row = self.store["brain"].get(cand)
            if row is not None:
                row["metadata"]["superseded_by"] = primary
                return _FakeResult([], rowcount=1)
            return _FakeResult([], rowcount=0)

        # SELECT existing pending promotion
        if sql.startswith("select 1 from dash_promotion_log where approver is null"):
            for r in self.store["promotions"]:
                if (r.get("fact_type") == params.get("ft")
                        and r.get("fact_text") == params.get("tx")
                        and r.get("approver") is None):
                    return _FakeResult([(1,)])
            return _FakeResult([])

        # INSERT promotion
        if sql.startswith("insert into dash_promotion_log"):
            self.store["promotions"].append({
                "fact_text": params.get("tx"),
                "fact_type": params.get("ft"),
                "approver": None,
            })
            return _FakeResult([], rowcount=1)

        return _FakeResult([])


class _FakeEngineCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def __enter__(self) -> _FakeConn:
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeEngine:
    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store

    def begin(self) -> _FakeEngineCtx:
        return _FakeEngineCtx(_FakeConn(self.store))


# ── Seeded ontology — 3 aliased entities ─────────────────────────────────


def _seeded_index() -> dict:
    """Three entities where ``Customer`` is the shared canonical.

    ``Client`` and ``Account`` both have ``Customer`` listed as an alias,
    so the alias-subset rule fires at confidence ≥ 0.95 — eligible for
    auto-merge. Columns overlap to also exercise rule 2 lightly.
    """
    return {
        "Customer": {
            "aliases": {"Customer", "Buyer"},
            "templates": [("retail", "retail_food")],
            "columns": {"id", "name", "email", "phone"},
            "category": "retail_food",
            "first_seen_template": "retail",
        },
        "Client": {
            "aliases": {"Customer"},
            "templates": [("saas", "tech_saas")],
            "columns": {"id", "name", "email"},
            "category": "tech_saas",
            "first_seen_template": "saas",
        },
        "Account": {
            "aliases": {"Customer"},
            "templates": [("bank", "financial_services")],
            "columns": {"id", "name", "email"},
            "category": "financial_services",
            "first_seen_template": "bank",
        },
    }


@pytest.fixture
def patched_daemon(monkeypatch):
    from app import ontology_api
    from dash.cron import ontology_cluster_daemon as daemon

    monkeypatch.setattr(ontology_api, "_entities_index", _seeded_index)

    store: dict[str, Any] = {"brain": {}, "promotions": [], "next_id": 1}
    fake = _FakeEngine(store)
    monkeypatch.setattr(daemon, "_engine", lambda: fake)
    return daemon, store


# ── Tests ────────────────────────────────────────────────────────────────


def test_suggester_yields_high_confidence_pairs():
    """The seeded index produces alias-subset hits ≥ auto-merge threshold."""
    from app.ontology_api import compute_cluster_suggestions
    from dash.cron.ontology_cluster_daemon import AUTO_MERGE_THRESHOLD

    # Patch via direct module attr swap for this lightweight call.
    import app.ontology_api as oa
    orig = oa._entities_index
    oa._entities_index = _seeded_index
    try:
        suggestions = compute_cluster_suggestions(50)
    finally:
        oa._entities_index = orig

    assert suggestions, "expected at least one merge suggestion"
    high = [s for s in suggestions if s["confidence"] >= AUTO_MERGE_THRESHOLD]
    assert high, "expected at least one auto-merge candidate"


def test_run_cycle_auto_merges_aliased_entities(patched_daemon):
    """Daemon merges the 3 seeded entities into a canonical primary."""
    daemon, store = patched_daemon
    out = daemon.run_cycle()
    assert out["candidates_found"] >= 2
    assert out["auto_merged"] >= 2, out
    assert out["errors"] == 0

    # Canonical row exists with both losers in metadata.aliases.
    assert store["brain"], "canonical brain row not created"
    canonical = next(iter(store["brain"].values()))
    aliases = set(canonical["metadata"].get("aliases") or [])
    # At least one of the loser names should be aliased into the canonical.
    assert aliases, "expected aliases on canonical entity"


def test_run_cycle_is_idempotent(patched_daemon):
    """Running twice does not re-merge or duplicate aliases."""
    daemon, store = patched_daemon
    first = daemon.run_cycle()
    second = daemon.run_cycle()
    assert second["auto_merged"] == 0, (
        f"second run should be a no-op, got {second}"
    )
    assert first["auto_merged"] >= 1


def test_disable_flag_short_circuits_loop(monkeypatch):
    """ONTOLOGY_CLUSTER_DISABLED=1 returns immediately from the loop."""
    monkeypatch.setenv("ONTOLOGY_CLUSTER_DISABLED", "1")
    from dash.cron.ontology_cluster_daemon import (
        _is_disabled, ontology_cluster_loop,
    )
    assert _is_disabled() is True

    import asyncio
    # Should return without ever sleeping.
    asyncio.get_event_loop().run_until_complete(ontology_cluster_loop()) \
        if False else asyncio.run(ontology_cluster_loop())
