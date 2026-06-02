-- 082_investment_agents.sql
-- Seed 7 investment agents into public.dash_agent_registry so OS Hub /ui/os
-- shows them and the Fleet view has a 7th "Investment" collapsible section.
--
-- Natural key on this table is agent_name (UNIQUE). We use deterministic
-- inv_* names so the rows are idempotent across re-runs.

INSERT INTO public.dash_agent_registry
  (agent_name, display_name, category, status, description,
   handler_kind, trigger_model, llm_model, cost_per_invocation)
VALUES
  ('inv_market_analyst', 'Market Analyst', 'investment', 'ready',
   'Macro + market view. Pulls live quotes (yfinance) and headlines (exa_news).',
   NULL, 'sync_chat', 'CHAT_MODEL', 0.02),
  ('inv_financial_analyst', 'Financial Analyst', 'investment', 'ready',
   'Fundamentals: ratios, statements, valuation from yfinance.',
   NULL, 'sync_chat', 'CHAT_MODEL', 0.02),
  ('inv_technical_analyst', 'Technical Analyst', 'investment', 'ready',
   'Price-action + indicators (RSI/MACD/MA) via compute_technicals.',
   NULL, 'sync_chat', 'CHAT_MODEL', 0.02),
  ('inv_risk_officer', 'Risk Officer', 'investment', 'ready',
   'Risk + compliance gate. yfinance volatility + compliance_check.',
   NULL, 'sync_chat', 'CHAT_MODEL', 0.015),
  ('inv_knowledge_agent', 'Knowledge Agent (Investment)', 'investment', 'ready',
   'Investment-team Knowledge Agent. Recall + brain search over IC memory.',
   NULL, 'sync_chat', 'CHAT_MODEL', 0.01),
  ('inv_memo_writer', 'Memo Writer', 'investment', 'ready',
   'Drafts IC memos. save_memo + list_memos for archive.',
   NULL, 'sync_chat', 'CHAT_MODEL', 0.01),
  ('inv_committee_chair', 'Committee Chair', 'investment', 'ready',
   'IC chair. Synthesizes team outputs into decision (DEEP_MODEL).',
   NULL, 'sync_chat', 'DEEP_MODEL', 0.05)
ON CONFLICT (agent_name) DO NOTHING;

-- Per-agent tools/target tagging (only if tags column exists)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema='public'
               AND table_name='dash_agent_registry'
               AND column_name='tags') THEN
    UPDATE public.dash_agent_registry SET tags = CAST('{"agent_target":"investment_team","tools":["yfinance","exa_news"]}' AS jsonb) WHERE agent_name='inv_market_analyst';
    UPDATE public.dash_agent_registry SET tags = CAST('{"agent_target":"investment_team","tools":["yfinance"]}' AS jsonb) WHERE agent_name='inv_financial_analyst';
    UPDATE public.dash_agent_registry SET tags = CAST('{"agent_target":"investment_team","tools":["yfinance","compute_technicals"]}' AS jsonb) WHERE agent_name='inv_technical_analyst';
    UPDATE public.dash_agent_registry SET tags = CAST('{"agent_target":"investment_team","tools":["yfinance","compliance_check"]}' AS jsonb) WHERE agent_name='inv_risk_officer';
    UPDATE public.dash_agent_registry SET tags = CAST('{"agent_target":"investment_team","tools":["recall","search_brain"]}' AS jsonb) WHERE agent_name='inv_knowledge_agent';
    UPDATE public.dash_agent_registry SET tags = CAST('{"agent_target":"investment_team","tools":["save_memo","list_memos"]}' AS jsonb) WHERE agent_name='inv_memo_writer';
    UPDATE public.dash_agent_registry SET tags = CAST('{"agent_target":"investment_team","tools":["none"]}' AS jsonb) WHERE agent_name='inv_committee_chair';
  END IF;
END $$;

-- ── ROLLBACK reference ─────────────────────────────────────────────────
-- DELETE FROM public.dash_agent_registry WHERE agent_name LIKE 'inv_%';
