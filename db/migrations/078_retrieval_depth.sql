-- Migration 078: Retrieval Depth Pack
--
-- Extends dash.dash_dream_reflection_tree to support depth-3 abstractions
-- (Generative Agents pattern, but deeper). depth-3 nodes aggregate 5-8
-- depth-2 reflections grouped by cosine-similarity clustering.
--
-- Idempotent — uses IF NOT EXISTS for column + index ADD.
-- Depends on: 067 (dash_dream_reflection_tree).

-- 1. Add depth col on tree if not already present (pre-067 installs).
--    067 already creates `depth INT NOT NULL DEFAULT 0`; this re-ADDs
--    defensively as `INT DEFAULT 1` for any older tree variant lacking it.
ALTER TABLE dash.dash_dream_reflection_tree
    ADD COLUMN IF NOT EXISTS depth INT DEFAULT 1;

-- 2. Multi-parent linkage for depth-3 nodes (1 depth-3 → N depth-2 parents).
--    Existing `parent_id` is single-parent (cite the highest-conf depth-2);
--    `parent_reflection_ids` keeps the full set for traceability.
ALTER TABLE dash.dash_dream_reflection_tree
    ADD COLUMN IF NOT EXISTS parent_reflection_ids BIGINT[];

-- 3. Index for fast depth filtering per project (UI: ?depth=3 view).
CREATE INDEX IF NOT EXISTS idx_reflect_tree_proj_depth_created
    ON dash.dash_dream_reflection_tree(project_slug, depth, created_at DESC);

-- ROLLBACK
-- DROP INDEX IF EXISTS dash.idx_reflect_tree_proj_depth_created;
-- ALTER TABLE dash.dash_dream_reflection_tree
--   DROP COLUMN IF EXISTS parent_reflection_ids;
-- (do NOT drop depth — owned by 067)
