<script lang="ts">
  import { dashFetch } from '$lib/api';
  import { onMount } from 'svelte';

  type SessionRow = {
    session_id: string;
    user_id: number | null;
    username: string | null;
    project_slug: string | null;
    first_message: string | null;
    msg_count: number;
    created_at: string | null;
    updated_at: string | null;
    total_cost_usd: number;
    error_count: number;
  };

  type SpanRow = {
    id: number;
    trace_id: string;
    parent_id: string | null;
    name: string;
    kind: string;
    status: string;
    duration_ms: number | null;
    cost_usd: number | null;
    tokens: number | null;
    error: string | null;
    started_at: string | null;
    finished_at: string | null;
    meta: any;
  };

  type Summary = {
    project_slug: string;
    days: number;
    session_count: number;
    total_cost_usd: number;
    error_rate: number;
    top_tables: { table: string; count: number }[];
    top_skills: { skill_id: string; count: number }[];
    top_users: { user_id: number; username: string | null; session_count: number; cost_usd: number }[];
    top_tools: { tool: string; count: number }[];
    warning: string | null;
  };

  type SessionDetail = {
    session: SessionRow;
    timeline: SpanRow[];
    tables_touched: { table: string; count: number }[];
    skills_used: { skill_id: string; count: number; success_count: number }[];
    tools_called: { tool: string; count: number; error_count: number }[];
    rls_policies_fired: { policy: string; count: number }[];
    errors: { span_name: string; error: string | null; started_at: string | null }[];
    total_cost_usd: number;
    total_tokens: number;
    warning: string | null;
  };

  type UserAudit = {
    user_id: number;
    username: string | null;
    days: number;
    session_count: number;
    project_count: number;
    total_cost_usd: number;
    recent_sessions: SessionRow[];
    top_projects: { project_slug: string; session_count: number; cost_usd: number }[];
    top_tools: { tool: string; count: number }[];
    warning: string | null;
  };

  // ── State ────────────────────────────────────────────────────────────────
  let projectSlug = $state('');
  let activeTab = $state<'sessions' | 'summary' | 'user'>('sessions');
  // privacy: dashboards show keyword chips, never raw chat text
  const _STOPW = new Set('the a an and or to in on for is are how what which this that with at by from as can my your you we they it do does of'.split(' '));
  function kw(s: string | null, n = 5): string[] {
    const out: string[] = []; const seen = new Set<string>();
    for (const m of (s || '').toLowerCase().match(/[a-z][a-z0-9\-]{2,}|[က-႟]+/g) || []) {
      if (_STOPW.has(m) || seen.has(m)) continue; seen.add(m); out.push(m); if (out.length >= n) break;
    }
    return out;
  }
  let days = $state(7);

  let sessions = $state<SessionRow[]>([]);
  let sessionsWarning = $state<string | null>(null);
  let sessionsLoading = $state(false);

  let expandedSession = $state<string | null>(null);
  let sessionDetail = $state<SessionDetail | null>(null);
  let detailLoading = $state(false);

  let summary = $state<Summary | null>(null);
  let summaryLoading = $state(false);

  let userIdInput = $state('');
  let userAudit = $state<UserAudit | null>(null);
  let userLoading = $state(false);

  // ── Loaders ──────────────────────────────────────────────────────────────
  async function loadSessions() {
    if (!projectSlug) {
      sessions = [];
      sessionsWarning = 'Enter a project slug';
      return;
    }
    sessionsLoading = true;
    sessionsWarning = null;
    try {
      const r = await dashFetch(`/api/scope-audit/sessions?project_slug=${encodeURIComponent(projectSlug)}&limit=50`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      sessions = data.sessions || [];
      sessionsWarning = data.warning;
    } catch (e: any) {
      sessionsWarning = e?.message || String(e);
      sessions = [];
    } finally {
      sessionsLoading = false;
    }
  }

  async function loadSessionDetail(sid: string) {
    if (expandedSession === sid) {
      expandedSession = null;
      sessionDetail = null;
      return;
    }
    expandedSession = sid;
    sessionDetail = null;
    detailLoading = true;
    try {
      const r = await dashFetch(`/api/scope-audit/session/${encodeURIComponent(sid)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      sessionDetail = await r.json();
    } catch (e: any) {
      sessionDetail = {
        session: { session_id: sid } as any,
        timeline: [], tables_touched: [], skills_used: [], tools_called: [],
        rls_policies_fired: [], errors: [], total_cost_usd: 0, total_tokens: 0,
        warning: e?.message || String(e),
      };
    } finally {
      detailLoading = false;
    }
  }

  async function loadSummary() {
    if (!projectSlug) return;
    summaryLoading = true;
    try {
      const r = await dashFetch(`/api/scope-audit/summary?project_slug=${encodeURIComponent(projectSlug)}&days=${days}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      summary = await r.json();
    } catch (e: any) {
      summary = {
        project_slug: projectSlug, days, session_count: 0, total_cost_usd: 0, error_rate: 0,
        top_tables: [], top_skills: [], top_users: [], top_tools: [],
        warning: e?.message || String(e),
      };
    } finally {
      summaryLoading = false;
    }
  }

  async function loadUser() {
    const uid = parseInt(userIdInput, 10);
    if (!uid) return;
    userLoading = true;
    try {
      const r = await dashFetch(`/api/scope-audit/user/${uid}?days=${30}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      userAudit = await r.json();
    } catch (e: any) {
      userAudit = {
        user_id: uid, username: null, days: 30, session_count: 0,
        project_count: 0, total_cost_usd: 0, recent_sessions: [],
        top_projects: [], top_tools: [], warning: e?.message || String(e),
      };
    } finally {
      userLoading = false;
    }
  }

  // ── Derived: chart bar max for top-N visuals ─────────────────────────────
  function maxCount(arr: { count: number }[]): number {
    if (!Array.isArray(arr) || arr.length === 0) return 1;
    return Math.max(1, ...arr.map(x => x.count || 0));
  }

  function fmtCost(v: number | null | undefined): string {
    if (v == null) return '—';
    if (v < 0.01) return `$${v.toFixed(4)}`;
    return `$${v.toFixed(2)}`;
  }

  function fmtMs(v: number | null | undefined): string {
    if (v == null) return '—';
    if (v < 1000) return `${v}ms`;
    return `${(v / 1000).toFixed(2)}s`;
  }

  function fmtTime(iso: string | null | undefined): string {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  function tabChange(t: 'sessions' | 'summary' | 'user') {
    activeTab = t;
    if (t === 'summary' && !summary && projectSlug) loadSummary();
  }

  let singleAgent = $state(false);

  onMount(async () => {
    // single-tenant: auto-fill the locked slug + load — no manual entry needed
    try {
      const f = await fetch('/api/flags').then((r) => (r.ok ? r.json() : null));
      if (f?.single_agent && f.locked_slug) {
        singleAgent = true;
        projectSlug = f.locked_slug;
        loadSessions();
      }
    } catch { /* ignore */ }
  });
</script>

<div class="sa-shell">
  <header class="sa-head">
    <div>
      <h1>Chat Scope Audit</h1>
      <p class="muted">Per-session view: tables touched · skills applied · tools called · cost · RLS · errors</p>
    </div>
    <div class="ctrls">
      {#if !singleAgent}
        <input
          type="text"
          placeholder="project slug"
          bind:value={projectSlug}
          onkeydown={(e) => { if (e.key === 'Enter') { if (activeTab === 'sessions') loadSessions(); else if (activeTab === 'summary') loadSummary(); } }}
        />
      {/if}
      <button class="primary" onclick={() => { if (activeTab === 'sessions') loadSessions(); else if (activeTab === 'summary') loadSummary(); }}>
        {singleAgent ? 'Refresh' : 'Load'}
      </button>
    </div>
  </header>

  <nav class="tabs">
    <button class:active={activeTab === 'sessions'} onclick={() => tabChange('sessions')}>Recent Sessions</button>
    <button class:active={activeTab === 'summary'} onclick={() => tabChange('summary')}>Aggregate Summary</button>
    <button class:active={activeTab === 'user'} onclick={() => tabChange('user')}>By User</button>
  </nav>

  <!-- ── SESSIONS TAB ─────────────────────────────────────────────── -->
  {#if activeTab === 'sessions'}
    {#if sessionsWarning}
      <div class="banner">{sessionsWarning}</div>
    {/if}
    {#if sessionsLoading}
      <p class="muted">Loading…</p>
    {:else if sessions.length === 0}
      <p class="muted small">No sessions found{projectSlug ? ` for ${projectSlug}` : ''}.</p>
    {:else}
      <section class="card">
        <table class="sa-table">
          <thead>
            <tr>
              <th></th>
              <th>Session</th>
              <th>User</th>
              <th>First message</th>
              <th>Msgs</th>
              <th>Cost</th>
              <th>Errors</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {#each sessions as s}
              <tr class="row" onclick={() => loadSessionDetail(s.session_id)}>
                <td><span class="chev">{expandedSession === s.session_id ? '▼' : '▶'}</span></td>
                <td><code class="mono">{s.session_id.slice(0, 16)}…</code></td>
                <td>{s.username || `#${s.user_id ?? '?'}`}</td>
                <td class="msg">{#each kw(s.first_message) as k}<span class="kwc">{k}</span>{/each}{#if !kw(s.first_message).length}—{/if}</td>
                <td>{s.msg_count}</td>
                <td>{fmtCost(s.total_cost_usd)}</td>
                <td class:err={s.error_count > 0}>{s.error_count}</td>
                <td class="small muted">{fmtTime(s.updated_at)}</td>
              </tr>
              {#if expandedSession === s.session_id}
                <tr class="detail-row">
                  <td colspan="8">
                    {#if detailLoading}
                      <p class="muted small">Loading audit…</p>
                    {:else if sessionDetail}
                      {#if sessionDetail.warning}
                        <div class="banner warn">{sessionDetail.warning}</div>
                      {/if}

                      <div class="kpi-strip">
                        <div class="kpi">
                          <div class="kpi-label">Cost</div>
                          <div class="kpi-val">{fmtCost(sessionDetail.total_cost_usd)}</div>
                        </div>
                        <div class="kpi">
                          <div class="kpi-label">Tokens</div>
                          <div class="kpi-val">{(sessionDetail.total_tokens || 0).toLocaleString()}</div>
                        </div>
                        <div class="kpi">
                          <div class="kpi-label">Spans</div>
                          <div class="kpi-val">{sessionDetail.timeline.length}</div>
                        </div>
                        <div class="kpi">
                          <div class="kpi-label">Errors</div>
                          <div class="kpi-val" class:err={sessionDetail.errors.length > 0}>{sessionDetail.errors.length}</div>
                        </div>
                      </div>

                      <div class="audit-grid">
                        <!-- TIMELINE -->
                        <div class="audit-block timeline">
                          <h3>Timeline</h3>
                          {#if sessionDetail.timeline.length === 0}
                            <p class="muted small">No spans recorded.</p>
                          {:else}
                            <ul class="span-list">
                              {#each sessionDetail.timeline as span}
                                <li class="span" class:span-error={span.status === 'error'}>
                                  <div class="span-row">
                                    <span class="span-kind kind-{span.kind}">{span.kind}</span>
                                    <span class="span-name">{span.name}</span>
                                    <span class="span-dur small muted">{fmtMs(span.duration_ms)}</span>
                                    {#if span.cost_usd != null}
                                      <span class="span-cost small">{fmtCost(span.cost_usd)}</span>
                                    {/if}
                                  </div>
                                  {#if span.error}
                                    <div class="span-err small">{span.error}</div>
                                  {/if}
                                </li>
                              {/each}
                            </ul>
                          {/if}
                        </div>

                        <!-- SIDE PANELS -->
                        <div class="audit-block">
                          <h3>Tables touched</h3>
                          {#if sessionDetail.tables_touched.length === 0}
                            <p class="muted small">No SELECT'd tables detected.</p>
                          {:else}
                            <ul class="bar-list">
                              {#each sessionDetail.tables_touched as t}
                                {@const m = maxCount(sessionDetail.tables_touched)}
                                <li>
                                  <span class="bar-label" title={t.table}>{t.table}</span>
                                  <span class="bar-track">
                                    <span class="bar-fill" style="width:{(t.count / m) * 100}%"></span>
                                  </span>
                                  <span class="bar-count">{t.count}</span>
                                </li>
                              {/each}
                            </ul>
                          {/if}
                        </div>

                        <div class="audit-block">
                          <h3>Tools called</h3>
                          {#if sessionDetail.tools_called.length === 0}
                            <p class="muted small">No tool invocations.</p>
                          {:else}
                            <ul class="bar-list">
                              {#each sessionDetail.tools_called as t}
                                {@const m = maxCount(sessionDetail.tools_called)}
                                <li>
                                  <span class="bar-label" title={t.tool}>{t.tool}</span>
                                  <span class="bar-track">
                                    <span class="bar-fill" style="width:{(t.count / m) * 100}%"></span>
                                  </span>
                                  <span class="bar-count" class:err={t.error_count > 0}>
                                    {t.count}{#if t.error_count > 0} ({t.error_count} err){/if}
                                  </span>
                                </li>
                              {/each}
                            </ul>
                          {/if}
                        </div>

                        <div class="audit-block">
                          <h3>Skills applied</h3>
                          {#if sessionDetail.skills_used.length === 0}
                            <p class="muted small">No skills recorded.</p>
                          {:else}
                            <ul class="bar-list">
                              {#each sessionDetail.skills_used as sk}
                                {@const m = maxCount(sessionDetail.skills_used)}
                                <li>
                                  <span class="bar-label" title={sk.skill_id}>{sk.skill_id}</span>
                                  <span class="bar-track">
                                    <span class="bar-fill" style="width:{(sk.count / m) * 100}%"></span>
                                  </span>
                                  <span class="bar-count">{sk.count}/{sk.success_count}✓</span>
                                </li>
                              {/each}
                            </ul>
                          {/if}
                        </div>

                        <div class="audit-block">
                          <h3>RLS policies fired</h3>
                          {#if sessionDetail.rls_policies_fired.length === 0}
                            <p class="muted small">No RLS policy hits captured.</p>
                          {:else}
                            <ul class="bar-list">
                              {#each sessionDetail.rls_policies_fired as p}
                                {@const m = maxCount(sessionDetail.rls_policies_fired)}
                                <li>
                                  <span class="bar-label" title={p.policy}>{p.policy}</span>
                                  <span class="bar-track">
                                    <span class="bar-fill" style="width:{(p.count / m) * 100}%"></span>
                                  </span>
                                  <span class="bar-count">{p.count}</span>
                                </li>
                              {/each}
                            </ul>
                          {/if}
                        </div>

                        <div class="audit-block">
                          <h3>Errors / retries</h3>
                          {#if sessionDetail.errors.length === 0}
                            <p class="muted small">No errors.</p>
                          {:else}
                            <ul class="err-list">
                              {#each sessionDetail.errors as e}
                                <li>
                                  <div class="err-name">{e.span_name}</div>
                                  <div class="err-msg small muted">{e.error || '(no detail)'}</div>
                                  <div class="err-time small muted">{fmtTime(e.started_at)}</div>
                                </li>
                              {/each}
                            </ul>
                          {/if}
                        </div>
                      </div>
                    {/if}
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </section>
    {/if}
  {/if}

  <!-- ── SUMMARY TAB ───────────────────────────────────────────── -->
  {#if activeTab === 'summary'}
    <div class="row-controls">
      <select bind:value={days} onchange={loadSummary}>
        <option value={1}>Last 24h</option>
        <option value={7}>Last 7 days</option>
        <option value={30}>Last 30 days</option>
        <option value={90}>Last 90 days</option>
      </select>
      <button class="refresh" onclick={loadSummary} disabled={summaryLoading}>{summaryLoading ? '…' : 'Refresh'}</button>
    </div>
    {#if !summary && !summaryLoading}
      <p class="muted small">Enter project slug and click Load.</p>
    {:else if summaryLoading}
      <p class="muted">Loading…</p>
    {:else if summary}
      {#if summary.warning}
        <div class="banner warn">{summary.warning}</div>
      {/if}
      <section class="tiles">
        <div class="tile">
          <div class="tile-label">Sessions</div>
          <div class="tile-val">{(summary.session_count || 0).toLocaleString()}</div>
          <div class="tile-sub">in {summary.days}d window</div>
        </div>
        <div class="tile">
          <div class="tile-label">Tables touched</div>
          <div class="tile-val">{summary.top_tables.length}</div>
          <div class="tile-sub">distinct tables</div>
        </div>
        <div class="tile">
          <div class="tile-label">Total cost</div>
          <div class="tile-val">{fmtCost(summary.total_cost_usd)}</div>
          <div class="tile-sub">LLM + tools</div>
        </div>
        <div class="tile">
          <div class="tile-label">Error rate</div>
          <div class="tile-val" class:err={summary.error_rate > 0.05}>{(summary.error_rate * 100).toFixed(1)}%</div>
          <div class="tile-sub">of total spans</div>
        </div>
      </section>

      <section class="agg-grid">
        <div class="audit-block">
          <h3>Top 10 tables</h3>
          {#if summary.top_tables.length === 0}
            <p class="muted small">No SELECT'd tables tracked.</p>
          {:else}
            <ul class="bar-list">
              {#each summary.top_tables as t}
                {@const m = maxCount(summary.top_tables)}
                <li>
                  <span class="bar-label" title={t.table}>{t.table}</span>
                  <span class="bar-track"><span class="bar-fill" style="width:{(t.count / m) * 100}%"></span></span>
                  <span class="bar-count">{t.count}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>

        <div class="audit-block">
          <h3>Top 10 skills</h3>
          {#if summary.top_skills.length === 0}
            <p class="muted small">No skills tracked in window.</p>
          {:else}
            <ul class="bar-list">
              {#each summary.top_skills as s}
                {@const m = maxCount(summary.top_skills)}
                <li>
                  <span class="bar-label" title={s.skill_id}>{s.skill_id}</span>
                  <span class="bar-track"><span class="bar-fill" style="width:{(s.count / m) * 100}%"></span></span>
                  <span class="bar-count">{s.count}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>

        <div class="audit-block">
          <h3>Top 10 users</h3>
          {#if summary.top_users.length === 0}
            <p class="muted small">No users.</p>
          {:else}
            <ul class="bar-list">
              {#each summary.top_users as u}
                {@const m = Math.max(1, ...summary.top_users.map(x => x.session_count))}
                <li>
                  <span class="bar-label" title={u.username || `#${u.user_id}`}>{u.username || `#${u.user_id}`}</span>
                  <span class="bar-track"><span class="bar-fill" style="width:{(u.session_count / m) * 100}%"></span></span>
                  <span class="bar-count">{u.session_count}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>

        <div class="audit-block">
          <h3>Top 10 tools</h3>
          {#if summary.top_tools.length === 0}
            <p class="muted small">No tool spans tracked.</p>
          {:else}
            <ul class="bar-list">
              {#each summary.top_tools as t}
                {@const m = maxCount(summary.top_tools)}
                <li>
                  <span class="bar-label" title={t.tool}>{t.tool}</span>
                  <span class="bar-track"><span class="bar-fill" style="width:{(t.count / m) * 100}%"></span></span>
                  <span class="bar-count">{t.count}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      </section>
    {/if}
  {/if}

  <!-- ── USER TAB ────────────────────────────────────────────── -->
  {#if activeTab === 'user'}
    <div class="row-controls">
      <input
        type="number"
        placeholder="user id"
        bind:value={userIdInput}
        onkeydown={(e) => { if (e.key === 'Enter') loadUser(); }}
      />
      <button class="primary" onclick={loadUser} disabled={userLoading}>{userLoading ? '…' : 'Load'}</button>
    </div>

    {#if userAudit}
      {#if userAudit.warning}
        <div class="banner warn">{userAudit.warning}</div>
      {/if}
      <section class="tiles">
        <div class="tile">
          <div class="tile-label">User</div>
          <div class="tile-val">{userAudit.username || `#${userAudit.user_id}`}</div>
          <div class="tile-sub">last {userAudit.days}d</div>
        </div>
        <div class="tile">
          <div class="tile-label">Sessions</div>
          <div class="tile-val">{userAudit.session_count}</div>
          <div class="tile-sub">{userAudit.project_count} project(s)</div>
        </div>
        <div class="tile">
          <div class="tile-label">Total cost</div>
          <div class="tile-val">{fmtCost(userAudit.total_cost_usd)}</div>
          <div class="tile-sub">all sessions</div>
        </div>
      </section>

      <section class="agg-grid">
        <div class="audit-block">
          <h3>Top projects</h3>
          {#if userAudit.top_projects.length === 0}
            <p class="muted small">No project activity.</p>
          {:else}
            <ul class="bar-list">
              {#each userAudit.top_projects as p}
                {@const m = Math.max(1, ...userAudit.top_projects.map(x => x.session_count))}
                <li>
                  <span class="bar-label" title={p.project_slug}>{p.project_slug}</span>
                  <span class="bar-track"><span class="bar-fill" style="width:{(p.session_count / m) * 100}%"></span></span>
                  <span class="bar-count">{p.session_count}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>

        <div class="audit-block">
          <h3>Top tools</h3>
          {#if userAudit.top_tools.length === 0}
            <p class="muted small">No tool spans tracked.</p>
          {:else}
            <ul class="bar-list">
              {#each userAudit.top_tools as t}
                {@const m = maxCount(userAudit.top_tools)}
                <li>
                  <span class="bar-label" title={t.tool}>{t.tool}</span>
                  <span class="bar-track"><span class="bar-fill" style="width:{(t.count / m) * 100}%"></span></span>
                  <span class="bar-count">{t.count}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      </section>

      <section class="card">
        <h2>Recent sessions ({userAudit.recent_sessions.length})</h2>
        {#if userAudit.recent_sessions.length === 0}
          <p class="muted small">No sessions in window.</p>
        {:else}
          <table class="sa-table">
            <thead>
              <tr>
                <th>Session</th>
                <th>Project</th>
                <th>First message</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {#each userAudit.recent_sessions as s}
                <tr>
                  <td><code class="mono">{s.session_id.slice(0, 16)}…</code></td>
                  <td>{s.project_slug || '—'}</td>
                  <td class="msg">{#each kw(s.first_message) as k}<span class="kwc">{k}</span>{/each}{#if !kw(s.first_message).length}—{/if}</td>
                  <td class="small muted">{fmtTime(s.created_at)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    {/if}
  {/if}
</div>

<style>
  .sa-shell {
    padding: 24px 32px;
    max-width: 1280px;
    margin: 0 auto;
    font-family: system-ui, -apple-system, sans-serif;
    color: #1f1c17;
  }
  .sa-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid #e8e3d6;
  }
  .sa-head h1 {
    font-size: 22px;
    margin: 0 0 4px 0;
    font-weight: 600;
  }
  .muted { color: #777; font-size: 13px; margin: 0; }
  .small { font-size: 12px; }
  .err { color: #b3261e !important; }
  .ctrls { display: flex; gap: 8px; align-items: center; }
  .ctrls input[type="text"], .row-controls input, .row-controls select {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    min-width: 240px;
  }
  button {
    padding: 6px 12px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
  }
  button.primary {
    background: #c96342; color: #fff; border-color: #c96342;
  }
  button.primary:hover { background: #b15639; }
  button:hover:not(.primary) { background: #f7f3e9; }
  button:disabled { opacity: 0.5; cursor: default; }

  .tabs {
    display: flex; gap: 0;
    margin-bottom: 18px;
    border-bottom: 1px solid #e8e3d6;
  }
  .tabs button {
    background: transparent; border: none; border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: 8px 16px; font-size: 13px; font-weight: 500;
    color: #777;
  }
  .tabs button.active {
    color: #c96342; border-bottom-color: #c96342;
  }

  .row-controls {
    display: flex; gap: 8px; margin-bottom: 16px; align-items: center;
  }

  .banner {
    background: #fdf3e7; border: 1px solid #f0c896;
    color: #8a4b14; padding: 8px 12px; border-radius: 4px;
    font-size: 12px; margin-bottom: 12px;
  }
  .banner.warn { background: #fff7e0; border-color: #f3d97a; color: #7a5a14; }

  .card {
    background: #fff; border: 1px solid #e8e3d6;
    border-radius: 6px; padding: 16px; margin-bottom: 20px;
  }
  .card h2 {
    font-size: 13px; margin: 0 0 12px 0;
    text-transform: uppercase; letter-spacing: 0.04em; color: #555; font-weight: 600;
  }

  .sa-table { width: 100%; border-collapse: collapse; }
  .sa-table th, .sa-table td {
    text-align: left; padding: 8px 10px;
    border-bottom: 1px solid #f0ebde;
    font-size: 13px;
  }
  .sa-table th {
    font-size: 11px; color: #777; text-transform: uppercase;
    letter-spacing: 0.04em; font-weight: 500; background: #faf7ef;
  }
  .sa-table tr.row { cursor: pointer; }
  .sa-table tr.row:hover { background: #faf7ef; }
  .sa-table .chev { font-size: 10px; color: #c96342; }
  .sa-table .msg { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .kwc { display: inline-block; font-size: 10px; background: #eef1f4; color: #4a5568; border-radius: 999px; padding: .05rem .4rem; margin: 0 .15rem .15rem 0; }
  .mono { font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 11px; background: #f7f3e9; padding: 2px 6px; border-radius: 3px; }

  .detail-row td { background: #fdfaf3; padding: 16px; }

  .kpi-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px; }
  .kpi { background: #fff; border: 1px solid #e8e3d6; border-radius: 6px; padding: 12px; }
  .kpi-label { font-size: 10px; color: #777; text-transform: uppercase; letter-spacing: 0.05em; }
  .kpi-val { font-size: 22px; font-weight: 600; color: #c96342; margin-top: 4px; }

  .audit-grid {
    display: grid;
    grid-template-columns: minmax(280px, 1.4fr) 1fr 1fr;
    gap: 12px;
  }
  .audit-block {
    background: #fff; border: 1px solid #e8e3d6;
    border-radius: 6px; padding: 12px;
    min-width: 0;
  }
  .audit-block h3 {
    font-size: 11px; margin: 0 0 10px 0;
    text-transform: uppercase; letter-spacing: 0.04em;
    color: #777; font-weight: 600;
  }
  .audit-block.timeline { grid-row: span 2; }

  .agg-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 14px;
    margin-bottom: 20px;
  }

  .tiles {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 18px;
  }
  .tile {
    background: #fff; border: 1px solid #e8e3d6;
    border-radius: 6px; padding: 14px;
  }
  .tile-label { font-size: 11px; color: #777; text-transform: uppercase; letter-spacing: 0.04em; }
  .tile-val { font-size: 24px; font-weight: 600; color: #c96342; margin: 6px 0 4px 0; }
  .tile-sub { font-size: 11px; color: #999; }

  .span-list { list-style: none; padding: 0; margin: 0; max-height: 460px; overflow-y: auto; }
  .span {
    padding: 6px 8px;
    border-left: 2px solid #e8e3d6;
    margin-bottom: 4px;
  }
  .span-error { border-left-color: #b3261e; background: #fef5f3; }
  .span-row { display: flex; gap: 8px; align-items: center; font-size: 12px; flex-wrap: wrap; }
  .span-name { font-family: 'SF Mono', Menlo, Consolas, monospace; flex: 1; min-width: 100px; }
  .span-kind {
    font-size: 10px; text-transform: uppercase; padding: 1px 6px;
    border-radius: 3px; font-weight: 600; letter-spacing: 0.04em;
    background: #efe9d8; color: #6b5a30;
  }
  .span-kind.kind-chat { background: #ffe0d5; color: #a13d1c; }
  .span-kind.kind-tool { background: #d6eaff; color: #1e5394; }
  .span-kind.kind-training { background: #e7d8ff; color: #5a2eaf; }
  .span-kind.kind-cron { background: #dfe9c2; color: #4d5e21; }
  .span-kind.kind-ml { background: #ffd9e0; color: #931b3a; }
  .span-dur { min-width: 50px; text-align: right; }
  .span-cost { color: #8a4b14; font-family: 'SF Mono', Menlo, monospace; }
  .span-err { color: #b3261e; margin-top: 3px; padding-left: 4px; }

  .bar-list { list-style: none; padding: 0; margin: 0; }
  .bar-list li {
    display: grid; grid-template-columns: minmax(0, 1fr) 80px 60px;
    gap: 6px; align-items: center; padding: 3px 0;
    font-size: 12px;
  }
  .bar-label {
    font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 11px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .bar-track {
    background: #f0ebde; height: 8px; border-radius: 2px; overflow: hidden;
  }
  .bar-fill { display: block; background: #c96342; height: 100%; }
  .bar-count { text-align: right; font-size: 11px; color: #666; font-variant-numeric: tabular-nums; }

  .err-list { list-style: none; padding: 0; margin: 0; }
  .err-list li {
    padding: 6px 8px;
    background: #fef5f3; border-left: 2px solid #b3261e;
    margin-bottom: 4px;
  }
  .err-name { font-size: 12px; font-weight: 500; color: #b3261e; }
  .err-msg { font-family: 'SF Mono', Menlo, monospace; word-break: break-word; }

  @media (max-width: 1080px) {
    .audit-grid { grid-template-columns: 1fr; }
    .audit-block.timeline { grid-row: auto; }
    .agg-grid { grid-template-columns: 1fr; }
    .tiles, .kpi-strip { grid-template-columns: repeat(2, 1fr); }
  }
</style>
