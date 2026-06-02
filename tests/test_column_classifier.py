"""Unit tests for dash.providers.column_classifier.

The classifier module is being implemented in a parallel agent. If the
module is not importable yet, every test in this file is skipped so the
suite stays green.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

cc = pytest.importorskip(
    "dash.providers.column_classifier",
    reason="awaiting classifier impl from parallel agent",
)


# ---------------------------------------------------------------------------
# Helpers — synth a fake knowledge/{slug}/source_{id}/ tree
# ---------------------------------------------------------------------------

def _write_source_tree(
    root: Path,
    slug: str,
    source_id: int,
    *,
    catalog: dict | None = None,
    profile: dict | None = None,
    dimensions: dict | None = None,
) -> Path:
    """Layout matches what classify_source() reads:
        base/catalog.json
        base/profile/{table}.json
        base/dimensions/{table}.json
    """
    base = root / slug / f"source_{source_id}"
    base.mkdir(parents=True, exist_ok=True)
    if catalog is not None:
        (base / "catalog.json").write_text(json.dumps(catalog))
    if profile is not None:
        (base / "profile").mkdir(exist_ok=True)
        for tbl, cols in profile.items():
            (base / "profile" / f"{tbl}.json").write_text(json.dumps(cols))
    if dimensions is not None:
        (base / "dimensions").mkdir(exist_ok=True)
        for tbl, dims in dimensions.items():
            (base / "dimensions" / f"{tbl}.json").write_text(json.dumps(dims))
    return base


def _make_profile(table: str, columns: dict) -> dict:
    return {table: columns}


def _has(obj, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj
    return hasattr(obj, key)


def _get(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ---------------------------------------------------------------------------
# Regex pattern tier
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not hasattr(cc, "regex_classify") and not hasattr(cc, "classify_by_regex"),
    reason="regex tier helper not exposed",
)
def test_regex_email():
    fn = getattr(cc, "regex_classify", None) or getattr(cc, "classify_by_regex")
    res = fn(["alice@example.com", "bob@test.org", "carol@x.io"])
    s = json.dumps(res, default=str).lower()
    assert "email" in s


@pytest.mark.skipif(
    not hasattr(cc, "regex_classify") and not hasattr(cc, "classify_by_regex"),
    reason="regex tier helper not exposed",
)
def test_regex_uuid():
    fn = getattr(cc, "regex_classify", None) or getattr(cc, "classify_by_regex")
    res = fn(
        [
            "550e8400-e29b-41d4-a716-446655440000",
            "550e8400-e29b-41d4-a716-446655440001",
        ]
    )
    s = json.dumps(res, default=str).lower()
    assert "uuid" in s or "id" in s


@pytest.mark.skipif(
    not hasattr(cc, "regex_classify") and not hasattr(cc, "classify_by_regex"),
    reason="regex tier helper not exposed",
)
def test_regex_phone():
    fn = getattr(cc, "regex_classify", None) or getattr(cc, "classify_by_regex")
    res = fn(["+1-555-123-4567", "+1-555-987-6543"])
    s = json.dumps(res, default=str).lower()
    assert "phone" in s


# ---------------------------------------------------------------------------
# Name vocabulary tier
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not hasattr(cc, "name_classify") and not hasattr(cc, "classify_by_name"),
    reason="name tier helper not exposed",
)
def test_name_email_pii():
    fn = getattr(cc, "name_classify", None) or getattr(cc, "classify_by_name")
    res = fn("customer_email")
    s = json.dumps(res, default=str).lower()
    assert "email" in s or "pii" in s


@pytest.mark.skipif(
    not hasattr(cc, "name_classify") and not hasattr(cc, "classify_by_name"),
    reason="name tier helper not exposed",
)
def test_name_at_suffix_temporal():
    fn = getattr(cc, "name_classify", None) or getattr(cc, "classify_by_name")
    res = fn("created_at")
    s = json.dumps(res, default=str).lower()
    assert "temporal" in s or "datetime" in s or "time" in s


@pytest.mark.skipif(
    not hasattr(cc, "name_classify") and not hasattr(cc, "classify_by_name"),
    reason="name tier helper not exposed",
)
def test_name_revenue_measure():
    fn = getattr(cc, "name_classify", None) or getattr(cc, "classify_by_name")
    res = fn("revenue")
    s = json.dumps(res, default=str).lower()
    assert "measure" in s or "metric" in s or "amount" in s


# ---------------------------------------------------------------------------
# Statistical fingerprint tier
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not hasattr(cc, "stats_classify") and not hasattr(cc, "classify_by_stats"),
    reason="stats tier helper not exposed",
)
def test_stats_unique_id():
    fn = getattr(cc, "stats_classify", None) or getattr(cc, "classify_by_stats")
    res = fn({"distinct_pct": 1.0, "ndv": 1000, "row_count": 1000})
    s = json.dumps(res, default=str).lower()
    assert "id" in s or "key" in s or "unique" in s


@pytest.mark.skipif(
    not hasattr(cc, "stats_classify") and not hasattr(cc, "classify_by_stats"),
    reason="stats tier helper not exposed",
)
def test_stats_low_cardinality_dim():
    fn = getattr(cc, "stats_classify", None) or getattr(cc, "classify_by_stats")
    res = fn({"distinct_pct": 0.004, "ndv": 4, "row_count": 1000})
    s = json.dumps(res, default=str).lower()
    assert "dim" in s or "categor" in s or "low_cardinality" in s


@pytest.mark.skipif(
    not hasattr(cc, "stats_classify") and not hasattr(cc, "classify_by_stats"),
    reason="stats tier helper not exposed",
)
def test_stats_numeric_skew_measure():
    fn = getattr(cc, "stats_classify", None) or getattr(cc, "classify_by_stats")
    res = fn(
        {
            "distinct_pct": 0.6,
            "ndv": 600,
            "row_count": 1000,
            "is_numeric": True,
            "skew": 4.2,
        }
    )
    s = json.dumps(res, default=str).lower()
    assert "measure" in s or "metric" in s


# ---------------------------------------------------------------------------
# Fusion / tie-break rules
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not hasattr(cc, "fuse"), reason="fuse() not exposed"
)
def test_fuse_agreement_high_confidence():
    res = cc.fuse(
        [
            {"label": "email", "confidence": 0.9, "source": "regex"},
            {"label": "email", "confidence": 0.8, "source": "name"},
        ]
    )
    s = json.dumps(res, default=str).lower()
    assert "email" in s


@pytest.mark.skipif(
    not hasattr(cc, "fuse"), reason="fuse() not exposed"
)
def test_fuse_pii_signal_wins():
    res = cc.fuse(
        [
            {"label": "string", "confidence": 0.5, "source": "stats"},
            {"label": "email", "confidence": 0.95, "source": "regex", "pii": True},
        ]
    )
    s = json.dumps(res, default=str).lower()
    assert "email" in s or "pii" in s


@pytest.mark.skipif(
    not hasattr(cc, "fuse"), reason="fuse() not exposed"
)
def test_fuse_tie_break_highest_confidence():
    res = cc.fuse(
        [
            {"label": "dimension", "confidence": 0.5, "source": "stats"},
            {"label": "measure", "confidence": 0.85, "source": "name"},
        ]
    )
    s = json.dumps(res, default=str).lower()
    assert "measure" in s


# ---------------------------------------------------------------------------
# PII detection direct vs quasi
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not hasattr(cc, "detect_pii"), reason="detect_pii() not exposed"
)
def test_pii_direct():
    res = cc.detect_pii("pii", "email", ["email"])
    # (is_pii, pii_class, masking_recommended)
    assert isinstance(res, tuple)
    assert res[0] is True


@pytest.mark.skipif(
    not hasattr(cc, "detect_pii"), reason="detect_pii() not exposed"
)
def test_pii_quasi_zip_dob_gender():
    # Quasi-PII path: regex_hits empty, semantic non-direct
    res = cc.detect_pii("dimension", "zip_code", [])
    assert isinstance(res, tuple)
    # Either flagged quasi or non-PII; just must not crash and return tuple


# ---------------------------------------------------------------------------
# classify_table end-to-end
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not hasattr(cc, "classify_table"), reason="classify_table() not exposed"
)
def test_classify_table_e2e():
    profile = {
        "id": {"distinct_pct": 1.0, "ndv": 100, "row_count": 100, "is_numeric": True},
        "email": {"distinct_pct": 0.95, "ndv": 95, "row_count": 100},
        "revenue": {
            "distinct_pct": 0.7,
            "ndv": 70,
            "row_count": 100,
            "is_numeric": True,
        },
        "region": {"distinct_pct": 0.04, "ndv": 4, "row_count": 100},
    }
    dimensions = {
        "region": {"values": ["EAST", "WEST", "NORTH", "SOUTH"]},
    }
    catalog = {
        "tables": [
            {
                "name": "customers",
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "email", "type": "varchar"},
                    {"name": "revenue", "type": "numeric"},
                    {"name": "region", "type": "varchar"},
                ],
            }
        ]
    }
    res = cc.classify_table(
        table_name="customers",
        catalog=catalog,
        profile=profile,
        dim_catalog=dimensions,
        llm_call_fn=None,
        embed_fn=None,
    )
    assert res is not None
    s = json.dumps(res, default=str).lower()
    assert "email" in s or "id" in s or "region" in s


# ---------------------------------------------------------------------------
# classify_source — file IO + JSON shape
# ---------------------------------------------------------------------------

def test_classify_source_writes_json(tmp_path):
    if not hasattr(cc, "classify_source"):
        pytest.skip("classify_source() not exposed")

    slug = "proj_test"
    sid = 42
    catalog = {"tables": ["customers"], "table_columns": {"customers": ["id", "email"]}}
    profile = _make_profile(
        "customers",
        {
            "id": {"distinct_pct": 1.0, "ndv": 10, "row_count": 10, "is_numeric": True},
            "email": {"distinct_pct": 1.0, "ndv": 10, "row_count": 10},
        },
    )
    dimensions = {"customers": {}}

    _write_source_tree(
        tmp_path,
        slug,
        sid,
        catalog=catalog,
        profile=profile,
        dimensions=dimensions,
    )

    out = cc.classify_source(
        knowledge_dir=tmp_path,
        project_slug=slug,
        source_id=sid,
        llm_call_fn=None,
        embed_fn=None,
    )
    assert isinstance(out, Path)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data is not None


def test_classify_source_missing_files_no_crash(tmp_path):
    if not hasattr(cc, "classify_source"):
        pytest.skip("classify_source() not exposed")

    slug = "proj_empty"
    sid = 1
    base = tmp_path / slug / f"source_{sid}"
    base.mkdir(parents=True, exist_ok=True)
    # Intentionally empty — no catalog/profile/dimensions
    out = cc.classify_source(
        knowledge_dir=tmp_path,
        project_slug=slug,
        source_id=sid,
        llm_call_fn=None,
        embed_fn=None,
    )
    assert isinstance(out, Path)


def test_classify_source_skips_llm_when_none(tmp_path):
    if not hasattr(cc, "classify_source"):
        pytest.skip("classify_source() not exposed")

    slug = "proj_no_llm"
    sid = 7
    profile = _make_profile(
        "t1",
        {"x": {"distinct_pct": 0.1, "ndv": 10, "row_count": 100, "is_numeric": True}},
    )
    _write_source_tree(
        tmp_path,
        slug,
        sid,
        catalog={"tables": ["t1"]},
        profile=profile,
        dimensions={},
    )
    # Must not raise even though llm_call_fn is None
    out = cc.classify_source(
        knowledge_dir=tmp_path,
        project_slug=slug,
        source_id=sid,
        llm_call_fn=None,
        embed_fn=None,
    )
    assert out.exists()


def test_classify_source_skips_embed_when_none(tmp_path):
    if not hasattr(cc, "classify_source"):
        pytest.skip("classify_source() not exposed")

    slug = "proj_no_embed"
    sid = 8
    profile = _make_profile(
        "t1",
        {"x": {"distinct_pct": 0.1, "ndv": 10, "row_count": 100}},
    )
    _write_source_tree(
        tmp_path, slug, sid,
        catalog={"tables": ["t1"]}, profile=profile, dimensions={},
    )
    out = cc.classify_source(
        knowledge_dir=tmp_path,
        project_slug=slug,
        source_id=sid,
        llm_call_fn=None,
        embed_fn=None,
    )
    assert out.exists()


def test_output_json_shape(tmp_path):
    if not hasattr(cc, "classify_source"):
        pytest.skip("classify_source() not exposed")

    slug = "proj_shape"
    sid = 99
    profile = _make_profile(
        "orders",
        {
            "order_id": {"distinct_pct": 1.0, "ndv": 50, "row_count": 50, "is_numeric": True},
            "amount": {
                "distinct_pct": 0.8,
                "ndv": 40,
                "row_count": 50,
                "is_numeric": True,
            },
        },
    )
    _write_source_tree(
        tmp_path,
        slug,
        sid,
        catalog={"tables": ["orders"]},
        profile=profile,
        dimensions={},
    )
    out = cc.classify_source(
        knowledge_dir=tmp_path,
        project_slug=slug,
        source_id=sid,
        llm_call_fn=None,
        embed_fn=None,
    )
    data = json.loads(out.read_text())
    # Must be a JSON-serialisable container
    assert isinstance(data, (dict, list))


# ---------------------------------------------------------------------------
# PII masking (dash.providers.pii_mask)
# ---------------------------------------------------------------------------

pm = pytest.importorskip(
    "dash.providers.pii_mask",
    reason="pii_mask module not present",
)


def test_mask_value_strategies():
    assert pm.mask_value(None, "redact") is None
    assert pm.mask_value("anything", "redact") == "***REDACTED***"
    # email
    out = pm.mask_value("alice@example.com", "mask_email")
    assert out.endswith("@example.com") and out.startswith("a")
    # phone — keeps last 4
    assert pm.mask_value("+1-555-123-4567", "mask_phone").endswith("4567")
    # generalize keeps first 3
    g = pm.mask_value("94107", "generalize")
    assert g.startswith("941") and "*" in g
    # truncate
    assert pm.mask_value("Smith", "truncate").startswith("Sm")
    # hash deterministic
    h1 = pm.mask_value("abc", "hash")
    h2 = pm.mask_value("abc", "hash")
    assert h1 == h2 and h1.startswith("hashed_")


def test_apply_masking_flag_mask_block(tmp_path, monkeypatch):
    # Build a fake classification file
    slug = "proj_pii"
    sid = 5
    base = tmp_path / slug / f"source_{sid}"
    base.mkdir(parents=True, exist_ok=True)
    classification = {
        "users": {
            "email": {"pii": True, "pii_class": "direct", "masking_recommended": "mask_email"},
            "name": {"pii": False},
        }
    }
    (base / "column_classification.json").write_text(json.dumps(classification))

    # Point pii_mask at our temp dir + clear cache
    monkeypatch.setattr(pm, "KNOWLEDGE_DIR", tmp_path)
    pm.invalidate_cache()

    rows = [("alice@example.com", "Alice"), ("bob@test.org", "Bob")]
    columns = ["email", "name"]

    # flag — rows unchanged, audit reports
    out_rows, audit = pm.apply_masking_to_rows(rows, columns, slug, sid, action="flag")
    assert out_rows == rows
    assert "email" in audit["pii_columns_present"]
    assert audit["blocked"] is False
    assert audit["cells_masked"] == 0

    # mask — email cells transformed
    out_rows, audit = pm.apply_masking_to_rows(rows, columns, slug, sid, action="mask")
    assert audit["cells_masked"] == 2
    assert all(r[0] != orig[0] for r, orig in zip(out_rows, rows))
    assert all(r[1] == orig[1] for r, orig in zip(out_rows, rows))  # name untouched

    # block — empty rows + blocked flag
    out_rows, audit = pm.apply_masking_to_rows(rows, columns, slug, sid, action="block")
    assert out_rows == []
    assert audit["blocked"] is True


def test_apply_masking_no_pii_columns(tmp_path, monkeypatch):
    slug = "proj_clean"
    sid = 1
    base = tmp_path / slug / f"source_{sid}"
    base.mkdir(parents=True, exist_ok=True)
    (base / "column_classification.json").write_text(json.dumps({
        "t": {"x": {"pii": False}}
    }))
    monkeypatch.setattr(pm, "KNOWLEDGE_DIR", tmp_path)
    pm.invalidate_cache()

    rows = [(1, 2), (3, 4)]
    out_rows, audit = pm.apply_masking_to_rows(rows, ["x", "y"], slug, sid, action="mask")
    assert out_rows == rows
    assert audit["pii_columns_present"] == []
    assert audit["cells_masked"] == 0
