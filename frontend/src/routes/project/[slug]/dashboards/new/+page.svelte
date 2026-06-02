<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import { page } from '$app/state';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';
 import DashRenderer from '$lib/dashboards/DashRenderer.svelte';
 import EditPanel from '$lib/dashboards/EditPanel.svelte';

 const projectSlug = $derived(page.params.slug || '');
 let spec = $state<any>(null);
 let editChangedIds = $state<string[]>([]);
 let showEdit = $state(true);
 let saving = $state(false);
 let savedId = $state<string | number | null>(null);
 let error = $state('');
 let deepening = $state(false);
 let thinkingLog = $state<string[]>([]);
 let logEl: HTMLDivElement | null = $state(null);
 let newCellIdx = $state<number | null>(null);

 function _headers(): Record<string, string> {
 const t = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 onMount(() => {
 const raw = sessionStorage.getItem('dashboard_spec');
 if (raw) {
 try { spec = JSON.parse(raw); } catch { error = 'Invalid spec in storage'; }
 }
 });

 $effect(() => {
 void thinkingLog.length;
 if (logEl) logEl.scrollTop = logEl.scrollHeight;
 });

 async function logMem(action: string, cell: any = null, spec_id: any = null) {
 try {
 await fetch('/api/dashboards/memory/log', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ project_slug: projectSlug, action, cell, spec_id: spec_id ? String(spec_id) : null })
 });
 } catch {}
 }

 function deleteCell(idx: number) {
 if (!spec || !spec.cells) return;
 const cell = spec.cells[idx];
 void logMem('deleted', cell, spec?.id);
 const cells = spec.cells.filter((_: any, i: number) => i !== idx);
 spec = { ...spec, cells };
 sessionStorage.setItem('dashboard_spec', JSON.stringify(spec));
 }

 async function save() {
 if (!spec) return;
 saving = true;
 try {
 const r = await fetch('/api/dashboards/save', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ project_slug: projectSlug, spec })
 });
 const data = await r.json();
 if (data.ok && data.id != null) {
 savedId = data.id;
 for (const cell of (spec.cells || [])) {
 void logMem('kept', cell, data.id);
 }
 void logMem('saved', null, data.id);
 sessionStorage.removeItem('dashboard_spec');
 } else {
 error = data.error || 'Save failed';
 }
 } catch (e: any) { error = String(e); }
 saving = false;
 }

 function handleEvent(evt: any) {
 if (evt.type === 'thinking') {
 thinkingLog = [...thinkingLog, evt.msg];
 } else if (evt.type === 'insight') {
 thinkingLog = [...thinkingLog, ` ${evt.insight?.finding ?? ''}`];
 } else if (evt.type === 'cell_added' && evt.cell) {
 const cells = [...(spec?.cells || []), evt.cell];
 spec = { ...spec, cells };
 newCellIdx = cells.length - 1;
 setTimeout(() => { newCellIdx = null; }, 650);
 } else if (evt.type === 'done') {
 if (evt.spec) spec = evt.spec;
 thinkingLog = [...thinkingLog, ' analysis complete'];
 sessionStorage.setItem('dashboard_spec', JSON.stringify(spec));
 } else if (evt.type === 'error') {
 thinkingLog = [...thinkingLog, ` ${evt.error || 'error'}`];
 }
 }

 async function deepen() {
 if (!spec || deepening) return;
 deepening = true;
 thinkingLog = [];
 try {
 const res = await fetch('/api/dashboards/deepen/stream', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ spec, project_slug: projectSlug, persona: spec?.persona || null })
 });
 if (!res.ok || !res.body) {
 thinkingLog = [...thinkingLog, ` HTTP ${res.status}`];
 deepening = false;
 return;
 }
 const reader = res.body.getReader();
 const decoder = new TextDecoder();
 let buf = '';
 while (true) {
 const { done, value } = await reader.read();
 if (done) break;
 buf += decoder.decode(value, { stream: true });
 const blocks = buf.split('\n\n');
 buf = blocks.pop() || '';
 for (const block of blocks) {
 const line = block.split('\n').find((l) => l.startsWith('data: '));
 if (!line) continue;
 try { handleEvent(JSON.parse(line.slice(6))); } catch {}
 }
 }
 } catch (e: any) {
 thinkingLog = [...thinkingLog, ` ${String(e)}`];
 }
 deepening = false;
 }
</script>

<div class="page" class:has-panel={deepening || thinkingLog.length > 0 || (spec && showEdit)}>
  <div class="main">
    {#if !spec}
      <div class="empty">
        <p>No dashboard spec found.</p>
        <button class="btn" onclick={() => goto(`${base}/project/${projectSlug}`)}>← Back to chat</button>
      </div>
    {:else}
      <div class="banner">
        <span><Icon name="check" size={14} /> generated from chat</span>
        <div style="display:flex; gap:8px; align-items:center;">
          <button class="btn deepen" onclick={deepen} disabled={deepening}>{deepening ? 'deepening…' : 'DEEPEN ANALYSIS'}</button>
          {#if savedId !== null}
            <span class="saved">saved · <a href="{base}/project/{projectSlug}/dashboards/{savedId}">{base}/project/{projectSlug}/dashboards/{savedId}</a></span>
          {:else}
            <button class="btn save" onclick={save} disabled={saving}>{saving ? 'saving…' : 'SAVE'}</button>
          {/if}
        </div>
      </div>
      {#if error}<div class="err">{error}</div>{/if}
      <div class="render" class:flash={newCellIdx !== null}>
        <div class="cell-actions">
          {#each (spec.cells || []) as cell, i (cell.id || i)}
            <button class="x-btn" title="Delete cell (logs preference)" onclick={() => deleteCell(i)}><Icon name="x" size={14} /> {cell.title || cell.id || `cell ${i + 1}`}</button>
          {/each}
        </div>
        <DashRenderer {spec} {editChangedIds} />
      </div>
    {/if}
  </div>

  {#if spec && showEdit}
    <EditPanel bind:spec bind:changedIds={editChangedIds} />
  {/if}

  {#if deepening || thinkingLog.length > 0}
    <aside class="thinking-panel">
      {#if deepening}
        <div class="pulse">thinking<span class="dots">...</span></div>
      {:else}
        <div class="pulse done">complete</div>
      {/if}
      <div class="log" bind:this={logEl}>
        {#each thinkingLog as line, idx}
          <div class="log-line" class:latest={idx === thinkingLog.length - 1}>{line}</div>
        {/each}
      </div>
    </aside>
  {/if}
</div>

<style>
 .page { background: #fafaf7; min-height: 100vh; padding: 20px; display: flex; gap: 14px; }
 .main { flex: 1; min-width: 0; }
 .has-panel .main { width: 70%; }
 .empty { background: #fff; border: 1px solid #e0e0d8; border-radius: 0; padding: 32px; text-align: center; max-width: 480px; margin: 80px auto; }
 .banner { display: flex; align-items: center; justify-content: space-between; background: #fff; border: 1px solid #e0e0d8; border-left: 3px solid #2e7d32; border-radius: 0; padding: 10px 14px; margin-bottom: 14px; font-size: 11px; color: #2e7d32; font-weight: 600; }
 .saved { color: #444; font-weight: 400; font-size: 11px; }
 .saved a { color: #2e7d32; }
 .render { background: #fff; border: 1px solid #e0e0d8; border-radius: 0; padding: 16px; transition: border-color 0.3s; }
 .render.flash { animation: flash 0.6s ease-out; }
 @keyframes flash { 0% { border-color: #2e7d32; box-shadow: 0 0 0 2px rgba(46,125,50,0.3); } 100% { border-color: #e0e0d8; box-shadow: none; } }
 .btn { background: #2e7d32; color: #fff; border: none; padding: 6px 14px; border-radius: 0; font-weight: 700; font-size: 11px; letter-spacing: 0.1em; cursor: pointer; }
 .btn:disabled { opacity: 0.5; cursor: not-allowed; }
 .save { padding: 6px 18px; }
 .deepen { background: #1a1a1a; }
 .err { background: #ffebee; color: #c62828; padding: 8px 12px; border-radius: 0; margin-bottom: 12px; font-size: 11px; }
 .thinking-panel { width: 30%; background: #1a1a1a; color: #fafaf7; border-radius: 0; padding: 8px; font-family: ui-monospace, 'SF Mono', Menlo, monospace; font-size: 11px; line-height: 1.6; max-height: 60vh; overflow-y: auto; align-self: flex-start; position: sticky; top: 20px; }
 .pulse { color: #4caf50; font-weight: 700; padding: 4px 6px; border-bottom: 1px solid #333; margin-bottom: 6px; animation: pulse 1.4s ease-in-out infinite; }
 .pulse.done { animation: none; opacity: 0.6; }
 .dots { display: inline-block; animation: dots 1.2s steps(4, end) infinite; }
 .log { padding: 4px 6px; }
 .log-line { color: #aaa; padding: 2px 0; white-space: pre-wrap; word-break: break-word; }
 .log-line.latest { color: #4caf50; font-weight: 700; }
 @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.55; } }
 @keyframes dots { 0% { content: ''; } 25% { content: '.'; } 50% { content: '..'; } 75% { content: '...'; } }
 .cell-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
 .x-btn { background: #fff; color: #c62828; border: 1px solid #f1c1c1; padding: 3px 8px; border-radius: 0; font-size: 11px; cursor: pointer; font-weight: 600; }
 .x-btn:hover { background: #ffebee; border-color: #c62828; }
</style>
