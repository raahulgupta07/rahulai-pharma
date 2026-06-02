"""Render column_classification.json as a compact Analyst prompt block.

Pure read-only — no DB, no LLM. Returns a string suitable to append to
the existing dialect_overlay() output for a provider.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path("knowledge")


def render_classification_overlay(
    project_slug: str,
    source_id: int,
    *,
    knowledge_dir: Path = KNOWLEDGE_DIR,
    max_tables: int = 12,
    max_cols_per_table: int = 25,
    char_budget: int = 6000,
) -> Optional[str]:
    """Read column_classification.json for a source and return a prompt block.

    Returns None if the file doesn't exist (classifier not run yet) or empty.
    """
    p = knowledge_dir / project_slug / f"source_{source_id}" / "column_classification.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        logger.warning(f"Failed to read classification for source {source_id}: {e}")
        return None
    if not data:
        return None

    lines = [f"## COLUMN INTELLIGENCE — source {source_id}", ""]
    pii_count = 0

    tables = list(data.items())[:max_tables]
    for tbl, cols in tables:
        if not isinstance(cols, dict):
            continue
        lines.append(f"Table: {tbl}")
        col_items = list(cols.items())[:max_cols_per_table]
        for col, c in col_items:
            if not isinstance(c, dict):
                continue
            role = c.get("role", "?")
            sem = c.get("semantic", "?")
            ndv = c.get("ndv_estimated", 0) or 0
            conf = c.get("confidence_overall", 0) or 0
            pii_flag = ""
            if c.get("pii"):
                pii_count += 1
                mask = c.get("masking_recommended") or "redact"
                pii_flag = f" [PII!! mask:{mask}]"

            extras = []
            try:
                if c.get("is_dimension") and ndv > 0 and ndv < 50:
                    extras.append(f"ndv={ndv}")
                elif ndv >= 50:
                    extras.append(f"ndv≈{_human_count(int(ndv))}")
            except Exception:
                pass
            if c.get("value_distribution") in ("long_tail", "bimodal", "monotonic"):
                extras.append(c["value_distribution"])
            if c.get("fk_candidate_for"):
                extras.append(f"fk→{c['fk_candidate_for']}")

            extra_str = " · ".join(extras)
            try:
                conf_f = float(conf)
            except Exception:
                conf_f = 0.0
            line = f"  {col:<24} {str(role):<10} · {str(sem):<22} {extra_str}{pii_flag} (conf {conf_f:.2f})"
            lines.append(line)
        lines.append("")

    if pii_count > 0:
        lines.append(f"⚠ {pii_count} PII columns detected — apply masking_recommended hint when surfacing.")
        lines.append("")

    lines.append("Rules for Analyst:")
    lines.append("- PII columns flagged [PII!!] must be hashed/redacted in output")
    lines.append("- Use ndv_estimated to decide GROUP BY vs aggregation strategy")
    lines.append("- Prefer dim columns for grouping; measures for SUM/AVG/MAX/MIN")
    lines.append("- Temporal columns drive WHERE filters and time-series logic")

    out = "\n".join(lines)
    if len(out) > char_budget:
        out = out[:char_budget] + "\n... [truncated to fit context budget]"
    return out


def _human_count(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1e9:.1f}B"
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.0f}K"
    return str(n)
