import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	mockAuthenticatedUser,
	mockToolkits,
	mockToolkitDetail,
	navigateTo,
} from './fixtures';

test.describe('Toolkits page', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthenticatedUser(page);
		await mockToolkits(page);
	});

	test('renders without errors', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.goto('/');
		await navigateTo(page, '/toolkits');
		// Use the exact page title — the empty-state renders a second heading
		// ("No toolkits yet") that the loose /toolkits/i regex also matched,
		// tripping Playwright strict mode.
		await expect(page.getByRole('heading', { name: 'Toolkits', exact: true })).toBeVisible();
		expect(errors).toHaveLength(0);
	});

	test('shows create toolkit button', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/toolkits');
		await expect(page.getByRole('button', { name: /create toolkit/i }).first()).toBeVisible();
	});
});

test.describe('Toolkit detail page', () => {
	test('renders toolkit detail', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await mockAuthenticatedUser(page);
		await mockToolkitDetail(page, 'test-tk');
		await mockToolkits(page);

		await page.goto('/');
		await navigateTo(page, '/toolkits/test-tk');
		await expect(page.getByRole('heading', { name: 'Test Toolkit' })).toBeVisible();
		await expect(page.getByText(/back to toolkits/i)).toBeVisible();

		expect(errors).toHaveLength(0);
	});
});
