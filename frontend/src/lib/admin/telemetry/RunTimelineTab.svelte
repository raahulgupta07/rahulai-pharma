<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import RunTimeline from '$lib/components/RunTimeline.svelte';

 let runId = $state('');
 let lookup = $state<{ kind: 'workflow' | 'agent'; data: any } | null>(null);
 let raw = $state<any>(null);
 let busy = $state(false);
 let err = $state<string | null>(null);

 let recent = $state<Array<{ id: string; kind: string; label: string; at: string }>>([]);

 function authHeaders(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 async function tryFetch(url: string): Promise<any | null> {
 try {
 const r = await fetch(url, { headers: authHeaders() });
 if (r.status !== 200) return null;
 return await r.json();
 } catch {
 return null;
 }
 }

 async function doLookup() {
 if (!runId.trim()) return;
 busy = true;
 err = null;
 lookup = null;
 raw = null;
 const id = runId.trim();
 const wf = await tryFetch(`/api/os/workflows/runs/${encodeURIComponent(id)}`);
 if (wf) {
 lookup = { kind: 'workflow', data: wf };
 raw = wf;
 busy = false;
 return;
 }
 const ev = await tryFetch(`/api/evals/runs/${encodeURIComponent(id)}`);
 if (ev) {
 lookup = { kind: 'workflow', data: ev };
 raw = ev;
 busy = false;
 return;
 }
 const sch = await tryFetch(`/api/agent-schedules/${encodeURIComponent(id)}`);
 if (sch) {
 lookup = { kind: 'agent', data: sch };
 raw = sch;
 busy = false;
 return;
 }
 err = 'Run not found in workflows, evals, or agent schedules.';
 busy = false;
 }

 async function loadRecent() {
 const collected: typeof recent = [];
 const wf = await tryFetch('/api/os/workflows/runs?limit=10');
 if (wf) {
 const list = Array.isArray(wf) ? wf : wf.items || wf.runs || [];
 for (const r of list.slice(0, 10)) {
 collected.push({ id: String(r.id || r.run_id), kind: 'workflow', label: r.name || r.workflow_id || '—', at: r.created_at || r.started_at || '' });
 }
 }
 const ev = await tryFetch('/api/evals/runs?limit=10');
 if (ev) {
 const list = Array.isArray(ev) ? ev : ev.items || ev.runs || [];
 for (const r of list.slice(0, 10)) {
 collected.push({ id: String(r.id || r.run_id), kind: 'eval', label: r.suite || r.name || '—', at: r.created_at || '' });
 }
 }
 const sch = await tryFetch('/api/agent-schedules?limit=10');
 if (sch) {
 const list = Array.isArray(sch) ? sch : sch.items || sch.schedules || [];
 for (const r of list.slice(0, 10)) {
 collected.push({ id: String(r.id), kind: 'schedule', label: r.name || r.agent || '—', at: r.created_at || '' });
 }
 }
 recent = collected;
 }

 function pickRecent(id: string) { runId = id; doLookup(); }

 onMount(loadRecent);
</script>

<p class="muted">Look up any run by ID across workflows, evals, and agent schedules.</p>

{#if recent.length > 0}
  <div class="strip">
    <span class="muted-sm">Recent:</span>
    {#each recent.slice(0, 30) as r}
      <button class="rchip" onclick={() => pickRecent(r.id)} title={r.label}>
        <span class="rk">{r.kind}</span>
        <span class="rid">{r.id.slice(0, 10)}</span>
      </button>
    {/each}
  </div>
{/if}

<div class="lookup">
  <input
    type="text"
    placeholder="Enter run ID…"
    bind:value={runId}
    onkeydown={(e) => e.key === 'Enter' && doLookup()}
  />
  <button class="btn" disabled={busy || !runId.trim()} onclick={doLookup}>
    {busy ? 'Searching…' : 'Lookup'}
  </button>
</div>

{#if err}
  <div class="empty err"><Icon name="alert-triangle" size={14} /> {err}</div>
{/if}

{#if lookup}
  <RunTimeline runId={runId.trim()} kind={lookup.kind} />

  <h4>Raw JSON</h4>
  <pre class="raw">{JSON.stringify(raw, null, 2)}</pre>
{/if}

<style>
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; margin: 0 0 12px; }
 .muted-sm { color: var(--pw-ink-soft, #87837a); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; margin-right: 4px; }
 .strip { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; padding: 10px; background: var(--pw-bg-alt, #f1ede4); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; }
 .rchip {
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 padding: 3px 8px;
 font: 11px Inter, system-ui, sans-serif;
 cursor: pointer;
 display: inline-flex;
 gap: 6px;
 }
 .rchip:hover { border-color: var(--pw-accent, #c96342); }
 .rk { color: var(--pw-ink-soft, #87837a); text-transform: uppercase; font-weight: 600; font-size: 10px; }
 .rid { font-family: 'JetBrains Mono', monospace; }
 .lookup { display: flex; gap: 8px; margin-bottom: 16px; }
 .lookup input {
 flex: 1;
 padding: 8px 12px;
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 background: var(--pw-surface, #faf9f5);
 font: 13px 'JetBrains Mono', monospace;
 color: var(--pw-ink, #2c2a26);
 }
 .btn {
 padding: 6px 14px;
 border-radius: 0;
 border: 1px solid var(--pw-accent, #c96342);
 background: var(--pw-accent, #c96342);
 color: white;
 cursor: pointer;
 font: 600 12px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 }
 .btn:disabled { opacity: 0.5; cursor: not-allowed; }
 .empty { padding: 20px; text-align: center; color: var(--pw-ink-soft, #87837a); background: var(--pw-surface, #faf9f5); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; margin-bottom: 12px; }
 .empty.err { color: #ef4444; }
 h4 { font: 600 12px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); margin: 20px 0 8px; }
 .raw {
 background: #1a1a1a;
 color: #d4d4d4;
 padding: 14px;
 border-radius: 0;
 overflow: auto;
 font: 12px 'JetBrains Mono', monospace;
 max-height: 400px;
 }
</style>
