"""Compiled-truth + evidence-trail page store.

Each "page" is a 2-section markdown document:
- TOP: compiled_truth — LLM-summarised current state (replaceable, single row).
- BOTTOM: evidence — append-only raw events (never erased).

This sits parallel to the existing dash_memory_state (last-write-wins) so we
preserve provenance for every assertion the agent makes about the world.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


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


# ── Page lifecycle ──────────────────────────────────────────────────────
def create_or_get_page(
    project: str,
    page_key: str,
    title: Optional[str] = None,
) -> Optional[int]:
    """Idempotent. Returns the page id."""
    eng = _get_engine()
    if eng is None:
        return None
    try:
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_pages (project_slug, page_key, title)
                    VALUES (:p, :k, :t)
                    ON CONFLICT (project_slug, page_key) DO UPDATE
                      SET title = COALESCE(EXCLUDED.title, dash.dash_pages.title),
                          updated_at = now()
                    RETURNING id
                    """
                ),
                {"p": project, "k": page_key, "t": title},
            ).first()
            return int(row[0]) if row else None
    except Exception:
        logger.exception("create_or_get_page failed")
        return None


def get_page(page_id: int) -> Optional[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, project_slug, page_key, title, compiled_truth,
                           compiled_at, compiled_by, content_hash,
                           created_at, updated_at
                    FROM dash.dash_pages WHERE id = :id
                    """
                ),
                {"id": page_id},
            ).mappings().first()
            if not row:
                return None
            d = dict(row)
            with conn.begin():
                cnt = conn.execute(
                    text("SELECT count(*) FROM dash.dash_page_evidence WHERE page_id = :id"),
                    {"id": page_id},
                ).scalar() or 0
            d["evidence_count"] = int(cnt)
            return d
    except Exception:
        logger.exception("get_page failed")
        return None


def list_pages(
    project: str,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return []
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT p.id, p.project_slug, p.page_key, p.title,
                           p.compiled_at, p.compiled_by,
                           p.created_at, p.updated_at,
                           (SELECT count(*) FROM dash.dash_page_evidence e
                              WHERE e.page_id = p.id) AS evidence_count
                    FROM dash.dash_pages p
                    WHERE CAST(:proj AS TEXT) IS NULL OR p.project_slug = :proj
                    ORDER BY p.updated_at DESC
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"proj": project, "lim": limit, "off": offset},
            ).mappings().all()
            return [dict(r) for r in rows]
    except Exception:
        logger.exception("list_pages failed")
        return []


def delete_page(page_id: int) -> bool:
    eng = _get_engine()
    if eng is None:
        return False
    try:
        with eng.begin() as conn:
            r = conn.execute(
                text("DELETE FROM dash.dash_pages WHERE id = :id"),
                {"id": page_id},
            )
        return r.rowcount > 0
    except Exception:
        logger.exception("delete_page failed")
        return False


# ── Evidence (append-only) ──────────────────────────────────────────────
def append_evidence(
    page_id: int,
    content: str,
    source: str,
    source_ref: Optional[str] = None,
    author: Optional[str] = None,
) -> Optional[int]:
    """Pure append. Never overwrites prior evidence."""
    eng = _get_engine()
    if eng is None:
        return None
    if not (content or "").strip():
        return None
    try:
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_page_evidence
                      (page_id, source, source_ref, content, author)
                    VALUES (:pid, :s, :sr, :c, :a)
                    RETURNING id
                    """
                ),
                {"pid": page_id, "s": source, "sr": source_ref,
                 "c": content, "a": author},
            ).first()
            # Bump the page's updated_at so list ordering reflects new evidence.
            conn.execute(
                text("UPDATE dash.dash_pages SET updated_at = now() WHERE id = :id"),
                {"id": page_id},
            )
            return int(row[0]) if row else None
    except Exception:
        logger.exception("append_evidence failed")
        return None


def list_evidence(
    page_id: int,
    limit: int = 200,
    since: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return []
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, page_id, ts, source, source_ref, content, author
                    FROM dash.dash_page_evidence
                    WHERE page_id = :pid
                      AND (CAST(:since AS TIMESTAMPTZ) IS NULL OR ts > :since)
                    ORDER BY ts DESC
                    LIMIT :lim
                    """
                ),
                {"pid": page_id, "since": since, "lim": limit},
            ).mappings().all()
            return [dict(r) for r in rows]
    except Exception:
        logger.exception("list_evidence failed")
        return []


# ── Compile ─────────────────────────────────────────────────────────────
_COMPILE_PROMPT = """You are maintaining a knowledge page. Below is the
existing compiled-truth block (the agreed current state) followed by recent
evidence events. Rewrite the compiled-truth block so it accurately reflects
the latest state implied by the evidence. Keep it concise, factual, and
markdown-formatted. Do NOT include the evidence list — only the compiled
truth.

# CURRENT COMPILED TRUTH
{current}

# RECENT EVIDENCE (newest first)
{evidence}

Return only the new compiled-truth markdown."""


def _fallback_compile(evidence_rows: List[Dict[str, Any]]) -> str:
    """Used when no LLM is available — concat of last 10 evidence rows."""
    lines = ["_Compiled without LLM — concatenated evidence:_", ""]
    for e in evidence_rows[:10]:
        ts = e.get("ts")
        ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        src = e.get("source") or "?"
        lines.append(f"- **{ts_s}** ({src}): {e.get('content', '')}")
    return "\n".join(lines)


def recompile(page_id: int, llm_call=None, author: str = "system") -> Optional[str]:
    """Pull last N evidence rows, ask LLM to rewrite the compiled-truth block,
    persist the result. Returns the new compiled_truth."""
    page = get_page(page_id)
    if not page:
        return None
    evidence = list_evidence(page_id, limit=50)
    if not evidence:
        return page.get("compiled_truth") or ""

    new_truth: Optional[str] = None
    if llm_call is None:
        try:
            from dash.settings import training_llm_call as _llm
            llm_call = _llm
        except Exception:
            llm_call = None

    if llm_call is not None:
        try:
            ev_block = "\n".join(
                f"- [{(e.get('ts').isoformat() if hasattr(e.get('ts'), 'isoformat') else e.get('ts'))}]"
                f" ({e.get('source') or '?'}) {e.get('content', '')}"
                for e in evidence
            )
            prompt = _COMPILE_PROMPT.format(
                current=(page.get("compiled_truth") or "_(empty)_")[:4000],
                evidence=ev_block[:8000],
            )
            try:
                raw = llm_call(prompt, task="extraction")
            except TypeError:
                raw = llm_call(prompt)
            if raw:
                new_truth = str(raw).strip()
        except Exception:
            logger.exception("recompile: LLM call failed; falling back")
            new_truth = None

    if not new_truth:
        new_truth = _fallback_compile(evidence)

    eng = _get_engine()
    if eng is None:
        return new_truth
    content_hash = hashlib.sha256(new_truth.encode("utf-8")).hexdigest()
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE dash.dash_pages
                       SET compiled_truth = :t,
                           compiled_at    = now(),
                           compiled_by    = :by,
                           content_hash   = :h,
                           updated_at     = now()
                     WHERE id = :id
                    """
                ),
                {"t": new_truth, "by": author, "h": content_hash, "id": page_id},
            )
    except Exception:
        logger.exception("recompile: persist failed")
    return new_truth


# ── Render ──────────────────────────────────────────────────────────────
def render_page(page_id: int) -> str:
    page = get_page(page_id)
    if not page:
        return ""
    evidence = list_evidence(page_id, limit=500)
    title = page.get("title") or page.get("page_key") or f"page {page_id}"
    compiled = (page.get("compiled_truth") or "_(not yet compiled)_").strip()
    lines = [f"# {title}", "", compiled, "", "---", "", "## Evidence"]
    if not evidence:
        lines.append("_(no evidence recorded)_")
    else:
        for e in evidence:
            ts = e.get("ts")
            ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            src = e.get("source") or "?"
            ref = f" `{e['source_ref']}`" if e.get("source_ref") else ""
            author = f" — {e['author']}" if e.get("author") else ""
            lines.append(f"- **{ts_s}** ({src}{ref}){author}: {e.get('content', '')}")
    return "\n".join(lines)
