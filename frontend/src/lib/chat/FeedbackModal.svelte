<script lang="ts">
  /**
   * Feedback comment capture. Opens on a thumb click and collects WHY before
   * POSTing to /feedback — so a 👎 carries a real training signal (reason + the
   * correct answer/SQL) instead of a bare negative. Self-contained: does the
   * POST itself; host only opens it with the message payload.
   */
  interface Payload {
    question: string;
    answer: string;
    sql?: string;
    sqlQueries?: string[];
    rating: 'up' | 'down';
    sessionId?: string;
  }
  let {
    open = $bindable(false),
    slug = '',
    payload = null as Payload | null,
    headers = (() => ({})) as () => Record<string, string>,
    ondone = (_r: any) => {},
  } = $props();

  let comment = $state('');
  let correction = $state('');
  let tags = $state<Set<string>>(new Set());
  let busy = $state(false);
  let err = $state('');

  const DOWN_TAGS = ['wrong number', 'wrong table', 'missing rows', 'bad format', 'too slow', 'off-topic', 'other'];
  const isDown = $derived(payload?.rating === 'down');

  function reset() { comment = ''; correction = ''; tags = new Set(); err = ''; busy = false; }
  function toggleTag(t: string) {
    const n = new Set(tags);
    n.has(t) ? n.delete(t) : n.add(t);
    tags = n;
  }
  function close() { open = false; reset(); }

  // re-init each time it opens
  $effect(() => { if (open) reset(); });

  async function submit(skip = false) {
    if (!payload || !slug) { close(); return; }
    busy = true; err = '';
    const body: any = {
      question: payload.question,
      answer: payload.answer,
      rating: payload.rating,
      sql: payload.sql || '',
      session_id: payload.sessionId || '',
    };
    if (!skip) {
      if (comment.trim()) body.comment = comment.trim();
      if (tags.size) body.tags = [...tags];
      if (correction.trim()) body.correction = correction.trim();
    }
    try {
      const res = await fetch(`/api/projects/${slug}/feedback`, {
        method: 'POST',
        headers: { ...headers(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      // 👍 also logs proven query patterns (mirrors prior host behavior)
      if (payload.rating === 'up' && payload.sqlQueries?.length) {
        for (const sql of payload.sqlQueries) {
          fetch(`/api/projects/${slug}/save-query-pattern`, {
            method: 'POST',
            headers: { ...headers(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: payload.question, sql }),
          }).catch(() => {});
        }
      }
      ondone(data);
      close();
    } catch (e: any) {
      err = 'Could not save feedback. Try again.';
      busy = false;
    }
  }
</script>

{#if open && payload}
  <div class="fbm-back" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) close(); }}>
    <div class="fbm" role="dialog" aria-modal="true" aria-label="Feedback">
      <div class="fbm-hd">
        <span class="fbm-thumb" class:down={isDown}>{isDown ? '👎' : '👍'}</span>
        <div class="fbm-ttl">{isDown ? 'What went wrong?' : 'Nice — what made this good?'}</div>
        <button class="fbm-x" aria-label="Close" onclick={close}>✕</button>
      </div>

      <div class="fbm-bd">
        {#if isDown}
          <div class="fbm-chips">
            {#each DOWN_TAGS as t}
              <button class="fbm-chip" class:on={tags.has(t)} onclick={() => toggleTag(t)}>{t}</button>
            {/each}
          </div>
        {/if}

        <textarea
          class="fbm-ta"
          rows={isDown ? 3 : 2}
          placeholder={isDown ? 'Tell the agent what was wrong (optional)…' : 'Optional — what was helpful?'}
          bind:value={comment}></textarea>

        {#if isDown}
          <label class="fbm-lbl">Correct answer or SQL <span>(optional — reviewed before it trains)</span></label>
          <textarea class="fbm-ta fbm-mono" rows="3"
            placeholder="The right number, the correct SQL, or how it should be answered…"
            bind:value={correction}></textarea>
        {/if}

        {#if err}<div class="fbm-err">{err}</div>{/if}
      </div>

      <div class="fbm-ft">
        <button class="fbm-btn fbm-ghost" disabled={busy} onclick={() => submit(true)}>Skip</button>
        <button class="fbm-btn fbm-go" disabled={busy} onclick={() => submit(false)}>
          {busy ? 'Saving…' : 'Submit'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .fbm-back {
    position: fixed; inset: 0; z-index: 9000;
    background: rgba(20, 14, 10, 0.38);
    display: flex; align-items: center; justify-content: center; padding: 1rem;
    backdrop-filter: blur(2px);
  }
  .fbm {
    width: min(520px, 94vw);
    background: #fffdfb; border: 1px solid #e7ddd2; border-radius: 14px;
    box-shadow: 0 24px 64px rgba(60, 36, 20, 0.22);
    overflow: hidden; font-family: inherit;
  }
  .fbm-hd {
    display: flex; align-items: center; gap: .6rem;
    padding: .9rem 1rem; border-bottom: 1px solid #f0e8df;
  }
  .fbm-thumb { font-size: 1.1rem; }
  .fbm-ttl { font-weight: 700; font-size: .98rem; color: #2c2118; flex: 1; }
  .fbm-x {
    border: none; background: none; cursor: pointer; color: #9a8b7c;
    font-size: .95rem; padding: 2px 6px; border-radius: 6px;
  }
  .fbm-x:hover { background: #f3ece4; color: #5a4a3a; }
  .fbm-bd { padding: 1rem; display: flex; flex-direction: column; gap: .65rem; }
  .fbm-chips { display: flex; flex-wrap: wrap; gap: .4rem; }
  .fbm-chip {
    border: 1px solid #e2d6c8; background: #faf6f1; color: #6b5a48;
    border-radius: 999px; padding: .28rem .7rem; font-size: .8rem; cursor: pointer;
    transition: all .12s;
  }
  .fbm-chip:hover { border-color: #cdab8c; }
  .fbm-chip.on { background: #9a4a2f; border-color: #9a4a2f; color: #fff; }
  .fbm-ta {
    width: 100%; box-sizing: border-box; resize: vertical;
    border: 1px solid #e2d6c8; border-radius: 9px; padding: .6rem .7rem;
    font: inherit; font-size: .88rem; color: #2c2118; background: #fff;
  }
  .fbm-ta:focus { outline: none; border-color: #c08a5e; box-shadow: 0 0 0 3px rgba(192,138,94,.15); }
  .fbm-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; }
  .fbm-lbl { font-size: .76rem; font-weight: 600; color: #6b5a48; margin-top: .1rem; }
  .fbm-lbl span { font-weight: 400; color: #a9988a; }
  .fbm-err { color: #b3261e; font-size: .8rem; }
  .fbm-ft {
    display: flex; justify-content: flex-end; gap: .5rem;
    padding: .8rem 1rem; border-top: 1px solid #f0e8df; background: #fcf9f5;
  }
  .fbm-btn {
    border-radius: 9px; padding: .5rem 1.1rem; font-size: .85rem; font-weight: 600;
    cursor: pointer; border: 1px solid transparent;
  }
  .fbm-btn:disabled { opacity: .55; cursor: default; }
  .fbm-ghost { background: #fff; border-color: #e2d6c8; color: #6b5a48; }
  .fbm-ghost:hover:not(:disabled) { background: #f3ece4; }
  .fbm-go { background: #9a4a2f; color: #fff; }
  .fbm-go:hover:not(:disabled) { background: #863d25; }
</style>
