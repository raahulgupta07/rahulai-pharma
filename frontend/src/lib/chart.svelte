<!-- Deprecated: use echart.svelte instead -->
<script lang="ts">
  import EChartView from './echart.svelte';
  import { detectChartType } from './chart-detect';
  import type { ParsedTable } from './table-parser';

  interface Props {
    headers: string[];
    rows: string[][];
    type?: 'bar' | 'line';
  }

  let { headers, rows, type = 'bar' }: Props = $props();
</script>

<EChartView {headers} {rows} chartType={type} />
