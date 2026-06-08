import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	mockAuthenticatedUser,
	mockJobs,
	mockJobDetail,
	mockTraces,
	mockToolkits,
	navigateTo,
} from './fixtures';

test.describe('Monitor page (Jobs)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthenticatedUser(page);
		await mockJobs(page);
		await mockTraces(page);
		await mockToolkits(page);
	});

	test('renders without errors', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.goto('/');
		// /jobs now redirects to the unified Monitor page.
		await navigateTo(page, '/monitor');
		await expect(page.getByRole('heading', { name: /^monitor$/i })).toBeVisible();
		expect(errors).toHaveLength(0);
	});

	test('shows monitor tabs', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/monitor');
		await expect(page.getByRole('button', { name: 'Overview' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Execution Log' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Jobs' })).toBeVisible();
	});

	test('shows empty state on the Jobs tab', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/monitor');
		await page.getByRole('button', { name: 'Jobs' }).click();
		await expect(page.getByText(/no jobs match your filters/i)).toBeVisible();
	});

	test('legacy /jobs route redirects to Monitor', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/jobs');
		await expect(page).toHaveURL(/\/monitor/);
		await expect(page.getByRole('heading', { name: /^monitor$/i })).toBeVisible();
	});
});

test.describe('Job detail page', () => {
	test('renders job detail', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await mockAuthenticatedUser(page);
		await mockJobDetail(page, 'job-1');
		await mockToolkits(page);

		await page.goto('/');
		await navigateTo(page, '/jobs/job-1');
		await expect(page.getByText('job-1')).toBeVisible();
		await expect(page.getByText(/back to jobs/i)).toBeVisible();
		await expect(page.getByText(/summary/i).first()).toBeVisible();

		expect(errors).toHaveLength(0);
	});
});
