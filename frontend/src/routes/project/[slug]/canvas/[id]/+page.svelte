<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';

  type Card = {
    id: string;
    type: 'note' | 'sql' | 'chart' | 'kpi';
    x: number;
    y: number;
    w: number;
    h: number;
    content: any;
  };

  let slug = $derived($page.params.slug);
  let canvasId = $derived($page.params.id);

  let name = $state('');
  let cards = $state<Card[]>([]);
  let loading = $state(true);
  let err = $state('');
  let savedFlash = $state(false);
  let dirty = $state(false);
  let saving = $state(false);

  // Drag state
  let dragging: { id: string; mode: 'move' | 'resize'; startX: number; startY: number; origX: number; origY: number; origW: number; origH: number } | null = $state(null);

  // Auto-save debounce
  let saveTimer: ReturnType<typeof setTimeout> | null = null;
  const SAVE_DEBOUNCE_MS = 3000;

  function genId(): string {
    return 'c_' + Math.random().toString(36).slice(2, 10);
  }

  async function load() {
    loading = true;
    err = '';
    try {
      const token = localStorage.getItem('dash_token') || '';
      const res = await fetch(`/api/canvas/${slug}/${canvasId}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      name = data?.name || 'Untitled canvas';
      const list = data?.board?.cards;
      cards = Array.isArray(list) ? list : [];
    } catch (e: any) {
      err = String(e?.message || e);
    } finally {
      loading = false;
    }
  }

  async function saveNow() {
    if (saving) return;
    saving = true;
    try {
      const token = localStorage.getItem('dash_token') || '';
      const res = await fetch(`/api/canvas/${slug}/${canvasId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ name, board: { cards } })
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      dirty = false;
      savedFlash = true;
      setTimeout(() => (savedFlash = false), 1200);
    } catch (e: any) {
      err = String(e?.message || e);
    } finally {
      saving = false;
    }
  }

  function scheduleSave() {
    dirty = true;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      saveNow();
    }, SAVE_DEBOUNCE_MS);
  }

  function addCard(type: Card['type']) {
    const base_x = 40 + (cards.length % 6) * 30;
    const base_y = 40 + (cards.length % 6) * 30;
    const defaults: Record<Card['type'], { w: number; h: number; content: any }> = {
      note: { w: 280, h: 180, content: { text: '' } },
      sql: { w: 360, h: 200, content: { sql: '-- SELECT ...' } },
      chart: { w: 360, h: 240, content: { title: 'Chart', placeholder: true } },
      kpi: { w: 200, h: 120, content: { value: '0', label: 'Metric' } }
    };
    const d = defaults[type];
    const c: Card = {
      id: genId(),
      type,
      x: base_x,
      y: base_y,
      w: d.w,
      h: d.h,
      content: d.content
    };
    cards = [...cards, c];
    scheduleSave();
  }

  function removeCard(id: string) {
    cards = cards.filter((c) => c.id !== id);
    scheduleSave();
  }

  function updateContent(id: string, patch: any) {
    cards = cards.map((c) =>
      c.id === id ? { ...c, content: { ...c.content, ...patch } } : c
    );
    scheduleSave();
  }

  function onPointerDown(e: PointerEvent, id: string, mode: 'move' | 'resize') {
    e.preventDefault();
    const card = cards.find((c) => c.id === id);
    if (!card) return;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    dragging = {
      id,
      mode,
      startX: e.clientX,
      startY: e.clientY,
      origX: card.x,
      origY: card.y,
      origW: card.w,
      origH: card.h
    };
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragging) return;
    const dx = e.clientX - dragging.startX;
    const dy = e.clientY - dragging.startY;
    const id = dragging.id;
    cards = cards.map((c) => {
      if (c.id !== id) return c;
      if (dragging!.mode === 'move') {
        return { ...c, x: Math.max(0, dragging!.origX + dx), y: Math.max(0, dragging!.origY + dy) };
      } else {
        return {
          ...c,
          w: Math.max(140, dragging!.origW + dx),
          h: Math.max(80, dragging!.origH + dy)
        };
      }
    });
  }

  function onPointerUp() {
    if (dragging) {
      dragging = null;
      scheduleSave();
    }
  }

  function onNameInput(e: Event) {
    name = (e.target as HTMLInputElement).value;
    scheduleSave();
  }

  onMount(load);

  onDestroy(() => {
    if (saveTimer) clearTimeout(saveTimer);
  });
</script>

<svelte:head>
  <title>{name || 'Canvas'} · {slug}</title>
</svelte:head>

<svelte:window onpointermove={onPointerMove} onpointerup={onPointerUp} />

<div class="root">
  <header class="bar">
    <div class="left">
      <button class="back" onclick={() => goto(`${base}/project/${slug}/canvas`)}>← Back</button>
      <input class="name-in" type="text" value={name} oninput={onNameInput} placeholder="Canvas name" />
    </div>
    <div class="right">
      {#if savedFlash}
        <span class="saved">✓ saved</span>
      {:else if dirty}
        <span class="dirty">unsaved…</span>
      {/if}
      <div class="toolbar">
        <button onclick={() => addCard('kpi')}>+ KPI</button>
        <button onclick={() => addCard('chart')}>+ Chart</button>
        <button onclick={() => addCard('sql')}>+ SQL</button>
        <button onclick={() => addCard('note')}>+ Note</button>
      </div>
    </div>
  </header>

  {#if err}
    <div class="err">⚠ {err}</div>
  {/if}

  <div class="board" class:dragging-mode={!!dragging}>
    {#if loading}
      <div class="loading">Loading…</div>
    {:else if cards.length === 0}
      <div class="empty">
        <p>Empty canvas.</p>
        <p class="hint">Add a card from the toolbar above.</p>
      </div>
    {:else}
      {#each cards as c (c.id)}
        <div
          class="card type-{c.type}"
          style="left: {c.x}px; top: {c.y}px; width: {c.w}px; height: {c.h}px;"
        >
          <div class="card-head" onpointerdown={(e) => onPointerDown(e, c.id, 'move')}>
            <span class="type-badge">{c.type}</span>
            <button class="x" onclick={() => removeCard(c.id)}>✕</button>
          </div>
          <div class="card-body">
            {#if c.type === 'note'}
              <textarea
                placeholder="Write a note…"
                value={c.content?.text || ''}
                oninput={(e) => updateContent(c.id, { text: (e.target as HTMLTextAreaElement).value })}
              ></textarea>
            {:else if c.type === 'sql'}
              <textarea
                class="sql"
                spellcheck="false"
                value={c.content?.sql || ''}
                oninput={(e) => updateContent(c.id, { sql: (e.target as HTMLTextAreaElement).value })}
              ></textarea>
            {:else if c.type === 'kpi'}
              <input
                class="kpi-value"
                type="text"
                value={c.content?.value || ''}
                oninput={(e) => updateContent(c.id, { value: (e.target as HTMLInputElement).value })}
                placeholder="0"
              />
              <input
                class="kpi-label"
                type="text"
                value={c.content?.label || ''}
                oninput={(e) => updateContent(c.id, { label: (e.target as HTMLInputElement).value })}
                placeholder="Metric"
              />
            {:else if c.type === 'chart'}
              <input
                class="chart-title"
                type="text"
                value={c.content?.title || ''}
                oninput={(e) => updateContent(c.id, { title: (e.target as HTMLInputElement).value })}
                placeholder="Chart title"
              />
              <div class="chart-placeholder">
                <span>chart placeholder</span>
              </div>
            {/if}
          </div>
          <div
            class="resize"
            onpointerdown={(e) => onPointerDown(e, c.id, 'resize')}
            title="Resize"
          ></div>
        </div>
      {/each}
    {/if}
  </div>
</div>

<style>
  .root {
    min-height: 100vh;
    background: #0f1115;
    color: #e8e3d6;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }
  .bar {
    position: sticky;
    top: 0;
    z-index: 20;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 20px;
    background: #1a1d23;
    border-bottom: 1px solid #2a2f38;
  }
  .left, .right {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .back {
    background: transparent;
    color: #8a8478;
    border: 1px solid #2a2f38;
    padding: 6px 12px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
  }
  .back:hover { background: #2a2f38; color: #e8e3d6; }
  .name-in {
    background: transparent;
    border: 1px solid transparent;
    color: #e8e3d6;
    font-size: 16px;
    font-weight: 600;
    padding: 4px 8px;
    border-radius: 4px;
    min-width: 280px;
    font-family: 'Source Serif Pro', Georgia, serif;
  }
  .name-in:focus { border-color: #c96342; outline: none; }
  .toolbar {
    display: flex;
    gap: 6px;
    background: #0f1115;
    border: 1px solid #2a2f38;
    border-radius: 6px;
    padding: 4px;
  }
  .toolbar button {
    background: transparent;
    color: #e8e3d6;
    border: none;
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
  }
  .toolbar button:hover { background: #c96342; }
  .saved { color: #58c878; font-size: 12px; }
  .dirty { color: #8a8478; font-size: 11px; font-style: italic; }
  .err {
    margin: 12px 20px;
    background: rgba(192, 57, 50, 0.15);
    border: 1px solid #c03932;
    color: #ff9d9d;
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 12px;
  }
  .board {
    position: relative;
    min-height: calc(100vh - 56px);
    background-image:
      radial-gradient(circle, #1a1d23 1px, transparent 1px);
    background-size: 24px 24px;
    background-position: 0 0;
    user-select: none;
  }
  .board.dragging-mode { cursor: grabbing; }
  .loading, .empty {
    text-align: center;
    padding: 80px 20px;
    color: #8a8478;
  }
  .empty .hint { font-size: 12px; opacity: 0.7; margin-top: 4px; }
  .card {
    position: absolute;
    background: #1a1d23;
    border: 1px solid #2a2f38;
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .card:hover { border-color: #353b46; }
  .card.type-kpi { border-left: 3px solid #c96342; }
  .card.type-chart { border-left: 3px solid #3a8dff; }
  .card.type-sql { border-left: 3px solid #58c878; }
  .card.type-note { border-left: 3px solid #f5b341; }
  .card-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 10px;
    background: #14171c;
    border-bottom: 1px solid #2a2f38;
    cursor: grab;
    flex-shrink: 0;
  }
  .card-head:active { cursor: grabbing; }
  .type-badge {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #8a8478;
    font-weight: 600;
  }
  .x {
    background: transparent;
    color: #8a8478;
    border: none;
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 11px;
  }
  .x:hover { background: #c03932; color: #fff; }
  .card-body {
    flex: 1;
    padding: 10px;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .card-body textarea {
    flex: 1;
    width: 100%;
    background: #0f1115;
    color: #e8e3d6;
    border: 1px solid #2a2f38;
    border-radius: 4px;
    padding: 8px;
    font-size: 12px;
    font-family: inherit;
    resize: none;
  }
  .card-body textarea.sql {
    font-family: 'SF Mono', Menlo, monospace;
    color: #58c878;
    background: #0a0c10;
  }
  .card-body textarea:focus { outline: none; border-color: #c96342; }
  .kpi-value {
    background: transparent;
    color: #c96342;
    border: none;
    font-size: 32px;
    font-weight: 700;
    font-family: 'Source Serif Pro', Georgia, serif;
    padding: 0;
  }
  .kpi-value:focus { outline: none; }
  .kpi-label {
    background: transparent;
    color: #8a8478;
    border: none;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0;
  }
  .kpi-label:focus { outline: none; color: #e8e3d6; }
  .chart-title {
    background: transparent;
    color: #e8e3d6;
    border: 1px solid transparent;
    font-size: 13px;
    font-weight: 600;
    padding: 4px 6px;
    border-radius: 3px;
  }
  .chart-title:focus { outline: none; border-color: #c96342; }
  .chart-placeholder {
    flex: 1;
    border: 1px dashed #2a2f38;
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #4a4d54;
    font-size: 11px;
    font-style: italic;
  }
  .resize {
    position: absolute;
    bottom: 0;
    right: 0;
    width: 14px;
    height: 14px;
    cursor: nwse-resize;
    background: linear-gradient(135deg, transparent 50%, #353b46 50%);
    border-bottom-right-radius: 6px;
  }
  .resize:hover { background: linear-gradient(135deg, transparent 50%, #c96342 50%); }
</style>
