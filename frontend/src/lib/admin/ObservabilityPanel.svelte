<script lang="ts">
  import { dashFetch } from '$lib/api';
  import { onMount } from 'svelte';

  // ---- embed prop ----
  let { embedded = false } = $props();

  // ---- filters ----
  let kind = $state('chat');
  let days = $state(7);
  const KINDS = ['chat', 'training', 'cron', 'learning', 'ml', 'all'];
  const DAYS = [1, 7, 30];

  // ---- state ----
  let loading = $state(true);
  let error = $state('');
  let traces = $state<any[]>([]);
  let rollup = $state<any>(null);
  let agents = $state<any[]>([]);
  let ctxHealth = $state<any>(null);
  let ctxError = $state('');
  let expanded = $state<Record<string, boolean>>({});

  const BUDGET = 32000;
  const NEAR = 28000;

  function arr(x: any): any[] {
    return Array.isArray(x) ? x : [];
  }

  function fmtMs(ms: any): string {
    const n = Number(ms);
    if (!isFinite(n) || n < 0) return '—';
    if (n < 1000) return `${Math.round(n)}ms`;
    return `${(n / 1000).toFixed(1)}s`;
  }
  function fmtCost(usd: any): string {
    const n = Number(usd);
    if (!isFinite(n) || n <= 0) return '$0';
    if (n < 0.01) return `$${n.toFixed(4)}`;
    return `$${n.toFixed(2)}`;
  }
  function fmtTok(t: any): string {
    const n = Number(t);
    if (!isFinite(n) || n <= 0) return '—';
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(Math.round(n));
  }
  function fmtTime(iso: any): string {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch { return String(iso); }
  }
  function statusGlyph(s: string): string {
    if (s === 'error') return '✗';
    if (s === 'running') return '◐';
    return '✓';
  }

  async function load() {
    loading = true;
    error = '';
    try {
      const r = await dashFetch(`/api/admin/traces?kind=${encodeURIComponent(kind)}&days=${days}&limit=200`, {
        headers: { Accept: 'application/json' },
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      traces = arr(d?.traces);
      rollup = d?.rollup ?? null;
      if (d?.error) error = String(d.error);
    } catch (e: any) {
      error = e?.message || String(e);
      traces = [];
      rollup = null;
    } finally {
      loading = false;
    }
    // agents (defensive, independent)
    try {
      const r = await dashFetch(`/api/admin/traces/agents?days=${days}`, { headers: { Accept: 'application/json' } });
      if (r.ok) {
        const d = await r.json();
        agents = arr(d?.agents);
      } else {
        agents = [];
      }
    } catch {
      agents = [];
    }
    // context-health (may 404 — code defensively)
    ctxError = '';
    try {
      const r = await dashFetch(`/api/admin/traces/context-health?days=${days}`, { headers: { Accept: 'application/json' } });
      if (r.ok) {
        const d = await r.json();
        if (d && (d.error || (d.prompt_tokens_p50 == null && d.prompt_tokens_p95 == null && d.runs == null))) {
          ctxHealth = null; ctxError = 'pending';
        } else {
          ctxHealth = d;
        }
      } else {
        ctxHealth = null; ctxError = 'pending';
      }
    } catch {
      ctxHealth = null; ctxError = 'pending';
    }
  }

  function setKind(k: string) { kind = k; load(); }
  function setDays(n: number) { days = n; load(); }
  function toggle(id: string) { expanded = { ...expanded, [id]: !expanded[id] }; }

  onMount(load);

  const slowest = $derived(arr(rollup?.slowest));
</script>

<svelte:head>{#if !embedded}<title>Observability · CityPharma</title>{/if}</svelte:head>

<div class="obs-root">
  {#if !embedded}
    <header class="obs-pagehead">
      <h1 class="obs-pagetitle">Observability</h1>
      <p class="obs-pagesub">Per-chat reasoning traces &amp; context health</p>
    </header>
  {/if}

  <!-- (a) FILTER ROW -->
  <div class="obs-filters">
    <div class="obs-pills">
      {#each KINDS as k}
        <button class="obs-pill" class:obs-pill-on={kind === k} onclick={() => setKind(k)}>{k}</button>
      {/each}
    </div>
    <div class="obs-pills">
      {#each DAYS as n}
        <button class="obs-pill" class:obs-pill-on={days === n} onclick={() => setDays(n)}>{n}d</button>
      {/each}
    </div>
    <button class="obs-pill obs-refresh" onclick={load} disabled={loading}>{loading ? '◐ …' : '↻ refresh'}</button>
  </div>

  <!-- (b) ROLLUP STRIP -->
  <section class="obs-strip">
    {#if loading && !rollup}
      <span class="obs-muted">◐ loading…</span>
    {:else if rollup}
      <span class="obs-stat"><b>{rollup.runs ?? 0}</b> runs</span>
      <span class="obs-sep">·</span>
      <span class="obs-stat" class:obs-bad={Number(rollup.failed) > 0}><b>{rollup.failed ?? 0}</b> failed</span>
      <span class="obs-sep">·</span>
      <span class="obs-stat"><b>{fmtCost(rollup.cost_usd)}</b></span>
      <span class="obs-sep">·</span>
      <span class="obs-stat">slowest <b>{fmtMs(slowest.length ? slowest[0]?.duration_ms : null)}</b></span>
    {:else}
      <span class="obs-muted">no rollup</span>
    {/if}
  </section>

  <!-- (c) CONTEXT-HEALTH STRIP -->
  <section class="obs-strip obs-strip-ctx">
    <span class="obs-strip-label">CONTEXT</span>
    {#if ctxHealth}
      {#if true}
        {@const p95 = Number(ctxHealth.prompt_tokens_p95)}
        <span class="obs-stat">prompt p50 <b>{fmtTok(ctxHealth.prompt_tokens_p50)}</b></span>
        <span class="obs-sep">/</span>
        <span class="obs-stat" class:obs-warn={isFinite(p95) && p95 > NEAR}>p95 <b>{fmtTok(ctxHealth.prompt_tokens_p95)}</b>{#if isFinite(p95) && p95 > NEAR}<span class="obs-warnmark" title={`approaching ${BUDGET} budget`}> ⚠</span>{/if}</span>
        <span class="obs-sep">·</span>
        <span class="obs-stat">caps fired <b>{ctxHealth.capped_count ?? 0}</b></span>
        <span class="obs-sep">·</span>
        <span class="obs-stat obs-muted">{ctxHealth.runs ?? 0} runs</span>
      {/if}
    {:else}
      <span class="obs-muted">prompt p50 <b>—</b> / p95 <b>—</b> · caps <b>—</b>{#if ctxError === 'pending'} <span class="obs-pending">(pending)</span>{/if}</span>
    {/if}
  </section>

  {#if error}
    <div class="obs-err-banner">✗ traces unavailable ({error})</div>
  {/if}

  <!-- (d) TRACE LIST -->
  <section class="obs-panel">
    <div class="obs-h">TRACES</div>
    {#if loading && traces.length === 0}
      <div class="obs-empty">◐ loading…</div>
    {:else if traces.length === 0}
      <div class="obs-empty">no traces in window</div>
    {:else}
      <div class="obs-tracelist">
        {#each traces as t (t.trace_id ?? t.name)}
          {@const tid = String(t.trace_id ?? t.name ?? Math.random())}
          {@const kids = arr(t.children)}
          {@const open = !!expanded[tid]}
          <div class="obs-trace" class:obs-trace-err={t.status === 'error'}>
            <button class="obs-trace-row" onclick={() => toggle(tid)}>
              <span class="obs-tw">{kids.length ? (open ? '▾' : '▸') : ' '}</span>
              <span class="obs-time">{fmtTime(t.started_at)}</span>
              <span class="obs-name" title={t.name}>{t.name || t.trace_id || '(unnamed)'}{#if t.project_slug}<span class="obs-slug"> · {t.project_slug}</span>{/if}</span>
              <span class="obs-dur">{fmtMs(t.duration_ms)}</span>
              <span class="obs-tok">{fmtTok(t.tokens)}</span>
              <span class="obs-cost">{fmtCost(t.cost_usd)}</span>
              <span class="obs-pill-status" data-s={t.status}>{statusGlyph(t.status)}</span>
            </button>
            {#if open}
              {#if t.error}
                <div class="obs-error-text">✗ {t.error}</div>
              {/if}
              {#if kids.length}
                <div class="obs-children">
                  {#each kids as c, ci (ci)}
                    <div class="obs-child" class:obs-child-err={c.status === 'error'}>
                      <span class="obs-child-name" title={c.name}>{c.name || '(span)'}</span>
                      <span class="obs-dur">{fmtMs(c.duration_ms)}</span>
                      <span class="obs-tok">{fmtTok(c.tokens)}</span>
                      <span class="obs-pill-status" data-s={c.status}>{statusGlyph(c.status)}</span>
                      {#if c.error}<div class="obs-error-text obs-child-error">✗ {c.error}</div>{/if}
                    </div>
                  {/each}
                </div>
              {:else}
                <div class="obs-empty obs-empty-sm">no nested spans</div>
              {/if}
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </section>

  <!-- (e) PER-AGENT ROLLUP -->
  <section class="obs-panel">
    <div class="obs-h">PER-AGENT</div>
    {#if agents.length === 0}
      <div class="obs-empty">no agent activity</div>
    {:else}
      <table class="obs-table">
        <thead><tr><th>agent</th><th>calls</th><th>avg</th><th>$</th><th>errors</th></tr></thead>
        <tbody>
          {#each agents as a, ai (a.agent ?? ai)}
            <tr>
              <td><code class="obs-code">{a.agent ?? '—'}</code></td>
              <td>{a.calls ?? 0}</td>
              <td>{fmtMs(a.avg_ms)}</td>
              <td>{fmtCost(a.cost_usd)}</td>
              <td class:obs-bad={Number(a.errors) > 0}>{a.errors ?? 0}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
</div>

<style>
  .obs-root { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--pw-ink, #2c2a26); padding: 0; }
  .obs-pagehead { margin-bottom: 18px; }
  .obs-pagetitle { font-family: var(--pw-serif, Georgia, serif); font-size: 24px; font-weight: 600; color: var(--pw-ink, #2c2a26); margin: 0 0 4px; }
  .obs-pagesub { color: var(--pw-muted, #877f74); font-size: 13px; margin: 0; }

  .obs-filters { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
  .obs-pills { display: flex; gap: 4px; flex-wrap: wrap; }
  .obs-pill { background: transparent; border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 4px 12px; font-family: inherit; font-size: 11px; cursor: pointer; color: var(--pw-ink-soft, #4a4438); text-transform: lowercase; }
  .obs-pill:hover { border-color: var(--pw-accent, #c96342); color: var(--pw-accent, #c96342); }
  .obs-pill-on { background: var(--pw-accent, #c96342); color: #fff; border-color: var(--pw-accent, #c96342); }
  .obs-pill-on:hover { color: #fff; }
  .obs-refresh { margin-left: auto; }
  .obs-pill:disabled { opacity: 0.5; cursor: default; }

  .obs-strip { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; background: var(--pw-bg-alt, #f6f2ea); border: 1px solid var(--pw-border, #e5ddcf); padding: 10px 14px; margin-bottom: 12px; font-size: 13px; }
  .obs-strip-ctx { font-size: 12px; }
  .obs-strip-label { font-size: 10px; letter-spacing: 0.08em; color: var(--pw-muted, #877f74); margin-right: 4px; }
  .obs-stat b { color: var(--pw-ink, #2c2a26); font-weight: 700; }
  .obs-sep { color: var(--pw-muted, #877f74); }
  .obs-bad b, .obs-bad { color: #c0392b; }
  .obs-warn b { color: #a06000; }
  .obs-warnmark { color: #a06000; }
  .obs-pending { color: var(--pw-muted, #877f74); font-style: italic; }

  .obs-err-banner { color: #c0392b; font-size: 12px; padding: 8px 0; }

  .obs-panel { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e5ddcf); padding: 14px 16px; margin-bottom: 14px; }
  .obs-h { color: var(--pw-accent, #c96342); font-weight: 700; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 10px; }
  .obs-empty { color: var(--pw-muted, #877f74); font-size: 13px; padding: 6px 0; }
  .obs-empty-sm { font-size: 11px; padding: 4px 0 4px 20px; }
  .obs-muted { color: var(--pw-muted, #877f74); }

  .obs-tracelist { display: flex; flex-direction: column; }
  .obs-trace { border-bottom: 1px solid var(--pw-border, #ece6d9); }
  .obs-trace-row { display: grid; grid-template-columns: 16px 130px 1fr 56px 48px 56px 22px; align-items: center; gap: 8px; width: 100%; background: transparent; border: none; cursor: pointer; padding: 7px 4px; font-family: inherit; font-size: 12px; text-align: left; color: var(--pw-ink, #2c2a26); }
  .obs-trace-row:hover { background: rgba(201, 99, 66, 0.04); }
  .obs-tw { color: var(--pw-muted, #877f74); }
  .obs-time { color: var(--pw-muted, #877f74); font-size: 11px; white-space: nowrap; }
  .obs-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .obs-slug { color: var(--pw-muted, #877f74); }
  .obs-dur, .obs-tok, .obs-cost { text-align: right; font-size: 11px; color: var(--pw-ink-soft, #4a4438); white-space: nowrap; }
  .obs-pill-status { text-align: center; font-weight: 700; }
  .obs-pill-status[data-s="error"] { color: #c0392b; }
  .obs-pill-status[data-s="running"] { color: #a06000; }
  .obs-pill-status[data-s="done"], .obs-pill-status[data-s="ok"], .obs-pill-status:not([data-s="error"]):not([data-s="running"]) { color: var(--pw-accent, #c96342); }

  .obs-error-text { color: #c0392b; font-size: 11px; padding: 4px 4px 6px 24px; word-break: break-word; }
  .obs-child-error { padding-left: 0; }

  .obs-children { padding: 2px 0 8px 24px; }
  .obs-child { display: grid; grid-template-columns: 1fr 56px 48px 22px; align-items: center; gap: 8px; padding: 4px 4px; font-size: 11px; border-left: 2px solid var(--pw-border, #e5ddcf); padding-left: 10px; margin-left: 2px; }
  .obs-child-err { border-left-color: #c0392b; }
  .obs-child-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--pw-ink-soft, #4a4438); }

  .obs-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .obs-table th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted, #877f74); padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #e5ddcf); font-weight: 600; }
  .obs-table td { padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #ece6d9); }
  .obs-table td:not(:first-child), .obs-table th:not(:first-child) { text-align: right; }
  .obs-code { background: #1a1614; color: #e8e3d6; padding: 2px 7px; font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
</style>
