"""Phase G — finding retention, cross-tenant promotion, scope auto-learn.

Uses an in-memory sqlite engine + monkeypatching `_get_engine` from
`dash.tools.skill_refinery` so the modules under test see a real DB.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
_REPO = os.path.abspath(os.path.join(_HERE, os.pardir))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture
def sqlite_engine(monkeypatch):
    """Return a sqlite engine that emulates the small subset of PG syntax used."""
    from sqlalchemy import create_engine, event

    eng = create_engine("sqlite:///:memory:", future=True)

    # WHY: tests use Postgres-flavored DDL/DML (JSONB, ::JSONB, NOW(), array_agg, etc.).
    # Translate at the parameterize stage so the modules' SQL works on sqlite.
    @event.listens_for(eng, "before_cursor_execute", retval=True)
    def _translate(conn, cursor, statement, parameters, context, executemany):
        s = statement
        s = s.replace("CAST(:sig AS JSONB)", ":sig")
        s = s.replace("CAST(:p AS JSONB)", ":p")
        s = s.replace("public.", "")
        s = s.replace("INTERVAL '1 day'", "'-1 day'")
        s = s.replace("array_agg(DISTINCT project_slug)", "GROUP_CONCAT(DISTINCT project_slug)")
        s = s.replace("finding_signature::text", "finding_signature")
        # sqlite cannot bind lists — coerce list params to JSON-encoded strings.
        if isinstance(parameters, dict):
            parameters = {k: (json.dumps(v) if isinstance(v, list) else v)
                          for k, v in parameters.items()}
        elif isinstance(parameters, (list, tuple)):
            parameters = type(parameters)(
                json.dumps(v) if isinstance(v, list) else v for v in parameters
            )
        s = s.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        s = s.replace("TIMESTAMPTZ DEFAULT now()", "TEXT DEFAULT CURRENT_TIMESTAMP")
        s = s.replace("TIMESTAMPTZ DEFAULT NOW()", "TEXT DEFAULT CURRENT_TIMESTAMP")
        s = s.replace("JSONB", "TEXT")
        s = s.replace("TEXT[]", "TEXT")
        s = s.replace("now()", "CURRENT_TIMESTAMP")
        s = s.replace("NOW()", "CURRENT_TIMESTAMP")
        # finding_signature->>'severity' index — drop the index entirely on sqlite
        if "CREATE INDEX IF NOT EXISTS idx_retention_signature" in s:
            s = "SELECT 1"
        return s, parameters

    # Patch the engine getter used by all Phase G modules.
    import dash.tools.skill_refinery as sr
    monkeypatch.setattr(sr, "_get_engine", lambda: eng, raising=False)
    return eng


@pytest.fixture
def Finding():
    from dash.dashboards.agents.contracts import Finding as _F
    return _F


# ─────────────────────────── tests ───────────────────────────


def test_hash_finding_is_stable(Finding):
    from dash.dashboards.agents import memory_loop

    f1 = Finding(headline="Stockouts up 34% WoW", cause_hypothesis="supplier delay")
    f2 = Finding(headline="  Stockouts up 34% WoW  ", cause_hypothesis="supplier delay")
    f3 = Finding(headline="Stockouts up 34% WoW", cause_hypothesis="DIFFERENT cause")
    h1 = memory_loop.hash_finding(f1)
    h2 = memory_loop.hash_finding(f2)
    h3 = memory_loop.hash_finding(f3)
    assert h1 == h2, "normalization should make whitespace irrelevant"
    assert h1 != h3, "different cause must produce different hash"
    assert len(h1) == 64


def test_record_surface_and_keep_increments(sqlite_engine, Finding):
    from dash.dashboards.agents import memory_loop

    f = Finding(headline="Sales drop in MUM01", cause_hypothesis="weekend dip",
                sql="SELECT a FROM t GROUP BY a", domain_tags=["sales"], severity="high")
    h = memory_loop.record_surface("proj1", f)
    assert h is not None
    # Surface again — surface_count goes up
    memory_loop.record_surface("proj1", f)
    memory_loop.record_keep("proj1", f)
    memory_loop.record_keep("proj1", h)  # also accept hash form

    from sqlalchemy import text
    with sqlite_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT surface_count, keep_count, dismiss_count "
            "FROM dash_finding_retention WHERE project_slug='proj1' AND finding_hash=:h"
        ), {"h": h}).fetchone()
    assert row is not None
    assert row[0] >= 2  # surfaced twice
    assert row[1] == 2  # kept twice
    assert row[2] == 0


def test_get_retained_findings_orders_by_keep_desc(sqlite_engine, Finding):
    from dash.dashboards.agents import memory_loop

    a = Finding(headline="A finding", cause_hypothesis="x")
    b = Finding(headline="B finding", cause_hypothesis="y")
    c = Finding(headline="C finding", cause_hypothesis="z")  # dismissed

    for f in (a, b, c):
        memory_loop.record_surface("proj1", f)
    # B kept 3x, A kept 1x, C dismissed 2x
    for _ in range(3): memory_loop.record_keep("proj1", b)
    memory_loop.record_keep("proj1", a)
    for _ in range(2): memory_loop.record_dismiss("proj1", c)

    out = memory_loop.get_retained_findings("proj1", top_n=10)
    headlines = [r["headline"] for r in out]
    assert "C finding" not in headlines, "dismissed > kept must be excluded"
    assert headlines.index("B finding") < headlines.index("A finding")


def test_should_promote_threshold(sqlite_engine, Finding):
    from dash.dashboards.agents import memory_loop

    f = Finding(headline="Cross-tenant pattern", cause_hypothesis="root")
    h = memory_loop.hash_finding(f)
    # Three projects each with keep_count >= 2 should trigger promotion.
    for slug in ("p1", "p2", "p3"):
        memory_loop.record_surface(slug, f)
        memory_loop.record_keep(slug, f)
        memory_loop.record_keep(slug, f)
    assert memory_loop.should_promote(h) is True

    # Lone project with many keeps should NOT trigger promotion.
    g = Finding(headline="Lone pattern", cause_hypothesis="root")
    gh = memory_loop.hash_finding(g)
    memory_loop.record_surface("p1", g)
    for _ in range(5):
        memory_loop.record_keep("p1", g)
    assert memory_loop.should_promote(gh) is False


def test_promotion_writes_brain_and_promotion_row(sqlite_engine, Finding):
    from dash.dashboards.agents import memory_loop
    from dash.dashboards.agents.promotion import run_promotion_cycle

    f = Finding(headline="Shared pattern: stockouts", cause_hypothesis="supply",
                sql="SELECT s FROM o GROUP BY s", domain_tags=["supply"], severity="high")
    for slug in ("alpha", "beta", "gamma"):
        memory_loop.record_surface(slug, f)
        memory_loop.record_keep(slug, f)
        memory_loop.record_keep(slug, f)

    res = run_promotion_cycle()
    assert res.get("promoted", 0) >= 1

    from sqlalchemy import text
    with sqlite_engine.connect() as conn:
        prom = conn.execute(text(
            "SELECT headline, pattern FROM dash_finding_promotions"
        )).fetchall()
        brain = conn.execute(text(
            "SELECT scope, category, source FROM dash_company_brain "
            "WHERE source='cross_tenant_promotion'"
        )).fetchall()
    assert len(prom) >= 1
    assert "stockouts" in (prom[0][0] or "").lower()
    pattern = json.loads(prom[0][1])
    # Anonymization: pattern must NOT contain raw row data.
    assert set(pattern.keys()) <= {"sql_keywords", "domain_tags", "severity"}
    assert len(brain) >= 1
    assert brain[0][0] == "global"
    assert brain[0][1] == "shared_finding"


def test_scope_auto_learn_inserts_brain_alias(sqlite_engine):
    from dash.policy.scope_brain import auto_learn_scopes

    n = auto_learn_scopes("proj1", [("MUM01", "Mumbai-Bandra"),
                                    ("DEL02", "Delhi-Connaught")])
    assert n == 2

    from sqlalchemy import text
    with sqlite_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT name, value, scope, category, source FROM dash_company_brain "
            "WHERE source='auto_scope_learn' ORDER BY name"
        )).fetchall()
    assert len(rows) == 2
    names = {r[0]: r[1] for r in rows}
    assert names["MUM01"] == "Mumbai-Bandra"
    assert names["DEL02"] == "Delhi-Connaught"
    assert rows[0][2] == "project"
    assert rows[0][3] == "alias"

    # Idempotent — second call updates value, doesn't duplicate.
    auto_learn_scopes("proj1", [("MUM01", "Mumbai-Bandra-West")])
    with sqlite_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT value FROM dash_company_brain WHERE name='MUM01'"
        )).fetchone()
    assert row[0] == "Mumbai-Bandra-West"
