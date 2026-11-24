import { test, expect } from '@playwright/test';

async function mockMapApis(page: import('@playwright/test').Page) {
  await page.route('**/api/vessels*', async (route) => {
    await route.fulfill({ json: { vessels: [] } });
  });
  await page.route('**/api/ports*', (route) => route.fulfill({ json: { ports: [] } }));
  await page.route('**/api/chokepoints*', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/alerts/sts-events*', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/satellite*', (route) => route.fulfill({ json: { detections: [] } }));
}

test.describe('Map Distance Measurement Tool', () => {
  test('enables/disables, measures per-part & total distance, ignores drags, undoes and clears trail', async ({ page }) => {
    await mockMapApis(page);
    await page.goto('/map');

    const mapEl = page.locator('.leaflet-container');
    await expect(mapEl).toBeVisible({ timeout: 15000 });

    const toggleBtn = page.getByTestId('measure-toggle-btn');
    await expect(toggleBtn).toBeVisible();
    await expect(toggleBtn).toHaveAttribute('aria-pressed', 'false');

    // Enable measure mode
    await toggleBtn.click();
    await expect(toggleBtn).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('measure-hud')).toBeVisible();

    const box = await mapEl.boundingBox();
    expect(box).not.toBeNull();
    const cx = box!.x + box!.width / 2;
    const cy = box!.y + box!.height / 2;

    // Place 3 points via distinct clicks
    await page.mouse.click(cx - 80, cy - 40);
    await page.mouse.click(cx + 40, cy - 40);
    await page.mouse.click(cx + 80, cy + 50);

    await expect(page.locator('.measure-point-marker')).toHaveCount(3);
    await expect(page.locator('.measure-segment-label')).toHaveCount(2);
    await expect(page.getByTestId('measure-total')).toContainText('NM');
    await expect(page.getByTestId('measure-total')).toContainText('km');

    // Expand per-leg breakdown
    await page.getByTestId('measure-legs-btn').click();
    await expect(page.getByTestId('measure-legs-list')).toBeVisible();
    await expect(page.locator('.measure-leg-row')).toHaveCount(2);

    // Dragging across the map MUST NOT add a new point
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    await page.mouse.move(cx - 160, cy + 80, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(300);

    await expect(page.locator('.measure-point-marker')).toHaveCount(3);
    await expect(page.locator('.measure-segment-label')).toHaveCount(2);

    // Undo last point
    await page.getByTestId('measure-undo-btn').click();
    await expect(page.locator('.measure-point-marker')).toHaveCount(2);
    await expect(page.locator('.measure-segment-label')).toHaveCount(1);

    // Clear entire trail
    await page.getByTestId('measure-clear-btn').click();
    await expect(page.locator('.measure-point-marker')).toHaveCount(0);
    await expect(page.locator('.measure-segment-label')).toHaveCount(0);

    // Disable measure mode -> clicking map should not add points
    await toggleBtn.click();
    await expect(toggleBtn).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByTestId('measure-hud')).toBeHidden();

    await page.mouse.click(cx, cy);
    await expect(page.locator('.measure-point-marker')).toHaveCount(0);
  });

  test('is mobile friendly and does not add points on mobile drag', async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 375, height: 812 },
      hasTouch: true,
      isMobile: true,
    });
    const page = await context.newPage();
    await mockMapApis(page);
    await page.goto('/map');

    const mapEl = page.locator('.leaflet-container');
    await expect(mapEl).toBeVisible({ timeout: 15000 });

    const toggleBtn = page.getByTestId('measure-toggle-btn');
    await expect(toggleBtn).toBeVisible();
    await toggleBtn.tap();

    const hud = page.getByTestId('measure-hud');
    await expect(hud).toBeVisible();
    const hudBox = await hud.boundingBox();
    expect(hudBox).not.toBeNull();
    expect(hudBox!.x).toBeGreaterThanOrEqual(0);
    expect(hudBox!.x + hudBox!.width).toBeLessThanOrEqual(375);

    const box = await mapEl.boundingBox();
    expect(box).not.toBeNull();
    const cx = box!.x + box!.width / 2;
    const cy = box!.y + box!.height / 2;

    // Tap two points
    await page.touchscreen.tap(cx - 50, cy - 30);
    await page.touchscreen.tap(cx + 50, cy + 20);
    await expect(page.locator('.measure-point-marker')).toHaveCount(2);
    await expect(page.locator('.measure-segment-label')).toHaveCount(1);

    // Drag across screen -> should NOT add a 3rd point
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    await page.mouse.move(cx - 100, cy + 60, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(300);

    await expect(page.locator('.measure-point-marker')).toHaveCount(2);

    await context.close();
  });
});
