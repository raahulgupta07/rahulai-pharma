"""Brain seed importer — load JSON seed files into dash_company_brain.

Seed JSON files live in knowledge/seeds/*.json. Each is a JSON array of
{name, category, value, scope, source, confidence}. The actual table schema
uses `definition` (not `value`) as the text column, so this importer maps
`value` -> `definition` and stows `source` + `confidence` + original `scope`
inside the JSONB `metadata` column. The `category` field on the seed entry
is normalised to one of brain.VALID_CATEGORIES; unknown categories
(e.g. "metric") are coerced to "glossary" but the original kind is kept in
metadata.kind so it round-trips.

NOTE: The existing dash_company_brain table has NO unique constraint on
(project_slug, name). To support clean ON CONFLICT semantics this module
attempts to create two partial unique indexes the first time it runs (one
for project rows, one for global rows). The CREATE INDEX statements are
wrapped in try/except so re-runs and DBs without permission just fall
through to a SELECT-then-INSERT path.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brain", tags=["brain-seeds"])

SEEDS_DIR = Path("knowledge/seeds")

# Categories the existing brain accepts; anything else is bucketed into
# "glossary" with the original tag preserved in metadata.kind.
_VALID_CATEGORIES = {
    "glossary", "formula", "alias", "pattern",
    "org", "threshold", "calendar",
}

_INDEX_BOOTSTRAPPED = False


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_admin(user: dict):
    """Allow super-admin through. Mirrors brain._require_super_admin but
    is tolerant of either flag style (is_super_admin bool or username match)."""
    if user.get("is_super_admin"):
        return
    try:
        from app.auth import SUPER_ADMIN
        if user.get("username") == SUPER_ADMIN:
            return
    except Exception:
        pass
    raise HTTPException(403, "Admin access required")


def _bootstrap_unique_indexes(conn) -> None:
    """DEPRECATED — see db/migrations/006_brain_unique_index.sql.

    Kept as no-op fallback for older deployments where the migration
    hasn't been applied yet. The CREATE UNIQUE INDEX IF NOT EXISTS
    statements are idempotent so this is safe to keep.

    Original behavior: One-time CREATE UNIQUE INDEX for ON CONFLICT
    support. Wrapped in try/except so it never blocks the import — if
    the index can't be created we fall back to SELECT-then-INSERT."""
    global _INDEX_BOOTSTRAPPED
    if _INDEX_BOOTSTRAPPED:
        return
    try:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_brain_slug_name "
            "ON public.dash_company_brain(project_slug, name) "
            "WHERE project_slug IS NOT NULL"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_brain_global_name "
            "ON public.dash_company_brain(name) "
            "WHERE project_slug IS NULL"
        ))
        conn.commit()
        _INDEX_BOOTSTRAPPED = True
    except Exception as e:
        logger.warning(f"brain_seeds: unique index bootstrap skipped: {e}")


def _normalize_category(raw: str | None) -> tuple[str, str]:
    """Return (db_category, original_kind). Unknown -> glossary."""
    cat = (raw or "").strip().lower()
    if cat in _VALID_CATEGORIES:
        return cat, cat
    return "glossary", cat or "unknown"


@router.get("/seeds")
def list_seeds(request: Request):
    """List available seed JSON files in knowledge/seeds/."""
    _ = _get_user(request)
    if not SEEDS_DIR.exists():
        return {"seeds": []}
    out = []
    for p in sorted(SEEDS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            count = len(data) if isinstance(data, list) else 0
        except Exception:
            count = 0
        out.append({
            "filename": p.name,
            "entry_count": count,
            "size_bytes": p.stat().st_size,
        })
    return {"seeds": out}


@router.get("/seeds-preview")
def seeds_preview(request: Request, file: str = ""):
    """Preview entries from a single seed file (first 20 entries)."""
    _ = _get_user(request)
    if not file or not file.endswith(".json"):
        raise HTTPException(400, "file param required")
    p = SEEDS_DIR / file
    if not p.exists():
        raise HTTPException(404, f"{file} not found")
    try:
        data = json.loads(p.read_text())
        return {
            "filename": file,
            "entries": data[:20] if isinstance(data, list) else [],
        }
    except Exception as e:
        raise HTTPException(500, f"parse: {e}")


@router.post("/import-seeds")
def import_seeds(request: Request, body: dict):
    """Bulk-import seed JSON files into dash_company_brain.

    Body keys:
      files (list[str], optional): subset of filenames; empty/missing = ALL.
      project_slug (str, optional): if absent, scope defaults to 'global'.
      scope (str, optional): 'project' | 'global'. Default 'project' when
          a slug is given, else 'global'.
      overwrite (bool, optional): if true, existing rows on the same
          (project_slug, name) are UPDATED. Otherwise duplicates are skipped.
    """
    user = _get_user(request)
    _require_admin(user)

    files = body.get("files") or []
    project_slug = body.get("project_slug")
    scope = body.get("scope") or ("project" if project_slug else "global")
    overwrite = bool(body.get("overwrite", False))

    if not isinstance(files, list):
        raise HTTPException(400, "files must be a list")

    if not files:
        files = sorted(p.name for p in SEEDS_DIR.glob("*.json"))

    from db.session import get_sql_engine
    engine = get_sql_engine()

    imported = 0
    file_results: list[dict] = []
    errors: list[str] = []

    with engine.connect() as conn:
        _bootstrap_unique_indexes(conn)

        for fname in files:
            p = SEEDS_DIR / fname
            if not p.exists():
                errors.append(f"missing: {fname}")
                continue
            try:
                entries = json.loads(p.read_text())
                if not isinstance(entries, list):
                    errors.append(f"{fname}: not a JSON array")
                    continue
            except Exception as e:
                errors.append(f"{fname}: parse error {e}")
                continue

            file_count = 0
            for entry in entries:
                name = (entry.get("name") or "").strip()
                value = entry.get("value") or entry.get("definition") or ""
                if not name or not value:
                    errors.append(f"{fname}: skipped entry missing name/value")
                    continue
                db_cat, original_kind = _normalize_category(entry.get("category"))
                meta = {
                    "kind": original_kind,
                    "source": entry.get("source") or fname,
                    "confidence": entry.get("confidence"),
                    "seed_scope": entry.get("scope") or scope,
                    "seed_file": fname,
                }
                params = {
                    "slug": project_slug,
                    "name": name,
                    "cat": db_cat,
                    "definition": value,
                    "meta": json.dumps(meta),
                    "by": user.get("username") or "seed-importer",
                }
                try:
                    # Try ON CONFLICT path first (requires unique index).
                    if overwrite:
                        conn.execute(text(
                            "INSERT INTO public.dash_company_brain "
                            "(project_slug, name, category, definition, metadata, created_by) "
                            "VALUES (:slug, :name, :cat, :definition, CAST(:meta AS jsonb), :by) "
                            "ON CONFLICT (project_slug, name) DO UPDATE SET "
                            "  definition = EXCLUDED.definition, "
                            "  category = EXCLUDED.category, "
                            "  metadata = EXCLUDED.metadata, "
                            "  updated_at = NOW()"
                        ), params)
                    else:
                        conn.execute(text(
                            "INSERT INTO public.dash_company_brain "
                            "(project_slug, name, category, definition, metadata, created_by) "
                            "VALUES (:slug, :name, :cat, :definition, CAST(:meta AS jsonb), :by) "
                            "ON CONFLICT (project_slug, name) DO NOTHING"
                        ), params)
                    file_count += 1
                except Exception as on_conflict_err:
                    # Fallback: no unique index present. Use SELECT-then-INSERT/UPDATE.
                    try:
                        existing = conn.execute(text(
                            "SELECT id FROM public.dash_company_brain "
                            "WHERE name = :name AND "
                            "      ((project_slug IS NULL AND :slug IS NULL) "
                            "       OR project_slug = :slug) "
                            "LIMIT 1"
                        ), {"name": name, "slug": project_slug}).fetchone()
                        if existing:
                            if overwrite:
                                conn.execute(text(
                                    "UPDATE public.dash_company_brain SET "
                                    "  definition = :definition, "
                                    "  category = :cat, "
                                    "  metadata = CAST(:meta AS jsonb), "
                                    "  updated_at = NOW() "
                                    "WHERE id = :id"
                                ), {**params, "id": existing[0]})
                                file_count += 1
                            # else: skip
                        else:
                            conn.execute(text(
                                "INSERT INTO public.dash_company_brain "
                                "(project_slug, name, category, definition, metadata, created_by) "
                                "VALUES (:slug, :name, :cat, :definition, CAST(:meta AS jsonb), :by)"
                            ), params)
                            file_count += 1
                    except Exception as fallback_err:
                        errors.append(
                            f"{fname}/{name}: {str(fallback_err)[:120]} "
                            f"(initial: {str(on_conflict_err)[:80]})"
                        )

            imported += file_count
            file_results.append({"file": fname, "imported": file_count})

        conn.commit()

    return {
        "imported": imported,
        "files": file_results,
        "errors": errors[:50],
    }
