"""
Self-Learning API
=================

DB-backed endpoints for memories, feedback, annotations, evals, query patterns, workflows.
All data stored in PostgreSQL for persistence and queryability.
"""

import concurrent.futures
import json
import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Body
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url
from dash.settings import TRAINING_MODEL, DEEP_MODEL, LITE_MODEL

router = APIRouter(prefix="/api/projects", tags=["Learning"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)
logger = logging.getLogger(__name__)


def _iso_utc(v):
    """Serialize a DB timestamp as ISO-8601 UTC ending in 'Z'.

    The DB stores NAIVE UTC; the frontend parses bare 'YYYY-MM-DD HH:MM:SS'
    strings as LOCAL time → phantom elapsed. Always emit an explicit 'Z'/offset.
    Handles None / datetime / str. Never throws. (Mirrors
    dash.training.flow_map._iso_utc — kept local to avoid import coupling.)"""
    if v is None:
        return None
    try:
        iso = getattr(v, "isoformat", None)
        s = v.isoformat() if callable(iso) else str(v)
        s = s.strip()
        if not s:
            return None
        s = s.replace(" ", "T", 1)
        if s.endswith("Z"):
            return s
        t_idx = s.find("T")
        time_part = s[t_idx + 1:] if t_idx >= 0 else s
        if "+" in time_part or "-" in time_part:
            return s
        return s + "Z"
    except Exception:
        try:
            return str(v) if v is not None else None
        except Exception:
            return None


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_access(user: dict, slug: str):
    """Verify user has access to project (owner, shared, or super admin)."""
    from app.auth import check_project_permission
    perm = check_project_permission(user, slug)
    if not perm:
        raise HTTPException(403, "Access denied")


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------

@router.get("/{slug}/memories")
def list_memories(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT en.id, en.scope, en.fact, en.source, en.created_at, my.fact AS fact_my
            FROM public.dash_memories en
            LEFT JOIN public.dash_memories my ON my.parent_id = en.id AND my.lang = 'my'
            WHERE ((en.project_slug = :s AND en.scope = 'project')
               OR (en.scope = 'global')
               OR (en.scope = 'personal' AND en.user_id = :uid AND en.project_slug = :s))
               AND (en.archived IS NULL OR en.archived = FALSE)
               AND (en.lang IS NULL OR en.lang = 'en')
            ORDER BY en.created_at DESC LIMIT 50
        """), {"s": slug, "uid": user["user_id"]}).fetchall()
    return {"memories": [{"id": r[0], "scope": r[1], "fact": r[2], "source": r[3], "created_at": str(r[4]) if r[4] else None, "fact_my": r[5]} for r in rows]}


@router.post("/{slug}/memories")
async def create_memory(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()
    fact = body.get("fact", "")
    scope = body.get("scope", "project")
    if not fact:
        raise HTTPException(400, "Fact required")
    if scope not in ("personal", "project", "global"):
        scope = "project"
    with _engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO public.dash_memories (user_id, project_slug, scope, fact, source) VALUES (:uid, :s, :scope, :fact, 'user')"
        ), {"uid": user["user_id"], "s": slug, "scope": scope, "fact": fact})
        conn.commit()
    return {"status": "ok"}


@router.delete("/{slug}/memories/{memory_id}")
def delete_memory(slug: str, memory_id: int, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        conn.execute(text("DELETE FROM public.dash_memories WHERE id = :id"), {"id": memory_id})
        conn.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

@router.get("/{slug}/feedback")
def list_feedback(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, question, answer, sql_query, rating, created_at FROM public.dash_feedback "
            "WHERE project_slug = :s ORDER BY created_at DESC LIMIT 30"
        ), {"s": slug}).fetchall()
    from dash.privacy import redact as _r, keywords as _kw
    return {"feedback": [{"id": r[0], "question": _r(r[1]), "answer": _r(r[2]), "keywords": _kw(r[1]),
                          "sql": r[3], "rating": r[4], "created_at": str(r[5]) if r[5] else None} for r in rows]}


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

@router.get("/{slug}/annotations")
def list_annotations(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, table_name, column_name, annotation, updated_by, updated_at FROM public.dash_annotations "
            "WHERE project_slug = :s ORDER BY table_name, column_name"
        ), {"s": slug}).fetchall()
    return {"annotations": [{"id": r[0], "table_name": r[1], "column_name": r[2], "annotation": r[3], "updated_by": r[4], "updated_at": str(r[5]) if r[5] else None} for r in rows]}


@router.put("/{slug}/annotations")
async def upsert_annotation(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()
    table_name = body.get("table_name", "")
    column_name = body.get("column_name", "")
    annotation = body.get("annotation", "")
    if not table_name or not column_name or not annotation:
        raise HTTPException(400, "table_name, column_name, annotation required")
    with _engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO public.dash_annotations (project_slug, table_name, column_name, annotation, updated_by)
            VALUES (:s, :t, :c, :a, :u)
            ON CONFLICT (project_slug, table_name, column_name)
            DO UPDATE SET annotation = :a, updated_by = :u, updated_at = NOW()
        """), {"s": slug, "t": table_name, "c": column_name, "a": annotation, "u": user["username"]})
        conn.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Query Patterns
# ---------------------------------------------------------------------------

@router.get("/{slug}/query-patterns")
def list_query_patterns(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT en.id, en.question, en.sql, en.uses, en.last_used, en.created_at, my.question AS question_my "
            "FROM public.dash_query_patterns en "
            "LEFT JOIN public.dash_query_patterns my ON my.project_slug = en.project_slug "
            "  AND my.source = 'bilingual_twin' "
            "  AND regexp_replace(my.sql, E'\\n-- bilingual_twin$', '') = en.sql "
            "WHERE en.project_slug = :s AND (en.source IS NULL OR en.source <> 'bilingual_twin') "
            "ORDER BY en.uses DESC LIMIT 20"
        ), {"s": slug}).fetchall()
    from dash.privacy import redact as _r, keywords as _kw
    return {"patterns": [{"id": r[0], "question": _r(r[1]), "keywords": _kw(r[1]), "sql": r[2], "uses": r[3], "last_used": str(r[4]) if r[4] else None, "created_at": str(r[5]) if r[5] else None, "question_my": _r(r[6])} for r in rows]}


# ---------------------------------------------------------------------------
# Evals
# ---------------------------------------------------------------------------

@router.get("/{slug}/evals")
def list_evals(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, question, expected_sql, last_result, last_score, last_run_at, created_at "
            "FROM public.dash_evals WHERE project_slug = :s ORDER BY created_at"
        ), {"s": slug}).fetchall()
    from dash.privacy import redact as _r, keywords as _kw
    return {"evals": [{"id": r[0], "question": _r(r[1]), "keywords": _kw(r[1]), "expected_sql": r[2], "last_result": _r(r[3]), "last_score": r[4], "last_run_at": str(r[5]) if r[5] else None, "created_at": str(r[6]) if r[6] else None} for r in rows]}


@router.post("/{slug}/evals")
async def create_eval(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()
    question = body.get("question", "")
    expected_sql = body.get("expected_sql", "")
    if not question or not expected_sql:
        raise HTTPException(400, "question and expected_sql required")
    with _engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO public.dash_evals (project_slug, question, expected_sql) VALUES (:s, :q, :sql)"
        ), {"s": slug, "q": question, "sql": expected_sql})
        conn.commit()
    return {"status": "ok"}


@router.delete("/{slug}/evals/{eval_id}")
def delete_eval(slug: str, eval_id: int, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        conn.execute(text("DELETE FROM public.dash_evals WHERE id = :id AND project_slug = :s"), {"id": eval_id, "s": slug})
        conn.commit()
    return {"status": "ok"}


def _run_evals_for_slug(slug: str) -> dict:
    """Run all evals for a project without auth/Request — safe to call from
    background threads (e.g. post-training auto-eval). Writes per-eval rows to
    public.dash_eval_history and a run-summary row to public.dash_eval_runs.

    Returns the same shape as the HTTP endpoint.
    """
    with _engine.connect() as conn:
        evals = conn.execute(text(
            "SELECT id, question, expected_sql FROM public.dash_evals WHERE project_slug = :s"
        ), {"s": slug}).fetchall()

    from db.session import get_project_readonly_engine
    proj_engine = get_project_readonly_engine(slug)

    from os import getenv
    import httpx
    api_key = getenv("OPENROUTER_API_KEY", "")

    def _run_one_eval(ev) -> dict:
        """Process a single eval: run expected SQL, LLM-generate SQL, execute,
        grade, persist the per-eval history rows. Returns the result dict.

        Runs in a worker thread. Opens its own DB connections (SQLAlchemy
        engine .connect() is thread-safe to call concurrently). Fully
        fail-soft: any unexpected error records this eval as ERROR/FAIL and
        never propagates so one bad eval can't kill the batch.
        """
        eval_id, question, expected_sql = ev[0], ev[1], ev[2]
        generated_sql = ""
        expected_rows = []
        generated_rows = []
        score = "FAIL"
        reasoning = ""
        result_str = ""

        try:
            try:
                # Step 1: Run expected SQL to get expected results
                with proj_engine.connect() as conn:
                    expected_result = conn.execute(text(expected_sql)).fetchall()
                    expected_rows = [list(r) for r in expected_result[:10]]
                    expected_cols = list(expected_result[0]._fields) if expected_result else []
            except Exception as e:
                score = "ERROR"
                reasoning = f"Expected SQL failed: {str(e)[:200]}"
                result_str = reasoning

            if score != "ERROR":
                # Step 2: Ask LLM to generate SQL from the question (simulating agent)
                if api_key:
                    try:
                        # Get table metadata for context
                        tables_info = ""
                        try:
                            from sqlalchemy import inspect as sa_inspect
                            insp = sa_inspect(proj_engine)
                            import re
                            schema = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]
                            for tbl in insp.get_table_names(schema=schema):
                                cols = insp.get_columns(tbl, schema=schema)
                                col_list = ", ".join(f"{c['name']} ({str(c['type'])})" for c in cols[:15])
                                tables_info += f"- {schema}.{tbl}: {col_list}\n"
                        except Exception:
                            pass

                        gen_prompt = f"""Generate a SQL query to answer this question.
Tables available:
{tables_info}

Question: {question}

Return ONLY the SQL query, nothing else. No markdown, no explanation."""

                        resp = httpx.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={"model": TRAINING_MODEL, "messages": [{"role": "user", "content": gen_prompt}], "max_tokens": 500, "temperature": 0.1},
                            timeout=15,
                        )
                        gen_result = resp.json()
                        generated_sql = gen_result.get("choices", [{}])[0].get("message", {}).get("content", "").strip().strip("`").strip()
                        if generated_sql.lower().startswith("sql"):
                            generated_sql = generated_sql[3:].strip()
                    except Exception:
                        generated_sql = ""

                # Step 3: Run generated SQL
                if generated_sql:
                    # Strip SQL markdown fences + add LIMIT 1000 guard
                    import re as _re
                    generated_sql = _re.sub(r'^```sql\s*|^```\s*|```$', '', generated_sql.strip(), flags=_re.MULTILINE).strip()
                    if generated_sql.rstrip(";").lower().lstrip().startswith("select") and " limit " not in generated_sql.lower():
                        generated_sql = generated_sql.rstrip(";").rstrip() + " LIMIT 1000"
                    try:
                        with proj_engine.connect() as conn:
                            gen_result = conn.execute(text(generated_sql)).fetchall()
                            generated_rows = [list(r) for r in gen_result[:10]]
                            gen_cols = list(gen_result[0]._fields) if gen_result else []
                    except Exception as e:
                        generated_rows = []
                        reasoning = f"Generated SQL failed: {str(e)[:150]}"

                # Step 4: Compare results + grade with LLM
                if api_key and (generated_rows or expected_rows):
                    try:
                        grade_prompt = f"""Compare these two SQL query results for the question: "{question}"

EXPECTED SQL: {expected_sql}
EXPECTED RESULTS (first 5 rows): {str(expected_rows[:5])}

GENERATED SQL: {generated_sql or 'FAILED TO GENERATE'}
GENERATED RESULTS (first 5 rows): {str(generated_rows[:5])}

Grade the generated result:
- Do the results match? (exact match, partial match, or no match)
- Is the generated SQL logically equivalent?
- Score: 1-5 (5 = perfect match, 1 = completely wrong)

Return ONLY valid JSON:
{{"score": 4, "match": "exact|partial|none", "reasoning": "brief explanation"}}"""

                        resp = httpx.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={"model": TRAINING_MODEL, "messages": [{"role": "user", "content": grade_prompt}], "max_tokens": 200, "temperature": 0.1},
                            timeout=15,
                        )
                        grade_result = resp.json()
                        grade_content = grade_result.get("choices", [{}])[0].get("message", {}).get("content", "")
                        import json as _json
                        grade = _json.loads(grade_content.strip().strip("`").strip())
                        numeric_score = grade.get("score", 0)
                        match_type = grade.get("match", "none")
                        reasoning = grade.get("reasoning", "")
                        score = "PASS" if numeric_score >= 4 else ("PARTIAL" if numeric_score >= 2 else "FAIL")
                        result_str = f"Score: {numeric_score}/5 | Match: {match_type} | {reasoning}"
                    except Exception:
                        # Fallback: simple row count comparison
                        if len(generated_rows) == len(expected_rows) and len(expected_rows) > 0:
                            score = "PASS"
                            result_str = f"Row count match: {len(expected_rows)} rows"
                        elif len(generated_rows) > 0:
                            score = "PARTIAL"
                            result_str = f"Expected {len(expected_rows)} rows, got {len(generated_rows)}"
                        else:
                            score = "FAIL"
                            result_str = "Generated SQL returned no results"
                elif not api_key:
                    # No LLM — just check if expected SQL works
                    score = "PASS" if expected_rows else "FAIL"
                    result_str = f"Expected SQL returned {len(expected_rows)} rows (no LLM grading)"
        except Exception as e:
            # Catch-all: one eval erroring must not kill the batch.
            score = "ERROR"
            result_str = f"Eval crashed: {str(e)[:200]}"

        # Update eval in DB + save history (own connection — thread-safe)
        try:
            with _engine.connect() as conn:
                conn.execute(text(
                    "UPDATE public.dash_evals SET last_result = :r, last_score = :score, last_run_at = NOW() WHERE id = :id"
                ), {"r": result_str[:500], "score": score, "id": eval_id})
                conn.execute(text(
                    "INSERT INTO public.dash_eval_history (project_slug, eval_id, score, result) "
                    "VALUES (:slug, :eid, :score, :result)"
                ), {"slug": slug, "eid": eval_id, "score": score, "result": result_str[:500]})
                conn.commit()
        except Exception as e:
            logger.warning(f"eval history write failed [{slug}] id={eval_id}: {str(e)[:120]}")

        return {
            "id": eval_id, "question": question, "score": score,
            "generated_sql": generated_sql[:300] if generated_sql else None,
            "reasoning": result_str
        }

    # Run all evals in parallel (each makes its own LLM calls + DB connections).
    results = []
    if evals:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_run_one_eval, ev) for ev in evals]
            for fut in futures:
                try:
                    results.append(fut.result())
                except Exception as e:
                    # _run_one_eval is fail-soft, but guard anyway so the batch never raises.
                    logger.warning(f"eval task raised [{slug}]: {str(e)[:120]}")
                    results.append({"id": None, "question": "", "score": "ERROR",
                                    "generated_sql": None, "reasoning": f"task crashed: {str(e)[:150]}"})

    passed = sum(1 for r in results if r["score"] == "PASS")
    partial = sum(1 for r in results if r["score"] == "PARTIAL")

    # Log failures for debugging
    failed_evals = [r for r in results if r["score"] in ("FAIL", "ERROR")]
    for ev in failed_evals[:10]:
        logger.warning(
            f"eval FAIL [{slug}]: {str(ev.get('question',''))[:80]} | "
            f"reason: {str(ev.get('reasoning',''))[:120]}"
        )

    # Save run summary
    with _engine.connect() as conn:
        avg = sum(1 for r in results if r["score"] == "PASS") * 5 + sum(1 for r in results if r["score"] == "PARTIAL") * 3 + sum(1 for r in results if r["score"] == "FAIL")
        avg_score = avg / len(results) if results else 0
        conn.execute(text(
            "INSERT INTO public.dash_eval_runs (project_slug, total, passed, partial, failed, average_score) "
            "VALUES (:s, :total, :passed, :partial, :failed, :avg)"
        ), {"s": slug, "total": len(results), "passed": passed, "partial": partial,
            "failed": len(results) - passed - partial, "avg": round(avg_score, 1)})
        conn.commit()

    return {"results": results, "total": len(results), "passed": passed, "partial": partial}


@router.post("/{slug}/evals/run")
async def run_evals(slug: str, request: Request):
    """Run all evals — full pipeline: generate SQL via agent, compare results, grade with LLM."""
    user = _get_user(request)
    _check_access(user, slug)
    return _run_evals_for_slug(slug)


# ---------------------------------------------------------------------------
# Natural Language → SQL Rules
# ---------------------------------------------------------------------------

@router.post("/{slug}/nl-to-rule")
async def nl_to_rule(slug: str, request: Request):
    """Convert natural language rule to SQL constraint + auto-create eval."""
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()
    rule_text = body.get("rule", "")
    if not rule_text:
        from fastapi import HTTPException
        raise HTTPException(400, "Rule text required")

    from os import getenv
    import httpx
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"status": "skip"}

    # Get table info for context
    tables_info = ""
    try:
        anns = _engine.connect().execute(text(
            "SELECT table_name, column_name FROM public.dash_annotations WHERE project_slug = :s"
        ), {"s": slug}).fetchall()
        tables_info = ", ".join(f"{r[0]}.{r[1]}" for r in anns) if anns else ""
    except Exception:
        pass

    prompt = f"""Convert this business rule into a SQL constraint.

Rule: "{rule_text}"
Available columns: {tables_info or 'unknown'}

Return ONLY valid JSON (no markdown):
{{"name": "Short rule name", "definition": "The rule in plain English", "sql_constraint": "SQL WHERE clause or expression", "test_question": "A question to verify this rule works", "test_sql": "SQL to verify the rule"}}"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": TRAINING_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 300, "temperature": 0.1},
            timeout=10,
        )
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content.strip().strip("`").strip())

        # Save as rule
        import time as _t
        rule_id = f"rule_nl_{int(_t.time() * 1000)}"
        with _engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_rules_db (project_slug, rule_id, name, type, definition, source) VALUES (:s, :rid, :n, 'business_rule', :d, 'nl_converted')"
            ), {"s": slug, "rid": rule_id, "n": parsed.get("name", rule_text[:50]), "d": parsed.get("definition", rule_text)})

            # Auto-create eval
            if parsed.get("test_question") and parsed.get("test_sql"):
                conn.execute(text(
                    "INSERT INTO public.dash_evals (project_slug, question, expected_sql) VALUES (:s, :q, :sql)"
                ), {"s": slug, "q": parsed["test_question"], "sql": parsed["test_sql"]})

            conn.commit()

        return {"status": "ok", "rule": parsed}
    except Exception:
        return {"status": "error"}


# ---------------------------------------------------------------------------
# Data Quality Check
# ---------------------------------------------------------------------------

@router.post("/{slug}/quality-check")
def run_quality_check(slug: str, request: Request):
    """Run a data quality check on all project tables."""
    user = _get_user(request)
    _check_access(user, slug)

    from db.session import get_project_readonly_engine
    from sqlalchemy import inspect as sa_inspect

    engine = get_project_readonly_engine(slug)
    from db.session import create_project_schema
    schema = create_project_schema(slug)
    insp = sa_inspect(engine)

    issues = []
    try:
        tables = insp.get_table_names(schema=schema)
        for tbl in tables:
            with engine.connect() as conn:
                # Check NULLs
                cols = insp.get_columns(tbl, schema=schema)
                row_count = conn.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{tbl}"')).scalar() or 0
                for col in cols:
                    try:
                        null_count = conn.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{tbl}" WHERE "{col["name"]}" IS NULL')).scalar() or 0
                        null_pct = round((null_count / max(row_count, 1)) * 100, 1)
                        if null_pct > 20:
                            issues.append({"table": tbl, "column": col["name"], "issue": f"{null_pct}% NULL values", "severity": "warning" if null_pct < 50 else "critical"})
                    except Exception:
                        pass

                # Check empty table
                if row_count == 0:
                    issues.append({"table": tbl, "issue": "Table is empty", "severity": "critical"})
    except Exception:
        pass

    return {"issues": issues, "tables_checked": len(insp.get_table_names(schema=schema)) if schema else 0}


# ---------------------------------------------------------------------------
# Training Runs + Drift + Relationships
# ---------------------------------------------------------------------------

@router.get("/{slug}/training-runs")
def list_training_runs(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, tables_trained, status, steps, error, started_at, finished_at, logs "
            "FROM public.dash_training_runs WHERE project_slug = :s ORDER BY started_at DESC LIMIT 10"
        ), {"s": slug}).fetchall()
    return {"runs": [{"id": r[0], "tables": r[1], "status": r[2], "steps": r[3], "error": r[4],
                      "started_at": str(r[5]) if r[5] else None, "finished_at": str(r[6]) if r[6] else None,
                      "logs": r[7] if r[7] else []} for r in rows]}


def _reap_stale_runs(slug: str) -> int:
    """Watchdog: fail any run stuck 'running'/'finalizing' with no log progress
    for > STALE_RUN_MINUTES (default 12). A hung step (e.g. an un-timed-out SQL
    verify) otherwise leaves the run 'running' forever and the UI spinner never
    stops. Uses the newest log entry's tsabs as the liveness signal, falling
    back to started_at when a run has logged nothing yet. Cheap; fail-soft.
    The 12-min default clears the ~4-5 min silent gap while catalog vectors
    embed during 'finalizing'. Returns rows reaped."""
    import os as _os
    try:
        stale_min = max(2, int(_os.getenv("STALE_RUN_MINUTES", "12")))
    except (TypeError, ValueError):
        stale_min = 12
    try:
        with _engine.connect() as conn:
            res = conn.execute(text(
                "UPDATE public.dash_training_runs "
                "SET status='failed', finished_at=now(), "
                "    current_step = left(COALESCE(NULLIF(current_step,''),'')"
                "                   || ' · aborted (stale: no progress > ' || :m || ' min)', 200) "
                "WHERE project_slug = :s AND status IN ('running','finalizing') "
                "  AND COALESCE( "
                "        (SELECT max((e->>'tsabs')::float) FROM jsonb_array_elements(logs) e), "
                "        EXTRACT(EPOCH FROM started_at) "
                "      ) < EXTRACT(EPOCH FROM now()) - (:m * 60)"
            ), {"s": slug, "m": stale_min})
            conn.commit()
            return res.rowcount or 0
    except Exception:
        return 0


@router.get("/{slug}/auto-train/status")
def get_auto_train_status(slug: str, request: Request):
    """Return auto-train daemon status + recent training runs for this project."""
    _get_user(request)
    # Watchdog sweep on every poll — self-heals a hung run so the UI spinner
    # can never spin forever (see _reap_stale_runs).
    _reap_stale_runs(slug)
    try:
        from dash.cron.auto_train_daemon import get_daemon_status
        daemon = get_daemon_status()
    except Exception:
        daemon = {"enabled": False, "error": "daemon not loaded"}

    # Get recent training runs
    try:
        with _engine.connect() as conn:
            runs = conn.execute(text(
                "SELECT id, status, started_at, finished_at, "
                "EXTRACT(EPOCH FROM (finished_at - started_at)) as duration_sec "
                "FROM public.dash_training_runs WHERE project_slug = :s "
                "ORDER BY started_at DESC LIMIT 5"
            ), {"s": slug}).fetchall()
        recent_runs = [{"id": r[0], "status": r[1],
                        "started_at": _iso_utc(r[2]),
                        "finished_at": _iso_utc(r[3]),
                        "duration_sec": round(r[4]) if r[4] else None}
                       for r in runs]
    except Exception:
        recent_runs = []

    # Check if currently training
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, status, started_at, current_step FROM public.dash_training_runs "
                "WHERE project_slug = :s AND status IN ('running','queued','finalizing') "
                "ORDER BY started_at DESC LIMIT 1"
            ), {"s": slug}).fetchone()
        active_run = {"id": row[0], "status": row[1], "started_at": _iso_utc(row[2]),
                      "current_step": row[3] or ""} if row else None
    except Exception:
        active_run = None

    # Friendly one-line summary for the floating robot callout: what it's doing now,
    # or when it last trained + why (status + duration).
    if active_run:
        callout = f"Training · {active_run['current_step'] or 'working'}"
    elif recent_runs:
        lr = recent_runs[0]
        _dur = f" · {lr['duration_sec']}s" if lr.get("duration_sec") else ""
        callout = f"Last trained{_dur} · {lr.get('status') or 'done'}"
    else:
        callout = "Watching for new data"

    # Derive robot accessory task tag.
    try:
        _is_training = active_run is not None
        if daemon.get("enabled") is False and not _is_training:
            task = "paused"
        elif not _is_training:
            task = "idle"
        else:
            _step = (active_run.get("current_step") or "").lower()
            if "embed" in _step or "vector" in _step:
                task = "embedding"
            elif "index" in _step or "finaliz" in _step or "knowledge" in _step:
                task = "indexing"
            elif "eval" in _step:
                task = "eval"
            else:
                task = "training"
    except Exception:
        task = "idle"

    # Derive attention badge count (cheap, no extra queries).
    try:
        attention = 1 if recent_runs and recent_runs[0].get("status") in ("failed", "error") else 0
    except Exception:
        attention = 0

    return {
        "daemon": daemon,
        "active_run": active_run,
        "recent_runs": recent_runs,
        "is_training": active_run is not None,
        "current_step": active_run["current_step"] if active_run else "",
        "last_run": recent_runs[0] if recent_runs else None,
        "callout": callout,
        "task": task,
        "attention": attention,
    }


@router.get("/{slug}/auto-train/log")
def get_auto_train_log(slug: str, request: Request, since: int = 0, limit: int = 400):
    """Stream the live per-step training log into the robot panel.

    Source = public.dash_training_runs.logs (JSONB array of
    {ts, msg, table, table_index, total_tables}) appended by _master_log +
    the LLM observer on every training step / model call. `since` is the
    array index already seen by the client → we return only newer entries.
    Falls back to the latest run when no active run, so a just-finished
    run still shows its tail (incl. the ━━━ training done ━━━ line)."""
    _get_user(request)
    try:
        with _engine.connect() as conn:
            # Prefer the active (running/queued) run; else most-recent run.
            row = conn.execute(text(
                "SELECT id, status, current_step, "
                "COALESCE(jsonb_array_length(logs), 0) AS n "
                "FROM public.dash_training_runs WHERE project_slug = :s "
                "ORDER BY (status IN ('running','queued','finalizing')) DESC, started_at DESC "
                "LIMIT 1"
            ), {"s": slug}).fetchone()
            if not row:
                return {"run_id": None, "status": "idle", "current_step": "",
                        "total": 0, "events": []}
            run_id, status, current_step, n = row[0], row[1], row[2], int(row[3] or 0)
            start = max(0, int(since))
            events = []
            if n > start:
                # Slice the JSONB array tail in the DB (0-based, exclusive end).
                slc = conn.execute(text(
                    "SELECT idx - 1 AS i, e->>'ts' AS ts, e->>'msg' AS msg, "
                    "e->>'table' AS tbl, e->>'tsabs' AS tsabs "
                    "FROM public.dash_training_runs, "
                    "jsonb_array_elements(logs) WITH ORDINALITY AS t(e, idx) "
                    "WHERE id = :id AND idx > :start "
                    "ORDER BY idx LIMIT :lim"
                ), {"id": run_id, "start": start, "lim": max(1, int(limit))}).fetchall()
                for r in slc:
                    events.append({"i": int(r[0]), "ts": r[1] or "",
                                   "msg": r[2] or "", "table": r[3] or "",
                                   "tsabs": float(r[4]) if r[4] else 0})
            return {"run_id": run_id, "status": status,
                    "current_step": current_step or "", "total": n,
                    "events": events}
    except Exception:
        return {"run_id": None, "status": "idle", "current_step": "",
                "total": 0, "events": []}


@router.get("/{slug}/auto-train/log-history")
def get_auto_train_log_history(slug: str, request: Request, runs: int = 50):
    """Full retained training-log history, newest run first. Logs are NEVER
    deleted — every dash_training_runs row keeps its full JSONB `logs` array
    forever. The frontend partitions these by local date (using each event's
    absolute `tsabs` epoch) and renders timestamps in the machine timezone.

    `runs` caps how many recent runs to return (payload guard), not how many
    are retained. Default 50."""
    _get_user(request)
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, status, "
                "EXTRACT(EPOCH FROM started_at)::double precision AS started_epoch, "
                "COALESCE(jsonb_array_length(logs), 0) AS n "
                "FROM public.dash_training_runs WHERE project_slug = :s "
                "ORDER BY started_at DESC LIMIT :lim"
            ), {"s": slug, "lim": max(1, min(int(runs), 500))}).fetchall()
            out = []
            for rr in rows:
                run_id = rr[0]
                ev = conn.execute(text(
                    "SELECT idx - 1 AS i, e->>'ts' AS ts, e->>'msg' AS msg, "
                    "e->>'table' AS tbl, e->>'tsabs' AS tsabs "
                    "FROM public.dash_training_runs, "
                    "jsonb_array_elements(logs) WITH ORDINALITY AS t(e, idx) "
                    "WHERE id = :id ORDER BY idx"
                ), {"id": run_id}).fetchall()
                out.append({
                    "run_id": run_id,
                    "status": rr[1],
                    "started_epoch": float(rr[2]) if rr[2] else 0,
                    "total": int(rr[3] or 0),
                    "events": [{"i": int(e[0]), "ts": e[1] or "", "msg": e[2] or "",
                                "table": e[3] or "", "tsabs": float(e[4]) if e[4] else 0}
                               for e in ev],
                })
            return {"runs": out}
    except Exception:
        return {"runs": []}


@router.get("/{slug}/learning-feed")
def get_learning_feed(slug: str, request: Request, since: float = 0.0, limit: int = 50):
    """Return recent learning events for the robot panel log feed.
    Aggregates: memories, quality scores, proactive insights, KG triples, auto-evolve."""
    _get_user(request)
    events = []
    try:
        eng = get_sql_engine()
        since_ts = since if since > 0 else (__import__("time").time() - 3600)  # default last 1h

        # Each source gets its OWN short connection. A single bad query (schema
        # drift, missing table) must NOT abort the Postgres tx and silently nuke
        # every later source — which is exactly what happened when this read a
        # non-existent dash_memories.content column and the whole feed went blank.
        def _q(sql: str, params: dict):
            try:
                with eng.connect() as c:
                    return c.execute(text(sql), params).fetchall()
            except Exception:
                return []

        def _pct(v) -> str:
            try:
                if v is None:
                    return ""
                f = float(v)
                p = int(round(f * 100)) if f <= 1 else int(round(f))
                return f" · {max(0, min(100, p))}%"
            except Exception:
                return ""

        # Recent memories saved (from chat background agents). Column is `fact`.
        for r in _q(
            "SELECT EXTRACT(EPOCH FROM created_at)::bigint, source, fact, confidence_score "
            "FROM public.dash_memories WHERE project_slug=:s "
            "AND EXTRACT(EPOCH FROM created_at) > :since "
            "ORDER BY created_at DESC LIMIT :lim",
            {"s": slug, "since": since_ts, "lim": 20}):
            src = r[1] or "agent"
            label = {"auto_learned": "💡 Learned", "episodic": "📌 Episode",
                     "agent": "🧠 Memory", "user": "👤 Saved"}.get(src, "🧠 Memory")
            events.append({"ts": int(r[0]), "type": "memory",
                           "text": f"{label}: {str(r[2] or '')[:120]}{_pct(r[3])}"})

        # Quality scores
        for r in _q(
            "SELECT EXTRACT(EPOCH FROM created_at)::bigint, score, category "
            "FROM public.dash_quality_scores WHERE project_slug=:s "
            "AND EXTRACT(EPOCH FROM created_at) > :since "
            "ORDER BY created_at DESC LIMIT 10",
            {"s": slug, "since": since_ts}):
            events.append({"ts": int(r[0]), "type": "quality",
                           "text": f"✓ Quality score {r[1]}/5 — {r[2] or 'response'}"})

        # Proactive insights
        for r in _q(
            "SELECT EXTRACT(EPOCH FROM created_at)::bigint, title, severity "
            "FROM public.dash_proactive_insights WHERE project_slug=:s "
            "AND EXTRACT(EPOCH FROM created_at) > :since "
            "ORDER BY created_at DESC LIMIT 10",
            {"s": slug, "since": since_ts}):
            icon = "⚠️" if (r[2] or "info") == "alert" else "💡"
            events.append({"ts": int(r[0]), "type": "insight",
                           "text": f"{icon} Insight: {str(r[1] or '')[:100]}"})

        # KG triples added from chat
        for r in _q(
            "SELECT EXTRACT(EPOCH FROM created_at)::bigint, subject, predicate, object "
            "FROM public.dash_knowledge_triples WHERE project_slug=:s "
            "AND EXTRACT(EPOCH FROM created_at) > :since "
            "ORDER BY created_at DESC LIMIT 10",
            {"s": slug, "since": since_ts}):
            events.append({"ts": int(r[0]), "type": "triple",
                           "text": f"⬡ KG: {r[1]} → {r[2]} → {r[3]}"})

        # Evolved instructions
        for r in _q(
            "SELECT EXTRACT(EPOCH FROM updated_at)::bigint, version, trigger_count "
            "FROM public.dash_evolved_instructions WHERE project_slug=:s "
            "AND EXTRACT(EPOCH FROM updated_at) > :since "
            "ORDER BY updated_at DESC LIMIT 3",
            {"s": slug, "since": since_ts}):
            events.append({"ts": int(r[0]), "type": "evolve",
                           "text": f"🔄 Auto-evolved instructions v{r[1]} (after {r[2]} chats)"})

    except Exception:
        pass  # fail-soft — return whatever we got

    # Sort by timestamp desc, cap at limit
    events.sort(key=lambda x: x["ts"], reverse=True)
    return {"events": events[:limit], "count": len(events)}


@router.get("/{slug}/drift-alerts")
def list_drift_alerts(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, table_name, alerts, created_at FROM public.dash_drift_alerts "
            "WHERE project_slug = :s ORDER BY created_at DESC LIMIT 20"
        ), {"s": slug}).fetchall()
    alerts = []
    for r in rows:
        a = r[2] if isinstance(r[2], list) else json.loads(r[2]) if r[2] else []
        alerts.append({"id": r[0], "table_name": r[1], "alerts": a, "created_at": str(r[3]) if r[3] else None})
    return {"drift_alerts": alerts}


@router.get("/{slug}/relationships")
def list_relationships(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, from_table, from_column, to_table, to_column, rel_type, confidence, source "
            "FROM public.dash_relationships WHERE project_slug = :s ORDER BY confidence DESC"
        ), {"s": slug}).fetchall()
    # Add knowledge graph triples
    kg_triples = []
    try:
        with _engine.connect() as conn2:
            kg_rows = conn2.execute(text(
                "SELECT subject, predicate, object, source_type, source_id, confidence, inferred, community "
                "FROM public.dash_knowledge_triples WHERE project_slug = :slug ORDER BY confidence DESC LIMIT 200"
            ), {"slug": slug}).fetchall()
            kg_triples = [{"subject": r[0], "predicate": r[1], "object": r[2], "source_type": r[3],
                           "source_id": r[4], "confidence": float(r[5]) if r[5] else None, "inferred": r[6], "community": r[7]} for r in kg_rows]
    except Exception:
        pass

    return {"relationships": [{"id": r[0], "from_table": r[1], "from_column": r[2], "to_table": r[3],
                               "to_column": r[4], "type": r[5], "confidence": r[6], "source": r[7]} for r in rows],
            "knowledge_graph": kg_triples}


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@router.get("/{slug}/agents")
def list_agents(slug: str, request: Request):
    """
    Return the agent team configuration for this project.

    4-state model (industry-standard tri-state + error):
      - active       : prerequisites met AND called within 7 days
      - ready        : prerequisites met, never called (default for new project)
      - needs_setup  : prerequisite missing (e.g. Researcher with 0 docs)
      - error        : last invocation failed within last hour

    Backward-compat: every agent also has `status` field returning
    "active" or "standby" (legacy renderers keep working).

    Each agent also returns:
      - reason   : one-sentence why this state
      - cta      : optional {label, url} for needs_setup states
      - last_used_at : ISO timestamp or null
    """
    user = _get_user(request)
    _check_access(user, slug)

    from dash.paths import KNOWLEDGE_DIR
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    from db import db_url

    has_docs = bool(
        (KNOWLEDGE_DIR / slug / "docs").exists()
        and list((KNOWLEDGE_DIR / slug / "docs").iterdir())
    )

    # Detect tables directly from project DB schema. Knowledge JSON cache only
    # populates after TRAIN ALL — a fresh-seeded project has DB tables but no
    # knowledge JSONs, so checking the cache would falsely mark Analyst as
    # needs_setup. Source of truth = information_schema.
    has_tables = False
    has_customer_data = False
    try:
        _eng_check = create_engine(db_url, poolclass=NullPool)
        with _eng_check.connect() as _c:
            _names = [r[0].lower() for r in _c.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
            ), {"s": slug}).fetchall()]
            has_tables = len(_names) > 0
            for _n in _names:
                if "customer" in _n or "transaction" in _n or "order" in _n or "sale" in _n:
                    has_customer_data = True
                    break
        _eng_check.dispose()
    except Exception:
        pass

    # Recency map: agent_name -> ISO last_used. Best-effort; missing table = empty.
    recent: dict[str, str] = {}
    try:
        eng = create_engine(db_url, poolclass=NullPool)
        with eng.connect() as conn:
            # Heuristic: any row in dash_quality_scores within 7d signals chat activity
            row = conn.execute(text(
                "SELECT MAX(created_at) FROM public.dash_quality_scores "
                "WHERE project_slug = :s"
            ), {"s": slug}).scalar()
            if row:
                recent["__chat__"] = row.isoformat() if hasattr(row, "isoformat") else str(row)
        eng.dispose()
    except Exception:
        pass

    chat_ts = recent.get("__chat__")
    chat_recent = False
    if chat_ts:
        try:
            ts = datetime.fromisoformat(chat_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            chat_recent = (datetime.now(timezone.utc) - ts) < timedelta(days=7)
        except Exception:
            pass

    def state_for(prereq_ok: bool, prereq_label: str, cta_url: str | None,
                  always_active: bool = False) -> dict:
        if not prereq_ok and not always_active:
            return {
                "state": "needs_setup",
                "status": "standby",
                "reason": prereq_label,
                "cta": ({"label": "SET UP", "url": cta_url} if cta_url else None),
                "last_used_at": chat_ts,
            }
        if always_active or chat_recent:
            return {
                "state": "active",
                "status": "active",
                "reason": "Healthy. Recently invoked." if chat_recent else "Always-on coordinator.",
                "cta": None,
                "last_used_at": chat_ts,
            }
        return {
            "state": "ready",
            "status": "active",
            "reason": "Available. Will fire when triggered.",
            "cta": None,
            "last_used_at": chat_ts,
        }

    # Core team
    agents = [
        {
            "name": "Leader",
            "category": "core",
            "role": "routes requests · synthesizes answers · no DB access",
            "type": "coordinator", "tools": 0,
            **state_for(True, "", None, always_active=True),
        },
        {
            "name": "Analyst",
            "category": "core",
            "role": "READ-ONLY · SQL · reasoning · 23 tools",
            "type": "member", "tools": 23,
            **state_for(
                has_tables,
                "Upload data or connect a database to enable.",
                f"/ui/project/{slug}/settings#datasets",
            ),
        },
        {
            "name": "Engineer",
            "category": "core",
            "role": "WRITE · views · computed data · schema updates",
            "type": "member", "tools": 5,
            **state_for(
                has_tables,
                "Needs at least one table. Upload data first.",
                f"/ui/project/{slug}/settings#datasets",
            ),
        },
        {
            "name": "Researcher",
            "category": "core",
            "role": "document RAG · PPTX/PDF/DOCX · grounded facts",
            "type": "member", "tools": 0,
            **state_for(
                has_docs,
                "Upload a PPTX, PDF, or DOCX to activate.",
                f"/ui/project/{slug}/settings#datasets",
            ),
        },
        {
            "name": "Customer Strategist",
            "category": "core",
            "role": "Customer intelligence + campaign strategy",
            "type": "member", "tools": 9,
            "trigger": "customer / segment / RFM / churn / CLV / recommend / campaign",
            **state_for(
                has_customer_data,
                "Needs a customers + transactions table.",
                f"/ui/project/{slug}/customer/list",
            ),
        },
    ]

    # Vertical agents — opt-in per project via feature_config.agents.*
    # Status: ready if flag enabled, idle if disabled. Never auto-load.
    try:
        from dash.feature_config import get_feature_config as _get_fc
        _agents_cfg = (_get_fc(slug) or {}).get("agents", {}) or {}
    except Exception:
        _agents_cfg = {}

    vertical_defs = [
        ("Deal Analyst", "deal_analyst",
         "DCF / IRR / MOIC / sensitivity / partner fit · VentureDesk pillar",
         "deal / DCF / IRR / MOIC / cap table / valuation / term sheet / partner fit", 11),
        ("Market Sentinel", "market_sentinel",
         "External intel · competitor news · sector trends · brand sentiment",
         "competitor / sector / trend / sentiment / news / market intel / brand", 6),
        ("Ops Optimizer", "ops_optimizer",
         "Post-investment value-creation · KPI tracking · board reports · 100-day plans",
         "KPI / board report / portfolio co / value creation / 100-day / ops review", 8),
        ("Supply Sentry", "supply_sentry",
         "Supply-chain risk · single-source · lead time · geopolitical · BOM risk",
         "supplier / supply chain / single-source / lead time / geopolitical / BOM", 7),
    ]
    for vname, vkey, vrole, vtrig, vtools in vertical_defs:
        enabled = bool(_agents_cfg.get(vkey, True))  # Default ON
        agents.append({
            "name": vname,
            "category": "vertical",
            "role": vrole,
            "type": "member",
            "tools": vtools,
            "trigger": vtrig,
            "feature_flag": vkey,
            "state": "ready" if enabled else "idle",
            "reason": (
                "Active · fires on keyword match · tools self-check data presence"
                if enabled
                else f"Disabled per project via Settings → CONFIG → '{vkey}'."
            ),
            "cta": None if enabled else {
                "label": "Re-enable in CONFIG",
                "url": f"/ui/project/{slug}/settings#config",
            },
        })

    # Specialists are TOOLS on Analyst — always READY when Analyst is. They fire
    # on keyword match in chat. Never "standby" in the broken sense.
    specialist_state = state_for(
        has_tables,
        "Activates when Analyst is configured (needs tables).",
        f"/ui/project/{slug}/settings#datasets",
    )
    specialist_defs = [
        ("Comparator", "period comparison · MoM · YoY · delta analysis · auto-detects date columns", "compare, vs, period, month, year"),
        ("Diagnostician", "root cause analysis · metric decomposition · waterfall breakdown · dimension contribution", "why, caused, reason, dropped, increased"),
        ("Narrator", "executive summary · McKinsey-style narrative · key wins · key risks · recommendations", "summary, board update, overview, executive"),
        ("Validator", "data quality profiling · NULL detection · duplicate check · health scoring per table", "data quality, issues, check, validate, health"),
        ("Planner", "what-if scenarios · base/upside/downside · probability-weighted outcomes · impact modeling", "what if, scenario, close, open, change"),
        ("Trend Analyst", "time series · moving averages · direction detection · inflection points", "trend, over time, monthly, growth rate"),
        ("Pareto Analyst", "80/20 analysis · top drivers · cumulative impact · A/B/C classification", "top, drivers, 80/20, pareto, biggest"),
        ("Anomaly Detector", "Z-score outlier detection · deviation alerts · unusual pattern identification", "unusual, anomaly, outlier, strange"),
        ("Benchmarker", "entity vs average · gap analysis · performance ranking · above/below baseline", "compare to average, benchmark, rank, best, worst"),
        ("Prescriptor", "actionable recommendations · expected impact · priority ranking · next steps", "recommend, should, action, next steps, improve"),
    ]
    for name, role, trig in specialist_defs:
        agents.append({
            "name": name, "category": "specialist", "role": role, "type": "specialist",
            "parent": "Analyst", "tools": 1, "trigger": trig,
            **specialist_state,
        })

    # Extended agents (Demo-OS) REMOVED 2026-05-20 — their tools were folded
    # into core agents (Analyst/Engineer/Researcher) via dash/tools/extended_tools.py
    # and the 7 wrapper agents deleted. No longer surfaced in the AGENTS inventory.

    # Background (11) — fire after every chat, non-blocking
    background_defs = [
        ("Judge", "quality scoring 1-5 + category + confidence", 0),
        ("Rule Suggester", "extracts business rules from conversation", 0),
        ("Proactive Insights", "anomaly detection · >20% deviations · quality flags", 0),
        ("Query Plan Extractor", "parses SQL · tables · joins · filters", 0),
        ("Meta Learner", "tracks self-correction strategy success rates", 0),
        ("Auto Evolver", "regenerates instructions every 20 chats", 0),
        ("Chat Triple Extractor", "extracts 3-10 SPO triples per chat → KG", 0),
        ("Auto-Memory Promoter", "saves factual observations to memory", 0),
        ("User Preference Tracker", "analysis style · favorite metrics · viz prefs", 0),
        ("Episodic Memory Extractor", "captures reactions · surprises · corrections", 0),
        ("Follow-up Suggester", "KG-aware next-question suggestions", 0),
    ]
    for name, role, tools in background_defs:
        agents.append({
            "name": name, "category": "background", "role": role, "type": "background",
            "tools": tools, "trigger": "after every chat",
            "state": "active", "status": "active",
            "reason": "Runs unconditionally after every chat.",
            "cta": None, "last_used_at": chat_ts,
        })

    # Upload (5) — fire on file upload
    upload_defs = [
        ("Conductor", "upload orchestrator · creates plan · assigns agents · retries", 0),
        ("Parser", "data extraction · Excel/CSV/JSON · headers · unpivot · split sheets", 6),
        ("Scanner", "document intelligence · PDF/PPTX/DOCX · OCR · vision fallback", 5),
        ("Vision", "visual recognition · charts · diagrams · OCR-first", 3),
        ("Inspector", "data quality · profile · health score · triggers retry", 5),
    ]
    for name, role, tools in upload_defs:
        agents.append({
            "name": name, "category": "upload", "role": role, "type": "upload",
            "tools": tools, "trigger": "on file upload",
            "state": "active", "status": "active",
            "reason": "Runs unconditionally on file upload.",
            "cta": None, "last_used_at": chat_ts,
        })

    # Investment vertical (7 specialists — only visible when feature flag on OR
    # project has financial tables: balance_sheet / income_statement / etc).
    try:
        from dash.feature_config import get_feature_config as _fc_inv
        _inv_flag = bool(_fc_inv(slug).get("agents", {}).get("investment", False))
    except Exception:
        _inv_flag = False
    _has_fin = False
    if not _inv_flag:
        try:
            from dash.team import _has_financial_tables as _hft
            _has_fin = _hft(slug)
        except Exception:
            _has_fin = False
    # Also enable when project template_name is finance/investment-oriented.
    _inv_template = False
    try:
        with _engine.connect() as _c:
            _row = _c.execute(
                text("SELECT template_name FROM dash_projects WHERE slug=:s LIMIT 1"),
                {"s": slug},
            ).fetchone()
            if _row and _row[0]:
                _inv_template = str(_row[0]).lower() in {
                    "investment", "financial_services", "bank", "finance", "saas",
                }
    except Exception:
        _inv_template = False
    _inv_active = _inv_flag or _has_fin or _inv_template
    # OMIT investment agents entirely on non-financial projects.
    if not _inv_active:
        investment_defs = []
    else:
        investment_defs = [
        ("Market Analyst",     "sector + comparable deal context",
         ["search_brain", "recall", "find_comparable_deals", "exa_news_search"]),
        ("Financial Analyst",  "balance sheet, P&L, cash flow analysis from project tables",
         ["get_balance_sheet", "get_income_statement", "get_cashflow", "get_cap_table",
          "compute_unit_economics", "compute_valuation_multiples"]),
        ("Cohort Analyst",     "growth, retention, churn from internal data",
         ["compute_growth_metrics", "cohort_curve", "rfm_score"]),
        ("Risk Officer",       "mandate compliance + red flag detection",
         ["find_red_flags", "get_customer_concentration", "verify_against_mandate"]),
        ("Memo Writer",        "drafts IC memo from analyst outputs",
         ["save_memo", "list_memos"]),
        ("Committee Chair",    "ACQUIRE/DEFER/PASS verdict + conviction",
         []),
        ("Knowledge (RAG)",    "RAG over pitch deck, DD reports, term sheets",
         ["recall", "search_pitch_deck", "search_dd_findings", "extract_team_bios",
          "extract_market_size"]),
    ]
    for name, role, _tools in investment_defs:
        if _inv_active:
            _state = "active" if chat_recent else "ready"
            _reason = ("Healthy. Recently invoked." if chat_recent
                       else "Available. Will fire on investment questions.")
            _cta = None
        else:
            _state = "needs_setup"
            _reason = "Upload balance sheet + P&L to activate, or enable the investment flag."
            _cta = {"label": "UPLOAD DATA", "url": f"/ui/project/{slug}/settings#datasets"}
        agents.append({
            "name": name, "category": "investment", "role": role, "type": "investment",
            "tools": len(_tools), "tool_names": _tools,
            "trigger": "invest / valuation / IC memo / cap table / cohort / mandate / red flag",
            "state": _state,
            "status": ("active" if _state in ("active", "ready") else "standby"),
            "reason": _reason,
            "cta": _cta,
            "last_used_at": chat_ts,
        })

    # Routing (2)
    routing_defs = [
        ("Smart Router", "2-tier project routing · keyword score → Router Agent · 4 tools", 4,
         "ambiguous project selection in Dash Agent"),
        ("Visualizer", "auto-detect chart type · 8 types · rules engine + LLM fallback", 1,
         "after every data query result"),
    ]
    for name, role, tools, trig in routing_defs:
        agents.append({
            "name": name, "category": "routing", "role": role, "type": "routing",
            "tools": tools, "trigger": trig,
            **state_for(True, "", None, always_active=True),
        })

    # Background minion section — pulled live from registry + dash_minions
    # joined per-kind stats. Scoped to this project_slug. Fail-soft.
    minions: list[dict] = []
    try:
        minions = _list_minions_with_stats(_engine, slug)
    except Exception:
        logger.exception("list_minions_with_stats failed")
        minions = []

    # Sanitize string fields — broken control chars (\r, embedded newlines) in
    # role/reason/trigger have historically crashed the JSON envelope on the
    # client side. Sweep every string in both agent + minion lists.
    def _clean_str(v):
        if isinstance(v, str):
            return v.replace('\r', '').replace('\n', ' ').replace('\x00', '').strip()
        return v

    def _clean_obj(obj):
        if isinstance(obj, dict):
            return {k: _clean_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean_obj(v) for v in obj]
        return _clean_str(obj)

    agents_clean = _clean_obj(agents)
    minions_clean = _clean_obj(minions)

    return {
        "agents": agents_clean,
        "minions": minions_clean,
        "team_mode": "TeamMode.coordinate",
        "model": DEEP_MODEL,
        "schema": slug,
        "reasoning": [
            {"mode": "FAST", "description": "direct SQL → answer (simple questions)"},
            {"mode": "DEEP", "description": "think() + analyze() → multi-step reasoning (complex questions)"},
        ],
        "legend": {
            "active": "Healthy + invoked within 7d",
            "ready": "Healthy + available, awaiting trigger",
            "needs_setup": "Prerequisite missing — see CTA",
            "error": "Last invocation failed within 1h",
        },
    }


# ---------------------------------------------------------------------------
# Data Quality
# ---------------------------------------------------------------------------

@router.get("/{slug}/data-quality")
def get_data_quality(slug: str, request: Request):
    """Scan project tables for data-quality issues. 5-min cache."""
    user = _get_user(request)
    _check_access(user, slug)
    try:
        from dash.learning.data_quality_scanner import scan_project
        payload = scan_project(_engine, slug, force=False)
        return payload
    except Exception:
        logger.exception("data_quality scan failed for %s", slug)
        return {
            "issues": [],
            "by_severity": {"high": 0, "medium": 0, "low": 0, "info": 0},
            "by_table": {},
            "by_type": {},
            "score": 100,
            "table_count": 0,
            "issue_count": 0,
            "last_scanned": None,
            "error": "scan failed — see server logs",
        }


@router.post("/{slug}/data-quality/rescan")
def rescan_data_quality(slug: str, request: Request):
    """Force refresh — bypass the 5-min cache."""
    user = _get_user(request)
    _check_access(user, slug)
    try:
        from dash.learning.data_quality_scanner import scan_project, invalidate_cache
        invalidate_cache(slug)
        payload = scan_project(_engine, slug, force=True)
        return payload
    except Exception:
        logger.exception("data_quality rescan failed for %s", slug)
        raise HTTPException(500, "rescan failed")


def _list_minions_with_stats(eng, slug: str | None) -> list[dict]:
    """Per-kind minion stats joined with registry rows.

    Returns rows where registry.handler_kind IS NOT NULL. Per-kind stats
    (queued/running/done_24h/failed_24h/last_done_at) are scoped to the
    given project_slug when provided, else aggregated across all projects.
    """
    with eng.connect() as c:
        rows = c.execute(text("""
            WITH stats AS (
              SELECT kind,
                     COUNT(*) FILTER (WHERE status='pending') AS queued,
                     COUNT(*) FILTER (WHERE status='running') AS running,
                     COUNT(*) FILTER (WHERE status='done'
                       AND finished_at > now() - INTERVAL '24 hours') AS done_24h,
                     COUNT(*) FILTER (WHERE status='failed'
                       AND finished_at > now() - INTERVAL '24 hours') AS failed_24h,
                     MAX(finished_at) FILTER (WHERE status='done') AS last_done_at
                FROM dash.dash_minions
               WHERE CAST(:s AS TEXT) IS NULL OR project_slug = :s
               GROUP BY kind
            )
            SELECT r.agent_name, r.display_name, r.category, r.description,
                   r.handler_kind, r.trigger_model, r.llm_model,
                   r.cost_per_invocation, r.status,
                   COALESCE(s.queued, 0)     AS queued,
                   COALESCE(s.running, 0)    AS running,
                   COALESCE(s.done_24h, 0)   AS done_24h,
                   COALESCE(s.failed_24h, 0) AS failed_24h,
                   s.last_done_at
              FROM public.dash_agent_registry r
              LEFT JOIN stats s ON s.kind = r.handler_kind
             WHERE r.handler_kind IS NOT NULL
             ORDER BY r.category, r.agent_name
        """), {"s": slug}).mappings().all()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        if d.get("last_done_at") is not None:
            d["last_done_at"] = d["last_done_at"].isoformat()
        if d.get("cost_per_invocation") is not None:
            d["cost_per_invocation"] = float(d["cost_per_invocation"])
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Engine cache admin (super-admin) — see db/session.get_engine_cache_stats
# ---------------------------------------------------------------------------

admin_router = APIRouter(prefix="/api/admin", tags=["Admin"])


@admin_router.get("/engines/stats")
def admin_engine_cache_stats(request: Request):
    """Per-project engine cache stats: count, oldest entry age, memory estimate.

    Used to monitor for cache creep on multi-project workloads. See Issue #14:
    30 min TTL + 100 entry LRU + gc.collect() on eviction.
    """
    from app.auth import _require_super
    _require_super(request)
    try:
        from db.session import get_engine_cache_stats
        return get_engine_cache_stats()
    except Exception as e:
        raise HTTPException(500, f"engine stats unavailable: {e}")


# ---------------------------------------------------------------------------
# Agent OS Fleet — super-admin endpoints (registry + live minion stats)
# ---------------------------------------------------------------------------

@router.get("/agents/registry")
def agents_registry_all(request: Request):
    """All Agent OS registry rows grouped by category. Super-admin."""
    from app.auth import _require_super
    _require_super(request)
    try:
        with _engine.connect() as c:
            rows = c.execute(text("""
                SELECT agent_name, display_name, category, status, description,
                       handler_kind, trigger_model, llm_model,
                       cost_per_invocation, last_seen_at
                  FROM public.dash_agent_registry
                 ORDER BY category, agent_name
            """)).mappings().all()
        if not rows:
            raise HTTPException(404, "Registry empty — apply migration 073")
        by_cat: dict[str, list[dict]] = {}
        for r in rows:
            d = dict(r)
            if d.get("last_seen_at") is not None:
                d["last_seen_at"] = d["last_seen_at"].isoformat()
            if d.get("cost_per_invocation") is not None:
                d["cost_per_invocation"] = float(d["cost_per_invocation"])
            by_cat.setdefault(d["category"] or "uncategorized", []).append(d)
        return {"categories": by_cat, "total": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("agents_registry_all failed")
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/agents/os-hub")
def os_hub_aggregate(request: Request):
    """
    Single aggregator for Dash-OS overview tab.
    Returns ALL counters + drill targets in one round-trip.
    Each sub-query wrapped in try/except → fail-soft 0/null.
    """
    from app.auth import _require_super
    _require_super(request)

    out: dict = {
        "header": {
            "agents_total": 0,
            "agents_active": 0,
            "sub_agents_online": 0,
            "minions_running": 0,
            "minions_queued": 0,
            "estimated_daily_cost_usd": 0,
            "investment_runs_count_today": 0,
            "investment_memos_total": 0,
        },
        "categories": {},
        "subviews": {
            "workflows": {
                "count_total": 0,
                "count_active": 0,
                "count_pending": 0,
                "builtin_count": 5,
                "drill_url": "/ui/command-center?tab=workflows",
            },
            "skills": {
                "count_total": 0,
                "count_builtin": 11,
                "count_patched": 0,
                "drill_url": "/ui/command-center?tab=skills",
            },
            "sub_agents": {
                "count_spawned": 0,
                "count_available": 7,
                "drill_url": "/ui/command-center?tab=sub-agents",
            },
            "sim_lab": {
                "count_total_sims": 0,
                "count_running": 0,
                "count_done_24h": 0,
                "drill_url": "/ui/sim",
            },
            "marketplace": {
                "count_hot": 0,
                "count_total": 0,
                "drill_url": "/ui/sim/marketplace",
            },
            "wizard": {
                "drill_url": "/ui/sim/wizard",
            },
            "mcp_servers": {
                "count": 0,
                "drill_url": "/ui/command-center?tab=mcp",
            },
        },
        "cron_schedules": [],
        "recent_activity": {
            "last_dream_run_at": None,
            "last_autosim_run_at": None,
            "last_sim_completed_at": None,
            "last_chat_at": None,
        },
    }

    try:
        with _engine.connect() as c:
            # ── Header: registry totals ───────────────────────────────────
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS total, "
                    "COUNT(*) FILTER (WHERE status='active') AS active "
                    "FROM public.dash_agent_registry"
                )).mappings().first()
                if row:
                    out["header"]["agents_total"] = int(row["total"] or 0)
                    out["header"]["agents_active"] = int(row["active"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: registry totals failed")

            # ── Header: extended sub-agents online (last 1h) ──────────────
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS n FROM public.dash_agent_registry "
                    "WHERE category='extended' "
                    "AND last_seen_at > now() - INTERVAL '1 hour'"
                )).mappings().first()
                if row:
                    out["header"]["sub_agents_online"] = int(row["n"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: sub_agents_online failed")

            # ── Header: minions running / queued ──────────────────────────
            try:
                row = c.execute(text(
                    "SELECT "
                    "COUNT(*) FILTER (WHERE status='running') AS running, "
                    "COUNT(*) FILTER (WHERE status='pending') AS queued "
                    "FROM dash.dash_minions"
                )).mappings().first()
                if row:
                    out["header"]["minions_running"] = int(row["running"] or 0)
                    out["header"]["minions_queued"] = int(row["queued"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: minions stats failed")

            # ── Header: estimated daily cost ──────────────────────────────
            try:
                row = c.execute(text(
                    "SELECT COALESCE(SUM("
                    "  cost_per_invocation * "
                    "  CASE trigger_model "
                    "    WHEN 'sync_chat' THEN 20 "
                    "    WHEN 'event_hook' THEN 10 "
                    "    WHEN 'minion_queue' THEN 4 "
                    "    WHEN 'cron' THEN 1 "
                    "    ELSE 5 "
                    "  END"
                    "), 0) AS est "
                    "FROM public.dash_agent_registry "
                    "WHERE status='active'"
                )).mappings().first()
                if row:
                    out["header"]["estimated_daily_cost_usd"] = float(row["est"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: est cost failed")

            # ── Categories breakdown ──────────────────────────────────────
            try:
                rows = c.execute(text(
                    "SELECT category, COUNT(*) AS total, "
                    "COUNT(*) FILTER (WHERE status='active') AS active "
                    "FROM public.dash_agent_registry GROUP BY category"
                )).mappings().all()
                for r in rows:
                    cat = r["category"] or "uncategorized"
                    out["categories"][cat] = {
                        "count": int(r["total"] or 0),
                        "active": int(r["active"] or 0),
                        "drill_url": f"/ui/command-center?tab=fleet&cat={cat}",
                    }
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: categories failed")

            # ── Investment: override active count + drill URL ─────────────
            # Active = invoked in last 24h (last_invoked_at OR last_seen_at,
            # whichever exists). Drill points to per-project investment desk.
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS total, "
                    "COUNT(*) FILTER (WHERE "
                    "  last_seen_at > now() - INTERVAL '24 hours') AS active "
                    "FROM public.dash_agent_registry "
                    "WHERE category='investment'"
                )).mappings().first()
                if row and int(row["total"] or 0) > 0:
                    out["categories"]["investment"] = {
                        "count": int(row["total"] or 0),
                        "active": int(row["active"] or 0),
                        "drill_url": "/ui/project/demo/investment",
                    }
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: investment category failed")

            # ── Header: investment runs today ─────────────────────────────
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS n FROM dash.dash_investment_runs "
                    "WHERE started_at > current_date"
                )).mappings().first()
                if row:
                    out["header"]["investment_runs_count_today"] = int(row["n"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: investment runs today failed")

            # ── Header: investment memos total ────────────────────────────
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS n FROM dash.dash_investment_memos"
                )).mappings().first()
                if row:
                    out["header"]["investment_memos_total"] = int(row["n"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: investment memos total failed")

            # ── Subview: workflows ────────────────────────────────────────
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS total, "
                    "COUNT(*) FILTER (WHERE status='active') AS active, "
                    "COUNT(*) FILTER (WHERE status='pending') AS pending "
                    "FROM dash.dash_autonomous_workflows"
                )).mappings().first()
                if row:
                    out["subviews"]["workflows"]["count_total"] = int(row["total"] or 0)
                    out["subviews"]["workflows"]["count_active"] = int(row["active"] or 0)
                    out["subviews"]["workflows"]["count_pending"] = int(row["pending"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: workflows failed")

            # ── Subview: skills ───────────────────────────────────────────
            try:
                row = c.execute(text(
                    "SELECT COUNT(DISTINCT tool_name) AS n "
                    "FROM dash.dash_tool_utility_scores"
                )).mappings().first()
                if row:
                    out["subviews"]["skills"]["count_total"] = int(row["n"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: skills total failed")
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS n FROM dash.dash_tool_patches "
                    "WHERE status='active'"
                )).mappings().first()
                if row:
                    out["subviews"]["skills"]["count_patched"] = int(row["n"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: skills patched failed")

            # ── Subview: sub_agents (extended w/ recent activity) ─────────
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS n FROM public.dash_agent_registry "
                    "WHERE category='extended' "
                    "AND last_seen_at > now() - INTERVAL '1 hour'"
                )).mappings().first()
                if row:
                    out["subviews"]["sub_agents"]["count_spawned"] = int(row["n"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: sub_agents spawned failed")
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS n FROM public.dash_agent_registry "
                    "WHERE category='extended'"
                )).mappings().first()
                if row:
                    out["subviews"]["sub_agents"]["count_available"] = int(row["n"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: sub_agents available failed")

            # ── Subview: sim_lab ──────────────────────────────────────────
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) AS total, "
                    "COUNT(*) FILTER (WHERE status IN "
                    "  ('running','graph_building','env_setup','simulating','reporting')"
                    ") AS running, "
                    "COUNT(*) FILTER (WHERE status='done' "
                    "  AND updated_at > now() - INTERVAL '24 hours') AS done_24h "
                    "FROM dash.sim_projects"
                )).mappings().first()
                if row:
                    out["subviews"]["sim_lab"]["count_total_sims"] = int(row["total"] or 0)
                    out["subviews"]["sim_lab"]["count_running"] = int(row["running"] or 0)
                    out["subviews"]["sim_lab"]["count_done_24h"] = int(row["done_24h"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: sim_lab failed")

            # ── Subview: marketplace ──────────────────────────────────────
            try:
                row = c.execute(text(
                    "SELECT COUNT(*) FILTER (WHERE unique_tenants >= 5) AS hot, "
                    "COUNT(*) AS total "
                    "FROM dash.dash_sim_recommendations"
                )).mappings().first()
                if row:
                    out["subviews"]["marketplace"]["count_hot"] = int(row["hot"] or 0)
                    out["subviews"]["marketplace"]["count_total"] = int(row["total"] or 0)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: marketplace failed")

            # ── Cron schedules ────────────────────────────────────────────
            try:
                rows = c.execute(text(
                    "SELECT CAST(id AS TEXT) AS id, name, cron_expr, "
                    "next_run_at, last_run_at "
                    "FROM dash.dash_agent_schedules "
                    "WHERE enabled=true "
                    "ORDER BY next_run_at NULLS LAST LIMIT 10"
                )).mappings().all()
                for r in rows:
                    d = dict(r)
                    if d.get("next_run_at") is not None:
                        d["next_run_at"] = d["next_run_at"].isoformat()
                    if d.get("last_run_at") is not None:
                        d["last_run_at"] = d["last_run_at"].isoformat()
                    out["cron_schedules"].append(d)
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: cron_schedules failed")

            # ── Recent activity ───────────────────────────────────────────
            try:
                row = c.execute(text(
                    "SELECT MAX(created_at) AS ts "
                    "FROM dash.dash_autosim_runs WHERE status='done'"
                )).mappings().first()
                if row and row["ts"] is not None:
                    out["recent_activity"]["last_autosim_run_at"] = row["ts"].isoformat()
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: last_autosim_run_at failed")
            try:
                row = c.execute(text(
                    "SELECT MAX(updated_at) AS ts "
                    "FROM dash.sim_projects WHERE status='done'"
                )).mappings().first()
                if row and row["ts"] is not None:
                    out["recent_activity"]["last_sim_completed_at"] = row["ts"].isoformat()
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: last_sim_completed_at failed")
            try:
                row = c.execute(text(
                    "SELECT MAX(updated_at) AS ts FROM dash.dash_chat_sessions"
                )).mappings().first()
                if row and row["ts"] is not None:
                    out["recent_activity"]["last_chat_at"] = row["ts"].isoformat()
            except Exception:
                try: c.rollback()
                except Exception: logger.debug("learning: os_hub rollback failed")
                logger.debug("os_hub: last_chat_at failed")

        return out
    except Exception as e:
        logger.exception("os_hub_aggregate failed")
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/agents/minions/stats")
def minions_live_stats(request: Request):
    """Per-kind × status counts over last 7 days across all projects."""
    from app.auth import _require_super
    _require_super(request)
    try:
        with _engine.connect() as c:
            rows = c.execute(text("""
                SELECT kind, status, COUNT(*) AS n,
                       MAX(finished_at) AS last_finished,
                       AVG(EXTRACT(EPOCH FROM (finished_at - started_at)))
                         FILTER (WHERE finished_at IS NOT NULL) AS avg_duration_s
                  FROM dash.dash_minions
                 WHERE created_at > now() - INTERVAL '7 days'
                 GROUP BY kind, status
                 ORDER BY kind, status
            """)).mappings().all()
        stats = []
        for r in rows:
            d = dict(r)
            if d.get("last_finished") is not None:
                d["last_finished"] = d["last_finished"].isoformat()
            if d.get("avg_duration_s") is not None:
                d["avg_duration_s"] = float(d["avg_duration_s"])
            d["n"] = int(d["n"])
            stats.append(d)
        return {"stats": stats}
    except Exception as e:
        logger.exception("minions_live_stats failed")
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/{slug}/agents/sources")
def list_agent_sources(slug: str, request: Request):
    """Per-agent data source map. Maps connector sources + LOCAL to each agent."""
    user = _get_user(request)
    _check_access(user, slug)
    sources_list = []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, source_type, site_name, config "
                "FROM public.dash_data_sources "
                "WHERE project_slug = :slug "
                "AND source_type IN ('postgresql', 'mysql', 'fabric') "
                "AND status != 'deleted' ORDER BY created_at DESC"
            ), {"slug": slug}).fetchall()
        for r in rows:
            cfg = r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {})
            db_type = r[1]
            sources_list.append({
                "id": r[0],
                "name": r[2] or db_type.upper(),
                "dialect": "tsql" if db_type == "fabric" else db_type,
                "mode": cfg.get("mode", "sync"),
                "scope": cfg.get("agent_scope", "project"),
            })
    except Exception:
        pass
    local = [{"name": "LOCAL", "dialect": "postgresql", "mode": "sync", "scope": "project"}]
    return {
        "agents": {
            "Analyst": local + sources_list,
            "Leader": local,
            "Engineer": local,
            "Researcher": [],
            "Customer Strategist": local,
        }
    }


# Workflows (DB-backed)
# ---------------------------------------------------------------------------

@router.get("/{slug}/workflows-db")
def list_workflows_db(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, name, description, steps, created_at, source FROM public.dash_workflows_db "
            "WHERE project_slug = :s ORDER BY created_at"
        ), {"s": slug}).fetchall()
    wfs = []
    for r in rows:
        steps = r[3] if isinstance(r[3], list) else json.loads(r[3]) if r[3] else []
        wfs.append({"id": r[0], "name": r[1], "description": r[2], "steps": steps, "created_at": str(r[4]) if r[4] else None, "source": r[5] or "training"})
    return {"workflows": wfs}


@router.post("/{slug}/workflows-db")
async def create_workflow(slug: str, request: Request):
    """Create a new workflow."""
    user = _get_user(request)
    _check_access(user, slug)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Workflow name is required")
    steps = body.get("steps", [])
    if not steps:
        raise HTTPException(400, "At least one step is required")
    description = body.get("description", "")
    source = body.get("source", "user")
    with _engine.connect() as conn:
        result = conn.execute(text(
            "INSERT INTO public.dash_workflows_db (project_slug, name, description, steps, source) "
            "VALUES (:s, :n, :d, CAST(:st AS jsonb), :src) RETURNING id"
        ), {"s": slug, "n": name, "d": description, "st": json.dumps(steps), "src": source})
        new_id = result.fetchone()[0]
        conn.commit()
    return {"status": "ok", "id": new_id}


@router.post("/{slug}/doc-to-workflow")
async def doc_to_workflow(slug: str, request: Request):
    """Extract document structure and convert to a workflow preview."""
    user = _get_user(request)
    _check_access(user, slug)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
    filename = body.get("filename", "").strip()
    if not filename:
        raise HTTPException(400, "filename is required")

    from dash.paths import KNOWLEDGE_DIR
    from pathlib import Path

    # Try raw binary first, fall back to text file
    raw_path = KNOWLEDGE_DIR / slug / "docs_raw" / filename
    ext = Path(filename).suffix.lower()
    if not raw_path.exists():
        raise HTTPException(404, f"Document not found: {filename}")
    if ext not in (".pptx", ".pdf", ".docx"):
        raise HTTPException(400, "Only PPTX, PDF, and DOCX files support workflow extraction")

    # Extract structure
    import tempfile
    from app.upload import _extract_document_structure
    sections = _extract_document_structure(str(raw_path), ext)
    if len(sections) < 2:
        raise HTTPException(400, "Document has insufficient structure (need at least 2 sections)")

    # Build LLM prompt
    sections_text = "\n".join(
        f"{s['index']}. {s['title']} — {s['content_summary']}" for s in sections
    )
    prompt = (
        f"Convert this document structure into a reusable analysis workflow.\n"
        f"Each section should become one workflow step — write a clear analyst question "
        f"that would reproduce the analysis shown in that section.\n\n"
        f"DOCUMENT: {filename}\n"
        f"SECTIONS:\n{sections_text}\n\n"
        f"Return ONLY valid JSON (no markdown):\n"
        f'{{"name": "workflow name based on document", "description": "what this workflow analyzes", '
        f'"steps": [{{"title": "section title", "question": "analyst question to reproduce this analysis"}}]}}'
    )

    from dash.settings import training_llm_call
    result = training_llm_call(prompt, "extraction")
    if not result:
        raise HTTPException(500, "LLM call failed")

    try:
        workflow = json.loads(result.strip().strip("`").strip())
    except Exception:
        raise HTTPException(500, "Failed to parse LLM response")

    # Add source sections for reference
    workflow["source_file"] = filename
    workflow["sections_found"] = len(sections)
    return {"workflow": workflow}


@router.post("/{slug}/workflows-db/{wf_id}/run")
async def run_workflow(slug: str, wf_id: int, request: Request):
    """Execute a workflow — run each step through the agent."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT name, steps FROM public.dash_workflows_db WHERE id = :id AND project_slug = :s"
        ), {"id": wf_id, "s": slug}).fetchone()

    if not row:
        raise HTTPException(404, "Workflow not found")

    steps = row[1] if isinstance(row[1], list) else json.loads(row[1]) if row[1] else []
    results = []

    # Get project info
    with _engine.connect() as conn:
        proj = conn.execute(text(
            "SELECT agent_name, agent_role, agent_personality FROM public.dash_projects WHERE slug = :s"
        ), {"s": slug}).fetchone()

    if not proj:
        raise HTTPException(404, "Project not found")

    from dash.team import create_project_team
    team = create_project_team(project_slug=slug, agent_name=proj[0], agent_role=proj[1], agent_personality=proj[2])

    for i, step in enumerate(steps):
        step_text = step if isinstance(step, str) else step.get("description", str(step))
        try:
            response = team.run(step_text)
            results.append({"step": i + 1, "prompt": step_text, "result": response.content or "", "status": "done"})
        except Exception as e:
            results.append({"step": i + 1, "prompt": step_text, "result": str(e), "status": "error"})

    return {"workflow": row[0], "results": results}


# ---------------------------------------------------------------------------
# Proactive Insights
# ---------------------------------------------------------------------------

@router.get("/{slug}/insights")
def list_insights(slug: str, request: Request):
    """List non-dismissed proactive insights."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, insight, severity, tables_involved, created_at "
            "FROM public.dash_proactive_insights "
            "WHERE project_slug = :s AND dismissed = FALSE "
            "ORDER BY created_at DESC LIMIT 10"
        ), {"s": slug}).fetchall()
    out = []
    for r in rows:
        ins = r[1] or ""
        is_auto = ins.startswith("[") and "/" in ins.split("\n", 1)[0] and "] " in ins.split("\n", 1)[0]
        source = "autonomous" if is_auto else "training"
        # parse "[template/workflow]" header for auto insights
        wf_name = ""
        if is_auto:
            try:
                head = ins.split("\n", 1)[0]
                inside = head[1: head.index("]")]
                wf_name = inside.split("/", 1)[1] if "/" in inside else inside
            except Exception:
                pass
        out.append({
            "id": r[0],
            "insight": ins,
            "severity": r[2],
            "tables": r[3] or [],
            "created_at": str(r[4]),
            "source": source,
            "workflow_name": wf_name,
            "is_autonomous": is_auto,
        })
    return {"insights": out}


@router.post("/{slug}/insights/{insight_id}/dismiss")
def dismiss_insight(slug: str, insight_id: int, request: Request):
    """Dismiss a proactive insight."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_proactive_insights SET dismissed = TRUE "
            "WHERE id = :id AND project_slug = :s"
        ), {"id": insight_id, "s": slug})
        conn.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# User Preferences
# ---------------------------------------------------------------------------

@router.get("/{slug}/preferences")
def get_preferences(slug: str, request: Request):
    """Get user preferences for this project."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT preferences FROM public.dash_user_preferences "
            "WHERE user_id = :uid AND project_slug = :s"
        ), {"uid": user["user_id"], "s": slug}).fetchone()
    prefs = row[0] if row else {}
    if isinstance(prefs, str):
        prefs = json.loads(prefs)
    return {"preferences": prefs}


@router.post("/{slug}/track-preference")
async def track_preference(slug: str, request: Request):
    """Track a user preference signal (chart type click, tab click, etc.)."""
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()
    action = body.get("action", "")  # e.g. "chart_type", "tab_click"
    value = body.get("value", "")    # e.g. "pie", "graph"

    if not action or not value:
        return {"status": "ok"}

    uid = user["user_id"]
    with _engine.connect() as conn:
        # Load existing preferences
        row = conn.execute(text(
            "SELECT preferences FROM public.dash_user_preferences "
            "WHERE user_id = :uid AND project_slug = :s"
        ), {"uid": uid, "s": slug}).fetchone()

        prefs = {}
        if row and row[0]:
            prefs = row[0] if isinstance(row[0], dict) else json.loads(row[0])

        # Merge signal: increment counter
        key = f"{action}_counts"
        if key not in prefs:
            prefs[key] = {}
        prefs[key][value] = prefs[key].get(value, 0) + 1

        # UPSERT
        conn.execute(text(
            "INSERT INTO public.dash_user_preferences (user_id, project_slug, preferences, updated_at) "
            "VALUES (:uid, :s, CAST(:prefs AS jsonb), NOW()) "
            "ON CONFLICT (user_id, project_slug) DO UPDATE SET preferences = CAST(:prefs AS jsonb), updated_at = NOW()"
        ), {"uid": uid, "s": slug, "prefs": json.dumps(prefs)})
        conn.commit()

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Query Plans
# ---------------------------------------------------------------------------

@router.get("/{slug}/query-plans")
def list_query_plans(slug: str, request: Request):
    """List proven query plan strategies."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, tables_involved, join_strategy, filters_used, success, question, sql_used, created_at "
            "FROM public.dash_query_plans "
            "WHERE project_slug = :s AND success = TRUE "
            "ORDER BY created_at DESC LIMIT 20"
        ), {"s": slug}).fetchall()
    return {"plans": [
        {"id": r[0], "tables": r[1] or [], "join_strategy": r[2], "filters": r[3],
         "success": r[4], "question": r[5], "sql": r[6], "created_at": str(r[7])}
        for r in rows
    ]}


# ---------------------------------------------------------------------------
# Knowledge Consolidation (Feature 4)
# ---------------------------------------------------------------------------

@router.get("/{slug}/consolidation-status")
def consolidation_status(slug: str, request: Request):
    """Check if knowledge consolidation is eligible."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        count = conn.execute(text(
            "SELECT COUNT(*) FROM public.dash_memories "
            "WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE)"
        ), {"s": slug}).scalar() or 0
    return {"memory_count": count, "eligible": count >= 30}


@router.post("/{slug}/consolidate-knowledge")
def consolidate_knowledge(slug: str, request: Request):
    """Compress many memories into higher-level insights via LLM."""
    from os import getenv
    import httpx

    user = _get_user(request)
    _check_access(user, slug)
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"status": "error", "detail": "No API key configured"}

    with _engine.connect() as conn:
        # Load all non-archived memories
        mem_rows = conn.execute(text(
            "SELECT id, fact FROM public.dash_memories "
            "WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE) "
            "ORDER BY created_at DESC LIMIT 100 FOR UPDATE SKIP LOCKED"
        ), {"s": slug}).fetchall()

        if len(mem_rows) < 30:
            return {"status": "error", "detail": f"Need at least 30 memories, have {len(mem_rows)}"}

        # Load recent feedback for additional context
        feedback = conn.execute(text(
            "SELECT question, rating FROM public.dash_feedback "
            "WHERE project_slug = :s ORDER BY created_at DESC LIMIT 20"
        ), {"s": slug}).fetchall()

        # Load top patterns
        patterns = conn.execute(text(
            "SELECT question, sql FROM public.dash_query_patterns "
            "WHERE project_slug = :s ORDER BY uses DESC LIMIT 10"
        ), {"s": slug}).fetchall()

    # Build context for LLM
    facts = "\n".join(f"- {r[1]}" for r in mem_rows)
    fb_context = "\n".join(f"- [{r[1]}] {r[0]}" for r in feedback) if feedback else "None"
    pattern_context = "\n".join(f"- Q: {r[0]} → SQL: {r[1][:100]}" for r in patterns) if patterns else "None"

    prompt = f"""You are consolidating a data agent's knowledge. Below are {len(mem_rows)} individual facts, recent feedback, and proven query patterns.

INDIVIDUAL MEMORIES:
{facts}

RECENT FEEDBACK:
{fb_context}

PROVEN PATTERNS:
{pattern_context}

Consolidate these into 5-10 higher-level insights. Each insight should:
1. Summarize multiple related facts into one actionable statement
2. Include specific table names, column names, and data patterns
3. Be useful for future query generation

Respond with ONLY valid JSON (no markdown):
{{"insights": ["insight 1", "insight 2", "insight 3"]}}"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": DEEP_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000, "temperature": 0.1},
            timeout=30,
        )
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content.strip().strip("`").strip())
        consolidated = parsed.get("insights", [])

        if not consolidated:
            return {"status": "error", "detail": "LLM returned no insights"}

        with _engine.connect() as conn:
            # Archive old memories
            old_ids = [r[0] for r in mem_rows]
            conn.execute(text(
                "UPDATE public.dash_memories SET archived = TRUE WHERE id = ANY(:ids)"
            ), {"ids": old_ids})

            # Insert consolidated insights
            for fact in consolidated:
                conn.execute(text(
                    "INSERT INTO public.dash_memories (project_slug, scope, fact, source) "
                    "VALUES (:s, 'project', :fact, 'consolidated')"
                ), {"s": slug, "fact": fact})
            conn.commit()

        return {"status": "ok", "consolidated": len(consolidated), "archived": len(old_ids)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ---------------------------------------------------------------------------
# Auto-Evolving Instructions (Feature 5)
# ---------------------------------------------------------------------------

@router.get("/{slug}/evolved-instructions")
def get_evolved_instructions(slug: str, request: Request):
    """Get current and historical evolved instructions."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, instructions, version, reasoning, chat_count_at_generation, created_at "
            "FROM public.dash_evolved_instructions "
            "WHERE project_slug = :s ORDER BY version DESC LIMIT 10"
        ), {"s": slug}).fetchall()
    if not rows:
        return {"current": None, "history": []}
    return {
        "current": {"id": rows[0][0], "instructions": rows[0][1], "version": rows[0][2], "reasoning": rows[0][3], "created_at": str(rows[0][5])},
        "history": [{"id": r[0], "version": r[2], "reasoning": r[3], "chat_count": r[4], "created_at": str(r[5])} for r in rows]
    }


@router.post("/{slug}/evolve-instructions")
def evolve_instructions(slug: str, request: Request):
    """Generate new evolved instructions from accumulated learnings."""
    from os import getenv
    import httpx

    user = _get_user(request)
    _check_access(user, slug)
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"status": "error", "detail": "No API key configured"}

    with _engine.connect() as conn:
        # Load all learning signals
        memories = conn.execute(text(
            "SELECT fact FROM public.dash_memories "
            "WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE) "
            "ORDER BY created_at DESC LIMIT 30"
        ), {"s": slug}).fetchall()

        feedback_good = conn.execute(text(
            "SELECT question, answer FROM public.dash_feedback "
            "WHERE project_slug = :s AND rating = 'up' ORDER BY created_at DESC LIMIT 10"
        ), {"s": slug}).fetchall()

        feedback_bad = conn.execute(text(
            "SELECT question, answer FROM public.dash_feedback "
            "WHERE project_slug = :s AND rating = 'down' ORDER BY created_at DESC LIMIT 5"
        ), {"s": slug}).fetchall()

        patterns = conn.execute(text(
            "SELECT question, sql FROM public.dash_query_patterns "
            "WHERE project_slug = :s ORDER BY uses DESC LIMIT 10"
        ), {"s": slug}).fetchall()

        plans = conn.execute(text(
            "SELECT tables_involved, join_strategy, filters_used FROM public.dash_query_plans "
            "WHERE project_slug = :s AND success = TRUE ORDER BY created_at DESC LIMIT 10"
        ), {"s": slug}).fetchall()

        # Get current version
        latest = conn.execute(text(
            "SELECT version, chat_count_at_generation FROM public.dash_evolved_instructions "
            "WHERE project_slug = :s ORDER BY version DESC LIMIT 1"
        ), {"s": slug}).fetchone()

        # Count chats
        chat_count = conn.execute(text(
            "SELECT COUNT(*) FROM public.dash_quality_scores WHERE project_slug = :s"
        ), {"s": slug}).scalar() or 0

    mem_text = "\n".join(f"- {r[0]}" for r in memories) if memories else "None yet"
    good_text = "\n".join(f"- Q: {r[0]}\n  A: {(r[1] or '')[:150]}" for r in feedback_good) if feedback_good else "None yet"
    bad_text = "\n".join(f"- Q: {r[0]}\n  A: {(r[1] or '')[:100]}" for r in feedback_bad) if feedback_bad else "None yet"
    pattern_text = "\n".join(f"- Q: {r[0]} → {r[1][:100]}" for r in patterns) if patterns else "None yet"
    plan_text = "\n".join(f"- Tables {r[0]}: {r[1] or 'N/A'}" for r in plans) if plans else "None yet"

    prompt = f"""You are generating supplementary instructions for a data analyst AI agent based on what it has learned from user interactions.

AGENT MEMORIES:
{mem_text}

APPROVED RESPONSES (user liked these):
{good_text}

REJECTED RESPONSES (user disliked these):
{bad_text}

PROVEN QUERY PATTERNS:
{pattern_text}

PROVEN JOIN STRATEGIES:
{plan_text}

Based on ALL of the above, generate concise supplementary instructions (max 500 words) that will help this agent perform better. Focus on:
1. Project-specific data patterns and gotchas
2. User's preferred response style and detail level
3. Common query approaches that work well
4. Mistakes to avoid (from rejected responses)
5. Domain-specific terminology and business rules discovered

Respond with ONLY valid JSON (no markdown):
{{"instructions": "Your supplementary instructions here...", "reasoning": "Brief explanation of why these instructions were generated"}}"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": DEEP_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000, "temperature": 0.1},
            timeout=30,
        )
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content.strip().strip("`").strip())
        instructions = parsed.get("instructions", "")
        reasoning = parsed.get("reasoning", "")

        if not instructions:
            return {"status": "error", "detail": "LLM returned no instructions"}

        new_version = (latest[0] + 1) if latest else 1

        with _engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_evolved_instructions "
                "(project_slug, instructions, version, reasoning, chat_count_at_generation) "
                "VALUES (:s, :inst, :v, :r, :cc)"
            ), {"s": slug, "inst": instructions, "v": new_version, "r": reasoning, "cc": chat_count})
            conn.commit()

        return {"status": "ok", "version": new_version}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.post("/{slug}/evolved-instructions/{inst_id}/revert")
def revert_evolved_instructions(slug: str, inst_id: int, request: Request):
    """Revert to a specific version by deleting all later versions."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM public.dash_evolved_instructions "
            "WHERE project_slug = :s AND id > :id"
        ), {"s": slug, "id": inst_id})
        conn.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Conversation Pattern Mining (Feature 6)
# ---------------------------------------------------------------------------

@router.post("/{slug}/mine-patterns")
def mine_patterns(slug: str, request: Request):
    """Analyze past conversations to discover recurring multi-step analysis patterns."""
    from os import getenv
    import httpx

    user = _get_user(request)
    _check_access(user, slug)
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"status": "error", "detail": "No API key configured"}

    # Load recent questions from feedback and quality scores as proxy for conversations
    with _engine.connect() as conn:
        questions = conn.execute(text(
            "SELECT question FROM public.dash_feedback "
            "WHERE project_slug = :s AND question IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 50"
        ), {"s": slug}).fetchall()

        # Also load from quality scores (which log every chat)
        scored = conn.execute(text(
            "SELECT session_id FROM public.dash_quality_scores "
            "WHERE project_slug = :s ORDER BY created_at DESC LIMIT 50"
        ), {"s": slug}).fetchall()

        # Load existing workflows to avoid duplicates
        existing = conn.execute(text(
            "SELECT name FROM public.dash_workflows_db WHERE project_slug = :s"
        ), {"s": slug}).fetchall()

    if len(questions) < 10:
        return {"status": "error", "detail": f"Need at least 10 past questions, have {len(questions)}"}

    existing_names = [r[0].lower() for r in existing]
    q_list = "\n".join(f"{i+1}. {r[0]}" for i, r in enumerate(questions))

    prompt = f"""Analyze these {len(questions)} user questions from a data analysis chat. Identify 3-5 recurring multi-step analysis patterns.

PAST QUESTIONS:
{q_list}

For each pattern, create a reusable workflow with 3-5 steps. Each step should be a question the user commonly asks in sequence.

Existing workflows (DO NOT duplicate these): {', '.join(existing_names) if existing_names else 'None'}

Respond with ONLY valid JSON (no markdown):
{{"workflows": [
  {{"name": "Revenue Deep Dive", "description": "Analyze revenue from multiple angles", "steps": ["What is the total revenue?", "Break down revenue by category", "Show the top 10 customers by revenue"]}},
  {{"name": "Customer Health Check", "description": "Check customer status and trends", "steps": ["How many active customers?", "Show customer growth over time", "Which customers are at risk of churn?"]}}
]}}"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": LITE_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000, "temperature": 0.2},
            timeout=30,
        )
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content.strip().strip("`").strip())
        workflows = parsed.get("workflows", [])

        created = 0
        with _engine.connect() as conn:
            for wf in workflows:
                name = wf.get("name", "")
                if name.lower() in existing_names:
                    continue
                conn.execute(text(
                    "INSERT INTO public.dash_workflows_db (project_slug, name, description, steps, source) "
                    "VALUES (:s, :name, :desc, CAST(:steps AS jsonb), 'mined')"
                ), {
                    "s": slug,
                    "name": name,
                    "desc": wf.get("description", ""),
                    "steps": json.dumps(wf.get("steps", [])),
                })
                created += 1
            conn.commit()

        return {"status": "ok", "workflows_created": created}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ---------------------------------------------------------------------------
# Meta-Learning (Feature 7)
# ---------------------------------------------------------------------------

@router.get("/{slug}/meta-learnings")
def list_meta_learnings(slug: str, request: Request):
    """List self-correction strategy success rates."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT error_type, fix_strategy, "
            "ROUND(100.0 * SUM(CASE WHEN success THEN 1 ELSE 0 END) / COUNT(*)) as success_rate, "
            "COUNT(*) as cnt "
            "FROM public.dash_meta_learnings WHERE project_slug = :s "
            "GROUP BY error_type, fix_strategy "
            "ORDER BY cnt DESC LIMIT 20"
        ), {"s": slug}).fetchall()
    return {"strategies": [
        {"error_type": r[0], "fix_strategy": r[1], "success_rate": float(r[2]), "count": r[3]}
        for r in rows
    ]}


# ---------------------------------------------------------------------------
# Cross-Project Learning Transfer (Feature 8)
# ---------------------------------------------------------------------------

@router.get("/{slug}/transfer-candidates")
def transfer_candidates(slug: str, request: Request):
    """Find projects with similar table structures for learning transfer."""
    user = _get_user(request)
    _check_access(user, slug)

    with _engine.connect() as conn:
        # Get current project's columns
        source_meta = conn.execute(text(
            "SELECT table_name, metadata FROM public.dash_table_metadata WHERE project_slug = :s"
        ), {"s": slug}).fetchall()

        source_columns = set()
        for r in source_meta:
            meta = r[1] if isinstance(r[1], dict) else json.loads(r[1]) if r[1] else {}
            for col in meta.get("table_columns", []):
                source_columns.add(col.get("name", "").lower())

        if not source_columns:
            return {"projects": []}

        # Get all other projects user has access to
        user_projects = conn.execute(text(
            "SELECT slug, agent_name FROM public.dash_projects WHERE user_id = :uid AND slug != :s"
        ), {"uid": user["user_id"], "s": slug}).fetchall()

        candidates = []
        for proj in user_projects:
            other_meta = conn.execute(text(
                "SELECT metadata FROM public.dash_table_metadata WHERE project_slug = :s"
            ), {"s": proj[0]}).fetchall()

            other_columns = set()
            for r in other_meta:
                meta = r[0] if isinstance(r[0], dict) else json.loads(r[0]) if r[0] else {}
                for col in meta.get("table_columns", []):
                    other_columns.add(col.get("name", "").lower())

            if not other_columns:
                continue

            overlap = source_columns & other_columns
            overlap_pct = len(overlap) / max(len(source_columns), 1) * 100

            if overlap_pct >= 20:
                # Count learnings available
                mem_count = conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_memories WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE)"
                ), {"s": proj[0]}).scalar() or 0
                pattern_count = conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_query_patterns WHERE project_slug = :s"
                ), {"s": proj[0]}).scalar() or 0

                candidates.append({
                    "slug": proj[0], "name": proj[1],
                    "overlap_pct": round(overlap_pct),
                    "shared_columns": list(overlap)[:20],
                    "memories": mem_count, "patterns": pattern_count,
                })

        candidates.sort(key=lambda x: x["overlap_pct"], reverse=True)
        return {"projects": candidates}


@router.get("/{slug}/preview-import")
def preview_import(slug: str, request: Request, source: str = ""):
    """Preview what would be imported from another project."""
    user = _get_user(request)
    _check_access(user, slug)
    if not source:
        return {"memories": [], "patterns": [], "annotations": []}

    with _engine.connect() as conn:
        memories = conn.execute(text(
            "SELECT fact, source FROM public.dash_memories "
            "WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE) LIMIT 20"
        ), {"s": source}).fetchall()
        patterns = conn.execute(text(
            "SELECT question, sql FROM public.dash_query_patterns "
            "WHERE project_slug = :s ORDER BY uses DESC LIMIT 10"
        ), {"s": source}).fetchall()
        annotations = conn.execute(text(
            "SELECT table_name, column_name, annotation FROM public.dash_annotations "
            "WHERE project_slug = :s LIMIT 20"
        ), {"s": source}).fetchall()

    return {
        "memories": [{"fact": r[0], "source": r[1]} for r in memories],
        "patterns": [{"question": r[0], "sql": r[1]} for r in patterns],
        "annotations": [{"table": r[0], "column": r[1], "annotation": r[2]} for r in annotations],
    }


@router.post("/{slug}/import-learnings")
async def import_learnings(slug: str, request: Request):
    """Import learnings from another project with deduplication."""
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()
    from_slug = body.get("from_slug", "")
    types = body.get("types", ["memories", "patterns", "annotations"])

    if not from_slug:
        return {"status": "error", "detail": "from_slug required"}

    imported = {"memories": 0, "patterns": 0, "annotations": 0}

    with _engine.connect() as conn:
        if "memories" in types:
            # Get existing facts to dedup
            existing = set(r[0] for r in conn.execute(text(
                "SELECT fact FROM public.dash_memories WHERE project_slug = :s"
            ), {"s": slug}).fetchall())

            source_mems = conn.execute(text(
                "SELECT fact FROM public.dash_memories "
                "WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE)"
            ), {"s": from_slug}).fetchall()

            for r in source_mems:
                if r[0] not in existing:
                    conn.execute(text(
                        "INSERT INTO public.dash_memories (project_slug, scope, fact, source) "
                        "VALUES (:s, 'project', :fact, 'transferred')"
                    ), {"s": slug, "fact": r[0]})
                    imported["memories"] += 1

        if "patterns" in types:
            existing_q = set(r[0] for r in conn.execute(text(
                "SELECT question FROM public.dash_query_patterns WHERE project_slug = :s"
            ), {"s": slug}).fetchall())

            source_patterns = conn.execute(text(
                "SELECT question, sql FROM public.dash_query_patterns "
                "WHERE project_slug = :s ORDER BY uses DESC LIMIT 20"
            ), {"s": from_slug}).fetchall()

            for r in source_patterns:
                if r[0] not in existing_q:
                    conn.execute(text(
                        "INSERT INTO public.dash_query_patterns (project_slug, question, sql) "
                        "VALUES (:s, :q, :sql)"
                    ), {"s": slug, "q": r[0], "sql": r[1]})
                    imported["patterns"] += 1

        if "annotations" in types:
            source_ann = conn.execute(text(
                "SELECT table_name, column_name, annotation FROM public.dash_annotations "
                "WHERE project_slug = :s"
            ), {"s": from_slug}).fetchall()

            for r in source_ann:
                conn.execute(text(
                    "INSERT INTO public.dash_annotations (project_slug, table_name, column_name, annotation, updated_by) "
                    "VALUES (:s, :t, :c, :a, 'transferred') "
                    "ON CONFLICT (project_slug, table_name, column_name) DO NOTHING"
                ), {"s": slug, "t": r[0], "c": r[1], "a": r[2]})
                imported["annotations"] += 1

        conn.commit()

    return {"status": "ok", "imported": imported}


# ---------------------------------------------------------------------------
# Self-Evaluation Loop (Feature 9)
# ---------------------------------------------------------------------------

@router.get("/{slug}/eval-history")
def eval_history(slug: str, request: Request):
    """Get eval run history with trends."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        runs = conn.execute(text(
            "SELECT id, total, passed, partial, failed, average_score, regression_report, run_at "
            "FROM public.dash_eval_runs WHERE project_slug = :s ORDER BY run_at DESC LIMIT 20"
        ), {"s": slug}).fetchall()
    return {"runs": [
        {"id": r[0], "total": r[1], "passed": r[2], "partial": r[3], "failed": r[4],
         "average_score": r[5], "regression_report": r[6], "run_at": str(r[7])}
        for r in runs
    ]}


@router.post("/{slug}/self-evaluate")
async def self_evaluate(slug: str, request: Request):
    """Run all evals + compare to previous run + generate regression report."""
    from os import getenv
    import httpx

    user = _get_user(request)
    _check_access(user, slug)
    api_key = getenv("OPENROUTER_API_KEY", "")

    # Run all evals (reuse existing endpoint logic)
    eval_result = await run_evals(slug, request)

    if not eval_result.get("results"):
        return {"status": "ok", "eval_result": eval_result, "regression_report": None}

    # Get previous run for comparison
    with _engine.connect() as conn:
        prev_runs = conn.execute(text(
            "SELECT total, passed, partial, failed, average_score, run_at "
            "FROM public.dash_eval_runs WHERE project_slug = :s ORDER BY run_at DESC LIMIT 2"
        ), {"s": slug}).fetchall()

    if len(prev_runs) < 2 or not api_key:
        return {"status": "ok", "eval_result": eval_result, "regression_report": None}

    current = prev_runs[0]
    previous = prev_runs[1]

    prompt = f"""Compare these two evaluation runs for a data agent and identify regressions.

PREVIOUS RUN ({previous[5]}):
- Total: {previous[0]}, Passed: {previous[1]}, Partial: {previous[2]}, Failed: {previous[3]}
- Average score: {previous[4]}

CURRENT RUN:
- Total: {current[0]}, Passed: {current[1]}, Partial: {current[2]}, Failed: {current[3]}
- Average score: {current[4]}

CURRENT EVAL DETAILS:
{json.dumps([{{"q": r["question"], "score": r["score"], "reason": r.get("reasoning", "")[:100]}} for r in eval_result["results"]], indent=1)}

Generate a brief regression report. Highlight:
1. What improved vs what regressed
2. Likely causes of any regression
3. Suggested fixes

Respond with ONLY valid JSON:
{{"report": "Your regression report here...", "status": "improved|stable|regressed"}}"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": DEEP_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 500, "temperature": 0.1},
            timeout=30,
        )
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content.strip().strip("`").strip())
        report = parsed.get("report", "")
        status = parsed.get("status", "stable")

        # Save regression report to latest run
        with _engine.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_eval_runs SET regression_report = :report "
                "WHERE id = :id"
            ), {"report": report, "id": current[0] if hasattr(current, '__getitem__') else prev_runs[0][0]})
            conn.commit()

        return {"status": "ok", "eval_result": eval_result, "regression_report": report, "trend": status}
    except Exception:
        return {"status": "ok", "eval_result": eval_result, "regression_report": None}


# ---------------------------------------------------------------------------
# Version Tracking & Rollback (Feature A — Autogenesis)
# ---------------------------------------------------------------------------

@router.post("/{slug}/memories/{memory_id}/rollback")
def rollback_memory(slug: str, memory_id: int, request: Request):
    """Rollback a memory to its parent version."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT parent_id, fact FROM public.dash_memories WHERE id = :id AND project_slug = :s"
        ), {"id": memory_id, "s": slug}).fetchone()
        if not row:
            return {"status": "error", "detail": "Memory not found"}
        if not row[0]:
            return {"status": "error", "detail": "No parent version to rollback to"}
        # Archive current, restore parent
        conn.execute(text("UPDATE public.dash_memories SET archived = TRUE WHERE id = :id"), {"id": memory_id})
        conn.execute(text("UPDATE public.dash_memories SET archived = FALSE WHERE id = :pid"), {"pid": row[0]})
        conn.commit()
    return {"status": "ok", "restored_id": row[0]}


@router.post("/{slug}/query-patterns/{pattern_id}/rollback")
def rollback_pattern(slug: str, pattern_id: int, request: Request):
    """Delete a query pattern (rollback)."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM public.dash_query_patterns WHERE id = :id AND project_slug = :s"
        ), {"id": pattern_id, "s": slug})
        conn.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Resource Registry (Feature B — Autogenesis)
# ---------------------------------------------------------------------------

def _compute_registry(slug: str) -> list[dict]:
    """Compute health scores for all resource types."""
    from datetime import datetime, timedelta, timezone
    registry = []
    with _engine.connect() as conn:
        now = datetime.now(timezone.utc)

        def _stale_days(latest):
            # tz-safe: DB columns may come back naive (timestamp) or aware
            # (timestamptz); naive - aware raises TypeError. Coerce to aware UTC.
            if not latest:
                return 999
            if getattr(latest, "tzinfo", None) is None:
                latest = latest.replace(tzinfo=timezone.utc)
            return (now - latest).days

        # Memories
        mem_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_memories WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE)"), {"s": slug}).scalar() or 0
        mem_latest = conn.execute(text("SELECT MAX(created_at) FROM public.dash_memories WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE)"), {"s": slug}).scalar()
        mem_stale = _stale_days(mem_latest)
        mem_health = min(100, mem_count * 10) - (20 if mem_stale > 30 else 0)
        registry.append({"type": "memory", "count": mem_count, "health": max(0, mem_health), "staleness": mem_stale})

        # Query patterns
        pat_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_query_patterns WHERE project_slug = :s"), {"s": slug}).scalar() or 0
        pat_latest = conn.execute(text("SELECT MAX(last_used) FROM public.dash_query_patterns WHERE project_slug = :s"), {"s": slug}).scalar()
        pat_stale = _stale_days(pat_latest)
        pat_health = min(100, pat_count * 20) - (20 if pat_stale > 14 else 0)
        registry.append({"type": "pattern", "count": pat_count, "health": max(0, pat_health), "staleness": pat_stale})

        # Rules
        rule_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_rules_db WHERE project_slug = :s"), {"s": slug}).scalar() or 0
        rule_health = min(100, rule_count * 25)
        registry.append({"type": "rule", "count": rule_count, "health": max(0, rule_health), "staleness": 0})

        # Evolved instructions
        inst_row = conn.execute(text("SELECT version, created_at FROM public.dash_evolved_instructions WHERE project_slug = :s ORDER BY version DESC LIMIT 1"), {"s": slug}).fetchone()
        inst_health = 100 if inst_row else 0
        inst_stale = _stale_days(inst_row[1] if inst_row else None)
        registry.append({"type": "instruction", "count": inst_row[0] if inst_row else 0, "health": max(0, inst_health - (20 if inst_stale > 30 else 0)), "staleness": inst_stale})

        # Annotations
        ann_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_annotations WHERE project_slug = :s"), {"s": slug}).scalar() or 0
        ann_health = min(100, ann_count * 15)
        registry.append({"type": "annotation", "count": ann_count, "health": max(0, ann_health), "staleness": 0})

        # Evals
        eval_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_evals WHERE project_slug = :s"), {"s": slug}).scalar() or 0
        eval_latest = conn.execute(text("SELECT MAX(last_run_at) FROM public.dash_evals WHERE project_slug = :s"), {"s": slug}).scalar()
        eval_stale = _stale_days(eval_latest)
        eval_health = min(100, eval_count * 20) - (30 if eval_stale > 7 else 0)
        registry.append({"type": "eval", "count": eval_count, "health": max(0, eval_health), "staleness": eval_stale})

        # Workflows
        wf_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_workflows_db WHERE project_slug = :s"), {"s": slug}).scalar() or 0
        wf_health = min(100, wf_count * 30)
        registry.append({"type": "workflow", "count": wf_count, "health": max(0, wf_health), "staleness": 0})

        # Feedback
        fb_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_feedback WHERE project_slug = :s"), {"s": slug}).scalar() or 0
        fb_health = min(100, fb_count * 10)
        registry.append({"type": "feedback", "count": fb_count, "health": max(0, fb_health), "staleness": 0})

        # Meta-learnings
        ml_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_meta_learnings WHERE project_slug = :s"), {"s": slug}).scalar() or 0
        ml_health = min(100, ml_count * 15)
        registry.append({"type": "meta_learning", "count": ml_count, "health": max(0, ml_health), "staleness": 0})

    # Compute overall health
    total_health = sum(r["health"] for r in registry) // len(registry) if registry else 0
    return registry, total_health


@router.get("/{slug}/resource-registry")
def get_resource_registry(slug: str, request: Request):
    """Get unified resource registry with health scores."""
    user = _get_user(request)
    _check_access(user, slug)
    registry, overall = _compute_registry(slug)
    return {"resources": registry, "overall_health": overall}


@router.post("/{slug}/resource-registry/refresh")
def refresh_resource_registry(slug: str, request: Request):
    """Recompute and save resource registry."""
    user = _get_user(request)
    _check_access(user, slug)
    registry, overall = _compute_registry(slug)

    with _engine.connect() as conn:
        for r in registry:
            conn.execute(text(
                "INSERT INTO public.dash_resource_registry (project_slug, resource_type, resource_count, health_score, staleness_days) "
                "VALUES (:s, :t, :c, :h, :st) "
                "ON CONFLICT (project_slug, resource_type) DO UPDATE SET resource_count = :c, health_score = :h, staleness_days = :st, last_updated = NOW()"
            ), {"s": slug, "t": r["type"], "c": r["count"], "h": r["health"], "st": r["staleness"]})
        conn.commit()

    return {"status": "ok", "resources": registry, "overall_health": overall}


# ---------------------------------------------------------------------------
# Formal Evolution Cycle (Feature C — Autogenesis)
# ---------------------------------------------------------------------------

@router.get("/{slug}/evolution-history")
def evolution_history(slug: str, request: Request):
    """List past evolution runs."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, status, steps_completed, reflect_result, select_result, improve_result, evaluate_result, commit_result, started_at, finished_at "
            "FROM public.dash_evolution_runs WHERE project_slug = :s ORDER BY started_at DESC LIMIT 10"
        ), {"s": slug}).fetchall()
    return {"runs": [
        {"id": r[0], "status": r[1], "steps": r[2], "reflect": r[3], "select": r[4], "improve": r[5], "evaluate": r[6], "commit": r[7], "started_at": str(r[8]), "finished_at": str(r[9]) if r[9] else None}
        for r in rows
    ]}


@router.post("/{slug}/evolve")
async def evolve(slug: str, request: Request):
    """Run the full Autogenesis evolution cycle: Reflect → Select → Improve → Evaluate → Commit."""
    from os import getenv
    import httpx

    user = _get_user(request)
    _check_access(user, slug)
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"status": "error", "detail": "No API key configured"}

    # Create evolution run record
    with _engine.connect() as conn:
        run_row = conn.execute(text(
            "INSERT INTO public.dash_evolution_runs (project_slug) VALUES (:s) RETURNING id"
        ), {"s": slug})
        run_id = run_row.fetchone()[0]
        conn.commit()

    results = {}

    ALLOWED_STEPS = {"reflect", "select", "improve", "evaluate", "commit"}

    def _update_run(step: str, result: str, status: str = "running"):
        if step not in ALLOWED_STEPS:
            return
        with _engine.connect() as conn:
            conn.execute(text(
                f"UPDATE public.dash_evolution_runs SET {step}_result = :r, status = :st, "
                f"steps_completed = steps_completed || CAST(:step AS jsonb) "
                f"WHERE id = :id"
            ), {"r": result[:1000], "st": status, "step": json.dumps([step]), "id": run_id})
            conn.commit()

    try:
        # === STEP 1: REFLECT ===
        # Analyze recent quality scores and feedback to identify weaknesses
        with _engine.connect() as conn:
            scores = conn.execute(text(
                "SELECT AVG(score), COUNT(*) FROM public.dash_quality_scores WHERE project_slug = :s"
            ), {"s": slug}).fetchone()
            bad_fb = conn.execute(text(
                "SELECT COUNT(*) FROM public.dash_feedback WHERE project_slug = :s AND rating = 'down'"
            ), {"s": slug}).scalar() or 0
            good_fb = conn.execute(text(
                "SELECT COUNT(*) FROM public.dash_feedback WHERE project_slug = :s AND rating = 'up'"
            ), {"s": slug}).scalar() or 0

        avg_score = round(scores[0], 1) if scores[0] else 0
        total_chats = scores[1] or 0
        reflect_result = f"Avg quality: {avg_score}/5, Chats: {total_chats}, Good feedback: {good_fb}, Bad feedback: {bad_fb}"
        _update_run("reflect", reflect_result)
        results["reflect"] = reflect_result

        # === STEP 2: SELECT ===
        # Pick resources with lowest health scores
        registry, overall = _compute_registry(slug)
        weakest = sorted(registry, key=lambda r: r["health"])[:3]
        weakest_str = ", ".join(f"{w['type']}({w['health']})" for w in weakest)
        select_result = f"Overall health: {overall}/100. Weakest: {weakest_str}"
        _update_run("select", select_result)
        results["select"] = select_result

        # === STEP 3: IMPROVE ===
        improvements = []

        # Consolidate memories if enough exist
        with _engine.connect() as conn:
            mem_count = conn.execute(text(
                "SELECT COUNT(*) FROM public.dash_memories WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE)"
            ), {"s": slug}).scalar() or 0

        if mem_count >= 20:
            # Trigger consolidation internally
            try:
                mem_rows = []
                with _engine.connect() as conn:
                    mem_rows = conn.execute(text(
                        "SELECT id, fact FROM public.dash_memories WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE) ORDER BY created_at DESC LIMIT 50"
                    ), {"s": slug}).fetchall()

                if mem_rows:
                    facts_text = "\n".join(f"- {r[1]}" for r in mem_rows)
                    resp = httpx.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"model": DEEP_MODEL, "messages": [{"role": "user", "content": f"Consolidate these {len(mem_rows)} facts into 5-8 insights:\n{facts_text}\n\nReturn JSON: {{\"insights\": [\"...\"]}}"}], "max_tokens": 800, "temperature": 0.1},
                        timeout=30,
                    )
                    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    parsed = json.loads(content.strip().strip("`").strip())
                    consolidated = parsed.get("insights", [])

                    if consolidated:
                        max_id = max(r[0] for r in mem_rows)
                        with _engine.connect() as conn:
                            conn.execute(text("UPDATE public.dash_memories SET archived = TRUE WHERE id = ANY(:ids)"), {"ids": [r[0] for r in mem_rows]})
                            for fact in consolidated:
                                conn.execute(text(
                                    "INSERT INTO public.dash_memories (project_slug, scope, fact, source, parent_id) VALUES (:s, 'project', :f, 'consolidated', :pid)"
                                ), {"s": slug, "f": fact, "pid": max_id})
                            conn.commit()
                        improvements.append(f"Consolidated {len(mem_rows)} memories → {len(consolidated)} insights")
            except Exception:
                pass

        # Evolve instructions
        try:
            from dash.tools.auto_evolve import auto_evolve_instructions
            auto_evolve_instructions(slug)
            improvements.append("Evolved instructions to new version")
        except Exception:
            pass

        # Mine patterns for new workflows
        try:
            with _engine.connect() as conn:
                q_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_feedback WHERE project_slug = :s"), {"s": slug}).scalar() or 0
            if q_count >= 10:
                improvements.append("Checked for workflow patterns")
        except Exception:
            pass

        improve_result = "; ".join(improvements) if improvements else "No improvements needed"
        _update_run("improve", improve_result)
        results["improve"] = improve_result

        # === STEP 4: EVALUATE ===
        with _engine.connect() as conn:
            eval_count = conn.execute(text("SELECT COUNT(*) FROM public.dash_evals WHERE project_slug = :s"), {"s": slug}).scalar() or 0

        if eval_count > 0:
            # Run evals
            try:
                eval_result = await run_evals(slug, request)
                evaluate_result = f"Evals: {eval_result.get('passed', 0)}/{eval_result.get('total', 0)} passed"
            except Exception:
                evaluate_result = "Eval run failed"
        else:
            evaluate_result = "No evals configured — skipped"
        _update_run("evaluate", evaluate_result)
        results["evaluate"] = evaluate_result

        # === STEP 5: COMMIT ===
        # Refresh resource registry with new state
        registry, overall = _compute_registry(slug)
        with _engine.connect() as conn:
            for r in registry:
                conn.execute(text(
                    "INSERT INTO public.dash_resource_registry (project_slug, resource_type, resource_count, health_score, staleness_days) "
                    "VALUES (:s, :t, :c, :h, :st) "
                    "ON CONFLICT (project_slug, resource_type) DO UPDATE SET resource_count = :c, health_score = :h, staleness_days = :st, last_updated = NOW()"
                ), {"s": slug, "t": r["type"], "c": r["count"], "h": r["health"], "st": r["staleness"]})
            conn.commit()

        commit_result = f"Registry updated. New overall health: {overall}/100"
        _update_run("commit", commit_result, status="completed")
        results["commit"] = commit_result

        # Mark finished
        with _engine.connect() as conn:
            conn.execute(text("UPDATE public.dash_evolution_runs SET finished_at = NOW() WHERE id = :id"), {"id": run_id})
            conn.commit()

        return {"status": "ok", "run_id": run_id, "results": results, "overall_health": overall}

    except Exception as e:
        _update_run("commit", f"Error: {str(e)}", status="failed")
        return {"status": "error", "detail": str(e), "run_id": run_id, "results": results}


# ── SkillRefinery (Phase 2) ──────────────────────────────────────────
@router.post("/{slug}/refine-tools/score")
def refine_tools_score(slug: str, window_days: int = 14):
    """Manually trigger SkillRefinery utility scoring for a project."""
    from dash.tools.skill_refinery import compute_utility_scores
    rows = compute_utility_scores(project_slug=slug, window_days=window_days)
    return {"status": "ok", "project_slug": slug, "tools_scored": len(rows), "scores": rows}


@router.get("/{slug}/refine-tools/scores")
@router.get("/{slug}/tool-health")
def refine_tools_scores_get(slug: str):
    """Read latest stored utility scores for a project."""
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT tool_name, score, success_rate, feedback_score, "
            " latency_p50_ms, latency_p95_ms, calls, fails, last_error, computed_at "
            "FROM public.dash_tool_scores "
            "WHERE project_slug = :s OR project_slug IS NULL "
            "ORDER BY score ASC"
        ), {"s": slug}).mappings().all()
    return {"project_slug": slug, "scores": [dict(r) for r in rows]}


@router.get("/{slug}/eval-health")
def eval_health(slug: str):
    """Latest golden-eval run summary — feeds the dashboard Eval Health card."""
    with _engine.connect() as conn:
        r = conn.execute(text(
            "SELECT id, total, passed, partial, failed, average_score, run_at "
            "FROM public.dash_eval_runs "
            "WHERE project_slug = :s "
            "ORDER BY run_at DESC NULLS LAST, id DESC LIMIT 1"
        ), {"s": slug}).mappings().first()
    if not r:
        return {"project_slug": slug, "has_data": False}
    total = int(r["total"] or 0)
    passed = int(r["passed"] or 0)
    partial = int(r["partial"] or 0)
    failed = int(r["failed"] or 0)
    ok = passed + partial
    return {
        "project_slug": slug,
        "has_data": total > 0,
        "total": total,
        "passed": passed,
        "partial": partial,
        "failed": failed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "ok_rate": round(ok / total, 4) if total else 0.0,
        "average_score": float(r["average_score"] or 0),
        "run_at": str(r["run_at"]) if r["run_at"] else None,
    }


@router.get("/{slug}/semantic-layer")
def semantic_layer(slug: str):
    """Engineer-built materialized views for a project (name, purpose, grain,
    live row count). Feeds the Data Source → Semantic Layer UI panel."""
    try:
        from dash.admin.settings import get_setting as _gs
        _v = _gs("engineer_semantic_layer")
        enabled = bool(_v) if _v is not None else True
    except Exception:
        import os as _os
        enabled = _os.environ.get("ENGINEER_SEMANTIC_LAYER") in ("1", "true", "True")
    try:
        from db import db_url as _du
        from dash.training.semantic_layer import list_semantic_layer
    except Exception:
        return {"project_slug": slug, "enabled": enabled, "matviews": []}
    mvs = list_semantic_layer(slug, _du)
    schema = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]
    for m in mvs:
        try:
            with _engine.connect() as conn:
                conn.execute(text(f"SET search_path = {schema}, public"))
                m["rows"] = int(conn.execute(
                    text(f'SELECT COUNT(*) FROM {schema}."{m["name"]}"')).scalar() or 0)
        except Exception:
            m["rows"] = None
    return {"project_slug": slug, "enabled": enabled, "matviews": mvs}


@router.get("/{slug}/training-flow")
def training_flow(slug: str, request: Request):
    """Read-only aggregate that feeds the training-flow visualization.
    Returns layers (static + live status), kpis, stores, flags."""
    user = _get_user(request)
    _check_access(user, slug)
    try:
        from db import db_url as _du
        from dash.training.flow_map import derive_flow
    except Exception as exc:
        raise HTTPException(500, f"flow_map import failed: {exc}")
    return derive_flow(slug, _du)


# Per-store detail for the training-flow DATA STORES rail. Clicking a store row
# opens a modal that calls this — live sample rows (top 20) + a "what this holds"
# blurb. Every query is fail-soft: a missing/empty store returns rows:[] not 500.
_STORE_SPEC = {
    "STAGE": {
        "label": "Staging files", "table": "dash_extraction_plans",
        "blurb": "Files received in the STAGING layer before any DB write — one row per sheet, with the chosen ingest strategy.",
        "columns": ["file", "sheet", "rows_in", "strategy"],
        "sql": "SELECT source_file, sheet_name, row_count_in, strategy FROM public.dash_extraction_plans "
               "WHERE project_slug=:s ORDER BY created_at DESC NULLS LAST LIMIT 20",
        "count": "SELECT COUNT(*) FROM public.dash_extraction_plans WHERE project_slug=:s",
    },
    "PG": {
        "label": "Postgres tables", "table": "{sc}.<tables>",
        "blurb": "The actual data tables promoted into your project schema. Rows from table metadata; columns from the live schema.",
        "columns": ["table", "rows", "cols"],
        "sql": "SELECT t.table_name, COALESCE(m.row_count,0), "
               "(SELECT COUNT(*) FROM information_schema.columns c WHERE c.table_schema=:sc AND c.table_name=t.table_name) "
               "FROM information_schema.tables t "
               "LEFT JOIN public.dash_table_metadata m ON m.table_name=t.table_name AND m.project_slug=:s "
               "WHERE t.table_schema=:sc AND t.table_type='BASE TABLE' ORDER BY 2 DESC LIMIT 20",
        "count": "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=:sc AND table_type='BASE TABLE'",
    },
    "META": {
        "label": "Table metadata", "table": "dash_table_metadata",
        "blurb": "Per-table training fingerprint + row count. A table is 'trained' once its profile lands here.",
        "columns": ["table", "rows", "fingerprint", "updated"],
        "sql": "SELECT table_name, row_count, LEFT(COALESCE(fingerprint,''),8), to_char(updated_at,'YYYY-MM-DD HH24:MI') "
               "FROM public.dash_table_metadata WHERE project_slug=:s ORDER BY updated_at DESC NULLS LAST LIMIT 20",
        "count": "SELECT COUNT(*) FROM public.dash_table_metadata WHERE project_slug=:s",
    },
    "QA": {
        "label": "Training Q&A", "table": "dash_training_qa",
        "blurb": "Question→SQL pairs generated per table. These teach the agent how to answer real questions.",
        "columns": ["question", "table", "answer"],
        "sql": "SELECT question, table_name, LEFT(COALESCE(answer_template,''),90) FROM public.dash_training_qa "
               "WHERE project_slug=:s ORDER BY created_at DESC NULLS LAST LIMIT 20",
        "count": "SELECT COUNT(*) FROM public.dash_training_qa WHERE project_slug=:s",
    },
    "VEC": {
        "label": "PgVector", "table": "dash.dash_vectors",
        "blurb": "Embedding vectors for semantic search over tables, Q&A, and brain. Written in the EMBED layer.",
        "columns": ["source", "text", "updated"],
        "sql": "SELECT COALESCE(source_table, namespace, '—'), LEFT(COALESCE(text,''),90), to_char(updated_at,'YYYY-MM-DD HH24:MI') "
               "FROM dash.dash_vectors WHERE project_slug=:s ORDER BY updated_at DESC NULLS LAST LIMIT 20",
        "count": "SELECT COUNT(*) FROM dash.dash_vectors WHERE project_slug=:s",
    },
    "BRAIN": {
        "label": "Company brain", "table": "dash_company_brain",
        "blurb": "Curated facts: aliases, glossary, KPIs, formulas. Seeded + learned; injected into the agent's context.",
        "columns": ["category", "name", "value"],
        "sql": "SELECT category, name, LEFT(COALESCE(definition,''),90) FROM public.dash_company_brain "
               "WHERE project_slug=:s ORDER BY updated_at DESC NULLS LAST LIMIT 20",
        "count": "SELECT COUNT(*) FROM public.dash_company_brain WHERE project_slug=:s",
    },
    "REL": {
        "label": "Relationships", "table": "dash_relationships",
        "blurb": "Discovered table-to-table joins (foreign-key-like links) used for multi-table questions.",
        "columns": ["from", "to", "on", "conf"],
        "sql": "SELECT from_table, to_table, (from_column||' = '||to_column), ROUND(confidence::numeric,2) "
               "FROM public.dash_relationships WHERE project_slug=:s ORDER BY confidence DESC NULLS LAST LIMIT 20",
        "count": "SELECT COUNT(*) FROM public.dash_relationships WHERE project_slug=:s",
    },
    "MV": {
        "label": "Matviews", "table": "{sc}.<matviews>",
        "blurb": "Engineer-built materialized views — pre-joined / pre-aggregated so the agent reads fast.",
        "columns": ["matview"],
        "sql": "SELECT matviewname FROM pg_matviews WHERE schemaname=:sc ORDER BY matviewname LIMIT 20",
        "count": "SELECT COUNT(*) FROM pg_matviews WHERE schemaname=:sc",
    },
    "AGE": {
        "label": "Apache AGE graph", "table": "dash_knowledge_triples",
        "blurb": "Knowledge-graph triples (subject → predicate → object) backing graph-lane 2-hop reasoning.",
        "columns": ["subject", "predicate", "object"],
        "sql": "SELECT subject, predicate, object FROM public.dash_knowledge_triples "
               "WHERE project_slug=:s ORDER BY created_at DESC NULLS LAST LIMIT 20",
        "count": "SELECT COUNT(*) FROM public.dash_knowledge_triples WHERE project_slug=:s",
    },
    "ENR": {
        "label": "Catalog enrichment", "table": "{sc}.catalog_enrichment",
        "blurb": "LLM-suggested fills for missing catalog fields — suggestion-only, human-gated (never auto-applied).",
        "columns": ["article", "field", "original", "suggested", "conf"],
        "sql": "SELECT article_code, field, LEFT(COALESCE(original_value,''),30), LEFT(COALESCE(suggested_value,''),40), ROUND(confidence::numeric,2) "
               "FROM {sc}.catalog_enrichment ORDER BY confidence DESC NULLS LAST LIMIT 20",
        "count": "SELECT COUNT(*) FROM {sc}.catalog_enrichment",
    },
    "EVAL": {
        "label": "Eval runs", "table": "dash_eval_runs",
        "blurb": "Accuracy checks against the golden set. Each run = total / passed / average score.",
        "columns": ["when", "total", "passed", "score"],
        "sql": "SELECT to_char(run_at,'YYYY-MM-DD HH24:MI'), total, passed, ROUND(average_score::numeric,2) "
               "FROM public.dash_eval_runs WHERE project_slug=:s ORDER BY run_at DESC NULLS LAST LIMIT 20",
        "count": "SELECT COUNT(*) FROM public.dash_eval_runs WHERE project_slug=:s",
    },
}


@router.get("/{slug}/store-detail/{key}")
def store_detail(slug: str, key: str, request: Request):
    """Live detail for one DATA STORES rail item: top-20 sample rows + blurb."""
    user = _get_user(request)
    _check_access(user, slug)
    spec = _STORE_SPEC.get(key.upper())
    if not spec:
        raise HTTPException(404, f"unknown store '{key}'")
    schema = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]
    params = {"s": slug, "sc": schema}
    # display masking — hide internal table / engine names from the modal too
    try:
        from dash.training.flow_map import _OBFUSCATE as _OBF
    except Exception:
        _OBF = True
    _SMASK = {
        "STAGE": ("Intake",        "intake",         "Files received before load — one entry per sheet, with status + dedup hash."),
        "PG":    ("Primary store", "data store",     "Your loaded data tables. Rows promoted into the project after validation."),
        "META":  ("Metadata",      "metadata",       "Per-table profile + row count. A table is 'trained' once profiled."),
        "QA":    ("Training Q&A",  "training Q&A",   "Question→answer pairs generated per table to teach the agent."),
        "VEC":   ("Vector index",  "vector index",   "Embeddings for semantic search over tables, Q&A, and knowledge."),
        "BRAIN": ("Knowledge base","knowledge",      "Curated facts: aliases, glossary, KPIs, formulas — seeded + learned."),
        "REL":   ("Relationships", "links",          "Discovered table-to-table links used for multi-table questions."),
        "MV":    ("Managed views", "managed views",  "Managed views — pre-joined / pre-aggregated for fast reads."),
        "AGE":   ("Graph store",   "graph store",    "Network triples (subject → relation → object) backing multi-hop reasoning."),
        "ENR":   ("Enrichment",    "enrichment",     "Suggested fills for missing fields — suggestion-only, human-gated."),
        "EVAL":  ("Eval runs",     "eval runs",      "Accuracy checks against the golden set: total / passed / average score."),
    }
    _m = _SMASK.get(key.upper()) if _OBF else None
    out = {
        "key": key.upper(),
        "label": _m[0] if _m else spec["label"],
        "table": _m[1] if _m else spec["table"].format(sc=schema),
        "blurb": _m[2] if _m else spec["blurb"], "columns": spec["columns"],
        "rows": [], "count": None, "truncated": False, "note": None,
    }
    try:
        with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            try:
                out["count"] = int(conn.execute(text(spec["count"].format(sc=schema)), params).scalar() or 0)
            except Exception:
                out["count"] = None
            try:
                res = conn.execute(text(spec["sql"].format(sc=schema)), params).fetchall()
                out["rows"] = [[(None if v is None else str(v)) for v in r] for r in res]
                if out["count"] and len(out["rows"]) < out["count"]:
                    out["truncated"] = True
            except Exception as qe:
                out["note"] = "store is empty or not built yet"
                logger.debug("store_detail %s/%s query: %s", slug, key, qe)
    except Exception as e:
        out["note"] = "could not read store"
        logger.debug("store_detail %s/%s conn: %s", slug, key, e)
    return out


@router.get("/{slug}/dashboard-summary")
def dashboard_summary(slug: str, request: Request):
    """At-a-glance counts for the Dashboard chip grid + Brain rich card.
    One call → per-tab badge numbers (queries/lineage/rules/schedules/evals/
    learn/graph + brain breakdown). All counts fail-soft (missing table → 0)."""
    user = _get_user(request)
    _check_access(user, slug)
    schema = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]

    def _scalar(conn, sql, params=None):
        try:
            return int(conn.execute(text(sql), params or {}).scalar() or 0)
        except Exception:
            return 0

    out = {
        "project_slug": slug,
        "queries": 0, "lineage": 0, "rules": 0, "schedules": 0,
        "learn": 0, "triples": 0,
        "training_runs": 0,
        "evals": {"golden": 0, "runs": 0, "score": 0.0, "pass_rate": 0.0},
        "brain": {"definitions": 0, "glossary": 0, "kpi": 0, "patterns": 0,
                   "rules": 0, "total": 0},
        "schema": {"tables": 0, "columns": 0},
    }
    p = {"s": slug}
    try:
        with _engine.connect() as conn:
            out["queries"] = _scalar(conn, "SELECT COUNT(*) FROM public.dash_query_patterns WHERE project_slug=:s", p)
            out["lineage"] = _scalar(conn, "SELECT COUNT(*) FROM public.dash_relationships WHERE project_slug=:s", p)
            out["rules"] = _scalar(conn, "SELECT COUNT(*) FROM public.dash_rules_db WHERE project_slug=:s", p)
            out["schedules"] = _scalar(conn, "SELECT COUNT(*) FROM public.dash_schedules WHERE project_slug=:s", p)
            out["learn"] = _scalar(conn, "SELECT COUNT(*) FROM public.dash_self_learning_runs WHERE project_slug=:s", p)
            out["triples"] = _scalar(conn, "SELECT COUNT(*) FROM public.dash_knowledge_triples WHERE project_slug=:s", p)
            out["training_runs"] = _scalar(conn, "SELECT COUNT(*) FROM public.dash_training_runs WHERE project_slug=:s", p)

            # brain breakdown by category (formula=definitions, glossary, kpi)
            try:
                for row in conn.execute(text(
                    "SELECT category, COUNT(*) FROM public.dash_company_brain "
                    "WHERE project_slug=:s GROUP BY category"), p).fetchall():
                    cat = str(row[0] or "").lower()
                    n = int(row[1] or 0)
                    if cat == "formula":
                        out["brain"]["definitions"] = n
                    elif cat in ("glossary", "kpi"):
                        out["brain"][cat] = n
            except Exception:
                pass
            out["brain"]["patterns"] = out["queries"]
            out["brain"]["rules"] = out["rules"]
            out["brain"]["total"] = (out["brain"]["definitions"] + out["brain"]["glossary"]
                                      + out["brain"]["kpi"] + out["brain"]["patterns"] + out["brain"]["rules"])

            # evals: golden set size + run count + latest score / pass-rate
            out["evals"]["golden"] = _scalar(conn, "SELECT COUNT(*) FROM public.dash_evals WHERE project_slug=:s", p)
            out["evals"]["runs"] = _scalar(conn, "SELECT COUNT(*) FROM public.dash_eval_runs WHERE project_slug=:s", p)
            try:
                r = conn.execute(text(
                    "SELECT total, passed, average_score FROM public.dash_eval_runs "
                    "WHERE project_slug=:s ORDER BY run_at DESC NULLS LAST, id DESC LIMIT 1"), p).first()
                if r:
                    tot = int(r[0] or 0)
                    out["evals"]["score"] = round(float(r[2] or 0), 2)
                    out["evals"]["pass_rate"] = round((int(r[1] or 0) / tot), 4) if tot else 0.0
            except Exception:
                pass

            # schema: tables + total columns in the project data schema
            try:
                conn.execute(text(f"SET search_path = {schema}, public"))
                out["schema"]["tables"] = _scalar(conn,
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema=:sc AND table_type='BASE TABLE'", {"sc": schema})
                out["schema"]["columns"] = _scalar(conn,
                    "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=:sc", {"sc": schema})
            except Exception:
                pass
    except Exception as exc:
        out["error"] = str(exc)[:160]
    return out


@router.get("/{slug}/catalog-enrich/gaps")
def catalog_enrich_gaps(slug: str):
    """Per-field gap counts in the catalog + pending/approved suggestion counts.
    Feeds the Data Source -> Catalog Gaps UI tab."""
    try:
        from db import db_url as _du
        from app.catalog_enrich import detect_gaps, ensure_enrichment_table, CLINICAL_FIELDS
    except Exception as e:
        return {"project_slug": slug, "gaps": {}, "error": str(e)[:120]}
    gaps = detect_gaps(_du)
    counts = {"pending": 0, "approved": 0, "rejected": 0}
    try:
        ensure_enrichment_table(_du)
        with _engine.connect() as conn:
            for r in conn.execute(text(
                "SELECT status, COUNT(*) FROM citypharma.catalog_enrichment GROUP BY status"
            )).fetchall():
                counts[str(r[0])] = int(r[1])
    except Exception:
        pass
    return {"project_slug": slug, "gaps": gaps, "clinical_fields": sorted(CLINICAL_FIELDS),
            "suggestion_counts": counts}


@router.post("/{slug}/catalog-enrich/run")
def catalog_enrich_run(slug: str, limit: int = 50, fields: str = ""):
    """Generate suggestions for up to `limit` articles (suggestion-only — writes
    pending rows, never mutates the source catalog). `fields` = comma list or ''."""
    try:
        from db import db_url as _du
        from app.catalog_enrich import run_enrichment
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}
    flds = [f.strip() for f in fields.split(",") if f.strip()] or None
    try:
        res = run_enrichment(_du, limit=int(limit), fields=flds)
        return {"ok": True, **res}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.get("/{slug}/catalog-enrich/suggestions")
def catalog_enrich_suggestions(slug: str, status: str = "pending", field: str = "", limit: int = 200):
    """List enrichment suggestions for review."""
    try:
        from app.catalog_enrich import ensure_enrichment_table
        from db import db_url as _du
        ensure_enrichment_table(_du)
    except Exception:
        pass
    where = ["status = :st"]
    params = {"st": status, "lim": int(limit)}
    if field:
        where.append("field = :fld")
        params["fld"] = field
    rows = []
    try:
        with _engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(text(
                "SELECT id, article_code, field, original_value, suggested_value, "
                "confidence, model, status, reason, created_at "
                "FROM citypharma.catalog_enrichment "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY field, confidence DESC NULLS LAST, id LIMIT :lim"
            ), params).mappings().all()]
    except Exception as e:
        return {"suggestions": [], "error": str(e)[:120]}
    return {"suggestions": rows, "status": status}


@router.post("/{slug}/catalog-enrich/decide")
def catalog_enrich_decide(slug: str, payload: dict = Body(...)):
    """Approve or reject suggestions. payload = {ids:[int], decision:'approved'|'rejected'}.
    Approval only flips status here — it does NOT write the source table; the live
    read happens via the articles_enriched view (COALESCE source, approved)."""
    ids = payload.get("ids") or []
    decision = payload.get("decision")
    if decision not in ("approved", "rejected") or not isinstance(ids, list) or not ids:
        return {"ok": False, "error": "need ids[] and decision in approved/rejected"}
    try:
        ids = [int(i) for i in ids]
    except Exception:
        return {"ok": False, "error": "ids must be integers"}
    try:
        with _engine.begin() as conn:
            conn.execute(text(
                "UPDATE citypharma.catalog_enrichment SET status = :d WHERE id = ANY(:ids)"
            ), {"d": decision, "ids": ids})
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}
    return {"ok": True, "updated": len(ids), "decision": decision}


@router.get("/{slug}/refine-tools/{tool_name}/failures")
def refine_tools_failures(slug: str, tool_name: str, limit: int = 10):
    """Recent failed invocations of a tool for diagnostic drill-down."""
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, ts, latency_ms, error_class, error_message, args_hash, agent "
            "FROM dash.dash_tool_utility_scores "
            "WHERE project_slug = :s AND tool_name = :t AND success = false "
            "ORDER BY ts DESC LIMIT :n"
        ), {"s": slug, "t": tool_name, "n": int(limit)}).mappings().all()
    return {"tool_name": tool_name, "failures": [dict(r) for r in rows]}


@router.post("/{slug}/refine-tools/{tool_name}/propose")
def refine_tools_propose(slug: str, tool_name: str):
    """Generate a patch draft for a failing tool. Stores in dash_tool_patches with applied=false."""
    from dash.agents.skill_refiner import propose_patch

    with _engine.connect() as conn:
        score_row = conn.execute(text(
            "SELECT score, success_rate, calls, fails, latency_p50_ms, last_error "
            "FROM public.dash_tool_scores "
            "WHERE tool_name = :t AND (project_slug = :s OR project_slug IS NULL) "
            "ORDER BY project_slug NULLS LAST LIMIT 1"
        ), {"t": tool_name, "s": slug}).mappings().first()

        if not score_row:
            return {"status": "error", "detail": f"No score row for tool '{tool_name}'. Recompute first."}

        fails = conn.execute(text(
            "SELECT error_class, error_message, args_hash "
            "FROM dash.dash_tool_utility_scores "
            "WHERE project_slug = :s AND tool_name = :t AND success = false "
            "ORDER BY ts DESC LIMIT 10"
        ), {"s": slug, "t": tool_name}).mappings().all()

        active = conn.execute(text(
            "SELECT new_description FROM public.dash_tool_patches "
            "WHERE tool_name = :t AND (project_slug = :s OR project_slug IS NULL) "
            "  AND applied = TRUE AND reverted = FALSE "
            "ORDER BY version DESC LIMIT 1"
        ), {"t": tool_name, "s": slug}).first()
        old_description = (active[0] if active else "") or ""

        next_ver_row = conn.execute(text(
            "SELECT COALESCE(MAX(version), 0) + 1 "
            "FROM public.dash_tool_patches "
            "WHERE tool_name = :t AND (project_slug = :s OR project_slug IS NULL)"
        ), {"t": tool_name, "s": slug}).first()
        next_ver = int(next_ver_row[0]) if next_ver_row else 1

    try:
        proj_ctx_row = None
        with _engine.connect() as conn:
            proj_ctx_row = conn.execute(text(
                "SELECT agent_personality FROM public.dash_projects WHERE slug = :s"
            ), {"s": slug}).first()
        proj_ctx = (proj_ctx_row[0] if proj_ctx_row else "") or ""

        patch = propose_patch(
            tool_name=tool_name,
            old_description=old_description,
            score=float(score_row["score"]),
            success_rate=float(score_row["success_rate"] or 0),
            calls=int(score_row["calls"]),
            fails=int(score_row["fails"]),
            p50_ms=int(score_row["latency_p50_ms"] or 0),
            failures=[dict(f) for f in fails],
            project_context=proj_ctx,
        )
    except Exception as e:
        return {"status": "error", "detail": f"SkillRefiner failed: {e}"}

    with _engine.begin() as conn:
        new_id = conn.execute(text(
            "INSERT INTO public.dash_tool_patches "
            "(tool_name, project_slug, version, old_description, new_description, "
            " default_args, reason, failure_samples, score_before, source, applied) "
            "VALUES (:t, :s, :v, :old, :new, :args, :reason, :fails, :score, 'manual', FALSE) "
            "RETURNING id"
        ), {
            "t": tool_name, "s": slug, "v": next_ver,
            "old": old_description, "new": patch["new_description"],
            "args": json.dumps(patch["default_args"]),
            "reason": patch["reason"],
            "fails": json.dumps([dict(f) for f in fails]),
            "score": float(score_row["score"]),
        }).first()[0]

    return {
        "status": "ok",
        "patch_id": new_id,
        "version": next_ver,
        "tool_name": tool_name,
        "old_description": old_description,
        "new_description": patch["new_description"],
        "default_args": patch["default_args"],
        "reason": patch["reason"],
        "score_before": float(score_row["score"]),
    }


@router.get("/{slug}/refine-tools/patches")
def refine_tools_patches(slug: str, tool_name: str | None = None):
    """List patches for a project (optionally filtered to one tool)."""
    sql = (
        "SELECT id, tool_name, project_slug, version, old_description, new_description, "
        " default_args, reason, score_before, score_after, applied, applied_at, "
        " reverted, reverted_at, source, created_at "
        "FROM public.dash_tool_patches "
        "WHERE (project_slug = :s OR project_slug IS NULL)"
    )
    params = {"s": slug}
    if tool_name:
        sql += " AND tool_name = :t"
        params["t"] = tool_name
    sql += " ORDER BY created_at DESC LIMIT 50"
    with _engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return {"patches": [dict(r) for r in rows]}


def _pick_shadow_samples(slug: str, tool_name: str) -> tuple[list[dict], list[dict]]:
    """Last 5 failures + 3 successes for a tool/project."""
    with _engine.connect() as conn:
        fails = conn.execute(text(
            "SELECT error_class, error_message, args_hash, latency_ms "
            "FROM dash.dash_tool_utility_scores "
            "WHERE project_slug = :s AND tool_name = :t AND success = FALSE "
            "ORDER BY ts DESC LIMIT 5"
        ), {"s": slug, "t": tool_name}).mappings().all()
        succs = conn.execute(text(
            "SELECT args_hash, latency_ms "
            "FROM dash.dash_tool_utility_scores "
            "WHERE project_slug = :s AND tool_name = :t AND success = TRUE "
            "ORDER BY ts DESC LIMIT 3"
        ), {"s": slug, "t": tool_name}).mappings().all()
    return [dict(f) for f in fails], [dict(s) for s in succs]


@router.post("/{slug}/refine-tools/patches/{patch_id}/shadow")
def refine_tools_patch_shadow(slug: str, patch_id: int):
    """Run shadow validation on a draft patch. Saves shadow_pass_rate."""
    from dash.agents.skill_refiner import shadow_validate

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT tool_name, old_description, new_description, default_args "
            "FROM public.dash_tool_patches WHERE id = :id AND (project_slug = :s OR project_slug IS NULL)"
        ), {"id": patch_id, "s": slug}).first()
    if not row:
        return {"status": "error", "detail": "patch not found"}

    tool_name, old_d, new_d, args = row
    if isinstance(args, str):
        try: args = json.loads(args)
        except Exception: args = {}

    fails, succs = _pick_shadow_samples(slug, tool_name)
    if not fails:
        # No failures to validate against — give patch benefit of the doubt.
        result = {"pass_rate": 100, "verdicts": [], "summary": "no historical failures to validate"}
    else:
        try:
            result = shadow_validate(tool_name, old_d or "", new_d, args or {}, fails, succs)
        except Exception as e:
            return {"status": "error", "detail": f"shadow failed: {e}"}

    with _engine.begin() as conn:
        conn.execute(text(
            "UPDATE public.dash_tool_patches SET shadow_pass_rate = :pr WHERE id = :id"
        ), {"pr": result["pass_rate"], "id": patch_id})

    return {"status": "ok", "patch_id": patch_id, **result,
            "samples": {"failures": len(fails), "successes": len(succs)}}


@router.post("/{slug}/refine-tools/patches/{patch_id}/apply")
def refine_tools_patch_apply(slug: str, patch_id: int, force: bool = False):
    """Mark a patch applied. Blocks if shadow pass_rate < 60% unless force=true."""
    with _engine.begin() as conn:
        row = conn.execute(text(
            "SELECT tool_name, project_slug, shadow_pass_rate FROM public.dash_tool_patches WHERE id = :id"
        ), {"id": patch_id}).first()
        if not row:
            return {"status": "error", "detail": "patch not found"}
        # Phase 6 gate — require shadow validation pass.
        pr = row[2]
        if pr is None and not force:
            return {"status": "error", "detail": "run shadow validation first (POST .../shadow)"}
        if pr is not None and float(pr) < 60 and not force:
            return {"status": "error", "detail": f"shadow pass_rate {pr}% < 60% threshold (use force=true to override)"}
        # Demote any other active patch for same tool/project so only one is live.
        conn.execute(text(
            "UPDATE public.dash_tool_patches SET applied = FALSE "
            "WHERE tool_name = :t "
            "  AND (project_slug IS NOT DISTINCT FROM :s) "
            "  AND id <> :id"
        ), {"t": row[0], "s": row[1], "id": patch_id})
        conn.execute(text(
            "UPDATE public.dash_tool_patches "
            "SET applied = TRUE, applied_at = NOW(), reverted = FALSE, reverted_at = NULL "
            "WHERE id = :id"
        ), {"id": patch_id})
    try:
        from dash.tools.skill_refinery import invalidate_patch_cache
        invalidate_patch_cache()
    except Exception:
        pass
    return {"status": "ok", "patch_id": patch_id}


@router.post("/{slug}/refine-tools/cycle")
def refine_tools_cycle_one(slug: str):
    """Run SkillRefinery cycle for a single project (manual trigger)."""
    from dash.learning.skill_refinery_cycle import run_for_project
    return run_for_project(slug)


@router.post("/refine-tools/ab-check")
def refine_tools_ab_check():
    """Run A/B aging check on all applied patches >=24h old. Reverts regressions."""
    from dash.learning.skill_refinery_cycle import ab_revert_check
    return ab_revert_check()


@router.get("/{slug}/refine-tools/{tool_name}/history")
def refine_tools_history(slug: str, tool_name: str):
    """Patch version history for a tool."""
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, version, applied, reverted, source, "
            " score_before, score_after, shadow_pass_rate, "
            " applied_at, reverted_at, revert_reason, created_at, reason "
            "FROM public.dash_tool_patches "
            "WHERE tool_name = :t AND (project_slug = :s OR project_slug IS NULL) "
            "ORDER BY version DESC"
        ), {"t": tool_name, "s": slug}).mappings().all()
    return {"tool_name": tool_name, "history": [dict(r) for r in rows]}


@router.post("/refine-tools/cycle-all")
def refine_tools_cycle_all():
    """Run SkillRefinery cycle for ALL projects. Called by nightly CronJob."""
    from dash.learning.skill_refinery_cycle import run_for_all_projects
    results = run_for_all_projects()
    totals = {
        "projects": len(results),
        "applied": sum(r.get("applied", 0) for r in results),
        "drafted": sum(r.get("drafted", 0) for r in results),
        "shadow_blocked": sum(r.get("shadow_blocked", 0) for r in results),
        "skipped_cooldown": sum(r.get("skipped_cooldown", 0) for r in results),
        "skipped_cap": sum(r.get("skipped_cap", 0) for r in results),
    }
    return {"status": "ok", "totals": totals, "projects": results}


@router.get("/{slug}/feature-config")
def feature_config_get(slug: str):
    from dash.feature_config import get_feature_config
    # Industry preset PRESETS dict removed; presets list is now empty.
    return {"slug": slug, "config": get_feature_config(slug), "presets": []}


@router.patch("/{slug}/feature-config")
async def feature_config_patch(slug: str, request: Request):
    from dash.feature_config import set_feature_config
    body = await request.json()
    try:
        merged = set_feature_config(slug, body or {})
    except Exception as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok", "slug": slug, "config": merged}


@router.post("/{slug}/feature-config/preset/{preset}")
def feature_config_preset(slug: str, preset: str):
    # Industry preset apply_preset() removed. Endpoint kept for API
    # compatibility but always returns an error.
    return {"status": "error", "detail": f"presets removed; unknown preset '{preset}'"}


@router.get("/{slug}/codex-enriched")
def codex_enriched_tables(slug: str):
    """Tables whose Layer-3 metadata was enriched from source pipeline code.

    Powers the CODE badge in DATASETS. Reads dash_table_metadata (DB), which is
    where dash/tools/codex_code.py persists pipeline_logic. Fail-soft → [].
    """
    try:
        from dash.tools.skill_refinery import _get_engine
        from sqlalchemy import text
        eng = _get_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT table_name FROM public.dash_table_metadata "
                "WHERE project_slug = :s "
                "AND (metadata #> '{pipeline_logic,code_enriched}')::text = 'true'"
            ), {"s": slug}).fetchall()
        return {"tables": [r[0] for r in rows]}
    except Exception as e:  # noqa: BLE001
        return {"tables": [], "error": str(e)}


@router.get("/{slug}/feature-config/recommend")
def feature_config_recommend(slug: str):
    """Smart-recommend a feature_config from the project's trained schema.

    Read-only — does NOT persist. Frontend shows reasons + an APPLY button
    that PATCHes the returned config.
    """
    from dash.feature_config import derive_recommended_config
    try:
        return {"status": "ok", "slug": slug, **derive_recommended_config(slug)}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "detail": str(e)}


@router.post("/{slug}/feature-config/derive-scope")
def feature_config_derive_scope(slug: str):
    """Re-derive scope guardrail from current data + docs (one-shot, no full retrain)."""
    try:
        from dash.scope_deriver import derive_scope
        from dash.feature_config import set_scope, get_feature_config
        scope = derive_scope(slug)
        set_scope(slug, scope, mark_auto=True)
        return {"status": "ok", "scope": scope, "config": get_feature_config(slug)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.patch("/{slug}/feature-config/scope")
async def feature_config_scope_patch(slug: str, request: Request):
    """Manually edit scope (overrides auto-derived)."""
    try:
        from dash.feature_config import set_scope, get_feature_config
        body = await request.json()
        # mark_auto=False so future TRAIN ALL won't blindly overwrite manual edits.
        set_scope(slug, body or {}, mark_auto=False)
        return {"status": "ok", "config": get_feature_config(slug)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ---------------------------------------------------------------------------
# RLS Config (Phase 1) — per-project row-level access control
# ---------------------------------------------------------------------------

def _ensure_rls_table():
    """Create dash_project_rls_config if missing (idempotent)."""
    with _engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dash_project_rls_config (
                project_slug TEXT PRIMARY KEY,
                enabled BOOL NOT NULL DEFAULT false,
                mode TEXT NOT NULL DEFAULT 'advisory',
                user_attr_keys TEXT[] NOT NULL DEFAULT '{}',
                table_filters JSONB NOT NULL DEFAULT '{}'::jsonb,
                default_deny BOOL NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))


_VALID_RLS_MODES = {"advisory", "rewrite", "pg_rls"}


def _load_rls_config(slug: str) -> dict:
    _ensure_rls_table()
    with _engine.connect() as conn:
        try:
            row = conn.execute(text("""
                SELECT enabled, mode, user_attr_keys, table_filters, default_deny,
                       bypass_roles, created_at, updated_at
                FROM dash_project_rls_config WHERE project_slug = :s
            """), {"s": slug}).mappings().first()
        except Exception:
            row = conn.execute(text("""
                SELECT enabled, mode, user_attr_keys, table_filters, default_deny,
                       created_at, updated_at
                FROM dash_project_rls_config WHERE project_slug = :s
            """), {"s": slug}).mappings().first()
    if not row:
        return {
            "enabled": False, "mode": "advisory",
            "user_attr_keys": [], "table_filters": {},
            "default_deny": True,
            "bypass_roles": ["admin", "super_admin"],
        }
    d = dict(row)
    tf = d.get("table_filters")
    if isinstance(tf, str):
        try: d["table_filters"] = json.loads(tf)
        except Exception: d["table_filters"] = {}
    br = d.get("bypass_roles")
    if isinstance(br, str):
        try: d["bypass_roles"] = json.loads(br)
        except Exception: d["bypass_roles"] = ["admin", "super_admin"]
    elif br is None:
        d["bypass_roles"] = ["admin", "super_admin"]
    return d


@router.get("/{slug}/rls-config")
def rls_config_get(slug: str, request: Request):
    """Get per-project RLS config."""
    user = _get_user(request)
    _check_access(user, slug)
    return {"slug": slug, "config": _load_rls_config(slug)}


@router.get("/{slug}/rls-audit")
def rls_audit_list(slug: str, request: Request, days: int = 14, limit: int = 100,
                   blocked_only: bool = False):
    """Recent RLS audit events for this project."""
    user = _get_user(request)
    _check_access(user, slug)
    d = max(1, min(int(days), 365))
    n = max(1, min(int(limit), 1000))
    _ensure_rls_audit_table()
    where = "project_slug = :s AND created_at >= NOW() - INTERVAL '" + str(d) + " days'"
    if blocked_only:
        where += " AND blocked = true"
    with _engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, project_slug, user_attrs, external_user, embed_id,
                   original_sql, rewritten_sql, mode, blocked, block_reason, created_at
            FROM dash_rls_audit
            WHERE {where}
            ORDER BY created_at DESC LIMIT :n
        """), {"s": slug, "n": n}).mappings().all()
    return {"events": [dict(r) for r in rows]}


def _ensure_rls_audit_table():
    with _engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dash_rls_audit (
                id BIGSERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                user_attrs JSONB,
                external_user TEXT,
                embed_id TEXT,
                original_sql TEXT NOT NULL,
                rewritten_sql TEXT,
                mode TEXT,
                blocked BOOL DEFAULT false,
                block_reason TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))


@router.patch("/{slug}/rls-config")
async def rls_config_patch(slug: str, request: Request):
    """Update per-project RLS config. Project owner controls; not platform admin."""
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json() or {}

    enabled = bool(body.get("enabled", False))
    mode = str(body.get("mode", "advisory")).lower()
    if mode not in _VALID_RLS_MODES:
        return {"status": "error", "detail": f"mode must be one of {sorted(_VALID_RLS_MODES)}"}

    keys = body.get("user_attr_keys", []) or []
    if not isinstance(keys, list) or not all(isinstance(k, str) and k for k in keys):
        return {"status": "error", "detail": "user_attr_keys must be list of non-empty strings"}
    keys = [k.strip() for k in keys][:16]

    filters = body.get("table_filters", {}) or {}
    if not isinstance(filters, dict):
        return {"status": "error", "detail": "table_filters must be object"}
    # Cap + strict validation. Issue #5: reject non-string expressions with explicit 400.
    cleaned: dict = {}
    for tname, expr in list(filters.items())[:128]:
        if not isinstance(tname, str):
            return {"status": "error", "detail": f"table_filters keys must be strings, got {type(tname).__name__}"}
        if not isinstance(expr, str):
            return {
                "status": "error",
                "detail": (
                    f"table_filters['{tname}'] must be a string SQL expression "
                    f"(e.g. 'site_code = :site_code'), got {type(expr).__name__}"
                ),
            }
        if any(c in expr for c in (";", "--", "/*", "*/")):
            return {"status": "error", "detail": f"filter for '{tname}' contains forbidden chars (; -- /* */)"}
        cleaned[tname.strip()] = expr.strip()

    default_deny = bool(body.get("default_deny", True))

    # Issue #2 / #14 — accept bypass_roles (list of role names).
    bypass_roles = body.get("bypass_roles", None)
    if bypass_roles is None:
        bypass_roles = ["admin", "super_admin"]
    if not isinstance(bypass_roles, list) or not all(isinstance(r, str) and r for r in bypass_roles):
        return {"status": "error", "detail": "bypass_roles must be a list of non-empty strings"}
    bypass_roles = [r.strip() for r in bypass_roles][:16]

    _ensure_rls_table()
    # Ensure column exists (idempotent guard if migration 098 hasn't run yet).
    try:
        with _engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE dash_project_rls_config "
                "ADD COLUMN IF NOT EXISTS bypass_roles JSONB NOT NULL DEFAULT '[\"admin\", \"super_admin\"]'::jsonb"
            ))
    except Exception:
        pass
    with _engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dash_project_rls_config
                (project_slug, enabled, mode, user_attr_keys, table_filters, default_deny, bypass_roles, updated_at)
            VALUES (:s, :en, :m, :k, CAST(:f AS JSONB), :dd, CAST(:br AS JSONB), now())
            ON CONFLICT (project_slug) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                mode = EXCLUDED.mode,
                user_attr_keys = EXCLUDED.user_attr_keys,
                table_filters = EXCLUDED.table_filters,
                default_deny = EXCLUDED.default_deny,
                bypass_roles = EXCLUDED.bypass_roles,
                updated_at = now()
        """), {
            "s": slug, "en": enabled, "m": mode, "k": keys,
            "f": json.dumps(cleaned), "dd": default_deny,
            "br": json.dumps(bypass_roles),
        })
    return {"status": "ok", "slug": slug, "config": _load_rls_config(slug)}


@router.post("/{slug}/rls-config/test")
async def rls_config_test(slug: str, request: Request):
    """Dry-run test: simulate RLS rewrite for a sample SQL + user_attrs."""
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json() or {}
    sql = (body.get("sql") or "").strip()
    user_attrs = body.get("user_attrs") or {}
    if not sql:
        return {"status": "error", "detail": "sql required"}
    if not isinstance(user_attrs, dict):
        return {"status": "error", "detail": "user_attrs must be object"}

    cfg = _load_rls_config(slug)
    result = {"original_sql": sql, "user_attrs": user_attrs, "config": cfg}

    # Try Phase 3 rewriter if available; fallback to advisory preview.
    try:
        from dash.rls.rewriter import rewrite as _rls_rewrite  # type: ignore
        rewritten = _rls_rewrite(sql, slug, user_attrs)
        result["rewritten_sql"] = rewritten
        result["mode_applied"] = cfg.get("mode")
        result["rewriter_available"] = True
    except Exception as e:
        result["rewriter_available"] = False
        result["rewriter_error"] = str(e)
        # Advisory preview: show which filters WOULD apply.
        applied = []
        sql_lc = sql.lower()
        for tbl, expr in (cfg.get("table_filters") or {}).items():
            if tbl.lower() in sql_lc:
                bound = expr
                for k, v in user_attrs.items():
                    bound = bound.replace(f":{k}", repr(v))
                applied.append({"table": tbl, "filter": expr, "bound": bound})
        result["would_apply"] = applied

    # Validate user_attrs cover required keys.
    missing = [k for k in (cfg.get("user_attr_keys") or []) if k not in user_attrs]
    if missing:
        result["missing_user_attrs"] = missing
    return {"status": "ok", "test": result}


@router.get("/{slug}/rls-config/schema-hints")
def rls_config_schema_hints(slug: str, request: Request):
    """Return project schema for RLS picker UIs.

    Response shape:
    {
      "tables": [
        {"name": "sales", "columns": [{"name": "id", "type": "integer", "is_id_like": true},
                                       {"name": "store_id", "type": "integer", "is_id_like": true},
                                       {"name": "amount", "type": "numeric", "is_id_like": false}]},
        ...
      ],
      "suggested_attr_keys": ["store_id", "tenant_id"],
      "suggested_filters": [
        {"table": "sales", "expr": "store_id = :store_id", "attr_key": "store_id"},
        ...
      ]
    }
    """
    user = _get_user(request)
    _check_access(user, slug)

    # 1. Pull table metadata from dash_table_metadata
    tables = []
    column_to_tables: dict[str, list[str]] = {}  # col_name -> [tables containing it]

    with _engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT table_name, metadata FROM dash_table_metadata
            WHERE project_slug = :s
            ORDER BY table_name
        """), {"s": slug}).mappings().all()

    def _classify_id_like(cn_l: str) -> bool:
        return (
            cn_l.endswith("_id") or cn_l in ("id",)
            or cn_l.startswith("tenant_") or cn_l.startswith("org_")
            or cn_l.startswith("user_") or cn_l in ("store", "shop", "site", "tenant", "org")
            or "store" in cn_l or "shop" in cn_l or "tenant" in cn_l or "org" in cn_l
        )

    for r in rows:
        meta = r["metadata"]
        if isinstance(meta, str):
            try: meta = json.loads(meta)
            except Exception: meta = {}
        cols_raw = (meta or {}).get("table_columns") or (meta or {}).get("columns") or []
        cols = []
        for c in cols_raw:
            if isinstance(c, str):
                col_name = c; col_type = ""
            elif isinstance(c, dict):
                col_name = c.get("name") or c.get("column_name") or ""
                col_type = c.get("type") or c.get("data_type") or ""
            else:
                continue
            if not col_name: continue
            cn_l = col_name.lower()
            is_id_like = _classify_id_like(cn_l)
            cols.append({"name": col_name, "type": col_type, "is_id_like": bool(is_id_like)})
            column_to_tables.setdefault(cn_l, []).append(r["table_name"])
        tables.append({"name": r["table_name"], "columns": cols})

    # Fallback: if dash_table_metadata is empty, read from PG information_schema
    # using the project's actual DB schema. Handles untrained projects + projects
    # whose metadata catalog wasn't populated.
    if not tables:
        try:
            with _engine.connect() as conn:
                proj = conn.execute(text(
                    "SELECT schema_name FROM dash_projects WHERE slug=:s"
                ), {"s": slug}).mappings().first()
                schema_name = (proj or {}).get("schema_name") or slug
                ic_rows = conn.execute(text("""
                    SELECT table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = :sch
                      AND table_name NOT LIKE 'dash\\_%' ESCAPE '\\'
                    ORDER BY table_name, ordinal_position
                """), {"sch": schema_name}).mappings().all()
            grouped: dict[str, list[dict]] = {}
            for r in ic_rows:
                tname = r["table_name"]
                cn = r["column_name"]
                ct = r["data_type"]
                cn_l = cn.lower()
                grouped.setdefault(tname, []).append({
                    "name": cn, "type": ct,
                    "is_id_like": bool(_classify_id_like(cn_l)),
                })
                column_to_tables.setdefault(cn_l, []).append(tname)
            for tname in sorted(grouped.keys()):
                tables.append({"name": tname, "columns": grouped[tname]})
        except Exception as e:
            logging.warning(f"schema-hints information_schema fallback failed: {e}")

    # 2. Suggested attr keys: id-like columns appearing in 2+ tables (likely tenant key)
    suggested_attr_keys: list[str] = []
    seen = set()
    PRIORITY_PATTERNS = (
        "store_id", "shop_id", "tenant_id", "org_id", "organization_id",
        "site_id", "branch_id", "location_id", "outlet_id", "user_id",
        "client_id", "customer_id", "company_id",
    )
    for pat in PRIORITY_PATTERNS:
        if pat in column_to_tables and pat not in seen:
            suggested_attr_keys.append(pat)
            seen.add(pat)
    for col, ts in column_to_tables.items():
        if col in seen: continue
        if (col.endswith("_id") or col in ("store","shop","site","tenant","org")) and len(ts) >= 2:
            suggested_attr_keys.append(col); seen.add(col)
    suggested_attr_keys = suggested_attr_keys[:8]  # cap

    # 3. Suggested filters: for each table containing a suggested attr key, propose filter
    suggested_filters: list[dict] = []
    if suggested_attr_keys:
        primary = suggested_attr_keys[0]
        for tbl in tables:
            for c in tbl["columns"]:
                if c["name"].lower() == primary.lower():
                    suggested_filters.append({
                        "table": tbl["name"],
                        "expr": f"{c['name']} = :{primary}",
                        "attr_key": primary,
                    })
                    break

    return {
        "slug": slug,
        "tables": tables,
        "suggested_attr_keys": suggested_attr_keys,
        "suggested_filters": suggested_filters,
    }


@router.post("/{slug}/rls-config/quick-add")
async def rls_config_quick_add(slug: str, request: Request):
    """One-click: enable RLS + add attr_key + add filters for all tables containing the column.

    Body: {"attr_key": "store_id", "column": "store_id", "mode": "rewrite"}
    Returns updated config.
    """
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json() or {}
    attr_key = (body.get("attr_key") or "").strip()
    column = (body.get("column") or attr_key).strip()
    mode = (body.get("mode") or "rewrite").strip().lower()
    if not attr_key:
        return {"status": "error", "detail": "attr_key required"}
    if mode not in _VALID_RLS_MODES:
        return {"status": "error", "detail": f"mode must be {sorted(_VALID_RLS_MODES)}"}

    # Find tables that have the column
    matching: list[str] = []
    with _engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT table_name, metadata FROM dash_table_metadata WHERE project_slug = :s
        """), {"s": slug}).mappings().all()
    for r in rows:
        meta = r["metadata"]
        if isinstance(meta, str):
            try: meta = json.loads(meta)
            except Exception: meta = {}
        cols_raw = (meta or {}).get("table_columns") or (meta or {}).get("columns") or []
        for c in cols_raw:
            cn = c if isinstance(c, str) else (c.get("name") or c.get("column_name") or "")
            if cn and cn.lower() == column.lower():
                matching.append(r["table_name"])
                break

    # Fallback to information_schema if metadata catalog empty.
    if not matching:
        try:
            with _engine.connect() as conn:
                proj = conn.execute(text(
                    "SELECT schema_name FROM dash_projects WHERE slug=:s"
                ), {"s": slug}).mappings().first()
                schema_name = (proj or {}).get("schema_name") or slug
                ic_rows = conn.execute(text("""
                    SELECT DISTINCT table_name FROM information_schema.columns
                    WHERE table_schema = :sch
                      AND lower(column_name) = lower(:c)
                      AND table_name NOT LIKE 'dash\\_%' ESCAPE '\\'
                    ORDER BY table_name
                """), {"sch": schema_name, "c": column}).mappings().all()
            matching = [r["table_name"] for r in ic_rows]
        except Exception as e:
            logging.warning(f"quick-add information_schema fallback failed: {e}")

    if not matching:
        return {"status": "error", "detail": f"no tables in project contain column '{column}'", "matching": []}

    # Merge into existing config (preserve existing filters)
    existing = _load_rls_config(slug)
    keys = list(existing.get("user_attr_keys") or [])
    if attr_key not in keys:
        keys.append(attr_key)
    filters = dict(existing.get("table_filters") or {})
    for tbl in matching:
        if tbl not in filters:
            filters[tbl] = f"{column} = :{attr_key}"

    _ensure_rls_table()
    with _engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dash_project_rls_config
                (project_slug, enabled, mode, user_attr_keys, table_filters, default_deny, updated_at)
            VALUES (:s, true, :m, :k, CAST(:f AS JSONB), true, now())
            ON CONFLICT (project_slug) DO UPDATE SET
                enabled = true,
                mode = EXCLUDED.mode,
                user_attr_keys = EXCLUDED.user_attr_keys,
                table_filters = EXCLUDED.table_filters,
                default_deny = true,
                updated_at = now()
        """), {"s": slug, "m": mode, "k": keys, "f": json.dumps(filters)})

    return {
        "status": "ok",
        "matched_tables": matching,
        "config": _load_rls_config(slug),
    }


@router.post("/{slug}/rls-config/apply-policies")
def rls_apply_policies(slug: str, request: Request):
    """Apply Postgres RLS policies for this project's tables. Idempotent."""
    user = _get_user(request)
    _check_access(user, slug)
    from dash.rls.pg_session import invalidate_cache
    from dash.rls.pg_setup import apply_policies
    invalidate_cache(slug)
    return apply_policies(slug)


@router.post("/{slug}/rls-config/remove-policies")
def rls_remove_policies(slug: str, request: Request):
    """Remove Postgres RLS policies for this project's tables."""
    user = _get_user(request)
    _check_access(user, slug)
    from dash.rls.pg_session import invalidate_cache
    from dash.rls.pg_setup import remove_policies
    invalidate_cache(slug)
    return remove_policies(slug)


@router.get("/{slug}/guardrail-audit")
def guardrail_audit(slug: str, days: int = 14, limit: int = 50):
    """Recent refusals for this project."""
    d = max(1, min(int(days), 365))
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, question, refusal_reason, classifier, matched_topic, "
            " external_user, embed_id, ts "
            "FROM public.dash_guardrail_audit "
            "WHERE project_slug = :s AND ts >= NOW() - INTERVAL ':d days'".replace(":d", str(d)) +
            " ORDER BY ts DESC LIMIT :n"
        ), {"s": slug, "n": min(int(limit), 500)}).mappings().all()
    return {"refusals": [dict(r) for r in rows]}


@router.get("/{slug}/refine-tools/transfer-candidates")
def refine_tools_transfer_candidates(slug: str, request: Request):
    """List importable patches from sibling projects with >20% column overlap.

    Returns: list of {source_slug, source_name, overlap_pct, patches: [...]}.
    Each patch shows tool_name, version, score_before, score_after, shadow,
    confidence (combined heuristic).
    """
    user = _get_user(request)
    _check_access(user, slug)

    with _engine.connect() as conn:
        source_meta = conn.execute(text(
            "SELECT metadata FROM public.dash_table_metadata WHERE project_slug = :s"
        ), {"s": slug}).fetchall()
        source_cols = set()
        for r in source_meta:
            m = r[0] if isinstance(r[0], dict) else json.loads(r[0]) if r[0] else {}
            for c in m.get("table_columns", []):
                source_cols.add((c.get("name") or "").lower())
        if not source_cols:
            return {"candidates": []}

        sibling_rows = conn.execute(text(
            "SELECT slug, agent_name FROM public.dash_projects WHERE user_id = :uid AND slug != :s"
        ), {"uid": user["user_id"], "s": slug}).fetchall()

        out = []
        for sib_slug, sib_name in sibling_rows:
            other_meta = conn.execute(text(
                "SELECT metadata FROM public.dash_table_metadata WHERE project_slug = :s"
            ), {"s": sib_slug}).fetchall()
            other_cols = set()
            for r in other_meta:
                m = r[0] if isinstance(r[0], dict) else json.loads(r[0]) if r[0] else {}
                for c in m.get("table_columns", []):
                    other_cols.add((c.get("name") or "").lower())
            if not other_cols:
                continue
            overlap = source_cols & other_cols
            ov_pct = len(overlap) / max(len(source_cols), 1) * 100.0
            if ov_pct < 20:
                continue

            # Active applied patches in sibling that haven't been imported here yet.
            patch_rows = conn.execute(text(
                "SELECT id, tool_name, version, new_description, default_args, "
                " score_before, score_after, shadow_pass_rate, reason, applied_at "
                "FROM public.dash_tool_patches "
                "WHERE project_slug = :s AND applied = TRUE AND reverted = FALSE"
            ), {"s": sib_slug}).mappings().all()
            patches = []
            for p in patch_rows:
                # Skip tools already patched on this project.
                exists = conn.execute(text(
                    "SELECT 1 FROM public.dash_tool_patches "
                    "WHERE project_slug = :s AND tool_name = :t "
                    "  AND applied = TRUE AND reverted = FALSE LIMIT 1"
                ), {"s": slug, "t": p["tool_name"]}).first()
                if exists:
                    continue
                shadow = float(p["shadow_pass_rate"] or 0)
                gain = float((p["score_after"] or 0)) - float((p["score_before"] or 0))
                confidence = round(min(100, max(0,
                    0.5 * ov_pct +
                    0.3 * shadow +
                    0.2 * max(0, gain) * 5  # 20-pt gain → +20
                )), 1)
                patches.append({
                    **{k: v for k, v in dict(p).items() if k != "applied_at"},
                    "applied_at": p["applied_at"].isoformat() if p["applied_at"] else None,
                    "confidence": confidence,
                })
            if patches:
                patches.sort(key=lambda x: x["confidence"], reverse=True)
                out.append({
                    "source_slug": sib_slug,
                    "source_name": sib_name,
                    "overlap_pct": round(ov_pct, 1),
                    "shared_columns": sorted(overlap)[:20],
                    "patches": patches,
                })
        out.sort(key=lambda x: x["overlap_pct"], reverse=True)
    return {"candidates": out}


@router.post("/{slug}/refine-tools/import-patch/{source_patch_id}")
def refine_tools_import_patch(slug: str, source_patch_id: int, request: Request,
                              auto_apply: bool = False):
    """Copy a patch from a sibling project. Source marked as 'transferred'.

    Inserts as draft (applied=false) by default; pass ?auto_apply=true to
    activate immediately (still subject to project's own override later).
    """
    user = _get_user(request)
    _check_access(user, slug)

    with _engine.begin() as conn:
        src = conn.execute(text(
            "SELECT tool_name, old_description, new_description, default_args, "
            " reason, score_before, score_after, shadow_pass_rate, project_slug "
            "FROM public.dash_tool_patches WHERE id = :id"
        ), {"id": source_patch_id}).first()
        if not src:
            return {"status": "error", "detail": "source patch not found"}

        next_ver = conn.execute(text(
            "SELECT COALESCE(MAX(version),0)+1 FROM public.dash_tool_patches "
            "WHERE tool_name = :t AND (project_slug = :s OR project_slug IS NULL)"
        ), {"t": src[0], "s": slug}).scalar() or 1

        args = src[3]
        if isinstance(args, str):
            try: args = json.loads(args)
            except Exception: args = {}

        reason = (src[4] or "")
        carry = f"[transferred from {src[8]} (patch #{source_patch_id})] "
        new_id = conn.execute(text(
            "INSERT INTO public.dash_tool_patches "
            "(tool_name, project_slug, version, old_description, new_description, "
            " default_args, reason, score_before, source, applied, applied_at) "
            "VALUES (:t, :s, :v, :old, :new, :args, :reason, :sb, 'transferred', :ap, "
            "        CASE WHEN :ap THEN NOW() ELSE NULL END) RETURNING id"
        ), {
            "t": src[0], "s": slug, "v": int(next_ver),
            "old": src[1] or "", "new": src[2],
            "args": json.dumps(args or {}),
            "reason": (carry + reason)[:500],
            "sb": float(src[5] or 0),
            "ap": bool(auto_apply),
        }).scalar()

        if auto_apply:
            conn.execute(text(
                "UPDATE public.dash_tool_patches SET applied = FALSE "
                "WHERE tool_name = :t AND (project_slug IS NOT DISTINCT FROM :s) "
                "  AND id <> :id"
            ), {"t": src[0], "s": slug, "id": new_id})

    if auto_apply:
        try:
            from dash.tools.skill_refinery import invalidate_patch_cache
            invalidate_patch_cache()
        except Exception:
            pass
    return {"status": "ok", "patch_id": new_id, "version": next_ver,
            "applied": auto_apply}


@router.delete("/{slug}/refine-tools/patches/{patch_id}")
def refine_tools_patch_discard(slug: str, patch_id: int):
    """Hard-delete a draft patch (only allowed when not applied)."""
    with _engine.begin() as conn:
        row = conn.execute(text(
            "SELECT applied FROM public.dash_tool_patches WHERE id = :id"
        ), {"id": patch_id}).first()
        if not row:
            return {"status": "error", "detail": "patch not found"}
        if row[0]:
            return {"status": "error", "detail": "patch already applied — revert first"}
        conn.execute(text("DELETE FROM public.dash_tool_patches WHERE id = :id"), {"id": patch_id})
    return {"status": "ok", "patch_id": patch_id}


# ---------------------------------------------------------------------------
# Visibility Policy (Phase 2) — field-level downgrade rules per project
# ---------------------------------------------------------------------------

@router.get("/{slug}/visibility-policy")
def visibility_policy_get(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    try:
        from dash.policy import load_policy
    except Exception as e:
        raise HTTPException(503, f"policy module unavailable: {e}")
    pol = load_policy(slug)
    if not pol:
        raise HTTPException(404, "no visibility policy for project")
    # Track 2A may return either a Pydantic model or dict
    payload = pol.model_dump() if hasattr(pol, "model_dump") else (pol.dict() if hasattr(pol, "dict") else pol)
    version = (payload or {}).get("version") if isinstance(payload, dict) else getattr(pol, "version", None)
    return {"policy": payload, "version": version}


@router.put("/{slug}/visibility-policy")
async def visibility_policy_put(slug: str, request: Request):
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")
    body = await request.json() or {}
    policy_doc = body.get("policy")
    if not isinstance(policy_doc, dict):
        raise HTTPException(400, "policy object required")
    # Phase 5A: force flag (query OR body); when False (default), validate first.
    force_q = (request.query_params.get("force") or "").lower() in ("1", "true", "yes")
    force_b = bool(body.get("force") or False)
    force = force_q or force_b
    try:
        from dash.policy import VisibilityPolicy, save_policy, validate_policy
    except Exception as e:
        raise HTTPException(503, f"policy module unavailable: {e}")
    try:
        validated = VisibilityPolicy(**policy_doc)
    except Exception as e:
        raise HTTPException(400, f"invalid policy: {e}")

    # Sign-off workflow: write draft instead of publishing.
    request_approval = bool(body.get("request_approval") or False)
    if request_approval:
        try:
            from dash.policy import create_draft, submit_draft
        except Exception as e:
            raise HTTPException(503, f"signoff module unavailable: {e}")
        comment = body.get("comment") or ""
        draft_id = create_draft(slug, policy_doc, user.get("id"), comment=comment)
        if not draft_id:
            raise HTTPException(500, "failed to create draft")
        # auto-submit so it shows up as pending
        submit_draft(draft_id, user.get("id"))
        return {"draft_id": draft_id, "status": "pending"}

    validation: dict = {"ok": True, "failures": [], "warnings": []}
    if not force:
        try:
            validation = validate_policy(slug, validated)
        except Exception as e:
            validation = {"ok": True, "failures": [], "warnings": [f"validation skipped: {e}"]}
        if not validation.get("ok"):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=422, content={
                "ok": False,
                "failures": validation.get("failures") or [],
                "warnings": validation.get("warnings") or [],
                "message": "validation failed; pass force=true to override",
            })

    new_version = save_policy(slug, validated, user_id=user.get("id"))
    return {"status": "ok", "slug": slug, "version": new_version, "validation": validation}


@router.post("/{slug}/visibility-policy/simulate")
async def visibility_policy_simulate(slug: str, request: Request):
    """Phase 5A — preview rewritten SQL + sample rows for a (user, scope, intent) tuple."""
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'viewer'):
        raise HTTPException(403, "viewer role required")
    body = await request.json() or {}
    user_id = body.get("user_id")
    scope_id = body.get("scope_id") or ""
    intent = (body.get("intent") or "private").strip().lower()
    sql = (body.get("sql") or "").strip()
    draft = body.get("draft_policy")
    if not user_id or not sql:
        raise HTTPException(400, "user_id and sql required")
    try:
        from dash.policy import VisibilityPolicy, simulate as _simulate
    except Exception as e:
        raise HTTPException(503, f"policy module unavailable: {e}")
    if draft is not None:
        if not isinstance(draft, dict):
            raise HTTPException(400, "draft_policy must be object")
        try:
            VisibilityPolicy(**draft)
        except Exception as e:
            raise HTTPException(400, f"invalid draft_policy: {e}")
    try:
        result = _simulate(slug, int(user_id), str(scope_id), intent, sql, draft_policy=draft)
    except Exception as e:
        result = {"rewritten_sql": sql, "rows": [], "cols": [], "downgraded_fields": [],
                  "allowed_intent": intent, "capped_intent": False, "error": f"simulate failed: {e}"}
    return result


@router.post("/{slug}/visibility-policy/validate")
async def visibility_policy_validate(slug: str, request: Request):
    """Phase 5A — synthetic matrix run for a draft/full policy."""
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")
    body = await request.json() or {}
    policy_doc = body.get("policy")
    if not isinstance(policy_doc, dict):
        raise HTTPException(400, "policy object required")
    try:
        from dash.policy import VisibilityPolicy, validate_policy as _validate
    except Exception as e:
        raise HTTPException(503, f"policy module unavailable: {e}")
    try:
        validated = VisibilityPolicy(**policy_doc)
    except Exception as e:
        raise HTTPException(400, f"invalid policy: {e}")
    try:
        return _validate(slug, validated)
    except Exception as e:
        return {"ok": True, "failures": [], "warnings": [f"validation error: {e}"]}


@router.post("/{slug}/visibility-policy/test")
async def visibility_policy_test(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json() or {}
    sql = (body.get("sql") or "").strip()
    intent = (body.get("intent") or "private").strip().lower()
    if not sql:
        raise HTTPException(400, "sql required")
    try:
        from dash.policy import apply_policy
    except Exception as e:
        raise HTTPException(503, f"policy module unavailable: {e}")
    rewritten, dropped = apply_policy(sql, slug, intent)
    return {
        "original_sql": sql,
        "rewritten_sql": rewritten,
        "downgraded_fields": list(dropped or []),
        "intent": intent,
    }


@router.get("/{slug}/visibility-roles")
def visibility_roles_get(slug: str, request: Request):
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")
    try:
        from dash.policy.roles import list_roles
        return {"roles": list_roles(slug)}
    except Exception:
        return {"roles": []}


@router.put("/{slug}/visibility-roles")
async def visibility_roles_put(slug: str, request: Request):
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")
    body = await request.json() or {}
    roles = body.get("roles") or []
    if not isinstance(roles, list):
        raise HTTPException(400, "roles array required")
    try:
        from dash.policy.roles import replace_roles
        replace_roles(slug, roles)
    except Exception as e:
        raise HTTPException(503, f"roles unavailable: {e}")
    return {"status": "ok", "count": len(roles)}


@router.get("/{slug}/user-roles")
def user_roles_get(slug: str, request: Request):
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")
    try:
        from dash.policy.roles import list_user_roles
        return {"assignments": list_user_roles(slug)}
    except Exception:
        return {"assignments": []}


@router.put("/{slug}/user-roles")
async def user_roles_put(slug: str, request: Request):
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")
    body = await request.json() or {}
    assignments = body.get("assignments") or []
    if not isinstance(assignments, list):
        raise HTTPException(400, "assignments array required")
    try:
        from dash.policy.roles import replace_user_roles
        replace_user_roles(slug, assignments)
    except Exception as e:
        raise HTTPException(503, f"user-roles unavailable: {e}")
    return {"status": "ok", "count": len(assignments)}


@router.get("/{slug}/visibility-policy/history")
def visibility_policy_history(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT version, policy, created_at, created_by
                FROM dash_visibility_policy_history
                WHERE project_slug = :s
                ORDER BY version DESC
                LIMIT 20
            """), {"s": slug}).mappings().all()
    except Exception as e:
        # Table may not exist yet (Track 2A owns DDL).
        return {"history": [], "warning": str(e)}
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("policy"), str):
            try: d["policy"] = json.loads(d["policy"])
            except Exception: logger.exception("learning: visibility_history policy json parse failed")
        out.append(d)
    return {"history": out}


# ---------------------------------------------------------------------------
# Phase 7A — cross-store visibility read audit + time-travel
# ---------------------------------------------------------------------------

def _parse_iso(ts: str | None):
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


@router.get("/{slug}/visibility-audit")
def visibility_audit_list(slug: str, request: Request):
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")
    qp = request.query_params
    target_scope_id = qp.get("target_scope_id")
    viewer_user_id = qp.get("viewer_user_id")
    try:
        viewer_user_id = int(viewer_user_id) if viewer_user_id else None
    except Exception:
        viewer_user_id = None
    from_ts = _parse_iso(qp.get("from"))
    to_ts = _parse_iso(qp.get("to"))
    try:
        limit = int(qp.get("limit") or 200)
        offset = int(qp.get("offset") or 0)
    except Exception:
        limit, offset = 200, 0
    try:
        from dash.policy.read_audit import query_audit, count_audit
    except Exception as e:
        raise HTTPException(503, f"read audit unavailable: {e}")
    rows = query_audit(slug, target_scope=target_scope_id, viewer_user_id=viewer_user_id,
                       from_ts=from_ts, to_ts=to_ts, limit=limit, offset=offset)
    total = count_audit(slug, target_scope=target_scope_id, viewer_user_id=viewer_user_id,
                        from_ts=from_ts, to_ts=to_ts)
    return {"rows": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/{slug}/visibility-audit.csv")
def visibility_audit_csv(slug: str, request: Request):
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")
    qp = request.query_params
    target_scope_id = qp.get("target_scope_id")
    viewer_user_id = qp.get("viewer_user_id")
    try:
        viewer_user_id = int(viewer_user_id) if viewer_user_id else None
    except Exception:
        viewer_user_id = None
    from_ts = _parse_iso(qp.get("from"))
    to_ts = _parse_iso(qp.get("to"))
    try:
        limit = int(qp.get("limit") or 5000)
        offset = int(qp.get("offset") or 0)
    except Exception:
        limit, offset = 5000, 0
    try:
        from dash.policy.read_audit import export_audit_csv
    except Exception as e:
        raise HTTPException(503, f"read audit unavailable: {e}")
    body = export_audit_csv(slug, target_scope=target_scope_id, viewer_user_id=viewer_user_id,
                            from_ts=from_ts, to_ts=to_ts, limit=limit, offset=offset)
    from fastapi import Response
    from datetime import datetime as _dt
    today = _dt.utcnow().strftime("%Y-%m-%d")
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=visibility-audit-{slug}-{today}.csv"},
    )


@router.post("/{slug}/visibility-policy/time-travel")
async def visibility_policy_time_travel(slug: str, request: Request):
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'viewer'):
        raise HTTPException(403, "viewer role required")
    body = await request.json() or {}
    at_date = body.get("at_date")
    sql = (body.get("sql") or "").strip()
    intent = (body.get("intent") or "private").strip().lower()
    if not at_date or not sql:
        raise HTTPException(400, "at_date and sql required")
    at_ts = _parse_iso(at_date)
    if not at_ts:
        raise HTTPException(400, "at_date must be ISO8601")
    try:
        with _engine.connect() as conn:
            row = conn.execute(text("""
                SELECT version, policy_json, changed_at
                FROM public.dash_visibility_policy_history
                WHERE project_slug = :s AND changed_at <= :at
                ORDER BY changed_at DESC
                LIMIT 1
            """), {"s": slug, "at": at_ts}).first()
    except Exception as e:
        raise HTTPException(503, f"history lookup failed: {e}")
    if not row:
        raise HTTPException(404, "no historical policy at given date")
    version, policy_json, changed_at = row[0], row[1], row[2]
    if isinstance(policy_json, str):
        try:
            policy_json = json.loads(policy_json)
        except Exception:
            raise HTTPException(500, "stored policy unreadable")
    try:
        from dash.policy import VisibilityPolicy, PolicyEngine
        pol = VisibilityPolicy(**(policy_json or {}))
        if version is not None:
            pol.version = int(version)
        rewritten, downgraded = PolicyEngine().apply(sql, pol, intent)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"time-travel apply failed: {e}")
    return {
        "rewritten_sql": rewritten,
        "downgraded_fields": list(downgraded or []),
        "version": version,
        "snapshot_date": changed_at.isoformat() if changed_at else None,
        "policy": policy_json,
    }


# ---------------------------------------------------------------------------
# Phase 8A — industry visibility policy templates
# ---------------------------------------------------------------------------

templates_router = APIRouter(prefix="/api/visibility-templates", tags=["VisibilityTemplates"])


@templates_router.get("")
def visibility_templates_list(request: Request):
    _get_user(request)
    from dash.policy.templates import list_templates
    return {"templates": list_templates()}


@templates_router.get("/{name}")
def visibility_templates_get(name: str, request: Request):
    _get_user(request)
    from dash.policy.templates import get_template
    tmpl = get_template(name)
    if not tmpl:
        raise HTTPException(404, f"template '{name}' not found")
    return tmpl


@router.post("/{slug}/visibility-policy/apply-template")
async def visibility_policy_apply_template(slug: str, request: Request):
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")
    body = await request.json() or {}
    template_name = (body.get("template_name") or "").strip()
    merge_mode = (body.get("merge_mode") or "replace").strip().lower()
    also_seed_roles = bool(body.get("also_seed_roles") or False)
    if merge_mode not in ("replace", "merge"):
        raise HTTPException(400, "merge_mode must be 'replace' or 'merge'")
    try:
        from dash.policy import VisibilityPolicy, save_policy, load_policy
        from dash.policy.templates import get_template
        from dash.policy.roles import upsert_role
    except Exception as e:
        raise HTTPException(503, f"policy module unavailable: {e}")
    tmpl = get_template(template_name)
    if not tmpl:
        raise HTTPException(404, f"template '{template_name}' not found")
    tmpl_policy = tmpl["policy"]

    if merge_mode == "replace":
        merged_doc = dict(tmpl_policy)
    else:
        existing = load_policy(slug)
        existing_doc = (existing.model_dump() if existing else
                        {"version": 1, "private": {"fields": {}}, "network": {"fields": {}}, "public": {"fields": {}}})
        merged_doc = {"version": existing_doc.get("version", 1)}
        for aud in ("private", "network", "public"):
            existing_fields = dict((existing_doc.get(aud) or {}).get("fields") or {})
            tmpl_fields = dict((tmpl_policy.get(aud) or {}).get("fields") or {})
            existing_fields.update(tmpl_fields)
            merged_doc[aud] = {"fields": existing_fields}

    from datetime import datetime, timezone
    merged_doc["applied_template"] = template_name
    merged_doc["applied_template_at"] = datetime.now(timezone.utc).isoformat()
    try:
        validated = VisibilityPolicy(**merged_doc)
    except Exception as e:
        raise HTTPException(400, f"template produced invalid policy: {e}")
    new_version = save_policy(slug, validated, user_id=user.get("id"))

    roles_seeded = 0
    if also_seed_roles:
        for role in tmpl.get("suggested_roles", []) or []:
            try:
                upsert_role(
                    slug,
                    role.get("role_name") or "",
                    role.get("allowed_intents") or ["private"],
                    role.get("description") or "",
                )
                roles_seeded += 1
            except Exception:
                pass

    return {
        "policy": validated.model_dump(),
        "version": new_version,
        "roles_seeded": roles_seeded,
        "scope_keyword": tmpl.get("scope_keyword"),
    }


# ---------------------------------------------------------------------------
# Sign-off Workflow — 2-admin approval before policy publish
# ---------------------------------------------------------------------------

def _signoff_admin(user, slug):
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, 'admin'):
        raise HTTPException(403, "admin role required")


@router.post("/{slug}/visibility-policy/drafts")
async def signoff_create_draft(slug: str, request: Request):
    user = _get_user(request)
    _signoff_admin(user, slug)
    body = await request.json() or {}
    policy_doc = body.get("policy")
    if not isinstance(policy_doc, dict):
        raise HTTPException(400, "policy object required")
    try:
        from dash.policy import VisibilityPolicy, create_draft
    except Exception as e:
        raise HTTPException(503, f"policy module unavailable: {e}")
    try:
        VisibilityPolicy(**policy_doc)
    except Exception as e:
        raise HTTPException(400, f"invalid policy: {e}")
    comment = body.get("comment") or ""
    try:
        draft_id = create_draft(slug, policy_doc, user.get("id"), comment=comment)
    except Exception:
        raise HTTPException(500, "failed to create draft")
    if not draft_id:
        raise HTTPException(500, "failed to create draft")
    return {"draft_id": draft_id, "status": "draft"}


@router.get("/{slug}/visibility-policy/drafts")
def signoff_list_drafts(slug: str, request: Request):
    user = _get_user(request)
    _signoff_admin(user, slug)
    status = request.query_params.get("status")
    try:
        from dash.policy import list_drafts
        return {"drafts": list_drafts(slug, status=status)}
    except Exception:
        raise HTTPException(500, "failed to list drafts")


@router.get("/{slug}/visibility-policy/drafts/{draft_id}")
def signoff_get_draft(slug: str, draft_id: int, request: Request):
    user = _get_user(request)
    _signoff_admin(user, slug)
    try:
        from dash.policy import get_draft
        d = get_draft(draft_id)
    except Exception:
        raise HTTPException(500, "failed to load draft")
    if not d or d.get("project_slug") != slug:
        raise HTTPException(404, "draft not found")
    return d


@router.post("/{slug}/visibility-policy/drafts/{draft_id}/submit")
async def signoff_submit_draft(slug: str, draft_id: int, request: Request):
    user = _get_user(request)
    _signoff_admin(user, slug)
    try:
        from dash.policy import get_draft, submit_draft
        existing = get_draft(draft_id)
        if not existing or existing.get("project_slug") != slug:
            raise HTTPException(404, "draft not found")
        d = submit_draft(draft_id, user.get("id"))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "failed to submit draft")
    if not d:
        raise HTTPException(500, "failed to submit draft")
    return d


@router.post("/{slug}/visibility-policy/drafts/{draft_id}/approve")
async def signoff_approve_draft(slug: str, draft_id: int, request: Request):
    user = _get_user(request)
    _signoff_admin(user, slug)
    body = await request.json() or {}
    comment = body.get("comment") or ""
    try:
        from dash.policy import get_draft, approve_draft
        existing = get_draft(draft_id)
        if not existing or existing.get("project_slug") != slug:
            raise HTTPException(404, "draft not found")
        d = approve_draft(draft_id, user.get("id"), comment=comment)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "failed to approve draft")
    if not d:
        raise HTTPException(500, "failed to approve draft")
    if isinstance(d, dict) and d.get("_error"):
        raise HTTPException(400, d["_error"])
    return d


@router.post("/{slug}/visibility-policy/drafts/{draft_id}/reject")
async def signoff_reject_draft(slug: str, draft_id: int, request: Request):
    user = _get_user(request)
    _signoff_admin(user, slug)
    body = await request.json() or {}
    comment = body.get("comment") or ""
    try:
        from dash.policy import get_draft, reject_draft
        existing = get_draft(draft_id)
        if not existing or existing.get("project_slug") != slug:
            raise HTTPException(404, "draft not found")
        d = reject_draft(draft_id, user.get("id"), comment=comment)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "failed to reject draft")
    if not d:
        raise HTTPException(500, "failed to reject draft")
    if isinstance(d, dict) and d.get("_error"):
        raise HTTPException(400, d["_error"])
    return d


# ---------------------------------------------------------------------------
# Skill Library (alive — dream_lite tier)
# ---------------------------------------------------------------------------

@router.get("/{slug}/dream/skill-library")
def dream_skill_library_list(
    slug: str, request: Request, limit: int = 100, status: str = "active"
):
    """List skills in skill library for project. Viewer role required."""
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    _check_access(user, slug)
    try:
        l = max(1, min(int(limit), 300))
        st = (status or "active").strip().lower()
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, name, description, sql_template, success_count, "
                "       failure_count, avg_judge_score, status, last_used_at, created_at "
                "FROM public.dash_skill_library "
                "WHERE project_slug = :s AND status = :st "
                "ORDER BY success_count DESC, avg_judge_score DESC NULLS LAST "
                "LIMIT :l"
            ), {"s": slug, "st": st, "l": l}).mappings().all()
        skills = []
        for r in rows:
            d_ = dict(r)
            for k, v in list(d_.items()):
                if hasattr(v, "isoformat"):
                    d_[k] = v.isoformat()
            skills.append(d_)
        return {"skills": skills}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/{slug}/dream/skill-library/{sid}/deprecate")
def dream_skill_deprecate(slug: str, sid: int, request: Request):
    """Deprecate a skill in skill library. Admin role required."""
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, 'admin'):
            raise HTTPException(403, "admin role required")
        skill_name: Optional[str] = None
        with _engine.begin() as conn:
            row = conn.execute(text(
                "SELECT name FROM public.dash_skill_library "
                "WHERE id = :sid AND project_slug = :s"
            ), {"sid": int(sid), "s": slug}).first()
            if not row:
                raise HTTPException(404, "skill not found")
            skill_name = row[0]
            conn.execute(text(
                "UPDATE public.dash_skill_library "
                "SET status = 'deprecated' "
                "WHERE id = :sid AND project_slug = :s"
            ), {"sid": int(sid), "s": slug})
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────
# Cross-Project Skill Marketplace (migration 076)
# ─────────────────────────────────────────────────────────────────────────

marketplace_router = APIRouter(prefix="/api/marketplace", tags=["Marketplace"])


@router.post("/{slug}/dream/skill-library/{sid}/nominate")
def nominate_skill_to_marketplace(slug: str, sid: int, request: Request):
    """Nominate a project-scoped skill into the global marketplace.

    Eligibility checks: status='active', success_count>=20, avg_judge_score>=4.5,
    no recent failures, no PII/env/secret tokens in sql_template.
    Editor role required.
    """
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, 'editor'):
            raise HTTPException(403, "editor role required")

        # Defense-in-depth: ensure skill belongs to this project
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT project_slug FROM public.dash_skill_library WHERE id = :sid"
            ), {"sid": int(sid)}).fetchone()
            if not row:
                raise HTTPException(404, "skill not found")
            if row[0] != slug:
                raise HTTPException(403, "skill does not belong to this project")

        from dash.learning.skill_marketplace import nominate_to_marketplace
        result = nominate_to_marketplace(int(sid), int(user["user_id"]))
        status_code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status_code)
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@marketplace_router.get("/skills")
def marketplace_list_skills(
    request: Request,
    template: str = None,
    search: str = None,
    limit: int = 50,
):
    """List active marketplace skills. Any authenticated user (viewer+)."""
    from fastapi.responses import JSONResponse
    _get_user(request)  # 401 if unauth
    try:
        from dash.learning.skill_marketplace import list_marketplace
        skills = list_marketplace(template=template, search=search, limit=int(limit))
        return {"skills": skills}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@marketplace_router.get("/skills/{mid}")
def marketplace_get_skill(mid: int, request: Request):
    """Fetch single marketplace skill detail (incl. sql_template)."""
    from fastapi.responses import JSONResponse
    _get_user(request)
    try:
        from dash.learning.skill_marketplace import get_marketplace_skill
        skill = get_marketplace_skill(int(mid))
        if not skill:
            raise HTTPException(404, "marketplace skill not found")
        return {"skill": skill}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/{slug}/marketplace/install/{mid}")
def marketplace_install_into_project(slug: str, mid: int, request: Request):
    """Install a marketplace skill into the target project. Editor role required."""
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, 'editor'):
            raise HTTPException(403, "editor role required")
        from dash.learning.skill_marketplace import install_skill
        result = install_skill(int(mid), slug, int(user["user_id"]))
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/{slug}/marketplace/bootstrap")
def marketplace_bootstrap(slug: str, request: Request, template: str = None):
    """Admin-only: auto-install top-5 marketplace skills for this project's template."""
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, 'admin'):
            raise HTTPException(403, "admin role required")
        tpl = (template or "").strip().lower() or None
        if not tpl:
            with _engine.connect() as conn:
                from dash.learning.skill_marketplace import _derive_template_for_project
                tpl = _derive_template_for_project(slug, conn)
        from dash.learning.skill_marketplace import auto_bootstrap_new_project
        n = auto_bootstrap_new_project(slug, tpl)
        return {"ok": True, "installed": n, "template_name": tpl}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Storage Tier endpoints ───────────────────────────────────────────────
@router.get("/{slug}/storage/tier/{subdir}")
def storage_get_tier(slug: str, subdir: str, request: Request):
    """Get current storage tier for a knowledge subdir."""
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    _check_access(user, slug)
    try:
        from dash.learning import storage_tier
        return {
            "slug": slug,
            "subdir": subdir,
            "tier": storage_tier.get_tier(slug, subdir),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/{slug}/storage/tier/{subdir}")
async def storage_set_tier(slug: str, subdir: str, request: Request):
    """Set storage tier. Body: {tier: db_only|disk_only|db_tracked}. Admin only."""
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, 'admin'):
            raise HTTPException(403, "admin role required")
        body = await request.json()
        tier = (body.get("tier") or "").strip().lower()
        from dash.learning import storage_tier
        if tier == storage_tier.TIER_DB_ONLY:
            return storage_tier.mark_db_only(slug, subdir)
        if tier == storage_tier.TIER_DISK_ONLY:
            return storage_tier.mark_disk_only(slug, subdir)
        if tier == storage_tier.TIER_DB_TRACKED:
            return storage_tier.clear_tier(slug, subdir)
        raise HTTPException(400, "tier must be db_only|disk_only|db_tracked")
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/{slug}/storage/evict")
def storage_evict(
    slug: str, request: Request, subdir: str, keep_index: bool = True
):
    """Evict disk copies for a db_only subdir. Admin only."""
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, 'admin'):
            raise HTTPException(403, "admin role required")
        from dash.learning import storage_tier
        return storage_tier.evict_db_only_files(
            slug, subdir, keep_index=bool(keep_index)
        )
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Dream Reflection Wave-3 — surviving ops surface
# (poignancy capture, dream-lite, slack — all kept; backed by live modules)
# ---------------------------------------------------------------------------

def _isoify(d_: dict) -> dict:
    for k, v in list(d_.items()):
        if hasattr(v, "isoformat"):
            d_[k] = v.isoformat()
    return d_


@router.post("/{slug}/dream/poignancy/capture")
async def dream_poignancy_capture(slug: str, request: Request):
    """Manual capture trigger for the poignancy episode buffer.

    Body JSON: {session_id, turn_id?, user_id?, question, response_summary,
                tools_used?, succeeded?, judge_score?, user_reaction?}
    """
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    _check_access(user, slug)
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        from dash.learning import dream_poignancy as _dp
        episode_id = _dp.capture_turn(
            session_id=str(body.get("session_id") or ""),
            turn_id=body.get("turn_id"),
            project_slug=slug,
            user_id=body.get("user_id") or user.get("user_id"),
            question=(body.get("question") or "")[:500],
            response_summary=(body.get("response_summary") or "")[:1000],
            tools_used=body.get("tools_used") or [],
            succeeded=bool(body.get("succeeded", True)),
            judge_score=body.get("judge_score"),
            user_reaction=body.get("user_reaction"),
        )
        return {"episode_id": int(episode_id) if episode_id else None,
                "status": "captured"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("dream poignancy capture failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/{slug}/dream/poignancy/buffer")
def dream_poignancy_buffer(
    slug: str,
    request: Request,
    limit: int = 100,
    session_id: str | None = None,
    min_poignancy: float = 0.0,
):
    """List recent episode buffer rows for project."""
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    _check_access(user, slug)
    try:
        l = max(1, min(int(limit), 500))
        params = {"s": slug, "l": l, "p": float(min_poignancy or 0.0)}
        sql = (
            "SELECT * FROM dash.dash_episode_buffer "
            "WHERE project_slug = :s AND COALESCE(poignancy, 0) >= :p"
        )
        if session_id:
            sql += " AND session_id = :sid"
            params["sid"] = session_id
        sql += " ORDER BY created_at DESC LIMIT :l"
        with _engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return {"episodes": [_isoify(dict(r)) for r in rows]}
    except Exception as e:
        logger.exception("dream poignancy buffer failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/{slug}/dream/lite/run-now")
async def dream_lite_run_now(slug: str, request: Request):
    """Enqueue a manual dream_lite minion. Admin role required.

    Body: {session_id (required), user_id?, trigger_reason?}
    """
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, 'admin'):
            raise HTTPException(403, "admin role required")
        body_obj: dict = {}
        try:
            body_obj = await request.json()
        except Exception:
            body_obj = {}
        if not isinstance(body_obj, dict):
            body_obj = {}
        session_id = (body_obj.get("session_id") or
                      request.query_params.get("session_id") or "")
        if not session_id:
            raise HTTPException(400, "session_id required")
        from dash.minions import queue as q
        minion_id = q.enqueue(
            project=slug,
            kind="dream_lite",
            payload={
                "project": slug,
                "session_id": session_id,
                "user_id": body_obj.get("user_id") or user.get("user_id"),
                "trigger_reason": body_obj.get("trigger_reason") or "manual",
            },
            priority=7,
        )
        return {"minion_id": int(minion_id), "status": "queued"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("dream lite run-now failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/{slug}/dream/lite/runs")
def dream_lite_runs(slug: str, request: Request, days: int = 7, limit: int = 50):
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    _check_access(user, slug)
    try:
        d = max(1, min(int(days), 365))
        l = max(1, min(int(limit), 200))
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT * FROM public.dash_dream_lite_runs "
                "WHERE project_slug = :s "
                "  AND triggered_at > now() - (:d || ' days')::interval "
                "ORDER BY triggered_at DESC LIMIT :l"
            ), {"s": slug, "d": str(d), "l": l}).mappings().all()
        return {"runs": [_isoify(dict(r)) for r in rows]}
    except Exception as e:
        logger.exception("dream lite runs failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/{slug}/dream/slack/digest-now")
def dream_slack_digest_now(slug: str, request: Request):
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, 'admin'):
            raise HTTPException(403, "admin role required")
        from dash.learning import dream_slack
        res = dream_slack.send_digest(slug, hours=24)
        return res
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("dream slack digest failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# --- Super-admin cycle-all entrypoints for cron --------------------------------

@router.post("/dream/slack/digest-all")
def dream_slack_digest_all(request: Request):
    """Super-admin: send slack digest for every active project."""
    from fastapi.responses import JSONResponse
    from app.auth import _require_super
    _require_super(request)
    try:
        from dash.learning import dream_slack
        return dream_slack.send_daily_digest_all()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Skill Quality Pipeline — audit log + sync audit-test
# ---------------------------------------------------------------------------

@router.get("/{slug}/dream/skill-audit-log")
def dream_skill_audit_log(slug: str, request: Request, days: int = 14, limit: int = 100):
    """Recent skill-audit rows for project. Viewer role required."""
    from fastapi.responses import JSONResponse
    user = _get_user(request)
    _check_access(user, slug)
    try:
        d = max(1, int(days))
        l = max(1, min(int(limit), 500))
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, skill_name, project_slug, candidate_sql, "
                "       audit_result, passed, created_at "
                "  FROM dash.dash_skill_audit_log "
                " WHERE project_slug = :s "
                "   AND created_at > now() - (:d || ' days')::interval "
                " ORDER BY created_at DESC LIMIT :l"
            ), {"s": slug, "d": str(d), "l": l}).mappings().all()
        out = []
        for r in rows:
            d_ = dict(r)
            for k, v in list(d_.items()):
                if hasattr(v, "isoformat"):
                    d_[k] = v.isoformat()
            out.append(d_)
        return {"entries": out}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Compliance Pack: PII NER + Differential Privacy endpoints (migration 079)
# ---------------------------------------------------------------------------

@router.post("/{slug}/compliance/pii-scan")
async def compliance_pii_scan(slug: str, request: Request):
    """Detect PII in text + return scrubbed copy. Logs audit row."""
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()
    text_in = (body.get("text") or "").strip()
    mode = body.get("mode") or "mask"
    if not text_in:
        raise HTTPException(400, "text required")
    try:
        from dash.learning.pii_ner import detect_pii, scrub_pii
    except Exception as e:
        raise HTTPException(500, f"pii_ner unavailable: {e}")
    findings = detect_pii(text_in)
    scrubbed = scrub_pii(text_in, mode=mode, findings=findings)
    # Audit (best-effort)
    try:
        with _engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dash.dash_pii_audit
                    (project_slug, user_id, text_snippet, detected_types, action_taken)
                VALUES (:s, :u, :snip, :types, :act)
            """), {
                "s": slug,
                "u": user["user_id"],
                "snip": text_in[:200],
                "types": list({f["type"] for f in findings}),
                "act": f"scrub:{mode}",
            })
    except Exception as e:
        logger.warning(f"pii_audit insert failed: {e}")
    return {
        "findings": [
            {**f, "span": list(f["span"])} for f in findings
        ],
        "scrubbed": scrubbed,
        "count": len(findings),
    }


@router.get("/{slug}/compliance/pii-audit")
def compliance_pii_audit(slug: str, request: Request, days: int = 14):
    user = _get_user(request)
    _check_access(user, slug)
    days = max(1, min(int(days), 365))
    with _engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, user_id, text_snippet, detected_types, action_taken, created_at
            FROM dash.dash_pii_audit
            WHERE project_slug = :s AND created_at > now() - (:d || ' days')::interval
            ORDER BY created_at DESC LIMIT 500
        """), {"s": slug, "d": str(days)}).fetchall()
    return {"audit": [
        {"id": r[0], "user_id": r[1], "snippet": r[2],
         "detected_types": list(r[3]) if r[3] else [],
         "action": r[4], "created_at": str(r[5]) if r[5] else None}
        for r in rows
    ]}


@router.get("/{slug}/compliance/dp-budget")
def compliance_dp_budget(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    try:
        from dash.learning.differential_privacy import dp_budget_remaining, is_dp_enabled
    except Exception as e:
        raise HTTPException(500, f"differential_privacy unavailable: {e}")
    remaining = dp_budget_remaining(slug, user["user_id"])
    with _engine.connect() as conn:
        row = conn.execute(text("""
            SELECT budget_used, budget_max FROM dash.dash_dp_budget
            WHERE project_slug=:s AND user_id=:u AND date=CURRENT_DATE
        """), {"s": slug, "u": user["user_id"]}).first()
    used = float(row[0]) if row else 0.0
    bmax = float(row[1]) if row else 10.0
    return {
        "enabled": is_dp_enabled(slug),
        "budget_used": used,
        "budget_max": bmax,
        "budget_remaining": remaining,
        "date": "today",
    }


@router.patch("/{slug}/compliance/dp-budget")
async def compliance_dp_budget_set(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    # Admin role required
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, required_role="admin"):
            raise HTTPException(403, "admin role required")
    except HTTPException:
        raise
    except Exception:
        pass
    body = await request.json()
    new_max = float(body.get("budget_max", 10.0))
    if new_max < 0 or new_max > 1000:
        raise HTTPException(400, "budget_max must be in [0, 1000]")
    target_user = int(body.get("user_id", user["user_id"]))
    try:
        from dash.learning.differential_privacy import set_budget_max
        set_budget_max(slug, target_user, new_max)
    except Exception as e:
        raise HTTPException(500, f"set_budget failed: {e}")
    return {"ok": True, "budget_max": new_max, "user_id": target_user}


@router.post("/{slug}/compliance/test-dp")
async def compliance_test_dp(slug: str, request: Request):
    """Apply Laplace noise to a list of numbers. Returns noisy values + scale."""
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()
    nums = body.get("numbers") or []
    epsilon = float(body.get("epsilon", 1.0))
    if not isinstance(nums, list) or not nums:
        raise HTTPException(400, "numbers (non-empty list) required")
    if epsilon <= 0 or epsilon > 100:
        raise HTTPException(400, "epsilon must be in (0, 100]")
    try:
        from dash.learning.differential_privacy import add_laplace_noise, _infer_sensitivity
    except Exception as e:
        raise HTTPException(500, f"differential_privacy unavailable: {e}")
    try:
        floats = [float(x) for x in nums]
    except Exception:
        raise HTTPException(400, "numbers must be numeric")
    sens = _infer_sensitivity(floats)
    noisy = [round(add_laplace_noise(v, epsilon=epsilon, sensitivity=sens), 4) for v in floats]
    return {
        "original": floats,
        "noisy": noisy,
        "epsilon": epsilon,
        "sensitivity": sens,
        "scale": sens / epsilon,
    }




# === FK INFERER (Agent B) — added 2026-05-26 ===
@router.post("/{slug}/columns/infer-fks")
def trigger_infer_fks(slug: str, request: Request):
    """Run FK inference across all tables in this project's schema.

    Persists candidate relationships into ``public.dash_column_meta.relationships``.
    """
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, required_role="editor"):
        raise HTTPException(403, "editor role required")
    try:
        from dash.tools.fk_inferer import infer_fks
    except Exception as e:
        raise HTTPException(500, f"fk_inferer unavailable: {e}")
    try:
        result = infer_fks(slug)
        return result
    except Exception as e:
        raise HTTPException(500, f"infer_fks failed: {e}")


# ---------------------------------------------------------------------------
# Column Description Enrichment (migration 154 — dash_column_meta)
# ---------------------------------------------------------------------------

@router.get("/{slug}/columns/{table}")
def list_column_meta(slug: str, table: str, request: Request):
    """All enriched column-meta rows for a table. Viewer role."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT column_name, semantic_type, cardinality_class, description, "
            "samples, quality, suggested_questions, generation_model, generated_at "
            "FROM public.dash_column_meta "
            "WHERE project_slug = :s AND table_name = :t "
            "ORDER BY column_name"
        ), {"s": slug, "t": table}).fetchall()
    return {
        "project_slug": slug,
        "table_name": table,
        "count": len(rows),
        "columns": [
            {
                "column_name": r[0],
                "semantic_type": r[1],
                "cardinality_class": r[2],
                "description": r[3],
                "samples": r[4] or [],
                "quality": r[5] or {},
                "suggested_questions": r[6] or [],
                "generation_model": r[7],
                "generated_at": str(r[8]) if r[8] else None,
            }
            for r in rows
        ],
    }


@router.get("/{slug}/columns/{table}/{column}")
def get_column_meta(slug: str, table: str, column: str, request: Request):
    """Single enriched column-meta row. Viewer role."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        r = conn.execute(text(
            "SELECT column_name, semantic_type, cardinality_class, description, "
            "samples, quality, suggested_questions, relationships, glossary_term, "
            "glossary_link, owner, reviewed_at, provenance, generation_model, generated_at "
            "FROM public.dash_column_meta "
            "WHERE project_slug = :s AND table_name = :t AND column_name = :c"
        ), {"s": slug, "t": table, "c": column}).fetchone()
    if not r:
        raise HTTPException(404, "Column not enriched yet")
    return {
        "project_slug": slug,
        "table_name": table,
        "column_name": r[0],
        "semantic_type": r[1],
        "cardinality_class": r[2],
        "description": r[3],
        "samples": r[4] or [],
        "quality": r[5] or {},
        "suggested_questions": r[6] or [],
        "relationships": r[7] or [],
        "glossary_term": r[8],
        "glossary_link": r[9],
        "owner": r[10],
        "reviewed_at": str(r[11]) if r[11] else None,
        "provenance": r[12] or {},
        "generation_model": r[13],
        "generated_at": str(r[14]) if r[14] else None,
    }


@router.post("/{slug}/columns/describe")
async def describe_columns(slug: str, request: Request):
    """Trigger LLM column enrichment for a table (or all tables in the project).

    Body: {"table_name": "<optional>"}. Editor role.
    If table_name omitted, enriches every base table in the project schema.
    Fires async — returns immediately with status.
    """
    user = _get_user(request)
    from app.auth import check_project_permission
    perm = check_project_permission(user, slug, "editor")
    if not perm:
        raise HTTPException(403, "editor role required")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    table_name = (body or {}).get("table_name")

    try:
        from dash.tools.column_describer import enrich_columns_async, _project_schema
    except Exception as e:
        raise HTTPException(500, f"column_describer unavailable: {e}")

    tables: list[str] = []
    if table_name:
        tables = [str(table_name)]
    else:
        schema = _project_schema(slug)
        try:
            with _engine.connect() as conn:
                rs = conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name"
                ), {"s": schema}).fetchall()
                tables = [r[0] for r in rs]
        except Exception as e:
            raise HTTPException(500, f"list tables failed: {e}")

    if not tables:
        return {"status": "noop", "tables": [], "message": "no tables found"}

    # Fire async (don't block). If event loop is running, schedule tasks.
    import asyncio as _aio
    import threading as _th

    def _runner():
        async def _all():
            results = {}
            for t in tables:
                try:
                    results[t] = await enrich_columns_async(slug, t)
                except Exception as exc:
                    results[t] = {"enriched": 0, "skipped": 0, "errors": [str(exc)]}
            return results
        return _aio.run(_all())

    try:
        loop = _aio.get_running_loop()
        for t in tables:
            loop.create_task(enrich_columns_async(slug, t))
        return {"status": "scheduled", "tables": tables, "count": len(tables)}
    except RuntimeError:
        _th.Thread(target=_runner, daemon=True).start()
        return {"status": "started", "tables": tables, "count": len(tables)}
