import os
import sys
import asyncio
from playwright.async_api import async_playwright

AUTH_STATE_PATH = os.path.join(os.getcwd(), 'auth-sessions', 'storage-state.json')

async def handle_cookies(page):
    try:
        for sel in ['#onetrust-accept-btn-handler', '#btn-accept-all', 'button:has-text("Accept")', '.cookie-accept']:
            if await page.locator(sel).is_visible(timeout=2000):
                await page.click(sel)
                print(f"  [Cookies] Successfully clicked consent selector: {sel}")
                await page.wait_for_timeout(1000)
                break
    except Exception as e:
        print(f"  [Cookies] Error dismissing overlay: {e}")

async def main():
    async with async_playwright() as p:
        print("🚀 Launching Chromium in GUI mode (headless=False) to bypass CDN restrictions...")
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        
        print(f"📂 Loading SSO session cookies from: {AUTH_STATE_PATH}")
        context = await browser.new_context(
            user_agent=user_agent,
            storage_state=AUTH_STATE_PATH if os.path.exists(AUTH_STATE_PATH) else None,
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()
        
        url = "https://documentation.avaya.com/bundle/UsingJ189IPPhoneSIP_r4.1.x/page/Avaya_J189_IP_Phone_overview.html"
        print(f"🔗 Navigating to Production: {url}")
        await page.goto(url, wait_until="load", timeout=30000)
        
        print("🧹 Clearing cookies overlay...")
        await handle_cookies(page)
        await page.wait_for_timeout(2000)
        
        print("🔍 Checking if already logged in or if Login button is needed...")
        logged_in_indicator = page.locator('span:has-text("Ragul Thangarasu"), .zDocsUserMenu')
        if await logged_in_indicator.count() > 0:
            print("🎉 Already logged in as Ragul Thangarasu!")
        else:
            login_selectors = [
                'button:has-text("Login")', 
                'a:has-text("Login")', 
                '.zDocsLoginButton', 
                'a[href*="login"]',
                'a.login-btn'
            ]
            
            login_btn = None
            for sel in login_selectors:
                if await page.locator(sel).count() > 0:
                    print(f"  Found login button selector: {sel}")
                    login_btn = page.locator(sel).first
                    break
            
            if login_btn:
                print("⚡ Clicking Login button to trigger SSO redirection...")
                await login_btn.click()
                print("⏳ Waiting for SSO authentication and redirection back to documentation center...")
                
                # Wait up to 15 seconds for the redirect chains to settle
                for i in range(15):
                    await page.wait_for_timeout(1000)
                    cur_url = page.url
                    print(f"  [{i+1}/15] Current redirect URL: {cur_url}")
                    # If we landed back on a bundle page or home page and see the topic body, we are done
                    if "bundle/" in cur_url and await page.locator('.zDocsTopicPageBody').count() > 0:
                        print("🎉 Redirection settled and body content is visible!")
                        break
            else:
                print("⚠️ No login button found, waiting for manual inspection...")
                await page.wait_for_timeout(5000)
        
        # Verify content
        body_count = await page.locator('.zDocsTopicPageBody').count()
        print(f"\n📊 Extraction Verification:")
        print(f"  .zDocsTopicPageBody count: {body_count}")
        if body_count > 0:
            text = await page.locator('.zDocsTopicPageBody').first.inner_text()
            print(f"  ✅ Content loaded successfully! Length: {len(text)} characters.")
            print(f"  Snippet:\n{text[:300]}\n")
            
            # Save the new consolidated session cookies back to storage-state.json!
            print(f"💾 Saving consolidated Production session to: {AUTH_STATE_PATH}")
            await context.storage_state(path=AUTH_STATE_PATH)
            print("✅ Session storage saved successfully!")
            success = True
        else:
            print("❌ Content failed to render.")
            success = False
            
        await browser.close()
        sys.exit(0 if success else 1)

if __name__ == '__main__':
    asyncio.run(main())
