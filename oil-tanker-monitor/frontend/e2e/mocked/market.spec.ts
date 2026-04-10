import { test, expect } from '@playwright/test';

test.describe('Mocked Market Intelligence UI E2E', () => {
  test('Page loads properly and visualizes mocked features vs oil price', async ({ page }) => {
    // 1. Setup Network Mocks for the new ML features endpoint
    await page.route('**/api/global-oil-features/snapshots*', async (route) => {
      await route.fulfill({
        json: [
          {
            snapshot_date: '2026-04-09',
            total_active_vessels: 4000,
            vessels_in_transit: 2500,
            vessels_at_port: 1500,
            avg_fleet_speed: 12.5,
            vessels_idle_gt_48h: 50,
            dark_vessels_count: 5,
            new_ais_gaps_24h: 2,
            resolved_gaps_24h: 1,
            avg_gap_duration_hours: 6.5,
            sts_events_24h: 3,
            sts_confirmed_24h: 1,
            chokepoint_transits_24h: 80,
            strait_of_hormuz_transits: 20,
            cargo_events_24h: 150,
            estimated_volume_barrels_24h: 10000000.0,
            brent_close_usd: 85.50,
            wti_close_usd: 81.20
          },
          {
            snapshot_date: '2026-04-10',
            total_active_vessels: 4050,
            vessels_in_transit: 2600,
            vessels_at_port: 1450,
            avg_fleet_speed: 12.8,
            vessels_idle_gt_48h: 40,
            dark_vessels_count: 10,
            new_ais_gaps_24h: 5,
            resolved_gaps_24h: 2,
            avg_gap_duration_hours: 5.5,
            sts_events_24h: 5,
            sts_confirmed_24h: 2,
            chokepoint_transits_24h: 95,
            strait_of_hormuz_transits: 25,
            cargo_events_24h: 180,
            estimated_volume_barrels_24h: 12000000.0,
            brent_close_usd: 89.00,
            wti_close_usd: 84.50
          }
        ]
      });
    });

    // 2. Execute UI flow
    await page.goto('/market');
    
    // Check Header and KPI cards
    await expect(page.locator('h2:has-text("Market Intelligence")')).toBeVisible();
    await expect(page.locator('.stat-card-value').filter({ hasText: '$89.00' })).toBeVisible(); // Latest Brent
    await expect(page.locator('.stat-card-value').filter({ hasText: '$84.50' })).toBeVisible(); // Latest WTI
    await expect(page.locator('.stat-card-value').filter({ hasText: '4050' })).toBeVisible(); // Latest Active Vessels
    await expect(page.locator('.stat-card-value').filter({ hasText: '10' })).toBeVisible(); // Latest Dark Fleet

    // Check that Recharts SVG rendered
    const rechartsContainer = page.locator('.recharts-responsive-container');
    await expect(rechartsContainer).toBeVisible({ timeout: 10000 });
    await expect(page.locator('.recharts-surface')).toBeVisible(); // Actual SVG element

    // Test dropdown logic - when triggering STS events, verify table/charts adapt without crash
    const selectDropdown = page.locator('select');
    await selectDropdown.selectOption('sts_events_24h');
    
    // Check raw data table renders rows
    await expect(page.locator('table.data-table')).toBeVisible();
    // Verify the latest row data exists exactly as stringified
    await expect(page.locator('td').filter({ hasText: '2026-04-10' })).toBeVisible();
  });
});
