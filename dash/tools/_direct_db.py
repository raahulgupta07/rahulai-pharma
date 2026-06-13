"""Bounded direct-Postgres connections for tools that bypass PgBouncer.

Several pharma/AGE tools open a RAW psycopg connection straight to `dash-db`
(PgBouncer can't proxy AGE/`SET search_path` session state the way these tools
need). Each agent turn may fire several such tools, and the embed/chat path runs
many turns concurrently — so without a cap these direct connections count
straight against Postgres `max_connections` and a load spike yields
`FATAL: sorry, too many clients already`.

`direct_connect(**kw)` is a drop-in for `psycopg.connect(**kw)` that throttles the
TOTAL number of simultaneous direct connections via one process-wide semaphore.
The returned object proxies the real connection; its `.close()` releases the
permit, and a `__del__` backstop releases it even if a caller forgets or an error
fires between connect and the caller's `finally` (the prior leak window).

Tune the cap with `DIRECT_DB_MAX_CONN` (default 16) — keep it well under the DB's
`max_connections` minus the PgBouncer reservation. `DIRECT_DB_WAIT` (default 15s)
bounds how long a tool waits for a free slot before failing loudly.
"""
from __future__ import annotations

import os
import threading
import logging

log = logging.getLogger("dash.direct_db")

_MAX = max(1, int(os.getenv("DIRECT_DB_MAX_CONN", "16")))
_WAIT = float(os.getenv("DIRECT_DB_WAIT", "15"))
_sem = threading.BoundedSemaphore(_MAX)


class _Guarded:
    """Proxy around a psycopg connection that releases its pool permit on close."""

    def __init__(self, conn) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_released", False)

    def _release(self) -> None:
        if not object.__getattribute__(self, "_released"):
            object.__setattr__(self, "_released", True)
            try:
                _sem.release()
            except Exception:
                pass

    # Proxy everything else (cursor, commit, execute, autocommit, ...) to the real conn.
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def close(self) -> None:
        try:
            object.__getattribute__(self, "_conn").close()
        finally:
            self._release()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):
        # Backstop: if the caller never closed (e.g. setup raised before the
        # caller got a handle), CPython refcounting drops this promptly and we
        # release the permit so the cap can't bleed down over a long-lived run.
        try:
            self._release()
        except Exception:
            pass


def direct_connect(**kw):
    """Drop-in for psycopg.connect(**kw), capped by a process-wide semaphore."""
    import psycopg

    if not _sem.acquire(timeout=_WAIT):
        raise RuntimeError(
            f"direct DB pool exhausted (DIRECT_DB_MAX_CONN={_MAX}); "
            f"waited {_WAIT}s for a free connection slot"
        )
    try:
        conn = psycopg.connect(**kw)
    except Exception:
        _sem.release()
        raise
    return _Guarded(conn)
