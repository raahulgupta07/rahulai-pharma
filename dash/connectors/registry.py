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
    PostgresConfig,
    PostgresCredentials,
)


@dataclass(frozen=True)
class RegistryEntry:
    type: str                        # "postgresql"
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
