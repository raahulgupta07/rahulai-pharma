"""Self-distill loop (#5) — turn usage into durable, reviewable memory.

Reactive → proactive: instead of only learning when a user says "remember", the
system periodically reads what ALREADY happened — the 👎 corrections staff left
on wrong answers — and distils each into a SHORT durable fact worth remembering
("article codes must be matched as text across tables", "the busy branch is
20063"). Those facts land as `status='pending'` memories (Intern Rule) and never
reach chat until an admin approves them.

Complementary to the feedback→golden loop (which promotes a corrected Q→SQL pair
to a golden example): this captures the GENERAL fact/preference behind a
correction as a memory HINT, not the SQL.

DEFAULT OFF + capped. Reads only ALREADY-captured data (dash_feedback) — NO chat
hot-path changes, no new table. The LLM extraction is the only cost (LITE model,
budget-gated via training_llm_call); capped at DISTILLER_MAX_PER_CYCLE/cycle.
"""
from __future__ import annotations

import os
import logging

log = logging.getLogger("dash.distiller")

_MAX = int(os.getenv("DISTILLER_MAX_PER_CYCLE", "5"))

_PROMPT = """You are curating an analytics assistant's memory. A user thumbs-downed an answer and left a correction. Extract ONE short, durable, GENERALISABLE fact worth remembering for future questions — a rule, a definition, a data quirk, or a preference. Not the specific number. If nothing generalisable, reply exactly NONE.

Question: {q}
Wrong answer (excerpt): {a}
User correction: {c}

Reply with ONE sentence (the fact), or NONE. No preamble."""


def _conn():
    import psycopg
    c = psycopg.connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8,
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("SET statement_timeout = '30s';")
    return c, cur


def _candidates(cur, slug: str) -> list[dict]:
    """Recent 👎 corrections not yet distilled (created_by marker absent)."""
    cur.execute(
        """SELECT f.id, f.question, f.answer, f.correction
           FROM public.dash_feedback f
           WHERE f.project_slug = %s
             AND (f.rating = 'down' OR f.rating LIKE '-%%')
             AND COALESCE(f.correction, '') <> ''
             AND NOT EXISTS (
                 SELECT 1 FROM public.dash_memories m
                 WHERE m.created_by = 'distiller:fb' || f.id::text)
           ORDER BY f.created_at DESC
           LIMIT 50""",
        (slug,))
    return [{"id": r[0], "q": r[1] or "", "a": r[2] or "", "c": r[3] or ""}
            for r in cur.fetchall()]


def _extract(cand: dict) -> str | None:
    """LLM → one durable fact, or None. Fail-soft (no key / over budget → None)."""
    try:
        from dash.settings import training_llm_call
        prompt = _PROMPT.format(q=cand["q"][:400], a=cand["a"][:400], c=cand["c"][:400])
        out = training_llm_call(prompt, task="extraction")
        if not out:
            return None
        fact = out.strip().strip('"').strip()
        if not fact or fact.upper().startswith("NONE") or len(fact) < 8:
            return None
        return fact[:300]
    except Exception as e:
        log.debug(f"extract failed: {e}")
        return None


def run_distiller(slug: str, dry_run: bool = False) -> dict:
    """Distil pending memory facts from recent corrections. Capped, default-safe.

    Returns {"written": N, "facts": [...], "dry_run": bool}.
    """
    if not slug:
        return {"written": 0, "facts": [], "error": "no slug"}
    facts: list[dict] = []
    try:
        c, cur = _conn()
        try:
            cands = _candidates(cur, slug)
            for cand in cands:
                if len(facts) >= _MAX:
                    break
                fact = _extract(cand)
                if not fact:
                    # still mark as processed so we don't re-LLM it every cycle:
                    # write a rejected stub (cheap) ONLY when not dry-run.
                    if not dry_run:
                        try:
                            cur.execute(
                                "INSERT INTO public.dash_memories "
                                "(project_slug, scope, fact, source, status, created_by) "
                                "VALUES (%s,'project','(no generalisable fact)','distilled',"
                                " 'rejected', %s)",
                                (slug, f"distiller:fb{cand['id']}"))
                        except Exception:
                            pass
                    continue
                facts.append({"fb_id": cand["id"], "fact": fact, "question": cand["q"][:120]})
                if not dry_run:
                    cur.execute(
                        "INSERT INTO public.dash_memories "
                        "(project_slug, scope, fact, source, status, created_by) "
                        "VALUES (%s,'project',%s,'distilled','pending',%s)",
                        (slug, fact, f"distiller:fb{cand['id']}"))
            return {"written": (0 if dry_run else len(facts)), "facts": facts,
                    "candidates": len(cands), "dry_run": dry_run}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"run_distiller({slug}) failed: {e}")
        return {"written": 0, "facts": facts, "error": str(e)[:300]}
