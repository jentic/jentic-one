import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	mockAuthenticatedUser,
	mockCredentials,
	mockCredentialForm,
	mockToolkits,
	navigateTo,
} from './fixtures';

test.describe('Credentials page', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthenticatedUser(page);
		await mockCredentials(page);
		await mockToolkits(page);
	});

	test('renders without errors', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.goto('/');
		await navigateTo(page, '/credentials');
		await expect(page.getByRole('heading', { name: /^credentials$/i })).toBeVisible();
		expect(errors).toHaveLength(0);
	});

	test('shows add credential button', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/credentials');
		await expect(page.getByText(/add credential/i).first()).toBeVisible();
	});
});

test.describe('Credential form page', () => {
	test('renders new credential form', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await mockAuthenticatedUser(page);
		await mockCredentialForm(page);
		await mockToolkits(page);

		await page.goto('/');
		await navigateTo(page, '/credentials/new');
		await expect(page.getByRole('heading', { name: /add credential/i })).toBeVisible();

		expect(errors).toHaveLength(0);
	});
});
