"""Shared defensive utilities — single source of truth for type coercion,
schema lifecycle, column metadata, SSE emission. Import from here, never
reimplement inline.

Error-proofing roadmap (2026-05-25):
- safe_json     : JSON serialization that handles Decimal/UUID/datetime/numpy/bytes
- df_serialize  : DataFrame rows → JSON-safe dicts
- sse           : SSE event emitter w/ per-event try/except
- project_schemas: drop ALL schema variants for a project
- cascade       : auto-discover dash_* tables w/ project_slug → cascade delete
- column_classifier: free_text vs dimension (multi-byte aware)
- column_metadata: shared registry of column roles + lineage cols + constants
"""

from dash.utils.safe_json import safe_dumps, safe_loads  # noqa: F401
