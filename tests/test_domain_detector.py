"""Tests for dash.learning.domain_detector."""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from dash.learning import domain_detector as dd


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _setup_source(
    tmp_path: Path,
    *,
    slug: str = "proj",
    source_id: int = 1,
    tables: list | None = None,
    columns: dict | None = None,
    dimensions: dict | None = None,
    profile: dict | None = None,
) -> Path:
    """Create knowledge/{slug}/source_{id}/ tree with optional artifacts."""
    base = tmp_path / "knowledge" / slug / f"source_{source_id}"
    base.mkdir(parents=True, exist_ok=True)

    if tables is not None or columns is not None:
        catalog = {}
        if tables is not None:
            catalog["tables"] = tables
        if columns is not None:
            catalog["columns"] = columns
        (base / "catalog.json").write_text(json.dumps(catalog))

    if dimensions is not None:
        dim_dir = base / "dimensions"
        dim_dir.mkdir(exist_ok=True)
        (dim_dir / "values.json").write_text(json.dumps(dimensions))

    if profile is not None:
        prof_dir = base / "profile"
        prof_dir.mkdir(exist_ok=True)
        (prof_dir / "stats.json").write_text(json.dumps(profile))

    return tmp_path / "knowledge"


# ────────────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────────────

def test_detect_retail_from_table_names(tmp_path):
    kd = _setup_source(
        tmp_path,
        tables=["orders", "customers", "products", "stores", "skus"],
    )
    res = dd.detect("proj", 1, knowledge_dir=kd)
    assert res.primary == "retail"
    assert res.confidence > 0


def test_detect_finance_from_columns(tmp_path):
    kd = _setup_source(
        tmp_path,
        columns={
            "tbl": ["debit", "credit", "gl_account", "fiscal_period",
                    "fiscal_year", "cost_center", "ebitda"],
        },
    )
    res = dd.detect("proj", 1, knowledge_dir=kd)
    assert res.primary == "finance"


def test_detect_saas_from_values(tmp_path):
    kd = _setup_source(
        tmp_path,
        columns={"metrics": ["mrr", "arr", "nrr", "churn_date", "trial_start",
                              "plan_id", "tier", "ltv", "cac"]},
    )
    res = dd.detect("proj", 1, knowledge_dir=kd)
    assert res.primary == "saas"


def test_multi_domain_retail_finance(tmp_path):
    kd = _setup_source(
        tmp_path,
        tables=["orders", "customers", "products", "stores", "skus",
                "inventory", "receipts", "baskets", "promotions", "loyalty",
                "store_visits", "footfall",
                "ledger", "journal", "invoices", "ap", "ar",
                "general_ledger", "trial_balance", "fiscal_period",
                "expenses", "revenues", "budget"],
        columns={"tbl": ["sku", "upc", "store_id", "basket_id", "promo_id",
                          "qty", "units_sold",
                          "debit", "credit", "gl_account", "fiscal_year",
                          "ebitda", "opex", "capex", "cost_center"]},
    )
    res = dd.detect("proj", 1, knowledge_dir=kd, multi_domain_threshold=0.40)
    detected = {res.primary, *res.secondaries}
    assert "retail" in detected
    assert "finance" in detected


def test_unknown_returns_generic(tmp_path):
    # Use long nonsense tokens that won't substring-match short signatures
    kd = _setup_source(tmp_path, tables=["zzqxwvut", "qqxytplmn"])
    res = dd.detect("proj", 1, knowledge_dir=kd)
    # Either generic or very low confidence
    assert res.primary == "generic" or res.confidence < 0.2


def test_unknown_empty_returns_generic(tmp_path):
    base = tmp_path / "knowledge" / "proj" / "source_1"
    base.mkdir(parents=True)
    res = dd.detect("proj", 1, knowledge_dir=tmp_path / "knowledge")
    assert res.primary == "generic"
    assert res.confidence == 0.0


def test_load_returns_none_when_missing(tmp_path):
    assert dd.load("nope", 99, knowledge_dir=tmp_path / "knowledge") is None


def test_persist_writes_json(tmp_path):
    kd = _setup_source(tmp_path, tables=["orders", "customers", "products"])
    dd.detect("proj", 1, knowledge_dir=kd)
    out = kd / "proj" / "source_1" / "domain.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert "primary" in payload
    assert "secondaries" in payload
    assert "confidence" in payload
    assert "all_scores" in payload


def test_load_round_trip(tmp_path):
    kd = _setup_source(tmp_path, tables=["orders", "customers", "products"])
    dd.detect("proj", 1, knowledge_dir=kd)
    loaded = dd.load("proj", 1, knowledge_dir=kd)
    assert loaded is not None
    assert loaded["primary"] == "retail"


def test_all_domains_listed():
    domains = dd.all_domains()
    assert len(domains) == 15  # 14 industry + generic
    assert "retail" in domains
    assert "generic" in domains


def test_dimensions_values_contribute(tmp_path):
    kd = _setup_source(
        tmp_path,
        dimensions={
            "status_col": [["filed", 100], ["settled", 50],
                            ["litigation", 30], ["regulatory", 20]],
        },
        columns={"t": ["matter_id", "case_id", "attorney_id", "billable",
                        "filing_date", "court"]},
    )
    res = dd.detect("proj", 1, knowledge_dir=kd)
    assert res.primary == "legal"


def test_profile_columns_used(tmp_path):
    kd = _setup_source(
        tmp_path,
        profile={"meter_id": {}, "kwh": {}, "voltage": {},
                  "tariff_code": {}, "peak_demand": {}, "load_factor": {}},
    )
    res = dd.detect("proj", 1, knowledge_dir=kd)
    assert res.primary == "energy"


def test_dimensions_dict_form(tmp_path):
    kd = _setup_source(
        tmp_path,
        dimensions={
            "shift_col": [{"value": "first_shift"}, {"value": "second_shift"}],
        },
        columns={"t": ["machine_id", "oee", "mtbf", "scrap_rate",
                        "cycle_time", "operator_id"]},
    )
    res = dd.detect("proj", 1, knowledge_dir=kd)
    assert res.primary == "manufacturing"


def test_catalog_dict_tables(tmp_path):
    kd = _setup_source(
        tmp_path,
        tables=[{"name": "patients"}, {"name": "claims"},
                {"table_name": "encounters"}, {"name": "diagnoses"}],
    )
    res = dd.detect("proj", 1, knowledge_dir=kd)
    assert res.primary == "healthcare"


def test_corrupt_catalog_handled(tmp_path):
    base = tmp_path / "knowledge" / "proj" / "source_1"
    base.mkdir(parents=True)
    (base / "catalog.json").write_text("{ not valid json")
    res = dd.detect("proj", 1, knowledge_dir=tmp_path / "knowledge")
    assert res.primary == "generic"


def test_score_normalized_confidence(tmp_path):
    kd = _setup_source(
        tmp_path,
        tables=["orders", "customers", "products", "stores", "skus",
                "inventory", "transactions", "receipts", "baskets",
                "promotions", "loyalty"],
        columns={"t": ["sku", "upc", "store_id", "basket_id",
                        "promo_id", "qty", "revenue"]},
    )
    res = dd.detect("proj", 1, knowledge_dir=kd)
    assert res.primary == "retail"
    assert 0.0 < res.confidence <= 1.0


# ────────────────────────────────────────────────────────────────────────────
# Per-domain parameterized: each domain should be detected from its own sig
# ────────────────────────────────────────────────────────────────────────────

DOMAIN_FIXTURES = [
    ("retail", ["orders", "customers", "skus", "stores", "baskets"]),
    ("finance", ["ledger", "journal", "invoices", "ap", "ar", "general_ledger"]),
    ("healthcare", ["patients", "claims", "encounters", "diagnoses",
                     "procedures", "providers"]),
    ("hr", ["employees", "payroll", "benefits", "performance",
             "compensation", "departments"]),
    ("supply_chain", ["warehouses", "shipments", "lots", "purchase_orders",
                       "carriers", "tracking"]),
    ("marketing", ["campaigns", "leads", "conversions", "ad_spend",
                    "creatives", "audiences"]),
    ("saas", ["subscriptions", "trials", "feature_flags", "billing",
               "usage", "features"]),
    ("insurance", ["policies", "premiums", "adjusters", "underwriting",
                    "reinsurance", "reserves"]),
    ("manufacturing", ["machines", "production_orders", "downtime",
                        "scrap", "work_centers", "operators"]),
    ("telecom", ["subscribers", "calls", "data_usage", "sms", "towers", "cells"]),
    ("energy", ["meters", "tariffs", "outages", "transformers",
                 "substations", "generation"]),
    ("education", ["students", "courses", "enrollments", "grades",
                    "instructors", "majors"]),
    ("legal", ["matters", "cases", "filings", "depositions",
                "billable_hours", "attorneys"]),
    ("real_estate", ["properties", "listings", "leases", "tenants",
                      "units", "buildings", "appraisals"]),
]


@pytest.mark.parametrize("domain,tables", DOMAIN_FIXTURES)
def test_each_domain_detected(tmp_path, domain, tables):
    kd = _setup_source(tmp_path, tables=tables, slug=f"p_{domain}")
    res = dd.detect(f"p_{domain}", 1, knowledge_dir=kd)
    assert res.primary == domain, (
        f"expected {domain}, got {res.primary}; scores={[(s.domain, s.score) for s in res.all_scores]}"
    )
