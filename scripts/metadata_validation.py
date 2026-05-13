import os
import sys
import json
import time
import pandas as pd
from playwright.sync_api import sync_playwright

def run_validation():
    stage_url = os.environ.get('STAGE_URL')
    csv_path = os.environ.get('MASTER_CSV')
    report_path = os.environ.get('REPORT_FILENAME', 'metadata-validation-report.xlsx')
    auth_state = os.path.abspath(os.path.join(os.path.dirname(__file__), '../auth-sessions/storage-state.json'))

    if not stage_url or not csv_path:
        print("❌ Error: STAGE_URL and MASTER_CSV environment variables are required.")
        sys.exit(1)

    print(f"🚀 Starting Python Metadata Validation...")
    print(f"🔗 Stage: {stage_url}")
    print(f"📄 CSV: {csv_path}")

    # 1. Load Expected Data
    try:
        df_csv = pd.read_csv(csv_path, header=None)
        if len(df_csv) >= 2:
            keys = df_csv.iloc[0].tolist()
            values = df_csv.iloc[1].tolist()
            expected_data = {str(k).strip(): str(v).strip() for k, v in zip(keys, values) if pd.notna(k)}
        else:
            # Fallback to standard 2-column format
            df_csv = pd.read_csv(csv_path)
            expected_data = {str(row[0]).strip(): str(row[1]).strip() for row in df_csv.values}
        
        print(f"✅ Loaded {len(expected_data)} keys from CSV.")
    except Exception as e:
        print(f"❌ Error reading CSV: {e}")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        # Load auth state if exists
        context_args = {}
        if os.path.exists(auth_state):
            context_args['storage_state'] = auth_state
            
        context = browser.new_context(**context_args)
        page = context.new_page()

        print("> Navigating to AEM...")
        page.goto(stage_url, wait_until="domcontentloaded", timeout=60000)

        # Handle Editor to Properties transition
        if "/editor.html/" in page.url and "properties.html" not in page.url:
            print("> Detected Editor view. Navigating to Properties...")
            
            # Guides Editor
            guides_btn = page.locator('button.coral3-Button[title="More"], button[aria-label="More Options"]').first
            if guides_btn.is_visible():
                guides_btn.click()
                page.locator('coral-anchorlist-item:has-text("Properties"), coral-list-item:has-text("Properties")').click()
            else:
                # Sites Editor
                page.locator('button#pageinfo-trigger').click()
                page.locator('a.cq-dialog-page-info-properties').click()
            
            page.wait_for_load_state("networkidle")

        print("> Waiting for Properties UI...")
        page.wait_for_selector('coral-tab, .coral3-Tab', timeout=30000)
        time.sleep(2) # Stabilization

        # Switch to Publication Metadata Tab
        pub_tab = page.locator('coral-tab, .coral3-Tab').filter(has_text="Publication Metadata")
        if pub_tab.is_visible():
            print("> Switching to Publication Metadata tab...")
            pub_tab.click()
            time.sleep(1)

        # Extraction Logic
        print("> Extracting fields...")
        actual_data = page.evaluate("""
            () => {
                const data = {};
                const panel = document.querySelector('coral-panel.is-selected, .is-active') || document.body;
                const wrappers = panel.querySelectorAll('coral-form-field-wrapper, .coral-Form-fieldwrapper, .coral-Form-field');
                
                wrappers.forEach(w => {
                    const labelEl = w.querySelector('label, .coral-Form-fieldlabel');
                    if (labelEl) {
                        const label = labelEl.innerText.split('*')[0].trim();
                        let value = '';
                        const input = w.querySelector('input:not([type="hidden"]), textarea');
                        if (input) {
                            value = (input.type === 'checkbox' || input.type === 'radio') ? (input.checked ? 'true' : 'false') : input.value;
                        } else {
                            const sel = w.querySelector('coral-select-label, .coral-Select-label');
                            if (sel) value = sel.innerText;
                        }
                        if (label) data[label] = value.trim();
                    }
                });
                return data;
            }
        """)

        # Comparison
        results = []
        for key, expected in expected_data.items():
            actual = actual_data.get(key, "NOT FOUND")
            status = "PASS" if actual.lower() == expected.lower() else ("MISSING" if actual == "NOT FOUND" else "FAIL")
            results.append({
                "Metadata Key": key,
                "Expected Value": expected,
                "Actual Value": actual,
                "Status": status
            })

        # Generate Report
        print(f"> Generating report: {report_path}")
        df_results = pd.DataFrame(results)
        
        # Styling with Pandas Excel writer
        with pd.ExcelWriter(report_path, engine='openpyxl') as writer:
            df_results.to_excel(writer, index=False, sheet_name='Metadata Validation')
            
            # Simple styling
            worksheet = writer.sheets['Metadata Validation']
            for cell in worksheet["D"]: # Status column
                if cell.value == "PASS":
                    cell.style = 'Good'
                elif cell.value == "FAIL":
                    cell.style = 'Bad'
                elif cell.value == "MISSING":
                    cell.style = 'Neutral'

        print(f"✅ Validation Complete. Report saved to {report_path}")
        browser.close()

if __name__ == "__main__":
    run_validation()
