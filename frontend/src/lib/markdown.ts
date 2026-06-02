/**
 * Lightweight markdown to HTML converter.
 * Handles: headings, bold, italic, inline code, code blocks, tables, lists, paragraphs.
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Format inline markdown (bold, italic, code) for short strings like headlines.
 * Escapes HTML first to prevent XSS.
 */
export function formatInline(text: string): string {
  if (!text) return '';
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
  s = s.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
  return s;
}

export function markdownToHtml(md: string): string {
  if (!md) return '';

  const lines = md.split('\n');
  const output: string[] = [];
  let inCodeBlock = false;
  let codeContent: string[] = [];
  let inTable = false;
  let tableRows: string[] = [];
  let inList = false;
  let listItems: string[] = [];
  let listType: 'ul' | 'ol' = 'ul';

  function flushTable() {
    if (!inTable || tableRows.length === 0) return;
    const rows = tableRows.map((row) => {
      return row.split('|').filter((c) => c.trim() !== '').map((c) => c.trim());
    });
    if (rows.length === 0) { inTable = false; tableRows = []; return; }

    let html = '<table>';
    // First row is header
    html += '<thead><tr>' + rows[0].map((c) => `<th>${inlineFormat(c)}</th>`).join('') + '</tr></thead>';
    // Skip separator row (row[1] if it's dashes)
    const startIdx = rows.length > 1 && rows[1].every((c) => /^[-:]+$/.test(c)) ? 2 : 1;
    if (rows.length > startIdx) {
      html += '<tbody>';
      for (let i = startIdx; i < rows.length; i++) {
        html += '<tr>' + rows[i].map((c) => `<td>${inlineFormat(c)}</td>`).join('') + '</tr>';
      }
      html += '</tbody>';
    }
    html += '</table>';
    output.push(html);
    inTable = false;
    tableRows = [];
  }

  function flushList() {
    if (!inList || listItems.length === 0) return;
    const tag = listType;
    output.push(`<${tag}>` + listItems.map((item) => `<li>${inlineFormat(item)}</li>`).join('') + `</${tag}>`);
    inList = false;
    listItems = [];
  }

  function inlineFormat(text: string): string {
    let s = escapeHtml(text);
    // Code (inline)
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Italic
    s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    // Links
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }

  let codeLang = '';

  for (const line of lines) {
    // Code blocks
    if (line.startsWith('```')) {
      if (inCodeBlock) {
        const code = escapeHtml(codeContent.join('\n'));
        const lineCount = codeContent.length;
        const lang = codeLang || 'code';
        const langUpper = lang.toUpperCase();
        const codeId = 'code_' + Math.random().toString(36).slice(2, 8);
        output.push(
          `<details class="code-collapse">` +
          `<summary class="code-collapse-header">` +
          `<span class="code-collapse-arrow">▶</span>` +
          `<span class="code-collapse-lang">${langUpper}</span>` +
          `<span class="code-collapse-info">${lineCount} lines</span>` +
          `<button class="code-collapse-copy" onclick="navigator.clipboard.writeText(document.getElementById('${codeId}').textContent);this.textContent='COPIED';setTimeout(()=>this.textContent='COPY',1500)">COPY</button>` +
          `</summary>` +
          `<pre class="code-collapse-pre"><code id="${codeId}">${code}</code></pre>` +
          `</details>`
        );
        codeContent = [];
        codeLang = '';
        inCodeBlock = false;
      } else {
        flushTable();
        flushList();
        codeLang = line.slice(3).trim();
        inCodeBlock = true;
      }
      continue;
    }
    if (inCodeBlock) {
      codeContent.push(line);
      continue;
    }

    // Table rows
    if (line.includes('|') && line.trim().startsWith('|')) {
      flushList();
      if (!inTable) inTable = true;
      tableRows.push(line);
      continue;
    } else if (inTable) {
      flushTable();
    }

    // Horizontal rule — standalone `---` / `***` / `___`
    if (/^\s*([-*_])\1\1+\s*$/.test(line)) {
      flushTable();
      flushList();
      output.push('<hr />');
      continue;
    }

    // List items
    const ulMatch = line.match(/^(\s*)[-*]\s+(.*)/);
    const olMatch = line.match(/^(\s*)\d+\.\s+(.*)/);
    // Numbered SECTION HEADING (e.g. "1. Site Overview", "2. Negative Stock (Data Integrity Issue)")
    // — short, title-case, no trailing prose. Treat as <h3>, NOT list item.
    if (olMatch && olMatch[1] === '' && /^[A-Z]/.test(olMatch[2]) && olMatch[2].length < 90 && !olMatch[2].endsWith('.') && !olMatch[2].endsWith(':')) {
      flushTable();
      flushList();
      output.push(`<h3 class="num-section">${inlineFormat(olMatch[0].trim())}</h3>`);
      continue;
    }
    // "Step N — Title" → <h4>
    const stepMatch = line.match(/^(Step\s+\d+\s*[—\-:]\s*.+)$/i);
    if (stepMatch && stepMatch[1].length < 100) {
      flushTable();
      flushList();
      output.push(`<h4 class="step-section">${inlineFormat(stepMatch[1])}</h4>`);
      continue;
    }
    if (ulMatch) {
      flushTable();
      if (!inList) { inList = true; listType = 'ul'; }
      listItems.push(ulMatch[2]);
      continue;
    }
    if (olMatch) {
      flushTable();
      if (!inList) { inList = true; listType = 'ol'; }
      listItems.push(olMatch[2]);
      continue;
    }
    if (inList) {
      flushList();
    }

    // Headings (# .. ######)
    const headingMatch = line.match(/^(#{1,6})\s+(.*)/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      output.push(`<h${level}>${inlineFormat(headingMatch[2])}</h${level}>`);
      continue;
    }

    // Empty line
    if (line.trim() === '') {
      continue;
    }

    // Paragraph
    output.push(`<p>${inlineFormat(line)}</p>`);
  }

  // Flush remaining
  if (inCodeBlock) {
    const code = escapeHtml(codeContent.join('\n'));
    const lineCount = codeContent.length;
    const lang = codeLang || 'code';
    const codeId = 'code_' + Math.random().toString(36).slice(2, 8);
    output.push(
      `<details class="code-collapse">` +
      `<summary class="code-collapse-header">` +
      `<span class="code-collapse-arrow">▶</span>` +
      `<span class="code-collapse-lang">${lang.toUpperCase()}</span>` +
      `<span class="code-collapse-info">${lineCount} lines</span>` +
      `<button class="code-collapse-copy" onclick="navigator.clipboard.writeText(document.getElementById('${codeId}').textContent);this.textContent='COPIED';setTimeout(()=>this.textContent='COPY',1500)">COPY</button>` +
      `</summary>` +
      `<pre class="code-collapse-pre"><code id="${codeId}">${code}</code></pre>` +
      `</details>`
    );
  }
  flushTable();
  flushList();

  return output.join('');
}
