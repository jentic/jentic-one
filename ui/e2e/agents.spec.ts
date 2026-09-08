import { test, expect } from '@playwright/test';

/**
 * Agents primary flow (mocked): an operator approves a pending agent and the
 * row flips from Pending → Active. Runs against the Vite dev server with MSW
 * (the agents in-memory store), mirroring the real-backend flow verified during
 * planning (POST /register → pending → :approve → active).
 */
test('approve a pending agent flips its status to active', async ({ page }) => {
	await page.goto('/app/');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Agents' })
		.click();

	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();

	// The pending agent sits in the approval queue band AND as a Pending row in
	// the fleet table.
	const row = page.getByRole('row').filter({ hasText: 'inbox-triage-bot' });
	await expect(row.getByText('Pending', { exact: true })).toBeVisible();

	await page
		.getByRole('region', { name: /Awaiting approval/i })
		.getByRole('button', { name: 'Approve inbox-triage-bot' })
		.click();

	await expect(row.getByText('Active', { exact: true })).toBeVisible();
});

/**
 * Detail-page flow: open an agent's full detail page from the list, verify its
 * identity + bound toolkits render, and approve a pending agent from there.
 */
test('open the agent detail page and approve from it', async ({ page }) => {
	await page.goto('/app/');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Agents' })
		.click();

	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();

	// Drill into a pending agent's detail page via its name link in the fleet
	// table (the same name also links from the approval band — scope to the row).
	await page
		.getByRole('row')
		.filter({ hasText: 'release-notes-bot' })
		.getByRole('link', { name: 'release-notes-bot' })
		.click();

	await expect(page).toHaveURL(/\/app\/agents\/agnt_pending_2$/);
	await expect(page.getByRole('heading', { name: 'release-notes-bot' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Bound toolkits' })).toBeVisible();

	// Approve from the detail page → the identity header's badge flips to Active.
	await page.getByRole('button', { name: 'Approve release-notes-bot' }).click();
	await expect(page.getByTestId('detail-status-badge')).toHaveText('Active');

	// Back to the list works.
	await page.getByTestId('back-button').click();
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
});

/**
 * Identity console: the detail page's Activity tab shows THIS agent's
 * execution feed and deep-links into Monitor pre-filtered by actor.
 */
test('the Activity tab feeds per-agent executions and deep-links to Monitor', async ({ page }) => {
	await page.goto('/app/agents/agnt_active_1');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page.getByRole('heading', { name: 'support-agent' })).toBeVisible();

	// KPI strip reads the per-actor usage aggregate.
	await expect(page.getByRole('group', { name: 'Key metrics' }).getByText('1,204')).toBeVisible();

	await page.getByRole('tab', { name: 'Activity' }).click();
	await expect(page.getByText('github.create_issue')).toBeVisible();

	// The Monitor deep-link carries the actor filter (Monitor's URL contract).
	// Two links match (back row + feed card) — both share the same href.
	const href = await page
		.getByRole('link', { name: /Open Monitor/ })
		.first()
		.getAttribute('href');
	expect(href).toContain('tab=executions');
	expect(href).toContain('actor_id=agnt_active_1');
	expect(href).toContain('actor_type=agent');

	// Tab state is deep-linkable (?tab=) and survives reload.
	await expect(page).toHaveURL(/tab=activity/);
	await page.reload();
	await expect(page.getByText('github.create_issue')).toBeVisible();
});

/**
 * Editability: rename an agent from the Settings tab (PATCH
 * /agents/:id) and verify the round trip — header, toast, and the fleet
 * table row all pick up the new name from the same session store.
 */
test('rename an agent from the Settings tab round-trips to the list', async ({ page }) => {
	await page.goto('/app/agents/agnt_active_1');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page.getByRole('heading', { name: 'support-agent' })).toBeVisible();

	await page.getByRole('tab', { name: 'Settings' }).click();
	const nameInput = page.getByLabel('Name');
	await nameInput.fill('support-agent-renamed');
	await page.getByRole('button', { name: 'Save changes' }).click();

	// Toast + header re-render from the PATCH response.
	await expect(page.getByText('Agent updated')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'support-agent-renamed' })).toBeVisible();

	// Destructive lifecycle lives in the danger zone, not the header.
	await expect(page.getByText('Danger zone')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Archive support-agent-renamed' })).toBeVisible();

	// The fleet table reflects the rename (list invalidation → mock store).
	await page.getByTestId('back-button').click();
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	await expect(page.getByRole('row').filter({ hasText: 'support-agent-renamed' })).toBeVisible();
});

/**
 * Scopes flow (#615): open an active agent's detail page, grant a platform
 * permission via the Scopes editor, save (full-list PUT), and verify the new
 * scope renders as a chip and is reflected when the editor is reopened (read
 * back from the mock store within the session).
 */
test('grant a scope to an agent via the Scopes editor', async ({ page }) => {
	await page.goto('/app/agents/agnt_active_1');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page.getByRole('heading', { name: 'support-agent' })).toBeVisible();

	// Scopes live on the detail page's Access tab.
	await page.getByRole('tab', { name: 'Access' }).click();

	const scopeList = page.getByRole('list', { name: 'Granted scopes' });
	await expect(scopeList.getByText('capabilities:execute')).toBeVisible();
	await expect(scopeList.getByText('credentials:read')).toHaveCount(0);

	await page.getByRole('button', { name: 'Edit scopes for support-agent' }).click();
	const dialog = page.getByRole('dialog');
	await dialog.getByLabel('Search scopes').fill('credentials:read');
	// `exact` avoids colliding with `owner:credentials:read`, which the search
	// substring-matches too.
	await dialog.getByRole('checkbox', { name: 'credentials:read', exact: true }).click();
	await dialog.getByRole('button', { name: 'Save scopes' }).click();

	// New grant renders as a chip immediately (cache seeded from the PUT response).
	await expect(scopeList.getByText('credentials:read', { exact: true })).toBeVisible();
	// The synthetic non-catalogue scope the agent already held (legacy:orphaned:read
	// is absent from /permissions) must survive the save untouched.
	await expect(scopeList.getByText('legacy:orphaned:read', { exact: true })).toBeVisible();

	// Reopen the editor → the saved scope reads back as already selected.
	await page.getByRole('button', { name: 'Edit scopes for support-agent' }).click();
	const reopened = page.getByRole('dialog');
	await reopened.getByLabel('Search scopes').fill('credentials:read');
	await expect(
		reopened.getByRole('checkbox', { name: 'credentials:read', exact: true }),
	).toBeChecked();
});

/**
 * Per-actor access requests (#619): the active agent's detail page shows the
 * pending access requests THAT agent has filed (mock `ar_1`, scoped by
 * `actor_id`). Opening a row reveals the shared decide dialog; approving every
 * item decides the request so the row leaves the pending queue.
 */
test('show and decide an agent-filed pending access request', async ({ page }) => {
	await page.goto('/app/agents/agnt_active_1');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page.getByRole('heading', { name: 'support-agent' })).toBeVisible();

	// Access requests live on the detail page's Access tab.
	await page.getByRole('tab', { name: 'Access' }).click();

	// The card lists the request this agent filed, summarized by its first item.
	await expect(page.getByRole('heading', { name: 'Access requests' })).toBeVisible();
	const row = page.getByRole('button').filter({ hasText: 'toolkit · use' }).first();
	await expect(row).toBeVisible();

	// The status filter reveals decided history on demand — switching to
	// Approved surfaces a previously-approved request and hides the pending one.
	await page.getByRole('button', { name: 'Approved' }).click();
	await expect(page.getByText('agent needed read access to the analytics toolkit')).toBeVisible();

	// Back to the default Pending view to decide the still-open request.
	await page.getByRole('button', { name: 'Pending' }).click();
	await expect(row).toBeVisible();

	// Open the shared decide dialog, approve everything, advance to confirm, and
	// commit. The dialog is a two-step flow (review → confirm) ending in a
	// terminal screen that the operator dismisses with "Done".
	await row.click();
	const dialog = page.getByRole('dialog');
	await dialog.getByRole('button', { name: 'Approve all' }).click();
	await dialog.getByRole('button', { name: /Review & submit/i }).click();
	await dialog.getByRole('button', { name: /Confirm decision/i }).click();
	await dialog.getByRole('button', { name: 'Done' }).click();

	// Decided → the request drops off the pending list and the empty state shows.
	await expect(page.getByText('No pending access requests')).toBeVisible();
});
