# Root Cause Analysis — Embed Widget Chat (store-20064-CCGMLM)

**Date:** 2026-06-16
**Surface:** Embed widget (`emb_2Gpd3eIqAsC1c-55jlPuTA`, site `20064-CCGMLM`)
**Reporter:** User (screenshot, two-turn session)
**Severity:** High — model returned a wrong number (Q1) and fully fabricated data (Q2).

---

## 1. Symptoms

| Turn | Question | Answer shown | Verdict |
|------|----------|--------------|---------|
| Q1 | "How many unique products (SKUs) do we have?" | "24,971 units in stock … value 206,744,200 MMK" | **Wrong field** — answered total units, not SKU count |
| Q2 | "Show me the top products in this category" | AMLODIPINE 5MG 1,240u / METFORMIN 980u / ATORVASTATIN 850u / LOSARTAN 720u / OMEPRAZOLE 640u, with costs | **Fabricated** — none of this exists in the DB |

Also visible: duplicated boilerplate text in the rendered answer ("by category?SKUs by category?", repeated "highest to lowest").

---

## 2. Evidence (production logs + live DB)

### Q1 — tool returned correct data; model picked the wrong number
The stock-summary tool (`dash/tools/pharma_shop_tool.py`) returned:
```json
{"ok": true, "site": "20064-CCGMLM", "category": null,
 "total_stock_qty": 24971, "unique_articles": 1721,
 "total_inventory_value": 206744200.0}
```
- Correct SKU count = **`unique_articles` = 1721**.
- Model reported **`total_stock_qty` = 24,971** (physical units) instead.
- Right tool, right call, **wrong field selected** for a "unique products / SKU" intent.

### Q2 — no category in context → empty tool result → hallucination
Every tool call in the turn carried `"category": null`. "this category" was never bound from the prior turn. The model then ran a product search using the **category label as a free-text query**:
```json
{"ok": true, "site": "20064-CCGMLM",
 "query": "5102-PRESCRIPTION MEDICINE",
 "count": 0, "results": [], "state": "not_found", "message": "Not Found."}
```
- Tool returned **0 rows / `not_found`**.
- Instead of saying "no results" or "which category?", the model **invented** five plausible drugs with round quantities and fake costs.
- The 74s "Thinking" = repeated tool retries, all empty, followed by confabulation.

### Contributing bug A — `search_all` not bound in embed path
```
ERROR    Function search_all not found
```
- `dash/instructions.py:948` instructs the agent: **"ALWAYS call `search_all` BEFORE writing SQL."**
- `app/embed_public.py:2058` only maps a *display label* for `search_all` — the actual tool is **not registered** in the embed (single-analyst) toolset.
- Net: the model obeys the instruction, calls a non-existent function, wastes a hop, and falls back to the bad category-label query.

### Contributing bug B — tool-call telemetry INSERT is dead
```
psycopg.errors.UndefinedColumn
INSERT INTO dash.dash_tool_utility_scores
  (tool_name, agent, project_slug, user_id, args_hash, success,
   latency_ms, error_class, error_message, feedback, retry_count, ts) ...
```
- Code (`dash/tools/skill_refinery.py:139`) inserts **per-call** telemetry columns.
- Live `dash.dash_tool_utility_scores` has only the **aggregate** columns
  (`calls_30d, success_30d, avg_latency_ms, score`).
- Migration `085_dedup_tool_utility_scores.sql` step 2 (`ALTER TABLE … ADD COLUMN IF NOT EXISTS agent/user_id/args_hash/success/latency_ms/error_class/…`) **was never applied** to this DB (baseline regenerated without it).
- Result: every tool-call log flush fails → these tool errors were **invisible** in dashboards; the failure only surfaced because it threw in the request path.

---

## 3. Root causes (ranked)

1. **No anti-hallucination guard on empty/error/not_found tool results.**
   The model is free to invent rows when a tool returns `count:0` / `state:"not_found"` / error. This is the critical defect (Q2). It can silently emit fabricated business data.

2. **SKU/"unique products" intent not mapped to `unique_articles`.**
   Tool returns both `total_stock_qty` and `unique_articles`; nothing forces the count-of-distinct intent to the right field (Q1).

3. **No category-context carry on follow-ups.**
   "this category" had no antecedent; the agent neither inherited a prior category nor asked which one, and instead coerced a label into a product search.

4. **`search_all` instruction/toolset mismatch in embed path.**
   Instructions mandate a tool the embed agent doesn't have → wasted call + degraded fallback.

5. **Telemetry table schema drift (migration 085 not applied).**
   Tool-call errors never logged → these failures were undetectable until a user noticed bad output.

---

## 4. Recommended fixes

| # | Fix | File(s) | Priority |
|---|-----|---------|----------|
| 1 | When any tool returns `count:0` / `not_found` / `ok:false` / empty `results`, the agent MUST answer "no data found" (+ optional clarifying question) and is FORBIDDEN to invent rows. Add as a hard system rule AND a post-generation sanity check on embed answers. | `dash/instructions.py`, `app/embed_public.py` | P0 |
| 2 | Map "SKU / unique products / how many products" intent to `unique_articles`; tighten the tool field descriptions so the model can't confuse units vs distinct count. | `dash/tools/pharma_shop_tool.py`, `dash/instructions.py` | P1 |
| 3 | Carry the last category in session context for "this/that category" follow-ups; if still null, ask which category instead of guessing. | `app/embed_public.py` | P1 |
| 4 | Either register `search_all` in the embed single-analyst toolset, or gate the "ALWAYS call search_all" instruction so it is not emitted when the tool is absent. | `dash/team.py`, `app/embed_public.py`, `dash/instructions.py` | P1 |
| 5 | Apply migration 085 (or a new migration) so `dash.dash_tool_utility_scores` has the per-call telemetry columns; verify the flush succeeds so tool errors are logged again. | `db/migrations/`, live DB | P2 |
| 6 | Fix duplicated boilerplate in rendered embed answers (known model-side dup; trim repeated sentences post-stream). | `app/embed_public.py` / answer assembler | P2 |

---

## 5. Verification plan (after fixes)

- Q1: ask "how many unique products / SKUs" → must answer **1721** (not 24,971).
- Q2: ask "top products in this category" with no category set → must ask "which category?" or list real top SKUs from a real category; **must never** emit AMLODIPINE-style invented rows.
- Force a tool `not_found` → answer must say no data, zero fabricated rows.
- Check `dash.dash_tool_utility_scores` receives rows (no UndefinedColumn in logs).
- Grep `search_all not found` → zero occurrences in embed path.
