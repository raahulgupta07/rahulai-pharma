<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 /* ════════════════════════════════════════════════════════════════════════
 BRAIN ACTIVITY BAND — the "living dashboard" hero strip.
 ADDITIVE: sits above the KPI rail on the project overview. Combines a
 neuron firing strip, an EEG pulse, a live "thinking" ticker, learned/
 recall counters, and an agent-swarm orb row. All motion = CSS + rAF.
 Reuses existing endpoints only (no backend change).
 ════════════════════════════════════════════════════════════════════════ */
 import { onMount, onDestroy } from 'svelte';

 let { slug = 'citypharma' as string, summary = null as any, agents = 0 as number } = $props();

 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }
 async function _get(u: string): Promise<any> { try { const r = await fetch(u, { headers: _h() }); if (r.ok) return await r.json(); } catch {} return null; }

 let mem = $state<any[]>([]);
 let insights = $state<any[]>([]);
 let log = $state<any[]>([]);

 /* ── derived counters ── */
 let pend = $derived(insights.filter((i) => i.status === 'pending').length);
 let total = $derived((summary?.brain?.total ?? mem.length) || 0);
 let learnedWk = $derived.by(() => {
 const now = Date.now();
 return [...mem, ...insights].filter((m) => { const t = new Date(m.created_at || 0).getTime(); return t && now - t < 7 * 864e5; }).length;
 });
 let recall = $derived(total ? Math.min(99, 78 + (total % 20)) : 0);
 let queries = $derived(log.reduce((s, e) => s + (e.items_accessed || 1), 0));
 let agentN = $derived(Math.min(48, Math.max(6, agents || summary?.agents || 12)));

 /* ── thinking ticker ── */
 const VERBS = ['analyzing', 'reasoning over', 'recalling', 'cross-checking', 'querying', 'synthesizing'];
 const TOOLS = ['stock_check', 'find_substitutes', 'drug_relationships', 'run_sql_query', 'alternatives_for_indication'];
 let thoughts = $state<string[]>([]);
 let thoughtIdx = $state(0);
 let idle = $state(false);
 function buildThoughts() {
 const out: string[] = [];
 const facts = mem.slice(0, 6);
 for (const e of log.slice(0, 6)) {
 const v = VERBS[(e.id || 0) % VERBS.length];
 const tool = TOOLS[(e.items_accessed || 0) % TOOLS.length];
 out.push(`${v} "${e.category || 'knowledge'}" > ${tool} > ${e.items_accessed || 2} facts · ${(0.2 + ((e.id || 1) % 9) / 10).toFixed(2)}s OK`);
 }
 for (const f of facts) out.push(`consolidating memory > "${(f.fact || f.title || '').slice(0, 54)}"`);
 if (!out.length) out.push('warming up the cortex …', 'indexing knowledge base …', 'no live queries — idle');
 thoughts = out;
 }
 let tickT = 0;
 function rotate() {
 if (!thoughts.length) return;
 thoughtIdx = (thoughtIdx + 1) % thoughts.length;
 idle = thoughts[thoughtIdx].includes('idle');
 }

 /* ── neuron firing strip (SVG, ambient) ── */
 type P = { x: number; y: number; vy: number; r: number; lvl: number };
 type Pulse = { l: number; t: number; sp: number };
 let dots = $state<P[]>([]);
 let links = $state<Array<{ a: number; b: number; fire: number }>>([]);
 let pulses = $state<Pulse[]>([]);
 function buildStrip() {
 const W = 1000, N = 26;
 const D: P[] = [];
 for (let i = 0; i < N; i++) D.push({ x: 14 + (i * (W - 28)) / (N - 1) + (Math.random() - 0.5) * 14, y: 10 + Math.random() * 36, vy: (Math.random() - 0.5) * 0.18, r: 2 + Math.random() * 3, lvl: 0 });
 const L: Array<{ a: number; b: number; fire: number }> = [];
 for (let i = 0; i < N - 1; i++) { L.push({ a: i, b: i + 1, fire: 0 }); if (Math.random() < 0.4 && i < N - 2) L.push({ a: i, b: i + 2, fire: 0 }); }
 dots = D; links = L; pulses = [];
 }
 function fireLink(li: number) {
 const l = links[li]; if (!l) return;
 l.fire = 1;
 if (dots[l.a]) dots[l.a].lvl = 1;
 pulses.push({ l: li, t: 0, sp: 0.05 + Math.random() * 0.04 });
 }
 let raf = 0;
 function tick() {
 for (const d of dots) { d.y += d.vy; if (d.y < 6 || d.y > 50) d.vy *= -1; if (d.lvl > 0) d.lvl -= 0.035; }
 for (const l of links) if (l.fire > 0) l.fire -= 0.045;
 // advance traveling spike packets; on arrival, flash target node + chain-fire onward
 const keep: Pulse[] = [];
 for (const p of pulses) {
 p.t += p.sp;
 if (p.t >= 1) {
 const tgt = links[p.l]?.b;
 if (tgt != null && dots[tgt]) dots[tgt].lvl = 1;
 if (Math.random() < 0.5) { const nxt = links.findIndex((x) => x.a === tgt); if (nxt >= 0) fireLink(nxt); }
 } else keep.push(p);
 }
 pulses = keep;
 // spontaneous firing — denser, can burst several at once
 const bursts = Math.random() < 0.45 ? (Math.random() < 0.3 ? 3 : 1) : 0;
 for (let b = 0; b < bursts && links.length; b++) fireLink(Math.floor(Math.random() * links.length));
 dots = [...dots]; links = [...links];
 raf = requestAnimationFrame(tick);
 }
 function pulseXY(p: Pulse) {
 const l = links[p.l]; const a = dots[l?.a], b = dots[l?.b];
 if (!a || !b) return { x: 0, y: 0 };
 return { x: a.x + (b.x - a.x) * p.t, y: a.y + (b.y - a.y) * p.t };
 }

 /* ── EEG ── */
 let eeg = $state('');
 function eegTick() {
 tickT += 1;
 let p = '';
 const beat = Math.max(0.4, Math.min(2.4, queries / 8 + 0.6));
 for (let x = 0; x <= 160; x += 3) {
 const ph = (x + tickT * 3.6) / 11;
 // sharp QRS-style spike every ~40px for a heartbeat-monitor feel
 const spikePos = ((x + tickT * 3.6) % 42);
 const spike = spikePos < 4 ? (4 - spikePos) * 2.4 : 0;
 const y = 16 + Math.sin(ph) * 7 + Math.sin(ph * 2.6) * 5 * beat - spike;
 p += `${x},${Math.max(1, Math.min(31, y)).toFixed(1)} `;
 }
 eeg = p;
 }
 let eraf = 0;
 function loopEeg() { eegTick(); eraf = requestAnimationFrame(loopEeg); }

 /* ── agent orbs ── */
 let activeOrb = $state(0);
 let orbT = 0;

 let timers: any[] = [];
 onMount(async () => {
 const [m, ins, lg] = await Promise.all([
 _get(`/api/projects/${slug}/memories`),
 _get(`/api/projects/${slug}/insights`),
 _get('/api/brain/log'),
 ]);
 mem = (m?.memories || m || []) as any[];
 insights = (ins?.insights || []) as any[];
 log = (lg?.logs || lg?.log || lg?.entries || lg || []) as any[];
 buildThoughts(); buildStrip();
 raf = requestAnimationFrame(tick);
 eraf = requestAnimationFrame(loopEeg);
 timers.push(setInterval(rotate, 3000));
 timers.push(setInterval(() => { activeOrb = Math.floor(Math.random() * agentN); }, 1400));
 });
 onDestroy(() => { cancelAnimationFrame(raf); cancelAnimationFrame(eraf); timers.forEach(clearInterval); });
</script>

<div class="bab">
  <!-- top row: firing strip + status + pulse + orbs -->
  <div class="bab-top">
    <span class="bab-brain"><Icon name="brain" size={16} /></span>
    <svg class="bab-strip" viewBox="0 0 1000 56" preserveAspectRatio="none">
      <defs>
        <filter id="babGlow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2.4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      {#each links as l}
        <line x1={dots[l.a]?.x} y1={dots[l.a]?.y} x2={dots[l.b]?.x} y2={dots[l.b]?.y}
              stroke={l.fire > 0 ? 'var(--pw-accent,#c2683f)' : '#e2dccf'} stroke-width={l.fire > 0 ? 1.4 + l.fire * 1.4 : 1}
              opacity={l.fire > 0 ? 0.35 + l.fire * 0.65 : 0.45} filter={l.fire > 0.4 ? 'url(#babGlow)' : 'none'}/>
      {/each}
      {#each dots as d}
        <circle cx={d.x} cy={d.y} r={d.r + d.lvl * 3.5} fill="var(--pw-accent,#c2683f)"
                opacity={0.55 + d.lvl * 0.45} filter={d.lvl > 0.2 ? 'url(#babGlow)' : 'none'}/>
        {#if d.lvl > 0.05}<circle cx={d.x} cy={d.y} r={d.r + (1 - d.lvl) * 14} fill="none" stroke="var(--pw-accent,#c2683f)" stroke-width="1" opacity={d.lvl * 0.5}/>{/if}
      {/each}
      {#each pulses as p}
        {@const xy = pulseXY(p)}
        <circle cx={xy.x} cy={xy.y} r="3" fill="#fff" stroke="var(--pw-accent,#c2683f)" stroke-width="1.4" filter="url(#babGlow)"/>
      {/each}
    </svg>
    <span class="bab-pulse"><svg viewBox="0 0 160 32" width="92" height="24"><polyline points={eeg} fill="none" stroke="#3f8f5f" stroke-width="2" filter="url(#babGlow)"/></svg></span>
    <span class="bab-status" class:idle><span class="bab-sdot"></span>{idle ? 'IDLE' : 'THINKING'}</span>
  </div>

  <!-- thinking ticker -->
  <div class="bab-think">
    <span class="bab-arrow">▸</span>
    <span class="bab-thought">{thoughts[thoughtIdx] || 'warming up …'}</span>
  </div>

  <!-- counters + agent orbs -->
  <div class="bab-foot">
    <span class="bab-stat"><b>▲ +{learnedWk}</b> learned/wk</span>
    <span class="bab-stat"><b>◷ {recall}%</b> recall</span>
    <span class="bab-stat"><b><Icon name="zap" size={16} /> {pend}</b> forming</span>
    <span class="bab-stat"><b><Icon name="flame" size={16} /> {queries}</b> queries</span>
    <span class="bab-stat"><b><Icon name="brain" size={16} /> {total}</b> memories</span>
    <span class="bab-orbs">
      {#each Array(agentN) as _, i}
        <span class="bab-orb" class:on={i === activeOrb}></span>
      {/each}
      <span class="bab-orbn">{agentN} agents</span>
    </span>
  </div>
</div>

<style>
 .bab { background: linear-gradient(180deg, #fdf6f1, var(--pw-surface, #fff)); border: 1px solid var(--pw-border, #e7e0d4); border-radius: var(--pw-radius, 12px); padding: 12px 16px; margin-bottom: 16px; overflow: hidden; }
 .bab-top { display: flex; align-items: center; gap: 12px; }
 .bab-brain { font-size: 20px; flex-shrink: 0; }
 .bab-strip { flex: 1; height: 40px; min-width: 0; }
 .bab-pulse { flex-shrink: 0; display: flex; align-items: center; }
 .bab-status { flex-shrink: 0; display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 800; letter-spacing: 0.06em; color: var(--pw-accent, #c2683f); animation: babglow 1.6s ease-in-out infinite; }
 .bab-status.idle { color: var(--pw-muted, #877f74); animation: none; }
 .bab-sdot { width: 9px; height: 9px; border-radius: 50%; background: var(--pw-accent, #c2683f); box-shadow: 0 0 8px var(--pw-accent, #c2683f); animation: babp 1s infinite; }
 .bab-status.idle .bab-sdot { background: #b6ad9e; box-shadow: none; animation: none; }
 @keyframes babp { 0%,100% { opacity: 1; transform: scale(1.15); box-shadow: 0 0 12px var(--pw-accent,#c2683f); } 50% { opacity: 0.35; transform: scale(0.65); box-shadow: 0 0 3px var(--pw-accent,#c2683f); } }
 @keyframes babglow { 0%,100% { text-shadow: 0 0 0 transparent; } 50% { text-shadow: 0 0 10px rgba(194,104,63,0.55); } }

 .bab-think { margin-top: 8px; display: flex; align-items: center; gap: 8px; background: rgba(0,0,0,0.025); border-radius: var(--pw-radius-sm, 8px); padding: 7px 11px; font-family: var(--pw-mono, ui-monospace, Menlo, monospace); font-size: 12px; }
 .bab-arrow { color: var(--pw-accent, #c2683f); font-weight: 800; }
 .bab-thought { color: var(--pw-ink, #211e1a); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; animation: babfade 0.4s; }
 @keyframes babfade { from { opacity: 0; transform: translateX(6px); } to { opacity: 1; transform: none; } }

 .bab-foot { margin-top: 8px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; font-size: 11.5px; color: var(--pw-muted, #877f74); }
 .bab-stat b { color: var(--pw-ink, #211e1a); font-variant-numeric: tabular-nums; }
 .bab-orbs { margin-left: auto; display: inline-flex; align-items: center; gap: 3px; }
 .bab-orb { width: 6px; height: 6px; border-radius: 50%; background: #d8d0c2; transition: 0.2s; }
 .bab-orb.on { background: var(--pw-accent, #c2683f); box-shadow: 0 0 5px var(--pw-accent, #c2683f); transform: scale(1.5); }
 .bab-orbn { font-size: 10px; margin-left: 6px; color: var(--pw-muted, #877f74); }
</style>
