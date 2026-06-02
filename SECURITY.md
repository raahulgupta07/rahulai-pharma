# SECURITY.md

> Threat model, controls, secrets management, and audit guidance for Dash.

## Audience

- **Operators** running Dash in production.
- **Auditors** assessing the platform.
- **Engineers** modifying any auth, RBAC, or data path.

## Threat model

Dash is a multi-tenant data notebook. Tenancy is per-project (PostgreSQL schema isolation). Threats considered:

| # | Threat | Severity | Control |
|---|--------|----------|---------|
| T1 | Cross-project data leak | Critical | Schema isolation + `check_project_permission` decorator on every endpoint |
| T2 | LLM-generated SQL drops a table | Critical | LLM SQL sandbox blocks `DROP/ALTER/TRUNCATE/MERGE`; `UPDATE/DELETE` only on target table; rollback if >50% rows affected |
| T3 | LLM injects malicious SQL via uploaded data | High | All SQL parameterized; `quote_ident` for reserved words; CSV delimiter auto-detection |
| T4 | Token theft → impersonation | High | DB-backed tokens with TTL; thread-safe cache; bounded cache size |
| T5 | Path traversal via project slug | High | Slug regex `^[a-z0-9_-]+$` validated before any disk path |
| T6 | SSRF via connector callback URL | High | OAuth callback URLs allowlisted; `state` parameter validated |
| T7 | Secrets leaked in logs | High | `AGNO_DEBUG=False` in prod; password hashing; OAuth tokens base64 (encryption-at-rest is a known gap, see below) |
| T8 | DoS via ML training | Medium | ML worker isolated (1 GB cap, 5-min SIGALRM, LIMIT 100K); rate limiter (default 500/min) |
| T9 | DoS via large file upload | Medium | Streaming chunks (1 MB), max 200 MB per file, Caddy `max_size 250MB` |
| T10 | DoS via long context window | Medium | 50K char message cap; weighted truncation; 5-min stream timeout |
| T11 | Cross-tenant query in a single LLM call | Medium | Project context loaded per request; no global semantic model; team cache keyed by project |
| T12 | Connection exhaustion | Medium | PgBouncer transaction mode + `NullPool` everywhere + engine TTL eviction |
| T13 | Stale frontend serving old auth code | Low | `.dockerignore` strict; clean build dirs; SW self-unregisters |
| T14 | Stuck SQL via uncancellable `asyncio.to_thread` | Medium | DB `statement_timeout 110s` (cooperative cancel) — see Known issues |
| T15 | Multi-pod scheduler race promoting same canonical | Low | K8S CronJob replaces in-process daemon + Brain unique index (migration 006) |
| T16 | PII column-name collision | Medium | Conservative-rank resolves to most-restrictive strategy; qualified column extraction via `sqlglot` |

## Authentication

### Local users
- Stored in `dash_users`. Password hashing via `bcrypt`.
- Login → `dash_tokens` row (24 h TTL, auto-refresh on use).
- Token cache: `threading.Lock`-protected dict, bounded 5K, TTL eviction.

### OIDC (Keycloak default)
- Configured via `KEYCLOAK_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET`.
- Discovery endpoint loads automatically.
- ID token verified; user record auto-provisioned on first login (matched by email).
- Local users continue to work alongside OIDC.

### Token transport
- `Authorization: Bearer <token>` header.
- HTTPS enforced via Caddy auto-SSL.
- HSTS header set by Caddy.

## Authorization (RBAC)

Three roles, hierarchical:

| Role | Read | Chat | Upload | Train | Connectors | Admin |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| viewer | yes | yes | no | no | no | no |
| editor | yes | yes | yes | yes | yes | no |
| admin  | yes | yes | yes | yes | yes | yes |

Plus an implicit `owner` role: project creator inherits admin + cannot be removed without explicit transfer.

Enforcement:
- **Backend:** every endpoint annotated with `check_project_permission(slug, role)`. Default-deny.
- **Frontend:** `canEdit` / `canAdmin` derived from session; hides UI but never trusted as enforcement.
- **Sharing:** `dash_project_shares` table maps `(project_id, user_id, role)`. Owners invite users.

36+ endpoints check permission via `check_project_permission(role)`.

## Tenancy isolation

- Each project lives in PostgreSQL schema `proj_{slug}` (sanitised).
- `check_project_permission` resolves slug → schema → permission, returns 403 on mismatch.
- Per-project SQLAlchemy engine cached with TTL. Engine key includes project slug; cache hits never cross tenants.
- `SET LOCAL search_path TO proj_{slug}` inside transaction → cross-tenant reads physically impossible without explicit schema-qualified DDL.
- Per-source memory / KG / Brain scoping (project / shared / agent-only).
- Central pool opt-out per project (`contribute_to_central=FALSE`).
- Branding-level tenant separation via `branding/<tenant>/` directory.

## SQL safety

1. **Parameterized everywhere.** Direct string concat into SQL is forbidden by code review.
2. **Reserved words escaped** via `quote_ident` from `psycopg2.extensions`.
3. **CSV delimiter auto-detection** via `chardet` + `csv.Sniffer`.
4. **View creation validation** — column names matched against allowlist regex before `CREATE VIEW`.
5. **LLM SQL sandbox** (`_ai_review_and_fix_table()` in `app/upload.py`):
   - Blocks `DROP`, `ALTER`, `TRUNCATE`, `MERGE`.
   - `UPDATE/DELETE` allowed only on target table.
   - Rolls back if >50 % of target rows would be affected.
   - `statement_timeout 110s` on remote queries (cooperative cancel for stuck SQL).
   - Read-only enforced via `SET LOCAL transaction_read_only=on`.
   - Fabric path: `ApplicationIntent=ReadOnly` + `SET LOCK_TIMEOUT 5000`.

## PII protection

- 5-detector classifier (`column_classifier.py`) flags PII at training time:
  stats, regex (69 patterns), name match, LLM, embedding cosine.
- 7 masking strategies (`pii_mask.py`):
  `block` / `redact` / `hash` / `hash_email` / `mask_email` / `mask_phone` /
  `generalize` / `truncate`.
- `pii_action` per source: `flag` (default) / `mask` / `block`.
- Conservative rank: collisions resolve to most-restrictive strategy.
- Qualified column extraction via `sqlglot` (avoids ambiguous column-name collisions).
- Every PII-touching query logged to `dash_audit_log`.

## Encryption

- `scram-sha-256` PostgreSQL password encryption (was md5).
- TLS via Caddy (auto-cert via Let's Encrypt) or cert-manager (K8S).
- HSTS + X-Frame-Options + nosniff + X-XSS-Protection headers.
- Service Principal tokens for Fabric: MSAL-managed, never persisted to disk.

### Known gap: encryption-at-rest

OAuth tokens in `dash_data_sources` are base64-encoded but **not encrypted with a separate key**. Postgres `pgdata` is at-rest encrypted only if the underlying disk is encrypted (cloud responsibility).

**Mitigation roadmap:**
1. Add `ENCRYPTION_KEY` env var (32 bytes hex/base64).
2. Wrap token writes in AES-256-GCM (`enc:v1:nonce:ciphertext`).
3. Backward compat — plain base64 reads still work.
4. Migrate via `scripts/encrypt_secrets.sh`.
5. Rotate via `OLD_ENCRYPTION_KEY` + new key + `POST /api/super/admin/encryption/rotate-key`.

Tracked in `FUTUREPLAN.md` TIER 1.

## Secrets management

| Secret | Storage | Rotation |
|--------|---------|----------|
| `OPENROUTER_API_KEY` | `.env` (host) | Rotate at openrouter.ai/keys, restart `dash-app` |
| `DB_PASS` | `.env` + PostgreSQL | Update both, restart all containers |
| `SUPER_ADMIN_PASS` | `.env`, first boot only | Change via UI after first login |
| `MS_CLIENT_SECRET` / `GOOGLE_CLIENT_SECRET` | `.env` | Rotate at provider, update `.env`, restart |
| Keycloak client secret | `.env` | Rotate at Keycloak admin |
| OAuth tokens (per-user) | `dash_data_sources`, base64 | Auto-refreshed by connector |
| Fabric Service Principal | env vars (`AZURE_CLIENT_ID/SECRET/TENANT`) | Manual rotate; planned in TIER 1 |
| User passwords | `dash_users.password_hash` (bcrypt) | User-driven via UI |
| `SLACK_LEARNING_WEBHOOK` | `.env` (optional) | Rotate at Slack |
| `TAVILY_API_KEY` / `BRAVE_API_KEY` / etc. | `.env` (optional) | Rotate at provider |

## Audit + observability

- `dash_audit_log` records: login, logout, project create / delete, share, role change, training start, file upload, dashboard create, brain edit, every PII-touching query.
- `dash_brain_access_log` records every Company Brain read.
- `dash_promotion_log` records every project → central promotion.
- `dash_self_learning_runs` records every cycle (status + cost + error).
- `/api/architecture` returns live counts for Command Center.
- 11 background agents log to their respective tables.

## Data residency

- Per-project Postgres schema isolation.
- Per-source memory / KG / Brain scoping.
- Central pool opt-out per project.
- Branding-level multi-tenant separation.

## Rate limiting

- Per-IP via SlowAPI (`RATE_LIMIT` env var, default 500 / min).
- 30 chat / min per user (configurable in code).
- 10 uploads / min per user.
- 5 training jobs / min per user.
- Per-source rate limiting planned (TIER 2).

## Container hardening

- Non-root Docker user.
- Bounded thread pool (5 workers).
- Healthchecks + memory limits (Caddy 512 M).
- `NullPool` everywhere — PgBouncer owns pooling.
- ML Worker `SIGALRM` 5-min timeout.
- Engine cache TTL eviction (max 200, 1 h TTL).
- Atomic JSON writes (`tempfile` + `os.replace`).
- Engine `dispose()` in `finally` blocks.
- `AGNO_DEBUG=False` in prod.

## Network security

- Caddy reverse proxy, auto-SSL via Let's Encrypt (ACME-01 / TLS-01).
- HSTS, X-Frame-Options, XSS protection, nosniff (Caddyfile).
- Caddy 512 M memory cap; `request_body max_size 250MB`; read/write timeout 300 s.
- All inter-container traffic on private Docker network. Only Caddy exposed.
- PgBouncer health check + `CLIENT_IDLE_TIMEOUT` + `QUERY_WAIT_TIMEOUT`.
- PostgreSQL `idle_in_transaction_session_timeout=60s` + `statement_timeout=120s`.

## ML worker isolation

- Memory cap: 1 GB.
- Job timeout: 5-min `SIGALRM`. Hung jobs marked `failed`.
- `LIMIT 100,000` on `SELECT *` to prevent OOM.
- `engine.dispose()` in `finally` for save / load / experiment.
- Reads `dash_ml_jobs.status='pending'`; updates atomically.

## File upload hardening

- Streaming chunks: 1 MB; max 200 MB total.
- 24 file formats; type sniffing on extension + magic bytes.
- Auto-encoding detection (chardet) for CSV.
- EXIF auto-rotation for images.
- Image min size 3 KB filter.
- Path traversal: slug + filename regex-validated; written under `knowledge/{slug}/` only.
- ZIP recursion capped at 20 files.

## Penetration testing

No formal pen-test on Dash yet. Tracked in `FUTUREPLAN.md` TIER 1 ($15–30K external firm). Recommended scope:

1. Token theft + replay across project switches.
2. Schema isolation under concurrent multi-tenant load.
3. LLM SQL sandbox bypass via prompt injection.
4. Path traversal on every file endpoint.
5. CSRF on auth endpoints.
6. SSE stream hijack.
7. ML worker job poisoning.
8. K8S CronJob privilege escalation.
9. Branding directory traversal.
10. Fabric Service Principal token theft.

## Compliance posture

Dash inherits whatever the host PostgreSQL + cloud disk encryption provides. Out of the box, Dash is not certified for HIPAA / SOC 2 / GDPR.

### Roadmap

- **SOC 2 Type 1** — 3 mo, $25–80K. Tracked in `FUTUREPLAN.md` TIER 1.
- **GDPR data subject endpoint** — TIER 6.
- **HIPAA** — only if healthcare tenants commit.
- **Microsoft Purview DLP** — TIER 2.
- **Encryption-at-rest** — see Known gap above.

## Known issues + mitigations

| Issue | Mitigation |
|---|---|
| `asyncio.to_thread` can't kill stuck SQL | DB `statement_timeout 110s` (cooperative cancel) |
| Column-name PII collision | Qualified extraction via `sqlglot` + conservative rank |
| Multi-pod scheduler race | K8S CronJob replaces in-process daemon (v1.1.0) |
| Brain duplicate canonicals under concurrent promotion | Unique index via migration 006 |
| OAuth tokens not encrypted at rest | Roadmap above |

## Reporting a vulnerability

Email `security@your-org` (configure for your deployment). Do not open public GitHub issues. Allow 90 days for fix before disclosure.

## Related docs

- `AGENTS.md` — coding rules (auth, RBAC, secrets handling)
- `ARCHITECTURE.md` — schema isolation, RBAC enforcement points
- `DEPLOYMENT.md` — Caddy config, env vars
- `DEPLOYMENT_K8S.md` — K8S production deploy
- `UPGRADE.md` — secrets rotation procedure
- `FUTUREPLAN.md` — pen-test, SOC 2, encryption-at-rest tracked in TIER 1
- `OPERATIONS.md` — incident response
