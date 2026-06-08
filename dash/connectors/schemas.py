"""Pydantic config + credentials schemas — frozen contract §6.

All 10 classes exported by name. Fields using alias="schema" set
populate_by_name=True so callers can pass either `schema=` or `schema_=`.

UI form-renderer hints live in `json_schema_extra={"ui:type": "..."}`.
"""
from __future__ import annotations

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


__all__ = [
    "PostgresConfig",
    "PostgresCredentials",
]
