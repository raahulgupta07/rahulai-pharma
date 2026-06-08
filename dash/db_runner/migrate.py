"""
Database Migration Auto-Runner
==============================

Applies db/migrations/*.sql files in lexical order on startup. Tracks
applied migrations in public.dash_migrations to ensure each runs once.

Multi-worker safe via pg_advisory_lock(72157423) — concurrent uvicorn
workers serialize on the lock; only one applies migrations per startup.

Each migration runs in its own transaction with
``SET search_path = dash, public, ai;`` so unqualified ``CREATE TABLE``
statements land in the project's primary schema (``dash``) instead of
the wrong default.

Failure behavior is governed by ``RAISE_ON_MIGRATION_FAIL``:
- ``0`` (default) → log warning + continue (existing DBs).
- ``1`` → re-raise (fresh DBs, fail-fast).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

_LOCK_KEY = 72157423
_TRACKING_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS public.dash_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now(),
    checksum   TEXT
)
"""

log = logging.getLogger("db_runner.migrate")


def _migrations_dir() -> Path:
    # dash/db_runner/migrate.py -> ../../db/migrations
    return Path(__file__).resolve().parent.parent.parent / "db" / "migrations"


def _sha256(text_content: str) -> str:
    return hashlib.sha256(text_content.encode("utf-8")).hexdigest()


def _check_schema_prefix(sql_text: str) -> list[str]:
    """Phase 8 informational validator: scan a migration's SQL for
    schema-prefix patterns that historically caused drift between code
    references and actual table locations.

    Returns a list of warning strings (empty if clean). Does NOT block
    migration apply — caller logs at WARN level only.

    Patterns flagged:
      - References mixing `dash.dash_X` AND `public.dash_X` in same file
      - Unqualified `dash_X` table refs (could resolve to either schema
        depending on search_path)
    """
    warnings: list[str] = []
    dash_qualified = set(re.findall(r"\bdash\.(dash_[a-z_]+)\b", sql_text, re.I))
    public_qualified = set(re.findall(r"\bpublic\.(dash_[a-z_]+)\b", sql_text, re.I))

    mixed = dash_qualified & public_qualified
    if mixed:
        warnings.append(
            f"mixed schema refs (same table in both dash. and public.): "
            f"{sorted(mixed)[:5]}"
        )

    # Look for unqualified `FROM dash_X` / `JOIN dash_X` / `INSERT INTO dash_X`
    # that aren't preceded by a schema prefix.
    unqualified_pat = re.compile(
        r"(?:FROM|JOIN|INTO|UPDATE)\s+(?!dash\.|public\.|ai\.)(dash_[a-z_]+)",
        re.I,
    )
    unqualified = set(unqualified_pat.findall(sql_text))
    if unqualified:
        warnings.append(
            f"unqualified dash_* table refs (search_path-dependent): "
            f"{sorted(unqualified)[:5]}"
        )

    return warnings


def run_migrations() -> dict[str, Any]:
    """Apply pending SQL migrations.

    Returns ``{"applied": [filenames], "skipped": int, "errors": [strings]}``.
    On failure, behavior depends on ``RAISE_ON_MIGRATION_FAIL`` env var.
    """
    raise_on_fail = os.environ.get("RAISE_ON_MIGRATION_FAIL", "0") in (
        "1", "true", "TRUE", "yes",
    )
    applied: list[str] = []
    errors: list[str] = []
    skipped = 0

    migrations_dir = _migrations_dir()
    if not migrations_dir.is_dir():
        log.warning(f"migrations directory not found: {migrations_dir}")
        return {"applied": applied, "skipped": skipped, "errors": [f"missing dir: {migrations_dir}"]}

    files = sorted(migrations_dir.glob("*.sql"))
    log.info(f"migration scan: {len(files)} *.sql file(s) found in {migrations_dir}")
    for _p in files:
        log.debug(f"  scanned: {_p.name}")
    if not files:
        log.info("no migration files found")
        return {"applied": applied, "skipped": skipped, "errors": errors}

    engine = create_engine(db_url, poolclass=NullPool)
    lock_conn = None
    try:
        # Ensure tracking table exists (separate connection, autocommit).
        with engine.connect() as conn:
            conn.execute(text(_TRACKING_TABLE_DDL))
            conn.commit()

        # Acquire advisory lock on a dedicated connection — held until release.
        lock_conn = engine.connect()
        lock_conn.execute(text(f"SELECT pg_advisory_lock({_LOCK_KEY})"))

        # Re-fetch already-applied set under the lock.
        applied_set: set[str] = set()
        for row in lock_conn.execute(text("SELECT filename, checksum FROM public.dash_migrations")):
            applied_set.add(row[0])
        applied_checksums: dict[str, str] = {}
        for row in lock_conn.execute(text("SELECT filename, checksum FROM public.dash_migrations")):
            applied_checksums[row[0]] = row[1] or ""

        for path in files:
            fname = path.name
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as e:
                msg = f"migration {fname} read failed: {e}"
                log.warning(msg)
                errors.append(msg)
                if raise_on_fail:
                    raise
                continue

            checksum = _sha256(content)

            # Phase 8: schema-prefix validator (informational, non-blocking).
            # Only lint migrations about to be APPLIED — re-linting the full
            # already-applied set on every boot is pure noise.
            if fname not in applied_set:
                try:
                    _sp_warnings = _check_schema_prefix(content)
                    for _w in _sp_warnings:
                        log.warning(f"schema-prefix [{fname}]: {_w}")
                except Exception:
                    pass

            if fname in applied_set:
                prior = applied_checksums.get(fname, "")
                if prior and prior != checksum:
                    log.warning(
                        f"migration {fname} checksum drift "
                        f"(applied={prior[:8]} now={checksum[:8]}); "
                        "migrations are immutable, not re-applying"
                    )
                else:
                    # Issue #12 + #27: every skip is explained at INFO so
                    # ops can see WHY a migration didn't run (was: silent).
                    log.info(f"migration {fname} SKIPPED (already applied, checksum match)")
                skipped += 1
                continue

            # Apply in its own transaction with scoped search_path.
            # Issue #27: capture psql NOTICE output. If only NOTICEs (no
            # ERROR), log as INFO (not WARN) — "relation already exists,
            # skipping" is expected for idempotent IF NOT EXISTS migrations.
            notices: list[str] = []
            try:
                with engine.connect() as conn:
                    # Hook psycopg notices for this connection
                    raw = getattr(conn.connection, "driver_connection", None) or conn.connection
                    _orig_handler = None
                    try:
                        def _notice_handler(diag):  # psycopg3 Diagnostic
                            try:
                                notices.append(getattr(diag, "message_primary", str(diag)))
                            except Exception:
                                notices.append(str(diag))
                        if hasattr(raw, "add_notice_handler"):
                            raw.add_notice_handler(_notice_handler)
                            _orig_handler = _notice_handler
                        elif hasattr(raw, "notices"):
                            # psycopg2-style list buffer
                            _orig_handler = raw.notices
                    except Exception:
                        pass
                    trans = conn.begin()
                    try:
                        conn.execute(text("SET search_path = dash, public, ai"))
                        conn.exec_driver_sql(content)
                        conn.execute(
                            text(
                                "INSERT INTO public.dash_migrations (filename, checksum) "
                                "VALUES (:f, :c) ON CONFLICT (filename) DO NOTHING"
                            ),
                            {"f": fname, "c": checksum},
                        )
                        trans.commit()
                    except Exception:
                        trans.rollback()
                        raise
                    # psycopg2-style: drain conn.notices
                    try:
                        raw_notices = getattr(raw, "notices", None)
                        if raw_notices and not notices:
                            notices.extend([str(n).strip() for n in list(raw_notices)])
                    except Exception:
                        pass
                applied.append(fname)
                if notices:
                    # NOTICE-only => INFO (idempotent re-apply). No ERROR
                    # would have reached here (would have raised).
                    log.info(
                        f"applied migration: {fname} (with {len(notices)} NOTICE(s), idempotent)"
                    )
                    for n in notices[:5]:
                        log.info(f"  NOTICE [{fname}]: {n}")
                else:
                    log.info(f"applied migration: {fname}")
            except Exception as e:
                # Real ERROR — surface at WARN with notice context if any
                msg = f"migration {fname} failed: {e}"
                if notices:
                    msg += f" (also {len(notices)} NOTICE(s) before failure)"
                log.warning(msg)
                errors.append(msg)
                if raise_on_fail:
                    raise

        return {"applied": applied, "skipped": skipped, "errors": errors}
    finally:
        if lock_conn is not None:
            try:
                lock_conn.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))
            except Exception:
                pass
            try:
                lock_conn.close()
            except Exception:
                pass
        try:
            engine.dispose()
        except Exception:
            pass


def list_migrations_status() -> dict[str, Any]:
    """Return all *.sql files vs dash_migrations table.

    Used by /api/admin/migrations/status (Issue #12).
    """
    migrations_dir = _migrations_dir()
    files = sorted(migrations_dir.glob("*.sql")) if migrations_dir.is_dir() else []
    file_meta = [
        {"filename": p.name, "checksum": _sha256(p.read_text(encoding="utf-8"))}
        for p in files
    ]
    applied: dict[str, str] = {}
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            conn.execute(text(_TRACKING_TABLE_DDL))
            conn.commit()
            for row in conn.execute(text(
                "SELECT filename, checksum, applied_at FROM public.dash_migrations"
            )):
                applied[row[0]] = {
                    "checksum": row[1] or "",
                    "applied_at": str(row[2]) if row[2] else None,
                }
    finally:
        engine.dispose()

    out = []
    pending = 0
    for f in file_meta:
        rec = applied.get(f["filename"])
        if rec is None:
            status = "pending"
            pending += 1
        elif rec["checksum"] and rec["checksum"] != f["checksum"]:
            status = "applied_drift"
        else:
            status = "applied"
        out.append({
            "filename": f["filename"],
            "status": status,
            "file_checksum": f["checksum"][:12],
            "applied_checksum": (rec["checksum"][:12] if rec and rec["checksum"] else None),
            "applied_at": rec["applied_at"] if rec else None,
        })
    return {
        "total_files": len(file_meta),
        "applied": len(applied),
        "pending": pending,
        "migrations": out,
    }
