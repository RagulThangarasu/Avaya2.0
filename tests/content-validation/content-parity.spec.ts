/**
 * content-parity.spec.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * Reads test-urls.json to derive Stage & Prod base URLs, then:
 *   1. Navigates to BOTH environments and scrapes every left-nav topic link
 *   2. Compares the full topic sets bidirectionally:
 *        • Topics in Stage  → must exist in Prod
 *        • Topics in Prod   → must exist in Stage
 *   3. For every MATCHED topic visits both pages and compares:
 *        title / tags / versions / last-updated / headings / paragraphs /
 *        tables / bold text / text length
 *   4. Writes a colour-coded Excel report to  reports/content-parity-<ts>.xlsx
 *
 * Run:   npx playwright test tests/content-validation/content-parity.spec.ts
 */

import { test, expect, Page, BrowserContext, Browser } from '@playwright/test';
import * as fs   from 'fs';
import * as path from 'path';
import * as ExcelJS from 'exceljs';
import { execSync } from 'child_process';

// ─── Config ──────────────────────────────────────────────────────────────────

const TEST_URLS_PATH  = path.resolve(__dirname, '../../config/test-urls.json');
const AUTH_STATE_PATH = path.resolve(__dirname, '../../auth-sessions/storage-state.json');
const REPORTS_DIR     = path.resolve(__dirname, '../../reports');

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

/** Derive the base origin + bundle from the sample URLs in test-urls.json */
function deriveBase(url: string): { origin: string; bundle: string; isStage: boolean } {
  const parsed = new URL(url);
  const isStage = parsed.hostname.includes('adobeaemcloud.com');
  // extract bundle name from path  e.g. "AdministeringAvayaAuraAdminPortal"
  const bundleMatch = url.match(/\/bundle\/([^/]+)\//);
  const bundle = bundleMatch ? bundleMatch[1] : '';
  return { origin: parsed.origin, bundle, isStage };
}

const stageInfo = deriveBase(testUrls.stage);
const prodInfo  = deriveBase(testUrls.production);

console.log(`Stage  → ${stageInfo.origin}  bundle: ${stageInfo.bundle}`);
console.log(`Prod   → ${prodInfo.origin}   bundle: ${prodInfo.bundle}`);

// ─── Types ───────────────────────────────────────────────────────────────────

interface NavTopic { title: string; url: string }

interface TopicData {
  url:         string;
  status:      'ok' | 'error' | 'missing';
  title:       string;
  lastUpdated: string;
  tags:        string[];
  versions:    string[];
  headings:    string[];
  paragraphs:  number;
  tables:      number;
  boldItems:   number;
  textLength:  number;
  // Deep structure
  structure: {
    olCount:       number;
    ulCount:       number;
    liCount:       number;
    emCount:       number;
    strongCount:   number;
    codeCount:     number;
    linkCount:     number;
    imgCount:      number;
    blockquoteCount: number;
    preCount:      number;
    dlCount:       number;
    figureCount:   number;
    // Ordered list items text for comparison
    olItems:       string[];
    // Unordered list items text
    ulItems:       string[];
    // Links with href and text
    links:         { text: string; href: string }[];
    // Heading hierarchy (tag + text)
    headingTree:   { level: number; text: string }[];
    // Inline code snippets
    codeSnippets:  string[];
    // Image alt texts
    imgAlts:       string[];
    // Table structures: row count per table
    tableRows:     number[];
    // Emphasis text
    emTexts:       string[];
    // Bold/strong text
    strongTexts:   string[];
    // E2E Header/Footer additions
    metaDescription: string;
    breadcrumbs:    string[];
    footerLinks:    string[];
    headerText:     string;
    footerText:     string;
  };
}

interface CompareResult {
  topic:       string;
  stageUrl:    string;
  prodUrl:     string;
  missingIn:   'stage' | 'prod' | '';
  issues:      string[];
  stage:       TopicData | null;
  prod:        TopicData | null;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Build the URL for a topic slug in each environment */
function stageTopicUrl(slug: string): string {
  return `${stageInfo.origin}/content/aemsites/en-us/bundle/${stageInfo.bundle}/${slug}.html?wcmmode=disabled`;
}
function prodTopicUrl(slug: string): string {
  return `${prodInfo.origin}/bundle/${prodInfo.bundle}/page/${slug}.html`;
}

/** Normalise a title for fuzzy matching: lowercase, collapse non-alphanumeric chars to single space */
function normalizeTitle(t: string): string {
  return t.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

/** Title → URL slug (matches previous Python approach) */
function toStageSlug(title: string): string {
  return title.toLowerCase()
    .replace(/[®™©]/g, '')
    .replace(/[^a-z0-9\s]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .trim();
}
function toProdSlug(title: string): string {
  return title
    .replace(/[®™©]/g, '')
    .replace(/[^a-zA-Z0-9\s_]/g, '')
    .replace(/\s+/g, '_')
    .trim();
}

const LOGIN_SCRIPT = path.resolve(__dirname, '../../run_login.ts');

/**
 * Run run_login.ts headlessly using credentials from .env, wait for it to finish,
 * then return a brand-new context loaded with the freshly-saved storage state.
 */
async function autoLogin(browser: Browser): Promise<{ ctx: any; page: Page }> {
  console.log('\n🔐  Session expired — running run_login.ts automatically…');
  try {
    execSync(`npx tsx "${LOGIN_SCRIPT}"`, {
      stdio: 'inherit',
      cwd: path.resolve(__dirname, '../..'),
      timeout: 180_000,   // 3 min max for SSO flows
    });
    console.log('✅  Login completed — reloading session.');
  } catch (e) {
    console.error('❌  Auto-login failed:', (e as Error).message);
    throw new Error('Auto-login failed. Check credentials in .env and try again.');
  }
  // Build a fresh context from the newly-saved storage state
  const ctx = await browser.newContext({
    storageState: AUTH_STATE_PATH,
    ignoreHTTPSErrors: true,
  });
  const page = await ctx.newPage();
  return { ctx, page };
}

/** 
 * Appends ?wcmmode=disabled to a URL if not already present.
 * This ensures we see the publish view in AEM Stage.
 */
function appendWcmDisabled(urlStr: string, isStage: boolean): string {
  if (!isStage || !urlStr || urlStr.includes('wcmmode=disabled')) return urlStr;
  try {
    // If it's a relative URL or has issues, the URL constructor might fail
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

/** Detect if current page is a login/auth wall */
async function isAuthWall(page: Page): Promise<boolean> {
  const txt = await page.evaluate(() => document.body?.innerText ?? '');
  return /sign in|log in|adobe experience manager.*welcome|authentication required/i.test(txt)
    && !/zDocsTOC|leftnav|sidebar|toc-list/i.test(await page.content());
}

/** Extract every topic link from the left navigation, filtering out UI chrome */
async function extractNavTopics(page: Page, startUrl: string, isStage: boolean): Promise<NavTopic[]> {
  await page.goto(appendWcmDisabled(startUrl, isStage), { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.waitForTimeout(1200);

  const bundle = isStage ? stageInfo.bundle : prodInfo.bundle;

  // UI chrome keywords to exclude
  const uiChromeKeywords = [
    'share this', 'cookie', 'preference', 'download', 'export', 'pdf', 'print',
    'language', 'english', 'deutsch', 'español', 'français', 'italiano',
    'português', 'русский', '日本語', '中文', 'polski', 'svenska', 'magyar',
    'nederlands', 'türkçe', 'עברית', 'العربية',
    'topic navigation', 'in this article', 'stay connected', 'was this page',
    'javascript', 'feedback', 'submit', 'search', 'menu', 'breadcrumb', 'filter',
    'table of contents', 'document navigation', 'collapse', 'toggle', 'filter',
  ];

  let topics: NavTopic[] = [];

  // For both Stage and Prod, try nav selectors that look for content links
  const navSelectors = [
    'nav a[href]',                         // generic nav
    'navigation a[href]',                  // semantic nav
    '[role="navigation"] a[href]',         // ARIA nav
    'aside a[href]',                       // sidebar
    '[class*="toc"] a[href]',              // TOC
    '[class*="nav"] a[href]',              // nav containers
    '.sidebar a[href]',
    '.leftnav a[href]',
  ];

  for (const sel of navSelectors) {
    try {
      const items: NavTopic[] = await page.$$eval(sel, (els: any[], params: any) =>
        els.map((el: any) => ({
          title: (el as HTMLAnchorElement).textContent?.trim() ?? '',
          url:   (el as HTMLAnchorElement).href ?? '',
        }))
        .map((t: any) => ({
          ...t,
          url: params.isStg ? (t.url.includes('wcmmode=disabled') ? t.url : (t.url.includes('?') ? t.url + '&wcmmode=disabled' : t.url + '?wcmmode=disabled')) : t.url
        }))
        .filter((t: any) => {
          if (t.title.length < 3) return false;
          if (!t.url.includes(params.bndl)) return false;
          // Exclude UI chrome
          const lowerTitle = t.title.toLowerCase();
          if (params.chrome.some((kw: string) => lowerTitle.includes(kw))) return false;
          // Also exclude very short titles (single char)
          if (t.title.length < 3 || t.title.match(/^[^a-z0-9]*$/i)) return false;
          return true;
        }),
        { bndl: bundle, chrome: uiChromeKeywords, isStg: isStage }
      );
      if (items.length > 1) {
        topics = items;
        console.log(`   [nav selector matched: "${sel}" → ${items.length} content topics]`);
        break;
      }
    } catch { /* try next */ }
  }

  // Deduplicate by normalized title
  const seen = new Map<string, NavTopic>();
  for (const t of topics) {
    const key = normalizeTitle(t.title);
    if (!seen.has(key)) seen.set(key, t);
  }

  return Array.from(seen.values());
}

/** Extract content metadata from a topic page — single evaluate for speed */
async function extractTopicData(page: Page, url: string, isStage: boolean): Promise<TopicData> {
  const data: TopicData = {
    url, status: 'ok', title: '', lastUpdated: '',
    tags: [], versions: [], headings: [], paragraphs: 0,
    tables: 0, boldItems: 0, textLength: 0,
    structure: {
      olCount: 0, ulCount: 0, liCount: 0, emCount: 0, strongCount: 0,
      codeCount: 0, linkCount: 0, imgCount: 0, blockquoteCount: 0,
      preCount: 0, dlCount: 0, figureCount: 0,
      olItems: [], ulItems: [], links: [], headingTree: [],
      codeSnippets: [], imgAlts: [], tableRows: [], emTexts: [], strongTexts: [],
      metaDescription: '', breadcrumbs: [], footerLinks: [], headerText: '', footerText: '',
    },
  };

  try {
    const resp = await page.goto(appendWcmDisabled(url, isStage), { waitUntil: 'domcontentloaded', timeout: 30_000 });
    if (!resp || resp.status() === 404) { data.status = 'missing'; return data; }

    // Single evaluate to extract everything at once (avoids multiple round-trips)
    const extracted = await page.evaluate(() => {
      const q = (sel: string) => document.querySelector(sel);
      const qa = (sel: string) => Array.from(document.querySelectorAll(sel));
      const txt = (el: Element | null) => el?.textContent?.trim() ?? '';

      const bodyEl = (q('.zDocsTopicBody') || q('main') || q('article') || q('.content') || document.body) as HTMLElement;
      const bodyText = bodyEl ? bodyEl.innerText?.trim() ?? '' : '';

      // Detect login wall
      if (/sign[\s-]in|login required/i.test(document.body.innerText) && !document.querySelector('.zDocsTopicBody')) {
        return { error: true };
      }

      // Deep structure extraction
      const contentRoot = bodyEl || document.body;
      const olItems: string[] = [];
      contentRoot.querySelectorAll('ol > li').forEach(li => {
        const t = li.textContent?.trim() ?? '';
        if (t.length > 2) olItems.push(t.slice(0, 120));
      });
      const ulItems: string[] = [];
      contentRoot.querySelectorAll('ul > li').forEach(li => {
        const t = li.textContent?.trim() ?? '';
        if (t.length > 2) ulItems.push(t.slice(0, 120));
      });
      const links: { text: string; href: string }[] = [];
      contentRoot.querySelectorAll('a[href]').forEach(a => {
        const t = a.textContent?.trim() ?? '';
        const href = (a as HTMLAnchorElement).getAttribute('href') ?? '';
        if (t.length > 1 && !href.startsWith('#') && !href.startsWith('javascript'))
          links.push({ text: t.slice(0, 80), href: href.slice(0, 150) });
      });
      const headingTree: { level: number; text: string }[] = [];
      contentRoot.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => {
        const t = h.textContent?.trim() ?? '';
        if (t) headingTree.push({ level: parseInt(h.tagName[1]), text: t });
      });
      const codeSnippets: string[] = [];
      contentRoot.querySelectorAll('code').forEach(c => {
        const t = c.textContent?.trim() ?? '';
        if (t.length > 1) codeSnippets.push(t.slice(0, 100));
      });
      const imgAlts: string[] = [];
      contentRoot.querySelectorAll('img').forEach(img => {
        imgAlts.push((img as HTMLImageElement).alt || '[no alt]');
      });
      const tableRows: number[] = [];
      contentRoot.querySelectorAll('table').forEach(tbl => {
        tableRows.push(tbl.querySelectorAll('tr').length);
      });
      const emTexts: string[] = [];
      contentRoot.querySelectorAll('em, i').forEach(e => {
        const t = e.textContent?.trim() ?? '';
        if (t.length > 1) emTexts.push(t.slice(0, 80));
      });
      const strongTexts: string[] = [];
      contentRoot.querySelectorAll('strong, b').forEach(e => {
        const t = e.textContent?.trim() ?? '';
        if (t.length > 1) strongTexts.push(t.slice(0, 80));
      });

      // E2E Header / Footer / Meta
      const metaDesc = (document.querySelector('meta[name="description"]') as HTMLMetaElement)?.content ?? '';
      const breadcrumbs = Array.from(document.querySelectorAll('.breadcrumbs a, .breadcrumb-item, .zDocsBreadcrumb a')).map(el => el.textContent?.trim() ?? '').filter(Boolean);
      const footerLinks = Array.from(document.querySelectorAll('footer a, .footer a')).map(el => (el as HTMLAnchorElement).href).filter(Boolean);
      const headerText = document.querySelector('header, .header')?.textContent?.trim().slice(0, 500) ?? '';
      const footerText = document.querySelector('footer, .footer')?.textContent?.trim().slice(0, 500) ?? '';

      return {
        error: false,
        title: txt(q('h1') || q('.zDocsTitle') || q('.page-title')),
        lastUpdated: txt(q('.zDocsTopicPageDate') || q('[class*="date"]') || q('time')),
        tags: Array.from(new Set(qa('.zDocsLabel, .zDocsTags .label, [class*="tag"]:not(script)').map(e => txt(e)).filter(Boolean))),
        versions: Array.from(new Set(qa('.currentPublicationTag, .zDocsVersion, [class*="version"]').map(e => txt(e)).filter(Boolean))),
        headings: qa('h2, h3, h4, h5, h6').map(e => txt(e)).filter(Boolean),
        paragraphs: qa('p').filter(e => (e.textContent?.trim().length ?? 0) > 20).length,
        tables: qa('table').length,
        boldItems: qa('b, strong').filter(e => (e.textContent?.trim().length ?? 0) > 0).length,
        textLength: bodyText.length,
        structure: {
          olCount: contentRoot.querySelectorAll('ol').length,
          ulCount: contentRoot.querySelectorAll('ul').length,
          liCount: contentRoot.querySelectorAll('li').length,
          emCount: contentRoot.querySelectorAll('em, i').length,
          strongCount: contentRoot.querySelectorAll('strong, b').length,
          codeCount: contentRoot.querySelectorAll('code').length,
          linkCount: links.length,
          imgCount: contentRoot.querySelectorAll('img').length,
          blockquoteCount: contentRoot.querySelectorAll('blockquote').length,
          preCount: contentRoot.querySelectorAll('pre').length,
          dlCount: contentRoot.querySelectorAll('dl').length,
          figureCount: contentRoot.querySelectorAll('figure').length,
          olItems, ulItems, links, headingTree, codeSnippets, imgAlts,
          tableRows, emTexts, strongTexts,
          metaDescription: metaDesc, breadcrumbs, footerLinks, headerText, footerText,
        },
      };
    }).catch(() => ({ error: true }));

    if ((extracted as any).error) { data.status = 'error'; return data; }

    const ex = extracted as any;
    data.title = ex.title;
    data.lastUpdated = ex.lastUpdated;
    data.tags = ex.tags;
    data.versions = ex.versions;
    data.headings = ex.headings;
    data.paragraphs = ex.paragraphs;
    data.tables = ex.tables;
    data.boldItems = ex.boldItems;
    data.textLength = ex.textLength;
    data.structure = ex.structure || data.structure;

  } catch (e) {
    data.status = 'error';
  }

  return data;
}

/** Compare two TopicData objects and return human-readable issues */
function compareData(s: TopicData, p: TopicData): string[] {
  const issues: string[] = [];

  if (s.title && p.title && s.title.toLowerCase() !== p.title.toLowerCase())
    issues.push(`Title mismatch — Stage: "${s.title}" | Prod: "${p.title}"`);

  const stageTags = new Set(s.tags);
  const prodTags  = new Set(p.tags);
  const missingInProd  = Array.from(stageTags).filter(t => !prodTags.has(t));
  const extraInProd    = Array.from(prodTags).filter(t => !stageTags.has(t));
  if (missingInProd.length) issues.push(`Tags missing in Prod: ${missingInProd.join(', ')}`);
  if (extraInProd.length)   issues.push(`Extra tags in Prod: ${extraInProd.join(', ')}`);

  const stageVer = new Set(s.versions);
  const prodVer  = new Set(p.versions);
  if (JSON.stringify(Array.from(stageVer).sort()) !== JSON.stringify(Array.from(prodVer).sort()))
    issues.push(`Version mismatch — Stage: [${Array.from(stageVer).join(', ')}] | Prod: [${Array.from(prodVer).join(', ')}]`);

  if (s.headings.length !== p.headings.length)
    issues.push(`Heading count — Stage: ${s.headings.length} | Prod: ${p.headings.length}`);

  if (Math.abs(s.paragraphs - p.paragraphs) > 3)
    issues.push(`Paragraph count — Stage: ${s.paragraphs} | Prod: ${p.paragraphs}`);

  if (s.tables !== p.tables)
    issues.push(`Table count — Stage: ${s.tables} | Prod: ${p.tables}`);

  const lengthDiff = s.textLength > 0
    ? Math.abs(s.textLength - p.textLength) / s.textLength * 100 : 0;
  if (lengthDiff > 15)
    issues.push(`Content length diff ${lengthDiff.toFixed(1)}% — Stage: ${s.textLength} chars | Prod: ${p.textLength} chars`);

  // ── Deep Structure Comparison ────────────────────────────────────────────────
  const ss = s.structure;
  const ps = p.structure;

  if (ss.olCount !== ps.olCount)
    issues.push(`OL (ordered list) count — Stage: ${ss.olCount} | Prod: ${ps.olCount}`);
  if (ss.ulCount !== ps.ulCount)
    issues.push(`UL (unordered list) count — Stage: ${ss.ulCount} | Prod: ${ps.ulCount}`);
  if (Math.abs(ss.liCount - ps.liCount) > 2)
    issues.push(`LI (list item) count — Stage: ${ss.liCount} | Prod: ${ps.liCount}`);
  if (ss.emCount !== ps.emCount)
    issues.push(`EM/I (emphasis) count — Stage: ${ss.emCount} | Prod: ${ps.emCount}`);
  if (ss.strongCount !== ps.strongCount)
    issues.push(`STRONG/B (bold) count — Stage: ${ss.strongCount} | Prod: ${ps.strongCount}`);
  if (ss.codeCount !== ps.codeCount)
    issues.push(`CODE tag count — Stage: ${ss.codeCount} | Prod: ${ps.codeCount}`);
  if (Math.abs(ss.linkCount - ps.linkCount) > 2)
    issues.push(`Link count — Stage: ${ss.linkCount} | Prod: ${ps.linkCount}`);
  if (ss.imgCount !== ps.imgCount)
    issues.push(`IMG count — Stage: ${ss.imgCount} | Prod: ${ps.imgCount}`);
  if (ss.blockquoteCount !== ps.blockquoteCount)
    issues.push(`Blockquote count — Stage: ${ss.blockquoteCount} | Prod: ${ps.blockquoteCount}`);
  if (ss.preCount !== ps.preCount)
    issues.push(`PRE (preformatted) count — Stage: ${ss.preCount} | Prod: ${ps.preCount}`);
  if (ss.dlCount !== ps.dlCount)
    issues.push(`DL (definition list) count — Stage: ${ss.dlCount} | Prod: ${ps.dlCount}`);
  if (ss.figureCount !== ps.figureCount)
    issues.push(`FIGURE count — Stage: ${ss.figureCount} | Prod: ${ps.figureCount}`);

  // Compare ordered list items content
  const maxOl = Math.max(ss.olItems.length, ps.olItems.length);
  for (let i = 0; i < maxOl && i < 30; i++) {
    const sItem = ss.olItems[i] ?? '[MISSING]';
    const pItem = ps.olItems[i] ?? '[MISSING]';
    if (sItem !== pItem && sItem !== '[MISSING]' && pItem !== '[MISSING]') {
      // Only flag if notably different
      if (sItem.toLowerCase().replace(/\s+/g,' ') !== pItem.toLowerCase().replace(/\s+/g,' '))
        issues.push(`OL item #${i+1} mismatch — Stage: "${sItem.slice(0,60)}" | Prod: "${pItem.slice(0,60)}"`);
    } else if (sItem === '[MISSING]') {
      issues.push(`OL item #${i+1} missing in Stage (Prod: "${pItem.slice(0,60)}")`);
    } else if (pItem === '[MISSING]') {
      issues.push(`OL item #${i+1} missing in Prod (Stage: "${sItem.slice(0,60)}")`);
    }
  }

  // Compare heading hierarchy
  const maxHdg = Math.max(ss.headingTree.length, ps.headingTree.length);
  for (let i = 0; i < maxHdg && i < 30; i++) {
    const sh = ss.headingTree[i];
    const ph = ps.headingTree[i];
    if (sh && ph) {
      if (sh.level !== ph.level)
        issues.push(`Heading #${i+1} level mismatch — Stage: h${sh.level} | Prod: h${ph.level}`);
      if (sh.text.toLowerCase() !== ph.text.toLowerCase())
        issues.push(`Heading #${i+1} text mismatch — Stage: "${sh.text.slice(0,50)}" | Prod: "${ph.text.slice(0,50)}"`);
    } else if (!sh) {
      issues.push(`Heading #${i+1} missing in Stage (Prod: h${ph!.level} "${ph!.text.slice(0,50)}")`);
    } else {
      issues.push(`Heading #${i+1} missing in Prod (Stage: h${sh.level} "${sh.text.slice(0,50)}")`);
    }
  }

  // Compare table row counts
  const maxTbl = Math.max(ss.tableRows.length, ps.tableRows.length);
  for (let i = 0; i < maxTbl; i++) {
    const sr = ss.tableRows[i] ?? 0;
    const pr = ps.tableRows[i] ?? 0;
    if (sr !== pr)
      issues.push(`Table #${i+1} row count — Stage: ${sr} | Prod: ${pr}`);
  }

  // Compare images alt text
  if (ss.imgAlts.length !== ps.imgAlts.length)
    issues.push(`Image alt text count — Stage: ${ss.imgAlts.length} | Prod: ${ps.imgAlts.length}`);
  for (let i = 0; i < Math.min(ss.imgAlts.length, ps.imgAlts.length, 20); i++) {
    if (ss.imgAlts[i] !== ps.imgAlts[i])
      issues.push(`Image #${i+1} alt mismatch — Stage: "${ss.imgAlts[i]?.slice(0,50)}" | Prod: "${ps.imgAlts[i]?.slice(0,50)}"`);
  }

  // Compare emphasis text content (first few)
  const missingEm = ss.emTexts.filter(t => !ps.emTexts.includes(t));
  if (missingEm.length > 0 && missingEm.length <= 10)
    issues.push(`Emphasis text in Stage but not Prod: ${missingEm.slice(0,5).map(t => `"${t.slice(0,40)}"`).join(', ')}`);
  const extraEm = ps.emTexts.filter(t => !ss.emTexts.includes(t));
  if (extraEm.length > 0 && extraEm.length <= 10)
    issues.push(`Emphasis text in Prod but not Stage: ${extraEm.slice(0,5).map(t => `"${t.slice(0,40)}"`).join(', ')}`);

  // Compare code snippets
  const missingCode = ss.codeSnippets.filter(t => !ps.codeSnippets.includes(t));
  if (missingCode.length > 0 && missingCode.length <= 10)
    issues.push(`Code snippets in Stage but not Prod: ${missingCode.slice(0,5).map(t => `"${t.slice(0,40)}"`).join(', ')}`);
  const extraCode = ps.codeSnippets.filter(t => !ss.codeSnippets.includes(t));
  if (extraCode.length > 0 && extraCode.length <= 10)
    issues.push(`Code snippets in Prod but not Stage: ${extraCode.slice(0,5).map(t => `"${t.slice(0,40)}"`).join(', ')}`);

  // E2E Global Component Comparison
  if (ss.metaDescription !== ps.metaDescription)
    issues.push(`Meta Description mismatch — Stage: "${ss.metaDescription.slice(0,60)}" | Prod: "${ps.metaDescription.slice(0,60)}"`);
  
  if (ss.breadcrumbs.length !== ps.breadcrumbs.length)
    issues.push(`Breadcrumb count — Stage: ${ss.breadcrumbs.length} | Prod: ${ps.breadcrumbs.length}`);
  else if (JSON.stringify(ss.breadcrumbs) !== JSON.stringify(ps.breadcrumbs))
    issues.push(`Breadcrumb path mismatch — Stage: [${ss.breadcrumbs.join(' > ')}] | Prod: [${ps.breadcrumbs.join(' > ')}]`);

  if (Math.abs(ss.footerLinks.length - ps.footerLinks.length) > 2)
    issues.push(`Footer link count mismatch — Stage: ${ss.footerLinks.length} | Prod: ${ps.footerLinks.length}`);

  if (ss.headerText.length > 50 && ps.headerText.length > 50) {
     const headerDiff = Math.abs(ss.headerText.length - ps.headerText.length);
     if (headerDiff > 50) issues.push(`Global Header text differs significantly (${headerDiff} chars difference)`);
  }

  if (ss.footerText.length > 50 && ps.footerText.length > 50) {
     const footerDiff = Math.abs(ss.footerText.length - ps.footerText.length);
     if (footerDiff > 50) issues.push(`Global Footer text differs significantly (${footerDiff} chars difference)`);
  }

  return issues;
}

// ─── Report Builder ─────────────────────────────────────────────────────────

function calculateMatchPercent(issues: string[], s: TopicData | null, p: TopicData | null): string {
  if (!s || !p) return '0%';
  const totalWeight = 100;
  let penalty = 0;
  
  issues.forEach(issue => {
    if (issue.includes('Heading')) penalty += 15;
    else if (issue.includes('Table')) penalty += 20;
    else if (issue.includes('List')) penalty += 10;
    else if (issue.includes('Image')) penalty += 5;
    else if (issue.includes('Meta')) penalty += 5;
    else penalty += 5;
  });

  const score = Math.max(0, totalWeight - penalty);
  return `${score}%`;
}

function getUrlPath(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.pathname;
  } catch (e) {
    return url;
  }
}

async function buildReport(results: CompareResult[]) {
  fs.mkdirSync(REPORTS_DIR, { recursive: true });
  const filename = process.env.REPORT_FILENAME || 'content-parity.xlsx';
  const file = path.join(REPORTS_DIR, filename);

  const wb = new ExcelJS.Workbook();
  wb.creator = 'Content Parity Validator';
  wb.created = new Date();

  // ── colour palette ──────────────────────────────────────────────────────────
  const C = {
    hdrBg:     '1F4E78', hdrFg: 'FFFFFF',
    critical:  'FF0000', critFg: 'FFFFFF',
    warn:      'FFC000', warnFg: '000000',
    ok:        '00B050', okFg:   'FFFFFF',
    altRow:    'EFF3FF',
  };
  const hdrFont  = { bold: true, color: { argb: C.hdrFg }, size: 11 };
  const hdrFill  = (c = C.hdrBg): ExcelJS.Fill => ({ type: 'pattern', pattern: 'solid', fgColor: { argb: c } });
  const border = {
    top: { style: 'thin' as const }, bottom: { style: 'thin' as const },
    left: { style: 'thin' as const }, right: { style: 'thin' as const },
  } as ExcelJS.Borders;

  // helper: apply header style to row 1
  function styleHeader(ws: ExcelJS.Worksheet) {
    ws.getRow(1).eachCell(cell => {
      cell.fill  = hdrFill();
      cell.font  = hdrFont;
      cell.border = border;
      cell.alignment = { horizontal: 'center', vertical: 'middle', wrapText: true };
    });
    ws.getRow(1).height = 30;
  }

  const missingInProd  = results.filter(r => r.missingIn === 'prod');
  const missingInStage = results.filter(r => r.missingIn === 'stage');
  const withIssues     = results.filter(r => !r.missingIn && r.issues.length > 0);
  const clean          = results.filter(r => !r.missingIn && r.issues.length === 0);

  // ── Sheet 1 – Summary ───────────────────────────────────────────────────────
  {
    const ws = wb.addWorksheet('Summary');
    ws.mergeCells('A1:D1');
    const titleCell = ws.getCell('A1');
    titleCell.value = 'Stage ↔ Prod Content Parity Report';
    titleCell.font  = { bold: true, size: 14, color: { argb: C.hdrBg } };
    titleCell.alignment = { horizontal: 'center' };

    ws.mergeCells('A2:D2');
    ws.getCell('A2').value = `Generated: ${new Date().toLocaleString()}  |  Stage: ${stageInfo.origin}  |  Prod: ${prodInfo.origin}`;
    ws.getCell('A2').alignment = { horizontal: 'center' };

    ws.addRow([]);

    ws.addRow(['Metric', 'Count', 'Status']);
    styleHeader(ws);

    const avgMatch = results.length > 0 
      ? (results.reduce((acc, r) => acc + parseInt(calculateMatchPercent(r.issues, r.stage, r.prod)), 0) / results.length).toFixed(1) + '%'
      : '0%';

    const rows: [string, any, string][] = [
      ['Total topics analyzed',          results.length, ''],
      ['Average Content Match',          avgMatch, ''],
      ['Matched topics (Clean)',         clean.length,          '✓ OK'],
      ['Topics with issues',             withIssues.length,     withIssues.length     ? '⚠ REVIEW'          : '✓ OK'],
      ['Missing in Prod (Stage only)',   missingInProd.length,  missingInProd.length  ? '⚠ ACTION REQUIRED' : '✓ OK'],
      ['Missing in Stage (Prod only)',   missingInStage.length, missingInStage.length ? '⚠ ACTION REQUIRED' : '✓ OK'],
    ];

    rows.forEach(([metric, count, status]) => {
      const row = ws.addRow([metric, count, status]);
      row.eachCell(c => { c.border = border; c.alignment = { vertical: 'middle' }; });
      if (status.startsWith('⚠')) {
        row.getCell(3).fill = hdrFill(C.warn);
        row.getCell(3).font = { color: { argb: C.warnFg } };
      } else if (status.startsWith('✓')) {
        row.getCell(3).fill = hdrFill(C.ok);
        row.getCell(3).font = { color: { argb: C.okFg }, bold: true };
      }
    });

    ws.getColumn(1).width = 36;
    ws.getColumn(2).width = 10;
    ws.getColumn(3).width = 22;
  }

  // ── Sheet 2 – Main Analysis ────────────────────────────────────────────────
  {
    const ws = wb.addWorksheet('Main Analysis');
    ws.addRow([
      '#', 'Topic', 'Match %', 'Stage URL Path', 'Prod URL Path', 'Status',
      'Issue Count', 'Issues',
      'Hdgs S/P', 'Paras S/P', 'Tables S/P', 'Lists S/P', 'Imgs S/P',
      'Stage Updated', 'Prod Updated'
    ]);
    styleHeader(ws);

    let idx = 0;
    for (const r of results) {
      idx++;
      const s = r.stage;
      const p = r.prod;
      const status = r.missingIn ? `Missing in ${r.missingIn}` : (r.issues.length ? 'Issues Found' : '✓ Clean');
      const matchPct = r.missingIn ? '0%' : calculateMatchPercent(r.issues, s, p);

      const row = ws.addRow([
        idx,
        r.topic,
        matchPct,
        getUrlPath(r.stageUrl),
        getUrlPath(r.prodUrl),
        status,
        r.issues.length,
        r.issues.join(' | '),
        s && p ? `${s.headings.length}/${p.headings.length}` : '-',
        s && p ? `${s.paragraphs}/${p.paragraphs}`   : '-',
        s && p ? `${s.tables}/${p.tables}` : '-',
        s && p ? `${s.structure.olCount + s.structure.ulCount}/${p.structure.olCount + p.structure.ulCount}`   : '-',
        s && p ? `${s.structure.imgCount}/${p.structure.imgCount}` : '-',
        s?.lastUpdated || '-',
        p?.lastUpdated || '-'
      ]);

      // colour by status
      const statusCell = row.getCell(6);
      if (r.missingIn) {
        statusCell.fill = hdrFill(C.critical);
        statusCell.font = { bold: true, color: { argb: C.critFg } };
      } else if (r.issues.length) {
        statusCell.fill = hdrFill(C.warn);
      } else {
        statusCell.fill = hdrFill(C.ok);
        statusCell.font = { color: { argb: C.okFg } };
      }

      // alternating row colour
      if (idx % 2 === 0) {
        row.eachCell((c, n) => {
          if (n !== 6 && !c.fill) c.fill = hdrFill(C.altRow);
        });
      }

      row.eachCell(c => { c.border = border; c.alignment = { vertical: 'top', wrapText: true }; });
    }

    ws.getColumn(1).width  = 5;
    ws.getColumn(2).width  = 40;
    ws.getColumn(3).width  = 10;
    ws.getColumn(4).width  = 50;
    ws.getColumn(5).width  = 50;
    ws.getColumn(6).width  = 20;
    ws.getColumn(7).width  = 12;
    ws.getColumn(8).width  = 60;
    ws.views = [{ state: 'frozen', ySplit: 1 }];
  }

  // ── Sheet 3 – URL Issues ───────────────────────────────────────────────────
  {
    const ws = wb.addWorksheet('URL Issues');
    ws.addRow(['#', 'Topic', 'Stage URL Path', 'Prod URL Path', 'URL Match %', 'Issue Description']);
    styleHeader(ws);

    let idx = 0;
    for (const r of results) {
      const stagePath = getUrlPath(r.stageUrl);
      const prodPath  = getUrlPath(r.prodUrl);
      
      // Heuristic for URL match: do the paths end similarly?
      const sEnd = stagePath.split('/').pop() || '';
      const pEnd = prodPath.split('/').pop()  || '';
      
      let urlMatch = '100%';
      let urlIssue = 'None';

      if (sEnd !== pEnd) {
        urlMatch = '50%';
        urlIssue = `Path mismatch: "${sEnd}" vs "${pEnd}"`;
      }
      if (r.missingIn) {
        urlMatch = '0%';
        urlIssue = `Topic missing in ${r.missingIn}`;
      }

      if (urlMatch !== '100%') {
        idx++;
        const row = ws.addRow([idx, r.topic, stagePath, prodPath, urlMatch, urlIssue]);
        row.getCell(5).font = { bold: true, color: { argb: urlMatch === '0%' ? C.critical : C.warn } };
        row.eachCell(c => { c.border = border; c.alignment = { vertical: 'top', wrapText: true }; });
      }
    }
    ws.getColumn(1).width = 5;
    ws.getColumn(2).width = 35;
    ws.getColumn(3).width = 45;
    ws.getColumn(4).width = 45;
    ws.getColumn(5).width = 15;
    ws.getColumn(6).width = 50;
    ws.views = [{ state: 'frozen', ySplit: 1 }];
  }

  // ── Sheet 4 – Missing in Prod ────────────────────────────────────────────────
  {
    const ws = wb.addWorksheet('Missing in Prod');
    ws.addRow(['#', 'Topic Name', 'Stage URL', 'Action Required']);
    styleHeader(ws);
    missingInProd.forEach((r, i) => {
      const row = ws.addRow([i + 1, r.topic, r.stageUrl, 'Publish to Prod OR remove from Stage nav']);
      row.eachCell(c => { c.border = border; c.alignment = { wrapText: true, vertical: 'top' }; });
      row.getCell(4).fill = hdrFill(C.critical);
      row.getCell(4).font = { color: { argb: C.critFg }, bold: true };
    });
    ws.getColumn(1).width = 5;
    ws.getColumn(2).width = 50;
    ws.getColumn(3).width = 70;
    ws.getColumn(4).width = 40;
    ws.views = [{ state: 'frozen', ySplit: 1 }];
  }

  // ── Sheet 4 – Missing in Stage ───────────────────────────────────────────────
  {
    const ws = wb.addWorksheet('Missing in Stage');
    ws.addRow(['#', 'Topic Name', 'Prod URL', 'Action Required']);
    styleHeader(ws);
    missingInStage.forEach((r, i) => {
      const row = ws.addRow([i + 1, r.topic, r.prodUrl, 'Add to Stage nav OR remove from Prod']);
      row.eachCell(c => { c.border = border; c.alignment = { wrapText: true, vertical: 'top' }; });
      row.getCell(4).fill = hdrFill(C.critical);
      row.getCell(4).font = { color: { argb: C.critFg }, bold: true };
    });
    ws.getColumn(1).width = 5;
    ws.getColumn(2).width = 50;
    ws.getColumn(3).width = 70;
    ws.getColumn(4).width = 40;
    ws.views = [{ state: 'frozen', ySplit: 1 }];
  }

  // ── Sheet 5 – Content Issues ─────────────────────────────────────────────────
  {
    const ws = wb.addWorksheet('Content Issues');
    ws.addRow(['#', 'Topic', 'Issue Type', 'Stage Value', 'Prod Value', 'Detail']);
    styleHeader(ws);
    let n = 0;
    for (const r of withIssues) {
      for (const issue of r.issues) {
        n++;
        // parse issue string to extract type / values
        const row = ws.addRow([n, r.topic, issueType(issue), '', '', issue]);
        row.eachCell(c => { c.border = border; c.alignment = { wrapText: true, vertical: 'top' }; });
        // severity colouring on type cell
        const severity = issueSeverity(issue);
        const typeCell = row.getCell(3);
        if (severity === 'HIGH')   { typeCell.fill = hdrFill(C.critical); typeCell.font = { bold: true, color: { argb: C.critFg } }; }
        if (severity === 'MEDIUM') { typeCell.fill = hdrFill(C.warn); }
      }
    }
    ws.getColumn(1).width = 5;
    ws.getColumn(2).width = 45;
    ws.getColumn(3).width = 25;
    ws.getColumn(4).width = 20;
    ws.getColumn(5).width = 20;
    ws.getColumn(6).width = 70;
    ws.views = [{ state: 'frozen', ySplit: 1 }];
  }

  // ── Sheet 6 – Page Structure Comparison ──────────────────────────────────────
  {
    const ws = wb.addWorksheet('Page Structure');
    ws.addRow([
      '#', 'Topic',
      'OL (S)', 'OL (P)', 'UL (S)', 'UL (P)', 'LI (S)', 'LI (P)',
      'EM (S)', 'EM (P)', 'Strong (S)', 'Strong (P)',
      'Code (S)', 'Code (P)', 'Links (S)', 'Links (P)',
      'IMG (S)', 'IMG (P)', 'Blockquote (S)', 'Blockquote (P)',
      'PRE (S)', 'PRE (P)', 'DL (S)', 'DL (P)', 'Figure (S)', 'Figure (P)',
      'Tables (S)', 'Tables (P)', 'Headings (S)', 'Headings (P)',
      'Structure Match',
    ]);
    styleHeader(ws);

    const matched = results.filter(r => !r.missingIn && r.stage && r.prod);
    let idx = 0;
    for (const r of matched) {
      idx++;
      const ss = r.stage!.structure;
      const ps = r.prod!.structure;

      const structMatch = (
        ss.olCount === ps.olCount && ss.ulCount === ps.ulCount &&
        ss.liCount === ps.liCount && ss.emCount === ps.emCount &&
        ss.strongCount === ps.strongCount && ss.codeCount === ps.codeCount &&
        Math.abs(ss.linkCount - ps.linkCount) <= 2 && ss.imgCount === ps.imgCount &&
        ss.blockquoteCount === ps.blockquoteCount && ss.preCount === ps.preCount &&
        ss.dlCount === ps.dlCount && ss.figureCount === ps.figureCount
      );

      const row = ws.addRow([
        idx, r.topic,
        ss.olCount, ps.olCount, ss.ulCount, ps.ulCount, ss.liCount, ps.liCount,
        ss.emCount, ps.emCount, ss.strongCount, ps.strongCount,
        ss.codeCount, ps.codeCount, ss.linkCount, ps.linkCount,
        ss.imgCount, ps.imgCount, ss.blockquoteCount, ps.blockquoteCount,
        ss.preCount, ps.preCount, ss.dlCount, ps.dlCount, ss.figureCount, ps.figureCount,
        ss.tableRows.length, ps.tableRows.length,
        ss.headingTree.length, ps.headingTree.length,
        structMatch ? '✓ MATCH' : '✗ MISMATCH',
      ]);

      row.eachCell(c => { c.border = border; c.alignment = { vertical: 'middle', horizontal: 'center' }; });
      row.getCell(2).alignment = { vertical: 'middle', wrapText: true };

      const statusCell = row.getCell(31);
      if (structMatch) {
        statusCell.fill = hdrFill(C.ok);
        statusCell.font = { color: { argb: C.okFg }, bold: true };
      } else {
        statusCell.fill = hdrFill(C.warn);
        statusCell.font = { color: { argb: C.warnFg }, bold: true };
      }

      // Highlight mismatched cells in yellow
      const pairs = [[3,4],[5,6],[7,8],[9,10],[11,12],[13,14],[15,16],[17,18],[19,20],[21,22],[23,24],[25,26],[27,28],[29,30]];
      for (const [a, b] of pairs) {
        const va = row.getCell(a).value as number;
        const vb = row.getCell(b).value as number;
        if (va !== vb) {
          row.getCell(a).fill = hdrFill('FFDDDD');
          row.getCell(b).fill = hdrFill('FFDDDD');
        }
      }

      if (idx % 2 === 0) {
        row.eachCell((c, n) => {
          if (!c.fill || (c.fill as any).fgColor?.argb === undefined)
            c.fill = hdrFill(C.altRow);
        });
      }
    }

    ws.getColumn(1).width = 5;
    ws.getColumn(2).width = 40;
    for (let i = 3; i <= 30; i++) ws.getColumn(i).width = 9;
    ws.getColumn(31).width = 15;
    ws.views = [{ state: 'frozen', ySplit: 1, xSplit: 2 }];
  }

  await wb.xlsx.writeFile(file);
  return file;
}

function issueType(msg: string): string {
  if (msg.startsWith('Title'))          return 'TITLE_MISMATCH';
  if (msg.startsWith('Tags missing'))   return 'MISSING_TAGS_IN_PROD';
  if (msg.startsWith('Extra tags'))     return 'EXTRA_TAGS_IN_PROD';
  if (msg.startsWith('Version'))        return 'VERSION_MISMATCH';
  if (msg.startsWith('Heading #'))      return 'HEADING_STRUCTURE';
  if (msg.startsWith('Heading'))        return 'HEADING_COUNT_DIFF';
  if (msg.startsWith('Paragraph'))      return 'PARAGRAPH_COUNT_DIFF';
  if (msg.startsWith('Table #'))        return 'TABLE_STRUCTURE';
  if (msg.startsWith('Table'))          return 'TABLE_COUNT_DIFF';
  if (msg.startsWith('Content length')) return 'CONTENT_LENGTH_DIFF';
  if (msg.startsWith('OL '))            return 'OL_STRUCTURE';
  if (msg.startsWith('UL '))            return 'UL_STRUCTURE';
  if (msg.startsWith('LI '))            return 'LIST_ITEM_DIFF';
  if (msg.startsWith('EM/'))            return 'EMPHASIS_DIFF';
  if (msg.startsWith('STRONG/'))        return 'BOLD_DIFF';
  if (msg.startsWith('CODE'))           return 'CODE_DIFF';
  if (msg.startsWith('Link'))           return 'LINK_DIFF';
  if (msg.startsWith('IMG') || msg.startsWith('Image'))  return 'IMAGE_DIFF';
  if (msg.startsWith('Blockquote'))     return 'BLOCKQUOTE_DIFF';
  if (msg.startsWith('PRE'))            return 'PRE_DIFF';
  if (msg.startsWith('DL'))             return 'DEF_LIST_DIFF';
  if (msg.startsWith('FIGURE'))         return 'FIGURE_DIFF';
  if (msg.startsWith('Emphasis text'))  return 'EMPHASIS_CONTENT';
  if (msg.startsWith('Code snippets'))  return 'CODE_CONTENT';
  if (msg.startsWith('OL item'))        return 'OL_ITEM_CONTENT';
  return 'OTHER';
}
function issueSeverity(msg: string): 'HIGH' | 'MEDIUM' | 'LOW' {
  if (msg.startsWith('Title') || msg.startsWith('Version')) return 'HIGH';
  if (msg.startsWith('Heading #') && msg.includes('text mismatch')) return 'HIGH';
  if (msg.startsWith('OL item') && msg.includes('mismatch')) return 'HIGH';
  if (msg.startsWith('Content length') || msg.startsWith('Table')) return 'MEDIUM';
  if (msg.includes('count')) return 'MEDIUM';
  return 'LOW';
}

// ─── Test ─────────────────────────────────────────────────────────────────────

test.describe('Content Parity – Stage ↔ Prod', () => {

  let stagePage:   Page;
  let prodPage:    Page;
  let stageTopics: NavTopic[];
  let prodTopics:  NavTopic[];
  let results:     CompareResult[];

  // ── Step 1: Extract nav topics from both environments ───────────────────────
  test('Step 1 – Extract nav topics from Stage & Prod', async ({ browser }) => {

    // ── Stage context ── load saved auth session if present ──────────────────
    let stageCtx = await browser.newContext({
      storageState: fs.existsSync(AUTH_STATE_PATH) ? AUTH_STATE_PATH : undefined,
      ignoreHTTPSErrors: true,
    });
    stagePage = await stageCtx.newPage();

    // Prod is public – no auth needed
    const prodCtx = await browser.newContext({ ignoreHTTPSErrors: true });
    prodPage = await prodCtx.newPage();

    // ── Check Stage auth and navigate both in parallel ────────────────────
    console.log('\n📥 Extracting nav topics…');
    await stagePage.goto(testUrls.stage, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await stagePage.waitForTimeout(1200);

    if (await isAuthWall(stagePage)) {
      // Session expired — run the login script automatically, then retry
      await stageCtx.close();
      const fresh = await autoLogin(browser);
      stageCtx  = fresh.ctx;
      stagePage = fresh.page;
    }

    // Extract both Stage and Prod topics in parallel
    [stageTopics, prodTopics] = await Promise.all([
      extractNavTopics(stagePage, testUrls.stage, true),
      extractNavTopics(prodPage, testUrls.production, false),
    ]);

    console.log(`   Found ${stageTopics.length} Stage topics, ${prodTopics.length} Prod topics`);

    // Fallback: if Prod nav extraction failed, construct Prod URLs from Stage topics
    if (prodTopics.length === 0 && stageTopics.length > 0) {
      console.log('\n⚠  Prod nav extraction returned 0 topics. Constructing Prod URLs from Stage topics…');
      prodTopics = stageTopics.map(st => ({
        title: st.title,
        url: prodTopicUrl(toProdSlug(st.title)),
      }));
      console.log(`   Constructed ${prodTopics.length} Prod topic URLs`);
    }

    expect(stageTopics.length, 'Stage should have topics (auth must succeed)').toBeGreaterThan(0);
    expect(prodTopics.length,  'Prod should have topic URLs').toBeGreaterThan(0);

    // Save for subsequent steps
    fs.mkdirSync(REPORTS_DIR, { recursive: true });
    fs.writeFileSync(
      path.join(REPORTS_DIR, '_stage_topics.json'),
      JSON.stringify(stageTopics, null, 2)
    );
    fs.writeFileSync(
      path.join(REPORTS_DIR, '_prod_topics.json'),
      JSON.stringify(prodTopics, null, 2)
    );
  });

  // ── Step 2: Bidirectional topic existence check ─────────────────────────────
  test('Step 2 – Validate bidirectional topic parity', async () => {

    // Load from disk (tolerates separate runs)
    stageTopics = JSON.parse(fs.readFileSync(path.join(REPORTS_DIR, '_stage_topics.json'), 'utf-8'));
    prodTopics  = JSON.parse(fs.readFileSync(path.join(REPORTS_DIR, '_prod_topics.json'),  'utf-8'));

    const stageTitleMap = new Map(stageTopics.map(t => [normalizeTitle(t.title), t]));
    const prodTitleMap  = new Map(prodTopics.map(t  => [normalizeTitle(t.title), t]));

    console.log('\n📋 Stage topics:');
    stageTopics.forEach((t, i) => console.log(`   [S${String(i+1).padStart(2,'0')}] ${t.title}`));
    console.log('\n📋 Prod topics:');
    prodTopics.forEach((t, i) => console.log(`   [P${String(i+1).padStart(2,'0')}] ${t.title}`));
    console.log('');

    results = [];

    // Topics in Stage → check Prod
    for (const st of stageTopics) {
      const key = normalizeTitle(st.title);
      const pr  = prodTitleMap.get(key);
      results.push({
        topic:     st.title,
        stageUrl:  st.url || stageTopicUrl(toStageSlug(st.title)),
        prodUrl:   pr?.url || prodTopicUrl(toProdSlug(st.title)),
        missingIn: pr ? '' : 'prod',
        issues:    [],
        stage:     null,
        prod:      null,
      });
    }

    // Topics in Prod NOT in Stage
    for (const pt of prodTopics) {
      const key = normalizeTitle(pt.title);
      if (!stageTitleMap.has(key)) {
        results.push({
          topic:     pt.title,
          stageUrl:  stageTopicUrl(toStageSlug(pt.title)),
          prodUrl:   pt.url || prodTopicUrl(toProdSlug(pt.title)),
          missingIn: 'stage',
          issues:    [],
          stage:     null,
          prod:      null,
        });
      }
    }

    const missingInProd  = results.filter(r => r.missingIn === 'prod').length;
    const missingInStage = results.filter(r => r.missingIn === 'stage').length;

    console.log(`\n📊 Parity Check:`);
    console.log(`   Stage topics : ${stageTopics.length}`);
    console.log(`   Prod topics  : ${prodTopics.length}`);
    console.log(`   Matched      : ${results.filter(r => !r.missingIn).length}`);
    console.log(`   Missing in Prod  : ${missingInProd}`);
    console.log(`   Missing in Stage : ${missingInStage}`);

    fs.writeFileSync(
      path.join(REPORTS_DIR, '_compare_results.json'),
      JSON.stringify(results, null, 2)
    );

    // Report but don't hard-fail (let the Excel tell the story)
    if (missingInProd > 0)  console.warn(`⚠  ${missingInProd} topics exist in Stage but NOT in Prod`);
    if (missingInStage > 0) console.warn(`⚠  ${missingInStage} topics exist in Prod but NOT in Stage`);
  });

  // ── Step 3: Deep content comparison for matched topics ──────────────────────
  test('Step 3 – Deep content comparison for matched topics', async ({ browser }) => {

    results = JSON.parse(fs.readFileSync(path.join(REPORTS_DIR, '_compare_results.json'), 'utf-8'));

    const matched = results.filter(r => !r.missingIn);
    console.log(`\n🔍 Deep-comparing ${matched.length} matched topics (parallel, batch: 12)…`);

    // Create a pool of browser contexts for parallel execution
    const PARALLEL_LIMIT = 12;
    const stageContexts = await Promise.all(
      Array.from({ length: PARALLEL_LIMIT }, () => 
        browser.newContext({ storageState: AUTH_STATE_PATH, ignoreHTTPSErrors: true })
      )
    );
    const prodContexts = await Promise.all(
      Array.from({ length: PARALLEL_LIMIT }, () => 
        browser.newContext({ ignoreHTTPSErrors: true })
      )
    );

    // Extract pages from contexts
    const stagePages = await Promise.all(stageContexts.map(ctx => ctx.newPage()));
    const prodPages  = await Promise.all(prodContexts.map(ctx => ctx.newPage()));

    // Process in batches for better parallelization
    let processed = 0;
    for (let batchStart = 0; batchStart < matched.length; batchStart += PARALLEL_LIMIT) {
      const batch = matched.slice(batchStart, Math.min(batchStart + PARALLEL_LIMIT, matched.length));
      
      const promises = batch.map((r, idx) => (async () => {
        const stagePage = stagePages[idx];
        const prodPage  = prodPages[idx];
        
        const [stageData, prodData] = await Promise.all([
          extractTopicData(stagePage, r.stageUrl, true),
          extractTopicData(prodPage,  r.prodUrl, false),
        ]);
        
        r.stage  = stageData;
        r.prod   = prodData;
        r.issues = compareData(stageData, prodData);
        
        processed++;
        process.stdout.write(`\r   [${processed}/${matched.length}] ${r.topic.slice(0, 55).padEnd(55)}`);
      })());
      
      await Promise.all(promises);
    }

    // Clean up contexts
    await Promise.all([
      ...stageContexts.map(c => c.close()),
      ...prodContexts.map(c => c.close()),
    ]);

    console.log('\n');

    // Save results for Step 4
    fs.writeFileSync(
      path.join(REPORTS_DIR, '_compare_results.json'),
      JSON.stringify(results, null, 2)
    );
  });

  // ── Step 4: Generate Excel report ───────────────────────────────────────────
  test('Step 4 – Generate Excel parity report', async () => {

    results = JSON.parse(fs.readFileSync(path.join(REPORTS_DIR, '_compare_results.json'), 'utf-8'));

    const totalIssues = results.reduce((s, r) => s + r.issues.length, 0);
    const missingInProd  = results.filter(r => r.missingIn === 'prod').length;
    const missingInStage = results.filter(r => r.missingIn === 'stage').length;

    console.log(`\n📝 Building Excel report…`);
    console.log(`   Total results    : ${results.length}`);
    console.log(`   Missing in Prod  : ${missingInProd}`);
    console.log(`   Missing in Stage : ${missingInStage}`);
    console.log(`   Content issues   : ${totalIssues}`);

    const reportFile = await buildReport(results);
    console.log(`\n✅ Report saved → ${reportFile}`);

    // Clean up temp files
    [
      '_stage_topics.json', '_prod_topics.json', '_compare_results.json'
    ].forEach(f => {
      try { fs.unlinkSync(path.join(REPORTS_DIR, f)); } catch { /* ignore */ }
    });

    expect(fs.existsSync(reportFile), 'Report file should exist').toBe(true);
  });

});
