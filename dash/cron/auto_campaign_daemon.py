"""Auto-Campaign Daemon (Tier 4).

Periodically samples each active project's RFM segment distribution +
churn-risk distribution, compares against the previous cycle's snapshot,
and *drafts* campaigns when configured trigger rules fire. Drafts land in
``dash_campaigns`` with ``status='draft'`` + ``type='auto'`` + a
``metadata.reasoning`` blob the UI surfaces in an APPROVE & LAUNCH /
EDIT / DISMISS panel.

Trigger rules (initial set, easy to extend):

* ``champions_drop``       — Champions count fell ≥15% vs prior snapshot
* ``at_risk_surge``        — At Risk count rose ≥20% vs prior snapshot
* ``hibernating_overflow`` — Hibernating > 25% of total customer base
* ``new_spike``            — New Customers count rose ≥30% vs prior snapshot

Each fired rule:

* drafts a single campaign (``status='draft'``, ``type='auto'``,
  ``created_by='auto_campaign_daemon'``);
* records ``metadata.rule``, ``metadata.reasoning`` (detected_change +
  suggested_discount + suggested_audience + expected_revenue_lift);
* logs a ``dash_campaign_events`` row of type ``auto_drafted``;
* surfaces in chat via ``dash_proactive_insights`` with a 🤖 AUTO badge
  prefix on the message.

Operational guarantees:

* **Cooldown.** A given (project, rule) pair will not refire within 7d,
  enforced via a SELECT on existing auto-drafts before insert.
* **Cap.** ≤ ``MAX_DRAFTS_PER_PROJECT_PER_CYCLE`` per project per cycle.
* **Per-project disable** via ``feature_config.tools.auto_campaign_daemon``.
* **Global disable** via ``AUTO_CAMPAIGN_DAEMON_DISABLED=1``.
* **Multi-worker safe.** Cooldown query (rule + 7d window) means losing
  workers see the row already and skip — no claim table needed at 24h
  cadence.
* **Idempotent.** Snapshot insert is unconditional; cooldown blocks
  duplicate drafts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None


# ── Tunables ──────────────────────────────────────────────────────────

DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60  # 24h
COOLDOWN_DAYS = 7
MAX_DRAFTS_PER_PROJECT_PER_CYCLE = 5

# Heuristic — tweakable via env without redeploy.
CHAMPIONS_DROP_PCT = float(os.getenv("AUTO_CAMPAIGN_CHAMPIONS_DROP_PCT", "0.15"))
AT_RISK_SURGE_PCT = float(os.getenv("AUTO_CAMPAIGN_AT_RISK_SURGE_PCT", "0.20"))
HIBERNATING_SHARE = float(os.getenv("AUTO_CAMPAIGN_HIBERNATING_SHARE", "0.25"))
NEW_SPIKE_PCT = float(os.getenv("AUTO_CAMPAIGN_NEW_SPIKE_PCT", "0.30"))

# Conversion heuristic for expected_revenue_lift.
ASSUMED_CONVERSION = 0.30


# ── Helpers ───────────────────────────────────────────────────────────


def _engine():
    """Resolve the shared SQL engine, or None if unavailable."""
    try:
        from db.session import get_sql_engine
    except Exception:  # pragma: no cover
        from db import get_sql_engine  # type: ignore[no-redef]
    return get_sql_engine()


def _safe_json(value: Any) -> dict:
    """Coerce a JSONB column read into a dict (handles str / None / dict)."""
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
    """Return slugs of every project. ``dash_projects`` has no status col,
    so 'active' = exists; per-project disable lives in feature_config."""
    from sqlalchemy import text as _t
    rows = cn.execute(_t(
        "SELECT slug FROM public.dash_projects ORDER BY id"
    )).fetchall()
    return [r[0] for r in rows if r and r[0]]


def _is_project_enabled(slug: str) -> bool:
    """Honor feature_config.tools.auto_campaign_daemon (default True)."""
    try:
        from dash.feature_config import get_feature_config
        cfg = get_feature_config(slug) or {}
        tools = cfg.get("tools") or {}
        # Missing key → enabled by default.
        v = tools.get("auto_campaign_daemon", True)
        return bool(v)
    except Exception:
        # Fail-open — never block daemon on config-load issues.
        return True


# ── Distribution sampling ─────────────────────────────────────────────


def _sample_distributions(slug: str) -> dict[str, Any] | None:
    """Run rfm_score + churn_risk_score in a thread-safe way and return
    a normalized distribution dict, or None if sampling failed (no data
    table, missing columns, etc.) — callers skip the project in that case.
    """
    try:
        from dash.tools.customer_intelligence import rfm_score
        from dash.tools.clv_churn import churn_risk_score
    except Exception:
        logger.exception("auto_campaign: tool import failed")
        return None

    try:
        rfm = rfm_score(slug) or {}
    except Exception:
        logger.exception("auto_campaign: rfm_score crashed slug=%s", slug)
        rfm = {}

    try:
        churn = churn_risk_score(slug) or {}
    except Exception:
        logger.exception("auto_campaign: churn_risk_score crashed slug=%s", slug)
        churn = {}

    if not rfm.get("ok") and not churn.get("ok"):
        return None

    rfm_dist = {str(k): int(v) for k, v in (rfm.get("segments") or {}).items()}
    churn_dist = {str(k): int(v) for k, v in (churn.get("distribution") or {}).items()}
    total = int(rfm.get("rows_analyzed") or churn.get("rows_analyzed") or 0)

    # Average spend across all customers — used for revenue-lift heuristic.
    avg_spend = 0.0
    try:
        seg_pct = rfm.get("segments_with_pct") or {}
        if seg_pct:
            num, denom = 0.0, 0
            for sub in seg_pct.values():
                if not isinstance(sub, dict):
                    continue
                cnt = int(sub.get("count") or 0)
                rev = float(sub.get("total_revenue") or 0.0)
                num += rev
                denom += cnt
            if denom:
                avg_spend = num / denom
    except Exception:
        avg_spend = 0.0

    return {
        "rfm": rfm_dist,
        "churn": churn_dist,
        "total": total,
        "avg_spend": float(avg_spend),
        "top_customers": rfm.get("top_customers") or [],
    }


def _save_snapshot(cn, slug: str, sample: dict[str, Any]) -> int:
    from sqlalchemy import text as _t
    row = cn.execute(_t(
        "INSERT INTO dash_segment_snapshots "
        "  (project_slug, rfm_distribution, churn_distribution, total_customers, metadata) "
        "VALUES (:s, CAST(:r AS jsonb), CAST(:c AS jsonb), :t, CAST(:m AS jsonb)) "
        "RETURNING id"
    ), {
        "s": slug,
        "r": json.dumps(sample.get("rfm") or {}),
        "c": json.dumps(sample.get("churn") or {}),
        "t": int(sample.get("total") or 0),
        "m": json.dumps({"avg_spend": float(sample.get("avg_spend") or 0.0)}),
    }).fetchone()
    return int(row[0])


def _last_snapshot(cn, slug: str) -> dict[str, Any] | None:
    """Return the most-recent prior snapshot (excluding the just-inserted
    one)."""
    from sqlalchemy import text as _t
    row = cn.execute(_t(
        "SELECT rfm_distribution, churn_distribution, total_customers, metadata "
        "FROM dash_segment_snapshots "
        "WHERE project_slug = :s "
        "ORDER BY captured_at DESC "
        "OFFSET 1 LIMIT 1"
    ), {"s": slug}).fetchone()
    if not row:
        return None
    return {
        "rfm": _safe_json(row[0]),
        "churn": _safe_json(row[1]),
        "total": int(row[2] or 0),
        "metadata": _safe_json(row[3]),
    }


# ── Cooldown ──────────────────────────────────────────────────────────


def _is_in_cooldown(cn, slug: str, rule: str) -> bool:
    """True if an auto-draft for (slug, rule) was created within COOLDOWN_DAYS."""
    from sqlalchemy import text as _t
    row = cn.execute(_t(
        "SELECT 1 FROM dash_campaigns "
        " WHERE project_slug = :s "
        "   AND type = 'auto' "
        "   AND created_by = 'auto_campaign_daemon' "
        "   AND metadata->>'rule' = :r "
        "   AND created_at > now() - (:days::int * INTERVAL '1 day') "
        " LIMIT 1"
    ), {"s": slug, "r": rule, "days": COOLDOWN_DAYS}).fetchone()
    return row is not None


# ── Rule evaluation ───────────────────────────────────────────────────


def _evaluate_rules(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return a list of ``{rule, target_segment, discount_pct,
    detected_change, current_count, previous_count}`` dicts for each
    rule that fired this cycle.
    """
    fired: list[dict[str, Any]] = []
    cur_rfm = current.get("rfm") or {}
    cur_total = int(current.get("total") or 0)
    prev_rfm = (previous or {}).get("rfm") or {} if previous else {}

    def _delta(seg: str) -> tuple[int, int, float]:
        cur = int(cur_rfm.get(seg, 0))
        prv = int(prev_rfm.get(seg, 0))
        if prv <= 0:
            return cur, prv, 0.0
        return cur, prv, (cur - prv) / float(prv)

    # Champions drop ≥ X%
    if previous is not None:
        cur, prv, pct = _delta("Champions")
        if prv > 0 and pct <= -CHAMPIONS_DROP_PCT:
            fired.append({
                "rule": "champions_drop",
                "target_segment": "Champions",
                "discount_pct": 10,
                "detected_change": (
                    f"Champions dropped from {prv} to {cur} ({pct*100:.0f}%)"
                ),
                "current_count": cur,
                "previous_count": prv,
                "name": "Win-Back Champions",
                "description": (
                    "Auto-drafted: Champions segment shrank materially "
                    "since last cycle. Re-engage with a 10% loyalty offer."
                ),
                "severity": "warn",
            })

        cur, prv, pct = _delta("At Risk")
        if prv > 0 and pct >= AT_RISK_SURGE_PCT:
            fired.append({
                "rule": "at_risk_surge",
                "target_segment": "At Risk",
                "discount_pct": 15,
                "detected_change": (
                    f"At Risk surged from {prv} to {cur} (+{pct*100:.0f}%)"
                ),
                "current_count": cur,
                "previous_count": prv,
                "name": "Save At-Risk Customers",
                "description": (
                    "Auto-drafted: At Risk cohort growing fast — push a "
                    "15% rescue offer before they churn."
                ),
                "severity": "warn",
            })

        cur, prv, pct = _delta("New Customers")
        if prv > 0 and pct >= NEW_SPIKE_PCT:
            fired.append({
                "rule": "new_spike",
                "target_segment": "New Customers",
                "discount_pct": 5,
                "detected_change": (
                    f"New Customers spiked from {prv} to {cur} (+{pct*100:.0f}%)"
                ),
                "current_count": cur,
                "previous_count": prv,
                "name": "New Customer Welcome",
                "description": (
                    "Auto-drafted: New-customer inflow jumped — convert "
                    "them with a 5% welcome offer."
                ),
                "severity": "info",
            })

    # Hibernating share rule does NOT need a previous snapshot — it's a
    # static health threshold on the current state.
    if cur_total > 0:
        hib = int(cur_rfm.get("Hibernating", 0))
        share = hib / float(cur_total)
        if share >= HIBERNATING_SHARE:
            fired.append({
                "rule": "hibernating_overflow",
                "target_segment": "Hibernating",
                "discount_pct": 20,
                "detected_change": (
                    f"Hibernating = {hib} of {cur_total} customers "
                    f"({share*100:.0f}% of base — threshold "
                    f"{HIBERNATING_SHARE*100:.0f}%)"
                ),
                "current_count": hib,
                "previous_count": int(prev_rfm.get("Hibernating", 0)),
                "name": "Reactivate Hibernating Customers",
                "description": (
                    "Auto-drafted: too many customers gone dormant. "
                    "20% reactivation push."
                ),
                "severity": "warn",
            })

    return fired


def _expected_revenue_lift(audience: int, avg_spend: float, discount_pct: int) -> float:
    """Rough heuristic: audience × avg_spend × discount × conversion rate."""
    if audience <= 0 or avg_spend <= 0:
        return 0.0
    return round(
        audience
        * avg_spend
        * (discount_pct / 100.0)
        * ASSUMED_CONVERSION,
        2,
    )


# ── Draft creation ────────────────────────────────────────────────────


def _draft_campaign(
    cn,
    slug: str,
    rule: dict[str, Any],
    avg_spend: float,
) -> int | None:
    """Insert a draft campaign + auto_drafted event + proactive insight.

    Returns the new campaign id, or None on failure.
    """
    from sqlalchemy import text as _t

    audience = int(rule.get("current_count") or 0)
    discount_pct = int(rule.get("discount_pct") or 0)
    seg = rule.get("target_segment")
    name = rule.get("name") or f"Auto: {seg}"

    expected_lift = _expected_revenue_lift(audience, avg_spend, discount_pct)

    reasoning = {
        "detected_change": rule.get("detected_change"),
        "suggested_discount": discount_pct,
        "suggested_audience": seg,
        "expected_revenue_lift": expected_lift,
        "current_count": rule.get("current_count"),
        "previous_count": rule.get("previous_count"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata = {
        "rule": rule.get("rule"),
        "reasoning": reasoning,
        "auto": True,
    }
    offer = {
        "kind": "discount",
        "discount_pct": discount_pct,
        "description": f"{discount_pct}% off — auto-drafted by daemon",
    }

    try:
        row = cn.execute(_t(
            """
            INSERT INTO dash_campaigns
              (project_slug, name, description, type, status,
               target_segment, target_filter, audience_size, offer,
               cost_budget, created_by, metadata)
            VALUES
              (:slug, :name, :desc, 'auto', 'draft',
               :seg, CAST(:tf AS jsonb), :asize, CAST(:offer AS jsonb),
               NULL, 'auto_campaign_daemon', CAST(:meta AS jsonb))
            RETURNING id
            """
        ), {
            "slug": slug,
            "name": name,
            "desc": rule.get("description") or "",
            "seg": seg,
            "tf": json.dumps({}),
            "asize": audience,
            "offer": json.dumps(offer),
            "meta": json.dumps(metadata),
        }).fetchone()
    except Exception:
        logger.exception("auto_campaign: insert draft failed slug=%s rule=%s",
                         slug, rule.get("rule"))
        return None

    if not row:
        return None
    cid = int(row[0])

    # Best-effort: notify project owner of auto-drafted campaign
    try:
        from sqlalchemy import text as _nt
        from app.auth import notify_user  # type: ignore
        owner_row = cn.execute(_nt(
            "SELECT user_id FROM public.dash_projects WHERE slug = :s"
        ), {"s": slug}).fetchone()
        if owner_row and owner_row[0]:
            notify_user(
                int(owner_row[0]),
                f"Campaign drafted · {name}",
                f"Audience {audience:,} · est lift ${expected_lift:,.0f}",
                "info",
            )
    except Exception:
        logger.debug("auto_campaign: notify_user skipped", exc_info=True)

    # Event row
    try:
        cn.execute(_t(
            "INSERT INTO dash_campaign_events "
            "  (campaign_id, event_type, actor, payload) "
            "VALUES (:c, 'auto_drafted', 'auto_campaign_daemon', "
            "        CAST(:p AS jsonb))"
        ), {
            "c": cid,
            "p": json.dumps({
                "rule": rule.get("rule"),
                "reasoning": reasoning,
            }),
        })
    except Exception:
        logger.exception("auto_campaign: event log failed cid=%s", cid)

    # Proactive insight (chat surface) — best-effort, schema-tolerant.
    try:
        cn.execute(_t(
            "INSERT INTO public.dash_proactive_insights "
            "  (project_slug, user_id, insight, severity, tables_involved) "
            "VALUES (:s, NULL, :ins, :sev, :tbl)"
        ), {
            "s": slug,
            "ins": (
                f"🤖 AUTO: {rule.get('detected_change')}. "
                f"Drafted campaign '{name}' targeting {seg} "
                f"({audience:,} customers, {discount_pct}% off, "
                f"~${expected_lift:,.0f} expected lift). "
                f"Review in /campaigns."
            ),
            "sev": rule.get("severity") or "info",
            "tbl": ["dash_campaigns"],
        })
    except Exception:
        # Some deployments use a different schema for tables_involved
        # (e.g. JSONB). Fall back silently — insight is a UX nicety.
        logger.debug("auto_campaign: proactive insight insert skipped",
                     exc_info=True)

    return cid


# ── Per-project cycle ─────────────────────────────────────────────────


def run_cycle_for_project(slug: str) -> dict[str, Any]:
    """Single-project cycle. Used by the daemon loop AND the run-now
    endpoint. Returns counts + diagnostics."""
    out: dict[str, Any] = {
        "slug": slug,
        "drafts_created": 0,
        "rules_fired": [],
        "cooldown_skipped": [],
        "skipped_reason": None,
        "snapshot_id": None,
    }

    if not _is_project_enabled(slug):
        out["skipped_reason"] = "disabled_per_project"
        return out

    sample = _sample_distributions(slug)
    if sample is None:
        out["skipped_reason"] = "no_sample"
        return out

    eng = _engine()
    if eng is None:
        out["skipped_reason"] = "no_engine"
        return out

    # First write the snapshot (always — gives us the timeline) so the
    # NEXT cycle has a reliable prior. Then read back the previous
    # (offset-1) snapshot for delta evaluation.
    try:
        with eng.begin() as cn:
            snap_id = _save_snapshot(cn, slug, sample)
            previous = _last_snapshot(cn, slug)
        out["snapshot_id"] = snap_id
    except Exception:
        logger.exception("auto_campaign: snapshot save failed slug=%s", slug)
        out["skipped_reason"] = "snapshot_failed"
        return out

    fired = _evaluate_rules(sample, previous)
    if not fired:
        return out

    cap = MAX_DRAFTS_PER_PROJECT_PER_CYCLE
    avg_spend = float(sample.get("avg_spend") or 0.0)

    # Each draft is its own short transaction — keeps blast radius small
    # if one fails, and lets cooldown queries see committed rows from
    # earlier rules in the same cycle (e.g. extension rules in future).
    for rule in fired:
        if cap <= 0:
            break
        rule_name = rule.get("rule") or "unknown"
        try:
            with eng.begin() as cn:
                if _is_in_cooldown(cn, slug, rule_name):
                    out["cooldown_skipped"].append(rule_name)
                    continue
                cid = _draft_campaign(cn, slug, rule, avg_spend)
            if cid:
                out["drafts_created"] += 1
                out["rules_fired"].append({
                    "rule": rule_name,
                    "campaign_id": cid,
                    "target_segment": rule.get("target_segment"),
                    "discount_pct": rule.get("discount_pct"),
                })
                cap -= 1
        except Exception:
            logger.exception(
                "auto_campaign: draft failed slug=%s rule=%s",
                slug, rule_name,
            )

    return out


def run_cycle() -> dict[str, Any]:
    """Run a cycle across every project."""
    out: dict[str, Any] = {
        "projects_scanned": 0,
        "drafts_created": 0,
        "rules_fired_total": 0,
        "per_project": [],
    }
    eng = _engine()
    if eng is None:
        logger.warning("auto_campaign: no engine, aborting cycle")
        return out

    try:
        with eng.begin() as cn:
            slugs = _list_active_projects(cn)
    except Exception:
        logger.exception("auto_campaign: project listing failed")
        return out

    for slug in slugs:
        try:
            res = run_cycle_for_project(slug)
        except Exception:
            logger.exception("auto_campaign: project cycle crashed slug=%s", slug)
            continue
        out["projects_scanned"] += 1
        out["drafts_created"] += int(res.get("drafts_created") or 0)
        out["rules_fired_total"] += len(res.get("rules_fired") or [])
        out["per_project"].append(res)

    logger.info(
        "auto_campaign: cycle_done projects=%d drafts=%d rules_fired=%d",
        out["projects_scanned"], out["drafts_created"], out["rules_fired_total"],
    )
    return out


# ── Async loop ────────────────────────────────────────────────────────


def _interval_seconds() -> int:
    raw = os.getenv("AUTO_CAMPAIGN_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
        if v > 0:
            return v
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def _is_disabled() -> bool:
    return (os.getenv("AUTO_CAMPAIGN_DAEMON_DISABLED", "").lower()
            in ("1", "true", "yes"))


async def auto_campaign_loop() -> None:
    """Forever-loop: run cycle, sleep, repeat. Crash-resistant."""
    if _is_disabled():
        logger.info("auto_campaign: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("auto_campaign: starting (interval=%ds)", interval)
    while True:
        try:
            with trace_span("cron.auto_campaign", kind="cron"):
                await asyncio.to_thread(run_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("auto_campaign: cycle crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = [
    "run_cycle",
    "run_cycle_for_project",
    "auto_campaign_loop",
]
