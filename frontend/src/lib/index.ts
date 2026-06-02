export { sendMessage, generateSessionId, getActiveScope, setScope, clearScope, authHeaders, dashFetch } from './api';
export type { ToolCall, ActiveScope } from './api';
export { markdownToHtml, formatInline } from './markdown';
export { parseMarkdownTables, tableToCsv, hasNumericData } from './table-parser';
export type { ParsedTable } from './table-parser';
export { detectChartType, getAvailableTypes, parseChartHint } from './chart-detect';
export type { ChartType } from './chart-detect';
export { parseActionTitle, parseNarration, parseKpis, parseAttention, parseSegmentBreakdown, parseRecommendations, parseBenchmarks, parseScenarios, parseForecasts, parseRootCause, parseAudit, parseMeans, parseFreshness, parseLineage, parseSkillUsed } from './answer-tags';
