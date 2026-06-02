"""Dream Lite — Tier 2 between-turn pipeline (LITE_MODEL, ~$0.005/cycle).

3-step pipeline triggered between chat turns:
  1. Pull unconsumed episode buffer rows for session
  2. Update user persona via core_memory_replace pattern (LITE call)
     → write to public.dash_dream_personas (UPSERT)
  3. Generate top-3 anticipated next questions for this user/session topic
     (precompute enqueue removed — dream_precompute module retired)

Safety caps:
  - 20 episodes per cycle
  - 15s total wall time
  - 3 LITE calls max ($0.005 budget per cycle)
  - mark episodes consumed AFTER pipeline succeeds
  - Never raises — try/except per step, status='failed' on err

Public surface:
    run_lite_cycle(project_slug, session_id, user_id, trigger_reason) -> dict
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from dash.learning import dream_poignancy
from dash.settings import training_llm_call

logger = logging.getLogger(__name__)


# ── Hard caps ────────────────────────────────────────────────────────────
_MAX_EPISODES_PER_CYCLE = 20
_MAX_WALL_TIME_S = 15.0
_MAX_LITE_CALLS = 3
_LITE_COST_PER_CALL = 0.0003
_BUDGET_CAP_USD = 0.005
_MAX_ANTICIPATED_QUESTIONS = 3


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ─────────────────────────────────────────────────────────────────────────
# JSON parsing helper
# ─────────────────────────────────────────────────────────────────────────
def _safe_parse_json(raw: str) -> Optional[Any]:
    """4-tier robust JSON parser."""
    if not raw:
        return None
    cleaned = raw.strip().strip("`").strip()
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    for opener, closer in [("{", "}"), ("[", "]")]:
        i = cleaned.find(opener)
        j = cleaned.rfind(closer)
        if i >= 0 and j > i:
            try:
                return json.loads(cleaned[i : j + 1])
            except Exception:
                continue
    fixed = re.sub(r",(\s*[\}\]])", r"\1", cleaned)
    try:
        return json.loads(fixed)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# Run row lifecycle
# ─────────────────────────────────────────────────────────────────────────
def _create_run(
    eng,
    project_slug: str,
    session_id: str,
    user_id: Optional[int],
    trigger_reason: str,
) -> Optional[int]:
    try:
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO public.dash_dream_lite_runs
                      (project_slug, user_id, session_id, triggered_at,
                       trigger_reason, status)
                    VALUES (:p, :uid, :sid, now(), :tr, 'running')
                    RETURNING id
                    """
                ),
                {
                    "p": project_slug,
                    "uid": int(user_id) if user_id is not None else None,
                    "sid": str(session_id)[:200],
                    "tr": str(trigger_reason)[:100],
                },
            ).first()
            return int(row[0]) if row else None
    except Exception:
        logger.exception("dream_lite: create_run failed")
        return None


def _finalize_run(
    eng,
    run_id: int,
    status: str,
    episodes_consumed: int = 0,
    persona_updated: bool = False,
    precompute_queued: int = 0,
    memory_ops_applied: int = 0,
    cost_usd: float = 0.0,
    error: Optional[str] = None,
    is_bootstrap: bool = False,
    friendly_status: Optional[str] = None,
) -> None:
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE public.dash_dream_lite_runs
                       SET status = :st,
                           finished_at = now(),
                           episodes_consumed = :ec,
                           persona_updated = :pu,
                           precompute_queued = :pq,
                           memory_ops_applied = :ma,
                           cost_usd = :c,
                           error = :err,
                           is_bootstrap = COALESCE(:isb, is_bootstrap),
                           friendly_status = COALESCE(:fs, friendly_status)
                     WHERE id = :id
                    """
                ),
                {
                    "st": status,
                    "ec": int(episodes_consumed),
                    "pu": bool(persona_updated),
                    "pq": int(precompute_queued),
                    "ma": int(memory_ops_applied),
                    "c": float(cost_usd),
                    "err": (error or None),
                    "isb": bool(is_bootstrap) if is_bootstrap else None,
                    "fs": friendly_status,
                    "id": int(run_id),
                },
            )
    except Exception:
        # Fallback for DBs where is_bootstrap / friendly_status columns
        # don't exist yet (pre-migration 086).
        try:
            with eng.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE public.dash_dream_lite_runs
                           SET status = :st,
                               finished_at = now(),
                               episodes_consumed = :ec,
                               persona_updated = :pu,
                               precompute_queued = :pq,
                               memory_ops_applied = :ma,
                               cost_usd = :c,
                               error = :err
                         WHERE id = :id
                        """
                    ),
                    {
                        "st": status,
                        "ec": int(episodes_consumed),
                        "pu": bool(persona_updated),
                        "pq": int(precompute_queued),
                        "ma": int(memory_ops_applied),
                        "c": float(cost_usd),
                        "err": (error or None),
                        "id": int(run_id),
                    },
                )
        except Exception:
            logger.exception("dream_lite: finalize_run %s failed", run_id)


# ─────────────────────────────────────────────────────────────────────────
# Step 2 — Persona update (LITE call)
# ─────────────────────────────────────────────────────────────────────────
_PERSONA_PROMPT = """You are maintaining a per-user persona memory block.
Given the user's recent {n} chat episodes (questions + brief responses + reactions),
update their persona. Output STRICT JSON with keys:
  traits: list of short strings (e.g. "prefers concise answers", "interested in metrics")
  style: short string (e.g. "data-driven", "exploratory")
  preferences: object (e.g. {{"chart_type": "bar", "detail_level": "summary"}})
  recent_topics: list of short topic strings (max 5)

Constraints (STRICT):
  - Drop PII (names, ids, emails, phone numbers, exact dollar amounts).
  - Each list capped at 8 items.
  - No prose. JSON only.

PREVIOUS PERSONA (may be empty):
{prev_persona}

RECENT EPISODES:
{episodes_json}
"""


def _load_existing_persona(eng, project_slug: str, user_id: Optional[int]) -> Dict[str, Any]:
    if user_id is None:
        return {}
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT persona, version
                    FROM public.dash_dream_personas
                    WHERE project_slug = :p AND user_id = :uid
                    LIMIT 1
                    """
                ),
                {"p": project_slug, "uid": int(user_id)},
            ).mappings().first()
        if row and isinstance(row["persona"], (dict, str)):
            persona = row["persona"]
            if isinstance(persona, str):
                try:
                    persona = json.loads(persona)
                except Exception:
                    persona = {}
            return {"persona": persona, "version": int(row["version"] or 1)}
    except Exception:
        logger.debug("dream_lite: load existing persona failed", exc_info=True)
    return {}


def _save_persona(
    eng,
    project_slug: str,
    user_id: int,
    persona: Dict[str, Any],
    source_run_id: Optional[int],
) -> bool:
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO public.dash_dream_personas
                      (project_slug, user_id, persona, version,
                       source_dream_run_id, updated_at, created_at)
                    VALUES (:p, :uid, CAST(:per AS jsonb), 1, NULL, now(), now())
                    ON CONFLICT (project_slug, user_id) DO UPDATE
                      SET persona = EXCLUDED.persona,
                          version = public.dash_dream_personas.version + 1,
                          updated_at = now()
                    """
                ),
                {
                    "p": project_slug,
                    "uid": int(user_id),
                    "per": json.dumps(persona, ensure_ascii=False),
                },
            )
        return True
    except Exception:
        logger.exception("dream_lite: save_persona failed")
        return False


def _episodes_to_compact_json(episodes: List[Dict[str, Any]]) -> str:
    items = []
    for e in episodes[:_MAX_EPISODES_PER_CYCLE]:
        items.append(
            {
                "q": str(e.get("question") or "")[:300],
                "a": str(e.get("response_summary") or "")[:300],
                "reaction": str(e.get("user_reaction") or "")[:50],
                "poi": int(e.get("poignancy") or 0),
                "ok": bool(e.get("succeeded", True)),
            }
        )
    try:
        return json.dumps(items, ensure_ascii=False)[:8000]
    except Exception:
        return "[]"


def _update_persona(
    eng,
    project_slug: str,
    user_id: Optional[int],
    episodes: List[Dict[str, Any]],
    run_id: Optional[int],
) -> bool:
    if user_id is None or not episodes:
        return False
    prev = _load_existing_persona(eng, project_slug, user_id)
    prev_str = json.dumps(prev.get("persona") or {}, ensure_ascii=False)[:2000]
    prompt = _PERSONA_PROMPT.format(
        n=len(episodes),
        prev_persona=prev_str,
        episodes_json=_episodes_to_compact_json(episodes),
    )
    try:
        raw = training_llm_call(prompt, "extraction")
    except Exception:
        logger.exception("dream_lite: persona LLM call failed")
        return False
    parsed = _safe_parse_json(raw or "")
    if not isinstance(parsed, dict):
        return False
    # Sanitize: cap list sizes
    for k in ("traits", "recent_topics"):
        if isinstance(parsed.get(k), list):
            parsed[k] = [str(v)[:200] for v in parsed[k][:8]]
    if "preferences" in parsed and not isinstance(parsed.get("preferences"), dict):
        parsed["preferences"] = {}
    return _save_persona(eng, project_slug, int(user_id), parsed, run_id)


# ─────────────────────────────────────────────────────────────────────────
# Step 3 — Anticipated questions (LITE call)
# ─────────────────────────────────────────────────────────────────────────
_ANTICIPATE_PROMPT = """Based on the user's recent chat episodes below, predict the top {k}
next questions they're likely to ask. Focus on natural follow-ups, drill-downs,
or related comparisons.

Output STRICT JSON: {{"questions": ["...", "...", "..."]}}
Constraints:
  - Each question a single sentence, <= 200 chars.
  - No PII, no specific dollar amounts.
  - Cap {k} items.

RECENT EPISODES:
{episodes_json}
"""


def _anticipate_questions(
    project_slug: str,
    user_id: Optional[int],
    session_id: str,
    episodes: List[Dict[str, Any]],
) -> int:
    # Retired: the only consumer of anticipated questions was the
    # sleep-time precompute pipeline (dream_precompute), which has been
    # removed. The dream-lite cycle now just updates persona + consumes
    # episodes; question anticipation is a no-op so we avoid a wasted LLM
    # call with no downstream consumer.
    return 0


# ─────────────────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────────────────
def run_lite_cycle(
    project_slug: str,
    session_id: str,
    user_id: Optional[int] = None,
    trigger_reason: str = "poignancy_threshold",
) -> dict:
    """Run one Tier 2 between-turn dream-lite cycle. Never raises."""
    result: Dict[str, Any] = {
        "run_id": 0,
        "status": "failed",
        "episodes_consumed": 0,
        "persona_updated": False,
        "precompute_queued": 0,
        "cost_usd": 0.0,
        "error": None,
    }

    eng = None
    run_id: Optional[int] = None
    t_start = time.time()
    lite_calls = 0

    try:
        eng = _engine()
        run_id = _create_run(eng, project_slug, session_id, user_id, trigger_reason)
        if run_id is None:
            result["error"] = "failed_to_create_run_row"
            return result
        result["run_id"] = run_id

        # Step 1: pull unconsumed episodes for this session
        episodes: List[Dict[str, Any]] = []
        try:
            episodes = dream_poignancy.fetch_unconsumed(
                project_slug=project_slug,
                session_id=session_id,
                limit=_MAX_EPISODES_PER_CYCLE,
            )
        except Exception:
            logger.exception("dream_lite: fetch_unconsumed failed")

        if not episodes:
            # Not a failure — hook fired before any chat episodes existed.
            # Use friendly bootstrap status so UI doesn't render as red/failed.
            _finalize_run(
                eng, run_id, "bootstrap_ok",
                episodes_consumed=0,
                cost_usd=0.0,
                is_bootstrap=True,
                friendly_status="Hook fired · awaiting chat traffic",
            )
            result["status"] = "bootstrap_ok"
            result["is_bootstrap"] = True
            result["friendly_status"] = "Hook fired · awaiting chat traffic"
            result["trigger_reason"] = trigger_reason or "first_training"
            return result

        # Step 2: persona update (1 LITE call)
        persona_updated = False
        if (
            time.time() - t_start < _MAX_WALL_TIME_S
            and lite_calls < _MAX_LITE_CALLS
            and (lite_calls + 1) * _LITE_COST_PER_CALL <= _BUDGET_CAP_USD
            and user_id is not None
        ):
            try:
                persona_updated = _update_persona(
                    eng, project_slug, user_id, episodes, run_id
                )
                lite_calls += 1
            except Exception:
                logger.exception("dream_lite: persona step failed")
        result["persona_updated"] = persona_updated

        # Step 3: anticipated queries (1 LITE call)
        precompute_queued = 0
        if (
            time.time() - t_start < _MAX_WALL_TIME_S
            and lite_calls < _MAX_LITE_CALLS
            and (lite_calls + 1) * _LITE_COST_PER_CALL <= _BUDGET_CAP_USD
        ):
            try:
                precompute_queued = _anticipate_questions(
                    project_slug=project_slug,
                    user_id=user_id,
                    session_id=session_id,
                    episodes=episodes,
                )
                lite_calls += 1
            except Exception:
                logger.exception("dream_lite: anticipate step failed")
        result["precompute_queued"] = precompute_queued

        # Mark episodes consumed (only after pipeline succeeds)
        episode_ids = [int(e["id"]) for e in episodes if e.get("id")]
        try:
            dream_poignancy.mark_consumed(episode_ids)
        except Exception:
            logger.exception("dream_lite: mark_consumed failed")
        result["episodes_consumed"] = len(episode_ids)

        cost_usd = round(lite_calls * _LITE_COST_PER_CALL, 4)
        result["cost_usd"] = cost_usd

        _finalize_run(
            eng, run_id, "done",
            episodes_consumed=len(episode_ids),
            persona_updated=persona_updated,
            precompute_queued=precompute_queued,
            memory_ops_applied=0,
            cost_usd=cost_usd,
            friendly_status=f"Reflection complete · {len(episode_ids)} episodes consumed",
        )
        result["status"] = "done"
        result["friendly_status"] = f"Reflection complete · {len(episode_ids)} episodes consumed"
        return result

    except Exception as exc:
        logger.exception("dream_lite: cycle failed for %s/%s", project_slug, session_id)
        err = f"{exc.__class__.__name__}: {exc}"[:1500]
        result["error"] = err
        result["status"] = "failed"
        if eng is not None and run_id is not None:
            _finalize_run(
                eng, run_id, "failed",
                episodes_consumed=result.get("episodes_consumed", 0),
                persona_updated=result.get("persona_updated", False),
                precompute_queued=result.get("precompute_queued", 0),
                cost_usd=result.get("cost_usd", 0.0),
                error=err,
            )
        return result


__all__ = ["run_lite_cycle"]
