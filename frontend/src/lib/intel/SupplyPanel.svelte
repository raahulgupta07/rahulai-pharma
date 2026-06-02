<script lang="ts">
  import { onMount } from 'svelte';
  import { base } from '$app/paths';
  import { dashFetch } from '$lib/api';

  let { slug }: { slug: string } = $props();

  // ---- state ---------------------------------------------------------------
  let scorecard = $state<any>(null);
  let anomalies = $state<any[]>([]);
  let selectedSku = $state<string | null>(null);
  let alts = $state<any>(null);
  let consent = $state<any>({ share_aggregate: false, share_supplier_list: true });
  let consentDirty = $state(false);
  let consentSaving = $state(false);

  let tierFilter = $state<string>('');
  let countryFilter = $state<string>('');
  let selectedSupplier = $state<any>(null);
  let exposure = $state<any>(null);

  let anomalyDrawerEvent = $state<any | null>(null);
  let reportDrawerOpen = $state(false);
  let report = $state<any>(null);
  let reportLoading = $state(false);

  let busy = $state(false);
  let err = $state<string | null>(null);

  const TIERS = ['manufacturer', 'distributor', 'logistics', 'raw_material', 'packaging'];

  // ---- loaders -------------------------------------------------------------
  async function loadScorecard() {
    busy = true; err = null;
    try {
      const r = await dashFetch(`/api/supply/${slug}/scorecard`);
      if (!r.ok) { scorecard = null; return; }
      scorecard = await r.json();
    } catch (e: any) { err = String(e?.message || e); scorecard = null; }
    finally { busy = false; }
  }

  async function loadAnomalies() {
    try {
      const r = await dashFetch(`/api/supply/${slug}/anomalies?z=2.0`);
      if (!r.ok) { anomalies = []; return; }
      const data = await r.json();
      anomalies = (data?.anomalies ?? []).slice(0, 10);
    } catch { anomalies = []; }
  }

  async function loadAlts(sku: string) {
    try {
      const r = await dashFetch(`/api/supply/${slug}/alt-suppliers?sku=${encodeURIComponent(sku)}`);
      if (!r.ok) { alts = null; return; }
      alts = await r.json();
    } catch { alts = null; }
  }

  async function loadConsent() {
    try {
      const r = await dashFetch(`/api/supply/${slug}/consent`);
      if (!r.ok) return;
      const data = await r.json();
      consent = {
        share_aggregate: !!data?.share_aggregate,
        share_supplier_list: data?.share_supplier_list !== false,
      };
      consentDirty = false;
    } catch {}
  }

  async function saveConsent() {
    consentSaving = true;
    try {
      const r = await dashFetch(`/api/supply/${slug}/consent`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(consent),
      });
      if (r.ok) consentDirty = false;
    } catch {} finally { consentSaving = false; }
  }

  async function loadExposure(supplierId: string) {
    try {
      const r = await dashFetch(`/api/supply/${slug}/suppliers/${supplierId}/exposure`);
      if (!r.ok) { exposure = null; return; }
      exposure = await r.json();
    } catch { exposure = null; }
  }

  async function loadReport() {
    reportLoading = true;
    try {
      const r = await dashFetch(`/api/supply/${slug}/report?days=7`);
      if (!r.ok) { report = null; return; }
      report = await r.json();
      reportDrawerOpen = true;
    } catch { report = null; }
    finally { reportLoading = false; }
  }

  function reload() {
    loadScorecard();
    loadAnomalies();
    loadConsent();
  }

  onMount(reload);

  $effect(() => { if (selectedSku) loadAlts(selectedSku); else alts = null; });
  $effect(() => { if (selectedSupplier?.id) loadExposure(selectedSupplier.id); else exposure = null; });

  // ---- derived -------------------------------------------------------------
  const rollup = $derived(scorecard?.rollup ?? { green: 0, yellow: 0, red: 0 });
  const suppliers = $derived<any[]>(scorecard?.suppliers ?? []);
  const filteredSuppliers = $derived<any[]>(
    suppliers.filter((s: any) => {
      if (tierFilter && (s.tier ?? '') !== tierFilter) return false;
      if (countryFilter && (s.country ?? '') !== countryFilter) return false;
      return true;
    })
  );
  const countries = $derived<string[]>(Array.from(new Set(suppliers.map((s: any) => s.country).filter(Boolean))).sort());

  // ---- helpers -------------------------------------------------------------
  function bandColor(band: string) {
    const b = (band || '').toLowerCase();
    if (b === 'green') return '#2c7a3f';
    if (b === 'yellow') return '#c96342';
    if (b === 'red') return '#c0392b';
    return '#6b6557';
  }
  function severityColor(s: string) {
    const v = (s || '').toLowerCase();
    if (v === 'critical') return '#c0392b';
    if (v === 'warn' || v === 'warning') return '#c96342';
    return '#6b6557';
  }
  function fmtMoney(n: any): string {
    if (n == null) return '—';
    const a = Math.abs(n);
    if (a >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
    return '$' + Number(n).toFixed(0);
  }
  function fmtDate(s: any): string {
    if (!s) return '—';
    try { return new Date(s).toLocaleDateString(); } catch { return String(s); }
  }
  function renderMd(md: string): string {
    if (!md) return '';
    // very light renderer: headings + bullets + bold
    return md
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/^### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^## (.+)$/gm, '<h3>$1</h3>')
      .replace(/^# (.+)$/gm, '<h2>$1</h2>')
      .replace(/^\* (.+)$/gm, '<li>$1</li>')
      .replace(/^- (.+)$/gm, '<li>$1</li>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>')
      .replace(/\n\n/g, '<br/><br/>');
  }
</script>

<div class="sp-root">
  <!-- Consent strip -->
  <div class="sp-consent">
    <label>
      <input type="checkbox" bind:checked={consent.share_aggregate} onchange={() => consentDirty = true} />
      Share aggregate risk
    </label>
    <label>
      <input type="checkbox" bind:checked={consent.share_supplier_list} onchange={() => consentDirty = true} />
      Share supplier list
    </label>
    <button class="sp-btn-sm" disabled={!consentDirty || consentSaving} onclick={saveConsent}>
      {consentSaving ? '…' : 'Save'}
    </button>
    <div style="flex:1"></div>
    <button class="sp-btn" disabled={reportLoading} onclick={loadReport}>
      {reportLoading ? '…' : 'Generate Risk Report'}
    </button>
  </div>

  {#if err}<div class="sp-err">{err}</div>{/if}

  <div class="sp-grid">
    <!-- Top-left: Rollup tiles -->
    <section class="sp-card sp-card-rollup">
      <h3>Resilience rollup</h3>
      <div class="sp-tiles">
        <div class="sp-tile sp-tile-green">
          <span>Green</span><b>{rollup.green ?? 0}</b>
        </div>
        <div class="sp-tile sp-tile-yellow">
          <span>Yellow</span><b>{rollup.yellow ?? 0}</b>
        </div>
        <div class="sp-tile sp-tile-red">
          <span>Red</span><b>{rollup.red ?? 0}</b>
        </div>
      </div>
      <div class="sp-muted">{suppliers.length} suppliers tracked</div>
    </section>

    <!-- Center-left: Sankey (SVG 3-column flow) -->
    <section class="sp-card sp-card-sankey">
      <h3>Flow: suppliers → SKUs → tenants</h3>
      {#if suppliers.length}
        {@const cols = (() => {
          const ss = suppliers.slice(0, 8);
          const skus = Array.from(new Set(ss.flatMap((s: any) => (s.skus ?? [])).slice(0, 10))) as string[];
          if (!skus.length) {
            const inferred = ss.flatMap((s: any) => Array.from({ length: Math.min(s.sku_count || 0, 3) }, (_, i) => `${s.name}-sku${i + 1}`));
            return { ss, skus: inferred.slice(0, 12) };
          }
          return { ss, skus };
        })()}
        <svg class="sp-sankey" viewBox="0 0 600 280" preserveAspectRatio="xMidYMid meet">
          {#each cols.ss as s, i}
            {@const y = 30 + i * 30}
            <rect x="10" y={y - 10} width="140" height="22" rx="0" fill={bandColor(s.score_band)} opacity="0.85" />
            <text x="80" y={y + 4} text-anchor="middle" font-size="10" fill="#fff" font-weight="600">
              {(s.name || '').slice(0, 18)}
            </text>
            {#each (s.skus ?? cols.skus.slice(0, 2)) as sku, j}
              {@const sx = 150}
              {@const tx = 300}
              {@const ty = 30 + (cols.skus.indexOf(sku) >= 0 ? cols.skus.indexOf(sku) : j) * 22}
              <path d="M{sx},{y} C{(sx + tx) / 2},{y} {(sx + tx) / 2},{ty} {tx},{ty}"
                    stroke={bandColor(s.score_band)} stroke-width="1.2" fill="none" opacity="0.5" />
            {/each}
          {/each}
          {#each cols.skus as sku, j}
            {@const y = 30 + j * 22}
            <rect x="300" y={y - 8} width="120" height="18" rx="0" fill="#e8e3d6" stroke="#d6cfbe" />
            <text x="360" y={y + 4} text-anchor="middle" font-size="9" fill="#1a1614">
              {String(sku).slice(0, 18)}
            </text>
            <path d="M420,{y} C480,{y} 480,140 540,140" stroke="#c96342" stroke-width="1.2" fill="none" opacity="0.4" />
          {/each}
          <rect x="450" y="130" width="140" height="22" rx="0" fill="#1a1614" />
          <text x="520" y="144" text-anchor="middle" font-size="11" fill="#e8e3d6" font-weight="600">{slug}</text>
        </svg>
      {:else}
        <div class="sp-muted">No supplier flow data yet.</div>
      {/if}
    </section>

    <!-- Right: Anomaly tray -->
    <section class="sp-card sp-card-anomalies">
      <h3>Anomalies <span class="sp-pill-count">{anomalies.length}</span></h3>
      {#if anomalies.length}
        <ul class="sp-anomaly-list">
          {#each anomalies as a}
            <li>
              <button type="button" class="sp-anomaly-row" onclick={() => anomalyDrawerEvent = a}>
                <span class="sp-sev" style="background: {severityColor(a.severity)}"></span>
                <span class="sp-anomaly-title">{a.title || a.event_type || '—'}</span>
                <span class="sp-anomaly-supplier">{a.supplier_name || '—'}</span>
                <span class="sp-anomaly-date">{fmtDate(a.detected_at)}</span>
              </button>
            </li>
          {/each}
        </ul>
      {:else}
        <div class="sp-muted">No anomalies in window.</div>
      {/if}
    </section>

    <!-- Bottom-left: Supplier table -->
    <section class="sp-card sp-card-suppliers">
      <div class="sp-card-head">
        <h3>Suppliers</h3>
        <div class="sp-filters">
          <button class:active={!tierFilter} onclick={() => tierFilter = ''}>all</button>
          {#each TIERS as t}
            <button class:active={tierFilter === t} onclick={() => tierFilter = t}>{t}</button>
          {/each}
          <select bind:value={countryFilter}>
            <option value="">all countries</option>
            {#each countries as c}<option value={c}>{c}</option>{/each}
          </select>
        </div>
      </div>
      {#if filteredSuppliers.length}
        <table class="sp-table">
          <thead>
            <tr><th>Name</th><th>Country</th><th>Tier</th><th>Score</th><th>Band</th><th>SKUs</th><th></th></tr>
          </thead>
          <tbody>
            {#each filteredSuppliers.slice(0, 30) as s}
              <tr>
                <td><b>{s.name}</b></td>
                <td>{s.country || '—'}</td>
                <td>{s.tier || '—'}</td>
                <td class="num">{s.score != null ? Number(s.score).toFixed(1) : '—'}</td>
                <td><span class="sp-band" style="background: {bandColor(s.score_band)}">{s.score_band || '—'}</span></td>
                <td class="num">
                  {#if s.sku_count}
                    <button type="button" class="sp-mini" onclick={() => selectedSku = (s.skus?.[0]) ?? `${s.name}-sku1`}>
                      {s.sku_count}
                    </button>
                  {:else}—{/if}
                </td>
                <td><button class="sp-mini" onclick={() => selectedSupplier = s}>Exposure</button></td>
              </tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <div class="sp-muted">No suppliers match filter.</div>
      {/if}
    </section>

    <!-- Bottom-right: Alt supplier panel -->
    <section class="sp-card sp-card-alts">
      <h3>Alt suppliers {selectedSku ? `· SKU ${selectedSku}` : ''}</h3>
      {#if selectedSku && alts}
        {#if alts.primary}
          <div class="sp-alt-primary">
            <span class="sp-muted">Primary</span>
            <b>{alts.primary.name}</b>
            <span class="sp-band" style="background: {bandColor(alts.primary.score_band)}">{alts.primary.score_band || '—'}</span>
          </div>
        {/if}
        {#if alts.alternatives?.length}
          <table class="sp-table">
            <thead>
              <tr><th>Name</th><th>Band</th><th>Lead Δ</th><th>Switch $</th><th>Unit $</th></tr>
            </thead>
            <tbody>
              {#each alts.alternatives.slice(0, 6) as a}
                <tr>
                  <td><b>{a.name}</b></td>
                  <td><span class="sp-band" style="background: {bandColor(a.score_band)}">{a.score_band || '—'}</span></td>
                  <td class="num">{a.lead_time_delta_days != null ? `${a.lead_time_delta_days > 0 ? '+' : ''}${a.lead_time_delta_days}d` : '—'}</td>
                  <td class="num">{fmtMoney(a.switching_cost_usd)}</td>
                  <td class="num">{fmtMoney(a.unit_cost_usd)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {:else}
          <div class="sp-muted">No alternatives available.</div>
        {/if}
        <button class="sp-mini" onclick={() => selectedSku = null}>Clear</button>
      {:else}
        <div class="sp-muted">Click SKU count in supplier table to view alternatives.</div>
      {/if}
    </section>
  </div>

  <!-- Anomaly drawer -->
  {#if anomalyDrawerEvent}
    <button type="button" class="sp-backdrop" onclick={() => anomalyDrawerEvent = null} aria-label="Close"></button>
    <aside class="sp-drawer" role="dialog">
      <div class="sp-drawer-head">
        <h4>{anomalyDrawerEvent.title || anomalyDrawerEvent.event_type}</h4>
        <button class="sp-mini" onclick={() => anomalyDrawerEvent = null}>×</button>
      </div>
      <div class="sp-drawer-body">
        <div><b>Supplier:</b> {anomalyDrawerEvent.supplier_name || '—'}</div>
        <div><b>Type:</b> {anomalyDrawerEvent.event_type || '—'}</div>
        <div><b>Severity:</b> <span style="color: {severityColor(anomalyDrawerEvent.severity)}; font-weight:600">{anomalyDrawerEvent.severity || '—'}</span></div>
        <div><b>Detected:</b> {fmtDate(anomalyDrawerEvent.detected_at)}</div>
        {#if anomalyDrawerEvent.description}
          <p>{anomalyDrawerEvent.description}</p>
        {/if}
        <pre>{JSON.stringify(anomalyDrawerEvent, null, 2)}</pre>
      </div>
    </aside>
  {/if}

  <!-- Exposure drawer -->
  {#if selectedSupplier}
    <button type="button" class="sp-backdrop" onclick={() => selectedSupplier = null} aria-label="Close"></button>
    <aside class="sp-drawer" role="dialog">
      <div class="sp-drawer-head">
        <h4>Exposure · {selectedSupplier.name}</h4>
        <button class="sp-mini" onclick={() => selectedSupplier = null}>×</button>
      </div>
      <div class="sp-drawer-body">
        {#if exposure}
          <div><b>Revenue at risk:</b> {fmtMoney(exposure.revenue_at_risk_usd)}</div>
          <div><b>Consent:</b> {exposure.consent_status || '—'}</div>
          <div><b>Tenants:</b></div>
          <ul>
            {#each (exposure.tenants ?? []) as t}<li>{t}</li>{/each}
          </ul>
        {:else}
          <div class="sp-muted">Loading exposure…</div>
        {/if}
      </div>
    </aside>
  {/if}

  <!-- Report drawer -->
  {#if reportDrawerOpen}
    <button type="button" class="sp-backdrop" onclick={() => reportDrawerOpen = false} aria-label="Close"></button>
    <aside class="sp-drawer sp-drawer-wide" role="dialog">
      <div class="sp-drawer-head">
        <h4>Risk report · last 7 days</h4>
        <div style="flex:1"></div>
        <a class="sp-mini" href={`/api/venture/${slug}/deals/_supply/memo.pdf`} target="_blank" rel="noopener">Download PDF</a>
        <button class="sp-mini" onclick={() => reportDrawerOpen = false}>×</button>
      </div>
      <div class="sp-drawer-body">
        {#if report?.summary_md}
          <div class="sp-md">{@html renderMd(report.summary_md)}</div>
        {:else if report}
          <pre>{JSON.stringify(report, null, 2)}</pre>
        {:else}
          <div class="sp-muted">Loading report…</div>
        {/if}
      </div>
    </aside>
  {/if}
</div>

<style>
  .sp-root { padding: 16px; font-size: 13px; color: #2c2a26; }
  .sp-consent {
    display: flex; gap: 14px; align-items: center; padding: 8px 10px;
    background: #f7f6f3; border: 1px solid #e8e3d6; margin-bottom: 12px;
    font-size: 12px;
  }
  .sp-consent label { display: flex; gap: 6px; align-items: center; cursor: pointer; }
  .sp-err { color: #c0392b; padding: 6px 10px; background: rgba(192,57,43,0.06); margin-bottom: 8px; }
  .sp-grid {
    display: grid; gap: 12px;
    grid-template-columns: 1fr 1fr 1fr;
    grid-template-rows: auto auto;
  }
  .sp-card {
    background: #fff; border: 1px solid #e8e3d6; padding: 12px;
    display: flex; flex-direction: column; gap: 8px; min-width: 0;
  }
  .sp-card h3 {
    margin: 0; font-size: 13px; font-weight: 600; color: #1a1614;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .sp-card-rollup { grid-column: 1; grid-row: 1; }
  .sp-card-sankey { grid-column: 2; grid-row: 1; }
  .sp-card-anomalies { grid-column: 3; grid-row: 1 / span 2; }
  .sp-card-suppliers { grid-column: 1 / span 2; grid-row: 2; }
  .sp-card-alts { grid-column: 1 / span 2; grid-row: 3; }
  .sp-card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
  .sp-tiles { display: flex; gap: 10px; }
  .sp-tile { flex: 1; padding: 12px; display: flex; flex-direction: column; gap: 4px; border: 1px solid #e8e3d6; }
  .sp-tile span { font-size: 10.5px; text-transform: uppercase; color: #6b6557; letter-spacing: 0.04em; }
  .sp-tile b { font-size: 28px; color: #1a1614; }
  .sp-tile-green { border-left: 3px solid #2c7a3f; }
  .sp-tile-yellow { border-left: 3px solid #c96342; }
  .sp-tile-red { border-left: 3px solid #c0392b; }
  .sp-muted { color: #9a9080; font-size: 12px; }
  .sp-sankey { width: 100%; height: auto; max-height: 280px; background: #f7f6f3; border: 1px solid #e8e3d6; }
  .sp-anomaly-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 2px; max-height: 480px; overflow-y: auto; }
  .sp-anomaly-row {
    width: 100%; display: grid; grid-template-columns: 10px 1fr auto;
    gap: 6px; padding: 6px 4px; text-align: left;
    background: transparent; border: 0; border-bottom: 1px dotted #e8e3d6;
    cursor: pointer; font-size: 11.5px; align-items: center;
  }
  .sp-anomaly-row:hover { background: #f7f6f3; }
  .sp-sev { width: 8px; height: 8px; border-radius: 50%; }
  .sp-anomaly-title { color: #1a1614; font-weight: 500; }
  .sp-anomaly-supplier { font-size: 10.5px; color: #6b6557; grid-column: 2; }
  .sp-anomaly-date { font-size: 10.5px; color: #9a9080; }
  .sp-pill-count { background: #c96342; color: #fff; padding: 1px 6px; font-size: 10px; margin-left: 6px; font-weight: 600; }
  .sp-filters { display: flex; gap: 4px; flex-wrap: wrap; }
  .sp-filters button {
    background: transparent; border: 1px solid #d6cfbe; color: #6b6557;
    padding: 3px 8px; font-size: 10.5px; cursor: pointer;
  }
  .sp-filters button.active { background: #c96342; color: #fff; border-color: #c96342; }
  .sp-filters select { padding: 3px 6px; border: 1px solid #d6cfbe; background: #fff; font-size: 11px; }
  .sp-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .sp-table th, .sp-table td { padding: 6px 8px; text-align: left; border-bottom: 1px solid #f0ebde; }
  .sp-table th { font-size: 10.5px; text-transform: uppercase; color: #6b6557; letter-spacing: 0.05em; background: #f7f6f3; }
  .sp-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .sp-band { display: inline-block; padding: 1px 6px; font-size: 9.5px; font-weight: 600; text-transform: uppercase; color: #fff; min-width: 48px; text-align: center; }
  .sp-alt-primary { display: flex; gap: 10px; align-items: center; padding: 6px 8px; background: #f7f6f3; border: 1px solid #e8e3d6; margin-bottom: 6px; }
  .sp-btn { background: #c96342; color: #fff; border: 0; padding: 7px 14px; font-size: 12px; font-weight: 600; cursor: pointer; text-transform: uppercase; letter-spacing: 0.04em; }
  .sp-btn:hover:not(:disabled) { background: #b35636; }
  .sp-btn:disabled { background: #d6cfbe; cursor: not-allowed; }
  .sp-btn-sm { background: #c96342; color: #fff; border: 0; padding: 4px 10px; font-size: 11px; font-weight: 600; cursor: pointer; }
  .sp-btn-sm:disabled { background: #d6cfbe; cursor: not-allowed; }
  .sp-mini { background: transparent; border: 1px solid #d6cfbe; color: #1a1614; padding: 3px 8px; font-size: 10.5px; font-weight: 600; cursor: pointer; text-decoration: none; display: inline-block; }
  .sp-mini:hover { background: #c96342; color: #fff; border-color: #c96342; }

  .sp-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 8500; border: 0; }
  .sp-drawer {
    position: fixed; top: 56px; right: 0; bottom: 0; width: 420px;
    background: #fff; border-left: 1px solid #d6cfbe; z-index: 8600;
    display: flex; flex-direction: column;
  }
  .sp-drawer-wide { width: 640px; max-width: 90vw; }
  .sp-drawer-head { display: flex; gap: 8px; align-items: center; padding: 10px 12px; border-bottom: 1px solid #e8e3d6; background: #f7f6f3; }
  .sp-drawer-head h4 { margin: 0; font-size: 13px; font-weight: 600; flex: 1; }
  .sp-drawer-body { padding: 12px; overflow-y: auto; flex: 1; font-size: 12.5px; }
  .sp-drawer-body pre { background: #f7f6f3; padding: 8px; font-size: 11px; overflow-x: auto; }
  .sp-md :global(h2) { font-size: 16px; margin: 12px 0 6px; }
  .sp-md :global(h3) { font-size: 14px; margin: 10px 0 4px; color: #c96342; }
  .sp-md :global(h4) { font-size: 12.5px; margin: 8px 0 4px; }
  .sp-md :global(ul) { padding-left: 18px; }

  @media (max-width: 900px) {
    .sp-grid { grid-template-columns: 1fr; }
    .sp-card-rollup, .sp-card-sankey, .sp-card-anomalies, .sp-card-suppliers, .sp-card-alts {
      grid-column: 1; grid-row: auto;
    }
  }
</style>
