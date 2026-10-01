import { test, expect, type Page } from '@playwright/test';
import { captureConsoleErrors, importInlineApi, sampleOpenApiSpec, uniqueSuffix } from './helpers';

/**
 * Workspace (real backend) — the "Your workspace" panel docked beside the
 * Library's catalog (`/app/library`). There is no separate workspace page any
 * more: the panel lists every API registered in this instance (filterable),
 * links each to its hub, and its footer opens the import dialog. The retired
 * `/app/library/workspace` URL redirects to `/app/library` with its query.
 *
 * Import is ASYNC on the real backend (POST /apis -> 202 + job id, the UI
 * polls /jobs/{id}), unlike the synchronous MSW mock — so the paste-import
 * spec asserts the dialog INITIATED the import rather than racing its
 * (timing-sensitive) completion, while a helper-seeded spec covers the row
 * landing in the list deterministically.
 *
 * The default viewport (1280 wide) is `xl`, where the panel is docked.
 */

function panel(page: Page) {
	return page.getByTestId('workspace-dock-panel');
}

/** The panel row for an imported API (rows carry the humanized title, #631). */
async function rowFor(page: Page, apiName: string) {
	// Narrow the (full, possibly long) list to this API first.
	await panel(page).getByLabel('Filter your APIs').fill(apiName);
	return panel(page).getByTestId('workspace-panel-api').first();
}

test('the workspace panel renders on the Library', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');
	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Library' })
		.click();
	await expect(page).toHaveURL(/\/app\/library$/);
	await expect(panel(page).getByRole('heading', { name: 'Your workspace' })).toBeVisible();
	// No expand / "Open your workspace" — the panel is the whole view.
	await expect(page.getByLabel('Open the full workspace')).toHaveCount(0);

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('the retired workspace page redirects to the Library, keeping its filter', async ({
	page,
}) => {
	await page.goto('/app/library/workspace?status=draft');
	await expect(page).toHaveURL(/\/app\/library\?status=draft$/);
	await expect(panel(page)).toBeVisible();
});

test('import an API by pasting a spec drives the async import', async ({ page }) => {
	test.setTimeout(60_000);

	const title = `E2E Paste ${uniqueSuffix()}`;

	await page.goto('/app/library');
	await expect(panel(page)).toBeVisible();

	// Empty workspace ⇒ the empty state's button; otherwise the footer's.
	await panel(page).getByRole('button', { name: 'Import your own API' }).first().click();
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

test('an imported API is listed in the workspace panel', async ({ page, request }) => {
	// A cold worker's first import can take ~25s; widen the per-test budget so
	// the deterministic job-poll (helpers.ts) fits inside it.
	test.slow();

	// Seed via the helper (polls the job to done) so the row assertion is
	// deterministic rather than racing the UI's import poll.
	const apiName = `e2e-grid-${uniqueSuffix()}`;
	await importInlineApi(request, {
		vendor: 'httpbin.org',
		apiName,
		title: `E2E Grid ${apiName}`,
	});

	await page.goto('/app/library');
	const row = await rowFor(page, apiName);
	await expect(row).toBeVisible({ timeout: 30_000 });
	// The row links to the API's hub (raw api_name in the URL).
	await expect(row).toHaveAttribute('href', new RegExp(`/library/workspace/[^/]+/${apiName}/`));
});

test('open an API hub from the workspace panel', async ({ page, request }) => {
	test.slow();

	// Seed the API through the public import endpoint so this spec owns its
	// fixture and doesn't depend on the paste-dialog spec running first.
	const apiName = `e2e-detail-${uniqueSuffix()}`;
	await importInlineApi(request, {
		vendor: 'httpbin.org',
		apiName,
		title: `E2E Detail ${apiName}`,
	});

	await page.goto('/app/library');
	const row = await rowFor(page, apiName);
	await expect(row).toBeVisible({ timeout: 30_000 });
	await row.click();

	// The API hub (Overview tab by default). An inline import lands as a draft
	// revision with nothing promoted, so the Operations tab shows its
	// no-live-revision state with a shortcut to the Versions tab.
	await expect(page).toHaveURL(/\/app\/library\/workspace\//);
	await page.getByRole('tab', { name: /Operations/ }).click();
	// Scope to the operations section: visited tabs stay mounted (hidden), so an
	// unscoped text match could land on the hidden Overview panel.
	const operations = page.getByTestId('operations-section');
	await expect(operations).toBeVisible();
	await expect(operations.getByText('No live revision yet')).toBeVisible();
	await operations.getByTestId('operations-go-to-versions').click();
	await expect(page).toHaveURL(/[?&]tab=versions\b/);
	await expect(page.getByTestId('revisions-section')).toBeVisible();
});
