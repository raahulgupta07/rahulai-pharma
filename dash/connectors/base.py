"""ConnectorClient ABC — frozen contract §1."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

import pandas as pd


class ConnectorClient(ABC):
    """Abstract base for all data-source connector clients.

    Subclasses must accept merged config + decrypted credentials as **kwargs in __init__.
    """

    @abstractmethod
    def test_connection(self) -> dict:
        """{'success': bool, 'message': str, 'tables_visible': int|None}"""

    @abstractmethod
    def get_schemas(self, max_tables: int = 50) -> list[dict]:
        """[{'schema': str, 'table': str, 'columns': [{'name', 'dtype'}]}]"""

    @abstractmethod
    def execute_query(self, sql: str, timeout_s: int = 60, max_rows: int = 10000) -> pd.DataFrame:
        """Read-only SQL. Raise on error. Truncate to max_rows."""

    @abstractmethod
    def prompt_schema(self) -> str:
        """Human-readable schema block for LLM context. Include dialect note."""

    @abstractmethod
    def execute_query_stream(
        self,
        sql: str,
        chunk_size: int = 1000,
        timeout_s: int = 60,
        max_rows: int = 100000,
    ) -> Iterator[list[dict]]:
        """Read-only SQL streaming. Yields batches of row-dicts (each batch ≤ chunk_size).

        Raise NotImplementedError if native streaming not supported (caller will fall back
        to execute_query + chunking). Truncate at max_rows.
        """

    def _creds_for_scrub(self) -> list[str]:
        """Return secret strings (password / client_secret / credentials_json …)
        that must be scrubbed from any error message before it leaves the process.

        Default: nothing to scrub. Subclasses with stored credentials override.
        """
        return []
