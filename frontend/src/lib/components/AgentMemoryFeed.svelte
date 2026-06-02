<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { agentMemory, type AgentMemoryEvent } from '$lib/api';

  const TYPES = ['all', 'query', 'decision', 'feedback', 'action', 'observation'] as const;
  type Filter = typeof TYPES[number];

  let events = $state<AgentMemoryEvent[]>([]);
  let nextCursor = $state<string | undefined>(undefined);
  let filter = $state<Filter>('all');
  let loading = $state(false);
  let loadingMore = $state(false);
  let error = $state<string | null>(null);
  let refreshTimer: ReturnType<typeof setInterval> | null = null;

  const visible = $derived(
    filter === 'all' ? events : events.filter((e) => e.event_type === filter)
  );

  async function loadFirst() {
    loading = true;
    error = null;
    try {
      const res = await agentMemory(undefined, 20);
      events = res.events || [];
      nextCursor = res.next_cursor;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load memory';
    } finally {
      loading = false;
    }
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    loadingMore = true;
    try {
      const res = await agentMemory(nextCursor, 20);
      events = [...events, ...(res.events || [])];
      nextCursor = res.next_cursor;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load more';
    } finally {
      loadingMore = false;
    }
  }

  async function refresh() {
    try {
      const res = await agentMemory(undefined, 20);
      const incoming = res.events || [];
      // Merge: prepend new events not already known
      const known = new Set(events.map((e) => e.id));
      const fresh = incoming.filter((e) => !known.has(e.id));
      if (fresh.length > 0) events = [...fresh, ...events];
      if (!nextCursor) nextCursor = res.next_cursor;
    } catch {
      // silent on background refresh
    }
  }

  function relTime(ts: string): string {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
    return d.toLocaleDateString();
  }

  function summary(ev: AgentMemoryEvent): string {
    const p = ev.payload || {};
    const pick =
      (p.query as string) ||
      (p.summary as string) ||
      (p.action as string) ||
      (p.message as string) ||
      (p.text as string);
    if (pick && typeof pick === 'string') return pick.slice(0, 120);
    try {
      const s = JSON.stringify(p);
      return s.slice(0, 80);
    } catch {
      return '—';
    }
  }

  function badgeStyle(type: string): string {
    switch (type) {
      case 'query':
        return 'background: rgba(58,141,255,0.12); color: #2670d6;';
      case 'decision':
        return 'background: rgba(16,185,129,0.14); color: #047857;';
      case 'feedback':
        return 'background: rgba(201,99,66,0.14); color: var(--pw-accent);';
      case 'action':
        return 'background: rgba(44,42,38,0.10); color: var(--pw-ink);';
      case 'observation':
        return 'background: rgba(0,0,0,0.06); color: var(--pw-muted);';
      default:
        return 'background: rgba(0,0,0,0.06); color: var(--pw-muted);';
    }
  }

  onMount(() => {
    loadFirst();
    refreshTimer = setInterval(refresh, 30000);
  });

  onDestroy(() => {
    if (refreshTimer) clearInterval(refreshTimer);
  });
</script>

<div class="amf">
  <div class="amf-head">
    <div class="amf-title">Agent Memory</div>
    <select class="amf-filter" bind:value={filter}>
      {#each TYPES as t}
        <option value={t}>{t === 'all' ? 'All' : t}</option>
      {/each}
    </select>
  </div>

  {#if loading}
    <div class="amf-empty">Loading…</div>
  {:else if error}
    <div class="amf-empty amf-err">{error}</div>
  {:else if visible.length === 0}
    <div class="amf-empty">No memories yet. Use Dash and your agent learns from you.</div>
  {:else}
    <ul class="amf-list">
      {#each visible as ev (ev.id)}
        <li class="amf-row">
          <span class="amf-time" title={ev.ts}>{relTime(ev.ts)}</span>
          <span class="amf-badge" style={badgeStyle(ev.event_type)}>{ev.event_type}</span>
          <span class="amf-sum">{summary(ev)}</span>
        </li>
      {/each}
    </ul>

    {#if filter === 'all' && nextCursor}
      <div class="amf-more">
        <button class="amf-btn" onclick={loadMore} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : 'Load more'}
        </button>
      </div>
    {/if}
  {/if}
</div>

<style>
  .amf {
    background: var(--pw-surface, #fff);
    border: 1px solid var(--pw-border);
    border-radius: var(--pw-radius-sm, 8px);
    padding: 12px;
    font-size: 11px;
    color: var(--pw-ink);
  }
  .amf-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    gap: 8px;
  }
  .amf-title {
    font-weight: 600;
    font-size: 11px;
    color: var(--pw-ink);
  }
  .amf-filter {
    background: var(--pw-bg-alt);
    border: 1px solid var(--pw-border);
    border-radius: 0;
    padding: 4px 8px;
    font-size: 11px;
    color: var(--pw-ink);
    text-transform: capitalize;
  }
  .amf-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
  }
  .amf-row {
    display: grid;
    grid-template-columns: 64px auto 1fr;
    align-items: center;
    gap: 8px;
    min-height: 36px;
    padding: 6px 4px;
    border-bottom: 1px solid var(--pw-border);
  }
  .amf-row:last-child {
    border-bottom: none;
  }
  .amf-time {
    font-size: 11px;
    color: var(--pw-muted);
    white-space: nowrap;
  }
  .amf-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 0;
    font-size: 10.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    line-height: 1.6;
    white-space: nowrap;
  }
  .amf-sum {
    font-size: 12.5px;
    color: var(--pw-ink);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .amf-empty {
    padding: 18px 8px;
    color: var(--pw-muted);
    font-size: 12.5px;
    text-align: center;
  }
  .amf-err {
    color: #b91c1c;
  }
  .amf-more {
    margin-top: 10px;
    display: flex;
    justify-content: center;
  }
  .amf-btn {
    background: var(--pw-bg-alt);
    border: 1px solid var(--pw-border);
    border-radius: 0;
    padding: 6px 14px;
    font-size: 11px;
    font-weight: 600;
    color: var(--pw-ink);
    cursor: pointer;
    transition: background 0.15s;
  }
  .amf-btn:hover:not(:disabled) {
    background: var(--pw-border);
  }
  .amf-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
</style>
