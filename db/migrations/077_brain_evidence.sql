-- Migration 077: Brain Evidence Timeline (Build Provenance)
--
-- Append-only ledger linking every fact in dash_company_brain back to its
-- source material (chat sessions, uploaded docs, KG triples, memories, dream
-- insights, training runs, or manual admin edits). Powers the Brain Evidence
-- viewer + Time-Travel diff UI.
--
-- Lives in dash schema; brain table itself may live in `public` (older installs)
-- or `dash` (fresh). We do NOT enforce a foreign key on brain_id so evidence
-- rows survive brain entry deletes (audit-safety).
--
-- Idempotent (CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS dash;

CREATE TABLE IF NOT EXISTS dash.dash_brain_evidence (
    id                BIGSERIAL PRIMARY KEY,
    brain_id          BIGINT       NOT NULL,
        -- references dash_company_brain.id but FK intentionally omitted
        -- (brain rows may be deleted; evidence ledger must outlive them)
    source_type       TEXT         NOT NULL
        CHECK (source_type IN ('chat','doc','triple','memory','training','dream','manual')),
    source_id         TEXT,
        -- chat: message_id; doc: file path / doc id; triple: kg row id;
        -- memory: dash_memories.id; training: dash_training_runs.id;
        -- dream: dash_dream_findings.id; manual: NULL or admin note
    source_session_id TEXT,
        -- chat session id (when source_type='chat') — enables drill-back
    snippet           TEXT,
        -- short excerpt (≤2KB recommended) of the supporting text
    score             NUMERIC(4,3),
        -- 0.000–1.000 confidence/relevance, nullable
    metadata          JSONB        DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Hot path: list evidence for a brain entry, newest first
CREATE INDEX IF NOT EXISTS idx_brain_evidence_brain_recent
    ON dash.dash_brain_evidence (brain_id, created_at DESC);

-- Reverse lookup: find every brain entry sourced from a given chat/doc
CREATE INDEX IF NOT EXISTS idx_brain_evidence_source
    ON dash.dash_brain_evidence (source_type, source_id);

-- Optional: session drill-back
CREATE INDEX IF NOT EXISTS idx_brain_evidence_session
    ON dash.dash_brain_evidence (source_session_id)
    WHERE source_session_id IS NOT NULL;

-- ROLLBACK
-- DROP INDEX IF EXISTS dash.idx_brain_evidence_session;
-- DROP INDEX IF EXISTS dash.idx_brain_evidence_source;
-- DROP INDEX IF EXISTS dash.idx_brain_evidence_brain_recent;
-- DROP TABLE IF EXISTS dash.dash_brain_evidence;
