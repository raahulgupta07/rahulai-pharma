"""Generic CRM starter metric pack — packaged, reusable across ANY CRM agent.

Unlike `crm_metrics.py` / `metric_seed.py` (which encode ONE customer's bespoke
Pahtama/P&G definitions + exact column/value vocabulary), this module ships a
small set of UNIVERSAL CRM metrics that resolve their columns by alias against
the target project's REAL schema at seed time. Any metric whose required
columns aren't found is skipped — no fabricated numbers.

All seeded metrics are status='suggested' (NOT 'verified'): the value vocabulary
("Successful", "Uncontactable") is a best guess for a new tenant, so they are
proposals the owner confirms — never claimed as ground truth. The customer's
exact definitions still arrive via the shipped Definitions.xlsx auto-pin path
(`_autoload_definitions`) or the Definitions tab.

Wiring:
  - `looks_like_crm(slug)`  → detect a CRM-shaped schema.
  - `seed_crm_starter(slug)` → resolve columns + upsert the universal metrics.
Called fail-soft from the train pipeline (auto) and a manual endpoint.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── logical column → candidate real names (case-insensitive, first match wins) ──
_ALIASES: dict[str, list[str]] = {
    "outcome": [
        "call_outcome", "outcome", "call_result", "result", "disposition",
        "related_channel_response__channel_type",  # demo fallthrough not used as outcome; kept low
    ],
    "channel": [
        "channel", "channel_name", "channel_type", "source", "medium",
        "related_channel_response__channel_type", "channel__channel_name",
    ],
    "city": ["city", "region", "location", "area", "territory"],
    "month": ["month", "call_date", "date", "created_at", "call_timestamp", "timestamp"],
}
# outcome candidates that should NOT also be treated as channel etc.
_OUTCOME_ONLY = ["call_outcome", "outcome", "call_result", "result", "disposition"]

# value vocabulary (best-guess; matched case-insensitively via trim+ILIKE-ish '=')
_V_SUCCESS = "Successful"
_V_UNCONTACTABLE = "Uncontactable"


def _resolve_cols(slug: str):
    """Return (schema, crm_tables, colmap) or (schema, [], {}) when not CRM.

    colmap maps logical name → actual column present in the CRM tables.
    crm_tables = tables in the project schema that carry an outcome-like column
    OR whose name looks like crm/call/lead.
    """
    from sqlalchemy import inspect as _sa_inspect  # type: ignore[import]
    from dash.tools.metric_compiler import resolve_engine  # type: ignore[import]

    engine, schema = resolve_engine(slug)
    insp = _sa_inspect(engine)
    try:
        tables = insp.get_table_names(schema=schema)
    except Exception as e:  # noqa: BLE001
        logger.debug("crm_starter: list tables failed for %s: %s", slug, e)
        return schema, [], {}

    # Gather column names per table (lower-cased set) + union.
    table_cols: dict[str, set[str]] = {}
    union_cols: set[str] = set()
    for t in tables:
        try:
            cols = {c["name"].lower() for c in insp.get_columns(t, schema=schema)}
        except Exception:  # noqa: BLE001
            cols = set()
        table_cols[t] = cols
        union_cols |= cols

    def _first(cands: list[str]) -> str | None:
        for c in cands:
            if c.lower() in union_cols:
                # return the real-cased name from any table that has it
                for t, cs in table_cols.items():
                    if c.lower() in cs:
                        for real in (rc["name"] for rc in insp.get_columns(t, schema=schema)):
                            if real.lower() == c.lower():
                                return real
                return c
        return None

    outcome = _first(_OUTCOME_ONLY)
    name_is_crm = any(
        any(tok in t.lower() for tok in ("crm", "call", "lead", "contact"))
        for t in tables
    )
    if outcome is None and not name_is_crm:
        return schema, [], {}

    colmap = {
        "outcome": outcome,
        "channel": _first(_ALIASES["channel"]),
        "city": _first(_ALIASES["city"]),
        "month": _first(_ALIASES["month"]),
    }
    # CRM tables = those carrying the outcome col, else all tables when name-detected.
    if outcome:
        crm_tables = [t for t, cs in table_cols.items() if outcome.lower() in cs]
    else:
        crm_tables = list(tables)
    return schema, crm_tables, colmap


def looks_like_crm(slug: str) -> bool:
    """True if the project's schema looks like a CRM (has an outcome-like column
    or crm/call/lead-named tables). Fail-soft → False."""
    try:
        _schema, crm_tables, colmap = _resolve_cols(slug)
        return bool(crm_tables) and (colmap.get("outcome") is not None or len(crm_tables) > 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("crm_starter.looks_like_crm failed for %s: %s", slug, e)
        return False


def _build_specs(crm_tables: list[str], cm: dict) -> list[dict]:
    """Universal CRM metric specs from the resolved columns. Each metric is
    emitted only when its required columns exist (no fabricated numbers)."""
    out = cm.get("outcome")
    ch = cm.get("channel")
    mo = cm.get("month")
    src = sorted(crm_tables)
    group_dims = [c for c in (mo, ch, cm.get("city"), out) if c]

    def base(name, synonyms, desc, kind, **extra):
        s = {
            "name": name, "synonyms": synonyms, "description": desc, "kind": kind,
            "source_glob": None, "source_tables": src,
            "filters": [], "denom_filters": [], "group_dims": group_dims,
            "default_group": [], "trim_values": True,
            "status": "suggested",  # best-guess vocab — owner confirms
        }
        s.update(extra)
        return s

    specs: list[dict] = []

    # 1. Total call volume — every CRM has rows = calls/records. No columns needed.
    specs.append(base(
        "crm_total_calls", ["total_calls", "call_volume", "total_records"],
        "Total number of CRM call/contact records.", "count",
    ))

    if out:
        # 2. Call success rate
        specs.append(base(
            "crm_success_rate", ["call_success_rate", "success_rate"],
            f"% of calls with {out}='{_V_SUCCESS}'. (vocab is a guess — confirm.)",
            "rate",
            filters=[{"col": out, "op": "=", "value": _V_SUCCESS, "trim": True}],
            denom_filters=[],
        ))
        # 3. Uncontactable rate
        specs.append(base(
            "crm_uncontactable_rate", ["uncontactable_rate", "unreachable_rate"],
            f"% of calls with {out}='{_V_UNCONTACTABLE}'. (vocab is a guess — confirm.)",
            "rate",
            filters=[{"col": out, "op": "=", "value": _V_UNCONTACTABLE, "trim": True}],
            denom_filters=[],
        ))
        # 4. Outcome distribution
        specs.append(base(
            "crm_outcome_distribution", ["outcome_breakdown", "call_outcomes"],
            f"Count of calls grouped by {out}.", "count",
            default_group=[out],
        ))

    if ch:
        # 5. Calls by channel
        specs.append(base(
            "crm_calls_by_channel", ["channel_volume", "calls_per_channel"],
            f"Call volume grouped by {ch}.", "count",
            default_group=[ch],
        ))
        if out:
            # 6. Conversion rate by channel
            specs.append(base(
                "crm_conversion_by_channel", ["channel_conversion", "conversion_rate_by_channel"],
                f"Success rate ({out}='{_V_SUCCESS}') grouped by {ch}. (vocab guess — confirm.)",
                "rate",
                filters=[{"col": out, "op": "=", "value": _V_SUCCESS, "trim": True}],
                denom_filters=[],
                default_group=[ch],
            ))

    return specs


def _columns_used(spec: dict) -> list[str]:
    """Distinct real columns a spec touches (for the preview UI)."""
    cols: list[str] = []
    for f in (spec.get("filters", []) + spec.get("denom_filters", [])):
        if f.get("col") and f["col"] not in cols:
            cols.append(f["col"])
    for c in (spec.get("default_group", []) + spec.get("group_dims", [])):
        if c and c not in cols:
            cols.append(c)
    return cols


def preview_crm_starter(slug: str) -> dict:
    """Resolve CRM columns and return the candidate starter metrics WITHOUT
    saving — so the user can pick which to seed. Fail-soft.

    Returns {ok, project_slug, candidates:[{name, description, kind,
             columns_used, status, already_exists}], columns, tables}.
    """
    try:
        _schema, crm_tables, colmap = _resolve_cols(slug)
        if not crm_tables:
            return {"ok": False, "project_slug": slug, "candidates": [],
                    "skipped_reason": "not_crm_shaped"}
        specs = _build_specs(crm_tables, colmap)
        # Which already exist (so the UI can mark them).
        existing: set[str] = set()
        try:
            from dash.tools.metric_compiler import list_definitions  # type: ignore[import]
            for d in (list_definitions(slug) or []):
                n = d.get("name") if isinstance(d, dict) else None
                if n:
                    existing.add(n)
        except Exception:  # noqa: BLE001
            pass
        candidates = [{
            "name": s["name"],
            "description": s["description"],
            "kind": s["kind"],
            "columns_used": _columns_used(s),
            "status": s.get("status", "suggested"),
            "already_exists": s["name"] in existing,
        } for s in specs]
        return {"ok": True, "project_slug": slug, "candidates": candidates,
                "columns": colmap, "tables": sorted(crm_tables)}
    except Exception as e:  # noqa: BLE001
        logger.warning("crm_starter.preview failed for %s: %s", slug, e)
        return {"ok": False, "project_slug": slug, "candidates": [], "error": str(e)}


def seed_crm_starter(slug: str, only: list[str] | None = None) -> dict:
    """Resolve CRM columns for `slug` and upsert the universal starter metrics.
    If `only` is given, seed just those metric names (the user's selection);
    otherwise seed all resolvable. Idempotent. Fail-soft.

    Returns {ok, project_slug, seeded:[names], skipped_reason?}.
    """
    try:
        schema, crm_tables, colmap = _resolve_cols(slug)
        if not crm_tables:
            return {"ok": False, "project_slug": slug, "seeded": [],
                    "skipped_reason": "not_crm_shaped"}
        specs = _build_specs(crm_tables, colmap)
        if only:
            want = set(only)
            specs = [s for s in specs if s["name"] in want]
        if not specs:
            return {"ok": False, "project_slug": slug, "seeded": [],
                    "skipped_reason": "no_resolvable_metrics"}

        from dash.tools.metric_compiler import save_definition  # type: ignore[import]
        seeded: list[str] = []
        for spec in specs:
            try:
                save_definition(slug, spec, user="seed_crm_starter")
                seeded.append(spec["name"])
            except Exception as e:  # noqa: BLE001
                logger.warning("crm_starter: save %s failed for %s: %s", spec["name"], slug, e)
        logger.info("crm_starter: seeded %d/%d metrics for %s", len(seeded), len(specs), slug)
        return {"ok": True, "project_slug": slug, "seeded": seeded,
                "columns": colmap, "tables": sorted(crm_tables)}
    except Exception as e:  # noqa: BLE001
        logger.warning("crm_starter.seed_crm_starter failed for %s: %s", slug, e)
        return {"ok": False, "project_slug": slug, "seeded": [], "error": str(e)}
