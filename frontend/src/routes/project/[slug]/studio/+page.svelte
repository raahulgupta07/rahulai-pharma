<script lang="ts">
  /**
   * /project/[slug]/studio — NEW dashboard build session.
   *
   * Project-scoped Studio. Passes `slug` into every SSE call so backend
   * `/api/dashboards/deep-build/stream` can satisfy its `project_slug`
   * requirement. On `done`, URL rewrites to /project/{slug}/studio/{id}.
   */
  import { page } from '$app/state';
  import StudioShell from '$lib/dashboards/StudioShell.svelte';

  const slug = $derived(page.params.slug || '');
</script>

<StudioShell {slug} dashId={null} />
