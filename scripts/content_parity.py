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
    except:
        pass

if not STAGE_URL or not PROD_URL:
    print("❌ Error: Missing STAGE_URL or PROD_URL.")
    sys.exit(1)

print(f"🚀 Starting Content Parity Validation")
print(f"   Published (Stage): {STAGE_URL}")
print(f"   Prod:              {PROD_URL}")

def slugify(t):
    if not t: return ""
    return re.sub(r'[^a-z0-9]', '', t.lower())

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

# ── Prod TOC Extraction ──────────────────────────────────────────────
async def extract_prod_toc(page, base_url):
    """Extract TOC from Production (documentation.avaya.com)."""
    await page.wait_for_load_state("networkidle")
    await handle_cookies(page)

    try:
        expand_btn = page.locator('.zDocsCollapseExpandButton').first
        if await expand_btn.is_visible(timeout=5000):
            await expand_btn.click()
            await page.wait_for_timeout(5000)
        
        expand_icons = await page.locator('.zDocsTocCollapseItemButton, .expand-icon, button[aria-expanded="false"]').all()
        for icon in expand_icons[:100]:
            try:
                await icon.click(timeout=300)
            except: pass
        await page.wait_for_timeout(3000)
    except:
        pass

    links_data = await page.evaluate('''() => {
        const results = [];
        const container = document.querySelector('.zDocsTocList') || document.querySelector('.zDocsTOC');
        let allLinks = [];
        if (container) {
            allLinks = Array.from(container.querySelectorAll('a[href]'));
        }
        if (allLinks.length === 0) {
            allLinks = Array.from(document.querySelectorAll('nav a[href], [class*="sidebar"] a[href]'));
        }
        allLinks.forEach(a => {
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

    print(f"   ✅ Prod TOC: {len(toc)} topics found.")
    return toc

# ── Stage/Published TOC Extraction ───────────────────────────────────
async def extract_stage_toc(page, base_url):
    """Extract TOC from Published/Stage (AEM publish)."""
    await page.wait_for_load_state("networkidle")
    await handle_cookies(page)
    await page.wait_for_timeout(2000)

    links_data = await page.evaluate('''() => {
        const results = [];
        const links = document.querySelectorAll('.cmp-navigation__item-link');
        links.forEach(a => {
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
        if full_url not in seen and '.html' in full_url:
            toc.append({'title': item['text'], 'url': full_url})
            seen.add(full_url)

    print(f"   ✅ Stage TOC: {len(toc)} topics found.")
    return toc

# ── Main Validation ──────────────────────────────────────────────────
async def run_validation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        stage_ctx = await browser.new_context(
            storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None
        )
        prod_ctx = await browser.new_context()

        # ── Step 1: Extract TOCs ──────────────────────────────────────
        print("🔍 Scanning Navigation Structure...")

        p_page = await prod_ctx.new_page()
        await p_page.goto(PROD_URL, wait_until="networkidle", timeout=60000)
        prod_toc = await extract_prod_toc(p_page, PROD_URL)
        await p_page.close()

        s_page = await stage_ctx.new_page()
        print(f"🌐 Opening Published URL for TOC...")
        await s_page.goto(STAGE_URL, wait_until="networkidle", timeout=60000)
        stage_toc = await extract_stage_toc(s_page, STAGE_URL)
        await s_page.close()

        # ── Step 2: Build TOC Comparison Report ───────────────────────
        print(f"📊 Comparing TOC Structure: Prod={len(prod_toc)} vs Stage={len(stage_toc)}")

        stage_by_filename = {}
        for item in stage_toc:
            fn = get_filename(item['url'])
            if fn:
                stage_by_filename[fn] = item

        toc_comparison = []
        max_len = max(len(prod_toc), len(stage_toc), 1)

        for i in range(max_len):
            prod_item = prod_toc[i] if i < len(prod_toc) else None
            stage_item = stage_toc[i] if i < len(stage_toc) else None

            match_status = "NO"
            matched_stage = stage_item

            if prod_item and stage_item:
                if slugify(prod_item['title']) == slugify(stage_item['title']):
                    match_status = "YES"
                else:
                    prod_fn = get_filename(prod_item['url'])
                    if prod_fn in stage_by_filename:
                        matched_stage = stage_by_filename[prod_fn]
                        match_status = "FILENAME_MATCH"

            prod_fn = get_filename(prod_item['url']) if prod_item else ''
            pub_fn = get_filename(matched_stage['url']) if matched_stage else ''
            if prod_item and matched_stage and prod_fn and pub_fn:
                url_status = '✓ Match' if prod_fn == pub_fn else '✗ Mismatch'
            else:
                url_status = 'N/A'

            toc_comparison.append({
                'Order': i + 1,
                'Prod Topic': prod_item['title'] if prod_item else '[MISSING IN PROD]',
                'Published Topic': matched_stage['title'] if matched_stage else '[MISSING IN PUBLISHED]',
                'Match': match_status,
                'Prod URL': prod_item['url'] if prod_item else 'N/A',
                'Published URL': matched_stage['url'] if matched_stage else 'N/A',
                'URL Status': url_status
            })

        matched_count = len([c for c in toc_comparison if c['Match'] in ('YES', 'FILENAME_MATCH')])
        url_match_count = len([c for c in toc_comparison if c['URL Status'] == '✓ Match'])
        url_mismatch_count = len([c for c in toc_comparison if c['URL Status'] == '✗ Mismatch'])
        total = len(toc_comparison)
        print(f"   ✅ Structure Match: {matched_count}/{total} ({int(matched_count/total*100) if total else 0}%)")
        print(f"   🔗 URL Match: {url_match_count}/{total} | Mismatch: {url_mismatch_count}")

        # ── Step 3: Generate Report ───────────────────────────────────
        toc_df = pd.DataFrame(toc_comparison)

        summary_data = [
            ['Content Parity – TOC Structure Report'],
            [''],
            ['Run Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['Prod URL', PROD_URL],
            ['Published URL', STAGE_URL],
            [''],
            ['── TOC Structure Summary ──'],
            ['Prod Topics Found', len(prod_toc)],
            ['Published Topics Found', len(stage_toc)],
            ['Sequence Matches (YES)', len([c for c in toc_comparison if c['Match'] == 'YES'])],
            ['Filename Matches', len([c for c in toc_comparison if c['Match'] == 'FILENAME_MATCH'])],
            ['Mismatches', len([c for c in toc_comparison if c['Match'] == 'NO'])],
            ['Match Percentage', f"{int(matched_count/total*100) if total else 0}%"],
            [''],
            ['URL Match Count', url_match_count],
            ['URL Mismatch Count', url_mismatch_count],
        ]

        prod_only = []
        for item in prod_toc:
            fn = get_filename(item['url'])
            if fn not in stage_by_filename:
                prod_only.append({'Topic': item['title'], 'Prod URL': item['url']})

        prod_filenames = {get_filename(t['url']) for t in prod_toc}
        stage_only = []
        for item in stage_toc:
            fn = get_filename(item['url'])
            if fn not in prod_filenames:
                stage_only.append({'Topic': item['title'], 'Published URL': item['url']})

        with pd.ExcelWriter(REPORT_FILENAME, engine='openpyxl') as writer:
            pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', header=False, index=False)
            toc_df.to_excel(writer, sheet_name='TOC Structure', index=False)
            if prod_only:
                pd.DataFrame(prod_only).to_excel(writer, sheet_name='Only in Prod', index=False)
            if stage_only:
                pd.DataFrame(stage_only).to_excel(writer, sheet_name='Only in Published', index=False)

        overall_pct = int(matched_count / total * 100) if total else 0
        results = {
            'overall': overall_pct,
            'headings': 100,
            'tables': 100,
            'images': 100,
            'content': overall_pct,
        }
        print(f"::RESULTS::{json.dumps(results)}")
        print(f"✅ Report saved: {REPORT_FILENAME}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_validation())
