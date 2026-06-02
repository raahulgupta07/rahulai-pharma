"""PostgresRemoteProvider — live/synced remote PostgreSQL data sources.

Mirrors the URL-building convention used in ``app/connectors.py`` so a row
written by the connectors UI can be loaded directly into a Provider without
any translation step.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

from dash.providers.base import BaseProvider
from dash.providers.registry import register_provider_class


def _pg_set_timeouts(conn) -> None:
    """Begin-event listener: cooperative SQL cancellation via SET LOCAL.

    Forces Postgres to kill long queries at ~110s so that when
    asyncio.wait_for fires at PER_QUESTION_TIMEOUT_S=120s the underlying
    query is already terminated server-side rather than zombie-running.
    """
    try:
        conn.execute(text("SET LOCAL statement_timeout = '110s'"))
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        conn.execute(
            text("SET LOCAL idle_in_transaction_session_timeout = '60s'")
        )
    except Exception:  # noqa: BLE001
        pass

logger = logging.getLogger(__name__)


def _build_pg_url(config: dict[str, Any]) -> str:
    """Construct a postgresql+psycopg URL from a connectors config dict."""
    user = config["username"]
    password = config["password"]
    host = config["host"]
    port = config["port"]
    database = config["database"]
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


class PostgresRemoteProvider(BaseProvider):
    """Provider over a remote PostgreSQL database (live or synced)."""

    def __init__(
        self,
        project_slug: str,
        source_id: int,
        name: str,
        config: dict[str, Any],
    ) -> None:
        mode = (config.get("mode") or "live").lower()
        if mode not in {"sync", "live", "hybrid"}:
            mode = "live"
        super().__init__(
            id=f"pgremote_{source_id}",
            name=name,
            project_slug=project_slug,
            dialect="postgresql",
            mode=mode,
            agent_scope="project",
            read_instructions=(
                f"Remote PostgreSQL source '{name}'. "
                f"Schema: {config.get('schema', 'public')}. "
                "Read-only by default; writes require explicit guard removal."
            ),
            write_instructions=(
                "engine_rw exists for completeness but is gated upstream. "
                "Do not issue writes unless the orchestrator has whitelisted "
                "this source for mutations."
            ),
        )
        self.source_id = source_id
        self.config = dict(config)
        self.schema = config.get("schema", "public")

    # ---- Lifecycle -------------------------------------------------------

    async def setup(self) -> None:
        try:
            url = _build_pg_url(self.config)
            self.engine_ro = create_engine(
                url,
                poolclass=NullPool,
                connect_args={
                    "connect_timeout": 30,
                    "options": "-c default_transaction_read_only=on",
                },
            )
            self.engine_rw = create_engine(
                url,
                poolclass=NullPool,
                connect_args={"connect_timeout": 30},
            )
            for _eng in (self.engine_ro, self.engine_rw):
                try:
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
                "PostgresRemoteProvider setup failed for %s/%s: %s",
                self.project_slug,
                self.id,
                exc,
            )
            # Do NOT raise — registry policy is to mark degraded.

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
                    for r in conn.execute(tables_sql, {"schema": self.schema})
                ]
                for row in conn.execute(cols_sql, {"schema": self.schema}):
                    columns.setdefault(row[0], []).append(
                        {
                            "name": row[1],
                            "type": row[2],
                            "nullable": row[3] == "YES",
                        }
                    )
                for row in conn.execute(fks_sql, {"schema": self.schema}):
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
                "introspect() failed for %s/%s: %s",
                self.project_slug,
                self.id,
                exc,
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
            with self.engine_ro.connect().execution_options(
                stream_results=False
            ) as conn:
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


register_provider_class("postgres_remote", PostgresRemoteProvider)
