<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 interface Props { runId: string; kind?: 'workflow' | 'agent'; }
 let { runId, kind = 'workflow' }: Props = $props();

 let steps = $state<any[]>([]);
 let loading = $state(true);
 const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);

 async function load() {
 loading = true;
 const url = kind === 'workflow' ? `/api/workflows/runs/${runId}` : `/api/agent-schedules/${runId}/runs`;
 const r = await fetch(url, { headers: { Authorization: `Bearer ${token() || ''}` } });
 const j = await r.json();
 steps = j?.steps || j?.runs || [];
 loading = false;
 }

 onMount(load);
</script>

<div class="timeline">
  <header><h3>Run Timeline</h3><button class="ghost" onclick={load}>↻</button></header>
  {#if loading}
    <div class="empty">loading…</div>
  {:else if !steps.length}
    <div class="empty">No steps recorded.</div>
  {:else}
    {#each steps as s, i}
      <div class="step status-{s.status}">
        <div class="line">
          <span class="num">{i + 1}</span>
          <span class="dot status-{s.status}"></span>
          <span class="name">{s.step_id || s.case_name || s.case_id}</span>
          <span class="kind">{s.step_kind || ''}</span>
          <span class="muted">{s.latency_ms || 0}ms</span>
        </div>
        {#if s.error}<div class="err"><Icon name="alert-triangle" size={14} /> {s.error}</div>{/if}
      </div>
    {/each}
  {/if}
</div>

<style>
 .timeline { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: var(--pw-radius-sm); padding: 16px; font: 13px Inter; color: var(--pw-ink, #2c2a26); }
 header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
 h3 { font: 600 12px Inter; text-transform: uppercase; letter-spacing: 0.05em; margin: 0; }
 button.ghost { background: none; border: 1px solid var(--pw-border, #e7e3da); padding: 4px 10px; border-radius: var(--pw-radius-sm); cursor: pointer; font: 11px Inter; }
 .step { padding: 6px 0; border-top: 1px solid var(--pw-border, #e7e3da); }
 .step:first-of-type { border-top: none; }
 .line { display: flex; gap: 10px; align-items: center; }
 .num { font-family: 'JetBrains Mono', monospace; color: var(--pw-ink-soft, #87837a); font-size: 11px; min-width: 20px; }
 .dot { width: 8px; height: 8px; border-radius: 50%; background: #888; }
 .dot.status-done { background: #10b981; }
 .dot.status-pass { background: #10b981; }
 .dot.status-running { background: #f59e0b; animation: pulse 1.5s infinite; }
 .dot.status-failed { background: #ef4444; }
 .dot.status-fail { background: #ef4444; }
 .dot.status-skipped { background: #d1d5db; }
 .dot.status-error { background: #ef4444; }
 .name { font-weight: 600; flex: 1; }
 .kind { color: var(--pw-ink-soft, #87837a); font-size: 11px; font-family: 'JetBrains Mono', monospace; }
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 11px; }
 .err { font-size: 11px; color: #b91c1c; margin-left: 38px; margin-top: 4px; }
 .empty { text-align: center; color: var(--pw-ink-soft, #87837a); padding: 30px; }
 @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
</style>
