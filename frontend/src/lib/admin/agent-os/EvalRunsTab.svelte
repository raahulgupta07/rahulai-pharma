<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 type EvalRun = {
 id: string;
 suite?: string;
 status?: string;
 pass_rate?: number;
 passed?: number;
 failed?: number;
 avg_latency_ms?: number;
 started_at?: string;
 notes?: string;
 };
 type Case = { name?: string; status?: string; latency_ms?: number; expected?: string; got?: string; error?: string };

 let runs = $state<EvalRun[]>([]);
 let loading = $state(true);
 let statusFilter = $state<'all' | 'done' | 'regressed' | string>('all');
 let selected = $state<EvalRun | null>(null);
 let cases = $state<Case[]>([]);

 const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);
 const authHeaders = (): Record<string, string> => {
 const t = token();
 return t ? { Authorization: `Bearer ${t}` } : {};
 };

 async function load() {
 loading = true;
 try {
 const r = await fetch('/api/evals/runs?limit=100', { headers: authHeaders() });
 if (r.ok) {
 const j = await r.json();
 runs = Array.isArray(j) ? j : j.runs || j.items || [];
 }
 } catch {}
 loading = false;
 }

 async function open(run: EvalRun) {
 selected = run;
 cases = [];
 try {
 const r = await fetch(`/api/evals/runs/${run.id}`, { headers: authHeaders() });
 if (r.ok) {
 const j = await r.json();
 cases = j.cases || j.results || j.items || [];
 }
 } catch {}
 }

 function statusChipClass(s?: string) {
 const v = (s || '').toLowerCase();
 if (v === 'done' || v === 'success' || v === 'pass') return 'chip-green';
 if (v === 'running') return 'chip-amber';
 if (v === 'failed' || v === 'regressed' || v === 'fail') return 'chip-red';
 return 'chip-gray';
 }

 function fmtDate(s?: string) { if (!s) return '—'; try { return new Date(s).toLocaleString(); } catch { return s; } }
 function fmtLat(ms?: number) { if (!ms) return '—'; if (ms < 1000) return ms + 'ms'; return (ms / 1000).toFixed(1) + 's'; }

 const filtered = $derived(runs.filter(r => {
 if (statusFilter === 'all') return true;
 return (r.status || '').toLowerCase() === statusFilter;
 }));

 onMount(load);
</script>

<div class="wrap">
  <div class="toolbar">
    <div class="filters">
      <button class="fchip" class:on={statusFilter === 'all'} onclick={() => (statusFilter = 'all')}>all</button>
      <button class="fchip" class:on={statusFilter === 'done'} onclick={() => (statusFilter = 'done')}>done</button>
      <button class="fchip" class:on={statusFilter === 'regressed'} onclick={() => (statusFilter = 'regressed')}>regressed</button>
    </div>
    <button class="btn-ghost" onclick={load}>↻ Refresh</button>
  </div>

  {#if loading}
    <div class="empty">Loading…</div>
  {:else if !filtered.length}
    <div class="empty">No eval runs.</div>
  {:else}
    <table class="tbl">
      <thead>
        <tr><th>RUN_ID</th><th>SUITE</th><th>STATUS</th><th>PASS RATE</th><th>PASSED/FAILED</th><th>AVG LATENCY</th><th>STARTED</th><th>NOTES</th></tr>
      </thead>
      <tbody>
        {#each filtered as r}
          <tr onclick={() => open(r)}>
            <td class="mono">{r.id}</td>
            <td>{r.suite || '—'}</td>
            <td><span class="chip {statusChipClass(r.status)}">{r.status || '—'}</span></td>
            <td>
              <div class="pbar"><div class="pfill" style="width:{Math.round((r.pass_rate ?? 0) * 100)}%"></div></div>
              <span class="muted s">{((r.pass_rate ?? 0) * 100).toFixed(0)}%</span>
            </td>
            <td><span class="ok">{r.passed ?? 0}</span> / <span class="bad">{r.failed ?? 0}</span></td>
            <td>{fmtLat(r.avg_latency_ms)}</td>
            <td class="muted">{fmtDate(r.started_at)}</td>
            <td class="notes">{r.notes || ''}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

{#if selected}
  <div class="backdrop" role="button" tabindex="0" aria-label="Close drawer" onclick={() => (selected = null)} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ' || e.key === 'Escape') { e.preventDefault(); selected = null; } }}></div>
  <aside class="drawer">
    <header>
      <h3>{selected.suite || selected.id}</h3>
      <button class="btn-ghost" onclick={() => (selected = null)}><Icon name="x" size={14} /></button>
    </header>
    <section>
      <div class="kv">
        <div><span class="muted">Pass rate</span><strong>{((selected.pass_rate ?? 0) * 100).toFixed(0)}%</strong></div>
        <div><span class="muted">Passed</span><strong class="ok">{selected.passed ?? 0}</strong></div>
        <div><span class="muted">Failed</span><strong class="bad">{selected.failed ?? 0}</strong></div>
        <div><span class="muted">Avg latency</span><strong>{fmtLat(selected.avg_latency_ms)}</strong></div>
      </div>
    </section>
    <section>
      <h4>Case results</h4>
      {#if !cases.length}
        <div class="muted">No case data.</div>
      {:else}
        <table class="tbl tbl-sm">
          <thead><tr><th>CASE</th><th>STATUS</th><th>LATENCY</th><th>NOTES</th></tr></thead>
          <tbody>
            {#each cases as c}
              <tr>
                <td>{c.name || '—'}</td>
                <td><span class="chip {statusChipClass(c.status)}">{c.status || '—'}</span></td>
                <td>{fmtLat(c.latency_ms)}</td>
                <td class="notes">{c.error || c.got || ''}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
  </aside>
{/if}

<style>
 .wrap { display: flex; flex-direction: column; gap: 12px; }
 .toolbar { display: flex; justify-content: space-between; align-items: center; }
 .filters { display: flex; gap: 6px; }
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; }
 .s { font-size: 11px; margin-left: 6px; }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; background: var(--pw-surface, #faf9f5); }
 .fchip { background: var(--pw-bg-alt, #f1ede4); border: 1px solid var(--pw-border, #e7e3da); padding: 4px 10px; border-radius: 0; font-size: 11px; cursor: pointer; color: var(--pw-ink, #2c2a26); text-transform: uppercase; letter-spacing: 0.04em; }
 .fchip.on { background: var(--pw-accent, #c96342); color: white; border-color: var(--pw-accent, #c96342); }
 .btn-ghost { background: none; border: 1px solid var(--pw-border, #e7e3da); padding: 6px 10px; font-size: 12px; cursor: pointer; border-radius: 0; color: var(--pw-ink, #2c2a26); }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; }
 .tbl th { text-align: left; padding: 10px 12px; background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); border-bottom: 1px solid var(--pw-border, #e7e3da); }
 .tbl td { padding: 10px 12px; border-bottom: 1px solid var(--pw-border, #e7e3da); font-size: 13px; }
 .tbl tbody tr { cursor: pointer; }
 .tbl tbody tr:hover { background: var(--pw-bg-alt, #f1ede4); }
 .tbl-sm th, .tbl-sm td { padding: 6px 8px; font-size: 12px; }
 .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 14px; }
 .chip { display: inline-block; padding: 2px 8px; border-radius: 0; font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.04em; }
 .chip-green { background: rgba(22, 163, 74, 0.14); color: #15803d; }
 .chip-amber { background: rgba(217, 119, 6, 0.14); color: #b45309; }
 .chip-red { background: rgba(220, 38, 38, 0.14); color: #b91c1c; }
 .chip-gray { background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink-soft, #87837a); }
 .pbar { display: inline-block; width: 80px; height: 6px; background: var(--pw-bg-alt, #f1ede4); border-radius: 0; overflow: hidden; vertical-align: middle; }
 .pfill { height: 100%; background: var(--pw-accent, #c96342); }
 .ok { color: #15803d; font-weight: 600; }
 .bad { color: #b91c1c; font-weight: 600; }
 .notes { color: var(--pw-ink-soft, #87837a); font-size: 12px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
 .backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.2); z-index: 50; }
 .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: 480px; background: var(--pw-surface, #faf9f5); border-left: 1px solid var(--pw-border, #e7e3da); z-index: 51; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
 .drawer header { display: flex; justify-content: space-between; align-items: center; }
 .drawer h3 { margin: 0; font: 600 18px 'Source Serif 4', Georgia, serif; }
 .drawer h4 { margin: 0 0 8px; font: 600 12px Inter; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .kv { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
 .kv > div { display: flex; flex-direction: column; gap: 2px; padding: 8px; background: var(--pw-bg-alt, #f1ede4); border-radius: 0; }
 .kv strong { font: 600 16px 'Source Serif 4', Georgia, serif; color: var(--pw-ink, #2c2a26); }
</style>
