import type { ParsedTable } from './table-parser';

export type ChartType = 'bar' | 'line' | 'pie' | 'scatter' | 'area' | 'heatmap' | 'pareto';

function hasParetoCols(headers: string[]): boolean {
  const lower = headers.map(h => (h || '').toLowerCase());
  return lower.some(h => /cumulative|cum[ _.%]|cum%|cum_pct|running\s*(total|%)/.test(h));
}

const DATE_RE = /^\d{4}[-/]\d{1,2}([-/]\d{1,2})?$|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i;
const MONTH_RE = /^(Q[1-4]|H[12]|\d{4}[-/]?\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i;

function isDateLike(values: string[]): boolean {
  const sample = values.slice(0, 5).filter(v => v.trim());
  if (sample.length === 0) return false;
  return sample.filter(v => DATE_RE.test(v.trim()) || MONTH_RE.test(v.trim())).length >= sample.length * 0.6;
}

function isNumeric(val: string): boolean {
  const v = val.replace(/[$,%\s]/g, '').trim();
  return v !== '' && !isNaN(Number(v));
}

function countNumericColumns(table: ParsedTable): number {
  if (table.rows.length === 0) return 0;
  let count = 0;
  for (let col = 1; col < table.headers.length; col++) {
    const numericRows = table.rows.filter(r => r[col] && isNumeric(r[col])).length;
    if (numericRows >= table.rows.length * 0.5) count++;
  }
  return count;
}

export function detectChartType(table: ParsedTable): ChartType {
  if (!table.headers.length || !table.rows.length) return 'bar';

  const firstColValues = table.rows.map(r => r[0] || '');
  const dateCol = isDateLike(firstColValues);
  const numCols = countNumericColumns(table);
  const rowCount = table.rows.length;

  // Pareto: category + count + cumulative% column
  if (hasParetoCols(table.headers) && numCols >= 2 && !dateCol) {
    return 'pareto';
  }

  // Date-like first column
  if (dateCol) {
    return numCols >= 2 ? 'line' : 'area';
  }

  // Few rows + 1 numeric = pie
  if (rowCount <= 8 && numCols === 1) {
    return 'pie';
  }

  // 2 numeric columns, no clear category = scatter
  if (numCols >= 2 && rowCount > 8) {
    return 'scatter';
  }

  return 'bar';
}

export function parseChartHint(content: string): { type: ChartType; title: string } | null {
  const match = content.match(/\[CHARTS?:(\w+)\|title:([^\]]+)\]/);
  if (!match) return null;
  const type = match[1].toLowerCase() as ChartType;
  const validTypes: ChartType[] = ['bar', 'line', 'pie', 'scatter', 'area', 'pareto'];
  if (!validTypes.includes(type)) return null;
  return { type, title: match[2].trim() };
}

export function getAvailableTypes(table: ParsedTable): ChartType[] {
  const types: ChartType[] = ['bar', 'line'];
  const numCols = countNumericColumns(table);

  if (numCols >= 1 && table.rows.length <= 15) types.push('pie');
  if (numCols >= 2) types.push('scatter');
  types.push('area');
  if (hasParetoCols(table.headers)) types.push('pareto');

  return types;
}
