<script lang="ts">
  /**
   * SuggestionCard — surfaces the best-fit template suggestion after a fresh
   * upload completes.
   *
   * GET /api/agents/templates/suggest?slug={slug} → array of suggestions
   *   [{ template: {...}, score: number, reasons: string[] }]
   *
   * Top suggestion gets a hero card with [Use this template] + [See others ▾].
   * Hidden entirely if no suggestion clears 30% fit; in that case we fall
   * back to a single "Pick a template" link.
   */
  import { base } from '$app/paths';
  import { dashFetch } from '$lib/api';

  interface SuggestionTemplate {
    id: string;
    name?: string;
    description?: string;
    [k: string]: any;
  }
  interface Suggestion {
    template: SuggestionTemplate;
    score: number; // 0..1
    reasons?: string[];
  }

  interface Props {
    slug: string;
    onuse: (tpl: SuggestionTemplate | null) => void;
    /** Change this value to retrigger a fetch (e.g. after upload completes). */
    refresh_token?: any;
    onbrowse?: () => void;
  }
  let { slug, onuse, refresh_token = null, onbrowse }: Props = $props();

  let suggestions = $state<Suggestion[]>([]);
  let loading = $state(false);
  let error = $state('');
  let expanded = $state(false);
  let lastToken = $state<any>(undefined);

  async function load() {
    loading = true;
    error = '';
    try {
      const r = await dashFetch(`${base}/api/agents/templates/suggest?slug=${encodeURIComponent(slug)}`);
      if (!r.ok) {
        suggestions = [];
        // 404 / 501 = endpoint not wired yet; treat as quiet empty rather than scary error.
        if (r.status >= 500) error = `Failed (${r.status})`;
        return;
      }
      const data = await r.json();
      const list = Array.isArray(data) ? data : (data?.suggestions ?? data?.items ?? []);
      // Normalize score to 0..1 even if backend returns percentage.
      suggestions = (Array.isArray(list) ? list : [])
        .map((s: any) => {
          const tpl = s?.template ?? s;
          let score = Number(s?.score ?? s?.fit ?? tpl?.score ?? 0);
          if (Number.isFinite(score) && score > 1) score = score / 100;
          if (!Number.isFinite(score) || score < 0) score = 0;
          return { template: tpl, score, reasons: s?.reasons || s?.matches || [] };
        })
        .filter((s: Suggestion) => s.template && (s.template.id || s.template.name));
      // Sort descending by score
      suggestions.sort((a, b) => b.score - a.score);
    } catch (e: any) {
      error = String(e?.message || e);
      suggestions = [];
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (!slug) return;
    // Trigger on mount + every refresh_token change.
    if (refresh_token !== lastToken) {
      lastToken = refresh_token;
      load();
    }
  });

  const top = $derived(suggestions[0] ?? null);
  const rest = $derived(suggestions.slice(1));
  const hasStrong = $derived(top !== null && top.score >= 0.3);
  const formatPct = (v: number) => `${Math.round(v * 100)}%`;
</script>

{#if loading}
  <div class="sc-loading">Looking for template matches…</div>
{:else if hasStrong && top}
  <section class="sc-root" aria-label="Suggested templates">
    <div class="sc-banner">
      <span class="sc-icon">💡</span>
      Suggested agent template based on your data
    </div>

    <div class="sc-hero">
      <div class="sc-hero-row">
        <div class="sc-hero-title">
          <span class="sc-star">⭐</span>
          {top.template.name || top.template.id}
        </div>
        <span class="sc-fit">{formatPct(top.score)} fit</span>
      </div>
      {#if top.reasons && top.reasons.length > 0}
        <div class="sc-reasons">Matches: {top.reasons.slice(0, 4).join(', ')}</div>
      {:else if top.template.description}
        <div class="sc-reasons">{top.template.description}</div>
      {/if}
      <div class="sc-actions">
        <button type="button" class="sc-btn-primary" onclick={() => onuse(top.template)}>
          Use this template
        </button>
        {#if rest.length > 0}
          <button type="button" class="sc-btn-ghost" onclick={() => expanded = !expanded}>
            {expanded ? 'Hide others ▴' : 'See others ▾'}
          </button>
        {/if}
      </div>
    </div>

    {#if expanded && rest.length > 0}
      <div class="sc-others">
        <div class="sc-others-label">▾ Other matches:</div>
        <ul class="sc-others-list">
          {#each rest as s}
            <li class="sc-other-row">
              <span class="sc-other-dot">·</span>
              <span class="sc-other-name">{s.template.name || s.template.id}</span>
              <span class="sc-other-fit">{formatPct(s.score)}</span>
              <button type="button" class="sc-btn-mini" onclick={() => onuse(s.template)}>Use</button>
            </li>
          {/each}
          <li class="sc-other-row">
            <span class="sc-other-dot">·</span>
            <span class="sc-other-name">Custom (blank)</span>
            <span class="sc-other-fit sc-other-fit-muted">—</span>
            <button type="button" class="sc-btn-mini" onclick={() => onuse(null)}>Use</button>
          </li>
        </ul>
      </div>
    {/if}
  </section>
{:else if error}
  <div class="sc-err">{error}</div>
{:else}
  <div class="sc-fallback">
    <button type="button" class="sc-link" onclick={() => (onbrowse ? onbrowse() : onuse(null))}>
      Pick a template →
    </button>
  </div>
{/if}

<style>
  .sc-root {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 6px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-bottom: 12px;
  }
  .sc-banner {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11.5px;
    color: rgba(255, 255, 255, 0.65);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
  }
  .sc-icon { font-size: 14px; }

  .sc-hero {
    background: rgba(201, 99, 66, 0.06);
    border: 1px solid rgba(201, 99, 66, 0.35);
    border-radius: 5px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .sc-hero-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .sc-hero-title {
    font-size: 14px;
    font-weight: 600;
    color: #f5efe2;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .sc-star { color: #C96342; }
  .sc-fit {
    background: #C96342;
    color: #fff;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 3px;
    font-weight: 600;
    letter-spacing: 0.03em;
  }
  .sc-reasons {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.7);
    line-height: 1.5;
  }
  .sc-actions { display: flex; gap: 8px; flex-wrap: wrap; }

  .sc-btn-primary {
    background: #C96342;
    color: #fff;
    border: 1px solid #C96342;
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }
  .sc-btn-primary:hover { background: #b35636; border-color: #b35636; }
  .sc-btn-ghost {
    background: transparent;
    color: #C96342;
    border: 1px solid rgba(201, 99, 66, 0.5);
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }
  .sc-btn-ghost:hover { background: rgba(201, 99, 66, 0.08); border-color: #C96342; }
  .sc-btn-mini {
    background: transparent;
    color: #C96342;
    border: 1px solid rgba(201, 99, 66, 0.4);
    border-radius: 3px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }
  .sc-btn-mini:hover { background: #C96342; color: #fff; border-color: #C96342; }

  .sc-others {
    border-top: 1px dashed rgba(255, 255, 255, 0.1);
    padding-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .sc-others-label {
    font-size: 11px;
    color: rgba(255, 255, 255, 0.5);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .sc-others-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .sc-other-row {
    display: grid;
    grid-template-columns: 12px 1fr auto auto;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
    font-size: 12px;
  }
  .sc-other-dot { color: rgba(255, 255, 255, 0.35); }
  .sc-other-name { color: #e8e3d6; }
  .sc-other-fit { font-size: 11px; color: rgba(255, 255, 255, 0.6); font-variant-numeric: tabular-nums; }
  .sc-other-fit-muted { color: rgba(255, 255, 255, 0.35); }

  .sc-loading {
    font-size: 11.5px;
    color: rgba(255, 255, 255, 0.5);
    padding: 8px 12px;
    margin-bottom: 12px;
  }
  .sc-err {
    font-size: 12px;
    color: #f5b7b1;
    background: rgba(192, 57, 43, 0.1);
    border: 1px solid rgba(192, 57, 43, 0.35);
    border-radius: 4px;
    padding: 8px 12px;
    margin-bottom: 12px;
  }
  .sc-fallback {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.55);
    margin-bottom: 8px;
  }
  .sc-link {
    background: transparent;
    border: none;
    padding: 0;
    color: #C96342;
    cursor: pointer;
    font-family: inherit;
    font-size: 12px;
    font-weight: 600;
  }
  .sc-link:hover { text-decoration: underline; }
</style>
