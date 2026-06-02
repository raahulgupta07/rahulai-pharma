"""Nightly golden-SQL drift checker.

For each project's `_golden.json` entry, re-execute the SQL and compare
result rowcount/value against `expected_*` fields. If drift exceeds
threshold → demote (remove from corpus + log to audit).

Inspired by Dataherald's golden_sql auto-validation pattern: a pinned
query that no longer returns the truth class must not keep matching
future questions silently.

Cadence: daily 03:30 UTC via cron.
Disable: `GOLDEN_DRIFT_DISABLED=1`.
Threshold: ±50% rowcount delta OR scalar value delta > 1.5% (matches
score_verified default).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 86400  # 24h
_ROWCOUNT_DRIFT_PCT = 0.50
_SCALAR_DRIFT_REL = 0.015


def _list_projects() -> list[str]:
    """Return all project slugs that have a _golden.json file."""
    from dash.paths import KNOWLEDGE_DIR
    if not KNOWLEDGE_DIR.exists():
        return []
    out = []
    for sub in KNOWLEDGE_DIR.iterdir():
        if not sub.is_dir():
            continue
        if (sub / "training" / "_golden.json").exists():
            out.append(sub.name)
    return out


def _run_sql_safe(slug: str, sql: str) -> dict[str, Any] | None:
    """Read-only execute. Returns {rowcount, scalar_value} or None on error."""
    try:
        from sqlalchemy import text as _text
        from dash.tools.metric_compiler import resolve_engine
        engine, _ = resolve_engine(slug)
        with engine.connect() as conn:
            conn.execute(_text("SET LOCAL statement_timeout = '20s'"))
            safe = sql.rstrip().rstrip(";")
            rs = conn.execute(_text(safe))
            rows = rs.fetchmany(100)
            rowcount = len(rows)
            scalar = None
            if rowcount == 1 and rows[0] is not None and len(rows[0]) >= 1:
                v = rows[0][0]
                try:
                    scalar = float(v)
                except Exception:
                    scalar = None
            return {"rowcount": rowcount, "scalar_value": scalar}
    except Exception as e:
        logger.debug(f"golden re-exec failed for {slug}: {e}")
        return None


def _drift_check(entry: dict, current: dict) -> tuple[bool, str | None]:
    """Returns (should_demote, reason)."""
    exp_rc = entry.get("expected_rowcount")
    cur_rc = current.get("rowcount")
    if exp_rc is not None and isinstance(cur_rc, int) and exp_rc > 0:
        delta = abs(cur_rc - exp_rc) / exp_rc
        if delta > _ROWCOUNT_DRIFT_PCT:
            return True, f"rowcount drift {delta*100:.0f}% (was {exp_rc}, now {cur_rc})"

    exp_v = entry.get("expected_value")
    cur_v = current.get("scalar_value")
    if exp_v is not None and cur_v is not None:
        try:
            exp_f = float(exp_v)
            if exp_f != 0:
                rel = abs(cur_v - exp_f) / abs(exp_f)
                if rel > _SCALAR_DRIFT_REL:
                    return True, f"value drift {rel*100:.1f}% (was {exp_f}, now {cur_v})"
        except Exception:
            pass

    return False, None


def check_project(slug: str, dry_run: bool = False) -> dict:
    """Re-execute every golden for one project. Demote drifted ones."""
    from dash.learning.golden import list_goldens, demote
    entries = list_goldens(slug)
    checked = 0
    demoted = 0
    drifted: list[dict] = []
    for entry in entries:
        sql = entry.get("sql")
        if not sql:
            continue
        checked += 1
        current = _run_sql_safe(slug, sql)
        if current is None:
            # exec error → demote (broken SQL helps nobody)
            drifted.append({"entry": entry, "reason": "exec_failed"})
            if not dry_run:
                demote(slug, sql=sql)
                demoted += 1
            continue
        should, reason = _drift_check(entry, current)
        if should:
            drifted.append({"entry": entry, "reason": reason})
            if not dry_run:
                demote(slug, sql=sql)
                demoted += 1

    return {"slug": slug, "checked": checked, "demoted": demoted, "drifted": drifted}


def check_all(dry_run: bool = False) -> dict:
    """Run drift check across all projects with goldens."""
    slugs = _list_projects()
    results = []
    for s in slugs:
        try:
            results.append(check_project(s, dry_run=dry_run))
        except Exception as e:
            logger.exception(f"drift check crashed for {s}: {e}")
    total_checked = sum(r["checked"] for r in results)
    total_demoted = sum(r["demoted"] for r in results)
    return {
        "projects": len(slugs),
        "total_checked": total_checked,
        "total_demoted": total_demoted,
        "per_project": results,
    }


async def golden_drift_loop(interval_seconds: int = _DEFAULT_INTERVAL_S):
    """Daily background loop. Spawned by lifespan when not disabled."""
    if os.environ.get("GOLDEN_DRIFT_DISABLED") == "1":
        logger.info("golden_drift_loop disabled via GOLDEN_DRIFT_DISABLED=1")
        return
    logger.info(f"golden_drift_loop started (interval {interval_seconds}s)")
    # Stagger first run by 60s so startup isn't slammed
    await asyncio.sleep(60)
    while True:
        try:
            t0 = time.time()
            res = await asyncio.to_thread(check_all)
            logger.info(
                f"golden_drift_cycle done in {int(time.time()-t0)}s: "
                f"projects={res['projects']} checked={res['total_checked']} demoted={res['total_demoted']}"
            )
        except Exception as e:
            logger.exception(f"golden_drift cycle error: {e}")
        await asyncio.sleep(interval_seconds)
