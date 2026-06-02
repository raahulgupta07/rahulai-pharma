#!/usr/bin/env node
/**
 * Style Audit — fail CI when code violates the design system.
 *
 * Flags:
 *  - hardcoded hex colors in .svelte files (must use --pw-* / --ds-* tokens)
 *  - inline style="…" with border|background|color|border-radius|padding (use class)
 *
 * Allowlist exceptions:
 *  - app.css itself (tokens defined here)
 *  - chart config files (ECharts options often need raw colors)
 *  - test fixtures
 */
import { readdirSync, readFileSync, statSync } from 'fs';
import { join, extname, relative } from 'path';

const ROOT = new URL('../src/', import.meta.url).pathname;
const ALLOW_FILES = new Set(['app.css']);
const ALLOW_PATTERNS = [/echart/i, /chart-detect/i, /chart_themes/i];

const HEX_RE = /#[0-9a-fA-F]{3,8}\b/g;
const INLINE_STYLE_RE = /style\s*=\s*["'`]([^"'`]+)["'`]/g;
const BANNED_INLINE = /\b(border|background|background-color|color|border-radius|padding|margin)\s*:/i;

let violations = [];

function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (entry === 'node_modules' || entry.startsWith('.')) continue;
      walk(full);
      continue;
    }
    const ext = extname(entry);
    if (!['.svelte', '.ts', '.js'].includes(ext)) continue;
    if (ALLOW_FILES.has(entry)) continue;
    if (ALLOW_PATTERNS.some(re => re.test(full))) continue;
    audit(full);
  }
}

function audit(file) {
  const src = readFileSync(file, 'utf8');
  const lines = src.split('\n');
  const rel = relative(ROOT, file);

  lines.forEach((line, i) => {
    // skip CSS variable definitions and comments
    if (/^\s*\/\//.test(line)) return;
    if (/^\s*\*/.test(line)) return;

    // Hex check — only in .svelte <style> blocks or CSS-like contexts
    if (file.endsWith('.svelte') || file.endsWith('.css')) {
      const hexMatches = line.match(HEX_RE);
      if (hexMatches) {
        // ignore if inside a comment
        const before = line.slice(0, line.indexOf(hexMatches[0]));
        if (before.includes('//') || before.includes('/*')) return;
        // ignore very common chart palette literal arrays (line has multiple hexes on chart series)
        if (hexMatches.length > 3) return;
        violations.push({
          file: rel,
          line: i + 1,
          kind: 'hardcoded-hex',
          snippet: line.trim().slice(0, 120),
          hint: 'Use var(--pw-*) or var(--ds-*) tokens'
        });
      }
    }

    // Inline style check
    INLINE_STYLE_RE.lastIndex = 0;
    let m;
    while ((m = INLINE_STYLE_RE.exec(line)) !== null) {
      const css = m[1];
      if (BANNED_INLINE.test(css)) {
        violations.push({
          file: rel,
          line: i + 1,
          kind: 'inline-style',
          snippet: line.trim().slice(0, 120),
          hint: 'Move to class; use ds-card / ds-input / btn-primary / etc.'
        });
      }
    }
  });
}

walk(ROOT);

if (violations.length === 0) {
  console.log('✓ style-audit: 0 violations');
  process.exit(0);
}

// Group by file
const byFile = {};
for (const v of violations) {
  (byFile[v.file] = byFile[v.file] || []).push(v);
}

console.log(`\n✗ style-audit: ${violations.length} violations across ${Object.keys(byFile).length} files\n`);

for (const [file, vs] of Object.entries(byFile).slice(0, 50)) {
  console.log(`  ${file}`);
  for (const v of vs.slice(0, 5)) {
    console.log(`    L${v.line} [${v.kind}] ${v.snippet}`);
    console.log(`           → ${v.hint}`);
  }
  if (vs.length > 5) console.log(`    … +${vs.length - 5} more`);
}

const failOn = parseInt(process.env.STYLE_AUDIT_THRESHOLD || '0', 10);
if (violations.length > failOn) {
  console.log(`\nThreshold: ${failOn}. Found: ${violations.length}. Failing.`);
  process.exit(1);
}
process.exit(0);
