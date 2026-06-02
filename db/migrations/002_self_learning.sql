-- Self-learning migration — additive, backwards-compatible.
-- Adds hybrid (per-project + central) self-learning system: curiosity questions,
-- hypotheses, run tracking, promotion log, external facts cache, and reinforcement
-- counters on memories. project_slug NULL = central / cross-project pool.

-- 1. Curiosity questions
CREATE TABLE IF NOT EXISTS public.dash_curiosity_questions (
  id SERIAL PRIMARY KEY,
  project_slug TEXT,
  source_id INTEGER REFERENCES public.dash_data_sources(id) ON DELETE SET NULL,
  question TEXT NOT NULL,
  topic TEXT,
  reason TEXT,                      -- why curious: 'kg_hole'|'drift'|'failed_qa'|'thumbs_down'|'anomaly'|'underused_table'|'gap'|'user_request'|'cycle_followup'
  priority INTEGER DEFAULT 50,      -- 0-100, higher = more urgent
  status TEXT DEFAULT 'pending',    -- 'pending'|'researching'|'answered'|'failed'|'archived'
  cycle_num INTEGER,
  domain TEXT,                      -- 'retail'|'finance'|'hr'|...|'general'
  created_at TIMESTAMPTZ DEFAULT NOW(),
  answered_at TIMESTAMPTZ,
  metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_curiosity_project_status ON public.dash_curiosity_questions(project_slug, status, priority DESC);
CREATE INDEX IF NOT EXISTS idx_curiosity_topic ON public.dash_curiosity_questions(topic);
CREATE INDEX IF NOT EXISTS idx_curiosity_central ON public.dash_curiosity_questions(status, priority DESC) WHERE project_slug IS NULL;


-- 2. Hypotheses
CREATE TABLE IF NOT EXISTS public.dash_hypotheses (
  id SERIAL PRIMARY KEY,
  project_slug TEXT,
  source_id INTEGER REFERENCES public.dash_data_sources(id) ON DELETE SET NULL,
  question_id INTEGER REFERENCES public.dash_curiosity_questions(id) ON DELETE CASCADE,
  statement TEXT NOT NULL,
  hypothesis_type TEXT,             -- 'causal'|'correlation'|'rule'|'formula'|'threshold'|'definition'|'pattern'
  sources_consulted JSONB DEFAULT '[]',  -- list of {tier, source, url, confidence}
  triangulation_count INTEGER DEFAULT 0, -- # independent sources agreeing
  confidence NUMERIC(4,3) DEFAULT 0.500, -- 0..1
  verification_status TEXT DEFAULT 'pending', -- 'pending'|'verified'|'partial'|'failed'|'deprecated'
  verified_by TEXT,                  -- 'sql'|'cross_source'|'llm_review'|'user'
  verified_at TIMESTAMPTZ,
  failed_reason TEXT,
  citations JSONB DEFAULT '[]',
  promoted_to_central BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_hypotheses_project_status ON public.dash_hypotheses(project_slug, verification_status, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_hypotheses_question ON public.dash_hypotheses(question_id);
CREATE INDEX IF NOT EXISTS idx_hypotheses_promotable ON public.dash_hypotheses(verification_status, promoted_to_central) WHERE verification_status='verified' AND promoted_to_central=FALSE;


-- 3. Self-learning run tracker
CREATE TABLE IF NOT EXISTS public.dash_self_learning_runs (
  id SERIAL PRIMARY KEY,
  project_slug TEXT,                -- NULL = central run
  cycle_num INTEGER NOT NULL,
  status TEXT DEFAULT 'running',    -- 'running'|'completed'|'failed'|'partial'
  questions_generated INTEGER DEFAULT 0,
  questions_answered INTEGER DEFAULT 0,
  hypotheses_formed INTEGER DEFAULT 0,
  hypotheses_verified INTEGER DEFAULT 0,
  hypotheses_failed INTEGER DEFAULT 0,
  facts_consolidated INTEGER DEFAULT 0,
  facts_promoted INTEGER DEFAULT 0,
  cost_usd NUMERIC(10,4) DEFAULT 0,
  duration_seconds INTEGER,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  error TEXT,
  metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_self_learning_project ON public.dash_self_learning_runs(project_slug, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_self_learning_cycle ON public.dash_self_learning_runs(cycle_num DESC);


-- 4. Promotion log (project → central)
CREATE TABLE IF NOT EXISTS public.dash_promotion_log (
  id SERIAL PRIMARY KEY,
  source_project_slug TEXT,         -- the project that originated the fact
  hypothesis_id INTEGER REFERENCES public.dash_hypotheses(id) ON DELETE SET NULL,
  fact_text TEXT NOT NULL,
  fact_type TEXT,                   -- 'definition'|'formula'|'pattern'|'threshold'|'kg_triple'
  approval_method TEXT,             -- 'auto_llm'|'auto_triangulation'|'user'|'admin'
  pii_scrubbed BOOLEAN DEFAULT TRUE,
  triangulation_count INTEGER,
  approver TEXT,                    -- LLM model name or user_id
  approved_at TIMESTAMPTZ DEFAULT NOW(),
  rejection_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_promotion_source ON public.dash_promotion_log(source_project_slug);
CREATE INDEX IF NOT EXISTS idx_promotion_approved ON public.dash_promotion_log(approved_at DESC);


-- 5. External facts cache (web search, FRED, Wikipedia, etc.)
CREATE TABLE IF NOT EXISTS public.dash_external_facts (
  id SERIAL PRIMARY KEY,
  query_hash TEXT NOT NULL UNIQUE,    -- sha256 of normalized query+source
  source_type TEXT NOT NULL,         -- 'tavily'|'perplexity'|'brave'|'fred'|'census'|'wikipedia'|'alpha_vantage'|...
  query_text TEXT,
  result_json JSONB,
  result_summary TEXT,
  fetched_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ,
  cost_usd NUMERIC(8,5) DEFAULT 0,
  http_status INTEGER
);
CREATE INDEX IF NOT EXISTS idx_external_facts_hash ON public.dash_external_facts(query_hash);
CREATE INDEX IF NOT EXISTS idx_external_facts_source ON public.dash_external_facts(source_type, fetched_at DESC);


-- 6. Add per-project opt-out flag for central contribution
ALTER TABLE public.dash_projects
  ADD COLUMN IF NOT EXISTS contribute_to_central BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS receive_from_central BOOLEAN DEFAULT TRUE;


-- 7. Reinforcement counter on memories (for forgetting curve)
ALTER TABLE public.dash_memories
  ADD COLUMN IF NOT EXISTS citation_count INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_cited_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(4,3) DEFAULT 0.500,
  ADD COLUMN IF NOT EXISTS decay_resistant BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_memories_decay ON public.dash_memories(citation_count, last_cited_at) WHERE archived = FALSE OR archived IS NULL;


-- 8. Comments for self-documentation
COMMENT ON COLUMN public.dash_curiosity_questions.project_slug IS 'NULL = central / cross-project question pool';
COMMENT ON COLUMN public.dash_hypotheses.confidence IS '0-1; +0.05 on each chat citation; -0.02/day decay if unused';
COMMENT ON COLUMN public.dash_external_facts.expires_at IS 'cache TTL; web search 7d, FRED 30d, Wikipedia 90d';
COMMENT ON COLUMN public.dash_projects.contribute_to_central IS 'tenant opt-out from sharing PII-scrubbed generic facts';
COMMENT ON COLUMN public.dash_memories.decay_resistant IS 'core knowledge — bypasses forgetting curve';

-- ROLLBACK PLAN (manual):
--   DROP TABLE IF EXISTS dash_external_facts, dash_promotion_log,
--                        dash_self_learning_runs, dash_hypotheses,
--                        dash_curiosity_questions;
--   ALTER TABLE dash_projects DROP COLUMN IF EXISTS contribute_to_central, receive_from_central;
--   ALTER TABLE dash_memories DROP COLUMN IF EXISTS citation_count, last_cited_at, confidence_score, decay_resistant;
