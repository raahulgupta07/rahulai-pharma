<script lang="ts">
  import { onMount } from 'svelte';

  type Run = {
    run_id: string;
    workflow?: string;
    status?: string;
    trigger?: string;
    started_at?: string;
    duration_ms?: number;
    error?: string;
  };
  type Step = { name?: string; status?: string; duration_ms?: number; error?: string; output?: string };

  let runs = $state<Run[]>([]);
  let loading = $state(true);
  let expanded = $state<string | null>(null);
  let stepsByRun = $state<Record<string, Step[]>>({});
  let statusFilter = $state<string>('all');
  let triggerFilter = $state<string>('all');

  const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);
  const authHeaders = (): Record<string, string> => {
    const t = token();
    return t ? { Authorization: `Bearer ${t}` } : {};
  };

  async function load() {
    loading = true;
    try {
      const r = await fetch('/api/os/workflows/runs?limit=100', { headers: authHeaders() });
      if (r.ok) {
        const j = await r.json();
        runs = Array.isArray(j) ? j : j.runs || j.items || [];
      }
    } catch {}
    loading = false;
  }

  async function toggle(run: Run) {
    if (expanded === run.run_id) { expanded = null; return; }
    expanded = run.run_id;
    if (!stepsByRun[run.run_id]) {
      try {
        const r = await fetch(`/api/os/workflows/runs/${run.run_id}`, { headers: authHeaders() });
        if (r.ok) {
          const j = await r.json();
          stepsByRun[run.run_id] = j.steps || j.items || [];
          stepsByRun = { ...stepsByRun };
        }
      } catch {}
    }
  }

  function statusChipClass(s?: string) {
    const v = (s || '').toLowerCase();
    if (v === 'done' || v === 'success') return 'chip-green';
    if (v === 'running') return 'chip-amber';
    if (v === 'failed' || v === 'regressed' || v === 'error') return 'chip-red';
    return 'chip-gray';
  }

  function fmtDate(s?: string) { if (!s) return '—'; try { return new Date(s).toLocaleString(); } catch { return s; } }
  function fmtDur(ms?: number) { if (!ms) return '—'; if (ms < 1000) return ms + 'ms'; return (ms / 1000).toFixed(1) + 's'; }

  const filtered = $derived(runs.filter(r => {
    if (statusFilter !== 'all' && (r.status || '').toLowerCase() !== statusFilter) return false;
    if (triggerFilter !== 'all' && (r.trigger || '').toLowerCase() !== triggerFilter) return false;
    return true;
  }));

  const statuses = $derived(Array.from(new Set(runs.map(r => (r.status || '').toLowerCase()).filter(Boolean))));
  const triggers = $derived(Array.from(new Set(runs.map(r => (r.trigger || '').toLowerCase()).filter(Boolean))));

  onMount(load);
</script>

<div class="wrap">
  <div class="toolbar">
    <div class="filters">
      <span class="muted">Status:</span>
      <button class="fchip" class:on={statusFilter === 'all'} onclick={() => (statusFilter = 'all')}>all</button>
      {#each statuses as s}
        <button class="fchip" class:on={statusFilter === s} onclick={() => (statusFilter = s)}>{s}</button>
      {/each}
      <span class="muted" style="margin-left:12px">Trigger:</span>
      <button class="fchip" class:on={triggerFilter === 'all'} onclick={() => (triggerFilter = 'all')}>all</button>
      {#each triggers as t}
        <button class="fchip" class:on={triggerFilter === t} onclick={() => (triggerFilter = t)}>{t}</button>
      {/each}
    </div>
    <button class="btn-ghost" onclick={load}>↻ Refresh</button>
  </div>

  {#if loading}
    <div class="empty">Loading…</div>
  {:else if !filtered.length}
    <div class="empty">No runs.</div>
  {:else}
    <table class="tbl">
      <thead>
        <tr><th>RUN_ID</th><th>WORKFLOW</th><th>STATUS</th><th>TRIGGER</th><th>STARTED</th><th>DURATION</th><th>ERROR</th></tr>
      </thead>
      <tbody>
        {#each filtered as r}
          <tr onclick={() => toggle(r)} class:exp={expanded === r.run_id}>
            <td class="mono">{r.run_id}</td>
            <td>{r.workflow || '—'}</td>
            <td><span class="chip {statusChipClass(r.status)}">{r.status || '—'}</span></td>
            <td>{r.trigger || '—'}</td>
            <td class="muted">{fmtDate(r.started_at)}</td>
            <td>{fmtDur(r.duration_ms)}</td>
            <td class="err">{r.error || ''}</td>
          </tr>
          {#if expanded === r.run_id}
            <tr class="sub">
              <td colspan="7">
                <div class="steps">
                  <h4>Steps</h4>
                  {#if !stepsByRun[r.run_id]?.length}
                    <div class="muted">Loading steps…</div>
                  {:else}
                    <table class="tbl tbl-sm">
                      <thead><tr><th>STEP</th><th>STATUS</th><th>DURATION</th><th>ERROR</th></tr></thead>
                      <tbody>
                        {#each stepsByRun[r.run_id] as s}
                          <tr>
                            <td>{s.name || '—'}</td>
                            <td><span class="chip {statusChipClass(s.status)}">{s.status || '—'}</span></td>
                            <td>{fmtDur(s.duration_ms)}</td>
                            <td class="err">{s.error || ''}</td>
                          </tr>
                        {/each}
                      </tbody>
                    </table>
                  {/if}
                </div>
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .wrap { display: flex; flex-direction: column; gap: 12px; }
  .toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
  .filters { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; }
  .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; background: var(--pw-surface, #faf9f5); }
  .fchip { background: var(--pw-bg-alt, #f1ede4); border: 1px solid var(--pw-border, #e7e3da); padding: 4px 10px; border-radius: 0; font-size: 11px; cursor: pointer; color: var(--pw-ink, #2c2a26); text-transform: uppercase; letter-spacing: 0.04em; }
  .fchip.on { background: var(--pw-accent, #c96342); color: white; border-color: var(--pw-accent, #c96342); }
  .btn-ghost { background: none; border: 1px solid var(--pw-border, #e7e3da); padding: 6px 10px; font-size: 12px; cursor: pointer; border-radius: 0; color: var(--pw-ink, #2c2a26); }
  .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; }
  .tbl th { text-align: left; padding: 10px 12px; background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); border-bottom: 1px solid var(--pw-border, #e7e3da); }
  .tbl td { padding: 10px 12px; border-bottom: 1px solid var(--pw-border, #e7e3da); font-size: 13px; vertical-align: top; }
  .tbl tbody tr { cursor: pointer; }
  .tbl tbody tr:hover { background: var(--pw-bg-alt, #f1ede4); }
  .tbl tbody tr.exp { background: var(--pw-bg-alt, #f1ede4); }
  .tbl tbody tr.sub { cursor: default; background: var(--pw-bg-alt, #f1ede4); }
  .tbl tbody tr.sub:hover { background: var(--pw-bg-alt, #f1ede4); }
  .tbl-sm { background: var(--pw-surface, #faf9f5); margin-top: 6px; }
  .tbl-sm th, .tbl-sm td { padding: 6px 8px; font-size: 12px; }
  .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 14px; }
  .chip { display: inline-block; padding: 2px 8px; border-radius: 0; font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.04em; }
  .chip-green { background: rgba(22, 163, 74, 0.14); color: #15803d; }
  .chip-amber { background: rgba(217, 119, 6, 0.14); color: #b45309; }
  .chip-red { background: rgba(220, 38, 38, 0.14); color: #b91c1c; }
  .chip-gray { background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink-soft, #87837a); }
  .err { color: #b91c1c; font-size: 12px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .steps h4 { margin: 0 0 4px; font: 600 12px Inter; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
</style>
