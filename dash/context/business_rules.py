"""Load business definitions, metrics, and common gotchas."""

import json
from pathlib import Path
from typing import Any

from agno.utils.log import logger

from dash.paths import BUSINESS_DIR


def load_business_rules(business_dir: Path | None = None) -> dict[str, list[Any]]:
    """Load business definitions from JSON files."""
    if business_dir is None:
        business_dir = BUSINESS_DIR

    business: dict[str, list[Any]] = {"metrics": [], "business_rules": [], "common_gotchas": []}

    if not business_dir.exists():
        return business

    for filepath in sorted(business_dir.glob("*.json")):
        try:
            with open(filepath) as f:
                data = json.load(f)
            for key in business:
                if key in data:
                    business[key].extend(data[key])
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load {filepath}: {e}")

    return business


def build_business_context(business_dir: Path | None = None) -> str:
    """Build business context string for system prompt."""
    business = load_business_rules(business_dir)
    lines: list[str] = []

    # Metrics
    if business["metrics"]:
        lines.append("## METRICS\n")
        for m in business["metrics"]:
            lines.append(f"**{m.get('name', 'Unknown')}**: {m.get('definition', '')}")
            if m.get("table"):
                lines.append(f"  - Table: `{m['table']}`")
            if m.get("calculation"):
                lines.append(f"  - Calculation: {m['calculation']}")
            lines.append("")

    # Business rules
    if business["business_rules"]:
        lines.append("## BUSINESS RULES\n")
        for rule in business["business_rules"]:
            lines.append(f"- {rule}")
        lines.append("")

    # Common gotchas
    if business["common_gotchas"]:
        lines.append("## COMMON GOTCHAS\n")
        for g in business["common_gotchas"]:
            lines.append(f"**{g.get('issue', 'Unknown')}**")
            if g.get("tables_affected"):
                lines.append(f"  - Tables: {', '.join(g['tables_affected'])}")
            if g.get("solution"):
                lines.append(f"  - Solution: {g['solution']}")
            lines.append("")

    return "\n".join(lines)


def load_project_rules(project_slug: str) -> list[dict[str, Any]]:
    """Load user-defined rules for a project."""
    from dash.paths import KNOWLEDGE_DIR

    rules_dir = KNOWLEDGE_DIR / project_slug / "rules"
    rules: list[dict[str, Any]] = []

    if not rules_dir.exists():
        return rules

    for filepath in sorted(rules_dir.glob("*.json")):
        try:
            with open(filepath) as f:
                data = json.load(f)
            rules.append(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load rule {filepath}: {e}")

    return rules


def build_project_rules_context(project_slug: str) -> str:
    """Build user-defined rules context string for system prompt — files + DB rules."""
    rules = load_project_rules(project_slug)

    # Also load rules from DB (KPIs, calculations, metrics from auto-training)
    db_rules = _load_db_rules(project_slug)
    all_rules = rules + db_rules

    if not all_rules:
        return ""

    lines: list[str] = ["## PROJECT RULES & KPIs\n"]
    lines.append("These rules were defined by the project owner or auto-extracted from data. ALWAYS follow them.\n")

    # Group by type
    by_type: dict[str, list] = {}
    for r in all_rules:
        rtype = r.get("type", "business_rule")
        by_type.setdefault(rtype, []).append(r)

    type_labels = {"kpi": "KPI DEFINITIONS", "calculation": "CALCULATION RULES", "metric": "KEY METRICS", "business_rule": "BUSINESS RULES"}
    for rtype, items in by_type.items():
        label = type_labels.get(rtype, rtype.upper())
        lines.append(f"### {label}")
        for r in items:
            name = r.get("name", "Unnamed")
            definition = r.get("definition", "")
            lines.append(f"- **{name}**: {definition}")
        lines.append("")

    return "\n".join(lines)


_shared_engine = None


def _get_shared_engine(url: str):
    """Return a module-level cached engine to avoid pool exhaustion."""
    global _shared_engine
    if _shared_engine is None:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        _shared_engine = create_engine(url, poolclass=NullPool)
    return _shared_engine


def _load_db_rules(project_slug: str) -> list[dict]:
    """Load rules from dash_rules_db table (KPIs, calculations, metrics from auto-training)."""
    try:
        from sqlalchemy import text
        from db import db_url
        engine = _get_shared_engine(db_url)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT name, type, definition, source FROM public.dash_rules_db "
                "WHERE project_slug = :s ORDER BY type, name LIMIT 50"
            ), {"s": project_slug}).fetchall()
            return [{"name": r[0], "type": r[1], "definition": r[2], "source": r[3]} for r in rows]
    except Exception:
        return []
