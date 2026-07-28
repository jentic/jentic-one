import { test, expect } from '@playwright/test';
import { captureConsoleErrors } from './helpers';

/**
 * Dashboard (real backend). On a clean fixtures DB the workspace has no agents
 * and no executions, so the landing page renders the FIRST-RUN setup checklist
 * (not the working overview) plus the recent-activity teaser — this asserts
 * that swap happens against a real empty backend without console errors.
 *
 * Reuses the authenticated storageState from auth.setup.ts (see the `e2e`
 * project in playwright.docker.config.ts) — no per-spec login.
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

test('header quick-actions menu navigates to its surfaces', async ({ page }) => {
	await page.goto('/app');
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();

	// Quick actions live behind a header dropdown now (the bottom band is gone).
	await page.getByRole('button', { name: 'Quick actions' }).click();
	await page.getByRole('menuitem', { name: 'Add credential' }).click();
	await expect(page).toHaveURL(/\/app\/credentials\b/);
});
