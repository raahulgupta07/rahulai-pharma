"""Abstract base class for all Dash data source providers.

Inspired by Scout's ``DatabaseContextProvider`` pattern: an engine pair
(read-only + read-write), schema snapshot, dialect, and read/write
instructions are *co-located* in a single object. Each provider also emits
its own Agno-compatible tool callables so agents can route queries to the
correct backend without the orchestrator caring about transport details.

Concrete subclasses (PostgresProvider, MySQLProvider, FabricProvider, ...)
live in sibling modules and self-register via
:func:`dash.providers.registry.register_provider_class` on import.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


# Dialect-specific quoting characters. Keeps qualified_table_name() free of
# branching at call sites and trivially extensible (e.g. snowflake later).
_DIALECT_QUOTE: dict[str, tuple[str, str]] = {
    "postgresql": ('"', '"'),
    "mysql": ("`", "`"),
    "tsql": ("[", "]"),  # Microsoft Fabric / SQL Server family
}


class BaseProvider(ABC):
    """Abstract data source provider.

    Subclasses implement transport-specific :meth:`setup`, :meth:`teardown`,
    :meth:`emit_tools`, :meth:`introspect`, and :meth:`health_check`.
    Everything else (dialect quoting, prompt overlay assembly, repr) is
    inherited.
    """

    # ---- Concrete attributes (populated by subclass __init__) -----------

    id: str
    name: str
    project_slug: str
    dialect: str  # 'postgresql' | 'mysql' | 'tsql'
    mode: str  # 'sync' | 'live' | 'hybrid'
    agent_scope: str  # 'project' | 'analyst_only' | 'researcher_only' | 'shared'

    engine_ro: Optional[Engine]
    engine_rw: Optional[Engine]

    schema_blob: dict[str, Any]
    read_instructions: str
    write_instructions: str

    degraded: bool
    last_error: Optional[str]

    def __init__(
        self,
        *,
        id: str,
        name: str,
        project_slug: str,
        dialect: str,
        mode: str = "live",
        agent_scope: str = "project",
        read_instructions: str = "",
        write_instructions: str = "",
    ) -> None:
        if dialect not in _DIALECT_QUOTE:
            raise ValueError(
                f"Unsupported dialect {dialect!r}; "
                f"expected one of {sorted(_DIALECT_QUOTE)}"
            )
        self.id = id
        self.name = name
        self.project_slug = project_slug
        self.dialect = dialect
        self.mode = mode
        self.agent_scope = agent_scope

        self.engine_ro = None
        self.engine_rw = None

        self.schema_blob = {}
        self.read_instructions = read_instructions
        self.write_instructions = write_instructions

        self.degraded = False
        self.last_error = None

    # ---- Abstract surface -----------------------------------------------

    @abstractmethod
    async def setup(self) -> None:
        """Establish engines (NullPool) and fetch the initial schema snapshot.

        Implementations MUST set :attr:`degraded` + :attr:`last_error` on
        partial failure rather than raising past the registry.
        """

    @abstractmethod
    async def teardown(self) -> None:
        """Dispose engines and release any background resources."""

    @abstractmethod
    def emit_tools(self) -> list[Callable[..., Any]]:
        """Return plain Python callables (``query_<id>`` etc.).

        The agent framework wraps these into Agno tools at a higher layer;
        keeping this stage framework-agnostic simplifies unit testing.
        """

    @abstractmethod
    def introspect(self) -> dict[str, Any]:
        """Return ``{tables, columns, fks, dialect}`` snapshot."""

    @abstractmethod
    def health_check(self) -> bool:
        """Cheap liveness probe — should not raise."""

    # ---- Concrete helpers -----------------------------------------------

    def qualified_table_name(self, table: str) -> str:
        """Apply dialect quoting to a table identifier.

        Accepts ``"schema.table"`` and quotes each segment independently.
        """
        lq, rq = _DIALECT_QUOTE[self.dialect]
        parts = table.split(".")
        return ".".join(f"{lq}{p}{rq}" for p in parts)

    def dialect_overlay(self) -> str:
        """Per-source instruction snippet injected into the Analyst prompt.

        Bundles dialect rules, schema summary, dim values, and proven query
        patterns into a ~2KB string. The Analyst prompt assembler concatenates
        one overlay per active provider.
        """
        # Dialect-specific dos & don'ts.
        if self.dialect == "tsql":
            dialect_rules = (
                "DIALECT: Microsoft T-SQL (Fabric / SQL Server).\n"
                "- Use TOP N, never LIMIT.\n"
                "- Use GETDATE(), DATEADD, DATEDIFF; never NOW().\n"
                "- Bracket-quote identifiers with reserved words: [Order].\n"
                "- String concat with +, not ||.\n"
            )
        elif self.dialect == "mysql":
            dialect_rules = (
                "DIALECT: MySQL.\n"
                "- Use LIMIT N (or LIMIT offset, count).\n"
                "- Use NOW(), CURDATE(); date math via DATE_ADD/DATE_SUB.\n"
                "- Backtick-quote identifiers when needed: `order`.\n"
                "- String concat with CONCAT(), not ||.\n"
            )
        else:  # postgresql default
            dialect_rules = (
                "DIALECT: PostgreSQL.\n"
                "- Use LIMIT N OFFSET M.\n"
                "- Use NOW(), CURRENT_DATE; intervals like NOW() - INTERVAL '7 days'.\n"
                "- Double-quote identifiers when needed: \"order\".\n"
                "- String concat with || or CONCAT().\n"
            )

        tables = self.schema_blob.get("tables") or []
        if isinstance(tables, dict):
            table_names = list(tables.keys())
        else:
            table_names = [str(t) for t in tables]
        schema_summary = (
            f"SCHEMA ({len(table_names)} tables): "
            + (", ".join(table_names[:40]) if table_names else "<empty>")
        )

        dim_values = self.schema_blob.get("dim_values") or {}
        dim_lines: list[str] = []
        for col, vals in list(dim_values.items())[:8]:
            preview = ", ".join(str(v) for v in (vals or [])[:6])
            dim_lines.append(f"  - {col}: {preview}")
        dim_block = (
            "DIMENSION VALUES:\n" + "\n".join(dim_lines) if dim_lines else ""
        )

        patterns = self.schema_blob.get("proven_patterns") or []
        pattern_block = ""
        if patterns:
            pattern_block = "PROVEN PATTERNS:\n" + "\n".join(
                f"  - {p}" for p in patterns[:6]
            )

        header = (
            f"### Source: {self.name} (id={self.id}, mode={self.mode})\n"
            f"Tools: query_{self.id}, describe_{self.id}, sample_{self.id}\n"
        )

        body_parts = [header, dialect_rules, schema_summary]
        if dim_block:
            body_parts.append(dim_block)
        if pattern_block:
            body_parts.append(pattern_block)
        if self.read_instructions:
            body_parts.append("READ NOTES:\n" + self.read_instructions)
        if self.degraded:
            body_parts.append(
                f"!! DEGRADED: {self.last_error or 'unknown error'} — "
                "prefer cached snapshot, do not attempt live writes."
            )

        overlay = "\n\n".join(body_parts).strip() + "\n"
        # Soft cap at ~2KB; truncation here is preferable to bloating prompts.
        if len(overlay) > 2048:
            overlay = overlay[:2040] + "\n...[t]"
        return overlay

    # ---- Dunder ---------------------------------------------------------

    def __repr__(self) -> str:
        flag = " degraded" if self.degraded else ""
        return (
            f"<{type(self).__name__} id={self.id!r} "
            f"project={self.project_slug!r} dialect={self.dialect}"
            f" mode={self.mode} scope={self.agent_scope}{flag}>"
        )
