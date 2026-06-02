"""
Venture tools — pure-math DCF / IRR / MOIC / sensitivity + persistence.

Project-scoped via project_slug. All math is deterministic, numpy-only,
unit-testable in isolation. Persistence uses dash.settings.get_write_engine()
to keep platform writes guarded (CLAUDE.md rule: dash_* writes need
get_write_engine, not get_sql_engine).
"""
from __future__ import annotations

import json
import logging
import math
from typing import List, Optional

import numpy as np
from sqlalchemy import text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- math --

def dcf(cashflows: List[float], wacc: float, terminal_growth: float = 0.0,
        terminal_method: str = "gordon",
        terminal_cashflow: Optional[float] = None) -> dict:
    """
    Discounted cash flow valuation.

    cashflows[0] = year 0 (typically negative = ask), cashflows[1..n] = forecast FCF.
    wacc + terminal_growth as decimals (0.12 not 12). Gordon terminal applied at
    end of horizon. If terminal_cashflow is provided, Gordon TV is computed from
    that normalized cashflow instead of cashflows[-1] (use when the last forecast
    cashflow includes a one-time exit).

    Returns: {npv, pv_cashflows[], terminal_value, total_pv}
    """
    if not cashflows or len(cashflows) < 2:
        return {"ok": False, "error": "need >=2 cashflows (year 0 + >=1 forecast)"}
    if wacc <= 0:
        return {"ok": False, "error": "wacc must be > 0"}
    if terminal_growth >= wacc:
        return {"ok": False, "error": "terminal_growth must be < wacc"}

    cf = np.array(cashflows, dtype=float)
    years = np.arange(len(cf))
    discount = (1 + wacc) ** years
    pv = cf / discount

    tv = 0.0
    tv_pv = 0.0
    if terminal_method == "gordon":
        tv_base = terminal_cashflow if terminal_cashflow is not None else cf[-1]
        tv = (tv_base * (1 + terminal_growth)) / (wacc - terminal_growth)
        tv_pv = tv / ((1 + wacc) ** (len(cf) - 1))

    # When terminal_cashflow is explicit, the LAST cashflow typically already
    # bakes in an exit; TV is reported separately and NOT added to NPV
    # (matches use-case doc convention).
    npv_value = float(pv.sum() + (tv_pv if terminal_cashflow is None else 0.0))

    return {
        "ok": True,
        "npv": npv_value,
        "pv_cashflows": pv.tolist(),
        "terminal_value": float(tv),
        "terminal_pv": float(tv_pv),
        "wacc": wacc,
        "terminal_growth": terminal_growth,
    }


def irr_moic(cashflows: List[float], invested: Optional[float] = None) -> dict:
    """
    IRR (Newton-Raphson via numpy) + MOIC.

    cashflows[0] = initial investment (negative). Subsequent = distributions.
    If `invested` kwarg is provided, MOIC = sum(positives) / invested. Otherwise
    MOIC = sum(positives) / abs(sum(negatives)).
    Returns {irr, moic, payback_yrs, total_invested, total_returned}.
    """
    if not cashflows or len(cashflows) < 2:
        return {"ok": False, "error": "need >=2 cashflows"}

    cf = np.array(cashflows, dtype=float)
    invested_auto = float(-cf[cf < 0].sum())
    returned = float(cf[cf > 0].sum())

    if invested_auto <= 0:
        return {"ok": False, "error": "no negative cashflows (no investment)"}

    if invested is not None and invested > 0:
        moic = returned / float(invested)
        invested_reported = float(invested)
    else:
        moic = returned / invested_auto if invested_auto > 0 else 0.0
        invested_reported = invested_auto

    # IRR via Newton-Raphson
    irr = _solve_irr(cf)

    # Payback (years until cumulative >= 0)
    cum = np.cumsum(cf)
    payback = None
    for i, c in enumerate(cum):
        if c >= 0:
            payback = float(i)
            break

    return {
        "ok": True,
        "irr": irr,
        "moic": float(moic),
        "payback_yrs": payback,
        "total_invested": float(invested_reported),
        "total_returned": float(returned),
    }


def _solve_irr(cf: np.ndarray, guess: float = 0.1, max_iter: int = 200,
               tol: float = 1e-7) -> Optional[float]:
    """Newton-Raphson IRR. Returns None if no convergence."""
    r = guess
    for _ in range(max_iter):
        years = np.arange(len(cf))
        denom = (1 + r) ** years
        npv = (cf / denom).sum()
        # derivative
        deriv = (-years * cf / ((1 + r) ** (years + 1))).sum()
        if abs(deriv) < 1e-12:
            return None
        r_new = r - npv / deriv
        if abs(r_new - r) < tol:
            return float(r_new)
        r = r_new
        if r < -0.99:
            r = -0.99  # clamp
    return None


def sensitivity_grid(base_cashflows: List[float], wacc_range: List[float],
                     growth_range: List[float]) -> dict:
    """
    2D NPV grid varying wacc + terminal_growth.

    wacc_range, growth_range = decimals (0.08, 0.10, 0.12 ...).
    Returns {grid[][], wacc_axis[], growth_axis[]} where grid[i][j] = NPV at
    wacc_range[i], growth_range[j].
    """
    if not wacc_range or not growth_range:
        return {"ok": False, "error": "empty ranges"}

    grid = []
    for w in wacc_range:
        row = []
        for g in growth_range:
            if g >= w:
                row.append(None)  # invalid combo
                continue
            r = dcf(base_cashflows, w, g)
            row.append(r.get("npv") if r.get("ok") else None)
        grid.append(row)

    return {
        "ok": True,
        "wacc_axis": list(wacc_range),
        "growth_axis": list(growth_range),
        "grid": grid,
    }


def unit_economics(cac: float, ltv: float, gross_margin: float = 1.0,
                   payback_months: Optional[float] = None) -> dict:
    """
    LTV/CAC ratio + payback. gross_margin as decimal.

    Returns BOTH raw `ltv_cac` = ltv / cac AND `effective_ltv_cac` =
    (ltv * gross_margin) / cac. Flag is computed against effective ratio.
    """
    if cac <= 0:
        return {"ok": False, "error": "cac must be > 0"}
    raw_ratio = ltv / cac
    effective_ratio = (ltv * gross_margin) / cac
    flag = (
        "healthy" if effective_ratio >= 3
        else ("marginal" if effective_ratio >= 1.5 else "unhealthy")
    )
    return {
        "ok": True,
        "ltv_cac": float(raw_ratio),
        "effective_ltv_cac": float(effective_ratio),
        "flag": flag,
        "payback_months": payback_months,
        "gross_margin": gross_margin,
    }


def load_capability_weights(project_slug: str) -> dict[str, float]:
    """
    Load per-tenant capability importance weights from Company Brain glossary.

    Queries public.dash_company_brain for rows where category='capability_weight'
    and (project_slug=:p OR project_slug IS NULL). Project-scoped rows override
    global rows on same name.

    Returns {capability_lowercase: weight (0-1)}. Fail-soft → {} on any DB error.
    """
    try:
        eng = _engine()
        with eng.connect() as cx:
            # Global first, then project — later writes override earlier
            rows = cx.execute(text("""
                SELECT name, metadata, project_slug
                FROM public.dash_company_brain
                WHERE category = 'capability_weight'
                  AND (project_slug = :p OR project_slug IS NULL)
                ORDER BY (project_slug IS NULL) DESC, project_slug NULLS FIRST
            """), {"p": project_slug}).fetchall()

        weights: dict[str, float] = {}
        for r in rows:
            name = (r[0] or "").lower().strip()
            meta = r[1] or {}
            if not name:
                continue
            try:
                w = float(meta.get("weight")) if isinstance(meta, dict) else None
            except (TypeError, ValueError):
                w = None
            if w is None:
                continue
            # Clamp 0-1
            w = max(0.0, min(1.0, w))
            weights[name] = w  # project rows come last, override global
        return weights
    except Exception:
        logger.exception("load_capability_weights failed")
        return {}


def partner_fit_score(self_caps, partner_caps,
                      weights: Optional[dict] = None,
                      project_slug: Optional[str] = None) -> dict:
    """
    Complementarity score. Two modes:

    - list[str] x list[str] → returns 0..100 `fit_score` (legacy behavior).
    - dict[str, float] x dict[str, float] (cap scores 0..1 each) → returns
      0..1 `score` computed as weighted complement on the shared 0..1 cap
      scale. Output keys: {score, overlap, complement, gaps}.

    weights = {capability: importance 0-1}, optional (used in list mode and
    can override defaults in dict mode).
    """
    if not self_caps or not partner_caps:
        return {"ok": False, "error": "empty capability lists"}

    # ---- dict / dict overload --------------------------------------------
    if isinstance(self_caps, dict) and isinstance(partner_caps, dict):
        s_map = {k.lower().strip(): float(v) for k, v in self_caps.items()}
        p_map = {k.lower().strip(): float(v) for k, v in partner_caps.items()}

        shared = sorted(set(s_map) & set(p_map))
        partner_only = sorted(set(p_map) - set(s_map))
        # gaps = capabilities we (self) have but partner is weak/missing on
        gaps = sorted([k for k in s_map if p_map.get(k, 0.0) < s_map[k]])

        # Optional importance weights (user-supplied overrides defaults of 1.0)
        importance: dict = {}
        if project_slug:
            importance.update(load_capability_weights(project_slug))
        if weights:
            importance.update({k.lower().strip(): float(v) for k, v in weights.items()})

        # Complement strength = partner's score on caps where self is weak.
        # For each cap in union, contribution = max(0, p - s) * importance.
        # Normalize by sum of importance over union → score in [0, 1].
        union = sorted(set(s_map) | set(p_map))
        total_w = 0.0
        gained = 0.0
        for cap in union:
            w = importance.get(cap, 1.0)
            total_w += w
            s_v = s_map.get(cap, 0.0)
            p_v = p_map.get(cap, 0.0)
            # Reward partner filling our gap; clamp negatives to 0
            gained += max(0.0, p_v - s_v) * w

        score = (gained / total_w) if total_w > 0 else 0.0
        score = max(0.0, min(1.0, score))

        return {
            "ok": True,
            "score": float(score),
            "overlap": shared,
            "complement": partner_only,
            "gaps": gaps,
        }

    # ---- list / list legacy path -----------------------------------------
    self_set = {c.lower().strip() for c in self_caps}
    partner_set = {c.lower().strip() for c in partner_caps}

    overlap = self_set & partner_set
    complement = partner_set - self_set
    gaps_filled = len(complement) / max(len(self_set), 1)

    # Merge brain weights w/ user-supplied (user wins)
    merged_weights: Optional[dict] = None
    if project_slug:
        brain_w = load_capability_weights(project_slug)
        if brain_w or weights:
            merged_weights = dict(brain_w)
            if weights:
                merged_weights.update({k.lower().strip(): v for k, v in weights.items()})
    elif weights:
        merged_weights = {k.lower().strip(): v for k, v in weights.items()}

    if merged_weights:
        weighted = sum(merged_weights.get(c, 0.5) for c in complement)
        score = min(100, weighted * 100 / max(len(self_set), 1))
    else:
        score = min(100, gaps_filled * 100)

    return {
        "ok": True,
        "fit_score": float(score),
        "overlap": sorted(overlap),
        "complement": sorted(complement),
        "gaps_filled_pct": float(gaps_filled * 100),
        "weights_source": (
            "brain+user" if (merged_weights and project_slug and weights)
            else "brain" if (merged_weights and project_slug)
            else "user" if merged_weights
            else "unweighted"
        ),
    }


# -------------------------------------------------------- persistence --

def _engine():
    """Use write engine for dash.* tables (CLAUDE.md gotcha)."""
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        from db.session import get_sql_engine
        return get_sql_engine()


def save_deal(project_slug: str, name: str, stage: str = "screening",
              sector: str = "", geography: str = "", ask_amount: float = 0.0,
              created_by: Optional[int] = None) -> dict:
    """Persist a deal row. Returns {ok, deal_id}."""
    try:
        eng = _engine()
        with eng.begin() as cx:
            row = cx.execute(text("""
                INSERT INTO dash.dash_venture_deals
                    (project_slug, name, stage, sector, geography, ask_amount, created_by)
                VALUES (:p, :n, :st, :sec, :geo, :ask, :u)
                RETURNING id
            """), {"p": project_slug, "n": name, "st": stage, "sec": sector,
                   "geo": geography, "ask": ask_amount, "u": created_by}).fetchone()
        return {"ok": True, "deal_id": str(row[0])}
    except Exception as e:
        logger.exception("save_deal failed")
        return {"ok": False, "error": str(e)}


def save_scenario(deal_id: str, name: str, inputs: dict, results: dict,
                  verdict: str = "hold") -> dict:
    """Persist a scenario row. results = output of dcf/irr_moic."""
    try:
        eng = _engine()
        with eng.begin() as cx:
            row = cx.execute(text("""
                INSERT INTO dash.dash_venture_scenarios
                    (deal_id, name, irr, moic, payback_yrs, npv, inputs, verdict)
                VALUES (:d, :n, :irr, :moic, :p, :npv, CAST(:i AS jsonb), :v)
                RETURNING id
            """), {
                "d": deal_id, "n": name,
                "irr": results.get("irr"), "moic": results.get("moic"),
                "p": results.get("payback_yrs"), "npv": results.get("npv"),
                "i": json.dumps(inputs), "v": verdict,
            }).fetchone()
        return {"ok": True, "scenario_id": str(row[0])}
    except Exception as e:
        logger.exception("save_scenario failed")
        return {"ok": False, "error": str(e)}


def seed_capability_weights(project_slug: str, weights: dict[str, float]) -> dict:
    """
    Upsert capability importance weights into public.dash_company_brain w/
    category='capability_weight'. Per-tenant (project_slug-scoped).
    PgBouncer-safe: uses CAST(:x AS jsonb).

    Returns {ok, inserted} or {ok: False, error}.
    """
    if not weights:
        return {"ok": False, "error": "no weights provided"}

    try:
        from db.session import get_write_engine
        eng = get_write_engine()
    except Exception as e:
        logger.exception("seed_capability_weights: get_write_engine failed")
        return {"ok": False, "error": f"engine init: {e}"}

    inserted = 0
    try:
        with eng.begin() as cx:
            for name, w in weights.items():
                cname = (name or "").lower().strip()
                if not cname:
                    continue
                try:
                    wv = float(w)
                except (TypeError, ValueError):
                    continue
                wv = max(0.0, min(1.0, wv))
                meta = json.dumps({"weight": wv})

                # Try ON CONFLICT first (if unique idx exists on (project_slug, name, category))
                try:
                    cx.execute(text("""
                        INSERT INTO public.dash_company_brain
                            (project_slug, name, category, definition, metadata)
                        VALUES (:p, :n, 'capability_weight',
                                'Auto-seeded capability weight',
                                CAST(:m AS jsonb))
                        ON CONFLICT (project_slug, name, category)
                        DO UPDATE SET metadata = CAST(:m AS jsonb),
                                      definition = EXCLUDED.definition
                    """), {"p": project_slug, "n": cname, "m": meta})
                    inserted += 1
                except Exception:
                    # No matching unique constraint — fall back to manual check
                    existing = cx.execute(text("""
                        SELECT id FROM public.dash_company_brain
                        WHERE project_slug = :p AND name = :n
                          AND category = 'capability_weight'
                        LIMIT 1
                    """), {"p": project_slug, "n": cname}).fetchone()
                    if existing:
                        cx.execute(text("""
                            UPDATE public.dash_company_brain
                            SET metadata = CAST(:m AS jsonb),
                                definition = 'Auto-seeded capability weight'
                            WHERE id = :id
                        """), {"m": meta, "id": existing[0]})
                    else:
                        cx.execute(text("""
                            INSERT INTO public.dash_company_brain
                                (project_slug, name, category, definition, metadata)
                            VALUES (:p, :n, 'capability_weight',
                                    'Auto-seeded capability weight',
                                    CAST(:m AS jsonb))
                        """), {"p": project_slug, "n": cname, "m": meta})
                    inserted += 1
        return {"ok": True, "inserted": inserted}
    except Exception as e:
        logger.exception("seed_capability_weights failed")
        return {"ok": False, "error": str(e)}


def list_deals(project_slug: str, status: Optional[str] = None) -> dict:
    """List deals for a project, optionally filtered by status."""
    try:
        eng = _engine()
        with eng.connect() as cx:
            if status:
                rows = cx.execute(text("""
                    SELECT id, name, stage, sector, geography, ask_amount, status, created_at
                    FROM dash.dash_venture_deals
                    WHERE project_slug = :p AND status = :s
                    ORDER BY created_at DESC
                    LIMIT 100
                """), {"p": project_slug, "s": status}).fetchall()
            else:
                rows = cx.execute(text("""
                    SELECT id, name, stage, sector, geography, ask_amount, status, created_at
                    FROM dash.dash_venture_deals
                    WHERE project_slug = :p
                    ORDER BY created_at DESC
                    LIMIT 100
                """), {"p": project_slug}).fetchall()
        return {
            "ok": True,
            "deals": [
                {
                    "id": str(r[0]), "name": r[1], "stage": r[2], "sector": r[3],
                    "geography": r[4], "ask_amount": float(r[5]) if r[5] else None,
                    "status": r[6], "created_at": r[7].isoformat() if r[7] else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        logger.exception("list_deals failed")
        return {"ok": False, "error": str(e)}


# ----------------------------------------------------------- @tools --

def create_venture_tools(project_slug: str, user_id: Optional[int] = None):
    """Return list of Agno @tools for the deal_analyst agent."""
    from agno.tools import tool

    @tool(name="dcf", description="Discounted cash flow valuation. Args: cashflows (list[float], year 0 first, negative for investment), wacc (float decimal e.g. 0.12), terminal_growth (float decimal e.g. 0.03). Returns NPV + PV breakdown + terminal value.")
    def dcf_tool(cashflows: List[float], wacc: float, terminal_growth: float = 0.03) -> str:
        r = dcf(cashflows, wacc, terminal_growth)
        return json.dumps(r, default=str)

    @tool(name="irr_moic", description="IRR + MOIC + payback. Args: cashflows (list[float], year 0 negative = invested, subsequent = distributions). Returns IRR, MOIC, payback years.")
    def irr_tool(cashflows: List[float]) -> str:
        r = irr_moic(cashflows)
        return json.dumps(r, default=str)

    @tool(name="sensitivity_grid", description="2D NPV grid varying WACC + terminal growth. Args: base_cashflows (list[float]), wacc_range (list[float], e.g. [0.08, 0.10, 0.12]), growth_range (list[float], e.g. [0.02, 0.03, 0.04]). Returns grid for heatmap render.")
    def sens_tool(base_cashflows: List[float], wacc_range: List[float], growth_range: List[float]) -> str:
        r = sensitivity_grid(base_cashflows, wacc_range, growth_range)
        return json.dumps(r, default=str)

    @tool(name="unit_economics", description="LTV/CAC ratio + payback. Args: cac (float), ltv (float), gross_margin (float decimal, default 1.0), payback_months (float optional). Returns ratio + health flag.")
    def ue_tool(cac: float, ltv: float, gross_margin: float = 1.0,
                payback_months: Optional[float] = None) -> str:
        r = unit_economics(cac, ltv, gross_margin, payback_months)
        return json.dumps(r, default=str)

    @tool(name="partner_fit_score", description="Score complementarity 0-100 between two capability lists. Args: self_caps (list[str]), partner_caps (list[str]). Higher = better fit (more capability gaps filled). Uses per-tenant capability weights from Company Brain when available.")
    def fit_tool(self_caps: List[str], partner_caps: List[str]) -> str:
        r = partner_fit_score(self_caps, partner_caps, weights=None, project_slug=project_slug)
        return json.dumps(r, default=str)

    @tool(name="seed_capability_weights", description="Seed capability importance weights for partner-fit scoring. Args: weights (dict[str, float] mapping capability name to importance 0-1).")
    def seed_caps_tool(weights: dict) -> str:
        r = seed_capability_weights(project_slug, weights)
        return json.dumps(r, default=str)

    @tool(name="save_deal", description="Persist a deal row in this project's venture pipeline. Args: name (str), stage (str: seed/series_a/series_b/late), sector (str), geography (str), ask_amount (float). Returns deal_id.")
    def save_deal_tool(name: str, stage: str = "screening", sector: str = "",
                       geography: str = "", ask_amount: float = 0.0) -> str:
        r = save_deal(project_slug, name, stage, sector, geography, ask_amount, user_id)
        return json.dumps(r, default=str)

    @tool(name="save_scenario", description="Save a scenario (base/upside/downside) for a deal. Args: deal_id (str uuid), name (str), inputs (dict), results (dict from dcf or irr_moic), verdict (str: go/hold/pass).")
    def save_scen_tool(deal_id: str, name: str, inputs: dict, results: dict,
                       verdict: str = "hold") -> str:
        r = save_scenario(deal_id, name, inputs, results, verdict)
        return json.dumps(r, default=str)

    @tool(name="list_deals", description="List deals for this project. Args: status (str optional: screening/diligence/ic/shortlist/pass/closed).")
    def list_tool(status: str = "") -> str:
        r = list_deals(project_slug, status if status else None)
        return json.dumps(r, default=str)

    return [dcf_tool, irr_tool, sens_tool, ue_tool, fit_tool, seed_caps_tool,
            save_deal_tool, save_scen_tool, list_tool]
