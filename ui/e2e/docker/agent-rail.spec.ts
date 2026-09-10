import { test, expect } from '@playwright/test';
import { captureConsoleErrors, uniqueSuffix } from './helpers';
import { provisionAdminOwnedAgent, fileAccessRequestAsAgent } from './agent-flow';

/**
 * Agent rail (real backend). The rail is the persistent `complementary` landmark
 * pinned to the right of every authenticated page at `xl+` (≥1280px — the
 * Playwright default 1280×720 viewport clears it). It is backed by the REAL
 * platform event feed: a backlog page from `GET /events` plus a live `GET
 * /events/stream` SSE (see ui/src/shared/app/rail/AgentRail.tsx). This spec
 * drives the operator controls (collapse/expand, pause/resume) and proves a
 * real backend mutation propagates into the live feed.
 *
 * Reuses the authenticated storageState from auth.setup.ts (the `e2e` project
 * in playwright.docker.config.ts) — no per-spec login.
 */
test('agent rail mounts with its live feed and no console errors', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');

	// The rail is a labelled complementary landmark with a header and an
	// aria-live event log — all owned by the rail, not data-dependent.
	const rail = page.getByRole('complementary', { name: 'Agent rail' });
	await expect(rail).toBeVisible();
	await expect(rail.getByText('Agent rail')).toBeVisible();
	await expect(rail.getByRole('log', { name: 'Agent event feed' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('agent rail collapses and expands', async ({ page }) => {
	await page.goto('/app');

	const rail = page.getByRole('complementary', { name: 'Agent rail' });
	await expect(rail).toBeVisible();

	// Collapse: the expanded header label goes away and the narrow rail exposes
	// only an "Expand agent rail" affordance.
	await rail.getByRole('button', { name: 'Collapse agent rail' }).click();
	await expect(rail.getByText('Agent rail')).toBeHidden();
	const expand = page.getByRole('button', { name: 'Expand agent rail' });
	await expect(expand).toBeVisible();

	// Expand restores the full rail.
	await expand.click();
	await expect(
		page.getByRole('complementary', { name: 'Agent rail' }).getByText('Agent rail'),
	).toBeVisible();
});

test('agent rail pauses and resumes the live feed', async ({ page }) => {
	await page.goto('/app');

	const rail = page.getByRole('complementary', { name: 'Agent rail' });
	await expect(rail).toBeVisible();

	// The single toggle swaps its accessible name between the two states.
	const pause = rail.getByRole('button', { name: 'Pause live feed' });
	await expect(pause).toBeVisible();
	await pause.click();

	const resume = rail.getByRole('button', { name: 'Resume live feed' });
	await expect(resume).toBeVisible();
	await resume.click();

	await expect(rail.getByRole('button', { name: 'Pause live feed' })).toBeVisible();
});

test('a real filed access request surfaces in the rail live feed', async ({ page, request }) => {
	// Seed a real event over the same /events feed the rail consumes: an
	// admin-owned agent files an access request, which the backend records as an
	// `access_request.filed` event summarised "Access request filed by {filer}".
	// We key the assertion on THIS agent's id so it is independent of any other
	// rows already in the (shared, non-pristine) feed.
	const agent = await provisionAdminOwnedAgent(request, { name: `e2e-rail-${uniqueSuffix()}` });
	await fileAccessRequestAsAgent(request, agent, {
		reason: `e2e rail ${uniqueSuffix()}`,
		resourceType: 'credential',
		action: 'bind',
		resourceId: `e2e-rail-${uniqueSuffix()}`,
	});

	await page.goto('/app');

	const rail = page.getByRole('complementary', { name: 'Agent rail' });
	await expect(rail).toBeVisible();

	// The backlog page loads on mount; the just-filed event carries our filer id.
	await expect(
		rail.getByText(new RegExp(`Access request filed by ${agent.clientId}`)),
	).toBeVisible({
		timeout: 15_000,
	});
});
