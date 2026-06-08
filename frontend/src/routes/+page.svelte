<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { sendMessage, generateSessionId, markdownToHtml, parseMarkdownTables, tableToCsv, hasNumericData } from '$lib';
  import type { ToolCall } from '$lib/api';
  import type { ParsedTable } from '$lib/table-parser';
  import ChartView from '$lib/chart.svelte';

  interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
    timestamp: string;
    status?: 'streaming' | 'done' | 'error';
    suggestions?: string[];
    toolCalls?: ToolCall[];
    workflowExpanded?: boolean;
    sqlQueries?: string[];
    showSql?: boolean;
    showChart?: boolean;
  }

  let messages = $state<ChatMessage[]>([]);
  let inputText = $state('');
  let isStreaming = $state(false);
  let sessionId = $state('');
  let sessionStartTime = $state('');
  let messagesEl: HTMLDivElement;
  let textareaEl: HTMLTextAreaElement;
  let copiedIndex = $state(-1);

  // Sidebar
  let sidebarOpen = $state(false);
  let pastSessions = $state<{session_id: string; created_at: string; updated_at: string}[]>([]);

  function _headers(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  async function loadSessions() {
    try {
      const res = await fetch('/api/sessions', { headers: _headers() });
      if (res.ok) { const d = await res.json(); pastSessions = d.sessions || []; }
    } catch {}
  }

  function switchSession(sid: string) {
    sessionId = sid;
    localStorage.setItem('dash_session', sid);
    messages = [];
    sessionStartTime = getTimestamp();
    sidebarOpen = false;
    textareaEl?.focus();
  }

  function formatSessionTime(ts: string | null): string {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      const now = new Date();
      const diff = now.getTime() - d.getTime();
      if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
      if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch { return ''; }
  }

  let dynamicSuggestions = $state<string[]>([
    "What's our current MRR?",
    "Which plan has the highest churn rate?",
    "Show me revenue trends by plan",
    "Which customers are at risk of churning?"
  ]);

  async function loadDynamicSuggestions() {
    try {
      const token = localStorage.getItem('dash_token');
      const res = await fetch('/api/tables', { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      const data = await res.json();
      const tables = (data.tables || []).map((t: any) => t.name);
      if (tables.length === 0) return;

      const suggestions: string[] = [];
      for (const t of tables.slice(0, 6)) {
        const name = t.replace(/_/g, ' ');
        suggestions.push(`What are the key metrics in ${name}?`);
      }
      if (tables.length > 1) {
        suggestions.push(`How do ${tables[0].replace(/_/g, ' ')} relate to ${tables[1].replace(/_/g, ' ')}?`);
      }
      suggestions.push("What tables do I have?");
      dynamicSuggestions = suggestions.slice(0, 6);
    } catch {}
  }

  const followUpSuggestions: Record<string, string[]> = {
    mrr: ["Break down MRR by plan tier", "Show MRR trend over last 6 months", "What's our net revenue retention?"],
    churn: ["What are the top cancellation reasons?", "Which customers are at risk?", "Compare churn rates across plans"],
    revenue: ["Show revenue by acquisition source", "What's our average revenue per account?", "Calculate customer lifetime value"],
    default: ["Show me support quality metrics", "Create a monthly MRR view", "What's our customer health score?"]
  };

  function getTimestamp(): string {
    return new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
  }

  function getSuggestions(content: string): string[] {
    const lower = content.toLowerCase();
    if (lower.includes('mrr') || lower.includes('revenue')) return followUpSuggestions.mrr;
    if (lower.includes('churn') || lower.includes('cancel')) return followUpSuggestions.churn;
    if (lower.includes('trend') || lower.includes('growth')) return followUpSuggestions.revenue;
    return followUpSuggestions.default;
  }

  function getTotalDuration(tools: ToolCall[]): string {
    let total = 0;
    for (const t of tools) {
      if (t.duration) {
        total += parseFloat(t.duration);
      }
    }
    return total > 0 ? total.toFixed(1) + 's' : '';
  }

  function getAgentMode(tools: ToolCall[]): 'deep' | 'fast' {
    const names = tools.map(t => t.name.toLowerCase());
    if (names.some(n => n === 'think' || n === 'analyze')) return 'deep';
    // Also detect deep by number of SQL queries or total steps
    const sqlCount = names.filter(n => n.includes('sql') || n.includes('query')).length;
    if (sqlCount >= 2 || tools.length >= 7) return 'deep';
    return 'fast';
  }

  const DEEP_KEYWORDS = /\b(why|compare|explain|suggest|recommend|correlate|analyze|break down|what should|how can|investigate|diagnose|root cause)\b/i;

  function isComplexQuery(text: string): boolean {
    if (DEEP_KEYWORDS.test(text)) return true;
    if ((text.match(/\band\b/gi) || []).length >= 2) return true;
    if (text.split('?').length > 2) return true;
    return false;
  }

  onMount(() => {
    sessionId = localStorage.getItem('dash_session') || generateSessionId();
    localStorage.setItem('dash_session', sessionId);
    sessionStartTime = getTimestamp();
    textareaEl?.focus();
    loadDynamicSuggestions();
    loadSessions();
  });

  function newChat() {
    messages = [];
    sessionId = generateSessionId();
    localStorage.setItem('dash_session', sessionId);
    sessionStartTime = getTimestamp();
    textareaEl?.focus();
    loadSessions();
  }

  function toggleWorkflow(index: number) {
    const msg = messages[index];
    if (msg) {
      messages = [
        ...messages.slice(0, index),
        { ...msg, workflowExpanded: !msg.workflowExpanded },
        ...messages.slice(index + 1)
      ];
    }
  }

  async function scrollToBottom() {
    await tick();
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  async function copyMessage(index: number) {
    const msg = messages[index];
    if (msg) {
      await navigator.clipboard.writeText(msg.content);
      copiedIndex = index;
      setTimeout(() => { copiedIndex = -1; }, 2000);
    }
  }

  async function send(text?: string) {
    const msg = (text || inputText).trim();
    if (!msg || isStreaming) return;
    inputText = '';
    if (textareaEl) textareaEl.style.height = 'auto';

    const useDeep = isComplexQuery(msg);
    const apiMsg = useDeep
      ? `This is a complex question. Tell your specialist to reason step-by-step, consider multiple angles, and provide a thorough analysis with actionable recommendations. Question: ${msg}`
      : msg;

    messages = [...messages, { role: 'user', content: msg, timestamp: getTimestamp() }];
    isStreaming = true;
    await scrollToBottom();

    messages = [...messages, { role: 'assistant', content: '', timestamp: '', status: 'streaming', toolCalls: [], workflowExpanded: true }];
    await scrollToBottom();

    await sendMessage(
      apiMsg, sessionId,
      (token) => {
        const last = messages[messages.length - 1];
        if (last.role === 'assistant') {
          messages = [...messages.slice(0, -1), { ...last, content: last.content + token }];
        }
        scrollToBottom();
      },
      () => {
        const last = messages[messages.length - 1];
        if (last.role === 'assistant') {
          messages = [...messages.slice(0, -1), {
            ...last, timestamp: getTimestamp(), status: 'done',
            suggestions: getSuggestions(last.content), workflowExpanded: false
          }];
        }
        isStreaming = false;
        scrollToBottom();
        textareaEl?.focus();
      },
      (error) => {
        const last = messages[messages.length - 1];
        if (last.role === 'assistant') {
          messages = [...messages.slice(0, -1), { ...last, content: `Error: ${error}`, timestamp: getTimestamp(), status: 'error' }];
        }
        isStreaming = false;
        scrollToBottom();
      },
      (tool: ToolCall) => {
        const last = messages[messages.length - 1];
        if (last.role === 'assistant') {
          const existing = last.toolCalls || [];
          const idx = existing.findIndex(t => t.name === tool.name && t.status === 'running');
          let updated: ToolCall[];
          if (idx >= 0 && tool.status === 'done') {
            updated = [...existing];
            updated[idx] = { ...updated[idx], status: 'done', duration: tool.duration };
          } else if (tool.status === 'running') {
            updated = [...existing, tool];
          } else {
            updated = existing;
          }
          // Capture SQL queries
          const sqls = last.sqlQueries || [];
          if (tool.sqlQuery && !sqls.includes(tool.sqlQuery)) {
            sqls.push(tool.sqlQuery);
          }
          messages = [...messages.slice(0, -1), { ...last, toolCalls: updated, sqlQueries: sqls }];
        }
        scrollToBottom();
      }
    );
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }

  function autoResize() {
    if (textareaEl) {
      textareaEl.style.height = 'auto';
      textareaEl.style.height = Math.min(textareaEl.scrollHeight, 120) + 'px';
    }
  }
</script>

<div class="flex h-full">
  <!-- Sidebar -->
  {#if sidebarOpen}
    <div class="chat-sidebar">
      <div class="flex items-center justify-between p-3" style="border-bottom: 2px solid var(--pw-border, #e5ddcf);">
        <span style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em;">SESSIONS</span>
        <button onclick={() => sidebarOpen = false} style="background: none; border: none; cursor: pointer; font-size: 11px; font-weight: 900; color: var(--color-on-surface);">&#10005;</button>
      </div>
      <div style="padding: 8px;">
        <button class="sidebar-new-btn" onclick={() => { newChat(); sidebarOpen = false; }}>+ NEW CHAT</button>
      </div>
      <div class="sidebar-sessions">
        {#each pastSessions as s}
          <button class="sidebar-session" class:sidebar-session-active={s.session_id === sessionId} onclick={() => switchSession(s.session_id)}>
            <div style="font-size: 11px; font-weight: {s.session_id === sessionId ? 900 : 400}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              {s.session_id.slice(0, 8)}...
            </div>
            <div style="font-size: 11px; color: var(--color-on-surface-dim);">{formatSessionTime(s.updated_at)}</div>
          </button>
        {/each}
        {#if pastSessions.length === 0}
          <div style="padding: 12px; font-size: 11px; color: var(--color-on-surface-dim); text-align: center;">No sessions yet</div>
        {/if}
      </div>
    </div>
  {/if}

  <!-- Main chat -->
  <div class="flex flex-col" style="flex: 1; min-width: 0;">

  <!-- Sidebar toggle -->
  <button onclick={() => { sidebarOpen = !sidebarOpen; if (sidebarOpen) loadSessions(); }} class="sidebar-toggle-btn" title="Sessions">&#9776;</button>

  <div class="flex-1 overflow-y-auto" bind:this={messagesEl} style="padding: 20px 20px 16px 20px;">
    <div style="max-width: 820px; margin: 0 auto;">

      {#if sessionStartTime}
        <div class="flex justify-center mb-6 animate-fade-up">
          <div class="tag-label" style="font-size: 10px; padding: 4px 14px; letter-spacing: 0.12em;">
            SESSION STARTED: {sessionStartTime}
          </div>
        </div>
      {/if}

      {#if messages.length === 0}
        <div class="flex flex-col items-center animate-fade-up" style="padding-top: 60px;">
          <div style="text-align: center; margin-bottom: 40px;">
            <div style="font-size: 52px; font-weight: 900; letter-spacing: -0.02em; color: var(--color-on-surface); line-height: 1;">DASH</div>
            <div style="font-size: 11px; letter-spacing: 0.15em; color: var(--color-on-surface-dim); margin-top: 10px; text-transform: uppercase;">
              Self-learning data agent for SaaS analytics
            </div>
          </div>
          <div class="flex flex-wrap justify-center gap-2" style="max-width: 650px;">
            {#each dynamicSuggestions as prompt}
              <button class="prompt-card" onclick={() => send(prompt)} disabled={isStreaming}>{prompt}</button>
            {/each}
          </div>
        </div>
      {:else}
        <div class="flex flex-col gap-5">
          {#each messages as msg, i}
            {#if msg.role === 'user'}
              <div class="animate-fade-up">
                <div class="flex items-start justify-end gap-3">
                  <div class="bubble-user">{msg.content}</div>
                  <div class="avatar-user">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                  </div>
                </div>
                <div style="text-align: right; font-size: 10px; color: var(--color-on-surface-dim); margin-top: 4px; padding-right: 40px; letter-spacing: 0.05em; text-transform: uppercase;">
                  {msg.timestamp} &middot; READ
                </div>
              </div>
            {:else}
              <div class="animate-fade-up">
                <div class="flex items-start gap-3">
                  <div class="avatar-agent">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="0"/><path d="M12 2v4"/><circle cx="12" cy="7" r="1"/><path d="M7 15h0M17 15h0M9 18h6"/></svg>
                  </div>
                  <div style="flex: 1; min-width: 0;">
                    <div class="bubble-assistant">

                      <!-- Workflow: expanded (while streaming) or collapsed toggle (when done) -->
                      {#if (msg.toolCalls && msg.toolCalls.length > 0) || msg.status === 'streaming'}
                        {#if msg.status === 'done' && !msg.workflowExpanded && msg.toolCalls && msg.toolCalls.length > 0}
                          <!-- Collapsed workflow bar -->
                          <button class="workflow-toggle" onclick={() => toggleWorkflow(i)}>
                            <span style="color: var(--color-primary); font-weight: 900;">&#9654;</span>
                            <span class="tag-label" style="font-size: 11px;">AGENT WORKFLOW</span>
                            {#if getAgentMode(msg.toolCalls) === 'deep'}
                              <span class="mode-badge mode-badge-deep">DEEP</span>
                            {:else}
                              <span class="mode-badge mode-badge-fast">FAST</span>
                            {/if}
                            <span style="font-size: 10px; color: var(--color-on-surface-dim);">
                              {msg.toolCalls.length} steps {getTotalDuration(msg.toolCalls) ? '· ' + getTotalDuration(msg.toolCalls) : ''}
                            </span>
                          </button>
                        {:else}
                          <!-- Expanded workflow -->
                          <div class="workflow-panel">
                            <button class="workflow-toggle" onclick={() => msg.status === 'done' && toggleWorkflow(i)}>
                              {#if msg.status === 'done'}
                                <span style="color: var(--color-primary); font-weight: 900;">&#9660;</span>
                              {/if}
                              <span class="tag-label" style="font-size: 11px;">
                                {msg.status === 'streaming' ? 'DASH ANALYZING' : 'AGENT WORKFLOW'}
                              </span>
                              {#if msg.status === 'streaming'}
                                <span class="status-dots">
                                  <span style="background: var(--color-primary);"></span>
                                  <span style="background: var(--color-warning);"></span>
                                  <span style="background: var(--color-error);"></span>
                                </span>
                              {/if}
                            </button>
                            <div class="tool-calls-list">
                              {#if msg.toolCalls && msg.toolCalls.length > 0}
                                {#each msg.toolCalls as tool}
                                  <div class="tool-call-item">
                                    {#if tool.status === 'done'}
                                      <span style="color: var(--color-primary); font-weight: 700;">&#10003;</span>
                                    {:else}
                                      <span class="typing-indicator" style="display: inline-flex; gap: 2px;">
                                        <span style="width: 4px; height: 4px;"></span>
                                        <span style="width: 4px; height: 4px;"></span>
                                        <span style="width: 4px; height: 4px;"></span>
                                      </span>
                                    {/if}
                                    <span class="tool-name">{tool.name}</span>
                                    {#if tool.duration}
                                      <span class="tool-duration">{tool.duration}</span>
                                    {/if}
                                  </div>
                                {/each}
                              {:else if msg.status === 'streaming'}
                                <div class="tool-call-item">
                                  <span class="typing-indicator" style="display: inline-flex; gap: 2px;">
                                    <span style="width: 4px; height: 4px;"></span>
                                    <span style="width: 4px; height: 4px;"></span>
                                    <span style="width: 4px; height: 4px;"></span>
                                  </span>
                                  <span class="tool-name" style="color: var(--color-on-surface-dim);">Connecting to agent...</span>
                                </div>
                              {/if}
                            </div>
                          </div>
                        {/if}
                      {/if}

                      <!-- Response content -->
                      {#if msg.content}
                        <div class="prose-chat" style="{msg.toolCalls && msg.toolCalls.length > 0 ? 'margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--color-on-surface);' : ''}">
                          {@html markdownToHtml(msg.content)}
                        </div>
                        {#if msg.status === 'streaming'}
                          <div class="typing-indicator" style="margin-top: 8px;"><span></span><span></span><span></span></div>
                        {/if}
                      {/if}

                      <!-- Feedback bar -->
                      {#if msg.status === 'done' && msg.content}
                        <div class="flex items-center justify-between" style="margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--pw-border, #e5ddcf); opacity: 0.6;">
                          <div class="flex items-center gap-2" style="font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; font-weight: 700;">
                            HELPFUL?
                            <button class="feedback-btn" title="Helpful"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg></button>
                            <button class="feedback-btn" title="Not helpful"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg></button>
                          </div>
                          <button class="feedback-btn flex items-center gap-1" onclick={() => copyMessage(i)} style="font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; font-weight: 700;">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="0"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                            {copiedIndex === i ? 'COPIED' : 'COPY'}
                          </button>
                        </div>
                      {/if}
                    </div>

                    <!-- Chart + CSV for tables -->
                    {#if msg.status === 'done' && msg.content}
                      {@const tables = parseMarkdownTables(msg.content)}
                      {#if tables.length > 0}
                        <div class="flex gap-2 mt-2" style="padding-top: 6px;">
                          {#if hasNumericData(tables[0])}
                            <button class="feedback-btn flex items-center gap-1" onclick={() => { messages = [...messages.slice(0, i), { ...msg, showChart: !msg.showChart }, ...messages.slice(i + 1)]; }} style="font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 3px 8px;">
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18"/><path d="M7 17V13M12 17V8M17 17V11"/></svg>
                              {msg.showChart ? 'HIDE CHART' : 'VIEW CHART'}
                            </button>
                          {/if}
                          <button class="feedback-btn flex items-center gap-1" onclick={() => {
                            const csv = tableToCsv(tables[0]);
                            const blob = new Blob([csv], { type: 'text/csv' });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url; a.download = `dash-export-${Date.now()}.csv`; a.click();
                            URL.revokeObjectURL(url);
                          }} style="font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 3px 8px;">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                            CSV
                          </button>
                        </div>
                        {#if msg.showChart && hasNumericData(tables[0])}
                          <div class="ink-border mt-2" style="background: var(--color-surface-bright); padding: 8px;">
                            <ChartView headers={tables[0].headers} rows={tables[0].rows} />
                          </div>
                        {/if}
                      {/if}
                    {/if}

                    <!-- SQL Queries Used -->
                    {#if msg.status === 'done' && msg.sqlQueries && msg.sqlQueries.length > 0}
                      <button class="sql-toggle" onclick={() => { messages = [...messages.slice(0, i), { ...msg, showSql: !msg.showSql }, ...messages.slice(i + 1)]; }}>
                        <span style="font-size: 10px;">{msg.showSql ? '▼' : '▶'}</span>
                        <span class="tag-label" style="font-size: 11px;">SQL USED</span>
                        <span style="font-size: 10px; color: var(--color-on-surface-dim);">{msg.sqlQueries.length} {msg.sqlQueries.length === 1 ? 'query' : 'queries'}</span>
                      </button>
                      {#if msg.showSql}
                        <div style="margin-top: 4px;">
                          {#each msg.sqlQueries as sql}
                            <pre style="background: var(--color-on-surface); color: var(--color-primary-container); padding: 10px 14px; font-size: 11px; font-family: var(--font-family-display); overflow-x: auto; margin: 4px 0; border: 2px solid var(--pw-border, #e5ddcf);">{sql}</pre>
                          {/each}
                        </div>
                      {/if}
                    {/if}

                    {#if msg.timestamp}
                      <div style="font-size: 10px; color: var(--color-on-surface-dim); margin-top: 4px; letter-spacing: 0.05em; text-transform: uppercase;">
                        {msg.timestamp} &middot; Agent
                      </div>
                    {/if}

                    {#if msg.status === 'done' && msg.suggestions && i === messages.length - 1}
                      <div class="flex flex-wrap gap-2 mt-3">
                        {#each msg.suggestions as suggestion}
                          <button class="suggestion-btn" onclick={() => send(suggestion)} disabled={isStreaming}>{suggestion}</button>
                        {/each}
                      </div>
                    {/if}
                  </div>
                </div>
              </div>
            {/if}
          {/each}
        </div>
      {/if}
    </div>
  </div>

  <div class="input-bar shrink-0">
    <div style="max-width: 820px; margin: 0 auto; padding: 12px 20px;">
      <div class="flex gap-2 items-end">
        <div style="flex: 1;">
          <textarea bind:this={textareaEl} bind:value={inputText} onkeydown={handleKeydown} oninput={autoResize} placeholder="Ask a question..." rows="1" disabled={isStreaming}></textarea>
        </div>
        <button class="send-btn" onclick={() => send()} disabled={isStreaming || !inputText.trim()} title="Send">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        </button>
      </div>
    </div>
    <div style="text-align: center; font-size: 11px; color: var(--color-on-surface-dim); padding: 0 20px 8px; text-transform: uppercase; letter-spacing: 0.1em;">
      DASH CAN MAKE MISTAKES. VERIFY CRITICAL INFORMATION.
    </div>
  </div>
</div>
</div>
