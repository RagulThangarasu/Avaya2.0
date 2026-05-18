import os
import sys
import json
import asyncio
import re
import difflib
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

STAGE_MD_PATH = os.path.join(REPORTS_DIR, 'stage_topics.md')
PROD_MD_PATH = os.path.join(REPORTS_DIR, 'prod_topics.md')
COMPARISON_REPORT_PATH = os.path.join(REPORTS_DIR, 'md_comparison_report.md')

# Load fallback URLs if not in environment
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

print(f"🚀 Starting Markdown Extractor & Comparer")
print(f"   Stage TOC URL: {STAGE_URL}")
print(f"   Prod TOC URL:  {PROD_URL}\n")

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

# ── Content MD Extraction ────────────────────────────────────────────
async def extract_topic_markdown(page, url, is_prod=False):
    """Fetches topic and formats content beautifully as Markdown."""
    try:
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await handle_cookies(page)
        await page.wait_for_timeout(300)
        
        root_selector = '.zDocsTopicPageBody' if is_prod else '.topic-renderer__content'
        
        try:
            await page.wait_for_selector(root_selector, timeout=5000)
        except:
            pass
            
        md_content = await page.evaluate('''async (selector) => {
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

            let markdown = '';
            
            // Extract title or main heading
            const h1 = root.querySelector('h1');
            if (h1) {
                markdown += `# ${h1.innerText.trim()}\\n\\n`;
            }

            // Iterate children recursively to build rich text markdown representation
            function walk(node) {
                let text = '';
                if (node.nodeType === 3) { // TEXT_NODE
                    text += node.textContent;
                } else if (node.nodeType === 1) { // ELEMENT_NODE
                    const tag = node.tagName.toLowerCase();
                    const className = node.className ? node.className.toLowerCase() : '';
                    
                    // Skip noisy boilerplate
                    if (['script', 'style', 'header', 'footer', 'nav'].includes(tag) ||
                        className.includes('breadcrumb') || className.includes('toolbar') ||
                        className.includes('metadata') || className.includes('search')) {
                        return '';
                    }

                    const isBold = isBoldElement(node);
                    const isItalic = isItalicElement(node);

                    let childContent = '';
                    for (let child of node.childNodes) {
                        childContent += walk(child);
                    }

                    if (tag === 'h2') {
                        text += `\\n\\n## ${childContent.trim()}\\n\\n`;
                    } else if (tag === 'h3') {
                        text += `\\n\\n### ${childContent.trim()}\\n\\n`;
                    } else if (tag === 'p') {
                        text += `\\n\\n${childContent.trim()}\\n\\n`;
                    } else if (tag === 'li') {
                        text += `\\n- ${childContent.trim()}`;
                    } else {
                        // Apply bold/italic decorations
                        let dec = childContent;
                        if (isBold && dec.trim()) dec = `**${dec.trim()}**`;
                        if (isItalic && dec.trim()) dec = `*${dec.trim()}*`;
                        text += dec;
                    }
                }
                return text;
            }

            markdown += walk(root);

            // Related Links Extraction
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

            if (relatedLinks.length > 0) {
                markdown += `\\n\\n### Related Information\\n\\n`;
                relatedLinks.forEach(link => {
                    markdown += `- [${link.text}](${link.href})\\n`;
                });
            }

            // Post-processing to normalize markdown spacing
            return markdown.replace(/\\n{3,}/g, '\\n\\n').trim();
        }''', root_selector)
        
        return md_content
    except Exception as e:
        return f"<!-- Error loading page: {e} -->"

async def process_topics_in_markdown():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        stage_ctx = await browser.new_context(
            storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None
        )
        prod_ctx = await browser.new_context()
        
        print("🔍 Crawling TOCs...")
        p_page = await prod_ctx.new_page()
        await p_page.goto(PROD_URL, wait_until="networkidle", timeout=60000)
        prod_toc = await extract_prod_toc(p_page, PROD_URL)
        await p_page.close()
        
        s_page = await stage_ctx.new_page()
        await s_page.goto(STAGE_URL, wait_until="networkidle", timeout=60000)
        stage_toc = await extract_stage_toc(s_page, STAGE_URL)
        await s_page.close()
        
        # Build Stage mapping by filename
        stage_by_filename = {}
        for idx, item in enumerate(stage_toc):
            fn = get_filename(item['url'])
            if fn:
                if fn not in stage_by_filename:
                    stage_by_filename[fn] = []
                stage_by_filename[fn].append({**item, 'index': idx + 1, 'used': False})
        
        # Write stage_topics.md and prod_topics.md concurrently
        stage_md_blocks = ["" for _ in prod_toc]
        prod_md_blocks = ["" for _ in prod_toc]
        
        print(f"\n📄 Compiling markdown for {len(prod_toc)} matched topics...")
        
        async def process_single(prod_item, idx, total):
            prod_url = prod_item['url']
            prod_fn = get_filename(prod_url)
            
            stage_url = None
            candidates = stage_by_filename.get(prod_fn, [])
            for cand in candidates:
                if not cand['used']:
                    cand['used'] = True
                    stage_url = cand['url']
                    break
            
            p_page = await prod_ctx.new_page()
            prod_md = await extract_topic_markdown(p_page, prod_url, is_prod=True)
            await p_page.close()
            
            stage_md = ""
            if stage_url:
                s_page = await stage_ctx.new_page()
                stage_md = await extract_topic_markdown(s_page, stage_url, is_prod=False)
                await s_page.close()
            else:
                stage_md = f"# {prod_item['title']}\n\n*Topic is completely missing in Stage environment.*"
                
            print(f"  [{idx}/{total}] compiled: {prod_item['title'][:50]}")
            return idx - 1, stage_md, prod_md
            
        # Process in batches of 15 for optimal speed
        BATCH_SIZE = 15
        for batch_start in range(0, len(prod_toc), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(prod_toc))
            tasks = [
                process_single(prod_toc[i], i + 1, len(prod_toc))
                for i in range(batch_start, batch_end)
            ]
            batch_results = await asyncio.gather(*tasks)
            for idx, s_md, p_md in batch_results:
                stage_md_blocks[idx] = s_md
                prod_md_blocks[idx] = p_md
                
        # Write files
        print(f"\n✍️ Saving stage_topics.md...")
        with open(STAGE_MD_PATH, 'w', encoding='utf-8') as sf:
            sf.write(f"# Consolidated Stage Topics\n")
            sf.write(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            sf.write("\n\n---\n\n".join(stage_md_blocks))
            
        print(f"✍️ Saving prod_topics.md...")
        with open(PROD_MD_PATH, 'w', encoding='utf-8') as pf:
            pf.write(f"# Consolidated Production Topics\n")
            pf.write(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            pf.write("\n\n---\n\n".join(prod_md_blocks))
            
        await prod_ctx.close()
        await stage_ctx.close()
        await browser.close()
        
    return len(prod_toc)

# ── Direct Markdown Comparison ───────────────────────────────────────
def split_into_sentences(text):
    """Normalize whitespaces and split content into clean sentences, ignoring empty spaces."""
    # Convert all consecutive whitespaces/newlines/tabs into a single space
    normalized = re.sub(r'\s+', ' ', text)
    # Split by standard sentence delimiters (. ! ?) followed by a space
    sentences = re.split(r'(?<=[.!?])\s+', normalized)
    return [s.strip() for s in sentences if s.strip()]

def compare_compiled_markdowns():
    print("\n🔍 Validating and comparing markdown contents...")
    
    with open(STAGE_MD_PATH, 'r', encoding='utf-8') as sf:
        stage_text = sf.read()
    with open(PROD_MD_PATH, 'r', encoding='utf-8') as pf:
        prod_text = pf.read()
        
    # Extract clean sentence structures to ignore blank lines and raw empty spaces
    stage_sentences = split_into_sentences(stage_text)
    prod_sentences = split_into_sentences(prod_text)
    
    # Calculate similarity on actual matching sentences
    matcher = difflib.SequenceMatcher(None, stage_sentences, prod_sentences)
    match_pct = matcher.ratio() * 100
    
    # Word count breakdown
    stage_words = len(stage_text.split())
    prod_words = len(prod_text.split())
    
    # Bold & Italic markdown count parity check
    stage_bold_cnt = len(re.findall(r'\*\*.*?\*\*', stage_text))
    prod_bold_cnt = len(re.findall(r'\*\*.*?\*\*', prod_text))
    
    stage_italic_cnt = len(re.findall(r'\*.*?\*', stage_text)) - (stage_bold_cnt * 2) # adjust for bold overlap *
    prod_italic_cnt = len(re.findall(r'\*.*?\*', prod_text)) - (prod_bold_cnt * 2)
    stage_italic_cnt = max(stage_italic_cnt, 0)
    prod_italic_cnt = max(prod_italic_cnt, 0)
    
    # Related links count check
    stage_links_cnt = len(re.findall(r'\[.*?\]\(.*?\)', stage_text))
    prod_links_cnt = len(re.findall(r'\[.*?\]\(.*?\)', prod_text))
    
    # Generate unified diff details for summary
    diff_lines = list(difflib.unified_diff(
        stage_sentences,
        prod_sentences,
        fromfile='stage_topics.md (Sentences)',
        tofile='prod_topics.md (Sentences)',
        n=2
    ))
    
    # ── Bold Text Content Parity & Checklist Generation ─────────────────
    stage_bold_phrases = [b.strip() for b in re.findall(r'\*\*(.*?)\*\*', stage_text) if b.strip()]
    prod_bold_phrases = [b.strip() for b in re.findall(r'\*\*(.*?)\*\*', prod_text) if b.strip()]
    
    stage_bold_set = set(stage_bold_phrases)
    prod_bold_set = set(prod_bold_phrases)
    
    matched_bolds = sorted(list(stage_bold_set & prod_bold_set))
    missing_in_prod = sorted(list(stage_bold_set - prod_bold_set))
    extra_in_prod = sorted(list(prod_bold_set - stage_bold_set))
    
    total_unique_bolds = len(stage_bold_set | prod_bold_set)
    bold_match_pct = (len(matched_bolds) / total_unique_bolds * 100) if total_unique_bolds else 100.0
    
    BOLD_REPORT_PATH = os.path.join(REPORTS_DIR, 'bold_text_parity_report.md')
    with open(BOLD_REPORT_PATH, 'w', encoding='utf-8') as brf:
        brf.write(f"# 🔠 Bold Text Content Parity & Comparison Report\n\n")
        brf.write(f"> **Report Date**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`  \n")
        brf.write(f"> **Stage MD File**: [{os.path.basename(STAGE_MD_PATH)}](file://{STAGE_MD_PATH})  \n")
        brf.write(f"> **Production MD File**: [{os.path.basename(PROD_MD_PATH)}](file://{PROD_MD_PATH})  \n\n")
        
        brf.write(f"## 📈 Bold Text Parity Stats\n\n")
        brf.write(f"| Bold Metric | Value | Percentage |\n")
        brf.write(f"| :--- | :---: | :---: |\n")
        brf.write(f"| **Stage Unique Bold Phrases** | {len(stage_bold_set)} | - |\n")
        brf.write(f"| **Production Unique Bold Phrases** | {len(prod_bold_set)} | - |\n")
        brf.write(f"| **Perfect Matches** | {len(matched_bolds)} | {len(matched_bolds)/max(total_unique_bolds, 1)*100:.2f}% |\n")
        brf.write(f"| **Missing in Production** | {len(missing_in_prod)} | {len(missing_in_prod)/max(total_unique_bolds, 1)*100:.2f}% |\n")
        brf.write(f"| **Extra in Production (Missing in Stage)** | {len(extra_in_prod)} | {len(extra_in_prod)/max(total_unique_bolds, 1)*100:.2f}% |\n\n")
        
        brf.write(f"## ❌ Missing in Production ({len(missing_in_prod)})\n\n")
        if missing_in_prod:
            for item in missing_in_prod:
                brf.write(f"- [ ] `{item}`\n")
        else:
            brf.write(f"🎉 **None! All Stage bold phrases are styled as bold in Production.**\n")
            
        brf.write(f"\n## ➕ Extra in Production ({len(extra_in_prod)})\n\n")
        if extra_in_prod:
            for item in extra_in_prod:
                brf.write(f"- [ ] `{item}`\n")
        else:
            brf.write(f"🎉 **None! No extra bold phrases styled in Production.**\n")
            
        brf.write(f"\n## ✅ Perfect Matches ({len(matched_bolds)})\n\n")
        if matched_bolds:
            for item in matched_bolds:
                brf.write(f"- [x] `{item}`\n")
        else:
            brf.write(f"*No perfect matches found.*\n")
            
    # Beautiful report
    with open(COMPARISON_REPORT_PATH, 'w', encoding='utf-8') as rf:
        rf.write(f"# 📊 Consolidated Topics Markdown Comparison Report\n\n")
        rf.write(f"> **Report Date**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`  \n")
        rf.write(f"> **Stage Source File**: [{os.path.basename(STAGE_MD_PATH)}](file://{STAGE_MD_PATH})  \n")
        rf.write(f"> **Production Source File**: [{os.path.basename(PROD_MD_PATH)}](file://{PROD_MD_PATH})  \n\n")
        
        rf.write(f"## 📈 Content Parity Score\n\n")
        rf.write(f"### **Content Match Percentage**: ` {match_pct:.2f}% `\n\n")
        
        rf.write(f"| Parity Metric | Stage Environment | Production Environment | Status |\n")
        rf.write(f"| :--- | :---: | :---: | :---: |\n")
        rf.write(f"| **Total Character Length** | {len(stage_text)} | {len(prod_text)} | {'✅ Match' if len(stage_text) == len(prod_text) else '❌ Drifted'} |\n")
        rf.write(f"| **Word Count** | {stage_words} | {prod_words} | {'✅ Match' if stage_words == prod_words else '❌ Drifted'} |\n")
        rf.write(f"| **Bold Text Blocks (`**`)** | {stage_bold_cnt} | {prod_bold_cnt} | {'✅ Match' if stage_bold_cnt == prod_bold_cnt else '❌ Drifted'} |\n")
        rf.write(f"| **Italic Text Blocks (`*`)** | {stage_italic_cnt} | {prod_italic_cnt} | {'✅ Match' if stage_italic_cnt == prod_italic_cnt else '❌ Drifted'} |\n")
        rf.write(f"| **Related / Inline Links** | {stage_links_cnt} | {prod_links_cnt} | {'✅ Match' if stage_links_cnt == prod_links_cnt else '❌ Drifted'} |\n\n")
        
        rf.write(f"## 🔠 Bold Text Content Parity Audit\n\n")
        rf.write(f"* **Total Unique Bold Phrases Extracted**: `{total_unique_bolds}`\n")
        rf.write(f"* **Perfect Matches**: `{len(matched_bolds)}`\n")
        rf.write(f"* **Missing in Production**: `{len(missing_in_prod)}` (Stage bold phrases not styled as bold in Prod)\n")
        rf.write(f"* **Extra in Production**: `{len(extra_in_prod)}` (Prod bold phrases not styled as bold in Stage)\n")
        rf.write(f"* **Bold Text Parity Score**: ` {bold_match_pct:.2f}% `\n\n")
        rf.write(f"👉 **For the full side-by-side Bold Parity Checklist Report, see**: [bold_text_parity_report.md](file://{BOLD_REPORT_PATH})  \n\n")

        if diff_lines:
            rf.write(f"## 🔍 Parity Diff Highlights (First 100 Diff Lines)\n\n")
            rf.write(f"```diff\n")
            for line in diff_lines[:100]:
                rf.write(line + "\n")
            if len(diff_lines) > 100:
                rf.write(f"\n... (truncated {len(diff_lines) - 100} more difference lines)\n")
            rf.write(f"```\n")
        else:
            rf.write(f"🎉 **Perfect Match! The consolidated markdown files are identical.**\n")

    print(f"\n📊 Match Percentage: {match_pct:.2f}%")
    print(f"✓ Summary report saved: {COMPARISON_REPORT_PATH}")
    
    # Standard format print for parent agent extraction
    print(f"::MATCH_PERCENTAGE::{match_pct:.2f}%")
    print(f"::STAGE_WORDS::{stage_words}")
    print(f"::PROD_WORDS::{prod_words}")
    print(f"::STAGE_BOLD::{stage_bold_cnt}")
    print(f"::PROD_BOLD::{prod_bold_cnt}")
    print(f"::STAGE_ITALIC::{stage_italic_cnt}")
    print(f"::PROD_ITALIC::{prod_italic_cnt}")
    print(f"::STAGE_LINKS::{stage_links_cnt}")
    print(f"::PROD_LINKS::{prod_links_cnt}")

async def main():
    try:
        total = await process_topics_in_markdown()
        compare_compiled_markdowns()
        return 0
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    asyncio.run(main())
