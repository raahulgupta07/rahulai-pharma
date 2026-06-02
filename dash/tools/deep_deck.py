"""Deep Deck — 6-stage research-then-present pipeline.

Pipeline:
    1. INGEST       extract chat history + persona + KG + table catalog
    2. GAPS         DEEP_MODEL identifies 5-8 missing analysis angles
    3. PLAN         LITE_MODEL writes SQL per gap (reuses semantic model)
    4. EXECUTE      run_sql_query each plan (read-only, capped, self-correcting)
    5. SYNTHESIZE   DEEP_MODEL combines chat + enriched data → insight pack
    6. BUILD        passes insight pack to legacy /slides-agent → deck saved

All stages yield SSE events: {stage, status, message, data}.
Streamed via asyncio generator from app/deep_deck_api.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Chart auto-injection from executed SQL (post-codegen) ──────────────
def _inject_chart_slides(spec: Optional[Dict[str, Any]],
                        executed: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Walk executed queries, map any chartable result via chart_mapper, and
    insert them before the closing slide. Idempotent — skips queries whose
    title already appears as an existing chart slide."""
    if not spec or not isinstance(spec, dict):
        return spec
    slides = spec.get("slides") or []
    if not isinstance(slides, list):
        return spec
    try:
        from dash.tools.chart_mapper import build_chart_slide
    except Exception as e:
        logger.warning("chart_mapper import failed: %s", e)
        return spec

    existing_titles = {
        (s.get("title") or "").strip().lower()
        for s in slides if isinstance(s, dict) and s.get("layout") == "chart"
    }

    new_charts: List[Dict[str, Any]] = []
    for q in (executed or []):
        if not isinstance(q, dict):
            continue
        rows = q.get("rows") or q.get("rows_preview") or []
        if not rows or not isinstance(rows, list):
            continue
        title = (q.get("title") or q.get("question") or q.get("intent") or "").strip()
        if not title or title.lower() in existing_titles:
            continue
        eyebrow = "DATA"
        meta = {
            "source_table": q.get("source_table") or q.get("table") or "query",
            "rowcount": q.get("rowcount") or len(rows),
        }
        try:
            slide = build_chart_slide(rows, meta, eyebrow, title[:80])
        except Exception as e:
            logger.warning("build_chart_slide for %r failed: %s", title[:60], e)
            continue
        if slide:
            new_charts.append(slide)
            existing_titles.add(title.lower())

    if not new_charts:
        return spec

    # Insert before closing if present, else append
    insert_at = len(slides)
    for i, s in enumerate(slides):
        if isinstance(s, dict) and s.get("layout") == "closing":
            insert_at = i
            break
    slides[insert_at:insert_at] = new_charts
    spec["slides"] = slides
    spec["total"] = len(slides)
    return spec


def _slide_text_blob(slide: Dict[str, Any]) -> str:
    """Flatten all visible text on a slide into one searchable string."""
    parts: List[str] = []
    for key in ("title", "subtitle", "tagline", "eyebrow", "action_line",
                "body", "note", "source"):
        v = slide.get(key)
        if isinstance(v, str):
            parts.append(v)
    for card in (slide.get("cards") or []):
        if isinstance(card, dict):
            for k in ("title", "body"):
                v = card.get(k)
                if isinstance(v, str):
                    parts.append(v)
    for stat in (slide.get("stats") or []):
        if isinstance(stat, dict):
            for k in ("value", "label"):
                v = stat.get(k)
                if isinstance(v, (str, int, float)):
                    parts.append(str(v))
    for row in (slide.get("rows") or []):
        if isinstance(row, list):
            for cell in row:
                if isinstance(cell, (str, int, float)):
                    parts.append(str(cell))
    cd = slide.get("chart_data") or {}
    if isinstance(cd, dict):
        for series in (cd.get("series") or []):
            if isinstance(series, dict):
                for val in (series.get("values") or []):
                    parts.append(str(val))
    for item in (slide.get("items") or []):
        if isinstance(item, dict):
            for k in ("label", "description"):
                v = item.get(k)
                if isinstance(v, str):
                    parts.append(v)
    return " | ".join(parts)


def _norm_num_token(val: Any) -> Optional[str]:
    """Normalize a numeric value to a string suitable for text matching."""
    if val is None:
        return None
    try:
        n = float(val)
    except Exception:
        return None
    # Match both integer and 1-decimal forms; keep ints crisp.
    if abs(n - int(n)) < 1e-9:
        return str(int(n))
    return f"{n:.1f}"


def _mark_verified_slides(spec: Optional[Dict[str, Any]],
                          executed: List[Dict[str, Any]]) -> int:
    """Walk slides; if a slide's text contains a verified_value, mark it.

    Returns count of slides tagged verified. Fail-soft: any error → 0.
    """
    if not spec or not isinstance(spec, dict):
        return 0
    slides = spec.get("slides") or []
    if not isinstance(slides, list):
        return 0
    # Build verified registry: list of (norm_value_str, source_metric, value)
    verified_metrics: List[Dict[str, Any]] = []
    for e in (executed or []):
        if not isinstance(e, dict) or not e.get("verified"):
            continue
        nv = _norm_num_token(e.get("verified_value"))
        if not nv:
            continue
        verified_metrics.append({
            "needle": nv,
            "source_metric": e.get("source_metric") or "",
            "value": e.get("verified_value"),
        })
    if not verified_metrics:
        return 0
    count = 0
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        blob = _slide_text_blob(slide)
        if not blob:
            continue
        # Search for any verified value substring; require word-boundary-ish match
        for vm in verified_metrics:
            needle = vm["needle"]
            # crude word boundary: ensure not adjacent to a digit
            idx = blob.find(needle)
            ok = False
            while idx != -1:
                left_ok = idx == 0 or not blob[idx - 1].isdigit()
                right_end = idx + len(needle)
                right_ok = right_end >= len(blob) or not blob[right_end].isdigit()
                if left_ok and right_ok:
                    ok = True
                    break
                idx = blob.find(needle, idx + 1)
            if ok:
                slide["verified"] = True
                slide["source_metric"] = vm["source_metric"][:80]
                slide["verified_value"] = vm["value"]
                count += 1
                break
    return count


def _run_qa_loop(pptx_path: str) -> Dict[str, Any]:
    """PPTX → PDF (soffice) → JPG (pdftoppm). Fail-soft."""
    import os
    import shutil
    import subprocess
    if not pptx_path or not os.path.exists(pptx_path):
        return {"pages": 0, "dir": ""}
    out_dir = os.path.join(os.path.dirname(pptx_path), "qa")
    os.makedirs(out_dir, exist_ok=True)

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    pdftoppm = shutil.which("pdftoppm")
    if not soffice or not pdftoppm:
        return {"pages": 0, "dir": out_dir, "skipped": "soffice/pdftoppm missing"}

    pptx_dir = os.path.dirname(pptx_path)
    pptx_name = os.path.splitext(os.path.basename(pptx_path))[0]
    pdf_path = os.path.join(pptx_dir, f"{pptx_name}.pdf")

    try:
        # soffice headless convert → PDF
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", pptx_dir, pptx_path],
            capture_output=True, timeout=120, check=False,
        )
        if not os.path.exists(pdf_path):
            return {"pages": 0, "dir": out_dir, "error": "soffice produced no pdf"}

        # pdftoppm → JPGs (slide-1.jpg, slide-2.jpg, ...)
        subprocess.run(
            [pdftoppm, "-jpeg", "-r", "120", pdf_path, os.path.join(out_dir, "slide")],
            capture_output=True, timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"pages": 0, "dir": out_dir, "error": "timeout"}
    except Exception as e:
        return {"pages": 0, "dir": out_dir, "error": str(e)}

    pages = sum(1 for f in os.listdir(out_dir) if f.lower().endswith((".jpg", ".jpeg")))
    return {"pages": pages, "dir": out_dir}


# ── Helpers ─────────────────────────────────────────────────────────────
def _get_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _llm_call(prompt: str, task: str = "extraction") -> Optional[str]:
    try:
        from dash.settings import training_llm_call
        return training_llm_call(prompt, task=task)
    except Exception as e:
        logger.warning("llm call failed (task=%s): %s", task, e)
        return None


def _parse_json_lenient(text: str) -> Optional[Any]:
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"[\[{].*[\]}]", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _event(stage: str, status: str, message: str = "", data: Any = None) -> Dict[str, Any]:
    return {"stage": stage, "status": status, "message": message, "data": data, "ts": time.time()}


# ── Approval gate registry ──────────────────────────────────────────────
# Module-level dict of asyncio.Event keyed by run_id. The /approve endpoint
# sets the event; orchestrator awaits it (with 5-min timeout fallback so
# abandoned runs don't leak memory).
_APPROVAL_EVENTS: Dict[int, "asyncio.Event"] = {}
_APPROVAL_PAYLOADS: Dict[int, Dict[str, Any]] = {}
_APPROVAL_TIMEOUT_S = 300  # 5 min


def signal_approval(run_id: int, payload: Optional[Dict[str, Any]] = None) -> bool:
    """Persist approval to DB (cross-worker safe). Orchestrator polls DB.

    Returns True if run row exists and was awaiting approval. False if
    no such run OR already past the gate.
    """
    eng = _get_engine()
    if eng is None:
        return False
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT approval_state FROM dash.dash_deep_deck_runs WHERE id = :id"
                ),
                {"id": int(run_id)},
            ).first()
            if not row:
                return False
            state = row[0]
            if state == "approved":
                return True  # idempotent — already approved
            if state != "awaiting":
                return False  # not waiting (done, failed, never reached gate)
            conn.execute(
                text(
                    "UPDATE dash.dash_deep_deck_runs "
                    "SET approval_state = 'approved', "
                    "    approval_payload = CAST(:p AS jsonb) "
                    "WHERE id = :id"
                ),
                {"id": int(run_id), "p": json.dumps(payload or {})},
            )
            conn.commit()
    except Exception as e:
        logger.warning("signal_approval DB write failed: %s", e)
        return False
    # ALSO trigger local Event (same-worker fast path)
    ev = _APPROVAL_EVENTS.get(int(run_id))
    if ev is not None:
        if payload:
            _APPROVAL_PAYLOADS[int(run_id)] = payload
        ev.set()
    return True


def _poll_approval_db(run_id: int) -> Optional[Dict[str, Any]]:
    """Check DB for approval state. Returns payload dict if approved, None if still waiting."""
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT approval_state, approval_payload "
                    "FROM dash.dash_deep_deck_runs WHERE id = :id"
                ),
                {"id": int(run_id)},
            ).mappings().first()
        if row and row.get("approval_state") == "approved":
            p = row.get("approval_payload")
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except Exception:
                    p = {}
            return p or {}
    except Exception:
        pass
    return None


def _mark_awaiting(run_id: int) -> None:
    """Set approval_state='awaiting' before orchestrator yields the gate event."""
    eng = _get_engine()
    if eng is None:
        return
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            conn.execute(
                text(
                    "UPDATE dash.dash_deep_deck_runs "
                    "SET approval_state = 'awaiting' WHERE id = :id"
                ),
                {"id": int(run_id)},
            )
            conn.commit()
    except Exception as e:
        logger.warning("_mark_awaiting failed: %s", e)


# ── Run-level persistence ───────────────────────────────────────────────
def _create_run(project_slug: str, user_id: Optional[int], session_id: Optional[str], config: Dict[str, Any]) -> Optional[int]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            r = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_deep_deck_runs
                        (project_slug, user_id, session_id, status, config)
                    VALUES (:p, :u, :s, 'running', CAST(:c AS jsonb))
                    RETURNING id
                    """
                ),
                {"p": project_slug, "u": user_id, "s": session_id, "c": json.dumps(config)},
            )
            run_id = r.fetchone()[0]
            conn.commit()
            return int(run_id)
    except Exception as e:
        logger.warning("_create_run failed: %s", e)
        return None


def _update_run(run_id: int, **fields) -> None:
    if not run_id or not fields:
        return
    eng = _get_engine()
    if eng is None:
        return
    sets = []
    params: Dict[str, Any] = {"id": run_id}
    for k, v in fields.items():
        sets.append(f"{k} = :{k}")
        params[k] = v
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            conn.execute(
                text(f"UPDATE dash.dash_deep_deck_runs SET {', '.join(sets)} WHERE id = :id"),
                params,
            )
            conn.commit()
    except Exception as e:
        logger.warning("_update_run failed: %s", e)


def _save_gap(run_id: int, rank: int, question: str, rationale: str, priority: float) -> Optional[int]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            r = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_deep_deck_gaps
                        (run_id, rank, question, rationale, priority)
                    VALUES (:r, :rk, :q, :rt, :p)
                    RETURNING id
                    """
                ),
                {"r": run_id, "rk": rank, "q": question, "rt": rationale, "p": priority},
            )
            gid = r.fetchone()[0]
            conn.commit()
            return int(gid)
    except Exception:
        return None


def _save_query(run_id: int, gap_id: Optional[int], rank: int, question: str, sql_text: str) -> Optional[int]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            r = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_deep_deck_queries
                        (run_id, gap_id, rank, question, sql_text)
                    VALUES (:r, :g, :rk, :q, :s)
                    RETURNING id
                    """
                ),
                {"r": run_id, "g": gap_id, "rk": rank, "q": question, "s": sql_text},
            )
            qid = r.fetchone()[0]
            conn.commit()
            return int(qid)
    except Exception:
        return None


def _update_query(qid: int, **fields) -> None:
    if not qid or not fields:
        return
    eng = _get_engine()
    if eng is None:
        return
    sets = []
    params: Dict[str, Any] = {"id": qid}
    for k, v in fields.items():
        if k in ("columns", "rows_preview"):
            sets.append(f"{k} = CAST(:{k} AS jsonb)")
            params[k] = json.dumps(v) if not isinstance(v, str) else v
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            conn.execute(
                text(f"UPDATE dash.dash_deep_deck_queries SET {', '.join(sets)} WHERE id = :id"),
                params,
            )
            conn.commit()
    except Exception:
        pass


# ── STAGE 1: INGEST ─────────────────────────────────────────────────────
def stage_ingest(project_slug: str, session_id: Optional[str], messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Gather context: chat, persona, table catalog (w/ columns), KG hits.

    Authoritative table source = information_schema (PG truth). Tables for a
    project live in the project_slug schema. Columns pulled so planner knows
    real column names instead of hallucinating.
    """
    persona = ""
    table_catalog: List[str] = []
    table_columns: Dict[str, List[str]] = {}
    # Each query gets its own short-lived connection so a failure on one
    # doesn't poison the txn for the next (avoids "current transaction is
    # aborted, commands ignored" cascade).
    def _q(sql: str, params: Dict[str, Any]) -> List[Any]:
        try:
            from sqlalchemy import text
            from sqlalchemy import create_engine as _ce
            from sqlalchemy.pool import NullPool
            from db import db_url
            e = _ce(db_url, poolclass=NullPool)
            with e.connect() as c:
                return c.execute(text(sql), params).fetchall()
        except Exception as ex:
            logger.warning("stage_ingest query failed (%s): %s", sql[:60], ex)
            return []

    pr = _q(
        "SELECT persona_text FROM public.dash_personas "
        "WHERE project_slug = :s ORDER BY created_at DESC LIMIT 1",
        {"s": project_slug},
    )
    if pr:
        persona = (pr[0][0] or "")

    tr = _q(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = :s AND table_type = 'BASE TABLE' "
        "ORDER BY table_name LIMIT 50",
        {"s": project_slug},
    )
    table_catalog = [r[0] for r in tr if r and r[0]]

    if table_catalog:
        cols_rows = _q(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = :s "
            "ORDER BY table_name, ordinal_position",
            {"s": project_slug},
        )
        for tn, cn, dt in cols_rows:
            table_columns.setdefault(tn, []).append(f"{cn}:{dt}")

    # Distill chat to user questions + assistant data refs
    user_questions: List[str] = []
    tables_seen: List[str] = []
    for m in messages[-30:]:
        if m.get("role") == "user":
            uq = (m.get("content") or "").strip()
            if uq:
                user_questions.append(uq[:400])
        else:
            c = m.get("content") or ""
            for t in re.findall(r"FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", c):
                tables_seen.append(t)

    return {
        "user_questions": user_questions,
        "tables_seen": list(dict.fromkeys(tables_seen))[:20],
        "table_catalog": table_catalog,
        "table_columns": table_columns,
        "persona": persona[:1500],
        "message_count": len(messages),
    }


# ── STAGE 2: GAPS ───────────────────────────────────────────────────────
def stage_gaps(ingest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """DEEP_MODEL: identify 5-8 follow-up questions a senior analyst would ask."""
    qs = "\n".join(f"- {q}" for q in (ingest.get("user_questions") or [])[:10])
    table_columns = ingest.get("table_columns") or {}
    schema_lines = []
    for tn in (ingest.get("table_catalog") or [])[:15]:
        cols = table_columns.get(tn, [])[:25]
        schema_lines.append(f"  {tn}({', '.join(cols)})")
    schema_block = "\n".join(schema_lines) if schema_lines else "(no schema available)"
    seen = ", ".join((ingest.get("tables_seen") or [])[:15])
    persona = ingest.get("persona", "")[:800]

    prompt = (
        "You are a senior consulting analyst reviewing a junior's chat with a data agent.\n\n"
        f"PROJECT PERSONA:\n{persona}\n\n"
        f"SCHEMA (real columns):\n{schema_block}\n\n"
        f"TABLES ALREADY QUERIED: {seen}\n\n"
        f"USER ASKED:\n{qs}\n\n"
        "Identify 5-8 FOLLOW-UP questions the junior MISSED that would make the analysis publication-ready. "
        "Focus on: comparisons, segmentation, time-trends, anomalies, root causes, and the 'so what'. "
        "Each question MUST be answerable from the SCHEMA above (no web data, no external sources). "
        "Reference only columns that actually exist.\n\n"
        'Return ONLY JSON: {"gaps": [{"question": "...", "rationale": "1 sentence why it matters", "priority": 0.0-1.0}, ...]}'
    )

    raw = _llm_call(prompt, task="deep_analysis")
    parsed = _parse_json_lenient(raw or "")
    gaps: List[Dict[str, Any]] = []
    if parsed and isinstance(parsed, dict):
        for g in (parsed.get("gaps") or [])[:8]:
            q = (g.get("question") or "").strip()
            if q:
                gaps.append({
                    "question": q[:500],
                    "rationale": (g.get("rationale") or "")[:400],
                    "priority": float(g.get("priority") or 0.5),
                })
    return gaps


# ── STAGE 3: PLAN ───────────────────────────────────────────────────────
def stage_plan(gaps: List[Dict[str, Any]], ingest: Dict[str, Any],
               project_slug: Optional[str] = None) -> List[Dict[str, Any]]:
    """LITE_MODEL: generate SQL per gap question, constrained to available tables.

    Passes ACTUAL columns per table so LLM doesn't hallucinate field names.

    Phase 2 — Verified-metric truth oracle:
        Before LLM-fresh SQL gen, check whether the gap question STRONGLY
        matches a verified pinned metric Q&A. If so, the plan entry is
        marked `verified=True` and carries the verified SQL + value, so the
        downstream slide uses the oracle number instead of an LLM number.
    """
    table_columns = ingest.get("table_columns") or {}
    schema_lines: List[str] = []
    for tn in (ingest.get("table_catalog") or [])[:15]:
        cols = table_columns.get(tn, [])[:30]   # cap cols / table
        schema_lines.append(f"  {tn}({', '.join(cols)})")
    schema_block = "\n".join(schema_lines) if schema_lines else "(no tables — empty schema)"

    # skl_deck_orchestrator = pipeline-side instructions (separate from skl_pptx_builder
    # which is Leader-facing redirect skill).
    try:
        from dash.dashboards.agent import _skill_prefix as _sp
        _skl_pre = _sp("skl_deck_orchestrator")
    except Exception:
        _skl_pre = ""

    # SQL-VALIDATED: central Postgres dialect rules (single source of truth)
    try:
        from dash.tools.llm_sql_helper import _postgres_sql_rules
        _pg_rules = _postgres_sql_rules() + "\n\n"
    except Exception:
        # Fail-soft fallback so prompt is never empty of dialect guidance
        _pg_rules = (
            "## POSTGRES DIALECT — ENFORCED:\n"
            "- TEXT date columns: cast `col::date` before date_trunc / EXTRACT / comparisons.\n"
            "- TEXT numeric columns: cast `col::numeric` before SUM/AVG/MIN/MAX.\n"
            "- Use COALESCE(col, 0) for NULL-safe numeric ops.\n\n"
        )

    # Phase 2 hook: verified-metric truth oracle (per-gap, $0, ~30ms)
    try:
        from dash.learning.verified_reward import try_metric_shortcut
    except Exception as _exc:  # noqa: BLE001
        try_metric_shortcut = None  # type: ignore
        logger.debug("try_metric_shortcut unavailable: %s", _exc)

    plan: List[Dict[str, Any]] = []
    for g in gaps[:6]:
        # ── Phase 2: verified-metric shortcut ──────────────────────────
        # Build slide-question from gap fields; high-confidence pin → use
        # the verified SQL verbatim, mark slide as verified, skip LLM SQL.
        slide_question = f"{g.get('question', '')} {g.get('rationale', '')}".strip()
        if project_slug and try_metric_shortcut and slide_question:
            try:
                shortcut = try_metric_shortcut(project_slug, slide_question)
            except Exception as _exc:  # noqa: BLE001
                logger.debug("try_metric_shortcut raised: %s", _exc)
                shortcut = None
            if (shortcut and shortcut.get("matched")
                    and shortcut.get("value") is not None
                    and shortcut.get("sql")):
                source_q = (shortcut.get("source_q") or "")[:80]
                verified_value = shortcut.get("value")
                logger.info(
                    "stage_plan verified-metric hit project=%s gap=%r → metric=%r value=%s",
                    project_slug, slide_question[:80], source_q, verified_value,
                )
                plan.append({
                    "question": g["question"],
                    "sql": shortcut["sql"][:4000],
                    "expected_shape": "1 row × 1 col (verified metric)",
                    "gap_priority": g.get("priority", 0.5),
                    "verified": True,
                    "source_metric": source_q,
                    "verified_value": verified_value,
                })
                continue

        # SQL-VALIDATED: prepend central PG dialect rules from helper
        prompt = _skl_pre + _pg_rules + (
            f"Write ONE read-only SQL query (Postgres dialect) to answer: \"{g['question']}\"\n\n"
            f"SCHEMA (table(col:type, ...)):\n{schema_block}\n\n"
            "Additional rules (deck-specific):\n"
            "- SELECT only, no DDL/DML.\n"
            "- Use ONLY columns shown in SCHEMA above. NEVER invent column names.\n"
            "- Reference tables by bare name (search_path is set to the project schema).\n"
            "- Add LIMIT 1000 at the end (we cap anyway).\n"
            "- If grouping, use clear column aliases.\n"
            "- If date arithmetic, use DATE_TRUNC.\n"
            "- If the question can't be answered from this schema, return {\"sql\": \"\", \"expected_shape\": \"unanswerable\"}.\n\n"
            'Return ONLY JSON: {"sql": "SELECT ...", "expected_shape": "e.g. 1 row × 3 cols"}'
        )
        raw = _llm_call(prompt, task="extraction")
        parsed = _parse_json_lenient(raw or "")
        sql = ""
        shape = ""
        if parsed and isinstance(parsed, dict):
            sql = (parsed.get("sql") or "").strip()
            shape = (parsed.get("expected_shape") or "").strip()
        # safety: hard-reject if not SELECT/WITH
        if sql and re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE):
            plan.append({
                "question": g["question"],
                "sql": sql[:4000],
                "expected_shape": shape[:200],
                "gap_priority": g.get("priority", 0.5),
            })
    return plan


# ── STAGE 4: EXECUTE ────────────────────────────────────────────────────
# Errors that indicate schema-mismatch (column/table doesn't exist).  When the
# raw SQL fast-path fails with one of these, we fall back to the Analyst
# agent which carries 13-layer context + introspect_schema and can self-correct.
_SCHEMA_GAP_MARKERS = (
    "undefinedtable",
    "undefinedcolumn",
    "does not exist",
    "relation",  # "relation X does not exist"
    "column",    # "column X does not exist"
)


def _looks_like_schema_gap(err_msg: str) -> bool:
    if not err_msg:
        return False
    e = err_msg.lower()
    # Require both a strong marker AND "does not exist" to avoid catching
    # generic syntax errors that merely mention 'column' / 'relation'.
    if "does not exist" in e:
        return True
    if "undefinedtable" in e or "undefinedcolumn" in e:
        return True
    return False


def _execute_via_analyst(
    project_slug: str,
    sql_text: str,
    gap_question: str = "",
    row_cap: int = 1000,
) -> Dict[str, Any]:
    """Delegate SQL execution to the project's Analyst agent.

    Analyst has 13-layer context + introspect_schema + memories of correct
    column names. We ask it to execute the SQL, fix any wrong identifiers
    using introspect_schema, then return tabular data as JSON.

    Returns same dict shape as stage_execute_one fast path.
    """
    sched = time.time()
    try:
        from dash.team import create_project_team
    except Exception as e:
        return {"ok": False, "error": f"analyst_import_failed: {e}"}

    try:
        team = create_project_team(project_slug=project_slug)
    except Exception as e:
        return {"ok": False, "error": f"team_build_failed: {str(e)[:300]}"}

    analyst = None
    try:
        for m in getattr(team, "members", []) or []:
            nm = (getattr(m, "name", "") or "").lower()
            if "analyst" in nm:
                analyst = m
                break
    except Exception:
        analyst = None
    if analyst is None:
        return {"ok": False, "error": "analyst_not_found_in_team"}

    prompt = (
        "Execute the following SQL against the project schema and return ONLY the "
        "resulting rows as a JSON array of objects (no prose, no markdown). "
        "If a column or table name is wrong, call `introspect_schema` to find the "
        "correct name, fix the SQL, then retry. Cap output at "
        f"{int(row_cap)} rows.\n\n"
    )
    if gap_question:
        prompt += f"Question this query is meant to answer: {gap_question[:300]}\n\n"
    prompt += f"SQL:\n```sql\n{sql_text}\n```\n\nReturn ONLY the JSON array."

    try:
        run = analyst.run(prompt)
        # Agno agents: prefer .content, fallback to str()
        raw = getattr(run, "content", None) or str(run)
    except Exception as e:
        # Some agents may need arun; try once before giving up.
        try:
            run = asyncio.run(analyst.arun(prompt))  # type: ignore[attr-defined]
            raw = getattr(run, "content", None) or str(run)
        except Exception as e2:
            return {"ok": False, "error": f"analyst_run_failed: {str(e2)[:300]}"}

    parsed = _parse_json_lenient(raw or "")
    rows: List[Dict[str, Any]] = []
    if isinstance(parsed, list):
        for r in parsed[:row_cap]:
            if isinstance(r, dict):
                rows.append({k: _to_json_safe(v) for k, v in r.items()})
    elif isinstance(parsed, dict):
        # Sometimes models wrap rows in {"rows": [...]} or {"data": [...]}
        for key in ("rows", "data", "result", "results"):
            v = parsed.get(key)
            if isinstance(v, list):
                for r in v[:row_cap]:
                    if isinstance(r, dict):
                        rows.append({k: _to_json_safe(vv) for k, vv in r.items()})
                break

    if not rows:
        return {
            "ok": False,
            "error": "analyst_no_rows_parsed",
            "raw_excerpt": (raw or "")[:300],
        }

    cols = list(rows[0].keys())
    duration_ms = int((time.time() - sched) * 1000)
    return {
        "ok": True,
        "columns": cols,
        "row_count": len(rows),
        "rows_preview": rows[:20],
        "duration_ms": duration_ms,
        "via": "analyst",
    }


def stage_execute_one(
    project_slug: str,
    sql_text: str,
    timeout_s: int = 30,
    row_cap: int = 1000,
    gap_question: str = "",
) -> Dict[str, Any]:
    """Run one SQL on project's read-only engine. Return rows or error.

    Hybrid path:
      1. Try raw SQL via SQLAlchemy (fast, $0).
      2. On UndefinedTable / UndefinedColumn / "does not exist" errors only,
         fall back to the Analyst agent (13-layer context + self-correction).
      3. Other errors (syntax, timeout, perms) are returned as-is — no fallback.
    """
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "no_engine"}

    # ── Fast path: raw SQL ──────────────────────────────────────────────
    try:
        from sqlalchemy import text
        sched = time.time()
        # Use begin() so SET LOCAL applies inside the transaction
        with eng.begin() as conn:
            try:
                conn.execute(text(f"SET LOCAL statement_timeout = '{int(timeout_s)}s'"))
            except Exception:
                pass
            # Identifier quoting on slug to handle hyphens/underscores
            try:
                conn.execute(text(f'SET LOCAL search_path TO "{project_slug}", public, dash'))
            except Exception:
                pass
            res = conn.execute(text(sql_text))
            cols = list(res.keys())
            rows = res.fetchmany(row_cap)
        duration_ms = int((time.time() - sched) * 1000)
        out_rows: List[Dict[str, Any]] = []
        for row in rows:
            try:
                out_rows.append({c: _to_json_safe(v) for c, v in zip(cols, row)})
            except Exception:
                continue
        logger.info(
            "stage_execute_one fast-path ok project=%s rows=%d duration_ms=%d",
            project_slug, len(out_rows), duration_ms,
        )
        return {
            "ok": True,
            "columns": cols,
            "row_count": len(out_rows),
            "rows_preview": out_rows[:20],
            "duration_ms": duration_ms,
            "via": "raw_sql",
        }
    except Exception as e:
        err_msg = str(e)
        if not _looks_like_schema_gap(err_msg):
            logger.info(
                "stage_execute_one fast-path failed (no analyst fallback) project=%s err=%s",
                project_slug, err_msg[:200],
            )
            return {"ok": False, "error": err_msg[:400]}
        logger.warning(
            "stage_execute_one fast-path schema-gap project=%s err=%s — delegating to Analyst",
            project_slug, err_msg[:200],
        )

    # ── Fallback: Analyst agent with 13-layer context ──────────────────
    analyst_res = _execute_via_analyst(
        project_slug=project_slug,
        sql_text=sql_text,
        gap_question=gap_question,
        row_cap=row_cap,
    )
    if analyst_res.get("ok"):
        logger.info(
            "stage_execute_one analyst-fallback ok project=%s rows=%d duration_ms=%s",
            project_slug, analyst_res.get("row_count", 0),
            analyst_res.get("duration_ms"),
        )
    else:
        logger.warning(
            "stage_execute_one analyst-fallback failed project=%s err=%s",
            project_slug, str(analyst_res.get("error", ""))[:200],
        )
    return analyst_res


def _to_json_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    try:
        return float(v)
    except Exception:
        pass
    try:
        return str(v)
    except Exception:
        return None


# ── STAGE 5: SYNTHESIZE ─────────────────────────────────────────────────
def stage_synthesize(ingest: Dict[str, Any], gaps: List[Dict[str, Any]], executed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """DEEP_MODEL: combine chat + enriched data → insight pack for slide build."""
    persona = ingest.get("persona", "")[:800]
    chat_qs = "\n".join(f"- {q}" for q in (ingest.get("user_questions") or [])[:8])

    enriched: List[str] = []
    for i, e in enumerate(executed, 1):
        if not e.get("ok"):
            continue
        rows_snippet = json.dumps(e.get("rows_preview", [])[:10], default=str)[:1500]
        verified_hint = ""
        if e.get("verified") and e.get("verified_value") is not None:
            verified_hint = (
                f"  ⚑ VERIFIED METRIC — USE THIS NUMBER VERBATIM: {e['verified_value']} "
                f"(from pinned metric '{(e.get('source_metric') or '')[:80]}'). "
                f"Do NOT recompute or round.\n"
            )
        enriched.append(
            f"FINDING {i} (q: {e['question'][:200]}):\n"
            f"rows={e.get('row_count')} · cols={e.get('columns')}\n"
            f"data: {rows_snippet}\n"
            f"{verified_hint}"
        )

    prompt = (
        "You are a McKinsey partner synthesizing a presentation.\n\n"
        f"PERSONA:\n{persona}\n\n"
        f"USER ORIGINALLY ASKED:\n{chat_qs}\n\n"
        f"WE THEN RAN {len(enriched)} ADDITIONAL ANALYSES:\n\n"
        + "\n".join(enriched)
        + "\n\nProduce a synthesis pack the slide-builder can consume:\n"
        '{\n'
        '  "narrative": "one-sentence story arc",\n'
        '  "key_insight": "the ONE surprising finding",\n'
        '  "supporting_evidence": [{"claim": "...", "value": "...", "source_query_id": 1}, ...],\n'
        '  "recommendations": [{"action": "...", "owner": "Data Ops|Finance|...", "effort": "1d|1wk|1mo", "risk": "low|med|high"}, ...],\n'
        '  "risks": ["...", "..."],\n'
        '  "audience_action": "ONE specific action this audience should take"\n'
        "}\n\n"
        "HARD RULES — auto-fail if violated:\n"
        "- Every numeric claim MUST cite source_query_id (1-indexed by FINDING above).\n"
        "- Numbers MUST appear in the FINDING data shown above — do NOT invent.\n"
        "- NEVER name external sources you weren't given:\n"
        "  Forbidden: 'McKinsey', 'Gartner', 'Forrester', 'BCG', 'Bain', 'industry benchmark',\n"
        "  'McKinsey Operational Excellence Study', 'McKinsey Benchmarking', any made-up paper title.\n"
        "  Allowed: '(Source: [Q3])' pointing to an actual FINDING above, or 'Internal data' / 'Project data'.\n"
        "- NEVER use placeholder tokens: [X], [Y], [ERP System Name], $X, $XM, $[Y]M, [X]%.\n"
        "  If a number is unknown, DROP the claim. Don't write [X] expecting a human fill.\n"
        "- Correlation r in [-0.1, 0.1] means NO relationship. Never call this a 'driver' or 'cause'.\n"
        "  If r is near zero, write 'no measurable correlation' and move on — don't manufacture insight.\n"
        "- Numbers must agree across the pack. If FINDING 1 says 1,614 unclassified, every claim about\n"
        "  unclassified count uses 1,614 — no other number.\n"
        "- 3-5 recommendations, each with owner + effort + risk.\n"
        "- Return ONLY JSON."
    )

    raw = _llm_call(prompt, task="deep_analysis")
    parsed = _parse_json_lenient(raw or "")
    if not parsed or not isinstance(parsed, dict):
        return {
            "narrative": "Analysis synthesis pending",
            "key_insight": "",
            "supporting_evidence": [],
            "recommendations": [],
            "risks": [],
            "audience_action": "",
        }
    return parsed


# ── STAGE 6: BUILD ──────────────────────────────────────────────────────
def stage_build(project_slug: str, agent_name: str, messages: List[Dict[str, Any]],
                insight_pack: Dict[str, Any], executed: List[Dict[str, Any]],
                auth_token: Optional[str] = None,
                audience: Optional[str] = None) -> Dict[str, Any]:
    """Call existing /slides-agent internals w/ enriched insight pack + executed data.

    Strategy: prepend a synthetic 'assistant' message to messages[] that embeds
    the insight_pack + enriched data as JSON. Slide Agent v2's prompt already
    extracts insights from chat — feeding it richer context yields richer slides.
    """
    # Build a synthetic context message
    enriched_summary = []
    for i, e in enumerate(executed, 1):
        if not e.get("ok"):
            continue
        enriched_summary.append(
            f"[Q{i}] {e['question']}\n"
            f"   {e.get('row_count', 0)} rows, cols={e.get('columns')}\n"
            f"   sample={json.dumps(e.get('rows_preview', [])[:5], default=str)[:600]}"
        )

    # Audience tuning
    audience_block = ""
    pack_for_prompt = insight_pack
    try:
        from dash.tools.slide_audience import tune_insight_pack_for_audience, build_variant_message
        pack_for_prompt = tune_insight_pack_for_audience(insight_pack, audience)
        audience_block = build_variant_message(audience, pack_for_prompt) + "\n"
    except Exception as e:
        logger.warning("audience tuning failed: %s", e)

    synth_block = (
        audience_block
        + "DEEP RESEARCH PACK (use this as primary input for slides):\n"
        "HARD RULES — auto-fail if violated:\n"
        "- Numbers MUST come from the ENRICHED QUERY RESULTS below. Never invent.\n"
        "- Citations: only '(Source: [Q1])' or '(Source: Internal data)'. NEVER write\n"
        "  'McKinsey', 'Gartner', 'Forrester', 'BCG', 'Bain', 'industry study',\n"
        "  'benchmark report', or any external paper/firm name not in this pack.\n"
        "- NEVER use placeholder tokens [X] / [Y] / $X / $XM / $[Y]M / [X]% / [ERP System Name].\n"
        "  If a number is unknown, drop the claim entirely.\n"
        "- Numbers must agree across slides. Pick ONE canonical value per metric.\n"
        "- Correlation r between -0.1 and 0.1 means NO relationship. Never call that a 'driver'.\n\n"
        f"Narrative: {insight_pack.get('narrative', '')}\n"
        f"Key insight: {insight_pack.get('key_insight', '')}\n"
        f"Audience action: {insight_pack.get('audience_action', '')}\n\n"
        "Supporting evidence:\n"
        + "\n".join(f"  - {ev.get('claim')} = {ev.get('value')} (src=Q{ev.get('source_query_id')})"
                    for ev in (insight_pack.get('supporting_evidence') or [])[:10])
        + "\n\nRecommendations:\n"
        + "\n".join(f"  - {r.get('action')} (owner={r.get('owner')}, effort={r.get('effort')}, risk={r.get('risk')})"
                    for r in (insight_pack.get('recommendations') or [])[:6])
        + "\n\nRisks:\n"
        + "\n".join(f"  - {x}" for x in (insight_pack.get('risks') or [])[:4])
        + "\n\nEnriched query results:\n"
        + "\n\n".join(enriched_summary)
    )

    augmented_messages = list(messages) + [
        {"role": "assistant", "content": synth_block}
    ]

    # Legacy /slides-agent endpoint removed. Build minimal slide stubs from
    # insight pack so the dash_presentations row has narrative for card preview.
    # Stage 8 (codegen_pptxgenjs) builds the real spec from executed data, and
    # Stage 9 (render_pptx_via_js) produces the .pptx via native python-pptx.
    data = {
        "slides": [
            {
                "title": (insight_pack.get("key_insight") or "Deep Analysis")[:120],
                "bullets": [
                    str(e.get("claim") or e)[:200]
                    for e in (insight_pack.get("supporting_evidence") or [])[:6]
                    if e
                ],
                "action_line": insight_pack.get("audience_action") or "",
            },
        ],
        "thinking": {
            "narrative": insight_pack.get("narrative") or insight_pack.get("key_insight") or "",
            "audience": audience,
        },
        "theme": "city_executive",
    }

    slides_out = data.get("slides") or []
    # F2: auto-icon per bullet (no LLM, $0)
    try:
        from dash.tools.slide_icons import enrich_slide_with_icons
        for s in slides_out:
            enrich_slide_with_icons(s)
    except Exception as e:
        logger.warning("icon enrich failed: %s", e)
    # F2b: Pexels hero image (skip silently if PEXELS_API_KEY missing)
    try:
        from dash.tools.slide_images import enrich_slide_with_hero
        for s in slides_out:
            enrich_slide_with_hero(s)
    except Exception as e:
        logger.warning("hero enrich failed: %s", e)

    return {
        "ok": True,
        "slides": slides_out,
        "thinking": data.get("thinking") or {},
        "theme": data.get("theme"),
    }


# ── PHASE 4: stages 8 (Vision-QA judge) + 9 (Iterate / finalize) ────────
#
# Different-model rule (TACL): generator uses CHAT_MODEL (codegen_pptxgenjs),
# judge uses DEEP_MODEL (deck_vision_judge.judge_slide pins task="deep_analysis"
# which routes to DEEP_MODEL via TRAINING_CONFIGS).
#
# PNG vs text fallback: stage_vision_judge prefers real rendered slide PNGs
# from the existing _run_qa_loop output (qa/*.jpg, produced via pptx → pdf →
# jpg). If those are unavailable (renderer offline, qa.sh missing,
# LibreOffice/pdftoppm not installed), judge_slide degrades to text-only
# LLM judgment of the slide JSON — still on DEEP_MODEL, just blind.
#
# Kill switch: env DEEP_DECK_V2_DISABLED=1 → orchestrator skips stages 8+9
# (falls back to the existing 7-stage flow).

_V2_VISUAL_LAYOUTS = {
    "chart", "kpi", "kpi_strip", "kpi_row", "table",
    "bar", "line", "pie", "scatter", "grouped_bar", "histogram",
    "heatmap", "metric", "dashboard", "comparison",
}


def _slide_has_visual_content(slide: Dict[str, Any]) -> bool:
    """Vision QA only on slides w/ charts, tables, KPI cards. Skip pure
    narrative / text-only slides (text judge contributes nothing)."""
    if not isinstance(slide, dict):
        return False
    layout = str(slide.get("layout") or "").lower()
    if layout in _V2_VISUAL_LAYOUTS:
        return True
    # Heuristic: presence of any data-ish keys
    for k in ("chart", "chart_spec", "echarts", "rows", "data", "kpis",
              "metrics", "table", "series"):
        if slide.get(k):
            return True
    # Cards w/ values often indicate KPI strip
    cards = slide.get("cards") or []
    if isinstance(cards, list) and any(
        isinstance(c, dict) and (c.get("value") or c.get("number"))
        for c in cards
    ):
        return True
    return False


def _v2_cost_guard_blocked(project_slug: str) -> bool:
    """Skip vision QA + iterate when the daily cost cap is reached.
    Reuses dash/learning/cost_guard.py when available; otherwise inline no-op."""
    try:
        from dash.learning.cost_guard import get_status
        st = get_status(project_slug)
        return bool(getattr(st, "over_budget", False))
    except Exception as e:
        logger.debug("v2 cost guard skipped (no cost_guard available): %s", e)
        return False


def _v2_find_slide_pngs(qa_dir: Optional[str], n_slides: int) -> List[Optional[str]]:
    """Map slide index → rendered preview PNG/JPG path produced by _run_qa_loop.

    qa.sh runs pptx → pdf → jpg, dropping page-N.jpg into qa_dir. We return a
    list aligned to spec['slides']; entries can be None when a corresponding
    image is missing (text-fallback then kicks in in judge_slide)."""
    out: List[Optional[str]] = [None] * n_slides
    if not qa_dir:
        return out
    try:
        import os
        if not os.path.isdir(qa_dir):
            return out
        files = sorted(
            f for f in os.listdir(qa_dir)
            if f.lower().endswith((".jpg", ".jpeg", ".png"))
        )
        for i in range(min(n_slides, len(files))):
            out[i] = os.path.join(qa_dir, files[i])
    except Exception as e:
        logger.warning("v2: scan qa dir failed: %s", e)
    return out


def stage_vision_judge(
    pptxgenjs_spec: Optional[Dict[str, Any]],
    deck_context: Dict[str, Any],
    qa_dir: Optional[str] = None,
    max_slides: int = 12,
) -> List[Dict[str, Any]]:
    """Stage 8 — different-model vision judge.

    Iterates slides in the spec (cap 12), calls deck_vision_judge.judge_slide
    on each visual slide. Skips text-only narrative slides. Returns per-slide
    list `[{slide_idx, score, issues, suggestions, judged: bool, reason?}]`.

    Fail-soft per slide — one judge crash never blocks the rest.
    """
    results: List[Dict[str, Any]] = []
    if not pptxgenjs_spec or not isinstance(pptxgenjs_spec, dict):
        return results
    slides = pptxgenjs_spec.get("slides") or []
    if not isinstance(slides, list):
        return results

    try:
        from dash.tools.deck_vision_judge import judge_slide
    except Exception as e:
        logger.warning("stage_vision_judge: judge_slide import failed: %s", e)
        return results

    pngs = _v2_find_slide_pngs(qa_dir, len(slides))
    deck_ctx_compact = {
        "audience": deck_context.get("audience"),
        "brand": deck_context.get("brand"),
        "total": len(slides),
        "theme": deck_context.get("theme"),
    }

    for i, slide in enumerate(slides[:max_slides]):
        if not isinstance(slide, dict):
            continue
        if not _slide_has_visual_content(slide):
            results.append({
                "slide_idx": i,
                "score": 100,
                "issues": [],
                "suggestions": [],
                "judged": False,
                "reason": "text-only slide (skipped)",
            })
            continue
        try:
            png_path = pngs[i] if i < len(pngs) else None
            res = judge_slide(png_path, slide, deck_ctx_compact)
            res = res or {"score": 100, "issues": [], "suggestions": []}
            results.append({
                "slide_idx": i,
                "score": int(res.get("score", 100)),
                "issues": list(res.get("issues") or [])[:8],
                "suggestions": list(res.get("suggestions") or [])[:8],
                "judged": True,
                "image": "png" if png_path else "text-fallback",
            })
        except Exception as e:
            logger.warning("stage_vision_judge: slide %d crashed: %s", i, e)
            results.append({
                "slide_idx": i,
                "score": 100,
                "issues": [],
                "suggestions": [],
                "judged": False,
                "reason": f"judge_error: {str(e)[:120]}",
            })
    return results


def _regenerate_slide(
    slide: Dict[str, Any],
    issues: List[str],
    suggestions: List[str],
) -> Optional[Dict[str, Any]]:
    """Regenerate a single failing slide given the judge's feedback.

    Targets the SAME slide schema as codegen_pptxgenjs — we ask the LLM
    (CHAT_MODEL for codegen tier) to rewrite ONE slide JSON, preserving
    `title`, `layout`, and structural fields, fixing the listed issues.
    Returns the updated slide dict, or None on failure (caller keeps original).
    """
    try:
        prompt = (
            "You are revising ONE slide of a presentation. The visual judge "
            "flagged problems. Return ONLY a JSON object representing the "
            "revised slide using the SAME shape as the input (preserve "
            "`layout`, top-level keys). FIX THESE issues:\n"
            f"ISSUES: {json.dumps(issues[:8], default=str)}\n"
            f"SUGGESTIONS: {json.dumps(suggestions[:8], default=str)}\n\n"
            "Current slide JSON:\n"
            f"{json.dumps(slide, default=str)[:3000]}\n\n"
            "Return ONLY the revised slide JSON, no prose, no fences."
        )
        # Use the same gen-tier task as codegen_pptxgenjs — CHAT_MODEL
        from dash.settings import training_llm_call
        raw = training_llm_call(prompt, task="dashboard_gen")
        if not raw:
            return None
        # Reuse _first_object from deck_vision_judge for balanced-brace parsing
        from dash.tools.deck_vision_judge import _first_object
        blob = _first_object(raw)
        if not blob:
            return None
        try:
            new_slide = json.loads(blob)
        except Exception:
            return None
        if not isinstance(new_slide, dict):
            return None
        # Preserve verified-metric tags so we don't drop the ✓ badge
        for k in ("verified", "source_metric", "verified_value", "layout"):
            if k in slide and k not in new_slide:
                new_slide[k] = slide[k]
        return new_slide
    except Exception as e:
        logger.warning("_regenerate_slide failed: %s", e)
        return None


def stage_iterate(
    slides: List[Dict[str, Any]],
    judge_results: List[Dict[str, Any]],
    max_iters: int = 2,
    score_threshold: int = 80,
) -> tuple:
    """Stage 9 — iterate failing slides.

    Finds slides with score < `score_threshold`, regenerates each ONCE via
    CHAT_MODEL with judge issues + suggestions injected as "FIX THESE: ...".
    `max_iters` caps the *total* number of regenerations across the deck —
    even if 5 slides failed, only 2 get redone (worst scores first).

    Returns (updated_slides_list, iter_count_used).
    """
    if not isinstance(slides, list):
        return slides, 0
    if not isinstance(judge_results, list) or not judge_results:
        return list(slides), 0

    # Pick failing slides, sorted by worst-score first
    failing = [
        r for r in judge_results
        if isinstance(r, dict)
        and r.get("judged")
        and isinstance(r.get("score"), int)
        and r["score"] < score_threshold
    ]
    failing.sort(key=lambda r: r.get("score", 100))

    out = list(slides)
    iters = 0
    for r in failing:
        if iters >= max_iters:
            break
        idx = r.get("slide_idx")
        if not isinstance(idx, int) or idx < 0 or idx >= len(out):
            continue
        orig = out[idx]
        if not isinstance(orig, dict):
            continue
        new_slide = _regenerate_slide(
            orig,
            list(r.get("issues") or []),
            list(r.get("suggestions") or []),
        )
        iters += 1
        if new_slide is not None:
            out[idx] = new_slide
            logger.info(
                "stage_iterate: regenerated slide %d (score was %d)",
                idx, r.get("score", -1),
            )
        else:
            logger.info(
                "stage_iterate: slide %d regen failed, keeping original",
                idx,
            )
    return out, iters


def _update_pres_judge_scores(
    pres_id: int,
    judge_results: List[Dict[str, Any]],
    iter_count: int,
) -> None:
    """Persist per-slide judge scores + iter_count into thinking JSONB.
    Reuses the same `public.dash_presentations` raw-engine pattern as
    _update_pres_spec()."""
    try:
        from sqlalchemy import create_engine as _ce, text
        from sqlalchemy.pool import NullPool
        from db import db_url
        eng = _ce(db_url, poolclass=NullPool)
    except Exception as e:
        logger.warning("_update_pres_judge_scores engine init failed: %s", e)
        return
    try:
        meta = {
            "judge_scores": judge_results,
            "judge_iterations": int(iter_count),
        }
        with eng.connect() as conn:
            conn.execute(
                text(
                    "UPDATE public.dash_presentations "
                    "SET thinking = COALESCE(thinking, CAST('{}' AS jsonb)) "
                    "               || CAST(:m AS jsonb) "
                    "WHERE id = :id"
                ),
                {"id": int(pres_id), "m": json.dumps(meta, default=str)},
            )
            conn.commit()
    except Exception as e:
        logger.warning("_update_pres_judge_scores failed: %s", e)


# ── ORCHESTRATOR ────────────────────────────────────────────────────────
async def orchestrate_deep_deck(
    project_slug: str,
    user_id: Optional[int],
    session_id: Optional[str],
    agent_name: str,
    messages: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    auth_token: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Run all 6 stages as async generator yielding SSE events."""
    config = config or {}
    run_id = _create_run(project_slug, user_id, session_id, config)

    def emit(stage, status, message="", data=None):
        return _event(stage, status, message, data)

    try:
        # Stage 1
        yield emit("ingest", "running", "Reading chat history + persona + table catalog")
        ingest = await asyncio.to_thread(stage_ingest, project_slug, session_id, messages)
        _update_run(run_id, current_stage="ingest", stage_progress=1)
        yield emit("ingest", "done",
                   f"{len(ingest['user_questions'])} questions · {len(ingest['table_catalog'])} tables",
                   {"questions": len(ingest['user_questions']), "tables": len(ingest['table_catalog'])})

        # Stage 2
        yield emit("gaps", "running", "Identifying missing analysis angles")
        gaps = await asyncio.to_thread(stage_gaps, ingest)
        gap_ids: List[int] = []
        for i, g in enumerate(gaps, 1):
            gid = await asyncio.to_thread(_save_gap, run_id, i, g["question"], g["rationale"], g["priority"])
            gap_ids.append(gid or 0)
        _update_run(run_id, current_stage="gaps", stage_progress=2)
        yield emit("gaps", "done", f"{len(gaps)} follow-up questions identified", {"gaps": gaps})

        if not gaps:
            yield emit("gaps", "error", "No gaps identified — falling back to chat-only build")

        # Stage 3
        yield emit("plan", "running", "Writing SQL for each gap")
        plan = await asyncio.to_thread(stage_plan, gaps, ingest, project_slug)
        for i, p in enumerate(plan, 1):
            gid = gap_ids[i - 1] if i - 1 < len(gap_ids) else None
            qid = await asyncio.to_thread(_save_query, run_id, gid, i, p["question"], p["sql"])
            p["_qid"] = qid
        _update_run(run_id, current_stage="plan", stage_progress=3)
        yield emit("plan", "done", f"{len(plan)} queries written", {"plan": plan})

        # ── Outline approval gate ──────────────────────────────────────
        # If wait_for_approval=true, pause here and let user APPROVE / EDIT
        # the outline (gaps + planned queries) before spending $0.13 on
        # stages 4-7. Uses asyncio.Event keyed by run_id; 5-min timeout
        # fallback prevents abandoned runs from leaking memory.
        if (config or {}).get("wait_for_approval") and run_id:
            ev = asyncio.Event()
            _APPROVAL_EVENTS[run_id] = ev
            # Persist 'awaiting' state in DB so any worker's /approve endpoint
            # can see + update this run (multi-worker safe).
            await asyncio.to_thread(_mark_awaiting, run_id)
            try:
                yield emit(
                    "approval", "waiting",
                    "Awaiting outline approval (5 min)",
                    {"plan": plan, "gaps": gaps, "run_id": run_id},
                )
                # Wait via local Event (fast path same-worker) OR DB poll (cross-worker).
                # Race: whichever fires first wins.
                payload: Dict[str, Any] = {}
                deadline = time.time() + _APPROVAL_TIMEOUT_S
                approved = False
                while time.time() < deadline:
                    # Local event tick (1s)
                    try:
                        await asyncio.wait_for(ev.wait(), timeout=1.0)
                        # Same-worker approve fired
                        payload = _APPROVAL_PAYLOADS.pop(run_id, None) or {}
                        approved = True
                        break
                    except asyncio.TimeoutError:
                        pass
                    # DB poll fallback (cross-worker)
                    db_payload = await asyncio.to_thread(_poll_approval_db, run_id)
                    if db_payload is not None:
                        payload = db_payload
                        approved = True
                        break

                if not approved:
                    _update_run(run_id, status="failed",
                                error_text="approval_timeout",
                                finished_at=_now())
                    yield emit("approval", "error",
                               "Approval timed out — pipeline aborted")
                    return

                kept = payload.get("kept_gap_indices")
                if isinstance(kept, list) and kept:
                    kept_set = set(int(i) for i in kept)
                    plan = [p for i, p in enumerate(plan) if i in kept_set]
                    gaps = [g for i, g in enumerate(gaps) if i in kept_set]
                yield emit("approval", "done",
                           f"Approved — proceeding with {len(plan)} queries",
                           {"plan": plan, "gaps": gaps})
            finally:
                _APPROVAL_EVENTS.pop(run_id, None)
                _APPROVAL_PAYLOADS.pop(run_id, None)

        # Stage 4
        yield emit("execute", "running", f"Executing {len(plan)} queries")
        executed: List[Dict[str, Any]] = []
        for i, p in enumerate(plan, 1):
            yield emit("execute", "running",
                       f"Query {i}/{len(plan)}: {p['question'][:80]}",
                       {"current": i, "total": len(plan)})
            res = await asyncio.to_thread(stage_execute_one, project_slug, p["sql"])
            entry = {"question": p["question"], **res}
            # Phase 2: carry verified-metric flag through to synthesis + slide marking
            if p.get("verified"):
                entry["verified"] = True
                entry["source_metric"] = p.get("source_metric")
                entry["verified_value"] = p.get("verified_value")
            executed.append(entry)
            if p.get("_qid"):
                if res.get("ok"):
                    _update_query(
                        p["_qid"],
                        status="executed",
                        row_count=res.get("row_count"),
                        columns=res.get("columns") or [],
                        rows_preview=res.get("rows_preview") or [],
                        duration_ms=res.get("duration_ms"),
                    )
                else:
                    _update_query(p["_qid"], status="failed", error_text=res.get("error", "")[:400])
        _update_run(run_id, current_stage="execute", stage_progress=4)
        ok_count = sum(1 for e in executed if e.get("ok"))
        yield emit("execute", "done",
                   f"{ok_count}/{len(executed)} queries succeeded",
                   {"executed": [
                       {"question": e["question"], "ok": e.get("ok"),
                        "row_count": e.get("row_count", 0),
                        "rows_preview": e.get("rows_preview", [])[:5]}
                       for e in executed
                   ]})

        # Stage 5
        yield emit("synthesize", "running", "Synthesizing insight pack")
        insight_pack = await asyncio.to_thread(stage_synthesize, ingest, gaps, executed)
        _update_run(run_id, current_stage="synthesize", stage_progress=5)
        yield emit("synthesize", "done",
                   f"{len(insight_pack.get('supporting_evidence', []))} evidence · {len(insight_pack.get('recommendations', []))} recs",
                   {"insight_pack": insight_pack})

        # Stage 6
        audience = (config or {}).get("audience")
        aud_label = f" [{audience}]" if audience else ""
        yield emit("build", "running", f"Generating slides{aud_label}")
        build_res = await asyncio.to_thread(
            stage_build, project_slug, agent_name, messages, insight_pack, executed,
            auth_token, audience,
        )
        if not build_res.get("ok"):
            _update_run(run_id, current_stage="build", stage_progress=5,
                        status="failed", error_text=build_res.get("error", "build_failed"))
            yield emit("build", "error", build_res.get("error", "build_failed"))
            return

        slides = build_res.get("slides") or []

        # Stage 6.4 — Slide Polish (action titles · evidence cites · visual picker · narrative arc)
        # Each step is fail-soft: a failure in one polish pass never kills the pipeline.
        try:
            from dash.tools.slide_polish import (
                apply_action_titles,
                apply_evidence_citer,
                apply_visual_picker,
                apply_narrative_arc,
            )
            _narr = insight_pack.get("narrative", "") if isinstance(insight_pack, dict) else ""
            try:
                slides = await asyncio.to_thread(apply_action_titles, slides, _narr)
            except Exception as e:
                logger.warning("polish/action_titles failed: %s", e)
            try:
                slides = await asyncio.to_thread(apply_evidence_citer, slides, executed)
            except Exception as e:
                logger.warning("polish/evidence_citer failed: %s", e)
            try:
                for _s in slides or []:
                    try:
                        apply_visual_picker(_s, _s)
                    except Exception as e2:
                        logger.warning("polish/visual_picker (one slide) failed: %s", e2)
            except Exception as e:
                logger.warning("polish/visual_picker loop failed: %s", e)
            try:
                slides = await asyncio.to_thread(apply_narrative_arc, slides, _narr)
            except Exception as e:
                logger.warning("polish/narrative_arc failed: %s", e)
        except Exception as e:
            logger.warning("slide_polish stage skipped: %s", e)

        # Stage 6.5 — Slide Critic (F4)
        critique_log: List[Dict[str, Any]] = []
        critique_enabled = (config or {}).get("critique", True)
        if slides and critique_enabled:
            yield emit("critique", "running",
                       f"Adversarial review of {len(slides)} slides (max 2 passes)")
            try:
                from dash.tools.slide_critic import critique_and_patch
                cr = await asyncio.to_thread(
                    critique_and_patch, slides,
                    insight_pack.get("narrative", ""), audience, 2, 4.0,
                )
                slides = cr["slides"]
                critique_log = cr["critique_log"]
                accepted = sum(1 for c in critique_log if c.get("verdict") == "accept")
                revised = sum(1 for c in critique_log if c.get("verdict") == "revise")
                yield emit("critique", "done",
                           f"{cr['passes_used']} pass(es) · {accepted} accepted · {revised} revised",
                           {"passes_used": cr['passes_used'],
                            "critique_count": len(critique_log)})
            except Exception as e:
                logger.warning("critique stage failed: %s", e)
                yield emit("critique", "error", f"critique skipped: {e}")

        # Post-build sanitizer (Fix 1-3 — strip placeholders, fake cites)
        try:
            from dash.tools.slide_sanitizer import sanitize_deck
            san = await asyncio.to_thread(sanitize_deck, slides)
            slides = san["slides"]
            stats = san["stats"]
            if (stats["placeholders_stripped"] + stats["fake_cites_stripped"] +
                stats["dead_bullets_dropped"]) > 0:
                yield emit("critique", "done",
                           f"sanitizer: -{stats['placeholders_stripped']} placeholders · "
                           f"-{stats['fake_cites_stripped']} fake cites · "
                           f"-{stats['dead_bullets_dropped']} dead bullets",
                           {"sanitizer_stats": stats})
        except Exception as e:
            logger.warning("sanitize_deck failed: %s", e)

        # Save presentation row (legacy python-pptx output goes here)
        pres_id = await asyncio.to_thread(
            _save_pres,
            project_slug, agent_name, slides, build_res.get("thinking") or {},
            insight_pack, executed, audience,
        )

        # Persist critique log for audit
        if pres_id and critique_log:
            try:
                from dash.tools.slide_critic import save_critique_log
                await asyncio.to_thread(save_critique_log, pres_id, critique_log)
            except Exception as e:
                logger.warning("save_critique_log failed: %s", e)

        # ── Stage 8 — codegen pptxgenjs spec ──────────────────────────
        pptxgenjs_spec: Optional[Dict[str, Any]] = None
        try:
            yield emit("codegen", "running", "Generating pptxgenjs spec")
            from dash.tools.codegen_pptxgenjs import generate_pptxgenjs_spec
            brand = (config or {}).get("brand") or agent_name or project_slug
            author = (config or {}).get("author") or agent_name or "Dash"
            theme = (config or {}).get("theme") or "city_executive"
            pptxgenjs_spec = await asyncio.to_thread(
                generate_pptxgenjs_spec,
                insight_pack, executed, audience, brand, author, theme,
            )
            # Augment spec w/ deterministic chart slides from executed SQL.
            try:
                pptxgenjs_spec = _inject_chart_slides(pptxgenjs_spec, executed)
            except Exception as e:
                logger.warning("chart_mapper inject failed: %s", e)
            # Phase 2 — mark slides that use a verified metric value
            verified_slides_count = 0
            try:
                verified_slides_count = _mark_verified_slides(pptxgenjs_spec, executed)
                if verified_slides_count:
                    logger.info("deep_deck: %d slide(s) tagged as verified-vs-pinned",
                                verified_slides_count)
            except Exception as e:
                logger.warning("mark_verified_slides failed: %s", e)
            slide_n = len((pptxgenjs_spec or {}).get("slides") or [])
            yield emit("codegen", "done",
                       f"Spec generated: {slide_n} slides",
                       {"slide_count": slide_n})
            # Persist spec onto the presentation row.
            if pres_id and pptxgenjs_spec:
                try:
                    await asyncio.to_thread(
                        _update_pres_spec, pres_id, pptxgenjs_spec,
                        verified_slides_count,
                    )
                except Exception as e:
                    logger.warning("persist pptxgenjs_spec failed: %s", e)
        except Exception as e:
            logger.warning("codegen stage failed: %s", e)
            yield emit("codegen", "error", f"codegen skipped: {e}")
            pptxgenjs_spec = None

        # ── Stage 9 — render rich .pptx via Node renderer ─────────────
        rendered_pptx_path: Optional[str] = None
        if pres_id and pptxgenjs_spec and (pptxgenjs_spec.get("slides") or []):
            try:
                yield emit("render", "running", "Rendering rich .pptx (Node)")
                from dash.tools.render_pptxgenjs import render_pptx_via_js  # phase-2 file
                import os
                out_dir = f"/app/knowledge/{project_slug}/decks/{pres_id}"
                os.makedirs(out_dir, exist_ok=True)
                out_path = os.path.join(out_dir, "deck.pptx")
                rendered_pptx_path = await asyncio.to_thread(
                    render_pptx_via_js, pptxgenjs_spec, out_path,
                )
                if rendered_pptx_path:
                    try:
                        await asyncio.to_thread(
                            _update_pres_render, pres_id, rendered_pptx_path, "pptxgenjs",
                        )
                    except Exception as e:
                        logger.warning("persist render path failed: %s", e)
                    yield emit("render", "done",
                               f"Rendered: {rendered_pptx_path}",
                               {"rendered_pptx_path": rendered_pptx_path})
                    # QA: pptx → pdf → jpgs (non-blocking, fail-soft)
                    try:
                        qa_info = await asyncio.to_thread(_run_qa_loop, rendered_pptx_path)
                        if qa_info.get("pages", 0) > 0:
                            yield emit("render", "info",
                                       f"QA: {qa_info['pages']} page(s) → {qa_info['dir']}",
                                       qa_info)
                    except Exception as e:
                        logger.warning("qa loop failed: %s", e)
                else:
                    yield emit("render", "error", "Node renderer returned no path")
            except Exception as e:
                logger.warning("render stage failed: %s", e)
                yield emit("render", "error", f"render skipped: {e}")
                rendered_pptx_path = None

        # ── Phase 4 stages 8 + 9 — Vision-QA judge + iterate ─────────
        # Kill switch — env DEEP_DECK_V2_DISABLED=1 reverts to 7-stage flow.
        import os as _os_v2
        v2_disabled = (_os_v2.getenv("DEEP_DECK_V2_DISABLED", "") or "").strip() in {"1", "true", "yes", "on"}
        judge_results: List[Dict[str, Any]] = []
        iter_count = 0
        if (
            not v2_disabled
            and pptxgenjs_spec
            and (pptxgenjs_spec.get("slides") or [])
        ):
            # Cost guard — skip 8/9 when daily cap reached
            if _v2_cost_guard_blocked(project_slug):
                logger.warning(
                    "deep_deck v2: cost guard cap reached — skipping stages 8/9 for project=%s",
                    project_slug,
                )
                yield emit("vision_judge", "skipped",
                           "Skipped: daily cost cap reached")
            else:
                # Stage 8 — Vision QA judge (different model: DEEP_MODEL)
                try:
                    yield emit("vision_judge", "running",
                               "Vision-judging slides (different model)")
                    deck_ctx = {
                        "audience": audience,
                        "brand": (config or {}).get("brand") or agent_name or project_slug,
                        "theme": (config or {}).get("theme") or "city_executive",
                    }
                    # Look for rendered slide images from _run_qa_loop (qa/*.jpg)
                    qa_dir = None
                    try:
                        if rendered_pptx_path:
                            qa_dir = _os_v2.path.join(
                                _os_v2.path.dirname(rendered_pptx_path), "qa"
                            )
                            if not _os_v2.path.isdir(qa_dir):
                                qa_dir = None
                    except Exception:
                        qa_dir = None
                    judge_results = await asyncio.to_thread(
                        stage_vision_judge,
                        pptxgenjs_spec, deck_ctx, qa_dir, 12,
                    )
                    failing = sum(
                        1 for r in judge_results
                        if isinstance(r, dict) and r.get("judged")
                        and isinstance(r.get("score"), int) and r["score"] < 80
                    )
                    yield emit("vision_judge", "done",
                               f"Judged {len(judge_results)} slide(s), {failing} below 80",
                               {"judged": len(judge_results), "failing": failing,
                                "image_source": ("png" if qa_dir else "text-fallback")})
                except Exception as e:
                    logger.warning("stage_vision_judge crashed: %s", e)
                    judge_results = []
                    yield emit("vision_judge", "error",
                               f"vision judge skipped: {str(e)[:200]}")

                # Stage 9 — Iterate (regenerate < 80, cap 2 iters total)
                if judge_results and any(
                    isinstance(r, dict) and r.get("judged")
                    and isinstance(r.get("score"), int) and r["score"] < 80
                    for r in judge_results
                ):
                    try:
                        yield emit("iterate", "running",
                                   "Regenerating low-scoring slides (cap 2)")
                        current_slides = list(pptxgenjs_spec.get("slides") or [])
                        updated_slides, iter_count = await asyncio.to_thread(
                            stage_iterate, current_slides, judge_results, 2, 80,
                        )
                        if iter_count > 0 and updated_slides is not current_slides:
                            pptxgenjs_spec["slides"] = updated_slides
                            pptxgenjs_spec["total"] = len(updated_slides)
                            yield emit("iterate", "done",
                                       f"Regenerated {iter_count} slide(s)",
                                       {"iterations": iter_count})
                            # Persist updated spec
                            if pres_id:
                                try:
                                    await asyncio.to_thread(
                                        _update_pres_spec, pres_id,
                                        pptxgenjs_spec, verified_slides_count,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "persist v2 spec after iterate failed: %s", e)
                            # Re-render once with the patched slides (fail-soft)
                            try:
                                from dash.tools.render_pptxgenjs import render_pptx_via_js
                                out_dir2 = f"/app/knowledge/{project_slug}/decks/{pres_id}"
                                _os_v2.makedirs(out_dir2, exist_ok=True)
                                out_path2 = _os_v2.path.join(out_dir2, "deck.pptx")
                                rendered_pptx_path = await asyncio.to_thread(
                                    render_pptx_via_js, pptxgenjs_spec, out_path2,
                                )
                                if rendered_pptx_path and pres_id:
                                    try:
                                        await asyncio.to_thread(
                                            _update_pres_render, pres_id,
                                            rendered_pptx_path, "pptxgenjs",
                                        )
                                    except Exception as e:
                                        logger.warning(
                                            "persist re-render path failed: %s", e)
                                    yield emit("render", "done",
                                               f"Re-rendered after iterate: {rendered_pptx_path}",
                                               {"rendered_pptx_path": rendered_pptx_path,
                                                "post_iterate": True})
                            except Exception as e:
                                logger.warning("v2 re-render failed: %s", e)
                        else:
                            yield emit("iterate", "done",
                                       "No regenerations applied",
                                       {"iterations": 0})
                    except Exception as e:
                        logger.warning("stage_iterate crashed: %s", e)
                        yield emit("iterate", "error",
                                   f"iterate skipped: {str(e)[:200]}")

                # Persist judge scores + iter count regardless of regen outcome
                if pres_id and judge_results:
                    try:
                        await asyncio.to_thread(
                            _update_pres_judge_scores, pres_id,
                            judge_results, iter_count,
                        )
                    except Exception as e:
                        logger.warning("persist judge_scores failed: %s", e)

        _update_run(run_id, current_stage="build", stage_progress=6,
                    status="done", pres_id=pres_id,
                    finished_at=_now())

        # Build URLs for frontend
        urls: Dict[str, Any] = {}
        if pres_id:
            urls = {
                "edit_url": f"/ui/presentations/{pres_id}",
                "preview_url": f"/ui/project/{project_slug}/presentations/{pres_id}",
                "pptx_url": f"/api/export/presentations/{pres_id}/pptx",
            }

        yield emit("build", "done",
                   f"Deck saved: {len(slides)} slides",
                   {"pres_id": pres_id, "slide_count": len(slides), **urls})

    except Exception as e:
        logger.exception("orchestrate_deep_deck failed: %s", e)
        _update_run(run_id, status="failed", error_text=str(e)[:400], finished_at=_now())
        yield emit("error", "failed", str(e)[:300])


def _save_pres(project_slug: str, agent_name: str, slides: List[Dict[str, Any]],
               thinking: Dict[str, Any], insight_pack: Dict[str, Any],
               executed: List[Dict[str, Any]],
               audience: Optional[str] = None) -> Optional[int]:
    """Save final presentation row, embed deep-research artifacts in thinking jsonb.

    Uses raw engine (NOT the guarded get_sql_engine) because dash_presentations
    lives in `public` and the global engine blocks writes there. Mirrors the
    pattern in app/export.py save_presentation().
    """
    try:
        from sqlalchemy import create_engine as _ce, text
        from sqlalchemy.pool import NullPool
        from db import db_url
        eng = _ce(db_url, poolclass=NullPool)
    except Exception as e:
        logger.warning("_save_pres engine init failed: %s", e)
        return None
    try:
        aud_suffix = f" — {audience.title()}" if audience else ""
        title = f"{agent_name} — Deep Analysis{aud_suffix}"
        thinking_full = dict(thinking)
        thinking_full["deep_deck"] = True
        thinking_full["audience"] = audience
        thinking_full["insight_pack"] = insight_pack
        thinking_full["enriched_query_count"] = sum(1 for e in executed if e.get("ok"))
        with eng.connect() as conn:
            existing = conn.execute(
                text("SELECT MAX(version) FROM public.dash_presentations WHERE project_slug=:s AND title=:t"),
                {"s": project_slug, "t": title},
            ).scalar()
            v = (existing or 0) + 1
            r = conn.execute(
                text(
                    "INSERT INTO public.dash_presentations "
                    "(project_slug, title, version, thinking, slides, source_messages, audience) "
                    "VALUES (:s, :t, :v, CAST(:th AS jsonb), CAST(:sl AS jsonb), CAST(:msg AS jsonb), :aud) "
                    "RETURNING id"
                ),
                {
                    "s": project_slug, "t": title, "v": v,
                    "th": json.dumps(thinking_full, default=str),
                    "sl": json.dumps(slides, default=str),
                    "msg": json.dumps([]),
                    "aud": audience,
                },
            )
            pid = r.fetchone()[0]
            conn.commit()
            return int(pid)
    except Exception as e:
        logger.warning("_save_pres failed: %s", e)
        return None


def _update_pres_spec(pres_id: int, spec: Dict[str, Any],
                      verified_slides: int = 0) -> None:
    """Persist pptxgenjs_spec JSONB onto an existing dash_presentations row.

    Also merges `verified_slides: <int>` into the existing thinking JSONB
    metadata (Phase 2 — truth-grounded slides telemetry).
    """
    try:
        from sqlalchemy import create_engine as _ce, text
        from sqlalchemy.pool import NullPool
        from db import db_url
        eng = _ce(db_url, poolclass=NullPool)
    except Exception as e:
        logger.warning("_update_pres_spec engine init failed: %s", e)
        return
    try:
        with eng.connect() as conn:
            conn.execute(
                text(
                    "UPDATE public.dash_presentations "
                    "SET pptxgenjs_spec = CAST(:s AS jsonb), "
                    "    thinking = COALESCE(thinking, CAST('{}' AS jsonb)) "
                    "               || CAST(:m AS jsonb) "
                    "WHERE id = :id"
                ),
                {
                    "id": int(pres_id),
                    "s": json.dumps(spec, default=str),
                    "m": json.dumps({"verified_slides": int(verified_slides)}),
                },
            )
            conn.commit()
    except Exception as e:
        logger.warning("_update_pres_spec failed: %s", e)


def _update_pres_render(pres_id: int, path: str, engine_name: str) -> None:
    """Persist rendered_pptx_path + render_engine onto an existing row."""
    try:
        from sqlalchemy import create_engine as _ce, text
        from sqlalchemy.pool import NullPool
        from db import db_url
        eng = _ce(db_url, poolclass=NullPool)
    except Exception as e:
        logger.warning("_update_pres_render engine init failed: %s", e)
        return
    try:
        with eng.connect() as conn:
            conn.execute(
                text(
                    "UPDATE public.dash_presentations "
                    "SET rendered_pptx_path = :p, render_engine = :e "
                    "WHERE id = :id"
                ),
                {"id": int(pres_id), "p": path, "e": engine_name},
            )
            conn.commit()
    except Exception as e:
        logger.warning("_update_pres_render failed: %s", e)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
