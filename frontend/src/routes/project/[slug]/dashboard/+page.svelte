<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import { page } from '$app/state';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';
 import EChartView from '$lib/echart.svelte';
 import { markdownToHtml } from '$lib';
 import type { ChartType } from '$lib/chart-detect';

 const slug = $derived(page.params.slug || '');
 let projectInfo = $state<any>(null);
 let dashboards = $state<any[]>([]);
 let activeDashboard = $state<any>(null);
 let editMode = $state(false);
 let loading = $state(true);
 let newDashName = $state('');
 let showCreate = $state(false);
 let view = $state<'list' | 'detail'>('list');

 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 onMount(async () => {
 try {
 const [pRes, dRes] = await Promise.all([
 fetch(`/api/projects/${slug}`, { headers: _h() }),
 fetch(`/api/projects/${slug}/dashboards`, { headers: _h() }),
 ]);
 if (pRes.ok) projectInfo = await pRes.json();
 if (dRes.ok) {
 const d = await dRes.json();
 dashboards = d.dashboards || [];
 }
 } catch {}
 loading = false;
 });

 async function loadDashboard(id: number) {
 try {
 const res = await fetch(`/api/projects/${slug}/dashboards/${id}`, { headers: _h() });
 if (res.ok) {
 activeDashboard = await res.json();
 view = 'detail';
 editMode = false;
 }
 } catch {}
 }

 async function createDashboard() {
 const name = newDashName.trim() || 'Dashboard';
 try {
 const res = await fetch(`/api/projects/${slug}/dashboards?name=${encodeURIComponent(name)}`, { method: 'POST', headers: _h() });
 if (res.ok) {
 const d = await res.json();
 newDashName = ''; showCreate = false;
 const dRes = await fetch(`/api/projects/${slug}/dashboards`, { headers: _h() });
 if (dRes.ok) { dashboards = (await dRes.json()).dashboards || []; }
 await loadDashboard(d.id);
 }
 } catch {}
 }

 async function removeWidget(widgetId: string) {
 if (!activeDashboard) return;
 try {
 await fetch(`/api/projects/${slug}/dashboards/${activeDashboard.id}/widgets/${widgetId}`, { method: 'DELETE', headers: _h() });
 await loadDashboard(activeDashboard.id);
 } catch {}
 }

 async function deleteDashboard() {
 if (!activeDashboard) return;
 try {
 await fetch(`/api/projects/${slug}/dashboards/${activeDashboard.id}`, { method: 'DELETE', headers: _h() });
 activeDashboard = null;
 view = 'list';
 const dRes = await fetch(`/api/projects/${slug}/dashboards`, { headers: _h() });
 if (dRes.ok) { dashboards = (await dRes.json()).dashboards || []; }
 } catch {}
 }

 function goBack() {
 activeDashboard = null;
 view = 'list';
 editMode = false;
 // Reload list
 fetch(`/api/projects/${slug}/dashboards`, { headers: _h() }).then(r => r.json()).then(d => { dashboards = d.dashboards || []; }).catch(() => {});
 }

 function timeAgo(ts: string | null): string {
 if (!ts) return '';
 const diff = Date.now() - new Date(ts).getTime();
 if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
 if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
 return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
 }
</script>

<div class="flex flex-col h-full" style="overflow-y: auto;">

  {#if view === 'list'}
    <!-- ═══ DASHBOARD LIST VIEW ═══ -->
    <div class="dark-title-bar flex items-center justify-between" style="padding: 10px 20px;">
      <div class="flex items-center gap-3">
        <button onclick={() => goto(`${base}/project/${slug}`)} style="background: none; border: none; cursor: pointer; color: var(--color-surface); font-size: 11px;">&larr;</button>
        <span style="font-size: 11px; font-weight: 900; letter-spacing: 0.05em;">DASHBOARDS</span>
        <span style="font-size: 10px; opacity: 0.6;">{projectInfo?.agent_name || ''}</span>
      </div>
      <button class="header-btn" style="font-size: 11px; padding: 4px 10px;" onclick={() => showCreate = true}>+ NEW DASHBOARD</button>
    </div>

    <div style="flex: 1; padding: 20px; overflow-y: auto;">
      {#if loading}
        <div style="text-align: center; padding: 60px; font-size: 11px; color: var(--color-on-surface-dim);">Loading...</div>
      {:else if dashboards.length === 0}
        <div style="text-align: center; padding: 60px;">
          <div style="font-size: 16px; font-weight: 900; margin-bottom: 8px;">NO DASHBOARDS YET</div>
          <div style="font-size: 11px; color: var(--color-on-surface-dim); margin-bottom: 20px;">
            Create a dashboard and pin charts from your chat conversations.
          </div>
          <button class="send-btn" style="font-size: 11px; padding: 10px 20px;" onclick={() => showCreate = true}>+ CREATE DASHBOARD</button>
        </div>
      {:else}
        <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px;">
          {#each dashboards as d}
            <button class="ink-border" style="background: var(--color-surface-bright); text-align: left; cursor: pointer; border: 2px solid var(--pw-border, #e5ddcf); border-bottom-width: 4px; border-right-width: 4px; transition: transform 0.1s, box-shadow 0.1s; padding: 0; font-family: var(--font-family-display);" onclick={() => loadDashboard(d.id)}>
              <!-- Card header -->
              <div style="background: var(--color-on-surface); color: var(--color-surface); padding: 10px 16px; display: flex; align-items: center; justify-content: space-between;">
                <span style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.05em;">{d.name}</span>
                <span style="font-size: 11px; opacity: 0.7;">{d.widget_count} widgets</span>
              </div>
              <!-- Card body -->
              <div style="padding: 16px;">
                <div style="display: flex; gap: 12px; margin-bottom: 12px;">
                  <div class="ink-border" style="flex: 1; padding: 8px; text-align: center; background: var(--color-surface);">
                    <div style="font-size: 14px; font-weight: 900; color: var(--color-primary);">{d.widget_count}</div>
                    <div style="font-size: 11px; text-transform: uppercase; color: var(--color-on-surface-dim); letter-spacing: 0.08em;">WIDGETS</div>
                  </div>
                  <div class="ink-border" style="flex: 1; padding: 8px; text-align: center; background: var(--color-surface);">
                    <div style="font-size: 11px; font-weight: 700; color: var(--color-on-surface);">{timeAgo(d.updated_at)}</div>
                    <div style="font-size: 11px; text-transform: uppercase; color: var(--color-on-surface-dim); letter-spacing: 0.08em;">UPDATED</div>
                  </div>
                </div>
                <div style="font-size: 10px; color: var(--color-primary); text-transform: uppercase; font-weight: 900; letter-spacing: 0.08em; text-align: center;">
                  OPEN DASHBOARD &rarr;
                </div>
              </div>
            </button>
          {/each}

          <!-- Create new card -->
          <button class="ink-border" style="background: var(--color-surface-dim); text-align: center; cursor: pointer; border: 2px dashed var(--color-on-surface-dim); padding: 40px 20px; font-family: var(--font-family-display); transition: background 0.1s;" onclick={() => showCreate = true}>
            <div style="font-size: 16px; font-weight: 900; color: var(--color-on-surface-dim);">+</div>
            <div style="font-size: 10px; text-transform: uppercase; color: var(--color-on-surface-dim); letter-spacing: 0.08em; margin-top: 6px;">NEW DASHBOARD</div>
          </button>
        </div>
      {/if}
    </div>

  {:else}
    <!-- ═══ DASHBOARD DETAIL VIEW ═══ -->
    <div class="dark-title-bar flex items-center justify-between" style="padding: 10px 20px;">
      <div class="flex items-center gap-3">
        <button onclick={goBack} style="background: none; border: none; cursor: pointer; color: var(--color-surface); font-size: 11px;">&larr;</button>
        <span style="font-size: 11px; font-weight: 900; letter-spacing: 0.05em;">
          {activeDashboard?.name || 'DASHBOARD'}
        </span>
        <span style="font-size: 10px; opacity: 0.6;">{projectInfo?.agent_name || ''} · {activeDashboard?.widgets?.length || 0} widgets</span>
      </div>
      <div class="flex items-center gap-2">
        <button class="header-btn" style="font-size: 11px; padding: 4px 10px;" onclick={async () => {
          if (!activeDashboard?.widgets?.length) return;
          const slides = activeDashboard.widgets.map((w: any) => ({
            title: w.title || 'Widget', content: w.content || '',
            headers: w.headers || [], rows: w.rows || [],
          }));
          try {
            const res = await fetch('/api/export/pptx', {
              method: 'POST', headers: { ..._h(), 'Content-Type': 'application/json' },
              body: JSON.stringify({ title: activeDashboard.name || 'Dashboard', slides })
            });
            if (res.ok) {
              const blob = await res.blob();
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a'); a.href = url; a.download = `${activeDashboard.name || 'dashboard'}.pptx`; a.click(); URL.revokeObjectURL(url);
            }
          } catch {}
        }}>EXPORT PPTX</button>
        <button class="header-btn" style="font-size: 11px; padding: 4px 10px;" onclick={() => editMode = !editMode}>
          {editMode ? 'DONE' : 'EDIT'}
        </button>
      </div>
    </div>

    <div style="flex: 1; padding: 20px; overflow-y: auto;">
      {#if activeDashboard?.widgets?.length > 0}
        <div class="dashboard-grid">
          {#each activeDashboard.widgets as widget, wi}
            <div class="ink-border" class:widget-full={widget.full || widget.type === 'text'} style="background: var(--color-surface-bright); overflow: hidden;">
              <!-- Widget header -->
              <div class="flex items-center justify-between" style="padding: 8px 14px; border-bottom: 2px solid var(--pw-border, #e5ddcf); background: var(--color-on-surface); color: var(--color-surface);">
                <span style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.06em;">{widget.title}</span>
                <div class="flex items-center gap-2">
                  <span style="font-size: 11px; padding: 1px 6px; background: var(--color-primary); color: white; font-weight: 900;">{widget.type?.toUpperCase()}</span>
                  {#if editMode}
                    <button onclick={() => removeWidget(widget.id)} style="background: none; border: none; cursor: pointer; color: var(--color-error); font-size: 11px; font-weight: 900;"><Icon name="x" size={14} /></button>
                  {/if}
                </div>
              </div>
              <!-- Widget body -->
              <div style="padding: 14px;">
                {#if widget.type === 'chart' && widget.headers && widget.rows}
                  <div class="flex gap-0 mb-2">
                    {#each ['bar', 'line', 'pie', 'scatter', 'area'] as ct}
                      <button class="chart-type-btn" class:chart-type-btn-active={(widget.chartType || 'bar') === ct} onclick={() => {
                        const widgets = [...activeDashboard.widgets];
                        widgets[wi] = { ...widgets[wi], chartType: ct };
                        activeDashboard = { ...activeDashboard, widgets };
                      }}>{ct.toUpperCase()}</button>
                    {/each}
                  </div>
                  <div style="height: 280px;">
                    <EChartView headers={widget.headers} rows={(widget.rows || []).map((r: any[]) => r.map((c: any) => String(c ?? '')))} chartType={widget.chartType || 'bar'} />
                  </div>
                  <details style="margin-top: 8px;">
                    <summary style="font-size: 11px; font-weight: 700; text-transform: uppercase; cursor: pointer; color: var(--color-on-surface-dim);">VIEW DATA ({widget.rows?.length} rows)</summary>
                    <div style="overflow-x: auto; margin-top: 6px;">
                      <table class="data-table" style="font-size: 10px;">
                        <thead><tr>{#each widget.headers as h}<th style="padding: 4px 8px;">{h}</th>{/each}</tr></thead>
                        <tbody>{#each widget.rows as row}<tr>{#each row as cell}<td style="padding: 3px 8px;">{cell}</td>{/each}</tr>{/each}</tbody>
                      </table>
                    </div>
                  </details>
                {:else if widget.type === 'metric'}
                  <div style="text-align: center; padding: 20px;">
                    <div style="font-size: 30px; font-weight: 900; color: var(--color-primary);">{widget.content || '0'}</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--color-on-surface-dim); letter-spacing: 0.1em; margin-top: 4px;">{widget.title}</div>
                  </div>
                {:else if widget.type === 'table' && widget.headers && widget.rows}
                  <div style="overflow-x: auto;">
                    <table class="data-table" style="font-size: 11px; width: 100%;">
                      <thead><tr>{#each widget.headers as h}<th style="padding: 6px 10px;">{h}</th>{/each}</tr></thead>
                      <tbody>{#each widget.rows as row}<tr>{#each row as cell}<td style="padding: 5px 10px;">{cell}</td>{/each}</tr>{/each}</tbody>
                    </table>
                  </div>
                {:else}
                  <div class="prose-chat">{@html markdownToHtml(widget.content || '')}</div>
                {/if}
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div style="text-align: center; padding: 60px; color: var(--color-on-surface-dim);">
          <div style="font-size: 13px; font-weight: 900; margin-bottom: 8px;">EMPTY DASHBOARD</div>
          <div style="font-size: 11px; margin-bottom: 20px;">Go to <strong>chat</strong> and click <strong>PIN</strong> on any response to add widgets.</div>
          <button class="send-btn" style="font-size: 11px; padding: 8px 16px;" onclick={() => goto(`${base}/project/${slug}`)}>GO TO CHAT</button>
        </div>
      {/if}

      {#if editMode}
        <div style="margin-top: 20px; text-align: center;">
          <button class="feedback-btn" style="font-size: 10px; padding: 6px 14px; color: var(--color-error); border-color: var(--color-error);" onclick={deleteDashboard}>DELETE THIS DASHBOARD</button>
        </div>
      {/if}
    </div>
  {/if}
</div>

<!-- Create modal -->
{#if showCreate}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div style="position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 200; display: flex; align-items: center; justify-content: center;" onclick={(e) => { if (e.target === e.currentTarget) showCreate = false; }}>
    <div class="ink-border stamp-shadow" style="background: var(--color-surface); width: 360px;">
      <div class="dark-title-bar" style="padding: 8px 14px; font-size: 11px;">CREATE DASHBOARD</div>
      <div style="padding: 16px;">
        <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">DASHBOARD NAME</div>
        <input type="text" bind:value={newDashName} placeholder="e.g. Sales Overview, Weekly Report..." style="width: 100%; border: 2px solid var(--pw-border, #e5ddcf); padding: 8px 12px; font-family: var(--font-family-display); font-size: 12px; background: var(--color-surface); margin-bottom: 14px;" />
        <div class="flex gap-2">
          <button class="send-btn" onclick={createDashboard} style="flex: 1; padding: 8px; font-size: 11px; justify-content: center; display: flex;">CREATE</button>
          <button class="feedback-btn" onclick={() => showCreate = false} style="flex: 1; padding: 8px; font-size: 11px; justify-content: center; display: flex; font-weight: 700;">CANCEL</button>
        </div>
      </div>
    </div>
  </div>
{/if}
