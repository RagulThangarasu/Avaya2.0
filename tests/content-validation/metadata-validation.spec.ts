import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import * as ExcelJS from 'exceljs';
import { parse } from 'csv-parse/sync';

const AUTH_STATE = path.resolve(__dirname, '../../auth-sessions/storage-state.json');
const REPORTS_DIR = path.resolve(__dirname, '../../reports');

test.describe('AEM Metadata Validation', () => {
  test.setTimeout(120_000);

  test('Validate page properties against master CSV', async ({ browser }) => {
    const stageUrl = process.env.STAGE_URL;
    const masterCsvPath = process.env.MASTER_CSV;

    if (!stageUrl || !masterCsvPath) {
      throw new Error('STAGE_URL and MASTER_CSV environment variables are required');
    }

    // 1. Load Expected Data from CSV
    console.log(`Reading master CSV: ${masterCsvPath}`);
    const csvContent = fs.readFileSync(masterCsvPath, 'utf-8');
    const records = parse(csvContent, {
      skip_empty_lines: true,
      trim: true
    });

    const expectedPairs: { key: string; expected: string }[] = [];
    if (records.length >= 2) {
      // Columnar format: Row 0 = Keys, Row 1 = Values
      const keys = records[0];
      const values = records[1];
      keys.forEach((key: string, i: number) => {
        if (key) {
          expectedPairs.push({ key: key.trim(), expected: (values[i] || '').trim() });
        }
      });
    } else {
      // Fallback: Row-based format (Key, Value)
      records.forEach((row: string[]) => {
        if (row.length >= 2) {
          expectedPairs.push({ key: row[0].trim(), expected: row[1].trim() });
        }
      });
    }

    console.log(`Loaded ${expectedPairs.length} metadata pairs to validate.`);

    // 2. Setup Browser & Auth
    const context = await browser.newContext({
      storageState: fs.existsSync(AUTH_STATE) ? AUTH_STATE : undefined
    });
    const page = await context.newPage();

    // 3. Navigate to Stage URL (Editor or Properties directly)
    console.log(`Navigating to Stage: ${stageUrl}`);
    try {
      await page.goto(stageUrl, { waitUntil: 'load', timeout: 60000 });
    } catch (e) {
      console.warn('⚠️ Initial navigation timeout, continuing anyway...');
    }

    // Check if we are stuck on Login Page
    if (await page.isVisible('input[name="j_username"], #username')) {
      console.error('❌ ERROR: Stuck on Login Page. Please ensure AEM session is active.');
      await page.screenshot({ path: path.join(REPORTS_DIR, 'metadata-login-error.png') });
      throw new Error('Authentication required. Run AEM Login first.');
    }

    // 4. Navigate to Properties if we are in Editor (Handles both Sites and Guides Editor)
    if (page.url().includes('/editor.html/') && !page.url().includes('properties.html')) {
      console.log('Detected Editor view. Navigating to Page Properties...');
      
      // Try Guides Editor first (based on screenshot)
      const guidesContextBtn = page.locator('button.coral3-Button[title="More"], button[aria-label="More Options"], .tree-item-action-btn').first();
      const sitesPageInfoBtn = page.locator('button#pageinfo-trigger, button[title="Page Information"]');

      if (await guidesContextBtn.isVisible()) {
        console.log('Detected Guides Editor UI. Opening context menu...');
        await guidesContextBtn.click();
        const propertiesMenuItem = page.locator('coral-anchorlist-item:has-text("Properties"), coral-list-item:has-text("Properties")');
        await propertiesMenuItem.waitFor({ state: 'visible', timeout: 5000 });
        await propertiesMenuItem.click();
      } else if (await sitesPageInfoBtn.isVisible()) {
        console.log('Detected Sites Editor UI. Opening Page Information...');
        await sitesPageInfoBtn.click();
        const sitesPropertiesBtn = page.locator('a.cq-dialog-page-info-properties, a[href*="properties.html"]');
        await sitesPropertiesBtn.waitFor({ state: 'visible', timeout: 5000 });
        await sitesPropertiesBtn.click();
      } else {
        console.error('❌ Could not find "Properties" menu in current Editor view.');
      }
      await page.waitForLoadState('networkidle');
    }
    
    // Wait for the properties dialog to be ready
    console.log('Waiting for Properties UI to initialize...');
    try {
      await page.waitForSelector('coral-tab, .coral-TabPanel-tab, coral-tab-label, .coral3-Tab', { timeout: 30000 });
    } catch (e) {
      console.log('⚠️ Tabs not found, checking for Guides specific form wrappers...');
      await page.waitForSelector('.guides-properties-container, .cq-dialog-content-page, form.foundation-form', { timeout: 10000 }).catch(() => {});
    }

    // Small stabilization wait for AEM JS to settle
    await page.waitForTimeout(2500);

    // 5. Navigate to "Publication Metadata" tab
    console.log('Locating "Publication Metadata" tab...');
    const pubTab = page.locator('coral-tab, .coral-TabPanel-tab, coral-tab-label, .coral3-Tab').filter({ hasText: /Publication Metadata/i });
    if (await pubTab.isVisible()) {
      await pubTab.click();
      console.log('Switched to Publication Metadata tab.');
      // Wait for the active panel to switch
      await page.waitForTimeout(1000); 
    } else {
      console.warn('⚠️ "Publication Metadata" tab not found. Validating all visible fields...');
    }

    // 6. DEEP EXTRACTION: Extract fields from the active panel
    console.log('Extracting metadata fields (Deep Search)...');
    const actualData = await page.evaluate(() => {
      const data: Record<string, string> = {};
      
      // Focus on the active panel content to avoid noise from other tabs
      const activePanel = document.querySelector('coral-panel.is-selected, .coral-TabPanel-content.is-active, .coral-TabPanel-pane.is-active') || document.body;
      
      // Find all field wrappers
      const wrappers = activePanel.querySelectorAll('coral-form-field-wrapper, .coral-Form-fieldwrapper, .coral-Form-field');
      
      wrappers.forEach(wrapper => {
        const labelEl = wrapper.querySelector('label.coral-Form-fieldlabel, .coral-Form-fieldlabel, label') as HTMLElement;
        if (labelEl) {
          // Clean label: remove required asterisk and extra whitespace
          const label = labelEl.innerText.split('*')[0].trim();
          if (!label) return;

          let value = '';
          // 1. Standard Inputs/Textarea
          const input = wrapper.querySelector('input:not([type="hidden"]), textarea') as HTMLInputElement;
          if (input) {
            if (input.type === 'checkbox' || input.type === 'radio') {
              value = input.checked ? 'true' : 'false';
            } else {
              value = input.value;
            }
          } 
          // 2. Coral Selects / Dropdowns
          if (!value) {
            const selectLabel = wrapper.querySelector('coral-select-label, .coral-Select-label, .coral-Select-button-text') as HTMLElement;
            if (selectLabel) value = selectLabel.innerText;
          }
          // 3. Multi-field / Tag Lists
          if (!value) {
            const tags = Array.from(wrapper.querySelectorAll('coral-tag')).map(t => (t as HTMLElement).innerText.trim());
            if (tags.length > 0) value = tags.join(', ');
          }

          if (label && value !== undefined) {
            data[label] = value.trim();
          }
        }
      });
      return data;
    });

    // 7. Compare and Record Results
    const results: any[] = [];
    for (const pair of expectedPairs) {
      const { key, expected } = pair;
      const actual = actualData[key] !== undefined ? actualData[key] : 'NOT FOUND IN UI';
      
      const status = (actual.toLowerCase() === expected.toLowerCase()) ? 'PASS' : (actual === 'NOT FOUND IN UI' ? 'MISSING' : 'FAIL');
      
      results.push({
        key,
        expected,
        actual,
        status
      });
    }

    // 8. Generate Excel Report
    await buildExcelReport(results, stageUrl);

    console.log(`\n✅ Metadata Validation Complete. Total: ${results.length}, Passed: ${results.filter(r => r.status === 'PASS').length}`);
  });
});

async function buildExcelReport(results: any[], stageUrl: string) {
  const wb = new ExcelJS.Workbook();
  const sheet = wb.addWorksheet('Metadata Validation');

  sheet.columns = [
    { header: 'Metadata Key', key: 'key', width: 30 },
    { header: 'Expected Value (Master CSV)', key: 'expected', width: 40 },
    { header: 'Actual Value (AEM Stage)', key: 'actual', width: 40 },
    { header: 'Status', key: 'status', width: 15 }
  ];

  // Header Styling
  const headerRow = sheet.getRow(1);
  headerRow.font = { bold: true, color: { argb: 'FFFFFFFF' } };
  headerRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1F4E79' } } as ExcelJS.FillPattern;

  results.forEach(res => {
    const row = sheet.addRow(res);
    const statusCell = row.getCell('status');
    
    if (res.status === 'PASS') {
      statusCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFD4EDDA' } } as ExcelJS.FillPattern;
      statusCell.font = { color: { argb: 'FF155724' } };
    } else if (res.status === 'FAIL') {
      statusCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFF8D7DA' } } as ExcelJS.FillPattern;
      statusCell.font = { color: { argb: 'FF721C24' } };
    } else {
      statusCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFF3CD' } } as ExcelJS.FillPattern;
      statusCell.font = { color: { argb: 'FF856404' } };
    }
  });

  if (!fs.existsSync(REPORTS_DIR)) fs.mkdirSync(REPORTS_DIR, { recursive: true });
  const filename = process.env.REPORT_FILENAME || 'metadata-validation-report.xlsx';
  const reportPath = path.isAbsolute(filename) ? filename : path.join(REPORTS_DIR, filename);
  
  await wb.xlsx.writeFile(reportPath);
  console.log(`📊 Report saved: ${reportPath}`);
}
