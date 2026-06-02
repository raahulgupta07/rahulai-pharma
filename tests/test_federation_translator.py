"""Tests for federation SQL dialect translator."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure repo root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dash.providers.federation.translator import (  # noqa: E402
    TranslationResult,
    get_supported_dialects,
    normalize_to_canonical,
    to_dialect,
    translate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _force_regex(monkeypatch):
    """Force the regex fallback path by making sqlglot import fail."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sqlglot":
            raise ImportError("forced")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def test_no_op_when_same_dialect():
    sql = "SELECT * FROM users WHERE id = 1"
    result = translate(sql, source_dialect="postgres", target_dialect="postgres")
    assert isinstance(result, TranslationResult)
    assert result.translated_sql == sql
    assert result.warnings == []


def test_no_op_with_alias_dialect_names():
    sql = "SELECT 1"
    # postgresql -> postgres should still be no-op
    result = translate(sql, source_dialect="postgresql", target_dialect="postgres")
    assert result.translated_sql == sql


def test_get_supported_dialects():
    dialects = get_supported_dialects()
    assert "postgres" in dialects
    assert "tsql" in dialects
    assert "mysql" in dialects
    assert "duckdb" in dialects


# ---------------------------------------------------------------------------
# Postgres ↔ T-SQL via regex fallback
# ---------------------------------------------------------------------------

def test_postgres_to_tsql_limit_to_top(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT id FROM users LIMIT 10"
    result = translate(sql, source_dialect="postgres", target_dialect="tsql")
    assert "TOP 10" in result.translated_sql
    assert "LIMIT" not in result.translated_sql.upper()


def test_postgres_to_tsql_double_cast(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT id::int FROM users"
    result = translate(sql, source_dialect="postgres", target_dialect="tsql")
    assert "CAST(id AS int)" in result.translated_sql


def test_postgres_to_tsql_now_to_getdate(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT NOW()"
    result = translate(sql, source_dialect="postgres", target_dialect="tsql")
    assert "GETDATE()" in result.translated_sql


def test_tsql_to_postgres_top_to_limit(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT TOP 5 id FROM users"
    result = translate(sql, source_dialect="tsql", target_dialect="postgres")
    assert "LIMIT 5" in result.translated_sql
    assert "TOP" not in result.translated_sql.upper()


def test_tsql_to_postgres_brackets_to_quotes(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT [user_id] FROM [users]"
    result = translate(sql, source_dialect="tsql", target_dialect="postgres")
    assert '"user_id"' in result.translated_sql
    assert '"users"' in result.translated_sql


def test_tsql_to_postgres_isnull_to_coalesce(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT ISNULL(name, 'unknown') FROM users"
    result = translate(sql, source_dialect="tsql", target_dialect="postgres")
    assert "COALESCE(" in result.translated_sql


# ---------------------------------------------------------------------------
# Postgres ↔ MySQL
# ---------------------------------------------------------------------------

def test_postgres_to_mysql_quotes_to_backticks(monkeypatch):
    _force_regex(monkeypatch)
    sql = 'SELECT "user_id" FROM "users"'
    result = translate(sql, source_dialect="postgres", target_dialect="mysql")
    assert "`user_id`" in result.translated_sql
    assert "`users`" in result.translated_sql


def test_mysql_to_postgres_backticks_to_quotes(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT `user_id` FROM `users`"
    result = translate(sql, source_dialect="mysql", target_dialect="postgres")
    assert '"user_id"' in result.translated_sql
    assert '"users"' in result.translated_sql


def test_postgres_to_mysql_ilike_warns(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT 1 FROM users WHERE name ILIKE 'a%'"
    result = translate(sql, source_dialect="postgres", target_dialect="mysql")
    assert "LIKE" in result.translated_sql.upper()
    assert any("ILIKE" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Unknown dialects + fallbacks
# ---------------------------------------------------------------------------

def test_unknown_dialect_falls_back():
    sql = "SELECT 1"
    # Unknown source dialect should map to postgres (default)
    result = translate(sql, source_dialect="oracle_db", target_dialect="bogus")
    assert result.source_dialect == "postgres"
    assert result.target_dialect == "postgres"
    assert result.translated_sql == sql


def test_translation_emits_warnings_on_imperfect(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT * FROM events WHERE ts > NOW() - INTERVAL '1 day'"
    result = translate(sql, source_dialect="postgres", target_dialect="tsql")
    # INTERVAL flag is appended as a warning
    assert any("INTERVAL" in w for w in result.warnings)


def test_warnings_when_sqlglot_fails(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT NOW()"
    result = translate(sql, source_dialect="postgres", target_dialect="tsql")
    # sqlglot import failed → warning recorded
    assert any("sqlglot" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def test_normalize_to_canonical():
    sql = "SELECT 1"
    out = normalize_to_canonical(sql, "postgres")
    # canonical = postgres → unchanged
    assert out == sql


def test_normalize_to_canonical_from_tsql(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT TOP 3 id FROM users"
    out = normalize_to_canonical(sql, "tsql")
    assert "LIMIT 3" in out


def test_to_dialect_helper(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT id FROM users LIMIT 7"
    out = to_dialect(sql, target_dialect="tsql", source_dialect="postgres")
    assert "TOP 7" in out


# ---------------------------------------------------------------------------
# T-SQL ↔ MySQL
# ---------------------------------------------------------------------------

def test_tsql_to_mysql_top_to_limit(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT TOP 4 id FROM [users]"
    result = translate(sql, source_dialect="tsql", target_dialect="mysql")
    assert "LIMIT 4" in result.translated_sql
    assert "`users`" in result.translated_sql


def test_mysql_to_tsql_limit_to_top(monkeypatch):
    _force_regex(monkeypatch)
    sql = "SELECT `id` FROM `users` LIMIT 8"
    result = translate(sql, source_dialect="mysql", target_dialect="tsql")
    assert "TOP 8" in result.translated_sql
    assert "[users]" in result.translated_sql
