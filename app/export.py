"""
Export API
==========

Generate PDF from chat content and PPTX from dashboards.
"""

import io
import os
import json
import re as _re
import tempfile
from pathlib import Path

try:
    import glob as _glob
    import base64 as _base64
    import subprocess as _subprocess
except ImportError:
    pass

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/export", tags=["Export"])


# ── Branding / icon helpers (used by PPTX exporters) ──────────────────────
LUCIDE_TO_GLYPH = {
    "trending-up": "▲", "trending-down": "▼", "minus": "━",
    "dollar-sign": "$", "percent": "%", "check": "✓", "x": "✗",
    "users": "👥", "user": "●", "package": "📦", "box": "📦",
    "check-circle": "✓", "alert-triangle": "⚠", "target": "◎",
    "clock": "◷", "calendar": "📅", "zap": "⚡", "trophy": "🏆",
    "circle": "•", "search": "🔍", "wrench": "🔧", "heart": "♥",
    "pill": "💊", "syringe": "◐", "shield-check": "🛡",
    "alert-circle": "⚠", "info": "i", "star": "★", "flag": "⚑",
    "arrow-up": "↑", "arrow-down": "↓", "arrow-right": "→",
}


def _glyph_for_icon(name: str) -> str:
    """Map a Lucide icon name → Unicode glyph. Fallback to bullet •."""
    if not name:
        return "•"
    return LUCIDE_TO_GLYPH.get(str(name).strip().lower(), "•")


def _get_brand_name() -> str:
    """Read tenant company.json["name"], fall back to CityAgent Insights."""
    try:
        from app.branding import _load_company  # type: ignore
        company = _load_company() or {}
        name = company.get("name")
        if name and isinstance(name, str):
            return name.strip()
    except Exception:
        pass
    # Direct file fallback
    try:
        candidates = [
            Path(__file__).parent.parent / "branding" / "default" / "company.json",
            Path.cwd() / "branding" / "default" / "company.json",
        ]
        for p in candidates:
            if p.exists():
                data = json.loads(p.read_text())
                if data.get("name"):
                    return str(data["name"]).strip()
    except Exception:
        pass
    return "CityAgent Insights"


def _fetch_hero_image(url: str, timeout: float = 5.0):
    """Fetch hero image bytes via httpx. Returns BytesIO or None on any failure."""
    if not url or not isinstance(url, str):
        return None
    try:
        import httpx
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 200 and resp.content:
                ct = resp.headers.get("content-type", "")
                if not ct.startswith("image/") and not url.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                    return None
                return io.BytesIO(resp.content)
    except Exception:
        return None
    return None


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


@router.post("/pdf")
async def export_pdf(request: Request):
    """Generate a PDF from markdown/HTML content."""
    _get_user(request)
    body = await request.json()
    content = body.get("content", "")
    title = _re.sub(r'[^\w\s\-.]', '', body.get("title", "Dash Export"))[:100] or 'export'

    if not content:
        raise HTTPException(400, "Content required")

    try:
        import markdown as md
        from weasyprint import HTML
    except ImportError:
        # Fallback: return HTML as downloadable file
        html_content = _build_html(title, content)
        buf = io.BytesIO(html_content.encode("utf-8"))
        return StreamingResponse(buf, media_type="text/html",
                                 headers={"Content-Disposition": f'attachment; filename="{title}.html"'})

    html_body = md.markdown(content, extensions=["tables", "fenced_code"])
    full_html = _build_html(title, html_body, is_raw_html=True)

    pdf_bytes = HTML(string=full_html).write_pdf()
    buf = io.BytesIO(pdf_bytes)
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{title}.pdf"'})



@router.get("/presentations")
def list_presentations(request: Request, project: str):
    _get_user(request)
    from sqlalchemy import create_engine as _ce, text
    from sqlalchemy.pool import NullPool
    from db import db_url
    engine = _ce(db_url, poolclass=NullPool)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, title, version, thinking, created_at "
            "FROM public.dash_presentations WHERE project_slug = :s "
            "ORDER BY created_at DESC"
        ), {"s": project}).fetchall()
    return {"presentations": [
        {"id": r[0], "title": r[1], "version": r[2], "thinking": r[3], "created_at": str(r[4])}
        for r in rows
    ]}


@router.get("/presentations/{pres_id}")
def get_presentation(pres_id: int, request: Request):
    _get_user(request)
    from sqlalchemy import create_engine as _ce, text
    from sqlalchemy.pool import NullPool
    from db import db_url
    engine = _ce(db_url, poolclass=NullPool)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, project_slug, title, version, thinking, slides, created_at "
            "FROM public.dash_presentations WHERE id = :id"
        ), {"id": pres_id}).fetchone()
    if not row:
        raise HTTPException(404, "Presentation not found")
    return {"id": row[0], "project_slug": row[1], "title": row[2], "version": row[3],
            "thinking": row[4], "slides": row[5], "created_at": str(row[6])}


@router.post("/presentations")
async def save_presentation(request: Request):
    _get_user(request)
    body = await request.json()
    project = body.get("project_slug", "")
    title = body.get("title", "Untitled")
    thinking = body.get("thinking", {})
    slides = body.get("slides", [])
    messages = body.get("source_messages", [])

    import json
    from sqlalchemy import create_engine as _ce, text
    from sqlalchemy.pool import NullPool
    from db import db_url
    engine = _ce(db_url, poolclass=NullPool)
    with engine.connect() as conn:
        # Check if same title exists — increment version
        existing = conn.execute(text(
            "SELECT MAX(version) FROM public.dash_presentations WHERE project_slug = :s AND title = :t"
        ), {"s": project, "t": title}).scalar()
        version = (existing or 0) + 1

        result = conn.execute(text(
            "INSERT INTO public.dash_presentations (project_slug, title, version, thinking, slides, source_messages) "
            "VALUES (:s, :t, :v, CAST(:th AS jsonb), CAST(:sl AS jsonb), CAST(:msg AS jsonb)) RETURNING id"
        ), {"s": project, "t": title, "v": version, "th": json.dumps(thinking), "sl": json.dumps(slides), "msg": json.dumps(messages)})
        pres_id = result.fetchone()[0]
        conn.commit()
    return {"status": "ok", "id": pres_id, "version": version}


@router.delete("/presentations/{pres_id}")
def delete_presentation(pres_id: int, request: Request):
    _get_user(request)
    from sqlalchemy import create_engine as _ce, text
    from sqlalchemy.pool import NullPool
    from db import db_url
    engine = _ce(db_url, poolclass=NullPool)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM public.dash_presentations WHERE id = :id"), {"id": pres_id})
        conn.commit()
    return {"status": "ok"}


@router.post("/presentations/{pres_id}/pptx")
def export_saved_pptx(pres_id: int, request: Request):
    _get_user(request)
    from sqlalchemy import create_engine as _ce, text
    from sqlalchemy.pool import NullPool
    from db import db_url
    engine = _ce(db_url, poolclass=NullPool)
    with engine.connect() as conn:
        # New render columns required (migration 096).
        try:
            row = conn.execute(text(
                "SELECT title, slides, thinking, rendered_pptx_path, pptxgenjs_spec "
                "FROM public.dash_presentations WHERE id = :id"
            ), {"id": pres_id}).fetchone()
        except Exception as e:
            raise HTTPException(500, f"migration 096 not applied: {e}")
    if not row:
        raise HTTPException(404, "Presentation not found")

    title = row[0]
    slides = row[1] if isinstance(row[1], list) else []
    thinking_data = row[2] if len(row) > 2 else None
    rendered_pptx_path = row[3] if len(row) > 3 else None
    pptxgenjs_spec = row[4] if len(row) > 4 else None

    import os as _os
    from fastapi.responses import FileResponse

    def _return_file(path: str):
        safe_name = (title or f"deck_{pres_id}").replace("/", "_") + ".pptx"
        return FileResponse(
            path,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
            filename=safe_name,
        )

    # 1. Already rendered → serve from disk
    if rendered_pptx_path and _os.path.isfile(rendered_pptx_path):
        return _return_file(rendered_pptx_path)

    # 2. Spec exists but not yet rendered → render NOW via Node + persist path
    if pptxgenjs_spec and isinstance(pptxgenjs_spec, dict) and (pptxgenjs_spec.get("slides") or []):
        try:
            from dash.tools.render_pptxgenjs import render_pptx_via_js
        except Exception as e:
            raise HTTPException(500, f"Node renderer unavailable: {e}")
        out_dir = f"/app/knowledge/_decks/{pres_id}"
        _os.makedirs(out_dir, exist_ok=True)
        out_path = _os.path.join(out_dir, "deck.pptx")
        try:
            new_path = render_pptx_via_js(pptxgenjs_spec, out_path)
        except Exception as e:
            raise HTTPException(500, f"Node render failed: {e}")
        if not new_path or not _os.path.isfile(new_path):
            raise HTTPException(500, "Node renderer produced no output")
        # Persist path so next download skips re-render
        try:
            with engine.connect() as conn2:
                conn2.execute(text(
                    "UPDATE public.dash_presentations "
                    "SET rendered_pptx_path = :p, render_engine = 'pptxgenjs' "
                    "WHERE id = :id"
                ), {"p": new_path, "id": pres_id})
                conn2.commit()
        except Exception:
            pass
        return _return_file(new_path)

    # 3. No spec, no rendered file → cannot serve. python-pptx fallback removed.
    raise HTTPException(
        500,
        "No pptxgenjs_spec on this presentation. Old python-pptx fallback removed. "
        "Regenerate deck via DP button.",
    )


@router.get("/presentations/{pres_id}/preview")
def get_pres_preview(pres_id: int, request: Request):
    """Return slide image URLs for in-UI rendering. Auto-renders + auto-QA if missing."""
    _get_user(request)
    from sqlalchemy import create_engine as _ce, text
    from sqlalchemy.pool import NullPool
    from db import db_url
    import os as _os
    engine = _ce(db_url, poolclass=NullPool)

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT title, rendered_pptx_path, pptxgenjs_spec, slides "
            "FROM public.dash_presentations WHERE id = :id"
        ), {"id": pres_id}).fetchone()
    if not row:
        raise HTTPException(404, "Presentation not found")
    title = row[0]
    rendered_pptx_path = row[1]
    spec = row[2]
    slides_col = row[3]
    # Fallback: legacy rows have slides JSONB but no pptxgenjs_spec
    if (not spec or not (isinstance(spec, dict) and spec.get("slides"))) and isinstance(slides_col, list) and slides_col:
        spec = {"title": title, "theme": "city_executive", "slides": slides_col}

    deck_dir = f"/app/knowledge/_decks/{pres_id}"
    qa_dir = _os.path.join(deck_dir, "qa")
    pptx_path = _os.path.join(deck_dir, "deck.pptx")

    if not (rendered_pptx_path and _os.path.isfile(rendered_pptx_path)):
        if not (spec and isinstance(spec, dict) and (spec.get("slides") or [])):
            # Graceful empty state — legacy decks may have no spec yet
            return {"title": title, "slides": [], "empty": True, "reason": "no_spec"}
        try:
            from dash.tools.render_pptxgenjs import render_pptx_via_js
            _os.makedirs(deck_dir, exist_ok=True)
            new_path = render_pptx_via_js(spec, pptx_path)
        except Exception as e:
            raise HTTPException(500, f"render failed: {e}")
        if not new_path or not _os.path.isfile(new_path):
            raise HTTPException(500, "render produced no output")
        rendered_pptx_path = new_path
        try:
            with engine.connect() as c2:
                c2.execute(text(
                    "UPDATE public.dash_presentations "
                    "SET rendered_pptx_path=:p, render_engine='pptxgenjs' WHERE id=:id"
                ), {"p": new_path, "id": pres_id})
                c2.commit()
        except Exception:
            pass

    existing_jpgs = sorted([
        f for f in (_os.listdir(qa_dir) if _os.path.isdir(qa_dir) else [])
        if f.lower().endswith((".jpg", ".jpeg"))
    ])
    if not existing_jpgs:
        try:
            from dash.tools.deep_deck import _run_qa_loop
            _run_qa_loop(rendered_pptx_path)
            existing_jpgs = sorted([
                f for f in _os.listdir(qa_dir)
                if f.lower().endswith((".jpg", ".jpeg"))
            ]) if _os.path.isdir(qa_dir) else []
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("qa loop failed in preview: %s", e)

    if not existing_jpgs and rendered_pptx_path:
        alt_qa = _os.path.join(_os.path.dirname(rendered_pptx_path), "qa")
        if _os.path.isdir(alt_qa):
            alt_jpgs = sorted([
                f for f in _os.listdir(alt_qa)
                if f.lower().endswith((".jpg", ".jpeg"))
            ])
            try:
                _os.makedirs(qa_dir, exist_ok=True)
                for f in alt_jpgs:
                    dst = _os.path.join(qa_dir, f)
                    if not _os.path.exists(dst):
                        _os.symlink(_os.path.join(alt_qa, f), dst)
                existing_jpgs = alt_jpgs
            except Exception:
                pass

    slides = [
        {"idx": i, "page": i + 1, "image_url": f"/decks/{pres_id}/qa/{f}"}
        for i, f in enumerate(existing_jpgs)
    ]
    return {
        "ok": True,
        "pres_id": pres_id,
        "title": title,
        "slides": slides,
        "pptx_url": f"/api/export/presentations/{pres_id}/pptx",
    }


@router.post("/excel-from-chat")
async def export_excel_from_chat(request: Request):
    """Generate Excel workbook from chat messages with tables, charts, and narrative."""
    _get_user(request)
    body = await request.json()
    messages = body.get("messages", [])
    title = _re.sub(r'[^\w\s\-.]', '', body.get("title", "Analysis Report"))[:100] or 'export'
    agent_name = body.get("agent_name", "Agent")

    if not messages:
        raise HTTPException(400, "Messages required")

    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {'in_memory': True})

    # Styles
    title_fmt = wb.add_format({'bold': True, 'font_size': 18, 'font_color': '#1a1a1a'})
    heading_fmt = wb.add_format({'bold': True, 'font_size': 13, 'font_color': '#217346', 'bottom': 2, 'bottom_color': '#217346'})
    bold_fmt = wb.add_format({'bold': True, 'font_size': 11})
    text_fmt = wb.add_format({'font_size': 11, 'text_wrap': True, 'valign': 'top'})
    header_fmt = wb.add_format({'bold': True, 'font_size': 10, 'bg_color': '#1a1a1a', 'font_color': '#ffffff', 'border': 1})
    cell_fmt = wb.add_format({'font_size': 10, 'border': 1, 'text_wrap': True, 'valign': 'top'})
    num_fmt = wb.add_format({'font_size': 10, 'border': 1, 'num_format': '#,##0.00'})
    pct_fmt = wb.add_format({'font_size': 10, 'border': 1, 'num_format': '0%'})
    date_fmt = wb.add_format({'font_size': 9, 'font_color': '#888888', 'italic': True})

    # ─── Sheet 1: Executive Summary ─────────────────────────────
    ws1 = wb.add_worksheet('Executive Summary')
    ws1.set_column('A:A', 80)
    ws1.hide_gridlines(2)

    row = 0
    ws1.write(row, 0, title, title_fmt); row += 1
    ws1.write(row, 0, f'Generated by {agent_name}', date_fmt); row += 1
    import datetime
    ws1.write(row, 0, f'Date: {datetime.date.today().strftime("%B %d, %Y")}', date_fmt); row += 2

    # Extract key content from assistant messages
    ws1.write(row, 0, 'KEY FINDINGS', heading_fmt); row += 1
    for m in messages:
        if m.get('role') == 'assistant' and m.get('content'):
            content = m['content']
            # Strip tags
            import re
            content = re.sub(r'\[MODE:\w+\]', '', content)
            content = re.sub(r'\[ANALYSIS:\w+\]', '', content)
            content = re.sub(r'\[CHART:[^\]]+\]', '', content)
            content = re.sub(r'```sql.*?```', '', content, flags=re.DOTALL)
            content = content.strip()
            if len(content) > 50:
                ws1.write(row, 0, content[:2000], text_fmt)
                ws1.set_row(row, max(30, min(200, len(content) // 4)))
                row += 2

    # ─── Sheet 2: Data Tables ───────────────────────────────────
    ws2 = wb.add_worksheet('Data')
    ws2.set_column('A:Z', 18)
    data_row = 0
    chart_data = []  # Collect for chart sheet

    for m in messages:
        if m.get('role') != 'assistant' or not m.get('content'):
            continue
        content = m['content']
        # Parse markdown tables
        import re as _re2
        table_pattern = r'\|(.+)\|\n\|[-| :]+\|\n((?:\|.+\|\n?)+)'
        matches = _re2.findall(table_pattern, content)
        for match in matches:
            headers = [h.strip() for h in match[0].split('|') if h.strip()]
            rows_text = match[1].strip().split('\n')
            rows = []
            for rt in rows_text:
                cells = [c.strip() for c in rt.split('|') if c.strip()]
                if cells:
                    rows.append(cells)

            if not headers or not rows:
                continue

            # Write table title
            ws2.write(data_row, 0, f'Table: {headers[0]}...', bold_fmt)
            data_row += 1

            # Headers
            for ci, h in enumerate(headers):
                ws2.write(data_row, ci, h, header_fmt)
            data_row += 1

            # Data rows
            for ri_row in rows:
                for ci, val in enumerate(ri_row):
                    if ci < len(headers):
                        # Try numeric
                        try:
                            clean_val = val.replace(',', '').replace('$', '').replace('%', '').replace('M', '').replace('B', '').replace('T', '').replace('K', '')
                            num = float(clean_val)
                            ws2.write_number(data_row, ci, num, cell_fmt)
                        except (ValueError, TypeError):
                            ws2.write(data_row, ci, val, cell_fmt)
                data_row += 1

            # Save for charts
            if len(headers) >= 2 and len(rows) >= 2:
                chart_data.append({'headers': headers, 'rows': rows, 'start_row': data_row - len(rows), 'sheet': 'Data'})

            data_row += 2  # Gap between tables

    if data_row == 0:
        ws2.write(0, 0, 'No data tables found in conversation', text_fmt)

    # ─── Sheet 3: Charts ────────────────────────────────────────
    ws3 = wb.add_worksheet('Charts')
    ws3.hide_gridlines(2)
    chart_row = 0

    for ci2, cd in enumerate(chart_data[:5]):  # Max 5 charts
        headers = cd['headers']
        rows = cd['rows']

        # Write data for chart on this sheet
        ws3.write(chart_row, 0, headers[0], header_fmt)
        ws3.write(chart_row, 1, headers[1] if len(headers) > 1 else 'Value', header_fmt)
        for ri3, r in enumerate(rows[:15]):
            ws3.write(chart_row + 1 + ri3, 0, r[0] if r else '', cell_fmt)
            try:
                val = r[1].replace(',', '').replace('$', '').replace('M', '').replace('B', '').replace('%', '') if len(r) > 1 else '0'
                ws3.write_number(chart_row + 1 + ri3, 1, float(val), cell_fmt)
            except (ValueError, IndexError):
                ws3.write(chart_row + 1 + ri3, 1, r[1] if len(r) > 1 else '', cell_fmt)

        # Create chart
        chart = wb.add_chart({'type': 'column'})
        chart.add_series({
            'name': headers[1] if len(headers) > 1 else 'Value',
            'categories': ['Charts', chart_row + 1, 0, chart_row + len(rows), 0],
            'values': ['Charts', chart_row + 1, 1, chart_row + len(rows), 1],
            'fill': {'color': '#217346'},
            'border': {'color': '#1a1a1a'},
        })
        chart.set_title({'name': f'{headers[0]} by {headers[1]}' if len(headers) > 1 else headers[0]})
        chart.set_style(10)
        chart.set_size({'width': 600, 'height': 350})
        chart.set_legend({'none': True})

        ws3.insert_chart(chart_row, 3, chart)
        chart_row += max(len(rows) + 2, 20)

    if not chart_data:
        ws3.write(0, 0, 'No chart data found — charts are generated from data tables in chat', text_fmt)

    # ─── Sheet 4: Conversation ──────────────────────────────────
    ws4 = wb.add_worksheet('Conversation')
    ws4.set_column('A:A', 12)
    ws4.set_column('B:B', 100)
    ws4.write(0, 0, 'Role', header_fmt)
    ws4.write(0, 1, 'Message', header_fmt)
    for ri4, m in enumerate(messages):
        role = 'You' if m.get('role') == 'user' else agent_name
        ws4.write(ri4 + 1, 0, role, bold_fmt)
        content = m.get('content', '')[:5000]
        ws4.write(ri4 + 1, 1, content, text_fmt)
        ws4.set_row(ri4 + 1, max(20, min(200, len(content) // 5)))

    wb.close()
    buf.seek(0)
    safe_title = _re.sub(r'[^\w\s\-.]', '', title)[:100] or 'export'
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{safe_title}.xlsx"'})


def _build_html(title: str, body: str, is_raw_html: bool = False) -> str:
    """Build a full HTML document with print-friendly styling."""
    import markdown as md

    if not is_raw_html:
        body = md.markdown(body, extensions=["tables", "fenced_code"])

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #333; font-size: 14px; line-height: 1.6; }}
  h1 {{ font-size: 24px; border-bottom: 2px solid #333; padding-bottom: 8px; }}
  h2 {{ font-size: 18px; margin-top: 24px; }}
  h3 {{ font-size: 15px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 12px; }}
  th {{ background: #333; color: #fff; padding: 6px 10px; text-align: left; font-weight: 700; }}
  td {{ padding: 5px 10px; border: 1px solid #ddd; }}
  tr:nth-child(even) td {{ background: #f9f9f9; }}
  code {{ background: #f0f0f0; padding: 1px 4px; font-size: 12px; }}
  pre {{ background: #333; color: #0f0; padding: 12px; overflow-x: auto; font-size: 12px; }}
  pre code {{ background: none; color: inherit; }}
  .header {{ text-align: center; margin-bottom: 30px; }}
  .footer {{ margin-top: 40px; font-size: 10px; color: #999; text-align: center; border-top: 1px solid #ddd; padding-top: 10px; }}
</style>
</head>
<body>
<div class="header"><h1>{title}</h1></div>
{body}
<div class="footer">Generated by Dash Data Agent</div>
</body>
</html>"""
