"""MySQLRemoteProvider — remote MySQL data sources (live or synced).

MySQL has no connection-string flag for read-only sessions, so we attach a
``begin`` event listener that issues ``SET SESSION TRANSACTION READ ONLY``
at the start of every transaction on the read engine.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

from dash.providers.base import BaseProvider
from dash.providers.registry import register_provider_class

logger = logging.getLogger(__name__)


def _build_mysql_url(config: dict[str, Any]) -> str:
    """Construct a mysql+pymysql URL from a connectors config dict."""
    user = config["username"]
    password = config["password"]
    host = config["host"]
    port = config["port"]
    database = config["database"]
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"


def _mysql_set_timeouts(conn) -> None:
    """Begin-event listener: cooperative cancellation for MySQL.

    MAX_EXECUTION_TIME (ms) caps SELECT runtime; lock_wait_timeout (s)
    bounds metadata-lock waits. MAX_EXECUTION_TIME requires MySQL 5.7.4+;
    older MySQL/MariaDB will raise — we swallow it.
    """
    try:
        conn.execute(text("SET SESSION MAX_EXECUTION_TIME = 110000"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("MySQL MAX_EXECUTION_TIME not supported: %s", exc)
    try:
        conn.execute(text("SET SESSION lock_wait_timeout = 5"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("MySQL lock_wait_timeout set failed: %s", exc)


def _make_mysql_readonly_listener() -> Callable[..., None]:
    """Return a ``begin`` event listener that flips the txn to READ ONLY."""

    def _listener(conn) -> None:
        try:
            conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
        except Exception as exc:  # noqa: BLE001
            # Older MySQL / MariaDB variants may not support the flag; log and
            # continue rather than poisoning the connection.
            logger.warning("MySQL READ ONLY set failed: %s", exc)

    return _listener


class MySQLRemoteProvider(BaseProvider):
    """Provider over a remote MySQL database (live or synced)."""

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
            id=f"mysql_{source_id}",
            name=name,
            project_slug=project_slug,
            dialect="mysql",
            mode=mode,
            agent_scope="project",
            read_instructions=(
                f"Remote MySQL source '{name}'. "
                "Read engine is locked to READ ONLY transactions. "
                "Use backticks around reserved-word identifiers."
            ),
            write_instructions=(
                "engine_rw exists for completeness but is gated upstream. "
                "Do not issue writes unless the orchestrator has whitelisted "
                "this source for mutations."
            ),
        )
        self.source_id = source_id
        self.config = dict(config)

    # ---- Lifecycle -------------------------------------------------------

    async def setup(self) -> None:
        try:
            url = _build_mysql_url(self.config)
            self.engine_ro = create_engine(
                url,
                poolclass=NullPool,
                connect_args={"connect_timeout": 30},
            )
            event.listen(
                self.engine_ro, "begin", _make_mysql_readonly_listener()
            )
            self.engine_rw = create_engine(
                url,
                poolclass=NullPool,
                connect_args={"connect_timeout": 30},
            )
            for _eng in (self.engine_ro, self.engine_rw):
                try:
                    event.listen(_eng, "begin", _mysql_set_timeouts)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "MySQL timeout listener attach failed for %s/%s",
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
                "MySQLRemoteProvider setup failed for %s/%s: %s",
                self.project_slug,
                self.id,
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
        if self.engine_ro is None:
            return {
                "tables": [],
                "columns": {},
                "fks": [],
                "dialect": "mysql",
            }

        tables: list[str] = []
        columns: dict[str, list[dict[str, Any]]] = {}
        fks: list[dict[str, Any]] = []

        tables_sql = text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() ORDER BY table_name"
        )
        cols_sql = text(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = DATABASE() "
            "ORDER BY table_name, ordinal_position"
        )
        fks_sql = text(
            """
            SELECT
                table_name             AS from_table,
                column_name            AS from_column,
                referenced_table_name  AS to_table,
                referenced_column_name AS to_column
            FROM information_schema.key_column_usage
            WHERE table_schema = DATABASE()
              AND referenced_table_name IS NOT NULL
            """
        )

        try:
            with self.engine_ro.connect() as conn:
                tables = [r[0] for r in conn.execute(tables_sql)]
                for row in conn.execute(cols_sql):
                    columns.setdefault(row[0], []).append(
                        {
                            "name": row[1],
                            "type": row[2],
                            "nullable": row[3] == "YES",
                        }
                    )
                for row in conn.execute(fks_sql):
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
            "dialect": "mysql",
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


register_provider_class("mysql_remote", MySQLRemoteProvider)
