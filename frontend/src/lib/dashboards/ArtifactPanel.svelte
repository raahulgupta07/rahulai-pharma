<script>
  import Icon from '$lib/Icon.svelte';
 import DashRenderer from '$lib/dashboards/DashRenderer.svelte';
 let { open = false, spec = null, data = {}, thinkingLog = [], deepening = false, findings = [], onClose, onSave, onRegenerate = null } = $props();
 let logExpanded = $state(false);
 let showFindings = $state(false);
 const lastLines = $derived(
 logExpanded ? (thinkingLog || []) : (thinkingLog || []).slice(-5)
 );
 function cleanTitle(t) {
 if (!t) return '';
 return String(t)
 .replace(/critical\s+style\s+rule.*/i, '')
 .replace(/fast\s+mode\b.*/i, '')
 .replace(/^build\s+dashboard\s+covering[:\s]*/i, '')
 .replace(/^\d+\)\s*/, '')
 .replace(/^[\s\-—:>•*#]+/, '')
 .trim()
 .slice(0, 80);
 }
 const headerTitle = $derived(cleanTitle(spec?.title) || 'DEEP DASHBOARD');
</script>

{#if open}
  <div class="artifact-panel" class:mobile={true}>
    <div class="ap-header">
      <div class="ap-title">
        <span class="ap-dot" class:pulse={deepening}>●</span>
        <span>{headerTitle}</span>
      </div>
      <div class="ap-actions">
        <button class="findings-toggle" onclick={() => showFindings = !showFindings}><Icon name="search" size={14} /> FINDINGS ({(findings || []).length})</button>
        {#if onRegenerate}
          <button class="ap-btn regen" onclick={onRegenerate} title="Regenerate"><Icon name="refresh" size={14} /> REGENERATE</button>
        {/if}
        <button class="ap-btn save" onclick={onSave} disabled={!spec}>SAVE</button>
        <button class="ap-btn close" onclick={onClose} aria-label="Close"><Icon name="x" size={14} /></button>
      </div>
    </div>

    <div class="ap-body">
      {#if spec}
        <DashRenderer {spec} {data} />
      {:else}
        <div class="ap-empty">
          <div class="spinner">●</div>
          <div>Generating dashboard...</div>
        </div>
      {/if}

      {#if showFindings}
        <div class="findings-panel">
          <h3>Findings ({(findings || []).length})</h3>
          {#each ['high','medium','low'] as sev}
            {@const group = (findings || []).filter((f) => f.severity === sev)}
            {#if group.length}
              <div class="finding-group sev-{sev}">
                <div class="group-label">{sev.toUpperCase()} ({group.length})</div>
                {#each group as f}
                  <div class="finding-item">
                    <div class="f-headline">{f.headline}</div>
                    {#if f.cause_hypothesis}<div class="f-cause">→ {f.cause_hypothesis}</div>{/if}
                    {#if f.suggested_action}<div class="f-action"><Icon name="zap" size={14} /> {f.suggested_action}</div>{/if}
                    {#if f.domain_tags?.length}<div class="f-tags">{f.domain_tags.join(' · ')}</div>{/if}
                  </div>
                {/each}
              </div>
            {/if}
          {/each}
          {#if !(findings || []).length}
            <div class="findings-empty">No findings yet.</div>
          {/if}
        </div>
      {/if}
    </div>

    <div class="ap-footer" class:expanded={logExpanded}>
      <button class="ap-log-toggle" onclick={() => logExpanded = !logExpanded}>
        <span class="log-pulse" class:pulse={deepening}>●</span>
        <span>THINKING LOG ({(thinkingLog || []).length})</span>
        <span class="caret">{logExpanded ? '▼' : '▲'}</span>
      </button>
      <div class="ap-log">
        {#each lastLines as line}
          {@const cls = line.startsWith('[SCOUT]') ? 'log-scout' : line.startsWith('[DESIGNER]') ? 'log-designer' : line.startsWith('[ERROR]') ? 'log-error' : line.startsWith('[ORCH]') ? 'log-orch' : ''}
          <div class="log-line {cls}">{line}</div>
        {/each}
      </div>
    </div>
  </div>
{/if}

<style>
 .artifact-panel {
 position: fixed;
 top: 0;
 right: 0;
 bottom: 0;
 width: 35%;
 min-width: 420px;
 background: #fafaf7;
 border-left: 2px solid #e0e0d8;
 box-shadow: -4px 0 16px rgba(0,0,0,0.08);
 display: flex;
 flex-direction: column;
 z-index: 10000;
 animation: slideIn 200ms ease-out;
 }
 @keyframes slideIn {
 from { transform: translateX(100%); }
 to { transform: translateX(0); }
 }
 @media (max-width: 900px) {
 .artifact-panel {
 width: 100% !important;
 max-width: 100vw;
 min-width: 0;
 z-index: 9999;
 }
 }
 .ap-header {
 display: flex; align-items: center; justify-content: space-between;
 padding: 10px 14px;
 background: #fff;
 border-bottom: 2px solid #e0e0d8;
 }
 .ap-title {
 display: flex; align-items: center; gap: 8px;
 font-size: 11px; font-weight: 900;
 text-transform: uppercase; letter-spacing: 0.08em;
 color: #1a1a1a;
 }
 .ap-dot { color: #2196f3; font-size: 10px; }
 .ap-dot.pulse { animation: pulse 1.2s infinite; }
 @keyframes pulse { 50% { opacity: 0.3; } }
 .ap-actions { display: flex; gap: 6px; }
 .ap-btn {
 border: 2px solid #1a1a1a;
 background: #fff;
 padding: 5px 10px;
 font-size: 10px; font-weight: 900;
 text-transform: uppercase; letter-spacing: 0.08em;
 cursor: pointer;
 font-family: inherit;
 }
 .ap-btn.save { background: #2e7d32; color: #fff; border-color: #2e7d32; }
 .ap-btn.regen { background: #f57c00; color: #fff; border-color: #f57c00; }
 .ap-btn.save:disabled { background: #ccc; border-color: #ccc; cursor: not-allowed; }
 .ap-btn.close { width: 28px; padding: 0; height: 26px; }
 .ap-body {
 flex: 1; overflow-y: auto; overflow-x: hidden;
 background: #fafaf7;
 padding: 12px;
 position: relative;
 }
 .ap-empty {
 display: flex; flex-direction: column; align-items: center; justify-content: center;
 height: 100%; gap: 12px; color: #888; font-size: 11px;
 }
 .spinner { font-size: 16px; color: #2196f3; animation: pulse 1s infinite; }
 .ap-footer {
 border-top: 2px solid #e0e0d8;
 background: #1a1a1a;
 color: #d4d4d4;
 max-height: 38px;
 transition: max-height 200ms ease;
 overflow: hidden;
 }
 .ap-footer.expanded { max-height: 240px; }
 .ap-log-toggle {
 width: 100%;
 display: flex; align-items: center; gap: 8px;
 padding: 10px 14px;
 background: transparent;
 border: none;
 color: #d4d4d4;
 font-size: 10px; font-weight: 700;
 text-transform: uppercase; letter-spacing: 0.08em;
 cursor: pointer;
 font-family: 'SF Mono', Menlo, monospace;
 }
 .log-pulse { color: #4caf50; font-size: 11px; }
 .log-pulse.pulse { animation: pulse 1.2s infinite; }
 .caret { margin-left: auto; opacity: 0.6; }
 .ap-log {
 padding: 4px 14px 12px;
 font-family: 'SF Mono', Menlo, monospace;
 font-size: 10.5px;
 line-height: 1.5;
 max-height: 200px;
 overflow-y: auto;
 }
 .log-line {
 padding: 1px 0;
 white-space: pre-wrap;
 word-break: break-word;
 }
 .log-scout { color: #00bcd4; }
 .log-designer { color: #e91e63; }
 .log-orch { color: #4caf50; }
 .log-error { color: #f44336; }
 .findings-toggle { background: #f1f8e9; border: 1px solid #2e7d32; color: #2e7d32; padding: 4px 10px; border-radius: var(--pw-radius-sm); font-size: 11px; cursor: pointer; font-family: inherit; font-weight: 700; }
 .findings-panel { position: absolute; top: 60px; right: 8px; bottom: 8px; width: 320px; background: #fff; border: 1px solid #e0e0d8; border-radius: var(--pw-radius-sm); padding: 12px; overflow-y: auto; z-index: 50; box-shadow: -4px 0 12px rgba(0,0,0,0.1); }
 .findings-panel h3 { margin: 0 0 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; }
 .findings-empty { color: #888; font-size: 11px; }
 .finding-group { margin-bottom: 16px; }
 .group-label { font-size: 10px; color: #666; letter-spacing: 0.5px; margin-bottom: 6px; font-weight: 700; }
 .sev-high .group-label { color: #c62828; }
 .sev-medium .group-label { color: #e65100; }
 .finding-item { padding: 8px; border-left: 3px solid #e0e0d8; margin-bottom: 6px; background: #fafaf7; font-size: 11px; }
 .sev-high .finding-item { border-left-color: #c62828; }
 .sev-medium .finding-item { border-left-color: #e65100; }
 .f-headline { font-weight: 600; }
 .f-cause { color: #666; margin-top: 4px; }
 .f-action { color: #2e7d32; margin-top: 4px; }
 .f-tags { font-size: 11px; color: #888; margin-top: 6px; }
</style>
