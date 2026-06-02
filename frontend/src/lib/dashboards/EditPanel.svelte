<script lang="ts">
  type Entry = { prompt: string; rationale: string; ops: any[]; ts: number };

  let { spec = $bindable<any>(), changedIds = $bindable<string[]>([]) } = $props<{
    spec: any;
    changedIds: string[];
  }>();

  let prompt = $state('');
  let busy = $state(false);
  let error = $state('');
  let history = $state<Entry[]>([]);
  let stack = $state<any[]>([]); // past spec versions for undo

  function _headers(): Record<string, string> {
    const t = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  function cellIds(s: any): string[] {
    return (s?.cells || []).map((c: any) => c.id).filter(Boolean);
  }

  function flashChanges(oldSpec: any, newSpec: any) {
    const oldMap = new Map((oldSpec?.cells || []).map((c: any) => [c.id, JSON.stringify(c)]));
    const ids: string[] = [];
    for (const c of newSpec?.cells || []) {
      if (oldMap.get(c.id) !== JSON.stringify(c)) ids.push(c.id);
    }
    changedIds = ids;
    setTimeout(() => { changedIds = []; }, 600);
  }

  async function submit() {
    if (!prompt.trim() || busy || !spec) return;
    busy = true;
    error = '';
    const userPrompt = prompt;
    try {
      const r = await fetch('/api/dashboards/patch', {
        method: 'POST',
        headers: { ..._headers(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ spec, prompt: userPrompt })
      });
      const data = await r.json();
      if (!data.ok) {
        error = data.error || 'patch failed';
      } else {
        stack = [...stack, spec];
        const oldSpec = spec;
        spec = data.spec;
        flashChanges(oldSpec, data.spec);
        history = [
          { prompt: userPrompt, rationale: data.rationale || '', ops: data.ops || [], ts: Date.now() },
          ...history
        ].slice(0, 5);
        prompt = '';
      }
    } catch (e: any) {
      error = String(e);
    }
    busy = false;
  }

  function undo(idx: number) {
    // idx is position in history (0 = most recent). Pop matching count from stack.
    const popCount = idx + 1;
    if (stack.length < popCount) return;
    const target = stack[stack.length - popCount];
    const oldSpec = spec;
    spec = target;
    stack = stack.slice(0, stack.length - popCount);
    history = history.slice(popCount);
    flashChanges(oldSpec, target);
  }
</script>

<aside class="edit-panel">
  <div class="hdr">EDIT</div>
  <div class="input-row">
    <input
      class="ipt"
      type="text"
      placeholder="tell me what to change..."
      bind:value={prompt}
      disabled={busy}
      onkeydown={(e) => e.key === 'Enter' && submit()}
    />
    <button class="go" onclick={submit} disabled={busy || !prompt.trim()}>
      {busy ? '…' : '→'}
    </button>
  </div>
  {#if error}<div class="err">{error}</div>{/if}
  {#if history.length > 0}
    <div class="hist-hdr">RECENT EDITS</div>
    <div class="hist">
      {#each history as h, i}
        <div class="row">
          <div class="row-main">
            <div class="prompt">"{h.prompt}"</div>
            {#if h.rationale}<div class="rat">{h.rationale}</div>{/if}
            <div class="ops">{h.ops.length} op{h.ops.length === 1 ? '' : 's'}</div>
          </div>
          <button class="undo" onclick={() => undo(i)} title="Undo">↶</button>
        </div>
      {/each}
    </div>
  {/if}
</aside>

<style>
  .edit-panel { background: #1a1a1a; color: #fafaf7; border-radius: 0; padding: 10px; font-family: ui-monospace, 'SF Mono', Menlo, monospace; font-size: 11px; align-self: flex-start; position: sticky; top: 20px; max-height: 80vh; overflow-y: auto; }
  .hdr { color: #4caf50; font-weight: 700; letter-spacing: 0.15em; padding: 4px 4px 8px; border-bottom: 1px solid #333; margin-bottom: 8px; }
  .input-row { display: flex; gap: 6px; }
  .ipt { flex: 1; background: #0e0e0e; color: #fafaf7; border: 1px solid #333; border-radius: 0; padding: 6px 8px; font-family: inherit; font-size: 11px; outline: none; }
  .ipt:focus { border-color: #4caf50; }
  .go { background: #2e7d32; color: #fff; border: none; border-radius: 0; padding: 0 12px; font-weight: 700; cursor: pointer; }
  .go:disabled { opacity: 0.5; cursor: not-allowed; }
  .err { background: #4a1212; color: #ff8a80; padding: 6px 8px; border-radius: 0; margin-top: 6px; font-size: 11px; }
  .hist-hdr { color: #888; font-size: 10px; letter-spacing: 0.15em; margin: 12px 4px 6px; }
  .hist { display: flex; flex-direction: column; gap: 6px; }
  .row { display: flex; gap: 6px; align-items: flex-start; background: #0e0e0e; border: 1px solid #2a2a2a; border-radius: 0; padding: 6px 8px; }
  .row-main { flex: 1; min-width: 0; }
  .prompt { color: #fafaf7; word-break: break-word; }
  .rat { color: #aaa; font-size: 11px; margin-top: 2px; word-break: break-word; }
  .ops { color: #4caf50; font-size: 10px; margin-top: 2px; }
  .undo { background: transparent; color: #aaa; border: 1px solid #333; border-radius: 0; padding: 2px 8px; cursor: pointer; font-size: 11px; }
  .undo:hover { color: #4caf50; border-color: #4caf50; }
</style>
