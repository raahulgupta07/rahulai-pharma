# Chat Renderer Architecture

**Status:** Single shared component is the canonical renderer. Pages compose around it.
**Owner:** Architecture / P4 debt (Issues #29 + #25b).
**Last reviewed:** 2026-05-17.

This document is the **single source of truth** for how chat messages render across the app. Anyone touching `[KPI:`, `[VERDICT:`, `[CONFIDENCE:`, `[IMPACT:`, `bubble-user`, or any other chat tag MUST read this first.

---

## 1. Surfaces that render chat

There are exactly **three** chat surfaces in the product:

| # | Surface         | Route                          | File                                            | Renderer            |
|---|-----------------|--------------------------------|-------------------------------------------------|---------------------|
| 1 | Dash Agent chat | `/ui/chat`                     | `frontend/src/routes/chat/+page.svelte`         | `<ChatMessageList>` |
| 2 | Project chat    | `/ui/project/{slug}`           | `frontend/src/routes/project/[slug]/+page.svelte` | `<ChatMessageList>` |
| 3 | Embed widget    | embedded `<script>` on 3rd-party site | `dash/embed/widget.js` (vanilla JS, shadow DOM) | **Intentional fork** |

Surfaces 1 and 2 share the renderer. Surface 3 is intentionally divergent (see §4).

> A historical fourth surface, `frontend/src/routes/+page.svelte`, contains a single isolated `bubble-user` class for the public landing-page preview only. Not a chat surface.

---

## 2. Canonical shared component

**File:** `frontend/src/lib/chat/ChatMessageList.svelte` (912 LOC)

### 2.1 Tag rendering capabilities (canonical)

Every chat-tag pattern listed in `CLAUDE.md` "Render tags" section MUST be parsed and rendered exclusively inside this component. Page-level pre-render stripping is acceptable; page-level rendering of the visual is NOT.

| Tag                                              | Parse location                          | Visual                                |
|--------------------------------------------------|------------------------------------------|---------------------------------------|
| `[KPI:value\|label\|change]`                     | `ChatMessageList.svelte:414-428`         | KPI tile grid                          |
| `[VERDICT:ACQUIRE\|DEFER\|PASS\|BUY\|HOLD\|SELL\|N\|rationale]` | `ChatMessageList.svelte:431-491`         | Color-bordered verdict card           |
| `[CONFIDENCE:HIGH\|MED\|LOW]`                    | `ChatMessageList.svelte:493-544, 714`    | Confidence progress bar               |
| `[IMPACT:pct\|recovered\|total]`                 | `ChatMessageList.svelte:546+`            | Impact summary card                   |
| `[CHART:title]`                                  | Inline within ANALYSIS rendering         | ECharts inline chart                  |
| `[RELATED:question]`                             | (in ChatMessageList; clickable buttons)  | Suggestion buttons                    |
| `[CLARIFY:option1\|option2]`                     | parsed page-side, rendered shared        | Clickable cards (legacy: page-level — see TODO §6) |
| `[ROUTING:agent]`                                | Pre-stripped, surfaced in SOURCES tab    | Badge                                  |
| `[DASHBOARD:id]`                                 | Page-level — opens dashboard panel       | Side-panel trigger (page concern, NOT render) |
| `[REF:table:row]`                                | shared                                   | Reference link                         |
| `[CAMPAIGN_PROPOSAL:name\|segment\|disc\|aud]`   | Project-page only (gated by Strategist)  | Orange-bordered proposal card         |
| `[UP:+N%]` / `[DOWN:-N%]` / `[FLAT:N]`           | `formatCell()` (still duplicated — TODO) | Inline colored badges in table cells  |

### 2.2 User bubble — canonical class location

**Canonical CSS:** `frontend/src/lib/chat/ChatMessageList.svelte:871-882` (`.bubble-user`).

```css
.bubble-user {
  background: var(--pw-accent-soft, rgba(201,99,66,0.12));
  color: var(--pw-ink);
  border: 1px solid var(--pw-accent-soft, rgba(201,99,66,0.18));
  border-radius: 14px 14px 4px 14px;
  padding: 8px 14px;
  max-width: min(560px, 60%);
  width: fit-content;
  word-wrap: break-word;
  font-size: 14px;
  line-height: 1.5;
}
```

**Mirror in project page:** `frontend/src/routes/project/[slug]/+page.svelte:2742-2759` applies the same visual rules via `:global(.user-bubble), :global(.bubble-user), :global(.msg-user), :global(.user-msg-bubble)` so legacy class names still resolve. This page-level mirror is **the one allowed exception** and is documented in `STYLEGUIDE.md §Chat tag rendering mirror rule`.

### 2.3 Markdown / table helpers (canonical)

All three live in `frontend/src/lib/chat/markdown.ts` (imported by `ChatMessageList`):

- `markdownToHtml(text)`
- `parseMarkdownTables(text)`
- `tableToCsv(table)`
- `hasNumericData(table)`
- `detectChartType(table)`

Pages that import these directly (chat/+page.svelte:4, project/[slug]/+page.svelte:6) are fine — same source. Page-local `formatCell()` and `generateChartCaption()` are NOT fine — see TODO §6.

---

## 3. Page-specific overrides — what is allowed

Allowed via **class composition**, never via `!important` or inline `style="border|background|color|border-radius"`:

| Page                  | Allowed override                                                        | How                                          |
|-----------------------|-------------------------------------------------------------------------|----------------------------------------------|
| `/ui/chat`            | Wider message column (Dash agent has more room)                         | Wrapper class `.dash-chat-wide`              |
| `/ui/project/{slug}`  | Mount `[CAMPAIGN_PROPOSAL:` card after assistant message                | Project-only feature (Strategist gated)      |
| `/ui/project/{slug}`  | Mount `[DASHBOARD:id]` side panel                                        | Page state, not render                        |
| `/ui/project/{slug}`  | Legacy `.user-bubble`/`.msg-user` class aliases                          | `:global()` mirror per §2.2                   |

Everything else (KPI tiles, verdict cards, confidence bars, impact summary, related questions, user-bubble visual, markdown rendering, table cell formatting) MUST come from `ChatMessageList.svelte` unchanged.

---

## 4. Embed widget — intentional divergence

**File:** `dash/embed/widget.js` (648 LOC, vanilla JS, shadow DOM isolated).

The embed widget renders chat on a 3rd-party site and intentionally diverges from `ChatMessageList.svelte`:

- No SvelteKit runtime — vanilla JS, ~10 KB gzipped.
- Shadow DOM CSS isolation (`/api/embed/widget.js`).
- **No auth context** — operates on per-embed public/HMAC/JWT credentials.
- **Reduced feature set:** no KPI tiles, no verdict cards, no inline charts, no SOURCES tab, no CHART tab.
- **Reason:** embed surface targets marketing/docs use cases; full data-tab UI would bloat bundle size and expose internals.
- Server-driven theme via `/api/embed/config/{id}` (primary_color, logo_url, welcome_msg, position).

Divergence is by design and stays. Do NOT try to merge widget.js into ChatMessageList.

---

## 5. Lint rule (enforcement)

**Script:** `frontend/scripts/check-chat-mirror.mjs`
**Wired as:** `npm run style:audit` (chained after style-audit) and `npm run chat:audit` (standalone).

Detects:

1. `<div class="bubble-user">` (or class list containing `bubble-user`) in any `.svelte` file OUTSIDE `src/lib/chat/`, `src/routes/+page.svelte` (landing preview), or `src/routes/+layout.svelte` (the scroll observer).
2. Page-level parsing of `[VERDICT:`, `[KPI:`, `[CONFIDENCE:`, `[IMPACT:` regex outside `src/lib/chat/`. Pre-render stripping (`.replace(/\[KPI:...\]/g, '')`) is allowed; visual rendering is not.
3. Duplicate `formatCell` / `generateChartCaption` function definitions outside `src/lib/chat/`.

Failures print file path, line, anti-pattern, and the canonical location to use. Non-zero exit on violation.

ESLint flat-config rule fragment (place in repo root when ESLint adopted):

```js
// eslint.config.js (when ESLint is wired)
export default [
  {
    files: ['frontend/src/routes/**/*.svelte'],
    rules: {
      'no-restricted-syntax': ['warn', {
        selector: 'JSXAttribute[name.name="class"][value.value=/\\bbubble-user\\b/]',
        message: 'bubble-user belongs in lib/chat/ChatMessageList.svelte. Use <ChatMessageList> instead.'
      }]
    }
  }
];
```

The vanilla audit script in `frontend/scripts/check-chat-mirror.mjs` runs today; ESLint rule above is informational only (Svelte AST quirks).

---

## 6. Migration TODO — chat-renderer consolidation

Items below are flagged for future consolidation but not blocking. Each was discovered during the 2026-05-17 audit (Issue #29).

- [ ] **Hoist `formatCell()` to `lib/chat/markdown.ts`** — currently duplicated three places:
  - `frontend/src/lib/chat/ChatMessageList.svelte:177`
  - `frontend/src/routes/chat/+page.svelte:816`
  - `frontend/src/routes/project/[slug]/+page.svelte:269`
  Logic for `[UP:` / `[DOWN:` / `[FLAT:` badges and trend-arrow coloring is identical. Move to shared helper, import in all three.

- [ ] **Hoist `generateChartCaption()` to `lib/chat/markdown.ts`** — same duplication pattern:
  - `frontend/src/lib/chat/ChatMessageList.svelte:141`
  - `frontend/src/routes/chat/+page.svelte:778`
  - `frontend/src/routes/project/[slug]/+page.svelte:212`

- [ ] **`[CLARIFY:` parsing** lives in both page files (`chat/+page.svelte:772`, `project/[slug]/+page.svelte:1592`). Move parse + render to `ChatMessageList.svelte` and pass click handler as prop.

- [ ] **`[RELATED:` split logic** at `project/[slug]/+page.svelte:204` mirrors logic already inside `ChatMessageList`. Remove from page once verified component handles it for project chat too.

- [ ] **`[CAMPAIGN_PROPOSAL:` rendering** at `project/[slug]/+page.svelte:1762` is project-specific (Customer Strategist agent). Keep page-level for now; revisit when/if Dash Agent gains the Strategist.

- [ ] **`[DASHBOARD:id]` side-panel trigger** at `project/[slug]/+page.svelte:748` is a page-state concern (opens DashboardPanel), not a render concern. Stay as-is.

- [ ] **Legacy `:global(.user-bubble) ... { ... }` block** at `project/[slug]/+page.svelte:2742-2759` can be deleted once we audit that no message ever ships with the legacy class names. Mirror rule documented in STYLEGUIDE.md until then.

- [ ] **Embed widget tag support** — widget.js currently renders plain text. If user demand grows, consider porting `[KPI:` and `[CONFIDENCE:` (visual-only, no SOURCES tab) into widget.js as a thin parallel implementation. Keep ChatMessageList as the source-of-truth spec.

- [ ] **Unify imports** — both pages already import `markdownToHtml` etc. from `$lib/chat/markdown`. Confirm `chat/+page.svelte:4` and `project/[slug]/+page.svelte:6` import the exact same set; remove dead duplicates from page modules after §6 items 1-3 ship.

---

## 7. Bug-fix workflow (the point of consolidation)

Before this doc, a bug like "user bubble background wrong" took **3 patches** — one per surface. Now:

1. Patch `frontend/src/lib/chat/ChatMessageList.svelte` (canonical).
2. Both `/ui/chat` and `/ui/project/{slug}` pick it up.
3. Embed widget intentionally untouched (different design surface).
4. Run `npm run style:audit` — fails if anyone reintroduced page-level renderers.
5. Confirm with the mirror rule in `STYLEGUIDE.md`.

Tag-rendering bugs follow the same flow.

---

## 8. Quick reference

- Canonical renderer: `frontend/src/lib/chat/ChatMessageList.svelte`
- Canonical helpers: `frontend/src/lib/chat/markdown.ts`
- Canonical user-bubble CSS: `ChatMessageList.svelte:871-882`
- Style guide mirror rule: `frontend/STYLEGUIDE.md §Chat tag rendering mirror rule`
- Lint script: `frontend/scripts/check-chat-mirror.mjs`
- Audit command: `npm run style:audit` (from `frontend/`)
- Embed widget (intentional fork): `dash/embed/widget.js`
