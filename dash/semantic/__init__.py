"""Semantic layer — WrenAI-style MDL (Modeling Definition Language).

Lifts WrenAI's core idea: separate logical model (clean names + virtual cols
+ relationships) from raw schema. LLM sees logical model, code compiles to
raw SQL via sqlglot AST rewrite.

Storage: extends existing `dash_metric_definitions` (migration 134), not a
new table. Each metric_def can declare model_name + raw_table_ref +
virtual_columns + relationships JSONB fields.

Two public entrypoints:

    from dash.semantic import compile_query, models_for_prompt

    raw_sql = compile_query(slug, "SELECT was_successful FROM customer_calls")
    prompt_ctx = models_for_prompt(slug)  # short text for LLM context
"""

from dash.semantic.compile import (
    compile_query,
    models_for_prompt,
    load_models,
    invalidate,
    detect_cycles,
)

__all__ = [
    "compile_query",
    "models_for_prompt",
    "load_models",
    "invalidate",
    "detect_cycles",
]
