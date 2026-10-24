import { test, expect } from '@playwright/test';
import { captureConsoleErrors } from './helpers';

/**
 * First run (real backend). `/app` lands on Agents, the app's home; with no
 * agents yet it shows the zero-agents landing instead of the fleet, and the page
 * header's "New agent" button steps back to a secondary style.
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

	// No agents → the zero-agents landing; the header's create button keeps its label.
	await expect(page.getByRole('heading', { name: 'Connect your first agent' })).toBeVisible();
	await expect(
		page.getByRole('radiogroup', { name: 'How to connect your first agent' }),
	).toBeVisible();
	await expect(page.getByRole('button', { name: 'New agent' })).toHaveAttribute(
		'data-emphasis',
		'secondary',
	);
	await expect(page.getByTestId('register-command')).toContainText('jentic register --url ');

	// One failing/empty source must not spam the console with app errors.
	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('the first-run landing opens the New agent panel on the chosen way in', async ({ page }) => {
	await page.goto('/app');
	await expect(page.getByRole('heading', { name: 'Connect your first agent' })).toBeVisible();

	// The real panel (real router, real guard); closing it creates nothing, so the
	// workspace stays in first run for the specs after.
	const sheet = page.getByRole('dialog', { name: 'New agent' });
	await page.getByRole('button', { name: 'New agent' }).click();
	await expect(sheet).toBeVisible();
	await expect(sheet.getByRole('tab', { name: 'Register from the CLI' })).toHaveAttribute(
		'aria-selected',
		'true',
	);
	await page.keyboard.press('Escape');
	await expect(sheet).toHaveCount(0);

	// The manual route: "Create it here", then Continue.
	await page.getByRole('radio', { name: /Create it here/ }).click();
	await page.getByRole('button', { name: /^Continue/ }).click();
	await expect(sheet).toBeVisible();
	await expect(sheet.getByRole('tab', { name: 'Create it here' })).toHaveAttribute(
		'aria-selected',
		'true',
	);
	await page.keyboard.press('Escape');
	await expect(sheet).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'New agent' })).toBeVisible();
});
