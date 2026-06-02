"""BigQuery connector client — contract §1, §6, §12.

Soft-imports google.cloud.bigquery and google.oauth2.service_account per §12.
If either library is absent the import succeeds but __init__ raises RuntimeError
with an install hint so the caller gets a clear message.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# §12 soft-imports
# ---------------------------------------------------------------------------
try:
    from google.cloud import bigquery
    from google.cloud.bigquery import QueryJobConfig
    _HAVE_BQ = True
except ImportError:
    _HAVE_BQ = False

try:
    from google.oauth2 import service_account
    _HAVE_AUTH = True
except ImportError:
    _HAVE_AUTH = False

from dash.connectors.base import ConnectorClient

log = logging.getLogger(__name__)

_MISSING_MSG = (
    "BigQuery connector requires google-cloud-bigquery and google-auth. "
    "Install them with: pip install google-cloud-bigquery google-auth"
)


class BigQueryClient(ConnectorClient):
    """Google BigQuery connector.

    Accepts merged config + decrypted credentials kwargs (§7):
        project_id          str  — GCP project identifier
        dataset             str  — default dataset (comma-separated for multi)
        location            str  — query location, default "US"
        maximum_bytes_billed int|None — cost-guard cap, default None (unlimited)
        use_query_cache     bool — enable BQ query result cache, default True
        credentials_json    str  — full service-account JSON as a string paste
                                   OR a file path to the JSON file
    Any additional kwargs are silently absorbed.
    """

    def __init__(
        self,
        *,
        project_id: str,
        dataset: str,
        location: str = "US",
        maximum_bytes_billed: int | None = None,
        use_query_cache: bool = True,
        credentials_json: str,
        max_bytes_per_query: int | None = None,
        **kwargs: Any,
    ) -> None:
        if not _HAVE_BQ or not _HAVE_AUTH:
            raise RuntimeError(_MISSING_MSG)

        self.project_id = project_id
        self.dataset = dataset
        self.maximum_bytes_billed = maximum_bytes_billed
        self.use_query_cache = use_query_cache
        self.max_bytes_per_query = max_bytes_per_query
        self._last_cost_info: dict | None = None
        # Keep raw JSON around so we can scrub it from any error message.
        self._credentials_json = credentials_json

        creds = self._load_credentials(credentials_json)

        self.client: bigquery.Client = bigquery.Client(
            project=project_id,
            credentials=creds,
            location=location,
        )
        log.debug("BigQueryClient initialised for project=%s dataset=%s", project_id, dataset)

    # ------------------------------------------------------------------
    # credential loading helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_credentials(credentials_json: str) -> "service_account.Credentials":
        """Parse credentials_json as JSON string; fall back to file path."""
        # First attempt: treat value as an inline JSON string.
        try:
            info = json.loads(credentials_json)
            return service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/bigquery.readonly"],
            )
        except (json.JSONDecodeError, ValueError):
            pass

        # Second attempt: treat value as a file path.
        try:
            return service_account.Credentials.from_service_account_file(
                credentials_json,
                scopes=["https://www.googleapis.com/auth/bigquery.readonly"],
            )
        except Exception as exc:
            raise ValueError(
                f"credentials_json could not be parsed as JSON string or read as a "
                f"service-account file path. Error: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Cost estimation helpers
    # ------------------------------------------------------------------

    _TB_BYTES: int = 1099511627776  # 1 TiB in bytes (BigQuery billing unit)
    _PRICE_PER_TB: float = 5.0     # USD per TiB on-demand pricing

    def estimate_cost(self, sql: str) -> dict:
        """Dry-run a query and return byte/cost estimates.

        Uses QueryJobConfig(dry_run=True) which is free and fast (~10ms).
        Returns:
            total_bytes_processed  int
            total_bytes_billed     int
            estimated_cost_usd     float
        """
        dry_cfg = QueryJobConfig(dry_run=True, use_query_cache=False)
        job = self.client.query(sql, job_config=dry_cfg)
        bytes_processed = job.total_bytes_processed or 0
        bytes_billed = job.total_bytes_billed or 0
        estimated_cost_usd = (bytes_billed / self._TB_BYTES) * self._PRICE_PER_TB
        return {
            "total_bytes_processed": bytes_processed,
            "total_bytes_billed": bytes_billed,
            "estimated_cost_usd": estimated_cost_usd,
        }

    # ------------------------------------------------------------------
    # ConnectorClient interface
    # ------------------------------------------------------------------

    def _creds_for_scrub(self) -> list[str]:
        return [self._credentials_json] if getattr(self, "_credentials_json", None) else []

    def test_connection(self) -> dict:
        """List up to 10 datasets; return success/message."""
        try:
            datasets = list(self.client.list_datasets(max_results=10))
            n = len(datasets)
            return {
                "success": True,
                "message": f"connected, {n} datasets accessible",
                "tables_visible": None,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("BigQueryClient.test_connection failed: %s", exc)
            from dash.connectors.safety import safe_error_message

            return {
                "success": False,
                "message": safe_error_message(
                    exc, {"credentials_json": self._credentials_json}
                ),
                "tables_visible": None,
            }

    def get_schemas(self, max_tables: int = 50) -> list[dict]:
        """Return schema/column info for each table in the configured dataset(s).

        Supports a comma-separated ``dataset`` field for multi-dataset scans.
        Each entry: {'schema': dataset_id, 'table': table_id,
                     'columns': [{'name': ..., 'dtype': ...}]}
        """
        results: list[dict] = []

        datasets = [d.strip() for d in self.dataset.split(",") if d.strip()]

        for ds_id in datasets:
            dataset_ref = self.client.dataset(ds_id, project=self.project_id)
            try:
                table_items = list(
                    self.client.list_tables(dataset_ref, max_results=max_tables)
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("get_schemas: could not list tables in %s: %s", ds_id, exc)
                continue

            for table_item in table_items:
                table_ref = dataset_ref.table(table_item.table_id)
                try:
                    table = self.client.get_table(table_ref)
                    columns = [
                        {"name": f.name, "dtype": f.field_type}
                        for f in table.schema
                    ]
                    results.append(
                        {
                            "schema": ds_id,
                            "table": table_item.table_id,
                            "columns": columns,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "get_schemas: could not describe %s.%s: %s",
                        ds_id,
                        table_item.table_id,
                        exc,
                    )

        return results

    def execute_query(
        self,
        sql: str,
        timeout_s: int = 60,
        max_rows: int = 10000,
    ) -> pd.DataFrame:
        """Execute a Standard SQL query and return up to max_rows as a DataFrame.

        Raises on query error (BQ exceptions propagate to caller).
        """
        # Pre-flight cost guard: dry-run if max_bytes_per_query set.
        if self.max_bytes_per_query is not None:
            try:
                cost = self.estimate_cost(sql)
                self._last_cost_info = cost
                if cost["total_bytes_processed"] > self.max_bytes_per_query:
                    raise ValueError(
                        f"Query would scan {cost['total_bytes_processed']:,} bytes "
                        f"(~${cost['estimated_cost_usd']:.4f}), exceeds cap "
                        f"{self.max_bytes_per_query:,}. Add a WHERE filter or "
                        f"partition predicate to narrow scan."
                    )
                log.info(
                    "BQ pre-flight: %s bytes (~$%.4f), within cap",
                    f"{cost['total_bytes_processed']:,}",
                    cost["estimated_cost_usd"],
                )
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("BQ dry-run failed (will still execute): %s", exc)

        cfg = QueryJobConfig(
            maximum_bytes_billed=self.maximum_bytes_billed,
            use_query_cache=self.use_query_cache,
        )
        try:
            job = self.client.query(sql, job_config=cfg, timeout=timeout_s)
            rows = job.result(max_results=max_rows).to_dataframe()
            # Capture actual cost from job after execution.
            if job.total_bytes_billed:
                actual_cost = (job.total_bytes_billed / self._TB_BYTES) * self._PRICE_PER_TB
                self._last_cost_info = {
                    "total_bytes_processed": job.total_bytes_processed or 0,
                    "total_bytes_billed": job.total_bytes_billed or 0,
                    "estimated_cost_usd": actual_cost,
                }
                log.info(
                    "BQ executed: %s bytes billed (~$%.4f)",
                    f"{job.total_bytes_billed:,}",
                    actual_cost,
                )
        except Exception as exc:
            from dash.connectors.safety import safe_error_message

            raise RuntimeError(
                safe_error_message(
                    exc, {"credentials_json": self._credentials_json}
                )
            ) from exc
        return rows

    def execute_query_stream(
        self,
        sql: str,
        chunk_size: int = 1000,
        timeout_s: int = 60,
        max_rows: int = 100000,
    ):
        """Stream rows in batches via BigQuery RowIterator pages."""
        cfg = QueryJobConfig(
            maximum_bytes_billed=self.maximum_bytes_billed,
            use_query_cache=self.use_query_cache,
        )
        query_job = self.client.query(sql, job_config=cfg, timeout=timeout_s)
        row_iter = query_job.result(page_size=chunk_size, max_results=max_rows)
        total = 0
        for page in row_iter.pages:
            df = page.to_dataframe()
            if df.empty:
                continue
            if total + len(df) > max_rows:
                df = df.iloc[: max_rows - total]
            batch = df.to_dict("records")
            total += len(batch)
            yield batch
            if total >= max_rows:
                break

    def prompt_schema(self) -> str:
        """Human-readable schema block for LLM context (Standard SQL dialect)."""
        schemas = self.get_schemas()
        if schemas:
            table_lines = "\n".join(
                f"  {s['schema']}.{s['table']} "
                f"({', '.join(c['name'] for c in s['columns'][:10])}{'…' if len(s['columns']) > 10 else ''})"
                for s in schemas[:30]
            )
            tables_block = f"Tables:\n{table_lines}"
        else:
            tables_block = "Tables: (none discovered)"

        return (
            f"Google BigQuery (Standard SQL dialect). "
            f"Project={self.project_id} Dataset={self.dataset}. "
            f"Use backtick `project.dataset.table` for fully-qualified refs. "
            f"DATE/TIMESTAMP functions: CURRENT_DATE(), TIMESTAMP_SUB. "
            f"{tables_block}"
        )
