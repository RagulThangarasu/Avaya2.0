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

if not STAGE_URL or not PROD_URL:
    try:
        with open(TEST_URLS_PATH, 'r') as f:
            config = json.load(f)
            STAGE_URL = STAGE_URL or config.get('stage')
            PROD_URL = PROD_URL or config.get('production')
    except:
        pass

if not STAGE_URL or not PROD_URL:
    print("❌ Error: Missing STAGE_URL or PROD_URL.")
    sys.exit(1)

print(f"🚀 Starting Deep Content Validation")
print(f"   Published (Stage): {STAGE_URL}")
print(f"   Prod:              {PROD_URL}")

def get_filename(url):
    path = urlparse(url).path
    filename = path.split('/')[-1] if '/' in path else path
    return filename.replace('.html', '').replace('.htm', '').lower().replace('-', '').replace('_', '')

async def handle_cookies(page):
    try:
        for sel in ['#onetrust-accept-btn-handler', '#btn-accept-all', 'button:has-text("Accept")', '.cookie-accept']:
            if await page.locator(sel).is_visible(timeout=1500):
                await page.click(sel)
                await page.wait_for_timeout(500)
                break
    except:
        pass

async def extract_toc(page, base_url, is_prod=True):
    await page.wait_for_load_state("networkidle")
    await handle_cookies(page)
    await page.wait_for_timeout(2000)

    if is_prod:
        # Prod TOC logic
        try:
            expand_btn = page.locator('.zDocsCollapseExpandButton').first
            if await expand_btn.is_visible(timeout=5000):
                await expand_btn.click()
                await page.wait_for_timeout(3000)
        except: pass
        
        links_data = await page.evaluate('''() => {
            const results = [];
            const container = document.querySelector('.zDocsTocList') || document.querySelector('.zDocsTOC') || document.body;
            Array.from(container.querySelectorAll('a[href]')).forEach(a => {
                const href = a.getAttribute('href');
                const text = a.innerText.trim();
                if (href && text && !href.startsWith('#') && !href.startsWith('javascript')) {
                    results.push({text, href});
                }
            });
            return results;
        }''')
    else:
        # Stage TOC logic
        links_data = await page.evaluate('''() => {
            const results = [];
            document.querySelectorAll('.cmp-navigation__item-link').forEach(a => {
                const href = a.getAttribute('href');
                const text = a.innerText.trim();
                if (href && text && !href.startsWith('#') && !href.startsWith('javascript')) {
                    results.push({text, href});
                }
            });
            return results;
        }''')

    toc = []
    seen = set()
    for item in links_data:
        full_url = urljoin(base_url, item['href']).split('#')[0].split('?')[0]
        if full_url not in seen:
            toc.append({'title': item['text'], 'url': full_url})
            seen.add(full_url)
    return toc

async def get_page_metrics(page, url):
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await handle_cookies(page)
        
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # Remove nav, footer, scripts etc to focus on main content
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
        print(f"   ⚠️ Error fetching {url}: {e}")
        return None

def calculate_text_similarity(text1, text2):
    if not text1 or not text2: return 0
    return difflib.SequenceMatcher(None, text1, text2).ratio()

async def run_deep_validation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        stage_ctx = await browser.new_context(storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None)
        prod_ctx = await browser.new_context()

        # Step 1: Extract TOCs
        print("🔍 Extracting TOCs...")
        p_page = await prod_ctx.new_page()
        await p_page.goto(PROD_URL, wait_until="networkidle", timeout=60000)
        prod_toc = await extract_toc(p_page, PROD_URL, is_prod=True)
        await p_page.close()

        s_page = await stage_ctx.new_page()
        await s_page.goto(STAGE_URL, wait_until="networkidle", timeout=60000)
        stage_toc = await extract_toc(s_page, STAGE_URL, is_prod=False)
        await s_page.close()

        # Match by filename
        stage_by_fn = {get_filename(t['url']): t for t in stage_toc if get_filename(t['url'])}
        
        deep_results = []
        
        # We only compare matched topics for "Deep Content Validation"
        topics_to_check = []
        for p_item in prod_toc:
            fn = get_filename(p_item['url'])
            if fn in stage_by_fn:
                topics_to_check.append({
                    'title': p_item['title'],
                    'prod_url': p_item['url'],
                    'stage_url': stage_by_fn[fn]['url']
                })

        print(f"📋 Starting deep comparison of {len(topics_to_check)} matched topics...")
        
        p_page = await prod_ctx.new_page()
        s_page = await stage_ctx.new_page()
        
        for i, topic in enumerate(topics_to_check[:50]): # Limiting to 50 for now for performance
            print(f"   [{i+1}/{len(topics_to_check)}] Comparing: {topic['title']}")
            
            p_metrics = await get_page_metrics(p_page, topic['prod_url'])
            s_metrics = await get_page_metrics(s_page, topic['stage_url'])
            
            if p_metrics and s_metrics:
                similarity = calculate_text_similarity(p_metrics['text'], s_metrics['text'])
                
                deep_results.append({
                    'Topic': topic['title'],
                    'Text Similarity (%)': round(similarity * 100, 2),
                    'Prod Images': p_metrics['images'],
                    'Stage Images': s_metrics['images'],
                    'Images Parity': '✓ Match' if p_metrics['images'] == s_metrics['images'] else '✗ Mismatch',
                    'Prod Tables': p_metrics['tables'],
                    'Stage Tables': s_metrics['tables'],
                    'Tables Parity': '✓ Match' if p_metrics['tables'] == s_metrics['tables'] else '✗ Mismatch',
                    'Prod Headings': p_metrics['headings'],
                    'Stage Headings': s_metrics['headings'],
                    'Prod URL': topic['prod_url'],
                    'Stage URL': topic['stage_url']
                })
            else:
                deep_results.append({
                    'Topic': topic['title'],
                    'Status': 'Error fetching one or both pages'
                })

        # Generate Report
        df = pd.DataFrame(deep_results)
        
        # Summary
        avg_similarity = df['Text Similarity (%)'].mean() if 'Text Similarity (%)' in df.columns else 0
        img_match = len(df[df['Images Parity'] == '✓ Match']) if 'Images Parity' in df.columns else 0
        tbl_match = len(df[df['Tables Parity'] == '✓ Match']) if 'Tables Parity' in df.columns else 0
        
        summary_data = [
            ['Deep Content Validation Report'],
            ['Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['Topics Checked', len(deep_results)],
            ['Average Content Similarity', f"{round(avg_similarity, 2)}%"],
            ['Image Parity Matches', f"{img_match}/{len(deep_results)}"],
            ['Table Parity Matches', f"{tbl_match}/{len(deep_results)}"],
        ]
        
        with pd.ExcelWriter(REPORT_FILENAME, engine='openpyxl') as writer:
            pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', header=False, index=False)
            df.to_excel(writer, sheet_name='Deep Comparison', index=False)

        print(f"::RESULTS::{json.dumps({'overall': int(avg_similarity), 'images': int(img_match/len(deep_results)*100 if deep_results else 0), 'tables': int(tbl_match/len(deep_results)*100 if deep_results else 0)})}")
        print(f"✅ Deep Validation complete. Report: {REPORT_FILENAME}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_deep_validation())
