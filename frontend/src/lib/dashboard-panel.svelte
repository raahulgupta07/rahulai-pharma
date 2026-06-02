<script lang="ts">
  import { onMount } from 'svelte';
  import EChartView from '$lib/echart.svelte';
  import { markdownToHtml } from '$lib';
  import type { ChartType } from '$lib/chart-detect';

  interface Props {
    dashboardId: number | null;
    projectSlug: string;
    generating?: boolean;
    unsavedId?: number | null;
    onClose: () => void;
    onSelectDashboard: (id: number) => void;
    onSave?: () => void;
    onDiscard?: () => void;
  }

  let { dashboardId, projectSlug, generating = false, unsavedId = null, onClose, onSelectDashboard, onSave, onDiscard }: Props = $props();

  let isUnsaved = $derived(unsavedId != null && dashboardId === unsavedId);

  let dashboard = $state<any>(null);
  let dashboards = $state<any[]>([]);
  let loading = $state(false);

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  function timeAgo(ts: string | null): string {
    if (!ts) return '';
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
    return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  async function loadDashboard(id: number) {
    loading = true;
    try {
      const res = await fetch(`/api/projects/${projectSlug}/dashboards/${id}`, { headers: _h() });
      if (res.ok) {
        dashboard = await res.json();
      }
    } catch {}
    loading = false;
  }

  async function loadDashboardList() {
    try {
      const res = await fetch(`/api/projects/${projectSlug}/dashboards`, { headers: _h() });
      if (res.ok) {
        const d = await res.json();
        dashboards = d.dashboards || [];
      }
    } catch {}
  }

  function changeChartType(widgetIndex: number, ct: string) {
    if (!dashboard) return;
    const widgets = [...dashboard.widgets];
    widgets[widgetIndex] = { ...widgets[widgetIndex], chartType: ct };
    dashboard = { ...dashboard, widgets };
  }

  async function exportPptx() {
    if (!dashboard?.widgets?.length) return;
    const slides = dashboard.widgets.map((w: any) => ({
      title: w.title || 'Widget',
      content: w.content || '',
      headers: w.headers || [],
      rows: w.rows || [],
    }));
    try {
      const res = await fetch('/api/export/pptx', {
        method: 'POST',
        headers: { ..._h(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: dashboard.name || 'Dashboard', slides }),
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${dashboard.name || 'dashboard'}.pptx`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch {}
  }

  onMount(() => {
    loadDashboardList();
  });

  // React to dashboardId changes (also handles initial load)
  $effect(() => {
    if (dashboardId) {
      loadDashboard(dashboardId);
    } else {
      dashboard = null;
    }
  });
</script>

<div class="dashboard-side-panel">
  <!-- Header -->
  <div class="panel-header">
    <div style="display: flex; align-items: center; gap: 8px;">
      {#if dashboard}
        <button onclick={() => { dashboard = null; onSelectDashboard(0); }} style="background: none; border: none; cursor: pointer; color: var(--color-surface); font-size: 13px; font-weight: 900;">&larr;</button>
      {/if}
      <span class="panel-header-title">
        {dashboard ? dashboard.name : 'DASHBOARDS'}
      </span>
      {#if dashboard && isUnsaved}
        <span style="font-size: 11px; padding: 1px 5px; background: #F0A030; color: #000; font-weight: 900; letter-spacing: 0.06em;">PREVIEW</span>
      {/if}
      {#if dashboard}
        <span style="font-size: 11px; opacity: 0.6;">{dashboard.widgets?.length || 0} widgets</span>
      {/if}
    </div>
    <div style="display: flex; align-items: center; gap: 6px;">
      {#if dashboard && isUnsaved}
        <button onclick={onSave} style="background: #00a86b; color: white; border: none; padding: 3px 8px; font-family: var(--font-family-display); font-size: 11px; font-weight: 900; text-transform: uppercase; cursor: pointer; letter-spacing: 0.06em;">SAVE</button>
        <button onclick={onDiscard} style="background: #cc3333; color: white; border: none; padding: 3px 8px; font-family: var(--font-family-display); font-size: 11px; font-weight: 900; text-transform: uppercase; cursor: pointer; letter-spacing: 0.06em;">DISCARD</button>
      {:else if dashboard}
        <button onclick={exportPptx} style="background: var(--color-primary); color: white; border: none; padding: 3px 8px; font-family: var(--font-family-display); font-size: 11px; font-weight: 900; text-transform: uppercase; cursor: pointer; letter-spacing: 0.06em;">EXPORT</button>
        <button onclick={() => window.location.href = `/ui/project/${projectSlug}/dashboard`} style="background: var(--color-secondary); color: white; border: none; padding: 3px 8px; font-family: var(--font-family-display); font-size: 11px; font-weight: 900; text-transform: uppercase; cursor: pointer; letter-spacing: 0.06em;">FULL VIEW</button>
      {/if}
      <button onclick={onClose} style="background: none; border: none; cursor: pointer; color: var(--color-surface); font-size: 14px; font-weight: 900;">&times;</button>
    </div>
  </div>

  <!-- Body -->
  <div class="panel-body">
    {#if loading}
      <div style="text-align: center; padding: 40px; font-size: 11px; color: var(--color-on-surface-dim);">Loading...</div>

    {:else if dashboard && dashboard.widgets?.length > 0}
      <!-- Dashboard view: render widgets -->
      <div class="panel-widget-grid">
        {#each dashboard.widgets as widget, wi}
          <div class="panel-widget" class:panel-widget-full={widget.full || widget.type === 'text'}>
            <!-- Widget header -->
            <div class="panel-widget-header">
              <span>{widget.title}</span>
              <span style="font-size: 7px; padding: 1px 4px; background: var(--color-primary); color: white;">{widget.type?.toUpperCase()}</span>
            </div>
            <!-- Widget body -->
            <div class="panel-widget-body">
              {#if widget.type === 'text' && widget.config?.items}
                <div style="padding: 10px;">
                  {#each widget.config.items as item}
                    <div style="display: flex; gap: 6px; margin-bottom: 6px; font-size: 11px; padding: 4px 8px; border-left: 2px solid var(--color-warning); background: #fff8e1;">
                      <span style="color: var(--color-warning);">●</span>
                      <span>{item}</span>
                    </div>
                  {/each}
                </div>
              {:else if widget.type === 'metric' && widget.config?.questions !== undefined}
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 10px;">
                  <div style="text-align: center; padding: 8px; background: var(--color-surface-dim);">
                    <div style="font-size: 16px; font-weight: 900;">{widget.config.questions}</div>
                    <div style="font-size: 11px; text-transform: uppercase; color: var(--color-on-surface-dim);">Questions</div>
                  </div>
                  <div style="text-align: center; padding: 8px; background: var(--color-surface-dim);">
                    <div style="font-size: 16px; font-weight: 900;">{widget.config.queries}</div>
                    <div style="font-size: 11px; text-transform: uppercase; color: var(--color-on-surface-dim);">SQL Queries</div>
                  </div>
                </div>
              {:else if widget.type === 'chart' && widget.config?.data?.labels && widget.config?.data?.values}
                <div style="display: flex; gap: 0; margin-bottom: 4px;">
                  {#each ['bar', 'line', 'pie', 'scatter', 'area'] as ct}
                    <button class="chart-type-btn" class:chart-type-btn-active={(widget.chartType || 'bar') === ct} onclick={() => changeChartType(wi, ct)} style="font-size: 11px; padding: 2px 6px;">{ct.toUpperCase()}</button>
                  {/each}
                </div>
                <div style="height: 200px;">
                  <EChartView headers={['Label', 'Value']} rows={widget.config.data.labels.map((l, i) => [l, String(widget.config.data.values[i] ?? 0)])} chartType={widget.chartType || 'bar'} />
                </div>
              {:else if widget.type === 'chart' && widget.headers && widget.rows && widget.rows.length > 0}
                <div style="display: flex; gap: 0; margin-bottom: 4px;">
                  {#each ['bar', 'line', 'pie', 'scatter', 'area'] as ct}
                    <button class="chart-type-btn" class:chart-type-btn-active={(widget.chartType || 'bar') === ct} onclick={() => changeChartType(wi, ct)} style="font-size: 11px; padding: 2px 6px;">{ct.toUpperCase()}</button>
                  {/each}
                </div>
                <div style="height: 200px;">
                  <EChartView headers={widget.headers} rows={widget.rows.map((r: any[]) => r.map((c: any) => String(c ?? '')))} chartType={widget.chartType || 'bar'} />
                </div>
              {:else if widget.type === 'metric'}
                <div style="text-align: center; padding: 12px;">
                  <div style="font-size: 24px; font-weight: 900; color: var(--color-primary);">{widget.content || '0'}</div>
                  <div style="font-size: 11px; text-transform: uppercase; color: var(--color-on-surface-dim); letter-spacing: 0.1em; margin-top: 2px;">{widget.title}</div>
                </div>
              {:else if widget.type === 'table' && widget.headers && widget.rows}
                <div style="overflow-x: auto;">
                  <table class="data-table" style="font-size: 11px;">
                    <thead><tr>{#each widget.headers as h}<th style="padding: 3px 6px;">{h}</th>{/each}</tr></thead>
                    <tbody>{#each widget.rows.slice(0, 10) as row}<tr>{#each row as cell}<td style="padding: 2px 6px;">{cell}</td>{/each}</tr>{/each}</tbody>
                  </table>
                  {#if widget.rows.length > 10}
                    <div style="font-size: 11px; color: var(--color-on-surface-dim); text-align: center; margin-top: 4px;">+{widget.rows.length - 10} more rows</div>
                  {/if}
                </div>
              {:else}
                <div class="prose-chat" style="font-size: 11px;">{@html markdownToHtml(widget.content || '')}</div>
              {/if}
            </div>
          </div>
        {/each}
      </div>

    {:else if dashboard && (!dashboard.widgets || dashboard.widgets.length === 0)}
      <div style="text-align: center; padding: 40px; color: var(--color-on-surface-dim);">
        <div style="font-size: 13px; font-weight: 900; margin-bottom: 6px;">EMPTY DASHBOARD</div>
        <div style="font-size: 10px;">Pin charts from chat to add widgets.</div>
      </div>

    {:else}
      <!-- Dashboard list view -->
      {#if generating}
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 60px 20px; gap: 16px;">
          <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--color-primary);">GENERATING DASHBOARD</div>
          <div style="width: 200px; height: 4px; background: var(--color-surface-dim); overflow: hidden;">
            <div style="height: 100%; width: 60%; background: var(--color-primary); animation: dash-progress 2s ease-in-out infinite;"></div>
          </div>
          <div style="font-size: 10px; color: var(--color-on-surface-dim); text-align: center; line-height: 1.6;">
            Analyzing chat responses...<br>
            Extracting metrics & charts...<br>
            Building dashboard layout...
          </div>
        </div>
      {:else if dashboards.length === 0}
        <div style="text-align: center; padding: 40px; color: var(--color-on-surface-dim);">
          <div style="font-size: 13px; font-weight: 900; margin-bottom: 6px;">NO DASHBOARDS</div>
          <div style="font-size: 10px;">Ask the agent to create a dashboard or pin charts from chat.</div>
        </div>
      {:else}
        <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; color: var(--color-on-surface-dim);">
          SELECT A DASHBOARD
        </div>
        <div class="panel-dash-list">
          {#each dashboards as d}
            <button class="panel-dash-item" onclick={() => { onSelectDashboard(d.id); loadDashboard(d.id); }}>
              <div>
                <div style="font-size: 11px; font-weight: 900; text-transform: uppercase;">{d.name}</div>
                <div style="font-size: 11px; color: var(--color-on-surface-dim);">{d.widget_count} widgets</div>
              </div>
              <div style="font-size: 11px; color: var(--color-on-surface-dim);">{timeAgo(d.updated_at)}</div>
            </button>
          {/each}
        </div>
      {/if}
    {/if}
  </div>

  <!-- Bottom actions (when viewing a dashboard) -->
  {#if dashboard && isUnsaved}
    <div class="panel-actions" style="display: flex; gap: 6px;">
      <button class="feedback-btn" onclick={onSave} style="font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 4px 10px; background: #00a86b; color: #fff; border: none;">SAVE</button>
      <button class="feedback-btn" onclick={onDiscard} style="font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 4px 10px; background: #cc3333; color: #fff; border: none;">DISCARD</button>
    </div>
  {:else if dashboard}
    <div class="panel-actions">
      <button class="feedback-btn" onclick={exportPptx} style="font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 4px 10px;">EXPORT PPTX</button>
      <button class="feedback-btn" onclick={() => window.location.href = `/ui/project/${projectSlug}/dashboard`} style="font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 4px 10px;">OPEN FULL VIEW</button>
    </div>
  {/if}
</div>
