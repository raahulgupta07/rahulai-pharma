-- Brain unique constraints for project + global scoping.
-- Idempotent (IF NOT EXISTS). Replaces runtime bootstrap in
-- app/brain_seeds.py — that bootstrap should fall through to no-op
-- once these indexes exist.

-- Project-scoped uniqueness: same name allowed across different projects
CREATE UNIQUE INDEX IF NOT EXISTS uq_brain_slug_name
  ON public.dash_company_brain (project_slug, name)
  WHERE project_slug IS NOT NULL;

-- Global (cross-project) uniqueness: when project_slug IS NULL,
-- name must be unique across all global entries
CREATE UNIQUE INDEX IF NOT EXISTS uq_brain_global_name
  ON public.dash_company_brain (name)
  WHERE project_slug IS NULL;

-- Personal (per-user) uniqueness: when user_id is set,
-- name must be unique within that user's personal scope
CREATE UNIQUE INDEX IF NOT EXISTS uq_brain_personal_name
  ON public.dash_company_brain (user_id, name)
  WHERE user_id IS NOT NULL;

COMMENT ON INDEX public.uq_brain_slug_name
  IS 'Unique name per project (NULL=global, see uq_brain_global_name).';

-- ROLLBACK: DROP INDEX IF EXISTS uq_brain_slug_name, uq_brain_global_name, uq_brain_personal_name;
