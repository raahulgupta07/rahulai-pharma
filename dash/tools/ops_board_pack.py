"""
Ops Optimizer board-pack PDF generator.

Reuses reportlab platypus pattern from venture_memo.py. Sections:
  - Cover
  - Exec summary
  - KPI grid (actual / plan / var% w/ color tags)
  - Open initiatives
  - Recent anomalies
  - Decisions placeholder

Optional KPI trend chart via matplotlib (Agg backend, lazy import).
Caller (ops_tools.generate_board_pack) writes bytes to disk.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception as e:
        raise RuntimeError(f"db unavailable: {e}")


def _fmt_num(n) -> str:
    if n is None:
        return "—"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    a = abs(n)
    if a >= 1e9:
        return f"{n/1e9:.2f}B"
    if a >= 1e6:
        return f"{n/1e6:.2f}M"
    if a >= 1e3:
        return f"{n/1e3:.1f}K"
    return f"{n:.2f}"


def _fmt_var(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _var_color(var_pct) -> str:
    """green > -5% · yellow -5..-15% · red < -15%."""
    if var_pct is None:
        return "#6b6557"
    try:
        v = float(var_pct)
    except (TypeError, ValueError):
        return "#6b6557"
    if v > -5:
        return "#2c7a3f"
    if v > -15:
        return "#c96342"
    return "#c0392b"


def _fetch_portco_context(portco_id: str) -> dict:
    """Pull portco + last 12 periods of KPIs + open initiatives +
    unacked anomalies."""
    eng = _engine()
    with eng.connect() as cx:
        p = cx.execute(text("""
            SELECT id, project_slug, legal_name, investment_date,
                   ownership_pct, board_seat, sector, status,
                   stage_at_invest, fiscal_year_end
            FROM dash.dash_portco WHERE id = :pc
        """), {"pc": portco_id}).fetchone()
        if not p:
            return {}

        kpis = cx.execute(text("""
            SELECT metric_name, metric_category, unit, period,
                   period_end, actual, plan, forecast, variance_pct
            FROM dash.dash_portco_kpis
            WHERE portco_id = :pc
            ORDER BY metric_name, period_end DESC
        """), {"pc": portco_id}).fetchall()

        inits = cx.execute(text("""
            SELECT id, title, description, play_type, owner,
                   target_metric, target_delta_pct, target_value_usd,
                   status, due_date, created_at, updated_at
            FROM dash.dash_portco_initiatives
            WHERE portco_id = :pc
              AND status IN ('proposed','approved','in_progress')
            ORDER BY status, updated_at DESC LIMIT 20
        """), {"pc": portco_id}).fetchall()

        anoms = cx.execute(text("""
            SELECT metric_name, period, severity, z_score,
                   explanation, detected_at
            FROM dash.dash_portco_anomalies
            WHERE portco_id = :pc AND acknowledged = false
            ORDER BY detected_at DESC LIMIT 15
        """), {"pc": portco_id}).fetchall()

    # Group KPIs (last 12 per metric).
    by_metric: dict[str, list] = {}
    for r in kpis:
        by_metric.setdefault(r[0], []).append(r)
    for k in list(by_metric.keys()):
        by_metric[k] = by_metric[k][:12]

    # Latest snapshot row per metric.
    latest = []
    for metric, rows in by_metric.items():
        if rows:
            r = rows[0]
            latest.append({
                "metric_name": r[0],
                "category": r[1],
                "unit": r[2],
                "period": r[3],
                "actual": float(r[5]) if r[5] is not None else None,
                "plan": float(r[6]) if r[6] is not None else None,
                "variance_pct": float(r[8]) if r[8] is not None else None,
            })

    return {
        "portco": {
            "id": str(p[0]), "project_slug": p[1], "legal_name": p[2],
            "investment_date": p[3].isoformat() if p[3] else None,
            "ownership_pct": float(p[4]) if p[4] else None,
            "board_seat": p[5], "sector": p[6], "status": p[7],
            "stage_at_invest": p[8], "fiscal_year_end": p[9],
        },
        "kpis_latest": latest,
        "kpis_series": by_metric,
        "initiatives": [
            {
                "id": str(i[0]), "title": i[1], "description": i[2],
                "play_type": i[3], "owner": i[4], "target_metric": i[5],
                "target_delta_pct": float(i[6]) if i[6] is not None else None,
                "target_value_usd": float(i[7]) if i[7] is not None else None,
                "status": i[8],
                "due_date": i[9].isoformat() if i[9] else None,
            } for i in inits
        ],
        "anomalies": [
            {
                "metric_name": a[0], "period": a[1], "severity": a[2],
                "z_score": float(a[3]) if a[3] is not None else None,
                "explanation": a[4],
                "detected_at": a[5].isoformat() if a[5] else None,
            } for a in anoms
        ],
    }


def _render_kpi_chart(metric: str, series_rows: list) -> bytes:
    """Single-KPI actual-vs-plan trend. Returns PNG bytes or empty."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return b""
    pts = [(r[4], r[5], r[6]) for r in series_rows[:12] if r[4] is not None]
    pts.reverse()  # oldest first
    if len(pts) < 2:
        return b""
    try:
        xs = [p[0] for p in pts]
        ya = [float(p[1]) if p[1] is not None else None for p in pts]
        yp = [float(p[2]) if p[2] is not None else None for p in pts]
        fig, ax = plt.subplots(figsize=(6, 3), dpi=100)
        ax.plot(xs, ya, marker="o", color="#c96342", label="actual")
        ax.plot(xs, yp, marker="s", color="#1a1614", linestyle="--",
                label="plan")
        ax.set_title(f"{metric} — actual vs plan", fontsize=10)
        ax.legend(fontsize=8)
        ax.tick_params(axis="x", labelsize=7, rotation=30)
        ax.tick_params(axis="y", labelsize=8)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"kpi chart failed: {e}")
        return b""


def generate_board_pack_pdf(portco_id: str, meeting_date: str) -> bytes:
    """Return board pack PDF bytes. Caller writes to disk."""
    ctx = _fetch_portco_context(portco_id)
    if not ctx:
        raise ValueError(f"portco {portco_id} not found")

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
        )
    except Exception as e:
        raise RuntimeError(f"reportlab unavailable: {e}")

    portco = ctx["portco"]
    kpis = ctx["kpis_latest"]
    inits = ctx["initiatives"]
    anoms = ctx["anomalies"]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=20,
                        textColor=colors.HexColor("#1a1614"), spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12,
                        textColor=colors.HexColor("#c96342"),
                        spaceAfter=6, spaceBefore=10)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5,
                          textColor=colors.HexColor("#2c2a26"), leading=13)
    muted = ParagraphStyle("muted", parent=body,
                            textColor=colors.HexColor("#6b6557"), fontSize=8.5)

    story = []

    # ── Cover ──
    story.append(Paragraph(f"Board Pack — {portco['legal_name']}", h1))
    story.append(Paragraph(
        f"Meeting {meeting_date} · {portco.get('sector') or '—'} · "
        f"Ownership {portco.get('ownership_pct') or 0:.1f}% · "
        f"Status {portco.get('status') or '—'} · "
        f"Invested {portco.get('investment_date') or '—'} · "
        f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        muted))
    story.append(Spacer(1, 8))

    # ── Exec summary ──
    story.append(Paragraph("Executive summary", h2))
    green = sum(1 for k in kpis
                if k["variance_pct"] is not None and k["variance_pct"] > -5)
    yellow = sum(1 for k in kpis
                  if k["variance_pct"] is not None
                  and -15 < k["variance_pct"] <= -5)
    red = sum(1 for k in kpis
              if k["variance_pct"] is not None and k["variance_pct"] <= -15)
    open_inits = len(inits)
    unacked = len(anoms)
    crit = sum(1 for a in anoms if a.get("severity") == "critical")
    story.append(Paragraph(
        f"{len(kpis)} KPIs tracked. Health: <font color='#2c7a3f'><b>{green} green</b></font>, "
        f"<font color='#c96342'><b>{yellow} yellow</b></font>, "
        f"<font color='#c0392b'><b>{red} red</b></font>. "
        f"{open_inits} open initiatives. {unacked} unacknowledged anomalies "
        f"({crit} critical).",
        body))
    story.append(Spacer(1, 6))

    # ── KPI grid ──
    story.append(Paragraph("KPI snapshot", h2))
    if kpis:
        rows = [["Metric", "Period", "Actual", "Plan", "Var %"]]
        for k in kpis:
            unit = k.get("unit") or ""
            color = _var_color(k["variance_pct"])
            var_str = _fmt_var(k["variance_pct"])
            rows.append([
                k["metric_name"],
                k["period"],
                f"{_fmt_num(k['actual'])} {unit}".strip(),
                f"{_fmt_num(k['plan'])} {unit}".strip(),
                Paragraph(f"<font color='{color}'><b>{var_str}</b></font>",
                          body),
            ])
        t = Table(rows, colWidths=[55 * mm, 25 * mm, 30 * mm, 30 * mm, 25 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1614")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f7f6f3")]),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d6cfbe")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e8e3d6")),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("No KPI data yet.", muted))
    story.append(Spacer(1, 8))

    # ── KPI chart (one trend) ──
    if kpis and ctx["kpis_series"]:
        # Pick worst variance metric for the trend chart.
        worst = min(
            (k for k in kpis if k["variance_pct"] is not None),
            key=lambda k: k["variance_pct"], default=None,
        )
        if worst:
            series_rows = ctx["kpis_series"].get(worst["metric_name"], [])
            try:
                img_bytes = _render_kpi_chart(worst["metric_name"], series_rows)
                if img_bytes:
                    from reportlab.platypus import Image as RLImage
                    story.append(Paragraph(
                        f"Trend — {worst['metric_name']}", h2))
                    story.append(RLImage(io.BytesIO(img_bytes),
                                          width=150 * mm, height=75 * mm,
                                          kind="proportional"))
                    story.append(Spacer(1, 6))
            except Exception:
                pass

    # ── Initiatives ──
    story.append(Paragraph("Open initiatives", h2))
    if inits:
        rows = [["Title", "Type", "Owner", "Status", "Due"]]
        for i in inits[:10]:
            rows.append([
                (i["title"] or "—")[:60],
                (i["play_type"] or "—"),
                (i["owner"] or "—")[:20],
                (i["status"] or "—").upper(),
                i.get("due_date") or "—",
            ])
        t = Table(rows, colWidths=[65 * mm, 30 * mm, 28 * mm, 25 * mm, 22 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e3d6")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 4),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e8e3d6")),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("No open initiatives.", muted))
    story.append(Spacer(1, 8))

    # ── Recent anomalies ──
    story.append(Paragraph("Recent anomalies", h2))
    if anoms:
        rows = [["Metric", "Period", "Severity", "Z", "Detail"]]
        for a in anoms[:10]:
            sev_color = ("#c0392b" if a["severity"] == "critical"
                          else "#c96342" if a["severity"] == "warn"
                          else "#6b6557")
            rows.append([
                a["metric_name"],
                a["period"],
                Paragraph(
                    f"<font color='{sev_color}'><b>{a['severity'].upper()}</b></font>",
                    body),
                f"{a['z_score']:+.2f}" if a["z_score"] is not None else "—",
                (a["explanation"] or "")[:80],
            ])
        t = Table(rows, colWidths=[35 * mm, 25 * mm, 22 * mm, 18 * mm, 70 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1614")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f7f6f3")]),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e8e3d6")),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("No unacknowledged anomalies.", muted))
    story.append(Spacer(1, 8))

    # ── Decisions placeholder ──
    story.append(Paragraph("Decisions for this meeting", h2))
    story.append(Paragraph(
        "<i>To be captured during the meeting and persisted via the "
        "decisions API.</i>", muted))

    # Footer
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Confidential · Portco {portco['id'][:8]} · "
        f"Project {portco.get('project_slug', '')}",
        muted))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
