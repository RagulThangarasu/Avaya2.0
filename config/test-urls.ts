import * as path from 'path';
import * as fs from 'fs';

// Load the common test URLs configuration
const testUrlsPath = path.resolve(__dirname, './test-urls.json');
const testUrlsData = JSON.parse(fs.readFileSync(testUrlsPath, 'utf-8'));

/**
 * Get Stage URL
 */
export function getStageUrl(): string {
  return testUrlsData.stage;
}

/**
 * Get Production URL
 */
export function getProdUrl(): string {
  return testUrlsData.production;
}

/**
 * Get both URLs
 */
export function getUrls() {
  return {
    stage: testUrlsData.stage,
    production: testUrlsData.production,
  };
}

export default testUrlsData;
