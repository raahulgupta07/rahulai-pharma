"""Bottleneck reporting for the standalone training profiler.

Pure formatting/aggregation over collected per-step timing dicts. No DB, no
network, stdlib only. Safe against missing fields.

Timing dict shape (all fields optional except ideally ``name``)::

    {"name": "deep_analysis", "target": "app.upload:_llm_generate_training",
     "step_no": 13, "phase": "B", "seconds": 31.2, "llm_calls": 4,
     "tokens_in": 18000, "tokens_out": 6000, "cost_usd": 0.012,
     "table": "fact_sales", "wrappable": True, "note": ""}
"""

from __future__ import annotations

import datetime as _dt

__all__ = ["print_report", "compare_runs", "write_markdown"]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _num(d: dict, key: str, default: float = 0.0) -> float:
    """Coerce a dict field to float, tolerating None / bad types."""
    try:
        v = d.get(key, default)
        return float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def _int(d: dict, key: str, default: int = 0) -> int:
    try:
        v = d.get(key, default)
        return int(v) if v is not None else int(default)
    except (TypeError, ValueError):
        return int(default)


def _str(d: dict, key: str, default: str = "") -> str:
    v = d.get(key, default)
    return str(v) if v is not None else default


def _fmt_sec(s: float) -> str:
    return f"{s:.1f}"


def _fmt_usd(c: float) -> str:
    return f"{c:.4f}"


def _fmt_tok(t: int) -> str:
    return f"{int(t):,}"


def aggregate(timings: list[dict]) -> list[dict]:
    """Aggregate raw timing entries by step ``name``.

    Returns a list of dicts (one per unique step) sorted descending by
    ``total_seconds``.
    """
    agg: dict[str, dict] = {}
    for t in timings or []:
        name = _str(t, "name", "?") or "?"
        a = agg.get(name)
        if a is None:
            a = {
                "name": name,
                "phase": _str(t, "phase"),
                "total_seconds": 0.0,
                "call_count": 0,
                "total_cost": 0.0,
                "total_llm_calls": 0,
                "total_tokens": 0,
                "note": "",
                "target": _str(t, "target"),
                "step_no": _int(t, "step_no"),
            }
            agg[name] = a
        a["total_seconds"] += _num(t, "seconds")
        a["call_count"] += 1
        a["total_cost"] += _num(t, "cost_usd")
        a["total_llm_calls"] += _int(t, "llm_calls")
        a["total_tokens"] += _int(t, "tokens_in") + _int(t, "tokens_out")
        # keep first non-empty phase / note we encounter
        if not a["phase"]:
            a["phase"] = _str(t, "phase")
        note = _str(t, "note")
        if note and note not in a["note"]:
            a["note"] = (a["note"] + "; " + note).strip("; ") if a["note"] else note

    rows = list(agg.values())
    rows.sort(key=lambda r: r["total_seconds"], reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# print_report
# --------------------------------------------------------------------------- #
def print_report(
    timings: list[dict],
    run_label: str = "",
    total_wall_s: float | None = None,
) -> str:
    """Build a ranked report string + print it. Returns the string."""
    rows = aggregate(timings)
    sum_seconds = sum(r["total_seconds"] for r in rows)
    denom = total_wall_s if (total_wall_s and total_wall_s > 0) else sum_seconds
    if denom <= 0:
        denom = 1.0  # avoid div-by-zero; percentages become 0

    headers = ["RANK", "STEP", "PHASE", "CALLS", "SEC", "%WALL",
               "LLM", "TOKENS", "$", "NOTE"]

    table: list[list[str]] = []
    for i, r in enumerate(rows, start=1):
        cached = r["total_seconds"] < 0.05 and r["call_count"] > 0
        marks = []
        if cached:
            marks.append("cached/skip")
        note = r["note"]
        if note:
            marks.append(note)
        note_cell = " | ".join(marks)
        table.append([
            str(i),
            r["name"],
            r["phase"] or "-",
            str(r["call_count"]),
            _fmt_sec(r["total_seconds"]),
            f"{(r['total_seconds'] / denom * 100):.1f}%",
            str(r["total_llm_calls"]),
            _fmt_tok(r["total_tokens"]),
            _fmt_usd(r["total_cost"]),
            note_cell,
        ])

    body = _render_table(headers, table)

    # footer
    total_cost = sum(r["total_cost"] for r in rows)
    total_llm = sum(r["total_llm_calls"] for r in rows)
    top3 = [r["name"] for r in rows[:3]]
    zero_steps = sum(1 for r in rows if r["total_seconds"] < 0.05)
    wall = total_wall_s if (total_wall_s and total_wall_s > 0) else sum_seconds

    title = f"TRAINING PROFILE REPORT"
    if run_label:
        title += f"  [run: {run_label}]"

    lines = [
        "=" * max(len(title), 60),
        title,
        "=" * max(len(title), 60),
        body,
        "-" * 60,
        f"TOTAL wall:       {_fmt_sec(wall)}s"
        + ("" if (total_wall_s and total_wall_s > 0)
           else "  (summed from steps; no wall clock given)"),
        f"TOTAL $:          ${_fmt_usd(total_cost)}",
        f"TOTAL llm_calls:  {total_llm}",
        f"Top-3 time sinks: {', '.join(top3) if top3 else '(none)'}",
        f"Steps at ~0s:     {zero_steps}  (likely cached/skipped)",
        "=" * 60,
    ]
    out = "\n".join(lines)
    print(out)
    return out


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    """Monospace-align columns."""
    ncol = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for c in range(ncol):
            widths[c] = max(widths[c], len(row[c]))

    # right-align numeric-ish columns, left-align text columns
    right = {0, 3, 4, 5, 6, 7, 8}  # RANK CALLS SEC %WALL LLM TOKENS $

    def fmt_row(cells: list[str]) -> str:
        out = []
        for c in range(ncol):
            cell = cells[c]
            if c in right:
                out.append(cell.rjust(widths[c]))
            else:
                out.append(cell.ljust(widths[c]))
        return "  ".join(out).rstrip()

    sep = "  ".join("-" * widths[c] for c in range(ncol))
    lines = [fmt_row(headers), sep]
    lines.extend(fmt_row(r) for r in rows)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# compare_runs
# --------------------------------------------------------------------------- #
def _agg_by_name(timings: list[dict]) -> dict[str, dict]:
    return {r["name"]: r for r in aggregate(timings)}


def _classify(name: str, cold: dict | None, warm: dict | None,
              mock: dict | None) -> str:
    """Classify a step into STUCK / SLOW / REDUNDANT / CHEAP."""
    c_sec = cold["total_seconds"] if cold else 0.0
    c_calls = cold["call_count"] if cold else 0
    c_llm = cold["total_llm_calls"] if cold else 0
    w_sec = warm["total_seconds"] if warm else None
    m_sec = mock["total_seconds"] if mock else None

    per_call = (c_sec / c_calls) if c_calls else c_sec

    tags = []
    if per_call > 60.0:
        tags.append("STUCK")
    # SLOW: big LLM time or big SQL time
    llm_time = (c_sec - m_sec) if (m_sec is not None) else c_sec
    if c_sec >= 10.0 and (c_llm > 0 or llm_time >= 5.0):
        tags.append("SLOW")
    # REDUNDANT: still ran (non-trivial) in warm despite caching expectation
    if w_sec is not None and w_sec >= 1.0 and c_sec >= 1.0:
        tags.append("REDUNDANT")
    if c_sec < 1.0:
        tags.append("CHEAP")
    return "/".join(tags) if tags else "OK"


def compare_runs(runs: dict[str, list[dict]]) -> str:
    """Side-by-side per-step seconds across runs + a verdict section."""
    runs = runs or {}
    cold = runs.get("cold")
    warm = runs.get("warm")
    mock = runs.get("mock")

    present = [k for k in ("cold", "warm", "mock") if runs.get(k) is not None]
    # include any extra labels too
    for k in runs:
        if k not in present and runs.get(k) is not None:
            present.append(k)

    by_run = {k: _agg_by_name(runs[k]) for k in present}

    # union of step names, ordered by cold seconds desc (fallback: any run)
    order_src = cold if cold is not None else (
        next((runs[k] for k in present), []))
    ordered = [r["name"] for r in aggregate(order_src or [])]
    seen = set(ordered)
    for k in present:
        for n in by_run[k]:
            if n not in seen:
                ordered.append(n)
                seen.add(n)

    headers = ["STEP"] + [f"{k} (s)" for k in present]
    table: list[list[str]] = []
    for name in ordered:
        row = [name]
        for k in present:
            a = by_run[k].get(name)
            row.append(_fmt_sec(a["total_seconds"]) if a else "-")
        table.append(row)

    lines = [
        "=" * 60,
        "RUN COMPARISON (seconds per step)",
        "=" * 60,
        _render_table(headers, table),
        "-" * 60,
        "VERDICT",
        "-" * 60,
    ]

    # cache value: cold - warm
    if cold is not None and warm is not None:
        cold_total = sum(_num(t, "seconds") for t in cold)
        warm_total = sum(_num(t, "seconds") for t in warm)
        saved = cold_total - warm_total
        lines.append(
            f"Cache value: cold {_fmt_sec(cold_total)}s -> warm "
            f"{_fmt_sec(warm_total)}s  (saved {_fmt_sec(saved)}s)"
        )
        went_zero = []
        cold_by = by_run.get("cold", {})
        warm_by = by_run.get("warm", {})
        for n, ca in cold_by.items():
            if ca["total_seconds"] >= 1.0:
                wa = warm_by.get(n)
                if wa is None or wa["total_seconds"] < 0.05:
                    went_zero.append(n)
        lines.append(
            "  Steps cached to ~0s in warm: "
            + (", ".join(went_zero) if went_zero else "(none)")
        )
    else:
        lines.append("Cache value: need both 'cold' and 'warm' runs.")

    # LLM share: cold - mock
    if cold is not None and mock is not None:
        cold_total = sum(_num(t, "seconds") for t in cold)
        mock_total = sum(_num(t, "seconds") for t in mock)
        llm_time = cold_total - mock_total
        share = (llm_time / cold_total * 100) if cold_total > 0 else 0.0
        lines.append(
            f"LLM share: real cold {_fmt_sec(cold_total)}s vs mock "
            f"{_fmt_sec(mock_total)}s  -> LLM time ~{_fmt_sec(llm_time)}s "
            f"({share:.1f}% of cold)"
        )
    else:
        lines.append("LLM share: need both 'cold' and 'mock' runs.")

    # classification of top steps (by cold, else first present run)
    lines.append("-" * 60)
    lines.append("CLASSIFICATION (top steps)")
    cold_by = by_run.get("cold", {})
    warm_by = by_run.get("warm", {})
    mock_by = by_run.get("mock", {})
    top_names = ordered[:8]
    for n in top_names:
        cls = _classify(n, cold_by.get(n), warm_by.get(n), mock_by.get(n))
        c_sec = cold_by.get(n, {}).get("total_seconds", 0.0) \
            if cold_by.get(n) else (
                next((by_run[k][n]["total_seconds"]
                      for k in present if n in by_run[k]), 0.0))
        lines.append(f"  {n:<28} {_fmt_sec(c_sec):>8}s  [{cls}]")

    lines.append("=" * 60)
    out = "\n".join(lines)
    print(out)
    return out


# --------------------------------------------------------------------------- #
# write_markdown
# --------------------------------------------------------------------------- #
def _recommend(name: str, cold: dict | None, warm: dict | None,
               mock: dict | None) -> str:
    """Derive an optimization suggestion for a top step."""
    cls = _classify(name, cold, warm, mock)
    c = cold or {}
    sec = c.get("total_seconds", 0.0)
    calls = c.get("call_count", 0)
    llm = c.get("total_llm_calls", 0)
    per_call = (sec / calls) if calls else sec

    tips = []
    if "STUCK" in cls:
        tips.append(
            f"single call ~{_fmt_sec(per_call)}s — add timeout/retry, stream, "
            "or split the prompt")
    if "SLOW" in cls and llm > 0:
        tips.append(
            "LLM-bound — try a cheaper/faster model, trim prompt tokens, "
            "or batch the LLM calls")
    if "SLOW" in cls and llm == 0:
        tips.append("SQL/compute-bound — index, push down, or sample rows")
    if calls > 1:
        tips.append(f"called {calls}x — parallelize across tables")
    if "REDUNDANT" in cls:
        tips.append("ran in warm despite no change — cache result / skip")
    if "CHEAP" in cls and not tips:
        tips.append("cheap (<1s) — leave as is")
    if not tips:
        tips.append("monitor; no obvious win")
    return "; ".join(tips)


def write_markdown(
    path: str,
    *,
    cold=None,
    warm=None,
    mock=None,
    meta: dict | None = None,
) -> None:
    """Write docs/TRAINING_PROFILE.md with ranked report, comparison, and
    auto-derived bottleneck recommendations."""
    meta = meta or {}
    date = meta.get("date") or _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = meta.get("rows", "")
    db = meta.get("db", "")

    parts: list[str] = []
    parts.append("# Training Profile")
    parts.append("")
    parts.append("## Run metadata")
    parts.append("")
    parts.append(f"- Date: {date}")
    parts.append(f"- Rows: {rows}")
    parts.append(f"- DB: {db}")
    for k, v in meta.items():
        if k not in ("date", "rows", "db"):
            parts.append(f"- {k}: {v}")
    parts.append("")

    # ranked cold report
    if cold is not None:
        parts.append("## Ranked report (cold run)")
        parts.append("")
        parts.append("```")
        parts.append(print_report(
            cold, run_label="cold",
            total_wall_s=meta.get("cold_wall_s")))
        parts.append("```")
        parts.append("")

    # comparison
    runs: dict[str, list[dict]] = {}
    if cold is not None:
        runs["cold"] = cold
    if warm is not None:
        runs["warm"] = warm
    if mock is not None:
        runs["mock"] = mock
    if runs:
        parts.append("## Cold vs warm vs mock")
        parts.append("")
        parts.append("```")
        parts.append(compare_runs(runs))
        parts.append("```")
        parts.append("")

    # bottlenecks + recommendations
    parts.append("## Bottlenecks + recommended optimizations")
    parts.append("")
    base = cold if cold is not None else (warm if warm is not None else mock)
    if base:
        ranked = aggregate(base)
        cold_by = {r["name"]: r for r in aggregate(cold)} if cold else {}
        warm_by = {r["name"]: r for r in aggregate(warm)} if warm else {}
        mock_by = {r["name"]: r for r in aggregate(mock)} if mock else {}
        parts.append("| # | Step | Sec | Class | Recommendation |")
        parts.append("|---|------|-----|-------|----------------|")
        for i, r in enumerate(ranked[:8], start=1):
            n = r["name"]
            cls = _classify(n, cold_by.get(n) or r,
                            warm_by.get(n), mock_by.get(n))
            rec = _recommend(n, cold_by.get(n) or r,
                             warm_by.get(n), mock_by.get(n))
            parts.append(
                f"| {i} | {n} | {_fmt_sec(r['total_seconds'])} | "
                f"{cls} | {rec} |")
    else:
        parts.append("_No timing data provided._")
    parts.append("")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
