"""Semantic union — aggregate all project sources into one catalog.

For each source in project:
  - list its tables + columns + row counts
  - tag with provider_id

Then suggest cross-source JOIN keys:
  - explicit FKs (from each source's FK metadata)
  - implicit by column name match (fuzzy: customer_id ↔ cust_id ↔ id_customer)
  - LLM-confirmed (Phase 2 — stub for now)

Inject into Analyst prompt: "DATA SOURCES UNIFIED" block listing
all sources, all tables, suggested JOINs.
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TableEntry:
    provider_id: str
    table_name: str
    full_address: str         # "provider_id.table_name"
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    dialect: str = ""
    source_type: str = ""     # 'sql' | 'file'
    primary_keys: list[str] = field(default_factory=list)
    foreign_keys: list[dict] = field(default_factory=list)


@dataclass
class JoinSuggestion:
    left_table: str          # full_address
    left_column: str
    right_table: str
    right_column: str
    confidence: float        # 0.0-1.0
    reason: str              # 'explicit_fk' | 'name_match' | 'fuzzy_match' | 'llm'


@dataclass
class UnifiedCatalog:
    project_slug: str
    tables: list[TableEntry] = field(default_factory=list)
    join_suggestions: list[JoinSuggestion] = field(default_factory=list)
    source_count: int = 0
    table_count: int = 0


# ── Build ─────────────────────────────────────────────────────────────

def build(project_slug: str) -> UnifiedCatalog:
    """Aggregate all project sources into one catalog + suggest joins."""
    catalog = UnifiedCatalog(project_slug=project_slug)

    # 1. Walk providers
    try:
        from dash.providers import get_registry
        registry = get_registry()
        providers = registry.list_for_project(project_slug)
    except Exception as e:
        logger.warning(f"semantic_union: registry unavailable: {e}")
        providers = []

    catalog.source_count = len(providers)

    for provider in providers:
        if getattr(provider, "degraded", False):
            continue
        try:
            schema = provider.schema_blob or {}
            tables_data = schema.get("tables", [])
            cols_data = schema.get("columns", {})
            fks_data = schema.get("fks", [])

            for tbl in tables_data:
                if isinstance(tbl, str):
                    t_name = tbl
                elif isinstance(tbl, dict):
                    t_name = tbl.get("name") or tbl.get("table_name") or ""
                else:
                    continue
                if not t_name:
                    continue

                # Get columns for this table
                col_list = []
                tbl_cols = cols_data.get(t_name, [])
                for c in tbl_cols:
                    if isinstance(c, str):
                        col_list.append(c)
                    elif isinstance(c, dict):
                        col_list.append(c.get("name") or c.get("column_name") or "")

                # FKs for this table
                tbl_fks = []
                for fk in fks_data:
                    if isinstance(fk, dict) and fk.get("from_table") == t_name:
                        tbl_fks.append(fk)

                catalog.tables.append(TableEntry(
                    provider_id=provider.id,
                    table_name=t_name,
                    full_address=f"{provider.id}.{t_name}",
                    columns=[c for c in col_list if c],
                    dialect=getattr(provider, "dialect", ""),
                    source_type="sql",
                    foreign_keys=tbl_fks,
                ))
        except Exception as e:
            logger.debug(f"semantic_union: provider {provider.id} failed: {e}")

    # 2. File sources
    try:
        from dash.providers.federation.file_source import discover
        file_catalog = discover(project_slug)
        for ft in file_catalog.tables:
            catalog.tables.append(TableEntry(
                provider_id=ft.doc_id if ft.doc_type != "source" else ft.doc_id,
                table_name=ft.table_name,
                full_address=ft.full_address,
                columns=ft.columns,
                row_count=ft.row_count,
                dialect="files",
                source_type="file",
            ))
    except Exception as e:
        logger.debug(f"semantic_union: file_source failed: {e}")

    catalog.table_count = len(catalog.tables)

    # 3. Suggest joins
    catalog.join_suggestions = _suggest_joins(catalog.tables)

    return catalog


# ── Join suggestion ───────────────────────────────────────────────────

def _suggest_joins(tables: list[TableEntry]) -> list[JoinSuggestion]:
    """Find candidate cross-source joins.

    1. Explicit FKs in schema_blob.
    2. Column name match across tables of different providers.
    3. Fuzzy name match (strip _id suffix, normalize).
    """
    suggestions: list[JoinSuggestion] = []
    seen: set[tuple] = set()

    # 1. Explicit FKs (when both sides happen to be in the catalog)
    addr_by_table: dict[str, list[TableEntry]] = {}
    for t in tables:
        addr_by_table.setdefault(t.table_name.lower(), []).append(t)

    for t in tables:
        for fk in t.foreign_keys:
            ref_table = (fk.get("to_table") or fk.get("ref_table") or "").lower()
            ref_col = fk.get("to_col") or fk.get("ref_col") or ""
            from_col = fk.get("from_col") or fk.get("col") or ""
            if not (ref_table and ref_col and from_col):
                continue
            for target in addr_by_table.get(ref_table, []):
                if target.provider_id == t.provider_id:
                    continue   # same source — not federation-relevant
                key = (t.full_address, from_col, target.full_address, ref_col)
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append(JoinSuggestion(
                    left_table=t.full_address, left_column=from_col,
                    right_table=target.full_address, right_column=ref_col,
                    confidence=0.95, reason="explicit_fk",
                ))

    # 2. Column name match (exact)
    by_col: dict[str, list[tuple]] = {}
    for t in tables:
        for c in t.columns:
            by_col.setdefault(c.lower(), []).append((t, c))

    for col_name, occurrences in by_col.items():
        if len(occurrences) < 2:
            continue
        # Pair up cross-source occurrences
        for i in range(len(occurrences)):
            for j in range(i + 1, len(occurrences)):
                t_a, c_a = occurrences[i]
                t_b, c_b = occurrences[j]
                if t_a.provider_id == t_b.provider_id:
                    continue
                key = tuple(sorted([
                    (t_a.full_address, c_a),
                    (t_b.full_address, c_b),
                ]))
                if key in seen:
                    continue
                seen.add(key)
                # Higher conf for *_id suffix names
                conf = 0.80 if col_name.endswith("_id") else 0.65
                # Skip very generic columns
                if col_name in ("id", "name", "type", "status", "date", "value", "code"):
                    conf -= 0.30
                if conf < 0.30:
                    continue
                suggestions.append(JoinSuggestion(
                    left_table=t_a.full_address, left_column=c_a,
                    right_table=t_b.full_address, right_column=c_b,
                    confidence=conf, reason="name_match",
                ))

    # 3. Fuzzy match (strip suffixes, compare normalized form)
    norm_to_cols: dict[str, list[tuple]] = {}
    for t in tables:
        for c in t.columns:
            n = _normalize(c)
            if not n or len(n) < 3:
                continue
            norm_to_cols.setdefault(n, []).append((t, c))

    for norm, occurrences in norm_to_cols.items():
        if len(occurrences) < 2:
            continue
        # Already-paired-as-exact-match check
        for i in range(len(occurrences)):
            for j in range(i + 1, len(occurrences)):
                t_a, c_a = occurrences[i]
                t_b, c_b = occurrences[j]
                if t_a.provider_id == t_b.provider_id:
                    continue
                if c_a.lower() == c_b.lower():
                    continue   # already covered by exact match
                key = tuple(sorted([
                    (t_a.full_address, c_a),
                    (t_b.full_address, c_b),
                ]))
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append(JoinSuggestion(
                    left_table=t_a.full_address, left_column=c_a,
                    right_table=t_b.full_address, right_column=c_b,
                    confidence=0.55, reason="fuzzy_match",
                ))

    # Sort by confidence desc + cap
    suggestions.sort(key=lambda s: s.confidence, reverse=True)
    return suggestions[:50]


def _normalize(name: str) -> str:
    """Normalize column name for fuzzy match: lowercase, strip _id, _key, _no, _num."""
    n = name.lower()
    for suffix in ("_id", "_key", "_code", "_no", "_num", "_number"):
        if n.endswith(suffix):
            n = n[:-len(suffix)]
            break
    n = re.sub(r"[^a-z0-9]", "", n)
    return n


# ── Render to prompt block ────────────────────────────────────────────

def render_for_analyst(project_slug: str, *, max_chars: int = 4000) -> str:
    """Build the DATA SOURCES UNIFIED prompt block. Capped to fit context budget."""
    catalog = build(project_slug)
    if catalog.source_count <= 1:
        return ""  # No federation needed for single-source projects

    lines = [f"## DATA SOURCES UNIFIED — {catalog.source_count} sources, {catalog.table_count} tables"]

    # Group by provider
    by_provider: dict[str, list[TableEntry]] = {}
    for t in catalog.tables:
        by_provider.setdefault(t.provider_id, []).append(t)

    for pid, tables in by_provider.items():
        lines.append(f"\n### {pid}  ({tables[0].dialect}, {tables[0].source_type})")
        for t in tables[:15]:
            cols_summary = ", ".join(t.columns[:8])
            if len(t.columns) > 8:
                cols_summary += f", ... ({len(t.columns)} cols)"
            lines.append(f"  {t.table_name}({cols_summary})")

    # Suggested JOINs
    if catalog.join_suggestions:
        lines.append(f"\n### SUGGESTED JOIN KEYS (top {min(15, len(catalog.join_suggestions))})")
        for s in catalog.join_suggestions[:15]:
            conf_label = "high" if s.confidence > 0.8 else "med" if s.confidence > 0.6 else "low"
            lines.append(
                f"  {s.left_table}.{s.left_column} = {s.right_table}.{s.right_column} "
                f"  [{conf_label} · {s.reason}]"
            )

    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n... [truncated]"
    return out
