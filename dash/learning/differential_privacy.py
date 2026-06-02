"""ε-Differential Privacy noise utilities for numeric SQL aggregates.

Laplace mechanism: noise ~ Lap(sensitivity / epsilon). Lower ε = more privacy.
Counts → integer Laplace noise. Sums → float Laplace.

Per-(project, user, day) budget tracker in dash.dash_dp_budget. Gated by
feature_config.privacy.differential_privacy_enabled (default False, opt-in).

Usage:
    from dash.learning.differential_privacy import noisy_count, apply_dp_to_result

    n = noisy_count(true_count=1234, epsilon=0.5)
    rows = apply_dp_to_result(rows, numeric_cols=['revenue'], epsilon=1.0)
"""
from __future__ import annotations

import logging
import math
import random
from datetime import date
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Core Laplace mechanism
# ----------------------------------------------------------------------------
def _laplace_sample(scale: float) -> float:
    """Sample from Laplace(0, scale) via inverse CDF."""
    if scale <= 0:
        return 0.0
    u = random.random() - 0.5
    sign = 1 if u >= 0 else -1
    return -scale * sign * math.log(1 - 2 * abs(u))


def add_laplace_noise(value: float, epsilon: float = 1.0, sensitivity: float = 1.0) -> float:
    """Add Laplace(0, sensitivity/epsilon) noise to a scalar."""
    if epsilon <= 0:
        raise ValueError("epsilon must be > 0")
    if sensitivity < 0:
        raise ValueError("sensitivity must be >= 0")
    scale = sensitivity / epsilon
    return float(value) + _laplace_sample(scale)


def noisy_count(true_count: int, epsilon: float = 1.0) -> int:
    """DP count. Sensitivity = 1 (one row add/remove → count ±1). Clamped >= 0."""
    noisy = add_laplace_noise(float(true_count), epsilon=epsilon, sensitivity=1.0)
    return max(0, int(round(noisy)))


def noisy_sum(true_sum: float, sensitivity: float, epsilon: float = 1.0) -> float:
    """DP sum. Caller must supply sensitivity (max value bound)."""
    return add_laplace_noise(float(true_sum), epsilon=epsilon, sensitivity=sensitivity)


def _infer_sensitivity(values: list[float]) -> float:
    """Best-effort sensitivity = max absolute value in the column."""
    if not values:
        return 1.0
    try:
        return max(abs(float(v)) for v in values if v is not None) or 1.0
    except Exception:
        return 1.0


def apply_dp_to_result(
    result: list[dict],
    numeric_cols: list[str],
    epsilon: float = 1.0,
    sensitivities: dict[str, float] | None = None,
) -> list[dict]:
    """Apply DP noise to specified numeric columns across all rows.

    Splits ε budget equally across columns (sequential composition).
    Returns new list of dicts (does not mutate input).
    """
    if not result or not numeric_cols:
        return result
    sensitivities = sensitivities or {}
    n_cols = max(1, len(numeric_cols))
    per_eps = epsilon / n_cols
    # Pre-infer sensitivities from data when not supplied
    col_sens: dict[str, float] = {}
    for col in numeric_cols:
        if col in sensitivities:
            col_sens[col] = float(sensitivities[col])
        else:
            vals = [r.get(col) for r in result if r.get(col) is not None]
            col_sens[col] = _infer_sensitivity([v for v in vals if isinstance(v, (int, float))])
    out: list[dict] = []
    for row in result:
        new_row = dict(row)
        for col in numeric_cols:
            v = new_row.get(col)
            if v is None or not isinstance(v, (int, float)):
                continue
            noisy = add_laplace_noise(float(v), epsilon=per_eps, sensitivity=col_sens[col])
            if isinstance(v, int):
                new_row[col] = max(0, int(round(noisy)))
            else:
                new_row[col] = round(noisy, 4)
        out.append(new_row)
    return out


# ----------------------------------------------------------------------------
# Budget tracker
# ----------------------------------------------------------------------------
def _engine():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from db import db_url
    return create_engine(db_url, poolclass=NullPool)


def _ensure_budget_row(conn, project_slug: str, user_id: int, today: date) -> None:
    from sqlalchemy import text
    conn.execute(text("""
        INSERT INTO dash.dash_dp_budget (project_slug, user_id, date, budget_used, budget_max)
        VALUES (:s, :u, :d, 0, 10.0)
        ON CONFLICT (project_slug, user_id, date) DO NOTHING
    """), {"s": project_slug, "u": user_id, "d": today})


def dp_budget_remaining(project_slug: str, user_id: int) -> float:
    """Return remaining ε for today. Creates row on first call."""
    from sqlalchemy import text
    eng = _engine()
    try:
        with eng.begin() as conn:
            today = date.today()
            _ensure_budget_row(conn, project_slug, user_id, today)
            row = conn.execute(text("""
                SELECT budget_max - budget_used FROM dash.dash_dp_budget
                WHERE project_slug=:s AND user_id=:u AND date=:d
            """), {"s": project_slug, "u": user_id, "d": today}).first()
            return float(row[0]) if row else 0.0
    finally:
        eng.dispose()


def consume_budget(project_slug: str, user_id: int, epsilon: float) -> tuple[bool, float]:
    """Atomically deduct ε from today's budget. Returns (ok, remaining_after)."""
    from sqlalchemy import text
    eng = _engine()
    try:
        with eng.begin() as conn:
            today = date.today()
            _ensure_budget_row(conn, project_slug, user_id, today)
            row = conn.execute(text("""
                UPDATE dash.dash_dp_budget
                SET budget_used = budget_used + :e
                WHERE project_slug=:s AND user_id=:u AND date=:d
                  AND budget_used + :e <= budget_max
                RETURNING budget_max - budget_used
            """), {"s": project_slug, "u": user_id, "d": today, "e": float(epsilon)}).first()
            if row is None:
                rem = conn.execute(text("""
                    SELECT budget_max - budget_used FROM dash.dash_dp_budget
                    WHERE project_slug=:s AND user_id=:u AND date=:d
                """), {"s": project_slug, "u": user_id, "d": today}).first()
                return (False, float(rem[0]) if rem else 0.0)
            return (True, float(row[0]))
    finally:
        eng.dispose()


def set_budget_max(project_slug: str, user_id: int, new_max: float) -> None:
    from sqlalchemy import text
    eng = _engine()
    try:
        with eng.begin() as conn:
            today = date.today()
            _ensure_budget_row(conn, project_slug, user_id, today)
            conn.execute(text("""
                UPDATE dash.dash_dp_budget SET budget_max = :m
                WHERE project_slug=:s AND user_id=:u AND date=:d
            """), {"s": project_slug, "u": user_id, "d": today, "m": float(new_max)})
    finally:
        eng.dispose()


def is_dp_enabled(project_slug: str) -> bool:
    """Read feature_config.privacy.differential_privacy_enabled (default False)."""
    from sqlalchemy import text
    eng = _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(text("""
                SELECT feature_config FROM public.dash_projects WHERE slug=:s
            """), {"s": project_slug}).first()
            if not row or not row[0]:
                return False
            cfg = row[0]
            if isinstance(cfg, str):
                import json
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    return False
            return bool((cfg or {}).get("privacy", {}).get("differential_privacy_enabled", False))
    except Exception as e:
        logger.warning(f"differential_privacy: feature flag check failed: {e}")
        return False
    finally:
        eng.dispose()


__all__ = [
    "add_laplace_noise",
    "noisy_count",
    "noisy_sum",
    "apply_dp_to_result",
    "dp_budget_remaining",
    "consume_budget",
    "set_budget_max",
    "is_dp_enabled",
]
