# Secrets Audit (Track C2)

**Audit date:** 2026-05-25
**Scope:** every secret-shaped string committed to the repo on disk.
**Outcome of this doc:** audit + recommendation only — **NO ROTATION, NO MIGRATION** was performed. The user makes the rotation call.

Stack vendor key (third-party) entries are flagged separately from internal app secrets so the operator can prioritize.

---

## Severity legend

| Tag | Meaning |
|---|---|
| **REAL** | live credential that grants real access to a third-party or internal system |
| **PLACEHOLDER** | example/template value, never a working credential (`your-key-here`, `change-me`, etc.) |
| **TEST FIXTURE** | string crafted to look secret-shaped but only used to test scanners/detectors |
| **DERIVED** | secret derived at runtime from another env (no static storage risk) |
| **CONTAINER ENV** | value lives in `.env` and is consumed via `${VAR}` interpolation — not literally embedded in code |

---

## Findings

### 1. `OPENROUTER_API_KEY` — **REAL**

| where | file:line |
|---|---|
| `.env:2` | `OPENROUTER_API_KEY=sk-or-v1-<REDACTED>` |

> NOTE: live key redacted 2026-05-25; rotated externally.
| `.env.bak:2` | identical value, second copy |

- **Classification:** REAL OpenRouter API key, `sk-or-v1-` prefix is the canonical OpenRouter format.
- **Blast radius:** powers every LLM call in Dash (chat, training, embeddings via `openai/text-embedding-3-small` proxy). Anyone with this key can rack up bills on the owner's OpenRouter account.
- **Recommended migration path** (operator picks one — DO NOT mass-rotate without ops coordination):
  1. **AWS Secrets Manager** if deploying on AWS/ECS/EKS — `--set-secrets="OPENROUTER_API_KEY=dash-openrouter-key:latest"` pattern already documented in `DEPLOYMENT_AWS.md` and `DEPLOYMENT_GCP.md:131`.
  2. **k8s native Secret** (`kubectl create secret generic dash-secrets --from-literal=OPENROUTER_API_KEY=...`) — works for the Helm chart in `helm/dash/`.
  3. **Doppler** if multi-env (dev/stage/prod) — operator wants single dashboard for env diff.
  4. **Infisical** if self-hosted vault preferred (open source, low ops cost).
- **Why a vault, not a tighter file ACL:** `.env` is referenced by Docker Compose `env_file:` / `${OPENROUTER_API_KEY}` interpolation in `compose.yaml`; vault → env injection at container start cleanly replaces the file without changing the consumer surface.

### 2. `SUPER_ADMIN_PASS` — **REAL**

| where | file:line |
|---|---|
| `.env:6` | `SUPER_ADMIN_PASS=<SUPER_ADMIN_PASS>` |
| `.env.bak:6` | identical value |

- **Classification:** REAL — this is the actual super-admin login password used by the demo account. The username `demo` is shipped as the default admin (`SUPER_ADMIN=demo`).
- **Cross-refs:** `README.md:977,1009,1363` document this password publicly. `scripts/test_sim_integration.sh:22` defaults the smoke-test password to `<SUPER_ADMIN_PASS>`. `docs/DASH_VS_A2.md:155` documents it. `CLAUDE.md:1425,3024` references it for live-test scripts.
- **Blast radius:** super-admin == platform owner. Grants access to Command Center (cross-project access, all chat logs, all data sources, all integrations, RLS bypass).
- **Recommended migration path:**
  1. Treat `<SUPER_ADMIN_PASS>` as a **DEV/DEMO-ONLY** credential, never a production credential. Confirm prod deployments override `SUPER_ADMIN_PASS` from a vault.
  2. Production: `AWS SM` / `Doppler` / `k8s Secret` (same matrix as #1).
  3. Add a startup-time refusal in `app/main.py` lifespan: if `RUNTIME_ENV=prd` AND `SUPER_ADMIN_PASS` is `<SUPER_ADMIN_PASS>` or `admin` → log critical + refuse to start. (NOT shipped this session — recommendation only.)

### 3. `PEXELS_API_KEY` — **REAL**

| where | file:line |
|---|---|
| `.env:34` | `PEXELS_API_KEY=<REDACTED>` |
| `.env.bak:27` | identical value |

- **Classification:** REAL — 56-char base62-ish string, matches Pexels free-tier API key shape. Used for stock image lookup in slide/deck generation.
- **Blast radius:** rate-limited free-tier key; abuse caps the demo account's image fetches but no financial liability.
- **Recommended migration path:**
  1. **k8s native Secret** is sufficient — low-sensitivity third-party key.
  2. OR keep in `.env` but ensure `.env` is never committed (gitignore already covers it).

### 4. `CONNECTION_ENCRYPTION_KEY` — **REAL**

| where | file:line |
|---|---|
| `.env:36` | `CONNECTION_ENCRYPTION_KEY=<REDACTED>` |

- **Classification:** REAL — Fernet 44-char urlsafe-base64 key. Used by `dash/connectors/crypto.py` to symmetrically encrypt OAuth refresh tokens, DB passwords, etc., stored in `dash_connections` and `dash_connection_user_tokens` tables.
- **Blast radius:** rotating this key invalidates every stored encrypted credential — all connected data sources (Postgres, MSSQL, Fabric, BigQuery, PowerBI) and OAuth tokens (SharePoint, GDrive, OneDrive) must be re-entered by users.
- **Recommended migration path:**
  1. **AWS Secrets Manager / Doppler / k8s Secret** — same matrix as #1.
  2. **NEVER rotate without a re-encryption migration**: provide a one-shot script that reads with the old key, writes with the new key, then bumps the key in the vault. Pattern: dual-key decrypt during cutover.

### 5. `DB_PASS=ai` — **PLACEHOLDER (production-DANGEROUS default)**

| where | file:line |
|---|---|
| `.env.example:9` | `DB_PASS=ai` |
| `compose.yaml` | `POSTGRES_PASSWORD: ${DB_PASS:-ai}` |
| `evals/cases/security.py:16` | mention in eval test fixture |
| `DEPLOY.md:15` | flagged as production-dangerous default |
| `docs/BACKUP_RESTORE.md:60` | dev-only backup example |

- **Classification:** PLACEHOLDER for the example file (intended) — but `compose.yaml` falls back to `ai` if `DB_PASS` is unset, so a deployer who forgets to set it gets `ai` in production.
- **Blast radius:** full DB compromise if exposed on a public port; PgBouncer is the only thing in front.
- **Recommended migration path:**
  1. Same vault as everything else.
  2. **Plus**: change `${DB_PASS:-ai}` in `compose.yaml` to `${DB_PASS:?DB_PASS env var is required}` so containers refuse to boot without an explicit password. (NOT shipped this session — recommendation only.)

### 6. `KEYCLOAK_CLIENT_SECRET` — **CONTAINER ENV (empty by default)**

| where | file:line |
|---|---|
| `compose.yaml` | `KEYCLOAK_CLIENT_SECRET=${KEYCLOAK_CLIENT_SECRET:-}` |
| `.env.example` | commented-out, no default |

- **Classification:** No live value committed; only consumed via env interpolation. Optional feature (SSO).
- **Recommended migration path:** when an operator deploys with Keycloak SSO, sourced from same vault as other secrets.

### 7. `SLACK_TOKEN` / `SLACK_SIGNING_SECRET` — **CONTAINER ENV (empty by default)**

| where | file:line |
|---|---|
| `compose.yaml` | `SLACK_TOKEN=${SLACK_TOKEN:-}` and `SLACK_SIGNING_SECRET=${SLACK_SIGNING_SECRET:-}` |
| `.env.example` | commented `# SLACK_TOKEN=xoxb-your-token` |

- **Classification:** No live value committed. Optional integration.
- **Recommended migration path:** when configured, vault-injected.

### 8. `MS_CLIENT_SECRET`, `GOOGLE_CLIENT_SECRET` — **CONTAINER ENV (empty by default)**

| where | file:line |
|---|---|
| `compose.yaml` | `MS_CLIENT_SECRET=${MS_CLIENT_SECRET:-}` (and `GOOGLE_CLIENT_SECRET`) |

- **Classification:** No live value committed. Optional integration (SharePoint / Google Drive connectors).
- **Recommended migration path:** vault-injected when configured.

### 9. `JWT_SECRET` — **DERIVED (no static storage)**

| where | file:line |
|---|---|
| `dash/connectors/crypto.py:29` | `jwt_secret = os.environ.get("JWT_SECRET") or "dev-insecure-jwt-secret"` |

- **Classification:** DERIVED — no value committed. Falls back to literal `"dev-insecure-jwt-secret"` if env unset.
- **Blast radius:** if env unset in prod, the fallback `dev-insecure-jwt-secret` is used → `CONNECTION_ENCRYPTION_KEY` (Fernet derived) becomes predictable → all encrypted connection credentials become recoverable.
- **Recommended migration path:**
  1. Provision `JWT_SECRET` in vault as a 32-byte urlsafe string.
  2. Optionally: refuse to start if `JWT_SECRET` is unset OR equals the dev fallback in `RUNTIME_ENV=prd`. (NOT shipped this session — recommendation only.)

### 10. Test-fixture secret-shaped strings — **TEST FIXTURE**

| where | file:line |
|---|---|
| `tests/test_phase6.py:30` | `text = "Here is my key: <REDACTED>"` |
| `tests/test_phase6.py:63` | `text = "use <REDACTED> to call api"` |

- **Classification:** TEST FIXTURE — synthetic OpenAI-prefix string, never works as a real key. Used by Phase-6 secret-leak detector eval suite to verify the audit pipeline flags them.
- **Action:** none required. Standard pattern.

---

## Summary

| Severity | Count |
|---|---|
| REAL secret on disk | 4 (`OPENROUTER_API_KEY`, `SUPER_ADMIN_PASS`, `PEXELS_API_KEY`, `CONNECTION_ENCRYPTION_KEY`) |
| Production-dangerous default | 1 (`DB_PASS=ai` fallback in `compose.yaml`) |
| Empty container-env passthrough | 4 (Keycloak, Slack, MSGraph, Google) |
| Derived w/ insecure fallback | 1 (`JWT_SECRET`) |
| Test fixture (synthetic) | 2 in `tests/test_phase6.py` |
| Placeholder in `.env.example` | many (intentional) |

**Two files contain ALL live secrets:** `.env` and `.env.bak`. Both are already in `.gitignore` (verified `.gitignore:46` `.env`). `.env.bak` is also intended to be ignored — see `.gitignore` update in this session below.

**Operator action items (NOT shipped — recommendations only):**
1. **Pick a vault** (see `docs/SECRETS.md` decision matrix).
2. **Migrate** the 4 REAL secrets to the vault. Order of priority: `OPENROUTER_API_KEY` (financial liability) → `CONNECTION_ENCRYPTION_KEY` (re-encrypt migration) → `SUPER_ADMIN_PASS` (immediately rotate AFTER vault is wired) → `PEXELS_API_KEY` (low priority).
3. **Rotate** the four REAL keys post-migration (each was on disk and possibly checked into a backup before `.gitignore` landed — assume compromised).
4. **Harden `compose.yaml`**: replace `${DB_PASS:-ai}` with `${DB_PASS:?DB_PASS env var is required}`.
5. **Harden `app/main.py` lifespan**: refuse to start if `RUNTIME_ENV=prd` AND `SUPER_ADMIN_PASS in ("<SUPER_ADMIN_PASS>", "admin")` OR `JWT_SECRET == "dev-insecure-jwt-secret"`.
6. **Strip secrets from docs**: `README.md:977,1009,1363`, `docs/DASH_VS_A2.md:155`, `scripts/test_sim_integration.sh:22`, `CLAUDE.md:1425,3024` all reference `demo / <SUPER_ADMIN_PASS>` literally. Replace with `$DASH_TEST_PASS` env var pattern.

This audit was deliberately read-only. The user makes the rotation/migration call after reviewing.
