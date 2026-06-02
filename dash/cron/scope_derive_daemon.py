"""Scope Guardrail Auto-Derive Daemon.

Nightly sweep: any project whose `feature_config.scope.topics` is empty/missing
gets a scope auto-derived via `dash.scope_deriver.derive_scope()` and persisted.

Why:
  Scope guardrail blocks off-topic answers per project. Empty scope = both
  the pre-flight LITE classifier and the instruction-level hard rule are
  no-ops → agent answers from world knowledge → leak of generic/wrong info.

  TRAIN ALL runs `scope_derivation` step automatically for newly trained
  projects. Older projects (trained before that step landed) OR projects
  where derivation failed silently are missed. This daemon closes the gap.

Trigger:
  - Default cadence: 24h (env `SCOPE_DERIVE_INTERVAL_SECONDS`)
  - Default time of day: ~03:30 UTC if first run; otherwise interval-based
  - Manual one-shot: `POST /api/admin/scope-derive/run-now` (super-admin)
  - Global disable: `SCOPE_DERIVE_DAEMON_DISABLED=1`

Safety:
  - Skips manually-edited scopes (`_auto: false`) — operator wins
  - Skips projects with non-empty topics (already covered)
  - Per-project try/except — one bad project never kills sweep
  - Fail-soft on derive errors — logs + continues
  - Cost-guarded via cost_guard (uses LITE_MODEL ~$0.001/derive)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Observability tracing fail-soft
try:
    from dash.obs.trace import trace_span
except Exception:
    from contextlib import contextmanager
    @contextmanager
    def trace_span(*args, **kwargs):
        yield

DEFAULT_INTERVAL_SECONDS = 86400  # 24h


# ───────────────────────────────────────────────────────────────────────────
# Core cycle
# ───────────────────────────────────────────────────────────────────────────

def _list_missing_scope_projects() -> list[str]:
    """Return slugs where scope is missing/empty AND not manually flagged."""
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text as _t
    except Exception as e:
        logger.warning("scope_derive: db imports failed: %s", e)
        return []
    rows: list = []
    try:
        eng = get_sql_engine()
        with eng.connect() as c:
            rows = c.execute(_t("""
                SELECT slug
                FROM public.dash_projects
                WHERE
                  COALESCE(jsonb_array_length(feature_config->'scope'->'topics'), 0) = 0
                  AND COALESCE(feature_config->'scope'->>'_auto', 'true') <> 'false'
                ORDER BY slug
            """)).fetchall()
    except Exception as e:
        logger.warning("scope_derive: project query failed: %s", e)
        return []
    return [r[0] for r in rows if r and r[0]]


def _derive_and_persist(slug: str) -> dict[str, Any]:
    """Derive scope for one project + persist. Returns {ok, slug, topics_count, error?}."""
    try:
        from dash.scope_deriver import derive_scope
        from dash.feature_config import get_feature_config, set_feature_config
    except Exception as e:
        return {"ok": False, "slug": slug, "error": f"import: {e}"}
    try:
        scope = derive_scope(slug)
        if not scope or not isinstance(scope, dict):
            return {"ok": False, "slug": slug, "error": "empty derive result"}
        topics = scope.get("topics") or []
        if not topics:
            return {"ok": False, "slug": slug, "error": "no topics derived"}

        cfg = get_feature_config(slug) or {}
        cfg["scope"] = dict(scope)
        cfg["scope"]["_auto"] = True
        set_feature_config(slug, cfg)
        return {
            "ok": True,
            "slug": slug,
            "topics_count": len(topics),
            "denied_count": len(scope.get("denied_intents") or []),
        }
    except Exception as e:
        return {"ok": False, "slug": slug, "error": str(e)[:200]}


def run_cycle() -> dict[str, Any]:
    """Single sweep — derive scope for every project missing one.

    Returns:
      {checked: N, derived: M, failed: K, results: [...]}
    """
    slugs = _list_missing_scope_projects()
    if not slugs:
        logger.info("scope_derive: all projects covered, nothing to do")
        return {"checked": 0, "derived": 0, "failed": 0, "results": []}

    logger.info("scope_derive: %d project(s) missing scope: %s", len(slugs), ", ".join(slugs[:10]))
    results: list[dict] = []
    derived = 0
    failed = 0
    for slug in slugs:
        try:
            res = _derive_and_persist(slug)
        except Exception as e:
            res = {"ok": False, "slug": slug, "error": f"unexpected: {str(e)[:200]}"}
        results.append(res)
        if res.get("ok"):
            derived += 1
            logger.info(
                "scope_derive: %s ✓ topics=%d denied=%d",
                slug, res.get("topics_count", 0), res.get("denied_count", 0),
            )
        else:
            failed += 1
            logger.warning("scope_derive: %s ✗ %s", slug, res.get("error"))

    return {
        "checked": len(slugs),
        "derived": derived,
        "failed": failed,
        "results": results,
    }


# ───────────────────────────────────────────────────────────────────────────
# Daemon loop
# ───────────────────────────────────────────────────────────────────────────

def _interval_seconds() -> int:
    try:
        return int(os.getenv("SCOPE_DERIVE_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))
    except Exception:
        return DEFAULT_INTERVAL_SECONDS


def _is_disabled() -> bool:
    return (os.getenv("SCOPE_DERIVE_DAEMON_DISABLED", "").lower()
            in ("1", "true", "yes"))


async def scope_derive_loop() -> None:
    """Forever-loop: run cycle, sleep, repeat. Crash-resistant."""
    if _is_disabled():
        logger.info("scope_derive: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("scope_derive: starting (interval=%ds)", interval)
    # Initial 5-min stagger so all daemons don't slam DB at lifespan start
    try:
        await asyncio.sleep(300)
    except asyncio.CancelledError:
        raise

    while True:
        try:
            with trace_span("cron.scope_derive", kind="cron"):
                await asyncio.to_thread(run_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scope_derive: cycle crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = [
    "run_cycle",
    "_derive_and_persist",
    "_list_missing_scope_projects",
    "scope_derive_loop",
]
