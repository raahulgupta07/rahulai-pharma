# Visibility FAQ

> Common questions from admins, security reviewers, and integrators. For full concepts see [VISIBILITY.md](./VISIBILITY.md). For endpoint contracts see [VISIBILITY_API.md](./VISIBILITY_API.md).

---

### 1. What's the difference between RLS and visibility policy?

Row-Level Security hides **rows**. The visibility policy hides or downgrades **columns**, by audience. They're orthogonal — stack them. RLS decides "can you see this row at all?" The policy decides "given that you can, how sharp are the values?"

### 2. Can a user be in multiple scopes?

Yes. A user can be assigned to any number of scopes with potentially different roles in each. They pick one at login via the scope chip in the top nav, and that choice flows into every request as `X-Scope-Id`. Switching scopes is one click — no logout needed.

### 3. What happens if the band field is null?

Null falls through to the *highest* (broadest) bucket label, which renders as `"unknown"` rather than leaking the null itself. This is deliberate: null often correlates with newly-inserted rows or upstream pipeline gaps, and you don't want absence-of-value to be a side channel.

### 4. How do I rotate scope assignments quickly?

`PUT /api/projects/{slug}/user-roles` with the full assignment list — it's a bulk replace, idempotent. The UI's bulk CSV upload uses the same endpoint. Cycle time is one HTTP call.

### 5. Can I export the audit log to BigQuery / Snowflake?

Yes, via `GET /visibility-audit.csv`. The endpoint streams CSV with the same filter parameters as the JSON variant. Pipe it into your warehouse via your usual loader. There is no native BigQuery push — CSV export is the supported path.

### 6. Why is my `qty` showing as a raw integer instead of a band?

Three possible reasons, in order of likelihood: (a) you're reading at `intent=private` and you own the scope — full projection is correct; (b) your role has a field override that bumps `qty` back to `full`; (c) the policy was updated but the draft hasn't been published. Use **PREVIEW AS USER** to see exactly which rule fired.

### 7. Can a user approve their own draft?

No. The server rejects self-approval with `409 self_approval_forbidden`. Drafts need at least one reviewer who is *not* the author. This is enforced server-side, not just hidden in the UI.

### 8. What if I publish a broken policy by accident?

Open HISTORY → find the last good version → click `RESTORE`. That forks a new draft from that snapshot. Review and publish. The broken version stays in history (you can't delete history rows) but the active policy is good again. Total time: under a minute.

### 9. Does the policy affect query performance?

Projection happens after the SQL plan executes — it's a transform on result rows, not a query rewrite. Cost scales with `(column_count × row_count)` and is small relative to query time. We have not seen a benchmark where the projection step exceeded 5% of total request latency.

### 10. Can I have more than three roles?

Yes. `viewer` / `manager` / `admin` are the templated defaults, but `PUT /visibility-roles` accepts any number of named roles with custom intent grants and field overrides. Keep the count small enough to fit in the UI table comfortably (we've tested up to ~20).

### 11. How do embed links honour the policy?

Embed tokens are bound at mint time to a fixed `(scope, intent, role)` tuple via the `bound_*` fields on the embed record. The viewer's own account doesn't matter — the binding is what the policy engine evaluates against. To raise an embed's audience, mint a new one (which rotates the token).

### 12. What auth providers are supported for the underlying user identity?

The visibility framework is auth-provider-agnostic — it operates on whatever `user_id` the rest of the app resolves. If your stack uses session cookies, JWT, OAuth, or SSO, all of those flow through. The framework only adds `X-Scope-Id` and `X-Query-Intent` on top.

### 13. Can I run a query on yesterday's policy?

Yes — `POST /visibility-policy/time-travel` with `as_of` set to any past timestamp. The engine resolves the policy version active at that moment and projects accordingly. Useful for reproducing what a historical user saw, or for "what did this report look like at quarter-close" questions.

### 14. What if the field type doesn't support a rule (e.g. `band` on a string)?

`PUT /visibility-policy` returns `422 validation_failed` with the offending field listed. Either change the rule (`mask` is usually the right pick for strings) or pass `?force=true` (admin-only, audited) if you really know what you're doing. Forced policies are flagged in HISTORY.

### 15. How is the framework versioned?

Policies are versioned monotonically per project (1, 2, 3, ...). The `ETag` header on `GET /visibility-policy` returns the current version; pass it in `If-Match` on `PUT` to avoid lost updates. Concurrent edits get `409` and must re-fetch.

### 16. Does the AUDIT LOG record reads that returned zero rows?

Yes. Any read that goes through the policy engine — including ones where RLS or the policy itself reduces the result to empty — is recorded. The `row_count` field will be `0` and the `downgrades` array still lists what would have been applied. This is intentional so you can audit attempted access.

### 17. Can I write a policy in code instead of using the UI?

Yes — the UI is a thin client over `PUT /visibility-policy`. Many teams keep their policies in version control as JSON and apply via CI. The schema is documented in [VISIBILITY_API.md → Policy core](./VISIBILITY_API.md#policy-core-phase-2).

### 18. Are there any fields the framework can never expose?

Anything tagged `pii=true` in the schema is forced to `mask` minimum at every audience by default — you'd have to explicitly downgrade it to `full` per role override, and that override is logged. There's no setting that hides PII tagging from the audit trail.

### 19. What happens during onboarding if `onboard-industry` is called twice?

First call seeds and publishes v1. Second call returns `409 already_onboarded`. To re-onboard, archive the project or call the lower-level endpoints (`apply-template` + `seed`) individually — those are idempotent.

### 20. Where do I file a bug?

Open an issue in the repo with the project slug, policy version, the exact request, the response payload, and (if possible) a TEST SANDBOX run that reproduces it. Do *not* paste real PHI / PII — use the sample dataset shipped with the `pharmacy` template if you need to share a repro.

---

## See also

- [VISIBILITY.md](./VISIBILITY.md) — admin guide
- [VISIBILITY_API.md](./VISIBILITY_API.md) — endpoint reference
- [VISIBILITY_DEMO.md](./VISIBILITY_DEMO.md) — stakeholder script
