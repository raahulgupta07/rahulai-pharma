<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';

 /* ─── auth helper ─── */
 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 /* ─── tabs ─── */
 const tabs = [
 { id: 'types', label: 'Types' },
 { id: 'links', label: 'Links' },
 { id: 'actions', label: 'Actions' },
 { id: 'glossary', label: 'Glossary' },
 { id: 'growth', label: 'Growth' },
 { id: 'lineage', label: 'Lineage' },
 { id: 'promote', label: 'Promote' },
 { id: 'apikeys', label: 'API keys' },
 ];

 const navGroups = [
 { label: 'Catalog', items: ['types', 'links', 'actions', 'glossary'] },
 { label: 'Insights', items: ['growth', 'lineage'] },
 { label: 'Governance', items: ['promote', 'apikeys'] },
 ];

 function tabLabel(id: string): string {
 return tabs.find(t => t.id === id)?.label || id;
 }
 function tabCount(id: string): number | null {
 if (id === 'types') return summary.types_total ?? null;
 if (id === 'links') return summary.links_total ?? null;
 if (id === 'actions') return summary.actions_total ?? null;
 if (id === 'glossary') return summary.glossary ?? null;
 if (id === 'promote') return summary.promotions_pending ?? null;
 return null;
 }

 let activeTab = $state('types');
 let tabLoaded = $state<Record<string, boolean>>({});

 /* ─── summary ─── */
 let summary = $state<any>({});
 let summaryLoaded = $state(false);

 /* ─── tab error/loading state ─── */
 let tabError = $state<Record<string, string>>({});
 let tabLoading = $state<Record<string, boolean>>({});

 /* ─── refresh tracking ─── */
 let lastFetched = $state<Date | null>(null);
 let nowTick = $state(Date.now());
 let tickTimer: any = null;

 /* ─── theme ─── */
 let theme = $state<'light' | 'dark'>('light');

 /* ─── sparkline data ─── */
 let sparkSeries = $state<any>(null);

 /* ─── copy tooltip ─── */
 let copyFlash = $state(false);

 /* ─── TYPES tab ─── */
 let typesData = $state<any[]>([]);
 let typesQuery = $state('');
 let typesSource = $state<'all' | 'template' | 'learned' | 'promoted'>('all');
 let typesSort = $state<{ key: string; dir: 1 | -1 }>({ key: 'name', dir: 1 });
 let typesSearchTimer: any = null;
 let drillOpen = $state(false);
 let drillName = $state('');
 let drillDetail = $state<any>(null);
 let drillLoading = $state(false);

 /* ─── LINKS tab ─── */
 let linksData = $state<any[]>([]);
 let linksQuery = $state('');

 /* ─── ACTIONS tab ─── */
 let actionsData = $state<any[]>([]);

 /* ─── GLOSSARY tab ─── */
 let glossaryData = $state<any[]>([]);
 let glossaryScope = $state<'all' | 'global' | 'project'>('all');
 let glossaryCategory = $state<'all' | 'glossary' | 'formula' | 'alias' | 'pattern'>('all');
 let glossaryQuery = $state('');
 let glossarySearchTimer: any = null;

 /* ─── GROWTH tab ─── */
 let growthData = $state<any>(null);
 let growthDays = $state(30);
 let growthContainer: HTMLDivElement | undefined = $state();
 let growthChart: any = null;

 /* ─── LINEAGE tab ─── */
 let lineageData = $state<any>(null);
 let lineageEntity = $state('');
 let lineageMaxNodes = $state(200);
 let lineageContainer: HTMLDivElement | undefined = $state();
 let lineageChart: any = null;
 let lineageSearch = $state('');
 let lineageHiddenSources = $state<Record<string, boolean>>({});
 let lineageSelectedNode = $state<any>(null);
 let lineageNodeBusy = $state(false);

 /* ─── PROMOTE tab ─── */
 let promoteData = $state<any[]>([]);
 let promoteBusy = $state<Record<number, boolean>>({});
 let promoteSelected = $state<Record<number, boolean>>({});
 let promoteExpanded = $state<Record<number, boolean>>({});
 let promoteMinConf = $state(0.7);
 let promoteCategoryFilter = $state<string>('all');
 let promoteSort = $state<{ key: string; dir: 1 | -1 }>({ key: 'created_at', dir: -1 });
 let promoteBulkBusy = $state(false);

 /* ─── stats / auto-refresh ─── */
 let showEmptyStats = $state(false);
 let autoRefreshTimer: any = null;

 /* ─── API KEYS tab ─── */
 let apikeysData = $state<any[]>([]);
 let apikeysBusy = $state<Record<number, boolean>>({});
 let apikeysShowCreate = $state(false);
 let apikeyNew = $state({
 name: '',
 project_slug: '',
 rate_limit_per_min: 60,
 allowed_origins: '',
 scope: { types: true, glossary: true, links: true, lineage: false } as Record<string, boolean>,
 });
 let apikeyCreateBusy = $state(false);
 let apikeyJustCreatedSecret = $state<string | null>(null);
 let apikeyJustCreatedRow = $state<any | null>(null);
 let apikeyRotatedSecret = $state<{ id: number; secret: string } | null>(null);
 let apikeyUsageOpen = $state(false);
 let apikeyUsageRow = $state<any | null>(null);
 let apikeyUsageData = $state<any | null>(null);
 let apikeyUsageContainer: HTMLDivElement | undefined = $state();
 let apikeyUsageChart: any = null;
 let projectsList = $state<any[]>([]);

 /* ───── derived ───── */
 const filteredTypes = $derived.by(() => {
 let arr = [...typesData];
 const k = typesSort.key;
 const d = typesSort.dir;
 arr.sort((a: any, b: any) => {
 const av = a[k] ?? '';
 const bv = b[k] ?? '';
 if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * d;
 return String(av).localeCompare(String(bv)) * d;
 });
 return arr;
 });

 const filteredLinks = $derived(
 linksData.filter((l) => !linksQuery.trim() || (l.from_entity || '').toLowerCase().includes(linksQuery.toLowerCase()) || (l.to_entity || '').toLowerCase().includes(linksQuery.toLowerCase()))
 );

 /* parse confidence from name like "merge candidate: X (confidence=0.78)" */
 function parsePromoteConfidence(p: any): number {
 if (typeof p?.confidence === 'number') return p.confidence;
 const txt = `${p?.name || ''} ${p?.definition || ''}`;
 const m = txt.match(/confidence\s*=\s*([0-9.]+)/i);
 return m ? Math.max(0, Math.min(1, parseFloat(m[1]))) : 0;
 }
 function cleanPromoteName(p: any): string {
 const n = String(p?.name || '');
 return n.replace(/^merge candidate:\s*/i, '').replace(/\s*\(confidence=[0-9.]+\)\s*$/i, '');
 }
 /* parse left | right diff from definition for ALIAS_MERGE candidates */
 function parsePromoteDiff(p: any): { left: string; right: string } | null {
 const def = String(p?.definition || '');
 // patterns: "merge X with Y", "X <-> Y", "X => Y", "left: X right: Y"
 let m = def.match(/(?:merge|alias)\s+["']?([^"'<=>]+?)["']?\s+(?:with|to|=>|<->|→|->)\s+["']?([^"'\n]+?)["']?(?:\s|$)/i);
 if (m) return { left: m[1].trim(), right: m[2].trim() };
 m = def.match(/^([^|]+)\|([^|]+)$/);
 if (m) return { left: m[1].trim(), right: m[2].trim() };
 const name = String(p?.name || '');
 m = name.match(/(?:merge candidate:\s*)?([^→<=>]+?)\s*(?:→|<->|=>|with|to)\s*(.+?)(?:\s*\(|$)/i);
 if (m) return { left: m[1].trim(), right: m[2].trim() };
 return null;
 }

 const allStats = $derived([
 { label: 'Types', val: summary.types_total, icon: '⊞' },
 { label: 'Links', val: summary.links_total, icon: '↔' },
 { label: 'Actions', val: summary.actions_total, icon: '▶' },
 { label: 'Formulas', val: summary.formulas, icon: 'ƒ' },
 { label: 'Glossary', val: summary.glossary, icon: 'Aa' },
 { label: 'Aliases', val: summary.aliases, icon: '≡' },
 ]);
 const visibleStats = $derived(showEmptyStats ? allStats : allStats.filter((c: any) => (c.val ?? 0) > 0));

 const promoteCategories = $derived.by(() => {
 const set = new Set<string>();
 for (const p of promoteData) if (p?.category) set.add(String(p.category));
 return Array.from(set).sort();
 });

 const filteredPromote = $derived.by(() => {
 let arr = promoteData.filter((p: any) => {
 const c = parsePromoteConfidence(p);
 if (c < promoteMinConf) return false;
 if (promoteCategoryFilter !== 'all' && (p.category || '') !== promoteCategoryFilter) return false;
 return true;
 });
 const k = promoteSort.key;
 const d = promoteSort.dir;
 arr.sort((a: any, b: any) => {
 let av: any, bv: any;
 if (k === 'confidence') { av = parsePromoteConfidence(a); bv = parsePromoteConfidence(b); }
 else if (k === 'name') { av = cleanPromoteName(a); bv = cleanPromoteName(b); }
 else { av = a[k] ?? ''; bv = b[k] ?? ''; }
 if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * d;
 return String(av).localeCompare(String(bv)) * d;
 });
 return arr;
 });

 const promoteSelectedIds = $derived(Object.keys(promoteSelected).filter(k => promoteSelected[+k]).map(Number));

 function togglePromoteSort(key: string) {
 if (promoteSort.key === key) promoteSort = { key, dir: (promoteSort.dir === 1 ? -1 : 1) as 1 | -1 };
 else promoteSort = { key, dir: key === 'confidence' || key === 'created_at' ? -1 : 1 };
 }
 function toggleSelectAllPromote() {
 const allIds = filteredPromote.map((p: any) => p.id);
 const allSel = allIds.every(id => promoteSelected[id]);
 const next: Record<number, boolean> = {};
 if (!allSel) for (const id of allIds) next[id] = true;
 promoteSelected = next;
 }
 function clearPromoteSelection() { promoteSelected = {}; }
 async function bulkApprovePromote() {
 if (promoteSelectedIds.length === 0) return;
 promoteBulkBusy = true;
 try {
 for (const id of promoteSelectedIds) {
 try { await fetchJSON(`/api/ontology/promotions/${id}/approve`, { method: 'POST' }); } catch {}
 }
 promoteSelected = {};
 await loadPromote();
 await loadSummary();
 } finally { promoteBulkBusy = false; }
 }
 async function bulkRejectPromote() {
 if (promoteSelectedIds.length === 0) return;
 promoteBulkBusy = true;
 try {
 for (const id of promoteSelectedIds) {
 try { await fetchJSON(`/api/ontology/promotions/${id}/reject`, { method: 'POST' }); } catch {}
 }
 promoteSelected = {};
 await loadPromote();
 await loadSummary();
 } finally { promoteBulkBusy = false; }
 }

 /* ─── lineage helpers ─── */
 function toggleLineageSource(src: string) {
 lineageHiddenSources = { ...lineageHiddenSources, [src]: !lineageHiddenSources[src] };
 renderLineage();
 }
 function lineageZoom(factor: number) {
 if (!lineageChart) return;
 try {
 const opt = lineageChart.getOption();
 const z = (opt.series?.[0]?.zoom ?? 1) * factor;
 lineageChart.setOption({ series: [{ zoom: Math.max(0.2, Math.min(5, z)) }] });
 } catch {}
 }
 function lineageFit() {
 if (!lineageChart) return;
 try { lineageChart.setOption({ series: [{ zoom: 1, center: null }] }); } catch {}
 }
 function lineageReset() {
 lineageSearch = '';
 lineageHiddenSources = {};
 lineageSelectedNode = null;
 renderLineage();
 }
 async function openLineageNodeDetail(name: string) {
 lineageNodeBusy = true;
 lineageSelectedNode = { name, loading: true };
 try {
 const d = await fetchJSON(`/api/ontology/types/${encodeURIComponent(name)}`);
 lineageSelectedNode = { name, ...d };
 } catch (e: any) {
 lineageSelectedNode = { name, error: e.message };
 } finally { lineageNodeBusy = false; }
 }
 function closeLineageNodeDetail() { lineageSelectedNode = null; }
 async function promoteLineageNode() {
 if (!lineageSelectedNode?.name) return;
 try {
 await fetchJSON(`/api/ontology/types/${encodeURIComponent(lineageSelectedNode.name)}/promote`, { method: 'POST' });
 } catch {}
 }

 const lastFetchedLabel = $derived.by(() => {
 if (!lastFetched) return '';
 const diff = Math.max(0, Math.floor((nowTick - lastFetched.getTime()) / 1000));
 if (diff < 60) return `last ${diff}s ago`;
 if (diff < 3600) return `last ${Math.floor(diff / 60)}m ago`;
 return `last ${Math.floor(diff / 3600)}h ago`;
 });

 /* ─── data loaders ─── */
 async function fetchJSON(url: string, opts: any = {}): Promise<any> {
 const res = await fetch(url, { ...opts, headers: { ..._h(), ...(opts.headers || {}) } });
 if (!res.ok) {
 let msg = `HTTP ${res.status}`;
 try { const d = await res.json(); msg = d.detail || d.message || msg; } catch {}
 throw new Error(msg);
 }
 return res.json();
 }

 async function loadSummary() {
 try {
 summary = await fetchJSON('/api/ontology/summary');
 summaryLoaded = true;
 lastFetched = new Date();
 } catch (e: any) {
 tabError['_summary'] = e.message;
 }
 }

 async function loadSparklines() {
 try {
 const d = await fetchJSON('/api/ontology/growth?days=14');
 sparkSeries = d.series || {};
 } catch {
 sparkSeries = {};
 }
 }

 async function loadTypes() {
 tabLoading['types'] = true;
 tabError['types'] = '';
 try {
 const params = new URLSearchParams();
 params.set('source', typesSource);
 if (typesQuery.trim()) params.set('q', typesQuery.trim());
 params.set('limit', '200');
 const d = await fetchJSON(`/api/ontology/types?${params.toString()}`);
 typesData = d.types || [];
 tabLoaded['types'] = true;
 lastFetched = new Date();
 } catch (e: any) {
 tabError['types'] = e.message;
 }
 tabLoading['types'] = false;
 }

 async function loadLinks() {
 tabLoading['links'] = true;
 tabError['links'] = '';
 try {
 const d = await fetchJSON('/api/ontology/links?limit=300');
 linksData = d.links || [];
 tabLoaded['links'] = true;
 lastFetched = new Date();
 } catch (e: any) {
 tabError['links'] = e.message;
 }
 tabLoading['links'] = false;
 }

 async function loadActions() {
 tabLoading['actions'] = true;
 tabError['actions'] = '';
 try {
 const d = await fetchJSON('/api/ontology/actions?limit=300');
 actionsData = d.actions || [];
 tabLoaded['actions'] = true;
 lastFetched = new Date();
 } catch (e: any) {
 tabError['actions'] = e.message;
 }
 tabLoading['actions'] = false;
 }

 async function loadGlossary() {
 tabLoading['glossary'] = true;
 tabError['glossary'] = '';
 try {
 const params = new URLSearchParams();
 params.set('scope', glossaryScope);
 if (glossaryQuery.trim()) params.set('q', glossaryQuery.trim());
 params.set('limit', '500');
 const d = await fetchJSON(`/api/ontology/glossary?${params.toString()}`);
 let g = d.glossary || [];
 if (glossaryCategory !== 'all') g = g.filter((x: any) => (x.category || '').toLowerCase() === glossaryCategory);
 glossaryData = g;
 tabLoaded['glossary'] = true;
 lastFetched = new Date();
 } catch (e: any) {
 tabError['glossary'] = e.message;
 }
 tabLoading['glossary'] = false;
 }

 async function loadGrowth() {
 tabLoading['growth'] = true;
 tabError['growth'] = '';
 try {
 growthData = await fetchJSON(`/api/ontology/growth?days=${growthDays}`);
 tabLoaded['growth'] = true;
 lastFetched = new Date();
 setTimeout(() => renderGrowth(), 80);
 } catch (e: any) {
 tabError['growth'] = e.message;
 }
 tabLoading['growth'] = false;
 }

 async function loadLineage() {
 tabLoading['lineage'] = true;
 tabError['lineage'] = '';
 try {
 const params = new URLSearchParams();
 if (lineageEntity) params.set('entity', lineageEntity);
 params.set('max_nodes', String(lineageMaxNodes));
 lineageData = await fetchJSON(`/api/ontology/lineage?${params.toString()}`);
 tabLoaded['lineage'] = true;
 lastFetched = new Date();
 setTimeout(() => renderLineage(), 80);
 } catch (e: any) {
 tabError['lineage'] = e.message;
 }
 tabLoading['lineage'] = false;
 }

 async function loadPromote() {
 tabLoading['promote'] = true;
 tabError['promote'] = '';
 try {
 const d = await fetchJSON('/api/ontology/promotions/pending');
 promoteData = d.pending || [];
 tabLoaded['promote'] = true;
 lastFetched = new Date();
 } catch (e: any) {
 tabError['promote'] = e.message;
 }
 tabLoading['promote'] = false;
 }

 async function approvePromotion(id: number) {
 promoteBusy[id] = true;
 try {
 await fetchJSON(`/api/ontology/promotions/${id}/approve`, { method: 'POST' });
 await loadPromote();
 await loadSummary();
 } catch (e: any) {
 tabError['promote'] = e.message;
 }
 promoteBusy[id] = false;
 }

 async function rejectPromotion(id: number) {
 promoteBusy[id] = true;
 try {
 await fetchJSON(`/api/ontology/promotions/${id}/reject`, { method: 'POST' });
 await loadPromote();
 await loadSummary();
 } catch (e: any) {
 tabError['promote'] = e.message;
 }
 promoteBusy[id] = false;
 }

 /* ─── tab switch ─── */
 async function switchTab(id: string) {
 activeTab = id;
 if (tabLoaded[id]) {
 // re-render charts on revisit
 if (id === 'growth') setTimeout(() => renderGrowth(), 80);
 if (id === 'lineage') setTimeout(() => renderLineage(), 80);
 return;
 }
 if (id === 'types') await loadTypes();
 else if (id === 'links') await loadLinks();
 else if (id === 'actions') await loadActions();
 else if (id === 'glossary') await loadGlossary();
 else if (id === 'growth') await loadGrowth();
 else if (id === 'lineage') await loadLineage();
 else if (id === 'promote') await loadPromote();
 else if (id === 'apikeys') await loadApiKeys();
 }

 /* ─── API KEYS loaders ─── */
 async function loadApiKeys() {
 tabLoading['apikeys'] = true;
 tabError['apikeys'] = '';
 try {
 const d = await fetchJSON('/api/ontology/api-keys');
 apikeysData = d.keys || [];
 tabLoaded['apikeys'] = true;
 // Best-effort load of project list for the dropdown.
 if (projectsList.length === 0) {
 try {
 const pj = await fetchJSON('/api/projects');
 projectsList = (pj.projects || pj || []).map((p: any) => ({
 slug: p.slug, name: p.name || p.slug,
 }));
 } catch {}
 }
 } catch (e: any) {
 tabError['apikeys'] = e.message;
 } finally {
 tabLoading['apikeys'] = false;
 }
 }

 async function createApiKey() {
 if (!apikeyNew.name.trim()) return;
 apikeyCreateBusy = true;
 try {
 const origins = apikeyNew.allowed_origins
 .split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
 const body = {
 name: apikeyNew.name.trim(),
 project_slug: apikeyNew.project_slug || null,
 scope: apikeyNew.scope,
 rate_limit_per_min: Number(apikeyNew.rate_limit_per_min) || 60,
 allowed_origins: origins,
 };
 const d = await fetchJSON('/api/ontology/api-keys', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify(body),
 });
 apikeyJustCreatedSecret = d.secret;
 apikeyJustCreatedRow = d;
 apikeysShowCreate = false;
 // Reset form, reload list
 apikeyNew = { name: '', project_slug: '', rate_limit_per_min: 60,
 allowed_origins: '',
 scope: { types: true, glossary: true, links: true, lineage: false } };
 await loadApiKeys();
 } catch (e: any) {
 tabError['apikeys'] = e.message;
 } finally {
 apikeyCreateBusy = false;
 }
 }

 async function rotateApiKey(id: number) {
 if (!confirm('Rotate this key? The old secret will stop working immediately.')) return;
 apikeysBusy[id] = true;
 try {
 const d = await fetchJSON(`/api/ontology/api-keys/${id}/rotate`, { method: 'POST' });
 apikeyRotatedSecret = { id, secret: d.secret };
 await loadApiKeys();
 } catch (e: any) {
 tabError['apikeys'] = e.message;
 } finally {
 apikeysBusy[id] = false;
 }
 }

 async function revokeApiKey(id: number) {
 if (!confirm('Revoke this key? Status becomes "revoked"; rows are kept for audit.')) return;
 apikeysBusy[id] = true;
 try {
 await fetchJSON(`/api/ontology/api-keys/${id}`, { method: 'DELETE' });
 await loadApiKeys();
 } catch (e: any) {
 tabError['apikeys'] = e.message;
 } finally {
 apikeysBusy[id] = false;
 }
 }

 async function openUsage(row: any) {
 apikeyUsageRow = row;
 apikeyUsageOpen = true;
 apikeyUsageData = null;
 try {
 const d = await fetchJSON(`/api/ontology/api-keys/${row.id}/usage?days=14`);
 apikeyUsageData = d.usage || d;
 setTimeout(() => renderUsageChart(), 60);
 } catch (e: any) {
 apikeyUsageData = { error: e.message };
 }
 }

 function closeUsage() {
 apikeyUsageOpen = false;
 apikeyUsageRow = null;
 apikeyUsageData = null;
 if (apikeyUsageChart) {
 try { apikeyUsageChart.dispose(); } catch {}
 apikeyUsageChart = null;
 }
 }

 async function renderUsageChart() {
 if (!apikeyUsageContainer || !apikeyUsageData?.daily) return;
 try {
 const echarts = (await import('echarts')).default || (await import('echarts'));
 if (apikeyUsageChart) { try { apikeyUsageChart.dispose(); } catch {} }
 apikeyUsageChart = (echarts as any).init(apikeyUsageContainer);
 apikeyUsageChart.setOption({
 grid: { left: 40, right: 16, top: 24, bottom: 30 },
 tooltip: { trigger: 'axis' },
 xAxis: { type: 'category',
 data: apikeyUsageData.daily.map((d: any) => d.date),
 axisLabel: { fontSize: 9 } },
 yAxis: { type: 'value', axisLabel: { fontSize: 9 } },
 series: [{
 name: 'calls', type: 'line', smooth: true,
 areaStyle: { color: 'rgba(0,120,212,0.15)' },
 lineStyle: { color: '#0078d4', width: 2 },
 itemStyle: { color: '#0078d4' },
 data: apikeyUsageData.daily.map((d: any) => d.count),
 }],
 });
 } catch (e) {
 console.error('renderUsageChart failed', e);
 }
 }

 function copyText(s: string) {
 try { navigator.clipboard.writeText(s); copyFlash = true;
 setTimeout(() => copyFlash = false, 1200); } catch {}
 }

 /* ─── debounced search ─── */
 function onTypesSearchInput() {
 if (typesSearchTimer) clearTimeout(typesSearchTimer);
 typesSearchTimer = setTimeout(() => loadTypes(), 300);
 }

 function onGlossarySearchInput() {
 if (glossarySearchTimer) clearTimeout(glossarySearchTimer);
 glossarySearchTimer = setTimeout(() => loadGlossary(), 300);
 }

 let lineageSearchTimer: any = null;
 function onLineageSearchInput() {
 if (lineageSearchTimer) clearTimeout(lineageSearchTimer);
 lineageSearchTimer = setTimeout(() => renderLineage(), 200);
 }

 /* ─── drill drawer ─── */
 async function openDrill(name: string) {
 drillName = name;
 drillOpen = true;
 drillDetail = null;
 drillLoading = true;
 try {
 drillDetail = await fetchJSON(`/api/ontology/types/${encodeURIComponent(name)}`);
 } catch (e: any) {
 drillDetail = { error: e.message };
 }
 drillLoading = false;
 }

 function closeDrill() {
 drillOpen = false;
 drillDetail = null;
 }

 async function copyDetailJSON() {
 if (!drillDetail) return;
 try {
 await navigator.clipboard.writeText(JSON.stringify(drillDetail, null, 2));
 copyFlash = true;
 setTimeout(() => { copyFlash = false; }, 1000);
 } catch {}
 }

 /* ─── ESC closes drawer ─── */
 function onKeydown(e: KeyboardEvent) {
 if (e.key === 'Escape') {
 if (drillOpen) closeDrill();
 if (lineageSelectedNode) closeLineageNodeDetail();
 }
 }

 /* ─── refresh all ─── */
 async function refreshAll() {
 summaryLoaded = false;
 tabLoaded = {};
 await loadSummary();
 await loadSparklines();
 await switchTab(activeTab);
 }

 /* ─── sort toggle ─── */
 function toggleSort(key: string) {
 if (typesSort.key === key) typesSort = { key, dir: (typesSort.dir === 1 ? -1 : 1) as 1 | -1 };
 else typesSort = { key, dir: 1 };
 }

 /* ─── source persistence ─── */
 function setTypesSource(src: any) {
 typesSource = src;
 try { localStorage.setItem('ontology_source_filter', src); } catch {}
 loadTypes();
 }

 /* ─── theme ─── */
 function toggleTheme() {
 theme = theme === 'light' ? 'dark' : 'light';
 try { localStorage.setItem('ontology_theme', theme); } catch {}
 applyTheme();
 }

 function applyTheme() {
 if (typeof document === 'undefined') return;
 if (theme === 'dark') document.body.classList.add('ontology-dark');
 else document.body.classList.remove('ontology-dark');
 }

 /* ─── source colors ─── */
 function sourceColor(src: string): string {
 const m: Record<string, string> = {
 template: 'var(--pw-success)',
 learned: '#c5934a',
 promoted: '#9b59b6',
 web: '#0078d4',
 };
 return m[(src || '').toLowerCase()] || '#666';
 }

 function sourceTextColor(src: string): string {
 const s = (src || '').toLowerCase();
 if (s === 'learned') return 'var(--pw-ink)';
 return '#fff';
 }

 function fmtDate(d: string | null | undefined): string {
 if (!d) return '-';
 try {
 return new Date(d).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
 } catch {
 return String(d).slice(0, 19);
 }
 }

 /* ─── Sparkline SVG ─── */
 function sparkline(values: number[]): string {
 if (!values || values.length === 0) return '';
 const allZero = values.every((v) => !v || v === 0);
 if (allZero) return '';
 const w = 60;
 const h = 20;
 const n = values.length;
 const max = Math.max(...values, 1);
 const min = Math.min(...values, 0);
 const range = max - min || 1;
 const pts = values.map((v, i) => {
 const x = (i / (n - 1 || 1)) * w;
 const y = h - ((v - min) / range) * (h - 2) - 1;
 return `${x.toFixed(1)},${y.toFixed(1)}`;
 });
 return pts.join(' ');
 }

 function sparkValuesFor(label: string): number[] {
 if (!sparkSeries) return [];
 const map: Record<string, string> = {
 TYPES: 'new_entities',
 LINKS: 'new_triples',
 ACTIONS: 'new_workflows',
 FORMULAS: 'new_memories',
 GLOSSARY: 'new_memories',
 ALIASES: 'new_memories',
 };
 const key = map[label];
 const arr = (sparkSeries as any)[key] || [];
 return Array.isArray(arr) ? arr.slice(-14) : [];
 }

 /* ─── CSV export ─── */
 function escCsv(v: any): string {
 if (v == null) return '';
 const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
 if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
 return s;
 }

 function downloadCSV(filename: string, headers: string[], rows: any[][]) {
 const csv = [headers.map(escCsv).join(','), ...rows.map((r) => r.map(escCsv).join(','))].join('\n');
 const blob = new Blob([csv], { type: 'text/csv' });
 const url = URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url;
 a.download = filename;
 document.body.appendChild(a);
 a.click();
 document.body.removeChild(a);
 setTimeout(() => URL.revokeObjectURL(url), 1000);
 }

 function exportTypesCsv() {
 const rows = filteredTypes.map((t: any) => [
 t.name, (t.aliases || []).join('; '), (t.used_in_templates || []).join('; '),
 t.active_agents ?? 0, t.confidence ?? '', t.source ?? '',
 ]);
 downloadCSV('ontology_types.csv', ['name', 'aliases', 'templates', 'agents', 'confidence', 'source'], rows);
 }

 function exportLinksCsv() {
 const rows = filteredLinks.map((l: any) => [
 l.from_entity, l.relation, l.to_entity, l.agent_count ?? 0, l.source ?? '', l.confidence ?? '',
 ]);
 downloadCSV('ontology_links.csv', ['from', 'relation', 'to', 'agents', 'source', 'confidence'], rows);
 }

 function exportActionsCsv() {
 const rows = actionsData.map((a: any) => [
 a.name, a.schedule || '', a.action || '', (a.templates_using || []).join('; '),
 a.active_count ?? 0, a.paused_count ?? 0, a.failed_count ?? 0, a.last_run_avg || '',
 ]);
 downloadCSV('ontology_actions.csv', ['name', 'schedule', 'action', 'templates', 'active', 'paused', 'failed', 'last_run'], rows);
 }

 function exportGlossaryCsv() {
 const rows = glossaryData.map((g: any) => [
 g.name, g.category || '', g.definition || '', g.scope || '', g.project_slug || '',
 ]);
 downloadCSV('ontology_glossary.csv', ['term', 'category', 'definition', 'scope', 'project'], rows);
 }

 /* ─── ECharts: GROWTH line ─── */
 async function renderGrowth() {
 if (!growthContainer || !growthData) return;
 const echarts = await import('echarts');
 if (growthChart) growthChart.dispose();
 growthChart = echarts.init(growthContainer);
 const days = growthData.days || growthDays;
 const xs: string[] = [];
 const today = new Date();
 for (let i = days - 1; i >= 0; i--) {
 const d = new Date(today);
 d.setDate(d.getDate() - i);
 xs.push(d.toISOString().slice(5, 10));
 }
 const series = (growthData.series || {});
 // API returns [{date,count},...]; align to xs by date and extract count.
 const align = (arr: any[]): number[] => {
 const map: Record<string, number> = {};
 for (const r of (arr || [])) {
 if (!r) continue;
 const d = String(r.date || r.day || '').slice(5, 10);
 map[d] = Number(r.count ?? r.value ?? 0);
 }
 return xs.map(x => map[x] ?? 0);
 };
 const sEntities = align(series.new_entities);
 const sTriples = align(series.new_triples);
 const sMemories = align(series.new_memories);
 const sPromotions = align(series.new_promotions);
 const sWorkflows = align(series.new_workflows);
 growthChart.setOption({
 backgroundColor: 'transparent',
 textStyle: { color: '#2c2a26' },
 tooltip: { trigger: 'axis' },
 legend: {
 data: ['entities', 'triples', 'memories', 'promotions', 'workflows'],
 textStyle: { color: '#2c2a26', fontSize: 11 },
 top: 4,
 },
 grid: { top: 40, left: 50, right: 20, bottom: 40 },
 xAxis: {
 type: 'category',
 data: xs,
 axisLabel: { fontSize: 10, color: '#807a72' },
 axisLine: { lineStyle: { color: '#e4e2dd' } },
 splitLine: { lineStyle: { color: '#f0eee5' } },
 },
 yAxis: {
 type: 'value',
 axisLabel: { fontSize: 10, color: '#807a72' },
 axisLine: { lineStyle: { color: '#e4e2dd' } },
 splitLine: { lineStyle: { color: '#f0eee5' } },
 },
 series: [
 { name: 'entities', type: 'line', data: sEntities, smooth: true, itemStyle: { color: '#c96342' }, lineStyle: { color: '#c96342', width: 2 } },
 { name: 'triples', type: 'line', data: sTriples, smooth: true, itemStyle: { color: '#3a8dff' }, lineStyle: { color: '#3a8dff', width: 2 } },
 { name: 'memories', type: 'line', data: sMemories, smooth: true, itemStyle: { color: '#f59e0b' }, lineStyle: { color: '#f59e0b', width: 2 } },
 { name: 'promotions', type: 'line', data: sPromotions, smooth: true, itemStyle: { color: '#9b6dff' }, lineStyle: { color: '#9b6dff', width: 2 } },
 { name: 'workflows', type: 'line', data: sWorkflows, smooth: true, itemStyle: { color: '#dc2626' }, lineStyle: { color: '#dc2626', width: 2 } },
 ],
 });
 }

 /* ─── ECharts: LINEAGE force graph (zoom/pan via roam, collision via repulsion) ─── */
 async function renderLineage() {
 if (!lineageContainer || !lineageData) return;
 const echarts = await import('echarts');
 if (lineageChart) lineageChart.dispose();
 lineageChart = echarts.init(lineageContainer, 'dark');
 const cats = ['template', 'learned', 'promoted', 'web', 'other'];
 const q = lineageSearch.trim().toLowerCase();
 const visibleNodeIds = new Set<string>();
 const rawNodes = (lineageData.nodes || []).filter((n: any) => {
 const src = (n.source || 'other').toLowerCase();
 if (lineageHiddenSources[src]) return false;
 return true;
 });
 let centroidX = 0, centroidY = 0, matchCount = 0;
 const nodes = rawNodes.map((n: any) => {
 visibleNodeIds.add(String(n.id));
 const isMatch = q && String(n.name || '').toLowerCase().includes(q);
 if (isMatch) matchCount++;
 const size = Math.max(18, Math.min(46, 14 + Math.sqrt((n.value || 1)) * 6));
 return {
 id: String(n.id),
 name: n.name,
 value: n.value || 1,
 symbolSize: size,
 category: n.source || 'other',
 itemStyle: {
 color: sourceColor(n.source),
 borderColor: isMatch ? '#ffeb3b' : 'transparent',
 borderWidth: isMatch ? 3 : 0,
 opacity: q && !isMatch ? 0.25 : 1,
 },
 label: {
 show: true,
 position: 'right',
 fontSize: 10,
 color: q && !isMatch ? '#666' : '#eee',
 distance: 4,
 fontWeight: isMatch ? 700 : 400,
 },
 };
 });
 const edges = (lineageData.edges || [])
 .filter((e: any) => visibleNodeIds.has(String(e.source)) && visibleNodeIds.has(String(e.target)))
 .map((e: any) => ({
 source: String(e.source),
 target: String(e.target),
 label: { show: false, formatter: e.relation || '', fontSize: 8 },
 lineStyle: { color: '#555', width: 1.1, opacity: 0.7, curveness: 0.08 },
 }));

 lineageChart.setOption({
 backgroundColor: '#1a1714',
 tooltip: {
 trigger: 'item',
 formatter: (p: any) => p.dataType === 'node'
 ? `<b>${p.data.name}</b><br/>source: ${p.data.category}<br/>refs: ${p.data.value}`
 : '',
 },
 animationDurationUpdate: 400,
 series: [{
 type: 'graph',
 layout: 'force',
 roam: true,
 draggable: true,
 zoom: 1,
 data: nodes,
 links: edges,
 categories: cats.map((c) => ({ name: c, itemStyle: { color: sourceColor(c) } })),
 force: {
 repulsion: 380,
 gravity: 0.06,
 edgeLength: [70, 180],
 friction: 0.6,
 layoutAnimation: true,
 },
 emphasis: { focus: 'adjacency', lineStyle: { width: 2, color: '#c96342' } },
 lineStyle: { color: '#555', width: 1.1, opacity: 0.7 },
 }],
 });

 // auto-pan to search matches by computing centroid post-layout
 if (q && matchCount > 0) {
 setTimeout(() => {
 try {
 const opt = lineageChart.getOption();
 const data = opt.series?.[0]?.data || [];
 let cx = 0, cy = 0, n = 0;
 for (const d of data) {
 if (d.itemStyle?.borderWidth === 3) {
 const pos = lineageChart.convertToPixel({ seriesIndex: 0 }, d.id);
 if (pos) { cx += pos[0]; cy += pos[1]; n++; }
 }
 }
 if (n > 0) {
 const w = lineageContainer!.clientWidth;
 const h = lineageContainer!.clientHeight;
 const dx = w / 2 - cx / n;
 const dy = h / 2 - cy / n;
 lineageChart.setOption({ series: [{ center: [w / 2 + dx, h / 2 + dy] }] });
 }
 } catch {}
 }, 600);
 }

 lineageChart.off('click');
 lineageChart.on('click', (p: any) => {
 if (p.dataType === 'node' && p.data?.name) openLineageNodeDetail(p.data.name);
 });
 }

 /* ─── lifecycle ─── */
 onMount(async () => {
 // restore filters + theme
 try {
 const sf = localStorage.getItem('ontology_source_filter');
 if (sf && ['all', 'template', 'learned', 'promoted'].includes(sf)) typesSource = sf as any;
 // force light: remove any persisted dark mode + body class
 try { localStorage.removeItem('ontology_theme'); } catch {}
 if (typeof document !== 'undefined') document.body.classList.remove('ontology-dark');
 } catch {}

 // super admin guard
 try {
 const res = await fetch('/api/auth/check', { headers: _h() });
 if (res.ok) {
 const me = await res.json();
 if (!me.is_super) { window.location.href = '/ui/home'; return; }
 } else {
 window.location.href = '/ui/home';
 return;
 }
 } catch {
 window.location.href = '/ui/home';
 return;
 }

 window.addEventListener('keydown', onKeydown);
 tickTimer = setInterval(() => { nowTick = Date.now(); }, 5000);

 await loadSummary();
 await loadSparklines();
 await switchTab('types');

 // auto-refresh every 30s: re-loads summary + active tab
 autoRefreshTimer = setInterval(async () => {
 try {
 await loadSummary();
 // refresh promote tab in background so badge stays current
 if (activeTab !== 'promote') {
 try { const d = await fetchJSON('/api/ontology/promotions/pending'); promoteData = d.pending || []; } catch {}
 } else {
 await loadPromote();
 }
 } catch {}
 }, 30000);
 });

 onDestroy(() => {
 if (growthChart) { growthChart.dispose(); growthChart = null; }
 if (lineageChart) { lineageChart.dispose(); lineageChart = null; }
 if (tickTimer) clearInterval(tickTimer);
 if (autoRefreshTimer) clearInterval(autoRefreshTimer);
 if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown);
 });
</script>

<style>
 /* ─── Left-rail shell (matches Brain + Command Center) ─── */
 :global(.ow-shell) {
 display: grid;
 grid-template-columns: 220px 1fr;
 background: var(--pw-bg);
 min-height: calc(100vh - 56px);
 font-family: var(--pw-sans);
 color: var(--pw-ink);
 }
 :global(.ow-rail) {
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
 :global(.ow-rail-group) { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
 :global(.ow-rail-grouplabel) {
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-muted);
 padding: 12px 14px 6px;
 font-weight: 600;
 }
 :global(.ow-rail-btn) {
 display: flex;
 align-items: center;
 justify-content: space-between;
 gap: 8px;
 width: 100%;
 text-align: left;
 background: transparent;
 border: none;
 padding: 8px 12px;
 border-radius: 0;
 font-size: 11px;
 color: var(--pw-ink);
 font-family: inherit;
 cursor: pointer;
 line-height: 1.3;
 }
 :global(.ow-rail-label) { flex: 1; }
 :global(.ow-rail-count) {
 font-size: 11px;
 color: var(--pw-muted);
 font-variant-numeric: tabular-nums;
 }
 :global(.ow-rail-badge) {
 background: #dc2626;
 color: #fff;
 font-size: 10px;
 font-weight: 700;
 padding: 1px 7px;
 border-radius: 0;
 font-variant-numeric: tabular-nums;
 line-height: 1.4;
 }
 :global(.ow-rail-btn:hover) { background: rgba(201, 99, 66, 0.04); }
 :global(.ow-rail-btn.active) {
 background: rgba(201, 99, 66, 0.08);
 color: var(--pw-accent);
 font-weight: 600;
 }
 :global(.ow-rail-btn.active .ow-rail-count) { color: var(--pw-accent); }

 :global(.ow-main) {
 padding: 32px 48px 80px 48px;
 max-width: 1280px;
 margin: 0 auto;
 width: 100%;
 box-sizing: border-box;
 }
 @media (max-width: 1024px) {
 :global(.ow-main) { padding: 24px; }
 }
 @media (max-width: 640px) {
 :global(.ow-shell) { grid-template-columns: 1fr; }
 :global(.ow-rail) { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--pw-border); padding: 12px 8px; }
 :global(.ow-main) { padding: 16px; }
 }

 @keyframes ontologyPulse {
 0%, 100% { opacity: 0.55; }
 50% { opacity: 1; }
 }
 .skel-row {
 height: 14px;
 background: #ddd;
 margin: 8px 0;
 border-radius: 0;
 animation: ontologyPulse 1.4s ease-in-out infinite;
 }
 :global(body.ontology-dark) {
 background: var(--pw-ink) !important;
 }
 :global(body.ontology-dark) .ontology-card {
 background: #222 !important;
 color: var(--pw-surface-warm) !important;
 }
 :global(body.ontology-dark) .ontology-bg {
 background: var(--pw-ink) !important;
 }

 /* ─── warm cream/white theme overrides ─── */
 .ow-page { --pw-border: var(--pw-border, #e5dfd4); --pw-bg-alt: var(--pw-bg-alt, #f5f0e6); --pw-ink-muted: var(--pw-ink-muted, #6f675c); --pw-serif: var(--pw-serif, var(--pw-font-headline, 'EB Garamond', serif)); }

 /* hero summary tiles */
 .ow-page .ow-stats {
 display: grid;
 grid-template-columns: repeat(6, 1fr);
 gap: 14px;
 margin: 24px 0;
 }
 @media (max-width: 1200px) { .ow-page .ow-stats { grid-template-columns: repeat(3, 1fr); } }
 @media (max-width: 720px) { .ow-page .ow-stats { grid-template-columns: repeat(2, 1fr); } }
 .ow-page .ow-stat {
 background: #fff;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 20px 22px;
 display: flex;
 flex-direction: column;
 gap: 12px;
 transition: border-color .12s, box-shadow .12s;
 text-align: left;
 box-shadow: none;
 }
 .ow-page .ow-stat:hover {
 border-color: var(--pw-ink-muted);
 box-shadow: 0 2px 12px rgba(0,0,0,0.04);
 }
 .ow-page .ow-stat-row {
 display: flex;
 align-items: flex-start;
 justify-content: space-between;
 }
 .ow-page .ow-stat-value {
 font-family: var(--pw-serif);
 font-weight: 400;
 font-size: 34px;
 line-height: 1;
 color: var(--pw-ink);
 letter-spacing: -0.01em;
 }
 .ow-page .ow-stat-icon {
 width: 28px;
 height: 28px;
 display: flex;
 align-items: center;
 justify-content: center;
 color: var(--pw-ink-muted);
 font-size: 12px;
 opacity: 0.6;
 }
 .ow-page .ow-stat-label {
 font-size: 11px;
 font-weight: 500;
 color: var(--pw-ink-muted);
 text-transform: none;
 letter-spacing: 0;
 margin: 0;
 }

 /* sub-stat meta row */
 .ow-page .ow-meta {
 display: flex;
 gap: 24px;
 flex-wrap: wrap;
 font-size: 12.5px;
 color: var(--pw-ink-muted);
 margin: -8px 0 20px;
 }
 .ow-page .ow-meta-item {
 display: inline-flex;
 gap: 6px;
 align-items: baseline;
 }
 .ow-page .ow-meta-item strong {
 color: var(--pw-ink);
 font-weight: 600;
 }
 .ow-page .ow-meta-item .ow-meta-pending {
 color: #9b6dff;
 }

 /* tab bar */
 .ow-page .ow-tabs {
 display: flex;
 gap: 0;
 border-bottom: 1px solid var(--pw-border);
 margin: 24px 0 0;
 flex-wrap: wrap;
 }
 .ow-page .ow-tab {
 padding: 12px 18px;
 font: inherit;
 font-size: 11px;
 font-weight: 500;
 border: 0;
 background: transparent;
 color: var(--pw-ink-muted);
 cursor: pointer;
 border-bottom: 2px solid transparent;
 margin-bottom: -1px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 display: inline-flex;
 align-items: center;
 gap: 6px;
 }
 .ow-page .ow-tab:hover { color: var(--pw-ink); }
 .ow-page .ow-tab.active {
 color: var(--pw-ink) !important;
 font-weight: 600 !important;
 border-bottom-color: var(--pw-accent) !important;
 }
 .ow-page .ow-tab-count {
 background: var(--pw-bg-alt);
 color: var(--pw-ink-muted);
 padding: 1px 7px;
 border-radius: 0;
 font-size: 10px;
 font-weight: 600;
 }
 .ow-page .ow-tab.active .ow-tab-count {
 background: var(--pw-accent-tint, rgba(201,99,66,0.18));
 color: var(--pw-accent);
 }

 /* tab body container */
 .ow-page .ow-tabbody {
 background: transparent;
 border: 0;
 padding: 16px 0;
 }

 /* sub-filter pills */
 .ow-page .ow-filter-pill {
 padding: 6px 14px;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 font: inherit;
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 color: var(--pw-ink-muted);
 background: transparent;
 cursor: pointer;
 }
 .ow-page .ow-filter-pill:hover { background: var(--pw-bg-alt); color: var(--pw-ink); }
 .ow-page .ow-filter-pill.active {
 background: var(--pw-ink);
 color: #fff;
 border-color: var(--pw-ink);
 }

 /* table */
 .ow-page .ow-table-wrap {
 max-height: 60vh;
 overflow-y: auto;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 background: #fff;
 }
 .ow-page .ow-table {
 width: 100%;
 border-collapse: collapse;
 font-size: 11px;
 }
 .ow-page .ow-table thead {
 position: sticky;
 top: 0;
 z-index: 1;
 background: var(--pw-bg-alt);
 }
 .ow-page .ow-table thead th {
 color: var(--pw-ink-muted);
 font-weight: 600;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 font-size: 11px;
 padding: 10px 14px;
 border-bottom: 1px solid var(--pw-border);
 text-align: left;
 }
 .ow-page .ow-table tbody tr {
 border-bottom: 1px solid var(--pw-border);
 background: #fff;
 cursor: inherit;
 }
 .ow-page .ow-table tbody tr:nth-child(even) { background: var(--pw-bg-alt); }
 .ow-page .ow-table tbody tr:hover { background: rgba(201,99,66,0.06); }
 .ow-page .ow-table tbody td {
 padding: 10px 14px;
 font-size: 11px;
 color: var(--pw-ink);
 }

 /* source/status chips */
 .ow-page .ow-source-chip {
 display: inline-block;
 padding: 3px 10px;
 border-radius: 0;
 font-size: 10px;
 font-weight: 600;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 background: var(--pw-bg-alt);
 color: var(--pw-ink-muted);
 }
 .ow-page .ow-source-template { background: #dcfce7; color: #166534; }
 .ow-page .ow-source-learned { background: #fef3c7; color: #92400e; }
 .ow-page .ow-source-promoted { background: rgba(201,99,66,0.12); color: var(--pw-accent); }

 /* drill / CSV / ghost buttons */
 .ow-page .ow-drill-btn {
 padding: 5px 12px;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 font: inherit;
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 color: var(--pw-ink);
 background: transparent;
 cursor: pointer;
 }
 .ow-page .ow-drill-btn:hover:not(:disabled) {
 background: var(--pw-bg-alt);
 border-color: var(--pw-accent);
 color: var(--pw-accent);
 }
 .ow-page .ow-drill-btn:disabled { cursor: not-allowed; }

 /* refresh CTA — canonical .btn-primary rectangle */
 .ow-page .ow-refresh {
 display: inline-flex;
 align-items: center;
 gap: 6px;
 height: 36px;
 padding: 0 16px;
 border-radius: 0;
 border: 1px solid #d97757;
 background: #d97757;
 color: #fff;
 font: inherit;
 font-size: 11px;
 font-weight: 500;
 cursor: pointer;
 }
 .ow-page .ow-refresh:hover { background: #c96644; border-color: #c96644; }

 /* search input */
 .ow-page .ow-search-input {
 width: 280px;
 padding: 8px 12px;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 font: inherit;
 font-size: 13px;
 color: var(--pw-ink);
 background: #fff;
 outline: none;
 }
 .ow-page .ow-search-input:focus {
 border-color: var(--pw-accent);
 box-shadow: 0 0 0 3px rgba(201,99,66,0.08);
 }

 /* days pill (GROWTH 7D/30D/90D) */
 .ow-page .ow-days-pill {
 padding: 6px 14px;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 font: inherit;
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 font-weight: 500;
 color: var(--pw-ink-muted);
 background: transparent;
 cursor: pointer;
 }
 .ow-page .ow-days-pill:hover { background: var(--pw-bg-alt); color: var(--pw-ink); }
 .ow-page .ow-days-pill.active {
 background: var(--pw-ink-fill, #1a1714);
 color: #fff;
 border-color: var(--pw-ink-fill, #1a1714);
 }

 /* growth chart container — white card */
 .ow-page .ow-chart-container {
 background: #fff !important;
 border: 1px solid var(--pw-border) !important;
 border-radius: 0!important;
 padding: 20px !important;
 }

 /* mini counter tiles (GROWTH KPIs: ENTITIES / TRIPLES / etc) */
 .ow-page .ow-mini-counter {
 background: #fff;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 18px 20px;
 display: flex;
 flex-direction: column;
 gap: 4px;
 }
 .ow-page .ow-mini-counter .value {
 font-family: var(--pw-serif, 'EB Garamond', serif);
 font-weight: 400;
 font-size: 30px;
 color: var(--pw-ink);
 line-height: 1.2;
 }
 .ow-page .ow-mini-counter .label {
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.05em;
 color: var(--pw-ink-muted);
 font-weight: 600;
 }

 /* alias chips */
 .ow-page .ow-alias-chip {
 display: inline-block;
 padding: 2px 8px;
 background: rgba(201,99,66,0.10);
 color: var(--pw-accent);
 border-radius: 0;
 font-size: 11px;
 font-weight: 500;
 margin: 1px 2px;
 border: 0;
 }
</style>

<div class="ow-shell">

<!-- ═══ LEFT RAIL ═══ -->
<aside class="ow-rail">
  {#each navGroups as g}
    <div class="ow-rail-group">
      <div class="ow-rail-grouplabel">{g.label}</div>
      {#each g.items as id}
        <button class="ow-rail-btn" class:active={activeTab === id} onclick={() => switchTab(id)}>
          <span class="ow-rail-label">{tabLabel(id)}</span>
          {#if id === 'promote' && (tabCount(id) ?? 0) > 0}
            <span class="ow-rail-badge">{tabCount(id)}</span>
          {:else if tabCount(id) !== null && (tabCount(id) ?? 0) > 0}
            <span class="ow-rail-count">{tabCount(id)}</span>
          {/if}
        </button>
      {/each}
    </div>
  {/each}
</aside>

<main class="ow-main">

  <!-- ═══ HEADER ═══ -->
  <div class="ds-page-head">
    <div>
      <h1 class="ds-page-title">Ontology Workbench</h1>
      <div class="ds-page-sub">Unified entity, link, action and glossary catalog · v2026.05</div>
    </div>
    <button class="btn-primary" onclick={refreshAll}>
      ↻ Refresh{#if lastFetched}&nbsp;·&nbsp;<span style="font-weight: 400; opacity: 0.85;">{lastFetchedLabel}</span>{/if}
    </button>
  </div>

  <!-- ═══ SUMMARY COUNTERS ═══ -->
  <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px;">
    <div style="font-size: 11px; color: var(--pw-muted);">
      {#if !showEmptyStats && visibleStats.length < allStats.length}
        {allStats.length - visibleStats.length} empty hidden
      {/if}
    </div>
    <button class="btn-ghost btn-sm" onclick={() => showEmptyStats = !showEmptyStats}>
      {showEmptyStats ? 'hide empty' : ' show empty'}
    </button>
  </div>
  <div class="ds-grid ds-grid-6">
    {#each visibleStats as card}
      <div class="ds-stat">
        <div class="ds-stat-row">
          <div class="ds-stat-value">{card.val ?? '—'}</div>
          <div class="ds-stat-icon" aria-hidden="true">{card.icon}</div>
        </div>
        <div class="ds-stat-label">{card.label}</div>
      </div>
    {/each}
    {#if visibleStats.length === 0}
      <div style="grid-column: 1 / -1; padding: 18px; text-align: center; font-size: 11px; color: var(--pw-muted); border: 1px dashed var(--pw-border); border-radius: 0;">
        All stats are zero. Apply a template to a project to populate.
      </div>
    {/if}
  </div>

  <!-- secondary stats -->
  <div class="ow-meta">
    <span class="ow-meta-item">Templates <strong>{summary.templates ?? '—'}</strong></span>
    <span class="ow-meta-item">Active agents <strong>{summary.active_agents ?? '—'}</strong></span>
    <span class="ow-meta-item">Projects <strong>{summary.projects_total ?? '—'}</strong></span>
    <span class="ow-meta-item">Promotions pending <strong class={summary.promotions_pending > 0 ? 'ow-meta-pending' : ''}>{summary.promotions_pending ?? '—'}</strong></span>
  </div>

  <!-- ═══ TAB BODY ═══ -->
  <div class="ow-tabbody">

    <!-- ═════ TYPES ═════ -->
    {#if activeTab === 'types'}
      <div class="ds-toolbar">
        <div class="ds-toolbar-group">
          <input
            class="ds-input"
            type="text"
            bind:value={typesQuery}
            oninput={onTypesSearchInput}
            placeholder="Search types…"
            style="width: 240px;"
          />
          {#each ['all', 'template', 'learned', 'promoted'] as src}
            <button
              class="pill-segment"
              class:active={typesSource === src}
              onclick={() => setTypesSource(src)}
            >{src}</button>
          {/each}
        </div>
        <div class="ds-toolbar-group">
          <span style="font-size: var(--fs-sm); color: var(--pw-muted);">{typesData.length} types</span>
          <button
            class="btn-ghost"
            onclick={exportTypesCsv}
            disabled={typesData.length === 0}
          >↓ CSV</button>
        </div>
      </div>

      {#if tabError['types']}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px; margin-bottom: 10px;">API error: {tabError['types']}</div>
      {/if}
      {#if tabLoading['types']}
        <div style="padding: 12px;">
          <div class="skel-row" style="width: 80%;"></div>
          <div class="skel-row" style="width: 65%;"></div>
          <div class="skel-row" style="width: 75%;"></div>
        </div>
      {:else if typesData.length === 0}
        <div style="border: 2px dashed var(--pw-ink); background: #fff; padding: 32px 18px; text-align: center; font-family: monospace;">
          <div style="font-size: 30px; margin-bottom: 8px;"><Icon name="inbox" size={14} /></div>
          <div style="font-size: 11px; font-weight: 700; color: var(--pw-ink); margin-bottom: 4px; letter-spacing: 0.06em;">No entities yet.</div>
          <div style="font-size: 10px; color: #555; margin-bottom: 12px;">Apply a template to a project to populate the ontology.</div>
          <a href="/ui/projects" style="display: inline-block; font-family: monospace; font-size: 10px; font-weight: 700; padding: 6px 14px; background: var(--pw-ink); color: var(--pw-accent); border: 2px solid var(--pw-ink); text-transform: uppercase; letter-spacing: 0.1em; text-decoration: none;">+ BROWSE TEMPLATES</a>
        </div>
      {:else}
        <div class="ds-table-wrap">
          <table class="ds-table">
            <thead>
              <tr>
                {#each [
                  { k: 'name', l: 'Name' },
                  { k: 'aliases', l: 'Aliases' },
                  { k: 'used_in_templates', l: 'Templates' },
                  { k: 'active_agents', l: 'Agents' },
                  { k: 'confidence', l: 'Conf' },
                  { k: 'source', l: 'Source' },
                ] as col}
                  <th
                    onclick={() => toggleSort(col.k)}
                    style="cursor: pointer; user-select: none;"
                  >{col.l}{typesSort.key === col.k ? (typesSort.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                {/each}
                <th>Act</th>
              </tr>
            </thead>
            <tbody>
              {#each filteredTypes as t}
                <tr onclick={() => openDrill(t.name)} style="cursor: pointer;">
                  <td style="font-weight: 600;">{t.name}</td>
                  <td>
                    {#if (t.aliases || []).length > 0}
                      <div style="display: flex; flex-wrap: wrap; gap: 3px;">
                        {#each (t.aliases || []).slice(0, 3) as a}
                          <span class="chip chip-coral">{a}</span>
                        {/each}
                        {#if (t.aliases || []).length > 3}<span style="font-size: var(--fs-xs); color: var(--pw-muted);">+{(t.aliases || []).length - 3}</span>{/if}
                      </div>
                    {:else}<span style="color: var(--pw-muted);">—</span>{/if}
                  </td>
                  <td style="font-size: var(--fs-sm);">{(t.used_in_templates || []).join(', ') || '—'}</td>
                  <td style="text-align: center;">{t.active_agents ?? 0}</td>
                  <td>
                    <div style="display: flex; align-items: center; gap: 6px;">
                      <div style="flex: 1; max-width: 60px; height: 6px; background: var(--pw-bg-alt); border-radius: var(--r-pill); overflow: hidden;">
                        <div style="height: 100%; width: {Math.round((t.confidence ?? 0) * 100)}%; background: {(t.confidence ?? 0) >= 0.85 ? 'var(--ds-success)' : (t.confidence ?? 0) >= 0.7 ? 'var(--ds-warn)' : 'var(--ds-danger)'};"></div>
                      </div>
                      <span style="font-size: var(--fs-xs); color: var(--pw-muted);">{((t.confidence ?? 0) * 100).toFixed(0)}%</span>
                    </div>
                  </td>
                  <td>
                    <span class="chip {(t.source || '').toLowerCase() === 'template' ? 'chip-green' : (t.source || '').toLowerCase() === 'learned' ? 'chip-amber' : (t.source || '').toLowerCase() === 'promoted' ? 'chip-coral' : 'chip-gray'}">{t.source || '—'}</span>
                  </td>
                  <td>
                    <button
                      class="btn-ghost btn-sm"
                      onclick={(e) => { e.stopPropagation(); openDrill(t.name); }}
                    >Drill</button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}

    <!-- ═════ LINKS ═════ -->
    {:else if activeTab === 'links'}
      <div class="ds-toolbar">
        <div class="ds-toolbar-group">
          <input
            class="ds-input"
            type="text"
            bind:value={linksQuery}
            placeholder="Filter entities…"
            style="width: 240px;"
          />
        </div>
        <div class="ds-toolbar-group">
          <span style="font-size: var(--fs-sm); color: var(--pw-muted);">{linksData.length} links</span>
          <button class="btn-ghost" onclick={exportLinksCsv} disabled={linksData.length === 0}>↓ CSV</button>
        </div>
      </div>

      {#if tabError['links']}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px; margin-bottom: 10px;">API error: {tabError['links']}</div>
      {/if}
      {#if tabLoading['links']}
        <div style="padding: 12px;">
          <div class="skel-row" style="width: 78%;"></div>
          <div class="skel-row" style="width: 60%;"></div>
          <div class="skel-row" style="width: 70%;"></div>
        </div>
      {:else if linksData.length === 0}
        <div style="border: 2px dashed var(--pw-ink); background: #fff; padding: 32px 18px; text-align: center; font-family: monospace;">
          <div style="font-size: 30px; margin-bottom: 8px;"><Icon name="inbox" size={14} /></div>
          <div style="font-size: 11px; font-weight: 700; color: var(--pw-ink); margin-bottom: 4px; letter-spacing: 0.06em;">No links yet.</div>
          <div style="font-size: 10px; color: #555; margin-bottom: 12px;">Apply a template to a project to populate the ontology.</div>
          <a href="/ui/projects" style="display: inline-block; font-family: monospace; font-size: 10px; font-weight: 700; padding: 6px 14px; background: var(--pw-ink); color: var(--pw-accent); border: 2px solid var(--pw-ink); text-transform: uppercase; letter-spacing: 0.1em; text-decoration: none;">+ BROWSE TEMPLATES</a>
        </div>
      {:else}
        <div class="ds-table-wrap">
          <table class="ds-table">
            <thead>
              <tr>
                <th>From</th>
                <th>Relation</th>
                <th>To</th>
                <th>Agents</th>
                <th>Source</th>
                <th>Conf</th>
              </tr>
            </thead>
            <tbody>
              {#each filteredLinks as l}
                <tr>
                  <td style="font-weight: 600;">{l.from_entity}</td>
                  <td><span class="chip chip-coral">{l.relation}</span></td>
                  <td style="font-weight: 600;">{l.to_entity}</td>
                  <td style="text-align: center;">{l.agent_count ?? 0}</td>
                  <td>
                    <span class="chip {(l.source || '').toLowerCase() === 'template' ? 'chip-green' : (l.source || '').toLowerCase() === 'learned' ? 'chip-amber' : (l.source || '').toLowerCase() === 'promoted' ? 'chip-coral' : 'chip-gray'}">{l.source || '—'}</span>
                  </td>
                  <td>{((l.confidence ?? 0) * 100).toFixed(0)}%</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}

    <!-- ═════ ACTIONS ═════ -->
    {:else if activeTab === 'actions'}
      <div class="ds-toolbar">
        <div class="ds-toolbar-group"></div>
        <div class="ds-toolbar-group">
          <span style="font-size: var(--fs-sm); color: var(--pw-muted);">{actionsData.length} actions</span>
          <button class="btn-ghost" onclick={exportActionsCsv} disabled={actionsData.length === 0}>↓ CSV</button>
        </div>
      </div>

      {#if tabError['actions']}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px; margin-bottom: 10px;">API error: {tabError['actions']}</div>
      {/if}
      {#if tabLoading['actions']}
        <div style="padding: 12px;">
          <div class="skel-row" style="width: 75%;"></div>
          <div class="skel-row" style="width: 65%;"></div>
          <div class="skel-row" style="width: 80%;"></div>
        </div>
      {:else if actionsData.length === 0}
        <div style="border: 2px dashed var(--pw-ink); background: #fff; padding: 32px 18px; text-align: center; font-family: monospace;">
          <div style="font-size: 30px; margin-bottom: 8px;"><Icon name="inbox" size={14} /></div>
          <div style="font-size: 11px; font-weight: 700; color: var(--pw-ink); margin-bottom: 4px; letter-spacing: 0.06em;">No actions yet.</div>
          <div style="font-size: 10px; color: #555; margin-bottom: 12px;">Apply a template to a project to populate the ontology.</div>
          <a href="/ui/projects" style="display: inline-block; font-family: monospace; font-size: 10px; font-weight: 700; padding: 6px 14px; background: var(--pw-ink); color: var(--pw-accent); border: 2px solid var(--pw-ink); text-transform: uppercase; letter-spacing: 0.1em; text-decoration: none;">+ BROWSE TEMPLATES</a>
        </div>
      {:else}
        <div class="ds-table-wrap">
          <table class="ds-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Schedule</th>
                <th>Action</th>
                <th>Templates</th>
                <th style="text-align: center;">Status</th>
                <th>Last run</th>
              </tr>
            </thead>
            <tbody>
              {#each actionsData as a}
                <tr>
                  <td style="font-weight: 600;">{a.name}</td>
                  <td style="font-size: var(--fs-sm);"><code style="background: var(--pw-bg-alt); padding: 1px 4px; border-radius: var(--r-xs);">{a.schedule || '—'}</code></td>
                  <td><span class="chip chip-purple">{a.action || '—'}</span></td>
                  <td style="font-size: var(--fs-sm);">{(a.templates_using || []).join(', ') || '—'}</td>
                  <td>
                    <div style="display: flex; gap: 4px; justify-content: center;">
                      <span class="chip chip-green" title="Active">{a.active_count ?? 0}</span>
                      <span class="chip chip-amber" title="Paused">{a.paused_count ?? 0}</span>
                      <span class="chip chip-red" title="Failed">{a.failed_count ?? 0}</span>
                    </div>
                  </td>
                  <td style="font-size: var(--fs-sm); color: var(--pw-muted);">{a.last_run_avg || '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}

    <!-- ═════ GLOSSARY ═════ -->
    {:else if activeTab === 'glossary'}
      <div class="ds-toolbar">
        <div class="ds-toolbar-group">
          <span style="font-size: var(--fs-sm); color: var(--pw-muted); margin-right: 4px;">Scope:</span>
          {#each ['all', 'global', 'project'] as s}
            <button
              class="pill-segment"
              class:active={glossaryScope === s}
              onclick={() => { glossaryScope = s as any; loadGlossary(); }}
            >{s}</button>
          {/each}
        </div>
        <div class="ds-toolbar-group">
          <button class="btn-ghost" onclick={exportGlossaryCsv} disabled={glossaryData.length === 0}>↓ CSV</button>
        </div>
      </div>
      <div class="ds-toolbar">
        <div class="ds-toolbar-group">
          <span style="font-size: var(--fs-sm); color: var(--pw-muted); margin-right: 4px;">Category:</span>
          {#each ['all', 'glossary', 'formula', 'alias', 'pattern'] as c}
            <button
              class="pill-segment"
              class:active={glossaryCategory === c}
              onclick={() => { glossaryCategory = c as any; loadGlossary(); }}
            >{c}</button>
          {/each}
        </div>
        <div class="ds-toolbar-group">
          <input
            class="ds-input"
            type="text"
            bind:value={glossaryQuery}
            oninput={onGlossarySearchInput}
            placeholder="Search…"
            style="width: 240px;"
          />
        </div>
      </div>

      {#if tabError['glossary']}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px; margin-bottom: 10px;">API error: {tabError['glossary']}</div>
      {/if}
      {#if tabLoading['glossary']}
        <div style="padding: 12px;">
          <div class="skel-row" style="width: 82%;"></div>
          <div class="skel-row" style="width: 70%;"></div>
          <div class="skel-row" style="width: 76%;"></div>
        </div>
      {:else if glossaryData.length === 0}
        <div style="border: 2px dashed var(--pw-ink); background: #fff; padding: 32px 18px; text-align: center; font-family: monospace;">
          <div style="font-size: 30px; margin-bottom: 8px;"><Icon name="inbox" size={14} /></div>
          <div style="font-size: 11px; font-weight: 700; color: var(--pw-ink); margin-bottom: 4px; letter-spacing: 0.06em;">No glossary entries yet.</div>
          <div style="font-size: 10px; color: #555; margin-bottom: 12px;">Apply a template to a project to populate the ontology.</div>
          <a href="/ui/projects" style="display: inline-block; font-family: monospace; font-size: 10px; font-weight: 700; padding: 6px 14px; background: var(--pw-ink); color: var(--pw-accent); border: 2px solid var(--pw-ink); text-transform: uppercase; letter-spacing: 0.1em; text-decoration: none;">+ BROWSE TEMPLATES</a>
        </div>
      {:else}
        <div class="ds-table-wrap">
          <table class="ds-table">
            <thead>
              <tr>
                <th style="width: 25%;">Term</th>
                <th style="width: 15%;">Category</th>
                <th>Definition</th>
              </tr>
            </thead>
            <tbody>
              {#each glossaryData as g}
                <tr>
                  <td>
                    <div style="font-weight: 600;">{g.name}</div>
                    {#if g.scope === 'global'}
                      <span class="chip chip-green" style="margin-top: 2px;">Global</span>
                    {:else if g.project_slug}
                      <span class="chip chip-amber" style="margin-top: 2px;">{g.project_slug}</span>
                    {/if}
                  </td>
                  <td><span class="chip chip-gray">{g.category || '—'}</span></td>
                  <td style="color: var(--pw-ink); line-height: var(--lh-normal);">{g.definition || '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}

    <!-- ═════ GROWTH ═════ -->
    {:else if activeTab === 'growth'}
      <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 12px;">
        <span style="font-size: 11px; color: #555; font-weight: 700; letter-spacing: 0.1em;">DAYS:</span>
        {#each [7, 30, 90] as d}
          <button
            class="ow-days-pill {growthDays === d ? 'active' : ''}"
            onclick={() => { growthDays = d; loadGrowth(); }}
          >{d}D</button>
        {/each}
      </div>

      {#if tabError['growth']}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px; margin-bottom: 10px;">API error: {tabError['growth']}</div>
      {/if}
      {#if tabLoading['growth'] && !growthData}
        <div style="padding: 12px;">
          <div class="skel-row" style="width: 90%; height: 22px;"></div>
          <div class="skel-row" style="width: 100%; height: 220px;"></div>
        </div>
      {:else if growthData}
        <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 14px;">
          {#each [
            { l: 'ENTITIES', k: 'entities' },
            { l: 'TRIPLES', k: 'triples' },
            { l: 'MEMORIES', k: 'memories' },
            { l: 'PROMOTIONS', k: 'promotions' },
            { l: 'WORKFLOWS', k: 'workflows' },
          ] as kpi}
            <div class="ow-mini-counter">
              <div class="value">{(growthData.totals || {})[kpi.k] ?? '—'}</div>
              <div class="label">{kpi.l}</div>
            </div>
          {/each}
        </div>
        <div class="ow-chart-container">
          <div bind:this={growthContainer} style="width: 100%; height: 380px;"></div>
        </div>
      {:else}
        <div style="font-size: 11px; color: #888; font-style: italic; padding: 12px;">— no data —</div>
      {/if}

    <!-- ═════ LINEAGE ═════ -->
    {:else if activeTab === 'lineage'}
      <div class="ds-toolbar">
        <div class="ds-toolbar-group">
          <span style="font-size: var(--fs-sm); color: var(--pw-muted);">Entity:</span>
          <select
            bind:value={lineageEntity}
            onchange={() => loadLineage()}
            class="ds-input"
            style="width: 180px;"
          >
            <option value="">(all)</option>
            {#each typesData.slice(0, 50) as t}
              <option value={t.name}>{t.name}</option>
            {/each}
          </select>
          <span style="font-size: var(--fs-sm); color: var(--pw-muted); margin-left: 8px;">Max nodes:</span>
          {#each [50, 100, 200, 500] as n}
            <button
              class="pill-segment"
              class:active={lineageMaxNodes === n}
              onclick={() => { lineageMaxNodes = n; loadLineage(); }}
            >{n}</button>
          {/each}
        </div>
        <div class="ds-toolbar-group">
          <!-- Search input -->
          <div style="position: relative;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="position: absolute; left: 8px; top: 50%; transform: translateY(-50%); color: var(--pw-muted); pointer-events: none;">
              <circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>
            </svg>
            <input
              class="ds-input"
              type="text"
              bind:value={lineageSearch}
              oninput={onLineageSearchInput}
              placeholder="Search node…"
              style="width: 200px; padding-left: 28px;"
            />
          </div>
        </div>
      </div>

      {#if tabError['lineage']}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px; margin-bottom: 10px;">API error: {tabError['lineage']}</div>
      {/if}
      {#if tabLoading['lineage'] && !lineageData}
        <div style="padding: 12px;">
          <div class="skel-row" style="width: 100%; height: 320px;"></div>
        </div>
      {:else if lineageData && (lineageData.nodes || []).length > 0}
        <div style="position: relative;">
          <div bind:this={lineageContainer} style="width: 100%; height: 600px; border: 1px solid var(--pw-border); border-radius: 0; background: #1a1714; overflow: hidden;"></div>
          <!-- Zoom controls (top-right) -->
          <div style="position: absolute; top: 12px; right: 12px; display: flex; flex-direction: column; gap: 4px; background: rgba(255,255,255,0.92); border: 1px solid var(--pw-border); border-radius: 0; padding: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.15);">
            <button onclick={() => lineageZoom(1.25)} title="Zoom in" style="background: none; border: none; cursor: pointer; padding: 6px 8px; font-size: 11px; color: var(--pw-ink);">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            </button>
            <button onclick={() => lineageZoom(0.8)} title="Zoom out" style="background: none; border: none; cursor: pointer; padding: 6px 8px; color: var(--pw-ink);">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="5" y1="12" x2="19" y2="12"/></svg>
            </button>
            <button onclick={lineageFit} title="Fit to screen" style="background: none; border: none; cursor: pointer; padding: 6px 8px; color: var(--pw-ink);">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4"/></svg>
            </button>
            <button onclick={lineageReset} title="Reset" style="background: none; border: none; cursor: pointer; padding: 6px 8px; color: var(--pw-ink);">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>
            </button>
          </div>
        </div>
        <!-- Clickable legend chips -->
        <div style="display: flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; align-items: center;">
          <span style="font-size: var(--fs-sm); color: var(--pw-muted);">Filter sources:</span>
          {#each ['template', 'learned', 'promoted', 'web', 'other'] as src}
            <button
              onclick={() => toggleLineageSource(src)}
              style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border: 1px solid var(--pw-border); border-radius: 0; background: {lineageHiddenSources[src] ? 'var(--pw-bg-alt)' : '#fff'}; cursor: pointer; font-size: 11px; opacity: {lineageHiddenSources[src] ? 0.5 : 1}; color: var(--pw-ink);"
              title={lineageHiddenSources[src] ? `Show ${src}` : `Hide ${src}`}
            >
              <span style="width: 10px; height: 10px; background: {sourceColor(src)}; border-radius: 50%; display: inline-block;"></span>
              <span style="text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600;">{src}</span>
              {#if lineageHiddenSources[src]}<span style="font-size: 11px; color: var(--pw-muted);">(hidden)</span>{/if}
            </button>
          {/each}
        </div>
      {:else}
        <div style="font-size: 11px; color: #888; font-style: italic; padding: 12px;">— no data —</div>
      {/if}

    <!-- ═════ PROMOTE ═════ -->
    {:else if activeTab === 'promote'}
      <div class="ds-toolbar">
        <div class="ds-toolbar-group">
          <span style="font-size: var(--fs-md); font-weight: 600; color: var(--pw-ink);">Pending promotions</span>
          <span style="font-size: var(--fs-sm); color: var(--pw-muted);">{filteredPromote.length} of {promoteData.length}</span>
        </div>
        <div class="ds-toolbar-group">
          <span style="font-size: var(--fs-sm); color: var(--pw-muted);">Conf ≥</span>
          <input type="range" min="0.5" max="1" step="0.05" bind:value={promoteMinConf} style="width: 100px;" />
          <span style="font-size: var(--fs-sm); font-weight: 600; min-width: 36px;">{(promoteMinConf * 100).toFixed(0)}%</span>
          <select class="ds-input" bind:value={promoteCategoryFilter} style="width: 150px;">
            <option value="all">All categories</option>
            {#each promoteCategories as c}<option value={c}>{c}</option>{/each}
          </select>
        </div>
      </div>

      {#if tabError['promote']}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px; margin-bottom: 10px;">API error: {tabError['promote']}</div>
      {/if}
      {#if tabLoading['promote']}
        <div style="padding: 12px;">
          <div class="skel-row" style="width: 80%;"></div>
          <div class="skel-row" style="width: 70%;"></div>
          <div class="skel-row" style="width: 75%;"></div>
        </div>
      {:else if filteredPromote.length === 0}
        <div style="font-size: 11px; color: #888; font-style: italic; padding: 12px;">— no matching promotions —</div>
      {:else}
        <div class="ds-table-wrap">
          <table class="ds-table">
            <thead>
              <tr>
                <th style="width: 36px;">
                  <input
                    type="checkbox"
                    onchange={toggleSelectAllPromote}
                    checked={filteredPromote.length > 0 && filteredPromote.every((p: any) => promoteSelected[p.id])}
                    title="Select all visible"
                  />
                </th>
                <th style="width: 32px;"></th>
                <th onclick={() => togglePromoteSort('name')} style="cursor: pointer;">Name{promoteSort.key === 'name' ? (promoteSort.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                <th>Category</th>
                <th onclick={() => togglePromoteSort('confidence')} style="cursor: pointer;">Confidence{promoteSort.key === 'confidence' ? (promoteSort.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                <th>Project</th>
                <th onclick={() => togglePromoteSort('created_at')} style="cursor: pointer;">Created{promoteSort.key === 'created_at' ? (promoteSort.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                <th style="width: 180px;">Actions</th>
              </tr>
            </thead>
            <tbody>
              {#each filteredPromote as p}
                {@const conf = parsePromoteConfidence(p)}
                {@const diff = parsePromoteDiff(p)}
                <tr>
                  <td>
                    <input
                      type="checkbox"
                      checked={!!promoteSelected[p.id]}
                      onchange={() => promoteSelected = { ...promoteSelected, [p.id]: !promoteSelected[p.id] }}
                    />
                  </td>
                  <td>
                    <button
                      class="btn-ghost btn-sm"
                      onclick={() => promoteExpanded = { ...promoteExpanded, [p.id]: !promoteExpanded[p.id] }}
                      title={promoteExpanded[p.id] ? 'Collapse' : 'Expand diff'}
                      style="padding: 2px 6px;"
                    >{promoteExpanded[p.id] ? '' : '⤢'}</button>
                  </td>
                  <td style="font-weight: 600;">{cleanPromoteName(p)}</td>
                  <td><span class="chip chip-gray">{p.category || '—'}</span></td>
                  <td>
                    <div style="display: flex; align-items: center; gap: 6px;">
                      <div style="flex: 1; max-width: 60px; height: 6px; background: var(--pw-bg-alt); border-radius: var(--r-pill); overflow: hidden;">
                        <div style="height: 100%; width: {Math.round(conf * 100)}%; background: {conf >= 0.85 ? 'var(--ds-success)' : conf >= 0.7 ? 'var(--ds-warn)' : 'var(--ds-danger)'};"></div>
                      </div>
                      <span style="font-size: var(--fs-xs); color: var(--pw-muted); min-width: 30px;">{(conf * 100).toFixed(0)}%</span>
                    </div>
                  </td>
                  <td><span class="chip chip-amber">{p.project_slug || '—'}</span></td>
                  <td style="font-size: var(--fs-sm); color: var(--pw-muted);">{fmtDate(p.created_at)}</td>
                  <td>
                    <div style="display: flex; gap: 6px;">
                      <button class="btn-primary btn-sm" onclick={() => approvePromotion(p.id)} disabled={promoteBusy[p.id]}>
                        {promoteBusy[p.id] ? '…' : ''}
                      </button>
                      <button class="btn-secondary btn-sm" onclick={() => rejectPromotion(p.id)} disabled={promoteBusy[p.id]}>
                        {promoteBusy[p.id] ? '…' : ''}
                      </button>
                    </div>
                  </td>
                </tr>
                {#if promoteExpanded[p.id]}
                  <tr style="background: var(--pw-bg-alt);">
                    <td colspan="8" style="padding: 12px 18px;">
                      <div style="font-size: 11px; color: var(--pw-muted); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; margin-bottom: 8px;">Definition / Diff</div>
                      {#if diff}
                        <div style="display: grid; grid-template-columns: 1fr 32px 1fr; gap: 8px; align-items: center;">
                          <div style="background: #fff; border: 1px solid var(--pw-border); border-left: 3px solid #dc2626; padding: 8px 12px; border-radius: 0; font-family: var(--pw-mono, monospace); font-size: 11px;">
                            <div style="font-size: 11px; color: #dc2626; font-weight: 700; letter-spacing: 0.08em; margin-bottom: 4px;">LEFT</div>
                            {diff.left}
                          </div>
                          <div style="text-align: center; color: var(--pw-accent); font-weight: 700; font-size: 12px;">→</div>
                          <div style="background: #fff; border: 1px solid var(--pw-border); border-left: 3px solid var(--ds-success); padding: 8px 12px; border-radius: 0; font-family: var(--pw-mono, monospace); font-size: 11px;">
                            <div style="font-size: 11px; color: var(--ds-success); font-weight: 700; letter-spacing: 0.08em; margin-bottom: 4px;">RIGHT (canonical)</div>
                            {diff.right}
                          </div>
                        </div>
                      {/if}
                      <div style="margin-top: 8px; background: #fff; border: 1px solid var(--pw-border); padding: 8px 12px; border-radius: 0; font-size: 11px; color: var(--pw-ink);">
                        {p.definition || '—'}
                      </div>
                    </td>
                  </tr>
                {/if}
              {/each}
            </tbody>
          </table>
        </div>
      {/if}

      <!-- Floating bulk-action bar -->
      {#if promoteSelectedIds.length > 0}
        <div style="position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); background: #2c2a26; color: #fff; padding: 10px 18px; border-radius: 999px; box-shadow: 0 8px 28px rgba(0,0,0,0.28); display: flex; gap: 12px; align-items: center; z-index: 200; font-size: 11px;">
          <span style="font-weight: 600;">{promoteSelectedIds.length} selected</span>
          <button
            onclick={bulkApprovePromote}
            disabled={promoteBulkBusy}
            style="background: #16a34a; color: #fff; border: none; padding: 7px 14px; border-radius: 0; cursor: pointer; font-weight: 600; font-size: 11px; display: inline-flex; gap: 6px; align-items: center;"
          ><Icon name="check" size={14} /> Approve {promoteSelectedIds.length}</button>
          <button
            onclick={bulkRejectPromote}
            disabled={promoteBulkBusy}
            style="background: #dc2626; color: #fff; border: none; padding: 7px 14px; border-radius: 0; cursor: pointer; font-weight: 600; font-size: 11px;"
          ><Icon name="x" size={14} /> Reject {promoteSelectedIds.length}</button>
          <button
            onclick={clearPromoteSelection}
            disabled={promoteBulkBusy}
            style="background: transparent; color: #fff; border: 1px solid rgba(255,255,255,0.3); padding: 7px 14px; border-radius: 0; cursor: pointer; font-size: 11px;"
          > Clear</button>
        </div>
      {/if}

    <!-- ═════ API KEYS ═════ -->
    {:else if activeTab === 'apikeys'}
      <div class="ds-toolbar">
        <div class="ds-toolbar-group">
          <span style="font-size: var(--fs-md); font-weight: 600; color: var(--pw-ink);">Public API keys</span>
          <span style="font-size: var(--fs-sm); color: var(--pw-muted);">{apikeysData.length} total</span>
        </div>
        <div class="ds-toolbar-group">
          <button class="btn-primary" onclick={() => { apikeysShowCreate = true; apikeyJustCreatedSecret = null; }}>+ New key</button>
        </div>
      </div>

      {#if apikeyJustCreatedSecret}
        <div style="border: 2px solid var(--pw-success); background: #e8f5e9; padding: 12px; margin-bottom: 14px;">
          <div style="font-size: 11px; font-weight: 900; color: var(--pw-success); margin-bottom: 6px; letter-spacing: 0.06em;"><Icon name="check" size={14} /> KEY CREATED — SAVE THIS SECRET, IT WON'T SHOW AGAIN</div>
          <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
            <code style="background: var(--pw-ink); color: var(--pw-accent); padding: 6px 10px; font-size: 11px; flex: 1; min-width: 240px; word-break: break-all;">{apikeyJustCreatedSecret}</code>
            <button
              onclick={() => copyText(apikeyJustCreatedSecret!)}
              style="font-family: monospace; font-size: 10px; font-weight: 700; padding: 5px 12px; cursor: pointer; background: #fff; color: var(--pw-success); border: 2px solid var(--pw-success); text-transform: uppercase;"
            >{copyFlash ? 'COPIED' : 'COPY'}</button>
            <button
              onclick={() => { apikeyJustCreatedSecret = null; apikeyJustCreatedRow = null; }}
              style="font-family: monospace; font-size: 10px; font-weight: 700; padding: 5px 12px; cursor: pointer; background: transparent; color: #555; border: 2px solid #555; text-transform: uppercase;"
            >DISMISS</button>
          </div>
          {#if apikeyJustCreatedRow}
            <div style="margin-top: 8px; font-size: 10px; color: #555;">
              public_key: <code style="background:#fff; padding: 1px 4px;">{apikeyJustCreatedRow.public_key}</code>
              · scope: <code style="background:#fff; padding: 1px 4px;">{Object.keys(apikeyJustCreatedRow.scope || {}).filter(k => apikeyJustCreatedRow.scope[k]).join(',')}</code>
            </div>
          {/if}
        </div>
      {/if}

      {#if apikeyRotatedSecret}
        <div style="border: 2px solid #d97706; background: #fff8e1; padding: 12px; margin-bottom: 14px;">
          <div style="font-size: 11px; font-weight: 900; color: #d97706; margin-bottom: 6px;"><Icon name="check" size={14} /> KEY #{apikeyRotatedSecret.id} ROTATED — SAVE NEW SECRET</div>
          <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
            <code style="background: var(--pw-ink); color: var(--pw-accent); padding: 6px 10px; font-size: 11px; flex: 1; min-width: 240px; word-break: break-all;">{apikeyRotatedSecret.secret}</code>
            <button onclick={() => copyText(apikeyRotatedSecret!.secret)} style="font-family: monospace; font-size: 10px; font-weight: 700; padding: 5px 12px; cursor: pointer; background: #fff; color: #d97706; border: 2px solid #d97706;"><Icon name="clipboard" size={14} /> COPY</button>
            <button onclick={() => apikeyRotatedSecret = null} style="font-family: monospace; font-size: 10px; font-weight: 700; padding: 5px 12px; cursor: pointer; background: transparent; color: #555; border: 2px solid #555;">DISMISS</button>
          </div>
        </div>
      {/if}

      {#if tabError['apikeys']}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px; margin-bottom: 10px;">API error: {tabError['apikeys']}</div>
      {/if}

      {#if tabLoading['apikeys']}
        <div style="padding: 12px;"><div class="skel-row" style="width: 80%;"></div><div class="skel-row" style="width: 70%;"></div></div>
      {:else if apikeysData.length === 0}
        <div style="font-size: 11px; color: #888; font-style: italic; padding: 12px;">— no keys issued yet —</div>
      {:else}
        <div class="ds-table-wrap">
          <table class="ds-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Public key</th>
                <th>Project</th>
                <th>Rate/min</th>
                <th>Last used</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {#each apikeysData as k}
                <tr>
                  <td style="font-weight: 600;">{k.name}</td>
                  <td><code style="background: var(--pw-bg-alt); padding: 1px 5px; border-radius: var(--r-xs); font-size: var(--fs-sm);">{k.public_key}</code></td>
                  <td>{k.project_slug ? `${k.project_slug}` : 'Global'}</td>
                  <td>{k.rate_limit_per_min || 60}</td>
                  <td style="font-size: var(--fs-sm); color: var(--pw-muted);">{fmtDate(k.last_used_at)}</td>
                  <td>
                    {#if k.status === 'active'}
                      <span class="chip chip-green">Active</span>
                    {:else}
                      <span class="chip chip-red">Revoked</span>
                    {/if}
                  </td>
                  <td>
                    <div style="display: flex; gap: 4px;">
                      <button class="btn-ghost btn-sm" onclick={() => openUsage(k)} disabled={apikeysBusy[k.id]}>Usage</button>
                      {#if k.status === 'active'}
                        <button class="btn-secondary btn-sm" onclick={() => rotateApiKey(k.id)} disabled={apikeysBusy[k.id]}>Rotate</button>
                        <button class="btn-danger btn-sm" onclick={() => revokeApiKey(k.id)} disabled={apikeysBusy[k.id]}>Revoke</button>
                      {/if}
                    </div>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}
  </div>
</main>
</div>

<!-- ═════ NEW KEY MODAL ═════ -->
{#if apikeysShowCreate}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div onclick={() => apikeysShowCreate = false} style="position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 600;"></div>
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div onclick={(e) => e.stopPropagation()} style="position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%); width: 520px; max-width: 95vw; max-height: 90vh; overflow-y: auto; background: var(--pw-surface-warm); border: 2px solid var(--pw-ink); z-index: 601; font-family: monospace; box-shadow: 6px 6px 0 var(--pw-ink);">
    <div style="background: var(--pw-ink); color: var(--pw-accent); padding: 10px 14px; display: flex; align-items: center; justify-content: space-between;">
      <span style="font-size: 11px; font-weight: 900; letter-spacing: 0.1em;">+ NEW API KEY</span>
      <button onclick={() => apikeysShowCreate = false} style="background: none; border: 1.5px solid var(--pw-accent); color: var(--pw-accent); padding: 2px 8px; cursor: pointer; font-family: monospace; font-size: 11px; font-weight: 700;"><Icon name="x" size={14} /></button>
    </div>
    <div style="padding: 14px; display: flex; flex-direction: column; gap: 10px;">
      <label style="font-size: 10px; font-weight: 700; color: #555; letter-spacing: 0.08em; text-transform: uppercase;">NAME
        <input type="text" bind:value={apikeyNew.name} placeholder="e.g. partner-acme" style="display:block; width: 100%; margin-top: 4px; padding: 6px 8px; font-family: monospace; font-size: 12px; border: 2px solid var(--pw-ink);" />
      </label>
      <label style="font-size: 10px; font-weight: 700; color: #555; letter-spacing: 0.08em; text-transform: uppercase;">PROJECT
        <select bind:value={apikeyNew.project_slug} style="display:block; width: 100%; margin-top: 4px; padding: 6px 8px; font-family: monospace; font-size: 11px; border: 2px solid var(--pw-ink); background: #fff;">
          <option value=""><Icon name="globe" size={14} /> GLOBAL (all projects readable)</option>
          {#each projectsList as p}
            <option value={p.slug}><Icon name="bar-chart" size={14} /> {p.name} ({p.slug})</option>
          {/each}
        </select>
      </label>
      <div>
        <div style="font-size: 10px; font-weight: 700; color: #555; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 4px;">SCOPE</div>
        <div style="display: flex; gap: 14px; flex-wrap: wrap; padding: 6px 8px; border: 2px solid var(--pw-ink); background: #fff;">
          {#each ['types','glossary','links','lineage'] as k}
            <label style="font-size: 11px; font-weight: 700; cursor: pointer;">
              <input type="checkbox" bind:checked={apikeyNew.scope[k]} /> {k}
            </label>
          {/each}
        </div>
      </div>
      <label style="font-size: 10px; font-weight: 700; color: #555; letter-spacing: 0.08em; text-transform: uppercase;">RATE LIMIT (per minute)
        <input type="number" min="1" max="6000" bind:value={apikeyNew.rate_limit_per_min} style="display:block; width: 120px; margin-top: 4px; padding: 6px 8px; font-family: monospace; font-size: 12px; border: 2px solid var(--pw-ink);" />
      </label>
      <label style="font-size: 10px; font-weight: 700; color: #555; letter-spacing: 0.08em; text-transform: uppercase;">ALLOWED ORIGINS (one per line, blank for any)
        <textarea bind:value={apikeyNew.allowed_origins} rows="3" placeholder="https://example.com" style="display:block; width: 100%; margin-top: 4px; padding: 6px 8px; font-family: monospace; font-size: 11px; border: 2px solid var(--pw-ink);"></textarea>
      </label>
      <div style="display: flex; gap: 8px; margin-top: 6px;">
        <button onclick={createApiKey} disabled={apikeyCreateBusy || !apikeyNew.name.trim()} style="flex: 1; font-family: monospace; font-size: 11px; font-weight: 700; padding: 8px 12px; cursor: pointer; background: var(--pw-success); color: #fff; border: 2px solid var(--pw-success); text-transform: uppercase; opacity: {(apikeyCreateBusy || !apikeyNew.name.trim()) ? 0.5 : 1};">
          {apikeyCreateBusy ? 'CREATING…' : 'CREATE KEY'}
        </button>
        <button onclick={() => apikeysShowCreate = false} disabled={apikeyCreateBusy} style="font-family: monospace; font-size: 11px; font-weight: 700; padding: 8px 16px; cursor: pointer; background: transparent; color: #555; border: 2px solid #555; text-transform: uppercase;">CANCEL</button>
      </div>
    </div>
  </div>
{/if}

<!-- ═════ USAGE DRAWER ═════ -->
{#if apikeyUsageOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div onclick={closeUsage} style="position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 500;"></div>
  <div style="position: fixed; top: 0; right: 0; bottom: 0; width: 520px; max-width: 100vw; background: var(--pw-surface-warm); border-left: 2px solid var(--pw-ink); z-index: 501; overflow-y: auto; font-family: monospace; box-shadow: -6px 0 0 var(--pw-ink);">
    <div style="background: var(--pw-ink); color: var(--pw-accent); padding: 10px 14px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 1;">
      <span style="font-size: 11px; font-weight: 900; letter-spacing: 0.08em;">▸ USAGE — {apikeyUsageRow?.name}</span>
      <button onclick={closeUsage} style="background: none; border: 1.5px solid var(--pw-accent); color: var(--pw-accent); padding: 2px 8px; cursor: pointer; font-family: monospace; font-size: 11px; font-weight: 700;"><Icon name="x" size={14} /></button>
    </div>
    <div style="padding: 14px;">
      {#if !apikeyUsageData}
        <div class="skel-row" style="width: 80%;"></div>
        <div class="skel-row" style="width: 70%;"></div>
      {:else if apikeyUsageData.error}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px;">{apikeyUsageData.error}</div>
      {:else}
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 14px;">
          {#each [
            { l: 'CALLS', v: apikeyUsageData.calls, c: '#0078d4' },
            { l: 'ERRORS', v: apikeyUsageData.errors, c: 'var(--pw-error)' },
            { l: 'p50 ms', v: apikeyUsageData.p50_latency_ms ?? '—', c: 'var(--pw-success)' },
            { l: 'p95 ms', v: apikeyUsageData.p95_latency_ms ?? '—', c: '#d97706' },
          ] as k}
            <div style="border: 2px solid var(--pw-ink); padding: 8px; text-align: center; background: #fff;">
              <div style="font-size: 13px; font-weight: 900; color: {k.c};">{k.v}</div>
              <div style="font-size: 11px; font-weight: 700; color: #555; letter-spacing: 0.1em; margin-top: 2px;">{k.l}</div>
            </div>
          {/each}
        </div>
        <div style="font-size: 10px; font-weight: 700; color: #555; letter-spacing: 0.1em; margin-bottom: 6px;">DAILY CALLS ({apikeyUsageData.days || 14}d)</div>
        <div bind:this={apikeyUsageContainer} style="width: 100%; height: 200px; border: 2px solid var(--pw-ink); background: var(--pw-surface-warm); margin-bottom: 14px;"></div>

        {#if (apikeyUsageData.top_endpoints || []).length > 0}
          <div style="font-size: 10px; font-weight: 700; color: #555; letter-spacing: 0.1em; margin-bottom: 6px;">TOP ENDPOINTS</div>
          <div style="display: flex; flex-direction: column; gap: 4px;">
            {#each apikeyUsageData.top_endpoints as e}
              <div style="background: #fff; border: 1.5px solid var(--pw-ink); padding: 4px 8px; font-size: 10px; display: flex; justify-content: space-between;">
                <code>{e.endpoint}</code>
                <span style="font-weight: 700;">{e.count}</span>
              </div>
            {/each}
          </div>
        {/if}
      {/if}
    </div>
  </div>
{/if}

<!-- ═════ LINEAGE NODE DETAIL PANEL (right side) ═════ -->
{#if lineageSelectedNode}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div onclick={closeLineageNodeDetail} style="position: fixed; inset: 0; background: rgba(0,0,0,0.35); z-index: 480;"></div>
  <div class="lineage-detail" style="position: fixed; right: 0; top: 0; bottom: 0; width: 320px; background: #fff; border-left: 1px solid var(--pw-border); z-index: 481; box-shadow: -4px 0 18px rgba(0,0,0,0.10); overflow-y: auto; font-family: var(--pw-sans);">
    <div style="display: flex; justify-content: space-between; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--pw-border); position: sticky; top: 0; background: #fff; z-index: 1;">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-muted); font-weight: 600;">Node detail</div>
      <button onclick={closeLineageNodeDetail} title="Close (Esc)" style="background: none; border: none; cursor: pointer; padding: 4px; color: var(--pw-ink);">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div style="padding: 18px;">
      <h2 style="font-family: var(--pw-serif, 'EB Garamond', serif); font-size: 19px; font-weight: 500; color: var(--pw-ink); margin: 0 0 12px; word-break: break-word;">{lineageSelectedNode.name}</h2>
      {#if lineageSelectedNode.loading}
        <div class="skel-row" style="width: 80%;"></div>
        <div class="skel-row" style="width: 60%;"></div>
      {:else if lineageSelectedNode.error}
        <div style="color: var(--pw-error, #dc2626); font-size: 11px;">Error: {lineageSelectedNode.error}</div>
      {:else}
        <div style="display: flex; flex-direction: column; gap: 12px;">
          {#if lineageSelectedNode.provenance?.source || lineageSelectedNode.source}
            <div>
              <div style="font-size: 10px; color: var(--pw-muted); text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; margin-bottom: 4px;">Source</div>
              <span class="chip chip-coral">{lineageSelectedNode.provenance?.source || lineageSelectedNode.source}</span>
            </div>
          {/if}
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
            <div style="background: var(--pw-bg-alt); padding: 10px 12px; border-radius: 0;">
              <div style="font-size: 10px; color: var(--pw-muted); text-transform: uppercase; font-weight: 600;">Neighbors</div>
              <div style="font-family: var(--pw-serif, serif); font-size: 19px; font-weight: 500;">{(lineageSelectedNode.links_out || []).length + (lineageSelectedNode.links_in || []).length}</div>
            </div>
            <div style="background: var(--pw-bg-alt); padding: 10px 12px; border-radius: 0;">
              <div style="font-size: 10px; color: var(--pw-muted); text-transform: uppercase; font-weight: 600;">Used by</div>
              <div style="font-family: var(--pw-serif, serif); font-size: 19px; font-weight: 500;">{(lineageSelectedNode.active_agents || []).length}</div>
            </div>
          </div>
          {#if lineageSelectedNode.provenance?.confidence != null}
            <div>
              <div style="font-size: 10px; color: var(--pw-muted); text-transform: uppercase; font-weight: 600; margin-bottom: 4px;">Confidence</div>
              <div style="display: flex; align-items: center; gap: 8px;">
                <div style="flex: 1; height: 6px; background: var(--pw-bg-alt); border-radius: 0; overflow: hidden;">
                  <div style="height: 100%; width: {Math.round((lineageSelectedNode.provenance.confidence || 0) * 100)}%; background: var(--pw-accent, #c96342);"></div>
                </div>
                <span style="font-size: 11px; font-weight: 600;">{((lineageSelectedNode.provenance.confidence || 0) * 100).toFixed(0)}%</span>
              </div>
            </div>
          {/if}
          {#if (lineageSelectedNode.aliases || []).length > 0}
            <div>
              <div style="font-size: 10px; color: var(--pw-muted); text-transform: uppercase; font-weight: 600; margin-bottom: 6px;">Aliases</div>
              <div style="display: flex; flex-wrap: wrap; gap: 4px;">
                {#each lineageSelectedNode.aliases as a}<span class="chip chip-coral">{a}</span>{/each}
              </div>
            </div>
          {/if}
          <div style="display: flex; gap: 8px; margin-top: 6px;">
            <button class="btn-primary btn-sm" onclick={() => { openDrill(lineageSelectedNode.name); closeLineageNodeDetail(); }}>View neighbors</button>
            <button class="btn-secondary btn-sm" onclick={promoteLineageNode}>Promote</button>
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}

<!-- ═══════════════════════════════════════════════════════ -->
<!-- DRILL DRAWER                                            -->
<!-- ═══════════════════════════════════════════════════════ -->
{#if drillOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div
    onclick={closeDrill}
    style="position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 500;"
  ></div>
  <div style="position: fixed; top: 0; right: 0; bottom: 0; width: 480px; max-width: 100vw; background: var(--pw-surface-warm); border-left: 2px solid var(--pw-ink); z-index: 501; overflow-y: auto; font-family: monospace; box-shadow: -6px 0 0 var(--pw-ink);">
    <div style="background: var(--pw-ink); color: var(--pw-accent); padding: 10px 14px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 1; gap: 8px;">
      <span style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">▸ {drillName}</span>
      <div style="display: flex; gap: 6px; align-items: center;">
        <button
          onclick={copyDetailJSON}
          disabled={!drillDetail || drillDetail.error}
          title="Copy JSON"
          style="background: none; border: 1.5px solid var(--pw-accent); color: var(--pw-accent); padding: 2px 8px; cursor: pointer; font-family: monospace; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; opacity: {(!drillDetail || drillDetail.error) ? 0.4 : 1};"
        >{copyFlash ? 'Copied!' : 'COPY JSON'}</button>
        <button onclick={closeDrill} style="background: none; border: 1.5px solid var(--pw-accent); color: var(--pw-accent); padding: 2px 8px; cursor: pointer; font-family: monospace; font-size: 11px; font-weight: 700;"><Icon name="x" size={14} /></button>
      </div>
    </div>
    <div style="padding: 14px;">
      {#if drillLoading}
        <div class="skel-row" style="width: 65%;"></div>
        <div class="skel-row" style="width: 80%;"></div>
        <div class="skel-row" style="width: 70%;"></div>
      {:else if drillDetail?.error}
        <div style="border: 2px solid var(--pw-error); background: var(--pw-accent-soft); color: var(--pw-error); padding: 8px 12px; font-size: 11px;">API error: {drillDetail.error}</div>
      {:else if drillDetail}
        <!-- Aliases -->
        {#if (drillDetail.aliases || []).length > 0}
          <div style="margin-bottom: 14px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; color: #555; margin-bottom: 6px; text-transform: uppercase;">aliases</div>
            <div style="display: flex; flex-wrap: wrap; gap: 4px;">
              {#each drillDetail.aliases as a}
                <span style="background: #fff3d6; border: 1px solid #d97706; padding: 2px 8px; font-size: 10px; font-weight: 700;">{a}</span>
              {/each}
            </div>
          </div>
        {/if}

        <!-- Templates using -->
        {#if (drillDetail.templates_using || []).length > 0}
          <div style="margin-bottom: 14px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; color: #555; margin-bottom: 6px; text-transform: uppercase;">templates using ({drillDetail.templates_using.length})</div>
            <div style="display: flex; flex-direction: column; gap: 4px;">
              {#each drillDetail.templates_using as t}
                <div style="background: #fff; border: 1.5px solid var(--pw-ink); padding: 5px 9px; font-size: 10px; display: flex; justify-content: space-between;">
                  <span style="font-weight: 700;">{t.template}</span>
                  <span style="color: #555; font-size: 11px;">{t.category || ''}</span>
                </div>
              {/each}
            </div>
          </div>
        {/if}

        <!-- Active agents -->
        {#if (drillDetail.active_agents || []).length > 0}
          <div style="margin-bottom: 14px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; color: #555; margin-bottom: 6px; text-transform: uppercase;">active agents ({drillDetail.active_agents.length})</div>
            <div style="display: flex; flex-direction: column; gap: 4px;">
              {#each drillDetail.active_agents as a}
                <div style="background: #fff; border: 1.5px solid var(--pw-ink); padding: 5px 9px; font-size: 10px; display: flex; justify-content: space-between; gap: 6px;">
                  <span style="font-weight: 700;"><Icon name="bar-chart" size={14} /> {a.project_name || a.project_slug}</span>
                  <span style="color: #555; font-size: 11px;">{fmtDate(a.applied_at)}</span>
                </div>
              {/each}
            </div>
          </div>
        {/if}

        <!-- Common properties -->
        {#if (drillDetail.properties_common || []).length > 0}
          <div style="margin-bottom: 14px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; color: #555; margin-bottom: 6px; text-transform: uppercase;">common properties</div>
            <div style="display: flex; flex-direction: column; gap: 3px;">
              {#each drillDetail.properties_common as p}
                <div style="font-size: 10px; display: flex; align-items: center; gap: 8px;">
                  <span style="font-weight: 700; min-width: 100px;">{p.col}</span>
                  <div style="flex: 1; height: 8px; background: #ddd; border: 1px solid var(--pw-ink);">
                    <div style="height: 100%; width: {Math.min(100, (p.appears_in || 0) * 10)}%; background: var(--pw-success);"></div>
                  </div>
                  <span style="color: #555; font-size: 11px; min-width: 28px; text-align: right;">{p.appears_in}×</span>
                </div>
              {/each}
            </div>
          </div>
        {/if}

        <!-- Links out -->
        {#if (drillDetail.links_out || []).length > 0}
          <div style="margin-bottom: 14px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; color: #555; margin-bottom: 6px; text-transform: uppercase;">links out ({drillDetail.links_out.length})</div>
            <div style="display: flex; flex-direction: column; gap: 4px;">
              {#each drillDetail.links_out as l}
                <div style="background: #fff; border: 1.5px solid var(--pw-ink); padding: 5px 9px; font-size: 10px; display: flex; align-items: center; gap: 6px;">
                  <span style="font-weight: 700;">{l.from}</span>
                  <span style="color: #d97706; font-weight: 700;">→{l.rel}→</span>
                  <span style="font-weight: 700;">{l.to}</span>
                  <span style="margin-left: auto; color: #555; font-size: 11px;">{l.count ?? ''}</span>
                </div>
              {/each}
            </div>
          </div>
        {/if}

        <!-- Actions using -->
        {#if (drillDetail.actions_using || []).length > 0}
          <div style="margin-bottom: 14px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; color: #555; margin-bottom: 6px; text-transform: uppercase;">actions using ({drillDetail.actions_using.length})</div>
            <div style="display: flex; flex-direction: column; gap: 4px;">
              {#each drillDetail.actions_using as a}
                <div style="background: #fff; border: 1.5px solid var(--pw-ink); padding: 5px 9px; font-size: 10px;">
                  <div style="font-weight: 700;">{a.name}</div>
                  <div style="color: #555; font-size: 11px; margin-top: 2px;">
                    <code style="background: #f5f5e8; padding: 1px 4px;">{a.schedule || '—'}</code>
                    · <span style="background: #f0e8ff; border: 1px solid #9b59b6; padding: 0px 4px;">{a.action || '—'}</span>
                  </div>
                </div>
              {/each}
            </div>
          </div>
        {/if}

        <!-- Provenance -->
        {#if drillDetail.provenance}
          <div style="margin-bottom: 14px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; color: #555; margin-bottom: 6px; text-transform: uppercase;">provenance</div>
            <div style="background: var(--pw-ink); color: var(--pw-accent); padding: 10px 12px; font-size: 10px;">
              <div>first_seen: <span style="color: #fff;">{drillDetail.provenance.first_seen_template || '—'}</span></div>
              <div>promoted_at: <span style="color: #fff;">{fmtDate(drillDetail.provenance.promoted_at)}</span></div>
              <div>confidence: <span style="color: #fff;">{((drillDetail.provenance.confidence ?? 0) * 100).toFixed(0)}%</span></div>
            </div>
          </div>
        {/if}
      {/if}
    </div>
  </div>
{/if}
