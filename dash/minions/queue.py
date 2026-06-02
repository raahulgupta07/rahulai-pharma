"""Dash B5 — Postgres-only durable job queue (no Redis/Celery).

Lease-based claim using SELECT FOR UPDATE SKIP LOCKED so multiple workers
can poll the same kinds without stepping on each other. Crash-safe: a
worker that dies mid-job leaves the row with an expired lease; the next
claim_next() picks it up automatically.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _row_to_dict(r) -> Dict[str, Any]:
    d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
    # Coerce datetimes to iso for JSON friendliness on API layer
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _backoff_seconds(attempts: int) -> int:
    # 30s, 2min, 8min, 32min ...
    return min(30 * (4 ** max(attempts - 1, 0)), 60 * 60)


def enqueue(
    project: Optional[str],
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
    priority: int = 5,
    scheduled_at: Optional[datetime] = None,
    max_attempts: int = 3,
) -> int:
    """Enqueue a minion. Returns the minion id."""
    eng = _engine()
    pl = json.dumps(payload or {})
    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO dash.dash_minions
                  (project_slug, kind, payload, priority, scheduled_at, max_attempts)
                VALUES
                  (CAST(:p AS TEXT), :k, CAST(:pl AS JSONB), :pr,
                   COALESCE(CAST(:sa AS TIMESTAMPTZ), now()), :ma)
                RETURNING id
                """
            ),
            {
                "p": project,
                "k": kind,
                "pl": pl,
                "pr": int(priority),
                "sa": scheduled_at.isoformat() if scheduled_at else None,
                "ma": int(max_attempts),
            },
        ).first()
        return int(row[0])


def claim_next(
    worker_id: str,
    kinds: Optional[List[str]] = None,
    lease_seconds: int = 300,
) -> Optional[Dict[str, Any]]:
    """Atomically claim the next eligible pending minion.

    Uses SELECT FOR UPDATE SKIP LOCKED so concurrent workers never collide.
    Picks rows that are pending OR have an expired lease (crash recovery).
    """
    eng = _engine()
    kinds_filter = bool(kinds)
    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                WITH cte AS (
                  SELECT id FROM dash.dash_minions
                   WHERE (
                           status = 'pending'
                           OR (status = 'running' AND lease_until IS NOT NULL AND lease_until < now())
                         )
                     AND scheduled_at <= now()
                     AND (NOT :use_kinds OR kind = ANY(:kinds))
                   ORDER BY priority ASC, scheduled_at ASC, id ASC
                   FOR UPDATE SKIP LOCKED
                   LIMIT 1
                )
                UPDATE dash.dash_minions m
                   SET status      = 'running',
                       claimed_by  = :worker,
                       lease_until = now() + (:lease || ' seconds')::interval,
                       attempts    = m.attempts + 1,
                       started_at  = COALESCE(m.started_at, now())
                  FROM cte
                 WHERE m.id = cte.id
                 RETURNING m.*
                """
            ),
            {
                "worker": worker_id,
                "lease": int(lease_seconds),
                "use_kinds": kinds_filter,
                "kinds": list(kinds or []),
            },
        ).first()
        if row is None:
            return None
        return _row_to_dict(row)


def complete(minion_id: int, result: Optional[Dict[str, Any]] = None) -> bool:
    eng = _engine()
    with eng.begin() as conn:
        res = conn.execute(
            text(
                """
                UPDATE dash.dash_minions
                   SET status = 'done',
                       finished_at = now(),
                       result = CAST(:r AS JSONB),
                       lease_until = NULL,
                       error = NULL
                 WHERE id = :id
                """
            ),
            {"id": int(minion_id), "r": json.dumps(result or {})},
        )
        return res.rowcount > 0


def fail(minion_id: int, error: str, retry: bool = True) -> Dict[str, Any]:
    """Mark a minion failed. Retries with backoff until max_attempts reached."""
    eng = _engine()
    with eng.begin() as conn:
        cur = conn.execute(
            text("SELECT attempts, max_attempts FROM dash.dash_minions WHERE id = :id"),
            {"id": int(minion_id)},
        ).first()
        if cur is None:
            return {"ok": False, "error": "not_found"}
        attempts = int(cur[0] or 0)
        max_attempts = int(cur[1] or 3)
        will_retry = retry and attempts < max_attempts
        if will_retry:
            backoff = _backoff_seconds(attempts)
            conn.execute(
                text(
                    """
                    UPDATE dash.dash_minions
                       SET status = 'pending',
                           scheduled_at = now() + (:bo || ' seconds')::interval,
                           lease_until = NULL,
                           claimed_by = NULL,
                           error = :err
                     WHERE id = :id
                    """
                ),
                {"id": int(minion_id), "bo": backoff, "err": (error or "")[:2000]},
            )
            return {"ok": True, "retried": True, "backoff_seconds": backoff, "attempts": attempts}
        conn.execute(
            text(
                """
                UPDATE dash.dash_minions
                   SET status = 'failed',
                       finished_at = now(),
                       lease_until = NULL,
                       error = :err
                 WHERE id = :id
                """
            ),
            {"id": int(minion_id), "err": (error or "")[:2000]},
        )
        return {"ok": True, "retried": False, "attempts": attempts}


def extend_lease(minion_id: int, more_seconds: int) -> bool:
    eng = _engine()
    with eng.begin() as conn:
        res = conn.execute(
            text(
                """
                UPDATE dash.dash_minions
                   SET lease_until = GREATEST(COALESCE(lease_until, now()), now())
                                     + (:s || ' seconds')::interval
                 WHERE id = :id AND status = 'running'
                """
            ),
            {"id": int(minion_id), "s": int(more_seconds)},
        )
        return res.rowcount > 0


def list_minions(
    project: Optional[str] = None,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    eng = _engine()
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, project_slug, kind, status, priority, attempts, max_attempts,
                       claimed_by, lease_until, scheduled_at, started_at, finished_at,
                       created_at, error, payload, result
                  FROM dash.dash_minions
                 WHERE (CAST(:p AS TEXT) IS NULL OR project_slug = :p)
                   AND (CAST(:s AS TEXT) IS NULL OR status = :s)
                   AND (CAST(:k AS TEXT) IS NULL OR kind = :k)
                 ORDER BY id DESC
                 LIMIT :lim OFFSET :off
                """
            ),
            {"p": project, "s": status, "k": kind, "lim": limit, "off": offset},
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_minion(minion_id: int) -> Optional[Dict[str, Any]]:
    eng = _engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM dash.dash_minions WHERE id = :id"),
            {"id": int(minion_id)},
        ).first()
        return _row_to_dict(row) if row else None


def cancel(minion_id: int) -> bool:
    eng = _engine()
    with eng.begin() as conn:
        res = conn.execute(
            text(
                """
                UPDATE dash.dash_minions
                   SET status = 'cancelled',
                       finished_at = now(),
                       lease_until = NULL
                 WHERE id = :id AND status IN ('pending','running')
                """
            ),
            {"id": int(minion_id)},
        )
        return res.rowcount > 0
