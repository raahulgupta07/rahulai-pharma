<script lang="ts">
  import { onMount } from 'svelte';
  import { authHeaders } from '$lib/api';

  let { slug = 'citypharma' } = $props();

  let pending = $state<{ facts: any[]; queries: any[]; triples: any[]; total: number }>({ facts: [], queries: [], triples: [], total: 0 });
  let loading = $state(false);
  let busy = $state(false);
  let msg = $state('');
  let fileEl = $state<HTMLInputElement | null>(null);

  const base = `/api/projects/${slug}`;

  async function loadPending() {
    loading = true;
    try {
      const r = await fetch(`${base}/okf-pending`, { headers: authHeaders() });
      if (r.ok) pending = await r.json();
    } catch (e) { msg = 'failed to load pending'; }
    loading = false;
  }

  async function doImport(ev: Event) {
    const f = (ev.target as HTMLInputElement).files?.[0];
    if (!f) return;
    busy = true; msg = '';
    try {
      const fd = new FormData();
      fd.append('file', f);
      const r = await fetch(`${base}/okf-import`, { method: 'POST', headers: authHeaders(), body: fd });
      const d = await r.json();
      if (r.ok) { msg = `Imported ${d.imported.queries} queries · ${d.imported.facts} facts · ${d.imported.triples} triples → pending`; await loadPending(); }
      else msg = d.detail || 'import failed';
    } catch (e) { msg = 'import failed'; }
    busy = false;
    if (fileEl) fileEl.value = '';
  }

  async function promote() {
    if (!confirm('Promote all pending OKF knowledge to LIVE? The agent will use it in normal chat.')) return;
    busy = true; msg = '';
    try {
      const r = await fetch(`${base}/okf-promote`, { method: 'POST', headers: authHeaders() });
      const d = await r.json();
      msg = r.ok ? `Promoted to live: ${d.promoted.facts} facts · ${d.promoted.queries} queries · ${d.promoted.triples} triples` : (d.detail || 'promote failed');
      await loadPending();
    } catch (e) { msg = 'promote failed'; }
    busy = false;
  }

  async function reject() {
    if (!confirm('Discard all pending OKF knowledge? This deletes the imported lane.')) return;
    busy = true; msg = '';
    try {
      const r = await fetch(`${base}/okf-reject`, { method: 'POST', headers: authHeaders() });
      const d = await r.json();
      msg = r.ok ? `Discarded: ${d.rejected.facts} facts · ${d.rejected.queries} queries · ${d.rejected.triples} triples` : (d.detail || 'reject failed');
      await loadPending();
    } catch (e) { msg = 'reject failed'; }
    busy = false;
  }

  async function exportBundle() {
    busy = true; msg = '';
    try {
      const r = await fetch(`${base}/okf-export`, { headers: authHeaders() });
      if (!r.ok) { msg = 'export failed'; busy = false; return; }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${slug}-okf-bundle.zip`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      msg = 'Exported bundle (.zip downloaded)';
    } catch (e) { msg = 'export failed'; }
    busy = false;
  }

  onMount(loadPending);
</script>

<div class="okf">
  <div class="okf-head">
    <div>
      <div class="okf-title">Open Knowledge Format</div>
      <div class="okf-sub">Import a knowledge bundle → review → promote to live. Export the current knowledge as a portable bundle.</div>
    </div>
    <div class="okf-actions">
      <button class="okf-btn" disabled={busy} onclick={exportBundle}>⬇ Export bundle</button>
      <button class="okf-btn okf-primary" disabled={busy} onclick={() => fileEl?.click()}>⬆ Import bundle (.zip)</button>
      <input bind:this={fileEl} type="file" accept=".zip" style="display:none" onchange={doImport} />
    </div>
  </div>

  {#if msg}<div class="okf-msg">{msg}</div>{/if}

  <div class="okf-bar">
    <div class="okf-count">Pending review: <b>{pending.total}</b></div>
    <div style="flex:1"></div>
    <button class="okf-btn" disabled={busy} onclick={loadPending}>↻ Refresh</button>
    <button class="okf-btn okf-reject" disabled={busy || pending.total === 0} onclick={reject}>✕ Reject all</button>
    <button class="okf-btn okf-approve" disabled={busy || pending.total === 0} onclick={promote}>✓ Promote to live</button>
  </div>

  {#if loading}
    <div class="okf-empty">loading…</div>
  {:else if pending.total === 0}
    <div class="okf-empty">No pending OKF knowledge. Import a bundle to review it here. Imported items stay isolated (chat ignores them) until you Promote.</div>
  {:else}
    {#if pending.facts.length}
      <div class="okf-group">Facts ({pending.facts.length})</div>
      {#each pending.facts as f (f.id)}<div class="okf-row">🧠 {f.text}</div>{/each}
    {/if}
    {#if pending.queries.length}
      <div class="okf-group">Verified Queries ({pending.queries.length})</div>
      {#each pending.queries as q (q.id)}<div class="okf-row">🔎 <b>{q.question}</b><pre>{q.sql}</pre></div>{/each}
    {/if}
    {#if pending.triples.length}
      <div class="okf-group">Relationships ({pending.triples.length})</div>
      {#each pending.triples as t (t.id)}<div class="okf-row">🕸 {t.text}</div>{/each}
    {/if}
  {/if}
</div>

<style>
  .okf { padding: 4px 2px; }
  .okf-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; }
  .okf-title { font-size: 15px; font-weight: 800; }
  .okf-sub { font-size: 12px; color: var(--pw-muted, #8a8175); margin-top: 3px; max-width: 520px; }
  .okf-actions { display: flex; gap: 8px; flex: none; }
  .okf-btn { font-size: 12px; font-weight: 600; padding: 6px 12px; border-radius: var(--pw-radius-sm, 8px); border: 1px solid var(--pw-border, #e6e1d7); background: var(--pw-bg, #fff); color: var(--pw-fg, #1c1b18); cursor: pointer; }
  .okf-btn:hover:not(:disabled) { border-color: var(--pw-accent, #c2683f); }
  .okf-btn:disabled { opacity: .5; cursor: default; }
  .okf-primary { background: #1c1b18; color: #fff; border-color: #1c1b18; }
  .okf-approve { background: #3f8f5f; color: #fff; border-color: #3f8f5f; }
  .okf-reject { color: #c0492f; border-color: #e7c4ba; }
  .okf-bar { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-top: 1px solid var(--pw-border, #eee); border-bottom: 1px solid var(--pw-border, #eee); margin-bottom: 10px; }
  .okf-count { font-size: 13px; }
  .okf-msg { font-size: 12px; padding: 7px 11px; background: #f4f1ea; border-radius: var(--pw-radius-sm, 8px); margin-bottom: 10px; }
  .okf-group { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; color: var(--pw-muted, #8a8175); margin: 12px 0 6px; }
  .okf-row { font-size: 13px; padding: 8px 10px; border: 1px solid var(--pw-border, #eee); border-radius: var(--pw-radius-sm, 8px); margin-bottom: 6px; background: var(--pw-bg, #fff); }
  .okf-row pre { font-size: 11px; background: #f5f3ee; padding: 7px; border-radius: 6px; overflow: auto; margin: 5px 0 0; }
  .okf-empty { font-size: 13px; color: var(--pw-muted, #8a8175); padding: 22px 4px; }
</style>
