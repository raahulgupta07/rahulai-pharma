"""Unit tests for ``dash.learning.seed_loader``.

All DB engines mocked — no real PostgreSQL touched. Uses the same
``db.session`` stub pattern as ``test_self_learning.py`` so the module
can be imported on Python 3.9.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub db.session BEFORE imports
# ---------------------------------------------------------------------------
if "db.session" not in sys.modules:
    _stub = ModuleType("db.session")
    _stub.get_sql_engine = MagicMock(name="get_sql_engine_stub")
    _db_pkg = sys.modules.setdefault("db", ModuleType("db"))
    _db_pkg.session = _stub
    sys.modules["db.session"] = _stub
    _url_stub = ModuleType("db.url")
    _url_stub.db_url = MagicMock(return_value="postgresql://stub")
    _db_pkg.url = _url_stub
    sys.modules["db.url"] = _url_stub


from dash.learning import seed_loader  # noqa: E402
from dash.learning.seed_loader import (  # noqa: E402
    LoadResult,
    _map_category,
    auto_load,
    load_seeds_for_domain,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_engine(insert_returns_id: bool = True):
    """Build a mock engine whose execute(...).fetchone() returns a row
    (simulating successful insert) or None (simulating ON CONFLICT skip)."""
    result = MagicMock()
    result.fetchone.return_value = (1,) if insert_returns_id else None

    conn = MagicMock()
    conn.execute.return_value = result
    conn.commit = MagicMock()

    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False

    eng = MagicMock()
    eng.connect.return_value = cm
    return eng, conn, result


def _write_seed(seeds_dir: Path, name: str, entries: list[dict]) -> Path:
    seeds_dir.mkdir(parents=True, exist_ok=True)
    p = seeds_dir / name
    p.write_text(json.dumps(entries))
    return p


# ---------------------------------------------------------------------------
# load_seeds_for_domain
# ---------------------------------------------------------------------------

class TestLoadSeedsForDomain:
    def test_load_seeds_no_files_returns_empty(self, tmp_path):
        seeds = tmp_path / "seeds"
        seeds.mkdir()
        eng, _, _ = _mk_engine()
        out = load_seeds_for_domain(
            "proj_x", "retail",
            seeds_dir=seeds, dash_engine=eng,
        )
        assert isinstance(out, LoadResult)
        assert out.entries_inserted == 0
        assert out.entries_skipped == 0
        assert out.files_loaded == []

    def test_load_seeds_no_dir_records_error(self, tmp_path):
        seeds = tmp_path / "missing"
        eng, _, _ = _mk_engine()
        out = load_seeds_for_domain(
            "proj_x", "retail",
            seeds_dir=seeds, dash_engine=eng,
        )
        assert any("seeds dir missing" in e for e in out.errors)

    def test_load_seeds_inserts_entries(self, tmp_path):
        seeds = tmp_path / "seeds"
        _write_seed(seeds, "retail_brain_a.json", [
            {"name": "NPS", "value": "x", "category": "formula",
             "scope": "general", "confidence": 0.9},
            {"name": "AOV", "value": "y", "category": "metric",
             "scope": "retail", "confidence": 0.8},
        ])
        eng, conn, _ = _mk_engine(insert_returns_id=True)
        out = load_seeds_for_domain(
            "proj_x", "retail",
            seeds_dir=seeds, dash_engine=eng,
        )
        assert out.entries_inserted == 2
        assert out.entries_skipped == 0
        assert "retail_brain_a.json" in out.files_loaded
        # Two execute calls expected (one per entry)
        assert conn.execute.call_count >= 2
        conn.commit.assert_called()

    def test_load_seeds_skips_on_conflict(self, tmp_path):
        seeds = tmp_path / "seeds"
        _write_seed(seeds, "retail_brain_b.json", [
            {"name": "DUPE", "value": "v", "category": "glossary"},
        ])
        eng, _, _ = _mk_engine(insert_returns_id=False)
        out = load_seeds_for_domain(
            "proj_x", "retail",
            seeds_dir=seeds, dash_engine=eng,
        )
        assert out.entries_inserted == 0
        assert out.entries_skipped == 1

    def test_load_seeds_skips_invalid_json(self, tmp_path):
        seeds = tmp_path / "seeds"
        seeds.mkdir()
        # Not a JSON list -> recorded as error and skipped
        (seeds / "retail_brain_bad.json").write_text(
            json.dumps({"not": "a list"})
        )
        eng, _, _ = _mk_engine()
        out = load_seeds_for_domain(
            "proj_x", "retail",
            seeds_dir=seeds, dash_engine=eng,
        )
        assert out.entries_inserted == 0
        assert any("not a list" in e for e in out.errors)

    def test_load_seeds_skips_entries_missing_name(self, tmp_path):
        seeds = tmp_path / "seeds"
        _write_seed(seeds, "retail_brain_c.json", [
            {"value": "no name", "category": "glossary"},
            {"name": "ok", "value": "v"},
        ])
        eng, conn, _ = _mk_engine(insert_returns_id=True)
        out = load_seeds_for_domain(
            "proj_x", "retail",
            seeds_dir=seeds, dash_engine=eng,
        )
        # Only the named entry is inserted
        assert out.entries_inserted == 1


# ---------------------------------------------------------------------------
# auto_load
# ---------------------------------------------------------------------------

class TestAutoLoad:
    def test_auto_load_skips_when_no_domain_json(self, tmp_path):
        out = auto_load(
            "proj_x", 7,
            knowledge_dir=tmp_path / "k",
            seeds_dir=tmp_path / "seeds",
        )
        assert out["loaded"] is False
        assert "no domain detected" in out["reason"]

    def test_auto_load_handles_bad_domain_json(self, tmp_path):
        kdir = tmp_path / "k" / "proj_x" / "source_5"
        kdir.mkdir(parents=True)
        (kdir / "domain.json").write_text("{not valid json")
        out = auto_load(
            "proj_x", 5,
            knowledge_dir=tmp_path / "k",
            seeds_dir=tmp_path / "seeds",
        )
        assert out["loaded"] is False
        assert out["reason"].startswith("parse:")

    def test_auto_load_includes_generic_fallback(self, tmp_path):
        kdir = tmp_path / "k" / "proj_x" / "source_3"
        kdir.mkdir(parents=True)
        (kdir / "domain.json").write_text(json.dumps(
            {"primary": "retail", "secondaries": ["finance"]}
        ))
        seeds = tmp_path / "seeds"
        seeds.mkdir()  # empty -> no inserts, but exercises domain plumbing

        eng, _, _ = _mk_engine()
        out = auto_load(
            "proj_x", 3,
            knowledge_dir=tmp_path / "k",
            seeds_dir=seeds, dash_engine=eng,
        )
        assert out["loaded"] is True
        assert "retail" in out["domains"]
        assert "finance" in out["domains"]
        assert "generic" in out["domains"]
        assert out["total_inserted"] == 0

    def test_auto_load_does_not_duplicate_generic(self, tmp_path):
        kdir = tmp_path / "k" / "proj_x" / "source_1"
        kdir.mkdir(parents=True)
        (kdir / "domain.json").write_text(json.dumps(
            {"primary": "generic", "secondaries": []}
        ))
        seeds = tmp_path / "seeds"
        seeds.mkdir()
        eng, _, _ = _mk_engine()
        out = auto_load(
            "proj_x", 1,
            knowledge_dir=tmp_path / "k",
            seeds_dir=seeds, dash_engine=eng,
        )
        assert out["domains"].count("generic") == 1


# ---------------------------------------------------------------------------
# _map_category
# ---------------------------------------------------------------------------

class TestMapCategory:
    @pytest.mark.parametrize("inp,expected", [
        ("formula", "formula"),
        ("alias", "alias"),
        ("pattern", "pattern"),
        ("threshold", "threshold"),
        ("glossary", "glossary"),
        ("metric", "glossary"),
        ("negative_example", "rule"),
        ("FORMULA", "formula"),  # case-insensitive
    ])
    def test_map_category_known(self, inp, expected):
        assert _map_category(inp) == expected

    def test_map_category_handles_unknown(self):
        assert _map_category("totally_made_up") == "glossary"

    def test_map_category_handles_empty(self):
        assert _map_category("") == "glossary"
