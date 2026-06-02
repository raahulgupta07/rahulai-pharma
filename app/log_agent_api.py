"""FEATURE D — Admin "ask the logs" agent.

Super-admin only. Pulls recent rows from the platform's confirmed log tables,
computes grouped counts + a recurring-pattern heuristic, builds a COMPACT text
summary, and feeds that + the question to the LLM for a concise NL answer.

Read-only. Never disposes the cached shared engine. Fail-soft on empty logs.

Confirmed log tables used (all in `public`):
  - dash_llm_costs       (ts, task, model, ok, cost_usd, project_slug)
  - dash_audit_log       (created_at, action, resource_type, username)
  - dash_quality_scores  (created_at, score, project_slug)
  - dash_guardrail_audit (ts, refusal_reason, classifier, project_slug)
  - dash_drift_events    (detected_at, drift_type, severity, status, project_slug)
  - dash_source_training_runs (started_at, status, project_slug)
  - dash_training_runs   (started_at, status, project_slug)
  - dash_ingest_batches  (created_at, status, project_slug)
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/log-agent", tags=["admin", "log-agent"])

# Hard cap on rows scanned per table (read-only safety).
_ROW_CAP = 5000
# Max distinct groups returned per dimension.
_TOP_N = 8


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    if not user.get("is_super_admin"):
        raise HTTPException(403, "super-admin only")


class AskBody(BaseModel):
    question: str
    days: int = 14
    project_slug: Optional[str] = None


# Each spec: table, timestamp column, and the dimension columns to group on.
# group dims are (sql_expr, label_prefix).
_SPECS = [
    {
        "table": "dash_llm_costs", "ts": "ts", "label": "LLM calls",
        "dims": [("task", "task"), ("model", "model")],
        "extra_counts": [("ok = false", "failed LLM calls")],
    },
    {
        "table": "dash_audit_log", "ts": "created_at", "label": "audit events",
        "dims": [("action", "action"), ("resource_type", "resource")],
    },
    {
        "table": "dash_quality_scores", "ts": "created_at", "label": "quality scores",
        "dims": [("score::text", "score")],
        "extra_counts": [("score <= 2", "low quality (<=2)")],
    },
    {
        "table": "dash_guardrail_audit", "ts": "ts", "label": "guardrail refusals",
        "dims": [("refusal_reason", "refusal"), ("classifier", "classifier")],
    },
    {
        "table": "dash_drift_events", "ts": "detected_at", "label": "drift events",
        "dims": [("drift_type", "drift"), ("severity", "severity"), ("status", "drift status")],
    },
    {
        "table": "dash_source_training_runs", "ts": "started_at", "label": "source training runs",
        "dims": [("status", "training status")],
        "extra_counts": [("status = 'failed'", "failed source trainings")],
    },
    {
        "table": "dash_training_runs", "ts": "started_at", "label": "training runs",
        "dims": [("status", "training status")],
        "extra_counts": [("status = 'failed'", "failed trainings")],
    },
    {
        "table": "dash_ingest_batches", "ts": "created_at", "label": "ingest batches",
        "dims": [("status", "ingest status")],
    },
]


def _scoped(conn, table: str, ts: str, where_sql: str, days: int,
            project_slug: Optional[str]) -> int:
    """COUNT rows within window (capped), optionally scoped to project."""
    clauses = [f"{ts} >= NOW() - (:days || ' days')::interval"]
    params = {"days": str(int(days))}
    if where_sql:
        clauses.append(f"({where_sql})")
    if project_slug and table != "dash_audit_log":
        clauses.append("project_slug = :slug")
        params["slug"] = project_slug
    sql = (
        f"SELECT COUNT(*) FROM (SELECT 1 FROM public.{table} "
        f"WHERE {' AND '.join(clauses)} LIMIT :cap) s"
    )
    params["cap"] = _ROW_CAP
    return int(conn.execute(text(sql), params).scalar() or 0)


def _group(conn, table: str, ts: str, expr: str, label_prefix: str,
           days: int, project_slug: Optional[str]) -> list[dict]:
    """Grouped counts for one dimension. Returns [{label,count,detail}]."""
    clauses = [f"{ts} >= NOW() - (:days || ' days')::interval", f"{expr} IS NOT NULL"]
    params = {"days": str(int(days)), "cap": _ROW_CAP, "topn": _TOP_N}
    if project_slug and table != "dash_audit_log":
        clauses.append("project_slug = :slug")
        params["slug"] = project_slug
    sql = (
        f"SELECT {expr} AS g, COUNT(*) AS c FROM "
        f"(SELECT {expr} FROM public.{table} WHERE {' AND '.join(clauses)} LIMIT :cap) s "
        f"GROUP BY g ORDER BY c DESC LIMIT :topn"
    )
    out = []
    for row in conn.execute(text(sql), params):
        g = row[0]
        if g is None or str(g).strip() == "":
            g = "(none)"
        out.append({
            "label": f"{label_prefix}: {g}",
            "count": int(row[1]),
            "detail": f"{table}",
        })
    return out


@router.post("/ask")
def ask_the_logs(body: AskBody, request: Request):
    user = _get_user(request)
    _require_super(user)

    question = (body.question or "").strip()
    days = max(1, min(int(body.days or 14), 365))
    project_slug = (body.project_slug or "").strip() or None

    if not question:
        raise HTTPException(400, "question is required")

    groups: list[dict] = []
    totals: dict[str, int] = {}
    failures: list[tuple[str, int]] = []

    try:
        from db.session import get_sql_engine
        engine = get_sql_engine()  # cached shared engine — DO NOT dispose
        with engine.connect() as conn:
            for spec in _SPECS:
                table, ts = spec["table"], spec["ts"]
                try:
                    total = _scoped(conn, table, ts, "", days, project_slug)
                except Exception as e:  # missing table / column drift — skip table
                    logger.debug("log-agent: skip %s (%s)", table, e)
                    continue
                if total <= 0:
                    continue
                totals[spec["label"]] = total
                # dimension groups
                for expr, prefix in spec.get("dims", []):
                    try:
                        groups.extend(_group(conn, table, ts, expr, prefix, days, project_slug))
                    except Exception as e:
                        logger.debug("log-agent: group fail %s.%s (%s)", table, expr, e)
                # extra failure-type counts (recurring-pattern signal)
                for where_sql, fail_label in spec.get("extra_counts", []):
                    try:
                        c = _scoped(conn, table, ts, where_sql, days, project_slug)
                    except Exception:
                        c = 0
                    if c > 0:
                        failures.append((fail_label, c))
                        groups.append({"label": fail_label, "count": c, "detail": table})
    except Exception as e:
        logger.exception("log-agent: engine/query error: %s", e)
        # fail soft — still return a valid empty payload below

    # sort groups by count desc, keep a sane cap
    groups.sort(key=lambda g: g["count"], reverse=True)
    groups = groups[:24]

    # ---- recurring-pattern heuristic ---------------------------------------
    pattern: Optional[str] = None
    grand = sum(totals.values())
    if grand > 0 and groups:
        top = groups[0]
        # a single group dominating the activity
        if top["count"] >= max(5, int(0.6 * grand)):
            pattern = (
                f"One group dominates: '{top['label']}' accounts for "
                f"{top['count']} of {grand} logged events "
                f"({round(100 * top['count'] / grand)}%)."
            )
    if pattern is None and failures:
        worst = max(failures, key=lambda f: f[1])
        if worst[1] >= 3:
            pattern = (
                f"Repeating failure: '{worst[0]}' occurred {worst[1]} times "
                f"in the last {days} days."
            )

    # ---- compact summary for the LLM ---------------------------------------
    if grand == 0:
        return {
            "answer": (
                "No log activity found in the selected window"
                + (f" for project '{project_slug}'" if project_slug else "")
                + f" (last {days} days)."
            ),
            "groups": [],
            "pattern": None,
            "window_days": days,
        }

    lines = [f"Window: last {days} days" + (f", project={project_slug}" if project_slug else "")]
    lines.append("Totals by log type: " + ", ".join(f"{k}={v}" for k, v in sorted(totals.items())))
    lines.append("Top groups: " + "; ".join(f"{g['label']}={g['count']}" for g in groups[:_TOP_N]))
    if failures:
        lines.append("Failures: " + ", ".join(f"{n}={c}" for n, c in failures))
    if pattern:
        lines.append("Detected pattern: " + pattern)
    summary = "\n".join(lines)

    prompt = (
        "You are an admin log analyst. Using ONLY the aggregated log summary "
        "below (counts, not raw rows), answer the admin's question concisely "
        "in 2-4 sentences. Reference concrete numbers. If the summary cannot "
        "answer the question, say so plainly.\n\n"
        f"=== LOG SUMMARY ===\n{summary}\n\n"
        f"=== QUESTION ===\n{question}\n"
    )

    answer = None
    try:
        from dash.settings import training_llm_call
        answer = training_llm_call(prompt, "extraction")
    except Exception as e:
        logger.warning("log-agent: LLM call failed: %s", e)

    if not answer:
        # deterministic fallback so we never 500 / never return empty
        answer = (
            f"In the last {days} days there were {grand} logged events. "
            + (f"Top: {groups[0]['label']} ({groups[0]['count']}). " if groups else "")
            + (pattern or "No dominant recurring pattern detected.")
        )

    return {
        "answer": answer.strip(),
        "groups": groups,
        "pattern": pattern,
        "window_days": days,
    }
