-- 150_dash_links.sql
-- Bidirectional artifact links (Obsidian-style) between Dash artifacts:
-- chats, charts, dashboards, decks, skills, metrics, tables, rules, etc.
--
-- Source artifact (src_type, src_id) links to destination artifact
-- (dst_type, dst_id) via a relation kind (`rel`), within a project scope.
--
-- Idempotent. Uses public schema (platform metadata).

CREATE TABLE IF NOT EXISTS dash.dash_links (
    src_type     TEXT        NOT NULL,
    src_id       TEXT        NOT NULL,
    dst_type     TEXT        NOT NULL,
    dst_id       TEXT        NOT NULL,
    rel          TEXT        NOT NULL,
    project_slug TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (src_type, src_id, dst_type, dst_id, rel)
);

CREATE INDEX IF NOT EXISTS idx_dash_links_dst
    ON dash.dash_links (dst_type, dst_id);

CREATE INDEX IF NOT EXISTS idx_dash_links_src
    ON dash.dash_links (src_type, src_id);

CREATE INDEX IF NOT EXISTS idx_dash_links_project
    ON dash.dash_links (project_slug);
