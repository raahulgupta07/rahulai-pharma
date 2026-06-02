"""Storage Tiering — declarative DB-vs-disk policy per knowledge subdir.

Lets huge corpora stay in PostgreSQL (pgvector) without bloating disk, OR stay
on disk without re-loading into DB on reindex. Default tier is `db_tracked`
(both DB + disk — current behavior).

Marker files under `knowledge/{slug}/{subdir}/`:
    .db_only    — content lives in DB only (disk copies evictable)
    .disk_only  — content lives on disk only (skip on next DB reindex)

If neither marker present → tier = `db_tracked`.

Public surface:
    mark_db_only(slug, subdir)        -> dict
    mark_disk_only(slug, subdir)      -> dict
    clear_tier(slug, subdir)          -> dict           (revert to db_tracked)
    get_tier(slug, subdir)            -> str            ('db_tracked'|'db_only'|'disk_only')
    list_tiers(slug)                  -> dict[subdir, tier]
    evict_db_only_files(slug, subdir, keep_index=True) -> dict
    restore_from_db(slug, subdir)     -> dict

All functions are sync, never raise (log + return error dicts on failure).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from dash.paths import KNOWLEDGE_DIR

logger = logging.getLogger(__name__)

TIER_DB_TRACKED = "db_tracked"
TIER_DB_ONLY = "db_only"
TIER_DISK_ONLY = "disk_only"

_DB_ONLY_MARKER = ".db_only"
_DISK_ONLY_MARKER = ".disk_only"

# Subdirs that are eligible for tiering. Restrict to known knowledge folders to
# avoid accidental traversal into git/system dirs.
_ALLOWED_SUBDIRS = {
    "tables", "business", "queries", "rules", "docs", "docs_raw",
    "doc_meta", "doc_structure", "dimensions", "training", "skills",
    "dreams", "staging", "synthesis", "table_sources", "workflows",
}


def _safe_slug(slug: str) -> str:
    if not slug or not re.match(r"^[a-z0-9_\-]+$", slug):
        raise ValueError(f"invalid slug: {slug!r}")
    return slug


def _safe_subdir(subdir: str) -> str:
    s = (subdir or "").strip().strip("/").strip("\\")
    if not s or not re.match(r"^[a-zA-Z0-9_\-]+$", s):
        raise ValueError(f"invalid subdir: {subdir!r}")
    if s not in _ALLOWED_SUBDIRS:
        # Be permissive but log — allows future subdirs without code change
        logger.debug("storage_tier: subdir %r not in allowlist; allowing", s)
    return s


def _subdir_path(slug: str, subdir: str) -> Path:
    return KNOWLEDGE_DIR / _safe_slug(slug) / _safe_subdir(subdir)


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── Tier marker management ─────────────────────────────────────────────────
def _write_marker(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text("", encoding="utf-8")


def _clear_markers(path: Path) -> int:
    cleared = 0
    for m in (_DB_ONLY_MARKER, _DISK_ONLY_MARKER):
        f = path / m
        if f.exists():
            try:
                f.unlink()
                cleared += 1
            except Exception:
                logger.debug("storage_tier: unlink %s failed", f, exc_info=True)
    return cleared


def mark_db_only(slug: str, subdir: str) -> Dict[str, Any]:
    """Mark a subdir as db_only — content stays in DB; disk copies evictable."""
    try:
        path = _subdir_path(slug, subdir)
        _clear_markers(path)
        _write_marker(path, _DB_ONLY_MARKER)
        return {"ok": True, "tier": TIER_DB_ONLY, "path": str(path)}
    except Exception as e:
        logger.exception("storage_tier: mark_db_only failed")
        return {"ok": False, "error": str(e)}


def mark_disk_only(slug: str, subdir: str) -> Dict[str, Any]:
    """Mark a subdir as disk_only — content stays on disk; not loaded to DB on reindex."""
    try:
        path = _subdir_path(slug, subdir)
        _clear_markers(path)
        _write_marker(path, _DISK_ONLY_MARKER)
        return {"ok": True, "tier": TIER_DISK_ONLY, "path": str(path)}
    except Exception as e:
        logger.exception("storage_tier: mark_disk_only failed")
        return {"ok": False, "error": str(e)}


def clear_tier(slug: str, subdir: str) -> Dict[str, Any]:
    """Remove tier markers — revert to default db_tracked."""
    try:
        path = _subdir_path(slug, subdir)
        if not path.exists():
            return {"ok": True, "tier": TIER_DB_TRACKED, "cleared": 0}
        n = _clear_markers(path)
        return {"ok": True, "tier": TIER_DB_TRACKED, "cleared": n}
    except Exception as e:
        logger.exception("storage_tier: clear_tier failed")
        return {"ok": False, "error": str(e)}


def get_tier(slug: str, subdir: str) -> str:
    """Return current tier for a subdir. Defaults to db_tracked."""
    try:
        path = _subdir_path(slug, subdir)
        if (path / _DB_ONLY_MARKER).exists():
            return TIER_DB_ONLY
        if (path / _DISK_ONLY_MARKER).exists():
            return TIER_DISK_ONLY
        return TIER_DB_TRACKED
    except Exception:
        logger.exception("storage_tier: get_tier failed")
        return TIER_DB_TRACKED


def list_tiers(slug: str) -> Dict[str, str]:
    """Map every existing subdir under knowledge/{slug}/ to its tier."""
    out: Dict[str, str] = {}
    try:
        root = KNOWLEDGE_DIR / _safe_slug(slug)
        if not root.exists():
            return out
        for p in root.iterdir():
            if not p.is_dir():
                continue
            name = p.name
            if name.startswith("."):
                continue
            try:
                out[name] = get_tier(slug, name)
            except Exception:
                continue
    except Exception:
        logger.exception("storage_tier: list_tiers failed")
    return out


# ── Eviction / restore ─────────────────────────────────────────────────────
def _count_db_rows(slug: str, namespace: str) -> int:
    """How many rows in dash.dash_vectors for this project + namespace?"""
    try:
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM dash.dash_vectors
                     WHERE project_slug = :p AND namespace = :n
                    """
                ),
                {"p": slug, "n": namespace},
            ).first()
            return int(row[0]) if row else 0
    except Exception:
        logger.debug("storage_tier: _count_db_rows failed", exc_info=True)
        return 0


def evict_db_only_files(
    slug: str, subdir: str, keep_index: bool = True
) -> Dict[str, Any]:
    """Remove disk copies under knowledge/{slug}/{subdir}/ (db_only tier required).

    If `keep_index=True` (default), verify pgvector still has rows for this
    namespace before deleting disk copies. The marker file is preserved.
    """
    result: Dict[str, Any] = {
        "evicted": 0,
        "skipped": 0,
        "errors": 0,
        "freed_bytes": 0,
    }
    try:
        path = _subdir_path(slug, subdir)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    tier = get_tier(slug, subdir)
    if tier != TIER_DB_ONLY:
        return {
            "ok": False,
            "error": f"tier is {tier!r}; must mark_db_only first",
        }

    if not path.exists():
        return {"ok": True, **result, "reason": "no disk content"}

    if keep_index:
        db_rows = _count_db_rows(slug, subdir)
        if db_rows == 0:
            return {
                "ok": False,
                "error": (
                    f"refusing to evict — pgvector has 0 rows for namespace={subdir!r}. "
                    "Reindex first or pass keep_index=False."
                ),
            }
        result["db_rows"] = db_rows

    # Walk and delete files, preserve marker
    keep = {_DB_ONLY_MARKER}
    for f in sorted(path.rglob("*")):
        try:
            if f.is_file() and f.name not in keep:
                size = f.stat().st_size
                f.unlink()
                result["evicted"] += 1
                result["freed_bytes"] += size
            elif f.is_dir():
                result["skipped"] += 1
        except Exception:
            logger.debug("storage_tier: unlink %s failed", f, exc_info=True)
            result["errors"] += 1

    # Remove empty subdirs (bottom-up); leave root + marker
    for d in sorted(path.rglob("*"), reverse=True):
        try:
            if d.is_dir() and d != path:
                try:
                    d.rmdir()
                except OSError:
                    pass
        except Exception:
            pass

    return {"ok": True, **result, "path": str(path)}


def restore_from_db(slug: str, subdir: str) -> Dict[str, Any]:
    """Re-materialize files from pgvector rows for this project + namespace.

    Files are written as `{source_id}.txt` containing the raw text. Metadata
    (scope_attrs) is sidecar `{source_id}.json` when present. This is a
    best-effort restore — original file layout/extensions are not preserved
    because vectors store flattened text per chunk.
    """
    result: Dict[str, Any] = {"restored": 0, "errors": 0}
    try:
        path = _subdir_path(slug, subdir)
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        eng = _engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT source_id, text, scope_attrs, metadata, updated_at
                      FROM dash.dash_vectors
                     WHERE project_slug = :p AND namespace = :n
                     ORDER BY updated_at DESC
                    """
                ),
                {"p": slug, "n": subdir},
            ).mappings().all()
    except Exception as e:
        logger.exception("storage_tier: restore fetch failed")
        return {"ok": False, "error": str(e)}

    import json
    for r in rows:
        try:
            sid = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(r.get("source_id") or ""))[:120]
            if not sid:
                continue
            txt_path = path / f"{sid}.txt"
            txt_path.write_text(str(r.get("text") or ""), encoding="utf-8")
            meta = {
                "source_id": r.get("source_id"),
                "scope_attrs": r.get("scope_attrs"),
                "metadata": r.get("metadata"),
            }
            (path / f"{sid}.json").write_text(
                json.dumps(meta, default=str, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result["restored"] += 1
        except Exception:
            logger.debug("storage_tier: per-row restore failed", exc_info=True)
            result["errors"] += 1

    return {"ok": True, **result, "path": str(path), "db_rows": len(rows)}


__all__ = [
    "TIER_DB_TRACKED",
    "TIER_DB_ONLY",
    "TIER_DISK_ONLY",
    "mark_db_only",
    "mark_disk_only",
    "clear_tier",
    "get_tier",
    "list_tiers",
    "evict_db_only_files",
    "restore_from_db",
]
