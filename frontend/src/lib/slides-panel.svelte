<script lang="ts">
  // Slides artifact panel — mirrors dashboard-panel.svelte pattern.
  // Triggered by [PRESENTATION:<id>] tag in assistant message.
  // 45% width slide-in from right, list slides w/ patch + download actions.
  import { onMount } from 'svelte';
  import Icon from '$lib/Icon.svelte';

  let { presId, projectSlug = '', onClose }: {
    presId: number;
    projectSlug?: string;
    onClose?: () => void;
  } = $props();

  let inventory: any[] = $state([]);
  let title: string = $state('Presentation');
  let theme: string = $state('');
  let loading: boolean = $state(true);
  let error: string = $state('');
  let editingIdx: number | null = $state(null);
  let editBuf: { title: string; bullets: string; speaker_notes: string } = $state({
    title: '', bullets: '', speaker_notes: '',
  });
  let saving: boolean = $state(false);
  let regeneratingIdx: number | null = $state(null);
  let promptIdx: number | null = $state(null);
  let promptText: string = $state('');

  async function loadInventory() {
    loading = true;
    error = '';
    try {
      const token = localStorage.getItem('dash_token') || '';
      const res = await fetch(`/api/slides/${presId}/inventory`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'load_failed');
      inventory = data.inventory || [];

      // Pull title + theme from the saved row
      const meta = await fetch(`/api/export/presentations/${presId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (meta.ok) {
        const m = await meta.json();
        title = m?.presentation?.title || 'Presentation';
        theme = m?.presentation?.thinking?.theme || '';
      }
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      loading = false;
    }
  }

  function openEdit(idx: number) {
    const s = inventory[idx];
    editingIdx = idx;
    editBuf = {
      title: s.title || '',
      bullets: (s.bullets || []).join('\n'),
      speaker_notes: s.speaker_notes || '',
    };
  }

  async function savePatch() {
    if (editingIdx === null) return;
    saving = true;
    try {
      const token = localStorage.getItem('dash_token') || '';
      const patches = [
        { key: 'title', value: editBuf.title },
        { key: 'bullets', value: editBuf.bullets.split('\n').map(s => s.trim()).filter(Boolean) },
        { key: 'speaker_notes', value: editBuf.speaker_notes },
      ];
      const res = await fetch(`/api/slides/${presId}/patch`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ slide_idx: editingIdx, patches }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      editingIdx = null;
      await loadInventory();
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      saving = false;
    }
  }

  async function regenerateSlide(idx: number, prompt: string = '') {
    if (regeneratingIdx !== null) return;
    regeneratingIdx = idx;
    error = '';
    try {
      const token = localStorage.getItem('dash_token') || '';
      const res = await fetch(`/api/slides/${presId}/slides/${idx}/regenerate`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(prompt ? { prompt } : {}),
      });
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json())?.detail || ''; } catch {}
        throw new Error(detail || `HTTP ${res.status}`);
      }
      await loadInventory();
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      regeneratingIdx = null;
      promptIdx = null;
      promptText = '';
    }
  }

  function openPrompt(idx: number) {
    promptIdx = idx;
    promptText = '';
  }

  async function submitPrompt() {
    if (promptIdx === null) return;
    const t = promptText.trim();
    if (!t) return;
    await regenerateSlide(promptIdx, t);
  }

  async function downloadPptx() {
    const token = localStorage.getItem('dash_token') || '';
    const res = await fetch(`/api/export/presentations/${presId}/pptx`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      error = `download HTTP ${res.status}`;
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title.replace(/[^\w\s-]/g, '').slice(0, 60) || 'presentation'}.pptx`;
    a.click();
    URL.revokeObjectURL(url);
  }

  onMount(() => { loadInventory(); });
</script>

<div class="slides-panel">
  <header class="sp-header">
    <div class="sp-title">
      <span class="sp-icon">📊</span>
      <div>
        <h3>{title}</h3>
        <div class="sp-meta">
          {inventory.length} slides{theme ? ` · ${theme}` : ''}
        </div>
      </div>
    </div>
    <div class="sp-actions">
      <button class="sp-btn-primary" onclick={downloadPptx}>↓ DOWNLOAD .PPTX</button>
      {#if onClose}
        <button class="sp-btn-ghost" onclick={() => onClose && onClose()}>✕</button>
      {/if}
    </div>
  </header>

  {#if loading}
    <div class="sp-loading">Loading slides…</div>
  {:else if error}
    <div class="sp-error">⚠ {error}</div>
  {:else}
    <div class="sp-body">
      {#each inventory as s, i}
        <div class="sp-slide" class:editing={editingIdx === i}>
          <div class="sp-slide-head">
            <span class="sp-slide-num">SLIDE {i + 1}</span>
            <span class="sp-slide-layout">{s.layout || ''}</span>
            <div class="sp-slide-actions">
              <button class="sp-btn-link" disabled={regeneratingIdx !== null} onclick={() => regenerateSlide(i)} title="Regenerate this slide">
                {regeneratingIdx === i ? '⏳ REGEN…' : '🔄 REGEN'}
              </button>
              <button class="sp-btn-link" disabled={regeneratingIdx !== null} onclick={() => openPrompt(i)} title="Rewrite with prompt">
                💬 PROMPT
              </button>
              <button class="sp-btn-link" disabled={regeneratingIdx !== null} onclick={() => openEdit(i)}>✎ EDIT</button>
            </div>
          </div>
          {#if promptIdx === i}
            <div class="sp-prompt-box">
              <input
                bind:value={promptText}
                placeholder="e.g. make this punchier, focus on revenue impact"
                onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') submitPrompt(); }}
              />
              <button class="sp-btn-primary" disabled={regeneratingIdx !== null || !promptText.trim()} onclick={submitPrompt}>
                {regeneratingIdx === i ? 'REWRITING…' : '↵ REWRITE'}
              </button>
              <button class="sp-btn-ghost" onclick={() => { promptIdx = null; promptText = ''; }}>CANCEL</button>
            </div>
          {/if}
          {#if editingIdx === i}
            <div class="sp-edit">
              <label>
                <span>Title</span>
                <input bind:value={editBuf.title} />
              </label>
              <label>
                <span>Bullets (one per line)</span>
                <textarea rows="4" bind:value={editBuf.bullets}></textarea>
              </label>
              <label>
                <span>Speaker notes</span>
                <textarea rows="3" bind:value={editBuf.speaker_notes}></textarea>
              </label>
              <div class="sp-edit-actions">
                <button class="sp-btn-primary" disabled={saving} onclick={savePatch}>
                  {saving ? 'SAVING…' : '✓ SAVE'}
                </button>
                <button class="sp-btn-ghost" onclick={() => (editingIdx = null)}>CANCEL</button>
              </div>
            </div>
          {:else}
            {#if s.hero_image_url}
              <div class="sp-hero">
                <img src={s.hero_image_url} alt="" loading="lazy" />
                {#if s.hero_credit}<div class="sp-hero-credit">Photo: {s.hero_credit}</div>{/if}
              </div>
            {/if}
            <h4 class="sp-slide-title">{s.title}</h4>
            {#if s.bullets && s.bullets.length}
              <ul class="sp-bullets">
                {#each s.bullets as b, bi}
                  <li>
                    {#if s.bullet_icons && s.bullet_icons[bi]}
                      <span class="sp-bullet-icon">
                        <Icon name={s.bullet_icons[bi]} size={14} />
                      </span>
                    {/if}
                    <span>{b}</span>
                  </li>
                {/each}
              </ul>
            {/if}
            {#if s.speaker_notes}
              <div class="sp-notes"><strong>Notes:</strong> {s.speaker_notes}</div>
            {/if}
            {#if s.visual}
              <div class="sp-visual">Visual: <code>{s.visual}</code></div>
            {/if}
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .slides-panel {
    position: fixed; top: 56px; right: 0; bottom: 0;
    width: 45%; min-width: 480px; max-width: 720px;
    background: var(--pw-surface, #faf9f5);
    border-left: 1px solid var(--pw-border, #e5e1d4);
    display: flex; flex-direction: column;
    z-index: 9000;
    box-shadow: -4px 0 16px rgba(0,0,0,0.06);
  }
  @media (max-width: 900px) {
    .slides-panel { width: 100%; min-width: 0; }
  }
  .sp-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid var(--pw-border, #e5e1d4);
    background: var(--pw-bg-alt, #f3efe4);
  }
  .sp-title { display: flex; gap: 12px; align-items: center; }
  .sp-icon { font-size: 24px; }
  .sp-title h3 { margin: 0; font-size: 15px; font-weight: 600; }
  .sp-meta { font-size: 11px; color: var(--pw-ink-soft, #6b6557); margin-top: 2px; }
  .sp-actions { display: flex; gap: 8px; }
  .sp-btn-primary {
    background: var(--pw-accent, #c96342); color: #fff;
    border: none; padding: 8px 14px; font-size: 12px; font-weight: 600;
    cursor: pointer; border-radius: var(--pw-radius-sm);
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .sp-btn-primary:hover { filter: brightness(1.05); }
  .sp-btn-primary:disabled { opacity: 0.5; cursor: wait; }
  .sp-btn-ghost {
    background: transparent; border: 1px solid var(--pw-border, #e5e1d4);
    padding: 8px 12px; font-size: 12px; cursor: pointer; border-radius: var(--pw-radius-sm);
    color: var(--pw-ink, #2c2a26);
  }
  .sp-btn-link {
    background: transparent; border: none; color: var(--pw-accent, #c96342);
    cursor: pointer; font-size: 11px; padding: 0;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .sp-btn-link:disabled { opacity: 0.4; cursor: wait; }
  .sp-slide-actions { display: flex; gap: 10px; margin-left: auto; }
  .sp-prompt-box {
    display: flex; gap: 6px; margin: 6px 0 10px; align-items: stretch;
    background: var(--pw-bg-alt, #f3efe4);
    border: 1px solid var(--pw-border, #e5e1d4); border-radius: var(--pw-radius-sm);
    padding: 6px;
  }
  .sp-prompt-box input {
    flex: 1; border: 1px solid var(--pw-border, #e5e1d4); border-radius: var(--pw-radius-sm);
    padding: 6px 8px; font-size: 12px; font-family: inherit;
    background: #fff;
  }
  .sp-body {
    overflow-y: auto; padding: 16px;
    flex: 1; display: flex; flex-direction: column; gap: 12px;
  }
  .sp-slide {
    background: #fff;
    border: 1px solid var(--pw-border, #e5e1d4);
    border-radius: var(--pw-radius-sm);
    padding: 12px 14px;
  }
  .sp-slide.editing {
    border-color: var(--pw-accent, #c96342);
    box-shadow: 0 0 0 3px rgba(201,99,66,0.08);
  }
  .sp-slide-head {
    display: flex; align-items: center; gap: 10px; margin-bottom: 6px;
  }
  .sp-slide-num {
    font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
    color: var(--pw-ink-soft, #6b6557);
  }
  .sp-slide-layout {
    font-size: 10px; padding: 2px 6px;
    background: var(--pw-bg-alt, #f3efe4); border-radius: var(--pw-radius-sm);
    color: var(--pw-ink-soft, #6b6557);
  }
  .sp-slide-title {
    margin: 4px 0 8px 0; font-size: 15px; font-weight: 600;
    color: var(--pw-ink, #2c2a26);
  }
  .sp-hero { margin-bottom: 8px; border-radius: var(--pw-radius-sm); overflow: hidden;
    border: 1px solid var(--pw-border, #e5e1d4); position: relative; }
  .sp-hero img { width: 100%; height: 110px; object-fit: cover; display: block; }
  .sp-hero-credit { position: absolute; bottom: 2px; right: 4px; font-size: 9px;
    color: #fff; background: rgba(0,0,0,0.5); padding: 1px 4px; border-radius: var(--pw-radius-sm); }
  .sp-bullets { margin: 0 0 6px 0; padding-left: 0; list-style: none; font-size: 13px; }
  .sp-bullets li { margin: 4px 0; display: flex; gap: 6px; align-items: flex-start; }
  .sp-bullet-icon { color: var(--pw-accent, #c96342); flex-shrink: 0; margin-top: 2px; }
  .sp-notes {
    margin-top: 8px; padding: 6px 10px;
    background: var(--pw-bg-alt, #f3efe4); border-radius: var(--pw-radius-sm);
    font-size: 12px; color: var(--pw-ink-soft, #6b6557);
  }
  .sp-visual {
    margin-top: 6px; font-size: 11px; color: var(--pw-ink-soft, #6b6557);
  }
  .sp-visual code {
    background: var(--pw-bg-alt, #f3efe4); padding: 1px 5px;
    border-radius: var(--pw-radius-sm); font-size: 11px;
  }
  .sp-edit { display: flex; flex-direction: column; gap: 8px; }
  .sp-edit label { display: flex; flex-direction: column; font-size: 11px; gap: 3px; }
  .sp-edit label > span {
    text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--pw-ink-soft, #6b6557); font-weight: 600;
  }
  .sp-edit input, .sp-edit textarea {
    border: 1px solid var(--pw-border, #e5e1d4); border-radius: var(--pw-radius-sm);
    padding: 6px 8px; font-size: 13px;
    font-family: inherit;
  }
  .sp-edit-actions { display: flex; gap: 8px; }
  .sp-loading, .sp-error {
    padding: 32px; text-align: center;
    color: var(--pw-ink-soft, #6b6557); font-size: 13px;
  }
  .sp-error { color: #b94a3d; }
</style>
