<script lang="ts">
import { onMount } from 'svelte';
import { base } from '$app/paths';

const TOK = typeof window !== 'undefined' ? (localStorage.getItem('dash_token') || '') : '';
const H = () => ({ 'Authorization': `Bearer ${TOK}` });

interface BP { slug: string; name: string; industry: string|null; icon: string|null; description: string|null;
  claim_count: number; policy_count: number; required_tables: string[]; is_system: boolean;
  created_by: number|null; created_at: string|null; popularity: number; }

interface Claim { key: string; label: string; type: 'string'|'number'|'enum'; required: boolean; values?: string[] }
interface Policy { table: string; column: string; mode: 'private'|'own_value'|'shared'|'redacted'|'hidden'; filter: string; bypass_roles: string }

let blueprints = $state<BP[]>([]);
let loading = $state(true);
let editorOpen = $state(false);
let editing = $state<any>(null);  // full BP detail
let isNew = $state(false);
let savingState = $state<'idle'|'saving'|'saved'|'error'>('idle');

async function loadList() {
  loading = true;
  const r = await fetch('/api/embed-rls-blueprints', { headers: H() });
  if (r.ok) {
    const d = await r.json();
    blueprints = (d.blueprints || []) as BP[];
  }
  loading = false;
}

async function openEditor(slug: string) {
  const r = await fetch(`/api/embed-rls-blueprints/${slug}`, { headers: H() });
  if (!r.ok) return;
  const d = await r.json();
  editing = d.blueprint || d;
  editing.claims = editing.claims || [];
  editing.policies = editing.policies || [];
  editing.required_tables = editing.required_tables || [];
  isNew = false;
  editorOpen = true;
}

function openNew() {
  editing = {
    slug: '', name: '', industry: 'custom', icon: '⚙️', description: '',
    claims: [{ key: 'site_id', label: 'Site ID', type: 'string', required: true }],
    policies: [],
    required_tables: [],
    is_system: false, created_by: null,
  };
  isNew = true;
  editorOpen = true;
}

async function save() {
  savingState = 'saving';
  const body = JSON.stringify({
    slug: editing.slug, name: editing.name, industry: editing.industry,
    icon: editing.icon, description: editing.description,
    claims: editing.claims, policies: editing.policies,
    required_tables: editing.required_tables,
  });
  const url = isNew ? '/api/embed-rls-blueprints' : `/api/embed-rls-blueprints/${editing.slug}`;
  const method = isNew ? 'POST' : 'PATCH';
  const r = await fetch(url, { method, headers: { ...H(), 'Content-Type': 'application/json' }, body });
  if (r.ok) {
    savingState = 'saved';
    setTimeout(() => { savingState = 'idle'; }, 2000);
    await loadList();
    if (isNew) { editorOpen = false; }
  } else {
    savingState = 'error';
    const err = await r.text();
    alert('Save failed: ' + err);
  }
}

async function del(slug: string) {
  if (!confirm(`Delete template "${slug}"? This cannot be undone.`)) return;
  const r = await fetch(`/api/embed-rls-blueprints/${slug}`, { method: 'DELETE', headers: H() });
  if (r.ok) await loadList();
  else alert('Delete failed');
}

function addClaim() { editing.claims = [...editing.claims, { key: '', label: '', type: 'string', required: false }]; }
function rmClaim(i: number) { editing.claims.splice(i, 1); editing.claims = [...editing.claims]; }
function addPolicy() { editing.policies = [...editing.policies, { table: '', column: '*', mode: 'private', filter: '', bypass_roles: '' }]; }
function rmPolicy(i: number) { editing.policies.splice(i, 1); editing.policies = [...editing.policies]; }

onMount(loadList);
</script>

<svelte:head><title>Embed Templates — CityAgent Insights</title></svelte:head>

<div style="max-width:1100px; margin:0 auto; padding:24px;">
  <!-- Header -->
  <div style="margin-bottom:18px; padding:14px 18px; background:#1a1614; color:#e8e3d6; font-family:monospace; font-size:13px;">
    <span style="color:#10b981;">$ dash embed-templates list</span>
    <span style="color:#888; margin-left:10px;">— reusable RLS bundles · 1-click apply across embeds</span>
  </div>

  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
    <div>
      <h1 style="font-family:Georgia,serif; font-size:24px; margin:0; color:#1a1614;">Embed Templates</h1>
      <div style="font-size:12px; color:#777; margin-top:4px;">Pre-built claim + policy bundles. Pick one when configuring an embed for instant setup.</div>
    </div>
    <button onclick={openNew}
      style="padding:10px 18px; background:#c96342; border:none; color:#fff; cursor:pointer; font-weight:700; letter-spacing:0.04em; font-size:12px;">+ NEW TEMPLATE</button>
  </div>

  {#if loading}
    <div style="padding:40px; text-align:center; color:#888;">Loading…</div>
  {:else}
    <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:14px;">
      {#each blueprints as bp (bp.slug)}
        <div style="background:#fff; border:1px solid #ddd; padding:16px; display:flex; flex-direction:column; gap:8px;">
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:26px;">{bp.icon || '⚙️'}</span>
            <div style="flex:1;">
              <div style="font-weight:700; color:#1a1614;">{bp.name}</div>
              <div style="font-size:11px; color:#999; font-family:monospace;">{bp.slug}</div>
            </div>
            {#if bp.is_system}
              <span style="font-size:10px; padding:2px 6px; background:#1a1614; color:#fff; font-weight:700;">SYSTEM</span>
            {:else}
              <span style="font-size:10px; padding:2px 6px; background:#10b981; color:#fff; font-weight:700;">CUSTOM</span>
            {/if}
          </div>
          <div style="font-size:12px; color:#555; line-height:1.5; min-height:54px;">{bp.description || '(no description)'}</div>
          <div style="display:flex; gap:10px; font-size:11px; color:#888; padding-top:6px; border-top:1px dashed #eee;">
            <span>🎫 {bp.claim_count} claims</span>
            <span>🔒 {bp.policy_count} policies</span>
            <span>📊 {bp.required_tables.length} tables</span>
          </div>
          <div style="display:flex; gap:6px; margin-top:6px;">
            <button onclick={() => openEditor(bp.slug)}
              style="flex:1; padding:7px; background:#1a1614; border:none; color:#fff; cursor:pointer; font-weight:700; font-size:11px; letter-spacing:0.04em;">✏️ EDIT</button>
            {#if !bp.is_system}
              <button onclick={() => del(bp.slug)}
                style="padding:7px 12px; background:#fff; border:1px solid #c0392b; color:#c0392b; cursor:pointer; font-weight:700; font-size:11px;">DELETE</button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<!-- Editor modal -->
{#if editorOpen && editing}
  <div onclick={() => editorOpen = false}
    style="position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:200; display:flex; align-items:center; justify-content:center;">
    <div onclick={(e: any) => e.stopPropagation()}
      style="background:#f5f1ea; width:900px; max-width:96vw; max-height:92vh; display:flex; flex-direction:column;">
      <div style="padding:14px 20px; border-bottom:1px solid #ddd; display:flex; align-items:center; gap:10px; background:#1a1614; color:#e8e3d6;">
        <span style="font-size:22px;">{editing.icon || '⚙️'}</span>
        <div style="flex:1;">
          <div style="font-weight:700;">{isNew ? 'New Template' : 'Edit ' + editing.name}</div>
          <div style="font-size:11px; color:#aaa; font-family:monospace;">{editing.slug || '(slug will be generated)'}</div>
        </div>
        {#if savingState === 'saved'}<span style="color:#10b981; font-size:12px;">✓ Saved</span>{/if}
        <button onclick={() => editorOpen = false}
          style="padding:6px 12px; background:transparent; border:1px solid #aaa; color:#e8e3d6; cursor:pointer; font-size:11px;">✕</button>
      </div>

      <div style="flex:1; overflow-y:auto; padding:18px 20px;">
        <!-- Identity -->
        <fieldset style="margin-bottom:16px; padding:12px 14px; background:#fff; border:1px solid #ddd;">
          <legend style="font-weight:700; font-size:11px; letter-spacing:0.04em; padding:0 6px; color:#1a1614;">IDENTITY</legend>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <label style="display:flex; flex-direction:column; gap:3px; font-size:11px; color:#666;"><b>Slug (unique id, lowercase)</b>
              <input type="text" bind:value={editing.slug} disabled={!isNew} placeholder="my_custom_template"
                style="padding:6px 8px; border:1px solid #ccc; font-family:monospace; font-size:12px; background:{!isNew ? '#f5f1ea' : '#fff'};" /></label>
            <label style="display:flex; flex-direction:column; gap:3px; font-size:11px; color:#666;"><b>Name (display)</b>
              <input type="text" bind:value={editing.name} placeholder="Pharmacy — Custom Rules"
                style="padding:6px 8px; border:1px solid #ccc; font-size:12px;" /></label>
            <label style="display:flex; flex-direction:column; gap:3px; font-size:11px; color:#666;"><b>Industry</b>
              <input type="text" bind:value={editing.industry} placeholder="pharmacy"
                style="padding:6px 8px; border:1px solid #ccc; font-size:12px;" /></label>
            <label style="display:flex; flex-direction:column; gap:3px; font-size:11px; color:#666;"><b>Icon (emoji)</b>
              <input type="text" bind:value={editing.icon} maxlength="4" placeholder="💊"
                style="padding:6px 8px; border:1px solid #ccc; font-size:18px; text-align:center;" /></label>
            <label style="display:flex; flex-direction:column; gap:3px; font-size:11px; color:#666; grid-column:1/-1;"><b>Description</b>
              <textarea bind:value={editing.description} rows="2" placeholder="Each shop sees own stock. Catalog shared across network."
                style="padding:6px 8px; border:1px solid #ccc; font-size:12px; font-family:inherit; resize:vertical;"></textarea></label>
            <label style="display:flex; flex-direction:column; gap:3px; font-size:11px; color:#666; grid-column:1/-1;"><b>Required tables (comma-separated, used for compatibility check)</b>
              <input type="text" value={editing.required_tables.join(', ')}
                oninput={(e: any) => editing.required_tables = e.target.value.split(',').map((x: string) => x.trim()).filter(Boolean)}
                placeholder="citypharma_articles, citypharma_balance_stock"
                style="padding:6px 8px; border:1px solid #ccc; font-family:monospace; font-size:11px;" /></label>
          </div>
        </fieldset>

        <!-- Claims -->
        <fieldset style="margin-bottom:16px; padding:12px 14px; background:#fff; border:1px solid #ddd;">
          <legend style="font-weight:700; font-size:11px; letter-spacing:0.04em; padding:0 6px; color:#1a1614;">🎫 CLAIMS — fields on user's badge</legend>
          <div style="font-size:11px; color:#777; font-style:italic; margin-bottom:8px;">What your auth system MUST send for each user.</div>
          <div style="display:grid; grid-template-columns:1.2fr 1.2fr 0.8fr 0.6fr 1.5fr 32px; gap:6px; font-size:10px; color:#999; text-transform:uppercase; padding:2px 0;">
            <span>Key</span><span>Label</span><span>Type</span><span>Req</span><span>Values (enum, csv)</span><span></span>
          </div>
          {#each editing.claims as c, i (i)}
            <div style="display:grid; grid-template-columns:1.2fr 1.2fr 0.8fr 0.6fr 1.5fr 32px; gap:6px; align-items:center; padding:3px 0;">
              <input type="text" bind:value={c.key} placeholder="site_id"
                style="padding:5px 8px; border:1px solid #ccc; font-family:monospace; font-size:11px;" />
              <input type="text" bind:value={c.label} placeholder="Site ID"
                style="padding:5px 8px; border:1px solid #ccc; font-size:11px;" />
              <select bind:value={c.type}
                style="padding:5px 8px; border:1px solid #ccc; font-size:11px;">
                <option value="string">string</option>
                <option value="number">number</option>
                <option value="enum">enum</option>
              </select>
              <label style="display:flex; justify-content:center;"><input type="checkbox" bind:checked={c.required} /></label>
              <input type="text" value={(c.values || []).join(',')}
                oninput={(e: any) => c.values = e.target.value.split(',').map((x: string) => x.trim()).filter(Boolean)}
                disabled={c.type !== 'enum'} placeholder={c.type === 'enum' ? 'staff,manager,admin' : '—'}
                style="padding:5px 8px; border:1px solid #ccc; font-family:monospace; font-size:11px; background:{c.type === 'enum' ? '#fff' : '#f5f1ea'};" />
              <button onclick={() => rmClaim(i)}
                style="padding:4px; background:transparent; border:1px solid #ccc; color:#c0392b; cursor:pointer;">🗑</button>
            </div>
          {/each}
          <button onclick={addClaim}
            style="margin-top:6px; padding:5px 12px; background:#1a1614; border:none; color:#fff; cursor:pointer; font-size:10px; font-weight:700;">+ ADD CLAIM</button>
        </fieldset>

        <!-- Policies -->
        <fieldset style="margin-bottom:16px; padding:12px 14px; background:#fff; border:1px solid #ddd;">
          <legend style="font-weight:700; font-size:11px; letter-spacing:0.04em; padding:0 6px; color:#1a1614;">🔒 POLICIES — door locks per table</legend>
          <div style="font-size:11px; color:#777; font-style:italic; margin-bottom:8px;">SHARED = global · PRIVATE = match claim · REDACTED = masked · HIDDEN = column gone</div>
          <div style="display:grid; grid-template-columns:1.4fr 0.9fr 0.9fr 1fr 1.1fr 32px; gap:6px; font-size:10px; color:#999; text-transform:uppercase; padding:2px 0;">
            <span>Table</span><span>Column</span><span>Mode</span><span>Filter (claim)</span><span>Bypass roles</span><span></span>
          </div>
          {#each editing.policies as p, i (i)}
            <div style="display:grid; grid-template-columns:1.4fr 0.9fr 0.9fr 1fr 1.1fr 32px; gap:6px; align-items:center; padding:3px 0;">
              <input type="text" bind:value={p.table} placeholder="citypharma_balance_stock"
                style="padding:5px 8px; border:1px solid #ccc; font-family:monospace; font-size:11px;" />
              <input type="text" bind:value={p.column} placeholder="*"
                style="padding:5px 8px; border:1px solid #ccc; font-family:monospace; font-size:11px;" />
              <select bind:value={p.mode}
                style="padding:5px 8px; border:1px solid #ccc; font-size:11px;">
                <option value="shared">shared</option>
                <option value="private">private</option>
                <option value="own_value">own_value</option>
                <option value="redacted">redacted</option>
                <option value="hidden">hidden</option>
              </select>
              <input type="text" bind:value={p.filter}
                disabled={p.mode !== 'private' && p.mode !== 'own_value'}
                placeholder={(p.mode === 'private' || p.mode === 'own_value') ? 'site_id' : '—'}
                style="padding:5px 8px; border:1px solid #ccc; font-family:monospace; font-size:11px; background:{(p.mode === 'private' || p.mode === 'own_value') ? '#fff' : '#f5f1ea'};" />
              <input type="text" bind:value={p.bypass_roles} placeholder="admin,owner"
                style="padding:5px 8px; border:1px solid #ccc; font-size:11px;" />
              <button onclick={() => rmPolicy(i)}
                style="padding:4px; background:transparent; border:1px solid #ccc; color:#c0392b; cursor:pointer;">🗑</button>
            </div>
          {/each}
          <button onclick={addPolicy}
            style="margin-top:6px; padding:5px 12px; background:#1a1614; border:none; color:#fff; cursor:pointer; font-size:10px; font-weight:700;">+ ADD POLICY</button>
        </fieldset>
      </div>

      <div style="padding:12px 20px; border-top:1px solid #ddd; display:flex; gap:10px; justify-content:flex-end; background:#eae5d8;">
        <button onclick={() => editorOpen = false}
          style="padding:9px 18px; background:#fff; border:1px solid #ccc; color:#1a1614; cursor:pointer; font-weight:700; font-size:11px;">CANCEL</button>
        <button onclick={save} disabled={savingState === 'saving' || !editing.slug || !editing.name}
          style="padding:9px 22px; background:{(editing.slug && editing.name) ? '#10b981' : '#aaa'}; border:none; color:#fff; cursor:{(editing.slug && editing.name) ? 'pointer' : 'not-allowed'}; font-weight:700; font-size:11px; letter-spacing:0.04em;">
          {savingState === 'saving' ? 'SAVING…' : '💾 SAVE'}
        </button>
      </div>
    </div>
  </div>
{/if}
