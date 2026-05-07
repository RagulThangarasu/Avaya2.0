import { defineConfig } from '@playwright/test';
import * as dotenv from 'dotenv';

dotenv.config();

export default defineConfig({
  testDir: './tests',
  timeout: 1800_000, // 30 minutes for extraction tests
  expect: {
    timeout: 10_000,
  },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'reports/html', open: 'never' }],
  ],
  use: {
    headless: true,
    viewport: { width: 1920, height: 1080 },
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'Content Validation',
      testDir: './tests/content-validation',
      testMatch: /.*\.spec\.ts/,
      use: {
        storageState: './auth-sessions/storage-state.json',
      },
    },
    {
      name: 'Left Nav Extraction',
      testDir: './tests/leftnav',
      testMatch: /.*\.spec\.ts/,
      use: {
        storageState: './auth-sessions/storage-state.json',
      },
    },
    {
      name: 'Auth Tests',
      testDir: './tests/auth',
      testMatch: /.*\.spec\.ts/,
      use: {
        storageState: './auth-sessions/storage-state.json',
      },
    },
  ],
});
