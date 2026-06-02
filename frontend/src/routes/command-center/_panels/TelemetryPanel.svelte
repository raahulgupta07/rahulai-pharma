<script lang="ts">
  import { onDestroy } from 'svelte';

  let { sub = 'overview' } = $props<{ sub?: string }>();

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  const ENDPOINTS: Record<string, string> = {
    overview: '/api/telemetry-admin/summary',
    live: '/api/telemetry-admin/live',
    cost: '/api/telemetry-admin/cost',
    errors: '/api/telemetry-admin/errors',
    latency: '/api/telemetry-admin/latency',
    tools: '/api/telemetry-admin/tool-usage',
    connectors: '/api/telemetry-admin/connector-health',
    tokens: '/api/telemetry-admin/token-flow',
    alerts: '/api/telemetry-admin/alerts',
  };

  let data = $state<any>(null);
  let loading = $state(false);
  let err = $state<string>('');
  let liveTimer: ReturnType<typeof setInterval> | null = null;

  const endpoint = $derived(ENDPOINTS[sub] || ENDPOINTS.overview);

  async function load(ep: string) {
    loading = true; err = ''; if (sub !== 'live') data = null;
    try {
      const r = await fetch(ep, { headers: _h() });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e: any) {
      err = e?.message || 'load failed';
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    const ep = endpoint;
    load(ep);
    if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
    if (sub === 'live') {
      liveTimer = setInterval(() => load(ep), 5000);
    }
  });

  onDestroy(() => { if (liveTimer) clearInterval(liveTimer); });

  async function silence(id: string, on: boolean) {
    try {
      await fetch(`/api/telemetry-admin/alerts/${id}/${on ? 'silence' : 'unsilence'}`, {
        method: 'POST', headers: _h(),
      });
      await load(endpoint);
    } catch {}
  }

  function rows(): any[] {
    if (!data) return [];
    if (Array.isArray(data)) return data;
    for (const k of ['items', 'rows', 'events', 'series', 'alerts', 'connectors', 'tools', 'errors']) {
      if (Array.isArray((data as any)[k])) return (data as any)[k];
    }
    return [];
  }

  function stats(): { k: string; v: any }[] {
    if (!data) return [];
    if (data.stats && typeof data.stats === 'object') {
      return Object.entries(data.stats).slice(0, 5).map(([k, v]) => ({ k, v }));
    }
    const out: { k: string; v: any }[] = [];
    for (const [k, v] of Object.entries(data)) {
      if (typeof v === 'number' || typeof v === 'string') out.push({ k, v });
      if (out.length >= 5) break;
    }
    return out;
  }

  function cols(list: any[]): string[] {
    if (!list.length) return [];
    const first = list[0];
    if (typeof first !== 'object' || first === null) return [];
    return Object.keys(first).slice(0, 8);
  }

  function sparkline(values: number[], w = 80, h = 22): string {
    if (!values || values.length < 2) return '';
    const min = Math.min(...values), max = Math.max(...values);
    const range = max - min || 1;
    const step = w / (values.length - 1);
    return values.map((v, i) => `${(i * step).toFixed(1)},${(h - ((v - min) / range) * h).toFixed(1)}`).join(' ');
  }

  function findSpark(r: any): number[] | null {
    for (const k of ['spark', 'sparkline', 'series', 'points', 'history']) {
      if (Array.isArray(r[k]) && r[k].length && typeof r[k][0] === 'number') return r[k];
    }
    return null;
  }
</script>

<div class="sub-content">
  {#if loading && !data}
    <div class="muted">loading…</div>
  {:else if err}
    <div class="err">error: {err}</div>
  {:else if sub === 'overview'}
    {@const st = stats()}
    {#if st.length}
      <div class="stat-strip">
        {#each st.slice(0, 5) as s}
          <div class="stat">
            <div class="stat-k">{s.k}</div>
            <div class="stat-v">{s.v}</div>
          </div>
        {/each}
      </div>
    {:else}
      <div class="muted">no summary data</div>
    {/if}
    <div class="section-title">Recent activity</div>
    <table class="tbl">
      <thead><tr><th>when</th><th>source</th><th>event</th><th>detail</th></tr></thead>
      <tbody>
        <tr><td colspan="4" class="muted" style="text-align:center; padding:20px;">no recent activity</td></tr>
      </tbody>
    </table>
  {:else}
    {@const st = stats()}
    {@const lst = rows()}
    {@const cs = cols(lst)}
    {#if sub === 'live'}
      <div class="live-banner">● LIVE · auto-refresh every 5s</div>
    {/if}
    {#if st.length}
      <div class="stat-strip">
        {#each st as s}
          <div class="stat">
            <div class="stat-k">{s.k}</div>
            <div class="stat-v">{s.v}</div>
          </div>
        {/each}
      </div>
    {/if}

    {#if cs.length}
      <table class="tbl">
        <thead>
          <tr>
            {#each cs as c}<th>{c}</th>{/each}
            <th>TREND</th>
            {#if sub === 'alerts'}<th>ACTION</th>{/if}
          </tr>
        </thead>
        <tbody>
          {#each lst as r}
            {@const sp = findSpark(r)}
            <tr>
              {#each cs as c}<td>{typeof r[c] === 'object' ? JSON.stringify(r[c]) : (r[c] ?? '')}</td>{/each}
              <td>
                {#if sp}
                  <svg width="80" height="22" style="display: block;">
                    <polyline points={sparkline(sp)} fill="none" stroke="var(--pw-accent)" stroke-width="1.5" />
                  </svg>
                {/if}
              </td>
              {#if sub === 'alerts'}
                <td>
                  <button class="btn-sm" onclick={() => silence(r.id, !r.silenced)}>
                    {r.silenced ? 'unsilence' : 'silence'}
                  </button>
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    {:else}
      <div class="muted">no data</div>
    {/if}
  {/if}
</div>

<style>
  .sub-content { width: 100%; min-width: 0; }
  .muted { color: var(--pw-ink-soft); font: 12px Inter; padding: 20px 0; }
  .err { color: #c0392b; font: 12px Inter; padding: 12px; background: rgba(192,57,43,0.08); border: 1px solid #c0392b; }
  .live-banner { padding: 6px 12px; background: rgba(26,127,55,0.08); border: 1px solid #1a7f37; color: #1a7f37; font: 600 11px Inter; letter-spacing: 0.06em; margin-bottom: 12px; }
  .stat-strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin-bottom: 16px; }
  .stat { background: var(--pw-surface); border: 1px solid var(--pw-border); padding: 10px 12px; }
  .stat-k { font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft); margin-bottom: 4px; }
  .stat-v { font: 600 18px Inter; color: var(--pw-ink); }
  .section-title { font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft); margin: 16px 0 8px; }
  .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface); border: 1px solid var(--pw-border); font-size: 12px; }
  .tbl th { text-align: left; padding: 8px 12px; font: 600 10px Inter; text-transform: uppercase; background: var(--pw-bg-alt); border-bottom: 1px solid var(--pw-border); }
  .tbl td { padding: 8px 12px; border-top: 1px solid var(--pw-border); vertical-align: top; }
  .btn-sm { padding: 4px 10px; font: 600 10px Inter; border: 1px solid var(--pw-border); background: var(--pw-bg-alt); cursor: pointer; }
</style>
