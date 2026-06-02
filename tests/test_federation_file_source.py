"""Tests for federation file source adapter."""
from __future__ import annotations
import json
import sys
import types
from pathlib import Path

import pytest

from dash.providers.federation import file_source
from dash.providers.federation.file_source import (
    FileTable,
    FileSourceCatalog,
    discover,
    list_tables,
    get_table,
    _guess_type,
)


@pytest.fixture(autouse=True)
def _stub_db_session(monkeypatch):
    """Stub db.session so DB discovery returns empty without a real DB."""
    mod = types.ModuleType("db.session")

    def get_sql_engine():
        raise RuntimeError("no db in tests")

    mod.get_sql_engine = get_sql_engine
    pkg = types.ModuleType("db")
    monkeypatch.setitem(sys.modules, "db", pkg)
    monkeypatch.setitem(sys.modules, "db.session", mod)
    yield


def _make_proj(tmp_path: Path, slug: str) -> Path:
    proj = tmp_path / slug
    proj.mkdir(parents=True, exist_ok=True)
    return proj


def test_discover_empty_project_returns_empty_catalog(tmp_path):
    cat = discover("nope_slug", knowledge_dir=tmp_path)
    assert isinstance(cat, FileSourceCatalog)
    assert cat.project_slug == "nope_slug"
    assert cat.tables == []
    assert cat.docs_processed == 0


def test_discover_finds_doc_meta_tables(tmp_path):
    proj = _make_proj(tmp_path, "demo")
    dm = proj / "doc_meta"
    dm.mkdir()
    payload = {
        "filename": "report.pptx",
        "type": "pptx",
        "tables_extracted": [
            {"name": "slide_3_table_1",
             "columns": ["region", "sales"],
             "rows": 12,
             "sql_table": "report_t1"},
            {"name": "slide_5_table_1",
             "headers": ["sku", "qty"],
             "row_count": 7},
        ],
    }
    (dm / "report_pptx.json").write_text(json.dumps(payload))

    cat = discover("demo", knowledge_dir=tmp_path)
    assert cat.docs_processed == 1
    assert len(cat.tables) == 2
    t0 = cat.tables[0]
    assert t0.doc_name == "report.pptx"
    assert t0.doc_type == "pptx"
    assert t0.full_address == "file_report_pptx.slide_3_table_1"
    assert t0.columns == ["region", "sales"]
    assert t0.row_count == 12
    assert t0.sql_table_name == "report_t1"
    t1 = cat.tables[1]
    assert t1.columns == ["sku", "qty"]
    assert t1.row_count == 7


def test_discover_finds_profile_tables(tmp_path):
    proj = _make_proj(tmp_path, "demo")
    src = proj / "source_42" / "profile"
    src.mkdir(parents=True)
    profile = {
        "region": {"count": 100, "unique": 4},
        "sales": {"count": 100, "mean": 50.0},
    }
    (src / "monthly_sales.json").write_text(json.dumps(profile))

    cat = discover("demo", knowledge_dir=tmp_path)
    assert len(cat.tables) == 1
    t = cat.tables[0]
    assert t.full_address == "src42.monthly_sales"
    assert t.doc_id == "src42"
    assert t.doc_type == "source"
    assert set(t.columns) == {"region", "sales"}
    assert t.row_count == 100
    assert t.metadata["from_profile"] is True


def test_discover_handles_corrupt_json(tmp_path):
    proj = _make_proj(tmp_path, "demo")
    dm = proj / "doc_meta"
    dm.mkdir()
    (dm / "bad.json").write_text("{not valid json")
    # Add a good one too
    (dm / "good.json").write_text(json.dumps({
        "filename": "f.pdf",
        "tables_extracted": [{"name": "t", "columns": ["a"]}],
    }))

    cat = discover("demo", knowledge_dir=tmp_path)
    # Bad file silently skipped; good one processed
    assert cat.docs_processed == 1
    assert len(cat.tables) == 1
    assert cat.tables[0].full_address == "file_good.t"


def test_list_tables_returns_dicts(tmp_path, monkeypatch):
    proj = _make_proj(tmp_path, "demo")
    dm = proj / "doc_meta"
    dm.mkdir()
    (dm / "d.json").write_text(json.dumps({
        "filename": "x.xlsx",
        "tables_extracted": [{"name": "sheet1", "columns": ["a", "b"]}],
    }))

    orig_discover = file_source.discover
    monkeypatch.setattr(
        file_source, "discover",
        lambda slug, **kw: orig_discover(slug, knowledge_dir=tmp_path),
    )
    out = list_tables("demo")
    assert isinstance(out, list)
    assert len(out) == 1
    rec = out[0]
    assert isinstance(rec, dict)
    assert rec["full_address"] == "file_d.sheet1"
    assert rec["columns"] == ["a", "b"]
    assert rec["doc_name"] == "x.xlsx"
    assert "row_count" in rec


def test_get_table_returns_none_for_unknown_address(tmp_path, monkeypatch):
    orig_discover = file_source.discover
    monkeypatch.setattr(
        file_source, "discover",
        lambda slug, **kw: orig_discover(slug, knowledge_dir=tmp_path),
    )
    assert get_table("demo", "file_nope.t") is None


def test_full_address_format(tmp_path, monkeypatch):
    proj = _make_proj(tmp_path, "demo")
    dm = proj / "doc_meta"
    dm.mkdir()
    (dm / "doc1.json").write_text(json.dumps({
        "filename": "a.pdf",
        "tables_extracted": [{"name": "tbl_a", "columns": ["x"]}],
    }))
    src = proj / "source_9" / "profile"
    src.mkdir(parents=True)
    (src / "tbl_b.json").write_text(json.dumps({"x": {"count": 1}}))

    orig_discover = file_source.discover
    monkeypatch.setattr(
        file_source, "discover",
        lambda slug, **kw: orig_discover(slug, knowledge_dir=tmp_path),
    )
    addrs = {t["full_address"] for t in list_tables("demo")}
    assert "file_doc1.tbl_a" in addrs
    assert "src9.tbl_b" in addrs
    # All file-derived addresses begin with file_ or src
    for a in addrs:
        assert a.startswith("file_") or a.startswith("src")
        assert "." in a


def test_guess_type_from_filename():
    assert _guess_type("report.PPTX") == "pptx"
    assert _guess_type("data.csv") == "csv"
    assert _guess_type("x.parquet") == "parquet"
    assert _guess_type("page.html") == "html"
    assert _guess_type("doc.docx") == "docx"
    assert _guess_type("sheet.xlsx") == "xlsx"
    assert _guess_type("nothing") == "unknown"
    assert _guess_type("") == "unknown"
