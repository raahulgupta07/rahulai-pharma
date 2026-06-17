"""
dash.ingest.colmap
==================

Phase-2 of the self-adapting (dynamic) schema: the **logical -> physical column
registry + resolver**.

CityPharma's tools, training prompts and pre-join builders refer to columns by a
stable *logical* name (e.g. ``brand``, ``cost``) even though the *physical* column
in the live uploaded table may be named differently (``brand_name``,
``weighted_cost_price``). This module is the single chokepoint that maps one to
the other, backed by the ``public.dash_column_map`` registry (migration 194) and
falling back to a canonical default map.

The registry table shape (migration 194)::

    project_slug, table_logical, logical_name, physical_col,
    confidence, source, status, created_at, updated_at
    UNIQUE(project_slug, table_logical, logical_name)

Two logical tables are modelled today:

* ``catalog`` — the article master (one row per SKU).
* ``stock``   — the per-site balance / inventory table.

The physical defaults below mirror exactly what ``scripts/build_shop_flat.py``
selects today (catalog: article_code/brand_name/generic_name/composition/category;
stock: article_code/site_code/stock_qty/weighted_cost_price).

EVERYTHING here is FAIL-SOFT: any DB error is logged at WARNING and swallowed, and
the resolver falls back to the default map and finally to the logical name itself,
so a registry hiccup can never break ingest or chat.

Public API
----------
DEFAULT_MAP   : canonical seed truth (logical_table -> {logical_name: physical_col})
resolve       : one logical column -> physical column (DB -> default -> identity)
resolve_many  : {logical: physical} for a list of logical names
set_map       : UPSERT one mapping row (never raises, returns bool)
list_maps     : active+pending rows for a project (optionally one logical table)
seed_defaults : idempotently insert every DEFAULT_MAP entry (returns count attempted)
"""

from __future__ import annotations

import difflib
import logging

from sqlalchemy import text

# get_sql_engine = guarded (reads public, writes dash). Writes to public.dash_column_map
# need the unguarded read-write engine (it blocks public-schema writes by design).
from db.session import get_sql_engine, get_write_engine  # CACHED SHARED — NEVER .dispose()

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Canonical seed truth.
#
# These physical names match scripts/build_shop_flat.py exactly. ``indication``
# is included for the catalog because the resolver/tools reference it even though
# build_shop_flat does not currently SELECT it (the registry is the superset).
# --------------------------------------------------------------------------- #
DEFAULT_MAP: dict[str, dict[str, str]] = {
    "catalog": {
        "article_code": "article_code",
        "brand": "brand_name",
        "generic": "generic_name",
        "composition": "composition",
        "category": "category",
        "indication": "indication",
    },
    "stock": {
        "article_code": "article_code",
        "site_code": "site_code",
        "stock_qty": "stock_qty",
        "cost": "weighted_cost_price",
    },
}


def resolve(project_slug: str, table_logical: str, logical_name: str) -> str:
    """Resolve one *logical* column to its *physical* column name.

    Fallback order:
        1. an ``active`` row in ``public.dash_column_map`` for
           (project_slug, table_logical, logical_name)
        2. ``DEFAULT_MAP[table_logical][logical_name]``
        3. ``logical_name`` itself (identity)

    Never raises — a DB failure falls through to the default / identity.
    """
    try:
        engine = get_sql_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT physical_col FROM public.dash_column_map "
                    "WHERE project_slug = :p AND table_logical = :t "
                    "AND logical_name = :l AND status = 'active' "
                    "LIMIT 1"
                ),
                {"p": project_slug, "t": table_logical, "l": logical_name},
            ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception as exc:  # fail-soft — fall through to the default map
        logger.warning(
            "colmap.resolve DB lookup failed for %s.%s.%s: %s",
            project_slug,
            table_logical,
            logical_name,
            exc,
        )

    return DEFAULT_MAP.get(table_logical, {}).get(logical_name, logical_name)


def resolve_many(
    project_slug: str, table_logical: str, logical_names: list[str]
) -> dict[str, str]:
    """Resolve a list of logical names to ``{logical: physical}`` (per-name fail-soft)."""
    return {
        name: resolve(project_slug, table_logical, name) for name in logical_names
    }


def set_map(
    project_slug: str,
    table_logical: str,
    logical_name: str,
    physical_col: str,
    confidence: float = 1.0,
    source: str = "manual",
    status: str = "active",
) -> bool:
    """UPSERT one mapping row. Returns ``True`` on success, ``False`` on failure.

    On conflict of (project_slug, table_logical, logical_name) updates
    physical_col / confidence / source / status and bumps ``updated_at``.
    Never raises.
    """
    try:
        engine = get_write_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.dash_column_map "
                    "(project_slug, table_logical, logical_name, physical_col, "
                    " confidence, source, status) "
                    "VALUES (:p, :t, :l, :phys, :conf, :src, :st) "
                    "ON CONFLICT (project_slug, table_logical, logical_name) "
                    "DO UPDATE SET physical_col = EXCLUDED.physical_col, "
                    " confidence = EXCLUDED.confidence, "
                    " source = EXCLUDED.source, "
                    " status = EXCLUDED.status, "
                    " updated_at = now()"
                ),
                {
                    "p": project_slug,
                    "t": table_logical,
                    "l": logical_name,
                    "phys": physical_col,
                    "conf": confidence,
                    "src": source,
                    "st": status,
                },
            )
        return True
    except Exception as exc:  # fail-soft — never block the caller
        logger.warning(
            "colmap.set_map UPSERT failed for %s.%s.%s -> %s: %s",
            project_slug,
            table_logical,
            logical_name,
            physical_col,
            exc,
        )
        return False


def list_maps(
    project_slug: str, table_logical: str | None = None
) -> list[dict]:
    """Return active+pending mapping rows for a project (as dicts).

    Optionally filtered to one ``table_logical``. Never raises (empty list on error).
    """
    try:
        engine = get_sql_engine()
        params: dict = {"p": project_slug}
        sql = (
            "SELECT project_slug, table_logical, logical_name, physical_col, "
            " confidence, source, status, created_at, updated_at "
            "FROM public.dash_column_map "
            "WHERE project_slug = :p AND status IN ('active', 'pending')"
        )
        if table_logical is not None:
            sql += " AND table_logical = :t"
            params["t"] = table_logical
        sql += " ORDER BY table_logical, logical_name"
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:  # fail-soft
        logger.warning(
            "colmap.list_maps failed for %s (%s): %s",
            project_slug,
            table_logical,
            exc,
        )
        return []


def seed_defaults(project_slug: str) -> int:
    """Idempotently seed every ``DEFAULT_MAP`` entry for a project.

    Inserts with ``source='seed'``, ``status='active'``, ``confidence=1.0`` using
    ``ON CONFLICT … DO NOTHING`` so existing rows are left untouched. Returns the
    number of rows *attempted* (= total entries in DEFAULT_MAP). Never raises.
    """
    attempted = 0
    try:
        engine = get_write_engine()
        with engine.begin() as conn:
            for table_logical, cols in DEFAULT_MAP.items():
                for logical_name, physical_col in cols.items():
                    attempted += 1
                    conn.execute(
                        text(
                            "INSERT INTO public.dash_column_map "
                            "(project_slug, table_logical, logical_name, "
                            " physical_col, confidence, source, status) "
                            "VALUES (:p, :t, :l, :phys, 1.0, 'seed', 'active') "
                            "ON CONFLICT (project_slug, table_logical, logical_name) "
                            "DO NOTHING"
                        ),
                        {
                            "p": project_slug,
                            "t": table_logical,
                            "l": logical_name,
                            "phys": physical_col,
                        },
                    )
    except Exception as exc:  # fail-soft
        logger.warning("colmap.seed_defaults failed for %s: %s", project_slug, exc)
    return attempted


# --------------------------------------------------------------------------- #
# Phase-4: rename matching + review gate.
#
# When a contract drift renames a column, we try to keep the logical mapping
# pointing at the new physical column. Obvious renames (high name similarity)
# are auto-applied (status='active'); ambiguous ones are parked as 'pending'
# for a human to confirm/reject in the review UI. Until a pending remap is
# confirmed, resolve() keeps using the last active/default mapping, so
# build_shop_flat falls back to its no-empty guard rather than breaking.
# --------------------------------------------------------------------------- #
_AUTO_THRESHOLD = 0.85  # name-similarity >= this -> auto-map; below -> pending review


def _logical_for_physical(
    project_slug: str, table_logical: str, physical_col: str
) -> str | None:
    """Reverse lookup: which logical name currently points at *physical_col*?

    Checks active registry rows first, then DEFAULT_MAP. Returns None if the
    physical column is not a tracked logical (nothing to remap). Fail-soft.
    """
    try:
        for row in list_maps(project_slug, table_logical):
            if row.get("physical_col") == physical_col and row.get("status") == "active":
                return row.get("logical_name")
    except Exception:
        pass
    for lname, pcol in DEFAULT_MAP.get(table_logical, {}).items():
        if pcol == physical_col:
            return lname
    return None


def propose_remaps(project_slug: str, table_logical: str, diff: dict | None) -> list[dict]:
    """Given a contract drift *diff*, propose logical->physical remaps for renamed
    columns of *table_logical*. High name-similarity is auto-applied (active);
    ambiguous is parked 'pending'. Returns a list of proposal dicts. Fail-soft.

    Only columns whose OLD physical name maps to a tracked logical produce a
    proposal — unrelated added/removed columns are ignored here.
    """
    proposals: list[dict] = []
    try:
        diff = diff or {}
        renamed = diff.get("renamed") or []
        removed = list(diff.get("removed") or [])
        added = list(diff.get("added") or [])

        pairs: list[tuple[str, str]] = [
            (r.get("from"), r.get("to"))
            for r in renamed
            if r.get("from") and r.get("to")
        ]
        covered_from = {p[0] for p in pairs}
        covered_to = {p[1] for p in pairs}
        # also pair leftover removed/added by name similarity (>= 0.5 to consider)
        for rem in removed:
            if rem in covered_from:
                continue
            best, best_score = None, 0.0
            for add in added:
                if add in covered_to:
                    continue
                score = difflib.SequenceMatcher(None, str(rem), str(add)).ratio()
                if score > best_score:
                    best, best_score = add, score
            if best is not None and best_score >= 0.5:
                pairs.append((rem, best))
                covered_to.add(best)

        for old_phys, new_phys in pairs:
            logical = _logical_for_physical(project_slug, table_logical, old_phys)
            if not logical:
                continue
            score = difflib.SequenceMatcher(None, str(old_phys), str(new_phys)).ratio()
            status = "active" if score >= _AUTO_THRESHOLD else "pending"
            set_map(
                project_slug, table_logical, logical, new_phys,
                confidence=round(score, 3), source="auto", status=status,
            )
            proposals.append({
                "table_logical": table_logical, "logical_name": logical,
                "from": old_phys, "to": new_phys,
                "confidence": round(score, 3), "status": status,
            })
    except Exception as exc:  # fail-soft
        logger.warning(
            "colmap.propose_remaps failed for %s.%s: %s", project_slug, table_logical, exc
        )
    return proposals


def decide_map(
    project_slug: str,
    table_logical: str,
    logical_name: str,
    decision: str,
    physical_col: str | None = None,
) -> bool:
    """Apply a review decision to a mapping row. Never raises.

    decision:
        'confirm' -> set status='active', source='confirmed'
        'reject'  -> set status='rejected' (resolve falls back to default)
        'map'     -> set physical_col=*physical_col*, status='active', source='confirmed'
    """
    try:
        engine = get_write_engine()
        if decision == "map" and physical_col:
            return set_map(
                project_slug, table_logical, logical_name, physical_col,
                confidence=1.0, source="confirmed", status="active",
            )
        new_status = {"confirm": "active", "reject": "rejected"}.get(decision)
        if not new_status:
            return False
        new_source = "confirmed" if new_status == "active" else "auto"
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE public.dash_column_map "
                    "SET status = :st, source = :src, updated_at = now() "
                    "WHERE project_slug = :p AND table_logical = :t "
                    "AND logical_name = :l"
                ),
                {"st": new_status, "src": new_source, "p": project_slug,
                 "t": table_logical, "l": logical_name},
            )
        return True
    except Exception as exc:  # fail-soft
        logger.warning("colmap.decide_map failed: %s", exc)
        return False
