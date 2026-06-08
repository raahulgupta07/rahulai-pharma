<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  let { col, meta, slug, table_name } = $props<{
    col: { name: string; type: string; description?: string };
    meta: any | null;
    slug: string;
    table_name: string;
  }>();

  const dispatch = createEventDispatcher();

  let expanded = $state(false);
  let aiBusy = $state(false);
  let editBusy = $state(false);
  let localDesc = $state<string>('');
  let liveMeta = $state<any>(null);

  // Derived display data — prefer liveMeta (post-regenerate) else prop meta
  const m = $derived(liveMeta || meta);
  const semanticType = $derived(m?.semantic_type || null);
  const cardinality = $derived(m?.cardinality_class || null);
  const blurb = $derived(m?.description || col.description || '');
  const samples = $derived(Array.isArray(m?.samples) ? m.samples : []);
  const quality = $derived(m?.quality || null);
  const relationships = $derived(Array.isArray(m?.relationships) ? m.relationships : []);
  const suggestedQuestions = $derived(Array.isArray(m?.suggested_questions) ? m.suggested_questions : []);
  const hasLLMBlurb = $derived(Boolean(m?.description && m.description.trim().length > 0));

  // Description may carry an inline Burmese twin as "English\n[မြန်မာ] Burmese".
  // Split it into the English line + an optional Burmese line (prefix stripped).
  const descParts = $derived((() => {
    const lines = String(blurb || '').split('\n');
    const myIdx = lines.findIndex((l) => l.trim().startsWith('[မြန်မာ]'));
    if (myIdx === -1) return { en: blurb || '', my: '' };
    const my = lines[myIdx].trim().replace(/^\[မြန်မာ\]\s*/, '');
    const en = lines.filter((_, i) => i !== myIdx).join('\n').trim();
    return { en, my };
  })());

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  function toggleExpand() {
    expanded = !expanded;
  }

  function overlapColor(pct: number): string {
    if (pct >= 80) return '#2d8659';
    if (pct >= 50) return '#a06000';
    return 'var(--pw-muted)';
  }

  async function handleEdit(e: MouseEvent) {
    e.stopPropagation();
    const current = blurb || '';
    const newVal = prompt(`Business meaning for ${col.name}:`, current);
    if (!newVal || newVal === current) return;
    editBusy = true;
    try {
      await fetch(`/api/projects/${slug}/annotations`, {
        method: 'PUT',
        headers: { ..._h(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ table_name, column_name: col.name, annotation: newVal }),
      });
      localDesc = newVal;
      // Patch liveMeta description so UI updates instantly
      liveMeta = { ...(liveMeta || meta || {}), description: newVal };
    } catch (err) {
      console.warn('annotation save failed', err);
    } finally {
      editBusy = false;
    }
  }

  async function handleAIRegenerate(e: MouseEvent) {
    e.stopPropagation();
    if (aiBusy) return;
    aiBusy = true;
    try {
      const r = await fetch(`/api/projects/${slug}/columns/describe`, {
        method: 'POST',
        headers: { ..._h(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ table_name, column_names: [col.name] }),
      });
      if (r.ok) {
        // Refetch column metadata for this table
        const r2 = await fetch(`/api/projects/${slug}/columns/${table_name}`, { headers: _h() });
        if (r2.ok) {
          const d = await r2.json();
          const found = (Array.isArray(d?.columns) ? d.columns : []).find(
            (c: any) => c?.column_name === col.name,
          );
          if (found) liveMeta = found;
        }
      }
    } catch (err) {
      console.warn('AI regenerate failed', err);
    } finally {
      aiBusy = false;
    }
  }

  function askQuestion(q: string, e: MouseEvent) {
    e.stopPropagation();
    dispatch('askQuestion', q);
    // Fallback log so parent that doesn't yet forward still surfaces it
    console.log('[ColumnCard] askQuestion →', q);
  }
</script>

<!-- Collapsed row: matches existing 4-col table layout (Name · Type · Description · Edit) -->
<tr
  class="col-card-row"
  class:expanded
  onclick={toggleExpand}
  style="cursor: pointer; border-bottom: 1px solid var(--pw-bg-alt);"
>
  <td style="padding: 8px 6px; vertical-align: top; width: 22%;">
    <div style="display: flex; align-items: center; gap: 6px;">
      <span style="color: var(--pw-accent); font-weight: 700; font-size: 11px;">
        {expanded ? '▾' : '▸'}
      </span>
      <span style="font-weight: 700; font-family: ui-monospace, Menlo, monospace; font-size: 12px;">
        {col.name}
      </span>
    </div>
  </td>
  <td style="padding: 8px 6px; vertical-align: top; width: 14%;">
    <span style="font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: var(--pw-muted);">
      {col.type}
    </span>
  </td>
  <td style="padding: 8px 6px; vertical-align: top; width: 54%;">
    {#if semanticType || cardinality}
      <div style="display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 4px;">
        {#if cardinality}
          <span
            style="display: inline-block; padding: 1px 6px; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted); background: var(--pw-bg-alt); border-radius: 2px; font-weight: 600;"
          >
            {cardinality}
          </span>
        {/if}
        {#if semanticType}
          <span
            style="display: inline-block; padding: 1px 6px; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-accent); background: rgba(201,99,66,0.10); border-radius: 2px; font-weight: 700;"
          >
            {semanticType}
          </span>
        {/if}
      </div>
    {/if}
    {#if blurb}
      {#if descParts.my}
        <div class="col-bi-line">
          <span class="col-bi-badge">1</span>
          <span
            style="font-family: 'Source Serif Pro', Georgia, serif; font-size: 13px; color: var(--pw-ink); max-width: 600px; line-height: 1.45;"
            style:font-style={hasLLMBlurb ? 'normal' : 'italic'}
          >{descParts.en}</span>
        </div>
        <div class="col-bi-line" style="margin-top: 3px;">
          <span class="col-bi-badge">2</span>
          <span lang="my" style="font-size: 12.5px; color: var(--pw-muted); max-width: 600px; line-height: 1.45;">{descParts.my}</span>
        </div>
      {:else}
        <div
          style="font-family: 'Source Serif Pro', Georgia, serif; font-size: 13px; color: var(--pw-ink); max-width: 600px; line-height: 1.45;"
          style:font-style={hasLLMBlurb ? 'normal' : 'italic'}
        >
          {blurb}
        </div>
      {/if}
    {:else}
      <span style="font-size: 11px; color: var(--pw-muted); font-style: italic;">No description yet</span>
    {/if}
  </td>
  <td style="padding: 8px 6px; vertical-align: top; width: 10%; text-align: right; white-space: nowrap;">
    <button
      class="feedback-btn"
      style="font-size: 10.5px; padding: 2px 6px; margin-right: 4px;"
      disabled={editBusy}
      onclick={handleEdit}
      title="Edit business meaning"
    >
      Edit
    </button>
    <button
      class="feedback-btn"
      style="font-size: 10.5px; padding: 2px 6px;"
      disabled={aiBusy}
      onclick={handleAIRegenerate}
      title="Regenerate via LLM"
    >
      {aiBusy ? '…' : '🤖 AI'}
    </button>
  </td>
</tr>

<!-- Expanded panel: single full-width row spanning all 4 cols -->
{#if expanded}
  <tr class="col-card-expanded">
    <td colspan="4" style="padding: 0; background: var(--pw-bg-alt); border-bottom: 1px solid var(--pw-bg-alt);">
      <div style="padding: 14px 18px;">
        <!-- Samples + Quality grid -->
        {#if samples.length > 0 || quality}
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px;">
            {#if samples.length > 0}
              <div class="ink-border" style="background: var(--pw-surface); padding: 8px 10px;">
                <div
                  style="font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-muted); font-weight: 700; margin-bottom: 4px;"
                >
                  Samples
                </div>
                <ul style="list-style: none; padding: 0; margin: 0; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; line-height: 1.5;">
                  {#each samples.slice(0, 5) as s}
                    <li style="color: var(--pw-ink);">• {String(s)}</li>
                  {/each}
                </ul>
              </div>
            {/if}
            {#if quality}
              <div class="ink-border" style="background: var(--pw-surface); padding: 8px 10px;">
                <div
                  style="font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-muted); font-weight: 700; margin-bottom: 4px;"
                >
                  Quality
                </div>
                <div style="font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; line-height: 1.6; color: var(--pw-ink);">
                  {#if quality.null_pct !== undefined && quality.null_pct !== null}
                    NULL {Number(quality.null_pct).toFixed(quality.null_pct < 1 ? 2 : 0)}%
                  {/if}
                  {#if quality.dup_pct !== undefined && quality.dup_pct !== null}
                    {' · '}DUP {Number(quality.dup_pct).toFixed(quality.dup_pct < 1 ? 2 : 0)}%
                  {/if}
                  <br />
                  Format ✓ · PII {quality.pii_risk || 'none'}
                </div>
              </div>
            {/if}
          </div>
        {/if}

        <!-- Relationships -->
        {#if relationships.length > 0}
          <div class="ink-border" style="background: var(--pw-surface); padding: 8px 10px; margin-bottom: 10px;">
            <div
              style="font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-muted); font-weight: 700; margin-bottom: 4px;"
            >
              Relationships
            </div>
            <ul style="list-style: none; padding: 0; margin: 0; font-size: 11.5px; line-height: 1.6;">
              {#each relationships as r}
                {@const pct = Number(r?.overlap_pct ?? 0)}
                <li>
                  🔗
                  <span style="font-family: ui-monospace, Menlo, monospace; color: var(--pw-ink);">
                    {r.target_table || '?'}.{r.target_column || '?'}
                  </span>
                  {#if !Number.isNaN(pct) && pct > 0}
                    <span style="color: {overlapColor(pct)}; font-weight: 700;">({pct.toFixed(0)}% overlap)</span>
                  {/if}
                  {#if r.kind}
                    <span style="color: var(--pw-muted); font-size: 10.5px;"> · {r.kind}</span>
                  {/if}
                </li>
              {/each}
            </ul>
          </div>
        {/if}

        <!-- Suggested questions -->
        {#if suggestedQuestions.length > 0}
          <div class="ink-border" style="background: var(--pw-surface); padding: 8px 10px;">
            <div
              style="font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-muted); font-weight: 700; margin-bottom: 4px;"
            >
              Suggested questions
            </div>
            <div style="display: flex; flex-direction: column; gap: 4px;">
              {#each suggestedQuestions as q}
                <button
                  class="col-card-ask"
                  onclick={(e) => askQuestion(q, e)}
                  type="button"
                  style="display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 8px; background: transparent; border: 1px solid transparent; border-radius: 3px; cursor: pointer; text-align: left; font: inherit; color: inherit;"
                >
                  <span style="font-size: 12px; color: var(--pw-ink);">💡 {q}</span>
                  <span style="font-size: 11px; color: var(--pw-accent); font-weight: 700; white-space: nowrap;">Ask →</span>
                </button>
              {/each}
            </div>
          </div>
        {/if}

        <!-- Fallback: nothing enriched -->
        {#if !samples.length && !quality && !relationships.length && !suggestedQuestions.length && !semanticType}
          <div style="font-size: 11px; color: var(--pw-muted); font-style: italic;">
            No enriched metadata yet. Click 🤖 AI to generate.
          </div>
        {/if}
      </div>
    </td>
  </tr>
{/if}

<style>
  .col-card-row:hover {
    background: rgba(201, 99, 66, 0.04);
  }
  .col-card-row.expanded {
    background: rgba(201, 99, 66, 0.06);
  }
  .col-card-ask:hover {
    background: rgba(201, 99, 66, 0.08);
    border-color: rgba(201, 99, 66, 0.20) !important;
  }
  .col-bi-line {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }
  .col-bi-badge {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    font-size: 9px;
    font-weight: 700;
    line-height: 1;
    color: var(--pw-ink);
    background: var(--pw-bg-alt);
    border: 1px solid var(--pw-border);
    border-radius: 50%;
  }
</style>
