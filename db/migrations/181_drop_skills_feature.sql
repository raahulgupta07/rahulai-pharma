-- 181: remove the Skills (marketplace/library/builtins) feature — single-tenant
-- pharma never used it (0 invocations/bindings, injection gated off via
-- EXPERIMENTAL_AGI, builtins were deck/chart generators). Code side removed:
-- unmounted skills_api/resolver_api/skill_drafts_api routers + skill_marketplace
-- router, deleted those 3 api modules + resolver route, register_builtins boot
-- block gone, instructions._skill_layer14 now no-op. registry.py kept (fail-soft,
-- still imported by dashboards/agent + skill_refinery_cycle).
-- KEPT: public.dash_dashboard_skill_runs + public.dash_skill_overrides (these are
-- the DASHBOARD-GENERATION pipeline, raw INSERT in dashboards/agent.py — not the
-- skills marketplace). KEPT: dash.dash_tool_utility_scores / public.dash_tool_scores
-- (skill_refinery.py = tool-utility scoring, misnomer, live chat-path scoping).
BEGIN;
DROP TABLE IF EXISTS dash.dash_skill_bindings CASCADE;
DROP TABLE IF EXISTS dash.dash_skill_invocations CASCADE;
DROP TABLE IF EXISTS dash.dash_skill_drafts CASCADE;
DROP TABLE IF EXISTS dash.dash_skill_marketplace CASCADE;
DROP TABLE IF EXISTS dash.dash_skill_audit_log CASCADE;
DROP TABLE IF EXISTS dash.dash_skills CASCADE;
COMMIT;
