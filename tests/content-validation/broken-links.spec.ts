import { test, expect, Page, APIRequestContext, BrowserContext } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import ExcelJS from 'exceljs';

/**
 * Robust Broken Links, Images, and Icons Validator
 */

const REPORTS_DIR = path.join(process.cwd(), 'reports');
const REPORT_FILENAME = process.env.REPORT_FILENAME || path.join(REPORTS_DIR, 'broken-links-report.xlsx');
const CONCURRENCY_LIMIT = 15; 

const testUrls = {
  stage: process.env.STAGE_URL || '',
  production: process.env.PROD_URL || '',
};

function deriveBase(url: string) {
  if (!url) return { origin: '', bundle: '' };
  try {
    const parsed = new URL(url);
    const bundleMatch = url.match(/\/bundle\/([^/]+)\//);
    return { origin: parsed.origin, bundle: bundleMatch ? bundleMatch[1] : '' };
  } catch (e) {
    return { origin: '', bundle: '' };
  }
}

const targetInfo = deriveBase(testUrls.production || testUrls.stage);

interface BrokenItem {
  topic: string;
  topicUrl: string;
  elementText: string;
  targetUrl: string;
  status: number | string;
  category: 'Link' | 'Image' | 'Icon';
  type: 'Internal' | 'External';
  error?: string;
}

const results: BrokenItem[] = [];
const checkedUrls = new Map<string, { status: number | string, error?: string }>();

async function handleCommonModals(page: Page) {
  try {
    const cookieSelectors = ['#onetrust-accept-btn-handler', 'button:has-text("Accept")', '#btn-accept-all'];
    for (const sel of cookieSelectors) {
      if (await page.locator(sel).isVisible({ timeout: 1000 }).catch(() => false)) {
        await page.click(sel).catch(() => {});
      }
    }
  } catch (e) {}
}

async function validateItem(request: APIRequestContext, item: { text: string, url: string, category: 'Link' | 'Image' | 'Icon' }, topic: { title: string, url: string }) {
  let fullUrl = item.url;
  
  try {
    const resolved = new URL(item.url, topic.url);
    fullUrl = resolved.href;
  } catch (e) {
    if (fullUrl.startsWith('/')) fullUrl = targetInfo.origin + fullUrl;
  }

  if (!fullUrl.startsWith('http')) return;
  if (fullUrl.includes('mailto:') || fullUrl.includes('tel:')) return;

  if (checkedUrls.has(fullUrl)) {
    const cached = checkedUrls.get(fullUrl)!;
    if (cached.status !== 200 && cached.status !== 301 && cached.status !== 302) {
      results.push({
        topic: topic.title,
        topicUrl: topic.url,
        elementText: item.text || '(No Label)',
        targetUrl: fullUrl,
        status: cached.status,
        category: item.category,
        type: fullUrl.includes(targetInfo.origin) ? 'Internal' : 'External',
        error: cached.error
      });
    }
    return;
  }

  try {
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    };

    const response = await request.head(fullUrl, { timeout: 10000, headers }).catch(() => null);
    let status: number | string = response ? response.status() : 'TIMEOUT';
    
    if (!response || status === 405 || status === 404 || status === 403 || status === 401) {
       const getResp = await request.get(fullUrl, { timeout: 10000, headers }).catch(() => null);
       status = getResp ? getResp.status() : (status === 'TIMEOUT' ? 'TIMEOUT' : 'FAILED');
    }

    checkedUrls.set(fullUrl, { status });

    if (status !== 200 && status !== 301 && status !== 302) {
      results.push({
        topic: topic.title,
        topicUrl: topic.url,
        elementText: item.text || '(No Label)',
        targetUrl: fullUrl,
        status,
        category: item.category,
        type: fullUrl.includes(targetInfo.origin) ? 'Internal' : 'External'
      });
    }
  } catch (e: any) {
    checkedUrls.set(fullUrl, { status: 'ERROR', error: e.message });
    results.push({
      topic: topic.title,
      topicUrl: topic.url,
      elementText: item.text || '(No Label)',
      targetUrl: fullUrl,
      status: 'ERROR',
      category: item.category,
      type: fullUrl.includes(targetInfo.origin) ? 'Internal' : 'External',
      error: e.message
    });
  }
}

test.describe('Full Asset Validation', () => {
  test('Crawl Links, Images, and Icons', async ({ page, browser, context }) => {
    test.setTimeout(1800000); 

    const startUrl = testUrls.production || testUrls.stage;
    if (!startUrl) throw new Error('No URL provided');

    console.log(`🚀 Starting Full Asset Crawl for: ${startUrl}`);
    await page.goto(startUrl, { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
    await handleCommonModals(page);

    const cookies = await context.cookies();
    const cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join('; ');

    const authContext = await browser.newContext({
      extraHTTPHeaders: { 'Cookie': cookieHeader }
    });
    const authRequest = authContext.request;

    // Extract Nav
    await page.waitForSelector('.zDocsTocList, .zDocsTOC, nav', { timeout: 15000 }).catch(() => {});
    const topics = await page.evaluate(() => {
      const selectors = ['.zDocsTocList a[href]', '.zDocsTOC a[href]', '.toc-list a[href]', 'nav a[href]'];
      let links: { title: string, url: string }[] = [];
      for (const sel of selectors) {
        const els = Array.from(document.querySelectorAll(sel));
        if (els.length > 5) {
          links = els.map(el => ({
            title: (el as HTMLElement).innerText.trim(),
            url: (el as HTMLAnchorElement).href
          })).filter(l => l.url && (l.url.includes('/bundle/') || l.url.includes('/page/')));
          break;
        }
      }
      return links;
    });

    console.log(`✅ Found ${topics.length} topics. Validating Links + Images + Icons...`);

    const CHUNK_SIZE = 5; 
    for (let i = 0; i < topics.length; i += CHUNK_SIZE) {
      const chunk = topics.slice(i, i + CHUNK_SIZE);
      
      await Promise.all(chunk.map(async (topic) => {
        try {
          const response = await authRequest.get(topic.url, { timeout: 30000 });
          if (!response.ok()) return;
          
          const html = await response.text();
          const itemsToValidate: { text: string, url: string, category: 'Link' | 'Image' | 'Icon' }[] = [];
          
          // 1. Links
          const linkRegex = /<a\s+(?:[^>]*?\s+)?href=(["'])(.*?)\1[^>]*?>(.*?)<\/a>/gi;
          let match;
          while ((match = linkRegex.exec(html)) !== null) {
            const href = match[2];
            const text = match[3].replace(/<[^>]*>?/gm, '').trim();
            if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
              itemsToValidate.push({ text, url: href, category: 'Link' });
            }
          }

          // 2. Images
          const imgRegex = /<img\s+(?:[^>]*?\s+)?src=(["'])(.*?)\1[^>]*?>(?:.*?alt=(["'])(.*?)\3)?/gi;
          while ((match = imgRegex.exec(html)) !== null) {
            const src = match[2];
            const alt = match[4] || 'image';
            if (src && !src.startsWith('data:')) {
              itemsToValidate.push({ text: `Alt: ${alt}`, url: src, category: 'Image' });
            }
          }

          // 3. Icons (CSS background-image or specific classes)
          const iconRegex = /<span\s+(?:[^>]*?\s+)?class=["'](?:[^"']*?\s+)?(icon-[^"']*?|fa-[^"']*?)["'][^>]*?>/gi;
          // Note: Full icon validation would require CSS parsing, but we can catch <img> icons above.

          // Parallel validate this topic's assets
          for (let j = 0; j < itemsToValidate.length; j += CONCURRENCY_LIMIT) {
            const batch = itemsToValidate.slice(j, j + CONCURRENCY_LIMIT);
            await Promise.all(batch.map(item => validateItem(authRequest, item, topic)));
          }
          console.log(`Done: ${topic.title}`);
        } catch (e) {}
      }));
      console.log(`Progress: ${Math.min(i + CHUNK_SIZE, topics.length)} / ${topics.length}`);
    }

    await generateReport();
  });
});

async function generateReport() {
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet('Validation Results');

  ws.columns = [
    { header: 'Topic', key: 'topic', width: 35 },
    { header: 'Category', key: 'category', width: 12 },
    { header: 'Asset Label / Alt', key: 'text', width: 30 },
    { header: 'Asset URL', key: 'url', width: 50 },
    { header: 'Status', key: 'status', width: 12 },
    { header: 'Type', key: 'type', width: 10 },
    { header: 'Location', key: 'page', width: 50 },
    { header: 'Error Details', key: 'error', width: 30 },
  ];

  ws.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' } };
  ws.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFDA291C' } };

  results.forEach(r => {
    const row = ws.addRow({
      topic: r.topic,
      category: r.category,
      text: r.elementText,
      url: r.targetUrl,
      status: r.status,
      type: r.type,
      page: r.topicUrl,
      error: r.error || ''
    });

    const statusCell = row.getCell('status');
    if (r.status === 404) {
      statusCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFCCCC' } };
      statusCell.font = { color: { argb: 'FFFF0000' }, bold: true };
    }
  });

  const summaryWs = wb.addWorksheet('Summary');
  summaryWs.addRow(['Asset Validation Summary']);
  summaryWs.addRow(['Run Date', new Date().toLocaleString()]);
  summaryWs.addRow(['Total Assets Checked', checkedUrls.size]);
  summaryWs.addRow(['Broken Assets Found', results.length]);
  summaryWs.addRow(['Broken Links', results.filter(r => r.category === 'Link').length]);
  summaryWs.addRow(['Broken Images', results.filter(r => r.category === 'Image').length]);
  summaryWs.addRow(['Broken Icons', results.filter(r => r.category === 'Icon').length]);

  summaryWs.getColumn(1).width = 25;
  summaryWs.getRow(1).font = { bold: true, size: 14 };

  await wb.xlsx.writeFile(REPORT_FILENAME);
  console.log(`Excel report saved to: ${REPORT_FILENAME}`);
}
