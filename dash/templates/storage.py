"""Storage layer for agent templates — bootstrap tables, write expectations, read bindings."""
from __future__ import annotations

import json
import logging
from typing import Any
from sqlalchemy import text

logger = logging.getLogger(__name__)

_BOOTSTRAPPED = False


def _get_engine():
    from db.session import get_sql_engine as get_engine
    return get_engine()


def bootstrap_tables() -> None:
    """Idempotent. Create 3 tables for template storage."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    eng = _get_engine()
    if eng is None:
        return
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS dash_template_expectations (
            project_slug TEXT PRIMARY KEY,
            template_name TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expectations JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dash_template_bindings (
            project_slug TEXT NOT NULL,
            template_ref TEXT NOT NULL,
            real_ref TEXT,
            status TEXT NOT NULL DEFAULT 'unbound',
            match_method TEXT,
            confidence NUMERIC,
            reconciled_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (project_slug, template_ref)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dash_autonomous_workflows (
            id BIGSERIAL PRIMARY KEY,
            project_slug TEXT NOT NULL,
            template_name TEXT,
            name TEXT NOT NULL,
            description TEXT,
            schedule TEXT,
            query_template TEXT,
            resolved_query TEXT,
            expected_entity TEXT,
            expected_columns JSONB DEFAULT '[]'::jsonb,
            action TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            last_run_at TIMESTAMPTZ,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_dash_aw_project ON dash_autonomous_workflows(project_slug, status)",
        "CREATE INDEX IF NOT EXISTS ix_dash_bindings_project ON dash_template_bindings(project_slug, status)",
    ]
    try:
        with eng.begin() as cn:
            for stmt in ddl:
                cn.execute(text(stmt))
        _BOOTSTRAPPED = True
        logger.info("template tables bootstrapped")
    except Exception as e:
        logger.warning("template bootstrap failed: %s", e)


def save_expectations(project_slug: str, template_name: str, expectations: dict) -> None:
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return
    with eng.begin() as cn:
        cn.execute(
            text(
                """
                INSERT INTO dash_template_expectations(project_slug, template_name, expectations, applied_at)
                VALUES (:slug, :tname, CAST(:exp AS jsonb), NOW())
                ON CONFLICT (project_slug) DO UPDATE
                  SET template_name = EXCLUDED.template_name,
                      expectations  = EXCLUDED.expectations,
                      applied_at    = NOW()
                """
            ),
            {"slug": project_slug, "tname": template_name, "exp": json.dumps(expectations)},
        )


def load_expectations(project_slug: str) -> dict | None:
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return None
    with eng.begin() as cn:
        row = cn.execute(
            text("SELECT template_name, applied_at, expectations FROM dash_template_expectations WHERE project_slug=:s"),
            {"s": project_slug},
        ).fetchone()
    if not row:
        return None
    return {
        "template_name": row[0],
        "applied_at": row[1].isoformat() if row[1] else None,
        "expectations": row[2] or {},
    }


def save_autonomous_workflows(project_slug: str, template_name: str, workflows: list[dict]) -> int:
    """Insert all template workflows in pending state. Returns count saved."""
    bootstrap_tables()
    eng = _get_engine()
    if eng is None or not workflows:
        return 0
    with eng.begin() as cn:
        # delete prior workflows for this template (re-apply scenario)
        cn.execute(
            text("DELETE FROM dash_autonomous_workflows WHERE project_slug=:s AND template_name=:t"),
            {"s": project_slug, "t": template_name},
        )
        n = 0
        for wf in workflows:
            cn.execute(
                text(
                    """
                    INSERT INTO dash_autonomous_workflows
                      (project_slug, template_name, name, description, schedule,
                       query_template, expected_entity, expected_columns, action, status)
                    VALUES (:s, :t, :n, :d, :sch, :q, :ee, CAST(:ec AS jsonb), :a, 'pending')
                    """
                ),
                {
                    "s": project_slug,
                    "t": template_name,
                    "n": wf.get("name", ""),
                    "d": wf.get("description", ""),
                    "sch": wf.get("schedule", "daily"),
                    "q": wf.get("query_template", ""),
                    "ee": wf.get("expected_entity", ""),
                    "ec": json.dumps(wf.get("expected_columns") or []),
                    "a": wf.get("action", "post_insight"),
                },
            )
            n += 1
    return n


def list_autonomous_workflows(project_slug: str) -> list[dict]:
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return []
    with eng.begin() as cn:
        rows = cn.execute(
            text(
                """
                SELECT id, name, description, schedule, status, expected_entity,
                       expected_columns, action, last_run_at, last_error
                FROM dash_autonomous_workflows
                WHERE project_slug=:s
                ORDER BY id
                """
            ),
            {"s": project_slug},
        ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "description": r[2],
            "schedule": r[3],
            "status": r[4],
            "expected_entity": r[5],
            "expected_columns": r[6] or [],
            "action": r[7],
            "last_run_at": r[8].isoformat() if r[8] else None,
            "last_error": r[9],
        }
        for r in rows
    ]


def list_bindings(project_slug: str) -> list[dict]:
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return []
    with eng.begin() as cn:
        rows = cn.execute(
            text(
                """
                SELECT template_ref, real_ref, status, match_method, confidence, reconciled_at
                FROM dash_template_bindings
                WHERE project_slug=:s
                ORDER BY template_ref
                """
            ),
            {"s": project_slug},
        ).fetchall()
    return [
        {
            "template_ref": r[0],
            "real_ref": r[1],
            "status": r[2],
            "match_method": r[3],
            "confidence": float(r[4]) if r[4] is not None else None,
            "reconciled_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


def upsert_binding(
    project_slug: str,
    template_ref: str,
    real_ref: str | None,
    status: str = "bound",
    match_method: str = "manual",
    confidence: float | None = None,
) -> None:
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return
    with eng.begin() as cn:
        cn.execute(
            text(
                """
                INSERT INTO dash_template_bindings
                  (project_slug, template_ref, real_ref, status, match_method, confidence, reconciled_at)
                VALUES (:s, :tr, :rr, :st, :mm, :cf, NOW())
                ON CONFLICT (project_slug, template_ref) DO UPDATE
                  SET real_ref      = EXCLUDED.real_ref,
                      status        = EXCLUDED.status,
                      match_method  = EXCLUDED.match_method,
                      confidence    = EXCLUDED.confidence,
                      reconciled_at = NOW()
                """
            ),
            {
                "s": project_slug,
                "tr": template_ref,
                "rr": real_ref,
                "st": status,
                "mm": match_method,
                "cf": confidence,
            },
        )
