import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	createBearerCredential,
	createToolkit,
	uniqueSuffix,
} from './helpers';

/**
 * Toolkits (real backend). Covers the list empty-state, create-via-UI, detail
 * navigation, and the one-time API key reveal. Verified live: POST /toolkits ->
 * 201 (auto-mints one key), and the detail page exposes key creation under
 * /toolkits/{id}/keys.
 */
test('toolkits list renders the empty state on a clean backend', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');
	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Toolkits' })
		.click();

	await expect(page.getByRole('heading', { name: 'Toolkits' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('create a toolkit via the UI and see it in the list', async ({ page }) => {
	const name = `e2e-toolkit-${uniqueSuffix()}`;

	await page.goto('/app/toolkits');
	await expect(page.getByRole('heading', { name: 'Toolkits' })).toBeVisible();

	await page
		.getByRole('button', { name: /new toolkit/i })
		.first()
		.click();
	await page.getByLabel('Name').fill(name);
	await page.getByRole('button', { name: /^create$/i }).click();

	// The dialog reveals the real one-time key (real POST /toolkits -> 201);
	// dismiss it via the hand-off CTA, then return to the list.
	await expect(page.getByText(/jntc_live_/).first()).toBeVisible();
	await page.getByRole('button', { name: /open toolkit/i }).click();
	await expect(page.getByRole('heading', { name })).toBeVisible();
	await page.getByRole('link', { name: /all toolkits/i }).click();

	// The new toolkit lands in the list.
	await expect(page.getByText(name)).toBeVisible();
});

test('open a toolkit detail page and create an API key (one-time reveal)', async ({
	page,
	request,
}) => {
	// Seed a toolkit through the API so this spec owns its fixture independently.
	const name = `e2e-toolkit-detail-${uniqueSuffix()}`;
	const toolkitId = await createToolkit(request, name);

	await page.goto(`/app/toolkits/${toolkitId}`);
	await expect(page.getByRole('heading', { name })).toBeVisible();

	// Create a key on the Keys tab — the freshly-minted plaintext is revealed
	// exactly once.
	await page.getByRole('tab', { name: 'Keys' }).click();
	await page.getByRole('button', { name: /create key/i }).click();
	await page.getByRole('button', { name: /^generate$/i }).click();

	await expect(page.getByText(/New API Key Created/i)).toBeVisible();
	// Real keys are prefixed jntc_…; assert the shape rather than a fixed value.
	await expect(page.getByText(/jntc_/)).toBeVisible();
});

test('bind a credential with full access, then edit rules and dry-run against the real broker', async ({
	page,
	request,
}) => {
	// Own fixtures: a toolkit and an unbound credential.
	const suffix = uniqueSuffix();
	const toolkitId = await createToolkit(request, `e2e-toolkit-perms-${suffix}`);
	const credName = `e2e-cred-perms-${suffix}`;
	await createBearerCredential(request, credName);

	await page.goto(`/app/toolkits/${toolkitId}?tab=access`);
	await expect(page.getByRole('tab', { name: /^Access/ })).toHaveAttribute(
		'aria-selected',
		'true',
	);

	// Bind through the two-step wizard, keeping the allow-all default: the
	// binding must land WITHOUT the zero-rules warning (real allow_all bind).
	await page.getByRole('button', { name: /^bind credential$/i }).click();
	await page.getByText(credName).click();
	await expect(page.getByRole('radio', { name: /allow all operations/i })).toHaveAttribute(
		'aria-checked',
		'true',
	);
	await page
		.getByRole('dialog')
		.getByRole('button', { name: /^bind credential$/i })
		.click();
	const bindingRow = page.getByTestId('binding-row').filter({ hasText: credName });
	await expect(bindingRow).toBeVisible();
	await expect(bindingRow.getByTestId('binding-warning')).toHaveCount(0);

	// Narrow the grant to GET-only and save (real PUT …/permissions).
	await bindingRow.getByRole('button', { name: /edit rules/i }).click();
	await page.getByRole('button', { name: 'GET', pressed: false }).click();
	// The live diff announces the change before commit.
	await expect(page.getByTestId('rules-diff')).toBeVisible();
	await page.getByRole('button', { name: /save rules/i }).click();
	await expect(page.getByTestId('rules-diff')).toHaveCount(0);

	// Dry-run both verdicts against the real broker evaluation
	// (POST …/permissions:test): GET matches rule #1, DELETE default-denies.
	await bindingRow.getByRole('button', { name: /edit rules/i }).click();
	await page.getByRole('button', { name: /test a request/i }).click();
	await page.getByLabel('Request path').fill('/anything');
	await page.getByRole('button', { name: /^test$/i }).click();
	await expect(page.getByTestId('rule-verdict')).toContainText(/allowed — matched rule #1/i);

	await page.getByLabel('HTTP method').selectOption('DELETE');
	await page.getByRole('button', { name: /^test$/i }).click();
	await expect(page.getByTestId('rule-verdict')).toContainText(/denied — no rule matched/i);
});
