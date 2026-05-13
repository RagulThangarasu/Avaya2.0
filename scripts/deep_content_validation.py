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
REPORT_FILENAME = os.environ.get('REPORT_FILENAME') or os.path.join(UI_REPORTS_DIR, f'deep-content-validation-{int(datetime.now().timestamp())}.xlsx')

# Performance settings
CONCURRENT_PAGES = 5  # Number of parallel page fetches
MAX_TOPICS = 100      # Increased limit for faster validation

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

def get_filename(url):
    if not url: return ""
    path = urlparse(url).path
    filename = path.split('/')[-1] if '/' in path else path
    # Remove extensions and common separator/noise characters for better matching
    name = filename.split('.')[0] # Remove .html etc
    name = name.lower().replace('-', '').replace('_', '').replace(' ', '')
    return name

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
        # Optimization: Block unnecessary resources for metrics
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf}", lambda route: route.abort())
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            for tag in soup(['nav', 'footer', 'script', 'style', 'header', 'aside']):
                tag.decompose()
                
            main_content = soup.find('main') or soup.find('article') or soup.find('div', class_='content') or soup.body
            
            text = main_content.get_text(separator=' ', strip=True) if main_content else ""
            images = len(main_content.find_all('img')) if main_content else 0
            tables = len(main_content.find_all('table')) if main_content else 0
            headings = len(main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])) if main_content else 0
            
            return {
                'text': text,
                'images': images,
                'tables': tables,
                'headings': headings,
                'url': url
            }
        except Exception as e:
            print(f"   ⚠️ Error {url}: {str(e)[:50]}...")
            return None
        finally:
            await page.close()

async def extract_prod_toc(page, base_url):
    print(f"   [TOC] Scanning Prod: {base_url}")
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    await handle_cookies(page)
    
    # Expand TOC
    try:
        await page.evaluate('''() => {
            const btns = document.querySelectorAll('.zDocsCollapseExpandButton');
            if (btns.length > 0) btns[0].click();
        }''')
        print("   [TOC] Expanding virtualized navigation...")
        await page.wait_for_timeout(3000)
    except: pass

    # Scroll and Collect for Virtualized TOC
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
    print(f"   ✅ Prod TOC: {len(toc)} topics found.")
    return toc

async def extract_stage_toc(page, base_url):
    print(f"   [TOC] Scanning Stage: {base_url}")
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
    print(f"   ✅ Stage TOC: {len(toc)} topics found.")
    return toc

async def run_deep_validation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        stage_ctx = await browser.new_context(storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None)
        prod_ctx = await browser.new_context()

        print("🔍 Extracting TOCs...")
        t1 = await stage_ctx.new_page()
        t2 = await prod_ctx.new_page()
        
        stage_toc_task = extract_stage_toc(t1, STAGE_URL)
        prod_toc_task = extract_prod_toc(t2, PROD_URL)
        
        stage_toc, prod_toc = await asyncio.gather(stage_toc_task, prod_toc_task)
        await t1.close()
        await t2.close()

        stage_by_fn = {get_filename(t['url']): t for t in stage_toc if get_filename(t['url'])}
        topics_to_check = []
        # Use PROD as source of truth
        for p_item in prod_toc:
            fn = get_filename(p_item['url'])
            if fn in stage_by_fn:
                topics_to_check.append({
                    'title': p_item['title'], 
                    'prod_url': p_item['url'], 
                    'stage_url': stage_by_fn[fn]['url']
                })

        print(f"🔗 Matched {len(topics_to_check)} topics by filename parity.")
        topics_to_check = topics_to_check[:MAX_TOPICS]
        
        if not topics_to_check:
            print("⚠️ No matching topics found between Stage and Prod.")
            print(f"::RESULTS::{json.dumps({'overall': 0, 'images': 0, 'tables': 0})}")
            await browser.close()
            return

        print(f"📋 Validating content for {len(topics_to_check)} matched topics...")
        semaphore = asyncio.Semaphore(CONCURRENT_PAGES)
        
        async def check_topic(topic):
            p_task = get_page_metrics(prod_ctx, topic['prod_url'], semaphore)
            s_task = get_page_metrics(stage_ctx, topic['stage_url'], semaphore)
            p_m, s_m = await asyncio.gather(p_task, s_task)
            
            if p_m and s_m:
                sim = difflib.SequenceMatcher(None, p_m['text'], s_m['text']).ratio()
                return {
                    'Topic': topic['title'],
                    'Text Similarity (%)': round(sim * 100, 2),
                    'Prod Images': p_m['images'],
                    'Stage Images': s_m['images'],
                    'Images Parity': '✓ Match' if p_m['images'] == s_m['images'] else '✗ Mismatch',
                    'Prod Tables': p_m['tables'],
                    'Stage Tables': s_m['tables'],
                    'Tables Parity': '✓ Match' if p_m['tables'] == s_m['tables'] else '✗ Mismatch',
                    'Prod Headings': p_m['headings'],
                    'Stage Headings': s_m['headings'],
                    'Prod URL': topic['prod_url'],
                    'Stage URL': topic['stage_url']
                }
            return {'Topic': topic['title'], 'Status': 'Error'}

        results = await asyncio.gather(*(check_topic(t) for t in topics_to_check))
        deep_results = [r for r in results if r and r.get('Status') != 'Error']

        if not deep_results:
            print("⚠️ No deep validation results were generated.")
            print(f"::RESULTS::{json.dumps({'overall': 0, 'images': 0, 'tables': 0})}")
            await browser.close()
            return

        df = pd.DataFrame(deep_results)
        avg_sim = df['Text Similarity (%)'].mean()
        img_match_pct = (len(df[df['Images Parity'] == '✓ Match']) / len(df) * 100)
        tbl_match_pct = (len(df[df['Tables Parity'] == '✓ Match']) / len(df) * 100)
        
        summary = [
            ['Deep Content Validation Report'],
            ['Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['Topics Matched', len(deep_results)],
            ['Avg Similarity', f"{round(avg_sim, 2)}%"],
            ['Img Match %', f"{round(img_match_pct, 2)}%"],
            ['Tbl Match %', f"{round(tbl_match_pct, 2)}%"],
        ]
        
        with pd.ExcelWriter(REPORT_FILENAME) as writer:
            pd.DataFrame(summary).to_excel(writer, sheet_name='Summary', header=False, index=False)
            df.to_excel(writer, sheet_name='Comparison', index=False)

        print(f"::RESULTS::{json.dumps({'overall': int(avg_sim), 'images': int(img_match_pct), 'tables': int(tbl_match_pct)})}")
        print(f"✅ Fast validation complete. Report: {REPORT_FILENAME}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_deep_validation())
