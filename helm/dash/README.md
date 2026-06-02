# Dash Helm Chart

Helm chart for the multi-tenant Dash data notebook (FastAPI + PgBouncer + PostgreSQL 18 + ML worker + 3 learning CronJobs + optional Caddy).

For background and full env-var reference, see top-level `DEPLOYMENT_K8S.md` and `DEPLOYMENT.md`.

## Quickstart

```bash
helm install dash ./helm/dash \
  --namespace dash --create-namespace \
  --values ./helm/dash/values-prod.yaml \
  --set secrets.openrouterApiKey=$OPENROUTER_KEY \
  --set secrets.dbPass=$DB_PASS \
  --set secrets.superAdminPass=$ADMIN_PASS \
  --set ingress.host=dash.acme.com
```

Verify:

```bash
kubectl -n dash get pods
kubectl -n dash port-forward svc/dash-api 8001:8001
curl http://localhost:8001/health
```

## What gets deployed

| Component         | Kind                                 | Notes                                                   |
| ----------------- | ------------------------------------ | ------------------------------------------------------- |
| `dash-api`        | Deployment + Service + HPA           | FastAPI, autoscale 3 → 10, target CPU 70 %              |
| `dash-db`         | StatefulSet + Service + PVC          | PostgreSQL 18 + pgvector, 50 Gi default                 |
| `dash-pgbouncer`  | Deployment (2 replicas) + Service    | Transaction-mode pooler, scram-sha-256                  |
| `dash-ml-worker`  | Deployment (1 replica)               | 1 GB RAM cap, polls `dash_ml_jobs`                      |
| `dash-caddy`      | Deployment + Service + ConfigMap + PVC | **Optional.** Disable when fronting with cloud LB     |
| `dash-knowledge-pvc` | PVC (RWX)                         | Shared across api + ml-worker                           |
| `dash-learning-daily`   | CronJob `0 4 * * *`           | Daily self-learning cycle                                |
| `dash-learning-canary`  | CronJob `0 5 * * 0`           | Sunday dry-run, regression detection                     |
| `dash-learning-forget`  | CronJob `30 4 * * *`          | Daily forgetting-curve decay                             |
| `Ingress`         | Ingress                              | cert-manager TLS, NGINX or ALB                          |
| `ConfigMap`       | `dash-config`                        | All non-secret env                                      |
| `Secret`          | `dash-secrets`                       | OpenRouter / DB / OAuth / Slack                         |
| `NetworkPolicy`   | NetworkPolicy                        | Restricts cross-namespace traffic                       |
| `RBAC`            | ServiceAccount + Role + RoleBinding  | Least-privilege; CronJobs use same SA                   |

## Values files

| File                | Use case                                              |
| ------------------- | ----------------------------------------------------- |
| `values.yaml`       | Defaults — single-replica, no HPA, dev-friendly       |
| `values-dev.yaml`   | Dev override — small PVC, debug logs, Caddy enabled   |
| `values-prod.yaml`  | Prod override — HPA on, NetworkPolicy on, Caddy off (cloud LB)  |

## Required secrets

Pass via `--set secrets.X=...`, OR pre-create the Secret and `--set secrets.create=false`:

```bash
kubectl -n dash create secret generic dash-secrets \
  --from-literal=openrouter-api-key=$OPENROUTER_KEY \
  --from-literal=db-pass=$DB_PASS \
  --from-literal=super-admin-pass=$ADMIN_PASS
```

Optional secret keys (Tier 2-4 in `DEPLOYMENT.md`):

`ms-client-secret`, `google-client-secret`, `tavily-api-key`, `brave-api-key`, `perplexity-api-key`, `fred-api-key`, `slack-token`, `slack-signing-secret`, `slack-learning-webhook`.

## Key values

```yaml
image:
  repository: dash
  tag: latest

api:
  replicas: 3
  hpa:
    enabled: true
    minReplicas: 3
    maxReplicas: 10
    targetCPUUtilization: 70
  env:
    LEARNING_SCHEDULER_DISABLED: "true"   # cycle runs as CronJob, not in-pod
    AGNO_DEBUG: "False"
    CORS_ORIGINS: "https://dash.acme.com"
    BRANDING_DIR: /branding/acme         # mount custom branding via configmap

db:
  storage: 50Gi
  storageClass: gp3
  resources:
    requests: { cpu: 1,   memory: 4Gi }
    limits:   { cpu: 4,   memory: 16Gi }

mlWorker:
  resources:
    limits: { memory: 1Gi }    # hard cap matches Compose
  env:
    JOB_TIMEOUT_SECONDS: "300"  # 5-min SIGALRM ceiling

learningCronjobs:
  enabled: true
  daily:   { schedule: "0 4 * * *"  }
  canary:  { schedule: "0 5 * * 0"  }
  forget:  { schedule: "30 4 * * *" }

ingress:
  enabled: true
  className: nginx
  host: dash.acme.com
  tls:
    enabled: true
    issuer: letsencrypt-prod
```

See `values.yaml` for the full schema.

## Migrations

Run once after first install (or on upgrade if new files added in `db/migrations/`):

```bash
kubectl -n dash exec -it dash-db-0 -- bash -c \
  'for f in /migrations/*.sql; do psql -U ai -d ai -f "$f"; done'
```

All migrations are idempotent (`IF NOT EXISTS`). Re-running is safe.

## Multi-pod scheduler safety

Critical: the in-process learning scheduler is per-pod. Running it on every API replica causes a race (same cycle fires N times → wasted cost, double consolidations). The chart sets:

- `LEARNING_SCHEDULER_DISABLED=true` on `dash-api` (env in `configmap.yaml`)
- 3 `learning-cronjobs.yaml` resources own the schedule, single-pod execution

Do not flip this on a multi-replica install.

## Branding (multi-tenant)

Mount per-tenant branding directory:

```yaml
api:
  extraVolumes:
    - name: branding
      configMap:
        name: dash-branding-acme
  extraVolumeMounts:
    - name: branding
      mountPath: /branding/acme
  env:
    BRANDING_DIR: /branding/acme
```

`configmap.yaml` for branding holds `company.json`, `theme.css`, `logo.svg`. See `branding/default/README.md`.

## Upgrading

```bash
helm upgrade dash ./helm/dash -n dash --reuse-values \
  --set image.tag=v1.1.0
```

Code-only upgrade (no DB chart change):

```bash
kubectl -n dash rollout restart deploy/dash-api deploy/dash-ml-worker
kubectl -n dash rollout status deploy/dash-api
```

## Lint / debug

```bash
helm lint ./helm/dash
helm template dash ./helm/dash --debug | less
helm template dash ./helm/dash --values ./helm/dash/values-prod.yaml --debug
kubectl -n dash get cronjob,deploy,sts,svc,ingress,pvc
```

## Uninstall

```bash
helm uninstall dash -n dash
# DB PVC retained by default — delete manually if you want a clean slate:
kubectl -n dash delete pvc dash-db-data-dash-db-0
kubectl -n dash delete pvc dash-knowledge-pvc
```

## Dependencies

No subcharts. Depends on cluster having:

- Kubernetes 1.27+
- A default StorageClass (RWO for DB, RWX for knowledge)
- cert-manager (if `ingress.tls.enabled`)
- An ingress controller (NGINX, ALB, GCE) matching `ingress.className`

For RDS / Cloud SQL replacing the in-cluster `dash-db` StatefulSet, see `DEPLOYMENT_AWS.md` and `DEPLOYMENT_GCP.md`.

## Related

- `../../DEPLOYMENT_K8S.md` — full K8S deploy walkthrough
- `../../DEPLOYMENT.md` — env var reference
- `../../SECURITY.md` — RBAC, NetworkPolicy, secrets model
