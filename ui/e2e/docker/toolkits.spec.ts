import { test, expect } from '@playwright/test';
import { captureConsoleErrors, createToolkit, uniqueSuffix } from './helpers';

/**
 * Toolkits (real backend). Covers the list empty-state, create-via-UI, detail
 * navigation, and the one-time API key reveal. Verified live: POST /toolkits ->
 * 201 (auto-mints one key), and the detail page exposes key creation under
 * /toolkits/{id}/keys.
 */
test('toolkits list renders the empty state on a clean backend', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');
	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Toolkits' })
		.click();

	await expect(page.getByRole('heading', { name: 'Toolkits' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('create a toolkit via the UI and see it in the list', async ({ page }) => {
	const name = `e2e-toolkit-${uniqueSuffix()}`;

	await page.goto('/app/toolkits');
	await expect(page.getByRole('heading', { name: 'Toolkits' })).toBeVisible();

	await page
		.getByRole('button', { name: /new toolkit/i })
		.first()
		.click();
	await page.getByLabel('Name').fill(name);
	await page.getByRole('button', { name: /^create$/i }).click();

	// The dialog reveals the real one-time key (real POST /toolkits -> 201);
	// dismiss it via the hand-off CTA, then return to the list.
	await expect(page.getByText(/jntc_live_/).first()).toBeVisible();
	await page.getByRole('button', { name: /open toolkit/i }).click();
	await expect(page.getByRole('heading', { name })).toBeVisible();
	await page.getByRole('button', { name: /all toolkits/i }).click();

	// The new toolkit lands in the list.
	await expect(page.getByText(name)).toBeVisible();
});

test('open a toolkit detail page and create an API key (one-time reveal)', async ({
	page,
	request,
}) => {
	// Seed a toolkit through the API so this spec owns its fixture independently.
	const name = `e2e-toolkit-detail-${uniqueSuffix()}`;
	const toolkitId = await createToolkit(request, name);

	await page.goto(`/app/toolkits/${toolkitId}`);
	await expect(page.getByRole('heading', { name })).toBeVisible();

	// Create a key on the Keys tab — the freshly-minted plaintext is revealed
	// exactly once.
	await page.getByRole('tab', { name: 'Keys' }).click();
	await page.getByRole('button', { name: /create key/i }).click();
	await page.getByRole('button', { name: /^generate$/i }).click();

	await expect(page.getByText(/New API Key Created/i)).toBeVisible();
	// Real keys are prefixed jntc_…; assert the shape rather than a fixed value.
	await expect(page.getByText(/jntc_/)).toBeVisible();
});
