import { test, expect, Browser, BrowserContext, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import * as ExcelJS from 'exceljs';
import { execSync } from 'child_process';

// eslint-disable-next-line @typescript-eslint/no-var-requires
const pdf = require('pdf-parse');

/* ─────────────────────────────────────────────────────────────────────
   PDF Validation: Stage vs Prod — Line-by-Line Comparison
   ─────────────────────────────────────────────────────────────────────
   Compares PDF files from Prod and Stage folders (or downloads them).
   Validates:
     • Line-by-line text content differences
     • Missing / extra lines
     • Table structure & formatting
     • Whitespace, padding, line breaks
     • Page count differences
     • Character-level mismatches
   Report: reports/pdf-validation.xlsx (overwritten each run)
───────────────────────────────────────────────────────────────────── */

const PDF_DIR = path.resolve(__dirname, '../../PDF');
const PROD_DIR = path.join(PDF_DIR, 'prod');
const STAGE_DIR = path.join(PDF_DIR, 'stage');
const REPORTS_DIR = path.resolve(__dirname, '../../reports');
const REPORT_PATH = path.join(REPORTS_DIR, 'pdf-validation.xlsx');

// Config
const CONFIG_PATH = path.resolve(__dirname, '../../config/test-urls.json');
const AUTH_STATE = path.resolve(__dirname, '../../auth-sessions/storage-state.json');

interface PdfContent {
  fileName: string;
  pageCount: number;
  lines: string[];
  pages: PageContent[];
  tables: TableInfo[];
  metadata: Record<string, string>;
}

interface PageContent {
  pageNum: number;
  text: string;
  lines: string[];
  lineCount: number;
}

interface TableInfo {
  pageNum: number;
  rowCount: number;
  rows: string[][];
  raw: string;
}

interface Issue {
  category: string;
  severity: 'Critical' | 'Major' | 'Minor' | 'Info';
  page: string;
  lineNum: number;
  prodContent: string;
  stageContent: string;
  description: string;
}

/* ─── Helpers ─────────────────────────────────────────────────── */

/** Appends ?wcmmode=disabled to a URL if not already present */
function appendWcmDisabled(urlStr: string): string {
  if (!urlStr || urlStr.includes('wcmmode=disabled')) return urlStr;
  try {
    if (urlStr.startsWith('/') || !urlStr.includes('://')) {
        const [path, hash] = urlStr.split('#');
        const separator = path.includes('?') ? '&' : '?';
        return `${path}${separator}wcmmode=disabled${hash ? '#' + hash : ''}`;
    }
    const url = new URL(urlStr);
    url.searchParams.set('wcmmode', 'disabled');
    return url.toString();
  } catch (e) {
    const [path, hash] = urlStr.split('#');
    const separator = path.includes('?') ? '&' : '?';
    return `${path}${separator}wcmmode=disabled${hash ? '#' + hash : ''}`;
  }
}

function isAuthWall(page: Page): boolean {
  const url = page.url();
  return url.includes('adobelogin') || url.includes('ims-na1') || url.includes('auth.services.adobe.com');
}

async function autoLogin(browser: Browser): Promise<BrowserContext> {
  console.log('⚠️  Auth wall detected — running auto-login…');
  const loginScript = path.resolve(__dirname, '../../run_login.ts');
  try {
    execSync(`npx ts-node "${loginScript}"`, {
      cwd: path.resolve(__dirname, '../..'),
      stdio: 'inherit',
      timeout: 120_000,
    });
  } catch (e) {
    console.error('Auto-login failed:', e);
  }
  return browser.newContext({ storageState: AUTH_STATE });
}

async function getAuthContext(browser: Browser): Promise<BrowserContext> {
  if (fs.existsSync(AUTH_STATE)) {
    return browser.newContext({ storageState: AUTH_STATE });
  }
  const loginScript = path.resolve(__dirname, '../../run_login.ts');
  execSync(`npx ts-node "${loginScript}"`, {
    cwd: path.resolve(__dirname, '../..'),
    stdio: 'inherit',
    timeout: 120_000,
  });
  return browser.newContext({ storageState: AUTH_STATE });
}

/** Parse a PDF buffer into structured content */
async function parsePdf(buffer: Buffer, fileName: string): Promise<PdfContent> {
  if (!buffer || buffer.length === 0) {
    throw new Error(`❌ PDF Buffer is empty for file: ${fileName}`);
  }

  try {
    const parser = new pdf.PDFParse({ data: buffer });
    const result = await parser.getText();
    const info = await parser.getInfo().catch(() => ({ info: {} }));
    
    const fullText = (result.text || '').trim();
    
    if (!fullText) {
      console.error(`   [CRITICAL] No text extracted from ${fileName}. Possibly an image-only PDF.`);
    } else {
      console.log(`   [DEBUG] Extracted ${fullText.length} chars from ${fileName}. Page Count: ${result.total || result.numpages || '?'}`);
    }

    const allLines = fullText.split('\n').map((l: string) => l.trim()).filter((l: string) => l !== '');
    
    // Use the native pages array from PDFParse!
    const pages: PageContent[] = (result.pages || []).map((p: any, idx: number) => {
      const pText = p.text || '';
      const pLines = pText.split('\n').map((l: string) => l.trim()).filter((l: string) => l !== '');
      return {
        pageNum: p.num || idx + 1,
        text: pText,
        lines: pLines,
        lineCount: pLines.length
      };
    });

    console.log(`   [DEBUG] Successfully parsed ${fileName}: ${pages.length} pages mapped, ${allLines.length} lines total`);

  // Detect tables (heuristic: lines with multiple consecutive spaces or tab-separated values)
  const tables: TableInfo[] = [];
  let currentTable: string[][] = [];
  let tableStartPage = 1;

  for (let i = 0; i < allLines.length; i++) {
    const line = allLines[i];
    const isTableRow = /\S\s{3,}\S/.test(line) || line.includes('\t');
    if (isTableRow && line.trim()) {
      if (currentTable.length === 0) {
        // Find which page this line belongs to
        let charAccum = 0;
        for (let p = 0; p < pages.length; p++) {
          charAccum += pages[p].text.length;
          if (i * 40 < charAccum) { 
            tableStartPage = p + 1;
            break;
          }
        }
      }
      const cells = line.split(/\s{3,}|\t/).map((c: string) => c.trim()).filter((c: string) => c);
      currentTable.push(cells);
    } else {
      if (currentTable.length >= 2) {
        tables.push({
          pageNum: tableStartPage,
          rowCount: currentTable.length,
          rows: currentTable,
          raw: currentTable.map((r: string[]) => r.join(' | ')).join('\n'),
        });
      }
      currentTable = [];
    }
  }
  if (currentTable.length >= 2) {
    tables.push({
      pageNum: tableStartPage,
      rowCount: currentTable.length,
      rows: currentTable,
      raw: currentTable.map((r: string[]) => r.join(' | ')).join('\n'),
    });
  }

    return {
      fileName,
      pageCount: result.numpages || pages.length,
      lines: allLines,
      pages,
      tables,
      metadata: info.info || {},
    };
  } catch (err) {
    console.error(`   [FATAL ERROR] Failed to parse PDF ${fileName}:`, err);
    // Return empty content instead of crashing the whole test suite
    return {
      fileName,
      pageCount: 0,
      lines: [],
      pages: [],
      tables: [],
      metadata: {},
    };
  }
}

/** Compare two PDFs line by line and collect all issues */
function comparePdfs(prod: PdfContent, stage: PdfContent): Issue[] {
  const issues: Issue[] = [];

  // 1. Page count difference
  if (prod.pageCount !== stage.pageCount) {
    issues.push({
      category: 'Page Count Mismatch',
      severity: 'Critical',
      page: 'N/A',
      lineNum: 0,
      prodContent: `${prod.pageCount} pages`,
      stageContent: `${stage.pageCount} pages`,
      description: `❌ MAJOR DISCREPANCY: Prod has ${prod.pageCount} pages while Stage has ${stage.pageCount} pages. Content is likely missing or extra.`,
    });
  }

  // 2. Diff-aware Line Comparison
  let pIdx = 0;
  let sIdx = 0;

  while (pIdx < prod.lines.length || sIdx < stage.lines.length) {
    const prodLine = prod.lines[pIdx] || '';
    const stageLine = stage.lines[sIdx] || '';

    const pNorm = prodLine.trim();
    const sNorm = stageLine.trim();

    if (pNorm === sNorm) {
      pIdx++; sIdx++; continue;
    }

    let foundMatch = false;
    const lookAhead = 150; // DEEP VALIDATION: Check up to 150 lines ahead to resync

    for (let i = 1; i < lookAhead && (sIdx + i) < stage.lines.length; i++) {
      if (stage.lines[sIdx + i].trim() === pNorm && pNorm !== '') {
        const pageNum = getPageNum(stage, sIdx);
        const topicName = getTopicAtLine(stage, sIdx);
        issues.push({
          category: 'Extra Content in Stage',
          severity: 'Critical',
          page: `Page ${pageNum}`,
          lineNum: sIdx + 1,
          prodContent: 'NOT IN PROD',
          stageContent: `STAGE TOPIC: ${topicName} | ${stageLine.substring(0, 150)}`,
          description: `❌ Extra content found in Stage (Page ${pageNum}) that does not exist in Prod.`,
        });
        sIdx++;
        foundMatch = true;
        break;
      }
    }

    if (!foundMatch) {
      for (let i = 1; i < lookAhead && (pIdx + i) < prod.lines.length; i++) {
        if (prod.lines[pIdx + i].trim() === sNorm && sNorm !== '') {
          const pageNum = getPageNum(prod, pIdx);
          const topicName = getTopicAtLine(prod, pIdx);
          issues.push({
            category: 'Missing Content in Stage',
            severity: 'Critical',
            page: `Page ${pageNum}`,
            lineNum: pIdx + 1,
            prodContent: `PROD TOPIC: ${topicName} | ${prodLine.substring(0, 150)}`,
            stageContent: 'MISSING IN STAGE',
            description: `❌ Content found in Prod (Page ${pageNum}) but is completely missing in Stage.`,
          });
          pIdx++;
          foundMatch = true;
          break;
        }
      }
    }

    if (!foundMatch) {
      const pageNum = getPageNum(prod, pIdx);
      const topicName = getTopicAtLine(prod, pIdx);
      const severity = detectMismatchSeverity(prodLine, stageLine);
      issues.push({
        category: severity.category,
        severity: severity.level,
        page: `Page ${pageNum}`,
        lineNum: pIdx + 1,
        prodContent: `[${topicName}] ${prodLine.substring(0, 150)}`,
        stageContent: stageLine.substring(0, 150),
        description: severity.description,
      });
      pIdx++;
      sIdx++;
    }
  }

  // 3. Table comparison
  const maxTables = Math.max(prod.tables.length, stage.tables.length);
  for (let t = 0; t < maxTables; t++) {
    const prodTable = prod.tables[t];
    const stageTable = stage.tables[t];

    if (!prodTable && stageTable) {
      issues.push({
        category: 'Table Structure',
        severity: 'Critical',
        page: `Page ${stageTable.pageNum}`,
        lineNum: 0,
        prodContent: '(no table)',
        stageContent: `Table with ${stageTable.rowCount} rows`,
        description: `Extra table in Stage (Table #${t + 1})`,
      });
      continue;
    }
    if (prodTable && !stageTable) {
      issues.push({
        category: 'Table Structure',
        severity: 'Critical',
        page: `Page ${prodTable.pageNum}`,
        lineNum: 0,
        prodContent: `Table with ${prodTable.rowCount} rows`,
        stageContent: '(no table)',
        description: `Missing table in Stage (Table #${t + 1})`,
      });
      continue;
    }
    if (prodTable && stageTable) {
      // Row count mismatch
      if (prodTable.rowCount !== stageTable.rowCount) {
        issues.push({
          category: 'Table Structure',
          severity: 'Major',
          page: `Page ${prodTable.pageNum}`,
          lineNum: 0,
          prodContent: `${prodTable.rowCount} rows`,
          stageContent: `${stageTable.rowCount} rows`,
          description: `Table #${t + 1} row count mismatch`,
        });
      }
      // Cell-by-cell comparison
      const maxRows = Math.max(prodTable.rows.length, stageTable.rows.length);
      for (let r = 0; r < maxRows; r++) {
        const prodRow = prodTable.rows[r] || [];
        const stageRow = stageTable.rows[r] || [];
        const maxCells = Math.max(prodRow.length, stageRow.length);
        for (let c = 0; c < maxCells; c++) {
          const prodCell = (prodRow[c] || '').trim();
          const stageCell = (stageRow[c] || '').trim();
          if (prodCell !== stageCell) {
            issues.push({
              category: 'Table Cell Content',
              severity: 'Major',
              page: `Page ${prodTable.pageNum}`,
              lineNum: 0,
              prodContent: prodCell.substring(0, 150) || '(empty)',
              stageContent: stageCell.substring(0, 150) || '(empty)',
              description: `Table #${t + 1}, Row ${r + 1}, Col ${c + 1} content differs`,
            });
          }
        }
      }
    }
  }

  // 4. Line break / paragraph break analysis
  const prodBreaks = countConsecutiveEmptyLines(prod.lines);
  const stageBreaks = countConsecutiveEmptyLines(stage.lines);
  if (prodBreaks !== stageBreaks) {
    issues.push({
      category: 'Line Breaks / Formatting',
      severity: 'Minor',
      page: 'All',
      lineNum: 0,
      prodContent: `${prodBreaks} break groups`,
      stageContent: `${stageBreaks} break groups`,
      description: `Different paragraph/line break patterns (Prod: ${prodBreaks}, Stage: ${stageBreaks})`,
    });
  }

  // 5. Metadata comparison
  const allKeys = new Set([...Object.keys(prod.metadata), ...Object.keys(stage.metadata)]);
  for (const key of Array.from(allKeys)) {
    const pVal = String(prod.metadata[key] || '');
    const sVal = String(stage.metadata[key] || '');
    if (pVal !== sVal) {
      issues.push({
        category: 'Metadata',
        severity: 'Info',
        page: 'N/A',
        lineNum: 0,
        prodContent: `${key}: ${pVal.substring(0, 100)}`,
        stageContent: `${key}: ${sVal.substring(0, 100)}`,
        description: `PDF metadata "${key}" differs`,
      });
    }
  }

  return issues;
}

/** Helper to find page number for a global line index */
function getPageNum(pdf: PdfContent, lineIdx: number): number {
  let lineAccum = 0;
  for (const p of pdf.pages) {
    lineAccum += p.lines.length;
    if (lineIdx < lineAccum) return p.pageNum;
  }
  return pdf.pageCount;
}

/** Helper to find the nearest heading/topic name for a line */
function getTopicAtLine(pdf: PdfContent, lineIdx: number): string {
  for (let i = lineIdx; i >= 0; i--) {
    const line = pdf.lines[i].trim();
    if (line.length > 3 && line.length < 100 && (line === line.toUpperCase() || /^[0-9.]+ /.test(line))) {
       return line;
    }
  }
  return 'General Content';
}

function detectMismatchSeverity(prodLine: string, stageLine: string): { category: string; level: 'Critical' | 'Major' | 'Minor'; description: string } {
  const prodNorm = prodLine.replace(/\s+/g, ' ').trim();
  const stageNorm = stageLine.replace(/\s+/g, ' ').trim();

  if (prodNorm === stageNorm) {
    return { category: 'Whitespace/Padding', level: 'Minor', description: 'Only whitespace/padding difference' };
  }

  // Check if it's just case difference
  if (prodNorm.toLowerCase() === stageNorm.toLowerCase()) {
    return { category: 'Case Mismatch', level: 'Minor', description: 'Text differs only in letter casing' };
  }

  // Check if one is a subset of the other (truncation/overflow)
  if (prodNorm.includes(stageNorm) || stageNorm.includes(prodNorm)) {
    return { category: 'Text Truncation/Overflow', level: 'Major', description: 'One line appears to be truncated or has extra content' };
  }

  // Check punctuation only difference
  const prodAlpha = prodLine.replace(/[^a-zA-Z0-9]/g, '');
  const stageAlpha = stageLine.replace(/[^a-zA-Z0-9]/g, '');
  if (prodAlpha === stageAlpha) {
    return { category: 'Punctuation/Symbols', level: 'Minor', description: 'Difference in punctuation or special characters only' };
  }

  return { category: 'Content Mismatch', level: 'Critical', description: 'Text content is significantly different' };
}

function countConsecutiveEmptyLines(lines: string[]): number {
  let count = 0;
  let inEmpty = false;
  for (const line of lines) {
    if (!line.trim()) {
      if (!inEmpty) { count++; inEmpty = true; }
    } else {
      inEmpty = false;
    }
  }
  return count;
}

/** Match PDF files between prod and stage by normalized name */
function matchPdfFiles(prodFiles: string[], stageFiles: string[]): { prod: string; stage: string }[] {
  const normalize = (f: string) => f.toLowerCase().replace(/[-_\s]+/g, '_').replace(/\d{4}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2}/, '').replace(/\.pdf$/, '').trim();

  const pairs: { prod: string; stage: string }[] = [];
  for (const pf of prodFiles) {
    const pNorm = normalize(pf);
    // Find best match in stage
    let bestMatch = '';
    let bestScore = 0;
    for (const sf of stageFiles) {
      const sNorm = normalize(sf);
      // Simple similarity: common prefix length
      let common = 0;
      const minLen = Math.min(pNorm.length, sNorm.length);
      for (let i = 0; i < minLen; i++) {
        if (pNorm[i] === sNorm[i]) common++;
        else break;
      }
      const score = common / Math.max(pNorm.length, sNorm.length);
      if (score > bestScore && score > 0.4) {
        bestScore = score;
        bestMatch = sf;
      }
    }
    if (bestMatch) {
      pairs.push({ prod: pf, stage: bestMatch });
    } else if (stageFiles.length === 1 && prodFiles.length === 1) {
      // If only one file each, assume they match
      pairs.push({ prod: pf, stage: stageFiles[0] });
    }
  }
  return pairs;
}

/** Download PDF from a URL */
async function downloadPdf(page: Page, url: string, destPath: string): Promise<boolean> {
  try {
    const response = await page.goto(appendWcmDisabled(url), { waitUntil: 'networkidle', timeout: 60_000 });
    if (!response) return false;

    // If it's a direct PDF, save it
    const contentType = response.headers()['content-type'] || '';
    if (contentType.includes('pdf')) {
      const buffer = await response.body();
      fs.writeFileSync(destPath, buffer);
      return true;
    }

    // Try to find PDF download link on the page
    const pdfLink = await page.$('a[href$=".pdf"], a[href*="pdf"]');
    if (pdfLink) {
      const href = await pdfLink.getAttribute('href');
      if (href) {
        const absoluteUrl = new URL(href, url).toString();
        const pdfResponse = await page.goto(absoluteUrl, { waitUntil: 'networkidle', timeout: 60_000 });
        if (pdfResponse) {
          const buffer = await pdfResponse.body();
          fs.writeFileSync(destPath, buffer);
          return true;
        }
      }
    }
    return false;
  } catch (e) {
    console.error(`Failed to download PDF from ${url}:`, e);
    return false;
  }
}

/** Build Excel Report */
async function buildReport(pairs: { prodFile: string; stageFile: string; issues: Issue[]; prodContent: PdfContent; stageContent: PdfContent }[]): Promise<void> {
  if (!fs.existsSync(REPORTS_DIR)) fs.mkdirSync(REPORTS_DIR, { recursive: true });

  const wb = new ExcelJS.Workbook();

  // ─── Sheet 1: Summary ───
  const summary = wb.addWorksheet('Summary');
  summary.columns = [
    { header: 'PDF File (Prod)', key: 'prodFile', width: 45 },
    { header: 'PDF File (Stage)', key: 'stageFile', width: 45 },
    { header: 'Prod Pages', key: 'prodPages', width: 12 },
    { header: 'Stage Pages', key: 'stagePages', width: 12 },
    { header: 'Total Issues', key: 'totalIssues', width: 12 },
    { header: 'Critical', key: 'critical', width: 10 },
    { header: 'Major', key: 'major', width: 10 },
    { header: 'Minor', key: 'minor', width: 10 },
    { header: 'Info', key: 'info', width: 10 },
  ];
  styleHeader(summary);

  for (const p of pairs) {
    const crit = p.issues.filter(i => i.severity === 'Critical').length;
    const maj = p.issues.filter(i => i.severity === 'Major').length;
    const min = p.issues.filter(i => i.severity === 'Minor').length;
    const info = p.issues.filter(i => i.severity === 'Info').length;
    const row = summary.addRow({
      prodFile: p.prodFile,
      stageFile: p.stageFile,
      prodPages: p.prodContent.pageCount,
      stagePages: p.stageContent.pageCount,
      totalIssues: p.issues.length,
      critical: crit,
      major: maj,
      minor: min,
      info: info,
    });
    if (crit > 0) row.getCell('critical').fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFF0000' } } as ExcelJS.FillPattern;
    if (maj > 0) row.getCell('major').fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFF8C00' } } as ExcelJS.FillPattern;
  }

  // ─── Sheet 2: All Issues ───
  const issueSheet = wb.addWorksheet('All Issues');
  issueSheet.columns = [
    { header: 'File', key: 'file', width: 35 },
    { header: 'Category', key: 'category', width: 22 },
    { header: 'Severity', key: 'severity', width: 10 },
    { header: 'Page', key: 'page', width: 10 },
    { header: 'Line #', key: 'lineNum', width: 8 },
    { header: 'Prod Content', key: 'prodContent', width: 50 },
    { header: 'Stage Content', key: 'stageContent', width: 50 },
    { header: 'Description', key: 'description', width: 45 },
  ];
  styleHeader(issueSheet);

  for (const p of pairs) {
    for (const issue of p.issues) {
      const row = issueSheet.addRow({
        file: p.prodFile,
        category: issue.category,
        severity: issue.severity,
        page: issue.page,
        lineNum: issue.lineNum || '',
        prodContent: issue.prodContent,
        stageContent: issue.stageContent,
        description: issue.description,
      });
      const sevCell = row.getCell('severity');
      switch (issue.severity) {
        case 'Critical': sevCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFF0000' } } as ExcelJS.FillPattern; sevCell.font = { color: { argb: 'FFFFFFFF' }, bold: true }; break;
        case 'Major': sevCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFF8C00' } } as ExcelJS.FillPattern; break;
        case 'Minor': sevCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFFF00' } } as ExcelJS.FillPattern; break;
        case 'Info': sevCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF87CEEB' } } as ExcelJS.FillPattern; break;
      }
    }
  }

  // ─── Sheet 3: Table Comparison ───
  const tableSheet = wb.addWorksheet('Table Issues');
  tableSheet.columns = [
    { header: 'File', key: 'file', width: 35 },
    { header: 'Category', key: 'category', width: 20 },
    { header: 'Page', key: 'page', width: 10 },
    { header: 'Description', key: 'description', width: 50 },
    { header: 'Prod Content', key: 'prodContent', width: 50 },
    { header: 'Stage Content', key: 'stageContent', width: 50 },
  ];
  styleHeader(tableSheet);

  for (const p of pairs) {
    const tableIssues = p.issues.filter(i => i.category.includes('Table'));
    for (const issue of tableIssues) {
      tableSheet.addRow({
        file: p.prodFile,
        category: issue.category,
        page: issue.page,
        description: issue.description,
        prodContent: issue.prodContent,
        stageContent: issue.stageContent,
      });
    }
  }

  // ─── Sheet 4: Formatting Issues ───
  const fmtSheet = wb.addWorksheet('Formatting Issues');
  fmtSheet.columns = [
    { header: 'File', key: 'file', width: 35 },
    { header: 'Category', key: 'category', width: 22 },
    { header: 'Page', key: 'page', width: 10 },
    { header: 'Line #', key: 'lineNum', width: 8 },
    { header: 'Description', key: 'description', width: 50 },
    { header: 'Prod', key: 'prodContent', width: 50 },
    { header: 'Stage', key: 'stageContent', width: 50 },
  ];
  styleHeader(fmtSheet);

  const fmtCategories = ['Whitespace/Padding', 'Line Breaks / Formatting', 'Text Truncation/Overflow', 'Punctuation/Symbols'];
  for (const p of pairs) {
    const fmtIssues = p.issues.filter(i => fmtCategories.includes(i.category));
    for (const issue of fmtIssues) {
      fmtSheet.addRow({
        file: p.prodFile,
        category: issue.category,
        page: issue.page,
        lineNum: issue.lineNum || '',
        description: issue.description,
        prodContent: issue.prodContent,
        stageContent: issue.stageContent,
      });
    }
  }

  // ─── Sheet 5: Line-by-Line Diff (first 500 differences) ───
  const diffSheet = wb.addWorksheet('Line Diff (Sample)');
  diffSheet.columns = [
    { header: 'File', key: 'file', width: 30 },
    { header: 'Line #', key: 'lineNum', width: 8 },
    { header: 'Page', key: 'page', width: 10 },
    { header: 'Prod Line', key: 'prodLine', width: 60 },
    { header: 'Stage Line', key: 'stageLine', width: 60 },
    { header: 'Type', key: 'type', width: 20 },
  ];
  styleHeader(diffSheet);

  let diffCount = 0;
  const MAX_DIFFS = 500;
  for (const p of pairs) {
    if (diffCount >= MAX_DIFFS) break;
    const contentIssues = p.issues.filter(i =>
      i.category === 'Content Mismatch' ||
      i.category === 'Missing Content in Stage' ||
      i.category === 'Extra Content in Stage'
    );
    for (const issue of contentIssues) {
      if (diffCount >= MAX_DIFFS) break;
      diffSheet.addRow({
        file: p.prodFile,
        lineNum: issue.lineNum,
        page: issue.page,
        prodLine: issue.prodContent,
        stageLine: issue.stageContent,
        type: issue.category,
      });
      diffCount++;
    }
  }

  const filename = process.env.REPORT_FILENAME || 'pdf-validation.xlsx';
  const reportPath = path.join(REPORTS_DIR, filename);
  await wb.xlsx.writeFile(reportPath);
  console.log(`📊 Report saved: ${reportPath}`);
}

function styleHeader(sheet: ExcelJS.Worksheet) {
  const headerRow = sheet.getRow(1);
  headerRow.font = { bold: true, color: { argb: 'FFFFFFFF' } };
  headerRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1F4E79' } } as ExcelJS.FillPattern;
  headerRow.alignment = { vertical: 'middle', horizontal: 'center' };
  sheet.views = [{ state: 'frozen', ySplit: 1, xSplit: 0, topLeftCell: 'A2', activeCell: 'A2' }];
}

/* ─── Main Test ──────────────────────────────────────────────── */

test.describe('PDF Validation: Stage vs Prod', () => {
  test.setTimeout(600_000); // 10 min

  test('Compare PDF content line-by-line between Prod and Stage', async ({ browser }) => {
    // Ensure directories exist
    if (!fs.existsSync(PROD_DIR)) fs.mkdirSync(PROD_DIR, { recursive: true });
    if (!fs.existsSync(STAGE_DIR)) fs.mkdirSync(STAGE_DIR, { recursive: true });

    // Get PDF files
    let prodFiles = fs.readdirSync(PROD_DIR).filter(f => f.toLowerCase().endsWith('.pdf'));
    let stageFiles = fs.readdirSync(STAGE_DIR).filter(f => f.toLowerCase().endsWith('.pdf'));

    // Filter by environment variables if provided
    if (process.env.PROD_FILE) {
      prodFiles = prodFiles.filter(f => f === process.env.PROD_FILE);
    }
    if (process.env.STAGE_FILE) {
      stageFiles = stageFiles.filter(f => f === process.env.STAGE_FILE);
    }

    console.log(`📂 Prod PDFs: ${prodFiles.length}, Stage PDFs: ${stageFiles.length}`);

    // If Stage is empty but Prod has files, try to download Stage PDFs from AEM
    if (stageFiles.length === 0 && prodFiles.length > 0) {
      console.log('⚠️  Stage folder empty — attempting to download Stage PDFs from AEM...');
      const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
      const stageBaseUrl = new URL(config.stage).origin;

      const ctx = await getAuthContext(browser);
      const page = await ctx.newPage();

      // Navigate to stage to check auth
      await page.goto(appendWcmDisabled(config.stage), { waitUntil: 'domcontentloaded', timeout: 30_000 });
      if (isAuthWall(page)) {
        await page.close();
        await ctx.close();
        // Re-login
        const loginScript = path.resolve(__dirname, '../../run_login.ts');
        execSync(`npx ts-node "${loginScript}"`, {
          cwd: path.resolve(__dirname, '../..'),
          stdio: 'inherit',
          timeout: 120_000,
        });
        const ctx2 = await browser.newContext({ storageState: AUTH_STATE });
        const page2 = await ctx2.newPage();

        // Try to find and download PDFs from Stage
        for (const prodFile of prodFiles) {
          // Construct potential stage PDF URL from bundle
          const bundleName = 'AdministeringAvayaAuraAdminPortal'; // from config
          const pdfUrl = `${stageBaseUrl}/content/dam/aemsites/en-us/bundle/${bundleName}/${prodFile}`;
          console.log(`  Trying: ${pdfUrl}`);
          const downloaded = await downloadPdf(page2, appendWcmDisabled(pdfUrl), path.join(STAGE_DIR, prodFile));
          if (downloaded) console.log(`  ✅ Downloaded: ${prodFile}`);
          else console.log(`  ❌ Could not download: ${prodFile}`);
        }
        await page2.close();
        await ctx2.close();
      } else {
        // Try to find and download PDFs from Stage
        for (const prodFile of prodFiles) {
          const bundleName = 'AdministeringAvayaAuraAdminPortal';
          const pdfUrl = `${stageBaseUrl}/content/dam/aemsites/en-us/bundle/${bundleName}/${prodFile}`;
          console.log(`  Trying: ${pdfUrl}`);
          const downloaded = await downloadPdf(page, pdfUrl, path.join(STAGE_DIR, prodFile));
          if (downloaded) console.log(`  ✅ Downloaded: ${prodFile}`);
          else console.log(`  ❌ Could not download: ${prodFile}`);
        }
        await page.close();
        await ctx.close();
      }

      stageFiles = fs.readdirSync(STAGE_DIR).filter(f => f.toLowerCase().endsWith('.pdf'));
      if (process.env.STAGE_FILE) {
        stageFiles = stageFiles.filter(f => f === process.env.STAGE_FILE);
      }
    }

    if (stageFiles.length === 0 && prodFiles.length > 0) {
      throw new Error(`❌ STAGE FILE NOT FOUND. Ensure you uploaded the correct Stage file and that its name matches exactly: "${process.env.STAGE_FILE || 'any'}"`);
    }

    expect(prodFiles.length, 'No PDF files found in PDF/prod/').toBeGreaterThan(0);
    expect(stageFiles.length, 'No PDF files found in PDF/stage/').toBeGreaterThan(0);

    // Match files between prod and stage
    let pairs: { prod: string; stage: string }[] = [];
    if (process.env.PROD_FILE && process.env.STAGE_FILE && prodFiles.length === 1 && stageFiles.length === 1) {
      // Explicit 1-to-1 validation requested by UI
      pairs.push({ prod: prodFiles[0], stage: stageFiles[0] });
    } else {
      pairs = matchPdfFiles(prodFiles, stageFiles);
      if (pairs.length === 0 && prodFiles.length > 0 && stageFiles.length > 0) {
        // Fallback: match by index
        const minCount = Math.min(prodFiles.length, stageFiles.length);
        for (let i = 0; i < minCount; i++) {
          pairs.push({ prod: prodFiles[i], stage: stageFiles[i] });
        }
      }
    }

    console.log(`🔗 Matched ${pairs.length} PDF pair(s) for comparison`);

    // Parse and compare each pair
    const results: { prodFile: string; stageFile: string; issues: Issue[]; prodContent: PdfContent; stageContent: PdfContent }[] = [];

    for (const pair of pairs) {
      console.log(`\n📄 Comparing: "${pair.prod}" vs "${pair.stage}"`);

      const prodBuffer = fs.readFileSync(path.join(PROD_DIR, pair.prod));
      const stageBuffer = fs.readFileSync(path.join(STAGE_DIR, pair.stage));

      const prodContent = await parsePdf(prodBuffer, pair.prod);
      const stageContent = await parsePdf(stageBuffer, pair.stage);

      console.log(`   Prod: ${prodContent.pageCount} pages, ${prodContent.lines.length} lines, ${prodContent.tables.length} tables`);
      console.log(`   Stage: ${stageContent.pageCount} pages, ${stageContent.lines.length} lines, ${stageContent.tables.length} tables`);

      const issues = comparePdfs(prodContent, stageContent);
      console.log(`   Issues found: ${issues.length} (Critical: ${issues.filter(i => i.severity === 'Critical').length}, Major: ${issues.filter(i => i.severity === 'Major').length})`);

      results.push({
        prodFile: pair.prod,
        stageFile: pair.stage,
        issues,
        prodContent,
        stageContent,
      });
    }

    // Generate report
    await buildReport(results);

    const totalIssues = results.reduce((sum, r) => sum + r.issues.length, 0);
    console.log(`\n✅ PDF Validation Complete: ${pairs.length} pair(s), ${totalIssues} total issues`);
    console.log(`📊 Report: ${REPORT_PATH}`);
  });
});
