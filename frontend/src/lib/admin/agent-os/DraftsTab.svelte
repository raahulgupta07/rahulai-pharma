<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 type Verifier = { pass?: boolean; score?: number };
 type Draft = {
 id: string;
 proposed_name?: string;
 status?: string;
 trigger_phrase?: string;
 drafted_by_agent?: string;
 created_at?: string;
 proposed_skill_md?: string;
 verifier_results?: {
 smoke?: Verifier;
 reliability?: Verifier;
 llm_judge?: Verifier;
 regression?: Verifier;
 overall_score?: number;
 };
 };

 let drafts = $state<Draft[]>([]);
 let loading = $state(true);
 let selected = $state<Draft | null>(null);
 let showReject = $state(false);
 let rejectReason = $state('');
 let toast = $state<{ msg: string; ok: boolean } | null>(null);

 const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);
 const authHeaders = (): Record<string, string> => {
 const t = token();
 return t ? { Authorization: `Bearer ${t}` } : {};
 };

 async function load() {
 loading = true;
 try {
 const r = await fetch('/api/skill-drafts?status=pending', { headers: authHeaders() });
 if (r.ok) {
 const j = await r.json();
 drafts = Array.isArray(j) ? j : j.drafts || j.items || [];
 }
 } catch {}
 loading = false;
 }

 async function openDraft(d: Draft) {
 try {
 const r = await fetch(`/api/skill-drafts/${d.id}`, { headers: authHeaders() });
 if (r.ok) selected = await r.json();
 else selected = d;
 } catch {
 selected = d;
 }
 }

 function flash(msg: string, ok = true) {
 toast = { msg, ok };
 setTimeout(() => (toast = null), 2500);
 }

 async function approve() {
 if (!selected) return;
 try {
 const r = await fetch(`/api/skill-drafts/${selected.id}/approve`, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', ...authHeaders() },
 body: JSON.stringify({})
 });
 const j = await r.json().catch(() => ({}));
 flash(j.ok || r.ok ? 'Approved' : 'Failed', j.ok || r.ok);
 if (r.ok) { selected = null; load(); }
 } catch { flash('Failed', false); }
 }

 async function reject() {
 if (!selected || !rejectReason.trim()) return;
 try {
 const r = await fetch(`/api/skill-drafts/${selected.id}/reject`, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', ...authHeaders() },
 body: JSON.stringify({ reason: rejectReason })
 });
 flash(r.ok ? 'Rejected' : 'Failed', r.ok);
 if (r.ok) { selected = null; showReject = false; rejectReason = ''; load(); }
 } catch { flash('Failed', false); }
 }

 async function reverify() {
 if (!selected) return;
 try {
 const r = await fetch(`/api/skill-drafts/${selected.id}/verify`, {
 method: 'POST',
 headers: authHeaders()
 });
 flash(r.ok ? 'Re-verifying' : 'Failed', r.ok);
 if (r.ok) openDraft(selected);
 } catch { flash('Failed', false); }
 }

 function fmtDate(s?: string) {
 if (!s) return '—';
 try { return new Date(s).toLocaleString(); } catch { return s; }
 }

 function overallScore(d: Draft): number {
 const v = d.verifier_results;
 if (!v) return 0;
 if (typeof v.overall_score === 'number') return v.overall_score;
 const scores = [v.smoke?.score, v.reliability?.score, v.llm_judge?.score, v.regression?.score].filter((x): x is number => typeof x === 'number');
 if (!scores.length) return 0;
 return scores.reduce((a, b) => a + b, 0) / scores.length;
 }

 onMount(load);
</script>

<div class="wrap">
  <div class="toolbar">
    <div class="muted">{drafts.length} pending draft{drafts.length === 1 ? '' : 's'}</div>
    <button class="btn-ghost" onclick={load}>↻ Refresh</button>
  </div>

  {#if loading}
    <div class="empty">Loading…</div>
  {:else if !drafts.length}
    <div class="empty">No pending drafts.</div>
  {:else}
    <table class="tbl">
      <thead>
        <tr>
          <th>NAME</th><th>STATUS</th><th>VERIFIER SCORE</th>
          <th>TRIGGERED BY</th><th>CREATED</th><th>ACTIONS</th>
        </tr>
      </thead>
      <tbody>
        {#each drafts as d}
          {@const sc = overallScore(d)}
          <tr onclick={() => openDraft(d)}>
            <td class="mono">{d.proposed_name || d.id}</td>
            <td><span class="chip chip-amber">{d.status || 'pending'}</span></td>
            <td>
              <div class="pbar"><div class="pfill" style="width:{Math.round(sc * 100)}%"></div></div>
              <span class="muted s">{(sc * 100).toFixed(0)}%</span>
            </td>
            <td>{d.drafted_by_agent || '—'}</td>
            <td class="muted">{fmtDate(d.created_at)}</td>
            <td><button class="btn-link" onclick={(e) => { e.stopPropagation(); openDraft(d); }}>Review →</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

{#if selected}
  <div class="backdrop" onclick={() => (selected = null)}></div>
  <aside class="drawer">
    <header>
      <h3>{selected.proposed_name || selected.id}</h3>
      <button class="btn-ghost" onclick={() => (selected = null)}><Icon name="x" size={14} /></button>
    </header>

    <section>
      <div class="vgrid">
        {#each ['smoke','reliability','llm_judge','regression'] as k}
          {@const v = (selected.verifier_results as any)?.[k] || {}}
          <div class="vcard">
            <div class="vhead">
              <span class="dot" class:ok={v.pass} class:bad={v.pass === false}></span>
              <span class="vlbl">{k.replace('_', ' ')}</span>
            </div>
            <div class="vscore">{typeof v.score === 'number' ? (v.score * 100).toFixed(0) + '%' : '—'}</div>
          </div>
        {/each}
      </div>
    </section>

    <section>
      <h4>Proposed skill</h4>
      <pre class="skill">{selected.proposed_skill_md || '—'}</pre>
    </section>

    <footer>
      <button class="btn-primary" onclick={approve}>Approve</button>
      <button class="btn-danger" onclick={() => (showReject = true)}>Reject</button>
      <button class="btn-ghost" onclick={reverify}>Re-verify</button>
    </footer>

    {#if showReject}
      <div class="modal">
        <h4>Reject draft</h4>
        <textarea bind:value={rejectReason} placeholder="Reason…" rows="4"></textarea>
        <div class="row">
          <button class="btn-ghost" onclick={() => (showReject = false)}>Cancel</button>
          <button class="btn-danger" onclick={reject} disabled={!rejectReason.trim()}>Confirm reject</button>
        </div>
      </div>
    {/if}
  </aside>
{/if}

{#if toast}
  <div class="toast" class:bad={!toast.ok}>{toast.msg}</div>
{/if}

<style>
 .wrap { display: flex; flex-direction: column; gap: 12px; }
 .toolbar { display: flex; justify-content: space-between; align-items: center; }
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; }
 .s { font-size: 11px; margin-left: 6px; }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; background: var(--pw-surface, #faf9f5); }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; }
 .tbl th { text-align: left; padding: 10px 12px; background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); border-bottom: 1px solid var(--pw-border, #e7e3da); }
 .tbl td { padding: 10px 12px; border-bottom: 1px solid var(--pw-border, #e7e3da); font-size: 13px; }
 .tbl tbody tr { cursor: pointer; }
 .tbl tbody tr:hover { background: var(--pw-bg-alt, #f1ede4); }
 .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 13px; }
 .chip { display: inline-block; padding: 2px 8px; border-radius: 0; font: 600 10px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.04em; }
 .chip-amber { background: rgba(217, 119, 6, 0.14); color: #b45309; }
 .pbar { display: inline-block; width: 80px; height: 6px; background: var(--pw-bg-alt, #f1ede4); border-radius: 0; overflow: hidden; vertical-align: middle; }
 .pfill { height: 100%; background: var(--pw-accent, #c96342); }
 .btn-ghost { background: none; border: 1px solid var(--pw-border, #e7e3da); padding: 6px 10px; font-size: 12px; cursor: pointer; border-radius: 0; color: var(--pw-ink, #2c2a26); }
 .btn-link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font-size: 12px; font-weight: 600; }
 .btn-primary { background: var(--pw-accent, #c96342); color: white; border: none; padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; border-radius: 0; }
 .btn-danger { background: #dc2626; color: white; border: none; padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; border-radius: 0; }
 .btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }
 .backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.2); z-index: 50; }
 .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: 480px; background: var(--pw-surface, #faf9f5); border-left: 1px solid var(--pw-border, #e7e3da); z-index: 51; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
 .drawer header { display: flex; justify-content: space-between; align-items: center; }
 .drawer h3 { margin: 0; font: 600 18px 'Source Serif 4', Georgia, serif; color: var(--pw-ink, #2c2a26); }
 .drawer h4 { margin: 0 0 8px; font: 600 12px Inter; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .vgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
 .vcard { padding: 10px; background: var(--pw-bg-alt, #f1ede4); border-radius: 0; }
 .vhead { display: flex; align-items: center; gap: 6px; font-size: 11px; text-transform: uppercase; color: var(--pw-ink-soft, #87837a); }
 .dot { width: 8px; height: 8px; border-radius: 50%; background: #999; }
 .dot.ok { background: #16a34a; }
 .dot.bad { background: #dc2626; }
 .vlbl { font-weight: 600; }
 .vscore { font: 600 18px 'Source Serif 4', Georgia, serif; color: var(--pw-ink, #2c2a26); margin-top: 4px; }
 .skill { background: var(--pw-bg-alt, #f1ede4); padding: 12px; border-radius: 0; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; white-space: pre-wrap; max-height: 320px; overflow: auto; margin: 0; }
 .drawer footer { display: flex; gap: 8px; padding-top: 8px; border-top: 1px solid var(--pw-border, #e7e3da); }
 .modal { position: absolute; inset: 20px; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.1); display: flex; flex-direction: column; gap: 10px; }
 .modal textarea { width: 100%; padding: 8px; font: 13px Inter; border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; resize: vertical; box-sizing: border-box; }
 .row { display: flex; justify-content: flex-end; gap: 8px; }
 .toast { position: fixed; bottom: 24px; right: 24px; background: #16a34a; color: white; padding: 10px 16px; border-radius: 0; font: 600 13px Inter; z-index: 100; }
 .toast.bad { background: #dc2626; }
</style>
