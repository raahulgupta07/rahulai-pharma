"""Research PDF renderer for DeepResearch artifacts.

Renders a full research report via reportlab. Returns PDF bytes via BytesIO.

Layout:
    Cover  — title / question / date / project
    TOC
    §1 Scope + entities
    §2 Hypothesis tree (indented)
    §3 Findings per hypothesis + first-10-row data tables
    §4 Cross-check verdict
    §5 Recommendations (numbered + confidence badge)
    §6 Methodology + SQL appendix

Graceful degradation: if reportlab missing, returns a plaintext .txt-style
bytes blob (caller still gets a downloadable artifact).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    _REPORTLAB_OK = True
except Exception as _exc:  # pragma: no cover
    logger.warning("reportlab unavailable: %s — research PDF will fall back to text", _exc)
    _REPORTLAB_OK = False


# ── Public API ──────────────────────────────────────────────────────────
def render(spec: Dict[str, Any]) -> bytes:
    """Render full research artifact to PDF bytes.

    Expected `spec` shape (all optional, fail-soft):
        {
          "title": "...",
          "question": "...",
          "project_slug": "...",
          "project_name": "...",
          "date": "ISO-8601",
          "scope": {"intent": "...", "entities": [...], "time_range": "..."},
          "hypothesis_tree": [{"hypothesis": "...", "sub_questions": [...]}],
          "findings": [{"hypothesis": "...", "finding": "...", "rows": [...], "columns": [...]}],
          "cross_check": {"verdict": "...", "notes": "..."},
          "recommendations": [{"action": "...", "confidence": "high|med|low", "rationale": "..."}],
          "sql_appendix": [{"question": "...", "sql": "..."}],
        }
    """
    if not _REPORTLAB_OK:
        return _render_text_fallback(spec)
    try:
        return _render_pdf(spec)
    except Exception as e:
        logger.exception("research_pdf.render failed: %s — falling back to text", e)
        return _render_text_fallback(spec)


# ── reportlab path ──────────────────────────────────────────────────────
def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontSize=24, leading=30,
                                textColor=colors.HexColor("#1A1614"), spaceAfter=12),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontSize=13,
                                   textColor=colors.HexColor("#6B6557"), spaceAfter=6),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=16, leading=20,
                             textColor=colors.HexColor("#C96342"), spaceBefore=14, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13, leading=16,
                             textColor=colors.HexColor("#1A1614"), spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontSize=10.5, leading=14,
                               textColor=colors.HexColor("#2C2A26"), spaceAfter=6),
        "sub": ParagraphStyle("sub", parent=ss["BodyText"], fontSize=10, leading=13,
                              textColor=colors.HexColor("#3A3A36"), leftIndent=18,
                              spaceAfter=4),
        "subsub": ParagraphStyle("subsub", parent=ss["BodyText"], fontSize=9.5, leading=12,
                                 textColor=colors.HexColor("#4A4A44"), leftIndent=36,
                                 spaceAfter=3),
        "code": ParagraphStyle("code", parent=ss["Code"], fontSize=8.5, leading=11,
                               textColor=colors.HexColor("#1A1614"),
                               backColor=colors.HexColor("#F4F1EA"),
                               leftIndent=8, rightIndent=8, spaceAfter=6),
        "muted": ParagraphStyle("muted", parent=ss["Normal"], fontSize=9,
                                textColor=colors.HexColor("#8B8479"), spaceAfter=4),
    }


_CONF_COLORS = {
    "high":   colors.HexColor("#16A34A"),
    "medium": colors.HexColor("#A06000"),
    "med":    colors.HexColor("#A06000"),
    "low":    colors.HexColor("#C0392B"),
}


def _safe_str(v: Any, cap: int = 400) -> str:
    if v is None:
        return ""
    s = str(v)
    if len(s) > cap:
        s = s[: cap - 1] + "…"
    # escape <>& so reportlab doesn't blow up on stray HTML-ish chars
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _data_table(rows: List[Dict[str, Any]], columns: Optional[List[str]] = None,
                max_rows: int = 10) -> Optional[Table]:
    if not rows:
        return None
    if not columns:
        columns = list(rows[0].keys()) if isinstance(rows[0], dict) else []
    if not columns:
        return None
    cols = columns[:6]  # cap col count to fit page
    header = [_safe_str(c, 30) for c in cols]
    data = [header]
    for r in rows[:max_rows]:
        if isinstance(r, dict):
            data.append([_safe_str(r.get(c), 60) for c in cols])
        elif isinstance(r, (list, tuple)):
            data.append([_safe_str(r[i] if i < len(r) else "", 60)
                         for i in range(len(cols))])
    tbl = Table(data, repeatRows=1, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A1614")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#E8E3D6")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D4CFC0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.HexColor("#FFFFFF"), colors.HexColor("#FAF8F2")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return tbl


def _render_pdf(spec: Dict[str, Any]) -> bytes:
    styles = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=str(spec.get("title") or "Deep Research"),
    )
    story: List = []

    # ── Cover ──────────────────────────────────────────────────────────
    title = _safe_str(spec.get("title") or "Deep Research", 200)
    project_name = _safe_str(spec.get("project_name") or spec.get("project_slug") or "", 120)
    question = _safe_str(spec.get("question") or "", 600)
    date_str = _safe_str(spec.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"), 30)

    story.append(Spacer(1, 1.5 * inch))
    story.append(Paragraph(title, styles["title"]))
    if project_name:
        story.append(Paragraph(f"Project: {project_name}", styles["subtitle"]))
    if question:
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph(f"<b>Research question:</b> {question}", styles["body"]))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(f"Generated: {date_str}", styles["muted"]))
    story.append(PageBreak())

    # ── TOC ────────────────────────────────────────────────────────────
    story.append(Paragraph("Contents", styles["h1"]))
    for label in (
        "1. Scope",
        "2. Hypothesis tree",
        "3. Findings",
        "4. Cross-check verdict",
        "5. Recommendations",
        "6. Methodology + SQL appendix",
    ):
        story.append(Paragraph(label, styles["body"]))
    story.append(PageBreak())

    # ── §1 Scope ───────────────────────────────────────────────────────
    story.append(Paragraph("1. Scope", styles["h1"]))
    scope = spec.get("scope") or {}
    if isinstance(scope, dict):
        intent = _safe_str(scope.get("intent"), 600)
        if intent:
            story.append(Paragraph(f"<b>Intent:</b> {intent}", styles["body"]))
        time_range = _safe_str(scope.get("time_range"), 200)
        if time_range:
            story.append(Paragraph(f"<b>Time range:</b> {time_range}", styles["body"]))
        ents = scope.get("entities") or []
        if isinstance(ents, list) and ents:
            story.append(Paragraph("<b>Entities:</b>", styles["body"]))
            for e in ents[:20]:
                story.append(Paragraph(f"• {_safe_str(e, 160)}", styles["sub"]))
    else:
        story.append(Paragraph(_safe_str(scope, 1200), styles["body"]))
    story.append(Spacer(1, 0.2 * inch))

    # ── §2 Hypothesis tree ─────────────────────────────────────────────
    story.append(Paragraph("2. Hypothesis tree", styles["h1"]))
    tree = spec.get("hypothesis_tree") or []
    if not tree:
        story.append(Paragraph("(no hypotheses generated)", styles["muted"]))
    for i, node in enumerate(tree, 1):
        if not isinstance(node, dict):
            continue
        h = _safe_str(node.get("hypothesis"), 400)
        story.append(Paragraph(f"<b>H{i}.</b> {h}", styles["body"]))
        subs = node.get("sub_questions") or []
        if isinstance(subs, list):
            for sq in subs[:6]:
                story.append(Paragraph(f"– {_safe_str(sq, 300)}", styles["sub"]))
    story.append(Spacer(1, 0.2 * inch))

    # ── §3 Findings ────────────────────────────────────────────────────
    story.append(Paragraph("3. Findings", styles["h1"]))
    findings = spec.get("findings") or []
    if not findings:
        story.append(Paragraph("(no findings produced)", styles["muted"]))
    for i, f in enumerate(findings, 1):
        if not isinstance(f, dict):
            continue
        hyp = _safe_str(f.get("hypothesis"), 300)
        text = _safe_str(f.get("finding") or f.get("text"), 1500)
        story.append(Paragraph(f"<b>Finding {i}</b> — {hyp}", styles["h2"]))
        if text:
            story.append(Paragraph(text, styles["body"]))
        rows = f.get("rows") or []
        cols = f.get("columns") or None
        tbl = _data_table(rows if isinstance(rows, list) else [], cols, max_rows=10)
        if tbl is not None:
            story.append(tbl)
            story.append(Spacer(1, 0.1 * inch))
    story.append(Spacer(1, 0.15 * inch))

    # ── §4 Cross-check verdict ─────────────────────────────────────────
    story.append(Paragraph("4. Cross-check verdict", styles["h1"]))
    cc = spec.get("cross_check") or {}
    if isinstance(cc, dict):
        verdict = _safe_str(cc.get("verdict"), 200)
        notes = _safe_str(cc.get("notes"), 2000)
        if verdict:
            story.append(Paragraph(f"<b>Verdict:</b> {verdict}", styles["body"]))
        if notes:
            story.append(Paragraph(notes, styles["body"]))
    else:
        story.append(Paragraph(_safe_str(cc, 2000), styles["body"]))
    story.append(Spacer(1, 0.2 * inch))

    # ── §5 Recommendations ─────────────────────────────────────────────
    story.append(Paragraph("5. Recommendations", styles["h1"]))
    recs = spec.get("recommendations") or []
    if not recs:
        story.append(Paragraph("(no recommendations produced)", styles["muted"]))
    for i, r in enumerate(recs, 1):
        if not isinstance(r, dict):
            r = {"action": str(r)}
        action = _safe_str(r.get("action"), 400)
        conf = (r.get("confidence") or "").lower()
        rationale = _safe_str(r.get("rationale"), 600)
        color = _CONF_COLORS.get(conf, colors.HexColor("#6B6557"))
        badge_color = f"#{color.hexval()[2:].upper()}" if hasattr(color, "hexval") else "#6B6557"
        badge = (f'<font color="{badge_color}"><b>[{conf.upper()}]</b></font> '
                 if conf else "")
        story.append(Paragraph(f"<b>{i}.</b> {badge}{action}", styles["body"]))
        if rationale:
            story.append(Paragraph(rationale, styles["sub"]))
    story.append(Spacer(1, 0.2 * inch))

    # ── §6 Methodology + SQL appendix ──────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("6. Methodology + SQL appendix", styles["h1"]))
    story.append(Paragraph(
        "This report is produced by a 9-stage DeepResearch pipeline: "
        "scope → hypothesis tree → plan SQL → parallel exec → evidence "
        "ranking → synthesis → cross-check → recommendation → render.",
        styles["body"],
    ))
    sqls = spec.get("sql_appendix") or []
    if not sqls:
        story.append(Paragraph("(no SQL recorded)", styles["muted"]))
    for i, item in enumerate(sqls, 1):
        if not isinstance(item, dict):
            continue
        q = _safe_str(item.get("question"), 300)
        sql_text = _safe_str(item.get("sql"), 2000)
        story.append(Paragraph(f"<b>Q{i}.</b> {q}", styles["h2"]))
        if sql_text:
            story.append(Paragraph(f"<pre>{sql_text}</pre>", styles["code"]))

    try:
        doc.build(story)
    except Exception as e:
        logger.exception("reportlab doc.build failed: %s", e)
        return _render_text_fallback(spec)

    pdf = buf.getvalue()
    buf.close()
    return pdf


# ── Text fallback ───────────────────────────────────────────────────────
def _render_text_fallback(spec: Dict[str, Any]) -> bytes:
    """Plaintext fallback when reportlab unavailable. Returns bytes."""
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append(str(spec.get("title") or "Deep Research"))
    lines.append("=" * 72)
    if spec.get("project_name") or spec.get("project_slug"):
        lines.append(f"Project: {spec.get('project_name') or spec.get('project_slug')}")
    if spec.get("question"):
        lines.append(f"Question: {spec.get('question')}")
    lines.append(f"Date: {spec.get('date') or datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    lines.append("1. SCOPE")
    lines.append("-" * 40)
    scope = spec.get("scope") or {}
    if isinstance(scope, dict):
        for k, v in scope.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append(f"  {scope}")
    lines.append("")

    lines.append("2. HYPOTHESIS TREE")
    lines.append("-" * 40)
    for i, n in enumerate(spec.get("hypothesis_tree") or [], 1):
        if isinstance(n, dict):
            lines.append(f"  H{i}. {n.get('hypothesis', '')}")
            for sq in (n.get("sub_questions") or [])[:6]:
                lines.append(f"     - {sq}")
    lines.append("")

    lines.append("3. FINDINGS")
    lines.append("-" * 40)
    for i, f in enumerate(spec.get("findings") or [], 1):
        if isinstance(f, dict):
            lines.append(f"  Finding {i}: {f.get('hypothesis', '')}")
            lines.append(f"    {f.get('finding') or f.get('text', '')}")
            rows = f.get("rows") or []
            if rows:
                lines.append(f"    [{len(rows)} rows, first 10 shown]")
                for r in rows[:10]:
                    lines.append(f"      {r}")
    lines.append("")

    lines.append("4. CROSS-CHECK")
    lines.append("-" * 40)
    cc = spec.get("cross_check") or {}
    if isinstance(cc, dict):
        for k, v in cc.items():
            lines.append(f"  {k}: {v}")
    lines.append("")

    lines.append("5. RECOMMENDATIONS")
    lines.append("-" * 40)
    for i, r in enumerate(spec.get("recommendations") or [], 1):
        if isinstance(r, dict):
            conf = (r.get("confidence") or "").upper()
            lines.append(f"  {i}. [{conf}] {r.get('action', '')}")
            if r.get("rationale"):
                lines.append(f"      {r['rationale']}")
        else:
            lines.append(f"  {i}. {r}")
    lines.append("")

    lines.append("6. METHODOLOGY + SQL APPENDIX")
    lines.append("-" * 40)
    for i, item in enumerate(spec.get("sql_appendix") or [], 1):
        if isinstance(item, dict):
            lines.append(f"  Q{i}. {item.get('question', '')}")
            sql_text = item.get("sql") or ""
            for sline in str(sql_text).splitlines():
                lines.append(f"      {sline}")
    return ("\n".join(lines) + "\n").encode("utf-8")
