<script lang="ts">
  import { onMount } from 'svelte';

  type Preset = { id: string; label: string; description: string };
  type RunResult = {
    ok: boolean;
    columns: string[];
    rows: Record<string, any>[];
    row_count: number;
    truncated: boolean;
    elapsed_ms: number;
    generated_sql?: string;
    question?: string;
    preset?: string;
    label?: string;
  };

  let projectSlug = $state('');
  let q = $state('');
  let running = $state(false);
  let result = $state<RunResult | null>(null);
  let error = $state<string | null>(null);
  let showSql = $state(true);
  let presets = $state<Preset[]>([]);
  let bulkRunning = $state(false);
  let bulkMsg = $state<string | null>(null);

  function authHeaders(): HeadersInit {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('token') : null;
    return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
  }

  async function loadPresets() {
    try {
      const r = await fetch('/api/dataview/examples', { headers: authHeaders() });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      presets = j.presets || [];
    } catch (e: any) {
      // non-fatal
      console.warn('preset load failed', e);
    }
  }

  async function runNL() {
    if (!q.trim()) {
      error = 'enter a question';
      return;
    }
    running = true;
    error = null;
    result = null;
    try {
      const r = await fetch('/api/dataview/run', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ q: q.trim(), project_slug: projectSlug.trim() }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      result = j;
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      running = false;
    }
  }

  async function runPreset(name: string) {
    running = true;
    error = null;
    result = null;
    try {
      const r = await fetch(`/api/dataview/preset/${encodeURIComponent(name)}`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ project_slug: projectSlug.trim() }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      result = j;
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      running = false;
    }
  }

  function looksLikeStale(): boolean {
    if (!result || !result.rows || result.rows.length === 0) return false;
    const cols = (result.columns || []).map((c) => c.toLowerCase());
    return cols.includes('days_stale') || cols.some((c) => c.includes('stale'));
  }

  async function bulkArchive() {
    if (!result || !looksLikeStale()) return;
    if (!confirm(`Archive ${result.rows.length} stale tables?`)) return;
    bulkRunning = true;
    bulkMsg = null;
    try {
      const targets = result.rows
        .map((r) => ({
          schema: r.table_schema || r.schema,
          table: r.table_name || r.table,
        }))
        .filter((t) => t.schema && t.table);
      // Endpoint may not exist — graceful fallback.
      const r = await fetch('/api/tables/archive', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ targets }),
      });
      if (!r.ok) {
        bulkMsg = `archive endpoint returned ${r.status} (stub — wire to /api/tables/archive when ready)`;
      } else {
        const j = await r.json().catch(() => ({}));
        bulkMsg = `archived ${j.archived ?? targets.length} tables`;
      }
    } catch (e: any) {
      bulkMsg = `archive failed: ${e?.message || e}`;
    } finally {
      bulkRunning = false;
    }
  }

  function fmtCell(v: any): string {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }

  onMount(loadPresets);
</script>

<div class="dv-shell">
  <header class="dv-head">
    <h1>Dataview</h1>
    <p class="dv-sub">Obsidian-Dataview-style queries over warehouse metadata. SELECT-only, whitelisted, 10s timeout, 1000-row cap.</p>
  </header>

  <section class="dv-card">
    <label class="dv-row">
      <span class="dv-label">Project slug (optional)</span>
      <input class="dv-input" bind:value={projectSlug} placeholder="e.g. proj_demo_pharma" />
    </label>
    <label class="dv-row">
      <span class="dv-label">Natural-language query</span>
      <textarea
        class="dv-textarea"
        rows="3"
        bind:value={q}
        placeholder="e.g. tables not used in 30 days, or metrics with no last_used_at"
      ></textarea>
    </label>
    <div class="dv-actions">
      <button class="dv-btn dv-primary" disabled={running} onclick={runNL}>
        {running ? 'Running…' : 'Run'}
      </button>
      {#if error}
        <span class="dv-error">{error}</span>
      {/if}
    </div>
  </section>

  <section class="dv-card">
    <h2 class="dv-h2">Preset queries</h2>
    <div class="dv-chips">
      {#each presets as p}
        <button class="dv-chip" disabled={running} onclick={() => runPreset(p.id)} title={p.description}>
          {p.label}
        </button>
      {/each}
      {#if presets.length === 0}
        <span class="dv-muted">No presets loaded.</span>
      {/if}
    </div>
  </section>

  {#if result}
    <section class="dv-card">
      <div class="dv-result-head">
        <h2 class="dv-h2">
          Result
          <span class="dv-pill">{result.row_count} rows</span>
          <span class="dv-pill">{result.elapsed_ms} ms</span>
          {#if result.truncated}<span class="dv-pill dv-warn">truncated @ 1000</span>{/if}
        </h2>
        <div class="dv-result-actions">
          <button class="dv-btn" onclick={() => (showSql = !showSql)}>
            {showSql ? 'Hide SQL' : 'Show SQL'}
          </button>
          {#if looksLikeStale()}
            <button class="dv-btn dv-danger" disabled={bulkRunning} onclick={bulkArchive}>
              {bulkRunning ? 'Archiving…' : `Bulk archive (${result.rows.length})`}
            </button>
          {/if}
        </div>
      </div>

      {#if showSql && result.generated_sql}
        <pre class="dv-sql"><code>{result.generated_sql}</code></pre>
      {/if}

      {#if bulkMsg}
        <p class="dv-muted">{bulkMsg}</p>
      {/if}

      {#if result.rows.length === 0}
        <p class="dv-muted">No rows.</p>
      {:else}
        <div class="dv-table-wrap">
          <table class="dv-table">
            <thead>
              <tr>
                {#each result.columns as c}<th>{c}</th>{/each}
              </tr>
            </thead>
            <tbody>
              {#each result.rows as row}
                <tr>
                  {#each result.columns as c}
                    <td>{fmtCell(row[c])}</td>
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .dv-shell {
    max-width: 1080px;
    margin: 0 auto;
    padding: 24px 20px 80px;
    color: #e8e3d6;
    font-family: ui-sans-serif, system-ui, sans-serif;
  }
  .dv-head h1 {
    font-family: 'Source Serif Pro', Georgia, serif;
    font-size: 28px;
    margin: 0 0 4px;
    color: #f3eee0;
  }
  .dv-sub {
    color: #9a9388;
    margin: 0 0 18px;
    font-size: 13px;
  }
  .dv-card {
    background: #1f1c19;
    border: 1px solid #2e2a25;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 16px;
  }
  .dv-h2 {
    font-size: 13px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: #c8c2b3;
    margin: 0 0 10px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .dv-row {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 10px;
  }
  .dv-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #8a847a;
  }
  .dv-input,
  .dv-textarea {
    background: #15120f;
    border: 1px solid #322d27;
    color: #e8e3d6;
    border-radius: 6px;
    padding: 8px 10px;
    font-family: inherit;
    font-size: 13px;
    width: 100%;
    box-sizing: border-box;
  }
  .dv-textarea {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .dv-actions {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 8px;
  }
  .dv-btn {
    background: #2c2722;
    color: #e8e3d6;
    border: 1px solid #3d362e;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    cursor: pointer;
    font-family: inherit;
  }
  .dv-btn:hover:not(:disabled) {
    background: #383028;
  }
  .dv-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .dv-primary {
    background: #c96342;
    border-color: #c96342;
    color: #fff;
  }
  .dv-primary:hover:not(:disabled) {
    background: #d97050;
  }
  .dv-danger {
    background: #8a2c2c;
    border-color: #8a2c2c;
    color: #fff;
  }
  .dv-danger:hover:not(:disabled) {
    background: #9d3535;
  }
  .dv-error {
    color: #ff8a7a;
    font-size: 12px;
  }
  .dv-muted {
    color: #8a847a;
    font-size: 12px;
  }
  .dv-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .dv-chip {
    background: #2c2722;
    color: #e8e3d6;
    border: 1px solid #3d362e;
    border-radius: 999px;
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
    font-family: inherit;
  }
  .dv-chip:hover:not(:disabled) {
    background: #c96342;
    border-color: #c96342;
    color: #fff;
  }
  .dv-result-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
  }
  .dv-result-actions {
    display: flex;
    gap: 8px;
  }
  .dv-pill {
    background: #2c2722;
    color: #c8c2b3;
    border: 1px solid #3d362e;
    border-radius: 999px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0;
    text-transform: none;
  }
  .dv-pill.dv-warn {
    background: #5a3a1a;
    border-color: #8a5a2a;
    color: #f0c890;
  }
  .dv-sql {
    background: #15120f;
    border: 1px solid #2e2a25;
    border-radius: 6px;
    padding: 12px;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    color: #d0c8b8;
    margin: 10px 0;
    white-space: pre-wrap;
  }
  .dv-table-wrap {
    overflow-x: auto;
    max-height: 540px;
    overflow-y: auto;
    border: 1px solid #2e2a25;
    border-radius: 6px;
  }
  .dv-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }
  .dv-table thead {
    position: sticky;
    top: 0;
    background: #2c2722;
  }
  .dv-table th {
    text-align: left;
    padding: 8px 10px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #c8c2b3;
    border-bottom: 1px solid #3d362e;
  }
  .dv-table td {
    padding: 7px 10px;
    border-bottom: 1px solid #25211d;
    color: #e8e3d6;
    vertical-align: top;
  }
  .dv-table tr:hover td {
    background: #221e1a;
  }
</style>
