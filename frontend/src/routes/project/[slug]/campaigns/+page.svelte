<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy, tick } from 'svelte';
 import { page } from '$app/stores';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';
 import { confirmDelete } from '$lib/confirmDelete';

 /* ─── auth ─── */
 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 let slug = $state('');
 let token = $state<string | null>(null);

 /* ─── data ─── */
 type Campaign = {
 id: number;
 project_slug: string;
 name: string;
 description?: string;
 type: string;
 status: string;
 target_segment?: string | null;
 target_filter?: any;
 audience_size: number;
 offer?: any;
 starts_at?: string | null;
 ends_at?: string | null;
 cost_budget?: number | null;
 created_by?: string;
 created_at: string;
 updated_at?: string;
 events?: any[];
 metrics?: any[];
 metadata?: any;
 };

 let campaigns = $state<Campaign[]>([]);
 let summary = $state<any>({});
 let loading = $state(false);
 let errorMsg = $state('');

 /* ─── filters ─── */
 // 'auto' is a synthetic pill — filters by type='auto' instead of status.
 const STATUS_TABS = ['all', 'auto', 'draft', 'scheduled', 'active', 'paused', 'completed', 'cancelled'] as const;
 let activeStatus = $state<typeof STATUS_TABS[number]>('all');
 let searchTerm = $state('');

 /* ─── selection ─── */
 let selected = $state<Set<number>>(new Set());

 /* ─── drawer ─── */
 let drawerOpen = $state(false);
 let drawerCampaign = $state<Campaign | null>(null);
 let drawerLoading = $state(false);
 let drawerTab = $state<'overview' | 'metrics' | 'events' | 'audience' | 'roi'>('overview');
 let editingName = $state(false);
 let editingDesc = $state(false);
 let nameDraft = $state('');
 let descDraft = $state('');
 let audiencePreview = $state<any>(null);
 let roiData = $state<any>(null);
 let metricsChart: any = null;
 let metricsContainer: HTMLDivElement | undefined = $state();

 /* ─── new campaign modal ─── */
 let showNewModal = $state(false);
 let segOptions = $state<{ name: string; size: number }[]>([]);
 let nf_name = $state('');
 let nf_desc = $state('');
 let nf_type = $state('manual');
 let nf_segment = $state('');
 let nf_offer = $state('{\n "discount_pct": 15,\n "free_shipping": true\n}');
 let nf_starts = $state('');
 let nf_ends = $state('');
 let nf_budget = $state('');
 let nf_busy = $state(false);
 let nf_err = $state('');

 /* ─── metric quick add ─── */
 let mq_name = $state('revenue');
 let mq_value = $state('');
 let mq_busy = $state(false);

 /* ─── refresh tracker ─── */
 let lastFetched = $state<Date | null>(null);
 let nowTick = $state(Date.now());
 let tickTimer: any = null;

 /* ─── flash ─── */
 let copyFlash = $state('');

 /* ─── derived ─── */
 const filteredCampaigns = $derived.by(() => {
 let arr = [...campaigns];
 if (activeStatus === 'auto') {
 arr = arr.filter((c) => c.type === 'auto');
 } else if (activeStatus !== 'all') {
 arr = arr.filter((c) => c.status === activeStatus);
 }
 if (searchTerm.trim()) {
 const q = searchTerm.toLowerCase();
 arr = arr.filter((c) =>
 (c.name || '').toLowerCase().includes(q) ||
 (c.target_segment || '').toLowerCase().includes(q) ||
 (c.description || '').toLowerCase().includes(q)
 );
 }
 return arr;
 });

 const lastFetchedLabel = $derived.by(() => {
 if (!lastFetched) return '';
 const diff = Math.max(0, Math.floor((nowTick - lastFetched.getTime()) / 1000));
 if (diff < 60) return `last ${diff}s ago`;
 if (diff < 3600) return `last ${Math.floor(diff / 60)}m ago`;
 return `last ${Math.floor(diff / 3600)}h ago`;
 });

 const allOnPageSelected = $derived(
 filteredCampaigns.length > 0 && filteredCampaigns.every((c) => selected.has(c.id))
 );

 /* ─── helpers ─── */
 async function fetchJSON(url: string, opts: any = {}): Promise<any> {
 const res = await fetch(url, { ...opts, headers: { ..._h(), 'Content-Type': 'application/json', ...(opts.headers || {}) } });
 if (!res.ok) {
 let msg = `HTTP ${res.status}`;
 try { const d = await res.json(); msg = d.detail || d.message || msg; } catch {}
 throw new Error(msg);
 }
 return res.json();
 }

 function fmtDate(d?: string | null): string {
 if (!d) return '—';
 try {
 const dt = new Date(d);
 if (isNaN(dt.getTime())) return d;
 return dt.toLocaleDateString() + ' ' + dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
 } catch { return d; }
 }

 function fmtMoney(v?: number | null): string {
 if (v === null || v === undefined) return '—';
 if (v >= 1000000) return '$' + (v / 1000000).toFixed(1) + 'M';
 if (v >= 1000) return '$' + (v / 1000).toFixed(1) + 'k';
 return '$' + v.toFixed(0);
 }

 function fmtNum(v?: number | null): string {
 if (v === null || v === undefined) return '—';
 return v.toLocaleString();
 }

 function statusColor(s: string): string {
 return ({
 draft: '#888',
 scheduled: '#3b82f6',
 active: '#10b981',
 paused: '#f59e0b',
 completed: '#6366f1',
 cancelled: '#ef4444',
 } as any)[s] || '#444';
 }

 function copyText(s: string) {
 try { navigator.clipboard.writeText(s); copyFlash = s; setTimeout(() => copyFlash = '', 1200); } catch {}
 }

 /* ─── auto-campaign daemon ─── */
 let autoToast = $state('');
 let autoBusy = $state(false);

 async function runAutoCampaign() {
 if (autoBusy) return;
 autoBusy = true;
 autoToast = 'Running auto-campaign…';
 try {
 const r = await fetchJSON(`/api/projects/${slug}/auto-campaign/run-now`, { method: 'POST' });
 const fired = (r.rules_fired || []).map((x: any) => x.rule).join(', ') || 'none';
 const cd = (r.cooldown_skipped || []).join(', ');
 autoToast = ` ${r.drafts_created || 0} draft(s). Rules fired: ${fired}.` + (cd ? ` Cooldown: ${cd}.` : '');
 await loadAll();
 } catch (e: any) {
 autoToast = ` ${e.message || 'auto-campaign failed'}`;
 } finally {
 autoBusy = false;
 setTimeout(() => { autoToast = ''; }, 6000);
 }
 }

 async function approveAndLaunch(c: Campaign) {
 try {
 await fetchJSON(`/api/projects/${slug}/campaigns/${c.id}`, {
 method: 'PATCH', body: JSON.stringify({ status: 'active' }),
 headers: { 'content-type': 'application/json' },
 });
 await fetchJSON(`/api/projects/${slug}/campaigns/${c.id}/launch`, { method: 'POST' });
 await loadAll();
 // refresh drawer if open
 if (drawerCampaign && drawerCampaign.id === c.id) {
 const detail = await fetchJSON(`/api/projects/${slug}/campaigns/${c.id}`);
 drawerCampaign = detail;
 }
 autoToast = ` Approved & launched: ${c.name}`;
 setTimeout(() => { autoToast = ''; }, 4000);
 } catch (e: any) {
 autoToast = ` ${e.message || 'approve failed'}`;
 setTimeout(() => { autoToast = ''; }, 5000);
 }
 }

 async function dismissAuto(c: Campaign) {
 if (!(await confirmDelete({ itemName: c.name, itemType: 'auto-draft campaign', title: 'Dismiss confirmation' }))) return;
 try {
 await fetchJSON(`/api/projects/${slug}/campaigns/${c.id}`, { method: 'DELETE' });
 drawerOpen = false;
 drawerCampaign = null;
 await loadAll();
 } catch (e: any) {
 autoToast = ` ${e.message || 'dismiss failed'}`;
 setTimeout(() => { autoToast = ''; }, 5000);
 }
 }

 /* ─── data loaders ─── */
 async function loadAll() {
 loading = true;
 errorMsg = '';
 try {
 const [list, sum] = await Promise.all([
 fetchJSON(`/api/projects/${slug}/campaigns?limit=200`),
 fetchJSON(`/api/projects/${slug}/campaigns/summary`),
 ]);
 campaigns = list.campaigns || [];
 summary = sum || {};
 lastFetched = new Date();
 } catch (e: any) {
 errorMsg = e.message || 'load failed';
 } finally {
 loading = false;
 }
 }

 async function loadSegments() {
 // We piggyback on the backend's audience compute by listing well-known segments.
 segOptions = [
 { name: 'Champions', size: 0 },
 { name: 'Loyal Customers', size: 0 },
 { name: 'Potential Loyalists', size: 0 },
 { name: 'New Customers', size: 0 },
 { name: 'Promising', size: 0 },
 { name: 'Need Attention', size: 0 },
 { name: 'About To Sleep', size: 0 },
 { name: 'At Risk', size: 0 },
 { name: 'Cannot Lose Them', size: 0 },
 { name: 'Hibernating', size: 0 },
 { name: 'Lost', size: 0 },
 ];
 }

 /* ─── drawer actions ─── */
 async function openDrawer(c: Campaign) {
 drawerCampaign = c;
 drawerOpen = true;
 drawerTab = 'overview';
 drawerLoading = true;
 nameDraft = c.name;
 descDraft = c.description || '';
 editingName = false;
 editingDesc = false;
 audiencePreview = null;
 roiData = null;
 try {
 const detail = await fetchJSON(`/api/projects/${slug}/campaigns/${c.id}`);
 drawerCampaign = detail;
 } catch (e: any) {
 errorMsg = e.message;
 } finally {
 drawerLoading = false;
 }
 }

 function closeDrawer() {
 drawerOpen = false;
 drawerCampaign = null;
 if (metricsChart) {
 try { metricsChart.dispose(); } catch {}
 metricsChart = null;
 }
 }

 async function saveNameDesc() {
 if (!drawerCampaign) return;
 try {
 const body: any = {};
 if (nameDraft !== drawerCampaign.name) body.name = nameDraft;
 if (descDraft !== (drawerCampaign.description || '')) body.description = descDraft;
 if (Object.keys(body).length === 0) { editingName = false; editingDesc = false; return; }
 const updated = await fetchJSON(`/api/projects/${slug}/campaigns/${drawerCampaign.id}`, {
 method: 'PATCH', body: JSON.stringify(body),
 });
 drawerCampaign = { ...drawerCampaign, ...updated };
 const idx = campaigns.findIndex((c) => c.id === drawerCampaign!.id);
 if (idx >= 0) campaigns[idx] = { ...campaigns[idx], ...updated };
 editingName = false;
 editingDesc = false;
 } catch (e: any) {
 errorMsg = e.message;
 }
 }

 async function transition(action: 'launch' | 'pause' | 'resume' | 'complete') {
 if (!drawerCampaign) return;
 try {
 const updated = await fetchJSON(`/api/projects/${slug}/campaigns/${drawerCampaign.id}/${action}`, { method: 'POST' });
 drawerCampaign = { ...drawerCampaign, ...updated };
 await loadAll();
 } catch (e: any) {
 errorMsg = e.message;
 }
 }

 async function deleteCampaign() {
 if (!drawerCampaign) return;
 if (!confirm(`Cancel campaign "${drawerCampaign.name}"?`)) return;
 try {
 await fetchJSON(`/api/projects/${slug}/campaigns/${drawerCampaign.id}`, { method: 'DELETE' });
 closeDrawer();
 await loadAll();
 } catch (e: any) {
 errorMsg = e.message;
 }
 }

 async function fetchAudience() {
 if (!drawerCampaign) return;
 try {
 audiencePreview = await fetchJSON(`/api/projects/${slug}/campaigns/${drawerCampaign.id}/audience`);
 } catch (e: any) {
 errorMsg = e.message;
 }
 }

 async function fetchROI() {
 if (!drawerCampaign) return;
 try {
 roiData = await fetchJSON(`/api/projects/${slug}/campaigns/${drawerCampaign.id}/roi`);
 } catch (e: any) {
 errorMsg = e.message;
 }
 }

 async function recordMetric() {
 if (!drawerCampaign || !mq_value) return;
 mq_busy = true;
 try {
 await fetchJSON(`/api/projects/${slug}/campaigns/${drawerCampaign.id}/metric`, {
 method: 'POST',
 body: JSON.stringify({ metric_name: mq_name, value: parseFloat(mq_value) }),
 });
 mq_value = '';
 const detail = await fetchJSON(`/api/projects/${slug}/campaigns/${drawerCampaign.id}`);
 drawerCampaign = detail;
 await renderMetricsChart();
 } catch (e: any) {
 errorMsg = e.message;
 } finally {
 mq_busy = false;
 }
 }

 /* ─── new campaign ─── */
 async function createCampaign() {
 nf_busy = true;
 nf_err = '';
 try {
 let offerObj: any = {};
 if (nf_offer.trim()) {
 try { offerObj = JSON.parse(nf_offer); }
 catch { nf_err = 'Offer must be valid JSON'; nf_busy = false; return; }
 }
 const body: any = {
 name: nf_name.trim(),
 description: nf_desc.trim(),
 type: nf_type,
 offer: offerObj,
 };
 if (nf_segment) body.target_segment = nf_segment;
 if (nf_starts) body.starts_at = nf_starts;
 if (nf_ends) body.ends_at = nf_ends;
 if (nf_budget) body.cost_budget = parseFloat(nf_budget);
 if (!body.name) { nf_err = 'Name required'; nf_busy = false; return; }
 await fetchJSON(`/api/projects/${slug}/campaigns`, { method: 'POST', body: JSON.stringify(body) });
 showNewModal = false;
 nf_name = ''; nf_desc = ''; nf_type = 'manual'; nf_segment = '';
 nf_offer = '{\n "discount_pct": 15,\n "free_shipping": true\n}';
 nf_starts = ''; nf_ends = ''; nf_budget = '';
 await loadAll();
 } catch (e: any) {
 nf_err = e.message || 'create failed';
 } finally {
 nf_busy = false;
 }
 }

 /* ─── bulk actions ─── */
 function toggleAll() {
 if (allOnPageSelected) {
 filteredCampaigns.forEach((c) => selected.delete(c.id));
 } else {
 filteredCampaigns.forEach((c) => selected.add(c.id));
 }
 selected = new Set(selected);
 }

 function toggleOne(id: number) {
 if (selected.has(id)) selected.delete(id);
 else selected.add(id);
 selected = new Set(selected);
 }

 async function bulkAction(action: 'launch' | 'pause') {
 const ids = Array.from(selected);
 if (ids.length === 0) return;
 if (!confirm(`${action.toUpperCase()} ${ids.length} campaign(s)?`)) return;
 try {
 await Promise.all(ids.map((id) => fetchJSON(`/api/projects/${slug}/campaigns/${id}/${action}`, { method: 'POST' })));
 selected = new Set();
 await loadAll();
 } catch (e: any) {
 errorMsg = e.message;
 }
 }

 /* ─── CSV export ─── */
 function exportCSV() {
 const header = ['id', 'name', 'status', 'type', 'target_segment', 'audience_size', 'starts_at', 'ends_at', 'cost_budget', 'created_at'];
 const rows = filteredCampaigns.map((c) => [
 c.id, c.name, c.status, c.type, c.target_segment || '', c.audience_size,
 c.starts_at || '', c.ends_at || '', c.cost_budget ?? '', c.created_at,
 ]);
 const csv = [header, ...rows].map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
 const blob = new Blob([csv], { type: 'text/csv' });
 const url = URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url; a.download = `campaigns-${slug}.csv`; a.click();
 setTimeout(() => URL.revokeObjectURL(url), 1000);
 }

 /* ─── metrics chart ─── */
 async function renderMetricsChart() {
 if (!drawerCampaign || !metricsContainer) return;
 const ms = drawerCampaign.metrics || [];
 if (ms.length === 0) {
 if (metricsChart) { try { metricsChart.dispose(); } catch {} metricsChart = null; }
 return;
 }
 try {
 const echarts = await import('echarts');
 if (!metricsChart) metricsChart = echarts.init(metricsContainer);
 // Group metrics by name → sorted by recorded_at
 const byName: Record<string, { x: string; y: number }[]> = {};
 for (const m of ms) {
 const name = m.metric_name;
 const t = m.recorded_at;
 if (!byName[name]) byName[name] = [];
 byName[name].push({ x: t, y: Number(m.value) });
 }
 Object.values(byName).forEach((arr) => arr.sort((a, b) => a.x.localeCompare(b.x)));
 const series = Object.entries(byName).map(([name, arr]) => ({
 name, type: 'line', smooth: true, symbol: 'circle', symbolSize: 6,
 data: arr.map((p) => [p.x, p.y]),
 }));
 metricsChart.setOption({
 backgroundColor: 'transparent',
 textStyle: { fontFamily: 'JetBrains Mono, monospace', color: '#1a1a1a' },
 grid: { left: 50, right: 20, top: 40, bottom: 40 },
 legend: { top: 0 },
 tooltip: { trigger: 'axis' },
 xAxis: { type: 'time' },
 yAxis: { type: 'value' },
 series,
 }, true);
 } catch (e) {
 console.error('chart render', e);
 }
 }

 $effect(() => {
 if (drawerTab === 'metrics' && drawerCampaign) {
 tick().then(() => renderMetricsChart());
 }
 if (drawerTab === 'roi' && drawerCampaign && !roiData) {
 fetchROI();
 }
 if (drawerTab === 'audience' && drawerCampaign && !audiencePreview) {
 fetchAudience();
 }
 });

 /* ─── lifecycle ─── */
 function onKey(e: KeyboardEvent) {
 if (e.key === 'Escape') {
 if (showNewModal) showNewModal = false;
 else if (drawerOpen) closeDrawer();
 }
 }

 onMount(async () => {
 token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 if (!token) {
 goto('/login');
 return;
 }
 slug = $page.params.slug || '';
 if (!slug) {
 errorMsg = 'project slug missing';
 return;
 }
 tickTimer = setInterval(() => { nowTick = Date.now(); }, 5000);
 document.addEventListener('keydown', onKey);
 await loadAll();
 await loadSegments();
 });

 onDestroy(() => {
 if (tickTimer) clearInterval(tickTimer);
 if (typeof document !== 'undefined') document.removeEventListener('keydown', onKey);
 if (metricsChart) { try { metricsChart.dispose(); } catch {} }
 });
</script>

<svelte:head>
  <title>Campaigns · {slug}</title>
</svelte:head>

<div class="page" class:dim={drawerOpen}>
  <!-- ─── Header ─── -->
  <header class="hdr">
    <div class="hdr-l">
      <a href={`${base}/project/${slug}/settings`} class="back-btn" title="Back to settings">←</a>
      <span class="ico"><Icon name="megaphone" size={14} /></span>
      <span class="ttl">CAMPAIGNS</span>
      <span class="sep">·</span>
      <span class="slug">{slug}</span>
      {#if loading}<span class="dot pulse"></span>{/if}
    </div>
    <div class="hdr-r">
      {#if lastFetched}<span class="muted small">{lastFetchedLabel}</span>{/if}
      <button class="btn" onclick={() => loadAll()} title="refresh">↻ REFRESH</button>
      <button class="btn" onclick={exportCSV} title="export">↓ CSV</button>
      <button class="btn auto-btn" onclick={runAutoCampaign} disabled={autoBusy} title="Run auto-campaign daemon now">
        {autoBusy ? '… RUNNING' : 'AUTO RUN'}
      </button>
      <button class="btn primary" onclick={() => { showNewModal = true; }}>+ NEW CAMPAIGN</button>
    </div>
  </header>

  {#if errorMsg}
    <div class="banner err"><Icon name="alert-triangle" size={14} /> {errorMsg} <button class="x" onclick={() => errorMsg = ''}><Icon name="x" size={14} /></button></div>
  {/if}
  {#if autoToast}
    <div class="banner auto-banner"><Icon name="bot" size={14} /> {autoToast}</div>
  {/if}

  <!-- ─── KPI Cards ─── -->
  <section class="kpis">
    <div class="kpi">
      <div class="kpi-lbl">TOTAL</div>
      <div class="kpi-val">{summary.total ?? 0}</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">ACTIVE</div>
      <div class="kpi-val" style="color:#10b981">{(summary.by_status && summary.by_status.active) || 0}</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">SCHEDULED</div>
      <div class="kpi-val" style="color:#3b82f6">{(summary.by_status && summary.by_status.scheduled) || 0}</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">DRAFTS</div>
      <div class="kpi-val" style="color:#888">{(summary.by_status && summary.by_status.draft) || 0}</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">AUDIENCE</div>
      <div class="kpi-val">{fmtNum(summary.total_audience)}</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">BUDGET</div>
      <div class="kpi-val">{fmtMoney(summary.total_budget)}</div>
    </div>
  </section>

  <!-- ─── Status pills ─── -->
  <section class="pills">
    {#each STATUS_TABS as s}
      <button class="pill" class:active={activeStatus === s} class:auto-pill={s === 'auto'} onclick={() => activeStatus = s}>
        {#if s === 'auto'}<Icon name="bot" size={14} /> {/if}{s.toUpperCase()}
        {#if s === 'auto'}
          {@const n = campaigns.filter((c) => c.type === 'auto').length}
          {#if n > 0}<span class="pill-count">{n}</span>{/if}
        {:else if s !== 'all' && summary.by_status && summary.by_status[s]}
          <span class="pill-count">{summary.by_status[s]}</span>
        {/if}
      </button>
    {/each}
    <input class="search" type="text" placeholder="search name / segment…" bind:value={searchTerm} />
  </section>

  <!-- ─── Bulk bar ─── -->
  {#if selected.size > 0}
    <section class="bulk">
      <span class="bulk-count">{selected.size} selected</span>
      <button class="btn small" onclick={() => bulkAction('launch')}>▶ LAUNCH ALL</button>
      <button class="btn small" onclick={() => bulkAction('pause')}>‖ PAUSE ALL</button>
      <button class="btn small" onclick={() => { selected = new Set(); }}><Icon name="x" size={14} /> CLEAR</button>
    </section>
  {/if}

  <!-- ─── Table ─── -->
  <section class="tbl-wrap">
    {#if filteredCampaigns.length === 0 && !loading}
      <div class="empty">
        <div class="empty-ico"><Icon name="inbox" size={14} /></div>
        <div class="empty-ttl">No campaigns yet.</div>
        <div class="empty-sub">Click + NEW CAMPAIGN to create one.</div>
      </div>
    {:else}
      <table class="tbl">
        <thead>
          <tr>
            <th class="chk">
              <input type="checkbox" checked={allOnPageSelected} onchange={toggleAll} />
            </th>
            <th>NAME</th>
            <th>STATUS</th>
            <th>TYPE</th>
            <th>SEGMENT</th>
            <th class="num">AUDIENCE</th>
            <th>STARTS</th>
            <th>ENDS</th>
            <th class="num">BUDGET</th>
            <th>ACTIONS</th>
          </tr>
        </thead>
        <tbody>
          {#each filteredCampaigns as c (c.id)}
            <tr class="row" onclick={() => openDrawer(c)}>
              <td class="chk" onclick={(e) => e.stopPropagation()}>
                <input type="checkbox" checked={selected.has(c.id)} onchange={() => toggleOne(c.id)} />
              </td>
              <td class="name">
                {c.name}
                {#if c.description}<div class="muted small">{c.description.slice(0, 60)}{c.description.length > 60 ? '…' : ''}</div>{/if}
              </td>
              <td>
                <span class="status-pill" style="background:{statusColor(c.status)}">{c.status}</span>
              </td>
              <td><code>{c.type}</code></td>
              <td>{c.target_segment || '—'}</td>
              <td class="num">{fmtNum(c.audience_size)}</td>
              <td class="muted small">{fmtDate(c.starts_at)}</td>
              <td class="muted small">{fmtDate(c.ends_at)}</td>
              <td class="num">{fmtMoney(c.cost_budget)}</td>
              <td onclick={(e) => e.stopPropagation()}>
                <button class="ico-btn" title="copy id" onclick={() => copyText(String(c.id))}><Icon name="clipboard" size={14} /></button>
                <button class="ico-btn" title="open" onclick={() => openDrawer(c)}>→</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
</div>

<!-- ─── Drawer ─── -->
{#if drawerOpen && drawerCampaign}
  <div class="drawer-bg" onclick={closeDrawer}></div>
  <aside class="drawer">
    <header class="drw-hdr">
      <div class="drw-id">
        <code>#{drawerCampaign.id}</code>
        <button class="ico-btn" title="copy id" onclick={() => copyText(String(drawerCampaign!.id))}><Icon name="clipboard" size={14} /></button>
      </div>
      <button class="x" onclick={closeDrawer}><Icon name="x" size={14} /></button>
    </header>

    {#if drawerLoading}
      <div class="drw-load">loading…</div>
    {:else}
      <!-- name -->
      <div class="drw-name">
        {#if editingName}
          <input class="inp wide" bind:value={nameDraft} />
          <button class="btn small primary" onclick={saveNameDesc}>SAVE</button>
          <button class="btn small" onclick={() => { editingName = false; nameDraft = drawerCampaign!.name; }}>CANCEL</button>
        {:else}
          <h2 onclick={() => { editingName = true; }}>{drawerCampaign.name}</h2>
        {/if}
      </div>

      <!-- status + actions -->
      <div class="drw-status">
        <span class="status-pill big" style="background:{statusColor(drawerCampaign.status)}">{drawerCampaign.status.toUpperCase()}</span>
        <div class="drw-acts">
          {#if drawerCampaign.status === 'draft' || drawerCampaign.status === 'scheduled'}
            <button class="btn small primary" onclick={() => transition('launch')}>▶ LAUNCH</button>
          {/if}
          {#if drawerCampaign.status === 'active'}
            <button class="btn small" onclick={() => transition('pause')}>‖ PAUSE</button>
            <button class="btn small" onclick={() => transition('complete')}><Icon name="check" size={14} /> COMPLETE</button>
          {/if}
          {#if drawerCampaign.status === 'paused'}
            <button class="btn small primary" onclick={() => transition('resume')}>▶ RESUME</button>
            <button class="btn small" onclick={() => transition('complete')}><Icon name="check" size={14} /> COMPLETE</button>
          {/if}
          {#if drawerCampaign.status !== 'cancelled'}
            <button class="btn small danger" onclick={deleteCampaign}><Icon name="x" size={14} /> CANCEL</button>
          {/if}
        </div>
      </div>

      <!-- description -->
      <div class="drw-desc">
        {#if editingDesc}
          <textarea class="inp wide" rows="3" bind:value={descDraft}></textarea>
          <div class="row-acts">
            <button class="btn small primary" onclick={saveNameDesc}>SAVE</button>
            <button class="btn small" onclick={() => { editingDesc = false; descDraft = drawerCampaign!.description || ''; }}>CANCEL</button>
          </div>
        {:else}
          <p class="muted" onclick={() => { editingDesc = true; }}>
            {drawerCampaign.description || '(no description — click to edit)'}
          </p>
        {/if}
      </div>

      <!-- tabs -->
      <nav class="drw-tabs">
        {#each ['overview', 'metrics', 'events', 'audience', 'roi'] as t}
          <button class="drw-tab" class:active={drawerTab === t} onclick={() => drawerTab = t as any}>
            {t.toUpperCase()}
          </button>
        {/each}
      </nav>

      <!-- tab content -->
      <div class="drw-body">
        {#if drawerTab === 'overview'}
          {#if drawerCampaign.type === 'auto' && drawerCampaign.metadata && drawerCampaign.metadata.reasoning}
            {@const r = drawerCampaign.metadata.reasoning}
            <div class="auto-reasoning">
              <div class="auto-reasoning-hdr"><Icon name="bot" size={14} /> AUTO-DRAFTED · rule: <code>{drawerCampaign.metadata.rule || '—'}</code></div>
              <div class="auto-reasoning-row"><span class="ar-k">Detected</span><span class="ar-v">{r.detected_change || '—'}</span></div>
              <div class="auto-reasoning-row"><span class="ar-k">Suggested discount</span><span class="ar-v">{r.suggested_discount}%</span></div>
              <div class="auto-reasoning-row"><span class="ar-k">Suggested audience</span><span class="ar-v">{r.suggested_audience || '—'}</span></div>
              <div class="auto-reasoning-row"><span class="ar-k">Expected lift</span><span class="ar-v" style="color:#10b981;font-weight:600">${(r.expected_revenue_lift || 0).toLocaleString()}</span></div>
              {#if drawerCampaign.status === 'draft'}
                <div class="auto-reasoning-actions">
                  <button class="btn primary" style="background:#10b981" onclick={() => approveAndLaunch(drawerCampaign!)}><Icon name="check" size={14} /> APPROVE & LAUNCH</button>
                  <button class="btn" onclick={() => { editingName = true; }}><Icon name="pencil" size={14} /> EDIT</button>
                  <button class="btn danger" onclick={() => dismissAuto(drawerCampaign!)}><Icon name="x" size={14} /> DISMISS</button>
                </div>
              {:else}
                <div class="auto-reasoning-actions"><span class="muted small">Status: {drawerCampaign.status}</span></div>
              {/if}
            </div>
          {/if}
          <div class="kv-grid">
            <div class="kv"><span class="k">TYPE</span><span class="v"><code>{drawerCampaign.type}</code></span></div>
            <div class="kv"><span class="k">SEGMENT</span><span class="v">{drawerCampaign.target_segment || '—'}</span></div>
            <div class="kv"><span class="k">AUDIENCE</span><span class="v">{fmtNum(drawerCampaign.audience_size)}</span></div>
            <div class="kv"><span class="k">BUDGET</span><span class="v">{fmtMoney(drawerCampaign.cost_budget)}</span></div>
            <div class="kv"><span class="k">STARTS</span><span class="v">{fmtDate(drawerCampaign.starts_at)}</span></div>
            <div class="kv"><span class="k">ENDS</span><span class="v">{fmtDate(drawerCampaign.ends_at)}</span></div>
            <div class="kv"><span class="k">CREATED BY</span><span class="v">{drawerCampaign.created_by || '—'}</span></div>
            <div class="kv"><span class="k">CREATED</span><span class="v">{fmtDate(drawerCampaign.created_at)}</span></div>
          </div>
          <details class="json-block">
            <summary>OFFER</summary>
            <pre>{JSON.stringify(drawerCampaign.offer || {}, null, 2)}</pre>
          </details>
          <details class="json-block">
            <summary>TARGET FILTER</summary>
            <pre>{JSON.stringify(drawerCampaign.target_filter || {}, null, 2)}</pre>
          </details>
        {:else if drawerTab === 'metrics'}
          <div class="metric-quick">
            <select bind:value={mq_name}>
              <option>impressions</option>
              <option>clicks</option>
              <option>conversions</option>
              <option>revenue</option>
              <option>opt_outs</option>
            </select>
            <input class="inp" type="number" placeholder="value" bind:value={mq_value} />
            <button class="btn small primary" onclick={recordMetric} disabled={mq_busy}>
              {mq_busy ? '…' : '+ ADD'}
            </button>
          </div>
          <div class="chart-box" bind:this={metricsContainer}></div>
          {#if drawerCampaign.metrics && drawerCampaign.metrics.length > 0}
            <table class="mini-tbl">
              <thead><tr><th>METRIC</th><th class="num">VALUE</th><th>RECORDED</th></tr></thead>
              <tbody>
                {#each drawerCampaign.metrics.slice(0, 30) as m}
                  <tr><td>{m.metric_name}</td><td class="num">{fmtNum(m.value)}</td><td class="muted small">{fmtDate(m.recorded_at)}</td></tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <p class="muted center">No metrics recorded yet.</p>
          {/if}
        {:else if drawerTab === 'events'}
          {#if drawerCampaign.events && drawerCampaign.events.length > 0}
            <ul class="evt-log">
              {#each drawerCampaign.events as e}
                <li>
                  <span class="evt-type">{e.event_type}</span>
                  <span class="muted small">{fmtDate(e.occurred_at)}</span>
                  {#if e.actor}<span class="muted small">by {e.actor}</span>{/if}
                  {#if e.payload && Object.keys(e.payload).length > 0}
                    <details><summary>payload</summary><pre>{JSON.stringify(e.payload, null, 2)}</pre></details>
                  {/if}
                </li>
              {/each}
            </ul>
          {:else}
            <p class="muted center">No events.</p>
          {/if}
        {:else if drawerTab === 'audience'}
          <div class="row-acts">
            <button class="btn small" onclick={fetchAudience}>↻ RERUN AUDIENCE QUERY</button>
          </div>
          {#if audiencePreview}
            <div class="kv-grid">
              <div class="kv"><span class="k">SEGMENT</span><span class="v">{audiencePreview.target_segment || '—'}</span></div>
              <div class="kv"><span class="k">TOTAL</span><span class="v">{fmtNum(audiencePreview.audience_total)}</span></div>
              <div class="kv"><span class="k">SAMPLE</span><span class="v">{audiencePreview.sample_count}</span></div>
            </div>
            {#if audiencePreview.audience_sample && audiencePreview.audience_sample.length > 0}
              <table class="mini-tbl">
                <thead><tr><th>CUSTOMER</th><th>SEGMENT</th><th class="num">FREQ</th><th class="num">SPEND</th></tr></thead>
                <tbody>
                  {#each audiencePreview.audience_sample.slice(0, 50) as c}
                    <tr>
                      <td>{c.customer || '—'}</td>
                      <td>{c.segment || '—'}</td>
                      <td class="num">{fmtNum(c.frequency)}</td>
                      <td class="num">{fmtMoney(c.monetary)}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
          {:else}
            <p class="muted center">Click rerun to load audience.</p>
          {/if}
        {:else if drawerTab === 'roi'}
          {#if roiData}
            <div class="kpi-strip">
              <div class="kpi sm"><div class="kpi-lbl">REVENUE</div><div class="kpi-val">{fmtMoney(roiData.revenue_total)}</div></div>
              <div class="kpi sm"><div class="kpi-lbl">COST</div><div class="kpi-val">{fmtMoney(roiData.cost)}</div></div>
              <div class="kpi sm">
                <div class="kpi-lbl">ROI</div>
                <div class="kpi-val" style="color:{(roiData.roi_pct || 0) >= 0 ? '#10b981' : '#ef4444'}">
                  {roiData.roi_pct === null ? '—' : roiData.roi_pct.toFixed(1) + '%'}
                </div>
              </div>
            </div>
            <div class="kv-grid">
              <div class="kv"><span class="k">CONVERSIONS</span><span class="v">{fmtNum(roiData.conversions_total)}</span></div>
              <div class="kv"><span class="k">CONV. RATE</span><span class="v">{roiData.conversion_rate === null ? '—' : roiData.conversion_rate.toFixed(2) + '%'}</span></div>
              <div class="kv"><span class="k">IMPRESSIONS</span><span class="v">{fmtNum(roiData.impressions_total)}</span></div>
              <div class="kv"><span class="k">CLICKS</span><span class="v">{fmtNum(roiData.clicks_total)}</span></div>
              <div class="kv"><span class="k">CTR</span><span class="v">{roiData.ctr === null ? '—' : roiData.ctr.toFixed(2) + '%'}</span></div>
              <div class="kv"><span class="k">OPT OUTS</span><span class="v">{fmtNum(roiData.opt_outs_total)}</span></div>
            </div>
          {:else}
            <p class="muted center">loading roi…</p>
          {/if}
        {/if}
      </div>
    {/if}
  </aside>
{/if}

<!-- ─── New Campaign Modal ─── -->
{#if showNewModal}
  <div class="mod-bg" onclick={() => showNewModal = false}></div>
  <div class="mod">
    <header class="mod-hdr">
      <h3>+ NEW CAMPAIGN</h3>
      <button class="x" onclick={() => showNewModal = false}><Icon name="x" size={14} /></button>
    </header>
    <div class="mod-body">
      {#if nf_err}<div class="banner err">{nf_err}</div>{/if}
      <label>NAME *</label>
      <input class="inp wide" placeholder="Spring Champions Reactivation" bind:value={nf_name} />
      <label>DESCRIPTION</label>
      <textarea class="inp wide" rows="2" placeholder="brief intent" bind:value={nf_desc}></textarea>
      <div class="row-2">
        <div>
          <label>TYPE</label>
          <select class="inp wide" bind:value={nf_type}>
            <option value="manual">manual</option>
            <option value="email">email</option>
            <option value="sms">sms</option>
            <option value="push">push</option>
            <option value="web">web</option>
          </select>
        </div>
        <div>
          <label>TARGET SEGMENT</label>
          <select class="inp wide" bind:value={nf_segment}>
            <option value="">— none —</option>
            {#each segOptions as s}<option value={s.name}>{s.name}</option>{/each}
          </select>
        </div>
      </div>
      <label>OFFER (JSON)</label>
      <textarea class="inp wide mono" rows="5" bind:value={nf_offer}></textarea>
      <div class="row-2">
        <div>
          <label>STARTS AT</label>
          <input class="inp wide" type="datetime-local" bind:value={nf_starts} />
        </div>
        <div>
          <label>ENDS AT</label>
          <input class="inp wide" type="datetime-local" bind:value={nf_ends} />
        </div>
      </div>
      <label>BUDGET (USD)</label>
      <input class="inp wide" type="number" step="0.01" placeholder="500.00" bind:value={nf_budget} />
    </div>
    <footer class="mod-ftr">
      <button class="btn" onclick={() => showNewModal = false}>CANCEL</button>
      <button class="btn primary" onclick={createCampaign} disabled={nf_busy}>
        {nf_busy ? 'CREATING…' : 'CREATE'}
      </button>
    </footer>
  </div>
{/if}

{#if copyFlash}
  <div class="toast">copied: <code>{copyFlash}</code></div>
{/if}

<style>
 :global(body) { margin: 0; }

 .page {
 font-family: 'JetBrains Mono', 'Menlo', monospace;
 background: #faf7f0;
 color: #1a1a1a;
 min-height: 100vh;
 padding: 16px 22px 60px;
 transition: filter 0.2s ease;
 }
 .page.dim { filter: brightness(0.85); }

 .hdr {
 display: flex; justify-content: space-between; align-items: center;
 border-bottom: 2px solid #1a1a1a;
 padding-bottom: 10px; margin-bottom: 16px;
 }
 .hdr-l { display: flex; align-items: center; gap: 8px; }
 .back-btn { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 24px; padding: 0; background: transparent; color: #888; border: 1px solid #555; font-family: monospace; font-size: 11px; font-weight: 700; text-decoration: none; cursor: pointer; }
 .back-btn:hover { color: #00fc40; border-color: #00fc40; }
 .ico { font-size: 14px; }
 .ttl { font-size: 13px; font-weight: 700; letter-spacing: 1px; }
 .sep { color: #888; }
 .slug { color: #444; font-weight: 600; }
 .hdr-r { display: flex; gap: 8px; align-items: center; }

 .btn {
 background: #faf7f0; color: #1a1a1a;
 border: 1px solid #1a1a1a; padding: 6px 12px;
 font-family: inherit; font-size: 11px; cursor: pointer;
 letter-spacing: 0.5px; font-weight: 600;
 transition: background 0.1s;
 }
 .btn:hover { background: #1a1a1a; color: #faf7f0; }
 .btn.primary { background: #1a1a1a; color: #faf7f0; }
 .btn.primary:hover { background: #333; }
 .btn.danger { color: #ef4444; border-color: #ef4444; }
 .btn.danger:hover { background: #ef4444; color: #fff; }
 .btn.small { padding: 4px 8px; font-size: 11px; }
 .btn:disabled { opacity: 0.5; cursor: not-allowed; }

 .ico-btn {
 background: transparent; border: 1px solid transparent;
 color: #444; cursor: pointer; padding: 2px 6px;
 font-family: inherit; font-size: 11px; border-radius: 0;
 }
 .ico-btn:hover { background: #ede8d9; border-color: #ccc; }

 .banner.err {
 background: #fee; border: 1px solid #ef4444; color: #b91c1c;
 padding: 8px 12px; margin-bottom: 12px;
 display: flex; justify-content: space-between; align-items: center;
 }
 .banner .x { background: transparent; border: none; cursor: pointer; color: inherit; font-size: 11px; }

 .kpis {
 display: grid; grid-template-columns: repeat(6, 1fr);
 gap: 8px; margin-bottom: 14px;
 }
 .kpi {
 border: 1px solid #1a1a1a; padding: 10px 12px;
 background: #fff;
 }
 .kpi.sm { padding: 8px 10px; }
 .kpi-lbl { font-size: 10px; color: #666; letter-spacing: 1px; margin-bottom: 4px; }
 .kpi-val { font-size: 19px; font-weight: 700; }
 .kpi-strip { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
 .kpi-strip .kpi { flex: 1; min-width: 120px; }

 .pills {
 display: flex; gap: 6px; flex-wrap: wrap;
 border-bottom: 1px dashed #ccc; padding-bottom: 10px; margin-bottom: 12px;
 align-items: center;
 }
 .pill {
 background: transparent; border: 1px solid #1a1a1a;
 padding: 4px 10px; cursor: pointer; font-family: inherit;
 font-size: 11px; letter-spacing: 0.5px;
 }
 .pill:hover { background: #ede8d9; }
 .pill.active { background: #1a1a1a; color: #faf7f0; }
 .pill-count {
 background: #faf7f0; color: #1a1a1a; padding: 0 5px;
 border-radius: 0; margin-left: 4px; font-size: 10px;
 }
 .pill.active .pill-count { background: #faf7f0; color: #1a1a1a; }
 .search {
 margin-left: auto; padding: 4px 8px; border: 1px solid #888;
 background: #fff; font-family: inherit; font-size: 11px; min-width: 220px;
 }

 .bulk {
 display: flex; gap: 8px; align-items: center;
 background: #1a1a1a; color: #faf7f0; padding: 8px 12px;
 margin-bottom: 8px;
 }
 .bulk-count { font-weight: 600; }
 .bulk .btn { background: transparent; color: #faf7f0; border-color: #faf7f0; }
 .bulk .btn:hover { background: #faf7f0; color: #1a1a1a; }

 .tbl-wrap { overflow-x: auto; border: 1px solid #1a1a1a; background: #fff; }
 .tbl { width: 100%; border-collapse: collapse; font-size: 11px; }
 .tbl thead th {
 background: #1a1a1a; color: #faf7f0; text-align: left;
 padding: 8px; letter-spacing: 0.5px; font-size: 11px;
 position: sticky; top: 0;
 }
 .tbl tbody td { padding: 8px; border-bottom: 1px solid #eee; }
 .tbl tbody tr.row { cursor: pointer; }
 .tbl tbody tr.row:hover { background: #faf3df; }
 .tbl tbody tr:nth-child(even) { background: #fdfbf3; }
 .tbl tbody tr:nth-child(even):hover { background: #faf3df; }
 .num { text-align: right; font-variant-numeric: tabular-nums; }
 .chk { width: 32px; }
 .name { font-weight: 600; }

 .status-pill {
 display: inline-block; color: #fff;
 padding: 2px 8px; font-size: 10px; letter-spacing: 0.5px;
 text-transform: uppercase; border-radius: 0;
 }
 .status-pill.big { padding: 4px 12px; font-size: 11px; font-weight: 700; }

 .empty {
 text-align: center; padding: 60px 20px;
 }
 .empty-ico { font-size: 48px; margin-bottom: 12px; }
 .empty-ttl { font-size: 12px; font-weight: 600; margin-bottom: 6px; }
 .empty-sub { color: #666; }

 .muted { color: #666; }
 .small { font-size: 11px; }
 .center { text-align: center; padding: 18px; }

 /* Drawer */
 .drawer-bg {
 position: fixed; inset: 0; background: rgba(0,0,0,0.3);
 z-index: 998;
 }
 .drawer {
 position: fixed; top: 0; right: 0; bottom: 0; width: 480px;
 background: #faf7f0; border-left: 2px solid #1a1a1a;
 z-index: 999; overflow-y: auto;
 box-shadow: -4px 0 20px rgba(0,0,0,0.15);
 display: flex; flex-direction: column;
 }
 .drw-hdr {
 display: flex; justify-content: space-between; align-items: center;
 padding: 10px 16px; border-bottom: 1px solid #1a1a1a;
 background: #ede8d9;
 }
 .drw-id { display: flex; align-items: center; gap: 6px; }
 .drw-id code { background: #1a1a1a; color: #faf7f0; padding: 2px 6px; border-radius: 0; }
 .drw-hdr .x { background: transparent; border: 1px solid #1a1a1a; padding: 2px 8px; cursor: pointer; font-family: inherit; }
 .drw-load { padding: 40px; text-align: center; color: #666; }

 .drw-name { padding: 14px 16px 4px; }
 .drw-name h2 { margin: 0; font-size: 13px; cursor: text; }
 .drw-name h2:hover { background: #ede8d9; }

 .drw-status {
 display: flex; justify-content: space-between; align-items: center;
 padding: 8px 16px;
 }
 .drw-acts { display: flex; gap: 6px; flex-wrap: wrap; }

 .drw-desc { padding: 4px 16px 12px; }
 .drw-desc p { margin: 0; cursor: text; min-height: 20px; }
 .drw-desc p:hover { background: #ede8d9; }

 .row-acts { display: flex; gap: 6px; margin-top: 6px; }

 .drw-tabs {
 display: flex; gap: 0; border-top: 1px solid #1a1a1a;
 border-bottom: 1px solid #1a1a1a; background: #ede8d9;
 }
 .drw-tab {
 flex: 1; background: transparent; border: none;
 padding: 8px 4px; font-family: inherit; cursor: pointer;
 font-size: 11px; letter-spacing: 0.5px; font-weight: 600;
 border-right: 1px solid #ccc;
 }
 .drw-tab:last-child { border-right: none; }
 .drw-tab:hover { background: #d8d2bf; }
 .drw-tab.active { background: #1a1a1a; color: #faf7f0; }

 .drw-body { padding: 14px 16px; flex: 1; }

 .kv-grid {
 display: grid; grid-template-columns: repeat(2, 1fr);
 gap: 6px; margin-bottom: 12px;
 }
 .kv {
 display: flex; justify-content: space-between;
 border-bottom: 1px dotted #ccc; padding: 4px 0;
 font-size: 11px;
 }
 .kv .k { color: #666; letter-spacing: 0.5px; }
 .kv .v { font-weight: 600; }

 .json-block {
 background: #1a1a1a; color: #cfd8dc;
 border: 1px solid #1a1a1a; padding: 0; margin-bottom: 8px;
 font-size: 11px;
 }
 .json-block summary { padding: 6px 10px; cursor: pointer; color: #faf7f0; }
 .json-block pre { margin: 0; padding: 8px 10px; overflow-x: auto; max-height: 200px; }

 .metric-quick {
 display: flex; gap: 6px; margin-bottom: 10px;
 }
 .metric-quick select, .metric-quick input { flex: 1; }

 .chart-box { width: 100%; height: 220px; margin-bottom: 12px; background: #fff; border: 1px solid #ddd; }

 .mini-tbl { width: 100%; border-collapse: collapse; font-size: 11px; }
 .mini-tbl thead th {
 background: #1a1a1a; color: #faf7f0; text-align: left;
 padding: 4px 6px;
 }
 .mini-tbl tbody td { padding: 4px 6px; border-bottom: 1px dotted #ccc; }

 .evt-log { list-style: none; padding: 0; margin: 0; }
 .evt-log li {
 padding: 6px 8px; border-left: 3px solid #1a1a1a;
 margin-bottom: 6px; background: #fff; font-size: 11px;
 }
 .evt-type {
 background: #1a1a1a; color: #faf7f0;
 padding: 1px 6px; margin-right: 6px; font-size: 10px;
 }
 .evt-log details { margin-top: 4px; }
 .evt-log pre { background: #1a1a1a; color: #cfd8dc; padding: 6px; font-size: 10px; overflow-x: auto; }

 /* Modal */
 .mod-bg {
 position: fixed; inset: 0; background: rgba(0,0,0,0.5);
 z-index: 1000;
 }
 .mod {
 position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
 background: #faf7f0; border: 2px solid #1a1a1a;
 width: 540px; max-width: 90vw; max-height: 90vh;
 z-index: 1001; display: flex; flex-direction: column;
 box-shadow: 0 10px 40px rgba(0,0,0,0.25);
 }
 .mod-hdr {
 display: flex; justify-content: space-between; align-items: center;
 padding: 10px 16px; background: #1a1a1a; color: #faf7f0;
 }
 .mod-hdr h3 { margin: 0; font-size: 11px; letter-spacing: 1px; }
 .mod-hdr .x { background: transparent; border: 1px solid #faf7f0; color: #faf7f0; padding: 2px 8px; cursor: pointer; font-family: inherit; }
 .mod-body { padding: 14px 16px; overflow-y: auto; }
 .mod-body label {
 display: block; font-size: 10px; color: #666;
 letter-spacing: 1px; margin: 8px 0 3px;
 }
 .mod-ftr {
 display: flex; justify-content: flex-end; gap: 8px;
 padding: 10px 16px; border-top: 1px solid #1a1a1a;
 }
 .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }

 .inp {
 border: 1px solid #1a1a1a; background: #fff;
 padding: 5px 8px; font-family: inherit; font-size: 11px;
 }
 .inp.wide { width: 100%; box-sizing: border-box; }
 .inp.mono { font-family: 'JetBrains Mono', monospace; }

 .toast {
 position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
 background: #1a1a1a; color: #faf7f0; padding: 8px 16px;
 border-radius: 0; z-index: 2000; font-size: 11px;
 box-shadow: 0 4px 12px rgba(0,0,0,0.3);
 }
 .toast code { background: transparent; color: #fbbf24; }

 .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #10b981; }
 .pulse { animation: pulse 1s infinite; }
 @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

 @media (max-width: 768px) {
 .kpis { grid-template-columns: repeat(3, 1fr); }
 .drawer { width: 100%; }
 }
/* ─── Auto-Campaign Daemon UI ─── */
.btn.auto-btn {
 background: #fff7ed;
 border-color: #fb923c;
 color: #9a3412;
}
.btn.auto-btn:hover:not(:disabled) {
 background: #fed7aa;
}
.pill.auto-pill {
 border-color: #fb923c;
 color: #9a3412;
 background: #fff7ed;
}
.pill.auto-pill.active {
 background: #fb923c;
 color: #fff;
 border-color: #ea580c;
}
.banner.auto-banner {
 background: #fff7ed;
 border: 1px solid #fb923c;
 color: #9a3412;
 padding: 8px 12px;
 border-radius: 0;
 margin-bottom: 8px;
 font-size: 11px;
}
.auto-reasoning {
 background: linear-gradient(180deg,#fff7ed 0%, #ffedd5 100%);
 border: 1px solid #fb923c;
 border-radius: 0;
 padding: 12px 14px;
 margin-bottom: 14px;
}
.auto-reasoning-hdr {
 font-size: 11px;
 font-weight: 700;
 letter-spacing: 0.04em;
 color: #9a3412;
 margin-bottom: 8px;
}
.auto-reasoning-hdr code {
 background: #fed7aa;
 padding: 1px 6px;
 border-radius: 0;
 font-size: 11px;
}
.auto-reasoning-row {
 display: flex;
 justify-content: space-between;
 align-items: baseline;
 padding: 4px 0;
 font-size: 11px;
 border-top: 1px dashed #fdba74;
}
.auto-reasoning-row:first-of-type { border-top: none; }
.auto-reasoning-row .ar-k {
 color: #7c2d12;
 font-weight: 500;
 text-transform: uppercase;
 font-size: 11px;
 letter-spacing: 0.03em;
}
.auto-reasoning-row .ar-v {
 color: #1c1917;
 font-weight: 500;
 text-align: right;
}
.auto-reasoning-actions {
 display: flex;
 gap: 8px;
 margin-top: 12px;
 flex-wrap: wrap;
}
.btn.danger {
 border-color: #ef4444;
 color: #b91c1c;
}
.btn.danger:hover:not(:disabled) {
 background: #fee2e2;
}
</style>
