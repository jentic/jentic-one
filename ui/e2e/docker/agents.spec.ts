import { test, expect } from '@playwright/test';
import { captureConsoleErrors, createServiceAccount, uniqueSuffix } from './helpers';
import { provisionAdminOwnedAgent } from './agent-flow';

/**
 * Agents (real backend). One flat fleet with no roster and no tab switch, so the
 * shell contract is the page's own controls plus the agent strip. Agents are
 * created out-of-band via Dynamic Client Registration, so this asserts the list
 * contract rather than driving a create the UI doesn't own.
 *
 * Service accounts survive only as their own detail page, created through the
 * public API — so this self-seeds one and asserts that page renders.
 */
test('the agents surface renders its shell', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');
	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Agents' })
		.click();

	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	// No emptiness assertion: the shared docker DB accumulates actors from
	// other specs and reruns, so this pins the shell contract only — the
	// page-level fleet controls and the org-wide credential inventory trigger.
	await expect(page.getByLabel('Filter agents')).toBeVisible();
	await expect(page.getByRole('button', { name: 'New agent' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Credentials' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('a service account created via the API renders its detail page', async ({ page, request }) => {
	const name = `e2e-sa-${uniqueSuffix()}`;
	const id = await createServiceAccount(request, name);

	// There is no roster to click through: the detail page is reached by URL.
	await page.goto(`/app/agents/service-accounts/${id}`);

	// The unique name in the heading is what proves the seeded account resolved
	// from the backend; the status control beside it pins the header contract
	// without asserting a label that depends on which control the status picks.
	await expect(page.getByRole('heading', { name })).toBeVisible();
	await expect(page.getByTestId('detail-status-badge').first()).toBeVisible();
});

/**
 * A DCR-registered agent gets the full identity
 * console — KPI strip, tab shell, per-actor Activity with a Monitor deep-link —
 * and can be renamed in place through the Settings tab (real PATCH /agents/:id
 * round trip against the backend, not MSW).
 */
test('a DCR-registered agent gets the identity console and can be renamed', async ({
	page,
	request,
}) => {
	const errors = captureConsoleErrors(page);
	const agent = await provisionAdminOwnedAgent(request);

	await page.goto(`/app/agents/${agent.clientId}`);
	await expect(page.getByRole('heading', { name: agent.name })).toBeVisible();

	// Console shell: KPI strip + tab set render for a real (fresh) agent.
	await expect(page.getByRole('group', { name: 'Key metrics' })).toBeVisible();
	for (const tab of ['Overview', 'Activity', 'Keys', 'MCP', 'Settings']) {
		await expect(page.getByRole('tab', { name: tab })).toBeVisible();
	}

	// Activity: a fresh agent has no executions, but the feed card (and its
	// pre-filtered Monitor deep-link) still renders for an admin viewer —
	// asserted unconditionally so a regression can't silently skip this check.
	await page.getByRole('tab', { name: 'Activity' }).click();
	// Two links match (back row + feed card) — both share the same href.
	const monitorLink = page.getByRole('link', { name: /Open Monitor/ }).first();
	await expect(monitorLink).toBeVisible();
	expect(await monitorLink.getAttribute('href')).toContain(`actor_id=${agent.clientId}`);

	// Settings: rename via the real PATCH endpoint and verify the round trip.
	await page.getByRole('tab', { name: 'Settings' }).click();
	const renamed = `${agent.name}-renamed`;
	await page.getByLabel('Name').fill(renamed);
	await page.getByRole('button', { name: 'Save changes' }).click();

	await expect(page.getByText('Agent updated')).toBeVisible();
	await expect(page.getByRole('heading', { name: renamed })).toBeVisible();
	// Destructive lifecycle lives in the danger zone.
	await expect(page.getByText('Danger zone')).toBeVisible();
	await expect(page.getByRole('button', { name: `Archive ${renamed}` })).toBeVisible();

	// A hard reload proves the rename persisted server-side — the heading above
	// could otherwise be satisfied by the client-rendered PATCH response alone.
	await page.reload();
	await expect(page.getByRole('heading', { name: renamed })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});
