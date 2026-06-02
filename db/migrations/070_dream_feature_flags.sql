-- Migration 070: Dream Reflection feature flag default
--
-- Ensures every existing project has `feature_config.tools.dream_reflection=true`
-- so the Dream Reflection subsystem is on-by-default after deploy. Idempotent:
-- skips rows where the key is already set.
--
-- Depends on: 017_feature_config.sql (feature_config jsonb column on dash_projects).

UPDATE public.dash_projects
   SET feature_config = jsonb_set(
       COALESCE(feature_config, '{}'::jsonb),
       '{tools,dream_reflection}',
       'true'::jsonb,
       true
   )
 WHERE feature_config IS NULL
    OR feature_config -> 'tools' IS NULL
    OR feature_config -> 'tools' -> 'dream_reflection' IS NULL;

-- ROLLBACK
-- To revert this default, remove the key from every project:
--
-- UPDATE public.dash_projects
--    SET feature_config = feature_config #- '{tools,dream_reflection}'
--  WHERE feature_config -> 'tools' ? 'dream_reflection';
