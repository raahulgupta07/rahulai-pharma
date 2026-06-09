"""Adversarial unit tests for the Engineer semantic-layer whitelist.

This parser is the ENTIRE safety story for LLM-authored matview SQL. Every
attack we can think of must be rejected; every legitimate pharma matview must
pass. No DB needed — pure validation.
"""
import pytest

from dash.training.semantic_layer import (
    MatviewSpec, MatviewRejected, validate_matview_spec, build_ddl,
)

BASE = {"articles_clean", "balance_stock_2", "shop_flat"}


def _spec(select_sql, name="cat_stock", uidx="art_key", extra=None):
    return MatviewSpec(name=name, select_sql=select_sql, unique_index=uidx,
                       extra_indexes=extra or [])


# ── legitimate proposals MUST pass ───────────────────────────────────────────

def test_simple_select_passes():
    validate_matview_spec(_spec("SELECT article_code, brand FROM articles_clean"), BASE)


def test_full_join_aggregate_passes():
    sql = ("SELECT a.article_code::text AS art_key, s.site_code, "
           "SUM(s.stock_qty) AS stock_qty, (a.article_code IS NOT NULL) AS linked "
           "FROM articles_clean a "
           "FULL JOIN balance_stock_2 s ON s.article_code::text = a.article_code::text "
           "GROUP BY a.article_code, s.site_code")
    validate_matview_spec(_spec(sql, uidx="art_key, site_code"), BASE)


def test_cte_with_passes():
    sql = ("WITH t AS (SELECT category, SUM(stock_qty) q FROM balance_stock_2 "
           "GROUP BY category) SELECT category, q FROM t")
    validate_matview_spec(_spec(sql, uidx="category"), BASE)


def test_column_named_update_not_rejected():
    # whole-word match: a column called "updated_at" must not trip UPDATE
    validate_matview_spec(_spec("SELECT updated_at, article_code FROM articles_clean",
                                uidx="article_code"), BASE)


def test_build_ddl_shape():
    spec = _spec("SELECT article_code FROM articles_clean", uidx="article_code",
                 extra=["article_code"])
    stmts = build_ddl(spec)
    assert stmts[0].startswith("DROP MATERIALIZED VIEW IF EXISTS citypharma.")
    assert "CREATE MATERIALIZED VIEW citypharma.\"cat_stock\" AS" in stmts[1]
    assert stmts[1].rstrip().endswith("WITH DATA")
    assert "CREATE UNIQUE INDEX \"cat_stock_uidx\"" in stmts[2]
    assert any("ix0" in s for s in stmts[3:])
    # no stray semicolons inside any statement (executed individually)
    assert all(";" not in s.rstrip(";") for s in stmts)


# ── attacks MUST be rejected ─────────────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE articles_clean",
    "SELECT 1; DELETE FROM balance_stock_2",
    "SELECT 1;;",
])
def test_reject_multiple_statements(sql):
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec(sql), BASE)


@pytest.mark.parametrize("sql", [
    "DROP MATERIALIZED VIEW shop_flat",
    "DELETE FROM articles_clean",
    "UPDATE articles_clean SET brand='x'",
    "INSERT INTO articles_clean VALUES (1)",
    "TRUNCATE balance_stock_2",
    "ALTER TABLE articles_clean DROP COLUMN brand",
    "GRANT ALL ON articles_clean TO public",
    "CREATE TABLE evil (x int)",
    "REFRESH MATERIALIZED VIEW shop_flat",
    "SELECT * FROM articles_clean; CALL evil()",
])
def test_reject_write_or_ddl(sql):
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec(sql), BASE)


def test_reject_select_into():
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec("SELECT * INTO evil FROM articles_clean"), BASE)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM pg_catalog.pg_user",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM public.dash_users",
    "SELECT * FROM dash.dash_projects",
    "SELECT * FROM ai.agno_sessions",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM pg_shadow",
])
def test_reject_cross_schema(sql):
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec(sql), BASE)


@pytest.mark.parametrize("sql", [
    "SELECT 1 -- DROP TABLE x",
    "SELECT 1 /* hidden */ FROM articles_clean",
    "SELECT 1 FROM articles_clean /* DROP */",
])
def test_reject_comments(sql):
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec(sql), BASE)


def test_reject_non_select_start():
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec("EXPLAIN SELECT 1"), BASE)


@pytest.mark.parametrize("name", [
    "1bad", "Bad", "bad-name", "bad name", "bad;", "drop", "../x", "",
    "x" * 60,
])
def test_reject_bad_names(name):
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec("SELECT 1 FROM articles_clean", name=name), BASE)


def test_reject_shadow_base_table():
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec("SELECT 1 FROM articles_clean", name="articles_clean"), BASE)


def test_reject_missing_unique_index():
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec("SELECT 1 FROM articles_clean", uidx=""), BASE)


@pytest.mark.parametrize("uidx", [
    "art_key); DROP TABLE x --",
    "lower(brand)",
    "art_key, (1)",
    "*",
])
def test_reject_bad_unique_index(uidx):
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec("SELECT 1 FROM articles_clean", uidx=uidx), BASE)


def test_reject_bad_extra_index():
    with pytest.raises(MatviewRejected):
        validate_matview_spec(
            _spec("SELECT 1 FROM articles_clean", extra=["brand); DROP TABLE x --"]), BASE)


def test_reject_empty_and_oversized():
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec(""), BASE)
    with pytest.raises(MatviewRejected):
        validate_matview_spec(_spec("SELECT '" + "a" * 9000 + "'"), BASE)
