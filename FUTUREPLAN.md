# FUTUREPLAN.md

> Roadmap of what is NOT yet built. Living document, organized by tier.
> Tier order = priority × user-visible value, not effort.

---

## TIER 1 — required for v2 launch (1–2 weeks)

| Item | Why |
|---|---|
| Real K8S cluster smoke deploy | Helm chart untested on real cluster |
| Tavily / Brave / Perplexity env wiring | Curiosity researcher silently degraded without web search |
| FRED / Alpha Vantage env wiring | External-data hypotheses can't run |
| Slack webhook validation | Daily digest path unverified at scale |
| Service Principal Fabric token rotation | Static SP tokens block enterprise SSO |
| Group AD / SSO federation | Per-user OIDC works; group claims don't flow into RBAC |
| Penetration test (external firm) | $15–30K. Scope in `SECURITY.md` |
| SOC 2 Type 1 | 3 mo, $25–80K. Required for enterprise pipeline |
| Encryption-at-rest for OAuth tokens | Known gap in `SECURITY.md` |

---

## TIER 2 — quick wins (1–3 days each)

| Item | Why |
|---|---|
| MCP provider | Steal pattern from Scout — 1000s of tools instantly |
| Web fetch as Researcher tool | Live URL ingestion at chat time |
| Snowflake provider | Enterprise must-have |
| BigQuery provider | GCP shops |
| Databricks provider | Lakehouse parity |
| Salesforce connector | CRM analytics |
| HubSpot connector | Marketing analytics |
| Slack connector (data, not just digest) | Conversation mining |
| Microsoft Purview DLP integration | Compliance gate |
| Backup automation (cron + S3) | No automated backup today |
| Prometheus + Loki + Grafana | Logs/metrics today are `docker logs` only |
| Trivy CI scan | Image CVE gate |
| Performance SLA + SLI dashboard | Latency/error budget visibility |
| Rate limit per source (not just per-IP) | Today only SlowAPI per-IP |
| GDPR data subject endpoint | Legal requirement for EU tenants |

---

## TIER 3 — UI/UX (2–3 weeks)

| Item | Why |
|---|---|
| Mobile responsive chat | Project chat breaks <768px |
| Onboarding wizard | First-run UX is bare |
| Drift alert UI bell | `dash_drift_alerts` populated, no UI surface |
| Cost dashboard per source | LLM/source spend opaque |
| Source health page (graphs) | Connector status only in Command Center text |
| Tree-of-experiments visualizer | Lineage exists in DB; needs interactive view |
| Sparkline polish (7d/30d/90d toggle) | Currently only 7d |
| Branding live preview | `branding/<tenant>/` requires container restart |

---

## TIER 4 — advanced intelligence (3–4 months)

| Item | Why |
|---|---|
| Functional dependency mining | Auto-discover X → Y rules in tables |
| Entity resolution (cross-source) | Match "Acme Corp" across SF / HubSpot / DB |
| Outlier classification | Distinguish legit anomaly from data-quality |
| Causal inference module | Beyond correlation — DoWhy/EconML integration |
| T-Digest percentiles | Streaming p50/p95/p99 over learning runs |
| Counterfactual scenarios | "What if region X grew 10%?" |
| Active learning loop | Surface lowest-confidence rows for label |
| Hypothesis falsification log | Track which hypotheses got disproven and why |
| Bayesian model comparison | Replace "best R²" with posterior probabilities |

---

## TIER 5 — agent capabilities (2–3 months)

| Item | Why |
|---|---|
| Cross-source federation join | Kills the Palantir pitch |
| DAX bridge (Power BI semantic) | XMLA pull exists; query path doesn't |
| KQL bridge (Azure Data Explorer) | Logs / telemetry workloads |
| Async chat queue | Long-running questions today block stream |
| Slack bot (chat from Slack) | Meet users where they are |
| Teams bot | Same, MS shops |
| Voice assistant | Speech-to-SQL via Whisper |
| Multi-step planner agent | Today's Leader is shallow; planner could decompose 5+ steps |

---

## TIER 6 — data quality + lifecycle (1 month)

| Item | Why |
|---|---|
| GDPR delete API | Legal requirement |
| Data contracts | Producer/consumer schema agreements |
| Schema versioning | Track schema across retrains |
| Time-travel queries | Reproduce analysis as-of past date |
| Soft-delete + retention policies | Today: hard delete only |
| Audit-log retention rules | SIEM forwarding + tiered storage |
| Lineage to source row | Today: source file, not source row |

---

## TIER 7 — testing + tooling (1 week)

| Item | Why |
|---|---|
| `tests/test_learning_api.py` | `/api/learning/*` endpoints uncovered |
| `tests/test_scheduler.py` | CronJob + canary path uncovered |
| `tests/test_promotion.py` | Promotion gate uncovered |
| `tests/test_lineage.py` | Lineage walk uncovered |
| `tests/test_digest.py` | Digest summary uncovered |
| `tests/test_cost_guard.py` | Cap pre-flight uncovered |
| End-to-end smoke (real DB + provider + cycle) | No integration test today |
| Playwright frontend tests | Manual smoke only |
| Pre-commit hooks (ruff + black + mypy + gitleaks) | Tracked in `AGENTS.md` |
| 24 h soak test | Memory leak detect |
| 200-user load re-run after major changes | Last run was v1.0 |

---

## Top 10 highest leverage

| # | Item | Payoff |
|---|---|---|
| 1 | MCP provider | 1000s of tools instantly |
| 2 | Cross-source federation join | Kills Palantir pitch |
| 3 | Web fetch tool | Live URLs in chat |
| 4 | E2E smoke test | Catch integration bugs |
| 5 | Backup automation | Data safety |
| 6 | Trivy CI scan | Compliance gate |
| 7 | Snowflake provider | Enterprise must-have |
| 8 | Drift alert UI bell | User-visible growth signal |
| 9 | User guide doc | Self-serve adoption |
| 10 | GDPR delete API | Legal requirement |

---

## Out of scope (rejected)

| Idea | Reason |
|---|---|
| Replace Agno with MindsDB Agents | Agno team has 13 context layers, self-correction, meta-learning. MindsDB agents have none. Rebuild cost > benefit |
| Replace PgVector with MindsDB KB | Already integrated, deeply wired into `search_knowledge_base` |
| Embed Anton (CLI agent) | AGPL-3.0 — would force whole app open-source. CLI-only, no API |
| Add Scratchpad (Python exec) | Massive security surface; specialist + ML tools cover analytical needs |
| MindsDB sidecar | Optional data layer; kpt patterns + provider abstraction cover ML needs |
| Webhook plugin marketplace | Premature; not enough plugin demand to warrant infra |

---

## Capacity notes

- Current team can hold ~3 TIER-1 items in flight.
- TIER-4 items each need a research spike before estimation.
- TIER-2 items can be parallelised across contributors with no shared code path.

## Related docs

- `CHANGELOG.md` — what shipped
- `UPGRADE.md` — version-to-version migration
- `ARCHITECTURE.md` — system layout
- `AGENTS.md` — coding rules + planned pre-commit gates
- `SECURITY.md` — pen-test scope, encryption-at-rest gap
