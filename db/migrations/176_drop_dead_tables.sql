-- 176_drop_dead_tables.sql — remove vestigial tables with ZERO live code paths.
-- Idempotent (DROP ... IF EXISTS) + fail-soft. Reversible: delete this file +
-- re-run baseline to restore (all targets are still CREATEd in db/baseline/schema.sql,
-- so a fresh DB creates-then-drops them here — harmless, no data lost).
--
-- METHOD (2026-06-08 audit): cross-referenced all 229 live tables against every
-- table-name reference in app/ + dash/ + frontend + scripts. Only the tables below
-- had NO reference on any live request path. Agno-framework-managed `ai.*` tables
-- (agno_*, citypharma_knowledge/_contents, citypharma_learnings, dash_knowledge/_learnings)
-- are EXCLUDED on purpose — their names are built dynamically by the Agno lib /
-- create_project_knowledge(slug), so grep shows 0 hits but they are LIVE. Do NOT add them.
--
-- VERIFIED no FK / no view depends on any target (pg_constraint + pg_depend clean).

-- Agent-OS builder subsystem (UI + router pruned 2026-06; seed rows only, no code reads).
DROP TABLE IF EXISTS dash.aos_capabilities  CASCADE;
DROP TABLE IF EXISTS dash.aos_cost_guard    CASCADE;
DROP TABLE IF EXISTS dash.aos_kill_switch   CASCADE;
DROP TABLE IF EXISTS dash.aos_models        CASCADE;
DROP TABLE IF EXISTS dash.aos_tool_registry CASCADE;

-- Brainbench (corpus benchmarking) — router pruned; only orphan offline script
-- scripts/brainbench_regression.{sh,py} names it, and that script's target endpoint
-- /api/projects/{slug}/brainbench/runs no longer exists. Dead in the product.
DROP TABLE IF EXISTS dash.dash_brainbench_corpus CASCADE;

-- Journal — router pruned; only orphan script scripts/daily_journal.py writes it,
-- no live reader. 0 rows.
DROP TABLE IF EXISTS public.dash_journal CASCADE;

-- Stray Agno orphan: ai.knowledge_citypharma (mis-named twin of citypharma_knowledge).
-- 0 rows, no creator, no reader, not in any migration DDL. Live store is citypharma_knowledge.
DROP TABLE IF EXISTS ai.knowledge_citypharma CASCADE;
