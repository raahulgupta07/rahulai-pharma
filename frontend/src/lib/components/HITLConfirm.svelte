<script lang="ts">
  // Svelte 5 runes
  import { onMount, onDestroy, createEventDispatcher } from 'svelte';

  type SchemaField = {
    name: string;
    type?: string; // 'string' | 'number' | 'select'
    label?: string;
    options?: string[];
    default?: any;
    required?: boolean;
  };

  type HITLPending = {
    run_id: string;
    project_slug?: string | null;
    agent_name: string;
    action_type: 'confirmation' | 'user_input' | 'external_execution' | string;
    payload: any;
    expires_at: string; // ISO
    created_at?: string;
  };

  let { pending }: { pending: HITLPending } = $props();
  const dispatch = createEventDispatcher<{ resolved: { run_id: string; outcome: string } }>();

  let showDetails = $state(false);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let now = $state(Date.now());
  let interval: any;

  // user_input form state
  let formData = $state<Record<string, any>>({});

  $effect(() => {
    // Initialize form defaults from schema (run when payload changes)
    const fields = schemaFields();
    const next: Record<string, any> = {};
    for (const f of fields) {
      next[f.name] = formData[f.name] ?? f.default ?? '';
    }
    formData = next;
  });

  onMount(() => {
    interval = setInterval(() => (now = Date.now()), 1000);
  });
  onDestroy(() => interval && clearInterval(interval));

  const expiresMs = $derived(new Date(pending.expires_at).getTime());
  const remaining = $derived(Math.max(0, expiresMs - now));
  const remainingLabel = $derived(formatRemaining(remaining));
  const expired = $derived(remaining <= 0);

  function formatRemaining(ms: number): string {
    if (ms <= 0) return 'expired';
    const s = Math.floor(ms / 1000);
    const mm = Math.floor(s / 60);
    const ss = s % 60;
    return `${mm}:${ss.toString().padStart(2, '0')}`;
  }

  function token(): string {
    try {
      return localStorage.getItem('dash_token') || '';
    } catch {
      return '';
    }
  }

  function payloadAny(): any {
    return pending.payload || {};
  }

  function sqlPreview(): string | null {
    const p = payloadAny();
    const sql = p.sql || p?.kwargs?.sql || p?.args?.[1];
    return typeof sql === 'string' && sql.length ? sql : null;
  }

  function actionMessage(): string {
    const p = payloadAny();
    return p.message || p.prompt || `${pending.agent_name} requests ${pending.action_type}`;
  }

  function manifest(): any {
    return payloadAny().manifest || null;
  }

  function schemaFields(): SchemaField[] {
    if (pending.action_type !== 'user_input') return [];
    const p = payloadAny();
    const schema = p.schema || {};
    const props = schema.properties || {};
    const required: string[] = schema.required || [];
    const fields: SchemaField[] = [];
    for (const [name, raw] of Object.entries<any>(props)) {
      let type = 'string';
      if (raw?.type === 'number' || raw?.type === 'integer') type = 'number';
      if (raw?.enum) type = 'select';
      fields.push({
        name,
        type,
        label: raw?.title || name,
        options: raw?.enum,
        default: raw?.default,
        required: required.includes(name),
      });
    }
    return fields;
  }

  async function postJSON(path: string, body: any): Promise<Response> {
    return fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token()}`,
      },
      body: JSON.stringify(body),
    });
  }

  async function approve() {
    if (busy || expired) return;
    busy = true;
    error = null;
    try {
      const res = await postJSON(`/api/hitl/${pending.run_id}/respond`, { decision: 'approve' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      dispatch('resolved', { run_id: pending.run_id, outcome: 'approved' });
    } catch (e: any) {
      error = e?.message || 'request failed';
    } finally {
      busy = false;
    }
  }

  async function reject() {
    if (busy || expired) return;
    busy = true;
    error = null;
    try {
      const res = await postJSON(`/api/hitl/${pending.run_id}/respond`, { decision: 'reject' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      dispatch('resolved', { run_id: pending.run_id, outcome: 'rejected' });
    } catch (e: any) {
      error = e?.message || 'request failed';
    } finally {
      busy = false;
    }
  }

  async function submitInput() {
    if (busy || expired) return;
    busy = true;
    error = null;
    try {
      // Coerce numbers
      const data: Record<string, any> = {};
      for (const f of schemaFields()) {
        let v = formData[f.name];
        if (f.type === 'number' && v !== '' && v !== null && v !== undefined) v = Number(v);
        data[f.name] = v;
      }
      const res = await postJSON(`/api/hitl/${pending.run_id}/input`, { data });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      dispatch('resolved', { run_id: pending.run_id, outcome: 'submitted' });
    } catch (e: any) {
      error = e?.message || 'request failed';
    } finally {
      busy = false;
    }
  }

  function toggleDetails() {
    showDetails = !showDetails;
  }
  function onDetailsKey(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      toggleDetails();
    }
  }
</script>

<div class="hitl-card" data-action={pending.action_type}>
  <div class="hitl-head">
    <span class="hitl-badge">HUMAN APPROVAL</span>
    <span class="hitl-agent">{pending.agent_name}</span>
    <span class="hitl-spacer"></span>
    <span class="hitl-timer" class:expired>{remainingLabel}</span>
  </div>

  <div class="hitl-body">
    <div class="hitl-msg">{actionMessage()}</div>

    {#if pending.action_type === 'confirmation'}
      {#if sqlPreview()}
        <pre class="hitl-pre">{sqlPreview()}</pre>
      {/if}

      {#if showDetails}
        <pre class="hitl-pre hitl-pre-json">{JSON.stringify(payloadAny(), null, 2)}</pre>
      {/if}
    {:else if pending.action_type === 'user_input'}
      <form
        class="hitl-form"
        onsubmit={(e) => {
          e.preventDefault();
          submitInput();
        }}
      >
        {#each schemaFields() as f (f.name)}
          <label class="hitl-label">
            <span class="hitl-label-text">{f.label}{f.required ? ' *' : ''}</span>
            {#if f.type === 'select'}
              <select class="hitl-input" bind:value={formData[f.name]}>
                {#each f.options || [] as opt}
                  <option value={opt}>{opt}</option>
                {/each}
              </select>
            {:else if f.type === 'number'}
              <input
                class="hitl-input"
                type="number"
                bind:value={formData[f.name]}
                required={f.required}
              />
            {:else}
              <input
                class="hitl-input"
                type="text"
                bind:value={formData[f.name]}
                required={f.required}
              />
            {/if}
          </label>
        {/each}
      </form>
    {:else if pending.action_type === 'external_execution'}
      <div class="hitl-manifest">
        <pre class="hitl-pre">{JSON.stringify(manifest() ?? payloadAny(), null, 2)}</pre>
        <div class="hitl-waiting">
          <span class="hitl-dot"></span>
          <span class="hitl-dot"></span>
          <span class="hitl-dot"></span>
          <span class="hitl-waiting-label">waiting for external executor…</span>
        </div>
      </div>
    {/if}

    {#if error}
      <div class="hitl-error">{error}</div>
    {/if}
  </div>

  <div class="hitl-actions">
    {#if pending.action_type === 'confirmation'}
      <button
        type="button"
        class="hitl-btn hitl-approve"
        disabled={busy || expired}
        onclick={approve}
      >
        APPROVE
      </button>
      <button
        type="button"
        class="hitl-btn hitl-ghost"
        disabled={busy || expired}
        onclick={reject}
      >
        REJECT
      </button>
      <div
        role="button"
        tabindex="0"
        class="hitl-btn hitl-ghost"
        onclick={toggleDetails}
        onkeydown={onDetailsKey}
      >
        {showDetails ? 'HIDE DETAILS' : 'SHOW DETAILS'}
      </div>
    {:else if pending.action_type === 'user_input'}
      <button
        type="button"
        class="hitl-btn hitl-approve"
        disabled={busy || expired}
        onclick={submitInput}
      >
        SUBMIT
      </button>
      <button
        type="button"
        class="hitl-btn hitl-ghost"
        disabled={busy || expired}
        onclick={reject}
      >
        CANCEL
      </button>
    {/if}
  </div>
</div>

<style>
  .hitl-card {
    border: 2px solid var(--pw-accent, #c96342);
    border-radius: var(--pw-radius-sm, 8px);
    background: var(--pw-bg-alt, #faf2ed);
    color: var(--pw-ink, #2c2c2c);
    padding: 14px 16px;
    margin: 12px 0;
    font-size: 11px;
    line-height: 1.4;
  }
  .hitl-head {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .hitl-badge {
    background: var(--pw-accent, #c96342);
    color: #fff;
    padding: 2px 8px;
    border-radius: 0;
    font-weight: 700;
  }
  .hitl-agent {
    font-weight: 600;
    color: var(--pw-ink, #2c2c2c);
  }
  .hitl-spacer { flex: 1; }
  .hitl-timer {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--pw-ink, #2c2c2c);
  }
  .hitl-timer.expired { color: #b04f30; font-weight: 700; }

  .hitl-body { margin-bottom: 12px; }
  .hitl-msg {
    font-size: 11px;
    color: var(--pw-ink, #2c2c2c);
    margin-bottom: 8px;
  }
  .hitl-pre {
    background: #1a1714;
    color: #f7f6f3;
    padding: 10px 12px;
    border-radius: 0;
    overflow: auto;
    max-height: 240px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    margin: 8px 0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .hitl-pre-json {
    background: #faf2ed;
    color: var(--pw-ink, #2c2c2c);
    border: 1px solid rgba(201,99,66,0.18);
  }

  .hitl-form { display: flex; flex-direction: column; gap: 8px; }
  .hitl-label { display: flex; flex-direction: column; gap: 4px; }
  .hitl-label-text {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--pw-ink-soft, #4a4a48);
  }
  .hitl-input {
    border: 1px solid rgba(201,99,66,0.24);
    background: #fff;
    color: var(--pw-ink, #2c2c2c);
    padding: 6px 10px;
    border-radius: 0;
    font-size: 11px;
  }
  .hitl-input:focus {
    outline: none;
    border-color: var(--pw-accent, #c96342);
    box-shadow: 0 0 0 3px rgba(201,99,66,0.18);
  }

  .hitl-manifest { display: flex; flex-direction: column; gap: 8px; }
  .hitl-waiting {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--pw-ink-soft, #4a4a48);
    font-size: 11px;
  }
  .hitl-waiting-label { margin-left: 6px; }
  .hitl-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--pw-accent, #c96342);
    animation: hitl-pulse 1.2s infinite ease-in-out;
  }
  .hitl-dot:nth-child(2) { animation-delay: 0.2s; }
  .hitl-dot:nth-child(3) { animation-delay: 0.4s; }
  @keyframes hitl-pulse {
    0%, 100% { opacity: 0.3; transform: scale(0.85); }
    50%      { opacity: 1;   transform: scale(1); }
  }

  .hitl-error {
    color: #b04f30;
    background: rgba(176,79,48,0.08);
    border: 1px solid rgba(176,79,48,0.25);
    padding: 6px 10px;
    border-radius: 0;
    margin-top: 8px;
    font-size: 11px;
  }

  .hitl-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .hitl-btn {
    border-radius: var(--pw-radius-sm, 8px);
    padding: 8px 14px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    cursor: pointer;
    border: 1px solid transparent;
    line-height: 1;
    user-select: none;
  }
  .hitl-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .hitl-approve {
    background: var(--pw-accent, #c96342);
    color: #fff;
    border-color: var(--pw-accent, #c96342);
  }
  .hitl-approve:hover:not(:disabled) { background: #b8553a; border-color: #b8553a; }
  .hitl-ghost {
    background: transparent;
    color: var(--pw-ink, #2c2c2c);
    border-color: rgba(201,99,66,0.30);
  }
  .hitl-ghost:hover:not(:disabled) { background: rgba(201,99,66,0.08); }
</style>
