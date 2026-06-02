"""SQL result-set grader — OpenAI-style.

Executes both generated_sql + expected_sql against the eval engine and
compares result frames. LLM-as-judge scores the variance and reasoning.

Public API:
    exec_sql(engine, sql, timeout=30)          -> pd.DataFrame
    compare_frames(gen_df, expected_df, ...)   -> dict
    judge_sql_pair(gen_sql, expected_sql, ...) -> dict
    grade_case(case_row, engine)               -> dict (full eval record)
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text as _sa_text

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_UNSAFE_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|merge|create|replace)\b",
    re.IGNORECASE,
)


def _clean_sql(sql: str) -> str:
    if not sql:
        return ""
    s = sql.strip()
    m = _FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()
    return s.rstrip(";").strip()


def _is_safe_sql(sql: str) -> bool:
    if not sql:
        return False
    if _UNSAFE_RE.search(sql):
        return False
    head = sql.lstrip().lower()
    return head.startswith("select") or head.startswith("with")


def exec_sql(engine, sql: str, timeout: int = 30) -> pd.DataFrame:
    """Execute a read-only SQL against the engine and return a DataFrame.

    Fail-soft: raises on bad SQL so caller can categorise the failure.
    """
    cleaned = _clean_sql(sql)
    if not _is_safe_sql(cleaned):
        raise ValueError(f"unsafe or non-SELECT SQL rejected: {cleaned[:200]}")
    if engine is None:
        raise RuntimeError("no engine available for exec_sql")
    with engine.connect() as conn:
        try:
            conn.execute(_sa_text(f"SET LOCAL statement_timeout = {int(timeout) * 1000}"))
        except Exception:
            pass
        result = conn.execute(_sa_text(cleaned))
        rows = result.fetchall()
        cols = list(result.keys()) if hasattr(result, "keys") else []
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    return df


def _sort_invariant_signature(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Project to shared cols, coerce to str for stable compare, sort rows."""
    if df.empty or not cols:
        return df
    sub = df[cols].copy()
    for c in cols:
        sub[c] = sub[c].apply(lambda v: "" if pd.isna(v) else str(v))
    return sub.sort_values(by=cols, kind="mergesort").reset_index(drop=True)


def _numeric_diff_pct(gen_df: pd.DataFrame, exp_df: pd.DataFrame, cols: list[str], eps: float) -> float:
    """Mean relative diff across numeric cols. Returns 0..1 (1 = total mismatch)."""
    diffs: list[float] = []
    for c in cols:
        try:
            g = pd.to_numeric(gen_df[c], errors="coerce")
            e = pd.to_numeric(exp_df[c], errors="coerce")
        except Exception:
            continue
        if g.isna().all() or e.isna().all():
            continue
        n = min(len(g), len(e))
        if n == 0:
            continue
        for i in range(n):
            gv, ev = g.iloc[i], e.iloc[i]
            if pd.isna(gv) and pd.isna(ev):
                continue
            if pd.isna(gv) or pd.isna(ev):
                diffs.append(1.0)
                continue
            denom = max(abs(float(ev)), eps)
            diffs.append(min(1.0, abs(float(gv) - float(ev)) / denom))
    if not diffs:
        return 0.0
    return sum(diffs) / len(diffs)


def compare_frames(
    gen_df: pd.DataFrame,
    expected_df: pd.DataFrame,
    numeric_eps: float = 1e-6,
) -> Dict[str, Any]:
    """Sort-invariant frame compare. Tolerates extra cols in gen_df.

    Returns dict with: match, reason, column_superset_ok, row_count_match,
    value_diff_pct, shared_cols.
    """
    out: Dict[str, Any] = {
        "match": False,
        "reason": "",
        "column_superset_ok": False,
        "row_count_match": False,
        "value_diff_pct": 1.0,
        "shared_cols": [],
        "gen_shape": [int(gen_df.shape[0]), int(gen_df.shape[1])],
        "expected_shape": [int(expected_df.shape[0]), int(expected_df.shape[1])],
    }
    exp_cols = list(expected_df.columns)
    gen_cols = list(gen_df.columns)
    shared = [c for c in exp_cols if c in gen_cols]
    out["shared_cols"] = shared
    out["column_superset_ok"] = len(shared) == len(exp_cols)

    if not out["column_superset_ok"]:
        missing = [c for c in exp_cols if c not in gen_cols]
        out["reason"] = f"missing expected columns: {missing[:5]}"
        return out

    out["row_count_match"] = len(gen_df) == len(expected_df)

    if expected_df.empty and gen_df[shared].empty:
        out["match"] = True
        out["reason"] = "both empty result-sets"
        out["value_diff_pct"] = 0.0
        return out

    gen_sig = _sort_invariant_signature(gen_df, shared)
    exp_sig = _sort_invariant_signature(expected_df, shared)

    # exact stringified equality first
    if gen_sig.equals(exp_sig):
        out["match"] = True
        out["reason"] = "exact match on sort-invariant compare"
        out["value_diff_pct"] = 0.0
        return out

    # numeric tolerance pass
    numeric_cols = [c for c in shared if pd.api.types.is_numeric_dtype(expected_df[c])]
    if numeric_cols and out["row_count_match"]:
        diff_pct = _numeric_diff_pct(gen_sig, exp_sig, numeric_cols, numeric_eps)
        out["value_diff_pct"] = round(diff_pct, 6)
        if diff_pct <= numeric_eps:
            out["match"] = True
            out["reason"] = f"numeric tolerance match (diff_pct={diff_pct:.6f})"
            return out
        out["reason"] = f"numeric diff above eps (diff_pct={diff_pct:.4f})"
        return out

    out["reason"] = (
        f"value mismatch on sort-invariant compare "
        f"(gen_rows={len(gen_df)}, expected_rows={len(expected_df)})"
    )
    out["value_diff_pct"] = 1.0 if not out["row_count_match"] else 0.5
    return out


def judge_sql_pair(
    gen_sql: str,
    expected_sql: str,
    gen_df: pd.DataFrame,
    expected_df: pd.DataFrame,
    compare_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """LLM-as-judge — scores semantic equivalence on top of compare_frames."""
    from os import getenv

    import httpx

    try:
        from dash.settings import LITE_MODEL
    except Exception:
        LITE_MODEL = "google/gemini-3.1-flash-lite-preview"

    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"score": 0.5, "reasoning": "no OPENROUTER_API_KEY — judge skipped"}

    def _preview(df: pd.DataFrame) -> str:
        if df is None or df.empty:
            return "(empty result)"
        try:
            return df.head(10).to_csv(index=False)
        except Exception:
            return str(df.head(10))

    cmp_blob = json.dumps(compare_result, default=str)[:1500] if compare_result else "{}"
    prompt = f"""You are a SQL evaluation judge. Score how well GENERATED_SQL
matches EXPECTED_SQL by comparing both result-sets.

EXPECTED_SQL:
{expected_sql[:1500]}

GENERATED_SQL:
{gen_sql[:1500]}

EXPECTED_RESULT (csv, up to 10 rows):
{_preview(expected_df)}

GENERATED_RESULT (csv, up to 10 rows):
{_preview(gen_df)}

FRAME_COMPARE: {cmp_blob}

Score 0.0 to 1.0. 1.0 = semantically equivalent (right answer, may differ in
column order or extra projection). 0.5 = partially correct. 0.0 = wrong answer
or empty when it should not be.

Respond with ONLY JSON: {{"score": 0.0..1.0, "reasoning": "brief"}}
"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": LITE_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0,
            },
            timeout=15,
        )
        raw = (resp.json().get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "") or "")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(m.group(0)) if m else {"score": 0.5, "reasoning": "parse failed"}
        score = float(parsed.get("score", 0.5))
        score = max(0.0, min(1.0, score))
        return {"score": score, "reasoning": str(parsed.get("reasoning", ""))[:500]}
    except Exception as e:
        return {"score": 0.5, "reasoning": f"judge error: {e}"}


def _generate_sql_from_prompt(case_row: Dict[str, Any]) -> str:
    """Use the case's hint or LLM to produce a candidate generated_sql.

    Order: prefer pre-supplied `generated_sql_hint` (e.g. for deterministic
    testing) → otherwise LLM. Returns '' if no SQL could be produced.
    """
    hint = (case_row.get("generated_sql_hint") or "").strip()
    if hint:
        return hint

    try:
        from dash.settings import training_llm_call
    except Exception:
        return ""

    dialect = case_row.get("expected_dialect") or "postgres"
    prompt = f"""Write a single SELECT-only {dialect} SQL query that answers
the user question. Output ONLY the SQL (no markdown, no commentary).

QUESTION: {case_row.get('input_prompt', '')}
"""
    try:
        out = training_llm_call(prompt, "extraction")
        return _clean_sql(out or "")
    except Exception as e:
        logger.warning("generated SQL fallback failed: %s", e)
        return ""


def grade_case(case_row: Dict[str, Any], engine) -> Dict[str, Any]:
    """Orchestrator: exec both SQLs, compare frames, LLM-judge, build record."""
    started = time.time()
    expected_sql = (case_row.get("expected_sql") or "").strip()
    if not expected_sql:
        return {
            "case_id": case_row.get("id"),
            "case_name": case_row.get("name"),
            "status": "error",
            "score": 0.0,
            "reason": "case has no expected_sql",
            "latency_ms": int((time.time() - started) * 1000),
        }

    gen_sql = _generate_sql_from_prompt(case_row)
    if not gen_sql:
        return {
            "case_id": case_row.get("id"),
            "case_name": case_row.get("name"),
            "status": "error",
            "score": 0.0,
            "reason": "could not generate candidate SQL",
            "latency_ms": int((time.time() - started) * 1000),
            "expected_sql": expected_sql,
        }

    # Execute expected first (authoritative)
    try:
        expected_df = exec_sql(engine, expected_sql)
    except Exception as e:
        return {
            "case_id": case_row.get("id"),
            "case_name": case_row.get("name"),
            "status": "error",
            "score": 0.0,
            "reason": f"expected_sql failed: {e}",
            "latency_ms": int((time.time() - started) * 1000),
            "generated_sql": gen_sql,
            "expected_sql": expected_sql,
        }

    # Execute generated
    gen_error = None
    try:
        gen_df = exec_sql(engine, gen_sql)
    except Exception as e:
        gen_df = pd.DataFrame()
        gen_error = str(e)

    cmp = compare_frames(gen_df, expected_df)
    judge = judge_sql_pair(gen_sql, expected_sql, gen_df, expected_df, cmp)

    score = float(judge.get("score", 0.0))
    if cmp.get("match"):
        score = max(score, 0.95)
    status = "pass" if score >= 0.7 else "fail"
    if gen_error:
        status = "fail"

    reason_parts = [cmp.get("reason", ""), judge.get("reasoning", "")]
    if gen_error:
        reason_parts.append(f"generated_sql error: {gen_error}")
    reason = " | ".join(p for p in reason_parts if p)

    return {
        "case_id": case_row.get("id"),
        "case_name": case_row.get("name"),
        "status": status,
        "score": round(score, 4),
        "reason": reason[:1000],
        "compare": cmp,
        "judge": judge,
        "generated_sql": gen_sql,
        "expected_sql": expected_sql,
        "gen_rows": int(len(gen_df)),
        "expected_rows": int(len(expected_df)),
        "latency_ms": int((time.time() - started) * 1000),
        "gen_error": gen_error,
    }
