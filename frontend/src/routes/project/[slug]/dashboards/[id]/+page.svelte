<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { page } from '$app/state';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';
 import DashRenderer from '$lib/dashboards/DashRenderer.svelte';
 import EditPanel from '$lib/dashboards/EditPanel.svelte';

 const projectSlug = $derived(page.params.slug || '');
 let showEdit = $state(false);
 let editChangedIds = $state<string[]>([]);
 let savingEdit = $state(false);
 const dashId = $derived(page.params.id || '');
 let spec = $state<any>(null);
 let dashData = $state<any>({});
 let prevSpec = $state<any>(null);
 let loading = $state(true);
 let error = $state('');
 let shareUrl = $state('');
 let isPublic = $state(false);
 let showShare = $state(false);
 let autoRefresh = $state(false);
 let refreshTimer: any = null;
 let busy = $state(false);
 let convertingDeck = $state(false);
 let deckProgress = $state('');
 let deckError = $state('');

 function _headers(): Record<string, string> {
 const t = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 async function logMem(action: string, cell: any = null) {
 try {
 await fetch('/api/dashboards/memory/log', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ project_slug: projectSlug, action, cell, spec_id: String(dashId) })
 });
 } catch {}
 }

 function deleteCell(idx: number) {
 if (!spec || !spec.cells) return;
 const cell = spec.cells[idx];
 void logMem('deleted', cell);
 spec = { ...spec, cells: spec.cells.filter((_: any, i: number) => i !== idx) };
 }

 function applyShare(s: any) {
 isPublic = !!s.is_public;
 if (s.is_public && s.share_token) {
 shareUrl = `${location.origin}/api/dashboards/public/${s.share_token}`;
 } else {
 shareUrl = '';
 }
 }

 async function fetchData() {
 if (!spec) return;
 try {
 const r = await fetch('/api/dashboards/run-data', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ spec, project_slug: projectSlug })
 });
 const d = await r.json();
 if (d.ok) dashData = d.data || {};
 } catch {}
 }

 onMount(async () => {
 try {
 const r = await fetch(`/api/dashboards/${dashId}`, { headers: _headers() });
 const data = await r.json();
 spec = data.spec || (data.cells ? data : null);
 if (!spec) error = data.error || 'Dashboard not found';
 else applyShare(spec);
 } catch (e: any) { error = String(e); }
 loading = false;
 await fetchData();
 });

 onDestroy(() => { if (refreshTimer) clearInterval(refreshTimer); });

 async function toggleShare() {
 busy = true;
 const r = await fetch(`/api/dashboards/${dashId}/share`, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', ..._headers() },
 body: JSON.stringify({ public: !isPublic })
 });
 const data = await r.json();
 if (data.ok) {
 isPublic = data.public;
 shareUrl = data.token ? `${location.origin}/api/dashboards/public/${data.token}` : '';
 }
 busy = false;
 }

 function copyUrl() {
 if (shareUrl) navigator.clipboard?.writeText(shareUrl);
 }

 async function exportPng() {
 const node = document.querySelector('.dashboard-render') as HTMLElement
 || document.querySelector('.render') as HTMLElement;
 if (!node) return;
 try {
 const htmlToImage: any = await import('html-to-image');
 const dataUrl = await htmlToImage.toPng(node);
 const a = document.createElement('a');
 a.href = dataUrl;
 a.download = `${spec?.title || 'dashboard'}.png`;
 a.click();
 } catch {
 alert('PNG export needs html-to-image. Run: pnpm add html-to-image');
 }
 }

 async function refresh() {
 busy = true;
 try {
 const r = await fetch(`/api/dashboards/${dashId}/refresh`, {
 method: 'POST', headers: _headers()
 });
 const data = await r.json();
 if (data.ok && data.spec) {
 prevSpec = spec;
 spec = data.spec;
 }
 await fetchData();
 } catch (e) { /* ignore */ }
 busy = false;
 }

 function toggleAutoRefresh() {
 autoRefresh = !autoRefresh;
 if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
 if (autoRefresh) refreshTimer = setInterval(fetchData, 60000);
 }

 async function saveEdits() {
 if (!spec || savingEdit) return;
 savingEdit = true;
 try {
 await fetch('/api/dashboards/save', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify(spec)
 });
 for (const cell of (spec.cells || [])) void logMem('kept', cell);
 void logMem('saved');
 } catch {}
 savingEdit = false;
 }

 function toggleCompare() {
 if (spec.compare_to) { spec.compare_to = null; prevSpec = null; }
 else { spec.compare_to = '-7d'; alert('Compare mode stub: -7d offset set on spec. Full impl deferred.'); }
 }

 async function convertToDeck() {
   if (convertingDeck || !dashId) return;
   convertingDeck = true;
   deckError = '';
   deckProgress = 'Converting dashboard to deck…';
   try {
     const r = await fetch(`/api/dashboards/${dashId}/to-deck`, {
       method: 'POST',
       headers: { ..._headers(), 'Content-Type': 'application/json' },
       body: JSON.stringify({})
     });
     const data = await r.json();
     if (!r.ok || data.ok === false) {
       throw new Error(data.detail || data.error || `HTTP ${r.status}`);
     }
     deckProgress = `Deck ready · ${data.slide_count} slides · presentation #${data.presentation_id}`;
     setTimeout(() => { goto(`${base}/presentations`); }, 600);
   } catch (e: any) {
     deckError = `Failed: ${e?.message || e}`;
     deckProgress = '';
   } finally {
     convertingDeck = false;
   }
 }
</script>

<div class="page">
  {#if loading}
    <div class="msg">Loading…</div>
  {:else if error}
    <div class="msg err">{error}</div>
  {:else if spec}
    <button class="back-btn" onclick={() => goto(`${base}/project/${projectSlug}`)}>← Back to chat</button>
    <div class="header">
      <h1>{spec.title || 'Dashboard'}</h1>
      <div class="actions">
        <button class="btn" onclick={refresh} disabled={busy}>REFRESH</button>
        <button class="btn" class:active={autoRefresh} onclick={toggleAutoRefresh}>AUTO {autoRefresh ? 'ON' : 'OFF'}</button>
        <button class="btn" onclick={toggleCompare}>{spec.compare_to ? 'COMPARE ON' : 'COMPARE'}</button>
        <button class="btn" onclick={exportPng}>EXPORT PNG</button>
        <button class="btn deck" onclick={convertToDeck} disabled={convertingDeck} title="Convert this dashboard to a PowerPoint deck">
          {convertingDeck ? 'CONVERTING…' : '→ CONVERT TO DECK'}
        </button>
        <button class="btn" onclick={() => (showShare = !showShare)}>SHARE</button>
        <button class="btn" class:active={showEdit} onclick={() => (showEdit = !showEdit)}>EDIT</button>
        {#if showEdit}
          <button class="btn" onclick={saveEdits} disabled={savingEdit}>{savingEdit ? '…' : 'SAVE EDITS'}</button>
        {/if}
      </div>
    </div>
    {#if deckProgress || deckError}
      <div class="deck-banner" class:err={!!deckError}>
        {deckError || deckProgress}
      </div>
    {/if}
    {#if showShare}
      <div class="share">
        <label><input type="checkbox" checked={isPublic} onchange={toggleShare} disabled={busy}/> Public</label>
        {#if shareUrl}
          <input class="url" readonly value={shareUrl}/>
          <button class="btn small" onclick={copyUrl}>COPY</button>
        {:else}
          <span class="hint">Toggle public to generate a share URL.</span>
        {/if}
      </div>
    {/if}
    {#if spec.compare_to && prevSpec}
      <div class="compare">
        <div class="col"><div class="lbl">Current</div><div class="render dashboard-render"><DashRenderer {spec} data={dashData} /></div></div>
        <div class="col"><div class="lbl">Previous</div><div class="render"><DashRenderer spec={prevSpec} data={dashData} /></div></div>
      </div>
    {:else}
      <div class="layout" class:has-edit={showEdit}>
        <div class="render dashboard-render main">
          {#if showEdit}
            <div class="cell-actions">
              {#each (spec.cells || []) as cell, i (cell.id || i)}
                <button class="x-btn" title="Delete cell (logs preference)" onclick={() => deleteCell(i)}><Icon name="x" size={14} /> {cell.title || cell.id || `cell ${i + 1}`}</button>
              {/each}
            </div>
          {/if}
          <DashRenderer {spec} data={dashData} {editChangedIds} />
        </div>
        {#if showEdit}
          <EditPanel bind:spec bind:changedIds={editChangedIds} />
        {/if}
      </div>
    {/if}
  {/if}
</div>

<style>
 .page { background: #fafaf7; min-height: 100vh; height: 100vh; overflow-y: auto; padding: 20px; box-sizing: border-box; }
 .back-btn { background: #fff; color: #2e7d32; border: 1px solid #e0e0d8; padding: 6px 12px; border-radius: 0; font-weight: 700; font-size: 11px; letter-spacing: 0.06em; cursor: pointer; margin-bottom: 12px; }
 .back-btn:hover { background: #f0f5f0; border-color: #2e7d32; }
 .header { display: flex; align-items: center; justify-content: space-between; background: #fff; border: 1px solid #e0e0d8; border-radius: 0; padding: 10px 14px; margin-bottom: 14px; }
 .header h1 { margin: 0; font-size: 12px; color: #2e7d32; font-weight: 700; }
 .actions { display: flex; gap: 6px; }
 .render { background: #fff; border: 1px solid #e0e0d8; border-radius: 0; padding: 16px; }
 .msg { background: #fff; border: 1px solid #e0e0d8; border-radius: 0; padding: 24px; text-align: center; color: #666; }
 .err { color: #c62828; }
 .btn { background: #2e7d32; color: #fff; border: none; padding: 6px 12px; border-radius: 0; font-weight: 700; font-size: 11px; letter-spacing: 0.08em; cursor: pointer; }
 .btn.small { padding: 4px 8px; font-size: 10px; }
 .btn.active { background: #1565c0; }
 .btn.deck { background: #c96342; }
 .btn.deck:hover { background: #a84e30; }
 .deck-banner { background: #fff; border: 1px solid #c96342; color: #c96342; padding: 8px 12px; margin-bottom: 12px; font-size: 12px; font-weight: 600; letter-spacing: 0.04em; }
 .deck-banner.err { border-color: #c62828; color: #c62828; }
 .btn:disabled { opacity: 0.4; cursor: not-allowed; }
 .share { background: #fff; border: 1px solid #e0e0d8; border-radius: 0; padding: 10px 14px; margin-bottom: 14px; display: flex; align-items: center; gap: 10px; font-size: 11px; }
 .share .url { flex: 1; padding: 4px 8px; border: 1px solid #ccc; border-radius: 0; font-family: monospace; font-size: 11px; }
 .share .hint { color: #888; font-style: italic; }
 .compare { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
 .compare .lbl { font-size: 11px; font-weight: 700; letter-spacing: 0.1em; color: #2e7d32; margin-bottom: 6px; }
 .layout { display: flex; gap: 14px; }
 .layout .main { flex: 1; min-width: 0; }
 .layout.has-edit > :global(aside.edit-panel) { width: 30%; flex: 0 0 30%; }
 .cell-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
 .x-btn { background: #fff; color: #c62828; border: 1px solid #f1c1c1; padding: 3px 8px; border-radius: 0; font-size: 11px; cursor: pointer; font-weight: 600; }
 .x-btn:hover { background: #ffebee; border-color: #c62828; }
</style>
