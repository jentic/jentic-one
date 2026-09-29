import { test, expect } from '@playwright/test';
import { captureConsoleErrors } from './helpers';

/**
 * First run (real backend). `/app` lands on Agents, the app's home; with no
 * agents yet it shows the setup checklist instead of the fleet.
 *
 * They live in their own Playwright project (`first-run`, see
 * playwright.docker.config.ts) that runs right after auth and BEFORE the main
 * `e2e` project: the suite shares one real DB, so the specs that register
 * agents (agents, broker-authz, …) would otherwise flip the workspace out of
 * first-run before these ran.
 */
test('Agents renders the first-run checklist against an empty backend, console clean', async ({
	page,
}) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');

	// The index redirects to Agents, inside the primary nav.
	await expect(page).toHaveURL(/\/app\/agents\b/);
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();

	// No agents → the setup checklist, then the self-registration route.
	await expect(page.getByRole('heading', { name: 'Set up your workspace' })).toBeVisible();
	await expect(page.getByRole('link', { name: /Discover an API/ })).toBeVisible();
	await expect(page.getByRole('link', { name: /Add a credential/ })).toBeVisible();
	await expect(page.getByRole('button', { name: /Create an agent/ })).toBeVisible();
	await expect(page.getByText('Register an agent from the command line')).toBeVisible();

	// One failing/empty source must not spam the console with app errors.
	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('first-run checklist links navigate to their surfaces', async ({ page }) => {
	await page.goto('/app');
	await expect(page.getByRole('heading', { name: 'Set up your workspace' })).toBeVisible();

	// Checklist links route into the module surfaces (real router, real guard).
	await page.getByRole('link', { name: /Discover an API/ }).click();
	await expect(page).toHaveURL(/\/app\/discover\b/);
	await expect(page.getByRole('heading', { name: 'Discover APIs', exact: true })).toBeVisible();
});

test('the tour overlay links into the real surfaces', async ({ page }) => {
	await page.goto('/app');
	await page.getByRole('button', { name: /^Show the tour/ }).click();
	const tour = page.getByRole('dialog', { name: 'The Jentic One tour' });
	await tour.getByRole('tab', { name: 'Set it up' }).click();
	await tour
		.getByRole('list', { name: 'Setup steps' })
		.getByRole('button', { name: /Choose its APIs/ })
		.click();

	// The step's CTA routes into the module surface (real router, real guard).
	await tour.getByTestId('setup-cta').getByRole('link', { name: 'Browse the catalog' }).click();
	await expect(page).toHaveURL(/\/app\/discover\b/);
	await expect(page.getByRole('heading', { name: 'Discover APIs', exact: true })).toBeVisible();
});
