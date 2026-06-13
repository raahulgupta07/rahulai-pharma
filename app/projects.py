"""
Projects API
=============

CRUD for user projects. Each project = an independent data agent
with its own schema, knowledge, and agent persona.
"""

import asyncio
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
import os as _os_bg

# Bounded background pool for per-chat fire-and-forget write-backs (answer-cache,
# metric-shortcut, batched hooks). Bounded = backpressure under a chat burst
# instead of unbounded raw-thread spawning; kept under DIRECT_DB_MAX_CONN so the
# write-backs can't exhaust the direct-connection cap. Tune with DASH_BG_WORKERS.
_bg_executor = ThreadPoolExecutor(
    max_workers=max(2, int(_os_bg.getenv("DASH_BG_WORKERS", "8"))),
    thread_name_prefix="dash-bg",
)

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, inspect, text
from sqlalchemy.pool import NullPool

from db import db_url
from db.session import create_project_schema
from dash.single_agent import is_single_agent, locked_slug, guard_no_project_management

router = APIRouter(prefix="/api/projects", tags=["Projects"])

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import start_trace, trace_span, record_cost, end_trace
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    def start_trace(*_a, **_k):  # type: ignore
        return ""

    def record_cost(*_a, **_k):  # type: ignore
        return None

    def end_trace(*_a, **_k):  # type: ignore
        return None

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None

_engine = _sa_create_engine(db_url, poolclass=NullPool)

_QA_STOP = {"the","a","an","is","are","of","for","by","to","in","on","and","or",
            "what","which","how","many","much","with","across","all","total",
            "number","give","me","provide","show","please","based"}


def _qa_tokens(s: str) -> set:
    # Phase 2 bilingual: also capture Burmese (U+1000–U+109F) runs as tokens so a
    # Burmese question matches its Burmese training twin. Latin tokens keep the
    # >2-char + stopword filter; Burmese word-runs are kept at >=2 chars (no
    # Burmese stopword list — the rare-term IDF weighting downweights particles).
    out = set()
    for w in re.findall(r"[a-z0-9_]+", (s or "").lower()):
        if w not in _QA_STOP and len(w) > 2:
            out.add(w)
    for w in re.findall(r"[က-႟]+", s or ""):
        if len(w) >= 2:
            out.add(w)
    return out


# Parsed-training-pairs cache: {slug: (dir_signature, pairs, df, n)}. Avoids
# re-globbing + JSON-parsing every training file on EVERY chat turn (the parse is
# the only cost here — ranking itself is pure-Python token math). Busted when the
# training dir's file set / mtimes change, so a retrain is picked up automatically.
_TRAINING_PAIRS_CACHE: dict = {}
_training_pairs_lock = threading.Lock()


def _load_training_pairs(project_slug: str):
    """Return (pairs, df, n) for a project's trained Q→SQL files, cached by the
    training dir's (file, mtime) signature. Empty (([], {}, 0)) when none."""
    from dash.paths import KNOWLEDGE_DIR
    import json as _json
    tdir = KNOWLEDGE_DIR / project_slug / "training"
    if not tdir.exists():
        return [], {}, 0
    try:
        files = sorted(tdir.glob("*.json"))
        sig = tuple((f.name, f.stat().st_mtime_ns) for f in files)
    except Exception:
        files, sig = [], ()
    with _training_pairs_lock:
        hit = _TRAINING_PAIRS_CACHE.get(project_slug)
        if hit and hit[0] == sig:
            return hit[1], hit[2], hit[3]
    pairs = []
    for f in files:
        try:
            with open(f) as _fp:
                data = _json.load(_fp)
        except Exception:
            continue
        if isinstance(data, list):
            for qa in data:
                q, sql = qa.get("question", ""), qa.get("sql", "")
                if q and sql:
                    pairs.append((q, sql, qa.get("answer_template", "")))
    df: dict = {}
    for q, _s, _a in pairs:
        for t in _qa_tokens(q):
            df[t] = df.get(t, 0) + 1
    n = len(pairs)
    with _training_pairs_lock:
        _TRAINING_PAIRS_CACHE[project_slug] = (sig, pairs, df, n)
    return pairs, df, n


def _rank_training_qa(project_slug: str, question: str, k: int = 3) -> str:
    """Rank this project's trained Q→SQL pairs by relevance to `question` and
    return a compact 'RELEVANT PROVEN QUERIES' block of the top-k matches.

    Lexical relevance (rare-term-weighted token overlap) — $0, no latency, no
    embedding call. Lets a short prompt retrieve the proven SQL for its metric
    even though session-level instructions can't see the live question.
    """
    import math
    pairs, df, n = _load_training_pairs(project_slug)
    if not pairs:
        return ""
    qtok = _qa_tokens(question)
    if not qtok:
        return ""
    scored = []
    for q, sql, ans in pairs:
        pt = _qa_tokens(q)
        overlap = qtok & pt
        if not overlap:
            continue
        score = sum(math.log((n + 1) / (df.get(t, 1))) for t in overlap)
        scored.append((score, q, sql, ans))
    if not scored:
        return ""
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:k]
    lines = ["## RELEVANT PROVEN QUERIES (matched to your question — reuse this SQL, re-execute for fresh numbers)"]
    for _sc, q, sql, ans in top:
        lines.append(f"- Q: {q}\n  SQL: {sql[:600]}" + (f"\n  Note: {ans[:200]}" if ans else ""))
    return "\n".join(lines)


def _dedupe_greeting(text: str) -> str:
    """Safety net for the duplicated-persona-greeting bug.

    1. Collapses consecutive duplicate sentences/paragraphs (a ~40+ char
       segment repeated back-to-back keeps only one copy).
    2. Strips a leading persona-greeting block ("I'm …assistant… ?") when real
       analysis follows it. If the WHOLE message is just a greeting, leaves it.

    Fail-soft: returns the original text on any error.
    """
    if not text or not isinstance(text, str):
        return text
    try:
        original = text
        # ── 1. Collapse consecutive duplicate segments (split on sentence/para
        #       boundaries; only dedupe long-ish segments to stay conservative).
        segments = re.split(r"(?<=[.?!])\s+|\n+", text)
        out: list[str] = []
        prev_norm = None
        for seg in segments:
            norm = seg.strip().lower()
            if norm and len(norm) >= 40 and norm == prev_norm:
                continue  # skip back-to-back duplicate
            out.append(seg)
            if norm:
                prev_norm = norm
        text = " ".join(s for s in out if s).strip() or original

        # ── 2. Strip a leading persona-greeting block if real content follows.
        #       Greeting heuristic: starts with "I'm "/"I am " + mentions
        #       "assistant" before its first "?".
        m = re.match(r"\s*(I'm|I am)\b.{0,400}?\?", text, re.IGNORECASE | re.DOTALL)
        if m:
            head = m.group(0)
            tail = text[m.end():].strip()
            # Only strip when the head looks like a greeting AND substantive
            # content remains after it.
            if "assistant" in head.lower() and len(tail) >= 40:
                text = tail

        return text or original
    except Exception:
        return text


# ── SSE reasoning-trace normalization ──────────────────────────────────────
# Maps raw Agno stream events into a stable SSE contract the frontend renders
# as an OpenAI-style reasoning trace. Tool events get a normalized `tool`
# object {id, name, args, result}; ReasoningTools think/analyze/reason calls
# (and any reasoning content) become `ReasoningStep` events. All extraction is
# fail-soft — never break the stream.
_REASONING_TOOL_NAMES = {"think", "analyze", "reason", "reasoning"}


def _short_str(val, limit: int = 300) -> str:
    """Stringify a tool result (dict/list/scalar) and truncate."""
    try:
        if val is None:
            return ""
        if isinstance(val, (dict, list)):
            import json as _j
            s = _j.dumps(val, default=str)
        else:
            s = str(val)
        return s if len(s) <= limit else s[:limit] + "…"
    except Exception:
        return ""


def _normalize_tool(data: dict) -> dict:
    """Return a normalized tool dict {id, name, args, result} merged onto the
    existing Agno tool object so legacy `tool_name`/`tool_args` keep working."""
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    try:
        name = tool.get("tool_name") or tool.get("name") or ""
        args = tool.get("tool_args")
        if args is None:
            args = tool.get("args")
        if args is None:
            args = {}
        tool_id = (
            tool.get("tool_call_id")
            or tool.get("id")
            or f"{name}:{id(tool)}"
        )
        merged = dict(tool)
        merged["id"] = tool_id
        merged["name"] = name
        merged["args"] = args
        # result only present on completed events
        if "result" in tool or "content" in tool:
            merged["result"] = _short_str(tool.get("result", tool.get("content")))
        return merged
    except Exception:
        return tool or {}


_SQL_TOOL_NAMES = {"run_sql_query", "run_sql", "execute_sql", "query"}
_SQL_ARG_KEYS = ("query", "sql", "statement", "sql_query", "q")


def _attach_sql_cost(tool: dict, engine) -> None:
    """Best-effort: attach `cost` (rounded total_cost int) + `est_rows` to a
    normalized tool object if it's a SQL tool carrying SQL text. Fail-soft —
    on any error (no engine, EXPLAIN fails, not-ok estimate), leave the tool
    untouched so the stream is never broken. Call only on ToolCallStarted."""
    try:
        if not isinstance(tool, dict):
            return
        name = (tool.get("name") or tool.get("tool_name") or "").lower()
        if name not in _SQL_TOOL_NAMES:
            return
        args = tool.get("args")
        if not isinstance(args, dict):
            args = tool.get("tool_args") if isinstance(tool.get("tool_args"), dict) else {}
        sql = None
        for k in _SQL_ARG_KEYS:
            v = args.get(k) if isinstance(args, dict) else None
            if v and isinstance(v, str):
                sql = v
                break
        if not sql:
            return
        if engine is None:
            from dash.tools.skill_refinery import _get_engine
            engine = _get_engine()
        from dash.tools.sql_cost_guard import estimate_query_cost
        est = estimate_query_cost(engine, sql)
        if not isinstance(est, dict) or not est.get("ok"):
            return
        tc = est.get("total_cost")
        if tc is None:
            return
        tool["cost"] = int(round(float(tc)))
        tool["est_rows"] = int(est.get("est_rows") or 0)
    except Exception:
        pass


def _reasoning_step_from_tool(data: dict, agent_name: str):
    """If this tool event is a ReasoningTools think/analyze/reason call, return
    a ReasoningStep payload dict; else None."""
    try:
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        name = (tool.get("tool_name") or tool.get("name") or "").lower()
        if name not in _REASONING_TOOL_NAMES:
            return None
        args = tool.get("tool_args") or tool.get("args") or {}
        if isinstance(args, dict):
            title = args.get("title") or args.get("topic") or name
            content = (
                args.get("thought")
                or args.get("reasoning")
                or args.get("content")
                or args.get("analysis")
                or ""
            )
        else:
            title, content = name, _short_str(args, 1000)
        return {"title": str(title)[:120], "content": str(content), "agent_name": agent_name or ""}
    except Exception:
        return None


def _reasoning_step_from_content(data: dict, agent_name: str):
    """Extract a ReasoningStep from a reasoning content event (ReasoningStep /
    ReasoningStepCompleted / reasoning_content on RunContent). Returns dict or None."""
    try:
        title = data.get("title") or data.get("reasoning_title") or "Reasoning"
        content = (
            data.get("reasoning_content")
            or data.get("content")
            or data.get("reasoning")
            or ""
        )
        if not content:
            return None
        return {"title": str(title)[:120], "content": str(content), "agent_name": agent_name or ""}
    except Exception:
        return None


def _usage_from_event(data: dict, agent_name: str = "") -> dict | None:
    """Best-effort extraction of token usage + model from an Agno event dict
    (post `to_dict()`). Returns {input_tokens, output_tokens, model, agent_name}
    when usage metrics are present, else None. Fail-soft — never raises.

    Agno exposes usage via a `metrics` dict (RunMetrics.to_dict) carrying
    input_tokens / output_tokens / total_tokens on *completed* events
    (RunCompleted / TeamRunCompleted / member-completed). Model id lives on
    `model` (fallback `model_id`), provider on `model_provider`. Some shapes
    nest under `usage`."""
    try:
        m = data.get("metrics")
        if not isinstance(m, dict):
            m = data.get("usage") if isinstance(data.get("usage"), dict) else None
        if not isinstance(m, dict):
            return None
        in_tok = m.get("input_tokens")
        out_tok = m.get("output_tokens")
        tot_tok = m.get("total_tokens")
        # Require at least one real token count to bother emitting.
        if not any(isinstance(x, (int, float)) and x for x in (in_tok, out_tok, tot_tok)):
            return None
        model = (
            data.get("model")
            or data.get("model_id")
            or (data.get("model_provider") and str(data.get("model_provider")))
            or ""
        )
        payload = {
            "input_tokens": int(in_tok or 0),
            "output_tokens": int(out_tok or 0),
            "model": str(model or ""),
            "agent_name": agent_name or data.get("agent_name") or data.get("member_id") or "",
        }
        if isinstance(tot_tok, (int, float)) and tot_tok:
            payload["total_tokens"] = int(tot_tok)
        return payload
    except Exception:
        return None


def _attach_model_to_tool(tool: dict, data: dict) -> None:
    """Attach `model` (and `duration` if present) from the surrounding event onto
    the normalized tool object so the trace can show model per step. Fail-soft —
    never overwrites existing keys with empties, never raises."""
    try:
        if not isinstance(tool, dict):
            return
        model = data.get("model") or data.get("model_id") or ""
        if model and not tool.get("model"):
            tool["model"] = str(model)
        dur = (
            data.get("duration")
            or (data.get("tool") or {}).get("duration") if isinstance(data.get("tool"), dict) else None
        )
        if isinstance(dur, (int, float)) and dur and not tool.get("duration"):
            tool["duration"] = dur
    except Exception:
        pass


def _trace_from_stored_run(parent_run: dict, children: list[dict]) -> list[dict]:
    """Reconstruct a persisted reasoning `trace` for one assistant message from
    its stored Agno run (parent run + its member/child runs). Mirrors the SAME
    shape the live SSE stream emits via `_normalize_tool` /
    `_reasoning_step_from_tool` / `_reasoning_step_from_content`, so the trace
    restored after a page refresh matches what the user saw live.

    Items are emitted in chronological order:
      - reasoning step (think/analyze/reason tool, or reasoning_content) →
        {"kind":"step","id","title","text","agent_name"}
      - tool call → {"kind":"tool","id","name","args","result","status":"done",
        "cost"?,"model"?,"duration"?,"agent_name"}

    Fail-soft: any parse error on a single item is skipped; a fully unparseable
    run yields []. Never raises.
    """
    trace: list[dict] = []
    try:
        # Order: parent run first, then its child/member runs (each carrying its
        # own tools + reasoning). Within a run, reasoning content precedes tools.
        ordered: list[tuple[dict, str]] = []
        p_agent = parent_run.get("agent_name") or parent_run.get("agent_id") or parent_run.get("member_id") or ""
        ordered.append((parent_run, p_agent))
        for ch in (children or []):
            if not isinstance(ch, dict):
                continue
            c_agent = ch.get("agent_name") or ch.get("agent_id") or ch.get("member_id") or ""
            ordered.append((ch, c_agent))

        for run, agent_name in ordered:
            if not isinstance(run, dict):
                continue

            # 1) Reasoning content carried directly on the run (reasoning_content).
            try:
                rstep = _reasoning_step_from_content(run, agent_name)
                if rstep:
                    trace.append({
                        "kind": "step",
                        "id": f"step:{run.get('run_id') or len(trace)}",
                        "title": rstep.get("title") or "Reasoning",
                        "text": rstep.get("content") or "",
                        "agent_name": rstep.get("agent_name") or agent_name or "",
                    })
            except Exception:
                pass

            # 2) Tools — reasoning tools become steps, everything else a tool item.
            for t in (run.get("tools") or []):
                if not isinstance(t, dict):
                    continue
                try:
                    data = {
                        "tool": t,
                        "agent_name": agent_name,
                        "model": run.get("model") or run.get("model_id") or "",
                    }
                    rstep = _reasoning_step_from_tool(data, agent_name)
                    if rstep:
                        trace.append({
                            "kind": "step",
                            "id": f"step:{t.get('tool_call_id') or t.get('tool_name') or len(trace)}",
                            "title": rstep.get("title") or "",
                            "text": rstep.get("content") or "",
                            "agent_name": rstep.get("agent_name") or agent_name or "",
                        })
                        continue

                    tool = _normalize_tool(data)
                    _attach_model_to_tool(tool, data)
                    metrics = t.get("metrics") if isinstance(t.get("metrics"), dict) else {}
                    item = {
                        "kind": "tool",
                        "id": tool.get("id") or tool.get("name") or f"tool:{len(trace)}",
                        "name": tool.get("name") or t.get("tool_name") or "tool",
                        "args": tool.get("args") or t.get("tool_args") or {},
                        "result": tool.get("result", _short_str(t.get("result", t.get("content")), 1200)),
                        "status": "error" if t.get("tool_call_error") else "done",
                        "agent_name": agent_name or "",
                    }
                    model = tool.get("model") or run.get("model") or run.get("model_id")
                    if model:
                        item["model"] = str(model)
                    cost = tool.get("cost")
                    if cost is None and isinstance(metrics, dict):
                        cost = metrics.get("cost") or metrics.get("total_cost")
                    if cost is not None:
                        try:
                            item["cost"] = int(round(float(cost)))
                        except Exception:
                            pass
                    dur = tool.get("duration")
                    if dur is None and isinstance(metrics, dict):
                        dur = metrics.get("duration")
                    if isinstance(dur, (int, float)) and dur:
                        item["duration"] = float(dur)
                    trace.append(item)
                except Exception:
                    continue
    except Exception:
        return []
    return trace


def _usage_from_stored_run(parent_run: dict, children: list[dict]) -> dict | None:
    """Per-assistant-message token usage from the stored run's metrics. Sums
    parent + member runs. Returns {input_tokens, output_tokens, model} | None.
    Fail-soft — never raises."""
    try:
        in_tok = 0
        out_tok = 0
        model = ""
        found = False
        for run in [parent_run, *(children or [])]:
            if not isinstance(run, dict):
                continue
            m = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
            it = m.get("input_tokens")
            ot = m.get("output_tokens")
            if isinstance(it, (int, float)) and it:
                in_tok += int(it)
                found = True
            if isinstance(ot, (int, float)) and ot:
                out_tok += int(ot)
                found = True
            if not model:
                model = run.get("model") or run.get("model_id") or ""
        if not found:
            return None
        return {"input_tokens": int(in_tok), "output_tokens": int(out_tok), "model": str(model or "")}
    except Exception:
        return None


@router.get("/{slug}/sessions")
def project_sessions(slug: str, request: Request, limit: int = 40):
    """List the user's recent chat sessions for this project (robot CHAT tab).
    Lightweight: id + first message + timestamps; turns load lazily per session
    via the /messages route. Fail-soft → {sessions: []} on any error."""
    user = _get_user(request)
    get_project(slug, request)
    out: list[dict] = []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT session_id, first_message, "
                "       EXTRACT(EPOCH FROM created_at)::bigint, "
                "       EXTRACT(EPOCH FROM updated_at)::bigint "
                "FROM public.dash_chat_sessions "
                "WHERE user_id = :uid "
                "ORDER BY updated_at DESC NULLS LAST LIMIT :lim"
            ), {"uid": user["user_id"], "lim": max(1, min(limit, 100))}).fetchall()
            for r in rows:
                out.append({
                    "session_id": r[0],
                    "first_message": (r[1] or "(untitled chat)")[:160],
                    "created": int(r[2]) if r[2] is not None else None,
                    "updated": int(r[3]) if r[3] is not None else None,
                })
    except Exception:
        pass
    return {"sessions": out}


@router.get("/{slug}/sessions/{session_id}/messages")
def project_session_messages(slug: str, session_id: str, request: Request):
    """Load a stored chat session's messages WITH a reconstructed reasoning
    `trace` + token `usage` per assistant message, so the frontend can restore
    the OpenAI-style trace after a page refresh (the live stream's trace is
    otherwise lost). Trace shape matches the live SSE contract exactly.

    Fail-soft: a run that can't be parsed yields `trace: []`, never breaks load.
    """
    user = _get_user(request)
    get_project(slug, request)
    messages: list[dict] = []
    try:
        with _engine.connect() as conn:
            owned = conn.execute(text(
                "SELECT 1 FROM public.dash_chat_sessions WHERE session_id = :sid AND user_id = :uid"
            ), {"sid": session_id, "uid": user["user_id"]}).fetchone()
            if not owned:
                return {"messages": []}

            row = conn.execute(text(
                "SELECT runs FROM ai.agno_sessions WHERE session_id = :sid"
            ), {"sid": session_id}).fetchone()
            runs = (row[0] if row and isinstance(row[0], list) else []) or []

            children_by_parent: dict[str, list] = {}
            for r in runs:
                if not isinstance(r, dict):
                    continue
                pid = r.get("parent_run_id")
                if pid:
                    children_by_parent.setdefault(str(pid), []).append(r)

            try:
                q_rows = conn.execute(text(
                    "SELECT chat_id, score FROM public.dash_quality_scores WHERE session_id = :sid"
                ), {"sid": session_id}).fetchall()
                quality_list = [qr[1] for qr in q_rows]
            except Exception:
                quality_list = []
            q_idx = 0

            for run in runs:
                if not isinstance(run, dict) or run.get("parent_run_id"):
                    continue

                inp = run.get("input") or {}
                user_msg = ""
                if isinstance(inp, dict):
                    user_msg = inp.get("input_content", "") or inp.get("content", "")
                elif isinstance(inp, str):
                    user_msg = inp
                if user_msg:
                    messages.append({"role": "user", "content": user_msg})

                content = run.get("content", "")
                if not content:
                    continue

                run_id = str(run.get("run_id") or "")
                children = children_by_parent.get(run_id, [])

                # Legacy tool_calls (kept for back-compat with existing UI bits).
                primary_agent = None
                tool_calls: list[dict] = []
                for ch in children:
                    agent_nm = ch.get("agent_name") or ch.get("agent_id") or ""
                    if agent_nm and not primary_agent and agent_nm.lower() not in ("leader", "team"):
                        primary_agent = agent_nm
                    for t in (ch.get("tools") or []):
                        m = t.get("metrics") or {}
                        tool_calls.append({
                            "name": t.get("tool_name") or "tool",
                            "args": t.get("tool_args") or {},
                            "result": str(t.get("result") or "")[:200],
                            "duration": float(m.get("duration") or 0),
                            "status": "error" if t.get("tool_call_error") else "done",
                        })
                for t in (run.get("tools") or []):
                    m = t.get("metrics") or {}
                    tool_calls.append({
                        "name": t.get("tool_name") or "tool",
                        "args": t.get("tool_args") or {},
                        "result": str(t.get("result") or "")[:200],
                        "duration": float(m.get("duration") or 0),
                        "status": "error" if t.get("tool_call_error") else "done",
                    })

                q_score = None
                if q_idx < len(quality_list):
                    try:
                        q_score = int(quality_list[q_idx])
                    except Exception:
                        q_score = None
                    q_idx += 1

                trace = _trace_from_stored_run(run, children)
                usage = _usage_from_stored_run(run, children)
                run_metrics = run.get("metrics") or {}

                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                    "trace": trace,
                    "usage": usage,
                    "duration": float(run_metrics.get("duration") or 0),
                    "quality_score": q_score,
                    "routing": {"routed_to": primary_agent} if primary_agent else None,
                })
    except Exception:
        return {"messages": messages}

    return {"messages": messages}


@router.get("/{slug}/sessions/{session_id}/verified")
def project_session_verified(slug: str, session_id: str, request: Request):
    """Latest verified-reward result for a session (HARD truth check of the last
    answer vs proven SQL / pinned number). Drives the SOURCES-tab badge."""
    _get_user(request)
    get_project(slug, request)
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT verified, expected, got, source_q FROM public.dash_verified_scores "
                "WHERE project_slug = :s AND session_id = :sid ORDER BY created_at DESC LIMIT 1"
            ), {"s": slug, "sid": session_id}).fetchone()
        if not row:
            return {"verified": "unknown"}
        return {"verified": row[0], "expected": row[1], "got": row[2], "source_q": row[3]}
    except Exception:
        return {"verified": "unknown"}


@router.get("/{slug}/accuracy")
def project_accuracy(slug: str, request: Request, days: int = 30):
    """Verified-correctness rate over the window — a REAL accuracy number
    (answers that matched ground truth), not the LLM judge's opinion."""
    _get_user(request)
    get_project(slug, request)
    try:
        with _engine.connect() as conn:
            r = conn.execute(text(
                "SELECT "
                " SUM(CASE WHEN verified='pass' THEN 1 ELSE 0 END) AS passed, "
                " SUM(CASE WHEN verified='fail' THEN 1 ELSE 0 END) AS failed, "
                " SUM(CASE WHEN verified='unknown' THEN 1 ELSE 0 END) AS unknown "
                "FROM public.dash_verified_scores "
                "WHERE project_slug = :s AND created_at > NOW() - (:d || ' days')::interval"
            ), {"s": slug, "d": days}).fetchone()
        passed = int(r[0] or 0); failed = int(r[1] or 0); unknown = int(r[2] or 0)
        checked = passed + failed
        return {"passed": passed, "failed": failed, "unknown": unknown,
                "checked": checked, "pct": round(100.0 * passed / checked, 1) if checked else None}
    except Exception:
        return {"passed": 0, "failed": 0, "unknown": 0, "checked": 0, "pct": None}


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _make_slug(username: str, name: str) -> str:
    """Generate a project slug: proj_{user}_{name}. Capped at 35 chars total to avoid PG 63-char index limit."""
    # Single-tenant lock: never mint a new tenant slug — always the locked one.
    if is_single_agent():
        return locked_slug()
    safe_user = re.sub(r"[^a-z0-9]", "_", username.lower().strip())[:10]
    safe_name = re.sub(r"[^a-z0-9]", "_", name.lower().strip())
    safe_name = re.sub(r"_+", "_", safe_name).strip("_")[:20]
    return f"proj_{safe_user}_{safe_name}"


@router.get("")
def list_projects(request: Request):
    """List current user's projects with stats."""
    user = _get_user(request)

    with _engine.connect() as conn:
        if is_single_agent():
            # Single-agent product: only ever surface the one locked project,
            # regardless of which user owns the seeded row.
            rows = conn.execute(text(
                "SELECT id, slug, name, agent_name, agent_role, agent_personality, schema_name, created_at, updated_at, COALESCE(is_favorite, FALSE) "
                "FROM public.dash_projects WHERE slug = :slug ORDER BY updated_at DESC"
            ), {"slug": locked_slug()}).fetchall()
        else:
            rows = conn.execute(text(
                "SELECT id, slug, name, agent_name, agent_role, agent_personality, schema_name, created_at, updated_at, COALESCE(is_favorite, FALSE) "
                "FROM public.dash_projects WHERE user_id = :uid ORDER BY updated_at DESC"
            ), {"uid": user["user_id"]}).fetchall()

    # Batch query: get table counts per schema in one query
    schema_stats: dict[str, dict] = {}
    schemas = [r[6] for r in rows if r[6]]
    if schemas:
        try:
            with _engine.connect() as conn:
                for schema in schemas:
                    try:
                        result = conn.execute(text(
                            "SELECT COUNT(*) as tbl_count, "
                            "COALESCE((SELECT SUM(n_live_tup) FROM pg_stat_user_tables WHERE schemaname = :s), 0) as row_count "
                            "FROM information_schema.tables WHERE table_schema = :s AND table_type = 'BASE TABLE'"
                        ), {"s": schema}).fetchone()
                        if result:
                            schema_stats[schema] = {"tables": result[0], "rows": int(result[1])}
                    except Exception:
                        schema_stats[schema] = {"tables": 0, "rows": 0}
        except Exception:
            pass

    # Batch query: get last trained timestamp per project
    slugs = [r[1] for r in rows]
    last_trained_map: dict[str, str] = {}
    if slugs:
        try:
            with _engine.connect() as conn:
                trained_rows = conn.execute(text(
                    "SELECT DISTINCT ON (project_slug) project_slug, finished_at "
                    "FROM public.dash_training_runs WHERE status = 'done' AND finished_at IS NOT NULL "
                    "ORDER BY project_slug, finished_at DESC"
                )).fetchall()
                for tr in trained_rows:
                    last_trained_map[tr[0]] = str(tr[1])
        except Exception:
            pass

    projects = []
    for r in rows:
        schema = r[6]
        stats = schema_stats.get(schema, {"tables": 0, "rows": 0})
        projects.append({
            "id": r[0], "slug": r[1], "name": r[2],
            "agent_name": r[3], "agent_role": r[4], "agent_personality": r[5],
            "schema_name": schema,
            "tables": stats["tables"], "rows": stats["rows"],
            "is_favorite": r[9] if len(r) > 9 else False,
            "created_at": str(r[7]) if r[7] else None,
            "updated_at": str(r[8]) if r[8] else None,
            "last_trained": last_trained_map.get(r[1]),
        })

    return {"projects": projects}


@router.post("")
def create_project(request: Request, name: str, agent_name: str, agent_role: str = "", agent_personality: str = "friendly"):
    """Create a new project."""
    guard_no_project_management("create projects")
    user = _get_user(request)

    if not name or len(name) < 2:
        raise HTTPException(400, "Name must be at least 2 characters")
    if not agent_name or len(agent_name) < 2:
        raise HTTPException(400, "Agent name must be at least 2 characters")

    slug = _make_slug(user["username"], name)
    schema_name = slug

    # Atomic insert with conflict check to prevent race conditions
    with _engine.connect() as conn:
        # Create schema
        create_project_schema(slug)

        # Insert project (ON CONFLICT handles race condition)
        result = conn.execute(text(
            "INSERT INTO public.dash_projects (user_id, slug, name, agent_name, agent_role, agent_personality, schema_name) "
            "VALUES (:uid, :slug, :name, :an, :ar, :ap, :sn) "
            "ON CONFLICT (slug) DO NOTHING RETURNING id"
        ), {"uid": user["user_id"], "slug": slug, "name": name, "an": agent_name, "ar": agent_role, "ap": agent_personality, "sn": schema_name})
        if not result.fetchone():
            raise HTTPException(409, f"Project '{name}' already exists")
        conn.commit()

    from app.auth import log_action
    log_action(user, "create_project", "project", slug, f"name={name}, agent={agent_name}")

    # Auto-bootstrap marketplace skills (non-blocking, fire-and-forget)
    def _bootstrap_marketplace():
        try:
            from dash.learning.skill_marketplace import (
                auto_bootstrap_new_project,
                _derive_template_for_project,
            )
            with _engine.connect() as conn:
                template_name = _derive_template_for_project(slug, conn)
            n = auto_bootstrap_new_project(slug, template_name)
            if n:
                import logging
                logging.getLogger(__name__).info(
                    "create_project: auto-bootstrapped %d marketplace skills for slug=%s template=%s",
                    n, slug, template_name,
                )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "create_project: marketplace bootstrap failed for slug=%s (non-fatal)", slug
            )

    try:
        _bg_executor.submit(_bootstrap_marketplace)
    except Exception:
        pass

    return {"status": "ok", "slug": slug, "schema_name": schema_name}


def ensure_locked_project() -> None:
    """Single-tenant: guarantee the locked project row + schema exist on boot.

    On a FRESH install (no demo seed) nothing ever created the locked `citypharma`
    project — the only INSERT path is create_project(), which guard_no_project_management
    blocks in single-agent mode. With no row in public.dash_projects, EVERY access
    check fails before the super-admin branch (check_project_permission returns None
    when the project row is missing) → GET /projects/{slug} 404, datasource 403 →
    Workspace stuck "loading…", Upload/Force-Train hidden even for the super-admin.

    This seeds the row (owner = the SUPER_ADMIN env account) + its schema, idempotently,
    so a clean AWS deploy lands a working, empty, ready-to-upload project. No-op if the
    row already exists or if not in single-agent mode.
    """
    import os
    import logging
    log = logging.getLogger(__name__)
    try:
        if not is_single_agent():
            return
        slug = locked_slug()
        with _engine.connect() as conn:
            exists = conn.execute(text(
                "SELECT 1 FROM public.dash_projects WHERE slug = :s"
            ), {"s": slug}).fetchone()
            if exists:
                return
            # Owner = the env super-admin (self-healed to role='super' by auth init);
            # fall back to the lowest user id so the row is always ownable.
            admin_user = os.getenv("SUPER_ADMIN", "admin")
            owner = conn.execute(text(
                "SELECT id FROM public.dash_users WHERE username = :u"
            ), {"u": admin_user}).fetchone()
            if not owner:
                owner = conn.execute(text(
                    "SELECT id FROM public.dash_users ORDER BY id ASC LIMIT 1"
                )).fetchone()
            owner_id = owner[0] if owner else 1

        try:
            from dash.single_agent import product_name
            pname = product_name()
        except Exception:
            pname = "CityAgent Pharma"

        # Schema first (own txn inside helper), then the row.
        create_project_schema(slug)
        with _engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_projects "
                "(user_id, slug, name, agent_name, agent_role, agent_personality, schema_name) "
                "VALUES (:uid, :slug, :name, :an, :ar, :ap, :sn) "
                "ON CONFLICT (slug) DO NOTHING"
            ), {"uid": owner_id, "slug": slug, "name": pname,
                "an": pname, "ar": "Pharma data analyst",
                "ap": "professional", "sn": slug})
            conn.commit()
        log.info("ensure_locked_project: seeded locked project '%s' (owner uid=%s)", slug, owner_id)
    except Exception:
        log.exception("ensure_locked_project: failed (non-fatal)")


@router.post("/{slug}/duplicate")
def duplicate_project(slug: str, request: Request):
    """Duplicate a project — copies config + persona. Does NOT copy data (user retrains)."""
    guard_no_project_management("duplicate projects")
    user = _get_user(request)
    src = get_project(slug, request)

    base_name = f"{src['name']} (copy)"
    new_name = base_name
    new_slug = _make_slug(user["username"], new_name)

    # Bump suffix if slug collides
    n = 2
    with _engine.connect() as conn:
        while conn.execute(text("SELECT 1 FROM public.dash_projects WHERE slug=:s"), {"s": new_slug}).fetchone():
            new_name = f"{src['name']} (copy {n})"
            new_slug = _make_slug(user["username"], new_name)
            n += 1
            if n > 50:
                raise HTTPException(409, "Too many copies of this project")

    create_project_schema(new_slug)
    with _engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO public.dash_projects (user_id, slug, name, agent_name, agent_role, agent_personality, schema_name) "
            "VALUES (:uid, :slug, :name, :an, :ar, :ap, :sn)"
        ), {
            "uid": user["user_id"], "slug": new_slug, "name": new_name,
            "an": src["agent_name"], "ar": src.get("agent_role", ""),
            "ap": src.get("agent_personality", "friendly"), "sn": new_slug,
        })

    from app.auth import log_action
    log_action(user, "duplicate_project", "project", new_slug, f"src={slug}")
    return {"status": "ok", "slug": new_slug, "name": new_name}


@router.post("/{slug}/chat")
async def project_chat(slug: str, request: Request):
    """Chat with a project's agent using AgentOS endpoint for full SSE events."""
    from fastapi.responses import StreamingResponse
    import json as _json

    user = _get_user(request)
    proj = get_project(slug, request)

    form = await request.form()
    message = form.get("message", "")
    stream = str(form.get("stream", "true")).lower() == "true"
    session_id = form.get("session_id")
    reasoning = form.get("reasoning", "auto")  # "auto" | "fast" | "deep"
    analysis_type = form.get("analysis_type", "auto")  # "auto" | "descriptive" | etc.
    # Composer model picker (Claude-Code style): model_pref overrides the router's
    # auto choice; effort maps to the reasoning/thinking mode.
    model_pref = (form.get("model_pref") or "auto").strip().lower()  # auto|lite|mid|deep
    effort = (form.get("effort") or "").strip().lower()              # low|medium|high|max
    if effort:
        reasoning = {"low": "fast", "medium": "auto", "high": "deep", "max": "deep"}.get(effort, reasoning)

    # Set EXEC_TIER ContextVar for tier-aware exec layout directives.
    # Picks up explicit override from frontend slash commands (quick/deep).
    # Router complexity unknown at this point; only explicit override matters here.
    try:
        from dash.instructions import EXEC_TIER
        from app.main import _tier_label
        _tier_value = _tier_label(None, reasoning)
        EXEC_TIER.set(_tier_value)
    except Exception:
        pass

    # Expose session_id + project_slug to tool hooks for auto-link writes
    # (chat → cites → table, chat → uses → skill). Fail-soft.
    try:
        from dash.links_ctx import CUR_SESSION_ID, CUR_PROJECT_SLUG
        if session_id:
            CUR_SESSION_ID.set(session_id)
        CUR_PROJECT_SLUG.set(slug)
    except Exception:
        pass

    # Reset tool-call guardrail state at chat-turn start so per-turn retry
    # counters don't leak across requests. Fail-soft.
    try:
        from dash.runtime.tool_guardrail import reset_for_session
        reset_for_session(session_id or slug or "global")
    except Exception:
        pass

    if not message:
        raise HTTPException(400, "Message required")

    if len(message) > 50000:
        raise HTTPException(413, "Message too long (max 50000 chars)")

    # ── Unintelligible-input guard ───────────────────────────────────────
    # Fire on stray single letters/symbols ("v", ".", "??", "x") so we don't
    # spin up the whole agent on noise. Conservative by design — must NOT block
    # legitimate short messages: Burmese text (unicode), "hi"/"ok"/"fever", or
    # any message with >=2 alpha chars passes. Returns the SAME shape as the
    # TRIVIAL short-circuit (stream → RouterDecision-less SSE; non-stream → dict).
    try:
        _ng = (message or "").strip()
        # Known short greeting/ack tokens that are legit even at <=2 chars.
        _KNOWN_SHORT = {
            "hi", "hii", "ok", "yo", "no", "ty", "k", "kk", "hey", "yes",
            "yep", "sup", "thx", "bye", "lol", "haha", "cya", "nope",
        }
        _ng_lc = _ng.lower()
        # Count letters across all scripts (Burmese, Latin, etc.) — \W is too
        # blunt, so use str.isalpha() which is unicode-aware.
        _alpha = [c for c in _ng if c.isalpha()]
        _alnum = [c for c in _ng if c.isalnum()]
        _is_nonsense = False
        if _ng:
            if len(_alpha) == 0:
                # No letter at all → ".", "??", "123" alone, pure symbols.
                _is_nonsense = True
            elif len(_alnum) < 2:
                # After stripping non-alphanumerics, <2 chars remain → "v", "x.".
                _is_nonsense = True
            else:
                # Single short token (<=2 chars) that isn't a known word/greeting.
                _toks = _ng.split()
                if (len(_toks) == 1 and len(_ng) <= 2
                        and _ng_lc not in _KNOWN_SHORT):
                    _is_nonsense = True
        if _is_nonsense:
            _clar = (
                "I didn't quite catch that — could you rephrase? I can help with "
                "stock levels, drug info, substitutes, valuations, or categories."
            )
            if stream:
                async def _clarify_stream():
                    yield f"event: TeamRunContent\ndata: {_json.dumps({'content': _clar})}\n\n"
                    yield "event: TeamRunCompleted\ndata: {}\n\n"
                return StreamingResponse(_clarify_stream(), media_type="text/event-stream")
            return {"content": _clar, "session_id": session_id, "clarify": True}
    except HTTPException:
        raise
    except Exception:
        # Never let the guard block a real chat — fail open to the agent.
        pass

    # Begin root trace for this chat message.
    start_trace("chat", project_slug=slug, name="chat")
    # Tag the run for the Usage dashboard: who ran it (actor), how it came in
    # (channel: api for service keys, web otherwise), and which store (api keys).
    try:
        from dash.obs.trace import set_root_meta as _srm
        _via_api = bool((user or {}).get("via_api_key"))
        _srm(
            actor=(user or {}).get("username"),
            channel=("api" if _via_api else "web"),
            store_id=((user or {}).get("store_id") or (user or {}).get("site_code")) if _via_api else None,
        )
    except Exception:
        pass
    # Prometheus counter — record arrival before any error gating.
    try:
        from dash.utils.metrics import inc_chat as _inc_chat
        _inc_chat(slug, "started")
    except Exception:
        pass

    # Expand @customer:ID mentions with cached 360 context (RFM, churn, CLV, …)
    try:
        from dash.instructions import expand_customer_mentions
        message = expand_customer_mentions(slug, message)
    except Exception:
        pass

    # Continuous query learning (P1): expose the user's question to the
    # run_sql_query capture hook so any SQL the agent writes this turn is paired
    # with the question. Best-effort; the clean user message (no scaffolding).
    try:
        from dash.links_ctx import CUR_QUESTION as _CUR_Q
        _CUR_Q.set((message or "").strip())
    except Exception:
        pass

    # Phase 1 bilingual: set the reply-language contract from the user's script
    # BEFORE the team is built/cached. Burmese unicode block (U+1000–U+109F) ⇒ 'my'.
    # build_analyst_instructions appends the Burmese override and dash.team caches
    # the MY team separately. Must run before create_project_team below.
    try:
        from dash.instructions import REPLY_LANG as _REPLY_LANG
        _is_my = any('က' <= _c <= '႟' for _c in (message or ""))
        _REPLY_LANG.set("my" if _is_my else "en")
    except Exception:
        pass

    # ── Dash-OS Phase 9 — set RunContext for skill auto-inject + hooks/HITL ──
    try:
        from dash.agentic.run_context import set_context, RunContext
        _rc_cm = set_context(RunContext(
            project_slug=slug,
            user_id=user.get("id") if user else None,
            session_id=session_id,
            agent_name="Leader",
            trigger_kind="chat",
            user_attrs={"last_user_message": message[:8000]},
        ))
        _rc_cm.__enter__()  # held for endpoint duration; not strict but safe
    except Exception:
        pass

    # Bind project + user to the LLM cost ledger so platform-chat spend
    # attributes to a person in the Admin Usage dashboard (per-user rollups).
    try:
        from dash.settings import set_llm_project, set_llm_actor
        set_llm_project(slug)
        set_llm_actor(user.get("username") if user else None)
    except Exception:
        pass

    # ── Issue #3 — RLS context wiring for internal chat ──────────────────
    # Pull user's active scope from dash_user_scopes (if any) and set both
    # ContextVars (skill_refinery) AND PG session attrs for any tool that
    # opens a connection downstream. Fail-soft: no scope → empty dict.
    try:
        _user_attrs: dict = {}
        _uid = user.get("user_id") if user else None
        if _uid:
            from sqlalchemy import text as _sa_text
            from db import get_sql_engine as _get_eng
            _eng = _get_eng()
            with _eng.connect() as _conn:
                _row = _conn.execute(_sa_text(
                    "SELECT scope_id, scope_label, role FROM public.dash_user_scopes "
                    "WHERE user_id = :uid AND project_slug = :p "
                    "ORDER BY is_default DESC, scope_id ASC LIMIT 1"
                ), {"uid": _uid, "p": slug}).mappings().first()
            if _row:
                _user_attrs = {
                    "scope_id": _row["scope_id"],
                    "scope_label": _row["scope_label"],
                    "_user_role": _row["role"],
                    "role": _row["role"],
                }
                # Common alias: many filters use :site_code / :store_id.
                if _row["scope_id"]:
                    _user_attrs.setdefault("site_code", _row["scope_id"])
                    _user_attrs.setdefault("store_id", _row["scope_id"])
        # Always set context (empty if no scope) so downstream tools see *something*.
        try:
            from dash.tools.skill_refinery import set_request_context as _set_ctx
            _set_ctx(project_slug=slug, user_id=_uid, user_attrs=_user_attrs)
        except Exception:
            pass
    except Exception as _e:
        import logging as _lg
        _lg.warning(f"rls user_attrs wiring failed (passthrough): {_e}")

    # ── Scope guardrail pre-flight (Phase 5) ─────────────────────────────
    # Cheap LITE_MODEL check before invoking the team. If off-topic per
    # this project's auto-derived scope, short-circuit with a refusal.
    # FOLLOW-UP BYPASS: short messages w/ pronouns referencing prior turn
    # (e.g. "show me the data behind this", "break this down by category")
    # are NOT independently classifiable — they only make sense in context.
    # If session has any prior on-topic turn, skip the gate.
    _skip_scope_gate = False
    # Chitchat (greeting / "who are you" / "what can you do") is never off-topic
    # and is answered in plain prose anyway — don't spend a classifier LLM call
    # (even a cache MISS is ~500-800ms) gating it.
    try:
        from app.main import _is_chitchat as _isc_pre
        if _isc_pre(message):
            _skip_scope_gate = True
    except Exception:
        pass
    try:
        _msg_lc = (message or "").strip().lower()
        _wc = len(_msg_lc.split())
        _has_pronoun = any(p in _msg_lc.split() for p in
                           ("this", "that", "these", "those", "it", "them",
                            "they", "here", "such", "above", "previous"))
        # short + pronoun = almost certainly a follow-up
        if _wc <= 12 and _has_pronoun:
            _skip_scope_gate = True
        # OR session has any prior assistant message → follow-up context exists
        elif session_id:
            try:
                from db import get_sql_engine as _gse_fu
                _hist_eng = _gse_fu()
                with _hist_eng.connect() as _hc:
                    _has_prior = _hc.execute(text(
                        "SELECT 1 FROM ai.agno_sessions "
                        " WHERE session_id=:sid AND jsonb_array_length(COALESCE(runs,'[]'::jsonb)) > 0 "
                        " LIMIT 1"
                    ), {"sid": session_id}).fetchone() is not None
                if _has_prior and _wc <= 20:
                    # Short follow-up after at least one prior turn → trust context
                    _skip_scope_gate = True
            except Exception:
                pass
    except Exception:
        pass

    if not _skip_scope_gate:
        try:
            from dash.scope_classifier import classify_question, log_refusal
            # API-mode wrapper: the gateway prepends a big "[API MODE] … USER
            # QUESTION:\n" style directive. The scope classifier must see ONLY the
            # real user question — the directive's API/JSON/table jargon otherwise
            # tips a LITE-model classifier into a false "off-topic" refusal.
            _scope_msg = message
            if isinstance(message, str) and message.startswith("[API MODE]"):
                _qi = message.find("USER QUESTION:\n")
                if _qi != -1:
                    _scope_msg = message[_qi + len("USER QUESTION:\n"):].strip()
            decision = classify_question(slug, _scope_msg)
            if decision.refused:
                log_refusal(slug, message, decision, user_id=user.get("user_id"))
                # Mark refusal for the memory promoter + other bg tasks (Issue #6)
                try:
                    from dash.runtime.refusal import mark_refused
                    mark_refused(session_id, message,
                                 source="scope_classifier",
                                 reason=decision.reason or "off_topic")
                except Exception:
                    pass
                refusal = decision.refusal_message or "I can't help with that."
                if stream:
                    from fastapi.responses import StreamingResponse
                    import json as _json
                    async def _refuse_stream():
                        yield f"event: TeamRunContent\ndata: {_json.dumps({'content': refusal})}\n\n"
                        yield "event: TeamRunCompleted\ndata: {}\n\n"
                    return StreamingResponse(_refuse_stream(), media_type="text/event-stream")
                return {"content": refusal, "session_id": session_id, "refused": True}
        except Exception as e:
            import logging
            logging.warning(f"scope classifier failed (fail-open): {e}")

    # Apply reasoning mode + analysis type (backend detection).
    # Capability/greeting questions that DO run the agent (e.g. "who are you",
    # "what can you do", "help") should answer like a pharmacist in plain prose
    # — NOT with the dashboard tag scaffolding. _is_chitchat catches those and
    # _chitchat_instructions bans every structured tag/card/chart/table.
    from app.main import _apply_reasoning_mode, _is_chitchat, _chitchat_instructions
    if _is_chitchat(message):
        context_msg = _chitchat_instructions()
    else:
        context_msg = _apply_reasoning_mode(message, reasoning, analysis_type)

    # Per-message retrieval: rank trained Q→SQL pairs by relevance to THIS question
    # and prepend the best matches so short prompts hit the right proven SQL.
    # (Session-level instructions can't rank against the live question — Issue: CRM
    # feedback 2026-05-21, "only works with long prompts".)
    try:
        _proven = _rank_training_qa(slug, message, k=3)
        if _proven:
            context_msg = _proven + "\n\n" + context_msg
    except Exception as _e:
        logging.debug(f"training-qa rank skipped: {_e}")

    # SHOP CONTEXT — bind counter staff to their branch (CityPharma single-agent).
    # Stock/availability/substitute answers default to this branch; other branches = transfer hint.
    try:
        _uid_shop = (user.get("user_id") or user.get("id")) if user else None
        if _uid_shop:
            from sqlalchemy import text as _sqltext
            from db import get_sql_engine as _gse_shop
            with _gse_shop().connect() as _shc:
                _srow = _shc.execute(
                    _sqltext("SELECT site_code, store_ids, scope_mode FROM public.dash_users WHERE id = :uid"),
                    {"uid": _uid_shop}).fetchone()
            _site = _srow[0] if _srow else None
            _ids_csv = (_srow[1] if _srow and len(_srow) > 1 else "") or ""
            _scope_mode = (_srow[2] if _srow and len(_srow) > 2 else "") or "store"
            _stores = [x.strip() for x in _ids_csv.split(",") if x.strip()]
            # scope_mode='global' = platform owner / analyst → NO branch lock, sees ALL shops.
            # Only real store logins (scope_mode='store') get the SHOP CONTEXT branch filter.
            # (API-gateway / embed store keys are scoped via API_STORE_SCOPE, a separate path.)
            if _scope_mode == "global":
                pass
            elif len(_stores) > 1:
                # Multi-outlet key: the toolset scopes to the owned SET automatically —
                # the agent must NOT pass a single site_code (would narrow the set).
                _lst = ", ".join(_stores)
                context_msg = (
                    "## SHOP CONTEXT (multi-outlet)\n"
                    f"You assist a group covering {len(_stores)} outlets: {_lst}. "
                    "For stock / availability / 'do we have X' / 'find <salt>' / substitute questions, "
                    "do NOT pass a single site_code — the tools already scope to ALL of these outlets and "
                    "return per-outlet breakdowns ('your_stores'). Report the combined total AND name the "
                    "outlets. Stores outside this set are availability-only.\n\n"
                ) + context_msg
            elif _site:
                context_msg = (
                    "## SHOP CONTEXT (pharmacy counter)\n"
                    f"You assist counter staff at branch site_code = {_site}. For stock / availability / "
                    f"'do we have X' / 'find <salt>' / substitute questions, ALWAYS pass site_code='{_site}' to "
                    "stock_check / find_substitutes so stock means THIS branch. List other branches only as a "
                    "transfer option.\n\n"
                ) + context_msg
    except Exception as _she:
        logging.debug(f"shop context skipped: {_she}")

    # Follow-up context: previously prepended a "## PRIOR TURN" Q+A block onto
    # the user message for scope-gate-bypass pronoun questions. Removed — Agno
    # already carries multi-turn memory via ai.agno_sessions.runs, so the manual
    # prepend was redundant and leaked scaffolding into the stored/displayed turn.
    # Raw user question is now sent + stored + shown unchanged.

    # ── Complexity Router (Feature A) — informational only ───────────────
    # Classify how hard this message is and which model tier it maps to.
    # Does NOT change which team runs; surfaced to the UI via SSE / response.
    try:
        from dash.routing import classify_complexity
        _router_decision = classify_complexity(slug, message, session_id=session_id)
    except Exception as _e:
        logging.debug(f"complexity router skipped: {_e}")
        _router_decision = None

    # ── Manual model override (composer picker) ──────────────────────────
    # model_pref: auto|lite|mid|deep. When not "auto", the user's pick wins; the
    # router's choice is preserved as suggested_tier/suggested_model so the UI can
    # still show "auto would pick X". effort already mapped into reasoning above.
    _effective_model = None
    if _router_decision:
        _router_decision["suggested_tier"] = _router_decision.get("tier")
        _router_decision["suggested_model"] = _router_decision.get("model")
        _router_decision["effort"] = effort or None
        if model_pref and model_pref != "auto":
            try:
                from dash.settings import DEEP_MODEL as _DM, LITE_MODEL as _LM, MID_MODEL as _MM, REASONING_MODEL as _RM, ULTRA_MODEL as _UM
                _eff = {"lite": _LM, "mid": _MM, "deep": _DM, "reasoning": _RM, "ultra": _UM}.get(model_pref)
                if _eff:
                    _effective_model = _eff
                    _router_decision["override"] = model_pref
                    _router_decision["model"] = _eff
                    # Manual pick runs the team — drop the TRIVIAL short-circuit.
                    _router_decision.pop("short_circuit", None)
                    _router_decision.pop("reply", None)
            except Exception:
                pass

    # TRIVIAL tier → short-circuit ONLY on Auto. A manual model pick means the
    # user wants a real answer even to smalltalk, so respect it and run the team.
    if _router_decision and _router_decision.get("short_circuit") and model_pref in ("", "auto"):
        _reply = _router_decision.get("reply") or "Hi! Ask me about your data."
        if stream:
            from fastapi.responses import StreamingResponse
            async def _trivial_stream():
                yield f"event: RouterDecision\ndata: {_json.dumps(_router_decision, default=str)}\n\n"
                yield f"event: TeamRunContent\ndata: {_json.dumps({'content': _reply})}\n\n"
                yield "event: TeamRunCompleted\ndata: {}\n\n"
            return StreamingResponse(_trivial_stream(), media_type="text/event-stream")
        return {"content": _reply, "session_id": session_id, "router_decision": _router_decision}

    # ── Answer-cache short-circuit (P1) ──────────────────────────────────
    # When the question SEMANTICALLY matches a pinned full answer (cosine NN in
    # dash_vectors namespace='qcache', sim ≥ ANSWER_CACHE_MIN_SIM) AND the source
    # schema is unchanged, serve the saved AnswerCard verbatim — zero LLM, no SQL
    # re-run. Paraphrases hit. Falls through on miss/drift. Kill: ANSWER_CACHE_DISABLED=1.
    import os as _os_ac
    if (model_pref in ("", "auto")
            and _os_ac.getenv("ANSWER_CACHE_DISABLED", "").strip().lower() not in ("1", "true", "yes")):
        try:
            from dash.learning.answer_cache import try_answer_cache
            _ac = await try_answer_cache(slug, message)
        except Exception:
            _ac = None
        if _ac and _ac.get("content"):
            _ac_answer = _ac["content"]
            _ac_elapsed = int(_ac.get("elapsed_ms") or 0)
            _ac_sim = float(_ac.get("similarity") or 0.0)
            import logging as _ac_log
            _ac_log.getLogger(__name__).info(
                "answer_cache hit for %s · sim=%.3f · id=%s · %dms · 0 LLM",
                slug, _ac_sim, _ac.get("id"), _ac_elapsed)

            # Learning loop still records this turn (judge + verified reward).
            def _ac_bg(_q: str, _a: str, _slug: str, _sid: str):
                try:
                    from dash.tools.judge import judge_response
                    judge_response(_slug, _sid, _q, _a)
                except Exception:
                    pass
                try:
                    from dash.learning.verified_reward import score_verified
                    score_verified(_slug, _q, _a, _sid)
                except Exception:
                    pass
            # Bounded pool, not a raw per-request thread: a burst of concurrent
            # chats would otherwise spawn an unbounded burst of OS threads, each
            # opening a DB connection (amplifies direct-connection pressure).
            _bg_executor.submit(_ac_bg, message, _ac_answer, slug, session_id or "")

            _ac_trace = {
                "id": f"answercache_{session_id or 'ac'}",
                "title": "Served from answer cache",
                "content": (f"Matched a pinned answer by meaning (cosine {_ac_sim:.3f} ≥ "
                            f"{_os_ac.getenv('ANSWER_CACHE_MIN_SIM', '0.93')}). "
                            f"Returned the saved answer — 0 LLM tokens, {_ac_elapsed}ms."),
                "agent": "answer_cache",
                "model": "cached",
                "tier": "cached_answer",
                "tokens": 0,
                "cost_usd": 0,
                "duration_ms": _ac_elapsed,
            }
            if stream:
                from fastapi.responses import StreamingResponse
                from dash.utils.sse import emit_event_sync as _emit_ac
                async def _ac_stream():
                    _sid = session_id or ""
                    try:
                        yield _emit_ac("ReasoningStep", _ac_trace, session_id=_sid, project_slug=slug)
                    except Exception:
                        pass
                    yield _emit_ac("TeamRunContent", {"content": _ac_answer}, session_id=_sid, project_slug=slug)
                    yield _emit_ac("TeamRunCompleted", {}, session_id=_sid, project_slug=slug)
                return StreamingResponse(_ac_stream(), media_type="text/event-stream")
            return {"content": _ac_answer, "session_id": session_id,
                    "cached_answer": True, "similarity": _ac_sim,
                    "elapsed_ms": _ac_elapsed, "trace": [_ac_trace]}

    # ── Metric-fallback short-circuit ────────────────────────────────────
    # When the question STRONGLY matches a verified proven Q&A pair (≥3
    # shared rare-term tokens), serve the metric engine's locked answer
    # directly. Pinned-metric questions become deterministic + ~30ms instead
    # of risking LITE-model flakiness on the `metric` tool (the "couldn't
    # query / empty result" class). Reuses the verified-reward matcher.
    # Kill-switch: admin setting metric_shortcut_disabled (env fallback).
    try:
        from dash.admin.settings import get_setting as _gs_ms
        _ms_disabled = bool(_gs_ms("metric_shortcut_disabled"))
    except Exception:
        import os as _os_ms
        _ms_disabled = _os_ms.getenv("METRIC_SHORTCUT_DISABLED", "").strip().lower() in ("1", "true", "yes")
    if (model_pref in ("", "auto") and not _ms_disabled):
        try:
            from dash.learning.verified_reward import try_metric_shortcut
            _ms = try_metric_shortcut(slug, message)
        except Exception:
            _ms = None
        if _ms and _ms.get("matched") and _ms.get("value") is not None:
            _v = _ms["value"]
            _fmt = f"{int(_v):,}" if isinstance(_v, (int, float)) and float(_v).is_integer() else f"{_v:,.2f}"
            _src_q = (_ms.get("source_q") or "").strip()
            _ms_rows = _ms.get("rows") or []
            _ms_cols = _ms.get("columns") or []
            _ms_rowcount = int(_ms.get("row_count") or 0)
            _ms_elapsed = int(_ms.get("elapsed_ms") or 0)
            # Auto-chart hint when shape supports it (1 dim + 1 measure, ≥2 rows)
            _chart_hint = ""
            if _ms_rowcount >= 2 and len(_ms_cols) >= 2:
                _chart_hint = f"\n[CHART:{_src_q[:60]}]"
            # Speed badge — surfaced near the KPI
            _speed_tag = f"[VERIFIED:{_ms_elapsed}ms · cached]"

            # 2026-05-26: enrich cached path so STANDARD exec card renders
            # properly (narration + RELATED + RECOMMENDATION). Mini LLM call
            # (~$0.001 LITE_MODEL) — kill switch: METRIC_SHORTCUT_ENRICH=0.
            _narration = ""
            _related_tag = ""
            _rec_tag = ""
            _action_title = ""
            if _os_ms.getenv("METRIC_SHORTCUT_ENRICH", "1").strip().lower() not in ("0", "false", "no", "off"):
                try:
                    from dash.settings import training_llm_call as _tllm
                    import json as _json_e
                    _preview_rows = _ms_rows[:5] if _ms_rows else []
                    _enrich_prompt = (
                        "Write a 2-sentence executive narration AND 3 follow-up "
                        "questions for this verified data answer. Return ONLY "
                        "JSON: {\"action_title\":\"...\",\"narration\":\"...\","
                        "\"related\":[\"q1\",\"q2\",\"q3\"],"
                        "\"recommendation\":\"action sentence\"}.\n\n"
                        f"Question: {message[:300]}\n"
                        f"Headline value: {_fmt}\n"
                        f"Row count: {_ms_rowcount}\n"
                        f"Columns: {_ms_cols[:8]}\n"
                        f"Sample rows: {_preview_rows}\n"
                    )
                    _raw = _tllm(_enrich_prompt, "extraction") or ""
                    # Strip code fences if present
                    _raw_clean = _raw.strip()
                    if _raw_clean.startswith("```"):
                        _raw_clean = _raw_clean.split("```")[1] if "```" in _raw_clean[3:] else _raw_clean[3:]
                        if _raw_clean.startswith("json"):
                            _raw_clean = _raw_clean[4:]
                    _o_start = _raw_clean.find("{")
                    _o_end = _raw_clean.rfind("}")
                    if _o_start >= 0 and _o_end > _o_start:
                        _enr = _json_e.loads(_raw_clean[_o_start:_o_end + 1])
                        _action_title = (_enr.get("action_title") or "").strip()[:140]
                        _narration = (_enr.get("narration") or "").strip()[:400]
                        _rels = _enr.get("related") or []
                        if isinstance(_rels, list) and _rels:
                            _related_tag = "[RELATED: " + "|".join(
                                str(q).strip()[:80] for q in _rels[:3] if q
                            ) + "]"
                        _rec_text = (_enr.get("recommendation") or "").strip()[:160]
                        if _rec_text:
                            _rec_tag = f"[RECOMMENDATION: 1|{_rec_text}|—|5 min|Investigate]"
                except Exception as _enr_exc:
                    import logging as _ms_log2
                    _ms_log2.getLogger(__name__).debug(
                        "metric_shortcut enrich failed: %s", _enr_exc)

            # Build full STANDARD exec answer
            _parts: list[str] = []
            if _action_title:
                _parts.append(f"[ACTION_TITLE: {_action_title}]")
            else:
                _parts.append(f"[ACTION_TITLE: {_fmt} — verified result for your question]")
            if _narration:
                _parts.append(f"[NARRATION: {_narration}]")
            else:
                _fallback_nar = (
                    f"The verified pinned metric returned **{_fmt}** "
                    f"({_ms_rowcount} row(s) from cached SQL, {_ms_elapsed}ms). "
                    f"Matched proven question: _{_src_q[:120]}_."
                )
                _parts.append(f"[NARRATION: {_fallback_nar}]")
            _parts.append(f"[KPI:{_fmt}|Result 🟢|—]")
            if _ms_rowcount:
                _parts.append(f"[KPI:{_ms_rowcount:,}|Rows returned 🟢|—]")
            if _ms_elapsed:
                _parts.append(f"[KPI:{_ms_elapsed}ms|Cached lookup 🟢|deterministic]")
            if _rec_tag:
                _parts.append(_rec_tag)
            _parts.append(
                "[RECOMMENDATION: 2|Drill into the result by breakdown column|"
                "Surface drivers|10 min|Drill in]"
            )
            if _related_tag:
                _parts.append(_related_tag)
            _parts.append("[CONFIDENCE:HIGH]")
            if _chart_hint:
                _parts.append(_chart_hint.strip())

            _answer = (
                f"**{_fmt}** {_speed_tag}\n\n"
                + "\n".join(_parts)
                + (f"\n\n**{_ms_rowcount} row(s) returned** — see DATA tab for full result."
                   if _ms_rowcount > 1 else "")
            )
            import logging as _ms_log
            _ms_log.getLogger(__name__).info(
                "metric_shortcut hit for %s · score=%.2f · src=%r",
                slug, float(_ms.get("score") or 0.0), _src_q[:80])

            # Fire judge + verified-reward in a daemon thread so the learning
            # loop still records this turn (verified-reward will mark `pass`).
            def _ms_bg(_q: str, _a: str, _slug: str, _sid: str):
                try:
                    from dash.tools.judge import judge_response
                    judge_response(_slug, _sid, _q, _a)
                except Exception:
                    pass
                try:
                    from dash.learning.verified_reward import score_verified
                    score_verified(_slug, _q, _a, _sid)
                except Exception:
                    pass
            # Bounded pool, not a raw per-request thread (see _ac_bg above).
            _bg_executor.submit(_ms_bg, message, _answer, slug, session_id or "")

            # Synthetic trace events so the UI surfaces the cached path:
            #   - ReasoningStep → trace card "Used cached verified metric"
            #   - ToolCall (started+completed, run_sql_query) → SQL tab + tool count
            _ms_sql = (_ms.get("sql") or "").strip()
            _tc_id = f"metric_tool_{session_id or 'ms'}"
            _trace_payload = {
                "id": f"metric_{session_id or 'ms'}",
                "title": "Used cached verified metric",
                "content": (f"Matched pinned Q→SQL pair (score={float(_ms.get('score') or 0.0):.2f}). "
                            f"Source question: {_src_q[:160]}. "
                            f"Computed deterministically — 0 LLM tokens."),
                "agent": "metric_engine",
                "model": "deterministic",
                "tier": "verified_metric",
                "tokens": 0,
                "cost_usd": 0,
                "duration_ms": int((_ms.get('elapsed_ms') or 0)),
            }
            _tool_start_payload = {
                "tool": {
                    "tool_call_id": _tc_id,
                    "tool_name": "run_sql_query",
                    "tool_args": {"query": _ms_sql},
                },
                "agent": "metric_engine",
            }
            _tool_done_payload = {
                "tool": {
                    "tool_call_id": _tc_id,
                    "tool_name": "run_sql_query",
                    "tool_args": {"query": _ms_sql},
                    "result": {
                        "value": _v,
                        "matched_metric": _src_q,
                        "cached": True,
                        "deterministic": True,
                        "rows": _ms_rows,
                        "columns": _ms_cols,
                        "row_count": _ms_rowcount,
                    },
                    "duration_ms": _ms_elapsed,
                },
                "agent": "metric_engine",
            }
            if stream:
                from fastapi.responses import StreamingResponse
                from dash.utils.sse import emit_event_sync as _emit
                # 2026-05-25 (Day 2): centralized SSE emit (safe_dumps under
                # the hood) + per-event try/except so a bad payload type can
                # never kill the stream before TeamRunContent. See
                # dash/utils/sse.py — every new SSE generator MUST use this.
                async def _metric_stream():
                    # 2026-05-25 (Phase 7): pass session_id + project_slug so
                    # sse_audit captures every event for "broken stream" diag.
                    _sid = session_id or ""
                    try:
                        yield _emit("ReasoningStep", _trace_payload, session_id=_sid, project_slug=slug)
                    except Exception:
                        pass
                    try:
                        yield _emit("ToolCallStarted", _tool_start_payload, session_id=_sid, project_slug=slug)
                    except Exception:
                        pass
                    try:
                        yield _emit("ToolCallCompleted", _tool_done_payload, session_id=_sid, project_slug=slug)
                    except Exception:
                        pass
                    # Answer text MUST emit even if upstream events failed.
                    yield _emit("TeamRunContent", {"content": _answer}, session_id=_sid, project_slug=slug)
                    yield _emit("TeamRunCompleted", {}, session_id=_sid, project_slug=slug)
                return StreamingResponse(_metric_stream(), media_type="text/event-stream")
            return {"content": _answer, "session_id": session_id,
                    "matched_metric": _src_q, "verified_value": _v,
                    "sql": _ms_sql, "rows": _ms_rows, "columns": _ms_cols,
                    "row_count": _ms_rowcount, "elapsed_ms": _ms_elapsed,
                    "trace": [_trace_payload],
                    "tool_calls": [{"name": "run_sql_query",
                                    "args": {"query": _ms_sql},
                                    "result": {"value": _v, "cached": True,
                                               "rows": _ms_rows, "columns": _ms_cols}}]}

    # Continuous query learning (P4, Mode 1 BYPASS): exact-enough hit on a PROVEN
    # learned query → re-run its SQL live (fresh numbers) + render in code. ZERO
    # LLM. Only fires for verbatim-ish repeats of a verified question; everything
    # else falls through to recall-hint (P2) + the agent. Gated, auto-only.
    if model_pref in ("", "auto"):
        try:
            from dash.learning.query_bank import try_query_bank_serve
            _qb = try_query_bank_serve(slug, message)
        except Exception:
            _qb = None
        if _qb and _qb.get("content"):
            _qb_answer = _qb["content"]
            _qb_sid = session_id or ""
            _qb_trace = {"step": "query_bank", "label": "Reused a learned query",
                         "matched": _qb.get("matched_q", ""), "sim": _qb.get("sim"),
                         "elapsed_ms": _qb.get("elapsed_ms")}
            _qb_tool = {"name": "run_sql_query", "args": {"query": _qb.get("sql", "")},
                        "result": {"value": _qb.get("value"), "learned": True,
                                   "rows": _qb.get("rows", []), "columns": _qb.get("columns", [])}}
            if stream:
                from fastapi.responses import StreamingResponse
                from dash.utils.sse import emit_event_sync as _emit
                async def _qb_stream():
                    try:
                        yield _emit("ReasoningStep", _qb_trace, session_id=_qb_sid, project_slug=slug)
                    except Exception:
                        pass
                    yield _emit("TeamRunContent", {"content": _qb_answer}, session_id=_qb_sid, project_slug=slug)
                    yield _emit("TeamRunCompleted", {}, session_id=_qb_sid, project_slug=slug)
                return StreamingResponse(_qb_stream(), media_type="text/event-stream")
            return {"content": _qb_answer, "session_id": session_id,
                    "sql": _qb.get("sql"), "rows": _qb.get("rows"),
                    "columns": _qb.get("columns"), "row_count": _qb.get("row_count"),
                    "elapsed_ms": _qb.get("elapsed_ms"), "learned": True,
                    "trace": [_qb_trace], "tool_calls": [_qb_tool]}

        # Mode-1.5 reasoning cache — Mode-1 missed; reuse a proven plan with the
        # STORE literal swapped. Gated OFF by default (shadow-logs only when off).
        try:
            from dash.learning.param_swap import try_param_swap_serve as _ps_serve
            _ps = _ps_serve(slug, message)
        except Exception:
            _ps = None
        if _ps and _ps.get("content"):
            _ps_answer = _ps["content"]
            _ps_sid = session_id or ""
            _ps_trace = {"step": "query_bank", "label": "Reused a learned query (adapted)",
                         "matched": _ps.get("matched_q", ""), "shape": _ps.get("shape"),
                         "elapsed_ms": _ps.get("elapsed_ms")}
            _ps_tool = {"name": "run_sql_query", "args": {"query": _ps.get("sql", "")},
                        "result": {"value": _ps.get("value"), "learned": True,
                                   "rows": _ps.get("rows", []), "columns": _ps.get("columns", [])}}
            if stream:
                from fastapi.responses import StreamingResponse
                from dash.utils.sse import emit_event_sync as _emit
                async def _ps_stream():
                    try:
                        yield _emit("ReasoningStep", _ps_trace, session_id=_ps_sid, project_slug=slug)
                    except Exception:
                        pass
                    yield _emit("TeamRunContent", {"content": _ps_answer}, session_id=_ps_sid, project_slug=slug)
                    yield _emit("TeamRunCompleted", {}, session_id=_ps_sid, project_slug=slug)
                return StreamingResponse(_ps_stream(), media_type="text/event-stream")
            return {"content": _ps_answer, "session_id": session_id,
                    "sql": _ps.get("sql"), "rows": _ps.get("rows"),
                    "columns": _ps.get("columns"), "row_count": _ps.get("row_count"),
                    "elapsed_ms": _ps.get("elapsed_ms"), "learned": True,
                    "trace": [_ps_trace], "tool_calls": [_ps_tool]}

    # Inject proven similar queries into the agent context so the model reliably
    # reuses verified SQL (the recall_similar_queries tool is often skipped).
    # Mirrors the training-qa rank block above (prepend to context_msg). Fail-soft,
    # gated, thread-isolated recall. NB: os is not a module-level import here.
    try:
        import os as _qr_os
        if str(_qr_os.getenv("QUERY_RECALL_CONTEXT_DISABLED", "0")).lower() not in ("1", "true", "yes") \
                and len((message or "").strip()) >= 8:
            from dash.learning.query_bank import recall_similar_sync as _qr_recall
            _qr_hits = _qr_recall(slug, message, limit=2)
            if _qr_hits:
                _qr_block = "\n## SIMILAR PROVEN QUERIES (reuse/adapt these — they are verified)\n"
                for _qr_h in _qr_hits:
                    _qr_block += f"- Q: {_qr_h.get('question','')}\n  SQL: {_qr_h.get('sql','')}\n"
                context_msg = _qr_block + "\n\n" + context_msg
    except Exception:
        pass

    # Continuous query learning (P1 shadow): both shortcuts missed and we're about
    # to run the full agent. Log whether this question WOULD have hit the query
    # bank — measurement only, serves nothing. Fire-and-forget (own embed call), so
    # it never adds latency to the agent path. Fail-soft.
    try:
        import asyncio as _qb_asyncio
        from dash.learning.query_capture import shadow_match as _qb_shadow
        _qb_asyncio.ensure_future(_qb_shadow(slug, message))
    except Exception:
        pass

    # Create project-scoped team
    from dash.team import create_project_team
    team = create_project_team(
        project_slug=slug,
        agent_name=proj.get("agent_name", "Agent"),
        agent_role=proj.get("agent_role", ""),
        agent_personality=proj.get("agent_personality", "friendly"),
        user_id=user.get("user_id"),
    )

    # ── Enforce the router's model choice (Feature A) ────────────────────
    # The complexity tier picks a model string; apply it to the team + members
    # for THIS message so LOOKUP runs cheap and AGENTIC runs deep. temperature
    # stays pinned at 0.1 (determinism rule). Mutates the cached team in place
    # and is re-set on every message, so no stale-model carryover. Kill switch:
    # COMPLEXITY_ROUTER_ENFORCE=0. Fail-soft → keep the team's default model.
    try:
        import os as _os_rt
        _enforce_id = _effective_model or (_router_decision and _router_decision.get("model"))
        if (_enforce_id
                and _os_rt.getenv("COMPLEXITY_ROUTER_ENFORCE", "1").strip().lower() not in ("0", "false", "no", "off")):
            from agno.models.openrouter import OpenRouter as _OpenRouter
            # Effort → real model thinking. OpenRouter reasoning_effort: low|medium|high
            # (max maps to high). Setting it also makes the model stream its reasoning,
            # which the UI surfaces as a live thinking trace.
            # Floor reasoning_effort at "low" so the model always streams a
            # thinking trace (surfaced live in the UI), even on fast/lite lookups.
            # Opt out via admin setting reasoning_floor (env REASONING_FLOOR fallback).
            try:
                from dash.admin.settings import get_setting as _gs_rf
                _floor_on = bool(_gs_rf("reasoning_floor"))
            except Exception:
                import os as _os_rf
                _floor_on = _os_rf.getenv("REASONING_FLOOR", "1").strip().lower() not in ("0", "false", "no", "off")
            # Cheap tiers (TRIVIAL/LOOKUP = simple stock/drug lookups) don't need a
            # streamed thinking trace — flooring reasoning_effort there just adds
            # ~400ms with no analytical benefit. Floor only kicks in on
            # ANALYSIS/AGENTIC/REASONING where step-by-step actually helps.
            _tier = (_router_decision or {}).get("tier") or ""
            _cheap_tier = _tier in ("TRIVIAL", "LOOKUP")
            _re = {"low": "low", "medium": "medium", "high": "high", "max": "high"}.get(effort)
            if _re is None and _floor_on and not _cheap_tier:
                _re = "low"
            _mk = {"id": _enforce_id, "temperature": 0.1,
                   # Opt into OpenRouter providers that may train on inputs — without
                   # this the account data policy can 404 ("No endpoints matching your
                   # data policy") when the only compliant provider is unavailable for
                   # the full tool-using call. This per-tier model overrides MODEL, so
                   # the same extra_body that settings.MODEL sets must be repeated here.
                   "extra_body": {"provider": {"data_collection": "allow"}},
                   # Fast-with-failover: OpenRouter models[] tries the primary first,
                   # then gpt-5-mini if it errors/404s — single call, instant failover,
                   # no 2x cost. agno merges this list into extra_body["models"] while
                   # keeping the provider opt-in above. Primary listed first so it wins
                   # when healthy (gemini = fast + honors the Burmese override).
                   "models": ([_enforce_id, "openai/gpt-5-mini"]
                              if _enforce_id != "openai/gpt-5-mini"
                              else ["openai/gpt-5-mini", "google/gemini-3-flash-preview"])}
            if _re:
                _mk["reasoning_effort"] = _re
            _tier_model = _OpenRouter(**_mk)
            if _router_decision and _re:
                _router_decision["reasoning_effort"] = _re
            team.model = _tier_model
            for _m in (getattr(team, "members", None) or []):
                if hasattr(_m, "model"):
                    _m.model = _tier_model
            if _router_decision:
                _router_decision["enforced"] = True
    except Exception as _e:
        logging.debug(f"router model enforce skipped: {_e}")

    def _run_background_tasks(question: str, answer: str):
        """Run all post-chat learning tasks in ONE daemon thread (was ~11 separate calls).

        Each task is wrapped in its own try/except — one failing must not block the
        others. We open ONE write engine handle up front and reuse it for the
        auto_evolve+registry block. Tasks that own their own get_write_engine()
        internally (judge_response, score_verified, etc.) are called sequentially
        in the same thread — saves cumulative thread-spawn overhead but keeps
        their per-task connection handles intact (their internals not refactored).
        """
        import logging
        _logger = logging.getLogger(__name__)

        def _batched_bg():
            # 0. Detect agent self-refusal post-response (Issue #6 second site).
            # Scope-classifier already marks refusals at pre-flight; this catches
            # the case where the AGENT itself refused via SCOPE GUARDRAIL
            # instruction-level rule (no pre-flight refusal fired but answer
            # is gate-keep boilerplate). Marking here lets memory promoter +
            # other bg tasks short-circuit on next call.
            try:
                from dash.runtime.refusal import is_refusal_text, was_refused, mark_refused
                if is_refusal_text(answer) and not was_refused(session_id, question):
                    mark_refused(session_id or "_anon", question,
                                 source="agent_self", reason="text_sentinel_post")
                    _logger.info(f"agent_self refusal detected for {slug}/{session_id or '_'} — marked")
                    # Skip remaining bg tasks for refused turns — they'd poison memory
                    return
            except Exception as _re:
                _logger.debug(f"agent_self refusal detection failed: {_re}")

            # 1. suggest_rules
            try:
                from dash.tools.suggest_rules import suggest_rules_from_conversation
                suggest_rules_from_conversation(slug, session_id or "", question, answer)
            except Exception as e:
                _logger.error(f"Background task suggest_rules failed for {slug}: {e}")

            # 2. judge_response (owns its own write engine internally)
            try:
                from dash.tools.judge import judge_response
                judge_response(slug, session_id or "", question, answer)
            except Exception as e:
                _logger.error(f"Background task judge_response failed for {slug}: {e}")

            # 3. score_verified — HARD truth check (owns its own write engine)
            try:
                from dash.learning.verified_reward import score_verified
                score_verified(slug, question, answer, session_id or "")
            except Exception as e:
                _logger.debug(f"Background task score_verified failed for {slug}: {e}")

            # 4. proactive_insights
            try:
                from dash.tools.proactive_insights import generate_proactive_insights
                generate_proactive_insights(slug, question, answer, user.get("user_id"))
            except Exception as e:
                _logger.error(f"Background task proactive_insights failed for {slug}: {e}")

            # 5. extract_query_plan
            try:
                from dash.tools.query_plan_extractor import extract_query_plan
                extract_query_plan(slug, question, answer)
            except Exception as e:
                _logger.error(f"Background task query_plan_extractor failed for {slug}: {e}")

            # 6. extract_meta_learnings
            try:
                from dash.tools.meta_learning import extract_meta_learnings
                extract_meta_learnings(slug, question, answer)
            except Exception as e:
                _logger.error(f"Background task meta_learning failed for {slug}: {e}")

            # 7. reinforce_by_text (citation hook)
            try:
                from dash.learning.forgetting import reinforce_by_text
                reinforce_by_text(
                    (answer or "")[:500],
                    project_slug=slug,
                    max_matches=5,
                )
            except Exception as e:
                _logger.debug(f"Background task reinforce_by_text failed for {slug}: {e}")

            # 8. auto_evolve + registry refresh — uses ONE shared engine handle for
            # the chat-count read and the registry upsert (was opening twice).
            try:
                from sqlalchemy import text as _sa_text
                from db import get_sql_engine as _get_eng
                _eng = _get_eng()
                with _eng.connect() as _conn:
                    chat_count = _conn.execute(_sa_text("SELECT COUNT(*) FROM public.dash_quality_scores WHERE project_slug = :s"), {"s": slug}).scalar() or 0
                    last_evo = _conn.execute(_sa_text("SELECT chat_count_at_generation FROM public.dash_evolved_instructions WHERE project_slug = :s ORDER BY version DESC LIMIT 1"), {"s": slug}).fetchone()
                    last_count = last_evo[0] if last_evo else 0
                if chat_count - last_count >= 20:
                    from dash.tools.auto_evolve import auto_evolve_instructions
                    auto_evolve_instructions(slug)
                if chat_count % 10 == 0 and chat_count > 0:
                    from app.learning import _compute_registry
                    registry, _ = _compute_registry(slug)
                    with _eng.connect() as _conn2:
                        for r in registry:
                            _conn2.execute(_sa_text(
                                "INSERT INTO dash.dash_resource_registry (project_slug, resource_type, resource_count, health_score, staleness_days) "
                                "VALUES (:s, :t, :c, :h, :st) ON CONFLICT (project_slug, resource_type) DO UPDATE SET resource_count = :c, health_score = :h, staleness_days = :st, last_updated = NOW()"
                            ), {"s": slug, "t": r["type"], "c": r["count"], "h": r["health"], "st": r["staleness"]})
                        _conn2.commit()
            except Exception as e:
                _logger.error(f"Background task auto_evolve_registry failed for {slug}: {e}")

            # 9. dream_poignancy_hook
            try:
                _dream_poignancy_hook(
                    slug=slug,
                    session_id=session_id or "",
                    user=user,
                    question=question,
                    answer=answer,
                )
            except Exception as e:
                _logger.debug(f"dream_poignancy_hook failed for {slug}: {e}")

        # ONE batched job for all tasks (was ~11 separate submissions), on the
        # bounded pool rather than a raw per-request thread.
        _bg_executor.submit(_batched_bg)

    # sim chassis deleted 2026-05-23

    # ── Continuous training signal: capture message_id + start time so we can
    # log_signal after stream completes. tables_hit/sql_text best-effort from
    # response content; fire-and-forget, never breaks chat.
    # TODO: followup pill click endpoint not yet implemented — when frontend
    # POSTs followup-clicked events, call dash.learning.continuous.mark_followup_clicked(message_id).
    import uuid as _uuid_signal
    _signal_message_id = _uuid_signal.uuid4().hex
    _signal_start_ts = time.time()

    if stream:
        def event_generator():
          # Close the root chat trace when the stream finishes OR the client
          # disconnects (GeneratorExit hits this finally too). Fail-soft.
          _trace_err: str | None = None
          try:
            full_content = []
            _stream_start = time.time()
            # Best-effort per-project read engine for EXPLAIN cost estimates on
            # SQL tool events (search_path set to the project schema). Fail-soft.
            _cost_engine = None
            try:
                from db import get_project_readonly_engine as _get_proj_ro
                _cost_engine = _get_proj_ro(slug)
            except Exception:
                _cost_engine = None
            _seen_event_names: set[str] = set()
            _sql_texts: list[str] = []
            _sql_errors: list[str] = []
            _tables_hit: set[str] = set()
            # Running token totals for the per-chat cost-ledger row (written once
            # at stream end → powers Admin → Cost Analytics / dash_llm_costs).
            _usage_in_total = 0
            _usage_out_total = 0
            _usage_model: str | None = None
            # Heavy tiers run multi-step tool chains → give them a longer cap.
            _tier_now = (_router_decision or {}).get("tier")
            _stream_cap = 480 if _tier_now in ("AGENTIC", "REASONING") else 300
            if _router_decision:
                yield f"event: RouterDecision\ndata: {_json.dumps(_router_decision, default=str)}\n\n"
            # ── Context-exhaustion guard: trim stale tool-result content from
            # the agent's conversation history before the run. Behind env flag
            # (CONTEXT_GUARDS_DISABLED) + fail-open: any error → history untouched.
            try:
                from dash.guards.context import trim_stale_tool_results as _trim
                _msgs = None
                _sess = getattr(team, "memory", None) or getattr(team, "session", None)
                if _sess is not None and hasattr(_sess, "messages"):
                    _msgs = getattr(_sess, "messages", None)
                if isinstance(_msgs, list) and _msgs:
                    _trimmed = _trim(_msgs)
                    if _trimmed is not _msgs:
                        try:
                            _sess.messages = _trimmed
                        except Exception:
                            pass
            except Exception:
                pass
            try:
              with trace_span("chat.run", kind="chat", project_slug=slug):
                from dash.utils.agno_sse_wrap import audited_team_stream_sync
                # session_id used for audit attribution + auto-forwarded to team.run
                response_iter = audited_team_stream_sync(
                    team,
                    context_msg,
                    session_id=session_id or "",
                    project_slug=slug,
                    stream=True,
                    stream_events=True,
                )
                for event in response_iter:
                    if time.time() - _stream_start > _stream_cap:
                        timeout_msg = _json.dumps({"content": f"\n\nResponse timed out after {_stream_cap // 60} minutes."})
                        yield f"event: TeamRunContent\ndata: {timeout_msg}\n\n"
                        break
                    # Forward all events as SSE
                    if hasattr(event, 'to_dict'):
                        data = event.to_dict()
                    elif hasattr(event, 'model_dump'):
                        data = event.model_dump()
                    elif hasattr(event, 'content'):
                        data = {"content": event.content, "event": "TeamRunContent"}
                    else:
                        data = {"content": str(event)}

                    event_name = data.get("event", "TeamRunContent")

                    # ── Debug: log first occurrence of each event_name (helps trace
                    # nested member tool propagation issues across Agno versions).
                    if event_name not in _seen_event_names:
                        _seen_event_names.add(event_name)
                        try:
                            import logging
                            tool_name = (data.get("tool") or {}).get("tool_name") if isinstance(data.get("tool"), dict) else None
                            agent_name = data.get("agent_name") or data.get("member_id") or data.get("member_name")
                            logging.info(
                                f"[chat-stream] first event: name={event_name} "
                                f"agent={agent_name} tool={tool_name} keys={list(data.keys())[:8]}"
                            )
                        except Exception:
                            pass

                    # ── Inject agent_name on tool events so frontend can group nested
                    # member tool calls under their owning agent. Agno's member tool
                    # events sometimes lack agent_name at top level — pull from
                    # nested fields (member_id, agent_id, parent_run, etc.).
                    if event_name in (
                        "ToolCallStarted", "ToolCallCompleted",
                        "TeamToolCallStarted", "TeamToolCallCompleted",
                    ):
                        if not data.get("agent_name"):
                            owner = (
                                data.get("member_name")
                                or data.get("member_id")
                                or data.get("agent_id")
                                or (data.get("tool") or {}).get("agent_name")
                                if isinstance(data.get("tool"), dict) else None
                            )
                            if owner:
                                data["agent_name"] = owner

                    # ── Reasoning-trace normalization (OpenAI-style trace) ──
                    _agent_name = data.get("agent_name") or ""
                    try:
                        if event_name in (
                            "ToolCallStarted", "ToolCallCompleted",
                            "TeamToolCallStarted", "TeamToolCallCompleted",
                        ):
                            # think/analyze/reason → ReasoningStep (cleaner trace)
                            rstep = _reasoning_step_from_tool(data, _agent_name)
                            if rstep is not None:
                                yield f"event: ReasoningStep\ndata: {_json.dumps(rstep, default=str)}\n\n"
                            # Normalize tool object so frontend gets id/name/args/result
                            data["tool"] = _normalize_tool(data)
                            # Attach model (+ duration) per step when available.
                            _attach_model_to_tool(data["tool"], data)
                            # Attach EXPLAIN cost on SQL tool *start* events only.
                            if event_name in ("ToolCallStarted", "TeamToolCallStarted"):
                                _attach_sql_cost(data["tool"], _cost_engine)
                        elif event_name in (
                            "ReasoningStep", "ReasoningStepStarted", "ReasoningStepCompleted",
                            "ReasoningCompleted", "ReasoningContent",
                        ):
                            rstep = _reasoning_step_from_content(data, _agent_name)
                            if rstep is not None:
                                yield f"event: ReasoningStep\ndata: {_json.dumps(rstep, default=str)}\n\n"
                                continue
                        elif event_name in ("TeamRunContent", "RunContent") and data.get("reasoning_content"):
                            rstep = _reasoning_step_from_content(data, _agent_name)
                            if rstep is not None:
                                yield f"event: ReasoningStep\ndata: {_json.dumps(rstep, default=str)}\n\n"
                    except Exception:
                        pass

                    # Accumulate content for background tasks
                    if event_name in ("TeamRunContent", "RunContent") and data.get("content"):
                        full_content.append(data["content"])

                    # Capture SQL + tables hit from tool call events for training signal
                    try:
                        if event_name in ("ToolCallStarted", "ToolCallCompleted", "TeamToolCallStarted", "TeamToolCallCompleted"):
                            tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
                            args = tool.get("tool_args") if isinstance(tool.get("tool_args"), dict) else {}
                            for k in ("query", "sql", "statement", "sql_query"):
                                v = args.get(k) if isinstance(args, dict) else None
                                if v and isinstance(v, str):
                                    _sql_texts.append(v)
                                    import re as _re_sig
                                    for m in _re_sig.finditer(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w\.]*)", v, _re_sig.IGNORECASE):
                                        _tables_hit.add(m.group(1))
                            result = tool.get("result") if isinstance(tool.get("result"), str) else None
                            if result and ("error" in result.lower() or "exception" in result.lower()):
                                _sql_errors.append(result[:500])
                    except Exception:
                        pass

                    # ── Usage event — token counts + model when Agno exposes them.
                    # Best-effort per agent/member completion; cumulative total at
                    # TeamRunCompleted. Multiple Usage events are fine (frontend sums).
                    try:
                        _usage = _usage_from_event(data, _agent_name)
                        if _usage is not None:
                            _usage_in_total += int(_usage.get("input_tokens", 0) or 0)
                            _usage_out_total += int(_usage.get("output_tokens", 0) or 0)
                            if _usage.get("model"):
                                _usage_model = str(_usage["model"])
                            yield f"event: Usage\ndata: {_json.dumps(_usage, default=str)}\n\n"
                    except Exception:
                        pass

                    yield f"event: {event_name}\ndata: {_json.dumps(data, default=str)}\n\n"
                # Attach token usage to the chat.run span (cost computed below).
                if _usage_in_total or _usage_out_total:
                    try:
                        from dash.settings import _compute_cost as _cc2, CHAT_MODEL as _cm2
                        record_cost(
                            usd=_cc2(_usage_model or _cm2, {
                                "prompt_tokens": _usage_in_total,
                                "completion_tokens": _usage_out_total,
                            }),
                            tokens=int(_usage_in_total) + int(_usage_out_total),
                            model=(_usage_model or _cm2),
                        )
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as e:
                import logging
                logging.exception("Chat error")
                _trace_err = str(e)
                yield f"event: TeamRunContent\ndata: {_json.dumps({'content': 'An error occurred while processing your request'})}\n\n"

            # Trigger background tasks after stream completes.
            # Apply the greeting-dedupe guard to the fully-assembled answer
            # (the only point where complete content is known) before it feeds
            # background tasks / persistence.
            # NOTE: SSE content chunks are already emitted per-token above
            # (event: TeamRunContent), so the live-displayed stream cannot be
            # cleanly de-duped here without breaking chunking — the instruction
            # rule (E1) is the primary fix for the streamed display; this guard
            # protects everything that consumes the final assembled text.
            answer = _dedupe_greeting("".join(full_content))
            if answer:
                _run_background_tasks(message, answer)

            # ── Per-chat cost ledger row → public.dash_llm_costs (powers
            # Admin → Cost Analytics). One row per chat turn. Fail-soft.
            if _usage_in_total or _usage_out_total:
                try:
                    from dash.settings import _compute_cost as _cc, CHAT_MODEL as _cm
                    _model = _usage_model or _cm
                    _cost = _cc(_model, {
                        "prompt_tokens": _usage_in_total,
                        "completion_tokens": _usage_out_total,
                    })
                    from sqlalchemy import text as _t
                    from db.session import get_sql_engine as _gse
                    with _gse().connect() as _c:
                        _c.execute(_t(
                            "INSERT INTO public.dash_llm_costs "
                            "(project_slug, task, model, cost_usd, tokens_in, tokens_out, ok) "
                            "VALUES (:s, 'chat', :m, :c, :ti, :to, true)"
                        ), {"s": slug, "m": _model, "c": float(_cost),
                            "ti": int(_usage_in_total), "to": int(_usage_out_total)})
                        _c.commit()
                except Exception:
                    import logging as _lg
                    _lg.getLogger("dash.projects").debug("chat cost ledger write skipped", exc_info=True)

            # Continuous training signal — fire-and-forget
            try:
                from dash.learning.continuous import log_signal as _log_signal
                _log_signal(
                    project_slug=slug,
                    chat_id=session_id or "",
                    message_id=_signal_message_id,
                    question=message,
                    tables_hit=sorted(_tables_hit),
                    sql_text=(_sql_texts[-1] if _sql_texts else None),
                    sql_success=(len(_sql_texts) > 0 and not _sql_errors) if _sql_texts else None,
                    sql_error=(_sql_errors[0] if _sql_errors else None),
                    agent_used=proj.get("agent_name", "Agent"),
                    duration_ms=int((time.time() - _signal_start_ts) * 1000),
                )
            except Exception:
                pass
          finally:
            # Close the root chat trace (fires on normal end + client disconnect).
            try:
                end_trace("error" if _trace_err else "done", _trace_err)
            except Exception:  # noqa: BLE001
                pass

        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else:
        try:
            with trace_span("chat.run", kind="chat", project_slug=slug):
                response = team.run(context_msg, session_id=session_id)
            content = _dedupe_greeting(response.content or "")
            if content:
                _run_background_tasks(message, content)
            # Continuous training signal — fire-and-forget
            try:
                from dash.learning.continuous import log_signal as _log_signal
                _log_signal(
                    project_slug=slug,
                    chat_id=session_id or "",
                    message_id=_signal_message_id,
                    question=message,
                    tables_hit=[],
                    sql_text=None,
                    sql_success=None,
                    sql_error=None,
                    agent_used=proj.get("agent_name", "Agent"),
                    duration_ms=int((time.time() - _signal_start_ts) * 1000),
                )
            except Exception:
                pass
            end_trace("done")
            return {"content": content, "session_id": session_id, "router_decision": _router_decision}
        except Exception as e:
            import logging
            logging.exception("Chat error")
            try:
                end_trace("error", str(e))
            except Exception:  # noqa: BLE001
                pass
            return {"content": "An error occurred while processing your request", "session_id": session_id}


@router.get("/shared")
def shared_with_me(request: Request):
    """List projects shared with current user."""
    user = _get_user(request)
    with _engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT p.id, p.slug, p.name, p.agent_name, p.agent_role, p.schema_name, p.created_at, s.shared_by, p.updated_at
            FROM public.dash_projects p
            JOIN public.dash_project_shares s ON s.project_id = p.id
            WHERE s.shared_with_user_id = :uid
            ORDER BY s.created_at DESC
        """), {"uid": user["user_id"]}).fetchall()

    # Get last_trained for shared projects
    last_trained_map: dict[str, str] = {}
    with _engine.connect() as conn:
        slugs = [r[1] for r in rows]
        if slugs:
            for sl in slugs:
                tr = conn.execute(text(
                    "SELECT finished_at FROM public.dash_training_runs WHERE project_slug = :s AND status = 'done' ORDER BY finished_at DESC LIMIT 1"
                ), {"s": sl}).fetchone()
                if tr and tr[0]:
                    last_trained_map[sl] = str(tr[0])

    insp = inspect(_engine)
    projects = []
    for r in rows:
        schema = r[5]
        tables = 0
        total_rows = 0
        try:
            tbl_names = insp.get_table_names(schema=schema)
            tables = len(tbl_names)
            with _engine.connect() as conn:
                for t in tbl_names:
                    try:
                        c = conn.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{t}"')).scalar() or 0
                        total_rows += c
                    except Exception:
                        pass
        except Exception:
            pass
        projects.append({
            "id": r[0], "slug": r[1], "name": r[2], "agent_name": r[3], "agent_role": r[4],
            "tables": tables, "rows": total_rows, "shared_by": r[7],
            "created_at": str(r[6]) if r[6] else None,
            "updated_at": str(r[8]) if r[8] else str(r[6]) if r[6] else None,
            "last_trained": last_trained_map.get(r[1]),
        })
    return {"projects": projects}


@router.put("/{slug}")
def update_project(slug: str, request: Request, name: str = "", agent_name: str = "", agent_role: str = "", agent_personality: str = ""):
    """Update project agent config and/or name."""
    user = _get_user(request)
    updates = []
    params: dict = {"s": slug, "uid": user["user_id"]}
    if name:
        if len(name) < 2:
            raise HTTPException(400, "Name must be at least 2 characters")
        updates.append("name = :nm")
        params["nm"] = name
    if agent_name:
        updates.append("agent_name = :an")
        params["an"] = agent_name
    if agent_role is not None:
        updates.append("agent_role = :ar")
        params["ar"] = agent_role
    if agent_personality:
        updates.append("agent_personality = :ap")
        params["ap"] = agent_personality
    if updates:
        updates.append("updated_at = NOW()")
        with _engine.connect() as conn:
            conn.execute(text(
                f"UPDATE public.dash_projects SET {', '.join(updates)} WHERE slug = :s AND user_id = :uid"
            ), params)
            conn.commit()
    return {"status": "ok"}


@router.post("/{slug}/reembed/run-now")
async def reembed_run_now(slug: str, request: Request):
    """Admin: force a stale-vector scan + reembed enqueue for a single project.

    Bypasses the 6h cron cadence — useful right after a fresh train or new
    knowledge insert so vectors are searchable in seconds instead of hours.
    Requires admin role on the project.
    """
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        check_project_permission(user, slug, required_role="admin")
    except Exception as _e:
        raise HTTPException(403, f"admin permission required: {_e}")

    try:
        from dash.cron.reembed_stale import enqueue_project_for_reembed
        queued = await enqueue_project_for_reembed(slug)
        return {"status": "ok", "project_slug": slug, "queued": queued}
    except Exception as e:
        raise HTTPException(500, f"reembed enqueue failed: {e}")


@router.post("/{slug}/archive")
def archive_project(slug: str, request: Request):
    """Archive a project (soft hide). Adds 'archived' marker to agent_role suffix or status table.

    Implementation: bootstraps a 'status' column on dash_projects (NULL = active, 'archived' = hidden)
    and toggles it. Idempotent column add.
    """
    user = _get_user(request)
    with _engine.begin() as conn:
        # Idempotent column add
        conn.execute(text("ALTER TABLE public.dash_projects ADD COLUMN IF NOT EXISTS status TEXT"))
        r = conn.execute(text(
            "UPDATE public.dash_projects SET status='archived', updated_at=NOW() "
            "WHERE slug=:s AND user_id=:uid RETURNING id"
        ), {"s": slug, "uid": user["user_id"]}).fetchone()
        if not r:
            raise HTTPException(404, "Project not found or not owned by you")

    from app.auth import log_action
    log_action(user, "archive_project", "project", slug, "")
    return {"status": "ok"}


@router.post("/{slug}/unarchive")
def unarchive_project(slug: str, request: Request):
    user = _get_user(request)
    with _engine.begin() as conn:
        conn.execute(text("ALTER TABLE public.dash_projects ADD COLUMN IF NOT EXISTS status TEXT"))
        r = conn.execute(text(
            "UPDATE public.dash_projects SET status=NULL, updated_at=NOW() "
            "WHERE slug=:s AND user_id=:uid RETURNING id"
        ), {"s": slug, "uid": user["user_id"]}).fetchone()
        if not r:
            raise HTTPException(404, "Project not found")
    return {"status": "ok"}


@router.get("/{slug}")
def get_project(slug: str, request: Request):
    """Get project details."""
    user = _get_user(request)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, slug, name, agent_name, agent_role, agent_personality, schema_name, created_at, updated_at, user_id "
            "FROM public.dash_projects WHERE slug = :s"
        ), {"s": slug}).fetchone()

    if not row:
        raise HTTPException(404, "Project not found")

    # Check access via permission system (supports owner, shared users, super_admin)
    from app.auth import check_project_permission
    perm = check_project_permission(user, slug)
    if not perm:
        raise HTTPException(403, "No access to this project")

    user_role = perm["role"]  # "owner", "viewer", "editor", or "admin"

    return {
        "id": row[0], "slug": row[1], "name": row[2],
        "agent_name": row[3], "agent_role": row[4], "agent_personality": row[5],
        "schema_name": row[6],
        "created_at": str(row[7]) if row[7] else None,
        "updated_at": str(row[8]) if row[8] else None,
        "user_role": user_role,
    }


@router.delete("/{slug}")
def delete_project(slug: str, request: Request):
    """Delete a project, its schema, and all data."""
    guard_no_project_management("delete projects")
    from dash.utils.project_schemas import drop_all_project_schemas, drop_project_knowledge_tables
    from dash.utils.cascade import cascade_delete_project

    user = _get_user(request)

    # Step 1: read project row + perm check (short txn).
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, schema_name, user_id FROM public.dash_projects WHERE slug = :s"
        ), {"s": slug}).fetchone()

    if not row:
        raise HTTPException(404, "Project not found")

    # Only owner or admin can delete
    from app.auth import SUPER_ADMIN, check_project_permission
    if row[2] != user["user_id"] and user.get("username") != SUPER_ADMIN:
        perm = check_project_permission(user, slug, required_role="admin")
        if not perm:
            raise HTTPException(403, "Admin access required to delete project")

    schema = row[1]

    # Step 2: cleanup in AUTOCOMMIT mode — one statement per DELETE so a
    # FK violation on table N doesn't abort the txn and block tables N+1..
    # InFailedSqlTransaction class bug. — 2026-05-25 fix.
    #
    # Day 3 error-proofing: schema-variant + knowledge-table drops live in
    # `dash.utils.project_schemas`; the cascade-DELETE list is now
    # auto-discovered from `information_schema` via `dash.utils.cascade`.
    autocommit_engine = _engine.execution_options(isolation_level="AUTOCOMMIT")
    with autocommit_engine.connect() as conn:
        # Drop all project schema variants (canonical + user_<slug> orphan + future)
        drop_all_project_schemas(conn, schema)

        # Drop Agno PgVector knowledge tables for every variant
        drop_project_knowledge_tables(conn, schema)

        # Cascade-DELETE every public.dash_* table with a project_slug column.
        # Auto-discovered from information_schema — never hand-edit a list.
        cascade_delete_project(conn, slug)

        # Delete project record
        try:
            conn.execute(text("DELETE FROM public.dash_projects WHERE slug = :s"), {"s": slug})
        except Exception:
            pass
        # No conn.commit() — running in AUTOCOMMIT mode now

    # Clean up files on disk
    import shutil
    from dash.paths import KNOWLEDGE_DIR
    project_dir = KNOWLEDGE_DIR / slug
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)

    from app.auth import log_action
    log_action(user, "delete_project", "project", slug)
    return {"status": "ok", "deleted": slug}


@router.post("/{slug}/favorite")
def toggle_favorite(slug: str, request: Request):
    """Toggle favorite on a project."""
    user = _get_user(request)
    with _engine.connect() as conn:
        row = conn.execute(text("SELECT is_favorite FROM public.dash_projects WHERE slug = :s AND user_id = :uid"), {"s": slug, "uid": user["user_id"]}).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        new_val = not (row[0] or False)
        conn.execute(text("UPDATE public.dash_projects SET is_favorite = :f WHERE slug = :s"), {"f": new_val, "s": slug})
        conn.commit()
    return {"status": "ok", "is_favorite": new_val}


@router.post("/{slug}/share")
def share_project(slug: str, username: str, request: Request, role: str = "viewer"):
    """Share a project with another user with a specific role (viewer/editor/admin)."""
    user = _get_user(request)
    if role not in ("viewer", "editor", "admin"):
        role = "viewer"
    with _engine.connect() as conn:
        proj = conn.execute(text("SELECT id, user_id FROM public.dash_projects WHERE slug = :s"), {"s": slug}).fetchone()
        if not proj:
            raise HTTPException(404, "Project not found")
        # Only owner or admin can share
        from app.auth import SUPER_ADMIN, check_project_permission
        if proj[1] != user["user_id"] and user.get("username") != SUPER_ADMIN:
            perm = check_project_permission(user, slug, required_role="admin")
            if not perm:
                raise HTTPException(403, "Admin access required to share project")
        target = conn.execute(text("SELECT id FROM public.dash_users WHERE username = :u"), {"u": username}).fetchone()
        if not target:
            raise HTTPException(404, f"User '{username}' not found")
        try:
            conn.execute(text(
                "INSERT INTO public.dash_project_shares (project_id, shared_with_user_id, shared_by, role) VALUES (:pid, :uid, :by, :role)"
            ), {"pid": proj[0], "uid": target[0], "by": user["username"], "role": role})
            conn.commit()
        except Exception:
            # Update role if already shared
            conn.execute(text(
                "UPDATE public.dash_project_shares SET role = :role WHERE project_id = :pid AND shared_with_user_id = :uid"
            ), {"role": role, "pid": proj[0], "uid": target[0]})
            conn.commit()
            return {"status": "role_updated"}

    # Log + notify
    from app.auth import log_action, notify_user
    log_action(user, "share_project", "project", slug, f"shared with {username} as {role}")
    notify_user(target[0], f"Project shared with you", f"{user['username']} shared '{slug}' with you as {role}", "share")

    return {"status": "ok"}


@router.delete("/{slug}/share/{username}")
def unshare_project(slug: str, username: str, request: Request):
    """Remove sharing for a user."""
    user = _get_user(request)
    with _engine.connect() as conn:
        proj = conn.execute(text("SELECT id FROM public.dash_projects WHERE slug = :s"), {"s": slug}).fetchone()
        if not proj:
            raise HTTPException(404)
        target = conn.execute(text("SELECT id FROM public.dash_users WHERE username = :u"), {"u": username}).fetchone()
        if target:
            conn.execute(text("DELETE FROM public.dash_project_shares WHERE project_id = :pid AND shared_with_user_id = :uid"), {"pid": proj[0], "uid": target[0]})
            conn.commit()
    return {"status": "ok"}


@router.get("/{slug}/export")
def export_project(slug: str, request: Request):
    """Export entire project as a ZIP file (data CSVs + knowledge files + config)."""
    import io
    import zipfile
    import pandas as pd
    from fastapi.responses import StreamingResponse

    user = _get_user(request)
    proj = get_project(slug, request)
    schema = proj["schema_name"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Project config
        import json
        config = {
            "slug": slug, "name": proj["name"], "agent_name": proj["agent_name"],
            "agent_role": proj.get("agent_role", ""), "agent_personality": proj.get("agent_personality", "friendly"),
        }
        zf.writestr("config.json", json.dumps(config, indent=2))

        # Table data as CSVs
        insp = inspect(_engine)
        try:
            # Cap rows per table — a full SELECT * on balance_stock (100k+ rows)
            # loaded into a pandas DataFrame then a CSV string in memory can OOM
            # the worker, especially with concurrent exports. Stream in chunks and
            # cap total rows; flag truncation in the filename.
            import os as _os_exp
            _EXPORT_ROW_CAP = int(_os_exp.getenv("EXPORT_ROW_CAP", "200000"))
            _CHUNK = 50000
            for tbl in insp.get_table_names(schema=schema):
                try:
                    parts: list[str] = []
                    rows_done = 0
                    truncated = False
                    header = True
                    for chunk in pd.read_sql(
                        f'SELECT * FROM "{schema}"."{tbl}" LIMIT {_EXPORT_ROW_CAP + 1}',
                        _engine, chunksize=_CHUNK,
                    ):
                        if rows_done >= _EXPORT_ROW_CAP:
                            truncated = True
                            break
                        if rows_done + len(chunk) > _EXPORT_ROW_CAP:
                            chunk = chunk.iloc[: _EXPORT_ROW_CAP - rows_done]
                            truncated = True
                        parts.append(chunk.to_csv(index=False, header=header))
                        header = False
                        rows_done += len(chunk)
                        if truncated:
                            break
                    fname = f"data/{tbl}{'_TRUNCATED' if truncated else ''}.csv"
                    zf.writestr(fname, "".join(parts))
                except Exception:
                    pass
        except Exception:
            pass

        # Knowledge files
        from dash.paths import KNOWLEDGE_DIR
        proj_dir = KNOWLEDGE_DIR / slug
        if proj_dir.exists():
            for root_path in proj_dir.rglob("*"):
                if root_path.is_file() and not root_path.name.startswith("."):
                    arcname = f"knowledge/{root_path.relative_to(proj_dir)}"
                    zf.write(root_path, arcname)

        # Dashboards
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT name, widgets FROM public.dash_dashboards WHERE project_slug = :s"
            ), {"s": slug}).fetchall()
            for i, r in enumerate(rows):
                zf.writestr(f"dashboards/dashboard_{i}.json", json.dumps({"name": r[0], "widgets": r[1]}, default=str))

    buf.seek(0)
    from app.auth import log_action
    log_action(user, "export_project", "project", slug)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'})


@router.get("/{slug}/shared-users")
def list_shared_users(slug: str, request: Request):
    """List users this project is shared with."""
    user = _get_user(request)
    # Verify project ownership or admin
    with _engine.connect() as conn:
        proj = conn.execute(text("SELECT id, user_id FROM public.dash_projects WHERE slug = :s"), {"s": slug}).fetchone()
        if not proj:
            raise HTTPException(404, "Project not found")
        from app.auth import SUPER_ADMIN
        if proj[1] != user["user_id"] and user.get("username") != SUPER_ADMIN:
            raise HTTPException(403, "Not your project")
    with _engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT u.username, s.shared_by, s.role, s.created_at
            FROM public.dash_project_shares s
            JOIN public.dash_users u ON u.id = s.shared_with_user_id
            JOIN public.dash_projects p ON p.id = s.project_id
            WHERE p.slug = :slug
            ORDER BY s.created_at DESC
        """), {"slug": slug}).fetchall()

    users = [{"username": r[0], "shared_by": r[1], "role": r[2] or "viewer", "created_at": str(r[3]) if r[3] else None} for r in rows]
    return {"users": users}


@router.get("/{slug}/detail")
def project_detail(slug: str, request: Request):
    """Full project detail: tables, knowledge files, learnings, docs, config."""
    user = _get_user(request)
    proj = get_project(slug, request)
    schema = proj["schema_name"]

    insp = inspect(_engine)
    # Load source metadata for tables (saved during upload)
    from dash.paths import KNOWLEDGE_DIR as _KD
    _source_meta = {}
    _source_dir = _KD / slug / "table_sources"
    if _source_dir.exists():
        for sf in _source_dir.iterdir():
            if sf.suffix == ".json":
                try:
                    import json as _json
                    _source_meta[sf.stem] = _json.loads(sf.read_text())
                except Exception:
                    pass

    # Build connector-source map: synced table -> (dialect, host:port/db)
    _connector_map: dict = {}
    try:
        import json as _json
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT source_type, config, sync_state FROM public.dash_data_sources "
                "WHERE project_slug = :slug AND status != 'deleted' "
                "AND source_type IN ('postgresql', 'mysql')"
            ), {"slug": slug}).fetchall()
        for stype, cfg_raw, ss_raw in rows:
            cfg = cfg_raw if isinstance(cfg_raw, dict) else (_json.loads(cfg_raw) if cfg_raw else {})
            ss = ss_raw if isinstance(ss_raw, dict) else (_json.loads(ss_raw) if ss_raw else {})
            host = cfg.get("host", ""); port = cfg.get("port", ""); db = cfg.get("database", "")
            label = f"{host}:{port}/{db}" if host else stype
            for tname in (ss.get("tables") or {}).keys():
                _connector_map[tname] = {"dialect": stype, "label": label}
    except Exception:
        pass

    # Tables
    tables_list = []
    try:
        for t in insp.get_table_names(schema=schema):
            try:
                with _engine.connect() as conn:
                    count = conn.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{t}"')).scalar() or 0
                cols = insp.get_columns(t, schema=schema)
                src = _source_meta.get(t, {})
                # Load profile for real health %
                _profile = {}
                try:
                    _pf = _source_dir.parent / "table_sources" / f"{t}_profile.json"
                    if _pf.exists():
                        _profile = _json.loads(_pf.read_text())
                except Exception:
                    pass
                _conn = _connector_map.get(t)
                tables_list.append({
                    "name": t, "rows": count, "columns": len(cols),
                    "source_file": src.get("source_file", ""),
                    "source_detail": src.get("source_detail", ""),
                    "description": src.get("description", ""),
                    "health": _profile.get("health", 0),
                    "alerts": _profile.get("alerts", [])[:5],
                    "duplicate_rows": _profile.get("duplicate_rows", 0),
                    "connector_dialect": (_conn or {}).get("dialect", ""),
                    "connector_label": (_conn or {}).get("label", ""),
                    "origin": ("connector" if _conn else ("upload" if src.get("source_file") else "")),
                })
            except Exception:
                tables_list.append({"name": t, "rows": 0, "columns": 0, "source_file": "", "source_detail": "", "description": "", "connector_dialect": "", "connector_label": "", "origin": ""})
    except Exception:
        pass

    # Knowledge files
    from dash.paths import KNOWLEDGE_DIR
    knowledge_files = []
    proj_dir = KNOWLEDGE_DIR / slug
    if proj_dir.exists():
        for subdir in ["tables", "queries", "business"]:
            path = proj_dir / subdir
            if path.exists():
                for f in sorted(path.iterdir()):
                    if f.is_file() and not f.name.startswith("."):
                        knowledge_files.append({"name": f.name, "type": subdir, "size": f.stat().st_size})

    # Docs
    docs = []
    docs_dir = KNOWLEDGE_DIR / slug / "docs"
    if docs_dir.exists():
        for f in sorted(docs_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                docs.append({"name": f.name, "size": f.stat().st_size, "type": f.suffix})

    # Learnings count
    learnings = 0
    try:
        with _engine.connect() as conn:
            learnings = conn.execute(text(f'SELECT COUNT(*) FROM ai."{schema}_learnings"')).scalar() or 0
    except Exception:
        pass

    # Knowledge vectors
    vectors = 0
    try:
        with _engine.connect() as conn:
            vectors = conn.execute(text(f'SELECT COUNT(*) FROM ai."{schema}_knowledge"')).scalar() or 0
    except Exception:
        pass

    return {
        "project": proj,
        "tables": tables_list,
        "knowledge_files": knowledge_files,
        "knowledge_vectors": vectors,
        "learnings": learnings,
        "docs": docs,
    }


@router.get("/{slug}/stats")
def project_stats(slug: str, request: Request):
    """Get project stats: tables, rows, knowledge vectors."""
    user = _get_user(request)
    proj = get_project(slug, request)
    schema = proj["schema_name"]

    insp = inspect(_engine)
    tables_list: list[dict] = []
    total_rows = 0

    try:
        tbl_names = insp.get_table_names(schema=schema)
        with _engine.connect() as conn:
            for t in tbl_names:
                try:
                    count = conn.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{t}"')).scalar() or 0
                    cols = insp.get_columns(t, schema=schema)
                    tables_list.append({"name": t, "rows": count, "columns": len(cols)})
                    total_rows += count
                except Exception:
                    tables_list.append({"name": t, "rows": 0, "columns": 0})
    except Exception:
        pass

    # Knowledge vectors
    knowledge_count = 0
    try:
        with _engine.connect() as conn:
            knowledge_count = conn.execute(text(f'SELECT COUNT(*) FROM ai."{schema}_knowledge"')).scalar() or 0
    except Exception:
        pass

    return {
        "tables": tables_list,
        "total_rows": total_rows,
        "knowledge_vectors": knowledge_count,
        "schema_name": schema,
    }


@router.get("/{slug}/chats")
def project_chats(slug: str, request: Request, limit: int = 5):
    """Recent chat sessions for Cockpit. Joins quality_scores for judge rating."""
    _get_user(request)
    get_project(slug, request)
    out: list[dict] = []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT c.session_id, c.first_message, c.updated_at, "
                "       (SELECT AVG(q.score) FROM public.dash_quality_scores q WHERE q.session_id = c.session_id) AS avg_score "
                "FROM public.dash_chat_sessions c "
                "WHERE c.project_slug=:s "
                "ORDER BY c.updated_at DESC LIMIT :l"
            ), {"s": slug, "l": int(limit)}).mappings().all()
            for r in rows:
                out.append({
                    "session_id": r["session_id"],
                    "first_message": (r["first_message"] or "")[:80],
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                    "score": round(float(r["avg_score"]), 1) if r["avg_score"] is not None else None,
                })
    except Exception:
        pass
    return {"chats": out}


@router.get("/{slug}/activity")
def project_activity(slug: str, request: Request, limit: int = 8):
    """Recent activity events: training runs + uploads + drift. Used by Cockpit."""
    _get_user(request)
    get_project(slug, request)
    events: list[dict] = []
    try:
        with _engine.connect() as conn:
            try:
                rows = conn.execute(text(
                    "SELECT finished_at AS ts, status, COALESCE(steps_done,0) AS d, COALESCE(steps_total,0) AS t "
                    "FROM public.dash_training_runs WHERE project_slug=:s AND finished_at IS NOT NULL "
                    "ORDER BY finished_at DESC LIMIT :l"
                ), {"s": slug, "l": int(limit)}).mappings().all()
                for r in rows:
                    events.append({
                        "ts": (r["ts"].isoformat() if r["ts"] else None),
                        "kind": "pipeline",
                        "message": f"training {r['status']} · {r['d']}/{r['t']} steps",
                    })
            except Exception:
                pass
            try:
                drift_rows = conn.execute(text(
                    "SELECT created_at AS ts, table_name, severity FROM public.dash_drift_alerts "
                    "WHERE project_slug=:s ORDER BY created_at DESC LIMIT :l"
                ), {"s": slug, "l": int(limit)}).mappings().all()
                for r in drift_rows:
                    events.append({
                        "ts": (r["ts"].isoformat() if r["ts"] else None),
                        "kind": "drift",
                        "message": f"drift {r['severity']} on {r['table_name']}",
                    })
            except Exception:
                pass
    except Exception:
        pass
    events = [e for e in events if e["ts"]]
    events.sort(key=lambda e: e["ts"], reverse=True)
    return {"events": events[:int(limit)]}


@router.get("/{slug}/activity-metrics")
def project_activity_metrics(slug: str, request: Request):
    """Cockpit: counts of feedback (good/bad), insights, drift alerts, evolutions."""
    _get_user(request)
    get_project(slug, request)
    out = {"good_feedback": 0, "bad_feedback": 0, "insights": 0, "drift_alerts": 0, "evolutions": 0}
    try:
        with _engine.connect() as conn:
            try:
                rows = conn.execute(text(
                    "SELECT rating, COUNT(*) AS c FROM public.dash_feedback "
                    "WHERE project_slug=:s GROUP BY rating"
                ), {"s": slug}).mappings().all()
                for r in rows:
                    rating = (r["rating"] or "").lower()
                    if rating in ("up", "good", "positive", "1") or rating.startswith("good"):
                        out["good_feedback"] += int(r["c"] or 0)
                    elif rating in ("down", "bad", "negative", "-1") or rating.startswith("bad"):
                        out["bad_feedback"] += int(r["c"] or 0)
            except Exception:
                pass
            try:
                out["insights"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_proactive_insights WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                out["drift_alerts"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_drift_alerts WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                out["evolutions"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_evolved_instructions WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
    except Exception:
        pass
    return out


@router.get("/{slug}/tab-completion")
def project_tab_completion(slug: str, request: Request):
    """Cockpit: per-tab item counts for completion indicators."""
    _get_user(request)
    proj = get_project(slug, request)
    schema = proj.get("schema_name") or slug
    out = {
        "datasets": 0, "knowledge": 0, "rules": 0, "training": 0, "docs": 0,
        "queries": 0, "lineage": 0, "agents": 41, "workflows": 0,
        "schedules": 0, "evals": 0, "users": 0,
    }
    try:
        with _engine.connect() as conn:
            try:
                out["datasets"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=:s"
                ), {"s": schema}).scalar() or 0)
            except Exception:
                pass
            try:
                out["knowledge"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_documents WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                out["rules"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_rules_db WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                out["training"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_training_qa WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                out["docs"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_documents WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                out["queries"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_query_patterns WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                out["lineage"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_relationships WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            wf = 0
            try:
                wf += int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_workflows_db WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                wf += int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_autonomous_workflows WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            out["workflows"] = wf
            try:
                out["schedules"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_schedules WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                out["evals"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_evals WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
            try:
                out["users"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_project_shares WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                pass
    except Exception:
        pass
    return out


@router.get("/{slug}/training-pipeline")
def project_training_pipeline(slug: str, request: Request):
    """Cockpit: most recent training run — steps, duration, status."""
    _get_user(request)
    get_project(slug, request)
    step_names = ["analysis", "qa", "persona", "workflows", "relationships", "knowledge_index"]
    out = {
        "steps": [{"name": n, "status": "pending"} for n in step_names],
        "last_trained_at": None,
        "duration_sec": 0.0,
        "tables_count": 0,
        "status": "pending",
    }
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT status, steps, tables_trained, started_at, finished_at "
                "FROM public.dash_training_runs WHERE project_slug=:s "
                "ORDER BY started_at DESC LIMIT 1"
            ), {"s": slug}).mappings().first()
            if row:
                status = (row["status"] or "").lower()
                out["status"] = status or "pending"
                out["tables_count"] = int(row["tables_trained"] or 0)
                if row["finished_at"]:
                    out["last_trained_at"] = row["finished_at"].isoformat()
                if row["started_at"] and row["finished_at"]:
                    try:
                        out["duration_sec"] = float((row["finished_at"] - row["started_at"]).total_seconds())
                    except Exception:
                        out["duration_sec"] = 0.0
                # Parse steps field format: "step_name|table|idx|total" — extract current step
                steps_done_set = set()
                current_step = None
                steps_raw = row["steps"] or ""
                try:
                    parts = steps_raw.split("|")
                    if parts and parts[0]:
                        current_step = parts[0].strip().lower()
                    if len(parts) >= 4:
                        try:
                            idx = int(parts[2])
                            total = int(parts[3])
                            if total > 0:
                                # Mark proportional steps done
                                pct = idx / total
                                done_count = int(pct * len(step_names))
                                for i in range(done_count):
                                    steps_done_set.add(step_names[i])
                        except Exception:
                            pass
                except Exception:
                    pass
                # If run done, mark all done
                if status == "done" or status == "complete" or status == "completed":
                    steps_done_set = set(step_names)
                steps_out = []
                for n in step_names:
                    if n in steps_done_set:
                        st = "done"
                    elif current_step and n == current_step and status == "running":
                        st = "running"
                    elif status == "failed":
                        st = "pending"
                    else:
                        st = "pending"
                    steps_out.append({"name": n, "status": st})
                out["steps"] = steps_out
    except Exception:
        pass
    return out


@router.get("/{slug}/skills-summary")
def project_skills_summary(slug: str, request: Request):
    """Cockpit: skill library count + top entry. Returns zeros if table missing."""
    _get_user(request)
    get_project(slug, request)
    out = {"count": 0, "top_skill": None, "last_promoted_at": None}
    try:
        with _engine.connect() as conn:
            try:
                out["count"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_skill_library WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                return out
            try:
                row = conn.execute(text(
                    "SELECT name, created_at FROM public.dash_skill_library "
                    "WHERE project_slug=:s ORDER BY COALESCE(success_count,0) DESC, created_at DESC LIMIT 1"
                ), {"s": slug}).mappings().first()
                if row:
                    out["top_skill"] = row.get("name")
                    if row.get("created_at"):
                        out["last_promoted_at"] = row["created_at"].isoformat()
            except Exception:
                pass
    except Exception:
        pass
    return out


@router.get("/{slug}/anti-patterns-summary")
def project_anti_patterns_summary(slug: str, request: Request):
    """Cockpit: anti-patterns count + top hit. Returns zeros if table missing."""
    _get_user(request)
    get_project(slug, request)
    out = {"count": 0, "top_pattern": None, "last_flagged_at": None}
    try:
        with _engine.connect() as conn:
            try:
                out["count"] = int(conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_anti_patterns WHERE project_slug=:s"
                ), {"s": slug}).scalar() or 0)
            except Exception:
                return out
            try:
                row = conn.execute(text(
                    "SELECT pattern, created_at FROM public.dash_anti_patterns "
                    "WHERE project_slug=:s ORDER BY COALESCE(hit_count,0) DESC, created_at DESC LIMIT 1"
                ), {"s": slug}).mappings().first()
                if row:
                    out["top_pattern"] = row.get("pattern")
                    if row.get("created_at"):
                        out["last_flagged_at"] = row["created_at"].isoformat()
            except Exception:
                pass
    except Exception:
        pass
    return out


@router.get("/{slug}/dream-summary")
def project_dream_summary(slug: str, request: Request):
    """Cockpit: Dream Reflection cycle summary. Returns defaults if tables missing."""
    _get_user(request)
    get_project(slug, request)
    out = {
        "enabled": False, "last_cycle_at": None, "findings": 0,
        "anti_patterns": 0, "skills_added": 0, "persona_deltas": 0,
    }
    try:
        with _engine.connect() as conn:
            try:
                row = conn.execute(text(
                    "SELECT created_at, status FROM public.dash_dream_runs "
                    "WHERE project_slug=:s ORDER BY created_at DESC LIMIT 1"
                ), {"s": slug}).mappings().first()
                if row:
                    out["enabled"] = True
                    if row.get("created_at"):
                        out["last_cycle_at"] = row["created_at"].isoformat()
            except Exception:
                return out
            try:
                rows = conn.execute(text(
                    "SELECT finding_type, COUNT(*) AS c FROM public.dash_dream_findings "
                    "WHERE project_slug=:s GROUP BY finding_type"
                ), {"s": slug}).mappings().all()
                total = 0
                for r in rows:
                    c = int(r["c"] or 0)
                    total += c
                    ft = (r["finding_type"] or "").lower()
                    if ft == "anti_pattern":
                        out["anti_patterns"] = c
                    elif ft in ("skill_patch_candidate", "skill"):
                        out["skills_added"] = c
                    elif ft in ("user_persona_delta", "persona_delta"):
                        out["persona_deltas"] = c
                out["findings"] = total
            except Exception:
                pass
    except Exception:
        pass
    return out


@router.get("/{slug}/cost-summary")
def project_cost_summary(slug: str, request: Request):
    """Per-project LLM cost rollup: today, this month, current cap."""
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug):
        raise HTTPException(403, "No access to this project")
    today_usd = 0.0
    month_usd = 0.0
    cap_usd_day = 0.0
    paused_until = None
    try:
        with _engine.connect() as conn:
            r = conn.execute(text(
                "SELECT COALESCE(daily_cost_cap_usd, 0), cost_paused_until "
                "FROM public.dash_projects WHERE slug = :s"
            ), {"s": slug}).fetchone()
            if r:
                cap_usd_day = float(r[0] or 0)
                paused_until = r[1].isoformat() if r[1] and hasattr(r[1], "isoformat") else (str(r[1]) if r[1] else None)
            r2 = conn.execute(text(
                "SELECT "
                "  COALESCE(SUM(CASE WHEN ts >= DATE_TRUNC('day',   NOW() AT TIME ZONE 'UTC') THEN cost_usd ELSE 0 END), 0), "
                "  COALESCE(SUM(CASE WHEN ts >= DATE_TRUNC('month', NOW() AT TIME ZONE 'UTC') THEN cost_usd ELSE 0 END), 0)  "
                "FROM public.dash_llm_costs WHERE project_slug = :s"
            ), {"s": slug}).fetchone()
            if r2:
                today_usd = float(r2[0] or 0)
                month_usd = float(r2[1] or 0)
            # Fold in legacy self-learning runs so existing data still counts.
            r3 = conn.execute(text(
                "SELECT "
                "  COALESCE(SUM(CASE WHEN started_at >= DATE_TRUNC('day',   NOW() AT TIME ZONE 'UTC') THEN cost_usd ELSE 0 END), 0), "
                "  COALESCE(SUM(CASE WHEN started_at >= DATE_TRUNC('month', NOW() AT TIME ZONE 'UTC') THEN cost_usd ELSE 0 END), 0)  "
                "FROM public.dash_self_learning_runs WHERE project_slug = :s"
            ), {"s": slug}).fetchone()
            if r3:
                today_usd += float(r3[0] or 0)
                month_usd += float(r3[1] or 0)
    except Exception as e:
        raise HTTPException(500, f"cost summary failed: {e}")
    pct = (today_usd / cap_usd_day * 100.0) if cap_usd_day > 0 else 0.0
    over = bool(cap_usd_day > 0 and today_usd >= cap_usd_day)
    return {
        "slug": slug,
        "today_usd": round(today_usd, 6),
        "month_usd": round(month_usd, 6),
        "cap_usd_day": round(cap_usd_day, 4),
        "pct_used": round(min(pct, 100.0), 2),
        "over_budget": over,
        "paused_until": paused_until,
    }


@router.post("/{slug}/cost-cap")
async def project_set_cost_cap(slug: str, request: Request):
    """Update per-project daily LLM cost cap (USD/day). 0 or null = unlimited."""
    user = _get_user(request)
    from app.auth import check_project_permission
    perm = check_project_permission(user, slug)
    if not perm:
        raise HTTPException(403, "No access to this project")
    if perm.get("role") not in ("owner", "editor", "admin"):
        raise HTTPException(403, "Editor role required")
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = (body or {}).get("cap_usd_day", body.get("daily_cost_cap_usd", 0))
    try:
        cap = float(raw or 0)
    except Exception:
        raise HTTPException(400, "cap_usd_day must be a number")
    if cap < 0:
        cap = 0.0
    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_projects SET daily_cost_cap_usd = :c WHERE slug = :s"
        ), {"c": cap, "s": slug})
        conn.commit()
    # Invalidate cached budget so the gate sees the new cap immediately.
    try:
        from dash.settings import _invalidate_budget_cache
        _invalidate_budget_cache(slug)
    except Exception:
        pass
    return {"slug": slug, "cap_usd_day": cap}


@router.get("/{slug}/drift-events")
def project_drift_events(
    slug: str,
    request: Request,
    source_id: int | None = None,
    severity: str | None = None,
    status: str | None = None,
    limit: int = 50,
    since: str | None = None,
):
    """List drift events for a project (read-only).

    Optional filters: source_id, severity (low|med|high|critical),
    status (open|acknowledged|dismissed|retrain_triggered),
    since (ISO timestamp), limit (default 50, max 500).
    Each item: {id, ts, source_id, source_label, table, drift_type,
    severity, summary, raw_diff, status, column}.
    """
    user = _get_user(request)
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, required_role="viewer"):
        raise HTTPException(403)

    limit = max(1, min(int(limit or 50), 500))
    params: dict[str, Any] = {"slug": slug, "n": limit}
    sql = (
        "SELECT e.id, e.detected_at, e.source_id, "
        "       COALESCE(s.site_name, s.source_type, ''),"
        "       e.table_name, e.drift_type, e.severity, "
        "       e.details, e.status, e.column_name "
        "  FROM public.dash_drift_events e "
        "  LEFT JOIN public.dash_data_sources s ON s.id = e.source_id "
        " WHERE e.project_slug = :slug "
    )
    if source_id is not None:
        sql += "AND e.source_id = :sid "
        params["sid"] = int(source_id)
    if severity:
        sql += "AND e.severity = :sev "
        params["sev"] = severity
    if status:
        sql += "AND e.status = :st "
        params["st"] = status
    if since:
        sql += "AND e.detected_at >= :since "
        params["since"] = since
    sql += "ORDER BY e.detected_at DESC LIMIT :n"

    events: list[dict] = []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        for r in rows:
            details = r[7] or {}
            # Build a short, human summary string
            summary = ""
            if isinstance(details, dict):
                summary = (
                    details.get("summary")
                    or details.get("message")
                    or details.get("note")
                    or ""
                )
                if not summary:
                    # Synthesize one from common keys
                    bits = []
                    if "before" in details and "after" in details:
                        bits.append(f"{details.get('before')} → {details.get('after')}")
                    if "delta_pct" in details:
                        bits.append(f"Δ {details.get('delta_pct')}%")
                    if "added" in details:
                        bits.append(f"+{len(details.get('added') or [])} added")
                    if "removed" in details:
                        bits.append(f"-{len(details.get('removed') or [])} removed")
                    summary = " · ".join(bits)
            events.append({
                "id": r[0],
                "ts": r[1].isoformat() if r[1] else None,
                "source_id": r[2],
                "source_label": r[3] or (f"source #{r[2]}" if r[2] else "—"),
                "table": r[4],
                "drift_type": r[5],
                "severity": r[6],
                "summary": summary,
                "raw_diff": details if isinstance(details, dict) else {},
                "status": r[8],
                "column": r[9],
            })
    except Exception as e:
        raise HTTPException(500, f"drift events query failed: {str(e)[:200]}")

    return {
        "project_slug": slug,
        "count": len(events),
        "events": events,
        "filters": {
            "source_id": source_id,
            "severity": severity,
            "status": status,
            "since": since,
            "limit": limit,
        },
    }


@router.post("/{slug}/onboard-industry")
async def onboard_industry(slug: str, request: Request):
    """Onboarding wizard endpoint — applies industry template + seeds scopes/role.

    Body: {template_name, seed_scopes?: [{scope_id, scope_label, role}],
           default_role?: str}
    Auth: project creator/owner or super admin only.
    """
    user = _get_user(request)
    body = await request.json() or {}
    template_name = (body.get("template_name") or "").strip()
    if not template_name:
        raise HTTPException(400, "template_name required")

    # Auth: project creator/owner only (or super admin)
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT user_id FROM public.dash_projects WHERE slug = :s"
        ), {"s": slug}).fetchone()
    if not row:
        raise HTTPException(404, "project not found")
    from app.auth import SUPER_ADMIN
    is_super = (user.get("username") == SUPER_ADMIN)
    if int(row[0]) != int(user.get("user_id")) and not is_super:
        raise HTTPException(403, "only project creator can onboard")

    try:
        from dash.policy import VisibilityPolicy, save_policy
        from dash.policy.templates import get_template
        from dash.policy.roles import upsert_role, assign_user_role
    except Exception as e:
        raise HTTPException(503, f"policy module unavailable: {e}")

    tmpl = get_template(template_name)
    if not tmpl:
        raise HTTPException(404, f"template '{template_name}' not found")

    # Apply template policy (replace mode for fresh project)
    try:
        validated = VisibilityPolicy(**tmpl["policy"])
    except Exception as e:
        raise HTTPException(400, f"template invalid: {e}")
    new_version = save_policy(slug, validated, user_id=user.get("user_id"))

    roles_seeded = 0
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

    # Seed scopes (idempotent via ON CONFLICT)
    seed_scopes = body.get("seed_scopes") or []
    scopes_seeded = 0
    if seed_scopes:
        try:
            with _engine.connect() as conn:
                for s in seed_scopes:
                    sid = (s.get("scope_id") or "").strip()
                    slab = (s.get("scope_label") or "").strip()
                    role = (s.get("role") or "staff").strip()
                    if not sid or not slab:
                        continue
                    conn.execute(text(
                        "INSERT INTO public.dash_user_scopes "
                        "(user_id, project_slug, scope_id, scope_label, role, is_default) "
                        "VALUES (:uid, :p, :sid, :slab, :role, FALSE) "
                        "ON CONFLICT (user_id, project_slug, scope_id) DO UPDATE "
                        "SET scope_label = EXCLUDED.scope_label, role = EXCLUDED.role"
                    ), {"uid": user["user_id"], "p": slug, "sid": sid,
                        "slab": slab, "role": role})
                    scopes_seeded += 1
                conn.commit()
        except Exception as e:
            raise HTTPException(500, f"scope seed failed: {e}")

    # Optional default-role assignment
    default_role = (body.get("default_role") or "").strip()
    if default_role:
        try:
            assign_user_role(int(user["user_id"]), slug, default_role)
        except Exception:
            pass

    return {
        "policy": validated.model_dump(),
        "version": new_version,
        "scopes_seeded": scopes_seeded,
        "roles_seeded": roles_seeded,
        "scope_keyword": tmpl.get("scope_keyword"),
    }


# ---------------------------------------------------------------------------
# Vertical SKU bundles — preset + brain seed + workflows + sample data
# ---------------------------------------------------------------------------

@router.get("/verticals/list")
def list_verticals_endpoint(request: Request):
    """Return available vertical SKU bundles."""
    _get_user(request)
    from dash.verticals import list_verticals
    return {"verticals": list_verticals()}


@router.post("/{slug}/apply-vertical")
async def apply_vertical(slug: str, request: Request):
    """Apply a vertical SKU bundle to a project.

    Body: {vertical_name: str, also_seed_data?: bool}
    Returns: {brain_seeded, workflows_seeded, vertical_label, visibility_template_applied}
    Auth: project owner/admin.
    Idempotent: brain entries use ON CONFLICT DO NOTHING; workflows skipped if
    same (project, name, source) already present.
    """
    import json as _json
    user = _get_user(request)
    body = await request.json() or {}
    vertical_name = (body.get("vertical_name") or "").strip().lower()
    also_seed_data = bool(body.get("also_seed_data") or False)
    if not vertical_name:
        _keys = list(body.keys()) if isinstance(body, dict) else []
        raise HTTPException(
            400,
            "vertical_name required (got keys: "
            + ", ".join(_keys)
            + "). Hint: POST body must include 'vertical_name', e.g. "
            + "{\"vertical_name\": \"pharma\"}. List available via "
            + "GET /api/projects/verticals/list.",
        )

    from dash.verticals import get_vertical
    bundle = get_vertical(vertical_name)
    if not bundle:
        raise HTTPException(404, f"vertical '{vertical_name}' not found")

    # Auth: project owner or super admin
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT user_id FROM public.dash_projects WHERE slug = :s"
        ), {"s": slug}).fetchone()
    if not row:
        raise HTTPException(404, "project not found")
    from app.auth import SUPER_ADMIN, check_project_permission
    is_super = (user.get("username") == SUPER_ADMIN)
    is_owner = int(row[0]) == int(user.get("user_id"))
    if not (is_owner or is_super or check_project_permission(user, slug, 'admin')):
        raise HTTPException(403, "owner/admin required to apply vertical")

    source_tag = f"vertical_{vertical_name}"
    created_by = user.get("username") or "system"

    # 1. Apply visibility template if bundle declares one.
    visibility_template_applied = None
    vt = bundle.get("visibility_template")
    if vt:
        try:
            from dash.policy import VisibilityPolicy, save_policy
            from dash.policy.templates import get_template
            tmpl = get_template(vt)
            if tmpl:
                validated = VisibilityPolicy(**tmpl["policy"])
                save_policy(slug, validated, user_id=user.get("user_id"))
                visibility_template_applied = vt
        except Exception:
            pass

    # 2. Seed brain entries (idempotent via ON CONFLICT on (project_slug, name)).
    brain_entries = bundle.get("brain_entries", []) or []
    brain_seeded = 0
    if brain_entries:
        with _engine.connect() as conn:
            for entry in brain_entries:
                try:
                    res = conn.execute(text(
                        "INSERT INTO public.dash_company_brain "
                        "(category, name, definition, metadata, created_by, project_slug, user_id) "
                        "VALUES (:cat, :name, :def, CAST(:meta AS JSONB), :by, :slug, NULL) "
                        "ON CONFLICT DO NOTHING RETURNING id"
                    ), {
                        "cat": entry["category"],
                        "name": entry["name"],
                        "def": entry["value"],
                        "meta": _json.dumps({"source": source_tag}),
                        "by": created_by,
                        "slug": slug,
                    })
                    if res.fetchone() is not None:
                        brain_seeded += 1
                except Exception:
                    pass
            conn.commit()

    # 3. Seed workflows into the canonical autonomous workflows table
    # (dash.dash_autonomous_workflows). Verticals contribute autonomous
    # cron-driven workflows per docs/WORKFLOW_TABLES.md.
    # Idempotency: skip if (project_slug, template_name, name) already exists.
    # Steps (multi-step playbooks) are stashed in expected_columns jsonb so the
    # runner / UI can render them; cron runner ignores rows with NULL query_template.
    workflows = bundle.get("workflows", []) or []
    workflows_seeded = 0
    inserted_ids: list[int] = []
    if workflows:
        # Ensure the autonomous workflows table + indexes exist (bootstrap).
        try:
            from dash.templates.storage import bootstrap_tables as _bootstrap_tpl
            _bootstrap_tpl()
        except Exception:
            pass
        template_name = source_tag  # e.g. "vertical_pharma"
        with _engine.begin() as conn:
            for wf in workflows:
                wf_name = (wf.get("name") or "").strip()
                if not wf_name:
                    continue
                exists = conn.execute(text(
                    "SELECT 1 FROM dash.dash_autonomous_workflows "
                    "WHERE project_slug = :s AND template_name = :t AND name = :n LIMIT 1"
                ), {"s": slug, "t": template_name, "n": wf_name}).fetchone()
                if exists:
                    continue
                try:
                    res = conn.execute(text(
                        "INSERT INTO dash.dash_autonomous_workflows "
                        "(project_slug, template_name, name, description, schedule, "
                        " query_template, expected_entity, expected_columns, action, status) "
                        "VALUES (:s, :t, :n, :d, :sch, :q, :ee, CAST(:ec AS jsonb), :a, 'pending') "
                        "RETURNING id"
                    ), {
                        "s": slug,
                        "t": template_name,
                        "n": wf_name,
                        "d": wf.get("description") or "",
                        "sch": wf.get("schedule") or "daily",
                        "q": wf.get("query_template") or "",
                        "ee": wf.get("expected_entity") or "",
                        # Stash full step list inside expected_columns JSONB so it
                        # survives without a separate `steps` column.
                        "ec": _json.dumps({
                            "steps": wf.get("steps") or [],
                            "source": source_tag,
                        }),
                        "a": wf.get("action") or "post_insight",
                    })
                    new_id = res.scalar()
                    if new_id is not None:
                        inserted_ids.append(int(new_id))
                except Exception:
                    # fail-soft per workflow; never abort whole apply.
                    pass
        workflows_seeded = len(inserted_ids)

    # 4. Sample/synthetic data loading has been REMOVED from the product — a
    # vertical pack only seeds brain + workflows + visibility template (config),
    # never fake catalog/stock rows. The `also_seed_data` flag is ignored.
    sample_data_loaded = False

    return {
        "vertical_name": vertical_name,
        "vertical_label": bundle.get("label"),
        "brain_seeded": brain_seeded,
        "workflows_seeded": workflows_seeded,
        "visibility_template_applied": visibility_template_applied,
        "sample_data_loaded": sample_data_loaded,
    }


# ---------------------------------------------------------------------------
# Dream Reflection post-chat hook (poignancy capture + dream_lite trigger)
# ---------------------------------------------------------------------------

def _dream_poignancy_hook(
    slug: str,
    session_id: str,
    user: dict,
    question: str,
    answer: str,
    turn_id=None,
    tools_used=None,
) -> None:
    """Fire-and-forget poignancy capture + Σ-threshold dream_lite trigger.

    Wraps every step in try/except so a failure here NEVER breaks chat.
    Other agents own `dash_poignancy` / `dream_lite`; this only wires the call.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    has_error = not bool(answer) or "An error occurred" in (answer or "")
    try:
        from dash.learning.dream_poignancy import capture_turn  # type: ignore
        try:
            capture_turn(
                session_id=session_id or "",
                turn_id=turn_id,
                project_slug=slug,
                user_id=(user or {}).get("user_id") or (user or {}).get("id"),
                question=(question or "")[:500],
                response_summary=(answer or "")[:1000],
                tools_used=tools_used or [],
                succeeded=not has_error,
                judge_score=None,   # filled later by judge bg agent
                user_reaction=None, # filled later
            )
        except Exception as e:
            _log.debug(f"capture_turn failed for {slug}: {e}")
    except Exception:
        # dream_poignancy module not yet shipped — silently skip.
        return

    # Session poignancy Σ check → enqueue dream_lite minion at threshold.
    try:
        from dash.learning.dream_poignancy import session_poignancy_sum  # type: ignore
        try:
            total = session_poignancy_sum(session_id) if session_id else 0
        except Exception:
            total = 0
        if total and float(total) >= 30:
            try:
                from dash.minions import queue as _q  # type: ignore
                _q.enqueue(
                    project=slug,
                    kind="dream_lite",
                    payload={
                        "project": slug,
                        "session_id": session_id,
                        "user_id": (user or {}).get("user_id") or (user or {}).get("id"),
                        "trigger_reason": "poignancy_threshold",
                    },
                    priority=7,
                )
            except Exception as e:
                _log.debug(f"dream_lite enqueue failed for {slug}: {e}")
    except Exception:
        return


# ---------------------------------------------------------------------------
# Decision Log — saved SO_WHAT actions (McKinsey-style decision diary)
# ---------------------------------------------------------------------------

@router.post("/{slug}/decisions")
def create_decision(slug: str, payload: dict, request: Request):
    user = _get_user(request)
    action = (payload.get("action") or "").strip()
    if not action:
        raise HTTPException(400, "action is required")
    owner = (payload.get("owner") or "").strip() or None
    effort = (payload.get("effort") or "").strip() or None
    risk = (payload.get("risk") or "").strip() or None
    chat_msg_id = (payload.get("chat_msg_id") or None)
    source = (payload.get("source_content") or "")[:2000] or None
    try:
        with _engine.begin() as conn:
            row = conn.execute(text(
                """INSERT INTO public.dash_decisions
                    (project_slug, user_id, chat_msg_id, action, owner, effort, risk, source_excerpt)
                   VALUES (:s, :u, :c, :a, :o, :e, :r, :x)
                   RETURNING id, created_at"""
            ), {"s": slug, "u": user.get("id"), "c": chat_msg_id, "a": action,
                "o": owner, "e": effort, "r": risk, "x": source}).fetchone()
        return {"ok": True, "id": row[0], "created_at": row[1].isoformat() if row[1] else None}
    except Exception as e:
        raise HTTPException(500, f"Failed to save decision: {e}")


@router.get("/{slug}/decisions")
def list_decisions(slug: str, request: Request, status: str | None = None, limit: int = 50):
    _get_user(request)
    limit = max(1, min(200, limit))
    q = "SELECT id, action, owner, effort, risk, status, chat_msg_id, created_at FROM public.dash_decisions WHERE project_slug = :s"
    params: dict = {"s": slug, "lim": limit}
    if status:
        q += " AND status = :st"
        params["st"] = status
    q += " ORDER BY created_at DESC LIMIT :lim"
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(q), params).fetchall()
        return {"decisions": [
            {"id": r[0], "action": r[1], "owner": r[2], "effort": r[3], "risk": r[4],
             "status": r[5], "chat_msg_id": r[6], "created_at": r[7].isoformat() if r[7] else None}
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(500, f"Failed to list decisions: {e}")


@router.patch("/{slug}/decisions/{decision_id}")
def update_decision(slug: str, decision_id: int, payload: dict, request: Request):
    _get_user(request)
    new_status = (payload.get("status") or "").strip()
    if new_status not in ("open", "in_progress", "done", "dismissed"):
        raise HTTPException(400, "invalid status")
    try:
        with _engine.begin() as conn:
            conn.execute(text(
                "UPDATE public.dash_decisions SET status = :st, updated_at = now() "
                "WHERE id = :id AND project_slug = :s"
            ), {"st": new_status, "id": decision_id, "s": slug})
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Failed to update decision: {e}")


# ---------------------------------------------------------------------------
# Decisions v2 — extended schema (migration 148): decision_text, evidence (jsonb),
# owner_user_id, due_at, status ∈ pending|in_progress|done|cancelled,
# session_id, source_message_id.
#
# NOTE: Three legacy decision routes already exist earlier in this file (using
# the migration-091 schema: action / owner / chat_msg_id / source_excerpt /
# status='open'). The endpoints below are appended per spec; FastAPI dispatches
# to the first-registered handler for duplicate paths, so the single-detail GET
# and DELETE below are the actually-reachable new surfaces. POST/list/PATCH
# behavior continues to be served by the legacy handlers above for back-compat.
# ---------------------------------------------------------------------------

def _decisions_table_exists() -> bool:
    """Fail-soft check so endpoints can return empty rather than 500 when the
    migration hasn't been applied yet."""
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='dash_decisions' LIMIT 1"
            )).fetchone()
            return bool(row)
    except Exception:
        return False


def _decision_row_to_dict(r) -> dict:
    return {
        "id": r[0],
        "project_slug": r[1],
        "user_id": r[2],
        "session_id": r[3],
        "decision_text": r[4],
        "evidence": r[5] or {},
        "owner_user_id": r[6],
        "due_at": r[7].isoformat() if r[7] else None,
        "status": r[8],
        "source_message_id": r[9],
        "created_at": r[10].isoformat() if r[10] else None,
        "updated_at": r[11].isoformat() if r[11] else None,
    }


_DECISION_COLS = (
    "id, project_slug, user_id, session_id, decision_text, evidence, "
    "owner_user_id, due_at, status, source_message_id, created_at, updated_at"
)

_DECISION_STATUSES = ("pending", "in_progress", "done", "cancelled")


@router.post("/{slug}/decisions")
def create_decision_v2(slug: str, payload: dict, request: Request):
    """Create a saved decision (v2 schema). Requires editor role on the project."""
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        check_project_permission(user, slug, required_role="editor")
    except HTTPException:
        raise
    except Exception as _e:
        raise HTTPException(403, f"editor permission required: {_e}")

    if not _decisions_table_exists():
        return {"ok": False, "error": "decisions table not available", "decision": None}

    decision_text = (payload.get("decision_text") or "").strip()
    if not decision_text:
        raise HTTPException(400, "decision_text is required")

    evidence = payload.get("evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {}
    import json as _json
    owner_user_id = payload.get("owner_user_id")
    try:
        owner_user_id = int(owner_user_id) if owner_user_id is not None else None
    except Exception:
        owner_user_id = None
    due_at = payload.get("due_at") or None
    source_message_id = (payload.get("source_message_id") or None)
    session_id = (payload.get("session_id") or None)
    status_in = (payload.get("status") or "pending").strip()
    if status_in not in _DECISION_STATUSES:
        status_in = "pending"

    try:
        try:
            from db.session import get_write_engine as _gwe
            eng = _gwe()
        except Exception:
            eng = _engine
        with eng.begin() as conn:
            row = conn.execute(text(
                f"""INSERT INTO public.dash_decisions
                      (project_slug, user_id, session_id, decision_text, evidence,
                       owner_user_id, due_at, status, source_message_id)
                    VALUES
                      (:s, :u, :sid, :dt, CAST(:ev AS jsonb),
                       :own, :due, :st, :smid)
                    RETURNING {_DECISION_COLS}"""
            ), {
                "s": slug,
                "u": int(user.get("id") or 0),
                "sid": session_id,
                "dt": decision_text,
                "ev": _json.dumps(evidence),
                "own": owner_user_id,
                "due": due_at,
                "st": status_in,
                "smid": source_message_id,
            }).fetchone()
        try:
            from app.auth import audit_log as _audit
            _audit(int(user.get("id") or 0), "decision_v2_create", slug, {"id": row[0]})
        except Exception:
            pass
        return {"ok": True, "decision": _decision_row_to_dict(row)}
    except HTTPException:
        raise
    except Exception as e:
        # Fail-soft on schema drift — log and surface 500 only for unrelated
        # errors; if the table simply lacks new columns, return an empty result.
        msg = str(e)
        if "column" in msg.lower() and "does not exist" in msg.lower():
            return {"ok": False, "error": "decisions table schema not migrated", "decision": None}
        raise HTTPException(500, f"Failed to save decision: {e}")


@router.get("/{slug}/decisions/{decision_id}")
def get_decision_v2(slug: str, decision_id: int, request: Request):
    """Fetch a single decision detail (v2 schema)."""
    _get_user(request)
    if not _decisions_table_exists():
        return {"decision": None}
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                f"SELECT {_DECISION_COLS} FROM public.dash_decisions "
                f"WHERE id = :id AND project_slug = :s"
            ), {"id": decision_id, "s": slug}).fetchone()
        if not row:
            raise HTTPException(404, "decision not found")
        return {"decision": _decision_row_to_dict(row)}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "column" in msg.lower() and "does not exist" in msg.lower():
            return {"decision": None}
        raise HTTPException(500, f"Failed to load decision: {e}")


@router.get("/{slug}/decisions")
def list_decisions_v2(slug: str, request: Request,
                     status: str | None = None,
                     limit: int = 50):
    """List decisions for a project. Fail-soft — returns empty list if the
    table doesn't exist or hasn't been migrated to v2 schema yet."""
    _get_user(request)
    if not _decisions_table_exists():
        return {"decisions": []}
    limit = max(1, min(200, int(limit) if isinstance(limit, int) else 50))
    q = (f"SELECT {_DECISION_COLS} FROM public.dash_decisions "
         f"WHERE project_slug = :s")
    params: dict = {"s": slug, "lim": limit}
    if status:
        if status not in _DECISION_STATUSES:
            return {"decisions": []}
        q += " AND status = :st"
        params["st"] = status
    q += " ORDER BY created_at DESC LIMIT :lim"
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(q), params).fetchall()
        return {"decisions": [_decision_row_to_dict(r) for r in rows]}
    except Exception as e:
        msg = str(e)
        if "column" in msg.lower() and "does not exist" in msg.lower():
            return {"decisions": []}
        raise HTTPException(500, f"Failed to list decisions: {e}")


@router.patch("/{slug}/decisions/{decision_id}")
def update_decision_v2(slug: str, decision_id: int, payload: dict, request: Request):
    """Update a decision's status / owner / due_at. Requires editor role."""
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        check_project_permission(user, slug, required_role="editor")
    except HTTPException:
        raise
    except Exception as _e:
        raise HTTPException(403, f"editor permission required: {_e}")

    if not _decisions_table_exists():
        return {"ok": False, "error": "decisions table not available"}

    sets: list[str] = []
    params: dict = {"id": decision_id, "s": slug}

    if "status" in payload:
        new_status = (payload.get("status") or "").strip()
        if new_status not in _DECISION_STATUSES:
            raise HTTPException(400, f"invalid status; allowed: {_DECISION_STATUSES}")
        sets.append("status = :st")
        params["st"] = new_status

    if "owner_user_id" in payload:
        own = payload.get("owner_user_id")
        try:
            own = int(own) if own is not None else None
        except Exception:
            own = None
        sets.append("owner_user_id = :own")
        params["own"] = own

    if "due_at" in payload:
        sets.append("due_at = :due")
        params["due"] = payload.get("due_at") or None

    if not sets:
        raise HTTPException(400, "no updatable fields provided")

    sets.append("updated_at = now()")
    sql = (f"UPDATE public.dash_decisions SET {', '.join(sets)} "
           f"WHERE id = :id AND project_slug = :s "
           f"RETURNING {_DECISION_COLS}")
    try:
        try:
            from db.session import get_write_engine as _gwe
            eng = _gwe()
        except Exception:
            eng = _engine
        with eng.begin() as conn:
            row = conn.execute(text(sql), params).fetchone()
        if not row:
            raise HTTPException(404, "decision not found")
        try:
            from app.auth import audit_log as _audit
            _audit(int(user.get("id") or 0), "decision_v2_update", slug,
                   {"id": decision_id, "fields": list(payload.keys())})
        except Exception:
            pass
        return {"ok": True, "decision": _decision_row_to_dict(row)}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "column" in msg.lower() and "does not exist" in msg.lower():
            return {"ok": False, "error": "decisions table schema not migrated"}
        raise HTTPException(500, f"Failed to update decision: {e}")


@router.delete("/{slug}/decisions/{decision_id}")
def delete_decision_v2(slug: str, decision_id: int, request: Request):
    """Soft-delete by setting status='cancelled'. Requires editor role."""
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        check_project_permission(user, slug, required_role="editor")
    except HTTPException:
        raise
    except Exception as _e:
        raise HTTPException(403, f"editor permission required: {_e}")

    if not _decisions_table_exists():
        return {"ok": False, "error": "decisions table not available"}

    try:
        try:
            from db.session import get_write_engine as _gwe
            eng = _gwe()
        except Exception:
            eng = _engine
        with eng.begin() as conn:
            row = conn.execute(text(
                f"UPDATE public.dash_decisions "
                f"SET status = 'cancelled', updated_at = now() "
                f"WHERE id = :id AND project_slug = :s "
                f"RETURNING {_DECISION_COLS}"
            ), {"id": decision_id, "s": slug}).fetchone()
        if not row:
            raise HTTPException(404, "decision not found")
        try:
            from app.auth import audit_log as _audit
            _audit(int(user.get("id") or 0), "decision_v2_cancel", slug, {"id": decision_id})
        except Exception:
            pass
        return {"ok": True, "decision": _decision_row_to_dict(row)}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "column" in msg.lower() and "does not exist" in msg.lower():
            return {"ok": False, "error": "decisions table schema not migrated"}
        raise HTTPException(500, f"Failed to cancel decision: {e}")
