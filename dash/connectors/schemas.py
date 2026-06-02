"""Pydantic config + credentials schemas — frozen contract §6.

All 10 classes exported by name. Fields using alias="schema" set
populate_by_name=True so callers can pass either `schema=` or `schema_=`.

UI form-renderer hints live in `json_schema_extra={"ui:type": "..."}`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
class PostgresConfig(BaseModel):
    host: str = Field(json_schema_extra={"ui:type": "string"})
    port: int = Field(default=5432, json_schema_extra={"ui:type": "number"})
    database: str = Field(json_schema_extra={"ui:type": "string"})
    schema_: str | None = Field(
        default="public", alias="schema", json_schema_extra={"ui:type": "string"}
    )
    sslmode: str = Field(default="prefer", json_schema_extra={"ui:type": "string"})

    class Config:
        populate_by_name = True


class PostgresCredentials(BaseModel):
    user: str = Field(json_schema_extra={"ui:type": "string"})
    password: str = Field(json_schema_extra={"ui:type": "password"})


# ---------------------------------------------------------------------------
# MSSQL (SQL auth)
# ---------------------------------------------------------------------------
class MssqlConfig(BaseModel):
    host: str = Field(json_schema_extra={"ui:type": "string"})
    port: int = Field(default=1433, json_schema_extra={"ui:type": "number"})
    database: str = Field(json_schema_extra={"ui:type": "string"})
    schema_: str = Field(
        default="dbo", alias="schema", json_schema_extra={"ui:type": "string"}
    )
    encrypt: bool = Field(default=True, json_schema_extra={"ui:type": "boolean"})
    trust_server_certificate: bool = Field(
        default=False, json_schema_extra={"ui:type": "boolean"}
    )

    class Config:
        populate_by_name = True


class MssqlCredentials(BaseModel):
    user: str = Field(json_schema_extra={"ui:type": "string"})
    password: str = Field(json_schema_extra={"ui:type": "password"})


# ---------------------------------------------------------------------------
# Microsoft Fabric (Azure AD Service Principal)
# ---------------------------------------------------------------------------
class FabricConfig(BaseModel):
    sql_endpoint: str = Field(
        description="xxxxx.datawarehouse.fabric.microsoft.com",
        json_schema_extra={"ui:type": "string"},
    )
    database: str = Field(json_schema_extra={"ui:type": "string"})
    schema_: str = Field(
        default="dbo", alias="schema", json_schema_extra={"ui:type": "string"}
    )
    query_timeout_seconds: int = Field(
        default=120, json_schema_extra={"ui:type": "number"}
    )

    class Config:
        populate_by_name = True


class FabricCredentials(BaseModel):
    tenant_id: str = Field(json_schema_extra={"ui:type": "string"})
    client_id: str = Field(json_schema_extra={"ui:type": "string"})
    client_secret: str = Field(json_schema_extra={"ui:type": "password"})


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------
class BigQueryConfig(BaseModel):
    project_id: str = Field(json_schema_extra={"ui:type": "string"})
    dataset: str = Field(json_schema_extra={"ui:type": "string"})
    location: str = Field(default="US", json_schema_extra={"ui:type": "string"})
    maximum_bytes_billed: int | None = Field(
        default=None, json_schema_extra={"ui:type": "number"}
    )
    use_query_cache: bool = Field(default=True, json_schema_extra={"ui:type": "boolean"})


class BigQueryCredentials(BaseModel):
    credentials_json: str = Field(
        description="Full service-account JSON pasted in",
        json_schema_extra={"ui:type": "textarea"},
    )


# ---------------------------------------------------------------------------
# PowerBI (Azure AD Service Principal, DAX)
# ---------------------------------------------------------------------------
class PowerBIConfig(BaseModel):
    workspace_id: str = Field(json_schema_extra={"ui:type": "string"})
    dataset_id: str | None = Field(default=None, json_schema_extra={"ui:type": "string"})
    api_base: str = Field(
        default="https://api.powerbi.com/v1.0/myorg",
        json_schema_extra={"ui:type": "string"},
    )
    auth_mode: Literal["service_principal", "obo"] = Field(
        default="service_principal",
        json_schema_extra={"ui:type": "string"},
    )
    redirect_uri: str | None = Field(
        default=None,
        json_schema_extra={"ui:type": "string"},
        description="OAuth callback URL for OBO consent flow (auth_mode=obo)",
    )


class PowerBICredentials(BaseModel):
    tenant_id: str = Field(json_schema_extra={"ui:type": "string"})
    client_id: str = Field(json_schema_extra={"ui:type": "string"})
    client_secret: str = Field(json_schema_extra={"ui:type": "password"})


__all__ = [
    "PostgresConfig",
    "PostgresCredentials",
    "MssqlConfig",
    "MssqlCredentials",
    "FabricConfig",
    "FabricCredentials",
    "BigQueryConfig",
    "BigQueryCredentials",
    "PowerBIConfig",
    "PowerBICredentials",
]
