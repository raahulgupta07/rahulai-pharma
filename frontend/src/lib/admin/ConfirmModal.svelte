<script lang="ts">
  interface Props {
    open: boolean;
    title: string;
    message: string;
    danger?: boolean;
    confirmLabel?: string;
    cancelLabel?: string;
    typeToConfirm?: string | null;
    hideCancel?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
  }

  let {
    open,
    title,
    message,
    danger = false,
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    typeToConfirm = null,
    hideCancel = false,
    onConfirm,
    onCancel,
  }: Props = $props();

  let typed = $state('');

  // Reset typed input whenever the modal closes
  $effect(() => {
    if (!open) typed = '';
  });

  const canConfirm = $derived(
    typeToConfirm == null || typed.trim() === typeToConfirm
  );

  function handleKeydown(e: KeyboardEvent) {
    if (!open) return;
    if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
    if (e.key === 'Enter' && canConfirm) { e.preventDefault(); onConfirm(); }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="cm-backdrop" onclick={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
    <div class="cm-card" role="dialog" aria-modal="true" aria-labelledby="cm-title" onclick={(e) => e.stopPropagation()}>

      <!-- header -->
      <div class="cm-header">
        <div class="cm-title-row">
          {#if danger}<span class="cm-warn-icon" aria-hidden="true">⚠</span>{/if}
          <h2 class="cm-title" id="cm-title">{title}</h2>
        </div>
        <button class="cm-close" onclick={onCancel} aria-label="Close">&#10005;</button>
      </div>

      <!-- message -->
      <div class="cm-body">
        <p class="cm-message">{message}</p>

        <!-- type-to-confirm input -->
        {#if typeToConfirm != null}
          <label class="cm-ttc-label" for="cm-ttc-input">
            Type <code class="cm-ttc-code">{typeToConfirm}</code> to confirm:
          </label>
          <!-- svelte-ignore a11y_autofocus -->
          <input
            id="cm-ttc-input"
            class="cm-ttc-input"
            class:cm-ttc-ok={typed.trim() === typeToConfirm}
            type="text"
            bind:value={typed}
            placeholder={typeToConfirm}
            autocomplete="off"
            autofocus
          />
        {/if}
      </div>

      <!-- footer -->
      <div class="cm-footer">
        {#if !hideCancel}<button class="cm-btn cm-btn-cancel" onclick={onCancel}>{cancelLabel}</button>{/if}
        <button
          class="cm-btn"
          class:cm-btn-accent={!danger}
          class:cm-btn-danger={danger}
          onclick={onConfirm}
          disabled={!canConfirm}
        >{confirmLabel}</button>
      </div>

    </div>
  </div>
{/if}

<style>
  .cm-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(2px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 99999;
    animation: cm-fade 0.12s ease-out;
  }
  @keyframes cm-fade {
    from { opacity: 0; }
    to   { opacity: 1; }
  }

  .cm-card {
    background: var(--pw-surface, #fff);
    border: 1px solid var(--pw-border, #ece6d9);
    border-radius: 12px;
    box-shadow: 0 12px 40px rgba(0,0,0,0.18), 0 2px 8px rgba(0,0,0,0.08);
    width: 440px;
    max-width: calc(100vw - 32px);
    overflow: hidden;
    animation: cm-pop 0.14s ease-out;
  }
  @keyframes cm-pop {
    from { opacity: 0; transform: scale(0.96) translateY(6px); }
    to   { opacity: 1; transform: scale(1) translateY(0); }
  }

  /* header */
  .cm-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    padding: 20px 22px 14px;
    border-bottom: 1px solid var(--pw-border, #ece6d9);
  }
  .cm-title-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    min-width: 0;
  }
  .cm-warn-icon {
    font-size: 17px;
    color: #c0392b;
    flex-shrink: 0;
    line-height: 1;
  }
  .cm-title {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    font-family: var(--pw-font-headline, inherit);
    color: var(--pw-ink, #3a352c);
    line-height: 1.3;
  }
  .cm-close {
    background: none;
    border: none;
    color: var(--pw-muted, #8a8275);
    cursor: pointer;
    font-size: 15px;
    padding: 0;
    line-height: 1;
    flex-shrink: 0;
    margin-top: 1px;
  }
  .cm-close:hover { color: var(--pw-ink, #3a352c); }

  /* body */
  .cm-body {
    padding: 16px 22px 20px;
  }
  .cm-message {
    margin: 0;
    font-size: 13.5px;
    line-height: 1.5;
    color: var(--pw-muted, #8a8275);
    white-space: pre-line;
    word-break: break-word;
  }

  /* type-to-confirm */
  .cm-ttc-label {
    display: block;
    font-size: 12px;
    color: var(--pw-muted, #8a8275);
    margin-top: 16px;
    margin-bottom: 6px;
    font-weight: 500;
  }
  .cm-ttc-code {
    background: var(--pw-bg-alt, #f5f1eb);
    padding: 1px 6px;
    border-radius: 4px;
    font-family: ui-monospace, Menlo, monospace;
    font-size: 12px;
    color: var(--pw-accent, #c96342);
    font-weight: 700;
  }
  .cm-ttc-input {
    width: 100%;
    box-sizing: border-box;
    padding: 9px 12px;
    background: var(--pw-surface, #fff);
    border: 1.5px solid var(--pw-border, #ece6d9);
    border-radius: 6px;
    font-size: 13px;
    font-family: ui-monospace, Menlo, monospace;
    color: var(--pw-ink, #3a352c);
    outline: none;
    transition: border-color 0.15s;
  }
  .cm-ttc-input:focus { border-color: var(--pw-accent, #c96342); }
  .cm-ttc-ok { border-color: #2e7d32; background: rgba(46,125,50,0.04); }

  /* footer */
  .cm-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    padding: 14px 22px;
    border-top: 1px solid var(--pw-border, #ece6d9);
    background: var(--pw-bg-alt, #f8f4ee);
  }
  .cm-btn {
    padding: 8px 18px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    border: 1.5px solid transparent;
    transition: all 0.15s;
  }
  .cm-btn-cancel {
    background: var(--pw-surface, #fff);
    border-color: var(--pw-border, #ece6d9);
    color: var(--pw-ink, #3a352c);
  }
  .cm-btn-cancel:hover { background: var(--pw-bg-alt, #f0ebe2); }
  .cm-btn-accent {
    background: var(--pw-accent, #c96342);
    color: #fff;
    border-color: var(--pw-accent, #c96342);
  }
  .cm-btn-accent:hover:not(:disabled) { filter: brightness(0.93); }
  .cm-btn-danger {
    background: #c0392b;
    color: #fff;
    border-color: #c0392b;
  }
  .cm-btn-danger:hover:not(:disabled) { background: #a93226; border-color: #a93226; }
  .cm-btn:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
</style>
