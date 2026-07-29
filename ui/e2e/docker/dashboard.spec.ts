import { test, expect } from '@playwright/test';
import { captureConsoleErrors } from './helpers';

/**
 * Dashboard (real backend). By the time the alphabetical `e2e` project reaches
 * this file, earlier specs (access-requests, agents, broker-authz, …) have
 * already registered agents against the shared DB — so the workspace is NOT
 * first-run here and the page renders its working layout. The first-run swap
 * itself is asserted in first-run.spec.ts, which runs in its own project
 * BEFORE anything pollutes the DB (see playwright.docker.config.ts).
 *
 * Reuses the authenticated storageState from auth.setup.ts (see the `e2e`
 * project in playwright.docker.config.ts) — no per-spec login.
 */
test('dashboard renders its working layout against the shared backend, console clean', async ({
	page,
}) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');

	// Landing header + primary nav are present.
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();

	// Agents exist by now, so the working layout mounts: the action bell (badge
	// state depends on what earlier specs left pending — accept either) and the
	// admin-only Gateway health section.
	await expect(page.getByRole('button', { name: /Needs your action/ })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Gateway health' })).toBeVisible();

	// The detail layer mounts (empty or not — earlier specs may have executed).
	await expect(page.getByRole('heading', { name: 'Recent activity' })).toBeVisible();

	// One failing/empty source must not spam the console with app errors.
	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('header quick-actions menu navigates to its surfaces', async ({ page }) => {
	await page.goto('/app');
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();

	// Quick actions live behind a header dropdown now (the bottom band is gone).
	await page.getByRole('button', { name: 'Quick actions' }).click();
	await page.getByRole('menuitem', { name: 'Add credential' }).click();
	await expect(page).toHaveURL(/\/app\/credentials\b/);
});
