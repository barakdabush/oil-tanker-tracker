import { test, expect } from '@playwright/test';

test.describe('Mocked UI E2E (PR Guard)', () => {
  test('App loads and toggles "All" history cleanly', async ({ page }) => {
    // 1. Setup Network Mocks
    await page.route('**/api/vessels*', async (route) => {
      await route.fulfill({ json: {
        vessels: [
           { mmsi: 555555, name: 'MOCKED_MMSI', last_seen: new Date().toISOString(), last_lat: 10.0, last_lon: 20.0, last_speed: 10.0 }
        ]
      }});
    });
    
    await page.route('**/api/ports*', route => route.fulfill({ json: { ports: [] } }));
    await page.route('**/api/chokepoints*', route => route.fulfill({ json: [] }));
    await page.route('**/api/vessels/*/trail*', route => route.fulfill({ 
        json: { positions: [{latitude: 10.0, longitude: 20.0}, {latitude: 11.0, longitude: 21.0}] } 
    }));

    // 2. Execute UI flow
    await page.goto('/map');
    await expect(page.locator('.leaflet-container')).toBeVisible({ timeout: 15000 });
    
    // Search deterministic mocked data
    await page.getByPlaceholder('Search Ship by Name or MMSI...').fill('MOCKED');
    await page.locator('text=MOCKED_MMSI').click();

    // Verify side panel & toggle our feature
    await expect(page.locator('h3:has-text("🚢 MOCKED_MMSI")')).toBeVisible();
    const allButton = page.getByRole('button', { name: 'All', exact: true });
    await allButton.click();
    
    // Validate CSS logic and mocked DOM mapping
    await expect(allButton).toHaveCSS('color', 'rgb(0, 255, 255)');
    await expect(page.locator('path.animated-dash-line-555555')).toBeAttached();
  });
});
