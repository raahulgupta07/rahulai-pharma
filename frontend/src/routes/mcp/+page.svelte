<script lang="ts">
  import { onMount } from 'svelte';
  import { confirmDelete } from '$lib/confirmDelete';

  let servers = $state<any[]>([]);
  let creating = $state(false);
  let form = $state({ name: '', transport: 'http', url: '', command: '', auth_header: '' });
  const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);

  async function load() {
    const r = await fetch('/api/mcp/servers', { headers: { Authorization: `Bearer ${token() || ''}` } });
    const j = await r.json();
    servers = j?.servers || [];
  }

  async function create() {
    const r = await fetch('/api/mcp/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token() || ''}` },
      body: JSON.stringify(form),
    });
    const j = await r.json();
    if (j.ok) { creating = false; form = { name: '', transport: 'http', url: '', command: '', auth_header: '' }; load(); }
    else alert(`Failed: ${JSON.stringify(j)}`);
  }

  async function test(id: string) {
    const r = await fetch(`/api/mcp/servers/${id}/test`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token() || ''}` },
    });
    const j = await r.json();
    alert(j.ok ? `Connected! ${j.tool_count} tools discovered.` : `Failed: ${j.error}`);
    load();
  }

  async function del(id: string) {
    if (!(await confirmDelete({ itemName: id, itemType: 'MCP server' }))) return;
    await fetch(`/api/mcp/servers/${id}?hard=1`, { method: 'DELETE', headers: { Authorization: `Bearer ${token() || ''}` } });
    load();
  }

  onMount(load);
</script>

<div class="page">
  <header>
    <div>
      <h1>MCP Servers</h1>
      <p class="muted">External Model Context Protocol servers — auto-discovered tools surfaced as Agno @tools.</p>
    </div>
    <button class="primary" onclick={() => (creating = !creating)}>+ new server</button>
  </header>

  {#if creating}
    <div class="form">
      <input bind:value={form.name} placeholder="Name (e.g. Exa Search)" />
      <select bind:value={form.transport}>
        <option value="http">http</option>
        <option value="sse">sse</option>
        <option value="stdio">stdio</option>
      </select>
      {#if form.transport === 'stdio'}
        <input bind:value={form.command} placeholder="Command (e.g. npx -y @modelcontextprotocol/server-exa)" />
      {:else}
        <input bind:value={form.url} placeholder="URL (https://...)" />
      {/if}
      <input bind:value={form.auth_header} placeholder="Auth header (e.g. Authorization: Bearer xxx)" />
      <button class="primary" onclick={create}>create + discover</button>
      <button class="ghost" onclick={() => (creating = false)}>cancel</button>
    </div>
  {/if}

  <table class="tbl">
    <thead><tr><th>Name</th><th>Transport</th><th>Status</th><th>Tools</th><th>Last Health</th><th>Actions</th></tr></thead>
    <tbody>
      {#each servers as s}
        <tr>
          <td><strong>{s.name}</strong></td>
          <td><span class="chip">{s.transport}</span></td>
          <td><span class="dot {s.status}"></span> {s.status}</td>
          <td>{s.tool_count || 0}</td>
          <td class="muted">{s.last_health_at || '—'}</td>
          <td>
            <button class="ghost sm" onclick={() => test(s.id)}>test</button>
            <button class="ghost sm" style="color: #d33;" onclick={() => del(s.id)}>delete</button>
          </td>
        </tr>
      {/each}
      {#if !servers.length}<tr><td colspan="6" class="empty">No MCP servers. Add Exa, Perplexity, GitHub, Linear, etc.</td></tr>{/if}
    </tbody>
  </table>
</div>

<style>
  .page { padding: 24px 32px 60px; max-width: 1280px; margin: 0 auto; font: 14px Inter; color: var(--pw-ink, #2c2a26); }
  header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 24px; }
  h1 { font: 600 28px 'Source Serif 4', Georgia, serif; margin: 0; color: var(--pw-accent, #c96342); }
  .muted { color: var(--pw-ink-soft, #87837a); font-size: 11px; margin: 4px 0 0; }
  .form { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 16px; margin-bottom: 16px; display: grid; gap: 8px; }
  .form input, .form select { padding: 8px 12px; border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; font: 13px Inter; }
  .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; font-size: 11px; }
  .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
  .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.05em; }
  .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
  .chip { display: inline-block; background: var(--pw-bg-alt, #f1ede4); border-radius: 0; padding: 2px 8px; font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.04em; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #888; margin-right: 4px; }
  .dot.connected { background: #10b981; }
  .dot.failed { background: #ef4444; }
  .empty { text-align: center; color: var(--pw-ink-soft, #87837a); padding: 30px; }
  button.primary { background: var(--pw-accent, #c96342); color: white; border: none; border-radius: 0; padding: 8px 14px; cursor: pointer; font: 600 12px Inter; }
  button.ghost { background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink, #2c2a26); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 6px 12px; cursor: pointer; font: 600 11px Inter; }
  button.sm { padding: 4px 10px; font-size: 11px; }
</style>
