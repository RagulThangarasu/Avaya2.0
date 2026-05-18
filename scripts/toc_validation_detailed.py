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

TEST_URLS_PATH = os.path.join(os.getcwd(), 'config', 'test-urls.json')
AUTH_STATE_PATH = os.path.join(os.getcwd(), 'auth-sessions', 'storage-state.json')

STAGE_URL = os.environ.get('STAGE_URL')
PROD_URL = os.environ.get('PROD_URL')
REPORT_FILENAME = os.environ.get('REPORT_FILENAME') or os.path.join(UI_REPORTS_DIR, f'toc-validation-{int(datetime.now().timestamp())}.xlsx')

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

print(f"🚀 Starting TOC Validation with Detailed Analysis")
print(f"   Stage:      {STAGE_URL}")
print(f"   Production: {PROD_URL}")

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

async def extract_prod_toc(page, base_url):
    """Extract TOC from Production with order."""
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

async def extract_stage_toc(page, base_url):
    """Extract TOC from Stage with order."""
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

async def run_validation():
    """Validate TOC with detailed analysis: match, mismatch, sequence, order."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        stage_ctx = await browser.new_context(
            storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None
        )
        prod_ctx = await browser.new_context()

        print("🔍 Extracting TOC...")
        
        p_page = await prod_ctx.new_page()
        await p_page.goto(PROD_URL, wait_until="networkidle", timeout=60000)
        prod_toc = await extract_prod_toc(p_page, PROD_URL)
        await p_page.close()

        s_page = await stage_ctx.new_page()
        await s_page.goto(STAGE_URL, wait_until="networkidle", timeout=60000)
        stage_toc = await extract_stage_toc(s_page, STAGE_URL)
        await s_page.close()

        # Build maps for matching
        prod_by_filename = {get_filename(t['url']): (i, t) for i, t in enumerate(prod_toc)}
        stage_by_filename = {get_filename(t['url']): (i, t) for i, t in enumerate(stage_toc)}
        prod_by_slug = {slugify(t['title']): (i, t) for i, t in enumerate(prod_toc)}
        stage_by_slug = {slugify(t['title']): (i, t) for i, t in enumerate(stage_toc)}

        print(f"📊 Analyzing TOC: Prod={len(prod_toc)} vs Stage={len(stage_toc)}")

        # Detailed comparison
        comparison_results = []
        matched_pairs = set()

        for prod_idx, prod_item in enumerate(prod_toc):
            prod_fn = get_filename(prod_item['url'])
            prod_slug = slugify(prod_item['title'])
            
            stage_idx = None
            stage_item = None
            match_type = 'MISSING'
            sequence_status = 'N/A'
            order_status = 'N/A'

            # Try to find match
            if prod_fn in stage_by_filename:
                stage_idx, stage_item = stage_by_filename[prod_fn]
                match_type = 'MATCH'
                matched_pairs.add(prod_fn)
            elif prod_slug in stage_by_slug:
                stage_idx, stage_item = stage_by_slug[prod_slug]
                match_type = 'TITLE_MATCH'
                matched_pairs.add(prod_fn)
            else:
                stage_item = None

            # Check sequence
            if stage_item:
                if stage_idx == prod_idx:
                    sequence_status = 'CORRECT'
                    order_status = '✅ Same Position'
                else:
                    sequence_status = 'WRONG'
                    order_status = f'⚠️ Prod[{prod_idx+1}] vs Stage[{stage_idx+1}]'

            comparison_results.append({
                'Prod Order': prod_idx + 1,
                'Prod Title': prod_item['title'],
                'Stage Order': stage_idx + 1 if stage_idx is not None else '-',
                'Stage Title': stage_item['title'] if stage_item else '[MISSING]',
                'Match Type': match_type,
                'Sequence': sequence_status,
                'Position Status': order_status,
                'Prod URL': prod_item['url'],
                'Stage URL': stage_item['url'] if stage_item else 'N/A',
            })

        # Find stage-only items
        for stage_fn, (stage_idx, stage_item) in stage_by_filename.items():
            if stage_fn not in matched_pairs:
                comparison_results.append({
                    'Prod Order': '-',
                    'Prod Title': '[MISSING IN PROD]',
                    'Stage Order': stage_idx + 1,
                    'Stage Title': stage_item['title'],
                    'Match Type': 'EXTRA',
                    'Sequence': 'N/A',
                    'Position Status': 'Only in Stage',
                    'Prod URL': 'N/A',
                    'Stage URL': stage_item['url'],
                })

        # Calculate stats
        total_prod = len(prod_toc)
        total_stage = len(stage_toc)
        matches = len([r for r in comparison_results if r['Match Type'] in ('MATCH', 'TITLE_MATCH')])
        mismatches = len([r for r in comparison_results if r['Match Type'] in ('MISSING', 'EXTRA')])
        sequence_correct = len([r for r in comparison_results if r['Sequence'] == 'CORRECT'])
        sequence_wrong = len([r for r in comparison_results if r['Sequence'] == 'WRONG'])

        print(f"\n{'='*70}")
        print(f"📈 TOC Validation Report")
        print(f"{'='*70}")
        print(f"✅ Matches: {matches}/{total_prod} ({int(matches/max(total_prod,1)*100)}%)")
        print(f"❌ Mismatches: {mismatches}")
        print(f"📍 Sequence Correct: {sequence_correct}/{total_prod}")
        print(f"⚠️  Sequence Wrong: {sequence_wrong}/{total_prod}")
        print(f"{'='*70}\n")

        # Generate Excel report
        df = pd.DataFrame(comparison_results)
        
        summary_data = [
            ['TOC Validation Report'],
            [''],
            ['Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['Prod URL', PROD_URL],
            ['Stage URL', STAGE_URL],
            [''],
            ['── Summary Statistics ──'],
            ['Prod Topics', total_prod],
            ['Stage Topics', total_stage],
            ['Total Matches', matches],
            ['Match Percentage', f"{int(matches/max(total_prod,1)*100)}%"],
            ['Mismatches (Missing/Extra)', mismatches],
            [''],
            ['── Sequence Analysis ──'],
            ['Correct Position', sequence_correct],
            ['Wrong Position', sequence_wrong],
            ['Position Accuracy', f"{int(sequence_correct/max(total_prod,1)*100)}%"],
        ]

        os.makedirs(os.path.dirname(REPORT_FILENAME), exist_ok=True)
        
        with pd.ExcelWriter(REPORT_FILENAME, engine='openpyxl') as writer:
            pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', header=False, index=False)
            df.to_excel(writer, sheet_name='Detailed Comparison', index=False)
            
            # Add sheets for different issue types
            matches_df = df[df['Match Type'].isin(['MATCH', 'TITLE_MATCH'])]
            if len(matches_df) > 0:
                matches_df.to_excel(writer, sheet_name='Matched Items', index=False)
            
            missing_df = df[df['Match Type'] == 'MISSING']
            if len(missing_df) > 0:
                missing_df.to_excel(writer, sheet_name='Missing in Stage', index=False)
            
            extra_df = df[df['Match Type'] == 'EXTRA']
            if len(extra_df) > 0:
                extra_df.to_excel(writer, sheet_name='Extra in Stage', index=False)
            
            sequence_wrong_df = df[df['Sequence'] == 'WRONG']
            if len(sequence_wrong_df) > 0:
                sequence_wrong_df.to_excel(writer, sheet_name='Sequence Mismatches', index=False)

        results = {
            'overall': int(matches/max(total_prod,1)*100),
            'matches': matches,
            'mismatches': mismatches,
            'sequence_correct': sequence_correct,
            'sequence_wrong': sequence_wrong,
            'match_rate': f"{int(matches/max(total_prod,1)*100)}%",
            'sequence_rate': f"{int(sequence_correct/max(total_prod,1)*100)}%",
        }

        print(f"✅ Report saved: {REPORT_FILENAME}")
        print(f"::RESULTS::{json.dumps(results)}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_validation())
