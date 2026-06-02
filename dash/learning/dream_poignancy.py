"""Dream Poignancy — Tier 1 rule-based per-turn poignancy capture.

Sync, fast (<10ms), no LLM cost. Inserts into dash.dash_episode_buffer
with a rule-based 1-10 poignancy score, enforcing a rolling LRU cap
of 1000 rows per project.

Public surface:
    classify_poignancy(turn) -> int
    capture_turn(...) -> int
    fetch_unconsumed(...) -> list[dict]
    mark_consumed(ids) -> None
    session_poignancy_sum(session_id) -> int
    register_post_chat_hook(result) -> int | None
    batch_recover(project_slug, limit=200) -> dict

Module is sync-safe + thread-safe. Never raises from public functions.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────
_LRU_CAP_PER_PROJECT = 1000
_LRU_EVICT_BATCH = 100
_MAX_RECOVER_PER_RUN = 200

# Keyword sets for reaction detection
_POSITIVE_PHRASES = (
    "thanks", "thank you", "perfect", "exactly", "great", "awesome",
    "excellent", "love it",
)
_NEGATIVE_PHRASES = (
    "no, ", "no.", "wrong", "not right", "incorrect", "that's not",
    "that is not", "not what", "doesn't match", "does not match",
)


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ─────────────────────────────────────────────────────────────────────────
# Poignancy classifier (rule-based, no LLM)
# ─────────────────────────────────────────────────────────────────────────
def classify_poignancy(turn: Dict[str, Any]) -> int:
    """Rule-based 1-10 score per turn. Cheap, sync, no LLM.

    Signals (additive):
      +3  user said thanks/perfect/exactly/great
      +5  user said no/wrong/not right/incorrect
      +2  judge_score >= 4
      +4  judge_score <= 2
      +6  same question 2nd time in same session
      +3  tool error encountered
      +2  auto-correction triggered
      +1  new entity in KG triples
      base 1 default
    Cap at 10.
    """
    score = 1
    try:
        question = str(turn.get("question") or "").lower()
        response = str(turn.get("response") or turn.get("response_summary") or "").lower()
        user_reaction = str(turn.get("user_reaction") or "").lower()
        tools_used = turn.get("tools_used") or []
        succeeded = bool(turn.get("succeeded", True))
        judge_score = turn.get("judge_score")
        repeat = bool(turn.get("repeat_question", False))
        auto_correction = bool(turn.get("auto_correction", False))
        new_entity = bool(turn.get("new_entity", False))

        text_blob = " ".join([question, response, user_reaction])

        if any(p in text_blob for p in _POSITIVE_PHRASES):
            score += 3
        if any(p in text_blob for p in _NEGATIVE_PHRASES):
            score += 5
        if user_reaction in {"thanks", "thank_you"}:
            score += 3
        if user_reaction in {"correction", "wrong"}:
            score += 5

        if judge_score is not None:
            try:
                js = float(judge_score)
                if js >= 4:
                    score += 2
                elif js <= 2:
                    score += 4
            except Exception:
                pass

        if repeat:
            score += 6
        if not succeeded:
            score += 3
        if auto_correction:
            score += 2
        if new_entity:
            score += 1
        # Tool errors heuristic
        if isinstance(tools_used, (list, tuple)):
            for t in tools_used:
                if isinstance(t, str) and "error" in t.lower():
                    score += 3
                    break
    except Exception:
        logger.exception("classify_poignancy: scoring failed; default=1")
        return 1
    return max(1, min(10, int(score)))


# ─────────────────────────────────────────────────────────────────────────
# Detect repeat question
# ─────────────────────────────────────────────────────────────────────────
_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize_question(q: str) -> str:
    if not q:
        return ""
    return " ".join(_WORD_RE.findall(q.lower()))[:500]


def _was_question_asked_recently(eng, session_id: str, question: str) -> bool:
    try:
        norm = _normalize_question(question)
        if not norm or len(norm) < 4:
            return False
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT question
                    FROM dash.dash_episode_buffer
                    WHERE session_id = :s
                    ORDER BY created_at DESC
                    LIMIT 20
                    """
                ),
                {"s": session_id},
            ).all()
        for r in rows:
            if _normalize_question(str(r[0] or "")) == norm:
                return True
    except Exception:
        return False
    return False


# ─────────────────────────────────────────────────────────────────────────
# LRU enforcement
# ─────────────────────────────────────────────────────────────────────────
def _enforce_lru(eng, project_slug: str) -> None:
    try:
        with eng.begin() as conn:
            cnt = conn.execute(
                text(
                    "SELECT COUNT(*) FROM dash.dash_episode_buffer "
                    "WHERE project_slug = :p"
                ),
                {"p": project_slug},
            ).scalar() or 0
            if int(cnt) >= _LRU_CAP_PER_PROJECT:
                conn.execute(
                    text(
                        """
                        DELETE FROM dash.dash_episode_buffer
                        WHERE id IN (
                            SELECT id FROM dash.dash_episode_buffer
                            WHERE project_slug = :p
                            ORDER BY created_at ASC
                            LIMIT :n
                        )
                        """
                    ),
                    {"p": project_slug, "n": _LRU_EVICT_BATCH},
                )
    except Exception:
        logger.exception("_enforce_lru: failed for %s", project_slug)


# ─────────────────────────────────────────────────────────────────────────
# Public capture API
# ─────────────────────────────────────────────────────────────────────────
def capture_turn(
    session_id: str,
    turn_id: Optional[int],
    project_slug: str,
    user_id: Optional[int],
    question: str,
    response_summary: str,
    tools_used: Optional[List[str]] = None,
    succeeded: bool = True,
    judge_score: Optional[float] = None,
    user_reaction: Optional[str] = None,
) -> int:
    """Insert one episode_buffer row. Returns row id (0 on failure).

    Sync, fast (<10ms). Enforces rolling LRU cap.
    """
    try:
        eng = _engine()
    except Exception:
        logger.exception("capture_turn: engine acquire failed")
        return 0

    # Repeat detection
    repeat = False
    try:
        repeat = _was_question_asked_recently(eng, session_id, question or "")
    except Exception:
        pass

    poignancy = classify_poignancy(
        {
            "question": question,
            "response_summary": response_summary,
            "user_reaction": user_reaction,
            "tools_used": tools_used or [],
            "succeeded": succeeded,
            "judge_score": judge_score,
            "repeat_question": repeat,
        }
    )

    # LRU eviction before insert
    _enforce_lru(eng, project_slug)

    try:
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_episode_buffer
                      (session_id, turn_id, project_slug, user_id,
                       poignancy, question, response_summary, tools_used,
                       succeeded, judge_score, user_reaction, created_at)
                    VALUES
                      (:sid, :tid, :p, :uid,
                       :poi, :q, :rs, :tu,
                       :ok, :js, :ur, now())
                    RETURNING id
                    """
                ),
                {
                    "sid": str(session_id)[:200],
                    "tid": int(turn_id) if turn_id is not None else None,
                    "p": project_slug,
                    "uid": int(user_id) if user_id is not None else None,
                    "poi": int(poignancy),
                    "q": (question or "")[:4000],
                    "rs": (response_summary or "")[:4000],
                    "tu": list(tools_used or []),
                    "ok": bool(succeeded),
                    "js": float(judge_score) if judge_score is not None else None,
                    "ur": (user_reaction or None),
                },
            ).first()
            return int(row[0]) if row else 0
    except Exception:
        logger.exception("capture_turn: insert failed for %s/%s", project_slug, session_id)
        return 0


def fetch_unconsumed(
    project_slug: str,
    session_id: Optional[str] = None,
    min_poignancy: int = 0,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Pull unconsumed episode buffer rows."""
    try:
        eng = _engine()
        params: Dict[str, Any] = {
            "p": project_slug,
            "mp": int(min_poignancy),
            "lim": int(limit),
        }
        sql_filter = "AND session_id = :sid" if session_id else ""
        if session_id:
            params["sid"] = session_id
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT id, session_id, turn_id, project_slug, user_id,
                           poignancy, question, response_summary, tools_used,
                           succeeded, judge_score, user_reaction, created_at
                    FROM dash.dash_episode_buffer
                    WHERE project_slug = :p
                      AND consumed_at IS NULL
                      AND poignancy >= :mp
                      {sql_filter}
                    ORDER BY created_at DESC
                    LIMIT :lim
                    """
                ),
                params,
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("fetch_unconsumed: failed for %s", project_slug)
        return []


def mark_consumed(episode_ids: List[int]) -> None:
    """UPDATE consumed_at=now() WHERE id = ANY(:ids)."""
    if not episode_ids:
        return
    try:
        eng = _engine()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "UPDATE dash.dash_episode_buffer "
                    "SET consumed_at = now() "
                    "WHERE id = ANY(:ids) AND consumed_at IS NULL"
                ),
                {"ids": [int(i) for i in episode_ids]},
            )
    except Exception:
        logger.exception("mark_consumed: failed for %d ids", len(episode_ids))


def session_poignancy_sum(session_id: str) -> int:
    """Sum poignancy of unconsumed rows for the session."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            v = conn.execute(
                text(
                    "SELECT COALESCE(SUM(poignancy), 0) "
                    "FROM dash.dash_episode_buffer "
                    "WHERE session_id = :s AND consumed_at IS NULL"
                ),
                {"s": session_id},
            ).scalar()
        return int(v or 0)
    except Exception:
        logger.exception("session_poignancy_sum: failed for %s", session_id)
        return 0


# ─────────────────────────────────────────────────────────────────────────
# Post-chat hook (callable from chat finalize path)
# ─────────────────────────────────────────────────────────────────────────
def register_post_chat_hook(chat_result: Dict[str, Any]) -> Optional[int]:
    """Take a chat result dict and capture a turn.

    Expected keys (any subset; missing → defaults):
      session_id, turn_id, project_slug, user_id, question,
      response, response_summary, tools_used, succeeded,
      judge_score, user_reaction

    Returns the inserted row id (or None on failure / missing required).
    Never raises.
    """
    try:
        session_id = chat_result.get("session_id")
        project_slug = chat_result.get("project_slug") or chat_result.get("project")
        if not session_id or not project_slug:
            return None
        rid = capture_turn(
            session_id=str(session_id),
            turn_id=chat_result.get("turn_id"),
            project_slug=str(project_slug),
            user_id=chat_result.get("user_id"),
            question=str(chat_result.get("question") or ""),
            response_summary=str(
                chat_result.get("response_summary") or chat_result.get("response") or ""
            )[:4000],
            tools_used=chat_result.get("tools_used") or [],
            succeeded=bool(chat_result.get("succeeded", True)),
            judge_score=chat_result.get("judge_score"),
            user_reaction=chat_result.get("user_reaction"),
        )
        return rid or None
    except Exception:
        logger.exception("register_post_chat_hook: failed")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Batch recovery (minion handler)
# ─────────────────────────────────────────────────────────────────────────
def batch_recover(
    project_slug: Optional[str] = None,
    limit: int = _MAX_RECOVER_PER_RUN,
) -> Dict[str, Any]:
    """Scan recent dash_chat_sessions for turns NOT yet in dash_episode_buffer.

    Used by cron OR as recovery if hot-path hook fails. Cap 200 turns/run.
    """
    result: Dict[str, Any] = {
        "scanned_sessions": 0,
        "recovered_turns": 0,
        "skipped": 0,
        "errors": 0,
    }
    cap = min(int(limit or _MAX_RECOVER_PER_RUN), _MAX_RECOVER_PER_RUN)
    try:
        eng = _engine()
    except Exception:
        logger.exception("batch_recover: engine acquire failed")
        result["errors"] += 1
        return result

    try:
        params: Dict[str, Any] = {"lim": cap}
        proj_filter = ""
        if project_slug:
            proj_filter = "AND c.project_slug = :p"
            params["p"] = project_slug

        # Pull recent agno_sessions joined to chat sessions for project scope.
        with eng.connect() as conn:
            sess_rows = conn.execute(
                text(
                    f"""
                    SELECT a.session_id, a.runs, c.project_slug
                    FROM ai.agno_sessions a
                    JOIN public.dash_chat_sessions c
                      ON c.session_id = a.session_id
                    WHERE a.updated_at >= now() - INTERVAL '24 hours'
                      AND a.runs IS NOT NULL
                      {proj_filter}
                    ORDER BY a.updated_at DESC
                    LIMIT 100
                    """
                ),
                params,
            ).mappings().all()
    except Exception:
        logger.exception("batch_recover: session fetch failed")
        result["errors"] += 1
        return result

    if not sess_rows:
        return result

    recovered = 0
    for srow in sess_rows:
        if recovered >= cap:
            break
        result["scanned_sessions"] += 1
        sid = str(srow["session_id"])
        slug = str(srow["project_slug"])
        runs = srow["runs"]
        if isinstance(runs, str):
            try:
                import json as _json
                runs = _json.loads(runs)
            except Exception:
                result["errors"] += 1
                continue
        if not isinstance(runs, list):
            continue

        # Existing turn_ids for this session
        existing: set = set()
        try:
            with eng.connect() as conn:
                exr = conn.execute(
                    text(
                        "SELECT turn_id FROM dash.dash_episode_buffer "
                        "WHERE session_id = :s AND turn_id IS NOT NULL"
                    ),
                    {"s": sid},
                ).all()
            existing = {int(r[0]) for r in exr if r[0] is not None}
        except Exception:
            pass

        for idx, run in enumerate(runs):
            if recovered >= cap:
                break
            if not isinstance(run, dict):
                continue
            if run.get("parent_run_id"):
                continue
            if idx in existing:
                result["skipped"] += 1
                continue
            # Extract Q/A
            q = ""
            inp = run.get("input")
            if isinstance(inp, dict):
                q = inp.get("input_content") or inp.get("content") or ""
            elif isinstance(inp, str):
                q = inp
            a = run.get("content") or ""
            if not q and not a:
                continue
            rid = capture_turn(
                session_id=sid,
                turn_id=idx,
                project_slug=slug,
                user_id=None,
                question=str(q)[:4000],
                response_summary=str(a)[:4000],
                tools_used=[],
                succeeded=True,
            )
            if rid:
                recovered += 1
            else:
                result["errors"] += 1

    result["recovered_turns"] = recovered
    return result


__all__ = [
    "classify_poignancy",
    "capture_turn",
    "fetch_unconsumed",
    "mark_consumed",
    "session_poignancy_sum",
    "register_post_chat_hook",
    "batch_recover",
]
