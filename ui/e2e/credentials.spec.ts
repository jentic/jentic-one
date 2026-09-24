import { test, expect, type Page } from '@playwright/test';

/**
 * Credentials primary flow (mocked): log in, open the Credentials module,
 * walk the add-credential wizard (guided picker → manual entry → form), create
 * a bearer-token credential, and confirm it lands in the list. Runs against the
 * in-browser MSW credentials handlers, so it exercises the real routing +
 * dialog wiring without a live backend.
 *
 * Note: the create flow doesn't surface the secret after saving (secrets are
 * redacted everywhere once stored), so this asserts the toast + the new row.
 */

function captureConsoleErrors(page: Page): string[] {
	const errors: string[] = [];
	page.on('console', (msg) => {
		if (msg.type() !== 'error') return;
		const text = msg.text();
		if (text.includes('Failed to load resource')) return;
		if (text.includes('net::ERR_')) return;
		errors.push(text);
	});
	return errors;
}

async function login(page: Page): Promise<void> {
	await page.goto('/app/');
	await expect(page.getByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
}

test('create a credential and see it in the list', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await login(page);

	await page.goto('/app/credentials');
	await expect(page.getByRole('heading', { name: 'Credentials' })).toBeVisible();

	await page.getByRole('button', { name: 'Add credential' }).click();

	// Step 1 of the wizard is the guided API picker ("Choose an API"); drop
	// into manual entry to reach the credential form (step 2).
	await expect(page.getByRole('heading', { name: 'Choose an API' })).toBeVisible();
	await page.getByRole('button', { name: /Enter manually/i }).click();

	await page.getByPlaceholder('Production API key').fill('CI bearer token');
	await page.getByPlaceholder('acme').fill('acme');
	await page.getByPlaceholder('sk_live_…').fill('super-secret-value');
	await page.getByRole('button', { name: 'Create credential' }).click();

	// Success surfaces as a toast (no secret reveal), then the dialog closes
	// and the new credential appears in the list. Assert on the list-row
	// heading specifically: the success toast also echoes the credential name
	// ("… is ready to use."), so a bare getByText is strict-mode ambiguous.
	await expect(page.getByTestId('toast').filter({ hasText: 'Credential created' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'CI bearer token' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});
