# Visibility & Sharing — Admin Guide

> Admin onboarding for the Dash Visibility Framework. For HTTP endpoint details see [VISIBILITY_API.md](./VISIBILITY_API.md). For a 12-min stakeholder demo see [VISIBILITY_DEMO.md](./VISIBILITY_DEMO.md). For common questions see [VISIBILITY_FAQ.md](./VISIBILITY_FAQ.md).

---

## Overview

Dash uses a **federated read with projection downgrade** pattern: every query is bound to a `(scope, intent, role)` tuple. The user's *scope* (e.g. store `MUM01`) controls which rows they own. The *intent* (`private` | `network` | `public`) declares how broadly the result is being read. The *role* gates which intents the user can request. The policy engine then **downgrades the projection** field-by-field — each column is rendered as `full`, `band` (bucketed), `mask` (redacted), or `hide` (dropped) based on whose data is being read and at what audience. The same SQL therefore returns sharper data inside your own scope and progressively fuzzier data when crossing into peer or public reads, all without rewriting queries.

---

## Concepts

| Term         | Meaning                                                                                         |
| ------------ | ----------------------------------------------------------------------------------------------- |
| **Scope**    | A unit of ownership — typically a store, branch, region, or tenant. Every row carries one.      |
| **Audience** | The read mode the caller declares: `private` (own data), `network` (peers), `public` (anyone). |
| **Field rule** | Per-column projection: `full` (raw), `band` (bucket), `mask` (redacted token), `hide` (drop). |
| **Role**     | A named set of allowed intents and field overrides. Roles gate **which intents a user may request**, and may further tighten field rules beyond the policy default. |
| **Policy**   | The full matrix of `{table → field → audience → rule}` plus role and band-threshold config.    |
| **Draft**    | An unpublished revision of a policy, awaiting sign-off.                                         |
| **Audit row** | A logged read: `(timestamp, user, scope, intent, table, row_count, downgrade_summary)`.       |

### Audience downgrade matrix (default)

```
                    private   network   public
own scope            full      full      band
peer scope (same     —         band      mask
  network)
foreign scope        —         —         hide
```

A user reading their **own** rows at `intent=private` sees `full`. The same user reading a **peer's** rows at `intent=network` sees `band`. A `public` reader of a foreign scope sees `hide` for sensitive fields.

---

## Quick Start

Five steps to enable visibility for a fresh project:

1. **Apply an industry template.** `POST /api/projects/{slug}/onboard-industry` with `{"industry": "pharmacy"}`. This seeds tables, default policy, default roles, and a sample dataset.
2. **Seed scopes.** `POST /api/auth/scopes/seed` to register store IDs as scopes. Each row in `stores` becomes a scope id.
3. **Assign roles.** Settings → VISIBILITY → ROLES & PERMISSIONS → assign each user a role (`viewer`, `manager`, `admin`).
4. **Publish the policy.** Open the field matrix, review it, click `SAVE`. If your project requires sign-off, this creates a draft and you must `REQUEST APPROVAL`.
5. **Test.** Use TEST SANDBOX (run a query) and PREVIEW AS USER (impersonate a viewer in another scope) to confirm bands and masks render correctly.

---

## UI Walkthrough

Open **Settings → VISIBILITY**. The tab is divided into nine collapsible sections, top to bottom.

```
┌── Settings → VISIBILITY ──────────────────────────────┐
│ [APPLY TEMPLATE ▾]  [SAVE]  [PUBLISH]  [DISCARD]      │
│ ─────────────────────────────────────────────────────  │
│ § FIELD MATRIX                                        │
│ § TEST SANDBOX                                        │
│ § PREVIEW AS USER                                     │
│ § ROLES & PERMISSIONS                                 │
│ § AUDIT LOG                                           │
│ § TIME TRAVEL                                         │
│ § DRAFTS & APPROVALS                                  │
│ § HISTORY                                             │
└────────────────────────────────────────────────────────┘
```

### APPLY TEMPLATE

Dropdown lists the bundled industry templates: `pharmacy`, `retail`, `hotel`, `bank`, `generic`. Selecting one overlays the template's field rules onto the current draft. Existing custom rules are **preserved** unless they collide; collisions show a yellow diff badge so you can review before saving.

### Field matrix

A scrollable table with one row per `(table, field)` pair and three columns per audience (`private` / `network` / `public`). Each cell is a select with values `full | band | mask | hide`. Numeric fields also show a band-config gear icon — click to set thresholds (e.g. `qty: [0–10, 11–50, 51+]`).

```
┌─ inventory ────────────────────────────────────────────┐
│ field      │ private │ network │ public │ band cfg    │
│ store_id   │ full    │ full    │ mask   │ —           │
│ sku_id     │ full    │ full    │ mask   │ —           │
│ qty        │ full    │ band    │ band   │ [0,10,50,∞] │
│ cost       │ full    │ hide    │ hide   │ —           │
│ updated_at │ full    │ full    │ band   │ daily       │
└────────────────────────────────────────────────────────┘
```

### SAVE bar

Sticky top bar with `SAVE`, `PUBLISH`, `DISCARD`. `SAVE` writes to the current draft. `PUBLISH` activates it (or moves it into approval if sign-off is required). `DISCARD` reverts unsaved cell edits. A diff counter shows pending changes.

### TEST SANDBOX

Paste any SQL query, choose `intent=`, click `RUN`. The sandbox executes the query through the policy engine and returns the projected rows side-by-side with the raw rows (admin-only view), so you can see exactly which fields downgraded and why.

### PREVIEW AS USER

Pick a real user (or a `(scope, role)` tuple) and a query. The system runs as if that user issued it. Results match what they'd actually see. This is the fastest way to debug "why can my manager not see column X."

### ROLES & PERMISSIONS

Two stacked tables:

- **Roles**: name, allowed intents (checkboxes for `private`/`network`/`public`), field overrides JSON.
- **User-Role assignments**: user, scope, role. Multi-row edit; `Bulk assign` button accepts a CSV upload.

### AUDIT LOG

Paginated table of every policy-checked read in the last 90 days. Columns: timestamp, user, scope, intent, table, row count, downgrade summary (e.g. `qty: full→band`). Filter by user / scope / intent / table. `EXPORT CSV` button hits `/visibility-audit.csv`.

### TIME TRAVEL

Date picker. Pick a past date and click `LOAD`. The field matrix re-renders showing the policy that was *active* on that date. Useful for "what did this user see last quarter?". Read-only.

### DRAFTS & APPROVALS

Lists pending drafts with status (`pending`, `approved`, `rejected`). Each row has `View diff`, `Approve`, `Reject`. Approval requires admin role and at least one reviewer other than the author.

### HISTORY

Append-only log of every published policy version. Each entry shows who published, when, and a diff link. Click any version → `RESTORE` to fork a new draft from it.

---

## Industry Templates

Templates are presets that combine: scope shape, default field matrix, default roles, sample seed data. All templates live in code; admins apply by name.

| Template   | Scope shape       | Sensitive bands           | Notable masks                    |
| ---------- | ----------------- | ------------------------- | -------------------------------- |
| `pharmacy` | store             | `qty`, `cost`, `margin`   | `patient_id`, `prescriber_npi`   |
| `retail`   | store / region    | `qty`, `revenue`, `gm%`   | `customer_email`, `loyalty_id`   |
| `hotel`    | property          | `adr`, `occupancy`, `rev` | `guest_name`, `room_no`, `cc4`   |
| `bank`     | branch            | `balance`, `tx_amt`       | `account_no`, `pan`, `customer_id` |
| `generic`  | tenant            | `amount`, `count`         | `email`, `phone`                 |

`pharmacy` is the most opinionated: it bands `qty` into `[0–10, 11–50, 51–200, 201+]`, hides `cost` at network and above, and masks every PHI field at all audiences.

`generic` is the floor: it sets `mask` on any field tagged `pii=true` and leaves everything else `full`. Use it as a starting point for custom industries.

---

## Workflows

### Add a scope

1. Insert the row into your `stores` (or equivalent) table.
2. `POST /api/auth/scopes/seed` (idempotent — only adds new ids).
3. Settings → VISIBILITY → ROLES & PERMISSIONS → assign at least one user.

### Change a band threshold

1. Settings → VISIBILITY → field matrix.
2. Click the gear icon on the numeric field.
3. Edit thresholds (CSV of breakpoints, e.g. `0,10,50,200`).
4. `SAVE` → `PUBLISH` (or `REQUEST APPROVAL`).
5. Use TEST SANDBOX to confirm new buckets render.

### Run a preview

1. Settings → VISIBILITY → PREVIEW AS USER.
2. Pick the user (or `(scope=MUM01, role=manager)`).
3. Paste your query and intent.
4. `RUN`. Inspect the projected rows and the downgrade summary.

### Request approval

1. Edit the policy → `SAVE` (creates draft).
2. Click `REQUEST APPROVAL` in the SAVE bar.
3. Add reviewers and a comment.
4. Reviewer opens DRAFTS & APPROVALS → `Approve` or `Reject`.
5. On approve, the draft auto-publishes and rolls into HISTORY.

### Audit a cross-store read

1. Settings → VISIBILITY → AUDIT LOG.
2. Filter `intent=network` and the table of interest.
3. Sort by row count desc to find the broadest reads.
4. Click any row → side panel shows the exact downgrade applied to that read.

### Restore a historical policy

1. Settings → VISIBILITY → HISTORY.
2. Find the version, click `RESTORE`.
3. A new draft is created from that snapshot; review and `PUBLISH` as usual.

---

## Troubleshooting

| Symptom                                              | Likely cause                                                                                                            | Fix                                                                                                                                                                       |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Query returns 403 with `intent_capped`               | User's role does not allow the requested intent.                                                                        | Either lower the request to a permitted intent or grant a higher role in ROLES & PERMISSIONS.                                                                             |
| `PUT /visibility-policy` returns 422                 | Validation rule violated — usually a band has fewer than 2 thresholds, or a `mask` is set on a non-string field.        | Open the response body — it lists violating fields. Either fix and re-PUT, or `PUT ?force=true` to override (admin only, audited).                                        |
| AUDIT LOG empty                                      | Auditing not enabled, or no policy-checked reads have happened yet.                                                     | Check `audit_enabled=true` in project settings. Run a query through the sandbox to confirm wiring.                                                                        |
| Draft stuck in `pending`                             | No reviewers assigned, or all reviewers are the author.                                                                 | DRAFTS & APPROVALS → click the draft → `Add reviewer`. Sign-off needs at least one reviewer other than the author.                                                        |
| `qty` shows as integer when you expected a band      | Caller declared `intent=private` and is in-scope, **or** their role overrides the field to `full`.                      | Use PREVIEW AS USER to confirm the resolved rule. Adjust either the matrix or the role override.                                                                          |
| Embedded chart shows `[hidden]` for all values       | The embed token was bound to `intent=public` but the field is `hide` at public.                                         | Re-mint the embed with a higher `bound_intent` (e.g. `network`) or relax the field rule. See [VISIBILITY_API.md → Embed binding](./VISIBILITY_API.md#embed-binding).      |
| TIME TRAVEL shows blank policy                       | Date is before the project's first published policy.                                                                    | Pick a date after the first HISTORY entry.                                                                                                                                |
| Scope chip in top nav shows `—`                      | User is logged in but has no scope assignments.                                                                         | ROLES & PERMISSIONS → assign at least one `(scope, role)` row.                                                                                                            |

---

## See also

- [VISIBILITY_API.md](./VISIBILITY_API.md) — full HTTP reference
- [VISIBILITY_DEMO.md](./VISIBILITY_DEMO.md) — 12-min stakeholder script
- [VISIBILITY_FAQ.md](./VISIBILITY_FAQ.md) — common questions
