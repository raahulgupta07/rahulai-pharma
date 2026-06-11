"""Autonomy heartbeat loop — token-frugal autonomy core.

Core principle: DETECTION IS FREE, THINKING IS PAID AND RARE.

Each tick:
  T0  resolve the locked slug + load the last signal snapshot.
  T1  collect_signals(slug)  — PURE SQL, ZERO tokens.
  T2  diff(old, new). NOT tripped → save snapshot (if changed) + sleep.
      A quiet tick writes ZERO tokens and NO journal row.
  T3  tripped → dispatch each tripped key to a STUB handler that journals ONE
      intent row (tier='T3', tokens=0). No LLM / training is run yet — real
      handlers land later. Then save the new snapshot.

Budget guard: if today's journalled token total for the slug is >= the daily
cap, T3 dispatch is skipped (free-only mode) and a single 'budget_cap_reached'
row is journalled at most once per day.

Leader election mirrors auto_train_daemon: exactly one worker runs the loop via
dash.runtime.daemon_leader.try_become_leader(). The whole tick is wrapped so one
bad tick never kills the loop.

Disable: AUTONOMY_HEARTBEAT_DISABLED=1
Tunables: AUTONOMY_POLL_INTERVAL_S (300), AUTONOMY_DAILY_TOKEN_CAP (50000).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

log = logging.getLogger("dash.heartbeat")

_DISABLED = os.getenv("AUTONOMY_HEARTBEAT_DISABLED", "0").strip().lower() in ("1", "true", "yes")
_POLL_INTERVAL = int(os.getenv("AUTONOMY_POLL_INTERVAL_S", "300"))
_DAILY_TOKEN_CAP = int(os.getenv("AUTONOMY_DAILY_TOKEN_CAP", "50000"))

# T3 real-action gate. Default OFF → handlers only journal the detected intent
# (the original stub behaviour, zero side effects). Flip AUTONOMY_T3_ACTIONS=1
# to let data-change signals trigger a real (free) retrain enqueue. Kept off by
# default so enabling autonomy is an explicit operator decision, never a
# surprise on upgrade.
_T3_ACTIONS = os.getenv("AUTONOMY_T3_ACTIONS", "0").strip().lower() in ("1", "true", "yes")

# Signals whose trip means "the underlying data/schema moved" → a retrain is the
# meaningful autonomous response. Any other signal stays a journal-only stub.
_T3_RETRAIN_SIGNALS = {"table_fingerprints", "schema_hash", "shop_flat"}

# Track the last day we journalled a budget_cap_reached row (per slug) so we
# don't spam it every tick once the cap is hit.
_budget_capped_day: dict[str, str] = {}


def _get_locked_slug() -> Optional[str]:
    try:
        from dash.single_agent import is_single_agent, locked_slug
        if is_single_agent():
            return locked_slug()
    except Exception:
        pass
    return None


def _tokens_today(slug: str) -> int:
    """Sum of tokens journalled for this slug since midnight (DB time). Fail-soft → 0."""
    from sqlalchemy import text
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.connect() as conn:
            n = conn.execute(text(
                "SELECT COALESCE(sum(tokens), 0) FROM public.dash_autonomy_journal "
                "WHERE project_slug = :s AND ts >= date_trunc('day', now())"
            ), {"s": slug}).scalar()
        return int(n or 0)
    except Exception as e:
        log.debug("heartbeat: token-budget query failed for %s: %s", slug, e)
        return 0


def _dispatch_t3(slug: str, signal: str, new: dict) -> None:
    """T3 handler for one tripped signal.

    Default (AUTONOMY_T3_ACTIONS off): journal the detected intent only — no
    LLM, no training, zero side effects (original stub behaviour).

    When AUTONOMY_T3_ACTIONS=1 and the signal is a data/schema-change signal,
    take the real autonomous action: enqueue a retrain for only the tables that
    actually need it (reuses the tested auto_train_daemon path). Enqueue itself
    is cheap SQL+Redis; the heavy work runs on the train worker. Any unknown
    signal — or a failed action — falls back to a journal row so the loop never
    raises.
    """
    from dash.autonomy.state import journal
    detail = {"value": new.get(signal)} if signal in new else {"removed": True}

    if not _T3_ACTIONS or signal not in _T3_RETRAIN_SIGNALS:
        journal(slug, "T3", signal, "detected — handler stub", detail=detail, tokens=0)
        return

    try:
        from dash.cron.auto_train_daemon import _enqueue_retrain
        queued = _enqueue_retrain(slug, reason=f"heartbeat:{signal}")
        action = "retrain enqueued" if queued else "no tables need retrain"
        journal(slug, "T3", signal, action, detail=detail, tokens=0)
        log.info("heartbeat: T3 action for %s signal=%s → %s", slug, signal, action)
    except Exception as e:
        journal(slug, "T3", signal, "action failed — journalled intent",
                detail={**detail, "error": str(e)}, tokens=0)
        log.debug("heartbeat: T3 action failed for %s/%s: %s", slug, signal, e)
    log.info("heartbeat: T3 intent journalled for %s signal=%s", slug, signal)


async def _tick() -> None:
    """One heartbeat tick. Fail-soft — never raises."""
    from dash.autonomy.signals import collect_signals
    from dash.autonomy.state import diff, journal, load_state, save_state

    # T0
    slug = _get_locked_slug()
    if not slug:
        return
    old = load_state(slug)

    # T1 — FREE
    new = collect_signals(slug)
    if not new:
        return  # collection failed entirely; nothing to compare

    # First-ever tick: no prior state → record a silent baseline, do NOT trip
    # every signal (avoids cold-start journal noise). One 'initialized' row only.
    if not old:
        save_state(slug, new)
        journal(slug, "T0", "initialized", "baseline recorded — watching",
                detail={"signals": sorted(new.keys())}, tokens=0)
        return

    # T2
    tripped = diff(old, new)
    if not tripped:
        if new != old:
            save_state(slug, new)
        return  # quiet tick — ZERO tokens, NO journal row

    # Budget guard
    spent = _tokens_today(slug)
    if spent >= _DAILY_TOKEN_CAP:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if _budget_capped_day.get(slug) != today:
            journal(slug, "budget", "budget_cap_reached",
                    "free-only — daily token cap reached",
                    detail={"spent": spent, "cap": _DAILY_TOKEN_CAP}, tokens=0)
            _budget_capped_day[slug] = today
        save_state(slug, new)
        return

    # T3 — dispatch each tripped signal to its stub handler
    for signal in tripped:
        try:
            _dispatch_t3(slug, signal, new)
        except Exception as e:
            log.debug("heartbeat: T3 dispatch failed for %s/%s: %s", slug, signal, e)
    save_state(slug, new)


async def heartbeat_loop() -> None:
    """Main autonomy heartbeat loop. Runs on exactly one (leader) worker."""
    if _DISABLED:
        log.info("autonomy heartbeat: disabled (AUTONOMY_HEARTBEAT_DISABLED=1)")
        return

    try:
        from dash.runtime.daemon_leader import try_become_leader
        if not try_become_leader():
            log.info("autonomy heartbeat: not the daemon leader; not running")
            return
    except Exception as e:
        # Fail-open: better to run on one worker than none.
        log.warning("autonomy heartbeat: leader election failed (%s) — running anyway", e)

    log.info(
        "autonomy heartbeat: started (poll=%ss, daily_token_cap=%s)",
        _POLL_INTERVAL, _DAILY_TOKEN_CAP,
    )
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            await _tick()
        except Exception as e:
            log.exception("autonomy heartbeat: unhandled tick error: %s", e)
