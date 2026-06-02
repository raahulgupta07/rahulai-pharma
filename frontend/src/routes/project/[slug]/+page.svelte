<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy, tick } from 'svelte';
 import { page } from '$app/state';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';
 import { sendMessage, generateSessionId, markdownToHtml, parseMarkdownTables, tableToCsv, hasNumericData, detectChartType, getAvailableTypes, parseChartHint } from '$lib';
 import type { ToolCall } from '$lib/api';
 import type { ParsedTable } from '$lib/table-parser';
 import type { ChartType } from '$lib/chart-detect';
 import EChartView from '$lib/echart.svelte';
 import * as echarts from 'echarts/core';
 import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
 import { TitleComponent, TooltipComponent, LegendComponent, GridComponent } from 'echarts/components';
 import { CanvasRenderer } from 'echarts/renderers';
 echarts.use([BarChart, LineChart, PieChart, ScatterChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer]);
 import TracePanel from '$lib/trace-panel.svelte';
 import DashboardPanel from '$lib/dashboard-panel.svelte';
 import DashRenderer from '$lib/dashboards/DashRenderer.svelte';
 import SlidesPanel from '$lib/slides-panel.svelte';
 import DeepDeckPanel from '$lib/deep-deck-panel.svelte';
 import ArtifactPanel from '$lib/dashboards/ArtifactPanel.svelte';
 import ChatMessageList, { formatCell, generateChartCaption } from '$lib/chat/ChatMessageList.svelte';
 import ReasoningTrace from '$lib/ReasoningTrace.svelte';
 import TraceTimeline from '$lib/trace/TraceTimeline.svelte';
 import type { TraceItem } from '$lib/api';
import { parseClarify, parseRelated } from '$lib/chat/tag-parsers';
 import ScheduleAnalysisModal from '$lib/components/ScheduleAnalysisModal.svelte';
 import MetricFixModal from '$lib/metrics/MetricFixModal.svelte';
 import MetricDefPopover from '$lib/metrics/MetricDefPopover.svelte';

 // Project context
 const projectSlug = $derived(page.params.slug || '');
 let projectInfo = $state<any>(null);

 // Metric modals (A3+A4+A6)
 let metricFixOpen = $state(false);
 let metricFixQuestion = $state('');
 let metricFixSql = $state('');
 let metricDefPopoverName = $state('');
 let metricDefPopoverOpen = $state(false);

 // Issue #20 — template prompt banner state
 let agentTplStatus = $state<any>(null);
 let templateSkipped = $state<boolean>(false);
 let applyingTemplate = $state<string | null>(null);
 const QUICK_TEMPLATES = ['pharmacy', 'investment', 'retail', 'hotel'];

 // Role-based access
 let userRole = $state<string>('viewer');
 let canEdit = $derived(userRole === 'editor' || userRole === 'admin' || userRole === 'owner');

 interface ChatMessage {
 role: 'user' | 'assistant';
 content: string;
 timestamp: string;
 status?: 'streaming' | 'done' | 'error';
 suggestions?: string[];
 toolCalls?: ToolCall[];
 workflowExpanded?: boolean;
 trace?: TraceItem[];
 traceStart?: number;
 traceLive?: boolean;
 traceDoneAt?: number;
 sqlQueries?: string[];
 showSql?: boolean;
 showChart?: boolean;
 chartType?: ChartType;
 qualityScore?: number;
 showTrace?: boolean;
 activeTab?: 'analysis' | 'data' | 'query' | 'chart' | 'sources';
 dataTableIndex?: number;
 reasoningUsed?: string;
 analysisUsed?: string;
 proposedLearnings?: string[];
 proposedLearningsWithScores?: {fact: string; score: number}[];
 autoSavedLearnings?: string[];
 autoSavedWithScores?: {fact: string; score: number}[];
 learningsSaved?: boolean;
 sources_used?: { id?: number; name?: string; dialect?: string; db_type?: string; mode?: string; latency_ms?: number; cost_usd?: number }[];
 federated_meta?: { latency_ms?: number; cost_usd?: number; engine?: string };
 }

 let messages = $state<ChatMessage[]>([]);
 let inputText = $state('');
 let isStreaming = $state(false);
 let sessionId = $state('');
 let sessionStartTime = $state('');
 let messagesEl: HTMLDivElement;
 let textareaEl: HTMLTextAreaElement;
 let copiedIndex = $state(-1);

 // AbortController for session-load fetches (cancel on rapid switches)
 let openSessionProjAbort: AbortController | null = null;

 // Proactive insights
 let insights = $state<{id: number; insight: string; severity: string; tables: string[]; created_at: string}[]>([]);
 let insightsExpanded = $state(false);
 let showWorkflowSaveModal = $state(false);
 let wfSaveName = $state('');
 let wfSaveDesc = $state('');
 let wfSaveSteps = $state<{question: string; checked: boolean}[]>([]);

 function openWorkflowSave() {
 const userMsgs = messages.filter(m => m.role === 'user');
 wfSaveSteps = userMsgs.map(m => ({ question: m.content, checked: true }));
 wfSaveName = '';
 wfSaveDesc = '';
 showWorkflowSaveModal = true;
 }

 let proposalCreated = $state<Set<string>>(new Set());

 // Sim wizard smart chips
 let simChips = $state<string[]>([]);
 let simChipTimer: ReturnType<typeof setInterval> | null = null;

 async function loadSimChips() {
 try {
 const r = await fetch(`/api/sim/wizard/suggestions?slug=${encodeURIComponent(projectSlug)}`, { headers: _headers() });
 if (r.ok) {
 const j = await r.json();
 const chips = Array.isArray(j?.chips) ? j.chips : [];
 simChips = chips.slice(0, 3).filter((c: any) => typeof c === 'string' && c.trim().length > 0);
 }
 } catch {}
 }

 function applyChip(text: string) {
 inputText = text;
 setTimeout(() => textareaEl?.focus(), 0);
 }

 async function createCampaignFromProposal(name: string, segment: string, discountPct: number, audience: number, propId: string) {
 if (proposalCreated.has(propId)) return;
 try {
 const r = await fetch(`/api/projects/${projectSlug}/campaigns`, {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 name,
 target_segment: segment,
 discount_pct: discountPct,
 predicted_audience: audience,
 status: 'draft',
 type: 'manual',
 source: 'chat_proposal',
 }),
 });
 if (r.ok) {
 const next = new Set(proposalCreated);
 next.add(propId);
 proposalCreated = next;
 }
 } catch {}
 }

 async function confirmWorkflowSave() {
 const steps = wfSaveSteps.filter(s => s.checked).map(s => ({ type: 'query', title: s.question.slice(0, 60), question: s.question }));
 if (!wfSaveName.trim() || steps.length === 0) return;
 try {
 await fetch(`/api/projects/${projectSlug}/workflows-db`, {
 method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ name: wfSaveName.trim(), description: wfSaveDesc.trim(), steps, source: 'user' })
 });
 showWorkflowSaveModal = false;
 loadWorkflows();
 } catch {}
 }

 async function loadInsights() {
 try {
 const res = await fetch(`/api/projects/${projectSlug}/insights`, { headers: _headers() });
 if (res.ok) { const d = await res.json(); insights = d.insights || []; }
 } catch {}
 }

 async function dismissInsight(id: number) {
 await fetch(`/api/projects/${projectSlug}/insights/${id}/dismiss`, { method: 'POST', headers: _headers() });
 insights = insights.filter(i => i.id !== id);
 }

 // Drift bell state
 let driftCount = $state(0);
 let driftEvents = $state<any[]>([]);
 let driftDropdownOpen = $state(false);

 async function loadDriftCount() {
 try {
 const r = await fetch(`/api/drift/count/${projectSlug}`, { headers: _headers() });
 if (r.ok) driftCount = (await r.json()).open_count || 0;
 } catch {}
 }

 async function loadDriftEvents() {
 try {
 const r = await fetch(`/api/drift/recent/${projectSlug}?status=open&limit=20`, { headers: _headers() });
 if (r.ok) driftEvents = (await r.json()).events || [];
 } catch {}
 }

 async function ackDrift(id: number) {
 await fetch(`/api/drift/${id}/acknowledge`, { method: 'POST', headers: _headers() });
 await loadDriftEvents();
 await loadDriftCount();
 }

 async function retrainFromDrift(id: number) {
 const r = await fetch(`/api/drift/${id}/retrain`, { method: 'POST', headers: _headers() });
 if (r.ok) {
 const j = await r.json();
 if (j.next_step) {
 await fetch(j.next_step, { method: 'POST', headers: _headers() });
 }
 await loadDriftEvents();
 await loadDriftCount();
 }
 }

 async function dismissDrift(id: number) {
 await fetch(`/api/drift/${id}/dismiss`, { method: 'POST', headers: _headers() });
 await loadDriftEvents();
 await loadDriftCount();
 }

 function cleanUserMessage(raw: string): string {
 if (!raw) return raw;
 // If the instrumented prompt embeds the real question after "Question:",
 // return only the text after the LAST occurrence (case-insensitive).
 const lastIdx = raw.toLowerCase().lastIndexOf('question:');
 if (lastIdx >= 0) {
 const q = raw.slice(lastIdx + 'question:'.length).trim();
 if (q) return q;
 }
 // Otherwise drop leading directive lines.
 const DIRECTIVE_RE = /(Do NOT|\[MODE:|\[ANALYSIS:|CRITICAL STYLE|tags for drill-down|every figure must come from|IMPACT:|RELATED:|KPI:)/i;
 const lines = raw.split('\n');
 const kept = lines.filter((ln) => !DIRECTIVE_RE.test(ln));
 const out = kept.join('\n').trim();
 return out || raw;
 }

 // generateChartCaption, formatCell are imported from $lib/chat/ChatMessageList.svelte
 // parseClarify, parseRelated are imported from $lib/chat/tag-parsers
 // See frontend/CHAT_RENDERER.md §6 for the single-renderer rule.

 async function copySql(sql: any) {
 if (!sql) return;
 try { await navigator.clipboard.writeText(String(sql)); } catch {}
 }

 function trackPreference(action: string, value: string) {
 fetch(`/api/projects/${projectSlug}/track-preference`, {
 method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ action, value })
 }).catch(() => {});
 }

 // Sidebar — persisted in localStorage, default open
 let sidebarOpen = $state(typeof localStorage !== 'undefined' ? localStorage.getItem('dash_sidebar') !== 'closed' : true);
 let pastSessions = $state<{session_id: string; created_at: string; updated_at: string; first_message?: string}[]>([]);
 let recentDashboards = $state<{id: string|number; title?: string; created_at?: string}[]>([]);

 async function loadRecentDashboards() {
 try {
 const res = await fetch(`/api/dashboards/list/${projectSlug}`, { headers: _headers() });
 if (res.ok) {
 const d = await res.json();
 const list = d.dashboards || d.items || d.list || (Array.isArray(d) ? d : []);
 recentDashboards = (list || []).slice(0, 5);
 }
 } catch {}
 }

 function toggleSidebar() {
 sidebarOpen = !sidebarOpen;
 localStorage.setItem('dash_sidebar', sidebarOpen ? 'open' : 'closed');
 if (sidebarOpen) loadSessions();
 }

 function _headers(): Record<string, string> {
 const headers: Record<string, string> = {};
 if (typeof localStorage === 'undefined') return headers;
 const t = localStorage.getItem('dash_token');
 if (t) headers['Authorization'] = `Bearer ${t}`;
 const scopeId = localStorage.getItem('dash_scope_id');
 if (scopeId) headers['X-Scope-Id'] = scopeId;
 return headers;
 }

 // Restore SQL queries from a reconstructed trace (after page refresh the live
 // SSE-extracted sqlQueries are gone, so the SQL tab would show "No SQL executed"
 // even though the trace clearly ran one). Pull SELECT/WITH SQL out of each
 // tool item's args.
 function _sqlsFromTrace(trace: any): string[] {
  if (!Array.isArray(trace)) return [];
  const out: string[] = [];
  for (const it of trace) {
   if (!it || it.kind !== 'tool') continue;
   const a = it.args;
   let sql = '';
   if (typeof a === 'string') {
    if (/\b(SELECT|WITH|INSERT|UPDATE|DELETE|EXPLAIN)\b/i.test(a)) sql = a;
   } else if (a && typeof a === 'object') {
    for (const k of ['sql', 'query', 'statement', 'sql_query', 'q']) {
     const v = a[k];
     if (typeof v === 'string' && /\b(SELECT|WITH|INSERT|UPDATE|DELETE|EXPLAIN)\b/i.test(v)) { sql = v; break; }
    }
   }
   if (sql && !out.includes(sql)) out.push(sql);
  }
  return out;
 }

 async function loadSessions() {
 try {
 const res = await fetch(`/api/sessions?project=${projectSlug}`, { headers: _headers() });
 if (res.ok) { const d = await res.json(); pastSessions = d.sessions || []; }
 } catch {}
 }

 async function registerSession(msg: string) {
 try {
 await fetch(`/api/sessions/register?session_id=${encodeURIComponent(sessionId)}&project=${encodeURIComponent(projectSlug)}&message=${encodeURIComponent(msg)}`, {
 method: 'POST', headers: _headers()
 });
 } catch {}
 }

 async function switchSession(sid: string) {
 sessionId = sid;
 localStorage.setItem(`dash_session_${projectSlug}`, sid);
 messages = [];
 sessionStartTime = getTimestamp();

 // Load messages from this session
 try {
 const res = await fetch(`/api/projects/${projectSlug}/sessions/${sid}/messages`, { headers: _headers() });
 if (res.ok) {
 const data = await res.json();
 const loaded = data.messages || [];
 messages = loaded.map((m: any) => ({
 role: m.role,
 content: m.role === 'user' ? cleanUserMessage(m.content || '') : (m.content || ''),
 rawContent: m.role === 'user' ? (m.content || '') : undefined,
 timestamp: '',
 status: m.role === 'assistant' ? 'done' : undefined,
 toolCalls: m.tool_calls || [],
 duration: m.duration || 0,
 qualityScore: m.quality_score,
 routing: m.routing || undefined,
 workflowExpanded: false,
 showPrompt: false,
 trace: Array.isArray(m.trace) ? m.trace : [],
 sqlQueries: _sqlsFromTrace(m.trace),
 usage: m.usage ?? null,
 traceLive: false,
 traceDoneAt: 0,
 }));
 scrollToBottom();
 }
 } catch {}
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

 let dynamicSuggestions = $state<string[]>([]);
 let projectTables = $state<{name: string; columns: string[]; rows: number}[]>([]);

 // Chat stats for empty state
 let avgScore = $state(0);
 let memoryCount = $state(0);

 let sessionCount = $derived(pastSessions.length);

 async function loadChatStats() {
 // Fetch average quality score
 try {
 const r = await fetch(`/api/projects/${projectSlug}/scores/latest?session_id=all`, { headers: _headers() });
 if (r.ok) { const d = await r.json(); avgScore = d.score || 0; }
 } catch {}
 // Fetch memory count from consolidation status
 try {
 const r = await fetch(`/api/projects/${projectSlug}/consolidation-status`, { headers: _headers() });
 if (r.ok) { const d = await r.json(); memoryCount = d.memory_count || 0; }
 } catch {}
 }

 async function loadDynamicSuggestions() {
 try {
 // Always try eval Q&A first — these are LLM-generated smart questions from training
 try {
 const evRes = await fetch(`/api/projects/${projectSlug}/evals`, { headers: _headers() });
 if (evRes.ok) {
 const evData = await evRes.json();
 const evals = evData.evals || [];
 if (evals.length > 0) {
 dynamicSuggestions = evals.slice(0, 6).map((e: any) => e.question?.replace(/^\[.*?\]\s*/, '') || '');
 dynamicSuggestions = dynamicSuggestions.filter((s: string) => s.length > 5);
 }
 }
 } catch {}
 if (dynamicSuggestions.length >= 3) return; // Got good suggestions from evals

 // Fallback: generate from table metadata
 const res = await fetch(`/api/projects/${projectSlug}/stats`, { headers: _headers() });
 if (!res.ok) return;
 const data = await res.json();
 const tables = data.tables || [];
 if (tables.length === 0) {
 if (dynamicSuggestions.length === 0) {
 dynamicSuggestions = ["What information do we have?", "What are the key findings?", "Summarize the documents"];
 }
 return;
 }

 // Fetch column details for each table
 const tableDetails: {name: string; columns: string[]; rows: number}[] = [];
 for (const t of tables.slice(0, 8)) {
 try {
 const ir = await fetch(`/api/tables/${t.name}/inspect?project=${projectSlug}`, { headers: _headers() });
 if (ir.ok) {
 const id = await ir.json();
 tableDetails.push({ name: t.name, columns: (id.columns || []).map((c: any) => c.name), rows: t.rows || 0 });
 } else {
 tableDetails.push({ name: t.name, columns: [], rows: t.rows || 0 });
 }
 } catch {
 tableDetails.push({ name: t.name, columns: [], rows: t.rows || 0 });
 }
 }
 projectTables = tableDetails;

 // Generate smart suggestions from column names (not table names)
 const suggestions: string[] = [];
 for (const td of tableDetails) {
 const numCol = td.columns.find(c => /amount|price|revenue|total|cost|value|salary|qty|quantity|count|sales|profit|budget/i.test(c));
 const dateCol = td.columns.find(c => /date|created|updated|time|month|year|period/i.test(c));
 const catCol = td.columns.find(c => /status|type|category|plan|region|country|department|segment|product|brand|channel/i.test(c));
 if (numCol && suggestions.length < 6) suggestions.push(`What is the total ${numCol.replace(/_/g, ' ')}?`);
 if (dateCol && numCol && suggestions.length < 6) suggestions.push(`Show ${numCol.replace(/_/g, ' ')} trends over time`);
 if (catCol && numCol && suggestions.length < 6) suggestions.push(`Break down ${numCol.replace(/_/g, ' ')} by ${catCol.replace(/_/g, ' ')}`);
 }

 // Add generic business questions
 if (suggestions.length < 6) suggestions.push("Give me an overview of my data");
 if (suggestions.length < 6) suggestions.push("What are the key metrics and trends?");
 if (tableDetails.length > 1 && suggestions.length < 6) suggestions.push("How do these datasets relate to each other?");

 dynamicSuggestions = suggestions.slice(0, 6);
 } catch {}
 }

 function getTimestamp(): string {
 return new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
 }

 function getSuggestions(content: string): string[] {
 // Generate follow-ups based on response content (no raw table names)
 const lower = content.toLowerCase();
 const suggestions: string[] = [];

 // Content-aware follow-ups
 if (lower.includes('trend') || lower.includes('growth') || lower.includes('month') || lower.includes('increase') || lower.includes('decrease')) {
 suggestions.push("Compare this to the previous period");
 suggestions.push("What's driving this trend?");
 }
 if (lower.includes('top') || lower.includes('highest') || lower.includes('largest') || lower.includes('best')) {
 suggestions.push("Show the bottom performers too");
 suggestions.push("What percentage of total do they represent?");
 }
 if (lower.includes('revenue') || lower.includes('sales') || lower.includes('profit') || lower.includes('cost')) {
 if (suggestions.length < 3) suggestions.push("Break this down by category");
 }
 if (lower.includes('anomal') || lower.includes('outlier') || lower.includes('unusual')) {
 if (suggestions.length < 3) suggestions.push("What caused these anomalies?");
 }

 // Fallback
 if (suggestions.length === 0) {
 suggestions.push("Tell me more about this", "Can you visualize this?", "What are the key takeaways?");
 }

 return suggestions.slice(0, 3);
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

 // Issue #20 — apply quick-pick template
 async function applyQuickTemplate(name: string) {
 if (applyingTemplate) return;
 applyingTemplate = name;
 try {
 const res = await fetch(`/api/projects/${projectSlug}/apply-agent-template`, {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ template_name: name }),
 });
 if (res.ok) {
 // Reload status then full page so dynamic instructions re-init.
 try {
 const r2 = await fetch(`/api/projects/${projectSlug}/agent-template`, { headers: _headers() });
 if (r2.ok) agentTplStatus = await r2.json();
 } catch {}
 window.location.reload();
 } else {
 alert(`Template apply failed: ${res.status}`);
 }
 } catch (e: any) {
 alert(`Template apply error: ${e?.message || e}`);
 } finally {
 applyingTemplate = null;
 }
 }
 function skipTemplatePrompt() {
 try { localStorage.setItem(`template_skipped_${projectSlug}`, '1'); } catch {}
 templateSkipped = true;
 }
 function browseAllTemplates() {
 window.location.href = `/ui/projects?library=1&target=${encodeURIComponent(projectSlug)}`;
 }

 let featureConfig = $state<any>(null);
 function tabEnabled(name: string): boolean {
 if (!featureConfig?.tabs) return true;
 return featureConfig.tabs[name] !== false;
 }

 onMount(async () => {
 // Load project info
 try {
 const res = await fetch(`/api/projects/${projectSlug}`, { headers: _headers() });
 if (res.ok) { projectInfo = await res.json(); userRole = projectInfo.user_role || 'owner'; }
 } catch {}

 // Auto-trigger Deep Dash if redirected from /chat with ?build_dash=1
 try {
   const params = new URLSearchParams(window.location.search);
   if (params.get('build_dash') === '1') {
     // Wait for messages to load (loadSession runs further below); poll briefly.
     setTimeout(() => {
       if (messages.length >= 2) openDeepDashboard();
     }, 800);
     // Clean URL so refresh doesn't retrigger.
     const clean = window.location.pathname + window.location.hash;
     window.history.replaceState({}, '', clean);
   }
 } catch {}

 // Scope guard — if project requires scope and none is selected, bounce to picker.
 // Admins (is_super) can bypass with ?force=1.
 try {
 const params = new URLSearchParams(window.location.search);
 const forced = params.get('force') === '1';
 const hasScope = !!localStorage.getItem('dash_scope_id');
 const requiresScope = !!(projectInfo && (projectInfo.requires_scope || projectInfo.scope_required));
 const isSuper = !!(projectInfo && (projectInfo.is_super || projectInfo.user_is_super));
 if (requiresScope && !hasScope && !(forced && isSuper)) {
 const next = window.location.pathname + window.location.search;
 window.location.href = `/ui/scope-picker?project_slug=${encodeURIComponent(projectSlug)}&next=${encodeURIComponent(next)}`;
 return;
 }
 } catch {}
 // Issue #20 — load agent template status + persisted skip flag
 try {
 templateSkipped = localStorage.getItem(`template_skipped_${projectSlug}`) === '1';
 } catch {}
 try {
 const res = await fetch(`/api/projects/${projectSlug}/agent-template`, { headers: _headers() });
 if (res.ok) agentTplStatus = await res.json();
 } catch {}

 // Load feature config (agent creator toggles)
 try {
 const res = await fetch(`/api/projects/${projectSlug}/feature-config`, { headers: _headers() });
 if (res.ok) { const d = await res.json(); featureConfig = d.config; }
 } catch {}

 const savedSession = localStorage.getItem(`dash_session_${projectSlug}`);
 if (savedSession) {
 sessionId = savedSession;
 // Restore messages from saved session
 try {
 const res = await fetch(`/api/projects/${projectSlug}/sessions/${savedSession}/messages`, { headers: _headers() });
 if (res.ok) {
 const data = await res.json();
 const loaded = data.messages || [];
 if (loaded.length > 0) {
 messages = loaded.map((m: any) => ({
 role: m.role,
 content: m.role === 'user' ? cleanUserMessage(m.content || '') : (m.content || ''),
 rawContent: m.role === 'user' ? (m.content || '') : undefined,
 timestamp: '',
 status: m.role === 'assistant' ? 'done' : undefined,
 toolCalls: m.tool_calls || [],
 duration: m.duration || 0,
 qualityScore: m.quality_score,
 routing: m.routing || undefined,
 workflowExpanded: false,
 activeTab: 'analysis',
 trace: Array.isArray(m.trace) ? m.trace : [],
 sqlQueries: _sqlsFromTrace(m.trace),
 usage: m.usage ?? null,
 traceLive: false,
 traceDoneAt: 0,
 }));
 }
 }
 } catch {}
 } else {
 sessionId = generateSessionId();
 }
 localStorage.setItem(`dash_session_${projectSlug}`, sessionId);
 sessionStartTime = getTimestamp();
 textareaEl?.focus();
 loadDynamicSuggestions();
 loadSessions().then(() => loadChatStats());
 loadDashForSession();
 loadRecentDashboards();
 loadWorkflows();
 loadDriftCount();
 setInterval(loadDriftCount, 60000);
 loadInsights();
 setInterval(loadInsights, 60000);
 // (loadSimChips + simChipTimer wired below by linter-managed block)

 // Listen for sidebar new-chat / open-session events scoped to this project
 const onNewChatProj = (e: any) => {
 try {
 if (!e?.detail || e.detail.slug === projectSlug) newChat();
 } catch { newChat(); }
 };
 const onOpenSessionProj = async (e: any) => {
 try {
 const sid = e?.detail?.sid;
 const scope = e?.detail?.scope;
 const slug = e?.detail?.slug;
 if (sid && scope === 'project' && slug === projectSlug) {
 // Cancel any in-flight session-load from a prior rapid switch
 if (openSessionProjAbort) openSessionProjAbort.abort();
 openSessionProjAbort = new AbortController();
 sessionId = sid;
 localStorage.setItem(`dash_session_${projectSlug}`, sid);
 messages = [];
 try {
 const t = localStorage.getItem('dash_token') || '';
 const res = await fetch(`/api/projects/${projectSlug}/sessions/${sid}/messages`, {
 headers: { Authorization: `Bearer ${t}` },
 signal: openSessionProjAbort.signal
 });
 if (res.ok) {
 const data = await res.json();
 const loaded = data.messages || [];
 if (loaded.length > 0) {
 messages = loaded.map((m: any) => ({
 role: m.role,
 content: m.role === 'user' ? cleanUserMessage(m.content || '') : (m.content || ''),
 rawContent: m.role === 'user' ? (m.content || '') : undefined,
 timestamp: '',
 status: m.role === 'assistant' ? 'done' : undefined,
 toolCalls: [],
 workflowExpanded: false,
 activeTab: 'analysis',
 trace: Array.isArray(m.trace) ? m.trace : [],
 sqlQueries: _sqlsFromTrace(m.trace),
 usage: m.usage ?? null,
 traceLive: false,
 traceDoneAt: 0,
 }));
 }
 }
 } catch (err: any) {
 if (err?.name === 'AbortError') return; // swallow expected cancellation
 }
 loadSessions();
 loadDashForSession();
 }
 } catch {}
 };
 window.addEventListener('dash-newchat-project', onNewChatProj);
 window.addEventListener('dash-open-session', onOpenSessionProj);
 // Sim wizard smart chips: load + 30s refresh
 loadSimChips();
 simChipTimer = setInterval(loadSimChips, 30000);
 return () => {
 window.removeEventListener('dash-newchat-project', onNewChatProj);
 window.removeEventListener('dash-open-session', onOpenSessionProj);
 };
 });

 onDestroy(() => {
 if (openSessionProjAbort) openSessionProjAbort.abort();
 if (simChipTimer) { clearInterval(simChipTimer); simChipTimer = null; }
 });

 function newChat() {
 messages = [];
 sessionId = generateSessionId();
 localStorage.setItem(`dash_session_${projectSlug}`, sessionId);
 sessionStartTime = getTimestamp();
 textareaEl?.focus();
 loadSessions();
 dashSpec = null;
 dashVersions = [];
 dashCurrentVersion = null;
 splitMode = false;
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

 // Inline action toast (no global toast system)
 let actionFlash = $state<{ kind: 'ok' | 'warn' | 'err'; text: string } | null>(null);
 function _flash(kind: 'ok' | 'warn' | 'err', text: string) {
 actionFlash = { kind, text };
 setTimeout(() => { actionFlash = null; }, 2400);
 }

 function _lastAssistantMsg() {
 for (let i = messages.length - 1; i >= 0; i--) {
 const m = messages[i];
 if (m?.role === 'assistant') return { msg: m, index: i };
 }
 return null;
 }

 async function handleAction(act: any, arg?: any) {
 // String actions from AnswerCard exec buttons (save/pin/csv/share/copy/diary/save_decision/related)
 if (typeof act === 'string') {
 const a = act;

 // Related-question chip — re-ask via send(). 'arg' carries the question text.
 if (a === 'related' || a.startsWith('related:')) {
 const q = (typeof arg === 'string' && arg.trim()) ? arg.trim() : a.slice('related:'.length).trim();
 if (q) { send(q); return; }
 return;
 }

 // Open VentureDesk panel — agent emits [ACTION_TITLE:Open Deal Panel]
 if (a === 'open_venture_panel' || /open.*deal.*panel|open.*venture/i.test(a)) {
 try { window.location.href = `${base}/project/${projectSlug}/settings#venture`; } catch {}
 return;
 }

 const ctx = _lastAssistantMsg();

 // save_decision:<json> — diary save with payload
 if (a.startsWith('save_decision:')) {
 const payload = a.slice('save_decision:'.length);
 try {
 const res = await fetch(`/api/projects/${projectSlug}/decisions`, {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: payload || '{}',
 });
 if (res.ok) _flash('ok', '✓ Saved to decision diary');
 else _flash('warn', 'Diary save not available yet');
 } catch { _flash('warn', 'Diary save not available yet'); }
 return;
 }

 if (a === 'copy') {
 if (ctx) {
 try { await navigator.clipboard.writeText(ctx.msg.content || ''); _flash('ok', '✓ Copied'); }
 catch { _flash('err', 'Copy failed'); }
 }
 return;
 }
 if (a === 'save') {
 // Save answer as a memory
 if (!ctx) return;
 const last = messages[messages.length - 2];
 const q = last?.role === 'user' ? last.content : '';
 try {
 const res = await fetch(`/api/projects/${projectSlug}/memories`, {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ scope: 'project', source: 'user', content: `Q: ${q}\nA: ${(ctx.msg.content || '').slice(0, 1200)}` }),
 });
 if (res.ok) _flash('ok', '✓ Saved to memory');
 else _flash('warn', 'Save not available yet');
 } catch { _flash('warn', 'Save not available yet'); }
 return;
 }
 if (a === 'pin') {
 if (!ctx) return;
 const tables = (ctx.msg as any).tables || [];
 openPinModal(ctx.index, tables, ctx.msg.content || '');
 return;
 }
 if (a === 'csv' || a === 'excel') {
 try { await exportExcelChat(); _flash('ok', '✓ Excel generated'); }
 catch { _flash('err', 'Excel export failed'); }
 return;
 }
 if (a === 'share' || a === 'email') {
 if (!ctx) return;
 try {
 const url = `${window.location.origin}/ui/project/${projectSlug}?session=${encodeURIComponent(sessionId || '')}`;
 await navigator.clipboard.writeText(url);
 _flash('ok', '✓ Share link copied to clipboard');
 } catch { _flash('warn', 'Share not available yet'); }
 return;
 }
 // Unknown string action — log + toast
 console.log('[action] unhandled string:', a);
 _flash('warn', `Action "${a}" not implemented yet`);
 return;
 }

 // Object actions (legacy [ACTION:label|type|param])
 const label = (act?.label || '').trim();
 const type = (act?.type || '').toLowerCase();
 if (!label) return;
 switch (type) {
 case 'investigate':
 send(`Investigate: ${label}`); return;
 case 'run_analysis':
 send(`Run deeper analysis on: ${label}`); return;
 case 'create_campaign':
 try { goto(`/project/${projectSlug}/campaigns?prefill=${encodeURIComponent(label)}`); } catch { send(label); }
 return;
 case 'train_model':
 send(`Train an ML model for: ${label}`); return;
 case 'drill_down':
 send(`Drill into: ${label}`); return;
 default:
 send(label);
 }
 }

 async function send(text?: string) {
 let msgText = (text || inputText).trim();
 if (!msgText || isStreaming) return;
 // Reasoning mode: slash command > mode picker > router default.
 let forcedReasoning = '';
 if (msgText.startsWith('/deep ')) {
   forcedReasoning = 'deep';
   msgText = msgText.slice(6).trim();
 } else if (msgText.startsWith('/quick ')) {
   forcedReasoning = 'quick';
   msgText = msgText.slice(7).trim();
 } else if (reasoningMode === 'fast') {
   forcedReasoning = 'quick';
 } else if (reasoningMode === 'deep') {
   forcedReasoning = 'deep';
 }  // else 'auto' → empty → router decides
 const msg = msgText;
 if (!msg) return;
 inputText = '';
 if (textareaEl) textareaEl.style.height = 'auto';

 messages = [...messages, { role: 'user', content: msg, timestamp: getTimestamp() }];
 isStreaming = true;
 registerSession(msg);
 await scrollToBottom();

 messages = [...messages, { role: 'assistant', content: '', timestamp: '', status: 'streaming', toolCalls: [], workflowExpanded: true, trace: [], traceStart: Date.now(), traceLive: true, reasoningUsed: reasoningMode, analysisUsed: analysisType }];
 await scrollToBottom();

 abortController = new AbortController();

 const onToken = (token: string) => {
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 messages = [...messages.slice(0, -1), { ...last, content: last.content + token }];
 }
 scrollToBottom();
 };

 await sendMessage(
 msg, sessionId,
 onToken,
 () => {
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 // Detect [DASHBOARD:id] tag from agent
 const dashMatch = last.content.match(/\[DASHBOARD:(\d+)\]/);
 if (dashMatch) {
 activeDashboardId = parseInt(dashMatch[1]);
 dashboardPanelOpen = true;
 }
 // Detect [PRESENTATION:id] tag from skl_pptx_builder skill
 const presMatch = last.content.match(/\[PRESENTATION:(\d+)\]/);
 if (presMatch) {
 activePresId = parseInt(presMatch[1]);
 presPanelOpen = true;
 }
 // Detect [CONFIRM_OUTLINE] tag → frontend renders approve buttons inline
 if (last.content.includes('[CONFIRM_OUTLINE]')) {
 last.outlinePending = true;
 }
 const finalizedTools = (last.toolCalls || []).map(t => t.status === 'running' ? { ...t, status: 'done' as const } : t);
 const finalizedTrace = (Array.isArray(last.trace) ? last.trace : []).map((t) => (t.kind === 'tool' && t.status === 'run') ? { ...t, status: 'done' as const } : t);
 messages = [...messages.slice(0, -1), {
 ...last, timestamp: getTimestamp(), status: 'done',
 toolCalls: finalizedTools,
 trace: finalizedTrace, traceLive: false, traceDoneAt: Date.now(),
 suggestions: [], workflowExpanded: false
 }];
 }
 isStreaming = false;
 scrollToBottom();
 textareaEl?.focus();
 loadSessions();
 // Extract context memory + smart follow-ups (background)
 const lastUserMsg = messages.length >= 2 ? messages[messages.length - 2]?.content : '';
 const lastAssistantMsg = messages[messages.length - 1]?.content || '';
 if (lastUserMsg && lastAssistantMsg) {
 // Context extraction — auto-save high confidence, propose low confidence
 fetch(`/api/projects/${projectSlug}/extract-context`, {
 method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ question: lastUserMsg, answer: lastAssistantMsg, session_id: sessionId })
 }).then(r => r.json()).then(d => {
 const last = messages[messages.length - 1];
 if (!last || last.role !== 'assistant') return;
 const autoSaved = d.auto_saved_with_scores || d.auto_saved?.map((f: string) => ({fact: f, score: 90})) || [];
 const needsApproval = d.facts_with_scores || d.facts?.map((f: string) => ({fact: f, score: 40})) || [];
 if (autoSaved.length > 0 || needsApproval.length > 0) {
 messages = [...messages.slice(0, -1), {
 ...last,
 proposedLearnings: needsApproval.length > 0 ? d.facts : undefined,
 proposedLearningsWithScores: needsApproval.length > 0 ? needsApproval : undefined,
 autoSavedLearnings: autoSaved.length > 0 ? d.auto_saved : undefined,
 autoSavedWithScores: autoSaved.length > 0 ? autoSaved : undefined,
 }];
 }
 }).catch(() => {});

 // Smart follow-ups DISABLED — duplicate of [RELATED:] pills. User wants single source.
 }

 // Fetch quality score after a delay (background scoring takes a moment)
 setTimeout(async () => {
 try {
 const res = await fetch(`/api/projects/${projectSlug}/scores/latest?session_id=${sessionId}`, { headers: _headers() });
 if (res.ok) {
 const d = await res.json();
 if (d.score) {
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 messages = [...messages.slice(0, -1), { ...last, qualityScore: d.score }];
 }
 }
 }
 } catch {}
 // Verified-reward: HARD truth check (answer vs proven SQL / pinned number).
 try {
 const vr = await fetch(`/api/projects/${projectSlug}/sessions/${sessionId}/verified`, { headers: _headers() });
 if (vr.ok) {
 const v = await vr.json();
 if (v && v.verified && v.verified !== 'unknown') {
 const lastV = messages[messages.length - 1];
 if (lastV?.role === 'assistant') {
 messages = [...messages.slice(0, -1), { ...lastV, verified: v }];
 }
 }
 }
 } catch {}
 // Load proactive insights (generated in background after quality scoring)
 loadInsights();
 }, 5000);
 },
 (error) => {
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 const errTrace = (Array.isArray(last.trace) ? last.trace : []).map((t) => (t.kind === 'tool' && t.status === 'run') ? { ...t, status: 'done' as const } : t);
 messages = [...messages.slice(0, -1), { ...last, content: `Error: ${error}`, timestamp: getTimestamp(), status: 'error', trace: errTrace, traceLive: false, traceDoneAt: Date.now() }];
 }
 isStreaming = false;
 scrollToBottom();
 },
 (tool: ToolCall) => {
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 const existing = last.toolCalls || [];
 // Detect "agent done" marker (e.g. name="Analyst agent") and force-clear
 // any of that agent's running member tools so spinner never sticks.
 const isAgentDoneMarker = tool.status === 'done' && tool.agentName && tool.name === `${tool.agentName} agent`;
 // Match by (name + agentName) so same tool used by different members
 // does not collide. Falls back to name-only when no agentName.
 const idx = existing.findIndex(t =>
 t.name === tool.name &&
 (t.agentName || '') === (tool.agentName || '') &&
 t.status === 'running'
 );
 let updated: ToolCall[];
 if (idx >= 0 && tool.status === 'done') {
 updated = [...existing];
 updated[idx] = {
 ...updated[idx],
 status: 'done',
 duration: tool.duration,
 sqlQuery: tool.sqlQuery || updated[idx].sqlQuery,
 };
 } else if (tool.status === 'running') {
 updated = [...existing, tool];
 } else if (tool.status === 'done') {
 // Completion w/o prior running entry — append as done so it still shows.
 updated = [...existing, tool];
 } else {
 updated = existing;
 }
 if (isAgentDoneMarker) {
 updated = updated.map(t =>
 t.agentName === tool.agentName && t.status === 'running'
 ? { ...t, status: 'done' as const }
 : t
 );
 }
 // Capture SQL queries
 const sqls = last.sqlQueries || [];
 if (tool.sqlQuery && !sqls.includes(tool.sqlQuery)) {
 sqls.push(tool.sqlQuery);
 }
 messages = [...messages.slice(0, -1), { ...last, toolCalls: updated, sqlQueries: sqls }];
 }
 scrollToBottom();
 },
 projectSlug,
 forcedReasoning || '',
 analysisType,
 abortController.signal,
 (item: TraceItem) => {
 const last = messages[messages.length - 1];
 if (!last || last.role !== 'assistant') return;
 const prev = Array.isArray(last.trace) ? last.trace : [];
 let next: TraceItem[];
 if (item.kind === 'tool') {
 const idx = prev.findIndex((t) => t.kind === 'tool' && t.id === item.id);
 if (idx >= 0) {
 next = [...prev];
 next[idx] = { ...next[idx], ...item };
 } else {
 next = [...prev, item];
 }
 } else {
 // Reasoning models (Standard+ tier) stream reasoning_content token-by-token,
 // each arriving as its own 'step' event. Merge consecutive reasoning steps
 // from the same agent into ONE growing step instead of 64 single-word rows.
 const lastItem = prev[prev.length - 1] as any;
 const isReason = (t: any) => t && t.kind === 'step' && /reason/i.test(String(t.title || ''));
 if (isReason(item) && isReason(lastItem) && (lastItem.agent || '') === ((item as any).agent || '')) {
 next = [...prev];
 const mergedText = (((lastItem.text || '') + ' ' + ((item as any).text || ''))).replace(/\s+/g, ' ').trim();
 next[next.length - 1] = { ...lastItem, text: mergedText };
 } else {
 next = [...prev, item];
 }
 }
 messages = [...messages.slice(0, -1), { ...last, trace: next }];
 },
 (u: { input_tokens: number; output_tokens: number; model?: string }) => {
 const last = messages[messages.length - 1];
 if (!last || last.role !== 'assistant') return;
 const cur = (last as any).usage || { input_tokens: 0, output_tokens: 0 };
 const merged = { input_tokens: (cur.input_tokens || 0) + (u.input_tokens || 0), output_tokens: (cur.output_tokens || 0) + (u.output_tokens || 0), model: u.model || cur.model };
 messages = [...messages.slice(0, -1), { ...last, usage: merged }];
 },
 undefined, // mode (global super-chat only)
 (r: any) => {
 // Complexity router decision (Feature A) → attach to message for trace badge.
 const last = messages[messages.length - 1];
 if (!last || last.role !== 'assistant') return;
 messages = [...messages.slice(0, -1), { ...last, routing: r }];
 },
 modelPref,
 effort
 );
 }

 function handleKeydown(e: KeyboardEvent) {
 if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
 }

 // Feature E — share a read-only public snapshot of this conversation.
 let shareLink = $state('');
 let shareBusy = $state(false);
 async function shareConversation() {
 if (shareBusy || !sessionId) return;
 shareBusy = true;
 try {
 const res = await fetch(`/api/projects/${projectSlug}/chats/${encodeURIComponent(sessionId)}/share`, {
 method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ expire_days: 7, include_lineage: true })
 });
 const d = await res.json();
 if (d.url) {
 const full = `${location.origin}${d.url}`;
 shareLink = full;
 try { await navigator.clipboard.writeText(full); } catch {}
 }
 } catch {} finally { shareBusy = false; }
 }

 function renderStars(score: number | undefined): string {
 if (!score) return '';
 return ''.repeat(score) + ''.repeat(5 - score);
 }

 function autoResize() {
 if (textareaEl) {
 textareaEl.style.height = 'auto';
 textareaEl.style.height = Math.min(textareaEl.scrollHeight, 120) + 'px';
 }
 }

 let pinned = $state<Record<number, boolean>>({});
 let abortController: AbortController | null = null;

 // Dashboard side panel
 let dashboardPanelOpen = $state(false);
 let activeDashboardId = $state<number | null>(null);
 let dashboardGenerating = $state(false);
 let unsavedDashboardId = $state<number | null>(null);

 // Slides artifact panel (skl_pptx_builder)
 let presPanelOpen = $state(false);
 let activePresId = $state<number | null>(null);

 // Deep Deck panel (DP button — 6-stage research pipeline)
 let deepDeckOpen = $state(false);

 // Research deep-dive export — POST last question to /api/research/deep, download PDF
 let researchBusy = $state(false);
 async function exportResearchChat() {
   if (researchBusy || messages.length < 1) return;
   const lastUser = [...messages].reverse().find(m => m.role === 'user');
   const lastUserQuestion = lastUser?.content?.trim();
   if (!lastUserQuestion) return;
   researchBusy = true;
   try {
     const res = await fetch('/api/research/deep', {
       method: 'POST',
       headers: { ..._headers(), 'Content-Type': 'application/json' },
       body: JSON.stringify({ question: lastUserQuestion, project_slug: projectSlug }),
     });
     if (!res.ok) throw new Error(`HTTP ${res.status}`);
     const data = await res.json();
     if (!data?.run_id) throw new Error('No run_id returned');
     const pdfRes = await fetch(`/api/research/${encodeURIComponent(data.run_id)}/pdf`, { headers: _headers() });
     if (!pdfRes.ok) throw new Error(`PDF HTTP ${pdfRes.status}`);
     const blob = await pdfRes.blob();
     const url = URL.createObjectURL(blob);
     const a = document.createElement('a');
     a.href = url;
     a.download = `research_${(projectSlug || 'project').replace(/[^\w-]/g,'_')}_${Date.now()}.pdf`;
     document.body.appendChild(a); a.click(); a.remove();
     URL.revokeObjectURL(url);
   } catch (e) {
     console.error('Research export failed', e);
   } finally {
     researchBusy = false;
   }
 }

 // Excel export — POST messages to /api/export/excel-from-chat, download .xlsx
 let excelBusy = $state(false);
 async function exportExcelChat() {
   if (excelBusy || messages.length < 2) return;
   excelBusy = true;
   try {
     const res = await fetch('/api/export/excel-from-chat', {
       method: 'POST',
       headers: { ..._headers(), 'Content-Type': 'application/json' },
       body: JSON.stringify({
         messages: messages.map(m => ({ role: m.role, content: m.content, sqlQueries: (m as any).sqlQueries || [] })),
         title: `${projectInfo?.name || 'Analysis'} — Chat Export`,
         agent_name: projectInfo?.agent_name || 'Agent',
       }),
     });
     if (!res.ok) throw new Error(`HTTP ${res.status}`);
     const blob = await res.blob();
     const url = URL.createObjectURL(blob);
     const a = document.createElement('a');
     a.href = url;
     a.download = `${(projectInfo?.name || 'export').replace(/[^\w-]/g,'_')}_${new Date().toISOString().slice(0,10)}.xlsx`;
     document.body.appendChild(a); a.click(); a.remove();
     URL.revokeObjectURL(url);
   } catch (e) {
     console.error('Excel export failed', e);
   } finally {
     excelBusy = false;
   }
 }

 function startDeepDeck() {
   if (messages.length < 2) return;
   // Close other panels
   slidesPanelOpen = false;
   presPanelOpen = false;
   pptxSteps = [];
   deepDeckOpen = true;
 }

 function onDeepDeckComplete(presId: number) {
   deepDeckOpen = false;
   activePresId = presId;
   presPanelOpen = true;
 }

 // Deep Dashboard artifact panel
 let dashPanelOpen = $state(false);
 let dashSpec = $state<any>(null);
 let dashData = $state<any>({});
 let dashThinking = $state<string[]>([]);
 let dashDeepening = $state(false);
 let dashFindings = $state<any[]>([]);
 let dashVersions = $state<any[]>([]);
 let dashCurrentVersion = $state<number | null>(null);
 let dashVersionsDropdownOpen = $state(false);

 // CHAT→DASH (build dashboard from full chat session via SSE)
 let chatDashBusy = $state(false);
 let splitMode = $state(false);  // when true, chat shrinks to 50% + dashboard renders right
 let chatDashProgress = $state<any>({ stage: '', current: 0, total: 9, panel_pills: [] as any[], narrative: '', complete: false, error: '', dashboard_id: '', started_at: 0, stages_done: [] as any[], events_log: [] as any[], tokens: 0, cost_usd: 0, intent: null, schema_tables: 0, sql_count: 0 });
 let chatDashElapsed = $state(0);  // seconds, ticks every 1s while busy
 const DASH_STAGE_DEFS: Record<string, { label: string; desc: string }> = {
   intent_classify:     { label: '1 · Intent',       desc: 'Classifying chat intent + extracting audience + topics' },
   schema_rag:          { label: '2 · Schema RAG',   desc: 'Pulling table catalog + Codex pipeline_logic + Brain context' },
   panel_plan:          { label: '3 · Panel Plan',   desc: 'Deciding 4–12 panels: KPIs, charts, tables, narratives' },
   sql_gen:             { label: '4 · SQL Gen',      desc: 'Writing Postgres SELECT per panel (dialect-aware, dtype casts)' },
   explain_gate:        { label: '5 · EXPLAIN gate', desc: 'Validating SQL via EXPLAIN before execute; retries on failure' },
   execute:             { label: '6 · Execute',      desc: 'Running SQL against read-only project engine; profiling rows' },
   executive_overview:  { label: '6.5 · Overview',   desc: 'Drafting truth-grounded narrative (uses verified metrics)' },
   chart_specs:         { label: '7 · Chart Specs',  desc: 'Generating ECharts options per panel + Pydantic validation' },
   panel_announce:      { label: '7.5 · Announce',   desc: 'Emitting per-panel chat pill + mini sparkline' },
   judge:               { label: '8 · Judge',        desc: 'Different-model critic scoring spec (TACL rule)' },
   layout:              { label: '9 · Layout',       desc: 'Packing panels into grid: executive | operational | narrative' },
   intent: { label: '1 · Intent', desc: 'Classifying chat intent + extracting audience + topics' },
 };
 const DASH_STAGE_ORDER = ['intent_classify','schema_rag','panel_plan','sql_gen','explain_gate','execute','executive_overview','chart_specs','panel_announce','judge','layout'];

 async function loadDashForSession() {
   if (!sessionId || !projectSlug) return;
   try {
     const r = await fetch(`/api/dashboards/by-session/${encodeURIComponent(sessionId)}/latest?project_slug=${encodeURIComponent(projectSlug)}`, { headers: _headers() });
     if (r.status === 404) { dashSpec = null; dashVersions = []; dashCurrentVersion = null; splitMode = false; return; }
     if (!r.ok) return;
     const j = await r.json();
     const s = j.spec || {};
     if ((!Array.isArray(s.cells) || s.cells.length === 0) && Array.isArray(s.panels) && s.panels.length) {
       s.cells = s.panels.map((p:any) => panelToCell(p));
     }
     s.id = j.dashboard_id;
     dashSpec = s;
     dashCurrentVersion = j.version;
     splitMode = true;
     fetchDashData(s);
     // also load version list
     const lr = await fetch(`/api/dashboards/by-session/${encodeURIComponent(sessionId)}?project_slug=${encodeURIComponent(projectSlug)}`, { headers: _headers() });
     if (lr.ok) dashVersions = await lr.json();
   } catch {}
 }

 async function loadDashVersion(dashboardId: string) {
   try {
     const r = await fetch(`/api/dashboards/${encodeURIComponent(dashboardId)}`, { headers: _headers() });
     if (!r.ok) return;
     const j = await r.json();
     const s = j.spec || j;
     if ((!Array.isArray(s.cells) || s.cells.length === 0) && Array.isArray(s.panels) && s.panels.length) {
       s.cells = s.panels.map((p:any) => panelToCell(p));
     }
     s.id = dashboardId;
     dashSpec = s;
     const ver = dashVersions.find(v => v.dashboard_id === dashboardId);
     dashCurrentVersion = ver ? ver.version : null;
     fetchDashData(s);
     dashVersionsDropdownOpen = false;
   } catch {}
 }

 async function deleteDashVersion(dashboardId: string, version: number) {
   if (dashVersions.length <= 1) return;
   if (!confirm(`Delete v${version}? This cannot be undone.`)) return;
   try {
     const r = await fetch(`/api/dashboards/${encodeURIComponent(dashboardId)}?project_slug=${encodeURIComponent(projectSlug)}`, { method: 'DELETE', headers: _headers() });
     if (!r.ok) { alert('Delete failed'); return; }
     const lr = await fetch(`/api/dashboards/by-session/${encodeURIComponent(sessionId)}?project_slug=${encodeURIComponent(projectSlug)}`, { headers: _headers() });
     if (lr.ok) dashVersions = await lr.json();
     if (dashboardId === dashSpec?.id) {
       if (dashVersions.length > 0) {
         await loadDashVersion(dashVersions[0].dashboard_id);
       } else {
         dashSpec = null;
         dashCurrentVersion = null;
         splitMode = false;
       }
     }
   } catch (e) {
     alert('Delete failed');
   }
 }

 async function buildDashFromChat(force = false) {
   if (chatDashBusy || messages.length === 0) return;
   // If we already have dashSpec for this session and not forcing, just open pane
   if (!force && dashSpec && dashCurrentVersion != null) { splitMode = true; return; }
   chatDashBusy = true;
   splitMode = true;   // open right pane immediately — progress lives there, not at bottom
   const t0 = Date.now();
   chatDashProgress = { stage: 'starting', current: 0, total: 9, panel_pills: [], narrative: '', complete: false, error: '', dashboard_id: '', started_at: t0, stages_done: [], events_log: [{ at: 0, type: 'start', msg: `POST /api/dashboards/from-chat/stream · session=${sessionId}` }], tokens: 0, cost_usd: 0, intent: null, schema_tables: 0, sql_count: 0 };
   chatDashElapsed = 0;
   const tick = setInterval(() => { if (chatDashBusy) chatDashElapsed = Math.floor((Date.now() - t0) / 1000); else clearInterval(tick); }, 1000);
   try {
     const res = await fetch('/api/dashboards/from-chat/stream', {
       method: 'POST',
       headers: { ..._headers(), 'content-type': 'application/json' },
       body: JSON.stringify({ project_slug: projectSlug, session_id: sessionId, audience: 'Exec', deepen: true, force_rebuild: force })
     });
     if (!res.body) { chatDashBusy = false; chatDashProgress = { ...chatDashProgress, error: 'No response body' }; return; }
     const reader = res.body.getReader();
     const decoder = new TextDecoder();
     let buf = '';
     while (true) {
       const { done, value } = await reader.read();
       if (done) break;
       buf += decoder.decode(value, { stream: true });
       const blocks = buf.split('\n\n');
       buf = blocks.pop() || '';
       for (const block of blocks) {
         if (!block.startsWith('data: ')) continue;
         try {
           const evt = JSON.parse(block.slice(6));
           const t = evt.type;
           const _now = Math.floor((Date.now() - chatDashProgress.started_at) / 1000);
           if (t === 'stage_start') {
             const stg = evt.stage || evt.name || '';
             const def = DASH_STAGE_DEFS[stg];
             chatDashProgress = {
               ...chatDashProgress,
               stage: stg,
               current: Math.min((chatDashProgress.current || 0) + 1, chatDashProgress.total),
               events_log: [...chatDashProgress.events_log, { at: _now, type: 'stage_start', stage: stg, msg: def ? def.desc : `Stage: ${stg}` }],
             };
           } else if (t === 'stage_done') {
             const stg = evt.stage || evt.name || chatDashProgress.stage;
             const took = evt.duration_ms != null ? Math.round(evt.duration_ms) : null;
             const stages_done = [...chatDashProgress.stages_done, { stage: stg, at: _now, took_ms: took, payload: evt.payload || evt.result || null }];
             // capture stage-specific payload details
             let extra: any = {};
             const p = evt.payload || evt.result || {};
             if (stg === 'intent_classify')     extra.intent = p.intent || p.classification || p;
             else if (stg === 'schema_rag')     extra.schema_tables = (p.tables && p.tables.length) || p.n_tables || chatDashProgress.schema_tables;
             else if (stg === 'sql_gen')        extra.sql_count = (p.sqls && p.sqls.length) || p.n_sqls || chatDashProgress.sql_count;
             chatDashProgress = {
               ...chatDashProgress,
               ...extra,
               stages_done,
               events_log: [...chatDashProgress.events_log, { at: _now, type: 'stage_done', stage: stg, msg: took != null ? `✓ ${stg} (${took}ms)` : `✓ ${stg}` }],
             };
           } else if (t === 'panel_ready') {
             const pn = evt.panel || {};
             const title = pn.title || pn.name || evt.title || 'Panel';
             const rows = pn.row_count || pn.rows || evt.row_count;
             const chart_type = pn.chart_type || pn.panel_type || evt.chart_type || '';
             const cols = (pn.columns && pn.columns.length) || evt.n_columns;
             chatDashProgress = {
               ...chatDashProgress,
               panel_pills: [...chatDashProgress.panel_pills, { title, rows, chart_type, cols, panel_id: pn.id || pn.panel_id, kind: 'panel' }],
               events_log: [...chatDashProgress.events_log, { at: _now, type: 'panel', msg: `+ panel: ${title}${chart_type ? ` (${chart_type})` : ''}${rows ? ` · ${rows} rows` : ''}` }],
             };
           } else if (t === 'panel_announcement') {
             chatDashProgress = { ...chatDashProgress, panel_pills: [...chatDashProgress.panel_pills, { title: evt.message || evt.title || 'Panel announced', mini: evt.mini_thumbnail_spec, kind: 'announcement' }] };
           } else if (t === 'narrative_ready') {
             chatDashProgress = {
               ...chatDashProgress,
               narrative: evt.text || evt.narrative || '',
               events_log: [...chatDashProgress.events_log, { at: _now, type: 'narrative', msg: `narrative ready (${(evt.verified_value_count != null ? evt.verified_value_count + ' verified' : (evt.text || '').length + ' chars')})` }],
             };
           } else if (t === 'done') {
             const dashId = evt.dashboard_id || (evt.spec && evt.spec.id) || '';
             let s: any = null;
             if (evt.spec && typeof evt.spec === 'object') {
               s = { ...evt.spec };
               if ((!Array.isArray(s.cells) || s.cells.length === 0) && Array.isArray(s.panels) && s.panels.length) {
                 s.cells = s.panels.map((p: any) => panelToCell(p));
               }
               s.id = dashId;
               dashSpec = s;
               dashFindings = (evt.spec.insights || evt.spec.findings || []);
               splitMode = true;
               if (evt.version) dashCurrentVersion = evt.version;
               try { fetchDashData(s); } catch (e) { console.warn('fetchDashData failed', e); }
             } else if (dashId) {
               try {
                 const r = await fetch(`/api/dashboards/${encodeURIComponent(dashId)}`, { headers: _headers() });
                 if (r.ok) {
                   const j = await r.json();
                   s = j.spec || j;
                   if ((!Array.isArray(s.cells) || s.cells.length === 0) && Array.isArray(s.panels) && s.panels.length) {
                     s.cells = s.panels.map((p: any) => panelToCell(p));
                   }
                   s.id = dashId;
                   dashSpec = s;
                   splitMode = true;
                   if (j.version) dashCurrentVersion = j.version;
                   try { fetchDashData(s); } catch {}
                 }
               } catch (e) { console.warn('post-done fetch failed', e); }
             }
             chatDashProgress = {
               ...chatDashProgress,
               complete: true,
               dashboard_id: dashId,
               tokens: evt.tokens_used || evt.tokens || chatDashProgress.tokens,
               cost_usd: evt.cost_usd || evt.cost || chatDashProgress.cost_usd,
               events_log: [...chatDashProgress.events_log, { at: _now, type: 'done', msg: `✓ done · ${s ? (Array.isArray(s.cells) ? s.cells.length : 0) : 0} cells rendered` }],
             };
             // reload versions list
             try {
               const lr = await fetch(`/api/dashboards/by-session/${encodeURIComponent(sessionId)}?project_slug=${encodeURIComponent(projectSlug)}`, { headers: _headers() });
               if (lr.ok) dashVersions = await lr.json();
             } catch {}
           } else if (t === 'error' || t === 'stage_error') {
             chatDashProgress = { ...chatDashProgress, error: evt.detail || evt.error || 'Build error' };
           }
         } catch {}
       }
     }
   } catch (e: any) {
     chatDashProgress = { ...chatDashProgress, error: e?.message || 'Network error' };
   }
   chatDashBusy = false;
 }

 function dismissChatDashProgress() {
   chatDashBusy = false;
   chatDashProgress = { stage: '', current: 0, total: 9, panel_pills: [], narrative: '', complete: false, error: '', dashboard_id: '' };
 }

 async function fetchDashData(spec: any) {
 if (!spec) return;
 try {
 const r = await fetch('/api/dashboards/run-data', {
 method: 'POST',
 headers: { ..._headers(), 'content-type': 'application/json' },
 body: JSON.stringify({ spec, project_slug: projectSlug })
 });
 const d = await r.json();
 if (d.ok) dashData = d.data || {};
 } catch {}
 }

 // Adapt new Deep Dash 9-stage panel → legacy cell shape consumed by DashRenderer/Cell.svelte
 function panelToCell(p: any) {
   const grid = Array.isArray(p.grid) && p.grid.length >= 4 ? p.grid : [0, 0, 6, 3];
   const ptype = (p.panel_type || 'chart').toLowerCase();
   const type = ptype === 'kpi' ? 'kpi'
              : ptype === 'insight' ? 'insight'
              : ptype === 'narrative' ? 'insight'
              : ptype === 'table' ? 'table'
              : 'chart';
   return {
     id: p.panel_id || `p_${Math.random().toString(36).slice(2, 8)}`,
     type,
     grid,
     title: p.title || '',
     config: {
       chart_type: p.chart_type || 'bar',
       echarts_options: p.options || {},   // Cell.svelte uses this directly (Deep Dash path)
       narrative: p.narrative || '',
       confidence: p.confidence || 'medium',
       sources: p.sources || [],
       headline: (type === 'insight' || type === 'kpi') ? (p.title || '') : undefined,
       cause: (type === 'insight') ? (p.narrative || '') : undefined,
     },
   };
 }

 async function openDeepDashboard(force: boolean = false) {
 const lastAssistant = [...messages].reverse().find(m => m.role === 'assistant');
 if (!lastAssistant) {
 alert('Ask agent a question first');
 return;
 }
 // Compose question from last user turn (or assistant content if no user turn yet)
 const lastUser = [...messages].reverse().find(m => m.role === 'user');
 const question = (lastUser?.content || lastAssistant.content || '').slice(0, 2000);
 // Check sessionStorage cache for this chat session
 const cacheKey = `dash_panel_${sessionId}`;
 if (!force) {
 try {
 const cached = sessionStorage.getItem(cacheKey);
 if (cached) {
 const { spec, data, findings } = JSON.parse(cached);
 if (spec) {
 dashSpec = spec;
 dashData = data || {};
 dashFindings = findings || [];
 dashThinking = [' loaded cached dashboard'];
 dashDeepening = false;
 dashPanelOpen = true;
 return;
 }
 }
 } catch {}
 }
 dashPanelOpen = true;
 dashThinking = ['[ORCH] starting multi-agent analysis...'];
 dashDeepening = true;
 dashSpec = null;
 dashData = {};
 dashFindings = [];

 // NEW: 9-stage Deep Dash event handler.
 // SSE event types: stage_start, stage_done, stage_error, panel_ready, done, error
 function handleEvent(evt: any) {
   if (evt.type === 'stage_start') {
     dashThinking = [...dashThinking, `[${evt.n}/9] ${evt.stage} starting…`];
   } else if (evt.type === 'stage_done') {
     let tail = '';
     if (evt.stage === 'panel_plan' && evt.panels) tail = ` (${evt.panels.length} panels)`;
     else if (evt.stage === 'explain_gate') tail = ` passed=${evt.passed} failed=${evt.failed}`;
     else if (evt.stage === 'chart_specs') tail = ` count=${evt.count}`;
     else if (evt.stage === 'judge') tail = ` score=${evt.score} issues=${evt.issues}`;
     dashThinking = [...dashThinking, `[${evt.n}/9] ${evt.stage} done${tail}`];
   } else if (evt.type === 'stage_error') {
     dashThinking = [...dashThinking, `[ERR] ${evt.stage}: ${evt.error}`];
   } else if (evt.type === 'panel_ready') {
     const cell = panelToCell(evt.panel || {});
     dashThinking = [...dashThinking, `[panel] ${cell.title}`];
     dashSpec = dashSpec
       ? { ...dashSpec, cells: [...(dashSpec.cells || []), cell] }
       : { id: '', title: 'Dashboard', cells: [cell], insights: [] };
   } else if (evt.type === 'done') {
     const finalSpec = evt.spec || {};
     // Replace cells with final ordered panels (layout stage applies grid packing)
     if (Array.isArray(finalSpec.panels) && finalSpec.panels.length) {
       const cells = finalSpec.panels.map(panelToCell);
       dashSpec = {
         id: finalSpec.id || '',
         title: finalSpec.title || 'Dashboard',
         project_slug: finalSpec.project_slug || projectSlug,
         cells,
         insights: [],
         template: finalSpec.layout || 'executive',
         persona: finalSpec.persona || '',
         spec_version: finalSpec.spec_version || 1,
         judge_score: finalSpec.judge_score,
         _deep_dash_raw: finalSpec,
       };
     }
     dashThinking = [...dashThinking, `[done] tokens=${evt.tokens_used} wall=${evt.wall_s}s`];
     dashDeepening = false;
     try {
       sessionStorage.setItem(`dash_panel_${sessionId}`, JSON.stringify({ spec: dashSpec, data: dashData, findings: dashFindings }));
     } catch {}
   } else if (evt.type === 'error') {
     dashThinking = [...dashThinking, `[ERROR] ${evt.error}`];
     dashDeepening = false;
   }
 }

 try {
 const allQuestions = messages
 .filter((m: any) => m.role === 'user')
 .map((m: any) => m.content || m.text || '')
 .filter(Boolean);
 const questionText = allQuestions.length > 1
 ? `Build dashboard covering: ${allQuestions.map((q: string, i: number) => `${i+1}) ${q}`).join(' ')}`
 : (allQuestions[0] || question || 'overview dashboard');

 const body: any = {
   project_slug: projectSlug,
   question: questionText,
   audience: 'executive',
   n_panels: 8,
 };
 const res = await fetch('/api/dashboards/deep-build/stream', {
 method: 'POST', headers: { ..._headers(), 'content-type':'application/json' },
 body: JSON.stringify(body)
 });
 if (!res.body) { dashDeepening = false; return; }
 const reader = res.body.getReader();
 const decoder = new TextDecoder();
 let buf = '';
 while (true) {
 const { done, value } = await reader.read();
 if (done) break;
 buf += decoder.decode(value, { stream: true });
 const blocks = buf.split('\n\n');
 buf = blocks.pop() || '';
 for (const block of blocks) {
 if (!block.startsWith('data: ')) continue;
 try {
 const evt = JSON.parse(block.slice(6));
 handleEvent(evt);
 } catch {}
 }
 }
 } catch (e: any) {
 dashThinking = [...dashThinking, `[ERROR] ${e?.message || 'error'}`];
 }
 dashDeepening = false;
 try {
 if (dashSpec) {
 sessionStorage.setItem(`dash_panel_${sessionId}`, JSON.stringify({ spec: dashSpec, data: dashData, findings: dashFindings }));
 }
 } catch {}
 }

 async function saveDashFromPanel() {
 if (!dashSpec) return;
 try {
 const r = await fetch('/api/dashboards/save', {
 method: 'POST', headers: { ..._headers(), 'content-type':'application/json' },
 body: JSON.stringify(dashSpec)
 });
 const d = await r.json();
 if (d.ok) {
 dashThinking = [...dashThinking, ` saved: /project/${projectSlug}/dashboards/${d.id}`];
 await loadRecentDashboards();
 } else {
 dashThinking = [...dashThinking, ' save failed: ' + (d.detail || d.error || 'unknown')];
 }
 } catch (e: any) {
 dashThinking = [...dashThinking, ' ' + (e?.message || 'save error')];
 }
 }

 // Slide Agent panel
 let slidesPanelOpen = $state(false);
 let slidesData = $state<any[]>([]);
 let slidesThinking = $state<any>(null);
 let slidesLoading = $state(false);
 let slidesProgress = $state('');
 let currentSlide = $state(0);
 let showSaveModal = $state(false);
 let saveTitle = $state('');
 let pptxSteps = $state<{label: string; status: 'pending'|'active'|'done'|'error'}[]>([]);
 let pptxGenerating = $state(false);
 let pptxSavedVersion = $state(0);

 async function generateDashboardFromChat() {
 if (dashboardGenerating || messages.length < 2) return;
 dashboardGenerating = true;
 dashboardPanelOpen = true;
 activeDashboardId = null;

 try {
 const chatContent = messages
 .filter(m => m.role === 'assistant' && m.status === 'done')
 .map(m => m.content)
 .join('\n\n---\n\n');
 const sqlQueries = messages
 .filter(m => m.sqlQueries?.length)
 .flatMap(m => m.sqlQueries || []);

 const res = await fetch(`/api/projects/${projectSlug}/generate-dashboard-from-chat`, {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 chat_content: chatContent.slice(0, 10000),
 sql_queries: sqlQueries.slice(0, 20),
 session_id: sessionId,
 message_count: messages.length
 })
 });
 if (res.ok) {
 const data = await res.json();
 if (data.dashboard_id) {
 activeDashboardId = data.dashboard_id;
 unsavedDashboardId = data.dashboard_id;
 }
 }
 } catch {}
 dashboardGenerating = false;
 }

 async function discardDashboard() {
 if (!unsavedDashboardId) return;
 try {
 await fetch(`/api/projects/${projectSlug}/dashboards/${unsavedDashboardId}`, {
 method: 'DELETE',
 headers: _headers()
 });
 } catch {}
 activeDashboardId = null;
 unsavedDashboardId = null;
 dashboardPanelOpen = false;
 }

 function confirmDashboardSave() {
 unsavedDashboardId = null;
 }

 async function generateSlides() {
 if (messages.length < 2 || slidesLoading) return;
 slidesLoading = true;
 pptxGenerating = true;
 slidesPanelOpen = true;
 slidesData = [];
 slidesThinking = null;
 currentSlide = 0;
 pptxSavedVersion = 0;

 pptxSteps = [
 { label: `Reading conversation (${messages.filter(m => m.role === 'user').length} questions)`, status: 'active' },
 { label: 'Analyzing narrative and key insights', status: 'pending' },
 { label: 'Planning slide structure', status: 'pending' },
 { label: 'Generating slide content', status: 'pending' },
 ];

 const updateStep = (idx: number, status: 'done'|'active'|'error', label?: string) => {
 pptxSteps = pptxSteps.map((s, i) => {
 if (i === idx) return { ...s, status, label: label || s.label };
 if (i < idx && s.status !== 'error') return { ...s, status: 'done' };
 return s;
 });
 };

 try {
 await new Promise(r => setTimeout(r, 300));
 updateStep(0, 'done');
 updateStep(1, 'active');
 await new Promise(r => setTimeout(r, 200));
 updateStep(2, 'active');

 const res = await fetch('/api/export/slides-agent', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 messages: messages.map(m => ({ role: m.role, content: m.content })),
 title: projectInfo?.agent_name + ' Analysis',
 agent_name: projectInfo?.agent_name || 'Agent'
 })
 });

 if (res.ok) {
 const data = await res.json();
 slidesThinking = data.thinking;
 slidesData = data.slides || [];
 updateStep(1, 'done');
 updateStep(2, 'done', `Planned ${slidesData.length} slides`);
 updateStep(3, 'done');

 // Auto-save to dash_presentations + open editable SlidesPanel (skl_pptx_builder flow)
 try {
 const saveRes = await fetch('/api/export/presentations', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 project_slug: projectSlug,
 title: (projectInfo?.agent_name || 'Agent') + ' Analysis',
 thinking: slidesThinking,
 slides: slidesData,
 source_messages: messages.slice(-20).map(m => ({ role: m.role, content: m.content })),
 }),
 });
 if (saveRes.ok) {
 const sd = await saveRes.json();
 if (sd?.id) {
 // Close legacy panel + open new editable SlidesPanel
 slidesPanelOpen = false;
 pptxSteps = [];
 activePresId = sd.id;
 presPanelOpen = true;
 }
 }
 } catch (saveErr) {
 // Save failed — legacy panel still shows preview, user can still edit there
 console.warn('auto-save failed, legacy panel remains', saveErr);
 }
 } else {
 updateStep(2, 'error', 'Failed to generate slides');
 }
 } catch (e) {
 const failIdx = pptxSteps.findIndex(s => s.status === 'active');
 if (failIdx >= 0) pptxSteps = pptxSteps.map((s, i) => i === failIdx ? { ...s, status: 'error' as const, label: 'Error' } : s);
 }
 slidesLoading = false;
 pptxGenerating = false;
 // Auto-show slides after 1 second
 if (slidesData.length > 0) {
 setTimeout(() => { pptxSteps = []; }, 1500);
 }
 }

 async function savePresentation() {
 if (slidesData.length === 0) return;
 saveTitle = (projectInfo?.agent_name || 'Agent') + ' Analysis';
 showSaveModal = true;
 }

 async function confirmSave() {
 if (!saveTitle.trim()) return;
 try {
 const res = await fetch('/api/export/presentations', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 project_slug: projectSlug,
 title: saveTitle.trim(),
 thinking: slidesThinking,
 slides: slidesData,
 source_messages: messages.map(m => ({ role: m.role, content: m.content }))
 })
 });
 if (res.ok) {
 const d = await res.json();
 showSaveModal = false;
 slidesProgress = `Saved as v${d.version}`;
 setTimeout(() => slidesProgress = '', 3000);
 }
 } catch {}
 }

 async function downloadPptx() {
 if (messages.length < 2 || pptxGenerating) return;
 pptxGenerating = true;
 slidesPanelOpen = true;
 slidesData = [];
 slidesThinking = null;
 pptxSavedVersion = 0;
 const agentTitle = (projectInfo?.agent_name || 'Agent') + ' Analysis';

 pptxSteps = [
 { label: `Reading conversation (${messages.filter(m => m.role === 'user').length} questions)`, status: 'active' },
 { label: 'Analyzing narrative and key insights', status: 'pending' },
 { label: 'Planning slides', status: 'pending' },
 { label: 'Generating slide content', status: 'pending' },
 { label: 'Creating PowerPoint file', status: 'pending' },
 { label: 'Saving to presentations', status: 'pending' },
 { label: 'Downloading PPTX', status: 'pending' },
 ];

 const updateStep = (idx: number, status: 'done'|'active'|'error', label?: string) => {
 pptxSteps = pptxSteps.map((s, i) => {
 if (i === idx) return { ...s, status, label: label || s.label };
 if (i < idx && s.status !== 'error') return { ...s, status: 'done' };
 return s;
 });
 };

 try {
 // Step 1: Read conversation
 await new Promise(r => setTimeout(r, 300));
 updateStep(0, 'done');
 updateStep(1, 'active');

 // Step 2-4: Call slides-agent (think + generate)
 await new Promise(r => setTimeout(r, 200));
 updateStep(2, 'active');
 const agentRes = await fetch('/api/export/slides-agent', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 messages: messages.map(m => ({ role: m.role, content: m.content })),
 title: agentTitle,
 agent_name: projectInfo?.agent_name || 'Agent'
 })
 });

 if (!agentRes.ok) { updateStep(2, 'error', 'Failed to analyze conversation'); pptxGenerating = false; return; }
 const agentData = await agentRes.json();
 slidesThinking = agentData.thinking;
 slidesData = agentData.slides || [];
 updateStep(1, 'done');
 updateStep(2, 'done', `Planning ${slidesData.length} slides`);
 updateStep(3, 'done');
 updateStep(4, 'active');

 // Step 5: Save to DB
 await new Promise(r => setTimeout(r, 300));
 updateStep(5, 'active');
 const saveRes = await fetch('/api/export/presentations', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 project_slug: projectSlug,
 title: agentTitle,
 thinking: slidesThinking,
 slides: slidesData,
 source_messages: messages.map(m => ({ role: m.role, content: m.content }))
 })
 });

 let presId = 0;
 if (saveRes.ok) {
 const d = await saveRes.json();
 presId = d.id;
 pptxSavedVersion = d.version;
 updateStep(5, 'done', `Saved as "${agentTitle} v${d.version}"`);
 } else {
 updateStep(5, 'error', 'Save failed');
 }

 // Step 6: Create and download PPTX
 updateStep(4, 'done');
 updateStep(6, 'active');
 if (presId) {
 const pptxRes = await fetch(`/api/export/presentations/${presId}/pptx`, {
 method: 'POST', headers: _headers()
 });
 if (pptxRes.ok) {
 const blob = await pptxRes.blob();
 const url = URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url;
 a.download = `${projectInfo?.agent_name || 'presentation'}-v${pptxSavedVersion}.pptx`;
 a.click();
 URL.revokeObjectURL(url);
 updateStep(6, 'done', `Downloaded ${projectInfo?.agent_name}-v${pptxSavedVersion}.pptx`);
 } else {
 updateStep(6, 'error', 'Download failed');
 }
 }
 } catch (e) {
 const failIdx = pptxSteps.findIndex(s => s.status === 'active');
 if (failIdx >= 0) updateStep(failIdx, 'error', 'Failed');
 }
 pptxGenerating = false;
 }

 function downloadHTML() {
 if (slidesData.length === 0) return;
 const title = (projectInfo?.agent_name || 'Presentation') + ' Analysis';
 const date = new Date().toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'});
 const agent = projectInfo?.agent_name || 'Agent';
 const narrative = slidesThinking?.narrative || '';
 const keyInsight = slidesThinking?.key_insight || '';

 let slidesHTML = '';
 for (let i = 0; i < slidesData.length; i++) {
 const s = slidesData[i];
 const layout = s.layout || 'bullets';
 const sTitle = s.title || '';
 const bullets = (s.bullets || []).map((b: string, bi: number) => `<div style="font-size:14px;color:#333;padding:10px 14px;border-left:4px solid #D24726;margin-bottom:8px;background:#fafaf8;"><span style="font-weight:800;color:#D24726;">${bi+1}.</span> ${b}</div>`).join('');
 const actionLine = s.action_line ? `<div style="margin-top:20px;padding:12px 16px;border-top:2px solid #1a1a1a;font-size:12px;font-weight:700;">${s.action_line}</div>` : '';
 const topic = s.topic || 'ANALYSIS';

 let content = '';
 if (layout === 'cover') {
 content = `<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;text-align:center;">
 <div style="font-size:42px;font-weight:900;color:#1a1a1a;max-width:700px;">${sTitle}</div>
 <div style="width:80px;height:3px;background:linear-gradient(90deg,#D24726,#F0A030);margin:24px auto;"></div>
 ${s.bullets?.[0] ? `<div style="font-size:16px;color:#666;">${s.bullets[0]}</div>` : ''}
 <div style="font-size:11px;color:#999;margin-top:40px;text-transform:uppercase;letter-spacing:0.1em;">${agent} &middot; ${date}</div>
 <div style="font-size:10px;color:#bbb;margin-top:8px;letter-spacing:0.15em;">POWERED BY RLAI DASH</div>
 </div>`;
 } else if (layout === 'kpi' && s.kpis?.length) {
 const kpis = s.kpis.map((k: any) => `<div style="flex:1;text-align:center;padding:24px;border:2px solid #e0e0d8;">
 <div style="font-size:42px;font-weight:900;">${k.value}</div>
 <div style="font-size:11px;color:#888;text-transform:uppercase;margin-top:6px;">${k.label}</div>
 ${k.change ? `<div style="font-size:14px;font-weight:700;color:${k.change?.startsWith('+') ? '#00873c' : '#d32f2f'};margin-top:4px;">${k.change}</div>` : ''}
 </div>`).join('');
 content = `<div style="font-size:19px;font-weight:800;margin-bottom:20px;">${sTitle}</div>
 <div style="width:100%;height:2px;background:#1a1a1a;margin-bottom:24px;"></div>
 <div style="display:flex;gap:16px;margin:20px 0;">${kpis}</div>${bullets}${actionLine}`;
 } else if (s.chart) {
 const chartId = 'chart_' + i;
 const chartType = s.chart.type === 'horizontal_bar' ? 'bar' : (s.chart.type || 'bar');
 const labels = JSON.stringify(s.chart.labels || []);
 const values = JSON.stringify(s.chart.values || []);
 let chartOpt = '';
 if (chartType === 'pie') {
 chartOpt = `{tooltip:{trigger:'item',formatter:'{b}: {c} ({d}%)'},series:[{type:'pie',radius:['35%','60%'],data:${labels}.map((l,i)=>({name:l,value:${values}[i]})),label:{fontSize:11},itemStyle:{borderRadius:4,borderColor:'#fff',borderWidth:2}}]}`;
 } else {
 chartOpt = `{tooltip:{trigger:'axis'},xAxis:{type:'category',data:${labels},axisLabel:{fontSize:10,rotate:${labels}.length>5?25:0}},yAxis:{type:'value'},series:[{type:'${chartType}',data:${values},itemStyle:{color:'#D24726',borderRadius:[4,4,0,0]}${chartType==='line'?',smooth:true,areaStyle:{opacity:0.08}':''}}],grid:{left:'10%',right:'5%',bottom:'15%',top:'12%'}}`;
 }
 content = `<div style="font-size:19px;font-weight:800;margin-bottom:20px;">${sTitle}</div>
 <div style="width:100%;height:2px;background:#1a1a1a;margin-bottom:20px;"></div>
 <div style="display:flex;gap:24px;">
 <div style="flex:3;"><div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#888;margin-bottom:8px;">${s.chart.title || 'EXHIBIT'}</div><div id="${chartId}" style="width:100%;height:300px;"></div><div style="font-size: 11px;color:#aaa;margin-top:4px;">Source: Project data</div></div>
 <div style="flex:2;border-left:2px solid #e0e0d8;padding-left:16px;"><div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#888;margin-bottom:12px;">KEY TAKEAWAYS</div>${bullets}</div>
 </div>${actionLine}
 <script>echarts.init(document.getElementById('${chartId}')).setOption(${chartOpt});<\/script>`;
 } else if (s.table) {
 const headers = (s.table.headers||[]).map((h: string) => `<th style="padding:9px 14px;text-align:left;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:0.08em;color:#fff;background:#1a1a1a;border-right:1px solid rgba(255,255,255,0.12);">${h.replace(/\*\*/g,'')}</th>`).join('');
 const rows = (s.table.rows||[]).slice(0,12).map((r: string[], ri: number) => `<tr style="background:${ri%2===0?'#fafaf8':'#fff'};">${r.map((c: string) => `<td style="padding:8px 14px;font-size:12px;border-bottom:1px solid #e5e5e0;line-height:1.5;">${formatCell(c)}</td>`).join('')}</tr>`).join('');
 content = `<div style="font-size:19px;font-weight:800;margin-bottom:20px;">${sTitle}</div>
 <div style="width:100%;height:2px;background:#1a1a1a;margin-bottom:20px;"></div>
 <table style="width:100%;border-collapse:collapse;border:2px solid #1a1a1a;"><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>${bullets}${actionLine}`;
 } else if (layout === 'recommendations') {
 const cards = (s.bullets||[]).map((b: string, bi: number) => `<div style="padding:16px;border:2px solid #e0e0d8;background:#fff;"><div style="font-size:10px;font-weight:900;color:#D24726;text-transform:uppercase;margin-bottom:6px;">PRIORITY ${bi+1}</div><div style="font-size:13px;color:#333;">${b}</div></div>`).join('');
 content = `<div style="font-size:19px;font-weight:800;margin-bottom:20px;">${sTitle}</div>
 <div style="width:100%;height:2px;background:#1a1a1a;margin-bottom:20px;"></div>
 <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">${cards}</div>${actionLine}`;
 } else if (layout === 'comparison' && s.comparison) {
 const left = (s.comparison.left?.items||[]).map((i: string) => `<div style="font-size:12px;color:#444;padding:4px 0;">${i}</div>`).join('');
 const right = (s.comparison.right?.items||[]).map((i: string) => `<div style="font-size:12px;color:#444;padding:4px 0;">${i}</div>`).join('');
 content = `<div style="font-size:19px;font-weight:800;margin-bottom:20px;">${sTitle}</div>
 <div style="width:100%;height:2px;background:#1a1a1a;margin-bottom:20px;"></div>
 <div style="display:flex;gap:16px;">
 <div style="flex:1;border:2px solid #e0e0d8;padding:16px;"><div style="font-size:13px;font-weight:900;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #D24726;">${s.comparison.left?.title||'A'}</div>${left}</div>
 <div style="display:flex;align-items:center;font-size:18px;color:#ccc;font-weight:900;">vs</div>
 <div style="flex:1;border:2px solid #e0e0d8;padding:16px;"><div style="font-size:13px;font-weight:900;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #00873c;">${s.comparison.right?.title||'B'}</div>${right}</div>
 </div>${actionLine}`;
 } else {
 content = `<div style="font-size:19px;font-weight:800;margin-bottom:20px;">${sTitle}</div>
 <div style="width:100%;height:2px;background:#1a1a1a;margin-bottom:20px;"></div>${bullets}${actionLine}`;
 }

 slidesHTML += `<div class="slide" style="page-break-after:always;padding:40px 50px;min-height:${layout==='cover'?'100vh':'auto'};">
 ${layout !== 'cover' ? `<div style="font-size: 11px;font-weight:700;text-transform:uppercase;letter-spacing:0.15em;color:#888;margin-bottom:16px;display:flex;justify-content:space-between;"><span>${topic}</span><span>${i+1} / ${slidesData.length}</span></div>` : ''}
 ${content}
 </div>`;
 }

 const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${title}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"><\/script>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{font-family:'Helvetica Neue',Arial,sans-serif;background:#fff;}
.slide{border-bottom:1px solid #eee;} @media print{.slide{page-break-after:always;border:none;} .no-print{display:none!important;}}</style>
</head><body>
<div class="no-print" style="position:fixed;top:0;left:0;right:0;background:#1a1a1a;color:#fff;padding:8px 20px;display:flex;justify-content:space-between;align-items:center;z-index:100;">
 <span style="font-size:11px;font-weight:700;">${title}</span>
 <button onclick="window.print()" style="font-size:10px;padding:4px 12px;background:none;border:1px solid #555;color:#fff;cursor:pointer;">PRINT / SAVE AS PDF</button>
</div>
<div style="padding-top:40px;">${slidesHTML}</div>
</body></html>`;

 const blob = new Blob([html], { type: 'text/html' });
 const url = URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url;
 a.download = `${agent}-slides.html`;
 a.click();
 URL.revokeObjectURL(url);
 }

 $effect(() => {
 const slide = slidesData[currentSlide];
 if (slide?.chart) {
 setTimeout(() => {
 const el = document.getElementById(`slide-chart-${currentSlide}`);
 if (!el) return;
 const existing = echarts.getInstanceByDom(el);
 if (existing) existing.dispose();
 const chart = echarts.init(el);
 const type = slide.chart.type === 'horizontal_bar' ? 'bar' : (slide.chart.type || 'bar');
 const isHorizontal = slide.chart.type === 'horizontal_bar';
 const option: any = {
 tooltip: { trigger: 'axis' },
 title: { text: slide.chart.title || '', left: 'center', textStyle: { fontSize: 12, fontWeight: 700 } },
 xAxis: isHorizontal
 ? { type: 'value', axisLabel: { fontSize: 10 } }
 : { type: 'category', data: slide.chart.labels || [], axisLabel: { fontSize: 10, rotate: (slide.chart.labels?.length || 0) > 5 ? 25 : 0 } },
 yAxis: isHorizontal
 ? { type: 'category', data: slide.chart.labels || [], axisLabel: { fontSize: 10 } }
 : { type: 'value', axisLabel: { fontSize: 10 } },
 series: [{ type: 'bar', data: slide.chart.values || [], itemStyle: { color: '#D24726', borderRadius: [4,4,0,0] }, smooth: type === 'line', areaStyle: type === 'line' ? { opacity: 0.08 } : undefined }],
 grid: { left: isHorizontal ? '25%' : '10%', right: '5%', bottom: '12%', top: '15%' }
 };
 if (type === 'line') {
 option.series[0].type = 'line';
 }
 if (type === 'pie') {
 delete option.xAxis; delete option.yAxis; delete option.grid;
 option.series = [{ type: 'pie', radius: ['35%','60%'], data: (slide.chart.labels||[]).map((l:string,i:number) => ({name:l, value:(slide.chart.values||[])[i]})), label: { fontSize: 10 }, itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 } }];
 option.tooltip = { trigger: 'item', formatter: '{b}: {c} ({d}%)' };
 }
 chart.setOption(option);
 }, 200);
 }
 });

 // Schedule analysis modal ( SCHEDULE button)
 let scheduleOpen = $state(false);
 let scheduleMsgId = $state<string | undefined>(undefined);
 let scheduleInitialName = $state('');
 let scheduleInitialDescription = $state('');
 let scheduleInitialSteps = $state<{ kind: 'sql' | 'agent'; sql?: string; agent?: string; prompt?: string }[]>([]);
 let scheduleToast = $state<{ wfId: number; slug: string } | null>(null);

 function extractStepsFromMessage(msg: any): { kind: 'sql' | 'agent'; sql?: string; agent?: string; prompt?: string }[] {
 const steps: { kind: 'sql' | 'agent'; sql?: string; agent?: string; prompt?: string }[] = [];
 const calls = Array.isArray(msg?.toolCalls) ? msg.toolCalls : [];
 const seen = new Set<string>();
 for (const t of calls) {
 const sql = (t && (t.sqlQuery || (t.args && (t.args.sql || t.args.query || t.args.statement)))) as string | undefined;
 if (sql && typeof sql === 'string') {
 const trimmed = sql.trim();
 if (trimmed && !seen.has(trimmed)) {
 seen.add(trimmed);
 steps.push({ kind: 'sql', sql: trimmed });
 }
 }
 }
 if (Array.isArray(msg?.sqlQueries)) {
 for (const sql of msg.sqlQueries) {
 if (typeof sql === 'string') {
 const trimmed = sql.trim();
 if (trimmed && !seen.has(trimmed)) {
 seen.add(trimmed);
 steps.push({ kind: 'sql', sql: trimmed });
 }
 }
 }
 }
 steps.push({ kind: 'agent', agent: 'Analyst', prompt: 'Summarize this analysis in 3 bullets and post as insight' });
 return steps;
 }

 function openScheduleModal(msgIndex: number) {
 const msg: any = messages[msgIndex];
 if (!msg) return;
 // Last user message before this assistant turn
 let lastUser = '';
 for (let k = msgIndex - 1; k >= 0; k--) {
 if (messages[k]?.role === 'user') { lastUser = messages[k].content || ''; break; }
 }
 scheduleInitialName = (lastUser || 'Scheduled analysis').slice(0, 80);
 scheduleInitialDescription = (msg.content || '').slice(0, 200);
 scheduleInitialSteps = extractStepsFromMessage(msg);
 scheduleMsgId = msg.id ? String(msg.id) : undefined;
 scheduleToast = null;
 scheduleOpen = true;
 }

 // Dashboard pin modal
 let showPinModal = $state(false);
 let pinModalData = $state<{msgIndex: number; tables: any[]; content: string} | null>(null);
 let userDashboards = $state<any[]>([]);
 let selectedDashId = $state<number | null>(null);
 let newDashNameForPin = $state('');
 let pinWidgetTitle = $state('');

 async function openPinModal(msgIndex: number, tables: any[], content: string) {
 pinModalData = { msgIndex, tables, content };
 pinWidgetTitle = tables?.[0]?.headers?.join(' / ') || content.slice(0, 50);
 showPinModal = true;
 // Load all dashboards for this project
 try {
 const res = await fetch(`/api/projects/${projectSlug}/dashboards`, { headers: _headers() });
 if (res.ok) { const d = await res.json(); userDashboards = d.dashboards || []; }
 } catch {}
 selectedDashId = userDashboards.length > 0 ? userDashboards[0].id : null;
 }

 async function confirmPin() {
 if (!pinModalData) return;
 const { msgIndex, tables, content } = pinModalData;
 let dashId = selectedDashId;

 // Create new dashboard if needed
 if (!dashId && newDashNameForPin.trim()) {
 try {
 const res = await fetch(`/api/projects/${projectSlug}/dashboards?name=${encodeURIComponent(newDashNameForPin.trim())}`, { method: 'POST', headers: _headers() });
 if (res.ok) { const d = await res.json(); dashId = d.id; }
 } catch {}
 }
 if (!dashId) return;

 // Build widget
 const hasTable = tables?.length > 0 && tables[0].headers?.length > 0;
 const widget = hasTable
 ? { type: 'chart', title: pinWidgetTitle, chartType: 'bar', headers: tables[0].headers, rows: tables[0].rows }
 : { type: 'text', title: pinWidgetTitle, content, full: true };

 await fetch(`/api/projects/${projectSlug}/dashboards/${dashId}/widgets`, {
 method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify(widget),
 });
 pinned = { ...pinned, [msgIndex]: true };
 activeDashboardId = dashId;
 dashboardPanelOpen = true;
 showPinModal = false;
 pinModalData = null;
 newDashNameForPin = '';
 }

 // Workflows
 let workflows = $state<{id: number; name: string; description?: string; steps: any[]}[]>([]);
 let workflowPickerOpen = $state(false);
 let inputFocused = $state(false);

 // Reasoning mode / analysis type / effort: removed from UI (use /deep or /quick slash
 // commands instead). Kept as empty constants because trace badges + sendMessage still
 // read them.
 let reasoningMode = $state<string>(typeof window !== 'undefined' ? (localStorage.getItem('reasoning_mode') || 'auto') : 'auto');
 $effect(() => { try { localStorage.setItem('reasoning_mode', reasoningMode); } catch {} });
 let modeMenuOpen = $state(false);
 const MODE_OPTIONS = [
   { id: 'auto',      label: 'AUTO',      desc: 'Router auto-picks tier from question' },
   { id: 'fast',      label: 'FAST',      desc: 'Quick tier — 1 KPI + 1 line · <500ms · $0.0001' },
   { id: 'standard',  label: 'STANDARD',  desc: 'Analytical card — KPI strip + 2 recs · ~2s · $0.005' },
   { id: 'deep',      label: 'DEEP',      desc: 'RCA + segments + benchmarks + scenarios + forecast · ~8-15s · $0.05' },
   { id: 'reasoning', label: 'REASONING', desc: 'Visible thinking chain · multi-step · ~10-20s · $0.08' },
   { id: 'ultra',     label: 'ULTRA',     desc: 'Deep + verification + counter-hypothesis · ~30-60s · $0.15' },
 ];
 const analysisType = '';
 const effort = '';

 // Model picker removed — backend router decides per question.
 // Empty-string constants kept for any downstream readers (sendMessage, trace badges).
 const modelPref = '';
 let modelMenuOpen = false;

 async function loadWorkflows() {
 try {
 const res = await fetch(`/api/workflows?project=${projectSlug}`, { headers: _headers() });
 if (res.ok) {
 const d = await res.json();
 workflows = d.workflows || [];
 }
 } catch {}
 }

 async function runWorkflow(wf: {id: number; name: string; steps: any[]}) {
 workflowPickerOpen = false;
 // Execute each step as a chat message
 const steps = wf.steps || [];
 if (steps.length === 0) {
 send(`Run the "${wf.name}" workflow`);
 return;
 }
 // Send first step, then queue the rest
 for (const step of steps) {
 const stepMsg = typeof step === 'string' ? step : (step.question || step.prompt || step.query || JSON.stringify(step));
 await send(stepMsg);
 // Wait for response to finish
 await new Promise<void>((resolve) => {
 const check = () => {
 if (!isStreaming) resolve();
 else setTimeout(check, 500);
 };
 setTimeout(check, 1000);
 });
 }
 }

 function stopStreaming() {
 if (abortController) {
 abortController.abort();
 abortController = null;
 isStreaming = false;
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 const finalizedTools = (last.toolCalls || []).map(t => t.status === 'running' ? { ...t, status: 'done' as const } : t);
 const finalizedTrace = (Array.isArray(last.trace) ? last.trace : []).map((t) => (t.kind === 'tool' && t.status === 'run') ? { ...t, status: 'done' as const } : t);
 messages = [...messages.slice(0, -1), { ...last, status: 'done', toolCalls: finalizedTools, trace: finalizedTrace, traceLive: false, traceDoneAt: Date.now(), timestamp: getTimestamp() }];
 }
 textareaEl?.focus();
 }
 }

 // parseClarify is imported from $lib/chat/tag-parsers (see CHAT_RENDERER.md §6).

 function parseAssumptions(content: string): string[] | null {
 const match = content.match(/\[ASSUMPTIONS:\s*(.+?)\]/s);
 if (match) return match[1].split(/[,;]|\n/).map(s => s.trim()).filter(s => s);
 return null;
 }

 async function pinToDashboard(msgIndex: number, tables: any[], content: string) {
 try {
 // Get or create dashboard
 const listRes = await fetch(`/api/projects/${projectSlug}/dashboards`, { headers: _headers() });
 let dashId: number;
 if (listRes.ok) {
 const data = await listRes.json();
 if (data.dashboards?.length > 0) {
 dashId = data.dashboards[0].id;
 } else {
 const createRes = await fetch(`/api/projects/${projectSlug}/dashboards?name=Dashboard`, { method: 'POST', headers: _headers() });
 const cd = await createRes.json();
 dashId = cd.id;
 }
 } else return;

 // Build widget
 const hasTable = tables && tables.length > 0 && tables[0].headers?.length > 0;
 const widget = hasTable
 ? { type: 'chart', title: tables[0].headers.join(' / '), chartType: 'bar', headers: tables[0].headers, rows: tables[0].rows }
 : { type: 'text', title: content.slice(0, 50) + '...', content, full: true };

 await fetch(`/api/projects/${projectSlug}/dashboards/${dashId}/widgets`, {
 method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify(widget),
 });
 pinned = { ...pinned, [msgIndex]: true };
 setTimeout(() => { pinned = { ...pinned, [msgIndex]: false }; }, 2000);
 } catch {}
 }
</script>

<svelte:window onclick={(e) => { const t = (e.target as HTMLElement); if (!t.closest('.mode-selector')) { workflowPickerOpen = false; modelMenuOpen = false; modeMenuOpen = false; } if (!t.closest('.dash-versions-actions')) { dashVersionsDropdownOpen = false; } }} />

<div class="flex h-full proj-page-body">
  <!-- Main chat -->
  <div class="flex flex-col" style="flex: 1 1 100%; width: 100%; min-width: 0; max-width: 100%; position: relative; padding-right: {splitMode ? 'min(48vw, 900px)' : '0'}; transition: padding-right 0.25s ease;">

  <!-- Dashboard panel toggle removed — chat-only product -->

  <!-- Drift bell -->
  {#if !dashPanelOpen}
  <div class="drift-bell-wrap">
    <button onclick={() => { driftDropdownOpen = !driftDropdownOpen; if (driftDropdownOpen) loadDriftEvents(); }}
            class="bell-btn" title="Data drift alerts">
      <Icon name="bell" size={14} />
      {#if driftCount > 0}
        <span class="bell-dot">{driftCount}</span>
      {/if}
    </button>

    {#if driftDropdownOpen}
      <div class="drift-dropdown">
        <div class="drift-hdr">DRIFT ALERTS · {driftEvents.length} OPEN</div>
        {#if driftEvents.length === 0}
          <div class="drift-empty">No active drift events.</div>
        {:else}
          {#each driftEvents as ev}
            <div class="drift-row sev-{ev.severity}">
              <div>
                <span class="sev-dot">●</span>
                <strong>{ev.severity}</strong> · {ev.drift_type}
              </div>
              <div style="font-size:11px; opacity:0.7;">
                {#if ev.table_name}{ev.table_name}{/if}{#if ev.column_name}.{ev.column_name}{/if}
              </div>
              <div style="font-size:10px; opacity:0.6;">
                {ev.ts}
                {#if ev.details?.pct_change}· {(ev.details.pct_change * 100).toFixed(0)}% change{/if}
                {#if ev.details?.action}· {ev.details.action}{/if}
                {#if ev.details?.days_stale}· {ev.details.days_stale} days stale{/if}
              </div>
              <div style="display:flex; gap:4px; margin-top:4px;">
                <button onclick={() => ackDrift(ev.id)} class="mini-btn">ACK</button>
                <button onclick={() => retrainFromDrift(ev.id)} class="mini-btn">RETRAIN</button>
                <button onclick={() => dismissDrift(ev.id)} class="mini-btn">DISMISS</button>
              </div>
            </div>
          {/each}
        {/if}
      </div>
    {/if}
  </div>
  {/if}

  <div class="flex-1 overflow-y-auto" bind:this={messagesEl} style="padding: 20px 20px 16px 20px; background: #ffffff;">
    <!-- Hybrid Claude-style: 880px reading column on white bg -->
    <div style="max-width: 880px; margin: 0 auto;">

      {#if messages.length > 0}
        <div class="flex justify-center mb-2 animate-fade-up">
          <div style="font-family: var(--pw-font-headline); font-size: 16px; font-weight: 500; color: var(--pw-ink);">
            {projectInfo?.agent_name || 'Agent'}
          </div>
        </div>
      {/if}
      {#if sessionStartTime}
        <div class="flex justify-center mb-6 animate-fade-up">
          <div class="tag-label">
            {projectInfo?.agent_name || 'Agent'} · {sessionStartTime}
          </div>
        </div>
      {/if}

      {#if messages.length === 0}
        <!-- Issue #20 — first-chat template prompt banner (hidden: single-agent product) -->
        {#if false}
          <div class="tpl-prompt-banner" style="max-width: 720px; margin: 24px auto 0; padding: 14px 18px; border: 1px solid rgba(201,99,66,0.30); background: rgba(201,99,66,0.06); border-radius: 0; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
            <div style="font-size: 12px; color: var(--pw-ink); flex: 1 1 280px;">
              <Icon name="lightbulb" size={14} /> <strong>Get domain-specific intelligence</strong> — apply a template
            </div>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
              {#each QUICK_TEMPLATES as t}
                <button
                  class="tpl-pick-btn"
                  style="padding: 5px 12px; font-size: 11px; border-radius: 0; border: 1px solid rgba(201,99,66,0.35); background: #fff; color: var(--pw-ink); cursor: pointer; text-transform: capitalize;"
                  disabled={!!applyingTemplate}
                  onclick={() => applyQuickTemplate(t)}
                >{applyingTemplate === t ? 'Applying…' : t}</button>
              {/each}
              <button
                style="padding: 5px 12px; font-size: 11px; border-radius: 0; border: 1px solid var(--pw-border, #ddd); background: #fff; color: var(--pw-ink); cursor: pointer;"
                onclick={browseAllTemplates}
              >Browse all →</button>
              <button
                style="padding: 5px 10px; font-size: 11px; border-radius: 0; border: 1px solid transparent; background: transparent; color: var(--pw-muted); cursor: pointer;"
                onclick={skipTemplatePrompt}
              >Skip</button>
            </div>
          </div>
        {/if}

        <div class="flex flex-col items-center animate-fade-up" style="padding-top: 60px;">
          <div style="text-align: center; margin-bottom: 40px;">
            <div class="pw-hero-title">{projectInfo?.agent_name || 'Agent'}</div>
            <div class="pw-hero-sub">
              {projectInfo?.agent_role || 'Ask me anything about your data'}
            </div>
          </div>
          <!-- Analytics stats -->
          <div style="display: flex; gap: 12px; justify-content: center; margin-bottom: 20px;">
            <div class="pw-stat-card">
              <div class="pw-stat-num">{sessionCount}</div>
              <div class="pw-stat-label">Sessions</div>
            </div>
            <div class="pw-stat-card">
              <div class="pw-stat-num">{avgScore > 0 ? avgScore.toFixed(1) : '--'}</div>
              <div class="pw-stat-label">Avg quality</div>
            </div>
            <div class="pw-stat-card">
              <div class="pw-stat-num">{memoryCount}</div>
              <div class="pw-stat-label">Memories</div>
            </div>
          </div>
          <!-- Design A: 4 starter cards in 2x2 grid, ChatGPT-style -->
          <div style="text-transform:uppercase; letter-spacing:0.08em; font-size:11px; font-weight:700; color:var(--pw-muted); margin-bottom:10px; text-align:center;">Try asking</div>
          <div class="starter-grid">
            {#each (dynamicSuggestions || []).slice(0, 4) as prompt, i}
              <button class="starter-card" onclick={() => send(prompt)} disabled={isStreaming}>
                <div class="starter-card-head">
                  <span class="starter-icon">{['','','',''][i] || ''}</span>
                  <span class="starter-label">{['ANALYZE','COMPARE','BREAKDOWN','DISCOVER'][i] || 'ASK'}</span>
                </div>
                <div class="starter-text">{prompt}</div>
              </button>
            {/each}
          </div>
        </div>
      {:else}
        <div class="flex flex-col gap-5">
          <ChatMessageList
            messages={messages}
            isStreaming={isStreaming}
            routeLabel={(messages[messages.length-1] as any)?.reasoningUsed || reasoningMode || "auto"}
            agentName={projectInfo?.agent_name || "Agent"}
            copiedIndex={copiedIndex}
            pinnedMap={pinned}
            chartsEnabled={featureConfig?.tools?.charts !== false}
            tabEnabled={(name) => tabEnabled(name)}
            updateMessage={(idx, patch) => { messages = [...messages.slice(0, idx), { ...messages[idx], ...patch }, ...messages.slice(idx + 1)]; }}
            onSend={(t) => send(t)}
            onCopy={(idx) => copyMessage(idx)}
            onCopySql={(sql) => copySql(sql)}
            onTrackPreference={(kind, value) => trackPreference(kind, value)}
            onAction={(act, arg) => handleAction(act, arg)}
            onSaveMemory={(idx) => { const fact = prompt("Save a fact the agent should remember:", ""); if (fact) fetch(`/api/projects/${projectSlug}/memories`, { method: "POST", headers: { ..._headers(), "Content-Type": "application/json" }, body: JSON.stringify({ fact, scope: "project" }) }); }}
            onExportCsv={(idx, tables) => { if (!tables[0]) return; const csv = tableToCsv(tables[0]); const blob = new Blob([csv], { type: "text/csv" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `dash-export-${Date.now()}.csv`; a.click(); URL.revokeObjectURL(url); }}
            onExportPdf={async (idx) => { const m = messages[idx]; if (!m) return; try { const res = await fetch("/api/export/pdf", { method: "POST", headers: { ..._headers(), "Content-Type": "application/json" }, body: JSON.stringify({ content: m.content, title: projectInfo?.agent_name || "Report" }) }); if (res.ok) { const blob = await res.blob(); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `dash-report-${Date.now()}.pdf`; a.click(); URL.revokeObjectURL(url); } } catch {} }}
            onPin={(idx, tables) => { const m = messages[idx]; if (!m) return; openPinModal(idx, tables, m.content); }}
            onSchedule={(idx) => openScheduleModal(idx)}
            onFeedback={async (idx, rating) => { const m = messages[idx]; if (!m) return; const q = idx > 0 ? messages[idx-1]?.content : ""; const firstSql = Array.isArray(m.sqlQueries) && m.sqlQueries.length ? m.sqlQueries[0] : ""; const fbRes = await fetch(`/api/projects/${projectSlug}/feedback`, { method: "POST", headers: { ..._headers(), "Content-Type": "application/json" }, body: JSON.stringify({ question: q, answer: m.content, rating, sql: firstSql }) }); if (rating === "up" && m.sqlQueries?.length) { for (const sql of m.sqlQueries) { await fetch(`/api/projects/${projectSlug}/save-query-pattern`, { method: "POST", headers: { ..._headers(), "Content-Type": "application/json" }, body: JSON.stringify({ question: q, sql }) }); } } try { const fbd = await fbRes.json(); if (fbd?.promoted?.total_goldens) { console.log(`[golden] promoted to corpus (total: ${fbd.promoted.total_goldens})`); } } catch {} }}
          >
            {#snippet analysisExtras({ msg, index: i })}
              {#if Array.isArray((msg as any).trace) && (msg as any).trace.length}
                <TraceTimeline steps={(msg as any).trace} usage={(msg as any).usage ?? null} routerDecision={(msg as any).routing ?? null} mode={(msg as any).reasoningUsed ?? ''} analysis={(msg as any).analysisUsed ?? ''} elapsedMs={((msg as any).traceDoneAt ?? Date.now()) - ((msg as any).traceStart ?? Date.now())} live={(msg as any).traceLive === true} />
              {/if}
              {#if msg.status === 'done' && i === messages.length - 1}
                <div style="display:flex; align-items:center; gap:8px; margin-top:6px;">
                  <button class="action-btn" onclick={shareConversation} disabled={shareBusy} title="Create a read-only public link to this conversation">🔗 {shareBusy ? 'Sharing…' : 'Share'}</button>
                  {#if shareLink}
                    <input readonly value={shareLink} onclick={(e) => (e.currentTarget as HTMLInputElement).select()} style="flex:1; font-size:11px; font-family:monospace; padding:4px 8px; border:1px solid var(--pw-border); border-radius: 0; background:var(--pw-bg-alt); color:var(--pw-ink);" />
                  {/if}
                </div>
              {/if}
              {@const proposalMatches = msg.content?.match(/\[CAMPAIGN_PROPOSAL:([^\]]+)\]/g) || []}
              {#if proposalMatches.length > 0}
                <div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px;">
                  {#each proposalMatches as proposal, pi}
                    {@const parts = proposal.replace("[CAMPAIGN_PROPOSAL:", "").replace("]", "").split("|").map((s) => s.trim())}
                    {@const cpName = parts[0] || "Auto Campaign"}
                    {@const cpSegment = parts[1] || "all"}
                    {@const cpDiscount = parseFloat(parts[2]) || 10}
                    {@const cpAudience = parseInt(parts[3]) || 0}
                    <div style="border: 2px solid #a06000; background: #fff8e8; padding: 12px; display: flex; align-items: center; gap: 14px;">
                      <div style="font-size: 16px;"><Icon name="megaphone" size={14} /></div>
                      <div style="flex: 1;">
                        <div style="font-size: 11px; font-weight: 900; letter-spacing: 0.06em; color: #b06e00; margin-bottom: 4px;">CAMPAIGN PROPOSAL</div>
                        <div style="font-size: 12px; font-weight: 700; color: var(--pw-ink);">{cpName}</div>
                        <div style="font-size: 10px; color: #555; margin-top: 3px;">
                          Target: <strong>{cpSegment}</strong> · Discount: <strong>{cpDiscount}%</strong>{cpAudience ? ` · Audience: ~${cpAudience}` : ""}
                        </div>
                      </div>
                      <button onclick={() => createCampaignFromProposal(cpName, cpSegment, cpDiscount, cpAudience, msg.id + "_" + pi)} disabled={proposalCreated.has(msg.id + "_" + pi)} style="padding: 6px 14px; background: {proposalCreated.has(msg.id + '_' + pi) ? 'var(--pw-bg-alt)' : 'var(--pw-accent)'}; color: {proposalCreated.has(msg.id + '_' + pi) ? 'var(--pw-muted)' : '#fff'}; border: 1px solid {proposalCreated.has(msg.id + '_' + pi) ? 'var(--pw-border)' : 'var(--pw-accent)'}; border-radius: var(--pw-radius-pill); cursor: {proposalCreated.has(msg.id + '_' + pi) ? 'default' : 'pointer'}; font-family: var(--pw-font-body); font-size: 11px; font-weight: 600;">
                        {proposalCreated.has(msg.id + "_" + pi) ? "CREATED" : "+ CREATE DRAFT"}
                      </button>
                    </div>
                  {/each}
                </div>
              {/if}

              {@const simMatches = msg.content?.match(/\[SIM:([a-zA-Z0-9_]+)\]/g) || []}
              {#each simMatches as simTag (simTag)}
                {@const simId = simTag.replace('[SIM:', '').replace(']', '')}
                <div class="sim-progress-card">
                  <span class="sim-pulse">●</span>
                  <div style="flex:1;">
                    <div style="font-size: 11px; font-weight:900; letter-spacing:0.06em; color:var(--pw-accent); margin-bottom:2px;">SIMULATION RUNNING</div>
                    <div style="font-size:11px; color:var(--pw-ink);">Simulating scenario · {simId}</div>
                  </div>
                  <a href="{base}/sim/process/{simId}" class="sim-link">View live →</a>
                </div>
              {/each}

              {@const simReportMatches = msg.content?.match(/\[SIM_REPORT:([a-zA-Z0-9_]+)\]/g) || []}
              {#each simReportMatches as simRTag (simRTag)}
                {@const simRId = simRTag.replace('[SIM_REPORT:', '').replace(']', '')}
                <div class="sim-report-card">
                  <div style="font-size:16px;"><Icon name="file-text" size={14} /></div>
                  <div style="flex:1;">
                    <div style="font-size: 11px; font-weight:900; letter-spacing:0.06em; color:#2e7d32; margin-bottom:2px;">SIMULATION COMPLETE</div>
                    <div style="font-size:11px; color:var(--pw-ink);">Report ready · {simRId}</div>
                  </div>
                  <a href="{base}/sim/process/{simRId}" class="sim-link sim-link-done">Open full →</a>
                </div>
              {/each}

              {@const simLaunchMatches = [...(msg.content?.matchAll(/\[SIM_LAUNCHED:([a-z0-9_]+)\]/gi) || [])]}
              {@const simLaunches = simLaunchMatches.map((m) => m[1])}
              {#each simLaunches as simId (simId)}
                <div class="sim-launch-card">
                  <div class="sim-launch-head">
                    <Icon name="zap" size={14} />
                    <span class="sim-launch-title">Simulation launched</span>
                    <code class="sim-launch-id">{simId}</code>
                  </div>
                  <div class="sim-launch-body">
                    30 grounded personas reacting over horizon. Live progress + report when done.
                  </div>
                  <button class="sim-launch-open" onclick={() => goto(`${base}/sim/process/${simId}`)}>
                    Open simulation →
                  </button>
                </div>
              {/each}

              {#each [...(msg.content?.matchAll(/\[METRIC:([a-zA-Z0-9_]+)\]/g) || [])] as mTag (mTag[1])}
                {#if mTag[1]}
                  <button
                    onclick={() => { metricDefPopoverName = mTag[1]; metricDefPopoverOpen = true; }}
                    style="
                      display:inline-flex; align-items:center; gap:4px;
                      margin-top:4px; margin-right:4px;
                      padding:3px 9px; border:1px solid #2e7d32;
                      background:#f1f8e9; color:#2e7d32;
                      font-family:monospace; font-size:10px; font-weight:700;
                      letter-spacing:0.05em; cursor:pointer;
                    "
                  >✓ {mTag[1]} (verified metric)</button>
                {/if}
              {/each}
            {/snippet}

            {#snippet messageFooter({ msg, index: i })}
              {#if msg.status === "done" && msg.autoSavedLearnings && msg.autoSavedLearnings.length > 0}
                <div class="learning-card" style="opacity: 0.7; border-color: var(--pw-accent);">
                  <div class="learning-card-header" style="color: var(--pw-accent);">Auto-saved {msg.autoSavedLearnings.length} {msg.autoSavedLearnings.length === 1 ? "learning" : "learnings"} to memory.</div>
                  <div class="learning-card-facts">
                    {#each (msg.autoSavedWithScores || msg.autoSavedLearnings.map((f) => ({fact: f, score: 90}))) as item}
                      <div class="learning-fact" style="opacity: 0.7; display: flex; align-items: center; gap: 8px;">
                        <span style="flex: 1;">{item.fact}</span>
                        <span style="font-size: 11px; font-weight: 900; padding: 1px 6px; background: var(--pw-accent); color: white; flex-shrink: 0;">{item.score}%</span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if msg.status === "done" && msg.proposedLearnings && msg.proposedLearnings.length > 0 && !msg.learningsSaved}
                <div class="learning-card">
                  <div class="learning-card-header">Approve {msg.proposedLearnings.length} {msg.proposedLearnings.length === 1 ? "learning" : "learnings"}?</div>
                  <div class="learning-card-facts">
                    {#each (msg.proposedLearningsWithScores || msg.proposedLearnings.map((f) => ({fact: f, score: 40}))) as item}
                      <div class="learning-fact" style="display: flex; align-items: center; gap: 8px;">
                        <span style="flex: 1;">{item.fact}</span>
                        <span style="font-size: 11px; font-weight: 900; padding: 1px 6px; background: #a06000; color: var(--pw-ink); flex-shrink: 0;">{item.score}%</span>
                      </div>
                    {/each}
                  </div>
                  <div class="flex gap-2 mt-2">
                    <button class="learning-approve-btn" onclick={async () => { await fetch(`/api/projects/${projectSlug}/approve-learnings`, { method: "POST", headers: { ..._headers(), "Content-Type": "application/json" }, body: JSON.stringify({ facts: msg.proposedLearnings, scope: "project" }) }); messages = [...messages.slice(0, i), { ...msg, learningsSaved: true }, ...messages.slice(i + 1)]; }}>SAVE TO MEMORY</button>
                    <button class="learning-dismiss-btn" onclick={() => { messages = [...messages.slice(0, i), { ...msg, proposedLearnings: [], learningsSaved: true }, ...messages.slice(i + 1)]; }}>DISMISS</button>
                  </div>
                </div>
              {/if}
              {#if msg.status === "done" && msg.learningsSaved && msg.proposedLearnings && msg.proposedLearnings.length > 0}
                <div class="learning-card" style="opacity: 0.7;">
                  <div class="learning-card-header" style="color: var(--pw-accent);">Saved to project memory.</div>
                </div>
              {/if}
              {#if msg.status === "done" && msg.suggestions && i === messages.length - 1}
                <div class="flex flex-wrap gap-2 mt-3">
                  {#each msg.suggestions as suggestion}
                    <button class="suggestion-btn" onclick={() => send(suggestion)} disabled={isStreaming}>{suggestion}</button>
                  {/each}
                </div>
              {/if}
              {#if msg.status === "done" && msg.role === "assistant"}
                {#if true}
                  {@const _mfSqls = Array.isArray((msg as any).sqlQueries) ? (msg as any).sqlQueries : []}
                  {@const _mfSql = _mfSqls[0] || ''}
                  <div style="margin-top: 6px;">
                    <button
                      onclick={() => {
                        let lastUserQ = '';
                        for (let k = i - 1; k >= 0; k--) {
                          if (messages[k]?.role === 'user') { lastUserQ = messages[k].content || ''; break; }
                        }
                        metricFixQuestion = lastUserQ;
                        metricFixSql = _mfSql;
                        metricFixOpen = true;
                      }}
                      style="
                        padding: 3px 10px; border: 1px solid var(--pw-border, #ccc);
                        background: none; cursor: pointer; font-family: monospace;
                        font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
                        color: var(--pw-ink, #1a1614);
                      "
                    >📌 Define/Fix metric</button>
                  </div>
                {/if}
              {/if}
            {/snippet}
          </ChatMessageList>
        </div>
      {/if}
    </div>
  </div>

  <!-- Insights banner removed: insights now surface via the inline Insights tab on each response -->

  <div class="input-bar shrink-0">
    <div style="padding: 12px 0;">
      <div class="composer-card" class:focused={inputFocused}>
        <div class="composer-row">
          <!-- Segment 1: Filter pills -->
          <div class="composer-seg composer-filters">
        <!-- Workflow picker -->
        <div class="mode-selector" style="position: relative;">
          <button class="cmp-chip" onclick={() => { if (workflows.length === 0) loadWorkflows(); modelMenuOpen = false; workflowPickerOpen = !workflowPickerOpen; }} disabled={isStreaming} title="Workflow">
            <span>⊞</span>
            <span>Flow</span>
            <span class="caret">▾</span>
          </button>
          {#if workflowPickerOpen}
            <div class="mode-dropdown" style="min-width: 280px;">
              <!-- Save current chat as workflow -->
              {#if canEdit && messages.filter(m => m.role === 'user').length >= 1}
                <button class="mode-dropdown-item" onclick={() => { workflowPickerOpen = false; openWorkflowSave(); }} style="border-bottom: 2px solid var(--pw-muted);">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#7c3aed" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
                  <div>
                    <div style="font-weight: 900; color: #7c3aed;">SAVE CURRENT AS WORKFLOW</div>
                    <span class="mode-dropdown-label">{messages.filter(m => m.role === 'user').length} steps from this chat</span>
                  </div>
                </button>
              {/if}
              {#if workflows.length === 0}
                <div style="padding: 14px; font-size: 11px; color: var(--pw-muted); text-align: center;">No workflows yet. Train your project to auto-generate workflows.</div>
              {:else}
                {#each workflows as wf}
                  <button class="mode-dropdown-item" onclick={() => runWorkflow(wf)}>
                    <div>
                      <div style="font-weight: 900;">{wf.name}</div>
                      {#if wf.description}
                        <span class="mode-dropdown-label">{wf.description}</span>
                      {/if}
                      <span class="mode-dropdown-label">{wf.steps?.length || 0} steps</span>
                    </div>
                  </button>
                {/each}
              {/if}
            </div>
          {/if}
        </div>

        <!-- Mode picker (AUTO / FAST / DEEP) -->
        <div class="mode-selector" style="position: relative; margin-left: 4px;">
          <button class="cmp-chip" onclick={() => { workflowPickerOpen = false; modeMenuOpen = !modeMenuOpen; }} disabled={isStreaming} title="Reasoning mode">
            <span style="opacity: 0.7;">◐</span>
            <span style="text-transform: uppercase; font-weight: 700; letter-spacing: 0.04em;">{(MODE_OPTIONS.find(m => m.id === reasoningMode) || MODE_OPTIONS[0]).label}</span>
            <span class="caret">▾</span>
          </button>
          {#if modeMenuOpen}
            <div class="mode-dropdown" style="min-width: 240px;">
              {#each MODE_OPTIONS as opt}
                <button class="mode-dropdown-item" onclick={() => { reasoningMode = opt.id; modeMenuOpen = false; }}>
                  <div>
                    <div style="font-weight: 900; color: {opt.id === reasoningMode ? 'var(--pw-accent)' : 'inherit'};">
                      {opt.id === reasoningMode ? '● ' : ''}{opt.label}
                    </div>
                    <span class="mode-dropdown-label">{opt.desc}</span>
                  </div>
                </button>
              {/each}
            </div>
          {/if}
        </div>

          </div>

          <!-- Segment 3: Input -->
          <textarea
            bind:this={textareaEl}
            bind:value={inputText}
            onkeydown={handleKeydown}
            oninput={autoResize}
            onfocus={() => inputFocused = true}
            onblur={() => inputFocused = false}
            placeholder="Ask anything…"
            rows="1"
            disabled={isStreaming}
            class="composer-input"
          ></textarea>

          <!-- Segment 4: Send -->
          {#if isStreaming}
            <button class="composer-send" onclick={stopStreaming} aria-label="Stop" title="Stop" style="background: var(--pw-error, #dc2626);">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
            </button>
          {:else}
            <button class="composer-send" onclick={() => send()} disabled={!inputText.trim()} aria-label="Send" title="Send">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          {/if}

          <!-- composer actions (Dashboard/Slides/Excel/Research) removed — chat-only product -->
        </div>
      </div>
      {#if !splitMode && dashVersions.length > 0}
        <div style="margin: 6px 0; padding: 6px 10px; background: var(--pw-bg-alt); border: 1px solid var(--pw-border); display: inline-flex; align-items: center; gap: 8px; font-size: 11px; max-width: 420px;">
          <span style="color: var(--pw-accent);">📊</span>
          <span>{dashVersions.length} dashboard{dashVersions.length === 1 ? '' : 's'} from this chat</span>
          <button onclick={() => { splitMode = true; if (dashVersions[0]) loadDashVersion(dashVersions[0].dashboard_id); }}
                  style="margin-left: auto; padding: 2px 8px; background: var(--pw-accent); color: #fff; border: none; cursor: pointer; font-family: inherit; font-size: 10px;">OPEN ▸</button>
        </div>
      {/if}
      {#if false && !splitMode && (chatDashBusy || chatDashProgress.complete || chatDashProgress.error)}
        <!-- Bottom pill only when split-pane is OFF (otherwise progress lives in the right pane) -->
        <div style="margin: 6px 0 6px 0; padding: 8px 10px; background: #fff8f0; border: 1px solid var(--pw-accent, #c96342); color: var(--pw-ink); font-family: inherit; font-size: 11px; animation: fadeIn 0.25s ease-out; position: relative; max-width: 420px; margin-right: auto;">
          <button onclick={dismissChatDashProgress} title="Dismiss" style="position: absolute; top: 2px; right: 4px; background: none; border: none; cursor: pointer; font-size: 12px; color: var(--pw-muted);">✕</button>
          {#if chatDashProgress.error}
            <div style="color: #c0392b; font-weight: 700;">⚠ {chatDashProgress.error}</div>
          {:else if chatDashProgress.complete}
            <div style="display: flex; align-items: center; gap: 6px;">
              <span style="font-weight: 700; color: var(--pw-accent, #c96342);">✓ Dashboard ready</span>
              <span style="color: var(--pw-muted);">— opened in panel →</span>
              <span>({chatDashProgress.panel_pills.length} panel{chatDashProgress.panel_pills.length === 1 ? '' : 's'})</span>
            </div>
          {:else}
            <div style="display: flex; align-items: center; gap: 6px;">
              <span>📋</span>
              <span style="font-weight: 600;">Building…</span>
              <span style="color: var(--pw-muted);">{chatDashProgress.stage ? `${chatDashProgress.current}/${chatDashProgress.total} · ${chatDashProgress.stage}` : `${chatDashProgress.current}/${chatDashProgress.total}`}</span>
              <span style="font-family: monospace; color: var(--pw-accent, #c96342); font-size: 10px;">{'█'.repeat(Math.max(0, chatDashProgress.current))}{'░'.repeat(Math.max(0, chatDashProgress.total - chatDashProgress.current))}</span>
            </div>
          {/if}
          {#if !chatDashProgress.complete && chatDashProgress.panel_pills.length > 0}
            <div style="margin-top: 4px; display: flex; flex-direction: column; gap: 1px;">
              {#each chatDashProgress.panel_pills.slice(-3) as pill, i}
                <div style="font-size: 10px; color: var(--pw-muted);">
                  <span style="color: var(--pw-accent, #c96342);">+</span> {pill.title}{pill.rows ? ` (${typeof pill.rows === 'number' ? pill.rows.toLocaleString() : pill.rows} rows)` : ''}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
      {#if simChips.length > 0}
        <div class="sim-chips" title="Auto-detected from your recent activity">
          <span class="sim-chips-lead"><Icon name="message-circle" size={14} /> Try:</span>
          {#each simChips as chip, ci}
            <button
              type="button"
              class="sim-chip"
              title="Auto-detected from your recent activity"
              onclick={() => applyChip(chip)}
            >{chip}</button>
            {#if ci < simChips.length - 1}<span class="sim-chip-sep">|</span>{/if}
          {/each}
        </div>
      {/if}
    </div>
  </div>
  {#if splitMode && (chatDashBusy || dashSpec || chatDashProgress.error || chatDashProgress.complete)}
    <!-- Split-pane right side: shows live BUILD progress + then the dashboard -->
    <div style="position: fixed; top: 56px; right: 0; bottom: 0; width: 48vw; max-width: 900px; min-width: 360px; z-index: 8500; border-left: 1px solid var(--pw-border, #e2ddd2); background: var(--pw-bg, #fdfaf5); display: flex; flex-direction: column; overflow: hidden; box-shadow: -4px 0 16px rgba(0,0,0,0.06);">
      <div style="display: flex; align-items: center; justify-content: space-between; padding: 8px 14px; background: var(--pw-bg-alt, #f7f6f3); border-bottom: 1px solid var(--pw-border, #e2ddd2);">
        <div style="font-size: 12px; font-weight: 700; color: var(--pw-accent, #c96342); text-transform: uppercase; letter-spacing: 0.05em;">
          📊 Dashboard {#if chatDashBusy && !dashSpec}· Building…{/if}
        </div>
        <div class="dash-versions-actions" style="display: flex; gap: 6px; align-items: center; position: relative;">
          {#if dashCurrentVersion}
            <button onclick={() => dashVersionsDropdownOpen = !dashVersionsDropdownOpen}
                    style="font-size: 11px; padding: 3px 8px; background: var(--pw-bg-alt); color: var(--pw-ink); border: 1px solid var(--pw-border); cursor: pointer; font-family: inherit;"
                    title="Version history">v{dashCurrentVersion} ▾</button>
            {#if dashVersionsDropdownOpen}
              <div style="position: absolute; top: 26px; right: 0; min-width: 240px; background: var(--pw-bg); border: 1px solid var(--pw-border); box-shadow: 0 4px 12px rgba(0,0,0,0.08); z-index: 10; max-height: 320px; overflow-y: auto;">
                {#each dashVersions as v}
                  <div style="display: flex; align-items: center; gap: 6px; border-bottom: 1px solid var(--pw-border);">
                    <button onclick={() => loadDashVersion(v.dashboard_id)}
                            style="flex: 1; text-align: left; padding: 8px 10px; background: {v.version === dashCurrentVersion ? 'rgba(201,99,66,0.08)' : 'transparent'}; border: none; cursor: pointer; font-family: inherit; font-size: 11px;">
                      <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="color: {v.version === dashCurrentVersion ? 'var(--pw-accent)' : 'var(--pw-muted)'};">{v.version === dashCurrentVersion ? '●' : '○'}</span>
                        <strong>v{v.version}</strong>
                        <span style="color: var(--pw-muted); margin-left: auto; font-size: 10px;">{new Date(v.created_at).toLocaleString()}</span>
                      </div>
                      {#if v.label}<div style="margin-top: 2px; color: var(--pw-muted); font-size: 10px; padding-left: 14px;">{v.label}</div>{/if}
                      <div style="font-size: 10px; color: var(--pw-muted); padding-left: 14px;">{v.n_panels || 0} panels</div>
                    </button>
                    <button onclick={(e) => { e.stopPropagation(); deleteDashVersion(v.dashboard_id, v.version); }}
                            disabled={dashVersions.length === 1}
                            title={dashVersions.length === 1 ? 'Last version, cannot delete' : `Delete v${v.version}`}
                            style="padding: 4px 8px; margin-right: 4px; background: transparent; color: {dashVersions.length === 1 ? 'var(--pw-muted)' : '#c0392b'}; border: none; cursor: {dashVersions.length === 1 ? 'not-allowed' : 'pointer'}; opacity: {dashVersions.length === 1 ? 0.45 : 1}; font-size: 12px;">✕</button>
                  </div>
                {/each}
              </div>
            {/if}
          {/if}
          <button onclick={() => buildDashFromChat(true)} disabled={chatDashBusy}
                  style="font-size: 11px; padding: 3px 8px; background: transparent; color: var(--pw-accent); border: 1px solid var(--pw-accent); cursor: {chatDashBusy ? 'not-allowed' : 'pointer'}; opacity: {chatDashBusy ? 0.45 : 1}; font-family: inherit;"
                  title="Rebuild as new version">↻ Rebuild</button>
          {#if dashSpec?.id}
            <button onclick={() => goto(`${base}/project/${encodeURIComponent(projectSlug)}/studio/${encodeURIComponent(dashSpec.id)}`)} style="font-size: 11px; padding: 3px 8px; background: transparent; color: var(--pw-accent, #c96342); border: 1px solid var(--pw-accent, #c96342); cursor: pointer; font-family: inherit;" title="Open in full Studio">↗ Studio</button>
          {/if}
          <button onclick={() => { splitMode = false; }} style="font-size: 11px; padding: 3px 8px; background: transparent; color: var(--pw-muted); border: 1px solid var(--pw-border, #e2ddd2); cursor: pointer; font-family: inherit;" title="Close split view (chat stays)">✕</button>
        </div>
      </div>

      {#if !dashSpec}
        <!-- BUILD PROGRESS view: detailed event log + stage checklist + panels + narrative -->
        <div style="flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 14px;">
          {#if chatDashProgress.error}
            <div style="padding: 10px 12px; background: #fdecea; border-left: 3px solid #c0392b; color: #c0392b; font-weight: 700; font-size: 13px;">⚠ {chatDashProgress.error}</div>
          {/if}

          <!-- Top meta row -->
          <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; font-size: 11px;">
            <div style="padding: 6px 8px; background: var(--pw-bg-alt, #f7f6f3); border: 1px solid var(--pw-border, #e2ddd2);">
              <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted); font-weight: 700;">Elapsed</div>
              <div style="font-family: monospace; font-size: 13px; color: var(--pw-ink);">{Math.floor(chatDashElapsed / 60)}m {chatDashElapsed % 60}s</div>
            </div>
            <div style="padding: 6px 8px; background: var(--pw-bg-alt, #f7f6f3); border: 1px solid var(--pw-border, #e2ddd2);">
              <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted); font-weight: 700;">Stage</div>
              <div style="font-family: monospace; font-size: 13px; color: var(--pw-accent, #c96342);">{chatDashProgress.current}/{chatDashProgress.total}</div>
            </div>
            <div style="padding: 6px 8px; background: var(--pw-bg-alt, #f7f6f3); border: 1px solid var(--pw-border, #e2ddd2);">
              <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted); font-weight: 700;">Panels</div>
              <div style="font-family: monospace; font-size: 13px; color: var(--pw-ink);">{chatDashProgress.panel_pills.length}</div>
            </div>
            <div style="padding: 6px 8px; background: var(--pw-bg-alt, #f7f6f3); border: 1px solid var(--pw-border, #e2ddd2);">
              <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted); font-weight: 700;">Tokens</div>
              <div style="font-family: monospace; font-size: 13px; color: var(--pw-ink);">{(chatDashProgress.tokens || 0).toLocaleString()}</div>
            </div>
          </div>

          <!-- Progress bar -->
          <div>
            <div style="display: flex; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 4px;">
              <span style="font-weight: 700; color: var(--pw-accent, #c96342);">📋 {chatDashProgress.stage || 'starting…'}</span>
              {#if DASH_STAGE_DEFS[chatDashProgress.stage]}
                <span style="color: var(--pw-muted); font-size: 11px;">— {DASH_STAGE_DEFS[chatDashProgress.stage].desc}</span>
              {/if}
            </div>
            <div style="font-family: monospace; color: var(--pw-accent, #c96342); font-size: 13px; letter-spacing: -1px;">{'█'.repeat(Math.max(0, chatDashProgress.current))}{'░'.repeat(Math.max(0, chatDashProgress.total - chatDashProgress.current))}</div>
          </div>

          <!-- Stage checklist -->
          <div>
            <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted); font-weight: 700; margin-bottom: 6px;">Pipeline</div>
            <div style="display: flex; flex-direction: column; gap: 2px; font-size: 11px;">
              {#each DASH_STAGE_ORDER as sg}
                {@const _done = chatDashProgress.stages_done.find((s: any) => s.stage === sg)}
                {@const _current = chatDashProgress.stage === sg && !_done}
                {@const _glyph = _done ? '✓' : _current ? '●' : '○'}
                {@const _color = _done ? '#16a34a' : _current ? 'var(--pw-accent, #c96342)' : 'var(--pw-muted)'}
                <div style="display: flex; align-items: center; gap: 8px; padding: 3px 6px; background: {_current ? 'rgba(201,99,66,0.06)' : 'transparent'}; border-left: 2px solid {_current ? 'var(--pw-accent, #c96342)' : 'transparent'};">
                  <span style="font-family: monospace; color: {_color}; font-weight: 700; width: 12px;">{_glyph}</span>
                  <span style="color: {_done || _current ? 'var(--pw-ink)' : 'var(--pw-muted)'}; font-weight: {_current ? 700 : 400};">{(DASH_STAGE_DEFS[sg] && DASH_STAGE_DEFS[sg].label) || sg}</span>
                  {#if _done && _done.took_ms != null}<span style="color: var(--pw-muted); margin-left: auto; font-family: monospace; font-size: 10px;">{_done.took_ms}ms</span>{/if}
                </div>
              {/each}
            </div>
          </div>

          <!-- Per-stage detail badges -->
          {#if chatDashProgress.intent || chatDashProgress.schema_tables || chatDashProgress.sql_count}
            <div style="display: flex; flex-wrap: wrap; gap: 6px; font-size: 10px;">
              {#if chatDashProgress.intent}
                <span style="padding: 3px 7px; background: var(--pw-bg-alt, #f7f6f3); border: 1px solid var(--pw-border); font-family: monospace;">intent: {typeof chatDashProgress.intent === 'string' ? chatDashProgress.intent : (chatDashProgress.intent.intent || chatDashProgress.intent.classification || JSON.stringify(chatDashProgress.intent).slice(0, 50))}</span>
              {/if}
              {#if chatDashProgress.schema_tables}
                <span style="padding: 3px 7px; background: var(--pw-bg-alt, #f7f6f3); border: 1px solid var(--pw-border); font-family: monospace;">schema: {chatDashProgress.schema_tables} tables</span>
              {/if}
              {#if chatDashProgress.sql_count}
                <span style="padding: 3px 7px; background: var(--pw-bg-alt, #f7f6f3); border: 1px solid var(--pw-border); font-family: monospace;">SQL: {chatDashProgress.sql_count} queries</span>
              {/if}
            </div>
          {/if}

          <!-- Panel cards -->
          {#if chatDashProgress.panel_pills.length > 0}
            <div>
              <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted); font-weight: 700; margin-bottom: 6px;">Panels · {chatDashProgress.panel_pills.length}</div>
              <div style="display: flex; flex-direction: column; gap: 4px;">
                {#each chatDashProgress.panel_pills as pill, i}
                  <div style="display: flex; align-items: center; gap: 8px; padding: 6px 10px; background: var(--pw-bg-alt, #f7f6f3); border: 1px solid var(--pw-border, #e2ddd2); font-size: 12px; animation: fadeIn 0.25s ease-out;">
                    <span style="color: var(--pw-accent, #c96342); font-weight: 700;">{i + 1}</span>
                    <span style="flex: 1; color: var(--pw-ink);">{pill.title}</span>
                    {#if pill.chart_type}<span style="padding: 1px 5px; background: var(--pw-bg); border: 1px solid var(--pw-border); font-family: monospace; font-size: 10px; color: var(--pw-muted);">{pill.chart_type}</span>{/if}
                    {#if pill.rows}<span style="color: var(--pw-muted); font-family: monospace; font-size: 10px;">{typeof pill.rows === 'number' ? pill.rows.toLocaleString() : pill.rows} rows</span>{/if}
                    {#if pill.cols}<span style="color: var(--pw-muted); font-family: monospace; font-size: 10px;">· {pill.cols} cols</span>{/if}
                  </div>
                {/each}
              </div>
            </div>
          {/if}

          <!-- Narrative -->
          {#if chatDashProgress.narrative}
            <div style="padding: 10px 12px; background: var(--pw-bg-alt, #f7f6f3); border-left: 3px solid var(--pw-accent, #c96342); font-family: 'Source Serif Pro', Georgia, serif; font-size: 13px; line-height: 1.5; color: var(--pw-ink, #2c2a26); font-style: italic;">
              {chatDashProgress.narrative}
            </div>
          {/if}

          <!-- Live event log (CLI-style) -->
          {#if chatDashProgress.events_log.length > 0}
            <div>
              <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted); font-weight: 700; margin-bottom: 4px;">Event log · {chatDashProgress.events_log.length}</div>
              <div style="background: #1a1614; color: #e8e3d6; padding: 8px 10px; font-family: monospace; font-size: 10px; line-height: 1.55; max-height: 220px; overflow-y: auto; border: 1px solid #2c2a26;">
                {#each chatDashProgress.events_log.slice(-40) as ev}
                  {@const _mm = String(Math.floor(ev.at / 60)).padStart(2, '0')}
                  {@const _ss = String(ev.at % 60).padStart(2, '0')}
                  {@const _color = ev.type === 'stage_done' ? '#00fc40' : ev.type === 'panel' ? '#ffb84d' : ev.type === 'narrative' ? '#9b6dff' : ev.type === 'done' ? '#00fc40' : '#e8e3d6'}
                  <div style="white-space: pre-wrap;"><span style="color: #888;">{_mm}:{_ss}</span> <span style="color: {_color};">{ev.msg}</span></div>
                {/each}
              </div>
            </div>
          {/if}
        </div>
      {:else if dashSpec}
        <!-- READY view: rendered dashboard -->
        {#if dashSpec.narrative}
          <div style="padding: 10px 16px; background: var(--pw-bg, #fdfaf5); border-bottom: 1px solid var(--pw-border, #e2ddd2);">
            <div style="font-family: 'Source Serif Pro', Georgia, serif; font-size: 14px; line-height: 1.5; color: var(--pw-ink, #2c2a26);">
              {typeof dashSpec.narrative === 'string' ? dashSpec.narrative : (dashSpec.narrative?.text || '')}
            </div>
          </div>
        {/if}
        <div style="flex: 1; overflow-y: auto; padding: 12px;">
          <DashRenderer spec={dashSpec} data={dashData} />
        </div>
      {/if}
    </div>
  {/if}
</div>
</div>

<!-- Save Workflow Modal -->
{#if showWorkflowSaveModal}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div style="position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 200; display: flex; align-items: center; justify-content: center;" onclick={(e) => { if (e.target === e.currentTarget) showWorkflowSaveModal = false; }}>
  <div style="background: var(--pw-bg); border: 3px solid var(--pw-ink); width: 450px; max-height: 80vh; overflow-y: auto; box-shadow: 6px 6px 0 rgba(0,0,0,0.3);">
    <div style="padding: 10px 16px; background: #7c3aed; color: #fff; font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; display: flex; justify-content: space-between; align-items: center;">
      <span>SAVE AS WORKFLOW</span>
      <button onclick={() => showWorkflowSaveModal = false} style="background: none; border: none; color: #fff; cursor: pointer; font-size: 13px;"><Icon name="x" size={14} /></button>
    </div>
    <div style="padding: 16px;">
      <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted); margin-bottom: 4px;">WORKFLOW NAME</div>
      <input type="text" bind:value={wfSaveName} placeholder="e.g. Monthly Revenue Deep Dive" style="width: 100%; padding: 8px 12px; border: 2px solid var(--pw-ink); font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-bg-alt); margin-bottom: 12px;" />

      <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted); margin-bottom: 4px;">DESCRIPTION</div>
      <input type="text" bind:value={wfSaveDesc} placeholder="What this workflow analyzes..." style="width: 100%; padding: 8px 12px; border: 2px solid var(--pw-ink); font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg-alt); margin-bottom: 12px;" />

      <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted); margin-bottom: 6px;">STEPS (uncheck to exclude)</div>
      <div style="display: flex; flex-direction: column; gap: 4px; margin-bottom: 16px;">
        {#each wfSaveSteps as step, si}
          <label style="display: flex; align-items: flex-start; gap: 8px; padding: 6px 8px; background: {step.checked ? '#f3e8ff' : 'var(--pw-bg-alt)'}; border: 1px solid {step.checked ? '#7c3aed' : 'var(--pw-muted)'}; cursor: pointer;">
            <input type="checkbox" bind:checked={step.checked} style="margin-top: 2px;" />
            <div>
              <div style="font-size: 11px; font-weight: 900; color: #7c3aed;">STEP {si + 1}</div>
              <div style="font-size: 11px; color: var(--pw-ink);">{step.question}</div>
            </div>
          </label>
        {/each}
      </div>

      <div style="display: flex; gap: 8px; justify-content: flex-end;">
        <button onclick={() => showWorkflowSaveModal = false} style="padding: 8px 16px; font-size: 11px; font-weight: 900; border: 2px solid var(--pw-ink); background: none; cursor: pointer; font-family: var(--pw-font-body); text-transform: uppercase;">CANCEL</button>
        <button onclick={confirmWorkflowSave} disabled={!wfSaveName.trim() || wfSaveSteps.filter(s => s.checked).length === 0} style="padding: 8px 16px; font-size: 11px; font-weight: 900; border: 2px solid #7c3aed; background: #7c3aed; color: #fff; cursor: pointer; font-family: var(--pw-font-body); text-transform: uppercase;">SAVE WORKFLOW</button>
      </div>
    </div>
  </div>
</div>
{/if}

<!-- Save Presentation Modal -->
{#if showSaveModal}
<div style="position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 200; display: flex; align-items: center; justify-content: center;" onclick={(e) => { if (e.target === e.currentTarget) showSaveModal = false; }}>
  <div style="background: var(--pw-bg); border: 3px solid var(--pw-ink); width: 400px; box-shadow: 6px 6px 0 rgba(0,0,0,0.3);">
    <div style="padding: 10px 16px; background: #1a1a1a; color: #fff; font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; display: flex; justify-content: space-between; align-items: center;">
      <span>SAVE PRESENTATION</span>
      <button onclick={() => showSaveModal = false} style="background: none; border: none; color: #fff; cursor: pointer; font-size: 13px;"><Icon name="x" size={14} /></button>
    </div>
    <div style="padding: 20px;">
      <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted); margin-bottom: 6px;">PRESENTATION NAME</div>
      <input type="text" bind:value={saveTitle} style="width: 100%; padding: 8px 12px; border: 2px solid var(--pw-ink); font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-bg-alt);" />
      <div style="font-size: 10px; color: var(--pw-muted); margin-top: 6px;">Same name = new version (v1, v2, v3...)</div>
      <div style="display: flex; gap: 8px; margin-top: 16px; justify-content: flex-end;">
        <button onclick={() => showSaveModal = false} style="padding: 8px 16px; font-size: 11px; font-weight: 900; border: 2px solid var(--pw-ink); background: none; cursor: pointer; font-family: var(--pw-font-body); text-transform: uppercase;">CANCEL</button>
        <button onclick={confirmSave} style="padding: 8px 16px; font-size: 11px; font-weight: 900; border: 2px solid var(--pw-ink); background: var(--pw-ink); color: var(--pw-bg); cursor: pointer; font-family: var(--pw-font-body); text-transform: uppercase;">SAVE</button>
      </div>
    </div>
  </div>
</div>
{/if}

  <!-- Slide Panel -->
{#if slidesPanelOpen}
<div style="position: fixed; top: 0; right: 0; width: 55%; height: 100vh; background: #fff; border-left: 3px solid #1a1a1a; z-index: 90; display: flex; flex-direction: column; box-shadow: -4px 0 30px rgba(0,0,0,0.15);">
  <!-- Header -->
  <div style="padding: 8px 16px; background: #1a1a1a; color: #fff; display: flex; align-items: center; justify-content: space-between;">
    <div style="display: flex; align-items: center; gap: 8px;">
      <svg width="18" height="18" viewBox="0 0 24 24"><defs><linearGradient id="pg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#D24726"/><stop offset="100%" stop-color="#F0A030"/></linearGradient></defs><rect x="2" y="2" width="20" height="20" rx="3" fill="url(#pg)"/><text x="12" y="16" text-anchor="middle" fill="#fff" font-size="12" font-weight="900" font-family="Arial">P</text></svg>
      <span style="font-size: 11px; font-weight: 700; letter-spacing: 0.1em;">PRESENTATION</span>
      {#if slidesData.length > 0}
        <span style="font-size: 10px; opacity: 0.6;">{currentSlide + 1} / {slidesData.length}</span>
      {/if}
    </div>
    <div style="display: flex; gap: 8px; align-items: center;">
      <button onclick={savePresentation} disabled={slidesData.length === 0} style="font-size: 11px; padding: 3px 8px; background: none; border: 1px solid #555; color: #fff; cursor: pointer; text-transform: uppercase;">SAVE</button>
      <button onclick={downloadHTML} disabled={slidesData.length === 0} style="font-size: 11px; padding: 3px 8px; background: none; border: 1px solid #555; color: #fff; cursor: pointer; text-transform: uppercase;">HTML</button>
      <button onclick={downloadPptx} disabled={slidesData.length === 0} style="font-size: 11px; padding: 3px 8px; background: none; border: 1px solid #555; color: #fff; cursor: pointer; text-transform: uppercase;">PPTX</button>
      <button onclick={() => { downloadHTML(); }} style="font-size: 11px; padding: 3px 8px; background: none; border: 1px solid #555; color: #fff; cursor: pointer; text-transform: uppercase;">PDF</button>
      <button onclick={() => slidesPanelOpen = false} style="background: none; border: none; cursor: pointer; color: #fff; font-size: 14px;"><Icon name="x" size={14} /></button>
    </div>
  </div>

  <!-- Loading / PPTX Generation Steps -->
  {#if slidesLoading || pptxGenerating}
    <div style="flex: 1; display: flex; flex-direction: column; background: #1a1a1a; color: #e0e0e0; padding: 24px; font-family: 'SF Mono', 'Menlo', monospace;">
      <!-- Header -->
      <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 20px;">
        <svg width="24" height="24" viewBox="0 0 24 24"><defs><linearGradient id="pg2" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#D24726"/><stop offset="100%" stop-color="#F0A030"/></linearGradient></defs><rect x="2" y="2" width="20" height="20" rx="3" fill="url(#pg2)"/><text x="12" y="16" text-anchor="middle" fill="#fff" font-size="12" font-weight="900" font-family="Arial">P</text></svg>
        <span style="font-size: 12px; font-weight: 700; color: #fff; text-transform: uppercase; letter-spacing: 0.1em;">
          {pptxGenerating ? (pptxSteps.every(s => s.status === 'done') ? 'PRESENTATION READY' : 'GENERATING PRESENTATION') : 'CREATING SLIDES'}
        </span>
      </div>

      <!-- Progress bar -->
      {#if pptxSteps.length > 0}
        {@const doneCount = pptxSteps.filter(s => s.status === 'done').length}
        {@const pct = Math.round((doneCount / pptxSteps.length) * 100)}
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 20px;">
          <div style="flex: 1; height: 4px; background: #333;">
            <div style="height: 100%; background: linear-gradient(90deg, #D24726, #F0A030); width: {pct}%; transition: width 0.5s;"></div>
          </div>
          <span style="font-size: 11px; font-weight: 700; color: #D24726;">{pct}%</span>
        </div>

        <!-- Steps -->
        <div style="flex: 1;">
          {#each pptxSteps as step}
            <div style="display: flex; align-items: center; gap: 8px; padding: 5px 0; font-size: 11px;">
              {#if step.status === 'done'}
                <span style="color: var(--pw-accent);"><Icon name="check" size={14} /></span>
              {:else if step.status === 'active'}
                <span style="color: #F0A030;">●</span>
              {:else if step.status === 'error'}
                <span style="color: #ff4444;"><Icon name="x" size={14} /></span>
              {:else}
                <span style="color: #555;">○</span>
              {/if}
              <span style="color: {step.status === 'done' ? 'var(--pw-accent)' : step.status === 'active' ? '#fff' : step.status === 'error' ? '#ff4444' : '#555'};">{step.label}</span>
            </div>
          {/each}
        </div>

        <!-- After complete -->
        {#if pptxSteps.every(s => s.status === 'done') && pptxSavedVersion > 0}
          <div style="margin-top: 16px; padding: 10px 14px; border: 1px solid #333; background: #222; font-size: 11px; color: #aaa;">
            Saved to <span style="color: #D24726; font-weight: 700;">PRESENTATIONS</span> tab as v{pptxSavedVersion}
          </div>
          <div style="display: flex; gap: 8px; margin-top: 12px;">
            <button onclick={() => { pptxSteps = []; currentSlide = 0; }} style="flex: 1; padding: 8px; font-size: 11px; font-weight: 900; background: #D24726; color: #fff; border: none; cursor: pointer; text-transform: uppercase;">OPEN SLIDES</button>
            <button onclick={() => { slidesPanelOpen = false; pptxSteps = []; }} style="flex: 1; padding: 8px; font-size: 11px; font-weight: 900; background: none; border: 1px solid #555; color: #aaa; cursor: pointer; text-transform: uppercase;">CLOSE</button>
          </div>
        {/if}

      {:else}
        <!-- Simple loading for generateSlides -->
        <div style="flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px;">
          <div style="font-size: 12px; color: #aaa;">{slidesProgress}</div>
          <div style="width: 200px; height: 3px; background: #333;">
            <div style="height: 100%; background: linear-gradient(90deg, #D24726, #F0A030); width: {slidesProgress.includes('Analyz') ? '30' : slidesProgress.includes('Structur') ? '60' : slidesProgress.includes('Done') ? '100' : '15'}%; transition: width 0.8s;"></div>
          </div>
        </div>
      {/if}
    </div>

  {:else if slidesData.length > 0}
    <!-- Narrative bar -->
    {#if slidesThinking}
      <div style="padding: 6px 16px; background: #f5f5f0; border-bottom: 1px solid #e0e0d8; font-size: 10px; color: #555; line-height: 1.4;">
        <strong style="color: #1a1a1a;">NARRATIVE:</strong> {slidesThinking.narrative || ''}
        {#if slidesThinking.key_insight} &middot; <strong style="color: #D24726;">KEY INSIGHT:</strong> {slidesThinking.key_insight}{/if}
      </div>
    {/if}

    <!-- Slide content -->
    <div style="flex: 1; overflow-y: auto; background: #fafaf8;" class="slide-render-area">
      {#if slidesData[currentSlide]}
        {@const slide = slidesData[currentSlide]}

        <!-- Topic bar -->
        <div style="padding: 6px 24px; background: #f0f0eb; border-bottom: 1px solid #ddd; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.15em; color: #888; display: flex; justify-content: space-between;">
          <span>{slide.topic || 'ANALYSIS'}</span>
          <span>{currentSlide + 1} / {slidesData.length}</span>
        </div>

        <div style="padding: 24px 28px;">

          {#if slide.layout === 'cover'}
            <!-- COVER SLIDE -->
            <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 380px; text-align: center;">
              <div style="font-size: 28px; font-weight: 900; color: #1a1a1a; line-height: 1.2; max-width: 500px;">{slide.title}</div>
              <div style="width: 60px; height: 3px; background: linear-gradient(90deg, #D24726, #F0A030); margin: 20px auto;"></div>
              {#if slide.bullets?.length}
                <div style="font-size: 14px; color: #666; margin-top: 8px;">{slide.bullets[0]}</div>
              {/if}
              <div style="font-size: 11px; color: #999; margin-top: 30px; text-transform: uppercase; letter-spacing: 0.1em;">{projectInfo?.agent_name || 'Agent'} &middot; {new Date().toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'})}</div>
              <div style="font-size: 11px; color: #bbb; margin-top: 8px; letter-spacing: 0.15em;">POWERED BY RLAI DASH</div>
            </div>

          {:else if slide.layout === 'kpi' && slide.kpis?.length}
            <!-- KPI SLIDE -->
            <div style="font-size: 16px; font-weight: 800; color: #1a1a1a; line-height: 1.3; margin-bottom: 20px;">{slide.title}</div>
            <div style="width: 100%; height: 2px; background: #1a1a1a; margin-bottom: 24px;"></div>
            <div style="display: flex; gap: 16px; justify-content: center; margin: 20px 0;">
              {#each slide.kpis as kpi}
                <div style="flex: 1; text-align: center; padding: 20px; border: 2px solid #e0e0d8;">
                  <div style="font-size: 30px; font-weight: 900; color: #1a1a1a;">{kpi.value}</div>
                  <div style="font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 6px;">{kpi.label}</div>
                  {#if kpi.change}
                    <div style="font-size: 13px; font-weight: 700; color: {kpi.change?.startsWith('+') || kpi.change?.startsWith('▲') ? '#00873c' : '#d32f2f'}; margin-top: 4px;">{kpi.change}</div>
                  {/if}
                </div>
              {/each}
            </div>
            {#each (slide.bullets || []) as b, bi}
              <div style="font-size: 12px; color: #444; padding: 6px 0; border-bottom: 1px solid #eee;">{bi + 1}. {b}</div>
            {/each}
            {#if slide.action_line}
              <div style="margin-top: 20px; padding: 10px 14px; border-top: 2px solid #1a1a1a; font-size: 11px; font-weight: 700; color: #1a1a1a;">{slide.action_line}</div>
            {/if}

          {:else if slide.layout === 'exhibit' || slide.chart}
            <!-- EXHIBIT SLIDE (chart + takeaways) -->
            <div style="font-size: 16px; font-weight: 800; color: #1a1a1a; line-height: 1.3; margin-bottom: 20px;">{slide.title}</div>
            <div style="width: 100%; height: 2px; background: #1a1a1a; margin-bottom: 20px;"></div>
            <div style="display: flex; gap: 24px;">
              <div style="flex: 3;">
                {#if slide.chart}
                  <div style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: #888; margin-bottom: 8px;">{slide.chart.title || 'EXHIBIT'}</div>
                  <div id="slide-chart-{currentSlide}" style="width: 100%; height: 260px;"></div>
                  <div style="font-size: 11px; color: #aaa; margin-top: 4px;">Source: Project data</div>
                {/if}
              </div>
              <div style="flex: 2; border-left: 2px solid #e0e0d8; padding-left: 16px;">
                <div style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: #888; margin-bottom: 12px;">KEY TAKEAWAYS</div>
                {#each (slide.bullets || []) as b, bi}
                  <div style="font-size: 11px; color: #333; margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #eee;">
                    <span style="font-weight: 800; color: #D24726;">{bi + 1}.</span> {b}
                  </div>
                {/each}
              </div>
            </div>
            {#if slide.action_line}
              <div style="margin-top: 16px; padding: 10px 14px; border-top: 2px solid #1a1a1a; font-size: 11px; font-weight: 700; color: #1a1a1a;">{slide.action_line}</div>
            {/if}

          {:else if slide.layout === 'data' || slide.table}
            <!-- DATA SLIDE (table) -->
            <div style="font-size: 16px; font-weight: 800; color: #1a1a1a; line-height: 1.3; margin-bottom: 20px;">{slide.title}</div>
            <div style="width: 100%; height: 2px; background: #1a1a1a; margin-bottom: 20px;"></div>
            {#if slide.table}
              <table style="width: 100%; border-collapse: collapse;">
                <thead>
                  <tr style="border-bottom: 2px solid #1a1a1a;">
                    {#each (slide.table.headers || []) as h}
                      <th style="padding: 8px 12px; text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: #666; font-weight: 700;">{h}</th>
                    {/each}
                  </tr>
                </thead>
                <tbody>
                  {#each (slide.table.rows || []).slice(0, 10) as row, ri}
                    <tr style="border-bottom: 1px solid #eee; background: {ri % 2 === 0 ? '#fafaf8' : '#fff'};">
                      {#each row as cell}
                        <td style="padding: 7px 12px; font-size: 11px; color: #333;">{cell}</td>
                      {/each}
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
            {#each (slide.bullets || []) as b}
              <div style="font-size: 11px; color: #555; margin-top: 6px;">· {b}</div>
            {/each}
            {#if slide.action_line}
              <div style="margin-top: 16px; padding: 10px 14px; border-top: 2px solid #1a1a1a; font-size: 11px; font-weight: 700; color: #1a1a1a;">{slide.action_line}</div>
            {/if}

          {:else if slide.layout === 'comparison' && slide.comparison}
            <!-- COMPARISON SLIDE -->
            <div style="font-size: 16px; font-weight: 800; color: #1a1a1a; line-height: 1.3; margin-bottom: 20px;">{slide.title}</div>
            <div style="width: 100%; height: 2px; background: #1a1a1a; margin-bottom: 20px;"></div>
            <div style="display: flex; gap: 16px;">
              <div style="flex: 1; border: 2px solid #e0e0d8; padding: 16px;">
                <div style="font-size: 13px; font-weight: 900; color: #1a1a1a; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 2px solid #D24726;">{slide.comparison.left?.title || 'A'}</div>
                {#each (slide.comparison.left?.items || []) as item}
                  <div style="font-size: 11px; color: #444; padding: 4px 0;">{item}</div>
                {/each}
              </div>
              <div style="display: flex; align-items: center; font-size: 18px; color: #ccc; font-weight: 900;">vs</div>
              <div style="flex: 1; border: 2px solid #e0e0d8; padding: 16px;">
                <div style="font-size: 13px; font-weight: 900; color: #1a1a1a; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 2px solid #00873c;">{slide.comparison.right?.title || 'B'}</div>
                {#each (slide.comparison.right?.items || []) as item}
                  <div style="font-size: 11px; color: #444; padding: 4px 0;">{item}</div>
                {/each}
              </div>
            </div>
            {#if slide.action_line}
              <div style="margin-top: 16px; padding: 10px 14px; border-top: 2px solid #1a1a1a; font-size: 11px; font-weight: 700; color: #1a1a1a;">{slide.action_line}</div>
            {/if}

          {:else if slide.layout === 'recommendations'}
            <!-- RECOMMENDATIONS SLIDE -->
            <div style="font-size: 16px; font-weight: 800; color: #1a1a1a; line-height: 1.3; margin-bottom: 20px;">{slide.title}</div>
            <div style="width: 100%; height: 2px; background: #1a1a1a; margin-bottom: 20px;"></div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
              {#each (slide.bullets || []) as b, bi}
                <div style="padding: 14px; border: 2px solid #e0e0d8; background: #fff;">
                  <div style="font-size: 10px; font-weight: 900; color: #D24726; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px;">PRIORITY {bi + 1}</div>
                  <div style="font-size: 12px; color: #333; line-height: 1.4;">{b}</div>
                </div>
              {/each}
            </div>
            {#if slide.action_line}
              <div style="margin-top: 16px; padding: 10px 14px; border-top: 2px solid #1a1a1a; font-size: 11px; font-weight: 700; color: #1a1a1a;">{slide.action_line}</div>
            {/if}

          {:else}
            <!-- GENERIC BULLETS -->
            <div style="font-size: 16px; font-weight: 800; color: #1a1a1a; line-height: 1.3; margin-bottom: 20px;">{slide.title}</div>
            <div style="width: 100%; height: 2px; background: #1a1a1a; margin-bottom: 20px;"></div>
            {#each (slide.bullets || []) as b, bi}
              <div style="font-size: 13px; color: #333; padding: 10px 14px; border-left: 3px solid #D24726; margin-bottom: 8px; background: #fafaf8;">
                <span style="font-weight: 800; color: #D24726;">{bi + 1}.</span> {b}
              </div>
            {/each}
            {#if slide.action_line}
              <div style="margin-top: 16px; padding: 10px 14px; border-top: 2px solid #1a1a1a; font-size: 11px; font-weight: 700; color: #1a1a1a;">{slide.action_line}</div>
            {/if}
          {/if}

        </div>
      {/if}
    </div>

    <!-- Thumbnail navigation -->
    <div style="padding: 8px 16px; border-top: 2px solid #1a1a1a; background: #f5f5f0; display: flex; align-items: center; justify-content: space-between;">
      <button onclick={() => currentSlide = Math.max(0, currentSlide - 1)} disabled={currentSlide === 0} style="font-size: 11px; font-weight: 900; padding: 4px 10px; border: 1px solid #ccc; background: #fff; cursor: pointer;">← PREV</button>
      <div style="display: flex; gap: 3px; align-items: center;">
        {#each slidesData as s, si}
          <button onclick={() => currentSlide = si} style="width: 28px; height: 18px; border: {si === currentSlide ? '2px solid #D24726' : '1px solid #ccc'}; background: {si === currentSlide ? '#FFF3E0' : '#fff'}; cursor: pointer; padding: 0; font-size: 7px; font-weight: 700; color: {si === currentSlide ? '#D24726' : '#999'};">{si + 1}</button>
        {/each}
      </div>
      <button onclick={() => currentSlide = Math.min(slidesData.length - 1, currentSlide + 1)} disabled={currentSlide >= slidesData.length - 1} style="font-size: 11px; font-weight: 900; padding: 4px 10px; border: 1px solid #ccc; background: #fff; cursor: pointer;">NEXT →</button>
    </div>

  {:else}
    <div style="flex: 1; display: flex; align-items: center; justify-content: center; color: #999; font-size: 11px;">No slides generated</div>
  {/if}
</div>
{/if}

  <!-- Slides artifact panel (skl_pptx_builder skill) -->
  {#if presPanelOpen && activePresId !== null}
    <SlidesPanel
      presId={activePresId}
      projectSlug={projectSlug}
      onClose={() => { presPanelOpen = false; activePresId = null; }}
    />
  {/if}

  <!-- Deep Deck Panel (DP button — 6-stage research-then-present pipeline) -->
  {#if deepDeckOpen}
    <DeepDeckPanel
      projectSlug={projectSlug}
      agentName={projectInfo?.agent_name || 'Agent'}
      messages={messages}
      sessionId={sessionId}
      onComplete={onDeepDeckComplete}
      onClose={() => { deepDeckOpen = false; }}
    />
  {/if}

  <!-- Dashboard Side Panel (legacy — only opens via [DASHBOARD:id] tag or PIN flow) -->
  {#if dashboardPanelOpen}
    <DashboardPanel
      dashboardId={activeDashboardId}
      projectSlug={projectSlug}
      generating={dashboardGenerating}
      unsavedId={unsavedDashboardId}
      onClose={() => { if (unsavedDashboardId) { discardDashboard(); } else { dashboardPanelOpen = false; activeDashboardId = null; } }}
      onSelectDashboard={(id) => { activeDashboardId = id || null; }}
      onSave={confirmDashboardSave}
      onDiscard={discardDashboard}
    />
  {/if}

  <!-- Deep Dashboard Artifact Panel -->
  <ArtifactPanel
    open={dashPanelOpen}
    spec={dashSpec}
    data={dashData}
    thinkingLog={dashThinking}
    deepening={dashDeepening}
    findings={dashFindings}
    onClose={() => dashPanelOpen = false}
    onSave={saveDashFromPanel}
    onRegenerate={() => { try { sessionStorage.removeItem(`dash_panel_${sessionId}`); } catch {} openDeepDashboard(true); }}
  />

<!-- Metric Fix Modal (A3+A6) -->
{#if metricFixOpen}
  <MetricFixModal
    slug={projectSlug}
    question={metricFixQuestion}
    sql={metricFixSql}
    onclose={() => { metricFixOpen = false; }}
  />
{/if}

<!-- Metric Def Popover (A4) -->
{#if metricDefPopoverOpen && metricDefPopoverName}
  <MetricDefPopover
    slug={projectSlug}
    name={metricDefPopoverName}
    onclose={() => { metricDefPopoverOpen = false; metricDefPopoverName = ''; }}
  />
{/if}

<!-- Schedule Analysis Modal -->
<ScheduleAnalysisModal
  bind:open={scheduleOpen}
  projectSlug={projectSlug}
  msgId={scheduleMsgId}
  initialName={scheduleInitialName}
  initialDescription={scheduleInitialDescription}
  initialSteps={scheduleInitialSteps}
  onClose={() => { scheduleOpen = false; }}
  onSaved={(wfId, slug) => { scheduleToast = { wfId, slug }; setTimeout(() => { scheduleToast = null; }, 4000); }}
/>

{#if scheduleToast}
  <div style="position: fixed; bottom: 20px; right: 20px; z-index: 240; background: var(--pw-ink); color: var(--pw-bg); padding: 10px 14px; border-radius: 0; box-shadow: 0 4px 12px rgba(0,0,0,0.2); font-size: 12px; display: flex; gap: 10px; align-items: center;">
    <span>Saved as workflow #{scheduleToast.wfId}</span>
    <a href="{base}/ui/agent-os/workflows" style="color: var(--pw-accent-soft, #f0c4b0); text-decoration: underline; font-weight: 700;">View →</a>
  </div>
{/if}

<!-- Inline action toast -->
{#if actionFlash}
  <div style="position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); z-index: 9999; padding: 10px 18px; font-size: 13px; font-weight: 600; border-radius: 6px; box-shadow: 0 4px 16px rgba(0,0,0,0.25); background: {actionFlash.kind === 'ok' ? 'var(--pw-accent, #c96342)' : actionFlash.kind === 'warn' ? '#b87c0a' : '#b03030'}; color: #fff;">
    {actionFlash.text}
  </div>
{/if}

<!-- Pin to Dashboard Modal -->
{#if showPinModal}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div style="position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 200; display: flex; align-items: center; justify-content: center;" onclick={(e) => { if (e.target === e.currentTarget) showPinModal = false; }}>
    <div class="ink-border stamp-shadow" style="background: var(--pw-bg); width: 400px;">
      <div class="dark-title-bar" style="padding: 8px 14px; font-size: 11px;">PIN TO DASHBOARD</div>
      <div style="padding: 16px;">

        <!-- Widget title -->
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">WIDGET TITLE</div>
        <input type="text" bind:value={pinWidgetTitle} style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg); margin-bottom: 12px;" />

        <!-- Select dashboard -->
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 6px;">SELECT DASHBOARD</div>
        {#if userDashboards.length > 0}
          <div style="display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; max-height: 150px; overflow-y: auto;">
            {#each userDashboards as d}
              <button
                style="text-align: left; padding: 8px 12px; border: 2px solid {selectedDashId === d.id ? 'var(--pw-accent)' : 'var(--pw-ink)'}; background: {selectedDashId === d.id ? 'var(--pw-accent-soft)' : 'var(--pw-surface)'}; cursor: pointer; font-family: var(--pw-font-body);"
                onclick={() => { selectedDashId = d.id; newDashNameForPin = ''; }}
              >
                <div style="font-size: 11px; font-weight: 900; text-transform: uppercase;">{d.name}</div>
                <div style="font-size: 11px; color: var(--pw-muted);">{d.widget_count} widgets</div>
              </button>
            {/each}
          </div>
        {/if}

        <!-- Or create new -->
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">OR CREATE NEW DASHBOARD</div>
        <input type="text" bind:value={newDashNameForPin} placeholder="New dashboard name..." onfocus={() => { selectedDashId = null; }} style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg); margin-bottom: 16px;" />

        <div class="flex gap-2">
          <button class="send-btn" onclick={confirmPin} disabled={!selectedDashId && !newDashNameForPin.trim()} style="flex: 1; padding: 8px; font-size: 11px; justify-content: center; display: flex;">PIN</button>
          <button class="feedback-btn" onclick={() => showPinModal = false} style="flex: 1; padding: 8px; font-size: 11px; justify-content: center; display: flex; font-weight: 700;">CANCEL</button>
        </div>
      </div>
    </div>
  </div>
{/if}

<style>
/* === Design A: starter cards 2x2 grid, ChatGPT-style === */
:global(.starter-grid) {
 display: grid;
 grid-template-columns: repeat(2, 1fr);
 gap: 12px;
 max-width: 720px;
 margin: 0 auto;
 width: 100%;
}
:global(.starter-card) {
 background: #ffffff;
 border: 1px solid #ebebeb;
 border-radius: 0;
 padding: 16px 18px;
 text-align: left;
 cursor: pointer;
 display: flex;
 flex-direction: column;
 gap: 8px;
 min-height: 100px;
 transition: border-color 150ms, box-shadow 150ms, transform 100ms;
 font-family: inherit;
}
:global(.starter-card:hover:not(:disabled)) {
 border-color: #c96342;
 box-shadow: 0 2px 8px rgba(201,99,66,0.10);
 transform: translateY(-1px);
}
:global(.starter-card:disabled) { opacity: 0.5; cursor: not-allowed; }
:global(.starter-card-head) {
 display: flex;
 align-items: center;
 gap: 8px;
}
:global(.starter-icon) {
 font-size: 18px;
 line-height: 1;
}
:global(.starter-label) {
 font-size: 10px;
 font-weight: 700;
 letter-spacing: 0.08em;
 text-transform: uppercase;
 color: #c96342;
}
:global(.starter-text) {
 font-size: 13px;
 color: #1a1614;
 line-height: 1.4;
 font-weight: 500;
}
@media (max-width: 640px) {
 :global(.starter-grid) { grid-template-columns: 1fr; }
}

/* === Sim tag cards + what-if chips === */
.sim-progress-card,
.sim-report-card {
 display:flex; align-items:center; gap:12px;
 margin: 8px 0 4px; padding: 10px 14px;
 border-radius: 0;
 background: var(--pw-bg-alt, #faf2ed);
 border: 1px solid var(--pw-border, #e3e1dc);
}
.sim-progress-card { border-left: 3px solid var(--pw-accent, #c96342); }
.sim-report-card { border-left: 3px solid #2e7d32; }
.sim-pulse {
 color: var(--pw-accent, #c96342); font-size: 13px;
 animation: simPulse 1.4s ease-in-out infinite;
}
@keyframes simPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
.sim-link {
 font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
 color: var(--pw-accent, #c96342); text-decoration: none;
 padding: 4px 10px; border: 1px solid var(--pw-accent, #c96342); border-radius: 0;
}
.sim-link:hover { background: var(--pw-accent, #c96342); color: #fff; }
.sim-link-done { color: #2e7d32; border-color: #2e7d32; }
.sim-link-done:hover { background: #2e7d32; color: #fff; }

.sim-chip-row {
 display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
 padding: 4px 28px 0;
 max-width: 1100px; margin: 0 auto;
}
.sim-chip-label {
 font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
 color: var(--pw-muted, #6f6e69); text-transform: uppercase;
 margin-right: 4px;
}
.sim-chip {
 font-size: 11px; padding: 4px 10px;
 background: var(--pw-bg, #fff);
 border: 1px dashed var(--pw-border, #e3e1dc);
 color: var(--pw-ink, #2c2c2c);
 border-radius: 0; cursor: pointer;
 font-family: inherit;
}
.sim-chip:hover:not(:disabled) {
 border-style: solid;
 border-color: var(--pw-accent, #c96342);
 color: var(--pw-accent, #c96342);
}
.sim-chip:disabled { opacity: 0.5; cursor: not-allowed; }

/* === Single-row composer card === */
.composer-card { background:#fff; border:1px solid var(--pw-border); border-radius: 0; padding:0; margin:0 auto; max-width:1100px; overflow:visible; transition:box-shadow .15s, border-color .15s; box-shadow:0 2px 8px rgba(0,0,0,0.04); }
.composer-card.focused, .composer-card:focus-within { border-color:var(--pw-accent, #c96342); box-shadow:0 0 0 3px rgba(201,99,66,0.10); }
.composer-row { display:flex; align-items:center; gap:0; min-height:48px; padding:4px 6px; overflow:visible; }
.composer-seg { display:flex; align-items:center; gap:4px; flex-shrink:0; padding:0 8px; position:relative; }
.composer-seg :global(.mode-dropdown) { z-index: 1000; max-height: 60vh; overflow-y: auto; }
.composer-seg + .composer-seg { border-left:1px solid var(--pw-border); }
.composer-input { flex:1 1 auto; min-width:0; border:0; outline:0; resize:none; background:transparent; font:inherit; font-size:13px; color:var(--pw-ink); line-height:1.5; padding:10px 12px; min-height:36px; max-height:200px; border-left:1px solid var(--pw-border); }
.composer-input::placeholder { color:var(--pw-muted, #9b9b9b); }
.composer-send { width:36px; height:36px; border-radius:50%; background:var(--pw-accent, #c96342); color:#fff; border:0; cursor:pointer; display:inline-flex; align-items:center; justify-content:center; flex-shrink:0; margin:0 6px; box-shadow:0 2px 6px rgba(201,99,66,0.25); transition:all .15s; }
.composer-send:hover:not(:disabled) { background:#b85a3a; box-shadow:0 4px 10px rgba(201,99,66,0.35); }
.composer-send:disabled { background:#e5b4b4; cursor:not-allowed; box-shadow:none; }
.composer-actions { border-left:1px solid var(--pw-border); display:flex; gap:4px; padding:0 4px; }
.composer-actions button { padding:6px 8px; border-radius: 6px; background:transparent; border:1px solid transparent; cursor:pointer; font-size:11px; font-weight:600; color:var(--pw-ink); transition:all .15s; font-family:inherit; }
.composer-actions button:hover:not(:disabled) { background:var(--pw-bg-alt); border-color:var(--pw-border); }
.composer-actions button:disabled { color:var(--pw-muted); cursor:not-allowed; opacity:0.5; }
.cmp-icon-btn { display:inline-flex; align-items:center; gap:6px; }
.cmp-icon-label { font-size:11px; font-weight:600; letter-spacing:0.02em; }
.cmp-icon-btn:hover:not(:disabled) { transform:translateY(-1px); }
/* Dashboard icon = teal (matches data/analytics products) */
.cmp-dash { color:#0e7c86; }
.cmp-dash:hover:not(:disabled) { background:rgba(14,124,134,0.08); border-color:rgba(14,124,134,0.3); color:#0e7c86; }
/* Presentation icon = PowerPoint orange/red */
.cmp-pres { color:#d24726; }
.cmp-pres:hover:not(:disabled) { background:rgba(210,71,38,0.08); border-color:rgba(210,71,38,0.3); color:#d24726; }
/* Excel icon = Excel green */
.cmp-excel { color:#217346; }
.cmp-excel:hover:not(:disabled) { background:rgba(33,115,70,0.08); border-color:rgba(33,115,70,0.3); color:#217346; }
/* Research icon = violet/purple (deep-research aesthetic) */
.cmp-research { color:#7c3aed; }
.cmp-research:hover:not(:disabled) { background:rgba(124,58,237,0.08); border-color:rgba(124,58,237,0.3); color:#7c3aed; }
@media (max-width: 720px) {
  .cmp-icon-label { display:none; }
}
.composer-filters button { padding:4px 8px; border-radius: 0; background:transparent; border:0; cursor:pointer; font-size:11px; font-weight:500; color:var(--pw-muted); display:inline-flex; align-items:center; gap:4px; font-family:inherit; }
.composer-filters button:hover:not(:disabled) { background:var(--pw-bg-alt); color:var(--pw-ink); }
.composer-filters button:disabled { opacity:.5; cursor:not-allowed; }
.composer-filters .caret { font-size:10px; color:var(--pw-ink-muted, var(--pw-muted)); }
.composer-hint { font-size: 11px; color: var(--pw-ink-muted, #7a6f60); padding: 4px 12px 0; flex-basis: 100%; }
.composer-hint code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10.5px; padding: 1px 4px; background: var(--pw-bg-alt); border-radius: 2px; }
@media (max-width: 720px) {
 .composer-row { flex-wrap:wrap; }
 .composer-input { width:100%; flex-basis:100%; border-left:0; border-top:1px solid var(--pw-border); margin-top:4px; }
 .composer-card { margin:0 12px; }
}
.cmp-chip { display:inline-flex; align-items:center; gap:5px; height:28px; padding:0 10px; border-radius: 0; border:1px solid var(--pw-border); background:transparent; cursor:pointer; font:inherit; font-size:11px; color:var(--pw-ink); }
.cmp-chip:hover:not(:disabled) { background:rgba(201,99,66,0.08); border-color:var(--pw-accent); color:var(--pw-accent); }
.cmp-chip:disabled { opacity:.5; cursor:not-allowed; }
.cmp-chip .caret { font-size: 11px; color:var(--pw-ink-muted, var(--pw-muted)); }
.cmp-icon { width:30px; height:30px; border-radius: 0; border:1px solid var(--pw-border); background:#fff; cursor:pointer; font:inherit; font-size:11px; font-weight:600; color:var(--pw-ink); display:flex; align-items:center; justify-content:center; padding:0; }
.cmp-icon:hover:not(:disabled) { border-color:var(--pw-accent); color:var(--pw-accent); }
.cmp-icon:disabled { opacity:.4; cursor:not-allowed; }
.cmp-pptx { color:#d97706; border-color:#fbbf24; }
.cmp-pptx:hover:not(:disabled) { background:rgba(217,119,6,0.08); border-color:#d97706; color:#d97706; }
.cmp-xlsx { color:#16a34a; border-color:#86efac; }
.cmp-xlsx:hover:not(:disabled) { background:rgba(22,163,74,0.08); border-color:#16a34a; color:#16a34a; }
.cmp-send { width:32px; height:32px; border-radius:50%; border:0; background:transparent; color:var(--pw-ink-muted, var(--pw-muted)); cursor:pointer; font-size:14px; display:flex; align-items:center; justify-content:center; padding:0; }
.cmp-send.ready { background:var(--pw-accent,#c96342); color:#fff; }
.cmp-send:disabled { opacity:.4; cursor:not-allowed; }

/* Sim wizard smart chips */
.sim-chips { display:inline-flex; align-items:center; flex-wrap:wrap; gap:8px; margin:8px 24px 0; padding:6px 10px; font-size:11px; color:var(--pw-muted); }
.sim-chips-lead { font-weight:700; color:var(--pw-muted); }
.sim-chip { display:inline-flex; align-items:center; height:24px; padding:0 10px; border-radius: 0; border:1px solid var(--pw-border); background:var(--pw-bg-alt); color:var(--pw-ink); font:inherit; font-size:11px; cursor:pointer; }
.sim-chip:hover { background:var(--pw-accent-soft, rgba(201,99,66,0.10)); border-color:var(--pw-accent); color:var(--pw-accent); }
.sim-chip-sep { color:var(--pw-border); font-weight:700; }
.cmp-stop { background:var(--pw-error); color:#fff; }

/* === Agent header (sentence-case, serif) === */
.msg-meta { display:flex; align-items:center; gap:8px; margin-bottom:10px; margin-top:6px; }
.msg-meta .ast { color:var(--pw-accent); font-size:13px; }
.msg-agent { font-family:var(--pw-serif,'EB Garamond',Tiempos,serif); font-size:14px; color:var(--pw-ink); }
.msg-time { font-size:11px; color:var(--pw-ink-muted, var(--pw-muted)); }

/* === Action toolbar + feedback row === */
.action-toolbar { display:flex; gap:6px; margin-top:12px; padding-top:10px; border-top:1px solid var(--pw-border); flex-wrap:wrap; }
.action-btn { font-size:11px; padding:5px 12px; border:1px solid var(--pw-border); background:transparent; color:var(--pw-ink); border-radius: 0; cursor:pointer; font:inherit; font-size:11px; }
.action-btn:hover { background:rgba(201,99,66,0.08); border-color:var(--pw-accent); color:var(--pw-accent); }
.fb-row { display:flex; gap:4px; margin-top:8px; }
.fb-ic { width:28px; height:28px; border:0; border-radius: 0; background:transparent; cursor:pointer; font-size:13px; color:var(--pw-ink-muted, var(--pw-muted)); display:flex; align-items:center; justify-content:center; padding:0; }
.fb-ic:hover { background:var(--pw-bg-alt); color:var(--pw-ink); }

/* === Soften route CLI cards === */
:global(.cli-terminal) { background:var(--pw-bg-alt) !important; border-radius: 0!important; border:1px solid var(--pw-border) !important; font-family:'JetBrains Mono', ui-monospace, monospace !important; color:var(--pw-ink) !important; }
:global(.cli-terminal .cli-prompt) { color:var(--pw-accent) !important; }
:global(.cli-terminal .cli-command) { color:#3a8dff !important; }
:global(.cli-terminal .cli-output) { color:var(--pw-ink) !important; }
:global(.cli-terminal .cli-dim) { color:var(--pw-ink-muted, var(--pw-muted)) !important; }
:global(.cli-terminal .cli-success) { color:#2c6e3f !important; }

/* Thinking spinner — coral dots */
:global(.cli-spinner span), :global(.typing-indicator span) { background:var(--pw-accent) !important; }

/* === Auto-saved learnings — soft green === */
:global(.learning-card) { background:#f0f9f4 !important; border:1px solid #cfe6d7 !important; border-radius: 0!important; padding:14px !important; margin-top:10px; }
:global(.learning-card-header) { color:#2c6e3f !important; font-size:12px !important; font-weight:500 !important; text-transform:none !important; letter-spacing:0 !important; display:flex; align-items:center; gap:8px; }
:global(.learning-card-facts .learning-fact) { font-size:12px !important; font-weight:400 !important; color:var(--pw-ink-soft, var(--pw-ink)) !important; }
:global(.learning-card-facts .learning-fact span:last-child) { background:rgba(0,0,0,0.04) !important; color:#807a72 !important; font-size:10px !important; font-weight:500 !important; }
:global(.learning-approve-btn) { background:#2c6e3f !important; color:#fff !important; border:0 !important; padding:7px 14px !important; font-size:11px !important; text-transform:none !important; letter-spacing:0 !important; border-radius: 0!important; cursor:pointer; }
:global(.learning-dismiss-btn) { background:transparent !important; color:var(--pw-ink-muted, var(--pw-muted)) !important; border:1px solid var(--pw-border) !important; padding:7px 14px !important; font-size:11px !important; text-transform:none !important; letter-spacing:0 !important; border-radius: 0!important; cursor:pointer; }

/* === Follow-up pills — ghost rounded === */
:global(.suggestion-btn) { background:transparent !important; border:1px solid var(--pw-border) !important; color:var(--pw-ink) !important; padding:8px 14px !important; font-size:12px !important; text-transform:none !important; letter-spacing:0 !important; font-weight:400 !important; border-radius: 0!important; cursor:pointer; font:inherit; font-size:12px; }
:global(.suggestion-btn:hover) { background:rgba(201,99,66,0.08) !important; border-color:var(--pw-accent) !important; color:var(--pw-accent) !important; }

/* === Response tabs — underline only === */
:global(.response-tabs) { display:flex !important; gap:0 !important; border-bottom:1px solid var(--pw-border) !important; background:transparent !important; padding:0 !important; }
:global(.response-tab) { padding:8px 12px !important; font:inherit !important; font-size:11px !important; border:0 !important; background:transparent !important; color:var(--pw-ink-muted, var(--pw-muted)) !important; cursor:pointer; border-bottom:2px solid transparent !important; margin-bottom:-1px !important; text-transform:none !important; letter-spacing:0 !important; font-weight:400 !important; }
:global(.response-tab:hover) { color:var(--pw-ink) !important; }
:global(.response-tab.response-tab-active) { color:var(--pw-accent) !important; border-bottom-color:var(--pw-accent) !important; font-weight:500 !important; }
:global(.response-tab .tab-badge) { display:inline-block !important; margin-left:5px !important; padding:1px 5px !important; border-radius: 0!important; background:var(--pw-bg-alt) !important; color:var(--pw-ink-muted, var(--pw-muted)) !important; font-size: 11px !important; border:0 !important; }

.recent-dashboards { padding: 8px; border-top: 1px solid var(--pw-border); }
.recent-dashboards .rd-header { font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted); padding: 6px 4px 8px; }
.recent-dashboards .dash-item { display: flex; flex-direction: column; gap: 2px; padding: 6px 8px; font-size: 11px; text-decoration: none; color: inherit; border-radius: 0; }
.recent-dashboards .dash-item:hover { background: rgba(0,0,0,0.05); }
.recent-dashboards .dash-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-dashboards .dash-time { font-size: 11px; color: var(--pw-muted); }
.recent-dashboards .empty { padding: 12px; font-size: 11px; color: var(--pw-muted); text-align: center; }
.drift-bell-wrap { position: absolute; top: 8px; right: 110px; z-index: 1001; }
.bell-btn {
 position: relative; background: transparent; border: 1px solid var(--pw-border);
 color: var(--pw-muted); padding: 6px 10px; cursor: pointer; font-size: 13px;
 border-radius: var(--pw-radius-sm); transition: color .15s, border-color .15s;
}
.bell-btn:hover { color: var(--pw-accent); border-color: var(--pw-accent); }
.bell-dot {
 position: absolute; top: -4px; right: -4px; background: var(--pw-error);
 color: white; border-radius: 0; padding: 1px 5px; font-size: 10px;
 font-weight: bold; min-width: 14px; text-align: center;
}
.drift-dropdown {
 position: absolute; right: 0; top: 100%; width: 380px;
 background: var(--pw-surface); border: 1px solid var(--pw-border); padding: 0;
 z-index: 1000; max-height: 500px; overflow-y: auto;
 font-family: var(--pw-font-body); font-size: 11px; color: var(--pw-ink);
 border-radius: var(--pw-radius-sm); box-shadow: var(--pw-shadow-md);
}
.drift-hdr { padding: 10px 14px; border-bottom: 1px solid var(--pw-border); font-weight: 600; background: var(--pw-bg-alt); color: var(--pw-ink); font-size: 11px; letter-spacing: .04em; }
.drift-empty { padding: 16px; text-align: center; color: var(--pw-muted); }
.drift-row { padding: 10px 14px; border-bottom: 1px solid var(--pw-border-soft); }
.drift-row:last-child { border-bottom: none; }
.drift-row .sev-dot { color: var(--pw-muted); }
.drift-row.sev-critical .sev-dot { color: var(--pw-error); }
.drift-row.sev-high .sev-dot { color: #a06000; }
.drift-row.sev-med .sev-dot { color: #c4a000; }
.drift-row.sev-low .sev-dot { color: var(--pw-muted); }
.mini-btn {
 background: transparent; border: 1px solid var(--pw-border); color: var(--pw-ink-soft);
 padding: 3px 8px; cursor: pointer; font-size: 10px; font-family: var(--pw-font-body);
 border-radius: var(--pw-radius-pill); transition: color .15s, border-color .15s;
}
.mini-btn:hover { border-color: var(--pw-accent); color: var(--pw-accent); }

/* === Soften remaining brutalist chat-area blocks === */

/* 1. Route CLI / exec block — extra polish on top of existing .cli-terminal override */
:global(.cli-terminal), :global(.cli-exec), :global(.exec-block) {
 background: var(--pw-bg-alt) !important;
 border: 1px solid var(--pw-border) !important;
 border-radius: 0!important;
 padding: 10px 14px !important;
 font-family: 'JetBrains Mono', ui-monospace, monospace !important;
 font-size: 12px !important;
 color: var(--pw-ink) !important;
}
:global(.cli-prompt), :global(.exec-prompt) { color: var(--pw-accent, #c96342) !important; }
:global(.cli-keyword), :global(.exec-flag) { color: #3a8dff !important; }
:global(.cli-success) { color: #16a34a !important; }

/* 2. RESULT DATA / EXECUTION LOG / RESULT TABLES headers */
:global(.result-header), :global(.exec-header), :global(.sources-section-header), :global(.section-header-dark) {
 background: var(--pw-bg-alt) !important;
 color: var(--pw-ink-muted) !important;
 text-transform: uppercase !important;
 letter-spacing: 0.05em !important;
 font-size: 11px !important;
 font-weight: 600 !important;
 padding: 8px 14px !important;
 border-radius: 0!important;
 border: 1px solid var(--pw-border) !important;
 border-bottom: 0 !important;
}

/* 3. Data table headers — kill black filled th cells */
:global(.data-table thead), :global(.data-table thead tr), :global(.tbl thead), :global(.prose-chat thead) {
 background: var(--pw-bg-alt) !important;
}
:global(.data-table thead th), :global(.tbl thead th), :global(.prose-chat thead th) {
 background: var(--pw-bg-alt) !important;
 color: var(--pw-ink-muted) !important;
 font-weight: 600 !important;
 text-transform: uppercase !important;
 letter-spacing: 0.04em !important;
 font-size: 11.5px !important;
 padding: 10px 12px !important;
 border-bottom: 1px solid var(--pw-border) !important;
}
:global(.data-table tbody tr:nth-child(even)) { background: var(--pw-bg-alt) !important; }
:global(.data-table tbody td), :global(.tbl tbody td) {
 padding: 9px 12px !important;
 border-bottom: 1px solid var(--pw-border) !important;
 font-size: 12px !important;
}

/* 5. Chart container — soften thick black border */
:global(.chart-frame), :global(.chart-container), :global(.chart-wrap) {
 border: 1px solid var(--pw-border) !important;
 border-radius: 0!important;
 background: #fff !important;
 padding: 16px !important;
}

/* 6. Chart type pills — warm */
:global(.chart-type-btn), :global(.chart-pill) {
 background: transparent !important;
 border: 1px solid var(--pw-border) !important;
 border-radius: 0!important;
 padding: 6px 12px !important;
 color: var(--pw-ink) !important;
 font-size: 11px !important;
 font-weight: 500 !important;
 letter-spacing: 0.04em !important;
 cursor: pointer !important;
}
:global(.chart-type-btn:hover), :global(.chart-pill:hover) {
 background: rgba(201,99,66,0.08) !important;
 border-color: var(--pw-accent) !important;
 color: var(--pw-accent) !important;
}
:global(.chart-type-btn.active), :global(.chart-pill.active),
:global(.chart-type-btn.chart-type-btn-active), :global(.chart-pill.chart-pill-active),
:global(.chart-type-btn[aria-pressed="true"]) {
 background: var(--pw-accent, #c96342) !important;
 border-color: var(--pw-accent) !important;
 color: #fff !important;
}

/* 7. KPI metric cards — Analysis tab boxes */
:global(.kpi-card), :global(.metric-card), :global(.kpi-grid > *), :global(.kpi-tile) {
 background: #fff !important;
 border: 1px solid var(--pw-border) !important;
 border-radius: 0!important;
 padding: 16px !important;
 box-shadow: none !important;
}
:global(.kpi-value), :global(.kpi-card .value) {
 font-family: var(--pw-serif, 'EB Garamond', serif) !important;
 font-weight: 400 !important;
}

/* 8. CONFIDENCE bar — warm coral */
:global(.confidence-bar), :global(.conf-bar) {
 background: var(--pw-bg-alt) !important;
 border-radius: 0!important;
 height: 6px !important;
 overflow: hidden !important;
}
:global(.confidence-fill), :global(.conf-fill) {
 background: var(--pw-accent, #c96342) !important;
 height: 100% !important;
 border-radius: 0!important;
}
:global(.confidence-fill.high), :global(.conf-fill.high) { background: #16a34a !important; }
:global(.confidence-fill.medium), :global(.conf-fill.medium) { background: #f59e0b !important; }
:global(.confidence-fill.low), :global(.conf-fill.low) { background: #dc2626 !important; }

/* 9. User message bubble — Issue #25: canonical styles live in app.css
 `.dash-user-bubble`. Legacy selectors below INHERIT from it via reset
 so older markup keeps working without `!important` overrides. */
:global(.user-bubble), :global(.bubble-user), :global(.msg-user), :global(.user-msg-bubble) {
 background: #f0f0f0 !important;
 color: #1a1614;
 border: none;
 border-radius: 0;
 padding: 8px 14px;
 max-width: 60ch;
 width: fit-content;
 margin-left: auto;
 align-self: flex-end;
 font-size: 13px;
 /* Issue #26: see comment in app.css .dash-user-bubble. Sans-serif HARDCODED. */
 font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif !important;
 line-height: 1.5;
 word-wrap: break-word;
 overflow-wrap: anywhere;
}
:global(.user-bubble *), :global(.bubble-user *), :global(.msg-user *) {
 color: inherit;
}

/* === Phase 6+: de-brutalize KPI tiles, Sources tiles, learning cards === */

/* KPI tiles in Analysis tab — [KPI:value|label|change] render */
:global(.kpi-tile-inline) {
 background: #fff !important;
 border: 1px solid var(--pw-border) !important;
 border-radius: 0!important;
 padding: 18px 20px !important;
 box-shadow: none !important;
 max-width: none !important;
}
:global(.kpi-tile-inline .kpi-tile-value) {
 font-family: var(--pw-serif, 'EB Garamond', serif) !important;
 font-weight: 400 !important;
 font-size: 28px !important;
 color: var(--pw-ink) !important;
}
:global(.kpi-tile-inline .kpi-tile-label) {
 font-size: 11px !important;
 text-transform: uppercase !important;
 letter-spacing: 0.05em !important;
 color: var(--pw-ink-muted, var(--pw-muted)) !important;
 font-weight: 600 !important;
 margin-top: 6px !important;
}
:global(.kpi-tile-inline .kpi-tile-delta) {
 font-size: 11px !important;
 font-weight: 500 !important;
 margin-top: 6px !important;
}
:global(.kpi-tile-inline .kpi-up) { color: #16a34a !important; }
:global(.kpi-tile-inline .kpi-down) { color: #c96342 !important; }
:global(.kpi-tile-inline .kpi-flat) { color: var(--pw-ink-muted, var(--pw-muted)) !important; }

/* Sources tab metric tiles (AGENT / MODE / QUERIES / RESULT TABLES / ANALYSIS) */
:global(.src-stat) {
 background: #fff !important;
 border: 1px solid var(--pw-border) !important;
 border-radius: 0!important;
 padding: 14px 16px !important;
 box-shadow: none !important;
}
:global(.src-stat .src-stat-label) {
 font-size: 10px !important;
 text-transform: uppercase !important;
 letter-spacing: 0.05em !important;
 color: var(--pw-ink-muted, var(--pw-muted)) !important;
 font-weight: 600 !important;
 margin-bottom: 6px !important;
}
:global(.src-stat .src-stat-value) {
 font-size: 14px !important;
 font-weight: 500 !important;
 color: var(--pw-ink) !important;
 text-transform: none !important;
 letter-spacing: 0 !important;
}

/* Sources CONFIDENCE tile — neutral by default, color only on value */
:global(.src-confidence) {
 background: #fff !important;
 border: 1px solid var(--pw-border) !important;
 border-radius: 0!important;
}
:global(.src-confidence .src-conf-value) {
 font-weight: 500 !important;
}
:global(.src-confidence.high .src-conf-value) { color: #16a34a !important; }
:global(.src-confidence.medium .src-conf-value) { color: #d97706 !important; }
:global(.src-confidence.low .src-conf-value) { color: #c96342 !important; }
:global(.src-confidence .src-conf-bar) {
 background: var(--pw-bg-alt) !important;
 border-radius: 0!important;
 overflow: hidden !important;
}

/* Sources block containers (DATA SOURCES / RESULT DATA / EXECUTION LOG wrappers) */
:global(.src-block) {
 border: 1px solid var(--pw-border) !important;
 border-radius: 0!important;
 background: #fff !important;
 overflow: hidden !important;
}

/* Section headers — kill black filled bars */
:global(.src-block-header) {
 background: var(--pw-bg-alt) !important;
 color: var(--pw-ink-muted, var(--pw-muted)) !important;
 text-transform: uppercase !important;
 letter-spacing: 0.05em !important;
 font-size: 11px !important;
 font-weight: 600 !important;
 padding: 10px 14px !important;
 border-bottom: 1px solid var(--pw-border) !important;
}

/* Learning cards — lighten chunky borders, ensure soft green for auto-saved */
:global(.learning-card) {
 background: #f0f9f4 !important;
 border: 1px solid #cfe6d7 !important;
 border-left: 3px solid #16a34a !important;
 border-radius: 0!important;
 padding: 12px 14px !important;
}

/* Catch-all: any remaining 2px ink-bordered card inside chat msg-body */
:global(.msg-body div[style*="border: 2px solid var(--pw-ink)"]),
:global(.chat-msg div[style*="border: 2px solid var(--pw-ink)"]) {
 border-width: 1px !important;
 border-color: var(--pw-border) !important;
 border-radius: 0!important;
}

.query-empty {
 display: flex;
 flex-direction: column;
 align-items: center;
 justify-content: center;
 padding: 40px 24px;
 text-align: center;
 color: var(--pw-ink-muted);
 border: 1px dashed var(--pw-border);
 border-radius: 0;
 margin: 8px 0;
}
.query-empty-icon { font-size: 24px; margin-bottom: 10px; opacity: 0.5; }
.query-empty-title { font-size: 13px; font-weight: 500; color: var(--pw-ink); margin-bottom: 4px; }
.query-empty-sub { font-size: 12.5px; line-height: 1.5; max-width: 420px; }

@media print {
 :global(nav), :global(.input-bar), :global(.nav-btn),
 :global([style*="CHAT HISTORY"]), :global(header) {
 display: none !important;
 }
 :global(body) {
 overflow: visible !important;
 background: white !important;
 }
 .slide-render-area {
 overflow: visible !important;
 height: auto !important;
 }
}

/* ── Project top nav (sticky horizontal menu) ─────────────────────────── */
.proj-page-wrap {
 display: flex;
 flex-direction: column;
 height: 100%;
 min-height: 0;
}
.proj-page-body {
 flex: 1;
 min-height: 0;
 display: flex;
 flex-direction: row;
 flex-wrap: nowrap;
 align-items: stretch;
 width: 100%;
 overflow: hidden;
}
.proj-topnav {
 position: sticky;
 top: 56px;
 z-index: 50;
 background: var(--pw-bg-alt, #f5f1e8);
 border-bottom: 1px solid var(--pw-border);
 box-shadow: 0 1px 0 rgba(0,0,0,0.02);
 flex-shrink: 0;
}
.proj-topnav-inner {
 display: flex;
 align-items: center;
 gap: 12px;
 max-width: 100%;
 padding: 6px 16px;
 height: 40px;
}
.proj-topnav-brand {
 display: inline-flex; align-items: center; gap: 8px;
 text-decoration: none;
 color: var(--pw-ink);
 flex-shrink: 0;
 padding: 4px 8px; border-radius: 0;
 transition: background .12s;
}
.proj-topnav-brand:hover { background: rgba(201,99,66,0.06); }
.proj-topnav-icon { font-size: 14px; }
.proj-topnav-name {
 font-family: var(--pw-font-body);
 font-size: 13px;
 font-weight: 700;
 white-space: nowrap;
 max-width: 220px;
 overflow: hidden;
 text-overflow: ellipsis;
}
.proj-topnav-divider {
 width: 1px;
 height: 22px;
 background: var(--pw-border);
 flex-shrink: 0;
}
.proj-topnav-scroll {
 display: flex;
 align-items: center;
 gap: 2px;
 overflow-x: auto;
 overflow-y: hidden;
 scrollbar-width: thin;
 scrollbar-color: var(--pw-border) transparent;
 flex: 1;
 min-width: 0;
 -webkit-overflow-scrolling: touch;
 scroll-behavior: smooth;
}
.proj-topnav-scroll::-webkit-scrollbar { height: 4px; }
.proj-topnav-scroll::-webkit-scrollbar-thumb { background: var(--pw-border); border-radius: 2px; }
.proj-topnav-scroll::-webkit-scrollbar-track { background: transparent; }
.proj-topnav-link {
 display: inline-flex; align-items: center;
 padding: 5px 12px;
 border-radius: 0;
 font-family: var(--pw-font-body);
 font-size: 12px;
 font-weight: 500;
 color: var(--pw-ink-soft, var(--pw-muted));
 text-decoration: none;
 white-space: nowrap;
 flex-shrink: 0;
 transition: background .12s, color .12s;
}
.proj-topnav-link:hover {
 background: rgba(201,99,66,0.06);
 color: var(--pw-accent);
}
.proj-topnav-link.active {
 background: rgba(201,99,66,0.12);
 color: var(--pw-accent);
 font-weight: 600;
}
@media (max-width: 640px) {
 .proj-topnav-brand .proj-topnav-name { max-width: 100px; }
 .proj-topnav-divider { height: 18px; }
 .proj-topnav-link { padding: 4px 10px; font-size: 11px; }
}

.sim-launch-card {
  margin: 12px 0;
  padding: 14px 16px;
  background: rgba(201, 99, 66, 0.06);
  border: 1px solid var(--pw-accent, #c96342);
  border-radius: 0;
}
.sim-launch-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.sim-launch-title { font-weight: 600; font-size: 13px; color: var(--pw-accent, #c96342); }
.sim-launch-id { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--pw-ink-soft); background: rgba(0,0,0,0.04); padding: 2px 6px; border-radius: 0; }
.sim-launch-body { font-size: 12px; color: var(--pw-ink-soft); margin-bottom: 10px; }
.sim-launch-open {
  display: inline-flex; align-items: center; gap: 4px;
  background: var(--pw-accent, #c96342); color: #fff; border: none;
  padding: 6px 14px; border-radius: 0; font-size: 12px; font-weight: 600;
  cursor: pointer;
}
.sim-launch-open:hover { filter: brightness(0.95); }
</style>
