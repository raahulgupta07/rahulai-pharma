<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import { confirmDelete } from '$lib/confirmDelete';

 let presentations = $state<any[]>([]);
 let loading = $state(true);
 let searchQuery = $state('');
 let searchInput: HTMLInputElement | null = $state(null);
 let activeListTab = $state<'all' | 'recent' | 'favorites'>('all');
 let favorites = $state<Set<string>>(new Set());

 function presKey(p: any): string { return `pres:${p.project_slug}:${p.id}`; }
 function toggleFav(k: string) {
 if (favorites.has(k)) favorites.delete(k); else favorites.add(k);
 favorites = new Set(favorites);
 try { localStorage.setItem('dash_pres_favs', JSON.stringify([...favorites])); } catch {}
 }
 function handleKeydown(e: KeyboardEvent) {
 if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); searchInput?.focus(); }
 }

 const filteredPres = $derived(
 presentations.filter((p) => {
 if (activeListTab === 'favorites' && !favorites.has(presKey(p))) return false;
 if (activeListTab === 'recent') {
 const ageH = (Date.now() - new Date(p.created_at).getTime()) / 3600000;
 if (ageH > 168) return false;
 }
 const q = searchQuery.trim().toLowerCase();
 if (!q) return true;
 const hay = `${p.title || ''} ${p.project_name || ''} ${p.project_slug || ''}`.toLowerCase();
 return hay.includes(q);
 })
 );
 // Phase 7: schedule delivery drawer state
 let scheduleOpen = $state(false);
 let scheduleTarget = $state<any | null>(null);
 let schedCron = $state('0 9 * * MON');
 let schedRecipients = $state('');
 let schedFormat = $state<'pptx' | 'pdf' | 'both'>('pptx');
 let schedName = $state('');
 let schedSaving = $state(false);
 let schedError = $state('');
 let schedStubMode = $state<boolean | null>(null);
 let schedExisting = $state<any[]>([]);
 let schedRunResult = $state('');

 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { 'Authorization': `Bearer ${t}` } : {};
 }

 function timeAgo(ts: string | null): string {
 if (!ts) return '';
 const diff = Date.now() - new Date(ts).getTime();
 const mins = Math.floor(diff / 60000);
 if (mins < 60) return `${mins}m ago`;
 const hrs = Math.floor(mins / 60);
 if (hrs < 24) return `${hrs}h ago`;
 return `${Math.floor(hrs / 24)}d ago`;
 }

 onMount(async () => {
 try {
 const projRes = await fetch('/api/projects', { headers: _h() });
 if (projRes.ok) {
 const projData = await projRes.json();
 const allPres: any[] = [];
 for (const p of projData.projects || []) {
 const presRes = await fetch(`/api/export/presentations?project=${p.slug}`, { headers: _h() });
 if (presRes.ok) {
 const presData = await presRes.json();
 for (const pr of presData.presentations || []) {
 allPres.push({ ...pr, project_slug: p.slug, project_name: p.agent_name });
 }
 }
 }
 presentations = allPres.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
 }
 } catch {}
 loading = false;
 try { const f = localStorage.getItem('dash_pres_favs'); if (f) favorites = new Set(JSON.parse(f)); } catch {}
 if (typeof window !== 'undefined') window.addEventListener('keydown', handleKeydown);
 });

 async function deletePres(id: number) {
 const pres = presentations.find(p => p.id === id);
 const name = pres?.title || `presentation #${id}`;
 if (!(await confirmDelete({ itemName: name, itemType: 'presentation' }))) return;
 await fetch(`/api/export/presentations/${id}`, { method: 'DELETE', headers: _h() });
 presentations = presentations.filter(p => p.id !== id);
 }

 async function downloadPptx(id: number, title: string) {
 const res = await fetch(`/api/export/presentations/${id}/pptx`, { method: 'POST', headers: _h() });
 if (res.ok) {
 const blob = await res.blob();
 const url = URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url;
 a.download = `${title}.pptx`;
 a.click();
 URL.revokeObjectURL(url);
 }
 }

 // Phase 7: schedule delivery
 const CRON_PRESETS = [
   { label: 'Daily 9am', cron: '0 9 * * *' },
   { label: 'Weekly Mon 9am', cron: '0 9 * * MON' },
   { label: 'Monthly 1st 9am', cron: '0 9 1 * *' }
 ];

 async function checkStubMode() {
   try {
     const r = await fetch('/api/health/distribution-stub-mode', { headers: _h() });
     if (r.ok) {
       const d = await r.json();
       schedStubMode = !!d.stub;
     }
   } catch { schedStubMode = null; }
 }

 async function openSchedule(pres: any) {
   scheduleTarget = pres;
   schedName = `${pres.title} weekly`;
   schedCron = '0 9 * * MON';
   schedRecipients = '';
   schedFormat = 'pptx';
   schedError = '';
   schedRunResult = '';
   schedExisting = [];
   scheduleOpen = true;
   await checkStubMode();
   try {
     const r = await fetch(`/api/presentations/${pres.id}/schedules`, { headers: _h() });
     if (r.ok) {
       const d = await r.json();
       schedExisting = d.schedules || [];
     }
   } catch {}
 }

 function closeSchedule() {
   scheduleOpen = false;
   scheduleTarget = null;
 }

 async function saveSchedule() {
   if (!scheduleTarget) return;
   schedError = '';
   const recipients = schedRecipients.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
   if (recipients.length === 0) { schedError = 'Add at least one recipient'; return; }
   const hasEmail = recipients.some(r => r.includes('@'));
   const hasSlack = recipients.some(r => r.startsWith('#') || r.toLowerCase().startsWith('slack:'));
   const channel = hasEmail && hasSlack ? 'both' : hasSlack ? 'slack' : 'email';
   schedSaving = true;
   try {
     const r = await fetch(`/api/presentations/${scheduleTarget.id}/schedule`, {
       method: 'POST',
       headers: { ..._h(), 'Content-Type': 'application/json' },
       body: JSON.stringify({
         name: schedName || 'Scheduled deck',
         cron: schedCron,
         recipients,
         channel,
         format: schedFormat,
         enabled: true
       })
     });
     if (!r.ok) {
       const txt = await r.text();
       schedError = `Save failed: ${r.status} ${txt.slice(0, 200)}`;
     } else {
       const newRow = await r.json();
       schedExisting = [newRow, ...schedExisting];
       schedRecipients = '';
     }
   } catch (e: any) {
     schedError = `Save error: ${e?.message || e}`;
   } finally {
     schedSaving = false;
   }
 }

 async function runNow(scheduleId: number) {
   schedRunResult = 'Running...';
   try {
     const r = await fetch(`/api/presentations/schedules/${scheduleId}/run-now`, {
       method: 'POST',
       headers: _h()
     });
     const d = await r.json().catch(() => ({}));
     if (!r.ok) {
       schedRunResult = `Failed: ${r.status} ${d?.detail || ''}`;
     } else {
       const ds = d.delivery_status || d.status || 'done';
       const to = (d.delivered_to || []).join(', ');
       schedRunResult = `${ds} → ${to || '(no recipients)'}`;
       // Refresh list to pick up last_run_at
       if (scheduleTarget) {
         try {
           const rr = await fetch(`/api/presentations/${scheduleTarget.id}/schedules`, { headers: _h() });
           if (rr.ok) {
             const dd = await rr.json();
             schedExisting = dd.schedules || [];
           }
         } catch {}
       }
     }
   } catch (e: any) {
     schedRunResult = `Error: ${e?.message || e}`;
   }
 }

 async function deleteSchedule(scheduleId: number) {
   if (!confirm('Delete this schedule?')) return;
   try {
     const r = await fetch(`/api/presentations/schedules/${scheduleId}`, {
       method: 'DELETE',
       headers: _h()
     });
     if (r.ok) {
       schedExisting = schedExisting.filter(s => s.id !== scheduleId);
     }
   } catch {}
 }

 async function toggleEnabled(s: any) {
   try {
     const r = await fetch(`/api/presentations/schedules/${s.id}`, {
       method: 'PATCH',
       headers: { ..._h(), 'Content-Type': 'application/json' },
       body: JSON.stringify({ enabled: !s.enabled })
     });
     if (r.ok) {
       const updated = await r.json();
       schedExisting = schedExisting.map(x => x.id === updated.id ? updated : x);
     }
   } catch {}
 }
</script>

<div class="ds-page">
  <div class="ds-page-head">
    <div>
      <h1 class="ds-page-title">Presentations</h1>
      <div class="ds-page-sub">{presentations.length} saved {presentations.length === 1 ? 'deck' : 'decks'} · download or schedule delivery</div>
    </div>
    <div class="pres-header-right">
      <div class="pres-search-wrap">
        <svg class="pres-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input bind:this={searchInput} bind:value={searchQuery} type="text" placeholder="Search decks…" class="ds-input pres-search-input" />
        <kbd class="pres-kbd">K</kbd>
      </div>
      <div class="pres-filter-pills">
        {#each [ {id:'all', label:'All'}, {id:'recent', label:'Recent'}, {id:'favorites', label:'Favorites'} ] as tab}
          <button class="pill-segment" class:active={activeListTab === tab.id} onclick={() => activeListTab = tab.id as any}>{tab.label}</button>
        {/each}
      </div>
    </div>
  </div>

  {#if loading}
    <div class="ds-empty">
      <div class="ds-empty-text">Loading…</div>
    </div>
  {:else if presentations.length === 0}
    <div class="ds-empty">
      <div class="ds-empty-icon">▤</div>
      <div class="ds-empty-title">No decks yet</div>
      <div class="ds-empty-text">Chat with an agent, then click the P button to create a presentation. Save to keep it here.</div>
    </div>
  {:else}
    <div class="pres-grid">
      {#each filteredPres as pres}
        <div class="pres-card">
          <div class="pres-card-head">
            <div class="pres-card-icon">{(pres.title || 'P')[0]?.toUpperCase()}</div>
            <div class="pres-card-title">
              <h3>
                {#if favorites.has(presKey(pres))}<span class="pres-star-inline" title="Starred">★</span>{/if}
                {pres.title}
              </h3>
              <p class="pres-card-cat">{pres.project_name} · v{pres.version}</p>
            </div>
            <button class="pres-icon-btn" title={favorites.has(presKey(pres)) ? 'Unstar' : 'Star'} aria-label="Favorite" onclick={() => toggleFav(presKey(pres))}>{favorites.has(presKey(pres)) ? '★' : '☆'}</button>
            <button class="pres-icon-btn" title="Download PPTX" aria-label="Download" onclick={() => downloadPptx(pres.id, pres.title)}>↓</button>
            <button class="pres-icon-btn" title="Schedule" aria-label="Schedule" onclick={() => openSchedule(pres)}>📅</button>
            <button class="pres-icon-btn" title="Delete" aria-label="Delete" onclick={() => deletePres(pres.id)}><Icon name="x" size={14} /></button>
          </div>

          {#if pres.thinking?.narrative}
            <p class="pres-card-desc">{pres.thinking.narrative}</p>
          {:else}
            <p class="pres-card-desc">Generated deck · ready to download or schedule</p>
          {/if}

          <div class="pres-card-status">
            <span class="pres-card-status-dot ready"></span>
            <span>Created {timeAgo(pres.created_at)}</span>
          </div>

          <a class="pres-open-cta" href="/ui/presentations/{pres.id}">
            <span class="pres-open-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="14" rx="1"/><path d="M3 9h18"/></svg>
            </span>
            <span class="pres-open-label">Open deck</span>
            <span class="pres-open-arrow">→</span>
          </a>
        </div>
      {/each}
    </div>
  {/if}
</div>

{#if scheduleOpen && scheduleTarget}
  <div class="sched-overlay" onclick={closeSchedule} role="presentation"></div>
  <div class="sched-drawer" role="dialog" aria-label="Schedule deck delivery">
    <div class="sched-head">
      <div>
        <div class="sched-title">📅 Schedule delivery</div>
        <div class="sched-sub">{scheduleTarget.title}</div>
      </div>
      <button class="btn-ghost btn-sm" onclick={closeSchedule} aria-label="Close">×</button>
    </div>

    {#if schedStubMode}
      <div class="sched-stub-banner">
        ⚠ Stub mode: SMTP/Slack credentials not set. Saved schedules will log intended sends without delivering.
        Configure <code>SMTP_HOST</code>, <code>SLACK_BOT_TOKEN</code>, or <code>SLACK_WEBHOOK_URL</code> to enable live delivery.
      </div>
    {/if}

    <div class="sched-body">
      <label class="sched-label">Schedule name</label>
      <input class="sched-input" bind:value={schedName} placeholder="e.g. Weekly board update" />

      <label class="sched-label">Cron expression</label>
      <input class="sched-input" bind:value={schedCron} placeholder="0 9 * * MON" />
      <div class="sched-presets">
        {#each CRON_PRESETS as p}
          <button class="sched-chip" type="button" onclick={() => (schedCron = p.cron)}>{p.label}</button>
        {/each}
      </div>

      <label class="sched-label">Recipients (one per line — email or #channel)</label>
      <textarea class="sched-input sched-textarea" bind:value={schedRecipients} rows="4"
        placeholder="alice@example.com&#10;#exec-updates&#10;slack:CXXXX"></textarea>

      <label class="sched-label">Format</label>
      <div class="sched-radios">
        <label><input type="radio" bind:group={schedFormat} value="pptx" /> PPTX</label>
        <label><input type="radio" bind:group={schedFormat} value="pdf" /> PDF</label>
        <label><input type="radio" bind:group={schedFormat} value="both" /> Both</label>
      </div>

      {#if schedError}
        <div class="sched-err">{schedError}</div>
      {/if}

      <div class="sched-actions">
        <button class="btn-primary btn-sm" disabled={schedSaving} onclick={saveSchedule}>
          {schedSaving ? 'Saving…' : 'Save schedule'}
        </button>
      </div>

      {#if schedExisting.length > 0}
        <div class="sched-section-title">Existing schedules</div>
        <div class="sched-list">
          {#each schedExisting as s}
            <div class="sched-row">
              <div class="sched-row-main">
                <div class="sched-row-name">
                  <span class="sched-dot" class:on={s.enabled}></span>
                  <strong>{s.name}</strong>
                  <span class="sched-mono">{s.cron}</span>
                </div>
                <div class="sched-row-meta">
                  {(s.recipients || []).length} recipient(s) · {s.format} · {s.channel}
                  {#if s.last_run_at}· last: {s.last_status || 'ran'} @ {new Date(s.last_run_at).toLocaleString()}{/if}
                  {#if s.last_error}<span class="sched-err-inline"> ⚠ {s.last_error}</span>{/if}
                </div>
              </div>
              <div class="sched-row-actions">
                <button class="btn-secondary btn-sm" onclick={() => runNow(s.id)}>Run now</button>
                <button class="btn-ghost btn-sm" onclick={() => toggleEnabled(s)}>
                  {s.enabled ? 'Disable' : 'Enable'}
                </button>
                <button class="btn-ghost btn-sm" onclick={() => deleteSchedule(s.id)} aria-label="Delete">🗑</button>
              </div>
            </div>
          {/each}
        </div>
        {#if schedRunResult}
          <div class="sched-run-result">Run result: {schedRunResult}</div>
        {/if}
      {/if}
    </div>
  </div>
{/if}

<style>
 /* Header — mirror Dashboards header */
 .pres-header-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
 .pres-search-wrap { position: relative; display: flex; align-items: center; width: 280px; }
 .pres-search-icon { position: absolute; left: 12px; color: var(--pw-muted); pointer-events: none; }
 .pres-search-input { width: 100%; height: 36px; padding: 0 56px 0 34px; }
 .pres-kbd {
 position: absolute; right: 10px;
 font-family: var(--pw-font-body); font-size: 10px;
 color: var(--pw-muted); background: var(--pw-bg-alt);
 border: 1px solid var(--pw-border); border-radius: 0;
 padding: 1px 5px; pointer-events: none;
 }
 .pres-filter-pills {
 display: inline-flex;
 background: var(--pw-bg-alt);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 3px; gap: 2px;
 }
 .pres-star-inline { color: var(--pw-accent); margin-right: 2px; }

 /* Grid + card — mirror Dashboards card */
 .pres-grid {
 display: grid;
 grid-template-columns: repeat(3, minmax(0, 1fr));
 gap: 24px;
 }
 @media (max-width: 1100px) {
 .pres-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
 }
 @media (max-width: 700px) {
 .pres-grid { grid-template-columns: minmax(0, 1fr); }
 }
 .pres-card {
 position: relative;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius, 16px);
 padding: 24px;
 display: flex;
 flex-direction: column;
 gap: 14px;
 box-shadow: var(--pw-shadow-sm);
 transition: box-shadow .15s, transform .15s, border-color .15s;
 overflow: visible;
 }
 .pres-card:hover {
 box-shadow: var(--pw-shadow-md);
 transform: translateY(-2px);
 border-color: var(--pw-border-strong);
 }
 .pres-card-head { display: flex; align-items: flex-start; gap: 12px; }
 .pres-card-icon {
 width: 36px; height: 36px; border-radius: 0;
 background: var(--pw-accent); color: #fff;
 display: grid; place-items: center;
 font-family: var(--pw-font-headline);
 font-weight: 600; font-size: 14px; flex: 0 0 auto;
 }
 .pres-card-title { flex: 1; min-width: 0; }
 .pres-card-title h3 {
 font-family: var(--pw-font-headline);
 font-size: 15px; font-weight: 500;
 letter-spacing: -0.015em;
 margin: 0 0 2px; color: var(--pw-ink);
 white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
 }
 .pres-card-cat { font-size: 12.5px; color: var(--pw-muted); margin: 0; }
 .pres-icon-btn {
 width: 28px; height: 28px; border: none; background: transparent;
 color: var(--pw-muted); cursor: pointer;
 display: grid; place-items: center; flex: 0 0 auto;
 transition: background .15s, color .15s;
 }
 .pres-icon-btn:hover { background: var(--pw-bg-alt); color: var(--pw-ink); }
 .pres-card-desc {
 font-size: 13.5px; color: var(--pw-ink-soft);
 line-height: 1.5; margin: 0;
 display: -webkit-box; -webkit-line-clamp: 2;
 -webkit-box-orient: vertical; overflow: hidden;
 }
 .pres-card-status {
 display: flex; align-items: center; gap: 8px;
 font-size: 12.5px; color: var(--pw-muted);
 }
 .pres-card-status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--pw-success); }
 .pres-card-row { display: flex; gap: 8px; }
 .pres-mini-btn {
 flex: 1; padding: 7px 10px;
 background: var(--pw-bg-alt); border: 1px solid var(--pw-border);
 color: var(--pw-ink); font-size: 12px; cursor: pointer;
 transition: background .12s, border-color .12s;
 }
 .pres-mini-btn:hover { background: var(--pw-surface); border-color: var(--pw-border-strong); }
 .pres-open-cta {
 display: flex; align-items: center; gap: 10px;
 width: calc(100% + 40px);
 margin: 16px -20px -20px;
 padding: 12px 20px;
 background: transparent;
 border: none;
 border-top: 1px solid var(--pw-border-soft);
 font: inherit; font-size: 12px; font-weight: 500;
 color: var(--pw-accent);
 text-decoration: none;
 cursor: pointer;
 transition: background .12s;
 margin-top: auto;
 }
 .pres-open-cta:hover { background: var(--pw-accent-wash); }
 .pres-open-cta:hover .pres-open-arrow { transform: translateX(2px); }
 .pres-open-icon { display: inline-flex; align-items: center; }
 .pres-open-label { flex: 1; text-align: left; }
 .pres-open-arrow { transition: transform .15s; opacity: .75; }

 /* Phase 7: schedule delivery drawer */
 .sched-overlay {
   position: fixed; inset: 0; background: rgba(0,0,0,0.35); z-index: 100;
 }
 .sched-drawer {
   position: fixed; right: 0; top: 0; bottom: 0; width: 480px; max-width: 100vw;
   background: var(--pw-surface, #fff); border-left: 1px solid var(--pw-border);
   box-shadow: -4px 0 18px rgba(0,0,0,0.18); z-index: 101;
   display: flex; flex-direction: column; overflow: hidden;
 }
 .sched-head {
   display: flex; align-items: flex-start; justify-content: space-between;
   padding: 16px; border-bottom: 1px solid var(--pw-border);
 }
 .sched-title { font-family: var(--pw-serif); font-size: 18px; font-weight: 600; }
 .sched-sub { font-size: 12px; color: var(--pw-muted); margin-top: 2px; }
 .sched-stub-banner {
   background: #fff6e0; border-bottom: 1px solid #f0d690; color: #715300;
   padding: 10px 16px; font-size: 12px; line-height: 1.45;
 }
 .sched-stub-banner code { background: #f0e1b8; padding: 1px 4px; border-radius: 3px; }
 .sched-body { padding: 16px; overflow-y: auto; flex: 1; }
 .sched-label {
   display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
   color: var(--pw-muted); margin-top: 14px; margin-bottom: 6px; font-weight: 600;
 }
 .sched-input {
   width: 100%; padding: 8px 10px; border: 1px solid var(--pw-border);
   background: var(--pw-bg, #fff); font-size: 13px; box-sizing: border-box;
 }
 .sched-textarea { font-family: ui-monospace, Menlo, monospace; resize: vertical; }
 .sched-presets { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
 .sched-chip {
   font-size: 11px; padding: 4px 9px; border: 1px solid var(--pw-border);
   background: var(--pw-bg-alt, #f4f1ec); cursor: pointer; color: var(--pw-ink);
 }
 .sched-chip:hover { background: var(--pw-accent-soft, #fae5dc); }
 .sched-radios { display: flex; gap: 16px; font-size: 13px; }
 .sched-radios label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
 .sched-err {
   margin-top: 12px; padding: 8px 10px; background: #fdecec; border-left: 3px solid #c0392b;
   color: #8a1f1f; font-size: 12px;
 }
 .sched-err-inline { color: #c0392b; }
 .sched-actions { margin-top: 16px; display: flex; gap: 8px; }
 .sched-section-title {
   font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
   color: var(--pw-muted); margin-top: 22px; margin-bottom: 8px;
   border-top: 1px solid var(--pw-border); padding-top: 14px; font-weight: 600;
 }
 .sched-list { display: flex; flex-direction: column; gap: 8px; }
 .sched-row {
   display: flex; align-items: flex-start; justify-content: space-between;
   gap: 10px; padding: 10px; border: 1px solid var(--pw-border); background: var(--pw-bg-alt, #fafaf7);
 }
 .sched-row-main { flex: 1; min-width: 0; }
 .sched-row-name { display: flex; align-items: center; gap: 8px; font-size: 13px; }
 .sched-row-meta { font-size: 11.5px; color: var(--pw-muted); margin-top: 4px; line-height: 1.4; word-break: break-word; }
 .sched-row-actions { display: flex; gap: 4px; flex-shrink: 0; }
 .sched-dot {
   display: inline-block; width: 8px; height: 8px; border-radius: 50%;
   background: var(--pw-muted, #999);
 }
 .sched-dot.on { background: #2e8b57; }
 .sched-mono { font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; color: var(--pw-muted); }
 .sched-run-result {
   margin-top: 10px; font-size: 12px; padding: 8px 10px;
   background: var(--pw-bg-alt, #f4f1ec); border-left: 3px solid var(--pw-accent, #c96342);
 }
</style>
