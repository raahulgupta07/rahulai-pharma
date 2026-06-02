"""
Tool Assembly
=============

Factory functions that assemble tools per agent role.

Schema boundaries:
- Analyst: read-only SQL against public + user schema.
- Engineer: full SQL scoped to user schema. Creates views, summary tables.
"""

import os

from agno.knowledge import Knowledge
from agno.tools.reasoning import ReasoningTools
from agno.tools.sql import SQLTools


class RLSAwareSQLTools(SQLTools):
    """Wraps SQLTools.run_sql_query so RLS PermissionError surfaces to the agent
    as a structured tool result instead of being swallowed by Agno's generic
    exception handler. Fixes issue #4: silent "no rows" → user-visible
    "access denied" message.

    Also: MDL compile pass (semantic SQL → raw SQL) when `project_slug` set
    and MDL models exist for project. Pass-through when no models. Fail-soft.
    """
    def __init__(self, *args, project_slug: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._mdl_slug = project_slug

    def run_sql_query(self, query: str = None, *args, **extra):  # type: ignore[override]
        # ── Malformed-call guard ──────────────────────────────────────────
        # Some models wrap the call in {"args":"{}","kwargs":"{}","query":"SELECT…"}
        # or pass the real SQL as a JSON string. Agno's SQLTools then rejects it
        # ("got unexpected keyword argument 'args'") → wasted failed call + retry.
        # Tolerate: swallow stray args/kwargs keys via **extra, and unwrap a
        # JSON-string `query` that nests the real SQL. Fail-soft.
        try:
            # If query came in as one of the stray keys instead of positional.
            if query is None:
                for _k in ("query", "sql", "statement", "sql_query"):
                    _v = extra.get(_k)
                    if isinstance(_v, str) and _v.strip():
                        query = _v
                        break
            # If query is itself a JSON object/string carrying a nested query.
            if isinstance(query, str):
                _q = query.strip()
                if _q.startswith("{") and ('"query"' in _q or "'query'" in _q):
                    try:
                        import json as _json_g
                        _parsed = _json_g.loads(_q)
                        if isinstance(_parsed, dict):
                            for _k in ("query", "sql", "statement", "sql_query"):
                                _v = _parsed.get(_k)
                                if isinstance(_v, str) and _v.strip():
                                    query = _v
                                    break
                    except Exception:
                        pass
        except Exception:
            pass
        if not isinstance(query, str) or not query.strip():
            return (
                '{"ok": false, "error": "NO_QUERY", '
                '"message": "No SQL query was provided.", '
                '"hint": "Call run_sql_query with a single SQL string in the query argument."}'
            )
        # Tool-call guardrail — block identical-args retry loops. Fail-soft.
        try:
            from dash.runtime.tool_guardrail import check as _g_check
            from dash.links_ctx import CUR_SESSION_ID
            _sid = CUR_SESSION_ID.get() or (self._mdl_slug or "global")
            _decision = _g_check("run_sql_query", {"sql": query.strip()}, session_id=_sid)
            if _decision.action in ("block", "halt") and _decision.synthetic_result:
                import logging as _glog
                _glog.getLogger(__name__).info(
                    f"[guardrail] {_decision.action} run_sql_query: {_decision.reason}"
                )
                return _decision.synthetic_result
        except Exception:
            pass
        # MDL compile: semantic table/col names → raw. Pass-through when no models.
        # Fail-soft: any error keeps original SQL.
        if getattr(self, "_mdl_slug", None):
            try:
                from dash.semantic import compile_query as _mdl_compile
                _compiled = _mdl_compile(self._mdl_slug, query)
                if _compiled and _compiled != query:
                    import logging as _mlog
                    _mlog.getLogger(__name__).debug(
                        f"[mdl] compiled for {self._mdl_slug}: {query[:80]}... → {_compiled[:80]}..."
                    )
                    query = _compiled
            except Exception as _me:
                import logging as _mlog
                _mlog.getLogger(__name__).debug(f"[mdl] compile skipped: {_me}")
        # SQL-VALIDATED — chat-time auto-fix (TEXT::date casts etc) before exec.
        # Closes class where LLM emits `date_trunc('month', text_col)` causing
        # UndefinedFunction. Auto-fix lets first attempt succeed instead of
        # burning 3 retry cycles. Fail-soft: skip on any validator error.
        if getattr(self, "_mdl_slug", None):
            try:
                from dash.tools.sql_validator import validate_and_fix as _vf
                _v = _vf(query, self._mdl_slug, strict=False)
                if _v.get("sql") and _v.get("fixes_applied"):
                    import logging as _vlog
                    _vlog.getLogger(__name__).info(
                        f"[sql-validator] chat-time auto-fix on {self._mdl_slug}: {_v['fixes_applied']}"
                    )
                    query = _v["sql"]
                    # Telemetry: chat-time auto-fix event. Fail-soft.
                    try:
                        from dash.tools.sql_validator import _emit_event as _se
                        _se(
                            "chat_autofix",
                            project_slug=self._mdl_slug,
                            source="chat_runtime",
                            details={"fixes": _v["fixes_applied"]},
                        )
                    except Exception:
                        pass
            except Exception:
                pass
        # Cost pre-flight (EXPLAIN-based). Fail-soft: never blocks on guard error.
        try:
            from dash.tools.sql_cost_guard import guard_or_note as _cost_note
            _eng = getattr(self, "db_engine", None)
            if _eng is not None:
                _warn = _cost_note(_eng, query)
                if _warn:
                    return (
                        '{"ok": false, "error": "QUERY_TOO_EXPENSIVE", '
                        f'"message": "{_warn}", '
                        '"hint": "Add WHERE filters or a LIMIT to reduce scope, then retry. '
                        'Do NOT run this query as-is."}'
                    )
        except Exception:
            pass
        # Lazy profile_v2: ensure profile metadata exists for every table referenced in the SQL.
        # Closes class where new table added post-train returns wrong results (stale prompt).
        # Cost: ~1.4s first hit per unprofiled table, then cached. Fail-soft.
        if getattr(self, "_mdl_slug", None) and not os.getenv("LAZY_PROFILE_V2_DISABLED"):
            try:
                import sqlglot
                from sqlalchemy import text as _sa_text
                from db.session import get_sql_engine as _gse
                # Extract table refs
                _tables_ref = set()
                try:
                    _parsed = sqlglot.parse_one(query, dialect="postgres")
                    for _t in _parsed.find_all(sqlglot.exp.Table):
                        _name = (_t.name or "").lower().strip()
                        if _name and not _name.startswith("pg_") and not _name.startswith("_"):
                            _tables_ref.add(_name)
                except Exception:
                    pass
                # Cap protection: pathological UNION ALL across schema
                if _tables_ref and len(_tables_ref) <= 10:
                    _eng_meta = _gse()
                    # Check which tables lack profile_v2 metadata
                    with _eng_meta.connect() as _c:
                        _existing = {
                            r[0] for r in _c.execute(_sa_text(
                                "SELECT table_name FROM public.dash_table_metadata "
                                "WHERE project_slug = :p AND table_name = ANY(:t) "
                                "AND metadata ? 'profile_v2'"
                            ), {"p": self._mdl_slug, "t": list(_tables_ref)}).fetchall()
                        }
                    _missing = _tables_ref - _existing
                    if _missing:
                        import logging as _plog
                        _plog.getLogger(__name__).info(
                            f"[lazy-profile] auto-profiling {len(_missing)} unprofiled table(s) "
                            f"for {self._mdl_slug}: {sorted(_missing)}"
                        )
                        from dash.training.profile_v2 import profile_table_v2 as _ptv2
                        for _tbl in _missing:
                            try:
                                _ptv2(self._mdl_slug, _tbl)
                            except Exception as _pe:
                                _plog.getLogger(__name__).warning(
                                    f"[lazy-profile] failed for {self._mdl_slug}.{_tbl}: {_pe}"
                                )
                        # Invalidate prompt cache so next turn picks up new profile
                        try:
                            from dash.instructions import _PROFILE_V2_CACHE as _ppc
                            _ppc.pop(self._mdl_slug, None)
                        except Exception:
                            pass
            except Exception as _le:
                import logging as _llog
                _llog.getLogger(__name__).debug(f"[lazy-profile] skipped: {_le}")
        try:
            # Only forward the cleaned SQL string. Stray wrapper keys
            # (args/kwargs/query/...) in **extra are intentionally dropped so
            # the parent SQLTools never sees an unexpected keyword argument.
            _result = super().run_sql_query(query)
            # Guardrail: record success. Fail-soft.
            try:
                from dash.runtime.tool_guardrail import record as _g_rec
                from dash.links_ctx import CUR_SESSION_ID
                _g_rec("run_sql_query", {"sql": query.strip()},
                       success=True, result=_result,
                       session_id=CUR_SESSION_ID.get() or (self._mdl_slug or "global"))
            except Exception:
                pass
            # Obsidian-style bidirectional links: chat → cites → table. Fail-soft.
            try:
                from dash.links_ctx import extract_table_refs, link_chat_cites_tables
                _tbls = extract_table_refs(query)
                if _tbls:
                    link_chat_cites_tables(_tbls)
            except Exception:
                import logging as _l
                _l.getLogger(__name__).debug("dash_links write skipped", exc_info=True)
            return _result
        except PermissionError as e:
            import logging as _log
            _log.getLogger(__name__).info(f"RLS blocked SQL: {e}")
            try:
                from dash.runtime.tool_guardrail import record as _g_rec
                from dash.links_ctx import CUR_SESSION_ID
                _g_rec("run_sql_query", {"sql": query.strip()},
                       success=False,
                       session_id=CUR_SESSION_ID.get() or (self._mdl_slug or "global"))
            except Exception:
                pass
            return (
                '{"ok": false, "error": "RLS_BLOCKED", '
                f'"message": "Row-level security blocked this query: {str(e)[:200]}", '
                '"hint": "Tell the user you cannot access this data for their current scope; do NOT retry."}'
            )
        except Exception as _exec_err:
            # Catch SQL exec errors so guardrail counts them. Re-raise after recording.
            try:
                from dash.runtime.tool_guardrail import record as _g_rec
                from dash.links_ctx import CUR_SESSION_ID
                _g_rec("run_sql_query", {"sql": query.strip()},
                       success=False,
                       session_id=CUR_SESSION_ID.get() or (self._mdl_slug or "global"))
            except Exception:
                pass
            raise

from dash.tools.dashboard import create_dashboard_tool
from dash.tools.forecast import run_forecast
from dash.tools.specialist import (
    detect_anomalies, run_pareto, compare_periods,
    root_cause_drill, scenario_model, benchmark_check, correlation_matrix,
)
from dash.tools.introspect import create_introspect_schema_tool
from dash.tools.semantic_search import create_search_all_tool
from dash.tools.save_query import create_save_validated_query_tool
from dash.tools.update_knowledge import create_update_knowledge_tool
# ML model tools (predict, feature_importance, anomaly_ml, llm_predict)
# now owned by Data Scientist agent — see dash/agents/data_scientist.py
from db import db_url, get_readonly_engine, get_sql_engine, get_user_engine, get_user_readonly_engine
from db.session import _sanitize_user_id

from sqlalchemy import event as _sa_event
from dash.tools.skill_refinery import get_user_attrs as _rls_get_attrs


def _attach_rls_rewrite(engine, project_slug: str | None) -> None:
    """Attach a SQLAlchemy before_cursor_execute listener that applies the
    Layer 2 RLS rewriter to every SQL statement run via this engine.

    No-op when project_slug is None. Per-engine idempotent — listener stamps
    a `_rls_attached` attribute so we don't double-attach when engines are
    cached (e.g., per-project engine cache in db.session).
    """
    if not project_slug or engine is None:
        return
    if getattr(engine, "_rls_attached", False):
        return

    @_sa_event.listens_for(engine, "before_cursor_execute", retval=True)
    def _rewrite_listener(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        try:
            from dash.rls.rewriter import rewrite as _rls_rewrite
            attrs = _rls_get_attrs() or {}
            new_sql = _rls_rewrite(statement, project_slug, attrs)
        except PermissionError:
            raise
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"RLS rewrite failed (passthrough): {e}")
            new_sql = statement

        # Phase 2 — visibility policy: field-level downgrade after row-level RLS.
        try:
            from dash.policy import apply_policy as _apply_policy
            from dash.tools.skill_refinery import get_query_intent as _get_intent
            intent = _get_intent() or "private"
            policy_sql, dropped = _apply_policy(new_sql, project_slug, intent)
            if dropped:
                try:
                    from dash.rls.audit import log_rls_event
                    from dash.tools.skill_refinery import get_external_user as _get_ext
                    log_rls_event(
                        project_slug, statement, rewritten_sql=policy_sql,
                        mode=f"policy:{intent}",
                        user_attrs=_rls_get_attrs(), external_user=_get_ext(),
                        fields_downgraded=list(dropped),
                    )
                except Exception:
                    pass
            new_sql = policy_sql
            # Phase 7A — cross-store read audit (only on non-private intents).
            try:
                if intent and intent != "private":
                    from dash.policy.read_audit import log_read as _log_read
                    from dash.policy.loader import load_policy as _load_pol
                    from dash.tools.skill_refinery import (
                        get_viewer_user_id as _get_vuid,
                        get_viewer_scope_id as _get_vsid,
                    )
                    pol = _load_pol(project_slug)
                    pol_ver = getattr(pol, "version", None) if pol else None
                    target_scope = (attrs or {}).get("store_id") or (attrs or {}).get("region")
                    _log_read(
                        project_slug=project_slug,
                        viewer_user_id=_get_vuid(),
                        viewer_scope_id=_get_vsid(),
                        target_scope_id=target_scope,
                        intent=intent,
                        policy_version=pol_ver,
                        sql=policy_sql,
                        fields_downgraded=list(dropped) if dropped else [],
                        row_count=None,
                    )
            except Exception:
                pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"visibility policy failed (passthrough): {e}")

        return new_sql, params

    try:
        engine._rls_attached = True
    except Exception:
        pass


def _attach_embed_rls(engine) -> None:
    """Embed row-level security guard (migration 064).

    Reads policies from EMBED_RLS_POLICIES ContextVar. If set and non-empty,
    every SQL statement is rewritten via dash.embed.rls.rewrite_sql() and
    rows are scrubbed post-fetch. Per-engine idempotent.

    When rewrite_sql() raises RLSDenied (unsafe SQL or missing claim) the
    statement is replaced with a stub that returns a single row containing
    a note string, so the agent receives a clear "policy restricted"
    response instead of an unhandled exception.
    """
    if engine is None or getattr(engine, "_embed_rls_attached", False):
        return

    @_sa_event.listens_for(engine, "before_cursor_execute", retval=True)
    def _embed_rls_listener(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        try:
            from dash.embed.rls import (
                EMBED_RLS_POLICIES as _POL,
                EMBED_CLAIMS as _CLAIMS,
                EMBED_RLS_AUDIT_CTX as _AUD,
                rewrite_sql as _rw,
                RLSDenied as _RLSDenied,
                audit_denial as _audit,
            )
            policies = _POL.get()
            if not policies:
                return statement, params
            claims = _CLAIMS.get() or {}
            audit_ctx = _AUD.get() or {}

            def _cb():
                _audit(
                    embed_id=audit_ctx.get("embed_id"),
                    session_token=audit_ctx.get("session_token"),
                    claims=claims,
                    table=None,
                    column=None,
                    action="rewrite_denied",
                    sql_snippet=statement,
                )

            try:
                new_sql = _rw(statement, policies, claims, audit_cb=_cb)
                return new_sql, params
            except _RLSDenied as e:
                import logging as _lg
                _lg.getLogger(__name__).warning("embed RLS denied: %s", e)
                note = "Access restricted by row-level policy."
                stub = "SELECT '" + note.replace("'", "''") + "'::text AS note"
                return stub, {}
        except Exception as e:
            # Fail-soft: never break the query because of guard internals.
            import logging as _lg
            _lg.getLogger(__name__).warning("embed RLS guard error (passthrough): %s", e)
            return statement, params

    try:
        engine._embed_rls_attached = True
    except Exception:
        pass


def build_analyst_tools(knowledge: Knowledge, user_id: str | None = None, project_slug: str | None = None) -> list:
    """Assemble tools for the Analyst agent.

    Read-only SQL enforced at the PostgreSQL level via
    ``default_transaction_read_only``. Any DML/DDL is rejected by the database.
    """
    if project_slug:
        from db import get_project_readonly_engine
        import re
        ro_engine = get_project_readonly_engine(project_slug)
        user_schema = re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]
    elif user_id:
        ro_engine = get_user_readonly_engine(user_id)
        user_schema = _sanitize_user_id(user_id)
    else:
        ro_engine = get_readonly_engine()
        user_schema = None

    # Create forecast tool with injected engine/schema
    from agno.tools import tool
    from dash.tools.skill_refinery import tracked as _tracked, apply_patch_to_description as _patch_desc

    _DESC_FORECAST = _patch_desc("run_forecast", "Run time series forecast using Prophet. Use when user asks to predict, forecast, or project future values. Args: table (str), date_column (str), value_column (str), periods (int, default 3)", project_slug)
    _DESC_ANOMALY = _patch_desc("detect_anomalies", "Detect outliers/anomalies in a numeric column using Z-score or IQR. Use for anomaly detection questions. Args: table (str), column (str), method (str, 'zscore' or 'iqr'), threshold (float, default 2.0)", project_slug)
    _DESC_PARETO = _patch_desc("run_pareto", "Run 80/20 Pareto analysis — find which categories drive most value. Args: table (str), category_col (str), value_col (str)", project_slug)
    _DESC_COMPARE = _patch_desc("compare_periods", "Compare current period vs previous period automatically. Args: table (str), date_col (str), value_col (str), group_col (str, optional)", project_slug)

    @tool(name="run_forecast", description=_DESC_FORECAST)
    def forecast_tool(table: str, date_column: str, value_column: str, periods: int = 3) -> str:
        return _tracked("run_forecast", run_forecast, table, date_column, value_column, periods,
                        agent="analyst", project_slug=project_slug,
                        _engine=ro_engine, _schema=user_schema or "public")

    @tool(name="detect_anomalies", description=_DESC_ANOMALY)
    def anomaly_tool(table: str, column: str, method: str = "zscore", threshold: float = 2.0) -> str:
        return _tracked("detect_anomalies", detect_anomalies, table, column, method, threshold,
                        agent="analyst", project_slug=project_slug,
                        _engine=ro_engine, _schema=user_schema or "public")

    @tool(name="run_pareto", description=_DESC_PARETO)
    def pareto_tool(table: str, category_col: str, value_col: str) -> str:
        return _tracked("run_pareto", run_pareto, table, category_col, value_col,
                        agent="analyst", project_slug=project_slug,
                        _engine=ro_engine, _schema=user_schema or "public")

    @tool(name="compare_periods", description=_DESC_COMPARE)
    def compare_tool(table: str, date_col: str, value_col: str, group_col: str = "") -> str:
        return _tracked("compare_periods", compare_periods, table, date_col, value_col, group_col,
                        agent="analyst", project_slug=project_slug,
                        _engine=ro_engine, _schema=user_schema or "public")

    @tool(name="root_cause_drill", description="Find which dimension drives a metric — automatic drill-down. Args: table (str), metric_col (str), dimension_cols (str, comma-separated)")
    def root_cause_tool(table: str, metric_col: str, dimension_cols: str) -> str:
        return root_cause_drill(table, metric_col, dimension_cols, _engine=ro_engine, _schema=user_schema or "public")

    @tool(name="scenario_model", description="What-if scenario analysis — recalculate totals with percentage change. Args: table (str), metric_col (str), change_pct (float), group_col (str, optional)")
    def scenario_tool(table: str, metric_col: str, change_pct: float, group_col: str = "") -> str:
        return scenario_model(table, metric_col, change_pct, group_col, _engine=ro_engine, _schema=user_schema or "public")

    @tool(name="benchmark_check", description="Compare actual values against a target/benchmark. Args: table (str), metric_col (str), target (float), group_col (str, optional)")
    def benchmark_tool(table: str, metric_col: str, target: float, group_col: str = "") -> str:
        return benchmark_check(table, metric_col, target, group_col, _engine=ro_engine, _schema=user_schema or "public")

    _DESC_CORR = _patch_desc("correlation_matrix", "Find correlations between numeric columns. Args: table (str), columns (str, comma-separated column names)", project_slug)

    @tool(name="correlation_matrix", description=_DESC_CORR)
    def correlation_tool(table: str, columns: str) -> str:
        return _tracked("correlation_matrix", correlation_matrix, table, columns,
                        agent="analyst", project_slug=project_slug,
                        _engine=ro_engine, _schema=user_schema or "public")

    # Detect live-source mode for this project
    live_mode = None
    if project_slug:
        try:
            from dash.tools.live_query import get_live_mode
            live_mode = get_live_mode(project_slug)
        except Exception:
            live_mode = None

    tools = []
    # ── Mode-aware tool registration ───────────────────────────────────────
    # In LIVE mode, every tool that runs SQL against the local synced schema
    # is excluded — those would return stale or empty data. Agent must use
    # query_live_source for everything (including schema introspection via
    # information_schema queries).
    # In HYBRID / SYNC, all local tools are registered.
    is_live_only = (live_mode == "live")

    from dash.tools.skill_refinery import patch_tool_object as _patch_obj
    from dash.feature_config import get_feature_config as _fc
    _cfg = _fc(project_slug)
    _tcfg = _cfg.get("tools", {})

    if not is_live_only and _tcfg.get("sql", True):
        _attach_rls_rewrite(ro_engine, project_slug)
        _attach_embed_rls(ro_engine)
        # RLS Layer 3: Postgres SET LOCAL session vars per transaction.
        if project_slug:
            try:
                from dash.rls.pg_session import attach_pg_rls_session as _rls_pg_attach
                _rls_pg_attach(ro_engine, project_slug)
            except Exception:
                pass
        tools.append(RLSAwareSQLTools(db_engine=ro_engine, project_slug=project_slug))
        introspect = create_introspect_schema_tool(db_url, engine=ro_engine, user_schema=user_schema)
        _patch_obj(introspect, "introspect_schema", project_slug)
        tools.append(introspect)
        if _tcfg.get("forecast", True):
            tools.append(forecast_tool)
        if _tcfg.get("anomaly", True):
            tools.append(anomaly_tool)
            tools.append(pareto_tool)
            tools.append(compare_tool)
            tools.append(root_cause_tool)
            tools.append(scenario_tool)
            tools.append(benchmark_tool)
            tools.append(correlation_tool)

        # ── Pharma knowledge graph (Apache AGE) ────────────────────────────
        # Drug-relationship traversal: substitutes (same generic), therapeutic
        # alternatives by indication, neighbourhood. Stock joined relationally.
        import os as _os
        if project_slug and _os.getenv("PHARMA_GRAPH_DISABLED") != "1":
            try:
                from dash.tools.pharma_graph_tool import (
                    find_substitutes as _pg_subs,
                    alternatives_for_indication as _pg_alt,
                    drug_relationships as _pg_rel,
                )
                import json as _pgj

                @tool(name="find_substitutes", description="Find substitute drugs (same generic molecule) for an out-of-stock article, with current stock. Args: article_code (int, optional), brand_name (str, optional), site_code (str, optional), in_stock_only (bool, optional). Uses the pharma knowledge graph (Apache AGE).")
                def _subs_tool(article_code: int = 0, brand_name: str = "", site_code: str = "", in_stock_only: bool = False) -> str:
                    return _pgj.dumps(_pg_subs(article_code, brand_name, site_code, in_stock_only))

                @tool(name="alternatives_for_indication", description="Find all articles that treat a given indication/condition, with stock. Args: indication (str), site_code (str, optional), in_stock_only (bool, optional). Uses the pharma knowledge graph.")
                def _alt_tool(indication: str = "", site_code: str = "", in_stock_only: bool = False) -> str:
                    return _pgj.dumps(_pg_alt(indication, site_code, in_stock_only))

                @tool(name="drug_relationships", description="Show the graph neighbourhood of one drug: generic, category, indications, compositions, substitutes. Args: article_code (int, optional), brand_name (str, optional).")
                def _rel_tool(article_code: int = 0, brand_name: str = "") -> str:
                    return _pgj.dumps(_pg_rel(article_code, brand_name))

                tools.append(_subs_tool)
                tools.append(_alt_tool)
                tools.append(_rel_tool)
                import logging as _pgl
                _pgl.getLogger(__name__).info("pharma graph tools enabled: +3 (find_substitutes, alternatives_for_indication, drug_relationships)")
            except Exception as _pge:
                import logging as _pgl
                _pgl.getLogger(__name__).warning(f"pharma graph tools not loaded: {_pge}")

    tools.append(create_save_validated_query_tool(knowledge))
    tools.append(ReasoningTools())

    # EDA drill-down tools — inspect_dimension / inspect_cross_dim / inspect_time.
    # Replaces raw SQL for "show me all values" / "cross-tab" / "monthly trend" Qs.
    # Fail-soft + gated by EDA_TOOLS_DISABLED=1.
    if project_slug:
        try:
            from dash.tools.eda_tools import create_eda_tools
            _eda = create_eda_tools(project_slug)
            if _eda:
                tools.extend(_eda)
        except Exception as _e:
            import logging as _eda_log
            _eda_log.getLogger(__name__).warning(f"eda_tools not registered: {_e}")

    # Analysis specialist tools — consolidated into ONE dispatcher (`analyze`).
    # OpenAI's #1 lesson: overlapping tools confuse the agent; fewer, well-scoped
    # tools win. The 11 underlying analyzers still live in analysis_types.py and
    # are routed to internally by `analyze(analysis_type=...)`.
    try:
        from dash.tools.analysis_types import analyze
        tools.append(analyze)
    except ImportError:
        # Fail-soft fallback: if the dispatcher can't import for any reason,
        # fall back to registering the 11 individual analyzers so the Analyst
        # still has analysis capability (reversible, no capability loss).
        try:
            from dash.tools.analysis_types import (
                comparator_analysis, diagnostic_analysis, narrator_analysis,
                validator_analysis, planner_analysis, trend_analysis,
                pareto_analysis, anomaly_analysis, benchmark_analysis,
                root_cause_analysis, prescriptive_analysis,
            )
            tools.extend([
                comparator_analysis, diagnostic_analysis, narrator_analysis,
                validator_analysis, planner_analysis, trend_analysis,
                pareto_analysis, anomaly_analysis, benchmark_analysis,
                root_cause_analysis, prescriptive_analysis,
            ])
        except ImportError:
            pass

    # Visualization agent tool
    if _tcfg.get("charts", True):
        try:
            from dash.tools.visualizer import auto_visualize
            tools.append(auto_visualize)
        except ImportError:
            pass

    # Context loader tool (on-demand deep context)
    try:
        from dash.tools.context_loader import load_context
        tools.append(load_context)
    except ImportError:
        pass

    # Apply Skill tool — execute proven SQL from skill library (Layer 15)
    try:
        from dash.tools.apply_skill import apply_skill
        tools.append(apply_skill)
    except ImportError:
        pass

    # Lifted Demo-OS tools: math + pii (non-HITL)
    try:
        from dash.tools.extended_tools import calculator, scan_for_pii
        tools.extend([calculator, scan_for_pii])
    except ImportError:
        pass

    # Unified semantic search (KB + Brain + KG + Facts with reranking)
    try:
        sa = create_search_all_tool(project_slug=project_slug)
        _patch_obj(sa, "search_all", project_slug)
        tools.append(sa)
    except ImportError:
        pass

    # Hybrid lookup — routes exact metrics (count/sum/total) to deterministic
    # SQL and meaning questions to semantic search. Guards counts against the
    # similarity-search miscount class. Fail-soft: never break tool assembly.
    try:
        from dash.tools.hybrid_lookup import create_hybrid_lookup_tool
        hl = create_hybrid_lookup_tool(project_slug=project_slug)
        _patch_obj(hl, "hybrid_lookup", project_slug)
        tools.append(hl)
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).debug(f"hybrid_lookup tool not loaded: {_e}")

    # CRM metric registry — definition-locked deterministic metrics. Forces the
    # agent to use the ONE canonical SQL per business metric instead of
    # re-deriving (and dropping) filters. Fixes the drop-off/contribution drift
    # class (benchmark 2026-05). Fail-soft: never break tool assembly.
    try:
        from dash.tools.crm_metrics import create_crm_metric_tool
        cm = create_crm_metric_tool(project_slug=project_slug)
        _patch_obj(cm, "crm_metric", project_slug)
        tools.append(cm)
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).debug(f"crm_metric tool not loaded: {_e}")

    # User-configurable metric registry (DB-backed, per-project). Generalizes
    # crm_metrics into definitions any project owner edits from the UI. Fail-soft.
    try:
        from dash.tools.metric_tool import create_metric_tool
        mt = create_metric_tool(project_slug=project_slug)
        _patch_obj(mt, "metric", project_slug)
        tools.append(mt)
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).debug(f"metric tool not loaded: {_e}")

    # Live remote-DB query (only when project has a source flagged mode=live)
    if project_slug:
        try:
            from dash.tools.live_query import create_live_query_tool, _resolve_live_source
            if _resolve_live_source(project_slug):
                tools.append(create_live_query_tool(project_slug))
        except Exception:
            pass

    # ML prediction tools (predict, feature_importance, detect_anomalies_ml, llm_predict)
    # now belong to Data Scientist agent — removed from Analyst.

    # Sim what-if tool removed — dash/tools/sim_tools.py deleted.

    # External connectors — opt-in per project via feature_config.tools.external_connectors.
    # Tool: query_connector(connection_name, sql) — RBAC-checked + audited.
    if _tcfg.get("external_connectors", False):
        try:
            from dash.tools.connector_query import query_connector
            tools.append(query_connector)
        except Exception as _e:
            import logging as _lg
            _lg.getLogger(__name__).debug(f"query_connector tool not loaded: {_e}")

    # SkillRefinery auto-track all Function/Tool objects in the list.
    from dash.tools.skill_refinery import auto_track_list as _sr_track
    _sr_track(tools, agent="analyst", project_slug=project_slug)
    sanitize_tool_schemas(tools)
    return tools


def sanitize_tool_schemas(tools: list) -> None:
    """Prune `required` to intersect with `properties` keys in every tool's
    JSON-Schema parameter spec.

    Gemini's strict validator rejects `required: ["x"]` when `properties.x`
    is absent, returning 400 INVALID_ARGUMENT and killing the whole agent
    run. Agno's auto-generated schemas occasionally drift (e.g. `*args`,
    kwargs-only signatures, runtime-injected params), so prune defensively.

    Idempotent; safe to call multiple times.
    """
    import logging
    log = logging.getLogger(__name__)

    def _walk(params):
        if not isinstance(params, dict):
            return
        props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
        req = params.get("required")
        if isinstance(req, list) and props:
            valid = [r for r in req if isinstance(r, str) and r in props]
            if valid != req:
                params["required"] = valid
        # Recurse into nested object schemas.
        for v in props.values():
            if isinstance(v, dict):
                _walk(v)
                items = v.get("items")
                if isinstance(items, dict):
                    _walk(items)

    fixed = 0
    for t in tools or []:
        # Direct Function object
        params = getattr(t, "parameters", None)
        if isinstance(params, dict):
            before = list(params.get("required") or [])
            _walk(params)
            if before != list(params.get("required") or []):
                fixed += 1
        # Toolkit with .functions dict[name -> Function]
        fns = getattr(t, "functions", None)
        if isinstance(fns, dict):
            for f in fns.values():
                fparams = getattr(f, "parameters", None)
                if isinstance(fparams, dict):
                    before = list(fparams.get("required") or [])
                    _walk(fparams)
                    if before != list(fparams.get("required") or []):
                        fixed += 1
    if fixed:
        log.info(f"sanitize_tool_schemas: pruned required[] in {fixed} tool(s)")


def build_engineer_tools(knowledge: Knowledge, user_id: str | None = None, project_slug: str | None = None, dashboard_user_id: int | None = None) -> list:
    """Assemble tools for the Engineer agent.

    Full SQL scoped to the user/project schema via search_path.
    """
    if project_slug:
        from db import get_project_engine
        import re
        eng = get_project_engine(project_slug)
        user_schema = re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]
    elif user_id:
        eng = get_user_engine(user_id)
        user_schema = _sanitize_user_id(user_id)
    else:
        eng = get_sql_engine()
        user_schema = "dash"

    # Live-mode awareness: drop local SQL tools when project is live-only.
    live_mode = None
    if project_slug:
        try:
            from dash.tools.live_query import get_live_mode
            live_mode = get_live_mode(project_slug)
        except Exception:
            live_mode = None
    is_live_only = (live_mode == "live")

    tools = [ReasoningTools(), create_update_knowledge_tool(knowledge)]
    if not is_live_only:
        _attach_rls_rewrite(eng, project_slug)
        _attach_embed_rls(eng)
        # RLS Layer 3: Postgres SET LOCAL session vars per transaction.
        if project_slug:
            try:
                from dash.rls.pg_session import attach_pg_rls_session as _rls_pg_attach
                _rls_pg_attach(eng, project_slug)
            except Exception:
                pass
        tools.insert(0, RLSAwareSQLTools(db_engine=eng, schema=user_schema, project_slug=project_slug))
        tools.insert(1, create_introspect_schema_tool(db_url, engine=eng, user_schema=user_schema))

    if project_slug:
        tools.append(create_dashboard_tool(project_slug, user_id=dashboard_user_id or 1))
        # Add live tool for engineer too in live/hybrid
        try:
            from dash.tools.live_query import create_live_query_tool, _resolve_live_source
            if _resolve_live_source(project_slug):
                tools.append(create_live_query_tool(project_slug))
        except Exception:
            pass

    # Lifted Demo-OS tools: reports + schedules
    try:
        from dash.tools.extended_tools import (
            generate_pdf, generate_pptx, generate_csv,
            create_schedule, list_schedules, delete_schedule, enable_schedule,
        )
        tools.extend([generate_pdf, generate_pptx, generate_csv,
                      create_schedule, list_schedules, delete_schedule, enable_schedule])
    except ImportError:
        pass

    # Phase 2 internal action execution (HITL-gated webhooks/APIs)
    try:
        from dash.tools.action_tools import request_action, execute_approved_action
        tools.extend([request_action, execute_approved_action])
    except ImportError:
        pass

    # External connectors — opt-in per project via feature_config.tools.external_connectors.
    try:
        from dash.feature_config import get_feature_config as _fc_eng
        _ext_on = bool(_fc_eng(project_slug).get("tools", {}).get("external_connectors", False))
    except Exception:
        _ext_on = False
    if _ext_on:
        try:
            from dash.tools.connector_query import query_connector
            tools.append(query_connector)
        except Exception as _e:
            import logging as _lg
            _lg.getLogger(__name__).debug(f"query_connector tool not loaded (engineer): {_e}")

    # Cowork office skills — opt-in per project via feature_config.tools.office_skills.
    # 15 tools: xlsx_recalc + deck visual QA + 6 PPTX edit + 3 DOCX edit + 4 PDF form.
    try:
        from dash.feature_config import get_feature_config as _fc_eng2
        _office_skills = bool(_fc_eng2(project_slug).get("tools", {}).get("office_skills", False))
    except Exception:
        _office_skills = False
    if _office_skills:
        try:
            from dash.tools.xlsx_recalc import xlsx_recalc
            from dash.tools.deck_visual_qa import generate_deck_thumbnail_grid
            from dash.tools.deck_edit import (
                pptx_extract_inventory, pptx_replace_text, pptx_rearrange_slides,
                pptx_ooxml_unpack, pptx_ooxml_pack, pptx_ooxml_validate,
            )
            from dash.tools.docx_edit import docx_unpack, docx_pack, docx_add_comment
            from dash.tools.pdf_form import (
                pdf_extract_form_fields, pdf_check_has_fillable,
                pdf_fill_fillable_fields, pdf_fill_with_annotations,
            )
            tools.extend([
                xlsx_recalc,
                generate_deck_thumbnail_grid,
                pptx_extract_inventory, pptx_replace_text, pptx_rearrange_slides,
                pptx_ooxml_unpack, pptx_ooxml_pack, pptx_ooxml_validate,
                docx_unpack, docx_pack, docx_add_comment,
                pdf_extract_form_fields, pdf_check_has_fillable,
                pdf_fill_fillable_fields, pdf_fill_with_annotations,
            ])
            import logging as _lg
            _lg.getLogger(__name__).info("office_skills enabled: +15 tools on Engineer")
        except Exception as _oe:
            import logging as _lg
            _lg.getLogger(__name__).warning(f"office_skills tools not loaded: {_oe}")

    from dash.tools.skill_refinery import auto_track_list as _sr_track
    _sr_track(tools, agent="engineer", project_slug=project_slug)
    sanitize_tool_schemas(tools)
    return tools
