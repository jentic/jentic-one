import { test, expect } from '@playwright/test';
import { captureConsoleErrors } from './helpers';

/**
 * Discover (real backend). The Discover surface browses the PUBLIC Jentic
 * catalog (`GET /catalog`) — an external, network-dependent source whose
 * contents we don't control and which may be empty or unreachable in CI. So
 * this spec asserts the page's own shell renders and its controls wire up
 * (search, filter, refresh) WITHOUT asserting on specific catalog entries.
 * The catalog *content* is exercised indirectly via Workspace import.
 */
test('discover renders its catalog shell and toolbar, console clean', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');
	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Discover APIs' })
		.click();

	await expect(page.getByRole('heading', { name: 'Discover APIs' })).toBeVisible();

	// The toolbar (search + Imported/Available filter + refresh) is the surface's
	// own chrome and renders regardless of catalog availability.
	await expect(page.getByTestId('discover-toolbar')).toBeVisible();
	await expect(page.getByRole('searchbox', { name: 'Search APIs' })).toBeVisible();
	await expect(page.getByTestId('discover-refresh')).toBeVisible();

	// Catalog reachability is environmental; failing to fetch it is surfaced in
	// the UI, not as an uncaught console error.
	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('discover filter and search controls are interactive', async ({ page }) => {
	await page.goto('/app/discover');
	await expect(page.getByRole('heading', { name: 'Discover APIs' })).toBeVisible();

	// The registration filter is a segmented control of buttons (All / Imported
	// / Available). Clicking one drives the catalog query param. There's no
	// aria-checked on the segments, so we assert the click is accepted and the
	// surface stays healthy (no results assertion — the catalog is external).
	const toolbar = page.getByTestId('discover-toolbar');
	await toolbar.getByRole('button', { name: 'Available' }).click();
	await expect(page.getByRole('heading', { name: 'Discover APIs' })).toBeVisible();

	// Type into the search box; the field owns its own value.
	const search = page.getByRole('searchbox', { name: 'Search APIs' });
	await search.fill('stripe');
	await expect(search).toHaveValue('stripe');
});
