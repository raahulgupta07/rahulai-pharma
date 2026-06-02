<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import * as echarts from 'echarts/core';
  import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
  import { TitleComponent, TooltipComponent, LegendComponent, GridComponent } from 'echarts/components';
  import { CanvasRenderer } from 'echarts/renderers';
  import type { ChartType } from './chart-detect';

  echarts.use([BarChart, LineChart, PieChart, ScatterChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer]);

  interface Props {
    headers: string[];
    rows: string[][];
    chartType?: ChartType;
  }

  let { headers, rows, chartType = 'bar' }: Props = $props();
  let container: HTMLDivElement;
  let chart: echarts.ECharts | null = null;

  const COLORS = ['#c96342', '#6b6b6b', '#9ea0a3', '#d4a574', '#8b9d83', '#5c8a8a'];

  function isNumericish(v: string): boolean {
    if (!v) return false;
    const c = v.replace(/[$,€£¥%\s]/g, '').trim();
    return c !== '' && !isNaN(Number(c));
  }

  function parseNum(v: string): number {
    if (!v) return 0;
    // Remove currency symbols, commas, spaces, percent signs
    const clean = v.replace(/[$,€£¥%\s]/g, '').trim();
    const num = parseFloat(clean);
    return isNaN(num) ? 0 : num;
  }

  function formatLabel(v: string): string {
    if (!v) return '';
    // Format date strings: 20240728 → Jul 28, 2024
    if (/^\d{8}$/.test(v.trim())) {
      const y = v.slice(0, 4);
      const m = parseInt(v.slice(4, 6)) - 1;
      const d = parseInt(v.slice(6, 8));
      const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      return `${months[m]} ${d}`;
    }
    // Format YYYY-MM → readable
    if (/^\d{4}-\d{2}$/.test(v.trim())) {
      const [y, m] = v.split('-');
      const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      return `${months[parseInt(m) - 1]} ${y}`;
    }
    // Truncate long labels
    if (v.length > 15) return v.slice(0, 13) + '..';
    return v;
  }

  function formatValue(val: number): string {
    if (Math.abs(val) >= 1e12) return (val / 1e12).toFixed(1) + 'T';
    if (Math.abs(val) >= 1e9) return (val / 1e9).toFixed(1) + 'B';
    if (Math.abs(val) >= 1e6) return (val / 1e6).toFixed(1) + 'M';
    if (Math.abs(val) >= 1e3) return (val / 1e3).toFixed(1) + 'K';
    return val.toLocaleString();
  }

  function formatTooltipValue(val: number): string {
    if (Math.abs(val) >= 1e9) return (val / 1e9).toFixed(2) + 'B';
    if (Math.abs(val) >= 1e6) return (val / 1e6).toFixed(2) + 'M';
    return val.toLocaleString();
  }

  function buildOption(): echarts.EChartsOption {
    if (!headers?.length || !rows?.length) return {};

    const labels = rows.map(r => formatLabel(r[0] || ''));
    const fontFamily = "'Space Grotesk', monospace";

    if (chartType === 'pie') {
      const data = rows.map((r, i) => ({
        name: r[0] || `Row ${i}`,
        value: parseNum(r[1] || '0'),
      }));
      return {
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { bottom: 0, textStyle: { fontFamily, fontSize: 10 } },
        color: COLORS,
        series: [{ type: 'pie', radius: ['30%', '65%'], data, label: { fontFamily, fontSize: 10 }, itemStyle: { borderColor: '#383832', borderWidth: 1 } }],
      };
    }

    if (chartType === 'pareto') {
      // Find cumulative col (last numeric col with "cum" in header), count col (first numeric)
      const lowerH = headers.map(h => (h || '').toLowerCase());
      let cumIdx = lowerH.findIndex(h => /cumulative|cum[ _.%]|cum%|cum_pct|running/.test(h));
      if (cumIdx === -1) cumIdx = headers.length - 1;
      let countIdx = 1;
      for (let i = 1; i < headers.length; i++) {
        if (i !== cumIdx && rows.some(r => isNumericish(r[i]))) { countIdx = i; break; }
      }
      const counts = rows.map(r => parseNum(r[countIdx] || '0'));
      const cum = rows.map(r => parseNum(r[cumIdx] || '0'));
      return {
        tooltip: { trigger: 'axis', textStyle: { fontFamily, fontSize: 11 } },
        legend: { top: 0, textStyle: { fontFamily, fontSize: 10 }, data: [headers[countIdx], headers[cumIdx]] },
        grid: { left: 65, right: 60, bottom: labels.length > 6 ? 50 : 25, top: 30 },
        xAxis: { type: 'category', data: labels, axisLabel: { fontFamily, fontSize: 10, rotate: labels.length > 6 ? 35 : 0, overflow: 'truncate', width: 80 } },
        yAxis: [
          { type: 'value', name: headers[countIdx] || 'Count', nameTextStyle: { fontFamily, fontSize: 10 }, axisLabel: { fontFamily, fontSize: 10, formatter: (v: number) => formatValue(v) } },
          { type: 'value', name: '%', nameTextStyle: { fontFamily, fontSize: 10 }, axisLabel: { fontFamily, fontSize: 10, formatter: '{value}%' }, max: 100, min: 0 },
        ],
        color: COLORS,
        series: [
          { name: headers[countIdx] || 'Count', type: 'bar', yAxisIndex: 0, data: counts, barMaxWidth: 40, itemStyle: { color: COLORS[0], borderColor: '#383832', borderWidth: 1 } },
          { name: headers[cumIdx] || 'Cumulative %', type: 'line', yAxisIndex: 1, data: cum, smooth: true, symbol: 'circle', symbolSize: 6,
            lineStyle: { color: COLORS[2], width: 2 }, itemStyle: { color: COLORS[2] },
            markLine: { silent: true, symbol: 'none', data: [{ yAxis: 80, label: { formatter: '80%', fontSize: 10, color: '#888' }, lineStyle: { color: '#888', type: 'dashed' } }] } },
        ],
      };
    }

    if (chartType === 'scatter') {
      const data = rows.map(r => [parseNum(r[1] || '0'), parseNum(r[2] || '0')]);
      return {
        tooltip: { trigger: 'item', formatter: (p: any) => `${labels[p.dataIndex]}: (${p.value[0]}, ${p.value[1]})` },
        xAxis: { type: 'value', name: headers[1] || '', nameTextStyle: { fontFamily, fontSize: 10 }, axisLabel: { fontFamily, fontSize: 10 } },
        yAxis: { type: 'value', name: headers[2] || '', nameTextStyle: { fontFamily, fontSize: 10 }, axisLabel: { fontFamily, fontSize: 10 } },
        grid: { left: 60, right: 20, bottom: 40, top: 20 },
        color: COLORS,
        series: [{ type: 'scatter', data, symbolSize: 10, itemStyle: { borderColor: '#383832', borderWidth: 1 } }],
      };
    }

    // bar, line, area
    const echartsType = chartType === 'area' ? 'line' : chartType;
    const series = headers.slice(1).map((header, i) => {
      const values = rows.map(r => parseNum(r[i + 1] || '0'));
      const s: any = {
        name: header,
        type: echartsType,
        data: values,
        itemStyle: { color: COLORS[i % COLORS.length], borderColor: '#383832', borderWidth: 1 },
      };
      if (chartType === 'area') {
        s.areaStyle = { opacity: 0.3 };
      }
      if (chartType === 'bar') {
        s.barMaxWidth = 40;
      }
      return s;
    });

    const hasLegend = series.length > 1;
    const rotateLabels = labels.length > 6;

    return {
      tooltip: { trigger: 'axis', textStyle: { fontFamily, fontSize: 11 }, valueFormatter: (v: any) => typeof v === 'number' ? formatTooltipValue(v) : v },
      legend: hasLegend ? { top: 0, textStyle: { fontFamily, fontSize: 10 } } : undefined,
      grid: { left: 65, right: 20, bottom: rotateLabels ? 50 : 25, top: hasLegend ? 30 : 15 },
      xAxis: { type: 'category', data: labels, axisLabel: { fontFamily, fontSize: 10, rotate: rotateLabels ? 35 : 0, overflow: 'truncate', width: 80 } },
      yAxis: { type: 'value', axisLabel: { fontFamily, fontSize: 10, formatter: (v: number) => formatValue(v) } },
      color: COLORS,
      series,
    };
  }

  function renderChart() {
    if (!container) return;
    if (chart) chart.dispose();
    chart = echarts.init(container, undefined, { renderer: 'canvas' });
    chart.setOption(buildOption());
  }

  onMount(() => { renderChart(); });

  $effect(() => {
    // Re-render when chartType changes
    chartType;
    if (chart) renderChart();
  });

  onDestroy(() => { chart?.dispose(); });

  // Resize on window resize
  function handleResize() { chart?.resize(); }
</script>

<svelte:window onresize={handleResize} />

<div bind:this={container} style="height: 300px; width: 100%;"></div>
