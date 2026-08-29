import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './visual-tests',
  outputDir: 'test-results/visual-quality',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI
    ? [['line'], ['html', { outputFolder: 'test-results/playwright-report', open: 'never' }]]
    : [['list']],
  use: {
    browserName: 'chromium',
    colorScheme: 'light',
    locale: 'ru-RU',
    reducedMotion: 'reduce',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure'
  }
});
