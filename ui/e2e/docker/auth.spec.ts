import { test, expect } from '@playwright/test';
import { SETUP_ADMIN, captureConsoleErrors, getHealth, submitLogin } from './helpers';

/**
 * Real-backend auth specs.
 *
 * The `e2e` project supplies an authenticated `storageState` (see auth.setup.ts),
 * so the authenticated test below starts already-logged-in. Negative-path tests
 * opt OUT of that state per-test with an empty storageState, so they see the
 * unauthenticated app.
 */

test.describe('authenticated (reuses storageState)', () => {
	test('starts authenticated and renders the shell, console clean', async ({ page }) => {
		const errors = captureConsoleErrors(page);

		await page.goto('/app');

		await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
		await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();

		expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
	});

	test('health reports the first-run gate cleared after setup', async ({ request }) => {
		const health = await getHealth(request);
		expect(health.setup_required).toBe(false);
		expect(health.next_step).toBeNull();
	});
});

test.describe('unauthenticated (no storageState)', () => {
	// Drop the shared auth state so these tests see the signed-out app.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('rejects wrong credentials', async ({ page }) => {
		await page.goto('/app/login');
		await submitLogin(page, SETUP_ADMIN.email, 'definitely-wrong-password');
		await expect(page.getByText('Incorrect email or password.')).toBeVisible();
		await expect(page).toHaveURL(/\/app\/login/);
	});

	test('unauthenticated deep link redirects to login', async ({ page }) => {
		await page.goto('/app');
		await expect(page.getByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
		await expect(page).toHaveURL(/\/app\/login/);
	});
});
