<script lang="ts">
  let { sub = 'overview' } = $props<{ sub?: string }>();

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  const ENDPOINTS: Record<string, string> = {
    overview: '/api/governance/summary',
    policies: '/api/governance/policies',
    approvals: '/api/governance/approvals',
    zones: '/api/governance/data-zones',
    pii: '/api/governance/pii-rules',
    retention: '/api/governance/retention',
    hooks: '/api/governance/audit-hooks',
    compliance: '/api/governance/compliance-map',
  };

  let data = $state<any>(null);
  let loading = $state(false);
  let err = $state<string>('');
  let showNewPolicy = $state(false);
  let newPolicy = $state({ name: '', scope: 'global', rule: '', severity: 'warn' });

  const endpoint = $derived(ENDPOINTS[sub] || ENDPOINTS.overview);

  async function load(ep: string) {
    loading = true; err = ''; data = null;
    try {
      const r = await fetch(ep, { headers: _h() });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e: any) {
      err = e?.message || 'load failed';
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(endpoint); });

  async function decide(reqId: string, decision: 'approve' | 'deny') {
    try {
      await fetch(`/api/governance/approvals/${reqId}/decide`, {
        method: 'POST',
        headers: { ..._h(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
      await load(endpoint);
    } catch {}
  }

  async function createPolicy() {
    try {
      await fetch('/api/governance/policies', {
        method: 'POST',
        headers: { ..._h(), 'Content-Type': 'application/json' },
        body: JSON.stringify(newPolicy),
      });
      showNewPolicy = false;
      newPolicy = { name: '', scope: 'global', rule: '', severity: 'warn' };
      await load(endpoint);
    } catch {}
  }

  function rows(): any[] {
    if (!data) return [];
    if (Array.isArray(data)) return data;
    for (const k of ['items','rows','policies','approvals','zones','rules','hooks','frameworks']) {
      if (Array.isArray((data as any)[k])) return (data as any)[k];
    }
    return [];
  }

  function stats(): { k: string; v: any }[] {
    if (!data) return [];
    if (data.stats && typeof data.stats === 'object') {
      return Object.entries(data.stats).slice(0, 5).map(([k, v]) => ({ k, v }));
    }
    const out: { k: string; v: any }[] = [];
    for (const [k, v] of Object.entries(data)) {
      if (typeof v === 'number' || typeof v === 'string') out.push({ k, v });
      if (out.length >= 5) break;
    }
    return out;
  }

  function cols(list: any[]): string[] {
    if (!list.length) return [];
    const first = list[0];
    if (typeof first !== 'object' || first === null) return [];
    return Object.keys(first).slice(0, 8);
  }
</script>

<div class="sub-content">
  {#if loading}
    <div class="muted">loading…</div>
  {:else if err}
    <div class="err">error: {err}</div>
  {:else if sub === 'overview'}
    {@const st = stats()}
    {#if st.length}
      <div class="stat-strip">
        {#each st.slice(0, 5) as s}
          <div class="stat">
            <div class="stat-k">{s.k}</div>
            <div class="stat-v">{s.v}</div>
          </div>
        {/each}
      </div>
    {:else}
      <div class="muted">no summary data</div>
    {/if}
    <div class="section-title">Recent activity</div>
    <table class="tbl">
      <thead><tr><th>when</th><th>kind</th><th>actor</th><th>detail</th></tr></thead>
      <tbody>
        <tr><td colspan="4" class="muted" style="text-align:center; padding:20px;">no recent activity</td></tr>
      </tbody>
    </table>
  {:else}
    {@const st = stats()}
    {@const lst = rows()}
    {@const cs = cols(lst)}
    {#if st.length}
      <div class="stat-strip">
        {#each st as s}
          <div class="stat">
            <div class="stat-k">{s.k}</div>
            <div class="stat-v">{s.v}</div>
          </div>
        {/each}
      </div>
    {/if}

    {#if sub === 'policies'}
      <div class="toolbar">
        <button class="btn" onclick={() => showNewPolicy = !showNewPolicy}>+ NEW POLICY</button>
      </div>
      {#if showNewPolicy}
        <div class="form">
          <input placeholder="name" bind:value={newPolicy.name} />
          <input placeholder="scope" bind:value={newPolicy.scope} />
          <input placeholder="rule" bind:value={newPolicy.rule} />
          <select bind:value={newPolicy.severity}>
            <option value="info">info</option>
            <option value="warn">warn</option>
            <option value="block">block</option>
          </select>
          <button class="btn" onclick={createPolicy}>CREATE</button>
        </div>
      {/if}
    {/if}

    {#if cs.length}
      <table class="tbl">
        <thead>
          <tr>
            {#each cs as c}<th>{c}</th>{/each}
            {#if sub === 'approvals'}<th>ACTION</th>{/if}
          </tr>
        </thead>
        <tbody>
          {#each lst as r}
            <tr>
              {#each cs as c}<td>{typeof r[c] === 'object' ? JSON.stringify(r[c]) : (r[c] ?? '')}</td>{/each}
              {#if sub === 'approvals'}
                <td>
                  <button class="btn-sm ok" onclick={() => decide(r.id || r.req_id, 'approve')}>approve</button>
                  <button class="btn-sm no" onclick={() => decide(r.id || r.req_id, 'deny')}>deny</button>
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    {:else}
      <div class="muted">no data</div>
    {/if}
  {/if}
</div>

<style>
  .sub-content { width: 100%; min-width: 0; }
  .muted { color: var(--pw-ink-soft); font: 12px Inter; padding: 20px 0; }
  .err { color: #c0392b; font: 12px Inter; padding: 12px; background: rgba(192,57,43,0.08); border: 1px solid #c0392b; }
  .stat-strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin-bottom: 16px; }
  .stat { background: var(--pw-surface); border: 1px solid var(--pw-border); padding: 10px 12px; }
  .stat-k { font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft); margin-bottom: 4px; }
  .stat-v { font: 600 18px Inter; color: var(--pw-ink); }
  .section-title { font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft); margin: 16px 0 8px; }
  .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface); border: 1px solid var(--pw-border); font-size: 12px; }
  .tbl th { text-align: left; padding: 8px 12px; font: 600 10px Inter; text-transform: uppercase; background: var(--pw-bg-alt); border-bottom: 1px solid var(--pw-border); }
  .tbl td { padding: 8px 12px; border-top: 1px solid var(--pw-border); vertical-align: top; }
  .toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
  .btn { padding: 6px 14px; font: 600 11px Inter; letter-spacing: 0.04em; background: var(--pw-accent); color: #fff; border: none; cursor: pointer; }
  .btn-sm { padding: 4px 10px; font: 600 10px Inter; margin-right: 4px; border: 1px solid var(--pw-border); background: var(--pw-bg-alt); cursor: pointer; }
  .btn-sm.ok { color: #1a7f37; border-color: #1a7f37; }
  .btn-sm.no { color: #c0392b; border-color: #c0392b; }
  .form { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)) auto; gap: 8px; margin-bottom: 12px; padding: 12px; background: var(--pw-bg-alt); border: 1px solid var(--pw-border); }
  .form input, .form select { padding: 6px 10px; border: 1px solid var(--pw-border); background: var(--pw-surface); font: 12px Inter; }
</style>
