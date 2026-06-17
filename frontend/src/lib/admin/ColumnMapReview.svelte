<script lang="ts">
  import Icon from '$lib/Icon.svelte';
  import { onMount } from 'svelte';

  // Single-agent product: the locked project slug. Matches the ingest API the
  // projects page already calls (/api/ingest/{project}/...).
  let { embedded = false, slug = 'citypharma' } = $props();

  function authHeaders(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }
  function jh(): Record<string, string> { return { 'Content-Type': 'application/json', ...authHeaders() }; }

  type ColMap = {
    project_slug: string;
    table_logical: string;
    logical_name: string;
    physical_col: string;
    confidence: number;
    source: 'seed' | 'auto' | 'confirmed' | 'manual';
    status: 'active' | 'pending' | 'rejected';
    created_at?: string;
    updated_at?: string;
  };

  const base = `/api/ingest/${slug}/colmap`;
  const eventsBase = `/api/ingest/${slug}/schema-events`;

  type SchemaEvent = {
    table_name: string;
    event: 'adapt' | 'rebuild' | 'empty' | 'remap_confirmed' | string;
    detail: any;
    created_at?: string;
  };

  let maps = $state<ColMap[]>([]);
  let pending = $state<ColMap[]>([]);
  let pendingCount = $state(0);
  let loading = $state(true);
  let busyKey = $state(''); // `${table_logical}|${logical_name}` of the row being decided
  let msg = $state('');
  let mapInput = $state<Record<string, string>>({}); // manual "map to…" text per row key

  let events = $state<SchemaEvent[]>([]);
  let eventsLoading = $state(true);
  let showEvents = $state(true); // collapsible Schema Changes section

  function rowKey(m: ColMap): string { return `${m.table_logical}|${m.logical_name}`; }
  function pct(c: number): string {
    if (c === null || c === undefined || isNaN(c)) return '—';
    return `${Math.round(c * 100)}%`;
  }

  // ── Schema Changes feed helpers ────────────────────────────────────────────
  function eventIcon(ev: string): string {
    switch (ev) {
      case 'adapt': return 'git-branch';
      case 'rebuild': return 'refresh';
      case 'empty': return 'ban';
      case 'remap_confirmed': return 'check-circle';
      default: return 'circle';
    }
  }

  function n(v: any): number { return Array.isArray(v) ? v.length : 0; }

  // Human one-line summary derived from the event detail.
  function eventSummary(e: SchemaEvent): string {
    const d = e.detail || {};
    if (e.event === 'adapt') {
      const parts: string[] = [];
      if (n(d.added)) parts.push(`+${n(d.added)} cols`);
      if (n(d.removed)) parts.push(`-${n(d.removed)}`);
      if (n(d.retyped)) parts.push(`${n(d.retyped)} retyped`);
      if (n(d.renamed)) parts.push(`${n(d.renamed)} renamed`);
      const mode = d.mode ? ` (${d.mode})` : '';
      return (parts.length ? parts.join(', ') : 'no column changes') + mode;
    }
    if (e.event === 'rebuild') {
      if (d.skipped) {
        const missing = n(d.missing) ? ` [${(d.missing || []).join(', ')}]` : '';
        return `rebuild skipped${d.reason ? `: ${d.reason}` : ''}${missing}`;
      }
      const bits: string[] = [];
      if (d.rows !== undefined && d.rows !== null) bits.push(`${d.rows} rows`);
      if (d.art !== undefined && d.art !== null) bits.push(`${d.art} art`);
      if (d.stock !== undefined && d.stock !== null) bits.push(`${d.stock} stock`);
      return `${e.table_name} rebuilt${bits.length ? ` (${bits.join(', ')})` : ''}`;
    }
    if (e.event === 'empty') {
      return `empty file — kept previous data${d.reason ? `: ${d.reason}` : ''}`;
    }
    if (e.event === 'remap_confirmed') {
      const ln = d.logical_name || '?';
      const pc = d.physical_col || '?';
      const dec = d.decision ? ` (${d.decision})` : '';
      return `mapping confirmed: ${ln} -> ${pc}${dec}`;
    }
    // Unknown event — show a compact JSON-ish hint.
    try { return JSON.stringify(d).slice(0, 120); } catch { return ''; }
  }

  // Short relative timestamp (e.g. "5m ago", "2h ago", "3d ago").
  function shortTs(iso?: string): string {
    if (!iso) return '';
    const t = Date.parse(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
    if (isNaN(t)) return '';
    const s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 45) return 'just now';
    const m = Math.round(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}h ago`;
    const dy = Math.round(h / 24);
    return `${dy}d ago`;
  }

  async function loadEvents() {
    eventsLoading = true;
    try {
      const r = await fetch(`${eventsBase}?limit=50`, { headers: authHeaders() });
      if (r.ok) {
        const d = await r.json();
        events = d.events || [];
      }
    } catch (e) {
      // fail-soft: leave events as-is, empty state will show
    }
    eventsLoading = false;
  }

  // Active mappings grouped by table_logical, sorted within group by logical_name.
  const activeMaps = $derived(maps.filter((m) => m.status === 'active'));
  const activeGroups = $derived.by(() => {
    const g: Record<string, ColMap[]> = {};
    for (const m of activeMaps) {
      (g[m.table_logical] ||= []).push(m);
    }
    for (const k of Object.keys(g)) {
      g[k].sort((a, b) => a.logical_name.localeCompare(b.logical_name));
    }
    return Object.keys(g).sort().map((k) => ({ table: k, rows: g[k] }));
  });

  async function load() {
    loading = true;
    try {
      const r = await fetch(base, { headers: authHeaders() });
      if (r.ok) {
        const d = await r.json();
        maps = d.maps || [];
        pending = d.pending || [];
        pendingCount = d.pending_count ?? pending.length;
      } else {
        msg = `Failed to load mappings (${r.status})`;
      }
    } catch (e) {
      msg = 'Failed to load mappings';
    }
    loading = false;
  }

  async function decide(m: ColMap, decision: 'confirm' | 'reject' | 'map', physical_col?: string) {
    const key = rowKey(m);
    busyKey = key;
    msg = '';
    try {
      const body: any = { table_logical: m.table_logical, logical_name: m.logical_name, decision };
      if (decision === 'map') body.physical_col = physical_col;
      const r = await fetch(`${base}/decide`, { method: 'POST', headers: jh(), body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        const verb = decision === 'reject' ? 'Rejected' : decision === 'map' ? 'Mapped' : 'Confirmed';
        msg = d.rebuilt ? `${verb} — shop_flat rebuilt` : `${verb} · saved`;
        if (decision === 'map') mapInput[key] = '';
        await load();
      } else {
        msg = d.detail || d.error || `Decision failed (${r.status})`;
      }
    } catch (e) {
      msg = 'Decision failed';
    }
    busyKey = '';
  }

  function mapTo(m: ColMap) {
    const v = (mapInput[rowKey(m)] || '').trim();
    if (!v) return;
    decide(m, 'map', v);
  }

  onMount(() => { load(); loadEvents(); });
</script>

<div class="cmr" class:embedded>
  <div class="cmr-head">
    <div>
      <div class="cmr-title">Column Mapping Review</div>
      <div class="cmr-sub">Confirm or reject how detected physical columns map to logical names. Ambiguous renames wait here until you decide.</div>
    </div>
    <button class="cmr-btn" disabled={loading} onclick={() => { load(); loadEvents(); }}><Icon name="refresh" size={16} /> Refresh</button>
  </div>

  {#if msg}<div class="cmr-msg">{msg}</div>{/if}

  <!-- PENDING REVIEW QUEUE -->
  <div class="cmr-group">Review queue {#if pendingCount}<span class="cmr-count">{pendingCount}</span>{/if}</div>

  {#if loading}
    <div class="cmr-empty">loading…</div>
  {:else if pendingCount === 0}
    <div class="cmr-empty"><Icon name="check" size={16} /> No mappings need review</div>
  {:else}
    {#each pending as m (rowKey(m))}
      <div class="cmr-card">
        <div class="cmr-card-line">
          <code>{m.physical_col}</code> looks like logical <strong>{m.logical_name}</strong>
          <span class="cmr-dim">({m.table_logical})</span>
          <span class="cmr-conf">confidence {pct(m.confidence)}</span>
        </div>
        <div class="cmr-actions">
          <button class="cmr-btn cmr-ok" disabled={busyKey === rowKey(m)} onclick={() => decide(m, 'confirm')}>
            <Icon name="check" size={16} /> Confirm
          </button>
          <button class="cmr-btn cmr-no" disabled={busyKey === rowKey(m)} onclick={() => decide(m, 'reject')}>
            <Icon name="x" size={16} /> Reject
          </button>
          <span class="cmr-spacer"></span>
          <input
            class="cmr-input"
            placeholder="physical column…"
            bind:value={mapInput[rowKey(m)]}
            onkeydown={(e) => { if (e.key === 'Enter') mapTo(m); }}
          />
          <button class="cmr-btn" disabled={busyKey === rowKey(m) || !(mapInput[rowKey(m)] || '').trim()} onclick={() => mapTo(m)}>
            <Icon name="arrow-right" size={16} /> Map to…
          </button>
        </div>
      </div>
    {/each}
  {/if}

  <!-- ACTIVE MAPPINGS (transparency) -->
  <div class="cmr-group">Active mappings</div>
  {#if !loading && activeGroups.length === 0}
    <div class="cmr-empty">No active mappings yet.</div>
  {:else}
    {#each activeGroups as grp (grp.table)}
      <div class="cmr-tablegroup">
        <div class="cmr-tablename"><Icon name="database" size={16} /> {grp.table}</div>
        <table class="cmr-table">
          <thead>
            <tr><th>Logical</th><th>Physical</th><th>Source</th><th>Confidence</th></tr>
          </thead>
          <tbody>
            {#each grp.rows as m (rowKey(m))}
              <tr>
                <td><strong>{m.logical_name}</strong></td>
                <td><code>{m.physical_col}</code></td>
                <td><span class="cmr-tag cmr-src-{m.source}">{m.source}</span></td>
                <td class="cmr-dim">{pct(m.confidence)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/each}
  {/if}

  <!-- SCHEMA CHANGES (collapsible timeline) -->
  <button class="cmr-group cmr-group-btn" onclick={() => (showEvents = !showEvents)}>
    <Icon name={showEvents ? 'chevron-down' : 'chevron-right'} size={14} />
    Schema changes
    {#if events.length}<span class="cmr-count cmr-count-mut">{events.length}</span>{/if}
  </button>

  {#if showEvents}
    {#if eventsLoading}
      <div class="cmr-empty">loading…</div>
    {:else if events.length === 0}
      <div class="cmr-empty"><Icon name="inbox" size={16} /> No schema changes yet.</div>
    {:else}
      <div class="cmr-timeline">
        {#each events as e, i (i)}
          <div class="cmr-evt cmr-evt-{e.event}">
            <span class="cmr-evt-ico"><Icon name={eventIcon(e.event)} size={15} /></span>
            <span class="cmr-evt-tbl"><code>{e.table_name}</code></span>
            <span class="cmr-evt-sum">{eventSummary(e)}</span>
            <span class="cmr-evt-ts">{shortTs(e.created_at)}</span>
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<style>
  .cmr { padding: 4px 2px; }
  .cmr-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; }
  .cmr-title { font-size: 15px; font-weight: 800; }
  .cmr-sub { font-size: 12px; color: var(--pw-muted, #8a8175); margin-top: 3px; max-width: 560px; }
  .cmr-msg { font-size: 12px; padding: 7px 11px; background: #f4f1ea; border-radius: var(--pw-radius-sm, 8px); margin-bottom: 10px; }
  .cmr-group { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; color: var(--pw-muted, #8a8175); margin: 16px 0 8px; display: flex; align-items: center; gap: 8px; }
  .cmr-count { background: var(--pw-accent, #c2683f); color: #fff; font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: var(--pw-radius-pill, 999px); }
  .cmr-empty { font-size: 13px; color: var(--pw-muted, #8a8175); padding: 18px 4px; display: flex; align-items: center; gap: 6px; }
  .cmr-card { border: 1px solid var(--pw-border, #e6e1d7); border-radius: var(--pw-radius-sm, 8px); padding: 11px 12px; margin-bottom: 8px; background: var(--pw-bg, #fff); }
  .cmr-card-line { font-size: 13px; line-height: 1.6; }
  .cmr-card-line code, .cmr-table code { background: #f3ece1; color: #9a4a2f; font-size: 12px; padding: 1px 6px; border-radius: 5px; }
  .cmr-dim { color: var(--pw-muted, #8a8175); }
  .cmr-conf { color: var(--pw-muted, #8a8175); font-size: 12px; margin-left: 6px; }
  .cmr-actions { display: flex; align-items: center; gap: 7px; margin-top: 9px; flex-wrap: wrap; }
  .cmr-spacer { flex: 1; }
  .cmr-btn { font-size: 12px; font-weight: 600; padding: 6px 11px; border-radius: var(--pw-radius-sm, 8px); border: 1px solid var(--pw-border, #e6e1d7); background: var(--pw-bg, #fff); color: var(--pw-fg, #1c1b18); cursor: pointer; display: inline-flex; align-items: center; gap: 5px; }
  .cmr-btn:hover:not(:disabled) { border-color: var(--pw-accent, #c2683f); }
  .cmr-btn:disabled { opacity: .5; cursor: default; }
  .cmr-ok { background: #3f8f5f; color: #fff; border-color: #3f8f5f; }
  .cmr-no { color: #c0492f; border-color: #e7c4ba; }
  .cmr-input { font-size: 12px; padding: 5px 9px; border: 1px solid var(--pw-border, #e6e1d7); border-radius: var(--pw-radius-sm, 8px); background: var(--pw-bg, #fff); color: var(--pw-fg, #1c1b18); width: 150px; }
  .cmr-input:focus { outline: none; border-color: var(--pw-accent, #c2683f); }
  .cmr-tablegroup { margin-bottom: 14px; }
  .cmr-tablename { font-size: 12px; font-weight: 700; margin-bottom: 5px; display: flex; align-items: center; gap: 6px; }
  .cmr-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .cmr-table th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: .05em; color: var(--pw-muted, #8a8175); font-weight: 700; padding: 5px 8px; border-bottom: 1px solid var(--pw-border, #e6e1d7); }
  .cmr-table td { padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #f0ece4); }
  .cmr-tag { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; padding: 1px 7px; border-radius: var(--pw-radius-pill, 999px); background: #f3ece1; color: #9a4a2f; }
  .cmr-src-confirmed { background: #e3f1e8; color: #2f7a4d; }
  .cmr-src-manual { background: #e6eef7; color: #2f5a9a; }
  .cmr-src-seed { background: #f0ece4; color: #8a8175; }
  .cmr-src-auto { background: #faf0e4; color: #a86a1f; }

  /* Schema changes timeline */
  .cmr-group-btn { background: none; border: none; cursor: pointer; padding: 0; width: 100%; text-align: left; }
  .cmr-group-btn:hover { color: var(--pw-fg, #1c1b18); }
  .cmr-count-mut { background: var(--pw-muted, #8a8175); }
  .cmr-timeline { display: flex; flex-direction: column; }
  .cmr-evt { display: flex; align-items: center; gap: 9px; font-size: 12.5px; padding: 7px 4px; border-bottom: 1px solid var(--pw-border, #f0ece4); }
  .cmr-evt:last-child { border-bottom: none; }
  .cmr-evt-ico { display: inline-flex; color: var(--pw-muted, #8a8175); flex-shrink: 0; }
  .cmr-evt-adapt .cmr-evt-ico { color: #2f5a9a; }
  .cmr-evt-rebuild .cmr-evt-ico { color: #2f7a4d; }
  .cmr-evt-empty .cmr-evt-ico { color: #c0492f; }
  .cmr-evt-remap_confirmed .cmr-evt-ico { color: #a86a1f; }
  .cmr-evt-tbl { flex-shrink: 0; }
  .cmr-evt-tbl code { background: #f3ece1; color: #9a4a2f; font-size: 11.5px; padding: 1px 6px; border-radius: 5px; }
  .cmr-evt-sum { flex: 1; min-width: 0; color: var(--pw-fg, #1c1b18); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cmr-evt-ts { flex-shrink: 0; font-size: 11px; color: var(--pw-muted, #8a8175); white-space: nowrap; }
</style>
