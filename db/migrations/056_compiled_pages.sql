-- Build B1: compiled-truth + evidence-trail page model.
-- Replaces last-write-wins memory_state with append-only evidence
-- plus an LLM-summarised compiled-truth block per page.

CREATE TABLE IF NOT EXISTS dash.dash_pages (
  id              BIGSERIAL PRIMARY KEY,
  project_slug    TEXT NOT NULL,
  page_key        TEXT NOT NULL,
  title           TEXT,
  compiled_truth  TEXT,
  compiled_at     TIMESTAMPTZ,
  compiled_by     TEXT,
  content_hash    TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_slug, page_key)
);

CREATE INDEX IF NOT EXISTS idx_dash_pages_project_key
  ON dash.dash_pages (project_slug, page_key);

CREATE TABLE IF NOT EXISTS dash.dash_page_evidence (
  id          BIGSERIAL PRIMARY KEY,
  page_id     BIGINT NOT NULL REFERENCES dash.dash_pages(id) ON DELETE CASCADE,
  ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
  source      TEXT,         -- chat | workflow | user | import | automl
  source_ref  TEXT,
  content     TEXT NOT NULL,
  author      TEXT
);

CREATE INDEX IF NOT EXISTS idx_dash_page_evidence_page_ts
  ON dash.dash_page_evidence (page_id, ts DESC);
