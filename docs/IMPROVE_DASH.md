# Improving Dash for your data

Dash gets smarter automatically. This doc explains the four tiers of improvement, how to steer them, and how to read the score.

## Tier 0 — Day 0: train each source

For every connected source (uploaded files, SharePoint folder, Google Drive, Postgres / MySQL / Fabric), click **TRAIN** in Settings → SOURCES. Pipeline runs 14 steps:

1. Drift check (compare to last training)
2. SQL profiling (every column: COUNT / DISTINCT / MIN / MAX / AVG / STDDEV / percentiles, classify dimension vs measure vs id)
3. Dimension catalog (`SELECT DISTINCT` per categorical column with unique < 500, saved to `knowledge/<slug>/dimensions/<table>.json`)
4. Hierarchy detection (parent → child between dimension columns)
5. Smart sampling (3 start + 3 middle + 3 end + outliers + null patterns)
6. **Codex enrichment** (LLM-derived purpose / grain / PKs / FKs / usage patterns / freshness — DEEP_MODEL)
7. Q&A pairs (LLM-generated, then SQL-verified against real data)
8. Persona (project tone + jargon)
9. Workflows + multi-file synthesis
10. Relationship discovery (hidden joins across tables)
11. Knowledge re-index (PgVector hybrid)
12. Brain fill (7 sub-steps populating Company Brain layers)
13. **Domain knowledge** (glossary / calculations / value mappings / KPIs / data quality / negative examples — 6 sub-steps)
14. LangExtract grounded facts + Knowledge Graph triples

Cost: ~$0.50 per source. Time: ~5 min for a 10-table source.

Doc-only sources (PPTX / PDF / DOCX with no tables) run an 18-step variant: structure extraction → section-aware chunking → hierarchical summarization → memories / persona / workflows / evals / rules / domain / Q&A / synthesis / cross-doc relationships / langextract / KG.

## Tier 1 — every chat: 11 background agents

Non-blocking after each chat response:

| Agent                       | Writes to                          | Why                                                          |
| --------------------------- | ---------------------------------- | ------------------------------------------------------------ |
| Judge                       | `dash_quality_scores`              | 1-5 quality + category + confidence                          |
| Rule Suggester              | `dash_suggested_rules`             | Extract rules from convo (you approve)                       |
| Proactive Insights          | `dash_proactive_insights`          | Anomalies > 20 % deviation                                   |
| Query Plan Extractor        | `dash_query_plans`                 | Tables / joins / filters per SQL → "proven JOIN strategies"  |
| Meta Learner                | `dash_meta_learnings`              | Which self-correction strategies work for which errors       |
| Auto Evolver                | `dash_evolved_instructions`        | Every 20 chats → supplementary instructions, versioned       |
| Chat Triple Extractor       | `dash_knowledge_triples`           | 3-10 SPO triples per Q&A → KG grows                          |
| Auto-Memory Promoter        | `dash_memories` (`source=auto`)    | Factual observations saved without approval                  |
| User Preference Tracker     | `dash_user_preferences`            | Style / metrics / chart prefs / detail level                 |
| Episodic Memory Extractor   | `dash_memories` (`source=episodic`)| Surprises / corrections / repeated interest                  |
| Follow-up Suggester         | (frontend)                         | KG-aware next questions                                      |

Pattern miner runs separately: proven SQL with 3+ uses auto-becomes a `dash` schema view.

## Tier 2 — Week 1+: self-learning daily cycle

Scheduler triggers at `LEARNING_DAILY_TIME_UTC` (default 04:00). On K8S, the in-process scheduler is disabled (`LEARNING_SCHEDULER_DISABLED=true` on API pods) and a CronJob runs `python -m dash.learning.cycle` instead.

Cycle (`dash/learning/cycle.py`):

1. **Curiosity** (`curiosity.py`) — generate 20 questions from 10 gap sources (untouched tables, low-confidence answers, repeated user clarifications, KG holes, drift alerts, outlier columns, untested hypothesis lineages, idle dimensions, schema diff, learning_goals.md).
2. **Branch + prune** — each question forks 3 variants, top-1 kept by predicted information gain.
3. **Research** (`researcher.py`) — `asyncio.gather` across 7 tiers per question: DB → KG → Brain → Memory → LLM internal → Web (`external_data.py` → Tavily / Brave / Perplexity / FRED) → connector APIs.
4. **Hypothesis** (`hypothesis.py`) — testable claim with `parent_hypothesis` lineage (diff-as-experiment).
5. **Verifier** (`verifier.py`) — execute on real data, `statement_timeout 110s`, score support.
6. **Consolidator** (`consolidator.py`) — verified results route to **Memory** (project-scoped facts), **KG** (cross-source triples), **Brain** (org-wide canonicals), or **Rules** (constraints).
7. **Forgetting** (`forgetting.py`) — daily decay -0.02 / day on unused memories, threshold archive.
8. **Promotion** (`promotion.py`) — verified hypothesis with 3+ project agreement → PII-scrubbed → LLM-gated → central Brain (`project_slug=NULL`), visible to all opt-in projects.

Run-then-review digest (`digest.py`) summarizes the cycle into one paragraph; posted to Slack if `SLACK_LEARNING_WEBHOOK` set.

Sunday CronJob runs the same cycle in dry-run canary mode (no writes) to detect prompt regressions.

`agent_iq` (`agent_iq.py`) — single-number health metric. Grows from ~0 (new project) to 500-1000 (well-trained, multi-week). Sparkline in Command Center.

## Tier 3 — steer the loop

### `learning_goals.md` per project

Edit `knowledge/<slug>/learning_goals.md` (or Settings → SELF-LEARN tab). Bullet-list domain priorities. Injected into curiosity prompt — biases the 20 questions toward your goals.

```markdown
# Learning goals — fund3

- Track fund-level IRR drift week over week
- Surface portfolio-company KPI changes not in monthly reports
- Detect covenant breaches before quarter close
```

### Cost cap per project per day

Default $1 (`LEARNING_DAILY_COST_CAP_USD`). Enforced by `cost_guard.py` — cycle aborts mid-question when cap hit, partial progress saved.

```sql
UPDATE dash_projects SET daily_cost_cap_usd = 0.50 WHERE slug = 'mine';
```

Or Settings → SELF-LEARN tab → **Daily cost cap** input.

### Time cap per question

120 s hard ceiling. `verifier.py` SQL has 110 s `statement_timeout` (10 s headroom).

### External data tiers

Tier 3 env vars in `DEPLOYMENT.md`. Without them, researcher skips web/macro tiers (DB / KG / Brain / Memory / LLM still run). Useful when learning goals reference macro context (e.g. "compare our churn to SaaS benchmark").

## Tier 4 — Brain seeds (bulk-load priors)

For domain-specific knowledge skip the cold start:

- Settings → BRAIN → **IMPORT RETAIL SEEDS** (107 retail seeds: RFM, basket, cohort, NPS thresholds…)
- Or `POST /api/brain/import-seeds` with `{"pack": "retail" | "saas" | "finance"}`
- 241 canonical column priors (`column_priors.py`) auto-match column names on upload

Brain entries scope: **Global** (`project_slug=NULL`, all projects), **Project** (`project_slug='mine'`), **Personal** (`user_id=42`, Dash Agent only). UI: `/ui/brain` with scope filter tabs.

## Reading the score

Settings → SELF-LEARN tab shows:

| Metric                      | What it means                                                  |
| --------------------------- | -------------------------------------------------------------- |
| `agent_iq`                  | Composite — verified facts × KG depth × Q&A pass rate          |
| Verified hypotheses (7d)    | Cycle output that passed `verifier.py`                         |
| Promoted to central (7d)    | Crossed 3-project agreement gate                               |
| Forgetting archive (7d)     | Memories below decay threshold, hidden from prompt             |
| Daily cost (7d sparkline)   | LLM spend; should plateau as gaps close                        |

Healthy trajectory: IQ rising, verified rate stable 60-80 %, cost trending down.

## Common pitfalls

| Symptom                                       | Cause / fix                                                            |
| --------------------------------------------- | ---------------------------------------------------------------------- |
| IQ flat for a week                            | No new chats AND no new data — loop has nothing new to learn           |
| Cycle aborts halfway                          | `daily_cost_cap_usd` hit. Raise cap or scope `learning_goals.md` tighter |
| Promotion never fires                         | Need 3 projects agreeing on same canonical. Single-project deploy → disable promotion or accept project-only Brain |
| "No verified facts today"                     | All 20 questions failed verification. Check `dash_learning_runs.error`. Often: missing FK, statement_timeout |
| Slack digest empty                            | Cycle produced no consolidations. Expected on quiet days.              |

## Related

- `ARCHITECTURE.md` — 13 context layers, agent topology
- `DEPLOYMENT.md` — Tier 3-5 env vars
- `docs/SLACK_CONNECT.md` — wire digests
- `docs/TEST_QUESTIONS.md` — validate a freshly trained project
- `dash/learning/` — all 17 modules listed in `CLAUDE.md`
