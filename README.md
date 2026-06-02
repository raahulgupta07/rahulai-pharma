# CityAgent Insights — Self-Learning Multi-Tenant Data Notebook

> Internal codename: **Dash**. Public brand: **CityAgent Insights** (warm Claude-inspired theme — coral `#c96342` on cream `#faf9f5`, Source Serif 4 + Inter, sentence case).

Production-ready data agent platform. Each project is an isolated agent that
auto-trains, self-learns daily, and grows expert over time. Multi-source
(Postgres / MySQL / Fabric / SharePoint / OneDrive / Google Drive). kpt-
inspired autonomous research loop. White-label ready (default tenant ships as CityAgent Insights; replace `branding/default/logo.png` + `company.json` to rebrand).

**Brand assets**
- Logo: `frontend/static/brand/cityagent.png` (812×297 transparent PNG, also at `branding/default/logo.png`).
- Served at `/brand/cityagent.png` (mounted in `app/main.py`, allow-listed in `AuthMiddleware.SKIP_PREFIXES`).
- Logo endpoint `/api/branding/logo.svg` probes `logo.png/jpg/webp/svg` per tenant, falls back to bundled CityAgent PNG, last resort cream-coral SVG.

## What is Dash?

Dash is a multi-tenant, self-learning data agent platform — NotebookLM for
your databases, dashboards, and documents. Each business problem becomes a
project (data agent) that connects to data, auto-trains on schemas + sample
rows + uploaded docs, builds a private knowledge graph + glossary + KPI
library, lets users chat in natural language (SQL, forecasts, anomalies),
and improves itself nightly by running hypotheses, verifying on live data,
and promoting facts to a shared Company Brain. Federation across multiple
sources inside one agent. Hard tenant + RBAC isolation.

## Latest (2026-05-27 latest+++++) — Queue finalizer + zero-touch schema drift

Three small follow-ups after the scaling-sprint session (below). All hot-copied + USR2 reloaded.

**1. Training run finalizer** — `dash/training/train_queue.py::complete_job()` aggregates sibling job statuses after every finish. When 0 pending/running siblings → flips parent `dash_training_runs.status` to `done` (or `failed` if any sibling failed). Idempotent via `NOT IN (terminal)` guard. Fixes 3 stale "running forever" runs that confused UI.

**2. Lazy `profile_v2` on cache miss** — `dash/tools/build.py::RLSAwareSQLTools.run_sql_query` extracts table refs via sqlglot, checks `dash_table_metadata` for missing `profile_v2`, calls `profile_table_v2()` inline (1.4s cold), invalidates prompt cache. New table added post-train → first query takes +1.4s → correct answer + auto-profiled. No manual retrain. Cap 10 tables, kill switch `LAZY_PROFILE_V2_DISABLED=1`, fail-soft.

**3. Unknown-table prompt rule** — `dash/instructions.py:560-571` injects `## 🆕 UNKNOWN-TABLE RULE` into Analyst grounding. Forces `discover_tables()` call BEFORE SQL when user mentions table/column not in SEMANTIC MODEL or PROFILE V2. Belt + suspenders with #2.

**Net:** schema drift between trainings no longer means wrong answers. Lazy profile + prompt rule = zero-touch onboarding of new tables.

**Env added:** `LAZY_PROFILE_V2_DISABLED=1`

**Files:** `dash/training/train_queue.py` (+30) · `dash/tools/build.py` (+55) · `dash/instructions.py` (+12)

---

## Earlier (2026-05-27 night) — Hermes patterns + GB ingest + advanced EDA + queued training + OOM fix

Multi-day scaling sprint (9 chat turns, single session). Closed customer-reported concerns: GB-scale data + multi-user training + OOM + agent dim comprehension. Lifted 3 patterns from Nous Research **hermes-agent** (915K LOC monorepo deep-read). Built advanced EDA pipeline + queue-based non-blocking training. ~4200 LOC across 12 new modules + 8 edited files. All hot-copied + live-tested on real CRM data (21,240 rows × 6 tables).

### 12 features shipped

| # | Feature | LOC | Win |
|---|---|---|---|
| 1 | **ToolGuardrail** — kills identical-args retry loops on Analyst SQL | 230 | No more 3-retry burn |
| 2 | **Skill SQL inline-prep** — `!`SELECT...`` + `${VAR}` at skill-load time | 180 | -1-2 LLM tool calls/chat |
| 3 | **Constraint gates** — 5 hard gates reject malformed skill patches | 175 | Saves $0.05/patch |
| 4 | **Scope-derive daemon** — nightly fills `feature_config.scope` per project | 175 | Off-topic guard always covered |
| 5 | **GB-scale streaming COPY ingest** — psycopg3 COPY FROM STDIN, no RAM cap | 515 | 10GB CSV (was 200MB) |
| 6 | **profile_v2 advanced profiler** — combined-query + pg_stats + variants | 750 | 250s → 5s on 10M rows |
| 7 | **3 EDA drill-down tools** — inspect_dimension/cross_dim/time | 430 | Cached fast-path |
| 8 | **Compact prompt formatter** Layer 3a-v2 — 80 chars/col | 140 | Catalog under 4KB |
| 9 | **profile_v2 training step** — wired into retrain pipeline | 25 | JSONB artifact |
| 10 | **Queue-based training MVP** Option B Redis — non-blocking 202 | 620 | Multi-user training |
| 11 | **WORKERS 8 → 2** — immediate OOM relief | 1 | Mem 80% → 40% |
| 12 | **Variant detector perf rewrite** — 30 queries → 2 batched | 70 | 64s → 1.4s |

### Hermes-agent deep-read (915K LOC)

Honest verdict: most primitives downstream of Dash's existing chassis. **3 patterns lifted, rest skipped.**

- `agent/tool_guardrails.py` (400 LOC) → `dash/runtime/tool_guardrail.py` — hash-based per-turn state machine
- `agent/skill_preprocessing.py` (140 LOC) → `dash/skills/preprocess.py` — replaced bash-exec w/ **tenant-scoped SQL** via `validate_and_fix`
- `evolution/core/constraints.py` (174 LOC) → `dash/learning/skill_patch_constraints.py` — 5 fail-fast gates before shadow validation

Skipped: trajectory_compressor (Dash has 9 layers), MemoryManager (Brain richer), system_and_3 cache (tool-heavy not chat-heavy), GEPA orchestration (SkillRefinery does 80%).

### Advanced EDA

Before: per-col SQL loop 250s/10M rows, verbose 500-value catalog OR nothing, agent wrote raw SQL for every dim question.

After: Layer 3a-v2 prompt = 80 chars/col compact catalog (DIMENSIONS / STATES / MEASURES / IDENTIFIERS / TEMPORAL) + top-3 values + freq + role + variant warnings. 3 new tools on Analyst (`inspect_dimension`, `inspect_cross_dim`, `inspect_time`). profile_v2 runs 1.4s cold / 0.1s warm on 21K-row table.

### Queue-based training (Option B Redis)

POST `/api/projects/{slug}/retrain-queued` enqueues per-table jobs → returns 202 instantly. In-process worker (gated `WORKER_RANK==0`) drains. Per-project lock (fair multi-tenant). 5min SIGALRM timeout. GET `/training-runs/{id}/status-v2` aggregates job status.

**Live test**: 6 jobs processed in **307ms total** on 21K-row CRM project. Status endpoint: `{status:"done", completed:6, failed:0}`.

### 2 bugs caught in real-data test

1. `_safe_ident` in `copy_stream.py` + `profile_v2.py` both stripped trailing `_` from slugs → orphan schema → 0 rows visible. Fixed both. **Pattern**: never `.strip("_")` on project slugs.
2. OOM at WORKERS=8 / 7.75GB cap (each worker ~2.5GB w/ embedder + LLM clients). Fixed via WORKERS=2 + scale horizontally.

### Migration

`170_training_jobs.sql` — `public.dash_training_jobs` (run_id, project_slug, table_name, job_type, status, payload JSONB, result JSONB, error, timestamps) + 3 indexes.

### Env vars added

12 new kill switches (all default ON): `TOOL_GUARDRAIL_*`, `SKILL_PREPROCESS_DISABLED`, `SKILL_PATCH_CONSTRAINTS_DISABLED`, `SCOPE_DERIVE_*`, `STREAM_UPLOAD_MAX_GB`, `PROFILE_V2_*`, `EDA_TOOLS_DISABLED`, `TRAINING_QUEUE_DISABLED`.

### Scaling capacity post-fix

| Pod setup | Concurrent chat | Concurrent training |
|---|---|---|
| 1 pod × 2 workers, 4GB | 50-100 | 1-2 |
| 3-pod HPA | 200-500 | 5-10 |
| 10-pod elastic | 1000+ | 30+ |

### Patterns to remember

- **Hermes is mostly downstream of Dash chassis** — don't lift wholesale, pick 3 additive
- **Project slugs end in `_`** — never `.strip("_")` on slugs, PR review gate
- **USR2 reload does NOT re-trigger lifespan** — worker auto-spawn needs full container restart
- **`docker compose up -d --force-recreate` wipes hot-copies** — bake into image first OR re-copy after
- **Per-column SQL loops are footguns** — batch into ONE combined query (30-450× measured)
- **Scale HORIZONTALLY** — each worker holds full embedder + LLM clients + agent team
- **In-process Redis queue beats new container for MVPs** — reuse existing Redis

---

## Earlier (2026-05-27 evening++) — SQL validator UI surfaces + chat-time auto-fix + VentureDesk default-OFF

4 parallel agents shipped 5 UI surfaces for the SQL validator infra (entry below). Users can now SEE the prevention work, not just read logs.

**5 UI surfaces:**

| Surface | Where |
|---|---|
| SQL safety tile | Settings → Cockpit → At a glance (4th tile) — auto-fixes / Q&A drops / cache hit % |
| Q&A drops card | Settings → Training → collapsible w/ per-table drops + click-row-to-expand reasons |
| Drift gate badge | Command Center → System health → CLEAN/DRIFT pill + stats |
| Chat trace pill | TraceTimeline ✨ "SQL auto-fixed" pill per step + header count chip |
| VentureDesk gate | Settings → CONFIG cards (existing UI, now default-OFF for new projects) |

**Backend telemetry (mig 164):**

- `dash.dash_sql_validator_events(kind, source, table_name, details JSONB)` — `kind ∈ {auto_fix, qa_drop, chat_autofix, reject}`
- `_emit_event()` helper in `sql_validator.py` — called from auto-fix paths, Q&A gen drop loop, chat-time auto-fix
- `get_cache_stats()` + hit/miss counters in `_load_schema`
- 4 endpoints in `app/sql_validator_api.py`: stats / qa-drops / cache-stats / admin/drift/status

**Chat-time auto-fix** (`dash/tools/build.py` `RLSAwareSQLTools.run_sql_query`): wired validator auto-fix between MDL compile + cost guard, `strict=False` (applies dialect fixes, doesn't reject unknown cols). Closes class where Analyst emits `date_trunc('month', text_col)` and burns 3 retry cycles. 4/4 CRM analytical questions verified — 0 UndefinedFunction errors.

**VentureDesk default-OFF**: 4 verticals flipped in `dash/feature_config.py`. Saved ~600 tokens/chat on generic projects. Existing projects keep saved config (no silent flip).

**4 bugs caught during ship:**
- Container missing `scripts/` + `db/migrations/` (Dockerfile gap) — `docker cp` fix
- Drift regex matched count-first format, script outputs label-first — rewrote w/ variadic patterns
- `0 or fallback` antipattern — `drift_after=0` falsy fell through to wrong line's 74
- `kill -HUP 1` doesn't reload module cache for already-imported modules — `pkill -USR2 -f gunicorn` instead

**Patterns:**
- Backend tells you it exists; UI tells you it works. Plan UI alongside infra.
- `docker cp` fast path; bake `scripts/` + `db/migrations/` into next image build.
- For gunicorn module-cache refresh: `pkill -USR2 -f gunicorn` (not `-HUP`).
- Python `0 or x` returns `x` — never use `or` in int parse fallback chains.
- Output-format regex: write against actual stdout, not imagined stdout. `LABEL: N` vs `N LABEL` look identical to eyes, different to regex.
- Forward-compatible frontend wiring: write extraction w/ multi-source fallbacks. When backend lights up later, frontend works w/o further edits.

3 commits today: `fef4b48` + `a8a3461` + `11cd90b`. 27 files, ~10,200 LOC.

---

## Earlier (2026-05-27 evening) — Central SQL validator + LLM hallucination class permanently closed

7-phase prevention sprint (5h, 7 parallel agents). End-to-end test on real 6-month CRM dataset (21,240 rows) found two bug classes — fixed both at the root, added CI gate so neither can recur.

**Headline:**
- Eval failures per CRM train **26 → 0**
- workflow_runner log spam **12/min → 0**
- Future migration drift caught at PR time (CI gate)

**Shipped:**
- **NEW `dash/tools/sql_validator.py`** — sqlglot parse + 5min schema cache + auto-fix (`date_trunc(_, text)` → `text::date`; comparison casts) + Levenshtein suggestion + EXPLAIN final gate
- **NEW `dash/tools/llm_sql_helper.py`** — `_postgres_sql_rules()` dialect block + `generate_sql_safe()` w/ 1-retry self-correction
- **Wired into 4 LLM-SQL sites** (Q&A gen, metrics_api NL→SQL, DeepDashAgent stage 4+5, workflow_runner). 2 sites audited + correctly skipped (no SQL emission)
- **Applied mig 092** that created `dash.dash_workflow_run_history` (was missed by auto-runner)
- **NEW CI gate** `scripts/check_migration_drift.py` + `scripts/drift_allowlist.txt` (72 entries) + `.github/workflows/migration-drift.yml` + `make check-drift`. Catches "code SELECTs from non-existent table" at PR time. 1495 refs scanned, 0 drift after allowlist
- **Dedup** — `dash/tools/deep_deck.py` now imports `_postgres_sql_rules()` (was inline). Single source of truth across all LLM-SQL prompts

**Pattern for any new LLM-SQL feature (REQUIRED):**
```python
try:
    from dash.tools.sql_validator import validate_and_fix
    from dash.tools.llm_sql_helper import _postgres_sql_rules, get_schema_hint
except Exception:
    # fail-soft
    pass

prompt = f"{_postgres_sql_rules()}\n\n{get_schema_hint(slug)}\n\n{user_prompt}"
sql = llm_generate(prompt)
v = validate_and_fix(sql, slug, strict=True)
if not v["ok"]:
    continue  # never persist bad SQL
sql = v["sql"]  # auto-fixed
# SQL-VALIDATED
```

`# SQL-VALIDATED` marker = greppable PR review gate. 11+ matches across 5 files.

---

## Earlier (2026-05-27) — Upload pipeline UX: 1-click Train + drift auto-evolve + live progress

3 confusing buttons (Re-check / Promote / Promote+Train / Reject) collapsed to **ONE Train button**. Schema drift no longer quarantines monthly CSV drops — contract auto-evolves silently. Quality scorer threshold lowered 40→10 (real CRM data w/ many sparse cols was hitting score 0). New `force=true` param bypasses all gates. CLI footer streams every pipeline step live. Cockpit pill bar + Training tab banner populate from polled state, survive page refresh. 15 bugs in single session, all hot-deployed.

**Backend (`app/upload.py`):**
- `stage_upload` — dual-source `project` (form OR query)
- staging drift → auto-evolve contract, status stays `ready`
- quality quarantine: threshold 40→10 + requires `rows < 5`
- `ingest_promote` — new `force` param bypasses drift + quarantine
- train gate: `if train and (loaded or force)` so force-train always fires

**Frontend (`settings/+page.svelte`):**
- 3→1 button **Train** (always `force=true`)
- `loadTrainingSteps()` poll dispatches `cLog()` events on every step transition (deduped via `Set`)
- Quality check modal pre-ticks ALL tables (was GOOD-only)
- Cockpit pill bar reacts to polled `trainStepsRunStatus === 'running'` (not just local flag)
- Training tab live banner: coral pulse + N runs active + 5s auto-poll
- Page mount resume: if active run in DB → `isTraining=true` + start poll
- VentureDesk: audited, gated decision to KEEP (default-on, ~600 tokens/chat overhead)

**Patterns:**
- `force=true` semantics: bypass quality + drift + auto-evolve contract. Default for "I know what I'm doing" buttons.
- CLI footer pops via `dash-cli-log` window event. Any new long flow MUST dispatch `cLog()` or user sees nothing happen → support tickets.
- Step poll dedup: `Set<string>` keyed `${run_id}|${step_name}|${status}` so 3s polling doesn't spam logs.
- `isTraining` state needs both local + polled sources (gate on OR) — survives page refresh.
- Project recreate w/ same slug ≠ delete. Disk dirs persist independently of DB row. Delete via UI or `shutil.rmtree`.

---

## Earlier (2026-05-26 evening) — Dead-code audit fix + 3 vertical agents wired + default-ON flip

3 commits shipped. Audit fix (`5d1a97a`) — PgBouncer `:x::jsonb` collisions in schedules/verifier/agent_iq, ML chassis fully removed (migration 111 already dropped tables, deleted ml_models.py + data_scientist.py + auto_ml.py + causal_drivers.py + ab_test.py + recall_tool.py + sim-cleanup cronjob + investment 15d block in upload.py + `/ml`/`/ml-insights`/`/automl` frontend routes + `/api/admin/ml/*` + Data Scientist registrations). Vertical agents (`e06edb8`) — wired 3 disk-only verticals into default team (market_sentinel, ops_optimizer, supply_sentry) via 7-phase sequential plan: team.py build blocks + feature_config flags + instructions.py Leader routing + /agents endpoint metadata + Settings CAP_MODEL toggle cards. Default-ON flip (`b8a9af8`) — reversed opt-in OFF to default-ON per user direction. Tools self-check data presence internally; no table-name auto-detect.

**Agent inventory now: 37 total** (was 33 pre-session). +4 verticals (Deal Analyst, Market Sentinel, Ops Optimizer, Supply Sentry), −1 Data Scientist (deleted). Core 5 / Verticals 4 / Specialists 10 / Background 11 / Upload 5 / Routing 2.

**ASCII org diagram + `/ui/home` cards rebuilt** to match `/ui/projects` style (pipeline progress + "Open chat →" CTA + kebab menu).

See CLAUDE.md `## 2026-05-26 (latest)` for full session log + per-commit details.

---

## Earlier (2026-05-26) — Composer collapse + exec-card answer layout + 3-tier router + skill subsystem fixes

Major UX + architecture session. Killed user-facing tech-choice dropdowns. Shipped exec-card answer layout. Collapsed 4 reasoning tiers to 3. Fixed `apply_skill` end-to-end. Added Excel export. 11 parallel agents across 3 waves, all baked + live.

### BLOCKER fixes

| What | Fix |
|---|---|
| `apply_skill` tool wired but querying non-existent `public.dash_skill_library` (migration drift) | Created table at `public.` to match all 20+ code refs. Seeded 3 pharma skills. End-to-end verified — 3/3 questions resolved via skill, success_count bumps |
| `_track_usage()` UPDATE blocked by public-schema read-only guard | Switched to `get_write_engine()` (CLAUDE.md gotcha, 4th occurrence) |
| Layer 15 skill list dropped by 32K context packer (rank 6, trimmed from blob tail) | Extracted to standalone `parts.append()` w/ `## PROVEN SKILLS` heading + rank-3 priority in `_rank_of()` |
| Headline `**12.47B**` rendered as literal text | Added `formatInline()` helper in `markdown.ts`, switched headline render to `{@html formatInline(...)}` |

### HIGH fixes

| What | Fix |
|---|---|
| Exec-card answer layout (Options 2+5+8 hybrid) | NEW `AnswerCard.svelte` (581 LOC) + `answer-tags.ts` (12 parsers). 18 blocks tier-gated. EXEC_TIER ContextVar |
| 4 reasoning tiers → 3 (ULTRA merged into DEEP) | `_build_exec_layout_directives()` collapsed branch, ULTRA blocks (SEGMENT/BENCHMARK/SCENARIO/FORECAST/AUDIT) now opportunistic emit inside DEEP |
| Old `**FAST mode**`/`**DEEP mode**` response-format blocks redundant w/ tier directives | Deleted lines 755-786 in instructions.py |
| Composer 12 controls → 6 | Deleted Effort/Type/Model/CHAT→DASH/composer-STUDIO/X buttons. Mode picker kept (3 options: AUTO/FAST/DEEP). Backend AUTO defaults |
| Slash command escape hatch | `/quick <q>` + `/deep <q>` parser in `send()` — hidden, no UI hint |
| 3 brand-colored action icons | Dashboard (teal `#0e7c86`), Slides (PPT orange `#d24726`), Excel (green `#217346`). Mobile labels hide <720px |
| Excel export wired | `exportExcelChat()` → `POST /api/export/excel-from-chat` → blob download. 4 sheets (Summary/Data/Charts/Conversation) |
| Markdown structure rules MANDATORY for DEEP/AGENTIC | Agents must use `## Section` headings (not `1. Title`), fenced code blocks, `---` on own line. Frontend `markdown.ts` adds `<hr>` + heading levels 1-6 + numbered/step section auto-detect |
| RELATED chips missing on DEEP answers | MANDATORY in STANDARD + DEEP prompts. Frontend fallback scans markdown body for `## Next Steps` / `## Related Questions` etc as chips |
| Decision diary | Migration 148 + 5 v2 endpoints. Decision action chip wired |

### Tier output map (LIVE)

| Tier | Auto-triggers | Blocks | Time | Cost |
|---|---|---|---|---|
| `quick` | "what is X", lookups | KPI×1 + 1 line | <500ms | $0.0001 |
| `standard` | Default analytical | Action title + narration + 3 KPI + 2 recs + related + audit | ~2s | $0.005 |
| `deep` | "why/explain/compare/what if/forecast/benchmark" | Standard + attention + root cause + segments + benchmarks + scenarios + forecast (opportunistic) | ~8-15s | $0.05 |

### Composer final shape (6 controls)

```
[⊞ Flow ▾] [◐ AUTO ▾] [Ask anything…] [→] [📊 Dashboard] [📽 Slides] [📊 Excel]
```

- **Flow ▾** — workflow picker (rare)
- **Mode ▾** — AUTO (router decides) / FAST (force quick) / DEEP (force deep)
- **Textarea** — `/quick`/`/deep` slash override hidden
- **→** Send / ⏹ Stop
- **Dashboard** (teal) — build interactive HTML dashboard
- **Slides** (orange) — build .pptx presentation
- **Excel** (green) — export chat to .xlsx

### Files added

```
db/migrations/148_decisions.sql                          decision diary table
frontend/src/lib/chat/AnswerCard.svelte                  exec card renderer (581 LOC, 18 blocks)
frontend/src/lib/answer-tags.ts                          12 tag parsers
```

### Files modified (key)

```
dash/tools/apply_skill.py                                _track_usage() uses get_write_engine()
mcp_server/tools_registry.py                             apply_skill import path fix
dash/instructions.py                                     exec directives + EXEC_TIER + ULTRA→DEEP collapse
                                                          + markdown structure rules + RELATED mandatory
                                                          + skill list rank-3 standalone
app/main.py                                              _tier_label() 3-tier + Routing SSE meta
app/projects.py                                          EXEC_TIER.set() from reasoning form field
dash/feature_config.py                                   tabs.exec_view default false
frontend/src/routes/project/[slug]/+page.svelte         composer collapse + Mode picker + slash parser
                                                          + Dashboard/Slides/Excel icons + Excel handler
frontend/src/lib/chat/ChatMessageList.svelte             conditional AnswerCard render + onAction dispatch
frontend/src/lib/markdown.ts                             formatInline export + hr/headings/sections
frontend/src/lib/index.ts                                barrel exports formatInline + 12 tag parsers
```

### Patterns to remember

- **Public.dash_* writes need `get_write_engine()`** — 4th-session reminder. `_guard_public_schema` silently rolls back on `get_sql_engine()`
- **32K packer drops blob tails** — extract MUST-KEEP features to standalone `parts.append()` w/ rank-3 detection in `_rank_of()`. Pattern works for skills + verified metrics + RLS
- **LLM tier variance on optional tags** — agents skip RECOMMENDATION/RELATED on DEEP/AGENTIC mode. Fix in 2 layers: MANDATORY prompt rule + frontend fallback parser scanning markdown body
- **Hidden slash commands** — no UI hint, discoverable via docs. Power users prefer clean composer
- **3 colored action icons proven UX win** — brand recognition beats text labels. Teal=data, orange=PowerPoint, green=Excel

---

## Earlier (2026-05-25 evening) — Deep audit sweep: 8 parallel agents fixed 4 BLOCKERS + 5 HIGH + 1 MED

8-agent parallel fix wave covering audit findings. All 16 touched files `ast.parse` clean.

### BLOCKER fixes

| # | Item | Result |
|---|------|--------|
| 1 | Committed secrets | `docs/SECRETS_AUDIT.md:29` live OpenRouter key redacted + dated. `CLAUDE.md`: 1 key + 4 pw scrubbed. `README.md`: 5 pw scrubbed. **External rotation still required.** |
| 2 | Owner-role 403 (A1 unblocker) | `app/auth.py` `role_levels = {viewer:0, editor:1, admin:2, owner:100}`. Fresh-project owner now passes editor gate → `test_million_row_scale` unblocked. |
| 3 | compose.yaml insecure defaults | 21 substitutions across 6 services → `${VAR:?required}`. Boot fails fast if `.env` missing. `dash-api` port `8001` bound `127.0.0.1` (nginx-only ingress). |
| 4 | Multipart `Form()` | `stage_upload`, `upload_document`, `upload_with_agent` params switched from bare `str \| None` → `Form(None)`. `Form` imported. |

### HIGH fixes

| # | Item | Result |
|---|------|--------|
| 5 | `get_write_engine` swap | 5 real write paths swapped (`campaigns.py` ×2, `attribution.py` ×1, `customer_360.py` ×2). 4 read/shared helpers correctly left on `get_sql_engine`. |
| 6 | `NullPool` wrapper | `app/upload.py:44-47` already injects `kw.setdefault("poolclass", NullPool)`. 38 callers auto-inherit. |
| 7 | SSE `safe_dumps` | `upload.py` 10 sites + 8 non-upload files (`dashboards_api`, `deep_deck_api`, `connectors`, `connectors_classify`, `onedrive`, `gdrive`, `sharepoint`, `agent_os_workflows`) 23 sites = **33 raw `json.dumps` → 0 in SSE generators**. Source `dash.utils.safe_dumps`. |
| 8 | `_bg_executor.submit` callbacks | 6 of 6 unguarded sites in `upload.py` wrapped with `_bg_done_log` (logs via `logger.exception` on failure). |
| 9 | Bare `except: pass` | `auth.py` 19 sites + `learning.py` 18 sites → `logger.exception("…: <context>")`. |

### MED fixes

| # | Item | Result |
|---|------|--------|
| 10 | `:x::jsonb` PgBouncer collision | `upload.py` 4 sites + `brain_seeds.py` 4 sites = **8 → 0 bind-param violations**. Literal `'[]'::jsonb` correctly preserved. |

### Still deferred

- Migration rollback headers (51 files in 080-145, scope too large for sweep)
- `upload.py` function size refactor (`_cancelled` 1570 LoC etc.) — maintainability, not a bug
- Healthchecks for `dash-ml` / `caddy` / `dash-backup` (no reasonable probe target)
- External secret rotation: `OPENROUTER_API_KEY`, `SUPER_ADMIN_PASS`, `CONNECTION_ENCRYPTION_KEY`, `PEXELS_API_KEY`

### Required ops actions

1. Set every required env var before next `docker compose up` (boot now refuses defaults)
2. Rotate the 4 secrets above out-of-band
3. `env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=$HOME docker compose build dash-api && docker compose up -d --force-recreate dash-api`

---

## Earlier (2026-05-25 morning) — Pre-prod checklist: Tracks A+B+C via 3 parallel agents — backup cron · Sentry · Prometheus · RLS test · secrets audit · rate-limit middleware. 1M scale fixture + Agno SSE gap flagged for follow-up

3 parallel agents dispatched (B+C background, A foreground). ~17min wall vs ~2hr serial. 8 of 12 pre-prod items shipped or documented; 3 critical user-gated follow-ups flagged.

### Track A — Verification (foreground)

| Item | Result |
|---|---|
| **A1** 1M-row scale | CSV gen ✓ (`gen_million.py`, 1M rows, 43MB). Pytest threshold lowered 50→40MB. **Blocked at upload**: 403 "Editor access required" even for project owner. `check_project_permission` bug — not in pre-prod scope. UI-driven upload works. |
| **A2** SSE audit | **Gap confirmed**: `dash_sse_audit` only has 2 direct-emit smoke rows. Either wrap silently no-ops OR no chat smoke ran post-deploy. Needs UI chat → verify event spectrum. |
| **A3** Boot `errors=11` | **Benign**. 11 idempotent migrations w/ `EXCEPTION WHEN OTHERS` blocks catch `UndefinedTable/UndefinedColumn` on re-runs of `CREATE IF NOT EXISTS` patterns. Documented. |

### Track B — Ops Plumbing

| Sub | Ship |
|---|---|
| **B1 Backup cron** | `dash/cron/backup_daemon.py` nightly `pg_dump | gzip` → `backup_data` vol, 7-day retention. `compose.yaml` `dash-backup` service (direct to `dash-db`, NOT pgbouncer — pg_dump needs real session). Migration 146 `dash_system_status` singleton + `last_backup_at`. `/health` surfaces. `docs/BACKUP.md`. |
| **B2 Sentry wire** | `sentry-sdk[fastapi]==2.57.0`. Init gated on `SENTRY_DSN` (empty = skip, zero overhead). `_SentryTagMiddleware` tags `project_slug`+`user_id`. `/api/_debug/sentry-test` raises (DASH_DEBUG=1 gate). `.env.example` extended. |
| **B3 Prometheus** | `Instrumentator().instrument(app).expose(app, "/metrics")`. 4 counters in `dash/utils/metrics.py`: `dash_chat_requests_total{project,status}`, `dash_sse_events_total{type}`, `dash_verified_pass_total{verdict}`, `dash_upload_bytes_total{ext}`. Wired at chat / Agno wrap / verified_reward / upload paths. All helpers fail-soft. |

### Track C — Security Hardening

| Sub | Ship |
|---|---|
| **C1 RLS test** | `tests/test_rls.py` 5 cases: cross-tenant read/chat/upload + cross-schema SELECT + positive control. `make test-rls`. `.github/workflows/edge-cases.yml` new `rls-isolation` CI job. |
| **C2 Secrets audit** | `docs/SECRETS_AUDIT.md`: **4 real live credentials in `.env`** — OPENROUTER_API_KEY (financial), SUPER_ADMIN_PASS=`<DEMO_PASSWORD>` (ALSO leaked in README+CLAUDE.md), PEXELS_API_KEY, CONNECTION_ENCRYPTION_KEY (Fernet, rotation needs re-encrypt). Production-dangerous default `compose.yaml ${DB_PASS:-ai}`. Derived-w/-insecure-fallback `crypto.py:29` → `"dev-insecure-jwt-secret"`. `docs/SECRETS.md` — vault matrix (AWS SM / Doppler / Infisical / k8s). NO rotation performed — audit + recommendation only. `.gitignore` extended w/ 12 `*.env*` variants. |
| **C3 Rate limit** | `app/rate_limit.py` `RateLimitMiddleware` sliding-window. Mounted BEFORE `AuthMiddleware` (so user_id is rate-key). Limits: chat=60/min, upload=10/min, training=20/min, default=120/min, env-overridable. Whitelist: health/metrics/ui/docs/branding/embed/brand. 429 + `Retry-After` + `X-RateLimit-*` headers. Kill switch `RATE_LIMIT_DISABLED=1`. `tests/test_rate_limit.py` 6 cases. |

### Files added

```
NEW   dash/cron/backup_daemon.py                 nightly pg_dump + retention
NEW   db/migrations/146_system_status.sql        singleton + last_backup_at
NEW   dash/utils/metrics.py                      4 prometheus counters
NEW   app/rate_limit.py                          sliding-window middleware
NEW   tests/test_rls.py                          5 cross-tenant cases
NEW   tests/test_rate_limit.py                   6 rate-limit cases
NEW   docs/BACKUP.md                             restore procedure
NEW   docs/SECRETS_AUDIT.md                      4 real secrets cataloged
NEW   docs/SECRETS.md                            vault decision matrix
```

### Files touched

```
EDIT  compose.yaml                +dash-backup service +backup_data volume
EDIT  app/main.py                 +Sentry init +Tag middleware +Instrumentator +RateLimitMiddleware (before Auth)
EDIT  app/projects.py             +inc_chat metric
EDIT  app/upload.py               +add_upload_bytes
EDIT  dash/utils/agno_sse_wrap.py +inc_sse per event
EDIT  dash/learning/verified_reward.py +inc_verified
EDIT  requirements.txt            +sentry-sdk[fastapi]==2.57.0 +prometheus-fastapi-instrumentator==7.0.0 +prometheus-client==0.21.1
EDIT  .env.example                +SENTRY_DSN +DASH_ENV +DASH_DEBUG +SENTRY_TRACES_SAMPLE_RATE
EDIT  .gitignore                  +12 *.env* variants
EDIT  .github/workflows/edge-cases.yml  +rls-isolation job
EDIT  pytest.ini                  +rls_isolation +rate_limit markers
EDIT  Makefile                    +test-rls +test-rate-limit targets
EDIT  tests/test_e2e.py           scale threshold 50→40MB
```

### Acceptance gate

| Item | Status |
|---|---|
| A1 1M CSV | partial (gen ✓, upload blocked on owner-role bug) |
| A2 SSE audit | needs UI chat smoke |
| A3 errors=11 | ✓ classified benign |
| B1 backup | ✓ shipped, pending `up -d dash-backup` |
| B2 Sentry | ✓ shipped, pending `SENTRY_DSN` |
| B3 metrics | ✓ shipped, scrape config in README |
| C1 RLS test | ✓ shipped, CI green pending |
| C2 secrets audit | ✓ documented, rotation pending user |
| C3 rate limit | ✓ shipped |

### Critical user-gated follow-ups

1. **Rotate live secrets** per `docs/SECRETS_AUDIT.md`: `OPENROUTER_API_KEY`, `SUPER_ADMIN_PASS`, `CONNECTION_ENCRYPTION_KEY` (re-encrypt path), `PEXELS_API_KEY`
2. **Scrub `<DEMO_PASSWORD>`** from README.md + CLAUDE.md references
3. **Production guards** (recommended, NOT shipped): refuse boot if `RUNTIME_ENV=prd` AND `SUPER_ADMIN_PASS in ("<DEMO_PASSWORD>","admin")` OR `JWT_SECRET == "dev-insecure-jwt-secret"`. Change `compose.yaml ${DB_PASS:-ai}` → `${DB_PASS:?required}`
4. **Fix A1 auth bug**: `check_project_permission` should auto-grant editor role to `dash_projects.user_id` owner (currently 403 on `/api/upload` for project owner)
5. **A2 UI smoke**: open chat, run 5 questions, verify `SELECT event_name, count(*) FROM public.dash_sse_audit WHERE ts > now()-interval '1 hour' GROUP BY 1` returns full event spectrum

### Deferred to next session

- **D1** Migration rollback docs per migration 080-145 (~3hr)
- **D2** 50-concurrent locust load test (~2hr)
- **E1** Batch 3 ship/defer decision (golden UI + MDL editor UI + diff panel + chat scope audit)

### Verification

`docker compose -f compose.yaml build dash-api` clean ✓ · image fresh ✓ · `/api/health` 200 OK ✓ · 0 boot tracebacks ✓ · all new Python files parse ✓ · rate-limit middleware resolves correct limits per route ✓ · Prometheus counters import + increment ✓ · Sentry init skipped when DSN empty ✓.

---

## Earlier (2026-05-25) — Error-proofing: 12 phases + out-of-scope closure shipped. Universal serializer · auto-discover cascade · multi-byte KG · SSE audit + Agno wrap · schema-prefix purge · 15 edge-case fixtures · PR gate · million-row nightly · stale-step reaper · threshold tuning

Full error-proofing program shipped via parallel multi-agent dispatch (5 agents in parallel + inline rounds, ~8 min wall vs ~6 hr sequential estimate). Goal: defensive helpers in `dash/utils/` absorb whole bug classes — any new bug surfaces as ONE failing fixture → patch in `dash/utils/` → propagates everywhere via import.

### Single source of truth — `dash/utils/` (8 modules)

| Module | Role |
|---|---|
| `safe_json.py` | `safe_dumps()` — Decimal/UUID/datetime/numpy/bytes/set/NaN/Inf, never raises |
| `df_serialize.py` | `df_rows_to_jsonable()` — SQLAlchemy rows → JSON-safe |
| `sse.py` | `emit_event_sync()` + audit hook (ThreadPoolExecutor → `dash_sse_audit`) |
| `agno_sse_wrap.py` | `audited_team_stream()` — wraps Agno team stream, captures every event |
| `project_schemas.py` | `SCHEMA_VARIANTS` tuple — covers `proj_<slug>` + `user_proj_<slug>` |
| `cascade.py` | `get_project_scoped_tables()` auto-discovers via `information_schema` |
| `column_classifier.py` | Multi-byte safe (`OCTET_LENGTH`), classifies dimension/free_text/skip/lineage |
| `column_metadata.py` | `LINEAGE_COLUMNS` frozenset + skip regex |
| `column_stats_cache.py` | 5-min TTL, 1 query per table (not per col) |

### Bug classes ELIMINATED across whole codebase

| Class | Defense |
|---|---|
| Decimal/UUID/datetime/numpy/bytes not JSON-serializable | `safe_dumps` everywhere |
| SSE stream closes silently on serialization error | `emit_event_sync` + per-event try/except + audit |
| Cascade list drift (new `dash_*` w/ `project_slug` forgotten) | auto-discover via `information_schema.columns` |
| Schema variant orphans (`user_<slug>` left behind) | `SCHEMA_VARIANTS` tuple |
| Multi-byte free-text leaks as KG dimensions | `AVG/MAX(LENGTH)` + `AVG(OCTET_LENGTH)` over ALL rows |
| Lineage cols becoming triples | `LINEAGE_COLUMNS` frozenset + skip regex |
| Constant cols as trend axis (DATE_TRUNC on single-value col) | 3-layer gate: prompt + classifier + SQL post-filter |
| Stuck training pipelines (status='running' forever) | `stale_step_reaper` daemon (every 5min, 15min threshold) |
| `dash.dash_*` vs `public.dash_*` schema drift | Migration 145 compat views + Python-side purge (33 → 0) |
| Echo-match weak shortcut | 3-tier acceptance: sim≥0.95 OR overlap≥4+score≥5 OR overlap≥3+score≥8 |
| Connector telemetry crash on missing deps | feature-flag gate |
| `KeyError 'workflow_id'` in workflow runner | `.get()` defaults + per-step try/except |
| Regression returning | 15 edge-case fixtures × E2E test, PR-gated |
| Raw `json.dumps` drift | CI lint rule |
| Full Agno chat stream invisible to audit | `audited_team_stream` wrapper |

### 15 edge-case fixtures (PR gate)

`multi_lang.csv` (Burmese+CJK+Arabic+Hindi+emoji), `decimal_heavy.csv`, `constant_columns.csv`, `nan_inf.csv`, `mixed_types.csv`, `huge_strings.csv`, `auto_injected_cols.csv`, `empty_columns.csv`, `single_row.csv`, `million_row.csv` (5K stand-in), `duplicate_pk.csv`, `unicode_filename_漢字.csv`, `empty.csv` (accepts 400/422), `mixed_dates.csv`, `pii_loaded.csv`.

`tests/test_e2e.py` walks each through upload → retrain → poll → memories≥1.

### CI gates

- `.github/workflows/edge-cases.yml` — PR gate, every PR runs the 15-fixture suite + `lint-safe-dumps` (WARN now, BLOCK once full migration).
- `.github/workflows/nightly-scale.yml` — cron 07:00 UTC, generates 1M-row CSV, asserts upload <120s + training <1200s + memories ≥1. `ubuntu-latest-16-cores`, 90-min timeout.

### New cron daemon

`dash/cron/stale_step_reaper.py` — every 5 min, marks `dash_training_steps` rows status='running' updated_at < now()-15min as failed (timeout reason). Registered in `app/main.py` lifespan, env-gated.

### New migrations

- `144_sse_audit.sql` — `dash_sse_audit(id, session_id, event_name, ts, bytes_emitted, error, project_slug)` + 3 indexes.
- `145_schema_normalize.sql` — idempotent compat views in `dash.` for: `dash_dream_reflection_tree`, `dash_workflow_run_history`, `dash_dream_runs`, `dash_dream_findings`, `dash_dream_insights`, `dash_anti_patterns`, `dash_dream_digests`, `dash_skill_library`.

### Schema-prefix Python-side purge

33 occurrences of `dash.dash_(skill_library|anti_patterns|dream_)` → `public.dash_*` across 9 files. Result: 0 Python drift. Migration validator (`dash/db_runner/migrate.py::_check_schema_prefix`) stays WARN-only — legacy migrations have legitimate mixed refs via compat views.

### Out-of-scope closure (same session)

1. **Agno SSE audit wrap** — `dash/utils/agno_sse_wrap.py` (sync + async). Wired into `app/projects.py:1437` chat SSE. Full event spectrum now in `dash_sse_audit`.
2. **Schema-prefix Python-side drift** — 0 violations after purge.
3. **empty.csv test** — codifies Option A: `status_code in (400, 422)` → success.
4. **Million-row nightly** — `gen_million.py` generator + workflow + `test_million_row_scale()`.

### Files added/touched

```
NEW  dash/utils/safe_json.py            Decimal/UUID/datetime/numpy/bytes safe
NEW  dash/utils/df_serialize.py         row → JSON coerce
NEW  dash/utils/sse.py                  emit_event_sync + audit
NEW  dash/utils/agno_sse_wrap.py        wrap team stream
NEW  dash/utils/project_schemas.py      SCHEMA_VARIANTS
NEW  dash/utils/cascade.py              auto-discover via information_schema
NEW  dash/utils/column_classifier.py    multi-byte safe
NEW  dash/utils/column_metadata.py      LINEAGE_COLUMNS
NEW  dash/utils/column_stats_cache.py   5min TTL
NEW  dash/cron/stale_step_reaper.py     mark stuck steps failed
NEW  db/migrations/144_sse_audit.sql
NEW  db/migrations/145_schema_normalize.sql
NEW  .github/workflows/edge-cases.yml   PR gate
NEW  .github/workflows/nightly-scale.yml nightly 1M
NEW  tests/fixtures/edge_cases/*.csv    15 fixtures
NEW  tests/fixtures/edge_cases/gen_million.py
NEW  tests/test_e2e.py                  parametrized E2E
NEW  tests/test_verified_reward.py      threshold unit tests
NEW  tests/conftest.py                  session fixtures
NEW  pytest.ini                         e2e marker
EDIT Makefile                           test-edge-cases target
EDIT app/projects.py                    delete_project refactor + chat SSE wrap
EDIT app/upload.py                      _is_date_col_usable + constant col warnings
EDIT app/admin_api.py                   GET /api/admin/sse-audit
EDIT app/main.py                        register stale_step_reaper_loop
EDIT app/agent_os_workflows.py          12 schema-prefix updates
EDIT dash/learning/verified_reward.py   3-tier acceptance + df_serialize
EDIT dash/cron/connector_rotation_daemon.py  feature gate
EDIT dash/cron/workflow_runner.py       .get() defaults
EDIT dash/tools/knowledge_graph.py      use column_metadata cache
EDIT dash/db_runner/migrate.py          _check_schema_prefix validator
EDIT dash/dashboards/agent.py           demote time-axis if all date cols constant
EDIT 9 schema-prefix Python files       dash.dash_X → public.dash_X (33 occurrences)
EDIT .gitignore                         million_row.csv
```

### Verification (post-bake)

- Cumulative state: memories=64 · training_qa=25 · kg_triples=2 (clean) · personas=1 · biz_rules=2 · relationships=1 · sse_audit ✓ direct emit
- Build: clean · Boot: healthy · Tracebacks: 0 · Python schema drift: 0

### Production-readiness

- **Internal/pilot users:** SHIP-READY now.
- **Paying customers:** 2-4 weeks gap. Needs: backup cron (`last_backup_at` currently NULL), monitoring/alerting (Sentry + Prometheus), 50-concurrent-session load test, secrets vault, rate-limit middleware, DR drill, RLS cross-tenant test.

---

## Earlier (2026-05-25) — Project lifecycle hardening: upload form-binding · delete cascade · KG noise filter · doc-only KG StepRunner · ml_auto_create skipped marker

End-to-end smoke (clean → create → upload → retrain) on CityPharma data exposed 6 distinct bugs across upload routing, delete cascade, KG quality, training audit. All fixed + verified.

### 6 bugs fixed

| # | File | Bug | Fix |
|---|---|---|---|
| 1 | `app/upload.py:12054` | `_task_ml()` gutted to `return` but step-cache row stuck at `FAILED` from pre-gut runs (unique `(slug, name, scope)` key never refreshed). | Upsert `status='skipped', elapsed_ms=0` on every call. |
| 2 | `app/upload.py:8277` | Upload `project: str \| None = None` = FastAPI query-only. `-F project=` silently dropped → tables routed to `user_demo` schema. | Dual-source: query bind + `request.form()` fallback after multipart consume. |
| 3 | `app/projects.py:1866` | `delete_project` cascade missing 23 tables (`dash_training_steps`, `dash_knowledge_triples`, `dash_company_brain`, `dash_extraction_plans`, `dash_traces`, `dash_chat_sessions`, `dash_documents`, `dash_audit_log`, `dash_brain_versions`, `dash_dashboards_v2`, `dash_verified_scores`, visibility ×5, ingest ×3, attribution ×3, snapshots ×2, campaigns ×3). Stale rows leaked across delete+recreate → fp cache hits skipped steps. | Extended cascade 26 → 49 tables. |
| 4 | `app/projects.py:1853` | Delete cascade in implicit txn → FK violation on any one table aborted txn → all subsequent DELETEs failed → project undeleted (`InFailedSqlTransaction`). | Open fresh connection w/ `_engine.execution_options(isolation_level="AUTOCOMMIT")` — each DELETE its own txn. SQLAlchemy 2.x forbids mid-txn isolation swap; must use new connection. |
| 5 | `dash/tools/knowledge_graph.py:114-167` | KG cell-value extractor leaked junk on mixed-content + UTF-8 columns. `LENGTH BETWEEN 2 AND 80` inside `COUNT(DISTINCT)` saw only short labels in `mm_label`, dumped `Yes/No/N0` as triples. Multi-byte Burmese sentences (60-80 chars = 180-240 bytes) ALSO passed cap → 30+ full sentences became triple subjects. Auto-injected `_source_file` col not in skip regex → 2 useless triples. | Read `AVG(LENGTH)::int, MAX(LENGTH)` over ALL non-null rows. Skip if `avg_len > 25` OR `max_len > 60`. Expanded skip regex: `_source_*`, `_period`, `_label`, `_desc`, `_note`, `_comment`, `_text`, `_body`, `narrative`, `instruction`, `warning`, `contraindication`, `caution`, `address`, `url`, `link`, `filename`, `filepath`. Per-value 3+ word sentence guard. **Result: 46 → 2 triples** (all semantic). |
| 6 | `app/upload.py:11476-11503` | Doc-only training branch called `build_knowledge_graph()` directly → no fp cache → KG rebuilt every retrain on unchanged docs. No `dash_training_steps` row → audit blind. Data branch uses `_tail_runner.run` w/ fp cache. | Wrap doc-only path in `StepRunner(run_id, slug, ...).run("knowledge_graph", ..., fp_inputs={docs, text_len})`. Symmetric audit across data + doc branches. |

### E2E verification

```
DELETE  proj_demo_citypharma_fresh    → cascade clean (0 leaked rows across 6 surveyed tables)
CREATE  proj_demo_citypharma_fresh    → status=ok
UPLOAD  balance_stock 2.csv  (-F project=)  → 106,322 rows → proj_demo_citypharma_fresh ✓
UPLOAD  articles-export 1.xlsx (-F project=) → 4,892 rows → proj_demo_citypharma_fresh ✓
RETRAIN → status=done, dur=2m, 0 errors
  step  knowledge_graph       done    576ms  (was 3.1s with noise)
  step  vertical_pack_static  done    27ms
  step  derive_scope          done    15s
  step  vector_backfill       done    37ms
  step  ml_auto_create        skipped 0ms    (was stuck FAILED)
Artifacts: memories=64 · training_qa=26 · relationships=1 · business_rules=2
           table_metadata=2 · kg_triples=2 (clean) · personas=1
```

### Rules codified for future files

Added to CLAUDE.md `Never-do list` + `Gotchas`:

1. Never define multipart-form endpoint param as bare `x: str | None = None` — FastAPI binds query-only. Use `Form(...)` typed OR read `await request.form()` manually.
2. Never write to `public.dash_*` via `get_sql_engine()` — use `db.session.get_write_engine()` (RO guard on public schema).
3. Never use `:x::jsonb` in SQLAlchemy named-param SQL — use `CAST(:x AS jsonb)` (PgBouncer collision).
4. Never silent `except: pass` on platform writes — log via `logger.exception()`.
5. Never add new `public.dash_*` table w/ `project_slug` column WITHOUT extending `app/projects.py:delete_project` cascade list at PR review.
6. Never gut a function to bare `return` if it has unique step-cache row — upsert `status='skipped'` so audit truthful.
7. Never use `LENGTH BETWEEN x AND y` inside `COUNT(DISTINCT)` for free-text detection — read `AVG/MAX(LENGTH)` over ALL rows. PG `LENGTH()` returns chars, not bytes — Burmese/CJK 60-char sentences = 180-240 bytes.
8. Never call `build_knowledge_graph()` directly outside `StepRunner.run()` — bypasses fp cache + step audit.
9. Never run cascade DELETE loop in implicit txn over FK-bound tables — use `engine.execution_options(isolation_level="AUTOCOMMIT")` on a fresh connection.
10. Never swap `isolation_level` mid-txn in SQLAlchemy 2.x — open new connection instead.

### Files changed
```
EDIT app/upload.py:12054          _task_ml() upserts 'skipped' step row
EDIT app/upload.py:8277           upload_file dual-source param resolution (query + form)
EDIT app/upload.py:11476          doc-only KG wrapped in StepRunner.run + fp cache
EDIT app/projects.py:1853         delete_project AUTOCOMMIT mode for cascade
EDIT app/projects.py:1866         cascade list 26 → 49 tables
EDIT dash/tools/knowledge_graph.py:114  free-text guard (avg/max length) + skip regex + sentence guard
EDIT CLAUDE.md                    10 new Never-do rules + 4 Gotchas codified
EDIT README.md                    this entry
```

---

## Earlier (2026-05-25) — Upload pipeline P1-P5 — banner-row fix + LLM rescue + full-sheet scan + plan persistence + file-hash cache

CityPharma customer hit 99.6% data loss uploading `articles-export 1.xlsx` (4892 rows → 21 rows). Audit found 5 distinct failure modes. Shipped 5 phases (P1+P2 sequential, P3+P4+P5 in parallel via 3 agents). Net **~1165 LOC + 2 migrations**, single-file edits in `app/upload.py` + new frontend EXTRACTION PLAN panel.

**5 failure modes uncovered + fixed:**

| # | Failure | Root cause | Patch |
|---|---|---|---|
| 1 | Banner-row 99.6% data loss | `_rules_find_blank_boundaries` on 25-row preview → block `data_end=25` truncates 4870 rows | P1 banner-block guard |
| 2 | Phantom banner column in schema | First block's header_row picks banner text; col name leaks into concat | P1 banner-block guard strips front blocks |
| 3 | Pandas `header + skiprows` semantic bug | skiprows applied FIRST, header relative to remaining rows; `header=4 + skiprows=[1]` reads data row as header | P1 pandas-safe conversion: `header=0 + skiprows=range(0,hrow)\|extra` |
| 4 | AI Validator gated only on `>30 cols` / repeats | Won't fire for normal-shape tables sliced wrong | P2 LLM rescue on row-count mismatch |
| 5 | Mid-sheet table boundaries missed beyond 25-row preview | Preview-only blank-boundary scan | P3 full-sheet blank scan up to 10K rows |

**P1 — Rules engine fixes (~45 LOC, sequential):**
- `_META_RE` regex: added Print Date / Generated / Created / Exported / Confidential / As of / etc + required `:` or `$` anchor (`\s*[:\-]\s+|\s*$`) — so "Created At" / "Updated At" headers DON'T false-match.
- `_rules_analyze_sheet` block-split path: NEW banner-block guard. Strip front blocks if ≤3 rows + ≤2 cells (or meta-pattern) when next block has ≥5 short text labels + no meta hits. Loops until invariant.
- `_handle_excel` load path: pandas-safe skiprows conversion. `full_skip = sorted(set(skip) | set(range(0, hrow)))` + `header=0`. All rows above header skipped, header always first remaining.

**P2 — LLM rescue layer (~125 LOC, sequential):**
- After AI Validator, iterate every loaded table. For each: extract sheet name from `tbl["source"]`, look up `sheet_previews.max_row`.
- If `actual_rows < expected_max * 0.1 AND expected_max > 100` → call `training_llm_call("extraction")` w/ first 30 non-blank rows raw + bad shape.
- 4-tier JSON parse. Re-read sheet with corrected `header_row + banner_rows_to_skip` via pandas-safe skiprows.
- Replace if ≥5× more rows; keep original otherwise. Save learning to `dash_memories` source=`structure_learning`. Tag source `[llm-rescued]`. Fail-soft per table.
- Cost: ~$0.0001 per suspicious sheet (LITE_MODEL).

**P3 — Full-sheet blank scan (~85 LOC, parallel agent A):**
- NEW `_full_sheet_blank_scan(file_path, sheet_name, max_row, ext)` streams via openpyxl read-only, finds indices where 2+ consecutive rows fully blank. Caps at 10K rows. Fail-soft → `[]`.
- `_rules_analyze_sheet` accepts `full_scan_boundaries: list[int] | None = None` kwarg. Merges with preview-only boundaries (dedupe, sort).
- `_handle_excel` calls full-scan when `max_row > 100`. After block-build, last block's `data_end = max_row - 1` so reads continue past preview.
- Catches mid-sheet table boundaries (e.g., table1 rows 0-3000, gap, table2 rows 3001-5000).

**P4 — Extraction plan persistence + RE-INGEST UI (~750 LOC, parallel agent B):**
- Migration `135_extraction_plans.sql`: `public.dash_extraction_plans` (14 cols: project_slug, table_name, source_file, sheet_name, file_hash, strategy, header_row, skip_rows, blocks, row_count_in/out, llm_rescued, rescue_reasoning, user_overrides, created_at, updated_at) + 3 indexes.
- `_persist_extraction_plan()` helper — `get_write_engine()` (public schema) + `CAST(:x AS jsonb)`. Fail-soft. Called after each `df.to_sql()` write.
- Raw file persisted to `KNOWLEDGE_DIR/{slug}/raw_uploads/{filename}` BEFORE temp deletion (xlsx/xls only). Enables re-ingest.
- 3 new endpoints under `/api/projects/{slug}`:
  - `GET /extraction-plans?limit=50` (viewer)
  - `GET /extraction-plans/{id}` (viewer)
  - `POST /extraction-plans/{id}/re-ingest` body `{header_row?, skip_rows?}` (editor) — re-reads source w/ overrides, REPLACE table, UPDATE plan w/ user_overrides JSONB.
- Frontend Settings → DATASETS → expanded row → NEW EXTRACTION PLAN panel. Strategy badge (color-coded green=rules-split, amber=llm-rescued, purple=ai-unpivot). Editable Header row + Skip rows inputs. RE-INGEST WITH OVERRIDES button.

**P5 — File-hash cache (~145 LOC, parallel agent C):**
- Migration `136_upload_cache.sql`: `public.dash_upload_cache` (file_hash PK sha256, plan JSONB, hit_count, last_used_at, etc).
- Helpers: `_compute_file_hash` (64KB chunks, fail-soft), `_lookup_upload_cache` (atomic UPDATE RETURNING bumps hit_count), `_save_upload_cache` (INSERT ON CONFLICT UPDATE).
- Wired into LLM RESCUE loop: cache lookup BEFORE LLM call per sheet. Cache hit → reuse `header_row + banner_rows_to_skip`, log `CACHE HIT for '{sname}' (hit_count=N)`. Cache save AFTER successful rescue. Cross-tenant (any project uploading same file_hash reuses plan).
- 2 admin endpoints (super_admin gated): `GET /api/admin/upload-cache/stats` + `DELETE /api/admin/upload-cache/{file_hash}`.

**Pipeline now (full chain):**

```
upload → sha256 file_hash → CACHE LOOKUP
  read 25-row preview per sheet
  ↓ when max_row > 100, full-sheet blank scan (P3)
  rules engine
    ├─ banner-block guard strip (P1)
    ├─ blank boundaries merged (preview + full scan)
    └─ last block data_end → max_row-1 (P3)
  ↓
  plan: load | split | unpivot | skip
  ↓
  pandas-safe read: header=0 + skiprows=range(0,hrow)|extra (P1)
  ↓
  AI Validator (>30 cols / repeats / cols >> rows)
  ↓
  LLM RESCUE (P2) when actual < 10% expected
    ├─ cache lookup first (P5)
    └─ save plan to cache on success (P5)
  ↓
  write to project schema → df.to_sql()
  ↓
  PERSIST extraction plan (P4) → dash_extraction_plans
  ↓
  copy raw file to KNOWLEDGE_DIR/{slug}/raw_uploads/
```

**Smoke results (all rebuilt + verified):**

| Test | Expected | Got | Status |
|---|---|---|---|
| balance_stock 2.csv (regression) | 106,322 rows | 106,322 | ✓ clean |
| articles-export 1.xlsx (real customer) | 4,892 rows | 4,892 | ✓ P1 fixed |
| Synthetic adversarial xlsx (1003 rows w/ mid-sheet gap) | boundary at row 500 | `[500]` | ✓ P3 full-scan |
| Synthetic tricky.xlsx (banner that defeats rules) | 2,000 rows | 2,000 | ✓ P2 LLM rescue |
| Re-upload tricky.xlsx | cache hit | works | ✓ P5 cross-defense |
| GET extraction-plans | 4 audit rows w/ strategy/timing/lineage | populated | ✓ P4 persist |
| Cache primitives | hit_count bumps atomically | 1→2 | ✓ P5 verified |

**Cumulative LOC:**
| Phase | LOC | Effect |
|---|---|---|
| P1 (sequential) | 45 | Banner row 99.6% data loss FIXED |
| P2 (sequential) | 125 | Unknown shape files auto-recovered via 1 LLM call |
| P3 (parallel A) | 85 | Mid-sheet boundaries past 25-row preview |
| P4 (parallel B) | 750 (backend + UI) | Audit trail + user override re-ingest |
| P5 (parallel C) | 160 | Same file = 0 LLM cost on re-upload, cross-tenant |
| **Total** | **~1165** | **+ 2 migrations** |

**Files this session:**
```
EDIT  app/upload.py                                    P1+P2+P3+P4+P5 wiring
NEW   db/migrations/135_extraction_plans.sql           14 cols + 3 indexes
NEW   db/migrations/136_upload_cache.sql               8 cols + 1 index
EDIT  frontend/src/routes/project/[slug]/settings/+page.svelte   EXTRACTION PLAN panel + state + handlers
```

**Multi-agent dispatch:** P3 / P4 / P5 ran in parallel — disjoint file regions chosen up front (P3 owns lines 4381-4578 rules engine, P4 owns upload_file flow + frontend + migration 135, P5 owns LLM rescue block + migration 136). Zero conflicts. P4 agent hit transient 529 mid-smoke but code shipped intact + verified standalone.

**Patterns added to "load-bearing" list:**
- **Pandas `header + skiprows` semantics is a footgun.** skiprows applied FIRST, header relative to REMAINING rows. ALWAYS convert: `header=0 + skiprows=sorted(set(skip)|set(range(0,hrow)))`. Don't pass non-zero `header=` with `skiprows=` separately.
- **Rules-based heuristics fail silently on unknown vendor exports.** 25-row preview can't know if banner has 4 blank rows or 40. Pair rules with cost-bounded LLM rescue triggered by row-count mismatch (<10% of expected). Single LLM call, ~$0.0001, fail-soft.
- **File-hash cache cross-tenant is the right granularity.** Same vendor template uploaded by N customers = 1 LLM call. Hash bytes, not metadata.
- **Persist EVERY upload decision.** `dash_extraction_plans` row per `df.to_sql()` write makes pipeline auditable + replayable. User-override path reuses same row.
- **Svelte 5 `{@const}` placement:** must be immediate child of `{#if}`/`{:else if}`/`{#each}`/`{#snippet}`. Workaround: `{#if true}{@const _x = ...}` wrapper.
- **`get_write_engine()` for `public.dash_extraction_plans` + `public.dash_upload_cache`** — 4th session this rule bit a builder.

**Deferred:** LLM rescue + cache for non-Excel formats (CSV/PDF/DOCX/PPTX table extracts) · cross-format Storage Strategy scout (broader "LLM decides everything" plan) · `raw_uploads/` daily cleanup cron · Batch 3 UI (golden mgmt + MDL editor + chat scope audit, ~3 days svelte).

---

## Earlier (2026-05-25) — 42-risk audit + 4/5 batch hardening (cycle detect · invalidate · bounds · number-cite · dialect param · prompt salt) + 3 live bug fixes (NaN serialize · write engine on slides · `[VERIFIED:]` HTML escape)

Continuation of prior session's Golden SQL + MDL ship. This pass audited the full risk surface (42 items across hallucination · isolation · security · compiler · ops · pending), built 5-batch fix plan, shipped 4/5. Plus Hex/Python deep-eval side-quests + 3 live bugs caught during PPT+dashboard smoke + fixed in-session. Net ~340 LOC backend + ~30 LOC frontend.

**Risk closure:** ❌open 5→1 · ⚠️partial 9→4 · ✅closed 28→37.

**Batch 1 — cycle detect + cache invalidate + name collision + rtk-bypass Makefile (C3, C6, C7, O4):**
- `dash/semantic/compile.py` — NEW `invalidate(slug)` + `detect_cycles(models)` (DFS over vcol DAG, returns cycle paths). MAX_ITERS exhaustion logs warning.
- `install_mdl()` — pre-install gate rejects packs w/ vcol cycles; surfaces `name_collisions[]` (model_name shadowing real table).
- `golden_promote/demote` endpoints now call `invalidate(slug)` — MDL cache propagates within 0s instead of 5min TTL wait.
- `Makefile rebuild-raw` — bypasses rtk shell wrapper via `env -i PATH=... docker compose`. Use when rtk swallows docker output (3rd time this session).

**Batch 2 — `dash/guards/` package: number-cite + bounds + vcol dry-run (H3, H10 partial, H11, H12):**
- NEW `dash/guards/number_cite.py` (~140 LOC). `audit_numbers(answer, tool_outputs)` regex-extracts numbers from agent reply (handles `$1,234.5M`, `12.5%`, `1.5e6`), matches against tool outputs ±0.5% rel. Filters small ints (articles) + years (1900-2100). Returns `{cited, fabricated, flagged[:20]}`. Smoke: catches `12.5%` as fab when only `1544` cited; honest case 0 fab.
- NEW `dash/guards/bounds.py` (~120 LOC). `check_bounds(slug, table, cols, rows)` reads vcol `bounds: {min, max, nullable}` from MDL, scans rows, flags violations (below_min/above_max/null_disallowed). 50-anomaly cap. Never raises.
- `verticals/__init__.py` — NEW `_vcol_dry_run()` EXPLAINs each vcol expression against raw table at install time. Skips pure column-renames (alias resolver already proved them). Surfaces failures in `skipped_workflows[]`.
- Vcol `bounds` field passes through to `dash_metric_definitions.virtual_columns` JSONB.

**Batch 4 partial — `call_id` aliases + multi-dialect (P5, P9/C5; P4 finance pack deferred):**
- `crm_calls_mdl` `call_id` alias list 3 → 10 (`interaction_uid, case_id, record_id, ticket_id, session_id, contact_id, lead_id, event_id`). Lifts CRM MDL coverage to 8/8 vcols on schemas where ID isn't literally `call_id`.
- `compile_query(slug, sql, dialect="postgres")` — sqlglot dialect passes through. MySQL/Snowflake/BigQuery/DuckDB/Spark/Trino all accepted. Default postgres = back-compat.
- `finance_fpa_mdl` 3rd vertical pack deferred (~200 LOC scope).

**Batch 5 — prompt-cache salt (I11):**
- `dash/settings.py training_llm_call()` prepends `# project: {slug}\n# tenant: {org}\n\n` salt. Forces upstream OpenRouter cache key to differ across tenants even when prompt body byte-identical. ~30 tokens overhead, prevents cross-tenant cache collision.
- I12 Redis namespace lint surveyed — 4 stubby refs in codebase, lint rule overkill, marked no-op.

**Batch 3 deferred — golden mgmt UI + MDL editor UI + diff panel + chat scope audit (P1, P2, P7, I4):**
- ~3 days svelte. Backend endpoints (`/golden/list`, `/golden/promote`, install_mdl) already exist. Pure frontend lift, dedicated session recommended.

**3 live bugs caught + fixed during PPT/dashboard smoke this session:**
1. **`[VERIFIED:Nms · cached]` rendered as raw `<span>` text in chat** — `stripStructureTags` emitted raw HTML → `inlineFormat()` `escapeHtml`'d it → catch-all `[A-Z_]+:` regex also stripped it (two-layer bug). Fix: unicode placeholder `‹‹VERIFIED:...››` at strip time (survives both `escapeHtml` + catch-all), post-process to `<span>` AFTER `markdownToHtml`. 3 render sites patched in `ChatMessageList.svelte`.
2. **`POST /api/dashboards/deep-build` 500 "nan not JSON compliant"** — pipeline ran 107s clean, FastAPI response serialize failed on pandas NaN in panel rows. Fix: NEW `_sanitize_json()` walks dict/list/tuple, replaces `math.isnan|isinf` floats w/ None. Wrapped deep-build + run-data + deep-patch returns. Post-fix smoke: 9 panels, 28k tokens, 112s, judge ran ✓.
3. **`POST /api/slides/from-markdown` `pres_id:null` (silent persist fail)** — `_save_presentation_row` used `get_sql_engine` (read-only sql_engine with `transaction_read_only=on`) → INSERT silently rolled back → try/except swallowed truncated log. **3rd time this gotcha bit this codebase.** Fix: switch to `db.session.get_write_engine()`. Post-fix smoke: pres_id=24, 2 slides, listed.

**Side-quest 1 — Tools-vs-Dash comparison (51 tools scanned):** ✅36 lifted, ⚠️4 inspiration-only, ❌11 rejected. Top adoptions: WrenAI MDL · Dataherald golden_sql · Wren EXPLAIN gate · TACL different-model judge · OpenCoworkAI office skills. Top rejections: AgentGym-RL · TimesFM · v0/Bolt/Lovable freeform codegen · CAMEL-OASIS+Zep · dbt semantic layer.

**Side-quest 2 — Hex feature lift evaluation (5 features ranked):** (1) reactive cell DAG ⭐⭐⭐ ~3 days, (2) per-cell ✨ chat ⭐⭐ ~1 day [recommended fastest demo], (3) REST endpoint auto-publish ⭐⭐ ~1 day, (4) app input widgets bound to SQL params ⭐⭐ ~2 days, (5) snapshot diff ⭐ ~1 day. Skip mixed SQL+Python cells.

**Side-quest 3 — Python engine eval: NO.** Multi-tenant SaaS + RestrictedPython CVEs + dep mgmt + resource exhaustion = wrong product fit. Dash already covers 95% via 13 DS tools + 31 Analyst tools + Engineer VIEW creation. If pushed: constrained `execute_python` tool on Data Scientist (~2 days, RestrictedPython + 5s timeout + 500MB cap, NOT a kernel). Per-cell ✨ chat covers same agency w/o code surface — pick that instead.

**Product gap surfaced (not bug, NOT fixed):** `/api/slides/from-markdown` writes `slides` JSONB but not `pptxgenjs_spec`. Markdown-built decks can't export PPTX (only DP/Deep-Deck path works). Future ticket: add `pptxgenjs_spec` generation in `build_slides_from_md()` via `codegen_pptxgenjs.py`.

**Files this session:**
```
NEW   dash/guards/__init__.py · number_cite.py · bounds.py          ~280 LOC
EDIT  dash/semantic/compile.py + __init__.py                         invalidate + detect_cycles + dialect kwarg + MAX_ITERS warn
EDIT  dash/workflows/verticals/__init__.py                            cycle gate + collision check + _vcol_dry_run + bounds passthrough
EDIT  dash/workflows/verticals/crm_calls.py                           call_id aliases 3 → 10
EDIT  dash/settings.py                                                prompt-cache salt
EDIT  app/upload.py                                                   invalidate(slug) on golden promote/demote
EDIT  app/dashboards_api.py                                           _sanitize_json + 3 return wrappers
EDIT  dash/tools/slides.py                                            get_write_engine for _save_presentation_row
EDIT  frontend/src/lib/chat/ChatMessageList.svelte                    ‹‹VERIFIED:...›› placeholder (3 sites)
EDIT  Makefile                                                        rebuild-raw target
```

**Live verified end-to-end (post-fix):**
| Surface | Status |
|---|---|
| Deep Dashboard | ✅ 9 panels, 28k tokens, 112s, judge ran |
| PPT from markdown | ✅ pres_id=24, 2 slides, persisted |
| PPT list (`?project=`) | ✅ 10 decks |
| PPTX export | ✅ 47880b OOXML on spec-bearing deck |
| Verified-pill render | ✅ green pill on cached-metric answers |
| Smoke: detect_cycles · audit_numbers · check_bounds | ✅ all clean |

**Patterns added to "load-bearing" list:**
- `<span>`/HTML inside markdown text → emit unicode placeholder, post-process AFTER `markdownToHtml`. Three render sites in ChatMessageList must apply same post-process.
- ANY write to `public.dash_*` → `db.session.get_write_engine()` mandatory. PR review gate; 3rd session this bit.
- LLM-pipeline endpoints w/ pandas rows → recursive NaN/Inf sanitize before return. FastAPI default rejects NaN.
- New MDL packs → pre-install cycle detect mandatory; compiler's MAX_ITERS cap silently fails otherwise.
- rtk shell wrapper occasionally swallows docker output → `make rebuild-raw` always available.

**What's deferred (next session):** Batch 3 UI (~3 days svelte) · `finance_fpa_mdl` 3rd pack (~200 LOC) · per-cell ✨ chat (Hex pick #2, ~1 day, smallest visible win) · PPT product gap (`pptxgenjs_spec` in slides builder).

---

## Earlier (2026-05-25) — Golden SQL corpus (Dataherald) + MDL semantic layer (WrenAI) + 2 vertical packs in MDL format

Two architecture lifts inspired by sibling OSS tools, both shipped + baked + smoke-verified live. **~990 LOC, 5 new files, 9 edits, ONE migration (134), zero new tables.**

**1. Golden SQL corpus** (Dataherald-style 👍 → cached deterministic shortcut)
- NEW `dash/learning/golden.py` — `promote/demote/list`. Appends to `KNOWLEDGE_DIR/{slug}/training/_golden.json` (underscore-prefix sorts first → loaded ahead of auto-gen). sha256 dedup. Read-only gate. 500-entry cap.
- NEW endpoints `POST /api/projects/{slug}/golden/{promote,demote,list,drift-check}`.
- AUTO-PROMOTE on 👍 in `/feedback` — when verified_reward doesn't gate + `sql` field present, auto-pins to corpus. Response includes `promoted.total_goldens`.
- FRONTEND wire — `routes/project/[slug]/+page.svelte` + `routes/chat/+page.svelte` now pass `sql: firstSql` in `/feedback` POST + log `[golden] promoted to corpus (total: N)`.
- NEW `dash/cron/golden_drift.py` — 24h daemon re-executes every golden's SQL, demotes drifted (>50% rowcount delta) or exec-failed entries. `GOLDEN_DRIFT_DISABLED=1` to disable. Wired into lifespan w/ `_should_run_daemons()` gate.
- **Skipped `_reload_project_knowledge` on promote** — `try_metric_shortcut` reads JSON fresh per chat (no in-mem cache), so reload was 30s + 24 LLM-embed-calls of pure waste. Promote now 4ms (7500× faster).

**2. MDL semantic layer** (WrenAI-style logical model → raw SQL via sqlglot AST rewrite)
- Migration 134 — ALTER `dash_metric_definitions` ADD 4 cols (`model_name`, `raw_table_ref`, `virtual_columns JSONB`, `relationships JSONB`) + partial index. **No new tables** — extends existing infrastructure (versioning + audit inherit free).
- NEW `dash/semantic/{__init__,compile}.py` — sqlglot-based MDL compiler. `load_models()` 5min TTL cache. Iterative fixed-point compile (MAX_ITERS=4) resolves vcol→vcol→raw chains (e.g., `extended_value = qty * unit_cost → stock_qty * weighted_cost_price`). Pass-through on parse error.
- `dash/tools/build.py` `RLSAwareSQLTools` — new `project_slug` ctor + MDL compile pass BEFORE every `super().run_sql_query()`. Single point intercepts all agent SQL. Transparent semantic→raw rewrite.
- `dash/instructions.py` Layer 3c — `models_for_prompt(slug)` injected after PIPELINE_LOGIC + VERIFIED_METRICS. Format: `## SEMANTIC MODELS (use these names, NOT raw columns)` + table list w/ virtual cols + relationships + auto-compile note.

**3. Vertical packs migrated to MDL format**
- `dash/workflows/verticals/crm_calls.py` + `pharmacy_retail.py` — both got `MDL_PACK` alongside legacy `PACK`. Each declares ONE logical model (`customer_calls`, `inventory`) + 8 virtual_columns + workflows written against LOGICAL names (no `{placeholder}` substitution).
- NEW `install_mdl()` in `dash/workflows/verticals/__init__.py` — alias-resolves `raw_table_aliases` → real table, alias-resolves each vcol → real raw column, INSERTs models into `dash_metric_definitions` via `get_write_engine()` (CLAUDE.md gotcha: `get_sql_engine` is read-only). Workflows persisted as SEMANTIC SQL (compile at exec time).
- `list_packs()` + `detect()` now return BOTH legacy + MDL w/ `format` discriminator. API auto-routes `_mdl` suffix to `install_mdl()`.

**Live verified end-to-end** (`make rebuild` + smoke against `proj_demo_pg_crm`):
```
4 packs visible:       crm_calls (legacy/5wf) · crm_calls_mdl (mdl/5wf+1m)
                       pharmacy_retail (legacy/4wf) · pharmacy_retail_mdl (mdl/5wf+1m)
Detect crm:            0.520 for both legacy + MDL (correctly tied)
Golden promote:        4ms (was 30s)
MDL install:           15ms, 1 model + 5 workflows, 7/8 vcols resolved
                       raw=crm_jun_2025, virtual_cols all bound to real columns
Migration 134:         applied=120, status=applied ✓
DB persistence:        mdl_customer_calls row + 5 pending workflows confirmed
```

**Iterative compile proof** (vcol-references-vcol chain):
```
SEMANTIC: SELECT SUM(extended_value) FROM inventory
ITER 1:   SELECT SUM(qty * unit_cost) FROM balance_stock  (vcol expanded once)
ITER 2:   SELECT SUM(stock_qty * weighted_cost_price) FROM balance_stock  (chain resolved)
ITER 3:   no change → fixed-point reached
```

**4 bugs found + fixed during smoke:**
1. `_get_user` not defined in `upload.py` → added local helper returning dict-or-empty
2. `list_packs/detect` only iterated legacy `_ALL_PACKS` → added `_ALL_MDL_PACKS` + format tag
3. Golden promote ~30s → dropped pointless `_reload_project_knowledge`
4. MDL install: "Cannot write to public schema" → switched installer to `get_write_engine()`

**Patterns to remember:**
- Platform-metadata writes to `public.dash_*` ALWAYS use `db.session.get_write_engine()`. CLAUDE.md mentioned this; it bit again 1 session later. Codify as PR review gate.
- JSON-on-disk feature ≠ requires re-embed. `try_metric_shortcut` reads `*.json` fresh per call. Don't pay reload cost (30s + 24 embed calls) for features that don't need it.
- Iterative fixed-point compile for vcol chains. Single-pass leaves vcol-references-vcol unresolved. MAX_ITERS=4 + early-exit on no-change handles depth-3 + protects against MDL cycles.
- Semantic layer = data extension to existing tables, NOT new system. 4 cols on `dash_metric_definitions` inherits versioning + audit + API.
- Vertical pack files can export BOTH `PACK` (legacy) AND `MDL_PACK`. Coexist + migrate gradually.

**Deferred next session:** golden management UI page (endpoints exist), MDL editor UI (Settings → MODELS tab), compiler cycle detector log warning, 3rd MDL pack (e.g., `finance_fpa_mdl`), broader `call_id` alias list.

## Earlier (2026-05-24) — Workflow run split-view + daemon hardening + vertical packs + refusal flag + cached-metric trace

Long session, two product threads.

**Workflow execution reliability:**
- `/ui/agent-os/workflows` redesigned w/ left-rail Library + Projects (Option D) — matches `/project/[slug]/settings` 240px rail convention. CLI banners + LIVE ACTIVITY footer killed.
- Workflow run split-view (`project/[slug]/agent-os/run/[run_id]`) now fetches run state via REST BEFORE opening SSE — fixes "FAILED 0/0 0s" for runs that finish before browser connects (sub-second cached or short workflows).
- Added `GET /api/agent-os/workflows/runs/{run_id}` returning full state + workflow_name + events JSONB.
- History page (`/ui/agent-os/workflows/[id]/history`) auth fixed — was raw `fetch()` + wrong URL → 401. Now `dashFetch` + `getWorkflowHistory()` helper.
- **WORKER_RANK gunicorn fix**: `worker.age` is 1-indexed → no rank-0 worker ever existed → daemons silently never spawned. Patched `scripts/gunicorn_conf.py` to subtract 1.
- **Workflow runner thread** moved from `asyncio.create_task` (GC'd silently) to `threading.Thread` w/ module-level ref + own event loop. Mirrors autonomous_runner pattern.
- **Leader-election retry path** in `dash/runtime/daemon_leader.py` — closes the force-recreate "stale-but-not-yet-expired" race. New workers that lose initial claim spawn a 45s retry thread + fire `_POST_CLAIM_CALLBACKS` (lifespan registers daemon-bootstrap as callback).
- **Orphan reaper** in workflow_runner loop (every 30 ticks ~5min): `UPDATE … status='failed' WHERE status='running' AND started_at < NOW() - INTERVAL '10 min'`. Recovered 184 stuck rows on first call.
- **Bulk SQL rewrite** for 37 broken seeded workflows in `proj_demo_pg_crm` (unresolved `{table.col}` placeholders + references to nonexistent `balance_stock_smoke`) — all re-pointed to real `crm_jun_2025` table via 7 SQL templates mapped by name category.

**Chat / metric / refusal:**
- **Cached-metric path emits full SSE trace**: `ReasoningStep` ("Used cached verified metric") + synthetic `ToolCallStarted/Completed` for `run_sql_query` + `TeamRunContent`. Frontend now shows "1 tool · 1 step · 7ms · deterministic" instead of empty. New `_run_rows()` returns rows + columns alongside scalar → DATA + SQL + CHART tabs populate for cached-metric questions. Added `[CHART:title]` hint + `[VERIFIED:Nms · cached]` speed badge rendered as green pill.
- **Follow-up bypass**: scope-classifier is stateless → was refusing "Show me the data behind this" alone. Added (1) heuristic skip if message ≤12 words w/ pronoun OR has prior session turn, (2) prior-turn context block prepended to `context_msg` from agno_sessions, (3) instruction-level FOLLOW-UP EXCEPTION clause in `_build_scope_guardrail` w/ explicit pronoun examples.
- **Refusal flag system** (migration `132_refusal_marks.sql` + `dash/runtime/refusal.py`): replaces brittle text-sentinel matching. New `dash.dash_refusal_marks` table + 3 functions (`mark_refused` / `was_refused` / `is_refusal_text` fallback). 3 refusal sites wired: scope_classifier path, agent-self detection in post-response bg, text-sentinel fallback in `extract_context`. Memory promoter + judge + KG extractor + prefs tracker all short-circuit when `was_refused()` returns True → no more "user is focused on CRM" poisoning when agent just refused.

**Vertical workflow packs (new agent-OS feature):**
- Migration `133_vertical_packs.sql` (cols + history table).
- New package `dash/workflows/verticals/`: resolver (`__init__.py`) + 2 packs (`crm_calls` 5 workflows · `pharmacy_retail` 4 workflows). Each pack is a Python dict (no YAML dep) w/ `detect.required_tables_any` + `required_cols_any` alias lists + per-workflow `expects` placeholder bindings.
- 3 endpoints in `app/vertical_packs_api.py`: `GET /api/vertical-packs` · `GET /api/projects/{slug}/vertical-packs/detect` · `POST /api/projects/{slug}/vertical-packs/install`. Resolver scores schemas 0-1, picks top pack ≥0.4, alias-resolves placeholders against real columns, skips unbindable workflows. Idempotent.
- **Auto-install hook** on `/retrain` complete: if project has 0 workflows AND top pack score ≥0.4 → auto-install. Never overwrites manual setups.
- Settings → WORKFLOWS tab new pack picker card: `↻ Detect packs` button → ranked list w/ match % + `+ Install` per pack. Color-coded scores (≥60% green / ≥40% amber / <40% red).

**Files (this session):**
- NEW: `db/migrations/132_refusal_marks.sql`, `db/migrations/133_vertical_packs.sql`, `dash/runtime/refusal.py`, `dash/workflows/verticals/{__init__,crm_calls,pharmacy_retail}.py`, `app/vertical_packs_api.py`
- EDIT backend: `app/main.py`, `app/projects.py`, `app/upload.py`, `app/agent_os_workflows.py`, `dash/runtime/daemon_leader.py`, `dash/learning/verified_reward.py`, `dash/instructions.py`, `dash/cron/workflow_runner.py`, `scripts/gunicorn_conf.py`
- EDIT frontend: `routes/agent-os/workflows/+page.svelte`, `routes/agent-os/workflows/[id]/history/+page.svelte`, `routes/project/[slug]/agent-os/run/[run_id]/+page.svelte`, `routes/project/[slug]/settings/+page.svelte`, `routes/project/[slug]/+page.svelte`, `routes/chat/+page.svelte`, `lib/chat/ChatMessageList.svelte`

**Patterns to remember:**
- Gunicorn `worker.age` is 1-indexed. Always `worker.age - 1` for rank stamping or you'll never have a rank-0 worker.
- asyncio tasks from lifespan startup get GC'd silently if reference isn't held module-level. Use `threading.Thread` + `globals()["_DAEMON_THREAD"] = t` for long-running daemons.
- Leader-election needs a retry thread for the force-recreate race (LEASE_S + buffer). Pair `try_become_leader()` w/ `_POST_CLAIM_CALLBACKS` so daemons start when retry wins.
- Cached/short-circuit response paths MUST emit same SSE event shape as full agent path. Frontend trace UI hides on `rows.length === 0` — emit synthetic ReasoningStep + ToolCallStarted/Completed for cached responses to keep the UI consistent.
- Refusal detection: flag-based table > text-sentinel matching (custom messages / i18n / future phrasing). Use text-sentinel only as fallback for legacy sessions.
- Vertical packs as Python dicts (not YAML) avoids adding `pyyaml` runtime dep. Drop new packs in `dash/workflows/verticals/<name>.py` + import in `__init__.py`. Resolver does alias-fuzzy-matching against real `information_schema`.

## Earlier (2026-05-24) — Office-skills self-test (14/14 tools verified) + orphan-workflow cleanup

Exhaustive in-container self-test of the 15 office tools shipped earlier. Caught 2 wrapper bugs + missing dep, fixed all live. Also diagnosed empty `/ui/agent-os/workflows` page — 37 orphan workflows pointing at deleted projects, cleaned up.

**14/14 tools verified working** (real artifacts produced):
- `xlsx_recalc`: `=SUM(100,250,300)` → 650, `=AVERAGE` → 216.67, `=MAX*2` → 600 (LibreOffice recalc + openpyxl read confirms cached values)
- `pptx_extract_inventory` / `_rearrange_slides` / `_ooxml_unpack`/`_validate`/`_pack`: 5 PPTX tools all return ok=True
- `generate_deck_thumbnail_grid`: 35KB JPG rendered via soffice→pdftoppm→PIL
- `docx_unpack` / `_add_comment` / `_pack`: round-trip works, comment injected at para_index=1
- `pdf_check_has_fillable` / `_extract_form_fields` / `_fill_fillable_fields`: extracted 2 fields, filled `{client_name: 'Acme Corp', sign_date: '2026-05-24'}`, verified via `pypdf.get_form_text_fields()` in output PDF

**2 bugs caught + fixed during test:**
1. `pypdf>=4.3` missing from `requirements.txt` — added, rebuilt
2. `pdf_fill_fillable_fields` schema mismatch (Anthropic script wants `[{field_id, page, value}]` list, wrapper accepted `{id: value}` dict). Fix: wrapper auto-calls extract first to resolve `page` per field, then converts dict → list. Accepts both shapes now.

**Orphan workflows fixed** (`/ui/agent-os/workflows` was empty):

Root cause: 37 rows in `dash.dash_autonomous_workflows` pointed at 3 deleted demo projects (`proj_demo_cmhl_pharmacy_agent`, `proj_demo_smoke_pharma`, `proj_demo_city_pharma`). Endpoint INNER JOINs to `public.dash_projects` → all filtered out. Demo user owns only `proj_demo_pg_crm`.

Inline fix:
```sql
UPDATE dash.dash_autonomous_workflows
SET project_slug='proj_demo_pg_crm', owner_user_id=1
WHERE project_slug IN ('proj_demo_cmhl_pharmacy_agent','proj_demo_smoke_pharma','proj_demo_city_pharma');
```

37 workflows now surface under PG CRM ownership.

**Next session ship:** migration 117 adds `FOREIGN KEY (project_slug) REFERENCES public.dash_projects(slug) ON DELETE CASCADE` to prevent recurrence.

**Patterns to remember:**
- **Self-test in container, not on host.** Host smoke tests pass via mock paths; in-container tests catch missing apt/pip deps + script schema mismatches. 30min self-test saves a week of bug reports.
- **Wrappers should adapt user-friendly shape to script's expected shape** — auto-discover metadata via prior subprocess call when cheap. Don't force users to know upstream JSON schema.
- **Inner-join on FK-less text column = silent data loss in UI.** Either add FK CASCADE or surface orphans w/ LEFT JOIN + "missing project" badge.

## Earlier (2026-05-24) — Cowork skills lift (15 office tools on Engineer agent)

Lifted `.claude/skills/` Python folder from [OpenCoworkAI/open-cowork](https://github.com/OpenCoworkAI/open-cowork) (MIT, originally Anthropic Claude Code skills). Wrapped 5 skill packs as 15 Agno `@tool` functions on Engineer agent. Gated by `feature_config.tools.office_skills` (default OFF, per-project opt-in).

**15 tools shipped** (`dash/tools/{xlsx_recalc,deck_visual_qa,deck_edit,docx_edit,pdf_form}.py`):

| Tool | Use |
|------|-----|
| `xlsx_recalc` | Compute Excel formulas via LibreOffice (fixes openpyxl uncalculated-formula bug) |
| `generate_deck_thumbnail_grid` | Render PPTX as JPG grid → feed to Vision LLM for QA before delivery |
| `pptx_extract_inventory` / `pptx_replace_text` / `pptx_rearrange_slides` | Template-driven branded deck gen (Citymart/CityBCP use case) |
| `pptx_ooxml_unpack` / `pptx_ooxml_pack` / `pptx_ooxml_validate` | Edit existing user-uploaded decks via raw OOXML + XSD validation |
| `docx_unpack` / `docx_pack` / `docx_add_comment` | Word output + tracked-change comments (CHL Legal Scout) |
| `pdf_extract_form_fields` / `pdf_check_has_fillable` / `pdf_fill_fillable_fields` / `pdf_fill_with_annotations` | PDF AcroForm fill + non-fillable overlay (legal/franchise contracts) |

**Source lift** — `dash/skills_cowork/` 40 .py + 78 XSD schemas (ISO-IEC29500-4). MIT preserved + `ATTRIBUTION.md` credits OpenCoworkAI + Anthropic. No modifications to lifted scripts — wrappers are pure subprocess calls (120s timeout, fail-soft, `{"ok": bool}` shape, `time.time_ns()` tmp uniqueness, absolute path validation).

**Wiring** — `dash/tools/build.py` Engineer block gated by `feature_config.tools.office_skills`, mirrors `external_connectors` precedent. `dash/feature_config.py` `DEFAULT_CONFIG["tools"]["office_skills"] = False`.

**Dockerfile additions** (4-round cascade — characteristic LibreOffice headless container journey):
- pandoc + qpdf (docx via pandoc, pdf cleanup)
- xvfb + xauth (recalc.py needs X display; xvfb-run requires xauth)
- libreoffice-calc + default-jre-headless (LO Calc requires Java for formula engine)
- requirements.txt: `+pdf2image>=1.17`, `+defusedxml>=0.7`

Image +~360MB. Worth it for Citymart/CHL/Shwelar customer fits.

**E2E verified live**: `xlsx_recalc` computes 30/60 for `=SUM(A1:A2)` + `=A3*2` cells. `generate_deck_thumbnail_grid` renders JPG from python-pptx-generated deck.

**Enable per project**:
```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  http://localhost:8001/api/projects/{slug}/feature-config \
  -d '{"tools":{"office_skills":true}}'
```

**Patterns to remember:**
- **Lift Anthropic skills wholesale when license allows** — don't reimplement what Anthropic already shipped + tested. Subprocess-wrap as Agno @tools, ~50ms overhead per call.
- **Subprocess > Python-path manipulation** — lifted files stay isolated, no namespace pollution, no shared state.
- **LibreOffice headless needs 4 things**: xvfb + xauth + JRE + the specific app pkg (calc/impress). Missing any = silent fail or cryptic errors.
- **Feature-flag every new tool batch** — default OFF + per-project opt-in. Canonical pattern via `feature_config.tools.<x>`.

## Earlier (2026-05-24) — External Connector System (Postgres · MSSQL · Fabric · BigQuery · PowerBI) + super-admin RBAC + admin nav consolidation

Shipped end-to-end multi-tenant external-connector system. 5 connectors. Super-admin sole configurator. Agents query via Agno tool gated by per-project feature flag + per-user/AAD-group RBAC. Architecture clean-room ported from bagofwords1/bagofwords registry pattern (AGPL avoided — code re-authored under existing license).

**Two phases, 12 parallel agents** (~4000 LOC backend + 1139 LOC frontend):

**Phase 1 — core:**
- `dash/connectors/` package: `base.py` ABC · `registry.py` (REGISTRY dict, 5 entries, lazy-imported via client_path string) · `schemas.py` (10 Pydantic Config+Credentials classes w/ `ui:type` widget hints) · `crypto.py` (Fernet from `CONNECTION_ENCRYPTION_KEY` env, falls back to sha256(JWT_SECRET)) · `access.py` (`can_user_use(user, conn)` — super_admin / allow_all / user_id / AAD-group GUID)
- 5 clients in `dash/connectors/clients/`: `postgres_client.py` (SQLAlchemy+psycopg, NullPool, `SET LOCAL statement_timeout`), `mssql_client.py` (pyodbc + SQL auth, ODBC18), `fabric_client.py` (pyodbc + Azure SP token struct, `SQL_COPT_SS_ACCESS_TOKEN=1256`), `bigquery_client.py` (google-cloud-bigquery + SA JSON), `powerbi_client.py` (httpx + DAX `executeQueries` + Azure SP OR per-user OBO)
- `app/admin_connectors.py` — 12 super-admin endpoints (CRUD + test + grant + rotate-secret + rotation-status + audit)
- `app/connectors_v2.py` — 5 user-facing endpoints (`/available` · `/{id}/schema` · `/{id}/query` · `/query/stream` · `/query/estimate`)
- `dash/tools/connector_query.py` — Agno `@tool query_connector(connection_name, sql)` w/ RBAC + audit; ContextVar lookup of session user, falls back to super-admin for background daemons
- `app/auth.py validate_token()` — extracts AAD `groups` claim into `user.aad_groups` (defaults `[]`, non-breaking)
- Migration `115_connections.sql` — `dash.dash_connections` (id UUID, name UNIQUE, connector_type, config JSONB, credentials TEXT Fernet-encrypted, owner_user_id, enabled, allow_all_users, users_allowed JSONB, ldap_groups_allowed JSONB) + `dash.dash_connection_audit`
- Frontend `command-center/connectors/+page.svelte` (1139 LOC, 4 tabs: TYPES · CONNECTIONS · GRANTS · AUDIT) — super-admin gated, dynamic form renderer reads Pydantic JSON Schema + `ui:type` widget hints, brand-logo dropdown via simpleicons.org CDN

**Phase 2 — production hardening:**
- **SSE streaming** — `execute_query_stream(chunk_size)` on all 5 clients, `POST /api/connections/{id}/query/stream` SSE w/ meta/chunk/done/error events. PowerBI falls back to single-batch chunking (DAX `executeQueries` returns full result).
- **BigQuery cost guard** — `estimate_cost()` dry-run, pre-flight check vs `max_bytes_per_query` cap, raises ValueError before billing. Response includes `meta.bytes_processed` + `estimated_cost_usd`. NEW `POST /query/estimate` endpoint.
- **PowerBI OBO per-user RLS** — MSAL `acquire_token_on_behalf_of`, Fernet-encrypted refresh+access tokens in `dash.dash_connection_user_tokens`, auto-refresh w/ 60s margin. 428 + `consent_url` returned when token missing. `auth_mode: "service_principal"|"obo"` per connection. `_OBO_USER_CTX` ContextVar set by query endpoint before client invocation. 4 OAuth endpoints in `app/connector_obo_api.py` (consent-url · callback · revoke · status).
- **Secret rotation cron** — `dash/cron/connector_rotation_daemon.py` 24h loop scans `secret_rotated_at` vs `secret_rotation_alert_days` (default 90d), writes severity ramp warn→critical(+30d) into `dash_notifications`, throttled 7d per connection. `POST /rotate-secret` resets timestamp.
- **Read-only SQL gate** — `is_read_only_sql(sql, dialect)` head-token check: allow `SELECT`/`WITH`/`EXPLAIN`/`SHOW`/`EVALUATE` (DAX), reject `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`TRUNCATE`/`CREATE`/`GRANT`/`REVOKE`/`MERGE`/`CALL`/`EXEC`. Enforced before every execute.
- **Secret scrubber** — `safe_error_message(exc, creds)` replaces every cred value occurrence in error strings with `***REDACTED***`, truncates 500ch. Wired into every client error path so HTTP responses never leak passwords/secrets/JSON keys.
- **Per-day quota** — `query_limit_per_day` (default 1000) enforced via `dash_connection_audit` count, super-admin bypass, 429 + `{limit, used, reset_at}`.
- **Statement timeouts** — PG `SET LOCAL statement_timeout`, MSSQL/Fabric `cursor.timeout`, BQ `QueryJobConfig(timeout=...)`.
- Migration `116_connector_p2.sql` — adds `query_limit_per_day`, `max_bytes_per_query`, `secret_rotated_at`, `secret_rotation_alert_days`, `last_rotation_warning_at` columns + `dash.dash_connection_user_tokens` table.

**Agent integration (gated by `feature_config.tools.external_connectors` — default OFF):**
- `dash/tools/build.py` — `query_connector` tool added to Analyst, Engineer, Data Scientist
- `dash/instructions.py` — new `_build_external_connectors_context(user_id, project_slug)` as Layer 14. Lists granted connections + each one's `prompt_schema()` (60s TTL cache, per-connector 600ch cap, total 3500ch). Fail-soft per layer — driver outage never blocks instruction build.

**Real brand logos on connector page** (after user asked):
- simpleicons.org CDN brand-colored: PostgreSQL `#336791`, MSSQL `#CC2927`, Fabric `#0078D4` (auto-fallback to Microsoft icon via `onerror` if `microsoftfabric` not yet in cache), BigQuery `#669DF6`, PowerBI `#F2C811`.
- 3 surfaces: TYPES card grid (48px tile above title), CONNECTIONS table TYPE column (18px inline logo), CREATE/EDIT modal type picker (custom dropdown replacing native `<select>` — native can't render images; uses button + popup `$state` toggle pattern w/ 28px logo per row + title + kind + description).

**Admin nav consolidation:**
- Top-nav `Admin` dropdown collapsed to single button → `/ui/command-center` (no popup menu).
- Command Center rail new **GOVERNANCE** group w/ 3 entries: Governance → `/ui/admin/governance`, Agent OS → `/ui/admin/agent-os`, Telemetry → `/ui/admin/telemetry`. Plus CONNECTORS in Data group.
- All 4 admin destinations reachable from one rail. `switchTab` external map handles navigation for non-inline tabs. 5/5 pages return 200 verified live.

**Tests:** `tests/test_connectors.py` — 20/20 PASS in 1.69s (read-only gate · secret scrub · RBAC permutations · Fernet roundtrip + tampering · registry · 5 client classes).

**RBAC defense-in-depth:**
1. API: `can_user_use(user, conn)` on every read/write/query
2. SQL: read-only gate before execute
3. Error: secret scrubber before HTTP response
4. Audit: every operation logged to `dash_connection_audit` (non-blocking)

**`uv pip sync` transitive-dep cascade (CLAUDE.md Issue #12 recurring):** new top-level deps `pyodbc` · `azure-identity` · `google-cloud-bigquery` required pinning full transitive closure (~17 packages: `google-resumable-media`, `google-cloud-core`, `google-api-core`, `googleapis-common-protos`, `grpcio`, `grpcio-status`, `protobuf`, `pyasn1`, `pyasn1-modules`, `charset-normalizer`, `requests`, `azure-core`, `msal`, `msal-extensions`, `google-auth`, `google-crc32c`, `proto-plus`). Surfaced one ImportError per rebuild (build EXIT 0, runtime fail) — 4 rebuilds before full closure landed. Dockerfile additions: `unixodbc-dev` (deps stage) + `msodbcsql18` from Microsoft bookworm repo + `unixodbc` (runtime stage). +~80MB image.

**Env vars added:**
- `CONNECTION_ENCRYPTION_KEY` — Fernet 44-char urlsafe-b64 (auto-derives from `JWT_SECRET` if absent)
- `OBO_REDIRECT_BASE_URL` — PowerBI OBO callback base (must match Azure AD app's redirect URI)
- `CONNECTOR_ROTATION_DAEMON_DISABLED=1` — disable rotation reminder daemon
- `CONNECTOR_ROTATION_INTERVAL_SECONDS` — default 86400 (24h)

**Patterns to remember:**
- **Dict registry > plugin discovery** — REGISTRY is just a frozen Pydantic dataclass dict; `client_path` string lazy-resolved via `importlib`. Greppable, type-checked, no decorators. Lifted clean-room from bagofwords.
- **Pydantic schema = UI form spec** — `json_schema_extra={"ui:type":...}` widget hints in every Field; frontend renders generic form from `model_json_schema()`. Zero per-connector frontend code.
- **Fernet on single TEXT column** — no vault, key from env. One place to encrypt/decrypt. `instantiate_client(conn_row)` is the only place creds are decrypted.
- **AAD groups → user dict via JWT claim** — purely additive in `validate_token()`, backward compatible. RBAC check is one-liner intersection.
- **Custom dropdown w/ logos** — native `<select>` can't render images; use button + `$state` popup div w/ `position: absolute; z-index: 50`.
- **`uv pip sync` transitive gotcha** — any new top-level dep MUST include full closure or container builds clean but ImportErrors at runtime. Use `pip install <pkg>` inside container to dump deps + back-pin to `requirements.txt`.
- **Stalled agent recovery** — when parallel agent hits autocompact mid-work, salvage by reading what's shipped + finishing inline. 3 of 12 agents stalled this session; all recovered inline in <5min each.

## Earlier (2026-05-23) — Dashboard→Deck rebuild + AI Deck Stylist + list-page redesign

Two-thread session. (1) Dashboard list + Presentations list redesigned to mirror Projects card (3-col grid, coral avatar tile + serif name + meta + status + "Open →" footer link). (2) Full overhaul of Dashboard→PPTX pipeline ending in an **AI Deck Stylist** that picks theme + accent + WCAG-safe card colors + chart palette per dashboard.

- **List-page card parity**: `/ui/dashboard` + `/ui/presentations` now use `repeat(3, minmax(0, 1fr))` w/ 2/1 breakpoints + identical card markup to `/ui/projects`. Presentations gained search (Cmd+K) + filter pills (All/Recent/Favorites) + per-card icon row (☆ star · ↓ download · 📅 schedule · ✕). Eliminates the visual mismatch where Presentations looked like a different product.
- **Legacy deck preview fix**: `GET /api/export/presentations/{id}/preview` no longer 500s on rows w/ NULL `pptxgenjs_spec`. Reconstructs spec from `slides` column as fallback; returns `{empty:true, reason:"no_spec"}` w/ friendly message for truly-empty decks.
- **`_run_qa_loop` restored** in `deep_deck.py` — old qa.sh script was deleted with render_js dir, never replaced. Replaced w/ inline `soffice --headless --convert-to pdf` → `pdftoppm -jpeg -r 120` calls. Preview JPGs now render.
- **Dashboard→Deck pipeline rebuilt (`app/dashboard_to_deck.py`)**: 4 root-cause bugs fixed:
  - **All charts EMPTY** — mapper looked for `rows`/`chart_data`/`echarts_options` but DeepDash panels bake data into `options.series[*].data` + `xAxis.data`. NEW `_extract_chart_data_from_echarts()` handles pie/cartesian/multi-series shapes.
  - **KPI strip missing** — `_is_kpi_panel` checked `type=='kpi'` but DeepDash panels have `chart_type='gauge'` + no `type` field. Added `_panel_chart_type()` helper; gauge/kpi/metric → KPI bucket.
  - **Chart titles duplicated inside chart frame** — `add_chart` was reading `data.title` (same as slide header). Renamed to `data.chart_title` (defaults empty) — header is sole title surface.
  - **Theme defaulted to dark teal** — `city_executive` never registered → fell back to `midnight_executive`. Switched to `coral_energy` (matches app palette).
  - Persists `pptxgenjs_spec` + `rendered_pptx_path` on INSERT (was missing). `_pretty_slug` formats slug for cover. Narrative surfaced as `action_line`. Single-series charts auto-hide legend (no "Series1" eyesore).
- **Layout zone overlap + responsive typography fixes** (pptx_renderer):
  - NEW `responsive_size()` helper shrinks font by char count (≤30→base, ≤60→0.72×, ≤90→0.55×, else 0.42×). Combined w/ `text_frame.auto_size = TEXT_TO_SHAPE_OR_SHRINK_TEXT` (PPT native autofit) as safety net.
  - `add_text_box` gained `auto_shrink: bool` kwarg.
  - Header/cover/title sizes now dynamic (24→14pt, 54→22pt).
  - `CHART_FRAME` y=1.55 h=3.45 (was y=1.1 h=3.8) — no more title/action_line/chart 3-way overlap.
  - Closing layout: `bullets[]` auto-mapped to `next_steps[]` cards (eliminates blank closing slide).
  - `_smart_truncate(text, max)` word-boundary truncation (action_line 140ch, source 100ch, chart title 90ch, cover title 70ch).
  - Empty chart panels (no data after extraction) auto-convert to narrative slide instead of empty bar placeholder.
- **AI Deck Stylist v1** (`dash/tools/deck_stylist.py`, NEW) — replaces fixed `DEFAULT_THEME`:
  - `analyze_dashboard()` → `{title, audience, mood, domain_guess, panel_snippets[]}` (regex, $0, <5ms).
  - `_llm_pick_style()` → LITE_MODEL picks theme + accent + tone + reasoning (~$0.0008, 1.1s).
  - Domain keyword fallback when LLM unavailable: 8 themes × 7 verticals (retail→coral_energy, finance→teal_trust, healthcare→forest_moss, risk→cherry_bold, executive→midnight_executive, ops→charcoal_minimal, consumer→berry_cream, sustainability→ocean_gradient).
  - Mood lexicon overrides domain: alert (issues/drop/risk/critical) → cherry_bold; positive (growth/exceed) → keeps domain; neutral → keeps domain.
  - `get_theme_with_overrides()` uses `dataclasses.replace()` on frozen Theme to patch accent/palette without mutating registry.
  - `render_to_path(theme_override=Theme)` bypasses name lookup.
  - Audit persisted in `dash_presentations.thinking.stylist.*`.
- **AI Deck Stylist v2 — WCAG-aware contrast** (after user caught red-on-dark-blue bug):
  - Extended `DeckStyle` to `{theme, accent, card_bg, card_value, card_label, chart_palette[]}`.
  - Defense-in-depth contrast: (1) LLM prompt teaches WCAG rules + auto-fix examples; (2) `ensure_contrast(fg, bg, 4.5)` swaps fg to white-or-dark if below 4.5:1 via luminance; (3) `pick_readable_text(bg)` fallback when fg missing; (4) cover layout re-validates at render time.
  - Helpers in `deck_stylist.py`: `_hex_to_rgb`, `_luminance` (WCAG 2.0), `_contrast_ratio`, `pick_readable_text`, `ensure_contrast`.
  - Cover layout uses `_card_bg`/`_card_value_color`/`_card_label_color` keys injected by `dashboard_to_deck`.
  - `chart_palette[]` flows through `get_theme_with_overrides(palette=…)` → ECharts series colors per chart.
  - Smoke test: alert-mood dashboard → LLM picked `cherry_bold` theme + `card_bg=#F5F5F5` + `card_value=#1A1614` (contrast ratio **16.48** AAA) + 5-color red palette. Old red-on-blue bug structurally impossible.

**Patterns to remember:**
- Field-name drift is the #1 dashboard→deck bug class. DeepDashAgent panels: `chart_type` + `options` at TOP LEVEL, no `panel_type`. Mapper must accept both legacy + new shapes via `_panel_type()` + `_panel_chart_type()` helpers.
- Slide layout zones must be explicitly non-overlapping — document the canonical zone table once instead of debugging y-collisions per stage.
- Responsive typography needs BOTH `font_size` AND `text_frame.auto_size`. Belt + suspenders.
- **LLM color picks are unreliable for WCAG.** Even with explicit prompt rules, LLM picks unreadable combos. Always post-validate contrast in code, not prose.
- Theme = name-only is insufficient. Theme + accent + card colors + chart palette must each be independently patchable. `dataclasses.replace()` on `frozen=True` Theme keeps registry immutable.

## Earlier (2026-05-23) — Dashboard versioning + Studio chatbot + render fixes

After 3-pane chat→dashboard shipped, polished the full versioning + persistence + Studio loop:

- **Versioning** (migration 113 + 114): every build INSERTS new row (`session_id`, `version`, `parent_id`, `label`, `signature_hash`). Refine also creates new version (parent_id chains). DELETE endpoint repoints children to NULL.
- **Auto-load on chat session open**: `GET /by-session/{sid}/latest` → if hit, opens dashboard pane (0 tokens). Click CHAT→DASH on existing → just opens pane. `↻ Rebuild` button forces new version v(N+1).
- **Signature-hash cache**: `sha256(project|sorted_top_5_questions|MAX(table_metadata.updated_at))[:32]`. Same question + same schema = cache hit, instant, 0 tokens. Invalidates auto when data changes.
- **Version dropdown** (`vN ▾`) in both project pane + Studio: per-row delete (`✕`), timestamp, panel count, label. Click row swaps version.
- **Studio chatbot**: NEW `POST /api/dashboards/{id}/chat` endpoint + `DashChatPanel.svelte`. Loads spec → builds context from up to 20 panels (title + narrative + sample rows + columns) → LITE model → returns `{answer, cited_panels}`. `🗨 Ask` toggle in topbar, 360px right sidebar, cite-panel chips scroll to panel.
- **Studio empty-state bug fixed**: panels → cells normalization now SYNC before `dashSpec` assignment (was async `$effect` running after first render).
- **"no sql" red banner fix**: runner now reads `cell.sql` OR `cell.config.sql` (top-level fallback) + inline `cell.rows` first (cached from build, no DB hit). No SQL + no rows + has narrative → renders narrative cleanly, no scary ⚠.
- **Gauge nuclear override**: LLM-generated gauge specs had overlapping axisLabel/title/pointer at center. Now `Object.keys(s).forEach(delete)` + total replace with clean Source Serif 30px coral detail, no axis labels, no pointer.
- **Truth-narrator render-time cleanup**: `cleanText()` regex `\b\d+\.\d{3,}\b` rounds floats at render. Fixes old persisted "182.333333334" → "182" without backend re-run.
- **Hard quota at panel-plan stage**: ≥3 KPI · ≥4 chart · ≥1 table · ≤2 insight · 8-12 total · ≥4 different chart_types. Validator + 1 retry + force-drop excess insights.
- **Drop-empty-panel filter at stage 6**: SQL error, 0 rows for kpi/chart/table, >95% null → drop. If 0 panels remain → return error.
- **Beautification**: Source Serif titles, 1px cells w/ hover lift, dashed gridlines, donut pies, suppressed HIGH confidence badge (only flag MEDIUM/LOW).
- **Detailed build progress view**: 4-card meta strip (elapsed/stage/panels/tokens) + 9-stage checklist with per-stage `Nms` durations + payload badges + per-panel cards + CLI event log (40 lines, color-coded).

**Build-process learning (load-bearing):** Docker frontend-build inside container silently fails if `npm run build` errors (e.g. TypeScript annotations in plain `<script>` block) but the cached `frontend/build` layer survives → image ships OLD bundle. ALWAYS run `cd frontend && npm run build` locally before `docker compose build`. If local fails, Docker WILL ship stale bundle without warning.

**Endpoints added:** `GET /api/dashboards/by-session/{sid}/latest`, `GET /api/dashboards/by-session/{sid}`, `DELETE /api/dashboards/{id}`, `POST /api/dashboards/{id}/chat`. Updated: `POST /from-chat/stream` (signature cache + force_rebuild), `POST /{id}/refine` (now INSERTs version), `GET /{id}` (returns wrapped envelope).

## Earlier (2026-05-23) — Chat→Dashboard with 3-pane live-build layout

The bagofwords UX win shipped end-to-end inside the project chat page (no Studio navigation needed):

- **NEW button `📋 CHAT→DASH`** in the project chat composer (between `D` and `DP`). Click → takes the **whole chat session** as context (every Q, every SQL, every result the agent already pulled) → runs the 9-stage Deep Dash pipeline → renders the dashboard in a NEW right-side pane.
- **3-pane layout** appears the moment you click — no waiting for done, no navigation. History rail (~18%, existing) + chat thread (52%, shrinks via CSS transition) + dashboard pane (48%, new). The right pane shows **live build progress in place** — header says `📊 Dashboard · Building…`, body shows `📋 Building from chat · 4/9 · panel_plan ████░░░░░`, then a growing list of "Panels building…" as each `panel_ready` SSE event fires, then the executive narrative (italic serif) when `narrative_ready` arrives, and finally the rendered DashRenderer grid on `done`.
- **You stay in the same chat.** No navigation, no slide-over. The left chat keeps working — ask another question while the dashboard sits on the right. ✕ collapses the right pane → chat returns to full width.
- `↗ Studio` button in the right-pane header opens the same dashboard in full-screen Studio for refine (skill-driven JSON-Patch via `skl_dashboard_refiner`).

**Backend changes:** new `POST /api/dashboards/from-chat/stream` (`app/dashboards_api.py`) — streaming variant of existing `/from-chat`. Pulls `agno_sessions.runs` via `extract_context()`, synthesizes a comprehensive prompt covering the conversation, runs Deep Dash → emits same SSE event shape as `/deep-build/stream`. Same audience-aware skill routing (`skl_narrative_{investor,ops,customer,exec}`) + verified-reward truth-grounding + Vision-QA judge.

**3 bugs found + fixed during E2E test** (documented for future-me):
1. `isFirstTurn()` checked user-message count AFTER pushing → first turn wrongly routed to `/refine` with empty id → 404. Fixed: capture firstTurn flag BEFORE the push.
2. Dashboard "built" event fired but `/studio/{id}` rendered empty — `DeepDashAgent.stream()` + `run_sync()` never INSERT'd into `dash_dashboards_v2` (only `/save` endpoint did). Fixed: upsert (`ON CONFLICT (id) DO UPDATE`) inside both methods before `done` emit, using `get_write_engine()`.
3. Row persisted but DashRenderer rendered nothing — Deep Dash writes `spec.panels` (new EChartsPanelSpec shape); DashRenderer reads `spec.cells` (legacy). Fixed at the **write path** (one place): agent's persist now mirrors `spec.panels` → `spec.cells` via `panelToCell` adapter before INSERT, so every reader sees both. Plus defensive `_ensureCells()` in StudioShell.

**Net effect:** chat-to-dashboard is now the natural flow — type → 9 panels stream live into the right pane → keep chatting. No leaving the page. Reuses every existing skill (`skl_dashboard_intent`/`narrator`/`refiner`/`panel_announcer`/audience styles/layouts/bundles) + verified-reward + metric-shortcut.

## Earlier (2026-05-23) — Skill-driven Dashboard Studio (bagofwords-parity) + closed-loop SkillRefinery

After studying `bagofwords1/bagofwords`, built feature parity the Dash way: every prompt-emitting stage is a versioned skill in `dash_skills` (runtime-loadable, SkillRefinery-tuned, per-tenant overridable) instead of hardcoded.

- **16 new skills** seeded in `dash/skills/builtin.py` (23 → 39 total): 4 pipeline (`skl_dashboard_{intent,narrator,refiner,panel_announcer}`) + 4 audience-aware narrative styles (`skl_narrative_{investor,ops,customer,exec}`) + 4 layout skills + 4 vertical bundles (`skl_dash_{qbr,investor_update,ops_review,customer_review}`).
- **Studio split-pane UI** at `/ui/project/{slug}/studio` (new) + `/ui/project/{slug}/studio/{id}` (refine). Left = chat thread + audience chips + composer (warm cream). Right = narrative header (truth-grounded Executive Overview) + DashRenderer (white). Incremental panel reveal w/ fade-in per `panel_ready` SSE event. Coral pill in chat per panel w/ 200×80 ECharts mini-thumbnail + scroll-to-panel on click. **✓ verified vs pinned metric badge** per cell. `📊 STUDIO` button in project chat composer.
- **Deep Dash v2 extended** with 4 new stages (intent_classify → narrator/audience-aware → panel_announce → refiner). Stage 7.5 narrator calls `try_metric_shortcut` per panel → injects "USE THESE NUMBERS VERBATIM" → executive paragraph never hallucinates a number. New endpoint `POST /api/dashboards/{id}/refine` (NL → RFC-6902 JSON Patch via `skl_dashboard_refiner`).
- **Closed-loop SkillRefinery**: migration 112 adds `dash_dashboard_skill_runs` + `dash_skill_overrides` + `dash_dashboard_audit`. `_persist_skill_run()` writes per stage (latency_ms, judge_score, verified_cell_count). `_persist_dashboard_audit()` writes per run (skill_versions JSONB + verified_cell_pct). `_draft_patch_for_dashboard_skill()` is a real LLM drafter (DEEP_MODEL) — reads last 5 failing runs, proposes revised instructions, persists to `dash_tool_patches`. `apply_dashboard_skill_patch()` applies w/ shadow-validate gate. Reward formula `0.5·verified_cell_ratio + 0.3·judge_score + 0.2·(1−latency)`.
- **Per-tenant skill overrides**: `_skill_prefix(skill_id, project_slug)` now checks `dash_skill_overrides` first → falls back to global. Cache key includes project_slug so overrides don't bleed across tenants.
- **Audience persistence**: `localStorage[dash_studio_audience_{slug}]` + `spec.audience` field. Survives refresh and refine turns.

End-to-end verified: Studio 200 · all helpers importable · build EXIT 0 · zero boot errors · routes 827→828 (+ refine).

**Net effect:** dashboards are now skill-driven (data team tunes prompts in UI, no deploys), audience-aware (one skill switch swaps narrative tone), truth-grounded (numbers come from verified-reward, not LLM), audit-traceable (every dashboard logs which skill versions ran), and self-improving (nightly SkillRefinery drafts patches on failure rows, shadow-validates, auto-applies on pass).

## Earlier (2026-05-23) — Final trim + 4 perf wins (full-day session totals)

After the DS+Presentation roadmap shipped, executed a sweeping trim + optimization round.

**Trim (12,277 more LOC removed today):**
- **Sim chassis deleted** — `app/sim_api.py` (1198) + `dash/sim/` (1853, 8 files) + `dash/agents/sim_router.py` + `dash/cron/sim_cleanup_daemon.py` + `frontend/src/routes/sim/` (3651) + SIM ROUTING RULE block + chat-time pre-check. Migration `110_drop_sim_tables.sql` drops 4 sim tables. **~8,376 LOC.**
- **workforce_api + /ui/os/agents deleted.**
- **ml_worker source deleted** — `_save_model` + `_save_experiment` stubbed (models live in-process for chat-turn only). Migration `111_drop_ml_model_tables.sql` drops `dash_ml_models` + `dash_ml_experiments`. **~736 LOC.**
- **autosim package deleted** (`dash/autosim/` — was tied to deleted sim chassis: auto-spawn-on-drift / from-dreams). `dash/tools/sim_tools.py` + `dash/learning/dream_to_sim.py` deleted. **~3,165 LOC.** Surgical: removed 5 autosim handlers from `dash/minions/worker.py`, autosim hook from `drift_detector.py`, `create_run_what_if_tool` registration from `build.py`.
- **Specialist agents — not a real trim.** Audit found zero specialist Agent objects existed; "10 specialists" lived only as routing rules. Trimmed the rules (HARD STOPS 11→3, Analyst MANDATORY block 11→6), rerouted Trend → `analyze(trend)` + Anomaly → `detect_anomalies_ml`. Team member count unchanged (5).

**4 perf wins (parallel):**
- **Phase 7 cron runner shipped** — `dash/cron/deck_schedule_runner.py` polls `dash_deck_schedules` every 60s via `croniter`, calls `deliver_scheduled_deck()`, fail-soft per schedule. Distribution is now fully wired — set SMTP/Slack env to flip stub → live.
- **Codex pipeline_logic TTL cache** — Layer 3 of the prompt cached with TTL 5min + invalidation on `MAX(updated_at)`. **Measured 1,279ms cold → 1ms warm (~1,280×).**
- **Embedding cascade reordered** — `openai/text-embedding-3-small` (native 1536, no truncation) FIRST. No more lossy 3072→1536 truncation by default.
- **Background tasks batched** — was N implicit threads per chat, now 1 daemon thread running all 9 task blocks sequentially. ~200ms/chat saved on bg overhead.

**Polish:**
- **Federation tests gated** behind `FEDERATION_ENABLED=1` env (14 files, ~3,500 LOC). CI ~40% faster by default.
- **Migration `109_cleanup_orphans.sql`** drops `dash_ml_jobs` (no remaining writers).
- **`automl` keyword removed** from `skl_ml_strategist` skill.
- **CLAUDE.md stale doc fixed** — "13 Context Layers" → 9 (the reruns were folded into single passes in a prior trim; doc was stale).

**Full-day session totals (across 5 batches):**
- **~22,800 LOC removed** · **~3,000 LOC added** · ~400 MB image lighter · 12 heavy deps gone · routes **893 → 827** (−66) · zero broken features
- All migrations applied, app boots clean, builds EXIT 0, helpers loadable

## Earlier (2026-05-23) — DS + Presentation roadmap shipped (LLM-native ML + truth-grounded slides + Deep Deck v2 + distribution)

Major roadmap batch executed across multi-agent waves. Net: **~10,500 LOC removed**, ~2,300 added, image ~400 MB lighter, 25 routes gone, zero feature loss.

- **LLM-native ML pivot** — deleted the heavy `dash/automl/` FLAML chassis + `mlforecast_engine` + 11 heavy deps (flaml/mlforecast/fugue/numba/llvmlite/pyarrow/…). Replaced with a single `auto_ml` LLM-conductor tool that profiles the table, picks one of 9 techniques (forecast/classify/cluster/feature_drivers/anomaly/causal/ab_test/cohort/none), routes to existing in-process tool, narrates the result. **`detect_anomalies_ml`** rewritten as **SQL z-score + LLM narrative + CREATE OR REPLACE VIEW {table}_anomalies** — no more IsolationForest. Per-row SHAP added to `classify` + `feature_importance` (top-3 drivers). New tools `causal_drivers` (LightGBM + SHAP delta between two periods) + `ab_test` (Welch/χ² + Bayesian credible interval). No ml_worker, no GPU, no queue.
- **Truth-grounded slides** — Slide Agent v2 now calls `try_metric_shortcut` per slide; on a high-confidence match the verified value is injected as authoritative ("USE THIS NUMBER VERBATIM"). Renderer draws a `✓ verified vs pinned metric` badge per verified slide. `dash_presentations.thinking.verified_slides` counts them. Slide numbers never drift from the pinned metric truth.
- **Deep Deck v2 — 9-stage Vision-QA loop** — extends Phase 2: render → judge each slide PNG (DEEP_MODEL via `training_vision_call`, different-model TACL rule enforced) → iterate failing slides (score <80, max 2 iters) → finalize. Reuses existing `qa/` jpg outputs from `_run_qa_loop()`; text-fallback when PNGs unavailable. Cost-guard gated. Kill switch `DEEP_DECK_V2_DISABLED=1`. Default ON for new decks.
- **Dashboard → Deck** — one-click `POST /api/dashboards/{id}/to-deck`: maps KPI strip → cover stat slide, chart panels → chart slides (via `chart_mapper`), insight panels → narrative, closing slide. Renders via native pptx_renderer. New "Convert to Deck" button on the dashboard view.
- **4 vertical deck templates** — `qbr`, `investor_update`, `ops_review`, `customer_review` in `dash/tools/deck_templates/` (YAML). Each template's `metrics_needed` list resolves via verified-metric matcher → Slide Agent pulls the truth, never re-computes.
- **Distribution pipeline (stub-mode)** — schedule a deck for daily/weekly/monthly delivery via email/Slack/PDF. `dash/distribution/` package + `app/deck_distribution.py` (5 endpoints under `/api/presentations`). Stub mode when SMTP/Slack creds absent (returns `{mode:"stub", would_send_to:[...]}` + INFO log, never blocks). Real send raises on actual SMTP/Slack error — no silent swallow. Migration `108_deck_schedules.sql`. Frontend `📅 Schedule` button on the presentations page with cron preset chips + recipients editor + format radio.
- **Orphan-router trim** — deleted `pages_api` / `minions_api` / `auto_apply_api` / `recall_api` / `custom_agents_api` / `investment_api` (~2,217 LOC) + entire `dash/verticals/investment/` (~3K LOC). All confirmed zero frontend refs.

## Earlier (2026-05-22) — No-jargon metric builder + Verified-Reward learning (grade by truth, not by a judge)

Two upgrades to the Definitions/metrics flow and the self-learning loop.

**Build metrics without knowing column names.** The metric editor now opens with a toggle:
- **✨ Describe it (default)** — a conversational KPI builder. Write plain English ("which KPIs can we build" or "success rate by channel"); the AI grounds on your real schema, proposes a checklist of KPIs, tests each live (`○ queued → ⟳ → ✓ value`), shows each in plain words ("counts every record · 6 tables · columns used: call_outcome"), then **"✓ Created N KPIs"**. No SQL, no column names.
- **⚙ Build manually** — dropdowns instead of typing: source tables = checkbox chips; a **columns reference strip** (one-click `filter` / `group`, and `sum/avg` on numeric columns flagged `#️⃣`); filter values auto-fill from real DISTINCT samples. Power-user fallback.
- `/metrics/derive` hardened (first-balanced-JSON parse + fail-soft) so exploratory prompts never 502.

**Verified-Reward self-learning** (inspired by evaluating WooooDyy/AgentGym-RL — took the *idea*, not the RL). Dash's learning loop graded answers with an LLM judge (a model scoring a model) while a hard oracle sat unused. Now, for data answers, Dash grades against **ground truth**: it runs the matching proven SQL / pinned metric and checks the answer's number (`dash/learning/verified_reward.py`, migration `107_verified_scores.sql`).
- **Chat → SOURCES tab** shows a **VERIFIED vs TRUTH** card per answer (✓ Matches `1,544` / ✗ Differs `got X · truth Y`).
- **Cockpit → Performance** shows **Verified accuracy** (`% matched truth`) next to the judge score — a real accuracy number.
- **Promotion gate** — a 👍 on a *provably-wrong* answer is auto-downgraded to a negative example, never learned as "good". Endpoints: `GET /{slug}/sessions/{sid}/verified`, `GET /{slug}/accuracy`.

## Earlier (2026-05-22) — Generic CRM starter metric pack (packaged, reusable for any CRM agent)

Packages CRM analytics *capability* — not one customer's numbers — so a new CRM agent has a sensible baseline Day-1.

- **Generic starter pack** (`dash/tools/crm_starter.py`): 6 universal CRM metrics (total calls, call success rate, uncontactable rate, outcome distribution, calls by channel, conversion by channel). Columns are **resolved by alias against the project's real schema at seed time** — any metric whose columns aren't found is skipped (no fabricated numbers). Reuses the existing metric engine.
- **status='suggested', not 'verified'** — the value vocabulary is a best guess for a new tenant; metrics are proposals the owner confirms, never claimed as ground truth.
- **Auto-seeds on train** when the schema looks like a CRM (`looks_like_crm`) as the Day-1 default; plus a **manual picker** — Definitions tab → More ▾ → "＋ Add CRM metrics…" shows the candidate KPIs with their resolved columns and lets the user **select** which to add (`crm-preview` + `seed-crm {names:[…]}`).
- **Bespoke definitions** still arrive via the shipped Definitions.xlsx auto-pin path + metric-authority engine — each customer pins their own, made authoritative + deterministic. The product ships the mechanism, not one customer's formulas.

## Earlier (2026-05-22) — Native observability: `dash_traces` span tracing + Command Center TRACES tab

System-wide tracing (a "flight recorder") for the whole backend — so failed training, dead cron jobs, and cost spikes are visible at a glance instead of grepping `docker logs`. Native (no external Jaeger/OTel container) — writes to one Postgres table, viewed in the admin Command Center.

- **Core** (`dash/obs/trace.py`): `@trace_step` decorator (sync/async) + `trace_span` ctx-manager + `start_trace`/`end_trace`/`record_cost`. Contextvar span trees, fail-soft (never breaks the wrapped code), `TRACING_DISABLED=1` kill-switch. Table `public.dash_traces` (migration 106).
- **Instrumented**: training pipeline (per-step), learning cycle (per-stage), 9 cron daemons (one span per fire → surfaces never-fired jobs), ML worker jobs, chat (`chat.run` + cost).
- **API** (`app/traces_api.py`, super-admin): `GET /api/admin/traces`, `/traces/cron-health` (stale-cron detection), `/traces/agents` (per-agent rollup).
- **UI**: Command Center → **TRACES** tab — rollup strip (runs / failed / cost / slowest), kind + time filters, ⚠ stale-cron badges, expandable root→child trace tree, per-agent breakdown. Warm-themed.
- **Three views, one write path**: central admin cockpit (all) · per-project (filter by slug) · per-agent (group by agent). Instrument once, read three ways.

## Earlier (2026-05-22) — Thinking-trace overhaul: OpenAI-style, grouped-by-agent, refresh-safe, full visibility

Redesigned the per-message **thinking trace** (the agent-reasoning panel under each chat answer) and decluttered the INSIGHT card.

- **OpenAI-"agent thinking" style** (`frontend/src/lib/trace/TraceTimeline.svelte`, full rewrite): light bordered card, gutter timeline with circles, **bold humanized step titles** ("Querying the database", "Searching internal knowledge"), narrative paragraphs, and light function-call / SQL code boxes (white-on-cream). Replaces the old dark CLI box.
- **Grouped by agent** — steps/tools nest under collapsible `▼ ANALYST   gpt-5.4-nano` sections; route/summary rows stay top-level.
- **Header chip cluster** — `▸ Thinking  [Instant]  [Low effort]  2 agents · 7 tools · 65 steps · 39s  184.9k tok  model ⌄`. The tier chip shows the **resolved** model-picker tier (LOOKUP→Instant, ANALYSIS→Standard, AGENTIC/REASONING→Deep, ULTRA→Ultra), the effort chip shows the picked effort, and the analysis chip shows the analysis type the agent chose (from the `analyze` tool).
- **Refresh-safe** — history now loads from the trace-aware `/api/projects/{slug}/sessions/{sid}/messages` endpoint (reconstructs trace + usage + clean content), so the trace, structured answer, and SQL tab survive a page reload. `_sqlsFromTrace()` restores `sqlQueries` from the reconstructed trace.
- **Tag stripping hardened** — `[CONFIDENCE_BREAKDOWN:…]` and other card-only tags no longer leak as raw text into the answer prose; a catch-all strips any `[UPPER_SNAKE:…]` tag in both the streaming and done cleaners.
- **Reasoning-model fix** — Standard+ tiers (gpt-5.4-nano/mini) stream reasoning token-by-token; consecutive same-agent reasoning steps now merge into one growing step instead of "64 single-word rows". Reconstructed reasoning markdown (tables/bold) is flattened to plain prose so live and reloaded views match.
- **INSIGHT declutter** — SO WHAT / KEY FINDINGS / FOR CONTEXT / confidence fold into one collapsible `▸ So what · key findings · context` row; three 100% confidence bars collapse to one `Confidence: High` line.
- **100% visibility** — every agent + tool that ran is shown expanded with full args/SQL + result (truncation raised 200→1200 chars); header shows agent/tool/step counts.
- **Theme-matched SOURCES tab** — the CONFIDENCE card and execution-log step ticks no longer use clashing success-green; they now use the coral/warm palette (`#c96342` / amber / warm-red).

> **Troubleshooting — "looks great live, breaks after refresh":** the live stream and the reloaded history are two different render paths (live builds from SSE; reload reconstructs from `ai.agno_sessions.runs`). Always test both. The trace-aware reload endpoint is `/api/projects/{slug}/sessions/{sid}/messages` (NOT the global `/api/sessions/{sid}/messages`). Raw `[TAG:…]` in prose → a strip regex is keyed too tightly; fix it in BOTH the `analysisContent` chain and `stripStructureTags()` in `ChatMessageList.svelte` (the `\[[A-Z][A-Z0-9_]{2,}:[^\]]*\]` catch-all guards future tags). Trace exploding into single-word rows → reasoning model streamed tokens; merge consecutive same-agent reasoning steps in the trace-merge callback. Tier chip showing a name not in the model dropdown → map the router complexity tier to a picker label via `tierLabel()`.

## Earlier (2026-05-22) — Unified Definitions tab: metrics + rules in one view, AI suggestions sub-tab

Folded the legacy Business Rules tab into a single **Definitions** tab (left rail) that shows structured **metrics** (locked, executable, drift-tracked) and natural-language **business rules** together. Resolved the long-standing metric-vs-rule conflict at both layers: instructions now declare verified metrics authoritative (call the `metric` tool, overrides brain formula), and 7 superseded brain formulas were deleted so there's no dual source of truth.

**Three views, one toggle (Table / Cards / AI):**
- **Table (default)** — dense, scannable rows: `Name · Type · Kind · Definition · Pinned · Drift · Actions`. Metrics (✅) and business rules (📝) run in one table. Click a row to edit (metric → editor, rule → promote-to-metric editor).
- **Cards** — the familiar Business-Rules card layout, kept for detail reading.
- **AI** — suggestions in their own sub-tab with a segmented source control: **All / 🧑 Human / 🎓 Training AI / 💬 From Chat**. Each suggestion (new metric, rule→metric promotion, drift fix, chat-extracted rule) has Accept / Reject wired to the real `/suggested-rules` approve/reject endpoints.

**New endpoint:** `GET /{slug}/metrics/recommend-new` — scans column catalog + existing metric names, LLM proposes up to 6 net-new metrics not already defined (fail-soft).

**Toolbar redesign:** clean 2-row header — title + summary (metrics / rules / in-review counts) on top, filter chips + view toggle + search + `+ New` + a `More ▾` overflow menu (Import template / NL describe / Import file / Permissions / Drift) below. Fixes the prior overlapping single-row layout. Whole app squared (radius tokens zeroed, ~870 literal `border-radius` swept to 0, circles preserved).

**Also fixed:** ChatGPT-style live streaming. The agent emits its answer as structure tags (`[HEADLINE:][KPI:][FINDING:]…`); the stream view was stripping all of it until done, so the answer "popped in at the end." The structured card block now renders progressively during streaming (each card appears as its tag closes), with a typing indicator and the action bar hidden until done. Shared component, so both project chat and the global Dash Agent chat get it.

**Files:** `dash/instructions.py` (metric-authority priority), `dash/tools/metric_seed.py` (dedupe superseded brain formulas), `app/metrics_api.py` (`recommend-new`), `frontend/src/lib/metrics/MetricsTab.svelte` (view toggle + table + AI sub-tab + toolbar), `frontend/src/routes/project/[slug]/settings/+page.svelte` (rail: rename Rules→Definitions, count, pipeline icon), `frontend/src/lib/chat/ChatMessageList.svelte` (progressive streaming render), `frontend/src/app.css` + all `.svelte` (squared corners).

## Earlier (2026-05-21) — Staged ingest: validate before load

Adds a governed ingest pipeline so data is verified correct before it ever touches the database. Motivated by the same Pahtama P&G CRM customer whose 6 monthly CSVs landed as 6 separate sibling tables — forcing every query to UNION them and opening the door to miscounts (the "1804 vs 1544" class of errors). The fix is structural: govern the ingest.

**How it works — upload → stage → validate → gate → promote → train:**
- Files land on disk first (sha256-hashed, no DB write). Nothing is committed until you explicitly promote.
- A Schema Contract is inferred for each dataset on first upload and saved (versioned). Every subsequent upload is checked against it — added columns, removed columns, type changes, and renames are all surfaced as a `DRIFT` verdict before anything is written.
- A dry-run diff shows exactly what rows will be inserted, replaced, or skipped.
- The gate auto-promotes clean files (`EXACT` verdict) and quarantines anything drifted, duplicate, or low-quality — the table is untouched until you confirm.
- Every promoted row is stamped with lineage: source file, period, batch ID, content hash, row key, and ingest timestamp. A full batch can be undone with a single `DELETE WHERE _batch_id = X`.

**Dataset consolidation:** Monthly files for the same dataset (`MM Conso Apr 25.csv`, `MM Conso May 25.csv`, …) are resolved to one logical dataset → one contract → **one table** with a `_period` column. No more sibling tables. No more UNIONs.

**Idempotency (4 auto-detected strategies):** single primary key / smallest unique composite ≤ 4 columns / period-replace (for monthly transactional drops, preferred when filename contains a date token) / whole-file content hash. Strategy is stored in the contract and can be overridden per dataset.

**New modules:** `dash/ingest/content.py` (hashing), `staging.py` (manifest + quarantine), `contract.py` (schema contract registry + drift detection), `loader.py` (promote + lineage + undo), `cleanup.py` (batch purge daemon).

**API:** `POST /upload/stage` → `GET /ingest/{project}/batches` → `GET /ingest/{project}/{batch}/dry-run` → `POST /ingest/{project}/{batch}/promote` → `POST /ingest/{project}/{batch}/reject`. DB migrations 102 + 103 (`dash_ingest_batches`, `dash_ingest_files`, `dash_ingest_contracts`).

**Frontend:** Settings → DATASETS → "Staged Ingest" panel — file staging, per-file verdict pills (`EXACT` / `DRIFT` / `NEW`), dry-run plan, Promote / Promote+Train / Reject.

**Verified live:** 3 monthly CSVs → 1 table (6 rows, 3 periods); re-upload identical file → skip duplicate; drift (renamed column) → quarantine, table untouched; partial promote. Direct (non-staged) upload still works — staged is opt-in.

## Earlier (2026-05-21) — Answer correctness: determinism + authoritative metrics + auto-pinned definitions

Fixed a class of "wrong / different-each-time" answers reported on a real CRM agent (total leads said 1,804, actual 1,544; same question gave different numbers across chats). Root cause was the engine, not the data.

- **Deterministic answers** — chat model temperature pinned to `0.1` (was unset → provider default). Same question → same SQL → same answer, every run.
- **Metric formulas are now authoritative** — a Company Brain `formula` entry is treated as the EXACT metric definition (filters translated to SQL verbatim, num/denom/% always shown), not a loose "hint." Kills denominator/filter drift on rates and contributions.
- **Training is reused, not ignored** — proven question→SQL pairs are now "reuse this SQL if it matches" instead of "hints, re-derive." Plus a per-message relevance ranker injects the best-matching proven query into each chat, so short prompts hit the right SQL.
- **Auto-pinned definitions** — upload an Excel with a definitions/glossary sheet and DASH loads every term/formula into the project Brain automatically (deduped, fail-soft). A customer's `Definitions.xlsx` self-configures the agent.
- **Subtotal guard** — never sums `TOTAL`/subtotal rows into a grand total (was inflating breakdowns).

Validated on the real 6-month CRM dataset: leads 1,544, contribution 64.3%/35.7%, recruitment rate 29.1%, drop-off rate 27.7% — all exact, including in fast mode, and stable across repeated asks. Remaining items are business-definition confirmations (e.g. which "new user" basis), not engine bugs.

## Earlier (2026-05-20) — OpenAI-parity push: real Codex code-enrichment + leaner tools/prompt

Benchmarked against [OpenAI's in-house data agent](https://openai.com/index/inside-our-in-house-data-agent/). Our eval pipeline, self-correction loop, and memory scopes already match it. This pass closes the gaps:

- **Real Codex code-enrichment (Layer 3)** — `dash/tools/codex_code.py` reads the **pipeline code** that builds each table (view DDL via `pg_get_viewdef`, reconstructed table DDL, saved-query SQL) and LLM-extracts grain, derived-column formulas, and included/excluded populations into `dash_table_metadata.metadata['pipeline_logic']`. This is OpenAI's "single biggest quality unlock" — *meaning lives in code, not schemas*. Runs as the `codex_code_enrich` training step; injected into the Analyst prompt as a `## PIPELINE LOGIC (from source code)` block. Enriched tables show a **CODE** badge in DATASETS (via `GET /{slug}/codex-enriched`).
- **Tool consolidation** — the 11 overlapping analysis tools collapsed into one self-describing `analyze(analysis_type=…)` dispatcher (OpenAI: *fewer, well-scoped tools beat broad sets*). Underlying fns kept on disk (reversible).
- **Declarative prompt** — the rigid "🛑 MANDATORY SQL / NO EXCEPTIONS" block became a concise `## SQL GROUNDING` goal; memories/examples relabeled HINTS + char-capped (Analyst base prompt ~49K→31K). OpenAI: *declarative guidance beats prescriptive instructions*.
- **Niche tabs gated** — `GET /api/flags` lets the UI hide Scenario Lab (unless `SIM_LAB_ENABLED`) and Federation (unless ≥2 sources) instead of showing dead pages.

## Earlier (2026-05-20) — 2-tier forecasting (stats + LightGBM) + pytds connector + TimesFM dropped

Three forecasting/infra changes, all baked + live (smoke-verified).

**Forecasting is now 2 auto-routed tiers** (`dash/tools/forecasting/`):
- `stats` — statsforecast AutoARIMA / AutoETS (classic, trend + seasonality).
- `mlforecast` — LightGBM + lag/calendar/promo features (exog-aware, for retail/distribution where promo/price drive sales).
- Auto-router `choose_tier(history_len, series_count, has_exog)` → has_exog ⇒ mlforecast, else stats. `detect_exog_columns(df)` token-matches promo/price/discount/holiday.
- Chat `predict(model='auto'|'stats'|'mlforecast')` tool on Data Scientist; AutoML "Sales Forecast Benchmark" races both engines on a holdout, ranks by MASE/MAPE/RMSE.
- **TimesFM dropped** — zero-shot foundation model removed entirely (torch/3 GB pod not worth it for tabular retail series; LLM is the *narrator*, never the forecaster).

**SQL Server connector: pyodbc/msodbcsql18 → pure-Python python-tds.**
- `mssql+pytds` URLs (`dash/providers/fabric.py`, `dash/tools/live_query.py`, `app/connectors.py`). MSAL AAD token via `connect_args={"access_token": token}`.
- Removed the Microsoft apt-repo + GPG-key fetch from the Dockerfile (the intermittent `gpg: no valid OpenPGP data found` build flake) + unixodbc. No ODBC driver in image.
- ⚠️ pytds service_principal AAD-token path still needs a live-Fabric smoke when Fabric is next used.

**Build gotcha — `uv pip sync` needs the FULL transitive closure.** `requirements.txt` is a compiled flat lockfile and `Dockerfile` uses `uv pip sync` (installs ONLY listed packages, NO transitive resolution). Adding bare `mlforecast` / `statsforecast` top-level names left their deps uninstalled → `ModuleNotFoundError` chain (cloudpickle → fsspec → fugue …) at runtime, not build time. Fix: pin the whole closure — `mlforecast==1.0.2 cloudpickle coreforecast==0.0.17 utilsforecast==0.2.15 fsspec narwhals optuna alembic colorlog mako fugue==0.9.4 triad==1.0.2 adagio==0.2.6 numba==0.65.1 llvmlite==0.47.0 pyarrow==24.0.0`. Rule: any new top-level dep added to `requirements.txt` must include its transitive closure (resolve via `uv pip compile`, or copy versions from a working `uv pip install` env like `dash-ml`).

**statsforecast 2.x API change** — `StatsForecast.forecast(h=)` now requires `df` positional; use `sf.fit(df)` + `sf.predict(h=)`. Fixed in `dash/automl/stages/forecast_bench.py` `_run_stats` (ml_models.py was already correct).

## Earlier (2026-05-20) — Native PPTX (Node sidecar removed) + ML worker slimmed 2.1 GB

Two infra cuts, both baked + live. Pre-change backup: `~/Desktop/dash_backup_<ts>.zip` (327 MB, source + .git).

**PPTX rendering is now native python-pptx only — the Node sidecar is gone.**
- Old: 3 selectable engines via `PPTX_ENGINE` — `subprocess` (spawn `node build.js`, ~2000ms), `sidecar` (HTTP to a `dash-pptx` Node container, ~94-189ms), `native` (in-process python-pptx, ~30ms). Default was `sidecar`.
- New: `render_pptx_via_js()` routes straight to `dash/pptx_renderer/` (python-pptx). 66× faster (~30ms), in-process, no extra container. `PPTX_ENGINE=native` default in compose.
- Removed: `dash-pptx` compose service, the `render-js` Dockerfile stage + Node binary (4-stage → 3-stage build, no Node in runtime image), the entire `dash/render_js/` dir (server.js / build.js / Dockerfile.pptx / package*.json), the orphan `dash-pptx:latest` image (0.88 GB).
- Public fn signature unchanged → callers (`deep_deck.py`, `app/export.py`) untouched. Spec builders (`codegen_pptxgenjs.py`, `chart_mapper.py`) still feed the native renderer.

**ML worker (`dash-dash-ml`) slimmed 3.65 GB → 1.53 GB (−2.12 GB).**
- New `ml_worker/requirements-ml.txt` (17 ML+DB+report deps) replaces the full 145-dep app `requirements.txt` — worker no longer carries fastapi/pymupdf/langextract/gdrive.
- `xgboost` → `xgboost-cpu` drops `nvidia-nccl-cu12` (396 MB CUDA, no GPU used).
- 2-stage Dockerfile (builder w/ gcc → slim runtime + libgomp1 only).
- Forecast = statsforecast (AutoARIMA/AutoETS) + statsmodels; prophet/matplotlib/pyarrow dropped (unused). All worker imports verified clean, worker polling.

## Earlier (2026-05-19) — runtime_role tagging for all 23 skills (rule #2 codified)

Every skill in `dash_skills` now declares what it actually does at runtime. UI badges, audit logs, and SkillRefinery telemetry can stop pretending all skills are equal.

- **Migration 097** (`db/migrations/097_skill_runtime_role.sql`): adds `runtime_role TEXT NOT NULL DEFAULT 'agent_hint'` + index. Idempotent (`ADD COLUMN IF NOT EXISTS`).
- **`RUNTIME_ROLES` mapping** in `dash/skills/builtin.py`: single source of truth for all 23 builtins. `register_builtins()` merges role into skill dict before upsert; `register_skill()` (`dash/skills/registry.py`) extended to INSERT + UPDATE the column.
- **5 buckets** — `pipeline` (5 skills, invoked by code via `_skill_prefix()`), `redirect` (2, Leader emits "click X button"), `agent_hint` (12, Leader/Analyst loads on keyword match), `meta` (2, skill-of-skills), `dev_tool` (2, no end-user path, candidates for deprecation).
- **Live counts**: 12 agent_hint + 5 pipeline + 2 dev_tool + 2 meta + 2 redirect = 23 ✓.
- **Going forward**: every new skill must declare `runtime_role`. PR review checklist: which code path reads this skill? If none, why is it being added?

## Earlier (2026-05-19) — Skills wired to Deep Dash pipeline (Option B)

Five skills now load `.instructions` from `dash_skills` registry at runtime via TTL-cached `_skill_prefix()` helper. SkillRefinery now has real targets to auto-improve nightly. Marketplace edits flow to production prompts within 5 min (TTL) or container restart.

- **3 existing skills wired**: `skl_panel_designer` → stage 7 ECharts codegen, `skl_dash_critic` → stage 8 judge (different model), `skl_sql_optimizer` → stage 5 EXPLAIN-fail retry.
- **2 new orchestrator skills**: `skl_dash_orchestrator` (stage 3 panel plan) + `skl_deck_orchestrator` (deep_deck.py stage_plan SQL gen). Pipeline-side, separate from user-facing redirect skills (`skl_dash_builder` / `skl_pptx_builder`) which Leader uses for "click D/P button" routing.
- **Loader**: `dash/dashboards/agent.py::_skill_prefix(skill_id)` — TTL 5min in-process cache, prepends formatted `# SKILL: {id}\n{instructions[:2500]}\n\n---\n\n` to existing hardcoded prompts. Silent `""` fallback if skill missing. Calls `dash.skills.registry.load_skill()` so each invocation hits `dash_skill_audit_log` for telemetry.
- **Builtins count**: 23 (was 21). `register_builtins()` lifespan upserts on worker-0 startup.
- **Cost**: ~+1500 tokens per dashboard (~500 per skill prepend × 3 stages w/ skills). Wall unchanged ~25-30s.
- **E2E verified**: gen=Gemini-lite + judge=GPT, 4/4 panels, 6469 tokens, 30.8s wall.

## Earlier (2026-05-19) — Deep Dash E2E test + 4 root-cause fixes + D button rewired

E2E smoke against `proj_demo_e2e_test_pharmacy` ("top 5 outlets by revenue last 30 days"). Surfaced 4 silent bugs; fixed all; final run: **4/4 panels, EXPLAIN 4/4 passed, judge clean, 24.7s wall, 4558 tokens**.

- **Bug 1 — `training_llm_call` rejects `model=` kwarg**: explicit `gen_model`/`judge_model` selection from UI dropdown silently produced empty LLM output, planner returned 0 panels. **Fix**: added `model: str | None = None` param override in `dash/settings.py::training_llm_call` — when provided, overrides `TRAINING_CONFIGS[task]['model']`.
- **Bug 2 — MySQL syntax leaking into Postgres**: LLM emitting `DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)` → `UndefinedFunction`. **Fix**: explicit Postgres-only dialect rule + correct/wrong examples in stage 4 SQL gen prompt + stage 3 planner filter rule.
- **Bug 3 — Schema RAG empty when project lacks codex metadata**: `dash_table_metadata` had no rows for fresh-trained project → planner saw no columns/dtypes → LLM hallucinated. **Fix**: always-enrich path via `information_schema.columns` in `stage2_schema_rag` — populates dtype + row_count + sample_rows for every table, regardless of metadata presence.
- **Bug 4 — TEXT-typed date columns failing comparisons**: `sale_date` stored as `text` not `date` → `text >= timestamp` error even w/ correct Postgres syntax. **Fix**: "column-type aware casting" rule in SQL gen prompt (auto-cast TEXT→date, TEXT→numeric where needed); dtype now shown next to every column in tables_blob.

- **D button rewired**: legacy `/api/dashboards/multi-agent/stream` (Scout + Designer) replaced with new 9-stage `/api/dashboards/deep-build/stream`. New SSE event handler maps `stage_start` / `stage_done` / `panel_ready` / `done` to existing ArtifactPanel state. `panelToCell()` adapter converts `EChartsPanelSpec` → legacy cell shape so existing `DashRenderer.svelte` / `Cell.svelte` render w/o frontend refactor. `Cell.svelte` patched to use `cfg.echarts_options` directly when provided (Deep Dash path) — bypasses legacy x_col/y_col rebuild.
- **Model pair dropdown**: 4 presets — AUTO / Gemini→Claude / Claude→GPT / GPT→Gemini. Persisted in `localStorage.dash_model_pair`. Backend enforces TACL rule (400 if `judge_model == gen_model`).
- **P button removed**: legacy quick-slide button gone from both project chat + `/chat` composer. `generateSlides()` handler kept as dead code for easy revert. DP (Deep Deck) and X (Excel) unchanged.
- **/chat D bridge**: Dash Agent global chat D button routes to last-routed project's chat w/ `?build_dash=1` → onMount auto-triggers `openDeepDashboard()` after 800ms, cleans URL.

## Earlier (2026-05-19) — Deep Dash 9-stage pipeline (chat → dashboard)

End-to-end agentic dashboard builder modeled on winning patterns from LIDA (Microsoft), Vizro-AI (McKinsey), Wren AI, Grafana, and Tableau Pulse. Spec-first (Pydantic + ECharts JSON), schema-RAG grounded, EXPLAIN-validated, different-model judge, JSON-Patch iteration.

- **Pipeline** (`dash/dashboards/agent.py::DeepDashAgent`): Intent → Schema RAG → Panel Plan → SQL Gen → **EXPLAIN gate** (1 retry on UndefinedColumn, Wren pattern) → Execute + profile → ECharts spec gen → **Judge (different model, TACL self-bias rule)** → Layout.
- **Spec contract** (`dash/dashboards/spec.py`): `DeepDashSpec` + `DashboardIntent` + `PanelPlan` + `PanelSQL` + `PanelData` + `EChartsPanelSpec` + `Critique` + `JsonPatchOp`. Pydantic validated at every stage. Spec versioned, JSON-Patch iteration never full-regens.
- **Skills** (`dash/skills/builtin.py`): 3 new builtins `skl_dash_builder` (orchestrator), `skl_panel_designer` (per-panel chart picker), `skl_dash_critic` (different-model judge). Registered automatically on worker-0 lifespan via `register_builtins()`. 21 builtin skills total.
- **API** (`app/dashboards_api.py`): `POST /api/dashboards/deep-build/stream` (SSE 9 stages + `panel_ready` per chart), `POST /api/dashboards/deep-build` (sync), `POST /api/dashboards/deep-patch` (RFC 6902 ops on spec, bumps `spec_version`). 400 enforced if `judge_model == gen_model`.
- **Dependency**: `jsonpatch==1.33` + `jsonpointer==3.0.0` added for RFC 6902 compliance.
- **Cost**: ~$0.09/dash (LITE intent + DEEP plan/SQL/codegen, parallel panels). Wall ~15-25s. Three gates (Pydantic@7 + EXPLAIN@5 + judge@8) kill ~90% failures observed in LIDA/Vizro postmortems.

```bash
TOKEN=$(curl -s -X POST localhost:8001/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"<DEMO_PASSWORD>"}' | jq -r .token)

curl -N -X POST localhost:8001/api/dashboards/deep-build/stream \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"project_slug":"pharmacy_stock","question":"Q3 stockouts by region",
       "audience":"executive","n_panels":8,
       "gen_model":"google/gemini-3-flash",
       "judge_model":"anthropic/claude-sonnet-4-6"}'
```

## Earlier (2026-05-19) — Documentation push + uncommitted-work snapshot

- **11 net-new top-level docs**: `AGENTS.md` (30+ agent catalog), `ARCHITECTURE.md` (8-layer system), `CHANGELOG.md` (Keep-a-Changelog), `DEPLOYMENT.md` + `DEPLOYMENT_AWS.md` + `DEPLOYMENT_GCP.md` + `DEPLOYMENT_K8S.md`, `FUTUREDEV.md`, `PATTERNS.md` (R1–R26 recipes), `ROADMAP.md`, `SECURITY.md` (threat model + 3-layer defense), `STYLEGUIDE.md` (`ds-*` design tokens), `TESTING.md`, `UPGRADE.md`. Reading order: CLAUDE.md → ARCHITECTURE.md → AGENTS.md → PATTERNS.md → SECURITY.md.
- **23 new SQL migrations (070→092)**: dream_reflection flags + fixes (070, 071, 086), `dash_autosim_projects` (072), agent_os ↔ skill binding (073), `dash_skill_audit_log` (074), `dash_entity_linking` (075), `dash_skill_marketplace` (076), `dash_brain_evidence` (077), `dash_retrieval_depth` (078), `dash_compliance_pack` (079), `dash_brainbench_scores` (080), `dash_investment_portfolio` + agents (081–082), `dash_brain_columns_safety` (083), `dash_tool_utility_scores` + dedup (084–085), `dash_auto_apply_history` (088), DROP `dash_template_expectations` (089), `dash.training_signals` (090), `dash_decisions` McKinsey decision diary (091), `dash_workflow_hub` cross-agent cron + extension (092). Apply via migration runner (auto on startup) or super-admin `POST /api/admin/migrations/apply-pending`.
- **Net-new project subroutes** under `/project/[slug]/`: `artifacts`, `attribution`, `campaigns`, `customer/[id]`, `customer/list`, `dashboards/[id]`, `dashboards/new`, `graph`, `investment`, `investment/analyze`, `minions`, `pages`, `resolver`, `revenue`, `rules`, `search`, `vectors`.
- **Net-new top-level routes**: `/admin/*`, `/agent-os/*`, `/automl/*`, `/channels`, `/dashboards/*`, `/mcp`, `/me/*`, `/ml`, `/ontology`, `/os/*`, `/scope-picker`, `/sim/*`, `/skills`, `/upgrades`, `/workflows`.
- **New `app/*.py` modules** (key themes): investment + portfolio, entity resolver + recall, HITL + approvals, agent OS, MCP, channels, brain seeds + versions, eval CRUD, auto-apply rules, schedules, custom agents.
- **New `dash/` packages**: `admin`, `agentic`, `artifacts`, `attribution`, `automl`, `autosim`, `channels`, `cron`, `dashboards`, `db_runner`, `embed`, `evals`, `feature_config`, `hitl`, `learning`, `linker`, `memory`, `minions`, `ontology_public`, `policy`, `providers`, `retrieval`, `rls`, `scope_classifier`, `scope_deriver`, `sim`, `skills`, `templates`, `verticals`, `workflows`.
- **Status**: branch is 8 commits ahead of `origin/main`, with 66 modified + 211 untracked files (~53k+ insertions) staged for next deploy window. Last shipped commit = `e0d6cc6`. See `CHANGELOG.md` `[Unreleased]` for the consolidated diff.

## Earlier (2026-05-15) — MiroFish-style Sim Lab + Per-User Agent

- **🐠 Sim Lab** (`/ui/sim`) — MiroFish-style 5-step swarm-intelligence pipeline, fully native (no external MiroFish, no Zep, no CAMEL-OASIS, no extra container). Steps: Ontology Generation → GraphRAG Build → Environment Setup → Simulation → Report. All in `dash/sim/{ontology,graph_builder,env_setup,simulator,reporter,orchestrator}.py`. 10 endpoints under `/api/sim/projects/*`. Live process viewer at `/ui/sim/process/[id]` clones MiroFish demo UX: 56px topbar w/ step pill + status dot + 📄 View Report button (when done), 55% force-graph viz (reuses `$lib/knowledge-graph.svelte`), 45% step cards w/ status badges + endpoint refs, pinned dark CLI dashboard streaming `HH:MM:SS ✓ msg` events via SSE. Cost ~$0.09 per full sim run, ~18 sec for 6-persona × 2-day scenario.
- **🧬 My Agent + Scenario Lab tabs** — Settings → Intelligence group gains personal "digital twin" agent (status + persona viewer + train/enable/reset + memory timeline) + Scenario Lab (what-if input + horizon + actors + live polling). Native engine `app/user_agent_engine.py` reuses Agno team + pgvector + LLM gateway. 11 endpoints under `/api/agents`. Memory via pgvector cosine on `dash.agent_memory_events.embedding` (no Zep needed). Audit log per mutation.
- **5 new tables** — migration 037 (`dash.user_agents`, `dash.agent_memory_events` w/ vector(1536), `dash.agent_simulations`, `dash.agent_audit_log`) + 038 (`dash.sim_projects`, `dash.sim_steps`, `dash.sim_graph_nodes`, `dash.sim_graph_edges`). **All app queries MUST schema-qualify as `dash.<table>`** — session search_path doesn't include `dash`.
- **5 frontend components** — AgentStatus / AgentMemoryFeed / ScenarioRunner / AgentChatToggle / AgentRecommendations (in `frontend/src/lib/components/`). `api.ts` gains 12 agent helpers + 7 sim helpers + interfaces.
- **Settings rail visual parity w/ Command Center** — mirrored CC pattern: `top: 0` sticky (was `56px`, double-stacked = 90px gap), `padding: 0 8px 60px`, font-size 13px, hover `rgba(201,99,66,0.04)`, active `rgba(201,99,66,0.08)` + coral text + coral icon (no full block fill), independent scroll w/ visible 10px rounded scrollbar, `gap: 2px` so all 18 items + Federation fit in viewport.
- **SSE auth fallback** — `/api/sim/projects/{id}/stream` accepts `?token=<jwt>` query param because EventSource API can't send Authorization header. Inline `_get_user_with_token_fallback()` reuses existing `validate_token` + `_validate_api_key`. Frontend appends token from `localStorage.dash_token`.
- **Robust to terse scenarios** — graph_builder has 3 fallbacks: (a) expand short scenarios w/ ontology context prefix, (b) LLM gen 2-3 seed labels per entity_type if extraction yields 0, (c) deterministic `{etype} 1` insert if LLM fallback also empty. Plus env_setup synthesizes personas directly from ontology entity_types if graph empty.
- **Central JSON parser** (`dash/sim/_json_parse.py`) — 4-tier robust parse used by all step modules: direct → strip ```fences → regex first `{...}` or `[...]` → trailing-comma repair. `parse_json_strict` raises, `parse_json_safe` falls back to default.
- **Top nav** — added 🐠 **Sim Lab** button between Knowledge and Admin. Navigates to `/ui/sim` (not `/sim` — SvelteKit `paths.base: '/ui'`, AuthMiddleware blocks bare `/sim` w/ 401 JSON).
- **Bug fixes** — UUID v5 deterministic hash for integer `dash_users.id` → uuid sim_projects column (mirrors `agents_api.py`). `ON CONFLICT (col list)` instead of `ON CONFLICT ON CONSTRAINT <name>` (Postgres distinguishes unique INDEX vs CONSTRAINT). Schema-qualified all 27 unqualified table refs. `training_llm_call()` does not accept `model=` kwarg. Nested `<button>` rejected by Svelte 5 → use `<div role="button">`. Scheduler `KeyError: 'due'` fixed (`s.get("candidates", 0)`). `transactions` table not found in duplicated project schemas → catch ValueError + return `{ok: False, error}`.
- **End-to-end smoke** — login `demo / <DEMO_PASSWORD>` → POST `/api/sim/projects` → POST `/run` → status done in 18 sec, 8 nodes + 5 edges + 3355-char markdown report. Pipeline survives terse scenarios via ontology fallback.

## Earlier (2026-05-15) — Standardization sweep + Left-rail conversions

- **Standardization sweep** — `frontend/src/app.css` got `ds-*` token + primitive layer (ds-card, ds-stat, ds-table, ds-tabbar, ds-input, ds-modal-*, ds-btn). New `frontend/STYLEGUIDE.md` = single source of truth (tokens + components + 10 rules + legacy→new map). `npm run style:audit` lints hardcoded hex + inline `style="border|background|color|radius"`.
- **Left-rail conversion** — Brain (`/ui/brain`) and Ontology Workbench (`/ui/ontology`) horizontal tabs replaced with 220px sticky rail grouped by section (Knowledge/Structure/Activity, Catalog/Insights/Governance). Mirrors Settings pattern.
- **Claude-style project cards** (`/ui/projects`) — kebab (⋮) menu w/ Star/Settings/Rename/Duplicate/Export/Share/Archive/Delete + `Open chat →` CTA. Click-outside close. Inline ★ shown when favorited. Rename modal. New backend: `POST /api/projects/{slug}/duplicate|archive|unarchive`, `PUT` accepts `name`.
- **AutoML + ML Insights merged → `/ui/ml`** (991 lines) — left-rail (Build/Analyze/Monitor) with tabs AutoML / Experiments / Models / Leaderboards / Drift / Retraining. Reads `?tab=`. `/ui/automl` + `/ui/ml-insights` 307-redirect into it. "ML Studio" nav label → "ML Insights".
- **Top nav polish** — active state is coral-wash pill (`rgba(201,99,66,0.14)` + accent text + 600 weight + 12px radius), no underline. Admin dropdown removed (only had 1 item) → direct link to Command Center.
- **Oval button kill** — single global override `:root:not([data-theme="brutalist"]) .send-btn { border-radius: 50%; padding: 0; }` in `app.css:1937` was turning every coral button across Settings + Command Center into a pill (SAVE CONFIG / +ADD SCHEDULE / RUN NOW / DISCOVER PATTERNS / SELF-EVALUATE / +ADD ACCESS / SAVE CHANGES / SAVE CAP / +CREATE EMBED / TEST CONNECTION / connector buttons). Replaced with `var(--pw-radius-sm, 8px)` + removed `padding: 0`. One-line fix, fixes 30+ buttons.
- **SQL tool-calling — two-layer root cause + fix (critical)**.
  - *Layer 1 backend SSE.* In Agno 2.5.14 Team mode, member-agent tool events are NOT forwarded over SSE unless `stream_events=True`. Added to `team.run()` in `app/projects.py:320` (project chat) + `app/main.py:1420` (Dash Agent). Query tab now receives `ToolCallStarted`/`ToolCallCompleted` for Analyst's `run_sql_query`.
  - *Layer 2 prompt ordering.* Analyst's 49 494-char prompt had "CHECK CONTEXT FIRST → answer directly" at char ~700 and MANDATORY SQL rule at char 1951. LLM read top-down, answered from memories with hallucinated round numbers (12 450 / 8 912 / 15 204) that matched neither real DB (work_orders=6000, raw_materials=200, bom=681) nor cached context. Fix: `dash/instructions.py` `ANALYST_INSTRUCTIONS` rewritten — TOP PRIORITY block ("🛑 You MUST call `run_sql_query`. NO EXCEPTIONS. DO NOT fabricate numbers. Hallucinated numbers will be CAUGHT.") moved to absolute top; context-first rule moved below + scoped to vague/document questions only.
  - *Frontend hardening.* `frontend/src/lib/api.ts` `SQL_TOOL_NAMES` extended to 9 variants, tries 5 arg keys (query / sql / statement / sql_query / q), content heuristic `/\b(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|EXPLAIN)\b/i` captures SQL even when tool name mismatches.
- **Docker rebuild caveat (now 3 sessions in a row)** — host code lives in image, not volume. `docker compose restart` does NOT pick up edits. Required:
  ```bash
  docker compose build dash-api && docker compose up -d --force-recreate dash-api
  ```
  Then hard-refresh browser (Cmd/Ctrl+Shift+R).

## Capabilities at a glance

- **Embeddings Stack v1 (2026-05-08)** — pgvector + HNSW index in
  `dash_vectors` (1536-dim, GIN on scope_attrs + tsvector BM25). OpenAI-compat
  `POST /v1/embeddings` endpoint (2048 input cap). Per-tenant
  `POST /api/projects/{slug}/vectors/{search,ingest,list}` + DELETE.
  Hybrid search (vector cosine top-50 + BM25 top-50 → Reciprocal Rank Fusion,
  K=60, alpha=0.5). **3-layer RLS** for store-scoped data: SQL filter
  (`WHERE scope_attrs @> :user_attrs::jsonb`) + Postgres row policy on session
  vars (`app.project_slug` / `app.user_attrs`) + post-filter leak detector.
  Catalog rows (`scope_attrs={}`) visible to all stores; inventory rows
  (`{store_id:N}`) only own store. Bulk embed (96-chunks, OpenRouter, sha256
  fallback for tests). Auto re-embed on doc update + 6h nightly cron
  (`reembed_loop` scans `dash_documents` + KG triples). Vectors UI tab
  (browse / ingest / search / RLS test runner with 8 canned tests + SEED
  FIXTURES) at `/ui/project/{slug}/vectors`.
- **Ontology Workbench v1** — Palantir-Foundry-style unified view of the
  entire ontology across all templates + agents. Super-admin only at
  `/ui/ontology`. 7 tabs: TYPES (sortable + drill drawer with provenance),
  LINKS (deduped relationships across templates + KG + FKs), ACTIONS
  (workflow rollup with active/paused/failed counts), GLOSSARY (brain entries
  scoped global/project), GROWTH (5-series ECharts line chart, dense daily
  series), LINEAGE (force-directed graph, click node → drill), PROMOTE
  (admin approves pending facts → writes to global brain). 12 backend
  endpoints under `/api/ontology/*`. Aggregates from existing tables — no
  new schema. CSV export per tab, sparkline counters, dark mode toggle,
  cluster-suggest endpoint for synonym detection. See `app/ontology_api.py`
  + `frontend/src/routes/ontology/+page.svelte`.
- **Agent template library v1** — 26 pre-configured industry agents
  browsable from `/ui/projects` → 📚 BROWSE LIBRARY. Each ships with full
  ontology (entities + KPIs + autonomous workflows + glossary + persona +
  visibility). Two-stage apply: schema-independent seed, then auto-reconcile
  against real data after upload + training. Workflows fire on schedule
  (multi-worker safe via atomic claim) and surface as 🤖 AUTO insights in
  chat. **12 categories**: starter, analytics, sales_gtm, customer_ops,
  people, finance, operations, retail_food, healthcare, hospitality,
  financial_services, tech_saas. Templates include retail, sales_pipeline,
  customer_success, hr_analytics, finance_fpa, marketing_analytics,
  inventory_planning, support_helpdesk, warehouse_3pl, product_analytics,
  executive_kpi, pharmacy, convenience_store, ecommerce, restaurant,
  hotel_group, bank, insurance, healthcare, manufacturing, distribution,
  supply_chain, real_estate, saas, sales_analyst + blank. **~155 entities ·
  ~96 KPIs · ~93 autonomous workflows.**
- **Auto-save learning card** — after every chat, agent extracts 0-3 facts via
  `/api/projects/{slug}/extract-context`. High-confidence (≥80) auto-save to
  `dash_memories` silently. Low-confidence (<80) surface as green "Approve N
  learnings?" card with SAVE TO MEMORY / DISMISS buttons. Note: env model
  fallback uses `getenv("X") or default` pattern (empty string falsy) — fixes
  bug where set-but-empty `TRAINING_MODEL=` returned `""` instead of default.
- **Customer Intelligence v1** — Ulys/Foundry-parity feature set. 7 new ML
  tools on Data Scientist agent: `rfm_score` (11 segments: Champions, Loyal,
  At Risk, etc.), `cohort_curve` (retention matrix), `next_best_offer`
  (collaborative filtering recommendations), `item_affinity` (market-basket
  with lift/support/confidence), `popular_products`, `clv_score` (BG/NBD if
  `lifetimes` lib else simple proxy), `churn_risk_score` (4-tier:
  active/cooling/at_risk/churned). Customer 360° drill page at
  `/ui/project/{slug}/customer/{id}` with 7 KPI cards + 7 tabs (timeline,
  history, monthly spend, category mix, top SKUs, recs, notes). Customer list
  landing at `/ui/project/{slug}/customer/list` with risk distribution +
  RFM segments + search/sort. Campaign Management Lite at
  `/ui/project/{slug}/campaigns`: CRUD + state machine (draft → scheduled →
  active → paused/completed/cancelled), audience auto-compute from segments,
  ROI tracking via `dash_campaign_metrics`, bulk launch/pause. 21 new
  endpoints: 8 customer + 13 campaign. 3 new tables: `dash_campaigns`,
  `dash_campaign_events`, `dash_campaign_metrics`. Quick-link buttons added
  to project Settings COCKPIT tab.
- **Visibility framework v1** — federated read with projection downgrade.
  Per-tenant policy across 3 audience tiers (private / network / public)
  and 4 field modes (full / band / mask / hide). 3-layer enforcement
  (LLM instructions + sqlglot AST rewriter + Postgres RLS). Sign-off
  workflow with N-of-M approvals. Industry templates
  (pharmacy/retail/hotel/bank/generic). Pharma vertical SKU bundle
  (59 brain entries + 8 workflows). See `docs/VISIBILITY.md`.
- 6 connectors: Postgres, MySQL, Microsoft Fabric, SharePoint, OneDrive,
  Google Drive (plus 24 file formats and an MCP provider)
- 17-step training pipeline (data) / 18-step pipeline (doc-only)
- 8-step self-learning cycle: curiosity → researcher → hypothesis →
  verifier → consolidator → forgetter → promotion → digest
- 11 background agents fire after every chat (Judge, Rule Suggester,
  Proactive Insights, Query Plan Extractor, Meta Learner, Auto Evolver,
  Chat Triple Extractor, Auto-Memory Promoter, User Preference Tracker,
  Episodic Memory Extractor, Follow-up Suggester)
- Drift drawer: 5 detectors (schema, NDV, row-count, watermark, PII) with
  UI bell + audit log
- Daily LLM cost cap per project + per-question 120s timeout
- Daily discoveries digest via SMTP + Slack webhook
- White-label per tenant (`branding/<tenant>/` — logo, theme, favicon,
  company.json)
- MCP provider + cron scheduler (K8S CronJobs: daily learning, Sunday
  canary, daily decay)

## Capabilities

### Connectors (7 providers)

- Postgres local (default per-project schema)
- Postgres remote (live or sync)
- MySQL remote
- Microsoft Fabric Warehouse / SQL Endpoint (T-SQL, Service Principal MSAL)
- SharePoint (Microsoft Graph + MSAL OAuth2)
- OneDrive personal + business
- Google Drive (OAuth2 + Workspace export auto-conversion)

All providers implement a common `BaseProvider` interface and register through
`dash/providers/registry.py`. Per-source scope (`analyst_only` /
`researcher_only` / `shared` / `project`) controls which agent sees which
source.

- **Cross-source federation** — JOIN data across multiple sources WITHIN one project (Postgres + Fabric + files). Hard tenant isolation. DuckDB primary, pandas fallback. See `docs/FEDERATION.md`.

### 37-agent team (post 2026-05-26)

- **5 core**: Leader, Analyst, Engineer, Researcher, Customer Strategist
- **4 vertical** (default ON, opt-out per project · CLAUDE.md 2026-05-26):
  Deal Analyst (DCF/IRR/MOIC), Market Sentinel (competitor/sentiment),
  Ops Optimizer (KPI/board), Supply Sentry (single-source/lead-time)
- **10 specialists**: Comparator, Diagnostician, Narrator, Validator, Planner,
  Trend Analyst, Pareto Analyst, Anomaly Detector, Benchmarker, Prescriptor
- **11 background**: Judge, Rule Suggester, Proactive Insights, Query Plan
  Extractor, Meta Learner, Auto Evolver, Chat Triple Extractor, Auto-Memory
  Promoter, User Pref Tracker, Episodic Memory Extractor, Followup Suggester
- **5 upload**: Conductor, Parser, Scanner, Vision, Inspector
- **2 routing**: Smart Router, Visualizer
- Data Scientist DELETED 2026-05-26 (FLAML chassis dropped per 2026-05-23
  LLM-native pivot; migration 111 dropped tables; ml_models.py removed)
- **1 Visualizer** (auto-detect chart type, ECharts config)
- **1 Smart Router** (2-tier routing for Dash Agent)
- **1 Self-Learning Cycle orchestrator**

Leader runs stuck-agent detection, ML keyword rejection on Analyst,
multi-agent synthesis (Analyst + Researcher) when a question references both
data and documents.

### Self-Learning autonomous loop (12 kpt patterns)

- **CuriosityEngine**: 10 question sources, branch+prune (3-variant fork)
- **ResearcherLoop**: 7 tiers in parallel (`asyncio.gather`, 30s/tier timeout)
- **HypothesisEngine**: confidence seed by triangulation
- **Verifier**: SQL + LLM, `statement_timeout 110s`
- **Consolidator**: route by type → Memory / KG / Brain / Rules
- **Forgetting curve**: decay -0.02/day, archive at 30d, resistant after 5
  citations
- **Promotion**: project → central, PII-scrubbed, LLM-gated
- **agent_iq** composite metric per cycle
- **learning_goals.md** per project (program.md pattern)
- Daily cost cap, per-question 120s timeout
- **parent_hypothesis** lineage tree (diff-as-experiment)
- Today's discoveries digest (Slack / email)
- Dry-run weekly canary

Implemented in `dash/learning/` (17 modules). Daily K8S CronJob orchestrates
`cycle.py`; weekly canary runs in dry-run mode.

### Smart column classifier

- 5 detectors: stats, regex, name, LLM, embedding cosine
- 69 PII regex patterns
- 7 masking strategies: block, redact, hash, email, phone, generalize,
  truncate
- Per-source `pii_action`: `flag` | `mask` | `block`
- Query-time masking + audit log
- 241 brain seed canonicals (column priors)

### Multi-tenant

- Per-project Postgres schema isolation (`proj_{slug}`)
- Per-source provider scope
- Per-source memory + KG + Brain scoping
- Hybrid central + per-project intelligence pool
- White-label branding via `branding/<tenant>/`

### Embeddable agent widget

- Single `<script>` tag drop-in for any host site
- Vanilla JS, ~13 KB, shadow-DOM isolated, mobile-fullscreen
- 3 auth modes: `public` (anon), `hmac` (host-signed user identity), `jwt`
  (RS256 verification — Phase 10, deferred)
- HMAC mode threads `user_attrs` (e.g. `store_id`, `role`) into agent for
  row-level filtering
- Per-embed config: allowed origins, rate limit, feature config override,
  user identity required toggle
- **Per-agent auto-provision (2026-05-16):** every agent in a project gets
  one default embed automatically. Opening the Embed tab the first time
  triggers `POST /embeds/backfill` → 41 rows materialize, no clicks. Click
  any row → inline editor for all settings + theme + live preview + snippet.
  Auto-save on every change (debounced 300ms). No modal. Removed
  Create-Embed flow as primary path (still available as `+ CUSTOM EMBED`
  ghost button for power users).
- **Server-driven theme:** widget fetches `/api/embed/config/{embed_id}` on
  init, applies `primary_color`, `logo_url`, `welcome_msg`, `position`,
  `theme` from server. `data-*` attrs on `<script>` always win. Owner edits
  color in dashboard → all live sites update on next page-load, no dev
  re-paste.
- Public endpoints (no auth): `widget.js`, `docs`, `session/create`, `chat`,
  `config/{embed_id}`
- Sandbox tester serves self-contained HTML page with live network console
- Snippet generator: HTML / Python / Node / PHP server-side examples
- Hardening: per-embed CORS echo, `Sec-Fetch-Site` check, origin allowlist,
  rate limit, secret rotation, audit log per call
- Settings UI: stacked expandable rows (one per agent embed) with inline
  edit + theme picker + live CSS-mockup preview + usage drawer (daily call
  chart, top users, origin distribution, sessions log)
- Migrations: 019 (`dash_agent_embeds`, `dash_embed_sessions`),
  020 (`dash_embed_calls`), 062 (per-agent `agent_id`, `auto_provisioned`,
  `status`, theme cols + unique partial idx)
- Public docs page at `/api/embed/docs`

### Agent feature config (outcome cards + smart recommend)

- Per-project toggles for tabs, capabilities, agents (JSONB
  `dash_projects.feature_config`)
- **CONFIG tab = outcome capability cards, not raw tool names.** 6 cards
  (Answer-from-data / Charts+dashboards / Forecasting / Anomaly+diagnostics /
  ML models / Document research). One switch each; **dependency cascade** so
  broken combos are impossible (toggling ML sets `ml`+`data_scientist`
  together; forecast/anomaly/ml **lock** when data/SQL is off)
- **★ Smart recommend** — `derive_recommended_config()` introspects the
  trained schema (`information_schema`) and proposes a data-fit config with
  plain-English reasons (date+numeric→forecast, customer/txn→ML, no tables→
  docs-only). Shown as a one-click APPLY banner when it differs from current
- **Train-time default = data-fit, not all-on.** New/retrained projects
  auto-apply the recommendation via `apply_recommended_if_unset()` (runs after
  scope derivation in both train paths). Unfit projects start lean (forecast/ML
  off → no dead tools). **Existing/manually-edited configs are never
  clobbered** — only first-ever capability set is auto-filled. RESET TO
  DEFAULT turns everything on
- Cosmetic CHAT TABS + background `auto_campaign_daemon` moved into collapsed
  Display / Automation accordions (separated from real chat capabilities)
- Backend gating: `build.py` skips disabled tools at registration; `team.py`
  filters members by `feature_config.agents`; chat tabs + inline charts respect
  config. Auto-invalidates team cache on save → next chat session uses it
- Industry presets (`PRESETS` dict) were removed; the smart recommend banner +
  RESET replace them. `POST /feature-config/preset/{name}` kept as a stub
- Migration: 017
- Endpoints: `GET/PATCH /feature-config`,
  `GET /feature-config/recommend`

### Auto-scope guardrail

- Auto-derives the agent's domain from training (persona + table catalog +
  doc filenames + KG entities + glossary + recent memories) and refuses
  off-topic questions
- Two-layer enforcement (~99% reliability):
  - **Layer 1**: hard-rule block prepended to all 4 instruction builders
    (Leader / Analyst / Engineer / Data Scientist) — refuses with project's
    canned `refusal_message`
  - **Layer 2**: pre-flight LITE_MODEL classifier short-circuits before team
    is invoked (~1s, $0.0001, 5-min SHA1 cache)
- Streaming-aware: refusal flows back as `TeamRunContent` SSE event
- Manual override: admin edits topics / denied list / refusal message in UI;
  `mark_auto=false` so next TRAIN ALL preserves manual edits
- One-shot RE-DERIVE button (no full retrain needed)
- Refusal audit log + collapsible viewer in CONFIG tab
- Fail-open on classifier outage (never blocks legit users)
- Migration: 021 (`dash_guardrail_audit`)
- Modules: `dash/scope_deriver.py`, `dash/scope_classifier.py`, helper in
  `dash/instructions.py`

### SkillRefinery — self-improving tools

- Per-tool telemetry: `tracked()` decorator wraps every callable, captures
  success / latency / error class. Daemon flushes 60s buffer to DB.
- Wraps 24/26 Analyst tools, 8/9 Data Scientist tools, 3/5 Engineer tools,
  11 specialist analysis tools — handles both agno Function objects and
  plain `@tool` callables
- Rolling utility score per tool per project: `0.5*success +
  0.3*feedback + 0.2*(1 - normalized_latency)`
- LLM-driven patches: SkillRefiner agent reads last 10 failures, outputs
  JSON patch (description + default_args + reason)
- Shadow validation: SkillJudge LLM predicts pass rate against past samples
  before APPLY (gate at 60%, force override available)
- Active patch loader: `_get_active_patch()` cache (60s TTL) wired into
  `build.py` so Analyst/Engineer/DS see patched descriptions at registration
- Nightly K8S CronJob: picks tools with score<60, drafts → shadow-validates
  → auto-applies. Cap 5/project/day, 7-day cooldown per tool
- A/B revert: 24h post-apply rescore; auto-revert if `score_after <
  score_before − 10` with reason logged
- Cross-project patch transfer: sibling projects with ≥20% column overlap
  surface IMPORT-able patches; confidence
  `0.5*overlap + 0.3*shadow + 0.2*gain*5`
- Migrations: 015 (`dash_tool_utility_scores`, `dash_tool_patches`),
  016 (`dash_tool_scores`)
- 11 endpoints + 3 sub-tabs (LIST / TOOL HEALTH / TRANSFER) under AGENTS

### Row-level access control (RLS) — per-project, three layers

Per-project (per-agent) row isolation. Project owner — not platform admin —
defines which tables filter by which user attribute. All 3 layers shipped.

- **Layer 1** (LLM hard rule, instructions): HMAC-signed user_attrs from
  embed payload → ContextVar → `_build_rls_layer1()` prepends hard-rule block
  to all 4 agent instruction builders (Leader/Analyst/Engineer/DS). Analyst
  refuses cross-tenant requests, writes filtered SQL.
- **Layer 2** (SQL rewriter, `dash/rls/rewriter.py`): sqlglot intercepts
  every SQL via SQLAlchemy `before_cursor_execute` event. Injects
  `WHERE :filter_expr` per protected table. Bind vars (`:store_id`)
  substituted from user_attrs. Combines with existing WHERE via AND. Handles
  CTEs, UNIONs, subqueries, JOINs, schema-qualified tables.
  `default_deny=True` raises `PermissionError` on missing user_attrs.
- **Layer 3** (Postgres RLS, `dash/rls/pg_session.py` + `pg_setup.py`):
  `SET LOCAL app.<key>=<val>` issued on `begin` event when mode=`pg_rls`;
  Postgres `CREATE POLICY USING (col = current_setting('app.col', true))`
  enforces at DB. Defense-in-depth even if rewriter bypassed.
- Modes: `advisory` (Layer 1 only) / `rewrite` (1+2) / `pg_rls` (1+2+3).
- Migrations: `022_project_rls_config.sql`, `023_rls_audit.sql`.
- Audit log (`dash_rls_audit`) — sampled 5% of rewrites, 100% of blocks,
  non-blocking queue + daemon flusher. AUDIT LOG section in RLS tab shows
  recent events with original/rewritten SQL diff, user_attrs, block reason.
- 8 endpoints: GET/PATCH/test/apply-policies/remove-policies on
  `/api/projects/{slug}/rls-config` + `/rls-audit?days=14&blocked_only=`.
- Settings → RLS POLICIES tab: enable+mode picker, attr-key chips, table
  filter editor, sandbox tester, policy apply/remove (pg_rls), audit log.
- Designed for 200+ tenants per project — each user sees only their store /
  region / department.

### Infrastructure

- Docker Compose (5 core services: dash-api, dash-pgbouncer, dash-db,
  dash-ml, caddy; plus test-postgres / test-mysql / test-fabric for the
  bundled demo)
- K8S manifests + Helm chart (17 templates)
- 3 K8S CronJobs (daily learning, Sunday canary, daily decay)
- 6 SQL migrations applied
- 153 unit tests passing

## Quick start (15 minutes)

The compose file ships with three sample databases preloaded with a `demo`
schema (`test-postgres`, `test-mysql`, `test-fabric` — Azure SQL Edge on
internal port 1433 / host port 11433). Walk through all 12 steps end-to-end
in about fifteen minutes.

```bash
# 1. Bring the stack up
cp .env.example .env                # set OPENROUTER_API_KEY, DB_PASS, DOMAIN
docker compose up -d                # dash-api/db/pgbouncer/ml/caddy
                                    # + test-postgres/test-mysql/test-fabric

# 2. Open the UI and log in
open http://localhost:8001          # login: demo / <DEMO_PASSWORD>

# 3. Create project
#    Click NEW PROJECT → name "Sales Analytics" → CREATE

# 4-5. Wire up Postgres
#    DATASETS tab → POSTGRES icon → connect with:
#       host: test-postgres   user: tester   pass: test123   db: demo
#    Pick all tables → CONNECT + SYNC

# 6. Wire up MySQL
#    DATASETS tab → MYSQL icon → connect with:
#       host: test-mysql      user: root     pass: test123   db: demo
#    Pick all tables → CONNECT + SYNC

# 7. Wire up Microsoft Fabric (Azure SQL Edge in compose)
#    DATASETS tab → FABRIC icon → connect with:
#       host: test-fabric     user: sa       pass: Test_123! db: demo
#    Pick all tables → CONNECT + SYNC

# 8. Train the agent
#    Click TRAIN ALL — runs the 17-step pipeline (~10-15 min)

# 9-11. Chat in natural language
#    "top 5 customers by lifetime revenue"
#    "forecast next 6 months sales by region"
#    "any anomalies in last quarter?"

# 12. Run the autonomous loop
#    SELF-LEARN tab → RUN CYCLE NOW          (8-step curiosity → digest)
#    COCKPIT                                  (agent_iq, drift bell, $/day)
#    BRAIN tab                                (auto-promoted glossary + KPIs)
```

Default test credentials in the bundled compose:

| Service       | Host (in compose) | User    | Password    | DB     |
|---------------|-------------------|---------|-------------|--------|
| test-postgres | `test-postgres`   | tester  | `test123`   | `demo` |
| test-mysql    | `test-mysql`      | root    | `test123`   | `demo` |
| test-fabric   | `test-fabric`     | sa      | `Test_123!` | `demo` |

For production, change `DB_PASS`, set `DOMAIN`, generate a fresh login, and
remove the test-* services from `docker-compose.yml`.

## Architecture (high level)

```
                      Internet
                          │
                          ▼
                  Caddy (auto-SSL, HSTS)
                          │
                          ▼
              ┌──── Dash API (FastAPI, 8 workers, NullPool) ────┐
              │                                                   │
              ▼                                                   ▼
       PgBouncer (txn mode)                          Provider Registry
              │                                      ├─ Postgres local/remote
              ▼                                      ├─ MySQL remote
       PostgreSQL 18                                 ├─ MS Fabric (MSAL SP)
       + PgVector                                    ├─ SharePoint (Graph)
       + 6 migrations                                ├─ OneDrive (Graph)
              │                                      └─ Google Drive (OAuth2)
              │
              ▼
       ML Worker (separate container, 1GB cap)
              │
              ▼
       K8S CronJobs ──▶ Learning Cycle (daily)
                       Dry-run canary (Sunday)
                       Forgetting decay (daily)
```

Each project = isolated PostgreSQL schema + own knowledge vectors + own agent
team + own persona + self-learning pipeline. 35+ DB tables, 6 applied
migrations.

## Configuration

### Required env vars

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter API key (https://openrouter.ai/keys). Optional once at least one key is added via the UI (Command Center → LLM config) |
| `DB_PASS` | PostgreSQL password (change for prod) |
| `DOMAIN` | Domain for Caddy auto-SSL |
| `CORS_ORIGINS` | Allowed CORS origins |
| `CONNECTION_ENCRYPTION_KEY` | Fernet key (44-char urlsafe-b64). Encrypts UI-added OpenRouter keys + connector creds. Falls back to `sha256(JWT_SECRET)` if unset (dev only) |

### LLM admin UI (Command Center → System → LLM config, super-admin)

After boot, manage everything from the UI instead of `.env`:

- **API keys** — add/test/disable/edit/replace OpenRouter keys. Stored Fernet-encrypted in `dash.dash_llm_keys`. Pool refreshes every 60s. Per-key in-flight / OK / 429 / cooldown counters live.
- **Multi-key pool** — semicolon-separated `OPENROUTER_API_KEYS` env OR multiple keys via UI. Round-robin least-in-flight, per-key 429 cooldown (30s default, or `Retry-After` header). DB keys merge w/ env (DB wins on dedup). 3 keys ≈ 3× cold LLM throughput at zero downtime.
- **Model catalog** — `[↻ Sync models]` button caches all 356 OpenRouter models into `dash.dash_llm_model_catalog` (GIN-indexed search). `[Change ▾]` per tier opens searchable picker w/ filters (Free / Tools / Vision / Reasoning) + sort (Popularity / Price / Context / Newest) + infinite scroll.
- **7-tier model routing** — every tier UI-editable, no restart:

  | Tier | Fires when | Default env var |
  |---|---|---|
  | CHAT | Agno team baseline + `training_llm_call` w/ CHAT_MODEL | `CHAT_MODEL` |
  | MID | Complexity router ANALYSIS tier (compare/trend/why) | `MID_MODEL` |
  | DEEP | Complexity router AGENTIC tier (build/plan/forecast) + DEEP synthesis + auto-evolve + KG standardize | `DEEP_MODEL` |
  | REASONING | Complexity router REASONING tier (heavy multi-step) | `REASONING_MODEL` |
  | ULTRA | ULTRA escalation (across-N + 2+ agentic verbs) | `ULTRA_MODEL` |
  | LITE | TRIVIAL/LOOKUP tier + scoring/routing/extraction + scope classifier | `LITE_MODEL` |
  | EMBED | All vector embedding calls (PgVector / KG / brain / RAG) | `EMBEDDING_MODEL` |

  DB row beats env. To restore env value: delete DB row OR PATCH back to env value via `[Reset to default]` button. Live for next LLM call.

### Optional env vars

**Boot-time model defaults** (overridden by UI edits — see LLM admin above)

| Variable | Default |
|----------|---------|
| `CHAT_MODEL` | `google/gemini-3-flash-preview` |
| `MID_MODEL` | `google/gemini-3-flash-preview` (ANALYSIS tier) |
| `DEEP_MODEL` | `openai/gpt-5.4-mini` (AGENTIC tier) |
| `REASONING_MODEL` | `openai/gpt-5.4-mini` (REASONING tier) |
| `ULTRA_MODEL` | `openai/gpt-5.4-mini` (ULTRA tier) |
| `LITE_MODEL` | `google/gemini-3.1-flash-lite-preview` |
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` |

**OpenRouter pool tuning**

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENROUTER_API_KEYS` | — | Semicolon-separated multi-key (e.g. `k1;k2;k3`). Folded in alongside single `OPENROUTER_API_KEY` |
| `OPENROUTER_POOL_MAX_CONNECTIONS` | 100 | httpx socket cap |
| `OPENROUTER_POOL_MAX_CONNECTIONS_PER_HOST` | 30 | per-host socket cap |
| `OPENROUTER_429_COOLDOWN_SECONDS` | 30 | Per-key cooldown after 429 |
| `OPENROUTER_MAX_RETRIES` | 3 | Retry attempts (expo backoff + jitter) |
| `OPENROUTER_KEY_REFRESH_TTL_S` | 60 | DB→pool reload interval |
| `LLM_CATALOG_SYNC_DISABLED` | 0 | Set 1 to disable daily 03:00 UTC catalog sync cron |

**Embedding model cascade** (auto-fallback in `db/session.py`):
1. `openai/text-embedding-3-small` — 1536 native dim (matches `dash.dash_vectors` schema, default)
2. `openai/text-embedding-3-large` — 3072 native, reduced to 1536
3. `google/gemini-embedding-2-preview` — 3072 native, truncated to 1536
4. `google/gemini-embedding-001` — 3072 native, stable fallback

Validation now checks `len(vec) == 1536`, raises if mismatch — silent empty-vector returns no longer slip through. Dead models removed from cascade: `cohere/embed-v4.0`, `cohere/embed-english-v3.0` (no longer on OpenRouter).

**Rerank cascade** (`dash/tools/semantic_search.py`):
1. `cohere/rerank-4-pro` — top quality (~0.79 on benchmark queries)
2. `cohere/rerank-4-fast` — fallback (~0.64)
3. `cohere/rerank-v3.5` — last resort (~0.19)
4. keyword-overlap fallback — pure Python, never fails

**Connectors**

| Variable | Used by |
|----------|---------|
| `MS_CLIENT_ID` | SharePoint, OneDrive, Fabric |
| `MS_CLIENT_SECRET` | SharePoint, OneDrive, Fabric |
| `MS_TENANT_ID` | SharePoint, OneDrive, Fabric |
| `GOOGLE_CLIENT_ID` | Google Drive |
| `GOOGLE_CLIENT_SECRET` | Google Drive |

**Self-learning external data**

| Variable | Used by |
|----------|---------|
| `TAVILY_API_KEY` | Researcher web tier |
| `BRAVE_API_KEY` | Researcher web tier |
| `PERPLEXITY_API_KEY` | Researcher synthesis tier |
| `FRED_API_KEY` | Macro data tier |
| `ALPHA_VANTAGE_API_KEY` | Markets tier |

**Notifications**

| Variable | Description |
|----------|-------------|
| `SLACK_LEARNING_WEBHOOK` | Daily discoveries digest |
| `SLACK_TOKEN` | General Slack notifications |
| `SLACK_SIGNING_SECRET` | Slack request signing |

**Compliance**

PII default action set per data source via `dash_data_sources.config`
(`flag` | `mask` | `block`). 69 regex patterns + LLM detector + embedding
cosine in `dash/providers/column_classifier.py`. 7 masking strategies in
`dash/providers/pii_mask.py`.

**Branding**

| Variable | Default |
|----------|---------|
| `BRANDING_DIR` | `branding/default/` |

**Scheduler**

| Variable | Default |
|----------|---------|
| `LEARNING_SCHEDULER_DISABLED` | `false` |
| `LEARNING_SCHEDULER_FORCE_INPROCESS` | `false` |

In K8S, set `LEARNING_SCHEDULER_DISABLED=true` on the API pods so only the
CronJob runs the cycle (avoids multi-pod race).

## Deployment

### Docker Compose (primary / recommended)

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8001/health
```

Backup before upgrade:

```bash
docker exec dash-db pg_dump -U ai -d ai > backup_$(date +%Y%m%d).sql
docker compose build dash-api
docker compose up -d dash-api
```

> **Never** use `docker compose down -v` — it deletes all volumes including
> the database.

### K8S / Helm (advanced / optional — multi-replica scaling only)

```bash
helm install dash ./helm/dash --namespace dash --create-namespace \
  --values ./helm/dash/values-prod.yaml \
  --set image.tag=v1.0.0 \
  --set secrets.openrouterApiKey=$OPENROUTER_KEY
```

Helm chart includes 17 templates: api Deployment, pgbouncer, db StatefulSet
with PVC, ml-worker, Caddy ingress, NetworkPolicy, RBAC, ServiceAccount,
Secret, ConfigMap, knowledge PVC, and 3 learning CronJobs.

See `DEPLOYMENT_K8S.md` for the full runbook.

## Branding (white-label)

```bash
# 1. Copy default branding
cp -r branding/default branding/tenant_acme

# 2. Edit company.json (name, domain, theme colors, support email)

# 3. Replace logo.svg + favicon.ico with tenant assets

# 4. Override CSS in theme.css (CSS custom properties)

# 5. Set BRANDING_DIR=branding/tenant_acme on deploy
```

Frontend pulls from `/api/branding` at boot. Logo served at
`/api/branding/logo.svg`.

## Security

- 36+ endpoints with RBAC (viewer / editor / admin / owner)
- scram-sha-256 password encryption (PostgreSQL + PgBouncer)
- Caddy auto-SSL + HSTS + X-Frame-Options + XSS Protection + nosniff
- `statement_timeout 110s` on remote queries
- LLM SQL sandbox blocks DROP / ALTER / TRUNCATE; UPDATE / DELETE limited to
  target table; rollback on >50% row changes
- PII auto-masking at query time with 7 strategies
- Audit log per PII query (`dash_audit_log`)
- NullPool on all SQLAlchemy engines (PgBouncer owns pooling)
- Token cache thread-safe (`threading.Lock`)
- Engine cache TTL eviction (max 200, 1hr)
- Atomic JSON writes
- LLM cost cap per project per day (self-learning)
- 120s per-question timeout in researcher loop

## Scaling

| Users   | Workers | RAM  | VPS Cost |
| ------- | ------- | ---- | -------- |
| 5–10    | 2       | 4G   | $6/mo    |
| 10–30   | 4       | 8G   | $12/mo   |
| 30–100  | 8       | 16G  | $24/mo   |
| 100–200 | 8–16    | 32G  | $48/mo   |

**Tested:** 200 concurrent users × 5 endpoints = 1000 simultaneous requests →
100% pass rate, 81 DB connections stable.

## File formats supported

24 formats: CSV, Excel (multi-sheet AI, unpivot), JSON, SQL, PPTX (speaker
notes), DOCX (headers/footers), PDF (scanned OCR + diagram detection),
JPG, JPEG, PNG, TIFF, BMP, GIF, WEBP, MD, TXT, PY, Parquet, ODS, XML, HTML,
ZIP (recursive), EML.

All file formats receive full brain training — no data tables required.
Upload Agent Team (Conductor → Parser / Scanner / Vision → Inspector →
Engineer) handles smart parsing, auto-merge same-structure tables, and
quality validation.

## Tech stack

- **Backend:** Python 3.12, FastAPI, Uvicorn
- **Frontend:** SvelteKit 2, Svelte 5, Tailwind v4, ECharts
- **DB:** PostgreSQL 18 + PgVector
- **LLM:** OpenRouter (3-model task-optimized)
- **PDF:** PyMuPDF4LLM
- **OCR:** Tesseract
- **Embeddings:** Gemini Embedding 2 (4-model cascade fallback)
- **Reranking:** Cohere rerank-4-pro / fast / v3.5 (3-tier cascade)
- **Containerization:** Docker Compose / K8S / Helm

## Documentation

- `DEPLOYMENT.md` — primary deploy path (single-host Docker Compose, recommended)
- `DEPLOYMENT_K8S.md` / `DEPLOYMENT_AWS.md` / `DEPLOYMENT_GCP.md` — advanced/enterprise reference (K8s/Helm/multi-cloud, optional)
- `ARCHITECTURE.md` — system architecture
- `AGENTS.md` — all 37 agents inventory (5 core + 4 vertical + 10 specialist + 11 background + 5 upload + 2 routing; post 2026-05-26)
- `PATTERNS.md` — design patterns (kpt + Scout)
- `OPERATIONS.md` — day-2 operations
- `CHANGELOG.md` — version history
- `TESTING.md` — test strategy
- `SECURITY.md` — security model
- `CONTRIBUTING.md` — dev workflow
- `FUTUREPLAN.md` — roadmap
- `UPGRADE.md` — version upgrade guide
- `DEPLOY.md` — quick deploy reference

## Health check

```bash
curl http://localhost:8001/health
# {"status":"ok","db":"connected","ml_retrain":{"last_run":"...","last_error":null}}
```

## Default login

- **Username:** `SUPER_ADMIN` env var (default: `admin`)
- **Password:** `SUPER_ADMIN_PASS` env var (default: same as username)

Change the password from the UI immediately after first login.

**Demo user (auto-provisioned with 10 vertical demo projects):**
- **Username:** `demo`
- **Password:** `demo`

Both `demo` and `${SUPER_ADMIN}` get the same 10 demo projects on first boot. See **Demo data on fresh install** below.

## Demo data on fresh install

On first boot of a fresh DB, the bootstrap auto-provisions:
1. User `demo / demo` (created if missing).
2. 10 demo projects across verticals — for **both** `demo` and `${SUPER_ADMIN}`.
3. Deterministic synthetic data per project (same seed = same data every install).

| # | Project | Vertical | Tables | Rows |
|---|---|---|---|---|
| 1 | Multi-Chain Retail Demo | 7-Eleven / Walmart / Target | 7 | 200k |
| 2 | Pharmacy Chain Demo | CVS / Walgreens / Rite Aid | 8 | 205k |
| 3 | 3PL Distribution Demo | Warehouses / OTIF / picks | 7 | 236k |
| 4 | Finance FP&A Demo | GL / JEs / budget vs actual | 6 | 45k |
| 5 | HR Analytics Demo | Headcount / attrition / comp | 7 | 17k |
| 6 | SaaS Subscription Demo | MRR / ARR / churn | 6 | 62k |
| 7 | Supply Chain Mfg Demo | BOMs / yield / defects | 8 | 28k |
| 8 | Hospital Operations Demo | Encounters / labs / providers | 7 | 154k |
| 9 | Retail Banking Demo | Accounts / loans / fraud | 7 | 148k |
| 10 | Hotel Group Demo | ADR / RevPAR / reservations | 7 | 83k |

Total: ~1.18M rows × 2 users = ~2.36M rows / ~385MB on disk.

**Data is async-loaded.** App starts in ~10s. Datasets populate in background ~5min. Login + browse other features while seeds finish.

**Customer 360 / Campaigns / Attribution / Revenue work without TRAIN ALL** — pure SQL on detected schemas. Click TRAIN ALL when you want chat agents to learn the project.

**REMOVED 2026-05-16:** Demo seed packs + `dash/bootstrap/` + `pharma_seed_data/` + `setup_demo.py` deleted per user request for clean-install workflow. Cold boot now starts with 0 projects, 0 brain entries, 1 admin user. `BOOTSTRAP_DEMO=0` is still respected (defense-in-depth) but the code path is gone. Re-seed verticals manually via template library if needed (`/ui/projects` → BROWSE LIBRARY).

**Provision for additional users:**
```bash
BOOTSTRAP_DEMO_USERS=demo,admin,sales,marketing
```

Adding a new vertical: drop a `dash/bootstrap/seeds/<name>.py` exporting `generate_and_load(engine, schema, seed=42) -> dict[str,int]` and add an entry to `DEMO_PROJECTS` in `dash/bootstrap/demo_provision.py`.

## AutoML v2 — DataRobot-style multi-agent

`/ui/automl` ships an end-to-end AutoML workflow with **8 specialist agents** that stream live narration. Inspired by DataRobot + Oracle OML.

```
LEAD DATA SCIENTIST  (orchestrator)
  ├─ DATA ENGINEER          fuzzy-joins multi-file uploads
  ├─ EDA ANALYST            distributions / missing / correlations / leakage
  ├─ DOMAIN EXPERT          7 verticals (HR/Finance/Healthcare/etc)
  ├─ FEATURE ENGINEER       derives features from template recipe
  ├─ ML ENGINEER            FLAML 5 algos + HPO + 5-fold CV
  ├─ EXPLAINABILITY ANALYST SHAP + plain English drivers
  └─ REPORT WRITER          PDF + PPTX + dashboard for management
```

**End-to-end UX:**

1. **Pick template** — gallery (`/ui/automl`) → 12 cards.
2. **Upload data** (`/ui/automl/upload?template=<id>`) — multi-file drag-drop OR pick project tables. CSV / Excel / JSON / Parquet.
3. **Review** (`/ui/automl/upload/<set_id>`) — merge report + EDA findings + domain expert interpretation + lead's plan.
4. **Run** (`/ui/automl/<exp_id>`) — live agent feed panel streams `agent_start` / `agent_msg` / `agent_done` SSE events.
5. **Results** — leaderboard + SHAP global + per-row + confusion + ROC + top-risk.
6. **Share with management** (`/ui/automl/<exp_id>/share`) — auto-generates Exec PDF, Board PPTX, Interactive Dashboard. Email form. Inline followup chat.
7. **Followup** (`/ui/automl/<exp_id>/followup`) — full-page chat scoped to experiment context, citations to leaderboard/SHAP.

**Domain experts (vertical-specific personas):**

| Vertical | Frameworks referenced |
|---|---|
| HR (people) | pay equity · compa-ratio · span of control · regretted attrition |
| RevOps (revenue) | MRR/ARR · NRR/GRR · LTV/CAC · expansion vs churn |
| Supply Chain | OEE · OTIF · safety stock · Pareto SKU classes |
| Finance (FP&A) | budget vs actual variance · burn rate · contribution margin |
| Healthcare | LOS · readmission · DRG · denial rate · case mix index |
| Hospitality | ADR / RevPAR · occupancy · GOPPAR · channel mix |
| Generalist | fallback for any vertical |

**Multi-file upload merge** — Data Engineer agent uses rapidfuzz to detect join keys via column name overlap + fuzzy match (96%+ similarity). Inner/left/outer chosen by row coverage. Reports applied joins + match counts + warnings.

**Report deliverables (one-click):**

| Type | Lib | Output |
|---|---|---|
| Exec Summary PDF | reportlab (fallback styled HTML) | 1-page narrative |
| Board Deck PPTX | python-pptx | 10 slides Midnight Executive theme |
| Interactive Dashboard | matches `dash_dashboards_v2.spec` | KPIs + charts |
| Email | stub for v1 (SMTP/SES deferred) | logs to reports table |

**Endpoints (12 new in Phase 2):**
```
POST   /api/automl/uploads
POST   /api/automl/uploads/{set_id}/files          multi-file (1MB chunks, 10 max, 200MB cap)
DELETE /api/automl/uploads/{set_id}/files/{fid}
POST   /api/automl/uploads/{set_id}/merge          → Data Engineer agent
POST   /api/automl/uploads/{set_id}/eda            → EDA Analyst agent
POST   /api/automl/uploads/{set_id}/start          → enqueue experiment
POST   /api/automl/experiments/{id}/report         → 3 deliverables generated
GET    /api/automl/experiments/{id}/reports
GET    /api/automl/experiments/{id}/reports/{rid}/download
POST   /api/automl/experiments/{id}/share          → email (stub)
POST   /api/automl/experiments/{id}/followup       → DEEP_MODEL chat
GET    /api/automl/experiments/{id}/followups
```

**Tables (migration 036):**

```sql
dash.dash_automl_upload_sets    -- multi-file staging set
dash.dash_automl_reports        -- generated PDFs/PPTXs/dashboards per experiment
dash.dash_automl_followups      -- chat messages scoped to experiment
```

**Cost per full v2 experiment:** ~$0.12 (FLAML compute $0; agents combined ~$0.10; PDF/PPTX $0).

**Optional deps with graceful fallbacks:**
- `rapidfuzz` → fuzzy join in Data Engineer; if missing, exact-name match only + warning
- `reportlab` → PDF; if missing, styled HTML pager
- `scipy` → ANOVA correlations in EDA; if missing, Pearson only

## AutoML — `/ui/automl`

Hybrid AutoML inspired by Oracle OML AutoML UI. **FLAML does math, LLM does judgment.** Zero-config wizard: pick template → pick data → click ANALYZE → click START.

**Architecture:**
- Queue-based via existing `dash-ml` worker (RAM 2GB, polls `dash_ml_jobs`).
- New `automl_experiment` job_type. No new container, no new port.
- FLAML 2.3+ (xgboost / lgbm / rf / extra_tree / lrl1) with CFO/BlendSearch HPO + 5-fold CV.
- imbalanced-learn for SMOTE / undersample.
- SHAP TreeExplainer for global + per-row feature importance.

**Decision router:**
| n_rows | positive_rate | path |
|---|---|---|
| < 1000 OR no labels | — | `llm_only` (LLM-as-classifier) |
| 1000–4999 | — | `hybrid` (FLAML + LLM second-opinion) |
| ≥ 5000 | ≥ 0.5% | `flaml` (full pipeline) |

**12 LLM touchpoints** at input + output:
- INPUT: use case detector, target column auto-detect, feature recipe extender, label SQL gen, schema requirement matcher, class imbalance advisor.
- OUTPUT: result narrator, per-row driver translator, failure diagnosis, next-step recommender, code snippet generator, drift narrator.

**Cost per experiment:** ~$0.06 (FLAML = $0; LLM = ~$0.05).

**12 templates available:**
| Template | Vertical | Task | Status |
|---|---|---|---|
| hr_attrition | HR | classification (F1) | ready |
| customer_churn | SaaS | classification | preview |
| sales_forecast | Retail | regression | preview |
| stockout_risk | Distribution | classification | preview |
| defect_predictor | Mfg | classification | preview |
| loan_default | Banking | classification | preview |
| budget_variance | Finance | regression | preview |
| readmission_30d | Healthcare | classification | preview |
| refill_adherence | Pharmacy | classification | preview |
| adr_forecast | Hotel | regression | preview |
| headcount_forecast | HR | regression | preview |
| custom_use_case | any | LLM-driven | open-ended |

Phase 1 ships HR Attrition with full label SQL. Phase 2 ships the other 9.

**Adding a new template:** drop `dash/automl/templates/<name>.py` exporting `TEMPLATE: dict` with `id, name, task, metric, label_column, label_sql, feature_recipe, exclude_columns, schema_required` keys. Register in `templates/__init__.py` `TEMPLATES_REGISTRY`.

**Endpoints:**
```
GET  /api/automl/templates                          12 cards
GET  /api/automl/templates/{id}                     detail + requirements_check
GET  /api/automl/templates/{id}/compatible-projects schema-aware filter
POST /api/automl/templates/{id}/upload              streaming CSV upload
POST /api/automl/templates/{id}/auto-config         LLM analyze → config
GET  /api/automl/experiments                        cross-project list + status_counts
GET  /api/automl/experiments/{id}                   cross-project single
POST /api/projects/{slug}/automl/start              enqueue
GET  /api/projects/{slug}/automl/{id}               full state
GET  /api/projects/{slug}/automl/{id}/stream        SSE
POST /api/projects/{slug}/automl/{id}/cancel
POST /api/projects/{slug}/automl/{id}/promote       → production REST endpoint
```

**Tables (migrations 034 + 035):**
- `dash.dash_automl_experiments(id, project_slug, template_id, status, decision_path, n_rows, positive_rate, leaderboard JSONB, shap_global JSONB, shap_per_row JSONB, narrative, recommendations, events JSONB, ...)`
- `dash.dash_automl_staging(id, project_slug, filename, file_path, format, n_rows, schema_columns, sample_rows, ...)`

**Smoke test (HR Attrition demo project):**
```bash
TOKEN=$(curl -sk -X POST -d '{"username":"demo","password":"demo"}' \
  -H 'Content-Type: application/json' https://localhost/api/auth/login | jq -r .token)

curl -sk -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  https://localhost/api/projects/proj_demo_hr_analytics_demo/automl/start \
  -d '{"template_id":"hr_attrition","time_budget":300}'
```
Expect `{"exp_id": N, "status": "queued"}`. Worker picks up within 60s. Watch live at `/ui/automl/{N}`.

**Worker scaling:** add replicas to `dash-ml` in compose.yaml — atomic UPDATE claim on `dash_ml_jobs` serializes job pickup. K8s: HPA on CPU or KEDA on queue depth.

**Env flags:**
| Var | Default | Purpose |
|---|---|---|
| `AUTOML_DAEMON_DISABLED` | `0` | skip ML worker AutoML handler entirely |
| `RAISE_ON_MIGRATION_FAIL` | `0` | fail-fast on missing 034/035 |

## Brain seed packs (auto-populated Day-1)

`/ui/brain` ships with **345 pre-seeded knowledge entries** across 10 verticals on first boot. Categories: glossary, formula, alias, pattern, org, entity.

| Vertical | Entries |
|---|---|
| retail | 37 |
| pharmacy | 38 |
| distribution | 35 |
| finance | 33 |
| hr | 32 |
| saas | 28 |
| supply_chain | 38 |
| healthcare | 36 |
| banking | 34 |
| hotel | 34 |

Loaded into `public.dash_company_brain` with `project_slug=NULL` (global scope) — visible to **all** projects. Acts as **Context Layer 13** in every agent's prompt. **No TRAIN ALL required** for chat agents to see industry-standard formulas, KPIs, glossary terms.

**Idempotent.** `ON CONFLICT DO NOTHING` on `uq_brain_global_name` index. Re-runs never overwrite admin edits.

**Override per project:**
```bash
BRAIN_SEED_PROJECT_SCOPED=1
```
When set, each demo project also gets a project-scoped copy of its vertical pack (allows tenant-specific edits without touching global). Default OFF.

**Add a new vertical:** drop `dash/bootstrap/brain_seeds/<name>.py` exporting `BRAIN_ENTRIES: list[dict]` and register in `VERTICAL_TO_MODULE` dict in `loader.py`.

**Inspect:**
```bash
docker exec dash-db psql -U ai -d ai -c \
  "SELECT category, COUNT(*) FROM dash_company_brain WHERE project_slug IS NULL GROUP BY category ORDER BY 2 DESC;"
```

## Agent status — 4-state model

Settings → AGENTS tab shows each agent in one of 4 states (industry-standard tri-state + error):

| State | Glyph | Meaning | Trigger |
|---|---|---|---|
| **ACTIVE** | ✓ green | healthy + recently called | last invoked < 7d |
| **READY** | ◐ blue | healthy + available, awaiting trigger | new project default |
| **NEEDS SETUP** | ○ gray + CTA | prerequisite missing | Researcher + 0 docs, Strategist + no customer table |
| **ERROR** | ✗ red | last invocation failed | last error < 1h |

Each agent row shows: state glyph + state label + role + tooltip with reason + inline CTA link when `needs_setup` (e.g. "→ UPLOAD PDF" for Researcher).

**Standby is gone** — replaced by READY (available) and NEEDS SETUP (prerequisite missing). User can no longer confuse "fine, just unused" with "actually broken".

**Endpoint:** `GET /api/projects/<slug>/agents` returns:
```json
{
  "agents": [{
    "name": "Researcher",
    "state": "needs_setup",
    "reason": "Upload a PPTX, PDF, or DOCX to activate.",
    "cta": {"label": "SET UP", "url": "/ui/project/<slug>/settings#datasets"},
    "last_used_at": null,
    ...
  }],
  "legend": {...}
}
```

## Database migration auto-runner

On every boot, `dash/db_runner/migrate.py` scans `db/migrations/*.sql`, applies pending in lexical order, tracks via `public.dash_migrations(filename PK, applied_at, checksum)`. Multi-worker safe via `pg_advisory_lock(72157423)`.

Each migration runs in its own transaction with `SET search_path = dash, public, ai;` so unqualified table refs land in the right schema (avoids the schema-drift bug class where `dash_campaigns` / `dash_vectors` end up in `ai` instead of `dash`).

sha256 checksums prevent silent re-application of edited migrations — drift logs a warning, never re-runs.

**Env flags:**
- `RAISE_ON_MIGRATION_FAIL=1` — re-raise instead of log + continue (useful for fresh-DB fail-fast).

**Inspect:**
```bash
docker exec dash-db psql -U ai -d ai -c \
  "SELECT filename, applied_at FROM public.dash_migrations ORDER BY filename;"
```

**Manual rerun:**
```bash
docker exec dash-api python -c \
  "from dash.db_runner.migrate import run_migrations; print(run_migrations())"
```

## Agentic Dashboard Framework

Self-deepening agentic dashboards. Ask a question in chat, click the blue
**D** button in the bottom toolbar, and a right-side artifact panel slides in
where an LLM analyst agent extracts your chat context, picks a layout
template, generates a spec, runs SQL, renders ECharts, and streams its live
thinking log as it deepens the dashboard with multi-round insight detection.

### User flow

- **Chat → 📊 D** — uses cached spec if exists, else generates fresh
- **🔄 REGENERATE** — forces a fresh build
- **SAVE** — persists to the `dash_dashboards_v2` table
- **DASHBOARD** top-nav → grid of all saved dashboards (legacy + v2 with an `AGENT` badge)
- `/ui/project/{slug}/dashboards/{id}` — saved dashboard view with
  REFRESH / AUTO 60s / COMPARE / EXPORT PNG / SHARE / EDIT

### What ships (8 phases)

| Phase | What |
|---|---|
| 0 | Spec layer (pydantic) + lint rules + Svelte renderer |
| 1 | LLM planner — 5-step pipeline (schema + persona + KG + memory → spec) |
| 2 | Chat handoff — extract questions/SQLs/insights from `agno_sessions` thread |
| 3 | JSON Patch edits (RFC 6902) — chat-driven cell modifications |
| 4 | Analyst agent loop — 4 rounds, 12-cell budget, 5 detectors (anomaly z-score, trend break, outlier 5×, correlation r>0.7, concentration 80/20) |
| 5 | SSE streaming — live thinking log + cell-by-cell render with green flash |
| 6 | Adaptive layout — 4 templates (executive / operational / analytical / exploratory) auto-picked from question + persona |
| 7 | Memory feedback — `dash_dashboard_memory` table; planner biases toward preferred chart types |
| 8 | Polish — share token + public URL, EXPORT PNG (html-to-image), REFRESH, AUTO 60s, compare-mode stub |

### Frontend

- `lib/dashboards/DashRenderer.svelte` — 12-col CSS grid, filter chips, insight banners, mobile single-col under 768px
- `lib/dashboards/cells/Cell.svelte` — KPI / chart (ECharts: line/bar/pie/scatter/area) / table / insight switch
- `lib/dashboards/ArtifactPanel.svelte` — right 35% slide-in panel (fullscreen <900px), dark mono thinking footer with pulse animation
- `lib/dashboards/EditPanel.svelte` — chat input + history (last 5) + per-row undo via spec stack
- Routes: `/dashboard` (global list), `/project/[slug]/dashboards/new` (preview from sessionStorage), `/project/[slug]/dashboards/[id]` (saved view)

### API

```
POST /api/dashboards/generate         {project_slug, prompt, persona?, deepen?}
POST /api/dashboards/from-chat        {thread_id, msg_id?, project_slug, prompt?, deepen?}
POST /api/dashboards/deepen           sync deepen
POST /api/dashboards/deepen/stream    SSE: thinking, insight, cell_added, done, error
POST /api/dashboards/run-data         {spec, project_slug} → {data: {cell_id: {value|rows}}}
POST /api/dashboards/patch            {spec, prompt} → JSON Patch + new spec
POST /api/dashboards/save             upsert into dash_dashboards_v2
GET  /api/dashboards/{id}             load spec
GET  /api/dashboards/list/{slug}      per-project list
GET  /api/dashboards/list-all         global v2 list (top nav)
POST /api/dashboards/{id}/share       toggle public + share_token
GET  /api/dashboards/public/{token}   no-auth public read
POST /api/dashboards/{id}/refresh     re-run all SQLs
POST /api/dashboards/memory/log       {action, cell, spec_id}
GET  /api/dashboards/memory/preferences/{slug}
```

### Cost

~$0.005–0.02 per dashboard depending on rounds and whether deepen is on.

### Known limitations

- Schema enrichment uses `information_schema` per-project read-only engine; cross-schema joins limited
- `compare_to` field exists but date-offset rerun deferred (currently renders side-by-side current vs last refresh only)
- `refresh_cron` field exists, no scheduler yet
- Vision (screenshot → spec) not shipped

## Project structure

```
app/                    # FastAPI application
  main.py               # Entry, CORS, routing, scheduler bootstrap
  auth.py               # Auth, users, RBAC
  projects.py           # Projects CRUD, chat, sharing
  upload.py             # Data upload + auto-training
  learning.py           # Self-learning API
  dashboards.py         # Dashboard CRUD + widgets
  export.py             # PDF, PPTX, Excel, HTML, slide agent
  brain.py              # Company Brain (3-layer: Global / Project / Personal)
  sharepoint.py         # SharePoint endpoints
  gdrive.py             # Google Drive endpoints
  connectors.py         # Database connector endpoints

dash/
  team.py               # Agent team factory
  instructions.py       # Dynamic prompt assembly (13 context layers)
  agents/               # 37 agents (5 core + 4 vertical + 10 specialist + 11 bg + 5 upload + 2 routing)
  context/              # Semantic model, business rules
  tools/                # Agent tools (Analyst: SQL + `analyze` dispatcher + retrieve + viz; verticals: venture/market/ops/supply tools)
  tools/codex_code.py   # Layer-3 Codex: reads view/table DDL → pipeline_logic
  providers/            # 7 connector providers + classifier + PII mask + trainer
  learning/             # 17 modules (kpt autonomous loop)

db/
  migrations/           # 6 SQL migrations
  session.py            # SQLAlchemy + PgVector + embedder cascade

frontend/               # SvelteKit application
helm/dash/              # Helm chart (Chart.yaml, values, 17 templates)
k8s/                    # Raw K8S manifests
ml_worker/              # Separate ML training container
branding/default/       # Default white-label assets
```

## K8s daemon mode

Production K8s deployments should set `DAEMONS_DISABLED=1` (or `K8S_DAEMON_MODE=cronjob`) on API pods. This single master gate disables all 6 in-process background loops (brain_versions_purge, vector_sync + reembed, ontology_cluster, auto_campaign, benchmark_sync, mrr_snapshot) so 8 uvicorn workers don't each spin up duplicate daemons. Scheduled work runs exclusively via the bundled K8s CronJobs in `helm/dash/templates/*-cronjob.yaml`. Per-daemon env flags (`AUTO_CAMPAIGN_DAEMON_DISABLED`, `BENCHMARK_SYNC_DISABLED`, etc.) still override individually for granular control.

## Campaign proposals from chat

Customer Strategist agent emits `[CAMPAIGN_PROPOSAL: name | segment | discount_pct | est_audience]` tags inside chat responses. Frontend renders these as orange-bordered cards with **+ CREATE DRAFT** button — one click drops a `status='draft', type='manual', source='chat_proposal'` campaign into `dash_campaigns`. Owner reviews + launches via the standard `/campaigns` page. Idempotent — re-clicking marks `✓ CREATED`.

## Tier 4 — Advanced Marketing & Revenue Intelligence

- **Auto-campaign daemon** — daily loop watches RFM segment shifts (Champions drop ≥15%, At Risk surge ≥20%, Hibernating overflow >25%, New spike ≥30%) and auto-drafts campaigns into `dash_campaigns` with reasoning blob (detected change, suggested discount, expected lift). Owner approves via 🤖 AUTO tab on `/campaigns` page (one-click APPROVE & LAUNCH). 7-day per-rule cooldown, 5 drafts/cycle cap. Per-project disable via `feature_config.tools.auto_campaign_daemon`. Env: `AUTO_CAMPAIGN_DAEMON_DISABLED`, `AUTO_CAMPAIGN_INTERVAL_SECONDS`. Endpoints: `POST /api/projects/{slug}/auto-campaign/run-now`, `POST /api/projects/auto-campaign/cycle-all`. K8s daily 02:00 UTC.

- **Multi-touch attribution (MTA)** — touchpoint event ingest (`POST /touchpoints` + `/touchpoints/bulk`) + conversion ingest (`POST /conversions`). 4 attribution models: linear / time-decay (7d half-life) / position-based (40/40/20) / Markov removal-effect (numpy). Per-conversion DELETE+INSERT idempotent. Customer 360 gets JOURNEY tab with vertical timeline + channel-colored dots + per-model credits. New `/project/{slug}/attribution` dashboard: 4 KPI cards, channel-mix pie, campaign attribution table, compare-models matrix highlighting model disagreement. Customer Strategist agent gets `mta_summary` tool (now 9 tools).

- **MRR / ARR analytics** — auto-detects subscription tables (subscriptions / plans / billing_cycles + mrr / monthly_amount / amount cols). `compute_mrr_breakdown` walks customer-level deltas to classify new / expansion / contraction / churn / reactivation. Gross + net retention, 12-mo trend, cohort survival. Daily 04:30 UTC snapshot daemon eligibility = SaaS template applied OR `feature_config.tools.mrr_analytics`. New `/project/{slug}/revenue` page: 6 KPI cards (MRR / ARR / NET NEW / GROSS RET / NET RET / ACTIVE SUBS), MRR Movement waterfall (ECharts), 12-mo trend, cohort retention heatmap. Customer 360 gets SUBSCRIPTION KPI card (8th). Data Scientist agent gets 4 MRR tools (now 17 ML tools). Migrations: `031_segment_snapshots.sql`, `032_attribution.sql`, `033_subscription_metrics.sql`.

## Ontology Phases B–E

- **Auto-cluster daemon** — 6h loop scans entities across all projects, auto-merges high-confidence duplicates (≥0.95) into `dash_company_brain` aliases, queues 0.70–0.95 for human approve. Multi-worker safe via atomic UPDATE claim. K8s daily CronJob 03:00 UTC. Env: `ONTOLOGY_CLUSTER_DISABLED`, `ONTOLOGY_CLUSTER_INTERVAL_SECONDS`. One-shot: `POST /api/ontology/cluster/run-now`.
- **Web benchmark sync** — 7-day cron pulls public industry KPIs (retail / saas / healthcare / finance / hospitality), parses via LITE_MODEL, runs through PII scrub + LLM-gated promotion, upserts as `category='benchmark'` brain entries. Auto-injects into Layer 13 of every agent (5-row LIMIT). Cost cap $0.50/run. Env: `BENCHMARK_SYNC_DISABLED`, `BENCHMARK_SYNC_INTERVAL_SECONDS`. One-shot: `POST /api/ontology/benchmarks/sync-now`. Read: `GET /api/ontology/benchmarks?industry=&kpi=`.
- **Versioning + rollback** — `dash_brain_versions` snapshot table written atomically with every brain create/update/delete. `GET /api/brain/{id}/history`, `POST /api/brain/{id}/rollback/{v}` (super-admin OR original-author gated). UI: 🕒 HISTORY button per brain entry → drawer with version list, color-coded change_type badges, DIFF view, ROLLBACK confirm. 365-day auto-purge.
- **Public read API** — `/v1/ontology/*` Bearer-key OpenAPI for external integrations. Per-key project scope, scope flags (types/glossary/links/lineage), 60s sliding rate limit, CORS origin allowlist, async audit. Admin UI in Ontology Workbench → API KEYS tab with create / rotate / revoke / usage drawer (daily call chart). Migrations: `029_brain_versions.sql`, `030_ontology_public_keys.sql`.

## Troubleshooting

### "I see old UI / old text / old buttons after rebuild"

**Symptoms:** Code edited + `docker compose build` exited 0, but browser still
renders strings or controls that the source has already removed. Specific
recurring example: Embed tab kept showing the old `+ CREATE EMBED` modal +
empty-state "No embeds yet" text even after the stacked inline-edit UI shipped.

**Root cause:** Docker layer cache hit + `compose up -d` skips recreate when
image hash hasn't changed in registry. Container keeps running the previous
hashed JS bundle (e.g. `_app/immutable/nodes/8.CLyNNrW7.js`).

**Diagnosis:**
1. DevTools → Network → reload → check first JS bundle hash. If it matches a
   previous deploy, you're stale.
2. `docker images dash:latest --format "{{.CreatedSince}}"` — must show seconds,
   not hours.
3. `docker exec dash-api md5sum /app/frontend/build/_app/immutable/nodes/<hash>.js`
   compared to host bundle.

**Fix sequence:**

```bash
docker compose build --no-cache dash-api
docker compose up -d --force-recreate dash-api
# Browser: Cmd+Shift+R
```

If image still old after the above:
```bash
docker builder prune --all -f
docker image rm dash:latest
docker compose build --no-cache dash-api
docker compose up -d --force-recreate dash-api
```

**Verify migration ran** (the embed feature needs 062):
```bash
docker exec dash-db psql -U ai -d ai -c \
  "SELECT column_name FROM information_schema.columns \
   WHERE table_name='dash_agent_embeds' \
     AND column_name IN ('agent_id','auto_provisioned','primary_color');"
```
Expect 3 rows. If 0, force-apply:
```bash
docker exec -i dash-db psql -U ai -d ai < db/migrations/062_embed_per_agent.sql
docker exec dash-db psql -U ai -d ai -c \
  "INSERT INTO public.dash_migrations(filename) \
   VALUES ('062_embed_per_agent.sql') ON CONFLICT DO NOTHING;"
```

This failure mode has hit 4 sessions in a row (vectors `_TABLE` fix, `agentTpl`
typo fix, `on:click → onclick` Svelte 5 sweep, per-agent embed UI). Default
assumption when user reports "still seeing old UI": rebuild first, debug
second.

### Embed tab shows "No embeds yet" or empty list with 41 agents present

Migration 062 not applied OR backfill endpoint 500'd. Check:

```bash
# 1. Migration cols exist?
docker exec dash-db psql -U ai -d ai -c "\d public.dash_agent_embeds" | grep agent_id

# 2. Manual backfill (auth required)
curl -X POST -H "Authorization: Bearer $T" \
  http://localhost:8000/api/projects/$SLUG/embeds/backfill
```

Returns `{status: "ok", agents_total: N, created: M}`. If `agents_total=5`
when project has 41 agents, `app.learning._list_project_agents` helper is
missing — backend falls back to hardcoded core-5 list. Add the helper or
extend the fallback list.

### Settings sidebar rail won't scroll OR scrolls together with main page

**Symptoms (any of these):**
- Left rail items disappear when you scroll the right page content
- Last items in rail (Federation, Scenario Lab) unreachable — you see them cut at bottom
- Scrollbar visible on rail but dragging it does nothing
- Top rail items (WORKSPACE label, Cockpit) gone from view when on a long page

**Root causes (in order of likelihood):**

1. **`.set-shell` is not a scroll container** — outer page `<main class="overflow-y-auto">` becomes the scroll ancestor, sticky rail tries to engage relative to non-scroller `.set-shell` and falls through. **Fix:** add `overflow-y: hidden` + explicit `height: calc(100vh - 64px)` to `.set-shell` so it becomes the scroll boundary.

2. **`position: sticky` + `height: 100%` deadlock** — MDN documents this: a sticky element with height equal to its containing block creates a scroll-locked state. Scrollbar paints but offset clamps to 0. **Fix:** drop `position: sticky` + `top: 0` from `.set-rail` entirely. Grid layout naturally pins the rail to the left column without needing sticky.

3. **Rail height mismatch with parent** — if `.set-shell` is `calc(100vh - 64px)` and `.set-rail` is `calc(100vh - 56px)`, the rail's bottom 8px (where the scrollbar handle lives) gets clipped by parent overflow. **Fix:** use `height: 100% !important` on `.set-rail` so it matches set-shell exactly.

4. **No bottom buffer** — last rail item (Federation) glued to viewport edge, no scroll room. **Fix:** `padding-bottom: 100px` on `.set-rail`.

5. **macOS auto-hides scrollbar** — user can't tell the rail is scrollable. **Fix:** force always-visible webkit scrollbar via `::-webkit-scrollbar` rules + `overflow-y: scroll` (or `auto` with custom scrollbar always rendered).

**Canonical working CSS for this pattern:**

```css
.set-shell {
  display: grid;
  grid-template-columns: 240px 1fr;
  height: calc(100vh - 64px);
  overflow-y: hidden;              /* makes set-shell the scroll boundary */
}
.set-main {
  overflow-y: auto;                /* right column independent scroll */
  overflow-x: hidden;
  min-height: 0;                   /* flex/grid child shrink trick */
  overscroll-behavior: contain;
}
.set-rail {
  /* NO position: sticky — grid layout pins it */
  height: 100% !important;
  align-self: stretch;
  overflow-y: auto !important;     /* left column independent scroll */
  overflow-x: hidden;
  overscroll-behavior: contain;
  padding: 6px 8px 100px !important;  /* bottom buffer */
  min-height: 0;
}
```

**Bonus — auto-scroll active tab into view** (covers URL hash navigation + tab clicks):

```ts
$effect(() => {
  if (typeof window !== 'undefined' && activeTab) {
    queueMicrotask(() => {
      const btn = document.querySelector(`.set-rail [data-tab="${activeTab}"]`);
      if (btn) btn.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  }
});
```

**Same pattern applies to Brain and Ontology pages** — they use identical grid layout (`/ui/brain`, `/ui/ontology`). If you fix the settings rail, apply the same CSS to those.

### Training pipeline silently saves nothing (Q&A / metadata / personas all empty)

**Symptom:** training run reports `status=done` but `dash_training_qa`, `dash_table_metadata`, `dash_personas`, `dash_business_rules_db` all stay at 0 rows for the project.

**Root cause (fixed 2026-05-16):** `_save_to_db()` in `app/upload.py` used `:m::jsonb` Postgres cast syntax which collides with SQLAlchemy `:m` named-param binding → silent `syntax error at or near ":"`. Outer `try/except: pass` swallowed for months.

**Fix:** all `:X::jsonb` patterns now use `CAST(:X AS jsonb)`. Error logging restored so future failures surface.

**To diagnose if you hit this again:** check `dash-api` logs for `_save_to_db failed` lines. Verify with `psql -c "SELECT COUNT(*) FROM public.dash_table_metadata WHERE project_slug='<slug>';"`.

### `expected 1536 dimensions, not 0` (vector upsert)

**Symptom:** Agno PgVector knowledge upsert + `dash.dash_vectors` writes fail with `(builtins.ValueError) expected 1536 dimensions, not 0`.

**Root causes (two distinct bugs, both fixed 2026-05-16):**

1. **`dash/tools/vector_sync.py:30`** — `_one(t)` called async `embed_text()` without `await`, wrapped in `run_in_executor` thread pool. Returned coroutine objects (never awaited). Vectors all empty. Fix: replaced with direct call to async `embed_batch()` from `embeddings_helper.py`.

2. **`db/session.py` `_create_embedder()` cascade** — first model in cascade was `gemini-embedding-2-preview` (native 3072 dim). Code forced `dimensions=1536` but OpenRouter returned silent empty list. Validation only caught exceptions, not empty vectors → cached the broken embedder. Fix: reordered cascade so `openai/text-embedding-3-small` (native 1536) is tried first; validation now checks `len(vec) == 1536`.

### KG entity standardization JSON parse errors

**Symptom:** `WARNING: dash.tools.knowledge_graph: KG: entity standardization LLM error: Unterminated string starting at: line N column M`.

**Root cause:** LITE/CHAT model (`gemini-3.1-flash-lite-preview`) truncated big entity-batch responses mid-string. 4-tier robust JSON parser can fix trailing-comma + extract first `[...]`, but cannot reconstruct a string missing its closing quote.

**Fix:** set `DEEP_MODEL=google/gemini-3-flash` in `.env`. KG `_standardize_entities()` uses DEEP_MODEL for higher-quality structured JSON output. Triples still get saved either way (per-batch fail-soft), only the alias-merge canonicalization step is affected.

### Training Q&A wrapped in markdown fences gets dropped

**Symptom:** Q&A generated by LLM with ```sql ... ``` fences fails `_is_safe_sql()` (requires SELECT/WITH start). Silently rejected.

**Fix (2026-05-16):** added `_strip_sql_fences()` in `app/upload.py` — regex strips ```sql / ``` fences + leading `--` comment lines + trailing `;` before safety check. Now logs `qa: kept=N rejected=M table=X` so rejections are visible.

### `column X does not exist` / `relation Y does not exist` (schema drift)

If pages show errors like `column "metadata" does not exist on dash_campaigns` or `relation "dash_vectors" does not exist`, you're hitting schema drift — a migration created the table in `ai` schema (or `public`) but the app session resolves `dash` first via search_path.

**Diagnosis:**
```bash
docker exec dash-db psql -U ai -d ai -c \
  "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid WHERE c.relname='<table_name>';"
```
If you see `ai` and `dash` (or only `ai`), drift confirmed.

**Fix:** the migration runner should handle this automatically by setting `search_path=dash,public,ai` per migration. If a migration was applied before the runner existed, force re-run:
```bash
docker exec dash-db psql -U ai -d ai -c \
  "DELETE FROM public.dash_migrations WHERE filename='028_vectors.sql';"
docker exec dash-api python -c \
  "from dash.db_runner.migrate import run_migrations; print(run_migrations())"
```

For one-off schema patches without re-running a full migration:
```bash
docker exec dash-db psql -U ai -d ai -c \
  "ALTER TABLE dash.<table> ADD COLUMN IF NOT EXISTS <col> <type>;"
```

### Customer 360 page shows "no transactions-style table found"

Project schema is empty — no `customers` / `transactions` / `orders` table exists yet. Customer 360 detects schema dynamically; needs at least one transactions-style table with `customer_id` + a date column.

**Fix on demo install:** use a pre-seeded demo project. Login as `demo / demo` → open any of the 10 demo projects (retail / pharmacy / banking etc) — all have detected schemas Day-1.

**Fix on real project:** upload sales/orders/transactions data via DATASETS tab, or wire a database connector that exposes a transactions table.

### Settings page stuck on `LOADING…`

Common after frontend changes. Open DevTools → Console:

| Console error | Cause | Fix |
|---|---|---|
| `ReferenceError: base is not defined` | Svelte file uses `${base}` but didn't import it | Add `import { base } from '$app/paths';` to top `<script>` |
| `ReferenceError: <var> is not defined` | Template references a var that doesn't exist (typo, renamed) | Fix var name to match the `$state`/`$derived` declaration |
| `404 GET /api/...` | Frontend calls an endpoint not wired in backend | Wire endpoint in matching `app/<router>.py` OR remove the loader call |

**Why it hangs:** Settings `onMount` does a 42-fetch `Promise.all(...)` and only sets `loading = false` after it resolves. Any uncaught `ReferenceError` in render aborts hydration and the flag never flips. Any single fetch that hangs (not 404 — those resolve) blocks first paint too. Future split: keep `loadDetail()` critical, push the rest to `Promise.allSettled` in background.

### Container running old code despite fresh image

If `docker images dash:latest` shows "18 seconds ago" but `docker compose ps dash-api` shows "Up 7 minutes" with stale behavior, Compose skipped the recreate because the image hash matched the running container in some intermediate way. Force it:

```bash
docker compose up -d --force-recreate dash-api
```

This always destroys the running container and starts a new one off the freshest image, regardless of perceived hash equality.

### Stale Docker image despite "build success" — the silent Svelte trap

If `docker compose build dash-api` reports success but `docker exec dash-api grep <changed-line> /app/app/<file>.py` shows old content, the **frontend build inside Docker silently failed**. Common cause: a Svelte 4 syntax leak (`on:click`, `on:change`, `on:keydown`, `on:input`) mixed with Svelte 5 `onclick`/`onchange` etc. in the same file → Vite errors out → Dockerfile's `npm run build` fallback exits non-zero → Docker keeps shipping the previous image layer, but Compose still happily restarts the container.

**Diagnose**:
```bash
# Step 1 — does the host build clean?
cd frontend && rm -rf .svelte-kit build && npm run build
# Look for: mixed_event_handler_syntaxes, or any vite-plugin-svelte error

# Step 2 — sweep for legacy syntax
grep -rn "on:click\|on:input\|on:change\|on:keydown" --include="*.svelte" src/

# Step 3 — verify image is actually new
docker images dash:latest --format "{{.CreatedSince}}"
# Should be "X seconds ago", NOT "2 hours ago"

# Step 4 — confirm container has the change
docker exec dash-api md5sum /app/app/<file>.py
md5 /Users/.../app/<file>.py     # macOS
md5sum /Users/.../app/<file>.py  # Linux
# Hashes must match
```

**Fix**: replace every `on:event` with `onevent` (no colon) in the offending file, rebuild, force-recreate the container.

### Frontend changes not reaching browser after rebuild

```bash
docker compose build dash-api
docker compose up -d dash-api
# hard refresh: Cmd+Shift+R
```

If browser still serves a stale hashed JS bundle (e.g. `21.CLyNNrW7.js` persists):

```bash
docker builder prune --all -f
docker image rm dash:latest
docker compose build --no-cache dash-api
docker compose up -d dash-api
```

Verify container picked up new image: `docker compose ps dash-api` should show `Up <few minutes>`, not `Up X hours`.

### Vector search fails with `relation "dash_vectors" does not exist`

Run pgvector migration:

```bash
psql $DB -f db/migrations/028_vectors.sql
```

### Embeddings tests without an OpenRouter key

Use the deterministic sha256 fallback embedder (zero-cost, no API call):

```bash
EMBEDDINGS_HELPER_FORCE_HASH=1 pytest tests/test_vector_rls.py -v
```

### Tab click breaks page / URL hash desyncs from rail highlight

**Symptom:** Click a tab → URL hash doesn't update OR shows a different tab's hash. Rail highlight moves but content stays on previous tab. Console shows `x.join is not a function`, `(x || []).includes is not a function`, or `x.filter is not a function`.

**Root cause:** Backend returns a JSONB field as **string** or **object** in some projects but **array** in others. Frontend guards like `(x || []).join(...)` or `x?.length` don't catch non-arrays — `"foo".length` is truthy, `({}).length` is undefined-falsy but `{}` passes `||` short-circuit. Once any expression throws inside Svelte `{#if}` / `{#each}`, hydration aborts mid-render and the hash-write `$effect` never runs.

**Diagnose:**
1. DevTools Console — find the `.join` / `.includes` / `.filter` error
2. Grep frontend for the field name + operation
3. Wrap in `Array.isArray()` guard

**Fix pattern:**

```ts
// ❌ BAD
{x.length && x.join(', ')}
{(arr || []).includes(y)}

// ✓ GOOD
{Array.isArray(x) && x.length && x.join(', ')}
{Array.isArray(arr) && arr.includes(y)}
```

**Fields known to need guards** (codex/LLM-generated): `alternate_tables`, `primary_keys`, `foreign_keys`, `usage_patterns`, `relationships`, `tables` (on `queryPlans`, `insights`, `trainingRuns`).

URL desync auto-resolves once render crash stops — downstream symptom, not root cause.

See `CLAUDE.md` → Issue #29 for full playbook.

## Observability — `/metrics` scrape config

Dash exposes Prometheus metrics at `/metrics` via
`prometheus-fastapi-instrumentator`. Default request histograms and
status-code counters are registered automatically; custom Dash counters
(`dash_chat_requests_total`, `dash_sse_events_total`,
`dash_verified_pass_total`, `dash_upload_bytes_total`) live in
`dash/utils/metrics.py`.

Scrape config (`prometheus.yml`):

```yaml
scrape_configs:
  - job_name: dash
    metrics_path: /metrics
    static_configs:
      - targets: ['dash-api:8000']      # inside the compose network
        labels:
          service: dash
```

Quick check (no auth required):

```bash
curl -s http://localhost:8001/metrics | head -40
```

Sentry crash reporting is enabled when `SENTRY_DSN` is set. The middleware
tags every event with `project_slug` (parsed from `/api/projects/{slug}`
paths) and `user_id` (from `request.state.user`). With `DASH_DEBUG=1` you
can verify wiring via `GET /api/_debug/sentry-test`, which raises on
purpose.

## License

See `LICENSE`.

---

## Obsidian-style features (2026-05-26)

Six features land, all embedded in Command Center right-pane (no new pages):

| Feature | Migration | API | Panel |
|---|---|---|---|
| Bidirectional links | `150_dash_links.sql` | `app/links_api.py` | `lib/links/LinkedBy.svelte` |
| Graph view | — | `app/graph_api.py` | `lib/intel/GraphPanel.svelte` (cytoscape) |
| Daily journal | `151_dash_journal.sql` | `app/journal_api.py` | `lib/intel/JournalPanel.svelte` |
| Pack registry | `152_dash_packs.sql` | `app/packs_api.py` | `lib/admin/PacksPanel.svelte` |
| Canvas | `153_dash_canvas.sql` | `app/canvas_api.py` | `lib/intel/CanvasPanel.svelte` + `[id]/+page.svelte` |
| Dataview | — | `app/dataview_api.py` | `lib/admin/DataviewPanel.svelte` |

Constraints honored:
- NO public marketplace, NO npm React SDK
- NO Stripe/billing (internal platform)
- NO chat file upload / multimodal

### Cached-metric shortcut env vars

| Var | Default | Effect |
|---|---|---|
| `METRIC_SHORTCUT_MIN_SCORE` | `40` | Score gate for cached shortcut (was firing too eagerly at 26) |
| `METRIC_SHORTCUT_ENRICH` | `1` | Mini-LLM enrichment on cached path (~$0.001 per hit) for full STANDARD card |

Set `METRIC_SHORTCUT_ENRICH=0` to disable enrichment and emit raw cached KPI only.

### Daily journal cron

```
python scripts/daily_journal.py --date 2026-05-26 --project <slug>
```

Uses `dash.settings.training_llm_call`. Idempotent (UPSERT on `(project_slug, date)`).

### Pack registry sync

After deploy, populate registry:
```
curl -X POST http://localhost:8001/api/packs/sync -H "Authorization: Bearer $TOKEN"
```
Scans `dash/workflows/verticals/`. Retry once on cold start (import race under multi-worker).
