"""Deterministic zero-LLM entity linker for Dash knowledge graph.

Public API:
- extract_entities(text, page_kind=None) -> list[dict]
- extract_links(text, page_kind=None, source_id=None) -> list[dict]
- link_text(project, text, page_kind=None, source_ref=None) -> dict
- link_page_content(project, page_id, text, page_kind)
- relink_all(project, limit=1000)
"""
from dash.linker.extractor import (  # noqa: F401
    extract_entities,
    extract_links,
    link_text,
    normalize_name,
)
from dash.linker.runner import link_page_content, relink_all  # noqa: F401
