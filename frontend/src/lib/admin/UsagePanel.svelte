<script lang="ts">
  import { dashFetch } from '$lib/api';
  import { onMount, onDestroy } from 'svelte';

  let { embedded = false } = $props();

  // ---- left-rail nav (grouped, admin-Overview style) ----
  const NAV = [
    { label: 'Overview', items: [['overview', 'Overview', 'grid'], ['performance', 'Performance', 'gauge'], ['errors', 'Errors', 'alert']] },
    { label: 'Models & Tokens', items: [['models', 'Models', 'cube'], ['tokens', 'Tokens', 'coins'], ['embeddings', 'Embeddings', 'vector']] },
    { label: 'Learning', items: [['learning', 'Like / Dislike', 'thumb']] },
    { label: 'People', items: [['people', 'People', 'people2']] },
    { label: 'Analytics', items: [['tools', 'Tools', 'wrench'], ['security', 'Security', 'shield'], ['entities', 'Entities', 'users']] },
    { label: 'Billing', items: [['billing', 'Billing', 'receipt'], ['live', 'Live', 'bolt']] },
  ] as { label: string; items: [string, string, string][] }[];
  const ICONS: Record<string, string> = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    gauge: '<path d="M12 14l4-4"/><circle cx="12" cy="13" r="8"/>',
    alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L3 17.8 6.2 21l6.3-6.3a4 4 0 0 0 5.2-5.4l-2.6 2.6-2.1-2.1z"/>',
    shield: '<path d="M12 3l8 3v5c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6z"/>',
    users: '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 6a3 3 0 0 1 0 6M21 20a6 6 0 0 0-4-5.6"/>',
    receipt: '<path d="M5 3v18l2-1 2 1 2-1 2 1 2-1 2 1V3l-2 1-2-1-2 1-2-1-2 1z"/><path d="M9 8h6M9 12h6"/>',
    bolt: '<path d="M13 2 4 14h6l-1 8 9-12h-6z"/>',
    people2: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    cube: '<path d="M12 2 3 7v10l9 5 9-5V7z"/><path d="M3 7l9 5 9-5M12 12v10"/>',
    coins: '<ellipse cx="9" cy="6" rx="6" ry="3"/><path d="M3 6v6c0 1.7 2.7 3 6 3s6-1.3 6-3V6"/><path d="M15 12c2.8.2 6 1.3 6 3v0c0 1.7-2.7 3-6 3-1 0-2-.1-3-.3"/>',
    vector: '<circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M6.7 7.3 10.5 16M17.3 7.3 13.5 16M7 6h10"/>',
    thumb: '<path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z"/><path d="M7 11l4-8a2 2 0 0 1 2 2v3h5a2 2 0 0 1 2 2.3l-1.2 6A2 2 0 0 1 16.8 20H7"/>',
  };
  let tab = $state('overview');
  let loaded: Record<string, boolean> = $state({});
  let lastMs = $state(0); // last fetch latency for the live·Xms badge

  // ---- shared filters ----
  const SOURCES = [
    { id: 'platform', label: 'Platform' }, { id: 'api_key', label: 'API Keys' },
    { id: 'embedding', label: 'Embedding' }, { id: 'training', label: 'Training' },
    { id: 'embed', label: 'Embed' },
  ];
  let selSrc: Record<string, boolean> = $state(Object.fromEntries(SOURCES.map((s) => [s.id, true])));
  let preset = $state('7d');
  let fromStr = $state(''); let toStr = $state('');
  let fModel = $state(''); let fStore = $state(''); let fActor = $state(''); let fStatus = $state('');
  let groupBy = $state('actor');

  // ---- per-tab data ----
  let ov: any = $state(null), perf: any = $state(null), errs: any = $state(null);
  let tools: any = $state(null), sec: any = $state(null), live: any = $state(null);
  let budget: any = $state(null), invoice: any = $state(null);
  // analytics expansion
  let modelsData: any = $state(null), tokensData: any = $state(null);
  let embedsData: any = $state(null), feedbackData: any = $state(null);
  let entUsers: any[] = $state([]), entStores: any[] = $state([]);
  let logins: any[] = $state([]);
  // people activity
  let people: any = $state(null);
  let peopleSort = $state('requests'); let peopleDesc = $state(true);
  let showService = $state(true); let peopleSearch = $state('');
  let peopleSeg = $state('app'); // 'app' (registered) | 'embed' (anonymous widget visitors)
  let embedPeople: any = $state(null); let embedView = $state('session'); // 'session' | 'widget'
  let loading = $state(false); let error = $state('');

  // budget edit
  let bDaily = $state(0); let bMonthly = $state(0); let bSaving = $state(false);
  let invGroup = $state('store');

  // drawer
  let drawerOpen = $state(false); let drawerType = $state(''); let drawerId = $state('');
  let drawerData: any = $state(null); let drawerLoading = $state(false);

  // live auto-refresh
  let autoRefresh = $state(false); let timer: any = null;

  function presetRange(p: string) {
    const now = new Date(); const end = now.toISOString(); const d = new Date(now);
    if (p === '24h') d.setDate(d.getDate() - 1);
    else if (p === '7d') d.setDate(d.getDate() - 7);
    else if (p === '30d') d.setDate(d.getDate() - 30);
    else if (p === 'mtd') { d.setDate(1); d.setHours(0, 0, 0, 0); }
    return { from: d.toISOString(), to: end };
  }
  function qs(extra: Record<string, string> = {}): string {
    let from: string, to: string;
    if (preset === 'custom' && fromStr) { from = fromStr; to = toStr || new Date().toISOString(); }
    else { const r = presetRange(preset); from = r.from; to = r.to; }
    const q = new URLSearchParams({ from, to });
    const srcs = SOURCES.filter((s) => selSrc[s.id]).map((s) => s.id);
    if (srcs.length && srcs.length < SOURCES.length) q.set('src', srcs.join(','));
    if (fModel) q.set('model', fModel); if (fStore) q.set('store', fStore);
    if (fActor) q.set('actor', fActor); if (fStatus) q.set('status', fStatus);
    for (const k in extra) q.set(k, extra[k]);
    return q.toString();
  }
  async function jget(url: string, _retry = 0): Promise<any> {
    const r = await dashFetch(url, { headers: { Accept: 'application/json' } });
    if (r.status === 429 && _retry < 2) {
      // rate limited — honour Retry-After (capped) then retry once or twice
      const ra = Math.min(5, Math.max(1, parseInt(r.headers.get('Retry-After') || '1') || 1));
      await new Promise((res) => setTimeout(res, ra * 1000));
      return jget(url, _retry + 1);
    }
    if (r.status === 429) throw new Error('Too many requests — please wait a moment and refresh.');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function loadTab(t: string, force = false) {
    if (loaded[t] && !force) return;
    loading = true; error = ''; const _t0 = Date.now();
    try {
      if (t === 'overview') {
        const [a, b] = await Promise.all([jget(`/api/admin/usage?${qs({ group_by: groupBy })}`), jget(`/api/admin/usage/logins?${qs()}`)]);
        ov = a; logins = b?.logins ?? []; if (a?.error) error = a.error;
      } else if (t === 'performance') perf = await jget(`/api/admin/usage/performance?${qs()}`);
      else if (t === 'models') modelsData = await jget(`/api/admin/usage/models?${qs()}`);
      else if (t === 'tokens') tokensData = await jget(`/api/admin/usage/tokens?${qs()}`);
      else if (t === 'embeddings') embedsData = await jget(`/api/admin/usage/embeddings?${qs()}`);
      else if (t === 'learning') feedbackData = await jget(`/api/admin/usage/feedback?${qs()}`);
      else if (t === 'errors') errs = await jget(`/api/admin/usage/errors?${qs()}`);
      else if (t === 'tools') tools = await jget(`/api/admin/usage/tools?${qs()}`);
      else if (t === 'security') sec = await jget(`/api/admin/usage/security?${qs()}`);
      else if (t === 'live') live = await jget(`/api/admin/usage/live`);
      else if (t === 'billing') {
        const [b, i] = await Promise.all([jget(`/api/admin/usage/budget`), jget(`/api/admin/usage/invoice?${qs({ group: invGroup })}`)]);
        budget = b; invoice = i; bDaily = b?.daily_usd ?? 0; bMonthly = b?.monthly_usd ?? 0;
      } else if (t === 'people') {
        if (peopleSeg === 'embed') {
          embedPeople = await jget(`/api/admin/usage/embed-people?${qs()}`);
          if (embedPeople?.error) error = embedPeople.error;
        } else {
          people = await jget(`/api/admin/usage/people?${qs({ include_service: String(showService) })}`);
          if (people?.error) error = people.error;
        }
      } else if (t === 'entities') {
        const u = await jget(`/api/admin/usage?${qs({ group_by: 'actor', limit: '1' })}`);
        const s = await jget(`/api/admin/usage?${qs({ group_by: 'store_id', limit: '1' })}`);
        entUsers = u?.breakdown ?? []; entStores = s?.breakdown ?? [];
      }
      loaded[t] = true; loaded = { ...loaded };
    } catch (e: any) { error = e?.message || String(e); }
    finally { loading = false; lastMs = Date.now() - _t0; }
  }
  function switchTab(t: string) { tab = t; loadTab(t); }
  function reloadAll() { loaded = {}; loadTab(tab, true); }

  async function openDrawer(type: string, id: string) {
    drawerOpen = true; drawerType = type; drawerId = id; drawerData = null; drawerLoading = true;
    try { drawerData = await jget(`/api/admin/usage/entity?type=${type}&id=${encodeURIComponent(id)}&${qs()}`); }
    catch (e: any) { drawerData = { error: e?.message }; }
    finally { drawerLoading = false; }
  }
  function closeDrawer() { drawerOpen = false; drawerData = null; }

  async function openPerson(username: string) {
    drawerOpen = true; drawerType = 'person'; drawerId = username; drawerData = null; drawerLoading = true;
    try { drawerData = await jget(`/api/admin/usage/person?username=${encodeURIComponent(username)}&${qs()}`); }
    catch (e: any) { drawerData = { error: e?.message }; }
    finally { drawerLoading = false; }
  }
  async function openEmbedSession(session: string, label: string) {
    drawerOpen = true; drawerType = 'embed'; drawerId = label || session; drawerData = null; drawerLoading = true;
    try { drawerData = await jget(`/api/admin/usage/embed-session?session=${encodeURIComponent(session)}&${qs()}`); }
    catch (e: any) { drawerData = { error: e?.message }; }
    finally { drawerLoading = false; }
  }
  function switchSeg(seg: string) { peopleSeg = seg; loadTab('people', true); }
  function setPeopleSort(col: string) { if (peopleSort === col) peopleDesc = !peopleDesc; else { peopleSort = col; peopleDesc = true; } }
  const sortedPeople = $derived.by(() => {
    let rows = [...((people?.people ?? []) as any[])];
    const q = peopleSearch.trim().toLowerCase();
    if (q) rows = rows.filter((r) => (r.username || '').toLowerCase().includes(q) || (r.email || '').toLowerCase().includes(q));
    const k = peopleSort;
    rows.sort((a, b) => {
      let av = a[k], bv = b[k];
      if (k === 'last_active') { av = av ? new Date(av).getTime() : 0; bv = bv ? new Date(bv).getTime() : 0; }
      else if (k === 'username') { av = (av || '').toLowerCase(); bv = (bv || '').toLowerCase(); return peopleDesc ? bv.localeCompare(av) : av.localeCompare(bv); }
      else if (k === 'satisfaction') { av = av ?? -1; bv = bv ?? -1; }
      else { av = av ?? 0; bv = bv ?? 0; }
      return peopleDesc ? (bv - av) : (av - bv);
    });
    return rows;
  });

  async function saveBudget() {
    bSaving = true;
    try {
      await dashFetch('/api/admin/usage/budget', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ daily_usd: Number(bDaily), monthly_usd: Number(bMonthly) }) });
      await loadTab('billing', true);
    } catch (e: any) { error = e?.message; } finally { bSaving = false; }
  }

  function toggleAuto() {
    autoRefresh = !autoRefresh;
    if (autoRefresh) { timer = setInterval(() => loadTab('live', true), 5000); }
    else if (timer) { clearInterval(timer); timer = null; }
  }
  onDestroy(() => { if (timer) clearInterval(timer); });

  // ---- formatters ----
  function usd(n: number): string { if (n == null) return '$0'; if (n > 0 && n < 0.01) return '$' + n.toFixed(4); return '$' + n.toFixed(2); }
  function compact(n: number): string { if (n == null) return '0'; if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M'; if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K'; return String(n); }
  function ms(n: number): string { if (n == null) return '—'; if (n >= 1000) return (n / 1000).toFixed(2) + 's'; return Math.round(n) + 'ms'; }
  function srcLabel(id: string): string { return SOURCES.find((s) => s.id === id)?.label ?? id; }
  function shortTs(ts: string): string { const d = new Date(ts); if (isNaN(d.getTime())) return ts; return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  function ago(ts: string | null): string { if (!ts) return '—'; const d = new Date(ts).getTime(); if (!d) return '—'; const s = Math.floor((Date.now() - d) / 1000); if (s < 60) return s + 's'; if (s < 3600) return Math.floor(s / 60) + 'm'; if (s < 86400) return Math.floor(s / 3600) + 'h'; return Math.floor(s / 86400) + 'd'; }
  function delta(cur: number, prev: number): { txt: string; up: boolean; flat: boolean } {
    if (!prev) return { txt: cur ? 'new' : '—', up: true, flat: !cur };
    const p = ((cur - prev) / prev) * 100;
    return { txt: (p >= 0 ? '▲' : '▼') + Math.abs(p).toFixed(0) + '%', up: p >= 0, flat: Math.abs(p) < 1 };
  }

  // ---- overview stacked charts (shared color map) ----
  const COLORS = ['#a855c9', '#84cc16', '#f97316', '#06b6d4', '#3b82f6', '#ec4899'];
  const OTHER_C = '#9ca3af';
  const modelSeries = $derived((ov?.model_series ?? []) as any[]);
  const modelColor: Record<string, string> = $derived.by(() => {
    const t: Record<string, number> = {}; for (const r of modelSeries) t[r.model] = (t[r.model] || 0) + (r.tokens || 0);
    const ranked = Object.keys(t).sort((a, b) => t[b] - t[a]); const m: Record<string, string> = {};
    ranked.slice(0, COLORS.length).forEach((x, i) => (m[x] = COLORS[i])); return m;
  });
  function mval(r: any, k: string) { return k === 'cost' ? (r.cost || 0) : k === 'requests' ? (r.requests || 0) : (r.tokens || 0); }
  function buildChart(metric: string) {
    const bmap: Record<string, Record<string, number>> = {}; const order: string[] = [];
    for (const r of modelSeries) { if (!bmap[r.bucket]) { bmap[r.bucket] = {}; order.push(r.bucket); } const k = modelColor[r.model] ? r.model : '__o'; bmap[r.bucket][k] = (bmap[r.bucket][k] || 0) + mval(r, metric); }
    const buckets = order.map((b) => { const segs: any[] = []; let total = 0; for (const k in bmap[b]) { const v = bmap[b][k]; total += v; segs.push({ val: v, color: k === '__o' ? OTHER_C : modelColor[k] }); } return { segs, total }; });
    const maxT = Math.max(1e-9, ...buckets.map((x) => x.total));
    const lt: Record<string, number> = {}; for (const r of modelSeries) { const k = modelColor[r.model] ? r.model : '__o'; lt[k] = (lt[k] || 0) + mval(r, metric); }
    const legend = Object.keys(lt).sort((a, b) => lt[b] - lt[a]).slice(0, 4).map((k) => ({ label: k === '__o' ? 'Others' : k, value: lt[k], color: k === '__o' ? OTHER_C : modelColor[k] }));
    return { buckets, maxT, legend, grand: buckets.reduce((s, x) => s + x.total, 0) };
  }
  const spendChart = $derived(buildChart('cost'));
  const reqChart = $derived(buildChart('requests'));
  const tokChart = $derived(buildChart('tokens'));

  // heatmap
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const heatMax = $derived(Math.max(1, ...((ov?.heatmap ?? []).map((h: any) => h.requests))));
  function heatVal(dow: number, hour: number): number { const h = (ov?.heatmap ?? []).find((x: any) => x.dow === dow && x.hour === hour); return h ? h.requests : 0; }

  function exportRows(rows: any[][], name: string) {
    const csv = rows.map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const u = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const a = document.createElement('a'); a.href = u; a.download = name; a.click(); URL.revokeObjectURL(u);
  }
  function exportActivity() { const r = [['ts', 'src', 'actor', 'store', 'model', 'tokens', 'cost', 'latency_ms', 'status'], ...(ov?.activity ?? []).map((a: any) => [a.ts, a.src, a.actor, a.store_id, a.model, a.tokens, a.cost, a.latency_ms, a.status])]; exportRows(r, 'usage_activity.csv'); }
  function exportInvoice() { const r = [[invGroup, 'requests', 'tokens', 'cost'], ...(invoice?.rows ?? []).map((x: any) => [x.key, x.requests, x.tokens, x.cost])]; exportRows(r, `invoice_${invGroup}.csv`); }

  onMount(() => loadTab('overview'));
</script>

<div class="usage" class:embedded>
 <div class="u-shell">
  <!-- left rail (admin-Overview style) -->
  <nav class="u-rail">
    {#each NAV as g}
      {#if g.label}<div class="u-railgrp">{g.label}</div>{/if}
      {#each g.items as [id, label, icon]}
        <button class="u-railitem" class:on={tab === id} onclick={() => switchTab(id)}>
          <svg class="u-ricon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{@html ICONS[icon]}</svg>
          <span class="u-rlbl">{label}</span>
          {#if id === 'live'}<span class="u-rdot"></span>{/if}
        </button>
      {/each}
    {/each}
  </nav>

  <!-- main column -->
  <div class="u-main">
  {#if !embedded}
    <header class="u-head">
      <div><h1>{NAV.flatMap((g) => g.items).find(([id]) => id === tab)?.[1] ?? 'Overview'}</h1>
        <p class="u-sub">Spend · requests · tokens · activity — one page</p></div>
      <span class="u-livebadge">● live · {lastMs}ms</span>
    </header>
  {/if}

  <!-- shared filters (hidden on live) -->
  {#if tab !== 'live'}
    <div class="u-filters">
      <div class="u-frow">
        {#each [['24h', '24h'], ['7d', '7d'], ['30d', '30d'], ['mtd', 'MTD'], ['custom', 'Custom']] as [p, l]}
          <button class="u-chip" class:on={preset === p} onclick={() => { preset = p; if (p !== 'custom') reloadAll(); }}>{l}</button>
        {/each}
        {#if preset === 'custom'}
          <input class="u-date" type="datetime-local" bind:value={fromStr} /><span class="u-arrow">→</span>
          <input class="u-date" type="datetime-local" bind:value={toStr} /><button class="u-btn" onclick={reloadAll}>Apply</button>
        {/if}
        <button class="u-btn ghost" onclick={reloadAll} title="Refresh">↻</button>
      </div>
      <div class="u-frow">
        <span class="u-lbl">Source</span>
        {#each SOURCES as s}<button class="u-chip src" class:on={selSrc[s.id]} onclick={() => { selSrc[s.id] = !selSrc[s.id]; selSrc = { ...selSrc }; reloadAll(); }}>{s.label}</button>{/each}
        <input class="u-in" placeholder="model…" bind:value={fModel} onchange={reloadAll} />
        <input class="u-in" placeholder="store…" bind:value={fStore} onchange={reloadAll} />
        <input class="u-in" placeholder="user/key…" bind:value={fActor} onchange={reloadAll} />
      </div>
    </div>
  {/if}

  {#if error}<div class="u-err">⚠ {error}</div>{/if}
  {#if loading}<div class="u-load">loading…</div>{/if}

  <!-- ============ OVERVIEW ============ -->
  {#if tab === 'overview' && ov}
    {@const t = ov.totals}{@const p = ov.prev}
    <div class="u-tiles">
      <div class="u-tile"><b>{usd(t.spend)}</b><span>Spend</span></div>
      <div class="u-tile"><b>{compact(t.requests)}</b><span>Requests</span></div>
      <div class="u-tile"><b>{compact((t.tokens_in || 0) + (t.tokens_out || 0))}</b><span>Tokens</span></div>
      <div class="u-tile" class:bad={t.errors > 0}><b>{t.errors}</b><span>Errors</span></div>
      <div class="u-tile"><b>{ov.adoption?.active ?? 0}<small>/{ov.adoption?.total_users ?? 0}</small></b><span>Active users</span></div>
    </div>
    <!-- ◷ TRENDS -->
    <section class="u-sec">
      <div class="u-sech"><span class="u-sect">Trends</span><span class="u-secbadge">● live · {lastMs}ms</span></div>
      <div class="u-cards3">
        {#each [['Spend', spendChart, true, t.spend, p.spend], ['Requests', reqChart, false, t.requests, p.requests], ['Tokens', tokChart, false, (t.tokens_in + t.tokens_out), p.tokens]] as [title, ch, money, cur, prv]}
          {@const d = delta(cur, prv)}
          <div class="oc">
            <div class="oc-head">{title}<span class="oc-delta" class:up={d.up} class:flat={d.flat}>{d.txt}</span></div>
            <div class="oc-total">{money ? usd(ch.grand) : compact(ch.grand)}</div>
            <div class="oc-bars">
              {#each ch.buckets as b}<div class="oc-col" title={money ? usd(b.total) : compact(b.total)}><div class="oc-stack" style={`height:${Math.round((b.total / ch.maxT) * 100)}%`}>{#each b.segs as s}<div class="oc-seg" style={`flex:${s.val || 0.0001};background:${s.color}`}></div>{/each}</div></div>{/each}
              {#if !ch.buckets.length}<div class="u-empty">no activity</div>{/if}
            </div>
            <div class="oc-legend">{#each ch.legend as l}<div class="oc-leg"><span class="dot" style={`background:${l.color}`}></span><span class="lbl">{l.label}</span><span class="val">{money ? usd(l.value) : compact(l.value)}</span></div>{/each}</div>
          </div>
        {/each}
      </div>
      <div class="u-substats">
        <span>Errors <b class:bad={t.errors > 0}>{t.errors}</b></span>
        <span>Cost/req <b>{usd(ov.cost_per?.per_request || 0)}</b></span>
        <span>Cost/1k tok <b>{usd(ov.cost_per?.per_1k_tokens || 0)}</b></span>
        <span>Active users <b>{ov.adoption?.active ?? 0}</b>/{ov.adoption?.total_users ?? 0}</span>
        <span>DAU <b>{ov.adoption?.dau ?? 0}</b> · WAU <b>{ov.adoption?.wau ?? 0}</b></span>
      </div>
    </section>

    <!-- ◷ BREAKDOWN -->
    <section class="u-sec">
      <div class="u-sech"><span class="u-sect">Breakdown</span><span class="u-secbadge">● live · {lastMs}ms</span></div>
      <div class="u-grid2">
        <div class="u-card"><div class="u-ctitle">By source</div>
          <table class="u-tbl"><thead><tr><th>source</th><th>req</th><th>tokens</th><th>cost</th></tr></thead><tbody>
            {#each ov.by_source as r}<tr><td><span class="u-tag {r.src}">{srcLabel(r.src)}</span></td><td>{compact(r.requests)}</td><td>{compact(r.tokens)}</td><td class="num">{usd(r.cost)}</td></tr>{/each}
          </tbody></table>
        </div>
        <div class="u-card"><div class="u-ctitle">By model (cost)</div>
          <table class="u-tbl"><thead><tr><th>model</th><th>req</th><th>tokens</th><th>cost</th></tr></thead><tbody>
            {#each ov.by_model as r}<tr><td class="mono">{r.model}</td><td>{compact(r.requests)}</td><td>{compact(r.tokens)}</td><td class="num">{usd(r.cost)}</td></tr>{/each}
          </tbody></table>
        </div>
      </div>
    </section>

    <!-- ◷ ACTIVITY HEATMAP -->
    <section class="u-sec">
      <div class="u-sech"><span class="u-sect">Activity heatmap · day × hour</span><span class="u-secbadge">● live · {lastMs}ms</span></div>
      <div class="heat">
        <div class="heat-row head"><span class="heat-lbl"></span>{#each Array(24) as _, h}<span class="heat-h">{h % 6 === 0 ? h : ''}</span>{/each}</div>
        {#each DOW as dname, di}
          <div class="heat-row"><span class="heat-lbl">{dname}</span>{#each Array(24) as _, h}{@const v = heatVal(di, h)}<span class="heat-c" title={`${dname} ${h}:00 · ${v} req`} style={`background:rgba(154,74,47,${v ? 0.12 + 0.88 * (v / heatMax) : 0.03})`}></span>{/each}</div>
        {/each}
      </div>
    </section>

    <!-- ◷ USERS & ACTIVITY -->
    <section class="u-sec">
      <div class="u-sech"><span class="u-sect">Users &amp; activity</span><span class="u-secbadge">● live · {lastMs}ms</span></div>
      <div class="u-stack2">
        <div class="u-card"><div class="u-ctitle">Breakdown by
          <select class="u-mini" bind:value={groupBy} onchange={() => loadTab('overview', true)}><option value="actor">user/key</option><option value="model">model</option><option value="store_id">store</option><option value="src">source</option><option value="status">status</option></select></div>
          <div class="u-scroll">
          <table class="u-tbl"><thead><tr><th>{groupBy}</th><th>req</th><th>cost</th><th>last</th></tr></thead><tbody>
            {#each ov.breakdown as r}<tr class="click" onclick={() => openDrawer(groupBy === 'store_id' ? 'store' : 'actor', r.key)}><td class="mono">{r.key}</td><td>{compact(r.requests)}</td><td class="num">{usd(r.cost)}</td><td class="dim">{ago(r.last)}</td></tr>{/each}
          </tbody></table>
          </div>
        </div>
        <div class="u-card"><div class="u-ctitle">Who — logins &amp; usage</div>
          <div class="u-scroll">
          <table class="u-tbl"><thead><tr><th>user</th><th>role</th><th>last login</th><th>req</th><th>cost</th></tr></thead><tbody>
            {#each logins as r}<tr class="click" onclick={() => openDrawer('actor', r.username)}><td class="mono">{r.username}</td><td>{r.role}</td><td class="dim">{ago(r.last_login)}</td><td>{compact(r.requests)}</td><td class="num">{usd(r.cost)}</td></tr>{/each}
          </tbody></table>
          </div>
        </div>
      </div>
      <div class="u-card" style="margin-top:14px;"><div class="u-ctitle">Activity log<button class="u-btn ghost sm" onclick={exportActivity}>export csv</button></div>
        <table class="u-tbl"><thead><tr><th>time</th><th>src</th><th>actor</th><th>store</th><th>model</th><th>tok</th><th>cost</th><th>lat</th><th>status</th></tr></thead><tbody>
          {#each ov.activity as a}<tr><td class="dim">{shortTs(a.ts)}</td><td><span class="u-tag {a.src}">{srcLabel(a.src)}</span></td><td class="mono">{a.actor ?? '—'}</td><td class="mono">{a.store_id ?? '—'}</td><td class="mono dim">{a.model ?? '—'}</td><td>{compact(a.tokens)}</td><td class="num">{usd(a.cost)}</td><td class="dim">{ms(a.latency_ms)}</td><td><span class="u-st {a.status}">{a.status}</span></td></tr>{/each}
        </tbody></table>
      </div>
    </section>
  {/if}

  <!-- ============ MODELS ============ -->
  {#if tab === 'models' && modelsData}
    {@const bm = (modelsData.by_model ?? []) as any[]}
    {@const maxCost = Math.max(0.0001, ...bm.map((r) => r.cost || 0))}
    <section class="ua">
      <div class="ua-grid2">
        <div class="ua-card">
          <div class="ua-h">Spend by model</div>
          <table class="ua-tbl">
            <thead><tr><th>Model</th><th class="r">Requests</th><th class="r">Tokens</th><th class="r">Cost</th><th></th></tr></thead>
            <tbody>
              {#each bm as r}
                <tr><td class="mono">{r.model}</td><td class="r">{compact(r.requests)}</td><td class="r">{compact(r.tokens)}</td><td class="r">{usd(r.cost)}</td>
                  <td class="barcell"><span class="bar" style="width:{Math.round(100*(r.cost||0)/maxCost)}%"></span></td></tr>
              {/each}
              {#if !bm.length}<tr><td colspan="5" class="u-empty">no model usage in this window</td></tr>{/if}
            </tbody>
          </table>
        </div>
        <div class="ua-card">
          <div class="ua-h">Trending <span class="ua-sub">vs previous {preset}</span></div>
          <div class="ua-trend">
            {#each (modelsData.trending ?? []) as t}
              <div class="ua-trow">
                <span class="ua-tmodel mono" title={t.model}>{t.model}</span>
                <span class="ua-tcost">{usd(t.cost)}</span>
                {#if t.is_new}<span class="ua-tnew">▲ New</span>
                {:else if t.pct == null}<span class="ua-tflat">—</span>
                {:else if t.pct >= 0}<span class="ua-tup">↑ {t.pct}%</span>
                {:else}<span class="ua-tdown">↓ {Math.abs(t.pct)}%</span>{/if}
              </div>
            {/each}
            {#if !(modelsData.trending ?? []).length}<div class="u-empty">no trend data</div>{/if}
          </div>
        </div>
      </div>
    </section>
  {/if}

  <!-- ============ TOKENS ============ -->
  {#if tab === 'tokens' && tokensData}
    {@const td = tokensData}
    {@const totTok = (td.prompt||0)+(td.completion||0)+(td.reasoning||0)}
    {@const _p = totTok || 1}
    {@const _q = (td.prompt||1)}
    <section class="ua">
      <div class="ua-kpis">
        <div class="ua-kpi"><div class="ua-kn">{compact(td.prompt)}</div><div class="ua-kl">Prompt tokens</div></div>
        <div class="ua-kpi"><div class="ua-kn">{compact(td.completion)}</div><div class="ua-kl">Completion</div></div>
        <div class="ua-kpi"><div class="ua-kn">{compact(td.reasoning)}</div><div class="ua-kl">⚙ Reasoning</div></div>
        <div class="ua-kpi"><div class="ua-kn">{compact(td.cached)}</div><div class="ua-kl">⚡ Cached</div></div>
        <div class="ua-kpi ua-kpi-accent"><div class="ua-kn">{td.cache_hit_rate}%</div><div class="ua-kl">Cache hit rate</div></div>
      </div>
      <div class="ua-card">
        <div class="ua-h">Token breakdown</div>
        <div class="ua-stack">
          <span class="seg s-prompt" style="width:{Math.round(100*(td.prompt||0)/_p)}%" title="prompt {compact(td.prompt)}"></span>
          <span class="seg s-compl" style="width:{Math.round(100*(td.completion||0)/_p)}%" title="completion {compact(td.completion)}"></span>
          <span class="seg s-reason" style="width:{Math.round(100*(td.reasoning||0)/_p)}%" title="reasoning {compact(td.reasoning)}"></span>
        </div>
        <div class="ua-legend"><span><i class="dot s-prompt"></i>prompt</span><span><i class="dot s-compl"></i>completion</span><span><i class="dot s-reason"></i>reasoning</span></div>
      </div>
      <div class="ua-card">
        <div class="ua-h">Prompt caching <span class="ua-sub">{compact(td.cached)} cached · {compact(td.uncached)} uncached</span></div>
        <div class="ua-stack">
          <span class="seg s-cached" style="width:{Math.round(100*(td.cached||0)/_q)}%"></span>
          <span class="seg s-uncached" style="width:{Math.round(100*(td.uncached||0)/_q)}%"></span>
        </div>
        <div class="ua-legend"><span><i class="dot s-cached"></i>cached (free/cheap)</span><span><i class="dot s-uncached"></i>uncached</span></div>
      </div>
      <p class="ua-note">Reasoning + cached counts populate from new traffic (OpenRouter usage details). Historical rows show 0.</p>
    </section>
  {/if}

  <!-- ============ EMBEDDINGS ============ -->
  {#if tab === 'embeddings' && embedsData}
    {@const s = embedsData.summary ?? {}}
    {@const bm = (embedsData.by_model ?? []) as any[]}
    <section class="ua">
      <div class="ua-kpis">
        <div class="ua-kpi"><div class="ua-kn">{compact(s.calls)}</div><div class="ua-kl">Embedding calls</div></div>
        <div class="ua-kpi"><div class="ua-kn">{compact(s.tokens)}</div><div class="ua-kl">Tokens</div></div>
        <div class="ua-kpi"><div class="ua-kn">{usd(s.cost)}</div><div class="ua-kl">Cost</div></div>
        <div class="ua-kpi"><div class="ua-kn">{s.models}</div><div class="ua-kl">Models</div></div>
        <div class="ua-kpi"><div class="ua-kn">{ms(s.avg_latency_ms)}</div><div class="ua-kl">Avg latency</div></div>
      </div>
      <div class="ua-card">
        <div class="ua-h">Recent embedding calls <span class="ua-sub">with the embedded text</span></div>
        {#if !embedsData.text_logging}
          <div class="ua-warn">Text capture is OFF — set <code>EMBED_LOG_INPUT=1</code> to record the embedded text (privacy opt-in). Tokens/cost/model still tracked.</div>
        {/if}
        <table class="ua-tbl">
          <thead><tr><th>When</th><th>Model</th><th>Text / question</th><th class="r">Tokens</th><th class="r">Cost</th><th class="r">Latency</th><th>Actor</th></tr></thead>
          <tbody>
            {#each (embedsData.recent ?? []) as r}
              <tr><td class="nowrap">{r.ts ? new Date(r.ts).toLocaleString() : '—'}</td><td class="mono">{r.model}</td>
                <td class="ua-text">{r.text ?? '—'}</td><td class="r">{compact(r.tokens)}</td><td class="r">{usd(r.cost)}</td>
                <td class="r">{r.latency_ms != null ? ms(r.latency_ms) : '—'}</td><td class="mono">{r.actor}</td></tr>
            {/each}
            {#if !(embedsData.recent ?? []).length}<tr><td colspan="7" class="u-empty">no embedding calls in this window</td></tr>{/if}
          </tbody>
        </table>
      </div>
    </section>
  {/if}

  <!-- ============ LEARNING (like / dislike) ============ -->
  {#if tab === 'learning' && feedbackData}
    {@const t = feedbackData.totals ?? {}}
    <section class="ua">
      <div class="ua-kpis">
        <div class="ua-kpi ua-kpi-good"><div class="ua-kn">👍 {compact(t.up)}</div><div class="ua-kl">Liked</div></div>
        <div class="ua-kpi ua-kpi-bad"><div class="ua-kn">👎 {compact(t.down)}</div><div class="ua-kl">Disliked</div></div>
        <div class="ua-kpi"><div class="ua-kn">{compact(t.total)}</div><div class="ua-kl">Total rated</div></div>
        <div class="ua-kpi ua-kpi-accent"><div class="ua-kn">{t.satisfaction != null ? t.satisfaction + '%' : '—'}</div><div class="ua-kl">Satisfaction</div></div>
      </div>
      <div class="ua-grid2">
        <div class="ua-card">
          <div class="ua-h">Satisfaction by project</div>
          <table class="ua-tbl">
            <thead><tr><th>Project</th><th class="r">👍</th><th class="r">👎</th><th class="r">Sat.</th></tr></thead>
            <tbody>
              {#each (feedbackData.by_project ?? []) as r}
                <tr><td class="mono">{r.project}</td><td class="r">{r.up}</td><td class="r">{r.down}</td>
                  <td class="r" class:ua-lo={r.satisfaction != null && r.satisfaction < 60}>{r.satisfaction != null ? r.satisfaction + '%' : '—'}</td></tr>
              {/each}
              {#if !(feedbackData.by_project ?? []).length}<tr><td colspan="4" class="u-empty">no feedback in this window</td></tr>{/if}
            </tbody>
          </table>
        </div>
        <div class="ua-card">
          <div class="ua-h">👎 Top disliked answers <span class="ua-sub">retrain candidates</span></div>
          <div class="ua-disliked">
            {#each (feedbackData.disliked ?? []) as d}
              <div class="ua-dcard">
                <div class="ua-dq">{d.question}</div>
                {#if d.answer}<div class="ua-da">{d.answer}</div>{/if}
                {#if d.sql}<code class="ua-dsql">{d.sql}</code>{/if}
                <div class="ua-dmeta">{d.project} · {d.ts ? new Date(d.ts).toLocaleString() : ''}</div>
              </div>
            {/each}
            {#if !(feedbackData.disliked ?? []).length}<div class="u-empty">no disliked answers 🎉</div>{/if}
          </div>
        </div>
      </div>
    </section>
  {/if}

  <!-- ============ PEOPLE ============ -->
  {#if tab === 'people'}
    <div class="u-frow" style="margin-bottom:1rem">
      <div class="u-seg">
        <button class="u-segbtn" class:on={peopleSeg === 'app'} onclick={() => switchSeg('app')}>App users</button>
        <button class="u-segbtn" class:on={peopleSeg === 'embed'} onclick={() => switchSeg('embed')}>Embed users</button>
      </div>
      <span class="u-hint">{peopleSeg === 'app' ? 'Registered accounts — humans + API keys' : 'Anonymous widget visitors — not registered'}</span>
    </div>
  {/if}

  <!-- ---- APP USERS (registered) ---- -->
  {#if tab === 'people' && peopleSeg === 'app' && people}
    {@const s = people.summary ?? {}}
    <div class="u-tiles">
      <div class="u-tile"><b>{s.active ?? 0}<small>/{s.total_users ?? 0}</small></b><span>Active users</span></div>
      <div class="u-tile"><b>{s.most_active ?? '—'}</b><span>Most active · {compact(s.most_active_reqs ?? 0)} req</span></div>
      <div class="u-tile"><b>{s.avg_q_per_user ?? 0}</b><span>Avg questions / user</span></div>
      <div class="u-tile"><b>{s.satisfaction != null ? s.satisfaction + '%' : '—'}</b><span>Satisfaction 👍</span></div>
      <div class="u-tile"><b>{s.humans ?? 0}<small> + {s.service_accounts ?? 0} keys</small></b><span>Humans · API keys</span></div>
    </div>
    <section class="u-sec">
      <div class="u-sech">
        <span class="u-sect">User leaderboard</span>
        <span class="u-secbadge">● live · {lastMs}ms</span>
      </div>
      <div class="u-frow" style="margin-bottom:.6rem">
        <input class="u-in" placeholder="search user / email…" bind:value={peopleSearch} style="min-width:180px" />
        <button class="u-chip" class:on={showService} onclick={() => { showService = !showService; loadTab('people', true); }}>{showService ? '● incl. API keys' : '○ humans only'}</button>
        <span class="u-hint" style="margin-left:auto">click a row → full activity drill-down</span>
      </div>
      <table class="u-tbl u-ppl">
        <thead><tr>
          {#each [['username','User'],['last_active','Last active'],['sessions','Sessions'],['requests','Questions'],['q_per_session','Q/sess'],['satisfaction','👍/👎'],['tokens','Tokens'],['cost','Cost'],['err_pct','Err%']] as [col,lbl]}
            <th class="sortable" class:numh={col!=='username'} onclick={() => setPeopleSort(col)}>
              {lbl}{#if peopleSort === col}<span class="sarrow">{peopleDesc ? '▾' : '▴'}</span>{/if}
            </th>
          {/each}
        </tr></thead>
        <tbody>
          {#each sortedPeople as r}
            <tr class="click" onclick={() => openPerson(r.username)}>
              <td>
                <span class="ppl-name">{r.label}</span>
                {#if r.is_service}<span class="ppl-badge svc">key</span>{:else}<span class="ppl-badge">user</span>{/if}
                {#if !r.active}<span class="ppl-badge off">off</span>{/if}
                {#if r.email}<div class="ppl-email">{r.email}</div>{/if}
              </td>
              <td class="dim">{ago(r.last_active)}</td>
              <td class="num">{compact(r.sessions)}</td>
              <td class="num"><b>{compact(r.requests)}</b></td>
              <td class="num dim">{r.q_per_session ?? 0}</td>
              <td class="num">
                {#if r.satisfaction != null}
                  <span class="sat" class:good={r.satisfaction >= 70} class:bad={r.satisfaction < 50}>{r.satisfaction}%</span>
                  <span class="dim" style="font-size:10px"> {r.ups}/{r.downs}</span>
                {:else}<span class="dim">—</span>{/if}
              </td>
              <td class="num">{compact(r.tokens)}</td>
              <td class="num">{usd(r.cost)}</td>
              <td class="num" class:bad={r.err_pct > 5}>{r.err_pct ?? 0}%</td>
            </tr>
          {/each}
          {#if !sortedPeople.length}<tr><td colspan="9" class="u-empty">no users in this window</td></tr>{/if}
        </tbody>
      </table>
    </section>
  {/if}

  <!-- ---- EMBED USERS (anonymous widget visitors) ---- -->
  {#if tab === 'people' && peopleSeg === 'embed' && embedPeople}
    {@const s = embedPeople.summary ?? {}}
    <div class="u-tiles">
      <div class="u-tile"><b>{compact(s.sessions ?? 0)}</b><span>Visitor sessions</span></div>
      <div class="u-tile"><b>{compact(s.messages ?? 0)}</b><span>Messages</span></div>
      <div class="u-tile"><b>{s.widgets ?? 0}</b><span>Active widgets</span></div>
      <div class="u-tile"><b>{s.avg_msgs_per_session ?? 0}</b><span>Avg msgs / session</span></div>
      <div class="u-tile" class:bad={(s.success_pct ?? 100) < 95}><b>{s.success_pct ?? 100}%</b><span>Success rate</span></div>
    </div>
    <section class="u-sec">
      <div class="u-sech">
        <span class="u-sect">Embed visitors</span>
        <span class="u-secbadge">● live · {lastMs}ms</span>
      </div>
      <div class="u-frow" style="margin-bottom:.6rem">
        <div class="u-seg sm">
          <button class="u-segbtn" class:on={embedView === 'session'} onclick={() => embedView = 'session'}>By session</button>
          <button class="u-segbtn" class:on={embedView === 'widget'} onclick={() => embedView = 'widget'}>By widget</button>
        </div>
        {#if (s.tokens ?? 0) === 0}<span class="u-hint">older calls logged $0 tokens — only new traffic prices</span>{/if}
        <span class="u-hint" style="margin-left:auto">click a session → message history</span>
      </div>
      {#if embedView === 'session'}
        <table class="u-tbl">
          <thead><tr><th>Visitor</th><th>Widget</th><th>Origin</th><th class="numh">Msgs</th><th class="numh">Avg ms</th><th class="numh">Tokens</th><th class="numh">Cost</th><th class="numh">Err</th><th>Last seen</th></tr></thead>
          <tbody>
            {#each embedPeople.by_session as r}
              <tr class="click" onclick={() => openEmbedSession(r.session, r.session_short)}>
                <td><span class="mono">{r.session_short}</span>{#if r.store}<span class="ppl-badge svc">{r.store}</span>{/if}</td>
                <td class="dw-q" style="max-width:160px">{r.widget}</td>
                <td class="dim mono" style="font-size:10.5px">{r.origin ?? '—'}</td>
                <td class="num"><b>{compact(r.messages)}</b></td>
                <td class="num dim">{r.avg_latency_ms}</td>
                <td class="num">{compact(r.tokens)}</td>
                <td class="num">{usd(r.cost)}</td>
                <td class="num" class:bad={r.errors > 0}>{r.errors || 0}</td>
                <td class="dim">{ago(r.last_seen)}</td>
              </tr>
            {/each}
            {#if !embedPeople.by_session.length}<tr><td colspan="9" class="u-empty">no embed visitors in this window</td></tr>{/if}
          </tbody>
        </table>
      {:else}
        <table class="u-tbl">
          <thead><tr><th>Widget</th><th>Store</th><th class="numh">Sessions</th><th class="numh">Messages</th><th class="numh">Cost</th><th class="numh">Err</th><th>Last seen</th></tr></thead>
          <tbody>
            {#each embedPeople.by_widget as r}
              <tr><td class="dw-q" style="max-width:220px">{r.widget}</td><td class="mono dim">{r.store || '—'}</td><td class="num"><b>{compact(r.sessions)}</b></td><td class="num">{compact(r.messages)}</td><td class="num">{usd(r.cost)}</td><td class="num" class:bad={r.errors > 0}>{r.errors || 0}</td><td class="dim">{ago(r.last_seen)}</td></tr>
            {/each}
            {#if !embedPeople.by_widget.length}<tr><td colspan="7" class="u-empty">no widgets active</td></tr>{/if}
          </tbody>
        </table>
      {/if}
    </section>
  {/if}

  <!-- ============ PERFORMANCE ============ -->
  {#if tab === 'performance' && perf}
    <div class="u-kpis">
      <div class="u-kpi"><span class="k">P50</span><b>{ms(perf.overall?.p50)}</b></div>
      <div class="u-kpi"><span class="k">P95</span><b>{ms(perf.overall?.p95)}</b></div>
      <div class="u-kpi"><span class="k">P99</span><b>{ms(perf.overall?.p99)}</b></div>
      <div class="u-kpi"><span class="k">AVG</span><b>{ms(perf.overall?.avg)}</b></div>
    </div>
    <div class="u-grid2">
      <div class="u-card"><div class="u-ctitle">Latency by source</div>
        <table class="u-tbl"><thead><tr><th>source</th><th>p50</th><th>p95</th><th>p99</th><th>n</th></tr></thead><tbody>
          {#each perf.by_source as r}<tr><td><span class="u-tag {r.src}">{srcLabel(r.src)}</span></td><td>{ms(r.p50)}</td><td>{ms(r.p95)}</td><td>{ms(r.p99)}</td><td>{compact(r.n)}</td></tr>{/each}
        </tbody></table>
      </div>
      <div class="u-card"><div class="u-ctitle">Latency by model</div>
        <table class="u-tbl"><thead><tr><th>model</th><th>p50</th><th>p95</th><th>n</th></tr></thead><tbody>
          {#each perf.by_model as r}<tr><td class="mono">{r.model}</td><td>{ms(r.p50)}</td><td>{ms(r.p95)}</td><td>{compact(r.n)}</td></tr>{/each}
        </tbody></table>
      </div>
    </div>
    <div class="u-card"><div class="u-ctitle">Slowest calls</div>
      <table class="u-tbl"><thead><tr><th>time</th><th>src</th><th>actor</th><th>model</th><th>latency</th><th>cost</th></tr></thead><tbody>
        {#each perf.slowest as r}<tr><td class="dim">{shortTs(r.ts)}</td><td><span class="u-tag {r.src}">{srcLabel(r.src)}</span></td><td class="mono">{r.actor}</td><td class="mono dim">{r.model}</td><td><b>{ms(r.latency_ms)}</b></td><td class="num">{usd(r.cost)}</td></tr>{/each}
      </tbody></table>
    </div>
  {/if}

  <!-- ============ ERRORS ============ -->
  {#if tab === 'errors' && errs}
    <div class="u-kpis">
      <div class="u-kpi" class:bad={errs.errors > 0}><span class="k">ERRORS</span><b>{errs.errors}</b></div>
      <div class="u-kpi"><span class="k">TOTAL</span><b>{compact(errs.total)}</b></div>
      <div class="u-kpi" class:bad={errs.rate > 0.02}><span class="k">ERROR RATE</span><b>{(errs.rate * 100).toFixed(2)}%</b></div>
    </div>
    <div class="u-grid2">
      <div class="u-card"><div class="u-ctitle">By source</div>
        <table class="u-tbl"><thead><tr><th>source</th><th>errors</th><th>total</th><th>rate</th></tr></thead><tbody>
          {#each errs.by_source as r}<tr><td><span class="u-tag {r.src}">{srcLabel(r.src)}</span></td><td>{r.errors}</td><td>{compact(r.total)}</td><td>{r.total ? (100 * r.errors / r.total).toFixed(1) : 0}%</td></tr>{/each}
        </tbody></table>
      </div>
      <div class="u-card"><div class="u-ctitle">Top error codes</div>
        <table class="u-tbl"><thead><tr><th>error</th><th>count</th></tr></thead><tbody>
          {#each errs.by_code as r}<tr><td class="mono">{r.code}</td><td>{r.count}</td></tr>{/each}
        </tbody></table>
      </div>
    </div>
    <div class="u-card"><div class="u-ctitle">Recent errors</div>
      <table class="u-tbl"><thead><tr><th>time</th><th>kind</th><th>name</th><th>error</th></tr></thead><tbody>
        {#each errs.recent as r}<tr><td class="dim">{shortTs(r.ts)}</td><td>{r.kind}</td><td class="mono">{r.name}</td><td class="mono">{r.error}</td></tr>{/each}
        {#if !errs.recent.length}<tr><td colspan="4" class="u-empty">no errors</td></tr>{/if}
      </tbody></table>
    </div>
  {/if}

  <!-- ============ TOOLS ============ -->
  {#if tab === 'tools' && tools}
    <div class="u-card"><div class="u-ctitle">Tool usage (what the agent actually did)</div>
      <table class="u-tbl"><thead><tr><th>tool</th><th>calls</th><th>errors</th><th>err%</th><th>p50</th><th>p95</th></tr></thead><tbody>
        {#each tools.tools as r}<tr><td class="mono">{r.tool}</td><td>{compact(r.calls)}</td><td>{r.errors}</td><td class:bad={r.err_pct > 5}>{r.err_pct.toFixed(1)}%</td><td>{ms(r.p50_ms)}</td><td>{ms(r.p95_ms)}</td></tr>{/each}
        {#if !tools.tools.length}<tr><td colspan="6" class="u-empty">no tool spans</td></tr>{/if}
      </tbody></table>
    </div>
  {/if}

  <!-- ============ SECURITY ============ -->
  {#if tab === 'security' && sec}
    <div class="u-kpis">
      <div class="u-kpi" class:bad={sec.rate_limited > 0}><span class="k">RATE LIMITED</span><b>{sec.rate_limited}</b></div>
      <div class="u-kpi" class:bad={(sec.by_kind.find((x:any)=>x.kind==='leak')?.count||0)>0}><span class="k">LEAK ATTEMPTS</span><b>{sec.by_kind.find((x:any)=>x.kind==='leak')?.count||0}</b></div>
      <div class="u-kpi"><span class="k">AUTH FAILS</span><b>{sec.auth_failures.reduce((s:number,x:any)=>s+x.count,0)}</b></div>
    </div>
    <div class="u-grid2">
      <div class="u-card"><div class="u-ctitle">Events by kind</div>
        <table class="u-tbl"><thead><tr><th>kind</th><th>severity</th><th>count</th></tr></thead><tbody>
          {#each sec.by_kind as r}<tr><td>{r.kind}</td><td><span class="u-st {r.severity==='CRIT'?'error':''}">{r.severity}</span></td><td>{r.count}</td></tr>{/each}
          {#if !sec.by_kind.length}<tr><td colspan="3" class="u-empty">clean — no events</td></tr>{/if}
        </tbody></table>
      </div>
      <div class="u-card"><div class="u-ctitle">Auth failures</div>
        <table class="u-tbl"><thead><tr><th>who</th><th>attempts</th></tr></thead><tbody>
          {#each sec.auth_failures as r}<tr><td class="mono">{r.who ?? '(unknown)'}</td><td>{r.count}</td></tr>{/each}
          {#if !sec.auth_failures.length}<tr><td colspan="2" class="u-empty">none</td></tr>{/if}
        </tbody></table>
      </div>
    </div>
    <div class="u-card"><div class="u-ctitle">Recent security events</div>
      <table class="u-tbl"><thead><tr><th>time</th><th>kind</th><th>sev</th><th>account</th><th>store</th><th>detail</th></tr></thead><tbody>
        {#each sec.recent as r}<tr><td class="dim">{shortTs(r.ts)}</td><td>{r.kind}</td><td><span class="u-st {r.severity==='CRIT'?'error':''}">{r.severity}</span></td><td class="mono">{r.service_account ?? '—'}</td><td class="mono">{r.store_id ?? '—'}</td><td class="mono">{r.detail ?? ''}</td></tr>{/each}
        {#if !sec.recent.length}<tr><td colspan="6" class="u-empty">no events</td></tr>{/if}
      </tbody></table>
    </div>
  {/if}

  <!-- ============ ENTITIES ============ -->
  {#if tab === 'entities'}
    <div class="u-grid2">
      <div class="u-card"><div class="u-ctitle">Top users / keys</div>
        <table class="u-tbl"><thead><tr><th>actor</th><th>req</th><th>cost</th><th>last</th></tr></thead><tbody>
          {#each entUsers as r}<tr class="click" onclick={() => openDrawer('actor', r.key)}><td class="mono">{r.key}</td><td>{compact(r.requests)}</td><td class="num">{usd(r.cost)}</td><td class="dim">{ago(r.last)}</td></tr>{/each}
          {#if !entUsers.length}<tr><td colspan="4" class="u-empty">—</td></tr>{/if}
        </tbody></table>
      </div>
      <div class="u-card"><div class="u-ctitle">Top stores</div>
        <table class="u-tbl"><thead><tr><th>store</th><th>req</th><th>cost</th><th>last</th></tr></thead><tbody>
          {#each entStores as r}<tr class="click" onclick={() => openDrawer('store', r.key)}><td class="mono">{r.key}</td><td>{compact(r.requests)}</td><td class="num">{usd(r.cost)}</td><td class="dim">{ago(r.last)}</td></tr>{/each}
          {#if !entStores.length}<tr><td colspan="4" class="u-empty">—</td></tr>{/if}
        </tbody></table>
      </div>
    </div>
    <p class="u-hint">Click any row → full drilldown drawer.</p>
  {/if}

  <!-- ============ BILLING ============ -->
  {#if tab === 'billing' && budget}
    <div class="u-kpis">
      <div class="u-kpi"><span class="k">TODAY</span><b>{usd(budget.today_spend)}</b></div>
      <div class="u-kpi"><span class="k">MONTH-TO-DATE</span><b>{usd(budget.mtd_spend)}</b></div>
      <div class="u-kpi"><span class="k">PROJECTED MONTH</span><b>{usd(budget.projected_month)}</b></div>
      <div class="u-kpi" class:bad={budget.over_monthly}><span class="k">MONTHLY BUDGET</span><b>{budget.monthly_usd ? usd(budget.monthly_usd) : '—'}</b></div>
    </div>
    <div class="u-card"><div class="u-ctitle">Budget targets</div>
      <div class="u-frow">
        <span class="u-lbl">Daily $</span><input class="u-in" type="number" step="0.01" bind:value={bDaily} />
        <span class="u-lbl">Monthly $</span><input class="u-in" type="number" step="0.01" bind:value={bMonthly} />
        <button class="u-btn" onclick={saveBudget} disabled={bSaving}>{bSaving ? 'saving…' : 'Save'}</button>
        {#if budget.over_daily}<span class="u-st error">over daily ({(budget.daily_pct||0).toFixed(0)}%)</span>{/if}
        {#if budget.over_monthly}<span class="u-st error">over monthly ({(budget.monthly_pct||0).toFixed(0)}%)</span>{/if}
      </div>
    </div>
    <div class="u-card"><div class="u-ctitle">Invoice rollup
      <select class="u-mini" bind:value={invGroup} onchange={() => loadTab('billing', true)}><option value="store">by store</option><option value="actor">by user/key</option></select>
      <button class="u-btn ghost sm" onclick={exportInvoice}>export csv</button></div>
      <table class="u-tbl"><thead><tr><th>{invGroup}</th><th>requests</th><th>tokens</th><th>cost</th></tr></thead><tbody>
        {#each invoice?.rows ?? [] as r}<tr><td class="mono">{r.key}</td><td>{compact(r.requests)}</td><td>{compact(r.tokens)}</td><td class="num">{usd(r.cost)}</td></tr>{/each}
        {#if !(invoice?.rows ?? []).length}<tr><td colspan="4" class="u-empty">—</td></tr>{/if}
      </tbody></table>
    </div>
  {/if}

  <!-- ============ LIVE ============ -->
  {#if tab === 'live'}
    <div class="u-frow" style="margin-bottom:.75rem">
      <button class="u-btn ghost" onclick={() => loadTab('live', true)}>↻ refresh</button>
      <button class="u-chip" class:on={autoRefresh} onclick={toggleAuto}>{autoRefresh ? '● auto 5s' : '○ auto-refresh'}</button>
    </div>
    {#if live}
      <div class="u-kpis">
        <div class="u-kpi"><span class="k">ACTIVE NOW</span><b>{live.active_sessions.length}</b></div>
        <div class="u-kpi"><span class="k">RUNNING</span><b>{live.running}</b></div>
        <div class="u-kpi"><span class="k">TOKENS / MIN</span><b>{compact(live.tokens_last_min)}</b></div>
      </div>
      <div class="u-grid2">
        <div class="u-card"><div class="u-ctitle">Active sessions</div>
          <table class="u-tbl"><thead><tr><th>session</th><th>last event</th></tr></thead><tbody>
            {#each live.active_sessions as r}<tr><td class="mono">{r.session_id}</td><td class="dim">{ago(r.last)}</td></tr>{/each}
            {#if !live.active_sessions.length}<tr><td colspan="2" class="u-empty">idle</td></tr>{/if}
          </tbody></table>
        </div>
        <div class="u-card"><div class="u-ctitle">Latest calls</div>
          <table class="u-tbl"><thead><tr><th>time</th><th>src</th><th>actor</th><th>tok</th><th>cost</th></tr></thead><tbody>
            {#each live.recent as r}<tr><td class="dim">{shortTs(r.ts)}</td><td><span class="u-tag {r.src}">{srcLabel(r.src)}</span></td><td class="mono">{r.actor}</td><td>{compact(r.tokens)}</td><td class="num">{usd(r.cost)}</td></tr>{/each}
          </tbody></table>
        </div>
      </div>
    {/if}
  {/if}

  </div><!-- /u-main -->
 </div><!-- /u-shell -->

  <!-- ============ DRAWER ============ -->
  {#if drawerOpen}
    <div class="dw-backdrop" onclick={closeDrawer} role="presentation"></div>
    <aside class="dw">
      <header class="dw-head"><div><span class="dw-type">{drawerType}</span><h2 class="mono">{drawerId}</h2></div><button class="dw-x" onclick={closeDrawer}>×</button></header>
      <div class="dw-body">
        {#if drawerLoading}<div class="u-load">loading…</div>
        {:else if drawerData?.error}<div class="u-err">{drawerData.error}</div>
        {:else if drawerType === 'embed' && drawerData}
          {#if !drawerData.found}
            <div class="u-empty">no record for this session</div>
          {:else}
            {@const h = drawerData.header}
            <div class="dw-meta">
              <span class="ppl-badge svc">embed visitor</span>
              <span class="dw-q" style="max-width:240px">{h.widget}</span>
              {#if h.store}<span class="dim">· {h.store}</span>{/if}
            </div>
            <div class="u-substats" style="margin:0 0 1rem">
              <span>Origin <b>{h.origin ?? '—'}</b></span>
              <span>IP <b>{h.ip ?? '—'}</b></span>
              <span>First <b>{ago(h.first_seen)}</b></span>
              <span>Last <b>{ago(h.last_seen)}</b></span>
            </div>
            <div class="u-kpis dw-kpis">
              <div class="u-kpi"><span class="k">MESSAGES</span><b>{compact(h.messages)}</b></div>
              <div class="u-kpi"><span class="k">AVG LATENCY</span><b>{ms(h.avg_latency_ms)}</b></div>
              <div class="u-kpi"><span class="k">TOKENS</span><b>{compact(h.tokens)}</b></div>
              <div class="u-kpi"><span class="k">COST</span><b>{usd(h.cost)}</b></div>
              <div class="u-kpi" class:bad={h.errors > 0}><span class="k">ERRORS</span><b>{h.errors}</b></div>
            </div>
            <div class="u-ctitle">Conversation</div>
            {#if !drawerData.bodies_logged}
              <p class="u-hint">Message bodies not logged (set EMBED_LOG_BODIES=1). Showing turns + sizes only.</p>
            {/if}
            {#each drawerData.messages ?? [] as m}
              <div class="emc">
                <div class="emc-ts">{shortTs(m.ts)} · {ms(m.latency_ms)} {#if !m.success}<span class="u-st error">error</span>{/if}</div>
                {#if m.question}<div class="emc-q">{m.question}</div>{:else}<div class="emc-q dim">[question · {m.q_chars} chars]</div>{/if}
                {#if m.answer}<div class="emc-a">{m.answer}</div>{:else}<div class="emc-a dim">[answer · {m.a_chars} chars]</div>{/if}
              </div>
            {/each}
            {#if !(drawerData.messages ?? []).length}<div class="u-empty">no messages</div>{/if}
          {/if}
        {:else if drawerType === 'person' && drawerData}
          {#if !drawerData.found}
            <div class="u-empty">no record for this user</div>
          {:else}
            {@const h = drawerData.header}
            <div class="dw-meta">
              <span class="ppl-badge {h.is_service ? 'svc' : ''}">{h.is_service ? 'API key' : 'user'}</span>
              {#if h.email}<span class="dim">{h.email}</span>{/if}
              <span class="dim">· {h.role}</span>
              {#if !h.active}<span class="ppl-badge off">disabled</span>{/if}
            </div>
            <div class="u-kpis dw-kpis">
              <div class="u-kpi"><span class="k">QUESTIONS</span><b>{compact(h.requests)}</b></div>
              <div class="u-kpi"><span class="k">SESSIONS</span><b>{compact(h.sessions)}</b></div>
              <div class="u-kpi"><span class="k">SATISFACTION</span><b>{h.satisfaction != null ? h.satisfaction + '%' : '—'}</b></div>
              <div class="u-kpi"><span class="k">TOKENS</span><b>{compact(h.tokens)}</b></div>
              <div class="u-kpi"><span class="k">COST</span><b>{usd(h.cost)}</b></div>
              <div class="u-kpi" class:bad={h.err_pct > 5}><span class="k">ERROR %</span><b>{h.err_pct}%</b></div>
            </div>
            <div class="u-substats" style="margin:0 0 1rem">
              <span>👍 <b>{h.ups}</b></span><span>👎 <b>{h.downs}</b></span>
              <span>Last active <b>{ago(h.last_active)}</b></span>
              <span>Last login <b>{ago(h.last_login)}</b></span>
              {#if h.created}<span>Joined <b>{shortTs(h.created)}</b></span>{/if}
            </div>
            {#if (drawerData.series ?? []).length}
              {@const mx = Math.max(1, ...drawerData.series.map((x:any)=>x.requests))}
              <div class="u-ctitle">Daily questions</div>
              <div class="dw-spark">
                {#each drawerData.series as b}<div class="dw-bar" title={`${shortTs(b.bucket)} · ${b.requests} q`} style={`height:${Math.round((b.requests/mx)*100)}%`}></div>{/each}
              </div>
            {/if}
            {#if (drawerData.by_source ?? []).length}
              <div class="u-ctitle" style="margin-top:1rem">By source</div>
              <table class="u-tbl"><thead><tr><th>source</th><th>req</th><th>cost</th></tr></thead><tbody>
                {#each drawerData.by_source as r}<tr><td><span class="u-tag {r.src}">{srcLabel(r.src)}</span></td><td>{compact(r.requests)}</td><td class="num">{usd(r.cost)}</td></tr>{/each}
              </tbody></table>
            {/if}
            <div class="u-ctitle" style="margin-top:1rem">Recent sessions ({drawerData.sessions?.length ?? 0})</div>
            <table class="u-tbl"><thead><tr><th>started</th><th>first message</th></tr></thead><tbody>
              {#each drawerData.sessions ?? [] as r}<tr><td class="dim">{shortTs(r.updated)}</td><td class="dw-q">{r.first_message ?? '—'}</td></tr>{/each}
              {#if !(drawerData.sessions ?? []).length}<tr><td colspan="2" class="u-empty">no sessions in window</td></tr>{/if}
            </tbody></table>
            <div class="u-ctitle" style="margin-top:1rem">Rated questions ({drawerData.questions?.length ?? 0})</div>
            <table class="u-tbl"><thead><tr><th>when</th><th>question</th><th>👍/👎</th></tr></thead><tbody>
              {#each drawerData.questions ?? [] as r}<tr><td class="dim">{shortTs(r.ts)}</td><td class="dw-q">{r.question}</td><td>{#if r.rating === 'up'}<span class="sat good">👍</span>{:else}<span class="sat bad">👎</span>{/if}</td></tr>{/each}
              {#if !(drawerData.questions ?? []).length}<tr><td colspan="3" class="u-empty">no rated questions (only thumbs-rated answers show here)</td></tr>{/if}
            </tbody></table>
          {/if}
        {:else if drawerData}
          {@const t = drawerData.totals}
          <div class="u-kpis dw-kpis">
            <div class="u-kpi"><span class="k">SPEND</span><b>{usd(t.spend)}</b></div>
            <div class="u-kpi"><span class="k">REQUESTS</span><b>{compact(t.requests)}</b></div>
            <div class="u-kpi"><span class="k">TOKENS</span><b>{compact(t.tokens)}</b></div>
            <div class="u-kpi" class:bad={t.errors>0}><span class="k">ERRORS</span><b>{t.errors}</b></div>
          </div>
          <div class="u-ctitle">By model</div>
          <table class="u-tbl"><thead><tr><th>model</th><th>req</th><th>cost</th></tr></thead><tbody>
            {#each drawerData.by_model as r}<tr><td class="mono">{r.model}</td><td>{compact(r.requests)}</td><td class="num">{usd(r.cost)}</td></tr>{/each}
          </tbody></table>
          <div class="u-ctitle" style="margin-top:1rem">Recent activity</div>
          <table class="u-tbl"><thead><tr><th>time</th><th>src</th><th>model</th><th>tok</th><th>cost</th><th>lat</th></tr></thead><tbody>
            {#each drawerData.activity as a}<tr><td class="dim">{shortTs(a.ts)}</td><td><span class="u-tag {a.src}">{srcLabel(a.src)}</span></td><td class="mono dim">{a.model}</td><td>{compact(a.tokens)}</td><td class="num">{usd(a.cost)}</td><td class="dim">{ms(a.latency_ms)}</td></tr>{/each}
          </tbody></table>
        {/if}
      </div>
    </aside>
  {/if}
</div>

<style>
  /* pass the page height down to .u-shell so rail + main scroll independently */
  .usage { color: #2c2620; font-size: 13px; height: 100%; min-height: 0; display: flex; flex-direction: column; }
  .usage.embedded { display: block; height: auto; }
  /* shell = rail + main, admin-Overview layout */
  /* Admin Clean — matches command-center .cc-rail exactly (flush, flat bg, white-card active) */
  /* Independent scroll: shell fills the layout <main>; rail + main-pane each scroll on their own */
  .u-shell { display: flex; align-items: stretch; gap: 0; flex: 1; min-height: 0; overflow: hidden; }
  .u-rail { flex: none; width: 220px; padding: 0 8px 40px; border-right: 1px solid var(--pw-border, #e8e6dd); background: var(--pw-bg-alt, #f7f6f3); align-self: stretch; overflow-y: auto; overscroll-behavior: contain; }
  .u-rail::-webkit-scrollbar { width: 6px; }
  .u-rail::-webkit-scrollbar-thumb { background: var(--pw-border, #e8e6dd); border-radius: 3px; }
  .u-rail::-webkit-scrollbar-track { background: transparent; }
  .u-railgrp { font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--pw-muted, #6f6e69); padding: 12px 14px 6px; }
  .u-railitem { position: relative; width: 100%; display: flex; align-items: center; gap: 10px; border: none; background: transparent; cursor: pointer; padding: 8px 12px; border-radius: 8px; font-size: 12px; line-height: 1.3; color: var(--pw-ink, #2c2c2c); text-align: left; transition: background .15s ease, color .15s ease, transform .12s ease; }
  .u-railitem:hover { background: rgba(201,99,66,.06); color: var(--pw-ink, #2c2c2c); }
  .u-railitem:active { transform: translateY(.5px); }
  .u-railitem.on { background: #fff; color: var(--pw-accent, #c96342); font-weight: 600; box-shadow: 0 1px 3px rgba(201,99,66,.08), 0 0 0 1px rgba(201,99,66,.14); }
  .u-railitem.on::before { content: ''; position: absolute; left: 0; top: 50%; transform: translateY(-50%); width: 3px; height: 60%; border-radius: 3px; background: linear-gradient(180deg, #c96342, var(--pw-accent, #c96342)); }
  .u-ricon { width: 14px; height: 14px; flex: 0 0 auto; color: var(--pw-muted, #6f6e69); transition: color .15s ease; }
  .u-railitem.on .u-ricon { color: var(--pw-accent, #c96342); }
  .u-rlbl { flex: 1; }
  .u-rdot { width: 7px; height: 7px; border-radius: 50%; background: #2f6b3a; flex: none; box-shadow: 0 0 0 3px rgba(47,107,58,.15); animation: u-pulse 2s ease-in-out infinite; }
  @keyframes u-pulse { 0%,100% { box-shadow: 0 0 0 3px rgba(47,107,58,.15); } 50% { box-shadow: 0 0 0 5px rgba(47,107,58,.06); } }
  .u-main { flex: 1; min-width: 0; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 1.1rem 1.25rem; }
  .u-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 1rem; }
  .u-head h1 { margin: 0; font-size: 1.35rem; }
  .u-sub { margin: .2rem 0 0; font-size: 12px; color: #9a8f80; }
  .u-livebadge { font-size: 11px; color: #2f6b3a; white-space: nowrap; padding-top: .35rem; }
  /* KPI tiles (admin Overview big-number style) */
  .u-tiles { display: grid; grid-template-columns: repeat(5, 1fr); gap: .75rem; margin-bottom: 1.1rem; }
  .u-tile { background: #fff; border: 1px solid #ece2d4; border-radius: 12px; padding: 1rem 1.1rem; display: flex; flex-direction: column; gap: .3rem; }
  .u-tile b { font-size: 1.85rem; font-weight: 700; letter-spacing: -.02em; line-height: 1; }
  .u-tile b small { font-size: .9rem; font-weight: 600; color: #9a8f80; }
  .u-tile span { font-size: 10px; letter-spacing: .06em; text-transform: uppercase; color: #9a8f80; }
  .u-tile.bad b { color: #b3261e; }
  @media (max-width: 900px) { .u-rail { width: 56px; } .u-railgrp, .u-rlbl { display: none; } .u-railitem { justify-content: center; } .u-tiles { grid-template-columns: repeat(2, 1fr); } }
  .u-filters { display: flex; flex-direction: column; gap: .5rem; margin-bottom: 1rem; }
  .u-frow { display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; }
  .u-lbl { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #9a8f80; }
  .u-chip { border: 1px solid #e0d6c8; background: #fff; color: #6b6052; padding: .25rem .6rem; border-radius: 999px; cursor: pointer; font-size: 12px; }
  .u-chip.on { background: #9a4a2f; border-color: #9a4a2f; color: #fff; }
  .u-chip.src.on { background: #f3ece1; color: #9a4a2f; border-color: #d8b69a; }
  .u-date, .u-in, .u-mini { border: 1px solid #e0d6c8; border-radius: 6px; padding: .25rem .45rem; font-size: 12px; background: #fff; color: #2c2620; }
  .u-in { min-width: 90px; } .u-arrow { color: #9a8f80; }
  .u-btn { border: 1px solid #9a4a2f; background: #9a4a2f; color: #fff; border-radius: 6px; padding: .25rem .7rem; cursor: pointer; font-size: 12px; }
  .u-btn.ghost { background: #fff; color: #9a4a2f; } .u-btn.sm { padding: .1rem .5rem; font-size: 11px; float: right; }
  .u-btn:disabled { opacity: .5; }
  .u-cards3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-bottom: .75rem; }
  .oc { background: #fff; border: 1px solid #ece2d4; border-radius: 12px; padding: 1rem 1.1rem; display: flex; flex-direction: column; }
  .oc-head { font-size: 13px; font-weight: 600; color: #6b6052; display: flex; justify-content: space-between; align-items: center; }
  .oc-delta { font-size: 11px; font-weight: 600; color: #b3261e; } .oc-delta.up { color: #2f6b3a; } .oc-delta.flat { color: #9a8f80; }
  .oc-total { font-size: 1.9rem; font-weight: 700; margin: .15rem 0 .6rem; letter-spacing: -.02em; }
  .oc-bars { display: flex; align-items: flex-end; gap: 3px; height: 150px; }
  .oc-col { flex: 1; min-width: 2px; height: 100%; display: flex; align-items: flex-end; }
  .oc-stack { width: 100%; display: flex; flex-direction: column-reverse; border-radius: 2px 2px 0 0; overflow: hidden; min-height: 2px; }
  .oc-seg { width: 100%; min-height: 1px; }
  .oc-legend { margin-top: .75rem; display: flex; flex-direction: column; gap: .35rem; }
  .oc-leg { display: flex; align-items: center; gap: .5rem; font-size: 12px; }
  .oc-leg .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .oc-leg .lbl { color: #2c2620; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .oc-leg .val { margin-left: auto; color: #6b6052; font-variant-numeric: tabular-nums; }
  .u-substats { display: flex; gap: 1.5rem; font-size: 12px; color: #9a8f80; margin-bottom: 1rem; padding: 0 .2rem; flex-wrap: wrap; }
  .u-substats b { color: #2c2620; } .u-substats b.bad { color: #b3261e; }
  .u-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: .75rem; margin-bottom: 1rem; }
  .u-kpi { background: #faf6f0; border: 1px solid #ece2d4; border-radius: 10px; padding: .75rem .9rem; display: flex; flex-direction: column; gap: .25rem; }
  .u-kpi .k { font-size: 10px; letter-spacing: .06em; color: #9a8f80; text-transform: uppercase; }
  .u-kpi b { font-size: 1.4rem; font-weight: 700; } .u-kpi.bad b { color: #b3261e; }
  .u-card { background: #fff; border: 1px solid #ece2d4; border-radius: 10px; padding: .75rem .9rem; margin-bottom: 1rem; }
  .u-ctitle { font-size: 12px; font-weight: 600; color: #6b6052; margin-bottom: .5rem; text-transform: uppercase; letter-spacing: .03em; }
  .u-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  /* Users & activity: 2 tables stacked top/bottom, each scrolls internally */
  .u-stack2 { display: flex; flex-direction: column; gap: 1rem; }
  .u-scroll { max-height: 400px; overflow-y: auto; overscroll-behavior: contain; border: 1px solid #f0e9de; border-radius: 8px; }
  .u-scroll .u-tbl thead th { position: sticky; top: 0; z-index: 1; background: #fff; box-shadow: 0 1px 0 #ece2d4; }
  /* Admin-Overview section panels (◷ TITLE + live badge) */
  .u-sec { border: 1px solid #ece2d4; border-radius: 10px; padding: 1rem 1.1rem; background: #fff; margin-bottom: 1rem; }
  .u-sech { display: flex; align-items: center; justify-content: space-between; margin-bottom: .9rem; }
  .u-sect { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; font-weight: 700; color: #9a4a2f; }
  .u-sect::before { content: '◷'; margin-right: .4rem; opacity: .8; }
  .u-secbadge { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: #9a8f80; font-family: ui-monospace, Menlo, monospace; }
  .u-sec .u-cards3, .u-sec .u-substats { margin-bottom: 0; }
  .u-sec .u-substats { margin-top: .75rem; }
  .u-sec > .u-card:last-child, .u-sec .u-grid2 .u-card, .u-sec .u-stack2 .u-card { margin-bottom: 0; }
  .u-tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
  .u-tbl th { text-align: left; font-weight: 600; color: #9a8f80; font-size: 10px; text-transform: uppercase; letter-spacing: .04em; padding: .3rem .4rem; border-bottom: 1px solid #ece2d4; }
  .u-tbl td { padding: .3rem .4rem; border-bottom: 1px solid #f5efe7; }
  .u-tbl td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .u-tbl td.dim { color: #9a8f80; } .u-tbl td.bad, .u-tbl td:has(+ td) .bad { color: #b3261e; }
  .u-tbl tr.click { cursor: pointer; } .u-tbl tr.click:hover { background: #faf6f0; }
  .mono { font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; }
  .u-empty { color: #b8ad9c; text-align: center; padding: .6rem; }
  .u-hint { font-size: 11px; color: #9a8f80; }
  .u-tag { font-size: 10.5px; padding: .1rem .4rem; border-radius: 4px; background: #f3ece1; color: #6b6052; }
  .u-tag.platform { background: #e7f0e7; color: #2f6b3a; } .u-tag.api_key { background: #f3ece1; color: #9a4a2f; }
  .u-tag.training { background: #ece7f5; color: #5a3a9a; } .u-tag.embedding { background: #e7eef5; color: #2f5a9a; }
  .u-tag.embed { background: #f5efe7; color: #8a7a5a; }
  .u-st { font-size: 10.5px; padding: .05rem .35rem; border-radius: 4px; color: #2f6b3a; }
  .u-st.error { background: #fbe9e7; color: #b3261e; }
  .u-err { background: #fbe9e7; color: #b3261e; padding: .5rem .75rem; border-radius: 8px; margin-bottom: .75rem; }
  .u-load { color: #9a8f80; padding: 1rem; }
  /* heatmap */
  .heat { display: flex; flex-direction: column; gap: 2px; overflow-x: auto; }
  .heat-row { display: flex; gap: 2px; align-items: center; }
  .heat-lbl { width: 30px; font-size: 10px; color: #9a8f80; flex: none; }
  .heat-h { flex: 1; min-width: 12px; font-size: 9px; color: #b8ad9c; text-align: center; }
  .heat-c { flex: 1; min-width: 12px; height: 16px; border-radius: 2px; }
  /* drawer */
  .dw-backdrop { position: fixed; inset: 0; background: rgba(31,28,23,.4); z-index: 9998; }
  .dw { position: fixed; top: 0; right: 0; width: 540px; max-width: 92vw; height: 100vh; background: #fff; border-left: 1px solid #ece2d4; z-index: 9999; box-shadow: -8px 0 28px rgba(0,0,0,.14); overflow-y: auto; }
  .dw-head { display: flex; justify-content: space-between; align-items: flex-start; padding: 1rem 1.25rem; border-bottom: 1px solid #ece2d4; position: sticky; top: 0; background: #fff; }
  .dw-type { font-size: 10px; text-transform: uppercase; letter-spacing: .05em; color: #9a8f80; }
  .dw-head h2 { margin: .15rem 0 0; font-size: 1rem; }
  .dw-x { border: none; background: none; font-size: 1.5rem; cursor: pointer; color: #9a8f80; line-height: 1; }
  .dw-body { padding: 1.25rem; } .dw-kpis { grid-template-columns: repeat(3, 1fr); }
  /* people leaderboard */
  .u-tbl th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
  .u-tbl th.sortable:hover { color: #9a4a2f; }
  .u-tbl th.numh { text-align: right; }
  .sarrow { margin-left: 2px; color: #9a4a2f; }
  .u-ppl td { vertical-align: top; }
  .ppl-name { font-weight: 600; color: #2c2620; }
  .ppl-email { font-size: 10.5px; color: #b8ad9c; margin-top: 1px; }
  .ppl-badge { font-size: 9px; text-transform: uppercase; letter-spacing: .04em; padding: .05rem .3rem; border-radius: 4px; background: #f3ece1; color: #8a7a5a; margin-left: .35rem; vertical-align: middle; }
  .ppl-badge.svc { background: #e7eef5; color: #2f5a9a; }
  .ppl-badge.off { background: #fbe9e7; color: #b3261e; }
  .sat { font-weight: 600; color: #6b6052; } .sat.good { color: #2f6b3a; } .sat.bad { color: #b3261e; }
  /* person drawer extras */
  .dw-meta { display: flex; align-items: center; gap: .5rem; font-size: 12px; margin-bottom: 1rem; flex-wrap: wrap; }
  .dw-meta .ppl-badge { margin-left: 0; }
  .dw-spark { display: flex; align-items: flex-end; gap: 2px; height: 70px; padding: .25rem 0; }
  .dw-bar { flex: 1; min-width: 2px; background: linear-gradient(180deg, #c96342, #9a4a2f); border-radius: 2px 2px 0 0; min-height: 2px; opacity: .85; }
  .dw-q { font-size: 11.5px; color: #2c2620; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* segment toggle (App users / Embed users) */
  .u-seg { display: inline-flex; border: 1px solid #e0d6c8; border-radius: 8px; overflow: hidden; background: #fff; }
  .u-segbtn { border: none; background: #fff; color: #6b6052; padding: .4rem .9rem; cursor: pointer; font-size: 12px; font-weight: 600; border-right: 1px solid #e0d6c8; }
  .u-segbtn:last-child { border-right: none; }
  .u-segbtn.on { background: #9a4a2f; color: #fff; }
  .u-seg.sm .u-segbtn { padding: .25rem .65rem; font-size: 11px; }
  /* embed conversation turns */
  .emc { border: 1px solid #ece2d4; border-radius: 8px; padding: .6rem .7rem; margin-bottom: .6rem; background: #faf6f0; }
  .emc-ts { font-size: 10px; color: #9a8f80; margin-bottom: .35rem; }
  .emc-q { font-size: 12px; color: #2c2620; font-weight: 600; margin-bottom: .3rem; white-space: pre-wrap; }
  .emc-a { font-size: 11.5px; color: #6b6052; white-space: pre-wrap; }
  .emc-q.dim, .emc-a.dim { font-weight: 400; color: #b8ad9c; font-style: italic; }
  /* ── analytics expansion (models / tokens / embeddings / learning) ── */
  .ua { display: flex; flex-direction: column; gap: 1rem; }
  .ua-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  @media (max-width: 820px) { .ua-grid2 { grid-template-columns: 1fr; } }
  .ua-card { background: #fff; border: 1px solid #ece2d4; border-radius: 10px; padding: .85rem 1rem; }
  .ua-h { font-size: 13px; font-weight: 700; color: #2c2620; margin-bottom: .6rem; }
  .ua-sub { font-size: 11px; font-weight: 500; color: #9a8f80; margin-left: .35rem; }
  .ua-note { font-size: 11px; color: #9a8f80; font-style: italic; }
  .ua-warn { font-size: 11.5px; color: #8a5a00; background: #fdf3e0; border: 1px solid #f0dcb8; border-radius: 8px; padding: .5rem .7rem; margin-bottom: .6rem; }
  .ua-warn code { background: #f3ece1; padding: 1px 5px; border-radius: 4px; color: #9a4a2f; }
  .ua-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: .8rem; }
  .ua-kpi { background: #faf6f0; border: 1px solid #ece2d4; border-radius: 10px; padding: .75rem .9rem; }
  .ua-kn { font-size: 1.5rem; font-weight: 800; color: #2c2620; line-height: 1; }
  .ua-kl { font-size: 10.5px; letter-spacing: .04em; color: #9a8f80; text-transform: uppercase; margin-top: .3rem; }
  .ua-kpi-accent { border-color: #e3b8a8; background: #fbeee8; } .ua-kpi-accent .ua-kn { color: #c96342; }
  .ua-kpi-good { border-color: #bfe0c8; background: #eef8f0; } .ua-kpi-good .ua-kn { color: #2d8a4e; }
  .ua-kpi-bad { border-color: #ecc4bd; background: #fdeeeb; } .ua-kpi-bad .ua-kn { color: #c0392b; }
  .ua-tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
  .ua-tbl th { text-align: left; font-weight: 600; color: #9a8f80; font-size: 10px; text-transform: uppercase; letter-spacing: .04em; padding: .3rem .45rem; border-bottom: 1px solid #ece2d4; }
  .ua-tbl th.r, .ua-tbl td.r { text-align: right; font-variant-numeric: tabular-nums; }
  .ua-tbl td { padding: .35rem .45rem; border-bottom: 1px solid #f5efe7; vertical-align: top; }
  .ua-tbl .mono { font-family: ui-monospace, monospace; font-size: 11px; }
  .ua-tbl .nowrap { white-space: nowrap; color: #9a8f80; font-size: 11px; }
  .ua-text { max-width: 360px; color: #4a4036; }
  .ua-lo { color: #c0392b; font-weight: 700; }
  .barcell { width: 90px; } .barcell .bar { display: block; height: 8px; background: #c96342; border-radius: 4px; }
  .ua-trend { display: flex; flex-direction: column; gap: .15rem; }
  .ua-trow { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: .6rem; padding: .35rem .4rem; border-bottom: 1px solid #f5efe7; }
  .ua-tmodel { font-family: ui-monospace, monospace; font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ua-tcost { font-variant-numeric: tabular-nums; font-size: 12px; color: #4a4036; }
  .ua-tnew { font-size: 11px; font-weight: 700; color: #2d8a4e; } .ua-tup { font-size: 11px; font-weight: 700; color: #2d8a4e; }
  .ua-tdown { font-size: 11px; font-weight: 700; color: #c0392b; } .ua-tflat { color: #b8ab98; }
  .ua-stack { display: flex; height: 22px; border-radius: 6px; overflow: hidden; background: #f0e9df; }
  .ua-stack .seg { height: 100%; }
  .seg.s-prompt { background: #4f86d6; } .seg.s-compl { background: #7c3aed; } .seg.s-reason { background: #e05a4a; }
  .seg.s-cached { background: #d4930e; } .seg.s-uncached { background: #b8ab98; }
  .ua-legend { display: flex; gap: 1rem; margin-top: .5rem; font-size: 11px; color: #6b6052; }
  .ua-legend i.dot { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }
  i.dot.s-prompt { background: #4f86d6; } i.dot.s-compl { background: #7c3aed; } i.dot.s-reason { background: #e05a4a; }
  i.dot.s-cached { background: #d4930e; } i.dot.s-uncached { background: #b8ab98; }
  .ua-disliked { display: flex; flex-direction: column; gap: .55rem; max-height: 420px; overflow-y: auto; }
  .ua-dcard { border: 1px solid #ecc4bd; background: #fdf6f4; border-radius: 8px; padding: .55rem .7rem; }
  .ua-dq { font-size: 12.5px; font-weight: 700; color: #2c2620; }
  .ua-da { font-size: 11.5px; color: #4a4036; margin-top: .25rem; max-height: 60px; overflow: hidden; }
  .ua-dsql { display: block; font-size: 10.5px; font-family: ui-monospace, monospace; background: #f3ece1; color: #9a4a2f; padding: .3rem .45rem; border-radius: 5px; margin-top: .35rem; white-space: pre-wrap; word-break: break-word; }
  .ua-dmeta { font-size: 10px; color: #9a8f80; margin-top: .3rem; }
</style>
