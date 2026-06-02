-- Migration 075: Zero-LLM Entity Linker
--
-- Adds extractor + cost columns to dash_knowledge_triples so we can split
-- regex/dict-extracted triples from LLM-extracted ones for cost analytics.
--
-- Schema is detected at apply time (table may live in `dash`, `public`, or
-- `ai` depending on install history — pattern borrowed from migration 067).

DO $$
DECLARE _schema TEXT;
BEGIN
  SELECT table_schema INTO _schema FROM information_schema.tables
   WHERE table_name = 'dash_knowledge_triples'
     AND table_schema IN ('dash', 'public', 'ai')
   ORDER BY CASE table_schema WHEN 'dash' THEN 1 WHEN 'public' THEN 2 ELSE 3 END
   LIMIT 1;
  IF _schema IS NOT NULL THEN
    EXECUTE format(
      'ALTER TABLE %I.dash_knowledge_triples
         ADD COLUMN IF NOT EXISTS extractor             TEXT          DEFAULT ''llm'',
         ADD COLUMN IF NOT EXISTS extraction_cost_usd   NUMERIC(10,6) DEFAULT 0',
      _schema);
    EXECUTE format(
      'CREATE INDEX IF NOT EXISTS idx_kg_extractor_%s
         ON %I.dash_knowledge_triples(project_slug, extractor, created_at DESC)',
      _schema, _schema);
  ELSE
    RAISE NOTICE 'dash_knowledge_triples not found in any schema — extractor cols skipped';
  END IF;
END $$;

-- ROLLBACK
-- DO $$
-- DECLARE _s TEXT;
-- BEGIN
--   FOREACH _s IN ARRAY ARRAY['dash','public','ai'] LOOP
--     BEGIN
--       EXECUTE format('DROP INDEX IF EXISTS %I.idx_kg_extractor_%s', _s, _s);
--       EXECUTE format(
--         'ALTER TABLE %I.dash_knowledge_triples
--            DROP COLUMN IF EXISTS extraction_cost_usd,
--            DROP COLUMN IF EXISTS extractor', _s);
--     EXCEPTION WHEN OTHERS THEN NULL;
--     END;
--   END LOOP;
-- END $$;
