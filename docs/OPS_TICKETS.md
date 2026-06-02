# Ops Ticket Prep — Group K8S Deployment

Ready-to-paste tickets for Group IT, Infrastructure, and BI Admin teams to
unblock the five operational gaps preventing Dash production deployment.

Each ticket is structured for direct paste into Jira / ServiceNow / Remedy:

- **Title** — short, copy as the ticket summary.
- **Recipient** — which Group team owns it.
- **Body** — paste-ready ticket description; only `<placeholder>` values need
  to be filled in by the requester (Group-specific values they already know).
- **Expected SLA** — typical turnaround based on Group standards.
- **Follow-up criteria** — verification steps the Dash team will run before
  marking the ticket "Done."

Cross-references (kept inside the Dash repo, not pasted into the ticket):

- `docs/DEPLOYMENT_K8S.md` — manifest order, Helm values, image build steps.
- `docs/SECURITY.md` — secret handling, RBAC, network policy rationale.
- `docs/ARCHITECTURE.md` — component diagram, data flow, external dependencies.

Placeholder convention used throughout this document:

- `<domain>` — Group's public domain (e.g. `corp.example.com`).
- `<tenant>` — Azure AD tenant GUID.
- `<workspace>` — Microsoft Fabric / Power BI workspace name.
- `<cluster>` — name of the Group production K8S cluster.
- `<registry>` — Group container registry hostname.
- `<placeholder>` — anything else the Group team must supply.

---

## TICKET 1 — Azure AD App Registration

### Recipient: Group IT / Identity team

### Title
Create Azure AD app registration: dash-prod (data agent platform)

### Body
We need an Azure AD app registration for the Dash data agent platform.

**App name:** dash-prod
**Account type:** Single tenant (Group only)
**Redirect URIs (Web platform):**
  - https://dash.<domain>/api/auth/sso/callback
  - https://dash.<domain>/api/sharepoint/callback
  - https://dash.<domain>/api/onedrive/callback
  - https://dash.<domain>/api/gdrive/callback (skip if Google OAuth used separately)

**API permissions (delegated):**
  - openid
  - profile
  - email
  - User.Read
  - Group.Read.All  (for role mapping)
  - Files.Read
  - Files.Read.All
  - Sites.Read.All
  - offline_access

**API permissions (application — for service principal):**
  - https://analysis.windows.net/powerbi/api/Tenant.Read.All
  - Microsoft Graph User.Read.All

**Client secret:** Generate one with 24-month expiration. Note expiration
date in Group secrets manager.

**Grant admin consent:** Required for application permissions above.

**Deliverables to dash team:**
  - Tenant ID (GUID)
  - Application (Client) ID (GUID)
  - Client Secret (one-time display)
  - Confirmation: admin consent granted

### Expected SLA
2-3 business days for approval + creation.

### Follow-up
- Verify dash team can authenticate via Postman: POST to
  `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` with
  `client_credentials` grant.
- Verify token contains expected scopes.
- Verify ID token returned for delegated flow includes `preferred_username`
  and `groups` claims.

---

## TICKET 2 — K8S Namespace + Quotas

### Recipient: Group Infrastructure / Platform team

### Title
Provision K8S namespace 'dash' for data agent platform

### Body
Provision a Kubernetes namespace for Dash production deployment.

**Cluster:** <name of Group prod K8S cluster>
**Namespace:** dash
**Environment:** production

**Resource quotas:**
  - CPU requests: 32 cores
  - CPU limits: 64 cores
  - Memory requests: 64 GiB
  - Memory limits: 128 GiB
  - PersistentVolumeClaims: 10
  - Storage: 200 GiB total
  - Pods: 50

**Network requirements:**
  - Ingress: from web zone (https only, port 443)
  - Egress (allowed):
      * Internet: api.openrouter.ai (LLM)
      * Microsoft Graph: graph.microsoft.com (port 443)
      * Microsoft Fabric: *.fabric.microsoft.com (port 443)
      * Power BI XMLA: *.analysis.windows.net (port 443)
      * Google APIs: *.googleapis.com (port 443) — skip if no Google
      * Tavily / Perplexity / Brave search: respective domains (optional)
      * FRED API: api.stlouisfed.org (optional)
      * Azure AD: login.microsoftonline.com (port 443)
  - Egress (denied): all others (default-deny)

**Storage classes needed:**
  - RWO 20 GiB block storage (for dash-db Postgres)
    Confirm class name (e.g. managed-premium / gp3-csi / rook-block)
  - RWX 50 GiB shared storage (for knowledge volume across replicas)
    Confirm class name (e.g. azurefile-csi / efs / nfs-client)

**Service account:** dash-sa with namespace-scoped permissions
(no cluster admin needed).

**Network policies:** see deployment manifests at `k8s/80-networkpolicy.yaml`
(provided by dash team on request).

### Expected SLA
3-5 business days.

### Follow-up
- `kubectl get ns dash` returns Active.
- `kubectl describe quota -n dash` shows requested limits.
- Confirm storage class names provided to dash team in writing so PVC
  manifests can pin them.
- Confirm egress allowlist deployed (test pod with `curl api.openrouter.ai`).

---

## TICKET 3 — Container Registry Access

### Recipient: Group DevOps / Platform team

### Title
Container registry namespace + push credentials for dash images

### Body
Need container registry write access for Dash CI/CD.

**Registry:** <Harbor / ACR / ECR / GCR — Group standard>
**Project / namespace:** dash
**Required access:**
  - Push: dash team CI/CD service account
  - Pull: K8S cluster service account (dash-sa) + dash-deploy

**Images to push (initial):**
  - registry.<domain>/dash:v1.1.0  (~700 MB)
  - registry.<domain>/dash-ml:v1.1.0  (~900 MB)

**Image scanning:** Trivy CI before promotion (block on Critical/High).

**Lifecycle policy:**
  - Keep last 10 production tags
  - Keep last 30 staging tags
  - Auto-prune everything older than 90 days

**Deliverables:**
  - Registry URL
  - Push credentials (service account + token, in Group secrets)
  - Pull secret yaml for K8S
    (`kubectl create secret docker-registry dash-pull --docker-server=...`)

### Expected SLA
1-2 business days.

### Follow-up
- Verify dash team can `docker push` test image.
- Verify K8S can pull image
  (`kubectl run probe --image=registry.<domain>/dash:v1.1.0 -n dash`).
- Confirm Trivy results visible in registry UI for pushed images.

---

## TICKET 4 — Microsoft Fabric Service Principal Permissions

### Recipient: BI / Fabric Admin

### Title
Grant dash-prod Service Principal access to Fabric workspace + XMLA

### Body
The Dash data agent connects to a Fabric workspace via Service Principal
to read schema, run T-SQL, and pull DAX measures via XMLA.

**Service Principal:** dash-prod (see Ticket 1, app id: <to be provided>)
**Workspace name:** <Group Fabric workspace, e.g. cfc-prod>
**Permissions needed (workspace-level):**
  - Contributor (or Member if more restrictive)
  - XMLA endpoint access enabled

**Tenant settings to enable** (Power BI admin portal):
  - "Service principals can use Fabric APIs" → Enabled (specific group OK)
  - "Service principals can access XMLA endpoints (read-only)" → Enabled
  - Add dash-prod service principal to allowed group

**Verification steps:**
  - Run from Postman or curl with SP token:
    `GET https://api.fabric.microsoft.com/v1/workspaces`
    Should return workspace list including target workspace.
  - Connect via DAX Studio with SP credentials → workspace visible.

### Expected SLA
1-2 business days.

### Follow-up
- Dash team confirms FabricProvider connection succeeds.
- XMLA puller pulls measures from semantic model.
- Audit log shows SP read activity, not blocked by conditional access.

---

## TICKET 5 — Sealed Secrets / Vault Setup

### Recipient: Group DevOps / Security team

### Title
Provision secret storage for dash-prod (sealed-secrets or Vault)

### Body
Need encrypted secret management for Dash deployment.

**Required secrets** (in Group standard secret store):
  - OPENROUTER_API_KEY
  - DB_PASS
  - SUPER_ADMIN_PASS
  - MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID  (from Ticket 1)
  - GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET  (if Google connector used)
  - KEYCLOAK_CLIENT_SECRET  (for SSO)
  - SLACK_LEARNING_WEBHOOK  (optional)
  - TAVILY_API_KEY, BRAVE_API_KEY, PERPLEXITY_API_KEY  (optional)
  - FRED_API_KEY, ALPHA_VANTAGE_API_KEY  (optional)
  - ADMIN_API_TOKEN  (for K8S CronJob → API auth)

**Method preference:**
  1. Sealed Secrets (Bitnami) — encrypted yaml in git.
  2. External Secrets Operator + Vault.
  3. CSI Secrets Store + Azure Key Vault / AWS Secrets Manager.

**Rotation policy:**
  - 90-day rotation for API keys.
  - 30-day rotation for DB_PASS.
  - 365-day rotation for OAuth client secrets (Azure AD reissue cycle).

**Deliverables:**
  - Confirmation method chosen (sealed-secrets / ESO / CSI).
  - Public key (sealed-secrets) or Vault path + role for dash team.
  - Initial secret values populated (dash team will provide values securely).

### Expected SLA
2-3 business days for setup + initial population.

### Follow-up
- `kubectl get secret dash-secrets -n dash` exists and contains all keys.
- Sync verified: rotate one secret, confirm pod sees new value within
  expected refresh window.
- Audit log shows secret access only by dash-sa.

---

## TICKET 6 — DNS + TLS Certificate

### Recipient: Group DNS / Security team

### Title
Create DNS CNAME + TLS cert for dash.<domain>

### Body
**Hostname:** dash.<domain>
**Type:** CNAME → cluster ingress LB hostname (provided after K8S deploy)
**TLS cert:** Use cert-manager + Group ClusterIssuer (Let's Encrypt OR
internal CA).

**Certificate annotation on Ingress:**
  `cert-manager.io/cluster-issuer: <name from Group infra>`

**Validation:**
  - HTTPS GET `dash.<domain>/health` returns `{"status":"ok"}`.
  - Certificate chain verifies in browser (no warnings).
  - HSTS header present (`Strict-Transport-Security: max-age=...`).

**Deliverables:**
  - DNS record created (TTL ≤ 300s during cutover).
  - ClusterIssuer name to be referenced in Ingress.
  - Confirmation cert-manager has rights to create Certificate resources
    in the dash namespace.

### Expected SLA
1 business day for DNS, plus cert-manager auto-issues TLS within 1 hour.

### Follow-up
- `dig dash.<domain>` resolves to ingress LB.
- `curl -vkI https://dash.<domain>/health` shows valid cert + 200 OK.
- Cert renewal cron tested (cert-manager logs).

---

## PRE-FLIGHT CHECKLIST (all tickets resolved)

Before running `helm install dash`:

- [ ] Ticket 1: Azure AD app reg created, tenant_id + client_id +
       client_secret in secret store.
- [ ] Ticket 2: K8S namespace 'dash' active, quotas applied,
       storage class names confirmed.
- [ ] Ticket 3: Registry credentials valid, images pushable.
- [ ] Ticket 4: Fabric SP has workspace access, XMLA enabled.
- [ ] Ticket 5: All secrets in sealed-secrets / Vault, sync verified.
- [ ] Ticket 6: DNS CNAME live, TLS cert auto-issuing.
- [ ] dash-secrets Secret created in namespace.
- [ ] dash-config ConfigMap created with non-secret env vars.
- [ ] PVC storage classes match actual cluster classes.
- [ ] Network policies tested (pod can reach OpenRouter, Fabric, etc.).
- [ ] Image vulnerability scan: 0 Critical, 0 High.
- [ ] Database password meets Group complexity policy.
- [ ] CORS_ORIGINS set to actual domain (not `*`).
- [ ] AGNO_DEBUG=False set.
- [ ] Rate limit (RATE_LIMIT) appropriate for production.
- [ ] Trivy CI scan passing.
- [ ] Backup policy confirmed for dash-db PVC (snapshot schedule + retention).
- [ ] On-call rotation defined and PagerDuty / OpsGenie configured.
- [ ] Runbook reviewed by SRE on-call lead.

---

## DEPLOY STEPS (post-tickets)

```bash
# 1. Apply sealed secrets (or wait for ESO sync)
kubectl apply -f sealed-secrets/dash-secrets.yaml

# 2. Apply manifests in order
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-configmap.yaml
kubectl apply -f k8s/10-db-pvc.yaml
kubectl apply -f k8s/10-db-statefulset.yaml
kubectl apply -f k8s/10-db-service.yaml

# Wait for db ready
kubectl wait --for=condition=Ready pod/dash-db-0 -n dash --timeout=300s

# 3. Run migrations
kubectl exec -n dash dash-db-0 -- bash -c \
  "for f in /docker-entrypoint-initdb.d/migrations/*.sql; do
     psql -U ai -d ai -f \$f
   done"

# 4. Apply pgbouncer + api + ml-worker
kubectl apply -f k8s/20-pgbouncer-deploy.yaml
kubectl apply -f k8s/20-pgbouncer-svc.yaml
kubectl apply -f k8s/30-knowledge-pvc.yaml
kubectl apply -f k8s/30-api-deploy.yaml
kubectl apply -f k8s/30-api-svc.yaml
kubectl apply -f k8s/30-api-hpa.yaml
kubectl apply -f k8s/40-ml-worker-deploy.yaml

# 5. Apply ingress + network policy + RBAC
kubectl apply -f k8s/60-ingress.yaml
kubectl apply -f k8s/80-networkpolicy.yaml
kubectl apply -f k8s/90-rbac.yaml
kubectl apply -f k8s/90-serviceaccount.yaml

# 6. Apply CronJobs
kubectl apply -f k8s/70-learning-cronjob.yaml
kubectl apply -f k8s/70-learning-canary-cronjob.yaml
kubectl apply -f k8s/70-decay-cronjob.yaml

# OR — Helm one-shot (preferred):
helm install dash ./helm/dash \
  --namespace dash --create-namespace \
  --values ./helm/dash/values-prod.yaml \
  --set image.tag=v1.1.0 \
  --set image.repository=registry.<domain>/dash
```

---

## SMOKE TEST PLAYBOOK

```bash
# 1. Health
curl https://dash.<domain>/health
# Expected: {"status":"ok","db":"connected","ml_retrain":{...}}

# 2. Login as super-admin (from secret)
TOKEN=$(curl -X POST https://dash.<domain>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<from secret>"}' | jq -r .token)

# 3. Create test project
curl -X POST https://dash.<domain>/api/projects \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"slug":"smoke-test","name":"Smoke Test"}'

# 4. Connect a Postgres source (provide test DB)
curl -X POST https://dash.<domain>/api/connectors/connect \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"project_slug":"smoke-test","db_type":"postgresql",
       "host":"...","port":5432,"username":"...","password":"...","database":"..."}'

# 5. Trigger training
curl -X POST https://dash.<domain>/api/connectors/sources/1/train \
  -H "Authorization: Bearer $TOKEN"

# 6. Run a chat query
curl -X POST https://dash.<domain>/api/projects/smoke-test/chat \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message":"top 5 customers by revenue"}'

# 7. Run a learning cycle
curl -X POST https://dash.<domain>/api/learning/cycle/smoke-test \
  -H "Authorization: Bearer $TOKEN"

# 8. Verify metrics
curl https://dash.<domain>/api/learning/iq/smoke-test \
  -H "Authorization: Bearer $TOKEN"
# Expected: agent_iq > 0
```

Acceptance criteria for smoke test:

- All 8 calls return 2xx.
- `/health` shows db connected and ml_retrain section populated.
- Chat call returns a SQL plan + tabular result, no 5xx in API logs.
- Learning cycle returns a `cycle_id` and writes a row to
  `learning_cycle_runs` (verify with psql).

---

## FIRST 24-HOUR RUNBOOK

After deploy:

| Time | Action | Owner |
|------|--------|-------|
| T+0  | Helm install | DevOps |
| T+5min | Health check, smoke test | Dash team |
| T+30min | First user login + tour | Customer success |
| T+1h | Connect first real source | Customer + Dash |
| T+2h | First training run | Customer |
| T+4h | First chat queries | Customer |
| T+8h | Monitor logs for errors | Dash team |
| T+12h | First proactive insights | Auto |
| T+24h | First scheduled learning cycle (04:00 UTC) | Auto-triggered |

**Monitoring during 24h:**

- `kubectl logs -n dash deploy/dash-api --tail=50` (continuous).
- `/health` endpoint every 5 min.
- `/api/learning/scheduler/state` hourly.
- DB connection count:
  `SELECT count(*), state FROM pg_stat_activity GROUP BY state;`
- HPA state: `kubectl get hpa -n dash`.
- PVC usage: `kubectl exec dash-db-0 -n dash -- df -h /var/lib/postgresql/data`.

**Escalation matrix:**

- Pod restart loop → check logs + secrets → page DevOps.
- DB connection exhausted → restart pgbouncer.
- Login fails → check Keycloak / Azure AD federation.
- Slow queries → check `statement_timeout` + `pg_stat_statements`.
- LLM 429 / 5xx from OpenRouter → check API key quota; flip to fallback model.
- XMLA puller failing → check Fabric SP token, retry; fall back to T-SQL.

---

## ROLLBACK PROCEDURE

```bash
# Helm rollback to previous version
helm rollback dash --namespace dash
helm history dash --namespace dash

# OR manual image revert
kubectl set image deployment/dash-api dash-api=registry.<domain>/dash:vPREVIOUS -n dash
kubectl rollout status deployment/dash-api -n dash

# DB rollback
# All migrations have IF NOT EXISTS — safe.
# To DROP new tables/cols, see ROLLBACK section in each migration file.
```

Rollback decision tree:

1. **Code-only regression** (chat broken, ML loop broken) → `helm rollback`.
2. **Schema regression** (new migration breaks reads) → revert image AND
   apply migration's documented `ROLLBACK` block.
3. **Config regression** (env var typo) → patch ConfigMap, restart deploy:
   `kubectl rollout restart deployment/dash-api -n dash`.
4. **Secret regression** (rotated key invalid) → revert secret in store,
   resync sealed-secrets / ESO, restart pods.

---

## CONTACTS

| Role | Contact |
|------|---------|
| Dash team | <slack channel> |
| Group IT | <Identity admin email> |
| Group Infra | <Platform team email> |
| BI Admin | <Power BI admin email> |
| On-call | <PagerDuty / OpsGenie> |
| Security / IR | <security incident email> |
| Change advisory board | <CAB email / queue> |

> All `<...>` entries above are intentionally blank — fill in with Group
> internal contact details before circulating this document outside the
> Dash team.

---

## STATUS TRACKING

| Ticket | Submitted | ETA | Owner | Status |
|--------|-----------|-----|-------|--------|
| 1 — Azure AD app reg | | | | |
| 2 — K8S namespace | | | | |
| 3 — Registry access | | | | |
| 4 — Fabric SP | | | | |
| 5 — Sealed secrets | | | | |
| 6 — DNS + TLS | | | | |

---

## APPENDIX A — Dependency Graph Between Tickets

```
T1 (Azure AD) ──────┬─────► T4 (Fabric SP)        ──┐
                    │                                ├─► Helm install
T2 (Namespace) ─────┼─► T6 (DNS+TLS) ───────────────┤
                    │                                │
T3 (Registry)  ─────┘                                │
                                                     │
T5 (Secrets)   ──────────────────────────────────────┘
```

- T4 requires T1 (needs the SP app id).
- T6 requires T2 (needs ingress LB hostname from cluster).
- T5 can run in parallel with T1–T3 but must finish before deploy.
- Helm install requires all six.

---

## APPENDIX B — Risk Register (Ops Gaps)

| # | Gap | Blocked Capability | Workaround | Permanent Fix |
|---|-----|--------------------|------------|---------------|
| 1 | No Azure AD app reg | SSO, SharePoint/OneDrive connectors, Fabric SP | local admin login only | Ticket 1 |
| 2 | No K8S namespace | Entire prod deploy | none | Ticket 2 |
| 3 | No registry access | Image push / pull | manual `docker save` + scp | Ticket 3 |
| 4 | No Fabric workspace grant | Fabric provider, XMLA measures | static CSV exports | Ticket 4 |
| 5 | No secret store | Secrets in plaintext yaml (unsafe) | env vars on host (unsafe) | Ticket 5 |
| 6 | No DNS / TLS | Public access | port-forward only | Ticket 6 |

---

## APPENDIX C — Communication Templates

### Status update to customer (weekly)

> Subject: Dash deployment — week N status
>
> Tickets resolved: <list>. Open: <list with ETA>. Risks: <none/list>.
> Next milestone: <e.g. dev environment available by YYYY-MM-DD>.
> Action requested: <if any>.

### Escalation (ticket past SLA)

> Subject: ESCALATION — Ticket <id> overdue, blocking dash-prod deploy
>
> Ticket <id> ("<title>") was submitted on <date> with SLA <n> business days.
> It is now <m> days overdue. This blocks <capability> for the dash-prod
> launch on <date>. Requesting immediate attention from <team lead>.

---

## APPENDIX D — Acceptance Sign-off

When all tickets are closed and pre-flight checklist passes, the following
sign-offs are required before flipping DNS to public:

- [ ] Dash team lead — code + migrations verified.
- [ ] Group SRE on-call — runbook reviewed, monitoring in place.
- [ ] Group Security — Trivy clean, secrets in Vault, network policy active.
- [ ] BI Admin — Fabric SP working, no audit findings.
- [ ] Identity team — SSO login round-trip tested with real user.
- [ ] Customer sponsor — smoke test acknowledged, willing to onboard.

Once all six signatures are obtained, proceed with the FIRST 24-HOUR
RUNBOOK.
