"""PowerBI connector client — DAX via REST API with Azure AD Service Principal.

Soft-imports httpx and azure.identity per §12 (driver-missing → RuntimeError at __init__).
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import pandas as pd

# Per-request user context for OBO mode. Set via set_obo_user(user_id) context
# manager from request middleware (e.g. connectors_v2.run_query) before calling
# client methods. Default None means no user bound — OBO calls will raise.
_OBO_USER_CTX: ContextVar[int | None] = ContextVar("powerbi_obo_user_id", default=None)


@contextmanager
def set_obo_user(user_id: int | None):
    """Context manager binding a user_id to PowerBI calls in OBO auth_mode."""
    token = _OBO_USER_CTX.set(user_id)
    try:
        yield
    finally:
        _OBO_USER_CTX.reset(token)

try:
    import httpx as _httpx
    _HAVE_HTTPX = True
except ImportError:
    _HAVE_HTTPX = False

try:
    from azure.identity import ClientSecretCredential as _ClientSecretCredential
    _HAVE_AZURE_IDENTITY = True
except ImportError:
    _HAVE_AZURE_IDENTITY = False

from dash.connectors.base import ConnectorClient


_POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


class PowerBIClient(ConnectorClient):
    """ConnectorClient for Microsoft PowerBI (DAX queries via REST API)."""

    def __init__(self, **kwargs: Any) -> None:
        if not _HAVE_HTTPX:
            raise RuntimeError(
                "PowerBI connector requires httpx. "
                "Add httpx to requirements.txt."
            )
        if not _HAVE_AZURE_IDENTITY:
            raise RuntimeError(
                "PowerBI connector requires azure-identity. "
                "Add azure-identity to requirements.txt."
            )

        self.workspace_id: str = kwargs["workspace_id"]
        self.dataset_id: str | None = kwargs.get("dataset_id")
        self.api_base: str = kwargs.get("api_base", "https://api.powerbi.com/v1.0/myorg")

        self._tenant_id: str = kwargs["tenant_id"]
        self._client_id: str = kwargs["client_id"]
        self._client_secret: str = kwargs["client_secret"]

        # OBO support — see Agent J contract.
        # auth_mode='obo' routes every API call through the calling user's
        # token so PowerBI applies that user's RLS.
        self.auth_mode: str = kwargs.get("auth_mode", "service_principal")
        self.connection_id: str | None = kwargs.get("connection_id")

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

    def _get_token(self) -> str:
        cred = _ClientSecretCredential(
            tenant_id=self._tenant_id,
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        return cred.get_token(_POWERBI_SCOPE).token

    def _get_token_for_user(self, user_id: int | None) -> str:
        """Resolve an OBO access token for the given user via stored refresh token."""
        from dash.connectors.oauth_obo import get_user_obo_token

        if not self.connection_id:
            raise RuntimeError(
                "PowerBI OBO mode requires connection_id at instantiation "
                "(set via instantiate_client from conn_row['id'])."
            )
        return get_user_obo_token(
            connection_id=self.connection_id,
            user_id=user_id,
            conn_creds={
                "tenant_id": self._tenant_id,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            scope=_POWERBI_SCOPE,
        )

    def _headers(self) -> dict:
        if self.auth_mode == "obo":
            user_id = _OBO_USER_CTX.get()
            tok = self._get_token_for_user(user_id)
        else:
            tok = self._get_token()
        return {
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        }

    def _datasets_url(self) -> str:
        return f"{self.api_base}/groups/{self.workspace_id}/datasets"

    def _execute_queries_url(self) -> str:
        return (
            f"{self.api_base}/groups/{self.workspace_id}"
            f"/datasets/{self.dataset_id}/executeQueries"
        )

    # ------------------------------------------------------------------
    # ABC implementation
    # ------------------------------------------------------------------

    def test_connection(self) -> dict:
        try:
            resp = _httpx.get(self._datasets_url(), headers=self._headers(), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            datasets = data.get("value", [])
            return {
                "success": True,
                "message": f"Connected to PowerBI workspace.",
                "tables_visible": len(datasets),
            }
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
        if not self.dataset_id:
            return []

        # Use DMV query to list tables and columns
        dax_query = "EVALUATE UNION(SELECTCOLUMNS(INFO.COLUMNS(), \"table\", [TableName], \"col\", [ExplicitName], \"dtype\", [DataTypeName]))"
        try:
            rows = self._run_dax(dax_query, max_rows=5000)
        except Exception:
            return []

        tables: dict[str, list] = {}
        for row in rows.to_dict("records"):
            table_name = str(row.get("[table]", row.get("table", "")))
            col_name = str(row.get("[col]", row.get("col", "")))
            dtype = str(row.get("[dtype]", row.get("dtype", "unknown")))
            if not table_name:
                continue
            tables.setdefault(table_name, []).append({"name": col_name, "dtype": dtype})
            if len(tables) >= max_tables:
                break

        return [
            {"schema": "powerbi", "table": table, "columns": cols}
            for table, cols in tables.items()
        ]

    def execute_query(self, sql: str, timeout_s: int = 60, max_rows: int = 10000) -> pd.DataFrame:
        if not self.dataset_id:
            raise ValueError(
                "dataset_id is required for execute_query. "
                "Set dataset_id in the PowerBI connection config."
            )
        return self._run_dax(sql, timeout_s=timeout_s, max_rows=max_rows)

    def execute_query_stream(
        self,
        sql: str,
        chunk_size: int = 1000,
        timeout_s: int = 60,
        max_rows: int = 100000,
    ):
        """PowerBI DAX REST API returns the full result in one shot — no native streaming.

        Caller in app/connectors_v2.py catches NotImplementedError and falls back to
        execute_query + manual chunking, so this raises to take that path.
        """
        raise NotImplementedError(
            "PowerBI DAX executeQueries returns full result — no native streaming. "
            "Use execute_query() then chunk client-side."
        )

    def prompt_schema(self) -> str:
        lines = [
            "[Microsoft PowerBI — DAX dialect]",
            f"API Base: {self.api_base}",
            f"Workspace: {self.workspace_id}",
        ]
        if self.dataset_id:
            lines.append(f"Dataset: {self.dataset_id}")
            try:
                tables = self.get_schemas()
                lines.append("")
                for t in tables:
                    col_str = ", ".join(
                        f"{c['name']} {c['dtype']}" for c in t["columns"]
                    )
                    lines.append(f"  {t['table']}({col_str})")
            except Exception as exc:
                lines.append(f"Schema unavailable: {exc}")
        else:
            lines.append("No dataset_id configured — schema unavailable.")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # DAX executor
    # ------------------------------------------------------------------

    def _run_dax(self, dax: str, timeout_s: int = 60, max_rows: int = 10000) -> pd.DataFrame:
        payload = json.dumps({"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}})
        try:
            resp = _httpx.post(
                self._execute_queries_url(),
                headers=self._headers(),
                content=payload,
                timeout=timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            from dash.connectors.safety import safe_error_message

            raise RuntimeError(
                safe_error_message(exc, {"client_secret": self._client_secret})
            ) from exc

        results = data.get("results", [])
        if not results:
            return pd.DataFrame()

        tables = results[0].get("tables", [])
        if not tables:
            return pd.DataFrame()

        rows = tables[0].get("rows", [])
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        if len(df) > max_rows:
            df = df.iloc[:max_rows]
        return df
