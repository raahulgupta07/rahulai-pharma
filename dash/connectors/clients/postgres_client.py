"""PostgresClient — read-only SQLAlchemy + psycopg connector.

Contract: §1 (ABC), §6 (schemas), §12 (soft imports).

Constructor accepts both ``schema`` and ``schema_`` keyword arguments
(Pydantic v2 uses ``schema_`` with alias ``schema`` when calling
``model_dump(by_alias=False)``).  We pop both and prefer whichever is set,
defaulting to ``"public"``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

# ConnectorClient is always available by integration time.
# During parallel development we use TYPE_CHECKING + a deferred import
# inside the class methods so that the module can be imported without
# base.py being present on disk.
if TYPE_CHECKING:
    pass

from dash.connectors.base import ConnectorClient  # noqa: E402 (resolved at integration)

log = logging.getLogger(__name__)


class PostgresClient(ConnectorClient):
    """Read-only Postgres connector using SQLAlchemy + psycopg (v3).

    URL format:
        postgresql+psycopg://{user}:{password}@{host}:{port}/{database}

    All engines use NullPool (PgBouncer-safe, no connection hoarding).
    Every method disposes the engine in a ``finally`` block.
    """

    def __init__(
        self,
        host: str,
        port: int = 5432,
        database: str = "",
        user: str = "",
        password: str = "",
        # Accept both schema and schema_ (Pydantic alias vs field name).
        schema: str | None = None,
        schema_: str | None = None,
        sslmode: str = "prefer",
        **kwargs,  # absorb any extra keys from merged config+creds dicts
    ) -> None:
        # Prefer schema_, then schema, then default "public".
        self._schema: str = schema_ or schema or "public"
        self._host = host
        self._port = int(port)
        self._database = database
        self._user = user
        self._password = password
        self._sslmode = sslmode

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _creds_for_scrub(self) -> list[str]:
        return [self._password] if self._password else []

    def _make_engine(self):
        """Return a NullPool SQLAlchemy engine for this connection."""
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool
        except ImportError as exc:
            raise RuntimeError(
                "PostgresClient requires sqlalchemy. "
                "Add it to requirements.txt."
            ) from exc

        url = (
            f"postgresql+psycopg://{self._user}:{self._password}"
            f"@{self._host}:{self._port}/{self._database}"
            f"?sslmode={self._sslmode}"
        )
        return create_engine(url, poolclass=NullPool)

    # ------------------------------------------------------------------
    # ConnectorClient interface
    # ------------------------------------------------------------------

    def test_connection(self) -> dict:
        """Run ``SELECT 1`` and count tables in the configured schema."""
        engine = self._make_engine()
        try:
            from sqlalchemy import text

            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                row = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema = :s"
                    ),
                    {"s": self._schema},
                ).scalar()
            return {
                "success": True,
                "message": f"Connected to {self._host}/{self._database}",
                "tables_visible": int(row or 0),
            }
        except Exception as exc:
            log.warning("PostgresClient.test_connection failed: %s", exc)
            from dash.connectors.safety import safe_error_message

            return {
                "success": False,
                "message": safe_error_message(exc, {"password": self._password}),
                "tables_visible": None,
            }
        finally:
            engine.dispose()

    def get_schemas(self, max_tables: int = 50) -> list[dict]:
        """Return table+column metadata from ``information_schema``.

        Returns:
            [{'schema': str, 'table': str, 'columns': [{'name': str, 'dtype': str}]}]
        """
        engine = self._make_engine()
        try:
            from sqlalchemy import text

            sql = text(
                """
                SELECT
                    t.table_schema,
                    t.table_name,
                    c.column_name,
                    c.data_type
                FROM information_schema.tables t
                JOIN information_schema.columns c
                    ON c.table_schema = t.table_schema
                    AND c.table_name  = t.table_name
                WHERE t.table_schema = :s
                  AND t.table_type   = 'BASE TABLE'
                ORDER BY t.table_name, c.ordinal_position
                LIMIT :lim
                """
            )
            with engine.connect() as conn:
                rows = conn.execute(
                    sql, {"s": self._schema, "lim": max_tables * 100}
                ).fetchall()

            # Group by table
            tables: dict[str, dict] = {}
            for schema_name, table_name, col_name, dtype in rows:
                key = f"{schema_name}.{table_name}"
                if key not in tables:
                    if len(tables) >= max_tables:
                        break
                    tables[key] = {
                        "schema": schema_name,
                        "table": table_name,
                        "columns": [],
                    }
                tables[key]["columns"].append({"name": col_name, "dtype": dtype})

            return list(tables.values())
        except Exception as exc:
            log.error("PostgresClient.get_schemas failed: %s", exc)
            raise
        finally:
            engine.dispose()

    def execute_query(
        self,
        sql: str,
        timeout_s: int = 60,
        max_rows: int = 10000,
    ) -> pd.DataFrame:
        """Execute *read-only* SQL inside a ``BEGIN READ ONLY`` transaction.

        - Statement timeout is applied via ``SET LOCAL``.
        - Result is truncated to *max_rows* rows.
        """
        engine = self._make_engine()
        try:
            from sqlalchemy import event, text

            # Inject per-statement timeout via connect event
            timeout_ms = int(timeout_s * 1000)

            @event.listens_for(engine, "connect")
            def _set_timeout(dbapi_conn, connection_record):  # noqa: ANN001
                pass  # applied below via SET LOCAL inside the transaction

            with engine.connect() as conn:
                conn.execute(
                    text(f"SET LOCAL statement_timeout = {timeout_ms}")
                )
                conn.execute(text("SET TRANSACTION READ ONLY"))
                df = pd.read_sql(sql, conn)

            if len(df) > max_rows:
                df = df.iloc[:max_rows].copy()
                log.info(
                    "PostgresClient.execute_query truncated to %d rows", max_rows
                )
            return df
        except Exception as exc:
            log.error("PostgresClient.execute_query failed: %s", exc)
            from dash.connectors.safety import safe_error_message

            raise RuntimeError(
                safe_error_message(exc, {"password": self._password})
            ) from exc
        finally:
            engine.dispose()

    def execute_query_stream(
        self,
        sql: str,
        chunk_size: int = 1000,
        timeout_s: int = 60,
        max_rows: int = 100000,
    ):
        """Stream rows in batches via SQLAlchemy server-side cursor."""
        engine = self._make_engine()
        try:
            from sqlalchemy import text

            timeout_ms = int(timeout_s * 1000)
            total = 0
            with engine.connect().execution_options(stream_results=True) as conn:
                conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
                conn.execute(text("SET TRANSACTION READ ONLY"))
                result = conn.execute(text(sql))
                cols = list(result.keys())
                while True:
                    rows = result.fetchmany(chunk_size)
                    if not rows:
                        break
                    if total + len(rows) > max_rows:
                        rows = rows[: max_rows - total]
                    batch = [dict(zip(cols, r)) for r in rows]
                    total += len(batch)
                    yield batch
                    if total >= max_rows:
                        log.info(
                            "PostgresClient.execute_query_stream hit max_rows=%d",
                            max_rows,
                        )
                        break
        except Exception as exc:
            log.error("PostgresClient.execute_query_stream failed: %s", exc)
            raise
        finally:
            engine.dispose()

    def prompt_schema(self) -> str:
        """Return a human-readable schema block for LLM context."""
        try:
            tables = self.get_schemas(max_tables=50)
        except Exception:
            tables = []

        lines: list[str] = [
            f"PostgreSQL (Postgres dialect). Schema={self._schema}.",
            "Tables:",
        ]
        for entry in tables:
            col_summary = ", ".join(
                f"{c['name']} ({c['dtype']})" for c in entry["columns"][:10]
            )
            lines.append(f"  - {entry['table']}: {col_summary}")

        if not tables:
            lines.append("  (no tables found)")

        return "\n".join(lines)
