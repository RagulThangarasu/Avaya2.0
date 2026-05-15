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

## Running Server Locally

The web UI server provides a dashboard for running validations and viewing reports.

### Start the Server

```bash
# Install dependencies (if not already done)
npm install

# Start the server (runs on http://localhost:3000)
npm run dev
# or
node scripts/server.ts
```

The server will be available at: **http://localhost:3000**

### Web UI Features

#### 1. **TOC Parity Validation** (`/toc-parity.html`)
- Validates Stage vs Production table of contents
- Shows missing topics, sequence differences
- Generates Excel report

#### 2. **Content Validation** (`/content-deep-validation.html`)
- Compares page content (h2, h3, paragraphs only)
- Skips headers, footers, right navigation
- Shows word count differences and sample missing/extra terms
- **Details:**
  - Stage URL: `https://publish-p181473-e1910301.adobeaemcloud.com/...`
  - Production URL: `https://documentation.avaya.com/...`
  - Validates 213+ pages in parallel (10 concurrent)
  - Generates detailed Excel report with mismatch analysis

#### 3. **Report Viewer** (`/report-viewer.html`)
- View generated Excel reports in table format
- Filter and sort results
- Download reports

### Environment Variables

Create a `.env` file or set these environment variables:

```env
STAGE_URL=https://publish-p181473-e1910301.adobeaemcloud.com/en-us/home/bundle/avaya-aura-admin-portal/AdministeringAvayaAuraAdminPortal/
PROD_URL=https://documentation.avaya.com/bundle/AdministeringAvayaAuraAdminPortal/page/
PORT=3000
```

### API Endpoints

```bash
# Run TOC Parity Validation
POST /api/run/toc-parity
Body: { "stageUrl": "...", "prodUrl": "..." }

# Run Deep Content Validation
POST /api/run/deep-content-validation
Body: { "stageUrl": "...", "prodUrl": "..." }

# Get logs for a job
GET /api/logs/{jobId}

# Download report
GET /api/reports/{reportType}/{filename}
```

### Example Workflow

1. **Start server:**
   ```bash
   npm run dev
   ```

2. **Open browser:**
   ```
   http://localhost:3000
   ```

3. **Run validation:**
   - Navigate to "Content Validation"
   - Enter Stage URL and Production URL
   - Click "Run Validation"
   - Watch live progress

4. **View results:**
   - See statistics (✅ Matched, ⚠️ Partial, ❌ Mismatch)
   - Download Excel report
   - View in Report Viewer

### Output Locations

- **Reports:** `.ui_reports/` directory
- **Logs:** `.logs/` directory (if enabled)
- **Cached pages:** `.cache/` directory (if caching enabled)

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
