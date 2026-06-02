<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';
  import { dashFetch } from '$lib/api';

  let { slug }: { slug: string } = $props();
  let canvases = $state<any[]>([]);
  let loading = $state(true);
  let err = $state('');
  let creating = $state(false);

  async function load() {
    loading = true;
    err = '';
    try {
      const res = await dashFetch(`/api/canvas/${slug}/list`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      canvases = Array.isArray(data?.canvases) ? data.canvases : [];
    } catch (e: any) {
      err = String(e?.message || e);
      canvases = [];
    } finally {
      loading = false;
    }
  }

  async function createNew() {
    if (creating) return;
    creating = true;
    try {
      const res = await dashFetch(`/api/canvas/${slug}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Untitled canvas', board: { cards: [] } }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data?.id) {
        goto(`${base}/project/${slug}/canvas/${data.id}`);
      } else {
        await load();
      }
    } catch (e: any) {
      err = String(e?.message || e);
    } finally {
      creating = false;
    }
  }

  async function del(id: string, name: string) {
    if (!confirm(`Delete "${name}"?`)) return;
    try {
      await dashFetch(`/api/canvas/${slug}/${id}`, { method: 'DELETE' });
      await load();
    } catch (e) {
      console.error('delete failed', e);
    }
  }

  function cardCount(c: any): number {
    const cards = c?.board?.cards;
    return Array.isArray(cards) ? cards.length : 0;
  }

  function relTime(iso: string): string {
    if (!iso) return '';
    const d = new Date(iso).getTime();
    const sec = Math.floor((Date.now() - d) / 1000);
    if (sec < 60) return `${sec}s ago`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  }

  onMount(load);
</script>

<div class="cv-page">
  <header class="ch">
    <div>
      <h2>Canvas</h2>
      <p class="sub">Free-form analysis boards · drag cards anywhere</p>
    </div>
    <button class="new-btn" onclick={createNew} disabled={creating}>
      {creating ? 'Creating…' : '+ New canvas'}
    </button>
  </header>

  {#if err}<div class="err">⚠ {err}</div>{/if}

  {#if loading}
    <div class="empty">Loading…</div>
  {:else if canvases.length === 0}
    <div class="empty">
      <p>No canvases yet.</p>
      <button class="new-btn" onclick={createNew} disabled={creating}>+ Create your first canvas</button>
    </div>
  {:else}
    <div class="grid">
      {#each canvases as c (c.id)}
        <div class="card" role="button" tabindex="0"
             onclick={() => goto(`${base}/project/${slug}/canvas/${c.id}`)}
             onkeydown={(e) => { if (e.key === 'Enter') goto(`${base}/project/${slug}/canvas/${c.id}`); }}>
          <div class="thumb">
            <div class="thumb-grid">
              {#each Array.from({ length: Math.min(4, cardCount(c)) }) as _, i}
                <div class="thumb-card" style="--i: {i}"></div>
              {/each}
              {#if cardCount(c) === 0}
                <div class="thumb-empty">empty</div>
              {/if}
            </div>
          </div>
          <div class="meta">
            <div class="name">{c.name || 'Untitled'}</div>
            <div class="info">
              <span>{cardCount(c)} card{cardCount(c) === 1 ? '' : 's'}</span>
              <span>·</span>
              <span>{relTime(c.updated_at)}</span>
            </div>
          </div>
          <button class="del" onclick={(e) => { e.stopPropagation(); del(c.id, c.name); }} title="Delete canvas">✕</button>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .cv-page { padding: 8px 4px 40px; color: var(--pw-ink, #2c2a26); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
  .ch { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 18px; border-bottom: 1px solid var(--pw-border, #e0d8c5); padding-bottom: 12px; }
  h2 { font-family: 'Source Serif Pro', Georgia, serif; font-size: 22px; font-weight: 600; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: var(--pw-ink-soft, #6b6557); font-size: 12px; margin: 0; }
  .new-btn { background: var(--pw-accent, #c96342); color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; transition: background 0.15s; }
  .new-btn:hover { background: #b35535; }
  .new-btn:disabled { opacity: 0.6; cursor: wait; }
  .err { background: #fde8e1; border: 1px solid #c0392b; color: #8a2a10; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
  .empty { text-align: center; padding: 60px 20px; color: var(--pw-ink-soft, #6b6557); }
  .empty p { margin-bottom: 16px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
  .card { position: relative; background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e0d8c5); border-radius: 8px; overflow: hidden; cursor: pointer; transition: transform 0.12s, border-color 0.12s; }
  .card:hover { transform: translateY(-2px); border-color: var(--pw-accent, #c96342); }
  .thumb { height: 120px; background: var(--pw-bg-alt, #efeadc); border-bottom: 1px solid var(--pw-border, #e0d8c5); position: relative; overflow: hidden; }
  .thumb-grid { position: absolute; inset: 0; padding: 10px; display: grid; grid-template-columns: 1fr 1fr; gap: 6px; align-content: start; }
  .thumb-card { background: linear-gradient(135deg, #d9d2bc, #c4bda9); border: 1px solid #b7af9a; border-radius: 4px; height: 32px; }
  .thumb-empty { grid-column: 1 / -1; text-align: center; color: var(--pw-ink-soft, #6b6557); font-size: 11px; padding-top: 36px; font-style: italic; }
  .meta { padding: 10px 12px; }
  .name { font-weight: 600; font-size: 13px; color: var(--pw-ink, #2c2a26); margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .info { color: var(--pw-ink-soft, #6b6557); font-size: 11px; display: flex; gap: 6px; }
  .del { position: absolute; top: 8px; right: 8px; background: rgba(255, 255, 255, 0.9); color: var(--pw-ink, #2c2a26); border: 1px solid var(--pw-border, #e0d8c5); width: 24px; height: 24px; border-radius: 4px; cursor: pointer; font-size: 12px; opacity: 0; transition: opacity 0.15s; }
  .card:hover .del { opacity: 1; }
  .del:hover { background: #c0392b; color: #fff; border-color: #c0392b; }
</style>
