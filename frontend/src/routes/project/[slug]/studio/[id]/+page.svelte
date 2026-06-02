<script lang="ts">
  /**
   * /project/[slug]/studio/[id] — REFINE existing dashboard.
   *
   * Project-scoped Studio in refine mode. Loads saved spec on mount and
   * routes user prompts to /api/dashboards/{id}/refine with project_slug.
   */
  import { page } from '$app/state';
  import StudioShell from '$lib/dashboards/StudioShell.svelte';

  const slug = $derived(page.params.slug || '');
  const dashId = $derived(page.params.id || null);
</script>

<StudioShell {slug} {dashId} />
