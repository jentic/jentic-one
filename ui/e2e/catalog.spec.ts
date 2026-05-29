import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	mockAuthenticatedUser,
	mockCatalog,
	mockToolkits,
	navigateTo,
} from './fixtures';

// `/catalog` is a legacy URL kept alive only as a redirect to `/discover`.
// The IA simplification collapsed the old "Your APIs / Public Catalog"
// tabbed page into a single Discover surface. These specs guard the
// redirect contract — bookmarks like /catalog?q=stripe must still land
// on the Discover page with state intact — without re-asserting the
// retired tabbed UI.
test.describe('Legacy /catalog redirect', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthenticatedUser(page);
		await mockCatalog(page);
		await mockToolkits(page);
	});

	test('redirects /catalog to /discover and renders the Discover page', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.goto('/');
		await navigateTo(page, '/catalog');
		await expect(page).toHaveURL(/\/discover(\?|$)/);
		await expect(page.getByRole('heading', { name: 'Discover', exact: true })).toBeVisible();
		expect(errors).toHaveLength(0);
	});

	test('preserves the query string when redirecting from /catalog', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/catalog?q=stripe');
		await expect(page).toHaveURL(/\/discover\?[^#]*q=stripe/);
	});
});
