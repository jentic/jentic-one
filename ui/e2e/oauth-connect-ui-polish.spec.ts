import { test, expect, type Page } from '@playwright/test';

/**
 * Real-browser verification for the grouped OAuth-connect / account UI polish
 * merged in #611:
 *
 *   #601 — OAuthPopupReturn surfaces a manual "Close window" affordance (and a
 *          "Return to credentials" link in the same-tab / no-opener case) when
 *          the browser refuses to auto-close, instead of a dead-end page.
 *   #598 — The /oauth/connected page posts an origin-restricted, detail-free
 *          advisory postMessage to its opener before closing.
 *   #594 — A voluntary "Change password" entry in the user menu lands on a
 *          ChangePasswordPage with neutral copy + a Cancel that returns to the
 *          app (vs. the forced "Set a new password" + Sign out gate).
 *
 * Runs against the Vite dev server + MSW (Mode A). Pure UI assertions; no
 * backend required.
 */

async function login(page: Page): Promise<void> {
	await page.goto('/app/');
	await expect(page.getByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
}

test.describe('#601 — OAuthPopupReturn close-blocked affordance', () => {
	test('same-tab landing (no opener) shows Close + Return to credentials', async ({ page }) => {
		// A direct navigation to the return route has window.opener == null, which
		// the page treats as the same-tab fallback: the affordance is shown
		// immediately (there is no popup to auto-close).
		await page.goto('/app/oauth/connected?status=ok');

		await expect(page.getByRole('heading', { name: 'Sign-in complete' })).toBeVisible();
		await expect(page.getByText('You can close this window.')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Close window' })).toBeVisible();
		await expect(page.getByRole('link', { name: 'Return to credentials' })).toBeVisible();
	});

	test('error landing shows the recoverable "you can close" copy', async ({ page }) => {
		await page.goto('/app/oauth/connected?status=error');

		await expect(page.getByRole('heading', { name: 'Sign-in failed' })).toBeVisible();
		await expect(
			page.getByText('Something went wrong. You can close this window and try again.'),
		).toBeVisible();
		// Same-tab → manual affordance is offered even on the error path.
		await expect(page.getByRole('button', { name: 'Close window' })).toBeVisible();
	});
});

test.describe('#598 — advisory postMessage to the opener', () => {
	test('posts an origin-restricted jentic:oauth-connect message on mount', async ({ page }) => {
		// Stand in for the opener: capture postMessage on the page that will open
		// the return route, then navigate it to the return route in the same
		// document so window.opener resolves to our capture target. We instead
		// install a fake opener and assert the page posts to it.
		await page.goto('/app/');

		// Capture messages the return page posts to its opener. We fake an opener
		// that records (message, targetOrigin) pairs.
		await page.addInitScript(() => {
			const received: Array<{ data: unknown; origin: string }> = [];
			(window as unknown as { __postedToOpener: typeof received }).__postedToOpener =
				received;
			Object.defineProperty(window, 'opener', {
				configurable: true,
				value: {
					postMessage(data: unknown, origin: string) {
						received.push({ data, origin });
					},
				},
			});
		});

		await page.goto('/app/oauth/connected?status=ok');
		await expect(page.getByRole('heading', { name: 'Sign-in complete' })).toBeVisible();

		const posted = await page.evaluate(
			() =>
				(
					window as unknown as {
						__postedToOpener?: Array<{ data: unknown; origin: string }>;
					}
				).__postedToOpener ?? [],
		);

		expect(posted.length).toBeGreaterThanOrEqual(1);
		const origin = await page.evaluate(() => window.location.origin);
		const msg = posted.find(
			(p) => (p.data as { type?: string })?.type === 'jentic:oauth-connect',
		);
		expect(msg).toBeTruthy();
		expect((msg!.data as { status?: string }).status).toBe('ok');
		// Strict target origin — never '*'.
		expect(msg!.origin).toBe(origin);
	});

	test('error status is propagated in the advisory message', async ({ page }) => {
		await page.goto('/app/');
		await page.addInitScript(() => {
			const received: Array<{ data: unknown; origin: string }> = [];
			(window as unknown as { __postedToOpener: typeof received }).__postedToOpener =
				received;
			Object.defineProperty(window, 'opener', {
				configurable: true,
				value: {
					postMessage(data: unknown, origin: string) {
						received.push({ data, origin });
					},
				},
			});
		});

		await page.goto('/app/oauth/connected?status=error');
		await expect(page.getByRole('heading', { name: 'Sign-in failed' })).toBeVisible();

		const posted = await page.evaluate(
			() =>
				(
					window as unknown as {
						__postedToOpener?: Array<{ data: unknown; origin: string }>;
					}
				).__postedToOpener ?? [],
		);
		const msg = posted.find(
			(p) => (p.data as { type?: string })?.type === 'jentic:oauth-connect',
		);
		expect(msg).toBeTruthy();
		expect((msg!.data as { status?: string }).status).toBe('error');
	});
});

test.describe('#594 — voluntary change password from the user menu', () => {
	test('opens neutral change-password and Cancel returns to the app', async ({ page }) => {
		await login(page);

		// Open the user menu and pick "Change password".
		await page.getByRole('button', { name: 'User menu' }).click();
		const changePassword = page.getByRole('menuitem', { name: 'Change password' });
		await expect(changePassword).toBeVisible();
		await changePassword.click();

		// Voluntary copy (not the forced "Set a new password" gate).
		await expect(page.getByRole('heading', { name: 'Change your password' })).toBeVisible();
		await expect(
			page.getByText('Enter your current password and choose a new one.'),
		).toBeVisible();
		await expect(page.getByRole('button', { name: 'Change password' })).toBeVisible();

		// Cancel returns to the app rather than signing out.
		const cancel = page.getByRole('button', { name: 'Cancel' });
		await expect(cancel).toBeVisible();
		await cancel.click();
		await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	});
});
