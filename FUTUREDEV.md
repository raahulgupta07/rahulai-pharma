# Future Development Backlog

Pending work organized by tier. Updated 2026-05-06.

---

## TIER 1 — required for production launch (1-2 weeks)

Mostly deploy + ops work, blocked on external access.

| Item | Blocked on |
|------|-----------|
| Real K8S cluster smoke deploy | cluster access |
| End-to-end smoke test (UI walk) | deploy |
| Real env vars: TAVILY/FRED/SLACK | free-tier signups |
| Real Service Principal Fabric token | Azure AD app reg |
| Real Group AD/SSO federation | IT team |
| Real container registry push | Harbor/ACR/ECR access |
| Storage class confirmation | infra team |
| Penetration test | external firm $15-30K |
| SOC2 Type 1 | audit firm $25-80K, 3 mo |
| GDPR data subject endpoint | legal review |
| DR plan + warm standby | infra team |
| Backup automation cron + S3 | 1-2 days dev |
| 24h soak test (memory leak detect) | 1 day dev |
| 200-user concurrency re-validation | 1 day dev |

---

## TIER 2 — quick wins (build now, no dependencies)

| Item | Effort | Leverage |
|------|--------|----------|
| MCP provider (steal from Scout) | 1 wk | HIGH |
| Cross-source federation join | 2 wk | HIGH |
| Web fetch as Researcher tool | 3 days | HIGH |
| Snowflake provider | 1 wk | MED |
| BigQuery provider | 1 wk | MED |
| Databricks provider | 1 wk | MED |
| SQL Server (non-Fabric) provider | 3 days | MED |
| Salesforce / HubSpot / Slack connectors | 3 days each | LOW-MED |
| Confluence / Notion / Jira | 3 days each | LOW |
| MongoDB / NoSQL provider | 1 wk | LOW |
| Microsoft Purview DLP integration | 1 wk | MED |
| Prometheus metrics endpoint | 1 wk | MED |
| Grafana dashboard JSON | 3 days | MED |
| Loki / ELK log aggregation | 1 wk | MED |
| Trivy CI scan | 2 days | HIGH |
| SBOM generation | 1 day | LOW |
| Performance SLA + SLI dashboard | 2 wk | MED |
| Field-level encryption | 1 wk | MED |
| Secrets rotation (Vault/KMS hooks) | 1 wk | MED |
| Per-source rate limit | 2 days | LOW |
| Cost dashboard per source UI | 3 days | MED |
| Drift alert UI bell | 2 days | MED |
| Source health page (graphs) | 1 wk | MED |

---

## TIER 3 — UI/UX polish (defer to v1.2+)

| Item | Effort |
|------|--------|
| Mobile responsive chat | 3 days |
| Dark/light theme toggle | 2 days |
| Multi-language UI (i18n) | 1 wk |
| Real-time collab (presence/cursor) | 2 wk |
| Onboarding wizard for new tenants | 1 wk |
| Custom branding per project | 3 days |
| Keyboard shortcuts | 2 days |
| Saved searches / pinned queries | 3 days |
| Voice input (whisper) | 1 wk |
| Inline edit memories from UI | 2 days |
| Drag-to-reorder dashboards | 3 days |

---

## TIER 4 — advanced intelligence (3-4 months)

| Item | Effort |
|------|--------|
| Functional dependency mining | 1 wk |
| Entity resolution across cols | 2 wk |
| Outlier classification (IQR + LLM) | 1 wk |
| Format inference (date/time) | 3 days |
| Column entropy / Benford check | 3 days |
| Word2Vec on text values | 1 wk |
| Semantic clustering of cols | 1 wk |
| HyperLogLog NDV from sample | 3 days |
| T-Digest streaming percentiles | 3 days |
| Causal inference (DoWhy / EconML) | 2 wk |
| Time-series anomaly + forecast UI | 1 wk |
| What-if simulator | 2 wk |
| Counterfactual analysis | 1 wk |
| Prescriptive optimization (CP-SAT) | 2 wk |
| Bigram language detection | 2 days |
| PII data residency tags | 3 days |

---

## TIER 5 — agent capabilities (2-3 months)

| Item | Effort |
|------|--------|
| Live SQL→DAX bridge for Fabric | 1 wk |
| KQL bridge (Fabric KQL DBs) | 1 wk |
| Agent-to-agent message passing | 2 wk |
| Long-running task queue | 1 wk |
| Async chat (job returns later) | 1 wk |
| Scheduled report generator | 1 wk |
| Email digest cron | 3 days |
| Slack bot integration | 1 wk |
| Teams bot integration | 1 wk |
| Voice assistant (phone/desk) | 2 wk |
| Multi-language Q&A (zh/ja/es/fr) | 1 wk |
| Conversational long-term memory | 2 wk |
| Reasoning traces export | 3 days |
| Trust-region SQL mutation (write back) | 2 wk |

---

## TIER 6 — data quality + lifecycle (1 month)

| Item | Effort |
|------|--------|
| Data freshness SLA per table | 3 days |
| Automated data quality rules | 1 wk |
| Anomaly digest email | 3 days |
| Data contract enforcement | 1 wk |
| Schema versioning + migration | 1 wk |
| GDPR right-to-deletion API | 3 days |
| Audit log retention policy | 2 days |
| Source archive (decommission) | 3 days |
| Project clone / template | 3 days |
| Source diff (compare two snapshots) | 1 wk |

---

## TIER 7 — testing gaps (1 week)

| Item | Effort | Priority |
|------|--------|----------|
| tests/test_learning_api.py | 1 day | HIGH |
| tests/test_scheduler.py | 1 day | HIGH |
| tests/test_promotion.py | 1 day | HIGH |
| tests/test_lineage.py | 1 day | MED |
| tests/test_digest.py | 1 day | MED |
| tests/test_cost_guard.py | 1 day | MED |
| E2E (real DB + provider + cycle) | 2 days | HIGH |
| Playwright UI E2E | 2 wk | MED |
| Pre-commit hooks (ruff + black + mypy) | 1 day | LOW |
| Trivy CI scan | 2 days | HIGH |

---

## TIER 8 — meta tooling (deferred)

| Item | Effort |
|------|--------|
| MkDocs/Docusaurus site | 3 days |
| Video walkthroughs | 1 wk |
| Architecture diagram (proper PNG) | 1 day |
| User guide (animated GIFs) | 3 days |
| Developer-onboarding guide | 1 day |

---

## Top 10 to start now

| Rank | Item | Reason |
|------|------|--------|
| 1 | MCP provider | opens 1000s of tools instantly |
| 2 | Cross-source federation join | kills Palantir pitch |
| 3 | Web fetch Researcher tool | live URLs in chat |
| 4 | Trivy CI scan | compliance gate |
| 5 | Backup automation | data safety basic |
| 6 | Snowflake provider | enterprise must-have |
| 7 | E2E smoke test | catches integration bugs |
| 8 | test_learning_api + test_scheduler | coverage gaps |
| 9 | GDPR delete endpoint | legal hard requirement |
| 10 | Drift alert UI bell | user-visible growth signal |

---

## Operational pending

### User must provide
- Real container registry URL
- Azure App Reg client_id/secret/tenant_id
- Storage class names
- Group AD federation config
- Optional API keys: Tavily, FRED, Brave, Perplexity, Slack webhook

### Decisions needed (have safe defaults)
- Central pool opt-out per project? (default: opt-in)
- PII default action? (default: `flag`)
- Daily cost cap per project? (default: $1)
- Web search budget cap? (no default yet)

---

## Recommended sequencing

```
WEEK 1   build top 3 (MCP + cross-source fed + web fetch)
WEEK 2   Trivy CI + backup automation + Snowflake provider
WEEK 3   TIER 7 tests + E2E
WEEK 4   smoke deploy on real cluster (when access available)
WEEK 5+  TIER 2 remaining + TIER 3 UI polish
```

---

## Out of scope (rejected for v1)

- Federated learning across tenants (privacy nightmare)
- On-prem GPU hosting (manage external LLM via OpenRouter)
- Custom LLM fine-tuning (use prompt + Brain instead)
- White-label per-project (already at tenant level)
- Mobile native apps (responsive web sufficient)
- Blockchain anything

---

## Bottom line

```
SHIP-READY today:        backend + frontend + docs + 153 tests pass
NEEDS CLUSTER:           to validate end-to-end on real K8S
NEEDS BUDGET:             pen test ($15-30K) + SOC2 ($25-80K, 3 mo)
NEEDS USER INPUTS:        registry + Azure AD + storage class
```
