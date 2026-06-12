-- 190_age_extension.sql — enable Apache AGE for the drug_network multi-hop tool.
--
-- The cp-db image (cp-db-age:pg18) ships age.so; this just registers the
-- extension so the pharma knowledge graph (citypharma_kg, built by
-- scripts/build_pharma_graph.py on training-complete) can be created/queried.
--
-- FAIL-SOFT: wrapped in a DO block that swallows the error, so a deploy on a
-- plain-postgres image (no age.so) does NOT break the migration run — the
-- drug_network tool is fail-soft and falls back to relational find_substitutes
-- when the graph is absent. Idempotent (IF NOT EXISTS).

DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS age;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'AGE extension not available (non-AGE image?) — drug_network will fall back to relational. %', SQLERRM;
END
$$;
