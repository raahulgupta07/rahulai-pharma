"""Dash-OS Phase 11A — Sub-agent factory tests.

Uses an in-memory fake engine that emulates the small subset of SQLAlchemy
behaviour exercised by ``dash.agents.factory`` and ``dash.tools.spawn_tools``.
Real Agno agents are NOT spawned — ``AgentFactory.instantiate`` is monkey-
patched.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Fake in-memory engine emulating just enough of SQLAlchemy for our code.
# ─────────────────────────────────────────────────────────────────────────────
class _Row(tuple):
    """Tuple-with-mapping wrapper so both index and key access work."""
    def __new__(cls, items):
        # items: list[tuple[str, any]]
        keys = [k for k, _ in items]
        values = [v for _, v in items]
        obj = super().__new__(cls, values)
        obj._keys = keys
        obj._map = dict(items)
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._map[key]
        return super().__getitem__(key)


class _MappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows, rowcount=0):
        self._rows = [_Row(list(r.items())) if isinstance(r, dict) else r for r in rows]
        self.rowcount = rowcount

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        if not self._rows:
            return None
        return self._rows[0][0]

    def mappings(self):
        # Return objects that are already dict-like
        return _MappingsResult([dict(r._map) if isinstance(r, _Row) else dict(r) for r in self._rows])


class _Conn:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql_text, params=None):
        sql = str(sql_text).strip()
        params = params or {}
        # Normalize whitespace for matching
        norm = re.sub(r"\s+", " ", sql).upper()
        s = self.store

        # ── SELECT id, usage_count FROM dash_custom_agents (upsert lookup)
        if norm.startswith("SELECT ID, USAGE_COUNT FROM DASH.DASH_CUSTOM_AGENTS"):
            for row in s["agents"].values():
                if row["project_slug"] == params.get("ps") and row["name"] == params.get("nm"):
                    return _Result([(row["id"], row.get("usage_count", 0))])
            return _Result([])

        # ── SELECT id FROM dash_custom_agents (cap-check existing lookup)
        if norm.startswith("SELECT ID FROM DASH.DASH_CUSTOM_AGENTS"):
            for row in s["agents"].values():
                if row["project_slug"] == params.get("ps") and row["name"] == params.get("nm"):
                    return _Result([{"id": row["id"], "usage_count": row.get("usage_count", 0)}])
            return _Result([])

        # ── SELECT id, name, purpose, base_agent, ... FROM dash_custom_agents (build_or_reuse)
        if norm.startswith("SELECT ID, NAME, PURPOSE, BASE_AGENT, SCOPED_SKILLS,"):
            for row in s["agents"].values():
                if row["project_slug"] == params.get("ps") and row["name"] == params.get("nm"):
                    return _Result([{
                        "id": row["id"], "name": row["name"], "purpose": row.get("purpose"),
                        "base_agent": row.get("base_agent"),
                        "scoped_skills": row.get("scoped_skills", []),
                        "scoped_tools": row.get("scoped_tools", []),
                        "persona": row.get("persona"),
                        "extra_instructions": row.get("extra_instructions", ""),
                        "usage_count": row.get("usage_count", 0),
                        "enabled": row.get("enabled", True),
                    }])
            return _Result([])

        # ── COUNT(*) for cap check
        if norm.startswith("SELECT COUNT(*) FROM DASH.DASH_CUSTOM_AGENTS"):
            ps = params.get("ps")
            c = sum(1 for r in s["agents"].values()
                    if r["project_slug"] == ps and r.get("enabled", True))
            return _Result([(c,)])

        # ── INSERT INTO dash_custom_agents
        if norm.startswith("INSERT INTO DASH.DASH_CUSTOM_AGENTS"):
            row = {
                "id": params["id"], "project_slug": params["ps"], "name": params["nm"],
                "purpose": params.get("pu"), "base_agent": params.get("ba"),
                "agent_md": params.get("md"),
                "scoped_skills": _safe_json(params.get("ss")),
                "scoped_tools": _safe_json(params.get("st")),
                "persona": params.get("per"),
                "extra_instructions": params.get("ei"),
                "created_by_agent": params.get("cba"),
                "created_by_user": params.get("cbu"),
                "usage_count": 0, "enabled": True,
                "last_used_at": None, "success_rate": None,
                "created_at": "2026-05-16T00:00:00",
            }
            s["agents"][params["id"]] = row
            return _Result([], rowcount=1)

        # ── UPDATE ... SET purpose=... (definition update) or usage_count bump or enabled=false
        if norm.startswith("UPDATE DASH.DASH_CUSTOM_AGENTS"):
            updated = 0
            if "ENABLED = FALSE" in norm:
                # soft delete by name+project_slug
                for r in s["agents"].values():
                    if r["name"] == params.get("nm") and r["project_slug"] == params.get("ps"):
                        r["enabled"] = False
                        updated += 1
                return _Result([], rowcount=updated)
            if "USAGE_COUNT" in norm:
                rid = params.get("id")
                if rid in s["agents"]:
                    s["agents"][rid]["usage_count"] = s["agents"][rid].get("usage_count", 0) + 1
                    updated = 1
                return _Result([], rowcount=updated)
            # full definition update
            rid = params.get("id")
            if rid in s["agents"]:
                r = s["agents"][rid]
                r["purpose"] = params.get("pu", r.get("purpose"))
                r["base_agent"] = params.get("ba", r.get("base_agent"))
                r["agent_md"] = params.get("md", r.get("agent_md"))
                r["scoped_skills"] = _safe_json(params.get("ss"))
                r["scoped_tools"] = _safe_json(params.get("st"))
                r["persona"] = params.get("per")
                r["extra_instructions"] = params.get("ei", "")
                updated = 1
            return _Result([], rowcount=updated)

        # ── List custom agents
        if norm.startswith("SELECT ID, NAME, PURPOSE, USAGE_COUNT, SUCCESS_RATE"):
            ps = params.get("ps")
            kw = (params.get("kw") or "").lower()
            rows = [r for r in s["agents"].values()
                    if r.get("enabled", True)
                    and (r["project_slug"] == ps or r["project_slug"] is None)
                    and (not kw or kw in r["name"].lower()
                         or kw in (r.get("purpose") or "").lower())]
            rows.sort(key=lambda r: (-(r.get("usage_count") or 0), r["name"]))
            rows = rows[: int(params.get("lim") or 20)]
            return _Result([{
                "id": r["id"], "name": r["name"], "purpose": r.get("purpose"),
                "usage_count": r.get("usage_count", 0),
                "success_rate": r.get("success_rate"),
                "project_slug": r["project_slug"], "enabled": r.get("enabled", True),
            } for r in rows])

        # ── Audit insert
        if norm.startswith("INSERT INTO DASH.DASH_SUBAGENT_RUNS"):
            s["runs"].append(dict(params))
            return _Result([], rowcount=1)

        # ── get_custom_agent_detail head
        if norm.startswith("SELECT ID, NAME, PURPOSE, BASE_AGENT, AGENT_MD"):
            rows = [r for r in s["agents"].values()
                    if r["name"] == params.get("nm")
                    and (r["project_slug"] == params.get("ps") or r["project_slug"] is None)]
            if not rows:
                return _Result([])
            r = rows[0]
            return _Result([dict(r)])

        # ── recent runs in get_detail
        if norm.startswith("SELECT ID, PARENT_RUN_ID, STATUS, LATENCY_MS"):
            runs = [r for r in s["runs"] if r.get("aid") == params.get("aid")][-10:]
            return _Result([{
                "id": i, "parent_run_id": r.get("pr"), "status": r.get("stat"),
                "latency_ms": r.get("lat"), "created_at": "2026-05-16T00:00:00",
                "output_preview": (r.get("op") or "")[:240],
            } for i, r in enumerate(runs)])

        return _Result([])


class _Engine:
    def __init__(self):
        self.store = {"agents": {}, "runs": []}

    def begin(self):
        return _Conn(self.store)

    def connect(self):
        return _Conn(self.store)


def _safe_json(v):
    import json as _json
    if v is None:
        return []
    if isinstance(v, (list, dict)):
        return v
    try:
        return _json.loads(v)
    except Exception:
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def fake_engine(monkeypatch):
    eng = _Engine()
    from dash.agents import factory as F
    from dash.tools import spawn_tools as ST
    monkeypatch.setattr(F, "_get_engine", lambda: eng)
    monkeypatch.setattr(ST, "_get_engine", lambda: eng)

    # Stub out Agent instantiation so we never need Agno
    class _StubAgent:
        def __init__(self, **kw):
            self.kw = kw

        def run(self, brief):
            return f"ran: {brief}"

    monkeypatch.setattr(F.AgentFactory, "instantiate", staticmethod(lambda spec: _StubAgent(name=spec.name)))

    # Stub the cost cap check so tests don't hit real cost_guard
    monkeypatch.setattr(ST, "_cost_cap_blocked", lambda ps: None)
    return eng


@pytest.fixture
def agi_on(monkeypatch):
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")
    yield
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)


@pytest.fixture
def project_ctx(monkeypatch):
    from dash.tools import spawn_tools as ST
    monkeypatch.setattr(ST, "_ctx", lambda: {
        "project_slug": "proj_test", "user_id": 1,
        "agent_name": "Analyst", "run_id": "run_abc",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Helper: invoke a tool (handles Agno wrapping)
# ─────────────────────────────────────────────────────────────────────────────
def _call(tool, **kw):
    fn = getattr(tool, "entrypoint", None) or getattr(tool, "fn", None) or tool
    return fn(**kw)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────
def test_migration_exists():
    p = ROOT / "db" / "migrations" / "053_custom_agents.sql"
    assert p.exists(), "053_custom_agents.sql missing"
    txt = p.read_text()
    assert "dash.dash_custom_agents" in txt
    assert "dash.dash_subagent_runs" in txt
    assert "UNIQUE(project_slug, name)" in txt
    assert "idx_cagent_project" in txt


def test_flag_off_returns_no_db_write(fake_engine, project_ctx, monkeypatch):
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    from dash.tools.spawn_tools import _spawn_subagent
    out = _spawn_subagent(name="X", purpose="purpose")
    assert out["ok"] is False
    assert out["reason"] == "experimental_agi_off"
    assert fake_engine.store["agents"] == {}
    assert fake_engine.store["runs"] == []


def test_first_spawn_creates_row(fake_engine, project_ctx, agi_on):
    from dash.tools.spawn_tools import _spawn_subagent
    out = _spawn_subagent(name="Auditor", purpose="audit costs", base_agent="Analyst")
    assert out["ok"], out
    assert out["was_new"] is True
    assert out["reused"] is False
    assert len(fake_engine.store["agents"]) == 1
    saved = next(iter(fake_engine.store["agents"].values()))
    assert saved["name"] == "Auditor"
    assert saved["project_slug"] == "proj_test"


def test_second_spawn_reuses(fake_engine, project_ctx, agi_on):
    from dash.tools.spawn_tools import _spawn_subagent
    a = _spawn_subagent(name="Auditor", purpose="audit costs")
    assert a["was_new"] is True
    b = _spawn_subagent(name="Auditor", purpose="audit costs again")
    assert b["was_new"] is False
    assert b["reused"] is True
    saved = next(iter(fake_engine.store["agents"].values()))
    # was_new path inserted 0, reuse bumps +1 = 1
    assert saved["usage_count"] >= 1


def test_depth_enforcement(fake_engine, project_ctx, agi_on):
    from dash.tools import spawn_tools as ST
    token = ST.is_subagent_run.set(True)
    try:
        out = ST._spawn_subagent(name="Nested", purpose="should be denied")
    finally:
        ST.is_subagent_run.reset(token)
    assert out["ok"] is False
    assert out["reason"] == "nesting_denied"
    # Audit row exists with denied_nesting
    assert any(r.get("stat") == "denied_nesting" for r in fake_engine.store["runs"])
    # No agent definition was created
    assert fake_engine.store["agents"] == {}


def test_cap_enforcement(fake_engine, project_ctx, agi_on, monkeypatch):
    monkeypatch.setenv("DASH_CUSTOM_AGENT_CAP", "2")
    from dash.tools.spawn_tools import _spawn_subagent
    a = _spawn_subagent(name="A1", purpose="p")
    b = _spawn_subagent(name="A2", purpose="p")
    c = _spawn_subagent(name="A3", purpose="p")
    assert a["ok"] and b["ok"]
    assert c["ok"] is False
    assert c["reason"] == "cap_reached"


def test_list_custom_agents_ordered_by_usage(fake_engine, project_ctx, agi_on):
    from dash.tools.spawn_tools import _spawn_subagent, _list_custom_agents
    _spawn_subagent(name="LowUse", purpose="x")
    _spawn_subagent(name="HighUse", purpose="x")
    # Bump HighUse usage by spawning again twice
    _spawn_subagent(name="HighUse", purpose="x")
    _spawn_subagent(name="HighUse", purpose="x")
    out = _list_custom_agents()
    assert out["ok"]
    names = [a["name"] for a in out["agents"]]
    assert names[0] == "HighUse"
    assert "LowUse" in names


def test_agent_md_frontmatter_matches_skill_md_pattern():
    from dash.agents.factory import AgentFactory, SubAgentSpec
    spec = SubAgentSpec(
        name="Inspector", purpose="inspect data quality",
        base_agent="Analyst",
        scoped_skills=["skl_aa11bb22"], scoped_tools=["run_sql_query", "search_all"],
        persona="meticulous", extra_instructions="Check NULL rates per column.",
    )
    md = AgentFactory._generate_agent_md(spec)
    # YAML frontmatter delimited by --- on its own lines
    assert md.startswith("---\n")
    head, _, body = md.partition("---\n\n")
    # head ends with closing ---
    assert "name: Inspector" in head
    assert "purpose: inspect data quality" in head
    assert "base_agent: Analyst" in head
    assert "scoped_skills:" in head and "skl_aa11bb22" in head
    assert "scoped_tools:" in head and "run_sql_query" in head
    assert "persona: meticulous" in head
    # body contains title + body text
    assert "# Inspector" in body
    assert "Check NULL rates per column." in body
