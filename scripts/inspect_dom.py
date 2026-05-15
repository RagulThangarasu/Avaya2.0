import asyncio, json
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        # Check PROD page
        ctx = await browser.new_context()
        page = await ctx.new_page()
        prod_url = "https://documentation.avaya.com/bundle/AdministeringAvayaAuraAdminPortal/page/Purpose.html"
        await page.goto(prod_url, wait_until='networkidle', timeout=30000)
        
        prod_info = await page.evaluate('''() => {
            const info = {};
            const selectors = ['main', 'article', '.content', '.main-content', '.content-body', '.topic-content', '.body-content', '[role="main"]', '.book-content', '.page-content', '#content', '#main-content', '.article-content', '.doc-content', '.section'];
            selectors.forEach(sel => {
                const el = document.querySelector(sel);
                if (el) {
                    info[sel] = { h2: el.querySelectorAll('h2').length, h3: el.querySelectorAll('h3').length, p: el.querySelectorAll('p').length, textLen: el.innerText.length };
                }
            });
            info._totals = { h2: document.querySelectorAll('h2').length, h3: document.querySelectorAll('h3').length, p: document.querySelectorAll('p').length };
            info._bodyChildren = Array.from(document.body.children).map(c => ({ tag: c.tagName, classes: c.className.substring(0,120), id: c.id, textLen: c.innerText?.length || 0 }));
            // Also get the actual h2/h3/p text to see what's being picked up
            info._sampleH2 = Array.from(document.querySelectorAll('h2')).slice(0,3).map(e => e.innerText.trim().substring(0,80));
            info._sampleP = Array.from(document.querySelectorAll('p')).slice(0,5).map(e => e.innerText.trim().substring(0,80));
            return info;
        }''')
        print("=== PROD ===")
        print(json.dumps(prod_info, indent=2))
        
        # Check STAGE page
        ctx2 = await browser.new_context(storage_state='auth-sessions/storage-state.json')
        page2 = await ctx2.new_page()
        stage_url = "https://publish-p181473-e1910301.adobeaemcloud.com/en-us/home/bundle/avaya-aura-admin-portal/AdministeringAvayaAuraAdminPortal/Purpose.html"
        await page2.goto(stage_url, wait_until='networkidle', timeout=30000)
        
        stage_info = await page2.evaluate('''() => {
            const info = {};
            const selectors = ['main', 'article', '.content', '.main-content', '.content-body', '.topic-content', '.body-content', '[role="main"]', '.book-content', '.page-content', '#content', '#main-content', '.article-content', '.doc-content', '.section'];
            selectors.forEach(sel => {
                const el = document.querySelector(sel);
                if (el) {
                    info[sel] = { h2: el.querySelectorAll('h2').length, h3: el.querySelectorAll('h3').length, p: el.querySelectorAll('p').length, textLen: el.innerText.length };
                }
            });
            info._totals = { h2: document.querySelectorAll('h2').length, h3: document.querySelectorAll('h3').length, p: document.querySelectorAll('p').length };
            info._bodyChildren = Array.from(document.body.children).map(c => ({ tag: c.tagName, classes: c.className.substring(0,120), id: c.id, textLen: c.innerText?.length || 0 }));
            info._sampleH2 = Array.from(document.querySelectorAll('h2')).slice(0,3).map(e => e.innerText.trim().substring(0,80));
            info._sampleP = Array.from(document.querySelectorAll('p')).slice(0,5).map(e => e.innerText.trim().substring(0,80));
            return info;
        }''')
        print("\n=== STAGE ===")
        print(json.dumps(stage_info, indent=2))
        
        await browser.close()

asyncio.run(inspect())
