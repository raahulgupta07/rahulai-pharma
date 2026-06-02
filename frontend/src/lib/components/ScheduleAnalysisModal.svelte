<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { base } from '$app/paths';
 import { createWorkflowFromChat, presetToCron } from '$lib/api';
 import type { WorkflowFromChatStep } from '$lib/api';

 interface Props {
 open: boolean;
 projectSlug: string;
 msgId?: string;
 initialName?: string;
 initialDescription?: string;
 initialSteps?: WorkflowFromChatStep[];
 onClose: () => void;
 onSaved?: (wfId: number, projectSlug: string) => void;
 }

 let {
 open = $bindable(false),
 projectSlug,
 msgId,
 initialName = '',
 initialDescription = '',
 initialSteps = [],
 onClose,
 onSaved,
 }: Props = $props();

 // ── Form state (Svelte 5 runes) — seeded via $effect on open ──
 let name = $state('');
 let description = $state('');
 let steps = $state<WorkflowFromChatStep[]>([]);
 let preset = $state<'daily' | 'weekly' | 'monthly' | 'hourly' | 'custom' | 'none'>('daily');
 let presetTime = $state('02:00');
 let presetDow = $state(1);
 let presetDay = $state(1);
 let presetEvery = $state(6);
 let presetCustom = $state('0 */6 * * *');
 let actionPostInsight = $state(true);
 let actionEmail = $state(false);
 let saving = $state(false);
 let errorMsg = $state('');
 let saved = $state<{ wf_id: number; project_slug: string } | null>(null);

 // Reset form whenever opened with new context (msgId or initialName change)
 $effect(() => {
 if (open) {
 name = initialName || 'Scheduled analysis';
 description = initialDescription || '';
 steps = Array.isArray(initialSteps) ? initialSteps.map((s) => ({ ...s })) : [];
 errorMsg = '';
 saved = null;
 }
 });

 function addStep() {
 steps = [...steps, { kind: 'sql', sql: '' }];
 }
 function removeStep(idx: number) {
 steps = steps.filter((_, i) => i !== idx);
 }
 function setStepKind(idx: number, kind: 'sql' | 'agent') {
 const cur = steps[idx];
 if (!cur) return;
 steps = steps.map((s, i) =>
 i === idx
 ? kind === 'sql'
 ? { kind: 'sql', sql: cur.sql || '' }
 : { kind: 'agent', agent: cur.agent || 'Analyst', prompt: cur.prompt || '' }
 : s,
 );
 }

 function close() {
 if (saving) return;
 onClose();
 }

 async function save() {
 if (saving) return;
 errorMsg = '';
 const cleanedSteps: WorkflowFromChatStep[] = steps
 .map((s) => {
 if (s.kind === 'sql') {
 const sql = (s.sql || '').trim();
 return sql ? { kind: 'sql' as const, sql } : null;
 }
 const prompt = (s.prompt || '').trim();
 const agent = (s.agent || 'Analyst').trim() || 'Analyst';
 return prompt ? { kind: 'agent' as const, agent, prompt } : null;
 })
 .filter((s): s is WorkflowFromChatStep => s !== null);

 if (!name.trim()) {
 errorMsg = 'Workflow name is required.';
 return;
 }
 if (cleanedSteps.length === 0) {
 errorMsg = 'Add at least one step.';
 return;
 }

 const cron = presetToCron(preset, {
 time: presetTime,
 dow: presetDow,
 day: presetDay,
 every: presetEvery,
 custom: presetCustom,
 });

 const actionStr = actionPostInsight
 ? 'post_insight'
 : actionEmail
 ? 'email'
 : 'none';

 saving = true;
 try {
 const res = await createWorkflowFromChat({
 project_slug: projectSlug,
 chat_msg_id: msgId,
 name: name.trim(),
 description: description.trim(),
 steps: cleanedSteps,
 schedule_cron: cron,
 schedule_action: actionStr,
 });
 saved = res;
 onSaved?.(res.wf_id, res.project_slug);
 } catch (e: any) {
 errorMsg = (e && e.message) || 'Failed to save workflow.';
 } finally {
 saving = false;
 }
 }
</script>

{#if open}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    style="position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 220; display: flex; align-items: center; justify-content: center;"
    onclick={(e) => { if (e.target === e.currentTarget) close(); }}
  >
    <div class="ink-border stamp-shadow" style="background: var(--pw-bg); width: 520px; max-height: 88vh; overflow-y: auto;">
      <div class="dark-title-bar" style="padding: 8px 14px; font-size: 11px; display: flex; justify-content: space-between; align-items: center;">
        <span>SCHEDULE THIS ANALYSIS</span>
        <button onclick={close} style="background: none; border: none; color: inherit; cursor: pointer; font-size: 13px;" aria-label="Close"><Icon name="x" size={14} /></button>
      </div>

      <div style="padding: 16px;">
        {#if saved}
          <div style="border: 2px solid var(--pw-accent, #c96342); background: rgba(201,99,66,0.08); padding: 12px; margin-bottom: 12px;">
            <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-accent, #c96342); margin-bottom: 4px;"><Icon name="check" size={14} /> SAVED AS WORKFLOW</div>
            <div style="font-size: 12px; color: var(--pw-ink);">Workflow #{saved.wf_id} is active for project <strong>{saved.project_slug}</strong>.</div>
            <div style="margin-top: 8px;">
              <a href="{base}/ui/agent-os/workflows" style="font-size: 11px; font-weight: 700; text-decoration: underline; color: var(--pw-accent, #c96342);">View → /ui/agent-os/workflows</a>
            </div>
          </div>
          <div style="display: flex; justify-content: flex-end;">
            <button class="feedback-btn" onclick={close} style="padding: 8px 16px; font-size: 11px; font-weight: 700;">CLOSE</button>
          </div>
        {:else}
          <!-- Name -->
          <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">WORKFLOW NAME</div>
          <input
            type="text"
            bind:value={name}
            placeholder="Stockout check"
            style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg); margin-bottom: 10px;"
          />

          <!-- Description -->
          <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 3px;">DESCRIPTION</div>
          <input
            type="text"
            bind:value={description}
            placeholder="Reorder SKU when stock < 10"
            style="width: 100%; border: 2px solid var(--pw-ink); padding: 6px 10px; font-family: var(--pw-font-body); font-size: 12px; background: var(--pw-bg); margin-bottom: 14px;"
          />

          <!-- Steps -->
          <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 6px;">STEPS</div>
          {#if steps.length === 0}
            <div style="padding: 10px; border: 1px dashed var(--pw-border, #c8c0b0); background: var(--pw-bg-alt, #f5f0e6); font-size: 11px; color: var(--pw-muted, #6b6b6b); margin-bottom: 8px;">
              No steps detected from this chat. Add at least one to schedule.
            </div>
          {:else}
            <div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px;">
              {#each steps as step, si (si)}
                <div style="border: 1px solid var(--pw-border, #c8c0b0); padding: 8px; background: var(--pw-surface, #fff);">
                  <div style="display: flex; gap: 6px; align-items: center; margin-bottom: 6px;">
                    <span style="font-size: 10px; font-weight: 900; color: var(--pw-muted, #6b6b6b);">s{si + 1}</span>
                    <button
                      onclick={() => setStepKind(si, 'sql')}
                      style="font-size: 10px; padding: 2px 8px; border: 1px solid var(--pw-ink); background: {step.kind === 'sql' ? 'var(--pw-ink)' : 'transparent'}; color: {step.kind === 'sql' ? 'var(--pw-bg)' : 'var(--pw-ink)'}; cursor: pointer; font-weight: 700;"
                    >SQL</button>
                    <button
                      onclick={() => setStepKind(si, 'agent')}
                      style="font-size: 10px; padding: 2px 8px; border: 1px solid var(--pw-ink); background: {step.kind === 'agent' ? 'var(--pw-ink)' : 'transparent'}; color: {step.kind === 'agent' ? 'var(--pw-bg)' : 'var(--pw-ink)'}; cursor: pointer; font-weight: 700;"
                    >AGENT</button>
                    <div style="flex: 1;"></div>
                    <button onclick={() => removeStep(si)} aria-label="Remove step" style="background: none; border: none; cursor: pointer; font-size: 13px; color: var(--pw-muted, #6b6b6b);"><Icon name="x" size={14} /></button>
                  </div>
                  {#if step.kind === 'sql'}
                    <textarea
                      value={step.sql || ''}
                      oninput={(e) => { steps = steps.map((s, i) => i === si ? { ...s, sql: (e.currentTarget as HTMLTextAreaElement).value } : s); }}
                      placeholder="SELECT ..."
                      rows="3"
                      style="width: 100%; border: 1px solid var(--pw-border, #c8c0b0); padding: 6px 8px; font-family: ui-monospace, monospace; font-size: 11px; background: var(--pw-bg-alt, #f5f0e6); resize: vertical;"
                    ></textarea>
                  {:else}
                    <div style="display: flex; gap: 6px; margin-bottom: 4px;">
                      <input
                        type="text"
                        value={step.agent || 'Analyst'}
                        oninput={(e) => { steps = steps.map((s, i) => i === si ? { ...s, agent: (e.currentTarget as HTMLInputElement).value } : s); }}
                        placeholder="Analyst"
                        style="width: 140px; border: 1px solid var(--pw-border, #c8c0b0); padding: 4px 8px; font-size: 11px; background: var(--pw-bg);"
                      />
                      <span style="font-size: 11px; color: var(--pw-muted, #6b6b6b); align-self: center;">→</span>
                      <input
                        type="text"
                        value={step.prompt || ''}
                        oninput={(e) => { steps = steps.map((s, i) => i === si ? { ...s, prompt: (e.currentTarget as HTMLInputElement).value } : s); }}
                        placeholder="Summarize result"
                        style="flex: 1; border: 1px solid var(--pw-border, #c8c0b0); padding: 4px 8px; font-size: 11px; background: var(--pw-bg);"
                      />
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
          <button onclick={addStep} style="font-size: 10px; padding: 4px 10px; border: 1px dashed var(--pw-ink); background: transparent; cursor: pointer; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 14px;">+ ADD STEP</button>

          <!-- Schedule -->
          <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 6px;">SCHEDULE</div>
          <div style="display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px;">
            <label style="display: flex; align-items: center; gap: 8px; font-size: 12px;">
              <input type="radio" name="sched-preset" value="daily" checked={preset === 'daily'} onchange={() => preset = 'daily'} />
              <span>Daily at</span>
              <input type="time" bind:value={presetTime} disabled={preset !== 'daily'} style="border: 1px solid var(--pw-border, #c8c0b0); padding: 2px 6px; font-size: 11px;" />
              <span style="font-size: 10px; color: var(--pw-muted, #6b6b6b);">UTC</span>
            </label>
            <label style="display: flex; align-items: center; gap: 8px; font-size: 12px;">
              <input type="radio" name="sched-preset" value="weekly" checked={preset === 'weekly'} onchange={() => preset = 'weekly'} />
              <span>Weekly</span>
              <select bind:value={presetDow} disabled={preset !== 'weekly'} style="border: 1px solid var(--pw-border, #c8c0b0); padding: 2px 6px; font-size: 11px;">
                <option value={0}>Sun</option>
                <option value={1}>Mon</option>
                <option value={2}>Tue</option>
                <option value={3}>Wed</option>
                <option value={4}>Thu</option>
                <option value={5}>Fri</option>
                <option value={6}>Sat</option>
              </select>
              <span>at</span>
              <input type="time" bind:value={presetTime} disabled={preset !== 'weekly'} style="border: 1px solid var(--pw-border, #c8c0b0); padding: 2px 6px; font-size: 11px;" />
            </label>
            <label style="display: flex; align-items: center; gap: 8px; font-size: 12px;">
              <input type="radio" name="sched-preset" value="monthly" checked={preset === 'monthly'} onchange={() => preset = 'monthly'} />
              <span>Monthly day</span>
              <input type="number" min="1" max="28" bind:value={presetDay} disabled={preset !== 'monthly'} style="width: 60px; border: 1px solid var(--pw-border, #c8c0b0); padding: 2px 6px; font-size: 11px;" />
              <span>at</span>
              <input type="time" bind:value={presetTime} disabled={preset !== 'monthly'} style="border: 1px solid var(--pw-border, #c8c0b0); padding: 2px 6px; font-size: 11px;" />
            </label>
            <label style="display: flex; align-items: center; gap: 8px; font-size: 12px;">
              <input type="radio" name="sched-preset" value="hourly" checked={preset === 'hourly'} onchange={() => preset = 'hourly'} />
              <span>Hourly every</span>
              <input type="number" min="1" max="23" bind:value={presetEvery} disabled={preset !== 'hourly'} style="width: 60px; border: 1px solid var(--pw-border, #c8c0b0); padding: 2px 6px; font-size: 11px;" />
              <span>hour(s)</span>
            </label>
            <label style="display: flex; align-items: center; gap: 8px; font-size: 12px;">
              <input type="radio" name="sched-preset" value="custom" checked={preset === 'custom'} onchange={() => preset = 'custom'} />
              <span>Custom</span>
              <input type="text" bind:value={presetCustom} disabled={preset !== 'custom'} placeholder="0 */6 * * *" style="flex: 1; border: 1px solid var(--pw-border, #c8c0b0); padding: 2px 6px; font-family: ui-monospace, monospace; font-size: 11px;" />
            </label>
            <label style="display: flex; align-items: center; gap: 8px; font-size: 12px;">
              <input type="radio" name="sched-preset" value="none" checked={preset === 'none'} onchange={() => preset = 'none'} />
              <span>None (manual only)</span>
            </label>
          </div>

          <!-- Action -->
          <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 6px;">ACTION ON COMPLETION</div>
          <div style="display: flex; flex-direction: column; gap: 4px; margin-bottom: 14px;">
            <label style="display: flex; align-items: center; gap: 8px; font-size: 12px;">
              <input type="checkbox" bind:checked={actionPostInsight} />
              <span>Post insight to chat</span>
            </label>
            <label style="display: flex; align-items: center; gap: 8px; font-size: 12px;">
              <input type="checkbox" bind:checked={actionEmail} />
              <span>Email me</span>
            </label>
          </div>

          {#if errorMsg}
            <div style="border: 1px solid #b03030; background: #fef0f0; color: #8a1a1a; padding: 8px 10px; font-size: 11px; margin-bottom: 10px;">
              {errorMsg}
            </div>
          {/if}

          <div class="flex gap-2" style="justify-content: flex-end;">
            <button class="feedback-btn" onclick={close} disabled={saving} style="padding: 8px 16px; font-size: 11px; font-weight: 700;">CANCEL</button>
            <button class="send-btn" onclick={save} disabled={saving} style="padding: 8px 18px; font-size: 11px; font-weight: 900; display: flex; align-items: center; justify-content: center;">{saving ? 'SAVING…' : 'SAVE & ACTIVATE'}</button>
          </div>
        {/if}
      </div>
    </div>
  </div>
{/if}
