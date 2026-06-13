<script lang="ts">
  import { onMount } from 'svelte';
  import { base } from '$app/paths';
  import { dashFetch } from '$lib/api';
  import OpsPanel from './OpsPanel.svelte';
  import MarketPanel from './MarketPanel.svelte';
  import SupplyPanel from './SupplyPanel.svelte';

  let { slug }: { slug: string } = $props();

  type Tab = 'pipeline' | 'model' | 'sensitivity' | 'unit_econ' | 'jv' | 'ops' | 'market' | 'supply';
  let tab = $state<Tab>('pipeline');

  // ---- pipeline -------------------------------------------------------
  let deals = $state<any[]>([]);
  let loadingDeals = $state(false);
  let dealErr = $state('');

  // new-deal form
  let newName = $state('');
  let newStage = $state('series_a');
  let newSector = $state('');
  let newGeo = $state('myanmar');
  let newAsk = $state(0);
  let creating = $state(false);

  async function loadDeals() {
    loadingDeals = true; dealErr = '';
    try {
      const r = await dashFetch(`/api/venture/${slug}/deals`);
      if (!r.ok) { dealErr = `failed: ${r.status}`; return; }
      const data = await r.json();
      deals = data?.deals ?? [];
    } catch (e: any) {
      dealErr = String(e?.message || e);
    } finally {
      loadingDeals = false;
    }
  }

  async function createDeal() {
    if (!newName.trim() || creating) return;
    creating = true;
    try {
      const r = await dashFetch(`/api/venture/${slug}/deals`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          name: newName.trim(), stage: newStage, sector: newSector,
          geography: newGeo, ask_amount: newAsk
        })
      });
      if (r.ok) {
        newName = ''; newAsk = 0; newSector = '';
        await loadDeals();
      }
    } catch {} finally { creating = false; }
  }

  // ---- DCF model -------------------------------------------------------
  let cfText = $state('-2000000, 600000, 800000, 1000000, 1200000, 1400000');
  let wacc = $state(0.12);
  let tg = $state(0.03);
  let modelResult = $state<any | null>(null);
  let modelRunning = $state(false);
  let modelErr = $state('');

  function parseCF(s: string): number[] {
    return s.split(',').map(x => parseFloat(x.trim())).filter(x => !isNaN(x));
  }

  async function runModel() {
    modelRunning = true; modelErr = ''; modelResult = null;
    try {
      const cf = parseCF(cfText);
      const [dr, ir] = await Promise.all([
        dashFetch(`/api/venture/${slug}/dcf`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ cashflows: cf, wacc, terminal_growth: tg })
        }),
        dashFetch(`/api/venture/${slug}/irr`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ cashflows: cf, wacc, terminal_growth: tg })
        })
      ]);
      if (!dr.ok || !ir.ok) { modelErr = `model failed: ${dr.status}/${ir.status}`; return; }
      const dcfR = await dr.json();
      const irrR = await ir.json();
      modelResult = { ...dcfR, ...irrR };
    } catch (e: any) {
      modelErr = String(e?.message || e);
    } finally { modelRunning = false; }
  }

  function verdictOf(r: any): string {
    if (!r) return '—';
    const irr = r.irr ?? 0;
    const moic = r.moic ?? 0;
    if (irr >= 0.25 && moic >= 3.0) return 'GO';
    if (irr >= 0.15) return 'HOLD';
    return 'PASS';
  }

  // ---- scenario save --------------------------------------------------
  let selectedDealId = $state('');
  let savedFlash = $state('');
  let savingScenario = $state(false);

  async function saveScenario(name: string) {
    if (!selectedDealId || !modelResult || savingScenario) return;
    savingScenario = true;
    try {
      const r = await dashFetch(`/api/venture/${slug}/scenarios`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          deal_id: selectedDealId,
          name,
          inputs: { wacc, terminal_growth: tg, cashflows: parseCF(cfText) },
          results: modelResult,
          verdict: verdictOf(modelResult).toLowerCase()
        })
      });
      if (r.ok) {
        savedFlash = `Saved as ${name}`;
        setTimeout(() => { savedFlash = ''; }, 2000);
        await loadDeals();
      }
    } catch {} finally { savingScenario = false; }
  }

  // ---- sensitivity -----------------------------------------------------
  let sensWAcc = $state('0.08, 0.10, 0.12, 0.14, 0.16');
  let sensG = $state('0.01, 0.02, 0.03, 0.04, 0.05');
  let sensResult = $state<any | null>(null);
  let sensRunning = $state(false);

  async function runSens() {
    sensRunning = true; sensResult = null;
    try {
      const cf = parseCF(cfText);
      const r = await dashFetch(`/api/venture/${slug}/sensitivity`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          base_cashflows: cf,
          wacc_range: parseCF(sensWAcc),
          growth_range: parseCF(sensG)
        })
      });
      if (r.ok) sensResult = await r.json();
    } catch {} finally { sensRunning = false; }
  }

  // ---- unit economics --------------------------------------------------
  let cac = $state(100);
  let ltv = $state(450);
  let gm = $state(0.7);
  let ueResult = $state<any | null>(null);

  async function runUE() {
    try {
      const r = await dashFetch(`/api/venture/${slug}/unit-economics`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ cac, ltv, gross_margin: gm })
      });
      if (r.ok) ueResult = await r.json();
    } catch {}
  }

  // ---- partner fit -----------------------------------------------------
  let selfCaps = $state('retail, logistics, supply chain');
  let partnerCaps = $state('fintech, distribution, last mile');
  let fitResult = $state<any | null>(null);

  async function runFit() {
    try {
      const r = await dashFetch(`/api/venture/${slug}/partner-fit`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          self_caps: selfCaps.split(',').map(s => s.trim()).filter(Boolean),
          partner_caps: partnerCaps.split(',').map(s => s.trim()).filter(Boolean)
        })
      });
      if (r.ok) fitResult = await r.json();
    } catch {}
  }

  async function exportMemo(dealId: string, fmt: 'pdf' | 'pptx') {
    try {
      const url = `/api/venture/${slug}/deals/${dealId}/memo.${fmt}`;
      const r = await dashFetch(url);
      if (!r.ok) {
        const txt = await r.text().catch(() => '');
        alert(`Export failed (${r.status}): ${txt.slice(0, 200)}`);
        return;
      }
      const blob = await r.blob();
      const dlUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = dlUrl;
      a.download = `ic_memo_${dealId.slice(0, 8)}.${fmt}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(dlUrl), 1000);
    } catch (e: any) {
      alert(`Export error: ${e?.message || e}`);
    }
  }

  function fmtMoney(n: number): string {
    if (n == null) return '—';
    const a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toFixed(0);
  }
  function fmtPct(n: number): string {
    if (n == null) return '—';
    return (n * 100).toFixed(1) + '%';
  }

  onMount(loadDeals);
</script>

<div class="vp-root">
  <div class="vp-head">
    <h2>VentureDesk</h2>
    <div class="vp-tabs">
      <button class:active={tab==='pipeline'} onclick={() => tab='pipeline'}>Pipeline</button>
      <button class:active={tab==='model'} onclick={() => tab='model'}>DCF / IRR</button>
      <button class:active={tab==='sensitivity'} onclick={() => tab='sensitivity'}>Sensitivity</button>
      <button class:active={tab==='unit_econ'} onclick={() => tab='unit_econ'}>Unit Econ</button>
      <button class:active={tab==='jv'} onclick={() => tab='jv'}>JV / Partners</button>
      <button class:active={tab==='ops'} onclick={() => tab='ops'}>Ops</button>
      <button class:active={tab==='market'} onclick={() => tab='market'}>Market</button>
      <button class:active={tab==='supply'} onclick={() => tab='supply'}>Supply</button>
    </div>
  </div>

  {#if tab === 'supply'}
    <SupplyPanel {slug} />
  {/if}

  {#if tab === 'ops'}
    <OpsPanel {slug} />
  {/if}

  {#if tab === 'market'}
    <MarketPanel {slug} />
  {/if}

  {#if tab === 'pipeline'}
    <section class="vp-section">
      <h3>Deal pipeline</h3>
      {#if dealErr}<div class="vp-err">{dealErr}</div>{/if}

      <div class="vp-newdeal">
        <input placeholder="Deal name (e.g. EV Charging Yangon)" bind:value={newName} />
        <select bind:value={newStage}>
          <option value="seed">Seed</option>
          <option value="series_a">Series A</option>
          <option value="series_b">Series B</option>
          <option value="late">Late</option>
        </select>
        <input placeholder="Sector" bind:value={newSector} style="max-width:140px" />
        <input placeholder="Geo" bind:value={newGeo} style="max-width:120px" />
        <input type="number" placeholder="Ask" bind:value={newAsk} style="max-width:120px" />
        <button class="vp-btn" disabled={creating || !newName.trim()} onclick={createDeal}>
          {creating ? '…' : '+ Add'}
        </button>
      </div>

      {#if loadingDeals}
        <div class="vp-muted">Loading…</div>
      {:else if deals.length === 0}
        <div class="vp-muted">No deals yet. Add one above.</div>
      {:else}
        <table class="vp-table">
          <thead>
            <tr><th>Name</th><th>Stage</th><th>Sector</th><th>Geo</th><th>Ask</th><th>Status</th><th>IC Memo</th></tr>
          </thead>
          <tbody>
            {#each deals as d}
              <tr>
                <td><b>{d.name}</b></td>
                <td>{d.stage || '—'}</td>
                <td>{d.sector || '—'}</td>
                <td>{d.geography || '—'}</td>
                <td>{fmtMoney(d.ask_amount)}</td>
                <td><span class="vp-pill">{d.status}</span></td>
                <td class="vp-export">
                  <button class="vp-mini" title="PDF memo" onclick={() => exportMemo(d.id, 'pdf')}>PDF</button>
                  <button class="vp-mini" title="PPTX deck" onclick={() => exportMemo(d.id, 'pptx')}>PPTX</button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
  {/if}

  {#if tab === 'model'}
    <section class="vp-section">
      <h3>DCF + IRR / MOIC</h3>
      <label>Cashflows (year 0 first, comma-separated)
        <input type="text" bind:value={cfText} />
      </label>
      <div class="vp-row">
        <label>WACC <input type="number" step="0.01" bind:value={wacc} /></label>
        <label>Terminal growth <input type="number" step="0.01" bind:value={tg} /></label>
        <button class="vp-btn" disabled={modelRunning} onclick={runModel}>
          {modelRunning ? 'Computing…' : 'Run model'}
        </button>
      </div>
      {#if modelErr}<div class="vp-err">{modelErr}</div>{/if}
      {#if modelResult}
        <div class="vp-kpis">
          <div class="vp-kpi"><span>NPV</span><b>{fmtMoney(modelResult.npv)}</b></div>
          <div class="vp-kpi"><span>IRR</span><b>{fmtPct(modelResult.irr)}</b></div>
          <div class="vp-kpi"><span>MOIC</span><b>{modelResult.moic?.toFixed(2)}×</b></div>
          <div class="vp-kpi"><span>Payback</span><b>{modelResult.payback_yrs ?? '—'} yrs</b></div>
          <div class="vp-kpi vp-verdict {verdictOf(modelResult).toLowerCase()}">
            <span>Verdict</span><b>{verdictOf(modelResult)}</b>
          </div>
        </div>
        <div class="vp-detail">
          <div>Terminal value: {fmtMoney(modelResult.terminal_value)}</div>
          <div>Total invested: {fmtMoney(modelResult.total_invested)}</div>
          <div>Total returned: {fmtMoney(modelResult.total_returned)}</div>
        </div>

        <div class="vp-save-block">
          {#if !selectedDealId}
            <select class="vp-save-deal" bind:value={selectedDealId}>
              <option value="">Pick a deal to save against</option>
              {#each deals as d}
                <option value={d.id}>{d.name}</option>
              {/each}
            </select>
          {:else}
            <div class="vp-save-deal-row">
              <span class="vp-save-deal-label">Saving to: <b>{deals.find(d => d.id === selectedDealId)?.name || '—'}</b></span>
              <button class="vp-mini" onclick={() => selectedDealId = ''}>change</button>
            </div>
          {/if}
          <div class="vp-save-buttons">
            <button class="vp-save-btn" disabled={!selectedDealId || !modelResult || savingScenario} onclick={() => saveScenario('base')}>+ SAVE AS BASE</button>
            <button class="vp-save-btn" disabled={!selectedDealId || !modelResult || savingScenario} onclick={() => saveScenario('upside')}>+ SAVE AS UPSIDE</button>
            <button class="vp-save-btn" disabled={!selectedDealId || !modelResult || savingScenario} onclick={() => saveScenario('downside')}>+ SAVE AS DOWNSIDE</button>
          </div>
          {#if savedFlash}<div class="vp-save-flash">✓ {savedFlash}</div>{/if}
        </div>
      {/if}
    </section>
  {/if}

  {#if tab === 'sensitivity'}
    <section class="vp-section">
      <h3>Sensitivity grid</h3>
      <label>WACC range <input type="text" bind:value={sensWAcc} /></label>
      <label>Terminal-growth range <input type="text" bind:value={sensG} /></label>
      <button class="vp-btn" disabled={sensRunning} onclick={runSens}>
        {sensRunning ? '…' : 'Run grid'}
      </button>
      {#if sensResult?.grid}
        {@const _flat = (sensResult.grid as any[][]).flat().filter((x: any) => x != null && !isNaN(x)).map((x: any) => Math.abs(x))}
        {@const _maxAbs = _flat.length ? Math.max(..._flat) : 1}
        <table class="vp-table vp-grid">
          <thead>
            <tr><th>WACC \ g</th>{#each sensResult.growth_axis as g}<th>{fmtPct(g)}</th>{/each}</tr>
          </thead>
          <tbody>
            {#each sensResult.grid as row, i}
              <tr>
                <td><b>{fmtPct(sensResult.wacc_axis[i])}</b></td>
                {#each row as v}
                  {@const _alpha = v == null || isNaN(v) ? 0 : Math.min(1, Math.abs(v) / (_maxAbs || 1))}
                  {@const _bg = v == null || isNaN(v) ? 'transparent' : (v >= 0 ? `rgba(44,122,63,${_alpha.toFixed(3)})` : `rgba(192,57,43,${_alpha.toFixed(3)})`)}
                  <td class="vp-grid-cell" class:positive={v > 0} class:negative={v < 0} style="background: {_bg}">{v == null ? '—' : fmtMoney(v)}</td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
  {/if}

  {#if tab === 'unit_econ'}
    <section class="vp-section">
      <h3>Unit economics</h3>
      <label>CAC <input type="number" bind:value={cac} /></label>
      <label>LTV <input type="number" bind:value={ltv} /></label>
      <label>Gross margin <input type="number" step="0.01" bind:value={gm} /></label>
      <button class="vp-btn" onclick={runUE}>Compute</button>
      {#if ueResult}
        <div class="vp-kpis">
          <div class="vp-kpi"><span>LTV/CAC</span><b>{ueResult.ltv_cac?.toFixed(2)}×</b></div>
          <div class="vp-kpi vp-verdict {ueResult.flag}"><span>Flag</span><b>{ueResult.flag}</b></div>
        </div>
      {/if}
    </section>
  {/if}

  {#if tab === 'jv'}
    <section class="vp-section">
      <h3>Partner fit</h3>
      <label>Our capabilities (comma-separated)
        <input type="text" bind:value={selfCaps} />
      </label>
      <label>Partner capabilities
        <input type="text" bind:value={partnerCaps} />
      </label>
      <button class="vp-btn" onclick={runFit}>Score fit</button>
      {#if fitResult}
        <div class="vp-kpis">
          <div class="vp-kpi"><span>Fit score</span><b>{fitResult.fit_score?.toFixed(0)}/100</b></div>
          <div class="vp-kpi"><span>Gaps filled</span><b>{fitResult.gaps_filled_pct?.toFixed(0)}%</b></div>
        </div>
        <div class="vp-detail">
          <div><b>Overlap:</b> {fitResult.overlap?.join(', ') || '—'}</div>
          <div><b>Complement:</b> {fitResult.complement?.join(', ') || '—'}</div>
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .vp-root { padding: 16px; font-size: 13px; color: #2c2a26; }
  .vp-head { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }
  .vp-head h2 { margin: 0; font-size: 18px; font-weight: 600; }
  .vp-tabs { display: flex; gap: 4px; border-bottom: 1px solid #e8e3d6; }
  .vp-tabs button {
    background: transparent; border: 0; padding: 8px 12px; cursor: pointer;
    font-size: 12px; color: #6b6557; border-bottom: 2px solid transparent;
  }
  .vp-tabs button.active { color: #c96342; border-bottom-color: #c96342; font-weight: 600; }
  .vp-section { display: flex; flex-direction: column; gap: 10px; }
  .vp-section h3 { margin: 0; font-size: 14px; font-weight: 600; color: #1a1614; }
  .vp-section label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: #6b6557; }
  .vp-section input, .vp-section select {
    padding: 6px 10px; border: 1px solid #d6cfbe; background: #fff;
    font-size: 13px; font-family: inherit; color: #2c2a26;
  }
  .vp-row { display: flex; gap: 10px; align-items: end; flex-wrap: wrap; }
  .vp-btn {
    background: #c96342; color: #fff; border: 0; padding: 8px 14px;
    font-size: 12px; font-weight: 600; cursor: pointer; text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .vp-btn:hover { background: #b35636; }
  .vp-btn:disabled { background: #d6cfbe; cursor: not-allowed; }
  .vp-newdeal { display: flex; gap: 6px; flex-wrap: wrap; margin: 10px 0; }
  .vp-newdeal input, .vp-newdeal select { flex: 1; min-width: 100px; }
  .vp-newdeal input[placeholder="Deal name (e.g. EV Charging Yangon)"] { min-width: 220px; }
  .vp-table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  .vp-table th, .vp-table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #e8e3d6; }
  .vp-table th { font-size: 10.5px; text-transform: uppercase; color: #6b6557; letter-spacing: 0.05em; }
  .vp-grid td { text-align: right; font-variant-numeric: tabular-nums; }
  .vp-grid td.positive { color: #2c7a3f; background: rgba(44, 122, 63, 0.04); }
  .vp-grid td.negative { color: #c0392b; background: rgba(192, 57, 43, 0.04); }
  .vp-export { display: flex; gap: 4px; }
  .vp-mini {
    background: transparent; border: 1px solid #d6cfbe; color: #1a1614;
    padding: 4px 8px; font-size: 10.5px; font-weight: 600; cursor: pointer;
    letter-spacing: 0.04em;
  }
  .vp-mini:hover { background: #c96342; color: #fff; border-color: #c96342; }
  .vp-pill {
    display: inline-block; padding: 2px 8px; background: #f5f0e3;
    border-radius: var(--pw-radius-sm); font-size: 10px; text-transform: uppercase; color: #6b6557;
  }
  .vp-kpis { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0; }
  .vp-kpi {
    flex: 1; min-width: 120px; padding: 10px 12px; border: 1px solid #e8e3d6;
    background: #fff; display: flex; flex-direction: column; gap: 4px;
  }
  .vp-kpi span { font-size: 10.5px; text-transform: uppercase; color: #6b6557; letter-spacing: 0.04em; }
  .vp-kpi b { font-size: 18px; color: #1a1614; }
  .vp-verdict.go, .vp-verdict.healthy { border-color: #2c7a3f; }
  .vp-verdict.go b, .vp-verdict.healthy b { color: #2c7a3f; }
  .vp-verdict.hold, .vp-verdict.marginal { border-color: #c96342; }
  .vp-verdict.hold b, .vp-verdict.marginal b { color: #c96342; }
  .vp-verdict.pass, .vp-verdict.unhealthy { border-color: #c0392b; }
  .vp-verdict.pass b, .vp-verdict.unhealthy b { color: #c0392b; }
  .vp-detail { font-size: 12px; color: #6b6557; display: flex; flex-direction: column; gap: 4px; }
  .vp-err { color: #c0392b; padding: 6px 10px; background: rgba(192,57,43,0.06); }
  .vp-muted { color: #9a9080; padding: 12px; }
  .vp-save-block { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; padding-top: 12px; border-top: 1px solid #e8e3d6; }
  .vp-save-deal { padding: 6px 10px; border: 1px solid #d6cfbe; background: #fff; font-size: 12px; max-width: 320px; color: #2c2a26; }
  .vp-save-deal-row { display: flex; gap: 10px; align-items: center; font-size: 11.5px; color: #6b6557; }
  .vp-save-deal-label b { color: #1a1614; }
  .vp-save-buttons { display: flex; gap: 6px; flex-wrap: wrap; }
  .vp-save-btn {
    background: transparent; border: 1px solid #c96342; color: #1a1614;
    padding: 6px 12px; font-size: 11px; font-weight: 600; cursor: pointer;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .vp-save-btn:hover:not(:disabled) { background: #c96342; color: #1a1614; }
  .vp-save-btn:disabled { border-color: #d6cfbe; color: #9a9080; cursor: not-allowed; }
  .vp-save-flash { color: #2c7a3f; font-size: 11.5px; font-weight: 600; }
  .vp-grid-cell { transition: background 0.15s; }
</style>
