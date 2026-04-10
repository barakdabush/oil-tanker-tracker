import { test, expect } from '@playwright/test';

// Use this test configuration if you map dynamically via Test DB seeding
test.describe('Live Full-Stack E2E (Deployment Guard)', () => {

  test('Maps genuine test-env database data and triggers live endpoints', async ({ page }) => {
    // 1. Load the Map App (Hits real FastAPI connected to Test DB)
    await page.goto('/map');
    await expect(page.locator('.leaflet-container')).toBeVisible({ timeout: 15000 });

    // 2. We use the seeded test database vessel MMSI (999999999) 
    const searchInput = page.getByPlaceholder('Search Ship by Name or MMSI...');
    await searchInput.fill('SEEDED_TEST_VESSEL');
    
    // Click on the matching database vessel in the search drop-down by its MMSI data-testid
    const searchResult = page.getByTestId('search-result-999999999');
    
    // Wait for the backend response from the real DB!
    await expect(searchResult).toBeVisible({ timeout: 15000 });
    await searchResult.click();
    
    // Ensure the side panel is open with the correct vessel
    await expect(page.locator('h3')).toContainText('SEEDED_TEST_VESSEL', { timeout: 15000 });

    // Trigger the feature triggering a real backend `/api/vessels/{mmsi}/trail?hours=0` API
    const allButton = page.getByTestId('trail-btn-0');
    // On slow environments, the panel might take a moment to appear
    await expect(allButton).toBeVisible({ timeout: 15000 });
    await allButton.click();

    // Verify that the live backend returned points by checking the DOM for SVG animated trails mapped to our seed MMSI
    await expect(page.locator('path.animated-dash-line-999999999')).toBeAttached({ timeout: 10000 });
  });

});
