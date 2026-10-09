import { test, expect } from '@playwright/test';
import { captureConsoleErrors, dismissFirstRunFor } from './helpers';
import { provisionAdminOwnedAgent } from './agent-flow';

/**
 * Agents (real backend). One flat fleet with no roster and no tab switch, so the
 * shell contract is the page's own controls plus the agent strip. Service
 * accounts were retired in theme 8 — migrated accounts are ordinary agents now.
 * Agents are created out-of-band via Dynamic Client Registration, so this
 * asserts the list contract rather than driving a create the UI doesn't own.
 */
test('the agents surface renders its shell without a service-accounts tab', async ({ page }) => {
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
	await expect(page.getByRole('button', { name: 'Service accounts' })).toHaveCount(0);

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

/**
 * A DCR-registered agent's path URL lands on the Agents page with it selected:
 * its dock's Activity sheet carries a Monitor deep link pre-filtered by actor,
 * and its Settings sheet renames it in place (real PATCH /agents/:id round trip
 * against the backend, not MSW).
 */
test('a DCR-registered agent opens on the Agents page and can be renamed', async ({
	page,
	request,
}) => {
	const errors = captureConsoleErrors(page);
	const agent = await provisionAdminOwnedAgent(request);
	// On a fresh DB this is the org's only agent — active, with no APIs — which
	// resumes the first-run landing rather than the fleet. This spec is about
	// the fleet view, so the operator has left that suggestion.
	await dismissFirstRunFor(page, agent.clientId);

	await page.goto(`/app/agents/${agent.clientId}`);
	await expect(page).toHaveURL(new RegExp(`/app/agents\\?agent=${agent.clientId}$`));
	const tab = page.getByRole('tab', { name: new RegExp(agent.name) });
	await expect(tab).toHaveAttribute('aria-selected', 'true');
	const dock = page.getByTestId('agent-dock');

	// Activity: a fresh agent has no executions, but the feed card (and its
	// pre-filtered Monitor deep link) still renders for an admin viewer —
	// asserted unconditionally so a regression can't silently skip this check.
	await dock.getByRole('button', { name: 'Activity' }).click();
	const activity = page.getByRole('dialog', { name: 'Activity' });
	const monitorLink = activity.getByRole('link', { name: /Open Monitor/ });
	await expect(monitorLink).toBeVisible();
	expect(await monitorLink.getAttribute('href')).toContain(`actor_id=${agent.clientId}`);
	await activity.getByRole('button', { name: 'Close' }).click();
	await expect(activity).toBeHidden();

	// Settings: rename via the real PATCH endpoint and verify the round trip.
	await dock.getByRole('button', { name: 'Settings' }).click();
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings.getByTestId('agent-provenance')).toBeVisible();
	const renamed = `${agent.name}-renamed`;
	await settings.getByLabel('Name').fill(renamed);
	await settings.getByRole('button', { name: 'Save changes' }).click();

	await expect(page.getByText('Agent updated')).toBeVisible();
	await expect(page.getByRole('tab', { name: new RegExp(renamed) })).toBeVisible();
	// Destructive lifecycle lives in the danger zone.
	await expect(settings.getByText('Danger zone')).toBeVisible();
	await expect(settings.getByRole('button', { name: `Archive ${renamed}` })).toBeVisible();

	// A hard reload proves the rename persisted server-side — the tab above
	// could otherwise be satisfied by the client-side refetch alone.
	await page.reload();
	await expect(page.getByRole('tab', { name: new RegExp(renamed) })).toHaveAttribute(
		'aria-selected',
		'true',
	);

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});
