<script lang="ts">
  import { onMount } from 'svelte';

  type SeriesRow = { date: string | null; pass_rate: number; tier: string; n: number };
  type Trend = {
    series: SeriesRow[];
    by_tier: Record<string, number>;
    overall: number;
    last_run_at: string | null;
  };

  let data = $state<Trend | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let days = $state(30);

  async function load() {
    loading = true;
    error = null;
    try {
      const r = await fetch(`/api/accuracy/trend?days=${days}`, {
        headers: { 'Accept': 'application/json' }
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e: any) {
      error = e?.message || String(e);
      data = null;
    } finally {
      loading = false;
    }
  }

  onMount(load);

  // Derived KPIs
  const overallPct = $derived(data ? (data.overall * 100).toFixed(1) : '—');
  const totalEvals = $derived(
    data ? data.series.reduce((acc, r) => acc + (r.n || 0), 0) : 0
  );
  const last7d = $derived(() => {
    if (!data || !data.series.length) return '—';
    const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
    let pass = 0;
    let total = 0;
    for (const r of data.series) {
      if (!r.date) continue;
      if (new Date(r.date).getTime() < cutoff) continue;
      pass += (r.pass_rate || 0) * (r.n || 0);
      total += r.n || 0;
    }
    return total > 0 ? ((pass / total) * 100).toFixed(1) : '—';
  });

  // Build chart points: one per date (average pass_rate across tiers, weighted by n)
  const chartPoints = $derived(() => {
    if (!data || !data.series.length) return [] as { date: string; rate: number }[];
    const byDate: Record<string, { p: number; n: number }> = {};
    for (const r of data.series) {
      if (!r.date) continue;
      const cur = byDate[r.date] || { p: 0, n: 0 };
      cur.p += (r.pass_rate || 0) * (r.n || 0);
      cur.n += r.n || 0;
      byDate[r.date] = cur;
    }
    return Object.entries(byDate)
      .map(([date, v]) => ({ date, rate: v.n > 0 ? v.p / v.n : 0 }))
      .sort((a, b) => a.date.localeCompare(b.date));
  });

  // SVG chart math
  const W = 800;
  const H = 220;
  const PAD = { top: 16, right: 16, bottom: 28, left: 40 };

  function chartPath(pts: { date: string; rate: number }[]): string {
    if (pts.length === 0) return '';
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const stepX = pts.length > 1 ? innerW / (pts.length - 1) : 0;
    return pts
      .map((p, i) => {
        const x = PAD.left + i * stepX;
        const y = PAD.top + innerH - p.rate * innerH;
        return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join(' ');
  }
</script>

<div class="acc-shell">
  <header class="acc-head">
    <div>
      <h1>Accuracy Trend</h1>
      <p class="muted">Eval pass rate + verified-truth grading over time</p>
    </div>
    <div class="ctrls">
      <select bind:value={days} onchange={load}>
        <option value={7}>Last 7 days</option>
        <option value={30}>Last 30 days</option>
        <option value={90}>Last 90 days</option>
      </select>
      <button class="refresh" onclick={load} disabled={loading}>{loading ? '...' : 'Refresh'}</button>
    </div>
  </header>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">Failed: {error}</p>
  {:else if data}
    <section class="tiles">
      <div class="tile">
        <div class="tile-label">Overall pass-rate</div>
        <div class="tile-value">{overallPct === '—' ? '—' : `${overallPct}%`}</div>
        <div class="tile-sub">across all evals in window</div>
      </div>
      <div class="tile">
        <div class="tile-label">Last 7d pass-rate</div>
        <div class="tile-value">{last7d() === '—' ? '—' : `${last7d()}%`}</div>
        <div class="tile-sub">{data.last_run_at ? `last: ${new Date(data.last_run_at).toLocaleString()}` : 'no runs'}</div>
      </div>
      <div class="tile">
        <div class="tile-label">Total evals run</div>
        <div class="tile-value">{totalEvals.toLocaleString()}</div>
        <div class="tile-sub">cases scored in window</div>
      </div>
    </section>

    <section class="chart-card">
      <h2>Pass rate over time</h2>
      {#if chartPoints().length === 0}
        <p class="muted small">No data points in window.</p>
      {:else}
        <svg viewBox={`0 0 ${W} ${H}`} class="chart" role="img" aria-label="Pass rate trend">
          <!-- gridlines -->
          {#each [0, 0.25, 0.5, 0.75, 1.0] as g}
            {@const y = PAD.top + (H - PAD.top - PAD.bottom) * (1 - g)}
            <line x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} stroke="#e8e3d6" stroke-width="1" />
            <text x={PAD.left - 6} y={y + 4} text-anchor="end" font-size="10" fill="#777">{Math.round(g * 100)}%</text>
          {/each}
          <!-- line -->
          <path d={chartPath(chartPoints())} fill="none" stroke="#c96342" stroke-width="2" />
          <!-- points -->
          {#each chartPoints() as p, i}
            {@const innerW = W - PAD.left - PAD.right}
            {@const innerH = H - PAD.top - PAD.bottom}
            {@const stepX = chartPoints().length > 1 ? innerW / (chartPoints().length - 1) : 0}
            {@const cx = PAD.left + i * stepX}
            {@const cy = PAD.top + innerH - p.rate * innerH}
            <circle {cx} {cy} r="3" fill="#c96342">
              <title>{p.date}: {(p.rate * 100).toFixed(1)}%</title>
            </circle>
          {/each}
          <!-- x labels: first + last -->
          {#if chartPoints().length > 0}
            <text x={PAD.left} y={H - 8} font-size="10" fill="#777">{chartPoints()[0].date}</text>
            {#if chartPoints().length > 1}
              <text x={W - PAD.right} y={H - 8} text-anchor="end" font-size="10" fill="#777">{chartPoints()[chartPoints().length - 1].date}</text>
            {/if}
          {/if}
        </svg>
      {/if}
    </section>

    <section class="tier-card">
      <h2>By tier / source</h2>
      {#if Object.keys(data.by_tier).length === 0}
        <p class="muted small">No tier-level data available.</p>
      {:else}
        <table>
          <thead>
            <tr><th>Tier</th><th>Pass-rate</th><th>n</th></tr>
          </thead>
          <tbody>
            {#each Object.entries(data.by_tier) as [tier, rate]}
              {@const total = data.series.filter(s => s.tier === tier).reduce((a, b) => a + (b.n || 0), 0)}
              <tr>
                <td><code>{tier}</code></td>
                <td>{(rate * 100).toFixed(1)}%</td>
                <td>{total.toLocaleString()}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
  {/if}
</div>

<style>
  .acc-shell {
    padding: 24px 32px;
    max-width: 1100px;
    margin: 0 auto;
    font-family: system-ui, -apple-system, sans-serif;
    color: #1f1c17;
  }
  .acc-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 24px;
    padding-bottom: 12px;
    border-bottom: 1px solid #e8e3d6;
  }
  .acc-head h1 {
    font-size: 22px;
    margin: 0 0 4px 0;
    font-weight: 600;
  }
  .muted { color: #777; font-size: 13px; margin: 0; }
  .small { font-size: 12px; }
  .err { color: #b3261e; font-size: 13px; }
  .ctrls { display: flex; gap: 8px; }
  .ctrls select, .ctrls button {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
  }
  .ctrls button.refresh:hover { background: #f7f3e9; }
  .ctrls button:disabled { opacity: 0.5; cursor: default; }

  .tiles {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 14px;
    margin-bottom: 24px;
  }
  .tile {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 16px;
  }
  .tile-label { font-size: 11px; color: #777; text-transform: uppercase; letter-spacing: 0.04em; }
  .tile-value { font-size: 28px; font-weight: 600; margin: 6px 0 4px 0; color: #c96342; }
  .tile-sub { font-size: 11px; color: #999; }

  .chart-card, .tier-card {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 16px;
    margin-bottom: 20px;
  }
  .chart-card h2, .tier-card h2 {
    font-size: 14px;
    margin: 0 0 12px 0;
    font-weight: 600;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .chart { width: 100%; height: auto; display: block; }

  table { width: 100%; border-collapse: collapse; }
  th, td {
    text-align: left;
    padding: 8px 10px;
    border-bottom: 1px solid #f0ebde;
    font-size: 13px;
  }
  th { font-size: 11px; color: #777; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 500; }
  td code { background: #f7f3e9; padding: 1px 6px; border-radius: 3px; font-size: 12px; }
</style>
