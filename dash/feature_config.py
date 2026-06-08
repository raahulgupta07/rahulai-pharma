"""Per-project feature config.

Resolves which tabs / tools / agents are enabled for a given project.
Backend gating reads this in build.py + team.py + chat router.
Frontend reads via GET /api/projects/{slug}/feature-config.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Defaults — full power; existing projects keep behaving as before.
DEFAULT_CONFIG: dict = {
    "tabs": {
        "analysis": True,
        "data": True,
        "query": True,
        "chart": True,
        "sources": True,
        "exec_view": True,    # 2026-05-26: default ON — consistent AnswerCard render across cached+full agent paths
    },
    # 2026-06-07: HARDCODED for the fixed CityPharma chemist agent — CORE ONLY.
    # This is a single locked pharma agent, not a multi-vertical tenant. The
    # Config toggle UI + APPLY VERTICAL + industry templates were removed; these
    # values are the product's fixed capability set. Pharma tools
    # (stock_check/substitutes/indications) are gated by PHARMA_GRAPH_DISABLED,
    # NOT feature_config, so they're always on regardless of this dict.
    "tools": {
        "sql": True,
        "charts": True,
        "dashboards": True,
        "forecast": False,               # off — chemist counter agent, no demand-forecasting
        "anomaly": False,                # off — no outlier/diagnostics lane
        "auto_campaign_daemon": False,   # off — no marketing campaigns
        "office_skills": False,
        "table_usage_boost": False,
    },
    "agents": {
        "analyst": True,
        "engineer": True,
        "researcher": True,
        # Venture/portfolio agents — permanently OFF for the pharma chemist.
        "customer_strategist": False,
        "deal_analyst": False,           # VentureDesk: DCF/IRR/MOIC
        "market_sentinel": False,        # external intel — needs web access
        "ops_optimizer": False,          # KPI tracking — needs portfolio data
        "supply_sentry": False,          # supply risk — needs supplier data
    },
    # Auto-scope guardrail — populated by training step `derive_scope`.
    # Empty dict means "no scope filter" (legacy behavior).
    "scope": {},
}


def get_scope(project_slug: str | None) -> dict:
    """Convenience accessor — returns {} if no scope set."""
    cfg = get_feature_config(project_slug)
    s = cfg.get("scope") or {}
    return s if isinstance(s, dict) else {}


def set_scope(project_slug: str, scope: dict, mark_auto: bool = True) -> dict:
    """Persist a derived/edited scope back into feature_config.scope."""
    cfg = get_feature_config(project_slug)
    new = dict(cfg)
    s = dict(scope or {})
    if mark_auto:
        s["_auto"] = True
        from datetime import datetime, timezone
        s["_derived_at"] = datetime.now(timezone.utc).isoformat()
    new["scope"] = s
    return set_feature_config(project_slug, new)

def _t(*on: str) -> dict[str, bool]:
    """Build tabs dict from short list of enabled tab names. Always include analysis+sources."""
    base = {"analysis": False, "data": False, "query": False, "chart": False, "sources": False}
    for k in on:
        if k in base:
            base[k] = True
    base["analysis"] = True
    base["sources"] = True
    return base


def _c(*on: str) -> dict[str, bool]:
    base = {"sql": False, "charts": False, "dashboards": False, "forecast": False, "anomaly": False}
    for k in on:
        if k in base:
            base[k] = True
    return base


def _a(*on: str) -> dict[str, bool]:
    base = {
        "analyst": False, "engineer": False, "researcher": False,
        "customer_strategist": False, "deal_analyst": False,
        "market_sentinel": False, "ops_optimizer": False, "supply_sentry": False,
    }
    for k in on:
        if k in base:
            base[k] = True
    if not any(base.values()):
        base["researcher"] = True
    return base


# NOTE: Industry preset PRESETS dict was removed. The _preset helper is kept
# below for any callers that still build ad-hoc preset payloads; nothing in the
# repo references PRESETS, apply_preset, or list_presets after this change.
def _preset(label: str, category: str, desc: str,
            tabs: dict, tools: dict, agents: dict) -> dict:
    return {"_label": label, "_category": category, "_desc": desc,
            "tabs": tabs, "tools": tools, "agents": agents}


def _merge(default: dict, override: dict) -> dict:
    """Merge override on top of default. tabs/tools/agents = bool dicts;
    scope = arbitrary dict preserved verbatim."""
    out = {}
    for k, v in default.items():
        out[k] = dict(v) if isinstance(v, dict) else v
    for section, vals in (override or {}).items():
        if section == "scope":
            out["scope"] = dict(vals) if isinstance(vals, dict) else (vals or {})
            continue
        if section in out and isinstance(vals, dict):
            out[section].update({k: bool(v) for k, v in vals.items()})
        elif isinstance(vals, dict):
            out[section] = {k: bool(v) for k, v in vals.items()}
    return out


def get_feature_config(project_slug: str | None) -> dict[str, dict[str, bool]]:
    """Resolve project's effective feature config (defaults + DB overrides)."""
    if not project_slug:
        return _merge(DEFAULT_CONFIG, {})

    try:
        from dash.tools.skill_refinery import _get_engine
        from sqlalchemy import text
        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT feature_config FROM public.dash_projects WHERE slug = :s"
            ), {"s": project_slug}).first()
    except Exception as e:
        logger.warning("feature_config: load failed for %s: %s", project_slug, e)
        return _merge(DEFAULT_CONFIG, {})

    if not row or not row[0]:
        return _merge(DEFAULT_CONFIG, {})
    raw = row[0]
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except Exception: raw = {}
    return _merge(DEFAULT_CONFIG, raw or {})


def is_enabled(project_slug: str | None, section: str, key: str) -> bool:
    cfg = get_feature_config(project_slug)
    return bool(cfg.get(section, {}).get(key, True))


def set_feature_config(project_slug: str, config: dict[str, Any]) -> dict:
    """Validate + persist user-supplied config. Returns the merged effective config."""
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")

    clean: dict = {}
    for section in ("tabs", "tools", "agents"):
        if section in config and isinstance(config[section], dict):
            clean[section] = {str(k): bool(v) for k, v in config[section].items()}
    # Scope guardrail (auto-derived or admin-edited) — preserve as-is, no boolean coercion.
    if "scope" in config and isinstance(config["scope"], dict):
        clean["scope"] = config["scope"]

    from dash.tools.skill_refinery import _get_engine
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE public.dash_projects SET feature_config = :c WHERE slug = :s"
        ), {"c": json.dumps(clean), "s": project_slug})

    try:
        from dash.team import invalidate_team_cache
        invalidate_team_cache(project_slug)
    except Exception as e:
        logger.warning("feature_config: team cache invalidate failed: %s", e)

    return _merge(DEFAULT_CONFIG, clean)


# apply_preset() and list_presets() were removed along with the PRESETS dict.
# Callers should use set_feature_config() directly with a hand-built config.


# ── Smart recommend: derive a config from the project's actual trained schema ──
_DATE_HINTS = ("date", "time", "timestamp", "datetime")
_DATE_NAMES = ("date", "day", "month", "year", "period", "week", "quarter", "dt", "created", "ts")
_NUM_HINTS = ("int", "numeric", "decimal", "double", "real", "float", "money", "bigint", "smallint")
_CUSTOMER_NAMES = (
    "customer", "transaction", "txn", "order", "sale", "invoice",
    "account", "client", "subscriber", "member", "patient", "user_id",
)


def derive_recommended_config(project_slug: str) -> dict:
    """Inspect the project's trained schema and recommend a feature_config.

    Heuristics (fail-soft — any error returns DEFAULT_CONFIG with a note):
      - tables present              → sql, analyst, engineer, charts, dashboards ON
      - no tables                   → sql OFF (doc-only project), researcher only
      - date col + numeric col      → forecasting ON
      - numeric depth (≥1 numeric)  → anomaly/diagnostics ON
      - auto_campaign_daemon        → always OFF (niche background daemon)

    Returns {"config": <feature_config>, "reasons": [str, ...]}.
    """
    reasons: list[str] = []
    tables: dict[str, list[tuple[str, str]]] = {}
    try:
        from dash.tools.skill_refinery import _get_engine
        from sqlalchemy import text
        eng = _get_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = :s ORDER BY table_name"
            ), {"s": project_slug}).fetchall()
        for t, col, dt in rows:
            tables.setdefault(t, []).append((str(col).lower(), str(dt).lower()))
    except Exception as e:  # noqa: BLE001
        logger.warning("derive_recommended_config: introspect failed for %s: %s", project_slug, e)
        return {"config": _merge(DEFAULT_CONFIG, {}), "reasons": [
            "Could not read schema — returning safe defaults (all on)."]}

    n_tables = len(tables)
    all_cols = [(c, d) for cols in tables.values() for (c, d) in cols]
    has_date = any(
        any(h in d for h in _DATE_HINTS) or any(n in c for n in _DATE_NAMES)
        for c, d in all_cols
    )
    has_numeric = any(any(h in d for h in _NUM_HINTS) for c, d in all_cols)
    has_customer = any(
        any(n in t.lower() for n in _CUSTOMER_NAMES) or any(n in c for c, _ in cols for n in _CUSTOMER_NAMES)
        for t, cols in tables.items()
    )

    tools = {
        "sql": n_tables > 0,
        "charts": n_tables > 0,
        "dashboards": n_tables > 0,
        "forecast": bool(has_date and has_numeric),
        "anomaly": bool(n_tables > 0 and has_numeric),
        "auto_campaign_daemon": False,
    }
    agents = {
        "analyst": n_tables > 0,
        "engineer": n_tables > 0,
        "researcher": True,
    }
    tabs = {
        "analysis": True,
        "sources": True,
        "data": n_tables > 0,
        "query": n_tables > 0,
        "chart": tools["charts"],
    }

    # Human-readable rationale.
    if n_tables > 0:
        reasons.append(f"{n_tables} table(s) detected → SQL + charts + dashboards ON.")
    else:
        reasons.append("No data tables → SQL/charts OFF, document research only.")
    if tools["forecast"]:
        reasons.append("Date + numeric columns → Forecasting ON.")
    else:
        reasons.append("No clear time-series → Forecasting OFF.")
    if tools["anomaly"]:
        reasons.append("Numeric data present → Anomaly + diagnostics ON.")
    else:
        reasons.append("No customer/transaction signal → ML models OFF.")
    reasons.append("Auto-campaign daemon left OFF (niche background job).")

    return {"config": {"tabs": tabs, "tools": tools, "agents": agents}, "reasons": reasons}


def _config_is_untouched(project_slug: str) -> bool:
    """True if no feature_config has ever been persisted for this project.

    We treat a stored config that lacks a 'tools' key as untouched (defaults
    only). Once auto-applied or manually saved, 'tools' exists → leave alone.
    """
    try:
        from dash.tools.skill_refinery import _get_engine
        from sqlalchemy import text
        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT feature_config FROM public.dash_projects WHERE slug = :s"
            ), {"s": project_slug}).first()
    except Exception:  # noqa: BLE001
        return False
    if not row or not row[0]:
        return True
    raw = row[0]
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except Exception: raw = {}
    # scope-only configs (guardrail set during training) still count as untouched
    # for capability purposes — they have 'scope' but no 'tools'.
    return "tools" not in (raw or {})


def apply_recommended_if_unset(project_slug: str) -> dict | None:
    """At train time: if capabilities were never set, persist the data-derived
    recommendation. Preserves any scope guardrail already stored. Returns the
    applied config, or None if skipped (already configured / no slug).

    Manual edits and existing projects are never clobbered — only first-ever
    capability set is auto-filled. Users get all-on via RESET TO DEFAULT.
    """
    if not project_slug or not _config_is_untouched(project_slug):
        return None
    rec = derive_recommended_config(project_slug).get("config") or {}
    if not rec.get("tools"):
        return None
    # Merge with any existing scope guardrail so we don't drop it.
    existing_scope = get_scope(project_slug)
    payload = dict(rec)
    if existing_scope:
        payload["scope"] = existing_scope
    try:
        return set_feature_config(project_slug, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("apply_recommended_if_unset failed for %s: %s", project_slug, e)
        return None
