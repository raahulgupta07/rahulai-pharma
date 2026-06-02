"""
Context Loader Tool
===================

On-demand deep context for the Analyst agent.
Instead of loading ALL context into every prompt,
the agent calls load_context(topic) when it needs
deeper detail on a specific topic.

Inspired by the skill-loading pattern from
workshop-agentic-search (Leonie).
"""

import json
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from dash.paths import KNOWLEDGE_DIR
from db import db_url

log = logging.getLogger(__name__)
_engine = create_engine(db_url, poolclass=NullPool)

# Available topics and their descriptions (shown in agent prompt as menu)
CONTEXT_TOPICS = {
    "formulas": "All calculation formulas with SQL examples, column mapping, units, and edge cases",
    "aliases": "All entity aliases with exact column names and table locations where each appears",
    "thresholds": "All KPI targets and alert thresholds with flag SQL (CASE WHEN statements)",
    "patterns": "Proven SQL query patterns from past successful queries with JOIN strategies",
    "domain": "Full business glossary, industry terms, fiscal calendar, reporting standards",
    "quality": "Known data quality issues per table — NULL handling, duplicates, suspicious values",
    "relationships": "All table relationships — FK joins, AI-discovered joins, column overlap, cardinality",
    "documents": "Document summaries, key excerpts, slide/page references from uploaded files",
    "corrections": "Past query mistakes, what went wrong, what fixed them, success rates per strategy",
    "org": "Full company org structure — parent/subsidiaries, brand hierarchy, store lists per division",
}


def get_context_menu() -> str:
    """Return the skill menu text for injection into agent prompt."""
    lines = [
        "\n## Deep Context (on-demand)",
        "Call `load_context(topic)` for detailed information when the summary above isn't enough:",
    ]
    for topic, desc in CONTEXT_TOPICS.items():
        lines.append(f'  • "{topic}" — {desc}')
    lines.append("Only load what you need. For simple queries, the summary above is sufficient.")
    return "\n".join(lines)


def load_context(topic: str, project_slug: str = "") -> str:
    """Load deep context on a specific topic. Returns formatted text."""
    topic = topic.strip().lower()
    if topic not in CONTEXT_TOPICS:
        return f"Unknown topic '{topic}'. Available: {', '.join(CONTEXT_TOPICS.keys())}"

    try:
        loader = _LOADERS.get(topic)
        if loader:
            return loader(project_slug)
        return f"No loader for topic '{topic}'"
    except Exception as e:
        log.warning("Context loader failed for %s: %s", topic, e)
        return f"Failed to load {topic}: {str(e)[:100]}"


# ── Individual loaders ──────────────────────────────────────────────────────

def _load_formulas(slug: str) -> str:
    lines = ["DEEP CONTEXT: FORMULAS\n"]
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT name, definition, metadata FROM public.dash_company_brain "
            "WHERE category = 'formula' ORDER BY name"
        )).fetchall()
    if not rows:
        return "No formulas defined in Company Brain. Ask admin to add them."
    for r in rows:
        meta = r[2] if isinstance(r[2], dict) else json.loads(r[2]) if r[2] else {}
        lines.append(f"{r[0]}:")
        lines.append(f"  Formula: {meta.get('formula', r[1])}")
        lines.append(f"  Unit: {meta.get('unit', 'n/a')}")
        lines.append(f"  Description: {r[1]}")
        if meta.get('sql_example'):
            lines.append(f"  Example SQL: {meta['sql_example']}")
        lines.append("")
    # Add column mapping from schema
    if slug:
        lines.append("REVENUE/COST COLUMN MAPPING:")
        try:
            schema = slug.replace("-", "_").lower()[:63]
            with _engine.connect() as conn:
                cols = conn.execute(text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND (column_name LIKE '%%revenue%%' OR column_name LIKE '%%cost%%' "
                    "OR column_name LIKE '%%cogs%%' OR column_name LIKE '%%sales%%' OR column_name LIKE '%%amount%%' "
                    "OR column_name LIKE '%%profit%%' OR column_name LIKE '%%margin%%')"
                ), {"s": schema}).fetchall()
            for c in cols[:20]:
                lines.append(f"  {c[0]}.{c[1]}")
        except Exception:
            pass
    return "\n".join(lines)


def _load_aliases(slug: str) -> str:
    lines = ["DEEP CONTEXT: ENTITY ALIASES\n"]
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT name, definition, metadata FROM public.dash_company_brain "
            "WHERE category = 'alias' ORDER BY name"
        )).fetchall()
    if not rows:
        return "No aliases defined in Company Brain."
    for r in rows:
        meta = r[2] if isinstance(r[2], dict) else json.loads(r[2]) if r[2] else {}
        aliases = meta.get('aliases', [])
        lines.append(f"{r[0]}:")
        lines.append(f"  Aliases: {', '.join(aliases)}")
        lines.append(f"  Context: {r[1]}")
    # Add column locations from KG
    if slug:
        lines.append("\nWHERE ALIASES APPEAR IN DATA:")
        try:
            with _engine.connect() as conn:
                triples = conn.execute(text(
                    "SELECT DISTINCT subject, object FROM public.dash_knowledge_triples "
                    "WHERE project_slug = :s AND predicate = 'found_in_column' LIMIT 30"
                ), {"s": slug}).fetchall()
            for t in triples:
                lines.append(f"  '{t[0]}' → column {t[1]}")
        except Exception:
            pass
    return "\n".join(lines)


def _load_thresholds(slug: str) -> str:
    lines = ["DEEP CONTEXT: THRESHOLDS & TARGETS\n"]
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT name, definition, metadata FROM public.dash_company_brain "
            "WHERE category = 'threshold' ORDER BY name"
        )).fetchall()
    if not rows:
        return "No thresholds defined in Company Brain."
    for r in rows:
        meta = r[2] if isinstance(r[2], dict) else json.loads(r[2]) if r[2] else {}
        lines.append(f"{r[0]}:")
        lines.append(f"  Target: {meta.get('target', 'n/a')}")
        if meta.get('alert_below'):
            lines.append(f"  Alert if below: {meta['alert_below']} (flag as CRITICAL)")
        if meta.get('alert_above'):
            lines.append(f"  Alert if above: {meta['alert_above']} (flag as ALARM)")
        lines.append(f"  Context: {r[1]}")
        lines.append(f"  Flag SQL: CASE WHEN metric < {meta.get('alert_below', 'X')} THEN 'CRITICAL' "
                      f"WHEN metric < {meta.get('target', 'Y')} THEN 'BELOW TARGET' ELSE 'ON TARGET' END")
        lines.append("")
    return "\n".join(lines)


def _load_patterns(slug: str) -> str:
    lines = ["DEEP CONTEXT: PROVEN SQL PATTERNS\n"]
    if not slug:
        return "No project context — provide project_slug."
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT question, sql_query, tables_used, execution_time "
                "FROM public.dash_query_patterns WHERE project_slug = :s "
                "ORDER BY use_count DESC LIMIT 15"
            ), {"s": slug}).fetchall()
        if not rows:
            return "No proven query patterns yet. Ask some questions first."
        for i, r in enumerate(rows, 1):
            lines.append(f"Pattern {i}: {r[0]}")
            lines.append(f"  SQL: {r[1][:300]}")
            lines.append(f"  Tables: {r[2]}")
            if r[3]:
                lines.append(f"  Time: {r[3]}")
            lines.append("")
    except Exception as e:
        lines.append(f"  Error loading patterns: {str(e)[:80]}")
    return "\n".join(lines)


def _load_domain(slug: str) -> str:
    lines = ["DEEP CONTEXT: DOMAIN KNOWLEDGE\n"]
    with _engine.connect() as conn:
        # Glossary
        rows = conn.execute(text(
            "SELECT name, definition FROM public.dash_company_brain "
            "WHERE category = 'glossary' ORDER BY name"
        )).fetchall()
        if rows:
            lines.append("GLOSSARY:")
            for r in rows:
                lines.append(f"  {r[0]} = {r[1]}")
        # Calendar
        rows = conn.execute(text(
            "SELECT name, definition FROM public.dash_company_brain "
            "WHERE category = 'calendar' ORDER BY name"
        )).fetchall()
        if rows:
            lines.append("\nCALENDAR & REPORTING:")
            for r in rows:
                lines.append(f"  {r[0]}: {r[1]}")
        # Patterns/best practices
        rows = conn.execute(text(
            "SELECT name, definition FROM public.dash_company_brain "
            "WHERE category = 'pattern' ORDER BY name"
        )).fetchall()
        if rows:
            lines.append("\nBEST PRACTICES:")
            for r in rows:
                lines.append(f"  • {r[0]}: {r[1]}")
    return "\n".join(lines) if len(lines) > 1 else "No domain knowledge in Company Brain."


def _load_quality(slug: str) -> str:
    lines = ["DEEP CONTEXT: DATA QUALITY\n"]
    if not slug:
        return "No project context."
    try:
        schema = slug.replace("-", "_").lower()[:63]
        with _engine.connect() as conn:
            tables = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
            ), {"s": schema}).fetchall()
            for t in tables[:10]:
                tbl = t[0]
                null_info = conn.execute(text(
                    f'SELECT COUNT(*) as total, '
                    f'COUNT(*) - COUNT(*) FILTER (WHERE TRUE) as issues '
                    f'FROM "{schema}"."{tbl}"'
                )).fetchone()
                lines.append(f"{tbl}: {null_info[0]} rows")
                # Check NULL percentages per column
                cols = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ), {"s": schema, "t": tbl}).fetchall()
                for c in cols[:10]:
                    try:
                        null_pct = conn.execute(text(
                            f'SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE "{c[0]}" IS NULL) / NULLIF(COUNT(*), 0), 1) '
                            f'FROM "{schema}"."{tbl}"'
                        )).scalar()
                        if null_pct and float(null_pct) > 5:
                            lines.append(f"  ⚠ {c[0]}: {null_pct}% NULL")
                    except Exception:
                        pass
    except Exception as e:
        lines.append(f"  Error: {str(e)[:80]}")
    return "\n".join(lines) if len(lines) > 1 else "No quality issues detected."


def _load_relationships(slug: str) -> str:
    lines = ["DEEP CONTEXT: TABLE RELATIONSHIPS\n"]
    if not slug:
        return "No project context."
    try:
        with _engine.connect() as conn:
            # AI-discovered relationships
            rows = conn.execute(text(
                "SELECT table1, table2, join_column, join_type, confidence "
                "FROM public.dash_relationships WHERE project_slug = :s ORDER BY confidence DESC"
            ), {"s": slug}).fetchall()
            if rows:
                lines.append("DISCOVERED JOINS:")
                for r in rows:
                    lines.append(f"  {r[0]} ←→ {r[1]} ON {r[2]} ({r[3]}, {r[4]}% confidence)")
            # KG joins
            kg = conn.execute(text(
                "SELECT DISTINCT subject, object FROM public.dash_knowledge_triples "
                "WHERE project_slug = :s AND predicate = 'joins_with' LIMIT 20"
            ), {"s": slug}).fetchall()
            if kg:
                lines.append("\nSHARED COLUMN JOINS:")
                for r in kg:
                    lines.append(f"  {r[0]} ←→ {r[1]}")
    except Exception as e:
        lines.append(f"  Error: {str(e)[:80]}")
    return "\n".join(lines) if len(lines) > 1 else "No relationships discovered yet. Run TRAIN ALL."


def _load_documents(slug: str) -> str:
    lines = ["DEEP CONTEXT: DOCUMENT SUMMARIES\n"]
    if not slug:
        return "No project context."
    docs_dir = KNOWLEDGE_DIR / slug / "docs"
    if not docs_dir.exists():
        return "No documents uploaded."
    for f in sorted(docs_dir.iterdir())[:10]:
        if f.is_file():
            try:
                content = f.read_text(errors='ignore')[:1500]
                lines.append(f"Document: {f.name}")
                lines.append(f"  Preview: {content[:300]}...")
                lines.append("")
            except Exception:
                pass
    # Grounded facts
    facts_file = KNOWLEDGE_DIR / slug / "training" / "grounded_facts.json"
    if facts_file.exists():
        try:
            facts = json.loads(facts_file.read_text())
            if facts:
                lines.append("GROUNDED FACTS (source-verified):")
                for gf in facts[:15]:
                    tag = "✅" if gf.get("grounded", True) else "⚠️"
                    lines.append(f"  {tag} [{gf.get('type', 'fact').upper()}] {gf.get('text', '')}")
        except Exception:
            pass
    return "\n".join(lines) if len(lines) > 1 else "No documents found."


def _load_corrections(slug: str) -> str:
    lines = ["DEEP CONTEXT: PAST CORRECTIONS & STRATEGIES\n"]
    if not slug:
        return "No project context."
    try:
        with _engine.connect() as conn:
            # Meta-learnings (what strategies work)
            rows = conn.execute(text(
                "SELECT error_type, strategy, success_rate, occurrences "
                "FROM public.dash_meta_learnings WHERE project_slug = :s "
                "ORDER BY success_rate DESC LIMIT 10"
            ), {"s": slug}).fetchall()
            if rows:
                lines.append("SELF-CORRECTION STRATEGIES (by success rate):")
                for r in rows:
                    lines.append(f"  Error: {r[0]}")
                    lines.append(f"  Strategy: {r[1]}")
                    lines.append(f"  Success: {r[2]}% ({r[3]} occurrences)")
                    lines.append("")
            # Bad feedback (anti-patterns)
            bad = conn.execute(text(
                "SELECT question, feedback FROM public.dash_feedback "
                "WHERE project_slug = :s AND rating = 'down' ORDER BY created_at DESC LIMIT 5"
            ), {"s": slug}).fetchall()
            if bad:
                lines.append("AVOID THESE (user gave 👎):")
                for r in bad:
                    lines.append(f"  Q: {r[0][:80]}")
                    lines.append(f"  Issue: {r[1][:100]}")
                    lines.append("")
    except Exception as e:
        lines.append(f"  Error: {str(e)[:80]}")
    return "\n".join(lines) if len(lines) > 1 else "No corrections logged yet."


def _load_org(slug: str) -> str:
    lines = ["DEEP CONTEXT: ORGANIZATION STRUCTURE\n"]
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT name, definition, metadata FROM public.dash_company_brain "
            "WHERE category = 'org' ORDER BY name"
        )).fetchall()
    if not rows:
        return "No org structure in Company Brain."
    for r in rows:
        meta = r[2] if isinstance(r[2], dict) else json.loads(r[2]) if r[2] else {}
        parent = meta.get('parent', '')
        children = meta.get('children', [])
        node_type = meta.get('type', '')
        lines.append(f"{r[0]} ({node_type}):")
        if parent:
            lines.append(f"  Parent: {parent}")
        if children:
            lines.append(f"  Children: {', '.join(children)}")
        lines.append(f"  Description: {r[1]}")
        lines.append("")
    return "\n".join(lines)


# ── Loader registry ──────────────────────────────────────────────────────────

_LOADERS = {
    "formulas": _load_formulas,
    "aliases": _load_aliases,
    "thresholds": _load_thresholds,
    "patterns": _load_patterns,
    "domain": _load_domain,
    "quality": _load_quality,
    "relationships": _load_relationships,
    "documents": _load_documents,
    "corrections": _load_corrections,
    "org": _load_org,
}
