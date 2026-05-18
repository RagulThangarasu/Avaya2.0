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
REPORT_MD_PATH = os.path.join(REPORTS_DIR, 'formatting_and_links_report.md')
REPORT_XLSX_PATH = os.path.join(REPORTS_DIR, 'formatting_and_links_report.xlsx')

# Load fallback URLs if not in environment
if not STAGE_URL or not PROD_URL:
    try:
        with open(TEST_URLS_PATH, 'r') as f:
            config = json.load(f)
            STAGE_URL = STAGE_URL or config.get('stage')
            PROD_URL = PROD_URL or config.get('production')
    except Exception as e:
        pass

if not STAGE_URL or not PROD_URL:
    print("❌ Error: Missing STAGE_URL or PROD_URL.")
    sys.exit(1)

print(f"🚀 Starting Formatting & Related Links Parity Engine")
print(f"   Stage (Publish): {STAGE_URL}")
print(f"   Production:      {PROD_URL}")
print(f"   Markdown Output: {REPORT_MD_PATH}")
print(f"   Excel Output:    {REPORT_XLSX_PATH}\n")

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

# ── TOC Extraction ───────────────────────────────────────────────────
async def extract_prod_toc(page, base_url):
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

# ── Formatting & Links Extraction ────────────────────────────────────
async def extract_page_formatting_and_links(page, url, is_prod=False):
    """Navigates to URL and extracts all bold, italic text and related links."""
    try:
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await handle_cookies(page)
        await page.wait_for_timeout(300)
        
        # Determine root container
        root_selector = '.zDocsTopicPageBody' if is_prod else '.topic-renderer__content'
        
        # Wait for content container
        try:
            await page.wait_for_selector(root_selector, timeout=5000)
        except:
            pass
            
        data = await page.evaluate('''async (selector) => {
            const root = document.querySelector(selector) || document.body;
            
            function isBoldElement(el) {
                const tag = el.tagName.toLowerCase();
                if (tag === 'b' || tag === 'strong') return true;
                const style = window.getComputedStyle(el);
                return style.fontWeight === 'bold' || parseInt(style.fontWeight) >= 700;
            }

            function isItalicElement(el) {
                const tag = el.tagName.toLowerCase();
                if (tag === 'i' || tag === 'em') return true;
                const style = window.getComputedStyle(el);
                return style.fontStyle === 'italic';
            }

            const boldSegments = [];
            const italicSegments = [];

            // Traverse the content under root to find bold and italic elements
            const walker = document.createTreeWalker(
                root,
                NodeFilter.SHOW_ELEMENT,
                {
                    acceptNode: function(node) {
                        const tag = node.tagName.toLowerCase();
                        const className = node.className ? node.className.toLowerCase() : '';
                        const id = node.id ? node.id.toLowerCase() : '';
                        
                        // Reject boilerplate/sidebar/header/footer
                        if (['header', 'footer', 'nav', 'script', 'style'].includes(tag) || 
                            className.includes('breadcrumb') || className.includes('toolbar') || 
                            className.includes('metadata') || className.includes('search') ||
                            id.includes('search') || id.includes('filter')) {
                            return NodeFilter.FILTER_REJECT;
                        }
                        return NodeFilter.FILTER_ACCEPT;
                    }
                }
            );

            let currentNode = walker.nextNode();
            while (currentNode) {
                const text = currentNode.innerText ? currentNode.innerText.trim() : '';
                if (text) {
                    if (isBoldElement(currentNode)) {
                        // Check if top-most bold
                        let parent = currentNode.parentElement;
                        let parentIsBold = false;
                        while (parent && parent !== root) {
                            if (isBoldElement(parent)) {
                                parentIsBold = true;
                                break;
                            }
                            parent = parent.parentElement;
                        }
                        if (!parentIsBold) {
                            const cleanText = text.replace(/\\s+/g, ' ').trim();
                            if (cleanText.length > 0) {
                                boldSegments.push(cleanText);
                            }
                        }
                    }

                    if (isItalicElement(currentNode)) {
                        // Check if top-most italic
                        let parent = currentNode.parentElement;
                        let parentIsItalic = false;
                        while (parent && parent !== root) {
                            if (isItalicElement(parent)) {
                                parentIsItalic = true;
                                break;
                            }
                            parent = parent.parentElement;
                        }
                        if (!parentIsItalic) {
                            const cleanText = text.replace(/\\s+/g, ' ').trim();
                            if (cleanText.length > 0) {
                                italicSegments.push(cleanText);
                            }
                        }
                    }
                }
                currentNode = walker.nextNode();
            }

            // Extract Related Links
            const relatedLinks = [];
            const relatedSelectors = [
                '.related-links', '.zDocsRelatedLinks', '.related-links-list', 
                '#related-links', '.related-links-container', '.cmp-related-links'
            ];
            
            relatedSelectors.forEach(sel => {
                root.querySelectorAll(sel).forEach(container => {
                    container.querySelectorAll('a[href]').forEach(a => {
                        const href = a.getAttribute('href');
                        const linkText = a.innerText.trim();
                        if (href && linkText && !href.startsWith('#') && !href.startsWith('javascript')) {
                            relatedLinks.push({ text: linkText, href: href });
                        }
                    });
                });
            });

            // Fallback: search by section heading
            if (relatedLinks.length === 0) {
                const headings = root.querySelectorAll('h2, h3, h4, h5, div');
                headings.forEach(h => {
                    const hText = h.innerText.trim().toLowerCase();
                    if (hText.includes('related information') || hText.includes('related links') || hText.includes('related topics') || hText.includes('related concepts') || hText.includes('related tasks') || hText.includes('related references')) {
                        let next = h.nextElementSibling;
                        while (next && !['h2', 'h3', 'h4', 'h5'].includes(next.tagName.toLowerCase())) {
                            next.querySelectorAll('a[href]').forEach(a => {
                                const href = a.getAttribute('href');
                                const linkText = a.innerText.trim();
                                if (href && linkText && !href.startsWith('#') && !href.startsWith('javascript')) {
                                    relatedLinks.push({ text: linkText, href: href });
                                }
                            });
                            next = next.nextElementSibling;
                        }
                    }
                });
            }

            return {
                bold: boldSegments,
                italic: italicSegments,
                relatedLinks: relatedLinks
            };
        }''', root_selector)
        
        return {
            'url': url,
            'title': await page.title(),
            'bold': data['bold'],
            'italic': data['italic'],
            'related_links': data['relatedLinks'],
            'status': 'success'
        }
    except Exception as e:
        return {
            'url': url,
            'title': 'Error',
            'bold': [],
            'italic': [],
            'related_links': [],
            'status': 'error',
            'error': str(e)
        }

# ── Parity Comparer ──────────────────────────────────────────────────
def compare_formatting_and_links(stage_data, prod_data):
    """Compares extracted formatting tags and related links."""
    # Compare Bold Text
    s_bold = stage_data['bold']
    p_bold = prod_data['bold']
    bold_matches = [x for x in p_bold if x in s_bold]
    bold_missing_in_stage = [x for x in p_bold if x not in s_bold]
    bold_extra_in_stage = [x for x in s_bold if x not in p_bold]
    bold_similarity = len(bold_matches) / max(len(p_bold), 1) if p_bold else 1.0
    bold_ok = len(bold_missing_in_stage) == 0

    # Compare Italic Text
    s_italic = stage_data['italic']
    p_italic = prod_data['italic']
    italic_matches = [x for x in p_italic if x in s_italic]
    italic_missing_in_stage = [x for x in p_italic if x not in s_italic]
    italic_extra_in_stage = [x for x in s_italic if x not in p_italic]
    italic_similarity = len(italic_matches) / max(len(p_italic), 1) if p_italic else 1.0
    italic_ok = len(italic_missing_in_stage) == 0

    # Compare Related Links (Production links must be in Stage)
    s_links = stage_data['related_links']
    p_links = prod_data['related_links']
    
    missing_links = []
    matched_links = []
    
    for pl in p_links:
        pl_fn = get_filename(pl['href'])
        pl_txt = pl['text'].lower()
        
        # Match by filename or by text content
        found = False
        for sl in s_links:
            sl_fn = get_filename(sl['href'])
            sl_txt = sl['text'].lower()
            
            if (pl_fn and sl_fn and pl_fn == sl_fn) or pl_txt == sl_txt or slugify(pl['text']) == slugify(sl['text']):
                found = True
                matched_links.append((pl, sl))
                break
        
        if not found:
            missing_links.append(pl)

    links_ok = len(missing_links) == 0
    links_similarity = len(matched_links) / max(len(p_links), 1) if p_links else 1.0

    overall_ok = bold_ok and italic_ok and links_ok

    return {
        'overall_ok': overall_ok,
        # Bold details
        'bold_ok': bold_ok,
        'prod_bold_count': len(p_bold),
        'stage_bold_count': len(s_bold),
        'bold_missing_in_stage': bold_missing_in_stage,
        'bold_extra_in_stage': bold_extra_in_stage,
        'bold_similarity': bold_similarity,
        # Italic details
        'italic_ok': italic_ok,
        'prod_italic_count': len(p_italic),
        'stage_italic_count': len(s_italic),
        'italic_missing_in_stage': italic_missing_in_stage,
        'italic_extra_in_stage': italic_extra_in_stage,
        'italic_similarity': italic_similarity,
        # Related Link details
        'links_ok': links_ok,
        'prod_links_count': len(p_links),
        'stage_links_count': len(s_links),
        'missing_links': missing_links,
        'links_similarity': links_similarity
    }

async def validate_formatting_and_links():
    results = []
    stats = {
        'total': 0,
        'passed': 0,
        'failed': 0,
        'bold_failed': 0,
        'italic_failed': 0,
        'links_failed': 0,
        'error': 0
    }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        # Load authenticated session for Stage Publish if available
        stage_ctx = await browser.new_context(
            storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None
        )
        prod_ctx = await browser.new_context()
        
        print("🔍 Crawling Table of Contents from Stage and Production...")
        p_page = await prod_ctx.new_page()
        await p_page.goto(PROD_URL, wait_until="networkidle", timeout=60000)
        prod_toc = await extract_prod_toc(p_page, PROD_URL)
        await p_page.close()
        
        s_page = await stage_ctx.new_page()
        await s_page.goto(STAGE_URL, wait_until="networkidle", timeout=60000)
        stage_toc = await extract_stage_toc(s_page, STAGE_URL)
        await s_page.close()
        
        # Match topics by filename
        stage_by_filename = {}
        for idx, item in enumerate(stage_toc):
            fn = get_filename(item['url'])
            if fn:
                if fn not in stage_by_filename:
                    stage_by_filename[fn] = []
                stage_by_filename[fn].append({**item, 'index': idx + 1, 'used': False})
        
        print(f"\n📄 Validating formatting & related links across {len(prod_toc)} topics...")
        
        async def validate_single_topic(prod_item, idx, total):
            prod_url = prod_item['url']
            prod_fn = get_filename(prod_url)
            
            stage_url = None
            candidates = stage_by_filename.get(prod_fn, [])
            for cand in candidates:
                if not cand['used']:
                    cand['used'] = True
                    stage_url = cand['url']
                    break
            
            if not stage_url:
                return {
                    'title': prod_item['title'],
                    'prod_url': prod_url,
                    'stage_url': 'N/A',
                    'status': 'mismatch',
                    'error': 'Topic is missing in Stage environment.',
                    'comparison': None
                }
            
            try:
                s_page = await stage_ctx.new_page()
                p_page = await prod_ctx.new_page()
                
                stage_data = await extract_page_formatting_and_links(s_page, stage_url, is_prod=False)
                prod_data = await extract_page_formatting_and_links(p_page, prod_url, is_prod=True)
                
                await s_page.close()
                await p_page.close()
                
                if stage_data['status'] == 'error' or prod_data['status'] == 'error':
                    err_msg = stage_data.get('error') or prod_data.get('error') or "Failed to load page contents."
                    return {
                        'title': prod_item['title'],
                        'prod_url': prod_url,
                        'stage_url': stage_url,
                        'status': 'error',
                        'error': err_msg,
                        'comparison': None
                    }
                
                comp = compare_formatting_and_links(stage_data, prod_data)
                
                emoji = '✅' if comp['overall_ok'] else '❌'
                indicators = []
                if not comp['bold_ok']: indicators.append('Bold')
                if not comp['italic_ok']: indicators.append('Italic')
                if not comp['links_ok']: indicators.append('Links')
                
                ind_str = f" (Mismatches in: {', '.join(indicators)})" if indicators else " (Perfect Match)"
                print(f"  [{idx}/{total}] {prod_item['title'][:40]:40s} {emoji}{ind_str}")
                
                return {
                    'title': prod_item['title'],
                    'prod_url': prod_url,
                    'stage_url': stage_url,
                    'status': 'success' if comp['overall_ok'] else 'mismatch',
                    'comparison': comp
                }
            except Exception as e:
                return {
                    'title': prod_item['title'],
                    'prod_url': prod_url,
                    'stage_url': stage_url,
                    'status': 'error',
                    'error': str(e),
                    'comparison': None
                }

        # Process in parallel batches of 8 for speed & reliability
        BATCH_SIZE = 8
        for batch_start in range(0, len(prod_toc), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(prod_toc))
            tasks = [
                validate_single_topic(prod_toc[i], i + 1, len(prod_toc))
                for i in range(batch_start, batch_end)
            ]
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)
            
        # Add topics that exist in Stage but NOT in Prod
        for fn, candidates in stage_by_filename.items():
            for cand in candidates:
                if not cand['used']:
                    results.append({
                        'title': cand['title'],
                        'prod_url': 'N/A',
                        'stage_url': cand['url'],
                        'status': 'mismatch',
                        'error': 'Topic exists only in Stage (Extra page).',
                        'comparison': None
                    })
                    
        await prod_ctx.close()
        await stage_ctx.close()
        await browser.close()
        
    return results, stage_toc, prod_toc

def write_reports(results, stage_toc, prod_toc):
    print("\n✍️ Generating Parity and Formatting Reports...")
    
    # ── Calculate Summary Stats ──────────────────────────────────────
    total_topics = len(results)
    passed_topics = 0
    failed_topics = 0
    errors = 0
    
    bold_fails = 0
    italic_fails = 0
    links_fails = 0
    
    for r in results:
        if r['status'] == 'error':
            errors += 1
        elif r['status'] == 'mismatch':
            failed_topics += 1
            comp = r['comparison']
            if comp:
                if not comp['bold_ok']: bold_fails += 1
                if not comp['italic_ok']: italic_fails += 1
                if not comp['links_ok']: links_fails += 1
            else:
                # Missing/extra topics counted as fails
                links_fails += 1
        else:
            passed_topics += 1

    pass_rate = (passed_topics / max(total_topics - errors, 1)) * 100
    
    # ── Write Markdown Report ────────────────────────────────────────
    with open(REPORT_MD_PATH, 'w', encoding='utf-8') as f:
        f.write(f"# 📄 Formatting & Related Links Parity Validation\n\n")
        f.write(f"> **Report Generated**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`  \n")
        f.write(f"> **Stage URL**: [{STAGE_URL}]({STAGE_URL})  \n")
        f.write(f"> **Production URL**: [{PROD_URL}]({PROD_URL})  \n\n")
        
        f.write(f"## 📊 Executive Summary\n\n")
        f.write(f"This report validates the deep content formatting (**Bold**, *Italics*) and **Related Links** parity between Stage (AEM Publish) and the Production site. Production is the source of truth; all formatting tags and related links present in Production must reflect correctly in Stage.\n\n")
        
        f.write(f"| Metric | Count / Percentage | Status |\n")
        f.write(f"| :--- | :--- | :--- |\n")
        f.write(f"| **Total Topics Scanned** | {total_topics} | - |\n")
        f.write(f"| **Perfect Matches** | `{passed_topics}` | ✅ Passed |\n")
        f.write(f"| **Parity Drift / Mismatches** | `{failed_topics}` | ❌ Issues Found |\n")
        f.write(f"| **Execution Failures/Errors** | `{errors}` | ⚠️ Warning |\n")
        f.write(f"| **Overall Parity Score** | **`{pass_rate:.1f}%`** | {'🟢 Excellent' if pass_rate >= 90 else '🟡 Warning' if pass_rate >= 75 else '🔴 Action Required'} |\n\n")
        
        f.write(f"### 🔍 Failure Breakdown\n\n")
        f.write(f"- **Bold Formatting Drifts**: `{bold_fails}` pages\n")
        f.write(f"- **Italic Formatting Drifts**: `{italic_fails}` pages\n")
        f.write(f"- **Related Links Missing in Stage**: `{links_fails}` pages\n\n")
        
        f.write(f"## 📋 Topic-by-Topic Parity Status\n\n")
        f.write(f"Below is the complete list of all evaluated topics and their validation results.\n\n")
        
        f.write(f"| Topic Title | Stage URL | Bold Status | Italic Status | Related Links | Overall |\n")
        f.write(f"| :--- | :--- | :---: | :---: | :---: | :---: |\n")
        
        for r in results:
            title = r['title']
            st_url = r['stage_url']
            
            bold_indicator = "-"
            italic_indicator = "-"
            links_indicator = "-"
            overall_indicator = "✅ PASS"
            
            if r['status'] == 'error':
                bold_indicator = "⚠️ ERROR"
                italic_indicator = "⚠️ ERROR"
                links_indicator = "⚠️ ERROR"
                overall_indicator = "⚠️ ERROR"
            elif r['status'] == 'mismatch':
                overall_indicator = "❌ FAIL"
                comp = r['comparison']
                if comp:
                    bold_indicator = "✅ OK" if comp['bold_ok'] else f"❌ ({len(comp['bold_missing_in_stage'])} missing)"
                    italic_indicator = "✅ OK" if comp['italic_ok'] else f"❌ ({len(comp['italic_missing_in_stage'])} missing)"
                    links_indicator = "✅ OK" if comp['links_ok'] else f"❌ ({len(comp['missing_links'])} missing)"
                else:
                    bold_indicator = "❌ Missing"
                    italic_indicator = "❌ Missing"
                    links_indicator = "❌ Missing"
            else:
                bold_indicator = "✅ OK"
                italic_indicator = "✅ OK"
                links_indicator = "✅ OK"
                
            f.write(f"| {title} | [Stage URL]({st_url}) | {bold_indicator} | {italic_indicator} | {links_indicator} | **{overall_indicator}** |\n")
            
        f.write(f"\n## ⚠️ Detailed Discrepancies\n\n")
        f.write(f"Below are the specific elements causing drift between Stage and Production for each page.\n\n")
        
        has_discrepancies = False
        for r in results:
            if r['status'] == 'success' or not r['comparison']:
                continue
                
            comp = r['comparison']
            has_discrepancies = True
            f.write(f"### 📄 {r['title']}\n")
            f.write(f"- **Prod URL**: {r['prod_url']}  \n")
            f.write(f"- **Stage URL**: {r['stage_url']}  \n\n")
            
            if not comp['bold_ok']:
                f.write(f"#### 🔴 Bold Formatting Differences:\n")
                if comp['bold_missing_in_stage']:
                    f.write(f"- **Missing in Stage** (Bold in Prod, regular in Stage):\n")
                    for x in comp['bold_missing_in_stage'][:10]:
                        f.write(f"  - `[BOLD: {x}]`  \n")
                    if len(comp['bold_missing_in_stage']) > 10:
                        f.write(f"  - *...and {len(comp['bold_missing_in_stage']) - 10} more items.*  \n")
                if comp['bold_extra_in_stage']:
                    f.write(f"- **Extra in Stage** (Bold in Stage, regular in Prod):\n")
                    for x in comp['bold_extra_in_stage'][:10]:
                        f.write(f"  - `[BOLD: {x}]`  \n")
                    if len(comp['bold_extra_in_stage']) > 10:
                        f.write(f"  - *...and {len(comp['bold_extra_in_stage']) - 10} more items.*  \n")
                f.write(f"\n")
                
            if not comp['italic_ok']:
                f.write(f"#### 🔴 Italic Formatting Differences:\n")
                if comp['italic_missing_in_stage']:
                    f.write(f"- **Missing in Stage** (Italic in Prod, regular in Stage):\n")
                    for x in comp['italic_missing_in_stage'][:10]:
                        f.write(f"  - `[ITALIC: {x}]`  \n")
                    if len(comp['italic_missing_in_stage']) > 10:
                        f.write(f"  - *...and {len(comp['italic_missing_in_stage']) - 10} more items.*  \n")
                if comp['italic_extra_in_stage']:
                    f.write(f"- **Extra in Stage** (Italic in Stage, regular in Prod):\n")
                    for x in comp['italic_extra_in_stage'][:10]:
                        f.write(f"  - `[ITALIC: {x}]`  \n")
                    if len(comp['italic_extra_in_stage']) > 10:
                        f.write(f"  - *...and {len(comp['italic_extra_in_stage']) - 10} more items.*  \n")
                f.write(f"\n")
                
            if not comp['links_ok']:
                f.write(f"#### 🔗 Related Links Missing in Stage:\n")
                f.write(f"The following related links are present in the Production topic but were completely **unresolved or missing** in Stage. In order to match production standard, these links must be created/published in AEM:\n")
                for link in comp['missing_links']:
                    f.write(f"- ❌ Label: `{link['text']}` | Target: `{link['href']}`  \n")
                f.write(f"\n")
                
            f.write(f"---\n\n")
            
        if not has_discrepancies:
            f.write(f"🎉 **Perfect Match! No differences or related link issues detected between Stage and Production.**\n")

    # ── Write Excel Report ───────────────────────────────────────────
    xlsx_data = []
    for r in results:
        comp = r['comparison']
        xlsx_data.append({
            'Topic Title': r['title'],
            'Overall Parity': 'PASS' if r['status'] == 'success' else 'FAIL' if r['status'] == 'mismatch' else 'ERROR',
            'Bold Match': 'YES' if (comp and comp['bold_ok']) else 'NO' if comp else '-',
            'Italic Match': 'YES' if (comp and comp['italic_ok']) else 'NO' if comp else '-',
            'Related Links Match': 'YES' if (comp and comp['links_ok']) else 'NO' if comp else '-',
            'Prod Bold Count': comp['prod_bold_count'] if comp else 0,
            'Stage Bold Count': comp['stage_bold_count'] if comp else 0,
            'Prod Italic Count': comp['prod_italic_count'] if comp else 0,
            'Stage Italic Count': comp['stage_italic_count'] if comp else 0,
            'Prod Links Count': comp['prod_links_count'] if comp else 0,
            'Stage Links Count': comp['stage_links_count'] if comp else 0,
            'Missing Links': ', '.join([f"{l['text']} ({l['href']})" for l in comp['missing_links']]) if comp and comp['missing_links'] else '',
            'Stage URL': r['stage_url'],
            'Prod URL': r['prod_url'],
            'Error Detail': r.get('error', '')
        })
        
    df = pd.DataFrame(xlsx_data)
    
    # Create Summary sheet data
    summary_info = [
        ['Formatting & Related Links Parity Audit'],
        ['Generated At', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
        ['Stage Base URL', STAGE_URL],
        ['Prod Base URL', PROD_URL],
        [''],
        ['Total Topics Checked', total_topics],
        ['Passed', passed_topics],
        ['Failed', failed_topics],
        ['Errors', errors],
        ['Overall Parity Rate', f"{pass_rate:.1f}%"]
    ]
    summary_df = pd.DataFrame(summary_info)
    
    with pd.ExcelWriter(REPORT_XLSX_PATH, engine='openpyxl') as writer:
        summary_df.to_excel(writer, sheet_name='Summary', header=False, index=False)
        df.to_excel(writer, sheet_name='Validation Details', index=False)
        
    print(f"✓ Markdown report generated: {REPORT_MD_PATH}")
    print(f"✓ Excel report generated: {REPORT_XLSX_PATH}")

async def main():
    try:
        results, stage_toc, prod_toc = await validate_formatting_and_links()
        write_reports(results, stage_toc, prod_toc)
        return 0
    except Exception as e:
        print(f"\n❌ Fatal error in script: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    asyncio.run(main())
