<script lang="ts">
  import { page } from '$app/state';
  import KnowledgeGraph from '$lib/KnowledgeGraph.svelte';

  const slug = $derived(page.params.slug || '');
</script>

<KnowledgeGraph {slug} />
