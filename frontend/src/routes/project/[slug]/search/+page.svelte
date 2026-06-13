<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { base } from '$app/paths';

  const slug = $derived($page.params.slug);

  let token = $state('');
  let query = $state('');
  let mode = $state<'conservative' | 'balanced' | 'tokenmax'>('balanced');
  let k = $state(10);
  let loading = $state(false);
  let err = $state('');
  let results = $state<any[]>([]);
  let debug = $state<any>(null);
  let log = $state<any[]>([]);
  let showDebug = $state(false);

  function _h(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }

  async function runSearch() {
    if (!query.trim()) return;
    loading = true; err = ''; results = []; debug = null;
    try {
      const r = await fetch('/api/retrieval/search', {
        method: 'POST',
        headers: _h(),
        body: JSON.stringify({ project: slug, query, mode, k }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.error || `HTTP ${r.status}`);
      results = d.results || [];
      debug = d.debug || null;
      loadLog();
    } catch (e: any) {
      err = e.message || String(e);
    }
    loading = false;
  }

  async function loadLog() {
    try {
      const r = await fetch(`/api/retrieval/log?project=${encodeURIComponent(slug)}&limit=20`, {
        headers: _h(),
      });
      const d = await r.json();
      if (r.ok) log = d.log || [];
    } catch {}
  }

  function snippet(text: string, max = 320): string {
    if (!text) return '';
    return text.length > max ? text.slice(0, max) + '…' : text;
  }

  function fmtTime(s: string): string {
    if (!s) return '';
    try { return new Date(s).toLocaleString(); } catch { return s; }
  }

  onMount(() => {
    try { token = localStorage.getItem('dash_token') || ''; } catch {}
    loadLog();
  });
</script>

<div class="search-shell">
  <header class="search-head">
    <a class="back" href="{base}/project/{slug}/settings">← back</a>
    <h1>Hybrid Search</h1>
    <p class="muted">BM25 + vector cosine + RRF fusion, with optional LLM multi-query expansion.</p>
  </header>

  <section class="search-form ink-border">
    <div class="row">
      <input
        class="qinput"
        type="text"
        placeholder="Ask anything across this project's knowledge…"
        bind:value={query}
        onkeydown={(e) => { if (e.key === 'Enter') runSearch(); }}
      />
      <select bind:value={mode} class="sel">
        <option value="conservative">conservative</option>
        <option value="balanced">balanced</option>
        <option value="tokenmax">tokenmax</option>
      </select>
      <div class="kpick">
        <label>k {k}</label>
        <input type="range" min="1" max="30" bind:value={k} />
      </div>
      <button class="go" onclick={runSearch} disabled={loading || !query.trim()}>
        {loading ? 'Searching…' : 'Search'}
      </button>
    </div>
    {#if err}<div class="err">{err}</div>{/if}
  </section>

  <section class="results">
    {#if loading}
      <div class="muted">Running hybrid retrieval…</div>
    {:else if results.length === 0}
      <div class="muted empty">No results yet. Try a query above.</div>
    {:else}
      <div class="meta">{results.length} hits · mode={mode} · k={k}</div>
      {#each results as r, i}
        <article class="hit ink-border">
          <header class="hit-head">
            <span class="rank">#{i + 1}</span>
            <span class="score">RRF {Number(r.score || 0).toFixed(4)}</span>
            <span class="src" title={r.source}>{r.source || '—'}</span>
            <span class="cid">chunk {r.chunk_id}</span>
          </header>
          <p class="snip">{snippet(r.content || '')}</p>
        </article>
      {/each}
    {/if}
  </section>

  {#if debug}
    <section class="debug ink-border">
      <button class="dtoggle" onclick={() => (showDebug = !showDebug)}>
        {showDebug ? '▾' : '▸'} debug — {debug.queries?.length || 0} sub-queries, {debug.fuse_lists || 0} fused lists
      </button>
      {#if showDebug}
        <div class="dbody">
          <div><b>mode:</b> {debug.mode} · <b>rrf k:</b> {debug.rrf_k}</div>
          <table class="dtable">
            <thead><tr><th>#</th><th>sub-query</th><th>bm25</th><th>vector</th></tr></thead>
            <tbody>
              {#each debug.queries || [] as q, i}
                <tr>
                  <td>{i + 1}</td>
                  <td class="q">{q.query}</td>
                  <td>{q.bm25_hits}</td>
                  <td>{q.vector_hits}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </section>
  {/if}

  {#if log.length}
    <section class="log ink-border">
      <div class="log-head">Recent searches</div>
      <table class="ltable">
        <thead><tr><th>when</th><th>query</th><th>mode</th><th>n</th><th>ms</th></tr></thead>
        <tbody>
          {#each log as row}
            <tr>
              <td>{fmtTime(row.ts)}</td>
              <td class="q">{row.query}</td>
              <td>{row.mode}</td>
              <td>{row.n_results}</td>
              <td>{row.latency_ms}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </section>
  {/if}
</div>

<style>
  .search-shell {
    max-width: 1100px;
    margin: 0 auto;
    padding: 24px 32px 64px;
    color: var(--pw-ink, #2c2a26);
  }
  .search-head { margin-bottom: 18px; }
  .search-head h1 {
    margin: 4px 0 2px;
    font-family: var(--pw-serif, Georgia, serif);
    font-size: 18px;
    font-weight: 700;
  }
  .back {
    display: inline-block;
    font-size: 11px;
    color: var(--pw-muted, #666);
    text-decoration: none;
    margin-bottom: 6px;
  }
  .back:hover { color: var(--pw-accent, #c96342); }
  .muted { color: var(--pw-muted, #666); font-size: 11px; }

  .ink-border {
    border: 1px solid var(--pw-border, #e4ddd2);
    background: var(--pw-surface, #fff);
    border-radius: var(--pw-radius-sm);
  }

  .search-form { padding: 14px 16px; margin-bottom: 16px; }
  .row {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
  }
  .qinput {
    flex: 1 1 320px;
    padding: 10px 12px;
    border: 1px solid var(--pw-border, #e4ddd2);
    border-radius: var(--pw-radius-sm);
    background: var(--pw-bg, #fbf7f1);
    font-size: 11px;
    color: var(--pw-ink, #2c2a26);
  }
  .qinput:focus { outline: 2px solid var(--pw-accent, #c96342); outline-offset: -1px; }
  .sel {
    padding: 8px 10px;
    border: 1px solid var(--pw-border, #e4ddd2);
    border-radius: var(--pw-radius-sm);
    background: var(--pw-bg, #fbf7f1);
    font-size: 11px;
  }
  .kpick { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--pw-muted, #666); }
  .kpick input { width: 120px; }
  .go {
    padding: 9px 18px;
    background: var(--pw-accent, #c96342);
    color: #fff;
    border: none;
    border-radius: var(--pw-radius-sm);
    font-weight: 600;
    font-size: 11px;
    cursor: pointer;
  }
  .go:disabled { opacity: 0.5; cursor: not-allowed; }
  .err {
    margin-top: 10px;
    padding: 8px 10px;
    background: rgba(255, 0, 0, 0.06);
    color: #b00;
    border-radius: var(--pw-radius-sm);
    font-size: 11px;
  }

  .results { margin-bottom: 18px; }
  .meta {
    font-size: 11px;
    color: var(--pw-muted, #666);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 8px;
  }
  .empty { padding: 24px 0; }
  .hit { padding: 12px 14px; margin-bottom: 8px; }
  .hit-head {
    display: flex;
    gap: 12px;
    align-items: center;
    font-size: 11px;
    color: var(--pw-muted, #666);
    margin-bottom: 6px;
  }
  .rank { font-weight: 700; color: var(--pw-accent, #c96342); }
  .score { font-family: monospace; }
  .src {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--pw-ink, #2c2a26);
    font-weight: 600;
  }
  .cid { font-family: monospace; opacity: 0.7; }
  .snip { margin: 0; font-size: 11px; line-height: 1.5; color: var(--pw-ink, #2c2a26); }

  .debug { padding: 10px 14px; margin-bottom: 14px; }
  .dtoggle {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 11px;
    color: var(--pw-muted, #666);
    padding: 0;
  }
  .dbody { margin-top: 8px; font-size: 11px; }
  .dtable, .ltable {
    width: 100%;
    margin-top: 8px;
    border-collapse: collapse;
    font-size: 11px;
  }
  .dtable th, .ltable th {
    text-align: left;
    font-weight: 600;
    color: var(--pw-muted, #666);
    padding: 6px 8px;
    border-bottom: 1px solid var(--pw-border, #e4ddd2);
  }
  .dtable td, .ltable td {
    padding: 6px 8px;
    border-bottom: 1px solid var(--pw-border-soft, #f0eae0);
  }
  .q { font-family: monospace; }
  .log { padding: 10px 14px; }
  .log-head {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--pw-muted, #666);
    margin-bottom: 4px;
  }
</style>
