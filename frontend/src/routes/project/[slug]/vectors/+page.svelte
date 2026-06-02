<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import { page } from '$app/stores';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';

 const slug = $derived($page.params.slug);

 let token = $state('');
 let activeTab = $state<'browse' | 'ingest' | 'search' | 'rls'>('browse');
 let err = $state('');
 let loading = $state(false);
 let recomputing = $state(false);
 let recomputeMsg = $state('');

 // BROWSE
 let rows = $state<any[]>([]);
 let totalRows = $state(0);
 let nsFilter = $state('');
 let availableNs = $state<string[]>([]);

 // INGEST
 let ingText = $state('');
 let ingNs = $state('default');
 let ingSourceId = $state('');
 let ingScopeAttrs = $state('{}');
 let ingMsg = $state('');
 let ingesting = $state(false);

 // SEARCH
 let searchQ = $state('');
 let searchTopK = $state(10);
 let searchHybrid = $state(false);
 let searchUserAttrs = $state('{}');
 let searchResults = $state<any[]>([]);
 let searching = $state(false);

 // RLS TEST
 type TestResult = { name: string; pass: boolean | null; detail: string; running: boolean };
 let tests = $state<TestResult[]>([
 { name: 'store1 catalog visible', pass: null, detail: '', running: false },
 { name: 'store2 catalog visible', pass: null, detail: '', running: false },
 { name: 'store1 own stock', pass: null, detail: '', running: false },
 { name: 'store2 own stock', pass: null, detail: '', running: false },
 { name: 'leak guard store1', pass: null, detail: '', running: false },
 { name: 'leak guard store2', pass: null, detail: '', running: false },
 { name: 'admin bypass', pass: null, detail: '', running: false },
 { name: 'hybrid mode safe', pass: null, detail: '', running: false },
 ]);
 let testRunning = $state(false);
 let fixturesMissing = $state(false);
 let seeding = $state(false);
 let seedMsg = $state('');

 function _h(): Record<string, string> {
 return token ? { Authorization: `Bearer ${token}` } : {};
 }

 function _hj(): Record<string, string> {
 return { 'Content-Type': 'application/json', ..._h() };
 }

 async function loadBrowse() {
 loading = true; err = '';
 try {
 const params = new URLSearchParams({ limit: '100' });
 if (nsFilter.trim()) params.set('namespace', nsFilter.trim());
 const r = await fetch(`/api/projects/${slug}/vectors/list?${params}`, { headers: _h() });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || d.error || 'load failed');
 rows = d.rows || d.items || d.vectors || [];
 totalRows = d.total ?? rows.length;
 const seen = new Set<string>();
 for (const r of rows) if (r.namespace) seen.add(r.namespace);
 availableNs = Array.from(seen).sort();
 } catch (e: any) { err = e.message; rows = []; }
 loading = false;
 }

 async function ingest() {
 if (ingesting) return;
 ingesting = true; ingMsg = '';
 try {
 let scope: any = {};
 try { scope = JSON.parse(ingScopeAttrs || '{}'); } catch { throw new Error('scope_attrs must be valid JSON'); }
 const body = {
 rows: [{
 text: ingText,
 namespace: ingNs || 'default',
 source_id: ingSourceId || null,
 scope_attrs: scope,
 }],
 };
 const r = await fetch(`/api/projects/${slug}/vectors/ingest`, {
 method: 'POST',
 headers: _hj(),
 body: JSON.stringify(body),
 });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || d.error || 'ingest failed');
 ingMsg = ` ingested ${d.inserted ?? 1} row${(d.inserted ?? 1) === 1 ? '' : 's'}`;
 ingText = '';
 } catch (e: any) {
 ingMsg = ` ${e.message}`;
 } finally {
 ingesting = false;
 }
 }

 async function runSearch(): Promise<any[]> {
 let attrs: any = {};
 try { attrs = JSON.parse(searchUserAttrs || '{}'); } catch { throw new Error('user_attrs must be valid JSON'); }
 const body = {
 query: searchQ,
 top_k: searchTopK,
 hybrid: searchHybrid,
 user_attrs: attrs,
 };
 const r = await fetch(`/api/projects/${slug}/vectors/search`, {
 method: 'POST',
 headers: _hj(),
 body: JSON.stringify(body),
 });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || d.error || 'search failed');
 return d.results || d.rows || [];
 }

 async function doSearch() {
 if (searching) return;
 searching = true; err = '';
 try {
 searchResults = await runSearch();
 } catch (e: any) {
 err = e.message; searchResults = [];
 }
 searching = false;
 }

 async function recompute() {
 if (recomputing) return;
 recomputing = true; recomputeMsg = '';
 try {
 const r = await fetch(`/api/projects/${slug}/vectors/recompute`, {
 method: 'POST',
 headers: _hj(),
 });
 const d = await r.json().catch(() => ({}));
 if (!r.ok) throw new Error(d.detail || d.error || 'recompute failed');
 recomputeMsg = ` ${d.processed ?? d.reembedded ?? 'done'}`;
 await loadBrowse();
 } catch (e: any) {
 recomputeMsg = ` ${e.message}`;
 } finally {
 recomputing = false;
 }
 }

 // ─── RLS TEST RUNNER ─────────────────────────────────
 async function searchWith(q: string, attrs: any, hybrid = false, top_k = 10): Promise<any[]> {
 const body = { query: q, top_k, hybrid, user_attrs: attrs };
 const r = await fetch(`/api/projects/${slug}/vectors/search`, {
 method: 'POST', headers: _hj(), body: JSON.stringify(body),
 });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || d.error || `search failed (${r.status})`);
 return d.results || d.rows || [];
 }

 function leaksContain(results: any[], needles: string[]): string | null {
 for (const row of results) {
 const t = String(row.text || '').toLowerCase();
 for (const n of needles) {
 if (t.includes(n.toLowerCase())) return `LEAK: "${n}" in result text`;
 }
 }
 return null;
 }

 async function runTest(idx: number) {
 tests[idx].running = true; tests[idx].pass = null; tests[idx].detail = '';
 try {
 let pass = false; let detail = '';
 if (idx === 0) {
 const r = await searchWith('widget', { store_id: 1 });
 pass = r.length >= 1;
 detail = `${r.length} hits`;
 } else if (idx === 1) {
 const r = await searchWith('widget', { store_id: 2 });
 pass = r.length >= 1;
 detail = `${r.length} hits`;
 } else if (idx === 2) {
 const r = await searchWith('stock', { store_id: 1 });
 pass = r.every(x => {
 const s = x.scope_attrs || {};
 return s.store_id === 1 || Object.keys(s).length === 0;
 });
 detail = `${r.length} rows · all store_id=1 or {}`;
 } else if (idx === 3) {
 const r = await searchWith('stock', { store_id: 2 });
 pass = r.every(x => {
 const s = x.scope_attrs || {};
 return s.store_id === 2 || Object.keys(s).length === 0;
 });
 detail = `${r.length} rows · all store_id=2 or {}`;
 } else if (idx === 4) {
 const r = await searchWith('stock', { store_id: 1 });
 const leak = leaksContain(r, ['200 units', 'store 2']);
 pass = leak === null;
 detail = leak || `clean (${r.length} rows)`;
 } else if (idx === 5) {
 const r = await searchWith('stock', { store_id: 2 });
 const leak = leaksContain(r, ['50 units', 'store 1']);
 pass = leak === null;
 detail = leak || `clean (${r.length} rows)`;
 } else if (idx === 6) {
 const r = await searchWith('stock OR widget', { role: 'admin' }, false, 50);
 pass = r.length >= 4;
 detail = `${r.length} rows (need ≥4)`;
 } else if (idx === 7) {
 const r1 = await searchWith('stock', { store_id: 1 }, true);
 const leak = leaksContain(r1, ['200 units', 'store 2']);
 pass = leak === null && r1.every(x => {
 const s = x.scope_attrs || {};
 return s.store_id === 1 || Object.keys(s).length === 0;
 });
 detail = leak || `hybrid clean (${r1.length} rows)`;
 }
 tests[idx].pass = pass;
 tests[idx].detail = detail;
 } catch (e: any) {
 tests[idx].pass = false;
 tests[idx].detail = e.message;
 } finally {
 tests[idx].running = false;
 }
 }

 async function runAllTests() {
 if (testRunning) return;
 testRunning = true; fixturesMissing = false;
 try {
 // probe fixtures
 const probe = await searchWith('widget', { store_id: 1 }).catch(() => []);
 if (probe.length === 0) {
 const probe2 = await searchWith('widget', { role: 'admin' }, false, 50).catch(() => []);
 if (probe2.length === 0) { fixturesMissing = true; testRunning = false; return; }
 }
 for (let i = 0; i < tests.length; i++) {
 await runTest(i);
 }
 } finally {
 testRunning = false;
 }
 }

 async function seedFixtures() {
 if (seeding) return;
 seeding = true; seedMsg = '';
 try {
 // Try dedicated endpoint first
 let r = await fetch(`/api/projects/${slug}/vectors/seed-fixtures`, {
 method: 'POST', headers: _hj(),
 });
 if (r.status === 404) {
 // Fallback: ingest 4 fixtures inline
 const fixtures = [
 { text: 'widget catalog product premium', namespace: 'products', source_id: 'p-w1', scope_attrs: {} },
 { text: 'gizmo catalog product standard', namespace: 'products', source_id: 'p-g1', scope_attrs: {} },
 { text: 'widget stock 50 units in store 1 warehouse', namespace: 'stock', source_id: 's1-w', scope_attrs: { store_id: 1 } },
 { text: 'widget stock 200 units in store 2 warehouse', namespace: 'stock', source_id: 's2-w', scope_attrs: { store_id: 2 } },
 ];
 const r2 = await fetch(`/api/projects/${slug}/vectors/ingest`, {
 method: 'POST', headers: _hj(), body: JSON.stringify({ rows: fixtures }),
 });
 const d2 = await r2.json();
 if (!r2.ok) throw new Error(d2.detail || d2.error || 'seed fallback failed');
 seedMsg = ` seeded ${d2.inserted ?? fixtures.length} fixtures`;
 } else {
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || d.error || 'seed failed');
 seedMsg = ` ${d.inserted ?? 'seeded'}`;
 }
 fixturesMissing = false;
 } catch (e: any) {
 seedMsg = ` ${e.message}`;
 } finally {
 seeding = false;
 }
 }

 function snippet(s: any, n = 200): string {
 const t = String(s ?? '');
 return t.length > n ? t.slice(0, n) + '…' : t;
 }

 function prettyJSON(v: any): string {
 try { return JSON.stringify(v ?? {}, null, 2); } catch { return String(v); }
 }

 function fmtDate(s: string | null | undefined): string {
 if (!s) return '—';
 try { const d = new Date(s); return d.toLocaleString(); } catch { return String(s); }
 }

 onMount(async () => {
 if (typeof localStorage !== 'undefined') token = localStorage.getItem('dash_token') || '';
 if (!token) { goto(`${base}/login`); return; }
 await loadBrowse();
 });

 $effect(() => {
 if (token && activeTab === 'browse') { loadBrowse(); }
 });
</script>

<svelte:head><title>Vectors · {slug}</title></svelte:head>

<div style="background: #f5f5e8; min-height: 100vh; padding: 16px; font-family: monospace;">

  <!-- Header -->
  <div style="background: #1a1a1a; color: #00fc40; padding: 12px 18px; display: flex; align-items: center; gap: 12px; margin-bottom: 14px;">
    <a href={`${base}/project/${slug}/settings`} style="color: #ccc; font-size: 11px; text-decoration: none; padding: 4px 8px; border: 1px solid #888;">←</a>
    <span style="font-size: 11px; font-weight: 900; letter-spacing: 0.06em;"><Icon name="dna" size={14} /> VECTORS · {slug}</span>
    <span style="margin-left: auto; font-size: 11px; color: #888;">{totalRows} rows</span>
    <button onclick={loadBrowse} style="padding: 4px 10px; background: #00fc40; color: #000; border: 1px solid #00fc40; cursor: pointer; font-family: monospace; font-size: 10px; font-weight: 900;">↻ REFRESH</button>
    <button onclick={recompute} disabled={recomputing} title="Re-embed all vectors" style="padding: 4px 10px; background: {recomputing ? '#555' : '#ff9d00'}; color: #000; border: 1px solid {recomputing ? '#555' : '#ff9d00'}; cursor: {recomputing ? 'wait' : 'pointer'}; font-family: monospace; font-size: 10px; font-weight: 900;">
      {recomputing ? 'COMPUTING…' : 'RECOMPUTE'}
    </button>
    {#if recomputeMsg}
      <span style="font-size: 11px; color: #ccc; margin-left: 6px;">{recomputeMsg}</span>
    {/if}
  </div>

  <!-- Tabs -->
  <div style="display: flex; gap: 0; margin-bottom: 14px; border-bottom: 1.5px solid #1a1a1a;">
    {#each [
      { id: 'browse', label: 'BROWSE' },
      { id: 'ingest', label: 'INGEST' },
      { id: 'search', label: 'SEARCH' },
      { id: 'rls',    label: 'RLS TEST' },
    ] as t}
      <button
        onclick={() => activeTab = t.id as any}
        style="padding: 8px 16px; background: {activeTab === t.id ? '#1a1a1a' : '#fafaf5'}; color: {activeTab === t.id ? '#00fc40' : '#1a1a1a'}; border: 1.5px solid #1a1a1a; border-bottom: none; cursor: pointer; font-family: monospace; font-size: 11px; font-weight: 900; letter-spacing: 0.06em; margin-right: 4px;">
        {t.label}
      </button>
    {/each}
  </div>

  {#if err}
    <div style="background: #fff; border: 2px solid #be2d06; color: #be2d06; padding: 12px; font-size: 11px; margin-bottom: 12px;">API error: {err}</div>
  {/if}

  <!-- BROWSE -->
  {#if activeTab === 'browse'}
    <div style="display: flex; gap: 8px; margin-bottom: 10px; align-items: center;">
      <select bind:value={nsFilter} style="padding: 6px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff;">
        <option value="">ALL NAMESPACES</option>
        {#each availableNs as ns}
          <option value={ns}>{ns}</option>
        {/each}
      </select>
      <input
        bind:value={nsFilter}
        onkeydown={(e) => { if (e.key === 'Enter') loadBrowse(); }}
        placeholder="filter namespace..."
        style="flex: 1; padding: 6px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff;"
      />
      <button onclick={loadBrowse} style="padding: 6px 12px; background: #00fc40; color: #000; border: 1.5px solid #1a1a1a; cursor: pointer; font-family: monospace; font-size: 11px; font-weight: 900;">FILTER</button>
    </div>

    {#if loading}
      <div style="padding: 20px; text-align: center; color: #888; font-size: 11px;">loading…</div>
    {:else if rows.length === 0}
      <div style="background: #fafaf5; border: 1.5px solid #ddd; padding: 30px; text-align: center; color: #888; font-size: 11px;">
        <Icon name="inbox" size={14} /> No vectors yet. Use INGEST tab to add rows.
      </div>
    {:else}
      <table style="width: 100%; background: #fafaf5; border: 1.5px solid #1a1a1a; border-collapse: collapse; font-size: 11px;">
        <thead style="background: #1a1a1a; color: #00fc40;">
          <tr>
            <th style="padding: 8px 10px; text-align: left;">NAMESPACE</th>
            <th style="padding: 8px 10px; text-align: left;">SOURCE_ID</th>
            <th style="padding: 8px 10px; text-align: left;">TEXT</th>
            <th style="padding: 8px 10px; text-align: left;">SCOPE_ATTRS</th>
            <th style="padding: 8px 10px; text-align: left;">UPDATED</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as r}
            <tr style="border-bottom: 1px solid #eee; background: #fff;">
              <td style="padding: 6px 10px; font-weight: 700; color: #007518;">{r.namespace || '—'}</td>
              <td style="padding: 6px 10px; color: #555;">{r.source_id || '—'}</td>
              <td style="padding: 6px 10px;">{snippet(r.text, 200)}</td>
              <td style="padding: 6px 10px;"><pre style="margin: 0; font-size: 10px; white-space: pre-wrap;">{prettyJSON(r.scope_attrs)}</pre></td>
              <td style="padding: 6px 10px; color: #888; font-size: 10px;">{fmtDate(r.updated_at)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}

  <!-- INGEST -->
  {#if activeTab === 'ingest'}
    <div style="background: #fafaf5; border: 1.5px solid #1a1a1a; padding: 16px;">
      <div style="font-size: 10px; font-weight: 900; letter-spacing: 0.06em; color: #555; margin-bottom: 6px;">TEXT</div>
      <textarea bind:value={ingText} rows="6" placeholder="multi-line text body to embed..." style="width: 100%; padding: 8px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff; box-sizing: border-box;"></textarea>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px;">
        <div>
          <div style="font-size: 10px; font-weight: 900; letter-spacing: 0.06em; color: #555; margin-bottom: 6px;">NAMESPACE</div>
          <input bind:value={ingNs} placeholder="default" style="width: 100%; padding: 6px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff; box-sizing: border-box;" />
        </div>
        <div>
          <div style="font-size: 10px; font-weight: 900; letter-spacing: 0.06em; color: #555; margin-bottom: 6px;">SOURCE_ID</div>
          <input bind:value={ingSourceId} placeholder="optional unique id" style="width: 100%; padding: 6px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff; box-sizing: border-box;" />
        </div>
      </div>

      <div style="margin-top: 12px;">
        <div style="font-size: 10px; font-weight: 900; letter-spacing: 0.06em; color: #555; margin-bottom: 6px;">SCOPE_ATTRS (JSON)</div>
        <textarea bind:value={ingScopeAttrs} rows="4" placeholder={'{"store_id": 1}'} style="width: 100%; padding: 8px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff; box-sizing: border-box;"></textarea>
      </div>

      <div style="margin-top: 14px; display: flex; align-items: center; gap: 12px;">
        <button onclick={ingest} disabled={ingesting || !ingText.trim()} style="padding: 8px 18px; background: {ingesting ? '#555' : '#00fc40'}; color: #000; border: 1.5px solid #1a1a1a; cursor: {ingesting ? 'wait' : 'pointer'}; font-family: monospace; font-size: 11px; font-weight: 900; letter-spacing: 0.06em;">
          {ingesting ? 'INGESTING…' : 'INGEST'}
        </button>
        {#if ingMsg}
          <span style="font-size: 11px; color: {ingMsg.startsWith('') ? '#007518' : '#be2d06'};">{ingMsg}</span>
        {/if}
      </div>
    </div>
  {/if}

  <!-- SEARCH -->
  {#if activeTab === 'search'}
    <div style="background: #fafaf5; border: 1.5px solid #1a1a1a; padding: 16px; margin-bottom: 14px;">
      <div style="display: flex; gap: 8px; align-items: center;">
        <input
          bind:value={searchQ}
          onkeydown={(e) => { if (e.key === 'Enter') doSearch(); }}
          placeholder="query text..."
          style="flex: 1; padding: 8px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff;"
        />
        <button onclick={doSearch} disabled={searching || !searchQ.trim()} style="padding: 8px 18px; background: {searching ? '#555' : '#00fc40'}; color: #000; border: 1.5px solid #1a1a1a; cursor: {searching ? 'wait' : 'pointer'}; font-family: monospace; font-size: 11px; font-weight: 900;">
          {searching ? 'SEARCHING…' : 'SEARCH'}
        </button>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-top: 12px; align-items: end;">
        <div>
          <div style="font-size: 10px; font-weight: 900; letter-spacing: 0.06em; color: #555; margin-bottom: 6px;">TOP_K: {searchTopK}</div>
          <input type="range" min="1" max="50" bind:value={searchTopK} style="width: 100%;" />
        </div>
        <div>
          <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; cursor: pointer;">
            <input type="checkbox" bind:checked={searchHybrid} />
            <span style="font-weight: 900; letter-spacing: 0.06em;">HYBRID (BM25+VECTOR)</span>
          </label>
        </div>
        <div>
          <div style="font-size: 10px; font-weight: 900; letter-spacing: 0.06em; color: #555; margin-bottom: 6px;">USER_ATTRS (JSON)</div>
          <input bind:value={searchUserAttrs} placeholder={'{"store_id": 1}'} style="width: 100%; padding: 6px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff; box-sizing: border-box;" />
        </div>
      </div>
    </div>

    {#if searchResults.length === 0 && !searching}
      <div style="background: #fafaf5; border: 1.5px solid #ddd; padding: 20px; text-align: center; color: #888; font-size: 11px;">no results yet</div>
    {:else if searchResults.length > 0}
      <table style="width: 100%; background: #fafaf5; border: 1.5px solid #1a1a1a; border-collapse: collapse; font-size: 11px;">
        <thead style="background: #1a1a1a; color: #00fc40;">
          <tr>
            <th style="padding: 8px 10px; text-align: right;">SCORE</th>
            <th style="padding: 8px 10px; text-align: left;">NAMESPACE</th>
            <th style="padding: 8px 10px; text-align: left;">SOURCE_ID</th>
            <th style="padding: 8px 10px; text-align: left;">TEXT</th>
            <th style="padding: 8px 10px; text-align: left;">SCOPE_ATTRS</th>
          </tr>
        </thead>
        <tbody>
          {#each searchResults as r}
            <tr style="border-bottom: 1px solid #eee; background: #fff;">
              <td style="padding: 6px 10px; text-align: right; font-weight: 700; color: #007518;">{(r.score ?? r.distance ?? 0).toFixed?.(3) ?? r.score}</td>
              <td style="padding: 6px 10px; color: #555;">{r.namespace || '—'}</td>
              <td style="padding: 6px 10px; color: #555;">{r.source_id || '—'}</td>
              <td style="padding: 6px 10px;">{snippet(r.text, 200)}</td>
              <td style="padding: 6px 10px;"><pre style="margin: 0; font-size: 10px; white-space: pre-wrap;">{prettyJSON(r.scope_attrs)}</pre></td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}

  <!-- RLS TEST -->
  {#if activeTab === 'rls'}
    <div style="background: #fafaf5; border: 1.5px solid #1a1a1a; padding: 16px; margin-bottom: 14px;">
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
        <span style="font-size: 11px; font-weight: 900; letter-spacing: 0.06em;">8 CANNED TESTS</span>
        <button onclick={runAllTests} disabled={testRunning} style="padding: 8px 18px; background: {testRunning ? '#555' : '#00fc40'}; color: #000; border: 1.5px solid #1a1a1a; cursor: {testRunning ? 'wait' : 'pointer'}; font-family: monospace; font-size: 11px; font-weight: 900;">
          {testRunning ? 'RUNNING…' : '▶ RUN ALL'}
        </button>
        <button onclick={seedFixtures} disabled={seeding} style="padding: 8px 14px; background: {seeding ? '#555' : '#ff9d00'}; color: #000; border: 1.5px solid #1a1a1a; cursor: {seeding ? 'wait' : 'pointer'}; font-family: monospace; font-size: 11px; font-weight: 900;">
          {seeding ? 'SEEDING…' : 'SEED FIXTURES'}
        </button>
        {#if seedMsg}
          <span style="font-size: 11px; color: {seedMsg.startsWith('') ? '#007518' : '#be2d06'};">{seedMsg}</span>
        {/if}
      </div>

      {#if fixturesMissing}
        <div style="background: #fff; border: 2px solid #ff9d00; color: #be2d06; padding: 12px; font-size: 11px; margin-bottom: 12px;">
          <Icon name="alert-triangle" size={14} /> no fixtures — click <Icon name="sprout" size={14} /> SEED FIXTURES (or POST /api/projects/{slug}/vectors/seed-fixtures)
        </div>
      {/if}

      <table style="width: 100%; border-collapse: collapse; font-size: 11px;">
        <thead style="background: #1a1a1a; color: #00fc40;">
          <tr>
            <th style="padding: 8px 10px; text-align: left; width: 60px;">#</th>
            <th style="padding: 8px 10px; text-align: left;">TEST</th>
            <th style="padding: 8px 10px; text-align: center; width: 80px;">RESULT</th>
            <th style="padding: 8px 10px; text-align: left;">DETAIL</th>
            <th style="padding: 8px 10px; text-align: center; width: 80px;">RUN</th>
          </tr>
        </thead>
        <tbody>
          {#each tests as t, i}
            <tr style="border-bottom: 1px solid #eee; background: #fff;">
              <td style="padding: 6px 10px; color: #888;">{i + 1}</td>
              <td style="padding: 6px 10px; font-weight: 700;">{t.name}</td>
              <td style="padding: 6px 10px; text-align: center;">
                {#if t.running}
                  <span style="color: #888;">…</span>
                {:else if t.pass === true}
                  <span style="background: #007518; color: #fff; padding: 2px 8px; font-weight: 900; letter-spacing: 0.06em;"><Icon name="check" size={14} /> PASS</span>
                {:else if t.pass === false}
                  <span style="background: #be2d06; color: #fff; padding: 2px 8px; font-weight: 900; letter-spacing: 0.06em;"><Icon name="x" size={14} /> FAIL</span>
                {:else}
                  <span style="color: #888;">—</span>
                {/if}
              </td>
              <td style="padding: 6px 10px; color: #555; font-size: 10px;">{t.detail || '—'}</td>
              <td style="padding: 6px 10px; text-align: center;">
                <button onclick={() => runTest(i)} disabled={t.running || testRunning} style="padding: 3px 8px; background: #1a1a1a; color: #00fc40; border: 1px solid #1a1a1a; cursor: pointer; font-family: monospace; font-size: 10px; font-weight: 900;">▶</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
