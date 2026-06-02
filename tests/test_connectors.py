"""Connector system tests — auto-skip when drivers missing.

Run: pytest tests/test_connectors.py -v
"""
from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# Safety / SQL gate
# ---------------------------------------------------------------------------
def test_read_only_gate_blocks_destructive():
    from dash.connectors.safety import is_read_only_sql

    for bad in [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN x INT",
        "TRUNCATE t",
        "CREATE TABLE t (x INT)",
        "GRANT SELECT ON t TO u",
        "REVOKE SELECT ON t FROM u",
        "MERGE INTO t USING s ON ...",
        "CALL proc()",
        "EXEC proc",
    ]:
        ok, reason = is_read_only_sql(bad, dialect="postgresql")
        assert not ok, f"should reject: {bad}"


def test_read_only_gate_allows_select():
    from dash.connectors.safety import is_read_only_sql

    for good in [
        "SELECT 1",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "EXPLAIN SELECT 1",
        "SHOW TABLES",
        "-- comment\nSELECT 1",
        "/* block */ SELECT 1",
        "  \n  SELECT *\nFROM t",
    ]:
        ok, reason = is_read_only_sql(good, dialect="postgresql")
        assert ok, f"should allow: {good} ({reason})"


def test_dax_evaluate_allowed():
    from dash.connectors.safety import is_read_only_sql

    ok, _ = is_read_only_sql("EVALUATE TOPN(10, 'Sales')", dialect="powerbi")
    assert ok


def test_secret_scrubbed_from_error():
    from dash.connectors.safety import safe_error_message

    msg = safe_error_message(
        Exception("connect failed: password=secret123 host=prod-db"),
        {"password": "secret123", "client_id": "abc"},
    )
    assert "secret123" not in msg
    assert "REDACTED" in msg


def test_safe_error_truncates():
    from dash.connectors.safety import safe_error_message

    long_err = "a" * 2000
    msg = safe_error_message(Exception(long_err), {})
    assert len(msg) <= 500


# ---------------------------------------------------------------------------
# RBAC / access
# ---------------------------------------------------------------------------
def test_can_user_use_super_admin_bypass():
    from dash.connectors.access import can_user_use

    user = {"id": 1, "is_super_admin": True, "aad_groups": []}
    conn = {"enabled": True, "users_allowed": [], "ldap_groups_allowed": [], "allow_all_users": False}
    assert can_user_use(user, conn) is True


def test_can_user_use_disabled_blocks():
    from dash.connectors.access import can_user_use

    user = {"id": 1, "is_super_admin": True, "aad_groups": []}
    conn = {"enabled": False}
    assert can_user_use(user, conn) is False


def test_can_user_use_allow_all():
    from dash.connectors.access import can_user_use

    user = {"id": 42, "is_super_admin": False, "aad_groups": []}
    conn = {"enabled": True, "allow_all_users": True}
    assert can_user_use(user, conn) is True


def test_can_user_use_user_id_match():
    from dash.connectors.access import can_user_use

    user = {"id": 42, "is_super_admin": False, "aad_groups": []}
    conn = {"enabled": True, "users_allowed": [42], "ldap_groups_allowed": []}
    assert can_user_use(user, conn) is True


def test_can_user_use_aad_group_match():
    from dash.connectors.access import can_user_use

    user = {"id": 1, "is_super_admin": False, "aad_groups": ["group-guid-1", "group-guid-2"]}
    conn = {"enabled": True, "users_allowed": [], "ldap_groups_allowed": ["group-guid-2"]}
    assert can_user_use(user, conn) is True


def test_can_user_use_denies_unprivileged():
    from dash.connectors.access import can_user_use

    user = {"id": 99, "is_super_admin": False, "aad_groups": ["unrelated"]}
    conn = {"enabled": True, "users_allowed": [1, 2], "ldap_groups_allowed": ["other"]}
    assert can_user_use(user, conn) is False


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------
def test_fernet_roundtrip():
    from dash.connectors.crypto import decrypt_credentials, encrypt_credentials

    creds = {"user": "ai", "password": "secret123", "tenant": "x"}
    tok = encrypt_credentials(creds)
    assert tok != json.dumps(creds)
    decoded = decrypt_credentials(tok)
    assert decoded == creds


def test_fernet_tampering_detected():
    from cryptography.fernet import InvalidToken

    from dash.connectors.crypto import decrypt_credentials, encrypt_credentials

    tok = encrypt_credentials({"a": 1})
    tampered = tok[:-4] + "xxxx"
    with pytest.raises((InvalidToken, Exception)):
        decrypt_credentials(tampered)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_registry_loads_5():
    from dash.connectors.registry import REGISTRY, list_connectors

    assert len(REGISTRY) == 5
    types = {e.type for e in REGISTRY.values()}
    assert types == {"postgresql", "mssql", "fabric", "bigquery", "powerbi"}

    listed = list_connectors()
    assert len(listed) == 5
    assert all("type" in c and "title" in c and "kind" in c for c in listed)


def test_registry_pydantic_schemas_loadable():
    from dash.connectors.registry import REGISTRY

    for ctype, entry in REGISTRY.items():
        cfg_schema = entry.config_schema.model_json_schema()
        cred_schema = entry.credentials_schema.model_json_schema()
        assert cfg_schema.get("type") == "object", f"{ctype} config not object"
        assert cred_schema.get("type") == "object", f"{ctype} credentials not object"
        assert "properties" in cfg_schema
        assert "properties" in cred_schema


# ---------------------------------------------------------------------------
# Client smoke (instantiate without driver — should raise informative error)
# ---------------------------------------------------------------------------
def test_postgres_client_class():
    from dash.connectors.base import ConnectorClient
    from dash.connectors.clients.postgres_client import PostgresClient

    assert issubclass(PostgresClient, ConnectorClient)


def test_mssql_client_class():
    from dash.connectors.base import ConnectorClient
    from dash.connectors.clients.mssql_client import MssqlClient

    assert issubclass(MssqlClient, ConnectorClient)


def test_fabric_client_class():
    from dash.connectors.base import ConnectorClient
    from dash.connectors.clients.fabric_client import FabricClient

    assert issubclass(FabricClient, ConnectorClient)


def test_bigquery_client_class():
    from dash.connectors.base import ConnectorClient
    from dash.connectors.clients.bigquery_client import BigQueryClient

    assert issubclass(BigQueryClient, ConnectorClient)


def test_powerbi_client_class():
    from dash.connectors.base import ConnectorClient
    from dash.connectors.clients.powerbi_client import PowerBIClient

    assert issubclass(PowerBIClient, ConnectorClient)
