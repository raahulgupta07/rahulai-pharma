<script lang="ts">
  import '../../app.css';
  import { onMount } from 'svelte';
  import { authHeaders, setScope } from '$lib/api';

  interface Scope {
    scope_id: string;
    scope_label: string;
    role: string;
    is_default?: boolean;
  }

  let projectSlug = $state('');
  let nextUrl = $state('/ui/home');
  let scopes = $state<Scope[]>([]);
  let loading = $state(true);
  let error = $state('');
  let selectedId = $state<string | null>(null);

  onMount(async () => {
    const params = new URLSearchParams(window.location.search);
    projectSlug = params.get('project_slug') || '';
    nextUrl = params.get('next') || '/ui/home';

    if (!projectSlug) {
      error = 'Missing project_slug parameter';
      loading = false;
      return;
    }

    try {
      const res = await fetch(`/api/auth/scopes?project_slug=${encodeURIComponent(projectSlug)}`, {
        headers: authHeaders(),
      });
      if (!res.ok) {
        error = `Failed to load scopes (${res.status})`;
        loading = false;
        return;
      }
      const data = await res.json();
      const list: Scope[] = Array.isArray(data) ? data : (data.scopes || []);
      scopes = list;

      if (list.length === 0) {
        loading = false;
        return;
      }

      if (list.length === 1) {
        const only = list[0];
        setScope(only.scope_id, only.scope_label);
        window.location.href = nextUrl;
        return;
      }

      const def = list.find(s => s.is_default);
      if (def) selectedId = def.scope_id;
      loading = false;
    } catch (e: any) {
      error = e?.message || 'Connection failed';
      loading = false;
    }
  });

  function pick(s: Scope) {
    selectedId = s.scope_id;
  }

  function continueWith() {
    const s = scopes.find(x => x.scope_id === selectedId);
    if (!s) return;
    setScope(s.scope_id, s.scope_label);
    window.location.href = nextUrl;
  }

  function logout() {
    const token = localStorage.getItem('dash_token');
    if (token) {
      fetch('/api/auth/logout', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => {});
    }
    localStorage.removeItem('dash_token');
    localStorage.removeItem('dash_user');
    localStorage.removeItem('dash_scope_id');
    localStorage.removeItem('dash_scope_label');
    window.location.href = '/ui/login';
  }
</script>

<div class="sp-page">
  <header class="sp-header">
    <div style="display: flex; align-items: center; gap: 10px;">
      <div class="sp-logo-box">D</div>
      <div class="sp-wordmark">Dash</div>
    </div>
    <button type="button" class="sp-link" onclick={logout}>Sign out</button>
  </header>

  <div class="sp-main">
    <div class="sp-card">
      <h1 class="sp-title">Choose a workspace</h1>
      <p class="sp-subtitle">Project: <span class="sp-mono">{projectSlug || '—'}</span></p>

      {#if loading}
        <div class="sp-empty">Loading available workspaces…</div>
      {:else if error}
        <div class="sp-error">{error}</div>
      {:else if scopes.length === 0}
        <div class="sp-empty">
          You don't have access to any workspace for this project yet.<br/>
          Ask your administrator to grant access.
        </div>
      {:else}
        <p class="sp-hint">{scopes.length} workspaces available. Pick one to continue.</p>
        <div class="sp-list">
          {#each scopes as s (s.scope_id)}
            <button type="button" class="sp-row" class:sp-row-active={selectedId === s.scope_id} onclick={() => pick(s)}>
              <div class="sp-row-icon">{(s.scope_label || '?').charAt(0).toUpperCase()}</div>
              <div class="sp-row-body">
                <div class="sp-row-name">{s.scope_label}</div>
                <div class="sp-row-meta">
                  <span class="sp-role sp-role-{s.role?.toLowerCase() || 'viewer'}">{(s.role || 'viewer').toLowerCase()}</span>
                  <span class="sp-mono">{s.scope_id}</span>
                  {#if s.is_default}<span class="sp-default">default</span>{/if}
                </div>
              </div>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="sp-row-chev"><polyline points="9 18 15 12 9 6"/></svg>
            </button>
          {/each}
        </div>

        <div class="sp-actions">
          <button type="button" class="sp-btn-primary" disabled={!selectedId} onclick={continueWith}>Continue</button>
        </div>
      {/if}
    </div>
  </div>

  <footer class="sp-footer">© 2026 Dash</footer>
</div>

<style>
  .sp-page { min-height: 100vh; background: var(--pw-bg); color: var(--pw-ink); font-family: var(--pw-font-body); display: flex; flex-direction: column; }
  .sp-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 28px; border-bottom: 1px solid var(--pw-border); background: var(--pw-bg-alt); }
  .sp-logo-box { width: 32px; height: 32px; border-radius: var(--pw-radius-sm); background: var(--pw-accent); color: #fff; display: flex; align-items: center; justify-content: center; font-family: var(--pw-font-headline); font-size: 12px; font-weight: 500; }
  .sp-wordmark { font-family: var(--pw-font-headline); font-size: 14px; font-weight: 500; color: var(--pw-ink); letter-spacing: -0.01em; }
  .sp-link { background: none; border: none; color: var(--pw-muted); font-family: var(--pw-font-body); font-size: 11px; cursor: pointer; }
  .sp-link:hover { color: var(--pw-accent); }
  .sp-footer { padding: 16px 28px; border-top: 1px solid var(--pw-border); color: var(--pw-muted); font-size: 11px; text-align: center; }

  .sp-main { flex: 1; display: flex; align-items: flex-start; justify-content: center; padding: 56px 24px; }
  .sp-card { width: 100%; max-width: 560px; background: var(--pw-surface); border: 1px solid var(--pw-border); border-radius: var(--pw-radius); padding: 36px; box-shadow: var(--pw-shadow-sm); }

  .sp-title { font-family: var(--pw-font-headline); font-size: 18px; font-weight: 500; letter-spacing: -0.02em; color: var(--pw-ink); margin: 0 0 6px 0; }
  .sp-subtitle { font-size: 11px; color: var(--pw-muted); margin: 0 0 20px 0; }
  .sp-hint { font-size: 11px; color: var(--pw-ink-soft); margin: 0 0 12px 0; }
  .sp-empty { font-size: 11px; color: var(--pw-muted); padding: 24px 0; line-height: 1.55; }
  .sp-error { font-size: 11px; color: var(--pw-error); background: var(--pw-error-soft); padding: 12px 14px; border-radius: var(--pw-radius-sm); }
  .sp-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; color: var(--pw-muted); }

  .sp-list { display: flex; flex-direction: column; gap: 8px; margin-top: 4px; }
  .sp-row { display: flex; align-items: center; gap: 14px; width: 100%; background: var(--pw-surface); border: 1px solid var(--pw-border); border-left: 3px solid transparent; border-radius: var(--pw-radius-sm); padding: 14px 16px; cursor: pointer; text-align: left; color: inherit; font-family: inherit; transition: all 0.15s; }
  .sp-row:hover { background: var(--pw-surface-warm); border-left-color: var(--pw-accent); }
  .sp-row-active { background: var(--pw-accent-soft); border-left-color: var(--pw-accent); }
  .sp-row-icon { width: 36px; height: 36px; flex-shrink: 0; border-radius: var(--pw-radius-sm); background: var(--pw-accent); color: #fff; display: flex; align-items: center; justify-content: center; font-family: var(--pw-font-headline); font-size: 12px; font-weight: 500; }
  .sp-row-body { flex: 1; min-width: 0; }
  .sp-row-name { font-size: 12px; font-weight: 500; color: var(--pw-ink); }
  .sp-row-meta { display: flex; align-items: center; gap: 10px; margin-top: 4px; font-size: 11px; color: var(--pw-muted); }
  .sp-row-chev { color: var(--pw-dim); flex-shrink: 0; }

  .sp-role { padding: 2px 8px; border-radius: var(--pw-radius-pill); font-size: 11px; font-weight: 500; background: var(--pw-bg-alt); color: var(--pw-ink-soft); }
  .sp-role-admin { background: #f6e6da; color: #8a4a1f; }
  .sp-role-editor { background: var(--pw-success-soft); color: var(--pw-success); }
  .sp-role-owner { background: #f6e6da; color: #8a4a1f; }
  .sp-default { padding: 2px 8px; border-radius: var(--pw-radius-pill); font-size: 11px; background: var(--pw-accent-soft); color: var(--pw-accent-ink); font-weight: 500; }

  .sp-actions { display: flex; justify-content: flex-end; margin-top: 24px; }
  .sp-btn-primary { background: var(--pw-accent); color: #fff; border: none; padding: 11px 26px; font-family: var(--pw-font-body); font-size: 11px; font-weight: 500; border-radius: var(--pw-radius-pill); cursor: pointer; transition: background 0.15s; }
  .sp-btn-primary:hover:not(:disabled) { background: var(--pw-accent-ink); }
  .sp-btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
