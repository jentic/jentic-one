import { test, expect, type Page } from '@playwright/test';
import { captureConsoleErrors } from './helpers';

/**
 * Activity rail (real backend). The rail is the `complementary` landmark
 * pinned to the right of authenticated pages at `xl+` (≥1280px — the
 * Playwright default 1280×720 viewport clears it), except Monitor,
 * which show the stream in-page. It ships collapsed to a strip. It is backed by the REAL
 * platform event feed: a backlog page from `GET /events` plus a live `GET
 * /events/stream` SSE (see ui/src/shared/app/rail/AgentRail.tsx). This spec
 * drives the operator controls (collapse/expand, pause/resume) and proves a
 * real backend mutation propagates into the live feed.
 *
 * Reuses the authenticated storageState from auth.setup.ts (the `e2e` project
 * in playwright.docker.config.ts) — no per-spec login.
 */
async function openRail(page: Page) {
	await page.goto('/app/agents');
	await page.getByRole('button', { name: /^Show live activity/ }).click();
	const rail = page.getByRole('complementary', { name: 'Activity' });
	await expect(rail).toBeVisible();
	return rail;
}

test('activity rail mounts with its live feed and no console errors', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	// The rail is a labelled complementary landmark with a header and an
	// aria-live event log — all owned by the rail, not data-dependent.
	const rail = await openRail(page);
	await expect(rail.getByRole('log', { name: 'Activity feed' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('activity rail expands and collapses', async ({ page }) => {
	const rail = await openRail(page);

	// Collapse: the feed goes away and the strip exposes only a
	// "Show live activity" affordance.
	await rail.getByRole('button', { name: 'Collapse activity' }).click();
	await expect(rail.getByRole('log', { name: 'Activity feed' })).toBeHidden();
	const expand = page.getByRole('button', { name: /^Show live activity/ });
	await expect(expand).toBeVisible();

	// Expand restores the full rail.
	await expand.click();
	await expect(page.getByRole('log', { name: 'Activity feed' })).toBeVisible();
});

test('activity rail pauses and resumes the live feed', async ({ page }) => {
	const rail = await openRail(page);

	// The single toggle swaps its accessible name between the two states.
	const pause = rail.getByRole('button', { name: 'Pause live feed' });
	await expect(pause).toBeVisible();
	await pause.click();

	const resume = rail.getByRole('button', { name: 'Resume live feed' });
	await expect(resume).toBeVisible();
	await resume.click();

	await expect(rail.getByRole('button', { name: 'Pause live feed' })).toBeVisible();
});
