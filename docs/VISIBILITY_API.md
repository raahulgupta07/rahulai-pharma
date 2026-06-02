# Visibility Policy API Reference

> HTTP reference for every endpoint shipped by the visibility framework. For concepts and UI walkthrough see [VISIBILITY.md](./VISIBILITY.md). For a stakeholder demo script see [VISIBILITY_DEMO.md](./VISIBILITY_DEMO.md).

All endpoints are JSON. All authenticated endpoints require a session cookie or `Authorization: Bearer <token>` header. Project-scoped paths use `{slug}` (the project slug, e.g. `pharma_adm_pharma`).

---

## Conventions

### Standard headers

| Header             | Required for                         | Values                                                                          |
| ------------------ | ------------------------------------ | ------------------------------------------------------------------------------- |
| `X-Scope-Id`       | Any data-read endpoint               | A scope id the user is assigned to, e.g. `MUM01`.                               |
| `X-Query-Intent`   | Any data-read endpoint               | `private` \| `network` \| `public`. Defaults to `private` if omitted.           |
| `Authorization`    | All authenticated endpoints          | `Bearer <token>` or session cookie.                                             |
| `If-Match`         | `PUT /visibility-policy`             | Etag from prior `GET`. Concurrency guard.                                       |

### Standard error envelope

```json
{
  "error": {
    "code": "intent_capped",
    "message": "Role 'viewer' does not permit intent 'public'.",
    "details": { "allowed_intents": ["private", "network"] }
  }
}
```

| HTTP | When                                                             |
| ---- | ---------------------------------------------------------------- |
| 403  | Role does not allow the requested intent or scope.               |
| 404  | Project, policy, draft, scope, or template not found.            |
| 409  | Etag mismatch on `PUT`.                                          |
| 422  | Policy validation failed. Pass `?force=true` (admin) to override.|

---

## Scope auth (Phase 1)

### `GET /api/auth/scopes`

List the scopes the current user is assigned to.

- **Auth:** any logged-in user.
- **Headers:** none.

**Response 200**

```json
{
  "user_id": "u_42",
  "scopes": [
    { "id": "MUM01", "label": "Mumbai Andheri",  "role": "manager" },
    { "id": "MUM02", "label": "Mumbai Bandra",   "role": "viewer"  }
  ]
}
```

**curl**

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://dash.example.com/api/auth/scopes
```

**Python**

```python
import requests
r = requests.get(
    "https://dash.example.com/api/auth/scopes",
    headers={"Authorization": f"Bearer {token}"},
)
r.raise_for_status()
print(r.json()["scopes"])
```

**JS**

```js
const r = await fetch("/api/auth/scopes", {
  headers: { Authorization: `Bearer ${token}` },
});
const { scopes } = await r.json();
```

---

### `POST /api/auth/scopes/seed`

Reconcile the scope registry from the source-of-truth table (e.g. `stores`). Idempotent — only inserts missing rows.

- **Auth:** admin.
- **Body:** optional `{ "source_table": "stores" }`. Defaults to project config.

**Response 200**

```json
{ "added": 3, "removed": 0, "total": 17 }
```

**curl**

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_table":"stores"}' \
  https://dash.example.com/api/auth/scopes/seed
```

---

### `GET /api/auth/check`

Return the auth context the server resolved for this request — useful for debugging "why is my query intent-capped."

- **Auth:** any logged-in user.
- **Headers:** `X-Scope-Id`, `X-Query-Intent` (optional).

**Response 200**

```json
{
  "user_id": "u_42",
  "scope": { "id": "MUM01", "role": "manager" },
  "intent_requested": "network",
  "intent_resolved":  "network",
  "intent_allowed":   ["private", "network"],
  "capped": false
}
```

If the requested intent exceeds the role's allowed list, `intent_resolved` is the highest permitted, `capped: true`, and the response is `200` (not 403) — `/auth/check` is descriptive, not enforcing. Real reads return 403.

---

## Policy core (Phase 2)

### `GET /api/projects/{slug}/visibility-policy`

Fetch the currently published policy for a project.

- **Auth:** any project member.
- **Response headers:** `ETag: "v17"`.

**Response 200**

```json
{
  "version": 17,
  "published_at": "2026-04-22T10:11:12Z",
  "published_by": "u_07",
  "tables": {
    "inventory": {
      "store_id": { "private": "full", "network": "full", "public": "mask" },
      "qty":      { "private": "full", "network": "band", "public": "band",
                    "band": [0, 10, 50, 200] },
      "cost":     { "private": "full", "network": "hide", "public": "hide" }
    }
  },
  "roles_default": "viewer"
}
```

---

### `PUT /api/projects/{slug}/visibility-policy`

Replace the policy. Creates a new draft (or publishes directly if sign-off is disabled).

- **Auth:** admin.
- **Headers:** `If-Match: "v17"` (concurrency guard).
- **Query:** `?force=true` to override 422 validation (admin only, audited).

**Body:** same shape as `GET` response, minus `version` / `published_*`.

**Response 200** (same as `GET`, with new version).

**Response 422**

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Policy validation failed.",
    "details": {
      "violations": [
        { "table": "inventory", "field": "qty",
          "rule": "band thresholds must be strictly increasing" }
      ]
    }
  }
}
```

**curl**

```bash
curl -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "If-Match: \"v17\"" \
  -H "Content-Type: application/json" \
  -d @policy.json \
  https://dash.example.com/api/projects/pharma_adm_pharma/visibility-policy
```

---

### `POST /api/projects/{slug}/visibility-policy/test`

Dry-run a query against the current (or draft) policy. Same as the TEST SANDBOX UI.

- **Auth:** admin.

**Body**

```json
{
  "sql": "SELECT store_id, sku_id, qty FROM inventory WHERE store_id = 'MUM01'",
  "as_scope": "MUM01",
  "as_intent": "network",
  "as_role": "manager",
  "draft": false
}
```

**Response 200**

```json
{
  "rows": [
    { "store_id": "MUM01", "sku_id": "SKU007", "qty": "11–50" }
  ],
  "downgrades": [
    { "field": "qty", "from": "full", "to": "band" }
  ],
  "row_count": 1
}
```

---

### `GET /api/projects/{slug}/visibility-policy/history`

List every published version, newest first.

**Response 200**

```json
{
  "versions": [
    { "version": 17, "published_at": "2026-04-22T10:11:12Z",
      "published_by": "u_07", "diff_summary": "+2 -1 fields" },
    { "version": 16, "published_at": "2026-04-15T08:02:00Z",
      "published_by": "u_07", "diff_summary": "initial" }
  ]
}
```

---

## Roles + audit (Phase 4)

### `GET /api/projects/{slug}/visibility-roles`

```json
{
  "roles": [
    { "name": "viewer",  "intents": ["private"],                    "overrides": {} },
    { "name": "manager", "intents": ["private", "network"],         "overrides": {
        "inventory.cost": { "network": "band" } } },
    { "name": "admin",   "intents": ["private", "network", "public"], "overrides": {} }
  ]
}
```

### `PUT /api/projects/{slug}/visibility-roles`

Replace the role set. Body matches `GET`. `409` on etag mismatch, `422` on a role that grants an intent not declared in the policy.

---

### `GET /api/projects/{slug}/user-roles`

Per-user, per-scope role assignments.

```json
{
  "assignments": [
    { "user_id": "u_42", "scope_id": "MUM01", "role": "manager" },
    { "user_id": "u_42", "scope_id": "MUM02", "role": "viewer"  }
  ]
}
```

### `PUT /api/projects/{slug}/user-roles`

Bulk replace. Body is the full assignment list (idempotent diff).

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"assignments":[{"user_id":"u_42","scope_id":"MUM01","role":"manager"}]}' \
  https://dash.example.com/api/projects/pharma_adm_pharma/user-roles
```

---

## Simulator + validation (Phase 5)

### `POST /api/projects/{slug}/visibility-policy/simulate`

Like `/test` but also returns *what would change* if you swapped role / intent / scope. Powers the PREVIEW AS USER UI.

**Body**

```json
{
  "sql": "SELECT * FROM inventory",
  "as_scope": "MUM01",
  "as_intent": "network",
  "as_role": "viewer"
}
```

**Response 200**

```json
{
  "would_be_capped_to": "private",
  "rows_visible": 0,
  "rows_in_scope": 50,
  "downgrades_applied": [],
  "explanation": "Role 'viewer' does not permit intent 'network'."
}
```

### `POST /api/projects/{slug}/visibility-policy/validate`

Same body as `PUT /visibility-policy`. Returns the same 422 envelope without writing. Use to lint before save.

`PUT` with `?force=true` skips validation but is logged in the audit trail with `forced=true`.

---

## Cross-store audit + time-travel (Phase 7)

### `GET /api/projects/{slug}/visibility-audit`

Paginated audit list. Filter via query params.

| Query param   | Type     | Description                              |
| ------------- | -------- | ---------------------------------------- |
| `from` / `to` | ISO date | Time window. Defaults: last 7 days.      |
| `user_id`     | string   | Filter by reader.                        |
| `scope_id`    | string   | Filter by row scope.                     |
| `intent`      | enum     | `private` \| `network` \| `public`.      |
| `table`       | string   | Filter by table name.                    |
| `limit`       | int      | Default 100, max 1000.                   |
| `cursor`      | string   | Opaque pagination cursor.                |

**Response 200**

```json
{
  "rows": [
    {
      "ts": "2026-05-07T09:14:22Z",
      "user_id": "u_42",
      "scope_id": "MUM01",
      "intent": "network",
      "table": "inventory",
      "row_count": 50,
      "downgrades": [{ "field": "qty", "from": "full", "to": "band" }]
    }
  ],
  "next_cursor": "eyJ0cyI6Li4ufQ"
}
```

### `GET /api/projects/{slug}/visibility-audit.csv`

Same filters, CSV stream. Header row included. Suitable for piping into BigQuery / Snowflake.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://dash.example.com/api/projects/$SLUG/visibility-audit.csv?from=2026-04-01" \
  > audit.csv
```

### `POST /api/projects/{slug}/visibility-policy/time-travel`

Run a query against a *historical* policy snapshot.

**Body**

```json
{
  "sql": "SELECT * FROM inventory",
  "as_of": "2026-03-15T00:00:00Z",
  "as_scope": "MUM01",
  "as_intent": "network",
  "as_role": "manager"
}
```

**Response 200** matches `/test` plus `"policy_version_used": 14`.

---

## Industry templates (Phase 8)

### `GET /api/visibility-templates`

```json
{
  "templates": [
    { "name": "pharmacy", "label": "💊 Pharmacy", "tables": 6 },
    { "name": "retail",   "label": "🛍️ Retail",   "tables": 5 },
    { "name": "hotel",    "label": "🏨 Hotel",    "tables": 4 },
    { "name": "bank",     "label": "🏦 Bank",     "tables": 7 },
    { "name": "generic",  "label": "Generic",     "tables": 1 }
  ]
}
```

### `GET /api/visibility-templates/{name}`

Returns the full template payload (policy + roles + sample seed manifest).

### `POST /api/projects/{slug}/visibility-policy/apply-template`

**Body**

```json
{ "template": "pharmacy", "merge": true }
```

`merge: true` overlays onto the existing draft. `merge: false` replaces wholesale. Always creates a draft — never publishes directly.

---

## Sign-off workflow (Hardening)

Six endpoints. All paths prefixed `/api/projects/{slug}/visibility-policy/drafts`.

| Method | Path                       | Purpose                                                 |
| ------ | -------------------------- | ------------------------------------------------------- |
| `GET`  | `/drafts`                  | List drafts (filter `?status=pending`).                 |
| `GET`  | `/drafts/{id}`             | Fetch one draft + diff vs. published.                   |
| `POST` | `/drafts/{id}/request`     | Request approval. Body: `{ "reviewers": ["u_07"] }`.    |
| `POST` | `/drafts/{id}/approve`     | Approve and publish. Reviewer must differ from author.  |
| `POST` | `/drafts/{id}/reject`      | Reject. Body: `{ "reason": "..." }`.                    |
| `DELETE` | `/drafts/{id}`           | Discard a draft. Author or admin only.                  |

**Example: request approval**

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reviewers":["u_07","u_09"],"comment":"Add band on margin"}' \
  https://dash.example.com/api/projects/$SLUG/visibility-policy/drafts/d_123/request
```

**Example: approve**

```python
requests.post(
    f"{base}/api/projects/{slug}/visibility-policy/drafts/{draft_id}/approve",
    headers={"Authorization": f"Bearer {token}"},
    json={"comment": "LGTM"},
)
```

**Error: self-approval**

```json
{ "error": { "code": "self_approval_forbidden",
             "message": "Reviewer must differ from author." } }
```

---

## Onboarding (Hardening)

### `POST /api/projects/{slug}/onboard-industry`

One-shot bootstrap: applies a template, seeds scopes, seeds sample data, creates default roles, publishes v1.

**Body**

```json
{ "industry": "pharmacy", "seed_sample_data": true }
```

**Response 200**

```json
{
  "policy_version": 1,
  "scopes_added": 5,
  "rows_seeded": 50,
  "roles_created": ["viewer", "manager", "admin"]
}
```

Idempotent only when called against an empty project. Re-running on a populated project returns `409 already_onboarded`.

---

## Embed binding (Hardening)

Embeds are public-link views of dashboards. To prevent privilege escalation, every embed token is **bound** at mint time to a fixed `(scope, intent, role)` tuple. The viewer of the embed cannot escalate beyond what the binding allows, regardless of their own user account.

The embed CRUD endpoints accept three new fields:

| Field           | Type     | Description                                                     |
| --------------- | -------- | --------------------------------------------------------------- |
| `bound_scope_id` | string  | Scope the embed reads as. Required.                             |
| `bound_intent`   | enum    | `private` \| `network` \| `public`. Required.                   |
| `bound_role`     | string  | Role to evaluate field rules under. Required.                   |

**Create**

```http
POST /api/projects/{slug}/embeds
```

```json
{
  "dashboard_id": "d_42",
  "bound_scope_id": "MUM01",
  "bound_intent": "network",
  "bound_role": "viewer",
  "expires_at": "2026-08-01T00:00:00Z"
}
```

**Response 201**

```json
{
  "id": "emb_77",
  "url": "https://dash.example.com/embed/emb_77?t=...",
  "bound_scope_id": "MUM01",
  "bound_intent": "network",
  "bound_role": "viewer"
}
```

**Update** (`PATCH /api/projects/{slug}/embeds/{id}`) accepts the same three fields. Changing any of them rotates the token — old links break.

**Validation:** `bound_intent` cannot exceed what `bound_role` permits. `422` if you try (e.g. `role=viewer` + `intent=public`).

---

## See also

- [VISIBILITY.md](./VISIBILITY.md) — admin guide and concepts
- [VISIBILITY_DEMO.md](./VISIBILITY_DEMO.md) — stakeholder script
- [VISIBILITY_FAQ.md](./VISIBILITY_FAQ.md) — troubleshooting
