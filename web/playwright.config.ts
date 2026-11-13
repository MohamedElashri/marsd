import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
const python =
  process.env.MARSD_PYTHON ||
  path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
export default defineConfig({
  testDir: './tests',
  reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
  timeout: 120000,
  expect: { timeout: 15000 },
  globalTimeout: 300000,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8765',
    actionTimeout: 15000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `"${python}" ../scripts/e2e_server.py`,
    url: 'http://127.0.0.1:8765/health',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
