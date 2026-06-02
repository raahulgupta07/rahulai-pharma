<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import { page } from '$app/stores';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';

 const slug = $derived($page.params.slug);

 let token = $state('');
 let customers = $state<any[]>([]);
 let total = $state(0);
 let loading = $state(true);
 let err = $state('');
 let q = $state('');
 let orderBy = $state<'spend' | 'recency' | 'frequency'>('spend');
 let segments = $state<any[]>([]);
 let health = $state<any>(null);
 let lastFetched = $state<Date | null>(null);
 let recomputing = $state(false);
 let recomputeMsg = $state('');

 function _h(): Record<string, string> {
 return token ? { Authorization: `Bearer ${token}` } : {};
 }

 async function load() {
 loading = true; err = '';
 try {
 const params = new URLSearchParams({ limit: '100', order_by: orderBy });
 if (q.trim()) params.set('q', q.trim());
 const r = await fetch(`/api/projects/${slug}/customers/list?${params}`, { headers: _h() });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || 'load failed');
 customers = d.customers || [];
 total = d.total_count || 0;
 lastFetched = new Date();
 // kick off lazy spend-trend hydration (non-blocking)
 hydrateSpendTrends();
 } catch (e: any) { err = e.message; customers = []; }
 loading = false;
 }

 async function hydrateSpendTrends() {
 if (!customers.length) return;
 try {
 const ids = customers.map(c => c.id).slice(0, 100).join(',');
 const r = await fetch(
 `/api/projects/${slug}/customer-spend-trends?ids=${encodeURIComponent(ids)}&weeks=12`,
 { headers: _h() }
 );
 if (!r.ok) return; // graceful degrade on 404
 const d = await r.json();
 const trends: Record<string, number[]> = d.trends || {};
 // mutate rows with spend_trend
 customers = customers.map(c => ({
 ...c,
 spend_trend: Array.isArray(trends[c.id]) ? trends[c.id] : c.spend_trend,
 }));
 } catch {
 // graceful degrade — sparklines just show fallback
 }
 }

 function sparkPath(values: number[], w: number, h: number): string {
 if (!values || values.length < 2) return '';
 const max = Math.max(...values);
 const min = Math.min(...values);
 const range = max - min || 1;
 const step = w / (values.length - 1);
 return values.map((v, i) => {
 const x = i * step;
 const y = h - ((v - min) / range) * h;
 return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
 }).join(' ');
 }

 async function loadSummaries() {
 try {
 const [s, h] = await Promise.all([
 fetch(`/api/projects/${slug}/customer-segments-summary`, { headers: _h() }).then(r => r.json()).catch(() => ({})),
 fetch(`/api/projects/${slug}/customer-health-summary`, { headers: _h() }).then(r => r.json()).catch(() => ({})),
 ]);
 segments = s.segments || [];
 health = h || null;
 } catch {}
 }

 function fmt(n: number | null | undefined): string {
 if (n == null) return '—';
 if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
 if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(1)}k`;
 return `$${Math.round(n)}`;
 }

 function fmtDate(s: string | null): string {
 if (!s) return '—';
 try { const d = new Date(s); const dy = (Date.now() - d.getTime()) / 86400000; if (dy < 1) return 'today'; if (dy < 30) return `${Math.floor(dy)}d ago`; return d.toLocaleDateString(); } catch { return s; }
 }

 async function recompute() {
 if (recomputing) return;
 recomputing = true; recomputeMsg = '';
 try {
 const r = await fetch(`/api/projects/${slug}/customer-recompute`, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', ..._h() },
 });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || d.error || 'recompute failed');
 recomputeMsg = d.skipped
 ? `cooldown · ${d.age_hours ?? '?'}h ago`
 : `scored ${d.scored ?? 0}`;
 await Promise.all([load(), loadSummaries()]);
 } catch (e: any) {
 recomputeMsg = `error: ${e.message}`;
 } finally {
 recomputing = false;
 }
 }

 function open(id: string) {
 goto(`${base}/project/${slug}/customer/${encodeURIComponent(id)}`);
 }

 onMount(async () => {
 if (typeof localStorage !== 'undefined') token = localStorage.getItem('dash_token') || '';
 if (!token) { goto(`${base}/login`); return; }
 await Promise.all([load(), loadSummaries()]);
 });

 $effect(() => {
 if (token) { load(); }
 });
</script>

<svelte:head><title>Customers · {slug}</title></svelte:head>

<div style="background: #f5f5e8; min-height: 100vh; padding: 16px; font-family: monospace;">

  <!-- Header -->
  <div style="background: #1a1a1a; color: #00fc40; padding: 12px 18px; display: flex; align-items: center; gap: 12px; margin-bottom: 14px;">
    <a href={`${base}/project/${slug}/settings`} style="color: #ccc; font-size: 11px; text-decoration: none; padding: 4px 8px; border: 1px solid #888;">←</a>
    <span style="font-size: 11px; font-weight: 900; letter-spacing: 0.06em;"><Icon name="user" size={14} /> CUSTOMERS · {slug}</span>
    <span style="margin-left: auto; font-size: 11px; color: #888;">{total} total</span>
    <button onclick={load} style="padding: 4px 10px; background: #00fc40; color: #000; border: 1px solid #00fc40; cursor: pointer; font-family: monospace; font-size: 10px; font-weight: 900;">↻ REFRESH</button>
    <button onclick={recompute} disabled={recomputing} title="Recompute nightly RFM + churn + CLV cache" style="padding: 4px 10px; background: {recomputing ? '#555' : '#ff9d00'}; color: #000; border: 1px solid {recomputing ? '#555' : '#ff9d00'}; cursor: {recomputing ? 'wait' : 'pointer'}; font-family: monospace; font-size: 10px; font-weight: 900;">
      {recomputing ? 'COMPUTING…' : 'RECOMPUTE NIGHTLY SCORES'}
    </button>
    {#if recomputeMsg}
      <span style="font-size: 11px; color: #ccc; margin-left: 6px;">{recomputeMsg}</span>
    {/if}
  </div>

  <!-- Health distribution -->
  {#if health?.risk_distribution}
    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px;">
      {#each Object.entries(health.risk_distribution) as [cat, count]}
        <div style="background: #fafaf5; border: 1.5px solid {cat==='active'?'#0078d4':cat==='cooling'?'#ff9d00':cat==='at_risk'?'#be2d06':'#666'}; padding: 10px;">
          <div style="font-size: 11px; font-weight: 900; letter-spacing: 0.06em; color: #555; text-transform: uppercase;">{cat}</div>
          <div style="font-size: 19px; font-weight: 900; color: {cat==='active'?'#0078d4':cat==='cooling'?'#ff9d00':cat==='at_risk'?'#be2d06':'#666'};">{count}</div>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Segments strip -->
  {#if segments.length}
    <div style="background: #fafaf5; border: 1.5px solid #1a1a1a; padding: 10px; margin-bottom: 14px; overflow-x: auto;">
      <div style="font-size: 11px; font-weight: 900; letter-spacing: 0.06em; color: #555; margin-bottom: 6px;">RFM SEGMENTS</div>
      <div style="display: flex; gap: 8px;">
        {#each segments as seg}
          <div style="border: 1px solid #ddd; padding: 4px 8px; background: #fff; font-size: 10px; white-space: nowrap;">
            <strong>{seg.name}</strong> · {seg.count}
          </div>
        {/each}
      </div>
    </div>
  {/if}

  <!-- Search + sort -->
  <div style="display: flex; gap: 8px; margin-bottom: 10px; align-items: center;">
    <input
      bind:value={q}
      onkeydown={(e) => { if (e.key === 'Enter') load(); }}
      placeholder="search customer id or name..."
      style="flex: 1; padding: 6px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff;"
    />
    <select bind:value={orderBy} style="padding: 6px 10px; border: 1.5px solid #1a1a1a; font-family: monospace; font-size: 11px; background: #fff;">
      <option value="spend">SORT: SPEND</option>
      <option value="recency">SORT: RECENT</option>
      <option value="frequency">SORT: FREQUENCY</option>
    </select>
  </div>

  {#if err}
    <div style="background: #fff; border: 2px solid #be2d06; color: #be2d06; padding: 12px; font-size: 11px;">API error: {err}</div>
  {:else if loading}
    <div style="padding: 20px; text-align: center; color: #888; font-size: 11px;">loading…</div>
  {:else if customers.length === 0}
    <div style="background: #fafaf5; border: 1.5px solid #ddd; padding: 30px; text-align: center; color: #888; font-size: 11px;">
      <Icon name="inbox" size={14} /> No customers found. Apply a retail/ecommerce template to your project to populate.
    </div>
  {:else}
    <table style="width: 100%; background: #fafaf5; border: 1.5px solid #1a1a1a; border-collapse: collapse; font-size: 11px;">
      <thead style="background: #1a1a1a; color: #00fc40;">
        <tr>
          <th style="padding: 8px 10px; text-align: left;">CUSTOMER</th>
          <th style="padding: 8px 10px; text-align: left;">NAME</th>
          <th style="padding: 8px 10px; text-align: right;">SPEND</th>
          <th style="padding: 8px 10px; text-align: right;">ORDERS</th>
          <th style="padding: 8px 10px; text-align: left;">LAST</th>
          <th style="padding: 8px 10px; text-align: right;">DAYS</th>
          <th style="padding: 8px 10px; text-align: center;">12W</th>
          <th style="padding: 8px 10px; text-align: left;">→</th>
        </tr>
      </thead>
      <tbody>
        {#each customers as c}
          <tr onclick={() => open(c.id)} style="border-bottom: 1px solid #eee; cursor: pointer; background: #fff;"
              onmouseover={(e: any) => e.currentTarget.style.background = '#fffce0'}
              onmouseout={(e: any) => e.currentTarget.style.background = '#fff'}>
            <td style="padding: 6px 10px; font-weight: 700;">{c.id}</td>
            <td style="padding: 6px 10px; color: #555;">{c.name || '—'}</td>
            <td style="padding: 6px 10px; text-align: right; font-weight: 700;">{fmt(c.total_spend)}</td>
            <td style="padding: 6px 10px; text-align: right;">{c.order_count ?? '—'}</td>
            <td style="padding: 6px 10px; color: #555;">{fmtDate(c.last_seen)}</td>
            <td style="padding: 6px 10px; text-align: right; color: {(c.days_since ?? 0) > 90 ? '#be2d06' : (c.days_since ?? 0) > 60 ? '#ff9d00' : '#0078d4'};">{c.days_since ?? '—'}</td>
            <td style="padding: 4px 8px; text-align: center;">
              {#if Array.isArray(c.spend_trend) && c.spend_trend.length >= 2}
                <svg width="60" height="16" viewBox="0 0 60 16" style="display: inline-block; vertical-align: middle;">
                  <path d={sparkPath(c.spend_trend, 60, 16)}
                        fill="none" stroke="#007518" stroke-width="1.2"
                        stroke-linejoin="round" stroke-linecap="round" />
                </svg>
              {:else if (c.total_spend ?? 0) > 0 || (c.order_count ?? 0) > 0}
                <svg width="60" height="16" viewBox="0 0 60 16" style="display: inline-block; vertical-align: middle;">
                  <line x1="0" y1="8" x2="60" y2="8" stroke="#888" stroke-width="1.2" stroke-dasharray="2,2" />
                </svg>
              {:else}
                <span style="color: #888;">—</span>
              {/if}
            </td>
            <td style="padding: 6px 10px;">→</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>
