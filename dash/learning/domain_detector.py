"""Domain detector — classifies project domain from data shape.

Reads catalog.json + dimensions.json + profile.json, scores each candidate
domain by name + value overlap with signature library, returns top 1-3
domains. Multi-domain projects (e.g. retail+finance) get all that score
within 10% of the leader.

Output: knowledge/{slug}/source_{id}/domain.json
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path("knowledge")


# ────────────────────────────────────────────────────────────────────────────
# Domain signatures — keywords that strongly indicate a domain
# ────────────────────────────────────────────────────────────────────────────

DOMAIN_SIGNATURES: dict[str, dict] = {
    "retail": {
        "tables": ["orders", "customers", "products", "stores", "skus",
                    "inventory", "transactions", "receipts", "baskets",
                    "promotions", "loyalty", "store_visits", "footfall"],
        "columns": ["sku", "upc", "ean", "gtin", "store_id", "store_format",
                    "basket_id", "promo_id", "category_id", "department_id",
                    "qty", "units_sold", "revenue", "discount_pct"],
        "values": ["online", "in_store", "click_collect", "pickup",
                   "promo", "regular", "full_price", "markdown"],
        "boost": 1.0,
    },
    "finance": {
        "tables": ["transactions", "accounts", "ledger", "journal", "budget",
                    "invoices", "payments", "expenses", "revenues", "ap", "ar",
                    "general_ledger", "trial_balance", "fiscal_period"],
        "columns": ["debit", "credit", "gl_account", "fiscal_period",
                    "fiscal_year", "fiscal_quarter", "cost_center",
                    "department_code", "ifrs", "gaap", "currency_code",
                    "exchange_rate", "ebitda", "opex", "capex"],
        "values": ["debit", "credit", "open", "closed", "void", "posted"],
        "boost": 1.0,
    },
    "healthcare": {
        "tables": ["patients", "claims", "encounters", "diagnoses",
                    "procedures", "medications", "providers", "facilities",
                    "appointments", "labs", "vitals", "admissions"],
        "columns": ["patient_id", "mrn", "icd10", "icd9", "cpt", "hcpcs",
                    "npi", "drg", "los", "admit_date", "discharge_date",
                    "ndc", "rx_norm", "snomed"],
        "values": ["inpatient", "outpatient", "emergency", "elective",
                   "approved", "denied", "pending", "paid"],
        "boost": 1.0,
    },
    "hr": {
        "tables": ["employees", "payroll", "benefits", "performance",
                    "compensation", "departments", "positions", "candidates",
                    "applications", "training", "reviews"],
        "columns": ["employee_id", "manager_id", "hire_date", "termination_date",
                    "salary", "bonus", "fte", "comp_ratio", "performance_band",
                    "department", "title", "level", "tenure_years"],
        "values": ["full_time", "part_time", "contractor", "intern",
                   "active", "terminated", "leave", "exempt", "non_exempt"],
        "boost": 1.0,
    },
    "supply_chain": {
        "tables": ["warehouses", "shipments", "inventory", "lots",
                    "purchase_orders", "carriers", "tracking", "bom",
                    "manufacturing_orders", "deliveries"],
        "columns": ["warehouse_id", "sku", "lot_number", "shipment_id",
                    "tracking_number", "carrier", "eta", "etd",
                    "lead_time_days", "fill_rate", "stock_level",
                    "safety_stock", "reorder_point"],
        "values": ["in_transit", "delivered", "delayed", "received",
                   "shipped", "ftl", "ltl", "ocean", "air"],
        "boost": 1.0,
    },
    "marketing": {
        "tables": ["campaigns", "channels", "leads", "conversions",
                    "ad_spend", "creatives", "audiences", "events",
                    "page_views", "sessions"],
        "columns": ["campaign_id", "channel", "source", "medium",
                    "impressions", "clicks", "ctr", "cpm", "cpc", "cpa",
                    "roas", "cac", "ltv", "conversion_rate", "bounce_rate"],
        "values": ["organic", "paid", "social", "email", "display",
                   "search", "referral", "direct"],
        "boost": 1.0,
    },
    "saas": {
        "tables": ["subscriptions", "users", "accounts", "features",
                    "usage", "billing", "invoices", "trials",
                    "feature_flags", "events", "sessions"],
        "columns": ["mrr", "arr", "nrr", "grr", "churn_date", "trial_start",
                    "trial_end", "plan_id", "tier", "seat_count", "ltv",
                    "cac", "acv", "dau", "mau", "wau"],
        "values": ["trial", "active", "churned", "paused", "free",
                   "starter", "pro", "enterprise"],
        "boost": 1.0,
    },
    "insurance": {
        "tables": ["policies", "claims", "premiums", "adjusters",
                    "underwriting", "reinsurance", "losses", "reserves"],
        "columns": ["policy_number", "claim_number", "premium",
                    "deductible", "coverage_limit", "loss_ratio",
                    "ibnr", "fnol", "underwriter", "adjuster_id",
                    "policy_holder", "perils"],
        "values": ["filed", "investigating", "approved", "denied", "settled",
                   "auto", "home", "life", "health", "casualty"],
        "boost": 1.0,
    },
    "manufacturing": {
        "tables": ["machines", "production_orders", "shifts",
                    "downtime", "scrap", "yield", "maintenance",
                    "work_centers", "operators"],
        "columns": ["machine_id", "shift", "oee", "mtbf", "mttr",
                    "scrap_rate", "yield_pct", "cycle_time", "takt_time",
                    "throughput", "downtime_minutes", "operator_id"],
        "values": ["running", "idle", "breakdown", "maintenance",
                   "first_shift", "second_shift", "third_shift"],
        "boost": 1.0,
    },
    "telecom": {
        "tables": ["subscribers", "calls", "data_usage", "sms",
                    "towers", "cells", "billing", "rate_plans"],
        "columns": ["msisdn", "imsi", "imei", "arpu", "mou", "data_mb",
                    "sms_count", "tower_id", "cell_id", "signal_strength"],
        "values": ["prepaid", "postpaid", "active", "suspended",
                   "voice", "data", "sms", "roaming"],
        "boost": 1.0,
    },
    "energy": {
        "tables": ["meters", "usage", "tariffs", "outages",
                    "transformers", "substations", "generation"],
        "columns": ["meter_id", "kwh", "kw", "voltage", "frequency",
                    "peak_demand", "load_factor", "capacity_factor",
                    "outage_minutes", "tariff_code"],
        "values": ["residential", "commercial", "industrial",
                   "solar", "wind", "gas", "nuclear", "hydro"],
        "boost": 1.0,
    },
    "education": {
        "tables": ["students", "courses", "enrollments", "grades",
                    "instructors", "terms", "departments", "majors"],
        "columns": ["student_id", "instructor_id", "course_id",
                    "gpa", "enrollment_date", "graduation_date",
                    "credit_hours", "term_code", "major_code"],
        "values": ["freshman", "sophomore", "junior", "senior",
                   "graduate", "fall", "spring", "summer"],
        "boost": 1.0,
    },
    "legal": {
        "tables": ["matters", "cases", "filings", "contracts",
                    "depositions", "billable_hours", "attorneys",
                    "clients", "documents", "court_dates"],
        "columns": ["matter_id", "case_id", "attorney_id", "billable",
                    "hours_logged", "rate", "court", "filing_date",
                    "deposition_date", "client_id"],
        "values": ["open", "closed", "settled", "tried", "appealed",
                   "litigation", "transactional", "regulatory"],
        "boost": 1.0,
    },
    "real_estate": {
        "tables": ["properties", "listings", "leases", "tenants",
                    "transactions", "units", "buildings", "appraisals"],
        "columns": ["property_id", "listing_id", "address", "zip_code",
                    "sqft", "noi", "cap_rate", "occupancy_rate",
                    "rent_per_sqft", "lease_start", "lease_end"],
        "values": ["residential", "commercial", "retail", "industrial",
                   "office", "vacant", "occupied", "for_sale", "for_rent"],
        "boost": 1.0,
    },
    "generic": {
        # Always-applicable cross-domain signatures
        "tables": ["dim_date", "dim_time", "fact_table", "metrics",
                    "kpi_dashboard"],
        "columns": ["created_at", "updated_at", "id", "uuid",
                    "name", "description", "status", "category"],
        "values": ["active", "inactive", "yes", "no", "pending"],
        "boost": 0.3,   # weight low — only kicks in when nothing else fits
    },
}


# ────────────────────────────────────────────────────────────────────────────
# Data classes
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class DomainScore:
    domain: str
    score: float
    matched_tables: list[str] = field(default_factory=list)
    matched_columns: list[str] = field(default_factory=list)
    matched_values: list[str] = field(default_factory=list)


@dataclass
class DomainDetection:
    project_slug: str
    source_id: int
    primary: str
    secondaries: list[str] = field(default_factory=list)
    confidence: float = 0.0
    all_scores: list[DomainScore] = field(default_factory=list)


# ────────────────────────────────────────────────────────────────────────────
# Detection
# ────────────────────────────────────────────────────────────────────────────

def detect(
    project_slug: str,
    source_id: int,
    *,
    knowledge_dir: Path = KNOWLEDGE_DIR,
    multi_domain_threshold: float = 0.10,
) -> DomainDetection:
    """Score 14 domains by signature overlap. Top scorer = primary,
    others within `multi_domain_threshold` of leader = secondaries.

    Persists to knowledge/{slug}/source_{id}/domain.json.
    """
    base = knowledge_dir / project_slug / f"source_{source_id}"

    # Gather data signals
    table_names = _gather_tables(base)
    column_names = _gather_columns(base)
    sample_values = _gather_values(base)

    # Score each domain
    scores: list[DomainScore] = []
    for domain, sig in DOMAIN_SIGNATURES.items():
        s = _score_domain(domain, sig, table_names, column_names, sample_values)
        scores.append(s)

    scores.sort(key=lambda x: x.score, reverse=True)

    if not scores or scores[0].score == 0:
        primary = "generic"
        secondaries: list[str] = []
        confidence = 0.0
    else:
        primary = scores[0].domain
        leader_score = scores[0].score
        confidence = min(1.0, leader_score / 10.0)  # normalize
        secondaries = [
            s.domain for s in scores[1:4]
            if s.score >= leader_score * (1.0 - multi_domain_threshold)
            and s.score > 0
            and s.domain != "generic"   # don't elevate generic to secondary
        ]

    detection = DomainDetection(
        project_slug=project_slug,
        source_id=source_id,
        primary=primary,
        secondaries=secondaries,
        confidence=confidence,
        all_scores=scores[:6],
    )

    _persist(detection, base)
    return detection


def _gather_tables(base: Path) -> set[str]:
    out: set[str] = set()
    catalog = base / "catalog.json"
    if catalog.exists():
        try:
            data = json.loads(catalog.read_text())
            tables = data.get("tables", [])
            for t in tables:
                if isinstance(t, str):
                    out.add(t.lower())
                elif isinstance(t, dict):
                    name = t.get("name") or t.get("table_name") or ""
                    if name:
                        out.add(name.lower())
        except Exception as e:
            logger.debug(f"catalog parse: {e}")
    return out


def _gather_columns(base: Path) -> set[str]:
    out: set[str] = set()
    catalog = base / "catalog.json"
    if catalog.exists():
        try:
            data = json.loads(catalog.read_text())
            cols = data.get("columns", {})
            if isinstance(cols, dict):
                for tbl, lst in cols.items():
                    if isinstance(lst, list):
                        for c in lst:
                            if isinstance(c, str):
                                out.add(c.lower())
                            elif isinstance(c, dict):
                                name = c.get("name") or c.get("column_name") or ""
                                if name:
                                    out.add(name.lower())
        except Exception:
            pass

    # Also scan profile/ directory
    profile_dir = base / "profile"
    if profile_dir.exists():
        for p in profile_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                if isinstance(data, dict):
                    for col in data.keys():
                        out.add(col.lower())
            except Exception:
                pass
    return out


def _gather_values(base: Path) -> set[str]:
    out: set[str] = set()
    dim_dir = base / "dimensions"
    if dim_dir.exists():
        for p in dim_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                if isinstance(data, dict):
                    for col, freq_list in data.items():
                        if not isinstance(freq_list, list):
                            continue
                        for entry in freq_list[:50]:  # cap per col
                            if isinstance(entry, list) and entry:
                                v = entry[0]
                            elif isinstance(entry, dict):
                                v = entry.get("value")
                            else:
                                v = entry
                            if isinstance(v, str):
                                out.add(v.lower())
            except Exception:
                pass
    return out


def _score_domain(
    domain: str,
    sig: dict,
    tables: set[str],
    columns: set[str],
    values: set[str],
) -> DomainScore:
    score = 0.0
    matched_t: list[str] = []
    matched_c: list[str] = []
    matched_v: list[str] = []

    # Table matches (heaviest weight)
    for t in sig.get("tables", []):
        for actual in tables:
            if t in actual or actual in t:
                score += 3.0
                matched_t.append(t)
                break

    # Column matches (medium weight)
    for c in sig.get("columns", []):
        for actual in columns:
            if c == actual or c in actual or actual in c:
                score += 1.5
                matched_c.append(c)
                break

    # Value matches (light weight)
    for v in sig.get("values", []):
        if v in values:
            score += 0.5
            matched_v.append(v)

    score *= sig.get("boost", 1.0)

    return DomainScore(
        domain=domain, score=round(score, 2),
        matched_tables=matched_t[:8],
        matched_columns=matched_c[:8],
        matched_values=matched_v[:8],
    )


def _persist(detection: DomainDetection, base: Path) -> None:
    try:
        base.mkdir(parents=True, exist_ok=True)
        out_path = base / "domain.json"
        out_path.write_text(json.dumps({
            "primary": detection.primary,
            "secondaries": detection.secondaries,
            "confidence": detection.confidence,
            "all_scores": [asdict(s) for s in detection.all_scores],
        }, indent=2))
    except Exception as e:
        logger.warning(f"persist failed: {e}")


def load(project_slug: str, source_id: int,
          *, knowledge_dir: Path = KNOWLEDGE_DIR) -> Optional[dict]:
    """Read previously-detected domain from disk. None if absent."""
    p = knowledge_dir / project_slug / f"source_{source_id}" / "domain.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def all_domains() -> list[str]:
    """List all known domains."""
    return list(DOMAIN_SIGNATURES.keys())
