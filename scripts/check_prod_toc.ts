import { chromium } from '@playwright/test';
import * as path from 'path';

(async () => {
  const AUTH = path.resolve('./auth-sessions/storage-state.json');
  const b = await chromium.launch();
  
  const stageCtx = await b.newContext({ storageState: AUTH, ignoreHTTPSErrors: true });
  const sp = await stageCtx.newPage();
  await sp.goto('https://author-p181473-e1910301.adobeaemcloud.com/content/aemsites/en-us/bundle/AdministeringAvayaAuraAdminPortal/notices.html?wcmmode=disabled', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sp.waitForTimeout(3000);

  // Check #toc-content
  const tocEl = await sp.$('#toc-content');
  if (tocEl) {
    // Scroll to load all items
    await tocEl.evaluate((el: any) => { el.scrollTop = el.scrollHeight; });
    await sp.waitForTimeout(1000);
    await tocEl.evaluate((el: any) => { el.scrollTop = 0; });
    await sp.waitForTimeout(500);
    
    const total = await tocEl.$$eval('a', els => els.length);
    console.log('Total links in #toc-content:', total);
    const items = await tocEl.$$eval('a', els => els.map(e => ({ t: e.textContent?.trim(), h: e.getAttribute('href') })));
    items.forEach((item, i) => console.log(`  [${i+1}] ${item.t}`));
  }

  // Also count total nav links with bundle
  const allNav = await sp.$$eval('a[href*="AdministeringAvayaAuraAdminPortal"]', els => els.length);
  console.log('\nTotal links with bundle in href (whole page):', allNav);

  // What about the navigation[ref=e103] area from the error context? Let's use [aria-label] or role
  const navEls = await sp.$$eval('navigation, [role="navigation"]', els => els.map(e => ({ tag: e.tagName, id: e.id, cls: e.className.substring(0,80), links: e.querySelectorAll('a').length })));
  console.log('\nNavigation elements:', JSON.stringify(navEls, null, 2));

  await b.close();
})();
