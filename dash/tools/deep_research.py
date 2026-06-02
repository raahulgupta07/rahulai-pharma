"""DeepResearch — 9-stage research artifact pipeline.

Pattern mirrors `dash/tools/deep_deck.py` (Deep Deck Vision-QA). Each stage
is fail-soft (logs + records into stages_log, never raises). The final
artifact is rendered to PDF bytes via `dash.tools.research_pdf.render`.

Stages
------
    1. SCOPE             parse question → ResearchSpec (intent, entities, time_range)
    2. HYPOTHESIS_TREE   3-5 hypotheses × 2-3 sub-questions
    3. PLAN_SQL          1-2 SQL queries per leaf sub-question
    4. PARALLEL_EXEC     asyncio.gather of read-only SQL against project schema
    5. EVIDENCE_RANKING  LLM judge scores each result 0-1 for relevance
    6. SYNTHESIS         one finding paragraph per hypothesis, citing data
    7. CROSS_CHECK       LLM self-critique: "do findings contradict?"
    8. RECOMMENDATION    3-5 ranked recommendations w/ confidence
    9. RENDER            research_pdf.render(spec) → bytes

Return dict:
    {
      "spec": {...scope...},
      "hypothesis_tree": [...],
      "findings": [...],
      "cross_check": {...},
      "recommendations": [...],
      "pdf_bytes": b"...",
      "stages_log": [{stage, status, message, elapsed_ms, ts}, ...],
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────
def _get_engine():
    """Resolve project SQL engine. Mirrors deep_deck._get_engine."""
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _llm_call(prompt: str, task: str = "extraction") -> Optional[str]:
    """Centralized LLM call. Fail-soft. Mirrors deep_deck._llm_call."""
    try:
        from dash.settings import training_llm_call
        return training_llm_call(prompt, task=task)
    except Exception as e:
        logger.warning("deep_research llm call failed (task=%s): %s", task, e)
        return None


def _parse_json_lenient(text: str) -> Optional[Any]:
    """Strip ``` fences, find first balanced object/array, json.loads."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"[\[{].*[\]}]", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _to_json_safe(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return float(v)
    except Exception:
        pass
    try:
        return str(v)
    except Exception:
        return None


def _maybe_trace_span(name: str, project_slug: Optional[str] = None):
    """Best-effort trace span. Returns context manager (nullcontext on miss)."""
    try:
        from dash.obs.trace import trace_span
        return trace_span(name, kind="research", project_slug=project_slug)
    except Exception:
        from contextlib import nullcontext
        return nullcontext()


def _log_stage(log: List[Dict[str, Any]], stage: str, status: str,
               message: str = "", elapsed_ms: Optional[int] = None,
               data: Any = None) -> None:
    log.append({
        "stage": stage,
        "status": status,
        "message": message,
        "elapsed_ms": elapsed_ms,
        "ts": time.time(),
        "data": data,
    })


# ── Schema introspection (for SQL planner grounding) ───────────────────
def _introspect_schema(project_slug: str) -> Dict[str, List[str]]:
    """Return {table_name: ['col:type', ...]} for project schema. Fail-soft."""
    out: Dict[str, List[str]] = {}
    try:
        from sqlalchemy import text
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        from db import db_url
        eng = create_engine(db_url, poolclass=NullPool)
        with eng.connect() as conn:
            tables = conn.execute(
                text("SELECT table_name FROM information_schema.tables "
                     "WHERE table_schema = :s AND table_type = 'BASE TABLE' "
                     "ORDER BY table_name LIMIT 60"),
                {"s": project_slug},
            ).fetchall()
            tnames = [r[0] for r in tables if r and r[0]]
            if tnames:
                cols = conn.execute(
                    text("SELECT table_name, column_name, data_type "
                         "FROM information_schema.columns "
                         "WHERE table_schema = :s "
                         "ORDER BY table_name, ordinal_position"),
                    {"s": project_slug},
                ).fetchall()
                for tn, cn, dt in cols:
                    out.setdefault(tn, []).append(f"{cn}:{dt}")
    except Exception as e:
        logger.warning("deep_research _introspect_schema failed: %s", e)
    return out


def _schema_block(table_columns: Dict[str, List[str]], cap_tables: int = 20,
                  cap_cols: int = 25) -> str:
    if not table_columns:
        return "(no schema available)"
    lines = []
    for tn in list(table_columns.keys())[:cap_tables]:
        cols = table_columns[tn][:cap_cols]
        lines.append(f"  {tn}({', '.join(cols)})")
    return "\n".join(lines)


# ── STAGE 1: SCOPE ──────────────────────────────────────────────────────
def stage_scope(question: str, project_slug: str) -> Dict[str, Any]:
    """Parse question → intent + entities + time_range."""
    prompt = (
        "You are a senior research analyst. Parse the following research question "
        "into a structured spec. Identify the research intent, the key entities "
        "(business objects, KPIs, segments), and any time range mentioned.\n\n"
        f"QUESTION:\n{question}\n\n"
        'Return ONLY JSON: {"intent": "1-sentence research intent", '
        '"entities": ["..."], "time_range": "e.g. last 90 days / Q2 2025 / all-time"}'
    )
    raw = _llm_call(prompt, task="deep_analysis")
    parsed = _parse_json_lenient(raw or "") or {}
    return {
        "question": question[:1000],
        "project_slug": project_slug,
        "intent": (parsed.get("intent") or "")[:400],
        "entities": [str(e)[:120] for e in (parsed.get("entities") or [])][:15],
        "time_range": (parsed.get("time_range") or "")[:120],
    }


# ── STAGE 2: HYPOTHESIS TREE ───────────────────────────────────────────
def stage_hypothesis_tree(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate 3-5 hypotheses, each w/ 2-3 sub-questions.

    Tries to delegate to dash.learning.hypothesis if available, otherwise
    inline LLM call. Returns list of {hypothesis, sub_questions[]}.
    """
    prompt = (
        "You are a senior analyst building a hypothesis tree for a research question.\n\n"
        f"RESEARCH QUESTION: {spec.get('question', '')}\n"
        f"INTENT: {spec.get('intent', '')}\n"
        f"ENTITIES: {', '.join(spec.get('entities') or [])}\n"
        f"TIME RANGE: {spec.get('time_range', '')}\n\n"
        "Generate 3-5 distinct testable hypotheses. For each, propose 2-3 "
        "sub-questions that can be answered with SQL against an internal database. "
        "Sub-questions must be specific and data-grounded (counts, sums, breakdowns, "
        "trends, comparisons).\n\n"
        'Return ONLY JSON: {"hypotheses": [{"hypothesis": "...", '
        '"sub_questions": ["...", "..."]}, ...]}'
    )
    raw = _llm_call(prompt, task="deep_analysis")
    parsed = _parse_json_lenient(raw or "")
    tree: List[Dict[str, Any]] = []
    if parsed and isinstance(parsed, dict):
        for h in (parsed.get("hypotheses") or [])[:5]:
            if not isinstance(h, dict):
                continue
            hyp = (h.get("hypothesis") or "").strip()
            subs = [str(s)[:300] for s in (h.get("sub_questions") or [])][:3]
            if hyp and subs:
                tree.append({"hypothesis": hyp[:400], "sub_questions": subs})
    return tree


# ── STAGE 3: PLAN SQL ──────────────────────────────────────────────────
def stage_plan_sql(tree: List[Dict[str, Any]], project_slug: str,
                   table_columns: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """For each leaf sub-question, propose 1-2 SQL queries.

    Returns list of {hypothesis_idx, sub_question, sql, expected_shape}.
    """
    schema = _schema_block(table_columns)
    plans: List[Dict[str, Any]] = []
    for hi, node in enumerate(tree):
        for sq in (node.get("sub_questions") or [])[:3]:
            prompt = (
                f"Write ONE read-only SQL query (Postgres dialect) to answer: \"{sq}\"\n\n"
                f"SCHEMA (table(col:type, ...)):\n{schema}\n\n"
                "Rules:\n"
                "- SELECT only. NO DDL/DML.\n"
                "- Use ONLY columns shown in SCHEMA above. NEVER invent column names.\n"
                "- Reference tables by bare name (search_path set to project schema).\n"
                "- Add LIMIT 500 (we cap anyway).\n"
                "- Use clear aliases. Use DATE_TRUNC for time grouping.\n"
                "- If the question can't be answered from this schema, return "
                '{"sql": "", "expected_shape": "unanswerable"}.\n\n'
                'Return ONLY JSON: {"sql": "SELECT ...", "expected_shape": "..."}'
            )
            raw = _llm_call(prompt, task="extraction")
            parsed = _parse_json_lenient(raw or "")
            sql_text = ""
            shape = ""
            if parsed and isinstance(parsed, dict):
                sql_text = (parsed.get("sql") or "").strip()
                shape = (parsed.get("expected_shape") or "").strip()
            if sql_text and re.match(r"^\s*(SELECT|WITH)\b", sql_text, re.IGNORECASE):
                plans.append({
                    "hypothesis_idx": hi,
                    "sub_question": sq,
                    "sql": sql_text[:4000],
                    "expected_shape": shape[:200],
                })
    return plans


# ── STAGE 4: PARALLEL EXEC ─────────────────────────────────────────────
def _execute_one(project_slug: str, sql_text: str, timeout_s: int = 30,
                 row_cap: int = 500) -> Dict[str, Any]:
    """Execute single SQL read-only against project schema. Fail-soft."""
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "no_engine"}
    try:
        from sqlalchemy import text
        sched = time.time()
        with eng.begin() as conn:
            try:
                conn.execute(text(f"SET LOCAL statement_timeout = '{int(timeout_s)}s'"))
            except Exception:
                pass
            try:
                conn.execute(text(f'SET LOCAL search_path TO "{project_slug}", public, dash'))
            except Exception:
                pass
            res = conn.execute(text(sql_text))
            cols = list(res.keys())
            rows = res.fetchmany(row_cap)
        out_rows: List[Dict[str, Any]] = []
        for row in rows:
            try:
                out_rows.append({c: _to_json_safe(v) for c, v in zip(cols, row)})
            except Exception:
                continue
        return {
            "ok": True,
            "columns": cols,
            "row_count": len(out_rows),
            "rows": out_rows,
            "duration_ms": int((time.time() - sched) * 1000),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:400]}


async def stage_parallel_exec(plans: List[Dict[str, Any]],
                              project_slug: str) -> List[Dict[str, Any]]:
    """Run all plan SQLs concurrently via asyncio.to_thread."""
    if not plans:
        return []

    async def _run_one(p: Dict[str, Any]) -> Dict[str, Any]:
        res = await asyncio.to_thread(_execute_one, project_slug, p["sql"])
        return {**p, **res}

    results = await asyncio.gather(
        *[_run_one(p) for p in plans], return_exceptions=True
    )
    out: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            out.append({"ok": False, "error": str(r)[:400]})
        else:
            out.append(r)
    return out


# ── STAGE 5: EVIDENCE RANKING ──────────────────────────────────────────
def stage_evidence_ranking(spec: Dict[str, Any],
                           executed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """LLM judge: score each successful result 0-1 for relevance to question.

    Mutates executed entries in-place adding `relevance` + `rank_notes`.
    Failed/no-row results get relevance=0.
    """
    if not executed:
        return executed
    snippets: List[Dict[str, Any]] = []
    for i, e in enumerate(executed):
        if not e.get("ok") or not e.get("rows"):
            e["relevance"] = 0.0
            e["rank_notes"] = "no rows or error"
            continue
        snippets.append({
            "idx": i,
            "sub_question": e.get("sub_question", "")[:200],
            "row_count": e.get("row_count", 0),
            "columns": e.get("columns", []),
            "sample": e.get("rows", [])[:3],
        })
    if not snippets:
        return executed

    prompt = (
        "You are an evidence judge. Given a research question and SQL result "
        "snippets, score each snippet's relevance 0.0-1.0 based on:\n"
        "- Does it directly address the sub-question?\n"
        "- Is row count reasonable (>0, not pathological)?\n"
        "- Are columns meaningful for the claim?\n\n"
        f"RESEARCH QUESTION: {spec.get('question', '')}\n"
        f"INTENT: {spec.get('intent', '')}\n\n"
        f"SNIPPETS:\n{json.dumps(snippets, default=str)[:6000]}\n\n"
        'Return ONLY JSON: {"scores": [{"idx": 0, "relevance": 0.85, '
        '"notes": "..."}, ...]}'
    )
    raw = _llm_call(prompt, task="deep_analysis")
    parsed = _parse_json_lenient(raw or "")
    if parsed and isinstance(parsed, dict):
        for s in (parsed.get("scores") or []):
            try:
                idx = int(s.get("idx"))
                if 0 <= idx < len(executed):
                    executed[idx]["relevance"] = max(0.0, min(1.0, float(s.get("relevance") or 0.0)))
                    executed[idx]["rank_notes"] = (s.get("notes") or "")[:300]
            except Exception:
                continue
    # Default any unscored OK entries
    for e in executed:
        e.setdefault("relevance", 0.5 if e.get("ok") else 0.0)
        e.setdefault("rank_notes", "")
    return executed


# ── STAGE 6: SYNTHESIS ─────────────────────────────────────────────────
def stage_synthesis(spec: Dict[str, Any], tree: List[Dict[str, Any]],
                    executed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per hypothesis, write 1-paragraph finding citing data."""
    findings: List[Dict[str, Any]] = []
    for hi, node in enumerate(tree):
        evidence = [e for e in executed if e.get("hypothesis_idx") == hi and e.get("ok")]
        evidence.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)
        evidence = evidence[:4]
        ev_summary = []
        for e in evidence:
            ev_summary.append(
                f"- sub_q: {e.get('sub_question', '')[:150]} | "
                f"rows={e.get('row_count', 0)} | rel={e.get('relevance', 0):.2f} | "
                f"sample={json.dumps(e.get('rows', [])[:3], default=str)[:600]}"
            )
        ev_block = "\n".join(ev_summary) if ev_summary else "(no supporting data)"
        prompt = (
            "You are a research analyst writing a finding paragraph for one "
            "hypothesis. Cite specific numbers from the evidence. Stay concise "
            "(3-5 sentences). NEVER invent numbers — if evidence is missing, "
            "say so explicitly.\n\n"
            f"RESEARCH QUESTION: {spec.get('question', '')}\n"
            f"HYPOTHESIS: {node.get('hypothesis', '')}\n\n"
            f"EVIDENCE:\n{ev_block}\n\n"
            "Return ONLY plain prose (no JSON, no markdown, no bullet points)."
        )
        raw = _llm_call(prompt, task="deep_analysis") or ""
        finding_text = raw.strip()[:1800]
        # Aggregate rows + columns for PDF table (use top-relevance evidence)
        top_rows: List[Dict[str, Any]] = []
        top_cols: List[str] = []
        if evidence:
            top = evidence[0]
            top_rows = top.get("rows") or []
            top_cols = top.get("columns") or []
        findings.append({
            "hypothesis_idx": hi,
            "hypothesis": node.get("hypothesis", ""),
            "finding": finding_text,
            "rows": top_rows[:10],
            "columns": top_cols,
            "evidence_count": len(evidence),
        })
    return findings


# ── STAGE 7: CROSS-CHECK ───────────────────────────────────────────────
def stage_cross_check(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """LLM self-critique: do findings contradict?"""
    if not findings:
        return {"verdict": "no findings", "notes": ""}
    block = "\n\n".join(
        f"H{f.get('hypothesis_idx', i)}: {f.get('hypothesis', '')}\n"
        f"FINDING: {f.get('finding', '')}"
        for i, f in enumerate(findings)
    )
    prompt = (
        "You are a peer reviewer. Read these findings and identify any "
        "contradictions, tensions, or unresolved gaps between them. Be honest "
        "— if they're consistent, say so.\n\n"
        f"FINDINGS:\n{block[:6000]}\n\n"
        'Return ONLY JSON: {"verdict": "consistent|tensions|contradictory", '
        '"notes": "1-3 sentence explanation"}'
    )
    raw = _llm_call(prompt, task="deep_analysis")
    parsed = _parse_json_lenient(raw or "")
    if parsed and isinstance(parsed, dict):
        return {
            "verdict": (parsed.get("verdict") or "consistent")[:40],
            "notes": (parsed.get("notes") or "")[:1500],
        }
    return {"verdict": "consistent", "notes": ""}


# ── STAGE 8: RECOMMENDATION ────────────────────────────────────────────
def stage_recommendation(spec: Dict[str, Any], findings: List[Dict[str, Any]],
                         cross_check: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Produce 3-5 ranked recommendations w/ confidence."""
    block = "\n".join(
        f"- {f.get('hypothesis', '')} → {f.get('finding', '')[:300]}"
        for f in findings
    )
    prompt = (
        "You are a senior consultant producing actionable recommendations from "
        "a research dossier. Each recommendation needs an action, a confidence "
        "level (high/medium/low) based on evidence strength, and a 1-sentence "
        "rationale.\n\n"
        f"RESEARCH QUESTION: {spec.get('question', '')}\n"
        f"FINDINGS:\n{block[:5000]}\n\n"
        f"CROSS-CHECK VERDICT: {cross_check.get('verdict', '')}\n"
        f"CROSS-CHECK NOTES: {cross_check.get('notes', '')}\n\n"
        "Produce 3-5 recommendations, ranked highest-impact first.\n\n"
        'Return ONLY JSON: {"recommendations": [{"action": "...", '
        '"confidence": "high|medium|low", "rationale": "..."}, ...]}'
    )
    raw = _llm_call(prompt, task="deep_analysis")
    parsed = _parse_json_lenient(raw or "")
    out: List[Dict[str, Any]] = []
    if parsed and isinstance(parsed, dict):
        for r in (parsed.get("recommendations") or [])[:5]:
            if not isinstance(r, dict):
                continue
            action = (r.get("action") or "").strip()
            if not action:
                continue
            out.append({
                "action": action[:400],
                "confidence": (r.get("confidence") or "medium").lower()[:10],
                "rationale": (r.get("rationale") or "")[:600],
            })
    return out


# ── DeepResearch orchestrator ──────────────────────────────────────────
class DeepResearch:
    """9-stage research-then-render pipeline."""

    def __init__(self, project_slug: Optional[str] = None):
        self.project_slug = project_slug

    def run(self, question: str, project_slug: Optional[str] = None) -> Dict[str, Any]:
        slug = project_slug or self.project_slug or ""
        log: List[Dict[str, Any]] = []

        # ── STAGE 1 ─────────────────────────────────────────────────────
        spec: Dict[str, Any] = {"question": question, "project_slug": slug}
        with _maybe_trace_span("research.scope", slug):
            t0 = time.time()
            try:
                spec = stage_scope(question, slug)
                _log_stage(log, "scope", "ok", elapsed_ms=int((time.time() - t0) * 1000))
            except Exception as e:
                logger.exception("stage_scope failed: %s", e)
                _log_stage(log, "scope", "error", str(e)[:200],
                           int((time.time() - t0) * 1000))

        # ── STAGE 2 ─────────────────────────────────────────────────────
        tree: List[Dict[str, Any]] = []
        with _maybe_trace_span("research.hypothesis_tree", slug):
            t0 = time.time()
            try:
                tree = stage_hypothesis_tree(spec)
                _log_stage(log, "hypothesis_tree", "ok",
                           f"{len(tree)} hypotheses",
                           int((time.time() - t0) * 1000))
            except Exception as e:
                logger.exception("stage_hypothesis_tree failed: %s", e)
                _log_stage(log, "hypothesis_tree", "error", str(e)[:200],
                           int((time.time() - t0) * 1000))

        # ── Schema introspection (no LLM, no logged stage) ─────────────
        table_columns: Dict[str, List[str]] = {}
        if slug:
            try:
                table_columns = _introspect_schema(slug)
            except Exception as e:
                logger.warning("schema introspection failed: %s", e)

        # ── STAGE 3 ─────────────────────────────────────────────────────
        plans: List[Dict[str, Any]] = []
        with _maybe_trace_span("research.plan_sql", slug):
            t0 = time.time()
            try:
                plans = stage_plan_sql(tree, slug, table_columns)
                _log_stage(log, "plan_sql", "ok", f"{len(plans)} SQL plans",
                           int((time.time() - t0) * 1000))
            except Exception as e:
                logger.exception("stage_plan_sql failed: %s", e)
                _log_stage(log, "plan_sql", "error", str(e)[:200],
                           int((time.time() - t0) * 1000))

        # ── STAGE 4 ─────────────────────────────────────────────────────
        executed: List[Dict[str, Any]] = []
        with _maybe_trace_span("research.parallel_exec", slug):
            t0 = time.time()
            try:
                # Drive async stage from sync caller. If we're already in a
                # running loop, fall back to thread + new loop.
                try:
                    executed = asyncio.run(stage_parallel_exec(plans, slug))
                except RuntimeError:
                    # Already inside an event loop — run via threadpool
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        fut = ex.submit(lambda: asyncio.new_event_loop().run_until_complete(
                            stage_parallel_exec(plans, slug)))
                        executed = fut.result(timeout=180)
                ok_count = sum(1 for e in executed if e.get("ok"))
                _log_stage(log, "parallel_exec", "ok",
                           f"{ok_count}/{len(executed)} ok",
                           int((time.time() - t0) * 1000))
            except Exception as e:
                logger.exception("stage_parallel_exec failed: %s", e)
                _log_stage(log, "parallel_exec", "error", str(e)[:200],
                           int((time.time() - t0) * 1000))

        # ── STAGE 5 ─────────────────────────────────────────────────────
        with _maybe_trace_span("research.evidence_ranking", slug):
            t0 = time.time()
            try:
                executed = stage_evidence_ranking(spec, executed)
                _log_stage(log, "evidence_ranking", "ok", "",
                           int((time.time() - t0) * 1000))
            except Exception as e:
                logger.exception("stage_evidence_ranking failed: %s", e)
                _log_stage(log, "evidence_ranking", "error", str(e)[:200],
                           int((time.time() - t0) * 1000))

        # ── STAGE 6 ─────────────────────────────────────────────────────
        findings: List[Dict[str, Any]] = []
        with _maybe_trace_span("research.synthesis", slug):
            t0 = time.time()
            try:
                findings = stage_synthesis(spec, tree, executed)
                _log_stage(log, "synthesis", "ok", f"{len(findings)} findings",
                           int((time.time() - t0) * 1000))
            except Exception as e:
                logger.exception("stage_synthesis failed: %s", e)
                _log_stage(log, "synthesis", "error", str(e)[:200],
                           int((time.time() - t0) * 1000))

        # ── STAGE 7 ─────────────────────────────────────────────────────
        cross_check: Dict[str, Any] = {"verdict": "unknown", "notes": ""}
        with _maybe_trace_span("research.cross_check", slug):
            t0 = time.time()
            try:
                cross_check = stage_cross_check(findings)
                _log_stage(log, "cross_check", "ok",
                           cross_check.get("verdict", ""),
                           int((time.time() - t0) * 1000))
            except Exception as e:
                logger.exception("stage_cross_check failed: %s", e)
                _log_stage(log, "cross_check", "error", str(e)[:200],
                           int((time.time() - t0) * 1000))

        # ── STAGE 8 ─────────────────────────────────────────────────────
        recommendations: List[Dict[str, Any]] = []
        with _maybe_trace_span("research.recommendation", slug):
            t0 = time.time()
            try:
                recommendations = stage_recommendation(spec, findings, cross_check)
                _log_stage(log, "recommendation", "ok",
                           f"{len(recommendations)} recs",
                           int((time.time() - t0) * 1000))
            except Exception as e:
                logger.exception("stage_recommendation failed: %s", e)
                _log_stage(log, "recommendation", "error", str(e)[:200],
                           int((time.time() - t0) * 1000))

        # ── STAGE 9: RENDER ────────────────────────────────────────────
        pdf_bytes = b""
        with _maybe_trace_span("research.render", slug):
            t0 = time.time()
            try:
                from dash.tools.research_pdf import render
                from datetime import datetime, timezone
                pdf_spec = {
                    "title": "Deep Research",
                    "question": spec.get("question", ""),
                    "project_slug": slug,
                    "project_name": slug,
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    "scope": {
                        "intent": spec.get("intent", ""),
                        "entities": spec.get("entities", []),
                        "time_range": spec.get("time_range", ""),
                    },
                    "hypothesis_tree": tree,
                    "findings": findings,
                    "cross_check": cross_check,
                    "recommendations": recommendations,
                    "sql_appendix": [
                        {"question": p.get("sub_question", ""), "sql": p.get("sql", "")}
                        for p in plans
                    ],
                }
                pdf_bytes = render(pdf_spec)
                _log_stage(log, "render", "ok", f"{len(pdf_bytes)} bytes",
                           int((time.time() - t0) * 1000))
            except Exception as e:
                logger.exception("stage_render failed: %s", e)
                _log_stage(log, "render", "error", str(e)[:200],
                           int((time.time() - t0) * 1000))

        return {
            "spec": spec,
            "hypothesis_tree": tree,
            "plans": plans,
            "executed": executed,
            "findings": findings,
            "cross_check": cross_check,
            "recommendations": recommendations,
            "pdf_bytes": pdf_bytes,
            "stages_log": log,
        }
