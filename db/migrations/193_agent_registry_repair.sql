-- 193_agent_registry_repair.sql
-- REPAIR: heal baseline-seed drift. db/baseline/migrations_seed.sql marked
-- 073_agent_os_link.sql as already-applied while db/baseline/schema.sql did
-- NOT carry 073's added columns — so on every install the runner SKIPPED 073,
-- leaving public.dash_agent_registry with only 6 columns and ZERO seeded agents.
-- Symptom: list_minions_with_stats / agents-registry 500 (UndefinedColumn:
-- status/handler_kind/cost_per_invocation) + empty Agents fleet.
-- This migration is NOT seeded, so it self-applies on existing + fresh DBs.
-- Body is the idempotent half of 073 (ADD COLUMN IF NOT EXISTS + ON CONFLICT
-- upsert) — safe to run anywhere, any number of times.
-- ── Defensive ALTERs ────────────────────────────────────────────────────
ALTER TABLE public.dash_agent_registry
  ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';
ALTER TABLE public.dash_agent_registry
  ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE public.dash_agent_registry
  ADD COLUMN IF NOT EXISTS handler_kind TEXT;
ALTER TABLE public.dash_agent_registry
  ADD COLUMN IF NOT EXISTS trigger_model TEXT;
ALTER TABLE public.dash_agent_registry
  ADD COLUMN IF NOT EXISTS llm_model TEXT;
ALTER TABLE public.dash_agent_registry
  ADD COLUMN IF NOT EXISTS cost_per_invocation NUMERIC(10,4);
ALTER TABLE public.dash_agent_registry
  ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE public.dash_agent_registry
  ADD COLUMN IF NOT EXISTS docs_url TEXT;

CREATE INDEX IF NOT EXISTS idx_registry_handler
  ON public.dash_agent_registry(handler_kind)
  WHERE handler_kind IS NOT NULL;

-- ── Seed all agents ─────────────────────────────────────────────────────
-- Uses agent_name as natural key. display_name carries the human label,
-- description carries the 1-2 sentence purpose blurb.

INSERT INTO public.dash_agent_registry
  (agent_name, display_name, category, status, description,
   handler_kind, trigger_model, llm_model, cost_per_invocation)
VALUES
  -- ── CORE (6) ─────────────────────────────────────────────────────────
  ('leader','Leader','core','active','Team orchestrator — routes questions to Analyst/Engineer/Researcher/DataSci based on intent. Stuck-agent detection.',NULL,'sync_chat','CHAT_MODEL',0.002),
  ('analyst','Analyst','core','active','SQL queries, 11 analysis types, forecasting, auto-visualization. 31 tools, 50K char context.',NULL,'sync_chat','CHAT_MODEL',0.003),
  ('engineer','Engineer','core','active','Views, dashboards, schema operations, table merge.',NULL,'sync_chat','CHAT_MODEL',0.002),
  ('researcher','Researcher','core','active','Document RAG specialist. Multi-signal retrieval (semantic + keyword + entity + cross-ref).',NULL,'sync_chat','CHAT_MODEL',0.002),
  ('data-scientist','Data Scientist','core','active','6 ML tools (predict, feature_importance, anomaly, classify, cluster, decompose).',NULL,'sync_chat','DEEP_MODEL',0.005),
  ('customer-strategist','Customer Strategist','core','active','Customer intelligence specialist — RFM, CLV, churn, NBO, campaigns. 9 tools.',NULL,'sync_chat','CHAT_MODEL',0.003),

  -- ── SPECIALISTS (10) ─────────────────────────────────────────────────
  ('comparator','Comparator','specialist','active','Triggered on compare/vs/versus/between.',NULL,'sync_chat','CHAT_MODEL',0.001),
  ('diagnostician','Diagnostician','specialist','active','Root cause analysis — triggered on why/root cause/diagnose.',NULL,'sync_chat','CHAT_MODEL',0.001),
  ('narrator','Narrator','specialist','active','Story/summary — triggered on tell me a story/summarize/narrative.',NULL,'sync_chat','CHAT_MODEL',0.001),
  ('validator','Validator','specialist','active','Data validation — triggered on validate/verify/check.',NULL,'sync_chat','CHAT_MODEL',0.001),
  ('planner','Planner','specialist','active','Strategy planner — triggered on plan/roadmap/strategy.',NULL,'sync_chat','CHAT_MODEL',0.001),
  ('trend-analyst','Trend Analyst','specialist','active','Time-series — triggered on trend/over time/growth.',NULL,'sync_chat','CHAT_MODEL',0.001),
  ('pareto-analyst','Pareto Analyst','specialist','active','80/20 concentration — triggered on top 20%/pareto.',NULL,'sync_chat','CHAT_MODEL',0.001),
  ('anomaly-detector','Anomaly Detector','specialist','active','Outlier detection — triggered on anomaly/outlier/spike.',NULL,'sync_chat','CHAT_MODEL',0.001),
  ('benchmarker','Benchmarker','specialist','active','Industry benchmarks — triggered on benchmark/peer comparison.',NULL,'sync_chat','CHAT_MODEL',0.001),
  ('prescriptor','Prescriptor','specialist','active','Actionable recommendations — triggered on what should/recommend.',NULL,'sync_chat','CHAT_MODEL',0.001),

  -- ── BACKGROUND (11) ──────────────────────────────────────────────────
  ('judge','Judge','background','active','Quality scoring 1-5 per chat. Writes dash_quality_scores.',NULL,'event_hook','LITE_MODEL',0.0003),
  ('rule-suggester','Rule Suggester','background','active','Extracts rules from conversations. Writes dash_suggested_rules.',NULL,'event_hook','LITE_MODEL',0.0003),
  ('proactive-insights','Proactive Insights','background','active','Detects anomalies after each chat. Writes dash_proactive_insights.',NULL,'event_hook','LITE_MODEL',0.0005),
  ('query-plan-extractor','Query Plan Extractor','background','active','Parses SQL plans → tables/joins/filters. Writes dash_query_plans.',NULL,'event_hook','none',0),
  ('meta-learner','Meta Learner','background','active','Tracks self-correction strategy success rates. Writes dash_meta_learnings.',NULL,'event_hook','LITE_MODEL',0.0003),
  ('auto-evolver','Auto Evolver','background','active','Every 20 chats, regenerates evolved instructions. Writes dash_evolved_instructions.',NULL,'event_hook','DEEP_MODEL',0.03),
  ('chat-triple-extractor','Chat Triple Extractor','background','active','Extracts SPO triples from Q&A. Writes dash_knowledge_triples.',NULL,'event_hook','LITE_MODEL',0.0005),
  ('auto-memory-promoter','Auto-Memory Promoter','background','active','Promotes factual observations to memories. Rule-based, no LLM.',NULL,'event_hook','none',0),
  ('user-preference-tracker','User Preference Tracker','background','active','Counter-merges user style/metric prefs. No LLM.',NULL,'event_hook','none',0),
  ('episodic-memory-extractor','Episodic Memory Extractor','background','active','Regex-detects user reactions. No LLM.',NULL,'event_hook','none',0),
  ('follow-up-suggester','Follow-up Suggester','background','active','KG-aware follow-up question generation.',NULL,'event_hook','LITE_MODEL',0.0003),

  -- ── UPLOAD (5) ───────────────────────────────────────────────────────
  ('conductor','Conductor','upload','active','Upload orchestrator. Sees all files, plans, assigns.',NULL,'event_hook','CHAT_MODEL',0.001),
  ('parser','Parser','upload','active','Excel/CSV/JSON: header detect, unpivot, split.',NULL,'event_hook','DEEP_MODEL',0.01),
  ('scanner','Scanner','upload','active','PDF/PPTX/DOCX/TXT: text + table + OCR + Vision.',NULL,'event_hook','CHAT_MODEL',0.005),
  ('vision-agent','Vision','upload','active','JPG/PNG/charts. Tesseract first, Vision LLM fallback.',NULL,'event_hook','CHAT_MODEL',0.005),
  ('inspector','Inspector','upload','active','Quality gate. Health score, dupes, retry trigger.',NULL,'event_hook','LITE_MODEL',0.0005),

  -- ── ROUTING (2) ──────────────────────────────────────────────────────
  ('smart-router','Smart Router','routing','active','2-tier routing for cross-project Dash Agent. Tier 1 keyword $0, Tier 2 Router Agent w/ 4 tools.',NULL,'sync_chat','LITE_MODEL',0.001),
  ('sim-router','Sim Router','routing','active','Detects what-if/imagine/simulate in chat, spawns sim inline.',NULL,'sync_chat','LITE_MODEL',0.0003),

  -- ── EXTENDED (7) ─────────────────────────────────────────────────────
  ('docs-agent','Docs','extended','active','Documentation lookup via llms.txt, web search, PDF parse.',NULL,'sync_chat','CHAT_MODEL',0.002),
  ('helpdesk-agent','Helpdesk','extended','active','PII scan, safe SQL gate, dangerous-op HITL.',NULL,'sync_chat','CHAT_MODEL',0.002),
  ('feedback-agent','Feedback','extended','active','Clarifying questions w/ [CLARIFY: a | b] tag.',NULL,'sync_chat','LITE_MODEL',0.0003),
  ('approvals-agent','Approvals','extended','active','Approval workflow, audit log, HITL pending.',NULL,'sync_chat','LITE_MODEL',0.0003),
  ('reasoner-agent','Reasoner','extended','active','Pure DEEP_MODEL reasoning. No tools.',NULL,'sync_chat','DEEP_MODEL',0.05),
  ('reporter-agent','Reporter','extended','active','PDF/PPTX/CSV/calculator. Falls back to reportlab.',NULL,'sync_chat','CHAT_MODEL',0.02),
  ('scheduler-agent','Scheduler','extended','active','Cron-style schedule CRUD. Validates cron expressions.',NULL,'sync_chat','LITE_MODEL',0.0003),

  -- ── VISUALIZER ───────────────────────────────────────────────────────
  ('visualizer','Visualizer','tool','active','auto_visualize tool. 8 chart types. Rules first, LLM fallback.',NULL,'sync_chat','LITE_MODEL',0.0001),

  -- ── LEARNING CYCLE (9) ───────────────────────────────────────────────
  ('curiosity-engine','Curiosity Engine','learning','active','Generates N=20 questions from gap analysis (10 sources).',NULL,'cron','LITE_MODEL',0.005),
  ('researcher-loop','Researcher Loop','learning','active','7 parallel retrieval tiers w/ triangulation count.',NULL,'cron','CHAT_MODEL',0.02),
  ('hypothesis-engine','Hypothesis Engine','learning','active','Forms testable hypotheses from research dossier.',NULL,'cron','DEEP_MODEL',0.01),
  ('verifier','Verifier','learning','active','Verifies hypotheses against live DB w/ 110s timeout.',NULL,'cron','none',0),
  ('consolidator','Consolidator','learning','active','Routes winners to Memory/KG/Brain/Rules.',NULL,'cron','LITE_MODEL',0.001),
  ('forgetter','Forgetting Module','learning','active','Daily Ebbinghaus decay (-0.02/day).',NULL,'cron','none',0),
  ('promoter','Promotion Gate','learning','active','PII scrub + LLM gate before central Brain promotion.',NULL,'cron','LITE_MODEL',0.001),
  ('digester','Daily Digest','learning','active','Composes daily learning digest, optional Slack.',NULL,'cron','CHAT_MODEL',0.002),
  ('cost-guard','Cost Guard','learning','active','Per-project daily LLM cost cap.',NULL,'event_hook','none',0),

  -- ── DREAM REFLECTION (9) ─────────────────────────────────────────────
  ('reflect-sessions','Reflect Sessions','dream','active','Tier 3 nightly: pulls 50 sessions, DEEP synthesis, 15 findings max, auto-promote ≥0.85.','reflect_sessions','minion_queue','DEEP_MODEL',0.13),
  ('poignancy-capture','Poignancy Capture','dream','active','Tier 1 per-turn rule-based scoring 1-10 into dash_episode_buffer.','poignancy_capture','event_hook','none',0),
  ('dream-lite','Dream Lite','dream','active','Tier 2 between-turn: persona update + anticipated-query queue. Fires when Σ poignancy ≥30.','dream_lite','minion_queue','LITE_MODEL',0.005),
  ('precompute-queries','Precompute Queries','dream','active','Sleep-time compute: executes pending anticipated SQL into 4h TTL cache.','precompute_queries','minion_queue','DEEP_MODEL',0.03),
  ('ab-revert-check','A/B Revert Check','dream','active','Daily check: anti-patterns/skills/insights scored 7d before/after; auto-revert on regression ≥0.5.','ab_revert_check','minion_queue','none',0),
  ('bi-temporal-reconcile','Bi-Temporal Reconciler','dream','active','Graphiti pattern: invalidate stale brain/KG facts via expired_at + superseded_by.',NULL,'event_hook','LITE_MODEL',0.015),
  ('skill-library-promoter','Skill Library Promoter','dream','active','Voyager pattern: promotes proven query patterns (≥3 uses, judge ≥4) to dash_skill_library w/ NL descriptions + embeddings.',NULL,'event_hook','LITE_MODEL',0.006),
  ('reflection-tree','Reflection Tree','dream','active','Generative Agents pattern: depth 2 abstraction over findings.',NULL,'event_hook','DEEP_MODEL',0.06),
  ('wiki-digest','Wiki Digest','dream','active','Devin pattern: human-readable markdown per nightly run.',NULL,'event_hook','none',0),

  -- ── AUTOSIM (7) ──────────────────────────────────────────────────────
  ('sim-run','Sim Runner','autosim','active','Executes a single sim via dash.sim.orchestrator pipeline. Source-tagged.','sim_run','minion_queue','CHAT_MODEL',0.09),
  ('autosim-generate-grounded','Grounded Generator','autosim','active','W2: schema-aware scenario gen using persona+brain+dimensions+drift.','autosim_generate_grounded','minion_queue','DEEP_MODEL',0.03),
  ('autosim-morning-brief','Morning Brief','autosim','active','W5: daily 08:00 UTC digest of overnight sim findings.','autosim_morning_brief','minion_queue','none',0),
  ('autosim-comparison-run','Comparison Runner','autosim','active','W6.H: spawns 3 sim variants (optimistic/baseline/pessimistic) per base scenario.','autosim_comparison_run','minion_queue','CHAT_MODEL',0.27),
  ('autosim-marketplace-aggregate','Marketplace Aggregator','autosim','active','W6.J: daily anonymized popularity rollup, k-anonymity ≥5.','autosim_marketplace_aggregate','minion_queue','none',0),
  ('autosim-drift-hook','Drift Hook','autosim','active','W6.G: spawns sim on high-severity drift events. Rate-limited per (project, table) per 24h.',NULL,'event_hook','LITE_MODEL',0.0005),
  ('autosim-slack-bot','Slack Bot','autosim','active','W6.I: /sim slash command handler. HMAC-verified, posts result to thread.',NULL,'event_hook','LITE_MODEL',0.0005),

  -- ── SIM LAB (5) ──────────────────────────────────────────────────────
  ('sim-ontology','Ontology Agent','sim','active','Step 1: extract entity_types + relation_types from scenario.',NULL,'event_hook','DEEP_MODEL',0.01),
  ('sim-graph-builder','Graph Builder','sim','active','Step 2: chunk scenario + docs, SPO triples → ECharts graph.',NULL,'event_hook','LITE_MODEL',0.03),
  ('sim-env-setup','Environment Setup','sim','active','Step 3: persona-per-node generation.',NULL,'event_hook','LITE_MODEL',0.001),
  ('sim-simulator','Simulator','sim','active','Step 4: N personas react across horizon, asyncio.gather.',NULL,'event_hook','LITE_MODEL',0.012),
  ('sim-reporter','Sim Reporter','sim','active','Step 5: DEEP exec summary markdown.',NULL,'event_hook','DEEP_MODEL',0.015),

  -- ── JANITOR (4) ──────────────────────────────────────────────────────
  ('dedupe-entities','Dedupe Entities','janitor','active','Merges duplicate KG entities by normalized name.','dedupe_entities','minion_queue','none',0),
  ('recompile-stale-pages','Recompile Stale Pages','janitor','active','Rebuilds pages where new evidence accumulated.','recompile_stale_pages','minion_queue','none',0),
  ('reembed-stale-chunks','Reembed Stale Chunks','janitor','active','Re-embeds dash_vectors when EMBEDDING_MODEL changes.','reembed_stale_chunks','minion_queue','none',0),
  ('prune-old-evidence','Prune Old Evidence','janitor','active','Deletes evidence >180 days, keeps 10 most recent per page.','prune_old_evidence','minion_queue','none',0)
ON CONFLICT (agent_name) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  category     = EXCLUDED.category,
  status       = EXCLUDED.status,
  description  = EXCLUDED.description,
  handler_kind = EXCLUDED.handler_kind,
  trigger_model= EXCLUDED.trigger_model,
  llm_model    = EXCLUDED.llm_model,
  cost_per_invocation = EXCLUDED.cost_per_invocation;

-- ── Add 2 missing AutoSim cron schedules ────────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema='dash'
               AND table_name='dash_agent_schedules'
               AND column_name='agent_target') THEN
    INSERT INTO dash.dash_agent_schedules
      (id, project_slug, created_by, created_by_agent, name, description,
       schedule_kind, cron_expr, interval_seconds, next_run_at,
       prompt, agent_target, enabled, max_runs)
    VALUES
      ('sch_morning_brief001', NULL, 0, 'system',
       'AutoSim morning brief daily',
       'Compose plain-English digest of overnight dream + autosim activity per project + user. Optional Slack post.',
       'cron', '0 8 * * *', NULL, now() + INTERVAL '1 day',
       'Run the autosim morning brief cycle: enqueue autosim_morning_brief minion with all_projects=true.',
       'leader', true, NULL),
      ('sch_marketplace001', NULL, 0, 'system',
       'AutoSim marketplace daily aggregate',
       'Anonymized popularity rollup across all tenants. k-anonymity ≥5.',
       'cron', '0 6 * * *', NULL, now() + INTERVAL '1 day',
       'Run the autosim marketplace aggregate cycle: enqueue autosim_marketplace_aggregate minion.',
       'leader', true, NULL)
    ON CONFLICT (id) DO NOTHING;
  END IF;
END $$;

-- ── ROLLBACK reference (commented) ──────────────────────────────────────
-- To undo this migration manually:
--   DELETE FROM public.dash_agent_registry WHERE agent_name IN (
--     'leader','analyst','engineer','researcher','data-scientist','customer-strategist',
--     'comparator','diagnostician','narrator','validator','planner','trend-analyst',
--     'pareto-analyst','anomaly-detector','benchmarker','prescriptor',
--     'judge','rule-suggester','proactive-insights','query-plan-extractor','meta-learner',
--     'auto-evolver','chat-triple-extractor','auto-memory-promoter','user-preference-tracker',
--     'episodic-memory-extractor','follow-up-suggester',
--     'conductor','parser','scanner','vision-agent','inspector',
--     'smart-router','sim-router',
--     'docs-agent','helpdesk-agent','feedback-agent','approvals-agent','reasoner-agent',
--     'reporter-agent','scheduler-agent','visualizer',
--     'curiosity-engine','researcher-loop','hypothesis-engine','verifier','consolidator',
--     'forgetter','promoter','digester','cost-guard',
--     'reflect-sessions','poignancy-capture','dream-lite','precompute-queries','ab-revert-check',
--     'bi-temporal-reconcile','skill-library-promoter','reflection-tree','wiki-digest',
--     'sim-run','autosim-generate-grounded','autosim-morning-brief','autosim-comparison-run',
--     'autosim-marketplace-aggregate','autosim-drift-hook','autosim-slack-bot',
--     'sim-ontology','sim-graph-builder','sim-env-setup','sim-simulator','sim-reporter',
--     'dedupe-entities','recompile-stale-pages','reembed-stale-chunks','prune-old-evidence'
--   );
--   DELETE FROM dash.dash_agent_schedules WHERE id IN
--     ('sch_morning_brief001','sch_marketplace001');
