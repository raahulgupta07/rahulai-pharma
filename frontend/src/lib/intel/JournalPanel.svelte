<script lang="ts">
  import { onMount } from 'svelte';
  import { base } from '$app/paths';
  import { dashFetch } from '$lib/api';

  let { slug }: { slug: string } = $props();

  function today(): string {
    const d = new Date();
    return d.toISOString().slice(0, 10);
  }

  function shift(dateStr: string, days: number): string {
    const d = new Date(dateStr + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
  }

  let currentDate = $state(today());
  let entry = $state<any | null>(null);
  let entries = $state<any[]>([]);
  let loading = $state(false);
  let err = $state('');
  let regenerating = $state(false);

  async function loadEntry() {
    err = '';
    loading = true;
    entry = null;
    try {
      const r = await dashFetch(`/api/journal/${slug}?date=${currentDate}`);
      if (r.status === 404) {
        entry = null;
      } else if (!r.ok) {
        err = `failed: ${r.status}`;
      } else {
        entry = await r.json();
      }
    } catch (e: any) {
      err = String(e?.message || e);
    } finally {
      loading = false;
    }
  }

  async function loadList() {
    try {
      const r = await dashFetch(`/api/journal/${slug}/list?limit=30`);
      if (r.ok) {
        const data = await r.json();
        entries = data.entries || [];
      }
    } catch {}
  }

  async function regenerate() {
    if (regenerating) return;
    regenerating = true;
    err = '';
    try {
      const r = await dashFetch(`/api/journal/${slug}/generate?date=${currentDate}`, { method: 'POST' });
      if (!r.ok) {
        err = `generate failed: ${r.status}`;
      } else {
        await loadEntry();
        await loadList();
      }
    } catch (e: any) {
      err = String(e?.message || e);
    } finally {
      regenerating = false;
    }
  }

  function pickDate(d: string) { currentDate = d; loadEntry(); }
  function prev() { pickDate(shift(currentDate, -1)); }
  function next() { pickDate(shift(currentDate, 1)); }

  function fmtDate(s: string): string {
    if (!s) return '';
    const d = new Date(s + 'T00:00:00Z');
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function renderMd(md: string): string {
    if (!md) return '';
    let html = md.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    const lines = html.split('\n');
    const out: string[] = [];
    let inList = false;
    for (const line of lines) {
      const m = line.match(/^\s*[-*]\s+(.*)$/);
      if (m) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push(`<li>${m[1]}</li>`);
      } else {
        if (inList) { out.push('</ul>'); inList = false; }
        if (line.trim()) out.push(`<p>${line}</p>`);
      }
    }
    if (inList) out.push('</ul>');
    return out.join('\n');
  }

  let stats = $derived(entry?.stats || {});
  let uploads = $derived(stats?.uploads || {});
  let anomalies = $derived(Array.isArray(stats?.anomalies) ? stats.anomalies : []);
  let kpis = $derived(stats?.kpi_diffs || {});

  onMount(() => { loadEntry(); loadList(); });
</script>

<div class="jp-shell">
  <aside class="jp-rail">
    <div class="jp-rail-head">Past 30 days</div>
    {#if entries.length === 0}
      <div class="jp-empty-rail">No entries yet.</div>
    {:else}
      {#each entries as e}
        <button class="jp-rail-item" class:active={e.journal_date === currentDate} onclick={() => pickDate(e.journal_date)}>
          <div class="jp-rail-date">{fmtDate(e.journal_date)}</div>
          <div class="jp-rail-meta">{(e.stats?.queries ?? 0)} queries</div>
        </button>
      {/each}
    {/if}
  </aside>

  <main class="jp-main">
    <div class="jp-toolbar">
      <button class="jp-arrow" onclick={prev} title="Previous day">‹</button>
      <input type="date" class="jp-date" bind:value={currentDate} onchange={loadEntry} />
      <button class="jp-arrow" onclick={next} title="Next day">›</button>
      <div class="jp-spacer"></div>
      <button class="jp-btn" onclick={regenerate} disabled={regenerating}>
        {regenerating ? 'Regenerating…' : 'Regenerate'}
      </button>
    </div>

    {#if err}<div class="jp-err">{err}</div>{/if}

    {#if loading}
      <div class="jp-loading">Loading…</div>
    {:else if !entry}
      <div class="jp-empty">
        <h3>No journal yet for {fmtDate(currentDate)}.</h3>
        <p>Click <strong>Regenerate</strong> to build one from today's activity.</p>
      </div>
    {:else}
      <h1 class="jp-title">{fmtDate(entry.journal_date)}</h1>

      <section class="jp-tiles">
        <div class="jp-tile">
          <div class="jp-tile-label">Queries</div>
          <div class="jp-tile-num">{stats?.queries ?? 0}</div>
        </div>
        <div class="jp-tile">
          <div class="jp-tile-label">Tables</div>
          <div class="jp-tile-num">{uploads?.tables ?? 0}</div>
        </div>
        <div class="jp-tile">
          <div class="jp-tile-label">Documents</div>
          <div class="jp-tile-num">{uploads?.documents ?? 0}</div>
        </div>
        <div class="jp-tile" class:warn={anomalies.length > 0}>
          <div class="jp-tile-label">Anomalies</div>
          <div class="jp-tile-num">{anomalies.length}</div>
        </div>
      </section>

      {#if Object.keys(kpis).length > 0}
        <section class="jp-section">
          <h3>KPI movement</h3>
          <ul class="jp-kpis">
            {#each Object.entries(kpis) as [k, v]}
              {@const dv = v as any}
              <li>
                <span class="jp-kpi-name">{k}</span>
                <span class="jp-kpi-val">
                  {dv?.current ?? '—'}
                  {#if dv?.pct != null}
                    <span class="jp-kpi-delta" class:up={dv.delta > 0} class:down={dv.delta < 0}>
                      {dv.delta > 0 ? '▲' : dv.delta < 0 ? '▼' : '━'} {Math.abs(dv.pct).toFixed(1)}%
                    </span>
                  {/if}
                </span>
              </li>
            {/each}
          </ul>
        </section>
      {/if}

      {#if anomalies.length > 0}
        <section class="jp-section">
          <h3>Anomalies</h3>
          <ul class="jp-anom">
            {#each anomalies as a}
              <li><strong>{a.table}</strong>: {a.rows_added} rows (avg 30d: {a.avg_30d}, z={a.z_score})</li>
            {/each}
          </ul>
        </section>
      {/if}

      <section class="jp-section">
        <h3>Summary</h3>
        <div class="jp-summary">
          {@html renderMd(entry.summary_md || '')}
        </div>
      </section>
    {/if}
  </main>
</div>

<style>
  .jp-shell { display: grid; grid-template-columns: 240px 1fr; min-height: calc(100vh - 220px); background: var(--pw-bg, #f7f4ec); border: 1px solid var(--pw-border, #e0d8c5); border-radius: 6px; overflow: hidden; }
  .jp-rail { border-right: 1px solid var(--pw-border, #e0d8c5); background: var(--pw-bg-alt, #efeadc); overflow-y: auto; padding: 12px 8px; max-height: calc(100vh - 220px); }
  .jp-rail-head { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft, #6b6557); padding: 4px 8px 8px; }
  .jp-rail-item { display: block; width: 100%; text-align: left; background: transparent; border: none; padding: 8px 10px; border-radius: 4px; cursor: pointer; border-left: 2px solid transparent; }
  .jp-rail-item:hover { background: rgba(201, 99, 66, 0.06); }
  .jp-rail-item.active { background: rgba(201, 99, 66, 0.12); border-left-color: var(--pw-accent, #c96342); }
  .jp-rail-date { font-weight: 600; font-size: 13px; }
  .jp-rail-meta { font-size: 11px; color: var(--pw-ink-soft, #6b6557); }
  .jp-empty-rail { padding: 12px; font-size: 12px; color: var(--pw-ink-soft, #6b6557); }
  .jp-main { padding: 20px 28px 40px; overflow-y: auto; max-height: calc(100vh - 220px); }
  .jp-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 18px; }
  .jp-arrow { width: 32px; height: 32px; border: 1px solid var(--pw-border, #e0d8c5); background: var(--pw-surface, #fff); border-radius: 4px; cursor: pointer; font-size: 18px; line-height: 1; }
  .jp-arrow:hover { background: var(--pw-bg-alt, #efeadc); }
  .jp-date { padding: 6px 10px; border: 1px solid var(--pw-border, #e0d8c5); border-radius: 4px; font-size: 13px; background: var(--pw-surface, #fff); }
  .jp-spacer { flex: 1; }
  .jp-btn { padding: 6px 14px; border: 1px solid var(--pw-accent, #c96342); background: var(--pw-accent, #c96342); color: #fff; border-radius: 4px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; cursor: pointer; }
  .jp-btn:disabled { opacity: 0.6; cursor: wait; }
  .jp-err { padding: 10px 14px; background: #fde8e1; color: #8a2a10; border-radius: 4px; margin-bottom: 14px; font-size: 13px; }
  .jp-loading, .jp-empty { padding: 36px; text-align: center; color: var(--pw-ink-soft, #6b6557); }
  .jp-empty h3 { margin: 0 0 6px; font-size: 16px; }
  .jp-empty p { margin: 0; font-size: 13px; }
  .jp-title { font-family: 'Source Serif Pro', Georgia, serif; font-size: 26px; font-weight: 600; margin: 0 0 16px; }
  .jp-tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 22px; }
  .jp-tile { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e0d8c5); border-radius: 6px; padding: 12px 14px; }
  .jp-tile.warn { border-color: var(--pw-accent, #c96342); }
  .jp-tile-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft, #6b6557); margin-bottom: 4px; }
  .jp-tile-num { font-family: 'Source Serif Pro', Georgia, serif; font-size: 26px; font-weight: 600; }
  .jp-section { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e0d8c5); border-radius: 6px; padding: 14px 18px; margin-bottom: 14px; }
  .jp-section h3 { margin: 0 0 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft, #6b6557); }
  .jp-kpis, .jp-anom { list-style: none; padding: 0; margin: 0; }
  .jp-kpis li, .jp-anom li { padding: 6px 0; border-bottom: 1px dashed var(--pw-border, #e0d8c5); display: flex; justify-content: space-between; font-size: 13px; }
  .jp-kpis li:last-child, .jp-anom li:last-child { border-bottom: none; }
  .jp-kpi-name { font-weight: 500; }
  .jp-kpi-delta { margin-left: 8px; font-size: 12px; }
  .jp-kpi-delta.up { color: #2d7a3a; }
  .jp-kpi-delta.down { color: #c0392b; }
  .jp-summary :global(p) { margin: 0 0 8px; line-height: 1.55; font-size: 14px; }
  .jp-summary :global(ul) { margin: 0; padding-left: 22px; }
  .jp-summary :global(li) { margin-bottom: 6px; line-height: 1.5; font-size: 14px; }
  .jp-summary :global(code) { background: var(--pw-bg-alt, #efeadc); padding: 1px 5px; border-radius: 3px; font-size: 12.5px; }
</style>
