"""Standalone throwaway-Postgres bootstrap for training profiling.

Creates a project schema and loads mockup CSVs into it, mimicking what the
upload pipeline produces, so the profiler can run training against
real-shaped data WITHOUT touching the production dash DB.

The dash app derives a project's PG schema name from its slug via
``re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]`` (see app/upload.py). Tables
live in that schema. We mirror only the essential parts:

  * create the schema (drop + recreate),
  * load each CSV into ``<schema>.<filename_without_ext>`` with reasonable
    dtypes (date-looking columns -> date, numeric-looking -> numeric, rest text),
  * best-effort register the project + per-table metadata in the public.*
    tables that training step functions query.

SAFETY: every destructive op must be preceded by ``assert_throwaway(db_url)``
so we can NEVER reset/load against the prod dash DB.
"""

from __future__ import annotations

import os
import re
import glob
import json
import logging
from urllib.parse import urlsplit

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

log = logging.getLogger("profile.db_setup")

# Columns whose names suggest a date (matched case-insensitively, substring).
_DATE_HINT = ("date", "_dt", "datetime", "timestamp", "_at")


def derive_schema(slug: str) -> str:
    """Derive the PG schema name from a project slug (mirrors app/upload.py)."""
    return re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]


def make_engine(db_url: str):
    """SQLAlchemy engine (NullPool) for the throwaway PG."""
    return create_engine(db_url, poolclass=NullPool, future=True)


def assert_throwaway(db_url: str) -> None:
    """Raise unless db_url clearly points at the throwaway profiling PG.

    Safe iff ANY of:
      * port == 55432, OR
      * host contains 'pg-profile', OR
      * env PROFILE_DB == '1'.
    Called before any destructive op so we never touch the prod dash DB.
    """
    if os.environ.get("PROFILE_DB") == "1":
        return
    try:
        parts = urlsplit(db_url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"assert_throwaway: could not parse db_url ({e!r}); refusing to proceed"
        ) from e
    if port == 55432:
        return
    if "pg-profile" in host:
        return
    raise RuntimeError(
        "assert_throwaway: refusing destructive op — db_url does not look like the "
        f"throwaway profiling PG (host={host!r}, port={port!r}). "
        "Expected port 55432, host containing 'pg-profile', or env PROFILE_DB=1."
    )


def reset_schema(engine, slug: str) -> str:
    """DROP SCHEMA IF EXISTS <derived> CASCADE; CREATE SCHEMA. Returns schema name."""
    assert_throwaway(str(engine.url))
    schema = derive_schema(slug)
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    log.info("reset_schema: recreated schema %r for slug %r", schema, slug)
    return schema


def _coerce_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Coerce date-looking columns to date and numeric-looking to numeric.

    Returns the coerced DataFrame and a {col: sql_type} map for to_sql dtype.
    Anything not date/numeric stays text.
    """
    from sqlalchemy.types import Date, Numeric, Text

    dtype_map: dict = {}
    for col in df.columns:
        name = str(col).lower()
        series = df[col]

        # 1) Date by name hint -> try parse.
        if any(h in name for h in _DATE_HINT):
            parsed = pd.to_datetime(series, errors="coerce")
            # Accept if a good fraction of non-null values parsed.
            nonnull = series.notna().sum()
            if nonnull == 0 or parsed.notna().sum() >= 0.7 * nonnull:
                df[col] = parsed.dt.date
                dtype_map[col] = Date()
                continue

        # 2) Numeric: try to coerce; accept if mostly numeric.
        if not pd.api.types.is_numeric_dtype(series):
            coerced = pd.to_numeric(series, errors="coerce")
            nonnull = series.notna().sum()
            if nonnull > 0 and coerced.notna().sum() >= 0.95 * nonnull:
                df[col] = coerced
                dtype_map[col] = Numeric()
                continue
            # else fall through to text
            df[col] = series.astype("string").where(series.notna(), None)
            dtype_map[col] = Text()
        else:
            dtype_map[col] = Numeric()

    return df, dtype_map


def load_csvs(engine, slug: str, data_dir: str = "/tmp/profile_data") -> dict:
    """Load all *.csv in data_dir into <schema>.<filename_without_ext>.

    Coerce date-looking columns to date, numeric-looking to numeric.
    Returns {table_name: row_count}.
    """
    assert_throwaway(str(engine.url))
    schema = derive_schema(slug)
    counts: dict = {}

    files = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
    if not files:
        log.warning("load_csvs: no CSVs found in %s", data_dir)
        return counts

    for path in files:
        table = os.path.splitext(os.path.basename(path))[0]
        df = pd.read_csv(path)
        df, dtype_map = _coerce_columns(df)
        df.to_sql(
            table,
            engine,
            schema=schema,
            if_exists="replace",
            index=False,
            dtype=dtype_map,
            chunksize=5000,
            method="multi",
        )
        counts[table] = int(len(df))
        log.info("load_csvs: loaded %s.%s (%d rows)", schema, table, len(df))

    return counts


def register_table_metadata(engine, slug: str) -> None:
    """Best-effort: insert minimal rows into public.dash_table_metadata + create the
    project row in public.dash_projects so the training step functions don't no-op.

    Idempotent (ON CONFLICT DO NOTHING). Fail-soft — any failure is logged and
    swallowed so it never blocks the profiling run.
    """
    schema = derive_schema(slug)
    try:
        with engine.begin() as conn:
            # Ensure the public tables exist (in case this throwaway DB is bare).
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS public.dash_projects (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    slug TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    agent_role TEXT DEFAULT '',
                    agent_personality TEXT DEFAULT 'friendly',
                    schema_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS public.dash_table_metadata (
                    id SERIAL PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(project_slug, table_name)
                )
            """))

            # Project row — minimal required columns (user_id nullable here since
            # this is a standalone throwaway DB with no real users).
            conn.execute(
                text("""
                    INSERT INTO public.dash_projects (slug, name, agent_name, schema_name)
                    VALUES (:slug, :name, :agent, :schema)
                    ON CONFLICT (slug) DO NOTHING
                """),
                {
                    "slug": slug,
                    "name": f"Profile {slug}",
                    "agent": "ProfileBot",
                    "schema": schema,
                },
            )

            # Per-table metadata rows. Pull table + column names from the loaded
            # schema so metadata reflects real shape.
            rows = conn.execute(
                text("""
                    SELECT table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = :schema
                    ORDER BY table_name, ordinal_position
                """),
                {"schema": schema},
            ).fetchall()

            by_table: dict[str, list[dict]] = {}
            for tname, cname, dtype in rows:
                by_table.setdefault(tname, []).append({"name": cname, "type": dtype})

            for tname, cols in by_table.items():
                meta = {
                    "table_name": tname,
                    "schema": schema,
                    "columns": cols,
                    "source": "profile_db_setup",
                }
                conn.execute(
                    text("""
                        INSERT INTO public.dash_table_metadata (project_slug, table_name, metadata)
                        VALUES (:slug, :table, CAST(:meta AS jsonb))
                        ON CONFLICT (project_slug, table_name) DO NOTHING
                    """),
                    {"slug": slug, "table": tname, "meta": json.dumps(meta, default=str)},
                )
        log.info("register_table_metadata: registered project %r + %d table(s)",
                 slug, len(by_table) if 'by_table' in dir() else 0)
    except Exception as e:  # noqa: BLE001 — best-effort, must never block profiling
        log.warning("register_table_metadata: best-effort registration failed: %r", e)


def bootstrap(db_url: str, slug: str, data_dir: str = "/tmp/profile_data") -> dict:
    """Full one-shot: guard -> engine -> reset schema -> load CSVs -> register metadata.

    Returns {schema, tables: {name: rows}}.
    """
    assert_throwaway(db_url)
    engine = make_engine(db_url)
    try:
        schema = reset_schema(engine, slug)
        counts = load_csvs(engine, slug, data_dir)
        register_table_metadata(engine, slug)
        return {"schema": schema, "tables": counts}
    finally:
        engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    import argparse

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Bootstrap throwaway profiling Postgres")
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--data-dir", default="/tmp/profile_data")
    args = ap.parse_args()
    print(bootstrap(args.db_url, args.slug, args.data_dir))
