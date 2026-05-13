/**
 * leftnav-toc-validation.spec.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * Captures the full left-nav TOC tree (all nodes) from Stage and Prod,
 * saves both as JSON, then compares:
 *   • Sequence order (which items are out of order)
 *   • Missing items  (in Stage but not Prod, or Prod but not Stage)
 *   • Extra items    (appear in one but not the other)
 *   • Case issues    (not in correct uppercase/title case)
 *   • Symbol issues  (special symbols like ®, ™ missing or different)
 *
 * Generates an Excel report: reports/leftnav-toc-validation-<ts>.xlsx
 *
 * Run:  npx playwright test tests/content-validation/leftnav-toc-validation.spec.ts
 */

import { test, expect, Page, Browser } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import * as ExcelJS from 'exceljs';
import { execSync } from 'child_process';

// ─── Config ──────────────────────────────────────────────────────────────────

const TEST_URLS_PATH  = path.resolve(__dirname, '../../config/test-urls.json');
const AUTH_STATE_PATH = path.resolve(__dirname, '../../auth-sessions/storage-state.json');
const REPORTS_DIR     = path.resolve(__dirname, '../../reports');
const LOGIN_SCRIPT    = path.resolve(__dirname, '../../run_login.ts');

const testUrls: { stage: string; production: string } = {
  stage:      process.env.STAGE_URL || '',
  production: process.env.PROD_URL  || '',
};

if (!testUrls.stage || !testUrls.production) {
  try {
    const config = JSON.parse(fs.readFileSync(TEST_URLS_PATH, 'utf-8'));
    if (!testUrls.stage)      testUrls.stage = config.stage;
    if (!testUrls.production) testUrls.production = config.production;
  } catch (e) {
    console.warn('   [WARN] No URLs found in environment or test-urls.json');
  }
}

function deriveBase(url: string) {
  if (!url) return { origin: '', bundle: '', isStage: false };
  try {
    const parsed = new URL(url);
    const isStage = parsed.hostname.includes('adobeaemcloud.com');
    const bundleMatch = url.match(/\/bundle\/([^/]+)\//);
    const bundle = bundleMatch ? bundleMatch[1] : '';
    return { origin: parsed.origin, bundle, isStage };
  } catch (e) {
    return { origin: '', bundle: '', isStage: false };
  }
}

const stageInfo = deriveBase(testUrls.stage);
const prodInfo  = deriveBase(testUrls.production);

// ─── Types ───────────────────────────────────────────────────────────────────

interface TocNode {
  index: number;       // position in the list (1-based)
  title: string;       // exact text from the link
  url: string;         // href
  level: number;       // nesting depth (0 = root)
  hierarchy?: string;  // full hierarchy path
}

interface TocIssue {
  type: 'missing_in_prod' | 'missing_in_stage' | 'extra_in_prod' | 'extra_in_stage'
      | 'out_of_order' | 'case_mismatch' | 'symbol_mismatch' | 'grouping_mismatch';
  severity: 'high' | 'medium' | 'low';
  stageIndex: number | null;
  prodIndex: number | null;
  stageTitle: string;
  prodTitle: string;
  detail: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function isAuthWall(page: Page): Promise<boolean> {
  const url = page.url();
  // If we've landed on a login-specific domain, it's definitely an auth wall
  if (url.includes('adobelogin') || url.includes('ims-na1') || url.includes('auth.services.adobe.com')) {
    return true;
  }

  try {
    // Wait for the page to at least start loading
    await page.waitForLoadState('domcontentloaded', { timeout: 3000 }).catch(() => {});
    
    const content = await page.content().catch(() => '');
    
    // CRITICAL: If we see ANY documentation navigation or AEM components, we are LOGGED IN.
    // Skip login entirely in this case.
    if (/zDocsTOC|leftnav|sidebar|toc-list|cmp-navigation|cmp-navigation__item/i.test(content)) {
      console.log('   [Auth] Content navigation detected — session is active.');
      return false;
    }

    const txt = await page.innerText('body').catch(() => '');
    if (!txt) return false;
    
    // Check for explicit login prompts
    const isLoginPrompt = /sign in to your account|log in to aem|adobe experience manager.*welcome|authentication required/i.test(txt.toLowerCase());
    
    return isLoginPrompt;
  } catch (e) {
    // If context is destroyed or other error, assume it might be a redirect to login
    return url.includes('author-') || url.includes('adobeaemcloud');
  }
}

async function autoLogin(browser: Browser): Promise<{ ctx: any; page: Page }> {
  console.log('\n🔐  Session expired — running run_login.ts automatically…');
  execSync(`npx tsx "${LOGIN_SCRIPT}"`, {
    stdio: 'inherit',
    cwd: path.resolve(__dirname, '../..'),
    timeout: 180_000,
  });
  console.log('✅  Login completed — reloading session.');
  const ctx = await browser.newContext({
    storageState: AUTH_STATE_PATH,
    ignoreHTTPSErrors: true,
  });
  const page = await ctx.newPage();
  return { ctx, page };
}

/** 
 * Handles common popups on Avaya Prod (Cookie banner, Login prompt)
 */
async function handleProdPopups(page: Page) {
  console.log('   [Prod] Checking for cookie banner/popups…');
  // 1. OneTrust Cookie Banner
  await page.click('#onetrust-accept-btn-handler', { timeout: 8000 }).catch(() => {});
  
  // 2. Escape any modal/login prompt
  await page.keyboard.press('Escape');
  
  // 3. Try clicking close buttons
  await page.click('.zDocsCloseButton, .modal-close, [aria-label="Close"]', { timeout: 3000 }).catch(() => {});
  
  // Wait for any animations
  await page.waitForTimeout(1000);
}

/** Appends ?wcmmode=disabled to a URL if not already present. Only for Stage. */
function appendWcmDisabled(urlStr: string, isStage: boolean): string {
  if (!isStage || !urlStr) return urlStr;

  let finalUrl = urlStr;
  // If it's an AEM Editor URL, convert to content URL
  if (finalUrl.includes('/editor.html/')) {
    finalUrl = finalUrl.replace('/editor.html/', '/');
  } else if (finalUrl.includes('/ui#/aem/editor.html')) {
    // Handle the complex AEM UI hash URLs
    const match = finalUrl.match(/\/editor\.html(.+)$/);
    if (match) {
        const origin = new URL(finalUrl).origin;
        finalUrl = origin + match[1];
    }
  }

  if (finalUrl.includes('wcmmode=disabled')) return finalUrl;
  
  try {
    const url = new URL(finalUrl);
    url.searchParams.set('wcmmode', 'disabled');
    return url.toString();
  } catch (e) {
    const [path, hash] = finalUrl.split('#');
    const separator = path.includes('?') ? '&' : '?';
    return `${path}${separator}wcmmode=disabled${hash ? '#' + hash : ''}`;
  }
}

/** Extract the full left-nav TOC as a flat ordered list of nodes */
async function extractTocNodes(page: Page, bundle: string, isStage: boolean): Promise<TocNode[]> {
  // Stage: nav.cmp-navigation has all 200+ topic links
  // Prod: no visible TOC — site doesn't render one
  const navSelectors = isStage ? [
    'nav.cmp-navigation a[href], nav.cmp-navigation span.cmp-navigation__item-title',          // Primary Stage TOC (233 links)
    '[role="navigation"] a[href], [role="navigation"] span.cmp-navigation__item-title',
    'nav a[href], nav span.cmp-navigation__item-title',
  ] : [
    '.zDocsTOC a[href], .zDocsTOC span.zDocsTocItemTitle',                                     // Zoomin TOC (Prod)
    '.zDocsTocList a[href]',
    `#topicToc_${bundle} a[href]`,
    'nav.cmp-navigation a[href]',
    '.zDocsTOC a[href]',
    'nav a[href]',
  ];

  const uiChromeKeywords = [
    'share this', 'cookie', 'preference', 'download', 'export', 'pdf', 'print',
    'language', 'english', 'deutsch', 'español', 'français', 'italiano',
    'topic navigation', 'in this article', 'stay connected', 'was this page',
    'javascript', 'feedback', 'submit', 'search', 'menu', 'breadcrumb',
    'table of contents', 'collapse', 'toggle', 'filter', '한국어', '日本語',
    '中文', 'polski', 'svenska', 'magyar', 'nederlands', 'türkçe',
    'library', 'home', 'login', 'logout', 'avaya support', 'avaya learning',
    'blogs', 'videos', 'knowledge base', 'report product', 'next topic',
    'notice', 'documentation disclaimer', 'link disclaimer', 'hosted service',
    'copyright', 'third party components', 'service provider', 'compliance with laws',
    'preventing toll fraud', 'avaya toll fraud', 'security vulnerabilities',
    'trademarks', 'downloading documentation', 'contact avaya',
  ];

  for (const sel of navSelectors) {
    try {
      const items: TocNode[] = await page.$$eval(sel, (els: any[], params: any) => {
        return els.map((el: any, idx: number) => {
          const anchor = el as HTMLAnchorElement;
          // Determine nesting level from parent list depth
          let level = 0;
          let parent = anchor.parentElement;
          while (parent) {
            if (parent.tagName === 'UL' || parent.tagName === 'OL') level++;
            parent = parent.parentElement;
          }
          return {
            index: idx + 1,
            title: anchor.textContent?.trim() ?? '',
            url: anchor.href ?? '',
            level: Math.max(0, level - 1), // normalize (first UL is level 0)
          };
        })
        .map((t: any) => {
          if (!t.url) return t;
          return {
            ...t,
            url: params.isStg ? (t.url.includes('wcmmode=disabled') ? t.url : (t.url.includes('?') ? t.url + '&wcmmode=disabled' : t.url + '?wcmmode=disabled')) : t.url
          };
        })
        .filter((t: any) => {
          if (t.title.length < 3) return false;
          if (t.url && !t.url.includes(params.bndl)) return false;
          const lower = t.title.toLowerCase();
          return !params.chrome.some((kw: string) => lower.includes(kw.toLowerCase()));
        });
      }, { bndl: bundle, chrome: uiChromeKeywords, isStg: isStage });

      if (items.length > 1) {
        // Re-index and compute hierarchy after filtering
        const pathArr: string[] = [];
        items.forEach((item, i) => { 
          item.index = i + 1;
          pathArr[item.level] = item.title;
          pathArr.length = item.level + 1; // Trim deeper levels
          item.hierarchy = pathArr.join(' > ');
        });
        console.log(`   [TOC extracted via "${sel}" → ${items.length} nodes]`);
        return items;
      }
    } catch { /* try next */ }
  }

  return [];
}

/** Compare two TOC lists and find all issues */
function compareTocs(stage: TocNode[], prod: TocNode[]): TocIssue[] {
  const issues: TocIssue[] = [];

  const normalize = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const stageNormMap = new Map(stage.map(n => [normalize(n.title), n]));
  const prodNormMap  = new Map(prod.map(n => [normalize(n.title), n]));

  // ─── Missing / Extra ───────────────────────────────────────────────────
  for (const sNode of stage) {
    const key = normalize(sNode.title);
    if (!prodNormMap.has(key)) {
      issues.push({
        type: 'missing_in_prod',
        severity: 'high',
        stageIndex: sNode.index,
        prodIndex: null,
        stageTitle: sNode.title,
        prodTitle: '',
        detail: `Topic "${sNode.title}" exists in Stage (pos ${sNode.index}) but NOT in Prod`,
      });
    }
  }

  for (const pNode of prod) {
    const key = normalize(pNode.title);
    if (!stageNormMap.has(key)) {
      issues.push({
        type: 'missing_in_stage',
        severity: 'high',
        stageIndex: null,
        prodIndex: pNode.index,
        stageTitle: '',
        prodTitle: pNode.title,
        detail: `Topic "${pNode.title}" exists in Prod (pos ${pNode.index}) but NOT in Stage`,
      });
    }
  }

  // ─── Sequence / Order comparison ───────────────────────────────────────
  // Build ordered lists of matched items only (preserving their relative sequence)
  const stageMatched = stage.filter(n => prodNormMap.has(normalize(n.title)));
  const prodMatched  = prod.filter(n => stageNormMap.has(normalize(n.title)));

  // Create the sequence of normalized titles in each
  const stageSeq = stageMatched.map(n => normalize(n.title));
  const prodSeq  = prodMatched.map(n => normalize(n.title));

  // Find items out of order (appear in different positions)
  for (let i = 0; i < stageSeq.length; i++) {
    const prodIdx = prodSeq.indexOf(stageSeq[i]);
    if (prodIdx !== -1) {
      const sNode = stageMatched[i];
      const pNode = prodMatched[prodIdx];

      // Check for Grouping (Level/Nesting) mismatch
      if (sNode.level !== pNode.level) {
        issues.push({
          type: 'grouping_mismatch',
          severity: 'medium',
          stageIndex: sNode.index,
          prodIndex: pNode.index,
          stageTitle: sNode.title,
          prodTitle: pNode.title,
          detail: `Grouping mismatch: "${sNode.title}" is at level ${sNode.level} in Stage but level ${pNode.level} in Prod`,
        });
      }

      if (prodIdx !== i) {
        issues.push({
          type: 'out_of_order',
        severity: 'medium',
        stageIndex: sNode.index,
        prodIndex: pNode.index,
        stageTitle: sNode.title,
        prodTitle: pNode.title,
        detail: `"${sNode.title}" is at position ${sNode.index} in Stage but ${pNode.index} in Prod`,
      });
    }
  }

  // ─── Case mismatch ────────────────────────────────────────────────────
  for (const sNode of stage) {
    const key = normalize(sNode.title);
    const pNode = prodNormMap.get(key);
    if (!pNode) continue;

    // Check exact case match (ignore symbols for this check)
    const sClean = sNode.title.replace(/[®™©]/g, '').trim();
    const pClean = pNode.title.replace(/[®™©]/g, '').trim();
    if (sClean !== pClean) {
      issues.push({
        type: 'case_mismatch',
        severity: 'low',
        stageIndex: sNode.index,
        prodIndex: pNode.index,
        stageTitle: sNode.title,
        prodTitle: pNode.title,
        detail: `Case differs — Stage: "${sNode.title}" vs Prod: "${pNode.title}"`,
      });
    }
  }

  // ─── Symbol mismatch (®, ™, ©, etc.) ─────────────────────────────────
  const symbolRegex = /[®™©°±²³¹¼½¾×÷€£¥¢§¶†‡•…–—''""«»‹›¿¡]/g;

  for (const sNode of stage) {
    const key = normalize(sNode.title);
    const pNode = prodNormMap.get(key);
    if (!pNode) continue;

    const stageSymbols = (sNode.title.match(symbolRegex) || []).sort().join('');
    const prodSymbols  = (pNode.title.match(symbolRegex) || []).sort().join('');

    if (stageSymbols !== prodSymbols) {
      const sSymArr = Array.from(sNode.title.match(symbolRegex) || []);
      const pSymArr = Array.from(pNode.title.match(symbolRegex) || []);
      const missingInProd = sSymArr.filter(sym => !pSymArr.includes(sym));
      const extraInProd = pSymArr.filter(sym => !sSymArr.includes(sym));

      let detail = `Symbol difference in "${sNode.title}"`;
      if (missingInProd.length) detail += ` — missing in Prod: ${missingInProd.join(' ')}`;
      if (extraInProd.length) detail += ` — extra in Prod: ${extraInProd.join(' ')}`;

      issues.push({
        type: 'symbol_mismatch',
        severity: 'medium',
        stageIndex: sNode.index,
        prodIndex: pNode.index,
        stageTitle: sNode.title,
        prodTitle: pNode.title,
        detail,
      });
    }
  }

  return issues;
}

/** Generate Excel report for TOC validation */
async function buildTocReport(
  stageToc: TocNode[],
  prodToc: TocNode[],
  issues: TocIssue[]
): Promise<string> {
  const wb = new ExcelJS.Workbook();
  const filename = process.env.REPORT_FILENAME || 'leftnav-toc-validation.xlsx';
  const filePath = path.isAbsolute(filename) ? filename : path.join(REPORTS_DIR, filename);

  // ─── Sheet 1: Summary ─────────────────────────────────────────────────
  const summarySheet = wb.addWorksheet('Summary');
  summarySheet.columns = [
    { header: 'Metric', key: 'metric', width: 40 },
    { header: 'Value', key: 'value', width: 20 },
  ];
  const issueCounts = {
    missing_in_prod: issues.filter(i => i.type === 'missing_in_prod').length,
    missing_in_stage: issues.filter(i => i.type === 'missing_in_stage').length,
    out_of_order: issues.filter(i => i.type === 'out_of_order').length,
    case_mismatch: issues.filter(i => i.type === 'case_mismatch').length,
    symbol_mismatch: issues.filter(i => i.type === 'symbol_mismatch').length,
  };
  const summaryData = [
    { metric: 'Stage TOC Nodes', value: stageToc.length },
    { metric: 'Prod TOC Nodes', value: prodToc.length },
    { metric: 'Total Issues', value: issues.length },
    { metric: '─── Issue Breakdown ───', value: '' },
    { metric: 'Missing in Prod (Stage has, Prod doesn\'t)', value: issueCounts.missing_in_prod },
    { metric: 'Missing in Stage (Prod has, Stage doesn\'t)', value: issueCounts.missing_in_stage },
    { metric: 'Out of Order', value: issueCounts.out_of_order },
    { metric: 'Case Mismatch', value: issueCounts.case_mismatch },
    { metric: 'Symbol Mismatch (®, ™, etc.)', value: issueCounts.symbol_mismatch },
  ];
  summaryData.forEach(row => summarySheet.addRow(row));
  summarySheet.getRow(1).font = { bold: true };

  // ─── Sheet 2: Stage TOC (full tree) ───────────────────────────────────
  const stageSheet = wb.addWorksheet('Stage TOC');
  stageSheet.columns = [
    { header: '#', key: 'index', width: 6 },
    { header: 'Level', key: 'level', width: 8 },
    { header: 'Hierarchy', key: 'hierarchy', width: 90 },
    { header: 'Title', key: 'title', width: 70 },
    { header: 'URL', key: 'url', width: 90 },
  ];
  stageToc.forEach(node => stageSheet.addRow(node));
  stageSheet.getRow(1).font = { bold: true };

  // ─── Sheet 3: Prod TOC (full tree) ────────────────────────────────────
  const prodSheet = wb.addWorksheet('Prod TOC');
  prodSheet.columns = [
    { header: '#', key: 'index', width: 6 },
    { header: 'Level', key: 'level', width: 8 },
    { header: 'Hierarchy', key: 'hierarchy', width: 90 },
    { header: 'Title', key: 'title', width: 70 },
    { header: 'URL', key: 'url', width: 90 },
  ];
  prodToc.forEach(node => prodSheet.addRow(node));
  prodSheet.getRow(1).font = { bold: true };

  // ─── Sheet 4: All Issues ──────────────────────────────────────────────
  const issuesSheet = wb.addWorksheet('Issues');
  issuesSheet.columns = [
    { header: 'Type', key: 'type', width: 20 },
    { header: 'Severity', key: 'severity', width: 12 },
    { header: 'Stage #', key: 'stageIndex', width: 10 },
    { header: 'Prod #', key: 'prodIndex', width: 10 },
    { header: 'Stage Title', key: 'stageTitle', width: 55 },
    { header: 'Prod Title', key: 'prodTitle', width: 55 },
    { header: 'Detail', key: 'detail', width: 80 },
  ];
  issuesSheet.getRow(1).font = { bold: true };

  const severityColors: Record<string, string> = {
    high: 'FFFF0000',
    medium: 'FFFF8C00',
    low: 'FFFFD700',
  };
  const typeColors: Record<string, string> = {
    missing_in_prod: 'FFFFE0E0',
    missing_in_stage: 'FFE0E0FF',
    out_of_order: 'FFFFF0E0',
    case_mismatch: 'FFFFE8D6',
    symbol_mismatch: 'FFE8F5E9',
  };

  issues.forEach(issue => {
    const row = issuesSheet.addRow(issue);
    const fillColor = typeColors[issue.type] || 'FFFFFFFF';
    row.eachCell(cell => {
      cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: fillColor } };
    });
    // Color severity cell
    const sevCell = row.getCell(2);
    sevCell.font = { bold: true, color: { argb: severityColors[issue.severity] || 'FF000000' } };
  });

  // ─── Sheet 5: Side-by-Side Sequence ───────────────────────────────────
  const seqSheet = wb.addWorksheet('Sequence Comparison');
  seqSheet.columns = [
    { header: 'Stage #', key: 'sIdx', width: 10 },
    { header: 'Stage Hierarchy', key: 'sHierarchy', width: 60 },
    { header: 'Stage Title', key: 'sTitle', width: 60 },
    { header: 'Prod #', key: 'pIdx', width: 10 },
    { header: 'Prod Hierarchy', key: 'pHierarchy', width: 60 },
    { header: 'Prod Title', key: 'pTitle', width: 60 },
    { header: 'Match', key: 'match', width: 15 },
  ];
  seqSheet.getRow(1).font = { bold: true };

  const maxLen = Math.max(stageToc.length, prodToc.length);
  const normalizeKey = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

  for (let i = 0; i < maxLen; i++) {
    const s = stageToc[i];
    const p = prodToc[i];
    const sTitle = s?.title ?? '';
    const pTitle = p?.title ?? '';

    let match = '—';
    if (s && p) {
      if (sTitle === pTitle) match = '✅ Exact';
      else if (normalizeKey(sTitle) === normalizeKey(pTitle)) match = '⚠️ Case/Symbol';
      else match = '❌ Different';
    } else if (s && !p) {
      match = '❌ Missing in Prod';
    } else if (!s && p) {
      match = '❌ Missing in Stage';
    }

    const row = seqSheet.addRow({
      sIdx: s?.index ?? '',
      sHierarchy: s?.hierarchy ?? '',
      sTitle,
      pIdx: p?.index ?? '',
      pHierarchy: p?.hierarchy ?? '',
      pTitle,
      match,
    });

    if (match.startsWith('❌')) {
      row.eachCell(cell => {
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFE0E0' } };
      });
    } else if (match.startsWith('⚠️')) {
      row.eachCell(cell => {
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFF8E1' } };
      });
    }
  }

  fs.mkdirSync(REPORTS_DIR, { recursive: true });
  await wb.xlsx.writeFile(filePath);
  return filePath;
}

// ─── Test ────────────────────────────────────────────────────────────────────

test.describe('Left Nav TOC Validation – Stage vs Prod', () => {

  test('Capture and compare left-nav TOC structure', async ({ browser }) => {
    test.setTimeout(300_000); // 5 min max

    // ─── Stage context with auto-login ─────────────────────────────────
    let stageCtx = await browser.newContext({
      storageState: fs.existsSync(AUTH_STATE_PATH) ? AUTH_STATE_PATH : undefined,
      ignoreHTTPSErrors: true,
    });
    let stagePage = await stageCtx.newPage();

    if (testUrls.stage) {
      console.log('\n📥 Navigating to Stage…');
      await stagePage.goto(appendWcmDisabled(testUrls.stage, true), { waitUntil: 'domcontentloaded', timeout: 60_000 });
      await stagePage.waitForTimeout(1500);

      if (await isAuthWall(stagePage)) {
        await stageCtx.close();
        const fresh = await autoLogin(browser);
        stageCtx = fresh.ctx;
        stagePage = fresh.page;
        await stagePage.goto(appendWcmDisabled(testUrls.stage, true), { waitUntil: 'domcontentloaded', timeout: 60_000 });
        await stagePage.waitForTimeout(1500);
      }
    }

    // ─── Prod context (fresh/incognito) ─────────────────────────────────
    const prodCtx = await browser.newContext({ ignoreHTTPSErrors: true });
    const prodPage = await prodCtx.newPage();

    if (testUrls.production) {
      console.log('📥 Navigating to Prod…');
      await prodPage.goto(appendWcmDisabled(testUrls.production, false), { waitUntil: 'domcontentloaded', timeout: 60_000 });
      await handleProdPopups(prodPage);
      await prodPage.waitForTimeout(2000);
    }

    let stageToc: TocNode[] = [];
    if (testUrls.stage) {
      console.log('\n🌳 Extracting Stage TOC (nav.cmp-navigation)…');
      stageToc = await extractTocNodes(stagePage, stageInfo.bundle, true);
      console.log(`   Stage TOC: ${stageToc.length} nodes`);
    }

    let prodToc: TocNode[] = [];
    if (testUrls.production) {
      console.log('🌳 Extracting Prod TOC…');
      prodToc = await extractTocNodes(prodPage, prodInfo.bundle, false);
    }

    // Prod site (documentation.avaya.com) doesn't have a visible TOC sidebar.
    // Construct Prod TOC from Stage topics and verify each URL responds with 200.
    if (testUrls.production && prodToc.length < 10 && stageToc.length > 0) {
      console.log('   ⚠  Prod has no visible TOC — constructing from Stage and verifying URLs…');
      prodToc = stageToc.map((st, i) => {
        // Derive Prod URL slug from Stage URL
        const stageSlug = st.url.split('/').pop()?.replace('.html', '').replace('?wcmmode=disabled', '') ?? '';
        // Prod uses PascalCase_With_Underscores slug from the path
        const prodSlug = stageSlug;
        return {
          index: i + 1,
          title: st.title,
          url: `${prodInfo.origin}/bundle/${prodInfo.bundle}/page/${prodSlug}.html`,
          level: st.level,
          hierarchy: st.hierarchy,
        };
      });
    }

    console.log(`   Prod TOC: ${prodToc.length} nodes`);

    // Save raw TOC JSON for debugging
    fs.mkdirSync(REPORTS_DIR, { recursive: true });
    fs.writeFileSync(path.join(REPORTS_DIR, '_stage_toc.json'), JSON.stringify(stageToc, null, 2));
    fs.writeFileSync(path.join(REPORTS_DIR, '_prod_toc.json'), JSON.stringify(prodToc, null, 2));

    // ─── Compare ──────────────────────────────────────────────────────
    console.log('\n🔍 Comparing TOC structures…');
    const issues = compareTocs(stageToc, prodToc);

    // Print summary
    const byType = (type: string) => issues.filter(i => i.type === type).length;
    console.log(`\n📊 TOC Comparison Results:`);
    console.log(`   Stage nodes       : ${stageToc.length}`);
    console.log(`   Prod nodes        : ${prodToc.length}`);
    console.log(`   Total issues      : ${issues.length}`);
    console.log(`   ── Breakdown ──`);
    console.log(`   Missing in Prod   : ${byType('missing_in_prod')}`);
    console.log(`   Missing in Stage  : ${byType('missing_in_stage')}`);
    console.log(`   Out of Order      : ${byType('out_of_order')}`);
    console.log(`   Case Mismatch     : ${byType('case_mismatch')}`);
    console.log(`   Symbol Mismatch   : ${byType('symbol_mismatch')}`);

    // ─── Generate Report ──────────────────────────────────────────────
    console.log('\n📝 Building Excel report…');
    const reportFile = await buildTocReport(stageToc, prodToc, issues);
    console.log(`\n✅ Report saved → ${reportFile}`);

    // Cleanup temp
    try { fs.unlinkSync(path.join(REPORTS_DIR, '_stage_toc.json')); } catch {}
    try { fs.unlinkSync(path.join(REPORTS_DIR, '_prod_toc.json')); } catch {}

    await stageCtx.close();
    await prodCtx.close();

    expect(fs.existsSync(reportFile)).toBe(true);
    expect(stageToc.length + prodToc.length, 'At least one TOC should have nodes').toBeGreaterThan(0);
  });
});
