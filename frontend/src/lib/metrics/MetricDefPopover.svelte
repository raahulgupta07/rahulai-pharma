<script lang="ts">
  // MetricDefPopover.svelte — A4: view metric definition inline popover
  let {
    slug,
    name,
    onclose
  }: {
    slug: string;
    name: string;
    onclose: () => void;
  } = $props();

  function _headers(): Record<string, string> {
    const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    const scopeId = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_scope_id') : null;
    const h: Record<string, string> = {};
    if (token) h['Authorization'] = `Bearer ${token}`;
    if (scopeId) h['X-Scope-Id'] = scopeId;
    return h;
  }

  let spec = $state<any>(null);
  let loading = $state(true);
  let error = $state('');

  import { onMount } from 'svelte';
  onMount(async () => {
    try {
      const res = await fetch(`/api/projects/${slug}/metrics/${encodeURIComponent(name)}`, {
        headers: _headers()
      });
      if (res.ok) {
        spec = await res.json().catch(() => null);
      } else {
        error = `HTTP ${res.status}`;
      }
    } catch (e: any) {
      error = e?.message || 'Network error';
    }
    loading = false;
  });

  function statusColor(s: string) {
    if (s === 'verified') return '#2e7d32';
    if (s === 'deprecated') return '#c62828';
    return '#a06000';
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose();
  }
</script>

<svelte:window onkeydown={onKeydown} />

<!-- Backdrop -->
<div style="
  position: fixed; inset: 0; z-index: 9990;
  background: rgba(0,0,0,0.3);
  display: flex; align-items: flex-start; justify-content: center;
  padding-top: 80px;
" onclick={() => onclose()}>
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div style="
    z-index: 9991;
    width: min(420px, 96vw);
    background: var(--pw-bg, #f5f0e8);
    border: 2px solid var(--pw-ink, #1a1614);
    font-family: monospace;
    max-height: 70vh;
    overflow-y: auto;
  " onclick={(e) => e.stopPropagation()}>

    <!-- Header -->
    <div style="
      background: var(--pw-ink, #1a1614);
      color: var(--pw-bg, #f5f0e8);
      padding: 8px 12px;
      display: flex; align-items: center; gap: 8px;
      font-size: 11px; font-weight: 900; letter-spacing: 0.07em;
    ">
      <span style="flex:1;">📐 {name}</span>
      <button onclick={() => onclose()} style="
        background:none; border:1px solid rgba(255,255,255,0.3);
        color:inherit; cursor:pointer; padding:1px 6px; font-size:10px;
      ">✕</button>
    </div>

    <div style="padding: 12px; font-size: 11px; color: var(--pw-ink, #1a1614);">
      {#if loading}
        <div style="color:var(--pw-muted,#888);">⟳ Loading…</div>
      {:else if error}
        <div style="color:#c62828;">{error}</div>
      {:else if !spec}
        <div style="color:var(--pw-muted,#888);">Metric not found.</div>
      {:else}
        <!-- Status badge -->
        <div style="margin-bottom: 8px;">
          <span style="
            display: inline-block;
            padding: 2px 8px;
            font-size: 10px; font-weight: 900; letter-spacing: 0.06em;
            border: 1px solid {statusColor(spec.status || 'draft')};
            color: {statusColor(spec.status || 'draft')};
          ">{(spec.status || 'DRAFT').toUpperCase()}</span>
        </div>

        {#if spec.description}
          <div style="margin-bottom: 8px; color: var(--pw-muted, #666); font-size: 10px;">{spec.description}</div>
        {/if}

        <!-- Kind + measure -->
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-bottom:8px;">
          <div>
            <div style="font-size:9px; font-weight:900; letter-spacing:0.06em; color:var(--pw-muted,#888); margin-bottom:2px;">KIND</div>
            <div style="font-weight:700;">{spec.kind || '—'}</div>
          </div>
          <div>
            <div style="font-size:9px; font-weight:900; letter-spacing:0.06em; color:var(--pw-muted,#888); margin-bottom:2px;">MEASURE COL</div>
            <div style="font-weight:700;">{spec.measure_col || '—'}</div>
          </div>
        </div>

        <!-- Verified answer -->
        {#if spec.verified_answer != null}
          <div style="
            border: 2px solid #2e7d32; background: #f1f8e9;
            padding: 6px 10px; margin-bottom: 8px;
          ">
            <div style="font-size:9px; font-weight:900; letter-spacing:0.06em; color:#2e7d32; margin-bottom:2px;">✓ VERIFIED TOTAL</div>
            <div style="font-size:16px; font-weight:900; color:#2e7d32;">{spec.verified_answer}</div>
          </div>
        {/if}

        <!-- Filters -->
        {#if Array.isArray(spec.filters) && spec.filters.length > 0}
          <div style="margin-bottom: 8px;">
            <div style="font-size:9px; font-weight:900; letter-spacing:0.06em; color:var(--pw-muted,#888); margin-bottom:4px;">FILTERS</div>
            {#each spec.filters as f}
              {#if f && f.col}
                <div style="
                  display:flex; gap:4px; align-items:center;
                  font-size:10px; margin-bottom:3px;
                  background:var(--pw-bg-alt,#ede8de); padding:3px 6px;
                ">
                  <code style="color:var(--pw-accent,#c96342); font-weight:700;">{f.col}</code>
                  <span style="color:var(--pw-muted,#888);">{f.op}</span>
                  <code>{f.value}</code>
                  {#if f.trim}
                    <span style="font-size:9px; color:var(--pw-muted,#888);">(trim)</span>
                  {/if}
                </div>
              {/if}
            {/each}
          </div>
        {/if}

        <!-- Group dims -->
        {#if Array.isArray(spec.group_dims) && spec.group_dims.length > 0}
          <div style="margin-bottom: 8px;">
            <div style="font-size:9px; font-weight:900; letter-spacing:0.06em; color:var(--pw-muted,#888); margin-bottom:4px;">GROUP DIMS</div>
            <div style="display:flex; flex-wrap:wrap; gap:4px;">
              {#each spec.group_dims as d}
                <span style="
                  background:var(--pw-ink,#1a1614); color:var(--pw-bg,#f5f0e8);
                  padding:2px 7px; font-size:10px;
                ">{d}</span>
              {/each}
            </div>
          </div>
        {/if}
      {/if}
    </div>
  </div>
</div>
