import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	mockAuthenticatedUser,
	mockTraces,
	mockTraceDetail,
	mockToolkits,
	mockJobs,
	navigateTo,
} from './fixtures';

test.describe('Monitor page (Execution Log)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthenticatedUser(page);
		await mockTraces(page);
		await mockJobs(page);
		await mockToolkits(page);
	});

	test('renders without errors', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.goto('/');
		// /traces now redirects to the unified Monitor page.
		await navigateTo(page, '/monitor');
		await expect(page.getByRole('heading', { name: /^monitor$/i })).toBeVisible();
		expect(errors).toHaveLength(0);
	});

	test('shows the Execution Log tab', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/monitor');
		await page.getByRole('button', { name: 'Execution Log' }).click();
		await expect(page.getByText(/no executions match your filters/i)).toBeVisible();
	});

	test('legacy /traces route redirects to Monitor', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/traces');
		await expect(page).toHaveURL(/\/monitor/);
		await expect(page.getByRole('heading', { name: /^monitor$/i })).toBeVisible();
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
