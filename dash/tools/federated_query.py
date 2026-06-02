"""Federated query Agno tool — JOIN across multiple sources within ONE project.

Pipeline:
  1. parse(sql)              identify source prefixes
  2. is_federated?           if single source, route to query_<id> instead
  3. resolve(...)             check visibility within project
  4. split(parsed)            build per-source subqueries
  5. execute_split_plan       parallel exec, per-source timeout + cap
  6. merge(plan, results)     DuckDB primary, pandas fallback
  7. format result            8KB cap, PII masking, audit log

Self-correction loop: up to 3 attempts with corrective adjustments
between attempts (relax filters, drop bad columns, strip aliases,
swap join keys).
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def make_federated_tool(project_slug: str, agent_role: str = "analyst",
                         user_id: Optional[int] = None):
    """Return Agno @tool callable for the Analyst.

    Closure captures project_slug + agent_role for tenant isolation.
    """
    try:
        from agno.tools import tool
    except ImportError:
        return None

    @tool(
        name="federated_query",
        description=(
            "Run a SELECT query that JOINs across multiple sources in this "
            "project. Use for cross-source analysis (e.g. SQL DB + file table). "
            "Sources addressed as <provider_id>.<table_name>. "
            "Read-only. Result capped at 50K rows. Federation only works "
            "WITHIN this project — never reads other projects."
        ),
    )
    def federated_query(sql: str) -> str:
        return _run_federated_sync(sql, project_slug, agent_role, user_id)

    return federated_query


def _run_federated_sync(sql: str, project_slug: str, agent_role: str,
                         user_id: Optional[int]) -> str:
    """Sync entrypoint. Runs self-correction loop (up to 3 attempts)."""
    return _self_correct(sql, project_slug, agent_role, user_id)


def _attempt_federated(sql: str, project_slug: str, agent_role: str,
                        user_id: Optional[int], attempt: int = 1) -> dict:
    """Single pipeline attempt. Returns dict with status + payload OR error+suggestions.

    status values:
      ok | zero_rows | exec_error | merge_error |
      translate_error | resolve_error | split_error | parse_error | disabled
    """
    try:
        from dash.providers.federation.parser import parse, is_select_only
        from dash.providers.federation.resolver import resolve
        from dash.providers.federation.splitter import split
        from dash.providers.federation.executor import execute_split_plan
    except Exception as e:
        return {
            "status": "exec_error",
            "result": "",
            "exec_errors": {"_subsystem": str(e)},
            "merge_warnings": [],
            "row_count": 0,
            "sources": [],
            "fail_reason": f"subsystem unavailable: {e}",
        }

    # 1. Safety check
    if not is_select_only(sql):
        return {
            "status": "parse_error",
            "result": "",
            "exec_errors": {},
            "merge_warnings": [],
            "row_count": 0,
            "sources": [],
            "fail_reason": "only SELECT/WITH queries allowed (no DDL/DML/stacked)",
        }

    # 2. Parse
    parsed = parse(sql)
    if parsed.error:
        return {
            "status": "parse_error",
            "result": "",
            "exec_errors": {},
            "merge_warnings": [],
            "row_count": 0,
            "sources": [],
            "fail_reason": f"parse error: {parsed.error}",
        }

    if not parsed.is_federated:
        if len(parsed.provider_ids) == 1:
            pid = next(iter(parsed.provider_ids))
            return {
                "status": "ok",
                "result": (f"NOT FEDERATED — only one source ('{pid}'). "
                           f"Call query_{pid}(sql) directly for better performance."),
                "exec_errors": {},
                "merge_warnings": [],
                "row_count": 0,
                "sources": list(parsed.provider_ids),
                "fail_reason": "",
            }
        return {
            "status": "parse_error",
            "result": "",
            "exec_errors": {},
            "merge_warnings": [],
            "row_count": 0,
            "sources": [],
            "fail_reason": "no source prefixes found in SQL",
        }

    # 3. Admin gate
    try:
        from dash.admin.settings import get_setting
        if not get_setting("enable_federation_join", project_slug=project_slug):
            return {
                "status": "disabled",
                "result": "",
                "exec_errors": {},
                "merge_warnings": [],
                "row_count": 0,
                "sources": list(parsed.provider_ids),
                "fail_reason": "admin setting enable_federation_join is off",
            }

        # Circuit breaker check
        try:
            from dash.providers.federation.circuit_breaker import check as cb_check
            cb_state = cb_check(project_slug)
            if cb_state.is_open:
                return {
                    "status": "disabled",
                    "result": "",
                    "exec_errors": {},
                    "merge_warnings": [],
                    "row_count": 0,
                    "sources": list(parsed.provider_ids),
                    "fail_reason": (
                        f"FEDERATION CIRCUIT OPEN: too many recent failures. "
                        f"Cooldown until {cb_state.open_until}. "
                        f"Last error: {cb_state.last_error}"
                    ),
                }
        except Exception:
            pass
        max_rows = int(get_setting("max_cross_source_rows") or 50_000)
        timeout_s = float(get_setting("federation_timeout_s") or 60)
        engine_pref = str(get_setting("federation_default_engine") or "duckdb")
    except Exception:
        max_rows = 50_000
        timeout_s = 60.0
        engine_pref = "duckdb"

    # 4. Resolve sources within project (RBAC-aware when user_id is known)
    if user_id is not None:
        from dash.providers.federation.resolver import resolve_with_rbac
        resolution = resolve_with_rbac(
            parsed.provider_ids, project_slug, user_id,
            requesting_agent_scope=agent_role,
            user_role="editor",
        )
    else:
        resolution = resolve(parsed.provider_ids, project_slug,
                             requesting_agent_scope=agent_role,
                             user_role="editor")
    if not resolution.all_accessible:
        return {
            "status": "resolve_error",
            "result": "",
            "exec_errors": {},
            "merge_warnings": [],
            "row_count": 0,
            "sources": list(parsed.provider_ids),
            "fail_reason": "; ".join(resolution.errors[:5]),
        }

    # 5. Split
    plan = split(parsed)
    if plan.error:
        return {
            "status": "split_error",
            "result": "",
            "exec_errors": {},
            "merge_warnings": [],
            "row_count": 0,
            "sources": list(parsed.provider_ids),
            "fail_reason": f"split error: {plan.error}",
        }

    # 6. Execute
    try:
        try:
            asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(_run_async_wrapper, plan, project_slug, timeout_s)
                exec_result = future.result(timeout=timeout_s + 30)
        except RuntimeError:
            exec_result = asyncio.run(execute_split_plan(
                plan, project_slug=project_slug,
                per_source_timeout_s=timeout_s,
            ))
    except Exception as e:
        return {
            "status": "exec_error",
            "result": "",
            "exec_errors": {"_runtime": str(e)[:300]},
            "merge_warnings": [],
            "row_count": 0,
            "sources": list(parsed.provider_ids),
            "fail_reason": f"exec error: {str(e)[:300]}",
        }

    if exec_result.errors:
        if len(exec_result.errors) >= len(plan.subqueries):
            return {
                "status": "exec_error",
                "result": "",
                "exec_errors": dict(exec_result.errors),
                "merge_warnings": [],
                "row_count": 0,
                "sources": list(parsed.provider_ids),
                "fail_reason": "all sources failed",
            }

    # 7. Merge
    try:
        if engine_pref == "duckdb":
            from dash.providers.federation.merge_duckdb import merge as duck_merge
            merge_result = duck_merge(plan, exec_result, max_final_rows=max_rows)
            if merge_result.error:
                from dash.providers.federation.merge_pandas import merge as pd_merge
                merge_result = pd_merge(plan, exec_result, max_final_rows=max_rows)
        else:
            from dash.providers.federation.merge_pandas import merge as pd_merge
            merge_result = pd_merge(plan, exec_result, max_final_rows=max_rows)
    except Exception as e:
        return {
            "status": "merge_error",
            "result": "",
            "exec_errors": dict(exec_result.errors) if exec_result.errors else {},
            "merge_warnings": [],
            "row_count": 0,
            "sources": list(parsed.provider_ids),
            "fail_reason": f"merge error: {str(e)[:300]}",
        }

    if merge_result.error:
        return {
            "status": "merge_error",
            "result": "",
            "exec_errors": dict(exec_result.errors) if exec_result.errors else {},
            "merge_warnings": list(merge_result.warnings or []),
            "row_count": 0,
            "sources": list(parsed.provider_ids),
            "fail_reason": f"merge error: {merge_result.error}",
        }

    # 8. Format result
    try:
        df = merge_result.df
        if df is None or len(df) == 0:
            return {
                "status": "zero_rows",
                "result": "FEDERATION RESULT: 0 rows",
                "exec_errors": dict(exec_result.errors) if exec_result.errors else {},
                "merge_warnings": list(merge_result.warnings or []),
                "row_count": 0,
                "sources": list(parsed.provider_ids),
                "fail_reason": "zero rows returned",
            }
        formatted = _format_dataframe(df)
        prefix = (f"# FEDERATED · {len(parsed.provider_ids)} sources · "
                  f"{merge_result.row_count} rows · {merge_result.duration_ms}ms · "
                  f"engine={merge_result.engine_used}\n")
        if exec_result.truncated:
            prefix += "# WARNING: per-source row cap hit; result may be incomplete\n"
        if merge_result.warnings:
            prefix += f"# warnings: {'; '.join(merge_result.warnings[:3])}\n"
        return {
            "status": "ok",
            "result": prefix + formatted,
            "exec_errors": {},
            "merge_warnings": list(merge_result.warnings or []),
            "row_count": merge_result.row_count,
            "sources": list(parsed.provider_ids),
            "fail_reason": "",
            "_audit": {
                "sql": sql,
                "sources": list(parsed.provider_ids),
                "row_count": merge_result.row_count,
                "duration_ms": merge_result.duration_ms,
            },
        }
    except Exception as e:
        return {
            "status": "exec_error",
            "result": "",
            "exec_errors": {"_format": str(e)[:200]},
            "merge_warnings": [],
            "row_count": 0,
            "sources": list(parsed.provider_ids),
            "fail_reason": f"format error: {str(e)[:200]}",
        }


def _self_correct(sql: str, project_slug: str, agent_role: str,
                   user_id: Optional[int]) -> str:
    """Run up to 3 attempts. Apply corrections between attempts.

    Audit log fires only on a successful final attempt.
    """
    last_attempt = None
    correction_log = []
    attempt = 0

    for attempt in range(1, 4):
        result = _attempt_federated(sql, project_slug, agent_role, user_id, attempt)
        last_attempt = result

        if result["status"] == "ok":
            # Audit log only on success
            try:
                audit = result.get("_audit")
                if audit:
                    _audit_federated(
                        project_slug, user_id,
                        audit["sql"], audit["sources"],
                        audit["row_count"], audit["duration_ms"],
                    )
            except Exception:
                pass

            # Circuit breaker: record success
            try:
                from dash.providers.federation.circuit_breaker import record_success
                record_success(project_slug)
            except Exception:
                pass

            if attempt > 1 and correction_log:
                return (f"# CORRECTED after {attempt} attempts:\n# "
                        + "\n# ".join(correction_log) + "\n\n" + result["result"])
            return result["result"]

        # Apply correction strategy based on status
        if result["status"] == "zero_rows":
            new_sql = _relax_filter(sql, attempt)
            if new_sql == sql:
                break
            correction_log.append(f"attempt {attempt}: 0 rows, relaxed filter")
            sql = new_sql

        elif result["status"] == "translate_error":
            correction_log.append(f"attempt {attempt}: translate fail, using canonical")
            break

        elif result["status"] == "exec_error":
            err_str = "; ".join(str(v) for v in result.get("exec_errors", {}).values())
            new_sql = _drop_problem_column(sql, err_str)
            if new_sql == sql:
                new_sql = _strip_aliases(sql)
            if new_sql == sql:
                break
            correction_log.append(f"attempt {attempt}: exec error, retried w/ fix")
            sql = new_sql

        elif result["status"] == "merge_error":
            alt_sql = _try_alt_join_key(sql, project_slug)
            if alt_sql is None or alt_sql == sql:
                break
            correction_log.append(f"attempt {attempt}: merge fail, alt join key")
            sql = alt_sql

        else:
            break  # unrecoverable (parse_error, resolve_error, split_error, disabled)

    # Final failure
    err_summary = (last_attempt.get("fail_reason", "unknown")
                   if last_attempt else "no attempts")
    log_str = "\n# ".join(correction_log) if correction_log else "no corrections attempted"

    # Circuit breaker: record failure for unrecoverable error states
    try:
        if last_attempt and last_attempt.get("status") in (
            "exec_error", "merge_error", "translate_error"
        ):
            from dash.providers.federation.circuit_breaker import record_failure
            record_failure(project_slug, str(err_summary))
    except Exception:
        pass

    return (f"FEDERATION FAILED after {attempt} attempts.\n"
            f"# correction log:\n# {log_str}\n"
            f"# final error: {err_summary}")


def _relax_filter(sql: str, attempt: int) -> str:
    """Remove the last AND clause from WHERE. If multiple, remove most restrictive."""
    m = re.search(r"(WHERE\s+.+?)\s+AND\s+([^)]+?)(?:\s+(?:GROUP|ORDER|LIMIT)\b|$)",
                   sql, re.IGNORECASE | re.DOTALL)
    if m:
        return sql.replace(f" AND {m.group(2)}", "", 1)
    return sql


def _drop_problem_column(sql: str, error_str: str) -> str:
    """If error mentions specific column, try removing it from SELECT."""
    m = re.search(r'(?:column[\s\'"]+)([a-zA-Z_][a-zA-Z0-9_]*)',
                   error_str, re.IGNORECASE)
    if not m:
        return sql
    bad_col = m.group(1)
    new_sql = re.sub(rf'\b\w+\.{bad_col}\b\s*,?\s*', '', sql, flags=re.IGNORECASE)
    new_sql = re.sub(rf'\b{bad_col}\b\s*,?\s*', '', new_sql, flags=re.IGNORECASE)
    new_sql = re.sub(r',\s*FROM', ' FROM', new_sql, flags=re.IGNORECASE)
    if new_sql.strip() != sql.strip():
        return new_sql
    return sql


def _strip_aliases(sql: str) -> str:
    """Remove AS aliases from FROM clause — sometimes confuses dialect translator."""
    return re.sub(r'\s+AS\s+\w+', '', sql, flags=re.IGNORECASE)


def _try_alt_join_key(sql: str, project_slug: str) -> Optional[str]:
    """Use semantic_union to find alternate join key with high confidence."""
    try:
        from dash.providers.federation.semantic_union import build
        catalog = build(project_slug)
        for js in catalog.join_suggestions[:5]:
            if js.confidence >= 0.7:
                pattern = r"ON\s+\w+\.\w+\s*=\s*\w+\.\w+"
                new_clause = (f"ON {js.left_table}.{js.left_column} = "
                                f"{js.right_table}.{js.right_column}")
                new_sql = re.sub(pattern, new_clause, sql,
                                   count=1, flags=re.IGNORECASE)
                if new_sql != sql:
                    return new_sql
        return None
    except Exception:
        return None


def _run_async_wrapper(plan, project_slug, timeout_s):
    """Run execute_split_plan in fresh event loop (for thread executor case)."""
    from dash.providers.federation.executor import execute_split_plan
    return asyncio.run(execute_split_plan(
        plan, project_slug=project_slug, per_source_timeout_s=timeout_s,
    ))


def _format_dataframe(df) -> str:
    """Format pandas DataFrame as compact CSV. Cap 8KB."""
    try:
        import io
        buf = io.StringIO()
        display_rows = min(len(df), 200)
        df.head(display_rows).to_csv(buf, index=False)
        out = buf.getvalue()
        if len(out) > 8192:
            out = out[:8192] + "\n... [truncated to 8KB]"
        return out
    except Exception as e:
        return f"format error: {e}"


def _audit_federated(project_slug, user_id, sql, sources, row_count, duration_ms,
                       cost_usd: float = 0.0):
    """Log to dash_audit_log."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_audit_log "
                "(project_slug, action, target, details, "
                " sources_used, row_count, latency_ms, cost_usd, created_at) "
                "VALUES (:slug, 'federated_query', :tgt, :det, "
                " :su, :rc, :lat, :cost, NOW())"
            ), {
                "slug": project_slug,
                "tgt": ",".join(sources),
                "det": json.dumps({
                    "user_id": user_id,
                    "sources": sources,
                    "rows": row_count,
                    "duration_ms": duration_ms,
                    "sql_first_200": sql[:200],
                }),
                "su": json.dumps(sources),
                "rc": row_count,
                "lat": duration_ms,
                "cost": cost_usd,
            })
            conn.commit()
    except Exception:
        pass
