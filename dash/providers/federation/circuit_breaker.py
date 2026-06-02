"""Federation circuit breaker.

After N consecutive failures (timeout / OOM / merge error) for a project,
opens circuit and rejects new federated queries until cooldown elapses.

States: closed (normal), open (rejecting), half_open (testing recovery).
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3
OPEN_DURATION_S = 300   # 5 min cooldown


@dataclass
class CircuitState:
    project_slug: str
    consecutive_failures: int = 0
    is_open: bool = False
    open_until: Optional[datetime] = None
    last_error: Optional[str] = None


def check(project_slug: str, dash_engine=None) -> CircuitState:
    """Read current state. If open + cooldown elapsed, auto-close (half_open)."""
    state = CircuitState(project_slug=project_slug)
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            r = conn.execute(text(
                "SELECT consecutive_failures, open_until, last_error "
                "FROM public.dash_federation_circuit WHERE project_slug = :s"
            ), {"s": project_slug}).fetchone()
        if r:
            state.consecutive_failures = int(r[0] or 0)
            state.last_error = r[2]
            if r[1]:
                # open_until in DB — check if still open
                open_until = r[1]
                if isinstance(open_until, datetime):
                    if datetime.utcnow() < open_until.replace(tzinfo=None):
                        state.is_open = True
                        state.open_until = open_until
    except Exception as e:
        logger.debug(f"circuit check: {e}")
    return state


def record_success(project_slug: str, dash_engine=None) -> None:
    """Reset failure count + close circuit."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_federation_circuit "
                "(project_slug, consecutive_failures, open_until, last_error, updated_at) "
                "VALUES (:s, 0, NULL, NULL, NOW()) "
                "ON CONFLICT (project_slug) DO UPDATE SET "
                " consecutive_failures = 0, open_until = NULL, "
                " last_error = NULL, updated_at = NOW()"
            ), {"s": project_slug})
            conn.commit()
    except Exception as e:
        logger.debug(f"record_success: {e}")


def record_failure(project_slug: str, error: str,
                    *, dash_engine=None,
                    threshold: int = FAILURE_THRESHOLD,
                    open_duration_s: int = OPEN_DURATION_S) -> CircuitState:
    """Increment failure count. Open circuit if threshold hit."""
    state = check(project_slug, dash_engine=dash_engine)
    state.consecutive_failures += 1

    will_open = state.consecutive_failures >= threshold
    open_until = (datetime.utcnow() + timedelta(seconds=open_duration_s)) if will_open else None

    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_federation_circuit "
                "(project_slug, consecutive_failures, open_until, last_error, updated_at) "
                "VALUES (:s, :n, :until, :err, NOW()) "
                "ON CONFLICT (project_slug) DO UPDATE SET "
                " consecutive_failures = EXCLUDED.consecutive_failures, "
                " open_until = EXCLUDED.open_until, "
                " last_error = EXCLUDED.last_error, "
                " updated_at = NOW()"
            ), {
                "s": project_slug,
                "n": state.consecutive_failures,
                "until": open_until,
                "err": (error or "")[:300],
            })
            conn.commit()
    except Exception as e:
        logger.debug(f"record_failure: {e}")

    if will_open:
        state.is_open = True
        state.open_until = open_until
        state.last_error = error

    return state


def reset(project_slug: str, dash_engine=None) -> bool:
    """Manually reset circuit (admin action)."""
    record_success(project_slug, dash_engine=dash_engine)
    return True
