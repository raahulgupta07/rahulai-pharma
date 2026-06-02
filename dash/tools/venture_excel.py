"""
Venture IC memo Excel export.

Single workbook, 4 sheets (Summary, Scenarios, Sensitivity, Competitors)
generated via XlsxWriter. Reuses _fetch_deal from venture_memo for parity
with PDF/PPTX exports.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Optional

from dash.tools.venture_memo import _fetch_deal

logger = logging.getLogger(__name__)


# ---------- format helpers ----------

def _fmt_money(n):
    if n is None:
        return "—"
    a = abs(n)
    if a >= 1e9:
        return f"{n/1e9:.2f}B"
    if a >= 1e6:
        return f"{n/1e6:.2f}M"
    if a >= 1e3:
        return f"{n/1e3:.1f}K"
    return f"{n:.0f}"


def _fmt_pct(n):
    if n is None:
        return "—"
    return f"{n*100:.1f}%"


def _verdict_color(v: Optional[str]) -> str:
    v = (v or "hold").lower()
    if v == "go":
        return "#2c7a3f"
    if v == "pass":
        return "#c0392b"
    return "#c96342"  # hold / coral


# ---------- main ----------

def generate_xlsx(deal_id: str) -> bytes:
    """Generate IC one-pager XLSX. Returns bytes."""
    data = _fetch_deal(deal_id)
    if not data:
        raise ValueError(f"deal {deal_id} not found")

    try:
        import xlsxwriter
    except Exception as e:
        raise RuntimeError(f"xlsxwriter unavailable: {e}")

    deal = data["deal"]
    scens = data["scenarios"] or []
    comps = data["competitors"] or []
    base = scens[0] if scens else {}

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})

    # ---------- formats ----------
    fmt_title = wb.add_format({
        "bold": True, "font_size": 20, "font_color": "#1A1614",
        "align": "left", "valign": "vcenter",
    })
    fmt_subtitle = wb.add_format({
        "italic": True, "font_size": 10, "font_color": "#6b6557",
        "align": "left", "valign": "vcenter",
    })
    fmt_section = wb.add_format({
        "bold": True, "font_size": 11, "font_color": "#C96342",
        "align": "left", "valign": "vcenter",
        "bottom": 1, "border_color": "#d6cfbe",
    })
    fmt_label = wb.add_format({
        "bold": True, "font_size": 10, "font_color": "#C96342",
        "align": "left", "valign": "vcenter", "bg_color": "#F7F6F3",
        "border": 1, "border_color": "#e8e3d6",
    })
    fmt_value = wb.add_format({
        "font_size": 10, "font_color": "#1A1614",
        "align": "left", "valign": "vcenter",
        "border": 1, "border_color": "#e8e3d6",
    })
    fmt_value_money = wb.add_format({
        "font_size": 10, "font_color": "#1A1614",
        "align": "right", "valign": "vcenter",
        "border": 1, "border_color": "#e8e3d6",
        "num_format": "#,##0",
    })

    fmt_header = wb.add_format({
        "bold": True, "font_size": 10, "font_color": "#FFFFFF",
        "bg_color": "#1A1614",
        "align": "left", "valign": "vcenter",
        "border": 1, "border_color": "#1A1614",
    })
    fmt_row_a = wb.add_format({
        "font_size": 10, "font_color": "#1A1614",
        "bg_color": "#FFFFFF",
        "align": "left", "valign": "vcenter",
        "border": 1, "border_color": "#e8e3d6",
    })
    fmt_row_b = wb.add_format({
        "font_size": 10, "font_color": "#1A1614",
        "bg_color": "#F7F6F3",
        "align": "left", "valign": "vcenter",
        "border": 1, "border_color": "#e8e3d6",
    })
    fmt_irr_a = wb.add_format({
        "font_size": 10, "bg_color": "#FFFFFF",
        "align": "right", "border": 1, "border_color": "#e8e3d6",
        "num_format": "0.0%",
    })
    fmt_irr_b = wb.add_format({
        "font_size": 10, "bg_color": "#F7F6F3",
        "align": "right", "border": 1, "border_color": "#e8e3d6",
        "num_format": "0.0%",
    })
    fmt_moic_a = wb.add_format({
        "font_size": 10, "bg_color": "#FFFFFF",
        "align": "right", "border": 1, "border_color": "#e8e3d6",
        "num_format": '0.00"×"',
    })
    fmt_moic_b = wb.add_format({
        "font_size": 10, "bg_color": "#F7F6F3",
        "align": "right", "border": 1, "border_color": "#e8e3d6",
        "num_format": '0.00"×"',
    })
    fmt_npv_a = wb.add_format({
        "font_size": 10, "bg_color": "#FFFFFF",
        "align": "right", "border": 1, "border_color": "#e8e3d6",
        "num_format": "#,##0",
    })
    fmt_npv_b = wb.add_format({
        "font_size": 10, "bg_color": "#F7F6F3",
        "align": "right", "border": 1, "border_color": "#e8e3d6",
        "num_format": "#,##0",
    })
    fmt_pb_a = wb.add_format({
        "font_size": 10, "bg_color": "#FFFFFF",
        "align": "right", "border": 1, "border_color": "#e8e3d6",
        "num_format": "0.0",
    })
    fmt_pb_b = wb.add_format({
        "font_size": 10, "bg_color": "#F7F6F3",
        "align": "right", "border": 1, "border_color": "#e8e3d6",
        "num_format": "0.0",
    })

    # verdict color formats
    def _verdict_fmt(verdict: Optional[str], alt: bool):
        v = (verdict or "hold").lower()
        if v == "go":
            color = "#2c7a3f"
        elif v == "pass":
            color = "#c0392b"
        else:
            color = "#c96342"
        return wb.add_format({
            "font_size": 10, "bold": True, "font_color": color,
            "bg_color": "#F7F6F3" if alt else "#FFFFFF",
            "align": "center", "valign": "vcenter",
            "border": 1, "border_color": "#e8e3d6",
        })

    # ---------- Sheet 1: Summary ----------
    ws = wb.add_worksheet("Summary")
    ws.set_column("A:A", 22)
    ws.set_column("B:B", 28)
    ws.set_column("C:C", 18)
    ws.set_column("D:D", 22)

    ws.merge_range("A1:D1", f"IC Memo — {deal.get('name','—')}", fmt_title)
    ws.set_row(0, 28)

    subtitle = (
        f"{(deal.get('stage') or '').replace('_',' ').title()} · "
        f"{deal.get('sector') or '—'} · {deal.get('geography') or '—'} · "
        f"Ask {_fmt_money(deal.get('ask_amount'))} · "
        f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    ws.merge_range("A2:D2", subtitle, fmt_subtitle)
    ws.set_row(1, 16)

    # Deal metadata
    ws.merge_range("A4:D4", "Deal Metadata", fmt_section)
    meta_rows = [
        ("Name", deal.get("name") or "—", False),
        ("Stage", (deal.get("stage") or "—").replace("_", " ").title(), False),
        ("Sector", deal.get("sector") or "—", False),
        ("Geography", deal.get("geography") or "—", False),
        ("Ask", deal.get("ask_amount"), True),
        ("Pre-money", deal.get("pre_money"), True),
        ("Post-money", deal.get("post_money"), True),
        ("Status", (deal.get("status") or "—").title(), False),
        ("Created at", deal.get("created_at") or "—", False),
    ]
    r = 4
    for label, val, is_money in meta_rows:
        ws.write(r, 0, label, fmt_label)
        if is_money and val is not None:
            ws.write_number(r, 1, float(val), fmt_value_money)
        else:
            ws.write(r, 1, val if val is not None else "—", fmt_value)
        r += 1

    # KPI strip
    kpi_row = r + 1
    ws.merge_range(kpi_row, 0, kpi_row, 3, "Latest Scenario KPIs", fmt_section)
    kpi_labels_row = kpi_row + 1
    kpi_vals_row = kpi_row + 2
    kpi_headers = ["IRR", "MOIC", "NPV", "Payback (yrs)"]
    for i, h in enumerate(kpi_headers):
        ws.write(kpi_labels_row, i, h, fmt_label)
    if base.get("irr") is not None:
        ws.write_number(kpi_vals_row, 0, float(base["irr"]), wb.add_format({
            "font_size": 12, "align": "right", "border": 1,
            "border_color": "#e8e3d6", "num_format": "0.0%",
        }))
    else:
        ws.write(kpi_vals_row, 0, "—", fmt_value)
    if base.get("moic") is not None:
        ws.write_number(kpi_vals_row, 1, float(base["moic"]), wb.add_format({
            "font_size": 12, "align": "right", "border": 1,
            "border_color": "#e8e3d6", "num_format": '0.00"×"',
        }))
    else:
        ws.write(kpi_vals_row, 1, "—", fmt_value)
    if base.get("npv") is not None:
        ws.write_number(kpi_vals_row, 2, float(base["npv"]), wb.add_format({
            "font_size": 12, "align": "right", "border": 1,
            "border_color": "#e8e3d6", "num_format": "#,##0",
        }))
    else:
        ws.write(kpi_vals_row, 2, "—", fmt_value)
    if base.get("payback_yrs") is not None:
        ws.write_number(kpi_vals_row, 3, float(base["payback_yrs"]), wb.add_format({
            "font_size": 12, "align": "right", "border": 1,
            "border_color": "#e8e3d6", "num_format": "0.0",
        }))
    else:
        ws.write(kpi_vals_row, 3, "—", fmt_value)

    # Verdict (color-coded)
    verdict_row = kpi_vals_row + 2
    ws.write(verdict_row, 0, "Verdict", fmt_label)
    verdict = (base.get("verdict") or "hold").upper()
    vcolor = _verdict_color(base.get("verdict"))
    fmt_verdict = wb.add_format({
        "bold": True, "font_size": 14, "font_color": vcolor,
        "align": "left", "valign": "vcenter",
        "border": 1, "border_color": "#e8e3d6",
    })
    ws.merge_range(verdict_row, 1, verdict_row, 3, verdict, fmt_verdict)

    # ---------- Sheet 2: Scenarios ----------
    ws2 = wb.add_worksheet("Scenarios")
    ws2.set_column("A:A", 22)
    ws2.set_column("B:B", 14)
    ws2.set_column("C:C", 14)
    ws2.set_column("D:D", 16)
    ws2.set_column("E:E", 18)
    ws2.set_column("F:F", 14)
    ws2.set_column("G:G", 22)

    headers = ["Name", "IRR", "MOIC", "Payback (yrs)", "NPV", "Verdict", "Created at"]
    for i, h in enumerate(headers):
        ws2.write(0, i, h, fmt_header)
    ws2.set_row(0, 22)

    if not scens:
        ws2.merge_range(1, 0, 1, 6, "No scenarios saved yet.", fmt_value)
    else:
        for idx, s in enumerate(scens):
            alt = idx % 2 == 1
            row_fmt = fmt_row_b if alt else fmt_row_a
            irr_fmt = fmt_irr_b if alt else fmt_irr_a
            moic_fmt = fmt_moic_b if alt else fmt_moic_a
            npv_fmt = fmt_npv_b if alt else fmt_npv_a
            pb_fmt = fmt_pb_b if alt else fmt_pb_a
            v_fmt = _verdict_fmt(s.get("verdict"), alt)

            r2 = idx + 1
            ws2.write(r2, 0, s.get("name") or "—", row_fmt)
            if s.get("irr") is not None:
                ws2.write_number(r2, 1, float(s["irr"]), irr_fmt)
            else:
                ws2.write(r2, 1, "—", row_fmt)
            if s.get("moic") is not None:
                ws2.write_number(r2, 2, float(s["moic"]), moic_fmt)
            else:
                ws2.write(r2, 2, "—", row_fmt)
            if s.get("payback_yrs") is not None:
                ws2.write_number(r2, 3, float(s["payback_yrs"]), pb_fmt)
            else:
                ws2.write(r2, 3, "—", row_fmt)
            if s.get("npv") is not None:
                ws2.write_number(r2, 4, float(s["npv"]), npv_fmt)
            else:
                ws2.write(r2, 4, "—", row_fmt)
            ws2.write(r2, 5, (s.get("verdict") or "hold").upper(), v_fmt)
            ws2.write(r2, 6, s.get("created_at") or "—", row_fmt)

    # ---------- Sheet 3: Sensitivity ----------
    ws3 = wb.add_worksheet("Sensitivity")
    ws3.set_column("A:A", 16)
    ws3.set_column("B:F", 14)

    wacc_range = [0.08, 0.10, 0.12, 0.14, 0.16]
    growth_range = [0.01, 0.02, 0.03, 0.04, 0.05]

    cashflows = None
    if base:
        inputs = base.get("inputs") or {}
        cf = inputs.get("cashflows")
        if isinstance(cf, list) and cf:
            cashflows = cf

    if not cashflows:
        ws3.merge_range(
            "A1:F1",
            "No sensitivity available — save a scenario w/ cashflows first.",
            fmt_subtitle,
        )
    else:
        try:
            from dash.tools.venture_tools import sensitivity_grid
            grid_res = sensitivity_grid(cashflows, wacc_range, growth_range)
            # sensitivity_grid returns {"ok": bool, "grid": [[...]], ...} or list-of-lists; handle both
            if isinstance(grid_res, dict):
                grid = grid_res.get("grid") or grid_res.get("npv_grid") or []
            else:
                grid = grid_res or []
        except Exception as e:
            logger.debug(f"sensitivity_grid failed: {e}")
            grid = []

        if not grid:
            ws3.merge_range(
                "A1:F1",
                "No sensitivity available — save a scenario w/ cashflows first.",
                fmt_subtitle,
            )
        else:
            # Header row: growth labels across
            ws3.write(0, 0, "WACC \\ Growth", fmt_header)
            for j, g in enumerate(growth_range):
                ws3.write(0, j + 1, f"{g*100:.0f}%", fmt_header)
            ws3.set_row(0, 22)

            fmt_grid_label = wb.add_format({
                "bold": True, "font_size": 10, "font_color": "#FFFFFF",
                "bg_color": "#1A1614",
                "align": "center", "valign": "vcenter",
                "border": 1, "border_color": "#1A1614",
            })
            fmt_grid_val = wb.add_format({
                "font_size": 10, "align": "right", "valign": "vcenter",
                "border": 1, "border_color": "#e8e3d6",
                "num_format": "#,##0",
            })

            for i, w in enumerate(wacc_range):
                ws3.write(i + 1, 0, f"{w*100:.0f}%", fmt_grid_label)
                row_vals = []
                for j in range(len(growth_range)):
                    try:
                        val = grid[i][j]
                    except (IndexError, TypeError):
                        val = None
                    if val is None:
                        ws3.write(i + 1, j + 1, "—", fmt_value)
                    else:
                        ws3.write_number(i + 1, j + 1, float(val), fmt_grid_val)
                        row_vals.append(float(val))

            # 3-color scale across grid cells
            last_row = len(wacc_range)
            last_col = len(growth_range)
            ws3.conditional_format(
                1, 1, last_row, last_col,
                {
                    "type": "3_color_scale",
                    "min_color": "#c0392b",
                    "mid_color": "#FFFFFF",
                    "max_color": "#2c7a3f",
                },
            )

    # ---------- Sheet 4: Competitors ----------
    ws4 = wb.add_worksheet("Competitors")
    ws4.set_column("A:A", 22)
    ws4.set_column("B:B", 14)
    ws4.set_column("C:C", 30)
    ws4.set_column("D:D", 18)

    comp_headers = ["Name", "Share %", "Moat", "Source"]
    for i, h in enumerate(comp_headers):
        ws4.write(0, i, h, fmt_header)
    ws4.set_row(0, 22)

    if not comps:
        ws4.merge_range(1, 0, 1, 3, "No competitors recorded.", fmt_value)
    else:
        fmt_share_a = wb.add_format({
            "font_size": 10, "bg_color": "#FFFFFF",
            "align": "right", "border": 1, "border_color": "#e8e3d6",
            "num_format": "0.0",
        })
        fmt_share_b = wb.add_format({
            "font_size": 10, "bg_color": "#F7F6F3",
            "align": "right", "border": 1, "border_color": "#e8e3d6",
            "num_format": "0.0",
        })
        for idx, c in enumerate(comps):
            alt = idx % 2 == 1
            row_fmt = fmt_row_b if alt else fmt_row_a
            share_fmt = fmt_share_b if alt else fmt_share_a
            r4 = idx + 1
            ws4.write(r4, 0, c.get("name") or "—", row_fmt)
            if c.get("share_pct") is not None:
                ws4.write_number(r4, 1, float(c["share_pct"]), share_fmt)
            else:
                ws4.write(r4, 1, "—", row_fmt)
            ws4.write(r4, 2, c.get("moat") or "—", row_fmt)
            ws4.write(r4, 3, c.get("source") or "—", row_fmt)

    wb.close()
    buf.seek(0)
    return buf.getvalue()
