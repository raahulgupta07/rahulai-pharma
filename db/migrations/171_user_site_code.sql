-- CityPharma shop-counter scoping: bind each user to their branch (site_code).
-- Injected into chat as SHOP CONTEXT so stock_check / find_substitutes default to
-- the staff member's branch. Idempotent.
ALTER TABLE public.dash_users ADD COLUMN IF NOT EXISTS site_code text;
