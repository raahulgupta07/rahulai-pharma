-- 101_trim_agent_registry.sql
-- Scope trim (2026-05-20). Reconcile Agent OS registry with reality after the
-- Demo-OS fold + niche-vertical gating.
--
--  * 'extended' (Demo-OS: Docs/Helpdesk/Feedback/Approvals/Reasoner/Reporter/
--    Scheduler) — agents DELETED, tools folded into core agents. Remove rows.
--  * 'sim' / 'autosim' / 'investment' — features GATED opt-in (SIM_LAB_ENABLED,
--    INVESTMENT_VERTICAL_ENABLED). Not deleted; mark disabled so the OS hub
--    stops counting them as active. Re-enabling the env flag + re-registering
--    flips them back.
-- Idempotent: safe to re-run.

DELETE FROM public.dash_agent_registry
 WHERE category = 'extended';

UPDATE public.dash_agent_registry
   SET status = 'disabled'
 WHERE category IN ('sim', 'autosim', 'investment')
   AND status <> 'disabled';
