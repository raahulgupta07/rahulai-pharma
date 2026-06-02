<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 let tab = $state<'slack' | 'email' | 'voice' | 'threads'>('threads');
 let slackWs = $state<any[]>([]);
 let emailAcc = $state<any[]>([]);
 let voiceNum = $state<any[]>([]);
 let threads = $state<any[]>([]);
 let openThread = $state<any>(null);
 const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);

 async function load() {
 const hdr = { headers: { Authorization: `Bearer ${token() || ''}` } };
 const [sw, ea, vn, th] = await Promise.all([
 fetch('/api/channels/slack/workspaces', hdr).then((r) => r.json()).catch(() => null),
 fetch('/api/channels/email/accounts', hdr).then((r) => r.json()).catch(() => null),
 fetch('/api/channels/voice/numbers', hdr).then((r) => r.json()).catch(() => null),
 fetch('/api/channels/threads?limit=50', hdr).then((r) => r.json()).catch(() => null),
 ]);
 slackWs = sw?.workspaces || [];
 emailAcc = ea?.accounts || [];
 voiceNum = vn?.numbers || [];
 threads = th?.threads || [];
 }

 async function viewThread(id: string) {
 const r = await fetch(`/api/channels/threads/${id}`, { headers: { Authorization: `Bearer ${token() || ''}` } });
 openThread = await r.json();
 }

 onMount(load);
</script>

<div class="page">
  <header>
    <div>
      <h1>Channels</h1>
      <p class="muted">Slack · Email · Voice — same agent, shared session memory.</p>
    </div>
  </header>

  <nav class="tabs">
    {#each ['threads','slack','email','voice'] as t}
      <button class:active={tab === t} onclick={() => (tab = t as any)}>{t}</button>
    {/each}
  </nav>

  {#if tab === 'threads'}
    <div class="layout">
      <aside>
        {#each threads as t}
          <button class="trow" class:active={openThread?.thread?.id === t.id} onclick={() => viewThread(t.id)}>
            <span class="chip chip-{t.channel_kind}">{t.channel_kind}</span>
            <span class="ext">{t.external_user || '—'}</span>
            <span class="muted">{t.subject || t.external_id?.slice(0, 24)}</span>
          </button>
        {/each}
        {#if !threads.length}<div class="empty-sm">No threads yet.</div>{/if}
      </aside>
      <main>
        {#if !openThread}
          <div class="empty">Pick a thread.</div>
        {:else}
          <h2>{openThread.thread.subject || openThread.thread.external_id}</h2>
          <div class="meta">
            <span><strong>From:</strong> {openThread.thread.external_user || '—'}</span>
            <span><strong>Project:</strong> {openThread.thread.project_slug}</span>
            <span><strong>Status:</strong> {openThread.thread.status}</span>
          </div>
          <div class="msgs">
            {#each openThread.messages as m}
              <div class="msg {m.direction}">
                <div class="msgh">
                  <strong>{m.direction === 'inbound' ? (m.author || 'user') : 'agent'}</strong>
                  <span class="muted">{m.created_at}{m.latency_ms ? ` · ${m.latency_ms}ms` : ''}</span>
                </div>
                <div class="body">{m.body}</div>
              </div>
            {/each}
          </div>
        {/if}
      </main>
    </div>
  {:else if tab === 'slack'}
    <table class="tbl">
      <thead><tr><th>Team</th><th>Team ID</th><th>Default Project</th><th>Enabled</th></tr></thead>
      <tbody>
        {#each slackWs as w}
          <tr><td><strong>{w.team_name || '—'}</strong></td><td class="mono">{w.team_id}</td><td>{w.default_project_slug || '—'}</td><td>{w.enabled ? '' : ''}</td></tr>
        {/each}
        {#if !slackWs.length}<tr><td colspan="4" class="empty">No workspaces. POST /api/channels/slack/workspaces (super-admin).</td></tr>{/if}
      </tbody>
    </table>
  {:else if tab === 'email'}
    <table class="tbl">
      <thead><tr><th>Name</th><th>Kind</th><th>User</th><th>Default Project</th></tr></thead>
      <tbody>
        {#each emailAcc as a}
          <tr><td><strong>{a.name}</strong></td><td><span class="chip">{a.inbound_kind}</span></td><td class="mono">{a.imap_user || a.smtp_user}</td><td>{a.default_project_slug || '—'}</td></tr>
        {/each}
        {#if !emailAcc.length}<tr><td colspan="4" class="empty">No email accounts. POST /api/channels/email/accounts (super-admin).</td></tr>{/if}
      </tbody>
    </table>
  {:else if tab === 'voice'}
    <table class="tbl">
      <thead><tr><th>Number</th><th>Provider</th><th>Default Project</th><th>Enabled</th></tr></thead>
      <tbody>
        {#each voiceNum as v}
          <tr><td class="mono"><strong>{v.phone_number}</strong></td><td><span class="chip">{v.provider}</span></td><td>{v.default_project_slug || '—'}</td><td>{v.enabled ? '' : ''}</td></tr>
        {/each}
        {#if !voiceNum.length}<tr><td colspan="4" class="empty">No voice numbers. POST /api/channels/voice/numbers (super-admin).</td></tr>{/if}
      </tbody>
    </table>
  {/if}
</div>

<style>
 .page { padding: 24px 32px 60px; max-width: 1280px; margin: 0 auto; font: 14px Inter; color: var(--pw-ink, #2c2a26); }
 header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 16px; }
 h1 { font: 600 28px 'Source Serif 4', Georgia, serif; margin: 0; color: var(--pw-accent, #c96342); }
 h2 { font: 600 18px 'Source Serif 4', Georgia, serif; margin: 0 0 8px; }
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 11px; margin: 4px 0 0; }
 .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--pw-border, #e7e3da); margin-bottom: 20px; }
 .tabs button { background: none; border: none; padding: 10px 14px; cursor: pointer; font: 13px Inter; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-ink-soft, #87837a); border-bottom: 2px solid transparent; }
 .tabs button.active { color: var(--pw-accent, #c96342); border-bottom-color: var(--pw-accent, #c96342); font-weight: 600; }
 .layout { display: grid; grid-template-columns: 360px 1fr; gap: 16px; }
 aside { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; max-height: 70vh; overflow-y: auto; padding: 6px; }
 .trow { display: block; width: 100%; background: none; border: none; text-align: left; padding: 10px 12px; cursor: pointer; border-radius: 0; font-size: 11px; }
 .trow:hover { background: rgba(201,99,66,0.04); }
 .trow.active { background: rgba(201,99,66,0.08); }
 .ext { font-weight: 600; margin: 0 6px; }
 .chip { display: inline-block; border-radius: 0; padding: 2px 8px; font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.04em; background: var(--pw-bg-alt, #f1ede4); margin-right: 6px; }
 .chip-slack { background: #4a154b; color: white; }
 .chip-email { background: #1d4ed8; color: white; }
 .chip-voice { background: #059669; color: white; }
 main { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 20px; min-height: 400px; }
 .meta { display: flex; gap: 12px; font-size: 11px; margin-bottom: 12px; color: var(--pw-ink-soft, #87837a); flex-wrap: wrap; }
 .msgs { display: flex; flex-direction: column; gap: 10px; }
 .msg { padding: 10px 12px; border-radius: 0; border: 1px solid var(--pw-border, #e7e3da); }
 .msg.inbound { background: var(--pw-bg-alt, #f1ede4); }
 .msg.outbound { background: rgba(201,99,66,0.06); border-color: rgba(201,99,66,0.2); }
 .msgh { display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 4px; }
 .body { white-space: pre-wrap; font-size: 11px; }
 .empty { text-align: center; padding: 60px; color: var(--pw-ink-soft, #87837a); }
 .empty-sm { text-align: center; padding: 30px; color: var(--pw-ink-soft, #87837a); font-size: 11px; }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; font-size: 11px; }
 .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
 .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.05em; }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .mono { font-family: 'JetBrains Mono', monospace; font-size: 11px; }
</style>
