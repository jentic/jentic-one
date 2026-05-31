import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	mockAuthenticatedUser,
	mockTraces,
	mockTraceDetail,
	mockToolkits,
	navigateTo,
} from './fixtures';

test.describe('Traces page', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthenticatedUser(page);
		await mockTraces(page);
		await mockToolkits(page);
	});

	test('renders without errors', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.goto('/');
		await navigateTo(page, '/traces');
		await expect(page.getByRole('heading', { name: /^traces$/i })).toBeVisible();
		expect(errors).toHaveLength(0);
	});

	test('shows empty state when no traces', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/traces');
		await expect(page.getByText(/no traces found/i)).toBeVisible();
	});
});

test.describe('Trace detail page', () => {
	test('renders trace detail', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await mockAuthenticatedUser(page);
		await mockTraceDetail(page, 'trace-1');
		await mockToolkits(page);

		await page.goto('/');
		await navigateTo(page, '/traces/trace-1');
		await expect(page.getByText('trace-1')).toBeVisible();
		await expect(page.getByText(/back to traces/i)).toBeVisible();

		expect(errors).toHaveLength(0);
	});
});
