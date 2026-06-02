"""Connector registry — frozen contract §2.

REGISTRY entries reference client_path as a dotted string; clients are
imported lazily via `resolve_client_class` so this module loads even when
driver-dependent client files have not been built yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Type

from pydantic import BaseModel

from .schemas import (
    BigQueryConfig,
    BigQueryCredentials,
    FabricConfig,
    FabricCredentials,
    MssqlConfig,
    MssqlCredentials,
    PostgresConfig,
    PostgresCredentials,
    PowerBIConfig,
    PowerBICredentials,
)


@dataclass(frozen=True)
class RegistryEntry:
    type: str                        # "postgresql"|"mssql"|"fabric"|"bigquery"|"powerbi"
    title: str
    kind: str                        # "database"|"service"
    description: str
    config_schema: Type[BaseModel]
    credentials_schema: Type[BaseModel]
    client_path: str                 # dotted path to client class


REGISTRY: dict[str, RegistryEntry] = {
    "postgresql": RegistryEntry(
        type="postgresql",
        title="PostgreSQL",
        kind="database",
        description="Connect to a PostgreSQL database (incl. PostGIS / pgvector).",
        config_schema=PostgresConfig,
        credentials_schema=PostgresCredentials,
        client_path="dash.connectors.clients.postgres_client.PostgresClient",
    ),
    "mssql": RegistryEntry(
        type="mssql",
        title="Microsoft SQL Server",
        kind="database",
        description="Connect to a Microsoft SQL Server database using SQL authentication.",
        config_schema=MssqlConfig,
        credentials_schema=MssqlCredentials,
        client_path="dash.connectors.clients.mssql_client.MssqlClient",
    ),
    "fabric": RegistryEntry(
        type="fabric",
        title="Microsoft Fabric",
        kind="database",
        description="Connect to a Microsoft Fabric warehouse via Azure AD Service Principal.",
        config_schema=FabricConfig,
        credentials_schema=FabricCredentials,
        client_path="dash.connectors.clients.fabric_client.FabricClient",
    ),
    "bigquery": RegistryEntry(
        type="bigquery",
        title="Google BigQuery",
        kind="database",
        description="Connect to a Google BigQuery dataset using a service-account JSON.",
        config_schema=BigQueryConfig,
        credentials_schema=BigQueryCredentials,
        client_path="dash.connectors.clients.bigquery_client.BigQueryClient",
    ),
    "powerbi": RegistryEntry(
        type="powerbi",
        title="Microsoft Power BI",
        kind="service",
        description="Query a Power BI dataset (DAX) via Azure AD Service Principal.",
        config_schema=PowerBIConfig,
        credentials_schema=PowerBICredentials,
        client_path="dash.connectors.clients.powerbi_client.PowerBIClient",
    ),
}


def resolve_client_class(connector_type: str):
    """Lazily import and return the client class for a registered connector."""
    e = REGISTRY[connector_type]
    mod, _, cls = e.client_path.rpartition(".")
    return getattr(import_module(mod), cls)


def list_connectors() -> list[dict]:
    """Lightweight summary of every registered connector type."""
    return [
        {
            "type": e.type,
            "title": e.title,
            "kind": e.kind,
            "description": e.description,
        }
        for e in REGISTRY.values()
    ]
