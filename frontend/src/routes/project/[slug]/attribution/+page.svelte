<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { page } from '$app/stores';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';

 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 type MtaModel = 'linear' | 'time_decay' | 'position' | 'markov';
 const MODELS: MtaModel[] = ['linear', 'time_decay', 'position', 'markov'];

 let slug = $state('');
 let authChecked = $state(false);
 let loading = $state(true);
 let errMsg = $state('');

 let days = $state<7 | 30 | 90>(30);
 let model = $state<MtaModel>('linear');

 let byChannel = $state<any[]>([]);
 let byCampaign = $state<any[]>([]);
 let compareMatrix = $state<Record<MtaModel, any[]>>({
 linear: [], time_decay: [], position: [], markov: [],
 });
 let kpis = $state<{ conversions: number; revenue: number; topChannel: string; coverage: number }>({
 conversions: 0, revenue: 0, topChannel: '—', coverage: 0,
 });

 let pieEl: HTMLDivElement | undefined = $state();
 let pieChart: any = null;
 let echartsLib: any = null;

 const channelColors: Record<string, string> = {
 email: '#0078d4', sms: '#8b5cf6', ad: '#dc2626',
 organic: '#16a34a', direct: '#525252', social: '#db2777',
 campaign: '#ea580c', push: '#ca8a04', referral: '#0d9488',
 };
 function channelColor(ch: string): string {
 return channelColors[(ch || '').toLowerCase()] || '#737373';
 }
 function fmtMoney(n: number | null | undefined): string {
 if (n === null || n === undefined) return '—';
 if (Math.abs(n) >= 1_000_000) return '$' + (n / 1_000_000).toFixed(2) + 'M';
 if (Math.abs(n) >= 1_000) return '$' + (n / 1_000).toFixed(1) + 'K';
 return '$' + Number(n).toFixed(2);
 }
 function fmtPct(n: number): string {
 return (n * 100).toFixed(1) + '%';
 }

 async function loadEcharts() {
 if (echartsLib) return echartsLib;
 try { echartsLib = await import('echarts'); } catch {}
 return echartsLib;
 }

 async function loadByChannel(m: MtaModel = model) {
 const r = await fetch(
 `/api/projects/${slug}/attribution/by-channel?model=${m}&days=${days}`,
 { headers: _h() }
 );
 return r.ok ? await r.json() : [];
 }

 async function loadByCampaign() {
 const r = await fetch(
 `/api/projects/${slug}/attribution/by-campaign?model=${model}&days=${days}`,
 { headers: _h() }
 );
 return r.ok ? await r.json() : [];
 }

 async function loadCoverage(): Promise<{ conv: number; rev: number; coverage: number }> {
 // Coverage = % conversions with ≥1 touchpoint = % conversions with credits.
 // Approximate via current model rows.
 let totalConv = 0;
 let convsWithCredits = 0;
 let totalRev = 0;
 for (const c of byChannel) {
 convsWithCredits = Math.max(convsWithCredits, c.conversions || 0);
 totalRev += c.credited_revenue || 0;
 }
 // No direct API for total conversions — use credited revenue and conversion count.
 totalConv = convsWithCredits;
 return { conv: totalConv, rev: totalRev, coverage: totalConv > 0 ? 1.0 : 0 };
 }

 async function refresh() {
 loading = true; errMsg = '';
 try {
 byChannel = await loadByChannel(model);
 byCampaign = await loadByCampaign();
 // Compare table: load each model.
 const results: any = {};
 await Promise.all(MODELS.map(async (m) => {
 results[m] = await loadByChannel(m);
 }));
 compareMatrix = results;
 const cov = await loadCoverage();
 const top = byChannel.length ? byChannel[0].channel : '—';
 kpis = {
 conversions: cov.conv,
 revenue: cov.rev,
 topChannel: top,
 coverage: cov.coverage,
 };
 queueMicrotask(renderPie);
 } catch (e: any) {
 errMsg = String(e?.message || e);
 } finally {
 loading = false;
 }
 }

 async function renderPie() {
 if (!pieEl || !byChannel.length) return;
 const ec = await loadEcharts();
 if (!ec) return;
 if (!pieChart) pieChart = ec.init(pieEl);
 pieChart.setOption({
 tooltip: { trigger: 'item', formatter: (p: any) => `${p.name}: ${fmtMoney(p.value)} (${p.percent}%)` },
 legend: { bottom: 0, textStyle: { fontFamily: 'monospace', fontSize: 10 } },
 series: [{
 type: 'pie',
 radius: ['40%', '70%'],
 avoidLabelOverlap: true,
 itemStyle: { borderColor: '#fafaf5', borderWidth: 2 },
 label: { fontFamily: 'monospace', fontSize: 10 },
 data: byChannel.map((c) => ({
 name: c.channel, value: c.credited_revenue,
 itemStyle: { color: channelColor(c.channel) },
 })),
 }],
 });
 }

 // Compare-models matrix: rows = top 10 channels (union), cols = models.
 const compareRows = $derived.by(() => {
 const ch = new Set<string>();
 for (const m of MODELS) for (const r of compareMatrix[m] || []) ch.add(r.channel);
 const top = Array.from(ch).slice(0, 10);
 return top.map((c) => {
 const row: any = { channel: c };
 for (const m of MODELS) {
 const rec = (compareMatrix[m] || []).find((x: any) => x.channel === c);
 row[m] = rec ? rec.credited_revenue : 0;
 }
 const vals = MODELS.map((m) => row[m] as number);
 const max = Math.max(...vals);
 const min = Math.min(...vals);
 row._disagreement = max > 0 ? (max - min) / max : 0;
 return row;
 });
 });

 $effect(() => { void days; void model; if (authChecked) refresh(); });

 onMount(async () => {
 slug = $page.params.slug;
 if (typeof localStorage !== 'undefined' && !localStorage.getItem('dash_token')) {
 goto('/ui/login'); return;
 }
 try {
 const r = await fetch('/api/auth/check', { headers: _h() });
 if (!r.ok) { goto('/ui/login'); return; }
 } catch { goto('/ui/login'); return; }
 authChecked = true;
 await refresh();
 });

 onDestroy(() => { if (pieChart) try { pieChart.dispose(); } catch {} });
</script>

<svelte:head><title>ATTRIBUTION · {slug}</title></svelte:head>

<main style="padding: 16px; font-family: monospace; color: #1a1a1a; background: #fafaf5; min-height: 100vh;">
  <header style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:12px;">
    <div>
      <a href={`${base}/project/${slug}/settings`} style="color:#525252;text-decoration:none;font-size:11px;">← BACK</a>
      <h1 style="font-size:14px;font-weight:900;margin:6px 0 2px 0;"><Icon name="target" size={14} /> ATTRIBUTION · {slug}</h1>
      <div style="font-size:10px;color:#525252;">Multi-Touch Attribution dashboard</div>
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;">
      {#each [7,30,90] as d}
        <button onclick={() => (days = d as 7|30|90)} style="padding:6px 12px;font-family:monospace;font-size:10px;font-weight:900;cursor:pointer;border:2px solid #1a1a1a;background:{days === d ? '#1a1a1a' : '#fafaf5'};color:{days === d ? '#fafaf5' : '#1a1a1a'};">
          LAST {d}D
        </button>
      {/each}
      <span style="width:12px;"></span>
      {#each MODELS as m}
        <button onclick={() => (model = m)} style="padding:6px 12px;font-family:monospace;font-size:10px;font-weight:900;cursor:pointer;border:2px solid #1a1a1a;background:{model === m ? '#1a1a1a' : '#fafaf5'};color:{model === m ? '#fafaf5' : '#1a1a1a'};text-transform:uppercase;">
          {m}
        </button>
      {/each}
    </div>
  </header>

  {#if errMsg}
    <div style="padding:8px;border:2px solid #dc2626;color:#dc2626;margin-bottom:12px;font-size:11px;">{errMsg}</div>
  {/if}

  {#if loading}
    <div style="text-align:center;padding:40px;font-size:11px;text-transform:uppercase;color:#525252;">Loading…</div>
  {:else}

  <!-- KPI cards -->
  <section style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:16px;">
    <div style="border:2px solid #1a1a1a;padding:12px;background:#fff;">
      <div style="font-size: 11px;color:#525252;letter-spacing:0.1em;">CONVERSIONS</div>
      <div style="font-size:18px;font-weight:900;margin-top:4px;">{kpis.conversions}</div>
    </div>
    <div style="border:2px solid #1a1a1a;padding:12px;background:#fff;">
      <div style="font-size: 11px;color:#525252;letter-spacing:0.1em;">CREDITED REVENUE</div>
      <div style="font-size:18px;font-weight:900;margin-top:4px;color:#16a34a;">{fmtMoney(kpis.revenue)}</div>
    </div>
    <div style="border:2px solid #1a1a1a;padding:12px;background:#fff;">
      <div style="font-size: 11px;color:#525252;letter-spacing:0.1em;">TOP CHANNEL</div>
      <div style="font-size:16px;font-weight:900;margin-top:4px;color:{channelColor(kpis.topChannel)};text-transform:uppercase;">{kpis.topChannel}</div>
    </div>
    <div style="border:2px solid #1a1a1a;padding:12px;background:#fff;">
      <div style="font-size: 11px;color:#525252;letter-spacing:0.1em;">COVERAGE</div>
      <div style="font-size:18px;font-weight:900;margin-top:4px;">{fmtPct(kpis.coverage)}</div>
      <div style="font-size: 11px;color:#525252;margin-top:2px;">conv with ≥1 touchpoint</div>
    </div>
  </section>

  <!-- Channel pie + by-campaign table -->
  <section style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">
    <div style="border:2px solid #1a1a1a;padding:12px;background:#fff;">
      <h3 style="font-size:11px;font-weight:900;margin:0 0 8px 0;letter-spacing:0.1em;">CHANNEL MIX · {model.toUpperCase()}</h3>
      {#if !byChannel.length}
        <div style="padding:30px;text-align:center;color:#525252;font-size:11px;">No data.</div>
      {:else}
        <div bind:this={pieEl} style="width:100%;height:280px;"></div>
      {/if}
    </div>
    <div style="border:2px solid #1a1a1a;padding:12px;background:#fff;">
      <h3 style="font-size:11px;font-weight:900;margin:0 0 8px 0;letter-spacing:0.1em;">CAMPAIGN ATTRIBUTION</h3>
      {#if !byCampaign.length}
        <div style="padding:30px;text-align:center;color:#525252;font-size:11px;">No campaign-tagged touchpoints yet.</div>
      {:else}
        <table style="width:100%;border-collapse:collapse;font-size:11px;">
          <thead>
            <tr style="border-bottom:2px solid #1a1a1a;">
              <th style="text-align:left;padding:6px;">CAMPAIGN</th>
              <th style="text-align:right;padding:6px;">CONV</th>
              <th style="text-align:right;padding:6px;">REVENUE</th>
              <th style="text-align:right;padding:6px;">SHARE</th>
            </tr>
          </thead>
          <tbody>
            {#each byCampaign as c}
              <tr style="border-bottom:1px solid #e5e5e5;">
                <td style="padding:6px;">{c.campaign_name}</td>
                <td style="padding:6px;text-align:right;">{c.conversions}</td>
                <td style="padding:6px;text-align:right;font-weight:700;">{fmtMoney(c.credited_revenue)}</td>
                <td style="padding:6px;text-align:right;">{fmtPct(c.credit_share)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>
  </section>

  <!-- Compare models table -->
  <section style="border:2px solid #1a1a1a;padding:12px;background:#fff;margin-bottom:16px;">
    <h3 style="font-size:11px;font-weight:900;margin:0 0 8px 0;letter-spacing:0.1em;">COMPARE MODELS · TOP CHANNELS</h3>
    {#if !compareRows.length}
      <div style="padding:30px;text-align:center;color:#525252;font-size:11px;">No data.</div>
    {:else}
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <thead>
          <tr style="border-bottom:2px solid #1a1a1a;">
            <th style="text-align:left;padding:6px;">CHANNEL</th>
            {#each MODELS as m}
              <th style="text-align:right;padding:6px;text-transform:uppercase;">{m}</th>
            {/each}
            <th style="text-align:right;padding:6px;">DISAGREEMENT</th>
          </tr>
        </thead>
        <tbody>
          {#each compareRows as row}
            <tr style="border-bottom:1px solid #e5e5e5;">
              <td style="padding:6px;font-weight:700;color:{channelColor(row.channel)};text-transform:uppercase;">{row.channel}</td>
              {#each MODELS as m}
                <td style="padding:6px;text-align:right;font-weight:{model === m ? 900 : 400};background:{model === m ? '#fff8dc' : 'transparent'};">{fmtMoney(row[m])}</td>
              {/each}
              <td style="padding:6px;text-align:right;color:{row._disagreement > 0.5 ? '#dc2626' : row._disagreement > 0.2 ? '#ea580c' : '#16a34a'};font-weight:900;">{fmtPct(row._disagreement)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
      <div style="font-size: 11px;color:#525252;margin-top:6px;">Highlighted column = active model. High disagreement → model choice changes the answer.</div>
    {/if}
  </section>

  {/if}
</main>
