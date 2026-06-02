"""Skill resolver — LLM intent-classification router.

Picks the best downstream skill for a user query from the registry.
Replaces word-overlap matching with intent classification.

Public API:
- list_candidate_skills(project) -> [{name, description, tags}]
- resolve(query, project, top_k=3) -> {chosen, candidates, reason}

Falls back to registry word-overlap (find_skills_for) when LLM unavailable
or returns unparseable output. Read-only: never mutates registry.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def list_candidate_skills(project: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read candidate skills from registry (no mutations).

    Returns minimal projection: name, description, tags. Uses
    registry.list_skills() which supports project_slug filtering and
    returns trigger_keywords (treated as tags here).
    """
    try:
        from dash.skills.registry import list_skills
    except Exception as e:
        logger.warning("resolver: registry import failed: %s", e)
        return []
    try:
        raw = list_skills(project_slug=project)
    except Exception as e:
        logger.warning("resolver: list_skills failed: %s", e)
        return []
    out: List[Dict[str, Any]] = []
    for s in raw or []:
        kws = s.get("trigger_keywords") or []
        if isinstance(kws, str):
            try:
                kws = json.loads(kws)
            except Exception:
                kws = []
        out.append({
            "name": s.get("name") or s.get("id"),
            "id": s.get("id"),
            "description": s.get("description") or "",
            "category": s.get("category"),
            "tags": kws or [],
        })
    return out


def _parse_json_lenient(text: str) -> Optional[Dict[str, Any]]:
    """Tolerant JSON parse: direct → strip fences → first {...} regex."""
    if not text:
        return None
    s = text.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # strip ``` fences
    s2 = re.sub(r"^```(?:json)?\s*", "", s)
    s2 = re.sub(r"\s*```$", "", s2).strip()
    try:
        return json.loads(s2)
    except Exception:
        pass
    m = re.search(r"\{.*\}", s2 or s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _fallback_top1(query: str, project: Optional[str]) -> Optional[Dict[str, Any]]:
    """Word-overlap fallback via existing registry.find_skills_for."""
    try:
        from dash.skills.registry import find_skills_for
        hits = find_skills_for(query, project_slug=project, top_k=1)
        if hits:
            h = hits[0]
            return {
                "name": h.get("name") or h.get("id"),
                "id": h.get("id"),
                "description": h.get("description") or "",
            }
    except Exception as e:
        logger.warning("resolver: fallback find_skills_for failed: %s", e)
    return None


def resolve(query: str, project: Optional[str] = None, top_k: int = 3) -> Dict[str, Any]:
    """Pick best skill for query via LLM intent classification.

    Returns:
        {
          "chosen": str | None,
          "reason": str,
          "candidates": [{name, description, tags, ...}, ...],
          "method": "llm" | "fallback_overlap" | "empty",
        }
    """
    candidates = list_candidate_skills(project)
    if not candidates:
        return {
            "chosen": None,
            "reason": "no skills registered for this project",
            "candidates": [],
            "method": "empty",
        }

    # Build numbered list for prompt
    lines = []
    for i, c in enumerate(candidates, 1):
        tags = ", ".join(c.get("tags") or [])
        tag_str = f" [tags: {tags}]" if tags else ""
        desc = (c.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 240:
            desc = desc[:240] + "…"
        lines.append(f"{i}. {c['name']} — {desc}{tag_str}")
    skills_block = "\n".join(lines)

    prompt = (
        f"User query: {query}\n\n"
        f"Skills:\n{skills_block}\n\n"
        "Pick the BEST skill for this query based on user intent. "
        "If no skill clearly fits, set chosen to null.\n"
        'Return ONLY JSON: {"chosen": "skill_name", "reason": "one-sentence why"}'
    )

    chosen: Optional[str] = None
    reason = ""
    method = "llm"
    try:
        from dash.llm import training_llm_call  # type: ignore
        raw = training_llm_call(prompt, task="extraction")
    except Exception:
        # Fall back to settings.training_llm_call (canonical in this repo)
        try:
            from dash.settings import training_llm_call  # type: ignore
            raw = training_llm_call(prompt, task="routing")
        except Exception as e:
            logger.warning("resolver: training_llm_call import failed: %s", e)
            raw = None

    parsed = _parse_json_lenient(raw or "") if raw else None
    if parsed and isinstance(parsed, dict):
        ch = parsed.get("chosen")
        if isinstance(ch, str) and ch.strip():
            # Validate chosen against candidate names (case-insensitive)
            names = {c["name"]: c["name"] for c in candidates if c.get("name")}
            names_lower = {n.lower(): n for n in names}
            chosen = names.get(ch) or names_lower.get(ch.strip().lower())
        reason = str(parsed.get("reason") or "")

    if not chosen:
        fb = _fallback_top1(query, project)
        if fb:
            chosen = fb["name"]
            reason = reason or "fallback: word-overlap top-1 (LLM unavailable or unparseable)"
            method = "fallback_overlap"
        else:
            method = "empty"
            reason = reason or "no clear match; LLM and overlap fallback both empty"

    return {
        "chosen": chosen,
        "reason": reason,
        "candidates": candidates[: max(top_k, 1)] if top_k else candidates,
        "method": method,
    }
