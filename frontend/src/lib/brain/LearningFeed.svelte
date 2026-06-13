<script lang="ts">
  /* LEARNING FEED — "🤖 just learned …" recent facts + forming insights.
     ADDITIVE side panel for the overview. Reuses existing endpoints only. */
  import { onMount } from 'svelte';
  let { slug = 'citypharma' as string } = $props();

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }
  async function _get(u: string): Promise<any> { try { const r = await fetch(u, { headers: _h() }); if (r.ok) return await r.json(); } catch {} return null; }

  let items = $state<any[]>([]);
  function ago(ts: string): string {
    const t = new Date(ts || 0).getTime(); if (!t) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return Math.round(s) + 's ago';
    if (s < 3600) return Math.round(s / 60) + 'm ago';
    if (s < 864e2) return Math.round(s / 3600) + 'h ago';
    return Math.round(s / 864e2) + 'd ago';
  }
  onMount(async () => {
    const [m, ins] = await Promise.all([
      _get(`/api/projects/${slug}/memories`),
      _get(`/api/projects/${slug}/insights`),
    ]);
    const mem = (m?.memories || m || []).map((x: any) => ({ text: x.fact, ts: x.created_at, status: 'cemented' }));
    const insi = (ins?.insights || []).map((x: any) => ({ text: x.title || x.detail, ts: x.created_at, status: x.status === 'pending' ? 'review' : x.status === 'rejected' ? 'dropped' : 'cemented' }));
    items = [...insi, ...mem]
      .filter((x) => x.text)
      .sort((a, b) => new Date(b.ts || 0).getTime() - new Date(a.ts || 0).getTime())
      .slice(0, 8);
  });
</script>

<div class="lf">
  <div class="lf-h">🤖 LEARNING FEED <span class="lf-live"><span class="lf-dot"></span>live</span></div>
  {#if !items.length}
    <div class="lf-empty">nothing learned yet</div>
  {:else}
    <ul class="lf-list">
      {#each items as it}
        <li class="lf-row">
          <span class="lf-txt">{it.text}</span>
          <span class="lf-meta">
            <span class="lf-ago">{ago(it.ts)}</span>
            <span class="lf-tag {it.status}">{it.status === 'cemented' ? '✓ cemented' : it.status === 'review' ? '⏳ review' : '✕ dropped'}</span>
          </span>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .lf { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e7e0d4); border-radius: var(--pw-radius, 12px); padding: 14px 16px; }
  .lf-h { font-size: 11px; font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase; color: var(--pw-ink, #211e1a); display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .lf-live { font-size: 9px; color: var(--pw-muted, #877f74); display: inline-flex; align-items: center; gap: 5px; font-weight: 700; }
  .lf-dot { width: 6px; height: 6px; border-radius: 50%; background: #5a9367; animation: lfp 1.6s infinite; }
  @keyframes lfp { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .lf-empty { font-size: 12px; color: #b6ad9e; padding: 12px 0; }
  .lf-list { list-style: none; margin: 0; padding: 0; }
  .lf-row { padding: 8px 0; border-bottom: 1px solid var(--pw-border, #e7e0d4); display: flex; flex-direction: column; gap: 4px; animation: lfin 0.4s; }
  .lf-row:last-child { border-bottom: none; }
  @keyframes lfin { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
  .lf-txt { font-size: 12.5px; color: var(--pw-ink, #211e1a); line-height: 1.35; }
  .lf-meta { display: flex; align-items: center; gap: 8px; }
  .lf-ago { font-size: 10.5px; color: var(--pw-muted, #877f74); }
  .lf-tag { font-size: 10px; font-weight: 700; border-radius: 999px; padding: 1px 8px; }
  .lf-tag.cemented { background: #e7f1ea; color: #2d6a4f; }
  .lf-tag.review { background: #fbf0dd; color: #9a6a14; }
  .lf-tag.dropped { background: #f7e3e0; color: #a13b2c; text-decoration: line-through; }
</style>
