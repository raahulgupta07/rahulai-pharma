-- 024_user_scopes.sql
-- Per-user scope claims (RLS scope thread). Optional: requests with X-Scope-Id
-- header bind these scopes into the RLS ContextVar (user_attrs.store_id).

CREATE TABLE IF NOT EXISTS public.dash_user_scopes (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES public.dash_users(id) ON DELETE CASCADE,
  project_slug TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  scope_label TEXT NOT NULL,
  role TEXT DEFAULT 'staff',
  is_default BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(user_id, project_slug, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_user_scopes_user
  ON public.dash_user_scopes(user_id, project_slug);
