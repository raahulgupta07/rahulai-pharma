# CityAgent Insights — UI Style Guide

Single source of truth. Every new component MUST use these tokens and primitives.

**Rule:** No hardcoded `#hex`, no inline `style="border|background|color|padding|border-radius"`. Use tokens + `ds-*` classes.

---

## 1. Tokens (CSS vars — see `src/app.css`)

### Colors

| Purpose | Token |
|---|---|
| Page bg | `--pw-bg` |
| Surface (card) | `--pw-surface` |
| Surface alt (subtle bg) | `--pw-bg-alt` |
| Ink (primary text) | `--pw-ink` |
| Muted (secondary text) | `--pw-muted` |
| Dim (placeholder, disabled) | `--pw-dim` |
| Border | `--pw-border` |
| Border strong (hover) | `--pw-border-strong` |
| Accent (coral, primary action) | `--pw-accent` |
| Accent wash (active bg) | `--pw-accent-wash` |
| Success / Warn / Danger / Info | `--ds-success`, `--ds-warn`, `--ds-danger`, `--ds-info` (each + `-soft` + `-ink`) |

### Spacing (4px base)

`--sp-1` 4 · `--sp-2` 8 · `--sp-3` 12 · `--sp-4` 16 · `--sp-5` 20 · `--sp-6` 24 · `--sp-7` 32 · `--sp-8` 48 · `--sp-9` 64

### Radius

`--r-xs` 4 · `--r-sm` 6 · `--r-md` 10 · `--r-lg` 14 · `--r-xl` 20 · `--r-pill` 999

### Type

`--fs-xs` 11 · `--fs-sm` 12 · `--fs-base` 13 · `--fs-md` 14 · `--fs-lg` 16 · `--fs-xl` 20 · `--fs-2xl` 24 · `--fs-3xl` 28 · `--fs-display` 34 · `--fs-hero` 44

### Shadow

`--sh-1` (subtle) · `--sh-2` (card hover) · `--sh-3` (drawer) · `--sh-pop` (modal)

### Z-index

`--z-nav` 100 · `--z-drawer` 500 · `--z-modal` 1000 · `--z-toast` 2000

---

## 2. Component primitives

### Card

```html
<div class="ds-card ds-card-hover">…</div>
<div class="ds-card-flat">…</div>
```

### KPI / Stat card (Activity pattern — LOCKED)

```html
<div class="ds-stat">
  <div class="ds-stat-row">
    <div class="ds-stat-value">1,202</div>
    <div class="ds-stat-icon">↔</div>
  </div>
  <div class="ds-stat-label">Links</div>
</div>
```

Optional delta: `<span class="ds-stat-delta up">▲ 12%</span>`

### Table

```html
<div class="ds-table-wrap">
  <table class="ds-table">
    <thead><tr><th>Name</th><th>Source</th></tr></thead>
    <tbody>
      <tr><td>account</td><td><span class="chip chip-green">TEMPLATE</span></td></tr>
    </tbody>
  </table>
</div>
```

Striped: add `ds-table-striped`.

### Tabs

```html
<div class="ds-tabbar">
  <button class="ds-tab active">Types <span class="ds-tab-badge">155</span></button>
  <button class="ds-tab">Links <span class="ds-tab-badge">1202</span></button>
</div>
```

Active = ink text + 600 weight + 2px coral underline + accent-wash badge.

### Form

```html
<div class="ds-field">
  <label class="ds-field-label">Name</label>
  <input class="ds-input" placeholder="…" />
  <span class="ds-field-help">Used to identify the entity.</span>
</div>

<select class="ds-select">…</select>
<textarea class="ds-textarea"></textarea>
```

### Modal

```html
<div class="ds-modal-backdrop">
  <div class="ds-modal">
    <div class="ds-modal-head">
      <h3 class="ds-modal-title">Add term</h3>
      <button class="ds-modal-close">×</button>
    </div>
    <div class="ds-modal-body">…</div>
    <div class="ds-modal-foot">
      <button class="btn-secondary">Cancel</button>
      <button class="btn-primary">Save</button>
    </div>
  </div>
</div>
```

### Buttons (existing canonical — do not duplicate)

`btn-primary` · `btn-secondary` · `btn-ghost` · `btn-danger` · `btn-sm`

### Chips (status — existing canonical)

`chip chip-{coral|green|amber|blue|gray|purple|red}`

### Pill segments

```html
<button class="pill-segment active">All</button>
<button class="pill-segment">Template</button>
```

### Page layout

```html
<div class="ds-page">
  <div class="ds-page-head">
    <div>
      <h1 class="ds-page-title">Ontology Workbench</h1>
      <div class="ds-page-sub">Unified entity, link, action catalog</div>
    </div>
    <button class="btn-primary">Refresh</button>
  </div>

  <div class="ds-grid ds-grid-6">… stat cards …</div>
</div>
```

### Empty state

```html
<div class="ds-empty">
  <div class="ds-empty-icon">∅</div>
  <div class="ds-empty-title">No queries yet</div>
  <div class="ds-empty-text">Run a SQL query from the Analyst agent to see results here.</div>
  <button class="btn-primary">Open chat</button>
</div>
```

### Toolbar / Section head

```html
<div class="ds-toolbar">
  <div class="ds-toolbar-group">
    <input class="ds-input" style="width:240px" placeholder="Search…" />
    <button class="pill-segment active">All</button>
  </div>
  <div class="ds-toolbar-group">
    <button class="btn-ghost">CSV</button>
    <button class="btn-primary">+ New</button>
  </div>
</div>

<div class="ds-section-head">
  <h2 class="ds-section-title">Recent activity</h2>
  <a class="ds-section-sub">View all →</a>
</div>
```

### Grid

`ds-grid ds-grid-{2|3|4|6|auto}` — auto-collapses on mobile.

---

## 3. Rules

1. **No hardcoded hex.** Use tokens. Audit script (`npm run style:audit`) fails CI on violation.
2. **No inline border/background/color/radius** in `style="…"`. Add a class.
3. **No new button styles.** Use `btn-primary/secondary/ghost/danger`. Need a variant? Extend in `app.css`, don't inline.
4. **Tables always wrapped** in `.ds-table-wrap` for the rounded border.
5. **Active states** = ink-fill (`--pw-ink-fill`) for pill segments, coral underline for tabs.
6. **KPI tiles always** follow `.ds-stat` Activity pattern. No exceptions. Value top-left serif, icon top-right muted, label bottom sentence-case.
7. **Headings** = serif (`--pw-serif`), body = Inter (`--pw-sans`), code = JetBrains Mono (`--pw-mono`).
8. **Sentence case** for labels and section titles. NO ALL-CAPS except `ds-tab-badge`, chip text, and `.tag-label`.
9. **Spacing** uses tokens (`--sp-*`), never raw `px` for padding/margin.
10. **Z-index** uses tokens. Never raw numbers.

---

## 4. Migration map (legacy → new)

| Legacy class | Use instead |
|---|---|
| `data-table` | `ds-table` inside `ds-table-wrap` |
| `dash-tab` / `dash-tab-active` | `ds-tab` + `.active` |
| `response-tab` / `response-tab-active` | keep (chat-specific), but match colors |
| `pw-stat-card` | `ds-stat` (Activity pattern) |
| `cc-btn-ghost` | `btn-ghost` |
| inline `<input style="border…">` | `ds-input` |
| inline `<div style="background:#fff; border:1px solid #ddd; border-radius:12px">` | `ds-card` |

---

## 5. Audit

```bash
cd frontend
npm run style:audit       # fails on hardcoded hex + inline border/bg, then runs chat-mirror audit
npm run chat:audit        # standalone chat-mirror linter
npm run style:audit -- --fix-suggest  # prints suggested replacements
```

---

## 6. Chat tag rendering mirror rule

**Single source of truth:** `frontend/src/lib/chat/ChatMessageList.svelte`.
**Full architecture doc:** `frontend/CHAT_RENDERER.md`.

The product has three chat surfaces (`/ui/chat`, `/ui/project/{slug}`, embed widget). The first two MUST render every chat tag through the shared `<ChatMessageList>` component. The embed widget (`dash/embed/widget.js`) is intentionally forked (no auth context, reduced feature set).

### 6.1 Tag → canonical render location

Every tag below is parsed and rendered exclusively inside `ChatMessageList.svelte`. Page-level pre-render stripping via `.replace()` is allowed; page-level visual rendering is **not**.

| Tag                                              | Canonical location                       |
|--------------------------------------------------|------------------------------------------|
| `[KPI:value\|label\|change]`                     | `ChatMessageList.svelte:414-428`         |
| `[VERDICT:ACQUIRE\|DEFER\|PASS\|BUY\|HOLD\|SELL\|conviction\|rationale]` | `ChatMessageList.svelte:431-491`         |
| `[CONFIDENCE:HIGH\|MED\|LOW]`                    | `ChatMessageList.svelte:493-544, 714`    |
| `[IMPACT:pct\|recovered\|total]`                 | `ChatMessageList.svelte:546+`            |
| `[CHART:title]`                                  | Inline ANALYSIS rendering inside shared  |
| `[RELATED:question]`                             | Shared component (clickable buttons)     |
| `[CLARIFY:option1\|option2]`                     | Shared; parser hoisted to `lib/chat/tag-parsers.ts` `parseClarify()` (~~TODO~~ consolidated) |
| `[ROUTING:agent]`                                | Pre-stripped, surfaced in SOURCES tab    |
| `[REF:table:row]`                                | Shared                                   |
| `[REL:...]` page-level split helper              | `lib/chat/tag-parsers.ts` `parseRelated()` (~~TODO~~ consolidated) |
| `[UP:+N%]` / `[DOWN:-N%]` / `[FLAT:N]`           | `formatCell()` — module export from `lib/chat/ChatMessageList.svelte` (~~TODO~~ consolidated, single definition) |
| Chart captions (`generateChartCaption()`)        | Module export from `lib/chat/ChatMessageList.svelte` (~~TODO~~ consolidated) |
| `[DASHBOARD:id]`                                 | Page state (opens DashboardPanel) — not a render concern |
| `[CAMPAIGN_PROPOSAL:name\|segment\|disc\|aud]`   | Project page only (Strategist-gated)     |

If you need a new tag, add the parser + renderer to `ChatMessageList.svelte` and list it in `CHAT_RENDERER.md §2.1` + this table.

### 6.2 User bubble — single source (Issue #25 cleanup)

**Mirror rule: User bubble = single source. Override via class composition, not `!important`.**

**Canonical CSS:** `frontend/src/app.css` — `.dash-user-bubble`. This is the *only* place
where bubble visuals (background, border, radius, padding, max-width, font) are defined.

```css
.dash-user-bubble {
  background: var(--pw-accent-soft, rgba(201,99,66,0.12));
  color: var(--pw-ink);
  border: 1px solid var(--pw-accent-soft, rgba(201,99,66,0.18));
  border-radius: 14px 14px 4px 14px;
  padding: 8px 14px;
  max-width: min(560px, 60%);
  width: fit-content;
  align-self: flex-end;
  font-size: 14px;
  line-height: 1.5;
  word-wrap: break-word;
}
```

**Rules:**

1. The bubble element MUST carry `class="dash-user-bubble"`. Legacy class aliases
   (`bubble-user`, `user-bubble`, `msg-user`, `user-msg-bubble`) MAY be added
   for backwards compatibility but contribute no styles of their own.
2. `ChatMessageList.svelte` `.bubble-user` block is empty — it exists only as a
   selector hook for tests/legacy scripts. Do NOT re-declare visuals there.
3. `frontend/src/routes/project/[slug]/+page.svelte` `:global(...)` overrides on
   the legacy aliases inherit from the canonical class via plain (non-`!important`)
   declarations so older markup keeps working without specificity wars.
4. Per-page or per-host overrides MUST compose a sibling class
   (e.g. `class="dash-user-bubble dash-user-bubble--narrow"`) — never add
   `!important` to a legacy alias.
5. Bug fixes to bubble visuals go in `app.css` `.dash-user-bubble` FIRST. The
   compatibility blocks downstream then mirror naturally because they reference
   the same CSS variables.

### 6.3 Enforcement

`scripts/check-chat-mirror.mjs` (wired as `npm run style:audit` and `npm run chat:audit`) fails the build when:

1. `bubble-user` class is added outside the shared component or allowlisted shells.
2. Page-level rendering of `[KPI:`, `[CONFIDENCE:`, `[VERDICT:`, `[IMPACT:` is introduced (stripping via `.replace()` is fine).
3. `formatCell()` or `generateChartCaption()` is redefined outside `lib/chat/`.

Threshold via `CHAT_MIRROR_THRESHOLD` env var; default **0** (any violation fails). Previously defaulted to 4 to grandfather the page-level `formatCell()` / `generateChartCaption()` / `parseClarify` / `parseRelated` duplications — those have all been hoisted (formatCell + generateChartCaption to module exports on `ChatMessageList.svelte`; parseClarify + parseRelated to `lib/chat/tag-parsers.ts`). Page files import — never redefine.

---

## Issue #26 — User bubble wrap regression guard

**Symptom:** Normal-length user questions (40-80 chars) wrap to 2-3 lines in chat bubble even on wide screens.

**Root cause stack:**
1. Chat messages wrapper has `max-width: 820px` (centers chat column)
2. Bubble `max-width: 90%` of wrapper = ~738px ceiling
3. Bubble inherits `--pw-font-body` which globally resolves to **EB Garamond serif** (~11px/char)
4. 70-char serif text = ~770px > 738px ceiling → wraps

**Fix (locked in):**
- Chat wrapper `max-width: 1280px` (was 820px) in **both**:
  - `routes/project/[slug]/+page.svelte:1649`
  - `routes/chat/+page.svelte:1272`
- `.dash-user-bubble` font HARDCODED to system sans-serif with `!important` in:
  - `frontend/src/app.css` `.dash-user-bubble`
  - `routes/project/[slug]/+page.svelte` `:global(.bubble-user)` mirror

**Never do:**
- Lower chat wrapper below 1200px without also switching bubble back to mono/narrow sans
- Swap bubble `font-family` to `var(--pw-font-body)` (resolves to wide serif)
- Remove `!important` on bubble `font-family` (parent `.prose-chat` / `.bubble-assistant` will inherit serif back)
