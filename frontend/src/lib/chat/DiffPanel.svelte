<script lang="ts">
  /**
   * Reusable diff panel for skill / metric / model versions.
   *
   * Props:
   *   leftLabel    — label shown above LEFT column
   *   rightLabel   — label shown above RIGHT column
   *   leftData     — JSON snapshot (Record<string, unknown>)
   *   rightData    — JSON snapshot
   *   sqlFields    — explicit field names to render as unified SQL diff (besides auto-detect)
   *   jsonFields   — explicit field names to render as field-level JSON (besides auto-detect)
   *   compact      — compact mode for chat-bubble embedding (smaller padding/fonts)
   *   initialView  — "side" | "unified"  (default: "side")
   *
   * Diff logic:
   *   - Field-level recursive compare with color highlights:
   *       same     → muted
   *       added    → green (#16a34a)
   *       removed  → red   (#b3261e)
   *       changed  → amber (#a06000)
   *   - SQL/text fields → unified diff (red removed, green added)
   *   - Auto-detect SQL: field name contains "sql" or ends in "_template" or "query_template"
   */

  type AnyVal = string | number | boolean | null | undefined | Record<string, unknown> | unknown[];

  let {
    leftLabel = 'Before',
    rightLabel = 'After',
    leftData = {} as Record<string, unknown>,
    rightData = {} as Record<string, unknown>,
    sqlFields = [] as string[],
    jsonFields = [] as string[],
    compact = false,
    initialView = 'side' as 'side' | 'unified',
  } = $props();

  let view = $state<'side' | 'unified'>(initialView);

  // ── Helpers ───────────────────────────────────────────────────────────
  function isSqlField(name: string): boolean {
    if (sqlFields.includes(name)) return true;
    const n = (name || '').toLowerCase();
    return n.includes('sql') || n.endsWith('_template') || n === 'query_template';
  }

  function isJsonField(name: string): boolean {
    if (jsonFields.includes(name)) return true;
    // any nested object/array
    const lv = (leftData as Record<string, unknown>)[name];
    const rv = (rightData as Record<string, unknown>)[name];
    return (lv && typeof lv === 'object') || (rv && typeof rv === 'object');
  }

  function fmtVal(v: AnyVal): string {
    if (v === null || v === undefined) return '∅';
    if (typeof v === 'string') return v;
    if (typeof v === 'object') {
      try { return JSON.stringify(v, null, 2); } catch { return String(v); }
    }
    return String(v);
  }

  function eq(a: AnyVal, b: AnyVal): boolean {
    if (a === b) return true;
    if (a === null || b === null || a === undefined || b === undefined) return false;
    if (typeof a !== typeof b) return false;
    if (typeof a === 'object') {
      try { return JSON.stringify(a) === JSON.stringify(b); } catch { return false; }
    }
    return false;
  }

  // ── Field-level diff (entries) ──────────────────────────────────────
  type Entry = {
    field: string;
    status: 'same' | 'added' | 'removed' | 'changed';
    left: AnyVal;
    right: AnyVal;
    isSql: boolean;
  };

  const entries = $derived.by((): Entry[] => {
    const out: Entry[] = [];
    const allKeys = Array.from(new Set([
      ...Object.keys(leftData || {}),
      ...Object.keys(rightData || {}),
    ])).sort();
    for (const k of allKeys) {
      const inL = k in (leftData || {});
      const inR = k in (rightData || {});
      const lv = (leftData as Record<string, unknown>)[k] as AnyVal;
      const rv = (rightData as Record<string, unknown>)[k] as AnyVal;
      let status: Entry['status'];
      if (inL && !inR) status = 'removed';
      else if (inR && !inL) status = 'added';
      else if (eq(lv, rv)) status = 'same';
      else status = 'changed';
      out.push({ field: k, status, left: lv, right: rv, isSql: isSqlField(k) });
    }
    return out;
  });

  const summary = $derived.by(() => {
    const s = { same: 0, added: 0, removed: 0, changed: 0 };
    for (const e of entries) s[e.status]++;
    return s;
  });

  // ── Unified diff for SQL ──────────────────────────────────────────────
  // Tiny LCS-based unified diff (line granularity).
  function unifiedDiff(a: string, b: string): { type: 'ctx' | 'add' | 'del'; text: string }[] {
    const al = (a || '').split('\n');
    const bl = (b || '').split('\n');
    // Build LCS table
    const m = al.length, n = bl.length;
    const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = m - 1; i >= 0; i--) {
      for (let j = n - 1; j >= 0; j--) {
        dp[i][j] = al[i] === bl[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    const out: { type: 'ctx' | 'add' | 'del'; text: string }[] = [];
    let i = 0, j = 0;
    while (i < m && j < n) {
      if (al[i] === bl[j]) { out.push({ type: 'ctx', text: al[i] }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ type: 'del', text: al[i] }); i++; }
      else { out.push({ type: 'add', text: bl[j] }); j++; }
    }
    while (i < m) { out.push({ type: 'del', text: al[i++] }); }
    while (j < n) { out.push({ type: 'add', text: bl[j++] }); }
    return out;
  }

  function copyVal(v: AnyVal): void {
    try { navigator.clipboard?.writeText(fmtVal(v)); } catch { /* no-op */ }
  }
</script>

<div class="diff-root" class:compact>
  <header class="diff-head">
    <div class="diff-labels">
      <span class="lbl lbl-left">{leftLabel}</span>
      <span class="lbl-arrow">→</span>
      <span class="lbl lbl-right">{rightLabel}</span>
    </div>
    <div class="diff-summary">
      <span class="sum-pill sum-same">●{summary.same}</span>
      <span class="sum-pill sum-added">+{summary.added}</span>
      <span class="sum-pill sum-removed">−{summary.removed}</span>
      <span class="sum-pill sum-changed">~{summary.changed}</span>
    </div>
    <div class="diff-toggle">
      <button
        class:active={view === 'side'}
        onclick={() => (view = 'side')}
        type="button"
      >side</button>
      <button
        class:active={view === 'unified'}
        onclick={() => (view = 'unified')}
        type="button"
      >unified</button>
    </div>
  </header>

  <div class="diff-body">
    {#each entries as e (e.field)}
      <article class="row row-{e.status}">
        <div class="row-field">
          <span class="field-name">{e.field}</span>
          <span class="field-status status-{e.status}">{e.status}</span>
          {#if e.isSql}<span class="field-tag">sql</span>{/if}
        </div>

        {#if e.status === 'same'}
          <div class="row-same"><pre>{fmtVal(e.left)}</pre></div>
        {:else if e.isSql && (typeof e.left === 'string' || typeof e.right === 'string')}
          <!-- SQL unified diff -->
          <div class="sql-diff">
            {#each unifiedDiff(typeof e.left === 'string' ? (e.left as string) : '', typeof e.right === 'string' ? (e.right as string) : '') as line, i (i)}
              <div class="sql-line sql-{line.type}">
                <span class="sql-marker">{line.type === 'add' ? '+' : line.type === 'del' ? '−' : ' '}</span><span class="sql-text">{line.text}</span>
              </div>
            {/each}
          </div>
        {:else if view === 'side'}
          <div class="row-side">
            <div class="side side-left">
              <pre>{fmtVal(e.left)}</pre>
              <button class="copy-btn" type="button" onclick={() => copyVal(e.left)} title="Copy">⧉</button>
            </div>
            <div class="side side-right">
              <pre>{fmtVal(e.right)}</pre>
              <button class="copy-btn" type="button" onclick={() => copyVal(e.right)} title="Copy">⧉</button>
            </div>
          </div>
        {:else}
          <!-- unified, non-sql: show as before/after stacked -->
          <div class="row-unified">
            <div class="uline uline-del"><span class="uline-marker">−</span><pre>{fmtVal(e.left)}</pre></div>
            <div class="uline uline-add"><span class="uline-marker">+</span><pre>{fmtVal(e.right)}</pre></div>
          </div>
        {/if}
      </article>
    {/each}

    {#if entries.length === 0}
      <p class="empty">No fields to compare.</p>
    {/if}
  </div>
</div>

<style>
  .diff-root {
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 12.5px;
    color: #1f1c17;
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    overflow: hidden;
  }
  .diff-root.compact { font-size: 11.5px; }

  .diff-head {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    background: #faf7f0;
    border-bottom: 1px solid #e8e3d6;
    flex-wrap: wrap;
  }
  .diff-labels { display: flex; align-items: center; gap: 8px; }
  .lbl { font-weight: 600; }
  .lbl-left { color: #b3261e; }
  .lbl-right { color: #16a34a; }
  .lbl-arrow { color: #999; }

  .diff-summary { display: flex; gap: 6px; margin-left: auto; }
  .sum-pill {
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
    border: 1px solid transparent;
  }
  .sum-same { background: #f0ebde; color: #777; }
  .sum-added { background: rgba(22,163,74,0.12); color: #16a34a; }
  .sum-removed { background: rgba(179,38,30,0.12); color: #b3261e; }
  .sum-changed { background: rgba(160,96,0,0.12); color: #a06000; }

  .diff-toggle { display: flex; border: 1px solid #d6d1c2; border-radius: 4px; overflow: hidden; }
  .diff-toggle button {
    padding: 3px 10px;
    font-size: 11px;
    background: #fff;
    border: 0;
    cursor: pointer;
    color: #555;
  }
  .diff-toggle button:hover { background: #f7f3e9; }
  .diff-toggle button.active { background: #c96342; color: #fff; }

  .diff-body { display: flex; flex-direction: column; }

  .row {
    border-bottom: 1px solid #f0ebde;
    padding: 10px 12px;
  }
  .row:last-child { border-bottom: 0; }
  .compact .row { padding: 7px 10px; }

  .row-same { opacity: 0.65; }
  .row-added { background: rgba(22,163,74,0.04); }
  .row-removed { background: rgba(179,38,30,0.04); }
  .row-changed { background: rgba(160,96,0,0.04); }

  .row-field {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }
  .field-name { font-weight: 600; color: #1f1c17; }
  .field-status {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 3px;
  }
  .status-same { background: #f0ebde; color: #999; }
  .status-added { background: rgba(22,163,74,0.16); color: #16a34a; }
  .status-removed { background: rgba(179,38,30,0.16); color: #b3261e; }
  .status-changed { background: rgba(160,96,0,0.16); color: #a06000; }
  .field-tag {
    font-size: 9px;
    background: #1a1614;
    color: #c96342;
    padding: 1px 5px;
    border-radius: 2px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  pre {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: inherit;
    font-size: inherit;
  }

  .row-side {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .side {
    position: relative;
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 4px;
    padding: 8px 28px 8px 10px;
    min-height: 22px;
  }
  .side-left { border-left: 3px solid #b3261e; }
  .side-right { border-left: 3px solid #16a34a; }
  .copy-btn {
    position: absolute;
    top: 4px;
    right: 4px;
    border: 0;
    background: transparent;
    cursor: pointer;
    color: #999;
    font-size: 13px;
    padding: 2px 4px;
  }
  .copy-btn:hover { color: #c96342; }

  .row-unified { display: flex; flex-direction: column; gap: 4px; }
  .uline {
    display: flex;
    gap: 8px;
    padding: 6px 10px;
    border-radius: 4px;
  }
  .uline-marker { font-weight: 700; min-width: 12px; }
  .uline-del { background: rgba(179,38,30,0.10); color: #b3261e; }
  .uline-add { background: rgba(22,163,74,0.10); color: #16a34a; }

  .sql-diff {
    background: #1a1614;
    border-radius: 4px;
    padding: 8px 0;
    overflow-x: auto;
  }
  .sql-line {
    display: flex;
    gap: 8px;
    padding: 1px 12px;
    font-family: ui-monospace, monospace;
    font-size: 12px;
  }
  .sql-marker { color: #888; min-width: 12px; }
  .sql-text { white-space: pre; color: #e8e3d6; }
  .sql-ctx { color: #888; }
  .sql-add { background: rgba(22,163,74,0.18); }
  .sql-add .sql-marker, .sql-add .sql-text { color: #4ade80; }
  .sql-del { background: rgba(179,38,30,0.18); }
  .sql-del .sql-marker, .sql-del .sql-text { color: #f87171; }

  .empty {
    padding: 16px;
    text-align: center;
    color: #999;
    font-size: 12px;
  }
</style>
