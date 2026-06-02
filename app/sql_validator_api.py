"""SQL Validator telemetry HTTP API.

Reads dash.dash_sql_validator_events + sql_validator cache stats.
Plus admin endpoint that wraps scripts/check_migration_drift.py.

All endpoints fail-soft on DB/import errors (return empty shapes, never 500).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sql-validator"])


# ---------- auth helpers (mirror app/admin_api.py pattern) ----------
def _get_user(request: Request) -> dict:
    from app.auth import get_current_user
    user = getattr(getattr(request, "state", None), "user", None) or get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict) -> None:
    if not user.get("is_super") and not user.get("is_super_admin"):
        raise HTTPException(403, "super-admin only")


# ---------- per-project stats ----------
@router.get("/api/projects/{slug}/sql-validator/stats")
def sql_validator_stats(slug: str, days: int = Query(7, ge=1, le=365)) -> dict[str, Any]:
    """Per-project validator activity counters + recent events + top-dropped tables."""
    out = {
        "auto_fix_count": 0,
        "qa_drop_count": 0,
        "chat_autofix_count": 0,
        "reject_count": 0,
        "top_dropped_tables": [],
        "recent_events": [],
    }
    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.begin() as c:
            counts = c.execute(text(
                "SELECT kind, COUNT(*)::int AS n "
                "FROM dash.dash_sql_validator_events "
                "WHERE project_slug = :p "
                "  AND ts > now() - (:d || ' days')::interval "
                "GROUP BY kind"
            ), {"p": slug, "d": str(days)}).fetchall()
            for row in counts:
                k = row[0]
                n = int(row[1] or 0)
                if k == "auto_fix":
                    out["auto_fix_count"] = n
                elif k == "qa_drop":
                    out["qa_drop_count"] = n
                elif k == "chat_autofix":
                    out["chat_autofix_count"] = n
                elif k == "reject":
                    out["reject_count"] = n

            top = c.execute(text(
                "SELECT table_name, COUNT(*)::int AS n "
                "FROM dash.dash_sql_validator_events "
                "WHERE project_slug = :p AND kind = 'qa_drop' "
                "  AND ts > now() - (:d || ' days')::interval "
                "  AND table_name IS NOT NULL "
                "GROUP BY table_name "
                "ORDER BY n DESC "
                "LIMIT 10"
            ), {"p": slug, "d": str(days)}).fetchall()
            out["top_dropped_tables"] = [
                {"table": r[0], "count": int(r[1])} for r in top
            ]

            recent = c.execute(text(
                "SELECT ts, kind, source, table_name, details "
                "FROM dash.dash_sql_validator_events "
                "WHERE project_slug = :p "
                "  AND ts > now() - (:d || ' days')::interval "
                "ORDER BY ts DESC "
                "LIMIT 50"
            ), {"p": slug, "d": str(days)}).fetchall()
            out["recent_events"] = [
                {
                    "ts": r[0].isoformat() if r[0] else None,
                    "kind": r[1],
                    "source": r[2],
                    "table_name": r[3],
                    "details": r[4] if isinstance(r[4], dict) else (
                        json.loads(r[4]) if r[4] else {}
                    ),
                }
                for r in recent
            ]
    except Exception as e:
        logger.debug(f"sql_validator_stats({slug}) failed: {e}")
    return out


@router.get("/api/projects/{slug}/sql-validator/qa-drops")
def sql_validator_qa_drops(slug: str, days: int = Query(30, ge=1, le=365)) -> list[dict]:
    """Per-table QA drop rollup."""
    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.begin() as c:
            rows = c.execute(text(
                "SELECT table_name, COUNT(*)::int AS drop_count, MAX(ts) AS last_drop_at, "
                "       array_agg(DISTINCT (details->>'reason')) AS reasons "
                "FROM dash.dash_sql_validator_events "
                "WHERE project_slug = :p AND kind = 'qa_drop' "
                "  AND ts > now() - (:d || ' days')::interval "
                "GROUP BY table_name "
                "ORDER BY drop_count DESC "
                "LIMIT 100"
            ), {"p": slug, "d": str(days)}).fetchall()
            out: list[dict] = []
            for r in rows:
                reasons_raw = r[3] or []
                reasons: list[str] = []
                for x in reasons_raw:
                    if x is None:
                        continue
                    try:
                        # details->>'reason' may be a JSON-encoded list string
                        parsed = json.loads(x) if isinstance(x, str) and x.startswith("[") else x
                        if isinstance(parsed, list):
                            reasons.extend(str(p) for p in parsed)
                        else:
                            reasons.append(str(parsed))
                    except Exception:
                        reasons.append(str(x))
                out.append({
                    "table_name": r[0],
                    "drop_count": int(r[1]),
                    "last_drop_at": r[2].isoformat() if r[2] else None,
                    "reasons": list(dict.fromkeys(reasons))[:10],
                })
            return out
    except Exception as e:
        logger.debug(f"sql_validator_qa_drops({slug}) failed: {e}")
        return []


@router.get("/api/projects/sql-validator/cache-stats")
def sql_validator_cache_stats() -> dict[str, Any]:
    """Schema-cache hit/miss counters from sql_validator module."""
    try:
        from dash.tools.sql_validator import get_cache_stats
        return get_cache_stats()
    except Exception as e:
        logger.debug(f"cache stats failed: {e}")
        return {"cache_size": 0, "hits": 0, "misses": 0, "hit_rate": 0.0}


# ---------- admin: drift status ----------
_DRIFT_LAST_RUN: dict[str, Any] = {"at": None, "result": None}


def _parse_drift_report(stdout: str, stderr: str) -> dict[str, Any]:
    """Parse `scripts/check_migration_drift.py --report` output (best-effort)."""
    text_blob = (stdout or "") + "\n" + (stderr or "")
    def _num(*patterns: str) -> int:
        """Try patterns in order, return first match (including 0)."""
        for pat in patterns:
            m = re.search(pat, text_blob, re.IGNORECASE)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    continue
        return 0
    # Script outputs "Refs scanned: 1500" — match label-first format primarily,
    # fall back to "1500 refs scanned" for older output styles.
    refs = _num(r"refs?\s+scanned:?\s+(\d+)", r"(\d+)\s+refs?\s+scanned")
    migs = _num(r"migrations?\s+parsed:?\s+(\d+)", r"(\d+)\s+migrations?\s+parsed")
    drift_before = _num(r"drift\s+before\s+allowlist:?\s+(\d+)", r"(\d+)\s+drift\s+(?:items?\s+)?before\s+allowlist")
    drift_after = _num(r"drift\s+after\s+allowlist:?\s+(\d+)", r"(\d+)\s+drift\s+(?:items?\s+)?after\s+allowlist")
    allowlist = _num(r"allowlist\s+entries:?\s+(\d+)", r"(\d+)\s+allowlist\s+entries")
    return {
        "refs_scanned": refs,
        "migrations_parsed": migs,
        "drift_before_allowlist": drift_before,
        "drift_after_allowlist": drift_after,
        "allowlist_entries": allowlist,
    }


@router.get("/api/admin/drift/status")
def admin_drift_status(request: Request) -> dict[str, Any]:
    """Run scripts/check_migration_drift.py --report and return parsed counters."""
    user = _get_user(request)
    _require_super(user)

    # Locate script — repo root is two levels up from this file (app/.. == repo root /dash)
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "check_migration_drift.py"
    if not script.exists():
        # Fallback: search cwd
        alt = Path.cwd() / "scripts" / "check_migration_drift.py"
        if alt.exists():
            script = alt
    if not script.exists():
        raise HTTPException(500, f"drift script not found at {script}")

    try:
        proc = subprocess.run(
            ["python3", str(script), "--report"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ},
        )
        parsed = _parse_drift_report(proc.stdout, proc.stderr)
        out = {
            "ok": proc.returncode == 0,
            **parsed,
            "last_run_at": datetime.now(timezone.utc).isoformat(),
        }
        _DRIFT_LAST_RUN["at"] = out["last_run_at"]
        _DRIFT_LAST_RUN["result"] = out
        return out
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "drift check timed out (120s)")
    except Exception as e:
        logger.exception(f"drift status failed: {e}")
        raise HTTPException(500, f"drift check failed: {e}")
