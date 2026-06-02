"""Connector subsystem — pluggable read-only data source clients.

Public surface:
    - base.ConnectorClient (ABC)
    - registry.REGISTRY, list_connectors, resolve_client_class
    - crypto.encrypt_credentials, decrypt_credentials
    - access.can_user_use
    - schemas.* (Pydantic config + credentials models)
    - instantiate_client(conn_row) — build client from a saved row
    - audit(...) — best-effort audit row writer
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def instantiate_client(conn_row: dict) -> Any:
    """Build a ConnectorClient instance from a saved dash_connections row.

    Decrypts credentials and merges with config to call the client ctor.
    """
    from dash.connectors.crypto import decrypt_credentials
    from dash.connectors.registry import resolve_client_class

    cls = resolve_client_class(conn_row["connector_type"])
    creds = decrypt_credentials(conn_row["credentials"])
    config = conn_row.get("config") or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception:
            config = {}
    if not isinstance(config, dict):
        config = {}
    if not isinstance(creds, dict):
        creds = {}

    # Always-passed metadata kwargs. Non-OBO clients ignore them via **kwargs.
    extra = {
        "connection_id": str(conn_row["id"]) if conn_row.get("id") is not None else None,
        "auth_mode": (config.get("auth_mode") or "service_principal"),
    }
    return cls(**config, **creds, **extra)


def audit(
    connection_id,
    user_id,
    action: str,
    sql: str | None = None,
    row_count: int | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Best-effort audit row into dash.dash_connection_audit. Never raises."""
    try:
        from db.session import get_write_engine
        from sqlalchemy import text

        eng = get_write_engine()
        with eng.begin() as c:
            c.execute(
                text(
                    """
                    INSERT INTO dash.dash_connection_audit
                    (connection_id, user_id, action, sql_text, row_count, duration_ms, error)
                    VALUES (:cid, :uid, :a, :s, :r, :d, :e)
                    """
                ),
                {
                    "cid": str(connection_id) if connection_id is not None else None,
                    "uid": int(user_id) if user_id is not None else None,
                    "a": action,
                    "s": sql,
                    "r": int(row_count) if row_count is not None else None,
                    "d": int(duration_ms) if duration_ms is not None else None,
                    "e": error,
                },
            )
    except Exception as e:
        logger.debug(f"audit insert failed: {e}")
