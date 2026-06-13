<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { pendingDelete, resolvePending } from './confirmDelete';

 let typed = $state('');
 let step = $state<1 | 2>(1);

 $effect(() => {
 if ($pendingDelete) {
 typed = '';
 step = 1;
 }
 });

 function cancel() {
 resolvePending(false);
 }

 function confirm() {
 const p = $pendingDelete;
 if (!p) return;
 if (p.requireType && typed.trim() !== p.itemName) return;
 if (step === 1) {
 step = 2;
 return;
 }
 resolvePending(true);
 }

 function onKeydown(e: KeyboardEvent) {
 if (e.key === 'Escape') cancel();
 if (e.key === 'Enter' && canProceed()) confirm();
 }

 function canProceed(): boolean {
 const p = $pendingDelete;
 if (!p) return false;
 if (p.requireType && typed.trim() !== p.itemName) return false;
 return true;
 }
</script>

<svelte:window onkeydown={onKeydown} />

{#if $pendingDelete}
  <div class="dcm-backdrop" onclick={cancel} role="presentation">
    <div class="dcm-modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="dcm-title">
      <header class="dcm-header">
        <span class="dcm-icon"><Icon name="alert-triangle" size={14} /></span>
        <h2 id="dcm-title">{$pendingDelete.title}</h2>
      </header>
      <div class="dcm-body">
        <p class="dcm-msg">
          {#if $pendingDelete.message}
            {$pendingDelete.message}
          {:else}
            Delete {$pendingDelete.itemType} <strong>"{$pendingDelete.itemName}"</strong>?
          {/if}
        </p>
        <p class="dcm-warn">This action cannot be undone.</p>

        {#if $pendingDelete.requireType}
          <label class="dcm-label" for="dcm-input">
            Type <code>{$pendingDelete.itemName}</code> to confirm:
          </label>
          <input
            id="dcm-input"
            type="text"
            class="dcm-input"
            class:dcm-input-ok={typed.trim() === $pendingDelete.itemName}
            bind:value={typed}
            placeholder={$pendingDelete.itemName}
            autocomplete="off"
            autofocus
          />
        {/if}

        {#if step === 2}
          <p class="dcm-step2"><Icon name="alert-triangle" size={14} /> Last chance. Click <strong>Delete permanently</strong> to proceed.</p>
        {/if}
      </div>
      <footer class="dcm-footer">
        <button class="dcm-btn dcm-btn-cancel" onclick={cancel}>Cancel</button>
        <button
          class="dcm-btn dcm-btn-danger"
          onclick={confirm}
          disabled={!canProceed()}
        >
          {step === 1 ? 'Delete' : 'Delete permanently'}
        </button>
      </footer>
    </div>
  </div>
{/if}

<style>
 .dcm-backdrop {
 position: fixed; inset: 0;
 background: rgba(0, 0, 0, 0.55);
 display: flex; align-items: center; justify-content: center;
 z-index: 99999;
 backdrop-filter: blur(2px);
 animation: dcm-fade 0.12s ease-out;
 }
 @keyframes dcm-fade {
 from { opacity: 0; }
 to { opacity: 1; }
 }
 .dcm-modal {
 width: 440px; max-width: calc(100vw - 32px);
 background: var(--pw-surface, #fff);
 border: 1px solid #d33;
 border-radius: var(--pw-radius-sm);
 box-shadow: 0 12px 40px rgba(0,0,0,0.25), 0 0 0 4px rgba(211,51,51,0.08);
 overflow: hidden;
 animation: dcm-pop 0.14s ease-out;
 }
 @keyframes dcm-pop {
 from { opacity: 0; transform: scale(0.96) translateY(8px); }
 to { opacity: 1; transform: scale(1) translateY(0); }
 }
 .dcm-header {
 display: flex; align-items: center; gap: 12px;
 padding: 16px 20px;
 background: linear-gradient(135deg, rgba(211,51,51,0.12), rgba(211,51,51,0.04));
 border-bottom: 1px solid rgba(211,51,51,0.20);
 }
 .dcm-icon {
 font-size: 22px;
 color: #d33;
 }
 .dcm-header h2 {
 margin: 0;
 font-size: 16px; font-weight: 800;
 color: #b91c1c;
 letter-spacing: 0.01em;
 }
 .dcm-body {
 padding: 20px;
 font-family: var(--pw-font-body, system-ui);
 font-size: 14px;
 color: var(--pw-ink, #2c2a26);
 }
 .dcm-msg {
 margin: 0 0 10px;
 line-height: 1.5;
 }
 .dcm-msg strong {
 color: #b91c1c;
 font-weight: 700;
 }
 .dcm-warn {
 margin: 0 0 16px;
 font-size: 12px;
 color: var(--pw-muted, #888);
 }
 .dcm-label {
 display: block;
 font-size: 12px;
 color: var(--pw-muted, #888);
 margin-bottom: 6px;
 font-weight: 500;
 }
 .dcm-label code {
 background: var(--pw-bg-alt, #f5f1eb);
 padding: 1px 6px;
 border-radius: var(--pw-radius-sm);
 font-family: var(--pw-font-mono, ui-monospace, monospace);
 font-size: 12px;
 color: #b91c1c;
 font-weight: 700;
 }
 .dcm-input {
 width: 100%;
 padding: 10px 14px;
 background: var(--pw-bg-alt, #f5f1eb);
 border: 1.5px solid var(--pw-border, #ddd);
 border-radius: var(--pw-radius-sm);
 font-family: var(--pw-font-mono, ui-monospace, monospace);
 font-size: 14px;
 color: var(--pw-ink);
 transition: border-color 0.15s, background 0.15s;
 box-sizing: border-box;
 }
 .dcm-input:focus {
 outline: none;
 border-color: #d33;
 background: var(--pw-surface);
 }
 .dcm-input-ok {
 border-color: #2e7d32;
 background: rgba(46,125,50,0.05);
 }
 .dcm-step2 {
 margin: 12px 0 0;
 padding: 10px 14px;
 background: rgba(211,51,51,0.08);
 border-left: 3px solid #d33;
 border-radius: var(--pw-radius-sm);
 font-size: 13px;
 color: #b91c1c;
 }
 .dcm-footer {
 display: flex; gap: 10px;
 justify-content: flex-end;
 padding: 14px 20px;
 border-top: 1px solid var(--pw-border, #ddd);
 background: var(--pw-bg-alt, #fafafa);
 }
 .dcm-btn {
 padding: 8px 18px;
 border-radius: var(--pw-radius-sm);
 font-size: 13px;
 font-weight: 700;
 font-family: inherit;
 cursor: pointer;
 transition: all 0.15s;
 border: 1.5px solid transparent;
 }
 .dcm-btn-cancel {
 background: var(--pw-surface);
 border-color: var(--pw-border, #ddd);
 color: var(--pw-ink);
 }
 .dcm-btn-cancel:hover {
 background: var(--pw-bg-alt);
 }
 .dcm-btn-danger {
 background: #d33;
 color: white;
 border-color: #d33;
 box-shadow: 0 2px 6px rgba(211,51,51,0.25);
 }
 .dcm-btn-danger:hover:not(:disabled) {
 background: #b91c1c;
 border-color: #b91c1c;
 box-shadow: 0 4px 12px rgba(211,51,51,0.35);
 }
 .dcm-btn-danger:disabled {
 background: #e5b4b4;
 border-color: #e5b4b4;
 cursor: not-allowed;
 box-shadow: none;
 }
</style>
