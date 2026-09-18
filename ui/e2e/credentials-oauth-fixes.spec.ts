import { test, expect, type Page } from '@playwright/test';

/**
 * Real-browser verification for the OAuth2 credential-create hardening:
 *
 *   1. Grant-type picker orders flows canonically (authorization_code first),
 *      regardless of the order the spec declares them.
 *   2. Token/Authorize URL are validated client-side — a malformed URL (and a
 *      missing authorize URL for the authorization_code grant) blocks submit
 *      with an inline error and fires NO `POST /credentials`.
 *   3. When a freshly-created OAuth2 credential's connect flow is abandoned
 *      (the user closes the popup), the dangling credential is discarded
 *      (`DELETE /credentials/{id}`) instead of being left unconnected.
 *
 * Runs against the Vite dev server + MSW (Mode A). We seed the in-page mock
 * stores through the DEV-only `window.__mswTestHooks` hook so the guided
 * picker resolves a deterministic multi-flow OAuth2 spec.
 */

const OAUTH_SPEC = {
	openapi: '3.0.0',
	info: { title: 'Acme OAuth', version: '1.0.0' },
	servers: [{ url: 'https://api.acme.test' }],
	components: {
		securitySchemes: {
			acmeOauth: {
				type: 'oauth2',
				// Deliberately declared out of canonical order: implicit and
				// password come BEFORE authorization_code in the spec.
				flows: {
					implicit: {
						authorizationUrl: 'https://api.acme.test/authorize',
						scopes: { read: 'Read' },
					},
					password: { tokenUrl: 'https://api.acme.test/token', scopes: {} },
					clientCredentials: {
						tokenUrl: 'https://api.acme.test/token',
						scopes: {},
					},
					authorizationCode: {
						authorizationUrl: 'https://api.acme.test/authorize',
						tokenUrl: 'https://api.acme.test/token',
						scopes: { read: 'Read', write: 'Write' },
					},
				},
			},
		},
	},
};

async function login(page: Page): Promise<void> {
	await page.goto('/app/');
	await expect(page.getByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
}

/** Seed the guided-picker store with a single multi-flow OAuth2 workspace API. */
async function seedOAuthApi(page: Page): Promise<void> {
	await page.evaluate((spec) => {
		const w = window as unknown as {
			__mswTestHooks?: {
				resetApisStore: (apis: unknown[]) => void;
				resetCredentialsStore: (seed?: unknown[]) => void;
				makeMockApi: (o: Record<string, unknown>) => unknown;
			};
			__queryClient?: { clear: () => void };
		};
		const m = w.__mswTestHooks;
		if (!m) throw new Error('MSW test hook not present — is VITE_ENABLE_MSW=1?');
		m.resetCredentialsStore();
		m.resetApisStore([
			m.makeMockApi({
				vendor: 'acme',
				name: 'oauth',
				version: '1.0.0',
				displayName: 'Acme OAuth',
				securitySchemes: ['oauth2'],
				spec,
			}),
		]);
		// Drop any /apis (etc.) cached from the dashboard render so the picker
		// refetches against the seeded store.
		w.__queryClient?.clear();
	}, OAUTH_SPEC);
}

/** Open the create dialog and pick the seeded Acme OAuth API from the picker. */
async function openFormForAcmeOAuth(page: Page): Promise<void> {
	await page.goto('/app/credentials');
	await expect(page.getByRole('heading', { name: 'Credentials' })).toBeVisible();
	await seedOAuthApi(page);

	await page.getByRole('button', { name: 'Add credential' }).click();
	await expect(page.getByRole('heading', { name: 'Add credential' })).toBeVisible();

	// Pick the seeded workspace API (a picker-row button labelled by display name).
	await page.getByTestId('picker-row').filter({ hasText: 'Acme OAuth' }).first().click();

	// Step 2: the spec is OAuth2, so the OAuth fields render. Wait for the
	// form heading to confirm we advanced past the picker.
	await expect(page.getByRole('heading', { name: /Add credential/ })).toBeVisible();
}

test('Issue 1: grant-type picker lists flows in canonical order (auth code first)', async ({
	page,
}) => {
	await login(page);
	await openFormForAcmeOAuth(page);

	const grantSelect = page.getByLabel('Grant type');
	await expect(grantSelect).toBeVisible();

	// Option labels in DOM order must be canonical, not spec order.
	const optionLabels = await grantSelect.locator('option').allTextContents();
	expect(optionLabels).toEqual([
		'Authorization Code',
		'Client Credentials',
		'Resource Owner Password',
		'Implicit',
	]);

	// The default selected flow is authorization_code (first canonical entry).
	const selectedLabel = await grantSelect.locator('option:checked').first().textContent();
	expect(selectedLabel).toBe('Authorization Code');
});

test('Issue 3: malformed Token URL blocks submit with no POST /credentials', async ({ page }) => {
	await login(page);

	// Use manual entry so we control every field directly (no spec-seeded URLs).
	await page.goto('/app/credentials');
	await expect(page.getByRole('heading', { name: 'Credentials' })).toBeVisible();
	await page.evaluate(() => {
		const w = window as unknown as {
			__mswTestHooks?: { resetCredentialsStore: () => void; resetApisStore: () => void };
		};
		w.__mswTestHooks?.resetCredentialsStore();
		w.__mswTestHooks?.resetApisStore();
	});

	await page.getByRole('button', { name: 'Add credential' }).click();
	await page.getByRole('button', { name: /Enter manually/i }).click();

	// Fill the API ref + name, choose OAuth 2.0, then enter a bad Token URL.
	await page.getByPlaceholder('Production API key').fill('Bad URL cred');
	await page.getByPlaceholder('acme').fill('acme');
	await page.getByRole('radio', { name: /OAuth 2\.0/i }).click();

	await page.getByLabel('Client ID').fill('client-123');
	await page.getByLabel('Client secret').fill('shhh');

	const createdToast = page.getByTestId('toast').filter({ hasText: 'Credential created' });

	// Case A — syntactically-broken URL. The native `type="url"` constraint
	// blocks submit (defence in depth), so the credential is never created.
	await page.getByLabel('Token URL').fill('not-a-real-url');
	await page.getByRole('button', { name: 'Create credential' }).click();
	await page.waitForTimeout(300);
	await expect(createdToast).toHaveCount(0);

	// Case B — a URL the native `type="url"` check ACCEPTS (it allows any
	// scheme) but which our client-side guard must reject: a non-http(s)
	// scheme. This is the case the JS validation exists for — it surfaces our
	// own inline error and the credential is still not created.
	await page.getByLabel('Token URL').fill('ftp://provider.example.com/token');
	await page.getByRole('button', { name: 'Create credential' }).click();
	await expect(page.getByText(/Token URL must be a valid http/i)).toBeVisible();
	await expect(page.getByRole('button', { name: 'Create credential' })).toBeVisible();
	await expect(createdToast).toHaveCount(0);

	// Fixing it to a valid https URL clears the error and the create succeeds.
	await page.getByLabel('Token URL').fill('https://api.acme.test/token');
	await page.getByRole('button', { name: 'Create credential' }).click();
	await expect(createdToast).toBeVisible();
});

test('Issue 2: abandoning the post-create connect discards the dangling credential', async ({
	page,
}) => {
	await login(page);
	await openFormForAcmeOAuth(page);

	// Turn OFF the mock connect auto-complete so the connection never lands —
	// the exact "user opened sign-in and walked away" case. Install a
	// controllable fake popup that flips to `closed` immediately so the poll
	// loop resolves to `cancelled` rather than waiting out the full timeout.
	await page.evaluate(() => {
		const w = window as unknown as {
			__mswTestHooks?: { setConnectAutoCompletes: (v: boolean) => void };
		};
		w.__mswTestHooks?.setConnectAutoCompletes(false);
		const fake = { closed: true, close() {} };
		(window as unknown as { open: () => unknown }).open = () => fake;
	});

	await page.getByLabel('Client ID').fill('client-123');
	await page.getByLabel('Client secret').fill('shhh');

	await page.getByRole('button', { name: 'Create credential' }).click();

	// Created → toast, then the abandoned connect → "discarded" toast.
	await expect(page.getByTestId('toast').filter({ hasText: 'Credential created' })).toBeVisible();
	await expect(page.getByTestId('toast').filter({ hasText: /discarded/i })).toBeVisible({
		timeout: 15_000,
	});

	// The dangling credential must not linger in the list (it was DELETEd).
	await expect(page.getByRole('heading', { name: 'Acme OAuth' })).toHaveCount(0);
});
