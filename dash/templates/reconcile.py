"""Stage 2 — reconcile template expectations against real project schema.

Algorithm per expected entity/column:
  1. Exact name match
  2. Alias match (template-defined alias list)
  3. Column-overlap heuristic for entities (table containing most expected cols)
  4. Fuzzy / token match
  5. Otherwise: unbound

For each KPI/workflow:
  - All refs bound → activate, substitute placeholders, write resolved_query
  - Partial bound → needs_review
  - None bound → stay pending

Read-only schema introspection. Never executes template SQL during reconcile.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from sqlalchemy import text

from .schema import AgentTemplate, ExpectedEntity, ExpectedColumn
from . import storage

# Industry preset registry removed; reconcile against stored expectations only.
def get_template(name: str):  # noqa: D401 - shim
    return None

logger = logging.getLogger(__name__)


# ─────────────────────────── Schema Introspection ───────────────────────────


def _project_schema_name(project_slug: str) -> str:
    """Look up actual schema_name from dash_projects, fallback to slug."""
    from db.session import get_sql_engine
    eng = get_sql_engine()
    if eng is None:
        return project_slug
    try:
        with eng.begin() as cn:
            row = cn.execute(
                text("SELECT schema_name FROM dash_projects WHERE slug = :s LIMIT 1"),
                {"s": project_slug},
            ).fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return project_slug


def read_project_schema(project_slug: str) -> dict[str, list[dict]]:
    """Read project tables + columns from information_schema.

    Returns: {table_name: [{name, dtype, nullable}, ...]}
    """
    from db.session import get_sql_engine
    eng = get_sql_engine()
    if eng is None:
        return {}
    schema = _project_schema_name(project_slug)
    out: dict[str, list[dict]] = {}
    try:
        with eng.begin() as cn:
            rows = cn.execute(
                text(
                    """
                    SELECT table_name, column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = :sch
                    ORDER BY table_name, ordinal_position
                    """
                ),
                {"sch": schema},
            ).fetchall()
        for r in rows:
            tbl = r[0]
            out.setdefault(tbl, []).append(
                {"name": r[1], "dtype": r[2], "nullable": r[3] == "YES"}
            )
    except Exception as e:
        logger.warning("schema read failed for %s: %s", project_slug, e)
    return out


# ─────────────────────────── Matching Helpers ───────────────────────────


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _match_table(entity: ExpectedEntity, schema: dict[str, list[dict]]) -> tuple[str | None, str, float]:
    """Find best real table for an expected entity.

    Returns: (real_table_name | None, match_method, confidence)
    """
    if not schema:
        return None, "no_schema", 0.0

    entity_keys = [_normalize(entity.name)] + [_normalize(a) for a in entity.aliases]
    entity_keys = [k for k in entity_keys if k]

    # 1. exact / alias name match
    for tbl in schema:
        if _normalize(tbl) in entity_keys:
            return tbl, "alias_name", 0.95

    # 2. column-overlap heuristic
    expected_cols = {_normalize(c.name) for c in entity.columns}
    expected_cols |= {_normalize(a) for c in entity.columns for a in c.aliases}
    expected_cols.discard("")
    if not expected_cols:
        return None, "unmatched", 0.0

    best_tbl: str | None = None
    best_overlap = 0
    best_ratio = 0.0
    for tbl, cols in schema.items():
        real_col_keys = {_normalize(c["name"]) for c in cols}
        overlap = len(expected_cols & real_col_keys)
        ratio = overlap / max(len(expected_cols), 1)
        if overlap > best_overlap or (overlap == best_overlap and ratio > best_ratio):
            best_tbl = tbl
            best_overlap = overlap
            best_ratio = ratio
    if best_overlap >= 2:
        return best_tbl, "column_overlap", min(0.5 + best_ratio * 0.4, 0.92)
    return None, "unmatched", 0.0


def _match_column(
    expected: ExpectedColumn,
    real_cols: list[dict],
) -> tuple[str | None, str, float]:
    """Find best real column. Returns (col_name | None, method, confidence)."""
    if not real_cols:
        return None, "no_columns", 0.0
    real_keys = {_normalize(c["name"]): c["name"] for c in real_cols}

    # 1. exact match
    if _normalize(expected.name) in real_keys:
        return real_keys[_normalize(expected.name)], "exact", 1.0

    # 2. alias match
    for alias in expected.aliases:
        if _normalize(alias) in real_keys:
            return real_keys[_normalize(alias)], "alias", 0.9

    # 3. token containment (qty in qty_on_hand)
    en = _normalize(expected.name)
    if len(en) >= 3:
        for k, original in real_keys.items():
            if en in k or k in en:
                return original, "token", 0.7

    # 4. alias token containment
    for alias in expected.aliases:
        an = _normalize(alias)
        if len(an) >= 3:
            for k, original in real_keys.items():
                if an in k or k in an:
                    return original, "alias_token", 0.65

    return None, "unmatched", 0.0


# ─────────────────────────── Workflow / KPI Resolution ───────────────────────────


_PLACEHOLDER_DOT_RE = re.compile(r"\{([a-zA-Z_]+)\.([a-zA-Z_]+)\}")
_PLACEHOLDER_BARE_RE = re.compile(r"\{([a-zA-Z_]+)\}")


def _resolve_query(query_template: str, refs_map: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute {entity.column} and {entity} placeholders.

    Returns (resolved_sql, missing_refs). Each {entity.column} → real_table.real_col,
    each {entity} → real_table. Missing placeholders preserved.
    """
    missing: list[str] = []

    def _sub_dot(m: re.Match) -> str:
        ent, col = m.group(1), m.group(2)
        ref = f"{ent}.{col}"
        real = refs_map.get(ref)
        if real:
            return real
        missing.append(ref)
        return m.group(0)

    def _sub_bare(m: re.Match) -> str:
        ent = m.group(1)
        real = refs_map.get(ent)
        if real:
            return real
        missing.append(ent)
        return m.group(0)

    resolved = _PLACEHOLDER_DOT_RE.sub(_sub_dot, query_template)
    resolved = _PLACEHOLDER_BARE_RE.sub(_sub_bare, resolved)
    return resolved, missing


def _resolve_workflows(project_slug: str, refs_map: dict[str, str]) -> dict:
    """Update each workflow's status + resolved_query based on bindings."""
    from db.session import get_sql_engine
    eng = get_sql_engine()
    if eng is None:
        return {"updated": 0, "active": 0, "needs_review": 0, "pending": 0}

    active = needs_review = pending_cnt = updated = 0
    with eng.begin() as cn:
        rows = cn.execute(
            text(
                """
                SELECT id, query_template, expected_entity, expected_columns
                FROM dash_autonomous_workflows
                WHERE project_slug = :s
                """
            ),
            {"s": project_slug},
        ).fetchall()
        for wf_id, qtmpl, _entity, cols in rows:
            cols_list = cols if isinstance(cols, list) else (cols or [])
            resolved_sql, missing = _resolve_query(qtmpl or "", refs_map)
            if not (qtmpl or "").strip():
                # no query — pending always
                new_status = "pending"
                pending_cnt += 1
            elif not missing:
                new_status = "active"
                active += 1
            elif len(missing) < (len(cols_list) or 1):
                new_status = "needs_review"
                needs_review += 1
            else:
                new_status = "pending"
                pending_cnt += 1
            cn.execute(
                text(
                    """
                    UPDATE dash_autonomous_workflows
                       SET resolved_query = :rs, status = :st
                     WHERE id = :id
                    """
                ),
                {"rs": resolved_sql if not missing else None, "st": new_status, "id": wf_id},
            )
            updated += 1
    return {"updated": updated, "active": active, "needs_review": needs_review, "pending": pending_cnt}


# ─────────────────────────── Main Reconciler ───────────────────────────


def reconcile(project_slug: str) -> dict:
    """Run full reconciliation. Idempotent — safe to re-run anytime.

    Returns summary:
      template_name, entities_matched, entities_total,
      bindings_bound, bindings_unbound, bindings_total,
      workflows: {active, needs_review, pending, updated}
    """
    storage.bootstrap_tables()
    exp = storage.load_expectations(project_slug)
    if not exp:
        return {"reconciled": False, "reason": "no template applied"}

    template_name = exp["template_name"]
    tmpl = get_template(template_name)
    if not tmpl:
        return {"reconciled": False, "reason": f"template '{template_name}' not in registry"}

    schema = read_project_schema(project_slug)
    if not schema:
        return {
            "reconciled": False,
            "reason": "no project schema (upload + train data first)",
            "template_name": template_name,
        }

    summary: dict[str, Any] = {
        "reconciled": True,
        "template_name": template_name,
        "tables_in_schema": len(schema),
        "entities_total": len(tmpl.entities),
        "entities_matched": 0,
        "entities_unmatched": [],
        "bindings_total": 0,
        "bindings_bound": 0,
        "bindings_unbound": 0,
        "errors": [],
    }

    refs_map: dict[str, str] = {}  # template_ref → real_ref

    # Match each entity → table
    for entity in tmpl.entities:
        real_tbl, method, conf = _match_table(entity, schema)
        if not real_tbl:
            summary["entities_unmatched"].append(entity.name)
            # mark whole-entity placeholder unbound
            storage.upsert_binding(
                project_slug,
                entity.name,
                None,
                status="unbound",
                match_method="entity_unmatched",
                confidence=0.0,
            )
            # mark each expected column as unbound too
            for col in entity.columns:
                storage.upsert_binding(
                    project_slug,
                    f"{entity.name}.{col.name}",
                    None,
                    status="unbound",
                    match_method="entity_unmatched",
                )
                summary["bindings_total"] += 1
                summary["bindings_unbound"] += 1
            continue

        summary["entities_matched"] += 1
        refs_map[entity.name] = real_tbl
        storage.upsert_binding(
            project_slug,
            entity.name,
            real_tbl,
            status="bound",
            match_method=method,
            confidence=conf,
        )

        # Match each column inside that table
        real_cols = schema[real_tbl]
        for col in entity.columns:
            col_name, col_method, col_conf = _match_column(col, real_cols)
            tref = f"{entity.name}.{col.name}"
            summary["bindings_total"] += 1
            if col_name:
                refs_map[tref] = f"{real_tbl}.{col_name}"
                status = "bound" if col_conf >= 0.85 else "needs_review"
                storage.upsert_binding(
                    project_slug, tref, f"{real_tbl}.{col_name}",
                    status=status, match_method=col_method, confidence=col_conf,
                )
                summary["bindings_bound"] += 1 if status == "bound" else 0
                if status == "needs_review":
                    summary.setdefault("bindings_needs_review", 0)
                    summary["bindings_needs_review"] += 1
            else:
                storage.upsert_binding(
                    project_slug, tref, None,
                    status="unbound", match_method="col_unmatched",
                )
                summary["bindings_unbound"] += 1

    # Resolve workflows
    summary["workflows"] = _resolve_workflows(project_slug, refs_map)
    return summary


# ─────────────────────────── Manual Override ───────────────────────────


def set_manual_binding(project_slug: str, template_ref: str, real_ref: str | None) -> None:
    """User-driven manual remap. real_ref = None marks not-applicable."""
    if real_ref is None or real_ref == "":
        storage.upsert_binding(project_slug, template_ref, None, status="unbound", match_method="manual_skip")
    else:
        storage.upsert_binding(project_slug, template_ref, real_ref, status="bound", match_method="manual", confidence=1.0)
    # Re-resolve workflows after manual change
    bindings = storage.list_bindings(project_slug)
    refs_map = {b["template_ref"]: b["real_ref"] for b in bindings if b["real_ref"]}
    _resolve_workflows(project_slug, refs_map)
