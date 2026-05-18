import os
import sys
import requests
import asyncio
import aiohttp
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime
import re

# Configurations
REPORTS_DIR = os.path.join(os.getcwd(), 'reports')
UI_REPORTS_DIR = os.path.join(os.getcwd(), '.ui_reports')
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(UI_REPORTS_DIR, exist_ok=True)

# Environment Variables
START_URL = os.environ.get('PROD_URL') or os.environ.get('STAGE_URL')
REPORT_FILENAME = os.environ.get('REPORT_FILENAME') or os.path.join(UI_REPORTS_DIR, f'broken-links-{int(datetime.now().timestamp())}.xlsx')

if not START_URL:
    print("❌ Error: No START_URL provided.")
    sys.exit(1)

print(f"🚀 Starting Python Broken Link Crawler for: {START_URL}")

RESULTS = []
CHECKED_URLS = {}
CONCURRENCY_LIMIT = 50

def get_base_info(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"

ORIGIN = get_base_info(START_URL)

async def validate_url(session, url, topic_title, topic_url, category, text):
    if not url or url.startswith(('javascript:', 'mailto:', 'tel:', '#', 'data:')):
        return

    # Clean URL (remove fragments for validation)
    clean_url = url.split('#')[0]
    
    if clean_url in CHECKED_URLS:
        status = CHECKED_URLS[clean_url]
        if status == 404:
            RESULTS.append({
                'Topic': topic_title,
                'Category': category,
                'Asset Label': text,
                'Asset URL': url,
                'Status': status,
                'Type': 'Internal' if ORIGIN in clean_url else 'External',
                'Location': topic_url
            })
        return

    try:
        # Try HEAD first
        async with session.head(clean_url, timeout=10, allow_redirects=True) as resp:
            status = resp.status
            
            # Fallback to GET for common blocks (some servers block HEAD)
            if status in [404, 405, 403, 401]:
                async with session.get(clean_url, timeout=10, allow_redirects=True) as gresp:
                    status = gresp.status
                    
    except Exception as e:
        status = f"ERROR: {str(e)[:50]}"

    CHECKED_URLS[clean_url] = status
    if status == 404:
        RESULTS.append({
            'Topic': topic_title,
            'Category': category,
            'Asset Label': text,
            'Asset URL': url,
            'Status': status,
            'Type': 'Internal' if ORIGIN in clean_url else 'External',
            'Location': topic_url
        })

async def process_topic(session, topic, semaphore):
    async with semaphore:
        try:
            print(f"Checking: {topic['title']}")
            async with session.get(topic['url'], timeout=30) as resp:
                if resp.status != 200:
                    RESULTS.append({
                        'Topic': topic['title'],
                        'Category': 'Page',
                        'Asset Label': 'Page Load',
                        'Asset URL': topic['url'],
                        'Status': resp.status,
                        'Type': 'Internal',
                        'Location': topic['url']
                    })
                    return
                
                html = await resp.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Exclude header and footer elements by scoping to the main body container
                body_container = soup.select_one('.zDocsTopicPageBody')
                if not body_container:
                    body_container = soup.select_one('.topic-renderer__content')
                if not body_container:
                    body_container = soup.select_one('main')
                if not body_container:
                    body_container = soup
                
                tasks = []
                
                # 1. Links (<a> tags) inside body content
                for a in body_container.find_all('a', href=True):
                    href = a['href']
                    full_url = urljoin(topic['url'], href)
                    tasks.append(validate_url(session, full_url, topic['title'], topic['url'], 'Link', a.get_text().strip() or 'Anchor'))

                # 2. Images (<img> tags) inside body content
                for img in body_container.find_all('img', src=True):
                    src = img['src']
                    full_url = urljoin(topic['url'], src)
                    tasks.append(validate_url(session, full_url, topic['title'], topic['url'], 'Image', img.get('alt', '').strip() or 'Image Asset'))

                # 3. Icons (link rel="icon", rel="shortcut icon") in head template metadata
                for link in soup.find_all('link', rel=re.compile(r'icon', re.I)):
                    if link.get('href'):
                        full_url = urljoin(topic['url'], link['href'])
                        tasks.append(validate_url(session, full_url, topic['title'], topic['url'], 'Icon', f"Favicon/Icon ({link.get('rel')})"))

                # 4. SVG Icons (<use xlink:href="..."> or <use href="...">) inside body content
                for use in body_container.find_all('use'):
                    href = use.get('href') or use.get('xlink:href')
                    if href:
                        # Extract the base URL if it's a sprite reference like icons.svg#home
                        base_asset = href.split('#')[0]
                        if base_asset:
                            full_url = urljoin(topic['url'], base_asset)
                            tasks.append(validate_url(session, full_url, topic['title'], topic['url'], 'SVG Icon', f"SVG Sprite: {href}"))

                # 5. Background Images inside body content
                for el in body_container.find_all(style=re.compile(r'background-image', re.I)):
                    style = el.get('style')
                    match = re.search(r'url\(([\'"]?)(.*?)\1\)', style)
                    if match:
                        bg_url = match.group(2)
                        full_url = urljoin(topic['url'], bg_url)
                        tasks.append(validate_url(session, full_url, topic['title'], topic['url'], 'BG Image', 'Inline Background Image'))

                # 6. Objects and Embeds inside body content
                for obj in body_container.find_all('object', data=True):
                    full_url = urljoin(topic['url'], obj['data'])
                    tasks.append(validate_url(session, full_url, topic['title'], topic['url'], 'Object', f"Object: {obj['data']}"))
                
                for embed in body_container.find_all('embed', src=True):
                    full_url = urljoin(topic['url'], embed['src'])
                    tasks.append(validate_url(session, full_url, topic['title'], topic['url'], 'Embed', f"Embed: {embed['src']}"))

                if tasks:
                    await asyncio.gather(*tasks)
                    
        except Exception as e:
            RESULTS.append({
                'Topic': topic['title'],
                'Category': 'Page',
                'Asset Label': 'Page Load Error',
                'Asset URL': topic['url'],
                'Status': f"ERROR: {str(e)[:50]}",
                'Type': 'Internal',
                'Location': topic['url']
            })

async def main():
    # 1. Extract Topics via Playwright (handles AEM redirects)
    print("🔍 Extracting Table of Contents...")
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            
            # Use auth state if available for Stage URLs
            auth_path = os.path.join(os.getcwd(), 'auth-sessions', 'storage-state.json')
            ctx = await browser.new_context(
                storage_state=auth_path if os.path.exists(auth_path) else None
            )
            page = await ctx.new_page()
            await page.goto(START_URL, wait_until="networkidle", timeout=60000)
            
            # Accept cookies
            for sel in ['#onetrust-accept-btn-handler', '#btn-accept-all', 'button:has-text("Accept")']:
                try:
                    if await page.locator(sel).is_visible(timeout=1500):
                        await page.click(sel)
                        await page.wait_for_timeout(500)
                        break
                except: pass
            
            # Click Expand button and recursively expand nested items
            # 1. Expand Prod-style TOC recursively
            try:
                expand_btn = page.locator('.zDocsCollapseExpandButton').first
                if await expand_btn.is_visible(timeout=2000):
                    await expand_btn.click()
                    await page.wait_for_timeout(4000)
            except: pass

            for level in range(5):
                try:
                    collapse_nodes = await page.locator('.zDocsTocCollapseItemButton[aria-expanded="false"], button[aria-expanded="false"], .expand-icon').all()
                    if not collapse_nodes:
                        break
                    for node in collapse_nodes[:120]:
                        try:
                            await node.click(timeout=500)
                            await page.wait_for_timeout(50)
                        except: pass
                    await page.wait_for_timeout(1500)
                except:
                    break

            # 2. Expand Stage-style TOC recursively
            for level in range(3):
                try:
                    collapse_nodes = await page.locator('.cmp-navigation__item--active[aria-expanded="false"], .cmp-navigation__item[aria-expanded="false"]').all()
                    if not collapse_nodes:
                        break
                    for node in collapse_nodes[:50]:
                        try:
                            await node.click(timeout=500)
                        except: pass
                    await page.wait_for_timeout(1000)
                except:
                    break
            
            # Extract all TOC links via JS with cleanup
            links_data = await page.evaluate(r'''() => {
                const results = [];
                // Try Prod selectors first, then Stage
                let links = document.querySelectorAll('.zDocsTocList a[href], .zDocsTOC a[href]');
                if (links.length === 0) {
                    links = document.querySelectorAll('.cmp-navigation__item-link');
                }
                if (links.length === 0) {
                    links = document.querySelectorAll('nav a[href], [class*="sidebar"] a[href]');
                }
                links.forEach(a => {
                    const href = a.getAttribute('href');
                    let text = a.innerText.trim();
                    // Clean up titles (remove leading/trailing symbols, newlines, tabs, chevron chars)
                    text = text.replace(/[\r\n\t]+/g, ' ').replace(/^\s*[\u203A\u25BC\u25B6>v-]\s*/g, '').trim();
                    if (href && text && !href.startsWith('#') && !href.startsWith('javascript')) {
                        results.push({text, href});
                    }
                });
                return results;
            }''')
            
            topics = []
            for item in links_data:
                full_url = urljoin(START_URL, item['href']).split('#')[0].split('?')[0]
                if '.html' in full_url or '/bundle/' in full_url or '/page/' in full_url:
                    topics.append({'title': item['text'], 'url': full_url})
            
            await browser.close()
        
        # Deduplicate topics
        seen_urls = set()
        unique_topics = []
        for t in topics:
            if t['url'] not in seen_urls:
                unique_topics.append(t)
                seen_urls.add(t['url'])
        
        if not unique_topics:
            print("⚠️ No topics found in TOC. Checking landing page only.")
            unique_topics = [{'title': 'Landing Page', 'url': START_URL}]

        print(f"✅ Found {len(unique_topics)} unique topics. Validating assets...")

        # 2. Parallel Process Topics and Links
        # Use a semaphore to limit concurrent TOPIC requests, but within each topic we fire links in parallel
        semaphore = asyncio.Semaphore(5) 
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        async with aiohttp.ClientSession(headers=headers) as session:
            topic_tasks = [process_topic(session, topic, semaphore) for topic in unique_topics]
            await asyncio.gather(*topic_tasks)

        # 3. Generate Report
        print(f"📊 Validation Complete. Checked {len(CHECKED_URLS)} unique assets across {len(unique_topics)} topics.")
        print(f"❌ 404 Broken assets found: {len(RESULTS)}")
        
        df = pd.DataFrame(RESULTS)
        # Sort by Topic then Category
        if not df.empty:
            df = df.sort_values(by=['Topic', 'Category'])
        
        with pd.ExcelWriter(REPORT_FILENAME, engine='openpyxl') as writer:
            if not df.empty:
                df.to_excel(writer, sheet_name='Broken Assets', index=False)
            else:
                pd.DataFrame([{'Message': 'No 404 broken assets found'}]).to_excel(writer, sheet_name='Broken Assets', index=False)
            
            # Summary Sheet
            summary = [
                ['404 Asset Validation Summary'],
                ['Run Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
                ['Start URL', START_URL],
                ['Total Unique Topics Scanned', len(unique_topics)],
                ['Total Unique Assets Checked', len(CHECKED_URLS)],
                ['Total 404 Broken Assets', len(RESULTS)],
                ['Broken 404 Links', len([r for r in RESULTS if r['Category'] == 'Link'])],
                ['Broken 404 Images', len([r for r in RESULTS if r['Category'] == 'Image'])],
                ['Broken 404 SVG Icons', len([r for r in RESULTS if r['Category'] == 'SVG Icon'])],
                ['Broken 404 Background Images', len([r for r in RESULTS if r['Category'] == 'BG Image'])],
            ]
            pd.DataFrame(summary).to_excel(writer, sheet_name='Summary', header=False, index=False)

        print(f"✅ Excel report saved: {REPORT_FILENAME}")

    except Exception as e:
        print(f"💥 Critical Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
