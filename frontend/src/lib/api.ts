const API_BASE = '';

// ── Scope helpers ───────────────────────────────────────────────
export interface ActiveScope { id: string; label: string; }

export function getActiveScope(): ActiveScope | null {
  if (typeof localStorage === 'undefined') return null;
  const id = localStorage.getItem('dash_scope_id');
  const label = localStorage.getItem('dash_scope_label');
  if (!id) return null;
  return { id, label: label || id };
}

export function setScope(id: string, label: string): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.setItem('dash_scope_id', id);
  localStorage.setItem('dash_scope_label', label);
}

export function clearScope(): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.removeItem('dash_scope_id');
  localStorage.removeItem('dash_scope_label');
}

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  if (typeof localStorage === 'undefined') return headers;
  const token = localStorage.getItem('dash_token');
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const scopeId = localStorage.getItem('dash_scope_id');
  if (scopeId) headers['X-Scope-Id'] = scopeId;
  return headers;
}

/** Fetch wrapper that auto-injects Authorization + X-Scope-Id headers. */
export async function dashFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = authHeaders((init.headers as Record<string, string>) || {});
  return fetch(input, { ...init, headers });
}

export interface ToolCall {
  name: string;
  status: 'running' | 'done' | 'error';
  args?: Record<string, unknown>;
  duration?: string;
  sqlQuery?: string;
  tokens?: { input: number; output: number };
  /** Member agent that owns this tool (e.g. "Analyst", "Engineer"). */
  agentName?: string;
  /** Hint to UI: render with indent under owning agent row. */
  isMemberTool?: boolean;
}

/** A single ordered entry in the reasoning trace (steps + data-tool calls). */
export type TraceItem =
  | { kind: 'step'; id: string; title: string; text: string; agent?: string; model?: string; tokensIn?: number; tokensOut?: number }
  | {
      kind: 'tool';
      id: string;
      name: string;
      args: any;
      result?: string;
      status: 'run' | 'done' | 'err';
      agent?: string;
      cost?: number;
      est_rows?: number;
      model?: string;
      duration?: string | number;
      tokensIn?: number;
      tokensOut?: number;
      /** chat-time SQL validator auto-fix metadata (forward-compatible hook).
       *  Backend may emit `validator_fix` / `auto_fix` on tool_result for
       *  run_sql_query — surfaced as a coral pill in TraceTimeline. */
      auto_fix?: string[];
    };

/** Running usage totals emitted by Usage events. */
export interface TraceUsage {
  input_tokens: number;
  output_tokens: number;
  model?: string;
}

/** Tool names that are really "thinking" — surfaced as steps, not data tools. */
const REASONING_TOOL_NAMES = new Set(['think', 'analyze', 'reason', 'reasoning']);

/** Truncate a value to ~200 chars for compact trace display. */
function _truncTrace(v: unknown, max = 200): string {
  let s: string;
  if (typeof v === 'string') s = v;
  else {
    try { s = JSON.stringify(v); } catch { s = String(v); }
  }
  if (s == null) return '';
  return s.length > max ? s.slice(0, max) + '…' : s;
}

export async function sendMessage(
  message: string,
  sessionId: string,
  onToken: (text: string) => void,
  onDone: () => void,
  onError: (error: string) => void,
  onToolCall?: (tool: ToolCall) => void,
  projectSlug?: string,
  reasoning?: string,
  analysisType?: string,
  signal?: AbortSignal,
  onTrace?: (item: TraceItem) => void,
  onUsage?: (u: TraceUsage) => void,
  mode?: string,
  onRouting?: (r: unknown) => void,
  modelPref?: string,
  effort?: string
): Promise<void> {
  try {
    const formData = new FormData();
    formData.append('message', message);
    formData.append('stream', 'true');
    formData.append('session_id', sessionId);
    if (reasoning) formData.append('reasoning', reasoning);
    if (analysisType) formData.append('analysis_type', analysisType);
    if (modelPref) formData.append('model_pref', modelPref);
    if (effort) formData.append('effort', effort);
    // OKF opt-in lane (test toggle). Read from a UI pref so the long positional
    // signature doesn't change. Default off → backend behaves identically.
    try { if (localStorage.getItem('cp_use_okf') === '1') formData.append('use_okf', '1'); } catch {}
    // Global (no-slug) chat = super-chat: carry the routing mode selector.
    if (!projectSlug && mode) formData.append('mode', mode);

    const headers: Record<string, string> = authHeaders();

    // Project chat is slug-scoped; the no-slug path is the global super-chat
    // (cross-project auto-routing). Both speak the same SSE event protocol.
    const endpoint = projectSlug ? `${API_BASE}/api/projects/${projectSlug}/chat` : `${API_BASE}/api/super-chat`;
    const response = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: formData,
      signal
    });

    if (!response.ok) {
      const text = await response.text();
      onError(`Error ${response.status}: ${text}`);
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) { onError('No response body'); return; }

    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = '';
    const toolTimers: Record<string, number> = {};
    // Reasoning-trace bookkeeping (independent of the legacy onToolCall path).
    // Map of pending data-tool id|name → true, plus a set of step ids/text we've
    // already emitted so a think-tool + ReasoningStep of same content dedupes.
    const tracePendingTools = new Map<string, true>();
    const traceSeenSteps = new Set<string>();
    // Track current member-agent so member-tool events can be tagged.
    let currentMember: string | null = null;
    // Track which agents have a running spinner row.
    const runningAgents = new Set<string>();
    // Running token totals + last model seen (from Usage events).
    let usageIn = 0;
    let usageOut = 0;
    let usageModel: string | undefined;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
          continue;
        }

        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (raw === '[DONE]') continue;

        try {
          const data = JSON.parse(raw);

          // Normalize event name. Agno emits CamelCase; older variants emit
          // snake_case. Treat both uniformly.
          const norm = (currentEvent || '').toLowerCase();
          // Helper: extract member-agent name from any event shape.
          const eventAgent: string | undefined =
            data.agent_name ||
            data.member_name ||
            data.member_id ||
            (data.tool && typeof data.tool === 'object' ? data.tool.agent_name : undefined);
          const isStarted = norm === 'toolcallstarted' || norm === 'tool_call_started' ||
            norm === 'teamtoolcallstarted' || norm === 'team_tool_call_started' ||
            norm === 'membertoolcallstarted' || norm === 'member_tool_call_started';
          const isCompleted = norm === 'toolcallcompleted' || norm === 'tool_call_completed' ||
            norm === 'teamtoolcallcompleted' || norm === 'team_tool_call_completed' ||
            norm === 'membertoolcallcompleted' || norm === 'member_tool_call_completed';
          const isTeamScope = norm.includes('teamtool') || norm.includes('team_tool');

          // ── Reasoning-trace emission (fail-soft, independent of onToolCall) ──
          if (onTrace) {
            try {
              const isReasoningStep = norm === 'reasoningstep' || norm === 'reasoning_step';
              if (isReasoningStep) {
                const title = String(data.title ?? data.reasoning_title ?? 'reasoning').trim() || 'reasoning';
                const text = String(data.content ?? data.text ?? '');
                const stepId = String(data.id ?? data.step_id ?? `step_${title}_${text.slice(0, 24)}`);
                const dedupeKey = `${stepId}|${text.slice(0, 64)}`;
                if (!traceSeenSteps.has(dedupeKey)) {
                  traceSeenSteps.add(dedupeKey);
                  onTrace({ kind: 'step', id: stepId, title, text, agent: eventAgent });
                }
              } else if (isStarted && data.tool && typeof data.tool === 'object') {
                const tool = data.tool;
                const name = String(tool.tool_name || tool.name || 'unknown');
                const id = String(tool.id ?? tool.tool_call_id ?? `${name}_${eventAgent || ''}`);
                const args = tool.tool_args || tool.args || {};
                if (REASONING_TOOL_NAMES.has(name.toLowerCase())) {
                  // A "think"-style tool — surface as a step, deduped.
                  const text = _truncTrace(args, 400);
                  const dedupeKey = `${id}|${text.slice(0, 64)}`;
                  if (!traceSeenSteps.has(dedupeKey)) {
                    traceSeenSteps.add(dedupeKey);
                    onTrace({ kind: 'step', id, title: name, text, agent: eventAgent });
                  }
                } else {
                  tracePendingTools.set(id, true);
                  onTrace({ kind: 'tool', id, name, args, status: 'run', agent: eventAgent });
                }
              } else if (isCompleted && data.tool && typeof data.tool === 'object') {
                const tool = data.tool;
                const name = String(tool.tool_name || tool.name || 'unknown');
                const id = String(tool.id ?? tool.tool_call_id ?? `${name}_${eventAgent || ''}`);
                const args = tool.tool_args || tool.args || {};
                if (REASONING_TOOL_NAMES.has(name.toLowerCase())) {
                  const text = _truncTrace(tool.result ?? args, 400);
                  const dedupeKey = `${id}|${text.slice(0, 64)}`;
                  if (!traceSeenSteps.has(dedupeKey)) {
                    traceSeenSteps.add(dedupeKey);
                    onTrace({ kind: 'step', id, title: name, text, agent: eventAgent });
                  }
                } else {
                  const result = _truncTrace(tool.result ?? tool.output ?? '', 1200);
                  // Optional extras carried on the tool object (fail-soft: only set when numeric/present).
                  const num = (v: unknown): number | undefined =>
                    typeof v === 'number' && !isNaN(v) ? v : undefined;
                  const cost = num(tool.cost);
                  const est_rows = num(tool.est_rows);
                  const tokensIn = num(tool.input_tokens ?? tool.tokens_in);
                  const tokensOut = num(tool.output_tokens ?? tool.tokens_out);
                  const model = typeof tool.model === 'string' ? tool.model : undefined;
                  const duration =
                    typeof tool.duration === 'string' || typeof tool.duration === 'number'
                      ? tool.duration
                      : undefined;
                  // ── chat-time SQL validator auto-fix extraction (forward-compatible) ──
                  // Backend may emit `validator_fix` / `auto_fix` on the tool result
                  // (string OR list of fix descriptions) when sql_validator auto-casts
                  // text columns etc. Fail-soft: only attached when present + list-shaped.
                  let auto_fix: string[] | undefined = undefined;
                  try {
                    const rawResult = tool.result ?? tool.output;
                    let parsed: any = null;
                    if (rawResult && typeof rawResult === 'object') parsed = rawResult;
                    else if (typeof rawResult === 'string' && rawResult.trim().startsWith('{')) {
                      try { parsed = JSON.parse(rawResult); } catch { parsed = null; }
                    }
                    const fx = (parsed && (parsed.validator_fix ?? parsed.auto_fix))
                            ?? (tool as any).validator_fix
                            ?? (tool as any).auto_fix;
                    if (Array.isArray(fx) && fx.length) {
                      auto_fix = fx.map((x) => String(x)).filter(Boolean);
                    } else if (typeof fx === 'string' && fx.trim()) {
                      auto_fix = [fx.trim()];
                    }
                  } catch { /* fail-soft */ }
                  // Re-emit with same id so the consumer can match the pending tool.
                  // (Push-order is preserved; consumer updates in place by id.)
                  onTrace({
                    kind: 'tool', id, name, args, result, status: 'done', agent: eventAgent,
                    cost, est_rows, model, duration, tokensIn, tokensOut,
                    ...(auto_fix ? { auto_fix } : {}),
                  });
                  tracePendingTools.delete(id);
                }
              }
            } catch {
              // malformed trace event — skip, never throw
            }
          }

          // ── Usage events (running token totals + last model) ──
          if (norm === 'usage') {
            try {
              const inc = (v: unknown): number =>
                typeof v === 'number' && !isNaN(v) ? v : Number(v) || 0;
              usageIn += inc(data.input_tokens);
              usageOut += inc(data.output_tokens);
              if (typeof data.model === 'string' && data.model) usageModel = data.model;
              if (onUsage) onUsage({ input_tokens: usageIn, output_tokens: usageOut, model: usageModel });
            } catch {
              // malformed usage event — skip, never throw
            }
          }

          if (isStarted && data.tool && onToolCall) {
            const tool = data.tool;
            const name = tool.tool_name || tool.name || 'unknown';
            const timerKey = isTeamScope ? `team_${name}` : `${eventAgent || ''}_${name}`;
            toolTimers[timerKey] = Date.now();
            // Capture sqlQuery at START so the live-SQL UI block can render before completion.
            const SQL_TOOL_NAMES_START = new Set([
              'run_sql_query', 'run_sql', 'execute_sql', 'execute_sql_query',
              'sql_query', 'query_db', 'read_query', 'query', 'sql'
            ]);
            const args = tool.tool_args || tool.args || {};
            const rawArgStart = args.query || args.sql || args.statement || args.sql_query || args.q;
            const looksLikeSqlStart = typeof rawArgStart === 'string'
              && /\b(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|EXPLAIN)\b/i.test(rawArgStart);
            const sqlQueryStart = (SQL_TOOL_NAMES_START.has(name) || looksLikeSqlStart) ? rawArgStart : undefined;
            onToolCall({
              name,
              status: 'running',
              args,
              sqlQuery: sqlQueryStart,
              agentName: eventAgent,
              isMemberTool: !isTeamScope && !!eventAgent,
            });
          } else if (isCompleted && data.tool && onToolCall) {
            const tool = data.tool;
            const name = tool.tool_name || tool.name || 'unknown';
            const timerKey = isTeamScope ? `team_${name}` : `${eventAgent || ''}_${name}`;
            const start = toolTimers[timerKey];
            const duration = start ? ((Date.now() - start) / 1000).toFixed(2) + 's' : undefined;
            const SQL_TOOL_NAMES = new Set([
              'run_sql_query', 'run_sql', 'execute_sql', 'execute_sql_query',
              'sql_query', 'query_db', 'read_query', 'query', 'sql'
            ]);
            const rawArg = tool.tool_args?.query
              || tool.tool_args?.sql
              || tool.tool_args?.statement
              || tool.tool_args?.sql_query
              || tool.tool_args?.q;
            const looksLikeSql = typeof rawArg === 'string'
              && /\b(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|EXPLAIN)\b/i.test(rawArg);
            const sqlQuery = (SQL_TOOL_NAMES.has(name) || looksLikeSql) ? rawArg : undefined;
            onToolCall({
              name,
              status: 'done',
              duration,
              sqlQuery,
              agentName: eventAgent,
              isMemberTool: !isTeamScope && !!eventAgent,
            });
          } else {
            switch (currentEvent) {
              case 'Routing': {
                // Auto-router decision (global super-chat only).
                if (onRouting) onRouting(data);
                break;
              }
              case 'RouterDecision': {
                // Complexity router (Feature A): {tier, model, score, signals, reason, cached}.
                if (onRouting) onRouting(data);
                break;
              }

              case 'RunStarted': {
                if (data.agent_name && onToolCall) {
                  currentMember = data.agent_name;
                  runningAgents.add(data.agent_name);
                  onToolCall({
                    name: `${data.agent_name} agent`,
                    status: 'running',
                    agentName: data.agent_name,
                  });
                }
                break;
              }

              case 'RunCompleted': {
                if (data.agent_name && onToolCall) {
                  runningAgents.delete(data.agent_name);
                  if (currentMember === data.agent_name) currentMember = null;
                  onToolCall({
                    name: `${data.agent_name} agent`,
                    status: 'done',
                    agentName: data.agent_name,
                  });
                }
                break;
              }

              case 'TeamRunContent':
              case 'RunContent': {
                if (data.content && typeof data.content === 'string') {
                  onToken(data.content);
                }
                break;
              }

              default:
                break;
            }
          }
        } catch {
          // Not JSON — skip
        }
      }
    }
    onDone();
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Connection failed';
    // User-initiated abort: don't surface as red error. Caller's onDone-after-stop
    // path repaints the bubble cleanly.
    if (err instanceof DOMException && err.name === 'AbortError') { onDone(); return; }
    if (/aborted|BodyStreamBuffer/i.test(msg)) { onDone(); return; }
    onError(msg);
  }
}

export function generateSessionId(): string {
  return crypto.randomUUID();
}

// ── Per-user Agent System ───────────────────────────────────────

export interface UserAgent {
  agent_id: string;
  state: 'building' | 'ready' | 'training' | 'archived' | 'error';
  enabled: boolean;
  persona: Record<string, unknown>;
  version: number;
  last_sync?: string;
  memory_count?: number;
}

export interface AgentMemoryEvent {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  ts: string;
}

export interface SimRun {
  sim_id: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  progress: number;
  scenario: string;
  horizon?: string;
  report_md?: string;
  result_json?: Record<string, unknown>;
  error?: string;
  created_at: string;
}

export async function agentGet(): Promise<UserAgent | null> {
  const res = await dashFetch(`${API_BASE}/api/agents/me`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`agentGet failed: ${res.status}`);
  return await res.json();
}

export async function agentBootstrap(): Promise<UserAgent> {
  const res = await dashFetch(`${API_BASE}/api/agents/bootstrap`, { method: 'POST' });
  if (!res.ok) throw new Error(`agentBootstrap failed: ${res.status}`);
  return await res.json();
}

export async function agentEnable(enabled: boolean): Promise<UserAgent> {
  const res = await dashFetch(`${API_BASE}/api/agents/me/enable`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  });
  if (!res.ok) throw new Error(`agentEnable failed: ${res.status}`);
  return await res.json();
}

export async function agentTrain(): Promise<void> {
  const res = await dashFetch(`${API_BASE}/api/agents/me/train`, { method: 'POST' });
  if (!res.ok) throw new Error(`agentTrain failed: ${res.status}`);
}

export async function agentDelete(): Promise<void> {
  const res = await dashFetch(`${API_BASE}/api/agents/me`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`agentDelete failed: ${res.status}`);
}

export async function agentMemory(
  cursor?: string,
  limit?: number
): Promise<{ events: AgentMemoryEvent[]; next_cursor?: string }> {
  const params = new URLSearchParams();
  if (cursor) params.set('cursor', cursor);
  if (limit !== undefined) params.set('limit', String(limit));
  const qs = params.toString();
  const url = `${API_BASE}/api/agents/me/memory${qs ? `?${qs}` : ''}`;
  const res = await dashFetch(url);
  if (!res.ok) throw new Error(`agentMemory failed: ${res.status}`);
  return await res.json();
}

export async function agentRecommendations(): Promise<string[]> {
  const res = await dashFetch(`${API_BASE}/api/agents/me/recommendations`);
  if (!res.ok) throw new Error(`agentRecommendations failed: ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : (data.recommendations || []);
}

export async function agentChat(
  message: string,
  onToken: (t: string) => void,
  onDone: () => void,
  onError: (e: string) => void,
  signal?: AbortSignal
): Promise<void> {
  try {
    const response = await fetch(`${API_BASE}/api/agents/me/chat`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ message }),
      signal
    });

    if (!response.ok) {
      const text = await response.text();
      onError(`Error ${response.status}: ${text}`);
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) { onError('No response body'); return; }

    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
          continue;
        }

        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (raw === '[DONE]') continue;

        try {
          const data = JSON.parse(raw);

          switch (currentEvent) {
            case 'Token': {
              if (data.content && typeof data.content === 'string') {
                onToken(data.content);
              }
              break;
            }
            case 'Done': {
              onDone();
              return;
            }
            case 'Error': {
              onError(typeof data === 'string' ? data : (data.error || data.message || 'Agent error'));
              return;
            }
            default:
              break;
          }
        } catch {
          // Not JSON — skip
        }
      }
    }
    onDone();
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Connection failed';
    if (err instanceof DOMException && err.name === 'AbortError') { onDone(); return; }
    if (/aborted|BodyStreamBuffer/i.test(msg)) { onDone(); return; }
    onError(msg);
  }
}

export async function simRun(
  scenario: string,
  horizon: string,
  seed_tables: string[],
  actors: number
): Promise<{ sim_id: string }> {
  const res = await dashFetch(`${API_BASE}/api/agents/sim`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario, horizon, seed_tables, actors })
  });
  if (!res.ok) throw new Error(`simRun failed: ${res.status}`);
  return await res.json();
}

export async function simGet(sim_id: string): Promise<SimRun> {
  const res = await dashFetch(`${API_BASE}/api/agents/sim/${sim_id}`);
  if (!res.ok) throw new Error(`simGet failed: ${res.status}`);
  return await res.json();
}

export async function simList(): Promise<SimRun[]> {
  const res = await dashFetch(`${API_BASE}/api/agents/sim`);
  if (!res.ok) throw new Error(`simList failed: ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : (data.sims || []);
}

// ── Sim Projects (MiroFish-style multi-step simulation) ─────────
export interface SimProject {
  id: string;
  name: string;
  scenario: string;
  status: string;
  current_step: number;
  ontology_json?: any;
  graph_stats?: any;
  personas?: any[];
  timeline?: any[];
  report_md?: string;
  created_at: string;
}

export async function simProjectCreate(body: {
  name: string;
  scenario: string;
  project_slug?: string;
  config?: Record<string, unknown>;
}): Promise<SimProject> {
  const res = await dashFetch(`${API_BASE}/api/sim/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`simProjectCreate failed: ${res.status}`);
  return await res.json();
}

export async function simProjectList(): Promise<SimProject[]> {
  const res = await dashFetch(`${API_BASE}/api/sim/projects`);
  if (!res.ok) throw new Error(`simProjectList failed: ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : (data.projects || []);
}

export async function simProjectGet(id: string): Promise<SimProject> {
  const res = await dashFetch(`${API_BASE}/api/sim/projects/${id}`);
  if (!res.ok) throw new Error(`simProjectGet failed: ${res.status}`);
  return await res.json();
}

export async function simProjectDelete(id: string): Promise<void> {
  const res = await dashFetch(`${API_BASE}/api/sim/projects/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`simProjectDelete failed: ${res.status}`);
}

export async function simProjectRun(id: string): Promise<void> {
  const res = await dashFetch(`${API_BASE}/api/sim/projects/${id}/run`, { method: 'POST' });
  if (!res.ok) throw new Error(`simProjectRun failed: ${res.status}`);
}

export async function simProjectRunStep(id: string, n: number): Promise<any> {
  const res = await dashFetch(`${API_BASE}/api/sim/projects/${id}/step/${n}`, { method: 'POST' });
  if (!res.ok) throw new Error(`simProjectRunStep failed: ${res.status}`);
  return await res.json().catch(() => ({}));
}

export async function simProjectGraph(id: string): Promise<{ nodes: any[]; edges: any[] }> {
  const res = await dashFetch(`${API_BASE}/api/sim/projects/${id}/graph`);
  if (!res.ok) throw new Error(`simProjectGraph failed: ${res.status}`);
  const data = await res.json();
  return { nodes: data.nodes || [], edges: data.edges || data.links || [] };
}

export async function simProjectSteps(id: string): Promise<any[]> {
  const res = await dashFetch(`${API_BASE}/api/sim/projects/${id}/steps`);
  if (!res.ok) throw new Error(`simProjectSteps failed: ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : (data.steps || []);
}

// ── Agent OS Workflows Hub ──────────────────────────────────────
export interface AgentOsWorkflow {
  id: number;
  name: string;
  description?: string;
  status?: string;        // live | ready | paused | failed
  cron?: string;
  cron_label?: string;    // human-readable cron
  next_run_at?: string;
  last_run_at?: string;
  last_status?: string;   // ok | fail
  last_duration_s?: number;
  agent_slug?: string;
  agent_name?: string;
  agent_icon?: string;
  agent_role?: string;
  project_slug?: string;
  scope?: string;         // owned | shared
  role?: string;          // owner | editor | viewer
  ownership?: string;
  cost_cap_usd?: number;
  actions?: string[];
}

export interface AgentOsWorkflowGroup {
  agent_slug: string;
  agent_name: string;
  agent_icon?: string;
  agent_role?: string;
  scope: string;          // owned | shared
  role?: string;
  workflows: AgentOsWorkflow[];
}

export interface AgentOsWorkflowsResponse {
  groups: AgentOsWorkflowGroup[];
  totals: {
    total: number;
    owned: number;
    shared: number;
    active: number;
    paused: number;
    failed: number;
  };
}

export async function listAgentOsWorkflows(filters: {
  status?: string;
  agent_slug?: string;
  search?: string;
  scope?: string;
} = {}): Promise<AgentOsWorkflowsResponse> {
  const params = new URLSearchParams();
  if (filters.status && filters.status !== 'all') params.set('status', filters.status);
  if (filters.agent_slug && filters.agent_slug !== 'all') params.set('agent_slug', filters.agent_slug);
  if (filters.search) params.set('search', filters.search);
  if (filters.scope && filters.scope !== 'all') params.set('scope', filters.scope);
  const qs = params.toString();
  const url = `${API_BASE}/api/agent-os/workflows${qs ? `?${qs}` : ''}`;
  const res = await dashFetch(url);
  if (!res.ok) {
    // Graceful fallback so the page still renders if backend hub endpoint is absent.
    return { groups: [], totals: { total: 0, owned: 0, shared: 0, active: 0, paused: 0, failed: 0 } };
  }
  const data = await res.json();
  let groups: AgentOsWorkflowGroup[] = [];
  if (Array.isArray(data?.groups)) {
    groups = data.groups.map((g: any) => ({
      agent_slug: g.agent_slug || g.slug || 'unknown',
      agent_name: g.agent_name || g.name || g.agent_slug || 'Unknown agent',
      agent_icon: g.agent_icon || g.icon,
      agent_role: g.agent_role || g.role_label,
      scope: g.scope || 'owned',
      role: g.role,
      workflows: Array.isArray(g.workflows) ? g.workflows : [],
    }));
  } else if (Array.isArray(data?.workflows)) {
    // Backend ships flat {workflows, stats} — group client-side by project_slug.
    const byAgent: Record<string, AgentOsWorkflowGroup> = {};
    for (const wf of data.workflows) {
      const key = wf.project_slug || 'unknown';
      if (!byAgent[key]) {
        byAgent[key] = {
          agent_slug: key,
          agent_name: wf.agent_name || wf.project_name || key,
          scope: wf.ownership || 'owned',
          role: wf.share_role,
          workflows: [],
        } as AgentOsWorkflowGroup;
      }
      byAgent[key].workflows.push(wf);
    }
    groups = Object.values(byAgent);
  }
  const totals = data?.totals || data?.stats || { total: 0, owned: 0, shared: 0, active: 0, paused: 0, failed: 0 };
  return { groups, totals };
}

export async function setWorkflowCron(wfId: number, body: {
  cron: string;
  enabled?: boolean;
  cost_cap_usd?: number;
  actions?: string[];
}): Promise<any> {
  const res = await dashFetch(`${API_BASE}/api/agent-os/workflows/${wfId}/cron`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`setWorkflowCron failed: ${res.status}`);
  return await res.json().catch(() => ({}));
}

export async function runWorkflowNow(wfId: number): Promise<{ run_id: string; stream_url: string }> {
  const res = await dashFetch(`${API_BASE}/api/agent-os/workflows/${wfId}/run`, { method: 'POST' });
  if (!res.ok) throw new Error(`runWorkflowNow failed: ${res.status}`);
  const body: any = await res.json().catch(() => ({}));
  const rid = body?.run_id;
  if (rid !== undefined && rid !== null && String(rid).length > 0) {
    const runId = String(rid);
    return { run_id: runId, stream_url: body.stream_url || `/api/agent-os/workflows/runs/${runId}/stream` };
  }
  return { run_id: '', stream_url: '' };
}

export async function pauseWorkflow(wfId: number): Promise<any> {
  const res = await dashFetch(`${API_BASE}/api/agent-os/workflows/${wfId}/pause`, { method: 'POST' });
  if (!res.ok) throw new Error(`pauseWorkflow failed: ${res.status}`);
  return await res.json().catch(() => ({}));
}

export async function resumeWorkflow(wfId: number): Promise<any> {
  const res = await dashFetch(`${API_BASE}/api/agent-os/workflows/${wfId}/resume`, { method: 'POST' });
  if (!res.ok) throw new Error(`resumeWorkflow failed: ${res.status}`);
  return await res.json().catch(() => ({}));
}

export interface AgentOsWorkflowRun {
  id: string;
  started_at: string;
  finished_at?: string;
  duration_s?: number;
  status: string;            // ok | fail | running
  steps_total?: number;
  steps_done?: number;
  cost_usd?: number;
  output_preview?: string;
  error?: string;
}

export async function getWorkflowHistory(wfId: number, limit = 20): Promise<AgentOsWorkflowRun[]> {
  const res = await dashFetch(`${API_BASE}/api/agent-os/workflows/${wfId}/history?limit=${limit}`);
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? data : (Array.isArray(data?.runs) ? data.runs : []);
}

export async function getWorkflowRunDetail(wfId: number, runId: string): Promise<any> {
  const res = await dashFetch(`${API_BASE}/api/agent-os/workflows/${wfId}/runs/${runId}`);
  if (!res.ok) throw new Error(`getWorkflowRunDetail failed: ${res.status}`);
  return await res.json();
}

// ── Schedule analysis as workflow (from chat bubble) ────────────────
export interface WorkflowFromChatStep {
  kind: 'sql' | 'agent';
  sql?: string;
  agent?: string;
  prompt?: string;
}

export interface WorkflowFromChatBody {
  project_slug: string;
  chat_msg_id?: string;
  name: string;
  description?: string;
  steps: WorkflowFromChatStep[];
  schedule_cron?: string | null;
  schedule_action?: string;
}

export async function createWorkflowFromChat(
  body: WorkflowFromChatBody,
): Promise<{ wf_id: number; project_slug: string }> {
  const res = await dashFetch(`${API_BASE}/api/agent-os/workflows/from-chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = '';
    try {
      const data = await res.json();
      detail = data?.detail || data?.message || JSON.stringify(data);
    } catch {
      try { detail = await res.text(); } catch {}
    }
    throw new Error(detail || `createWorkflowFromChat failed: ${res.status}`);
  }
  const data = await res.json();
  return { wf_id: data.wf_id, project_slug: data.project_slug };
}

export function presetToCron(
  preset: 'daily' | 'weekly' | 'monthly' | 'hourly' | 'custom' | 'none',
  opts: { time?: string; dow?: number; day?: number; every?: number; custom?: string } = {},
): string | null {
  if (preset === 'none') return null;
  if (preset === 'custom') return (opts.custom || '').trim() || null;
  const [hh, mm] = (opts.time || '02:00').split(':').map((v) => Number(v) || 0);
  if (preset === 'daily') return `${mm} ${hh} * * *`;
  if (preset === 'weekly') return `${mm} ${hh} * * ${opts.dow ?? 1}`;
  if (preset === 'monthly') return `${mm} ${hh} ${opts.day ?? 1} * *`;
  if (preset === 'hourly') return `0 */${opts.every ?? 6} * * *`;
  return null;
}
