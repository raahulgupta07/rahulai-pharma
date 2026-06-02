# Secrets Management — Vault Decision Matrix

Companion to `docs/SECRETS_AUDIT.md`. The audit enumerates what secrets exist. This doc helps operators choose **where to store them next**.

**No vault is recommended unilaterally.** Pick the one whose ops cost matches your deploy target.

---

## TL;DR

| If you deploy on… | Pick |
|---|---|
| AWS (ECS/EKS/Fargate/Lambda) | **AWS Secrets Manager** |
| GCP (Cloud Run / GKE) | **Google Secret Manager** (parallel to AWS SM — same shape) |
| Azure (ACI / AKS) | **Azure Key Vault** |
| Self-hosted k8s (any cloud) | **k8s native Secrets** for simple, **External Secrets Operator + cloud vault** if multi-tenant |
| Multi-env / multi-team SaaS | **Doppler** |
| Strict self-hosted, open source mandate | **Infisical** |
| Single-node Docker Compose dev box | keep `.env` w/ tight file ACL (chmod 600) until you graduate to one of the above |

---

## Option 1 — AWS Secrets Manager

**Already documented in this repo.** See `DEPLOYMENT_AWS.md` and `DEPLOYMENT_GCP.md:131`.

| Pro | Con |
|---|---|
| Native to AWS — IAM-gated, audit-logged in CloudTrail | $0.40/secret/month (adds up for many keys) |
| ECS/EKS native `valueFrom: secretsmanager:...` syntax in task defs | AWS-only — multi-cloud means duplicating |
| KMS-encrypted at rest by default | Rotation lambdas non-trivial for custom secrets |
| Built-in versioning + rollback | Eventual-consistency on write (a few seconds) |

**Best fit:** Dash deployments on ECS/EKS, single-cloud, mid-to-large team.

**Wiring pattern (already in `DEPLOYMENT_AWS.md`):**
```bash
aws secretsmanager create-secret --name dash-openrouter-key --secret-string "sk-or-v1-..."
# Then in ECS task def:
# secrets: [{ name: OPENROUTER_API_KEY, valueFrom: "arn:aws:secretsmanager:...:secret:dash-openrouter-key" }]
```

---

## Option 2 — Doppler

| Pro | Con |
|---|---|
| Multi-env (dev/stage/prod) in one dashboard, instant diff | Third-party SaaS — vendor risk |
| Single CLI for inject (`doppler run -- docker compose up`) | $4-7/user/month (free tier exists, limits on env count) |
| Cloud-agnostic — same UX on AWS, GCP, k8s | Yet another vendor account |
| Built-in audit log + RBAC | Network dep — Doppler outage = secret-fetch outage |
| Native Docker / k8s / GitHub Actions integrations | |

**Best fit:** small-to-mid team needing dev/stage/prod parity without per-cloud vault juggling.

**Wiring pattern:**
```bash
doppler setup --project dash --config prd
doppler run -- docker compose up -d   # inject secrets as env at container start
# OR in k8s:
doppler kubernetes secrets create  # syncs Doppler → k8s Secret automatically
```

---

## Option 3 — Infisical

| Pro | Con |
|---|---|
| Open source, self-hostable — full ownership of secret data | More ops cost than SaaS (you run the server) |
| Same UX shape as Doppler — multi-env, RBAC, audit log | Smaller ecosystem of integrations than AWS SM / Doppler |
| Free if self-hosted, $8/user/month for cloud-hosted | Web UI less polished than Doppler (subjective) |
| End-to-end encryption: server can't read your secrets | |
| Cloud-agnostic | |

**Best fit:** teams with a strict "no third-party hosts our secrets" policy AND a SecOps function to run the Infisical server.

**Wiring pattern:**
```bash
infisical run --env=prod -- docker compose up -d
# OR k8s:
# install infisical-secrets-operator; create InfisicalSecret CRD; auto-syncs to k8s Secret
```

---

## Option 4 — k8s Native Secrets

| Pro | Con |
|---|---|
| Zero extra infra — comes with cluster | Stored base64-encoded, NOT encrypted, in `etcd` by default |
| Native `valueFrom: secretKeyRef:` in pod specs | No multi-env (a cluster = one env, mostly) |
| Mature tooling: `kubectl create secret`, `sealed-secrets`, External Secrets Operator | Requires k8s — Docker Compose deployments can't use |
| Free | No built-in rotation — you do it manually |
| When paired with KMS provider, encryption-at-rest in etcd | No audit log without extra tooling |

**Best fit:** single-cluster k8s deployments where simplicity wins over rotation/audit features. Pair with **External Secrets Operator** to read from a cloud vault if you graduate.

**Wiring pattern:**
```bash
kubectl create secret generic dash-secrets \
  --from-literal=OPENROUTER_API_KEY=sk-or-v1-... \
  --from-literal=CONNECTION_ENCRYPTION_KEY=...
# Then in deployment.yaml:
# env: [{ name: OPENROUTER_API_KEY, valueFrom: { secretKeyRef: { name: dash-secrets, key: OPENROUTER_API_KEY } } }]
```

The existing `helm/dash/` chart already wires environment from a Secret. Helm `values.yaml` template can fill it from any of the above (`secretsBackend: aws-sm | doppler | infisical | k8s-native`).

---

## Comparison matrix

| | AWS SM | Doppler | Infisical | k8s Native |
|---|---|---|---|---|
| **Cost** | $0.40/secret/month | $4-7/user/month | $0 self-host, $8/user cloud | $0 |
| **Multi-env** | via separate secret names | ✅ first-class | ✅ first-class | ✗ |
| **Multi-cloud** | AWS-only | ✅ | ✅ | k8s-only |
| **Self-hosted option** | ✗ | ✗ | ✅ | ✅ |
| **Audit log** | ✅ CloudTrail | ✅ built-in | ✅ built-in | ✗ (needs extra tooling) |
| **Rotation automation** | ✅ Lambda rotations | ✅ scheduled rotations | ⚠ manual | ✗ |
| **Integration w/ existing Helm chart** | via External Secrets Operator | via Doppler k8s operator | via Infisical operator | ✅ native |
| **Time to ship for Dash** | ~2-4 hr | ~1-2 hr | ~3-6 hr (incl. self-host) | ~30 min |

---

## Recommendation for Dash specifically

The audit found **4 REAL secrets + 1 production-dangerous default**. Volume is small enough that any of the four works. Constraints that narrow the field:

1. **Existing docs already reference AWS SM pattern** (`DEPLOYMENT_AWS.md`, `DEPLOYMENT_GCP.md:131`) — there's a paved path on AWS.
2. **Helm chart** is the deployment target for prod — k8s native + ESO is the most idiomatic fit.
3. **Demo/dev deployments stay on Docker Compose** — `.env` w/ chmod 600 is fine there.

**Default recommendation (operator can override):**
- **Dev / local Docker Compose:** keep `.env` with `chmod 600`. No vault.
- **Single-cluster k8s prod:** **k8s native Secret** initially. Upgrade to **External Secrets Operator + AWS Secrets Manager** if/when audit/rotation become a compliance gate.
- **Multi-cloud SaaS prod:** **Doppler** for ops simplicity.

This audit + decision matrix is a recommendation surface. The operator makes the call, runs the migration when ready.
