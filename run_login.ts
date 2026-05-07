/**
 * Standalone login script – runs outside the Playwright test runner.
 * Performs AEM login and saves session cookies + storage-state to disk.
 *
 * Usage:  npx tsx run_login.ts
 */

import { chromium } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import * as dotenv from 'dotenv';

dotenv.config();

const BASE_URL   = process.env.BASE_URL   || '';
const USERNAME   = process.env.AEM_USERNAME || '';
const PASSWORD   = process.env.AEM_PASSWORD || '';
const SESSION_DIR = path.resolve('./auth-sessions');
const STORAGE_STATE_PATH = path.join(SESSION_DIR, 'storage-state.json');
const COOKIES_PATH       = path.join(SESSION_DIR, 'cookies.json');
const METADATA_PATH      = path.join(SESSION_DIR, 'session-metadata.json');

async function run() {
    console.log('=== AEM Login Script ===');
    console.log(`URL:  ${BASE_URL}`);
    console.log(`User: ${USERNAME}`);

    if (!BASE_URL || !USERNAME || !PASSWORD) {
        throw new Error('Missing BASE_URL, AEM_USERNAME or AEM_PASSWORD in .env');
    }

    fs.mkdirSync(SESSION_DIR, { recursive: true });

    const browser = await chromium.launch({ headless: false, slowMo: 100 });
    const context = await browser.newContext({
        viewport: { width: 1920, height: 1080 },
        ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();

    try {
        console.log('\n[1/5] Navigating to AEM…');
        await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
        await page.waitForTimeout(3000);

        let url = page.url();
        console.log(`      Current URL: ${url}`);

        // Wait for any redirect to settle (AEM login.html may redirect to IMS)
        try {
            await page.waitForURL(u => !u.toString().includes('$$login$$'), { timeout: 10_000 });
        } catch { /* page may stay on login URL, that's ok */ }
        await page.waitForTimeout(2000);
        url = page.url();
        console.log(`      Settled URL: ${url}`);

        // ── AEM native login ──────────────────────────────────────────────────
        if (url.includes('login.html') || url.includes('granite')) {
            console.log('\n[2/5] AEM native login detected.');

            // AEM uses Coral UI – inputs may be hidden, use evaluate to set values
            await page.waitForSelector('#username, input[name="j_username"]', { timeout: 15_000, state: 'attached' })
                .catch(async () => {
                    // AEM may have already redirected to SSO - check
                    const curUrl = page.url();
                    if (curUrl.includes('adobe.com') || curUrl.includes('adobeid')) {
                        console.log('      AEM immediately redirected to Adobe IMS SSO.');
                        await handleAdobeSSO(page, USERNAME, PASSWORD);
                        return;
                    }
                    throw new Error('Username field not found on AEM login page.');
                });

            await page.evaluate(({ u, p }) => {
                const userInput = document.querySelector<HTMLInputElement>('#username, input[name="j_username"]');
                const passInput = document.querySelector<HTMLInputElement>('#password, input[name="j_password"]');
                if (userInput) { userInput.value = u; userInput.dispatchEvent(new Event('input', { bubbles: true })); }
                if (passInput) { passInput.value = p; passInput.dispatchEvent(new Event('input', { bubbles: true })); }
            }, { u: USERNAME, p: PASSWORD });

            console.log('      Filled credentials via evaluate.');

            // Click submit button
            await page.locator('#submit-button, button[type="submit"], input[type="submit"], .coral-Button--primary').first().click({ force: true });
            console.log('      Submitted credentials.');

            await page.waitForURL(u => !u.toString().includes('login.html'), { timeout: 60_000 })
                .catch(() => console.warn('      Warning: did not navigate away from login page'));

            // AEM login may redirect to Adobe IMS SSO – handle it if so
            await page.waitForTimeout(3000);
            const afterAemUrl = page.url();
            if (afterAemUrl.includes('adobe.com') || afterAemUrl.includes('adobeid')) {
                console.log('      AEM redirected to Adobe IMS SSO, continuing…');
                await handleAdobeSSO(page, USERNAME, PASSWORD);
            }

        // ── Adobe IMS / SSO login ─────────────────────────────────────────────
        } else if (url.includes('adobe.com') || url.includes('adobeid')) {
            console.log('\n[2/5] Adobe IMS SSO login detected.');
            await handleAdobeSSO(page, USERNAME, PASSWORD);

        } else {
            console.log('\n[2/5] Already authenticated or unrecognised page – skipping login.');
        }

        await page.waitForTimeout(5000);
        url = page.url();
        console.log(`\n[3/5] Post-login URL: ${url}`);

        // ── Save session ──────────────────────────────────────────────────────
        console.log('\n[4/5] Saving session…');

        // storage-state (used by Playwright storageState config option)
        await context.storageState({ path: STORAGE_STATE_PATH });
        console.log(`      storage-state.json → ${STORAGE_STATE_PATH}`);

        // cookies
        const cookies = await context.cookies();
        fs.writeFileSync(COOKIES_PATH, JSON.stringify(cookies, null, 2));
        console.log(`      cookies.json (${cookies.length} cookies) → ${COOKIES_PATH}`);

        // metadata
        const metadata = {
            createdAt: new Date().toISOString(),
            username: USERNAME,
            cookieCount: cookies.length,
            expiresAt: new Date(Date.now() + 12 * 60 * 60 * 1000).toISOString(),
        };
        fs.writeFileSync(METADATA_PATH, JSON.stringify(metadata, null, 2));
        console.log(`      session-metadata.json → ${METADATA_PATH}`);

        console.log('\n[5/5] ✅ Session saved successfully!');
        console.log(`      Valid until: ${metadata.expiresAt}`);

    } catch (err) {
        console.error('\n❌ Login failed:', err);
        await page.screenshot({ path: path.join(SESSION_DIR, 'login-failure.png'), fullPage: true });
        console.log(`   Screenshot saved to ${path.join(SESSION_DIR, 'login-failure.png')}`);
        process.exit(1);
    } finally {
        await browser.close();
    }
}

run();

async function handleAdobeSSO(page: import('@playwright/test').Page, username: string, password: string) {
    // Step 1 – Adobe IMS: fill email if visible
    try {
        await page.waitForSelector('input[type="email"], #EmailPage-EmailField', { timeout: 10_000, state: 'visible' });
        await page.fill('input[type="email"], #EmailPage-EmailField', username);
        await page.locator(
            'button[data-id="EmailPage-ContinueButton"], button:has-text("Continue")'
        ).first().click();
        console.log('      [SSO] Submitted email.');
        await page.waitForTimeout(3000);
    } catch {
        console.log('      [SSO] Email field not found, continuing to next step…');
    }

    // Step 2 – May land on Avaya SSO (sso.avaya.com) or Adobe IMS password page
    // Wait up to 15s for the page to settle
    await page.waitForTimeout(3000);
    const interimUrl = page.url();
    console.log(`      [SSO] Interim URL: ${interimUrl}`);

    // Step 3 – Fill password (visible or hidden – try both)
    try {
        await page.waitForSelector('input[type="password"]', { timeout: 20_000, state: 'attached' });

        // Try visible fill first
        const isVisible = await page.locator('input[type="password"]').first().isVisible();
        if (isVisible) {
            await page.fill('input[type="password"]', password);
        } else {
            // Force-fill hidden field via evaluate
            await page.evaluate((p) => {
                const inputs = document.querySelectorAll<HTMLInputElement>('input[type="password"]');
                inputs.forEach(input => {
                    input.removeAttribute('aria-hidden');
                    input.style.display = 'block';
                    input.value = p;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                });
            }, password);
        }
        console.log('      [SSO] Filled password.');
    } catch {
        console.log('      [SSO] Password field not found, trying to proceed…');
    }

    // Step 4 – Click Sign In / Submit
    try {
        await page.locator(
            'button[data-id="PasswordPage-SignInButton"], button:has-text("Sign in"), button:has-text("Sign In"), input[type="submit"], button[type="submit"]'
        ).first().click({ force: true });
        console.log('      [SSO] Clicked sign-in button.');
    } catch {
        console.log('      [SSO] Could not find sign-in button, pressing Enter…');
        await page.keyboard.press('Enter');
    }

    // Step 5 – Wait for full redirect back to AEM (may go through multiple hops)
    console.log('      [SSO] Waiting for redirect back to AEM…');
    await page.waitForURL(
        u => u.toString().includes('adobeaemcloud.com') && !u.toString().includes('services.adobe.com'),
        { timeout: 90_000 }
    ).catch(() => console.warn(`      [SSO] Warning: final URL is ${page.url()}`));

    await page.waitForTimeout(4000);
    console.log(`      [SSO] Final URL: ${page.url()}`);
}
