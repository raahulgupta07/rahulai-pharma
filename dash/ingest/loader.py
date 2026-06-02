"""
dash.ingest.loader
==================

Idempotent load of a validated DataFrame into a project's Postgres schema.

Keyed by the contract's load_key strategy; stamps lineage on every row so
any load can be reversed by batch_id or period.

Public API
----------
compute_row_key      : sha256 key per row for single/composite strategies
stamp_lineage        : add 6 lineage columns to a DataFrame copy
ensure_columns       : ALTER TABLE … ADD COLUMN IF NOT EXISTS for new cols
table_exists         : check whether a schema.table is present
file_hash_seen       : check whether a content_hash is already present
delete_where_period  : DELETE WHERE _period=… (reversible replace)
delete_where_batch   : DELETE WHERE _batch_id=… (full undo)
promote_file         : THE CORE — decide + execute the idempotent load
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from db.session import get_sql_engine  # CACHED SHARED — NEVER .dispose()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Row-level key
# ---------------------------------------------------------------------------

def compute_row_key(df: pd.DataFrame, key_cols: list[str]) -> pd.Series:
    """Return a Series of sha256 hex strings, one per row.

    The hash input is the pipe-delimited concatenation of str(value) for each
    key column.  If *key_cols* is empty (or None) every element is None.
    """
    if not key_cols:
        return pd.Series([None] * len(df), index=df.index)

    def _hash_row(row: pd.Series) -> str:
        raw = "||".join(str(row[c]) for c in key_cols)
        return hashlib.sha256(raw.encode()).hexdigest()

    return df.apply(_hash_row, axis=1)


# ---------------------------------------------------------------------------
# Lineage stamping
# ---------------------------------------------------------------------------

def stamp_lineage(df: pd.DataFrame, lineage: dict, load_key: dict) -> pd.DataFrame:
    """Return a COPY of *df* with six lineage columns added.

    Columns stamped
    ---------------
    _source_file   TEXT
    _period        TEXT | None
    _batch_id      TEXT
    _content_hash  TEXT
    _row_key       TEXT | None   (only for single / composite strategies)
    _ingested_at   TIMESTAMP UTC
    """
    df2 = df.copy()

    strategy = (load_key or {}).get("strategy", "")
    key_cols: list[str] = (load_key or {}).get("columns", []) or []

    if strategy in ("single", "composite"):
        row_key = compute_row_key(df2, key_cols)
    else:
        row_key = pd.Series([None] * len(df2), index=df2.index)

    df2["_source_file"] = lineage.get("source_file")
    df2["_period"] = lineage.get("period")
    df2["_batch_id"] = lineage.get("batch_id")
    df2["_content_hash"] = lineage.get("content_hash")
    df2["_row_key"] = row_key
    df2["_ingested_at"] = pd.Timestamp.utcnow()

    return df2


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def table_exists(engine: Engine, schema: str, table: str) -> bool:
    """Return True when *schema.table* exists in the database."""
    try:
        insp = inspect(engine)
        return insp.has_table(table, schema=schema)
    except Exception as exc:
        logger.warning("table_exists(%s.%s) error: %s", schema, table, exc)
        return False


def ensure_columns(engine: Engine, schema: str, table: str, df: pd.DataFrame) -> None:
    """Add any DataFrame column that is missing from the live table as TEXT.

    Fail-soft: a column that cannot be added logs a warning and is skipped.
    """
    try:
        insp = inspect(engine)
        existing = {c["name"] for c in insp.get_columns(table, schema=schema)}
    except Exception as exc:
        logger.warning("ensure_columns: could not introspect %s.%s: %s", schema, table, exc)
        return

    for col in df.columns:
        if col not in existing:
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE "{schema}"."{table}" '
                        f'ADD COLUMN IF NOT EXISTS "{col}" TEXT'
                    ))
                logger.info("ensure_columns: added column %s to %s.%s", col, schema, table)
            except Exception as exc:
                logger.warning(
                    "ensure_columns: could not add column %s to %s.%s: %s",
                    col, schema, table, exc,
                )


# ---------------------------------------------------------------------------
# Hash + period dedup
# ---------------------------------------------------------------------------

def file_hash_seen(engine: Engine, schema: str, table: str, content_hash: str) -> bool:
    """Return True when *content_hash* is already present in the table.

    Fail-soft: any error returns False so the caller proceeds with the load.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    f'SELECT 1 FROM "{schema}"."{table}" '
                    f'WHERE _content_hash = :h LIMIT 1'
                ),
                {"h": content_hash},
            ).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("file_hash_seen(%s.%s): %s", schema, table, exc)
        return False


def delete_where_period(engine: Engine, schema: str, table: str, period: str) -> int:
    """DELETE rows where _period = *period*.  Returns rowcount; fail-soft 0."""
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(f'DELETE FROM "{schema}"."{table}" WHERE _period = :p'),
                {"p": period},
            )
        count = result.rowcount
        logger.info("delete_where_period: removed %d rows (%s) from %s.%s", count, period, schema, table)
        return count
    except Exception as exc:
        logger.warning("delete_where_period(%s.%s, %s): %s", schema, table, period, exc)
        return 0


def delete_where_batch(engine: Engine, schema: str, table: str, batch_id: str) -> int:
    """DELETE rows where _batch_id = *batch_id*.  Returns rowcount; fail-soft 0."""
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(f'DELETE FROM "{schema}"."{table}" WHERE _batch_id = :b'),
                {"b": batch_id},
            )
        count = result.rowcount
        logger.info("delete_where_batch: removed %d rows (%s) from %s.%s", count, batch_id, schema, table)
        return count
    except Exception as exc:
        logger.warning("delete_where_batch(%s.%s, %s): %s", schema, table, batch_id, exc)
        return 0


# ---------------------------------------------------------------------------
# Core loader
# ---------------------------------------------------------------------------

def promote_file(
    engine: Engine,
    schema: str,
    target_table: str,
    df: pd.DataFrame,
    contract: dict,
    lineage: dict,
    *,
    mode_hint: str = "auto",
) -> dict[str, Any]:
    """Idempotent load of *df* into *schema.target_table*.

    Returns a result dict with keys:
        action        : "create" | "append" | "replace_period" | "upsert" | "skip_duplicate" | "error"
        rows_loaded   : int
        rows_skipped  : int
        table         : target_table
        load_key      : the contract load_key dict
        note          : human-readable summary string
    """
    load_key: dict = contract.get("load_key", {}) or {}
    strategy: str = load_key.get("strategy", "content_hash")
    content_hash: str | None = lineage.get("content_hash") or None
    period: str | None = lineage.get("period") or None

    _result_base = {
        "table": target_table,
        "load_key": load_key,
        "rows_skipped": 0,
    }

    try:
        # 1. Stamp lineage onto a copy of the DataFrame
        df2 = stamp_lineage(df, lineage, load_key)

        # 2. Table does NOT exist → create
        if not table_exists(engine, schema, target_table):
            if_exists_mode = "replace" if mode_hint == "replace" else "append"
            df2.to_sql(
                target_table,
                engine,
                schema=schema,
                if_exists=if_exists_mode,
                index=False,
            )
            note = f"Created table {schema}.{target_table} with {len(df2)} rows."
            logger.info(note)
            return {**_result_base, "action": "create", "rows_loaded": len(df2), "note": note}

        # Table exists from here on -----------------------------------------------

        # 3. Ensure new columns exist before any INSERT
        ensure_columns(engine, schema, target_table, df2)

        # 4. File-level dedup (always, regardless of strategy)
        if content_hash and file_hash_seen(engine, schema, target_table, content_hash):
            note = (
                f"Skipped: content_hash {content_hash!r} already present in "
                f"{schema}.{target_table}."
            )
            logger.info(note)
            return {
                **_result_base,
                "action": "skip_duplicate",
                "rows_loaded": 0,
                "note": note,
            }

        # 5. Strategy-specific logic
        if strategy == "content_hash":
            # File-hash guard above already prevents whole-file duplication.
            df2.to_sql(target_table, engine, schema=schema, if_exists="append", index=False)
            note = f"Appended {len(df2)} rows to {schema}.{target_table} (content_hash strategy)."
            logger.info(note)
            return {**_result_base, "action": "append", "rows_loaded": len(df2), "note": note}

        elif strategy == "period":
            if period:
                deleted = delete_where_period(engine, schema, target_table, period)
                df2.to_sql(target_table, engine, schema=schema, if_exists="append", index=False)
                note = (
                    f"Replaced period {period!r} in {schema}.{target_table}: "
                    f"deleted {deleted}, inserted {len(df2)} rows."
                )
                logger.info(note)
                return {
                    **_result_base,
                    "action": "replace_period",
                    "rows_loaded": len(df2),
                    "note": note,
                }
            else:
                # No period info → plain append
                df2.to_sql(target_table, engine, schema=schema, if_exists="append", index=False)
                note = (
                    f"Appended {len(df2)} rows to {schema}.{target_table} "
                    f"(period strategy, no period value)."
                )
                logger.info(note)
                return {**_result_base, "action": "append", "rows_loaded": len(df2), "note": note}

        elif strategy in ("single", "composite"):
            # Read existing _row_key values (fail-soft to empty set)
            existing_keys: set[str] = set()
            try:
                with engine.connect() as conn:
                    rows = conn.execute(
                        text(f'SELECT "_row_key" FROM "{schema}"."{target_table}"')
                    ).fetchall()
                existing_keys = {r[0] for r in rows if r[0] is not None}
            except Exception as exc:
                logger.warning(
                    "promote_file: could not read existing _row_key from %s.%s: %s",
                    schema, target_table, exc,
                )

            # Split into new vs already-present
            mask_new = ~df2["_row_key"].isin(existing_keys)
            df_new = df2[mask_new]
            skipped = int((~mask_new).sum())

            if not df_new.empty:
                df_new.to_sql(target_table, engine, schema=schema, if_exists="append", index=False)

            note = (
                f"Upsert into {schema}.{target_table}: inserted {len(df_new)}, "
                f"skipped {skipped} duplicate row_key(s) ({strategy} strategy)."
            )
            logger.info(note)
            return {
                **_result_base,
                "action": "upsert",
                "rows_loaded": len(df_new),
                "rows_skipped": skipped,
                "note": note,
            }

        else:
            # Unknown strategy → plain append (safe fallback)
            df2.to_sql(target_table, engine, schema=schema, if_exists="append", index=False)
            note = (
                f"Appended {len(df2)} rows to {schema}.{target_table} "
                f"(unknown strategy {strategy!r}, fallback append)."
            )
            logger.warning(note)
            return {**_result_base, "action": "append", "rows_loaded": len(df2), "note": note}

    except Exception as exc:
        logger.error(
            "promote_file: fatal error loading into %s.%s: %s",
            schema, target_table, exc,
            exc_info=True,
        )
        return {
            **_result_base,
            "action": "error",
            "rows_loaded": 0,
            "note": str(exc),
        }
