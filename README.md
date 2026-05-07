# Avayaa 2.0 — Content Validation Framework

Playwright-based automation framework that validates content parity between **AEM Stage** (author) and **Production** (documentation.avaya.com).

---

## Prerequisites

- **Node.js** ≥ 18
- **npm** ≥ 9
- Playwright browsers installed

```bash
npm install
npx playwright install chromium
```

---

## Configuration

### `config/test-urls.json`

Single source of truth for Stage & Prod URLs. All tests derive their target URLs from this file.

```json
{
  "stage": "https://author-p181473-e1910301.adobeaemcloud.com/content/aemsites/en-us/bundle/<BundleName>/<slug>.html?wcmmode=disabled",
  "production": "https://documentation.avaya.com/bundle/<BundleName>/page/<Slug>.html"
}
```

### `.env`

Required for AEM Stage authentication:

```env
BASE_URL=https://author-p181473-e1910301.adobeaemcloud.com
AEM_USERNAME=your-email@avaya.com
AEM_PASSWORD=your-password
```

---

## Tests

### 1. Content Parity (`content-parity.spec.ts`)

Validates that all Stage topics exist on Prod with matching content.

```bash
npm run test:content-parity
```

**What it does:**
| Step | Description |
|------|-------------|
| 1 | Extracts left-nav topics from Stage (`nav.cmp-navigation`). If session expired, auto-runs login. |
| 2 | Bidirectional parity check — what's in Stage must be in Prod and vice versa. |
| 3 | Deep content comparison (parallel, 12 pages at once): title, headings, paragraphs, tables, tags, versions, text length. |
| 4 | Generates Excel report. |

**Output:** `reports/content-parity.xlsx`

---

### 2. Left-Nav TOC Validation (`leftnav-toc-validation.spec.ts`)

Captures the full TOC tree from Stage and Prod, then compares structure.

```bash
npx playwright test tests/content-validation/leftnav-toc-validation.spec.ts --reporter=list
```

**What it checks:**
- ❌ **Missing topics** — in Stage but not Prod (or vice versa)
- 🔀 **Sequence order** — topics that appear in a different position
- 🔤 **Case mismatch** — title casing differs between environments
- ®™ **Symbol mismatch** — special characters (®, ™, ©) missing or different

**Output:** `reports/leftnav-toc-validation.xlsx`

### 3. PDF Validation (`pdf-validation.spec.ts`)

Compares PDF files from `PDF/prod/` and `PDF/stage/` line by line, capturing all content and formatting differences.

```bash
npm run test:pdf-validation
```

**What it checks:**
- 📄 **Line-by-line content** — every text line compared between Prod and Stage PDFs
- ❌ **Missing / extra lines** — content in one PDF but not the other
- 📊 **Table structure** — row counts, cell-by-cell content comparison
- 📐 **Formatting** — whitespace, padding, line breaks, paragraph spacing
- ✂️ **Text truncation/overflow** — partial content differences
- 🔤 **Case & punctuation** — casing and special character mismatches
- 📑 **Page count** — different number of pages
- 🏷️ **PDF metadata** — title, author, creator differences

**Setup:** Place PDF files in `PDF/prod/` and `PDF/stage/`. Files are matched by normalized name.

**Output:** `reports/pdf-validation.xlsx` (5 sheets: Summary, All Issues, Table Issues, Formatting Issues, Line Diff)

---

## Authentication

The framework handles AEM login **automatically**:

1. On first run (or when session expires), it spawns `run_login.ts`
2. Performs Adobe IMS SSO login using credentials from `.env`
3. Saves session to `auth-sessions/storage-state.json`
4. Subsequent runs reuse the saved session (valid ~12 hours)

**Manual login (if needed):**
```bash
npx tsx run_login.ts
```

---

## Reports

All reports are written to `reports/` and **overwrite** on each run (no duplicates):

| File | Description |
|------|-------------|
| `content-parity.xlsx` | Full content comparison: matched topics, missing topics, content issues |
| `leftnav-toc-validation.xlsx` | TOC structure: sequence, missing, case/symbol issues |
| `pdf-validation.xlsx` | PDF line-by-line diff: content, tables, formatting, metadata |

### Excel Report Sheets (content-parity)

1. **Summary** — metrics overview
2. **All Topics** — every topic with comparison data
3. **Missing in Prod** — topics in Stage but not published
4. **Missing in Stage** — topics in Prod but not in Stage
5. **Content Issues** — detailed issue breakdown with severity

---

## Project Structure

```
├── config/
│   └── test-urls.json          # Stage & Prod URLs (source of truth)
├── auth-sessions/
│   ├── storage-state.json      # Playwright session (auto-generated)
│   ├── cookies.json            # Raw cookies
│   └── session-metadata.json   # Session validity info
├── tests/
│   └── content-validation/
│       ├── content-parity.spec.ts         # Main content comparison test
│       ├── leftnav-toc-validation.spec.ts # TOC structure validation
│       └── pdf-validation.spec.ts         # PDF line-by-line comparison
├── PDF/
│   ├── prod/                   # Production PDF files
│   └── stage/                  # Stage PDF files
├── reports/                    # Generated Excel reports (git-ignored)
├── run_login.ts                # Standalone AEM login script
├── playwright.config.ts        # Playwright configuration
├── package.json
└── .env                        # Credentials (git-ignored)
```

---

## Quick Commands

```bash
# Run content parity validation
npm run test:content-parity

# Run PDF validation
npm run test:pdf-validation

# Run TOC validation only
npx playwright test tests/content-validation/leftnav-toc-validation.spec.ts --reporter=list

# Run all content validation tests
npx playwright test tests/content-validation/ --reporter=list

# Force re-login
npx tsx run_login.ts

# Clean all reports
npm run clean:reports
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Stage returns 0 topics | Session expired. Run `npx tsx run_login.ts` or let auto-login handle it. |
| Prod returns 0 topics | Normal — Prod has no visible TOC. URLs are constructed from Stage. |
| Test timeout | Increase `test.setTimeout()` or check network connectivity. |
| TypeScript errors | Run `npx tsc --noEmit --skipLibCheck` to verify. |
