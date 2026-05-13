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

# Load URLs
TEST_URLS_PATH = os.path.join(os.getcwd(), 'config', 'test-urls.json')
AUTH_STATE_PATH = os.path.join(os.getcwd(), 'auth-sessions', 'storage-state.json')

STAGE_URL = os.environ.get('STAGE_URL')
PROD_URL = os.environ.get('PROD_URL')
REPORT_FILENAME = os.environ.get('REPORT_FILENAME') or os.path.join(UI_REPORTS_DIR, f'content-parity-{int(datetime.now().timestamp())}.xlsx')

if not STAGE_URL or not PROD_URL:
    try:
        with open(TEST_URLS_PATH, 'r') as f:
            config = json.load(f)
            STAGE_URL = STAGE_URL or config.get('stage')
            PROD_URL = PROD_URL or config.get('production')
    except: pass

if not STAGE_URL or not PROD_URL:
    print("❌ Error: Missing URLs.")
    sys.exit(1)

def slugify(t):
    if not t: return ""
    return re.sub(r'[^a-z0-9]', '', t.lower())

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

async def extract_prod_toc(page, base_url):
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    await handle_cookies(page)
    
    try:
        await page.evaluate('''() => {
            const btns = document.querySelectorAll('.zDocsCollapseExpandButton');
            if (btns.length > 0) btns[0].click();
        }''')
        await page.wait_for_timeout(3000)
    except: pass

    toc_links = await page.evaluate('''async () => {
        const container = document.querySelector('ul.zDocsTocList') || document.querySelector('.zDocsTOC') || document.body;
        const seen = new Set();
        const results = [];
        let lastHeight = 0, scrollCount = 0;
        
        while (scrollCount < 60) {
            container.querySelectorAll('a[href]').forEach(a => {
                const text = a.innerText.trim();
                const href = a.getAttribute('href');
                if (href && text && text.length > 1 && !href.startsWith('#') && !href.startsWith('javascript')) {
                    const full = new URL(href, window.location.href).href.split('#')[0].split('?')[0];
                    if (!seen.has(full)) {
                        seen.add(full);
                        results.push({text, href});
                    }
                }
            });
            container.scrollTop += 800;
            await new Promise(r => setTimeout(r, 400));
            if (container.scrollTop === lastHeight) break;
            lastHeight = container.scrollTop;
            scrollCount++;
        }
        return results;
    }''')
    
    toc = []
    seen = set()
    for item in toc_links:
        url = urljoin(base_url, item['href']).split('#')[0].split('?')[0]
        if url not in seen:
            toc.append({'title': item['text'], 'url': url})
            seen.add(url)
    return toc

async def extract_stage_toc(page, base_url):
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    await handle_cookies(page)
    await page.wait_for_timeout(2000)
    
    links_data = await page.evaluate('''() => {
        const results = [];
        document.querySelectorAll('.cmp-navigation__item-link').forEach(a => {
            const text = a.innerText.trim();
            const href = a.getAttribute('href');
            if (href && text) results.push({text, href});
        });
        return results;
    }''')
    
    toc = []
    seen = set()
    for item in links_data:
        url = urljoin(base_url, item['href']).split('#')[0].split('?')[0]
        if url not in seen and '.html' in url:
            toc.append({'title': item['text'], 'url': url})
            seen.add(url)
    return toc

async def run_validation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        stage_ctx = await browser.new_context(storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None)
        prod_ctx = await browser.new_context()

        p1 = await stage_ctx.new_page()
        p2 = await prod_ctx.new_page()
        
        print("🔍 Scanning Navigation Structure (Parallel)...")
        stage_task = extract_stage_toc(p1, STAGE_URL)
        prod_task = extract_prod_toc(p2, PROD_URL)
        
        stage_toc, prod_toc = await asyncio.gather(stage_task, prod_task)
        await browser.close()

        print(f"📊 Comparing: Prod={len(prod_toc)} vs Stage={len(stage_toc)}")

        if not prod_toc:
            print("⚠️ Production TOC is empty.")
            print(f"::RESULTS::{json.dumps({'overall': 0, 'content': 0})}")
            return

        stage_by_fn = {get_filename(t['url']): t for t in stage_toc if get_filename(t['url'])}
        comparison = []

        for i, p_item in enumerate(prod_toc):
            fn = get_filename(p_item['url'])
            matched_s = stage_by_fn.get(fn)
            
            match = "NO"
            if matched_s:
                if slugify(p_item['title']) == slugify(matched_s['title']):
                    match = "YES"
                else:
                    match = "FILENAME_MATCH"

            comparison.append({
                'Order': i + 1,
                'Prod Topic': p_item['title'],
                'Stage Topic': matched_s['title'] if matched_s else '[MISSING]',
                'Match': match,
                'Prod URL': p_item['url'],
                'Stage URL': matched_s['url'] if matched_s else 'N/A'
            })

        df = pd.DataFrame(comparison)
        matched_count = len(df[df['Match'].isin(['YES', 'FILENAME_MATCH'])])
        total = len(df)
        pct = int(matched_count/total*100) if total else 0

        summary = [
            ['Content Parity Summary'],
            ['Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['Prod Topics', len(prod_toc)],
            ['Stage Topics', len(stage_toc)],
            ['Match Percentage', f"{pct}%"]
        ]
        
        with pd.ExcelWriter(REPORT_FILENAME) as writer:
            pd.DataFrame(summary).to_excel(writer, sheet_name='Summary', header=False, index=False)
            df.to_excel(writer, sheet_name='TOC Parity', index=False)

        print(f"::RESULTS::{json.dumps({'overall': pct, 'content': pct})}")
        print(f"✅ TOC Validation complete: {REPORT_FILENAME}")

if __name__ == "__main__":
    asyncio.run(run_validation())
