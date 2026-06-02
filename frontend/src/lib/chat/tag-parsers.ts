/**
 * Shared tag parsers for chat message content.
 *
 * These hoist page-level [CLARIFY:...] and [RELATED:...] tag handling out of
 * the dash + project chat pages so both surfaces parse identically. Render-only
 * tags (KPI/CONFIDENCE/etc.) still live in ChatMessageList.svelte per the
 * chat-mirror linter rule. See frontend/CHAT_RENDERER.md.
 */

/**
 * Extract `[CLARIFY: option1 | option2 | ...]` options from a chat string.
 * Returns the stripped content (tag removed) plus an options array.
 * `options` is `null` when no CLARIFY tag is present.
 */
export function parseClarify(content: string): { stripped: string; options: string[] | null } {
 if (!content) return { stripped: content || '', options: null };
 const re = /\[CLARIFY:\s*(.+?)\]/;
 const match = content.match(re);
 if (!match) return { stripped: content, options: null };
 const options = match[1].split('|').map((s) => s.trim()).filter(Boolean);
 const stripped = content.replace(re, '').trim();
 return { stripped, options: options.length ? options : null };
}

/**
 * Split a chat string on `[RELATED:...]` tags. Returns the trailing text
 * segment (the actual user-facing question after any RELATED suggestions)
 * plus the list of suggestion strings extracted from each tag.
 */
export function parseRelated(content: string): { trailing: string; suggestions: string[] } {
 if (!content) return { trailing: content || '', suggestions: [] };
 const re = /\[RELATED:([^\]]*)\]/g;
 const suggestions: string[] = [];
 let m: RegExpExecArray | null;
 while ((m = re.exec(content)) !== null) {
 const s = (m[1] || '').trim();
 if (s) suggestions.push(s);
 }
 const parts = content.split(/\[RELATED:[^\]]*\]/g);
 const trailing = (parts[parts.length - 1] || '').trim();
 return { trailing, suggestions };
}

// ---------------------------------------------------------------------------
// Storytelling insight tags (Analyst agent)
// ---------------------------------------------------------------------------

export function parseHeadline(content: string): { stripped: string; headline: string | null } {
 if (!content) return { stripped: content || '', headline: null };
 const m = content.match(/\[HEADLINE:\s*(.+?)\]/s);
 if (!m) return { stripped: content, headline: null };
 return { stripped: content.replace(m[0], '').trim(), headline: m[1].trim() };
}

export function parseBecause(content: string): { stripped: string; causes: string[] } {
 if (!content) return { stripped: content || '', causes: [] };
 const m = content.match(/\[BECAUSE:\s*(.+?)\]/s);
 if (!m) return { stripped: content, causes: [] };
 const causes = m[1].split('|').map((s) => s.trim()).filter(Boolean);
 return { stripped: content.replace(m[0], '').trim(), causes };
}

export function parseAnomalies(content: string): {
 stripped: string;
 anomalies: Array<{ column: string; value: string; reason: string }>;
} {
 if (!content) return { stripped: content || '', anomalies: [] };
 const re = /\[ANOMALY:\s*([^|]+?)\|\s*([^|]+?)\|\s*([^\]]+?)\]/g;
 const out: Array<{ column: string; value: string; reason: string }> = [];
 let m: RegExpExecArray | null;
 while ((m = re.exec(content)) !== null) {
 out.push({ column: m[1].trim(), value: m[2].trim(), reason: m[3].trim() });
 }
 const stripped = content.replace(re, '').trim();
 return { stripped, anomalies: out };
}

export function parseActions(content: string): {
 stripped: string;
 actions: Array<{ label: string; type: string; param: string }>;
} {
 if (!content) return { stripped: content || '', actions: [] };
 const re = /\[ACTION:\s*([^|]+?)\|\s*([^|]+?)\|\s*([^\]]+?)\]/g;
 const out: Array<{ label: string; type: string; param: string }> = [];
 let m: RegExpExecArray | null;
 while ((m = re.exec(content)) !== null) {
 out.push({ label: m[1].trim(), type: m[2].trim(), param: m[3].trim() });
 }
 const stripped = content.replace(re, '').trim();
 return { stripped, actions: out };
}

export function parseCaveat(content: string): { stripped: string; caveat: string | null } {
 if (!content) return { stripped: content || '', caveat: null };
 const m = content.match(/\[CAVEAT:\s*(.+?)\]/s);
 if (!m) return { stripped: content, caveat: null };
 return { stripped: content.replace(m[0], '').trim(), caveat: m[1].trim() };
}

export function parseConfidenceBreakdown(content: string): {
 stripped: string;
 breakdown: { dq: number; qm: number; rp: number } | null;
} {
 if (!content) return { stripped: content || '', breakdown: null };
 const m = content.match(/\[CONFIDENCE_BREAKDOWN:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\]/);
 if (!m) return { stripped: content, breakdown: null };
 return {
 stripped: content.replace(m[0], '').trim(),
 breakdown: { dq: parseInt(m[1], 10), qm: parseInt(m[2], 10), rp: parseInt(m[3], 10) },
 };
}

/**
 * Lenient hallucination-safety check. Logs to console when a headline mentions
 * a number that doesn't appear in the body; returns the headline unchanged so
 * we never silently drop content. Tighten later if false-positive rate is low.
 */
export function safeHeadline(headline: string, body: string): string | null {
 if (!headline) return null;
 try {
 const numbersInHeadline = headline.match(/\$?[\d,]+\.?\d*\%?/g) || [];
 for (const num of numbersInHeadline) {
 if (num.length < 3) continue;
 const stripped = num.replace(/[\$,]/g, '').slice(0, 4);
 if (stripped && !body.includes(stripped)) {
 // eslint-disable-next-line no-console
 console.warn('[safeHeadline] number not in body:', num, '— keeping headline anyway');
 }
 }
 } catch {
 // never throw from a safety helper
 }
 return headline;
}

// ---------------------------------------------------------------------------
// McKinsey/BCG pyramid tags (FAST + DEEP)
// ---------------------------------------------------------------------------

export function parseSoWhat(content: string): {
 stripped: string;
 soWhat: { action: string; owner: string; effort: string; risk: string } | null;
} {
 if (!content) return { stripped: content || '', soWhat: null };
 const m = content.match(/\[SO_WHAT:\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^\]]*?)\]/);
 if (!m) return { stripped: content, soWhat: null };
 return {
 stripped: content.replace(m[0], '').trim(),
 soWhat: { action: m[1].trim(), owner: m[2].trim(), effort: m[3].trim(), risk: m[4].trim() },
 };
}

export function parseFindings(content: string): {
 stripped: string;
 findings: Array<{ rank: string; text: string; impact: string; severity: string }>;
} {
 if (!content) return { stripped: content || '', findings: [] };
 const re = /\[FINDING:\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^\]]*?)\]/g;
 const out: Array<{ rank: string; text: string; impact: string; severity: string }> = [];
 let m: RegExpExecArray | null;
 while ((m = re.exec(content)) !== null) {
 out.push({ rank: m[1].trim(), text: m[2].trim(), impact: m[3].trim(), severity: m[4].trim().toUpperCase() });
 }
 return { stripped: content.replace(re, '').trim(), findings: out };
}

export function parseKillCriteria(content: string): { stripped: string; criteria: string[] } {
 if (!content) return { stripped: content || '', criteria: [] };
 const m = content.match(/\[KILL:\s*(.+?)\]/s);
 if (!m) return { stripped: content, criteria: [] };
 const criteria = m[1].split('|').map((s) => s.trim()).filter(Boolean);
 return { stripped: content.replace(m[0], '').trim(), criteria };
}

export function parseSegments(content: string): {
 stripped: string;
 segments: Array<{ label: string; value: string; pct: number }>;
} {
 if (!content) return { stripped: content || '', segments: [] };
 const re = /\[SEGMENT:\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([0-9.]+)\s*\]/g;
 const out: Array<{ label: string; value: string; pct: number }> = [];
 let m: RegExpExecArray | null;
 while ((m = re.exec(content)) !== null) {
 out.push({ label: m[1].trim(), value: m[2].trim(), pct: parseFloat(m[3]) || 0 });
 }
 return { stripped: content.replace(re, '').trim(), segments: out };
}

export function parseAnchors(content: string): { stripped: string; anchors: string[] } {
 if (!content) return { stripped: content || '', anchors: [] };
 const re = /\[ANCHOR:\s*([^\]]+?)\]/g;
 const out: string[] = [];
 let m: RegExpExecArray | null;
 while ((m = re.exec(content)) !== null) out.push(m[1].trim());
 return { stripped: content.replace(re, '').trim(), anchors: out };
}

export function parseAssumptions(content: string): { stripped: string; assumptions: string[] } {
 if (!content) return { stripped: content || '', assumptions: [] };
 const re = /\[ASSUMPTION:\s*([^\]]+?)\]/g;
 const out: string[] = [];
 let m: RegExpExecArray | null;
 while ((m = re.exec(content)) !== null) out.push(m[1].trim());
 return { stripped: content.replace(re, '').trim(), assumptions: out };
}

export function actionIcon(type: string): string {
 switch ((type || '').toLowerCase()) {
 case 'investigate': return '';
 case 'run_analysis': return '';
 case 'create_campaign': return '';
 case 'train_model': return '';
 case 'drill_down': return '⤵';
 default: return '▶';
 }
}
