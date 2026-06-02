"""kpt pattern #15: Cost-ROI gate (skip low-value questions).

Before researching a question, estimate expected cost (USD) and expected info
gain (0..1). If ``expected_cost > expected_info_gain * VALUE_PER_UNIT``, skip
the question — kpt's "don't pay $0.20 to learn something worth $0.05".

Fires only when ``KPT_ROI_GATE=1``. Hooked into ``cycle.py`` at the top
of the per-question loop, before research starts. Pure heuristic — no LLM call,
so the gate itself costs zero.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Tunable knobs (intentionally simple — no admin setting yet)
VALUE_PER_UNIT_USD = 0.50  # 1.0 info-gain unit "worth" this much
DEFAULT_COST_USD = 0.05    # baseline cost we assume per research call
MIN_INFO_GAIN = 0.10       # below this, always skip


@dataclass
class ROIDecision:
    skip: bool
    expected_cost: float
    expected_gain: float
    reason: str


def _expected_cost(qobj) -> float:
    """Heuristic cost estimate based on question metadata."""
    meta = getattr(qobj, "metadata", None) or {}
    if isinstance(meta.get("estimated_cost_usd"), (int, float)):
        return float(meta["estimated_cost_usd"])
    # Longer questions tend to need more sources → linearly scale up
    qlen = len(getattr(qobj, "question", "") or "")
    return DEFAULT_COST_USD * (1.0 + qlen / 500.0)


def _expected_gain(qobj) -> float:
    """Heuristic info-gain estimate. 'gap' reasons → high; 'cycle_followup' → med;
    'restate' / 'duplicate' → low."""
    reason = (getattr(qobj, "reason", "") or "").lower()
    if reason in ("gap", "novel", "frontier"):
        return 0.8
    if reason in ("cycle_followup", "branch", "deepen"):
        return 0.55
    if reason in ("restate", "duplicate", "rephrase"):
        return 0.05
    return 0.4


def evaluate(qobj) -> ROIDecision:
    cost = _expected_cost(qobj)
    gain = _expected_gain(qobj)
    if gain < MIN_INFO_GAIN:
        return ROIDecision(True, cost, gain, f"info_gain {gain:.2f} below floor {MIN_INFO_GAIN}")
    if cost > gain * VALUE_PER_UNIT_USD:
        return ROIDecision(
            True, cost, gain,
            f"cost ${cost:.3f} > gain {gain:.2f} × ${VALUE_PER_UNIT_USD:.2f}",
        )
    return ROIDecision(False, cost, gain, "ok")


def is_enabled() -> bool:
    return os.getenv("KPT_ROI_GATE", "0") in ("1", "true", "True")
