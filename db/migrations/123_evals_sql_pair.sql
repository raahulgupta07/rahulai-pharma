-- SQL result-set grader: OpenAI-style eval pairs (generated_sql vs expected_sql)
-- Adds columns to dash.dash_eval_cases for SQL pair grading mode.

ALTER TABLE dash.dash_eval_cases
  ADD COLUMN IF NOT EXISTS expected_sql TEXT,
  ADD COLUMN IF NOT EXISTS expected_dialect TEXT DEFAULT 'postgres',
  ADD COLUMN IF NOT EXISTS grading_mode TEXT DEFAULT 'llm_judge',
  ADD COLUMN IF NOT EXISTS generated_sql_hint TEXT;

CREATE INDEX IF NOT EXISTS idx_eval_cases_grading_mode
  ON dash.dash_eval_cases(grading_mode);

-- Seed suite + 5 realistic Q&A pairs (idempotent via ON CONFLICT)
INSERT INTO dash.dash_eval_suites (id, project_slug, name, description, layer, target_agent, is_builtin, enabled)
VALUES (
  'es_sql_pair_seed',
  NULL,
  'SQL Pair Grader — Seed Pack',
  'Realistic Q&A pairs that compare generated SQL result-frames against expected SQL result-frames.',
  'llm_judge',
  'Analyst',
  true,
  true
)
ON CONFLICT (id) DO UPDATE
  SET description = EXCLUDED.description,
      layer = EXCLUDED.layer,
      updated_at = now();

INSERT INTO dash.dash_eval_cases
  (id, suite_id, name, input_prompt, expected_output, expected_sql, expected_dialect, grading_mode)
VALUES
  (
    'ec_sqlpair_01',
    'es_sql_pair_seed',
    'count rows in dash_users',
    'How many users are in the system?',
    'A single integer count of users.',
    'SELECT COUNT(*) AS user_count FROM public.dash_users',
    'postgres',
    'sql_pair'
  ),
  (
    'ec_sqlpair_02',
    'es_sql_pair_seed',
    'list distinct project slugs',
    'List all distinct project slugs.',
    'List of unique project slug strings.',
    'SELECT DISTINCT slug FROM public.dash_projects ORDER BY slug',
    'postgres',
    'sql_pair'
  ),
  (
    'ec_sqlpair_03',
    'es_sql_pair_seed',
    'top 5 eval suites by case count',
    'Which 5 eval suites have the most cases?',
    'Suite name and case count, ordered descending, limit 5.',
    'SELECT s.name, COUNT(c.id) AS case_count
     FROM dash.dash_eval_suites s
     LEFT JOIN dash.dash_eval_cases c ON c.suite_id = s.id
     GROUP BY s.name
     ORDER BY case_count DESC NULLS LAST
     LIMIT 5',
    'postgres',
    'sql_pair'
  ),
  (
    'ec_sqlpair_04',
    'es_sql_pair_seed',
    'avg pass_rate across recent eval runs',
    'What is the average pass_rate of eval runs?',
    'Single numeric average of pass_rate.',
    'SELECT AVG(pass_rate)::numeric(10,4) AS avg_pass_rate
     FROM dash.dash_eval_runs
     WHERE pass_rate IS NOT NULL',
    'postgres',
    'sql_pair'
  ),
  (
    'ec_sqlpair_05',
    'es_sql_pair_seed',
    'eval cases per grading_mode',
    'How many cases exist per grading_mode?',
    'Counts grouped by grading_mode.',
    'SELECT grading_mode, COUNT(*) AS n
     FROM dash.dash_eval_cases
     GROUP BY grading_mode
     ORDER BY n DESC',
    'postgres',
    'sql_pair'
  )
ON CONFLICT (id) DO UPDATE
  SET input_prompt = EXCLUDED.input_prompt,
      expected_sql = EXCLUDED.expected_sql,
      expected_dialect = EXCLUDED.expected_dialect,
      grading_mode = EXCLUDED.grading_mode;
