<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { confirmDelete } from '$lib/confirmDelete';
 import ScopeSwitch from '$lib/brain/ScopeSwitch.svelte';
 import RailNav from '$lib/brain/RailNav.svelte';
 import MergedList from '$lib/brain/MergedList.svelte';
 import BrainCortex from '$lib/brain/BrainCortex.svelte';
 import OkfPanel from '$lib/brain/OkfPanel.svelte';
 import { parseHash, writeHash, onHashChange } from '$lib/brain/hubRouting';

 // embedded=true when mounted inside Workspace settings: skip super-admin redirect
 // + skip top-level hash routing (settings owns window.location.hash for its activeTab).
 // item = externally-driven active brain item (e.g. 'glossary') — the settings rail
 // owns navigation, so BrainHub hides its own RailNav and follows this prop instead.
 let { embedded = false, item = null as string | null } = $props();

 /* ─── state ─── */
 let activeTab = $state('glossary');
 let brainQuery = $state('');
 let graphView = $state<'map' | 'list'>('map');
 let ugraphContainer = $state<HTMLDivElement | undefined>();
 let ugraphInstance: any = null;
 let entries = $state<any[]>([]);
 let stats = $state<any>({});
 let accessLog = $state<any[]>([]);
 let graphData = $state<any>({ nodes: [], edges: [] });
 let loading = $state(false);

 /* ─── scope filter ─── */
 let scopeFilter = $state('all'); // 'all' | 'global' | 'personal' | project slug
 let userProjects = $state<any[]>([]); // [{slug, name, agent_name}]

 /* ─── add form state ─── */
 let showAdd = $state(false);

 /* ─── retail seed importer state ─── */
 let seedsModalOpen = $state(false);
 let seedsList = $state<any[]>([]);
 let seedsSelected = $state<string[]>([]);
 let seedsLoading = $state(false);
 let seedsImporting = $state(false);
 let seedsToast = $state('');
 let seedsOverwrite = $state(false);

 async function openSeedsModal() {
 seedsModalOpen = true;
 seedsLoading = true;
 seedsList = [];
 try {
 const r = await fetch('/api/brain/seeds', { headers: _h() });
 if (r.ok) {
 const data = await r.json();
 seedsList = data.seeds || [];
 seedsSelected = seedsList.map((s: any) => s.filename);
 }
 } catch (e) {
 seedsToast = 'Failed to load seeds';
 }
 seedsLoading = false;
 }

 function toggleSeed(name: string) {
 if (seedsSelected.includes(name)) {
 seedsSelected = seedsSelected.filter((n) => n !== name);
 } else {
 seedsSelected = [...seedsSelected, name];
 }
 }

 async function runSeedImport() {
 if (seedsSelected.length === 0) {
 seedsToast = 'Select at least one file';
 return;
 }
 seedsImporting = true;
 seedsToast = '';
 try {
 const r = await fetch('/api/brain/import-seeds', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', ..._h() },
 body: JSON.stringify({
 files: seedsSelected,
 scope: 'global',
 overwrite: seedsOverwrite,
 }),
 });
 const data = await r.json();
 if (r.ok) {
 const n = data.imported ?? 0;
 const m = (data.files || []).length;
 seedsToast = `Imported ${n} entries from ${m} files`;
 // Refresh visible entries.
 tabLoaded = {};
 await loadEntries();
 setTimeout(() => { seedsModalOpen = false; seedsToast = ''; }, 1500);
 } else {
 seedsToast = data.detail || 'Import failed';
 }
 } catch (e) {
 seedsToast = 'Network error';
 }
 seedsImporting = false;
 }
 let addCategory = $state('glossary');
 let addName = $state('');
 let addDefinition = $state('');
 let addMetadata = $state('{}');
 let addError = $state('');
 let saving = $state(false);
 let newEntryScope = $state('global');

 /* ─── alias builder (for alias tab form) ─── */
 let aliasInput = $state('');

 /* ─── threshold builder (for threshold tab form) ─── */
 let threshTarget = $state('');
 let threshAlertBelow = $state('');
 let threshAlertAbove = $state('');

 /* ─── formula builder ─── */
 let formulaExpr = $state('');
 let formulaUnit = $state('');

 /* ─── edit state ─── */
 let editId = $state<number | null>(null);

 /* ─── echart ─── */
 let graphContainer: HTMLDivElement | undefined = $state();
 let chartInstance: any = null;

 /* ─── auth helper ─── */
 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 /* ─── tabs ─── */
 const tabs = [
 { id: 'glossary', label: 'Glossary' },
 { id: 'formulas', label: 'Formulas' },
 { id: 'aliases', label: 'Aliases' },
 { id: 'patterns', label: 'Patterns' },
 { id: 'org', label: 'Org map' },
 { id: 'thresholds', label: 'Rules' },
 { id: 'graph', label: 'Graph' },
 { id: 'log', label: 'Access log' },
 { id: 'okf', label: 'OKF' },
 ];

 const navGroups = [
 { label: 'Knowledge', items: ['glossary', 'formulas', 'aliases', 'patterns'] },
 { label: 'Structure', items: ['org', 'thresholds'] },
 { label: 'Activity', items: ['graph', 'log'] },
 { label: 'Exchange', items: ['okf'] },
 ];

 function _byCat(key: string): number {
 return (stats?.by_category?.[key] ?? 0) as number;
 }
 function tabCount(id: string): number | null {
 if (id === 'glossary') return _byCat('glossary');
 if (id === 'formulas') return _byCat('formula');
 if (id === 'aliases') return _byCat('alias');
 if (id === 'patterns') return _byCat('pattern');
 if (id === 'org') return _byCat('org');
 if (id === 'thresholds') return _byCat('rule') + _byCat('threshold');
 if (id === 'log') return accessLog.length || null;
 return null;
 }
 function tabLabel(id: string): string {
 return tabs.find(t => t.id === id)?.label || id;
 }

 /* ─── category map (tab id → API category) ─── */
 const tabCategoryMap: Record<string, string> = {
 glossary: 'glossary',
 formulas: 'formula',
 aliases: 'alias',
 patterns: 'pattern',
 org: 'org',
 thresholds: 'threshold',
 };

 /* ─── tab switch ─── */
 let tabLoaded = $state<Record<string, boolean>>({});

 async function switchTab(id: string) {
 activeTab = id;
 tabLoaded[id] = true;
 loading = true;
 entries = [];
 try {
 if (id === 'graph') {
 if (!graphData?.nodes?.length) await loadGraph();
 } else if (id === 'log') {
 await loadLog();
 } else if (id === 'okf') {
 // OkfPanel self-loads its own data — skip the brain-entries fetch.
 } else {
 await loadEntries();
 }
 } catch {}
 loading = false;
 }

 /* ═══════════════════════════════════════════════════════════ */
 /* DATA LOADERS */
 /* ═══════════════════════════════════════════════════════════ */

 async function loadEntries() {
 try {
 let url = '/api/brain/entries';
 if (activeTab !== 'log') {
 if (scopeFilter !== 'all') {
 if (scopeFilter === 'global') url += '?scope=global';
 else if (scopeFilter === 'personal') url += '?scope=personal';
 else url += `?project_slug=${scopeFilter}`;
 }
 const selectedCategory = tabCategoryMap[activeTab];
 if (selectedCategory) {
 url += (url.includes('?') ? '&' : '?') + `category=${selectedCategory}`;
 }
 }
 const res = await fetch(url, { headers: _h() });
 if (res.ok) { const d = await res.json(); entries = d.entries || d || []; }
 } catch {}
 }

 async function loadStats() {
 try {
 const res = await fetch('/api/brain/stats', { headers: _h() });
 if (res.ok) { stats = await res.json(); }
 } catch {}
 }

 async function loadLog() {
 try {
 const res = await fetch('/api/brain/log', { headers: _h() });
 if (res.ok) { const d = await res.json(); accessLog = d.logs || d.log || d.entries || d || []; }
 } catch {}
 }

 async function loadGraph() {
 try {
 const res = await fetch('/api/brain/graph', { headers: _h() });
 if (res.ok) { graphData = await res.json(); }
 } catch {}
 }

 async function loadProjects() {
 try {
 const res = await fetch('/api/user-projects-brief', { headers: _h() });
 if (res.ok) {
 const d = await res.json();
 const all = d.projects || [];
 // Only show projects that have brain entries
 try {
 const br = await fetch('/api/brain', { headers: _h() });
 if (br.ok) {
 const bd = await br.json();
 const slugs = new Set((bd.entries || []).filter((e: any) => e.project_slug).map((e: any) => e.project_slug));
 userProjects = slugs.size > 0 ? all.filter((p: any) => slugs.has(p.slug)) : [];
 } else { userProjects = []; }
 } catch { userProjects = []; }
 }
 } catch {}
 }

 /* ═══════════════════════════════════════════════════════════ */
 /* CRUD */
 /* ═══════════════════════════════════════════════════════════ */

 function resetForm() {
 addName = '';
 addDefinition = '';
 addMetadata = '{}';
 addError = '';
 aliasInput = '';
 threshTarget = '';
 threshAlertBelow = '';
 threshAlertAbove = '';
 formulaExpr = '';
 formulaUnit = '';
 newEntryScope = 'global';
 editId = null;
 showAdd = false;
 }

 function openAdd() {
 resetForm();
 addCategory = tabCategoryMap[activeTab] || 'glossary';
 showAdd = true;
 }

 function openEdit(entry: any) {
 editId = entry.id;
 addCategory = entry.category || 'glossary';
 addName = entry.name || '';
 addDefinition = entry.definition || '';
 const meta = entry.metadata || {};
 addMetadata = JSON.stringify(meta, null, 2);
 // Populate specialized fields
 if (entry.category === 'alias') {
 aliasInput = '';
 }
 if (entry.category === 'threshold') {
 threshTarget = meta.target ?? '';
 threshAlertBelow = meta.alert_below ?? '';
 threshAlertAbove = meta.alert_above ?? '';
 }
 if (entry.category === 'formula') {
 formulaExpr = meta.formula ?? '';
 formulaUnit = meta.unit ?? '';
 }
 showAdd = true;
 }

 function buildMetadata(): any {
 try {
 let meta = JSON.parse(addMetadata);
 if (addCategory === 'threshold') {
 meta.target = threshTarget ? parseFloat(threshTarget) : undefined;
 meta.alert_below = threshAlertBelow ? parseFloat(threshAlertBelow) : undefined;
 meta.alert_above = threshAlertAbove ? parseFloat(threshAlertAbove) : undefined;
 }
 if (addCategory === 'formula') {
 if (formulaExpr) meta.formula = formulaExpr;
 if (formulaUnit) meta.unit = formulaUnit;
 }
 return meta;
 } catch {
 return {};
 }
 }

 async function addEntry() {
 if (!addName.trim()) { addError = 'Name is required'; return; }
 saving = true;
 addError = '';
 try {
 const body: any = {
 category: addCategory,
 name: addName.trim(),
 definition: addDefinition.trim(),
 metadata: buildMetadata(),
 };
 if (newEntryScope === 'personal') {
 body.scope = 'personal';
 } else if (newEntryScope !== 'global') {
 body.project_slug = newEntryScope;
 }
 const res = await fetch('/api/brain/entries', {
 method: 'POST',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify(body),
 });
 if (res.ok) {
 resetForm();
 tabLoaded = {};
 await loadEntries();
 await loadStats();
 } else {
 const d = await res.json();
 addError = d.detail || d.message || 'Failed to save entry';
 }
 } catch (e: any) {
 addError = e.message || 'Network error';
 }
 saving = false;
 }

 async function updateEntry(id: number) {
 if (!addName.trim()) { addError = 'Name is required'; return; }
 saving = true;
 addError = '';
 try {
 const body = {
 category: addCategory,
 name: addName.trim(),
 definition: addDefinition.trim(),
 metadata: buildMetadata(),
 };
 const res = await fetch(`/api/brain/entries/${id}`, {
 method: 'PUT',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify(body),
 });
 if (res.ok) {
 resetForm();
 tabLoaded = {};
 await loadEntries();
 await loadStats();
 } else {
 const d = await res.json();
 addError = d.detail || d.message || 'Failed to update entry';
 }
 } catch (e: any) {
 addError = e.message || 'Network error';
 }
 saving = false;
 }

 /* ─── history drawer state (Phase D) ─── */
 let historyOpen = $state(false);
 let historyEntry = $state<any>(null);
 let historyVersions = $state<any[]>([]);
 let historyLoading = $state(false);
 let historyExpanded = $state<Record<number, boolean>>({});
 let rollbackPending = $state<{ version: number; nextVersion: number } | null>(null);
 let rollbackBusy = $state(false);

 async function openHistory(entry: any) {
 historyOpen = true;
 historyEntry = entry;
 historyVersions = [];
 historyExpanded = {};
 historyLoading = true;
 try {
 const r = await fetch(`/api/brain/${entry.id}/history?limit=50`, { headers: _h() });
 if (r.ok) {
 const d = await r.json();
 historyVersions = d.versions || [];
 }
 } catch {}
 historyLoading = false;
 }

 function closeHistory() {
 historyOpen = false;
 historyEntry = null;
 historyVersions = [];
 rollbackPending = null;
 }

 function changeTypeColor(t: string): string {
 if (t === 'create') return '#16a34a';
 if (t === 'update') return '#2563eb';
 if (t === 'delete') return '#dc2626';
 if (t === 'rollback') return '#ea580c';
 return '#6b7280';
 }

 function relTime(d: string | null | undefined): string {
 if (!d) return '-';
 try {
 const ms = Date.now() - new Date(d).getTime();
 const s = Math.floor(ms / 1000);
 if (s < 60) return `${s}s ago`;
 const m = Math.floor(s / 60);
 if (m < 60) return `${m}m ago`;
 const h = Math.floor(m / 60);
 if (h < 24) return `${h}h ago`;
 const days = Math.floor(h / 24);
 return `${days}d ago`;
 } catch { return '-'; }
 }

 function diffLines(a: string | null | undefined, b: string | null | undefined): string {
 const aT = (a || '').trim();
 const bT = (b || '').trim();
 if (aT === bT) return '(no change)';
 return `- ${bT || '(empty)'}\n+ ${aT || '(empty)'}`;
 }

 function askRollback(version: number) {
 const maxV = historyVersions.length ? Math.max(...historyVersions.map((v: any) => v.version)) : 0;
 rollbackPending = { version, nextVersion: maxV + 1 };
 }

 async function confirmRollback() {
 if (!historyEntry || !rollbackPending) return;
 rollbackBusy = true;
 try {
 const res = await fetch(`/api/brain/${historyEntry.id}/rollback/${rollbackPending.version}`, {
 method: 'POST',
 headers: _h(),
 });
 if (res.ok) {
 rollbackPending = null;
 // Refresh entry list and re-open the drawer with new history.
 tabLoaded = {};
 await loadEntries();
 const fresh = entries.find((e: any) => e.id === historyEntry.id);
 if (fresh) await openHistory(fresh); else closeHistory();
 } else {
 const d = await res.json().catch(() => ({}));
 alert(d.detail || 'Rollback failed');
 }
 } catch (e) {
 alert('Network error during rollback');
 }
 rollbackBusy = false;
 }

 async function deleteEntry(id: number, name: string) {
 if (!(await confirmDelete({ itemName: name, itemType: 'Brain entry' }))) return;
 try {
 await fetch(`/api/brain/entries/${id}`, { method: 'DELETE', headers: _h() });
 tabLoaded = {};
 await loadEntries();
 await loadStats();
 } catch {}
 }

 /* ─── alias helpers ─── */
 function getAliases(entry: any): string[] {
 return entry.metadata?.aliases || [];
 }

 function addAliasTag() {
 if (!aliasInput.trim()) return;
 try {
 let meta = JSON.parse(addMetadata);
 if (!meta.aliases) meta.aliases = [];
 if (!meta.aliases.includes(aliasInput.trim())) {
 meta.aliases.push(aliasInput.trim());
 }
 addMetadata = JSON.stringify(meta, null, 2);
 aliasInput = '';
 } catch {}
 }

 function removeAliasTag(alias: string) {
 try {
 let meta = JSON.parse(addMetadata);
 meta.aliases = (meta.aliases || []).filter((a: string) => a !== alias);
 addMetadata = JSON.stringify(meta, null, 2);
 } catch {}
 }

 function getFormAliases(): string[] {
 try { return JSON.parse(addMetadata).aliases || []; } catch { return []; }
 }

 /* ─── filtered entries ─── */
 function filtered(category: string): any[] {
 // Safety net: backend now folds the Burmese twin into the EN row's *_my field,
 // so standalone MY rows should not appear. Filter any that still leak through.
 return entries.filter((e: any) => e.category === category
   && e.lang !== 'my' && e.source !== 'bilingual_twin');
 }

 /* ─── helpers ─── */
 function fmtDate(d: string | null | undefined): string {
 if (!d) return '-';
 try { return new Date(d).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }); }
 catch { return String(d).slice(0, 19); }
 }

 function categoryColor(cat: string): string {
 const map: Record<string, string> = {
 glossary: 'var(--pw-accent)',
 formula: '#0078d4',
 alias: '#c5934a',
 pattern: '#9b59b6',
 org: '#e74c3c',
 threshold: '#e67e22',
 };
 return map[cat] || '#888';
 }

 /* ═══════════════════════════════════════════════════════════ */
 /* GRAPH (ECharts) */
 /* ═══════════════════════════════════════════════════════════ */

 async function renderGraph() {
 if (!graphContainer) return;
 const echarts = await import('echarts');
 if (chartInstance) chartInstance.dispose();
 chartInstance = echarts.init(graphContainer, 'dark');
 chartInstance.setOption({
 backgroundColor: 'var(--pw-ink)',
 tooltip: { trigger: 'item', formatter: (p: any) => p.data?.name || '' },
 series: [{
 type: 'graph',
 layout: 'force',
 data: (graphData.nodes || []).map((n: any) => ({
 name: n.name,
 id: String(n.id),
 symbolSize: n.type === 'org' ? 40 : n.type === 'formula' ? 30 : 20,
 itemStyle: {
 color: n.type === 'metric' ? 'var(--pw-accent)' : n.type === 'formula' ? '#0078d4' :
 n.type === 'entity' ? '#c5934a' : n.type === 'org' ? '#9b59b6' : '#e74c3c',
 borderColor: '#444',
 borderWidth: 1.5,
 },
 label: { show: true, fontSize: 9, color: '#ddd' },
 })),
 links: (graphData.edges || []).map((e: any) => ({
 source: String(e.source),
 target: String(e.target),
 lineStyle: { color: '#555', width: 1.5 },
 label: { show: true, formatter: e.relation || '', fontSize: 7, color: '#666' },
 })),
 roam: true,
 draggable: true,
 force: { repulsion: 300, gravity: 0.1, edgeLength: [80, 200] },
 }],
 });
 }

 /* ─── Unified graph (project KG triples → node-link MAP) ─── */
 function _ugNodeType(name: string): string {
   const n = (name || '').toLowerCase();
   if (/_\d{6,8}$/.test(n) || n.includes('stock') || n.includes('articles') || n.includes('_list')) return 'table';
   return 'metric';
 }

 function buildUnifiedGraph() {
   const nodes = new Map<string, any>();
   const links: any[] = [];
   const ensure = (name: string, type: string) => {
     if (!nodes.has(name)) nodes.set(name, { name, type });
     return name;
   };
   for (const it of (unifiedItems || [])) {
     const m = (it.meta || {}) as any;
     if (m.grouped) {
       // collapsed value-spam → table ─pred(N vals)→ column-group node
       const tbl = ensure(m.object, 'table');
       const grpName = `${m.predicate}\n${m.value_count} values`;
       ensure(grpName, 'group');
       links.push({ source: tbl, target: grpName, relation: m.predicate });
     } else if (m.subject && m.object) {
       const s = ensure(m.subject, _ugNodeType(m.subject));
       const o = ensure(m.object, _ugNodeType(m.object));
       links.push({ source: s, target: o, relation: m.predicate });
     }
   }
   return { nodes: [...nodes.values()], links };
 }

 async function renderUnifiedGraph() {
   if (!ugraphContainer) return;
   const echarts = await import('echarts');
   if (ugraphInstance) ugraphInstance.dispose();
   ugraphInstance = echarts.init(ugraphContainer);
   const { nodes, links } = buildUnifiedGraph();
   const color = (t: string) => t === 'table' ? '#c96342' : t === 'group' ? '#8a6db5' : '#5b6fb5';
   ugraphInstance.setOption({
     backgroundColor: '#faf8f1',
     tooltip: { trigger: 'item', formatter: (p: any) => (p.data?.name || p.data?.relation || '').replace(/\n/g, ' · ') },
     series: [{
       type: 'graph', layout: 'force', roam: true, draggable: true,
       focusNodeAdjacency: true,
       data: nodes.map((n) => ({
         name: n.name,
         symbol: n.type === 'metric' ? 'diamond' : n.type === 'group' ? 'roundRect' : 'circle',
         symbolSize: n.type === 'table' ? 46 : n.type === 'group' ? 34 : 28,
         itemStyle: { color: color(n.type), borderColor: '#2c2a26', borderWidth: 1 },
         label: { show: true, fontSize: 10, color: '#2c2a26', overflow: 'truncate', width: 90 },
       })),
       links: links.map((e) => ({
         source: e.source, target: e.target,
         lineStyle: { color: '#bdb6a6', width: 1.5, curveness: 0.08 },
         label: { show: true, formatter: e.relation || '', fontSize: 8, color: '#8a8478' },
       })),
       emphasis: { focus: 'adjacency', lineStyle: { width: 3, color: '#c96342' } },
       force: { repulsion: 420, gravity: 0.08, edgeLength: [90, 220] },
     }],
   });
   ugraphInstance.resize();
 }

 // render MAP when graph tab + map view + data present
 $effect(() => {
   if (activeTab === '__unified__' && hubItem === 'graph' && graphView === 'map' && (unifiedItems?.length ?? 0) > 0) {
     setTimeout(() => { if (ugraphContainer) renderUnifiedGraph(); }, 80);
   }
 });

 /* ─── lifecycle ─── */
 onMount(async () => {
 // Standalone route guards super-admin + redirects. Embedded (inside Workspace
 // settings) is already past auth — skip the redirect entirely.
 if (!embedded) {
 try {
 const res = await fetch('/api/auth/check', { headers: _h() });
 if (res.ok) {
 const me = await res.json();
 if (!me.is_super) { window.location.href = '/ui/home'; return; }
 } else { window.location.href = '/ui/home'; return; }
 } catch { window.location.href = '/ui/home'; return; }
 }
 loadProjects();
 await loadStats();
 loadLog();
 // Embedded: don't read top-level hash (settings owns it) — start on default item.
 await applyHubState(embedded ? { section: 'knowledge', item: 'glossary', scope: 'all' } : parseHash());
 });

 onDestroy(() => {
 if (chartInstance) { chartInstance.dispose(); chartInstance = null; }
 if (ugraphInstance) { ugraphInstance.dispose(); ugraphInstance = null; }
 });

 // Re-render graph when tab switches to graph and data is loaded
 $effect(() => {
 if (activeTab === 'graph' && graphData.nodes?.length > 0) {
 // Small delay to ensure DOM is mounted
 setTimeout(() => {
 if (graphContainer) renderGraph();
 }, 100);
 }
 });

 /* ═══ UNIFIED HUB (single Brain) ═══ */
 const LOCKED_SLUG = 'citypharma';
 let hubScope = $state('all');      // agent | company | personal | all
 let hubItem  = $state('glossary'); // rail item id

 // rail item id → unified merge category (KNOWLEDGE items)
 const ITEM_TO_CAT: Record<string, string> = {
   definitions: 'definitions',
   glossary: 'glossary',
   patterns: 'patterns',
   rules: 'rules',
   graph: 'graph',
   schema: 'schema',
   org: 'org',
 };
 // SHARING items → status filter over ALL merge categories
 const SHARING_FILTER: Record<string, string> = {
   promote: 'agent_only',
   pull: 'company_only',
   conflicts: 'conflict',
 };
 const MERGE_CATS = ['definitions', 'glossary', 'patterns', 'rules'];

 function hubScopeToFilter(s: string): string {
   if (s === 'agent') return LOCKED_SLUG;
   if (s === 'company') return 'global';
   if (s === 'personal') return 'personal';
   return 'all';
 }

 /* ── unified merged-list state ── */
 let unifiedItems = $state<any[]>([]);
 let unifiedLoading = $state(false);
 let unifiedStatusFilter = $state('all');

 async function _fetchUnified(cat: string): Promise<any[]> {
   try {
     const url = `/api/brain/unified?category=${cat}&scope=${hubScope}&project_slug=${LOCKED_SLUG}`;
     const r = await fetch(url, { headers: _h() });
     if (r.ok) { const d = await r.json(); return d.items || []; }
   } catch {}
   return [];
 }

 async function loadUnified(cat: string) {
   unifiedLoading = true; unifiedItems = [];
   unifiedItems = await _fetchUnified(cat);
   unifiedLoading = false;
 }

 async function loadUnifiedAll() {
   unifiedLoading = true; unifiedItems = [];
   const all = await Promise.all(MERGE_CATS.map((c) => _fetchUnified(c)));
   unifiedItems = all.flat();
   unifiedLoading = false;
 }

 async function refreshCurrentUnified() {
   const cat = ITEM_TO_CAT[hubItem];
   if (cat) await loadUnified(cat);
   else if (hubItem in SHARING_FILTER) await loadUnifiedAll();
 }

 async function handleMergedAction(action: string, item: any) {
   const cat = item.category;
   let ep = '', body: any = null;
   if (action === 'promote') {
     ep = '/api/brain/promote';
     body = { category: cat, name: item.name, agent_id: item.agent_id, project_slug: LOCKED_SLUG };
   } else if (action === 'pull') {
     ep = '/api/brain/pull';
     body = { category: cat, name: item.name, company_id: item.company_id, project_slug: LOCKED_SLUG };
   } else if (action === 'resolve_agent' || action === 'resolve_company') {
     ep = '/api/brain/resolve';
     body = {
       category: cat, name: item.name, agent_id: item.agent_id, company_id: item.company_id,
       winner: action === 'resolve_agent' ? 'agent' : 'company', project_slug: LOCKED_SLUG,
     };
   } else { return; }
   try {
     await fetch(ep, {
       method: 'POST',
       headers: { 'Content-Type': 'application/json', ..._h() },
       body: JSON.stringify(body),
     });
   } catch {}
   await refreshCurrentUnified();
   loadStats();
 }

 async function selectHubItem(section: string, item: string) {
   hubItem = item;
   if (!embedded) writeHash({ section, item, scope: hubScope });
   const cat = ITEM_TO_CAT[item];
   if (cat) {
     activeTab = '__unified__'; unifiedStatusFilter = 'all';
     await loadUnified(cat);
   } else if (item in SHARING_FILTER) {
     activeTab = '__unified__'; unifiedStatusFilter = SHARING_FILTER[item];
     await loadUnifiedAll();
   } else if (item === 'cortex') {
     activeTab = '__cortex__'; loading = false;
   } else if (item === 'okf') {
     activeTab = 'okf'; loading = false;
   } else if (item === 'accesslog' || item === 'activity') {
     await switchTab('log');
   } else if (item === 'training') {
     window.location.href = `/ui/project/${LOCKED_SLUG}/settings#training`;
   } else if (item === 'datasource') {
     window.location.href = `/ui/project/${LOCKED_SLUG}/settings#upload`;
   } else {
     activeTab = '__placeholder__'; loading = false;
   }
 }

 async function changeHubScope(s: string) {
   hubScope = s;
   scopeFilter = hubScopeToFilter(s);
   tabLoaded = {};
   if (!embedded) writeHash({ scope: s });
   await refreshCurrentUnified();
 }

 function placeholderTitle(item: string): string {
   const m: Record<string, string> = {
     definitions: 'Definitions', schema: 'Schema', training: 'Training',
     datasource: 'Data Source', promote: 'Promote agent → company',
     pull: 'Pull company → agent', conflicts: 'Conflicts',
   };
   return m[item] || item;
 }

 // hash-driven init + back/forward support
 let _hubUnsub: (() => void) | null = null;
 async function applyHubState(st: { section: string; item: string; scope: string }) {
   hubScope = st.scope;
   scopeFilter = hubScopeToFilter(st.scope);
   await selectHubItem(st.section, st.item);
 }
 onMount(() => {
   if (!embedded) _hubUnsub = onHashChange((st) => { applyHubState(st); });
 });
 onDestroy(() => { if (_hubUnsub) _hubUnsub(); });

 // Embedded: settings rail drives the active item via the `item` prop.
 $effect(() => {
   if (embedded && item && item !== hubItem) selectHubItem('', item);
 });
</script>

<!-- Reusable bilingual block: line 1 = English, line 2 = Burmese (when present). -->
{#snippet bilingual(en: string, my: string | null | undefined)}
  {#if my && String(my).trim()}
    <div class="bi-line"><span class="bi-badge">1</span><span class="bi-en">{en || '-'}</span></div>
    <div class="bi-line"><span class="bi-badge">2</span><span class="bi-my" lang="my">{my}</span></div>
  {:else}
    {en || '-'}
  {/if}
{/snippet}

<div class="brain-shell" class:brain-embedded={embedded}>

<!-- ═══ LEFT RAIL (unified hub) — hidden when embedded; settings rail drives it ═══ -->
{#if !embedded}
<aside class="brain-rail">
  <RailNav bind:active={hubItem} onSelect={selectHubItem} />
</aside>
{/if}

<!-- ═══ MAIN ═══ -->
<main class="brain-main">

{#if loading}
  <div class="brain-loadbar" aria-hidden="true"><span></span></div>
{/if}

<!-- ═══ HEADER (standalone only — settings shows its own head) ═══ -->
{#if !embedded}
<div class="ds-page-head">
  <div>
    <h1 class="ds-page-title">Brain</h1>
    <div class="ds-page-sub">Unified knowledge — this agent · company · personal</div>
  </div>
  <button class="btn-primary" onclick={openSeedsModal} title="Bulk-load retail Brain seed JSON files">Import retail seeds</button>
</div>
{/if}

<!-- ═══ STATS ═══ -->
<div class="ds-grid ds-grid-auto" style="margin-bottom: 16px;">
  <div class="ds-stat">
    <div class="ds-stat-row">
      <div class="ds-stat-value">{_byCat('glossary')}</div>
      <div class="ds-stat-icon"><Icon name="book" size={14} /></div>
    </div>
    <div class="ds-stat-label">Concepts</div>
  </div>
  <div class="ds-stat">
    <div class="ds-stat-row">
      <div class="ds-stat-value">{_byCat('formula')}</div>
      <div class="ds-stat-icon">ƒ</div>
    </div>
    <div class="ds-stat-label">Formulas</div>
  </div>
  {#if _byCat('alias') > 0}
  <div class="ds-stat">
    <div class="ds-stat-row">
      <div class="ds-stat-value">{_byCat('alias')}</div>
      <div class="ds-stat-icon">↔</div>
    </div>
    <div class="ds-stat-label">Aliases</div>
  </div>
  {/if}
  <div class="ds-stat">
    <div class="ds-stat-row">
      <div class="ds-stat-value">{_byCat('pattern')}</div>
      <div class="ds-stat-icon">◇</div>
    </div>
    <div class="ds-stat-label">Patterns</div>
  </div>
  <div class="ds-stat">
    <div class="ds-stat-row">
      <div class="ds-stat-value">{stats.access_count ?? accessLog.length}</div>
      <div class="ds-stat-icon"></div>
    </div>
    <div class="ds-stat-label">Access events</div>
  </div>
</div>

<!-- ═══ SCOPE SWITCH — single-tenant: collapsed to one unified brain (A — display merge) ═══ -->
<!-- ScopeSwitch hidden; hubScope pinned to 'all'. Un-hide to restore agent/company/personal tiers. -->
{#if false}
<div class="ds-toolbar">
  <div class="ds-toolbar-group">
    <span class="ds-field-label" style="margin-right: 8px;">Scope</span>
    <ScopeSwitch bind:scope={hubScope} onChange={changeHubScope} />
  </div>
</div>
{/if}

<div style="margin-top: 16px;">

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- GLOSSARY TAB                                               -->
<!-- ═══════════════════════════════════════════════════════════ -->
{#if activeTab === 'glossary'}
  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Business terms and definitions</div>
    <button class="btn-primary" onclick={openAdd}>+ ADD TERM</button>
  </div>
  {#if loading && filtered('glossary').length === 0}
    <div class="brain-skel"></div><div class="brain-skel" style="height: 48px;"></div><div class="brain-skel" style="height: 48px;"></div>
  {:else if filtered('glossary').length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No glossary entries yet.</div>
  {:else}
    {#each filtered('glossary') as entry}
      <div class="ds-card" style="margin-bottom: 8px;">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span style="font-weight: 900; font-size: 12px;">{entry.name}</span>
            <span class="chip chip-coral">{entry.category}</span>
            {#if entry.scope === 'global'}
              <span class="chip chip-green"><Icon name="globe" size={14} /> GLOBAL</span>
            {:else if entry.scope === 'personal'}
              <span class="chip chip-purple"><Icon name="user" size={14} /> PERSONAL</span>
            {:else if entry.project_slug}
              <span class="chip chip-amber"><Icon name="bar-chart" size={14} /> {entry.project_slug}</span>
            {/if}
          </div>
          <div class="flex gap-1">
            <button class="feedback-btn" title="History" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openHistory(entry)}>&#128337;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openEdit(entry)}>&#9998;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer; color: var(--pw-error); border-color: var(--pw-error);" onclick={() => deleteEntry(entry.id, entry.name)}>&#10005;</button>
          </div>
        </div>
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px; line-height: 1.5;">{@render bilingual(entry.definition, entry.definition_my)}</div>
      </div>
    {/each}
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- FORMULAS TAB                                               -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'formulas'}
  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Calculations and derived metrics</div>
    <button class="btn-primary" onclick={openAdd}>+ ADD FORMULA</button>
  </div>
  {#if loading && filtered('formula').length === 0}
    <div class="brain-skel"></div><div class="brain-skel" style="height: 48px;"></div><div class="brain-skel" style="height: 48px;"></div>
  {:else if filtered('formula').length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No formulas defined yet.</div>
  {:else}
    {#each filtered('formula') as entry}
      <div class="ds-card" style="margin-bottom: 8px;">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span style="font-weight: 900; font-size: 12px;">{entry.name}</span>
            <span class="chip chip-blue">FORMULA</span>
            {#if entry.scope === 'global'}
              <span class="chip chip-green"><Icon name="globe" size={14} /> GLOBAL</span>
            {:else if entry.scope === 'personal'}
              <span class="chip chip-purple"><Icon name="user" size={14} /> PERSONAL</span>
            {:else if entry.project_slug}
              <span class="chip chip-amber"><Icon name="bar-chart" size={14} /> {entry.project_slug}</span>
            {/if}
            {#if entry.metadata?.unit}
              <span style="font-size: 11px; color: var(--pw-muted); font-style: italic;">({entry.metadata.unit})</span>
            {/if}
          </div>
          <div class="flex gap-1">
            <button class="feedback-btn" title="History" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openHistory(entry)}>&#128337;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openEdit(entry)}>&#9998;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer; color: var(--pw-error); border-color: var(--pw-error);" onclick={() => deleteEntry(entry.id, entry.name)}>&#10005;</button>
          </div>
        </div>
        {#if entry.metadata?.formula}
          <div class="cli-terminal" style="margin-top: 6px; padding: 6px 10px; font-size: 10px;">
            <code style="color: #e0e0e0;">{entry.metadata.formula}</code>
          </div>
        {/if}
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px; line-height: 1.5;">{@render bilingual(entry.definition, entry.definition_my)}</div>
      </div>
    {/each}
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- ALIASES TAB                                                -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'aliases'}
  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Alternate names and abbreviations</div>
    <button class="btn-primary" onclick={openAdd}>+ ADD ALIAS</button>
  </div>
  {#if loading && filtered('alias').length === 0}
    <div class="brain-skel"></div><div class="brain-skel" style="height: 48px;"></div><div class="brain-skel" style="height: 48px;"></div>
  {:else if filtered('alias').length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No aliases defined yet.</div>
  {:else}
    {#each filtered('alias') as entry}
      <div class="ds-card" style="margin-bottom: 8px;">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span style="font-weight: 900; font-size: 12px;">{entry.name}</span>
            <span class="chip chip-amber">ALIAS</span>
            {#if entry.scope === 'global'}
              <span class="chip chip-green"><Icon name="globe" size={14} /> GLOBAL</span>
            {:else if entry.scope === 'personal'}
              <span class="chip chip-purple"><Icon name="user" size={14} /> PERSONAL</span>
            {:else if entry.project_slug}
              <span class="chip chip-amber"><Icon name="bar-chart" size={14} /> {entry.project_slug}</span>
            {/if}
          </div>
          <div class="flex gap-1">
            <button class="feedback-btn" title="History" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openHistory(entry)}>&#128337;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openEdit(entry)}>&#9998;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer; color: var(--pw-error); border-color: var(--pw-error);" onclick={() => deleteEntry(entry.id, entry.name)}>&#10005;</button>
          </div>
        </div>
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px;">{@render bilingual(entry.definition, entry.definition_my)}</div>
        {#if getAliases(entry).length > 0}
          <div style="display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px;">
            {#each getAliases(entry) as alias}
              <span style="font-size: 11px; font-weight: 700; padding: 2px 8px; background: #c5934a; color: var(--pw-ink); border-radius: var(--pw-radius-sm);">{alias}</span>
            {/each}
          </div>
        {/if}
      </div>
    {/each}
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- PATTERNS TAB                                               -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'patterns'}
  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Recurring business patterns and conventions</div>
    <button class="btn-primary" onclick={openAdd}>+ ADD PATTERN</button>
  </div>
  {#if loading && filtered('pattern').length === 0}
    <div class="brain-skel"></div><div class="brain-skel" style="height: 48px;"></div><div class="brain-skel" style="height: 48px;"></div>
  {:else if filtered('pattern').length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No patterns defined yet.</div>
  {:else}
    {#each filtered('pattern') as entry}
      <div class="ds-card" style="margin-bottom: 8px;">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span style="font-weight: 900; font-size: 12px;">{entry.name}</span>
            <span class="chip chip-gray">PATTERN</span>
            {#if entry.scope === 'global'}
              <span class="chip chip-green"><Icon name="globe" size={14} /> GLOBAL</span>
            {:else if entry.scope === 'personal'}
              <span class="chip chip-purple"><Icon name="user" size={14} /> PERSONAL</span>
            {:else if entry.project_slug}
              <span class="chip chip-amber"><Icon name="bar-chart" size={14} /> {entry.project_slug}</span>
            {/if}
          </div>
          <div class="flex gap-1">
            <button class="feedback-btn" title="History" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openHistory(entry)}>&#128337;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openEdit(entry)}>&#9998;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer; color: var(--pw-error); border-color: var(--pw-error);" onclick={() => deleteEntry(entry.id, entry.name)}>&#10005;</button>
          </div>
        </div>
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px; line-height: 1.5;">{@render bilingual(entry.definition ?? entry.question, entry.definition_my ?? entry.question_my)}</div>
      </div>
    {/each}
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- ORG MAP TAB                                                -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'org'}
  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Organizational structure and hierarchy</div>
    <button class="btn-primary" onclick={openAdd}>+ ADD ORG ENTRY</button>
  </div>
  {#if loading && filtered('org').length === 0}
    <div class="brain-skel"></div><div class="brain-skel" style="height: 48px;"></div><div class="brain-skel" style="height: 48px;"></div>
  {:else if filtered('org').length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No org entries defined yet.</div>
  {:else}
    {#each filtered('org') as entry}
      <div class="ds-card" style="margin-bottom: 8px;">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span style="font-weight: 900; font-size: 12px;">{entry.name}</span>
            <span class="chip chip-coral">ORG</span>
            {#if entry.scope === 'global'}
              <span class="chip chip-green"><Icon name="globe" size={14} /> GLOBAL</span>
            {:else if entry.scope === 'personal'}
              <span class="chip chip-purple"><Icon name="user" size={14} /> PERSONAL</span>
            {:else if entry.project_slug}
              <span class="chip chip-amber"><Icon name="bar-chart" size={14} /> {entry.project_slug}</span>
            {/if}
          </div>
          <div class="flex gap-1">
            <button class="feedback-btn" title="History" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openHistory(entry)}>&#128337;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openEdit(entry)}>&#9998;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer; color: var(--pw-error); border-color: var(--pw-error);" onclick={() => deleteEntry(entry.id, entry.name)}>&#10005;</button>
          </div>
        </div>
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px; line-height: 1.5;">{@render bilingual(entry.definition, entry.definition_my)}</div>
        {#if entry.metadata?.parent}
          <div style="font-size: 10px; margin-top: 6px;">
            <span style="color: var(--pw-muted);">Parent:</span>
            <span style="font-weight: 700;">{entry.metadata.parent}</span>
          </div>
        {/if}
        {#if entry.metadata?.children?.length}
          <div style="font-size: 10px; margin-top: 4px;">
            <span style="color: var(--pw-muted);">Children:</span>
            {#each entry.metadata.children as child}
              <span style="font-weight: 700; margin-left: 6px; padding: 1px 6px; background: var(--pw-bg-alt); font-size: 11px;">{child}</span>
            {/each}
          </div>
        {/if}
      </div>
    {/each}
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- RULES (THRESHOLDS) TAB                                     -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'thresholds'}
  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Thresholds, targets, and alert rules</div>
    <button class="btn-primary" onclick={openAdd}>+ ADD RULE</button>
  </div>
  {#if loading && filtered('threshold').length === 0}
    <div class="brain-skel"></div><div class="brain-skel" style="height: 48px;"></div><div class="brain-skel" style="height: 48px;"></div>
  {:else if filtered('threshold').length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No rules defined yet.</div>
  {:else}
    {#each filtered('threshold') as entry}
      <div class="ds-card" style="margin-bottom: 8px;">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span style="font-weight: 900; font-size: 12px;">{entry.name}</span>
            <span class="chip chip-blue">RULE</span>
            {#if entry.scope === 'global'}
              <span class="chip chip-green"><Icon name="globe" size={14} /> GLOBAL</span>
            {:else if entry.scope === 'personal'}
              <span class="chip chip-purple"><Icon name="user" size={14} /> PERSONAL</span>
            {:else if entry.project_slug}
              <span class="chip chip-amber"><Icon name="bar-chart" size={14} /> {entry.project_slug}</span>
            {/if}
          </div>
          <div class="flex gap-1">
            <button class="feedback-btn" title="History" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openHistory(entry)}>&#128337;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => openEdit(entry)}>&#9998;</button>
            <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer; color: var(--pw-error); border-color: var(--pw-error);" onclick={() => deleteEntry(entry.id, entry.name)}>&#10005;</button>
          </div>
        </div>
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px; line-height: 1.5;">{@render bilingual(entry.definition, entry.definition_my)}</div>
        <div style="display: flex; gap: 12px; margin-top: 8px;">
          {#if entry.metadata?.target != null}
            <div class="ink-border" style="padding: 6px 10px; background: var(--pw-surface); text-align: center;">
              <div style="font-size: 14px; font-weight: 900; color: var(--pw-accent);">{entry.metadata.target}</div>
              <div style="font-size: 11px; text-transform: uppercase; color: var(--pw-muted);">TARGET</div>
            </div>
          {/if}
          {#if entry.metadata?.alert_below != null}
            <div class="ink-border" style="padding: 6px 10px; background: var(--pw-surface); text-align: center;">
              <div style="font-size: 14px; font-weight: 900; color: #e74c3c;">&lt; {entry.metadata.alert_below}</div>
              <div style="font-size: 11px; text-transform: uppercase; color: var(--pw-muted);">ALERT BELOW</div>
            </div>
          {/if}
          {#if entry.metadata?.alert_above != null}
            <div class="ink-border" style="padding: 6px 10px; background: var(--pw-surface); text-align: center;">
              <div style="font-size: 14px; font-weight: 900; color: #e74c3c;">&gt; {entry.metadata.alert_above}</div>
              <div style="font-size: 11px; text-transform: uppercase; color: var(--pw-muted);">ALERT ABOVE</div>
            </div>
          {/if}
        </div>
      </div>
    {/each}
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- GRAPH TAB                                                  -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'graph'}
  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Knowledge graph — relationships between brain entries</div>
    <button class="feedback-btn" style="font-size: 11px; padding: 4px 10px; cursor: pointer;" onclick={async () => { tabLoaded['graph'] = false; loading = true; await loadGraph(); loading = false; tabLoaded['graph'] = true; }}>REFRESH</button>
  </div>
  {#if loading && graphData.nodes?.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">Loading graph...</div>
  {:else if graphData.nodes?.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No graph data available. Add entries to build the knowledge graph.</div>
  {/if}
  <div bind:this={graphContainer} style="width: 100%; height: 500px; background: var(--pw-ink); border: 2px solid var(--pw-ink);"></div>
  <div style="display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap;">
    {#each [
      { label: 'METRIC', color: 'var(--pw-accent)' },
      { label: 'FORMULA', color: '#0078d4' },
      { label: 'ENTITY', color: '#c5934a' },
      { label: 'ORG', color: '#9b59b6' },
      { label: 'OTHER', color: '#e74c3c' },
    ] as legend}
      <div class="flex items-center gap-1">
        <span style="width: 10px; height: 10px; background: {legend.color}; display: inline-block;"></span>
        <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--pw-muted);">{legend.label}</span>
      </div>
    {/each}
  </div>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- LOG TAB                                                    -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'log'}
  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Brain access log — who queried what</div>
    <button class="feedback-btn" style="font-size: 11px; padding: 4px 10px; cursor: pointer;" onclick={async () => { tabLoaded['log'] = false; loading = true; await loadLog(); loading = false; tabLoaded['log'] = true; }}>REFRESH</button>
  </div>
  {#if loading && accessLog.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">Loading…</div>
  {:else if accessLog.length === 0}
    <div class="ds-empty">
      <div class="ds-empty-icon">∅</div>
      <div class="ds-empty-title">No access log entries yet</div>
    </div>
  {:else}
    <div class="ds-table-wrap">
      <table class="ds-table">
        <thead><tr>
          <th>User</th>
          <th>Action</th>
          <th>Entry</th>
          <th>Detail</th>
          <th>Time</th>
        </tr></thead>
        <tbody>
          {#each accessLog as log}
            <tr>
              <td>{log.username || log.user || '-'}</td>
              <td><span class="chip chip-gray">{log.action || log.event || '-'}</span></td>
              <td>{log.entry_name || log.name || '-'}</td>
              <td style="color: var(--pw-muted); max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{log.detail || log.details || '-'}</td>
              <td style="color: var(--pw-muted);">{fmtDate(log.created_at || log.timestamp)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

{:else if activeTab === '__unified__'}
  {#if hubItem === 'graph'}
    <!-- view toggle -->
    <div class="brain-gtoggle">
      <button class="brain-gtog-btn" class:active={graphView === 'map'} type="button" onclick={() => (graphView = 'map')}>◉ MAP</button>
      <button class="brain-gtog-btn" class:active={graphView === 'list'} type="button" onclick={() => (graphView = 'list')}>▤ LIST</button>
    </div>
    {#if graphView === 'map'}
      {#if (unifiedItems?.length ?? 0) === 0}
        <div class="mgl-empty" style="padding:40px 12px;">No relationships in the knowledge graph yet.</div>
      {:else}
        <div bind:this={ugraphContainer} class="brain-gcanvas"></div>
        <div class="brain-glegend">
          <span><span class="brain-gdot" style="background:#c96342"></span>table</span>
          <span><span class="brain-gdot" style="background:#5b6fb5;border-radius:1px;transform:rotate(45deg)"></span>metric</span>
          <span><span class="brain-gdot" style="background:#8a6db5;border-radius:2px"></span>column group</span>
          <span class="brain-ghint">drag · scroll zoom · hover = highlight</span>
        </div>
      {/if}
    {:else}
      <MergedList items={unifiedItems} loading={unifiedLoading} statusFilter={unifiedStatusFilter} query={brainQuery} onAction={handleMergedAction} />
    {/if}
  {:else}
    <div class="brain-searchbar">
      <input class="brain-search-input" type="text" placeholder="⌕  Filter…" bind:value={brainQuery} />
      {#if brainQuery}
        <button class="brain-search-clear" type="button" onclick={() => (brainQuery = '')}>×</button>
      {/if}
    </div>
    <MergedList items={unifiedItems} loading={unifiedLoading} statusFilter={unifiedStatusFilter} query={brainQuery} onAction={handleMergedAction} />
  {/if}
{:else if activeTab === 'okf'}
  <OkfPanel slug={LOCKED_SLUG} />

{:else if activeTab === '__cortex__'}
  <BrainCortex slug={LOCKED_SLUG} />
{:else if activeTab === '__placeholder__'}
  <div class="brain-placeholder">
    <div class="brain-placeholder-title">{placeholderTitle(hubItem)}</div>
    <div class="brain-placeholder-sub">
      This section of the unified Brain lands in a later phase of the merge.
      Scope: <strong>{hubScope.toUpperCase()}</strong>.
    </div>
  </div>
{/if}

</div>
</main>
</div>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- ADD / EDIT MODAL                                           -->
<!-- ═══════════════════════════════════════════════════════════ -->
{#if showAdd}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ds-modal-backdrop" onclick={(e: MouseEvent) => { if (e.target === e.currentTarget) resetForm(); }}>
  <div class="ds-modal">
    <div class="ds-modal-head">
      <h3 class="ds-modal-title">{editId ? 'Edit entry' : 'Add entry'}</h3>
      <button class="ds-modal-close" onclick={resetForm} aria-label="Close">×</button>
    </div>
    <div class="ds-modal-body">

      <div class="ds-field" style="margin-bottom: 14px;">
        <label class="ds-field-label">Category</label>
        <select class="ds-select" bind:value={addCategory}>
          <option value="glossary">Glossary</option>
          <option value="formula">Formula</option>
          <option value="alias">Alias</option>
          <option value="pattern">Pattern</option>
          <option value="org">Org map</option>
          <option value="threshold">Rule / threshold</option>
        </select>
      </div>

      <div class="ds-field" style="margin-bottom: 14px;">
        <label class="ds-field-label">Scope</label>
        <select class="ds-select" bind:value={newEntryScope}>
          <option value="global">Global</option>
          {#each userProjects as proj}
            <option value={proj.slug}>{proj.agent_name || proj.name}</option>
          {/each}
          <option value="personal">Personal</option>
        </select>
      </div>

      <div class="ds-field" style="margin-bottom: 14px;">
        <label class="ds-field-label">Name *</label>
        <input class="ds-input" type="text" bind:value={addName} placeholder="e.g. Revenue, NPS Score, APAC Region…" />
      </div>

      <div class="ds-field" style="margin-bottom: 14px;">
        <label class="ds-field-label">Definition</label>
        <textarea class="ds-textarea" bind:value={addDefinition} placeholder="What does this mean in the business context?" rows="3"></textarea>
      </div>

      {#if addCategory === 'formula'}
        <div class="ds-field" style="margin-bottom: 14px;">
          <label class="ds-field-label">Formula expression</label>
          <input class="ds-input" type="text" bind:value={formulaExpr} placeholder="e.g. (revenue - cost) / revenue * 100" />
        </div>
        <div class="ds-field" style="margin-bottom: 14px;">
          <label class="ds-field-label">Unit</label>
          <select class="ds-select" bind:value={formulaUnit}>
            <option value="">None</option>
            <option value="%">%</option>
            <option value="$">$ (USD)</option>
            <option value="count">Count</option>
            <option value="ratio">Ratio</option>
            <option value="days">Days</option>
            <option value="hours">Hours</option>
            <option value="kg">kg</option>
            <option value="units">Units</option>
          </select>
        </div>
      {/if}

      {#if addCategory === 'alias'}
        <div class="ds-field" style="margin-bottom: 14px;">
          <label class="ds-field-label">Alias tags</label>
          <div style="display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px;">
            {#each getFormAliases() as alias}
              <span class="chip chip-amber" style="display: inline-flex; align-items: center; gap: 4px;">
                {alias}
                <button onclick={() => removeAliasTag(alias)} style="background: none; border: none; cursor: pointer; padding: 0; line-height: 1;" aria-label="Remove">×</button>
              </span>
            {/each}
          </div>
          <div style="display: flex; gap: 6px;">
            <input class="ds-input" type="text" bind:value={aliasInput} placeholder="Type alias and press Add…" style="flex: 1;"
              onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') { e.preventDefault(); addAliasTag(); } }} />
            <button class="btn-secondary" onclick={addAliasTag}>Add</button>
          </div>
        </div>
      {/if}

      {#if addCategory === 'threshold'}
        <div style="display: flex; gap: 8px; margin-bottom: 14px;">
          <div class="ds-field" style="flex: 1;">
            <label class="ds-field-label">Target</label>
            <input class="ds-input" type="number" bind:value={threshTarget} placeholder="100" />
          </div>
          <div class="ds-field" style="flex: 1;">
            <label class="ds-field-label">Alert below</label>
            <input class="ds-input" type="number" bind:value={threshAlertBelow} placeholder="80" />
          </div>
          <div class="ds-field" style="flex: 1;">
            <label class="ds-field-label">Alert above</label>
            <input class="ds-input" type="number" bind:value={threshAlertAbove} placeholder="120" />
          </div>
        </div>
      {/if}

      <details style="margin-bottom: 12px;">
        <summary style="font-size: 11px; font-weight: 500; cursor: pointer; color: var(--pw-muted); margin-bottom: 6px;">Advanced: raw metadata JSON</summary>
        <textarea class="ds-textarea" bind:value={addMetadata} rows="4" style="margin-top: 4px; font-family: var(--pw-mono); font-size: 11px;"></textarea>
      </details>

      {#if addError}
        <div class="ds-field-error" style="margin-bottom: 8px;">{addError}</div>
      {/if}
    </div>
    <div class="ds-modal-foot">
      <button class="btn-secondary" onclick={resetForm}>Cancel</button>
      {#if editId}
        <button class="btn-primary" onclick={() => updateEntry(editId!)} disabled={saving || !addName.trim()}>
          {saving ? 'Saving…' : 'Update entry'}
        </button>
      {:else}
        <button class="btn-primary" onclick={addEntry} disabled={saving || !addName.trim()}>
          {saving ? 'Saving…' : 'Save entry'}
        </button>
      {/if}
    </div>
  </div>
</div>
{/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- RETAIL SEEDS IMPORT MODAL                                  -->
<!-- ═══════════════════════════════════════════════════════════ -->
{#if seedsModalOpen}
<div
  role="presentation"
  onclick={() => { if (!seedsImporting) seedsModalOpen = false; }}
  class="ds-modal-backdrop"
>
  <div
    role="dialog"
    aria-modal="true"
    onclick={(e) => e.stopPropagation()}
    class="ds-modal"
  >
    <div class="ds-modal-head">
      <h3 class="ds-modal-title">Import retail Brain seeds</h3>
      <button class="ds-modal-close" onclick={() => { if (!seedsImporting) seedsModalOpen = false; }} aria-label="Close">×</button>
    </div>
    <div class="ds-modal-body">

    {#if seedsLoading}
      <div style="font-size: 11px; color: var(--pw-muted); padding: 16px 0;">Loading seed files…</div>
    {:else if seedsList.length === 0}
      <div style="font-size: 11px; color: var(--pw-muted); padding: 16px 0;">No seed files found in knowledge/seeds/.</div>
    {:else}
      <div class="ds-field-help" style="margin-bottom: 8px;">
        Select files to import (default: all). Imports as global Brain entries.
      </div>
      <div style="display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px;">
        {#each seedsList as s}
          <label class="ds-card-flat" style="display: flex; align-items: center; gap: 10px; padding: 8px 10px; cursor: pointer; font-size: 12px;">
            <input
              type="checkbox"
              checked={seedsSelected.includes(s.filename)}
              onchange={() => toggleSeed(s.filename)}
              disabled={seedsImporting}
            />
            <span style="flex: 1; font-family: var(--pw-mono);">{s.filename}</span>
            <span style="font-size: 11px; color: var(--pw-muted);">{s.entry_count} entries</span>
          </label>
        {/each}
      </div>

      <label style="display: flex; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 12px; cursor: pointer;">
        <input type="checkbox" bind:checked={seedsOverwrite} disabled={seedsImporting} />
        Overwrite existing entries with same name
      </label>

      {#if seedsToast}
        <div class="ds-card-flat" style="padding: 8px 12px; margin-bottom: 12px;">{seedsToast}</div>
      {/if}
    {/if}
    </div>
    <div class="ds-modal-foot">
      <button class="btn-secondary" onclick={() => { if (!seedsImporting) seedsModalOpen = false; }} disabled={seedsImporting}>Cancel</button>
      <button class="btn-primary" onclick={runSeedImport} disabled={seedsImporting || seedsSelected.length === 0}>
        {seedsImporting ? 'Importing…' : `Import ${seedsSelected.length} file${seedsSelected.length === 1 ? '' : 's'}`}
      </button>
    </div>
  </div>
</div>
{/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- HISTORY DRAWER (Phase D)                                    -->
<!-- ═══════════════════════════════════════════════════════════ -->
{#if historyOpen && historyEntry}
<div
  role="presentation"
  onclick={closeHistory}
  style="position: fixed; inset: 0; background: rgba(0,0,0,0.35); z-index: 9998;"
></div>
<div
  role="dialog"
  aria-label="Brain entry history"
  style="position: fixed; top: 0; right: 0; bottom: 0; width: 480px; max-width: 100vw; background: var(--pw-surface); border-left: 1px solid var(--pw-ink); z-index: 9999; display: flex; flex-direction: column;"
>
  <div style="padding: 14px 16px; border-bottom: 1px solid var(--pw-ink); display: flex; align-items: center; justify-content: space-between; gap: 12px;">
    <div style="min-width: 0;">
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">VERSION HISTORY</div>
      <div style="font-size: 13px; font-weight: 900; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{historyEntry.name}</div>
      <div style="font-size: 10px; color: var(--pw-muted); margin-top: 2px;">#{historyEntry.id} · {historyEntry.category}</div>
    </div>
    <button class="feedback-btn" style="font-size: 11px; padding: 4px 10px; cursor: pointer;" onclick={closeHistory}>CLOSE</button>
  </div>

  <div style="flex: 1; overflow-y: auto; padding: 12px 16px;">
    {#if historyLoading}
      <div style="font-size: 11px; color: var(--pw-muted);">Loading versions...</div>
    {:else if historyVersions.length === 0}
      <div style="font-size: 11px; color: var(--pw-muted);">No version history yet for this entry.</div>
    {:else}
      {#each historyVersions as v, idx}
        {@const isCurrent = idx === 0}
        {@const newer = idx > 0 ? historyVersions[idx - 1] : null}
        <div class="ink-border" style="padding: 10px 12px; margin-bottom: 8px; background: var(--pw-surface);">
          <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px;">
            <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
              <span style="font-size: 11px; font-weight: 900;">v{v.version}</span>
              {#if isCurrent}
                <span style="font-size: 11px; font-weight: 900; padding: 1px 6px; background: var(--pw-ink); color: #fff;">CURRENT</span>
              {/if}
              <span style="font-size: 11px; font-weight: 900; padding: 1px 6px; background: {changeTypeColor(v.change_type)}; color: #fff; text-transform: uppercase;">{v.change_type}</span>
              <span style="font-size: 10px; color: var(--pw-muted);">{relTime(v.created_at)}</span>
              {#if v.changed_by}
                <span style="font-size: 10px; color: var(--pw-muted);">by user #{v.changed_by}</span>
              {/if}
            </div>
            <div style="display: flex; gap: 4px;">
              <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer;" onclick={() => (historyExpanded = { ...historyExpanded, [v.version]: !historyExpanded[v.version] })}>
                {historyExpanded[v.version] ? 'HIDE' : 'DIFF'}
              </button>
              {#if !isCurrent}
                <button class="feedback-btn" style="font-size: 11px; padding: 2px 6px; cursor: pointer; color: #ea580c; border-color: #ea580c;" onclick={() => askRollback(v.version)}>ROLLBACK</button>
              {/if}
            </div>
          </div>
          {#if v.change_reason}
            <div style="font-size: 10px; color: var(--pw-muted); margin-top: 4px; font-style: italic;">{v.change_reason}</div>
          {/if}
          {#if historyExpanded[v.version]}
            <div style="margin-top: 8px;">
              <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--pw-muted); margin-bottom: 4px;">DEFINITION{newer ? ' (vs v' + newer.version + ')' : ''}</div>
              <pre class="cli-terminal" style="padding: 6px 10px; font-size: 10px; white-space: pre-wrap; word-break: break-word; color: #e0e0e0;">{newer ? diffLines(v.definition, newer.definition) : (v.definition || '(empty)')}</pre>
              <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--pw-muted); margin: 8px 0 4px;">METADATA{newer ? ' (vs v' + newer.version + ')' : ''}</div>
              <pre class="cli-terminal" style="padding: 6px 10px; font-size: 10px; white-space: pre-wrap; word-break: break-word; color: #e0e0e0;">{newer ? diffLines(JSON.stringify(v.metadata || {}, null, 2), JSON.stringify(newer.metadata || {}, null, 2)) : JSON.stringify(v.metadata || {}, null, 2)}</pre>
            </div>
          {/if}
        </div>
      {/each}
    {/if}
  </div>

  {#if rollbackPending}
    <div style="position: absolute; inset: 0; background: rgba(0,0,0,0.45); display: flex; align-items: center; justify-content: center; z-index: 1;">
      <div class="ink-border" style="background: var(--pw-surface); padding: 18px 20px; max-width: 360px; width: 100%;">
        <div style="font-size: 11px; font-weight: 900; margin-bottom: 8px;">Restore v{rollbackPending.version}?</div>
        <div style="font-size: 11px; color: var(--pw-muted); margin-bottom: 14px; line-height: 1.5;">
          Current state will be archived as v{rollbackPending.nextVersion}. This action is reversible — you can roll back again from history.
        </div>
        <div style="display: flex; gap: 8px; justify-content: flex-end;">
          <button class="feedback-btn" style="font-size: 11px; padding: 6px 12px; cursor: pointer;" disabled={rollbackBusy} onclick={() => (rollbackPending = null)}>CANCEL</button>
          <button class="send-btn" style="font-size: 11px; padding: 6px 14px;" disabled={rollbackBusy} onclick={confirmRollback}>{rollbackBusy ? 'RESTORING...' : 'RESTORE'}</button>
        </div>
      </div>
    </div>
  {/if}
</div>
{/if}

<style>
 /* ─── Bilingual 1/2 stacked block ─── */
 .bi-line { display: flex; align-items: baseline; gap: 6px; }
 .bi-line + .bi-line { margin-top: 3px; }
 .bi-badge {
   flex-shrink: 0;
   display: inline-flex; align-items: center; justify-content: center;
   width: 14px; height: 14px;
   font-size: 9px; font-weight: 700; line-height: 1;
   color: var(--pw-ink); background: var(--pw-bg-alt);
   border: 1px solid var(--pw-border); border-radius: 50%;
 }
 .bi-en { color: var(--pw-muted); }
 .bi-my { color: var(--pw-dim); }

 /* ─── Left-rail shell (mirrors Command Center pattern) ─── */
 :global(.brain-shell.brain-embedded) { min-height: 0; background: transparent; grid-template-columns: 1fr; }
 :global(.brain-shell.brain-embedded .brain-main) { padding: 0; }
 :global(.brain-shell) {
 display: grid;
 grid-template-columns: 220px 1fr;
 background: var(--pw-bg);
 min-height: calc(100vh - 56px);
 font-family: var(--pw-sans);
 color: var(--pw-ink);
 }
 :global(.brain-rail) {
 position: sticky;
 top: 0;
 align-self: start;
 height: calc(100vh - 56px);
 overflow-y: auto;
 overscroll-behavior: contain;
 background: var(--pw-bg-alt);
 border-right: 1px solid var(--pw-border);
 padding: 16px 8px 24px;
 }
 :global(.brain-rail-group) { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
 :global(.brain-rail-grouplabel) {
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-muted);
 padding: 12px 14px 6px;
 font-weight: 600;
 }
 :global(.brain-rail-btn) {
 display: flex;
 align-items: center;
 justify-content: space-between;
 gap: 8px;
 width: 100%;
 text-align: left;
 background: transparent;
 border: none;
 padding: 8px 12px;
 border-radius: var(--pw-radius-sm);
 font-size: 12px;
 color: var(--pw-ink);
 font-family: inherit;
 cursor: pointer;
 line-height: 1.3;
 }
 :global(.brain-rail-label) { flex: 1; }
 :global(.brain-rail-count) {
 font-size: 11px;
 color: var(--pw-muted);
 font-variant-numeric: tabular-nums;
 }
 :global(.brain-rail-btn:hover) { background: rgba(201, 99, 66, 0.04); }
 :global(.brain-rail-btn.active) {
 background: rgba(201, 99, 66, 0.08);
 color: var(--pw-accent);
 font-weight: 600;
 }
 :global(.brain-rail-btn.active .brain-rail-count) { color: var(--pw-accent); }

 :global(.brain-loadbar) {
 position: sticky;
 top: 0;
 left: 0;
 right: 0;
 height: 2px;
 margin: 0 -48px 0;
 overflow: hidden;
 background: var(--pw-accent-wash);
 z-index: 5;
 }
 :global(.brain-loadbar span) {
 display: block;
 width: 35%;
 height: 100%;
 background: var(--pw-accent);
 border-radius: var(--pw-radius-sm);
 animation: brainLoadSlide 1.2s ease-in-out infinite;
 }
 @keyframes brainLoadSlide {
 0% { transform: translateX(-100%); }
 50% { transform: translateX(220%); }
 100% { transform: translateX(220%); }
 }
 /* Skeleton row */
 :global(.brain-skel) {
 background: linear-gradient(90deg, var(--pw-bg-alt) 0%, var(--pw-border-soft) 50%, var(--pw-bg-alt) 100%);
 background-size: 200% 100%;
 border-radius: var(--r-md);
 height: 56px;
 margin-bottom: 8px;
 animation: brainSkelShimmer 1.4s linear infinite;
 }
 @keyframes brainSkelShimmer {
 0% { background-position: 200% 0; }
 100% { background-position: -200% 0; }
 }

 :global(.brain-main) {
 padding: 32px 48px 80px 48px;
 max-width: 1280px;
 margin: 0 auto;
 width: 100%;
 box-sizing: border-box;
 }
 @media (max-width: 1024px) {
 :global(.brain-main) { padding: 24px; }
 }
 @media (max-width: 640px) {
 :global(.brain-shell) { grid-template-columns: 1fr; }
 :global(.brain-rail) { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--pw-border); padding: 12px 8px; }
 :global(.brain-main) { padding: 16px; }
 }

 /* Brain page — warm theme overrides */

 /* Tab strip — underline style matching chat response tabs */
 :global(.brain-tabs) {
 display: flex;
 gap: 0;
 border-bottom: 1px solid var(--pw-border);
 margin-bottom: 16px;
 flex-wrap: wrap;
 flex-shrink: 0;
 }
 :global(.brain-tab) {
 padding: 12px 18px;
 font: inherit;
 font-size: 12px;
 font-weight: 500;
 border: 0;
 background: transparent;
 color: var(--pw-ink-muted);
 cursor: pointer;
 border-bottom: 2px solid transparent;
 margin-bottom: -1px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 transition: color 0.15s ease, border-color 0.15s ease;
 }
 :global(.brain-tab:hover) { color: var(--pw-ink); }
 :global(.brain-tab.active) {
 color: var(--pw-ink) !important;
 font-weight: 600 !important;
 border-bottom-color: var(--pw-accent) !important;
 background: transparent;
 }

 /* Scope pills */
 :global(.brain-scope-label) {
 font-size: 11px;
 font-weight: 600;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-ink-muted);
 margin-right: 6px;
 }
 :global(.brain-scope-pill) {
 padding: 6px 12px;
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 font: inherit;
 font-size: 11px;
 font-weight: 500;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 color: var(--pw-ink-muted);
 background: transparent;
 cursor: pointer;
 transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
 }
 :global(.brain-scope-pill:hover) {
 background: var(--pw-bg-alt);
 color: var(--pw-ink);
 }
 :global(.brain-scope-pill.active) {
 background: var(--pw-ink-fill, #1a1714) !important;
 color: #fff !important;
 border-color: var(--pw-ink-fill, #1a1714) !important;
 }

 /* Modal card */
 :global(.brain-modal-card) {
 background: #fff;
 border-radius: var(--pw-radius-sm);
 box-shadow: 0 12px 48px rgba(0, 0, 0, 0.16);
 width: 560px;
 max-width: 90vw;
 max-height: 85vh;
 overflow: hidden;
 display: flex;
 flex-direction: column;
 }
 :global(.brain-modal-head) {
 background: var(--pw-bg-alt);
 color: var(--pw-ink);
 padding: 14px 18px;
 border-bottom: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 display: flex;
 justify-content: space-between;
 align-items: center;
 }
 :global(.brain-modal-title) {
 font-size: 12px;
 font-weight: 600;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 color: var(--pw-ink-muted);
 }
 :global(.brain-modal-close) {
 background: transparent;
 border: 0;
 color: var(--pw-ink-muted);
 cursor: pointer;
 font-size: 14px;
 line-height: 1;
 padding: 4px;
 }
 :global(.brain-modal-close:hover) { color: var(--pw-ink); }

 /* Modal inputs */
 :global(.brain-modal-input) {
 width: 100%;
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 padding: 10px 12px;
 font: inherit;
 font-size: 13px;
 color: var(--pw-ink);
 background: #fff;
 outline: none;
 transition: border-color 0.15s ease, box-shadow 0.15s ease;
 }
 :global(.brain-modal-input:focus) {
 border-color: var(--pw-accent);
 box-shadow: 0 0 0 3px rgba(201, 99, 66, 0.08);
 }
 :global(.brain-modal-label) {
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 color: var(--pw-ink-muted);
 font-weight: 600;
 margin-bottom: 6px;
 }
 .brain-placeholder {
   border: 1px dashed var(--pw-border, #e3ddd0);
   padding: 40px 28px;
   text-align: center;
 }
 .brain-placeholder-title {
   font-size: 18px;
   font-weight: 700;
   color: var(--pw-ink, #2c2a26);
   margin-bottom: 8px;
 }
 .brain-placeholder-sub {
   font-size: 13px;
   color: var(--pw-muted, #6b6557);
   max-width: 480px;
   margin: 0 auto;
   line-height: 1.5;
 }

 .brain-searchbar {
   position: relative;
   margin-bottom: 10px;
 }
 .brain-search-input {
   width: 100%;
   box-sizing: border-box;
   padding: 8px 30px 8px 12px;
   font-size: 13px;
   font-family: inherit;
   color: #2c2a26;
   background: #fff;
   border: 1px solid #e3ddd0;
   border-radius: var(--pw-radius-sm);
   outline: none;
 }
 .brain-search-input:focus { border-color: #c96342; }
 .brain-search-input::placeholder { color: #a39d90; }
 .brain-search-clear {
   position: absolute;
   right: 8px;
   top: 50%;
   transform: translateY(-50%);
   border: none;
   background: transparent;
   color: #8a8478;
   font-size: 18px;
   line-height: 1;
   cursor: pointer;
   padding: 0 4px;
 }
 .brain-search-clear:hover { color: #c96342; }

 .brain-gtoggle { display: inline-flex; border: 1px solid #e3ddd0; margin-bottom: 12px; }
 .brain-gtog-btn {
   padding: 6px 14px; font-size: 11px; font-weight: 600; letter-spacing: 0.04em;
   background: #fff; color: #6b6557; border: none; cursor: pointer;
   border-right: 1px solid #e3ddd0;
 }
 .brain-gtog-btn:last-child { border-right: none; }
 .brain-gtog-btn.active { background: #c96342; color: #fff; }
 .brain-gcanvas {
   width: 100%; height: 520px; background: #faf8f1;
   border: 1px solid #e3ddd0;
 }
 .brain-glegend {
   display: flex; gap: 16px; flex-wrap: wrap; align-items: center;
   margin-top: 8px; font-size: 11px; color: #6b6557;
 }
 .brain-glegend span { display: inline-flex; align-items: center; gap: 5px; }
 .brain-gdot { width: 11px; height: 11px; display: inline-block; }
 .brain-ghint { color: #a39d90; font-style: italic; margin-left: auto; }
</style>
