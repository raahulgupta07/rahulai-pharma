CREATE TABLE IF NOT EXISTS dash.sim_projects (
  id TEXT PRIMARY KEY,
  user_id UUID NOT NULL,
  project_slug TEXT,
  name TEXT NOT NULL,
  scenario TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'created',
  current_step INT NOT NULL DEFAULT 0,
  ontology_json JSONB,
  graph_stats JSONB,
  personas JSONB,
  timeline JSONB,
  report_md TEXT,
  config JSONB,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sim_projects_user ON dash.sim_projects(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sim_projects_status ON dash.sim_projects(status);
CREATE INDEX IF NOT EXISTS idx_sim_projects_slug ON dash.sim_projects(project_slug);

CREATE TABLE IF NOT EXISTS dash.sim_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT REFERENCES dash.sim_projects(id) ON DELETE CASCADE,
  step_num INT NOT NULL,
  step_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  progress INT NOT NULL DEFAULT 0,
  payload JSONB,
  message TEXT,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sim_steps_project ON dash.sim_steps(project_id, step_num);
CREATE INDEX IF NOT EXISTS idx_sim_steps_ts ON dash.sim_steps(project_id, ts DESC);

CREATE TABLE IF NOT EXISTS dash.sim_graph_nodes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT REFERENCES dash.sim_projects(id) ON DELETE CASCADE,
  entity_type TEXT NOT NULL,
  label TEXT NOT NULL,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sim_nodes_project ON dash.sim_graph_nodes(project_id);
CREATE INDEX IF NOT EXISTS idx_sim_nodes_type ON dash.sim_graph_nodes(project_id, entity_type);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sim_nodes_label ON dash.sim_graph_nodes(project_id, entity_type, label);

CREATE TABLE IF NOT EXISTS dash.sim_graph_edges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT REFERENCES dash.sim_projects(id) ON DELETE CASCADE,
  src UUID NOT NULL REFERENCES dash.sim_graph_nodes(id) ON DELETE CASCADE,
  dst UUID NOT NULL REFERENCES dash.sim_graph_nodes(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sim_edges_project ON dash.sim_graph_edges(project_id);
CREATE INDEX IF NOT EXISTS idx_sim_edges_src ON dash.sim_graph_edges(src);
CREATE INDEX IF NOT EXISTS idx_sim_edges_dst ON dash.sim_graph_edges(dst);

CREATE OR REPLACE FUNCTION dash.sim_projects_set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sim_projects_set_updated_at ON dash.sim_projects;
CREATE TRIGGER sim_projects_set_updated_at
  BEFORE UPDATE ON dash.sim_projects
  FOR EACH ROW EXECUTE FUNCTION dash.sim_projects_set_updated_at();
