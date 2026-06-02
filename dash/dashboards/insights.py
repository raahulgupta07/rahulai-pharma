"""Pure-function insight detectors for the dashboard analyst loop.

Each detector takes a pandas DataFrame and returns a list of insight dicts:
    {type, severity (high|medium|low), finding, evidence, suggested_chart}
Detectors return [] silently if data is insufficient.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_ROWS = 5


def _num_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _cat_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]


def _time_cols(df: pd.DataFrame) -> list[str]:
    out = []
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            out.append(c)
        elif df[c].dtype == object:
            try:
                pd.to_datetime(df[c].head(20), errors="raise")
                out.append(c)
            except Exception:
                pass
    return out


def detect_anomaly(df: pd.DataFrame, col: str) -> list[dict]:
    if len(df) < MIN_ROWS or col not in df.columns:
        return []
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if len(s) < MIN_ROWS or s.std(ddof=0) == 0:
        return []
    z = (s - s.mean()) / s.std(ddof=0)
    hits = z[z.abs() > 3]
    if hits.empty:
        return []
    idx = hits.abs().idxmax()
    return [{
        "type": "anomaly", "severity": "high" if abs(z.loc[idx]) > 4 else "medium",
        "finding": f"{col} has outlier value {s.loc[idx]:.2f} (z={z.loc[idx]:.1f})",
        "evidence": {"col": col, "value": float(s.loc[idx]), "z": float(z.loc[idx]), "n": int(len(s))},
        "suggested_chart": {"type": "bar", "x": df.columns[0], "y": col},
    }]


def detect_trend_break(df: pd.DataFrame, time_col: str, value_col: str) -> list[dict]:
    if len(df) < MIN_ROWS * 2 or time_col not in df.columns or value_col not in df.columns:
        return []
    try:
        d = df[[time_col, value_col]].copy()
        d[time_col] = pd.to_datetime(d[time_col], errors="coerce")
        d[value_col] = pd.to_numeric(d[value_col], errors="coerce")
        d = d.dropna().sort_values(time_col).reset_index(drop=True)
    except Exception:
        return []
    if len(d) < MIN_ROWS * 2:
        return []
    half = len(d) // 2
    x1 = np.arange(half); x2 = np.arange(len(d) - half)
    s1 = np.polyfit(x1, d[value_col].iloc[:half].values, 1)[0]
    s2 = np.polyfit(x2, d[value_col].iloc[half:].values, 1)[0]
    if abs(s1) < 1e-9:
        return []
    delta = (s2 - s1) / abs(s1)
    if abs(delta) < 0.3:
        return []
    return [{
        "type": "trend_break", "severity": "high" if abs(delta) > 1 else "medium",
        "finding": f"{value_col} slope changed {delta*100:+.0f}% mid-series ({s1:.2f}→{s2:.2f})",
        "evidence": {"slope_before": float(s1), "slope_after": float(s2), "pivot_idx": half},
        "suggested_chart": {"type": "line", "x": time_col, "y": value_col},
    }]


def detect_outlier(df: pd.DataFrame, group_col: str, value_col: str) -> list[dict]:
    if len(df) < MIN_ROWS or group_col not in df.columns or value_col not in df.columns:
        return []
    g = df.groupby(group_col)[value_col].sum(numeric_only=True)
    if len(g) < 3 or g.mean() == 0:
        return []
    top = g.idxmax(); ratio = g.max() / g.mean()
    if ratio < 5:
        return []
    return [{
        "type": "outlier", "severity": "high" if ratio > 10 else "medium",
        "finding": f"{top} in {group_col} is {ratio:.1f}x the average {value_col}",
        "evidence": {"group": str(top), "value": float(g.max()), "mean": float(g.mean()), "ratio": float(ratio)},
        "suggested_chart": {"type": "bar", "x": group_col, "y": value_col},
    }]


def detect_correlation(df: pd.DataFrame, col_a: str, col_b: str) -> list[dict]:
    if len(df) < MIN_ROWS or col_a not in df.columns or col_b not in df.columns:
        return []
    a = pd.to_numeric(df[col_a], errors="coerce")
    b = pd.to_numeric(df[col_b], errors="coerce")
    mask = a.notna() & b.notna()
    if mask.sum() < MIN_ROWS:
        return []
    r = float(np.corrcoef(a[mask], b[mask])[0, 1])
    if np.isnan(r) or abs(r) < 0.7:
        return []
    return [{
        "type": "correlation", "severity": "high" if abs(r) > 0.9 else "medium",
        "finding": f"{col_a} and {col_b} are {'positively' if r>0 else 'negatively'} correlated (r={r:.2f})",
        "evidence": {"r": r, "n": int(mask.sum()), "a": col_a, "b": col_b},
        "suggested_chart": {"type": "scatter", "x": col_a, "y": col_b},
    }]


def detect_concentration(df: pd.DataFrame, group_col: str, value_col: str) -> list[dict]:
    if len(df) < MIN_ROWS or group_col not in df.columns or value_col not in df.columns:
        return []
    g = df.groupby(group_col)[value_col].sum(numeric_only=True).sort_values(ascending=False)
    total = float(g.sum())
    if total <= 0 or len(g) < 5:
        return []
    top_n = max(1, int(len(g) * 0.2))
    top_share = float(g.head(top_n).sum()) / total
    if top_share < 0.8:
        return []
    return [{
        "type": "concentration", "severity": "high" if top_share > 0.9 else "medium",
        "finding": f"Top 20% of {group_col} accounts for {top_share*100:.0f}% of {value_col} (Pareto)",
        "evidence": {"top_n": top_n, "top_share": top_share, "total_groups": int(len(g))},
        "suggested_chart": {"type": "bar", "x": group_col, "y": value_col},
    }]


def detect_all(df: pd.DataFrame) -> list[dict]:
    """Try every detector against plausible column combos. Return all hits."""
    out: list[dict] = []
    if df is None or len(df) < MIN_ROWS:
        return out
    nums = _num_cols(df); cats = _cat_cols(df); times = _time_cols(df)
    try:
        for c in nums:
            out.extend(detect_anomaly(df, c))
        for tc in times[:1]:
            for vc in nums[:2]:
                out.extend(detect_trend_break(df, tc, vc))
        for gc in cats[:2]:
            for vc in nums[:2]:
                out.extend(detect_outlier(df, gc, vc))
                out.extend(detect_concentration(df, gc, vc))
        for i, a in enumerate(nums):
            for b in nums[i+1:i+3]:
                out.extend(detect_correlation(df, a, b))
    except Exception as e:
        logger.debug(f"detect_all error: {e}")
    # Dedupe by (type, finding)
    seen = set(); uniq: list[dict] = []
    for ins in out:
        k = (ins.get("type"), ins.get("finding"))
        if k in seen:
            continue
        seen.add(k); uniq.append(ins)
    return uniq
