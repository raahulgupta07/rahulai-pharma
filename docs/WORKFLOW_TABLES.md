# Workflow Tables — Canonical Mapping

Dash has three top-level workflow tables plus per-run history. They serve
different use cases and MUST NOT be conflated. Each writer targets exactly
one canonical table.

## Tables

| Table | Use case | Schema | Steps storage |
|---|---|---|---|
| `dash.dash_autonomous_workflows` | Autonomous cron-driven workflows. Seeded by **verticals** (`apply-vertical`) and **agent-template apply** (`dash/templates/apply.py`). Picked up by `dash/templates/runner.py` daemon. Per-run audit lives in `dash.dash_workflow_run_history`. | `id, project_slug, template_name, name, description, schedule, query_template, resolved_query, expected_entity, expected_columns (jsonb), action, status, last_run_at, last_error, schedule_cron, schedule_action, schedule_email, schedule_webhook, max_cost_usd, daily_cap_usd, last_output (jsonb), owner_user_id, created_at` | Verticals stash multi-step playbooks in `expected_columns->'steps'` JSONB; cron runner uses `query_template` directly. |
| `public.dash_workflows_db` | User/training-saved **manual** workflows surfaced in the chat workflow picker. Written by training pipeline (`app/upload.py` doc-to-workflow), user save-from-chat (`POST /{slug}/workflows-db`), and conversation pattern mining. Read by chat UI. | `id, project_slug, name, description, steps (jsonb), source, created_at` | `steps` JSONB column. |
| `dash.dash_workflow_defs` | Workflow **definitions** for the Agent OS / template definition layer. Defined in `db/migrations/047_workflows.sql`. | Definition-level metadata. |

Per-run audit (always in `dash` schema):

- `dash.dash_workflow_run_history` — per-execution row written by the autonomous runner (status, duration_ms, cost_usd, output JSONB, error, triggered_by).
- `dash.dash_workflow_runs`, `dash.dash_workflow_run_steps` — legacy run audit used by some older paths.
- `public.dash_workflow_runs_v2` — newer extended-workflows audit (`app/workflows_extended_api.py`).

## Writers → Table mapping

| Writer | Target table |
|---|---|
| `app/projects.py::apply_vertical` (verticals) | **`dash.dash_autonomous_workflows`** (fixed 2026-05-19; previously wrote to `public.dash_workflows_db` which was wrong). |
| `dash/templates/storage.py::save_autonomous_workflows` (template apply) | `dash.dash_autonomous_workflows` |
| `dash/templates/runner.py` (cron daemon) | reads `dash.dash_autonomous_workflows`; writes `dash.dash_workflow_run_history` + UPDATEs the workflow row (last_run_at/last_error/last_output). |
| `app/learning.py` `POST /{slug}/workflows-db` (user save-from-chat) | `public.dash_workflows_db` |
| `app/learning.py` `POST /{slug}/doc-to-workflow` | `public.dash_workflows_db` |
| `app/upload.py` (training pipeline, conversation pattern mining) | `public.dash_workflows_db` |
| `app/workflows_extended_api.py` | `public.dash_workflow_runs_v2` (run audit only). |
| `dash/learning/auto_apply.py::auto_apply_vertical` | `dash.dash_autonomous_workflows` (via storage helper). |

## Rule of thumb

- **Cron-driven / vertical / agent-template auto-detect** → `dash.dash_autonomous_workflows`.
- **Manual / user-saved / chat playbook** → `public.dash_workflows_db`.
- **Definition catalog (Agent OS)** → `dash.dash_workflow_defs`.
- **Per-run audit** → `dash.dash_workflow_run_history` (autonomous) or `public.dash_workflow_runs_v2` (extended).

## Issue #6 — count discrepancy fix

`apply-vertical` previously reported `workflows_seeded: N` but inserted
zero rows into `dash.dash_autonomous_workflows` (it was writing to
`public.dash_workflows_db`). Fix:

1. Target `dash.dash_autonomous_workflows`.
2. Use `INSERT … RETURNING id` and count actual returned IDs.
3. Idempotency check on `(project_slug, template_name, name)` so re-apply doesn't
   double-insert.
4. Multi-step playbooks (pharma) stash their `steps` in
   `expected_columns->'steps'` JSONB — the column already exists; no schema
   migration needed.
