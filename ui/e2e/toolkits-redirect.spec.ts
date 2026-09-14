import { test, expect } from '@playwright/test';

/**
 * Deprecation-window alias for the retired Toolkits module (theme-5 5d) —
 * DELETE IN 6b together with `src/shared/app/toolkitsDeprecation.tsx`.
 *
 * Stale bookmarks / agent-emitted links to `/app/toolkits` and
 * `/app/toolkits/{tk_id}` must land on the Agents page (the binding
 * management home) with a one-time retirement notice — not on the not-found
 * catch-all, and without touching any toolkit endpoint (they were deleted).
 */
test('a stale /app/toolkits deep link redirects to Agents with the retirement notice', async ({
	page,
}) => {
	await page.goto('/app/');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();

	// A toolkit-era detail deep link (id + ?tab= variant) — the id is not
	// resolvable anymore and must not be looked up.
	await page.goto('/app/toolkits/tk_0123456789abcdef?tab=agents');

	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	await expect(page).toHaveURL(/\/app\/agents$/);

	// The one-time notice points at the replacement surface.
	const toast = page.getByTestId('toast');
	await expect(toast.getByText('Toolkits were retired')).toBeVisible();
	await expect(toast.getByText(/Access tab/)).toBeVisible();
});
