import { test, expect } from '@playwright/test';
import { captureConsoleErrors, createServiceAccount, uniqueSuffix } from './helpers';
import { provisionAdminOwnedAgent } from './agent-flow';

/**
 * Agents (real backend). The Agents surface has two tabs: OAuth Agents and
 * Service accounts. On a clean DB both are empty. Service accounts can be
 * created through the public API (verified live: POST /service-accounts -> 201),
 * so this self-seeds one and asserts it surfaces in the roster — the
 * create-then-assert pattern, since the real DB has no MSW fixtures.
 *
 * OAuth agents are created out-of-band via Dynamic Client Registration (the
 * agent self-registers), not from this admin UI, so we assert the empty/list
 * contract for that tab rather than driving a create that the UI doesn't own.
 */
test('the agents surface renders its shell and tab switch', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app');
	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Agents' })
		.click();

	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	// The Agents/Service-accounts tab switch is present (segmented control).
	// No emptiness assertion: the shared docker DB accumulates actors from
	// other specs and reruns, so this pins the shell contract only.
	await expect(page.getByRole('button', { name: 'Service accounts' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('a service account created via the API shows up in the roster', async ({ page, request }) => {
	const name = `e2e-sa-${uniqueSuffix()}`;
	await createServiceAccount(request, name);

	await page.goto('/app/agents');
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();

	// Switch to the Service accounts tab where the seeded account lives.
	await page.getByRole('button', { name: 'Service accounts' }).click();

	// The freshly created SA is approved at creation (the backend approves
	// inside the create transaction), so it renders in the fleet table.
	await expect(page.getByText(name).first()).toBeVisible();
});

test('the service-account create sheet opens from the agents surface', async ({ page }) => {
	await page.goto('/app/agents');
	await page.getByRole('button', { name: 'Service accounts' }).click();

	// The create affordance opens a sheet/dialog with a name field. We assert it
	// opens (UI wiring) without submitting, to keep this spec's mutation surface
	// limited to the API-seeded path above.
	await page
		.getByRole('button', { name: /new service account|create service account/i })
		.first()
		.click();
	await expect(page.getByLabel('Name')).toBeVisible();
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
	for (const tab of ['Overview', 'Activity', 'Access', 'Keys', 'Settings']) {
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
