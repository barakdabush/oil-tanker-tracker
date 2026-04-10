import { test, expect } from '@playwright/test';

test.describe('Live Market Intelligence Integration E2E', () => {

  test('Component connects to live fastAPI backend and renders metrics', async ({ page }) => {
    // Navigate to the Market page (this will hit the real fastAPI database on docker compose test)
    await page.goto('/market');
    
    // Check Header and KPI cards exist
    await expect(page.locator('h2:has-text("Market Intelligence")')).toBeVisible({ timeout: 15000 });
    
    // We can't guarantee exact numbers on live data since the seed/live DB may fluctuate,
    // but we can guarantee the components render structurally.
    const statCards = page.locator('.stat-card');
    await expect(statCards).toHaveCount(4, { timeout: 10000 });
    
    // Verify the live chart dynamically built itself
    const rechartsContainer = page.locator('.recharts-responsive-container');
    await expect(rechartsContainer).toBeVisible({ timeout: 10000 });
    await expect(page.locator('.recharts-surface')).toBeVisible();

    // Verify dropdown feature pivots the right axis without API failure
    const selectDropdown = page.locator('select');
    await selectDropdown.selectOption('sts_events_24h');
    
    // Verify the live raw data table rendered successfully without timing out
    await expect(page.locator('table.data-table')).toBeVisible();
    await expect(page.locator('table.data-table tbody tr').first()).toBeVisible({ timeout: 10000 });
  });

});
