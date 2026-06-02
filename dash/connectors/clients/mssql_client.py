"""MssqlClient — read-only pyodbc + ODBC Driver 18 connector.

Contract: §1 (ABC), §6 (schemas), §12 (soft imports).

pyodbc is a soft-import: if the package is not installed the module can
still be imported and the class can be defined; the missing driver is only
surfaced as a ``RuntimeError`` in ``__init__`` so the registry can be
loaded without the driver present.

Constructor accepts both ``schema`` and ``schema_`` keyword arguments
(Pydantic v2 uses ``schema_`` with alias ``schema`` when calling
``model_dump(by_alias=False)``).  We pop both and prefer whichever is set,
defaulting to ``"dbo"``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    pass

# ── Soft-import pyodbc (§12) ──────────────────────────────────────────────
try:
    import pyodbc  # noqa: F401

    _HAVE_PYODBC = True
except ImportError:
    _HAVE_PYODBC = False

from dash.connectors.base import ConnectorClient  # noqa: E402

log = logging.getLogger(__name__)


class MssqlClient(ConnectorClient):
    """Read-only SQL Server connector using pyodbc + ODBC Driver 18.

    Connection string format:
        Driver={ODBC Driver 18 for SQL Server};
        Server=host,port;Database=db;UID=user;PWD=pwd;
        Encrypt=yes;TrustServerCertificate=yes|no;

    Statement timeout is set via ``cursor.timeout`` (seconds).
    Isolation level is READ UNCOMMITTED to avoid blocking reads.
    """

    def __init__(
        self,
        host: str,
        port: int = 1433,
        database: str = "",
        user: str = "",
        password: str = "",
        # Accept both schema and schema_ (Pydantic alias vs field name).
        schema: str | None = None,
        schema_: str | None = None,
        encrypt: bool = True,
        trust_server_certificate: bool = False,
        **kwargs,  # absorb any extra keys from merged config+creds dicts
    ) -> None:
        if not _HAVE_PYODBC:
            raise RuntimeError(
                "MssqlClient requires pyodbc. "
                "Install it (pip install pyodbc) and ensure ODBC Driver 18 "
                "for SQL Server is present, then rebuild the image."
            )
        # Prefer schema_, then schema, then default "dbo".
        self._schema: str = schema_ or schema or "dbo"
        self._host = host
        self._port = int(port)
        self._database = database
        self._user = user
        self._password = password
        self._encrypt = encrypt
        self._trust = trust_server_certificate

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _creds_for_scrub(self) -> list[str]:
        return [self._password] if self._password else []

    def _conn_str(self) -> str:
        trust_val = "yes" if self._trust else "no"
        return (
            f"Driver={{ODBC Driver 18 for SQL Server}};"
            f"Server={self._host},{self._port};"
            f"Database={self._database};"
            f"UID={self._user};PWD={self._password};"
            f"Encrypt={'yes' if self._encrypt else 'no'};"
            f"TrustServerCertificate={trust_val};"
        )

    def _connect(self):  # -> pyodbc.Connection
        import pyodbc  # guaranteed by __init__ guard

        return pyodbc.connect(self._conn_str())

    # ------------------------------------------------------------------
    # ConnectorClient interface
    # ------------------------------------------------------------------

    def test_connection(self) -> dict:
        """Run ``SELECT 1`` and count tables in the configured schema."""
        conn = None
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = ?",
                self._schema,
            )
            row = cursor.fetchone()
            return {
                "success": True,
                "message": f"Connected to {self._host}/{self._database}",
                "tables_visible": int(row[0]) if row else 0,
            }
        except Exception as exc:
            log.warning("MssqlClient.test_connection failed: %s", exc)
            from dash.connectors.safety import safe_error_message

            return {
                "success": False,
                "message": safe_error_message(exc, {"password": self._password}),
                "tables_visible": None,
            }
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def get_schemas(self, max_tables: int = 50) -> list[dict]:
        """Return table+column metadata from ``INFORMATION_SCHEMA``.

        Returns:
            [{'schema': str, 'table': str, 'columns': [{'name': str, 'dtype': str}]}]
        """
        conn = None
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    TABLE_SCHEMA,
                    TABLE_NAME,
                    COLUMN_NAME,
                    DATA_TYPE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = ?
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                self._schema,
            )
            rows = cursor.fetchall()

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
            log.error("MssqlClient.get_schemas failed: %s", exc)
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def execute_query(
        self,
        sql: str,
        timeout_s: int = 60,
        max_rows: int = 10000,
    ) -> pd.DataFrame:
        """Execute *read-only* SQL with READ UNCOMMITTED isolation.

        - Statement timeout is set via ``cursor.timeout`` (in seconds).
        - Result is truncated to *max_rows* rows.
        """
        conn = None
        try:
            conn = self._connect()
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
            cursor = conn.cursor()
            cursor.timeout = int(timeout_s)
            df = pd.read_sql(sql, conn)
            if len(df) > max_rows:
                df = df.iloc[:max_rows].copy()
                log.info(
                    "MssqlClient.execute_query truncated to %d rows", max_rows
                )
            return df
        except Exception as exc:
            log.error("MssqlClient.execute_query failed: %s", exc)
            from dash.connectors.safety import safe_error_message

            raise RuntimeError(
                safe_error_message(exc, {"password": self._password})
            ) from exc
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def execute_query_stream(
        self,
        sql: str,
        chunk_size: int = 1000,
        timeout_s: int = 60,
        max_rows: int = 100000,
    ):
        """Stream rows in batches via pyodbc cursor.fetchmany."""
        conn = None
        try:
            conn = self._connect()
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
            cursor = conn.cursor()
            cursor.timeout = int(timeout_s)
            cursor.execute(sql)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            total = 0
            while True:
                rows = cursor.fetchmany(chunk_size)
                if not rows:
                    break
                if total + len(rows) > max_rows:
                    rows = rows[: max_rows - total]
                batch = [dict(zip(cols, r)) for r in rows]
                total += len(batch)
                yield batch
                if total >= max_rows:
                    log.info(
                        "MssqlClient.execute_query_stream hit max_rows=%d", max_rows
                    )
                    break
        except Exception as exc:
            log.error("MssqlClient.execute_query_stream failed: %s", exc)
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def prompt_schema(self) -> str:
        """Return a human-readable schema block for LLM context."""
        try:
            tables = self.get_schemas(max_tables=50)
        except Exception:
            tables = []

        lines: list[str] = [
            f"Microsoft SQL Server (T-SQL dialect). Schema={self._schema}. "
            "Use TOP not LIMIT.",
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
