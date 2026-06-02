"""
dash.ingest.staging
===================
Staged data-ingest pipeline helpers.

Responsibilities
----------------
- Generate batch identifiers.
- Manage on-disk staging directories under ``knowledge/{project}/staging/``.
- Copy source files into a batch, building frozen manifest entries.
- Atomically write / read manifest JSON files.
- Upsert manifest metadata into ``public.dash_ingest_batches`` and
  ``public.dash_ingest_files`` via the shared SQLAlchemy engine.
- Quarantine individual files within a batch.
- Check whether a content hash already exists in a target table column
  (deduplication guard for downstream loaders).

Rules
-----
- Import ``get_sql_engine`` from ``db.session``; NEVER call ``.dispose()`` on it.
- Every ``create_engine()`` in this module uses ``poolclass=NullPool``.
- JSONB parameters use ``CAST(:x AS jsonb)`` — never ``:x::jsonb``.
- All DB writes are fail-soft (try/except + log); never raise from helpers.
- JSON writes are atomic: write to a temp file then ``os.replace``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .content import content_hash as _content_hash

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# KNOWLEDGE_DIR resolution — mirrors dash.paths but avoids circular imports
# ---------------------------------------------------------------------------
try:
    from dash.paths import KNOWLEDGE_DIR as _KNOWLEDGE_DIR
except Exception:  # pragma: no cover — import may fail in isolated test envs
    _KNOWLEDGE_DIR = Path("knowledge")

_KNOWLEDGE_DIR = Path(_KNOWLEDGE_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return current UTC time as an ISO-8601 string (e.g. 2026-05-21T12:34:56.789Z)."""
    return datetime.now(timezone.utc).isoformat()


def _sanitize(name: str) -> str:
    """Lowercase *name*, replace non-[a-z0-9_] chars with underscores, cap at 50."""
    s = re.sub(r"[^a-z0-9_]", "_", name.lower())
    return s[:50]


# ---------------------------------------------------------------------------
# Public API — identifiers and paths
# ---------------------------------------------------------------------------

def new_batch_id() -> str:
    """Return a fresh, unique batch identifier.

    Format: ``batch_YYYYMMDD_HHMMSS_<6-hex-chars>``

    The timestamp component is always UTC.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(3)
    return f"batch_{ts}_{rand}"


def staging_root(project: str) -> Path:
    """Return the staging root directory for *project*.

    This is ``<KNOWLEDGE_DIR>/{project}/staging``.  The directory is created
    (parents included) if it does not already exist.
    """
    p = _KNOWLEDGE_DIR / project / "staging"
    p.mkdir(parents=True, exist_ok=True)
    return p


def batch_dir(project: str, batch_id: str) -> Path:
    """Return the directory for a specific batch, creating it if needed."""
    d = staging_root(project) / batch_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Core staging helper
# ---------------------------------------------------------------------------

def stage_file(
    project: str,
    batch_id: str,
    src_path: str,
    filename: str,
    *,
    df: "pd.DataFrame | None" = None,
    ext: str,
    quality: "dict | None" = None,
) -> dict:
    """Copy *src_path* into the batch directory and return a manifest entry.

    Parameters
    ----------
    project:
        Project slug (used to locate the staging directory).
    batch_id:
        Batch identifier (from :func:`new_batch_id`).
    src_path:
        Absolute path to the source file that will be copied.
    filename:
        Target filename inside the batch directory.
    df:
        Optional ``pandas.DataFrame``; if provided, ``rows``, ``cols``, and
        ``columns`` are derived from it.  Otherwise these default to ``0``,
        ``0``, and ``[]``.
    ext:
        File extension (e.g. ``"csv"``, ``"xlsx"``), without the leading dot.
    quality:
        Pre-computed quality dict with keys ``score``, ``problems``, and
        ``fixes``.  Defaults to ``{"score": 100, "problems": [], "fixes": []}``.

    Returns
    -------
    dict
        A manifest ``files[]`` entry matching the frozen interface shape.
    """
    dest = batch_dir(project, batch_id) / filename
    shutil.copy2(src_path, dest)

    raw_bytes = dest.read_bytes()
    chash = _content_hash(raw_bytes)

    if df is not None:
        rows = int(len(df))
        cols = int(len(df.columns))
        columns = [str(c) for c in df.columns.tolist()]
    else:
        rows = 0
        cols = 0
        columns = []

    if quality is None:
        quality = {"score": 100, "problems": [], "fixes": []}

    dataset = _sanitize(Path(filename).stem)

    return {
        "filename": filename,
        "staged_path": str(dest),
        "ext": ext,
        "content_hash": chash,
        "rows": rows,
        "cols": cols,
        "columns": columns,
        "dataset": dataset,
        "quality": quality,
        "verdict": "new",
        "target_table": None,
        "load_key": {"strategy": "", "columns": []},
        "diff": {},
        "period": None,
        "status": "ready",
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

def _manifest_path(project: str, batch_id: str) -> Path:
    return batch_dir(project, batch_id) / "manifest.json"


def write_manifest(project: str, batch_id: str, manifest: dict) -> None:
    """Atomically write *manifest* to disk and upsert its DB rows.

    The manifest JSON is written via ``tempfile + os.replace`` so it is never
    partially written.  DB writes (``public.dash_ingest_batches`` and
    ``public.dash_ingest_files``) are fail-soft: any exception is logged and
    swallowed so callers are never broken by DB outages.

    Parameters
    ----------
    project:
        Project slug.
    batch_id:
        Batch identifier.
    manifest:
        Full manifest dict (see frozen interface in the module docstring).
    """
    # --- Atomic JSON write ---
    target = _manifest_path(project, batch_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent),
        suffix=".tmp.json",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # --- DB upsert (fail-soft) ---
    try:
        from db.session import get_write_engine
        from sqlalchemy import text

        engine = get_write_engine()
        status = manifest.get("status", "staged")
        file_count = len(manifest.get("files", []))

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO public.dash_ingest_batches
                        (batch_id, project_slug, status, file_count, manifest, created_at, updated_at)
                    VALUES
                        (:bid, :proj, :status, :fc,
                         CAST(:m AS jsonb),
                         now(), now())
                    ON CONFLICT (batch_id) DO UPDATE SET
                        status      = EXCLUDED.status,
                        file_count  = EXCLUDED.file_count,
                        manifest    = EXCLUDED.manifest,
                        updated_at  = now()
                    """
                ),
                {
                    "bid": batch_id,
                    "proj": project,
                    "status": status,
                    "fc": file_count,
                    "m": json.dumps(manifest, default=str),
                },
            )

            # Replace file rows for this batch
            conn.execute(
                text(
                    "DELETE FROM public.dash_ingest_files WHERE batch_id = :bid"
                ),
                {"bid": batch_id},
            )

            for f in manifest.get("files", []):
                load_key_json = json.dumps(f.get("load_key") or {}, default=str)
                conn.execute(
                    text(
                        """
                        INSERT INTO public.dash_ingest_files
                            (batch_id, project_slug, filename, content_hash,
                             dataset, verdict, target_table, load_key,
                             score, status, reason, rows, created_at)
                        VALUES
                            (:bid, :proj, :fn, :ch,
                             :ds, :verdict, :tt, CAST(:lk AS jsonb),
                             :score, :status, :reason, :rows, now())
                        """
                    ),
                    {
                        "bid": batch_id,
                        "proj": project,
                        "fn": f.get("filename"),
                        "ch": f.get("content_hash"),
                        "ds": f.get("dataset"),
                        "verdict": f.get("verdict", "new"),
                        "tt": f.get("target_table"),
                        "lk": load_key_json,
                        "score": (f.get("quality") or {}).get("score", 100),
                        "status": f.get("status", "ready"),
                        "reason": f.get("reason", ""),
                        "rows": f.get("rows", 0),
                    },
                )

    except Exception as exc:
        log.warning(
            "write_manifest: DB upsert failed for batch %s / project %s: %s",
            batch_id,
            project,
            exc,
        )


def read_manifest(project: str, batch_id: str) -> "dict | None":
    """Load and return the manifest for *batch_id* from disk.

    Returns ``None`` if the manifest file does not exist or cannot be parsed.
    """
    path = _manifest_path(project, batch_id)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        log.warning("read_manifest: failed to read %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Listing batches
# ---------------------------------------------------------------------------

def list_batches(project: str) -> "list[dict]":
    """Return manifests for *project*, newest first.

    The primary source is the on-disk manifests inside
    ``staging_root(project)``.  If none are found (e.g. knowledge directory
    does not exist), the function falls back to querying
    ``public.dash_ingest_batches`` in the DB.  Both sources are fail-soft.

    Returns
    -------
    list[dict]
        List of manifest dicts, sorted by ``created_at`` descending.
    """
    root = _KNOWLEDGE_DIR / project / "staging"

    manifests: list[dict] = []

    if root.exists():
        try:
            for subdir in sorted(root.iterdir(), reverse=True):
                if not subdir.is_dir():
                    continue
                m = read_manifest(project, subdir.name)
                if m:
                    manifests.append(m)
        except Exception as exc:
            log.warning("list_batches: disk scan failed for project %s: %s", project, exc)

    if manifests:
        # Sort descending by created_at string (ISO-8601 sorts lexicographically)
        manifests.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return manifests

    # --- DB fallback ---
    try:
        from db.session import get_write_engine
        from sqlalchemy import text

        engine = get_write_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT manifest
                    FROM public.dash_ingest_batches
                    WHERE project_slug = :proj
                    ORDER BY created_at DESC
                    """
                ),
                {"proj": project},
            ).fetchall()
        for row in rows:
            raw = row[0]
            if isinstance(raw, dict):
                manifests.append(raw)
            elif isinstance(raw, str):
                try:
                    manifests.append(json.loads(raw))
                except Exception:
                    pass
    except Exception as exc:
        log.warning(
            "list_batches: DB fallback failed for project %s: %s", project, exc
        )

    return manifests


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------

def quarantine_file(
    project: str,
    batch_id: str,
    filename: str,
    reason: str,
) -> None:
    """Move *filename* into the batch's quarantine sub-directory and update the manifest.

    The staged file is moved to ``<batch_dir>/quarantine/<filename>``.  The
    corresponding entry in the manifest is updated with
    ``status="quarantine"`` and ``reason=<reason>``, and the manifest is
    rewritten atomically.

    This function is fail-soft: errors are logged but never raised.

    Parameters
    ----------
    project:
        Project slug.
    batch_id:
        Batch identifier.
    filename:
        Name of the file (as it appears in the manifest ``files[]`` list).
    reason:
        Human-readable reason for quarantine.
    """
    try:
        b_dir = batch_dir(project, batch_id)
        q_dir = b_dir / "quarantine"
        q_dir.mkdir(parents=True, exist_ok=True)

        src = b_dir / filename
        dst = q_dir / filename

        if src.exists():
            shutil.move(str(src), str(dst))
        else:
            log.warning(
                "quarantine_file: source not found: %s (may already be quarantined)",
                src,
            )

        manifest = read_manifest(project, batch_id)
        if manifest is None:
            log.warning(
                "quarantine_file: manifest not found for batch %s, skipping update",
                batch_id,
            )
            return

        updated = False
        for entry in manifest.get("files", []):
            if entry.get("filename") == filename:
                entry["status"] = "quarantine"
                entry["reason"] = reason
                entry["staged_path"] = str(dst)
                updated = True

        if not updated:
            log.warning(
                "quarantine_file: filename %s not found in manifest %s",
                filename,
                batch_id,
            )

        write_manifest(project, batch_id, manifest)

    except Exception as exc:
        log.warning(
            "quarantine_file: failed for batch %s / file %s: %s",
            batch_id,
            filename,
            exc,
        )


# ---------------------------------------------------------------------------
# Deduplication helper
# ---------------------------------------------------------------------------

def content_hash_seen(
    project: str,
    table: str,
    schema: str,
    h: str,
) -> bool:
    """Return ``True`` if *h* already exists in ``{schema}.{table}._content_hash``.

    This is a best-effort check.  Any exception (table not found, column not
    found, DB unavailable) causes the function to return ``False`` rather than
    raising.

    Parameters
    ----------
    project:
        Project slug (used only for logging context; not used in the query).
    table:
        Unqualified table name.
    schema:
        PostgreSQL schema name.
    h:
        The SHA-256 hex digest to look up.

    Returns
    -------
    bool
        ``True`` if the hash was found; ``False`` otherwise (including on any
        error).
    """
    try:
        from db.session import get_write_engine
        from sqlalchemy import text

        engine = get_write_engine()
        # Use double-quoting to safely embed schema/table names.
        # Parameters (:h) are still parameterised to prevent injection.
        safe_schema = schema.replace('"', '""')
        safe_table = table.replace('"', '""')
        query = text(
            f'SELECT 1 FROM "{safe_schema}"."{safe_table}"'
            " WHERE _content_hash = :h LIMIT 1"
        )
        with engine.connect() as conn:
            row = conn.execute(query, {"h": h}).fetchone()
        return row is not None
    except Exception as exc:
        log.debug(
            "content_hash_seen: lookup failed for %s.%s (project=%s): %s",
            schema,
            table,
            project,
            exc,
        )
        return False
