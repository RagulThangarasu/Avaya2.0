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
    
    # Expand all
    try:
        await page.evaluate('''() => {
            const btns = document.querySelectorAll('.zDocsCollapseExpandButton');
            if (btns.length > 0) btns[0].click();
        }''')
        await page.wait_for_timeout(3000)
    except: pass

    # Scroll-and-Collect for Virtualized TOC
    links = await page.evaluate('''async () => {
        const container = document.querySelector('ul.zDocsTocList') || document.body;
        const seen = new Set();
        const results = [];
        let lastHeight = 0, scrollCount = 0;
        while (scrollCount < 60) {
            container.querySelectorAll('a[href]').forEach(a => {
                const href = new URL(a.getAttribute('href'), window.location.href).href.split('#')[0].split('?')[0];
                if (href && !seen.has(href) && !href.startsWith('javascript')) {
                    seen.add(href);
                    results.push({text: a.innerText.trim(), url: href});
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
    return [{'title': l['text'], 'url': l['url']} for l in links]

async def extract_stage_toc(page, base_url):
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    await handle_cookies(page)
    await page.wait_for_timeout(2000)
    
    links = await page.evaluate('''() => {
        const results = [];
        document.querySelectorAll('.cmp-navigation__item-link').forEach(a => {
            const url = new URL(a.getAttribute('href'), window.location.href).href.split('#')[0].split('?')[0];
            results.push({text: a.innerText.trim(), url: url});
        });
        return results;
    }''')
    return [{'title': l['text'], 'url': l['url']} for l in links]

async def run_validation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        s_ctx = await browser.new_context(storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None)
        p_ctx = await browser.new_context()

        p1, p2 = await s_ctx.new_page(), await p_ctx.new_page()
        print("🔍 Scanning Navigation Structure (Parallel)...")
        stage_toc, prod_toc = await asyncio.gather(extract_stage_toc(p1, STAGE_URL), extract_prod_toc(p2, PROD_URL))
        await browser.close()

        print(f"📊 Comparing: Prod={len(prod_toc)} vs Stage={len(stage_toc)}")

        if not prod_toc:
            print("⚠️ Production TOC is empty.")
            print(f"::RESULTS::{json.dumps({'overall': 0, 'content': 0})}")
            return

        s_map = {get_filename(t['url']): t for t in stage_toc if get_filename(t['url'])}
        comparison = []

        for i, p_item in enumerate(prod_toc):
            fn = get_filename(p_item['url'])
            matched_s = s_map.get(fn)
            
            match = "NO"
            if matched_s:
                if slugify(p_item['title']) == slugify(matched_s['title']): match = "YES"
                else: match = "FILENAME_MATCH"

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
