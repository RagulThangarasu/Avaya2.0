import os
import sys
import json
import asyncio
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
import pandas as pd
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import difflib

# ── CONFIGURATIONS ──
REPORTS_DIR = os.path.join(os.getcwd(), 'reports')
UI_REPORTS_DIR = os.path.join(os.getcwd(), '.ui_reports')
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(UI_REPORTS_DIR, exist_ok=True)

AUTH_STATE_PATH = os.path.join(os.getcwd(), 'auth-sessions', 'storage-state.json')
TEST_URLS_PATH = os.path.join(os.getcwd(), 'config', 'test-urls.json')

# Thresholds for "Senior Tester" perspective
SIMILARITY_THRESHOLD = 95.0
CONCURRENT_PAGES = 5
MAX_TOPICS = 500 

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

async def get_page_metrics(browser_context, url, semaphore):
    async with semaphore:
        page = await browser_context.new_page()
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf}", lambda route: route.abort())
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            for noise in soup.select('nav, footer, script, style, header, aside, .breadcrumbs, .zDocsToolbar, .feedback-section'):
                noise.decompose()
            main = soup.find('main') or soup.find('article') or soup.find('div', class_='content') or soup.body
            if not main: return {'error': 'No content'}
            text = main.get_text(separator=' ', strip=True)
            images = len(main.find_all('img'))
            tables = len(main.find_all('table'))
            headings = [h.get_text().strip() for h in main.find_all(['h1', 'h2', 'h3'])]
            return {'text': text, 'img_count': images, 'tbl_count': tables, 'h_structure': headings}
        except Exception as e:
            return {'error': str(e)}
        finally:
            await page.close()

async def senior_toc_extraction(page, base_url, is_prod=True):
    print(f"🔍 [SENIOR SCAN] {'Production' if is_prod else 'Stage'}: {base_url}")
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
    return [l for l in links if '.html' in l['url'] or '/page/' in l['url']]

async def run_senior_validation():
    # Load URLs inside to avoid scoping issues
    STAGE_URL = os.environ.get('STAGE_URL')
    PROD_URL = os.environ.get('PROD_URL')
    REPORT_FILENAME = os.environ.get('REPORT_FILENAME') or os.path.join(UI_REPORTS_DIR, f'tester-content-report-{int(datetime.now().timestamp())}.xlsx')

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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        s_ctx = await browser.new_context(storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None)
        p_ctx = await browser.new_context()

        p1, p2 = await s_ctx.new_page(), await p_ctx.new_page()
        prod_toc_task = senior_toc_extraction(p2, PROD_URL, is_prod=True)
        stage_toc_task = senior_toc_extraction(p1, STAGE_URL, is_prod=False)
        prod_toc, stage_toc = await asyncio.gather(prod_toc_task, stage_toc_task)
        await p1.close(); await p2.close()

        s_map = {get_filename(t['url']): t for t in stage_toc if get_filename(t['url'])}
        work_list = []
        for p_item in prod_toc:
            fn = get_filename(p_item['url'])
            work_list.append({'title': p_item['text'], 'p_url': p_item['url'], 's_url': s_map.get(fn, {}).get('url')})

        work_list = work_list[:MAX_TOPICS]
        print(f"📋 Auditing content for {len(work_list)} topics...")
        
        semaphore = asyncio.Semaphore(CONCURRENT_PAGES)
        async def audit_topic(item):
            if not item['s_url']: return {**item, 'Pass/Fail': 'FAIL', 'Similarity %': 0, 'Status': 'MISSING'}
            p_res = await get_page_metrics(p_ctx, item['p_url'], semaphore)
            s_res = await get_page_metrics(s_ctx, item['s_url'], semaphore)
            if 'error' in p_res or 'error' in s_res: return {**item, 'Status': 'ERROR'}
            sim = round(difflib.SequenceMatcher(None, p_res['text'], s_res['text']).ratio() * 100, 2)
            passed = (sim >= SIMILARITY_THRESHOLD and p_res['img_count'] == s_res['img_count'] and p_res['tbl_count'] == s_res['tbl_count'])
            return {'Topic': item['title'], 'Pass/Fail': 'PASS' if passed else 'FAIL', 'Similarity %': sim, 
                    'Images': '✓' if p_res['img_count'] == s_res['img_count'] else f"{p_res['img_count']} vs {s_res['img_count']}",
                    'Tables': '✓' if p_res['tbl_count'] == s_res['tbl_count'] else f"{p_res['tbl_count']} vs {s_res['tbl_count']}",
                    'Prod URL': item['p_url'], 'Stage URL': item['s_url']}

        results = await asyncio.gather(*(audit_topic(it) for it in work_list))
        df = pd.DataFrame([r for r in results if r])
        
        avg_sim = df['Similarity %'].mean() if 'Similarity %' in df.columns else 0
        pass_rate = (len(df[df['Pass/Fail'] == 'PASS']) / len(df) * 100) if not df.empty else 0

        with pd.ExcelWriter(REPORT_FILENAME) as writer:
            pd.DataFrame([['Senior Audit'], ['Date', datetime.now().strftime('%Y-%m-%d')], ['Pass Rate', f"{round(pass_rate, 2)}%"]]).to_excel(writer, sheet_name='Summary', header=False, index=False)
            df.to_excel(writer, sheet_name='Details', index=False)

        print(f"::RESULTS::{json.dumps({'overall': int(avg_sim), 'images': int(pass_rate), 'tables': int(pass_rate)})}")
        print(f"✅ Audit Complete: {REPORT_FILENAME}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_senior_validation())
