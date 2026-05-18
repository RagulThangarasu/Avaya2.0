import os
import sys
import json
import asyncio
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright

# Configurations
REPORTS_DIR = os.path.join(os.getcwd(), 'reports')
os.makedirs(REPORTS_DIR, exist_ok=True)

TEST_URLS_PATH = os.path.join(os.getcwd(), 'config', 'test-urls.json')
AUTH_STATE_PATH = os.path.join(os.getcwd(), 'auth-sessions', 'storage-state.json')

STAGE_URL = os.environ.get('STAGE_URL')
PROD_URL = os.environ.get('PROD_URL')

# Load fallback URLs from config
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

print(f"🚀 Starting Left Nav TOC Text Extractor & Validator")
print(f"   Stage: {STAGE_URL}")
print(f"   Prod:  {PROD_URL}\n")

# Derive bundle name to filter out chrome/UI headers
def derive_bundle(url):
    bundle_match = re.search(r'/bundle/([^/]+)/', url)
    return bundle_match.group(1) if bundle_match else ''

STAGE_BUNDLE = derive_bundle(STAGE_URL)
PROD_BUNDLE = derive_bundle(PROD_URL)

async def handle_cookies(page):
    try:
        for sel in ['#onetrust-accept-btn-handler', '#btn-accept-all', 'button:has-text("Accept")', '.cookie-accept']:
            if await page.locator(sel).is_visible(timeout=1500):
                await page.click(sel)
                await page.wait_for_timeout(500)
                break
    except:
        pass

# ── Production TOC Extraction ─────────────────────────────────────────
async def extract_prod_toc(page, base_url):
    print("🌳 Loading Production Page & expanding TOC...")
    await page.wait_for_load_state("networkidle")
    await handle_cookies(page)
    await page.wait_for_timeout(3000)

    try:
        # Click Expand All if available
        expand_btn = page.locator('.zDocsCollapseExpandButton').first
        if await expand_btn.is_visible(timeout=4000):
            await expand_btn.click()
            await page.wait_for_timeout(4000)
    except:
        pass

    # Recursively expand up to 5 levels
    for level in range(5):
        try:
            collapse_nodes = await page.locator('.zDocsTocCollapseItemButton[aria-expanded="false"], button[aria-expanded="false"], .expand-icon').all()
            if not collapse_nodes:
                break
            print(f"   [Prod] Level {level+1} - Expanding {len(collapse_nodes)} nodes...")
            for node in collapse_nodes[:120]:
                try:
                    await node.click(timeout=600)
                    await page.wait_for_timeout(50)
                except: pass
            await page.wait_for_timeout(1500)
        except:
            break

    # Extract nodes with their nesting level
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
        
        allLinks.forEach((a, idx) => {
            const href = a.getAttribute('href');
            let text = a.innerText.trim();
            // Clean title text
            text = text.replace(/[\\r\\n\\t]+/g, ' ').replace(/^\\s*[\\u203A\\u25BC\\u25B6>v-]\\s*/g, '').trim();
            
            // Calculate nesting depth
            let level = 0;
            let parent = a.parentElement;
            while (parent && parent !== container) {
                if (parent.tagName === 'UL' || parent.tagName === 'OL') {
                    level++;
                }
                parent = parent.parentElement;
            }
            
            if (href && text && !href.startsWith('#') && !href.startsWith('javascript')) {
                results.push({
                    title: text,
                    href: href,
                    level: Math.max(0, level - 1)
                });
            }
        });
        return results;
    }''')

    toc = []
    seen = set()
    for idx, item in enumerate(links_data):
        full_url = urljoin(base_url, item['href']).split('#')[0].split('?')[0]
        # Filter UI chrome links
        if PROD_BUNDLE and PROD_BUNDLE not in full_url:
            continue
        if full_url not in seen:
            toc.append({
                'index': len(toc) + 1,
                'title': item['title'],
                'url': full_url,
                'level': item['level']
            })
            seen.add(full_url)
    print(f"   ✅ Prod TOC: {len(toc)} topics extracted.")
    return toc

# ── Stage TOC Extraction ──────────────────────────────────────────────
async def extract_stage_toc(page, base_url):
    print("🌳 Loading Stage Page & expanding TOC...")
    await page.wait_for_load_state("networkidle")
    await handle_cookies(page)
    await page.wait_for_timeout(3000)

    # Recursively expand up to 3 levels
    for level in range(3):
        try:
            collapse_nodes = await page.locator('.cmp-navigation__item--active[aria-expanded="false"], .cmp-navigation__item[aria-expanded="false"], button[aria-expanded="false"]').all()
            if not collapse_nodes:
                break
            print(f"   [Stage] Level {level+1} - Expanding {len(collapse_nodes)} nodes...")
            for node in collapse_nodes[:50]:
                try:
                    await node.click(timeout=600)
                except: pass
            await page.wait_for_timeout(1000)
        except:
            break

    # Extract Stage nodes
    links_data = await page.evaluate('''() => {
        const results = [];
        const links = document.querySelectorAll('.cmp-navigation__item-link');
        links.forEach((a, idx) => {
            const href = a.getAttribute('href');
            let text = a.innerText.trim();
            text = text.replace(/[\\r\\n\\t]+/g, ' ').replace(/^\\s*[\\u203A\\u25BC\\u25B6>v-]\\s*/g, '').trim();
            
            // Calculate nesting depth
            let level = 0;
            let parent = a.parentElement;
            while (parent) {
                if (parent.tagName === 'UL' || parent.tagName === 'OL') {
                    level++;
                }
                parent = parent.parentElement;
            }
            
            if (href && text && !href.startsWith('#') && !href.startsWith('javascript')) {
                results.push({
                    title: text,
                    href: href,
                    level: Math.max(0, level - 2) // Normalize depth
                });
            }
        });
        return results;
    }''')

    toc = []
    seen = set()
    for item in links_data:
        full_url = urljoin(base_url, item['href']).split('#')[0].split('?')[0]
        if STAGE_BUNDLE and STAGE_BUNDLE not in full_url:
            continue
        if full_url not in seen:
            toc.append({
                'index': len(toc) + 1,
                'title': item['title'],
                'url': full_url,
                'level': item['level']
            })
            seen.add(full_url)
    print(f"   ✅ Stage TOC: {len(toc)} topics extracted.")
    return toc

# ── Side-by-Side Validation & Text Output Generation ──────────────────
def validate_and_generate_txt(stage_toc, prod_toc):
    print("🔍 Performing Left Nav Parity Audit...")
    
    normalize = lambda t: re.sub(r'[^a-z0-9]', '', t.lower())
    stage_norm_map = {normalize(n['title']): n for n in stage_toc}
    prod_norm_map = {normalize(n['title']): n for n in prod_toc}
    
    issues = []
    
    # 1. Missing in Prod
    for s_node in stage_toc:
        norm = normalize(s_node['title'])
        if norm not in prod_norm_map:
            issues.append(f"❌ MISSING IN PROD: Topic '{s_node['title']}' exists in Stage (pos {s_node['index']}) but is missing in Prod.")
            
    # 2. Missing in Stage
    for p_node in prod_toc:
        norm = normalize(p_node['title'])
        if norm not in stage_norm_map:
            issues.append(f"❌ MISSING IN STAGE: Topic '{p_node['title']}' exists in Prod (pos {p_node['index']}) but is missing in Stage.")
            
    # 3. Order & Grouping & Case & Symbols checks for matched items
    stage_matched = [n for n in stage_toc if normalize(n['title']) in prod_norm_map]
    prod_matched = [n for n in prod_toc if normalize(n['title']) in stage_norm_map]
    
    prod_matched_seq = {normalize(n['title']): idx for idx, n in enumerate(prod_matched)}
    
    for idx_s, s_node in enumerate(stage_matched):
        norm = normalize(s_node['title'])
        p_node = prod_norm_map[norm]
        
        # Grouping (level) check
        if s_node['level'] != p_node['level']:
            issues.append(f"⚠️ GROUPING DRIFT: '{s_node['title']}' is Level {s_node['level']} in Stage but Level {p_node['level']} in Prod.")
            
        # Sequence/Order check
        idx_p = prod_matched_seq.get(norm, -1)
        if idx_p != -1 and idx_p != idx_s:
            issues.append(f"⚠️ SEQUENCE ORDER DRIFT: '{s_node['title']}' is at order position {s_node['index']} in Stage matched sequence but position {p_node['index']} in Prod.")
            
        # Case Check
        s_clean = re.sub(r'[®™©]', '', s_node['title']).strip()
        p_clean = re.sub(r'[®™©]', '', p_node['title']).strip()
        if s_clean != p_clean:
            issues.append(f"📝 CASING MISMATCH: Stage title '{s_node['title']}' vs Prod title '{p_node['title']}'.")
            
        # Symbols Check (®, ™, ©)
        sym_regex = r'[®™©]'
        s_sym = ''.join(sorted(re.findall(sym_regex, s_node['title'])))
        p_sym = ''.join(sorted(re.findall(sym_regex, p_node['title'])))
        if s_sym != p_sym:
            issues.append(f"🏷️ SPECIAL SYMBOL DRIFT: Stage title '{s_node['title']}' vs Prod title '{p_node['title']}' (Special symbols mismatch).")

    # Format complete hierarchical trees
    def format_tree(toc):
        lines = []
        for n in toc:
            indent = "    " * n['level']
            lines.append(f"{indent}- [{n['index']}] {n['title']} (Level {n['level']})")
            lines.append(f"{indent}  URL: {n['url']}")
        return "\n".join(lines) if lines else "(No nodes found)"

    report_lines = []
    report_lines.append("=========================================================================================")
    report_lines.append("                   📊 LEFT NAV TABLE OF CONTENTS (TOC) PARITY REPORT                     ")
    report_lines.append("=========================================================================================")
    report_lines.append(f"Generated On: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"Stage URL:    {STAGE_URL}")
    report_lines.append(f"Prod URL:     {PROD_URL}")
    report_lines.append("=========================================================================================\n")
    
    report_lines.append("-----------------------------------------------------------------------------------------")
    report_lines.append("📈 AUDIT STATS & SUMMARY")
    report_lines.append("-----------------------------------------------------------------------------------------")
    report_lines.append(f"Total Stage TOC Nodes Extracted:  {len(stage_toc)}")
    report_lines.append(f"Total Prod TOC Nodes Extracted:   {len(prod_toc)}")
    report_lines.append(f"Total Validation Issues Found:     {len(issues)}")
    report_lines.append("-----------------------------------------------------------------------------------------\n")
    
    report_lines.append("-----------------------------------------------------------------------------------------")
    report_lines.append("🚨 DETAILED VALIDATION ISSUES")
    report_lines.append("-----------------------------------------------------------------------------------------")
    if issues:
        for idx, issue in enumerate(issues, 1):
            report_lines.append(f"  {idx}. {issue}")
    else:
        report_lines.append("  🎉 PERFECT MATCH! Both Left Navs are identical in sequence, order, grouping, casing, and symbols!")
    report_lines.append("-----------------------------------------------------------------------------------------\n")
    
    report_lines.append("=========================================================================================")
    report_lines.append("🌳 STAGE ENVIRONMENT TOC HIERARCHICAL TREE")
    report_lines.append("=========================================================================================")
    report_lines.append(format_tree(stage_toc))
    report_lines.append("=========================================================================================\n")
    
    report_lines.append("=========================================================================================")
    report_lines.append("🌳 PRODUCTION ENVIRONMENT TOC HIERARCHICAL TREE")
    report_lines.append("=========================================================================================")
    report_lines.append(format_tree(prod_toc))
    report_lines.append("=========================================================================================\n")
    
    return "\n".join(report_lines)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        stage_ctx = await browser.new_context(
            storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None
        )
        prod_ctx = await browser.new_context()
        
        # 1. Extract Stage TOC
        s_page = await stage_ctx.new_page()
        await s_page.goto(STAGE_URL, wait_until="networkidle", timeout=60000)
        stage_toc = await extract_stage_toc(s_page, STAGE_URL)
        await s_page.close()
        
        # 2. Extract Prod TOC
        p_page = await prod_ctx.new_page()
        await p_page.goto(PROD_URL, wait_until="networkidle", timeout=60000)
        prod_toc = await extract_prod_toc(p_page, PROD_URL)
        await p_page.close()
        
        await stage_ctx.close()
        await prod_ctx.close()
        await browser.close()
        
    # Generate the text output content
    report_content = validate_and_generate_txt(stage_toc, prod_toc)
    
    # Save reports
    static_txt_path = os.path.join(REPORTS_DIR, 'leftnav-toc-validation.txt')
    timestamp_txt_path = os.path.join(REPORTS_DIR, f'leftnav-toc-validation-{int(datetime.now().timestamp())}.txt')
    
    with open(static_txt_path, 'w', encoding='utf-8') as f:
        f.write(report_content)
        
    with open(timestamp_txt_path, 'w', encoding='utf-8') as f:
        f.write(report_content)
        
    print(f"\n✅ Completed! Hierarchical Left Nav Text Reports saved:")
    print(f"   • Static:    {static_txt_path}")
    print(f"   • Timestamp: {timestamp_txt_path}")

if __name__ == '__main__':
    asyncio.run(main())
