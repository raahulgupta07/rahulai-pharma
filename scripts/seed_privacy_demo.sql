-- seed_privacy_demo.sql — demo data so the privacy/keyword analytics dashboards
-- have something to show on a blank install. SAFE to re-run: it first deletes its
-- own tagged rows (session_id / service_account / correction markers prefixed
-- 'demo-'). NOTHING here is real customer data — all synthetic pharma questions.
--
-- Feeds: /keywords, /feedback (Like-Dislike), /person, gateway messages, and the
-- LLM topic-cluster card (dash_keyword_topics). Run:
--   docker exec -i cp-db psql -U ai -d ai < scripts/seed_privacy_demo.sql

BEGIN;

-- clean prior demo rows ------------------------------------------------------
DELETE FROM public.dash_feedback        WHERE session_id LIKE 'demo-%';
DELETE FROM public.dash_chat_sessions   WHERE session_id LIKE 'demo-%';
DELETE FROM public.dash_apigw_messages  WHERE session_id LIKE 'demo-%';
DELETE FROM public.dash_keyword_topics  WHERE topic LIKE '[demo] %';

-- a demo user to attach sessions/feedback to (reuse if present) --------------
WITH u AS (
  INSERT INTO public.dash_users (username, email, role, is_active, created_at, password_hash)
  VALUES ('demo-analyst', 'demo-analyst@city.local', 'user', TRUE, now(),
          '$2b$12$disableddemoaccountxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx')
  ON CONFLICT (username) DO UPDATE SET email = EXCLUDED.email
  RETURNING id
)
SELECT id FROM u;

-- ── FEEDBACK (👍 / 👎) — current window (last 7 days) ───────────────────────
-- 👎 rows carry comment + correction so the train-review reveal flow has content.
INSERT INTO public.dash_feedback
  (user_id, project_slug, session_id, question, answer, sql_query, rating, created_at, comment, comment_tags, correction, correction_status)
SELECT (SELECT id FROM public.dash_users WHERE username='demo-analyst'),
       'citypharma', 'demo-fb-' || g, q, a, sq, rt,
       now() - (g || ' hours')::interval, cm, tg, co, st
FROM (VALUES
  (1,  'How many units of Paracetamol 500mg are in stock at the Yangon outlet?', 'Yangon currently holds 1,240 units of Paracetamol 500mg.', 'SELECT qty FROM stock WHERE article=''PARA500''', 'up',   NULL, NULL::text[], NULL, NULL),
  (2,  'What are the substitutes for Amoxicillin 250mg?', 'Available substitutes: Ampicillin 250mg, Cephalexin 250mg.', 'SELECT * FROM substitutes(''AMOX250'')', 'up', NULL, NULL, NULL, NULL),
  (3,  'Price of Vitamin C 1000mg tablets?', 'Vitamin C 1000mg is priced at 4,500 MMK per strip.', NULL, 'up', NULL, NULL, NULL, NULL),
  (4,  'What is the recommended dosage of Metformin for adults?', 'Typical adult dose is 500mg twice daily with meals.', NULL, 'up', NULL, NULL, NULL, NULL),
  (5,  'Show total sales of antibiotics last month by category', 'Antibiotic sales last month totaled 18.4M MMK across 6 categories.', 'SELECT category, SUM(sales) ...', 'up', NULL, NULL, NULL, NULL),
  (6,  'Is Insulin available in the Mandalay store?', 'No — Insulin is currently out of stock at Mandalay.', 'SELECT qty FROM stock ...', 'down', 'Answer said out of stock but warehouse shows 30 units in transit', ARRAY['stock','accuracy'], 'Insulin: 30 units in transit to Mandalay, available in 2 days', 'pending'),
  (7,  'What are the side effects of Ibuprofen?', 'Common side effects: nausea, heartburn, dizziness.', NULL, 'down', 'Missing the GI bleeding warning', ARRAY['drug-info','incomplete'], 'Add: risk of GI bleeding with long-term use', 'pending'),
  (8,  'Compare price of Augmentin across all outlets', 'Augmentin ranges 12,000–13,500 MMK across outlets.', 'SELECT outlet, price ...', 'down', 'Price for Naypyidaw outlet was wrong', ARRAY['price'], 'Naypyidaw Augmentin price is 12,800 MMK not 13,500', 'pending'),
  (9,  'How many strips of Cetirizine sold this week?', 'Cetirizine sold 430 strips this week.', 'SELECT SUM(qty) ...', 'up', NULL, NULL, NULL, NULL),
  (10, 'Alternative for Omeprazole 20mg if out of stock?', 'Try Pantoprazole 40mg or Esomeprazole 20mg.', NULL, 'up', NULL, NULL, NULL, NULL),
  (11, 'Dosage of Azithromycin for throat infection?', 'Azithromycin 500mg once daily for 3 days.', NULL, 'down', 'Should mention weight-based dosing for children', ARRAY['dosage'], 'For children use 10mg/kg once daily', 'pending'),
  (12, 'Which outlet has the most Amoxicillin stock?', 'Yangon Central holds the most Amoxicillin at 2,100 units.', 'SELECT outlet ...', 'up', NULL, NULL, NULL, NULL)
) AS v(g, q, a, sq, rt, cm, tg, co, st);

-- ── CHAT SESSIONS (first_message openers) — current window ──────────────────
INSERT INTO public.dash_chat_sessions
  (user_id, session_id, project_slug, first_message, created_at, updated_at)
SELECT (SELECT id FROM public.dash_users WHERE username='demo-analyst'),
       'demo-sess-' || g, 'citypharma', fm,
       now() - (g || ' hours')::interval, now() - (g || ' hours')::interval
FROM (VALUES
  (1, 'Check stock of Paracetamol across all outlets'),
  (2, 'I need substitutes for Amoxicillin urgently'),
  (3, 'What is the price of Vitamin C and Zinc supplements'),
  (4, 'Best selling antibiotics this quarter'),
  (5, 'Dosage guide for Metformin and Glimepiride'),
  (6, 'Out of stock report for diabetes medicines'),
  (7, 'Compare prices of cough syrups across branches'),
  (8, 'How much Insulin do we have in cold storage')
) AS v(g, fm);

-- ── API GATEWAY messages (external app user turns) — current window ─────────
INSERT INTO public.dash_apigw_messages
  (ts, session_id, service_account, store_id, role, content, masked)
SELECT now() - (g || ' hours')::interval, 'demo-gw-' || g, 'svc:demo', 'YGN-01', 'user', c, FALSE
FROM (VALUES
  (1, 'stock check for Paracetamol 500mg please'),
  (2, 'any substitute available for Ciprofloxacin'),
  (3, 'price list for all painkillers'),
  (4, 'how many units of Amoxicillin left'),
  (5, 'dosage of Paracetamol for fever in adults'),
  (6, 'total sales breakdown by category this month'),
  (7, 'is Vitamin D in stock at downtown branch'),
  (8, 'cheapest alternative to branded Augmentin')
) AS v(g, c);

-- ── PREVIOUS window (8–14 days ago) — gives /keywords "rising terms" a base ──
INSERT INTO public.dash_chat_sessions
  (user_id, session_id, project_slug, first_message, created_at, updated_at)
SELECT (SELECT id FROM public.dash_users WHERE username='demo-analyst'),
       'demo-sess-prev-' || g, 'citypharma', fm,
       now() - ((g + 192) || ' hours')::interval, now() - ((g + 192) || ' hours')::interval
FROM (VALUES
  (1, 'price of Paracetamol last week'),
  (2, 'stock of bandages and first aid'),
  (3, 'substitutes for blood pressure tablets'),
  (4, 'sales of vitamins overview')
) AS v(g, fm);

-- ── LLM TOPIC CLUSTERS (so the Topic clusters card shows without the daemon) ─
INSERT INTO public.dash_keyword_topics
  (window_start, window_end, topic, count, pct, keywords)
VALUES
  (now() - interval '7 days', now(), '[demo] Stock availability', 11, 34.4, '["stock","units","outlet","available","insulin"]'),
  (now() - interval '7 days', now(), '[demo] Drug substitutes',   7, 21.9, '["substitute","alternative","amoxicillin","omeprazole"]'),
  (now() - interval '7 days', now(), '[demo] Pricing',            6, 18.8, '["price","cost","augmentin","compare","mmk"]'),
  (now() - interval '7 days', now(), '[demo] Dosage & usage',     5, 15.6, '["dosage","metformin","azithromycin","adults","children"]'),
  (now() - interval '7 days', now(), '[demo] Sales analytics',    3,  9.4, '["sales","category","breakdown","total"]');

COMMIT;

SELECT 'feedback' AS tbl, COUNT(*) FROM public.dash_feedback WHERE session_id LIKE 'demo-%'
UNION ALL SELECT 'sessions', COUNT(*) FROM public.dash_chat_sessions WHERE session_id LIKE 'demo-%'
UNION ALL SELECT 'gateway_msgs', COUNT(*) FROM public.dash_apigw_messages WHERE session_id LIKE 'demo-%'
UNION ALL SELECT 'topics', COUNT(*) FROM public.dash_keyword_topics WHERE topic LIKE '[demo] %';
