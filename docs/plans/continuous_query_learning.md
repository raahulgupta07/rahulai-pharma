# Continuous Query Learning — improvement plan

**Status:** P1–P6 ALL SHIPPED (v1.21.0 P1; v1.22.0 P2–P6). **Date:** 2026-06-12.

> **P2–P6 done (2026-06-12, v1.22.0).** `dash/learning/query_bank.py` = `recall_similar`/`recall_similar_sync` (Mode-2 hint) + `try_query_bank_serve` (Mode-1 bypass: NN→schema-guard→re-run SQL live via `verified_reward._run_rows`→`cache_curator._build_card`, thread-isolated NN since chat loop is async). **P2** recall tool `recall_similar_queries` wired in `build.py` (non-store-locked, `tools.append`) + nudge in `instructions.py` — BEST-EFFORT (agent chooses; model often skips, like every soft nudge). **P4** Mode-1 serve lane in `app/projects.py project_chat` after metric_shortcut (verified: exact proven Q → 36ms live re-run, fresh numbers, zero agent LLM, `learned:true`). **P3** `dash/learning/query_curator.py` (approve/reject/promote/demote + `run_query_curator` verify-and-promote candidates + `demote_on_negative_feedback` + `bank_stats`/`list_patterns`) + `app/query_bank_api.py` (super-admin endpoints) + daemon `dash/cron/query_curator_daemon.py` (default OFF, `QUERY_CURATOR_ENABLED=1`) + UsagePanel **Query Bank** tab (repeat-rate/would-serve KPIs + review-gate approve/reject/promote/demote + curate/generalize buttons). **P5** `dash/learning/query_generalize.py` `propose_generalizations` (cluster by table+SQL-shape → LLM emits ONE parameterized template → writes pending, review-gated; the agent fills the slot via Mode-2 — no template engine). **P6** `fold_proven_into_training` hooked into `app/upload.py` training-complete (proven chat patterns → `dash_training_qa`). 2 bugs caught in test: `build.py` recall guard used `_os` before its import (→local `_os_qr`); `try_query_bank_serve` used `asyncio.run` in a running loop (→ThreadPoolExecutor). Lifecycle verified: capture→pending→approve→candidate→promote(verify 1,360,217)→proven→Mode-1 serve 36ms.
>
> **FOLLOW-UPS:** recall-tool firing is model-discretion (low rate) — consider injecting top proven patterns into per-turn context if recall usage stays low; fold mig 188 into baseline schema.sql + migrations_seed; Mode-1 currently lives in `project_chat` only (the main UI path) — `super_chat` has neither shortcuts nor Mode-1 (acceptable; it's the secondary global-chat path).

> **P1 done (2026-06-12).** Migration 188 (extends `dash_query_patterns` + `dash_query_bank_shadow`). `dash/learning/query_capture.py` = `capture_query_async` (fire-and-forget thread, dedupe-upsert on `question_norm`, schema_hash stamp, embed→`dash_vectors namespace='qbank'`) + `shadow_match` (embed→NN→log, serves nothing). Capture hooked in `dash/tools/build.py` `run_sql_query` success path (reads `CUR_QUESTION` contextvar). `CUR_QUESTION` set + `shadow_match` scheduled in BOTH `app/projects.py` `project_chat` AND `app/main.py` `super_chat` (the analyst path actually runs through super_chat — it builds its own team, does NOT call project_chat; wiring only project_chat captured nothing). Verified live: question→SQL captured (`source='chat'`, `status='pending'`), paraphrase shadow-matched at sim 0.928 (`would_serve=f`, below 0.96 + not proven). **Next: let it run, read repeat-rate from `dash_query_bank_shadow`, then decide P2.** Follow-up: fold mig 188 into `db/baseline/schema.sql` recon + `migrations_seed.sql` (currently relies on the boot migration runner, which is correct for cold installs but slower).
**Goal:** make the agent *learn from real questions* — capture the SQL it writes in live chat, verify it, reuse it (as an LLM hint and/or a fast lane), and fold it into the next retrain. Faster replies + a corpus of what users actually ask. **No hardcoded SQL — the LLM writes/adapts every query; the system only remembers and ranks its work.**

---

## 0. Why (the gap, verified against code)

The learning loop is **open**: training writes `dash_query_patterns` (Q→SQL, 84 rows, `source='training'`), the agent reads them as hints, users ask questions, the agent's live SQL is **never written back**. `dash_traces` logs span names, not SQL text. So the product only knows the questions training *guessed*, never the ones people *ask*.

We already have the parts to close it:
- `dash_query_patterns` — Q→SQL store, has `source`, `uses`, `last_used`, `version`, `parent_id` (lineage). Currently UI-display only.
- `metric_shortcut` (`dash/learning/verified_reward.py`) — **already** re-runs proven SQL live + formats in code + schema-guards. The serve mechanism exists.
- `schema_guard.py` — reusable `schema_hash_for_sql()` / `live_schema_hash()` (col-hash drift detection).
- `cache_curator.py` + daemon — leader LLM that judges stable-vs-volatile + verifies SQL read-only.
- Review gate / Intern Rule — `status='pending'`→admin approve (used for chat-learned memory facts).
- bilingual twin + retrain hooks — `regen_bilingual.py`, fold-into-training on force-retrain.

**Genuinely missing:** (1) a capture hook on the agent's `run_sql_query`; (2) a recall/serve path that consults captured patterns at query time. Both extend existing infra — **no new table required** (extend `dash_query_patterns`).

---

## 1. Design principle — LLM stays in control

Two ways the bank plugs in. Default = **Mode 2** (LLM-driven). Mode 1 = opt-in fast lane for verbatim repeats only.

- **Mode 2 (primary) — recall as a TOOL/hint.** New tool `recall_similar_queries(question)` returns top-3 proven SQL for similar past questions. The agent decides whether to adapt one or write fresh. Same trust pattern as `dash_memories`/brain context: retrieved → LLM decides. Degrades gracefully (bad/missing hint → agent ignores it).
- **Mode 1 (opt-in) — bypass for exact hits.** sim ≥ 0.96 + `proven` + schema-OK → re-run stored SQL, format in code, zero LLM. Only for questions identical to a verified past one (nothing to decide).

---

## 2. Schema changes (extend `dash_query_patterns`, no new table)

`ALTER TABLE public.dash_query_patterns` add (all `IF NOT EXISTS`, idempotent seed in `app/auth.py` style):
- `status TEXT DEFAULT 'proven'` — `'pending' | 'candidate' | 'proven' | 'demoted'`. Training rows backfill `'proven'`; chat-captured start `'pending'`.
- `schema_hash TEXT` — stamped via `schema_hash_for_sql()` at capture; checked at serve.
- `rows_returned INT`, `last_latency_ms INT` — capture telemetry.
- `success BOOL DEFAULT true` — did the SQL execute clean (no error)?
- `embedding` lives in `dash_vectors namespace='qbank'` (reuse the namespace pattern; `source_id` = pattern id, HNSW cosine). NOT a column.
- (existing `source`, `uses`, `last_used`, `version`, `parent_id` reused as-is.)

Migration: `db/migrations/188_query_bank.sql` + fold into `db/baseline/schema.sql` recon block + `migrations_seed.sql` (cold-install durability — same drill as 179/180).

---

## 3. Capture hook (the one new piece)

**Site:** `dash/tools/build.py` `run_sql_query` wrapper (line 73) — already wraps every agent SQL call, already trace-spanned. After a SUCCESSFUL execution (rows returned, no error), fire-and-forget (thread/async, never blocks the reply):

```
capture_query(project_slug, question=<current turn question>, sql=query,
              tables=sql_source_tables(query), rows_returned=n,
              latency_ms=dt, source='chat', status='pending')
  → INSERT dash_query_patterns (dedupe on normalized question+sql; bump `uses` if exists)
  → stamp schema_hash = schema_hash_for_sql(slug, sql)
  → embed question → dash_vectors namespace='qbank'
```
- The current-turn question is available in the chat context (thread it into the toolkit or read from a contextvar like `REPLY_LANG`).
- Dedupe: normalized-question + sql hash. Repeat → `uses++`, `last_used=now()`, don't duplicate.
- Gated `QUERY_CAPTURE_DISABLED=1`. Fail-soft (capture failure never touches the user path).
- **Only capture clean successes.** Errored/empty SQL is not learning material.

---

## 4. Recall + serve

**Mode 2 tool** (`dash/tools/recall_queries.py`, wired in `build.py` next to `run_sql_query`):
```
recall_similar_queries(question) →
   embed → NN in qbank (sim ≥ 0.90) → schema-guard filter →
   return top-3 {question, sql, status, uses} as a HINT STRING (not executed)
```
- Instruction nudge in `instructions.py`: *"Before writing analytical SQL, call `recall_similar_queries`. If a proven query fits, adapt and run it; else write your own."*
- Store-locked keys: NOT exposed (raw-SQL surface stays stripped) — Mode 2 is for global/analyst paths only.

**Mode 1 lane** (opt-in, `projects.py` after metric_shortcut ~line 1377):
```
embed → NN qbank (sim ≥ 0.96, status='proven') → schema-guard → re-run SQL live → format in code
   miss/drift/candidate → fall through (Mode 2 / agent)
```
Reuses metric_shortcut's `_run_rows` + code-format path.

---

## 5. Trust lifecycle (reuse curator + review gate + feedback)

```
captured (source='chat') ─► status='pending' ──[admin approve]──► candidate
   │ (review gate, Intern Rule — same UI as memory facts)
   ▼
candidate ──[ uses≥N AND (👍 OR eval-pass OR curator-verified) ]──► proven
   │                                                                  │
   │ 👎 / correction (dash_feedback.correction)                       │ schema drift
   ▼                                                                  ▼
demoted (stop serving; store corrected SQL as new version)      auto-evict → re-learn
```
- **Curator daemon** (`cache_curator.py`) extended to ALSO scan `dash_query_patterns WHERE source='chat' AND status IN ('pending','candidate')`: re-run SQL read-only, judge stable-vs-volatile, promote/demote. Leader-elected, OFF by default (`QUERY_CURATOR_ENABLED=1`).
- **Mode 1 serves `proven` only.** Mode 2 may hint `candidate` (LLM validates anyway).

---

## 6. Fold into learning (what makes it LEARNING, not caching)

- **Retrain hook** (`app/upload.py` training-complete): captured `proven` chat patterns are already in `dash_query_patterns` → seed them into the next training pass as Q&A → they harden into goldens (survive retrain, get brain/memory/bilingual support).
- **Bilingual twin** (`regen_bilingual.py`): a captured EN question auto-twins to MY (`parent_id` link) → Burmese variants hit the same SQL.
- **Coverage audit** (AIS-OS Four-Cs): captured questions = the corpus that surfaces blind spots ("asked often, no proven SQL").

---

## 7. Surfaces (visible as learning, not hidden plumbing)

- **Workspace → Intelligence → Proven Queries:** captured rows show a `🤖 learned from chat` badge + status chip; admin approve/reject (review gate UI, same as memory facts).
- **Training run / Robot Watching tab:** "learned N query patterns from live questions."
- **Usage panel:** bank hit-rate, candidate→proven funnel.

---

## 8. Rollout (measure before serving — de-risked)

| Phase | Ships | Risk | Decides |
|---|---|---|---|
| **P1 Capture + Shadow** | capture hook (`source='chat'`, `pending`) + Mode-2 recall in LOG-ONLY (match, record "would've hit", never serve) | ~none (async write, no serve) | repeat-rate %: would reused SQL have returned correct rows? |
| **P2 Recall tool ON** | `recall_similar_queries` live hint (Mode 2), proven+candidate | low (LLM validates hint) | does adapt-from-hint cut round-trips / errors? |
| **P3 Curator + review gate** | extend curator to chat patterns; admin approve UI; promotion funnel | low | candidate→proven quality |
| **P4 Mode-1 bypass** | exact-hit fast lane, proven only | medium (serves without LLM — correctness exposure on drug stock) | flip only if P1 hit-rate high |
| **P5 Parameterize (v2)** | LLM clusters similar patterns → one slotted SQL + slot-extractor | medium | 1 template covers 100s of Qs |
| **P6 Fold-into-retrain** | seed proven chat patterns into training | low | permanent learning |

**P1 is independently valuable even if we never serve:** the corpus of real question→SQL feeds better training + coverage audit, regardless of the speed bet. Build P1, read the number, then decide P2–P4.

---

## 9. Risks / landmines

- **Wrong SQL served to a pharmacist about drug stock = high stakes.** Mode-1 bypass only on `proven` + schema-OK + high sim; everything else keeps the LLM in the loop. Default to Mode 2.
- **Schema drift** — `schema_hash` guard auto-evicts (reuse existing). Row-count changes do NOT evict (SQL re-runs live).
- **Noisy capture** — only clean successes; dedupe by normalized question+sql; `pending` until reviewed/verified.
- **Hot-path bloat** — already 3 short-circuit lanes + scope gate. Mode 1 adds a 4th — gate it, document the order, keep Mode 2 (a tool, not a lane) as the default so the serve path stays 3 lanes.
- **Capture must NOT block reply** — async/fire-and-forget, fail-soft, gated.

---

## 10. New code vs reused

| Need | New? | Reuse |
|---|---|---|
| Store | — | extend `dash_query_patterns` |
| Embedding / NN | — | `dash_vectors` namespace='qbank' |
| Re-run SQL live + format | — | `metric_shortcut` path |
| Schema drift | — | `schema_guard.py` |
| Promote/verify | — | `cache_curator.py` daemon |
| Review gate UI | — | memory-fact approve/reject |
| Bilingual / retrain fold | — | `regen_bilingual.py`, upload retrain hook |
| **Capture hook** | ✅ | `build.py:73` post-success callback |
| **Recall tool** | ✅ | new `recall_queries.py` (thin) |
| **Migration 188** | ✅ | ALTER + seed + baseline recon |

**~3 genuinely new pieces.** Everything else is wiring existing infra into a closed loop.
