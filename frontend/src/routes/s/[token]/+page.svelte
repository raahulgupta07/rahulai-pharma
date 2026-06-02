<script lang="ts">
  import { page } from '$app/stores';

  // Public read-only conversation viewer. No auth header — fetches /api/s/{token}.
  type Msg = { role: string; content: string };
  type Batch = { batch_id?: string; status?: string; file_count?: number; created_at?: string };
  type Lineage = { datasets?: string[]; batches?: Batch[] };
  type Snapshot = {
    title?: string;
    project_slug?: string;
    messages?: Msg[];
    lineage?: Lineage;
    created_at?: string | null;
    expires_at?: string | null;
  };

  let loading = $state(true);
  let error = $state<{ code: number; message: string } | null>(null);
  let snap = $state<Snapshot | null>(null);

  const token = $derived($page.params.token);

  function fmtDate(s?: string | null): string {
    if (!s) return '';
    try {
      const d = new Date(s);
      if (isNaN(d.getTime())) return s;
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    } catch {
      return s;
    }
  }

  async function load() {
    loading = true;
    error = null;
    snap = null;
    try {
      const res = await fetch(`/api/s/${token}`, { headers: { Accept: 'application/json' } });
      if (!res.ok) {
        let msg = 'This conversation could not be loaded.';
        if (res.status === 404) msg = 'This share link does not exist.';
        else if (res.status === 410) {
          try {
            const j = await res.json();
            msg = j?.detail || 'This share link is no longer available.';
          } catch {
            msg = 'This share link has expired or been revoked.';
          }
        }
        error = { code: res.status, message: msg };
        return;
      }
      snap = await res.json();
    } catch (e) {
      error = { code: 0, message: 'Network error — could not reach the server.' };
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (token) load();
  });

  const messages = $derived(snap?.messages ?? []);
  const lineage = $derived(snap?.lineage ?? null);
  const hasLineage = $derived(
    !!lineage && (((lineage.datasets?.length ?? 0) > 0) || ((lineage.batches?.length ?? 0) > 0))
  );
</script>

<svelte:head>
  <title>{snap?.title ? snap.title + ' · shared' : 'Shared conversation'}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<div class="wrap">
  <header class="hdr">
    <div class="brand">◉ shared conversation</div>
    {#if snap}
      <div class="title">{snap.title}</div>
      <div class="meta">
        <span>read-only</span>
        {#if snap.project_slug}<span>· {snap.project_slug}</span>{/if}
        {#if snap.expires_at}<span>· expires {fmtDate(snap.expires_at)}</span>{/if}
      </div>
    {/if}
  </header>

  {#if loading}
    <div class="state">
      <div class="dot"></div> loading…
    </div>
  {:else if error}
    <div class="state err">
      <div class="errcode">{error.code === 410 ? 'GONE' : error.code === 404 ? 'NOT FOUND' : 'ERROR'}</div>
      <div class="errmsg">{error.message}</div>
    </div>
  {:else if snap}
    <main class="convo">
      {#each messages as m}
        <div class="msg {m.role === 'user' ? 'user' : 'assistant'}">
          <div class="who">{m.role === 'user' ? '> you' : '◉ agent'}</div>
          <div class="body">{m.content}</div>
        </div>
      {/each}
      {#if messages.length === 0}
        <div class="state">no messages in this conversation.</div>
      {/if}
    </main>

    {#if hasLineage}
      <footer class="prov">
        <div class="provhdr">— provenance —</div>
        {#if (lineage?.datasets?.length ?? 0) > 0}
          <div class="provrow">
            <span class="lbl">datasets</span>
            <span class="vals">
              {#each lineage?.datasets ?? [] as d}
                <span class="chip">{d}</span>
              {/each}
            </span>
          </div>
        {/if}
        {#if (lineage?.batches?.length ?? 0) > 0}
          <div class="provrow">
            <span class="lbl">batches</span>
            <span class="vals">
              {#each lineage?.batches ?? [] as b}
                <span class="chip" title={b.created_at ? fmtDate(b.created_at) : ''}>
                  {b.batch_id}{b.status ? ' · ' + b.status : ''}{b.file_count != null ? ' · ' + b.file_count + 'f' : ''}
                </span>
              {/each}
            </span>
          </div>
        {/if}
      </footer>
    {/if}

    <div class="ftr">
      shared snapshot · {fmtDate(snap.created_at)} · CityAgent Insights
    </div>
  {/if}
</div>

<style>
  :global(body) {
    margin: 0;
    background: #14110f;
  }
  .wrap {
    min-height: 100vh;
    max-width: 820px;
    margin: 0 auto;
    padding: 28px 18px 60px;
    font-family: ui-monospace, "Berkeley Mono", Menlo, monospace;
    color: #e8e3d6;
    background: #14110f;
  }
  .hdr {
    border-bottom: 1px solid #2a2522;
    padding-bottom: 14px;
    margin-bottom: 22px;
  }
  .brand {
    color: #00fc40;
    font-size: 12px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .title {
    font-size: 20px;
    margin-top: 10px;
    color: #f3efe6;
    line-height: 1.35;
  }
  .meta {
    font-size: 11px;
    color: #8a847a;
    margin-top: 6px;
    text-transform: lowercase;
    letter-spacing: 0.03em;
  }
  .meta span { margin-right: 4px; }

  .state {
    color: #8a847a;
    font-size: 13px;
    padding: 40px 0;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .state.err { flex-direction: column; align-items: flex-start; gap: 10px; }
  .errcode {
    color: #ff6b4a;
    font-size: 12px;
    letter-spacing: 0.08em;
    border: 1px solid #ff6b4a;
    padding: 3px 10px;
  }
  .errmsg { color: #c9c2b6; font-size: 14px; }
  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #00fc40;
    animation: pulse 1.2s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100% { opacity: 0.35; } 50% { opacity: 1; } }

  .convo { display: flex; flex-direction: column; gap: 18px; }
  .msg { display: flex; flex-direction: column; gap: 6px; }
  .who {
    font-size: 11px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .msg.user .who { color: #c96342; }
  .msg.assistant .who { color: #66aaff; }
  .body {
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.6;
    font-size: 14px;
    padding: 10px 14px;
    border-left: 2px solid #2a2522;
    background: #1a1614;
  }
  .msg.user .body { border-left-color: #c96342; }
  .msg.assistant .body { border-left-color: #2a2522; }

  .prov {
    margin-top: 30px;
    padding-top: 16px;
    border-top: 1px solid #2a2522;
  }
  .provhdr {
    color: #8a847a;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 12px;
  }
  .provrow {
    display: flex;
    gap: 12px;
    margin-bottom: 10px;
    align-items: baseline;
  }
  .lbl {
    color: #8a847a;
    font-size: 11px;
    min-width: 70px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .vals { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip {
    font-size: 11px;
    color: #e8e3d6;
    background: #221d1a;
    border: 1px solid #2a2522;
    padding: 2px 8px;
  }

  .ftr {
    margin-top: 34px;
    padding-top: 14px;
    border-top: 1px solid #2a2522;
    color: #6a655c;
    font-size: 11px;
    letter-spacing: 0.03em;
    text-align: center;
  }
</style>
