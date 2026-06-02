#!/usr/bin/env node
/**
 * Chat-mirror linter — enforces the "single chat renderer" rule.
 *
 * Canonical renderer:  frontend/src/lib/chat/ChatMessageList.svelte
 * Canonical helpers:   frontend/src/lib/chat/markdown.ts
 * Docs:                frontend/CHAT_RENDERER.md, STYLEGUIDE.md §Chat tag rendering mirror rule
 *
 * Flags:
 *   1. `bubble-user` CSS class used outside the shared component (or allowlisted shells).
 *   2. Page-level RENDERING of chat tags ([KPI: [CONFIDENCE: [VERDICT: [IMPACT:) outside lib/chat/.
 *      Pre-render stripping via .replace(/\[KPI:...\]/g, '') is allowed.
 *   3. Duplicate definitions of formatCell() / generateChartCaption() outside lib/chat/.
 *
 * Exit code: 0 = clean, 1 = violations found.
 */
import { readdirSync, readFileSync, statSync } from 'fs';
import { join, extname, relative } from 'path';

const ROOT = new URL('../src/', import.meta.url).pathname;
const SHARED_DIR = 'lib/chat/';

// Files allowed to use `bubble-user` directly (besides the shared component).
const BUBBLE_ALLOWLIST = new Set([
  'lib/chat/ChatMessageList.svelte',  // canonical
  'routes/+page.svelte',              // public landing preview, not a chat surface
  'routes/+layout.svelte',            // observer/scroll logic only, no rendering
  // Project page keeps a :global() CSS mirror per STYLEGUIDE.md mirror rule.
  // It is allowed but flagged as "informational" so reviewers see it.
  'routes/project/[slug]/+page.svelte',
]);

const TAG_PARSE_PATTERNS = [
  { tag: '[KPI:', re: /msg\.content[\s\S]{0,40}\[KPI:/ },
  { tag: '[CONFIDENCE:', re: /\.match\([^)]*\[CONFIDENCE:/ },
  { tag: '[VERDICT:', re: /\.match\([^)]*\[VERDICT:/ },
  { tag: '[IMPACT:', re: /\.match\([^)]*\[IMPACT:/ },
];

// Stripping (allowed) vs rendering (banned). Strip = .replace inside transform chains.
const STRIP_RE = /\.replace\(\s*\/\\\[(KPI|CONFIDENCE|VERDICT|IMPACT|RELATED|CHART|ROUTING|REF|DASHBOARD|CLARIFY|CAMPAIGN_PROPOSAL|UP|DOWN|FLAT):/;

const HELPER_DEFS = [
  { name: 'formatCell', re: /\bfunction\s+formatCell\s*\(/ },
  { name: 'generateChartCaption', re: /\bfunction\s+generateChartCaption\s*\(/ },
];

const violations = [];
const informational = [];

function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (entry === 'node_modules' || entry.startsWith('.')) continue;
      walk(full);
      continue;
    }
    if (!['.svelte', '.ts', '.js'].includes(extname(entry))) continue;
    audit(full);
  }
}

function audit(file) {
  const rel = relative(ROOT, file);
  const inShared = rel.startsWith(SHARED_DIR);
  const src = readFileSync(file, 'utf8');
  const lines = src.split('\n');

  // --- Rule 1: bubble-user class usage ---
  lines.forEach((line, i) => {
    if (!/\bbubble-user\b/.test(line)) return;
    if (inShared) return;
    if (BUBBLE_ALLOWLIST.has(rel)) {
      informational.push({
        file: rel, line: i + 1, kind: 'bubble-user-allowlisted',
        snippet: line.trim().slice(0, 120),
        hint: 'Allowed via STYLEGUIDE.md mirror rule — review if still needed.',
      });
      return;
    }
    violations.push({
      file: rel, line: i + 1, kind: 'bubble-user-outside-shared',
      snippet: line.trim().slice(0, 120),
      hint: 'Move to <ChatMessageList> (lib/chat/ChatMessageList.svelte). See CHAT_RENDERER.md §2.2.',
    });
  });

  // --- Rule 2: page-level tag rendering ---
  if (!inShared) {
    lines.forEach((line, i) => {
      // Stripping is fine.
      if (STRIP_RE.test(line)) return;
      for (const { tag, re } of TAG_PARSE_PATTERNS) {
        if (re.test(line)) {
          violations.push({
            file: rel, line: i + 1, kind: `page-level-${tag.slice(1, -1).toLowerCase()}-render`,
            snippet: line.trim().slice(0, 120),
            hint: `Tag ${tag} must render via ChatMessageList. Strip-only via .replace() is allowed. See CHAT_RENDERER.md §2.1.`,
          });
        }
      }
    });
  }

  // --- Rule 3: duplicate helper definitions ---
  if (!inShared) {
    for (const { name, re } of HELPER_DEFS) {
      lines.forEach((line, i) => {
        if (re.test(line)) {
          violations.push({
            file: rel, line: i + 1, kind: `duplicate-${name}`,
            snippet: line.trim().slice(0, 120),
            hint: `${name}() is duplicated. Hoist to lib/chat/markdown.ts and import. See CHAT_RENDERER.md §6.`,
          });
        }
      });
    }
  }
}

walk(ROOT);

const total = violations.length;
const info = informational.length;

if (total === 0 && info === 0) {
  console.log('✓ chat-mirror: 0 violations, 0 informational');
  process.exit(0);
}

if (info > 0) {
  console.log(`\nℹ chat-mirror: ${info} informational (allowlisted mirror sites)`);
  for (const v of informational.slice(0, 20)) {
    console.log(`  ${v.file}:${v.line}  ${v.kind}`);
    console.log(`    → ${v.hint}`);
  }
}

if (total === 0) {
  console.log('\n✓ chat-mirror: 0 violations');
  process.exit(0);
}

console.log(`\n✗ chat-mirror: ${total} violations`);
const byFile = {};
for (const v of violations) (byFile[v.file] = byFile[v.file] || []).push(v);
for (const [file, vs] of Object.entries(byFile)) {
  console.log(`\n  ${file}`);
  for (const v of vs.slice(0, 8)) {
    console.log(`    L${v.line} [${v.kind}] ${v.snippet}`);
    console.log(`           → ${v.hint}`);
  }
  if (vs.length > 8) console.log(`    … +${vs.length - 8} more`);
}

// Threshold = 0 after consolidation: formatCell + generateChartCaption are now
// module exports from lib/chat/ChatMessageList.svelte; parseClarify + parseRelated
// hoisted to lib/chat/tag-parsers.ts. Pages import (never redefine).
// See frontend/CHAT_RENDERER.md §6.
const failOn = parseInt(process.env.CHAT_MIRROR_THRESHOLD || '0', 10);
if (total > failOn) {
  console.log(`\nThreshold: ${failOn}. Found: ${total}. Failing.`);
  process.exit(1);
}
process.exit(0);
