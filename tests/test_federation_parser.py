"""Tests for federation SQL parser + source resolver."""
from __future__ import annotations
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from dash.providers.federation import parser as fed_parser
from dash.providers.federation import resolver as fed_resolver

try:
    import sqlglot  # noqa: F401
    _HAS_SQLGLOT = True
except ImportError:
    _HAS_SQLGLOT = False


def _ok(result):
    """Either sqlglot-parsed cleanly, or regex-fallback was used."""
    return result.error is None or result.error == "sqlglot not installed"


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

def test_parse_single_source_not_federated():
    sql = "SELECT id FROM fabric_42.orders"
    result = fed_parser.parse(sql)
    assert _ok(result)
    assert result.is_federated is False
    assert result.provider_ids == {"fabric_42"}
    assert len(result.table_refs) >= 1
    ref = next(r for r in result.table_refs if r.provider_id == "fabric_42")
    assert ref.table_name == "orders"


def test_parse_two_sources_federated():
    sql = (
        "SELECT o.id, c.name FROM fabric_42.orders o "
        "JOIN postgres_local.customers c ON o.cid = c.id"
    )
    result = fed_parser.parse(sql)
    assert _ok(result)
    assert result.is_federated is True
    assert {"fabric_42", "postgres_local"}.issubset(result.provider_ids)


@pytest.mark.skipif(not _HAS_SQLGLOT, reason="sqlglot not installed; alias parsing requires AST")
def test_parse_with_aliases():
    sql = "SELECT o.id FROM fabric_42.orders AS o"
    result = fed_parser.parse(sql)
    assert result.error is None
    assert len(result.table_refs) == 1
    assert result.table_refs[0].alias == "o"
    assert result.table_refs[0].provider_id == "fabric_42"


def test_regex_fallback_when_sqlglot_missing():
    """When sqlglot unavailable, fall back to regex."""
    sql = "SELECT * FROM fabric_42.orders JOIN pg_local.customers ON 1=1"

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sqlglot" or name.startswith("sqlglot."):
            raise ImportError("simulated missing sqlglot")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        result = fed_parser.parse(sql)

    assert result.error == "sqlglot not installed"
    assert "fabric_42" in result.provider_ids
    assert "pg_local" in result.provider_ids
    assert result.is_federated is True


def test_is_select_only_blocks_drop():
    assert fed_parser.is_select_only("DROP TABLE foo") is False
    assert fed_parser.is_select_only("SELECT 1; DROP TABLE foo") is False


def test_is_select_only_blocks_stacked():
    assert fed_parser.is_select_only("SELECT 1; SELECT 2") is False


def test_is_select_only_allows_with():
    assert fed_parser.is_select_only("WITH t AS (SELECT 1) SELECT * FROM t") is True
    assert fed_parser.is_select_only("SELECT 1") is True
    # Trailing semicolon ok
    assert fed_parser.is_select_only("SELECT 1;") is True


@pytest.mark.skipif(not _HAS_SQLGLOT, reason="sqlglot not installed; column extraction requires AST")
def test_extract_select_columns():
    cols = fed_parser.extract_select_columns(
        "SELECT id, name FROM fabric_42.orders"
    )
    assert "id" in cols
    assert "name" in cols


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

class _FakeProvider:
    def __init__(self, pid, project_slug, dialect="postgres",
                 agent_scope="project", degraded=False, last_error=None):
        self.id = pid
        self.project_slug = project_slug
        self.dialect = dialect
        self.agent_scope = agent_scope
        self.degraded = degraded
        self.last_error = last_error


class _FakeRegistry:
    def __init__(self, providers_by_project):
        self._by_project = providers_by_project

    def list_for_project(self, slug):
        return list(self._by_project.get(slug, []))


@pytest.fixture
def patched_registry():
    """Patch dash.providers.get_registry to return a controllable fake."""
    state = {"registry": None}

    def _set(reg):
        state["registry"] = reg

    def _get_registry():
        return state["registry"]

    # Inject into dash.providers module
    import dash.providers as dp
    original = getattr(dp, "get_registry", None)
    dp.get_registry = _get_registry
    yield _set
    if original is not None:
        dp.get_registry = original
    else:
        delattr(dp, "get_registry")


def test_resolve_unknown_source_returns_error(patched_registry):
    reg = _FakeRegistry({"proj_a": [_FakeProvider("fabric_42", "proj_a")]})
    patched_registry(reg)

    result = fed_resolver.resolve(["unknown_src"], "proj_a")
    assert result.all_accessible is False
    assert any("unknown source" in e for e in result.errors)
    assert len(result.sources) == 1
    assert result.sources[0].accessible is False


def test_resolve_returns_all_accessible_when_ok(patched_registry):
    reg = _FakeRegistry({
        "proj_a": [
            _FakeProvider("fabric_42", "proj_a"),
            _FakeProvider("pg_local", "proj_a"),
        ]
    })
    patched_registry(reg)

    result = fed_resolver.resolve(["fabric_42", "pg_local"], "proj_a")
    assert result.all_accessible is True
    assert result.errors == []
    assert len(result.sources) == 2
    assert all(s.accessible for s in result.sources)


def test_resolve_blocks_wrong_scope(patched_registry):
    reg = _FakeRegistry({
        "proj_a": [
            _FakeProvider("secret_src", "proj_a", agent_scope="researcher_only"),
        ]
    })
    patched_registry(reg)

    result = fed_resolver.resolve(
        ["secret_src"], "proj_a", requesting_agent_scope="analyst"
    )
    assert result.all_accessible is False
    assert any("scope" in e for e in result.errors)
    assert result.sources[0].accessible is False


def test_resolve_blocks_degraded(patched_registry):
    reg = _FakeRegistry({
        "proj_a": [
            _FakeProvider("fabric_42", "proj_a",
                          degraded=True, last_error="conn refused"),
        ]
    })
    patched_registry(reg)

    result = fed_resolver.resolve(["fabric_42"], "proj_a")
    assert result.all_accessible is False
    assert any("degraded" in e for e in result.errors)
    assert result.sources[0].error == "degraded"


def test_resolve_never_crosses_project(patched_registry):
    """Project A query asking for Project B's source must reject."""
    reg = _FakeRegistry({
        "proj_a": [_FakeProvider("a_src", "proj_a")],
        "proj_b": [_FakeProvider("b_src", "proj_b")],
    })
    patched_registry(reg)

    # Query proj_a but reference b_src
    result = fed_resolver.resolve(["b_src"], "proj_a")
    assert result.all_accessible is False
    assert any("b_src" in e and "proj_a" in e for e in result.errors)
    assert result.sources[0].accessible is False
    # Confirm resolver did NOT silently fetch from proj_b
    assert result.sources[0].provider is None
