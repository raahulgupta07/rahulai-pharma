"""Microsoft Fabric connector client — T-SQL via Azure AD Service Principal.

Soft-imports pyodbc and azure.identity per §12 (driver-missing → RuntimeError at __init__).
"""
from __future__ import annotations

import re
import struct as _struct
from typing import Any

import pandas as pd

try:
    import pyodbc as _pyodbc
    _HAVE_PYODBC = True
except ImportError:
    _HAVE_PYODBC = False

try:
    from azure.identity import ClientSecretCredential as _ClientSecretCredential
    _HAVE_AZURE_IDENTITY = True
except ImportError:
    _HAVE_AZURE_IDENTITY = False

from dash.connectors.base import ConnectorClient


_FABRIC_SCOPE = "https://database.windows.net/.default"
_SQL_COPT_SS_ACCESS_TOKEN = 1256


class FabricClient(ConnectorClient):
    """ConnectorClient for Microsoft Fabric (Synapse / Data Warehouse) via T-SQL."""

    def __init__(self, **kwargs: Any) -> None:
        if not _HAVE_PYODBC:
            raise RuntimeError(
                "Fabric connector requires pyodbc. "
                "Add pyodbc to requirements.txt and rebuild image with ODBC Driver 18 for SQL Server."
            )
        if not _HAVE_AZURE_IDENTITY:
            raise RuntimeError(
                "Fabric connector requires azure-identity. "
                "Add azure-identity to requirements.txt."
            )

        self.sql_endpoint: str = kwargs["sql_endpoint"]
        self.database: str = kwargs["database"]
        # Accept both "schema" (alias) and "schema_"
        self.schema_: str = kwargs.get("schema") or kwargs.get("schema_") or "dbo"
        self.query_timeout_seconds: int = int(kwargs.get("query_timeout_seconds", 120))

        self._tenant_id: str = kwargs["tenant_id"]
        self._client_id: str = kwargs["client_id"]
        self._client_secret: str = kwargs["client_secret"]

        self._conn_str = (
            f"Driver={{ODBC Driver 18 for SQL Server}};"
            f"Server={self.sql_endpoint},1433;"
            f"Database={self.database};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _creds_for_scrub(self) -> list[str]:
        out: list[str] = []
        if getattr(self, "_client_secret", None):
            out.append(self._client_secret)
        if getattr(self, "_tenant_id", None):
            out.append(self._tenant_id)
        if getattr(self, "_client_id", None):
            out.append(self._client_id)
        return out

    def _get_token_struct(self) -> bytes:
        cred = _ClientSecretCredential(
            tenant_id=self._tenant_id,
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        token_bytes = cred.get_token(_FABRIC_SCOPE).token.encode("utf-16-le")
        return _struct.pack("=i", len(token_bytes)) + token_bytes

    def _connect(self):
        token_struct = self._get_token_struct()
        return _pyodbc.connect(
            self._conn_str,
            attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct},
        )

    # ------------------------------------------------------------------
    # ABC implementation
    # ------------------------------------------------------------------

    def test_connection(self) -> dict:
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            # Count accessible tables in target schema
            cursor.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = ?",
                self.schema_,
            )
            row = cursor.fetchone()
            tables_visible = row[0] if row else None
            conn.close()
            return {"success": True, "message": "Connected to Fabric.", "tables_visible": tables_visible}
        except Exception as exc:
            from dash.connectors.safety import safe_error_message

            return {
                "success": False,
                "message": safe_error_message(
                    exc, {"client_secret": self._client_secret}
                ),
                "tables_visible": None,
            }

    def get_schemas(self, max_tables: int = 50) -> list[dict]:
        sql = """
            SELECT
                c.TABLE_SCHEMA,
                c.TABLE_NAME,
                c.COLUMN_NAME,
                c.DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS c
            INNER JOIN (
                SELECT TOP (?) TABLE_SCHEMA, TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = ?
                ORDER BY TABLE_NAME
            ) t ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME
            ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
        """
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, max_tables, self.schema_)
            rows = cursor.fetchall()
        finally:
            conn.close()

        tables: dict[tuple, list] = {}
        for row in rows:
            key = (row[0], row[1])
            tables.setdefault(key, []).append({"name": row[2], "dtype": row[3]})

        return [
            {"schema": schema, "table": table, "columns": cols}
            for (schema, table), cols in tables.items()
        ]

    def execute_query(self, sql: str, timeout_s: int = 60, max_rows: int = 10000) -> pd.DataFrame:
        # Inject TOP if no row-limiting clause present
        if not re.search(r'\bTOP\b|\bLIMIT\b', sql, re.IGNORECASE):
            sql = re.sub(
                r'\bSELECT\b',
                f'SELECT TOP {max_rows}',
                sql,
                count=1,
                flags=re.IGNORECASE,
            )

        conn = None
        try:
            conn = self._connect()
            conn.timeout = int(timeout_s)
            df = pd.read_sql(sql, conn)
        except Exception as exc:
            from dash.connectors.safety import safe_error_message

            raise RuntimeError(
                safe_error_message(exc, {"client_secret": self._client_secret})
            ) from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        if len(df) > max_rows:
            df = df.iloc[:max_rows]
        return df

    def execute_query_stream(
        self,
        sql: str,
        chunk_size: int = 1000,
        timeout_s: int = 60,
        max_rows: int = 100000,
    ):
        """Stream rows in batches via pyodbc cursor.fetchmany (AAD token auth)."""
        # Inject TOP if no row-limiting clause present
        if not re.search(r'\bTOP\b|\bLIMIT\b', sql, re.IGNORECASE):
            sql = re.sub(
                r'\bSELECT\b',
                f'SELECT TOP {max_rows}',
                sql,
                count=1,
                flags=re.IGNORECASE,
            )

        conn = None
        try:
            conn = self._connect()
            conn.timeout = timeout_s
            cursor = conn.cursor()
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
                    break
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def prompt_schema(self) -> str:
        try:
            tables = self.get_schemas()
        except Exception as exc:
            return (
                f"[Microsoft Fabric — T-SQL dialect]\n"
                f"Endpoint: {self.sql_endpoint} | DB: {self.database} | Schema: {self.schema_}\n"
                f"Schema unavailable: {exc}"
            )

        lines = [
            "[Microsoft Fabric — T-SQL dialect]",
            f"Endpoint: {self.sql_endpoint}",
            f"Database: {self.database}",
            f"Schema: {self.schema_}",
            "",
        ]
        for t in tables:
            col_str = ", ".join(
                f"{c['name']} {c['dtype']}" for c in t["columns"]
            )
            lines.append(f"  {t['schema']}.{t['table']}({col_str})")

        return "\n".join(lines)
