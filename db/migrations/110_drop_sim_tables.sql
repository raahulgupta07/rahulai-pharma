-- 2026-05-23: drop sim chassis tables (sim_api + dash/sim/ deleted)
DROP TABLE IF EXISTS dash.sim_projects CASCADE;
DROP TABLE IF EXISTS dash.sim_steps CASCADE;
DROP TABLE IF EXISTS dash.sim_graph_nodes CASCADE;
DROP TABLE IF EXISTS dash.sim_graph_edges CASCADE;
