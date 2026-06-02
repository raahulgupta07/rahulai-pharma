# Pharma Demo Walkthrough — 12-min stakeholder script

> Live demo of the Dash Visibility Framework using a pharmacy chain. Audience: execs, ops leads, security reviewers. For deeper concepts see [VISIBILITY.md](./VISIBILITY.md). For endpoint details see [VISIBILITY_API.md](./VISIBILITY_API.md).

**Setup before you start the clock:**

- Project `pharma_adm_pharma` is onboarded with the `pharmacy` template.
- 5 stores seeded: `MUM01`, `MUM02`, `DEL01`, `BLR01`, `HYD01`.
- 10 SKUs, 50 inventory rows.
- Two test users:
  - `pharma_admin` — admin role at every scope.
  - `mum01_manager` — manager role at `MUM01`, viewer at `MUM02`.
- Browser zoom 110%, dark mode off, dev tools closed.
- Have the policy diff JSON open in a side tab in case anyone asks.

---

## 0:00 – 1:00 — Setup framing

**Click:** the project tile `pharma_adm_pharma` from the home grid.

**Say:**
> "This is a fictional pharmacy chain — five stores, ten SKUs, fifty inventory rows. Same data shape you'd see in retail, hospitality, or banking. The point of the next twelve minutes is to show you a single mechanism that lets every store see its own numbers in full, see peer numbers in **bands**, and never see anything from outside the network. We call it federated read with projection downgrade."

**Watch for:** heads nodding on "federated" — security folks will perk up. If someone asks "is this RLS?" — "RLS hides rows; this hides *columns*, by audience. Different layer."

---

## 1:00 – 3:00 — Scope auth

**Click:** profile menu → `Sign in as → pharma_admin`.

**Click:** the scope picker chip in the top nav (currently `—`).

**Say:**
> "Every login lands on a scope picker. A user can belong to many stores; they pick which one they're operating as. The choice flows into every query as a header: `X-Scope-Id`. Pick `MUM01`."

**Click:** `MUM01`.

**Show:** top nav now shows `🏪 MUM01 · admin`.

**Say:**
> "That chip is sticky. It's not just a UI affordance — every API call from this session now carries `X-Scope-Id: MUM01`. The server can refuse anything that doesn't match the user's assignments. So step one of the framework is: **the scope is part of identity, not part of the query**."

**Watch for:** the security reviewer should ask "what if they tamper with the header?" — answer: "Server validates the header against the user's assignments on every request. Tamper → 403."

---

## 3:00 – 5:00 — Policy editor

**Click:** Settings (gear icon) → VISIBILITY tab.

**Say:**
> "This is the policy editor. Empty by default. Watch."

**Click:** `APPLY TEMPLATE` → `💊 Pharmacy`.

**Show:** field matrix populates with ~6 tables, several dozen rows.

**Say:**
> "One click loaded the pharmacy template. Look at the `inventory` table. `qty` at private is `full`, at network is `band`, at public is `band`. `cost` is `full` at private, `hide` everywhere else. `prescriber_npi` and `patient_id` are masked at every audience — that's PHI."

**Hover:** the gear icon next to `qty`.

**Say:**
> "Each numeric field has a band config. For `qty` we're using buckets `[0–10, 11–50, 51–200, 201+]`. Coarser buckets at higher audiences if you want — fully tunable."

**Click:** `SAVE` — show that it creates a draft, not an immediate publish.

**Watch for:** ops leads will ask "can we change these later?" — "Yes, every save creates a new draft. We'll see the approval flow in two minutes."

---

## 5:00 – 7:00 — Preview simulator

**Click:** `PREVIEW AS USER`.

**Configure:**
- User: `mum01_manager`
- Scope: `MUM02` *(deliberately a peer scope, not their primary)*
- Intent: `network`
- Query: paste

```sql
SELECT store_id, sku_id, qty, cost
FROM inventory
WHERE store_id = 'MUM02'
```

**Click:** `RUN`.

**Show:** results table renders with `qty` as `"11–50"`, `"51–200"` strings, and `cost` column missing entirely.

**Say:**
> "Same query, same SQL. But because this user is reading a *peer* store at `intent=network`, the policy engine downgraded `qty` to a band and dropped `cost` outright. Notice the right-hand panel: it lists every downgrade it applied and which rule fired. This is what your auditor wants to see."

**Click:** the intent selector → change to `private` → `RUN` again.

**Show:** 403 with `intent_capped`.

**Say:**
> "And when the same user tries to escalate — `private` would mean 'I own this data' — they're capped, because their role for `MUM02` is viewer, not manager. The role gates which intents you can even *request*."

**Watch for:** the "aha" moment. Pause for two seconds before moving on.

---

## 7:00 – 9:00 — Roles + sign-off

**Click:** ROLES & PERMISSIONS section.

**Show:** three roles (`viewer`, `manager`, `admin`) with their intent checkboxes.

**Click:** the assignments table → find a test user → change role from `viewer` to `manager`.

**Say:**
> "Roles are project-defined. Three is the typical shape but you can have more. Let's promote this user to manager."

**Scroll up. Click:** `SAVE` again — note the diff counter shows pending changes.

**Click:** `REQUEST APPROVAL`.

**Configure:** add reviewer `pharma_admin_2`, comment `"Promote user to manager for MUM01"`.

**Click:** `Submit`.

**Switch user:** profile → `Sign in as → pharma_admin_2`.

**Click:** Settings → VISIBILITY → DRAFTS & APPROVALS.

**Show:** the pending draft.

**Click:** the row → side panel shows the diff.

**Click:** `Approve`.

**Show:** draft auto-publishes, version number ticks up in HISTORY.

**Say:**
> "Two-person rule. The author can't approve their own draft — server enforces it, not just the UI. Every published version lands in HISTORY with author, reviewer, timestamp, diff. SOC-2 friendly out of the box."

**Watch for:** compliance folks will ask about audit retention — "90 days online, CSV export to your warehouse for longer."

---

## 9:00 – 11:00 — Audit + time-travel

**Click:** AUDIT LOG section.

**Filter:** `intent = network`, last 1 hour.

**Show:** the network query you ran in the simulator now appears in the log.

**Click:** the row.

**Show:** side panel with full payload — user, scope, table, row count, applied downgrades.

**Say:**
> "Every policy-checked read is logged. Filter by user, scope, table, intent. Export to CSV — pipe it into BigQuery and you have a regulator-ready audit warehouse."

**Click:** TIME TRAVEL section.

**Pick:** a date one week ago (before today's policy edits).

**Click:** `LOAD`.

**Show:** field matrix re-renders with the *old* policy.

**Say:**
> "Time travel. 'What did this user see last quarter?' is a question you can now answer in two clicks. Pick a date, the editor renders read-only with the policy that was active. Pair it with the audit log and you can fully reconstruct any historical read."

**Watch for:** legal-team eyebrows. This is usually the moment buy-in flips.

---

## 11:00 – 12:00 — Wrap

**Click:** back to dashboard. Don't navigate further — let the demo settle visually.

**Say:**
> "What you just saw is a single pattern: federated read with projection downgrade. Scope is identity. Audience is intent. Field rules downgrade column-by-column based on whose data is being read at what audience. Roles cap which intents you can request. Every read is audited. Every policy edit is approved and time-traveled."
>
> "We demoed it on pharmacy because PHI is the strictest case. Swap the template and the same engine runs retail (cost / margin), hotel (ADR / guest PII), or bank (balance / PAN). The data model and the UI are identical — only the field matrix changes. Build once, ship per industry."

**Final beat:** pause. Don't fill silence. Wait for questions.

**Recommended Q&A pivots:**

- "How do we onboard a new tenant?" → one POST to `/onboard-industry`. 30 seconds.
- "Performance impact?" → projection happens at result time, not query plan time. Cost is linear in column count, not row count.
- "Can existing dashboards use this?" → yes — embed tokens are bound at mint time, see [VISIBILITY_API.md → Embed binding](./VISIBILITY_API.md#embed-binding).
- "What if we already have RLS?" → orthogonal. RLS hides rows. This hides columns. Stack them.

**Backup if you have 2 extra minutes:** show the embed flow — mint a token bound to `(MUM01, network, viewer)`, paste into an incognito window, demonstrate the same banding applied to a public-looking link.

---

## See also

- [VISIBILITY.md](./VISIBILITY.md) — admin guide
- [VISIBILITY_API.md](./VISIBILITY_API.md) — endpoint reference
- [VISIBILITY_FAQ.md](./VISIBILITY_FAQ.md) — common questions
