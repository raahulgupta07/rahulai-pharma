"""Workflow DAG runner.

Patterns supported: parallel-fanout (parallel_group), loop-with-end-condition
(loop_until expr), router-branch (route_by), conditional skip (condition),
HITL gate (kind='hitl'), retry (on_error='retry'), continue-on-error.

Step output flows into a shared context dict (ctx[step_id] = output).
Prompts use {var} substitution from ctx.

Behind EXPERIMENTAL_AGI=1 for full feature set; otherwise runs but skips
HITL gates (auto-approves) and emits warning.

SSE event bus: per-run asyncio.Queue keyed by run_id, drop-oldest on overflow.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json as _json
import logging
import os
import re
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

PROMPT_VAR_RE = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


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


# ── SSE bus ──────────────────────────────────────────────────────────────
_run_buses: Dict[str, asyncio.Queue] = {}
_run_buses_lock: Optional[asyncio.Lock] = None


def _bus_lock() -> asyncio.Lock:
    global _run_buses_lock
    if _run_buses_lock is None:
        _run_buses_lock = asyncio.Lock()
    return _run_buses_lock


async def get_run_bus(run_id: str) -> asyncio.Queue:
    async with _bus_lock():
        q = _run_buses.get(run_id)
        if q is None:
            q = asyncio.Queue(maxsize=256)
            _run_buses[run_id] = q
        return q


async def _emit(run_id: str, event: Dict[str, Any]) -> None:
    q = await get_run_bus(run_id)
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        try:
            q.get_nowait()  # drop oldest
        except Exception:
            pass
        try:
            q.put_nowait(event)
        except Exception:
            pass


async def _drop_bus(run_id: str) -> None:
    async with _bus_lock():
        _run_buses.pop(run_id, None)


# ── DB helpers ───────────────────────────────────────────────────────────
def _insert_run(def_id: str, project_slug: Optional[str], triggered_by: Optional[int],
                trigger_kind: str, input_payload: Dict[str, Any]) -> str:
    run_id = "wfr_" + secrets.token_hex(4)
    eng = _get_engine()
    if eng is None:
        return run_id
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_workflow_runs
                      (id, def_id, project_slug, triggered_by, trigger_kind,
                       status, input_payload)
                    VALUES (:id, :def, :ps, :tb, :tk, 'running', CAST(:ip AS jsonb))
                    """
                ),
                {
                    "id": run_id, "def": def_id, "ps": project_slug,
                    "tb": triggered_by, "tk": trigger_kind,
                    "ip": _json.dumps(input_payload),
                },
            )
    except Exception as e:
        logger.warning("insert_run failed: %s", e)
    return run_id


def _update_run(run_id: str, **fields) -> None:
    eng = _get_engine()
    if eng is None:
        return
    try:
        from sqlalchemy import text
        sets = ", ".join(f"{k}=:{k}" for k in fields)
        with eng.begin() as conn:
            conn.execute(
                text(f"UPDATE dash.dash_workflow_runs SET {sets} WHERE id=:id"),
                {**fields, "id": run_id},
            )
    except Exception as e:
        logger.warning("update_run failed: %s", e)


def _insert_step_row(run_id: str, step_id: str, step_kind: str, iter_: int, input_: Any) -> Optional[int]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_workflow_run_steps
                      (run_id, step_id, step_kind, iter, status, input)
                    VALUES (:rid, :sid, :sk, :it, 'running', CAST(:in AS jsonb))
                    RETURNING id
                    """
                ),
                {
                    "rid": run_id, "sid": step_id, "sk": step_kind,
                    "it": iter_, "in": _json.dumps(input_, default=str)[:50000],
                },
            )
            return r.scalar()
    except Exception as e:
        logger.warning("insert_step_row failed: %s", e)
        return None


def _finalize_step_row(row_id: int, status: str, output: Any = None,
                       error: Optional[str] = None, latency_ms: int = 0) -> None:
    if row_id is None:
        return
    eng = _get_engine()
    if eng is None:
        return
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE dash.dash_workflow_run_steps
                       SET status=:st, output=CAST(:out AS jsonb), error=:err,
                           latency_ms=:lat, finished_at=now()
                     WHERE id=:id
                    """
                ),
                {
                    "id": row_id, "st": status,
                    "out": _json.dumps(output, default=str)[:50000] if output is not None else None,
                    "err": error, "lat": latency_ms,
                },
            )
    except Exception as e:
        logger.warning("finalize_step_row failed: %s", e)


# ── Prompt + expr eval ──────────────────────────────────────────────────
def _interpolate(template: str, ctx: Dict[str, Any]) -> str:
    if not template:
        return ""
    def _resolve(m):
        path = m.group(1).split(".")
        cur = ctx
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return m.group(0)
        if isinstance(cur, (dict, list)):
            return _json.dumps(cur, default=str)[:2000]
        return str(cur)
    return PROMPT_VAR_RE.sub(_resolve, template)


def _safe_eval(expr: str, ctx: Dict[str, Any]) -> Any:
    """Evaluate expr w/ ctx as namespace. Restricted builtins."""
    try:
        return eval(expr, {"__builtins__": {"len": len, "any": any, "all": all, "min": min, "max": max, "sum": sum}}, dict(ctx))
    except Exception as e:
        logger.warning("expr eval failed: %s on %r", e, expr)
        return None


# ── Step executors ──────────────────────────────────────────────────────
async def _exec_agent(step: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
    agent_name = step.get("agent", "Leader")
    prompt = _interpolate(step.get("prompt") or "", ctx)
    # Best-effort: try to invoke an existing agent factory.
    try:
        if agent_name == "Reporter":
            from dash.agents.reporter import build_reporter_agent
            agent = build_reporter_agent()
        else:
            agent = None
        if agent is not None and hasattr(agent, "run"):
            try:
                resp = agent.run(prompt)
                return getattr(resp, "content", str(resp))
            except Exception as e:
                return {"agent_error": str(e), "prompt": prompt[:500]}
    except Exception as e:
        logger.warning("agent build failed for %s: %s", agent_name, e)
    # Fallback: return prompt as "would-have-run" stub
    return {"stub": True, "agent": agent_name, "prompt": prompt[:500]}


async def _exec_tool(step: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
    tool_name = step.get("tool")
    args = step.get("args") or {}
    # Interpolate string args
    args_resolved = {}
    for k, v in args.items():
        if isinstance(v, str):
            args_resolved[k] = _interpolate(v, ctx)
        else:
            args_resolved[k] = v
    # Try dispatch
    try:
        if tool_name and tool_name.startswith("make_"):
            from dash.tools import file_generation as fg
            fn = getattr(fg, f"_{tool_name}", None)
            if fn is None:
                return {"ok": False, "error": f"unknown make_ tool: {tool_name}"}
            return fn(**args_resolved)
        if tool_name == "compress_search":
            from dash.tools.compressor import CompressionManager
            mgr = CompressionManager()
            results = args_resolved.get("results") or []
            return await mgr.compress_search_results(results, args_resolved.get("query_intent", ""))
    except Exception as e:
        return {"ok": False, "error": f"tool dispatch failed: {e}"}
    return {"ok": False, "error": f"tool not registered: {tool_name}"}


async def _exec_hitl(step: Dict[str, Any], ctx: Dict[str, Any], run_id: str) -> Any:
    if not _enabled():
        return {"ok": True, "auto_approved": True, "reason": "EXPERIMENTAL_AGI off"}
    try:
        from dash.agentic.hitl import current_run_id
        token = current_run_id.set(run_id)
        try:
            from dash.agentic._examples import safe_create_view  # noqa: F401
        finally:
            current_run_id.reset(token)
        return {"ok": True, "hitl_invoked": True}
    except Exception as e:
        return {"ok": False, "error": f"hitl unavailable: {e}"}


# ── Main runner ─────────────────────────────────────────────────────────
async def execute_workflow(
    def_row: Dict[str, Any],
    inputs: Optional[Dict[str, Any]] = None,
    triggered_by: Optional[int] = None,
    trigger_kind: str = "manual",
) -> Dict[str, Any]:
    spec = def_row.get("spec") or {}
    if isinstance(spec, str):
        try:
            spec = _json.loads(spec)
        except Exception:
            return {"ok": False, "error": "invalid spec json"}
    steps = spec.get("steps") or []
    ctx: Dict[str, Any] = dict(spec.get("inputs") or {})
    if inputs:
        ctx.update(inputs)

    run_id = _insert_run(
        def_row["id"], def_row.get("project_slug"), triggered_by, trigger_kind, ctx,
    )
    await _emit(run_id, {"event": "run_started", "run_id": run_id, "def_id": def_row["id"]})

    started = time.time()
    completed: Set[str] = set()
    skipped: Set[str] = set()
    failed_step = None

    # Index by id
    by_id = {s["id"]: s for s in steps if s.get("id")}
    # Track parallel groups
    pgroups: Dict[str, List[str]] = {}
    for s in steps:
        pg = s.get("parallel_group")
        if pg:
            pgroups.setdefault(pg, []).append(s["id"])

    # Topo-ish loop: keep scanning until all done or stuck
    max_passes = len(steps) * 4 + 10
    for _ in range(max_passes):
        progress = False
        # Identify ready steps (deps done, not yet run, not skipped)
        ready = []
        for s in steps:
            sid = s["id"]
            if sid in completed or sid in skipped:
                continue
            deps = s.get("depends_on") or []
            if all((d in completed or d in skipped) for d in deps):
                # condition gate
                cond = s.get("condition")
                if cond:
                    ok = _safe_eval(cond, ctx)
                    if not ok:
                        skipped.add(sid)
                        await _emit(run_id, {"event": "step_skipped", "step_id": sid, "reason": "condition_false"})
                        progress = True
                        continue
                ready.append(s)

        if not ready:
            break

        # Group ready by parallel_group; run each group concurrently
        groups: Dict[Optional[str], List[Dict[str, Any]]] = {}
        for s in ready:
            groups.setdefault(s.get("parallel_group"), []).append(s)

        for group_key, group_steps in groups.items():
            results = await asyncio.gather(
                *[_run_step(s, ctx, run_id) for s in group_steps],
                return_exceptions=True,
            )
            for s, res in zip(group_steps, results):
                sid = s["id"]
                if isinstance(res, Exception):
                    on_err = s.get("on_error", "fail")
                    if on_err == "continue":
                        ctx[sid] = {"error": str(res)}
                        completed.add(sid)
                        await _emit(run_id, {"event": "step_failed_continue", "step_id": sid, "error": str(res)})
                    else:
                        failed_step = sid
                        await _emit(run_id, {"event": "step_failed", "step_id": sid, "error": str(res)})
                        _update_run(
                            run_id, status="failed", error=f"{sid}: {res}",
                            finished_at=dt.datetime.utcnow(),
                        )
                        await _emit(run_id, {"event": "run_failed"})
                        await _drop_bus(run_id)
                        return {"ok": False, "run_id": run_id, "error": str(res), "failed_step": sid}
                else:
                    ctx[sid] = res
                    completed.add(sid)
                    # Router branching: skip non-selected branches
                    if s.get("kind") == "router" and s.get("branches"):
                        chosen = _safe_eval(s.get("route_by") or "", ctx)
                        all_branch_steps = []
                        for branch_name, branch_steps in s["branches"].items():
                            all_branch_steps.extend(branch_steps)
                            if branch_name != chosen:
                                for bs in branch_steps:
                                    skipped.add(bs)
                                    await _emit(run_id, {"event": "step_skipped", "step_id": bs, "reason": f"router_chose_{chosen}"})
            progress = True

        if not progress:
            break

    # Collect outputs
    outputs_keys = (spec.get("outputs") or [])[:]
    final_output = {k: ctx.get(k) for k in outputs_keys} if outputs_keys else {
        sid: ctx.get(sid) for sid in completed
    }
    elapsed = time.time() - started
    _update_run(
        run_id, status="done", output_payload=_json.dumps(final_output, default=str)[:50000],
        finished_at=dt.datetime.utcnow(),
    )
    await _emit(run_id, {"event": "run_done", "elapsed_s": round(elapsed, 2), "completed": len(completed)})
    await _drop_bus(run_id)
    return {"ok": True, "run_id": run_id, "output": final_output, "elapsed_s": elapsed}


async def _run_step(step: Dict[str, Any], ctx: Dict[str, Any], run_id: str) -> Any:
    sid = step["id"]
    kind = step.get("kind", "agent")
    started = time.time()
    row_id = _insert_step_row(run_id, sid, kind, 0, {"prompt": step.get("prompt"), "args": step.get("args")})
    await _emit(run_id, {"event": "step_started", "step_id": sid, "kind": kind})

    try:
        if kind == "agent":
            result = await _exec_agent(step, ctx)
        elif kind == "tool":
            result = await _exec_tool(step, ctx)
        elif kind == "router":
            # router result is just the route_by expr value; branching handled by parent loop
            result = _safe_eval(step.get("route_by") or "", ctx)
        elif kind == "loop":
            iter_results = []
            max_iter = step.get("max_iter", 3)
            inner_steps = step.get("inner") or []
            for i in range(max_iter):
                ctx["__loop_iter__"] = i
                # Run inner steps sequentially within this loop iteration
                loop_ctx = dict(ctx)
                for inner in inner_steps:
                    res = await _run_step(inner, loop_ctx, run_id)
                    loop_ctx[inner["id"]] = res
                iter_results.append({k: loop_ctx.get(k) for k in [s["id"] for s in inner_steps]})
                ctx.update(loop_ctx)
                # Check termination
                until = step.get("loop_until")
                if until and _safe_eval(until, ctx):
                    break
            result = {"iterations": iter_results, "count": len(iter_results)}
        elif kind == "parallel":
            # explicit parallel container — children listed in `branches`
            children = []
            for branch in (step.get("branches") or {}).values():
                children.extend(branch)
            child_steps = [step["__by_id__"][cid] for cid in children if "__by_id__" in step]
            results = await asyncio.gather(*[_run_step(c, ctx, run_id) for c in child_steps])
            result = dict(zip(children, results))
        elif kind == "hitl":
            result = await _exec_hitl(step, ctx, run_id)
        else:
            result = {"ok": False, "error": f"unknown kind: {kind}"}
    except Exception as e:
        latency = int((time.time() - started) * 1000)
        _finalize_step_row(row_id, "failed", error=str(e), latency_ms=latency)
        # retry?
        if step.get("on_error") == "retry":
            for attempt in range(step.get("retry_max", 2)):
                try:
                    return await _run_step(step, ctx, run_id)
                except Exception:
                    continue
        raise

    latency = int((time.time() - started) * 1000)
    _finalize_step_row(row_id, "done", output=result, latency_ms=latency)
    await _emit(run_id, {"event": "step_done", "step_id": sid, "latency_ms": latency})
    return result
