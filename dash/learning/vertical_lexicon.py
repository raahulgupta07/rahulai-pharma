"""
Vertical Lexicon — Keyword DB + Scoring Helpers for Auto-Vertical Detection.

Pure-Python, zero dependencies. Drives the rule-engine pass in
`auto_configurator.classify_vertical()`. LLM is only used as tie-breaker
when no vertical scores above a strong threshold.

Structure
---------
LEXICONS[vertical_key] = {
    "template":  <template name from dash/templates/registry.py>,
    "tables":    [(keyword, weight 1-5), ...],   # table-name signals
    "columns":   [(keyword, weight 1-5), ...],   # column-name signals
    "docs":      [(keyword, weight 1-5), ...],   # doc filename signals
    "personas":  [persona hint string, ...],     # persona text signals
}

Weights: 1=weak / 2=fair / 3=solid / 4=strong / 5=defining (almost
unambiguous — e.g. "ndc" → pharmacy, "rxnorm" → pharmacy).

Matching is case-insensitive substring against tokens. A token is a
lowercased name with non-alphanumeric replaced by spaces, so
``balance_stock_07052026`` matches both ``balance`` and ``stock``.

Scoring (per vertical)
----------------------
raw = sum(weight) for every (signal, weight) where signal substring
appears in any provided table/column/doc/persona token. Each lexicon row
counts at most once regardless of how many tokens match.

normalized score = clamp(raw / vertical_ceiling, 0, 1) where
vertical_ceiling = sum of all weights in that vertical's lexicon (table
+ column + doc + persona — persona weights default to 2 each).

This keeps scores comparable across verticals even though some have more
keywords than others. A perfect match (every keyword present) → 1.0.

Helpers
-------
- score_vertical(vertical_key, tables, columns, docs, persona="") -> float
- rank_verticals(tables, columns, docs, persona="")              -> list[(vk, score)]
- get_lexicon(vertical_key)                                       -> dict | None
- list_verticals()                                                -> list[str]
"""

from __future__ import annotations

import re
from typing import Iterable

# ---------------------------------------------------------------------------
# Lexicons — 12 core verticals
# ---------------------------------------------------------------------------

LEXICONS: dict[str, dict] = {
    # ------------------------------------------------------------------ PHARMA
    "pharmacy": {
        "template": "pharmacy_network",
        "tables": [
            ("articles", 3),
            ("stock", 3),
            ("drug", 4),
            ("rx", 5),
            ("balance_stock", 5),
            ("pharmacy", 4),
            ("mmreg", 5),
            ("dispense", 4),
            ("medication", 4),
            ("inventory", 2),
            ("prescription", 4),
            ("formulary", 4),
        ],
        "columns": [
            ("composition", 3),
            ("dosage", 3),
            ("indication", 3),
            ("expiry", 3),
            ("lot_number", 3),
            ("ndc", 5),
            ("atc", 4),
            ("prescription", 3),
            ("generic_name", 3),
            ("brand_name", 2),
            ("article_code", 2),
            ("rxnorm", 5),
        ],
        "docs": [
            ("pharmacy", 2),
            ("drug", 2),
            ("rx", 3),
            ("prescription", 3),
            ("compliance", 1),
            ("dispens", 2),
        ],
        "personas": ["pharmacist", "store manager", "dispenser", "rx clerk"],
    },

    # ------------------------------------------------------------------ RETAIL
    "retail": {
        "template": "retail",
        "tables": [
            ("pos", 4),
            ("sales", 2),
            ("transactions", 2),
            ("store", 3),
            ("outlet", 3),
            ("sku", 4),
            ("merchandise", 4),
            ("basket", 4),
            ("till", 3),
            ("receipt", 3),
            ("footfall", 4),
        ],
        "columns": [
            ("sku", 4),
            ("upc", 4),
            ("barcode", 3),
            ("store_id", 3),
            ("till_id", 3),
            ("basket_size", 3),
            ("aisle", 3),
            ("planogram", 4),
            ("category_manager", 2),
            ("loyalty_id", 3),
            ("unit_price", 2),
            ("promo_code", 2),
        ],
        "docs": [
            ("retail", 2),
            ("merchandis", 3),
            ("planogram", 4),
            ("category", 1),
            ("store_audit", 2),
            ("promo", 2),
        ],
        "personas": ["store manager", "category manager", "merchandiser", "cashier"],
    },

    # ------------------------------------------------------------------ HOTEL
    "hotel": {
        "template": "hotel_group",
        "tables": [
            ("reservation", 4),
            ("booking", 3),
            ("guest", 3),
            ("room", 3),
            ("folio", 5),
            ("rate_plan", 4),
            ("housekeeping", 4),
            ("hotel", 3),
            ("property", 2),
            ("check_in", 4),
            ("check_out", 4),
        ],
        "columns": [
            ("adr", 5),
            ("revpar", 5),
            ("occupancy", 4),
            ("room_type", 3),
            ("rate_code", 3),
            ("los", 2),
            ("ota", 3),
            ("pms_id", 4),
            ("rate_plan_code", 3),
            ("nights", 2),
            ("arrival_date", 3),
            ("departure_date", 3),
        ],
        "docs": [
            ("hotel", 2),
            ("revenue_manag", 3),
            ("housekeeping", 3),
            ("rate_strategy", 3),
            ("brand_standard", 2),
            ("guest", 1),
        ],
        "personas": ["revenue manager", "front desk", "hotel gm", "housekeeping lead"],
    },

    # ------------------------------------------------------------------ BANK
    "bank": {
        "template": "bank",
        "tables": [
            ("account", 3),
            ("loan", 4),
            ("transaction", 2),
            ("ledger", 3),
            ("branch", 3),
            ("customer", 2),
            ("teller", 4),
            ("deposit", 3),
            ("withdrawal", 3),
            ("kyc", 5),
            ("aml_alert", 5),
        ],
        "columns": [
            ("iban", 5),
            ("swift", 4),
            ("bic", 4),
            ("account_number", 3),
            ("balance", 2),
            ("credit_score", 4),
            ("loan_amount", 3),
            ("interest_rate", 3),
            ("npa", 4),
            ("branch_code", 3),
            ("aml_risk", 4),
            ("kyc_status", 4),
        ],
        "docs": [
            ("bank", 2),
            ("loan_policy", 3),
            ("kyc", 4),
            ("aml", 4),
            ("basel", 4),
            ("credit_risk", 3),
        ],
        "personas": ["branch manager", "loan officer", "compliance officer", "teller"],
    },

    # --------------------------------------------------------------- INSURANCE
    "insurance": {
        "template": "insurance",
        "tables": [
            ("policy", 4),
            ("claim", 4),
            ("premium", 4),
            ("underwriting", 5),
            ("insured", 4),
            ("broker", 3),
            ("policyholder", 4),
            ("reinsurance", 5),
            ("loss_event", 4),
            ("endorsement", 4),
        ],
        "columns": [
            ("policy_number", 4),
            ("sum_insured", 5),
            ("premium_amount", 3),
            ("deductible", 4),
            ("claim_amount", 3),
            ("loss_ratio", 4),
            ("peril", 4),
            ("coverage_type", 3),
            ("underwriter", 3),
            ("renewal_date", 3),
            ("incurred_loss", 4),
        ],
        "docs": [
            ("policy_wording", 4),
            ("claim", 3),
            ("underwriting", 4),
            ("actuarial", 4),
            ("reinsurance", 3),
            ("solvency", 3),
        ],
        "personas": ["underwriter", "claims adjuster", "actuary", "broker"],
    },

    # -------------------------------------------------------------- HEALTHCARE
    "healthcare": {
        "template": "healthcare",
        "tables": [
            ("patient", 4),
            ("encounter", 4),
            ("admission", 4),
            ("discharge", 4),
            ("clinical", 3),
            ("diagnosis", 4),
            ("provider", 3),
            ("appointment", 3),
            ("lab_result", 4),
            ("vital_sign", 4),
            ("ehr", 5),
            ("emr", 5),
        ],
        "columns": [
            ("mrn", 5),
            ("icd10", 5),
            ("icd_10", 5),
            ("cpt", 5),
            ("snomed", 5),
            ("hl7", 5),
            ("admission_date", 3),
            ("discharge_date", 3),
            ("length_of_stay", 4),
            ("readmit", 4),
            ("provider_npi", 5),
            ("dob", 2),
        ],
        "docs": [
            ("clinical", 3),
            ("hipaa", 4),
            ("ehr", 4),
            ("patient_chart", 3),
            ("care_pathway", 3),
            ("provider", 1),
        ],
        "personas": ["clinician", "nurse", "hospital admin", "case manager"],
    },

    # ---------------------------------------------------------------- ECOMMERCE
    "ecommerce": {
        "template": "ecommerce",
        "tables": [
            ("orders", 3),
            ("order_items", 4),
            ("cart", 4),
            ("checkout", 4),
            ("product", 2),
            ("variant", 3),
            ("shipment", 3),
            ("fulfillment", 3),
            ("return", 3),
            ("session", 2),
            ("pageview", 4),
            ("clickstream", 4),
        ],
        "columns": [
            ("session_id", 3),
            ("cart_id", 4),
            ("sku", 3),
            ("utm_source", 4),
            ("utm_campaign", 4),
            ("conversion_rate", 4),
            ("aov", 3),
            ("cart_abandon", 4),
            ("ship_method", 2),
            ("payment_gateway", 3),
            ("landing_page", 3),
            ("checkout_step", 4),
        ],
        "docs": [
            ("ecommerce", 3),
            ("conversion", 2),
            ("storefront", 3),
            ("marketing_funnel", 3),
            ("checkout", 2),
            ("growth", 1),
        ],
        "personas": ["growth marketer", "ecom manager", "merchandiser", "cro analyst"],
    },

    # --------------------------------------------------------------------- SAAS
    "saas": {
        "template": "saas",
        "tables": [
            ("subscription", 5),
            ("plan", 3),
            ("billing_cycle", 4),
            ("invoice", 3),
            ("seat", 4),
            ("workspace", 3),
            ("tenant", 3),
            ("feature_flag", 4),
            ("usage_event", 4),
            ("activation", 3),
        ],
        "columns": [
            ("mrr", 5),
            ("arr", 5),
            ("churn_rate", 4),
            ("ltv", 4),
            ("cac", 4),
            ("trial_end", 4),
            ("plan_tier", 3),
            ("seat_count", 4),
            ("activation_date", 3),
            ("nps", 3),
            ("dau", 4),
            ("mau", 4),
        ],
        "docs": [
            ("saas", 3),
            ("subscription", 3),
            ("pricing", 2),
            ("onboarding", 2),
            ("product_metric", 3),
            ("churn", 2),
        ],
        "personas": ["product manager", "growth lead", "customer success", "rev ops"],
    },

    # ----------------------------------------------------------- MANUFACTURING
    "manufacturing": {
        "template": "manufacturing",
        "tables": [
            ("bom", 5),
            ("work_order", 4),
            ("production_run", 4),
            ("raw_material", 4),
            ("defect", 4),
            ("machine", 3),
            ("shift", 2),
            ("yield", 4),
            ("scrap", 4),
            ("inspection", 3),
            ("plant", 3),
        ],
        "columns": [
            ("bom_id", 4),
            ("work_order_id", 4),
            ("oee", 5),
            ("yield_pct", 4),
            ("scrap_rate", 4),
            ("downtime_min", 4),
            ("cycle_time", 4),
            ("machine_id", 3),
            ("operator_id", 2),
            ("lot_number", 2),
            ("defect_code", 4),
            ("shift_id", 2),
        ],
        "docs": [
            ("production", 2),
            ("plant_layout", 3),
            ("oee", 4),
            ("quality_audit", 3),
            ("sop", 2),
            ("kaizen", 3),
        ],
        "personas": ["plant manager", "production planner", "quality engineer", "shift lead"],
    },

    # ------------------------------------------------------------ SUPPLY CHAIN
    "supply_chain": {
        "template": "supply_chain",
        "tables": [
            ("shipment", 3),
            ("warehouse", 3),
            ("inventory", 3),
            ("supplier", 3),
            ("purchase_order", 4),
            ("goods_receipt", 4),
            ("dock_door", 4),
            ("3pl", 4),
            ("pick_ticket", 4),
            ("transfer_order", 4),
            ("route", 3),
        ],
        "columns": [
            ("po_number", 4),
            ("sku", 2),
            ("warehouse_id", 3),
            ("supplier_id", 3),
            ("lead_time", 4),
            ("safety_stock", 4),
            ("reorder_point", 4),
            ("on_hand_qty", 3),
            ("in_transit_qty", 3),
            ("otif", 5),
            ("dock_door_id", 3),
            ("carrier", 3),
        ],
        "docs": [
            ("supply_chain", 4),
            ("logistics", 3),
            ("warehouse", 2),
            ("supplier_contract", 3),
            ("incoterm", 3),
            ("3pl", 3),
        ],
        "personas": ["supply planner", "warehouse manager", "logistics coordinator", "procurement"],
    },

    # ------------------------------------------------------------------------ HR
    "hr": {
        "template": "hr_analytics",
        "tables": [
            ("employee", 4),
            ("payroll", 4),
            ("attendance", 4),
            ("leave", 3),
            ("recruit", 3),
            ("performance", 3),
            ("compensation", 4),
            ("headcount", 4),
            ("onboarding", 3),
            ("offboarding", 3),
        ],
        "columns": [
            ("employee_id", 3),
            ("hire_date", 3),
            ("termination_date", 3),
            ("salary", 3),
            ("base_pay", 3),
            ("bonus", 2),
            ("manager_id", 3),
            ("department", 2),
            ("job_grade", 3),
            ("attrition", 4),
            ("ftes", 3),
            ("pay_band", 4),
        ],
        "docs": [
            ("hr", 2),
            ("payroll", 3),
            ("employee_handbook", 3),
            ("compensation", 3),
            ("recruit", 2),
            ("diversity", 3),
        ],
        "personas": ["hr business partner", "talent acquisition", "compensation analyst", "people analytics"],
    },

    # -------------------------------------------------------------------- FINANCE
    "finance": {
        "template": "finance_fpa",
        "tables": [
            ("gl", 4),
            ("general_ledger", 5),
            ("journal", 3),
            ("trial_balance", 5),
            ("budget", 3),
            ("forecast", 3),
            ("accruals", 4),
            ("cost_center", 4),
            ("ap_invoice", 4),
            ("ar_invoice", 4),
            ("chart_of_account", 5),
        ],
        "columns": [
            ("gl_account", 5),
            ("account_code", 3),
            ("cost_center", 4),
            ("debit", 3),
            ("credit", 3),
            ("fiscal_period", 4),
            ("fiscal_year", 3),
            ("budget_amount", 3),
            ("actual_amount", 3),
            ("variance_pct", 4),
            ("ebitda", 4),
            ("opex", 3),
        ],
        "docs": [
            ("finance", 2),
            ("fp_a", 4),
            ("budget", 2),
            ("month_end_close", 4),
            ("ifrs", 4),
            ("gaap", 4),
        ],
        "personas": ["finance controller", "fp&a analyst", "accounts manager", "treasurer"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokenize(items: Iterable[str]) -> set[str]:
    """Lowercase + normalize separators into a single flat searchable blob.

    Returns a *set* of tokens (whitespace-split). We also keep the joined
    blob so substring matches across token boundaries (e.g. ``balance_stock``)
    still work — callers do ``keyword in blob``.
    """
    out: set[str] = set()
    for raw in items or []:
        if not raw:
            continue
        norm = _TOKEN_SPLIT.sub(" ", str(raw).lower()).strip()
        if not norm:
            continue
        out.add(norm)
        for tok in norm.split():
            if tok:
                out.add(tok)
    return out


def _build_blob(tokens: Iterable[str]) -> str:
    """Single space-joined string for substring matching."""
    return " " + " ".join(sorted(tokens)) + " "


def _vertical_ceiling(lex: dict) -> int:
    """Maximum possible raw score for a vertical (sum of all weights)."""
    total = 0
    for k in ("tables", "columns", "docs"):
        for _kw, w in lex.get(k, []):
            total += int(w)
    # persona hints contribute a flat weight of 2 each (they're soft signals)
    total += 2 * len(lex.get("personas", []))
    return max(total, 1)


def _score_section(blob: str, section: list[tuple[str, int]]) -> tuple[int, list[str]]:
    """Sum weights for matched keywords + return matched signal labels."""
    raw = 0
    hits: list[str] = []
    for kw, weight in section:
        kw_norm = _TOKEN_SPLIT.sub(" ", kw.lower()).strip()
        if not kw_norm:
            continue
        if kw_norm in blob:
            raw += int(weight)
            hits.append(kw)
    return raw, hits


def _score_personas(blob: str, personas: list[str]) -> tuple[int, list[str]]:
    raw = 0
    hits: list[str] = []
    for p in personas:
        p_norm = _TOKEN_SPLIT.sub(" ", p.lower()).strip()
        if p_norm and p_norm in blob:
            raw += 2
            hits.append(p)
    return raw, hits


def score_vertical(
    vertical_key: str,
    tables: Iterable[str] | None = None,
    columns: Iterable[str] | None = None,
    docs: Iterable[str] | None = None,
    persona: str = "",
) -> float:
    """Score a single vertical against gathered signals. Returns 0.0–1.0."""
    lex = LEXICONS.get(vertical_key)
    if not lex:
        return 0.0

    table_blob = _build_blob(_tokenize(tables or []))
    col_blob = _build_blob(_tokenize(columns or []))
    doc_blob = _build_blob(_tokenize(docs or []))
    persona_blob = _build_blob(_tokenize([persona] if persona else []))

    raw = 0
    raw += _score_section(table_blob, lex.get("tables", []))[0]
    raw += _score_section(col_blob, lex.get("columns", []))[0]
    raw += _score_section(doc_blob, lex.get("docs", []))[0]
    raw += _score_personas(persona_blob, lex.get("personas", []))[0]

    ceiling = _vertical_ceiling(lex)
    score = raw / ceiling
    if score < 0:
        return 0.0
    if score > 1:
        return 1.0
    return round(score, 4)


def score_vertical_detail(
    vertical_key: str,
    tables: Iterable[str] | None = None,
    columns: Iterable[str] | None = None,
    docs: Iterable[str] | None = None,
    persona: str = "",
) -> dict:
    """Score + return matched signals (used by classifier for `signals` field)."""
    lex = LEXICONS.get(vertical_key)
    if not lex:
        return {"score": 0.0, "signals": [], "raw": 0, "ceiling": 1}

    table_blob = _build_blob(_tokenize(tables or []))
    col_blob = _build_blob(_tokenize(columns or []))
    doc_blob = _build_blob(_tokenize(docs or []))
    persona_blob = _build_blob(_tokenize([persona] if persona else []))

    raw = 0
    signals: list[str] = []

    r, hits = _score_section(table_blob, lex.get("tables", []))
    raw += r
    signals += [f"table:{h}" for h in hits]

    r, hits = _score_section(col_blob, lex.get("columns", []))
    raw += r
    signals += [f"col:{h}" for h in hits]

    r, hits = _score_section(doc_blob, lex.get("docs", []))
    raw += r
    signals += [f"doc:{h}" for h in hits]

    r, hits = _score_personas(persona_blob, lex.get("personas", []))
    raw += r
    signals += [f"persona:{h}" for h in hits]

    ceiling = _vertical_ceiling(lex)
    score = raw / ceiling
    if score < 0:
        score = 0.0
    if score > 1:
        score = 1.0

    return {
        "score": round(score, 4),
        "signals": signals,
        "raw": raw,
        "ceiling": ceiling,
    }


def rank_verticals(
    tables: Iterable[str] | None = None,
    columns: Iterable[str] | None = None,
    docs: Iterable[str] | None = None,
    persona: str = "",
) -> list[tuple[str, float]]:
    """Score every known vertical and return sorted desc by score."""
    results = [
        (vk, score_vertical(vk, tables, columns, docs, persona))
        for vk in LEXICONS.keys()
    ]
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def get_lexicon(vertical_key: str) -> dict | None:
    """Return the lexicon dict for `vertical_key` (or None if unknown)."""
    lex = LEXICONS.get(vertical_key)
    if lex is None:
        return None
    # return a shallow copy so callers can't mutate the registry
    return {
        "template": lex["template"],
        "tables": list(lex.get("tables", [])),
        "columns": list(lex.get("columns", [])),
        "docs": list(lex.get("docs", [])),
        "personas": list(lex.get("personas", [])),
    }


def list_verticals() -> list[str]:
    """Return all known vertical keys."""
    return list(LEXICONS.keys())


__all__ = [
    "LEXICONS",
    "score_vertical",
    "score_vertical_detail",
    "rank_verticals",
    "get_lexicon",
    "list_verticals",
]
