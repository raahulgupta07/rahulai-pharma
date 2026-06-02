"""Skill drafter — LLM-powered SKILL.md author with Voyager-style iteration.

Public API:
- draft_skill(...)            — reads conversation, drafts SKILL.md, persists pending draft
- parse_frontmatter(md)       — strict YAML frontmatter parser → (dict, body)
- reject_draft(...)           — mark a draft rejected (idempotent)
- approve_draft(...)          — approve + promote into dash.dash_skills via registry

All persistence goes through `dash.dash_skill_drafts`. Behind EXPERIMENTAL_AGI=1.
When the flag is off, draft_skill returns {ok: False, reason: 'disabled'} and
NO row is inserted.
"""
from __future__ import annotations

import json as _json
import logging
import os
import re
import secrets
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Flag gate ──────────────────────────────────────────────────────────────
def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


# ── DB engine ──────────────────────────────────────────────────────────────
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


# ── 4-tier JSON parse fallback ─────────────────────────────────────────────
def _parse_json_robust(raw: str) -> Optional[Dict[str, Any]]:
    """direct → strip ```fences → regex first {...} → trailing-comma repair."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()

    # Tier 1: direct
    try:
        return _json.loads(s)
    except Exception:
        pass

    # Tier 2: strip code fences (```json ... ```)
    stripped = s
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped[3:]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    stripped = stripped.strip().strip("`").strip()
    if stripped.lower().startswith("json"):
        stripped = stripped[4:].strip()
    try:
        return _json.loads(stripped)
    except Exception:
        pass

    # Tier 3: regex extract first {...} block (greedy)
    m = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if m:
        block = m.group(0)
        try:
            return _json.loads(block)
        except Exception:
            # Tier 4: trailing-comma repair on the block
            repaired = re.sub(r",\s*([}\]])", r"\1", block)
            try:
                return _json.loads(repaired)
            except Exception:
                pass

    # Tier 4 (last resort): trailing-comma repair on whole string
    repaired = re.sub(r",\s*([}\]])", r"\1", stripped)
    try:
        return _json.loads(repaired)
    except Exception:
        return None


# ── SKILL.md frontmatter helpers ───────────────────────────────────────────
_FM_BOUNDARY = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_frontmatter(md: str) -> Tuple[Dict[str, Any], str]:
    """Parse SKILL.md → (frontmatter_dict, body_text). Strict YAML between ---.

    Supports simple scalars (str/int/bool) and inline JSON-style lists like
    `trigger_keywords: [a, b, "c"]`. Returns ({}, md) when frontmatter
    boundaries are missing or malformed.
    """
    if not isinstance(md, str):
        return {}, ""
    m = _FM_BOUNDARY.match(md.lstrip("﻿").lstrip())
    if not m:
        return {}, md

    fm_block = m.group(1)
    body = m.group(2) or ""
    out: Dict[str, Any] = {}

    for line in fm_block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue

        # Inline list: [a, b, "c"]
        if val.startswith("[") and val.endswith("]"):
            try:
                parsed = _json.loads(val)
                if isinstance(parsed, list):
                    out[key] = parsed
                    continue
            except Exception:
                inner = val[1:-1]
                items = [
                    x.strip().strip('"').strip("'")
                    for x in inner.split(",")
                    if x.strip()
                ]
                out[key] = items
                continue

        # Quoted string
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            out[key] = val[1:-1]
            continue

        # Bool / int / fallback string
        if val.lower() in ("true", "false"):
            out[key] = val.lower() == "true"
        else:
            try:
                out[key] = int(val)
            except Exception:
                out[key] = val

    return out, body.lstrip("\n")


def _assemble_skill_md(fm: Dict[str, Any], body: str) -> str:
    """Render a frontmatter dict + body into canonical SKILL.md format."""
    lines = ["---"]
    # Stable key order
    for key in ("name", "description", "trigger_keywords", "allowed_tools"):
        if key not in fm:
            continue
        v = fm[key]
        if isinstance(v, list):
            inner = ", ".join(_json.dumps(x) if isinstance(x, str) else str(x) for x in v)
            lines.append(f"{key}: [{inner}]")
        elif isinstance(v, bool):
            lines.append(f"{key}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{key}: {v}")
        else:
            s = str(v).replace("\n", " ").strip()
            lines.append(f"{key}: {s}")
    # Extra keys
    for key, v in fm.items():
        if key in ("name", "description", "trigger_keywords", "allowed_tools"):
            continue
        if isinstance(v, list):
            inner = ", ".join(_json.dumps(x) if isinstance(x, str) else str(x) for x in v)
            lines.append(f"{key}: [{inner}]")
        else:
            lines.append(f"{key}: {v}")
    lines.append("---")
    body = (body or "").strip("\n")
    if body:
        lines.append("")
        lines.append(body)
    return "\n".join(lines) + "\n"


def _slugify(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unnamed-skill"


# ── Prompt template ────────────────────────────────────────────────────────
_DRAFT_PROMPT_TEMPLATE = """You are a skill authoring assistant for a multi-agent data analytics platform.

Read the following conversation excerpt and propose a reusable SKILL that
captures the workflow demonstrated. A skill encapsulates clear instructions,
examples, and edge cases an agent can follow on similar future questions.

CONVERSATION EXCERPT:
\"\"\"
{excerpt}
\"\"\"

TRIGGER PHRASE (what prompted skill creation): {trigger}
{prev_feedback}

Output STRICT JSON ONLY (no prose, no markdown fences) with this exact shape:
{{
  "name": "<short kebab-case-or-Title Case name>",
  "description": "<=200 chars describing WHEN to invoke this skill>",
  "trigger_keywords": ["kw1", "kw2", "..."],
  "allowed_tools": ["tool_name_1", "tool_name_2"],
  "body_markdown": "<markdown body with: ## Overview, ## Instructions (steps),\
 ## Examples, ## Edge Cases. Be specific. Reference concrete column/table\
 names from the conversation when relevant.>"
}}

Rules:
- description MUST be <=200 chars
- trigger_keywords: 3-8 short phrases that signal this skill applies
- allowed_tools: tool names the skill uses (empty array if generic)
- body_markdown: actionable instructions, not a summary of the chat
- Output JSON only. No leading/trailing prose.
"""


def _build_prompt(excerpt: str, trigger: str, prev_feedback: Optional[str]) -> str:
    fb = ""
    if prev_feedback:
        fb = (
            "\nPREVIOUS ATTEMPT FAILED VERIFICATION. Address this feedback:\n"
            f"{prev_feedback}\n"
        )
    return _DRAFT_PROMPT_TEMPLATE.format(
        excerpt=(excerpt or "")[:8000],
        trigger=(trigger or "(none)")[:300],
        prev_feedback=fb,
    )


# ── LLM call wrapper ───────────────────────────────────────────────────────
def _call_llm(prompt: str, task: str = "deep_analysis") -> Optional[str]:
    try:
        from dash.settings import training_llm_call
    except Exception as e:
        logger.warning("drafter: training_llm_call import failed: %s", e)
        return None
    try:
        return training_llm_call(prompt, task=task)
    except Exception as e:
        logger.warning("drafter: LLM call failed: %s", e)
        return None


# ── Validation of LLM payload ──────────────────────────────────────────────
def _validate_payload(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if not isinstance(payload, dict):
        return False, "payload_not_object"
    name = payload.get("name")
    desc = payload.get("description")
    body = payload.get("body_markdown")
    if not isinstance(name, str) or not name.strip():
        return False, "missing_name"
    if not isinstance(desc, str) or not desc.strip():
        return False, "missing_description"
    if len(desc) > 200:
        return False, "description_too_long"
    if not isinstance(body, str) or not body.strip():
        return False, "missing_body"
    if not isinstance(payload.get("trigger_keywords", []), list):
        return False, "trigger_keywords_not_list"
    if not isinstance(payload.get("allowed_tools", []), list):
        return False, "allowed_tools_not_list"
    return True, None


# ── DB helpers ─────────────────────────────────────────────────────────────
def _find_existing_pending(
    project_slug: Optional[str], proposed_name: str
) -> Optional[str]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id FROM dash.dash_skill_drafts
                    WHERE proposed_name = :nm
                      AND (
                        (CAST(:ps AS TEXT) IS NULL AND project_slug IS NULL)
                        OR project_slug = :ps
                      )
                      AND status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"nm": proposed_name, "ps": project_slug},
            ).first()
        return row[0] if row else None
    except Exception as e:
        logger.warning("drafter: dedup lookup failed: %s", e)
        return None


def _insert_draft(meta: Dict[str, Any]) -> Optional[str]:
    eng = _get_engine()
    draft_id = meta.get("id") or ("sd_" + secrets.token_hex(4))
    if eng is None:
        logger.warning("drafter: no DB engine — draft not persisted")
        return None
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_skill_drafts
                      (id, project_slug, source_run_id, source_conversation_excerpt,
                       drafted_by_agent, trigger_phrase, iteration,
                       proposed_name, proposed_description, proposed_skill_md,
                       frontmatter, verifier_results, status)
                    VALUES
                      (:id, :ps, :rid, :ex, :ag, :tp, :it,
                       :nm, :ds, :md,
                       CAST(:fm AS jsonb), CAST(:vr AS jsonb), :st)
                    """
                ),
                {
                    "id": draft_id,
                    "ps": meta.get("project_slug"),
                    "rid": meta.get("source_run_id"),
                    "ex": meta.get("source_conversation_excerpt"),
                    "ag": meta.get("drafted_by_agent"),
                    "tp": meta.get("trigger_phrase"),
                    "it": int(meta.get("iteration", 1)),
                    "nm": meta.get("proposed_name"),
                    "ds": meta.get("proposed_description"),
                    "md": meta.get("proposed_skill_md"),
                    "fm": _json.dumps(meta.get("frontmatter") or {}),
                    "vr": _json.dumps(meta.get("verifier_results") or []),
                    "st": meta.get("status", "pending"),
                },
            )
        return draft_id
    except Exception as e:
        logger.warning("drafter: insert failed: %s", e)
        return None


# ── Public API ─────────────────────────────────────────────────────────────
def draft_skill(
    conversation_excerpt: str,
    trigger_phrase: str = "",
    project_slug: Optional[str] = None,
    drafted_by_agent: str = "Leader",
    source_run_id: Optional[str] = None,
    max_iterations: int = 3,
    verifier_callback: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Draft a SKILL.md from a conversation excerpt and persist as pending.

    Voyager loop: up to `max_iterations` attempts. On each iteration the
    verifier_callback (if provided) is invoked between LLM calls. It must
    return a dict with at least {"ok": bool, "feedback": str}. If ok=False
    and we have iterations left, the LLM is re-prompted with the feedback.

    Returns:
        {ok, draft_id, proposed_name, frontmatter, skill_md, iterations_used}
        or {ok: False, reason: '...'} on failure / disabled.
    """
    if not _enabled():
        return {"ok": False, "reason": "disabled"}

    max_iterations = max(1, min(int(max_iterations or 1), 5))
    prev_feedback: Optional[str] = None
    verifier_history: List[Dict[str, Any]] = []
    last_payload: Optional[Dict[str, Any]] = None
    last_validation_err: Optional[str] = None

    for i in range(1, max_iterations + 1):
        prompt = _build_prompt(conversation_excerpt or "", trigger_phrase, prev_feedback)
        raw = _call_llm(prompt, task="deep_analysis")
        if not raw:
            last_validation_err = "llm_no_response"
            verifier_history.append({"iter": i, "ok": False, "error": last_validation_err})
            continue
        payload = _parse_json_robust(raw)
        ok, err = _validate_payload(payload or {})
        if not ok:
            last_validation_err = err
            verifier_history.append({"iter": i, "ok": False, "error": err})
            prev_feedback = (
                f"Previous output failed validation: {err}. "
                "Output STRICT JSON with name, description (<=200 chars), "
                "trigger_keywords (list), allowed_tools (list), body_markdown."
            )
            continue
        last_payload = payload  # type: ignore[assignment]

        if verifier_callback is not None:
            try:
                result = verifier_callback(payload) or {}
            except Exception as e:
                result = {"ok": False, "feedback": f"verifier_exception: {e}"}
            verifier_history.append({"iter": i, **result})
            if result.get("ok"):
                break
            if i < max_iterations:
                prev_feedback = str(result.get("feedback") or "verifier rejected the draft")
                continue
            # exhausted retries — fall through with last_payload retained
            break
        else:
            verifier_history.append({"iter": i, "ok": True})
            break

    if not last_payload:
        return {
            "ok": False,
            "reason": "validation_failed",
            "error": last_validation_err,
            "iterations_used": len(verifier_history),
        }

    # Assemble frontmatter and SKILL.md
    name = str(last_payload["name"]).strip()
    slug = _slugify(name)
    description = str(last_payload["description"]).strip()
    trigger_keywords = list(last_payload.get("trigger_keywords") or [])
    allowed_tools = list(last_payload.get("allowed_tools") or [])
    body_md = str(last_payload["body_markdown"]).strip()

    frontmatter = {
        "name": slug,
        "description": description,
        "trigger_keywords": trigger_keywords,
        "allowed_tools": allowed_tools,
    }
    # Body header uses original name if it looks human-friendly, else slug
    header = name if name else slug
    skill_md = _assemble_skill_md(frontmatter, f"# {header}\n\n{body_md}")

    # Idempotency: dedup on (project_slug, proposed_name=slug) when pending exists
    existing = _find_existing_pending(project_slug, slug)
    if existing:
        return {
            "ok": True,
            "draft_id": existing,
            "proposed_name": slug,
            "frontmatter": frontmatter,
            "skill_md": skill_md,
            "iterations_used": len(verifier_history),
            "deduped": True,
        }

    draft_id = _insert_draft({
        "project_slug": project_slug,
        "source_run_id": source_run_id,
        "source_conversation_excerpt": (conversation_excerpt or "")[:10000],
        "drafted_by_agent": drafted_by_agent,
        "trigger_phrase": trigger_phrase,
        "iteration": len(verifier_history),
        "proposed_name": slug,
        "proposed_description": description,
        "proposed_skill_md": skill_md,
        "frontmatter": frontmatter,
        "verifier_results": verifier_history,
        "status": "pending",
    })

    if not draft_id:
        return {
            "ok": False,
            "reason": "persist_failed",
            "proposed_name": slug,
            "frontmatter": frontmatter,
            "skill_md": skill_md,
            "iterations_used": len(verifier_history),
        }

    return {
        "ok": True,
        "draft_id": draft_id,
        "proposed_name": slug,
        "frontmatter": frontmatter,
        "skill_md": skill_md,
        "iterations_used": len(verifier_history),
    }


def reject_draft(
    draft_id: str, reason: str, approver_id: Optional[int] = None
) -> Dict[str, Any]:
    """Mark draft rejected. Idempotent: re-rejecting returns ok=True, already_rejected=True."""
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "reason": "no_engine"}
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            row = conn.execute(
                text("SELECT status FROM dash.dash_skill_drafts WHERE id = :id"),
                {"id": draft_id},
            ).first()
            if not row:
                return {"ok": False, "reason": "not_found"}
            current = row[0]
            if current == "rejected":
                return {"ok": True, "already_rejected": True}
            conn.execute(
                text(
                    """
                    UPDATE dash.dash_skill_drafts
                    SET status = 'rejected',
                        rejection_reason = :rr,
                        approved_by = :ab,
                        approved_at = now()
                    WHERE id = :id
                    """
                ),
                {"id": draft_id, "rr": reason, "ab": approver_id},
            )
        return {"ok": True, "draft_id": draft_id, "status": "rejected"}
    except Exception as e:
        logger.warning("drafter: reject failed: %s", e)
        return {"ok": False, "reason": "db_error", "error": str(e)[:200]}


def approve_draft(draft_id: str, approver_id: int) -> Dict[str, Any]:
    """Approve a pending draft and promote it into dash.dash_skills.

    Idempotent: if already approved, returns existing promoted_skill_id.
    """
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "reason": "no_engine"}
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, project_slug, proposed_name, proposed_description,
                           proposed_skill_md, frontmatter, status, promoted_skill_id
                    FROM dash.dash_skill_drafts WHERE id = :id
                    """
                ),
                {"id": draft_id},
            ).mappings().first()
        if not row:
            return {"ok": False, "reason": "not_found"}
        if row["status"] == "approved" and row.get("promoted_skill_id"):
            return {
                "ok": True,
                "draft_id": draft_id,
                "skill_id": row["promoted_skill_id"],
                "already_approved": True,
            }
        if row["status"] == "rejected":
            return {"ok": False, "reason": "already_rejected"}

        fm = row["frontmatter"] or {}
        if isinstance(fm, str):
            try:
                fm = _json.loads(fm)
            except Exception:
                fm = {}

        # Parse body from skill_md for instructions
        _, body = parse_frontmatter(row["proposed_skill_md"] or "")
        instructions = body or (row["proposed_skill_md"] or "")

        # Promote via registry
        try:
            from dash.skills.registry import register_skill
        except Exception as e:
            return {"ok": False, "reason": "registry_import_failed", "error": str(e)[:200]}

        skill_meta = {
            "project_slug": row["project_slug"],
            "name": row["proposed_name"],
            "category": fm.get("category", "meta"),
            "description": row["proposed_description"],
            "trigger_keywords": fm.get("trigger_keywords") or [],
            "instructions": instructions,
            "tools": [{"name": t} for t in (fm.get("allowed_tools") or []) if t],
            "is_builtin": False,
        }
        skill_id = register_skill(skill_meta)
        if not skill_id:
            return {"ok": False, "reason": "promotion_failed"}

        try:
            from sqlalchemy import text
            with eng.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE dash.dash_skill_drafts
                        SET status = 'approved',
                            approved_by = :ab,
                            approved_at = now(),
                            promoted_skill_id = :sid
                        WHERE id = :id
                        """
                    ),
                    {"id": draft_id, "ab": approver_id, "sid": skill_id},
                )
        except Exception as e:
            logger.warning("drafter: approve UPDATE failed (skill already promoted): %s", e)

        return {"ok": True, "draft_id": draft_id, "skill_id": skill_id}
    except Exception as e:
        logger.warning("drafter: approve failed: %s", e)
        return {"ok": False, "reason": "db_error", "error": str(e)[:200]}
