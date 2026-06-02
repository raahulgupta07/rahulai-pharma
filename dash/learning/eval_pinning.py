"""kpt pattern #13: Eval pinning + regression detection.

Each cycle, run a small pinned eval suite against the current cycle's verified
hypotheses / answered questions and emit a regression alert when the score
drops more than ``REGRESSION_THRESHOLD`` versus the trailing average.

This is a lightweight, *passive* sentinel — it never blocks the cycle. Fires
only when ``KPT_EVAL_PINNING=1`` is set on the dash-api process.

Triggered from ``cycle.py`` after Phase 5 (agent_iq snapshot) so we have the
final per-cycle counters available.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

REGRESSION_THRESHOLD = 0.15  # 15% drop vs trailing avg → alert
TRAILING_WINDOW = 5


@dataclass
class EvalPinResult:
    score: float
    trailing_avg: float
    regression: bool
    delta: float


def _current_score(verified: int, formed: int, failed: int) -> float:
    total = max(1, formed)
    # verified/formed minus a small penalty for failures
    return max(0.0, min(1.0, (verified / total) - 0.1 * (failed / total)))


def _trailing_avg(project_slug: Optional[str]) -> float:
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine

        with get_sql_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT AVG(CASE WHEN hypotheses_formed > 0 "
                "  THEN (hypotheses_verified::float / hypotheses_formed) "
                "         - 0.1 * (hypotheses_failed::float / hypotheses_formed) "
                "  ELSE NULL END) "
                "FROM (SELECT hypotheses_formed, hypotheses_verified, hypotheses_failed "
                "      FROM public.dash_self_learning_runs "
                "      WHERE project_slug IS NOT DISTINCT FROM :s "
                "        AND status = 'completed' "
                "      ORDER BY started_at DESC LIMIT :n) t"
            ), {"s": project_slug, "n": TRAILING_WINDOW}).fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
    except Exception as e:
        logger.warning(f"eval_pinning trailing_avg failed: {e}")
        return 0.0


def check(project_slug: Optional[str], verified: int, formed: int, failed: int) -> EvalPinResult:
    """Compute current score and compare against trailing window."""
    score = _current_score(verified, formed, failed)
    avg = _trailing_avg(project_slug)
    delta = score - avg
    regression = avg > 0.0 and delta <= -REGRESSION_THRESHOLD
    return EvalPinResult(score=score, trailing_avg=avg, regression=regression, delta=delta)


def is_enabled() -> bool:
    return os.getenv("KPT_EVAL_PINNING", "0") in ("1", "true", "True")
