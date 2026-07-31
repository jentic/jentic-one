import { test, expect } from '@playwright/test';
import { captureConsoleErrors, importInlineApi, sampleOpenApiSpec, uniqueSuffix } from './helpers';

/**
 * Workspace (real backend). The Workspace surface lists the APIs registered in
 * this instance and owns the import dialog. Import is ASYNC on the real backend
 * (POST /apis -> 202 + job id, the UI polls /jobs/{id}), unlike the synchronous
 * MSW mock — so the paste-import spec asserts the dialog INITIATED the import
 * rather than racing its (timing-sensitive) completion, while a helper-seeded
 * spec covers the card landing in the grid deterministically.
 *
 * On a clean DB the grid is empty; each spec seeds its own API so they stay
 * hermetic.
 */
test('workspace renders its empty state on a clean backend', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');
	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Workspace' })
		.click();

	await expect(page.getByRole('heading', { name: 'Workspace' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('import an API by pasting a spec drives the async import', async ({ page }) => {
	test.setTimeout(60_000);

	const title = `E2E Paste ${uniqueSuffix()}`;

	await page.goto('/app/workspace');
	await expect(page.getByRole('heading', { name: 'Workspace' })).toBeVisible();

	await page.getByTestId('workspace-import-open').first().click();
	await expect(page.getByRole('heading', { name: 'Choose import method' })).toBeVisible();
	await page.getByRole('radio', { name: /Paste content/i }).click();
	await page.getByTestId('import-spec-paste').fill(sampleOpenApiSpec(title));

	// Submitting must fire the real async import (202 + job id). Driving the full
	// async ingest through the UI poll is timing-sensitive (the dialog can sit on
	// "Importing…" for tens of seconds), so we assert the dialog *initiated* the
	// import rather than racing its completion.
	const [importResponse] = await Promise.all([
		page.waitForResponse((r) => r.url().endsWith('/apis') && r.request().method() === 'POST'),
		page.getByTestId('import-spec-submit').click(),
	]);
	expect(importResponse.status(), 'paste import should return 202 (async job)').toBe(202);

	// The dialog reflects the in-flight import with no error alert.
	await expect(page.getByTestId('import-spec-progress')).toBeVisible();
	await expect(page.getByTestId('import-spec-error')).toBeHidden();
});

test('an imported API renders as a card in the grid', async ({ page, request }) => {
	// A cold worker's first import can take ~25s; widen the per-test budget so
	// the deterministic job-poll (helpers.ts) fits inside it.
	test.slow();

	// Seed via the helper (polls the job to done) so the card assertion is
	// deterministic rather than racing the UI's import poll.
	const apiName = `e2e-grid-${uniqueSuffix()}`;
	await importInlineApi(request, {
		vendor: 'httpbin.org',
		apiName,
		title: `E2E Grid ${apiName}`,
	});

	await page.goto('/app/workspace');
	// The card heading is humanized (#631: apiRefDisplayName title-cases the
	// slug), so match on the mono `vendor/name/version` subtitle, which still
	// renders the raw api_name verbatim, and scope to the enclosing card.
	await expect(
		page.getByTestId('workspace-api-card').filter({ hasText: apiName }).first(),
	).toBeVisible({ timeout: 30_000 });
});

test('open an API detail page for an imported spec', async ({ page, request }) => {
	test.slow();

	// Seed the API through the public import endpoint so this spec owns its
	// fixture and doesn't depend on the paste-dialog spec running first.
	const apiName = `e2e-detail-${uniqueSuffix()}`;
	await importInlineApi(request, {
		vendor: 'httpbin.org',
		apiName,
		title: `E2E Detail ${apiName}`,
	});

	await page.goto('/app/workspace');
	await expect(page.getByRole('heading', { name: 'Workspace' })).toBeVisible();

	// Match the mono `vendor/name/version` subtitle (raw api_name) rather than
	// the humanized heading/aria-label (#631), then click the enclosing card.
	const card = page.getByTestId('workspace-api-card').filter({ hasText: apiName }).first();
	await expect(card).toBeVisible({ timeout: 30_000 });
	await card.click();

	// Detail page shows the operations the spec declared (sampleGet -> 1 op).
	await expect(page).toHaveURL(/\/app\/workspace\//);
	await expect(page.getByText(/operation/i).first()).toBeVisible();
});
