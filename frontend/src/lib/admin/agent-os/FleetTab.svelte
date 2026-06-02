<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 type Agent = {
 id: string;
 name?: string;
 purpose?: string;
 base_agent?: string;
 usage_count?: number;
 success_rate?: number;
 scoped_skills?: string[];
 scoped_tools?: string[];
 extra_instructions?: string;
 spec?: any;
 };
 type Run = { id?: string; question?: string; status?: string; duration_ms?: number; created_at?: string };

 let agents = $state<Agent[]>([]);
 let loading = $state(true);
 let selected = $state<Agent | null>(null);
 let runs = $state<Run[]>([]);
 let showNew = $state(false);
 let isSuper = $state(false);

 // ── Minion registry (all background/dream/autosim/extended/janitor agents) ──
 type Minion = {
 id?: string;
 name: string;
 description?: string;
 handler_kind?: string;
 trigger_model?: string; // sync_chat | minion_queue | cron | event_hook
 llm_model?: string; // CHAT_MODEL | DEEP_MODEL | LITE_MODEL | none
 cost_per_invocation?: number;
 status?: string;
 };
 type CatKey = 'core' | 'specialist' | 'background' | 'upload' | 'routing' | 'extended' | 'dream' | 'autosim' | 'sim' | 'learning' | 'janitor' | 'investment';
 let registry = $state<Partial<Record<CatKey, Minion[]>>>({});
 let registryTotal = $state(0);
 let minionStats = $state<Record<string, { queued: number; running: number; done_24h: number; failed_24h: number; last_done_at?: string | null; avg_duration_s?: number | null }>>({});
 let registryError = $state<string>('');
 let registryPollId: any = null;
 // Per-section collapse state (default expanded for "new" categories)
 let openCat = $state<Record<CatKey, boolean>>({
 core: false, specialist: false, background: false, upload: false, routing: false,
 extended: true, dream: true, autosim: true, sim: true, learning: true, janitor: true,
 investment: false
 });

 // Category metadata. Render labels include emoji prefix so headers read:
 // " Dream", " AutoSim", " Sim Lab", " Learning", " Janitor", " Extended".
 const CAT_META: Record<CatKey, { emoji: string; label: string }> = {
 core: { emoji: '', label: 'Core' },
 specialist: { emoji: '', label: 'Specialists' },
 background: { emoji: '', label: 'Background' },
 upload: { emoji: '', label: 'Upload' },
 routing: { emoji: '', label: 'Routing' },
 extended: { emoji: '', label: ' Extended' },
 dream: { emoji: '', label: ' Dream' },
 autosim: { emoji: '', label: ' AutoSim' },
 sim: { emoji: '', label: ' Sim Lab' },
 learning: { emoji: '', label: ' Learning' },
 janitor: { emoji: '', label: ' Janitor' },
 investment: { emoji: '', label: ' Investment' },
 };
 const CAT_RENDER_ORDER: CatKey[] = ['core', 'specialist', 'extended', 'investment', 'background', 'upload', 'routing', 'dream', 'autosim', 'sim', 'learning', 'janitor'];

 function _llmKind(m: string | undefined): 'DEEP_MODEL' | 'CHAT_MODEL' | 'LITE_MODEL' | 'none' {
 const v = (m || '').toUpperCase();
 if (!v || v === 'NONE') return 'none';
 if (v.includes('DEEP')) return 'DEEP_MODEL';
 if (v.includes('LITE')) return 'LITE_MODEL';
 if (v.includes('CHAT')) return 'CHAT_MODEL';
 return 'CHAT_MODEL';
 }
 function _llmBg(k: string) {
 return k === 'DEEP_MODEL' ? 'rgba(167,139,250,0.18)'
 : k === 'CHAT_MODEL' ? 'rgba(249,163,116,0.18)'
 : k === 'LITE_MODEL' ? 'rgba(58,141,255,0.18)'
 : 'rgba(136,136,136,0.14)';
 }
 function _llmFg(k: string) {
 return k === 'DEEP_MODEL' ? '#a78bfa'
 : k === 'CHAT_MODEL' ? '#f9a374'
 : k === 'LITE_MODEL' ? '#3a8dff'
 : '#888';
 }
 function _relTime(iso?: string | null): string {
 if (!iso) return '—';
 try {
 const t = new Date(iso).getTime();
 const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
 if (s < 60) return s + 's ago';
 if (s < 3600) return Math.floor(s / 60) + 'm ago';
 if (s < 86400) return Math.floor(s / 3600) + 'h ago';
 return Math.floor(s / 86400) + 'd ago';
 } catch { return '—'; }
 }
 function _statusGlyph(s?: string): string {
 if (!s) return '◐';
 const v = s.toLowerCase();
 if (v === 'active' || v === 'running' || v === 'ok') return '●';
 if (v === 'error' || v === 'failed') return '';
 if (v === 'idle' || v === 'needs_setup') return '○';
 return '◐';
 }
 function _expectedCallsPerDay(triggerModel?: string): number {
 const t = (triggerModel || '').toLowerCase();
 if (t === 'sync_chat') return 20;
 if (t === 'event_hook') return 10;
 if (t === 'minion_queue') return 4;
 if (t === 'cron') return 1;
 return 1;
 }
 const _estCostPerDay = $derived.by(() => {
 let total = 0;
 for (const cat of Object.keys(registry) as CatKey[]) {
 for (const m of registry[cat] || []) {
 const c = Number(m.cost_per_invocation || 0);
 total += c * _expectedCallsPerDay(m.trigger_model);
 }
 }
 return total;
 });
 const _fleetCounts = $derived.by(() => {
 let active = 0, idle = 0, err = 0;
 for (const cat of Object.keys(registry) as CatKey[]) {
 for (const m of registry[cat] || []) {
 const s = (m.status || '').toLowerCase();
 if (s === 'error' || s === 'failed') err++;
 else if (s === 'idle' || s === 'needs_setup') idle++;
 else active++;
 }
 }
 return { active, idle, err };
 });

 let newAgent = $state({ name: '', purpose: '', base_agent: 'analyst', scoped_skills: [] as string[], scoped_tools: [] as string[], extra_instructions: '' });
 let availableSkills = $state<{ id: string; name: string }[]>([]);
 const TOOL_OPTIONS = [
 'sql_query', 'introspect_schema', 'search_all', 'load_context',
 'create_dashboard', 'auto_visualize', 'feature_importance',
 'detect_anomalies_ml', 'predict', 'classify', 'cluster', 'decompose'
 ];

 const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);
 const authHeaders = (): Record<string, string> => {
 const t = token();
 return t ? { Authorization: `Bearer ${t}` } : {};
 };

 async function load() {
 loading = true;
 try {
 const r = await fetch('/api/custom-agents?limit=200', { headers: authHeaders() });
 if (r.ok) {
 const j = await r.json();
 agents = Array.isArray(j) ? j : j.agents || j.items || [];
 }
 } catch {}
 try {
 const r = await fetch('/api/auth/check', { headers: authHeaders() });
 if (r.ok) { const j = await r.json(); isSuper = !!(j.is_super_admin || j.role === 'super_admin'); }
 } catch {}
 loading = false;
 }

 async function openAgent(a: Agent) {
 selected = a;
 runs = [];
 try {
 const r = await fetch(`/api/custom-agents/${a.id}/runs?limit=20`, { headers: authHeaders() });
 if (r.ok) { const j = await r.json(); runs = Array.isArray(j) ? j : j.runs || j.items || []; }
 } catch {}
 }

 async function deprecate() {
 if (!selected) return;
 if (!confirm('Deprecate this agent?')) return;
 try {
 const r = await fetch(`/api/custom-agents/${selected.id}/deprecate`, { method: 'POST', headers: authHeaders() });
 if (r.ok) { selected = null; load(); }
 } catch {}
 }

 async function promoteGlobal() {
 if (!selected) return;
 try {
 const r = await fetch(`/api/custom-agents/${selected.id}/promote-global`, { method: 'POST', headers: authHeaders() });
 if (r.ok) { load(); }
 } catch {}
 }

 async function openNew() {
 showNew = true;
 try {
 const r = await fetch('/api/skills', { headers: authHeaders() });
 if (r.ok) {
 const j = await r.json();
 availableSkills = (Array.isArray(j) ? j : j.skills || j.items || []).map((s: any) => ({ id: s.id || s.name, name: s.name || s.id }));
 }
 } catch {}
 }

 async function createAgent() {
 if (!newAgent.name.trim()) return;
 try {
 const r = await fetch('/api/custom-agents', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', ...authHeaders() },
 body: JSON.stringify(newAgent)
 });
 if (r.ok) {
 showNew = false;
 newAgent = { name: '', purpose: '', base_agent: 'analyst', scoped_skills: [], scoped_tools: [], extra_instructions: '' };
 load();
 }
 } catch {}
 }

 async function loadRegistry() {
 try {
 const r = await fetch('/api/projects/agents/registry', { headers: authHeaders() });
 if (r.ok) {
 const j = await r.json();
 registry = (j.categories || {}) as any;
 registryTotal = Number(j.total || 0);
 registryError = '';
 // Auto-expand investment section if any agent active in last 24h
 try {
 const inv = (registry.investment || []) as Minion[];
 const cutoff = Date.now() - 24 * 60 * 60 * 1000;
 const anyActive = inv.some(m => {
 const t = (m as any).last_seen_at;
 return t && new Date(t).getTime() > cutoff;
 });
 if (anyActive) openCat.investment = true;
 } catch {}
 } else if (r.status === 404 || r.status === 500) {
 registryError = 'Registry empty — apply migration 073 to populate.';
 } else if (r.status === 403) {
 registryError = ''; // hide for non-super
 }
 } catch {
 // network err — leave existing state
 }
 try {
 const r = await fetch('/api/projects/agents/minions/stats', { headers: authHeaders() });
 if (r.ok) {
 const j = await r.json();
 const map: typeof minionStats = {};
 for (const s of (j.stats || []) as any[]) {
 const k = s.kind || s.name || s.id;
 if (!k) continue;
 if (!map[k]) map[k] = { queued: 0, running: 0, done_24h: 0, failed_24h: 0, last_done_at: null, avg_duration_s: null };
 const status = (s.status || '').toLowerCase();
 const n = Number(s.n || 0);
 if (status === 'queued' || status === 'pending') map[k].queued += n;
 else if (status === 'running') map[k].running += n;
 else if (status === 'done' || status === 'success' || status === 'ok') map[k].done_24h += n;
 else if (status === 'failed' || status === 'error') map[k].failed_24h += n;
 if (s.last_finished && (!map[k].last_done_at || s.last_finished > (map[k].last_done_at || ''))) map[k].last_done_at = s.last_finished;
 if (s.avg_duration_s != null) map[k].avg_duration_s = Number(s.avg_duration_s);
 }
 minionStats = map;
 }
 } catch {}
 }

 function initials(n?: string) { return (n || '?').split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() || '').join('') || '?'; }
 function fmtDate(s?: string) { if (!s) return '—'; try { return new Date(s).toLocaleString(); } catch { return s; } }

 function toggle(list: string[], v: string): string[] {
 return list.includes(v) ? list.filter(x => x !== v) : [...list, v];
 }

 onMount(() => {
 load();
 loadRegistry();
 registryPollId = setInterval(loadRegistry, 60000);
 return () => { if (registryPollId) clearInterval(registryPollId); };
 });
</script>

<div class="wrap">
  <!-- ── FLEET REGISTRY (all agents across categories) ── -->
  {#if registryTotal > 0 || Object.keys(registry).length > 0}
    <div class="fleet-summary">
      <span class="fs-pill"><span class="muted">Total:</span> <b>{registryTotal}</b> agents</span>
      <span class="fs-pill fs-ok"><span class="muted">Active:</span> <b>{_fleetCounts.active}</b></span>
      <span class="fs-pill"><span class="muted">Idle:</span> <b>{_fleetCounts.idle}</b></span>
      <span class="fs-pill fs-err"><span class="muted">Error:</span> <b>{_fleetCounts.err}</b></span>
      <span class="fs-pill fs-cost"><span class="muted">Est cost/day:</span> <b>${_estCostPerDay.toFixed(2)}</b></span>
      <span class="muted" style="margin-left:auto; font-size:11px;">auto-refresh 60s</span>
      <button class="btn-ghost" onclick={loadRegistry}>↻</button>
    </div>

    {#each CAT_RENDER_ORDER as catKey}
      {@const items = (registry[catKey] || []) as Minion[]}
      {#if items.length > 0}
        {@const meta = CAT_META[catKey]}
        {@const catCost = items.reduce((s, m) => s + Number(m.cost_per_invocation || 0) * _expectedCallsPerDay(m.trigger_model), 0)}
        {@const catActive = items.filter(m => { const s = (m.status || '').toLowerCase(); return s !== 'error' && s !== 'failed' && s !== 'idle' && s !== 'needs_setup'; }).length}
        <div class="fleet-minion-section">
          <button
            class="fleet-minion-header"
            onclick={() => (openCat[catKey] = !openCat[catKey])}
            aria-expanded={openCat[catKey]}
          >
            <span class="chev">{openCat[catKey] ? '▾' : '▸'}</span>
            <span class="cat-label">{meta.label.toUpperCase()}</span>
            <span class="muted">({items.length})</span>
            <span class="totals muted">●{catActive} active · ${catCost.toFixed(2)}/day est</span>
          </button>
          {#if openCat[catKey]}
            <div class="fleet-minion-grid">
              {#each items as m}
                {@const stats = minionStats[m.id || m.name || ''] || { queued: 0, running: 0, done_24h: 0, failed_24h: 0, last_done_at: null }}
                {@const llmK = _llmKind(m.llm_model)}
                {@const showStats = (m.trigger_model || '') === 'minion_queue'}
                <div class="fleet-minion-card" title={(m.description || '') + (m.handler_kind ? ' · handler: ' + m.handler_kind : '')}>
                  <div class="fmc-head">
                    <span class="fmc-glyph" data-status={(m.status || '').toLowerCase()}>{_statusGlyph(m.status)}</span>
                    <span class="fmc-name">{m.name}</span>
                  </div>
                  <div class="fmc-desc">{m.description || '—'}</div>
                  <div class="fmc-badges">
                    <span class="fmc-badge fmc-trig">{m.trigger_model || 'minion_queue'}</span>
                    <span class="fmc-badge" style="background:{_llmBg(llmK)}; color:{_llmFg(llmK)};">{llmK}</span>
                    {#if m.cost_per_invocation != null}
                      <span class="fmc-badge fmc-cost">~${Number(m.cost_per_invocation).toFixed(2)}</span>
                    {/if}
                  </div>
                  {#if showStats}
                    <div class="fmc-stats">
                      <span class="st-q" title="queued">●{stats.queued}</span>
                      <span class="st-r" title="running">⟲{stats.running}</span>
                      <span class="st-d" title="done 24h"><Icon name="check" size={14} />{stats.done_24h}</span>
                      <span class="st-f" title="failed 24h"><Icon name="x" size={14} />{stats.failed_24h}</span>
                      <span class="muted st-last">Last: {_relTime(stats.last_done_at)}</span>
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    {/each}
  {:else if registryError}
    <div class="empty">{registryError}</div>
  {/if}

  <div class="toolbar">
    <div class="muted">{agents.length} sub-agent{agents.length === 1 ? '' : 's'}</div>
    <div class="row">
      <button class="btn-ghost" onclick={load}>↻ Refresh</button>
      <button class="btn-primary" onclick={openNew}>+ New sub-agent</button>
    </div>
  </div>

  {#if loading}
    <div class="empty">Loading…</div>
  {:else if !agents.length}
    <div class="empty">No sub-agents yet.</div>
  {:else}
    <div class="grid">
      {#each agents as a}
        <button class="card" onclick={() => openAgent(a)}>
          <div class="avatar">{initials(a.name)}</div>
          <div class="cname">{a.name || a.id}</div>
          <div class="cpurpose">{a.purpose || '—'}</div>
          <div class="chips">
            {#if a.base_agent}<span class="chip">{a.base_agent}</span>{/if}
            {#if typeof a.usage_count === 'number'}<span class="chip">{a.usage_count} runs</span>{/if}
            {#if typeof a.success_rate === 'number'}<span class="chip chip-ok">{(a.success_rate * 100).toFixed(0)}% ok</span>{/if}
          </div>
          <div class="meta muted">{(a.scoped_skills?.length || 0)} skills · {(a.scoped_tools?.length || 0)} tools</div>
        </button>
      {/each}
    </div>
  {/if}
</div>

{#if selected}
  <div class="backdrop" role="button" tabindex="0" aria-label="Close drawer" onclick={() => (selected = null)} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ' || e.key === 'Escape') { e.preventDefault(); selected = null; } }}></div>
  <aside class="drawer">
    <header>
      <h3>{selected.name || selected.id}</h3>
      <button class="btn-ghost" onclick={() => (selected = null)}><Icon name="x" size={14} /></button>
    </header>
    <section>
      <h4>Spec</h4>
      <pre class="spec">{JSON.stringify(selected.spec || selected, null, 2)}</pre>
    </section>
    <section>
      <h4>Recent runs</h4>
      {#if !runs.length}
        <div class="muted">No runs yet.</div>
      {:else}
        <table class="tbl">
          <thead><tr><th>ID</th><th>STATUS</th><th>DURATION</th><th>WHEN</th></tr></thead>
          <tbody>
            {#each runs as r}
              <tr>
                <td class="mono">{(r.id || '').slice(0, 12)}</td>
                <td>{r.status || '—'}</td>
                <td>{r.duration_ms ? r.duration_ms + 'ms' : '—'}</td>
                <td class="muted">{fmtDate(r.created_at)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
    <footer>
      <button class="btn-danger" onclick={deprecate}>Deprecate</button>
      {#if isSuper}<button class="btn-primary" onclick={promoteGlobal}>Promote global</button>{/if}
    </footer>
  </aside>
{/if}

{#if showNew}
  <div class="backdrop" role="button" tabindex="0" aria-label="Close modal" onclick={() => (showNew = false)} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ' || e.key === 'Escape') { e.preventDefault(); showNew = false; } }}></div>
  <div class="modal-center">
    <h3>New sub-agent</h3>
    <label>Name<input bind:value={newAgent.name} placeholder="e.g. SalesScout" /></label>
    <label>Purpose<input bind:value={newAgent.purpose} placeholder="What does it do?" /></label>
    <label>Base agent
      <select bind:value={newAgent.base_agent}>
        <option value="analyst">analyst</option>
        <option value="engineer">engineer</option>
        <option value="researcher">researcher</option>
      </select>
    </label>
    <label>Scoped skills
      <div class="multi">
        {#each availableSkills as s}
          <button type="button" class="pchip" class:on={newAgent.scoped_skills.includes(s.id)}
            onclick={() => (newAgent.scoped_skills = toggle(newAgent.scoped_skills, s.id))}>{s.name}</button>
        {/each}
        {#if !availableSkills.length}<span class="muted">No skills available.</span>{/if}
      </div>
    </label>
    <label>Scoped tools
      <div class="multi">
        {#each TOOL_OPTIONS as t}
          <button type="button" class="pchip" class:on={newAgent.scoped_tools.includes(t)}
            onclick={() => (newAgent.scoped_tools = toggle(newAgent.scoped_tools, t))}>{t}</button>
        {/each}
      </div>
    </label>
    <label>Extra instructions<textarea bind:value={newAgent.extra_instructions} rows="3"></textarea></label>
    <div class="row end">
      <button class="btn-ghost" onclick={() => (showNew = false)}>Cancel</button>
      <button class="btn-primary" onclick={createAgent} disabled={!newAgent.name.trim()}>Create</button>
    </div>
  </div>
{/if}

<style>
 .wrap { display: flex; flex-direction: column; gap: 12px; }
 .toolbar { display: flex; justify-content: space-between; align-items: center; }
 .row { display: flex; gap: 8px; }
 .row.end { justify-content: flex-end; }
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; background: var(--pw-surface, #faf9f5); }
 .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
 .card { text-align: left; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 14px; cursor: pointer; display: flex; flex-direction: column; gap: 8px; }
 .card:hover { border-color: var(--pw-accent, #c96342); }
 .avatar { width: 36px; height: 36px; border-radius: 0; background: var(--pw-accent, #c96342); color: white; display: flex; align-items: center; justify-content: center; font: 600 13px Inter; }
 .cname { font: 600 14px Inter; color: var(--pw-ink, #2c2a26); }
 .cpurpose { font-size: 12px; color: var(--pw-ink-soft, #87837a); line-height: 1.4; min-height: 32px; }
 .chips { display: flex; flex-wrap: wrap; gap: 4px; }
 .chip { display: inline-block; padding: 2px 8px; border-radius: 0; font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.04em; background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink-soft, #87837a); }
 .chip-ok { background: rgba(22, 163, 74, 0.14); color: #15803d; }
 .meta { font-size: 11px; }
 .btn-ghost { background: none; border: 1px solid var(--pw-border, #e7e3da); padding: 6px 10px; font-size: 12px; cursor: pointer; border-radius: 0; color: var(--pw-ink, #2c2a26); }
 .btn-primary { background: var(--pw-accent, #c96342); color: white; border: none; padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; border-radius: 0; }
 .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
 .btn-danger { background: #dc2626; color: white; border: none; padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; border-radius: 0; }
 .backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.2); z-index: 50; }
 .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: 480px; background: var(--pw-surface, #faf9f5); border-left: 1px solid var(--pw-border, #e7e3da); z-index: 51; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
 .drawer header { display: flex; justify-content: space-between; align-items: center; }
 .drawer h3 { margin: 0; font: 600 18px 'Source Serif 4', Georgia, serif; }
 .drawer h4 { margin: 0 0 8px; font: 600 12px Inter; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .drawer footer { display: flex; gap: 8px; padding-top: 8px; border-top: 1px solid var(--pw-border, #e7e3da); }
 .spec { background: var(--pw-bg-alt, #f1ede4); padding: 12px; border-radius: 0; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 11px; white-space: pre-wrap; max-height: 240px; overflow: auto; margin: 0; }
 .tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
 .tbl th { text-align: left; padding: 6px 8px; background: var(--pw-bg-alt, #f1ede4); font: 600 10px Inter; text-transform: uppercase; color: var(--pw-ink-soft, #87837a); }
 .tbl td { padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #e7e3da); }
 .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
 .modal-center { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 520px; max-height: 90vh; overflow-y: auto; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 8px; padding: 20px; z-index: 51; display: flex; flex-direction: column; gap: 12px; box-shadow: 0 12px 32px rgba(0,0,0,0.15); }
 .modal-center h3 { margin: 0; font: 600 18px 'Source Serif 4', Georgia, serif; }
 .modal-center label { display: flex; flex-direction: column; gap: 4px; font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-ink-soft, #87837a); }
 .modal-center input, .modal-center select, .modal-center textarea { padding: 8px; font: 13px Inter; border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; background: white; }
 .multi { display: flex; flex-wrap: wrap; gap: 4px; }
 .pchip { background: var(--pw-bg-alt, #f1ede4); border: 1px solid var(--pw-border, #e7e3da); padding: 4px 8px; border-radius: 0; font-size: 11px; cursor: pointer; color: var(--pw-ink, #2c2a26); }
 .pchip.on { background: var(--pw-accent, #c96342); color: white; border-color: var(--pw-accent, #c96342); }

 /* ── Fleet minion registry ── */
 .fleet-summary { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 10px 12px; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; margin-bottom: 4px; font-size: 12px; }
 .fs-pill { padding: 4px 10px; border-radius: 0; background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink, #2c2a26); font-size: 12px; }
 .fs-pill b { font-weight: 700; }
 .fs-pill.fs-ok { background: rgba(16,185,129,0.12); color: #047857; }
 .fs-pill.fs-err { background: rgba(255,64,64,0.12); color: #b91c1c; }
 .fs-pill.fs-cost { background: rgba(249,163,116,0.18); color: #b45309; }
 .fleet-minion-section { display: flex; flex-direction: column; gap: 8px; }
 .fleet-minion-header { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: var(--pw-bg-alt, #f1ede4); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; cursor: pointer; font: 600 12px Inter; text-align: left; color: var(--pw-ink, #2c2a26); }
 .fleet-minion-header:hover { border-color: var(--pw-accent, #c96342); }
 .fleet-minion-header .chev { color: var(--pw-accent, #c96342); width: 14px; }
 .fleet-minion-header .emo { font-size: 14px; }
 .fleet-minion-header .cat-label { font-weight: 700; letter-spacing: 0.04em; }
 .fleet-minion-header .totals { margin-left: auto; font-weight: 500; font-size: 11px; }
 .fleet-minion-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px; }
 .fleet-minion-card { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
 .fleet-minion-card:hover { border-color: var(--pw-accent, #c96342); }
 .fmc-head { display: flex; align-items: center; gap: 6px; }
 .fmc-glyph { font-size: 12px; color: #3a8dff; }
 .fmc-glyph[data-status='active'], .fmc-glyph[data-status='running'], .fmc-glyph[data-status='ok'] { color: #10b981; }
 .fmc-glyph[data-status='error'], .fmc-glyph[data-status='failed'] { color: #ff4040; }
 .fmc-glyph[data-status='idle'], .fmc-glyph[data-status='needs_setup'] { color: #888; }
 .fmc-name { font: 600 12.5px Inter; color: var(--pw-ink, #2c2a26); }
 .fmc-desc { font-size: 11px; color: var(--pw-ink-soft, #87837a); line-height: 1.35; min-height: 28px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
 .fmc-badges { display: flex; flex-wrap: wrap; gap: 4px; }
 .fmc-badge { font: 700 9.5px Inter; padding: 2px 6px; border-radius: 0; letter-spacing: 0.04em; text-transform: uppercase; }
 .fmc-badge.fmc-trig { background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink-soft, #87837a); }
 .fmc-badge.fmc-cost { background: rgba(249,163,116,0.18); color: #b45309; }
 .fmc-stats { display: flex; flex-wrap: wrap; gap: 8px; font-size: 11px; padding-top: 4px; border-top: 1px dashed var(--pw-border, #e7e3da); }
 .fmc-stats .st-q { color: #cc7a00; }
 .fmc-stats .st-r { color: #3a8dff; }
 .fmc-stats .st-d { color: #10b981; }
 .fmc-stats .st-f { color: #ff4040; }
 .fmc-stats .st-last { font-size: 10.5px; }
</style>
