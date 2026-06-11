<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { brand } from '$lib/stores/branding';
 import { confirmDelete } from '$lib/confirmDelete';
 import GovernancePanel from './_panels/GovernancePanel.svelte';
 import AgentOsAdminPanel from './_panels/AgentOsAdminPanel.svelte';
 import TelemetryPanel from './_panels/TelemetryPanel.svelte';
 import AccuracyPanel from '$lib/admin/AccuracyPanel.svelte';
 import GoldenPanel from '$lib/admin/GoldenPanel.svelte';
 import ScopeAuditPanel from '$lib/admin/ScopeAuditPanel.svelte';
 import DataviewPanel from '$lib/admin/DataviewPanel.svelte';
 import PacksPanel from '$lib/admin/PacksPanel.svelte';
 import ConnectorsPanel from '$lib/admin/ConnectorsPanel.svelte';
import LLMConfigPanel from '$lib/admin/LLMConfigPanel.svelte';
 import GatewayPanel from '$lib/admin/GatewayPanel.svelte';
 import AuthAdminPanel from '$lib/admin/AuthAdminPanel.svelte';
 import ObservabilityPanel from '$lib/admin/ObservabilityPanel.svelte';
 import VersionCard from '$lib/VersionCard.svelte';

 /* ─── state ─── */
 let activeTab = $state('cockpit');
 let loading = $state(false);

 /* ─── auth helper ─── */
 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 /* ─── tabs ─── */
 const tabs = [
 { id: 'users', label: 'USERS' },
 { id: 'projects', label: 'PROJECTS' },
 { id: 'logs', label: 'LOGS' },
 { id: 'schemas', label: 'SCHEMAS' },
 { id: 'chatLogs', label: 'CHAT LOGS' },
 { id: 'health', label: 'HEALTH' },
 { id: 'stats', label: 'STATS' },
 { id: 'integrations', label: 'INTEGRATIONS' },
 { id: 'connectors', label: 'CONNECTORS' },
 { id: 'architecture', label: 'ARCHITECTURE' },
 { id: 'branding', label: 'BRANDING' },
 { id: 'drift', label: 'DRIFT' },
 { id: 'fed-admin', label: 'FEDERATION' },
 { id: 'admin-settings', label: 'ADMIN SETTINGS' },
 ];

 const tabMeta: Record<string, { label: string; subtitle: string }> = {
 cockpit: { label: 'Overview', subtitle: 'Health · stats · traces · integrations — one page' },
 observability: { label: 'Observability', subtitle: 'Per-chat traces & context health' },
 gateway: { label: 'API Gateway', subtitle: 'External /api/v1 keys & usage' },
 embed: { label: 'Embed', subtitle: 'Embeddable chat widgets + usage' },
 brain: { label: 'Brain', subtitle: 'Company knowledge — glossary, formulas, graph' },
 auth: { label: 'Authentication', subtitle: 'Local · LDAP · OIDC / SSO' },
 users: { label: 'Users', subtitle: 'Manage user accounts, roles, and access' },
 projects: { label: 'Projects', subtitle: 'All projects across the platform with brain health' },
 logs: { label: 'Audit logs', subtitle: 'Security and activity audit trail' },
 schemas: { label: 'Schemas', subtitle: 'PostgreSQL schemas and tables per project' },
 chatLogs: { label: 'Chat logs', subtitle: 'All chat sessions across users and projects' },
 health: { label: 'System health', subtitle: 'Live status of services, workers, and connectors' },
 stats: { label: 'Platform stats', subtitle: 'Usage, growth, and system metrics' },
 integrations: { label: 'Integrations', subtitle: 'Connector configuration and admin setup' },
 connectors: { label: 'External connectors', subtitle: 'PostgreSQL · MySQL · BigQuery · PowerBI (super-admin)' },
 governance: { label: 'Governance', subtitle: 'Secret leaks · hooks · refusal audit' },
 'agent-os-admin': { label: 'Agent OS admin', subtitle: 'Drafts · fleet · workflows · evals' },
 'telemetry-admin': { label: 'Telemetry', subtitle: 'Cost · run timeline · skill heatmap' },
 architecture: { label: 'Architecture', subtitle: 'Interactive system architecture and live metrics' },
 branding: { label: 'Branding', subtitle: 'White-label tenants, logos, and theme colors' },
 drift: { label: 'Data drift', subtitle: 'Drift alerts across all projects' },
 'fed-admin': { label: 'Federation', subtitle: 'Federated query health and circuit breakers' },
 'admin-settings':{ label: 'Admin settings', subtitle: 'Platform-wide configuration values' },
 traces: { label: 'Traces', subtitle: 'Observability — agent runs, crons, costs, and failures across the platform' },
 channels: { label: 'Channels', subtitle: 'Slack workspaces, email accounts, voice numbers, threaded inbox' },
 mcp: { label: 'MCP Servers', subtitle: 'External Model Context Protocol server registry + tool bindings' },
 accuracy: { label: 'Accuracy trend', subtitle: 'Pass-rate over time + per-tier breakdown' },
 golden: { label: 'Golden Q&A', subtitle: 'Manage golden corpus + drift status' },
 'scope-audit': { label: 'Chat scope audit', subtitle: 'Per-session timeline + tables/skills/tools/cost' },
 dataview: { label: 'Dataview', subtitle: 'Inspect tables, presets, archive' },
 packs: { label: 'Packs', subtitle: 'Vertical packs registry + install' },
 llm: { label: 'LLM config', subtitle: 'OpenRouter API keys (encrypted) + models — hot reload, no restart' },
 };

 // Single-agent product hides multi-project grids (Projects + Schemas).
 let singleAgent = $state(false);
 // CityPharma single-agent: pruned 6 dead/wrong-domain tabs from the rail —
 // architecture (multi-service viz), connectors (SharePoint/GDrive/OneDrive — backend gone),
 // drift (ML monitoring, no consumer), fed-admin (multi-tenant), channels (Slack/Teams fan-out),
 // mcp (agent-builder). Content blocks left dead-but-unreachable (no rail entry point).
 const _railGroupsBase: { label: string; items: string[] }[] = [
 { label: 'Overview', items: ['cockpit'] },
 { label: 'People', items: ['projects'] },
 { label: 'Data', items: ['schemas','integrations'] },
 { label: 'Platform', items: ['auth'] },
 { label: 'System', items: ['traces','logs','admin-settings','llm'] },
 { label: 'Trust & Governance', items: ['accuracy','golden','scope-audit'] },
 ];
 const railGroups = $derived(
 singleAgent
 ? _railGroupsBase.map(g => ({ ...g, items: g.items.filter(i => i !== 'projects' && i !== 'schemas') }))
 : _railGroupsBase
 );

 // Sub-tabs for the 3 expandable parents in the LEFT RAIL Governance group
 const govSubs = [
   {id:'gov-overview',label:'Overview',icon:'compass'},{id:'gov-policies',label:'Policies',icon:'shield'},
   {id:'gov-approvals',label:'Approvals',icon:'check-circle'},{id:'gov-zones',label:'Data zones',icon:'globe'},
   {id:'gov-pii',label:'PII rules',icon:'ban'},{id:'gov-retention',label:'Retention',icon:'inbox'},
   {id:'gov-hooks',label:'Audit hooks',icon:'plug'},{id:'gov-compliance',label:'Compliance map',icon:'clipboard'},
 ];
 const aosSubs = [
   {id:'aos-overview',label:'Overview',icon:'compass'},{id:'aos-registry',label:'Registry',icon:'list'},
   {id:'aos-capabilities',label:'Capabilities',icon:'sparkles'},{id:'aos-quotas',label:'Quotas',icon:'bar-chart-2'},
   {id:'aos-models',label:'Models',icon:'cpu'},{id:'aos-tools',label:'Tools',icon:'tool'},
   {id:'aos-memory',label:'Memory',icon:'database'},{id:'aos-workflows',label:'Workflows',icon:'puzzle'},
   {id:'aos-kill',label:'Kill switch',icon:'alert-triangle'},{id:'aos-cost',label:'Cost guard',icon:'dollar-sign'},
 ];
 const telSubs = [
   {id:'tel-overview',label:'Overview',icon:'compass'},{id:'tel-live',label:'Live',icon:'zap'},
   {id:'tel-cost',label:'Cost',icon:'dollar-sign'},{id:'tel-errors',label:'Errors',icon:'alert-triangle'},
   {id:'tel-latency',label:'Latency / SLO',icon:'loader'},{id:'tel-tools',label:'Tool usage',icon:'bar-chart'},
   {id:'tel-connectors',label:'Connector health',icon:'plug'},{id:'tel-tokens',label:'Token flow',icon:'circle-filled'},
   {id:'tel-alerts',label:'Alerts',icon:'bell'},
 ];
 // Per-group expanded state. ALL top-level rail groups + sub-parents default CLOSED.
 // Auto-expand only the group/sub-parent containing the current activeTab.
 const _railDefaults: Record<string, boolean> = {
   Overview: false, People: false, Data: false, System: false, 'Trust & Governance': true,
   gov: false, aos: false, tel: false,
 };
 // Rail is always fully expanded — kept as no-op stub for any residual reads
 let railOpen = $state<Record<string, boolean>>({ ..._railDefaults });

 /* ─── FEDERATION (admin) state ─── */
 let fedAdmin = $state<any>({});
 let fedAdminDays = $state(7);
 async function loadFedAdmin() {
 try {
 const r = await fetch(`/api/federation/admin/all-projects?days=${fedAdminDays}`, { headers: _ccHdr() });
 if (r.ok) fedAdmin = await r.json();
 } catch {}
 }
 async function resetCircuitAdmin(slug: string) {
 try {
 await fetch(`/api/federation/circuit/${slug}/reset`, { method: 'POST', headers: _ccHdr() });
 await loadFedAdmin();
 } catch {}
 }

 /* ─── MIGRATION DRIFT GATE state ─── */
 let driftStatus = $state<any>(null);
 let driftLoading = $state(false);
 let driftError = $state<string>('');
 async function loadDriftStatus() {
 driftLoading = true; driftError = '';
 try {
 const r = await fetch('/api/admin/drift/status', { headers: _ccHdr() });
 if (r.status === 404 || r.status === 403) { driftStatus = null; driftError = 'unavailable'; }
 else if (r.ok) {
 const j = await r.json();
 if (j && typeof j.drift_after_allowlist === 'number') driftStatus = j;
 else { driftStatus = null; driftError = 'malformed'; }
 } else { driftStatus = null; driftError = 'unavailable'; }
 } catch { driftStatus = null; driftError = 'unavailable'; }
 finally { driftLoading = false; }
 }
 function _driftRelTime(iso: string): string {
 if (!iso) return '';
 try {
 const t = new Date(iso).getTime();
 const diff = Date.now() - t;
 const s = Math.floor(diff / 1000);
 if (s < 60) return `${s}s ago`;
 const m = Math.floor(s / 60);
 if (m < 60) return `${m} min ago`;
 const h = Math.floor(m / 60);
 if (h < 24) return `${h}h ago`;
 const d = Math.floor(h / 24);
 return `${d}d ago`;
 } catch { return ''; }
 }

 /* ─── BRANDING state ─── */
 let brandingTenants = $state<any[]>([]);
 let brandingActive = $state<string>('');
 let brandingMessage = $state<string>('');
 let brandingRoot = $state<string>('');
 let brandingActivating = $state<string>('');

 async function loadBranding() {
 try {
 const r = await fetch('/api/branding/tenants', { headers: _ccHdr() });
 if (r.ok) {
 const j = await r.json();
 brandingTenants = j.tenants || [];
 brandingActive = j.active || '';
 brandingMessage = j.message || '';
 brandingRoot = j.branding_root || '';
 }
 } catch {}
 }

 async function activateBrandingTenant(name: string) {
 if (!name || brandingActivating) return;
 brandingActivating = name;
 try {
 const r = await fetch('/api/branding/active', {
 method: 'POST',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ tenant: name }),
 });
 if (r.ok) {
 // Hot reload so theme.css + logo refetch
 window.location.reload();
 } else {
 const err = await r.json().catch(() => ({}));
 alert('Failed to activate: ' + (err.detail || r.status));
 }
 } catch (e: any) {
 alert('Failed: ' + (e?.message || e));
 } finally {
 brandingActivating = '';
 }
 }

 /* ─── BRANDING extended state (modals + edit form) ─── */
 let showNewTenantModal = $state(false);
 let showEditTenantModal = $state<string | null>(null);
 let newTenantForm = $state<{ slug: string; display_name: string; clone_from: string; activate_after: boolean }>({
 slug: '',
 display_name: '',
 clone_from: '',
 activate_after: false,
 });
 let newTenantBusy = $state(false);
 let newTenantError = $state('');

 type BrandEditForm = {
 app_name: string;
 full_name: string;
 tagline: string;
 domain: string;
 support_email: string;
 primary_color: string;
 background_color: string;
 accent_color: string;
 footer_text: string;
 show_powered_by: boolean;
 css: string;
 logo_url: string;
 favicon_url: string;
 raw: any;
 };
 let editForm = $state<BrandEditForm>({
 app_name: '', full_name: '', tagline: '', domain: '', support_email: '',
 primary_color: 'var(--pw-accent)', background_color: '#0a0a0a', accent_color: '#a06000',
 footer_text: '', show_powered_by: true, css: '',
 logo_url: '', favicon_url: '', raw: {},
 });
 let editPendingLogo = $state<File | null>(null);
 let editPendingFavicon = $state<File | null>(null);
 let editBusy = $state(false);
 let editError = $state('');

 async function openEditTenant(slug: string) {
 editError = '';
 editPendingLogo = null;
 editPendingFavicon = null;
 try {
 const r = await fetch(`/api/branding/tenants/${slug}/preview`, { headers: _ccHdr() });
 if (!r.ok) {
 alert('Failed to load tenant preview: ' + r.status);
 return;
 }
 const j = await r.json();
 const c = j.company || {};
 const theme = j.theme || {};
 editForm = {
 app_name: c.app_name || c.short_name || '',
 full_name: c.full_name || c.company_name || '',
 tagline: c.tagline || '',
 domain: c.domain || '',
 support_email: c.support_email || '',
 primary_color: (theme.colors?.primary) || c.primary_color || 'var(--pw-accent)',
 background_color: (theme.colors?.background) || c.background_color || '#0a0a0a',
 accent_color: (theme.colors?.accent) || c.accent_color || '#a06000',
 footer_text: c.footer_text || '',
 show_powered_by: c.show_powered_by !== false,
 css: j.theme_css || theme.css || '',
 logo_url: `/api/branding/logo.svg?t=${slug}&v=${Date.now()}`,
 favicon_url: `/api/branding/favicon.ico?t=${slug}&v=${Date.now()}`,
 raw: c,
 };
 showEditTenantModal = slug;
 } catch (e: any) {
 alert('Failed: ' + (e?.message || e));
 }
 }

 function closeEditTenant() {
 showEditTenantModal = null;
 editPendingLogo = null;
 editPendingFavicon = null;
 editError = '';
 }

 function buildCompanyPayload(): any {
 const merged = { ...(editForm.raw || {}) };
 merged.app_name = editForm.app_name;
 merged.full_name = editForm.full_name;
 merged.tagline = editForm.tagline;
 merged.domain = editForm.domain;
 merged.support_email = editForm.support_email;
 merged.primary_color = editForm.primary_color;
 merged.background_color = editForm.background_color;
 merged.accent_color = editForm.accent_color;
 merged.footer_text = editForm.footer_text;
 merged.show_powered_by = editForm.show_powered_by;
 return merged;
 }

 async function uploadAsset(slug: string, file: File, kind: 'logo' | 'favicon'): Promise<void> {
 const fd = new FormData();
 fd.append('asset_type', kind);
 fd.append('file', file);
 const r = await fetch(`/api/branding/tenants/${slug}/upload`, {
 method: 'POST',
 headers: _ccHdr(), // do NOT set Content-Type — browser sets multipart boundary
 body: fd,
 });
 if (!r.ok) {
 const err = await r.json().catch(() => ({}));
 throw new Error(`Upload ${kind} failed: ${err.detail || r.status}`);
 }
 // Bust cache on preview URL after successful upload
 const ts = Date.now();
 if (kind === 'logo') {
 editForm.logo_url = `/api/branding/logo.svg?t=${slug}&v=${ts}`;
 } else {
 editForm.favicon_url = `/api/branding/favicon.ico?t=${slug}&v=${ts}`;
 }
 }

 async function saveTenantEdit(slug: string, activateAfter: boolean) {
 if (editBusy) return;
 editBusy = true;
 editError = '';
 try {
 // 1. PUT company.json
 const rCompany = await fetch(`/api/branding/tenants/${slug}`, {
 method: 'PUT',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify(buildCompanyPayload()),
 });
 if (!rCompany.ok) {
 const err = await rCompany.json().catch(() => ({}));
 throw new Error('Save company failed: ' + (err.detail || rCompany.status));
 }

 // 2. PUT theme.css
 const rTheme = await fetch(`/api/branding/tenants/${slug}/theme`, {
 method: 'PUT',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ css: editForm.css }),
 });
 if (!rTheme.ok) {
 const err = await rTheme.json().catch(() => ({}));
 throw new Error('Save theme failed: ' + (err.detail || rTheme.status));
 }

 // 3. Upload pending assets
 if (editPendingLogo) await uploadAsset(slug, editPendingLogo, 'logo');
 if (editPendingFavicon) await uploadAsset(slug, editPendingFavicon, 'favicon');

 // 4. Optionally activate
 if (activateAfter) {
 const rAct = await fetch('/api/branding/active', {
 method: 'POST',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ tenant: slug }),
 });
 if (rAct.ok) {
 window.location.reload();
 return;
 }
 }

 // Bust cache on preview URLs after successful save
 const ts = Date.now();
 editForm.logo_url = `/api/branding/logo.svg?t=${slug}&v=${ts}`;
 editForm.favicon_url = `/api/branding/favicon.ico?t=${slug}&v=${ts}`;
 await loadBranding();
 closeEditTenant();
 } catch (e: any) {
 editError = e?.message || String(e);
 } finally {
 editBusy = false;
 }
 }

 async function deleteTenant(slug: string) {
 if (!slug || slug === 'default' || slug === brandingActive) return;
 if (!(await confirmDelete({ itemName: slug, itemType: 'tenant' }))) return;
 try {
 const r = await fetch(`/api/branding/tenants/${slug}`, {
 method: 'DELETE',
 headers: _ccHdr(),
 });
 if (!r.ok) {
 const err = await r.json().catch(() => ({}));
 alert('Delete failed: ' + (err.detail || r.status));
 return;
 }
 if (showEditTenantModal === slug) closeEditTenant();
 await loadBranding();
 } catch (e: any) {
 alert('Delete failed: ' + (e?.message || e));
 }
 }

 async function exportTenant(slug: string) {
 try {
 const r = await fetch(`/api/branding/tenants/${slug}/export`, { headers: _ccHdr() });
 if (!r.ok) {
 alert('Export failed: ' + r.status);
 return;
 }
 const blob = await r.blob();
 const url = URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url;
 a.download = `${slug}-branding.zip`;
 document.body.appendChild(a);
 a.click();
 document.body.removeChild(a);
 URL.revokeObjectURL(url);
 } catch (e: any) {
 alert('Export failed: ' + (e?.message || e));
 }
 }

 function openNewTenantModal() {
 newTenantForm = { slug: '', display_name: '', clone_from: '', activate_after: false };
 newTenantError = '';
 showNewTenantModal = true;
 }

 async function submitNewTenant() {
 if (newTenantBusy) return;
 const slug = (newTenantForm.slug || '').trim().toLowerCase();
 if (!/^[a-z0-9_-]+$/.test(slug)) {
 newTenantError = 'Slug must be lowercase letters, digits, _ or - only.';
 return;
 }
 if (!newTenantForm.display_name.trim()) {
 newTenantError = 'Display name is required.';
 return;
 }
 newTenantBusy = true;
 newTenantError = '';
 try {
 const body: any = {
 slug,
 display_name: newTenantForm.display_name.trim(),
 activate_after: newTenantForm.activate_after,
 };
 if (newTenantForm.clone_from) body.clone_from = newTenantForm.clone_from;
 const r = await fetch('/api/branding/tenants', {
 method: 'POST',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify(body),
 });
 if (!r.ok) {
 const err = await r.json().catch(() => ({}));
 throw new Error(err.detail || `HTTP ${r.status}`);
 }
 if (newTenantForm.activate_after) {
 window.location.reload();
 return;
 }
 await loadBranding();
 showNewTenantModal = false;
 } catch (e: any) {
 newTenantError = e?.message || String(e);
 } finally {
 newTenantBusy = false;
 }
 }

 function onLogoFileChange(e: Event) {
 const f = (e.target as HTMLInputElement).files?.[0];
 editPendingLogo = f || null;
 if (f) editForm.logo_url = URL.createObjectURL(f);
 }
 function onFaviconFileChange(e: Event) {
 const f = (e.target as HTMLInputElement).files?.[0];
 editPendingFavicon = f || null;
 if (f) editForm.favicon_url = URL.createObjectURL(f);
 }

 /* ─── DRIFT state ─── */
 let allDriftEvents = $state<any[]>([]);
 async function loadAllDrift() {
 try {
 const r = await fetch('/api/drift/admin/all-open?limit=200', { headers: _ccHdr() });
 if (r.ok) allDriftEvents = (await r.json()).events || [];
 } catch {}
 }

 /* ─── ADMIN SETTINGS state ─── */
 let allSettings = $state<any[]>([]);
 let dirty = $state<Record<string, any>>({});
 let savingSection = $state<string | null>(null);
 let openSection = $state<string | null>('SELF-LEARNING');

 async function loadAdminSettings() {
 try {
 const r = await fetch('/api/admin/settings', { headers: _ccHdr() });
 if (r.ok) allSettings = (await r.json()).settings || [];
 } catch {}
 }

 async function saveSection(sectionKeys: string[]) {
 savingSection = sectionKeys[0] || 'unknown';
 const items = sectionKeys
 .filter(k => dirty[k] !== undefined)
 .map(k => ({ key: k, value: dirty[k], scope: 'global' }));
 if (items.length === 0) {
 savingSection = null;
 return;
 }
 try {
 await fetch('/api/admin/settings', {
 method: 'POST',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ settings: items }),
 });
 for (const k of sectionKeys) delete dirty[k];
 await loadAdminSettings();
 } finally {
 savingSection = null;
 }
 }

 // Integrations on/off (Cockpit) — stage changes, then Save (reload so the
 // top-nav re-reads /api/flags and shows/hides the surfaces).
 let integPending = $state<Record<string, boolean>>({});
 let integSaving = $state(false);
 function settingOn(key: string): boolean {
   const s = allSettings.find((x) => x.key === key);
   const v = s ? s.effective_value : true;
   return !(v === false || v === 'false' || v === 0 || v === '0');
 }
 function integView(key: string): boolean {
   return key in integPending ? integPending[key] : settingOn(key);
 }
 function toggleIntegration(key: string) {
   integPending = { ...integPending, [key]: !integView(key) };
 }
 let integDirty = $derived(
   Object.keys(integPending).some((k) => integPending[k] !== settingOn(k))
 );
 async function saveIntegrations() {
   const items = Object.keys(integPending)
     .filter((k) => integPending[k] !== settingOn(k))
     .map((k) => ({ key: k, value: integPending[k], scope: 'global' }));
   if (items.length === 0) return;
   integSaving = true;
   try {
     await fetch('/api/admin/settings', {
       method: 'POST',
       headers: { ..._h(), 'Content-Type': 'application/json' },
       body: JSON.stringify({ settings: items }),
     });
     // Reload so the shared top-nav re-fetches /api/flags and shows/hides items.
     window.location.reload();
   } finally {
     integSaving = false;
   }
 }

 async function resetSetting(key: string) {
 await fetch('/api/admin/settings/reset', {
 method: 'POST',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ key, scope: 'global' }),
 });
 delete dirty[key];
 await loadAdminSettings();
 }

 function findSetting(key: string): any {
 return allSettings.find(s => s.key === key);
 }

 /* ─── USERS state ─── */
 let users = $state<any[]>([]);
 let expandedUserId = $state<string | null>(null);
 let drawerUserId = $state<string | null>(null);
 let drawerUserRow = $state<any>(null);
 let userDetail = $state<any>(null);
 let loadingUserDetail = $state(false);

 // Create user
 let showCreateUser = $state(false);
 let newUsername = $state('');
 let newPassword = $state('');
 let newEmail = $state('');

 // Reset password
 let resetUser = $state('');
 let resetPass = $state('');
 let resetMsg = $state('');

 /* ─── PROJECTS state ─── */
 let projects = $state<any[]>([]);
 let expandedProjectSlug = $state<string | null>(null);
 let projectDetail = $state<any>(null);
 let loadingProjectDetail = $state(false);

 /* ─── LOGS state ─── */
 let logs = $state<any[]>([]);
 let logFilterAction = $state('');
 let logFilterUser = $state('');
 let logFilterProject = $state('');

 /* ─── SCHEMAS state ─── */
 let schemas = $state<any[]>([]);
 let expandedSchema = $state<string | null>(null);

 /* ─── CHAT LOGS state ─── */
 let chatLogs = $state<any[]>([]);
 let chatFilterUser = $state('');
 let chatFilterProject = $state('');
 let expandedChat = $state<string | null>(null);
 let drawerChatId = $state<string | null>(null);
 let drawerChatRow = $state<any>(null);

 /* ─── HEALTH state ─── */
 let health = $state<any>(null);

 /* ─── STATS state ─── */
 let stats = $state<any>(null);

 /* ─── ARCHITECTURE state ─── */
 let arch = $state<any>(null);
 let archFlowEl: HTMLDivElement;
 let archChart: any = null;

 async function loadArchitecture() {
 try { const r = await fetch('/api/architecture', { headers: _ccHdr() }); if (r.ok) arch = await r.json(); } catch {}
 // Render flow chart after data loads
 setTimeout(() => renderArchFlow(), 300);
 }

 async function renderArchFlow() {
 if (!archFlowEl || !arch) return;
 const echarts = await import('echarts/core');
 const { GraphChart } = await import('echarts/charts');
 const { TooltipComponent, TitleComponent } = await import('echarts/components');
 const { CanvasRenderer } = await import('echarts/renderers');
 echarts.use([GraphChart, TooltipComponent, TitleComponent, CanvasRenderer]);

 if (archChart) archChart.dispose();
 archChart = echarts.init(archFlowEl);

 const cats = [
 { name: 'Network', itemStyle: { color: '#3b82f6' } },
 { name: 'Agent', itemStyle: { color: '#22c55e' } },
 { name: 'Data', itemStyle: { color: '#f59e0b' } },
 { name: 'ML', itemStyle: { color: '#a855f7' } },
 { name: 'Knowledge', itemStyle: { color: '#06b6d4' } },
 { name: 'Security', itemStyle: { color: '#ef4444' } },
 { name: 'Learning', itemStyle: { color: '#ec4899' } },
 { name: 'Export', itemStyle: { color: '#84cc16' } },
 ];

 const m = arch.metrics || {};
 const md = arch.models || {};
 const w = arch.infra?.workers || 8;
 const rl = arch.infra?.rate_limit || '500/min';

 // Helper for tooltip
 const tt = (title: string, lines: string[]) => `<div style="font-size:11px;font-weight:700;margin-bottom:4px;color:#0f0;">${title}</div>` + lines.map(l => `<div style="font-size:11px;line-height:1.5;">${l}</div>`).join('');

 const nodes: any[] = [
 // ─── NETWORK LAYER ───
 { name: 'Users', x: 400, y: 20, symbolSize: 45, symbol: 'roundRect', category: 0,
 tooltip: { formatter: () => tt('USERS', [`${m.users || 0} registered`, `${m.chats || 0} chat sessions`]) } },
 { name: 'Caddy', x: 400, y: 90, symbolSize: 35, symbol: 'diamond', category: 5,
 tooltip: { formatter: () => tt('CADDY — REVERSE PROXY', ['Auto-SSL/TLS certificates', 'HSTS, X-Frame-Options, XSS', `Rate limit: ${rl}`, 'Body max: 250MB', 'Timeout: 300s']) } },
 { name: `FastAPI`, x: 400, y: 160, symbolSize: 50, symbol: 'roundRect', category: 0,
 tooltip: { formatter: () => tt(`FASTAPI — ${w} WORKERS`, ['Auth middleware + token cache', 'SSE streaming + rate limiter', `Model: ${md.chat}`, 'NullPool → PgBouncer']) } },
 { name: 'PgBouncer', x: 620, y: 110, symbolSize: 28, category: 0,
 tooltip: { formatter: () => tt('PGBOUNCER', ['Transaction pooling mode', 'Max 200 DB connections', 'NullPool (no double-pool)', 'SCRAM-SHA-256 auth']) } },
 { name: 'PostgreSQL', x: 770, y: 110, symbolSize: 50, symbol: 'roundRect', category: 3,
 tooltip: { formatter: () => tt('POSTGRESQL 18 + PGVECTOR', [`${m.projects || 0} project schemas`, '35+ system tables', 'Schema isolation per project', 'Statement timeout: 120s', 'pgvector for embeddings']) } },

 // ─── ROUTING ───
 { name: 'Router', x: 230, y: 240, symbolSize: 35, symbol: 'diamond', category: 5,
 tooltip: { formatter: () => tt('SMART ROUTER — 2 TIER', ['Tier 1: Keyword scoring (7 signals, $0, <1ms)', 'Tier 2: Router Agent + Brain ($0.001, <1.5s)', '', '"revenue by month" → Analyst', '"create view" → Engineer', '"what does report say" → Researcher']) } },

 // ─── AGENT LAYER ───
 { name: 'Leader', x: 400, y: 310, symbolSize: 45, symbol: 'roundRect', category: 1,
 tooltip: { formatter: () => tt('LEADER AGENT', ['Coordinates team of 4 specialists', 'Synthesizes responses', 'Decomposes complex questions', `Model: ${md.chat}`]) } },
 { name: 'Analyst', x: 180, y: 400, symbolSize: 42, symbol: 'roundRect', category: 1,
 tooltip: { formatter: () => tt('ANALYST — 31 TOOLS', ['SQL queries (read-only)', 'Self-correction (3 retries)', 'Auto-visualize charts', 'search_all for context first', `Model: ${md.chat}`]) } },
 { name: 'Researcher', x: 340, y: 400, symbolSize: 36, symbol: 'roundRect', category: 1,
 tooltip: { formatter: () => tt('RESEARCHER — DOC RAG', ['PPTX/PDF/DOCX analysis', 'Multi-signal search', 'Grounded facts (LangExtract)', 'Knowledge graph context', `Model: ${md.chat}`]) } },
 { name: 'Engineer', x: 670, y: 400, symbolSize: 36, symbol: 'roundRect', category: 1,
 tooltip: { formatter: () => tt('ENGINEER', ['Create views + dashboards', 'Schema management', 'Auto-VIEW at 3+ query uses', `Model: ${md.chat}`]) } },
 { name: 'Specialists', x: 180, y: 490, symbolSize: 28, category: 1,
 tooltip: { formatter: () => tt('10 SPECIALIST AGENTS', (arch.agents?.specialists || []).map((s: string) => `› ${s}`)) } },

 // ─── KNOWLEDGE LAYER ───
 { name: '13 Layers', x: 80, y: 310, symbolSize: 42, symbol: 'roundRect', category: 4,
 tooltip: { formatter: () => tt('13 CONTEXT LAYERS', ['1. Proven query patterns', '2. Approved responses', '3. Anti-patterns', '4. Agent memories', '5. Column annotations', '6. JOIN strategies', '7. User preferences', '8. Self-correction strategies', '9. Evolved instructions', '10. DB rules', '11. Grounded facts', '12. Knowledge graph', '13. Company brain']) } },
 { name: 'PgVector KB', x: 30, y: 400, symbolSize: 28, category: 4,
 tooltip: { formatter: () => tt('PGVECTOR KNOWLEDGE BASE', [`Embedding: ${md.embedding}`, 'Hybrid search (semantic + keyword)', 'Contextual retrieval (-49% failures)', '1536 dimensions']) } },
 { name: `Brain`, x: 30, y: 470, symbolSize: 28, category: 4,
 tooltip: { formatter: () => tt(`COMPANY BRAIN — ${m.brain_entries || 0} ENTRIES`, ['Formulas, glossary, aliases', 'Patterns, org structure', '3 scopes: global/project/personal', 'Cohere reranking']) } },
 { name: `KG`, x: 110, y: 470, symbolSize: 28, category: 4,
 tooltip: { formatter: () => tt(`KNOWLEDGE GRAPH — ${m.kg_triples || 0} TRIPLES`, ['SPO triple extraction', 'Entity standardization (fuzzy match)', 'Cross-source inference', 'Community detection (BFS)']) } },
 { name: 'Reranker', x: 30, y: 540, symbolSize: 22, category: 4,
 tooltip: { formatter: () => tt('COHERE RERANKING CASCADE', (arch.knowledge?.rerank_cascade || []).map((r: string, i: number) => `${i+1}. ${r}`)) } },

 // ─── BACKGROUND + LEARNING ───
 { name: '11 Background', x: 400, y: 530, symbolSize: 35, symbol: 'roundRect', category: 6,
 tooltip: { formatter: () => tt('11 BACKGROUND AGENTS — EVERY CHAT', ['Judge: Quality score (1-5)', 'Rule Suggester: Extract rules', 'KG Extractor: 3-10 triples', 'Auto-Memory: Save facts', 'Meta Learner: Track corrections', 'Auto Evolver: Every 20 chats', 'User Prefs + Episodic Memory', 'Proactive Insights + Follow-ups']) } },
 { name: 'Self-Learning', x: 250, y: 590, symbolSize: 35, symbol: 'roundRect', category: 6,
 tooltip: { formatter: () => tt('SELF-LEARNING LOOP', [`${m.memories || 0} memories`, `${m.feedback || 0} feedback entries`, `Avg quality: ${m.quality_avg || 0}`, 'Auto-evolve every 20 chats', 'ML retrain every 24h', 'Self-correction: 3 attempts']) } },

 // ─── DATA PIPELINE ───
 { name: 'Upload', x: 770, y: 20, symbolSize: 38, symbol: 'roundRect', category: 3,
 tooltip: { formatter: () => tt('DATA INGESTION — 18 FORMATS', ['CSV, Excel, JSON, SQL, PPTX, DOCX', 'PDF, MD, TXT, images (JPG/PNG/etc)', 'Excel: 5-layer pipeline', 'Vision OCR + contextual enrichment', 'Encoding detection (chardet)']) } },
 { name: 'Training', x: 770, y: 210, symbolSize: 35, symbol: 'roundRect', category: 3,
 tooltip: { formatter: () => tt('TRAINING PIPELINE — 14 STEPS', ['1. Drift check', '2. Deep analysis (Codex)', '3. Q&A generation', '4. Persona creation', '5. Workflows', '6. Relationships', '7. Knowledge index', '8. Brain fill (7 sub-steps)', '9. Domain knowledge (6 sub-steps)', '10-14. Seed, enrich, facts, KG, ML']) } },
 { name: 'Connectors', x: 620, y: 20, symbolSize: 28, category: 3,
 tooltip: { formatter: () => tt('DATA CONNECTORS', (arch.pipeline?.connectors || []).map((c: string) => `› ${c}`).concat(['', 'Live query on remote DBs', 'SSE streaming sync', 'Change detection'])) } },

 // ─── EXPORT ───
 { name: 'Export', x: 530, y: 590, symbolSize: 30, symbol: 'roundRect', category: 7,
 tooltip: { formatter: () => tt('EXPORT', (arch.pipeline?.export || []).map((e: string) => `› ${e}`).concat(['', 'Conversation-to-report', '8 PPTX design themes', 'Excel: 4 sheets + charts'])) } },

 // ─── OUTPUT ───
 { name: 'SSE Stream', x: 400, y: 640, symbolSize: 32, symbol: 'roundRect', category: 0,
 tooltip: { formatter: () => tt('SSE STREAM → BROWSER', ['ToolCallStarted → Completed', 'Content streaming (5-min timeout)', 'ML cards with badges', 'KPI cards, charts, tables', 'SOURCES tab, inline charts']) } },

 // ─── AI MODELS (right side) ───
 { name: `Chat Model`, x: 140, y: 160, symbolSize: 25, category: 0,
 tooltip: { formatter: () => tt('CHAT MODEL', [`${md.chat}`, 'Chat agents, SQL, vision, Q&A', 'Dashboard generation']) } },
 { name: `Deep Model`, x: 140, y: 210, symbolSize: 25, category: 6,
 tooltip: { formatter: () => tt('DEEP MODEL', [`${md.deep}`, 'Analysis, relationships, domain', 'ML predictions, auto-evolve']) } },
 { name: `Lite Model`, x: 140, y: 260, symbolSize: 25, category: 3,
 tooltip: { formatter: () => tt('LITE MODEL', [`${md.lite}`, 'Scoring, routing, extraction', 'Meta-learning, mining']) } },
 ];

 const links: any[] = [
 // Main request flow (thick, animated)
 { source: 'Users', target: 'Caddy', lineStyle: { width: 3 } },
 { source: 'Caddy', target: 'FastAPI', lineStyle: { width: 3 } },
 { source: 'FastAPI', target: 'Router', lineStyle: { width: 2.5 } },
 { source: 'Router', target: 'Leader', lineStyle: { width: 2.5 } },

 // DB connections
 { source: 'FastAPI', target: 'PgBouncer', lineStyle: { width: 2, type: 'dashed' } },
 { source: 'PgBouncer', target: 'PostgreSQL', lineStyle: { width: 2, type: 'dashed' } },

 // Agent delegation
 { source: 'Leader', target: 'Analyst' },
 { source: 'Leader', target: 'Researcher' },
 { source: 'Leader', target: 'Engineer' },
 { source: 'Analyst', target: 'Specialists', lineStyle: { type: 'dashed' } },

 // Knowledge connections
 { source: 'Analyst', target: '13 Layers', lineStyle: { color: '#06b6d4' } },
 { source: 'Researcher', target: '13 Layers', lineStyle: { color: '#06b6d4' } },
 { source: '13 Layers', target: 'PgVector KB', lineStyle: { color: '#06b6d4' } },
 { source: '13 Layers', target: 'Brain', lineStyle: { color: '#06b6d4' } },
 { source: '13 Layers', target: 'KG', lineStyle: { color: '#06b6d4' } },
 { source: 'PgVector KB', target: 'Reranker', lineStyle: { color: '#06b6d4', type: 'dashed' } },
 { source: 'Brain', target: 'Reranker', lineStyle: { color: '#06b6d4', type: 'dashed' } },

 // Background + learning loop
 { source: 'Leader', target: '11 Background', lineStyle: { color: '#ec4899' } },
 { source: '11 Background', target: 'Self-Learning', lineStyle: { color: '#ec4899' } },
 { source: 'Self-Learning', target: '13 Layers', lineStyle: { color: '#ec4899', type: 'dashed' } },

 // Data pipeline
 { source: 'Connectors', target: 'Upload', lineStyle: { color: '#f59e0b' } },
 { source: 'Upload', target: 'FastAPI', lineStyle: { color: '#f59e0b' } },
 { source: 'Upload', target: 'Training', lineStyle: { color: '#f59e0b' } },
 { source: 'Training', target: 'PostgreSQL', lineStyle: { color: '#f59e0b', type: 'dashed' } },

 // Export + output
 { source: 'Engineer', target: 'Export', lineStyle: { color: '#84cc16' } },
 { source: 'Leader', target: 'SSE Stream', lineStyle: { width: 2.5 } },
 { source: 'SSE Stream', target: 'Users', lineStyle: { width: 2.5, type: 'dashed' } },

 // Model connections
 { source: 'Chat Model', target: 'FastAPI', lineStyle: { type: 'dotted', color: '#666' } },
 { source: 'Deep Model', target: 'FastAPI', lineStyle: { type: 'dotted', color: '#666' } },
 { source: 'Lite Model', target: 'FastAPI', lineStyle: { type: 'dotted', color: '#666' } },
 ];

 archChart.setOption({
 backgroundColor: 'transparent',
 tooltip: { trigger: 'item', backgroundColor: '#ffffff', borderColor: '#d6cfc1', borderWidth: 1, padding: 12,
 textStyle: { color: '#2c2a26', fontSize: 11, fontFamily: 'var(--pw-font-body)' },
 extraCssText: 'max-width: 320px; white-space: normal; box-shadow: 0 4px 12px rgba(0,0,0,0.08);' },
 legend: { data: cats.map(c => c.name), bottom: 4, textStyle: { color: '#6b675f', fontSize: 9, fontFamily: 'var(--pw-font-body)' }, itemWidth: 10, itemHeight: 10, itemGap: 14 },
 animationDuration: 1500,
 animationEasingUpdate: 'quinticInOut',
 series: [{
 type: 'graph',
 layout: 'none',
 roam: true,
 zoom: 1.05,
 scaleLimit: { min: 0.5, max: 3 },
 categories: cats,
 nodes: nodes.map(n => ({
 ...n,
 label: { show: true, fontSize: 9, color: '#2c2a26', fontWeight: 'bold',
 fontFamily: 'var(--pw-font-body)',
 formatter: (p: any) => p.name, position: 'inside', lineHeight: 12 },
 itemStyle: { borderWidth: 1.5, borderColor: 'rgba(255,255,255,0.85)', shadowBlur: 6, shadowColor: 'rgba(60,50,30,0.12)' },
 })),
 links: links.map(l => ({
 ...l,
 lineStyle: { color: l.lineStyle?.color || '#b8b0a0', width: l.lineStyle?.width || 1.5, curveness: 0.15, type: l.lineStyle?.type || 'solid', opacity: 0.65 },
 symbol: ['none', 'arrow'], symbolSize: [0, 8],
 })),
 emphasis: {
 focus: 'adjacency',
 lineStyle: { width: 4, opacity: 1, color: 'var(--pw-accent)' },
 itemStyle: { borderWidth: 2.5, borderColor: 'var(--pw-accent, #c97a4a)', shadowBlur: 12, shadowColor: 'rgba(201,122,74,0.25)' },
 label: { fontSize: 11, color: '#1a1815' },
 },
 }],
 });

 if (!_archOnResize) {
 _archOnResize = () => archChart?.resize();
 window.addEventListener('resize', _archOnResize);
 }
 }

 /* ─── INTEGRATIONS state ─── */
 let spAdminConfig = $state<any>({});
 let spAdminSaving = $state(false);
 let spAdminMsg = $state('');
 let spAdminClientId = $state('');
 let spAdminClientSecret = $state('');
 let spAdminTenantId = $state('');
 let spAllSources = $state<any[]>([]);

 let gdAdminClientId = $state('');
 let gdAdminClientSecret = $state('');
 let gdAdminConfig = $state<any>({});
 let gdAdminSaving = $state(false);
 let gdAdminMsg = $state('');
 let dbAllSources = $state<any[]>([]);

 // OneDrive admin config
 let odAdminConfig = $state<any>({});
 let odAdminClientId = $state('');
 let odAdminClientSecret = $state('');
 let odAdminTenantId = $state('common');
 let odAdminSaving = $state(false);
 let odAdminMsg = $state('');

 // DB connector form (admin can connect to any project)
 let dbAdminStep = $state<'idle' | 'form' | 'tables'>('idle');
 let dbAdminType = $state('postgresql');
 let dbAdminHost = $state('');
 let dbAdminPort = $state('5432');
 let dbAdminUser = $state('');
 let dbAdminPass = $state('');
 let dbAdminName = $state('');
 let dbAdminProject = $state('');
 let dbAdminTesting = $state(false);
 let dbAdminTestResult = $state<any>(null);
 let dbAdminTables = $state<any[]>([]);
 let dbAdminSelectedTables = $state<string[]>([]);
 let dbAdminConnecting = $state(false);
 let dbAdminMsg2 = $state('');

 async function dbAdminTest() {
 dbAdminTesting = true; dbAdminTestResult = null;
 try {
 const r = await fetch('/api/connectors/test', {
 method: 'POST', headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ host: dbAdminHost, port: parseInt(dbAdminPort), username: dbAdminUser, password: dbAdminPass, database: dbAdminName, db_type: dbAdminType })
 });
 dbAdminTestResult = await r.json();
 if (dbAdminTestResult.tables) { dbAdminTables = dbAdminTestResult.tables; dbAdminStep = 'tables'; }
 } catch { dbAdminTestResult = { error: 'Connection failed' }; }
 dbAdminTesting = false;
 }
 async function dbAdminConnect() {
 if (!dbAdminProject) { dbAdminMsg2 = 'Select a project first'; return; }
 dbAdminConnecting = true; dbAdminMsg2 = '';
 try {
 const r = await fetch('/api/connectors/connect', {
 method: 'POST', headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 project_slug: dbAdminProject, host: dbAdminHost, port: parseInt(dbAdminPort),
 username: dbAdminUser, password: dbAdminPass, database: dbAdminName,
 db_type: dbAdminType, selected_tables: dbAdminSelectedTables, sync_schedule: 'manual',
 })
 });
 if (r.ok) {
 dbAdminMsg2 = `Connected ${dbAdminSelectedTables.length} tables to ${dbAdminProject}!`;
 dbAdminStep = 'idle'; dbAdminHost = ''; dbAdminUser = ''; dbAdminPass = ''; dbAdminName = '';
 dbAdminSelectedTables = []; dbAdminTables = [];
 tabLoaded['integrations'] = false; await loadIntegrations();
 } else { const d = await r.json(); dbAdminMsg2 = d.detail || 'Failed'; }
 } catch { dbAdminMsg2 = 'Failed'; }
 dbAdminConnecting = false;
 }

 /* ─── tab switch loader ─── */
 let tabLoaded = $state<Record<string, boolean>>({});
 let tabData = $state<Record<string, any>>({});

 async function switchTab(id: string) {
 // Legacy redirect for bare governance/agent-os-admin/telemetry-admin
 if (LEGACY_TAB_REDIRECT[id]) id = LEGACY_TAB_REDIRECT[id];
 activeTab = id;
 // Sub-tabs (gov-* / aos-* / tel-*) are handled by panel components themselves
 if (_isSubTab(id)) return;
 if (tabLoaded[id]) return;
 tabLoaded[id] = true;
 loading = true;
 try {
 if (id === 'users') await loadUsers();
 if (id === 'projects') await loadProjects();
 if (id === 'logs') await loadLogs();
 if (id === 'schemas') await loadSchemas();
 if (id === 'chatLogs') await loadChatLogs();
 if (id === 'health') { await loadHealth(); loadDriftStatus(); }
 if (id === 'stats') await loadStats();
 if (id === 'integrations') await loadIntegrations();
 if (id === 'architecture') await loadArchitecture();
 if (id === 'drift') await loadAllDrift();
 if (id === 'branding') await loadBranding();
 if (id === 'fed-admin') await loadFedAdmin();
 if (id === 'channels') await loadChannels();
 if (id === 'mcp') await loadMcp();
 if (id === 'traces') await loadTraces();
 if (id === 'cockpit') await loadCockpit();
 } catch {}
 loading = false;
 }

 /* ─── cockpit (folded-in Super Dashboard landing) ─── */
 let cockpit = $state<any>({});
 // per-section live status for the merged Overview page (●/◐/✗ + latency)
 let secLive = $state<Record<string, { ok: boolean; ms: number }>>({});
 async function loadCockpit() {
   loadAdminSettings();   // for the Integrations on/off toggles
   const _t0 = (typeof performance !== 'undefined') ? performance.now() : 0;
   const j = async (u: string) => { try { const r = await fetch(u, { headers: _h() }); return r.ok ? await r.json() : null; } catch { return null; } };
   const [arch, hl, dm, g] = await Promise.all([
     j('/api/architecture'), j('/api/health'), j('/api/health/daemons'), j('/api/auth/apigw-usage'),
   ]);
   cockpit = { metrics: arch?.metrics || {}, models: arch?.models || {}, health: hl || {}, daemons: dm || {}, gw: g || {} };
   const _ms = () => Math.max(1, Math.round(((typeof performance !== 'undefined') ? performance.now() : 0) - _t0));
   secLive = { ...secLive, health: { ok: (hl?.status === 'ok'), ms: _ms() } };
   // fold in the former Platform-stats + Observability tabs as sections
   try { const s0 = (typeof performance !== 'undefined') ? performance.now() : 0; await loadStats(); secLive = { ...secLive, stats: { ok: !!stats, ms: Math.max(1, Math.round(((typeof performance !== 'undefined') ? performance.now() : 0) - s0)) } }; }
   catch { secLive = { ...secLive, stats: { ok: false, ms: 0 } }; }
   try { const o0 = (typeof performance !== 'undefined') ? performance.now() : 0; await loadTraces(); secLive = { ...secLive, obs: { ok: !traceLoadError, ms: Math.max(1, Math.round(((typeof performance !== 'undefined') ? performance.now() : 0) - o0)) } }; }
   catch { secLive = { ...secLive, obs: { ok: false, ms: 0 } }; }
 }
 // live-status badge snippet helper
 function secBadge(k: string): { dot: string; txt: string } {
   const s = secLive[k];
   if (!s) return { dot: 'cc-sb-load', txt: '…' };
   if (s.ok) return { dot: 'cc-sb-ok', txt: `live · ${s.ms}ms` };
   return { dot: 'cc-sb-down', txt: 'unavailable' };
 }

 /* ═══════════════════════════════════════════════════════════ */
 /* CHANNELS state + loaders */
 /* ═══════════════════════════════════════════════════════════ */
 let chanSub = $state<'slack' | 'email' | 'voice' | 'threads'>('slack');
 let chanSlackWs = $state<any[]>([]);
 let chanSlackRoutes = $state<any[]>([]);
 let chanEmailAcc = $state<any[]>([]);
 let chanVoiceNums = $state<any[]>([]);
 let chanThreads = $state<any[]>([]);
 let chanThreadFilter = $state<string>('all');
 let chanOpenThread = $state<any>(null);
 let chanNewWs = $state<any>({ open: false, team_id: '', team_name: '', bot_token: '', signing_secret: '', default_project_slug: '' });
 let chanNewRoute = $state<any>({ open: false, workspace_id: '', channel_id: '', project_slug: '' });
 let chanNewEmail = $state<any>({ open: false, name: '', inbound_kind: 'imap', imap_host: '', imap_port: 993, imap_user: '', imap_pass: '', smtp_host: '', smtp_port: 587, smtp_user: '', smtp_pass: '', default_project_slug: '' });
 let chanNewVoice = $state<any>({ open: false, phone_number: '', account_sid: '', auth_token: '', default_project_slug: '', tts_voice: 'Rachel' });
 const _ccTok = () => typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 const _ccHdr = () => ({ 'Content-Type': 'application/json', Authorization: `Bearer ${_ccTok() || ''}` });

 async function loadChannels() {
 try {
 const [ws, ea, vn, th] = await Promise.all([
 fetch('/api/channels/slack/workspaces', { headers: _ccHdr() }).then(r => r.ok ? r.json() : null).catch(() => null),
 fetch('/api/channels/email/accounts', { headers: _ccHdr() }).then(r => r.ok ? r.json() : null).catch(() => null),
 fetch('/api/channels/voice/numbers', { headers: _ccHdr() }).then(r => r.ok ? r.json() : null).catch(() => null),
 fetch('/api/channels/threads?limit=50', { headers: _ccHdr() }).then(r => r.ok ? r.json() : null).catch(() => null),
 ]);
 chanSlackWs = ws?.workspaces || [];
 chanEmailAcc = ea?.accounts || [];
 chanVoiceNums = vn?.numbers || [];
 chanThreads = th?.threads || [];
 } catch {}
 }
 async function chanAddWs() {
 const r = await fetch('/api/channels/slack/workspaces', { method: 'POST', headers: _ccHdr(), body: JSON.stringify(chanNewWs) });
 if (r.ok) { chanNewWs = { open: false, team_id: '', team_name: '', bot_token: '', signing_secret: '', default_project_slug: '' }; loadChannels(); } else alert(`Failed: ${(await r.json()).detail || 'error'}`);
 }
 async function chanAddRoute() {
 const r = await fetch('/api/channels/slack/routes', { method: 'POST', headers: _ccHdr(), body: JSON.stringify(chanNewRoute) });
 if (r.ok) { chanNewRoute = { open: false, workspace_id: '', channel_id: '', project_slug: '' }; loadChannels(); }
 }
 async function chanAddEmail() {
 const r = await fetch('/api/channels/email/accounts', { method: 'POST', headers: _ccHdr(), body: JSON.stringify(chanNewEmail) });
 if (r.ok) { chanNewEmail.open = false; loadChannels(); }
 }
 async function chanAddVoice() {
 const r = await fetch('/api/channels/voice/numbers', { method: 'POST', headers: _ccHdr(), body: JSON.stringify(chanNewVoice) });
 if (r.ok) { chanNewVoice.open = false; loadChannels(); }
 }
 async function chanViewThread(id: string) {
 const r = await fetch(`/api/channels/threads/${id}`, { headers: _ccHdr() });
 chanOpenThread = r.ok ? await r.json() : null;
 }

 /* ═══════════════════════════════════════════════════════════ */
 /* MCP state + loaders */
 /* ═══════════════════════════════════════════════════════════ */
 let mcpSub = $state<'servers' | 'bindings' | 'invocations'>('servers');
 let mcpServers = $state<any[]>([]);
 let mcpSelectedServer = $state<string>('');
 let mcpBindings = $state<any[]>([]);
 let mcpInvocations = $state<any[]>([]);
 let mcpInvDays = $state(7);
 let mcpNew = $state<any>({ open: false, name: '', transport: 'http', url: '', command: '', auth_header: '' });

 async function loadMcp() {
 try {
 const r = await fetch('/api/mcp/servers', { headers: _ccHdr() });
 mcpServers = r.ok ? (await r.json()).servers || [] : [];
 if (mcpServers.length && !mcpSelectedServer) mcpSelectedServer = mcpServers[0].id;
 await loadMcpInv();
 } catch {}
 }
 async function loadMcpBindings() {
 if (!mcpSelectedServer) { mcpBindings = []; return; }
 const r = await fetch(`/api/mcp/servers/${mcpSelectedServer}/bindings`, { headers: _ccHdr() });
 mcpBindings = r.ok ? (await r.json()).bindings || [] : [];
 }
 async function loadMcpInv() {
 const r = await fetch(`/api/mcp/invocations?days=${mcpInvDays}&limit=200`, { headers: _ccHdr() });
 mcpInvocations = r.ok ? (await r.json()).invocations || [] : [];
 }
 async function mcpAdd() {
 const r = await fetch('/api/mcp/servers', { method: 'POST', headers: _ccHdr(), body: JSON.stringify(mcpNew) });
 if (r.ok) { mcpNew = { open: false, name: '', transport: 'http', url: '', command: '', auth_header: '' }; loadMcp(); }
 else alert('Add failed');
 }
 async function mcpTest(id: string) {
 const r = await fetch(`/api/mcp/servers/${id}/test`, { method: 'POST', headers: _ccHdr() });
 const j = await r.json();
 alert(j.ok ? `Connected! ${j.tool_count} tools.` : `Failed: ${j.error || 'error'}`);
 loadMcp();
 }
 async function mcpDelete(id: string) {
 if (!(await confirmDelete({ itemName: id, itemType: 'MCP server' }))) return;
 await fetch(`/api/mcp/servers/${id}?hard=1`, { method: 'DELETE', headers: _ccHdr() });
 loadMcp();
 }
 async function mcpSaveBindings() {
 const payload = { bindings: mcpBindings };
 await fetch(`/api/mcp/servers/${mcpSelectedServer}/bindings`, { method: 'PATCH', headers: _ccHdr(), body: JSON.stringify(payload) });
 alert('Bindings saved.');
 }

 /* ═══════════════════════════════════════════════════════════ */
 /* TRACES / OBSERVABILITY state + loaders */
 /* ═══════════════════════════════════════════════════════════ */
 let traces = $state<any[]>([]);
 let traceRollup = $state<any>(null);
 let traceCrons = $state<any[]>([]);
 let traceAgents = $state<any[]>([]);
 let traceKind = $state<string>('');
 let traceDays = $state<number>(1);
 let traceExpanded = $state<string | null>(null);
 let traceLoadError = $state<boolean>(false);

 async function loadTraces() {
 traceLoadError = false;
 const qs = new URLSearchParams();
 if (traceKind) qs.set('kind', traceKind);
 qs.set('days', String(traceDays));
 qs.set('limit', '200');
 try {
 const [tr, cr, ag] = await Promise.all([
 fetch(`/api/admin/traces?${qs.toString()}`, { headers: _ccHdr() }).then(r => r.ok ? r.json() : null).catch(() => null),
 fetch('/api/admin/traces/cron-health', { headers: _ccHdr() }).then(r => r.ok ? r.json() : null).catch(() => null),
 fetch(`/api/admin/traces/agents?days=${Math.max(traceDays, 7)}`, { headers: _ccHdr() }).then(r => r.ok ? r.json() : null).catch(() => null),
 ]);
 if (!tr || tr.error) { traceLoadError = true; traces = []; traceRollup = null; }
 else {
 traces = Array.isArray(tr.traces) ? tr.traces : [];
 traceRollup = tr.rollup || null;
 }
 traceCrons = (cr && Array.isArray(cr.crons)) ? cr.crons : [];
 traceAgents = (ag && Array.isArray(ag.agents)) ? ag.agents : [];
 } catch {
 traceLoadError = true;
 traces = []; traceRollup = null; traceCrons = []; traceAgents = [];
 }
 }

 async function reloadTraces() {
 tabLoaded['traces'] = true;
 loading = true;
 try { await loadTraces(); } catch {}
 loading = false;
 }

 function traceTime(d: any): string {
 if (!d) return '-';
 try { return new Date(d).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
 catch { return String(d).slice(0, 16); }
 }
 function traceDur(ms: any): string {
 const n = Number(ms);
 if (!isFinite(n) || n <= 0) return '-';
 if (n < 1000) return `${Math.round(n)}ms`;
 return `${(n / 1000).toFixed(1)}s`;
 }
 function traceCost(c: any): string {
 const n = Number(c);
 if (!isFinite(n) || n <= 0) return '$0';
 return `$${n.toFixed(n < 1 ? 4 : 2)}`;
 }
 function traceStatusGlyph(s: string): string {
 return s === 'done' ? '✓' : s === 'error' ? '✗' : s === 'running' ? '◐' : s === 'skipped' ? '⊘' : '·';
 }
 function traceStatusColor(s: string): string {
 return s === 'done' ? 'var(--pw-accent)' : s === 'error' ? '#c0392b' : s === 'running' ? '#a06000' : 'var(--pw-muted)';
 }
 function traceKindColor(k: string): string {
 const m: Record<string, string> = { training: '#a06000', chat: 'var(--pw-accent)', cron: '#5a7d9a', learning: '#7a5a9a', ml: '#3a8a6a', task: 'var(--pw-muted)' };
 return m[k] || 'var(--pw-muted)';
 }

 // Read ?tab= from URL on mount + sync on changes (guard against initial overwrite)
 let _tabInited = $state(false);

 const LEGACY_TAB_REDIRECT: Record<string,string> = {
   'governance': 'gov-overview',
   'agent-os-admin': 'aos-overview',
   'telemetry-admin': 'tel-overview',
   // Overview merge: Cockpit/Stats/Health/Observability folded into one page.
   'stats': 'cockpit',
   'health': 'cockpit',
   'observability': 'cockpit',
 };

 function _isSubTab(id: string): boolean {
   return id.startsWith('gov-') || id.startsWith('aos-') || id.startsWith('tel-');
 }

 function _initialTab(): string {
 if (typeof window === 'undefined') return 'cockpit';
 const t = new URL(window.location.href).searchParams.get('tab');
 if (t && LEGACY_TAB_REDIRECT[t]) return LEGACY_TAB_REDIRECT[t];
 if (t && _isSubTab(t)) return t;
 return t && tabMeta[t] ? t : 'cockpit';
 }

 $effect(() => {
 if (!_tabInited) return; // skip until onMount sets initial tab
 if (typeof window === 'undefined') return;
 const url = new URL(window.location.href);
 if (url.searchParams.get('tab') !== activeTab) {
 url.searchParams.set('tab', activeTab);
 window.history.replaceState({}, '', url);
 }
 });

 onMount(() => {
 // Product flag — hide multi-project grids in single-agent mode
 fetch('/api/flags').then(r => r.ok ? r.json() : null).then(f => { if (f) singleAgent = !!f.single_agent; }).catch(() => {});
 let initial = _initialTab();
 // If a deep-link landed on a hidden grid, fall back to stats
 if (initial === 'projects' || initial === 'schemas') initial = 'stats';
 switchTab(initial);
 _tabInited = true;
 loadAdminSettings();
 });

 /* ═══════════════════════════════════════════════════════════ */
 /* DATA LOADERS */
 /* ═══════════════════════════════════════════════════════════ */

 async function loadUsers() {
 try {
 const res = await fetch('/api/auth/users', { headers: _ccHdr() });
 if (res.ok) { const d = await res.json(); users = d.users || []; }
 } catch {}
 }

 async function toggleUserDetail(userId: string, row?: any) {
 // Open right drawer instead of inline expand
 if (drawerUserId === userId) { closeUserDrawer(); return; }
 drawerUserId = userId;
 drawerUserRow = row || null;
 userDetail = null;
 loadingUserDetail = true;
 try {
 const res = await fetch(`/api/auth/admin/user/${userId}/detail`, { headers: _ccHdr() });
 if (res.ok) userDetail = await res.json();
 } catch {}
 loadingUserDetail = false;
 }
 function closeUserDrawer() { drawerUserId = null; drawerUserRow = null; userDetail = null; }
 function openChatDrawer(c: any) {
 if (drawerChatId === c.session_id) { closeChatDrawer(); return; }
 drawerChatId = c.session_id;
 drawerChatRow = c;
 }
 function closeChatDrawer() { drawerChatId = null; drawerChatRow = null; }
 function fmtDuration(start: any, end: any): string {
 if (!start || !end) return '—';
 try {
 const ms = new Date(end).getTime() - new Date(start).getTime();
 if (!ms || ms < 0) return '—';
 const s = Math.floor(ms / 1000);
 if (s < 60) return `${s}s`;
 const m = Math.floor(s / 60);
 if (m < 60) return `${m}m`;
 const h = Math.floor(m / 60);
 return `${h}h ${m % 60}m`;
 } catch { return '—'; }
 }
 function chatStatus(c: any): { active: boolean; label: string } {
 const last = c.updated_at || c.last_message_at || c.created_at;
 if (!last) return { active: false, label: 'idle' };
 try {
 const ageMs = Date.now() - new Date(last).getTime();
 return ageMs < 15 * 60 * 1000 ? { active: true, label: 'active' } : { active: false, label: 'idle' };
 } catch { return { active: false, label: 'idle' }; }
 }
 let _archOnResize: (() => void) | null = null;
 const _ccEscHandler = (e: KeyboardEvent) => {
 if (e.key === 'Escape') {
 if (drawerUserId) closeUserDrawer();
 if (drawerChatId) closeChatDrawer();
 }
 };
 if (typeof window !== 'undefined') {
 window.addEventListener('keydown', _ccEscHandler);
 }
 onDestroy(() => {
 if (typeof window !== 'undefined') {
 try { window.removeEventListener('keydown', _ccEscHandler); } catch {}
 if (_archOnResize) { try { window.removeEventListener('resize', _archOnResize); } catch {} _archOnResize = null; }
 }
 });

 async function createUser() {
 if (!newUsername || !newPassword) return;
 try {
 const params = new URLSearchParams({ username: newUsername, password: newPassword, email: newEmail });
 const res = await fetch(`/api/auth/users/create?${params}`, { method: 'POST', headers: _ccHdr() });
 if (res.ok) { showCreateUser = false; newUsername = ''; newPassword = ''; newEmail = ''; tabLoaded['users'] = false; await loadUsers(); }
 else { const d = await res.json(); alert(d.detail || 'Failed'); }
 } catch {}
 }

 async function deleteUser(username: string) {
 if (!(await confirmDelete({ itemName: username, itemType: 'user (and ALL their projects/data)' }))) return;
 try { await fetch(`/api/auth/users/${username}`, { method: 'DELETE', headers: _ccHdr() }); expandedUserId = null; closeUserDrawer(); await loadUsers(); } catch {}
 }

 async function resetUserPassword() {
 if (!resetUser || !resetPass) return;
 resetMsg = '';
 try {
 const res = await fetch(`/api/auth/users/${resetUser}/reset-password?new_password=${encodeURIComponent(resetPass)}`, { method: 'POST', headers: _ccHdr() });
 resetMsg = res.ok ? 'Password reset!' : 'Failed';
 resetPass = '';
 } catch { resetMsg = 'Failed'; }
 }

 async function loadProjects() {
 try {
 const res = await fetch('/api/auth/admin/projects', { headers: _ccHdr() });
 if (res.ok) { const d = await res.json(); projects = d.projects || d || []; }
 } catch {}
 }

 async function toggleProjectDetail(slug: string) {
 if (expandedProjectSlug === slug) { expandedProjectSlug = null; projectDetail = null; return; }
 expandedProjectSlug = slug;
 projectDetail = null;
 loadingProjectDetail = true;
 try {
 const res = await fetch(`/api/projects/${slug}/detail`, { headers: _ccHdr() });
 if (res.ok) projectDetail = await res.json();
 } catch {}
 loadingProjectDetail = false;
 }

 async function loadLogs() {
 try {
 const res = await fetch('/api/auth/admin/audit-log', { headers: _ccHdr() });
 if (res.ok) { const d = await res.json(); logs = d.logs || d.entries || d || []; }
 } catch {}
 }

 async function loadSchemas() {
 try {
 const res = await fetch('/api/auth/admin/schemas', { headers: _ccHdr() });
 if (res.ok) { const d = await res.json(); schemas = d.schemas || d || []; }
 } catch {}
 }

 async function loadChatLogs() {
 try {
 let url = '/api/auth/admin/chat-logs?limit=50';
 if (chatFilterUser) url += `&user=${encodeURIComponent(chatFilterUser)}`;
 if (chatFilterProject) url += `&project=${encodeURIComponent(chatFilterProject)}`;
 const res = await fetch(url, { headers: _ccHdr() });
 if (res.ok) { const d = await res.json(); chatLogs = d.sessions || d.logs || d || []; }
 } catch {}
 }

 async function loadHealth() {
 try {
 const res = await fetch('/api/auth/admin/health', { headers: _ccHdr() });
 if (res.ok) health = await res.json();
 } catch {}
 // Image info (Issue #22 — surface stale containers to admins)
 try {
 const r2 = await fetch('/api/admin/image/info', { headers: _ccHdr() });
 if (r2.ok) imageInfo = await r2.json();
 } catch {}
 }

 let imageInfo: any = null;

 async function loadStats() {
 try {
 const res = await fetch('/api/auth/admin/stats', { headers: _ccHdr() });
 if (res.ok) stats = await res.json();
 } catch {}
 }

 async function loadIntegrations() {
 try {
 const res = await fetch('/api/sharepoint/admin/config', { headers: _ccHdr() });
 if (res.ok) {
 const d = await res.json();
 spAdminConfig = d;
 spAdminClientId = d.client_id || '';
 spAdminClientSecret = '';
 spAdminTenantId = d.tenant_id || '';
 spAllSources = d.sources || [];
 }
 } catch {}
 try {
 const res = await fetch('/api/gdrive/admin/config', { headers: _ccHdr() });
 if (res.ok) {
 const d = await res.json();
 gdAdminConfig = d;
 gdAdminClientId = d.client_id || '';
 gdAdminClientSecret = '';
 }
 } catch {}
 try {
 const res = await fetch('/api/onedrive/admin/config', { headers: _ccHdr() });
 if (res.ok) {
 const d = await res.json();
 odAdminConfig = d;
 odAdminClientId = d.ms_client_id || d.client_id || '';
 odAdminClientSecret = '';
 odAdminTenantId = d.ms_tenant_id || d.tenant_id || 'common';
 }
 } catch {}
 try {
 const res = await fetch('/api/connectors/admin/sources', { headers: _ccHdr() });
 if (res.ok) {
 const d = await res.json();
 dbAllSources = d.sources || d || [];
 }
 } catch {}
 // Load projects for DB connector project picker
 if (!projects.length) await loadProjects();
 }

 async function saveSharePointConfig() {
 spAdminSaving = true; spAdminMsg = '';
 try {
 const res = await fetch('/api/sharepoint/admin/config', {
 method: 'POST', headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 client_id: spAdminClientId,
 client_secret: spAdminClientSecret,
 tenant_id: spAdminTenantId,
 })
 });
 if (res.ok) {
 spAdminMsg = 'Saved! Restart Docker for changes to take effect.';
 spAdminClientSecret = '';
 } else {
 const d = await res.json();
 spAdminMsg = d.detail || 'Failed to save';
 }
 } catch { spAdminMsg = 'Failed to save'; }
 spAdminSaving = false;
 }

 async function saveOneDriveConfig() {
 odAdminSaving = true; odAdminMsg = '';
 try {
 const res = await fetch('/api/onedrive/admin/config', {
 method: 'POST', headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 ms_client_id: odAdminClientId,
 ms_client_secret: odAdminClientSecret,
 ms_tenant_id: odAdminTenantId || 'common',
 })
 });
 if (res.ok) {
 odAdminMsg = 'Saved! Restart Docker for changes to take effect.';
 odAdminClientSecret = '';
 odAdminConfig = { ...odAdminConfig, configured: true, has_secret: true };
 } else {
 let d: any = {}; try { d = await res.json(); } catch {}
 odAdminMsg = d.detail || 'Failed to save';
 }
 } catch (e: any) { odAdminMsg = e?.message || 'Failed to save'; }
 odAdminSaving = false;
 }

 async function saveGDriveConfig() {
 gdAdminSaving = true; gdAdminMsg = '';
 try {
 const res = await fetch('/api/gdrive/admin/config', {
 method: 'POST', headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify({
 client_id: gdAdminClientId,
 client_secret: gdAdminClientSecret,
 })
 });
 if (res.ok) {
 gdAdminMsg = 'Saved! Restart Docker for changes to take effect.';
 gdAdminClientSecret = '';
 } else {
 const d = await res.json();
 gdAdminMsg = d.detail || 'Failed to save';
 }
 } catch { gdAdminMsg = 'Failed to save'; }
 gdAdminSaving = false;
 }

 /* ─── helpers ─── */
 function fmtDate(d: string | null | undefined): string {
 if (!d) return '-';
 try { return new Date(d).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }); }
 catch { return d.slice(0, 19); }
 }

 function filteredLogs() {
 let out = logs;
 if (logFilterAction) out = out.filter((l: any) => (l.action || l.event || '').toLowerCase().includes(logFilterAction.toLowerCase()));
 if (logFilterUser) out = out.filter((l: any) => (l.username || l.user || l.user_id || '').toLowerCase().includes(logFilterUser.toLowerCase()));
 if (logFilterProject) out = out.filter((l: any) => (l.project || l.slug || l.project_slug || '').toLowerCase().includes(logFilterProject.toLowerCase()));
 return out;
 }
</script>

<div class="cc-shell">
  <aside class="cc-rail">
    {#each railGroups as g}
      {#if g.items.filter((i) => tabMeta[i]).length}
      <div class="cc-rail-group">
        <div class="rail-group-label">{g.label}</div>
        {#each g.items as id}
          {@const meta = tabMeta[id]}
          {#if meta}
            <button class="cc-rail-btn" class:active={activeTab === id} onclick={() => switchTab(id)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                {#if id === 'cockpit'}<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2" fill="currentColor"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>
                {:else if id === 'gateway'}<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>
                {:else if id === 'embed'}<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
                {:else if id === 'brain'}<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><line x1="7.5" y1="7.5" x2="10.5" y2="16"/><line x1="16.5" y1="7.5" x2="13.5" y2="16"/><line x1="8.5" y1="6" x2="15.5" y2="6"/>
                {:else if id === 'auth'}<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                {:else if id === 'users'}<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>
                {:else if id === 'projects'}<path d="M3 7h18M3 12h18M3 17h18"/>
                {:else if id === 'logs'}<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/>
                {:else if id === 'schemas'}<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>
                {:else if id === 'chatLogs'}<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                {:else if id === 'health'}<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
                {:else if id === 'observability'}<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/>
                {:else if id === 'stats'}<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
                {:else if id === 'integrations'}<path d="M16 3h5v5M21 3l-7 7M8 21H3v-5M3 21l7-7"/>
                {:else if id === 'architecture'}<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><path d="M9 6h6M6 9v6M18 9v6"/>
                {:else if id === 'branding'}<circle cx="12" cy="12" r="9"/><path d="M12 3a14 14 0 0 1 0 18M3 12h18"/>
                {:else if id === 'drift'}<path d="M3 12h4l3-8 4 16 3-8h4"/>
                {:else if id === 'fed-admin'}<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18"/>
                {:else if id === 'admin-settings'}<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
                {:else if id === 'traces'}<path d="M3 3v18h18"/><path d="M7 14l3-4 3 3 4-6"/><circle cx="7" cy="14" r="1"/><circle cx="10" cy="10" r="1"/><circle cx="13" cy="13" r="1"/><circle cx="17" cy="7" r="1"/>
                {:else if id === 'channels'}<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
                {:else if id === 'mcp'}<path d="M9 2v6M15 2v6M12 17.5V14M5 17h14a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2zM12 22a4 4 0 0 0 4-4v-1H8v1a4 4 0 0 0 4 4z"/>
                {:else if id === 'llm'}<rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4M8 16h.01M16 16h.01"/>
                {:else if id === 'connectors'}<path d="M9 2v6M15 2v6M7 8h10v4a5 5 0 0 1-5 5 5 5 0 0 1-5-5V8zM12 17v5"/>
                {:else if id === 'accuracy'}<polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/>
                {:else if id === 'golden'}<polygon points="12 2 15 9 22 9 17 14 19 21 12 17 5 21 7 14 2 9 9 9"/>
                {:else if id === 'scope-audit'}<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16" y2="16"/><line x1="8" y1="11" x2="14" y2="11"/>
                {:else if id === 'dataview'}<rect x="3" y="4" width="18" height="16" rx="1"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="14" x2="21" y2="14"/><line x1="9" y1="4" x2="9" y2="20"/>
                {:else if id === 'packs'}<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>
                {/if}
              </svg>
              <span>{meta.label}</span>
            </button>
          {/if}
        {/each}
      </div>
      {/if}
    {/each}
  </aside>

  <main class="cc-main">

    {#if tabMeta[activeTab]}
      <div class="ds-section-head cc-head">
        <div>
          <h1 class="ds-section-title cc-title">{tabMeta[activeTab].label}</h1>
          <p class="ds-section-sub cc-subtitle">{tabMeta[activeTab].subtitle}</p>
        </div>
      </div>
    {:else if _isSubTab(activeTab)}
      {@const _parentLabel = activeTab.startsWith('gov-') ? 'Governance' : activeTab.startsWith('aos-') ? 'Agent OS' : 'Telemetry'}
      {@const _subList = activeTab.startsWith('gov-') ? govSubs : activeTab.startsWith('aos-') ? aosSubs : telSubs}
      {@const _subLabel = (_subList.find((x: any) => x.id === activeTab)?.label) || activeTab}
      <div class="ds-section-head cc-head">
        <div>
          <h1 class="ds-section-title cc-title">{_parentLabel} · {_subLabel}</h1>
        </div>
      </div>
    {/if}

<div class="cc-panel">

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- USERS TAB                                                  -->
<!-- ═══════════════════════════════════════════════════════════ -->
{#if activeTab === 'users'}
  <div class="cli-terminal" style="margin-bottom: 16px; padding: 8px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash admin --users</span>
      <span style="margin-left: auto; font-size: 11px; opacity: 0.6;">{users.length} users</span>
    </div>
  </div>

  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 16px; font-weight: 900; text-transform: uppercase;">Users</div>
    <button class="send-btn" style="font-size: 10px; padding: 6px 14px;" onclick={() => showCreateUser = true}>+ CREATE USER</button>
  </div>

  {#if loading && users.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">Loading...</div>
  {:else}
    <table class="data-table" style="width: 100%;">
      <thead><tr>
        <th>USER</th>
        <th>EMAIL</th>
        <th>ROLE</th>
        <th>DEPT</th>
        <th style="text-align: right;">PROJECTS</th>
        <th>STATUS</th>
        <th>AUTH</th>
        <th style="text-align: right;">ACTIONS</th>
      </tr></thead>
      <tbody>
        {#each users as u}
          <tr style="cursor: pointer; {u.is_active === false ? 'opacity: 0.5;' : ''}" class:cc-row-active={drawerUserId === (u.id || u.username)} onclick={() => toggleUserDetail(u.id || u.username, u)}>
            <td>
              <div class="flex items-center gap-2">
                <div style="background: var(--pw-accent-soft); width: 24px; height: 24px; display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 10px; border: 1px solid var(--pw-ink); flex-shrink: 0;">{u.username.charAt(0).toUpperCase()}</div>
                <div>
                  <div style="font-weight: 900;">{u.first_name && u.last_name ? `${u.first_name} ${u.last_name}` : u.username}</div>
                  {#if u.first_name}<div style="font-size: 11px; color: var(--pw-muted);">@{u.username}</div>{/if}
                </div>
              </div>
            </td>
            <td style="font-size: 11px; color: var(--pw-muted);">{u.email || '-'}</td>
            <td>
              {#if u.is_super}<span class="cc-role-pill">super_admin</span>
              {:else}<span class="cc-role-text">user</span>{/if}
            </td>
            <td style="font-size: 11px; color: var(--pw-ink-soft, var(--pw-muted));">{u.department || '-'}</td>
            <td style="text-align: right; font-weight: 600;">{u.project_count || 0}</td>
            <td>
              {#if u.is_active !== false}
                <span class="cc-status"><span class="cc-dot cc-dot-on"></span>active</span>
              {:else}
                <span class="cc-status"><span class="cc-dot cc-dot-off"></span>inactive</span>
              {/if}
            </td>
            <td style="font-size: 11px; color: var(--pw-ink-soft, var(--pw-muted));">{u.auth_provider || 'local'}</td>
            <td style="text-align: right;" onclick={(e: MouseEvent) => e.stopPropagation()}>
              {#if !u.is_super}
                <span class="cc-actions">
                  <button class="cc-link" onclick={() => { resetUser = u.username; resetPass = ''; resetMsg = ''; }}>Reset password</button>
                  <span class="cc-sep">·</span>
                  <button class="cc-link" onclick={async () => {
                    await fetch(`/api/auth/users/${u.username}/toggle-active`, { method: 'POST', headers: _ccHdr() });
                    await loadUsers();
                  }}>{u.is_active !== false ? 'Disable' : 'Enable'}</button>
                  <span class="cc-sep">·</span>
                  <button class="cc-link cc-link-danger" onclick={() => deleteUser(u.username)}>Delete</button>
                </span>
              {/if}
            </td>
          </tr>

        {/each}
      </tbody>
    </table>

    {#if resetUser}
      <div class="ink-border p-3 mt-3" style="background: var(--pw-bg);">
        <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; margin-bottom: 6px;">Reset password: {resetUser}</div>
        <div class="flex gap-2 items-end">
          <input type="password" bind:value={resetPass} placeholder="New password" style="flex: 1; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg-alt);" />
          <button class="send-btn" onclick={resetUserPassword} style="padding: 6px 12px; font-size: 10px; cursor: pointer;">RESET</button>
          <button class="feedback-btn" onclick={() => resetUser = ''} style="padding: 4px 8px; font-size: 10px; cursor: pointer;">CANCEL</button>
        </div>
        {#if resetMsg}<div style="font-size: 11px; margin-top: 6px; color: {resetMsg.includes('reset') ? 'var(--pw-accent)' : 'var(--pw-error)'};">{resetMsg}</div>{/if}
      </div>
    {/if}
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- PROJECTS TAB                                               -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'projects'}
  <div class="cli-terminal" style="margin-bottom: 16px; padding: 8px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash admin --projects</span>
      <span style="margin-left: auto; font-size: 11px; opacity: 0.6;">{projects.length} projects</span>
    </div>
  </div>

  {#if loading && projects.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">Loading...</div>
  {:else if projects.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No projects found.</div>
  {:else}
    <table class="data-table" style="width: 100%;">
      <thead><tr>
        <th>PROJECT</th>
        <th>OWNER</th>
        <th style="text-align: right;">TABLES</th>
        <th style="text-align: right;">ROWS</th>
        <th>SCHEMA</th>
        <th>TRAINED</th>
        <th>HEALTH</th>
      </tr></thead>
      <tbody>
        {#each projects as p}
          <tr style="cursor: pointer;" onclick={() => toggleProjectDetail(p.slug)}>
            <td>
              <div style="font-weight: 900;">{p.agent_name || p.name || p.slug}</div>
              <div style="font-size: 11px; color: var(--pw-muted);">{p.slug}</div>
            </td>
            <td style="font-size: 11px;">{p.owner || p.username || '-'}</td>
            <td style="text-align: right; font-weight: 900;">{p.tables ?? p.table_count ?? 0}</td>
            <td style="text-align: right;">{(p.rows ?? p.total_rows ?? 0).toLocaleString()}</td>
            <td><span style="font-family: var(--pw-font-body); font-size: 10px;">{p.schema_name || p.schema || '-'}</span></td>
            <td style="font-size: 10px; color: var(--pw-muted);">{fmtDate(p.last_trained || p.trained_at)}</td>
            <td>
              {#if p.health === 'good' || p.health === 'healthy' || p.brain_health?.status === 'good'}
                <span style="color: var(--pw-accent); font-size: 10px; font-weight: 700;">GOOD</span>
              {:else if p.health === 'warning' || p.brain_health?.status === 'warning'}
                <span style="color: orange; font-size: 10px; font-weight: 700;">WARN</span>
              {:else if p.health}
                <span style="color: var(--pw-error); font-size: 10px; font-weight: 700;">{String(p.health).toUpperCase()}</span>
              {:else}
                <span style="font-size: 10px; color: var(--pw-muted);">-</span>
              {/if}
            </td>
          </tr>

          <!-- Expanded project detail row -->
          {#if expandedProjectSlug === p.slug}
            <tr>
              <td colspan="7" style="padding: 0; border: none;">
                <div class="ink-border" style="margin: 8px 0 12px 0; padding: 16px; background: var(--pw-surface);">
                  {#if loadingProjectDetail}
                    <div style="font-size: 11px; color: var(--pw-muted);">Loading project detail...</div>
                  {:else if projectDetail}
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                      <!-- Brain layers -->
                      <div>
                        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; border-bottom: 1px solid var(--pw-ink); padding-bottom: 4px;">Brain Layers</div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px;">
                          {#each [
                            { label: 'Vectors', value: projectDetail.knowledge_vectors ?? 0 },
                            { label: 'Learnings', value: projectDetail.learnings ?? 0 },
                            { label: 'Patterns', value: projectDetail.patterns ?? projectDetail.query_patterns ?? 0 },
                          ] as m}
                            <div class="ink-border" style="padding: 6px; text-align: center; background: var(--pw-bg);">
                              <div style="font-size: 14px; font-weight: 900;">{m.value}</div>
                              <div style="font-size: 11px; text-transform: uppercase;">{m.label}</div>
                            </div>
                          {/each}
                        </div>
                      </div>
                      <!-- Shared users -->
                      <div>
                        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; border-bottom: 1px solid var(--pw-ink); padding-bottom: 4px;">Shared Users</div>
                        {#if projectDetail.shared_users?.length}
                          {#each projectDetail.shared_users as su}
                            <div style="font-size: 11px; margin-bottom: 3px;">{su.username || su} <span class="tag-label" style="font-size: 7px;">{su.role || 'viewer'}</span></div>
                          {/each}
                        {:else}<div style="font-size: 11px; color: var(--pw-muted);">Not shared</div>{/if}
                      </div>
                      <!-- Tables list -->
                      <div>
                        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; border-bottom: 1px solid var(--pw-ink); padding-bottom: 4px;">Tables</div>
                        {#if projectDetail.tables?.length}
                          {#each projectDetail.tables as t}
                            <div style="font-size: 11px; margin-bottom: 3px; font-weight: 700;">{t.name || t} <span style="font-weight: 400; color: var(--pw-muted);">{t.rows != null ? `${t.rows.toLocaleString()} rows` : ''} {t.columns != null ? `${t.columns} cols` : ''}</span></div>
                          {/each}
                        {:else}<div style="font-size: 11px; color: var(--pw-muted);">No tables</div>{/if}
                      </div>
                      <!-- Recent chats -->
                      <div>
                        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; border-bottom: 1px solid var(--pw-ink); padding-bottom: 4px;">Recent Chats</div>
                        {#if projectDetail.recent_chats?.length}
                          {#each projectDetail.recent_chats.slice(0, 5) as c}
                            <div style="font-size: 11px; margin-bottom: 3px;">{c.message || c.first_message || c.session_id?.slice(0, 12)} <span style="color: var(--pw-muted);">{fmtDate(c.created_at)}</span></div>
                          {/each}
                        {:else}<div style="font-size: 11px; color: var(--pw-muted);">No recent chats</div>{/if}
                      </div>
                    </div>
                  {:else}
                    <div style="font-size: 11px; color: var(--pw-muted);">No detail available.</div>
                  {/if}
                </div>
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- LOGS TAB                                                   -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'logs'}
  <div class="cli-terminal" style="margin-bottom: 16px; padding: 8px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash admin --audit-log</span>
      <span style="margin-left: auto; font-size: 11px; opacity: 0.6;">{filteredLogs().length} entries</span>
    </div>
  </div>

  <!-- Filters -->
  <div class="flex gap-3 mb-4 items-center" style="flex-wrap: wrap;">
    <div>
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">Action</div>
      <input type="text" bind:value={logFilterAction} placeholder="Filter action..." style="border: 2px solid var(--pw-ink); padding: 4px 8px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg); width: 140px;" />
    </div>
    <div>
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">User</div>
      <input type="text" bind:value={logFilterUser} placeholder="Filter user..." style="border: 2px solid var(--pw-ink); padding: 4px 8px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg); width: 140px;" />
    </div>
    <div>
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">Project</div>
      <input type="text" bind:value={logFilterProject} placeholder="Filter project..." style="border: 2px solid var(--pw-ink); padding: 4px 8px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg); width: 140px;" />
    </div>
    <button class="cc-btn-ghost" style="align-self: flex-end;" onclick={() => { tabLoaded['logs'] = false; loadLogs(); }}>REFRESH</button>
  </div>

  {#if filteredLogs().length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No log entries found.</div>
  {:else}
    <table class="data-table" style="width: 100%;">
      <thead><tr>
        <th>ACTION</th>
        <th>USER</th>
        <th>PROJECT</th>
        <th>DETAIL</th>
        <th>TIME</th>
      </tr></thead>
      <tbody>
        {#each filteredLogs() as l}
          <tr>
            <td><span class="tag-label" style="font-size: 11px;">{l.action || l.event || '-'}</span></td>
            <td style="font-size: 11px;">{l.username || l.user || l.user_id || '-'}</td>
            <td style="font-size: 11px;">{l.project || l.slug || l.project_slug || '-'}</td>
            <td style="font-size: 10px; color: var(--pw-muted); max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{l.detail || l.details || l.message || '-'}</td>
            <td style="font-size: 10px; color: var(--pw-muted);">{fmtDate(l.created_at || l.timestamp)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- SCHEMAS TAB                                                -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'schemas'}
  <div class="cli-terminal" style="margin-bottom: 16px; padding: 8px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash admin --schemas</span>
      <span style="margin-left: auto; font-size: 11px; opacity: 0.6;">{schemas.length} schemas</span>
    </div>
  </div>

  {#if loading && schemas.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">Loading...</div>
  {:else if schemas.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No schemas found.</div>
  {:else}
    <table class="data-table" style="width: 100%;">
      <thead><tr>
        <th>SCHEMA</th>
        <th style="text-align: right;">TABLES</th>
        <th>OWNER</th>
      </tr></thead>
      <tbody>
        {#each schemas as s}
          {@const schemaName = s.name || s.schema_name || s.schema || s.schemaname || s.nspname || '—'}
          <tr style="cursor: pointer;" onclick={() => expandedSchema = expandedSchema === schemaName ? null : schemaName}>
            <td>
              <div style="font-family: var(--pw-font-serif, 'Source Serif 4', Georgia, serif); font-size: 14px; font-weight: 600; color: var(--pw-ink); line-height: 1.2;">{schemaName}</div>
              {#if s.project_slug || s.slug}
                <div style="font-size: 10px; color: var(--pw-muted); margin-top: 2px;">{s.project_slug || s.slug}</div>
              {/if}
            </td>
            <td style="text-align: right; font-weight: 600;">{s.tables?.length ?? s.table_count ?? 0}</td>
            <td style="font-size: 11px; color: var(--pw-muted);">{s.owner || s.username || '—'}</td>
          </tr>

          <!-- Expanded schema detail -->
          {#if expandedSchema === schemaName}
            <tr>
              <td colspan="3" style="padding: 0; border: none;">
                <div class="ink-border" style="margin: 8px 0 12px 0; padding: 16px; background: var(--pw-surface);">
                  {#if s.tables?.length}
                    <table class="data-table" style="width: 100%;">
                      <thead><tr>
                        <th>TABLE</th>
                        <th style="text-align: right;">ROWS</th>
                        <th>COLUMNS</th>
                      </tr></thead>
                      <tbody>
                        {#each s.tables as t}
                          <tr>
                            <td style="font-weight: 700;">{t.name || t.table_name}</td>
                            <td style="text-align: right;">{(t.rows ?? t.row_count ?? 0).toLocaleString()}</td>
                            <td style="font-size: 10px; color: var(--pw-muted);">
                              {#if t.columns?.length}
                                {t.columns.map((c: any) => typeof c === 'string' ? c : c.name || c.column_name).join(', ')}
                              {:else}
                                {t.column_count ?? '-'}
                              {/if}
                            </td>
                          </tr>
                        {/each}
                      </tbody>
                    </table>
                  {:else}
                    <div style="font-size: 11px; color: var(--pw-muted);">No tables in schema.</div>
                  {/if}
                </div>
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- CHAT LOGS TAB                                              -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'chatLogs'}
  <div class="cli-terminal" style="margin-bottom: 16px; padding: 8px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash admin --chat-logs</span>
      <span style="margin-left: auto; font-size: 11px; opacity: 0.6;">{chatLogs.length} sessions</span>
    </div>
  </div>

  <!-- Filters -->
  <div class="flex gap-3 mb-4 items-center" style="flex-wrap: wrap;">
    <div>
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">User</div>
      <input type="text" bind:value={chatFilterUser} placeholder="Filter user..." style="border: 2px solid var(--pw-ink); padding: 4px 8px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg); width: 140px;" />
    </div>
    <div>
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">Project</div>
      <input type="text" bind:value={chatFilterProject} placeholder="Filter project..." style="border: 2px solid var(--pw-ink); padding: 4px 8px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg); width: 140px;" />
    </div>
    <button class="send-btn" style="font-size: 11px; padding: 4px 10px; align-self: flex-end; cursor: pointer;" onclick={() => { tabLoaded['chatLogs'] = false; loadChatLogs(); }}>SEARCH</button>
  </div>

  {#if loading && chatLogs.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">Loading...</div>
  {:else if chatLogs.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">No chat sessions found.</div>
  {:else}
    <table class="data-table" style="width: 100%;">
      <thead><tr>
        <th>SESSION</th>
        <th>USER</th>
        <th>PROJECT</th>
        <th>FIRST MESSAGE</th>
        <th style="text-align: right;">MESSAGES</th>
        <th>MODEL</th>
        <th>DURATION</th>
        <th>STATUS</th>
        <th>CREATED</th>
      </tr></thead>
      <tbody>
        {#each chatLogs as c}
          {@const st = chatStatus(c)}
          {@const fm = (c.first_message || c.message || '').toString()}
          <tr style="cursor: pointer;" class:cc-row-active={drawerChatId === c.session_id} onclick={() => openChatDrawer(c)}>
            <td style="font-family: var(--pw-font-body); font-size: 10px;">{c.session_id?.slice(0, 12) || '-'}...</td>
            <td style="font-size: 11px;">{c.user_id || c.username || 'anonymous'}</td>
            <td style="font-size: 11px;">{c.project || c.project_slug || c.slug || '-'}</td>
            <td style="font-size: 11px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{fm.length > 60 ? fm.slice(0, 60) + '…' : (fm || '—')}</td>
            <td style="text-align: right; font-weight: 600;">{c.message_count ?? c.messages?.length ?? '—'}</td>
            <td style="font-size: 10px; color: var(--pw-muted);">{c.chat_model || c.model || '—'}</td>
            <td style="font-size: 10px; color: var(--pw-muted);">{fmtDuration(c.created_at, c.updated_at || c.last_message_at)}</td>
            <td><span class="cc-status"><span class="cc-dot {st.active ? 'cc-dot-on' : 'cc-dot-off'}"></span>{st.label}</span></td>
            <td style="font-size: 10px; color: var(--pw-muted);">{fmtDate(c.created_at)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- HEALTH TAB                                                 -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'health'}
  <div class="cli-terminal" style="margin-bottom: 16px; padding: 8px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash admin --health</span>
      <span style="margin-left: auto; font-size: 11px; opacity: 0.6;">system check</span>
    </div>
  </div>

  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 16px; font-weight: 900; text-transform: uppercase;">System Health</div>
    <button class="cc-btn-ghost" onclick={() => { tabLoaded['health'] = false; loading = true; loadHealth().then(() => { tabLoaded['health'] = true; loading = false; }); }}>REFRESH</button>
  </div>

  {#if loading && !health}
    <div style="font-size: 11px; color: var(--pw-muted);">Checking health...</div>
  {:else if health}
    <!-- Overall status -->
    <div class="ink-border" style="padding: 16px; margin-bottom: 16px; background: var(--pw-surface); text-align: center;">
      <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); margin-bottom: 4px;">Overall Status</div>
      <div style="font-size: 20px; font-weight: 900; text-transform: uppercase; color: {(health.status === 'ok' || health.status === 'healthy') ? 'var(--pw-accent)' : 'var(--pw-error)'};">
        {health.status || 'UNKNOWN'}
      </div>
    </div>

    <!-- Image / build provenance (Issue #22) -->
    {#if imageInfo}
      <div class="ink-border" style="padding: 12px 16px; margin-bottom: 16px; background: var(--pw-surface); display: flex; align-items: center; gap: 16px; flex-wrap: wrap;">
        <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted); font-weight: 700;">Image</div>
        <div style="font-size: 11px; color: var(--pw-ink);">
          {#if imageInfo.image_age_hours != null}
            <strong>Age:</strong> {imageInfo.image_age_hours}h
          {/if}
          {#if imageInfo.built_at}
            · <strong>Built:</strong> {imageInfo.built_at.replace('T',' ').slice(0,19)} UTC
          {/if}
          {#if imageInfo.git_commit && imageInfo.git_commit !== 'unknown'}
            · <strong>Commit:</strong> <code>{imageInfo.git_commit}</code>
          {/if}
          {#if imageInfo.version && imageInfo.version !== 'dev'}
            · <strong>v{imageInfo.version}</strong>
          {/if}
        </div>
        {#if imageInfo.stale_warning}
          <span style="margin-left: auto; padding: 3px 10px; border-radius: 0; background: #fef3c7; color: #92400e; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;"><Icon name="alert-triangle" size={14} /> Stale &gt; 24h</span>
        {/if}
      </div>
    {/if}

    <!-- Service cards -->
    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px;">
      {#if health.services}
        {#each Object.entries(health.services) as [name, svc]}
          <div class="ink-border" style="padding: 14px; background: var(--pw-surface);">
            <div class="flex items-center gap-2 mb-2">
              <span style="font-size: 13px; color: {(svc as any).status === 'ok' || (svc as any).status === 'healthy' || (svc as any).status === 'connected' || (svc as any).up === true ? 'var(--pw-accent)' : 'var(--pw-error)'};">●</span>
              <span style="font-weight: 900; text-transform: uppercase; font-size: 11px;">{name}</span>
            </div>
            <div style="font-size: 11px; color: var(--pw-muted);">
              {#if typeof svc === 'object' && svc !== null}
                {(svc as any).status || ((svc as any).up ? 'UP' : 'DOWN')}
                {#if (svc as any).latency != null}<br/>Latency: {(svc as any).latency}ms{/if}
                {#if (svc as any).version}<br/>v{(svc as any).version}{/if}
              {:else}
                {svc}
              {/if}
            </div>
          </div>
        {/each}
      {:else}
        <!-- Fallback: render health object keys as cards -->
        {#each Object.entries(health).filter(([k]) => k !== 'status') as [key, val]}
          <div class="ink-border" style="padding: 14px; background: var(--pw-surface);">
            <div class="flex items-center gap-2 mb-2">
              <span style="font-size: 13px; color: var(--pw-accent);">●</span>
              <span style="font-weight: 900; text-transform: uppercase; font-size: 11px;">{key}</span>
            </div>
            <div style="font-size: 11px; color: var(--pw-muted);">
              {#if typeof val === 'object' && val !== null}
                {JSON.stringify(val)}
              {:else}
                {val}
              {/if}
            </div>
          </div>
        {/each}
      {/if}
    </div>
  {:else}
    <div style="font-size: 11px; color: var(--pw-muted);">Health data unavailable.</div>
  {/if}

  <!-- MIGRATION DRIFT GATE — fail-soft: hidden when endpoint missing/403 -->
  {#if driftStatus || driftLoading}
    <div class="ink-border" style="padding: 16px; margin-top: 20px; background: var(--pw-surface);">
      <div class="flex items-center justify-between mb-3">
        <div style="font-size: 13px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.05em;">Migration Drift Gate</div>
        <button class="cc-btn-ghost" onclick={loadDriftStatus} disabled={driftLoading}>{driftLoading ? '...' : 'REFRESH'}</button>
      </div>
      {#if driftError === 'malformed'}
        <div style="padding: 10px 14px; background: #fef3c7; color: #92400e; font-size: 12px; font-weight: 600;">Unable to load drift status</div>
      {:else if driftStatus}
        {@const _clean = driftStatus.drift_after_allowlist === 0}
        <div style="display: flex; align-items: center; gap: 14px; margin-bottom: 14px;">
          <span style="padding: 6px 16px; font-size: 14px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.06em; background: {_clean ? '#d1fae5' : '#fee2e2'}; color: {_clean ? '#065f46' : '#991b1b'}; border-radius: 0;">
            {_clean ? '✓ CLEAN' : '✗ DRIFT'}
          </span>
          {#if driftStatus.last_run_at}
            <span style="font-size: 11px; color: var(--pw-muted);">Last run: {_driftRelTime(driftStatus.last_run_at)}</span>
          {/if}
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin-bottom: 14px;">
          <div style="padding: 10px; background: var(--pw-bg-alt); border: 1px solid var(--pw-border);">
            <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Refs scanned</div>
            <div style="font-size: 18px; font-weight: 900; margin-top: 2px;">{driftStatus.refs_scanned ?? '—'}</div>
          </div>
          <div style="padding: 10px; background: var(--pw-bg-alt); border: 1px solid var(--pw-border);">
            <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Migrations parsed</div>
            <div style="font-size: 18px; font-weight: 900; margin-top: 2px;">{driftStatus.migrations_parsed ?? '—'}</div>
          </div>
          <div style="padding: 10px; background: var(--pw-bg-alt); border: 1px solid var(--pw-border);">
            <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Drift (raw)</div>
            <div style="font-size: 18px; font-weight: 900; margin-top: 2px;">{driftStatus.drift_before_allowlist ?? '—'}</div>
          </div>
          <div style="padding: 10px; background: var(--pw-bg-alt); border: 1px solid var(--pw-border);">
            <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Drift (post-allowlist)</div>
            <div style="font-size: 18px; font-weight: 900; margin-top: 2px; color: {_clean ? 'var(--pw-accent)' : 'var(--pw-error)'};">{driftStatus.drift_after_allowlist ?? '—'}</div>
          </div>
          <div style="padding: 10px; background: var(--pw-bg-alt); border: 1px solid var(--pw-border);">
            <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted);">Allowlist entries</div>
            <div style="font-size: 18px; font-weight: 900; margin-top: 2px;">{driftStatus.allowlist_entries ?? '—'}</div>
          </div>
        </div>
        <div style="font-size: 11px; color: var(--pw-muted); line-height: 1.5; padding-top: 10px; border-top: 1px dashed var(--pw-border);">
          Catches code refs to non-existent DB tables at PR time. Run locally: <code style="background: var(--pw-bg-alt); padding: 1px 6px; font-size: 11px;">make check-drift</code>
        </div>
      {:else if driftLoading}
        <div style="font-size: 11px; color: var(--pw-muted);">Loading drift status...</div>
      {/if}
    </div>
  {/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- STATS TAB                                                  -->
<!-- ═══════════════════════════════════════════════════════════ -->
{:else if activeTab === 'stats'}
  <div class="cli-terminal" style="margin-bottom: 16px; padding: 8px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash admin --stats</span>
      <span style="margin-left: auto; font-size: 11px; opacity: 0.6;">platform metrics</span>
    </div>
  </div>

  <div class="flex items-center justify-between mb-4">
    <div style="font-size: 16px; font-weight: 900; text-transform: uppercase;">Platform Stats</div>
    <button class="cc-btn-ghost" onclick={() => { tabLoaded['stats'] = false; loading = true; loadStats().then(() => { tabLoaded['stats'] = true; loading = false; }); }}>REFRESH</button>
  </div>

  {#if loading && !stats}
    <div style="font-size: 11px; color: var(--pw-muted);">Loading stats...</div>
  {:else if stats}
    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px;">
      {#each [
        { label: 'USERS', value: stats.users ?? stats.stats?.users ?? stats.user_count ?? '-' },
        { label: 'PROJECTS', value: stats.projects ?? stats.stats?.projects ?? stats.project_count ?? '-' },
        { label: 'SESSIONS', value: stats.sessions ?? stats.stats?.sessions ?? stats.session_count ?? '-' },
        { label: 'FEEDBACK', value: stats.feedback ?? stats.stats?.feedback ?? stats.feedback_count ?? '-' },
        { label: 'TRAINING RUNS', value: stats.training_runs ?? stats.stats?.training_runs ?? '-' },
        { label: 'DB SIZE', value: stats.db_size ?? stats.stats?.db_size ?? '-' },
        { label: 'TABLES', value: stats.tables ?? stats.stats?.table_count ?? stats.table_count ?? '-' },
        { label: 'TOTAL ROWS', value: typeof (stats.total_rows ?? stats.stats?.total_rows) === 'number' ? (stats.total_rows ?? stats.stats?.total_rows).toLocaleString() : (stats.total_rows ?? stats.stats?.total_rows ?? '-') },
        { label: 'KNOWLEDGE', value: stats.knowledge_vectors ?? stats.stats?.knowledge_vectors ?? '-' },
        { label: 'MEMORIES', value: stats.memories ?? stats.stats?.memories ?? stats.memory_count ?? '-' },
        { label: 'LEARNINGS', value: stats.learnings ?? stats.stats?.learnings ?? '-' },
        { label: 'PATTERNS', value: stats.patterns ?? stats.stats?.patterns ?? stats.query_patterns ?? '-' },
      ] as card}
        <div class="ink-border" style="padding: 16px; background: var(--pw-surface); text-align: center;">
          <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); font-weight: 700;">{card.label}</div>
          <div style="font-size: 20px; font-weight: 900; margin-top: 4px;">{card.value}</div>
        </div>
      {/each}
    </div>

    <!-- Extra stats rendered as key-value if the API returns more -->
    {#if stats.extra || stats.details}
      <div style="margin-top: 20px;">
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; border-bottom: 1px solid var(--pw-ink); padding-bottom: 4px;">Additional Metrics</div>
        <div style="display: grid; grid-template-columns: 200px 1fr; gap: 4px 16px; font-size: 11px;">
          {#each Object.entries(stats.extra || stats.details || {}) as [k, v]}
            <span style="font-weight: 700; text-transform: uppercase; font-size: 10px;">{k.replace(/_/g, ' ')}</span>
            <span>{v}</span>
          {/each}
        </div>
      </div>
    {/if}
  {:else}
    <div style="font-size: 11px; color: var(--pw-muted);">Stats unavailable.</div>
  {/if}

{:else if activeTab === 'integrations'}
  <div class="cli-terminal" style="margin-bottom: 16px; padding: 8px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash admin --integrations</span>
      <span style="margin-left: auto; font-size: 11px; opacity: 0.6;">external connectors</span>
    </div>
  </div>

  <!-- SharePoint Configuration -->
  <div style="font-size: 16px; font-weight: 900; text-transform: uppercase; margin-bottom: 16px;">SharePoint Connector</div>

  <div class="ink-border" style="padding: 16px; background: var(--pw-surface); margin-bottom: 16px;">
    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; margin-bottom: 12px;">AZURE APP REGISTRATION</div>
    <div style="font-size: 10px; color: var(--pw-muted); margin-bottom: 16px; line-height: 1.6;">
      1. Go to <strong>Azure Portal</strong> &rarr; App Registrations &rarr; New Registration<br>
      2. Name: <code style="background: #222; color: #0f0; padding: 1px 4px; font-size: 11px;">{$brand.name} SharePoint Connector</code><br>
      3. Redirect URI: <code style="background: #222; color: #0f0; padding: 1px 4px; font-size: 11px;">https://your-domain/api/sharepoint/callback</code><br>
      4. API Permissions &rarr; Add: <strong>Sites.Read.All</strong>, <strong>Files.Read.All</strong>, <strong>User.Read</strong>, <strong>offline_access</strong><br>
      5. Certificates &amp; Secrets &rarr; New Client Secret &rarr; Copy value below
    </div>

    <div style="display: flex; flex-direction: column; gap: 10px; max-width: 500px;">
      <div>
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">CLIENT ID (Application ID)</div>
        <input type="text" bind:value={spAdminClientId} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
      </div>
      <div>
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">CLIENT SECRET</div>
        <input type="password" bind:value={spAdminClientSecret} placeholder="{spAdminConfig.has_secret ? '•••••••• (already set, leave blank to keep)' : 'paste secret value here'}" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
      </div>
      <div>
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">TENANT ID (Directory ID)</div>
        <input type="text" bind:value={spAdminTenantId} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
      </div>

      <div style="display: flex; align-items: center; gap: 12px; margin-top: 4px;">
        <button class="send-btn" style="padding: 8px 20px; font-size: 11px;" disabled={spAdminSaving || !spAdminClientId || !spAdminTenantId} onclick={saveSharePointConfig}>
          {spAdminSaving ? 'SAVING...' : 'SAVE CONFIGURATION'}
        </button>
        {#if spAdminConfig.configured}
          <span style="font-size: 10px; color: var(--pw-accent); font-weight: 700;">&#10003; CONFIGURED</span>
        {:else}
          <span style="font-size: 10px; color: var(--pw-muted);">NOT CONFIGURED</span>
        {/if}
      </div>
      {#if spAdminMsg}
        <div style="font-size: 10px; color: {spAdminMsg.includes('Saved') ? 'var(--pw-accent)' : '#e74c3c'}; font-weight: 700;">{spAdminMsg}</div>
      {/if}
    </div>
  </div>

  <!-- Connected Sources across all projects -->
  {#if spAllSources.length > 0}
    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px;">ALL CONNECTED SOURCES ({spAllSources.length})</div>
    <div style="overflow-x: auto;">
      <table style="width: 100%; font-size: 11px; border-collapse: collapse;">
        <thead>
          <tr style="border-bottom: 2px solid var(--pw-ink); text-align: left;">
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">PROJECT</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">SITE</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">FOLDER</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">FILES</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">LAST SYNC</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">STATUS</th>
          </tr>
        </thead>
        <tbody>
          {#each spAllSources as src}
            <tr style="border-bottom: 1px solid var(--pw-bg-alt);">
              <td style="padding: 6px 8px; font-weight: 700;">{src.project_slug}</td>
              <td style="padding: 6px 8px;">{src.site_name || '-'}</td>
              <td style="padding: 6px 8px; font-size: 10px; color: var(--pw-muted);">{src.folder_path || '/'}</td>
              <td style="padding: 6px 8px;">{src.files_synced}</td>
              <td style="padding: 6px 8px; font-size: 10px;">{src.last_sync_at ? fmtDate(src.last_sync_at) : '-'}</td>
              <td style="padding: 6px 8px;">
                <span style="font-size: 11px; font-weight: 700; padding: 1px 6px; background: {src.status === 'active' ? 'var(--pw-accent)' : '#888'}; color: #1a1a1a;">{src.status?.toUpperCase()}</span>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <div style="font-size: 11px; color: var(--pw-muted);">No SharePoint sources connected yet. Users can connect from their project Settings &rarr; SOURCES tab.</div>
  {/if}

  <!-- Google Drive Configuration -->
  <div style="font-size: 16px; font-weight: 900; text-transform: uppercase; margin-top: 30px; margin-bottom: 16px;">Google Drive Connector</div>

  <div class="ink-border" style="padding: 16px; background: var(--pw-surface); margin-bottom: 16px;">
    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; margin-bottom: 12px;">GOOGLE OAUTH SETUP</div>
    <div style="font-size: 10px; color: var(--pw-muted); margin-bottom: 16px; line-height: 1.6;">
      1. Go to <strong>Google Cloud Console</strong> &rarr; APIs &amp; Services &rarr; Credentials &rarr; Create OAuth Client ID
    </div>

    <div style="display: flex; flex-direction: column; gap: 10px; max-width: 500px;">
      <div>
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">GOOGLE_CLIENT_ID</div>
        <input type="text" bind:value={gdAdminClientId} placeholder="xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
      </div>
      <div>
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">GOOGLE_CLIENT_SECRET</div>
        <input type="password" bind:value={gdAdminClientSecret} placeholder="{gdAdminConfig.has_secret ? '•••••••• (already set, leave blank to keep)' : 'paste client secret here'}" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
      </div>

      <div style="display: flex; align-items: center; gap: 12px; margin-top: 4px;">
        <button class="send-btn" style="padding: 8px 20px; font-size: 11px;" disabled={gdAdminSaving || !gdAdminClientId} onclick={saveGDriveConfig}>
          {gdAdminSaving ? 'SAVING...' : 'SAVE CONFIGURATION'}
        </button>
        {#if gdAdminConfig.configured}
          <span style="font-size: 10px; color: var(--pw-accent); font-weight: 700;">&#10003; CONFIGURED</span>
        {:else}
          <span style="font-size: 10px; color: var(--pw-muted);">NOT CONFIGURED</span>
        {/if}
      </div>
      {#if gdAdminMsg}
        <div style="font-size: 10px; color: {gdAdminMsg.includes('Saved') ? 'var(--pw-accent)' : '#e74c3c'}; font-weight: 700;">{gdAdminMsg}</div>
      {/if}
    </div>
  </div>

  <!-- OneDrive Configuration -->
  <div style="font-size: 16px; font-weight: 900; text-transform: uppercase; margin-top: 30px; margin-bottom: 16px;">OneDrive Connector</div>

  <div class="ink-border" style="padding: 16px; background: var(--pw-surface); margin-bottom: 16px;">
    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; margin-bottom: 12px;">AZURE APP REGISTRATION (ONEDRIVE)</div>
    <div style="font-size: 10px; color: var(--pw-muted); margin-bottom: 16px; line-height: 1.6;">
      OneDrive can reuse the same Azure App Registration as SharePoint if scopes overlap.<br>
      If you already configured SharePoint with the same Azure App Reg, just add
      <code style="background: #222; color: #0f0; padding: 1px 4px; font-size: 11px;">https://your-domain/api/onedrive/callback</code>
      as another Redirect URI in Azure.<br>
      <br>
      Otherwise: <a href="https://portal.azure.com" target="_blank" rel="noreferrer" style="color: #0078d4;">portal.azure.com</a>
      &rarr; App registrations &rarr; Your app &rarr; Authentication &rarr; Add Redirect URI.<br>
      Required scopes: <strong>Files.Read</strong>, <strong>Files.Read.All</strong>, <strong>User.Read</strong>, <strong>offline_access</strong>.
    </div>

    <div style="display: flex; flex-direction: column; gap: 10px; max-width: 500px;">
      <div>
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">MS CLIENT ID</div>
        <input type="text" bind:value={odAdminClientId} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
      </div>
      <div>
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">MS CLIENT SECRET</div>
        <input type="password" bind:value={odAdminClientSecret} placeholder={odAdminConfig.has_secret ? '••••••••  (already set, leave blank to keep)' : 'paste secret value here'} style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
      </div>
      <div>
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">MS TENANT ID</div>
        <input type="text" bind:value={odAdminTenantId} placeholder="common" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 2px;">Use <code>common</code> for personal + multi-tenant, or your specific tenant ID for single-tenant.</div>
      </div>
      <div>
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">REDIRECT URI</div>
        <input type="text" readonly value={typeof window !== 'undefined' ? `${window.location.origin}/api/onedrive/callback` : '/api/onedrive/callback'} style="width: 100%; border: 2px solid var(--pw-bg-alt); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg-alt); color: var(--pw-muted);" />
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 2px;">Paste this exact URL into Azure &rarr; Authentication &rarr; Redirect URIs.</div>
      </div>

      <div style="display: flex; align-items: center; gap: 12px; margin-top: 4px;">
        <button class="send-btn" style="padding: 8px 20px; font-size: 11px;" disabled={odAdminSaving || !odAdminClientId || !odAdminTenantId} onclick={saveOneDriveConfig}>
          {odAdminSaving ? 'SAVING...' : 'SAVE CONFIGURATION'}
        </button>
        {#if odAdminConfig.configured}
          <span style="font-size: 10px; color: var(--pw-accent); font-weight: 700;">&#10003; CONFIGURED</span>
        {:else}
          <span style="font-size: 10px; color: var(--pw-muted);">NOT CONFIGURED</span>
        {/if}
      </div>
      {#if odAdminMsg}
        <div style="font-size: 10px; color: {odAdminMsg.includes('Saved') ? 'var(--pw-accent)' : '#e74c3c'}; font-weight: 700;">{odAdminMsg}</div>
      {/if}
    </div>
  </div>

  <!-- Database Connectors -->
  <div style="font-size: 16px; font-weight: 900; text-transform: uppercase; margin-top: 30px; margin-bottom: 16px;">Database Connectors</div>

  {#if dbAdminStep === 'idle'}
    <!-- DB type picker -->
    <div style="display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap;">
      {#each [['postgresql', 'PG', '#336791', '5432'], ['mysql', 'MY', '#00758f', '3306']] as [type, icon, color, port]}
        <button class="ink-border" style="padding: 14px 20px; background: var(--pw-surface); cursor: pointer; text-align: center; border-width: 2px; min-width: 140px;"
          onclick={() => { dbAdminType = type; dbAdminPort = port; dbAdminStep = 'form'; }}>
          <div style="width: 32px; height: 32px; background: {color}; display: flex; align-items: center; justify-content: center; font-weight: 900; color: white; font-size: 10px; margin: 0 auto 6px;">{icon}</div>
          <div style="font-size: 11px; font-weight: 900;">{type === 'postgresql' ? 'PostgreSQL' : 'MySQL'}</div>
        </button>
      {/each}
    </div>

  {:else if dbAdminStep === 'form'}
    <!-- Connection form -->
    <div class="ink-border" style="padding: 16px; background: var(--pw-surface); max-width: 500px; margin-bottom: 16px;">
      <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; margin-bottom: 10px;">CONNECT {dbAdminType.toUpperCase()}</div>
      <div style="display: flex; flex-direction: column; gap: 8px;">
        <div>
          <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">HOST</div>
          <input type="text" bind:value={dbAdminHost} placeholder="db.company.com" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
        </div>
        <div style="display: flex; gap: 8px;">
          <div style="flex: 1;">
            <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">PORT</div>
            <input type="text" bind:value={dbAdminPort} style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
          </div>
          <div style="flex: 2;">
            <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">DATABASE</div>
            <input type="text" bind:value={dbAdminName} placeholder="analytics" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
          </div>
        </div>
        <div style="display: flex; gap: 8px;">
          <div style="flex: 1;">
            <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">USERNAME</div>
            <input type="text" bind:value={dbAdminUser} style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
          </div>
          <div style="flex: 1;">
            <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">PASSWORD</div>
            <input type="password" bind:value={dbAdminPass} style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);" />
          </div>
        </div>
        <div style="display: flex; gap: 8px; margin-top: 4px;">
          <button class="feedback-btn" style="font-size: 11px; padding: 4px 10px;" onclick={() => dbAdminStep = 'idle'}>CANCEL</button>
          <button class="send-btn" style="font-size: 10px; padding: 6px 16px;" disabled={dbAdminTesting || !dbAdminHost || !dbAdminName || !dbAdminUser} onclick={dbAdminTest}>
            {dbAdminTesting ? 'TESTING...' : 'TEST CONNECTION'}
          </button>
        </div>
        {#if dbAdminTestResult}
          <div style="font-size: 10px; color: {dbAdminTestResult.error ? '#e74c3c' : 'var(--pw-accent)'}; font-weight: 700;">
            {dbAdminTestResult.error || `Connected! ${dbAdminTestResult.tables?.length || 0} tables found`}
          </div>
        {/if}
      </div>
    </div>

  {:else if dbAdminStep === 'tables'}
    <!-- Table selection + project picker -->
    <div class="ink-border" style="padding: 16px; background: var(--pw-surface); max-width: 500px; margin-bottom: 16px;">
      <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; margin-bottom: 10px;">SELECT TABLES & PROJECT</div>

      <!-- Project selector -->
      <div style="margin-bottom: 12px;">
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;">ASSIGN TO PROJECT</div>
        <select bind:value={dbAdminProject} style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-bg);">
          <option value="">-- select project --</option>
          {#each projects as p}
            <option value={p.slug || p.project_slug}>{p.name || p.agent_name || p.slug || p.project_slug}</option>
          {/each}
        </select>
      </div>

      <!-- Table checkboxes -->
      <div style="margin-bottom: 8px;">
        <label style="font-size: 10px; cursor: pointer;">
          <input type="checkbox" checked={dbAdminSelectedTables.length === dbAdminTables.length}
            onchange={() => { dbAdminSelectedTables = dbAdminSelectedTables.length === dbAdminTables.length ? [] : [...dbAdminTables]; }} />
          Select All ({dbAdminTables.length})
        </label>
      </div>
      <div style="max-height: 250px; overflow-y: auto; border: 1px solid var(--pw-bg-alt); padding: 6px;">
        {#each dbAdminTables as tbl}
          <label style="display: flex; align-items: center; gap: 6px; padding: 2px 0; font-size: 11px; cursor: pointer;">
            <input type="checkbox" checked={dbAdminSelectedTables.includes(tbl)}
              onchange={() => {
                if (dbAdminSelectedTables.includes(tbl)) dbAdminSelectedTables = dbAdminSelectedTables.filter(t => t !== tbl);
                else dbAdminSelectedTables = [...dbAdminSelectedTables, tbl];
              }} />
            {tbl}
          </label>
        {/each}
      </div>
      <div style="display: flex; gap: 8px; margin-top: 10px;">
        <button class="feedback-btn" style="font-size: 11px; padding: 4px 10px;" onclick={() => dbAdminStep = 'form'}>BACK</button>
        <button class="send-btn" style="font-size: 10px; padding: 6px 16px;" disabled={dbAdminConnecting || dbAdminSelectedTables.length === 0 || !dbAdminProject} onclick={dbAdminConnect}>
          {dbAdminConnecting ? 'CONNECTING...' : `CONNECT ${dbAdminSelectedTables.length} TABLES`}
        </button>
      </div>
      {#if dbAdminMsg2}
        <div style="font-size: 10px; color: {dbAdminMsg2.includes('!') ? 'var(--pw-accent)' : '#e74c3c'}; font-weight: 700; margin-top: 6px;">{dbAdminMsg2}</div>
      {/if}
    </div>
  {/if}

  <!-- All connected DB sources -->
  {#if dbAllSources.length > 0}
    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; margin-top: 16px;">ALL DATABASE SOURCES ({dbAllSources.length})</div>
    <div style="overflow-x: auto;">
      <table style="width: 100%; font-size: 11px; border-collapse: collapse;">
        <thead>
          <tr style="border-bottom: 2px solid var(--pw-ink); text-align: left;">
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">PROJECT</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">TYPE</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">HOST</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">DATABASE</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">TABLES</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">LAST SYNC</th>
            <th style="padding: 6px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;">STATUS</th>
          </tr>
        </thead>
        <tbody>
          {#each dbAllSources as src}
            <tr style="border-bottom: 1px solid var(--pw-bg-alt);">
              <td style="padding: 6px 8px; font-weight: 700;">{src.project_slug || src.project || '-'}</td>
              <td style="padding: 6px 8px;">
                <span style="font-size: 11px; font-weight: 700; padding: 1px 6px; background: var(--pw-bg-alt); color: var(--pw-ink);">{(src.type || src.db_type || '-').toUpperCase()}</span>
              </td>
              <td style="padding: 6px 8px; font-size: 10px; color: var(--pw-muted); font-family: var(--pw-font-body);">{src.host || '-'}</td>
              <td style="padding: 6px 8px;">{src.database || src.db_name || '-'}</td>
              <td style="padding: 6px 8px; font-weight: 900;">{src.tables ?? src.table_count ?? '-'}</td>
              <td style="padding: 6px 8px; font-size: 10px;">{src.last_sync_at || src.last_sync ? fmtDate(src.last_sync_at || src.last_sync) : '-'}</td>
              <td style="padding: 6px 8px;">
                <span style="font-size: 11px; font-weight: 700; padding: 1px 6px; background: {src.status === 'active' || src.status === 'connected' ? 'var(--pw-accent)' : '#888'}; color: #1a1a1a;">{(src.status || 'unknown').toUpperCase()}</span>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <!-- Future: Other integrations -->
  <div style="margin-top: 30px; border-top: 1px solid var(--pw-bg-alt); padding-top: 16px;">
    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; margin-bottom: 8px;">COMING SOON</div>
    <div style="display: flex; gap: 12px; flex-wrap: wrap;">
      <div class="ink-border" style="padding: 12px 16px; background: var(--pw-surface); opacity: 0.5; min-width: 150px; text-align: center;">
        <div style="font-size: 11px; font-weight: 900;">Snowflake</div>
        <div style="font-size: 11px; color: var(--pw-muted);">Cloud data warehouse</div>
      </div>
      <div class="ink-border" style="padding: 12px 16px; background: var(--pw-surface); opacity: 0.5; min-width: 150px; text-align: center;">
        <div style="font-size: 11px; font-weight: 900;">BigQuery</div>
        <div style="font-size: 11px; color: var(--pw-muted);">Google analytics warehouse</div>
      </div>
    </div>
  </div>

{:else if activeTab === 'branding'}
  <div class="cli-terminal" style="margin-bottom: 16px; padding: 8px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash branding --tenants</span>
      <span style="margin-left: auto; font-size: 11px; opacity: 0.6;">{brandingTenants.length} tenant(s)</span>
    </div>
  </div>

  <div class="flex items-center justify-between mb-4">
    <div>
      <div style="font-size: 16px; font-weight: 900; text-transform: uppercase;">White-Label Branding</div>
      <div style="font-size: 11px; color: var(--pw-muted);">
        Active: <span style="color: var(--pw-accent); font-weight: 900;">{brandingActive || '—'}</span>
        {#if brandingRoot}<span style="margin-left: 12px;">root: <code>{brandingRoot}/</code></span>{/if}
      </div>
    </div>
    <div style="display: flex; gap: 6px;">
      <button class="send-btn" style="font-size: 10px; padding: 6px 14px;" onclick={openNewTenantModal}>+ NEW TENANT</button>
      <button class="send-btn" style="font-size: 10px; padding: 6px 14px;" onclick={loadBranding}>↻ REFRESH</button>
    </div>
  </div>

  {#if brandingTenants.length === 0}
    <div class="ink-border" style="padding: 20px; margin-bottom: 16px;">
      <div style="font-weight: 900; margin-bottom: 8px;">NO TENANTS FOUND</div>
      <div style="font-size: 11px; color: var(--pw-muted); margin-bottom: 8px;">
        {brandingMessage || `Click + NEW TENANT to create one, or drop a folder into ${brandingRoot || 'branding'}/<name>/ and click REFRESH.`}
      </div>
    </div>
  {:else}
    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px;">
      {#each brandingTenants as t}
        {@const isActive = t.name === brandingActive}
        {@const isProtected = isActive || t.name === 'default'}
        <div class="ink-border" style="padding: 14px; position: relative; {isActive ? 'border-color: var(--pw-accent); box-shadow: 0 0 0 2px var(--pw-accent);' : ''}">
          {#if isActive}
            <span style="position: absolute; top: 8px; right: 8px; background: var(--pw-accent); color: #fff; font-size: 11px; font-weight: 900; padding: 2px 6px;">ACTIVE</span>
          {/if}
          <div style="height: 56px; display: flex; align-items: center; justify-content: center; background: var(--pw-bg-alt); border: 1px solid var(--pw-ink); margin-bottom: 10px;">
            {#if t.has_logo}
              <img src={`/api/branding/logo.svg?t=${t.name}`} alt={t.name} style="max-height: 40px; max-width: 80%;" />
            {:else}
              <span style="font-size: 10px; color: var(--pw-muted);">no logo.svg</span>
            {/if}
          </div>
          <div style="font-weight: 900; font-size: 13px; margin-bottom: 2px;">{t.company_name}</div>
          <div style="font-size: 10px; color: var(--pw-muted); margin-bottom: 8px;">slug: {t.name}</div>
          <div style="display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 10px;">
            <span class="tag-label" style="font-size: 11px; {t.has_logo ? '' : 'opacity:0.3;'}">LOGO</span>
            <span class="tag-label" style="font-size: 11px; {t.has_theme ? '' : 'opacity:0.3;'}">THEME</span>
            <span class="tag-label" style="font-size: 11px; {t.has_favicon ? '' : 'opacity:0.3;'}">FAVICON</span>
            <span class="tag-label" style="font-size: 11px; {t.has_company_json ? '' : 'opacity:0.3;'}">COMPANY</span>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 4px;">
            <button class="send-btn" style="font-size: 10px; padding: 5px 4px;" onclick={() => openEditTenant(t.name)}><Icon name="pencil" size={14} /> EDIT</button>
            <button
              class="send-btn"
              style="font-size: 10px; padding: 5px 4px; {isActive ? 'opacity: 0.4;' : ''}"
              disabled={isActive || brandingActivating === t.name}
              onclick={() => activateBrandingTenant(t.name)}
            >
              {isActive ? ' ACTIVE' : (brandingActivating === t.name ? ' …' : ' ACTIVATE')}
            </button>
            <button class="send-btn" style="font-size: 10px; padding: 5px 4px;" onclick={() => exportTenant(t.name)}>⊕ EXPORT</button>
            <button
              class="send-btn"
              style="font-size: 10px; padding: 5px 4px; color: #d33; {isProtected ? 'opacity: 0.4;' : ''}"
              disabled={isProtected}
              onclick={() => deleteTenant(t.name)}
            >
               DELETE
            </button>
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <div class="ink-border" style="padding: 14px; margin-top: 16px;">
    <div style="font-size: 11px; font-weight: 900; margin-bottom: 6px;">ADD A NEW TENANT</div>
    <div style="font-size: 11px; color: var(--pw-muted); line-height: 1.5;">
      Click <b>+ NEW TENANT</b> above to create one via the API (optionally cloned from an existing tenant), or drop a folder at
      <code>{brandingRoot || 'branding'}/&lt;name&gt;/</code> containing
      <code>logo.svg</code>, <code>theme.css</code>, <code>company.json</code>, <code>favicon.ico</code>, then click <b>↻ REFRESH</b>.
    </div>
  </div>

  <!-- ─── NEW TENANT MODAL ─── -->
  {#if showNewTenantModal}
    <div
      style="position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 9999; display: flex; align-items: center; justify-content: center; padding: 24px;"
      onclick={() => (showNewTenantModal = false)}
    >
      <div
        class="ink-border"
        style="background: var(--pw-surface); max-width: 520px; width: 100%; max-height: 90vh; overflow: auto; padding: 20px;"
        onclick={(e) => e.stopPropagation()}
      >
        <div style="font-size: 14px; font-weight: 900; margin-bottom: 14px;">+ NEW TENANT</div>

        <label style="display: block; font-size: 10px; font-weight: 900; margin-bottom: 4px;">SLUG (lowercase, [a-z0-9_-])</label>
        <input
          type="text"
          bind:value={newTenantForm.slug}
          placeholder="acme-corp"
          style="width: 100%; padding: 8px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: inherit; margin-bottom: 10px;"
        />

        <label style="display: block; font-size: 10px; font-weight: 900; margin-bottom: 4px;">DISPLAY NAME</label>
        <input
          type="text"
          bind:value={newTenantForm.display_name}
          placeholder="Acme Corp"
          style="width: 100%; padding: 8px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: inherit; margin-bottom: 12px;"
        />

        <div style="font-size: 10px; font-weight: 900; margin-bottom: 6px;">START FROM</div>
        <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 4px;">
          <input
            type="radio"
            name="clone_mode"
            checked={!newTenantForm.clone_from}
            onchange={() => (newTenantForm.clone_from = '')}
          />
          Blank
        </label>
        <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 6px;">
          <input
            type="radio"
            name="clone_mode"
            checked={!!newTenantForm.clone_from}
            onchange={() => (newTenantForm.clone_from = brandingTenants[0]?.name || '')}
          />
          Clone from existing
        </label>
        {#if newTenantForm.clone_from !== ''}
          <select
            bind:value={newTenantForm.clone_from}
            style="width: 100%; padding: 6px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: inherit; margin-bottom: 12px;"
          >
            {#each brandingTenants as t}
              <option value={t.name}>{t.name} — {t.company_name}</option>
            {/each}
          </select>
        {/if}

        <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 16px;">
          <input type="checkbox" bind:checked={newTenantForm.activate_after} />
          Activate immediately
        </label>

        {#if newTenantError}
          <div style="color: #ff4040; font-size: 11px; margin-bottom: 10px;">{newTenantError}</div>
        {/if}

        <div style="display: flex; gap: 6px; justify-content: flex-end;">
          <button class="send-btn" style="font-size: 11px; padding: 6px 14px;" onclick={() => (showNewTenantModal = false)} disabled={newTenantBusy}>CANCEL</button>
          <button class="send-btn" style="font-size: 11px; padding: 6px 14px;" onclick={submitNewTenant} disabled={newTenantBusy}>
            {newTenantBusy ? 'CREATING…' : 'CREATE'}
          </button>
        </div>
      </div>
    </div>
  {/if}

  <!-- ─── EDIT TENANT MODAL ─── -->
  {#if showEditTenantModal}
    {@const slug = showEditTenantModal}
    {@const isActive = slug === brandingActive}
    {@const isProtected = isActive || slug === 'default'}
    <div
      style="position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 9999; display: flex; align-items: center; justify-content: center; padding: 24px;"
      onclick={closeEditTenant}
    >
      <div
        class="ink-border"
        style="background: var(--pw-surface); max-width: 900px; width: 100%; max-height: 90vh; overflow: auto; padding: 20px;"
        onclick={(e) => e.stopPropagation()}
      >
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;">
          <div style="font-size: 14px; font-weight: 900;"><Icon name="pencil" size={14} /> EDIT TENANT — <span style="color: var(--pw-accent);">{slug}</span></div>
          <button class="send-btn" style="font-size: 10px; padding: 4px 10px;" onclick={closeEditTenant}><Icon name="x" size={14} /></button>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
          <!-- LEFT: FORM -->
          <div>
            <!-- IDENTITY -->
            <div style="font-size: 11px; font-weight: 900; margin-bottom: 6px; color: var(--pw-accent);">IDENTITY</div>
            <label style="display: block; font-size: 11px; margin-bottom: 2px;">APP NAME</label>
            <input type="text" bind:value={editForm.app_name} style="width: 100%; padding: 6px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: inherit; margin-bottom: 6px; font-size: 12px;" />
            <label style="display: block; font-size: 11px; margin-bottom: 2px;">FULL NAME</label>
            <input type="text" bind:value={editForm.full_name} style="width: 100%; padding: 6px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: inherit; margin-bottom: 6px; font-size: 12px;" />
            <label style="display: block; font-size: 11px; margin-bottom: 2px;">TAGLINE</label>
            <input type="text" bind:value={editForm.tagline} style="width: 100%; padding: 6px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: inherit; margin-bottom: 6px; font-size: 12px;" />
            <label style="display: block; font-size: 11px; margin-bottom: 2px;">DOMAIN</label>
            <input type="text" bind:value={editForm.domain} style="width: 100%; padding: 6px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: inherit; margin-bottom: 6px; font-size: 12px;" />
            <label style="display: block; font-size: 11px; margin-bottom: 2px;">SUPPORT EMAIL</label>
            <input type="text" bind:value={editForm.support_email} style="width: 100%; padding: 6px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: inherit; margin-bottom: 12px; font-size: 12px;" />

            <!-- ASSETS -->
            <div style="font-size: 11px; font-weight: 900; margin-bottom: 6px; color: var(--pw-accent);">ASSETS</div>
            <label style="display: block; font-size: 11px; margin-bottom: 2px;">LOGO {editPendingLogo ? '(pending)' : ''}</label>
            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 8px;">
              {#if editForm.logo_url}
                <img src={editForm.logo_url} alt="logo" style="height: 32px; max-width: 80px; border: 1px solid var(--pw-ink);" onerror={(e) => ((e.target as HTMLImageElement).style.display = 'none')} />
              {/if}
              <input type="file" accept="image/svg+xml,image/png" onchange={onLogoFileChange} style="font-size: 10px; flex: 1;" />
            </div>
            <label style="display: block; font-size: 11px; margin-bottom: 2px;">FAVICON {editPendingFavicon ? '(pending)' : ''}</label>
            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 12px;">
              {#if editForm.favicon_url}
                <img src={editForm.favicon_url} alt="favicon" style="height: 24px; width: 24px; border: 1px solid var(--pw-ink);" onerror={(e) => ((e.target as HTMLImageElement).style.display = 'none')} />
              {/if}
              <input type="file" accept="image/x-icon,image/png" onchange={onFaviconFileChange} style="font-size: 10px; flex: 1;" />
            </div>

            <!-- THEME COLORS -->
            <div style="font-size: 11px; font-weight: 900; margin-bottom: 6px; color: var(--pw-accent);">THEME COLORS</div>
            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 12px;">
              <div>
                <label style="display: block; font-size: 11px; margin-bottom: 2px;">PRIMARY</label>
                <input type="color" bind:value={editForm.primary_color} style="width: 100%; height: 28px; border: 1px solid var(--pw-ink); background: transparent;" />
              </div>
              <div>
                <label style="display: block; font-size: 11px; margin-bottom: 2px;">BACKGROUND</label>
                <input type="color" bind:value={editForm.background_color} style="width: 100%; height: 28px; border: 1px solid var(--pw-ink); background: transparent;" />
              </div>
              <div>
                <label style="display: block; font-size: 11px; margin-bottom: 2px;">ACCENT</label>
                <input type="color" bind:value={editForm.accent_color} style="width: 100%; height: 28px; border: 1px solid var(--pw-ink); background: transparent;" />
              </div>
            </div>

            <!-- CSS -->
            <div style="font-size: 11px; font-weight: 900; margin-bottom: 6px; color: var(--pw-accent);">CUSTOM CSS (theme.css)</div>
            <textarea
              bind:value={editForm.css}
              rows="6"
              style="width: 100%; padding: 6px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: monospace; font-size: 11px; margin-bottom: 12px;"
            ></textarea>

            <!-- FOOTER -->
            <div style="font-size: 11px; font-weight: 900; margin-bottom: 6px; color: var(--pw-accent);">FOOTER</div>
            <label style="display: block; font-size: 11px; margin-bottom: 2px;">FOOTER TEXT</label>
            <input type="text" bind:value={editForm.footer_text} style="width: 100%; padding: 6px; background: transparent; border: 1px solid var(--pw-ink); color: var(--pw-ink); font-family: inherit; margin-bottom: 6px; font-size: 12px;" />
            <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 8px;">
              <input type="checkbox" bind:checked={editForm.show_powered_by} />
              Show "Powered by Dash"
            </label>
          </div>

          <!-- RIGHT: LIVE PREVIEW -->
          <div>
            <div style="font-size: 11px; font-weight: 900; margin-bottom: 6px; color: var(--pw-accent);">LIVE PREVIEW</div>

            <!-- Browser tab mockup -->
            <div style="border: 1px solid var(--pw-ink); background: rgba(0,0,0,0.4); padding: 6px 10px; margin-bottom: 10px; display: flex; align-items: center; gap: 6px; font-size: 11px;">
              {#if editForm.favicon_url}
                <img src={editForm.favicon_url} alt="favicon" style="height: 14px; width: 14px;" onerror={(e) => ((e.target as HTMLImageElement).style.display = 'none')} />
              {:else}
                <span style="display: inline-block; width: 14px; height: 14px; background: {editForm.primary_color}; border-radius: 0;"></span>
              {/if}
              <span style="opacity: 0.85;">{editForm.app_name || slug} — {editForm.full_name || 'Tenant'}</span>
            </div>

            <!-- Mini app card -->
            <div style="border: 2px solid {editForm.primary_color}; background: {editForm.background_color}; padding: 14px; margin-bottom: 10px;">
              <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
                {#if editForm.logo_url}
                  <img src={editForm.logo_url} alt="logo" style="height: 36px; max-width: 100px;" onerror={(e) => ((e.target as HTMLImageElement).style.display = 'none')} />
                {/if}
                <div>
                  <div style="font-weight: 900; font-size: 14px; color: {editForm.primary_color};">{editForm.app_name || 'App'}</div>
                  <div style="font-size: 10px; opacity: 0.7;">{editForm.full_name || 'Full company name'}</div>
                </div>
              </div>
              <div style="font-size: 11px; font-style: italic; opacity: 0.85; margin-bottom: 10px;">{editForm.tagline || 'Your tagline here'}</div>
              <button style="background: {editForm.primary_color}; color: {editForm.background_color}; border: 0; padding: 6px 14px; font-weight: 900; font-size: 11px; cursor: default;">PRIMARY BUTTON</button>
              <button style="background: transparent; color: {editForm.accent_color}; border: 1px solid {editForm.accent_color}; padding: 6px 14px; font-weight: 900; font-size: 11px; margin-left: 6px; cursor: default;">ACCENT</button>
            </div>

            <!-- Swatches -->
            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; margin-bottom: 10px;">
              <div style="text-align: center;">
                <div style="background: {editForm.primary_color}; height: 32px; border: 1px solid var(--pw-ink);"></div>
                <div style="font-size: 11px; margin-top: 2px;">PRIMARY</div>
                <div style="font-size: 11px; opacity: 0.6;">{editForm.primary_color}</div>
              </div>
              <div style="text-align: center;">
                <div style="background: {editForm.background_color}; height: 32px; border: 1px solid var(--pw-ink);"></div>
                <div style="font-size: 11px; margin-top: 2px;">BACKGROUND</div>
                <div style="font-size: 11px; opacity: 0.6;">{editForm.background_color}</div>
              </div>
              <div style="text-align: center;">
                <div style="background: {editForm.accent_color}; height: 32px; border: 1px solid var(--pw-ink);"></div>
                <div style="font-size: 11px; margin-top: 2px;">ACCENT</div>
                <div style="font-size: 11px; opacity: 0.6;">{editForm.accent_color}</div>
              </div>
            </div>

            <div style="font-size: 11px; opacity: 0.6; line-height: 1.5;">
              Preview is illustrative only. Final styling applies after SAVE & ACTIVATE.
              {#if editForm.footer_text}
                <div style="margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--pw-ink);">{editForm.footer_text}{editForm.show_powered_by ? ' · Powered by Dash' : ''}</div>
              {/if}
            </div>
          </div>
        </div>

        {#if editError}
          <div style="color: #ff4040; font-size: 11px; margin-top: 12px;">{editError}</div>
        {/if}

        <div style="display: flex; gap: 6px; justify-content: space-between; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--pw-ink);">
          <button
            class="send-btn"
            style="font-size: 11px; padding: 6px 12px; color: #d33; {isProtected ? 'opacity: 0.4;' : ''}"
            disabled={isProtected || editBusy}
            onclick={() => deleteTenant(slug)}
          >
             DELETE
          </button>
          <div style="display: flex; gap: 6px;">
            <button class="send-btn" style="font-size: 11px; padding: 6px 12px;" onclick={closeEditTenant} disabled={editBusy}>CANCEL</button>
            <button class="send-btn" style="font-size: 11px; padding: 6px 12px;" onclick={() => saveTenantEdit(slug, false)} disabled={editBusy}>
              {editBusy ? 'SAVING…' : 'SAVE DRAFT'}
            </button>
            <button class="send-btn" style="font-size: 11px; padding: 6px 12px;" onclick={() => saveTenantEdit(slug, true)} disabled={editBusy}>
              {editBusy ? '…' : 'SAVE & ACTIVATE'}
            </button>
          </div>
        </div>
      </div>
    </div>
  {/if}

{:else if activeTab === 'admin-settings'}
  <div class="ink-border" style="padding:16px;">
    <h2 style="font-size:13px;">ADMIN SETTINGS</h2>
    <div style="font-size:11px; opacity:0.7; margin-bottom:12px;">
      Resolution order: project &gt; global &gt; env var &gt; default. Changes
      take effect within 30s (cache TTL).
    </div>
  </div>

  {#each [
    {name: 'SELF-LEARNING', keys: ['enable_self_learning','daily_cron_time','enable_sunday_canary','enable_daily_decay','max_questions_per_cycle','per_question_timeout_s','daily_cost_cap_default_usd','enable_promotion_to_central','enable_dry_run']},
    {name: 'RESEARCH PROVIDERS', keys: ['enable_tavily','enable_brave_search','enable_perplexity','enable_fred','enable_wikipedia','enable_world_bank','enable_alpha_vantage','enable_wikidata','enable_xmla_pull','enable_web_fetch']},
    {name: 'COLUMN CLASSIFIER', keys: ['auto_classify_on_train','pii_default_action','enable_llm_typing','enable_embedding_matcher']},
    {name: 'DOMAIN DETECTION', keys: ['auto_detect_domain','auto_load_seeds']},
    {name: 'BACKUP', keys: ['enable_backup','backup_retention_days']},
    {name: 'NOTIFICATIONS', keys: ['enable_digest_slack','slack_webhook_url','enable_email_digest']},
    {name: 'SECURITY', keys: ['rate_limit_per_minute','agno_debug']},
    {name: 'SCHEDULER', keys: ['enable_in_process_scheduler','enable_k8s_cronjob_mode']},
    {name: 'INTEGRATIONS', keys: ['gateway_enabled','embed_enabled']},
  ] as section}
    <div class="ink-border" style="margin-top:8px;">
      <div onclick={() => openSection = openSection === section.name ? null : section.name}
           style="padding:10px; cursor:pointer; display:flex; justify-content:space-between;">
        <strong>{section.name}</strong>
        <span>{openSection === section.name ? '−' : '+'}</span>
      </div>

      {#if openSection === section.name}
        <div style="padding:10px; border-top:1px solid var(--pw-border);">
          {#each section.keys as key}
            {@const s = findSetting(key)}
            {#if s}
              <div style="display:flex; align-items:center; padding:6px 0;
 border-bottom:1px solid #1a1a1a; gap:8px;">
                <div style="flex:0 0 35%;">
                  <div style="font-family:monospace; font-size:11px;">{s.key}</div>
                  <div style="font-size:10px; opacity:0.6;">{s.description}</div>
                </div>

                <div style="flex:0 0 30%;">
                  {#if s.type === 'bool'}
                    {@const cur = dirty[s.key] !== undefined ? dirty[s.key] : s.effective_value}
                    <label>
                      <input type="checkbox" checked={cur}
                             onchange={(e) => dirty[s.key] = e.currentTarget.checked} />
                      {cur ? 'on' : 'off'}
                    </label>
                  {:else if s.type === 'enum'}
                    {@const cur = dirty[s.key] !== undefined ? dirty[s.key] : s.effective_value}
                    <select onchange={(e) => dirty[s.key] = e.currentTarget.value}>
                      {#each s.choices as c}
                        <option value={c} selected={cur === c}>{c}</option>
                      {/each}
                    </select>
                  {:else if s.type === 'int'}
                    {@const cur = dirty[s.key] !== undefined ? dirty[s.key] : s.effective_value}
                    <input type="number"
                           value={cur}
                           onchange={(e) => dirty[s.key] = parseInt(e.currentTarget.value)} />
                  {:else if s.type === 'float'}
                    {@const cur = dirty[s.key] !== undefined ? dirty[s.key] : s.effective_value}
                    <input type="number" step="0.01"
                           value={cur}
                           onchange={(e) => dirty[s.key] = parseFloat(e.currentTarget.value)} />
                  {:else}
                    {@const cur = dirty[s.key] !== undefined ? dirty[s.key] : (s.effective_value ?? '')}
                    <input type="text"
                           value={cur}
                           onchange={(e) => dirty[s.key] = e.currentTarget.value} />
                  {/if}
                </div>

                <div style="flex:0 0 25%; font-size:10px; font-family:monospace;">
                  default: {String(s.default ?? '')}
                  {#if s.env_var}<br/>env: {s.env_var}{/if}
                </div>

                <div style="flex:0 0 10%;">
                  <button onclick={() => resetSetting(s.key)}
                          style="font-size:10px;">RESET</button>
                </div>
              </div>
            {/if}
          {/each}

          <button onclick={() => saveSection(section.keys)}
                  disabled={savingSection !== null}
                  style="margin-top:8px;">
            {savingSection ? 'SAVING...' : 'SAVE SECTION'}
          </button>
        </div>
      {/if}
    </div>
  {/each}

{:else if activeTab === 'traces'}
  <!-- ═══════════════════════════════════════════════════════════ -->
  <!-- TRACES / OBSERVABILITY TAB                                   -->
  <!-- ═══════════════════════════════════════════════════════════ -->

  <!-- Filter controls -->
  <div class="flex items-center gap-3 mb-3" style="flex-wrap: wrap;">
    <div class="flex items-center gap-2">
      <span style="font-size: 11px; color: var(--pw-muted); text-transform: uppercase; letter-spacing: 0.04em;">Kind</span>
      <select bind:value={traceKind} onchange={reloadTraces} style="border: 1px solid var(--pw-border); background: var(--pw-bg); padding: 5px 10px; font-size: 12px; font-family: var(--pw-font-body); color: var(--pw-ink);">
        <option value="">all</option>
        <option value="training">training</option>
        <option value="chat">chat</option>
        <option value="cron">cron</option>
        <option value="learning">learning</option>
        <option value="ml">ml</option>
        <option value="task">task</option>
      </select>
    </div>
    <div class="flex items-center gap-2">
      <span style="font-size: 11px; color: var(--pw-muted); text-transform: uppercase; letter-spacing: 0.04em;">Window</span>
      {#each [1, 7, 30] as d}
        <button class="cc-btn-ghost" style={traceDays === d ? 'background: var(--pw-ink); color: var(--pw-bg); border-color: var(--pw-ink);' : ''} onclick={() => { traceDays = d; reloadTraces(); }}>{d}D</button>
      {/each}
    </div>
    <button class="cc-btn-ghost" style="margin-left: auto;" onclick={reloadTraces}>REFRESH</button>
  </div>

  <!-- Rollup strip -->
  {#if traceRollup}
    {@const failed = Number(traceRollup.failed) || 0}
    <div class="ink-border" style="padding: 10px 14px; margin-bottom: 12px; display: flex; flex-wrap: wrap; align-items: center; gap: 14px; font-size: 12px; background: var(--pw-bg-alt);">
      <span><strong>{traceRollup.runs ?? 0}</strong> runs</span>
      <span style="color: var(--pw-muted);">·</span>
      <span style={failed > 0 ? 'color: #c0392b; font-weight: 700;' : 'color: var(--pw-muted);'}>{failed} failed</span>
      <span style="color: var(--pw-muted);">·</span>
      <span style="color: #a06000; font-weight: 600;">{traceCost(traceRollup.cost_usd)}</span>
      {#if traceRollup.by_kind}
        <span style="color: var(--pw-muted);">·</span>
        <span class="flex items-center gap-2" style="flex-wrap: wrap;">
          {#each Object.entries(traceRollup.by_kind) as [k, n]}
            <span style="font-size: 11px;"><span style="color: {traceKindColor(k)}; font-weight: 700;">{k}</span> {n}</span>
          {/each}
        </span>
      {/if}
      {#if traceRollup.slowest?.name}
        <span style="color: var(--pw-muted);">·</span>
        <span style="font-size: 11px; color: var(--pw-muted);">slowest: <strong style="color: var(--pw-ink);">{traceRollup.slowest.name}</strong> ({traceDur(traceRollup.slowest.duration_ms)})</span>
      {/if}
    </div>
  {/if}

  <!-- Cron health -->
  <div class="ink-border" style="padding: 12px 14px; margin-bottom: 12px;">
    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted); margin-bottom: 8px;">Cron Health</div>
    {#if !Array.isArray(traceCrons) || traceCrons.length === 0}
      <div style="font-size: 11px; color: var(--pw-muted);">No scheduled jobs registered.</div>
    {:else}
      <div style="display: flex; flex-direction: column; gap: 4px;">
        {#each traceCrons as c}
          <div class="flex items-center gap-3" style="font-size: 12px;">
            <span style="font-weight: 600; min-width: 200px;">{c?.name || '—'}</span>
            <span style="color: var(--pw-muted); font-size: 11px;">last: {traceTime(c?.last_run)}</span>
            {#if c?.stale}
              <span style="background: rgba(192,57,43,0.12); color: #c0392b; font-size: 10px; font-weight: 700; padding: 2px 8px; text-transform: uppercase; letter-spacing: 0.04em;">⚠ never fired / stale</span>
            {:else}
              <span class="cc-status"><span class="cc-dot cc-dot-on"></span>{c?.status || 'ok'}</span>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </div>

  <!-- Trace list -->
  {#if traceLoadError}
    <div class="ink-border" style="padding: 20px; text-align: center; color: var(--pw-muted); font-size: 12px;">Could not load traces. Try refreshing.</div>
  {:else if loading && traces.length === 0}
    <div style="font-size: 11px; color: var(--pw-muted);">Loading…</div>
  {:else if !Array.isArray(traces) || traces.length === 0}
    <div class="ink-border" style="padding: 20px; text-align: center; color: var(--pw-muted); font-size: 12px;">No traces in this window.</div>
  {:else}
    <table class="data-table" style="width: 100%;">
      <thead><tr>
        <th>TIME</th>
        <th>KIND</th>
        <th>NAME / PROJECT</th>
        <th style="text-align: center;">STATUS</th>
        <th style="text-align: right;">DURATION</th>
        <th style="text-align: right;">COST</th>
        <th style="text-align: right;">TOKENS</th>
      </tr></thead>
      <tbody>
        {#each traces as t (t?.trace_id)}
          {@const kids = Array.isArray(t?.children) ? t.children : []}
          <tr style="cursor: pointer;" class:cc-row-active={traceExpanded === t?.trace_id} onclick={() => traceExpanded = (traceExpanded === t?.trace_id ? null : t?.trace_id)}>
            <td style="font-size: 11px; color: var(--pw-muted);">{traceTime(t?.started_at)}</td>
            <td><span style="color: {traceKindColor(t?.kind)}; font-weight: 700; font-size: 11px; text-transform: uppercase;">{t?.kind || '—'}</span></td>
            <td>
              <div style="font-weight: 600;">{kids.length > 0 ? (traceExpanded === t?.trace_id ? '▾ ' : '▸ ') : ''}{t?.name || '—'}</div>
              {#if t?.project_slug}<div style="font-size: 11px; color: var(--pw-muted);">{t.project_slug}</div>{/if}
              {#if t?.error}<div style="font-size: 11px; color: #c0392b;">{t.error}</div>{/if}
            </td>
            <td style="text-align: center;"><span style="color: {traceStatusColor(t?.status)}; font-weight: 700;" title={t?.status}>{traceStatusGlyph(t?.status)}</span></td>
            <td style="text-align: right; font-size: 11px;">{traceDur(t?.duration_ms)}</td>
            <td style="text-align: right; font-size: 11px; color: #a06000;">{traceCost(t?.cost_usd)}</td>
            <td style="text-align: right; font-size: 11px;">{t?.tokens ?? '-'}</td>
          </tr>
          {#if traceExpanded === t?.trace_id && kids.length > 0}
            <tr>
              <td colspan="7" style="background: var(--pw-bg-alt); padding: 8px 14px 8px 28px;">
                <div style="display: flex; flex-direction: column; gap: 5px;">
                  {#each kids as c}
                    <div style="display: flex; align-items: baseline; gap: 8px; font-size: 12px;">
                      <span style="color: {traceStatusColor(c?.status)}; font-weight: 700;" title={c?.status}>{traceStatusGlyph(c?.status)}</span>
                      <span style="font-weight: 500;">{c?.name || '—'}</span>
                      {#if c?.kind}<span style="font-size: 10px; color: {traceKindColor(c.kind)}; text-transform: uppercase;">{c.kind}</span>{/if}
                      <span style="margin-left: auto; font-size: 11px; color: var(--pw-muted);">
                        {traceDur(c?.duration_ms)}
                        {#if Number(c?.cost_usd) > 0}· <span style="color: #a06000;">{traceCost(c.cost_usd)}</span>{/if}
                        {#if c?.tokens}· {c.tokens} tok{/if}
                      </span>
                    </div>
                    {#if c?.error}
                      <div style="font-size: 11px; color: #c0392b; padding-left: 24px;">{c.error}</div>
                    {/if}
                  {/each}
                </div>
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>

    <!-- Per-agent rollup -->
    {#if Array.isArray(traceAgents) && traceAgents.length > 0}
      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted); margin: 18px 0 8px;">Agents (last {Math.max(traceDays, 7)}d)</div>
      <table class="data-table" style="width: 100%;">
        <thead><tr>
          <th>AGENT</th>
          <th style="text-align: right;">CALLS</th>
          <th style="text-align: right;">COST</th>
          <th style="text-align: right;">AVG</th>
          <th style="text-align: right;">ERRORS</th>
        </tr></thead>
        <tbody>
          {#each traceAgents as a}
            <tr>
              <td style="font-weight: 600;">{a?.agent || '—'}</td>
              <td style="text-align: right; font-size: 11px;">{a?.calls ?? 0}</td>
              <td style="text-align: right; font-size: 11px; color: #a06000;">{traceCost(a?.cost_usd)}</td>
              <td style="text-align: right; font-size: 11px;">{traceDur(a?.avg_ms)}</td>
              <td style="text-align: right; font-size: 11px; {Number(a?.errors) > 0 ? 'color: #c0392b; font-weight: 700;' : 'color: var(--pw-muted);'}">{a?.errors ?? 0}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}

{:else if activeTab === 'accuracy'}
  <AccuracyPanel />

{:else if activeTab === 'golden'}
  <GoldenPanel />

{:else if activeTab === 'scope-audit'}
  <ScopeAuditPanel />

{:else if activeTab === 'llm'}
  <LLMConfigPanel />

{:else if activeTab === 'cockpit'}
  {@const _hb = secBadge('health')}
  {@const _sb = secBadge('stats')}
  {@const _ob = secBadge('obs')}
  <div class="ccc-kpis">
    <div class="ccc-kpi"><span class="ccc-n">{cockpit.metrics?.users ?? '—'}</span><span class="ccc-l">Users</span></div>
    <div class="ccc-kpi"><span class="ccc-n">{cockpit.metrics?.projects ?? '—'}</span><span class="ccc-l">Projects</span></div>
    <div class="ccc-kpi"><span class="ccc-n">{cockpit.metrics?.chats ?? '—'}</span><span class="ccc-l">Chats</span></div>
    <div class="ccc-kpi"><span class="ccc-n">{cockpit.metrics?.brain_entries ?? '—'}</span><span class="ccc-l">Brain</span></div>
    <div class="ccc-kpi"><span class="ccc-n">{cockpit.gw?.totals?.calls ?? '—'}</span><span class="ccc-l">GW calls 7d</span></div>
  </div>
  <section class="ccc-panel">
    <div class="ccc-h-row">
      <div class="ccc-h">⊕ BUILD &amp; RELEASE</div>
    </div>
    <VersionCard />
  </section>
  <section class="ccc-panel">
    <div class="ccc-h-row">
      <div class="ccc-h">① SYSTEM HEALTH</div>
      <span class="cc-sb"><i class="cc-sb-dot {_hb.dot}"></i>{_hb.txt}</span>
    </div>
    <div class="ccc-health">
      <span class="ccc-hi"><i class="ccc-dot" class:ok={cockpit.health?.status === 'ok'}></i>API <b>{cockpit.health?.status || '—'}</b></span>
      <span class="ccc-hi"><i class="ccc-dot" class:ok={cockpit.health?.db === 'connected'}></i>DB <b>{cockpit.health?.db || '—'}</b></span>
      <span class="ccc-hi"><i class="ccc-dot" class:warn={cockpit.health?.staleness_warning} class:ok={!cockpit.health?.staleness_warning}></i>Image <b>{cockpit.health?.staleness_warning ? 'stale' : 'fresh'}</b></span>
      <span class="ccc-hi"><i class="ccc-dot" class:ok={cockpit.daemons?.daemons_should_run_on_this_worker}></i>Daemons <b>{cockpit.daemons?.daemons_should_run_on_this_worker ? 'leader' : `w${cockpit.daemons?.worker_rank ?? '?'}`}</b></span>
      {#if cockpit.health?.workers}<span class="ccc-hi"><i class="ccc-dot ok"></i>Workers <b>{cockpit.health.workers}</b></span>{/if}
      {#if cockpit.health?.redis}<span class="ccc-hi"><i class="ccc-dot" class:ok={cockpit.health.redis === 'ok' || cockpit.health.redis === 'connected'}></i>Redis <b>{cockpit.health.redis}</b></span>{/if}
    </div>
  </section>
  <section class="ccc-panel">
    <div class="ccc-h-row">
      <div class="ccc-h">② INTEGRATIONS</div>
      <button class="ccc-save" disabled={!integDirty || integSaving} onclick={saveIntegrations}>
        {integSaving ? 'Saving…' : 'Save'}
      </button>
    </div>
    <div class="ccc-toggles">
      <div class="ccc-toggle-row">
        <div class="ccc-toggle-lbl">
          <span class="ccc-toggle-name">🔑 API Gateway</span>
          <span class="ccc-toggle-sub">OpenAI-compatible REST /api/v1 — {integView('gateway_enabled') ? 'live' : 'disabled (routes 403, hidden from nav)'}</span>
        </div>
        <button class="ccc-switch" class:on={integView('gateway_enabled')}
                onclick={() => toggleIntegration('gateway_enabled')} aria-label="Toggle API Gateway">
          <span class="ccc-knob"></span>
        </button>
      </div>
      <div class="ccc-toggle-row">
        <div class="ccc-toggle-lbl">
          <span class="ccc-toggle-name">&lt;/&gt; Embed</span>
          <span class="ccc-toggle-sub">Chat widget for external sites — {integView('embed_enabled') ? 'live' : 'disabled (routes 403, hidden from nav)'}</span>
        </div>
        <button class="ccc-switch" class:on={integView('embed_enabled')}
                onclick={() => toggleIntegration('embed_enabled')} aria-label="Toggle Embed">
          <span class="ccc-knob"></span>
        </button>
      </div>
      {#if integDirty}<div class="ccc-toggle-hint">Unsaved — click Save to apply (page reloads so the nav updates).</div>{/if}
    </div>
  </section>

  <!-- ③ PLATFORM STATS (folded from the Platform-stats tab) -->
  <section class="ccc-panel">
    <div class="ccc-h-row">
      <div class="ccc-h">③ PLATFORM STATS</div>
      <span class="cc-sb"><i class="cc-sb-dot {_sb.dot}"></i>{_sb.txt}</span>
    </div>
    {#if stats}
      <div class="ccc-statgrid">
        {#each [
          { l: 'Users', v: stats.users ?? stats.stats?.users ?? '—' },
          { l: 'Projects', v: stats.projects ?? stats.stats?.projects ?? '—' },
          { l: 'Sessions', v: stats.sessions ?? stats.stats?.sessions ?? '—' },
          { l: 'Tables', v: stats.tables ?? stats.stats?.table_count ?? stats.table_count ?? '—' },
          { l: 'Total rows', v: (() => { const n = stats.total_rows ?? stats.stats?.total_rows; return typeof n === 'number' ? n.toLocaleString() : (n ?? '—'); })() },
          { l: 'Knowledge', v: stats.knowledge_vectors ?? stats.stats?.knowledge_vectors ?? '—' },
          { l: 'Memories', v: stats.memories ?? stats.stats?.memories ?? '—' },
          { l: 'Training runs', v: stats.training_runs ?? stats.stats?.training_runs ?? '—' },
        ] as c}
          <div class="ccc-stat"><span class="ccc-stat-v">{c.v}</span><span class="ccc-stat-l">{c.l}</span></div>
        {/each}
      </div>
    {:else}
      <div class="ccc-muted">stats unavailable</div>
    {/if}
  </section>

  <!-- ④ OBSERVABILITY (folded from the Observability tab) -->
  <section class="ccc-panel">
    <div class="ccc-h-row">
      <div class="ccc-h">④ OBSERVABILITY</div>
      <span class="cc-sb"><i class="cc-sb-dot {_ob.dot}"></i>{_ob.txt}</span>
    </div>
    {#if traceRollup}
      <div class="ccc-health">
        <span class="ccc-hi"><i class="ccc-dot ok"></i>Runs <b>{traceRollup.runs ?? 0}</b></span>
        <span class="ccc-hi"><i class="ccc-dot" class:warn={Number(traceRollup.failed) > 0} class:ok={!Number(traceRollup.failed)}></i>Errors <b>{traceRollup.failed ?? 0}</b></span>
        <span class="ccc-hi"><i class="ccc-dot" class:ok={traceCrons.length === 0 || traceCrons.every((c) => c.ok !== false)}></i>Cron <b>{traceCrons.length === 0 ? '—' : (traceCrons.every((c) => c.ok !== false) ? 'ok' : 'issue')}</b></span>
        {#if traceRollup.cost_usd != null}<span class="ccc-hi"><i class="ccc-dot ok"></i>Cost <b>{traceCost(traceRollup.cost_usd)}</b></span>{/if}
        {#if traceRollup.slowest?.name}<span class="ccc-hi"><i class="ccc-dot"></i>Slowest <b>{traceRollup.slowest.name}</b> ({traceDur(traceRollup.slowest.duration_ms)})</span>{/if}
      </div>
      {#if traceAgents.length}
        <div class="ccc-agents">
          {#each traceAgents.slice(0, 5) as a}
            <span class="ccc-agent"><b>{a.agent || a.name || '—'}</b> {a.runs ?? a.count ?? 0}</span>
          {/each}
        </div>
      {/if}
      <div class="ccc-jump" style="margin-top:8px;"><button onclick={() => switchTab('traces')}>open full Traces →</button></div>
    {:else}
      <div class="ccc-muted">{traceLoadError ? 'traces unavailable' : 'no traces yet'}</div>
    {/if}
  </section>

  <!-- ⑤ JUMP TO -->
  <section class="ccc-panel">
    <div class="ccc-h">⑤ JUMP TO</div>
    <div class="ccc-jump">
      <button onclick={() => switchTab('brain')}>🧠 Brain</button>
      <button onclick={() => switchTab('llm')}>🤖 Models</button>
      <button onclick={() => switchTab('integrations')}>🔌 Integrations</button>
      <button onclick={() => switchTab('auth')}>🔒 Authentication</button>
    </div>
  </section>

{:else if activeTab === 'gateway'}
  <GatewayPanel embedded />

{:else if activeTab === 'auth'}
  <AuthAdminPanel embedded />

{:else if activeTab === 'observability'}
  <ObservabilityPanel embedded />

{/if}

</div>
  </main>
</div>

<!-- USER DRAWER -->
{#if drawerUserId}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="cc-drawer-backdrop" onclick={closeUserDrawer}></div>
<aside class="cc-drawer" role="dialog" aria-label="User detail">
  <div class="cc-drawer-header">
    <div style="display: flex; align-items: center; gap: 12px; min-width: 0;">
      <div style="background: rgba(201, 99, 66, 0.12); color: var(--pw-accent); width: 36px; height: 36px; border-radius: 0; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 13px; flex-shrink: 0;">
        {(drawerUserRow?.username || '?').charAt(0).toUpperCase()}
      </div>
      <div style="min-width: 0;">
        <div style="font-family: var(--pw-font-serif, 'Source Serif 4', Georgia, serif); font-size: 16px; font-weight: 600; color: var(--pw-ink); line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
          {drawerUserRow?.first_name && drawerUserRow?.last_name ? `${drawerUserRow.first_name} ${drawerUserRow.last_name}` : (drawerUserRow?.username || drawerUserId)}
        </div>
        {#if drawerUserRow?.email}<div style="font-size: 11px; color: var(--pw-muted);">{drawerUserRow.email}</div>{/if}
      </div>
    </div>
    <button class="cc-drawer-close" onclick={closeUserDrawer} aria-label="Close">&times;</button>
  </div>
  <div class="cc-drawer-body">
    {#if loadingUserDetail}
      <div style="font-size: 11px; color: var(--pw-muted);">Loading…</div>
    {:else if userDetail}
      <div class="cc-drawer-section">
        <div class="cc-drawer-section-title">Owned Projects</div>
        {#if userDetail.owned_projects?.length}
          {#each userDetail.owned_projects as p}
            <div style="font-size: 12px; margin-bottom: 6px;"><span style="font-weight: 600;">{p.agent_name || p.name || p.slug}</span> <span style="color: var(--pw-muted); font-size: 11px; margin-left: 4px;">{p.slug}</span></div>
          {/each}
        {:else}<div style="font-size: 11px; color: var(--pw-muted);">None</div>{/if}
      </div>
      <div class="cc-drawer-section">
        <div class="cc-drawer-section-title">Shared With User</div>
        {#if userDetail.shared_projects?.length}
          {#each userDetail.shared_projects as p}
            <div style="font-size: 12px; margin-bottom: 6px;"><span style="font-weight: 600;">{p.agent_name || p.name || p.slug}</span> <span style="color: var(--pw-muted); font-size: 11px; margin-left: 6px;">{p.role || 'viewer'}</span></div>
          {/each}
        {:else}<div style="font-size: 11px; color: var(--pw-muted);">None</div>{/if}
      </div>
      <div class="cc-drawer-section">
        <div class="cc-drawer-section-title">Recent Activity</div>
        {#if userDetail.recent_activity?.length}
          {#each userDetail.recent_activity.slice(0, 8) as a}
            <div style="font-size: 11px; margin-bottom: 4px;">
              <span style="font-weight: 600;">{a.action || a.event}</span>
              <span style="color: var(--pw-muted); margin-left: 6px; font-size: 11px;">{fmtDate(a.created_at || a.timestamp)}</span>
            </div>
          {/each}
        {:else}<div style="font-size: 11px; color: var(--pw-muted);">No recent activity</div>{/if}
      </div>
      <div class="cc-drawer-section">
        <div class="cc-drawer-section-title">Feedback Stats</div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
          <div style="padding: 12px; text-align: center; background: var(--pw-bg-alt); border: 1px solid var(--pw-border); border-radius: 0;">
            <div style="font-size: 19px; font-weight: 600;">{userDetail.feedback?.good ?? userDetail.feedback_good ?? 0}</div>
            <div style="font-size: 10px; text-transform: uppercase; color: var(--pw-muted); letter-spacing: 0.04em;">Good</div>
          </div>
          <div style="padding: 12px; text-align: center; background: var(--pw-bg-alt); border: 1px solid var(--pw-border); border-radius: 0;">
            <div style="font-size: 19px; font-weight: 600;">{userDetail.feedback?.bad ?? userDetail.feedback_bad ?? 0}</div>
            <div style="font-size: 10px; text-transform: uppercase; color: var(--pw-muted); letter-spacing: 0.04em;">Bad</div>
          </div>
        </div>
      </div>
    {:else}
      <div style="font-size: 11px; color: var(--pw-muted);">No detail available.</div>
    {/if}
  </div>
</aside>
{/if}

<!-- CHAT DRAWER -->
{#if drawerChatId && drawerChatRow}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="cc-drawer-backdrop" onclick={closeChatDrawer}></div>
<aside class="cc-drawer" role="dialog" aria-label="Chat detail">
  <div class="cc-drawer-header">
    <div style="display: flex; align-items: center; gap: 12px; min-width: 0;">
      <div style="background: rgba(201, 99, 66, 0.12); color: var(--pw-accent); width: 36px; height: 36px; border-radius: 0; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 13px; flex-shrink: 0;"><Icon name="message-circle" size={14} /></div>
      <div style="min-width: 0;">
        <div style="font-family: var(--pw-font-serif, 'Source Serif 4', Georgia, serif); font-size: 16px; font-weight: 600; color: var(--pw-ink); line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
          Session {drawerChatRow.session_id?.slice(0, 12)}…
        </div>
        <div style="font-size: 11px; color: var(--pw-muted);">{drawerChatRow.user_id || drawerChatRow.username || 'anonymous'} · {drawerChatRow.project || drawerChatRow.project_slug || '—'}</div>
      </div>
    </div>
    <button class="cc-drawer-close" onclick={closeChatDrawer} aria-label="Close">&times;</button>
  </div>
  <div class="cc-drawer-body">
    <div class="cc-drawer-section">
      <div class="cc-drawer-section-title">Metadata</div>
      <div style="display: grid; grid-template-columns: 120px 1fr; gap: 6px 12px; font-size: 11px;">
        <span style="color: var(--pw-muted);">Session ID</span><span style="font-family: var(--pw-font-body); font-size: 11px;">{drawerChatRow.session_id}</span>
        <span style="color: var(--pw-muted);">Messages</span><span>{drawerChatRow.message_count ?? drawerChatRow.messages?.length ?? '—'}</span>
        <span style="color: var(--pw-muted);">Model</span><span>{drawerChatRow.chat_model || drawerChatRow.model || '—'}</span>
        <span style="color: var(--pw-muted);">Duration</span><span>{fmtDuration(drawerChatRow.created_at, drawerChatRow.updated_at || drawerChatRow.last_message_at)}</span>
        <span style="color: var(--pw-muted);">Created</span><span>{fmtDate(drawerChatRow.created_at)}</span>
        {#if drawerChatRow.updated_at}<span style="color: var(--pw-muted);">Last Active</span><span>{fmtDate(drawerChatRow.updated_at)}</span>{/if}
      </div>
    </div>
    <div class="cc-drawer-section">
      <div class="cc-drawer-section-title">Full Conversation</div>
      {#if drawerChatRow.messages?.length}
        {#each drawerChatRow.messages as msg}
          <div style="font-size: 11px; margin-bottom: 10px; padding: 8px 10px; border-left: 2px solid {msg.role === 'user' ? 'var(--pw-accent)' : 'var(--pw-border)'}; background: var(--pw-bg-alt); border-radius: 0;">
            <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 4px;">
              <span style="font-weight: 600; text-transform: uppercase; font-size: 10px; color: {msg.role === 'user' ? 'var(--pw-accent)' : 'var(--pw-muted)'};">{msg.role || 'user'}</span>
              <span style="color: var(--pw-muted); font-size: 10px;">{fmtDate(msg.created_at)}</span>
            </div>
            <div style="white-space: pre-wrap; line-height: 1.45;">{msg.content || msg.message || ''}</div>
          </div>
        {/each}
      {:else}
        <div style="font-size: 11px; color: var(--pw-muted);">{drawerChatRow.first_message || drawerChatRow.message || 'No messages available.'}</div>
      {/if}
    </div>
  </div>
</aside>
{/if}

<!-- Create User Modal -->
{#if showCreateUser}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div style="position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 200; display: flex; align-items: center; justify-content: center;" onclick={(e: MouseEvent) => { if (e.target === e.currentTarget) showCreateUser = false; }}>
  <div class="ink-border stamp-shadow" style="background: var(--pw-bg); width: 400px;">
    <div class="dark-title-bar flex items-center justify-between" style="padding: 8px 14px; font-size: 11px;">
      <span>CREATE USER</span>
      <button onclick={() => showCreateUser = false} style="background: none; border: none; color: var(--pw-bg); cursor: pointer; font-weight: 900;">&#10005;</button>
    </div>
    <div style="padding: 16px;">
      <div style="margin-bottom: 10px;">
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">USERNAME *</div>
        <input type="text" bind:value={newUsername} placeholder="username" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg);" />
      </div>
      <div style="margin-bottom: 10px;">
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">PASSWORD *</div>
        <input type="password" bind:value={newPassword} placeholder="min 4 characters" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg);" />
      </div>
      <div style="margin-bottom: 12px;">
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">EMAIL</div>
        <input type="email" bind:value={newEmail} placeholder="user@company.com" style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg);" />
      </div>
      <button class="send-btn" onclick={createUser} disabled={!newUsername || !newPassword} style="width: 100%; padding: 8px; font-size: 11px; justify-content: center; display: flex;">CREATE USER</button>
    </div>
  </div>
</div>
{/if}

<style>
 :global(.pill-tabs) {
 display: inline-flex;
 gap: 6px;
 background: var(--pw-bg-alt, #f1ede4);
 padding: 4px;
 border-radius: 0;
 border: 1px solid var(--pw-border, #e7e3da);
 }
 :global(.pill-tab) {
 background: none;
 border: 1px solid transparent;
 border-radius: 0;
 padding: 8px 22px;
 cursor: pointer;
 font: 600 11px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-ink-soft, #87837a);
 transition: all 0.15s;
 display: inline-flex;
 align-items: center;
 gap: 6px;
 }
 :global(.pill-tab:hover) {
 background: rgba(255, 255, 255, 0.5);
 color: var(--pw-ink, #2c2a26);
 }
 :global(.pill-tab.active) {
 background: var(--pw-ink, #2c2a26);
 color: #fff;
 border-color: var(--pw-ink, #2c2a26);
 }
 :global(.pill-count) {
 background: rgba(255, 255, 255, 0.2);
 color: inherit;
 border-radius: 0;
 padding: 1px 7px;
 font-size: 10px;
 font-weight: 700;
 }
 :global(.pill-tab:not(.active) .pill-count) {
 background: var(--pw-surface, #faf9f5);
 color: var(--pw-ink-soft, #87837a);
 }

 :global(.cc-shell) {
 display: grid;
 grid-template-columns: 220px 1fr;
 background: var(--pw-bg);
 height: calc(100vh - 64px);
 min-height: 0;
 overflow: hidden;
 font-family: var(--pw-font-body, 'Inter', system-ui, sans-serif);
 color: var(--pw-ink);
 }

 :global(.cc-rail) {
 align-self: stretch;
 height: 100%;
 min-height: 0;
 overflow-y: auto;
 overscroll-behavior: contain;
 background: var(--pw-bg-alt);
 border-right: 1px solid var(--pw-border);
 padding: 0 8px 120px;
 }
 :global(.cc-rail .rail-group-label) {
   font-size: 11px;
   text-transform: uppercase;
   letter-spacing: 0.06em;
   color: var(--pw-muted);
   padding: 12px 14px 6px;
   font-weight: 600;
 }
 :global(.cc-rail .rail-subgroup-label) {
   font-size: 10px;
   text-transform: uppercase;
   letter-spacing: 0.05em;
   color: var(--pw-muted);
   padding: 10px 14px 4px;
   font-weight: 600;
   opacity: 0.85;
 }
 :global(.cc-rail::-webkit-scrollbar) { width: 6px; }
 :global(.cc-rail::-webkit-scrollbar-thumb) { background: var(--pw-border); border-radius: 3px; }
 :global(.cc-rail::-webkit-scrollbar-track) { background: transparent; }

 :global(.cc-rail-group) { display: flex; flex-direction: column; gap: 2px; margin-bottom: 4px; }
 :global(.cc-rail-grouplabel) {
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-muted);
 padding: 12px 14px 6px;
 font-weight: 600;
 }
 :global(.cc-rail .rail-group) {
 display: flex;
 align-items: center;
 justify-content: space-between;
 width: 100%;
 background: transparent;
 border: none;
 padding: 12px 14px 6px;
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-muted);
 font-weight: 600;
 font-family: inherit;
 cursor: pointer;
 text-align: left;
 }
 :global(.cc-rail .rail-group:hover) { background: rgba(201,99,66,0.04); }
 :global(.cc-rail .rail-group .caret) { font-size: 9px; color: var(--pw-muted); }
 :global(.cc-rail-btn) {
 position: relative;
 display: flex;
 align-items: center;
 gap: 10px;
 width: 100%;
 text-align: left;
 background: transparent;
 border: none;
 padding: 8px 12px;
 border-radius: 8px;
 font-size: 12px;
 color: var(--pw-ink);
 font-family: inherit;
 cursor: pointer;
 line-height: 1.3;
 transition: background .15s ease, color .15s ease, transform .12s ease;
 }
 :global(.cc-rail-btn svg) { width: 14px; height: 14px; flex: 0 0 auto; color: var(--pw-muted); transition: color .15s ease; }
 :global(.cc-rail-btn:hover) { background: rgba(201, 99, 66, 0.06); color: var(--pw-ink); }
 :global(.cc-rail-btn:active) { transform: translateY(.5px); }
 :global(.cc-rail-btn.active) {
 background: #fff;
 color: var(--pw-accent);
 font-weight: 600;
 box-shadow: 0 1px 3px rgba(201,99,66,.08), 0 0 0 1px rgba(201,99,66,.14);
 }
 :global(.cc-rail-btn.active::before) {
 content: '';
 position: absolute;
 left: 0;
 top: 50%;
 transform: translateY(-50%);
 width: 3px;
 height: 60%;
 border-radius: 3px;
 background: linear-gradient(180deg, #c96342, var(--pw-accent));
 }
 :global(.cc-rail-btn.active svg) { color: var(--pw-accent); }

 /* Expandable parents + indented children (Governance group) */
 :global(.cc-rail .rail-parent) {
   display: flex; align-items: center; justify-content: space-between;
   width: 100%; text-align: left;
   background: transparent; border: none;
   padding: 8px 12px; border-radius: 0;
   font-size: 12px; color: var(--pw-ink);
   font-family: inherit; cursor: pointer;
   border-left: 2px solid transparent;
   font-weight: 500;
 }
 :global(.cc-rail .rail-parent:hover) { background: rgba(201,99,66,0.04); }
 :global(.cc-rail .rail-parent.active-parent) { color: var(--pw-accent); font-weight: 600; }
 :global(.cc-rail .rail-parent .caret) { font-size: 9px; color: var(--pw-muted); margin-left: 8px; }
 :global(.cc-rail .rail-child) {
   display: block; width: 100%; text-align: left;
   background: transparent; border: none;
   padding: 6px 12px 6px 28px;
   border-radius: 0;
   font-size: 12px; color: var(--pw-ink-soft);
   font-family: inherit; cursor: pointer;
   border-left: 2px solid transparent;
   line-height: 1.3;
 }
 :global(.cc-rail .rail-child:hover) { background: rgba(201,99,66,0.04); color: var(--pw-ink); }
 :global(.cc-rail .rail-child.active) {
   background: rgba(201,99,66,0.08);
   color: var(--pw-accent);
   font-weight: 600;
 }
 :global(.cc-rail .rail-sep) {
   border: 0;
   border-top: 1px solid var(--border, #e5e7eb);
   margin: 8px 12px;
   opacity: 0.6;
 }

 :global(.cc-main) {
 padding: 32px 48px 80px 48px;
 max-width: 1280px;
 margin: 0 auto;
 width: 100%;
 min-height: 0;
 overflow-y: auto;
 overscroll-behavior: contain;
 box-sizing: border-box;
 }
 @media (max-width: 1024px) {
 :global(.cc-main) { padding: 24px; }
 }
 @media (max-width: 640px) {
 :global(.cc-shell) { grid-template-columns: 1fr; }
 :global(.cc-rail) { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--pw-border); }
 :global(.cc-main) { padding: 16px; }
 }

 :global(.cc-head) { margin-bottom: 24px; }
 :global(.cc-title) {
 font-family: var(--pw-font-serif, 'Source Serif 4', Georgia, serif);
 font-size: 24px;
 font-weight: 600;
 margin: 0 0 4px 0;
 color: var(--pw-ink);
 letter-spacing: -0.01em;
 }
 :global(.cc-subtitle) {
 font-size: 12px;
 color: var(--pw-muted);
 margin: 0;
 }

 :global(.cc-panel) { background: transparent; }

 /* Hide legacy "$ dash admin --xyz" CLI strips inside the command center */
 :global(.cc-shell .cli-terminal) { display: none !important; }
 /* Hide the in-tab brutalist <div> that says "USERS / PROJECTS" since we have a serif H1 */
 :global(.cc-shell .cc-panel > .flex > div[style*="text-transform: uppercase"]) { text-transform: none !important; font-family: var(--pw-font-serif, serif) !important; font-weight: 600 !important; font-size: 13px !important; color: var(--pw-muted) !important; }

 /* Tables: continuous cream-alt header, no chip cells */
 :global(.cc-shell .data-table) {
 width: 100%;
 border-collapse: separate;
 border-spacing: 0;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 overflow: hidden;
 font-size: 12px;
 }
 :global(.cc-shell .data-table thead th) {
 background: var(--pw-bg-alt);
 color: var(--pw-muted);
 font-size: 11.5px;
 text-transform: uppercase;
 letter-spacing: 0.05em;
 font-weight: 600;
 padding: 10px 14px;
 text-align: left;
 border-bottom: 1px solid var(--pw-border);
 border-radius: 0;
 }
 :global(.cc-shell .data-table thead th:first-child) { border-top-left-radius: 8px; }
 :global(.cc-shell .data-table thead th:last-child) { border-top-right-radius: 8px; }
 :global(.cc-shell .data-table tbody td) {
 padding: 10px 14px;
 border-bottom: 1px solid var(--pw-border);
 border-radius: 0;
 font-size: 12px;
 color: var(--pw-ink);
 }
 :global(.cc-shell .data-table tbody tr:last-child td) { border-bottom: none; }
 :global(.cc-shell .data-table tbody tr:hover) { background: var(--pw-bg-alt); }

 /* Status: dot + text */
 :global(.cc-status) { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--pw-ink); }
 :global(.cc-dot) { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
 :global(.cc-dot-on) { background: var(--pw-success, #2e7d32); }
 :global(.cc-dot-off) { background: var(--pw-muted, #999); }
 :global(.cc-dot-warn){ background: var(--pw-error, #c0392b); }

 /* Role */
 :global(.cc-role-text) { font-size: 12.5px; color: var(--pw-ink); }
 :global(.cc-role-pill) {
 display: inline-block;
 padding: 2px 8px;
 font-size: 11px;
 background: rgba(201, 99, 66, 0.12);
 color: var(--pw-accent);
 border-radius: 0;
 font-weight: 600;
 border: none;
 }

 /* Force all .send-btn inside Command Center to be rounded-rectangle, not pill */
 :global(.cc-shell .send-btn) {
 border-radius: 0!important;
 background: var(--pw-accent-soft-cta, #d97757) !important;
 border: 1px solid var(--pw-accent-soft-cta, #d97757) !important;
 color: #fff !important;
 padding: 6px 14px !important;
 font-size: 11px !important;
 font-weight: 500 !important;
 text-transform: none !important;
 letter-spacing: 0 !important;
 height: auto !important;
 line-height: 1.4 !important;
 font-family: inherit !important;
 box-shadow: none !important;
 transform: none !important;
 transition: background 0.12s, border-color 0.12s !important;
 }
 :global(.cc-shell .send-btn:hover:not(:disabled)) {
 background: var(--pw-accent, #c96342) !important;
 border-color: var(--pw-accent, #c96342) !important;
 transform: none !important;
 box-shadow: none !important;
 }
 :global(.cc-shell .send-btn:disabled) {
 opacity: 0.5 !important;
 }

 /* Warm rounded-rectangle ghost button (REFRESH etc) */
 :global(.cc-btn-ghost) {
 background: var(--pw-bg-alt);
 border: 1px solid var(--pw-border);
 color: var(--pw-ink);
 height: 36px;
 padding: 0 14px;
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 border-radius: 0;
 font-family: inherit;
 cursor: pointer;
 font-weight: 500;
 transition: background 0.12s ease, border-color 0.12s ease;
 display: inline-flex;
 align-items: center;
 gap: 6px;
 }
 :global(.cc-btn-ghost:hover) { background: var(--pw-surface); }

 /* Drawer */
 :global(.cc-drawer-backdrop) {
 position: fixed;
 inset: 0;
 background: rgba(0, 0, 0, 0.18);
 z-index: 250;
 }
 :global(.cc-drawer) {
 position: fixed;
 right: 0;
 top: 56px;
 width: 480px;
 max-width: 100vw;
 height: calc(100vh - 56px);
 background: var(--pw-surface);
 border-left: 1px solid var(--pw-border);
 box-shadow: -8px 0 24px rgba(0, 0, 0, 0.06);
 z-index: 260;
 display: flex;
 flex-direction: column;
 animation: cc-drawer-in 0.18s ease-out;
 }
 @keyframes cc-drawer-in {
 from { transform: translateX(20px); opacity: 0; }
 to { transform: translateX(0); opacity: 1; }
 }
 :global(.cc-drawer-header) {
 display: flex;
 align-items: center;
 justify-content: space-between;
 padding: 16px 20px;
 border-bottom: 1px solid var(--pw-border);
 flex-shrink: 0;
 gap: 10px;
 }
 :global(.cc-drawer-close) {
 background: none;
 border: none;
 font-size: 19px;
 line-height: 1;
 color: var(--pw-muted);
 cursor: pointer;
 padding: 4px 8px;
 border-radius: 0;
 flex-shrink: 0;
 }
 :global(.cc-drawer-close:hover) { background: var(--pw-bg-alt); color: var(--pw-ink); }
 :global(.cc-drawer-body) {
 flex: 1;
 overflow-y: auto;
 padding: 20px;
 }
 :global(.cc-drawer-section) { margin-bottom: 22px; }
 :global(.cc-drawer-section-title) {
 font-size: 10px;
 font-weight: 600;
 text-transform: uppercase;
 letter-spacing: 0.08em;
 color: var(--pw-muted);
 margin-bottom: 10px;
 padding-bottom: 6px;
 border-bottom: 1px solid var(--pw-border);
 }
 :global(.cc-row-active) { background: rgba(201, 99, 66, 0.05) !important; }

 /* Action verbs */
 :global(.cc-actions) { display: inline-flex; align-items: center; gap: 6px; }
 :global(.cc-link) {
 background: none;
 border: none;
 padding: 0;
 color: var(--pw-accent);
 font-size: 12.5px;
 font-family: inherit;
 cursor: pointer;
 }
 :global(.cc-link:hover) { text-decoration: underline; }
 :global(.cc-link-danger) { color: var(--pw-error, #c0392b); }
 :global(.cc-sep) { color: var(--pw-muted); font-size: 12.5px; }

 /* Soften any remaining ALL CAPS labels in the panel (rail labels + table headers excluded) */
 :global(.cc-shell .cc-panel .tag-label) { text-transform: none; font-weight: 500; }

 /* DS button aliases for legacy command-center buttons */
 :global(.cc-shell .send-btn) {
 background: var(--pw-accent);
 color: #fff;
 border: 1px solid var(--pw-accent);
 border-radius: var(--r-md);
 padding: 7px 14px;
 font-size: var(--fs-sm);
 font-weight: var(--fw-medium);
 font-family: var(--pw-sans);
 cursor: pointer;
 text-transform: none;
 letter-spacing: 0;
 transition: filter 0.12s;
 }
 :global(.cc-shell .send-btn:hover:not(:disabled)) { filter: brightness(0.95); }
 :global(.cc-shell .send-btn:disabled) { opacity: 0.5; cursor: not-allowed; }
 :global(.cc-shell .feedback-btn) {
 background: var(--pw-surface);
 color: var(--pw-ink);
 border: 1px solid var(--pw-border);
 border-radius: var(--r-md);
 padding: 7px 14px;
 font-size: var(--fs-sm);
 font-weight: var(--fw-medium);
 font-family: var(--pw-sans);
 cursor: pointer;
 text-transform: none;
 letter-spacing: 0;
 transition: background 0.12s;
 }
 :global(.cc-shell .feedback-btn:hover:not(:disabled)) { background: var(--pw-bg-alt); }
 :global(.cc-shell .feedback-btn:disabled) { opacity: 0.5; cursor: not-allowed; }
 /* cockpit (folded Super Dashboard) */
 .ccc-kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 18px; }
 .ccc-kpi { border: 1px solid var(--pw-border, #e7e1d6); border-radius: 10px; padding: 18px 16px; background: var(--pw-surface, #fff); display: flex; flex-direction: column; gap: 6px; }
 .ccc-n { font-size: 30px; font-weight: 800; color: var(--pw-ink, #1a1614); line-height: 1; }
 .ccc-l { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #8a8275); font-weight: 600; }
 .ccc-panel { border: 1px solid var(--pw-border, #e7e1d6); border-radius: 10px; padding: 18px 20px; background: var(--pw-surface, #fff); margin-bottom: 16px; }
 .ccc-h { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; color: var(--pw-accent, #c96342); margin-bottom: 14px; }
 .ccc-health { display: flex; flex-wrap: wrap; gap: 22px; }
 .ccc-hi { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; color: var(--pw-ink-soft, #6b6557); }
 .ccc-hi b { color: var(--pw-ink, #1a1614); font-weight: 700; }
 .ccc-dot { width: 8px; height: 8px; border-radius: 50%; background: #c0392b; display: inline-block; }
 .ccc-dot.ok { background: #1f9d55; }
 .ccc-dot.warn { background: #a06000; }
 .ccc-jump { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
 .ccc-jump button { text-align: left; border: 1px solid var(--pw-border, #e7e1d6); border-radius: 8px; padding: 14px 16px; background: var(--pw-bg-alt, #faf7f1); font-size: 14px; font-weight: 600; color: var(--pw-ink, #1a1614); cursor: pointer; transition: background 0.12s, border-color 0.12s; }
 .ccc-jump button:hover { background: rgba(201,99,66,0.06); border-color: var(--pw-accent, #c96342); }
 @media (max-width: 900px) { .ccc-kpis { grid-template-columns: repeat(2, 1fr); } .ccc-jump { grid-template-columns: 1fr; } }
 /* merged-overview: live badges + sections */
 .cc-sb { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--pw-muted, #877f74); font-family: ui-monospace, Menlo, monospace; }
 .cc-sb-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; background: #c0392b; }
 .cc-sb-ok { background: #2d8a4e; }
 .cc-sb-down { background: #c0392b; }
 .cc-sb-load { background: #c08a00; }
 .ccc-statgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; }
 .ccc-stat { border: 1px solid var(--pw-border, #e7e1d6); border-radius: 8px; padding: 10px 12px; background: var(--pw-bg-alt, #faf7f1); text-align: center; }
 .ccc-stat-v { display: block; font-size: 18px; font-weight: 900; color: var(--pw-ink, #1a1614); }
 .ccc-stat-l { display: block; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-muted, #877f74); margin-top: 2px; }
 .ccc-muted { font-size: 12px; color: var(--pw-muted, #877f74); }
 .ccc-agents { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 10px; font-size: 12px; }
 .ccc-agent { color: var(--pw-muted, #877f74); }
 .ccc-agent b { color: var(--pw-ink, #1a1614); }
 .ccc-h-row { display: flex; align-items: center; justify-content: space-between; }
 .ccc-save { font-size: 12px; font-weight: 600; padding: 6px 16px; border-radius: 7px; border: 1px solid var(--pw-accent, #c96342); background: var(--pw-accent, #c96342); color: #fff; cursor: pointer; transition: opacity 0.12s; }
 .ccc-save:disabled { opacity: 0.35; cursor: default; }
 .ccc-toggle-hint { font-size: 11px; color: var(--pw-accent, #c96342); margin-top: 2px; }
 .ccc-toggles { display: flex; flex-direction: column; gap: 14px; }
 .ccc-toggle-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
 .ccc-toggle-lbl { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
 .ccc-toggle-name { font-size: 14px; font-weight: 600; color: var(--pw-ink, #1a1614); }
 .ccc-toggle-sub { font-size: 11px; color: var(--pw-muted, #877f74); }
 .ccc-switch { position: relative; width: 46px; height: 26px; border-radius: 13px; border: none; background: var(--pw-border, #d9d2c6); cursor: pointer; flex-shrink: 0; transition: background 0.15s; padding: 0; }
 .ccc-switch.on { background: var(--pw-accent, #c96342); }
 .ccc-switch:disabled { opacity: 0.5; cursor: wait; }
 .ccc-knob { position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.25); transition: transform 0.15s; }
 .ccc-switch.on .ccc-knob { transform: translateX(20px); }
</style>
