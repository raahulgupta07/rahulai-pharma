<script lang="ts">
  // Standalone OpenRouter model browser modal.
  // NOTE on embedding modality: OpenRouter's catalog (chat/completions models) does NOT
  // include dedicated embedding models, so when modelType==='embedding' we do not pass
  // an `embedding=true` filter to the backend. Instead we hint users in the search
  // placeholder to search for "embed". This is a known limitation.

  type CatalogItem = {
    id: string;
    name?: string;
    provider?: string;
    description?: string;
    context_length?: number | null;
    pricing_prompt?: number | null;
    pricing_completion?: number | null;
    modalities?: string[];
    supported_params?: string[];
    is_free?: boolean;
  };

  let {
    open = $bindable(false),
    current,
    modelType,
    onSelect
  } = $props<{
    open: boolean;
    current: string;
    modelType: 'chat' | 'deep' | 'lite' | 'embedding';
    onSelect: (modelId: string) => void;
  }>();

  // --- state ---
  let items = $state<CatalogItem[]>([]);
  let total = $state(0);
  let hasMore = $state(false);
  let offset = $state(0);
  const limit = 50;

  let loading = $state(false);
  let loadingMore = $state(false);
  let error = $state<string | null>(null);

  let q = $state('');
  let qDebounceTimer: any = null;

  let freeOnly = $state(false);
  let toolsOnly = $state(false);
  let visionOnly = $state(false);
  let reasoningOnly = $state(false);
  let sort = $state<'popularity' | 'price' | 'context' | 'newest'>('popularity');

  let selectedId = $state<string>(current || '');
  let syncedAt = $state<string | null>(null);
  let syncStatusCount = $state<number>(0);
  let syncing = $state(false);

  let toast = $state<{ kind: 'ok' | 'err'; msg: string } | null>(null);
  let toastTimer: any = null;
  function flash(kind: 'ok' | 'err', msg: string) {
    toast = { kind, msg };
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast = null), 2500);
  }

  let scrollEl: HTMLDivElement | null = null;

  // --- helpers ---
  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t
      ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }
      : { 'Content-Type': 'application/json' };
  }

  function formatCtx(n: number | null | undefined): string {
    if (!n) return '—';
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
    return String(n);
  }
  function formatPrice(p: number | null | undefined): string {
    if (p === null || p === undefined) return '—';
    if (p === 0) return 'free';
    return `$${p.toFixed(2)}`;
  }
  function relTime(iso: string | null): string {
    if (!iso) return 'never';
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 60_000) return 'just now';
    if (ms < 3_600_000) return `${Math.floor(ms / 60_000)} min ago`;
    if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
    return `${Math.floor(ms / 86_400_000)}d ago`;
  }

  function buildQuery(off: number): string {
    const sp = new URLSearchParams();
    if (q.trim()) sp.set('q', q.trim());
    if (freeOnly) sp.set('free_only', 'true');
    if (toolsOnly) sp.set('tools_only', 'true');
    if (visionOnly) sp.set('vision_only', 'true');
    if (reasoningOnly) sp.set('reasoning_only', 'true');
    sp.set('sort', sort);
    sp.set('limit', String(limit));
    sp.set('offset', String(off));
    return sp.toString();
  }

  async function fetchPage(reset: boolean) {
    if (reset) {
      loading = true;
      error = null;
      offset = 0;
    } else {
      loadingMore = true;
    }
    try {
      const r = await fetch(`/api/admin/llm/models/catalog?${buildQuery(reset ? 0 : offset)}`, {
        headers: _h()
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const newItems: CatalogItem[] = Array.isArray(data?.items) ? data.items : [];
      total = Number(data?.total ?? newItems.length);
      hasMore = Boolean(data?.has_more);
      syncedAt = data?.synced_at ?? syncedAt;
      if (reset) {
        items = newItems;
        offset = newItems.length;
      } else {
        items = [...items, ...newItems];
        offset = offset + newItems.length;
      }
    } catch (e: any) {
      error = e?.message || 'Failed to load';
    } finally {
      loading = false;
      loadingMore = false;
    }
  }

  async function fetchSyncStatus() {
    try {
      const r = await fetch('/api/admin/llm/models/sync-status', { headers: _h() });
      if (!r.ok) return;
      const data = await r.json();
      syncedAt = data?.last_synced_at ?? syncedAt;
      syncStatusCount = Number(data?.count ?? 0);
    } catch {}
  }

  async function syncCatalog() {
    if (syncing) return;
    syncing = true;
    try {
      const r = await fetch('/api/admin/llm/models/sync', { method: 'POST', headers: _h() });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const count = Number(data?.count ?? 0);
      flash('ok', `Synced ${count} models`);
      syncedAt = data?.synced_at ?? new Date().toISOString();
      await fetchPage(true);
    } catch (e: any) {
      flash('err', `Sync failed: ${e?.message || 'unknown'}`);
    } finally {
      syncing = false;
    }
  }

  function debouncedSearch() {
    if (qDebounceTimer) clearTimeout(qDebounceTimer);
    qDebounceTimer = setTimeout(() => fetchPage(true), 250);
  }

  function onSortChange(s: typeof sort) {
    sort = s;
    fetchPage(true);
  }
  function toggleChip(which: 'free' | 'tools' | 'vision' | 'reasoning') {
    if (which === 'free') freeOnly = !freeOnly;
    if (which === 'tools') toolsOnly = !toolsOnly;
    if (which === 'vision') visionOnly = !visionOnly;
    if (which === 'reasoning') reasoningOnly = !reasoningOnly;
    fetchPage(true);
  }
  function clearFilters() {
    q = '';
    freeOnly = false;
    toolsOnly = false;
    visionOnly = false;
    reasoningOnly = false;
    sort = 'popularity';
    fetchPage(true);
  }

  function close() {
    open = false;
  }
  function confirmSelection() {
    if (!selectedId) return;
    onSelect(selectedId);
    open = false;
  }
  function pickRow(id: string) {
    selectedId = id;
  }

  function onKeydown(e: KeyboardEvent) {
    if (!open) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  }

  function onScroll() {
    if (!scrollEl || loadingMore || loading || !hasMore) return;
    const remaining = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight;
    if (remaining < 200) {
      fetchPage(false);
    }
  }

  function placeholderText(): string {
    if (modelType === 'embedding') {
      return 'Search models (try "embed" — embedding models are not in OpenRouter catalog)';
    }
    return 'Search by name, provider, capability…';
  }

  // open trigger: load on open
  $effect(() => {
    if (open) {
      selectedId = current || '';
      // default filters by modelType (none for now; modelType !== 'vision')
      fetchPage(true);
      fetchSyncStatus();
    }
  });

  function backdropClick(e: MouseEvent) {
    if (e.target === e.currentTarget) close();
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
  <div class="mpm-backdrop" onclick={backdropClick} role="presentation">
    <div
      class="mpm-modal"
      role="dialog"
      aria-modal="true"
      aria-label="Select model"
    >
      <!-- Header -->
      <div class="mpm-header">
        <div class="mpm-title">
          SELECT {modelType.toUpperCase()} MODEL
        </div>
        <div class="mpm-header-meta">
          <span class="mpm-total">{total} models</span>
          <button
            class="mpm-btn-sync"
            onclick={syncCatalog}
            disabled={syncing}
            title="Sync from OpenRouter"
          >
            {syncing ? '…' : '↻'} Sync
          </button>
          <button class="mpm-btn-close" onclick={close} aria-label="Close">✕</button>
        </div>
      </div>

      <!-- Search -->
      <div class="mpm-search">
        <span class="mpm-search-icon">🔍</span>
        <input
          type="text"
          placeholder={placeholderText()}
          bind:value={q}
          oninput={debouncedSearch}
        />
      </div>

      <!-- Chips -->
      <div class="mpm-chips">
        <button class="mpm-chip" class:active={freeOnly} onclick={() => toggleChip('free')}>
          {freeOnly ? '●' : '○'} Free only
        </button>
        <button class="mpm-chip" class:active={toolsOnly} onclick={() => toggleChip('tools')}>
          {toolsOnly ? '●' : '○'} Tools
        </button>
        <button class="mpm-chip" class:active={visionOnly} onclick={() => toggleChip('vision')}>
          {visionOnly ? '●' : '○'} Vision
        </button>
        <button
          class="mpm-chip"
          class:active={reasoningOnly}
          onclick={() => toggleChip('reasoning')}
        >
          {reasoningOnly ? '●' : '○'} Reasoning
        </button>
      </div>

      <!-- Sort -->
      <div class="mpm-sort">
        <label><input type="radio" name="mpmsort" checked={sort === 'popularity'} onchange={() => onSortChange('popularity')} /> Popularity</label>
        <label><input type="radio" name="mpmsort" checked={sort === 'price'} onchange={() => onSortChange('price')} /> Price asc</label>
        <label><input type="radio" name="mpmsort" checked={sort === 'context'} onchange={() => onSortChange('context')} /> Context size</label>
        <label><input type="radio" name="mpmsort" checked={sort === 'newest'} onchange={() => onSortChange('newest')} /> Newest</label>
      </div>

      <div class="mpm-divider"></div>

      <!-- List -->
      <div class="mpm-list" bind:this={scrollEl} onscroll={onScroll}>
        {#if loading}
          <div class="mpm-skel"></div>
          <div class="mpm-skel"></div>
          <div class="mpm-skel"></div>
        {:else if error}
          <div class="mpm-err">
            Failed to load — {error}
            <button class="mpm-btn-retry" onclick={() => fetchPage(true)}>Retry</button>
          </div>
        {:else if items.length === 0}
          <div class="mpm-empty">
            No models match
            <button class="mpm-btn-link" onclick={clearFilters}>Clear filters</button>
          </div>
        {:else}
          {#each items as m (m.id)}
            {@const isCurrent = m.id === current}
            {@const isSelected = m.id === selectedId}
            <div
              class="mpm-row"
              class:current={isCurrent}
              class:selected={isSelected && !isCurrent}
              onclick={() => pickRow(m.id)}
              role="button"
              tabindex="0"
              onkeydown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  pickRow(m.id);
                }
              }}
            >
              <div class="mpm-row-main">
                <div class="mpm-row-title">
                  <span class="mpm-star">⭐</span>
                  <span class="mpm-id">{m.id}</span>
                  {#if isCurrent}
                    <span class="mpm-pill-current">◉ CURRENT</span>
                  {:else}
                    <button
                      class="mpm-btn-select"
                      onclick={(e) => {
                        e.stopPropagation();
                        pickRow(m.id);
                      }}
                    >
                      {isSelected ? 'Selected' : 'Select'}
                    </button>
                  {/if}
                </div>
                <div class="mpm-row-meta">
                  {formatCtx(m.context_length)} ctx · ${formatPrice(m.pricing_prompt).replace('$','')}/${formatPrice(m.pricing_completion).replace('$','')} per 1M
                  {#if m.modalities && Array.isArray(m.modalities) && m.modalities.includes('image')}
                    · vision
                  {/if}
                  {#if m.supported_params && Array.isArray(m.supported_params) && m.supported_params.includes('tools')}
                    · tools
                  {/if}
                  {#if m.is_free}
                    · free
                  {/if}
                </div>
                {#if m.description}
                  <div class="mpm-row-desc">{m.description}</div>
                {/if}
              </div>
            </div>
          {/each}

          {#if hasMore}
            <div class="mpm-loadmore-wrap">
              <button class="mpm-btn-loadmore" onclick={() => fetchPage(false)} disabled={loadingMore}>
                {loadingMore ? 'Loading…' : `Load more (${Math.max(0, total - items.length)} remaining)`}
              </button>
            </div>
          {/if}
        {/if}
      </div>

      <div class="mpm-divider"></div>

      <!-- Footer -->
      <div class="mpm-footer">
        <div class="mpm-sync-info">Last synced: {relTime(syncedAt)}</div>
        <div class="mpm-footer-btns">
          <button class="mpm-btn-cancel" onclick={close}>Cancel</button>
          <button class="mpm-btn-primary" onclick={confirmSelection} disabled={!selectedId}>
            Use Selected
          </button>
        </div>
      </div>

      {#if toast}
        <div class="mpm-toast" class:err={toast.kind === 'err'}>{toast.msg}</div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .mpm-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 9998;
  }
  .mpm-modal {
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: min(900px, 92vw);
    max-height: 85vh;
    background: var(--pw-bg, #fffaf2);
    color: var(--pw-ink, #2a2522);
    border-radius: 8px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
    z-index: 9999;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    font-family: var(--pw-font-sans, system-ui, -apple-system, sans-serif);
  }
  .mpm-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 18px;
    border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  }
  .mpm-title {
    font-weight: 700;
    font-size: 14px;
    letter-spacing: 0.04em;
  }
  .mpm-header-meta {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 12px;
    color: var(--pw-ink-soft, #6b6557);
  }
  .mpm-total {
    font-variant-numeric: tabular-nums;
  }
  .mpm-btn-sync {
    background: transparent;
    border: 1px solid rgba(201, 99, 66, 0.4);
    color: #c96342;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
  }
  .mpm-btn-sync:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .mpm-btn-sync:hover:not(:disabled) {
    background: rgba(201, 99, 66, 0.08);
  }
  .mpm-btn-close {
    background: transparent;
    border: none;
    color: var(--pw-ink-soft, #6b6557);
    font-size: 16px;
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 4px;
  }
  .mpm-btn-close:hover {
    background: rgba(0, 0, 0, 0.05);
  }

  .mpm-search {
    display: flex;
    align-items: center;
    padding: 10px 18px;
    gap: 8px;
  }
  .mpm-search-icon {
    font-size: 14px;
    opacity: 0.7;
  }
  .mpm-search input {
    flex: 1;
    border: 1px solid rgba(0, 0, 0, 0.12);
    background: #fff;
    color: var(--pw-ink, #2a2522);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
    outline: none;
  }
  .mpm-search input:focus {
    border-color: rgba(201, 99, 66, 0.6);
  }

  .mpm-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 0 18px 8px 18px;
  }
  .mpm-chip {
    background: transparent;
    border: 1px solid rgba(0, 0, 0, 0.12);
    color: var(--pw-ink, #2a2522);
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 12px;
    cursor: pointer;
  }
  .mpm-chip.active {
    background: rgba(201, 99, 66, 0.08);
    border-color: #c96342;
    color: #c96342;
  }
  .mpm-chip:hover {
    background: rgba(201, 99, 66, 0.05);
  }

  .mpm-sort {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    padding: 0 18px 10px 18px;
    font-size: 12px;
    color: var(--pw-ink-soft, #6b6557);
  }
  .mpm-sort label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  }
  .mpm-sort input[type='radio'] {
    accent-color: #c96342;
  }

  .mpm-divider {
    height: 1px;
    background: rgba(0, 0, 0, 0.08);
  }

  .mpm-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px 0;
    min-height: 200px;
  }

  .mpm-row {
    padding: 10px 18px;
    cursor: pointer;
    border-left: 3px solid transparent;
  }
  .mpm-row:hover {
    background: rgba(201, 99, 66, 0.05);
  }
  .mpm-row.selected {
    background: rgba(201, 99, 66, 0.08);
    border-left-color: #c96342;
  }
  .mpm-row.current {
    background: rgba(21, 87, 36, 0.05);
    border-left-color: #155724;
  }
  .mpm-row-main {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .mpm-row-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 600;
  }
  .mpm-star {
    opacity: 0.5;
    font-size: 11px;
  }
  .mpm-id {
    flex: 1;
    font-family: var(--pw-font-mono, ui-monospace, monospace);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mpm-pill-current {
    background: #155724;
    color: #fff;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    letter-spacing: 0.04em;
    font-weight: 700;
  }
  .mpm-btn-select {
    background: transparent;
    border: 1px solid rgba(201, 99, 66, 0.5);
    color: #c96342;
    padding: 2px 10px;
    border-radius: 4px;
    font-size: 11px;
    cursor: pointer;
  }
  .mpm-btn-select:hover {
    background: rgba(201, 99, 66, 0.08);
  }
  .mpm-row-meta {
    font-size: 11.5px;
    color: var(--pw-ink-soft, #6b6557);
    font-variant-numeric: tabular-nums;
  }
  .mpm-row-desc {
    font-size: 12px;
    color: var(--pw-ink-soft, #6b6557);
    font-style: italic;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  .mpm-skel {
    height: 56px;
    margin: 6px 18px;
    background: linear-gradient(
      90deg,
      rgba(0, 0, 0, 0.04) 25%,
      rgba(0, 0, 0, 0.08) 50%,
      rgba(0, 0, 0, 0.04) 75%
    );
    background-size: 200% 100%;
    animation: mpm-pulse 1.4s ease-in-out infinite;
    border-radius: 6px;
  }
  @keyframes mpm-pulse {
    0% {
      background-position: 200% 0;
    }
    100% {
      background-position: -200% 0;
    }
  }

  .mpm-err {
    padding: 24px 18px;
    text-align: center;
    color: #b94a3b;
    font-size: 13px;
  }
  .mpm-btn-retry {
    margin-left: 12px;
    background: transparent;
    border: 1px solid #b94a3b;
    color: #b94a3b;
    padding: 4px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
  }
  .mpm-btn-retry:hover {
    background: rgba(185, 74, 59, 0.08);
  }

  .mpm-empty {
    padding: 32px 18px;
    text-align: center;
    color: var(--pw-ink-soft, #6b6557);
    font-size: 13px;
  }
  .mpm-btn-link {
    margin-left: 10px;
    background: transparent;
    border: none;
    color: #c96342;
    cursor: pointer;
    text-decoration: underline;
    font-size: 13px;
  }

  .mpm-loadmore-wrap {
    padding: 12px 18px;
    text-align: center;
  }
  .mpm-btn-loadmore {
    background: transparent;
    border: 1px solid rgba(0, 0, 0, 0.12);
    color: var(--pw-ink, #2a2522);
    padding: 6px 14px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
  }
  .mpm-btn-loadmore:hover:not(:disabled) {
    background: rgba(201, 99, 66, 0.05);
    border-color: rgba(201, 99, 66, 0.4);
  }
  .mpm-btn-loadmore:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .mpm-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 18px;
    border-top: 1px solid rgba(0, 0, 0, 0.08);
  }
  .mpm-sync-info {
    font-size: 11.5px;
    color: var(--pw-ink-soft, #6b6557);
  }
  .mpm-footer-btns {
    display: flex;
    gap: 8px;
  }
  .mpm-btn-cancel {
    background: transparent;
    border: 1px solid rgba(0, 0, 0, 0.15);
    color: var(--pw-ink, #2a2522);
    padding: 7px 16px;
    border-radius: 5px;
    font-size: 12.5px;
    cursor: pointer;
  }
  .mpm-btn-cancel:hover {
    background: rgba(0, 0, 0, 0.04);
  }
  .mpm-btn-primary {
    background: #c96342;
    border: 1px solid #c96342;
    color: #fff;
    padding: 7px 16px;
    border-radius: 5px;
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
  }
  .mpm-btn-primary:hover:not(:disabled) {
    background: #b3573a;
    border-color: #b3573a;
  }
  .mpm-btn-primary:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .mpm-toast {
    position: absolute;
    bottom: 64px;
    left: 50%;
    transform: translateX(-50%);
    background: #155724;
    color: #fff;
    padding: 8px 14px;
    border-radius: 5px;
    font-size: 12.5px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
    animation: mpm-toast-in 0.18s ease-out;
  }
  .mpm-toast.err {
    background: #b94a3b;
  }
  @keyframes mpm-toast-in {
    from {
      opacity: 0;
      transform: translate(-50%, 6px);
    }
    to {
      opacity: 1;
      transform: translate(-50%, 0);
    }
  }
</style>
