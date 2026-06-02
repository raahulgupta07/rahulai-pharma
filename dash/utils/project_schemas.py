"""Project schema variant helpers for cleanup operations.

Centralizes the knowledge of which Postgres schemas + knowledge-table variants
exist per project, so `delete_project` (and any future cleanup path) can drop
them all without hand-maintaining the list at the call site.

Schema variants
---------------
Historically, project data has lived under multiple schema name conventions:
  - `proj_<slug>` (canonical) — built via `lambda s: s`
  - `user_proj_<slug>` (legacy orphan) — built via `lambda s: f"user_{s}"`
Plus reserved room for future `stage_` / `staging_` variants.

All drop functions accept an AUTOCOMMIT-enabled connection so a failure on one
variant does not poison subsequent drops (each statement is its own txn).
"""

from __future__ import annotations

from typing import Callable
from sqlalchemy import text


SCHEMA_VARIANTS: tuple[Callable[[str], str], ...] = (
    lambda s: s,                  # canonical: proj_<slug>
    lambda s: f"user_{s}",        # legacy orphan: user_proj_<slug>
    # Reserved for future variants (uncomment when needed):
    # lambda s: f"stage_{s}",
    # lambda s: f"staging_{s}",
)


def drop_all_project_schemas(conn, slug: str) -> list[str]:
    """Drop every schema variant for the given project slug.

    Runs `DROP SCHEMA IF EXISTS "<variant>" CASCADE` for each entry in
    `SCHEMA_VARIANTS`. Each drop is wrapped in its own try/except so a
    permission error or odd state on one variant does not block the rest.

    Requires `conn` to be on an AUTOCOMMIT-enabled engine — caller is
    responsible for opening the connection on an engine with
    `execution_options(isolation_level="AUTOCOMMIT")`.

    Returns the list of variant names that were attempted (in order).
    """
    attempted: list[str] = []
    for fn in SCHEMA_VARIANTS:
        variant = fn(slug)
        attempted.append(variant)
        try:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{variant}" CASCADE'))
        except Exception:
            pass
    return attempted


def drop_project_knowledge_tables(conn, slug: str) -> None:
    """Drop Agno PgVector knowledge tables for every schema variant.

    For each variant, drops the four knowledge tables that Agno creates
    under the `ai` schema:
      - ai.<variant>_knowledge
      - ai.<variant>_knowledge_contents
      - ai.<variant>_learnings
      - ai.<variant>_learnings_contents

    Per-table try/except so missing/locked tables do not block subsequent
    drops. Requires an AUTOCOMMIT-enabled connection.
    """
    suffixes = ("_knowledge", "_knowledge_contents", "_learnings", "_learnings_contents")
    for fn in SCHEMA_VARIANTS:
        variant = fn(slug)
        for suffix in suffixes:
            try:
                conn.execute(text(f'DROP TABLE IF EXISTS ai."{variant}{suffix}" CASCADE'))
            except Exception:
                pass
