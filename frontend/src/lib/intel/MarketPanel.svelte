<script lang="ts">
  import { onMount } from 'svelte';
  import { base } from '$app/paths';
  import { dashFetch } from '$lib/api';

  let { slug }: { slug: string } = $props();

  // ---- filters -------------------------------------------------------------
  let sector = $state('');
  let geography = $state('');
  let methodology = $state<'top_down' | 'bottom_up' | 'value_theory' | 'hybrid'>('bottom_up');

  // ---- TAM/SAM/SOM ---------------------------------------------------------
  let latestEstimate = $state<any | null>(null);
  let estimating = $state(false);
  let estimateErr = $state('');

  async function loadLatestEstimate() {
    try {
      const url = new URL(`/api/market/${slug}/tam-sam`, window.location.origin);
      if (sector.trim()) url.searchParams.set('sector', sector.trim());
      url.searchParams.set('limit', '1');
      const r = await dashFetch(url.pathname + url.search);
      if (!r.ok) { latestEstimate = null; return; }
      const data = await r.json();
      latestEstimate = (data?.estimates || [])[0] || null;
    } catch { latestEstimate = null; }
  }

  async function recompute() {
    if (!sector.trim() || !geography.trim() || estimating) return;
    estimating = true; estimateErr = '';
    try {
      const r = await dashFetch(`/api/market/${slug}/tam-sam`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          sector: sector.trim(),
          geography: geography.trim(),
          methodology,
        }),
      });
      if (!r.ok) {
        const t = await r.text().catch(() => '');
        estimateErr = `Estimate failed: ${r.status} ${t.slice(0, 200)}`;
        return;
      }
      latestEstimate = await r.json();
      await Promise.all([loadCompetitors(), loadTrends(), loadSignals()]);
    } catch (e: any) {
      estimateErr = String(e?.message || e);
    } finally {
      estimating = false;
    }
  }

  // ---- Signals -------------------------------------------------------------
  let signals = $state<any[]>([]);
  let signalTypeFilter = $state<string>('');
  const SIGNAL_TYPES = ['news', 'filing', 'patent', 'hire', 'funding', 'product', 'web_traffic'];

  async function loadSignals() {
    try {
      const url = new URL(`/api/market/${slug}/signals`, window.location.origin);
      if (sector.trim()) url.searchParams.set('sector', sector.trim());
      if (signalTypeFilter) url.searchParams.set('signal_type', signalTypeFilter);
      url.searchParams.set('limit', '40');
      const r = await dashFetch(url.pathname + url.search);
      if (!r.ok) { signals = []; return; }
      const data = await r.json();
      signals = data?.signals || [];
    } catch { signals = []; }
  }

  // ---- Search --------------------------------------------------------------
  let searchQ = $state('');
  let searchResults = $state<any[]>([]);
  let searching = $state(false);

  async function runSearch() {
    if (!searchQ.trim() || searching) return;
    searching = true;
    try {
      const url = new URL(`/api/market/${slug}/signals/search`, window.location.origin);
      url.searchParams.set('q', searchQ.trim());
      url.searchParams.set('top_k', '15');
      if (sector.trim()) url.searchParams.set('sector', sector.trim());
      const r = await dashFetch(url.pathname + url.search);
      if (!r.ok) { searchResults = []; return; }
      const data = await r.json();
      searchResults = data?.results || [];
    } catch { searchResults = []; }
    finally { searching = false; }
  }

  // ---- Competitors ---------------------------------------------------------
  let competitors = $state<any[]>([]);

  async function loadCompetitors() {
    if (!sector.trim()) { competitors = []; return; }
    try {
      const url = new URL(`/api/market/${slug}/competitors`, window.location.origin);
      url.searchParams.set('sector', sector.trim());
      if (geography.trim()) url.searchParams.set('geography', geography.trim());
      const r = await dashFetch(url.pathname + url.search);
      if (!r.ok) { competitors = []; return; }
      const data = await r.json();
      competitors = data?.competitors || [];
    } catch { competitors = []; }
  }

  // ---- Trends --------------------------------------------------------------
  let trends = $state<any[]>([]);
  let trendMood = $state<string>('');
  let trendDays = $state(90);

  async function loadTrends() {
    try {
      const url = new URL(`/api/market/${slug}/trends`, window.location.origin);
      if (sector.trim()) url.searchParams.set('sector', sector.trim());
      url.searchParams.set('days', String(trendDays));
      const r = await dashFetch(url.pathname + url.search);
      if (!r.ok) { trends = []; trendMood = ''; return; }
      const data = await r.json();
      trends = data?.trends || [];
      trendMood = data?.mood || '';
    } catch { trends = []; trendMood = ''; }
  }

  // ---- Add Signal ----------------------------------------------------------
  let showAddSig = $state(false);
  let newSig = $state({
    signal_type: 'news',
    source_url: '',
    title: '',
    body: '',
  });
  let addingSig = $state(false);

  async function addSignal() {
    if (!newSig.title.trim() || addingSig) return;
    addingSig = true;
    try {
      const r = await dashFetch(`/api/market/${slug}/signals`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          ...newSig,
          sector: sector.trim() || null,
          geography: geography.trim() || null,
        }),
      });
      if (r.ok) {
        newSig = { signal_type: 'news', source_url: '', title: '', body: '' };
        showAddSig = false;
        await loadSignals();
      }
    } finally { addingSig = false; }
  }

  // ---- Formatters ----------------------------------------------------------
  function fmtMoney(n: number | null | undefined): string {
    if (n == null) return '—';
    const a = Math.abs(n);
    if (a >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
    return '$' + n.toFixed(0);
  }
  function fmtPct(n: number | null | undefined, scale = false): string {
    if (n == null) return '—';
    return (scale ? n * 100 : n).toFixed(1) + '%';
  }
  function fmtDate(s: string | null | undefined): string {
    if (!s) return '—';
    try { return new Date(s).toLocaleDateString(); } catch { return s; }
  }
  function trendArrow(s: string): string {
    if (s === 'positive') return '↑';
    if (s === 'negative') return '↓';
    return '→';
  }
  function moodClass(s: string): string {
    return s === 'positive' ? 'mood-pos' : s === 'negative' ? 'mood-neg' : 'mood-flat';
  }

  // ---- Effects -------------------------------------------------------------
  $effect(() => {
    if (signalTypeFilter !== undefined) loadSignals();
  });

  onMount(() => {
    loadSignals();
    loadTrends();
  });
</script>

<div class="mp-root">
  <!-- Header / filters -->
  <div class="mp-head">
    <h2>Market Sentinel</h2>
    <div class="mp-filters">
      <input placeholder="Sector (e.g. EV charging)" bind:value={sector} />
      <input placeholder="Geography (e.g. Vietnam)" bind:value={geography} />
      <button class="mp-btn" onclick={() => { loadLatestEstimate(); loadCompetitors(); loadTrends(); loadSignals(); }}>
        Apply
      </button>
    </div>
  </div>

  <!-- Search bar -->
  <div class="mp-search">
    <input
      placeholder="Semantic search across signals (e.g. battery recycling Q3)"
      bind:value={searchQ}
      onkeydown={(e) => { if (e.key === 'Enter') runSearch(); }}
    />
    <button class="mp-btn-sm" disabled={searching || !searchQ.trim()} onclick={runSearch}>
      {searching ? '…' : 'Search'}
    </button>
  </div>

  {#if searchResults.length}
    <div class="mp-search-results">
      <div class="mp-section-title">Search results ({searchResults.length})</div>
      <ul class="mp-sr-list">
        {#each searchResults as r}
          <li>
            <span class="mp-pill mp-pill-{r.signal_type}">{r.signal_type}</span>
            <span class="mp-sr-title">{r.title}</span>
            {#if r.score > 0}<span class="mp-score">conf {r.score.toFixed(2)}</span>{/if}
            {#if r.source_url}<a href={r.source_url} target="_blank" rel="noopener" class="mp-link">↗</a>{/if}
          </li>
        {/each}
      </ul>
      <button class="mp-mini" onclick={() => { searchResults = []; searchQ = ''; }}>Clear</button>
    </div>
  {/if}

  <div class="mp-grid">
    <!-- TOP-LEFT: TAM/SAM/SOM -->
    <section class="mp-card mp-card-tam">
      <h3>Market size</h3>
      {#if latestEstimate}
        <div class="mp-tam-row">
          <div class="mp-tam-cell">
            <span>TAM</span>
            <b>{fmtMoney(latestEstimate.tam_usd)}</b>
          </div>
          <div class="mp-tam-cell">
            <span>SAM</span>
            <b>{fmtMoney(latestEstimate.sam_usd)}</b>
          </div>
          <div class="mp-tam-cell">
            <span>SOM</span>
            <b>{fmtMoney(latestEstimate.som_usd)}</b>
          </div>
        </div>
        <div class="mp-tam-meta">
          <span>Method: <b>{latestEstimate.methodology || '—'}</b></span>
          {#if latestEstimate?.assumptions?.confidence != null}
            <span>Conf: <b>{(latestEstimate.assumptions.confidence * 100).toFixed(0)}%</b></span>
          {/if}
          {#if latestEstimate.computed_at}
            <span class="mp-muted">{fmtDate(latestEstimate.computed_at)}</span>
          {/if}
        </div>
        {#if latestEstimate?.assumptions?.method_explanation}
          <p class="mp-tam-expl">{latestEstimate.assumptions.method_explanation}</p>
        {/if}
      {:else}
        <div class="mp-muted">No estimate yet for this sector.</div>
      {/if}

      <div class="mp-recompute">
        <select bind:value={methodology}>
          <option value="bottom_up">Bottom-up</option>
          <option value="top_down">Top-down</option>
          <option value="value_theory">Value theory</option>
          <option value="hybrid">Hybrid</option>
        </select>
        <button class="mp-btn-sm" disabled={estimating || !sector.trim() || !geography.trim()} onclick={recompute}>
          {estimating ? 'Computing…' : 'Recompute'}
        </button>
      </div>
      {#if estimateErr}<div class="mp-err">{estimateErr}</div>{/if}
    </section>

    <!-- RIGHT: Competitors -->
    <section class="mp-card mp-card-comp">
      <h3>Competitors</h3>
      {#if competitors.length}
        <table class="mp-table">
          <thead>
            <tr><th>Name</th><th>Geo</th><th>Share %</th><th>Trend</th></tr>
          </thead>
          <tbody>
            {#each competitors.slice(0, 12) as c}
              <tr>
                <td><b>{c.name}</b></td>
                <td>{c.geography || '—'}</td>
                <td class="num">{fmtPct(c.share_pct)}</td>
                <td>
                  {#if c.evidence && c.evidence.length > 1}
                    {#if c.evidence[c.evidence.length - 1]?.mentions > (c.evidence[0]?.mentions || 0)}
                      <span class="mp-arrow-up">▲</span>
                    {:else if c.evidence[c.evidence.length - 1]?.mentions < (c.evidence[0]?.mentions || 0)}
                      <span class="mp-arrow-down">▼</span>
                    {:else}
                      <span class="mp-arrow-flat">━</span>
                    {/if}
                  {:else}
                    <span class="mp-arrow-flat">━</span>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <div class="mp-muted">No competitors mapped for sector "{sector || '—'}".</div>
      {/if}
    </section>

    <!-- CENTER: Signal feed -->
    <section class="mp-card mp-card-signals">
      <div class="mp-card-head">
        <h3>Signal feed</h3>
        <div class="mp-type-pills">
          <button class:active={signalTypeFilter === ''} onclick={() => signalTypeFilter = ''}>all</button>
          {#each SIGNAL_TYPES as t}
            <button class:active={signalTypeFilter === t} onclick={() => signalTypeFilter = t}>{t}</button>
          {/each}
          <button class="mp-mini" onclick={() => showAddSig = !showAddSig}>{showAddSig ? '× cancel' : '+ add'}</button>
        </div>
      </div>

      {#if showAddSig}
        <div class="mp-add-sig">
          <select bind:value={newSig.signal_type}>
            {#each SIGNAL_TYPES as t}
              <option value={t}>{t}</option>
            {/each}
          </select>
          <input placeholder="Title" bind:value={newSig.title} />
          <input placeholder="Source URL" bind:value={newSig.source_url} />
          <input placeholder="Body (optional)" bind:value={newSig.body} />
          <button class="mp-btn-sm" disabled={!newSig.title.trim() || addingSig} onclick={addSignal}>
            {addingSig ? '…' : 'Save'}
          </button>
        </div>
      {/if}

      {#if signals.length}
        <ul class="mp-signal-list">
          {#each signals as s}
            <li>
              <span class="mp-pill mp-pill-{s.signal_type}">{s.signal_type}</span>
              <span class="mp-sig-title">{s.title}</span>
              <span class="mp-muted mp-sig-date">{fmtDate(s.published_at || s.ingested_at)}</span>
              {#if s.source_url}<a href={s.source_url} target="_blank" rel="noopener" class="mp-link">↗</a>{/if}
            </li>
          {/each}
        </ul>
      {:else}
        <div class="mp-muted">No signals yet. Add one above or run a sector filter.</div>
      {/if}
    </section>

    <!-- BOTTOM: Trend cards -->
    <section class="mp-card mp-card-trends">
      <div class="mp-card-head">
        <h3>Trends</h3>
        <div class="mp-trend-ctrl">
          <select bind:value={trendDays} onchange={loadTrends}>
            <option value={30}>30d</option>
            <option value={90}>90d</option>
            <option value={180}>180d</option>
            <option value={365}>365d</option>
          </select>
          {#if trendMood}
            <span class="mp-mood {moodClass(trendMood)}">{trendArrow(trendMood)} {trendMood}</span>
          {/if}
        </div>
      </div>

      {#if trends.length}
        <div class="mp-trend-grid">
          {#each trends as t}
            <div class="mp-trend-card">
              <div class="mp-trend-theme">{t.theme}</div>
              <div class="mp-trend-meta">
                <span>{t.signal_count} signals</span>
                <span class="mp-mood {moodClass(t.sentiment)}">{trendArrow(t.sentiment)} {t.sentiment}</span>
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="mp-muted">No emerging trends. Add 10+ signals to surface themes.</div>
      {/if}
    </section>
  </div>
</div>

<style>
  .mp-root { padding: 16px; font-size: 13px; color: #2c2a26; }
  .mp-head { display: flex; flex-direction: column; gap: 10px; margin-bottom: 12px; }
  .mp-head h2 { margin: 0; font-size: 18px; font-weight: 600; }
  .mp-filters { display: flex; gap: 6px; flex-wrap: wrap; }
  .mp-filters input {
    padding: 6px 10px; border: 1px solid #d6cfbe; background: #fff;
    font-size: 12.5px; color: #2c2a26; flex: 1; min-width: 160px;
  }
  .mp-search { display: flex; gap: 6px; margin-bottom: 10px; }
  .mp-search input {
    flex: 1; padding: 7px 12px; border: 1px solid #d6cfbe; background: #fff;
    font-size: 13px; color: #2c2a26;
  }
  .mp-search-results {
    background: #f7f6f3; border: 1px solid #e8e3d6; padding: 8px 10px;
    margin-bottom: 12px;
  }
  .mp-section-title {
    font-size: 11px; text-transform: uppercase; color: #6b6557;
    letter-spacing: 0.05em; margin-bottom: 6px;
  }
  .mp-sr-list, .mp-signal-list {
    list-style: none; padding: 0; margin: 0;
    display: flex; flex-direction: column; gap: 4px;
  }
  .mp-sr-list li, .mp-signal-list li {
    display: flex; gap: 8px; align-items: center;
    padding: 4px 0; border-bottom: 1px dotted #e8e3d6; font-size: 12.5px;
  }
  .mp-sr-list li:last-child, .mp-signal-list li:last-child { border-bottom: 0; }
  .mp-sr-title, .mp-sig-title { flex: 1; color: #1a1614; }
  .mp-sig-date { font-size: 11px; }
  .mp-score { font-size: 10.5px; color: #6b6557; }

  .mp-btn {
    background: #c96342; color: #fff; border: 0; padding: 7px 14px;
    font-size: 12px; font-weight: 600; cursor: pointer;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .mp-btn:hover { background: #b35636; }
  .mp-btn-sm {
    background: #c96342; color: #fff; border: 0; padding: 5px 10px;
    font-size: 11.5px; font-weight: 600; cursor: pointer;
  }
  .mp-btn-sm:disabled { background: #d6cfbe; cursor: not-allowed; }
  .mp-mini {
    background: transparent; border: 1px solid #d6cfbe; color: #1a1614;
    padding: 3px 8px; font-size: 10.5px; font-weight: 600; cursor: pointer;
    letter-spacing: 0.04em;
  }
  .mp-mini:hover { background: #c96342; color: #fff; border-color: #c96342; }

  .mp-grid {
    display: grid; grid-template-columns: 1fr 1fr;
    grid-template-rows: auto 1fr auto; gap: 12px;
  }
  .mp-card {
    background: #fff; border: 1px solid #e8e3d6; padding: 12px;
    display: flex; flex-direction: column; gap: 8px;
  }
  .mp-card h3 {
    margin: 0; font-size: 13px; font-weight: 600; color: #1a1614;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .mp-card-tam { grid-column: 1; grid-row: 1; }
  .mp-card-comp { grid-column: 2; grid-row: 1 / span 2; }
  .mp-card-signals { grid-column: 1; grid-row: 2; }
  .mp-card-trends { grid-column: 1 / span 2; grid-row: 3; }

  .mp-card-head {
    display: flex; justify-content: space-between; align-items: center;
    gap: 8px; flex-wrap: wrap;
  }

  /* TAM/SAM/SOM */
  .mp-tam-row { display: flex; gap: 10px; }
  .mp-tam-cell {
    flex: 1; padding: 10px 8px; background: #f7f6f3; border: 1px solid #e8e3d6;
    display: flex; flex-direction: column; gap: 2px;
  }
  .mp-tam-cell span {
    font-size: 10.5px; color: #6b6557; text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .mp-tam-cell b { font-size: 20px; color: #1a1614; }
  .mp-tam-meta {
    display: flex; gap: 12px; flex-wrap: wrap; font-size: 11.5px;
    color: #6b6557;
  }
  .mp-tam-meta b { color: #1a1614; }
  .mp-tam-expl {
    margin: 0; font-size: 11.5px; color: #6b6557; font-style: italic;
    border-left: 2px solid #c96342; padding-left: 8px;
  }
  .mp-recompute {
    display: flex; gap: 6px; align-items: center;
    border-top: 1px solid #e8e3d6; padding-top: 8px;
  }
  .mp-recompute select {
    padding: 5px 8px; border: 1px solid #d6cfbe; background: #fff;
    font-size: 12px; color: #2c2a26;
  }
  .mp-muted { color: #9a9080; font-size: 12px; padding: 4px 0; }

  /* Tables */
  .mp-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .mp-table th, .mp-table td {
    padding: 6px 8px; text-align: left; border-bottom: 1px solid #f0ebde;
  }
  .mp-table th {
    font-size: 10.5px; text-transform: uppercase; color: #6b6557;
    letter-spacing: 0.05em; background: #f7f6f3;
  }
  .mp-table td.num { text-align: right; font-variant-numeric: tabular-nums; }

  /* Pills (signal types) */
  .mp-pill {
    display: inline-block; padding: 2px 6px; font-size: 9.5px;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
    background: #e8e3d6; color: #1a1614; min-width: 50px; text-align: center;
  }
  .mp-pill-news { background: #c96342; color: #fff; }
  .mp-pill-funding { background: #2c7a3f; color: #fff; }
  .mp-pill-patent { background: #6b46a8; color: #fff; }
  .mp-pill-filing { background: #1a1614; color: #fff; }
  .mp-pill-hire { background: #0e7c86; color: #fff; }
  .mp-pill-product { background: #d97706; color: #fff; }
  .mp-pill-web_traffic { background: #6b6557; color: #fff; }

  .mp-type-pills { display: flex; gap: 4px; flex-wrap: wrap; }
  .mp-type-pills button {
    background: transparent; border: 1px solid #d6cfbe; color: #6b6557;
    padding: 3px 8px; font-size: 10.5px; cursor: pointer;
    text-transform: lowercase;
  }
  .mp-type-pills button.active {
    background: #c96342; color: #fff; border-color: #c96342;
  }

  /* Add signal form */
  .mp-add-sig {
    display: flex; gap: 6px; flex-wrap: wrap; padding: 8px;
    background: #f7f6f3; border: 1px solid #e8e3d6;
  }
  .mp-add-sig input, .mp-add-sig select {
    padding: 5px 8px; border: 1px solid #d6cfbe; background: #fff;
    font-size: 12px; color: #2c2a26; flex: 1; min-width: 120px;
  }

  /* Trend grid */
  .mp-trend-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 8px;
  }
  .mp-trend-card {
    padding: 10px; background: #f7f6f3; border: 1px solid #e8e3d6;
    display: flex; flex-direction: column; gap: 4px;
  }
  .mp-trend-theme {
    font-size: 13px; font-weight: 600; color: #1a1614;
    text-transform: capitalize;
  }
  .mp-trend-meta {
    display: flex; justify-content: space-between; font-size: 11.5px;
    color: #6b6557;
  }
  .mp-trend-ctrl {
    display: flex; gap: 6px; align-items: center;
  }
  .mp-trend-ctrl select {
    padding: 4px 6px; border: 1px solid #d6cfbe; background: #fff;
    font-size: 11px;
  }

  /* Mood */
  .mp-mood {
    display: inline-block; padding: 2px 6px; font-size: 10.5px;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .mood-pos { background: rgba(44, 122, 63, 0.12); color: #2c7a3f; }
  .mood-neg { background: rgba(192, 57, 43, 0.12); color: #c0392b; }
  .mood-flat { background: #f0ebde; color: #6b6557; }

  .mp-arrow-up { color: #2c7a3f; font-weight: 700; }
  .mp-arrow-down { color: #c0392b; font-weight: 700; }
  .mp-arrow-flat { color: #6b6557; }

  .mp-link {
    color: #c96342; text-decoration: none; font-weight: 600;
  }
  .mp-link:hover { text-decoration: underline; }

  .mp-err {
    color: #c0392b; padding: 6px 10px;
    background: rgba(192, 57, 43, 0.06); font-size: 11.5px;
  }

  /* Mobile */
  @media (max-width: 800px) {
    .mp-grid { grid-template-columns: 1fr; }
    .mp-card-tam, .mp-card-comp, .mp-card-signals, .mp-card-trends {
      grid-column: 1; grid-row: auto;
    }
  }
</style>
