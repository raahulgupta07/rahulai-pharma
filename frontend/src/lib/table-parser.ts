/**
 * Parse markdown tables from text content.
 * Returns array of { headers, rows } for each table found.
 */

export interface ParsedTable {
  headers: string[];
  rows: string[][];
}

export function parseMarkdownTables(text: string): ParsedTable[] {
  const tables: ParsedTable[] = [];
  const lines = text.split('\n');
  let i = 0;

  while (i < lines.length) {
    const line = lines[i].trim();

    // Check if line looks like a table row (starts and ends with |)
    if (line.startsWith('|') && line.includes('|', 1)) {
      const tableLines: string[] = [];

      // Collect consecutive table lines
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i].trim());
        i++;
      }

      if (tableLines.length >= 2) {
        // Parse headers (first row)
        const headers = tableLines[0]
          .split('|')
          .filter(c => c.trim() !== '')
          .map(c => c.trim());

        // Skip separator row (row with dashes)
        const startIdx = tableLines.length > 1 && tableLines[1].replace(/[|\-:\s]/g, '') === '' ? 2 : 1;

        // Parse data rows
        const rows: string[][] = [];
        for (let j = startIdx; j < tableLines.length; j++) {
          const cells = tableLines[j]
            .split('|')
            .filter(c => c.trim() !== '')
            .map(c => c.trim());
          if (cells.length > 0) rows.push(cells);
        }

        if (headers.length > 0 && rows.length > 0) {
          tables.push({ headers, rows });
        }
      }
    } else {
      i++;
    }
  }

  return tables;
}

export function tableToCsv(table: ParsedTable): string {
  const lines = [table.headers.join(',')];
  for (const row of table.rows) {
    lines.push(row.map(c => c.includes(',') ? `"${c}"` : c).join(','));
  }
  return lines.join('\n');
}

export function hasNumericData(table: ParsedTable): boolean {
  if (table.headers.length < 2 || table.rows.length < 2) return false;
  // Check if at least one non-label column has numeric data
  return table.rows.some(row =>
    row.slice(1).some(cell => {
      const v = cell.replace(/[$,%]/g, '').trim();
      return !isNaN(parseFloat(v));
    })
  );
}
