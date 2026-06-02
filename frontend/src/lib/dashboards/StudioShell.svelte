<script lang="ts">
  /**
   * StudioShell — project-scoped dashboard Studio.
   *
   * Used by:
   *   /project/[slug]/studio       (new dashboard session, dashId=null)
   *   /project/[slug]/studio/[id]  (refine existing dashboard)
   *
   * Both pass `slug` (required) into every SSE call to:
   *   POST /api/dashboards/deep-build/stream   (first turn, new)
   *   POST /api/dashboards/{id}/refine         (subsequent turns)
   *
   * Warm Dash theme. Two-pane split (38% left chat / 62% right canvas).
   */
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';
  import DashRenderer from '$lib/dashboards/DashRenderer.svelte';
  import ChatMessageList from '$lib/chat/ChatMessageList.svelte';
  import DashChatPanel from '$lib/dashboards/DashChatPanel.svelte';
  import TemplateGalleryModal from '$lib/studio/TemplateGalleryModal.svelte';
  import NewAgentForm from '$lib/studio/NewAgentForm.svelte';
  import SuggestionCard from '$lib/studio/SuggestionCard.svelte';

  let chatOpen = $state(false);

  // ── Hybrid agent-template flow (Tasks #30, #31) ────────────────────────
  let templateGalleryOpen = $state(false);
  let newAgentTemplate = $state<any | null>(null);
  let newAgentFormOpen = $state(false);
  // Bumped on successful build → triggers SuggestionCard to refetch.
  let suggestionRefreshToken = $state(0);

  function openNewAgent() {
    newAgentTemplate = null;
    newAgentFormOpen = false;
    templateGalleryOpen = true;
  }
  function onTemplatePicked(tpl: any | null) {
    templateGalleryOpen = false;
    newAgentTemplate = tpl;
    newAgentFormOpen = true;
  }
  function onAgentSaved(agentId: string) {
    newAgentFormOpen = false;
    newAgentTemplate = null;
    if (agentId && typeof window !== 'undefined') {
      // Best-effort: navigate to agent detail if route exists; otherwise stay.
      try {
        goto(`${base}/project/${encodeURIComponent(slug)}/agents/${encodeURIComponent(agentId)}`);
      } catch {}
    }
  }
  function onAgentCancelled() {
    newAgentFormOpen = false;
    newAgentTemplate = null;
  }
  function onSuggestionUse(tpl: any | null) {
    newAgentTemplate = tpl;
    templateGalleryOpen = false;
    newAgentFormOpen = true;
  }

  // ────────────────────────────────────────────────────────────────────────
  // Props
  // ────────────────────────────────────────────────────────────────────────
  interface Props {
    slug: string;
    dashId?: string | null;
  }
  let { slug, dashId = null }: Props = $props();

  // ────────────────────────────────────────────────────────────────────────
  // State
  // ────────────────────────────────────────────────────────────────────────
  let messages = $state<any[]>([]);
  let spec = $state<any>({ title: '', cells: [], filters: [], insights: [] });
  let panelData = $state<Record<string, any>>({});
  let narrative = $state<{ text: string; audience?: string; verified_value_count?: number } | null>(null);
  let projectInfo = $state<any>(null);

  const AUDIENCES = ['Investor', 'Ops', 'Customer', 'Exec'] as const;
  const AUDIENCE_KEY = `dash_studio_audience_${slug}`;
  let audience = $state<string>('Exec');
  let audienceHint = $state<string>('');
  let audienceHintTimer: ReturnType<typeof setTimeout> | null = null;

  // Deep Dash v2 persists spec.panels (new EChartsPanelSpec shape).
  // DashRenderer reads spec.cells (legacy DashboardSpec). Bridge them here so
  // refine-mode load AND `done` event both produce a rendered grid.
  function _panelToCell(p: any) {
    const grid = Array.isArray(p?.grid) && p.grid.length >= 4 ? p.grid : [0, 0, 6, 3];
    const ptype = String(p?.panel_type || 'chart').toLowerCase();
    const type = ptype === 'kpi' ? 'kpi'
               : ptype === 'insight' ? 'insight'
               : ptype === 'narrative' ? 'insight'
               : ptype === 'table' ? 'table'
               : 'chart';
    return {
      id: p?.panel_id || `p_${Math.random().toString(36).slice(2, 8)}`,
      type, grid, title: p?.title || '',
      verified: !!p?.verified,
      source_metric: p?.source_metric,
      config: {
        chart_type: p?.chart_type || 'bar',
        echarts_options: p?.options || {},
        narrative: p?.narrative || '',
        confidence: p?.confidence || 'medium',
        sources: p?.sources || [],
        headline: (type === 'insight' || type === 'kpi') ? (p?.title || '') : undefined,
        cause: (type === 'insight') ? (p?.narrative || '') : undefined,
      },
    };
  }
  function _ensureCells(s: any): any[] {
    if (Array.isArray(s?.cells) && s.cells.length) return s.cells;
    if (Array.isArray(s?.panels) && s.panels.length) return s.panels.map(_panelToCell);
    return Array.isArray(s?.cells) ? s.cells : [];
  }

  function _normAudience(v: any): string {
    if (!v || typeof v !== 'string') return '';
    const s = v.trim().toLowerCase();
    for (const a of AUDIENCES) {
      if (a.toLowerCase() === s) return a;
    }
    return '';
  }

  function _persistAudience(a: string) {
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(AUDIENCE_KEY, a);
      }
    } catch {}
  }

  function setAudience(a: string) {
    if (a === audience) return;
    const prev = audience;
    audience = a;
    _persistAudience(a);
    if (prev) {
      audienceHint = `Audience changed — next build will use ${a} tone`;
      if (audienceHintTimer) clearTimeout(audienceHintTimer);
      audienceHintTimer = setTimeout(() => { audienceHint = ''; }, 3000);
    }
  }

  // Phase 4c — version dropdown state (populated in onMount if dashboard has session_id)
  let dashVersions = $state<any[]>([]);
  let dashCurrentVersion = $state<number | null>(null);
  let dashVersionsDropdownOpen = $state(false);
  let currentSessionId = $state<string | null>(null);

  async function deleteDashVersion(dashboardId: string, version: number) {
    if (dashVersions.length <= 1) return;
    if (!confirm(`Delete v${version}? This cannot be undone.`)) return;
    try {
      const r = await fetch(`/api/dashboards/${encodeURIComponent(dashboardId)}?project_slug=${encodeURIComponent(slug)}`, { method: 'DELETE', headers: _headers() });
      if (!r.ok) return;
      const wasCurrent = dashboardId === dashId;
      if (currentSessionId) {
        const lr = await fetch(`/api/dashboards/by-session/${encodeURIComponent(currentSessionId)}?project_slug=${encodeURIComponent(slug)}`, { headers: _headers() });
        if (lr.ok) dashVersions = await lr.json();
      } else {
        dashVersions = dashVersions.filter(v => v.dashboard_id !== dashboardId);
      }
      if (wasCurrent) {
        if (dashVersions.length > 0) goto(`${base}/project/${encodeURIComponent(slug)}/studio/${encodeURIComponent(dashVersions[0].dashboard_id)}`);
        else goto(`${base}/project/${encodeURIComponent(slug)}/studio`);
      }
    } catch {}
  }

  let composerText = $state('');
  let busy = $state(false);
  let stageStatus = $state<string>('');     // streaming status pill text
  let panelsAdded = $state<number>(0);
  let runStart = $state<number>(0);
  let runWallS = $state<number | null>(null);
  let sseAbort: AbortController | null = null;

  const EXAMPLE_PROMPTS = [
    'Top customers last 30 days',
    'Revenue by channel',
    'Churn by cohort',
  ];

  // ────────────────────────────────────────────────────────────────────────
  // Helpers
  // ────────────────────────────────────────────────────────────────────────
  function nowISO() { return new Date().toISOString(); }
  function _headers(): Record<string, string> {
    const t = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  function updateMessage(index: number, patch: any) {
    if (index < 0 || index >= messages.length) return;
    messages[index] = { ...messages[index], ...patch };
    messages = messages;
  }
  function lastAssistantIndex(): number {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') return i;
    }
    return -1;
  }
  function pushAssistantAnnouncement(payload: any) {
    const idx = lastAssistantIndex();
    if (idx < 0) return;
    const list = Array.isArray(messages[idx].panelAnnouncements)
      ? [...messages[idx].panelAnnouncements]
      : [];
    list.push(payload);
    updateMessage(idx, { panelAnnouncements: list });
  }
  function appendThinking(line: string) {
    const idx = lastAssistantIndex();
    if (idx < 0) return;
    const prev = messages[idx].content || '';
    updateMessage(idx, { content: prev ? prev + '\n' + line : line });
  }
  function appendCell(cell: any) {
    if (!cell || !cell.id) return;
    const cells = Array.isArray(spec.cells) ? [...spec.cells] : [];
    if (cells.some((c: any) => c.id === cell.id)) return;
    cells.push(cell);
    spec = { ...spec, cells };
    panelsAdded = cells.length;
    stageStatus = `Added ${cells.length} panel${cells.length === 1 ? '' : 's'}`;
    if (cell.data && typeof cell.data === 'object') {
      panelData = { ...panelData, [cell.id]: cell.data };
    }
  }
  // ────────────────────────────────────────────────────────────────────────
  // SSE pump
  // ────────────────────────────────────────────────────────────────────────
  async function streamSSE(url: string, body: any) {
    busy = true;
    stageStatus = 'Building dashboard…';
    panelsAdded = 0;
    runStart = Date.now();
    runWallS = null;

    if (sseAbort) { try { sseAbort.abort(); } catch {} }
    sseAbort = new AbortController();

    messages = [...messages, {
      role: 'assistant',
      content: '',
      timestamp: nowISO(),
      status: 'streaming',
      panelAnnouncements: [],
    } as any];

    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
          ..._headers(),
        },
        body: JSON.stringify(body),
        signal: sseAbort.signal,
      });
      if (!res.ok || !res.body) {
        const t = await res.text().catch(() => '');
        const idx = lastAssistantIndex();
        if (idx >= 0) updateMessage(idx, { status: 'error', content: (messages[idx].content || '') + `\n[error] ${res.status} ${t.slice(0, 200)}` });
        stageStatus = `Error · ${res.status}`;
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nlIdx: number;
        while ((nlIdx = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, nlIdx);
          buf = buf.slice(nlIdx + 2);
          handleSSEFrame(frame);
        }
      }
      const idx = lastAssistantIndex();
      if (idx >= 0 && messages[idx].status === 'streaming') {
        updateMessage(idx, { status: 'done' });
      }
      runWallS = (Date.now() - runStart) / 1000;
      stageStatus = `Done · ${runWallS.toFixed(1)}s`;
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        const idx = lastAssistantIndex();
        if (idx >= 0) updateMessage(idx, { status: 'error', content: (messages[idx].content || '') + `\n[error] ${String(e?.message || e)}` });
        stageStatus = 'Error';
      } else {
        stageStatus = 'Stopped';
      }
    } finally {
      busy = false;
      sseAbort = null;
    }
  }

  function handleSSEFrame(frame: string) {
    let evt = 'message';
    const dataLines: string[] = [];
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) evt = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return;
    const raw = dataLines.join('\n');
    let payload: any = raw;
    try { payload = JSON.parse(raw); } catch {}

    switch (evt) {
      case 'stage_start':
        appendThinking(`▸ ${payload?.stage || 'stage'} starting…`);
        stageStatus = `Building · ${payload?.stage || '…'}`;
        break;
      case 'stage_done':
        appendThinking(`✓ ${payload?.stage || 'stage'} done`);
        break;
      case 'stage_error':
        appendThinking(`✗ ${payload?.stage || 'stage'} — ${payload?.error || 'error'}`);
        break;
      case 'panel_ready':
        if (payload && typeof payload === 'object') {
          const cell = payload.cell || payload.panel || payload;
          appendCell(cell);
        }
        break;
      case 'panel_announcement':
        pushAssistantAnnouncement(payload || {});
        break;
      case 'narrative_ready':
        narrative = {
          text: String(payload?.narrative_text || payload?.text || ''),
          audience: payload?.audience,
          verified_value_count: payload?.verified_value_count,
        };
        break;
      case 'done':
        if (payload?.id && typeof window !== 'undefined') {
          // Rewrite URL to refine mode at the new id (preserve slug).
          try {
            history.replaceState(null, '', `${base}/project/${encodeURIComponent(slug)}/studio/${encodeURIComponent(payload.id)}`);
          } catch {}
        }
        if (payload?.spec && typeof payload.spec === 'object') {
          spec = { ...spec, ...payload.spec, cells: _ensureCells(payload.spec) };
          const savedAud = _normAudience(payload?.spec?.audience);
          if (savedAud) {
            audience = savedAud;
            _persistAudience(savedAud);
          } else {
            // Persist the active audience the user built with so it survives refresh
            _persistAudience(audience);
          }
        }
        if (payload?.data && typeof payload.data === 'object') {
          panelData = { ...panelData, ...payload.data };
        }
        // Task #31 — retrigger template suggestion fetch after build completes.
        suggestionRefreshToken += 1;
        break;
      case 'error':
        appendThinking(`[error] ${payload?.error || JSON.stringify(payload).slice(0, 200)}`);
        break;
    }
  }

  // ────────────────────────────────────────────────────────────────────────
  // Composer
  // ────────────────────────────────────────────────────────────────────────
  function isFirstTurn(): boolean {
    return !dashId && messages.filter(m => m.role === 'user').length === 0;
  }

  async function onSubmit() {
    const q = composerText.trim();
    if (!q || busy) return;
    // Capture first-turn BEFORE pushing the user message (else the just-pushed
    // user msg makes isFirstTurn() return false and refine fires with empty id → 404).
    const firstTurn = isFirstTurn();
    composerText = '';
    messages = [...messages, { role: 'user', content: q, timestamp: nowISO(), status: 'done' } as any];

    if (firstTurn) {
      await streamSSE('/api/dashboards/deep-build/stream', {
        project_slug: slug,
        question: q,
        audience,
      });
    } else {
      const id = dashId || (spec && spec.id) || '';
      if (!id) {
        // Defensive: no id yet (first build still streaming) — fall back to build path.
        await streamSSE('/api/dashboards/deep-build/stream', {
          project_slug: slug,
          question: q,
          audience,
        });
        return;
      }
      await streamSSE(`/api/dashboards/${encodeURIComponent(id)}/refine`, {
        project_slug: slug,
        command: q,
        audience,
      });
    }
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  }

  function onStop() {
    if (sseAbort) { try { sseAbort.abort(); } catch {} }
  }

  function pickExample(p: string) {
    composerText = p;
  }

  // Load project info + existing dashboard if [id]
  onMount(async () => {
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(slug)}`, { headers: _headers() });
      if (r.ok) projectInfo = await r.json();
    } catch {}

    if (dashId) {
      // Refine mode: prefer saved spec.audience; fall back to localStorage; else 'Exec'
      try {
        const r = await fetch(`/api/dashboards/${encodeURIComponent(dashId)}?project_slug=${encodeURIComponent(slug)}`, { headers: _headers() });
        if (r.ok) {
          const d = await r.json();
          if (d.spec) {
            const s = d.spec;
            // SYNC normalize: mirror panels -> cells BEFORE assignment so empty-state
            // doesn't flash + DashRenderer reads cells on first render. Phase 4a fix.
            if ((!Array.isArray(s.cells) || s.cells.length === 0) && Array.isArray(s.panels) && s.panels.length) {
              s.cells = s.panels.map((p: any) => _panelToCell(p));
            } else if (!Array.isArray(s.cells)) {
              s.cells = [];
            }
            s.id = d.dashboard_id || d.id || s.id || dashId;
            spec = s;
          }
          if (d.data) panelData = d.data;
          if (d.narrative) narrative = d.narrative;
          const fromSpec = _normAudience(d?.spec?.audience);
          if (fromSpec) {
            audience = fromSpec;
            _persistAudience(fromSpec);
          } else {
            try {
              const ls = (typeof localStorage !== 'undefined') ? localStorage.getItem(AUDIENCE_KEY) : null;
              const norm = _normAudience(ls);
              if (norm) audience = norm;
            } catch {}
          }
          // Phase 4c — load versions list if session_id present (graceful skip if not)
          const sid = d.session_id || d?.spec?.session_id || d?.spec?.metadata?.session_id;
          if (sid) { currentSessionId = sid; }
          if (sid) {
            try {
              const vr = await fetch(`/api/dashboards/by-session/${encodeURIComponent(sid)}?project_slug=${encodeURIComponent(slug)}`, { headers: _headers() });
              if (vr.ok) {
                const vlist = await vr.json();
                if (Array.isArray(vlist)) {
                  dashVersions = vlist;
                  const curId = d.dashboard_id || d.id || dashId;
                  const cur = vlist.find((v: any) => v.dashboard_id === curId);
                  if (cur && typeof cur.version === 'number') dashCurrentVersion = cur.version;
                }
              }
            } catch {}
          }
        }
      } catch {}
    } else {
      // New mode: pre-select from localStorage if present, else default 'Exec'
      try {
        const ls = (typeof localStorage !== 'undefined') ? localStorage.getItem(AUDIENCE_KEY) : null;
        const norm = _normAudience(ls);
        if (norm) audience = norm;
      } catch {}
    }
  });
</script>

<svelte:head>
  <title>Studio · {projectInfo?.agent_name || projectInfo?.name || slug}</title>
</svelte:head>

<div class="studio-root">
  <!-- Topbar -->
  <header class="studio-topbar">
    <div class="topbar-left">
      <button class="back-btn" onclick={() => goto(`${base}/project/${encodeURIComponent(slug)}`)} title="Back to project">←</button>
      <nav class="breadcrumb">
        <a href="{base}/projects">Projects</a>
        <span class="bc-sep">›</span>
        <a href="{base}/project/{slug}">{projectInfo?.agent_name || projectInfo?.name || slug}</a>
        <span class="bc-sep">›</span>
        <span class="bc-current">Studio{dashId ? ' · refine' : ''}</span>
      </nav>
    </div>
    <div class="topbar-right">
      {#if dashCurrentVersion && dashVersions.length > 1}
        <div style="position: relative; display: inline-block; margin-right: 10px;">
          <button onclick={() => dashVersionsDropdownOpen = !dashVersionsDropdownOpen}
                  type="button"
                  style="padding: 4px 10px; background: var(--pw-bg-alt, #f7f6f3); border: 1px solid var(--pw-border, #e2ddd2); cursor: pointer; font-family: inherit; font-size: 12px; color: var(--pw-ink, #2c2a26); border-radius: var(--pw-radius-sm, 4px);">
            v{dashCurrentVersion} ▾
          </button>
          {#if dashVersionsDropdownOpen}
            <div style="position: absolute; top: 32px; right: 0; min-width: 260px; background: var(--pw-bg, #fdfaf5); border: 1px solid var(--pw-border, #e2ddd2); box-shadow: 0 4px 12px rgba(0,0,0,0.08); z-index: 100; max-height: 360px; overflow-y: auto;">
              {#each dashVersions as v}
                <div style="display: flex; align-items: center; gap: 4px; border-bottom: 1px solid var(--pw-border, #e2ddd2);">
                  <button type="button"
                          onclick={() => { goto(`${base}/project/${encodeURIComponent(slug)}/studio/${encodeURIComponent(v.dashboard_id)}`); dashVersionsDropdownOpen = false; }}
                          style="flex: 1; text-align: left; padding: 10px 12px; background: {v.version === dashCurrentVersion ? 'rgba(201,99,66,0.08)' : 'transparent'}; border: none; cursor: pointer; font-family: inherit; font-size: 12px; color: var(--pw-ink, #2c2a26);">
                    <div style="display: flex; align-items: center; gap: 6px;">
                      <span style="color: {v.version === dashCurrentVersion ? 'var(--pw-accent, #c96342)' : 'var(--pw-muted, #999)'};">{v.version === dashCurrentVersion ? '●' : '○'}</span>
                      <strong>v{v.version}</strong>
                      <span style="color: var(--pw-muted, #999); margin-left: auto; font-size: 11px;">{v.created_at ? new Date(v.created_at).toLocaleString() : ''}</span>
                    </div>
                    {#if v.label}<div style="margin-top: 2px; color: var(--pw-muted, #999); font-size: 10px; padding-left: 14px;">{v.label}</div>{/if}
                    <div style="font-size: 10px; color: var(--pw-muted, #999); padding-left: 14px;">{v.n_panels ?? 0} panels</div>
                  </button>
                  <button type="button"
                          onclick={(e) => { e.stopPropagation(); deleteDashVersion(v.dashboard_id, v.version); }}
                          disabled={dashVersions.length === 1}
                          title={dashVersions.length === 1 ? 'Last version, cannot delete' : `Delete v${v.version}`}
                          style="padding: 4px 8px; margin-right: 4px; background: transparent; color: {dashVersions.length === 1 ? 'var(--pw-muted, #999)' : '#c0392b'}; border: none; cursor: {dashVersions.length === 1 ? 'not-allowed' : 'pointer'}; opacity: {dashVersions.length === 1 ? 0.45 : 1}; font-size: 13px;">✕</button>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
      {#if dashId}
        <button
          onclick={() => chatOpen = !chatOpen}
          type="button"
          title="Ask questions about this dashboard"
          style="padding: 4px 10px; margin-right: 10px; background: {chatOpen ? 'var(--pw-accent, #c96342)' : 'var(--pw-bg-alt, #f7f6f3)'}; color: {chatOpen ? '#fff' : 'var(--pw-ink, #2c2a26)'}; border: 1px solid {chatOpen ? 'var(--pw-accent, #c96342)' : 'var(--pw-border, #e2ddd2)'}; cursor: pointer; font-family: inherit; font-size: 12px; border-radius: var(--pw-radius-sm, 4px);">
          🗨 {chatOpen ? 'Close' : 'Ask'}
        </button>
      {/if}
      <button
        onclick={openNewAgent}
        type="button"
        title="Create a new agent from a template"
        style="padding: 4px 12px; margin-right: 10px; background: var(--pw-accent, #c96342); color: #fff; border: 1px solid var(--pw-accent, #c96342); cursor: pointer; font-family: inherit; font-size: 12px; font-weight: 600; border-radius: var(--pw-radius-sm, 4px);">
        + New Agent
      </button>
      <div class="audience-wrap">
        <div class="audience-group" role="group" aria-label="Audience">
          {#each AUDIENCES as a}
            <button
              class="aud-chip"
              class:active={audience === a}
              onclick={() => setAudience(a)}
              type="button"
            >{a}</button>
          {/each}
        </div>
        {#if audienceHint}
          <div class="audience-hint" role="status">{audienceHint}</div>
        {/if}
      </div>
    </div>
  </header>

  <!-- Two-pane body -->
  <div class="studio-body">
    <!-- LEFT 38–40%: chat thread -->
    <aside class="studio-left">
      <div class="studio-thread">
        <SuggestionCard
          {slug}
          refresh_token={suggestionRefreshToken}
          onuse={onSuggestionUse}
          onbrowse={openNewAgent}
        />
        {#if messages.length === 0}
          <div class="thread-empty">
            <div class="empty-title">Build a dashboard from a question.</div>
            <div class="empty-sub">Describe what you want to see. The agent plans panels, runs SQL, and renders the dashboard.</div>
            <div class="example-chips">
              {#each EXAMPLE_PROMPTS as p}
                <button class="example-chip" onclick={() => pickExample(p)} type="button">{p}</button>
              {/each}
            </div>
          </div>
        {:else}
          <ChatMessageList
            {messages}
            updateMessage={updateMessage}
            isStreaming={busy}
            routeLabel="studio"
            agentName="Studio"
          />
        {/if}
      </div>

      {#if stageStatus}
        <div class="status-pill" class:running={busy}>
          {#if busy}<span class="dot"></span>{/if}
          {stageStatus}
        </div>
      {/if}

      <div class="studio-composer">
        <textarea
          class="composer-input"
          placeholder={isFirstTurn() ? 'Build a dashboard about…' : 'Refine — e.g. "swap chart 2 to a bar"'}
          bind:value={composerText}
          onkeydown={onKey}
          rows="2"
          disabled={busy}
        ></textarea>
        <div class="composer-actions">
          {#if busy}
            <button class="composer-btn stop" onclick={onStop} type="button">STOP</button>
          {:else}
            <button
              class="composer-btn build"
              class:active={!!composerText.trim()}
              onclick={onSubmit}
              type="button"
              disabled={!composerText.trim()}
            >
              {isFirstTurn() ? 'BUILD' : 'REFINE'}
            </button>
          {/if}
        </div>
      </div>
    </aside>

    <!-- RIGHT 60–62%: dashboard canvas -->
    <main class="studio-right">
      {#if narrative}
        <header class="exec-overview">
          <h2 class="exec-h2">Executive Overview{narrative.audience ? ` — ${narrative.audience}` : ''}</h2>
          <p class="exec-p">{narrative.text}</p>
          {#if typeof narrative.verified_value_count === 'number' && narrative.verified_value_count > 0}
            <div class="exec-verified">✓ {narrative.verified_value_count} verified value{narrative.verified_value_count === 1 ? '' : 's'}</div>
          {/if}
        </header>
      {/if}

      <div class="studio-canvas">
        {#if (!Array.isArray(spec?.cells) || spec.cells.length === 0) && (!Array.isArray(spec?.panels) || spec.panels.length === 0) && !busy}
          <div class="canvas-empty">
            <div class="empty-glyph" aria-hidden="true">
              <svg width="48" height="48" viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="1.5">
                <rect x="4" y="4" width="18" height="18" />
                <rect x="26" y="4" width="18" height="10" />
                <rect x="26" y="18" width="18" height="26" />
                <rect x="4" y="26" width="18" height="18" />
              </svg>
            </div>
            <div class="empty-text">Panels appear here as they are built.</div>
          </div>
        {:else if (!Array.isArray(spec?.cells) || spec.cells.length === 0) && (!Array.isArray(spec?.panels) || spec.panels.length === 0) && busy}
          <!-- Loading shimmer for first stage -->
          <div class="canvas-shimmer">
            <div class="shim-row">
              <div class="shim shim-kpi"></div>
              <div class="shim shim-kpi"></div>
              <div class="shim shim-kpi"></div>
            </div>
            <div class="shim shim-chart"></div>
            <div class="shim shim-chart"></div>
          </div>
        {:else}
          <DashRenderer {spec} data={panelData} />
        {/if}
      </div>
    </main>

    {#if chatOpen && dashId}
      <div style="width: 360px; min-width: 360px; border-left: 1px solid var(--pw-border, #e2ddd2); background: var(--pw-bg, #fdfaf5); display: flex; flex-direction: column;">
        <DashChatPanel
          dashboardId={dashId}
          projectSlug={slug}
          panels={(spec?.panels && spec.panels.length ? spec.panels : spec?.cells) || []}
          onCitePanel={(n) => {
            const el = document.querySelector(`[data-panel-idx="${n - 1}"]`);
            if (el) (el as HTMLElement).scrollIntoView({ behavior: 'smooth', block: 'center' });
          }}
        />
      </div>
    {/if}
  </div>
</div>

<TemplateGalleryModal
  open={templateGalleryOpen}
  {slug}
  onclose={() => (templateGalleryOpen = false)}
  onselect={onTemplatePicked}
/>

{#if newAgentFormOpen}
  <NewAgentForm
    {slug}
    template={newAgentTemplate}
    onsave={onAgentSaved}
    oncancel={onAgentCancelled}
  />
{/if}

<style>
  .studio-root {
    position: fixed;
    inset: 56px 0 0 0;
    display: flex;
    flex-direction: column;
    background: var(--pw-bg, #fdfaf5);
    color: var(--pw-ink, #2c2a26);
    font-family: -apple-system, system-ui, sans-serif;
    overflow: hidden;
  }

  /* Topbar */
  .studio-topbar {
    flex-shrink: 0;
    height: 52px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 16px;
    background: var(--pw-bg, #fdfaf5);
    border-bottom: 1px solid var(--pw-border, #e2ddd2);
  }
  .topbar-left { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .back-btn {
    background: transparent;
    border: 1px solid var(--pw-border, #e2ddd2);
    color: var(--pw-ink, #2c2a26);
    width: 28px; height: 28px;
    cursor: pointer;
    border-radius: var(--pw-radius-sm, 4px);
    font-size: 14px;
    line-height: 1;
  }
  .back-btn:hover { background: var(--pw-bg-alt, #f7f6f3); }
  .breadcrumb {
    display: flex; align-items: center; gap: 6px;
    font-size: 13px;
    color: var(--pw-muted, #999);
    min-width: 0; overflow: hidden;
  }
  .breadcrumb a {
    color: var(--pw-muted, #999);
    text-decoration: none;
  }
  .breadcrumb a:hover { color: var(--pw-accent, #c96342); }
  .bc-sep { color: var(--pw-muted, #999); opacity: 0.6; }
  .bc-current { color: var(--pw-ink, #2c2a26); font-weight: 600; }

  .topbar-right { display: flex; align-items: center; gap: 8px; position: relative; }
  .audience-wrap { position: relative; display: inline-flex; align-items: center; }
  .audience-hint {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    font-size: 11px;
    color: var(--pw-accent, #c96342);
    background: rgba(201, 99, 66, 0.08);
    border: 1px solid rgba(201, 99, 66, 0.25);
    border-radius: var(--pw-radius-sm, 4px);
    padding: 4px 8px;
    white-space: nowrap;
    z-index: 5;
    animation: aud-fade 0.15s ease-out;
  }
  @keyframes aud-fade {
    from { opacity: 0; transform: translateY(-2px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .audience-group {
    display: inline-flex;
    border: 1px solid var(--pw-border, #e2ddd2);
    border-radius: var(--pw-radius-sm, 4px);
    overflow: hidden;
  }
  .aud-chip {
    background: transparent;
    color: var(--pw-ink, #2c2a26);
    border: none;
    border-right: 1px solid var(--pw-border, #e2ddd2);
    padding: 6px 12px;
    font-size: 11.5px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    cursor: pointer;
    font-family: inherit;
  }
  .aud-chip:last-child { border-right: none; }
  .aud-chip:hover { background: var(--pw-bg-alt, #f7f6f3); }
  .aud-chip.active {
    background: rgba(201, 99, 66, 0.14);
    color: var(--pw-accent, #c96342);
    font-weight: 600;
  }

  /* Body split */
  .studio-body {
    flex: 1;
    display: grid;
    grid-template-columns: 38% 62%;
    min-height: 0;
    overflow: hidden;
  }

  /* LEFT pane */
  .studio-left {
    display: flex;
    flex-direction: column;
    background: var(--pw-bg-alt, #f7f6f3);
    border-right: 1px solid var(--pw-border, #e2ddd2);
    min-width: 0;
    min-height: 0;
  }
  .studio-thread {
    flex: 1;
    overflow-y: auto;
    padding: 16px 18px;
    min-height: 0;
  }
  .thread-empty {
    padding: 32px 8px;
    color: var(--pw-muted, #999);
  }
  .empty-title {
    font-size: 15px;
    color: var(--pw-ink, #2c2a26);
    font-weight: 600;
    margin-bottom: 6px;
  }
  .empty-sub {
    font-size: 13px;
    line-height: 1.55;
    margin-bottom: 16px;
  }
  .example-chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .example-chip {
    background: var(--pw-bg, #fdfaf5);
    color: var(--pw-ink, #2c2a26);
    border: 1px solid var(--pw-border, #e2ddd2);
    padding: 6px 10px;
    font-size: 12px;
    border-radius: var(--pw-radius-sm, 4px);
    cursor: pointer;
    font-family: inherit;
  }
  .example-chip:hover {
    border-color: var(--pw-accent, #c96342);
    color: var(--pw-accent, #c96342);
    background: rgba(201, 99, 66, 0.04);
  }

  /* Status pill */
  .status-pill {
    flex-shrink: 0;
    margin: 0 18px 8px;
    padding: 6px 10px;
    font-size: 11.5px;
    color: var(--pw-muted, #999);
    background: var(--pw-bg, #fdfaf5);
    border: 1px solid var(--pw-border, #e2ddd2);
    border-radius: var(--pw-radius-sm, 4px);
    display: inline-flex;
    align-items: center;
    gap: 6px;
    width: fit-content;
  }
  .status-pill.running {
    color: var(--pw-accent, #c96342);
    border-color: rgba(201, 99, 66, 0.3);
    background: rgba(201, 99, 66, 0.06);
  }
  .status-pill .dot {
    width: 6px; height: 6px;
    background: var(--pw-accent, #c96342);
    border-radius: 50%;
    animation: pulse 1.2s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 0.4; }
    50% { opacity: 1; }
  }

  /* Composer */
  .studio-composer {
    flex-shrink: 0;
    padding: 12px 16px 14px;
    background: var(--pw-bg-alt, #f7f6f3);
    border-top: 1px solid var(--pw-border, #e2ddd2);
    display: flex;
    gap: 10px;
    align-items: flex-end;
  }
  .composer-input {
    flex: 1;
    min-height: 52px;
    max-height: 180px;
    resize: vertical;
    border: 1px solid var(--pw-border, #e2ddd2);
    background: var(--pw-bg, #fdfaf5);
    color: var(--pw-ink, #2c2a26);
    font-family: inherit;
    font-size: 13.5px;
    padding: 10px 12px;
    border-radius: var(--pw-radius-sm, 4px);
    line-height: 1.45;
  }
  .composer-input:focus {
    outline: none;
    border-color: var(--pw-accent, #c96342);
    box-shadow: 0 0 0 2px rgba(201, 99, 66, 0.12);
  }
  .composer-actions { display: flex; align-items: center; }
  .composer-btn {
    border: 1px solid var(--pw-border, #e2ddd2);
    background: var(--pw-bg, #fdfaf5);
    color: var(--pw-muted, #999);
    border-radius: var(--pw-radius-sm, 4px);
    padding: 10px 18px;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    cursor: not-allowed;
    font-family: inherit;
    transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease;
  }
  .composer-btn.build.active {
    background: var(--pw-accent, #c96342);
    color: #fff;
    border-color: var(--pw-accent, #c96342);
    cursor: pointer;
  }
  .composer-btn.build.active:hover { filter: brightness(1.06); }
  .composer-btn.stop {
    background: #c0392b;
    color: #fff;
    border-color: #c0392b;
    cursor: pointer;
  }

  /* RIGHT pane */
  .studio-right {
    display: flex;
    flex-direction: column;
    background: var(--pw-bg, #fdfaf5);
    overflow-y: auto;
    min-width: 0;
    min-height: 0;
  }
  .exec-overview {
    padding: 22px 32px 14px;
    border-bottom: 1px solid var(--pw-border, #e2ddd2);
    background: var(--pw-bg, #fdfaf5);
  }
  .exec-h2 {
    font-family: "Source Serif Pro", Georgia, serif;
    font-size: 24px;
    font-weight: 700;
    color: var(--pw-accent, #c96342);
    margin: 0 0 10px 0;
    letter-spacing: -0.01em;
  }
  .exec-p {
    font-family: "Source Serif Pro", Georgia, serif;
    font-size: 15px;
    line-height: 1.6;
    color: var(--pw-ink, #2c2a26);
    margin: 0;
    max-width: 78ch;
  }
  .exec-verified {
    margin-top: 10px;
    display: inline-block;
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--pw-accent, #c96342);
    background: rgba(201, 99, 66, 0.1);
    padding: 3px 9px;
    border: 1px solid rgba(201, 99, 66, 0.25);
    border-radius: var(--pw-radius-sm, 4px);
  }

  .studio-canvas { padding: 18px 24px 32px; }
  .canvas-empty {
    padding: 80px 20px;
    text-align: center;
    color: var(--pw-muted, #999);
  }
  .empty-glyph {
    color: var(--pw-border, #e2ddd2);
    line-height: 0;
    margin-bottom: 14px;
  }
  .empty-text { font-size: 13px; }

  /* Shimmer */
  .canvas-shimmer {
    padding: 8px 0;
    display: flex; flex-direction: column; gap: 14px;
  }
  .shim-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
  .shim {
    background: linear-gradient(90deg, var(--pw-bg-alt, #f7f6f3) 0%, #fff 50%, var(--pw-bg-alt, #f7f6f3) 100%);
    background-size: 200% 100%;
    border: 1px solid var(--pw-border, #e2ddd2);
    border-radius: var(--pw-radius-sm, 4px);
    animation: shimmer 1.4s ease-in-out infinite;
  }
  .shim-kpi { height: 88px; }
  .shim-chart { height: 220px; }
  @keyframes shimmer {
    0% { background-position: 200% 0; }
    100% { background-position: -200% 0; }
  }

  @media (max-width: 960px) {
    .studio-body { grid-template-columns: 100%; grid-template-rows: 50% 50%; }
    .studio-left { border-right: none; border-bottom: 1px solid var(--pw-border, #e2ddd2); }
  }
</style>
