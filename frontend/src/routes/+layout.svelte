<script lang="ts">
  import Icon from '$lib/Icon.svelte';
  import FloatingRobot from '$lib/FloatingRobot.svelte';
  import VersionBadge from '$lib/VersionBadge.svelte';
  import VersionCard from '$lib/VersionCard.svelte';
  import { versionInfo, loadVersion } from '$lib/stores/version';
 import '../app.css';
 import { page } from '$app/state';
 import { onMount } from 'svelte';
 import { goto } from '$app/navigation';
 import { getActiveScope, clearScope, type ActiveScope } from '$lib/api';
 import { brand, loadBrand, applyBrandToDocument } from '$lib/stores/branding';
 import SearchModal from '$lib/SearchModal.svelte';
 import DeleteConfirmModal from '$lib/DeleteConfirmModal.svelte';
 let { children } = $props();

 // Dropdown menu state
 let openMenu = $state<string | null>(null); // 'build' | 'knowledge' | 'admin' | 'user' | null
 let mobileNavOpen = $state(false);
 let agentsOnline = $state<number | null>(null);
 let agentsTotal = $state<number | null>(null);
 let subAgentCount = $state<number | null>(null);
 let osHubFetchFailed = $state(false);
 let showAgentsDrawer = $state(false);

 // Live fetch from OS Hub aggregator
 async function loadOsHubHeader() {
 try {
 const tok = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 const r = await fetch('/api/projects/agents/os-hub', { headers: { Authorization: `Bearer ${tok || ''}` } });
 if (r.ok) {
 const j = await r.json();
 const h = j?.header || {};
 agentsOnline = typeof h.agents_active === 'number' ? h.agents_active : null;
 agentsTotal = typeof h.agents_total === 'number' ? h.agents_total : null;
 subAgentCount = typeof h.sub_agents_online === 'number' ? h.sub_agents_online : null;
 osHubFetchFailed = false;
 } else {
 osHubFetchFailed = true;
 }
 } catch {
 osHubFetchFailed = true;
 }
 }
 $effect(() => {
 loadOsHubHeader();
 const iv = setInterval(loadOsHubHeader, 30000);
 return () => clearInterval(iv);
 });
 const _agentsList = [
 { group: 'Core team', items: [
 { name: 'Leader', role: 'Coordinator + multi-agent routing' },
 { name: 'Analyst', role: 'SQL + reasoning + 31 tools' },
 { name: 'Engineer', role: 'Views, dashboards, schema' },
 { name: 'Researcher', role: 'Document RAG + grounded facts' },
 { name: 'Customer Strategist', role: 'RFM, CLV, churn, NBO, campaigns' },
 ]},
 { group: 'Specialists', items: [
 { name: 'Comparator', role: 'Side-by-side compare' },
 { name: 'Diagnostician', role: 'Root cause analysis' },
 { name: 'Narrator', role: 'Plain-English explainer' },
 { name: 'Validator', role: 'Cross-check + sanity' },
 { name: 'Planner', role: 'Multi-step plans' },
 { name: 'Trend Analyst', role: 'Series + seasonality' },
 { name: 'Pareto Analyst', role: '80/20 driver split' },
 { name: 'Anomaly Detector', role: 'Outlier flag' },
 { name: 'Benchmarker', role: 'Industry compare' },
 { name: 'Prescriptor', role: 'Recommendations' },
 ]},
 { group: 'Background', items: [
 { name: 'Judge', role: 'Quality scoring 1–5' },
 { name: 'Rule Suggester', role: 'Extract rules from chat' },
 { name: 'Proactive Insights',role: 'Anomaly cards' },
 { name: 'Query Plan Extractor', role: 'JOIN strategies' },
 { name: 'Meta Learner', role: 'Self-correction tracking' },
 { name: 'Auto Evolver', role: 'Evolved instructions' },
 { name: 'Chat Triple Extractor', role: 'KG growth from chat' },
 ]},
 { group: 'Upload', items: [
 { name: 'Conductor', role: 'Upload orchestrator' },
 { name: 'Parser', role: 'CSV/Excel/JSON' },
 { name: 'Scanner', role: 'PDF/PPTX/DOCX' },
 { name: 'Vision', role: 'Charts + images' },
 { name: 'Inspector', role: 'Quality validation' },
 ]},
 { group: 'Visual + routing', items: [
 { name: 'Visualizer', role: 'Auto chart type' },
 { name: 'Router', role: 'Smart project routing' },
 ]},
 ];

 function toggleMenu(name: string) {
 openMenu = openMenu === name ? null : name;
 }
 function closeMenus() {
 openMenu = null;
 }
 function navTo(path: string) {
 closeMenus();
 mobileNavOpen = false;
 goto(path);
 }
 function handleEsc(e: KeyboardEvent) {
 if (e.key === 'Escape') { closeMenus(); mobileNavOpen = false; }
 if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K') && isChatRoute) {
 e.preventDefault();
 showChatSearch = true;
 }
 }

 // Active route helpers for grouped nav
 const isProjectsActive = $derived(page.url.pathname.includes('/projects') || page.url.pathname.includes('/project/'));
 const isChatActive = $derived(page.url.pathname.endsWith('/chat'));
 // Chat screens (project conversation / /chat) hide the floating robot — it clutters the chat surface.
 const isChatScreen = $derived(page.url.pathname.includes('/project/') || page.url.pathname.endsWith('/chat'));
 // bare conversation page (/project/{slug}) — no trailing sub-route
 const isBareChat = $derived(/\/project\/[^/]+\/?$/.test(page.url.pathname));
 // SINGLE auto-train robot — shown on every screen except chat.
 const showRobot = $derived(!isLogin && singleAgent && !!lockedSlug && !page.url.pathname.endsWith('/chat') && !isBareChat);
 const isBuildActive = $derived(
 page.url.pathname.includes('/dashboard') ||
 page.url.pathname.includes('/presentations') ||
 page.url.pathname.includes('/automl') ||
 page.url.pathname.startsWith('/ui/skills')
 );
 const isKnowledgeActive = $derived(page.url.pathname.includes('/brain'));
 // Usage & Cost = its own standalone full page (no command-center rail).
 const isUsageActive = $derived(page.url.pathname.includes('/usage'));
 const isAdminActive = $derived(
 page.url.pathname.includes('/command-center') ||
 page.url.pathname.includes('/ui/admin')
 );
 // Admin ▾ dropdown active when on any admin-group surface
 const isAdminGroupActive = $derived(
 isAdminActive ||
 isUsageActive ||
 page.url.pathname.includes('/users') ||
 page.url.pathname.includes('/super') ||
 page.url.pathname.includes('/roles') ||
 page.url.pathname.includes('/auth-admin')
 );
 // Admin dropdown badge counts
 // Human-in-loop (approvals/HITL) removed 2026-05-20 — no producers.
 let adminCounts = $state({ skill_drafts: 0, sub_agents: 0 });
 async function loadAdminCounts() {
 try {
 const tok = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 const h = { Authorization: `Bearer ${tok || ''}` };
 const [sd, sa] = await Promise.all([
 fetch('/api/skill-drafts?status=pending', { headers: h }).then(r => r.ok ? r.json() : null).catch(() => null),
 fetch('/api/custom-agents?limit=200', { headers: h }).then(r => r.ok ? r.json() : null).catch(() => null),
 ]);
 adminCounts = {
 skill_drafts: (sd?.drafts || []).length || 0,
 sub_agents: (sa?.agents || []).filter((x: any) => x?.enabled !== false).length || 0,
 };
 } catch {}
 }
 $effect(() => { loadAdminCounts(); });
 // Chat-route gate: sidebar + Cmd-K only active on chat pages
 const isChatRoute = $derived.by(() => {
 const p = page.url.pathname;
 if (p === '/ui/chat' || p === '/chat') return true;
 // Match ONLY bare project slug: /ui/project/{slug} or /project/{slug} (no further segments)
 if (/^\/(?:ui\/)?project\/[^/]+\/?$/.test(p)) return true;
 return false;
 });
 function routeMatches(path: string): boolean {
 return page.url.pathname.includes(path);
 }

 // Active scope (read from localStorage, refreshed on mount)
 let activeScope = $state<ActiveScope | null>(null);

 let authenticated = $state(false);
 let username = $state('');
 let checking = $state(true);
 let showChangePassword = $state(false);
 let isSuper = $state(false);
 let isAdmin = $state(false);
 // RBAC surface visibility (super-admin configurable matrix). Super = always full.
 type Surfaces = { dashboard: boolean; chat: boolean; workspace: boolean; integration: boolean; admin_console: boolean; users_access: boolean; usage_cost: boolean };
 let surfaces = $state<Surfaces>({ dashboard: true, chat: true, workspace: true, integration: true, admin_console: true, users_access: true, usage_cost: true });
 const canDashboard = $derived(isSuper || surfaces.dashboard);
 const canChat = $derived(isSuper || surfaces.chat);
 const canWorkspace = $derived(isSuper || surfaces.workspace);
 const canIntegration = $derived(isSuper || surfaces.integration);
 const canAdminConsole = $derived(isSuper || surfaces.admin_console);
 const canUsers = $derived(isSuper || surfaces.users_access);
 const canUsage = $derived(isSuper || surfaces.usage_cost);
 const canAdminGroup = $derived(canAdminConsole || canUsers || canUsage);
 let cpOld = $state('');
 let cpNew = $state('');
 let cpConfirm = $state('');
 let cpError = $state('');
 let cpSuccess = $state(false);

 // Notifications
 let notifications = $state<any[]>([]);
 let unreadCount = $state(0);
 let showNotifications = $state(false);
 let feedFilter = $state<'all' | 'unread' | 'training' | 'ml' | 'alerts'>('all');

 // Feed drawer = Activity (notifications) + What's new (releases), one bell.
 let feedTab = $state<'activity' | 'whatsnew'>('activity');
 let seenVersion = $state('');
 // dot lights when there's an unseen new version
 let versionIsNew = $derived(!!$versionInfo && !!$versionInfo.version && $versionInfo.version !== seenVersion);
 // single bell dot = unread events OR an unseen version
 let bellHasDot = $derived(unreadCount > 0 || versionIsNew);

 function openFeed(tab: 'activity' | 'whatsnew' = 'activity') {
   feedTab = tab;
   showNotifications = true;
   loadNotifications();
   loadVersion();
 }
 function markVersionSeen() {
   const v = $versionInfo?.version;
   if (v) { seenVersion = v; try { localStorage.setItem('cp_seen_version', v); } catch {} }
 }
 // when the user opens the What's-new tab, clear the version part of the dot
 $effect(() => { if (showNotifications && feedTab === 'whatsnew') markVersionSeen(); });

 function relativeTime(iso: string | undefined): string {
 if (!iso) return '';
 try {
 const t = new Date(iso).getTime();
 const diff = Date.now() - t;
 const s = Math.floor(diff / 1000);
 if (s < 60) return `${s}s ago`;
 const m = Math.floor(s / 60);
 if (m < 60) return `${m}m ago`;
 const h = Math.floor(m / 60);
 if (h < 24) return `${h}h ago`;
 const d = Math.floor(h / 24);
 if (d < 30) return `${d}d ago`;
 return new Date(iso).toLocaleDateString();
 } catch { return ''; }
 }

 function ntypeColor(t: string): string {
 if (t === 'success') return '#2d6a4f';
 if (t === 'warn' || t === 'warning') return '#d97706';
 if (t === 'error') return '#c0392b';
 return '#3b82f6'; // info
 }

 function feedFiltered(): any[] {
 if (feedFilter === 'all') return notifications;
 if (feedFilter === 'unread') return notifications.filter(n => !n.read);
 if (feedFilter === 'training') return notifications.filter(n => /train/i.test(n.title || ''));
 if (feedFilter === 'ml') return notifications.filter(n => /automl|ml |model/i.test(n.title || ''));
 if (feedFilter === 'alerts') return notifications.filter(n => n.type === 'warn' || n.type === 'warning' || n.type === 'error');
 return notifications;
 }

 async function loadNotifications() {
 const token = localStorage.getItem('dash_token');
 if (!token) return;
 try {
 const res = await fetch('/api/notifications', { headers: { Authorization: `Bearer ${token}` } });
 if (res.ok) { const d = await res.json(); notifications = d.notifications || []; unreadCount = d.unread || 0; }
 } catch {}
 }

 async function markAllRead() {
 const token = localStorage.getItem('dash_token');
 if (!token) return;
 try { await fetch('/api/notifications/read-all', { method: 'POST', headers: { Authorization: `Bearer ${token}` } }); unreadCount = 0; notifications = notifications.map(n => ({ ...n, read: true })); } catch {}
 }

 // Search
 let showSearch = $state(false);
 let searchQuery = $state('');
 let searchResults = $state<any[]>([]);

 // Claude-style chat sidebar
 let recents = $state<any[]>([]);
 let activeSession = $state('');
 let sidebarCollapsed = $state(false);
 let showChatSearch = $state(false);
 if (typeof localStorage !== 'undefined') {
 try { sidebarCollapsed = localStorage.getItem('sidebar_collapsed') === '1'; } catch {}
 try { activeSession = localStorage.getItem('dash_session') || ''; } catch {}
 }
 $effect(() => {
 try { localStorage.setItem('sidebar_collapsed', sidebarCollapsed ? '1' : '0'); } catch {}
 });
 const groupedRecents = $derived.by(() => {
 const groups: { label: string; items: any[] }[] = [
 { label: 'Today', items: [] },
 { label: 'Yesterday', items: [] },
 { label: 'Previous 7 days', items: [] },
 { label: 'Previous 30 days', items: [] },
 { label: 'Older', items: [] },
 ];
 const now = new Date();
 const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
 const startOfYesterday = startOfToday - 86400_000;
 const sevenDaysAgo = startOfToday - 6 * 86400_000;
 const thirtyDaysAgo = startOfToday - 29 * 86400_000;
 for (const r of recents) {
 const raw = r?.updated_at || r?.last_message_at || r?.created_at;
 const ts = raw ? new Date(raw).getTime() : 0;
 if (!ts) { groups[4].items.push(r); continue; }
 if (ts >= startOfToday) groups[0].items.push(r);
 else if (ts >= startOfYesterday) groups[1].items.push(r);
 else if (ts >= sevenDaysAgo) groups[2].items.push(r);
 else if (ts >= thirtyDaysAgo) groups[3].items.push(r);
 else groups[4].items.push(r);
 }
 return groups.filter(g => g.items.length > 0);
 });

 function recentTitle(r: any): string {
 const raw = (r?.title || r?.first_message || '').toString().trim();
 if (raw) return raw.slice(0, 40);
 if (r?.created_at) {
 try {
 return new Date(r.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
 } catch {}
 }
 return 'Chat';
 }
 async function loadRecents() {
 try {
 const token = localStorage.getItem('dash_token');
 if (!token) { recents = []; return; }
 const path = page.url.pathname;
 const projMatch = path.match(/^\/(?:ui\/)?project\/([^/]+)\/?$/);
 let list: any[] = [];
 if (projMatch) {
 const slug = projMatch[1];
 let r = await fetch(`/api/projects/${slug}/sessions`, { headers: { Authorization: `Bearer ${token}` } });
 if (!r.ok) {
 // fallback: fetch all + filter
 r = await fetch('/api/sessions', { headers: { Authorization: `Bearer ${token}` } });
 if (!r.ok) { recents = []; return; }
 const d = await r.json();
 list = (d.sessions || d || []).filter((s: any) => s.project_slug === slug).slice(0, 200);
 } else {
 const d = await r.json();
 list = (d.sessions || d || []).slice(0, 200);
 }
 } else if (path === '/ui/chat' || path === '/chat') {
 const r = await fetch('/api/sessions', { headers: { Authorization: `Bearer ${token}` } });
 if (!r.ok) { recents = []; return; }
 const d = await r.json();
 list = (d.sessions || d || []).slice(0, 200);
 } else {
 recents = []; return;
 }
 // Backfill missing first_message via per-session message fetch (best-effort, parallel)
 const needFetch = list.filter((x: any) => !x.title && !x.first_message && x.session_id);
 if (needFetch.length) {
 await Promise.all(needFetch.map(async (x: any) => {
 try {
 const mr = await fetch(`/api/sessions/${encodeURIComponent(x.session_id)}/messages`, { headers: { Authorization: `Bearer ${token}` } });
 if (mr.ok) {
 const md = await mr.json();
 const msgs = md.messages || md.runs || [];
 const firstUser = msgs.find((m: any) => (m.role || m.type) === 'user') || msgs[0];
 const text = (firstUser?.content || firstUser?.message || firstUser?.text || '').toString().trim();
 if (text) x.first_message = text;
 }
 } catch {}
 }));
 }
 recents = list;
 } catch { recents = []; }
 }

 // Re-fetch recents on route change between chat and project chat
 $effect(() => {
 // Track pathname for reactivity
 const _p = page.url.pathname;
 if (authenticated && isChatRoute) {
 loadRecents();
 }
 });

 function newChatFromSidebar() {
 const path = page.url.pathname;
 const projMatch = path.match(/^\/(?:ui\/)?project\/([^/]+)\/?$/);
 if (projMatch) {
 const slug = projMatch[1];
 try { localStorage.removeItem(`dash_project_session_${slug}`); } catch {}
 activeSession = '';
 try { window.dispatchEvent(new CustomEvent('dash-newchat-project', { detail: { slug } })); } catch {}
 goto(`/ui/project/${slug}`, { replaceState: true, invalidateAll: true });
 } else {
 try { localStorage.removeItem('dash_session'); } catch {}
 activeSession = '';
 if (path !== '/ui/chat' && path !== '/chat') {
 goto('/ui/chat');
 } else {
 try { window.dispatchEvent(new CustomEvent('dash-newchat-global')); } catch {}
 }
 }
 }

 function openSession(sid: string) {
 const path = page.url.pathname;
 const projMatch = path.match(/^\/(?:ui\/)?project\/([^/]+)\/?$/);
 if (projMatch) {
 const slug = projMatch[1];
 try { localStorage.setItem(`dash_project_session_${slug}`, sid); } catch {}
 activeSession = sid;
 try { window.dispatchEvent(new CustomEvent('dash-open-session', { detail: { sid, scope: 'project', slug } })); } catch {}
 goto(`/ui/project/${slug}`, { replaceState: true, invalidateAll: true });
 } else {
 try { localStorage.setItem('dash_session', sid); } catch {}
 activeSession = sid;
 if (path === '/ui/chat' || path === '/chat') {
 try { window.dispatchEvent(new CustomEvent('dash-open-session', { detail: { sid, scope: 'global' } })); } catch {}
 } else {
 goto('/ui/chat');
 }
 }
 }

 async function doSearch() {
 if (!searchQuery || searchQuery.length < 2) { searchResults = []; return; }
 const token = localStorage.getItem('dash_token');
 try {
 const res = await fetch(`/api/search?q=${encodeURIComponent(searchQuery)}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
 if (res.ok) { const d = await res.json(); searchResults = d.results || []; }
 } catch {}
 }

 // API Key
 let showApiKey = $state(false);
 let apiKey = $state('');
 let apiKeyLoading = $state(false);

 async function loadApiKey() {
 const token = localStorage.getItem('dash_token');
 if (!token) return;
 apiKeyLoading = true;
 try {
 const res = await fetch('/api/auth/api-key', { headers: { Authorization: `Bearer ${token}` } });
 if (res.ok) { const d = await res.json(); apiKey = d.api_key || 'No key generated'; }
 } catch {} finally { apiKeyLoading = false; }
 }

 async function regenerateApiKey() {
 const token = localStorage.getItem('dash_token');
 if (!token) return;
 try {
 const res = await fetch('/api/auth/api-key/regenerate', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
 if (res.ok) { const d = await res.json(); apiKey = d.api_key || ''; }
 } catch {}
 }

 // Global CLI footer — PULSE-style auto-show bottom bar
 let cliVisible = $state(true); // master visibility (true except after manual hide)
 let cliExpanded = $state(false); // expanded vs thin bar
 // Scout-style console controls
 let cliPaused = $state(false);
 let cliAutoscroll = $state(true);
 let cliMaxLines = $state(200);
 // Gate: the Activity terminal stays HIDDEN until a real training/pipeline
 // event fires (set in _markActivity). Tab-nav noise ("accessing X module")
 // is filtered by _SKIP_RE and never flips this, so navigation no longer
 // pops the CLI. Manual show (nav button) also flips it on.
 let cliHasActivity = $state(false);
 let cliLogs = $state<{text: string; done: boolean; ts?: number; fields?: any}[]>([]);
 let cliTraining = $state(false);
 let cliAutoCollapse: ReturnType<typeof setTimeout> | null = null;
 let cliScrollEl: HTMLDivElement;
 let _cliActivityTimer: ReturnType<typeof setTimeout> | null = null;
 let _lastActivityAt = $state(Date.now());
 let _userPinned = $state(false);
 let _cliLastLogLen = $state(0);
 let _cliUnread = $state(0);
 let _userScrolledUp = $state(false);

 // Completion patterns — when matched, training session ends, start grace timer
 const _COMPLETE_RE = /pipeline complete|all done|training complete|training done|cockpit\s*100\s*%| all|complete\s*\(100%\)/i;

 // Format timestamp as full ISO local: 2026-05-15 08:46:11
 function formatTs(ts: number): string {
 const d = new Date(ts);
 const yyyy = d.getFullYear();
 const mm = String(d.getMonth() + 1).padStart(2, '0');
 const dd = String(d.getDate()).padStart(2, '0');
 const hh = String(d.getHours()).padStart(2, '0');
 const mi = String(d.getMinutes()).padStart(2, '0');
 const ss = String(d.getSeconds()).padStart(2, '0');
 return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
 }

 // Parse pipe-delimited structured log line: "entity | ACTION | model | duration | cost"
 function parseStructuredFields(text: string): { entity: string; action: string; model: string; duration: string; cost: string } | null {
 if (!text || typeof text !== 'string') return null;
 const segs = text.split('|').map(s => s.trim());
 if (segs.length !== 5) return null;
 return { entity: segs[0], action: segs[1], model: segs[2], duration: segs[3], cost: segs[4] };
 }

 function onCliScroll() {
 if (!cliScrollEl) return;
 const distFromBottom = cliScrollEl.scrollHeight - cliScrollEl.scrollTop - cliScrollEl.clientHeight;
 _userScrolledUp = distFromBottom > 100;
 }

 function jumpToLatest() {
 _userScrolledUp = false;
 if (cliScrollEl) {
 cliScrollEl.scrollTop = cliScrollEl.scrollHeight;
 }
 }

 // Detect activity within last 5s
 let cliActive = $derived(Date.now() - _lastActivityAt < 5000);

 // Header live counts derived from cliLogs
 let cliStats = $derived.by(() => {
 let running = 0, done = 0, cost = 0;
 if (!cliLogs || !Array.isArray(cliLogs)) return { running: 0, done: 0, cost: '0.0000' };
 for (const log of cliLogs.slice(-200)) {
 const t = ((log as any)?.text ?? log)?.toString() ?? '';
 if (/\bRUNNING\b|started|streaming/i.test(t)) running++;
 if (/\bCOMPLETE\b|\bDONE\b|/i.test(t)) done++;
 const m = t.match(/\$\s*(\d+\.\d+)/);
 if (m) cost += parseFloat(m[1]);
 }
 return { running, done, cost: cost.toFixed(4) };
 });

 function _markActivity() {
 _lastActivityAt = Date.now();
 cliHasActivity = true; // marks unread badge on the thin console bar
 // Do NOT auto-expand the console on activity — the floating robot panel is the
 // glanceable live surface. Console stays a thin bar; user expands for the full
 // dev log (pause/clear/csv) on demand. (Differentiate, not duplicate.)
 cliTraining = true;
 // Cancel any pending collapse — stay open until completion
 if (_cliActivityTimer) { clearTimeout(_cliActivityTimer); _cliActivityTimer = null; }
 if (cliAutoCollapse) { clearTimeout(cliAutoCollapse); cliAutoCollapse = null; }
 }

 function _markComplete() {
 cliTraining = false;
 if (_cliActivityTimer) { clearTimeout(_cliActivityTimer); _cliActivityTimer = null; }
 if (cliAutoCollapse) clearTimeout(cliAutoCollapse);
 cliAutoCollapse = setTimeout(() => {
 if (cliExpanded && !_userPinned) cliExpanded = false;
 }, 8000);
 }

 // Backstop: if no training/pipeline log has arrived for 15s, the run is over
 // (or its terminal log didn't match) — force the robot/badge back to idle so
 // "TRAINING" can never stick. Runs for the app lifetime (SPA root layout).
 setInterval(() => {
 if (cliTraining && Date.now() - _lastActivityAt > 15000) _markComplete();
 }, 5000);

 // Authoritative training flag for the footer badge + robot — polls the REAL
 // active run (dash_training_runs) so the badge agrees with the pipeline strip,
 // not the log-text heuristic (which only sees the global tail steps).
 let srvTraining = $state(false);
 async function _fetchSrvTraining() {
 try {
 const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 if (!token) return;
 const slug = 'citypharma';
 const r = await fetch(`/api/projects/${slug}/auto-train/status`, { headers: { 'Authorization': `Bearer ${token}`, 'X-Scope-Id': slug } });
 if (!r.ok) return;
 const d = await r.json();
 srvTraining = !!d.is_training;
 } catch { /* fail-soft */ }
 }
 setInterval(_fetchSrvTraining, 5000);

 function manualMinimize() {
 cliExpanded = false;
 _userPinned = false;
 if (_cliActivityTimer) clearTimeout(_cliActivityTimer);
 }

 function manualShow() {
 cliVisible = true;
 cliExpanded = true;
 cliHasActivity = true; // explicit open via nav button
 _userPinned = true;
 setTimeout(() => { _userPinned = false; }, 30_000);
 }

 // Back-compat alias for existing nav button
 function showCliPanel() { manualShow(); }

 function hideCli() {
 cliVisible = false;
 if (_cliActivityTimer) clearTimeout(_cliActivityTimer);
 }

 function copyCli() {
 if (!Array.isArray(cliLogs)) return;
 const text = cliLogs.map(l => `[${(l as any).ts || ''}] ${(l as any).text || l}`).join('\n');
 if (text && navigator.clipboard) {
 navigator.clipboard.writeText(text).catch(() => {});
 }
 }

 function clearCli() {
 cliLogs = [];
 _cliUnread = 0;
 }

 function lineKind(line: any): string {
 const t = (line?.text ?? line)?.toString() ?? '';
 if (/\bCOMPLETE\b|\bDONE\b|/i.test(t)) return 'done';
 if (/\bFAILED\b||error/i.test(t)) return 'error';
 if (/\bRUNNING\b|streaming/i.test(t)) return 'running';
 return 'info';
 }

 function formatNow(): string {
 const d = new Date();
 return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
 }

 // Watch cliLogs.length for new entries → mark activity ONLY for training/pipeline events
 // Skip silent logs (tab switch info, "accessing X module", etc) — those just append to log.
 const _ACTIVITY_RE = /\b(training|trained|pipeline|automl|streaming|complete|done|step)\b|||step\s+\d+\/\d+|\$\s*\d/i;
 const _SKIP_RE = /accessing\s+\w+\s+module|[│└├─]|\bPENDING\b|\brows?\b\s*·\s*\d+\s*cols?|tables?\s*·.*rows?\s*·.*docs|total\s+files|supported:\s|·\s*TRAINED\b/i;
 $effect(() => {
 const len = cliLogs.length;
 if (len > _cliLastLogLen) {
 const newLines = cliLogs.slice(_cliLastLogLen, len);
 _cliLastLogLen = len;
 // Track unread when bar collapsed
 if (!cliExpanded) _cliUnread += newLines.length;
 const trainingActive = newLines.some((l: any) => {
 const t = (l?.text ?? l)?.toString() ?? '';
 if (_SKIP_RE.test(t)) return false;
 return _ACTIVITY_RE.test(t);
 });
 const completed = newLines.some((l: any) => {
 const t = (l?.text ?? l)?.toString() ?? '';
 return _COMPLETE_RE.test(t);
 });
 if (trainingActive) _markActivity();
 if (completed) _markComplete();
 } else if (len < _cliLastLogLen) {
 _cliLastLogLen = len;
 }
 });

 // Training header state
 let trainStartTs = $state(0);
 let trainElapsed = $state(0);
 let trainTimer: ReturnType<typeof setInterval> | null = null;
 let trainCurrentStep = $state('');
 let trainCurrentTable = $state('');
 let trainTableIdx = $state('');
 let trainTableTotal = $state('');

 // Parse running totals from cliLogs
 const trainTotals = $derived.by(() => {
 let calls = 0;
 let cost = 0;
 let tokIn = 0;
 let tokOut = 0;
 let model = '';
 // Iterate from newest to oldest, capture first match for each metric
 for (let i = cliLogs.length - 1; i >= 0; i--) {
 const t = cliLogs[i].text;
 if (!t) continue;
 // running $X.XXXX (N calls)
 if (calls === 0) {
 const m = t.match(/running \$(\d+\.\d+)\s*\((\d+)\s*calls?\)/);
 if (m) { cost = parseFloat(m[1]); calls = parseInt(m[2], 10); }
 }
 // last tokens "1234→567 tok"
 if (tokIn === 0 && tokOut === 0) {
 const m = t.match(/(\d+)\s*→\s*(\d+)\s*tok/);
 if (m) { tokIn = parseInt(m[1], 10); tokOut = parseInt(m[2], 10); }
 }
 // model: line like " llm · task · model_short · ..." — model_short is 3rd ·-segment
 if (!model) {
 const m = t.match(/llm\s*·\s*[^·]+·\s*([^·]+?)\s*·/);
 if (m) model = m[1].trim();
 }
 if (calls && model && tokIn) break;
 }
 return { calls, cost, tokIn, tokOut, model };
 });

 function cliScrollToBottom() {
 if (_userScrolledUp) return;
 requestAnimationFrame(() => {
 if (cliScrollEl) cliScrollEl.scrollTop = cliScrollEl.scrollHeight;
 });
 setTimeout(() => {
 if (cliScrollEl && !_userScrolledUp) cliScrollEl.scrollTop = cliScrollEl.scrollHeight;
 }, 50);
 }

 function cliLog(input: string | { text: string; done?: boolean; fields?: any }, done: boolean = true) {
 let entry: { text: string; done: boolean; ts: number; fields?: any };
 // Coerce any non-string into a real string so an object never renders as
 // the literal "[object Object]" in the terminal.
 const _str = (v: any): string => (typeof v === 'string' ? v : (v == null ? '' : (() => { try { return JSON.stringify(v); } catch { return String(v); } })()));
 if (typeof input === 'string') {
 const fields = parseStructuredFields(input);
 entry = { text: input, done, ts: Date.now(), fields: fields || undefined };
 } else {
 const text = _str(input?.text);
 const fields = input.fields || parseStructuredFields(text);
 entry = { text, done: input.done ?? true, ts: Date.now(), fields: fields || undefined };
 }
 if (cliPaused) return; // pause swallows new events
 cliLogs = [...cliLogs, entry];
 // Cap buffer to cliMaxLines (keep newest)
 if (cliLogs.length > cliMaxLines * 2) cliLogs = cliLogs.slice(-cliMaxLines);
 // Expand only handled via $effect activity filter (training/pipeline events)
 if (cliAutoscroll) cliScrollToBottom();
 }

 function cliMarkAllDone() {
 cliLogs = cliLogs.map(l => ({ ...l, done: true }));
 cliScrollToBottom();
 }

 function cliDoneAndCollapse(delay: number = 5000) {
 cliMarkAllDone();
 if (cliAutoCollapse) clearTimeout(cliAutoCollapse);
 cliAutoCollapse = setTimeout(() => { cliExpanded = false; }, delay);
 }

 function getCurrentProjectSlug(): string {
 const m = page.url.pathname.match(/\/project\/([^/]+)/);
 return m ? m[1] : '';
 }

 // Only show CLI on settings + command center (NOT on chat or dashboard)
 const showCli = $derived(page.url.pathname.includes('/settings') || page.url.pathname.includes('/command-center'));

 // (Auto-show on activity is handled by _markActivity via $effect on cliLogs.length)

 // Log page/tab navigation
 let lastLoggedPath = $state('');
 $effect(() => {
 const path = page.url.pathname;
 if (!showCli || path === lastLoggedPath) return;
 lastLoggedPath = path;

 // Entering a new page hides the Activity terminal again — it only comes
 // back when a real training/pipeline event fires (via _markActivity).
 // Don't reset while a training run is in flight (cliTraining).
 if (!cliTraining) cliHasActivity = false;

 if (path.includes('/settings')) {
 // Don't log settings root, tabs are logged separately
 } else if (path.includes('/dashboard')) {
 cliLog(` ${timeNow()} ── navigated to dashboard`, true);
 } else if (path.match(/\/project\/[^/]+$/)) {
 cliLog(` ${timeNow()} ── opened chat`, true);
 } else if (path.includes('/command-center')) {
 cliLog(` ${timeNow()} ── opened command center`, true);
 }
 });

 function timeNow(): string {
 const d = new Date();
 const date = d.toLocaleDateString('en-US', { month: 'short', day: '2-digit' });
 const time = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
 return `${date} ${time}`;
 }

 async function trainAll(skipPost = false) {
 // skipPost=true → another page (settings quality-card) already fired the
 // retrain (possibly for a SELECTED subset of tables). We only arm the
 // terminal + stream the run, so we don't double-train or ignore the picks.
 const slug = getCurrentProjectSlug();
 if (!slug || cliTraining) return;
 cliTraining = true;
 cliHasActivity = true; // real training run → reveal terminal
 cliExpanded = true;
 const token = localStorage.getItem('dash_token');
 const headers = token ? { Authorization: `Bearer ${token}` } : {};
 const t0 = Date.now();
 trainStartTs = t0;
 trainElapsed = 0;
 trainCurrentStep = 'starting';
 trainCurrentTable = '';
 trainTableIdx = '';
 trainTableTotal = '';
 if (trainTimer) clearInterval(trainTimer);
 trainTimer = setInterval(() => { trainElapsed = Math.floor((Date.now() - trainStartTs) / 1000); }, 1000);

 if (cliLogs.length > 0) { cliLog(``, true); cliLog(` ─────────────────────────────────────────────`, true); cliLog(``, true); }

 cliLog(`$ dash train-all`, true);
 cliLog(` ${timeNow()} ── scanning project...`, false);

 try {
 const statsRes = await fetch(`/api/projects/${slug}/stats`, { headers });
 let tables: any[] = [];
 if (statsRes.ok) { const d = await statsRes.json(); tables = d.tables || []; }
 cliMarkAllDone();
 const totalRows = tables.reduce((s: number, t: any) => s + (t.rows || 0), 0);
 const totalCols = tables.reduce((s: number, t: any) => s + (t.columns || 0), 0);
 cliLog(` ${timeNow()} ── found ${tables.length} tables · ${totalRows.toLocaleString()} rows · ${totalCols} cols`, true);
 cliLog(``, true);

 cliLog(` ${timeNow()} ── starting training pipeline (17 steps × ${tables.length} tables, est. ${Math.max(5, tables.length * 2)} min)...`, false);
 if (!skipPost) {
 const res = await fetch(`/api/projects/${slug}/retrain`, { method: 'POST', headers });
 if (!res.ok) {
 cliMarkAllDone();
 cliLog(` ${timeNow()} ── retrain endpoint failed (${res.status})`, true);
 cliTraining = false; cliDoneAndCollapse(8000);
 return;
 }
 }
 cliMarkAllDone();
 cliLog(``, true);

 // Poll real training-runs every 2s, stream new log entries
 let logsSeen = 0;
 let lastStep = '';
 let lastTable = '';
 let pollErrCount = 0;
 const POLL_MS = 2000;
 const MAX_POLL_MIN = 30;
 const deadline = Date.now() + MAX_POLL_MIN * 60 * 1000;

 while (Date.now() < deadline) {
 await new Promise(r => setTimeout(r, POLL_MS));
 try {
 const r = await fetch(`/api/projects/${slug}/training-runs`, { headers });
 if (!r.ok) { pollErrCount++; if (pollErrCount > 5) break; continue; }
 pollErrCount = 0;
 const d = await r.json();
 const run = (d.runs || [])[0];
 if (!run) continue;

 // Header tick (step | table_index | total)
 const steps = String(run.steps || '');
 if (steps && steps !== lastStep) {
 const parts = steps.split('|');
 if (parts.length === 4) {
 const [stepName, tblName, idx, total] = parts;
 trainCurrentStep = stepName;
 trainCurrentTable = tblName;
 trainTableIdx = idx;
 trainTableTotal = total;
 if (tblName && tblName !== lastTable) {
 cliLog(``, true);
 cliLog(` ${timeNow()} ── table ${idx}/${total} · ${tblName}`, false);
 lastTable = tblName;
 }
 cliMarkAllDone();
 cliLog(` ${timeNow()} │ ▸ ${stepName}`, false);
 } else {
 trainCurrentStep = steps;
 cliMarkAllDone();
 cliLog(` ${timeNow()} ── ${steps}`, false);
 }
 lastStep = steps;
 }

 // Append new log entries
 const allLogs = run.logs || [];
 if (allLogs.length > logsSeen) {
 for (let i = logsSeen; i < allLogs.length; i++) {
 const entry = allLogs[i];
 if (!entry || !entry.msg) continue;
 const ts = entry.ts || timeNow();
 const tbl = entry.table ? ` [${entry.table}]` : '';
 const tIdx = (entry.table_index && entry.total_tables) ? ` (${entry.table_index}/${entry.total_tables})` : '';
 const _msg = typeof entry.msg === 'string' ? entry.msg : JSON.stringify(entry.msg);
 cliLog(` ${ts} │ ${_msg}${tbl}${tIdx}`, true);
 }
 logsSeen = allLogs.length;
 }

 if (run.status === 'done' || run.status === 'failed' || run.status === 'cancelled') {
 cliMarkAllDone();
 const totalDur = ((Date.now() - t0) / 1000).toFixed(1);
 if (run.status === 'done') {
 cliLog(``, true);
 cliLog(` ${timeNow()} ── training complete · ${totalDur}s · ${run.tables || tables.length} tables`, true);
 } else {
 cliLog(``, true);
 cliLog(` ${timeNow()} ── training ${run.status}: ${run.error || 'unknown'}`, true);
 }
 break;
 }
 } catch {
 pollErrCount++;
 if (pollErrCount > 5) break;
 }
 }
 } catch {
 cliMarkAllDone();
 cliLog(` ${timeNow()} ── connection error`, true);
 }

 cliTraining = false;
 if (trainTimer) { clearInterval(trainTimer); trainTimer = null; }
 cliDoneAndCollapse(10000);
 }

 function fmtElapsed(s: number): string {
 const m = Math.floor(s / 60);
 const sec = s % 60;
 return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
 }

 // Auto-scroll CLI whenever logs change OR when terminal just expanded
 $effect(() => {
 const _len = cliLogs.length;
 const _expanded = cliExpanded;
 if (_expanded) _cliUnread = 0;
 if (cliScrollEl && _len > 0 && !_userScrolledUp) {
 requestAnimationFrame(() => { if (cliScrollEl && !_userScrolledUp) cliScrollEl.scrollTop = cliScrollEl.scrollHeight; });
 setTimeout(() => { if (cliScrollEl && !_userScrolledUp) cliScrollEl.scrollTop = cliScrollEl.scrollHeight; }, 50);
 }
 });

 // Dashboard nav
 let dashProjects = $state<{slug: string; name: string; agent_name: string}[]>([]);
 // Single-agent product (CityPharma) — set from /api/flags
 let singleAgent = $state(false);
 let lockedSlug = $state<string | null>(null);
 let productName = $state('Dash');
 // Integration kill switches (from /api/flags) — default true so nav doesn't flicker off while loading
 let gatewayEnabled = $state(true);
 let embedEnabled = $state(true);
 const chatHref = $derived(singleAgent && lockedSlug ? `/ui/project/${lockedSlug}` : '/ui/chat');
 const overviewHref = $derived(singleAgent && lockedSlug ? `/ui/project/${lockedSlug}/overview` : '/ui/projects');
 const agentBrainHref = $derived(singleAgent && lockedSlug ? `/ui/project/${lockedSlug}/settings#datasets` : '/ui/projects');
 const uploadHref = $derived(singleAgent && lockedSlug ? `/ui/project/${lockedSlug}/settings#upload` : '/ui/projects');
 let showDashPicker = $state(false);

 async function changePassword() {
 if (cpNew !== cpConfirm) { cpError = 'Passwords do not match'; return; }
 if (cpNew.length < 4) { cpError = 'Min 4 characters'; return; }
 cpError = '';
 const token = localStorage.getItem('dash_token');
 try {
 const res = await fetch(`/api/auth/change-password?old_password=${encodeURIComponent(cpOld)}&new_password=${encodeURIComponent(cpNew)}`, {
 method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}
 });
 if (res.ok) { cpSuccess = true; cpOld = ''; cpNew = ''; cpConfirm = ''; }
 else { const d = await res.json(); cpError = d.detail || 'Failed'; }
 } catch { cpError = 'Connection failed'; }
 }


 const isLogin = $derived(page.url.pathname.includes('/login'));
 const isHome = $derived(page.url.pathname.endsWith('/home'));
 const isProjects = $derived(page.url.pathname.endsWith('/projects'));
 const isProject = $derived(page.url.pathname.includes('/project/'));
 const isProjectDashboard = $derived(page.url.pathname.includes('/project/') && page.url.pathname.includes('/dashboard'));
 const isSuperChat = $derived(page.url.pathname.endsWith('/chat'));
 const isDashboard = $derived(page.url.pathname.includes('/command-center'));
 const currentProjectSlug = $derived(() => {
 const m = page.url.pathname.match(/\/project\/([^/]+)/);
 return m ? m[1] : '';
 });

 onMount(async () => {
 // Product flags (public) — single-agent lock
 try {
 const fr = await fetch('/api/flags');
 if (fr.ok) { const f = await fr.json(); singleAgent = !!f.single_agent; lockedSlug = f.locked_slug || null; productName = f.product_name || 'Dash'; gatewayEnabled = f.gateway_enabled !== false; embedEnabled = f.embed_enabled !== false; }
 } catch {}
 // Load tenant branding (public endpoint, no auth required)
 await loadBrand();
 const unsubBrand = brand.subscribe((b) => applyBrandToDocument(b));
 // Clean up subscription on unmount via window beforeunload (best effort)
 window.addEventListener('beforeunload', () => unsubBrand());
 // Listen for events from child pages
 window.addEventListener('dash-train-all', () => trainAll());
 // Settings quality-card already fired the retrain (selected tables) — just stream it.
 window.addEventListener('dash-train-stream', () => trainAll(true));
 window.addEventListener('dash-cli-log', ((e: CustomEvent) => {
 if (e.detail?.text) cliLog(e.detail.text, e.detail.done !== false);
 }) as EventListener);
 // Allow other components to signal CLI activity (auto-shows the bbar)
 window.addEventListener('dash-cli-activity', () => _markActivity());

 if (isLogin) { checking = false; return; }

 const token = localStorage.getItem('dash_token');
 if (!token) { checking = false; redirect(); return; }

 try {
 const res = await fetch('/api/auth/check', { headers: { Authorization: `Bearer ${token}` } });
 if (res.ok) {
 const data = await res.json();
 authenticated = true;
 username = data.username;
 isSuper = data.is_super || false;
 isAdmin = data.is_admin || data.is_super || false;
 if (data.surfaces && typeof data.surfaces === 'object') { const s = data.surfaces; surfaces = { dashboard: !!s.dashboard, chat: !!s.chat, workspace: !!s.workspace, integration: !!s.integration, admin_console: !!s.admin_console, users_access: !!s.users_access, usage_cost: !!s.usage_cost }; }
 // Refresh active scope chip from localStorage (set by scope-picker)
 activeScope = getActiveScope();
 // Load notifications + build version (for the merged feed bell)
 loadNotifications();
 try { seenVersion = localStorage.getItem('cp_seen_version') || ''; } catch {}
 loadVersion();
 // Load projects for dashboard nav
 try {
 const pRes = await fetch('/api/user-projects-brief', { headers: { Authorization: `Bearer ${token}` } });
 if (pRes.ok) { const pd = await pRes.json(); dashProjects = pd.projects || []; }
 } catch {}
 loadRecents();
 } else {
 localStorage.removeItem('dash_token');
 localStorage.removeItem('dash_user');
 redirect();
 }
 } catch { redirect(); }
 checking = false;
 });

 function redirect() {
 if (!isLogin) window.location.href = '/ui/login';
 }

 // Redirect root → chat (single-agent) or home
 $effect(() => {
 if (!authenticated) return;
 const p = page.url.pathname;
 if (singleAgent && lockedSlug) {
 // Land on the Dashboard cockpit; never show projects/home grids.
 if (p === '/ui' || p === '/ui/home' || p === '/ui/projects' || p === '/ui/chat') {
 window.location.href = `/ui/project/${lockedSlug}/overview`;
 }
 } else if (p === '/ui') {
 window.location.href = '/ui/home';
 }
 });

 function logout() {
 const token = localStorage.getItem('dash_token');
 if (token) fetch('/api/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
 localStorage.removeItem('dash_token');
 localStorage.removeItem('dash_user');
 localStorage.removeItem('dash_session');
 clearScope();
 window.location.href = '/ui/login';
 }

 function switchScope() {
 // Clear current scope and bounce through scope picker for the active project.
 clearScope();
 activeScope = null;
 const slug = currentProjectSlug();
 if (slug) {
 const next = `/ui/project/${slug}`;
 window.location.href = `/ui/scope-picker?project_slug=${encodeURIComponent(slug)}&next=${encodeURIComponent(next)}`;
 } else {
 // No project context — just send them home
 window.location.href = '/ui/home';
 }
 }

 function newSession() {
 localStorage.removeItem('dash_session');
 window.location.href = '/ui';
 }

 function exportChat() {
 const msgs = document.querySelectorAll('.bubble-user, .bubble-assistant');
 let text = 'DASH — Chat Export\n' + '='.repeat(40) + '\n\n';
 msgs.forEach((el) => {
 const isUser = el.classList.contains('bubble-user');
 text += (isUser ? 'USER: ' : 'DASH: ') + el.textContent?.trim() + '\n\n';
 });
 const blob = new Blob([text], { type: 'text/plain' });
 const url = URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url;
 a.download = `dash-chat-${Date.now()}.txt`;
 a.click();
 URL.revokeObjectURL(url);
 }

 function getAuthHeaders(): Record<string, string> {
 const token = localStorage.getItem('dash_token');
 return token ? { Authorization: `Bearer ${token}` } : {};
 }
</script>

<svelte:head>
  <link rel="stylesheet" href="/api/branding/theme.css" />
</svelte:head>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<svelte:window onclick={() => { showDashPicker = false; closeMenus(); }} onkeydown={handleEsc} />

<DeleteConfirmModal />

<!-- Global overlays — render in ANY auth branch (moved out of {#if isLogin}) -->
{#if showChangePassword}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div style="position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 200; display: flex; align-items: center; justify-content: center;" onclick={(e) => { if (e.target === e.currentTarget) showChangePassword = false; }}>
  <div style="background: var(--pw-surface); width: 380px; border: 1px solid var(--pw-border); border-radius: var(--pw-radius); box-shadow: var(--pw-shadow-lg); overflow: hidden; font-family: var(--pw-font-body);">
    <div style="display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--pw-border);">
      <span style="font-family: var(--pw-font-headline); font-size: 16px; font-weight: 500; color: var(--pw-ink);">Change password</span>
      <button onclick={() => showChangePassword = false} style="background: none; border: none; color: var(--pw-muted); cursor: pointer; font-size: 16px;">&#10005;</button>
    </div>
    <div style="padding: 20px;">
      {#if cpSuccess}
        <div style="color: var(--pw-success); font-weight: 500; font-size: 13px;"><Icon name="check" size={14} /> Password changed successfully.</div>
      {:else}
        <div style="margin-bottom: 12px;">
          <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">Current password</label>
          <input type="password" bind:value={cpOld} style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 9px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
        </div>
        <div style="margin-bottom: 12px;">
          <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">New password</label>
          <input type="password" bind:value={cpNew} style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 9px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
        </div>
        <div style="margin-bottom: 14px;">
          <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">Confirm</label>
          <input type="password" bind:value={cpConfirm} style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 9px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
        </div>
        {#if cpError}<div style="font-size: 12px; color: var(--pw-error); margin-bottom: 10px;">{cpError}</div>{/if}
        <button onclick={changePassword} style="width: 100%; padding: 10px; background: var(--pw-accent); color: #fff; border: none; border-radius: var(--pw-radius-pill); font-family: var(--pw-font-body); font-size: 13px; font-weight: 500; cursor: pointer;">Update password</button>
      {/if}
    </div>
  </div>
</div>
{/if}

{#if showApiKey}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div style="position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 200; display: flex; align-items: center; justify-content: center;" onclick={(e) => { if (e.target === e.currentTarget) showApiKey = false; }}>
  <div style="background: var(--pw-surface); width: 460px; border: 1px solid var(--pw-border); border-radius: var(--pw-radius); box-shadow: var(--pw-shadow-lg); overflow: hidden; font-family: var(--pw-font-body);">
    <div style="display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--pw-border);">
      <span style="font-family: var(--pw-font-headline); font-size: 16px; font-weight: 500; color: var(--pw-ink);">API key</span>
      <button onclick={() => showApiKey = false} style="background: none; border: none; color: var(--pw-muted); cursor: pointer; font-size: 16px;">&#10005;</button>
    </div>
    <div style="padding: 20px;">
      <div style="font-size: 12px; color: var(--pw-muted); margin-bottom: 14px; line-height: 1.5;">Use this key for programmatic access. Include as <code style="background: var(--pw-bg-alt); padding: 1px 6px; border-radius: 0; font-size: 12px;">Authorization: Bearer YOUR_KEY</code></div>
      {#if apiKeyLoading}
        <div style="font-size: 12px; color: var(--pw-muted);">Loading…</div>
      {:else}
        <div style="background: var(--pw-bg-alt); color: var(--pw-ink); padding: 12px 14px; font-size: 12.5px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; margin-bottom: 14px; border: 1px solid var(--pw-border); border-radius: var(--pw-radius-sm);">
          {apiKey || 'No key generated yet'}
        </div>
        <div style="display: flex; gap: 8px;">
          <button onclick={() => { navigator.clipboard.writeText(apiKey); }} style="background: transparent; color: var(--pw-ink-soft); border: 1px solid var(--pw-border-strong); padding: 8px 16px; font-family: var(--pw-font-body); font-size: 12px; font-weight: 500; border-radius: var(--pw-radius-pill); cursor: pointer;">Copy</button>
          <button onclick={regenerateApiKey} style="background: var(--pw-accent); color: #fff; border: none; padding: 8px 16px; font-family: var(--pw-font-body); font-size: 12px; font-weight: 500; border-radius: var(--pw-radius-pill); cursor: pointer;">Regenerate</button>
        </div>
      {/if}
    </div>
  </div>
</div>
{/if}

{#if showNotifications}
  <div class="pw-agents-backdrop" onclick={() => showNotifications = false}></div>
  <aside class="pw-agents-drawer" onclick={(e) => e.stopPropagation()}>
    <!-- header: tabs (left) + close (right) on ONE aligned row -->
    <div class="pw-feed-head">
      <div class="pw-feed-seg">
        <button class="pw-feed-segbtn" class:pw-feed-segbtn-active={feedTab === 'activity'} onclick={() => feedTab = 'activity'}>
          Activity{#if unreadCount > 0}<span class="pw-feed-segcount">{unreadCount > 99 ? '99+' : unreadCount}</span>{/if}
        </button>
        <button class="pw-feed-segbtn" class:pw-feed-segbtn-active={feedTab === 'whatsnew'} onclick={() => { feedTab = 'whatsnew'; }}>
          What's new{#if versionIsNew}<span class="pw-feed-segdot"></span>{/if}
        </button>
      </div>
      <button class="pw-feed-x" onclick={() => showNotifications = false} aria-label="Close"><Icon name="x" size={15} /></button>
    </div>

    {#if feedTab === 'activity'}
    <!-- slim summary strip (replaces the heavy dark hero) -->
    <div class="pw-feed-strip">
      <span class="pw-feed-strip-live"><span class="pw-pulse-green"></span> live</span>
      <span class="pw-feed-strip-main"><b>{unreadCount}</b> unread</span>
      <span class="pw-feed-strip-sep">·</span>
      <span class="pw-feed-strip-stat pw-fs-ok">{notifications.filter(n=>n.type==='success').length} ✓</span>
      <span class="pw-feed-strip-stat pw-fs-warn">{notifications.filter(n=>n.type==='warn').length} ⚠</span>
      <span class="pw-feed-strip-stat pw-fs-err">{notifications.filter(n=>n.type==='error').length} ✕</span>
      <span class="pw-feed-strip-tot">{notifications.length} total</span>
    </div>

    <div class="pw-feed-toolbar">
      <div class="pw-feed-chips">
        {#each [['all','All'],['unread','Unread'],['training','Training'],['ml','ML'],['alerts','Alerts']] as [k,l]}
          <button class="pw-feed-chip" class:pw-feed-chip-active={feedFilter === k} onclick={() => feedFilter = k as any}>{l}</button>
        {/each}
      </div>
      {#if unreadCount > 0}
        <button onclick={markAllRead} class="pw-feed-link">Mark all read</button>
      {/if}
    </div>

    <div class="pw-agents-body">
      {#if feedFiltered().length > 0}
        {#each feedFiltered() as n}
          <div class="pw-feed-card" style="--feed-accent: {ntypeColor(n.type)};">
            <div class="pw-feed-icon" style="color: {ntypeColor(n.type)}; background: color-mix(in srgb, {ntypeColor(n.type)} 14%, transparent);">
              {n.type === 'success' ? '✓' : n.type === 'warn' ? '⚠' : n.type === 'error' ? '✕' : 'i'}
            </div>
            <div class="pw-feed-card-text">
              <div class="pw-feed-card-top">
                <span class="pw-feed-card-title" class:pw-feed-card-unread={!n.read}>{n.title}</span>
                <span class="pw-feed-card-time">{relativeTime(n.created_at)}</span>
              </div>
              {#if n.message}<div class="pw-feed-card-msg">{n.message}</div>{/if}
            </div>
            {#if !n.read}<span class="pw-feed-unread-dot" title="Unread"></span>{/if}
          </div>
        {/each}
      {:else}
        <div class="pw-feed-empty-card">
          <div style="font-size: 30px; opacity: 0.3;">◌</div>
          <div style="font-weight: 600; margin-top: 8px; color: var(--pw-ink);">No new activity</div>
          <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px;">Training, alerts, ML runs, drift — all surface here.</div>
        </div>
      {/if}
    </div>

    <div class="pw-agents-footer">
      <a href="/ui/notifications" onclick={() => showNotifications = false} class="pw-feed-link">View all events →</a>
    </div>
    {:else}
    <!-- What's new tab: build/version + data freshness + release notes -->
    <div class="pw-agents-body pw-feed-wn">
      <VersionCard />
    </div>
    {/if}
  </aside>
{/if}

{#if showSearch}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div style="position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 200; display: flex; align-items: flex-start; justify-content: center; padding-top: 100px;" onclick={(e) => { if (e.target === e.currentTarget) showSearch = false; }}>
  <div style="background: var(--pw-surface); width: 540px; max-height: 520px; overflow-y: auto; border: 1px solid var(--pw-border); border-radius: var(--pw-radius); box-shadow: var(--pw-shadow-lg); font-family: var(--pw-font-body);">
    <div style="padding: 14px 18px; border-bottom: 1px solid var(--pw-border);">
      <input type="text" bind:value={searchQuery} oninput={doSearch} placeholder="Search projects, chats, tables…" autofocus style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 14px; font-family: var(--pw-font-body); font-size: 14.5px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
    </div>
    {#if searchResults.length > 0}
      {#each searchResults as r}
        <a href={r.url} onclick={() => showSearch = false} style="display: block; padding: 12px 18px; border-bottom: 1px solid var(--pw-border-soft); text-decoration: none; color: var(--pw-ink); cursor: pointer;" onmouseenter={(e) => (e.currentTarget as HTMLElement).style.background = 'var(--pw-surface-warm)'} onmouseleave={(e) => (e.currentTarget as HTMLElement).style.background = 'transparent'}>
          <div style="display: flex; align-items: center; gap: 8px;">
            <span style="font-size: 11px; padding: 2px 8px; background: var(--pw-bg-alt); color: var(--pw-ink-soft); border-radius: var(--pw-radius-pill);">{r.type}</span>
            <span style="font-size: 13px; font-weight: 500;">{r.title}</span>
          </div>
          <div style="font-size: 12.5px; color: var(--pw-muted); margin-top: 4px;">{r.subtitle}</div>
        </a>
      {/each}
    {:else if searchQuery.length >= 2}
      <div style="padding: 28px 18px; text-align: center; font-size: 12px; color: var(--pw-muted);">No results for "{searchQuery}".</div>
    {:else}
      <div style="padding: 28px 18px; text-align: center; font-size: 12px; color: var(--pw-muted);">Type to search across projects, chats, and data.</div>
    {/if}
  </div>
</div>
{/if}

{#if isLogin}
  {@render children()}

{:else if checking}
  <div style="min-height: 100vh; background: var(--pw-bg); display: flex; align-items: center; justify-content: center; font-family: var(--pw-font-body); font-size: 13px; color: var(--pw-muted);">
    Loading…
  </div>
{:else if authenticated}
  <div class="flex flex-col h-screen" style="background: var(--pw-bg); font-family: var(--pw-font-body); color: var(--pw-ink);">
    <!-- Header -->
    <header class="flex items-center justify-between shrink-0 pw-header">
      <div class="flex items-center gap-3 pw-header-left">
        <button class="pw-brand-btn" onclick={() => navTo('/ui/home')} title="Home">
          <img src="/brand/cityagent.png?v=3" alt={productName} class="pw-brand-img" />
        </button>

        <!-- Desktop Nav -->
        <nav class="pw-nav-row" onclick={(e) => e.stopPropagation()}>
        {#if singleAgent}
          {#if canDashboard}
          <button onclick={() => navTo(overviewHref)} class="pw-nav" class:pw-nav-active={page.url.pathname.includes('/overview')}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
            <span class="pw-nav-label">Dashboard</span>
          </button>
          {/if}
          {#if canChat}
          <button onclick={() => navTo(chatHref)} class="pw-nav" class:pw-nav-active={page.url.pathname.includes('/project/') && !page.url.pathname.includes('/settings') && !page.url.pathname.includes('/overview')}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
            <span class="pw-nav-label">Chat</span>
          </button>
          {/if}
          <!-- Data Source top button removed — it lives as the DATA SOURCE tab inside Workspace's left rail. -->
          {#if canWorkspace}
          <button onclick={() => { openMenu = null; window.location.href = agentBrainHref; }} class="pw-nav" class:pw-nav-active={page.url.pathname.includes('/settings')}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
            <span class="pw-nav-label">Workspace</span>
          </button>
          {/if}
          <!-- Integrations live under Admin → Integrations only (unified hub). -->
          {#if canAdminGroup}
            <div class="pw-nav-group" class:pw-group-active={isAdminGroupActive}>
              <button class="pw-nav"
                      class:pw-nav-active={isAdminGroupActive && openMenu !== 'admingrp'}
                      onclick={() => toggleMenu('admingrp')}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5z"/></svg>
                <span class="pw-nav-label">Admin</span>
                {#if adminCounts.skill_drafts > 0}<span class="pw-badge-coral">{adminCounts.skill_drafts}</span>{/if}
                <svg class="pw-chev" class:pw-chev-open={openMenu === 'admingrp'} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
              </button>
              {#if openMenu === 'admingrp'}
                <div class="pw-menu">
                  {#if canAdminConsole}
                    <div class="pw-menu-label">{isSuper ? 'Super admin' : 'Admin'}</div>
                    <button class="pw-menu-row" class:pw-menu-active={isAdminActive} onclick={() => navTo('/ui/command-center')}>
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                      <div class="pw-menu-text">
                        <span class="pw-menu-name">Admin Console{#if adminCounts.skill_drafts > 0}<span class="pw-badge-coral">{adminCounts.skill_drafts}</span>{/if}</span>
                        <span class="pw-menu-sub">Governance, traces, audit, LLM config</span>
                      </div>
                    </button>
                  {/if}
                  {#if canUsers || canUsage}
                    <div class="pw-menu-label">People</div>
                  {/if}
                  {#if canUsers}
                  <button class="pw-menu-row" class:pw-menu-active={routeMatches('/users')} onclick={() => navTo('/ui/users')}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                    <div class="pw-menu-text">
                      <span class="pw-menu-name">Users &amp; Access</span>
                      <span class="pw-menu-sub">Accounts + roles — create, reset, grant admin</span>
                    </div>
                  </button>
                  {/if}
                  {#if canUsage}
                    <button class="pw-menu-row" class:pw-menu-active={isUsageActive} onclick={() => navTo('/ui/usage')}>
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><path d="M3 20h18"/></svg>
                      <div class="pw-menu-text">
                        <span class="pw-menu-name">Usage &amp; Cost</span>
                        <span class="pw-menu-sub">Tokens · cost · requests — API keys, embeddings, platform, training</span>
                      </div>
                    </button>
                  {/if}
                </div>
              {/if}
            </div>
          {/if}
        {:else}
          <button onclick={() => navTo('/ui/projects')} class="pw-nav" class:pw-nav-active={isProjectsActive}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>
            Projects
          </button>
          {#if canChat}
          <button onclick={() => navTo('/ui/chat')} class="pw-nav" class:pw-nav-active={isChatActive}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
            Chat
          </button>
          {/if}

          <!-- Build dropdown -->
          <div class="pw-nav-group" class:pw-group-active={isBuildActive}>
            <button class="pw-nav" class:pw-nav-active={isBuildActive && openMenu !== 'build'} onclick={() => toggleMenu('build')}>
              Build
              <svg class="pw-chev" class:pw-chev-open={openMenu === 'build'} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            {#if openMenu === 'build'}
              <div class="pw-menu">
                {#if canDashboard}
                <button class="pw-menu-row" class:pw-menu-active={routeMatches('/dashboard')} onclick={() => navTo('/ui/dashboard')}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>
                  <div class="pw-menu-text">
                    <span class="pw-menu-name">Dashboards</span>
                    <span class="pw-menu-sub">Live KPI canvases</span>
                  </div>
                </button>
                {/if}
                <button class="pw-menu-row" class:pw-menu-active={routeMatches('/presentations')} onclick={() => navTo('/ui/presentations')}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                  <div class="pw-menu-text">
                    <span class="pw-menu-name">Presentations</span>
                    <span class="pw-menu-sub">Slide decks from chat</span>
                  </div>
                </button>
                <button class="pw-menu-row" class:pw-menu-active={routeMatches('/skills')} onclick={() => navTo('/ui/skills')}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
                  <div class="pw-menu-text">
                    <span class="pw-menu-name">Skills</span>
                    <span class="pw-menu-sub">10 builtin + drafted</span>
                  </div>
                </button>
              </div>
            {/if}
          </div>

          <!-- Knowledge dropdown -->
          <div class="pw-nav-group" class:pw-group-active={isKnowledgeActive}>
            <button class="pw-nav" class:pw-nav-active={isKnowledgeActive && openMenu !== 'knowledge'} onclick={() => toggleMenu('knowledge')}>
              Knowledge
              <svg class="pw-chev" class:pw-chev-open={openMenu === 'knowledge'} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            {#if openMenu === 'knowledge'}
              <div class="pw-menu">
                <button class="pw-menu-row" class:pw-menu-active={routeMatches('/brain') || page.url.hash.startsWith('#brain-')} onclick={() => { openMenu = null; window.location.href = singleAgent && lockedSlug ? `/ui/project/${lockedSlug}/settings#brain-glossary` : '/ui/brain'; }}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5v1a3 3 0 0 0 6 0V2H9zM15 2a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-2 5v1a3 3 0 0 1-6 0"/></svg>
                  <div class="pw-menu-text">
                    <span class="pw-menu-name">Brain</span>
                    <span class="pw-menu-sub">Glossary, formulas, org map</span>
                  </div>
                </button>
                <button class="pw-menu-row" class:pw-menu-active={routeMatches('/embed-templates')} onclick={() => navTo('/ui/embed-templates')}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                  <div class="pw-menu-text">
                    <span class="pw-menu-name">Embed Templates</span>
                    <span class="pw-menu-sub">RLS bundles · custom rules · reusable</span>
                  </div>
                </button>
              </div>
            {/if}
          </div>

          {#if isSuper}
            <div class="pw-nav-group" class:pw-group-active={isAdminActive}>
              <button class="pw-nav" class:pw-nav-active={isAdminActive} onclick={() => { openMenu = null; navTo('/ui/command-center'); }} title="Admin — Governance · Agent OS · Telemetry · System">
                Admin
                {#if adminCounts.skill_drafts > 0}
                  <span class="pw-badge-coral">{adminCounts.skill_drafts}</span>
                {/if}
              </button>
            </div>
          {/if}
        {/if}
        </nav>

        <!-- Mobile hamburger -->
        <button class="pw-hamburger" onclick={(e) => { e.stopPropagation(); mobileNavOpen = !mobileNavOpen; }} title="Menu" aria-label="Open navigation">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
        </button>
      </div>

      <div class="flex items-center gap-2 pw-header-right" onclick={(e) => e.stopPropagation()}>
        {#if activeScope}
          <button
            onclick={switchScope}
            title="Click to switch scope"
            aria-label="Switch active scope"
            class="pw-scope-chip"
          >
            <span class="pw-scope-dot"></span>
            <span>Scope: {activeScope.label}</span>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
          </button>
        {/if}

        <!-- Agents online + sub-agents combined pill (live from OS Hub) — hidden for single-agent product -->
        {#if !singleAgent}
        <button
          class="pw-agents-pill"
          title="View fleet — {agentsOnline ?? '—'} of {agentsTotal ?? '—'} active · {subAgentCount ?? '—'} sub-agents"
          onclick={(e) => { e.stopPropagation(); navTo('/ui/os?tab=overview'); }}
        >
          {#if osHubFetchFailed || agentsOnline === null}
            <span class="pw-pulse-green" style="background:#9ca3af;"></span>
            <span>— agents · — sub-agents</span>
          {:else}
            <span class="pw-pulse-green" style={agentsTotal !== null && agentsOnline === agentsTotal ? '' : 'background:#f59e0b;'}></span>
            <span>{agentsOnline} agents · {subAgentCount ?? 0} sub-agents</span>
          {/if}
        </button>
        {/if}

        <!-- Feed bell — Activity (notifications) + What's new (releases) -->
        <button onclick={(e) => { e.stopPropagation(); if (showNotifications) { showNotifications = false; } else { openFeed('activity'); } }} class="pw-feed-btn" title="Notifications & what's new">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
          <span>Feed</span>
          {#if unreadCount > 0}
            <span class="pw-feed-count">{unreadCount > 99 ? '99+' : unreadCount}</span>
          {:else if versionIsNew}
            <span class="pw-feed-dot" title="New version"></span>
          {/if}
        </button>

        <!-- Show Terminal button (only when CLI is hidden by user on a CLI-enabled route) -->
        {#if showCli && !cliVisible}
          <button class="pw-show-cli-btn" onclick={manualShow} title="Show terminal">
            <span style="font-family: 'JetBrains Mono', monospace;"></span>
            <span>Show terminal</span>
          </button>
        {/if}

        <!-- User chip (role folded in as sub-line — replaces standalone tier pill) -->
        <div class="pw-nav-group">
          <button class="pw-user-chip" onclick={() => toggleMenu('user')} title="Account">
            <span class="pw-user-avatar">{username.charAt(0).toUpperCase()}</span>
            <span class="pw-user-id" class:pw-user-id-bare={!isAdmin}>
              <span class="pw-user-name">{username}</span>
              {#if isSuper}
                <span class="pw-user-tier pw-user-tier-super">🔒 SUPER ADMIN</span>
              {:else if isAdmin}
                <span class="pw-user-tier pw-user-tier-admin">⚙ ADMIN</span>
              {/if}
            </span>
            <svg class="pw-chev" class:pw-chev-open={openMenu === 'user'} width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </button>
          {#if openMenu === 'user'}
            <div class="pw-menu pw-menu-right" style="width: 240px;">
              <div class="pw-menu-ident">
                <span class="pw-menu-ident-name">{username}</span>
                <span class="pw-menu-ident-role" class:is-super={isSuper} class:is-admin={isAdmin && !isSuper}>
                  {isSuper ? 'SUPER ADMIN' : (isAdmin ? 'ADMIN' : 'USER')}
                </span>
              </div>
              <div class="pw-menu-divider"></div>
              <button class="pw-menu-row" onclick={() => navTo('/ui/profile')}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="10" r="3"/><path d="M7 20.5a5 5 0 0 1 10 0"/></svg>
                <div class="pw-menu-text"><span class="pw-menu-name">Profile &amp; Settings</span><span class="pw-menu-sub">Profile · account · password</span></div>
              </button>
              <div class="pw-menu-divider"></div>
              <button class="pw-menu-row" onclick={() => { closeMenus(); logout(); }}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
                <div class="pw-menu-text"><span class="pw-menu-name">Sign out</span></div>
              </button>
            </div>
          {/if}
        </div>

      </div>
    </header>

    <!-- Agents drawer (dramatic) -->
    {#if showAgentsDrawer}
      <div class="pw-agents-backdrop" onclick={() => showAgentsDrawer = false}></div>
      <aside class="pw-agents-drawer" onclick={(e) => e.stopPropagation()}>
        <button class="pw-agents-close" onclick={() => showAgentsDrawer = false} aria-label="Close"><Icon name="x" size={14} /></button>
        <div class="pw-agents-hero">
          <div class="pw-agents-hero-glow"></div>
          <div class="pw-agents-hero-num">{_agentsList.reduce((n,g)=>n+g.items.length,0)}</div>
          <div class="pw-agents-hero-label">agents online</div>
          <div class="pw-agents-hero-meta">
            <span class="pw-pulse-green"></span> live · {_agentsList.length} teams · 31 tools
          </div>
          <div class="pw-agents-hero-stats">
            <div class="pw-agents-stat"><span class="pw-agents-stat-num">6</span><span class="pw-agents-stat-lbl">core</span></div>
            <div class="pw-agents-stat"><span class="pw-agents-stat-num">10</span><span class="pw-agents-stat-lbl">specialists</span></div>
            <div class="pw-agents-stat"><span class="pw-agents-stat-num">7</span><span class="pw-agents-stat-lbl">background</span></div>
            <div class="pw-agents-stat"><span class="pw-agents-stat-num">5</span><span class="pw-agents-stat-lbl">upload</span></div>
          </div>
        </div>
        <div class="pw-agents-body">
          {#each _agentsList as g, gi}
            <div class="pw-agents-group" style="--group-accent: {['#c96342','#3a8dff','#9b6dff','#10b981','#f59e0b'][gi % 5]};">
              <div class="pw-agents-group-head">
                <span class="pw-agents-group-bar"></span>
                <span class="pw-agents-group-name">{g.group}</span>
                <span class="pw-agents-group-count">{g.items.length}</span>
              </div>
              <div class="pw-agents-grid">
                {#each g.items as a}
                  <div class="pw-agents-card">
                    <div class="pw-agents-avatar">{a.name.split(' ').map(s=>s[0]).slice(0,2).join('')}</div>
                    <div class="pw-agents-card-text">
                      <div class="pw-agents-name">
                        {a.name}
                        <span class="pw-agents-status">●</span>
                      </div>
                      <div class="pw-agents-role">{a.role}</div>
                    </div>
                  </div>
                {/each}
              </div>
            </div>
          {/each}
        </div>
      </aside>
    {/if}

    <!-- Mobile slide-out nav -->
    {#if mobileNavOpen}
      <div class="pw-mobile-backdrop" onclick={() => mobileNavOpen = false}></div>
      <aside class="pw-mobile-panel" onclick={(e) => e.stopPropagation()}>
        <div class="pw-mobile-header">
          <span class="pw-wordmark">{productName}</span>
          <button class="pw-icon-btn" onclick={() => mobileNavOpen = false}><Icon name="x" size={14} /></button>
        </div>
        <button class="pw-mobile-row" onclick={() => navTo('/ui/projects')}>Projects</button>
        <button class="pw-mobile-row" onclick={() => navTo('/ui/chat')}>Chat</button>
        {#if singleAgent}
          <button class="pw-mobile-row" onclick={() => { mobileNavOpen = false; window.location.href = overviewHref; }}>Dashboard</button>
          <button class="pw-mobile-row" onclick={() => { mobileNavOpen = false; window.location.href = agentBrainHref; }}>Workspace</button>
        {/if}
        <div class="pw-mobile-section">Build</div>
        <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/dashboard')}>Dashboards</button>
        <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/presentations')}>Presentations</button>
        <div class="pw-mobile-section">Knowledge</div>
        <button class="pw-mobile-row pw-mobile-sub" onclick={() => { mobileNavOpen = false; window.location.href = singleAgent && lockedSlug ? `/ui/project/${lockedSlug}/settings#brain-glossary` : '/ui/brain'; }}>Brain</button>
        {#if isSuper}
          <div class="pw-mobile-section">Admin</div>
          <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/command-center')}>Command Center</button>
          <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/admin/accuracy')}>Accuracy trend</button>
          <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/admin/golden')}>Golden Q&amp;A</button>
          <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/admin/mdl')}>MDL editor</button>
          <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/admin/diff')}>Version diffs</button>
          <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/admin/scope-audit')}>Chat scope audit</button>
          <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/admin/approvals')}>Action approvals</button>
          <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/admin/actions')}>Action registry</button>
          <button class="pw-mobile-row pw-mobile-sub" onclick={() => navTo('/ui/admin/metricflow')}>MetricFlow import</button>
        {/if}
      </aside>
    {/if}

    <!-- Main Content with Claude-style sidebar (chat routes only) -->
    <div class="flex flex-1 overflow-hidden">
      {#if isChatRoute}
        <aside class="dash-sidebar" class:collapsed={sidebarCollapsed}>
          <button class="sb-newchat" onclick={newChatFromSidebar} title="New chat">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            <span>New chat</span>
          </button>
          <button class="sb-search" onclick={() => showChatSearch = true} title="Search chats (Cmd-K)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <span>Search</span>
          </button>
          <hr class="sb-div" />
          <div class="sb-recents">
            {#each groupedRecents as g (g.label)}
              <div class="sb-group-label">{g.label}</div>
              {#each g.items as r (r.session_id)}
                <button class="sb-row" class:active={r.session_id === activeSession} onclick={() => openSession(r.session_id)} title={recentTitle(r)}>
                  {recentTitle(r)}
                </button>
              {/each}
            {/each}
            {#if groupedRecents.length === 0}
              <div class="sb-empty">No chats yet</div>
            {/if}
          </div>
          <button class="sb-collapse" onclick={() => sidebarCollapsed = !sidebarCollapsed} title={sidebarCollapsed ? 'Expand' : 'Collapse'}>
            {sidebarCollapsed ? '›' : '‹'}
          </button>
        </aside>
      {/if}
      <main class="flex-1 overflow-y-auto overflow-x-hidden">
        {@render children()}
      </main>
    </div>
    {#if isChatRoute}
      <SearchModal bind:open={showChatSearch} {recents} />
    {/if}

    <!-- Bottom CONSOLE bar DISABLED — the floating RobotPanel is now the single CLI surface (no separate window). -->
    {#if false && showCli && cliVisible && cliHasActivity}
      {@const _errCount = cliLogs.filter(l => /\b(error|err|fail|✗|✕)\b/i.test((l as any).text || '')).length}
      {@const _warnCount = cliLogs.filter(l => /\b(warn|warning|⚠)\b/i.test((l as any).text || '')).length}
      {@const _evtCount = cliLogs.length}
      {@const _visible = cliLogs.slice(-cliMaxLines)}
      <div class="cn-bar {cliExpanded ? 'cn-bar--open' : 'cn-bar--idle'}">
        {#if cliExpanded}
          <div class="cn-head" onclick={(e) => { if ((e.target as HTMLElement)?.closest('.cn-ctrls')) return; manualMinimize(); }}>
            <div class="cn-head-left">
              <span class="cn-collapse-arrow">▼</span>
              <span class="cn-title">CONSOLE</span>
              <span class="cn-sep">·</span>
              <span class="cn-evt"><b>{_evtCount}</b> events</span>
              <span class="cn-sep">·</span>
              <span class="cn-state {cliActive ? 'cn-state--active' : ''}">{cliActive ? 'active' : 'idle'}</span>
              <span class="cn-sep">·</span>
              <span class="cn-err">errors <b>{_errCount}</b></span>
              <span class="cn-sep">·</span>
              <span class="cn-warn">warn <b>{_warnCount}</b></span>
            </div>
            <div class="cn-ctrls" onclick={(e) => e.stopPropagation()}>
              <label class="cn-chk"><input type="checkbox" bind:checked={cliAutoscroll} /> autoscroll</label>
              <button class="cn-btn" onclick={() => { cliPaused = !cliPaused; }}>{cliPaused ? '▶ resume' : '❚❚ pause'}</button>
              <button class="cn-btn" onclick={clearCli}>clear</button>
              <button class="cn-btn" onclick={copyCli}>↓ csv</button>
              <select class="cn-sel" bind:value={cliMaxLines}>
                <option value={100}>100</option>
                <option value={200}>200</option>
                <option value={500}>500</option>
                <option value={1000}>1000</option>
              </select>
              <button class="cn-btn cn-btn--icon" onclick={hideCli} title="Close">✕</button>
            </div>
          </div>
          <div class="cn-body-wrap">
            <div class="cn-body" bind:this={cliScrollEl} onscroll={onCliScroll}>
              {#if _evtCount === 0}
                <div class="cn-placeholder">Waiting for events...</div>
              {:else}
                {#each _visible as line}
                  {#if (line as any).fields}
                    <div class="cn-line cn-line--struct {lineKind(line)}">
                      <span class="cn-ts">{formatTs((line as any).ts || Date.now())}</span>
                      <span class="cn-col cn-col-entity">{(line as any).fields.entity}</span>
                      <span class="cn-col cn-col-action">{((line as any).fields.action || '').toUpperCase()}</span>
                      <span class="cn-col cn-col-model">{(line as any).fields.model}</span>
                      <span class="cn-col cn-col-dur">{(line as any).fields.duration}</span>
                      <span class="cn-col cn-col-cost">{(line as any).fields.cost}</span>
                    </div>
                  {:else}
                    <div class="cn-line {lineKind(line)}">
                      <span class="cn-ts">{formatTs((line as any).ts || Date.now())}</span>
                      <span class="cn-msg">{(line as any).text || line}</span>
                    </div>
                  {/if}
                {/each}
              {/if}
            </div>
            {#if _userScrolledUp}
              <button class="cn-jump" onclick={jumpToLatest}>↓ jump to latest</button>
            {/if}
          </div>
        {:else}
          <button class="cn-pill" onclick={() => { cliExpanded = true; _userPinned = true; setTimeout(() => { _userPinned = false; }, 30_000); }}>
            <span class="cn-pill-dot {cliActive ? 'cn-pill-dot--active' : ''}"></span>
            <span class="cn-pill-label">Console</span>
            <span class="cn-pill-evt"><b>{_evtCount}</b> events</span>
          </button>
        {/if}
      </div>
    {/if}
    <footer class="shrink-0 flex items-center justify-between px-5 pw-footer">
      <div class="flex items-center gap-2">
        {#if srvTraining}
          <span class="pw-pulse-dot pw-pulse-dot-warn"></span>
          <span>Training in progress</span>
        {:else}
          <span class="pw-pulse-dot"></span>
          <span>System active</span>
        {/if}
      </div>
      <div style="color: var(--pw-muted); display: flex; align-items: center; gap: 4px;">
        <span>{productName} Analyst can make mistakes. Verify critical information. · © 2026 {productName}</span>
        <VersionBadge variant="footer" />
      </div>
    </footer>
  </div>
{/if}

<!-- ── ONE auto-train robot — Dashboard + Integration screens only ── -->
{#if showRobot && lockedSlug}
  <FloatingRobot slug={lockedSlug} />
{/if}

<style>
 /* ───── Claude-style chat sidebar ───── */
 .dash-sidebar {
 width: 240px;
 background: var(--pw-sidebar, #f7f6f3);
 border-right: 1px solid var(--pw-border);
 display: flex;
 flex-direction: column;
 padding: 12px 8px;
 transition: width 0.15s ease;
 flex-shrink: 0;
 }
 .dash-sidebar.collapsed { width: 56px; padding: 12px 4px; }
 .dash-sidebar.collapsed .sb-label,
 .dash-sidebar.collapsed .sb-row,
 .dash-sidebar.collapsed .sb-newchat span,
 .dash-sidebar.collapsed .sb-search span { display: none; }
 .sb-newchat, .sb-search {
 display: flex; align-items: center; gap: 10px;
 width: 100%; padding: 8px 12px;
 border-radius: 0; border: 0;
 background: transparent; color: var(--pw-ink);
 font: inherit; font-size: 13px;
 cursor: pointer; text-align: left;
 }
 .sb-newchat:hover, .sb-search:hover {
 background: rgba(201, 99, 66, 0.08);
 color: var(--pw-accent, #c96342);
 }
 .sb-div { border: 0; border-top: 1px solid var(--pw-border); margin: 14px 8px; }
 .sb-label {
 font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
 color: var(--pw-muted, #807a72); padding: 4px 12px;
 }
 .sb-recents {
 flex: 1 1 0;
 min-height: 0;
 overflow-y: auto;
 overflow-x: hidden;
 display: flex;
 flex-direction: column;
 gap: 2px;
 padding-right: 4px;
 scrollbar-width: thin;
 scrollbar-color: var(--pw-border) transparent;
 }
 .sb-recents::-webkit-scrollbar { width: 8px; }
 .sb-recents::-webkit-scrollbar-thumb { background: var(--pw-border); border-radius: 4px; }
 .sb-recents::-webkit-scrollbar-thumb:hover { background: var(--pw-muted); }
 .sb-group-label {
 font-size: 10.5px;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-muted, #807a72);
 padding: 10px 12px 4px;
 font-weight: 600;
 }
 .sb-group-label:first-child { padding-top: 4px; }
 .sb-empty {
 padding: 12px;
 font-size: 12px;
 color: var(--pw-muted);
 text-align: center;
 }
 .sb-row {
 display: block; width: 100%; padding: 7px 12px;
 border: 0; background: transparent; color: var(--pw-ink);
 font: inherit; font-size: 12px; line-height: 1.35; text-align: left;
 border-radius: 0; cursor: pointer;
 white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
 flex-shrink: 0; min-height: 28px; box-sizing: border-box;
 }
 .sb-group-label { flex-shrink: 0; }
 .sb-row:hover { background: var(--pw-bg-alt); }
 .sb-row.active {
 background: rgba(201, 99, 66, 0.10);
 color: var(--pw-accent, #c96342);
 font-weight: 600;
 }
 .sb-collapse {
 margin-top: 8px; align-self: flex-start;
 padding: 6px 10px; border-radius: 0; border: 0;
 background: transparent; cursor: pointer;
 color: var(--pw-muted);
 font-size: 14px; line-height: 1;
 }
 .sb-collapse:hover { background: var(--pw-bg-alt); }

 /* ───── Header ───── */
 .pw-header {
 background: var(--pw-bg-alt);
 border-bottom: 1px solid var(--pw-border);
 padding: 10px 20px;
 position: sticky;
 top: 0;
 z-index: 50;
 gap: 8px;
 }
 /* Left group (logo + nav) can shrink; nav-row shrinks before user cluster disappears */
 .pw-header-left {
 min-width: 0;
 flex: 1 1 auto;
 }
 /* Right cluster (feed, bell, user menu) is ALWAYS visible — never clipped */
 .pw-header-right {
 flex-shrink: 0;
 }
 .pw-logomark {
 width: 32px;
 height: 32px;
 border-radius: var(--pw-radius-sm);
 background: var(--pw-accent);
 color: #fff;
 display: flex;
 align-items: center;
 justify-content: center;
 font-family: var(--pw-font-headline);
 font-size: 14px;
 font-weight: 500;
 }
 .pw-brand-img {
 height: 56px;
 width: auto;
 max-width: 240px;
 object-fit: contain;
 display: block;
 }
 .pw-agents-backdrop {
 position: fixed; inset: 0; background: rgba(20,12,8,0.55);
 z-index: 9998; backdrop-filter: blur(6px);
 animation: pw-fade-in 0.22s ease-out;
 }
 @keyframes pw-fade-in { from { opacity: 0; } to { opacity: 1; } }
 .pw-agents-drawer {
 position: fixed; top: 12px; right: 12px; bottom: 12px;
 width: 430px; max-width: calc(100vw - 24px); height: auto;
 background: var(--pw-bg, #fff);
 border: 1px solid var(--pw-border);
 border-radius: 16px;
 box-shadow: 0 24px 64px rgba(40,20,10,0.22);
 z-index: 9999;
 display: flex; flex-direction: column; overflow: hidden;
 animation: pw-slide-in 0.26s cubic-bezier(0.22, 0.94, 0.32, 1.0);
 }
 @keyframes pw-slide-in { from { transform: translateX(24px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
 /* mobile: full-bleed sheet */
 @media (max-width: 640px) {
   .pw-agents-drawer { top: 0; right: 0; bottom: 0; width: 100vw; max-width: 100vw; border-radius: 0; border: none; }
 }
 .pw-agents-close {
 position: absolute; top: 14px; right: 16px; z-index: 2;
 width: 32px; height: 32px; border-radius: 50%;
 background: rgba(255,255,255,0.7); border: 1px solid var(--pw-border);
 color: var(--pw-ink); cursor: pointer; font-size: 13px;
 backdrop-filter: blur(4px);
 transition: background 0.15s, transform 0.15s;
 }
 .pw-agents-close:hover { background: #fff; transform: scale(1.08); }

 .pw-agents-hero {
 position: relative;
 padding: 36px 28px 22px;
 background:
 radial-gradient(ellipse at 30% 0%, rgba(201,99,66,0.18) 0%, transparent 60%),
 linear-gradient(180deg, #2a2522 0%, #1a1614 100%);
 color: #fff;
 overflow: hidden;
 }
 .pw-agents-hero-glow {
 position: absolute; top: -40%; right: -20%;
 width: 400px; height: 400px;
 background: radial-gradient(circle, rgba(201,99,66,0.35) 0%, transparent 65%);
 filter: blur(20px);
 animation: pw-glow-pulse 4s ease-in-out infinite;
 }
 @keyframes pw-glow-pulse {
 0%, 100% { opacity: 0.6; transform: scale(1); }
 50% { opacity: 1.0; transform: scale(1.08); }
 }
 .pw-agents-hero-num {
 font-family: var(--pw-font-serif), Georgia, serif;
 font-size: 72px; font-weight: 400; line-height: 1; color: #fff;
 text-shadow: 0 2px 24px rgba(201,99,66,0.4);
 position: relative;
 }
 .pw-agents-hero-label {
 font-size: 12px; text-transform: uppercase; letter-spacing: 0.18em;
 color: rgba(255,255,255,0.7); margin-top: 4px;
 }
 .pw-agents-hero-meta {
 margin-top: 14px; font-size: 11px; color: rgba(255,255,255,0.6);
 display: flex; align-items: center; gap: 8px;
 }
 .pw-agents-hero-stats {
 display: grid; grid-template-columns: repeat(4, 1fr);
 gap: 8px; margin-top: 20px;
 }
 .pw-agents-stat {
 display: flex; flex-direction: column; align-items: center;
 padding: 10px 4px;
 background: rgba(255,255,255,0.06);
 border: 1px solid rgba(255,255,255,0.1);
 border-radius: 0;
 backdrop-filter: blur(4px);
 }
 .pw-agents-stat-num { font-family: var(--pw-font-serif), Georgia, serif; font-size: 19px; color: #fff; line-height: 1; }
 .pw-agents-stat-lbl { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: rgba(255,255,255,0.55); margin-top: 4px; }

 .pw-agents-body { flex: 1; overflow-y: auto; padding: 8px 0 32px; }
 .pw-agents-group { padding: 16px 24px 8px; }
 .pw-agents-group-head {
 display: flex; align-items: center; gap: 10px;
 margin-bottom: 12px;
 }
 .pw-agents-group-bar {
 width: 3px; height: 14px; background: var(--group-accent); border-radius: 0;
 }
 .pw-agents-group-name {
 font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em;
 color: var(--group-accent); font-weight: 700;
 }
 .pw-agents-group-count {
 margin-left: auto; font-size: 11px; color: var(--pw-muted);
 background: rgba(0,0,0,0.04); padding: 2px 8px; border-radius: 0;
 }
 .pw-agents-grid { display: grid; grid-template-columns: 1fr; gap: 6px; }
 .pw-agents-card {
 display: flex; align-items: flex-start; gap: 12px;
 padding: 10px 12px; border-radius: 0;
 background: rgba(255,255,255,0.5);
 border: 1px solid transparent;
 transition: background 0.15s, border-color 0.15s, transform 0.15s;
 cursor: default;
 }
 .pw-agents-card:hover {
 background: #fff;
 border-color: var(--group-accent);
 transform: translateX(-2px);
 box-shadow: 0 2px 8px rgba(0,0,0,0.04);
 }
 .pw-agents-avatar {
 width: 32px; height: 32px; border-radius: 0;
 display: flex; align-items: center; justify-content: center;
 background: linear-gradient(135deg, var(--group-accent), color-mix(in srgb, var(--group-accent) 60%, #000));
 color: #fff; font-weight: 600; font-size: 11px;
 letter-spacing: 0.02em; flex-shrink: 0;
 box-shadow: 0 2px 6px rgba(0,0,0,0.08);
 }
 .pw-agents-card-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
 .pw-agents-name {
 font-size: 13.5px; color: var(--pw-ink); font-weight: 600;
 display: flex; align-items: center; gap: 6px;
 }
 .pw-agents-status { color: #10b981; font-size: 11px; line-height: 1; }

 .pw-feed-toolbar {
 display: flex; align-items: center; justify-content: space-between;
 padding: 12px 20px 8px;
 border-bottom: 1px solid var(--pw-border);
 background: rgba(0,0,0,0.015);
 }
 .pw-feed-chips { display: flex; gap: 6px; flex-wrap: wrap; }
 .pw-feed-chip {
 padding: 5px 11px; font-size: 11.5px; font-weight: 500;
 background: transparent; color: var(--pw-muted);
 border: 1px solid var(--pw-border); border-radius: 0;
 cursor: pointer; transition: all 0.15s;
 }
 .pw-feed-chip:hover { background: rgba(0,0,0,0.04); color: var(--pw-ink); }
 .pw-feed-chip-active {
 background: var(--pw-accent); color: #fff; border-color: var(--pw-accent);
 }

 .pw-feed-card {
 display: flex; align-items: flex-start; gap: 12px;
 padding: 12px 14px; margin: 6px 12px;
 background: rgba(255,255,255,0.6);
 border: 1px solid transparent; border-left: 3px solid var(--feed-accent);
 border-radius: 0;
 transition: background 0.15s, border-color 0.15s, transform 0.15s;
 cursor: pointer; position: relative;
 }
 .pw-feed-card:hover {
 background: #fff;
 border-color: var(--feed-accent);
 border-left-color: var(--feed-accent);
 transform: translateX(-2px);
 box-shadow: 0 2px 8px rgba(0,0,0,0.04);
 }
 .pw-feed-icon {
 width: 30px; height: 30px; border-radius: 50%;
 display: flex; align-items: center; justify-content: center;
 font-weight: 700; font-size: 14px; line-height: 1; flex-shrink: 0;
 }
 .pw-feed-card-text { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; }
 .pw-feed-card-top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
 .pw-feed-card-title { font-size: 13.5px; color: var(--pw-ink); font-weight: 500; }
 .pw-feed-card-unread { font-weight: 700; }
 .pw-feed-card-time { font-size: 11px; color: var(--pw-muted); flex-shrink: 0; }
 .pw-feed-card-msg { font-size: 11px; color: var(--pw-muted); line-height: 1.4; overflow: hidden; text-overflow: ellipsis; }
 .pw-feed-unread-dot {
 width: 7px; height: 7px; border-radius: 50%;
 background: var(--pw-accent); flex-shrink: 0; margin-top: 6px;
 }
 .pw-feed-empty-card {
 text-align: center; padding: 60px 20px; color: var(--pw-muted);
 }
 .pw-agents-footer {
 border-top: 1px solid var(--pw-border);
 padding: 12px 20px;
 background: rgba(0,0,0,0.015);
 text-align: center;
 }
 .pw-feed-link {
 background: none; border: none; cursor: pointer;
 color: var(--pw-accent); font-weight: 600; font-size: 12.5px;
 text-decoration: none;
 }
 .pw-feed-link:hover { text-decoration: underline; }
 .pw-agents-role { font-size: 11.5px; color: var(--pw-muted); }
 .pw-wordmark {
 font-family: var(--pw-font-headline);
 font-size: 18px;
 font-weight: 500;
 color: var(--pw-ink);
 letter-spacing: -0.01em;
 }

 .pw-nav {
 display: inline-flex;
 align-items: center;
 gap: 8px;
 background: transparent;
 color: var(--pw-ink-soft);
 border: none;
 height: 42px;
 padding: 0 16px;
 font-family: var(--pw-font-body);
 font-size: 13px;
 font-weight: 500;
 border-radius: var(--pw-radius-pill);
 cursor: pointer;
 transition: background 0.15s, color 0.15s;
 }
 .pw-nav:hover {
 background: var(--pw-surface-warm);
 color: var(--pw-ink);
 }
 .pw-nav-active {
 background: rgba(201, 99, 66, 0.14);
 color: var(--pw-accent);
 font-weight: 600;
 border-radius: 0;
 }
 .pw-nav-active:hover {
 background: rgba(201, 99, 66, 0.20);
 color: var(--pw-accent);
 }

 .pw-icon-btn {
 display: inline-flex;
 align-items: center;
 justify-content: center;
 width: 32px;
 height: 32px;
 background: transparent;
 color: var(--pw-ink-soft);
 border: none;
 border-radius: var(--pw-radius-pill);
 cursor: pointer;
 transition: background 0.15s, color 0.15s;
 }
 .pw-icon-btn:hover {
 background: var(--pw-surface-warm);
 color: var(--pw-ink);
 }

 .pw-scope-chip {
 display: inline-flex;
 align-items: center;
 gap: 6px;
 background: var(--pw-accent-soft);
 border: 1px solid var(--pw-accent-soft);
 color: var(--pw-accent-ink);
 padding: 6px 12px;
 font-family: var(--pw-font-body);
 font-size: 12.5px;
 font-weight: 500;
 border-radius: var(--pw-radius-pill);
 cursor: pointer;
 }
 .pw-scope-dot {
 width: 7px;
 height: 7px;
 border-radius: 50%;
 background: var(--pw-accent);
 display: inline-block;
 }

 .pw-user-chip {
 display: inline-flex;
 align-items: center;
 gap: 8px;
 padding: 4px 12px 4px 4px;
 color: var(--pw-ink);
 text-decoration: none;
 border-radius: var(--pw-radius-pill);
 transition: background 0.15s;
 }
 .pw-user-chip:hover {
 background: var(--pw-surface-warm);
 }
 /* Auto-show CLI bottom bar — PULSE-style */
 .pw-bbar {
 position: fixed;
 left: 0;
 right: 0;
 bottom: 0;
 z-index: 9000;
 font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
 transition: max-height .25s ease, transform .15s ease;
 background: #1a1a1a;
 color: #d2a8ff;
 border-top: 1px solid #2a2f38;
 display: flex;
 flex-direction: column;
 overflow: hidden;
 }

 .pw-bbar--idle {
 height: 28px;
 background: var(--pw-bg-alt);
 border-top: 1px solid var(--pw-border);
 }

 .pw-bbar--open {
 height: 196px;
 box-shadow: 0 -6px 24px rgba(0, 0, 0, 0.18);
 }

 .pw-bbar-toggle {
 width: 100%;
 height: 100%;
 background: transparent;
 border: none;
 color: var(--pw-muted);
 font-family: var(--pw-font-body);
 font-size: 11.5px;
 font-weight: 500;
 cursor: pointer;
 display: flex;
 align-items: center;
 justify-content: center;
 gap: 6px;
 }
 .pw-bbar-toggle:hover { color: var(--pw-ink); background: var(--pw-surface-warm); }
 .pw-bbar-toggle-arrow { color: var(--pw-accent); font-size: 11px; }
 .pw-bbar-toggle-pulse {
 width: 6px;
 height: 6px;
 border-radius: 50%;
 background: var(--pw-accent);
 margin-left: 6px;
 animation: pw-bbar-pulse 1.4s ease-in-out infinite;
 }
 @keyframes pw-bbar-pulse {
 0%, 100% { opacity: 0.4; }
 50% { opacity: 1; }
 }

 .pw-bbar-head {
 flex: 0 0 auto;
 background: var(--pw-accent);
 color: #fff;
 padding: 7px 14px;
 display: flex;
 justify-content: space-between;
 align-items: center;
 font-family: var(--pw-font-body);
 font-size: 11px;
 font-weight: 500;
 }
 .pw-bbar-head-left {
 display: flex;
 align-items: center;
 gap: 8px;
 }
 .pw-bbar-icon { font-size: 12px; }
 .pw-bbar-title { font-weight: 600; letter-spacing: -0.005em; }
 .pw-bbar-stats {
 margin-left: 14px;
 display: inline-flex;
 align-items: center;
 gap: 6px;
 font-size: 11.5px;
 opacity: 0.92;
 }
 .pw-bbar-sep { opacity: 0.5; }
 .pw-bbar-head-right {
 display: flex;
 align-items: center;
 gap: 4px;
 }
 .pw-bbar-btn {
 background: transparent;
 border: none;
 color: rgba(255,255,255,0.85);
 font-family: var(--pw-font-body);
 font-size: 11px;
 font-weight: 500;
 padding: 3px 8px;
 border-radius: 0;
 cursor: pointer;
 }
 .pw-bbar-btn:hover { background: rgba(255,255,255,0.18); color: #fff; }
 .pw-bbar-btn--icon { padding: 3px 6px; }

 .pw-bbar-body {
 flex: 1;
 overflow-y: auto;
 padding: 10px 14px 12px;
 font-size: 11.5px;
 line-height: 1.55;
 color: #8b949e;
 background: #1a1a1a;
 }
 .pw-bbar-line {
 display: flex;
 gap: 10px;
 padding: 1px 0;
 white-space: pre-wrap;
 }
 .pw-bbar-ts { color: #6e7681; flex-shrink: 0; }
 .pw-bbar-msg { color: inherit; }

 .pw-bbar-line.done .pw-bbar-msg { color: #56d364; }
 .pw-bbar-line.error .pw-bbar-msg { color: #ff7b72; }
 .pw-bbar-line.running .pw-bbar-msg { color: #d2a8ff; }
 .pw-bbar-line.info .pw-bbar-msg { color: #9ddcff; }

 /* Structured 5-column log line renderer */
 .pw-bbar-body-wrap {
 flex: 1;
 position: relative;
 display: flex;
 flex-direction: column;
 min-height: 0;
 }
 .pw-bbar-line--struct {
 display: grid;
 grid-template-columns: auto 1fr 90px 1fr 80px 90px;
 gap: 12px;
 align-items: baseline;
 }
 .pw-bbar-col { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
 .pw-bbar-col-entity { color: #c9d1d9; }
 .pw-bbar-col-action { color: #d2a8ff; font-weight: 600; text-transform: uppercase; opacity: 0.85; }
 .pw-bbar-line--struct.done .pw-bbar-col-action { color: #56d364; }
 .pw-bbar-line--struct.error .pw-bbar-col-action { color: #ff7b72; }
 .pw-bbar-col-model { color: #6e7681; }
 .pw-bbar-col-dur { color: #6e7681; text-align: right; }
 .pw-bbar-col-cost { color: #6e7681; text-align: right; }

 .pw-bbar-unread {
 margin-left: 8px;
 background: var(--pw-accent);
 color: #fff;
 font-weight: 600;
 font-size: 10.5px;
 padding: 2px 7px;
 border-radius: var(--pw-radius-pill);
 }
 .pw-bbar-jump {
 position: absolute;
 bottom: 12px;
 right: 16px;
 background: var(--pw-accent);
 color: #fff;
 border: none;
 padding: 5px 11px;
 font-family: var(--pw-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
 font-size: 11px;
 font-weight: 600;
 border-radius: var(--pw-radius-pill);
 cursor: pointer;
 box-shadow: 0 2px 8px rgba(0,0,0,0.25);
 z-index: 5;
 }
 .pw-bbar-jump:hover { filter: brightness(1.1); }

 .pw-show-cli-btn {
 display: inline-flex;
 align-items: center;
 gap: 6px;
 background: transparent;
 color: var(--pw-ink-soft);
 border: 1px solid var(--pw-border);
 padding: 5px 12px;
 font-family: var(--pw-font-body);
 font-size: 11px;
 font-weight: 500;
 border-radius: var(--pw-radius-pill);
 cursor: pointer;
 transition: background 0.15s, color 0.15s, border-color 0.15s;
 }
 .pw-show-cli-btn:hover {
 background: var(--pw-surface-warm);
 color: var(--pw-ink);
 border-color: var(--pw-accent);
 }

 @media (max-width: 700px) {
 .pw-bbar--open { height: 50vh; }
 .pw-bbar-stats { display: none; }
 }

 @media (prefers-reduced-motion: reduce) {
 .pw-bbar { transition: none; }
 .pw-bbar-toggle-pulse { animation: none; }
 }

 .pw-user-avatar {
 width: 26px;
 height: 26px;
 border-radius: 50%;
 background: var(--pw-accent);
 color: #fff;
 display: flex;
 align-items: center;
 justify-content: center;
 font-family: var(--pw-font-headline);
 font-size: 12px;
 font-weight: 500;
 }
 .pw-user-id {
 display: flex;
 flex-direction: column;
 align-items: flex-start;
 justify-content: center;
 line-height: 1.1;
 gap: 1px;
 }
 .pw-user-id-bare {
 justify-content: center;
 }
 .pw-user-name {
 font-size: 12px;
 font-weight: 500;
 color: var(--pw-ink);
 }
 .pw-user-tier {
 font-size: 9px;
 font-weight: 700;
 letter-spacing: 0.05em;
 text-transform: uppercase;
 line-height: 1;
 }
 .pw-user-tier-super { color: var(--pw-accent); }
 .pw-user-tier-admin { color: var(--pw-ink-soft); }

 /* ───── Footer ───── */
 .pw-footer {
 height: 32px;
 background: var(--pw-bg-alt);
 border-top: 1px solid var(--pw-border);
 color: var(--pw-ink-soft);
 font-size: 11px;
 font-family: var(--pw-font-body);
 }
 .pw-pulse-dot {
 width: 7px;
 height: 7px;
 border-radius: 50%;
 background: var(--pw-success);
 display: inline-block;
 box-shadow: 0 0 0 0 rgba(45, 106, 79, 0.55);
 animation: pwPulse 2s infinite;
 }
 .pw-pulse-dot-warn {
 background: var(--pw-accent);
 box-shadow: 0 0 0 0 rgba(201, 99, 66, 0.55);
 }
 @keyframes pwPulse {
 0% { box-shadow: 0 0 0 0 rgba(45, 106, 79, 0.45); }
 70% { box-shadow: 0 0 0 6px rgba(45, 106, 79, 0); }
 100% { box-shadow: 0 0 0 0 rgba(45, 106, 79, 0); }
 }

 /* ───── Brand button ───── */
 .pw-brand-btn {
 display: inline-flex;
 align-items: center;
 gap: 8px;
 background: transparent;
 border: none;
 cursor: pointer;
 padding: 4px 6px;
 border-radius: var(--pw-radius-pill);
 transition: background 0.15s;
 }
 .pw-brand-btn:hover { background: var(--pw-surface-warm); }

 /* ───── Nav row + dropdown groups ───── */
 .pw-nav-row {
 display: inline-flex;
 align-items: center;
 gap: 2px;
 margin-left: 18px;
 min-width: 0;
 }
 .pw-nav-label { white-space: nowrap; }
 .pw-nav-group {
 position: relative;
 display: inline-flex;
 }
 .pw-group-active > .pw-nav {
 color: var(--pw-ink);
 }
 .pw-group-active > .pw-nav.pw-nav-active,
 .pw-group-active > .pw-nav-active {
 background: rgba(201, 99, 66, 0.10);
 color: var(--pw-accent) !important;
 font-weight: 600;
 }
 .pw-group-active > .pw-nav::after {
 content: '';
 position: absolute;
 left: 18px;
 right: 18px;
 bottom: 4px;
 height: 2px;
 background: var(--pw-accent);
 border-radius: 0;
 }
 .pw-chev {
 transition: transform 0.18s ease;
 margin-left: 2px;
 }
 .pw-chev-open { transform: rotate(180deg); }

 /* ───── Dropdown menu ───── */
 .pw-menu {
 position: absolute;
 top: calc(100% + 8px);
 left: 0;
 width: 280px;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 box-shadow: var(--pw-shadow-md, 0 8px 24px rgba(0,0,0,0.08));
 padding: 8px;
 z-index: 200;
 display: flex;
 flex-direction: column;
 gap: 2px;
 }
 .pw-menu-right {
 left: auto;
 right: 0;
 }
 .pw-menu-row {
 display: flex;
 align-items: flex-start;
 gap: 10px;
 width: 100%;
 padding: 8px 10px;
 background: transparent;
 border: none;
 border-left: 3px solid transparent;
 border-radius: 0;
 cursor: pointer;
 text-align: left;
 color: var(--pw-ink);
 transition: background 0.12s, color 0.12s;
 }
 .pw-menu-row:hover {
 background: var(--pw-surface-warm);
 color: var(--pw-accent);
 }
 .pw-menu-row :global(svg) {
 flex-shrink: 0;
 margin-top: 1px;
 color: var(--pw-ink-soft);
 }
 .pw-menu-row:hover :global(svg) {
 color: var(--pw-accent);
 }
 .pw-menu-active {
 background: var(--pw-accent-soft);
 border-left-color: var(--pw-accent);
 }
 .pw-menu-active :global(svg),
 .pw-menu-active .pw-menu-name {
 color: var(--pw-accent);
 }
 .pw-menu-text {
 display: flex;
 flex-direction: column;
 gap: 1px;
 min-width: 0;
 }
 .pw-menu-name {
 font-family: var(--pw-font-body);
 font-size: 12px;
 font-weight: 500;
 color: var(--pw-ink);
 line-height: 1.3;
 }
 .pw-menu-sub {
 font-size: 11px;
 color: var(--pw-muted);
 line-height: 1.3;
 }
 .pw-menu-divider {
 height: 1px;
 background: var(--pw-border);
 margin: 4px 0;
 }
 .pw-role-badge {
 display: inline-flex;
 align-items: center;
 height: 26px;
 padding: 0 10px;
 margin-right: 8px;
 border-radius: 13px;
 font-size: 11px;
 font-weight: 800;
 letter-spacing: 0.04em;
 white-space: nowrap;
 cursor: pointer;
 font-family: inherit;
 }
 .pw-role-badge:hover { filter: brightness(0.95); }
 .pw-role-super {
 background: rgba(192, 57, 43, 0.12);
 color: var(--pw-error, #c0392b);
 border: 1px solid rgba(192, 57, 43, 0.3);
 }
 .pw-role-admin {
 background: rgba(201, 99, 66, 0.12);
 color: var(--pw-accent, #c96342);
 border: 1px solid rgba(201, 99, 66, 0.3);
 }
 .pw-menu-ident {
 display: flex;
 align-items: center;
 justify-content: space-between;
 gap: 8px;
 padding: 8px 12px 6px;
 }
 .pw-menu-ident-name { font-size: 13px; font-weight: 700; color: var(--pw-ink); }
 .pw-menu-ident-role {
 font-size: 10px;
 font-weight: 800;
 letter-spacing: 0.05em;
 padding: 2px 7px;
 border-radius: 10px;
 background: var(--pw-bg-alt, rgba(0,0,0,0.05));
 color: var(--pw-ink-soft, #777);
 }
 .pw-menu-ident-role.is-super { background: rgba(192,57,43,0.12); color: var(--pw-error, #c0392b); }
 .pw-menu-ident-role.is-admin { background: rgba(201,99,66,0.12); color: var(--pw-accent, #c96342); }
 .pw-menu-label {
 padding: 6px 12px 2px;
 font-size: 10px;
 font-weight: 800;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 color: var(--pw-ink-soft, #999);
 }
 .pw-pill-new {
 display: inline-block;
 background: var(--pw-accent);
 color: #fff;
 font-size: 11px;
 font-weight: 700;
 padding: 1px 6px;
 border-radius: var(--pw-radius-pill);
 letter-spacing: 0.04em;
 margin-left: 4px;
 vertical-align: middle;
 }

 /* ───── Agents online pill ───── */
 .pw-agents-pill {
 display: inline-flex;
 align-items: center;
 gap: 6px;
 background: var(--pw-surface-warm);
 border: 1px solid var(--pw-border);
 color: var(--pw-ink-soft);
 padding: 5px 12px;
 font-family: var(--pw-font-body);
 font-size: 11px;
 border-radius: var(--pw-radius-pill);
 }
 .pw-pulse-green {
 width: 7px;
 height: 7px;
 border-radius: 50%;
 background: var(--pw-success, #2d6a4f);
 display: inline-block;
 box-shadow: 0 0 0 0 rgba(45, 106, 79, 0.55);
 animation: pwPulse 2s infinite;
 }

 /* ───── Feed bell button ───── */
 .pw-feed-btn {
 display: inline-flex;
 align-items: center;
 gap: 6px;
 background: transparent;
 border: none;
 color: var(--pw-ink-soft);
 padding: 6px 12px;
 font-family: var(--pw-font-body);
 font-size: 12.5px;
 font-weight: 500;
 border-radius: var(--pw-radius-pill);
 cursor: pointer;
 position: relative;
 transition: background 0.15s, color 0.15s;
 }
 .pw-feed-btn:hover {
 background: var(--pw-surface-warm);
 color: var(--pw-ink);
 }
 .pw-feed-count {
 background: var(--pw-accent);
 color: #fff;
 font-size: 10px;
 font-weight: 600;
 min-width: 16px;
 height: 16px;
 padding: 0 5px;
 border-radius: var(--pw-radius-pill);
 display: inline-flex;
 align-items: center;
 justify-content: center;
 }
 /* bell unread DOT (no count) — used when only a new version is unseen */
 .pw-feed-dot {
 width: 8px; height: 8px; border-radius: 50%;
 background: var(--pw-accent); margin-left: 1px;
 }
 /* drawer header: tabs + close, one aligned row */
 .pw-feed-head {
 display: flex; align-items: center; gap: 10px;
 padding: 12px 12px 10px 14px;
 border-bottom: 1px solid var(--pw-border);
 }
 .pw-feed-x {
 flex-shrink: 0;
 width: 32px; height: 32px; border-radius: 8px;
 display: inline-flex; align-items: center; justify-content: center;
 background: none; border: none; cursor: pointer;
 color: var(--pw-muted, #8a847c);
 transition: background .15s, color .15s;
 }
 .pw-feed-x:hover { background: var(--pw-bg-alt, #f5f1ea); color: var(--pw-ink, #2a2622); }
 /* slim summary strip (replaces dark hero) */
 .pw-feed-strip {
 display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
 padding: 10px 16px; font-size: 12px; color: var(--pw-ink-soft, #5a5550);
 background: var(--pw-bg-alt, #faf7f2);
 border-bottom: 1px solid var(--pw-border);
 }
 .pw-feed-strip-live { display: inline-flex; align-items: center; gap: 5px; color: var(--pw-muted, #8a847c); }
 .pw-feed-strip-main b { font-size: 13px; color: var(--pw-ink, #2a2622); }
 .pw-feed-strip-sep { opacity: .35; }
 .pw-feed-strip-stat { font-weight: 600; }
 .pw-fs-ok { color: #1f7a4d; }
 .pw-fs-warn { color: #b8860b; }
 .pw-fs-err { color: #b3382c; }
 .pw-feed-strip-tot { margin-left: auto; color: var(--pw-dim, #9a948c); font-size: 11px; }
 /* drawer segment toggle: Activity | What's new */
 .pw-feed-seg {
 flex: 1; display: flex; gap: 4px;
 background: var(--pw-bg-alt, #f5f1ea);
 border-radius: 10px; padding: 4px;
 }
 .pw-feed-segbtn {
 flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 6px;
 padding: 7px 10px; border: none; border-radius: 7px;
 background: none; cursor: pointer;
 font-size: 12.5px; font-weight: 600; color: var(--pw-muted, #8a847c);
 transition: background .15s, color .15s;
 }
 .pw-feed-segbtn:hover { color: var(--pw-ink, #2a2622); }
 .pw-feed-segbtn-active {
 background: var(--pw-bg, #fff); color: var(--pw-ink, #2a2622);
 box-shadow: 0 1px 3px rgba(0,0,0,.08);
 }
 .pw-feed-segcount {
 background: var(--pw-accent); color: #fff; font-size: 10px; font-weight: 700;
 min-width: 16px; height: 16px; padding: 0 5px; border-radius: 999px;
 display: inline-flex; align-items: center; justify-content: center;
 }
 .pw-feed-segdot { width: 7px; height: 7px; border-radius: 50%; background: var(--pw-accent); }
 .pw-feed-wn { padding: 4px 16px 24px; }

 /* ───── Activity Feed dropdown ───── */
 .pw-feed-panel {
 position: absolute;
 top: 56px;
 right: 16px;
 width: 400px;
 max-height: 560px;
 background: var(--pw-surface, #fdfaf6);
 border: 1px solid var(--pw-border, #e5dfd6);
 border-radius: var(--pw-radius, 10px);
 box-shadow: 0 14px 40px rgba(60, 40, 20, 0.14), 0 4px 12px rgba(60, 40, 20, 0.06);
 font-family: var(--pw-font-body);
 color: var(--pw-ink, #2a2520);
 display: flex;
 flex-direction: column;
 overflow: hidden;
 transform-origin: top right;
 animation: pwFeedSlide 0.18s ease-out;
 }
 @keyframes pwFeedSlide {
 from { opacity: 0; transform: translateY(-6px) scale(0.98); }
 to { opacity: 1; transform: translateY(0) scale(1); }
 }
 .pw-feed-header {
 padding: 14px 18px 10px;
 display: flex;
 justify-content: space-between;
 align-items: center;
 border-bottom: 1px solid var(--pw-border-soft, #efe8de);
 background: var(--pw-surface-warm, #faf4e9);
 }
 .pw-feed-title {
 font-family: var(--pw-font-headline, Georgia, serif);
 font-size: 16px;
 font-weight: 500;
 color: var(--pw-ink, #2a2520);
 letter-spacing: -0.01em;
 }
 .pw-feed-link {
 background: none;
 border: none;
 color: var(--pw-accent, #c0563b);
 cursor: pointer;
 font-size: 12.5px;
 font-family: var(--pw-font-body);
 text-decoration: none;
 padding: 0;
 }
 .pw-feed-link:hover { text-decoration: underline; }
 .pw-feed-close {
 background: none;
 border: none;
 color: var(--pw-muted, #8a7d6e);
 cursor: pointer;
 font-size: 14px;
 line-height: 1;
 padding: 4px 6px;
 border-radius: 0;
 }
 .pw-feed-close:hover { background: rgba(0,0,0,0.05); color: var(--pw-ink); }

 .pw-feed-chips {
 display: flex;
 gap: 6px;
 padding: 10px 14px 10px;
 border-bottom: 1px solid var(--pw-border-soft, #efe8de);
 overflow-x: auto;
 scrollbar-width: none;
 }
 .pw-feed-chips::-webkit-scrollbar { display: none; }
 .pw-feed-chip {
 background: transparent;
 border: 1px solid var(--pw-border, #e5dfd6);
 color: var(--pw-ink-soft, #5a4f43);
 font-size: 11px;
 font-family: var(--pw-font-body);
 padding: 4px 11px;
 border-radius: 0;
 cursor: pointer;
 white-space: nowrap;
 transition: all 0.12s;
 }
 .pw-feed-chip:hover { background: rgba(0,0,0,0.04); }
 .pw-feed-chip-active {
 background: var(--pw-ink, #2a2520);
 color: var(--pw-surface, #fdfaf6);
 border-color: var(--pw-ink, #2a2520);
 }

 .pw-feed-body {
 flex: 1;
 overflow-y: auto;
 max-height: 380px;
 }
 .pw-feed-row {
 padding: 12px 18px;
 display: flex;
 gap: 11px;
 align-items: flex-start;
 border-bottom: 1px solid var(--pw-border-soft, #efe8de);
 cursor: pointer;
 transition: background 0.1s;
 }
 .pw-feed-row:hover { background: rgba(0,0,0,0.03); }
 .pw-feed-row:last-child { border-bottom: none; }
 .pw-feed-dot {
 width: 9px;
 height: 9px;
 border-radius: 50%;
 flex-shrink: 0;
 margin-top: 6px;
 box-shadow: 0 0 0 2px rgba(255,255,255,0.6);
 }
 .pw-feed-row-top {
 display: flex;
 justify-content: space-between;
 align-items: baseline;
 gap: 8px;
 }
 .pw-feed-row-title {
 font-size: 13.5px;
 color: var(--pw-ink, #2a2520);
 font-weight: 500;
 line-height: 1.35;
 flex: 1;
 min-width: 0;
 }
 .pw-feed-row-title-unread { font-weight: 600; }
 .pw-feed-row-time {
 font-size: 11px;
 color: var(--pw-muted, #8a7d6e);
 white-space: nowrap;
 flex-shrink: 0;
 }
 .pw-feed-row-msg {
 font-size: 11px;
 color: var(--pw-muted, #8a7d6e);
 margin-top: 2px;
 line-height: 1.4;
 overflow: hidden;
 text-overflow: ellipsis;
 white-space: nowrap;
 }
 .pw-feed-unread-dot {
 width: 7px;
 height: 7px;
 border-radius: 50%;
 background: #c0563b;
 flex-shrink: 0;
 margin-top: 7px;
 }
 .pw-feed-empty {
 padding: 44px 18px;
 text-align: center;
 font-size: 12px;
 color: var(--pw-muted, #8a7d6e);
 }
 .pw-feed-footer {
 padding: 10px 18px;
 border-top: 1px solid var(--pw-border-soft, #efe8de);
 background: var(--pw-surface-warm, #faf4e9);
 text-align: center;
 }

 /* ───── Hamburger + mobile panel ───── */
 .pw-hamburger {
 display: none;
 background: transparent;
 border: none;
 color: var(--pw-ink);
 padding: 6px;
 border-radius: var(--pw-radius-pill);
 cursor: pointer;
 }
 .pw-mobile-backdrop {
 position: fixed;
 inset: 0;
 background: rgba(20, 20, 20, 0.4);
 z-index: 99;
 }
 .pw-mobile-panel {
 position: fixed;
 top: 0;
 right: 0;
 bottom: 0;
 width: 320px;
 max-width: 90vw;
 background: var(--pw-surface);
 border-left: 1px solid var(--pw-border);
 z-index: 100;
 display: flex;
 flex-direction: column;
 padding: 12px;
 overflow-y: auto;
 }
 .pw-mobile-header {
 display: flex;
 align-items: center;
 justify-content: space-between;
 padding: 6px 4px 14px;
 border-bottom: 1px solid var(--pw-border);
 margin-bottom: 8px;
 }
 .pw-mobile-section {
 font-size: 11px;
 font-weight: 600;
 color: var(--pw-muted);
 text-transform: uppercase;
 letter-spacing: 0.06em;
 padding: 12px 12px 4px;
 }
 .pw-mobile-row {
 display: block;
 width: 100%;
 background: transparent;
 border: none;
 text-align: left;
 padding: 10px 12px;
 font-family: var(--pw-font-body);
 font-size: 13px;
 font-weight: 500;
 color: var(--pw-ink);
 border-radius: 0;
 cursor: pointer;
 }
 .pw-mobile-row:hover { background: var(--pw-surface-warm); color: var(--pw-accent); }
 .pw-mobile-sub { padding-left: 22px; font-weight: 400; color: var(--pw-ink-soft); }

 /* ≤1100px (tablet / narrow desktop): shrink logo + nav to fit the 5 items */
 @media (max-width: 1100px) {
 .pw-header { padding: 10px 12px; }
 .pw-brand-img { height: 40px; max-width: 180px; }
 .pw-nav-row { margin-left: 8px; gap: 0; }
 .pw-nav { height: 38px; padding: 0 10px; font-size: 12.5px; gap: 6px; }
 .pw-agents-pill { display: none; }
 .pw-hide-md { display: none; }
 }

 /* ≤900px: nav-row scrolls horizontally instead of overflowing; user cluster stays pinned right */
 @media (max-width: 900px) {
 .pw-nav-row {
 overflow-x: auto;
 flex-wrap: nowrap;
 scrollbar-width: none;
 -ms-overflow-style: none;
 -webkit-overflow-scrolling: touch;
 }
 .pw-nav-row::-webkit-scrollbar { display: none; }
 .pw-nav { flex-shrink: 0; }
 .pw-user-name { display: none; }
 }

 /* ≤640px (mobile): drop nav text labels — icon-only scroll strip; tighter header */
 @media (max-width: 640px) {
 .pw-header { padding: 8px 8px; }
 .pw-brand-img { height: 32px; max-width: 120px; }
 .pw-nav-label { display: none; }
 .pw-nav { padding: 0 8px; gap: 0; }
 }
 .pw-badge-coral { display: inline-flex; align-items: center; justify-content: center; min-width: 18px; height: 16px; padding: 0 5px; margin-left: 4px; background: var(--pw-accent, #c96342); color: #fff; border-radius: 0; font: 600 10px Inter; }
 .pw-badge-gray { display: inline-flex; align-items: center; justify-content: center; min-width: 18px; height: 16px; padding: 0 5px; margin-left: 4px; background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink-soft, #87837a); border-radius: 0; font: 600 10px Inter; }
 .pw-menu-section { padding: 8px 12px 4px; font: 700 10px Inter; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft, #87837a); border-top: 1px solid var(--pw-border, #e7e3da); margin-top: 4px; }
 .pw-menu-section:first-child { border-top: none; margin-top: 0; }

 /* ───── Scout-style Console Bar (cn-*) ───── */
 .cn-bar {
   position: fixed; left: 0; right: 0; bottom: 0;
   z-index: 9000; font-family: 'JetBrains Mono', ui-monospace, Menlo, monospace;
   pointer-events: none;
 }
 .cn-bar > * { pointer-events: auto; }
 .cn-bar--idle {
   display: flex; justify-content: flex-end; padding: 0 16px 12px 0;
 }
 .cn-bar--open {
   background: #1a1614; border-top: 1px solid #2a2522;
   color: #e8e3d6;
   display: flex; flex-direction: column;
   max-height: 38vh; min-height: 140px;
 }
 .cn-pill {
   display: inline-flex; align-items: center; gap: 10px;
   padding: 7px 14px; border-radius: 0;
   background: #1a1614; color: #e8e3d6;
   border: 1px solid #2a2522;
   font: 600 11px 'JetBrains Mono', monospace;
   cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.18);
 }
 .cn-pill:hover { background: #221d1a; }
 .cn-pill-dot {
   width: 7px; height: 7px; border-radius: 50%;
   background: #6b6557; display: inline-block;
 }
 .cn-pill-dot--active {
   background: var(--pw-accent, #c96342); box-shadow: 0 0 6px rgba(201,99,66,0.6);
   animation: cnpulse 1.4s ease-in-out infinite;
 }
 @keyframes cnpulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
 .cn-pill-label { color: var(--pw-accent, #c96342); font-weight: 700; }
 .cn-pill-evt { color: #b8b3a6; }
 .cn-pill-evt b { color: #e8e3d6; }

 .cn-head {
   display: flex; align-items: center; gap: 10px;
   padding: 6px 14px; background: #1a1614;
   border-bottom: 1px solid #2a2522;
   font: 600 11px 'JetBrains Mono', monospace;
   cursor: pointer; user-select: none;
 }
 .cn-head:hover { background: #221d1a; }
 .cn-head-left { display: flex; align-items: center; gap: 8px; flex: 1; }
 .cn-collapse-arrow { color: var(--pw-accent, #c96342); font-size: 9px; }
 .cn-title { color: var(--pw-accent, #c96342); font-weight: 700; letter-spacing: 0.05em; }
 .cn-sep { color: #4a443e; }
 .cn-evt b, .cn-err b, .cn-warn b { color: #e8e3d6; font-weight: 700; }
 .cn-evt, .cn-state, .cn-err, .cn-warn { color: #8a8478; }
 .cn-state--active { color: var(--pw-accent, #c96342); font-style: italic; }
 .cn-err b { color: #ff7960; }
 .cn-warn b { color: #ffb84d; }

 .cn-ctrls { display: flex; align-items: center; gap: 6px; }
 .cn-chk { display: inline-flex; align-items: center; gap: 4px; color: #b8b3a6; font: 500 10.5px monospace; cursor: pointer; }
 .cn-chk input { accent-color: var(--pw-accent, #c96342); }
 .cn-btn {
   background: #2a2522; color: #e8e3d6; border: 1px solid #3a3530;
   padding: 3px 9px; font: 600 10.5px 'JetBrains Mono', monospace;
   cursor: pointer; border-radius: 0;
 }
 .cn-btn:hover { background: #3a3530; color: var(--pw-accent, #c96342); }
 .cn-btn--icon { padding: 3px 7px; }
 .cn-sel {
   background: #2a2522; color: #e8e3d6; border: 1px solid #3a3530;
   padding: 2px 6px; font: 600 10.5px 'JetBrains Mono', monospace;
   border-radius: 0; cursor: pointer;
 }

 .cn-body-wrap { position: relative; flex: 1; min-height: 0; display: flex; }
 .cn-body {
   flex: 1; overflow-y: auto; padding: 10px 14px;
   background: #0f0d0c; color: #c8c2b4;
   font: 12px 'JetBrains Mono', monospace; line-height: 1.5;
 }
 .cn-placeholder { color: #5a554d; font-style: italic; }
 .cn-line { display: flex; gap: 8px; padding: 1px 0; }
 .cn-ts { color: var(--pw-accent, #c96342); flex-shrink: 0; font-weight: 600; }
 .cn-msg { flex: 1; word-break: break-word; }
 .cn-line.cn-err .cn-msg, .cn-line.error .cn-msg { color: #ff7960; }
 .cn-line.cn-warn .cn-msg, .cn-line.warn .cn-msg { color: #ffb84d; }
 .cn-col { display: inline-block; min-width: 60px; color: #b8b3a6; }
 .cn-col-action { color: var(--pw-accent, #c96342); font-weight: 700; }
 .cn-col-cost { color: #6dc97a; }
 .cn-jump {
   position: absolute; bottom: 10px; right: 14px;
   background: var(--pw-accent, #c96342); color: #fff;
   border: 0; padding: 4px 10px; font: 600 10.5px monospace;
   cursor: pointer; border-radius: 0;
 }
 .cn-jump:hover { background: #b8553a; }

 /* Hide legacy pw-bbar (replaced by cn-*) */
 .pw-bbar { display: none !important; }
</style>
