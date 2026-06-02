-- Refusal marks — single source of truth for "was this turn refused?"
-- Replaces brittle text-sentinel matching in extract_context. Sites that
-- decide to refuse (scope classifier, agent scope-guardrail self-refusal,
-- leader stuck-loop, etc.) INSERT a row here. Background tasks (memory
-- promoter, judge, KG extractor, prefs tracker) check via was_refused()
-- before processing the turn.
CREATE TABLE IF NOT EXISTS dash.dash_refusal_marks (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL,
  question_hash TEXT NOT NULL,         -- sha1 of normalized question
  question_preview TEXT,               -- first 200 chars for audit
  refused_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source TEXT NOT NULL,                -- scope_classifier | agent_self | stuck_loop | text_sentinel
  reason TEXT                          -- off_topic | denied_intent | low_confidence | …
);
CREATE INDEX IF NOT EXISTS idx_refusal_marks_session ON dash.dash_refusal_marks (session_id, refused_at DESC);
CREATE INDEX IF NOT EXISTS idx_refusal_marks_qhash ON dash.dash_refusal_marks (session_id, question_hash);
