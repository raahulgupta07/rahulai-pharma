"""
Ops Optimizer tools — portfolio operations (post-investment).

Mirrors venture_tools.py pattern: module-level fns returning {ok: bool, ...}
+ create_ops_tools(project_slug, user_id=None) factory returning @tool wrappers.

DB rules:
  - get_write_engine() for INSERT/UPDATE on dash.* tables
  - get_sql_engine() for SELECTs
  - CAST(:m AS jsonb) — never :m::jsonb (PgBouncer collision)
  - Idempotent upserts via ON CONFLICT
  - variance_pct is GENERATED — never write to it
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date as _date
from typing import Any, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Engines (fail-soft) ──────────────────────────────────────────────────

def _write_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        from db.session import get_sql_engine
        return get_sql_engine()


def _sql_engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── Helpers ──────────────────────────────────────────────────────────────

def _stats(values: List[float]) -> tuple[float, float]:
    """Return (mean, stdev). stdev=0 if <2 samples or zero variance."""
    n = len(values)
    if n == 0:
        return (0.0, 0.0)
    m = sum(values) / n
    if n < 2:
        return (m, 0.0)
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return (m, math.sqrt(var))


# ── 1. register_portco ──────────────────────────────────────────────────

def register_portco(project_slug: str, deal_id: str, legal_name: str,
                    investment_date: str, ownership_pct: float,
                    board_seat: bool = False, sector: Optional[str] = None,
                    stage_at_invest: Optional[str] = None,
                    fiscal_year_end: str = "DEC") -> dict:
    """Promote a closed deal to portco. Verifies deal stage='closed' first."""
    try:
        with _sql_engine().connect() as cx:
            row = cx.execute(text("""
                SELECT stage, status, project_slug
                FROM dash.dash_venture_deals
                WHERE id = :d
            """), {"d": deal_id}).fetchone()
        if not row:
            return {"ok": False, "error": f"deal {deal_id} not found"}
        stage = (row[0] or "").lower()
        status = (row[1] or "").lower()
        # Per design doc: require dash_venture_deals.stage='closed' (or equivalent
        # status='closed'). Accept either signal.
        if stage != "closed" and status != "closed":
            return {"ok": False,
                    "error": f"deal not closed (stage={row[0]}, status={row[1]})"}
        # Cross-tenant guard.
        if row[2] and row[2] != project_slug:
            return {"ok": False, "error": "deal belongs to a different project"}

        with _write_engine().begin() as cx:
            r = cx.execute(text("""
                INSERT INTO dash.dash_portco
                    (project_slug, deal_id, legal_name, investment_date,
                     ownership_pct, board_seat, sector, stage_at_invest,
                     fiscal_year_end)
                VALUES (:p, :d, :ln, :idate, :own, :bs, :sec, :sai, :fye)
                ON CONFLICT (project_slug, deal_id) DO UPDATE SET
                    legal_name = EXCLUDED.legal_name,
                    investment_date = EXCLUDED.investment_date,
                    ownership_pct = EXCLUDED.ownership_pct,
                    board_seat = EXCLUDED.board_seat,
                    sector = COALESCE(EXCLUDED.sector, dash.dash_portco.sector),
                    updated_at = now()
                RETURNING id
            """), {"p": project_slug, "d": deal_id, "ln": legal_name,
                   "idate": investment_date, "own": ownership_pct,
                   "bs": board_seat, "sec": sector, "sai": stage_at_invest,
                   "fye": fiscal_year_end}).fetchone()
        return {"ok": True, "portco_id": str(r[0])}
    except Exception as e:
        logger.exception("register_portco failed")
        return {"ok": False, "error": str(e)}


# ── 2. ingest_kpis ──────────────────────────────────────────────────────

def ingest_kpis(portco_id: str, period: str, metrics: List[dict],
                source: str = "manual") -> dict:
    """Bulk upsert KPIs. metrics=[{name, category, unit, actual, plan,
    forecast, period_start, period_end}]. NEVER writes variance_pct (GENERATED).
    """
    if not metrics:
        return {"ok": False, "error": "no metrics"}
    if source not in ("manual", "api", "upload", "agent"):
        source = "manual"
    inserted = 0
    skipped = []
    try:
        with _write_engine().begin() as cx:
            for m in metrics:
                name = (m.get("name") or "").strip()
                if not name:
                    skipped.append({"metric": m, "reason": "missing name"})
                    continue
                try:
                    cx.execute(text("""
                        INSERT INTO dash.dash_portco_kpis
                            (portco_id, metric_name, metric_category, unit,
                             period, period_start, period_end,
                             actual, plan, forecast, source)
                        VALUES (:pc, :n, :cat, :u, :p, :ps, :pe,
                                :a, :pl, :fc, :src)
                        ON CONFLICT (portco_id, metric_name, period) DO UPDATE SET
                            actual = EXCLUDED.actual,
                            plan = EXCLUDED.plan,
                            forecast = EXCLUDED.forecast,
                            metric_category = COALESCE(EXCLUDED.metric_category,
                                                       dash.dash_portco_kpis.metric_category),
                            unit = EXCLUDED.unit,
                            period_start = EXCLUDED.period_start,
                            period_end = EXCLUDED.period_end,
                            source = EXCLUDED.source
                    """), {
                        "pc": portco_id, "n": name,
                        "cat": m.get("category"),
                        "u": m.get("unit") or "",
                        "p": period,
                        "ps": m.get("period_start") or m.get("start"),
                        "pe": m.get("period_end") or m.get("end"),
                        "a": m.get("actual"),
                        "pl": m.get("plan"),
                        "fc": m.get("forecast"),
                        "src": source,
                    })
                    inserted += 1
                except Exception as e:
                    skipped.append({"metric": name, "reason": str(e)})
        return {"ok": True, "inserted": inserted, "skipped": skipped,
                "period": period}
    except Exception as e:
        logger.exception("ingest_kpis failed")
        return {"ok": False, "error": str(e)}


# ── 3. kpi_dashboard ────────────────────────────────────────────────────

def kpi_dashboard(portco_id: str, since_periods: int = 12) -> dict:
    """Return time-series per KPI for last N periods, with variance_pct."""
    try:
        n = max(1, min(int(since_periods), 60))
        with _sql_engine().connect() as cx:
            rows = cx.execute(text("""
                SELECT metric_name, metric_category, unit, period,
                       period_start, period_end,
                       actual, plan, forecast, variance_pct
                FROM dash.dash_portco_kpis
                WHERE portco_id = :pc
                  AND metric_name IN (
                      SELECT metric_name FROM dash.dash_portco_kpis
                      WHERE portco_id = :pc
                  )
                ORDER BY metric_name, period_end DESC
            """), {"pc": portco_id}).fetchall()
        by_metric: dict[str, list[dict]] = {}
        for r in rows:
            by_metric.setdefault(r[0], []).append({
                "period": r[3],
                "period_start": r[4].isoformat() if r[4] else None,
                "period_end": r[5].isoformat() if r[5] else None,
                "actual": float(r[6]) if r[6] is not None else None,
                "plan": float(r[7]) if r[7] is not None else None,
                "forecast": float(r[8]) if r[8] is not None else None,
                "variance_pct": float(r[9]) if r[9] is not None else None,
                "category": r[1],
                "unit": r[2],
            })
        # cap to last N periods per metric
        for k in list(by_metric.keys()):
            by_metric[k] = by_metric[k][:n]
        return {"ok": True, "portco_id": portco_id, "metrics": by_metric,
                "metric_count": len(by_metric)}
    except Exception as e:
        logger.exception("kpi_dashboard failed")
        return {"ok": False, "error": str(e)}


# ── 4. detect_anomalies ─────────────────────────────────────────────────

def detect_anomalies(portco_id: str, z_threshold: float = 2.0) -> dict:
    """Compute z-score per metric across last 12 periods. Insert anomaly rows
    for |z| > z_threshold (warn 2-3, critical >3). Idempotent on
    (portco_id, metric_name, period) via unique idx + ON CONFLICT DO NOTHING."""
    try:
        with _sql_engine().connect() as cx:
            rows = cx.execute(text("""
                SELECT metric_name, period, actual, period_end
                FROM dash.dash_portco_kpis
                WHERE portco_id = :pc AND actual IS NOT NULL
                ORDER BY metric_name, period_end DESC
            """), {"pc": portco_id}).fetchall()

        # Group by metric, take last 12.
        by_metric: dict[str, list[tuple]] = {}
        for r in rows:
            by_metric.setdefault(r[0], []).append((r[1], float(r[2]), r[3]))

        new_anoms: list[dict] = []
        for metric, series in by_metric.items():
            recent = series[:12]
            if len(recent) < 3:
                continue
            values = [v for _, v, _ in recent]
            mean, stdev = _stats(values)
            if stdev == 0:
                continue
            for period, val, _ in recent:
                z = (val - mean) / stdev
                if abs(z) <= z_threshold:
                    continue
                severity = "critical" if abs(z) > 3.0 else "warn"
                # Reference at least one prior-period number in explanation.
                priors = [pv for (_, pv, _) in recent if pv != val][:3]
                prior_str = ", ".join(f"{p:.2f}" for p in priors) or "n/a"
                explanation = (
                    f"{metric}={val:.2f} at z={z:.2f} (mean={mean:.2f}, "
                    f"stdev={stdev:.2f}); recent priors: {prior_str}"
                )
                new_anoms.append({
                    "metric_name": metric, "period": period,
                    "severity": severity, "z_score": z,
                    "explanation": explanation,
                })

        if not new_anoms:
            return {"ok": True, "detected": 0, "anomalies": []}

        inserted = 0
        with _write_engine().begin() as cx:
            for a in new_anoms:
                try:
                    r = cx.execute(text("""
                        INSERT INTO dash.dash_portco_anomalies
                            (portco_id, metric_name, period, severity,
                             z_score, explanation)
                        VALUES (:pc, :m, :p, :sev, :z, :ex)
                        ON CONFLICT (portco_id, metric_name, period)
                        DO NOTHING
                        RETURNING id
                    """), {"pc": portco_id, "m": a["metric_name"],
                           "p": a["period"], "sev": a["severity"],
                           "z": a["z_score"], "ex": a["explanation"]}).fetchone()
                    if r:
                        inserted += 1
                except Exception:
                    logger.debug("detect_anomalies: insert skipped",
                                 exc_info=True)
        return {"ok": True, "detected": inserted,
                "scanned": len(new_anoms), "anomalies": new_anoms}
    except Exception as e:
        logger.exception("detect_anomalies failed")
        return {"ok": False, "error": str(e)}


# ── 5. propose_value_play ───────────────────────────────────────────────

def propose_value_play(portco_id: str, focus: Optional[str] = None,
                        play_type: Optional[str] = None,
                        owner: Optional[str] = None) -> dict:
    """Generate value-creation initiative from KPI drift. Always
    status='proposed', description references one KPI name + actual/plan."""
    try:
        # Find worst recent variance for this portco (cap to last 6 periods).
        with _sql_engine().connect() as cx:
            q = """
                SELECT metric_name, period, actual, plan, variance_pct,
                       metric_category, unit
                FROM dash.dash_portco_kpis
                WHERE portco_id = :pc AND variance_pct IS NOT NULL
                  AND actual IS NOT NULL AND plan IS NOT NULL
            """
            params: dict[str, Any] = {"pc": portco_id}
            if focus:
                q += " AND metric_name ILIKE :f"
                params["f"] = f"%{focus}%"
            q += " ORDER BY period_end DESC LIMIT 30"
            rows = cx.execute(text(q), params).fetchall()
        if not rows:
            return {"ok": False,
                    "error": "no KPI variance data to base a play on"}
        # pick most negative variance (worst drift)
        worst = min(rows, key=lambda r: float(r[4]) if r[4] is not None else 0)
        metric, period, actual, plan, var, cat, unit = worst
        actual_f = float(actual); plan_f = float(plan); var_f = float(var)

        # play_type heuristic
        if not play_type:
            cat_l = (cat or "").lower()
            if cat_l == "margin":
                play_type = "margin_expansion"
            elif cat_l == "cash":
                play_type = "cost_out"
            elif cat_l in ("revenue", "growth", "customer"):
                play_type = "revenue_uplift"
            else:
                play_type = "cost_out"

        title = f"Close {metric} gap ({var_f:+.1f}%)"
        # Description MUST include metric name + actual/plan numbers.
        description = (
            f"{metric} at {actual_f:.2f} {unit or ''} vs plan {plan_f:.2f} "
            f"({var_f:+.1f}%) for {period}. Drive a {play_type} initiative "
            f"to close the gap."
        )
        target_delta = -var_f  # close the gap

        with _write_engine().begin() as cx:
            r = cx.execute(text("""
                INSERT INTO dash.dash_portco_initiatives
                    (portco_id, title, description, play_type, owner,
                     target_metric, target_delta_pct, status)
                VALUES (:pc, :t, :d, :pt, :o, :tm, :td, 'proposed')
                RETURNING id
            """), {"pc": portco_id, "t": title, "d": description,
                   "pt": play_type, "o": owner, "tm": metric,
                   "td": target_delta}).fetchone()
        return {"ok": True, "initiative_id": str(r[0]),
                "title": title, "description": description,
                "play_type": play_type, "target_metric": metric,
                "target_delta_pct": target_delta, "status": "proposed"}
    except Exception as e:
        logger.exception("propose_value_play failed")
        return {"ok": False, "error": str(e)}


# ── 6. update_initiative ────────────────────────────────────────────────

def update_initiative(initiative_id: str, status: Optional[str] = None,
                      notes: Optional[str] = None,
                      owner: Optional[str] = None,
                      due_date: Optional[str] = None) -> dict:
    """Move initiative through lifecycle. Auto-stamps updated_at."""
    valid_statuses = ("proposed", "approved", "in_progress", "done", "cancelled")
    if status and status not in valid_statuses:
        return {"ok": False, "error": f"invalid status (allowed: {valid_statuses})"}
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {"id": initiative_id}
    if status:
        sets.append("status = :st")
        params["st"] = status
    if owner is not None:
        sets.append("owner = :o")
        params["o"] = owner
    if due_date:
        sets.append("due_date = :dd")
        params["dd"] = due_date
    if notes is not None:
        # append notes to description.
        sets.append("description = COALESCE(description, '') || E'\\n[update] ' || :n")
        params["n"] = notes
    try:
        with _write_engine().begin() as cx:
            r = cx.execute(text(f"""
                UPDATE dash.dash_portco_initiatives
                SET {', '.join(sets)}
                WHERE id = :id
                RETURNING id, status
            """), params).fetchone()
        if not r:
            return {"ok": False, "error": "initiative not found"}
        return {"ok": True, "initiative_id": str(r[0]), "status": r[1]}
    except Exception as e:
        logger.exception("update_initiative failed")
        return {"ok": False, "error": str(e)}


# ── 7. portfolio_health ─────────────────────────────────────────────────

def portfolio_health(project_slug: str) -> dict:
    """Cross-portco rollup: green/yellow/red via latest variance_pct.
    green > -5% · yellow -5..-15 · red < -15."""
    try:
        with _sql_engine().connect() as cx:
            portcos = cx.execute(text("""
                SELECT id, legal_name, status, ownership_pct, sector,
                       investment_date
                FROM dash.dash_portco
                WHERE project_slug = :p
            """), {"p": project_slug}).fetchall()

            results = []
            green = yellow = red = unknown = 0
            risks: list[dict] = []
            for p in portcos:
                pid = str(p[0])
                # Latest variance per metric, then avg.
                row = cx.execute(text("""
                    SELECT AVG(variance_pct), COUNT(*) FROM (
                        SELECT DISTINCT ON (metric_name)
                            metric_name, variance_pct
                        FROM dash.dash_portco_kpis
                        WHERE portco_id = :pc AND variance_pct IS NOT NULL
                        ORDER BY metric_name, period_end DESC
                    ) latest
                """), {"pc": pid}).fetchone()
                avg_var = float(row[0]) if row and row[0] is not None else None
                metric_count = int(row[1]) if row else 0
                if avg_var is None:
                    color = "unknown"
                    unknown += 1
                elif avg_var > -5:
                    color = "green"; green += 1
                elif avg_var > -15:
                    color = "yellow"; yellow += 1
                else:
                    color = "red"; red += 1
                entry = {
                    "portco_id": pid, "legal_name": p[1], "status": p[2],
                    "ownership_pct": float(p[3]) if p[3] else None,
                    "sector": p[4],
                    "investment_date": p[5].isoformat() if p[5] else None,
                    "avg_variance_pct": avg_var,
                    "metric_count": metric_count,
                    "color": color,
                }
                results.append(entry)
                if color in ("yellow", "red"):
                    reason = (
                        f"avg variance {avg_var:+.1f}% across {metric_count} KPIs"
                        if avg_var is not None else "no KPI data"
                    )
                    risks.append({"portco_id": pid, "legal_name": p[1],
                                  "reason": reason, "color": color})

            # top-3 risk portcos = most negative variance first.
            risks.sort(key=lambda r: r.get("color") == "red", reverse=True)
            top_risks = risks[:3]

        return {"ok": True, "project_slug": project_slug,
                "total": len(portcos),
                "counts": {"green": green, "yellow": yellow,
                            "red": red, "unknown": unknown},
                "portcos": results,
                "top_risks": top_risks}
    except Exception as e:
        logger.exception("portfolio_health failed")
        return {"ok": False, "error": str(e)}


# ── 8. generate_board_pack ──────────────────────────────────────────────

def generate_board_pack(portco_id: str, meeting_date: str) -> dict:
    """Compose board pack PDF, persist file, return id + url.
    Delegates rendering to dash.tools.ops_board_pack.generate_board_pack_pdf.
    """
    try:
        # Resolve project_slug from portco.
        with _sql_engine().connect() as cx:
            row = cx.execute(text("""
                SELECT project_slug, legal_name FROM dash.dash_portco
                WHERE id = :pc
            """), {"pc": portco_id}).fetchone()
        if not row:
            return {"ok": False, "error": "portco not found"}
        slug, legal_name = row[0], row[1]

        try:
            from dash.tools.ops_board_pack import generate_board_pack_pdf
            pdf_bytes = generate_board_pack_pdf(portco_id, meeting_date)
        except Exception as e:
            logger.exception("generate_board_pack: pdf gen failed")
            return {"ok": False, "error": f"pdf gen failed: {e}"}

        # Write file
        import os
        base_dir = os.path.join("/data", "board_packs", slug, portco_id)
        os.makedirs(base_dir, exist_ok=True)
        file_path = os.path.join(base_dir, f"{meeting_date}.pdf")
        with open(file_path, "wb") as f:
            f.write(pdf_bytes)

        # Persist row
        with _write_engine().begin() as cx:
            r = cx.execute(text("""
                INSERT INTO dash.dash_portco_board_packs
                    (portco_id, meeting_date, summary, file_path,
                     kpi_snapshot, decisions)
                VALUES (:pc, :md, :sum, :fp,
                        CAST(:ks AS jsonb), CAST(:dc AS jsonb))
                RETURNING id
            """), {"pc": portco_id, "md": meeting_date,
                   "sum": f"Board pack for {legal_name} — {meeting_date}",
                   "fp": file_path,
                   "ks": "{}", "dc": "[]"}).fetchone()
        bp_id = str(r[0])
        url = f"/api/ops/{slug}/portcos/{portco_id}/board-pack/{bp_id}.pdf"
        return {"ok": True, "id": bp_id, "file_path": file_path,
                "url": url, "bytes": len(pdf_bytes)}
    except Exception as e:
        logger.exception("generate_board_pack failed")
        return {"ok": False, "error": str(e)}


# ── 9. benchmark_portco ─────────────────────────────────────────────────

def benchmark_portco(portco_id: str, peer_segment: str) -> dict:
    """Compare portco KPIs vs sector benchmarks from
    public.dash_company_brain (category='sector_benchmark'). Returns
    confidence='low' + empty data if no benchmarks found."""
    try:
        with _sql_engine().connect() as cx:
            # Load benchmark rows for the segment.
            try:
                bench_rows = cx.execute(text("""
                    SELECT name, definition, metadata
                    FROM public.dash_company_brain
                    WHERE category = 'sector_benchmark'
                      AND (
                          name ILIKE :seg
                          OR metadata->>'segment' = :seg_exact
                          OR metadata->>'peer_segment' = :seg_exact
                      )
                    LIMIT 50
                """), {"seg": f"%{peer_segment}%",
                       "seg_exact": peer_segment}).fetchall()
            except Exception:
                bench_rows = []

            if not bench_rows:
                return {"ok": True, "portco_id": portco_id,
                        "peer_segment": peer_segment, "confidence": "low",
                        "data": [], "note": "no benchmarks for segment"}

            # Latest KPI value per metric for the portco.
            kpi_rows = cx.execute(text("""
                SELECT DISTINCT ON (metric_name)
                    metric_name, actual, unit, period
                FROM dash.dash_portco_kpis
                WHERE portco_id = :pc AND actual IS NOT NULL
                ORDER BY metric_name, period_end DESC
            """), {"pc": portco_id}).fetchall()
        portco_kpis = {r[0].lower(): {"actual": float(r[1]),
                                        "unit": r[2], "period": r[3]}
                       for r in kpi_rows}

        comparisons: list[dict] = []
        for b in bench_rows:
            meta = b[2] if isinstance(b[2], dict) else {}
            bench_value = meta.get("value") or meta.get("median")
            bench_metric = (meta.get("metric") or b[0] or "").lower()
            try:
                bv = float(bench_value) if bench_value is not None else None
            except (TypeError, ValueError):
                bv = None
            mine = portco_kpis.get(bench_metric)
            comparisons.append({
                "metric": bench_metric,
                "portco_value": mine.get("actual") if mine else None,
                "benchmark_value": bv,
                "delta_pct": (
                    ((mine["actual"] - bv) / bv * 100) if (mine and bv) else None
                ),
                "unit": (mine or {}).get("unit") or meta.get("unit"),
            })
        return {"ok": True, "portco_id": portco_id,
                "peer_segment": peer_segment,
                "confidence": "medium" if comparisons else "low",
                "data": comparisons}
    except Exception as e:
        logger.exception("benchmark_portco failed")
        return {"ok": False, "error": str(e)}


# ── 10. watchlist_add ───────────────────────────────────────────────────

def watchlist_add(portco_id: str, reason: str) -> dict:
    """Flip portco status to 'watch'. Reason required (non-empty)."""
    if not reason or not str(reason).strip():
        return {"ok": False, "error": "reason is required (non-empty)"}
    try:
        with _write_engine().begin() as cx:
            r = cx.execute(text("""
                UPDATE dash.dash_portco
                SET status = 'watch', updated_at = now()
                WHERE id = :pc
                RETURNING id, legal_name
            """), {"pc": portco_id}).fetchone()
        if not r:
            return {"ok": False, "error": "portco not found"}
        # Best-effort proactive insight w/ reason (audit trail).
        try:
            with _write_engine().begin() as cx:
                cx.execute(text("""
                    INSERT INTO public.dash_proactive_insights
                        (project_slug, user_id, insight, severity, tables_involved)
                    SELECT project_slug, NULL,
                           'Watchlist: ' || legal_name || ' — ' || :rsn,
                           'warn',
                           ARRAY['dash_portco']
                    FROM dash.dash_portco WHERE id = :pc
                """), {"pc": portco_id, "rsn": reason.strip()})
        except Exception:
            logger.debug("watchlist_add: insight insert skipped",
                         exc_info=True)
        return {"ok": True, "portco_id": str(r[0]),
                "legal_name": r[1], "status": "watch", "reason": reason}
    except Exception as e:
        logger.exception("watchlist_add failed")
        return {"ok": False, "error": str(e)}


# ── @tool factory ───────────────────────────────────────────────────────

def create_ops_tools(project_slug: str, user_id: Optional[int] = None):
    """Return Agno @tool wrappers for Ops Optimizer."""
    from agno.tools import tool

    @tool(name="register_portco",
          description="Promote a closed deal to a portfolio company (portco). "
          "Verifies underlying dash_venture_deals.stage='closed' first. "
          "Args: deal_id (uuid), legal_name, investment_date (YYYY-MM-DD), "
          "ownership_pct (float, e.g. 15.5 for 15.5%), board_seat (bool), "
          "sector (optional).")
    def _register(deal_id: str, legal_name: str, investment_date: str,
                   ownership_pct: float, board_seat: bool = False,
                   sector: str = "") -> str:
        r = register_portco(project_slug, deal_id, legal_name, investment_date,
                             ownership_pct, board_seat, sector or None)
        return json.dumps(r, default=str)

    @tool(name="ingest_kpis",
          description="Bulk upsert KPI rows. Idempotent on "
          "(portco_id, metric_name, period). NEVER computes variance_pct "
          "(DB GENERATED). Args: portco_id, period (e.g. '2026-Q1'), "
          "metrics (list of {name, category, unit, actual, plan, forecast, "
          "period_start, period_end}), source (manual|api|upload|agent).")
    def _ingest(portco_id: str, period: str, metrics: List[dict],
                 source: str = "manual") -> str:
        r = ingest_kpis(portco_id, period, metrics, source)
        return json.dumps(r, default=str)

    @tool(name="kpi_dashboard",
          description="Return KPI time-series w/ variance_pct for a portco. "
          "Args: portco_id, since_periods (default 12).")
    def _dash(portco_id: str, since_periods: int = 12) -> str:
        r = kpi_dashboard(portco_id, since_periods)
        return json.dumps(r, default=str)

    @tool(name="detect_anomalies",
          description="Z-score scan over last 12 periods per metric. "
          "Writes warn (|z|>2) or critical (|z|>3) rows. Idempotent. "
          "Args: portco_id, z_threshold (default 2.0).")
    def _detect(portco_id: str, z_threshold: float = 2.0) -> str:
        r = detect_anomalies(portco_id, z_threshold)
        return json.dumps(r, default=str)

    @tool(name="propose_value_play",
          description="Generate a proposed initiative based on worst KPI drift. "
          "Always status='proposed'. Description includes one KPI name + "
          "actual/plan numbers. Args: portco_id, focus (optional metric "
          "filter), play_type (optional override), owner (optional).")
    def _propose(portco_id: str, focus: str = "", play_type: str = "",
                  owner: str = "") -> str:
        r = propose_value_play(portco_id, focus or None,
                                play_type or None, owner or None)
        return json.dumps(r, default=str)

    @tool(name="update_initiative",
          description="Move an initiative through proposed → approved → "
          "in_progress → done/cancelled. Auto-stamps updated_at. "
          "Args: initiative_id, status, notes (optional appended), owner, "
          "due_date.")
    def _update(initiative_id: str, status: str = "", notes: str = "",
                 owner: str = "", due_date: str = "") -> str:
        r = update_initiative(initiative_id, status or None,
                                notes or None, owner or None,
                                due_date or None)
        return json.dumps(r, default=str)

    @tool(name="portfolio_health",
          description="Cross-portco rollup. Counts green/yellow/red based on "
          "latest avg variance_pct. green > -5%, yellow -5..-15, red < -15. "
          "Lists top-3 risks.")
    def _health() -> str:
        r = portfolio_health(project_slug)
        return json.dumps(r, default=str)

    @tool(name="generate_board_pack",
          description="Render board-pack PDF for a portco + meeting_date. "
          "Saves to disk, persists row, returns id + url. "
          "Args: portco_id, meeting_date (YYYY-MM-DD).")
    def _board(portco_id: str, meeting_date: str) -> str:
        r = generate_board_pack(portco_id, meeting_date)
        return json.dumps(r, default=str)

    @tool(name="benchmark_portco",
          description="Compare portco's latest KPIs vs sector benchmarks in "
          "Company Brain (category='sector_benchmark'). Returns "
          "confidence='low' + empty data if benchmarks unavailable. "
          "Args: portco_id, peer_segment.")
    def _bench(portco_id: str, peer_segment: str) -> str:
        r = benchmark_portco(portco_id, peer_segment)
        return json.dumps(r, default=str)

    @tool(name="watchlist_add",
          description="Flip a portco status to 'watch'. Reason required.")
    def _watch(portco_id: str, reason: str) -> str:
        r = watchlist_add(portco_id, reason)
        return json.dumps(r, default=str)

    return [_register, _ingest, _dash, _detect, _propose, _update, _health,
            _board, _bench, _watch]
