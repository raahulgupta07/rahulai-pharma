"""PostgresLocalProvider — wraps the legacy per-project Postgres schema.

Each Dash project has a private schema named ``proj_{slug}`` inside the main
application database. Historically agents talked to that schema directly via
the engines cached in :mod:`db.session`. This provider repackages that path
into the new :class:`BaseProvider` shape so the registry can treat the local
schema uniformly alongside remote sources.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from sqlalchemy import event, text

from dash.providers.base import BaseProvider
from dash.providers.registry import register_provider_class


def _pg_set_timeouts(conn) -> None:
    """Begin-event listener: cooperative SQL cancellation via SET LOCAL.

    Forces Postgres to kill the query at ~110s (under the 120s
    PER_QUESTION_TIMEOUT_S in dash.learning.cycle), so asyncio.wait_for
    cancellations don't leave zombie queries running in thread executors.
    """
    try:
        conn.execute(text("SET LOCAL statement_timeout = '110s'"))
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        conn.execute(
            text("SET LOCAL idle_in_transaction_session_timeout = '60s'")
        )
    except Exception:  # noqa: BLE001
        # Older PG / restricted roles may reject one of these; non-fatal.
        pass

logger = logging.getLogger(__name__)


def _safe_slug(slug: str) -> str:
    """Mirror db.session's slug sanitization: lowercase, [a-z0-9_], cap at 63."""
    return re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]


class PostgresLocalProvider(BaseProvider):
    """Provider over the per-project local Postgres schema (``proj_{slug}``)."""

    def __init__(
        self,
        project_slug: str,
        source_id: int = 0,
        name: str = "local",
    ) -> None:
        super().__init__(
            id=f"local_{source_id}",
            name=name,
            project_slug=project_slug,
            dialect="postgresql",
            mode="sync",
            agent_scope="project",
            read_instructions=(
                "Local project schema. All project tables live in "
                f"proj_{_safe_slug(project_slug)}. Search path is set "
                "automatically; bare table names resolve correctly."
            ),
            write_instructions=(
                "Use engine_rw only for explicit DDL/DML on the project "
                "schema. Read paths must use engine_ro."
            ),
        )
        self.source_id = source_id
        self._schema = f"proj_{_safe_slug(project_slug)}"

    # ---- Lifecycle -------------------------------------------------------

    async def setup(self) -> None:
        try:
            from db.session import (
                get_project_engine,
                get_project_readonly_engine,
            )

            self.engine_rw = get_project_engine(self.project_slug)
            self.engine_ro = get_project_readonly_engine(self.project_slug)
            for _eng in (self.engine_ro, self.engine_rw):
                try:
                    if _eng is not None:
                        event.listen(_eng, "begin", _pg_set_timeouts)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "begin-listener attach failed for %s/%s",
                        self.project_slug,
                        self.id,
                    )
            self.schema_blob = self.introspect()
            self.degraded = False
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.degraded = True
            self.last_error = str(exc)[:300]
            logger.exception(
                "PostgresLocalProvider setup failed for %s: %s",
                self.project_slug,
                exc,
            )

    async def teardown(self) -> None:
        for attr in ("engine_ro", "engine_rw"):
            eng = getattr(self, attr, None)
            if eng is not None:
                try:
                    eng.dispose()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Error disposing %s for %s/%s",
                        attr,
                        self.project_slug,
                        self.id,
                    )
                setattr(self, attr, None)

    # ---- Introspection ---------------------------------------------------

    def introspect(self) -> dict[str, Any]:
        """Return ``{tables, columns, fks, dialect}`` for ``proj_{slug}``."""
        if self.engine_ro is None:
            return {
                "tables": [],
                "columns": {},
                "fks": [],
                "dialect": "postgresql",
            }

        tables: list[str] = []
        columns: dict[str, list[dict[str, Any]]] = {}
        fks: list[dict[str, Any]] = []

        tables_sql = text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :schema ORDER BY table_name"
        )
        cols_sql = text(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = :schema "
            "ORDER BY table_name, ordinal_position"
        )
        fks_sql = text(
            """
            SELECT
                conrelid::regclass::text AS from_table,
                a.attname               AS from_column,
                confrelid::regclass::text AS to_table,
                af.attname              AS to_column
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            JOIN pg_attribute af
              ON af.attrelid = c.confrelid AND af.attnum = ANY(c.confkey)
            JOIN pg_namespace n
              ON n.oid = c.connamespace
            WHERE c.contype = 'f' AND n.nspname = :schema
            """
        )

        try:
            with self.engine_ro.connect() as conn:
                tables = [
                    r[0]
                    for r in conn.execute(tables_sql, {"schema": self._schema})
                ]
                for row in conn.execute(cols_sql, {"schema": self._schema}):
                    columns.setdefault(row[0], []).append(
                        {
                            "name": row[1],
                            "type": row[2],
                            "nullable": row[3] == "YES",
                        }
                    )
                for row in conn.execute(fks_sql, {"schema": self._schema}):
                    fks.append(
                        {
                            "from_table": row[0],
                            "from_column": row[1],
                            "to_table": row[2],
                            "to_column": row[3],
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "introspect() failed for %s: %s", self.project_slug, exc
            )

        return {
            "tables": tables,
            "columns": columns,
            "fks": fks,
            "dialect": "postgresql",
        }

    # ---- Tools / health --------------------------------------------------

    def emit_tools(self) -> list[Callable[..., Any]]:
        try:
            from dash.providers.tool_factory import make_tools  # type: ignore
        except ImportError:
            # TODO: tool_factory not yet present; emit nothing for now.
            return []
        return make_tools(self)

    def health_check(self) -> bool:
        if self.engine_ro is None:
            return False
        try:
            with self.engine_ro.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "health_check failed for %s/%s: %s",
                self.project_slug,
                self.id,
                exc,
            )
            return False


register_provider_class("postgres_local", PostgresLocalProvider)
