"""
Metric Seed
===========
Seeds the 9 verified CRM metric definitions from crm_metrics.py into the
DB-backed metric engine for proj_demo_pg_crm.

Idempotent: save_definition uses ON CONFLICT DO UPDATE so re-running is safe.

Column name constants (from crm_metrics.py):
    C_STATUS   = "status"
    C_OUTCOME  = "call_outcome"
    C_CAT      = "call_category__affiliate_value_name"
    C_TYPE     = "related_brand_relationship__type"
    C_RSTATUS  = "related_brand_relationship__status"
    C_CHANNEL  = "related_channel_response__channel_type"
    C_BRAND    = "related_channel_response__brand"
    C_REASON   = "unsuccessful_reason__affiliate_value_name"
    C_CITY     = "city"

Truth values (verified 2026-05 against proj_demo_pg_crm Jan–Jun 2025):
    total_leads          = 1,544
    new_users (all)      = 658
    drop_off_users       = 2,630
    contribution         = Successful 7,526 (64.3%) / Unsuccessful 4,179 (35.7%)
    top drop-off reason 'No purchase - normal meals is enough' = 636
"""

from __future__ import annotations

import logging

from dash.tools.metric_compiler import save_definition

logger = logging.getLogger(__name__)

# ── Column aliases ────────────────────────────────────────────────────────────
C_STATUS = "status"
C_OUTCOME = "call_outcome"
C_CAT = "call_category__affiliate_value_name"
C_TYPE = "related_brand_relationship__type"
C_RSTATUS = "related_brand_relationship__status"
C_CHANNEL = "related_channel_response__channel_type"
C_BRAND = "related_channel_response__brand"
C_REASON = "unsuccessful_reason__affiliate_value_name"
C_CITY = "city"

# ── Available group dimensions for CRM metrics ────────────────────────────────
CRM_GROUP_DIMS = ["month", C_CHANNEL, C_BRAND, C_CITY, C_REASON]

# ── 9 CRM metric definitions ──────────────────────────────────────────────────

_CRM_DEFS = [
    {
        "name": "total_leads",
        "synonyms": ["leads", "lead_count"],
        "description": "Total leads. Completed + Unsuccessful + type=Lead + status=Non_User. (truth=1,544)",
        "kind": "count",
        "source_glob": "crm_*",
        "filters": [
            {"col": C_STATUS,  "op": "=", "value": "Completed",   "trim": True},
            {"col": C_OUTCOME, "op": "=", "value": "Unsuccessful", "trim": True},
            {"col": C_TYPE,    "op": "=", "value": "Lead",         "trim": True},
            {"col": C_RSTATUS, "op": "=", "value": "Non_User",     "trim": True},
        ],
        "denom_filters": [],
        "group_dims": CRM_GROUP_DIMS,
        "default_group": [],
        "trim_values": True,
        "status": "verified",
        "verified_answer": {"total": 1544},
    },
    {
        "name": "channel_breakdown",
        "synonyms": ["leads_by_channel", "lead_channel"],
        "description": "Lead breakdown by channel/brand (same lead definition as total_leads).",
        "kind": "count",
        "source_glob": "crm_*",
        "filters": [
            {"col": C_STATUS,  "op": "=", "value": "Completed",   "trim": True},
            {"col": C_OUTCOME, "op": "=", "value": "Unsuccessful", "trim": True},
            {"col": C_TYPE,    "op": "=", "value": "Lead",         "trim": True},
            {"col": C_RSTATUS, "op": "=", "value": "Non_User",     "trim": True},
        ],
        "denom_filters": [],
        "group_dims": CRM_GROUP_DIMS,
        "default_group": [C_CHANNEL, C_BRAND],
        "trim_values": True,
        "status": "verified",
        "verified_answer": {"total": 1544},
    },
    {
        "name": "new_users",
        "synonyms": ["new_user_count", "recruited_users"],
        "description": (
            "New users. Completed + Successful + type=User + status=New. "
            "(truth total=658)"
        ),
        "kind": "count",
        "source_glob": "crm_*",
        "filters": [
            {"col": C_STATUS,  "op": "=", "value": "Completed",  "trim": True},
            {"col": C_OUTCOME, "op": "=", "value": "Successful",  "trim": True},
            {"col": C_TYPE,    "op": "=", "value": "User",        "trim": True},
            {"col": C_RSTATUS, "op": "=", "value": "New",         "trim": True},
        ],
        "denom_filters": [],
        "group_dims": CRM_GROUP_DIMS,
        "default_group": ["month", C_CHANNEL, C_BRAND],
        "trim_values": True,
        "status": "verified",
        "verified_answer": {"total": 658},
    },
    {
        "name": "drop_off_users",
        "synonyms": ["lapsed_users", "dropoff_users", "churned_users"],
        "description": (
            "Drop-off / lapsed users. Retention Call + Completed + Unsuccessful "
            "+ type=User + status=Lapsed. (truth=2,630)"
        ),
        "kind": "count",
        "source_glob": "crm_*",
        "filters": [
            {"col": C_CAT,     "op": "=", "value": "Retention Call", "trim": True},
            {"col": C_STATUS,  "op": "=", "value": "Completed",       "trim": True},
            {"col": C_OUTCOME, "op": "=", "value": "Unsuccessful",    "trim": True},
            {"col": C_TYPE,    "op": "=", "value": "User",            "trim": True},
            {"col": C_RSTATUS, "op": "=", "value": "Lapsed",          "trim": True},
        ],
        "denom_filters": [],
        "group_dims": CRM_GROUP_DIMS,
        "default_group": ["month", C_CHANNEL, C_BRAND],
        "trim_values": True,
        "status": "verified",
        "verified_answer": {"total": 2630},
    },
    {
        "name": "drop_off_reasons",
        "synonyms": ["lapsed_reasons", "dropoff_reasons"],
        "description": (
            "Drop-off reasons. Retention Call + Completed + Unsuccessful "
            "+ status=Lapsed, grouped by reason. "
            "(top reason 'normal meals' truth=636)"
        ),
        "kind": "count",
        "source_glob": "crm_*",
        "filters": [
            {"col": C_CAT,     "op": "=", "value": "Retention Call", "trim": True},
            {"col": C_STATUS,  "op": "=", "value": "Completed",       "trim": True},
            {"col": C_OUTCOME, "op": "=", "value": "Unsuccessful",    "trim": True},
            {"col": C_RSTATUS, "op": "=", "value": "Lapsed",          "trim": True},
        ],
        "denom_filters": [],
        "group_dims": CRM_GROUP_DIMS,
        "default_group": [C_BRAND, C_REASON],
        "trim_values": True,
        "status": "verified",
        "verified_answer": {"top_reason_count": 636},
    },
    {
        "name": "recruitment_rate",
        "synonyms": ["recruit_rate", "recruitment_success_rate"],
        "description": (
            "Recruitment rate = successful-new recruitment calls / "
            "completed recruitment calls."
        ),
        "kind": "rate",
        "source_glob": "crm_*",
        "filters": [
            {"col": C_CAT,     "op": "=", "value": "Recruitment Call", "trim": True},
            {"col": C_OUTCOME, "op": "=", "value": "Successful",        "trim": True},
            {"col": C_RSTATUS, "op": "=", "value": "New",               "trim": True},
        ],
        "denom_filters": [
            {"col": C_CAT,    "op": "=", "value": "Recruitment Call", "trim": True},
            {"col": C_STATUS, "op": "=", "value": "Completed",         "trim": True},
        ],
        "group_dims": CRM_GROUP_DIMS,
        "default_group": ["month", C_CHANNEL],
        "trim_values": True,
        "status": "verified",
        "verified_answer": {"rate_pct": 29.1},
    },
    {
        "name": "retention_rate",
        "synonyms": ["retain_rate", "retention_success_rate"],
        "description": (
            "Retention rate = successful retention calls (Retained/Existing) / "
            "completed retention calls."
        ),
        "kind": "rate",
        "source_glob": "crm_*",
        "filters": [
            {"col": C_CAT,     "op": "=",   "value": "Retention Call",       "trim": True},
            {"col": C_OUTCOME, "op": "=",   "value": "Successful",             "trim": True},
            {"col": C_RSTATUS, "op": "IN",  "value": ["Retained", "Existing"], "trim": True},
        ],
        "denom_filters": [
            {"col": C_CAT,    "op": "=", "value": "Retention Call", "trim": True},
            {"col": C_STATUS, "op": "=", "value": "Completed",       "trim": True},
        ],
        "group_dims": CRM_GROUP_DIMS,
        "default_group": ["month", C_CHANNEL],
        "trim_values": True,
        "status": "verified",
        "verified_answer": {},
    },
    {
        "name": "drop_off_rate",
        "synonyms": ["lapse_rate", "churn_rate"],
        "description": (
            "Drop-off rate = lapsed unsuccessful retention calls / "
            "completed retention calls."
        ),
        "kind": "rate",
        "source_glob": "crm_*",
        "filters": [
            {"col": C_CAT,     "op": "=", "value": "Retention Call", "trim": True},
            {"col": C_STATUS,  "op": "=", "value": "Completed",       "trim": True},
            {"col": C_OUTCOME, "op": "=", "value": "Unsuccessful",    "trim": True},
            {"col": C_RSTATUS, "op": "=", "value": "Lapsed",          "trim": True},
        ],
        "denom_filters": [
            {"col": C_CAT,    "op": "=", "value": "Retention Call", "trim": True},
            {"col": C_STATUS, "op": "=", "value": "Completed",       "trim": True},
        ],
        "group_dims": CRM_GROUP_DIMS,
        "default_group": ["month", C_CHANNEL],
        "trim_values": True,
        "status": "verified",
        "verified_answer": {"rate_pct": 27.7},
    },
    {
        "name": "contribution",
        "synonyms": ["outcome_contribution", "outcome_split", "contribution_pct"],
        "description": (
            "Successful vs unsuccessful contribution = each outcome's share of "
            "completed calls with an outcome. "
            "(truth 64.3% Successful / 35.7% Unsuccessful)"
        ),
        "kind": "contribution",
        "source_glob": "crm_*",
        "filters": [
            {"col": C_STATUS,  "op": "=",   "value": "Completed",                  "trim": True},
            {"col": C_OUTCOME, "op": "IN",  "value": ["Successful", "Unsuccessful"], "trim": True},
        ],
        "denom_filters": [],
        "group_dims": [C_OUTCOME, "month", C_CHANNEL, C_BRAND],
        "default_group": [C_OUTCOME],
        "trim_values": True,
        "status": "verified",
        "verified_answer": {
            "successful_pct": 64.3,
            "unsuccessful_pct": 35.7,
            "successful_count": 7526,
            "unsuccessful_count": 4179,
        },
    },
]


# Brain `formula` entries that the metric defs above now supersede. Removing
# them stops get_brain_context() from injecting a second, drift-prone definition
# of the same metric into the Analyst prompt (the metric tool is now the single
# source of truth — see instructions.py "METRIC DEFINITIONS ARE AUTHORITATIVE").
_SUPERSEDED_BRAIN_FORMULAS = [
    "Lead count",
    "New User count",
    "Drop-off / Lapsed User count",
    "Call contribution percent",
    "Recruitment Rate",
    "Retention Rate",
    "Drop-off Rate",
]


def _dedupe_brain_formulas(project_slug: str) -> int:
    """Delete project-scoped Brain `formula` rows that a verified metric def now
    supersedes. Fail-soft. Returns rows deleted."""
    try:
        from sqlalchemy import text
        from db.session import get_write_engine

        eng = get_write_engine()
        with eng.begin() as conn:
            res = conn.execute(
                text(
                    "DELETE FROM public.dash_company_brain "
                    "WHERE project_slug = :slug AND category = 'formula' "
                    "AND name = ANY(:names)"
                ),
                {"slug": project_slug, "names": _SUPERSEDED_BRAIN_FORMULAS},
            )
            return res.rowcount or 0
    except Exception as exc:
        logger.warning("Brain formula dedupe failed for %s: %s", project_slug, exc)
        return 0


def seed_metric_definitions(project_slug: str = "proj_demo_pg_crm") -> dict:
    """Seed the 9 CRM metric definitions into public.dash_metric_definitions.

    Idempotent — safe to run multiple times (ON CONFLICT DO UPDATE). Also removes
    the now-superseded Brain `formula` rows so the agent has a single source of
    truth per metric. Returns a summary dict with counts.
    """
    saved = []
    errors = []
    for defn in _CRM_DEFS:
        try:
            row = save_definition(project_slug, defn, user="seed_metric_definitions")
            saved.append(row.get("name", defn["name"]))
            logger.info("Seeded metric: %s (id=%s)", defn["name"], row.get("id"))
        except Exception as exc:
            msg = f"{defn['name']}: {exc}"
            errors.append(msg)
            logger.warning("Failed to seed metric %s: %s", defn["name"], exc)

    brain_removed = _dedupe_brain_formulas(project_slug)

    return {
        "ok": len(errors) == 0,
        "project_slug": project_slug,
        "seeded": saved,
        "errors": errors,
        "total": len(_CRM_DEFS),
        "success_count": len(saved),
        "brain_formulas_removed": brain_removed,
    }


if __name__ == "__main__":
    import sys
    result = seed_metric_definitions()
    print(result)
    if not result["ok"]:
        sys.exit(1)
