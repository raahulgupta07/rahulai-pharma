<script lang="ts">
  /**
   * DEPRECATED — old project-less Studio route.
   *
   * The canonical Studio is now project-scoped:
   *   /project/{slug}/studio       (new session)
   *   /project/{slug}/studio/{id}  (refine existing)
   *
   * Without a project_slug the backend /api/dashboards/deep-build/stream
   * cannot fire. We redirect to /projects so the user can pick one.
   */
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';

  onMount(() => {
    goto(`${base}/projects`, { replaceState: true });
  });
</script>

<div style="position: fixed; inset: 56px 0 0 0; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 12px; background: var(--pw-bg, #fdfaf5); color: var(--pw-muted, #999); font-family: -apple-system, system-ui, sans-serif;">
  <div style="font-size: 14px;">Studio now lives inside a project.</div>
  <a href="{base}/projects" style="font-size: 13px; color: var(--pw-accent, #c96342); text-decoration: none;">Pick a project →</a>
</div>
