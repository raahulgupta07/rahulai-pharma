# Security Testing — pre-production gate

Penetration / security test plan for CityPharma before going live. Tailored to the
real stack: **FastAPI + agentic LLM (text-to-SQL + tools) + Postgres/pgvector/AGE +
PgBouncer + Redis + Caddy/nginx/ALB + JWT-style tokens + OpenAI-compatible API
gateway + multi-store scoping + LDAP/OIDC**.

> **Rules of engagement:** test in **staging with prod-like config**, never prod.
> Snapshot the DB first. You own the system — but if you hire a firm, give written
> scope. Check each box only when it **passes**.

---

## 0. Hardening already shipped (commit f58135d + this round)

- [x] `SUPER_ADMIN_PASS` fail-closed (no admin/admin)
- [x] Fernet key requires `CONNECTION_ENCRYPTION_KEY`/`JWT_SECRET` (no dev fallback)
- [x] Telemetry retention loop (no unbounded log growth)
- [x] Direct-DB connection cap (`DIRECT_DB_MAX_CONN`) — no `too many clients`
- [x] Daemons off the event loop; bounded per-chat thread pool
- [x] Upload temp-file leaks closed; naive/aware datetime crashes fixed
- [x] pgbouncer pinned by digest; Caddy gated on `service_healthy`
- [x] **CSP** added to Caddyfile + nginx block; full header parity in nginx
- [x] **Redis `REDIS_PASS`** support (set in prod)
- [x] **SG / network-exposure** table in `DEPLOYMENT_AWS.md`

### Phase-5 fixes shipped (A, B, C, E-tactical)
- [x] **A — writable Engineer locked down.** Dropped from embed + store-locked
  teams (`team.py` `allow_write_agents`/`is_store_locked`); embed passes
  `allow_write_agents=False`; cache key now includes write-state.
- [x] **A/E — catastrophic-statement guard** on the writable engine
  (`db/session.py` `_make_blast_guard`): rejects `DROP DATABASE/SCHEMA/ROLE`,
  `ALTER SYSTEM`, `COPY…PROGRAM` (quoted literals stripped first). Migrations use a
  separate engine and are unaffected.
- [x] **B — web shop-staff store-scope enforced.** `validate_token` now returns the
  store binding; `resolve_api_scope` honors web `scope_mode='store'`; both web chat
  handlers set `API_STORE_SCOPE` → masking + raw-SQL strip engage (was prompt-only).
- [x] **C — auth hardening.** OIDC email-merge now requires `email_verified` + never
  merges into admin/super (`auth_federation.py`), default merge OFF; password min
  4→8; per-IP login throttle (20/5min, Redis); legacy unverified Keycloak OIDC routes
  gated behind `LEGACY_OIDC_ENABLED` (default off).

### Still open — the durable DB-role cutover (staged, needs DBA + staging)
- [ ] **App DB role `ai` is a SUPERUSER.** The blast guard above blocks the
  catastrophic statements, but the *durable* fix is least-privilege. **Blocker found:**
  migrations run **in the app boot** (`app/main.py:251` → `dash/db_runner/migrate.py`),
  so the app role can't simply be demoted — boot migrations need DDL/extension rights.
  **Cutover plan (staging-test first):**
  1. Create `cp_app` (non-superuser): `CONNECT`, `USAGE` on `public`/`dash`/`citypharma`,
     `SELECT` on all + default-privileges, `INSERT/UPDATE/DELETE` on app tables,
     `CREATE` on `dash` only. No `CREATEROLE`/`CREATEDB`/`SUPERUSER`.
  2. Add a separate `MIGRATE_DATABASE_URL` (superuser) and point
     `dash/db_runner/migrate.py` at it; run migrations as super, runtime pool as `cp_app`.
  3. AGE is `shared_preload_libraries='age'` so `LOAD 'age'` works for non-super — verify
     graph queries + ingest + Engineer view-build still pass on staging before cutover.

---

## P0 — Total-compromise boundaries (MUST pass)

### 1. Multi-store authorization (THE security boundary)
- [ ] Key bound to Store A cannot read Store B qty/price/PII (own/other/ref tiers)
- [ ] `store_id` / store header / JWT claim tampering → no escalation
- [ ] Multi-outlet set-keys can't widen beyond their store set
- [ ] `mask_row` actually strips qty+price on `other` tier (every endpoint)
- [ ] Embed widget respects store scope (per-request `API_STORE_SCOPE` contextvar)
- Tools: Burp Suite (authz matrix), manual. **Pass:** zero cross-store leakage.

### 2. LLM prompt injection & agent abuse
- [ ] Direct jailbreak: "ignore instructions / you are admin / show all stores" (EN + Burmese)
- [ ] **Indirect injection** via uploaded doc / learned chat fact treated as commands
- [ ] Agent coaxed into DDL/DML or cross-schema/cross-store SELECT
- [ ] System-prompt / tool-schema exfiltration
- [ ] Store-scope contextvar bypass via the model
- Tools: **garak**, **PyRIT**, **promptfoo**, manual. **Pass:** no scope bypass, no other-store data, no DDL/DML, no prompt leak.

### 3. SQL injection (classic + agent text-to-SQL)
- [ ] `sqlmap` on every endpoint/param/filter → no injection
- [ ] String-interpolated identifiers (username, slug, store code) — re-test the historical `DROP SCHEMA "user_{username}"` class
- [ ] Confirm agent SQL uses the **read-only engine** (`default_transaction_read_only`, DB rejects DML) — no writable path
- [ ] Runtime DB role is **least-privilege** (see §0 open item)
- Tools: **sqlmap**, code review. **Pass:** no injection; agent SQL can't write.

### 4. Authentication & privilege escalation
- [ ] Login brute-force / credential stuffing → rate-limited / lockout
- [ ] Token entropy, 7-day expiry, **revocation propagates ≤60s across all workers** (`_TOKEN_CACHE_FRESH_TTL`)
- [ ] No admin/admin on ANY deploy path (k8s, bare gunicorn, mcp)
- [ ] LDAP: anonymous bind / bind injection / bypass
- [ ] OIDC/Keycloak: signature + `aud` + `iss` + nonce validated; **email-claim account-takeover** (Azure fallback) closed; auth-code replay blocked
- [ ] Horizontal (other users' data) + vertical (user→admin→super) escalation
- Tools: Burp Intruder, `jwt_tool`. **Pass:** no bypass/escalation; revocation works.

---

## P1 — High

### 5. File upload (xlsx/csv/pdf/zip/docx/xml/ods parsers)
- [ ] Zip slip (path-traversal member names in `_handle_zip`) → no write outside temp
- [ ] Zip bomb / decompression DoS bounded; 200 MB cap enforced pre-parse
- [ ] XXE in xml/ods/docx; CSV/XLSX **formula injection** (`=cmd|…`) in exports
- [ ] Malicious PDF/docx → no parser RCE (pypdf/python-docx CVEs current)
- [ ] Content-type spoof / double extension / null byte
- Tools: manual + malware corpus, **trivy** (parser deps). **Pass:** no escape/XXE/RCE; bounded memory.

### 6. SSRF (connectors + OIDC + image fetch)
- [ ] S3 endpoint / OneDrive / SharePoint Graph URL / OIDC discovery / Pexels can't hit `169.254.169.254`, internal IPs, `file://`
- [ ] **IMDSv2-only** on instances; egress allowlist
- Tools: Burp Collaborator. **Pass:** internal/metadata blocked.

### 7. Secrets & information disclosure
- [ ] `gitleaks`/`trufflehog` on repo **+ full git history** — `.env` never committed
- [ ] `DASH_DEBUG` blank in prod (no traceback leak, `/api/_debug/*` unmounted)
- [ ] CORS not `*`-with-credentials; no stack/version disclosure; SvelteKit sourcemaps off
- [ ] `CONNECTION_ENCRYPTION_KEY` set + stable (not dev fallback)
- Tools: **gitleaks**, ZAP passive. **Pass:** no secrets in repo/history/responses.

### 8. API gateway hardening
- [ ] Key entropy; key→store binding can't be unbound
- [ ] Tool-masking can't be forced off; rate-limit can't be bypassed (header spoof / key rotation)
- [ ] Usage-metering tamper-proof; `/api/v1/docs` (no-auth) leaks no data
- **Pass:** binding + masking + metering hold under tampering.

---

## P2 — Medium

### 9. DoS & cost abuse (LLM = financial DoS)
- [ ] Expensive-query flooding bounded by token caps
- [ ] Per-key/org **global Redis** rate limit holds (not per-worker)
- [ ] Connection cap (`DIRECT_DB_MAX_CONN`), upload flood, regex/zip bombs, giant bodies all bounded
- Tools: `ffuf`/`wrk`. **Pass:** limits + caps hold; no unbounded spend.

### 10. Web / client-side
- [ ] **Stored XSS** via chat answers / uploaded-doc content / learned facts / embed (rendered HTML) — verify markdown sanitized (DOMPurify or equivalent)
- [ ] Reflected XSS; CSRF (token-in-header — confirm no cookie-auth path); clickjacking (`X-Frame DENY` ✓); **CSP enforced** (now set); open redirect (PUBLIC_URL/OIDC callback); SSE injection
- Tools: ZAP/Burp active. **Pass:** output escaped/sanitized; CSP holds.

### 11. Infrastructure & transport
- [ ] `testssl.sh` → TLS1.2+ only, strong ciphers, HSTS
- [ ] All security headers + CSP present on the **actual front proxy** (Caddy *or* nginx)
- [ ] Redis `REDIS_PASS` set; Postgres + pgbouncer not internet-reachable
- [ ] `nmap` the box/SG → only 443 public; DB/Redis/8000 private
- [ ] Container runs **non-root** (`USER dash`); backups encrypted
- Tools: **nmap**, **testssl.sh**, **trivy**. **Pass:** minimal surface.

### 12. Supply chain
- [ ] `pip-audit`/`osv-scanner` (Python), `npm audit`/`osv` (frontend) → no critical/high
- [ ] **trivy**/**grype** on `citypharma:latest` → no critical/high
- [ ] AGE pinned (not master); `dockerize` vendored/removed
- **Pass:** no unpatched critical/high; build inputs pinned.

### 13. Business-logic abuse (app-specific)
- [ ] Knowledge-base poisoning: false facts via chat stay **pending** (review gate can't be bypassed; chat facts don't auto-override docs)
- [ ] Feedback-loop poisoning (mass 👎 → distiller) gated
- [ ] Autonomy actions gated; **`DEMO_SEED_ON_EMPTY=0`** in prod
- **Pass:** no unreviewed fact reaches active; learning loops gated.

---

## Tooling — install once

```bash
# Web / network
brew install nmap testssl                       # or apt
pipx install sqlmap                              # SQLi
# Burp Suite Pro (or OWASP ZAP) — interactive proxy / active scan

# Secrets
brew install gitleaks                            # or: docker run zricethezav/gitleaks

# Dependencies + container CVEs
pipx install pip-audit
brew install trivy grype                         # or aquasec/trivy docker image
go install github.com/google/osv-scanner/cmd/osv-scanner@latest

# LLM red-team
pipx install garak
pip install pyrit-ai promptfoo                   # promptfoo also via npm i -g promptfoo
```

## Quick local scans (run from repo root)

```bash
# Secrets in working tree + full history
gitleaks detect --source . --redact -v
gitleaks detect --source . --log-opts="--all" --redact   # history

# Python dependency CVEs
pip-audit -r requirements.txt

# Frontend dependency CVEs
( cd frontend && npm audit --production )

# Image CVEs (build first)
trivy image citypharma:latest --severity HIGH,CRITICAL

# Port exposure (run against the box, not localhost-bound dev)
nmap -sS -p- <host>

# TLS
testssl.sh https://pharma.yourdomain.com
```

---

## Minimum gate before prod (non-negotiable)

P0 **§1–§4** fully pass **+** §7 (no secrets/debug) **+** §11 (nmap shows only 443
public, headers+CSP set, non-root) **+** §12 (no critical/high CVEs) **+** §0 DB-role
remediated. Everything else: strongly recommended.

> For an agentic app on pharmacy data, budget a **third-party pentest** focused on
> §1 (store-scope boundary) and §2 (prompt injection) — that's where a real
> attacker spends time and where self-testing has blind spots.
