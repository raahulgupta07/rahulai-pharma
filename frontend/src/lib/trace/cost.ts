// Small zero-dependency cost helper for the live trace timeline.
// Rough per-1M-token prices; falls back to 0 when the model is unknown.

export interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  model?: string;
}

const PRICE: Record<string, { in: number; out: number }> = {
  'gemini-3-flash': { in: 0.3, out: 2.5 },
  'gemini-3.1-flash-lite': { in: 0.1, out: 0.4 },
  'gemini-embedding': { in: 0.02, out: 0 },
  'gpt-5.4-mini': { in: 0.4, out: 1.6 },
};

export function priceFor(model: string | undefined): { in: number; out: number } {
  if (!model || typeof model !== 'string') return { in: 0, out: 0 };
  const m = model.toLowerCase();
  for (const key of Object.keys(PRICE)) {
    if (m.includes(key)) return PRICE[key];
  }
  return { in: 0, out: 0 };
}

export function costOf(tin: number, tout: number, model: string | undefined): number {
  const p = priceFor(model);
  return (tin / 1e6) * p.in + (tout / 1e6) * p.out;
}

/** Compact model tag, e.g. "google/gemini-3-flash-preview" -> "gemini-3-flash". */
export function shortModel(model: string | undefined): string {
  if (!model || typeof model !== 'string') return '';
  return model.split('/').pop()!.replace(/-preview$/, '').replace(/-latest$/, '');
}

/** Render a duration (ms or "1.2s" string) as a compact "1.2s" / "850ms". */
export function fmtDuration(d: string | number | undefined): string {
  if (d == null) return '';
  if (typeof d === 'string') return d.trim();
  if (d < 1000) return `${Math.round(d)}ms`;
  return `${(d / 1000).toFixed(1)}s`;
}

export function fmtTokens(n: number): string {
  if (!n || n < 0) return '0';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function fmtCost(c: number): string {
  if (!c || c <= 0) return '$0';
  if (c < 0.01) return `$${c.toFixed(4)}`;
  return `$${c.toFixed(3)}`;
}
