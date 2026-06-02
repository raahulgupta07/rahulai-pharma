"""Daemon leader election — pick exactly ONE worker/pod to run background daemons.

Replaces the broken ``WORKER_RANK == 0`` gate (gunicorn ``worker.age`` starts at
1 and drifts to N on respawn, so rank 0 is essentially never present → every
daemon gated on it silently never ran).

PgBouncer-safe: uses a heartbeat row + plain ``UPDATE`` (no session-level
``pg_advisory_lock``, which doesn't survive transaction-mode pooling). Portable
across compose / K8s (no rank assumptions, no direct-Postgres connection needed).

Model:
- ``dash.dash_daemon_leader`` single row (id=1) with ``holder`` + ``heartbeat``.
- A worker becomes leader by atomically claiming the row when it's unowned or the
  current holder's heartbeat is stale (> LEASE_S old).
- The leader renews its heartbeat on a background thread every RENEW_S. If the
  leader dies, its heartbeat goes stale and (on its next startup, or any worker's
  retry) another worker claims leadership → daemons resume.

Usage (in app lifespan):
    from dash.runtime.daemon_leader import try_become_leader
    if try_become_leader():   # exactly one worker returns True (best-effort)
        ... start daemons ...
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid

from sqlalchemy import text

logger = logging.getLogger(__name__)

LEASE_S = 30      # a holder is considered dead if heartbeat older than this
RENEW_S = 10      # leader renews heartbeat this often

_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
_is_leader = False
_renew_thread: threading.Thread | None = None


def _engine():
    # Unguarded engine that can write public.* (same one feature_config uses).
    from dash.tools.skill_refinery import _get_engine
    return _get_engine()


def _bootstrap() -> None:
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS dash.dash_daemon_leader ("
            "  id INT PRIMARY KEY,"
            "  holder TEXT,"
            "  heartbeat TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO dash.dash_daemon_leader (id, holder, heartbeat) "
            "VALUES (1, NULL, now() - INTERVAL '1 hour') "
            "ON CONFLICT (id) DO NOTHING"
        ))


def _claim() -> bool:
    """Atomically claim leadership if unowned, ours, or the holder is stale.

    Returns True if this worker now holds leadership.
    """
    eng = _engine()
    with eng.begin() as conn:
        res = conn.execute(text(
            "UPDATE dash.dash_daemon_leader "
            "SET holder = :me, heartbeat = now() "
            "WHERE id = 1 AND ("
            "  holder IS NULL OR holder = :me "
            "  OR heartbeat < now() - make_interval(secs => :lease)"
            ")"
        ), {"me": _WORKER_ID, "lease": LEASE_S})
        return (res.rowcount or 0) == 1


def _renew_loop() -> None:
    """Background: keep our heartbeat fresh. If we ever LOSE the row (another
    worker took over because we stalled), stop renewing — we are no longer
    leader (daemons keep running in this process, but we won't fight for it)."""
    global _is_leader
    while _is_leader:
        time.sleep(RENEW_S)
        try:
            eng = _engine()
            with eng.begin() as conn:
                res = conn.execute(text(
                    "UPDATE dash.dash_daemon_leader SET heartbeat = now() "
                    "WHERE id = 1 AND holder = :me"
                ), {"me": _WORKER_ID})
            if (res.rowcount or 0) != 1:
                logger.warning("daemon leadership lost (another worker took over); stopping renew")
                _is_leader = False
                return
        except Exception as e:
            logger.debug("daemon heartbeat renew failed (will retry): %s", e)


def try_become_leader() -> bool:
    """Attempt to become THE daemon-running worker. Idempotent per process.

    Returns True for exactly one worker (best-effort — under a stale-takeover
    race two workers could briefly both run, which is acceptable for these
    daemons; the heartbeat converges to one). Fail-OPEN: if the DB/leader infra
    is unavailable, returns True so a single-worker / misconfigured deploy still
    runs daemons rather than silently running none.

    Retry semantics: on initial loss (another holder appears alive), retry every
    5s for up to LEASE_S + 15s. Force-recreate timing window — old container's
    heartbeat may still be < 30s old at new container startup, so first claim
    fails. After old holder dies, its heartbeat goes stale → retry wins.
    """
    global _is_leader, _renew_thread
    if _is_leader:
        return True
    try:
        _bootstrap()
        # First-try fast path
        if _claim():
            _is_leader = True
            _renew_thread = threading.Thread(
                target=_renew_loop, daemon=True, name="daemon-leader-heartbeat"
            )
            _renew_thread.start()
            logger.info("this worker is the daemon leader (worker_id=%s)", _WORKER_ID)
            return True
        # Lost. Spawn background re-try thread so lifespan returns fast but daemon
        # eventually starts when old leader expires. Lifespan code can re-check
        # _is_leader to detect post-startup acquisition.
        def _retry_until_won() -> None:
            global _is_leader, _renew_thread
            deadline = time.time() + (LEASE_S + 15)  # 45s window — covers force-recreate
            while time.time() < deadline:
                time.sleep(5)
                try:
                    if _claim():
                        _is_leader = True
                        _renew_thread = threading.Thread(
                            target=_renew_loop, daemon=True, name="daemon-leader-heartbeat"
                        )
                        _renew_thread.start()
                        logger.info(
                            "daemon leadership acquired via retry (worker_id=%s) — "
                            "lifespan-daemons need re-bootstrap", _WORKER_ID
                        )
                        # Fire any registered post-claim callbacks (lifespan registers
                        # a daemon-bootstrap closure here)
                        for cb in list(_POST_CLAIM_CALLBACKS):
                            try:
                                cb()
                            except Exception as _cbe:
                                logger.warning("post-claim callback failed: %s", _cbe)
                        return
                except Exception as _re:
                    logger.debug("leader retry attempt failed: %s", _re)
            logger.info("daemon leader retry window exhausted; another worker holds leadership")

        threading.Thread(target=_retry_until_won, daemon=True, name="daemon-leader-retry").start()
        logger.info("another worker holds daemon leadership; this worker serves traffic only (will retry %ds)", LEASE_S + 15)
        return False
    except Exception as e:
        # Fail-open: better to run daemons (maybe duplicated) than run none.
        logger.warning("daemon leader election failed (%s) — failing OPEN (running daemons)", e)
        _is_leader = True
        return True


# Lifespan can register callbacks here that fire when leadership is acquired
# via the retry path (not initial claim). The callback should start the same
# daemons that lifespan would have started.
_POST_CLAIM_CALLBACKS: list = []


def register_post_claim_callback(cb) -> None:
    """Register a callback fired once when leadership is acquired via retry."""
    _POST_CLAIM_CALLBACKS.append(cb)
