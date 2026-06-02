"""Tests for federation semantic_union — project-wide catalog + JOIN suggestions."""
from __future__ import annotations
import sys
import types
from unittest.mock import patch

import pytest

from dash.providers.federation import semantic_union
from dash.providers.federation.semantic_union import (
    TableEntry,
    JoinSuggestion,
    UnifiedCatalog,
    build,
    render_for_analyst,
    _suggest_joins,
    _normalize,
)


@pytest.fixture(autouse=True)
def _stub_db_session(monkeypatch):
    """Stub db.session so DB-touching code paths return empty."""
    mod = types.ModuleType("db.session")

    def get_sql_engine():
        raise RuntimeError("no db in tests")

    mod.get_sql_engine = get_sql_engine
    pkg = types.ModuleType("db")
    monkeypatch.setitem(sys.modules, "db", pkg)
    monkeypatch.setitem(sys.modules, "db.session", mod)
    yield


class _FakeProvider:
    def __init__(self, pid, dialect="postgresql", schema=None, degraded=False):
        self.id = pid
        self.dialect = dialect
        self.schema_blob = schema or {}
        self.degraded = degraded
        self.project_slug = "p"


def _stub_registry(monkeypatch, providers):
    class _Reg:
        def list_for_project(self, slug):
            return providers
    monkeypatch.setattr(semantic_union, "__name__", semantic_union.__name__)
    import dash.providers as dp
    monkeypatch.setattr(dp, "get_registry", lambda: _Reg(), raising=False)


def _stub_file_source_empty(monkeypatch):
    from dash.providers.federation import file_source as fs

    class _Cat:
        tables: list = []
    monkeypatch.setattr(fs, "discover", lambda slug: _Cat())


# ── tests ─────────────────────────────────────────────────────────────

def test_build_empty_project_returns_empty_catalog(monkeypatch):
    _stub_registry(monkeypatch, [])
    _stub_file_source_empty(monkeypatch)
    cat = build("noproj")
    assert cat.source_count == 0
    assert cat.table_count == 0
    assert cat.tables == []
    assert cat.join_suggestions == []


def test_build_aggregates_provider_tables(monkeypatch):
    p1 = _FakeProvider("pg1", "postgresql", schema={
        "tables": ["customers", "orders"],
        "columns": {
            "customers": ["customer_id", "name"],
            "orders": ["order_id", "customer_id", "total"],
        },
    })
    p2 = _FakeProvider("fab2", "tsql", schema={
        "tables": [{"name": "invoices"}],
        "columns": {"invoices": [{"name": "invoice_id"}, {"name": "customer_id"}]},
    })
    _stub_registry(monkeypatch, [p1, p2])
    _stub_file_source_empty(monkeypatch)

    cat = build("p")
    assert cat.source_count == 2
    assert cat.table_count == 3
    addrs = {t.full_address for t in cat.tables}
    assert "pg1.customers" in addrs
    assert "pg1.orders" in addrs
    assert "fab2.invoices" in addrs


def test_join_suggestion_explicit_fk():
    tables = [
        TableEntry(provider_id="pg1", table_name="orders",
                   full_address="pg1.orders",
                   columns=["order_id", "customer_id"],
                   foreign_keys=[{"from_table": "orders", "from_col": "customer_id",
                                  "to_table": "customers", "to_col": "customer_id"}]),
        TableEntry(provider_id="fab2", table_name="customers",
                   full_address="fab2.customers",
                   columns=["customer_id", "name"]),
    ]
    sugs = _suggest_joins(tables)
    fk_sugs = [s for s in sugs if s.reason == "explicit_fk"]
    assert len(fk_sugs) == 1
    assert fk_sugs[0].confidence == 0.95
    assert fk_sugs[0].left_table == "pg1.orders"
    assert fk_sugs[0].right_table == "fab2.customers"


def test_join_suggestion_name_match_with_id_suffix():
    tables = [
        TableEntry(provider_id="pg1", table_name="orders",
                   full_address="pg1.orders", columns=["customer_id", "total"]),
        TableEntry(provider_id="fab2", table_name="customers",
                   full_address="fab2.customers", columns=["customer_id", "name"]),
    ]
    sugs = _suggest_joins(tables)
    nm = [s for s in sugs if s.reason == "name_match"]
    assert any(s.left_column == "customer_id" and s.right_column == "customer_id"
               for s in nm)
    # _id suffix → 0.80 confidence
    assert any(s.confidence == 0.80 for s in nm)


def test_join_suggestion_fuzzy_match():
    tables = [
        TableEntry(provider_id="pg1", table_name="orders",
                   full_address="pg1.orders", columns=["customer_id"]),
        TableEntry(provider_id="fab2", table_name="customers",
                   full_address="fab2.customers", columns=["customer_key"]),
    ]
    sugs = _suggest_joins(tables)
    fz = [s for s in sugs if s.reason == "fuzzy_match"]
    assert len(fz) >= 1
    assert fz[0].confidence == 0.55


def test_join_suggestion_skips_generic_cols():
    tables = [
        TableEntry(provider_id="pg1", table_name="a", full_address="pg1.a",
                   columns=["id", "name"]),
        TableEntry(provider_id="fab2", table_name="b", full_address="fab2.b",
                   columns=["id", "name"]),
    ]
    sugs = _suggest_joins(tables)
    nm = [s for s in sugs if s.reason == "name_match"]
    # 0.65 - 0.30 = 0.35 → still passes the 0.30 floor; ensure they are *low*
    for s in nm:
        assert s.confidence <= 0.36, f"generic col not penalized: {s}"


def test_join_suggestion_skips_same_provider():
    tables = [
        TableEntry(provider_id="pg1", table_name="a", full_address="pg1.a",
                   columns=["customer_id"]),
        TableEntry(provider_id="pg1", table_name="b", full_address="pg1.b",
                   columns=["customer_id"]),
    ]
    sugs = _suggest_joins(tables)
    assert sugs == []  # same source — not federation-relevant


def test_normalize_strips_suffixes():
    assert _normalize("customer_id") == "customer"
    assert _normalize("customer_key") == "customer"
    assert _normalize("cust_number") == "cust"
    assert _normalize("foo") == "foo"


def test_render_returns_empty_for_single_source(monkeypatch):
    p1 = _FakeProvider("pg1", schema={"tables": ["t1"], "columns": {"t1": ["a"]}})
    _stub_registry(monkeypatch, [p1])
    _stub_file_source_empty(monkeypatch)
    out = render_for_analyst("p")
    assert out == ""


def test_render_includes_sources_and_joins(monkeypatch):
    p1 = _FakeProvider("pg1", "postgresql", schema={
        "tables": ["orders"],
        "columns": {"orders": ["customer_id", "total"]},
    })
    p2 = _FakeProvider("fab2", "tsql", schema={
        "tables": ["customers"],
        "columns": {"customers": ["customer_id", "name"]},
    })
    _stub_registry(monkeypatch, [p1, p2])
    _stub_file_source_empty(monkeypatch)
    out = render_for_analyst("p")
    assert "DATA SOURCES UNIFIED" in out
    assert "pg1" in out
    assert "fab2" in out
    assert "orders" in out
    assert "customers" in out
    assert "SUGGESTED JOIN KEYS" in out
    assert "customer_id" in out


def test_render_respects_max_chars(monkeypatch):
    p1 = _FakeProvider("pg1", "postgresql", schema={
        "tables": [f"t{i}" for i in range(20)],
        "columns": {f"t{i}": [f"col{j}" for j in range(10)] for i in range(20)},
    })
    p2 = _FakeProvider("fab2", "tsql", schema={
        "tables": [f"u{i}" for i in range(20)],
        "columns": {f"u{i}": [f"col{j}" for j in range(10)] for i in range(20)},
    })
    _stub_registry(monkeypatch, [p1, p2])
    _stub_file_source_empty(monkeypatch)
    out = render_for_analyst("p", max_chars=500)
    assert len(out) <= 500 + len("\n... [truncated]")
    assert "[truncated]" in out
