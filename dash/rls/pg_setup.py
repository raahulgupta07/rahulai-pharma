"""Helper to enable Postgres RLS on a project's tables.

For each table_filter in config, runs:
  ALTER TABLE <schema>.<table> ENABLE ROW LEVEL SECURITY;
  ALTER TABLE <schema>.<table> FORCE ROW LEVEL SECURITY;
  CREATE POLICY <project>_<table>_isolation ON <schema>.<table>
    USING (<bound_filter_using_current_setting>);

The filter expression in config uses :keyname binds. We translate to:
  current_setting('app.keyname', true)
"""
import re
import json
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from db import db_url

_BIND = re.compile(r":([a-z_][a-z0-9_]*)")


def _translate(expr: str) -> str:
    """':store_id' -> current_setting('app.store_id', true).

    Default: text. Caller can edit policy DDL after to add ::int casts when needed.
    """
    return _BIND.sub(lambda m: f"current_setting('app.{m.group(1)}', true)", expr)


def apply_policies(project_slug: str, schema: str | None = None) -> dict:
    eng = create_engine(db_url, poolclass=NullPool)
    applied = []
    errors = []
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT enabled, mode, table_filters FROM dash_project_rls_config WHERE project_slug=:s"
            ), {"s": project_slug}).mappings().first()
            if not row or not row["enabled"]:
                return {"status": "skipped", "reason": "rls disabled"}
            tf = row["table_filters"]
            if isinstance(tf, str):
                tf = json.loads(tf)
            schema = schema or project_slug.replace("-", "_")
        with eng.begin() as conn:
            for tname, expr in (tf or {}).items():
                pol_name = f"dash_rls_{project_slug}_{tname}".replace("-", "_")[:63]
                using = _translate(expr)
                try:
                    conn.execute(text(f'ALTER TABLE "{schema}"."{tname}" ENABLE ROW LEVEL SECURITY'))
                    conn.execute(text(f'ALTER TABLE "{schema}"."{tname}" FORCE ROW LEVEL SECURITY'))
                    conn.execute(text(f'DROP POLICY IF EXISTS "{pol_name}" ON "{schema}"."{tname}"'))
                    conn.execute(text(f'CREATE POLICY "{pol_name}" ON "{schema}"."{tname}" USING ({using})'))
                    applied.append({"table": tname, "policy": pol_name, "using": using})
                except Exception as e:
                    errors.append({"table": tname, "error": str(e)})
    finally:
        eng.dispose()
    return {"status": "ok" if not errors else "partial", "applied": applied, "errors": errors}


def remove_policies(project_slug: str, schema: str | None = None) -> dict:
    eng = create_engine(db_url, poolclass=NullPool)
    removed = []
    try:
        schema = schema or project_slug.replace("-", "_")
        with eng.begin() as conn:
            rows = conn.execute(text("""
                SELECT tablename, policyname FROM pg_policies
                WHERE schemaname=:sch AND policyname LIKE :pfx
            """), {"sch": schema, "pfx": f"dash_rls_{project_slug}_%"}).mappings().all()
            for r in rows:
                conn.execute(text(f'DROP POLICY IF EXISTS "{r["policyname"]}" ON "{schema}"."{r["tablename"]}"'))
                removed.append(dict(r))
    finally:
        eng.dispose()
    return {"status": "ok", "removed": removed}
