<script>
  import DashRenderer from '$lib/dashboards/DashRenderer.svelte';

  const spec = {
    title: 'BCP Operations Demo',
    filters: [
      { key: 'region', label: 'Region', value: 'Yangon' },
      { key: 'date', label: 'Date', value: 'Last 7d' }
    ],
    insights: [
      { title: 'Fuel critical', finding: '3 sites below 20% reserve', severity: 'high' }
    ],
    cells: [
      { id: 'k1', type: 'kpi', grid: [1,1,3,1], title: 'Sites Online', config: { value: 58, delta: 2, down_good: false } },
      { id: 'k2', type: 'kpi', grid: [4,1,3,1], title: 'Avg Fuel %', config: { value: '64%', delta: -8, down_good: false } },
      { id: 'k3', type: 'kpi', grid: [7,1,3,1], title: 'Gen Runtime', config: { value: '14.2h', delta: 1.3, down_good: true } },
      { id: 'k4', type: 'kpi', grid: [10,1,3,1], title: 'Incidents', config: { value: 3, delta: -1, down_good: true } },
      { id: 'c1', type: 'chart', grid: [1,2,8,3], title: 'Fuel trend', config: { type: 'line' } },
      { id: 't1', type: 'table', grid: [9,2,4,3], title: 'Low sites', config: {} }
    ]
  };

  const data = {
    t1: [
      { site: 'YGN-12', fuel: '14%', status: 'critical' },
      { site: 'MDY-04', fuel: '17%', status: 'critical' },
      { site: 'NPT-08', fuel: '19%', status: 'warning' }
    ]
  };
</script>

<DashRenderer {spec} {data} />
