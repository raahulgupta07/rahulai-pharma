"""Venture Rescore Daemon.

Monthly async loop that re-runs DCF + IRR on every saved scenario across every
active venture deal and detects verdict drift. Fail-soft per scenario/project —
one bad row never kills a cycle.

Patterns lifted from `dash/cron/auto_campaign_daemon.py`:
  - master env gate (`VENTURE_RESCORE_DAEMON_DISABLED=1`)
  - tunable interval (`VENTURE_RESCORE_INTERVAL_SECONDS`, default 30d, floor 3600s)
  - trace_span best-effort import (no-op if tracing disabled)
  - public API: `run_once()` (single cycle) + `venture_rescore_loop()` (forever)
  - caller is responsible for `WORKER_RANK=0` gating; we only log it.

Drift is INSERTed into `dash.dash_venture_verdict_drift` (created on first run
by `_ensure_table()`) and surfaced as a `warn`-severity row in
`dash_proactive_insights` (best-effort).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Observability (fail-soft) ─────────────────────────────────────────
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None


# ── Tunables ──────────────────────────────────────────────────────────

DEFAULT_INTERVAL_SECONDS = 30 * 24 * 60 * 60  # 30 days
MIN_INTERVAL_SECONDS = 3600  # floor

# Verdict thresholds (same as venture_tools.dcf_irr scoring).
IRR_GO = 0.25
MOIC_GO = 3.0
IRR_HOLD = 0.15

_DRIFT_TABLE_ENSURED = False


# ── Engines (lazy, fail-soft) ─────────────────────────────────────────

def _sql_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        logger.exception("venture_rescore: get_sql_engine failed")
        return None


def _write_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        logger.exception("venture_rescore: get_write_engine failed")
        return None


# ── Drift table bootstrap ─────────────────────────────────────────────

_CREATE_DRIFT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dash.dash_venture_verdict_drift (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_id UUID NOT NULL,
    deal_id UUID NOT NULL,
    project_slug TEXT NOT NULL,
    old_verdict TEXT,
    new_verdict TEXT,
    old_irr NUMERIC,
    new_irr NUMERIC,
    old_npv NUMERIC,
    new_npv NUMERIC,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_venture_drift_proj
    ON dash.dash_venture_verdict_drift (project_slug, detected_at DESC);
"""


def _ensure_table() -> bool:
    """Idempotent create. Returns True on success or already-exists, False on
    failure (caller skips INSERTs that cycle)."""
    global _DRIFT_TABLE_ENSURED
    if _DRIFT_TABLE_ENSURED:
        return True
    eng = _write_engine()
    if eng is None:
        return False
    try:
        from sqlalchemy import text as _t
        with eng.begin() as cn:
            cn.execute(_t(_CREATE_DRIFT_TABLE_SQL))
        _DRIFT_TABLE_ENSURED = True
        return True
    except Exception:
        logger.exception("venture_rescore: _ensure_table failed")
        return False


# ── Verdict logic ─────────────────────────────────────────────────────

def _classify_verdict(irr: float | None, moic: float | None) -> str:
    """irr ≥ 0.25 AND moic ≥ 3.0 → 'go'; irr ≥ 0.15 → 'hold'; else 'pass'."""
    try:
        i = float(irr) if irr is not None else None
        m = float(moic) if moic is not None else None
    except Exception:
        return "pass"
    if i is None:
        return "pass"
    if i >= IRR_GO and (m is not None and m >= MOIC_GO):
        return "go"
    if i >= IRR_HOLD:
        return "hold"
    return "pass"


# ── Helpers ───────────────────────────────────────────────────────────

def _safe_json(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _list_active_projects(cn) -> list[str]:
    """Distinct project_slug from venture_deals where status NOT IN
    ('closed', 'pass')."""
    from sqlalchemy import text as _t
    rows = cn.execute(_t(
        "SELECT DISTINCT project_slug FROM dash.dash_venture_deals "
        " WHERE status IS NULL OR status NOT IN ('closed', 'pass')"
    )).fetchall()
    return [r[0] for r in rows if r and r[0]]


def _list_scenarios_for_project(cn, slug: str) -> list[dict[str, Any]]:
    """Scenarios w/ cashflows under inputs, ≤180 days old, active deals."""
    from sqlalchemy import text as _t
    rows = cn.execute(_t(
        """
        SELECT s.id, s.deal_id, s.name, s.irr, s.moic, s.npv, s.payback_yrs,
               s.inputs, s.verdict, d.name AS deal_name
        FROM dash.dash_venture_scenarios s
        JOIN dash.dash_venture_deals d ON s.deal_id = d.id
        WHERE d.project_slug = :p
          AND s.inputs ? 'cashflows'
          AND s.created_at > now() - interval '180 days'
        """
    ), {"p": slug}).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            out.append({
                "id": r[0],
                "deal_id": r[1],
                "name": r[2],
                "irr": r[3],
                "moic": r[4],
                "npv": r[5],
                "payback_yrs": r[6],
                "inputs": _safe_json(r[7]),
                "verdict": r[8],
                "deal_name": r[9],
            })
        except Exception:
            logger.debug("venture_rescore: skip malformed scenario row",
                         exc_info=True)
    return out


def _rerun_metrics(inputs: dict) -> dict[str, Any]:
    """Call venture_tools.irr_moic + dcf using inputs.cashflows + inputs.wacc
    + inputs.terminal_growth. Returns {ok, irr, moic, npv, payback_yrs} or
    {ok: False, error}."""
    try:
        from dash.tools.venture_tools import irr_moic, dcf
    except Exception:
        logger.exception("venture_rescore: venture_tools import failed")
        return {"ok": False, "error": "tools_import"}

    cashflows = inputs.get("cashflows")
    if not isinstance(cashflows, list) or len(cashflows) < 2:
        return {"ok": False, "error": "bad_cashflows"}

    wacc = inputs.get("wacc", 0.12)
    tg = inputs.get("terminal_growth", 0.03)

    try:
        im = irr_moic(cashflows) or {}
    except Exception:
        logger.debug("venture_rescore: irr_moic crashed", exc_info=True)
        im = {}

    try:
        dc = dcf(cashflows, float(wacc), float(tg)) or {}
    except Exception:
        logger.debug("venture_rescore: dcf crashed", exc_info=True)
        dc = {}

    if not im.get("ok") and not dc.get("ok"):
        return {"ok": False, "error": "both_calcs_failed"}

    return {
        "ok": True,
        "irr": im.get("irr"),
        "moic": im.get("moic"),
        "payback_yrs": im.get("payback_yrs"),
        "npv": dc.get("npv"),
    }


# ── Drift persistence ─────────────────────────────────────────────────

def _insert_drift(cn, scenario: dict, new_metrics: dict, slug: str,
                  new_verdict: str) -> bool:
    from sqlalchemy import text as _t
    try:
        cn.execute(_t(
            """
            INSERT INTO dash.dash_venture_verdict_drift
                (scenario_id, deal_id, project_slug,
                 old_verdict, new_verdict,
                 old_irr, new_irr, old_npv, new_npv)
            VALUES (:sid, :did, :p,
                    :ov, :nv,
                    :oirr, :nirr, :onpv, :nnpv)
            """
        ), {
            "sid": scenario.get("id"),
            "did": scenario.get("deal_id"),
            "p": slug,
            "ov": scenario.get("verdict"),
            "nv": new_verdict,
            "oirr": scenario.get("irr"),
            "nirr": new_metrics.get("irr"),
            "onpv": scenario.get("npv"),
            "nnpv": new_metrics.get("npv"),
        })
        return True
    except Exception:
        logger.exception("venture_rescore: drift insert failed sid=%s",
                         scenario.get("id"))
        return False


def _emit_insight(cn, slug: str, scenario: dict, new_verdict: str) -> None:
    """Best-effort proactive_insights row. Multiple schema variants exist
    across deployments — try the common one, swallow failure."""
    from sqlalchemy import text as _t
    deal_name = scenario.get("deal_name") or "deal"
    old_v = scenario.get("verdict") or "—"
    msg = (
        f"Verdict drift on '{deal_name}': "
        f"{old_v} → {new_verdict}. Review scenario before next IC."
    )
    try:
        cn.execute(_t(
            "INSERT INTO public.dash_proactive_insights "
            "  (project_slug, user_id, insight, severity, tables_involved) "
            "VALUES (:s, NULL, :ins, 'warn', :tbl)"
        ), {
            "s": slug,
            "ins": msg,
            "tbl": ["dash_venture_scenarios", "dash_venture_deals"],
        })
        return
    except Exception:
        logger.debug("venture_rescore: insight insert (variant 1) skipped",
                     exc_info=True)

    # Schema variant w/ title column.
    try:
        cn.execute(_t(
            "INSERT INTO public.dash_proactive_insights "
            "  (project_slug, title, insight, severity) "
            "VALUES (:s, :t, :ins, 'warn')"
        ), {
            "s": slug,
            "t": f"Verdict drift: {deal_name}",
            "ins": msg,
        })
    except Exception:
        logger.debug("venture_rescore: insight insert (variant 2) skipped",
                     exc_info=True)


# ── Public API ────────────────────────────────────────────────────────


async def run_once() -> dict[str, Any]:
    """One full cycle across every active project. Returns counts + errors.

    Fail-soft everywhere: a crash on one project/scenario never aborts the
    cycle. Caller may await this; internal DB calls are sync so we offload
    to `asyncio.to_thread`.
    """
    out: dict[str, Any] = {
        "ok": True,
        "projects_scanned": 0,
        "scenarios_rescored": 0,
        "drift_detected": 0,
        "errors": [],
    }

    if not _ensure_table():
        out["ok"] = False
        out["errors"].append("ensure_table_failed")
        return out

    sql_eng = _sql_engine()
    write_eng = _write_engine()
    if sql_eng is None or write_eng is None:
        out["ok"] = False
        out["errors"].append("no_engine")
        return out

    def _project_list_sync() -> list[str]:
        try:
            with sql_eng.connect() as cn:
                return _list_active_projects(cn)
        except Exception:
            logger.exception("venture_rescore: project listing failed")
            return []

    slugs = await asyncio.to_thread(_project_list_sync)

    for slug in slugs:
        try:
            res = await asyncio.to_thread(_run_for_project, slug,
                                          sql_eng, write_eng)
        except Exception as exc:  # noqa: BLE001
            logger.exception("venture_rescore: project cycle crashed slug=%s",
                             slug)
            out["errors"].append({"slug": slug, "error": str(exc)})
            continue
        out["projects_scanned"] += 1
        out["scenarios_rescored"] += int(res.get("scenarios_rescored") or 0)
        out["drift_detected"] += int(res.get("drift_detected") or 0)

    logger.info(
        "venture_rescore: cycle_done projects=%d rescored=%d drift=%d errors=%d",
        out["projects_scanned"], out["scenarios_rescored"],
        out["drift_detected"], len(out["errors"]),
    )
    return out


def _run_for_project(slug: str, sql_eng, write_eng) -> dict[str, Any]:
    """Sync per-project work. Reads via sql_eng, writes via write_eng."""
    res = {"slug": slug, "scenarios_rescored": 0, "drift_detected": 0}

    try:
        with sql_eng.connect() as cn:
            scenarios = _list_scenarios_for_project(cn, slug)
    except Exception:
        logger.exception("venture_rescore: scenario list failed slug=%s", slug)
        return res

    for sc in scenarios:
        try:
            metrics = _rerun_metrics(sc.get("inputs") or {})
        except Exception:
            logger.debug("venture_rescore: rerun crashed sid=%s",
                         sc.get("id"), exc_info=True)
            continue

        if not metrics.get("ok"):
            continue

        res["scenarios_rescored"] += 1
        new_verdict = _classify_verdict(metrics.get("irr"), metrics.get("moic"))
        old_verdict = (sc.get("verdict") or "").lower() or None

        if not new_verdict or new_verdict == old_verdict:
            continue

        # Drift! Persist + emit insight in its own short txn.
        try:
            with write_eng.begin() as wcn:
                if _insert_drift(wcn, sc, metrics, slug, new_verdict):
                    res["drift_detected"] += 1
                    _emit_insight(wcn, slug, sc, new_verdict)
        except Exception:
            logger.exception("venture_rescore: drift txn failed sid=%s",
                             sc.get("id"))

    return res


# ── Async loop ────────────────────────────────────────────────────────


def _interval_seconds() -> int:
    raw = os.getenv("VENTURE_RESCORE_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
    except Exception:
        v = DEFAULT_INTERVAL_SECONDS
    if v < MIN_INTERVAL_SECONDS:
        v = MIN_INTERVAL_SECONDS
    return v


def _is_disabled() -> bool:
    return (os.getenv("VENTURE_RESCORE_DAEMON_DISABLED", "").lower()
            in ("1", "true", "yes"))


async def venture_rescore_loop() -> None:
    """Forever loop. Crash-resistant: per-iteration try/except. Sleeps
    INTERVAL between cycles."""
    if _is_disabled():
        logger.info("venture_rescore: disabled via env")
        return

    worker_rank = os.getenv("WORKER_RANK", "?")
    interval = _interval_seconds()
    logger.info("venture_rescore: starting (worker_rank=%s interval=%ds)",
                worker_rank, interval)

    while True:
        try:
            with trace_span("cron.venture_rescore", kind="cron"):
                await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("venture_rescore: cycle crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_once", "venture_rescore_loop"]
