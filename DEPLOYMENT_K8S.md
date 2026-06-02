# Dash — Kubernetes Deployment Runbook

> **⚠ Advanced / enterprise reference.** Dash runs fine for typical loads (≈200 users, single tenant) on a single Docker Compose host — see `DEPLOYMENT.md`. The K8s/Helm/multi-cloud setup below is optional and only needed for multi-replica horizontal scaling, which most deployments never require. Not exercised in the default deploy path.

> Migration runbook for moving Dash from local Docker Compose (`compose.yaml`) to a production Kubernetes cluster. Style matches `DEPLOYMENT.md` / `DEPLOYMENT_AWS.md` / `DEPLOYMENT_GCP.md`.
>
> Audience: Dash platform engineers + your IT / platform team.
> Scope: production deploy, smoke tests, rollback, day-2 ops.

Substitute `${ORG_NAME}`, `${REGISTRY}`, and `${DOMAIN}` for your environment throughout.

---

## 1. Pre-flight Checklist (do BEFORE any deploy)

Every item in this section must be done before running anything in Section 2.

### 1.1 Azure AD App Registration

Required for SharePoint, OneDrive and (optionally) corporate SSO callbacks served by Dash.

- Create app registration named `dash-prod` in your tenant.
- Redirect URIs (Web platform):
  - `https://dash.example.com/api/sharepoint/callback`
  - `https://dash.example.com/api/onedrive/callback`
  - `https://dash.example.com/api/gdrive/callback`
  - `https://dash.example.com/api/auth/callback`
- API permissions (Microsoft Graph, **delegated**):
  - `Files.Read`
  - `Files.Read.All`
  - `Sites.Read.All`
  - `offline_access`
  - `User.Read`
- Click **Grant admin consent** (your directory admin).
- Generate a client secret with **24-month** expiry, store in Vault under `secret/dash/prod/ms_client_secret`.
- Note `client_id` and `tenant_id` — they go into the `dash-secrets` K8S Secret.

### 1.2 K8S Namespace Request

Submit a ticket to your platform team requesting:

- Namespace name: `dash`
- Resource quotas:
  - `requests.cpu: 32`
  - `requests.memory: 64Gi`
  - `requests.storage: 200Gi`
- Network requirements:
  - Ingress allowed from the `web` zone via the shared `nginx-ingress` controller.
  - Egress to public internet on `:443` (OpenRouter, Microsoft Graph, Google APIs).
  - Egress to Microsoft Fabric SQL endpoints (`*.fabric.microsoft.com:1433`).

### 1.3 Secrets Management

Use **sealed-secrets** OR HashiCorp **Vault** — whichever is the standard at deploy time.
> TODO: confirm canonical secrets backend with your IT.

Required secret keys (all populated before `kubectl apply`):

| Key | Source | Notes |
|-----|--------|-------|
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys | Required |
| `DB_PASS` | Generated, 32-char random | NOT `ai` |
| `SUPER_ADMIN_PASS` | Generated, 24-char random | First-boot admin |
| `MS_CLIENT_ID` | Section 1.1 | SharePoint connector |
| `MS_CLIENT_SECRET` | Section 1.1 | SharePoint connector |
| `MS_TENANT_ID` | Section 1.1 | SharePoint connector |
| `GOOGLE_CLIENT_ID` | Google Cloud Console | Drive connector |
| `GOOGLE_CLIENT_SECRET` | Google Cloud Console | Drive connector |
| `KEYCLOAK_CLIENT_SECRET` | Keycloak admin | Optional SSO |

**Rotation schedule:** 90-day default for all secrets. Calendar reminder in `#dash-oncall` 14 days before expiry. Microsoft and Google client secrets follow vendor-specific expiry (24 months max).

### 1.4 Container Registry

Push images to your container registry (Harbor / ACR / ECR — whichever is in use).
> TODO: confirm registry hostname with your IT.

Image naming convention:

```
your-registry.example.com/dash/dash:vX.Y.Z      # API + frontend
your-registry.example.com/dash/dash-ml:vX.Y.Z   # ML worker
```

Build + push:

```bash
docker build -t your-registry.example.com/dash/dash:v1.0.0 -f Dockerfile .
docker build -t your-registry.example.com/dash/dash-ml:v1.0.0 -f ml_worker/Dockerfile .
docker push your-registry.example.com/dash/dash:v1.0.0
docker push your-registry.example.com/dash/dash-ml:v1.0.0
```

**Image scanning:** Trivy scan required and must show `0 CRITICAL` before promotion to `prod` repository.

```bash
trivy image --severity CRITICAL,HIGH --exit-code 1 your-registry.example.com/dash/dash:v1.0.0
```

### 1.5 DNS + TLS

- Request CNAME from networking: `dash.example.com` → `<cluster-ingress-lb>.internal`.
  > TODO: ingress LB hostname.
- TLS: cert-manager + ClusterIssuer (Let's Encrypt prod OR internal CA).
- Add to Ingress:
  ```yaml
  annotations:
    cert-manager.io/cluster-issuer: <issuer-name>   # TBD with your IT
  spec:
    tls:
      - hosts: [dash.example.com]
        secretName: dash-tls
  ```

### 1.6 Storage Classes

| Volume | Mode | Size | Purpose |
|--------|------|------|---------|
| `dash-db` PVC | RWO | 20Gi | PostgreSQL data (block) |
| `knowledge` PVC | RWX | 50Gi | Per-project knowledge artifacts (multi-replica API) |
| `caddy-data` PVC | RWO | 1Gi | Caddy certs/state |

- Block storage class (RWO) for the DB. Likely `managed-premium` or `gp3`.
  > TODO: storageClassName.
- RWX for knowledge — NFS or Azure Files (`azurefile-csi-premium`) so multiple `dash-api` pods can share.
  > TODO: RWX storageClassName.
- Verify availability:
  ```bash
  kubectl get storageclass
  ```

### 1.7 Network Policy

- Default `deny-all` ingress in namespace.
- Allow:
  - `ingress-nginx` namespace → `dash-api:8000`
  - `dash-api` ↔ `dash-pgbouncer:5432`
  - `dash-pgbouncer` ↔ `dash-db:5432`
  - `dash-ml` → `dash-pgbouncer:5432`
- Egress allow-list (TCP/443):
  - `*.openrouter.ai`
  - `*.fabric.microsoft.com`
  - `graph.microsoft.com`
  - `*.googleapis.com`
  - `*.cohere.com` (reranker via OpenRouter, defensive)
- Policy file lives at: `k8s/80-networkpolicy.yaml`.

### 1.8 RBAC + Service Account

- ServiceAccount `dash-sa` in namespace `dash`.
- Namespace-scoped Role only — **no cluster-admin**.
- Bind via RoleBinding; your IT must approve binding before `kubectl apply`.
- Manifest: `k8s/90-rbac.yaml`.

### 1.9 Backup Policy

- **Database:** Velero (volume snapshot) OR `pgBackRest` sidecar.
  - Daily incremental, weekly full, **30-day retention**.
  - Verify restore quarterly.
- **Knowledge volume:** filesystem snapshot daily, weekly tier-down to cold storage.
- RTO: 1 hour. RPO: 15 minutes.

### 1.10 Observability Hookup

- Prometheus scrape annotation on `dash-api` Service:
  ```yaml
  prometheus.io/scrape: "true"
  prometheus.io/port: "8000"
  prometheus.io/path: "/metrics"
  ```
- Logs: Loki (DaemonSet collector). JSON logs from FastAPI honored.
- Grafana dashboards: import `monitoring/grafana-dash.json` (TODO: create file with `dash-overview`, `dash-cost`, `dash-pii-audit`).
- PagerDuty (or OpsGenie — your standard) integration for alerts in Section 5.

---

## 2. Migration Steps

### 2.1 Apply manifests in order

```bash
# 0. Set context
export KCTX=<NAMESPACE>          # e.g. dash-prod
kubectl config use-context $KCTX

# 1. Namespace + base config
kubectl create ns dash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-configmap.yaml
kubectl apply -f k8s/02-secret.yaml          # AFTER editing or sealed-secret unseal

# 2. Database
kubectl apply -f k8s/10-db-pvc.yaml
kubectl apply -f k8s/10-db-statefulset.yaml
kubectl apply -f k8s/10-db-service.yaml
kubectl wait --for=condition=Ready pod/dash-db-0 -n dash --timeout=300s

# 3. PgBouncer
kubectl apply -f k8s/20-pgbouncer-deploy.yaml
kubectl apply -f k8s/20-pgbouncer-svc.yaml
kubectl wait --for=condition=Available deploy/dash-pgbouncer -n dash --timeout=120s

# 4. API + ML worker
kubectl apply -f k8s/30-knowledge-pvc.yaml
kubectl apply -f k8s/30-api-deploy.yaml
kubectl apply -f k8s/30-api-svc.yaml
kubectl apply -f k8s/30-api-hpa.yaml
kubectl apply -f k8s/40-ml-worker-deploy.yaml

# 5. Edge + policies
kubectl apply -f k8s/60-caddy-deploy.yaml
kubectl apply -f k8s/60-ingress.yaml
kubectl apply -f k8s/80-networkpolicy.yaml
kubectl apply -f k8s/90-rbac.yaml

# 6. Verify
kubectl get all -n dash
```

### 2.2 Helm alternative

Equivalent to Section 2.1, single command:

```bash
helm install dash ./helm/dash \
  --namespace dash --create-namespace \
  --values helm/dash/values-prod.yaml \
  --set image.tag=v1.0.0 \
  --set image.repository=your-registry.example.com/dash/dash \
  --set ml.image.repository=your-registry.example.com/dash/dash-ml
```

> See `helm/dash/values-prod.yaml` for production overrides (storage class, registry, ingress host) and `helm/dash/values-dev.yaml` for a lighter dev configuration.

### 2.3 Run DB migrations

DB schema bootstraps on first API request, but provider-layer SQL must be applied explicitly:

```bash
kubectl exec -n dash dash-db-0 -- psql -U ai -d ai \
  -f /docker-entrypoint-initdb.d/001_provider_layer.sql
```

If the file is not mounted into the StatefulSet image, copy it in first:

```bash
kubectl cp db/001_provider_layer.sql dash/dash-db-0:/tmp/001_provider_layer.sql
kubectl exec -n dash dash-db-0 -- psql -U ai -d ai -f /tmp/001_provider_layer.sql
```

### 2.4 First-boot verification

```bash
kubectl logs -n dash deploy/dash-api --tail=50
kubectl logs -n dash deploy/dash-pgbouncer --tail=20
kubectl logs -n dash deploy/dash-ml --tail=20

curl -sS https://dash.example.com/health | jq .
# Expected:
# {
#   "status": "ok",
#   "db": "connected",
#   "ml_retrain": {"last_run": "...", "last_error": null}
# }
```

---

## 3. Smoke Tests (post-deploy)

Use a throwaway admin token (`<TOKEN>`). Replace `<NAMESPACE>` if running curls from inside the cluster.

```bash
# 1. Login
curl -X POST https://dash.example.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<SUPER_ADMIN_PASS>"}'
# → returns {"token": "..."}
export TOKEN=<paste-from-above>

# 2. Create test project
curl -X POST https://dash.example.com/api/projects \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"slug":"smoke-test","name":"Smoke"}'

# 3. Connect a Postgres source (read-only test DB)
curl -X POST https://dash.example.com/api/connectors/connect \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "project_slug": "smoke-test",
        "source_type": "postgres",
        "host": "<TEST_DB_HOST>",
        "port": 5432,
        "database": "test",
        "user": "readonly",
        "password": "<TEST_DB_PASS>",
        "tables": ["public.orders"]
      }'

# 4. Trigger training
curl -X POST https://dash.example.com/api/connectors/sources/1/train \
  -H "Authorization: Bearer $TOKEN"

# 5. Run a chat query
curl -X POST https://dash.example.com/api/projects/smoke-test/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"top 5 customers"}'

# 6. Verify cost logged
kubectl exec -n dash dash-db-0 -- psql -U ai -d ai \
  -c "SELECT created_at, total_cost FROM dash_audit_log ORDER BY id DESC LIMIT 5;"
```

All six steps must succeed with HTTP 2xx and step 6 must show a non-null `total_cost` row.

---

## 4. Rollback Procedure

### 4.1 Helm rollback

```bash
helm history dash --namespace dash                 # list versions
helm rollback dash <REVISION> --namespace dash     # roll to chosen revision
kubectl rollout status deploy/dash-api -n dash
```

### 4.2 Manual rollback (no Helm)

```bash
kubectl set image deployment/dash-api \
  dash-api=your-registry.example.com/dash/dash:<vPREVIOUS> -n dash
kubectl rollout status deployment/dash-api -n dash

# ML worker
kubectl set image deployment/dash-ml \
  dash-ml=your-registry.example.com/dash/dash-ml:<vPREVIOUS> -n dash
kubectl rollout status deployment/dash-ml -n dash
```

### 4.3 Database rollback

- Restore from Velero snapshot OR `pg_dump` backup.
- **RTO 1 hour, RPO 15 min.**
- Restore steps:
  ```bash
  velero restore create dash-db-restore \
    --from-backup dash-db-<DATE> \
    --include-namespaces dash
  ```
- After restore, run Section 2.4 verification before re-routing ingress traffic.

---

## 5. Monitoring Hookup

- **Metrics:** `/metrics` endpoint on `dash-api` Service, scraped by Prometheus.
- **Dashboards (Grafana):** `dash-overview`, `dash-cost`, `dash-pii-audit`.
- **Alerts:**
  - `api-down` — `up{job="dash-api"} == 0` for 2 min → PagerDuty P1
  - `db-conn-exhaustion` — PgBouncer `cl_waiting > 50` → P2
  - `ml-retrain-failed` — `/health.ml_retrain.last_error != null` → P2
  - `pii-block-rate-spike` — `pii_blocked_total` rate 5x baseline → P3
- **SLOs:**
  - chat p95 < 8s (FAST mode)
  - training step < 120s
  - ingest < 5min for 100MB

---

## 6. Day-2 Operations

### 6.1 Adding a new project

- UI: Settings → Create Project.
- Bulk:
  ```bash
  curl -X POST https://dash.example.com/api/projects \
    -H "Authorization: Bearer <ADMIN_TOKEN>" \
    -d '{"slug":"<SLUG>","name":"<NAME>"}'
  ```

### 6.2 Adding a new data source

- Settings → SOURCES tab → wizard. SharePoint / Google Drive / PostgreSQL / MySQL / Microsoft Fabric.

### 6.3 Scaling

- API HPA auto-scales `dash-api` 3–10 pods at CPU 70%:
  ```bash
  kubectl get hpa -n dash
  ```
- DB: vertical scale only — single replica StatefulSet. Edit resources block:
  ```bash
  kubectl edit statefulset dash-db -n dash
  ```
- ML worker: manual replica bump if the `dash_ml_jobs` backlog grows:
  ```bash
  kubectl scale deploy/dash-ml -n dash --replicas=3
  ```

### 6.4 Upgrade

```bash
helm upgrade dash ./helm/dash -n dash --set image.tag=vX.Y.Z
kubectl rollout status deployment/dash-api -n dash
kubectl rollout status deployment/dash-ml  -n dash
```

Run Section 3 smoke tests after every upgrade.

---

## 7. Compliance + Audit

- Audit log queryable via the `dash_audit_log` table (every chat, login, project op).
- Per-source PII activity in Settings → INTEGRATIONS tab.
- Your IT can join container stdout into Loki/ELK via the standard log stream.
- **Retention:** 90 days hot (Loki), 7 years cold (object storage tier-down).
  > TODO: confirm long-term cold retention with your compliance team.

---

## 8. Known Issues + Workarounds

*(empty — populate after first production deploy)*

---

## 9. Contacts

- **Dash team:** `#dash-eng` (Slack)
- **Infra:** your platform team — primary IT contact
  > TODO: named contact + email.
- **On-call:** PagerDuty schedule `dash-oncall` (rotation Mon-Mon)
- **Compliance:** your compliance team for retention / audit questions

---

## 10. Background Daemons — Why Split from API Pods

### Why split daemons from API

The API runs at `replicas=3` (HPA 3-10). In-process background daemons
(`cost_guard`, `auto-campaign`, dream cycles, ab-revert, benchmark sync,
ontology cluster, sim-cleanup, etc) are gated on `WORKER_RANK=0` in
`scripts/gunicorn_conf.py`. That gate works fine for a single multi-worker
pod (Compose) — only one worker per pod fires daemons. Under K8s multi-replica
deployment it breaks: each pod has its own `WORKER_RANK=0` worker → 3 API
pods × 1 daemon-host each = 3 duplicate cron fires + race conditions on
shared state (auto-campaign drafts duplicated, ab-revert rescoring twice, etc).

Fix: a dedicated `dash-daemon` Deployment with `replicas=1` + `strategy=Recreate`.
API pods get `DAEMONS_DISABLED=1` set in env so their `WORKER_RANK=0` workers
skip the daemon spawn at lifespan startup. The daemon pod has `WORKERS=1` +
`WORKER_RANK=0` + no `DAEMONS_DISABLED` so daemons spawn exactly once cluster-wide.

### How to deploy

`helm install` command is unchanged. The `daemon-deployment.yaml` template
auto-spawns whenever `daemon.enabled=true` (default in `values.yaml` and
`values-prod.yaml`; explicitly `false` in `values-dev.yaml`).

```bash
helm install dash ./helm/dash -f helm/dash/values-prod.yaml -n dash
# → creates dash-api (3 replicas, DAEMONS_DISABLED=1)
# → creates dash-daemon (1 replica, daemons active)
```

For raw manifests, apply `k8s/35-daemon-deploy.yaml` alongside the existing
`k8s/30-api-deploy.yaml`. Both have been updated:

- `k8s/30-api-deploy.yaml` injects `DAEMONS_DISABLED=1`
- `k8s/35-daemon-deploy.yaml` is the new singleton deployment

### Verifying

```bash
kubectl get pods -l app=dash-daemon -n dash
# expect exactly 1 Running pod

kubectl exec -n dash deploy/dash-api -- \
  curl -s http://localhost:8000/api/health/daemons | jq
# daemons_should_run_on_this_worker: false

kubectl exec -n dash deploy/dash-daemon -- \
  curl -s http://localhost:8000/api/health/daemons | jq
# daemons_should_run_on_this_worker: true
```

### Failure modes

| Symptom | Cause | Recovery |
|---|---|---|
| Daemon pod CrashLoopBackOff | bad image tag, missing secret, DB unreachable | K8s restarts on backoff; daemons paused until pod recovers. Check `kubectl logs dash-daemon-xxx --previous`. |
| Daemon pod Pending | resource quota / node pressure | bump cluster, or shrink `daemon.resources` via Helm `--set` |
| Auto-campaign / benchmark sync stopped firing | daemon pod down or stuck | `kubectl rollout restart deploy/dash-daemon -n dash` |
| Two daemon pods running | wrong strategy (RollingUpdate) | verify `spec.strategy.type: Recreate`; chart pins this. |

K8s `replicas=1` + `Recreate` strategy ensures recovery. Typical reschedule
30–60s. Daemons enforce their own cadence + dedup gates, so a 1–2 minute
outage is benign; longer outages may miss a specific cron window (e.g.
nightly dream cycle) — re-trigger via super-admin endpoints
(`POST /api/projects/auto-campaign/cycle-all`, etc.).

### Migration from old single-deployment setup

If upgrading from a pre-split deployment where API replicas ran daemons
themselves, do this in order to avoid a window of duplicate work:

1. **Set `DAEMONS_DISABLED=1` on existing API pods FIRST** (either via
   `kubectl set env deploy/dash-api DAEMONS_DISABLED=1` and wait for rolling
   update, or `helm upgrade` with `daemon.enabled=false` first).
2. Verify all API pods report daemons OFF via `/api/health/daemons`.
3. Then spawn the daemon-host deployment (`helm upgrade` with
   `daemon.enabled=true`, or apply `k8s/35-daemon-deploy.yaml`).

Doing it in the other order gives you 4× duplicate cron during the cutover
window — usually harmless (dedup gates handle it) but pollutes logs and
confuses cost attribution.

---
