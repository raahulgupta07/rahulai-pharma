<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { page } from '$app/stores';
 import { goto } from '$app/navigation';

 /* ── auth ── */
 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 let slug = $state('');
 let customerId = $state('');
 let authChecked = $state(false);

 let detail = $state<any>(null);
 let timeline = $state<any[]>([]);
 let notes = $state<any[]>([]);
 let newNote = $state('');
 let savingNote = $state(false);

 let loading = $state(true);
 let errMsg = $state('');
 let lastFetched = $state<Date | null>(null);

 type TabId = 'timeline' | 'journey' | 'history' | 'category' | 'skus' | 'monthly' | 'recs' | 'notes';
 const tabs: { id: TabId; label: string }[] = [
 { id: 'timeline', label: 'TIMELINE' },
 { id: 'journey', label: 'JOURNEY' },
 { id: 'history', label: 'HISTORY' },
 { id: 'monthly', label: 'MONTHLY SPEND' },
 { id: 'category', label: 'CATEGORY MIX' },
 { id: 'skus', label: 'TOP SKUS' },
 { id: 'recs', label: 'RECS' },
 { id: 'notes', label: 'NOTES' },
 ];
 let activeTab = $state<TabId>('timeline');

 /* ── MTA journey state ── */
 type MtaModel = 'linear' | 'time_decay' | 'position' | 'markov';
 let journeyModel = $state<MtaModel>('linear');
 let journeyData = $state<any>(null);
 let journeyLoading = $state(false);
 const channelColors: Record<string, string> = {
 email: '#0078d4', sms: '#8b5cf6', ad: '#dc2626',
 organic: '#16a34a', direct: '#525252', social: '#db2777',
 campaign: '#ea580c', push: '#ca8a04', referral: '#0d9488',
 };
 function channelColor(ch: string): string {
 return channelColors[(ch || '').toLowerCase()] || '#737373';
 }
 async function loadJourney() {
 if (!slug || !customerId) return;
 journeyLoading = true;
 try {
 const r = await fetch(
 `/api/projects/${slug}/attribution/customer/${encodeURIComponent(customerId)}?days=90`,
 { headers: _h() }
 );
 if (r.ok) journeyData = await r.json();
 } catch {} finally { journeyLoading = false; }
 }

 /* echarts holders */
 let categoryEl: HTMLDivElement | undefined = $state();
 let skusEl: HTMLDivElement | undefined = $state();
 let monthlyEl: HTMLDivElement | undefined = $state();
 let categoryChart: any = null;
 let skusChart: any = null;
 let monthlyChart: any = null;
 let echartsLib: any = null;

 /* ── derived ── */
 const segmentColor = $derived.by(() => {
 const seg = (detail?.rfm?.segment || '').toLowerCase();
 if (seg.includes('champion') || seg.includes('loyal')) return '#007518';
 if (seg.includes('at risk') || seg.includes('lost') || seg.includes('cannot')) return '#be2d06';
 if (seg.includes('about to sleep') || seg.includes('hibernating')) return '#ff9d00';
 if (seg.includes('new') || seg.includes('promising') || seg.includes('potential')) return '#0078d4';
 return '#1a1a1a';
 });

 const churnColor = $derived.by(() => {
 const r = detail?.churn?.risk;
 if (r === 'churned' || r === 'at_risk') return '#be2d06';
 if (r === 'cooling') return '#ff9d00';
 if (r === 'active') return '#0078d4';
 return '#888';
 });

 const lifetimeValue = $derived(detail?.stats?.lifetime_value ?? 0);
 const orders = $derived(detail?.stats?.order_count ?? 0);
 const aov = $derived(detail?.stats?.avg_order_value ?? 0);
 const tenureDays = $derived(detail?.stats?.tenure_days ?? 0);
 const clv = $derived(detail?.clv_predicted ?? null);

 /* ── RFM 5×5 heatmap helpers ── */
 function rfmCellColor(r: number, m: number, fHere: number): string {
 // Champions: R=5, F=4-5, M=4-5
 if (r === 5 && fHere >= 4 && m >= 4) return '#007518';
 // Cannot Lose (R=1, F=4-5, dark red)
 if (r === 1 && fHere >= 4) return '#7a1d04';
 // At Risk: R=1-2, F=2-5
 if (r <= 2 && fHere >= 2) return '#be2d06';
 // Hibernating/Lost: R=1-2, F=1-2
 if (r <= 2 && fHere <= 2) return '#888';
 // Loyal: R=4-5, F=3-5
 if (r >= 4 && fHere >= 3) return '#5fb573';
 // New: R=4-5, F=1
 if (r >= 4 && fHere === 1) return '#0078d4';
 // Need Attention: R=3, F=3
 if (r === 3 && fHere === 3) return '#ff9d00';
 // Default
 return '#f5e9c8';
 }
 const rfmHere = $derived.by(() => {
 const r = Number(detail?.rfm?.r) || 0;
 const f = Number(detail?.rfm?.f) || 0;
 const m = Number(detail?.rfm?.m) || 0;
 return { r, f, m };
 });

 /* ── fetch helpers ── */
 async function fetchJSON(url: string, opts: any = {}): Promise<any> {
 const res = await fetch(url, { ...opts, headers: { ..._h(), 'Content-Type': 'application/json', ...(opts.headers || {}) } });
 if (!res.ok) {
 let msg = `HTTP ${res.status}`;
 try { const d = await res.json(); msg = d.detail || d.message || msg; } catch {}
 throw new Error(msg);
 }
 return res.json();
 }

 async function loadDetail() {
 try {
 detail = await fetchJSON(`/api/projects/${slug}/customers/${encodeURIComponent(customerId)}`);
 lastFetched = new Date();
 errMsg = '';
 } catch (e: any) {
 errMsg = e.message || String(e);
 }
 }
 async function loadTimeline() {
 try {
 timeline = await fetchJSON(`/api/projects/${slug}/customers/${encodeURIComponent(customerId)}/timeline?limit=200`);
 } catch (e: any) {
 timeline = [];
 }
 }
 async function loadNotes() {
 try {
 const d = await fetchJSON(`/api/projects/${slug}/customers/${encodeURIComponent(customerId)}/note`);
 notes = d.notes || [];
 } catch {
 notes = [];
 }
 }
 async function saveNote() {
 if (!newNote.trim()) return;
 savingNote = true;
 try {
 await fetchJSON(`/api/projects/${slug}/customers/${encodeURIComponent(customerId)}/note`, {
 method: 'POST', body: JSON.stringify({ note: newNote }),
 });
 newNote = '';
 await loadNotes();
 } catch (e: any) {
 errMsg = e.message || String(e);
 } finally {
 savingNote = false;
 }
 }

 async function refreshAll() {
 loading = true;
 await Promise.all([loadDetail(), loadTimeline(), loadNotes()]);
 loading = false;
 queueMicrotask(renderAllCharts);
 }

 /* ── echarts ── */
 async function loadEcharts() {
 if (echartsLib) return echartsLib;
 try {
 // @ts-ignore
 echartsLib = await import('echarts');
 return echartsLib;
 } catch {
 return null;
 }
 }

 async function renderCategoryChart() {
 if (!categoryEl || !detail?.category_mix?.length) return;
 const ec = await loadEcharts();
 if (!ec) return;
 if (!categoryChart) categoryChart = ec.init(categoryEl);
 const palette = ['#007518', '#0078d4', '#ff9d00', '#be2d06', '#1a1a1a', '#888', '#aaa', '#cf6', '#fc6', '#6cf'];
 categoryChart.setOption({
 tooltip: { trigger: 'item', formatter: '{b}<br/>${c} ({d}%)' },
 series: [{
 type: 'pie', radius: ['40%', '70%'],
 data: detail.category_mix.map((c: any, i: number) => ({
 name: c.category, value: c.spend,
 itemStyle: { color: palette[i % palette.length] },
 })),
 label: { fontFamily: 'monospace', fontSize: 10 },
 }],
 });
 }
 async function renderSkusChart() {
 if (!skusEl || !detail?.top_skus?.length) return;
 const ec = await loadEcharts();
 if (!ec) return;
 if (!skusChart) skusChart = ec.init(skusEl);
 skusChart.setOption({
 grid: { left: 80, right: 20, top: 10, bottom: 30 },
 xAxis: { type: 'value', axisLabel: { fontFamily: 'monospace', fontSize: 9 } },
 yAxis: {
 type: 'category',
 data: detail.top_skus.map((s: any) => String(s.sku).slice(0, 18)).reverse(),
 axisLabel: { fontFamily: 'monospace', fontSize: 9 },
 },
 tooltip: { trigger: 'axis' },
 series: [{
 type: 'bar', data: [...detail.top_skus.map((s: any) => s.spend)].reverse(),
 itemStyle: { color: '#0078d4' },
 }],
 });
 }
 async function renderMonthlyChart() {
 if (!monthlyEl || !detail?.monthly_spend?.length) return;
 const ec = await loadEcharts();
 if (!ec) return;
 if (!monthlyChart) monthlyChart = ec.init(monthlyEl);
 monthlyChart.setOption({
 grid: { left: 60, right: 20, top: 20, bottom: 30 },
 xAxis: { type: 'category', data: detail.monthly_spend.map((m: any) => m.month),
 axisLabel: { fontFamily: 'monospace', fontSize: 9 } },
 yAxis: { type: 'value', axisLabel: { fontFamily: 'monospace', fontSize: 9 } },
 tooltip: { trigger: 'axis' },
 series: [{
 type: 'line', smooth: true, data: detail.monthly_spend.map((m: any) => m.amount),
 itemStyle: { color: '#007518' }, areaStyle: { color: 'rgba(0,117,24,0.1)' },
 }],
 });
 }
 function renderAllCharts() {
 if (activeTab === 'category') renderCategoryChart();
 if (activeTab === 'skus') renderSkusChart();
 if (activeTab === 'monthly') renderMonthlyChart();
 }

 $effect(() => {
 if (!loading && detail) {
 queueMicrotask(renderAllCharts);
 }
 });

 /* ── format helpers ── */
 function fmtMoney(n: number | null | undefined): string {
 if (n === null || n === undefined) return '—';
 if (Math.abs(n) >= 1_000_000) return '$' + (n / 1_000_000).toFixed(2) + 'M';
 if (Math.abs(n) >= 1_000) return '$' + (n / 1_000).toFixed(1) + 'K';
 return '$' + Number(n).toFixed(2);
 }
 function fmtDate(s: string | null | undefined): string {
 if (!s) return '—';
 const t = String(s);
 return t.length > 10 ? t.slice(0, 10) : t;
 }
 function fmtTs(s: string | null | undefined): string {
 if (!s) return '—';
 const t = String(s).replace('T', ' ');
 return t.length > 19 ? t.slice(0, 19) : t;
 }

 /* ── lifecycle ── */
 onMount(async () => {
 slug = $page.params.slug;
 customerId = $page.params.customer_id;
 if (typeof localStorage !== 'undefined' && !localStorage.getItem('dash_token')) {
 goto('/ui/login');
 return;
 }
 try {
 const r = await fetch('/api/auth/check', { headers: _h() });
 if (!r.ok) { goto('/ui/login'); return; }
 } catch {
 goto('/ui/login');
 return;
 }
 authChecked = true;
 await refreshAll();
 });

 onDestroy(() => {
 if (categoryChart) try { categoryChart.dispose(); } catch {}
 if (skusChart) try { skusChart.dispose(); } catch {}
 if (monthlyChart) try { monthlyChart.dispose(); } catch {}
 });

 function setTab(id: TabId) {
 activeTab = id;
 queueMicrotask(renderAllCharts);
 if (id === 'journey' && !journeyData) loadJourney();
 }

 function back() {
 goto(`/project/${slug}`);
 }
</script>

<svelte:head><title>CUSTOMER · {customerId}</title></svelte:head>

<main class="page">
  <header class="hdr">
    <div class="hdr-l">
      <button class="btn ghost" onclick={back}>&larr; BACK</button>
      <span class="dot"></span>
      <span class="lbl">CUSTOMER</span>
      <span class="cid">{customerId}</span>
      {#if detail?.profile?.name}
        <span class="pname">· {detail.profile.name}</span>
      {/if}
    </div>
    <div class="hdr-r">
      {#if lastFetched}
        <span class="meta">{lastFetched.toLocaleTimeString()}</span>
      {/if}
      <button class="btn" onclick={refreshAll} disabled={loading}>↻ REFRESH</button>
    </div>
  </header>

  {#if errMsg}
    <div class="err">API error: {errMsg}</div>
  {/if}

  {#if loading && !detail}
    <div class="skeletons">
      <div class="sk"></div>
      <div class="sk"></div>
      <div class="sk"></div>
    </div>
  {:else if detail}
    <!-- KPI strip -->
    <section class="kpis">
      <div class="kpi" style:border-color={segmentColor}>
        <div class="kpi-lbl">SEGMENT</div>
        <div class="kpi-val" style:color={segmentColor}>{detail.rfm?.segment || '—'}</div>
        <div class="kpi-sub">
          {#if detail.rfm?.r}R{detail.rfm.r} F{detail.rfm.f} M{detail.rfm.m}{:else}—{/if}
        </div>
      </div>
      <div class="kpi" style:border-color={churnColor}>
        <div class="kpi-lbl">CHURN RISK</div>
        <div class="kpi-val" style:color={churnColor}>{detail.churn?.risk || '—'}</div>
        <div class="kpi-sub">days: {detail.churn?.days_since ?? '—'}</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">CLV</div>
        <div class="kpi-val">{fmtMoney(clv)}</div>
        <div class="kpi-sub">predicted</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">LTV</div>
        <div class="kpi-val">{fmtMoney(lifetimeValue)}</div>
        <div class="kpi-sub">realized</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">ORDERS</div>
        <div class="kpi-val">{orders}</div>
        <div class="kpi-sub">total</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">AOV</div>
        <div class="kpi-val">{fmtMoney(aov)}</div>
        <div class="kpi-sub">avg order</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">TENURE</div>
        <div class="kpi-val">{tenureDays}</div>
        <div class="kpi-sub">days</div>
      </div>
      {#if detail?.subscription_status}
        <div class="kpi">
          <div class="kpi-lbl">SUBSCRIPTION</div>
          <div class="kpi-val">{fmtMoney(detail.subscription_status.current_mrr || 0)}</div>
          <div class="kpi-sub">
            {detail.subscription_status.plan || 'mrr'}
            {#if detail.subscription_status.active_subscriptions}
              · {detail.subscription_status.active_subscriptions} active
            {/if}
          </div>
        </div>
      {/if}
    </section>

    <!-- RFM 5×5 Heatmap -->
    {#if rfmHere.r > 0 && rfmHere.m > 0}
      <section class="rfm-card">
        <div class="rfm-head">
          <span class="rfm-title">RFM POSITION</span>
          <span class="rfm-meta">R{rfmHere.r} F{rfmHere.f} M{rfmHere.m} · {detail.rfm?.segment || ''}</span>
        </div>
        <svg class="rfm-grid" viewBox="0 0 200 160" xmlns="http://www.w3.org/2000/svg">
          <!-- M column headers -->
          {#each [1, 2, 3, 4, 5] as mi}
            <text x={28 + (mi - 1) * 28 + 14} y="12" text-anchor="middle"
                  font-size="8" font-family="monospace" fill="#888">M={mi}</text>
          {/each}
          <!-- R row headers + cells (R=5 at top → R=1 at bottom) -->
          {#each [5, 4, 3, 2, 1] as ri, rowIdx}
            <text x="20" y={32 + rowIdx * 26} text-anchor="end"
                  font-size="8" font-family="monospace" fill="#888">R={ri}</text>
            {#each [1, 2, 3, 4, 5] as mi}
              {@const cx = 28 + (mi - 1) * 28}
              {@const cy = 20 + rowIdx * 26}
              {@const fill = rfmCellColor(ri, mi, rfmHere.f)}
              {@const isHere = ri === rfmHere.r && mi === rfmHere.m}
              <g>
                <rect x={cx} y={cy} width="24" height="22"
                      fill={fill} stroke={isHere ? '#1a1a1a' : '#fff'}
                      stroke-width={isHere ? '2' : '1'}
                      data-f={rfmHere.f}>
                  <title>R={ri} M={mi} (this customer F={rfmHere.f})</title>
                </rect>
                {#if isHere}
                  <text x={cx + 12} y={cy + 16} text-anchor="middle"
                        font-size="14" font-weight="bold" fill="#fff"
                        style="paint-order: stroke; stroke: #1a1a1a; stroke-width: 2px;"><Icon name="star" size={14} /></text>
                {/if}
              </g>
            {/each}
          {/each}
        </svg>
        <div class="rfm-legend">
          <span><b style="color:#1a1a1a"><Icon name="star" size={14} /></b> this customer</span>
          <span><span class="sw" style="background:#007518"></span> Champions</span>
          <span><span class="sw" style="background:#5fb573"></span> Loyal</span>
          <span><span class="sw" style="background:#0078d4"></span> New</span>
          <span><span class="sw" style="background:#ff9d00"></span> Need Attn</span>
          <span><span class="sw" style="background:#be2d06"></span> At Risk</span>
          <span><span class="sw" style="background:#7a1d04"></span> Cannot Lose</span>
          <span><span class="sw" style="background:#888"></span> Hibernating</span>
        </div>
      </section>
    {/if}

    <!-- Profile mini-bar -->
    {#if detail.profile && (detail.profile.tier || detail.profile.joined_at)}
      <div class="profile">
        {#if detail.profile.tier}<span><b>TIER</b> {detail.profile.tier}</span>{/if}
        {#if detail.profile.joined_at}<span><b>JOINED</b> {fmtDate(detail.profile.joined_at)}</span>{/if}
        {#if detail.stats?.first_purchase}<span><b>FIRST</b> {fmtDate(detail.stats.first_purchase)}</span>{/if}
        {#if detail.stats?.last_purchase}<span><b>LAST</b> {fmtDate(detail.stats.last_purchase)}</span>{/if}
        {#if detail.stats?.median_inter_order_gap}<span><b>GAP</b> {detail.stats.median_inter_order_gap}d</span>{/if}
      </div>
    {/if}

    <!-- Tabs -->
    <nav class="tabs">
      {#each tabs as t (t.id)}
        <button class="tab" class:active={activeTab === t.id} onclick={() => setTab(t.id)}>
          {t.label}
        </button>
      {/each}
    </nav>

    <!-- Tab body -->
    <section class="body">
      {#if activeTab === 'timeline'}
        <div class="block">
          <h3>TIMELINE</h3>
          {#if !timeline.length}
            <div class="empty">No events.</div>
          {:else}
            <ul class="tl">
              {#each timeline as ev}
                <li>
                  <span class="tl-ts">{fmtTs(ev.ts)}</span>
                  <span class="tl-kind k-{ev.kind}">{ev.kind.toUpperCase()}</span>
                  <span class="tl-lbl">{ev.label}</span>
                  {#if ev.amount !== null && ev.amount !== undefined}
                    <span class="tl-amt">{fmtMoney(ev.amount)}</span>
                  {/if}
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      {:else if activeTab === 'journey'}
        <div class="block">
          <h3>ATTRIBUTION JOURNEY · 90d</h3>
          <div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;">
            {#each ['linear','time_decay','position','markov'] as m}
              <button
                onclick={() => { journeyModel = m as MtaModel; }}
                style="padding:6px 12px;font-family:monospace;font-size:10px;font-weight:900;cursor:pointer;border:2px solid #1a1a1a;background:{journeyModel === m ? '#1a1a1a' : '#fafaf5'};color:{journeyModel === m ? '#fafaf5' : '#1a1a1a'};text-transform:uppercase;">
                {m}
              </button>
            {/each}
            <button onclick={loadJourney} style="padding:6px 12px;font-family:monospace;font-size:10px;font-weight:900;cursor:pointer;border:2px solid #1a1a1a;background:#fafaf5;color:#1a1a1a;">↻ REFRESH</button>
          </div>
          {#if journeyLoading}
            <div class="empty">Loading…</div>
          {:else if !journeyData || (!journeyData.touchpoints?.length && !journeyData.conversions?.length)}
            <div class="empty">No touchpoints or conversions in last 90 days.</div>
          {:else}
            {@const items = [
              ...(journeyData.touchpoints || []).map((tp: any) => ({ kind: 'tp', ts: tp.event_at, data: tp })),
              ...(journeyData.conversions || []).map((c: any) => ({ kind: 'conv', ts: c.converted_at, data: c })),
            ].sort((a: any, b: any) => String(a.ts).localeCompare(String(b.ts)))}
            <div style="position:relative;padding-left:32px;border-left:3px solid #1a1a1a;">
              {#each items as it}
                {#if it.kind === 'tp'}
                  {@const cred = it.data.credits?.[journeyModel]}
                  <div style="position:relative;margin-bottom:14px;">
                    <span title="{it.data.channel} · {it.data.event_type} · {fmtTs(it.data.event_at)}{cred ? ` · credit=${cred.credit.toFixed(3)} · $${cred.credited_revenue.toFixed(2)}` : ''}" style="position:absolute;left:-41px;top:4px;width:16px;height:16px;border-radius:50%;background:{channelColor(it.data.channel)};border:2px solid #1a1a1a;display:inline-block;"></span>
                    <div style="font-family:monospace;font-size:11px;">
                      <span style="font-weight:900;text-transform:uppercase;color:{channelColor(it.data.channel)};">{it.data.channel}</span>
                      <span style="color:#525252;"> · {it.data.event_type} · {fmtTs(it.data.event_at)}</span>
                      {#if cred}
                        <span style="margin-left:8px;padding:2px 6px;background:#1a1a1a;color:#fafaf5;font-size: 11px;font-weight:900;">CR {(cred.credit*100).toFixed(1)}% · ${cred.credited_revenue.toFixed(0)}</span>
                      {/if}
                    </div>
                  </div>
                {:else}
                  <div style="position:relative;margin-bottom:14px;">
                    <span title="Conversion · ${it.data.revenue ?? 0} · {fmtTs(it.data.converted_at)}" style="position:absolute;left:-44px;top:0;width:22px;height:22px;border-radius: var(--pw-radius-sm);background:#16a34a;border:2px solid #1a1a1a;color:#fafaf5;font-family:monospace;font-size:10px;font-weight:900;display:flex;align-items:center;justify-content:center;">$</span>
                    <div style="font-family:monospace;font-size:11px;">
                      <span style="font-weight:900;color:#16a34a;">CONVERSION</span>
                      <span style="color:#525252;"> · {fmtTs(it.data.converted_at)}</span>
                      <span style="margin-left:8px;font-weight:900;">{fmtMoney(it.data.revenue)}</span>
                    </div>
                  </div>
                {/if}
              {/each}
            </div>
          {/if}
        </div>
      {:else if activeTab === 'history'}
        <div class="block">
          <h3>PURCHASE HISTORY · last {detail.purchase_history?.length || 0}</h3>
          {#if !detail.purchase_history?.length}
            <div class="empty">No history.</div>
          {:else}
            <table class="data">
              <thead>
                <tr><th>DATE</th><th>ORDER ID</th><th>ITEMS</th><th>AMOUNT</th></tr>
              </thead>
              <tbody>
                {#each detail.purchase_history as r}
                  <tr>
                    <td>{fmtTs(r.date)}</td>
                    <td class="mono">{r.order_id || '—'}</td>
                    <td>{r.items ?? '—'}</td>
                    <td class="num">{fmtMoney(r.amount)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </div>
      {:else if activeTab === 'monthly'}
        <div class="block">
          <h3>MONTHLY SPEND · last 12</h3>
          {#if !detail.monthly_spend?.length}
            <div class="empty">No monthly data.</div>
          {:else}
            <div class="chart-wrap" bind:this={monthlyEl}></div>
            <table class="data tight">
              <thead><tr><th>MONTH</th><th>AMOUNT</th><th>ORDERS</th></tr></thead>
              <tbody>
                {#each detail.monthly_spend as m}
                  <tr>
                    <td class="mono">{m.month}</td>
                    <td class="num">{fmtMoney(m.amount)}</td>
                    <td class="num">{m.orders}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </div>
      {:else if activeTab === 'category'}
        <div class="block">
          <h3>CATEGORY MIX</h3>
          {#if !detail.category_mix?.length}
            <div class="empty">No category data.</div>
          {:else}
            <div class="chart-wrap" bind:this={categoryEl}></div>
            <table class="data tight">
              <thead><tr><th>CATEGORY</th><th>SPEND</th><th>SHARE</th></tr></thead>
              <tbody>
                {#each detail.category_mix as c}
                  <tr>
                    <td>{c.category}</td>
                    <td class="num">{fmtMoney(c.spend)}</td>
                    <td class="num">{c.share_pct}%</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </div>
      {:else if activeTab === 'skus'}
        <div class="block">
          <h3>TOP SKUS</h3>
          {#if !detail.top_skus?.length}
            <div class="empty">No SKU data.</div>
          {:else}
            <div class="chart-wrap" bind:this={skusEl}></div>
            <table class="data tight">
              <thead><tr><th>SKU</th><th>QTY</th><th>SPEND</th></tr></thead>
              <tbody>
                {#each detail.top_skus as s}
                  <tr>
                    <td class="mono">{s.sku}</td>
                    <td class="num">{s.qty}</td>
                    <td class="num">{fmtMoney(s.spend)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </div>
      {:else if activeTab === 'recs'}
        <div class="block">
          <h3>NEXT BEST OFFERS</h3>
          {#if !detail.recommendations?.length}
            <div class="empty">No recommendations available.</div>
          {:else}
            <ul class="recs">
              {#each detail.recommendations.slice(0, 5) as r, i}
                <li>
                  <span class="rec-rank">#{i + 1}</span>
                  <span class="rec-sku">{r.sku}</span>
                  <span class="rec-score">score {Number(r.score || 0).toFixed(2)}</span>
                  {#if r.reason}<span class="rec-reason">{r.reason}</span>{/if}
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      {:else if activeTab === 'notes'}
        <div class="block">
          <h3>ADMIN NOTES</h3>
          <div class="note-form">
            <textarea
              bind:value={newNote}
              placeholder="Add a note about this customer..."
              maxlength="2000"
              rows="3"
            ></textarea>
            <button class="btn primary" onclick={saveNote} disabled={savingNote || !newNote.trim()}>
              {savingNote ? '…' : 'SAVE'}
            </button>
          </div>
          {#if !notes.length}
            <div class="empty">No notes yet.</div>
          {:else}
            <ul class="notes">
              {#each notes as n}
                <li>
                  <div class="note-meta">
                    <span class="note-author">{n.author || 'unknown'}</span>
                    <span class="note-ts">{fmtTs(n.created_at)}</span>
                  </div>
                  <div class="note-body">{n.note}</div>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      {/if}
    </section>
  {/if}
</main>

<style>
 :global(body) { background: #fafaf5; }

 .page {
 font-family: 'JetBrains Mono', 'Courier New', monospace;
 font-size: 11px;
 color: #1a1a1a;
 background: #fafaf5;
 min-height: 100vh;
 padding: 12px 16px;
 }

 .hdr {
 display: flex; align-items: center; justify-content: space-between;
 border: 2px solid #1a1a1a; background: #fff;
 padding: 8px 12px; margin-bottom: 10px;
 }
 .hdr-l, .hdr-r { display: flex; align-items: center; gap: 8px; }
 .dot { width: 6px; height: 6px; border-radius: 50%; background: #00fc40; box-shadow: 0 0 4px #00fc40; }
 .lbl { color: #888; font-weight: bold; letter-spacing: 1px; }
 .cid { font-weight: bold; }
 .pname { color: #555; }
 .meta { color: #888; font-size: 10px; }

 .btn {
 font-family: monospace; font-size: 11px;
 background: #fff; color: #1a1a1a; border: 1.5px solid #1a1a1a;
 padding: 4px 10px; cursor: pointer; letter-spacing: 0.5px;
 }
 .btn:hover { background: #f5f5e8; }
 .btn:disabled { opacity: 0.5; cursor: not-allowed; }
 .btn.ghost { border: 1.5px solid #ddd; background: transparent; }
 .btn.ghost:hover { border-color: #1a1a1a; }
 .btn.primary { background: #1a1a1a; color: #fff; }
 .btn.primary:hover { background: #333; }

 .err {
 border: 2px solid #be2d06; background: #fff5f0; color: #be2d06;
 padding: 8px 12px; margin-bottom: 10px; font-weight: bold;
 }

 .skeletons { display: grid; gap: 8px; }
 .sk { height: 60px; background: #f0f0e0; border: 1px solid #ddd; animation: pulse 1.4s infinite; }
 @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }

 .kpis {
 display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
 gap: 8px; margin-bottom: 8px;
 }
 .kpi {
 border: 2px solid #1a1a1a; background: #fff;
 padding: 8px 10px;
 }
 .kpi-lbl { font-size: 11px; color: #888; letter-spacing: 1px; font-weight: bold; }
 .kpi-val { font-size: 13px; font-weight: bold; margin: 2px 0; line-height: 1.1; }
 .kpi-sub { font-size: 10px; color: #555; }

 .rfm-card {
 border: 2px solid #1a1a1a; background: #fff;
 padding: 8px 10px; margin-bottom: 8px;
 }
 .rfm-head {
 display: flex; justify-content: space-between; align-items: baseline;
 margin-bottom: 4px;
 }
 .rfm-title {
 font-size: 10px; color: #888; letter-spacing: 1px; font-weight: bold;
 }
 .rfm-meta { font-size: 10px; color: #555; }
 .rfm-grid { width: 220px; height: 176px; display: block; }
 .rfm-legend {
 display: flex; flex-wrap: wrap; gap: 10px;
 font-size: 11px; color: #555; margin-top: 4px;
 }
 .rfm-legend .sw {
 display: inline-block; width: 9px; height: 9px;
 margin-right: 3px; vertical-align: middle;
 border: 1px solid #ddd;
 }

 .profile {
 border: 1.5px solid #ddd; background: #f5f5e8;
 padding: 6px 10px; margin-bottom: 10px;
 display: flex; flex-wrap: wrap; gap: 14px; font-size: 10px;
 }
 .profile b { color: #888; letter-spacing: 0.5px; margin-right: 4px; }

 .tabs {
 display: flex; gap: 0; border-bottom: 2px solid #1a1a1a; margin-bottom: 0;
 background: #fff;
 }
 .tab {
 font-family: monospace; font-size: 11px;
 background: transparent; color: #888; border: none;
 padding: 8px 12px; cursor: pointer; border-right: 1px solid #ddd;
 letter-spacing: 0.5px;
 }
 .tab:hover { color: #1a1a1a; background: #f5f5e8; }
 .tab.active { color: #1a1a1a; background: #f5f5e8; font-weight: bold; }

 .body {
 border: 2px solid #1a1a1a; border-top: none; background: #fff;
 padding: 12px; min-height: 240px;
 }
 .block h3 {
 margin: 0 0 8px 0; font-size: 11px; color: #888;
 letter-spacing: 1px; font-weight: bold;
 }
 .empty { color: #888; padding: 12px; text-align: center; font-style: italic; }

 /* timeline */
 .tl { list-style: none; margin: 0; padding: 0; }
 .tl li {
 display: grid; grid-template-columns: 140px 80px 1fr 100px;
 gap: 8px; padding: 4px 6px;
 border-bottom: 1px solid #eee; font-size: 11px;
 align-items: center;
 }
 .tl li:hover { background: #fafaf5; }
 .tl-ts { color: #888; font-size: 10px; }
 .tl-kind {
 display: inline-block; padding: 2px 6px; font-size: 11px;
 font-weight: bold; text-align: center; letter-spacing: 0.5px;
 }
 .k-order { background: #007518; color: #fff; }
 .k-return { background: #be2d06; color: #fff; }
 .k-support { background: #ff9d00; color: #fff; }
 .tl-lbl { color: #1a1a1a; }
 .tl-amt { text-align: right; font-variant-numeric: tabular-nums; }

 /* tables */
 table.data {
 width: 100%; border-collapse: collapse; font-size: 11px;
 margin-top: 6px;
 }
 table.data th, table.data td {
 text-align: left; padding: 5px 8px;
 border-bottom: 1px solid #eee;
 }
 table.data th {
 background: #f5f5e8; color: #888; font-weight: bold;
 font-size: 10px; letter-spacing: 0.5px;
 border-bottom: 2px solid #1a1a1a;
 }
 table.data tr:hover td { background: #fafaf5; }
 table.data td.num { text-align: right; font-variant-numeric: tabular-nums; }
 table.data td.mono { font-family: monospace; color: #555; }
 table.data.tight th, table.data.tight td { padding: 3px 6px; }

 .chart-wrap {
 width: 100%; height: 240px;
 border: 1px solid #ddd; background: #fff;
 margin-bottom: 8px;
 }

 /* recs */
 .recs { list-style: none; margin: 0; padding: 0; }
 .recs li {
 display: grid; grid-template-columns: 40px 1fr 110px 1fr;
 gap: 10px; padding: 8px;
 border-bottom: 1px solid #eee; align-items: center;
 }
 .rec-rank { font-weight: bold; color: #888; }
 .rec-sku { font-family: monospace; font-weight: bold; }
 .rec-score { color: #007518; font-size: 10px; }
 .rec-reason { color: #555; font-size: 10px; }

 /* notes */
 .note-form {
 display: flex; gap: 8px; margin-bottom: 12px;
 border: 1.5px solid #ddd; padding: 8px; background: #fafaf5;
 }
 .note-form textarea {
 flex: 1; font-family: monospace; font-size: 11px;
 border: 1px solid #ddd; padding: 6px 8px; resize: vertical;
 background: #fff; color: #1a1a1a;
 }
 .note-form textarea:focus { outline: none; border-color: #1a1a1a; }
 .notes { list-style: none; margin: 0; padding: 0; }
 .notes li {
 border: 1px solid #eee; background: #fafaf5;
 padding: 8px 10px; margin-bottom: 6px;
 }
 .note-meta {
 display: flex; justify-content: space-between;
 font-size: 11px; color: #888; margin-bottom: 4px;
 letter-spacing: 0.5px;
 }
 .note-author { font-weight: bold; }
 .note-body { color: #1a1a1a; white-space: pre-wrap; }
</style>
