<script lang="ts">
  import { onMount } from 'svelte';

  type Sched = {
    id: string;
    name?: string;
    kind?: string;
    cron?: string;
    next_run?: string;
    agent_target?: string;
    run_count?: number;
    last_result?: string;
    enabled?: boolean;
  };

  let schedules = $state<Sched[]>([]);
  let loading = $state(true);
  let filter = $state<'all' | 'enabled' | 'disabled'>('all');

  const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);
  const authHeaders = (): Record<string, string> => {
    const t = token();
    return t ? { Authorization: `Bearer ${t}` } : {};
  };

  async function load() {
    loading = true;
    try {
      const r = await fetch('/api/agent-schedules?enabled_only=false&limit=100', { headers: authHeaders() });
      if (r.ok) {
        const j = await r.json();
        schedules = Array.isArray(j) ? j : j.schedules || j.items || [];
      }
    } catch {}
    loading = false;
  }

  async function runNow(s: Sched) {
    try {
      const r = await fetch(`/api/agent-schedules/${s.id}/run-now`, { method: 'POST', headers: authHeaders() });
      if (r.ok) load();
    } catch {}
  }

  async function toggleEnabled(s: Sched) {
    try {
      const r = await fetch(`/api/agent-schedules/${s.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ enabled: !s.enabled })
      });
      if (r.ok) load();
    } catch {}
  }

  async function del(s: Sched) {
    if (!confirm(`Delete schedule "${s.name || s.id}"?`)) return;
    try {
      const r = await fetch(`/api/agent-schedules/${s.id}`, { method: 'DELETE', headers: authHeaders() });
      if (r.ok) load();
    } catch {}
  }

  function fmtDate(s?: string) { if (!s) return '—'; try { return new Date(s).toLocaleString(); } catch { return s; } }
  function kindChip(k?: string) {
    const v = (k || '').toLowerCase();
    if (v.includes('eval')) return 'chip-blue';
    if (v.includes('workflow')) return 'chip-purple';
    if (v.includes('learn')) return 'chip-green';
    return 'chip-gray';
  }

  const filtered = $derived(schedules.filter(s => {
    if (filter === 'enabled') return s.enabled;
    if (filter === 'disabled') return !s.enabled;
    return true;
  }));

  onMount(load);
</script>

<div class="wrap">
  <div class="toolbar">
    <div class="filters">
      <button class="fchip" class:on={filter === 'all'} onclick={() => (filter = 'all')}>all</button>
      <button class="fchip" class:on={filter === 'enabled'} onclick={() => (filter = 'enabled')}>enabled</button>
      <button class="fchip" class:on={filter === 'disabled'} onclick={() => (filter = 'disabled')}>disabled</button>
    </div>
    <button class="btn-ghost" onclick={load}>↻ Refresh</button>
  </div>

  {#if loading}
    <div class="empty">Loading…</div>
  {:else if !filtered.length}
    <div class="empty">No schedules.</div>
  {:else}
    <table class="tbl">
      <thead>
        <tr><th>NAME</th><th>KIND</th><th>CRON</th><th>NEXT_RUN</th><th>AGENT_TARGET</th><th>RUN_COUNT</th><th>LAST RESULT</th><th>ENABLED</th><th>ACTIONS</th></tr>
      </thead>
      <tbody>
        {#each filtered as s}
          <tr>
            <td>{s.name || s.id}</td>
            <td><span class="chip {kindChip(s.kind)}">{s.kind || '—'}</span></td>
            <td class="mono">{s.cron || '—'}</td>
            <td class="muted">{fmtDate(s.next_run)}</td>
            <td>{s.agent_target || '—'}</td>
            <td>{s.run_count ?? 0}</td>
            <td>{s.last_result || '—'}</td>
            <td>
              <span class="dot" class:on={s.enabled}></span>
              {s.enabled ? 'on' : 'off'}
            </td>
            <td class="actions">
              <button class="btn-link" onclick={() => runNow(s)}>Run now</button>
              <button class="btn-link" onclick={() => toggleEnabled(s)}>{s.enabled ? 'Disable' : 'Enable'}</button>
              <button class="btn-link danger" onclick={() => del(s)}>Delete</button>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .wrap { display: flex; flex-direction: column; gap: 12px; }
  .toolbar { display: flex; justify-content: space-between; align-items: center; }
  .filters { display: flex; gap: 6px; }
  .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; }
  .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; background: var(--pw-surface, #faf9f5); }
  .fchip { background: var(--pw-bg-alt, #f1ede4); border: 1px solid var(--pw-border, #e7e3da); padding: 4px 10px; border-radius: 0; font-size: 11px; cursor: pointer; color: var(--pw-ink, #2c2a26); text-transform: uppercase; letter-spacing: 0.04em; }
  .fchip.on { background: var(--pw-accent, #c96342); color: white; border-color: var(--pw-accent, #c96342); }
  .btn-ghost { background: none; border: 1px solid var(--pw-border, #e7e3da); padding: 6px 10px; font-size: 12px; cursor: pointer; border-radius: 0; color: var(--pw-ink, #2c2a26); }
  .btn-link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font-size: 12px; font-weight: 600; padding: 0 4px; }
  .btn-link.danger { color: #dc2626; }
  .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; }
  .tbl th { text-align: left; padding: 10px 12px; background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); border-bottom: 1px solid var(--pw-border, #e7e3da); }
  .tbl td { padding: 10px 12px; border-bottom: 1px solid var(--pw-border, #e7e3da); font-size: 13px; }
  .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; }
  .chip { display: inline-block; padding: 2px 8px; border-radius: 0; font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.04em; }
  .chip-blue { background: rgba(58, 141, 255, 0.14); color: #1e40af; }
  .chip-purple { background: rgba(155, 109, 255, 0.14); color: #6b21a8; }
  .chip-green { background: rgba(22, 163, 74, 0.14); color: #15803d; }
  .chip-gray { background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink-soft, #87837a); }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #999; margin-right: 6px; vertical-align: middle; }
  .dot.on { background: #16a34a; }
  .actions { display: flex; gap: 4px; flex-wrap: wrap; }
</style>
