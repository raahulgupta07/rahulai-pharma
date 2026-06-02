<script lang="ts">
 import type { ToolCall } from './api';

 interface Props {
 toolCalls: ToolCall[];
 agentName?: string;
 qualityScore?: number;
 }

 let { toolCalls = [], agentName = 'Agent', qualityScore }: Props = $props();

 function totalTokens(): { input: number; output: number } {
 let input = 0, output = 0;
 for (const t of toolCalls) {
 if (t.tokens) {
 input += t.tokens.input || 0;
 output += t.tokens.output || 0;
 }
 }
 return { input, output };
 }

 function totalDuration(): string {
 let total = 0;
 for (const t of toolCalls) {
 if (t.duration) total += parseFloat(t.duration);
 }
 return total > 0 ? total.toFixed(2) + 's' : '—';
 }

 function renderStars(score: number | undefined): string {
 if (!score) return '—';
 return ''.repeat(score) + ''.repeat(5 - score);
 }

 const tokens = $derived(totalTokens());
</script>

<div class="cli-terminal" style="margin-top: 8px; padding: 12px 16px;">
  <div class="cli-line">
    <span class="cli-prompt">$</span>
    <span class="cli-command">trace</span>
    <span class="cli-output">--agent {agentName}</span>
  </div>

  <!-- Tool call tree -->
  {#if toolCalls.length > 0}
    {#each toolCalls as tool}
      <div class="cli-line">
        <span style="color: #555;">&gt;</span>
        {#if tool.status === 'done'}
          <span class="cli-check">&#10003;</span>
        {:else}
          <span style="color: #ff9d00;">&#9679;</span>
        {/if}
        <span class="cli-command">{tool.name}</span>
        {#if tool.duration}
          <span class="cli-dim" style="margin-left: auto;">{tool.duration}</span>
        {/if}
      </div>
    {/each}
  {:else}
    <div class="cli-line">
      <span style="color: #555;">&gt;</span>
      <span class="cli-output">direct response (no tools)</span>
    </div>
  {/if}

  <!-- Summary -->
  <div style="margin-top: 8px; border-top: 1px solid #333; padding-top: 8px;">
    <div class="cli-line">
      <span class="cli-info">DURATION</span>
      <span class="cli-dim" style="margin-left: auto;">{totalDuration()}</span>
    </div>
    <div class="cli-line">
      <span class="cli-info">STEPS</span>
      <span class="cli-dim" style="margin-left: auto;">{toolCalls.length}</span>
    </div>
    <div class="cli-line">
      <span class="cli-info">MODEL</span>
      <span class="cli-dim" style="margin-left: auto;">openai/gpt-5.4-mini</span>
    </div>
    {#if qualityScore}
      <div class="cli-line">
        <span class="cli-info">QUALITY</span>
        <span style="margin-left: auto; color: {qualityScore >= 4 ? '#00fc40' : qualityScore >= 3 ? '#ff9d00' : '#ff6b6b'};">{renderStars(qualityScore)} ({qualityScore}/5)</span>
      </div>
    {/if}
  </div>
</div>
