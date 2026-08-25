import { test, expect } from '@playwright/test';
import { captureConsoleErrors } from './helpers';

/**
 * First-run dashboard (real backend). These specs assert the landing page's
 * FIRST-RUN swap — no agents and no executions yet → the setup checklist
 * replaces the health/context layers.
 *
 * They live in their own Playwright project (`first-run`, see
 * playwright.docker.config.ts) that runs right after auth and BEFORE the main
 * `e2e` project: the suite shares one real DB, so the specs that register
 * agents (access-requests, agents, broker-authz, …) would otherwise flip the
 * workspace out of first-run before alphabetical file order ever reached
 * dashboard.spec.ts.
 */
test('dashboard renders the first-run checklist against an empty backend, console clean', async ({
	page,
}) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');

	// Landing header + primary nav are present.
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();

	// No agents + no executions → the setup checklist replaces the queues and
	// the Gateway-health section.
	await expect(page.getByRole('heading', { name: 'Set up your workspace' })).toBeVisible();
	await expect(page.getByRole('link', { name: /Discover an API/ })).toBeVisible();
	await expect(page.getByRole('link', { name: /Register an agent/ })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Needs your action (all clear)' })).toBeVisible();

	// The detail layer still mounts (its own empty state).
	await expect(page.getByRole('heading', { name: 'Recent activity' })).toBeVisible();

	// One failing/empty source must not spam the console with app errors.
	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('first-run checklist steps navigate to their surfaces', async ({ page }) => {
	await page.goto('/app');
	await expect(page.getByRole('heading', { name: 'Set up your workspace' })).toBeVisible();

	// Checklist links route into the module surfaces (real router, real guard).
	await page.getByRole('link', { name: /Create a toolkit/ }).click();
	await expect(page).toHaveURL(/\/app\/toolkits\b/);
	await expect(page.getByRole('heading', { name: 'Toolkits' })).toBeVisible();
});
