<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 type Draft = {
 id: string;
 project_slug?: string | null;
 drafted_by_agent?: string | null;
 trigger_phrase?: string | null;
 proposed_name?: string | null;
 proposed_description?: string | null;
 proposed_skill_md?: string | null;
 frontmatter?: any;
 verifier_results?: any;
 status: string;
 rejection_reason?: string | null;
 created_at?: string;
 };

 let drafts = $state<Draft[]>([]);
 let loading = $state(true);
 let filter = $state<'all' | 'pending' | 'verified' | 'approved' | 'rejected'>('pending');
 let selected = $state<Draft | null>(null);
 let showReject = $state(false);
 let rejectReason = $state('');
 let toast = $state<{ msg: string; ok: boolean } | null>(null);

 const token = (): string | null =>
 typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;

 async function loadDrafts() {
 loading = true;
 const q = filter === 'all' ? '' : `?status=${filter}`;
 try {
 const r = await fetch(`/api/skill-drafts${q}`, {
 headers: { Authorization: `Bearer ${token() || ''}` },
 });
 if (r.ok) {
 const d = await r.json();
 drafts = d.drafts || [];
 }
 } catch {}
 loading = false;
 }

 async function openDraft(d: Draft) {
 try {
 const r = await fetch(`/api/skill-drafts/${d.id}`, {
 headers: { Authorization: `Bearer ${token() || ''}` },
 });
 if (r.ok) selected = await r.json();
 else selected = d;
 } catch {
 selected = d;
 }
 }

 function closeDrawer() {
 selected = null;
 showReject = false;
 rejectReason = '';
 }

 function flashToast(msg: string, ok = true) {
 toast = { msg, ok };
 setTimeout(() => { toast = null; }, 2500);
 }

 async function approve() {
 if (!selected) return;
 try {
 const r = await fetch(`/api/skill-drafts/${selected.id}/approve`, {
 method: 'POST',
 headers: {
 'Content-Type': 'application/json',
 Authorization: `Bearer ${token() || ''}`,
 },
 body: JSON.stringify({}),
 });
 const j = await r.json();
 flashToast(j.ok ? 'Approved' : `Failed: ${j.error || 'unknown'}`, !!j.ok);
 } catch (e: any) {
 flashToast(`Error: ${e?.message || e}`, false);
 }
 closeDrawer();
 loadDrafts();
 }

 async function submitReject() {
 if (!selected || !rejectReason.trim()) return;
 try {
 const r = await fetch(`/api/skill-drafts/${selected.id}/reject`, {
 method: 'POST',
 headers: {
 'Content-Type': 'application/json',
 Authorization: `Bearer ${token() || ''}`,
 },
 body: JSON.stringify({ reason: rejectReason.trim() }),
 });
 const j = await r.json();
 flashToast(j.ok ? 'Rejected' : `Failed: ${j.error || 'unknown'}`, !!j.ok);
 } catch (e: any) {
 flashToast(`Error: ${e?.message || e}`, false);
 }
 closeDrawer();
 loadDrafts();
 }

 async function reverify() {
 if (!selected) return;
 try {
 const r = await fetch(`/api/skill-drafts/${selected.id}/re-verify`, {
 method: 'POST',
 headers: { Authorization: `Bearer ${token() || ''}` },
 });
 const j = await r.json();
 flashToast(j.ok ? 'Re-verified' : `Failed: ${j.error || 'unknown'}`, !!j.ok);
 if (j.ok) openDraft(selected);
 } catch (e: any) {
 flashToast(`Error: ${e?.message || e}`, false);
 }
 }

 function verifierScore(vr: any): string {
 if (!vr || typeof vr !== 'object') return '—';
 const keys = ['smoke', 'reliability', 'llm_judge', 'regression'];
 const scores = keys
 .map((k) => vr?.[k]?.score)
 .filter((x) => typeof x === 'number');
 if (!scores.length) return '—';
 const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
 return avg.toFixed(2);
 }

 function statusColor(s: string): string {
 if (s === 'approved' || s === 'auto_promoted') return '#2d6a4f';
 if (s === 'rejected') return '#c0392b';
 if (s === 'verified') return '#3b82f6';
 return '#999';
 }

 $effect(() => {
 void filter;
 loadDrafts();
 });

 onMount(loadDrafts);
</script>

<div class="page">
  <header class="hdr">
    <h1>Skill Drafts</h1>
    <div class="chips">
      {#each ['all', 'pending', 'verified', 'approved', 'rejected'] as f}
        <button
          class="chip"
          class:chip-active={filter === f}
          onclick={() => (filter = f as any)}
        >{f}</button>
      {/each}
    </div>
  </header>

  {#if loading}
    <div class="muted">Loading...</div>
  {:else if drafts.length === 0}
    <div class="empty">
      <div class="empty-title">No drafts.</div>
      <div class="empty-sub">Agents propose skills automatically after each chat.</div>
    </div>
  {:else}
    <table class="tbl">
      <thead>
        <tr>
          <th>NAME</th>
          <th>STATUS</th>
          <th>VERIFIER SCORE</th>
          <th>TRIGGERED BY</th>
          <th>CREATED</th>
          <th>ACTIONS</th>
        </tr>
      </thead>
      <tbody>
        {#each drafts as d}
          <tr onclick={() => openDraft(d)} class="row">
            <td class="name">{d.proposed_name || d.id}</td>
            <td>
              <span class="dot" style="background:{statusColor(d.status)}"></span>
              {d.status}
            </td>
            <td>{verifierScore(d.verifier_results)}</td>
            <td>{d.drafted_by_agent || '—'}</td>
            <td class="muted">{d.created_at ? new Date(d.created_at).toLocaleString() : '—'}</td>
            <td><span class="link">view →</span></td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

{#if selected}
  <div
    class="scrim"
    role="button"
    tabindex="0"
    onclick={closeDrawer}
    onkeydown={(e) => { if (e.key === 'Escape') closeDrawer(); }}
  ></div>
  <aside
    class="drawer"
    role="dialog"
    aria-label="Skill draft detail"
    onclick={(e) => e.stopPropagation()}
    onkeydown={() => {}}
  >
    <div class="drawer-hdr">
      <div>
        <div class="drawer-title">{selected.proposed_name || selected.id}</div>
        <div class="muted small">{selected.proposed_description || ''}</div>
      </div>
      <button class="close" onclick={closeDrawer}>×</button>
    </div>

    <section>
      <h3 class="sec-title">Frontmatter</h3>
      <pre class="code">{JSON.stringify(selected.frontmatter || {}, null, 2)}</pre>
    </section>

    <section>
      <h3 class="sec-title">Skill markdown</h3>
      <pre class="code skill-md">{selected.proposed_skill_md || ''}</pre>
    </section>

    <section>
      <h3 class="sec-title">Verifier results</h3>
      <div class="cards">
        {#each ['smoke', 'reliability', 'llm_judge', 'regression'] as k}
          {@const v = selected?.verifier_results?.[k]}
          <div class="card">
            <div class="card-hdr">
              <span class="dot" style="background:{v?.pass ? '#2d6a4f' : '#c0392b'}"></span>
              <span class="card-title">{k}</span>
            </div>
            <div class="card-score">{v?.score ?? '—'}</div>
            {#if v?.notes}<div class="muted small">{v.notes}</div>{/if}
          </div>
        {/each}
      </div>
      {#if selected.verifier_results?.improvement_hints?.length}
        <ul class="hints">
          {#each selected.verifier_results.improvement_hints as h}<li>{h}</li>{/each}
        </ul>
      {/if}
    </section>

    <div class="actions">
      <button class="btn btn-primary" onclick={approve}><Icon name="check" size={14} /> APPROVE</button>
      <button class="btn btn-danger" onclick={() => (showReject = true)}><Icon name="x" size={14} /> REJECT</button>
      <button class="btn btn-ghost" onclick={reverify}>↻ RE-VERIFY</button>
    </div>

    {#if showReject}
      <div class="reject-box">
        <label class="lbl">Reason (required)</label>
        <textarea bind:value={rejectReason} rows="3" placeholder="Why is this draft being rejected?"></textarea>
        <div class="reject-actions">
          <button class="btn btn-ghost" onclick={() => { showReject = false; rejectReason = ''; }}>Cancel</button>
          <button class="btn btn-danger" disabled={!rejectReason.trim()} onclick={submitReject}>Submit reject</button>
        </div>
      </div>
    {/if}
  </aside>
{/if}

{#if toast}
  <div class="toast" class:toast-err={!toast.ok}>{toast.msg}</div>
{/if}

<style>
 .page {
 padding: 24px 32px;
 max-width: 1200px;
 margin: 0 auto;
 color: var(--pw-ink, #2c2a26);
 font-size: 11px;
 }
 .hdr {
 display: flex;
 align-items: center;
 justify-content: space-between;
 margin-bottom: 16px;
 flex-wrap: wrap;
 gap: 12px;
 }
 h1 { font-size: 19px; margin: 0; font-weight: 600; }
 .chips { display: flex; gap: 6px; flex-wrap: wrap; }
 .chip {
 padding: 4px 10px;
 border-radius: 0;
 border: 1px solid var(--pw-bg-alt, #e8e3da);
 background: transparent;
 color: var(--pw-ink, #2c2a26);
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 cursor: pointer;
 }
 .chip-active {
 background: var(--pw-accent, #c96342);
 color: #fff;
 border-color: var(--pw-accent, #c96342);
 }
 .tbl { width: 100%; border-collapse: collapse; }
 .tbl th {
 text-align: left;
 padding: 8px 10px;
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.05em;
 color: #888;
 border-bottom: 1px solid var(--pw-bg-alt, #e8e3da);
 }
 .tbl td {
 padding: 10px;
 border-bottom: 1px solid var(--pw-bg-alt, #e8e3da);
 font-size: 11px;
 }
 .row { cursor: pointer; }
 .row:hover { background: rgba(201, 99, 66, 0.04); }
 .name { font-weight: 600; }
 .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
 .link { color: var(--pw-accent, #c96342); font-size: 11px; }
 .muted { color: #888; }
 .small { font-size: 11px; }
 .empty {
 text-align: center;
 padding: 60px 20px;
 border: 1px dashed var(--pw-bg-alt, #e8e3da);
 border-radius: 0;
 }
 .empty-title { font-size: 11px; font-weight: 600; }
 .empty-sub { font-size: 11px; color: #888; margin-top: 4px; }

 .scrim {
 position: fixed; inset: 0; background: rgba(0, 0, 0, 0.3); z-index: 9000;
 }
 .drawer {
 position: fixed; top: 56px; right: 0; bottom: 0;
 width: 480px; max-width: 100vw;
 background: var(--pw-bg, #fbf7f2);
 border-left: 1px solid var(--pw-bg-alt, #e8e3da);
 z-index: 9001;
 overflow-y: auto;
 padding: 16px;
 font-size: 11px;
 color: var(--pw-ink, #2c2a26);
 }
 .drawer-hdr {
 display: flex; align-items: start; justify-content: space-between;
 margin-bottom: 16px; gap: 12px;
 }
 .drawer-title { font-size: 12px; font-weight: 600; }
 .close {
 background: transparent; border: 0; font-size: 14px; cursor: pointer;
 color: var(--pw-ink, #2c2a26);
 }
 .sec-title {
 font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
 margin: 16px 0 6px; color: #888;
 }
 .code {
 background: var(--pw-bg-alt, #ece7de);
 padding: 10px;
 border-radius: 0;
 font-family: ui-monospace, monospace;
 font-size: 11px;
 overflow-x: auto;
 white-space: pre-wrap;
 max-height: 400px;
 overflow-y: auto;
 }
 .skill-md { max-height: 320px; }
 .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
 .card {
 border: 1px solid var(--pw-bg-alt, #e8e3da);
 border-radius: 0;
 padding: 8px 10px;
 }
 .card-hdr { display: flex; align-items: center; gap: 6px; }
 .card-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
 .card-score { font-size: 13px; font-weight: 600; margin-top: 4px; }
 .hints { font-size: 11px; padding-left: 18px; margin-top: 6px; }
 .actions { display: flex; gap: 8px; margin-top: 20px; }
 .btn {
 padding: 8px 14px; border-radius: 0; border: 1px solid transparent;
 font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
 cursor: pointer; font-weight: 600;
 }
 .btn-primary { background: var(--pw-accent, #c96342); color: #fff; }
 .btn-danger { background: #c0392b; color: #fff; }
 .btn-ghost {
 background: transparent;
 color: var(--pw-ink, #2c2a26);
 border-color: var(--pw-bg-alt, #e8e3da);
 }
 .btn:disabled { opacity: 0.5; cursor: not-allowed; }
 .reject-box {
 margin-top: 16px;
 padding: 12px;
 background: var(--pw-bg-alt, #ece7de);
 border-radius: 0;
 }
 .reject-box textarea {
 width: 100%; padding: 8px; border-radius: 0;
 border: 1px solid var(--pw-bg-alt, #e8e3da); font-size: 11px;
 font-family: inherit;
 }
 .reject-actions { display: flex; gap: 8px; margin-top: 8px; justify-content: end; }
 .lbl { display: block; font-size: 11px; margin-bottom: 6px; }
 .toast {
 position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
 padding: 8px 16px; background: #2d6a4f; color: #fff;
 border-radius: 6px; font-size: 11px; z-index: 9999;
 }
 .toast-err { background: #c0392b; }
</style>
