"""Venture Reward Signal — self-learning loop for the deal-analyst tool.

Tracks `verdict='go'` scenarios over time and flips the reward negative when
a "go" deal later gets archived / passed (false-positive) or a "pass" deal
later closes (false-negative). Aggregates feed the SkillRefinery nightly
cycle so the deal-analyst tool's prompt / defaults can self-improve.

The existing `public.dash_tool_utility_scores` table is per-call telemetry
(tool_name + success/latency/error). It doesn't fit deal-level reward
semantics cleanly, so we write to a dedicated `dash.dash_venture_rewards`
table, bootstrapped on first run via `_ensure_table()`.

All functions are sync + fail-soft. They NEVER raise to caller.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Reward table bootstrap ────────────────────────────────────────────

_CREATE_REWARDS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dash.dash_venture_rewards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id UUID NOT NULL,
    project_slug TEXT NOT NULL,
    scenario_verdict TEXT,
    deal_status TEXT,
    reward NUMERIC NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_venture_rewards_proj_created
    ON dash.dash_venture_rewards (project_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_venture_rewards_deal
    ON dash.dash_venture_rewards (deal_id);
"""

_REWARDS_TABLE_ENSURED = False


def _ensure_table() -> bool:
    global _REWARDS_TABLE_ENSURED
    if _REWARDS_TABLE_ENSURED:
        return True
    try:
        from db.session import get_write_engine
        from sqlalchemy import text as _t
        eng = get_write_engine()
        with eng.begin() as cn:
            cn.execute(_t(_CREATE_REWARDS_TABLE_SQL))
        _REWARDS_TABLE_ENSURED = True
        return True
    except Exception:
        logger.exception("venture_reward: _ensure_table failed")
        return False


# ── Reward computation ───────────────────────────────────────────────


def _compute_reward(verdict: str | None, deal_status: str | None
                    ) -> tuple[float, str]:
    """Mapping:
      verdict=go   + status=closed → +1.0 (true positive)
      verdict=go   + status=pass   → -1.0 (false positive)
      verdict=pass + status=closed → -0.5 (false negative)
      verdict=hold                 →  0.0
      else                          →  0.0 (neutral / pending)
    """
    v = (verdict or "").lower()
    s = (deal_status or "").lower()

    if v == "go" and s == "closed":
        return 1.0, "true_positive: go-verdict deal closed"
    if v == "go" and s == "pass":
        return -1.0, "false_positive: go-verdict deal was passed"
    if v == "pass" and s == "closed":
        return -0.5, "false_negative: pass-verdict deal closed"
    if v == "hold":
        return 0.0, "hold_verdict: neutral"
    return 0.0, f"neutral: verdict={v or 'none'} status={s or 'none'}"


# ── Public API ───────────────────────────────────────────────────────


def reward_for_deal(deal_id: str) -> dict[str, Any]:
    """Pull deal status + most-recent scenario verdict, compute reward,
    persist. Returns {ok, reward, reason, scenario_verdict, deal_status}.
    Fail-soft: returns {ok: False, error} on any failure, never raises.
    """
    if not deal_id:
        return {"ok": False, "error": "missing_deal_id"}

    if not _ensure_table():
        return {"ok": False, "error": "ensure_table_failed"}

    try:
        from db.session import get_sql_engine, get_write_engine
        from sqlalchemy import text as _t
    except Exception:
        logger.exception("venture_reward: engine import failed")
        return {"ok": False, "error": "engine_import"}

    try:
        sql_eng = get_sql_engine()
        write_eng = get_write_engine()
    except Exception:
        logger.exception("venture_reward: engine init failed")
        return {"ok": False, "error": "engine_init"}

    deal_status: str | None = None
    project_slug: str | None = None
    verdict: str | None = None

    # 1) Deal row.
    try:
        with sql_eng.connect() as cn:
            row = cn.execute(_t(
                "SELECT status, project_slug FROM dash.dash_venture_deals "
                " WHERE id = CAST(:d AS uuid)"
            ), {"d": deal_id}).fetchone()
            if not row:
                return {"ok": False, "error": "deal_not_found"}
            deal_status = row[0]
            project_slug = row[1]

            # 2) Most-recent scenario verdict.
            scen = cn.execute(_t(
                "SELECT verdict FROM dash.dash_venture_scenarios "
                " WHERE deal_id = CAST(:d AS uuid) "
                " ORDER BY created_at DESC LIMIT 1"
            ), {"d": deal_id}).fetchone()
            verdict = scen[0] if scen else None
    except Exception:
        logger.exception("venture_reward: deal/scenario read failed deal=%s",
                         deal_id)
        return {"ok": False, "error": "db_read"}

    reward, reason = _compute_reward(verdict, deal_status)

    # 3) Persist (fail-soft).
    try:
        with write_eng.begin() as cn:
            cn.execute(_t(
                """
                INSERT INTO dash.dash_venture_rewards
                    (deal_id, project_slug, scenario_verdict, deal_status,
                     reward, reason)
                VALUES (CAST(:d AS uuid), :p, :v, :s, :r, :rsn)
                """
            ), {
                "d": deal_id,
                "p": project_slug or "",
                "v": verdict,
                "s": deal_status,
                "r": reward,
                "rsn": reason,
            })
    except Exception:
        logger.exception("venture_reward: persist failed deal=%s", deal_id)
        # Still return computed reward — write failure is non-fatal for caller.

    return {
        "ok": True,
        "reward": reward,
        "reason": reason,
        "scenario_verdict": verdict,
        "deal_status": deal_status,
    }


def aggregate_rewards(project_slug: str, days: int = 90) -> dict[str, Any]:
    """Sum rewards in window. Returns {total_reward, sample_count,
    by_verdict, accuracy_pct}. Accuracy = (positive rewards) /
    (positive + negative rewards). Fail-soft."""
    if not project_slug:
        return {"ok": False, "error": "missing_project_slug"}

    if not _ensure_table():
        return {
            "ok": True,
            "total_reward": 0.0,
            "sample_count": 0,
            "by_verdict": {"go": 0, "pass": 0, "hold": 0},
            "accuracy_pct": 0.0,
            "warning": "rewards_table_unavailable",
        }

    try:
        from db.session import get_sql_engine
        from sqlalchemy import text as _t
        eng = get_sql_engine()
    except Exception:
        logger.exception("venture_reward: engine init failed")
        return {"ok": False, "error": "engine_init"}

    try:
        days_int = max(1, int(days))
    except Exception:
        days_int = 90

    try:
        with eng.connect() as cn:
            rows = cn.execute(_t(
                """
                SELECT scenario_verdict, reward
                FROM dash.dash_venture_rewards
                WHERE project_slug = :p
                  AND created_at > now() - (CAST(:days AS INT) * INTERVAL '1 day')
                """
            ), {"p": project_slug, "days": days_int}).fetchall()
    except Exception:
        logger.exception("venture_reward: aggregate read failed slug=%s",
                         project_slug)
        return {"ok": False, "error": "db_read"}

    by_verdict = {"go": 0, "pass": 0, "hold": 0}
    total_reward = 0.0
    pos = 0
    neg = 0

    for r in rows:
        v = (r[0] or "").lower()
        try:
            rw = float(r[1] or 0.0)
        except Exception:
            rw = 0.0
        total_reward += rw
        if v in by_verdict:
            by_verdict[v] += 1
        if rw > 0:
            pos += 1
        elif rw < 0:
            neg += 1

    decided = pos + neg
    accuracy_pct = round((pos / decided) * 100.0, 2) if decided else 0.0

    return {
        "ok": True,
        "total_reward": round(total_reward, 4),
        "sample_count": len(rows),
        "by_verdict": by_verdict,
        "accuracy_pct": accuracy_pct,
        "decided_samples": decided,
    }


def score_deal_analyst(project_slug: str, days: int = 90) -> dict[str, Any]:
    """Wrapper: compute accuracy_pct and emit a score for the deal_analyst
    tool. Fail-soft. Returns {ok, project_slug, days, accuracy_pct,
    total_reward, sample_count, by_verdict, score}.

    `score` is a 0-100 utility score derived from accuracy + sample
    confidence. Low sample_count damps the score so a single +1 doesn't
    look like 100%.
    """
    agg = aggregate_rewards(project_slug, days=days)
    if not agg.get("ok"):
        return {"ok": False, "error": agg.get("error", "aggregate_failed"),
                "project_slug": project_slug, "days": days}

    accuracy = float(agg.get("accuracy_pct") or 0.0)
    decided = int(agg.get("decided_samples") or 0)

    # Confidence damp: full weight at 10+ decided samples, linear below.
    confidence = min(1.0, decided / 10.0)
    score = round(accuracy * confidence, 2)

    return {
        "ok": True,
        "project_slug": project_slug,
        "days": days,
        "accuracy_pct": accuracy,
        "total_reward": agg.get("total_reward"),
        "sample_count": agg.get("sample_count"),
        "decided_samples": decided,
        "by_verdict": agg.get("by_verdict"),
        "score": score,
        "tool": "deal_analyst",
    }


__all__ = ["reward_for_deal", "aggregate_rewards", "score_deal_analyst"]
