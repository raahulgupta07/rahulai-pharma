<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy, tick } from 'svelte';
 import { goto } from '$app/navigation';
 import { generateSessionId, markdownToHtml, parseMarkdownTables, tableToCsv, hasNumericData, detectChartType, getAvailableTypes, parseChartHint } from '$lib';
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
 import { brand } from '$lib/stores/branding';
 import ChatMessageList, { formatCell, generateChartCaption } from '$lib/chat/ChatMessageList.svelte';
 import ReasoningTrace from '$lib/ReasoningTrace.svelte';
 import type { TraceItem } from '$lib/api';
import { parseClarify } from '$lib/chat/tag-parsers';
 import AgentRecommendations from '$lib/components/AgentRecommendations.svelte';
 import ScheduleAnalysisModal from '$lib/components/ScheduleAnalysisModal.svelte';
 import MetricFixModal from '$lib/metrics/MetricFixModal.svelte';
 import { base } from '$app/paths';
 import { agentGet, agentBootstrap, sendMessage } from '$lib/api';

 interface ProjectBrief {
 slug: string;
 name: string;
 agent_name: string;
 owned: boolean;
 }

 interface RoutingInfo {
 routed_to: string;
 slug: string;
 reason: string;
 }

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
 routing?: RoutingInfo;
 activeTab?: 'analysis' | 'data' | 'query' | 'chart' | 'sources';
 qualityScore?: number;
 showTrace?: boolean;
 proposedLearnings?: string[];
 proposedLearningsWithScores?: {fact: string; score: number}[];
 autoSavedLearnings?: string[];
 autoSavedWithScores?: {fact: string; score: number}[];
 learningsSaved?: boolean;
 dataTableIndex?: number;
 reasoningUsed?: string;
 analysisUsed?: string;
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
 let openSessionAbort: AbortController | null = null;

 // Mode selector
 let projects = $state<ProjectBrief[]>([]);
 let selectedMode = $state('auto'); // 'auto' or project slug
 let dropdownOpen = $state(false);

 // Close all dropdowns except the named one (mutual-exclusion).
 function closeOtherDropdowns(keep: string) {
 if (keep !== 'workflow') workflowPickerOpen = false;
 // reasoning + analysis chips removed
 if (keep !== 'agents') dropdownOpen = false;
 if (keep !== 'fanout') fanoutDropdownOpen = false;
 }

 // Workflows
 let allWorkflows = $state<{project: string; name: string; description?: string; steps: any[]}[]>([]);
 let workflowPickerOpen = $state(false);

 // Reasoning mode (legacy — kept for closeOtherDropdowns refs; chips removed)
 const reasoningMode = '';

 // Chat mode: Smart Router (default) vs My Agent (personal cross-project agent)
 let chatMode = $state<'smart_router' | 'my_agent'>('smart_router');
 let myAgentBootstrapped = $state(false);
 let myAgentBootstrapping = $state(false);
 let fanout = $state<number>(1); // 1, 3, or 0 (all)
 let fanoutDropdownOpen = $state(false);

 async function activateMyAgent() {
 chatMode = 'my_agent';
 if (myAgentBootstrapped || myAgentBootstrapping) return;
 myAgentBootstrapping = true;
 try {
 const existing = await agentGet();
 if (existing && (existing as any).id) {
 myAgentBootstrapped = true;
 } else {
 await agentBootstrap();
 myAgentBootstrapped = true;
 }
 } catch (e) {
 console.warn('My Agent bootstrap failed', e);
 } finally {
 myAgentBootstrapping = false;
 }
 }

 function fanoutLabel(n: number): string {
 if (n === 0) return 'all projects';
 if (n === 1) return '1 project';
 return `top ${n}`;
 }

 // Analysis type selector (legacy — chip removed)
 const analysisType = '';
 const _analysisTypes_unused = [
 { value: 'auto', label: 'AUTO', desc: 'Auto-detect analysis type', icon: 'M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83' },
 { value: 'descriptive', label: 'Descriptive', desc: 'Summarize what happened', icon: 'M4 6h16M4 12h16M4 18h10' },
 { value: 'diagnostic', label: 'Diagnostic', desc: 'Why did it happen?', icon: 'M9 12l2 2 4-4M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z' },
 { value: 'comparative', label: 'Comparative', desc: 'Compare groups or periods', icon: 'M18 20V10M12 20V4M6 20v-6' },
 { value: 'trend', label: 'Trend', desc: 'Patterns over time', icon: 'M3 17l6-6 4 4 8-8' },
 { value: 'predictive', label: 'Predictive', desc: 'Forecast future outcomes', icon: 'M13 2L3 14h9l-1 8 10-12h-9l1-8' },
 { value: 'prescriptive', label: 'Prescriptive', desc: 'Recommend actions', icon: 'M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2' },
 { value: 'anomaly', label: 'Anomaly', desc: 'Find outliers and oddities', icon: 'M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01' },
 { value: 'root_cause', label: 'Root Cause', desc: 'Drill into why', icon: 'M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10zM12 6v6l4 2' },
 { value: 'pareto', label: 'Pareto', desc: '80/20 analysis', icon: 'M3 3v18h18M7 16h2V8H7v8zM11 16h2V6h-2v10zM15 16h2v-4h-2v4zM19 16h2v-2h-2v2z' },
 { value: 'scenario', label: 'Scenario', desc: 'What-if simulations', icon: 'M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5' },
 ];

 async function loadAllWorkflows() {
 const wfs: {project: string; name: string; description?: string; steps: any[]}[] = [];
 for (const p of projects) {
 try {
 const res = await fetch(`/api/workflows?project=${p.slug}`, { headers: _headers() });
 if (res.ok) {
 const d = await res.json();
 for (const w of (d.workflows || [])) {
 wfs.push({ ...w, project: p.agent_name || p.name });
 }
 }
 } catch {}
 }
 allWorkflows = wfs;
 }

 async function runWorkflowChat(wf: {name: string; steps: any[]}) {
 workflowPickerOpen = false;
 const steps = wf.steps || [];
 if (steps.length === 0) {
 send(`Run the "${wf.name}" workflow`);
 return;
 }
 for (const step of steps) {
 const stepMsg = typeof step === 'string' ? step : (step.question || step.prompt || step.query || JSON.stringify(step));
 await send(stepMsg);
 await new Promise<void>((resolve) => {
 const check = () => { if (!isStreaming) resolve(); else setTimeout(check, 500); };
 setTimeout(check, 1000);
 });
 }
 }

 // Sidebar — persisted in localStorage, default open
 let sidebarOpen = $state(typeof localStorage !== 'undefined' ? localStorage.getItem('dash_sidebar') !== 'closed' : true);
 let pastSessions = $state<{session_id: string; created_at: string; updated_at: string}[]>([]);

 function toggleSidebar() {
 sidebarOpen = !sidebarOpen;
 localStorage.setItem('dash_sidebar', sidebarOpen ? 'open' : 'closed');
 if (sidebarOpen) loadSessions();
 }

 async function loadSessions() {
 try {
 // Load ALL user sessions across all agents
 const res = await fetch('/api/sessions', { headers: _headers() });
 if (res.ok) { const d = await res.json(); pastSessions = d.sessions || []; }
 } catch {}
 }

 async function registerSession(msg: string) {
 try {
 const proj = selectedMode !== 'auto' ? selectedMode : '';
 await fetch(`/api/sessions/register?session_id=${encodeURIComponent(sessionId)}&message=${encodeURIComponent(msg)}${proj ? `&project=${encodeURIComponent(proj)}` : ''}`, {
 method: 'POST', headers: _headers()
 });
 } catch {}
 }

 async function switchSession(sid: string) {
 sessionId = sid;
 localStorage.setItem('dash_super_session', sid);
 messages = [];
 sessionStartTime = getTimestamp();

 // Load messages from this session
 try {
 const res = await fetch(`/api/sessions/${sid}/messages`, { headers: _headers() });
 if (res.ok) {
 const data = await res.json();
 const loaded = data.messages || [];
 messages = loaded.map((m: any) => ({
 role: m.role,
 content: m.role === 'user' ? cleanUserMessageInline(m.content || '') : (m.content || ''),
 rawContent: m.role === 'user' ? (m.content || '') : undefined,
 timestamp: '',
 status: m.role === 'assistant' ? 'done' : undefined,
 toolCalls: [],
 workflowExpanded: false,
 trace: Array.isArray(m.trace) ? m.trace : [],
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
 if (isNaN(d.getTime())) return '';
 const now = new Date();
 const diff = now.getTime() - d.getTime();
 if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
 if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
 return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
 } catch { return ''; }
 }

 function _headers(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 function cleanUserMessageInline(raw: string): string {
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

 function getSelectedLabel(): string {
 if (selectedMode === 'auto') return 'AUTO';
 const p = projects.find(p => p.slug === selectedMode);
 return p ? p.agent_name : 'AUTO';
 }

 async function loadProjects() {
 try {
 const res = await fetch('/api/user-projects-brief', { headers: _headers() });
 if (res.ok) {
 const d = await res.json();
 projects = d.projects || [];
 // Load workflows after projects
 loadAllWorkflows();
 }
 } catch {}
 }

 function getTimestamp(): string {
 return new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
 }

 function getTotalDuration(tools: ToolCall[]): string {
 let total = 0;
 for (const t of tools) {
 if (t.duration) total += parseFloat(t.duration);
 }
 return total > 0 ? total.toFixed(1) + 's' : '';
 }

 function getAgentMode(tools: ToolCall[]): 'deep' | 'fast' {
 const names = tools.map(t => t.name.toLowerCase());
 if (names.some(n => n === 'think' || n === 'analyze')) return 'deep';
 const sqlCount = names.filter(n => n.includes('sql') || n.includes('query')).length;
 if (sqlCount >= 2 || tools.length >= 7) return 'deep';
 return 'fast';
 }

 function getSuggestions(content: string): string[] {
 const lower = content.toLowerCase();
 if (lower.includes('mrr') || lower.includes('revenue')) return ["Break down MRR by plan tier", "Show MRR trend over last 6 months", "What's our net revenue retention?"];
 if (lower.includes('churn') || lower.includes('cancel')) return ["What are the top cancellation reasons?", "Which customers are at risk?", "Compare churn rates across plans"];
 return ["Show me a summary of all my data", "What trends do you see?", "Create a report"];
 }

 onMount(async () => {
 sessionId = localStorage.getItem('dash_super_session') || generateSessionId();
 localStorage.setItem('dash_super_session', sessionId);
 sessionStartTime = getTimestamp();
 textareaEl?.focus();
 loadProjects();
 loadSessions();
 // Fetch current user for hero greeting
 try {
 const res = await fetch('/api/me', { headers: _headers() });
 if (res.ok) {
 const d = await res.json();
 currentUser = d.name || d.full_name || d.username || d.display_name || '';
 }
 } catch {}
 if (!currentUser) {
 try {
 currentUser = localStorage.getItem('dash_user') || localStorage.getItem('dash_username') || localStorage.getItem('dash_last_username') || '';
 } catch {}
 }
 // Listen for sidebar new-chat / open-session events (global scope)
 const onNewChat = () => { newChat(); };
 const onOpenSession = async (e: any) => {
 try {
 const sid = e?.detail?.sid;
 if (!sid || e?.detail?.scope !== 'global') return;
 // Cancel any in-flight session-load from a prior rapid switch
 if (openSessionAbort) openSessionAbort.abort();
 openSessionAbort = new AbortController();
 sessionId = sid;
 localStorage.setItem('dash_super_session', sid);
 messages = [];
 try {
 const t = localStorage.getItem('dash_token') || '';
 const r = await fetch(`/api/sessions/${sid}/messages`, {
 headers: { Authorization: `Bearer ${t}` },
 signal: openSessionAbort.signal
 });
 if (r.ok) {
 const d = await r.json();
 const loaded = d?.messages || [];
 if (loaded.length > 0) {
 messages = loaded.map((m: any) => ({
 role: m.role,
 content: m.role === 'user' ? cleanUserMessageInline(m.content || '') : (m.content || ''),
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
 } catch {}
 };
 window.addEventListener('dash-newchat-global', onNewChat);
 window.addEventListener('dash-open-session', onOpenSession);
 // Cleanup on destroy via returned function (Svelte onMount supports this)
 return () => {
 window.removeEventListener('dash-newchat-global', onNewChat);
 window.removeEventListener('dash-open-session', onOpenSession);
 };
 });

 onDestroy(() => {
 if (openSessionAbort) openSessionAbort.abort();
 });

 function newChat() {
 messages = [];
 sessionId = generateSessionId();
 localStorage.setItem('dash_super_session', sessionId);
 sessionStartTime = getTimestamp();
 textareaEl?.focus();
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

 const DEEP_KEYWORDS = /\b(why|compare|explain|suggest|recommend|correlate|analyze|break down|what should|how can|investigate|diagnose|root cause)\b/i;

 function isComplexQuery(text: string): boolean {
 if (DEEP_KEYWORDS.test(text)) return true;
 if ((text.match(/\band\b/gi) || []).length >= 2) return true;
 if (text.split('?').length > 2) return true;
 return false;
 }

 // Inline action toast
 let actionFlash = $state<{ kind: 'ok' | 'warn' | 'err'; text: string } | null>(null);
 function _flash(kind: 'ok' | 'warn' | 'err', text: string) {
 actionFlash = { kind, text };
 setTimeout(() => { actionFlash = null; }, 2400);
 }
 function _lastAssistant() {
 for (let i = messages.length - 1; i >= 0; i--) {
 if (messages[i]?.role === 'assistant') return messages[i];
 }
 return null;
 }

 async function handleAction(act: any, arg?: any) {
 if (typeof act === 'string') {
 const a = act;
 // Related-question chip — re-ask via send().
 if (a === 'related' || a.startsWith('related:')) {
 const q = (typeof arg === 'string' && arg.trim()) ? arg.trim() : a.slice('related:'.length).trim();
 if (q) { send(q); return; }
 return;
 }
 const last = _lastAssistant();
 if (a === 'copy' && last) {
 try { await navigator.clipboard.writeText(last.content || ''); _flash('ok', '✓ Copied'); }
 catch { _flash('err', 'Copy failed'); }
 return;
 }
 if (a === 'save' || a === 'pin' || a === 'csv' || a === 'excel' || a === 'share' || a === 'email') {
 // Dash Agent (cross-project) has no fixed project slug — most actions need a project.
 _flash('warn', `"${a}" works on project chat — switch to a project to save/pin/export.`);
 return;
 }
 if (a.startsWith('save_decision:')) {
 _flash('warn', 'Decision diary works on project chat.');
 return;
 }
 console.log('[action] unhandled string:', a);
 _flash('warn', `Action "${a}" not implemented yet`);
 return;
 }

 const label = (act?.label || '').trim();
 const type = (act?.type || '').toLowerCase();
 if (!label) return;
 switch (type) {
 case 'investigate':
 send(`Investigate: ${label}`); return;
 case 'run_analysis':
 send(`Run deeper analysis on: ${label}`); return;
 case 'create_campaign':
 // Dash Agent chat has no fixed project slug; if act.param looks like a slug, route there.
 if (act?.param && /^[a-z0-9_-]+$/i.test(act.param)) {
 try { goto(`/project/${act.param}/campaigns?prefill=${encodeURIComponent(label)}`); return; } catch {}
 }
 send(`Create a campaign for: ${label}`); return;
 case 'train_model':
 send(`Train an ML model for: ${label}`); return;
 case 'drill_down':
 send(`Drill into: ${label}`); return;
 default:
 send(label);
 }
 }

 async function send(text?: string) {
 let msg = (text || inputText).trim();
 if (!msg || isStreaming) return;
 let forcedReasoning = '';
 if (msg.startsWith('/deep ')) {
  forcedReasoning = 'deep';
  msg = msg.slice(6).trim();
 } else if (msg.startsWith('/quick ')) {
  forcedReasoning = 'quick';
  msg = msg.slice(7).trim();
 }
 if (!msg) return;
 inputText = '';
 if (textareaEl) textareaEl.style.height = 'auto';

 messages = [...messages, { role: 'user', content: msg, timestamp: getTimestamp() }];
 isStreaming = true;
 registerSession(msg);
 await scrollToBottom();

 messages = [...messages, { role: 'assistant', content: '', timestamp: '', status: 'streaming', toolCalls: [], workflowExpanded: true, trace: [], traceStart: Date.now(), traceLive: true }];
 await scrollToBottom();

 try {
 abortController = new AbortController();

 // My Agent path — personal user-scoped agent w/ memory across all projects
 if (chatMode === 'my_agent') {
 try {
 const body: any = { message: msg, session_id: sessionId, stream: true };
 if (fanout && fanout !== 1) body.fanout = fanout; // TODO backend wiring: param best-effort
 const url = `/api/agents/me/chat${fanout && fanout !== 1 ? `?fanout=${fanout}` : ''}`;
 const myRes = await fetch(url, {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json', Accept: 'text/event-stream' },
 body: JSON.stringify(body),
 signal: abortController.signal,
 });
 if (!myRes.ok) {
 const errText = await myRes.text();
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 messages = [...messages.slice(0, -1), { ...last, content: `My Agent error: ${errText || myRes.status}`, timestamp: getTimestamp(), status: 'error', traceLive: false, traceDoneAt: Date.now() }];
 }
 isStreaming = false;
 return;
 }
 const myReader = myRes.body?.getReader();
 if (!myReader) { isStreaming = false; return; }
 const myDecoder = new TextDecoder();
 let myBuf = '';
 let myAccum = '';
 while (true) {
 const { done, value } = await myReader.read();
 if (done) break;
 myBuf += myDecoder.decode(value, { stream: true });
 const lines = myBuf.split('\n');
 myBuf = lines.pop() || '';
 for (const line of lines) {
 if (!line.startsWith('data: ')) continue;
 const raw = line.slice(6).trim();
 if (!raw || raw === '[DONE]') continue;
 let token = raw;
 try {
 const parsed = JSON.parse(raw);
 token = parsed.token ?? parsed.content ?? parsed.delta ?? (typeof parsed === 'string' ? parsed : '');
 } catch {
 // raw text fragment
 }
 if (!token) continue;
 myAccum += token;
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 messages = [...messages.slice(0, -1), { ...last, content: myAccum, status: 'streaming' }];
 }
 }
 }
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 messages = [...messages.slice(0, -1), { ...last, content: myAccum || last.content, timestamp: getTimestamp(), status: 'done', traceLive: false, traceDoneAt: Date.now() }];
 }
 await scrollToBottom();
 } catch (e: any) {
 if (e?.name !== 'AbortError') {
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 messages = [...messages.slice(0, -1), { ...last, content: `My Agent error: ${String(e?.message || e)}`, timestamp: getTimestamp(), status: 'error', traceLive: false, traceDoneAt: Date.now() }];
 }
 }
 } finally {
 isStreaming = false;
 }
 return;
 }

   const onToken = (token: string) => {
    const last = messages[messages.length - 1];
    if (last?.role === 'assistant') {
     messages = [...messages.slice(0, -1), { ...last, content: last.content + token }];
    }
    scrollToBottom();
   };

   // Global super-chat routes through the SAME api.ts sendMessage transport
   // the project page uses (no slug -> /api/super-chat). Single parser keeps
   // trace/usage/tool handling identical across both chat surfaces.
   await sendMessage(
    msg, sessionId,
    onToken,
    () => {
     const last = messages[messages.length - 1];
     if (last?.role === 'assistant') {
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

     const routedSlug = messages[messages.length - 1]?.routing?.slug || (selectedMode !== 'auto' ? selectedMode : projects[0]?.slug);
     const lastUserMsg = msg;
     const lastAssistantMsg = messages[messages.length - 1]?.content || '';
     if (routedSlug && lastUserMsg && lastAssistantMsg) {
      fetch(`/api/projects/${routedSlug}/extract-context`, {
       method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
       body: JSON.stringify({ question: lastUserMsg, answer: lastAssistantMsg, session_id: sessionId })
      }).then(r => r.json()).then(d => {
       const cur = messages[messages.length - 1];
       if (!cur || cur.role !== 'assistant') return;
       const autoSaved = d.auto_saved_with_scores || d.auto_saved?.map((f: string) => ({fact: f, score: 90})) || [];
       const needsApproval = d.facts_with_scores || d.facts?.map((f: string) => ({fact: f, score: 40})) || [];
       if (autoSaved.length > 0 || needsApproval.length > 0) {
        messages = [...messages.slice(0, -1), {
         ...cur,
         proposedLearnings: needsApproval.length > 0 ? d.facts : undefined,
         proposedLearningsWithScores: needsApproval.length > 0 ? needsApproval : undefined,
         autoSavedLearnings: autoSaved.length > 0 ? d.auto_saved : undefined,
         autoSavedWithScores: autoSaved.length > 0 ? autoSaved : undefined,
        }];
       }
      }).catch(() => {});

      setTimeout(async () => {
       try {
        const res = await fetch(`/api/projects/${routedSlug}/scores/latest?session_id=${sessionId}`, { headers: _headers() });
        if (res.ok) {
         const d = await res.json();
         if (d.score) {
          const cur = messages[messages.length - 1];
          if (cur?.role === 'assistant') {
           messages = [...messages.slice(0, -1), { ...cur, qualityScore: d.score }];
          }
         }
        }
       } catch {}
      }, 5000);
     }
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
      const isAgentDoneMarker = tool.status === 'done' && tool.agentName && tool.name === `${tool.agentName} agent`;
      const idx = existing.findIndex(t =>
       t.name === tool.name &&
       (t.agentName || '') === (tool.agentName || '') &&
       t.status === 'running'
      );
      let updated: ToolCall[];
      if (idx >= 0 && tool.status === 'done') {
       updated = [...existing];
       updated[idx] = { ...updated[idx], status: 'done', duration: tool.duration, sqlQuery: tool.sqlQuery || updated[idx].sqlQuery };
      } else if (tool.status === 'running') {
       updated = [...existing, tool];
      } else if (tool.status === 'done') {
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
      const sqls = last.sqlQueries || [];
      if (tool.sqlQuery && !sqls.includes(tool.sqlQuery)) sqls.push(tool.sqlQuery);
      messages = [...messages.slice(0, -1), { ...last, toolCalls: updated, sqlQueries: sqls }];
     }
     scrollToBottom();
    },
    undefined,
    forcedReasoning || '',
    '',
    abortController.signal,
    (item: TraceItem) => {
     const last = messages[messages.length - 1];
     if (!last || last.role !== 'assistant') return;
     const prev = Array.isArray(last.trace) ? last.trace : [];
     let next: TraceItem[];
     if (item.kind === 'tool') {
      const idx = prev.findIndex((t) => t.kind === 'tool' && t.id === item.id);
      if (idx >= 0) { next = [...prev]; next[idx] = { ...next[idx], ...item }; }
      else { next = [...prev, item]; }
     } else {
      next = [...prev, item];
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
    selectedMode,
    (r: unknown) => {
     const last = messages[messages.length - 1];
     if (last?.role === 'assistant') {
      messages = [...messages.slice(0, -1), { ...last, routing: r as RoutingInfo }];
     }
    }
   );
 } catch (err) {
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 const errTrace = (Array.isArray(last.trace) ? last.trace : []).map((t) => (t.kind === 'tool' && t.status === 'run') ? { ...t, status: 'done' as const } : t);
 messages = [...messages.slice(0, -1), { ...last, content: `Error: ${err instanceof Error ? err.message : 'Connection failed'}`, timestamp: getTimestamp(), status: 'error', trace: errTrace, traceLive: false, traceDoneAt: Date.now() }];
 }
 isStreaming = false;
 scrollToBottom();
 }
 }

 function handleKeydown(e: KeyboardEvent) {
 if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
 }

 function renderStars(score: number | undefined): string {
 if (!score) return '';
 return ''.repeat(score) + ''.repeat(5 - score);
 }

 // parseClarify, generateChartCaption, formatCell are imported from $lib/chat/*
 // See frontend/CHAT_RENDERER.md §6 for the single-renderer rule.

 function autoResize() {
 if (textareaEl) {
 textareaEl.style.height = 'auto';
 textareaEl.style.height = Math.min(textareaEl.scrollHeight, 240) + 'px';
 }
 }

 // Greeting + user display
 function getGreeting(): string {
 const h = new Date().getHours();
 if (h < 12) return 'Morning';
 if (h < 18) return 'Afternoon';
 return 'Evening';
 }
 const greeting = $derived(getGreeting());
 let currentUser = $state('');
 const userDisplay = $derived(currentUser ? currentUser : '');

 // Composer focus state
 let inputFocused = $state(false);

 let abortController: AbortController | null = null;

 // Slide Agent (presentation) state
 let slidesPanelOpen = $state(false);
 let slidesData = $state<any[]>([]);
 let slidesThinking = $state<any>(null);
 let slidesLoading = $state(false);
 let slidesProgress = $state('');
 let currentSlide = $state(0);
 let showSaveModal = $state(false);
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

 async function confirmWorkflowSave() {
 const steps = wfSaveSteps.filter(s => s.checked).map(s => ({ type: 'query', title: s.question.slice(0, 60), question: s.question }));
 if (!wfSaveName.trim() || steps.length === 0) return;
 const slug = selectedMode !== 'auto' ? selectedMode : (messages.find(m => m.routing?.slug)?.routing?.slug || '');
 if (!slug) return;
 try {
 await fetch(`/api/projects/${slug}/workflows-db`, {
 method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ name: wfSaveName.trim(), description: wfSaveDesc.trim(), steps, source: 'user' })
 });
 showWorkflowSaveModal = false;
 loadAllWorkflows();
 } catch {}
 }
 let saveTitle = $state('');
 let pptxSteps = $state<{label: string; status: 'pending'|'active'|'done'|'error'}[]>([]);
 let pptxGenerating = $state(false);
 let pptxSavedVersion = $state(0);

 // Schedule analysis modal ( SCHEDULE button)
 // Metric Fix Modal (A3+A6) — chat page
 let chatMetricFixOpen = $state(false);
 let chatMetricFixQuestion = $state('');
 let chatMetricFixSql = $state('');
 let chatMetricFixSlug = $state('');

 let scheduleOpen = $state(false);
 let scheduleSlug = $state('');
 let scheduleMsgId = $state<string | undefined>(undefined);
 let scheduleInitialName = $state('');
 let scheduleInitialDescription = $state('');
 let scheduleInitialSteps = $state<{ kind: 'sql' | 'agent'; sql?: string; agent?: string; prompt?: string }[]>([]);
 let scheduleToast = $state<{ wfId: number; slug: string } | null>(null);

 function extractStepsFromMessageChat(msg: any): { kind: 'sql' | 'agent'; sql?: string; agent?: string; prompt?: string }[] {
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

 function openScheduleModalChat(msgIndex: number) {
 const msg: any = messages[msgIndex];
 if (!msg) return;
 const slug = msg?.routing?.slug || (selectedMode !== 'auto' ? selectedMode : (projects[0]?.slug || ''));
 if (!slug) return;
 scheduleSlug = slug;
 let lastUser = '';
 for (let k = msgIndex - 1; k >= 0; k--) {
 if (messages[k]?.role === 'user') { lastUser = messages[k].content || ''; break; }
 }
 scheduleInitialName = (lastUser || 'Scheduled analysis').slice(0, 80);
 scheduleInitialDescription = (msg.content || '').slice(0, 200);
 scheduleInitialSteps = extractStepsFromMessageChat(msg);
 scheduleMsgId = msg.id ? String(msg.id) : undefined;
 scheduleToast = null;
 scheduleOpen = true;
 }

 // PIN modal
 let showPinModal = $state(false);
 let pinModalData = $state<{msgIndex: number; tables: any[]; content: string} | null>(null);
 let pinDashboards = $state<any[]>([]);
 let pinSelectedDash = $state<number | null>(null);
 let pinNewDashName = $state('');
 let pinWidgetTitle = $state('');
 let pinProjectSlug = $state('');

 async function openPinModal(msgIndex: number, tables: any[], content: string, projectSlug: string) {
 pinModalData = { msgIndex, tables, content };
 pinWidgetTitle = tables?.[0]?.headers?.join(' / ') || content.slice(0, 50);
 pinProjectSlug = projectSlug || '';
 showPinModal = true;
 pinDashboards = [];
 if (pinProjectSlug) {
 try {
 const res = await fetch(`/api/projects/${pinProjectSlug}/dashboards`, { headers: _headers() });
 if (res.ok) { const d = await res.json(); pinDashboards = d.dashboards || []; }
 } catch {}
 }
 pinSelectedDash = pinDashboards.length > 0 ? pinDashboards[0].id : null;
 }

 async function confirmPin() {
 if (!pinModalData || !pinProjectSlug) return;
 const { tables, content } = pinModalData;
 let dashId = pinSelectedDash;
 if (!dashId && pinNewDashName.trim()) {
 try {
 const res = await fetch(`/api/projects/${pinProjectSlug}/dashboards?name=${encodeURIComponent(pinNewDashName.trim())}`, { method: 'POST', headers: _headers() });
 if (res.ok) { const d = await res.json(); dashId = d.id; }
 } catch {}
 }
 if (!dashId) return;
 const hasTable = tables?.length > 0 && tables[0].headers?.length > 0;
 const widget = hasTable
 ? { type: 'chart', title: pinWidgetTitle, chartType: 'bar', headers: tables[0].headers, rows: tables[0].rows }
 : { type: 'text', title: pinWidgetTitle, content, full: true };
 await fetch(`/api/projects/${pinProjectSlug}/dashboards/${dashId}/widgets`, {
 method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify(widget),
 });
 showPinModal = false;
 pinModalData = null;
 pinNewDashName = '';
 }

 function openDashboardGenerator() {
   // Dash Agent (/chat) is cross-project — Deep Dash 9-stage needs a specific
   // project_slug for schema RAG. Route to last-routed project's chat and let
   // user click D there (full artifact panel + SSE 9-stage stream).
   const routedSlug = messages.find(m => m.routing?.slug)?.routing?.slug
     || (selectedMode !== 'auto' ? selectedMode : projects[0]?.slug);
   if (routedSlug) {
     goto(`/ui/project/${routedSlug}?build_dash=1`);
   } else {
     goto('/ui/dashboard');
   }
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

 const agentName = selectedMode !== 'auto' ? getSelectedLabel() : ($brand.name + ' Agent');

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
 title: agentName + ' Analysis',
 agent_name: agentName
 })
 });

 if (res.ok) {
 const data = await res.json();
 slidesThinking = data.thinking;
 slidesData = data.slides || [];
 updateStep(1, 'done');
 updateStep(2, 'done', `Planned ${slidesData.length} slides`);
 updateStep(3, 'done');
 } else {
 updateStep(2, 'error', 'Failed to generate slides');
 }
 } catch (e) {
 const failIdx = pptxSteps.findIndex(s => s.status === 'active');
 if (failIdx >= 0) pptxSteps = pptxSteps.map((s, i) => i === failIdx ? { ...s, status: 'error' as const, label: 'Error' } : s);
 }
 slidesLoading = false;
 pptxGenerating = false;
 if (slidesData.length > 0) {
 setTimeout(() => { pptxSteps = []; }, 1500);
 }
 }

 async function savePresentation() {
 if (slidesData.length === 0) return;
 const agentName = selectedMode !== 'auto' ? getSelectedLabel() : ($brand.name + ' Agent');
 saveTitle = agentName + ' Analysis';
 showSaveModal = true;
 }

 async function confirmSave() {
 if (!saveTitle.trim()) return;
 const projSlug = selectedMode !== 'auto' ? selectedMode : 'dash_agent';
 try {
 const res = await fetch('/api/export/presentations', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 project_slug: projSlug,
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
 const agentName = selectedMode !== 'auto' ? getSelectedLabel() : ($brand.name + ' Agent');
 const agentTitle = agentName + ' Analysis';
 const projSlug = selectedMode !== 'auto' ? selectedMode : 'dash_agent';

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
 await new Promise(r => setTimeout(r, 300));
 updateStep(0, 'done');
 updateStep(1, 'active');

 await new Promise(r => setTimeout(r, 200));
 updateStep(2, 'active');
 const agentRes = await fetch('/api/export/slides-agent', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 messages: messages.map(m => ({ role: m.role, content: m.content })),
 title: agentTitle,
 agent_name: agentName
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

 await new Promise(r => setTimeout(r, 300));
 updateStep(5, 'active');
 const saveRes = await fetch('/api/export/presentations', {
 method: 'POST',
 headers: { ..._headers(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 project_slug: projSlug,
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
 a.download = `${agentName}-v${pptxSavedVersion}.pptx`;
 a.click();
 URL.revokeObjectURL(url);
 updateStep(6, 'done', `Downloaded ${agentName}-v${pptxSavedVersion}.pptx`);
 } else {
 updateStep(6, 'error', 'Download failed');
 }
 }
 } catch (e) {
 const failIdx = pptxSteps.findIndex(s => s.status === 'active');
 if (failIdx >= 0) pptxSteps = pptxSteps.map((s, i) => i === failIdx ? { ...s, status: 'error' as const, label: 'Failed' } : s);
 }
 pptxGenerating = false;
 }

 function downloadHTML() {
 if (slidesData.length === 0) return;
 const agentName = selectedMode !== 'auto' ? getSelectedLabel() : ($brand.name + ' Agent');
 const title = agentName + ' Analysis';
 const date = new Date().toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'});
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
 <div style="font-size:11px;color:#999;margin-top:40px;text-transform:uppercase;letter-spacing:0.1em;">${agentName} &middot; ${date}</div>
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
 const headers = (s.table.headers||[]).map((h: string) => `<th style="padding:8px 12px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#666;border-bottom:2px solid #1a1a1a;">${h}</th>`).join('');
 const rows = (s.table.rows||[]).slice(0,12).map((r: string[], ri: number) => `<tr style="background:${ri%2===0?'#fafaf8':'#fff'};">${r.map((c: string) => `<td style="padding:7px 12px;font-size:11px;border-bottom:1px solid #eee;">${c}</td>`).join('')}</tr>`).join('');
 content = `<div style="font-size:19px;font-weight:800;margin-bottom:20px;">${sTitle}</div>
 <div style="width:100%;height:2px;background:#1a1a1a;margin-bottom:20px;"></div>
 <table style="width:100%;border-collapse:collapse;"><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>${bullets}${actionLine}`;
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
 a.download = `${agentName}-slides.html`;
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

 function stopStreaming() {
 if (abortController) {
 abortController.abort();
 abortController = null;
 }
 isStreaming = false;
 const last = messages[messages.length - 1];
 if (last?.role === 'assistant') {
 const finalizedTools = (last.toolCalls || []).map(t => t.status === 'running' ? { ...t, status: 'done' as const } : t);
 const finalizedTrace = (Array.isArray(last.trace) ? last.trace : []).map((t) => (t.kind === 'tool' && t.status === 'run') ? { ...t, status: 'done' as const } : t);
 messages = [...messages.slice(0, -1), { ...last, status: 'done', toolCalls: finalizedTools, trace: finalizedTrace, traceLive: false, traceDoneAt: Date.now(), timestamp: getTimestamp() }];
 }
 textareaEl?.focus();
 }

 function selectMode(mode: string) {
 selectedMode = mode;
 dropdownOpen = false;
 textareaEl?.focus();
 }

 // Close dropdown on click outside
 function handleWindowClick(e: MouseEvent) {
 const target = e.target as HTMLElement;
 if (!target.closest('.mode-selector')) {
 dropdownOpen = false;
 workflowPickerOpen = false;
 }
 }
</script>

<svelte:window onclick={handleWindowClick} />

{#if actionFlash}
  <div style="position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); z-index: 9999; padding: 10px 18px; font-size: 13px; font-weight: 600; border-radius: 6px; box-shadow: 0 4px 16px rgba(0,0,0,0.25); background: {actionFlash.kind === 'ok' ? 'var(--pw-accent, #c96342)' : actionFlash.kind === 'warn' ? '#b87c0a' : '#b03030'}; color: #fff;">
    {actionFlash.text}
  </div>
{/if}

<div class="flex h-full">
  <!-- Main chat (global sidebar is owned by +layout.svelte) -->
  <div class="flex flex-col" style="flex: 1; min-width: 0;">

  <div class="flex-1 overflow-y-auto" bind:this={messagesEl} style="padding: 20px 20px 16px 20px; background: #ffffff;">
    <!-- Hybrid Claude-style: 880px reading column on white bg -->
    <div style="max-width: 880px; margin: 0 auto;">

      {#if sessionStartTime}
        <div class="flex justify-center mb-6 animate-fade-up">
          <div class="tag-label">
            {$brand.name} Agent &middot; {sessionStartTime}
          </div>
        </div>
      {/if}

      {#if messages.length === 0}
        <div class="hero animate-fade-up">
          <h1 class="hero-title"><span class="ast"><Icon name="star" size={14} /></span>  {greeting}{userDisplay ? `, ${userDisplay}` : ''}</h1>
          <p class="hero-sub">
            Ask anything across your agents
            {#if chatMode === 'smart_router'}
              <span class="hero-mode-hint">· Smart Router will pick the right project</span>
            {:else}
              <span class="hero-mode-hint">· <Icon name="dna" size={14} /> Your personal agent w/ memory across all your work</span>
            {/if}
          </p>
          {#if chatMode === 'my_agent'}
            <div style="margin: 20px auto 0; max-width: 640px;">
              <AgentRecommendations />
            </div>
          {/if}
        </div>
      {:else}
        <div class="flex flex-col gap-5">
          <ChatMessageList
            messages={messages}
            isStreaming={isStreaming}
            routeLabel={selectedMode === "auto" ? "auto" : "pinned"}
            agentName={$brand.name + " Agent"}
            copiedIndex={copiedIndex}
            updateMessage={(idx, patch) => { messages = [...messages.slice(0, idx), { ...messages[idx], ...patch }, ...messages.slice(idx + 1)]; }}
            onSend={(t) => send(t)}
            onAction={(act, arg) => handleAction(act, arg)}
            onCopy={(idx) => copyMessage(idx)}
            onFeedback={async (idx, rating) => {
              const m = messages[idx]; if (!m) return;
              const rSlug = m.routing?.slug || (selectedMode !== "auto" ? selectedMode : projects[0]?.slug);
              if (!rSlug) return;
              const q = idx > 0 ? messages[idx-1]?.content : "";
              const firstSql = Array.isArray(m.sqlQueries) && m.sqlQueries.length ? m.sqlQueries[0] : "";
              const fbRes = await fetch(`/api/projects/${rSlug}/feedback`, { method: "POST", headers: { ..._headers(), "Content-Type": "application/json" }, body: JSON.stringify({ question: q, answer: m.content, rating, sql: firstSql }) });
              if (rating === "up" && m.sqlQueries?.length) { for (const sql of m.sqlQueries) { await fetch(`/api/projects/${rSlug}/save-query-pattern`, { method: "POST", headers: { ..._headers(), "Content-Type": "application/json" }, body: JSON.stringify({ question: q, sql }) }); } }
              try { const fbd = await fbRes.json(); if (fbd?.promoted?.total_goldens) { console.log(`[golden] promoted to corpus (total: ${fbd.promoted.total_goldens})`); } } catch {}
            }}
            onSaveMemory={(idx) => {
              const m = messages[idx]; if (!m) return;
              const fact = prompt("Save a fact the agent should remember:", "");
              const slug = m.routing?.slug || (selectedMode !== "auto" ? selectedMode : projects[0]?.slug);
              if (fact && slug) fetch(`/api/projects/${slug}/memories`, { method: "POST", headers: { ..._headers(), "Content-Type": "application/json" }, body: JSON.stringify({ fact, scope: "project" }) });
            }}
            onExportCsv={(idx, tables) => {
              if (!tables[0]) return;
              const csv = tableToCsv(tables[0]);
              const blob = new Blob([csv], { type: "text/csv" });
              const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `dash-export-${Date.now()}.csv`; a.click(); URL.revokeObjectURL(url);
            }}
            onExportPdf={async (idx) => {
              const m = messages[idx]; if (!m) return;
              try {
                const res = await fetch("/api/export/pdf", { method: "POST", headers: { ..._headers(), "Content-Type": "application/json" }, body: JSON.stringify({ content: m.content, title: $brand.name + " Agent Report" }) });
                if (res.ok) { const blob = await res.blob(); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `dash-report-${Date.now()}.pdf`; a.click(); URL.revokeObjectURL(url); }
              } catch {}
            }}
            onPin={(idx, tables) => {
              const m = messages[idx]; if (!m) return;
              const slug = m.routing?.slug || (selectedMode !== "auto" ? selectedMode : "");
              if (slug) openPinModal(idx, tables, m.content, slug);
            }}
            onSchedule={(idx) => openScheduleModalChat(idx)}
            onCopySql={(sql) => navigator.clipboard.writeText(sql)}
          >
            {#snippet analysisExtras({ msg, index: i })}
              {#if Array.isArray((msg as any).trace) && (msg as any).trace.length}
                <ReasoningTrace items={(msg as any).trace} usage={(msg as any).usage ?? null} mode={((msg as any).content?.match(/\[MODE:(\w+)\]/)?.[1]) || (msg as any).reasoningUsed || (((msg as any).content?.length || 0) > 800 ? 'deep' : 'fast')} analysis={(((msg as any).content?.match(/\[ANALYSIS:([^\]]+)\]/)?.[1]) || '').replace(/,/g, ' · ')} elapsedMs={((msg as any).traceDoneAt ?? Date.now()) - ((msg as any).traceStart ?? Date.now())} live={(msg as any).traceLive === true} />
              {/if}
            {/snippet}
            {#snippet messageFooter({ msg, index: i })}
              {#if msg.status === "done" && msg.role === "assistant"}
                {#if true}
                  {@const _cmSqls = Array.isArray((msg as any).sqlQueries) ? (msg as any).sqlQueries : []}
                  {@const _cmSql = _cmSqls[0] || ''}
                  <div style="margin-top: 6px;">
                    <button
                      onclick={() => {
                        let lastUserQ = '';
                        for (let k = i - 1; k >= 0; k--) {
                          if (messages[k]?.role === 'user') { lastUserQ = messages[k].content || ''; break; }
                        }
                        const rSlug = (msg as any).routing?.slug || (selectedMode !== 'auto' ? selectedMode : (Array.isArray(projects) && projects[0]?.slug) || '');
                        chatMetricFixQuestion = lastUserQ;
                        chatMetricFixSql = _cmSql;
                        chatMetricFixSlug = rSlug;
                        chatMetricFixOpen = true;
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

  <!-- Composer card (Claude-style) -->
  <div class="input-bar shrink-0">
    <div style="max-width: none; margin: 0; padding: 16px 20px 20px;">
      <div class="composer-card" class:focused={inputFocused}>
        <div class="composer-row">
          <!-- Segment 1: Filter pills -->
          <div class="composer-seg composer-filters">
            <!-- Workflow selector -->
            <div class="mode-selector">
              <button class="cmp-chip" onclick={() => { if (allWorkflows.length === 0 && projects.length > 0) loadAllWorkflows(); closeOtherDropdowns('workflow'); workflowPickerOpen = !workflowPickerOpen; }} disabled={isStreaming} title="Workflow">
            ⊞ Flow <span class="caret">▾</span>
          </button>
          {#if workflowPickerOpen}
            <div class="mode-dropdown" style="min-width: 280px;">
              <!-- Save current chat as workflow -->
              {#if messages.filter(m => m.role === 'user').length >= 1}
                <button class="mode-dropdown-item" onclick={() => { workflowPickerOpen = false; openWorkflowSave(); }} style="border-bottom: 2px solid var(--pw-muted);">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#7c3aed" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
                  <div>
                    <div style="font-weight: 900; color: #7c3aed;">SAVE CURRENT AS WORKFLOW</div>
                    <span class="mode-dropdown-label">{messages.filter(m => m.role === 'user').length} steps from this chat</span>
                  </div>
                </button>
              {/if}
              {#if allWorkflows.length === 0}
                <div style="padding: 14px; font-size: 11px; color: var(--pw-muted); text-align: center;">No workflows yet. Train your projects to auto-generate workflows.</div>
              {:else}
                {#each allWorkflows as wf}
                  <button class="mode-dropdown-item" onclick={() => runWorkflowChat(wf)}>
                    <div>
                      <div style="font-weight: 900;">{wf.name}</div>
                      <span class="mode-dropdown-label">{wf.project} &middot; {wf.steps?.length || 0} steps</span>
                    </div>
                  </button>
                {/each}
              {/if}
            </div>
          {/if}
        </div>

        <!-- Reasoning + analysis selectors removed (use /deep or /quick prefix) -->

        <!-- Mode selector (project routing) -->
        <div class="mode-selector">
          <button class="cmp-chip" onclick={() => { closeOtherDropdowns('agents'); dropdownOpen = !dropdownOpen; }} title={selectedMode === 'auto' ? 'All Agents' : getSelectedLabel()}>
            ⊞ {selectedMode === 'auto' ? `${projects.length} Agents` : getSelectedLabel()} <span class="caret">▾</span>
          </button>

          {#if dropdownOpen}
            <div class="mode-dropdown">
              <button class="mode-dropdown-item" class:mode-dropdown-item-active={selectedMode === 'auto'} onclick={() => selectMode('auto')}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                <div>
                  <div>AUTO</div>
                  <span class="mode-dropdown-label">Routes to the best agent automatically</span>
                </div>
              </button>

              {#if projects.length > 0}
                <div class="mode-dropdown-divider">YOUR AGENTS</div>
              {/if}

              {#each projects.filter(p => p.owned) as proj}
                <button class="mode-dropdown-item" class:mode-dropdown-item-active={selectedMode === proj.slug} onclick={() => selectMode(proj.slug)}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="0"/><path d="M12 2v4"/><circle cx="12" cy="7" r="1"/></svg>
                  <div>
                    <div>{proj.agent_name}</div>
                    <span class="mode-dropdown-label">{proj.name}</span>
                  </div>
                </button>
              {/each}

              {#if projects.filter(p => !p.owned).length > 0}
                <div class="mode-dropdown-divider">SHARED WITH ME</div>
                {#each projects.filter(p => !p.owned) as proj}
                  <button class="mode-dropdown-item" class:mode-dropdown-item-active={selectedMode === proj.slug} onclick={() => selectMode(proj.slug)}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="0"/><path d="M12 2v4"/><circle cx="12" cy="7" r="1"/></svg>
                    <div>
                      <div>{proj.agent_name}</div>
                      <span class="mode-dropdown-label">{proj.name} (shared)</span>
                    </div>
                  </button>
                {/each}
              {/if}
            </div>
          {/if}
        </div>

          </div>

          <!-- Segment 2: Mode chips (Smart Router / My Agent) -->
          <div class="composer-seg composer-mode">
            <button
              class:active={chatMode === 'smart_router'}
              onclick={() => { chatMode = 'smart_router'; }}
              disabled={isStreaming}
              title="Smart Router — picks one best project"
            ><Icon name="compass" size={14} /> Router</button>
            <button
              class:active={chatMode === 'my_agent'}
              onclick={activateMyAgent}
              disabled={isStreaming || myAgentBootstrapping}
              title="My Agent — personal agent w/ memory across all your projects"
            ><Icon name="dna" size={14} /> My Agent{myAgentBootstrapping ? '…' : ''}</button>
            {#if chatMode === 'my_agent'}
              <div class="mode-selector" style="position: relative;">
                <button onclick={() => { closeOtherDropdowns('fanout'); fanoutDropdownOpen = !fanoutDropdownOpen; }} disabled={isStreaming} title="Fan out across projects">
                  ⇉ {fanoutLabel(fanout)} <span class="caret">▾</span>
                </button>
                {#if fanoutDropdownOpen}
                  <div class="mode-dropdown" style="min-width: 200px; right: 0; left: auto;">
                    <button class="mode-dropdown-item" class:mode-dropdown-item-active={fanout === 1} onclick={() => { fanout = 1; fanoutDropdownOpen = false; }}>
                      <div><div>1 PROJECT</div><span class="mode-dropdown-label">Smart Router picks best</span></div>
                    </button>
                    <button class="mode-dropdown-item" class:mode-dropdown-item-active={fanout === 3} onclick={() => { fanout = 3; fanoutDropdownOpen = false; }}>
                      <div><div>TOP 3</div><span class="mode-dropdown-label">Parallel · synthesized answer</span></div>
                    </button>
                    <button class="mode-dropdown-item" class:mode-dropdown-item-active={fanout === 0} onclick={() => { fanout = 0; fanoutDropdownOpen = false; }}>
                      <div><div>ALL PROJECTS</div><span class="mode-dropdown-label">Federate across everything</span></div>
                    </button>
                  </div>
                {/if}
              </div>
            {/if}
          </div>

          <!-- Segment 3: Input -->
          <textarea
            bind:this={textareaEl}
            bind:value={inputText}
            onfocus={() => inputFocused = true}
            onblur={() => inputFocused = false}
            onkeydown={handleKeydown}
            oninput={autoResize}
            placeholder="Ask anything…"
            rows="1"
            disabled={isStreaming}
            class="composer-input"
          ></textarea>

          <!-- Segment 4: Send -->
          {#if isStreaming}
            <button class="composer-send" onclick={stopStreaming} title="Stop" aria-label="Stop" style="background: var(--pw-error, #dc2626);">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
            </button>
          {:else}
            <button class="composer-send" onclick={() => send()} disabled={!inputText.trim()} title="Send" aria-label="Send">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          {/if}

          <!-- Segment 5: Actions D/P/X -->
          <div class="composer-seg composer-actions">
            <button onclick={openDashboardGenerator} title="Generate dashboard from chat">D</button>
            <button onclick={async () => {
              if (messages.length < 2) return;
              const res = await fetch('/api/export/excel-from-chat', {
                method: 'POST', headers: { ..._headers(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ messages: messages.map(m => ({ role: m.role, content: m.content })), title: (selectedMode !== 'auto' ? getSelectedLabel() : ($brand.name + ' Agent')) + ' Analysis', agent_name: selectedMode !== 'auto' ? getSelectedLabel() : ($brand.name + ' Agent') })
              });
              if (res.ok) { const blob = await res.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'dash-analysis.xlsx'; a.click(); URL.revokeObjectURL(url); }
            }} disabled={messages.length < 2} title="Export Excel">X</button>
          </div>
        </div>
      </div>
      <div class="composer-hint">
        {#if chatMode === 'my_agent'}
          <Icon name="dna" size={14} /> My Agent · learns from your usage across all projects · fan-out: {fanoutLabel(fanout)}
        {:else}
          <Icon name="compass" size={14} /> Smart Router · picks the best project for each question
        {/if}
      </div>
    </div>
  </div>
</div>

<!-- Metric Fix Modal (A3+A6) — chat page -->
{#if chatMetricFixOpen && chatMetricFixSlug}
  <MetricFixModal
    slug={chatMetricFixSlug}
    question={chatMetricFixQuestion}
    sql={chatMetricFixSql}
    onclose={() => { chatMetricFixOpen = false; }}
  />
{/if}

<!-- Schedule Analysis Modal -->
<ScheduleAnalysisModal
  bind:open={scheduleOpen}
  projectSlug={scheduleSlug}
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

<!-- PIN to Dashboard Modal -->
{#if showPinModal}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div style="position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 200; display: flex; align-items: center; justify-content: center;" onclick={(e) => { if (e.target === e.currentTarget) showPinModal = false; }}>
  <div style="background: var(--pw-bg); border: 3px solid var(--pw-ink); width: 400px; box-shadow: 6px 6px 0 rgba(0,0,0,0.3);">
    <div style="padding: 10px 16px; background: #1a1a1a; color: #fff; font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; display: flex; justify-content: space-between; align-items: center;">
      <span>PIN TO DASHBOARD</span>
      <button onclick={() => showPinModal = false} style="background: none; border: none; color: #fff; cursor: pointer; font-size: 13px;"><Icon name="x" size={14} /></button>
    </div>
    <div style="padding: 16px;">
      <!-- Project info -->
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">PROJECT</div>
      <div style="padding: 6px 10px; border: 2px solid var(--pw-ink); background: var(--pw-bg-alt); font-size: 11px; margin-bottom: 12px; font-weight: 700;">{pinProjectSlug}</div>

      <!-- Widget title -->
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">WIDGET TITLE</div>
      <input type="text" bind:value={pinWidgetTitle} style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg); margin-bottom: 12px;" />

      <!-- Select dashboard -->
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 6px;">SELECT DASHBOARD</div>
      {#if pinDashboards.length > 0}
        <div style="display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; max-height: 120px; overflow-y: auto;">
          {#each pinDashboards as d}
            <button style="text-align: left; padding: 6px 10px; border: 2px solid {pinSelectedDash === d.id ? 'var(--pw-accent)' : 'var(--pw-ink)'}; background: {pinSelectedDash === d.id ? 'var(--pw-accent-soft)' : 'var(--pw-surface)'}; cursor: pointer; font-family: var(--pw-font-body);" onclick={() => { pinSelectedDash = d.id; pinNewDashName = ''; }}>
              <div style="font-size: 11px; font-weight: 900; text-transform: uppercase;">{d.name}</div>
            </button>
          {/each}
        </div>
      {/if}

      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">OR CREATE NEW</div>
      <input type="text" bind:value={pinNewDashName} placeholder="New dashboard name..." onfocus={() => { pinSelectedDash = null; }} style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg); margin-bottom: 16px;" />

      <div style="display: flex; gap: 8px; justify-content: flex-end;">
        <button onclick={() => showPinModal = false} style="padding: 8px 16px; font-size: 11px; font-weight: 900; border: 2px solid var(--pw-ink); background: none; cursor: pointer; font-family: var(--pw-font-body); text-transform: uppercase;">CANCEL</button>
        <button onclick={confirmPin} disabled={!pinSelectedDash && !pinNewDashName.trim()} style="padding: 8px 16px; font-size: 11px; font-weight: 900; border: 2px solid var(--pw-ink); background: var(--pw-ink); color: var(--pw-bg); cursor: pointer; font-family: var(--pw-font-body); text-transform: uppercase;">PIN</button>
      </div>
    </div>
  </div>
</div>
{/if}

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
      <button onclick={() => showSaveModal = false} style="background: none; border: none; color: #fff; cursor: pointer; font-size: 13px;">&#10005;</button>
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
      <button onclick={() => slidesPanelOpen = false} style="background: none; border: none; cursor: pointer; color: #fff; font-size: 14px;">&#10005;</button>
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
                <span style="color: var(--pw-accent);">&#10003;</span>
              {:else if step.status === 'active'}
                <span style="color: #F0A030;">&#9679;</span>
              {:else if step.status === 'error'}
                <span style="color: #ff4444;">&#10007;</span>
              {:else}
                <span style="color: #555;">&#9675;</span>
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
              <div style="font-size: 11px; color: #999; margin-top: 30px; text-transform: uppercase; letter-spacing: 0.1em;">{selectedMode !== 'auto' ? getSelectedLabel() : ($brand.name + ' Agent')} &middot; {new Date().toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'})}</div>
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
                    <div style="font-size: 13px; font-weight: 700; color: {kpi.change?.startsWith('+') || kpi.change?.startsWith('\u25B2') ? '#00873c' : '#d32f2f'}; margin-top: 4px;">{kpi.change}</div>
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
      <button onclick={() => currentSlide = Math.max(0, currentSlide - 1)} disabled={currentSlide === 0} style="font-size: 11px; font-weight: 900; padding: 4px 10px; border: 1px solid #ccc; background: #fff; cursor: pointer;">&#8592; PREV</button>
      <div style="display: flex; gap: 3px; align-items: center;">
        {#each slidesData as s, si}
          <button onclick={() => currentSlide = si} style="width: 28px; height: 18px; border: {si === currentSlide ? '2px solid #D24726' : '1px solid #ccc'}; background: {si === currentSlide ? '#FFF3E0' : '#fff'}; cursor: pointer; padding: 0; font-size: 7px; font-weight: 700; color: {si === currentSlide ? '#D24726' : '#999'};">{si + 1}</button>
        {/each}
      </div>
      <button onclick={() => currentSlide = Math.min(slidesData.length - 1, currentSlide + 1)} disabled={currentSlide >= slidesData.length - 1} style="font-size: 11px; font-weight: 900; padding: 4px 10px; border: 1px solid #ccc; background: #fff; cursor: pointer;">NEXT &#8594;</button>
    </div>

  {:else}
    <div style="flex: 1; display: flex; align-items: center; justify-content: center; color: #999; font-size: 11px;">No slides generated</div>
  {/if}
</div>
{/if}
</div>

<style>
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

/* Hero (empty state) */
.hero { text-align: center; padding: 80px 24px 32px; }
.hero-title {
 font-family: var(--pw-serif, 'EB Garamond', Tiempos, Georgia, serif);
 font-size: 30px;
 font-weight: 400;
 color: var(--pw-ink);
 margin: 0 0 12px;
}
.hero-title .ast { color: var(--pw-accent, #c96342); margin-right: 8px; }
.hero-sub { color: var(--pw-ink-muted, var(--pw-muted)); font-size: 13px; margin: 0; }

/* Composer card — single-row layout */
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
.composer-actions { border-left:1px solid var(--pw-border); }
.composer-actions button { padding:6px 8px; border-radius: 0; background:transparent; border:1px solid transparent; cursor:pointer; font-size:11px; font-weight:600; color:var(--pw-ink); transition:all .15s; }
.composer-actions button:hover:not(:disabled) { background:var(--pw-bg-alt); border-color:var(--pw-border); }
.composer-actions button:disabled { color:var(--pw-muted); cursor:not-allowed; }
.composer-filters button, .composer-mode > button, .composer-mode .mode-selector > button { padding:4px 8px; border-radius: 0; background:transparent; border:0; cursor:pointer; font-size:11px; font-weight:500; color:var(--pw-muted); display:inline-flex; align-items:center; gap:4px; font-family:inherit; }
.composer-filters button:hover:not(:disabled), .composer-mode > button:hover:not(:disabled), .composer-mode .mode-selector > button:hover:not(:disabled) { background:var(--pw-bg-alt); color:var(--pw-ink); }
.composer-filters button.active, .composer-mode > button.active { background:rgba(201,99,66,0.10); color:var(--pw-accent, #c96342); font-weight:600; }
.composer-filters button:disabled, .composer-mode > button:disabled { opacity:.5; cursor:not-allowed; }
.composer-filters .caret, .composer-mode .caret { font-size:10px; color:var(--pw-ink-muted, var(--pw-muted)); }
@media (max-width: 720px) {
 .composer-row { flex-wrap:wrap; }
 .composer-input { width:100%; flex-basis:100%; border-left:0; border-top:1px solid var(--pw-border); margin-top:4px; }
 .composer-card { margin:0 12px; }
}
.cmp-chip {
 display: inline-flex;
 align-items: center;
 gap: 5px;
 height: 28px;
 padding: 0 10px;
 border-radius: 0;
 border: 1px solid var(--pw-border);
 background: transparent;
 cursor: pointer;
 font: inherit;
 font-size: 11px;
 color: var(--pw-ink);
 white-space: nowrap;
}
.cmp-chip:hover:not(:disabled) {
 background: rgba(201,99,66,0.08);
 border-color: var(--pw-accent, #c96342);
 color: var(--pw-accent, #c96342);
}
.cmp-chip:disabled { opacity: 0.5; cursor: not-allowed; }
.cmp-chip .caret { font-size: 11px; color: var(--pw-ink-muted, var(--pw-muted)); }
.cmp-icon {
 width: 30px;
 height: 30px;
 border-radius: 0;
 border: 1px solid var(--pw-border);
 background: #fff;
 cursor: pointer;
 font: inherit;
 font-size: 11px;
 font-weight: 600;
 color: var(--pw-ink);
 display: flex;
 align-items: center;
 justify-content: center;
}
.cmp-icon:hover:not(:disabled) {
 border-color: var(--pw-accent, #c96342);
 color: var(--pw-accent, #c96342);
}
.cmp-icon:disabled { opacity: 0.4; cursor: not-allowed; }
.cmp-send {
 width: 32px;
 height: 32px;
 border-radius: 50%;
 border: 0;
 background: transparent;
 color: var(--pw-ink-muted, var(--pw-muted));
 cursor: pointer;
 font-size: 14px;
 display: flex;
 align-items: center;
 justify-content: center;
}
.cmp-send.ready {
 background: var(--pw-accent, #c96342);
 color: #fff;
}
.cmp-send:disabled { opacity: 0.4; cursor: not-allowed; }
.cmp-tile {
 width: 30px;
 height: 30px;
 border-radius: 0;
 border: 1px solid var(--pw-border);
 background: #fff;
 cursor: pointer;
 font: inherit;
 font-size: 11px;
 font-weight: 600;
 color: var(--pw-ink);
 display: inline-flex;
 align-items: center;
 justify-content: center;
}
.cmp-dash { color:#3a8dff; border-color:#bcdcff; font-weight:600; }
.cmp-dash:hover { background:rgba(58,141,255,0.08); }

/* Message agent footer */
.msg-meta {
 display: flex;
 align-items: center;
 gap: 8px;
 margin-bottom: 8px;
 margin-top: 4px;
}
.msg-meta .ast { color: var(--pw-accent, #c96342); }
.msg-agent {
 font-family: var(--pw-serif, 'EB Garamond', Georgia, serif);
 font-size: 14px;
 color: var(--pw-ink);
}
.msg-time { font-size: 11px; color: var(--pw-ink-muted, var(--pw-muted)); }

/* Auto-saved learnings card */
.autosave-card {
 background: #f0f9f4;
 border: 1px solid #cfe6d7;
 border-radius: 0;
 padding: 14px;
 margin-top: 10px;
}
.autosave-title {
 font-size: 12px;
 font-weight: 500;
 color: #2c6e3f;
 margin-bottom: 8px;
}
.autosave-list { display: flex; flex-direction: column; gap: 6px; }
.autosave-item {
 display: flex;
 align-items: center;
 gap: 8px;
 font-size: 12px;
 color: var(--pw-ink);
}
.autosave-item > span:first-child { flex: 1; }
.autosave-conf {
 font-size: 10px;
 padding: 2px 8px;
 border-radius: 0;
 background: rgba(0,0,0,0.04);
 color: #807a72;
 flex-shrink: 0;
}

/* Follow-up pills */
.followup-wrap { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--pw-border); }
.followup-label {
 font-size: 11px;
 color: var(--pw-ink-muted, var(--pw-muted));
 margin-bottom: 8px;
}
.followup-pills {
 display: flex;
 flex-wrap: wrap;
 gap: 8px;
}
.followup-pill {
 background: transparent;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 font-size: 12px;
 padding: 8px 14px;
 color: var(--pw-ink);
 cursor: pointer;
 font-family: inherit;
}
.followup-pill:hover:not(:disabled) {
 background: rgba(201,99,66,0.08);
 border-color: var(--pw-accent, #c96342);
}
.followup-pill:disabled { opacity: 0.5; cursor: not-allowed; }

/* Feedback row */
.fb-row {
 display: flex;
 justify-content: space-between;
 align-items: center;
 gap: 8px;
}
.fb-btn {
 width: 28px;
 height: 28px;
 border-radius: 0;
 background: transparent;
 border: 0;
 cursor: pointer;
 color: var(--pw-ink-muted, var(--pw-muted));
 display: inline-flex;
 align-items: center;
 justify-content: center;
}
.fb-btn:hover { background: var(--pw-bg-alt); color: var(--pw-ink); }

/* CLI Route card softening */
:global(.cli-terminal) {
 background: var(--pw-bg-alt) !important;
 border-radius: 0!important;
 font-family: 'JetBrains Mono', ui-monospace, monospace !important;
}
:global(.cli-terminal .cli-prompt) { color: var(--pw-accent, #c96342) !important; }
:global(.cli-terminal .cli-info) { color: #3a6fb5 !important; }

/* Chat-mode toggle (Smart Router vs My Agent) */
.mode-bar {
 display: flex;
 justify-content: space-between;
 align-items: center;
 gap: 12px;
 padding: 8px 12px 0 12px;
 border-bottom: 1px dashed var(--pw-border, #e8e3d6);
 padding-bottom: 8px;
 margin-bottom: 4px;
}
.mode-bar-left { display: inline-flex; gap: 4px; padding: 2px; border: 1px solid var(--pw-border, #e8e3d6); border-radius: 0; background: var(--pw-bg-alt, #faf6f1); }
.mode-bar-right { position: relative; }
.chatmode-group { padding: 2px; border: 1px solid var(--pw-border); border-radius: 0; background: var(--pw-bg-alt, #faf6f1); }
.chatmode-pill { border: 1px solid transparent !important; background: transparent !important; padding: 4px 10px !important; font-size: 12.5px !important; }
.chatmode-pill.chatmode-active {
 background: var(--pw-bg, #fff) !important;
 border-color: var(--pw-accent, #c96342) !important;
 color: var(--pw-accent, #c96342) !important;
 font-weight: 700;
 box-shadow: 0 1px 2px rgba(201,99,66,0.12);
}
@media (max-width: 768px) {
 .mode-bar { flex-direction: column; align-items: stretch; }
 .mode-bar-right { align-self: flex-end; }
}
.hero-mode-hint { color: var(--pw-muted, #888); font-size: 12px; margin-left: 4px; }
.composer-hint {
 text-align: center;
 font-size: 11.5px;
 color: var(--pw-muted, #888);
 margin: 8px auto 0;
 letter-spacing: 0.01em;
}
</style>
