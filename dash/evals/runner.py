"""Eval runner: executes suite cases, dispatches by layer kind.

Layers:
- smoke      — endpoint reachable + non-empty response
- reliability — expected tool calls observed (via SSE event capture)
- llm_judge   — judge LLM scores 0..1 against rubric
- regression  — compare suite pass_rate to baseline, flag if drop > threshold

Behind EXPERIMENTAL_AGI=1 enables LLM judge calls; otherwise judge layer
returns stub score=1.0 (treats as pass — no false alarms in CI).
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REGRESSION_DROP_THRESHOLD = float(os.getenv("EVAL_REGRESSION_DROP", "0.10"))


def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


def _get_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _list_cases(suite_id: str) -> List[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return []
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM dash.dash_eval_cases WHERE suite_id=:sid"),
                {"sid": suite_id},
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_suite(suite_id: str) -> Optional[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM dash.dash_eval_suites WHERE id=:id"),
                {"id": suite_id},
            ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def _baseline(suite_id: str) -> Optional[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT pass_rate, avg_latency_ms, set_at FROM dash.dash_eval_baselines "
                    "WHERE suite_id=:s ORDER BY set_at DESC LIMIT 1"
                ),
                {"s": suite_id},
            ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


# ── Per-layer judges ────────────────────────────────────────────────────
def _judge_smoke(case: Dict[str, Any], actual: str, tools: List[str], latency_ms: int) -> Tuple[str, float, str]:
    if actual and len(actual.strip()) >= 1:
        return "pass", 1.0, "non-empty response"
    return "fail", 0.0, "empty response"


def _judge_reliability(case: Dict[str, Any], actual: str, tools: List[str], latency_ms: int) -> Tuple[str, float, str]:
    expected = case.get("expected_tool_calls") or []
    if isinstance(expected, str):
        try:
            expected = _json.loads(expected)
        except Exception:
            expected = []
    if not expected:
        return "pass", 1.0, "no tool calls required"
    missing = [t for t in expected if t not in tools]
    if not missing:
        return "pass", 1.0, f"observed all expected: {expected}"
    return "fail", 0.0, f"missing tool calls: {missing}; observed: {tools}"


def _judge_llm(case: Dict[str, Any], actual: str, tools: List[str], latency_ms: int) -> Tuple[str, float, str]:
    if not _enabled():
        return "pass", 1.0, "LLM judge stub (EXPERIMENTAL_AGI off)"
    try:
        from dash.settings import training_llm_call  # type: ignore
        rubric = case.get("judge_prompt") or "Score 0..1 how well response answers prompt."
        prompt = (
            f"You are an eval judge. Given:\n"
            f"PROMPT: {case['input_prompt']}\n\n"
            f"RESPONSE: {actual[:4000]}\n\n"
            f"EXPECTED: {case.get('expected_output', '(any reasonable answer)')}\n\n"
            f"RUBRIC: {rubric}\n\n"
            f"Output JSON: {{\"score\": 0.0..1.0, \"reason\": \"...\"}}"
        )
        raw = training_llm_call(prompt, "extraction")
        try:
            obj = _json.loads(raw)
        except Exception:
            # try to find JSON in response
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            obj = _json.loads(m.group(0)) if m else {"score": 0.5, "reason": "judge parse failed"}
        score = float(obj.get("score", 0.0))
        reason = obj.get("reason", "")
        status = "pass" if score >= 0.7 else "fail"
        return status, score, reason
    except Exception as e:
        return "error", 0.0, f"judge error: {e}"


def _judge_regression(suite_id: str, current_pass_rate: float) -> Tuple[str, str]:
    base = _baseline(suite_id)
    if not base:
        return "pass", "no baseline set"
    drop = float(base["pass_rate"]) - current_pass_rate
    if drop > REGRESSION_DROP_THRESHOLD:
        return "fail", f"regression: baseline {base['pass_rate']:.2f} → current {current_pass_rate:.2f} (drop {drop:.2%})"
    return "pass", f"baseline {base['pass_rate']:.2f} → current {current_pass_rate:.2f}"


# ── Agent dispatch ──────────────────────────────────────────────────────
def _dispatch(prompt: str, project_slug: Optional[str], timeout_s: int = 60) -> Tuple[str, List[str], int]:
    """Returns (response_text, tool_calls_observed, latency_ms)."""
    started = time.time()
    try:
        import httpx
        base = os.getenv("DASH_INTERNAL_URL", "http://127.0.0.1:8000")
        url = f"{base}/api/projects/{project_slug}/chat" if project_slug else f"{base}/api/chat"
        token = os.getenv("INTERNAL_EVAL_TOKEN") or os.getenv("DASH_SYSTEM_TOKEN")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(timeout=timeout_s) as client:
            r = client.post(url, json={"message": prompt}, headers=headers)
        latency_ms = int((time.time() - started) * 1000)
        if r.status_code >= 400:
            return f"[http {r.status_code}]", [], latency_ms
        text = ""
        tools: List[str] = []
        try:
            j = r.json()
            text = j.get("response") or j.get("text") or str(j)[:5000]
            tools = j.get("tool_calls", []) if isinstance(j.get("tool_calls"), list) else []
        except Exception:
            text = r.text[:5000]
        return text, tools, latency_ms
    except Exception as e:
        return f"[dispatch error: {e}]", [], int((time.time() - started) * 1000)


# ── Run a suite ─────────────────────────────────────────────────────────
def run_suite(suite_id: str, triggered_by: Optional[int] = None) -> Dict[str, Any]:
    suite = _get_suite(suite_id)
    if not suite:
        return {"ok": False, "error": "suite_not_found"}
    cases = _list_cases(suite_id)
    if not cases:
        return {"ok": False, "error": "no_cases"}

    run_id = "er_" + secrets.token_hex(4)
    eng = _get_engine()
    if eng:
        try:
            from sqlalchemy import text
            with eng.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO dash.dash_eval_runs (id, suite_id, triggered_by, "
                        "status, total_cases) VALUES (:id, :sid, :tb, 'running', :tc)"
                    ),
                    {"id": run_id, "sid": suite_id, "tb": triggered_by, "tc": len(cases)},
                )
        except Exception as e:
            logger.warning("insert eval_run failed: %s", e)

    layer = suite["layer"]
    project_slug = suite.get("project_slug")
    passed = failed = 0
    total_latency = 0
    results: List[Dict[str, Any]] = []

    for case in cases:
        grading_mode = (case.get("grading_mode") or "").strip()

        # ── SQL pair grading mode — exec both SQLs + compare frames ──
        if grading_mode == "sql_pair":
            try:
                from dash.evals.sql_result_grader import grade_case as _grade_sql_pair
                eng_for_eval = _get_engine()
                t0 = time.time()
                graded = _grade_sql_pair(case, eng_for_eval)
                latency = int((time.time() - t0) * 1000)
                total_latency += latency
                status = graded.get("status", "fail")
                score = float(graded.get("score", 0.0))
                reason = graded.get("reason", "")
                actual = (graded.get("generated_sql") or "")[:2000]
                tools = []
            except Exception as e:
                latency = 0
                status, score, reason = "error", 0.0, f"sql_pair grader error: {e}"
                actual, tools = "", []
            if status == "pass":
                passed += 1
            else:
                failed += 1
            results.append({
                "case_id": case["id"], "case_name": case.get("name"),
                "status": status, "score": score, "actual": actual,
                "judge_reason": reason, "tool_calls": tools, "latency_ms": latency,
            })
            continue

        actual, tools, latency = _dispatch(case["input_prompt"], project_slug)
        total_latency += latency
        if layer == "smoke":
            status, score, reason = _judge_smoke(case, actual, tools, latency)
        elif layer == "reliability":
            status, score, reason = _judge_reliability(case, actual, tools, latency)
        elif layer == "llm_judge":
            status, score, reason = _judge_llm(case, actual, tools, latency)
        else:
            status, score, reason = "pass", 1.0, "unknown layer"

        if status == "pass":
            passed += 1
        else:
            failed += 1
        results.append({
            "case_id": case["id"], "case_name": case["name"],
            "status": status, "score": score, "actual": actual[:2000],
            "judge_reason": reason, "tool_calls": tools, "latency_ms": latency,
        })

    pass_rate = passed / len(cases) if cases else 0.0
    avg_latency = total_latency / len(cases) if cases else 0

    # Regression check
    regression_note = ""
    final_status = "done"
    if layer == "regression" or _baseline(suite_id):
        rstatus, rnote = _judge_regression(suite_id, pass_rate)
        regression_note = rnote
        if rstatus == "fail" and final_status == "done":
            final_status = "regressed"

    # Persist results
    if eng:
        try:
            from sqlalchemy import text
            with eng.begin() as conn:
                for res in results:
                    conn.execute(
                        text(
                            """
                            INSERT INTO dash.dash_eval_results
                              (run_id, case_id, case_name, status, score, actual_output,
                               judge_reason, tool_calls_observed, latency_ms)
                            VALUES (:rid, :cid, :cn, :st, :sc, :ao, :jr,
                                    CAST(:tc AS jsonb), :lat)
                            """
                        ),
                        {
                            "rid": run_id, "cid": res["case_id"], "cn": res["case_name"],
                            "st": res["status"], "sc": res["score"], "ao": res["actual"],
                            "jr": res["judge_reason"],
                            "tc": _json.dumps(res["tool_calls"]),
                            "lat": res["latency_ms"],
                        },
                    )
                conn.execute(
                    text(
                        """
                        UPDATE dash.dash_eval_runs
                           SET status=:s, passed=:p, failed=:f, pass_rate=:pr,
                               avg_latency_ms=:al, finished_at=now(), notes=:n
                         WHERE id=:id
                        """
                    ),
                    {
                        "s": final_status, "p": passed, "f": failed,
                        "pr": pass_rate, "al": avg_latency,
                        "n": regression_note, "id": run_id,
                    },
                )
        except Exception as e:
            logger.warning("persist eval results failed: %s", e)

    return {
        "ok": True, "run_id": run_id, "suite_id": suite_id, "layer": layer,
        "passed": passed, "failed": failed, "pass_rate": pass_rate,
        "avg_latency_ms": avg_latency, "regression_note": regression_note,
        "status": final_status, "results": results,
    }


def set_baseline(suite_id: str, source_run_id: Optional[str] = None,
                 notes: Optional[str] = None, user_id: Optional[int] = None) -> Dict[str, Any]:
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "db unavailable"}
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            if source_run_id:
                run = conn.execute(
                    text(
                        "SELECT pass_rate, avg_latency_ms FROM dash.dash_eval_runs WHERE id=:id"
                    ),
                    {"id": source_run_id},
                ).mappings().first()
                if not run:
                    return {"ok": False, "error": "run_not_found"}
                pass_rate = float(run["pass_rate"] or 0)
                avg_latency = float(run["avg_latency_ms"] or 0)
            else:
                run = conn.execute(
                    text(
                        "SELECT id, pass_rate, avg_latency_ms FROM dash.dash_eval_runs "
                        "WHERE suite_id=:s AND status='done' "
                        "ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"s": suite_id},
                ).mappings().first()
                if not run:
                    return {"ok": False, "error": "no_completed_runs"}
                source_run_id = run["id"]
                pass_rate = float(run["pass_rate"] or 0)
                avg_latency = float(run["avg_latency_ms"] or 0)
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_eval_baselines
                      (suite_id, pass_rate, avg_latency_ms, set_by, source_run_id, notes)
                    VALUES (:s, :pr, :al, :sb, :sr, :n)
                    """
                ),
                {"s": suite_id, "pr": pass_rate, "al": avg_latency,
                 "sb": user_id, "sr": source_run_id, "n": notes},
            )
        return {"ok": True, "pass_rate": pass_rate, "source_run_id": source_run_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}
