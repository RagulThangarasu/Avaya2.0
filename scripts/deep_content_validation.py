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

# Thresholds for "Tester" perspective
SIMILARITY_THRESHOLD = 95.0
IMAGE_PARITY_REQUIRED = True
TABLE_PARITY_REQUIRED = True

STAGE_URL = os.environ.get('STAGE_URL')
PROD_URL = os.environ.get('PROD_URL')
REPORT_FILENAME = os.environ.get('REPORT_FILENAME') or os.path.join(UI_REPORTS_DIR, f'tester-content-report-{int(datetime.now().timestamp())}.xlsx')

CONCURRENT_PAGES = 5
MAX_TOPICS = 150  # Increased for comprehensive testing

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

async def get_page_data(browser_context, url, semaphore):
    async with semaphore:
        page = await browser_context.new_page()
        # Block non-content assets
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf}", lambda route: route.abort())
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            # ── NOISE STRIPPING (The "Tester" Way) ──
            # Remove global UI elements that skew similarity
            for noise in soup.select('nav, footer, script, style, header, aside, .breadcrumbs, .search-results, .feedback-section, .zDocsToolbar'):
                noise.decompose()
                
            # Focus on the meat of the documentation
            main = soup.find('main') or soup.find('article') or soup.find('div', class_='content') or soup.body
            
            if not main:
                return {'error': 'No content container found'}

            # Extract metrics
            text = main.get_text(separator=' ', strip=True)
            images = [img.get('src') for img in main.find_all('img') if img.get('src')]
            tables = len(main.find_all('table'))
            
            # Heading structure (Sequence matters for testers!)
            headings = []
            for h in main.find_all(['h1', 'h2', 'h3', 'h4']):
                headings.append(f"{h.name}: {h.get_text().strip()}")
            
            return {
                'text': text,
                'image_count': len(images),
                'table_count': tables,
                'headings': headings,
                'url': url
            }
        except Exception as e:
            return {'error': str(e)}
        finally:
            await page.close()

async def extract_prod_toc(page, base_url):
    print(f"🔍 [PROD SCAN] Accessing source of truth: {base_url}")
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    await handle_cookies(page)
    
    # Expand All topics
    try:
        await page.evaluate('''() => {
            const btns = document.querySelectorAll('.zDocsCollapseExpandButton');
            if (btns.length > 0) btns[0].click();
        }''')
        await page.wait_for_timeout(4000)
    except: pass

    # Scroll and Collect (Handle virtualization)
    links = await page.evaluate('''async () => {
        const container = document.querySelector('ul.zDocsTocList') || document.body;
        const seen = new Set();
        const results = [];
        let lastHeight = 0, scrollCount = 0;
        while (scrollCount < 50) {
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
    print(f"✅ Found {len(links)} topics on Production.")
    return links

async def extract_stage_toc(page, base_url):
    print(f"🔍 [STAGE SCAN] Accessing staging environment...")
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
    print(f"✅ Found {len(links)} topics on Stage.")
    return links

async def run_tester_validation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        s_ctx = await browser.new_context(storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None)
        p_ctx = await browser.new_context()

        # Step 1: TOC Discovery
        p1, p2 = await s_ctx.new_page(), await p_ctx.new_page()
        s_toc_task = extract_stage_toc(p1, STAGE_URL)
        p_toc_task = extract_prod_toc(p2, PROD_URL)
        s_toc, p_toc = await asyncio.gather(s_toc_task, p_toc_task)
        await p1.close(); await p2.close()

        # Step 2: Mapping (Source of Truth = Production)
        s_map = {get_filename(t['url']): t for t in s_toc if get_filename(t['url'])}
        work_list = []
        for p_item in p_toc:
            fn = get_filename(p_item['url'])
            work_list.append({
                'title': p_item['title'] if 'title' in p_item else p_item.get('text'),
                'prod_url': p_item['url'],
                'stage_url': s_map.get(fn, {}).get('url')
            })

        work_list = work_list[:MAX_TOPICS]
        print(f"📋 Starting deep content check for {len(work_list)} items...")
        
        # Step 3: Deep Content Validation
        semaphore = asyncio.Semaphore(CONCURRENT_PAGES)
        
        async def test_topic(item):
            if not item['stage_url']:
                return {**item, 'Result': 'FAIL', 'Comment': 'Missing on Stage'}
            
            p_data = await get_page_data(p_ctx, item['prod_url'], semaphore)
            s_data = await get_page_data(s_ctx, item['stage_url'], semaphore)
            
            if 'error' in p_data or 'error' in s_data:
                return {**item, 'Result': 'ERROR', 'Comment': f"Fetch failed: {p_data.get('error') or s_data.get('error')}"}

            # Similarity
            sim = round(difflib.SequenceMatcher(None, p_data['text'], s_data['text']).ratio() * 100, 2)
            
            # Heading Structure
            h_match = "YES" if p_data['headings'] == s_data['headings'] else "NO"
            
            # Tester Decision
            is_pass = (sim >= SIMILARITY_THRESHOLD and 
                       p_data['image_count'] == s_data['image_count'] and 
                       p_data['table_count'] == s_data['table_count'])
            
            return {
                'Topic': item['title'],
                'Result': 'PASS' if is_pass else 'FAIL',
                'Similarity %': sim,
                'Image Parity': '✓' if p_data['image_count'] == s_data['image_count'] else f"{p_data['image_count']} vs {s_data['image_count']}",
                'Table Parity': '✓' if p_data['table_count'] == s_data['table_count'] else f"{p_data['table_count']} vs {s_data['table_count']}",
                'Heading Match': h_match,
                'Prod URL': item['prod_url'],
                'Stage URL': item['stage_url']
            }

        tasks = [test_topic(it) for it in work_list]
        raw_results = await asyncio.gather(*tasks)
        final_results = [r for r in raw_results if r]

        # Step 4: Report Generation
        df = pd.DataFrame(final_results)
        
        passed = len(df[df['Result'] == 'PASS'])
        total = len(df)
        score = int(passed/total*100) if total else 0
        
        # Calculate individual metrics for UI
        avg_sim = df['Similarity %'].mean() if 'Similarity %' in df.columns else 0
        img_match_count = len(df[df['Image Parity'] == '✓'])
        tbl_match_count = len(df[df['Table Parity'] == '✓'])
        
        with pd.ExcelWriter(REPORT_FILENAME) as writer:
            pd.DataFrame([
                ['Tester Quality Report'],
                ['Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
                ['Overall Quality Score', f"{score}%"],
                ['Total Topics Validated', total],
                ['Passed Topics', passed]
            ]).to_excel(writer, sheet_name='Summary', header=False, index=False)
            df.to_excel(writer, sheet_name='Detailed Validation', index=False)

        print(f"::RESULTS::{json.dumps({'overall': int(avg_sim), 'images': int(img_match_count/total*100), 'tables': int(tbl_match_count/total*100)})}")
        print(f"✅ Tester validation complete. Report saved: {REPORT_FILENAME}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_tester_validation())
