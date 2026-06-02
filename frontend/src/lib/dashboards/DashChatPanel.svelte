<script lang="ts">
  let { dashboardId, projectSlug, panels = [], onCitePanel = (_n: number) => {} } = $props<{
    dashboardId: string;
    projectSlug: string;
    panels?: any[];
    onCitePanel?: (n: number) => void;
  }>();

  function _headers(): Record<string, string> {
    const t = localStorage.getItem('dash_token');
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (t) h.Authorization = `Bearer ${t}`;
    return h;
  }

  let messages = $state<{role:'user'|'assistant'; content:string; cited?: number[]}[]>([]);
  let input = $state('');
  let busy = $state(false);

  async function send() {
    if (!input.trim() || busy) return;
    const q = input.trim();
    input = '';
    messages = [...messages, { role: 'user', content: q }];
    busy = true;
    try {
      const r = await fetch(`/api/dashboards/${encodeURIComponent(dashboardId)}/chat`, {
        method: 'POST',
        headers: _headers(),
        body: JSON.stringify({
          question: q,
          history: messages.slice(-10).map(m => ({ role: m.role, content: m.content })),
        }),
      });
      if (r.ok) {
        const j = await r.json();
        messages = [...messages, { role: 'assistant', content: j.answer || '(no answer)', cited: j.cited_panels || [] }];
      } else {
        messages = [...messages, { role: 'assistant', content: 'Error contacting agent.' }];
      }
    } catch (e: any) {
      messages = [...messages, { role: 'assistant', content: `Error: ${e?.message || 'network'}` }];
    }
    busy = false;
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }
</script>

<div style="display: flex; flex-direction: column; height: 100%; background: var(--pw-bg, #fdfaf5);">
  <div style="padding: 10px 12px; border-bottom: 1px solid var(--pw-border, #e2ddd2); display: flex; align-items: center; gap: 8px;">
    <span style="font-size: 14px;">🗨</span>
    <span style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-accent, #c96342);">Ask this dashboard</span>
  </div>

  <div style="flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px;">
    {#if messages.length === 0}
      <div style="color: var(--pw-muted); font-size: 12px; line-height: 1.5;">
        Ask questions about this dashboard. Examples:
        <ul style="margin: 6px 0 0 18px; padding: 0; font-size: 11px;">
          <li>What's the biggest finding?</li>
          <li>Which channel has the highest uncontactable rate?</li>
          <li>Compare Panel 2 vs Panel 4</li>
          <li>Why did volume drop in March?</li>
        </ul>
      </div>
    {/if}
    {#each messages as m}
      <div style="display: flex; flex-direction: column; gap: 4px; {m.role === 'user' ? 'align-items: flex-end' : 'align-items: flex-start'};">
        <div style="max-width: 90%; padding: 8px 12px; background: {m.role === 'user' ? 'var(--pw-bg-alt, #f7f6f3)' : 'rgba(201,99,66,0.05)'}; border: 1px solid {m.role === 'user' ? 'var(--pw-border, #e2ddd2)' : 'rgba(201,99,66,0.2)'}; font-size: 12px; line-height: 1.5; color: var(--pw-ink, #2c2a26); white-space: pre-wrap;">{m.content}</div>
        {#if m.cited && m.cited.length > 0}
          <div style="display: flex; gap: 4px; font-size: 10px;">
            {#each m.cited as n}
              <button onclick={() => onCitePanel(n)} style="padding: 2px 6px; background: var(--pw-bg); border: 1px solid var(--pw-border); color: var(--pw-accent); cursor: pointer; font-family: inherit;">Panel {n}</button>
            {/each}
          </div>
        {/if}
      </div>
    {/each}
    {#if busy}
      <div style="color: var(--pw-muted); font-size: 11px; font-style: italic;">thinking…</div>
    {/if}
  </div>

  <div style="padding: 10px; border-top: 1px solid var(--pw-border, #e2ddd2);">
    <textarea bind:value={input} onkeydown={onKey} placeholder="Ask about this dashboard…" rows="2" style="width: 100%; padding: 8px; border: 1px solid var(--pw-border, #e2ddd2); background: var(--pw-bg); font-family: inherit; font-size: 12px; resize: none;"></textarea>
    <div style="display: flex; justify-content: flex-end; margin-top: 6px;">
      <button onclick={send} disabled={busy || !input.trim()} style="padding: 4px 12px; background: var(--pw-accent, #c96342); color: #fff; border: none; cursor: {busy || !input.trim() ? 'not-allowed' : 'pointer'}; opacity: {busy || !input.trim() ? 0.45 : 1}; font-family: inherit; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;">Send</button>
    </div>
  </div>
</div>
