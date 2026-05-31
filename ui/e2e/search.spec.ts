import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	mockAuthenticatedUser,
	mockCatalog,
	mockSearch,
	mockToolkits,
	navigateTo,
} from './fixtures';

// `/search` was retired alongside `/catalog` and now redirects into
// `/discover` (with the query string preserved). These specs guard
// the redirect contract so old bookmarks like /search?q=stripe keep
// landing on a usable surface.
test.describe('Legacy /search redirect', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthenticatedUser(page);
		await mockSearch(page);
		await mockCatalog(page);
		await mockToolkits(page);
	});

	test('redirects /search to /discover and renders the Discover page', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.goto('/');
		await navigateTo(page, '/search');
		await expect(page).toHaveURL(/\/discover(\?|$)/);
		await expect(page.getByRole('heading', { name: 'Discover', exact: true })).toBeVisible();
		expect(errors).toHaveLength(0);
	});

	test('preserves the query string when redirecting from /search', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/search?q=stripe');
		await expect(page).toHaveURL(/\/discover\?[^#]*q=stripe/);
	});
});
