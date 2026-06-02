<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';

  let pres_id = $derived(parseInt(($page.params as any).id, 10));
  let loading = $state(true);
  let error: string | null = $state(null);
  let title = $state('Deck');
  let slides: Array<{ idx: number; page: number; image_url: string }> = $state([]);
  let pptx_url = $state('');
  let activeIdx = $state(0);
  let fullscreen = $state(false);

  const TOKEN = typeof localStorage !== 'undefined' ? (localStorage.getItem('dash_token') || '') : '';

  async function load() {
    loading = true;
    error = null;
    try {
      const r = await fetch(`/api/export/presentations/${pres_id}/preview`, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`);
      }
      const j = await r.json();
      title = j.title || 'Deck';
      slides = j.slides || [];
      pptx_url = j.pptx_url || '';
      if (j.empty) {
        error = 'This deck has no rendered slides yet. Regenerate via the P button in chat.';
      } else if (!slides.length) {
        error = 'Render produced no slides (soffice missing?). Try Download.';
      }
    } catch (e: any) {
      error = e.message || String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function download() {
    if (!pptx_url) return;
    fetch(pptx_url, { method: 'POST', headers: { Authorization: `Bearer ${TOKEN}` } })
      .then((r) => r.blob())
      .then((b) => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(b);
        a.download = `${title}.pptx`;
        a.click();
      });
  }

  function onKey(e: KeyboardEvent) {
    if (!fullscreen) return;
    if (e.key === 'Escape') fullscreen = false;
    if (e.key === 'ArrowRight') activeIdx = Math.min(slides.length - 1, activeIdx + 1);
    if (e.key === 'ArrowLeft') activeIdx = Math.max(0, activeIdx - 1);
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="viewer">
  <header class="bar">
    <a class="back" href="/ui/presentations">← Back</a>
    <h1>{title}</h1>
    <div class="spacer"></div>
    <span class="count">{slides.length} slide{slides.length === 1 ? '' : 's'}</span>
    <button class="btn-primary btn-sm" onclick={download} disabled={!pptx_url}>Download .pptx</button>
  </header>

  {#if loading}
    <div class="status">Rendering preview…</div>
  {:else if error}
    <div class="status err">⚠ {error}</div>
  {:else if !slides.length}
    <div class="status">No preview available.</div>
  {:else}
    <div class="layout">
      <aside class="thumbs">
        {#each slides as s, i (s.idx)}
          <button
            class="thumb"
            class:active={i === activeIdx}
            onclick={() => (activeIdx = i)}
            aria-label="Slide {s.page}"
          >
            <img src={s.image_url} alt="Slide {s.page}" loading="lazy" />
            <span class="thumb-num">{s.page}</span>
          </button>
        {/each}
      </aside>

      <main class="main">
        <div class="frame" onclick={() => (fullscreen = true)} role="button" tabindex="0">
          <img src={slides[activeIdx].image_url} alt="Slide {slides[activeIdx].page}" />
        </div>
        <div class="nav">
          <button class="btn-ghost btn-sm" onclick={() => (activeIdx = Math.max(0, activeIdx - 1))} disabled={activeIdx === 0}>← Prev</button>
          <span>{activeIdx + 1} / {slides.length}</span>
          <button class="btn-ghost btn-sm" onclick={() => (activeIdx = Math.min(slides.length - 1, activeIdx + 1))} disabled={activeIdx === slides.length - 1}>Next →</button>
          <button class="btn-ghost btn-sm" onclick={() => (fullscreen = true)}>Fullscreen ⛶</button>
        </div>
      </main>
    </div>

    {#if fullscreen}
      <div class="fs" onclick={() => (fullscreen = false)} role="button" tabindex="0">
        <img src={slides[activeIdx].image_url} alt="Slide {slides[activeIdx].page}" />
        <div class="fs-hint">{activeIdx + 1} / {slides.length} · ← → to navigate · Esc to close</div>
      </div>
    {/if}
  {/if}
</div>

<style>
  .viewer { display: flex; flex-direction: column; height: calc(100vh - 56px); background: var(--pw-bg, #f8f5ef); }
  .bar { display: flex; align-items: center; gap: 12px; padding: 12px 20px; border-bottom: 1px solid var(--pw-border, #e5dfd2); background: var(--pw-surface, #fff); }
  .bar h1 { font-family: Georgia, serif; font-size: 18px; margin: 0; color: var(--pw-ink, #1a1614); }
  .bar .spacer { flex: 1; }
  .bar .count { font-size: 12px; color: var(--pw-muted, #8a847a); }
  .back { color: var(--pw-accent, #c96342); text-decoration: none; font-size: 13px; }
  .status { padding: 40px; text-align: center; color: var(--pw-muted, #8a847a); font-size: 14px; }
  .status.err { color: #b91c1c; }

  .layout { display: grid; grid-template-columns: 200px 1fr; flex: 1; min-height: 0; }
  .thumbs { overflow-y: auto; padding: 12px 8px; border-right: 1px solid var(--pw-border, #e5dfd2); background: var(--pw-surface, #fff); display: flex; flex-direction: column; gap: 8px; }
  .thumb { position: relative; padding: 0; border: 2px solid transparent; border-radius: 0; background: #fff; cursor: pointer; overflow: hidden; }
  .thumb.active { border-color: var(--pw-accent, #c96342); }
  .thumb img { width: 100%; display: block; }
  .thumb-num { position: absolute; bottom: 4px; right: 6px; background: rgba(0,0,0,0.55); color: #fff; font-size: 10px; padding: 1px 5px; border-radius: 0; }

  .main { display: flex; flex-direction: column; min-height: 0; padding: 16px 20px; gap: 12px; }
  .frame { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; background: #1a1614; border-radius: 0; cursor: zoom-in; overflow: hidden; }
  .frame img { max-width: 100%; max-height: 100%; object-fit: contain; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
  .nav { display: flex; align-items: center; justify-content: center; gap: 14px; font-size: 13px; color: var(--pw-muted, #8a847a); }

  .fs { position: fixed; inset: 0; background: rgba(0,0,0,0.92); z-index: 9999; display: flex; align-items: center; justify-content: center; cursor: zoom-out; padding: 40px; }
  .fs img { max-width: 100%; max-height: 100%; object-fit: contain; }
  .fs-hint { position: absolute; bottom: 16px; left: 50%; transform: translateX(-50%); color: rgba(255,255,255,0.6); font-size: 12px; }
</style>
