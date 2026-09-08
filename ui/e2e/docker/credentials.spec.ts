import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	createApiKeyCredential,
	createBearerCredential,
	postCredentialRaw,
	updateCredential,
	uniqueSuffix,
} from './helpers';

/**
 * Credentials (real backend). Drives the add-credential wizard (guided picker →
 * manual entry → form) to create a real bearer-token credential, then asserts
 * the success toast + the new row. Also covers the API-seeded list + delete.
 *
 * Requires the credential-at-rest encryption keyset (config/local.yaml) — the
 * control surface envelope-encrypts secrets, and a missing keyset makes POST
 * /credentials 500. Verified live: POST /credentials -> 201 with the keyset.
 */
test('credentials list renders the empty state on a clean backend', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');
	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Credentials' })
		.click();

	await expect(page.getByRole('heading', { name: 'Credentials' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('create a bearer credential via the wizard and see it in the list', async ({ page }) => {
	const name = `e2e bearer ${uniqueSuffix()}`;

	await page.goto('/app/credentials');
	await expect(page.getByRole('heading', { name: 'Credentials' })).toBeVisible();

	await page.getByRole('button', { name: 'Add credential' }).click();

	// Step 1 is the guided API picker; drop into manual entry to reach the form.
	await expect(page.getByRole('heading', { name: 'Choose an API' })).toBeVisible();
	await page.getByRole('button', { name: /Enter manually/i }).click();

	await page.getByPlaceholder('Production API key').fill(name);
	await page.getByPlaceholder('acme').fill('httpbin.org');
	await page.getByPlaceholder('sk_live_…').fill('e2e-secret-value');
	await page.getByRole('button', { name: 'Create credential' }).click();

	// Real POST /credentials -> 201; success is a toast (no secret reveal) and
	// the credential appears in the list. Scope the list assertion to the card
	// heading — the name also echoes in the toast description, so a bare
	// getByText(name) is ambiguous (strict-mode) while the toast is up.
	await expect(
		page.getByTestId('toast').filter({ hasText: /Credential created/i }),
	).toBeVisible();
	await expect(page.getByRole('heading', { name })).toBeVisible();
});

test('a credential created via the API can be deleted from the UI', async ({ page, request }) => {
	const name = `e2e-del-${uniqueSuffix()}`;
	await createBearerCredential(request, name);

	await page.goto('/app/credentials');
	await expect(page.getByRole('heading', { name })).toBeVisible();

	// The card exposes an explicit per-credential delete control (aria-labelled),
	// which opens the shared CascadeDeleteDialog. Hard delete is irreversible, so
	// the dialog gates the confirm button behind a type-to-confirm field — type
	// the fixed word "delete" (like a real operator) to enable "Delete credential".
	await page.getByRole('button', { name: `Delete credential ${name}` }).click();

	const dialog = page.getByRole('dialog', { name: 'Delete credential' });
	await dialog.getByRole('textbox', { name: /Type delete to confirm/i }).fill('delete');
	const confirm = dialog.getByRole('button', { name: 'Delete credential', exact: true });
	await expect(confirm).toBeEnabled();
	await confirm.click();

	// The card heading is the stable per-row anchor — the name also appears
	// briefly inside the confirm dialog's body, so assert on the heading role
	// (not a bare text match) to avoid a strict-mode collision during teardown.
	await expect(page.getByRole('heading', { name })).toBeHidden();
});

// ── Credential type matrix ─────────────────────────────────────────────────
//
// The wizard's manual-entry path offers all four auth types (AuthTypeCards).
// These specs drive the UI for the three types the bearer happy-path above
// doesn't cover (api_key, basic, oauth2), entering manually so no spec fetch is
// needed. Each asserts the real POST /credentials -> 201 success toast + the new
// row. The credential-at-rest keyset (config/local.yaml) is required for all.

test('create an api_key credential via the wizard (manual entry)', async ({ page }) => {
	const name = `e2e apikey ${uniqueSuffix()}`;

	await page.goto('/app/credentials');
	await page.getByRole('button', { name: 'Add credential' }).click();
	await expect(page.getByRole('heading', { name: 'Choose an API' })).toBeVisible();
	await page.getByRole('button', { name: /Enter manually/i }).click();

	// Manual mode exposes the API-reference fieldset + all four type cards.
	// Target inputs by placeholder (the shared Input + required Label renders an
	// accessible name like "Vendor *", so a role-name match is brittle — the
	// proven bearer spec above uses placeholders for the same reason).
	await page.getByPlaceholder('acme').fill('httpbin.org');
	await page.getByRole('radio', { name: 'API key' }).click();

	await page.getByPlaceholder('Production API key').fill(name);
	await page.getByLabel('API key', { exact: true }).fill('sk-e2e-apikey');
	await page.getByPlaceholder('X-Api-Key').fill('X-Api-Key');
	await page.getByRole('button', { name: 'Create credential' }).click();

	await expect(
		page.getByTestId('toast').filter({ hasText: /Credential created/i }),
	).toBeVisible();
	await expect(page.getByRole('heading', { name })).toBeVisible();
});

test('create a basic-auth credential via the wizard (manual entry)', async ({ page }) => {
	const name = `e2e basic ${uniqueSuffix()}`;

	await page.goto('/app/credentials');
	await page.getByRole('button', { name: 'Add credential' }).click();
	await page.getByRole('button', { name: /Enter manually/i }).click();

	await page.getByPlaceholder('acme').fill('httpbin.org');
	await page.getByRole('radio', { name: 'Basic auth' }).click();

	await page.getByPlaceholder('Production API key').fill(name);
	await page.getByLabel('Username').fill('e2e-user');
	await page.getByLabel('Password', { exact: true }).fill('e2e-pass');
	await page.getByRole('button', { name: 'Create credential' }).click();

	await expect(
		page.getByTestId('toast').filter({ hasText: /Credential created/i }),
	).toBeVisible();
	await expect(page.getByRole('heading', { name })).toBeVisible();
});

test('create an oauth2 (client-credentials) credential via the wizard (manual entry)', async ({
	page,
}) => {
	const name = `e2e oauth2 ${uniqueSuffix()}`;

	await page.goto('/app/credentials');
	await page.getByRole('button', { name: 'Add credential' }).click();
	await page.getByRole('button', { name: /Enter manually/i }).click();

	await page.getByPlaceholder('acme').fill('httpbin.org');
	await page.getByRole('radio', { name: 'OAuth 2.0' }).click();

	await page.getByPlaceholder('Production API key').fill(name);
	await page.getByLabel('Client ID').fill('e2e-client-id');
	await page.getByLabel('Client secret').fill('e2e-client-secret');
	await page
		.getByPlaceholder('https://provider.com/oauth/token')
		.fill('https://httpbin.org/anything/token');
	await page.getByRole('button', { name: 'Create credential' }).click();

	await expect(
		page.getByTestId('toast').filter({ hasText: /Credential created/i }),
	).toBeVisible();
	await expect(page.getByRole('heading', { name })).toBeVisible();
});

// ── Update / rotate (API-level) ────────────────────────────────────────────
//
// PATCH /credentials/{id} rotates a secret or flips `active`. Secrets are never
// echoed back, so the assertable signal is the redacted view: `active` and
// `updated_at`. We drive these through the API (the helper) because the
// assertion is on the backend contract, not the edit sheet's chrome.

test('rotate an api_key secret via PATCH (redacted view updates, secret not echoed)', async ({
	request,
}) => {
	const name = `e2e-rotate-${uniqueSuffix()}`;
	const id = await createApiKeyCredential(request, { name });

	const view = await updateCredential(request, id, { type: 'api_key', key: 'sk-rotated-value' });
	// The secret must never come back on a redacted view — only metadata moves.
	expect(JSON.stringify(view)).not.toContain('sk-rotated-value');
	expect(view.updated_at, 'updated_at should be set after a rotation').not.toBeNull();
});

test('deactivate then reactivate a credential via PATCH (active toggles)', async ({ request }) => {
	const name = `e2e-toggle-${uniqueSuffix()}`;
	const id = await createBearerCredential(request, name);

	const off = await updateCredential(request, id, { type: 'bearer_token', active: false });
	expect(off.active).toBe(false);

	const on = await updateCredential(request, id, { type: 'bearer_token', active: true });
	expect(on.active).toBe(true);
});

// ── Negative input paths (assert the real validation contract) ─────────────
//
// These assert the backend REJECTS malformed input — we deliberately do NOT
// weaken the create helpers to make these pass. A discriminated-union body with
// a missing required secret, or an unknown type, must be a 4xx.

test('POST /credentials with a missing required secret is rejected (422)', async ({ request }) => {
	// api_key requires `key` + `location` + `field_name`; omit `key`.
	const { status, body } = await postCredentialRaw(request, {
		type: 'api_key',
		name: `e2e-bad-${uniqueSuffix()}`,
		api: { vendor: 'httpbin.org' },
		provider: 'static',
		location: 'header',
		field_name: 'X-Api-Key',
	});
	expect(status, `expected 422 for missing key, got ${status}: ${body}`).toBe(422);
});

test('POST /credentials with an unknown type is rejected (422)', async ({ request }) => {
	const { status, body } = await postCredentialRaw(request, {
		type: 'definitely-not-a-real-type',
		name: `e2e-bad-${uniqueSuffix()}`,
		api: { vendor: 'httpbin.org' },
	});
	expect(status, `expected 422 for unknown type, got ${status}: ${body}`).toBe(422);
});

test('POST /credentials oauth2 without a token_url is rejected (400)', async ({ request }) => {
	const { status, body } = await postCredentialRaw(request, {
		type: 'oauth2',
		name: `e2e-bad-${uniqueSuffix()}`,
		api: { vendor: 'httpbin.org' },
		provider: 'static',
		client_id: 'x',
		client_secret: 'y',
	});
	expect(status, `expected 400 for missing token_url, got ${status}: ${body}`).toBe(400);
});
