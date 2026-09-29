import { test, expect } from '@playwright/test';
import { captureConsoleErrors } from './helpers';

/**
 * First run (real backend). `/app` lands on Agents, the app's home; with no
 * agents yet it shows the zero-agents landing instead of the fleet, and the page
 * header's create button reads "Create your first agent".
 *
 * They live in their own Playwright project (`first-run`, see
 * playwright.docker.config.ts) that runs right after auth and BEFORE the main
 * `e2e` project: the suite shares one real DB, so the specs that register
 * agents (agents, broker-authz, …) would otherwise flip the workspace out of
 * first-run before these ran.
 */
test('Agents renders the first-run landing against an empty backend, console clean', async ({
	page,
}) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');

	// The index redirects to Agents, inside the primary nav.
	await expect(page).toHaveURL(/\/app\/agents\b/);
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();

	// No agents → the zero-agents landing, and the header's one create button says so.
	await expect(
		page.getByRole('heading', { name: 'Let your agent register itself' }),
	).toBeVisible();
	await expect(
		page.getByRole('heading', { name: 'Prefer to set it up yourself?' }),
	).toBeVisible();
	await expect(page.getByRole('button', { name: 'Create your first agent' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'New agent' })).toHaveCount(0);
	await expect(page.getByTestId('register-command')).toContainText('jentic register --url ');

	// One failing/empty source must not spam the console with app errors.
	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('the first-run create button opens the real create sheet', async ({ page }) => {
	await page.goto('/app');
	await expect(
		page.getByRole('heading', { name: 'Let your agent register itself' }),
	).toBeVisible();

	// The manual route opens the real sheet (real router, real guard); closing it
	// creates nothing, so the workspace stays in first run for the specs after.
	await page.getByRole('button', { name: 'Create your first agent' }).click();
	const sheet = page.getByRole('dialog', { name: 'Create agent' });
	await expect(sheet).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(sheet).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Create your first agent' })).toBeVisible();
});
