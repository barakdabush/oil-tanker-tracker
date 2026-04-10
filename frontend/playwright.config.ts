import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    // If running live E2E against docker-compose.test.yml, use 3002. Otherwise 3333 for the local webServer.
    baseURL: process.env.TEST_ENV === 'live' ? 'http://localhost:3002' : 'http://localhost:3333',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'PLAYWRIGHT_TESTING=1 PORT=3333 npm run dev',
    url: 'http://localhost:3333',
    reuseExistingServer: false,
    timeout: 120 * 1000,
  },

});
