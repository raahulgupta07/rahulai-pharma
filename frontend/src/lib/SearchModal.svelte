<script lang="ts">
 let { open = $bindable(false), recents = [] } = $props<{ open?: boolean; recents?: any[] }>();
 let query = $state('');
 let activeIdx = $state(0);
 // Layout backfills `first_message` (same field the sidebar shows). Use the
 // same fallback chain so the modal shows real questions, not session IDs.
 const label = (r: any): string => r.title || r.first_message || (r.session_id ? String(r.session_id).slice(0, 8) : '');
 const filtered = $derived(
 query.trim()
 ? recents.filter((r: any) => label(r).toLowerCase().includes(query.toLowerCase()))
 : recents
 );
 function close() { open = false; query = ''; activeIdx = 0; }
 function pick(r: any) {
 try { localStorage.setItem('dash_session', r.session_id); } catch {}
 // Reopen in the project chat if this session belongs to one; else global chat.
 window.location.href = r.project_slug ? `/ui/project/${r.project_slug}` : '/ui/chat';
 }
 function onKey(e: KeyboardEvent) {
 if (!open) return;
 if (e.key === 'Escape') { e.preventDefault(); close(); }
 else if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, filtered.length - 1); }
 else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); }
 else if (e.key === 'Enter') { e.preventDefault(); if (filtered[activeIdx]) pick(filtered[activeIdx]); }
 }
</script>

<svelte:window onkeydown={onKey} />

{#if open}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="sm-backdrop" onclick={close}>
    <div class="sm-card" onclick={(e) => e.stopPropagation()}>
      <div class="sm-head">
        <span class="sm-ic"></span>
        <!-- svelte-ignore a11y_autofocus -->
        <input bind:value={query} placeholder="Search chats…" autofocus />
        <kbd>esc</kbd>
      </div>
      <div class="sm-list">
        {#each filtered as r, i (r.session_id)}
          <button class="sm-row" class:active={i === activeIdx} onclick={() => pick(r)}>
            <span class="sm-title">{label(r)}</span>
            <span class="sm-time">{new Date(r.updated_at || r.created_at).toLocaleString()}</span>
          </button>
        {:else}
          <div class="sm-empty">No matches</div>
        {/each}
      </div>
    </div>
  </div>
{/if}

<style>
 .sm-backdrop {
 position: fixed; inset: 0;
 background: rgba(0, 0, 0, 0.4);
 display: flex; align-items: flex-start; justify-content: center;
 padding-top: 15vh; z-index: 10000;
 }
 .sm-card {
 width: 600px; max-width: 92vw; max-height: 70vh;
 background: #fff; border-radius: var(--pw-radius-sm);
 box-shadow: 0 12px 48px rgba(0, 0, 0, 0.18);
 display: flex; flex-direction: column; overflow: hidden;
 }
 .sm-head {
 display: flex; align-items: center; gap: 10px;
 padding: 14px 16px;
 border-bottom: 1px solid var(--pw-border, #e8e6dd);
 }
 .sm-ic { color: var(--pw-muted, #807a72); font-size: 14px; }
 .sm-head input {
 flex: 1; border: 0; outline: 0;
 background: transparent; font: inherit; font-size: 14px;
 color: var(--pw-ink, #2c2c2c);
 }
 .sm-head kbd {
 font-family: inherit; font-size: 10px;
 color: var(--pw-muted, #807a72);
 background: var(--pw-bg-alt, #f7f6f3);
 padding: 2px 6px; border-radius: var(--pw-radius-sm);
 }
 .sm-list { overflow-y: auto; padding: 6px; }
 .sm-row {
 display: flex; justify-content: space-between; align-items: center;
 width: 100%; padding: 10px 12px;
 border: 0; background: transparent;
 border-radius: var(--pw-radius-sm); text-align: left;
 font: inherit; cursor: pointer;
 color: var(--pw-ink, #2c2c2c);
 }
 .sm-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
 .sm-row:hover, .sm-row.active { background: var(--pw-bg-alt, #f7f6f3); }
 .sm-time { font-size: 11px; color: var(--pw-muted, #807a72); flex-shrink: 0; margin-left: 12px; }
 .sm-empty { padding: 24px; text-align: center; color: var(--pw-muted, #807a72); font-size: 12px; }
</style>
