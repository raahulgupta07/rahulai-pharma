"""learning_goals.md — human-editable agent program per project.

File location: knowledge/{project_slug}/learning_goals.md

Read on every cycle. Injected into CuriosityEngine prompt to bias
question generation toward user's stated goals.

If absent, defaults to a generic template that the user can customize.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path("knowledge")
DEFAULT_TEMPLATE = """\
# Learning Goals — {slug}

## What I want this agent to learn
(Replace this with bullet points describing what you want the agent
to become expert at — e.g. customer churn drivers, revenue trends
by region, common data quality issues, etc.)

## Topics to prioritize
- (e.g. "customer behavior patterns")
- (e.g. "anomaly root causes")

## Topics to deprioritize / skip
- (e.g. "marketing channel performance" — already covered elsewhere)

## Success criteria
- (e.g. "answer 90% of business questions correctly")
- (e.g. "detect anomalies within 24h of occurrence")

## Constraints
- Avoid generating questions about: (e.g. specific PII, financial details)
- Stay within domain: (e.g. retail / finance / healthcare)
"""


def goals_path(project_slug: str) -> Path:
    return KNOWLEDGE_DIR / project_slug / "learning_goals.md"


def read_goals(project_slug: str) -> str:
    """Return current goals.md content; create from template if absent."""
    p = goals_path(project_slug)
    if not p.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(DEFAULT_TEMPLATE.format(slug=project_slug))
        except Exception as e:
            logger.warning(f"goals init failed: {e}")
            return DEFAULT_TEMPLATE.format(slug=project_slug)
    try:
        return p.read_text()
    except Exception as e:
        logger.warning(f"goals read failed: {e}")
        return ""


def write_goals(project_slug: str, content: str) -> bool:
    """Persist goals.md content. Returns True on success."""
    p = goals_path(project_slug)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content[:50000])  # cap 50KB
        return True
    except Exception as e:
        logger.warning(f"goals write failed: {e}")
        return False


def goals_summary_for_prompt(project_slug: str, max_chars: int = 1500) -> str:
    """Compact goals snippet for injection into LLM prompts.

    Strips heavy markdown, keeps bullets. Returns empty string if file
    is still the unedited template (user hasn't customized).
    """
    raw = read_goals(project_slug)
    if not raw:
        return ""
    # Strip Replace this with... boilerplate to detect unedited template
    if "Replace this with bullet points" in raw and "(e.g." in raw:
        return ""
    # Keep first 1500 chars, strip excess whitespace
    cleaned = "\n".join(line.rstrip() for line in raw.split("\n")
                          if line.strip())
    return cleaned[:max_chars]
