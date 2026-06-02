"""Load table metadata for the system prompt."""

import json
from pathlib import Path
from typing import Any

from agno.utils.log import logger

from dash.paths import TABLES_DIR

MAX_QUALITY_NOTES = 5


def load_table_metadata(tables_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load table metadata from JSON files."""
    if tables_dir is None:
        tables_dir = TABLES_DIR

    tables: list[dict[str, Any]] = []
    if not tables_dir.exists():
        return tables

    for filepath in sorted(tables_dir.glob("*.json")):
        try:
            with open(filepath) as f:
                table = json.load(f)
            tables.append(
                {
                    "table_name": table["table_name"],
                    "description": table.get("table_description", ""),
                    "columns": table.get("table_columns", []),
                    "use_cases": table.get("use_cases", []),
                    "data_quality_notes": table.get("data_quality_notes", [])[:MAX_QUALITY_NOTES],
                    # Codex-enriched knowledge fields
                    "table_purpose": table.get("table_purpose", ""),
                    "grain": table.get("grain", ""),
                    "primary_keys": table.get("primary_keys", []),
                    "foreign_keys": table.get("foreign_keys", []),
                    "usage_patterns": table.get("usage_patterns", []),
                    "alternate_tables": table.get("alternate_tables", ""),
                    "freshness": table.get("freshness", ""),
                }
            )
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error(f"Failed to load {filepath}: {e}")

    return tables


def build_semantic_model(tables_dir: Path | None = None) -> dict[str, Any]:
    """Build semantic model from table metadata."""
    return {"tables": load_table_metadata(tables_dir)}


def format_semantic_model(model: dict[str, Any]) -> str:
    """Format semantic model for system prompt."""
    lines: list[str] = []

    for table in model.get("tables", []):
        lines.append(f"### {table['table_name']}")
        if table.get("description"):
            lines.append(table["description"])
        if table.get("table_purpose"):
            lines.append(f"**Purpose:** {table['table_purpose']}")
        if table.get("grain"):
            lines.append(f"**Grain:** {table['grain']}")
        if table.get("primary_keys"):
            lines.append(f"**Primary keys:** {', '.join(table['primary_keys'])}")
        if table.get("foreign_keys"):
            fk_parts = []
            for fk in table["foreign_keys"]:
                if isinstance(fk, dict):
                    fk_parts.append(f"`{fk.get('column', '')}` → `{fk.get('references', '')}` ({fk.get('relationship', '')})")
                else:
                    fk_parts.append(str(fk))
            lines.append(f"**Foreign keys:** {'; '.join(fk_parts)}")
        if table.get("columns"):
            lines.append("**Columns:**")
            for col in table["columns"]:
                col_type = col.get("type", "")
                col_desc = col.get("description", "")
                lines.append(f"  - `{col['name']}` ({col_type}) — {col_desc}")
        if table.get("usage_patterns"):
            lines.append("**Usage patterns:**")
            for p in table["usage_patterns"]:
                lines.append(f"  - {p}")
        if table.get("alternate_tables"):
            lines.append(f"**Alternate tables:** {table['alternate_tables']}")
        if table.get("freshness"):
            lines.append(f"**Freshness:** {table['freshness']}")
        if table.get("use_cases"):
            lines.append(f"**Use cases:** {', '.join(table['use_cases'])}")
        if table.get("data_quality_notes"):
            lines.append("**Data quality:**")
            for note in table["data_quality_notes"]:
                lines.append(f"  - {note}")
        # Dimension values — exact values for WHERE clauses
        if table.get("dimensions"):
            lines.append("**DIMENSION VALUES (use exact case in WHERE):**")
            for col, vals in table["dimensions"].items():
                if isinstance(vals, list) and vals:
                    val_str = ", ".join(f"{v['value']}({v['pct']}%)" if isinstance(v, dict) else str(v) for v in vals[:15])
                    lines.append(f"  - `{col}`: {val_str}")
        # Column profiles — type classification
        if table.get("column_profiles"):
            dim_cols = [n for n, p in table["column_profiles"].items() if p.get("classification") == "dimension"]
            meas_cols = [n for n, p in table["column_profiles"].items() if p.get("classification") == "measure"]
            if dim_cols:
                lines.append(f"**Dimensions (GROUP BY):** {', '.join(dim_cols)}")
            if meas_cols:
                lines.append(f"**Measures (SUM/AVG):** {', '.join(meas_cols)}")
        # Hierarchies
        if table.get("hierarchies"):
            hier_strs = [f"{h['parent']}({h['parent_count']}) → {h['child']}({h['child_count']})" for h in table["hierarchies"]]
            lines.append(f"**Hierarchies (drill-down):** {', '.join(hier_strs)}")
        lines.append("")

    return "\n".join(lines).rstrip()
