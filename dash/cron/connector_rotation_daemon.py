"""Connector Secret Rotation Reminder Daemon.

Scans dash.dash_connections for stale secret rotations and inserts
notifications into public.dash_notifications (if the table exists).

Disable via CONNECTOR_ROTATION_DAEMON_DISABLED=1.
Interval via CONNECTOR_ROTATION_INTERVAL_SECONDS (default 86400 / 24h).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

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

DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60  # 24 h
ROTATION_WARN_DAYS = 90   # ≥ 90d since rotation → "warn"
ROTATION_CRITICAL_DAYS = 120  # ≥ 120d → "critical"
WARNING_COOLDOWN_DAYS = 7  # suppress repeat warning within 7d


# ── Helpers ───────────────────────────────────────────────────────────


def _engine():
    try:
        from db.session import get_sql_engine
    except Exception:
        from db import get_sql_engine  # type: ignore[no-redef]
    return get_sql_engine()


def _write_engine():
    try:
        from db.session import get_write_engine
    except Exception:
        logger.warning("connector_rotation: get_write_engine not available, using read engine")
        return _engine()
    return get_write_engine()


def _notifications_table_exists(conn) -> bool:
    """Check whether public.dash_notifications exists."""
    from sqlalchemy import text as _t
    row = conn.execute(
        _t(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'dash_notifications' "
            "LIMIT 1"
        )
    ).fetchone()
    return row is not None


# ── Core cycle ────────────────────────────────────────────────────────


def run_cycle() -> dict:
    """Scan for stale rotations + emit notifications. Returns summary dict."""
    out: dict = {"scanned": 0, "warned": 0, "skipped": 0, "errors": 0}

    eng = _engine()
    if eng is None:
        logger.warning("connector_rotation: no engine, aborting cycle")
        return out

    write_eng = _write_engine()

    # Fetch connections whose secret is overdue AND cooldown has passed.
    from sqlalchemy import text as _t

    try:
        with eng.connect() as c:
            rows = c.execute(
                _t(
                    """
                    SELECT
                        id,
                        name,
                        connector_type,
                        owner_user_id,
                        secret_rotated_at,
                        secret_rotation_alert_days,
                        last_rotation_warning_at,
                        EXTRACT(EPOCH FROM (now() - COALESCE(secret_rotated_at, now())))
                            / 86400 AS days_since
                    FROM dash.dash_connections
                    WHERE enabled = true
                      AND secret_rotated_at IS NOT NULL
                      AND EXTRACT(EPOCH FROM (now() - secret_rotated_at)) / 86400
                          >= COALESCE(secret_rotation_alert_days, :warn_days)
                      AND (
                          last_rotation_warning_at IS NULL
                          OR last_rotation_warning_at < now() - (:cooldown_days * INTERVAL '1 day')
                      )
                    ORDER BY secret_rotated_at ASC
                    """
                ),
                {
                    "warn_days": ROTATION_WARN_DAYS,
                    "cooldown_days": WARNING_COOLDOWN_DAYS,
                },
            ).fetchall()
    except Exception:
        logger.exception("connector_rotation: failed to query connections")
        return out

    out["scanned"] = len(rows)

    # Check once whether notifications table is present.
    try:
        with write_eng.connect() as c:
            has_notif_table = _notifications_table_exists(c)
    except Exception:
        has_notif_table = False

    # Phase 11: per-connector feature-flag gate.
    # Each row has columns (id, name, connector_type, owner_user_id, ...).
    # Look up project_slug via owner if available; otherwise apply globally.
    # If feature flag tools.external_connectors is False for a project, skip its
    # connectors. Fail-soft on any flag-lookup error → process as before (warn-only).
    try:
        from dash.feature_config import get_feature_config  # noqa: F401  (lazy use below)
    except Exception:
        get_feature_config = None  # type: ignore[assignment]

    def _flag_enabled_for_owner(owner_id) -> bool:
        if get_feature_config is None or owner_id is None:
            return True
        try:
            # Best-effort: look up any project owned by this user and read its
            # feature_config.tools.external_connectors. If not found → allow.
            from sqlalchemy import text as _t
            with eng.connect() as c:
                pr = c.execute(
                    _t("SELECT slug FROM public.dash_projects WHERE user_id = :u LIMIT 1"),
                    {"u": int(owner_id)},
                ).fetchone()
            slug = pr[0] if pr else None
            if not slug:
                return True
            cfg = get_feature_config(slug) or {}
            return bool((cfg.get("tools") or {}).get("external_connectors", True))
        except Exception as e:  # noqa: BLE001
            logger.warning("connector_rotation: feature-flag lookup failed: %s", e)
            return True

    for row in rows:
        conn_id = str(row[0])
        owner = row[3] if len(row) > 3 else None
        try:
            # Per-connector feature-flag gate: skip if external_connectors disabled.
            if not _flag_enabled_for_owner(owner):
                logger.debug(
                    "connector_rotation: skipping conn=%s (external_connectors disabled)",
                    conn_id,
                )
                out["skipped"] += 1
                continue
            # Phase 11: wrap each connector telemetry/error-rate query in try/except.
            # Fail-soft per connector — one bad row logs WARN, never kills the batch.
            _process_connection(
                row=row,
                write_eng=write_eng,
                has_notif_table=has_notif_table,
            )
            out["warned"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "connector_rotation: error processing connection id=%s: %s",
                conn_id, e, exc_info=True,
            )
            out["errors"] += 1
            continue

    logger.info(
        "connector_rotation: cycle_done scanned=%d warned=%d errors=%d",
        out["scanned"],
        out["warned"],
        out["errors"],
    )
    return out


def _process_connection(row, write_eng, has_notif_table: bool) -> None:
    """Emit notification for one connection + update last_rotation_warning_at."""
    from sqlalchemy import text as _t

    conn_id = str(row[0])
    name = row[1] or "unknown"
    connector_type = row[2] or "unknown"
    owner_user_id = row[3]
    secret_rotated_at = row[4]
    days_since = float(row[7]) if row[7] is not None else 0.0

    severity = "critical" if days_since >= ROTATION_CRITICAL_DAYS else "warn"

    rotated_at_iso = (
        secret_rotated_at.isoformat()
        if hasattr(secret_rotated_at, "isoformat")
        else str(secret_rotated_at)
    )

    payload = {
        "connection_id": conn_id,
        "name": name,
        "connector_type": connector_type,
        "days_overdue": round(days_since, 1),
        "rotated_at": rotated_at_iso,
    }
    payload_json = json.dumps(payload)

    with write_eng.begin() as c:
        # Insert notification if table exists.
        if has_notif_table and owner_user_id is not None:
            try:
                c.execute(
                    _t(
                        """
                        INSERT INTO public.dash_notifications
                            (user_id, type, severity, payload)
                        VALUES
                            (:uid, 'connector.rotation_warning', :sev, CAST(:p AS jsonb))
                        """
                    ),
                    {
                        "uid": int(owner_user_id),
                        "sev": severity,
                        "p": payload_json,
                    },
                )
            except Exception:
                logger.warning(
                    "connector_rotation: could not insert notification for conn=%s",
                    conn_id,
                    exc_info=True,
                )

        # Always update last_rotation_warning_at so cooldown applies.
        c.execute(
            _t(
                """
                UPDATE dash.dash_connections
                   SET last_rotation_warning_at = now()
                 WHERE id = :cid
                """
            ),
            {"cid": conn_id},
        )

    logger.info(
        "connector_rotation: warned conn=%s name=%r days=%.0f severity=%s",
        conn_id,
        name,
        days_since,
        severity,
    )


# ── Async loop ────────────────────────────────────────────────────────


def _interval_seconds() -> int:
    raw = os.getenv("CONNECTOR_ROTATION_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
        if v > 0:
            return v
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def _is_disabled() -> bool:
    return (
        os.getenv("CONNECTOR_ROTATION_DAEMON_DISABLED", "").lower()
        in ("1", "true", "yes")
    )


async def connector_rotation_loop() -> None:
    """Forever-loop: run cycle, sleep, repeat. Crash-resistant."""
    if _is_disabled():
        logger.info("connector_rotation: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("connector_rotation: starting (interval=%ds)", interval)
    while True:
        try:
            with trace_span("cron.connector_rotation", kind="cron"):
                await asyncio.to_thread(run_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("connector_rotation: cycle crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_cycle", "connector_rotation_loop"]
