<script lang="ts">
  /**
   * TemplateGalleryModal — picks a built-in agent template.
   *
   * Renders a card grid fed by GET /api/agents/templates?source=builtin.
   * Selecting a card invokes onselect(template). Caller is responsible for
   * closing/handling next steps (typically open NewAgentForm).
   */
  import { base } from '$app/paths';
  import { dashFetch } from '$lib/api';

  interface Template {
    id: string;
    name: string;
    description?: string;
    base_agent?: string;
    scoped_tools?: any[];
    [k: string]: any;
  }

  interface Props {
    open: boolean;
    slug: string;
    onclose: () => void;
    onselect: (tpl: Template | null) => void;
  }
  let { open, slug, onclose, onselect }: Props = $props();

  let templates = $state<Template[]>([]);
  let loading = $state(false);
  let error = $state('');

  async function load() {
    loading = true;
    error = '';
    try {
      const r = await dashFetch(`${base}/api/agents/templates?source=builtin`);
      if (!r.ok) {
        error = `Failed to load templates (${r.status})`;
        templates = [];
        return;
      }
      const data = await r.json();
      const list = Array.isArray(data) ? data : (data?.templates ?? data?.items ?? []);
      templates = Array.isArray(list) ? list : [];
    } catch (e: any) {
      error = String(e?.message || e);
      templates = [];
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (open) {
      load();
    }
  });

  function toolCount(t: Template): number {
    const tools = t?.scoped_tools;
    if (Array.isArray(tools)) return tools.length;
    if (tools && typeof tools === 'object') return Object.keys(tools).length;
    return 0;
  }

  function pick(t: Template | null) {
    onselect(t);
  }
</script>

{#if open}
  <div class="tg-overlay" onclick={onclose} role="button" tabindex="-1" onkeydown={(e) => { if (e.key === 'Escape') onclose(); }}>
    <div class="tg-card" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" tabindex="-1" onkeydown={(e) => e.stopPropagation()}>
      <header class="tg-head">
        <div>
          <h2 class="tg-title">New agent</h2>
          <p class="tg-sub">Pick a template to start from, or start blank.</p>
        </div>
        <button class="tg-close" onclick={onclose} type="button" aria-label="Close">✕</button>
      </header>

      {#if loading}
        <div class="tg-state">Loading templates…</div>
      {:else if error}
        <div class="tg-state tg-err">{error}</div>
      {:else}
        <div class="tg-grid">
          <button class="tg-card-item tg-blank" onclick={() => pick(null)} type="button">
            <div class="tg-card-name">Custom (blank)</div>
            <div class="tg-card-desc">Start from scratch. No tools preselected.</div>
            <div class="tg-card-meta">
              <span class="tg-badge tg-badge-muted">blank</span>
            </div>
          </button>
          {#each templates as t}
            <button class="tg-card-item" onclick={() => pick(t)} type="button">
              <div class="tg-card-name">{t.name || t.id}</div>
              <div class="tg-card-desc">{t.description || '—'}</div>
              <div class="tg-card-meta">
                <span class="tg-badge">{toolCount(t)} tools</span>
                {#if t.base_agent}
                  <span class="tg-badge tg-badge-base">{t.base_agent}</span>
                {/if}
              </div>
            </button>
          {/each}
          {#if templates.length === 0}
            <div class="tg-state tg-empty">No templates available yet.</div>
          {/if}
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .tg-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    z-index: 9000;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .tg-card {
    background: #1A1614;
    color: #e8e3d6;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    width: 100%;
    max-width: 56rem;
    max-height: 86vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4);
  }
  .tg-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  }
  .tg-title { margin: 0; font-size: 16px; font-weight: 600; color: #f5efe2; }
  .tg-sub { margin: 4px 0 0; font-size: 12px; color: rgba(255, 255, 255, 0.55); }
  .tg-close {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.12);
    color: #e8e3d6;
    width: 28px;
    height: 28px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    line-height: 1;
  }
  .tg-close:hover { background: rgba(255, 255, 255, 0.06); color: #C96342; border-color: #C96342; }
  .tg-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 12px;
    padding: 16px 20px 20px;
    overflow-y: auto;
  }
  .tg-card-item {
    text-align: left;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 6px;
    padding: 12px 14px;
    cursor: pointer;
    color: #e8e3d6;
    font-family: inherit;
    transition: border-color 0.12s, background 0.12s, transform 0.12s;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .tg-card-item:hover {
    border-color: #C96342;
    background: rgba(201, 99, 66, 0.08);
    transform: translateY(-1px);
  }
  .tg-card-blank { border-style: dashed; }
  .tg-card-name { font-size: 13px; font-weight: 600; color: #f5efe2; }
  .tg-card-desc {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.6);
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .tg-card-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
  .tg-badge {
    font-size: 10.5px;
    padding: 2px 6px;
    background: rgba(201, 99, 66, 0.18);
    color: #C96342;
    border-radius: 3px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
  }
  .tg-badge-base {
    background: rgba(255, 255, 255, 0.08);
    color: rgba(255, 255, 255, 0.7);
  }
  .tg-badge-muted {
    background: rgba(255, 255, 255, 0.06);
    color: rgba(255, 255, 255, 0.5);
  }
  .tg-state {
    grid-column: 1 / -1;
    padding: 32px;
    text-align: center;
    color: rgba(255, 255, 255, 0.5);
    font-size: 13px;
  }
  .tg-err { color: #c0392b; }
  .tg-empty { color: rgba(255, 255, 255, 0.45); }
</style>
