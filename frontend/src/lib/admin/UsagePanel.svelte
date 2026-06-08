<script lang="ts">
  import { dashFetch } from '$lib/api';
  import { onMount, onDestroy } from 'svelte';

  let { embedded = false } = $props();

  // ---- tabs ----
  const TABS = [
    ['overview', 'Overview'], ['performance', 'Performance'], ['errors', 'Errors'],
    ['tools', 'Tools'], ['security', 'Security'], ['entities', 'Entities'],
    ['billing', 'Billing'], ['live', 'Live'],
  ];
  let tab = $state('overview');
  let loaded: Record<string, boolean> = $state({});

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
  let entUsers: any[] = $state([]), entStores: any[] = $state([]);
  let logins: any[] = $state([]);
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
  async function jget(url: string) {
    const r = await dashFetch(url, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function loadTab(t: string, force = false) {
    if (loaded[t] && !force) return;
    loading = true; error = '';
    try {
      if (t === 'overview') {
        const [a, b] = await Promise.all([jget(`/api/admin/usage?${qs({ group_by: groupBy })}`), jget(`/api/admin/usage/logins?${qs()}`)]);
        ov = a; logins = b?.logins ?? []; if (a?.error) error = a.error;
      } else if (t === 'performance') perf = await jget(`/api/admin/usage/performance?${qs()}`);
      else if (t === 'errors') errs = await jget(`/api/admin/usage/errors?${qs()}`);
      else if (t === 'tools') tools = await jget(`/api/admin/usage/tools?${qs()}`);
      else if (t === 'security') sec = await jget(`/api/admin/usage/security?${qs()}`);
      else if (t === 'live') live = await jget(`/api/admin/usage/live`);
      else if (t === 'billing') {
        const [b, i] = await Promise.all([jget(`/api/admin/usage/budget`), jget(`/api/admin/usage/invoice?${qs({ group: invGroup })}`)]);
        budget = b; invoice = i; bDaily = b?.daily_usd ?? 0; bMonthly = b?.monthly_usd ?? 0;
      } else if (t === 'entities') {
        const u = await jget(`/api/admin/usage?${qs({ group_by: 'actor', limit: '1' })}`);
        const s = await jget(`/api/admin/usage?${qs({ group_by: 'store_id', limit: '1' })}`);
        entUsers = u?.breakdown ?? []; entStores = s?.breakdown ?? [];
      }
      loaded[t] = true; loaded = { ...loaded };
    } catch (e: any) { error = e?.message || String(e); }
    finally { loading = false; }
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
  {#if !embedded}<header class="u-head"><h1>Usage &amp; Cost</h1></header>{/if}

  <!-- tab strip -->
  <div class="u-tabs">
    {#each TABS as [id, label]}
      <button class="u-tab" class:on={tab === id} onclick={() => switchTab(id)}>{label}</button>
    {/each}
  </div>

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

    <!-- heatmap -->
    <div class="u-card"><div class="u-ctitle">Activity heatmap (requests · day × hour)</div>
      <div class="heat">
        <div class="heat-row head"><span class="heat-lbl"></span>{#each Array(24) as _, h}<span class="heat-h">{h % 6 === 0 ? h : ''}</span>{/each}</div>
        {#each DOW as dname, di}
          <div class="heat-row"><span class="heat-lbl">{dname}</span>{#each Array(24) as _, h}{@const v = heatVal(di, h)}<span class="heat-c" title={`${dname} ${h}:00 · ${v} req`} style={`background:rgba(154,74,47,${v ? 0.12 + 0.88 * (v / heatMax) : 0.03})`}></span>{/each}</div>
        {/each}
      </div>
    </div>

    <div class="u-grid2">
      <div class="u-card"><div class="u-ctitle">Breakdown by
        <select class="u-mini" bind:value={groupBy} onchange={() => loadTab('overview', true)}><option value="actor">user/key</option><option value="model">model</option><option value="store_id">store</option><option value="src">source</option><option value="status">status</option></select></div>
        <table class="u-tbl"><thead><tr><th>{groupBy}</th><th>req</th><th>cost</th><th>last</th></tr></thead><tbody>
          {#each ov.breakdown as r}<tr class="click" onclick={() => openDrawer(groupBy === 'store_id' ? 'store' : 'actor', r.key)}><td class="mono">{r.key}</td><td>{compact(r.requests)}</td><td class="num">{usd(r.cost)}</td><td class="dim">{ago(r.last)}</td></tr>{/each}
        </tbody></table>
      </div>
      <div class="u-card"><div class="u-ctitle">Who — logins &amp; usage</div>
        <table class="u-tbl"><thead><tr><th>user</th><th>role</th><th>last login</th><th>req</th><th>cost</th></tr></thead><tbody>
          {#each logins as r}<tr class="click" onclick={() => openDrawer('actor', r.username)}><td class="mono">{r.username}</td><td>{r.role}</td><td class="dim">{ago(r.last_login)}</td><td>{compact(r.requests)}</td><td class="num">{usd(r.cost)}</td></tr>{/each}
        </tbody></table>
      </div>
    </div>

    <div class="u-card"><div class="u-ctitle">Activity log<button class="u-btn ghost sm" onclick={exportActivity}>export csv</button></div>
      <table class="u-tbl"><thead><tr><th>time</th><th>src</th><th>actor</th><th>store</th><th>model</th><th>tok</th><th>cost</th><th>lat</th><th>status</th></tr></thead><tbody>
        {#each ov.activity as a}<tr><td class="dim">{shortTs(a.ts)}</td><td><span class="u-tag {a.src}">{srcLabel(a.src)}</span></td><td class="mono">{a.actor ?? '—'}</td><td class="mono">{a.store_id ?? '—'}</td><td class="mono dim">{a.model ?? '—'}</td><td>{compact(a.tokens)}</td><td class="num">{usd(a.cost)}</td><td class="dim">{ms(a.latency_ms)}</td><td><span class="u-st {a.status}">{a.status}</span></td></tr>{/each}
      </tbody></table>
    </div>
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

  <!-- ============ DRAWER ============ -->
  {#if drawerOpen}
    <div class="dw-backdrop" onclick={closeDrawer} role="presentation"></div>
    <aside class="dw">
      <header class="dw-head"><div><span class="dw-type">{drawerType}</span><h2 class="mono">{drawerId}</h2></div><button class="dw-x" onclick={closeDrawer}>×</button></header>
      <div class="dw-body">
        {#if drawerLoading}<div class="u-load">loading…</div>
        {:else if drawerData?.error}<div class="u-err">{drawerData.error}</div>
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
  .usage { padding: 1rem 1.25rem; color: #2c2620; font-size: 13px; }
  .u-head h1 { margin: 0 0 .75rem; font-size: 1.25rem; }
  .u-tabs { display: flex; gap: .25rem; border-bottom: 1px solid #ece2d4; margin-bottom: 1rem; flex-wrap: wrap; }
  .u-tab { border: none; background: none; padding: .5rem .85rem; cursor: pointer; font-size: 13px; color: #6b6052; border-bottom: 2px solid transparent; margin-bottom: -1px; }
  .u-tab.on { color: #9a4a2f; border-bottom-color: #9a4a2f; font-weight: 600; }
  .u-tab:hover { color: #9a4a2f; }
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
  .dw-body { padding: 1.25rem; } .dw-kpis { grid-template-columns: repeat(2, 1fr); }
</style>
