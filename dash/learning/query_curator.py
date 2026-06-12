"""Query-bank curator + review gate (P3).

Captured chat patterns land as status='pending' (unverified). This module moves
them through the trust lifecycle:

    pending  --admin approve-->  candidate  --verified + used-->  proven
       |                              |                              |
       +--admin reject-->  demoted    +--re-verify fails-->  demoted  +--👎/correction-->  demoted

Verification = re-run the stored SQL READ-ONLY and confirm it still executes and
returns data (schema may have drifted). Promotion to 'proven' requires it to be a
candidate (admin-approved) AND have been used enough AND verify clean. The leader
daemon (query_curator_daemon) calls run_query_curator(); admins call the
approve/reject/promote/demote helpers from app/query_bank_api.py.

Reuses verified_reward._run_rows (exec) + cache_curator._is_read_only (safety) +
schema_guard (drift). Fail-soft throughout.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("dash.query_curator")

_NAMESPACE = "qbank"


def _promote_min_uses() -> int:
    # Raised 2 -> 3 (2026-06-12): auto-promote = zero-LLM Mode-1 serve on a
    # pharma agent, so demand the question recur a bit more before trusting it.
    try:
        return int(os.getenv("QUERY_CURATOR_MIN_USES", "3"))
    except Exception:
        return 3


def _max_promote_per_cycle() -> int:
    # Cap auto-promotes per scan so a bad batch can't flood the served bank in
    # one cycle (the rest stay candidate, promote next cycle / on review).
    try:
        return int(os.getenv("QUERY_CURATOR_MAX_PROMOTE", "10"))
    except Exception:
        return 10


def _crosscheck_on() -> bool:
    return str(os.getenv("QUERY_CURATOR_CROSSCHECK", "1")).strip().lower() in ("1", "true", "yes")


def _independent_value(slug: str, question: str):
    """Independently RE-DERIVE the answer: ask the LLM to write a FRESH SQL for
    the question (not the stored one), run it read-only, return its numeric value.

    This is the real correctness gate — the daemon's plain verify only proves the
    stored SQL *runs and returns a number*, not that the number is RIGHT. A
    semantically-wrong stored SQL still returns a number, so "verified" alone is
    not trustworthy enough to grant zero-LLM serve on a pharma agent. By deriving
    the answer a SECOND, independent way and requiring agreement, a wrong stored
    SQL is caught (its number won't match a fresh derivation). Returns None if it
    can't derive one (→ caller HOLDS rather than promotes — fail-safe)."""
    try:
        from dash.tools.llm_sql_helper import generate_sql_safe
        from dash.learning import verified_reward as _vr
        from dash.learning.cache_curator import _is_read_only
        gen = generate_sql_safe(
            f"Write ONE read-only SQL query that answers this question, using the "
            f"current pharmacy tables: {question}",
            slug, task="extraction", max_retries=1,
        )
        sql = (gen or {}).get("sql")
        if not sql or not _is_read_only(sql):
            return None
        run = _vr._run_rows(slug, sql, limit=20)
        if not run:
            return None
        return run.get("value")
    except Exception as exc:  # noqa: BLE001
        logger.debug("independent_value failed: %s", exc)
        return None


def _downvoted_norms(slug: str) -> set[str]:
    """Normalized questions that ever got a 👎 (any down rating). Auto-promote is
    BLOCKED for these — a thumbs-down is enough to withhold zero-LLM trust until
    a human reviews. Best-effort (empty set on error)."""
    from sqlalchemy import text as _text
    from db.session import get_sql_engine
    try:
        with get_sql_engine().connect() as conn:
            rows = conn.execute(_text(
                "SELECT DISTINCT lower(regexp_replace(COALESCE(question,''),'\\s+',' ','g')) "
                "FROM public.dash_feedback "
                "WHERE (rating = 'down' OR rating LIKE '-%') AND COALESCE(question,'') <> ''"
            )).fetchall()
        return {r[0] for r in rows if r[0]}
    except Exception as exc:  # noqa: BLE001
        logger.debug("downvoted_norms failed: %s", exc)
        return set()


def _exec(slug: str, sql: str):
    """Run a stored SQL read-only; return {value, rows, columns} or None."""
    try:
        from dash.learning.cache_curator import _is_read_only
        if not _is_read_only(sql):
            return None
        from dash.learning import verified_reward as _vr
        return _vr._run_rows(slug, sql, limit=20)
    except Exception as exc:  # noqa: BLE001
        logger.debug("query_curator exec failed: %s", exc)
        return None


def _set_status(slug: str, pattern_id: int, status: str, *, refresh_hash: bool = False) -> bool:
    from sqlalchemy import text as _text
    from db.session import get_write_engine
    try:
        eng = get_write_engine()
        with eng.begin() as conn:
            if refresh_hash:
                row = conn.execute(_text(
                    "SELECT sql FROM public.dash_query_patterns WHERE id = :id AND project_slug = :s"
                ), {"id": pattern_id, "s": slug}).first()
                sh = None
                if row and row[0]:
                    try:
                        from dash.learning.schema_guard import schema_hash_for_sql
                        sh = schema_hash_for_sql(slug, row[0])
                    except Exception:
                        sh = None
                conn.execute(_text(
                    "UPDATE public.dash_query_patterns SET status = :st, schema_hash = COALESCE(:sh, schema_hash) "
                    "WHERE id = :id AND project_slug = :s"
                ), {"st": status, "sh": sh, "id": pattern_id, "s": slug})
            else:
                conn.execute(_text(
                    "UPDATE public.dash_query_patterns SET status = :st WHERE id = :id AND project_slug = :s"
                ), {"st": status, "id": pattern_id, "s": slug})
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_status(%s->%s) failed: %s", pattern_id, status, exc)
        return False


# ── Review-gate admin actions ────────────────────────────────────────────────

def approve_pattern(slug: str, pattern_id: int) -> dict:
    """Admin approves a pending capture → candidate (eligible for serve as a
    Mode-2 hint; Mode-1 bypass still needs proven). Re-stamps schema_hash."""
    ok = _set_status(slug, pattern_id, "candidate", refresh_hash=True)
    return {"ok": ok, "status": "candidate"}


def reject_pattern(slug: str, pattern_id: int) -> dict:
    """Admin rejects a pattern → demoted (kept for audit, never served)."""
    ok = _set_status(slug, pattern_id, "demoted")
    return {"ok": ok, "status": "demoted"}


def promote_pattern(slug: str, pattern_id: int) -> dict:
    """Force-promote to proven after a verify (admin override)."""
    from sqlalchemy import text as _text
    from db.session import get_sql_engine
    try:
        with get_sql_engine().connect() as conn:
            row = conn.execute(_text(
                "SELECT sql FROM public.dash_query_patterns WHERE id = :id AND project_slug = :s"
            ), {"id": pattern_id, "s": slug}).first()
        if not row or not row[0]:
            return {"ok": False, "error": "pattern not found"}
        run = _exec(slug, row[0])
        if not run or run.get("value") is None:
            return {"ok": False, "error": "verify failed — SQL returned no data"}
        ok = _set_status(slug, pattern_id, "proven", refresh_hash=True)
        return {"ok": ok, "status": "proven", "verified_value": run.get("value")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def demote_pattern(slug: str, pattern_id: int) -> dict:
    ok = _set_status(slug, pattern_id, "demoted")
    return {"ok": ok, "status": "demoted"}


# ── Auto-curation (leader daemon) ────────────────────────────────────────────

def run_query_curator(slug: str, *, limit: int = 50, dry_run: bool = False) -> dict:
    """Scan candidate chat patterns; verify each (re-run read-only). Promote
    candidates that verify + meet the min-uses bar to 'proven'; demote any whose
    SQL no longer returns data. Pending rows are LEFT for human review (Intern
    Rule). Read-only-safe. Returns a summary."""
    from sqlalchemy import text as _text
    from db.session import get_sql_engine

    summary = {"scanned": 0, "promoted": 0, "demoted": 0, "kept": 0, "details": []}
    try:
        with get_sql_engine().connect() as conn:
            rows = conn.execute(_text(
                "SELECT id, question, sql, uses, status, question_norm FROM public.dash_query_patterns "
                "WHERE project_slug = :s AND source = 'chat' AND status = 'candidate' "
                "ORDER BY uses DESC, last_used DESC LIMIT :k"
            ), {"s": slug, "k": limit}).fetchall()

        min_uses = _promote_min_uses()
        max_promote = _max_promote_per_cycle()
        downvoted = _downvoted_norms(slug)
        for r in rows:
            pid, question, sql, uses, status = int(r[0]), r[1], r[2], int(r[3] or 0), r[4]
            qnorm = r[5] or ""
            summary["scanned"] += 1
            run = _exec(slug, sql)
            verified = bool(run and run.get("value") is not None)
            if not verified:
                summary["demoted"] += 1
                summary["details"].append({"id": pid, "action": "demote", "reason": "verify failed"})
                if not dry_run:
                    _set_status(slug, pid, "demoted")
                continue
            # SAFETY GATE 1 — never auto-promote a pattern that ever got a 👎.
            if qnorm and qnorm in downvoted:
                summary["kept"] += 1
                summary["details"].append({"id": pid, "action": "hold",
                                           "reason": "has negative feedback — needs human review"})
                continue
            # SAFETY GATE 2 — per-cycle promote cap (a bad batch can't flood).
            if uses >= min_uses and summary["promoted"] >= max_promote:
                summary["kept"] += 1
                summary["details"].append({"id": pid, "action": "hold",
                                           "reason": f"promote cap {max_promote}/cycle reached"})
                continue
            if uses >= min_uses:
                # SAFETY GATE 3 — CORRECTNESS cross-check. Independently re-derive
                # the answer (fresh LLM-written SQL) and require agreement before
                # granting zero-LLM serve. Catches semantically-wrong stored SQL
                # that "verifies" only because it returns *a* number.
                learned_val = run.get("value")
                if _crosscheck_on():
                    indep = _independent_value(slug, question)
                    from dash.learning import verified_reward as _vr
                    if indep is None:
                        summary["kept"] += 1
                        summary["details"].append({"id": pid, "action": "hold",
                            "reason": "cross-check could not re-derive — needs review"})
                        continue
                    if not (learned_val is not None and _vr._matches(float(learned_val), float(indep))):
                        summary["kept"] += 1
                        summary["details"].append({"id": pid, "action": "hold",
                            "reason": f"cross-check disagreed (stored={learned_val} vs fresh={indep}) — needs review"})
                        continue
                summary["promoted"] += 1
                summary["details"].append({"id": pid, "action": "promote",
                                           "reason": f"verified + uses={uses} + cross-checked",
                                           "value": learned_val})
                if not dry_run:
                    _set_status(slug, pid, "proven", refresh_hash=True)
            else:
                summary["kept"] += 1
                summary["details"].append({"id": pid, "action": "keep",
                                           "reason": f"verified, uses={uses}<{min_uses}"})
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_query_curator failed for %s: %s", slug, exc)
        summary["error"] = str(exc)
        return summary


def demote_on_negative_feedback(slug: str) -> dict:
    """Demote proven/candidate patterns whose matched question got a 👎 with a
    correction. Best-effort link via normalized question text in dash_feedback."""
    from sqlalchemy import text as _text
    from db.session import get_write_engine
    try:
        eng = get_write_engine()
        with eng.begin() as conn:
            n = conn.execute(_text(
                "UPDATE public.dash_query_patterns p SET status = 'demoted' "
                "FROM public.dash_feedback f "
                "WHERE p.project_slug = :s AND p.source = 'chat' "
                "  AND p.status IN ('candidate','proven') "
                # rating is TEXT: negative = 'down' / '-1' / any leading-minus value
                "  AND (f.rating = 'down' OR f.rating LIKE '-%') "
                "  AND f.correction IS NOT NULL AND f.correction <> '' "
                "  AND lower(regexp_replace(COALESCE(f.question,''),'\\s+',' ','g')) = p.question_norm"
            ), {"s": slug}).rowcount
        return {"ok": True, "demoted": int(n or 0)}
    except Exception as exc:  # noqa: BLE001
        logger.debug("demote_on_negative_feedback failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── Fold proven chat patterns into the training set (P6) ─────────────────────

def fold_proven_into_training(slug: str) -> dict:
    """Copy 'proven' chat-learned patterns into dash_training_qa so they harden
    into the training corpus (survive retrain, feed retrieval). Idempotent — skips
    rows whose (table, question) already exists. Called from the training-complete
    post-hooks. Fail-soft."""
    from sqlalchemy import text as _text
    from db.session import get_write_engine
    try:
        eng = get_write_engine()
        with eng.begin() as conn:
            n = conn.execute(_text(
                "INSERT INTO public.dash_training_qa "
                "  (project_slug, table_name, question, sql, created_at) "
                "SELECT p.project_slug, "
                "       COALESCE(split_part(p.tables_used, ',', 1), 'learned') AS table_name, "
                "       p.question, p.sql, now() "
                "FROM public.dash_query_patterns p "
                "WHERE p.project_slug = :s AND p.source = 'chat' AND p.status = 'proven' "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM public.dash_training_qa t "
                "    WHERE t.project_slug = p.project_slug "
                "      AND lower(t.question) = lower(p.question))"
            ), {"s": slug}).rowcount
        return {"ok": True, "folded": int(n or 0)}
    except Exception as exc:  # noqa: BLE001
        logger.debug("fold_proven_into_training failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── Stats (admin) ────────────────────────────────────────────────────────────

def bank_stats(slug: str) -> dict:
    from sqlalchemy import text as _text
    from db.session import get_sql_engine
    out = {"by_status": {}, "total_chat": 0, "total_training": 0, "shadow": {}}
    try:
        with get_sql_engine().connect() as conn:
            for row in conn.execute(_text(
                "SELECT source, status, count(*) FROM public.dash_query_patterns "
                "WHERE project_slug = :s GROUP BY source, status"
            ), {"s": slug}).fetchall():
                src, st, n = row[0], row[1], int(row[2])
                if src == "chat":
                    out["total_chat"] += n
                    out["by_status"][st] = out["by_status"].get(st, 0) + n
                else:
                    out["total_training"] += n
            sh = conn.execute(_text(
                "SELECT count(*) AS total, "
                "       count(*) FILTER (WHERE matched_id IS NOT NULL) AS near, "
                "       count(*) FILTER (WHERE would_serve) AS would_serve, "
                "       round(avg(sim) FILTER (WHERE matched_id IS NOT NULL), 3) AS avg_sim "
                "FROM public.dash_query_bank_shadow WHERE project_slug = :s "
                "  AND created_at > now() - interval '30 days'"
            ), {"s": slug}).first()
            if sh:
                total = int(sh[0] or 0)
                near = int(sh[1] or 0)
                out["shadow"] = {
                    "total": total, "near_match": near,
                    "would_serve": int(sh[2] or 0),
                    "avg_sim": float(sh[3]) if sh[3] is not None else None,
                    "repeat_rate": round(near / total, 3) if total else 0.0,
                    "serve_rate": round(int(sh[2] or 0) / total, 3) if total else 0.0,
                }
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def list_patterns(slug: str, *, source: str = "chat", status: str | None = None,
                  limit: int = 200) -> list[dict]:
    from sqlalchemy import text as _text
    from db.session import get_sql_engine
    try:
        clause = "WHERE project_slug = :s AND source = :src"
        params = {"s": slug, "src": source, "k": limit}
        if status:
            clause += " AND status = :st"
            params["st"] = status
        with get_sql_engine().connect() as conn:
            rows = conn.execute(_text(
                "SELECT id, question, sql, status, uses, rows_returned, last_latency_ms, "
                "       schema_hash, tables_used, last_used "
                "FROM public.dash_query_patterns " + clause +
                " ORDER BY uses DESC, last_used DESC NULLS LAST LIMIT :k"
            ), params).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": int(r[0]), "question": r[1], "sql": r[2], "status": r[3],
                "uses": int(r[4] or 0), "rows_returned": r[5], "latency_ms": r[6],
                "has_schema_hash": bool(r[7]), "tables": r[8],
                "last_used": r[9].isoformat() if r[9] else None,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("list_patterns failed: %s", exc)
        return []
