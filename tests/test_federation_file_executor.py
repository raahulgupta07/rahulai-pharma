"""Tests for federation file query executor."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from dash.providers.federation import file_executor
from dash.providers.federation.file_executor import (
    execute_file_sql,
    _load_table,
    _parse_simple_sql,
    _sql_where_to_pandas_query,
    list_loadable_tables,
)


@pytest.fixture
def patch_knowledge_dir(tmp_path, monkeypatch):
    """Redirect KNOWLEDGE_DIR to tmp_path."""
    monkeypatch.setattr(file_executor, "KNOWLEDGE_DIR", tmp_path)
    return tmp_path


def _mk_provider(slug="proj_x", pid="src42"):
    return SimpleNamespace(
        id=pid,
        project_slug=slug,
        dialect="files",
    )


def _write_parquet(path: Path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path)
    except Exception:
        pytest.skip("pyarrow/fastparquet not installed")


# ------------------------ _load_table ------------------------

def test_load_table_finds_parquet(patch_knowledge_dir):
    base = patch_knowledge_dir / "proj_x" / "source_42" / "sample"
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    _write_parquet(base / "sales.parquet", df)

    out = _load_table("proj_x", "sales", "src42")
    assert out is not None
    assert len(out) == 3
    assert list(out.columns) == ["a", "b"]


def test_load_table_finds_csv_fallback(patch_knowledge_dir):
    base = patch_knowledge_dir / "proj_x" / "source_42" / "sample"
    base.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"a": [10, 20], "b": ["p", "q"]})
    df.to_csv(base / "stuff.csv", index=False)

    out = _load_table("proj_x", "stuff", "src42")
    assert out is not None
    assert len(out) == 2
    assert out["a"].tolist() == [10, 20]


def test_load_table_finds_json_list(patch_knowledge_dir):
    base = patch_knowledge_dir / "proj_x" / "tables"
    base.mkdir(parents=True, exist_ok=True)
    data = [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]
    (base / "things.json").write_text(json.dumps(data))

    out = _load_table("proj_x", "things", "")
    assert out is not None
    assert len(out) == 2
    assert "x" in out.columns


def test_load_table_returns_none_for_missing(patch_knowledge_dir):
    out = _load_table("proj_x", "doesnt_exist", "src1")
    assert out is None


# ------------------------ _parse_simple_sql ------------------------

def test_parse_simple_sql_basic():
    p = _parse_simple_sql("SELECT a, b FROM mytable")
    assert p["error"] is None
    assert p["from"] == "mytable"
    assert "a" in p["select"] and "b" in p["select"]


def test_parse_simple_sql_with_where():
    p = _parse_simple_sql("SELECT * FROM t WHERE a = 5")
    assert p["error"] is None
    assert p["from"] == "t"
    assert "a" in p["where"]
    assert "5" in p["where"]


def test_parse_simple_sql_with_group_by():
    p = _parse_simple_sql("SELECT region FROM sales GROUP BY region")
    assert p["error"] is None
    assert p["from"] == "sales"
    assert any("region" in g for g in p["group_by"])


def test_parse_simple_sql_with_limit():
    p = _parse_simple_sql("SELECT * FROM t LIMIT 25")
    assert p["error"] is None
    assert p["limit"] == 25


# ------------------------ execute_file_sql ------------------------

def test_execute_file_sql_with_filter(patch_knowledge_dir):
    base = patch_knowledge_dir / "proj_x" / "tables"
    base.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"region": ["E", "W", "E"], "sales": [10, 20, 30]})
    df.to_csv(base / "orders.csv", index=False)

    provider = _mk_provider(slug="proj_x", pid="")
    out = execute_file_sql(provider, "SELECT region, sales FROM orders WHERE region = 'E'")
    assert len(out) == 2
    assert set(out["region"].unique()) == {"E"}


def test_execute_file_sql_with_group_by(patch_knowledge_dir):
    base = patch_knowledge_dir / "proj_x" / "tables"
    base.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"region": ["E", "W", "E", "W", "E"], "sales": [1, 2, 3, 4, 5]})
    df.to_csv(base / "orders.csv", index=False)

    provider = _mk_provider(slug="proj_x", pid="")
    out = execute_file_sql(provider, "SELECT region FROM orders GROUP BY region")
    assert "count" in out.columns
    assert len(out) == 2
    counts = dict(zip(out["region"], out["count"]))
    assert counts["E"] == 3
    assert counts["W"] == 2


def test_execute_file_sql_caps_at_max_rows(patch_knowledge_dir):
    base = patch_knowledge_dir / "proj_x" / "tables"
    base.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"a": list(range(50))})
    df.to_csv(base / "big.csv", index=False)

    provider = _mk_provider(slug="proj_x", pid="")
    out = execute_file_sql(provider, "SELECT * FROM big", max_rows=10)
    assert len(out) == 10


def test_execute_file_sql_with_order_by_limit(patch_knowledge_dir):
    base = patch_knowledge_dir / "proj_x" / "tables"
    base.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"a": [3, 1, 2, 5, 4]})
    df.to_csv(base / "nums.csv", index=False)

    provider = _mk_provider(slug="proj_x", pid="")
    out = execute_file_sql(provider, "SELECT a FROM nums ORDER BY a DESC LIMIT 2")
    assert out["a"].tolist() == [5, 4]


def test_execute_file_sql_missing_table_returns_empty(patch_knowledge_dir):
    provider = _mk_provider(slug="proj_x", pid="")
    out = execute_file_sql(provider, "SELECT * FROM nope")
    assert len(out) == 0


# ------------------------ where translator ------------------------

def test_sql_where_to_pandas_query_translation():
    out = _sql_where_to_pandas_query("a = 5 AND b IS NOT NULL")
    assert "==" in out
    assert "&" in out
    assert ".notna()" in out


# ------------------------ list_loadable_tables ------------------------

def test_list_loadable_tables_finds_files(patch_knowledge_dir):
    base = patch_knowledge_dir / "proj_x"
    (base / "tables").mkdir(parents=True, exist_ok=True)
    (base / "tables" / "a.csv").write_text("x\n1\n")
    (base / "tables" / "b.json").write_text("{}")

    out = list_loadable_tables("proj_x")
    names = {r["table_name"] for r in out}
    assert "a" in names
    assert "b" in names
    formats = {r["format"] for r in out}
    assert {"csv", "json"} <= formats
