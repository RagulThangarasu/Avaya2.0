import os
import sys
import json
import asyncio
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
import pandas as pd
from playwright.async_api import async_playwright

# Configurations
REPORTS_DIR = os.path.join(os.getcwd(), 'reports')
UI_REPORTS_DIR = os.path.join(os.getcwd(), '.ui_reports')
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(UI_REPORTS_DIR, exist_ok=True)

AUTH_STATE_PATH = os.path.join(os.getcwd(), 'auth-sessions', 'storage-state.json')

STAGE_URL = os.environ.get('STAGE_URL')
PROD_URL = os.environ.get('PROD_URL')
REPORT_FILENAME = os.environ.get('REPORT_FILENAME') or os.path.join(UI_REPORTS_DIR, f'content-parity-{int(datetime.now().timestamp())}.xlsx')

if not STAGE_URL or not PROD_URL:
    try:
        with open(TEST_URLS_PATH, 'r') as f:
            config = json.load(f)
            STAGE_URL = config.get('stage')
            PROD_URL = config.get('production')
    except: pass

def get_filename(url):
    if not url: return ""
    path = urlparse(url).path
    filename = path.split('/')[-1] if '/' in path else path
    name = filename.split('.')[0]
    return name.lower().replace('-', '').replace('_', '').replace(' ', '')

async def handle_cookies(page):
    try:
        await page.evaluate('''() => {
            const sels = ['#onetrust-accept-btn-handler', '#btn-accept-all', 'button:has-text("Accept")', '.cookie-accept'];
            for (const s of sels) {
                const b = document.querySelector(s);
                if (b) b.click();
            }
        }''')
    except: pass

async def senior_toc_extraction(page, base_url, is_prod=True):
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    await handle_cookies(page)
    
    if is_prod:
        try:
            await page.evaluate('''async () => {
                const sleep = m => new Promise(r => setTimeout(r, m));
                const rootBtn = document.querySelector('.zDocsCollapseExpandButton');
                if (rootBtn) rootBtn.click();
                await sleep(2000);
                for (let i = 0; i < 3; i++) {
                    const collapsed = document.querySelectorAll('.zDocsTocItemCollapsed .zDocsTocItemToggle');
                    if (collapsed.length === 0) break;
                    collapsed.forEach(btn => btn.click());
                    await sleep(1500);
                }
            }''')
        except: pass

    links = await page.evaluate('''async () => {
        const sleep = m => new Promise(r => setTimeout(r, m));
        const container = document.querySelector('ul.zDocsTocList') || document.querySelector('.zDocsTOC') || document.body;
        const results = [];
        const seen = new Set();
        let lastHeight = 0, scrollCount = 0;
        while (scrollCount < 80) {
            container.querySelectorAll('a[href]').forEach(a => {
                const href = new URL(a.getAttribute('href'), window.location.href).href.split('#')[0].split('?')[0];
                const text = a.innerText.trim();
                if (href && text && !seen.has(href) && !href.startsWith('javascript')) {
                    seen.add(href);
                    results.push({text, url: href});
                }
            });
            container.scrollTop += 600;
            await sleep(400);
            if (container.scrollTop === lastHeight) break;
            lastHeight = container.scrollTop;
            scrollCount++;
        }
        return results;
    }''')
    return [{'title': l['text'], 'url': l['url']} for l in links if '.html' in l['url'] or '/page/' in l['url']]

async def run_validation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        s_ctx = await browser.new_context(storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None)
        p_ctx = await browser.new_context()

        p1, p2 = await s_ctx.new_page(), await p_ctx.new_page()
        print("🔍 Scanning Navigation Structure (Parallel)...")
        stage_toc, prod_toc = await asyncio.gather(senior_toc_extraction(p1, STAGE_URL, False), senior_toc_extraction(p2, PROD_URL, True))
        await browser.close()

        print(f"📊 Results: Prod={len(prod_toc)} topics vs Stage={len(stage_toc)} topics")

        s_map = {get_filename(t['url']): t for t in stage_toc if get_filename(t['url'])}
        comparison = []
        for i, p_item in enumerate(prod_toc):
            fn = get_filename(p_item['url'])
            matched_s = s_map.get(fn)
            comparison.append({
                'Order': i + 1,
                'Prod Topic': p_item['title'],
                'Stage Topic': matched_s['title'] if matched_s else '[MISSING]',
                'Match': 'YES' if matched_s else 'NO',
                'Prod URL': p_item['url'],
                'Stage URL': matched_s['url'] if matched_s else 'N/A'
            })

        df = pd.DataFrame(comparison)
        pct = int(len(df[df['Match'] == 'YES'])/len(df)*100) if not df.empty else 0

        with pd.ExcelWriter(REPORT_FILENAME) as writer:
            pd.DataFrame([['TOC Parity Audit'], ['Date', datetime.now().strftime('%Y-%m-%d')], ['Match Rate', f"{pct}%"]]).to_excel(writer, sheet_name='Summary', header=False, index=False)
            df.to_excel(writer, sheet_name='Audit Details', index=False)

        print(f"::RESULTS::{json.dumps({'overall': pct, 'content': pct})}")
        print(f"✅ Audit Complete: {REPORT_FILENAME}")

if __name__ == "__main__":
    asyncio.run(run_validation())
