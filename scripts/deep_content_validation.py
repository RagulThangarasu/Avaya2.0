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
print(f"   Stage (Publish): {STAGE_URL}")
print(f"   Production:      {PROD_URL}")

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

# ── Content Extraction & Comparison ──────────────────────────────────
def extract_text_content(page_html):
    """Extract and normalize text content from HTML."""
    try:
        clean = re.sub(r'<script[^>]*>.*?</script>', '', page_html, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<!--.*?-->', '', clean, flags=re.DOTALL)
        clean = re.sub(r'\s+', ' ', clean).strip()
        
        body_match = re.search(r'<body[^>]*>(.*)</body>', clean, re.IGNORECASE | re.DOTALL)
        if body_match:
            return body_match.group(1)
        return clean
    except Exception as e:
        return page_html

async def get_page_text(page, url):
    """Fetch and extract text from page (h2, h3, paragraphs only - skip header, footer, nav, h1)."""
    try:
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await handle_cookies(page)
        await page.wait_for_timeout(300)
        
        # Extract only h2, h3, and paragraph content (skip header, footer, nav, h1)
        text = await page.evaluate('''() => {
            const results = [];
            
            // Extract h2, h3, and p - check they're not in excluded areas
            document.querySelectorAll('h2, h3, p').forEach(el => {
                // Skip if in header, footer, or nav
                let parent = el.parentElement;
                let inExcluded = false;
                for (let i = 0; i < 10; i++) {
                    if (!parent) break;
                    const tag = parent.tagName.toLowerCase();
                    if (tag === 'header' || tag === 'footer' || tag === 'nav') {
                        inExcluded = true;
                        break;
                    }
                    parent = parent.parentElement;
                }
                
                if (!inExcluded) {
                    const text = el.innerText.trim();
                    const noise = ["was this page helpful?", "helpful?", "options", "export", "feedback", "back", "network", "j189", "locking", "better"];
                    if (text && text.length > 0) {
                        const lowText = text.toLowerCase();
                        if (!noise.some(n => lowText === n || (lowText.includes(n) && text.length < 50))) {
                            results.push(text);
                        }
                    }
                }
            });
            
            return results.join(' ');
        }''')
        
        return {
            'url': url,
            'title': await page.title(),
            'text': text.strip(),
            'status': 'success'
        }
    except Exception as e:
        return {
            'url': url,
            'title': 'Error',
            'text': '',
            'status': 'error',
            'error': str(e)
        }

def compare_page_content(stage_text, prod_text):
    """Compare content and return detailed differences."""
    if not stage_text or not prod_text:
        return {
            'match_type': 'missing',
            'similarity': 0.0,
            'stage_length': len(stage_text) if stage_text else 0,
            'prod_length': len(prod_text) if prod_text else 0,
            'diff': 'Content is empty'
        }
    
    # Normalize
    stage_norm = ' '.join(stage_text.lower().split())
    prod_norm = ' '.join(prod_text.lower().split())
    
    if stage_norm == prod_norm:
        return {
            'match_type': 'match',
            'similarity': 1.0,
            'stage_length': len(stage_norm),
            'prod_length': len(prod_norm),
            'diff': 'Perfect match'
        }
    
    # Split into words for detailed comparison
    stage_words = stage_norm.split()
    prod_words = prod_norm.split()
    stage_set = set(stage_words)
    prod_set = set(prod_words)
    
    # Find differences
    missing_in_prod = stage_set - prod_set  # In stage but not in prod
    extra_in_prod = prod_set - stage_set     # In prod but not in stage
    common = stage_set & prod_set
    
    # Calculate similarity
    total = stage_set | prod_set
    similarity = len(common) / len(total) if total else 0.0
    
    # Create detailed diff
    missing_sample = ' '.join(list(missing_in_prod)[:5]) if missing_in_prod else '(none)'
    extra_sample = ' '.join(list(extra_in_prod)[:5]) if extra_in_prod else '(none)'
    
    diff_info = f"Stage: {len(stage_words)} words | Prod: {len(prod_words)} words | Missing in Prod: {len(missing_in_prod)} terms | Extra in Prod: {len(extra_in_prod)} terms"
    
    if similarity >= 0.95:
        match_type = 'match'
    elif similarity >= 0.80:
        match_type = 'partial'
    else:
        match_type = 'mismatch'
    
    return {
        'match_type': match_type,
        'similarity': similarity,
        'stage_length': len(stage_norm),
        'prod_length': len(prod_norm),
        'stage_words': len(stage_words),
        'prod_words': len(prod_words),
        'missing_in_prod_count': len(missing_in_prod),
        'extra_in_prod_count': len(extra_in_prod),
        'missing_sample': missing_sample,
        'extra_sample': extra_sample,
        'diff': diff_info
    }

async def validate_content():
    """Main validation logic - validates all TOC items in parallel."""
    results = []
    match_stats = {'match': 0, 'partial': 0, 'mismatch': 0, 'error': 0}
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        # Stage context
        stage_ctx = await browser.new_context(
            storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None
        )
        # Prod context
        prod_ctx = await browser.new_context()
        
        print("\n🔍 Scanning Navigation Structure...")
        
        # Extract TOCs
        p_page = await prod_ctx.new_page()
        await p_page.goto(PROD_URL, wait_until="networkidle", timeout=60000)
        prod_toc = await extract_prod_toc(p_page, PROD_URL)
        await p_page.close()
        
        s_page = await stage_ctx.new_page()
        print(f"🌐 Opening Published URL for TOC...")
        await s_page.goto(STAGE_URL, wait_until="networkidle", timeout=60000)
        stage_toc = await extract_stage_toc(s_page, STAGE_URL)
        await s_page.close()
        
        # Build URL mapping
        print(f"\n📊 Matching TOC: Prod={len(prod_toc)} vs Stage={len(stage_toc)}")
        
        # Build URL mapping with support for duplicate filenames (store list of items per filename)
        stage_by_filename = {}
        for idx, item in enumerate(stage_toc):
            fn = get_filename(item['url'])
            if fn:
                if fn not in stage_by_filename:
                    stage_by_filename[fn] = []
                stage_by_filename[fn].append({**item, 'index': idx + 1, 'used': False})
        
        # Parallel validation function
        async def validate_single_page(prod_item, idx, total):
            """Validate a single page."""
            prod_url = prod_item['url']
            prod_fn = get_filename(prod_url)
            
            # Find matching stage URL (pick first unused item with same filename)
            stage_url = None
            matched_stage = None
            candidates = stage_by_filename.get(prod_fn, [])
            for cand in candidates:
                if not cand['used']:
                    matched_stage = cand
                    cand['used'] = True
                    stage_url = cand['url']
                    break
            
            if not stage_url:
                # Missing in stage
                result = {
                    'Prod Sequence': idx,
                    'Stage Sequence': 'N/A',
                    'Content Match': '❌',
                    'Similarity %': '0%',
                    'Prod Title': prod_item['title'],
                    'Stage Title': '[MISSING]',
                    'Prod URL': prod_url,
                    'Stage URL': 'N/A'
                }
                return 'mismatch', result
            
            try:
                # Create new pages for this validation
                s_page = await stage_ctx.new_page()
                p_page = await prod_ctx.new_page()
                
                stage_data = await get_page_text(s_page, stage_url)
                prod_data = await get_page_text(p_page, prod_url)
                
                await s_page.close()
                await p_page.close()
                
                if stage_data['status'] == 'error' or prod_data['status'] == 'error':
                    result = {
                        'Prod Sequence': idx,
                        'Stage Sequence': matched_stage['index'],
                        'Content Match': '❌',
                        'Similarity %': '0%',
                        'Prod Title': prod_data.get('title', 'Error'),
                        'Stage Title': stage_data.get('title', 'Error'),
                        'Prod URL': prod_url,
                        'Stage URL': stage_url
                    }
                    return 'error', result
                
                # Compare
                comparison = compare_page_content(stage_data['text'], prod_data['text'])
                # Force to Match/Mismatch
                is_match = comparison['match_type'] == 'match' and idx == matched_stage['index']
                similarity = comparison['similarity']
                
                result = {
                    'Prod Sequence': idx,
                    'Stage Sequence': matched_stage['index'],
                    'Content Match': '✅' if is_match else '❌',
                    'Similarity %': f"{similarity * 100:.1f}%",
                    'Prod Title': prod_data['title'],
                    'Stage Title': stage_data['title'],
                    'Prod URL': prod_url,
                    'Stage URL': stage_url
                }
                
                # Simple progress log
                emoji = '✅' if is_match else '❌'
                print(f"  [{idx}/{total}] {prod_item['title'][:50]:50s} {emoji} ({similarity*100:.1f}%)")
                
                return 'match' if is_match else 'mismatch', result
                
            except Exception as e:
                print(f"  [{idx}/{total}] {prod_item['title'][:40]:40s} 🚨 EXCEPTION")
                result = {
                    'Prod Sequence': idx,
                    'Stage Sequence': matched_stage['index'] if matched_stage else 'N/A',
                    'Content Match': '❌',
                    'Similarity %': '0%',
                    'Prod Title': prod_item['title'],
                    'Stage Title': 'Error',
                    'Prod URL': prod_url,
                    'Stage URL': stage_url or 'N/A'
                }
                return 'error', result
        
        # Validate in batches (parallel)
        print(f"\n📄 Validating content for {len(prod_toc)} topics (parallel)...")
        
        # Process in batches of 10 concurrent validations (faster)
        BATCH_SIZE = 10
        for batch_start in range(0, len(prod_toc), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(prod_toc))
            batch = prod_toc[batch_start:batch_end]
            
            # Run concurrent validations
            tasks = [
                validate_single_page(prod_toc[idx], idx + 1, len(prod_toc))
                for idx in range(batch_start, batch_end)
            ]
            
            batch_results = await asyncio.gather(*tasks)
            
            for match_type, result in batch_results:
                if match_type != 'ignore':
                    match_stats[match_type] += 1
                    results.append(result)
        
        # Add items that exist in Stage but NOT in Prod (Extra items)
        for fn, candidates in stage_by_filename.items():
            for cand in candidates:
                if not cand['used']:
                    result = {
                        'Prod Sequence': 'N/A',
                        'Stage Sequence': cand['index'],
                        'Content Match': '❌',
                        'Similarity %': '0%',
                        'Prod Title': '[MISSING]',
                        'Stage Title': cand['title'],
                        'Prod URL': 'N/A',
                        'Stage URL': cand['url']
                    }
                    results.append(result)
                    match_stats['mismatch'] += 1
        
        await prod_ctx.close()
        await stage_ctx.close()
        await browser.close()
    
    return results, match_stats, stage_toc, prod_toc

async def main():
    """Run validation and generate report."""
    try:
        print(f"\n{'='*60}")
        results, match_stats, stage_toc, prod_toc = await validate_content()
        
        # Calculate overall percentage
        total = sum(match_stats.values())
        overall_pct = (match_stats['match'] / total * 100) if total > 0 else 0
        
        print(f"\n{'='*60}")
        print(f"  📊 Results Summary")
        print(f"{'='*60}")
        print(f"  ✅ Match:    {match_stats['match']}")
        print(f"  ❌ Mismatch: {match_stats['mismatch']}")
        print(f"  📈 Overall:  {overall_pct:.1f}%")
        print(f"{'='*60}\n")

        # Create DataFrame
        df = pd.DataFrame(results)

        # Summary DataFrame
        summary_df = pd.DataFrame([
            ['Deep Content Validation Report'],
            ['Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['Stage URL', STAGE_URL],
            ['Prod URL', PROD_URL],
            [''],
            ['✅ Match', match_stats['match']],
            ['❌ Mismatch', match_stats['mismatch']],
            ['Overall Score', f"{overall_pct:.1f}%"],
            [''],
            ['Stage Topics', len(stage_toc)],
            ['Prod Topics', len(prod_toc)]
        ])

        # TOC DataFrames
        stage_toc_df = pd.DataFrame([
            {'#': i + 1, 'Title': t['title'], 'URL': t['url']} 
            for i, t in enumerate(stage_toc)
        ])
        prod_toc_df = pd.DataFrame([
            {'#': i + 1, 'Title': t['title'], 'URL': t['url']} 
            for i, t in enumerate(prod_toc)
        ])

        # Save multi-tab report
        os.makedirs(os.path.dirname(REPORT_FILENAME), exist_ok=True)
        with pd.ExcelWriter(REPORT_FILENAME, engine='openpyxl') as writer:
            summary_df.to_excel(writer, sheet_name='Summary', header=False, index=False)
            df.to_excel(writer, sheet_name='Comparison', index=False)
            stage_toc_df.to_excel(writer, sheet_name='Stage TOC', index=False)
            prod_toc_df.to_excel(writer, sheet_name='Prod TOC', index=False)

            # 5. Structure (Side-by-side raw sequences)
            max_len = max(len(stage_toc), len(prod_toc))
            structure_data = []
            for i in range(max_len):
                s_item = stage_toc[i] if i < len(stage_toc) else {'title': '', 'url': ''}
                p_item = prod_toc[i] if i < len(prod_toc) else {'title': '', 'url': ''}
                structure_data.append({
                    'Stage #': i + 1 if i < len(stage_toc) else '',
                    'Stage Title': s_item['title'],
                    'Prod #': i + 1 if i < len(prod_toc) else '',
                    'Prod Title': p_item['title']
                })
            pd.DataFrame(structure_data).to_excel(writer, sheet_name='Structure', index=False)

        print(f"✓ Multi-tab report saved to: {REPORT_FILENAME}")
        
        # Output results in server format
        results_json = {
            'match': match_stats['match'],
            'mismatch': match_stats['mismatch'],
            'overall': overall_pct
        }
        print(f"::RESULTS::{json.dumps(results_json)}")
        print(f"\n✅ Deep content validation complete!")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 2

if __name__ == '__main__':
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
