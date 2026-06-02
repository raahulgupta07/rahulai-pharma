<script lang="ts">
  /**
   * NewAgentForm — create an agent from a template (or blank).
   *
   * Submits POST /api/agents with:
   *   { project_slug, name, base_template, system_prompt, tool_keys, data_scope }
   * On 200 → onsave(response.id).
   */
  import { base } from '$app/paths';
  import { dashFetch } from '$lib/api';

  interface Template {
    id: string;
    name?: string;
    description?: string;
    system_prompt?: string;
    scoped_tools?: any[];
    [k: string]: any;
  }

  interface Props {
    slug: string;
    template: Template | null;
    onsave: (id: string) => void;
    oncancel: () => void;
  }
  let { slug, template, onsave, oncancel }: Props = $props();

  // --- Tool resolution: tolerate string list, object list, or object map ---
  function _normTools(scoped: any): { key: string; label: string; description?: string }[] {
    if (!scoped) return [];
    if (Array.isArray(scoped)) {
      return scoped
        .map((t) => {
          if (typeof t === 'string') return { key: t, label: t };
          if (t && typeof t === 'object') {
            const key = t.key || t.id || t.name || '';
            if (!key) return null;
            return { key, label: t.name || t.label || key, description: t.description };
          }
          return null;
        })
        .filter(Boolean) as any[];
    }
    if (typeof scoped === 'object') {
      return Object.entries(scoped).map(([k, v]: [string, any]) => ({
        key: k,
        label: (v && (v.name || v.label)) || k,
        description: v && v.description,
      }));
    }
    return [];
  }

  const allTools = $derived(_normTools(template?.scoped_tools));

  // Default-checked = all tools from template.
  let name = $state(template?.name ? `${template.name} agent` : 'New agent');
  let system_prompt = $state(template?.system_prompt || '');
  let tool_keys = $state<Set<string>>(new Set(allTools.map((t) => t.key)));
  let data_scope = $state<Record<string, any>>({});
  let scopeText = $state('{}');

  let submitting = $state(false);
  let error = $state('');

  const form_valid = $derived(name.trim().length > 0);

  function toggleTool(key: string) {
    const next = new Set(tool_keys);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    tool_keys = next;
  }
  function selectAll() {
    tool_keys = new Set(allTools.map((t) => t.key));
  }
  function clearAll() {
    tool_keys = new Set();
  }
  function syncScope() {
    try {
      const parsed = scopeText.trim() ? JSON.parse(scopeText) : {};
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        data_scope = parsed;
        error = '';
      } else {
        error = 'data_scope must be a JSON object';
      }
    } catch (e: any) {
      error = `Invalid JSON in data_scope: ${e?.message || e}`;
    }
  }

  async function submit() {
    if (!form_valid || submitting) return;
    syncScope();
    if (error) return;
    submitting = true;
    try {
      const body = {
        project_slug: slug,
        name: name.trim(),
        base_template: template?.id ?? null,
        system_prompt: system_prompt,
        tool_keys: Array.from(tool_keys),
        data_scope,
      };
      const r = await dashFetch(`${base}/api/agents`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => '');
        error = `Save failed (${r.status})${txt ? `: ${txt.slice(0, 200)}` : ''}`;
        return;
      }
      const data = await r.json().catch(() => ({} as any));
      const id = data?.id || data?.agent_id || '';
      onsave(id);
    } catch (e: any) {
      error = String(e?.message || e);
    } finally {
      submitting = false;
    }
  }
</script>

<div class="naf-overlay" onclick={oncancel} role="button" tabindex="-1" onkeydown={(e) => { if (e.key === 'Escape') oncancel(); }}>
  <div class="naf-card" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" tabindex="-1" onkeydown={(e) => e.stopPropagation()}>
    <header class="naf-head">
      <div>
        <h2 class="naf-title">{template ? `New agent · ${template.name || template.id}` : 'New agent · custom'}</h2>
        {#if template?.description}
          <p class="naf-sub">{template.description}</p>
        {/if}
      </div>
      <button class="naf-close" onclick={oncancel} type="button" aria-label="Close">✕</button>
    </header>

    <div class="naf-body">
      <label class="naf-field">
        <span class="naf-label">Name</span>
        <input
          type="text"
          bind:value={name}
          placeholder="e.g. Deal Analyst"
          class="naf-input"
        />
      </label>

      <label class="naf-field">
        <span class="naf-label">System prompt</span>
        <textarea
          bind:value={system_prompt}
          rows="6"
          placeholder="Describe what the agent should do…"
          class="naf-textarea"
        ></textarea>
      </label>

      <div class="naf-field">
        <div class="naf-label-row">
          <span class="naf-label">Tools</span>
          <div class="naf-label-actions">
            <button type="button" class="naf-link" onclick={selectAll}>Select all</button>
            <span class="naf-sep">·</span>
            <button type="button" class="naf-link" onclick={clearAll}>Clear</button>
          </div>
        </div>
        {#if allTools.length === 0}
          <div class="naf-empty">No tools associated with this template.</div>
        {:else}
          <div class="naf-tools">
            {#each allTools as t}
              <label class="naf-tool">
                <input
                  type="checkbox"
                  checked={tool_keys.has(t.key)}
                  onchange={() => toggleTool(t.key)}
                />
                <span class="naf-tool-name">{t.label}</span>
                {#if t.description}
                  <span class="naf-tool-desc">{t.description}</span>
                {/if}
              </label>
            {/each}
          </div>
        {/if}
      </div>

      <label class="naf-field">
        <span class="naf-label">Data scope (optional JSON)</span>
        <textarea
          bind:value={scopeText}
          onblur={syncScope}
          rows="3"
          spellcheck="false"
          placeholder={`{"tables": ["sales"], "filters": {}}`}
          class="naf-textarea naf-mono"
        ></textarea>
      </label>

      {#if error}
        <div class="naf-err">{error}</div>
      {/if}
    </div>

    <footer class="naf-foot">
      <button type="button" class="naf-btn-secondary" onclick={oncancel} disabled={submitting}>
        Cancel
      </button>
      <button type="button" class="naf-btn-primary" onclick={submit} disabled={!form_valid || submitting}>
        {submitting ? 'Creating…' : 'Create agent'}
      </button>
    </footer>
  </div>
</div>

<style>
  .naf-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    z-index: 9100;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .naf-card {
    background: #1A1614;
    color: #e8e3d6;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    width: 100%;
    max-width: 48rem;
    max-height: 90vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4);
  }
  .naf-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  }
  .naf-title { margin: 0; font-size: 16px; font-weight: 600; color: #f5efe2; }
  .naf-sub { margin: 4px 0 0; font-size: 12px; color: rgba(255, 255, 255, 0.55); }
  .naf-close {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.12);
    color: #e8e3d6;
    width: 28px;
    height: 28px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
  }
  .naf-close:hover { background: rgba(255, 255, 255, 0.06); color: #C96342; border-color: #C96342; }

  .naf-body { padding: 16px 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }

  .naf-field { display: flex; flex-direction: column; gap: 6px; }
  .naf-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: rgba(255, 255, 255, 0.55);
    font-weight: 600;
  }
  .naf-label-row { display: flex; align-items: center; justify-content: space-between; }
  .naf-label-actions { display: flex; align-items: center; gap: 6px; font-size: 11px; }
  .naf-link {
    background: transparent;
    border: none;
    padding: 0;
    color: #C96342;
    cursor: pointer;
    font-size: 11px;
    font-family: inherit;
  }
  .naf-link:hover { text-decoration: underline; }
  .naf-sep { color: rgba(255, 255, 255, 0.3); }

  .naf-input, .naf-textarea {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 4px;
    padding: 8px 10px;
    color: #f5efe2;
    font-family: inherit;
    font-size: 13px;
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
  }
  .naf-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
  .naf-input:focus, .naf-textarea:focus {
    outline: none;
    border-color: #C96342;
    background: rgba(201, 99, 66, 0.06);
  }

  .naf-tools {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 16rem;
    overflow-y: auto;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 4px;
    padding: 8px 10px;
    background: rgba(255, 255, 255, 0.02);
  }
  .naf-tool {
    display: grid;
    grid-template-columns: 18px 1fr auto;
    align-items: center;
    gap: 8px;
    padding: 4px 2px;
    cursor: pointer;
    font-size: 12.5px;
  }
  .naf-tool:hover { background: rgba(201, 99, 66, 0.06); }
  .naf-tool input { accent-color: #C96342; }
  .naf-tool-name { color: #e8e3d6; font-weight: 500; }
  .naf-tool-desc {
    color: rgba(255, 255, 255, 0.5);
    font-size: 11px;
    grid-column: 2 / -1;
    margin-top: 2px;
  }

  .naf-empty {
    border: 1px dashed rgba(255, 255, 255, 0.12);
    border-radius: 4px;
    padding: 12px;
    color: rgba(255, 255, 255, 0.45);
    font-size: 12px;
    text-align: center;
  }

  .naf-err {
    padding: 8px 10px;
    background: rgba(192, 57, 43, 0.12);
    border: 1px solid rgba(192, 57, 43, 0.45);
    color: #f5b7b1;
    font-size: 12px;
    border-radius: 4px;
  }

  .naf-foot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(0, 0, 0, 0.18);
  }
  .naf-btn-primary, .naf-btn-secondary {
    border-radius: 4px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    letter-spacing: 0.02em;
  }
  .naf-btn-primary {
    background: #C96342;
    color: #fff;
    border: 1px solid #C96342;
  }
  .naf-btn-primary:hover:not(:disabled) { background: #b35636; border-color: #b35636; }
  .naf-btn-primary:disabled { background: rgba(201, 99, 66, 0.45); cursor: not-allowed; border-color: transparent; }
  .naf-btn-secondary {
    background: transparent;
    color: #e8e3d6;
    border: 1px solid rgba(255, 255, 255, 0.12);
  }
  .naf-btn-secondary:hover:not(:disabled) { background: rgba(255, 255, 255, 0.06); border-color: #C96342; color: #C96342; }
  .naf-btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
