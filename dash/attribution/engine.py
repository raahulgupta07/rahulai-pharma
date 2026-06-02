"""Multi-Touch Attribution engine.

Implements four attribution models — linear, time_decay, position-based, and
markov (data-driven removal effect) — plus orchestration helpers that read
touchpoints / conversions from PostgreSQL and persist computed credits to
``dash_attribution_credits``. All SQL is parameterized; per-project RLS is
enforced via ``project_slug`` filters on every query.

The Markov model uses NumPy matrix multiplication to compute conversion
probabilities under removal of each channel. Touchpoints per conversion are
capped at ``MAX_TOUCHPOINTS_PER_CONVERSION`` (default 100) for performance.

All public functions are idempotent — re-running the same model overwrites
prior credits via DELETE+INSERT inside a single transaction.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import numpy as np
from sqlalchemy import text as _t

logger = logging.getLogger(__name__)

# Performance cap — never attribute more than N touchpoints to a single
# conversion. Markov state-space and per-conversion writes scale with this.
MAX_TOUCHPOINTS_PER_CONVERSION = 100

# Allowed attribution model names — keep in sync with API/DB.
ALLOWED_MODELS = {"linear", "time_decay", "position", "markov"}


# ── Engine helper ──────────────────────────────────────────────────────────


def _engine():
    """Resolve a SQLAlchemy engine. Raises RuntimeError if unavailable."""
    from db.session import get_sql_engine
    eng = get_sql_engine()
    if eng is None:
        raise RuntimeError("db engine unavailable")
    return eng


# ── Pure model functions ──────────────────────────────────────────────────


def linear_attribution(touchpoints: list[dict]) -> list[tuple[int, float]]:
    """Equal-split credit across all touchpoints in lookback window.

    Args:
        touchpoints: list of dicts with at least ``id`` keys.

    Returns:
        List of ``(touchpoint_id, credit)`` tuples; credits sum to 1.0 (or 0
        if input is empty).
    """
    n = len(touchpoints)
    if n == 0:
        return []
    share = 1.0 / n
    return [(int(tp["id"]), share) for tp in touchpoints]


def time_decay_attribution(
    touchpoints: list[dict],
    half_life_days: float = 7.0,
    converted_at: datetime | None = None,
) -> list[tuple[int, float]]:
    """Exponential time-decay credit. More recent touchpoints earn more credit.

    Weight per touchpoint = ``2 ** (-delta_days / half_life_days)`` where
    delta_days is computed from the conversion timestamp (or the most recent
    touchpoint when ``converted_at`` is None).

    Args:
        touchpoints: list of dicts with ``id`` and ``event_at`` (datetime).
        half_life_days: half-life in days; default 7.
        converted_at: anchor timestamp. Defaults to max(event_at).

    Returns:
        List of ``(touchpoint_id, credit)``; credits sum to 1.0.
    """
    if not touchpoints:
        return []
    if converted_at is None:
        converted_at = max(_to_dt(tp.get("event_at")) for tp in touchpoints)
    weights: list[float] = []
    for tp in touchpoints:
        ts = _to_dt(tp.get("event_at"))
        delta_days = max(0.0, (converted_at - ts).total_seconds() / 86400.0)
        w = math.pow(2.0, -delta_days / max(0.01, half_life_days))
        weights.append(w)
    total = sum(weights)
    if total <= 0:
        return linear_attribution(touchpoints)
    return [(int(tp["id"]), w / total) for tp, w in zip(touchpoints, weights)]


def position_based_attribution(touchpoints: list[dict]) -> list[tuple[int, float]]:
    """Position-based U-shape: 40% first, 40% last, 20% split across middle.

    Single touchpoint → 100%. Two touchpoints → 50/50.
    """
    n = len(touchpoints)
    if n == 0:
        return []
    if n == 1:
        return [(int(touchpoints[0]["id"]), 1.0)]
    if n == 2:
        return [
            (int(touchpoints[0]["id"]), 0.5),
            (int(touchpoints[1]["id"]), 0.5),
        ]
    credits: list[tuple[int, float]] = []
    middle_share = 0.20 / (n - 2)
    for idx, tp in enumerate(touchpoints):
        if idx == 0:
            credits.append((int(tp["id"]), 0.40))
        elif idx == n - 1:
            credits.append((int(tp["id"]), 0.40))
        else:
            credits.append((int(tp["id"]), middle_share))
    return credits


def markov_attribution(
    touchpoints_per_conversion: list[list[dict]],
    target_touchpoint_ids: Iterable[int] | None = None,
) -> list[tuple[int, float]]:
    """Data-driven Markov removal-effect attribution.

    Builds a channel-level transition matrix from journey paths, computes the
    baseline conversion probability, then for each channel computes the
    removal-effect (drop in conversion prob when that channel is removed).
    Removal effects are normalized to sum to 1 and distributed back across
    each channel's touchpoint instances.

    Args:
        touchpoints_per_conversion: list of journeys (each a list of
            touchpoint dicts with ``id`` and ``channel``).
        target_touchpoint_ids: optional set of touchpoint ids to return
            credits for. If None, returns credits for every tp present.

    Returns:
        List of ``(touchpoint_id, credit)`` for the target tps. Credits sum
        to 1.0 across all touchpoints in the input (or to 0 if input empty).
    """
    if not touchpoints_per_conversion:
        return []

    # Collect channels.
    channels: list[str] = []
    seen: set[str] = set()
    for journey in touchpoints_per_conversion:
        for tp in journey:
            ch = str(tp.get("channel") or "unknown")
            if ch not in seen:
                seen.add(ch)
                channels.append(ch)
    if not channels:
        return []

    # State indices: 0..n-1 channels, n=START, n+1=CONVERT, n+2=NULL.
    n = len(channels)
    idx_of = {ch: i for i, ch in enumerate(channels)}
    START, CONVERT, NULL = n, n + 1, n + 2
    size = n + 3

    # Build raw transition counts.
    counts = np.zeros((size, size), dtype=np.float64)
    total_journeys = len(touchpoints_per_conversion)
    for journey in touchpoints_per_conversion:
        if not journey:
            continue
        prev = START
        for tp in journey:
            cur = idx_of[str(tp.get("channel") or "unknown")]
            counts[prev, cur] += 1
            prev = cur
        counts[prev, CONVERT] += 1  # last touchpoint → CONVERT

    # Add a NULL absorbing path proportional to projects without conversion.
    # In our pipeline only converters are passed in, so NULL gets a nominal
    # weight to avoid degenerate matrices.
    counts[START, NULL] = max(1.0, total_journeys * 0.01)

    # Row-normalize → transition matrix P.
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    P = counts / row_sums

    # Make CONVERT and NULL absorbing.
    P[CONVERT, :] = 0.0
    P[CONVERT, CONVERT] = 1.0
    P[NULL, :] = 0.0
    P[NULL, NULL] = 1.0

    # Baseline conversion prob from START via fundamental matrix-style
    # iteration (50 steps is plenty for small graphs).
    def _conv_prob(P_mat: np.ndarray) -> float:
        v = np.zeros(size)
        v[START] = 1.0
        for _ in range(50):
            v = v @ P_mat
        return float(v[CONVERT])

    baseline = _conv_prob(P)
    if baseline <= 1e-9:
        # Fallback: equal split across channels.
        per_ch = 1.0 / n
        removal: dict[str, float] = {ch: per_ch for ch in channels}
    else:
        removal = {}
        for ch, i in idx_of.items():
            P_rm = P.copy()
            # Redirect anything going INTO ch toward NULL.
            P_rm[:, NULL] = P_rm[:, NULL] + P_rm[:, i]
            P_rm[:, i] = 0.0
            P_rm[i, :] = 0.0
            P_rm[i, NULL] = 1.0
            cp = _conv_prob(P_rm)
            removal[ch] = max(0.0, baseline - cp)

        total = sum(removal.values())
        if total <= 0:
            per_ch = 1.0 / n
            removal = {ch: per_ch for ch in channels}
        else:
            removal = {ch: v / total for ch, v in removal.items()}

    # Distribute each channel's share equally across its touchpoint instances.
    instances: dict[str, list[int]] = {ch: [] for ch in channels}
    for journey in touchpoints_per_conversion:
        for tp in journey:
            ch = str(tp.get("channel") or "unknown")
            tp_id = int(tp["id"])
            instances[ch].append(tp_id)

    credits: dict[int, float] = {}
    for ch, tp_ids in instances.items():
        if not tp_ids:
            continue
        share = removal.get(ch, 0.0) / len(tp_ids)
        for tp_id in tp_ids:
            credits[tp_id] = credits.get(tp_id, 0.0) + share

    if target_touchpoint_ids is not None:
        target = set(int(x) for x in target_touchpoint_ids)
        return [(tp_id, c) for tp_id, c in credits.items() if tp_id in target]
    return list(credits.items())


# ── Helpers ────────────────────────────────────────────────────────────────


def _to_dt(v: Any) -> datetime:
    """Best-effort coerce to UTC-aware datetime."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            d = datetime.fromisoformat(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _fetch_touchpoints_for_conversion(
    cn,
    slug: str,
    customer_id: str,
    converted_at: datetime,
    lookback_days: int,
) -> list[dict]:
    """Read touchpoints in the lookback window for a single customer."""
    window_start = converted_at - timedelta(days=lookback_days)
    rows = cn.execute(
        _t(
            "SELECT id, channel, campaign_id, event_type, event_at "
            "FROM dash_touchpoints "
            "WHERE project_slug=:slug AND customer_id=:cid "
            "AND event_at >= :start AND event_at <= :end "
            "ORDER BY event_at ASC LIMIT :lim"
        ),
        {
            "slug": slug,
            "cid": customer_id,
            "start": window_start,
            "end": converted_at,
            "lim": MAX_TOUCHPOINTS_PER_CONVERSION,
        },
    ).fetchall()
    return [
        {
            "id": int(r[0]),
            "channel": r[1],
            "campaign_id": int(r[2]) if r[2] is not None else None,
            "event_type": r[3],
            "event_at": r[4],
        }
        for r in rows
    ]


def _persist_credits(
    cn,
    slug: str,
    conversion_id: int,
    revenue: float | None,
    model: str,
    credits: list[tuple[int, float]],
) -> int:
    """DELETE prior credits for (conversion, model) then INSERT fresh ones.

    Idempotent — safe to call repeatedly. Returns count of inserted rows.
    """
    cn.execute(
        _t(
            "DELETE FROM dash_attribution_credits "
            "WHERE project_slug=:slug AND conversion_id=:cid AND model=:model"
        ),
        {"slug": slug, "cid": conversion_id, "model": model},
    )
    inserted = 0
    rev = float(revenue) if revenue is not None else 0.0
    for tp_id, credit in credits:
        credited_revenue = rev * float(credit)
        cn.execute(
            _t(
                "INSERT INTO dash_attribution_credits "
                "(project_slug, conversion_id, touchpoint_id, model, credit, credited_revenue) "
                "VALUES (:slug, :cid, :tid, :model, :credit, :crev)"
            ),
            {
                "slug": slug,
                "cid": conversion_id,
                "tid": int(tp_id),
                "model": model,
                "credit": float(credit),
                "crev": float(credited_revenue),
            },
        )
        inserted += 1
    return inserted


# ── Orchestration ─────────────────────────────────────────────────────────


def attribute_conversion(
    slug: str,
    conversion_id: int,
    model: str = "linear",
    lookback_days: int = 30,
) -> dict:
    """Compute and persist attribution credits for a single conversion.

    Idempotent: re-running the same model deletes prior credits first.

    Returns:
        ``{"ok": True, "model": ..., "touchpoints": N, "credits_written": N}``
        or ``{"ok": False, "error": ...}``.
    """
    if model not in ALLOWED_MODELS:
        return {"ok": False, "error": f"invalid model '{model}'"}
    eng = _engine()
    with eng.begin() as cn:
        crow = cn.execute(
            _t(
                "SELECT id, customer_id, revenue, converted_at "
                "FROM dash_conversions WHERE id=:i AND project_slug=:slug"
            ),
            {"i": conversion_id, "slug": slug},
        ).fetchone()
        if not crow:
            return {"ok": False, "error": "conversion not found"}
        cid = int(crow[0])
        customer_id = crow[1]
        revenue = float(crow[2]) if crow[2] is not None else 0.0
        converted_at = _to_dt(crow[3])

        touchpoints = _fetch_touchpoints_for_conversion(
            cn, slug, customer_id, converted_at, lookback_days
        )
        if not touchpoints:
            # No touchpoints → no credits. Still clear stale rows for this model.
            cn.execute(
                _t(
                    "DELETE FROM dash_attribution_credits "
                    "WHERE project_slug=:slug AND conversion_id=:cid AND model=:model"
                ),
                {"slug": slug, "cid": cid, "model": model},
            )
            return {
                "ok": True,
                "model": model,
                "touchpoints": 0,
                "credits_written": 0,
            }

        if model == "linear":
            credits = linear_attribution(touchpoints)
        elif model == "time_decay":
            credits = time_decay_attribution(touchpoints, converted_at=converted_at)
        elif model == "position":
            credits = position_based_attribution(touchpoints)
        else:  # markov
            # Build journey corpus from recent conversions for this project.
            window_start = converted_at - timedelta(days=lookback_days)
            corpus_rows = cn.execute(
                _t(
                    "SELECT id, customer_id, converted_at FROM dash_conversions "
                    "WHERE project_slug=:slug AND converted_at >= :start "
                    "AND converted_at <= :end "
                    "ORDER BY converted_at DESC LIMIT 500"
                ),
                {"slug": slug, "start": window_start, "end": converted_at},
            ).fetchall()
            journeys: list[list[dict]] = []
            for cr in corpus_rows:
                tps = _fetch_touchpoints_for_conversion(
                    cn, slug, cr[1], _to_dt(cr[2]), lookback_days
                )
                if tps:
                    journeys.append(tps)
            if not journeys:
                journeys = [touchpoints]
            target_ids = [tp["id"] for tp in touchpoints]
            credits = markov_attribution(journeys, target_touchpoint_ids=target_ids)
            # Re-normalize to 1.0 across just this conversion's touchpoints.
            total = sum(c for _, c in credits)
            if total > 0:
                credits = [(tid, c / total) for tid, c in credits]
            else:
                credits = linear_attribution(touchpoints)

        written = _persist_credits(cn, slug, cid, revenue, model, credits)
        return {
            "ok": True,
            "model": model,
            "touchpoints": len(touchpoints),
            "credits_written": written,
        }


def attribute_all_pending(
    slug: str,
    model: str = "linear",
    since_days: int = 7,
    lookback_days: int = 30,
) -> dict:
    """Bulk runner — re-attribute every conversion in the last ``since_days``.

    Returns ``{"processed": N, "errors": N, "model": ...}``.
    """
    if model not in ALLOWED_MODELS:
        return {"ok": False, "error": f"invalid model '{model}'"}
    eng = _engine()
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    with eng.begin() as cn:
        rows = cn.execute(
            _t(
                "SELECT id FROM dash_conversions "
                "WHERE project_slug=:slug AND converted_at >= :start "
                "ORDER BY converted_at DESC"
            ),
            {"slug": slug, "start": cutoff},
        ).fetchall()
    ids = [int(r[0]) for r in rows]
    processed = 0
    errors = 0
    for cid in ids:
        try:
            res = attribute_conversion(slug, cid, model=model, lookback_days=lookback_days)
            if res.get("ok"):
                processed += 1
            else:
                errors += 1
        except Exception:
            logger.exception("attribute_conversion failed for %s", cid)
            errors += 1
    return {
        "ok": True,
        "model": model,
        "processed": processed,
        "errors": errors,
        "since_days": since_days,
    }
