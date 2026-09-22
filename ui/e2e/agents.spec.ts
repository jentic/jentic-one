import { test, expect, type Page } from '@playwright/test';

/**
 * Agents primary flow (mocked): an operator approves a pending agent and the
 * surface stops asking for a decision on it. Runs against the Vite dev server with
 * MSW, mirroring the real-backend flow (POST /register → pending → :approve →
 * active). An agent's state is read off its own panel, not a table row.
 */
test('approve a pending agent clears its pending state', async ({ page }) => {
	await page.goto('/app/');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Agents' })
		.click();

	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();

	// The pending agent sits in the approval band AND as a tab in the strip.
	// Landing selects a fallback agent, so select this one explicitly before
	// reading its panel. The tab's name carries its status glyph's sr-only text.
	await page.getByRole('tab', { name: /inbox-triage-bot/ }).click();
	await expect(
		page.getByRole('tab', { name: /inbox-triage-bot.*awaiting approval/i }),
	).toBeVisible();
	await expect(page.getByTestId('agent-state-banner-pending')).toBeVisible();

	await page
		.getByRole('region', { name: /Awaiting approval/i })
		.getByRole('button', { name: 'Approve inbox-triage-bot' })
		.click();

	// Approved → the banner that asked for the decision goes away, and the tab
	// no longer announces the agent as awaiting one.
	await expect(page.getByTestId('agent-state-banner-pending')).toBeHidden();
	await expect(page.getByRole('tab', { name: /inbox-triage-bot/ })).toBeVisible();
	await expect(
		page.getByRole('tab', { name: /inbox-triage-bot.*awaiting approval/i }),
	).toHaveCount(0);
});

/**
 * Detail-page flow: the per-agent console at `/agents/:id` is deep-link only — the
 * flat surface offers no jump-off to it (the dock's sheets carry every fact it
 * holds). Verifies its identity + KPI render, approves, and returns to the surface.
 */
test('the agent console is deep-link reachable and can approve', async ({ page }) => {
	await page.goto('/app/agents/agnt_pending_2');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page).toHaveURL(/\/app\/agents\/agnt_pending_2$/);
	await expect(page.getByRole('heading', { name: 'release-notes-bot' })).toBeVisible();
	await expect(page.getByText('Bound credentials')).toBeVisible();

	// Approve from the console → the identity header's badge flips to Active.
	await page.getByRole('button', { name: 'Approve release-notes-bot' }).click();
	await expect(page.getByTestId('detail-status-badge')).toHaveText('Active');

	// Back lands on the flat surface, with this agent among its tabs.
	await page.getByTestId('back-button').click();
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	await expect(page.getByRole('tab', { name: /release-notes-bot/ })).toBeVisible();
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
 * /agents/:id) and verify the round trip — header, toast, and the agent's tab
 * in the fleet strip all pick up the new name from the same session store.
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

	// The fleet strip reflects the rename (list invalidation → mock store).
	await page.getByTestId('back-button').click();
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	await expect(page.getByRole('tab', { name: /support-agent-renamed/ })).toBeVisible();
});

/**
 * Sign in and select `support-agent` on the flat Agents surface, so its dock is
 * the one on screen. Selection goes through the strip rather than a `?agent=`
 * deep link: the sign-in redirect does not carry the query string, and landing
 * picks its own fallback agent — a decisions-first one, not this.
 */
async function openSelectedAgent(page: Page): Promise<void> {
	await page.goto('/app/agents');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();

	const tab = page.getByRole('tab', { name: /support-agent/ });
	await tab.click();
	await expect(tab).toHaveAttribute('aria-selected', 'true');
}

/**
 * Scopes flow (#615): grant a platform permission via the Scopes editor, save
 * (full-list PUT), and verify the chip renders and survives reopening. The scopes
 * card lives in the selected agent's dock, behind the Permissions verb.
 */
test('grant a scope to an agent via the Scopes editor', async ({ page }) => {
	await openSelectedAgent(page);

	await page.getByRole('button', { name: 'Permissions' }).click();
	const sheet = page.getByRole('dialog', { name: 'Permissions' });

	const scopeList = sheet.getByRole('list', { name: 'Granted scopes' });
	await expect(scopeList.getByText('capabilities:execute')).toBeVisible();
	await expect(scopeList.getByText('credentials:read')).toHaveCount(0);

	// The editor is a second dialog stacked on the sheet, so both are named
	// rather than matched as "the dialog".
	await sheet.getByRole('button', { name: 'Edit scopes for support-agent' }).click();
	const editor = page.getByRole('dialog', { name: /Edit scopes/ });
	await editor.getByLabel('Search scopes').fill('credentials:read');
	// `exact` avoids colliding with `owner:credentials:read`, which the search
	// substring-matches too.
	await editor.getByRole('checkbox', { name: 'credentials:read', exact: true }).click();
	await editor.getByRole('button', { name: 'Save scopes' }).click();

	// New grant renders as a chip immediately (cache seeded from the PUT response).
	await expect(scopeList.getByText('credentials:read', { exact: true })).toBeVisible();
	// The synthetic non-catalogue scope the agent already held (legacy:orphaned:read
	// is absent from /permissions) must survive the save untouched.
	await expect(scopeList.getByText('legacy:orphaned:read', { exact: true })).toBeVisible();

	// Reopen the editor → the saved scope reads back as already selected.
	await sheet.getByRole('button', { name: 'Edit scopes for support-agent' }).click();
	const reopened = page.getByRole('dialog', { name: /Edit scopes/ });
	await reopened.getByLabel('Search scopes').fill('credentials:read');
	await expect(
		reopened.getByRole('checkbox', { name: 'credentials:read', exact: true }),
	).toBeChecked();
});

/**
 * Per-actor access requests (#619): the selected agent's Permissions sheet shows
 * the pending access requests THAT agent has filed (mock `ar_1`, scoped by
 * `actor_id`). Opening a row reveals the shared decide dialog; approving every
 * item decides the request so the row leaves the pending queue.
 */
test('show and decide an agent-filed pending access request', async ({ page }) => {
	await openSelectedAgent(page);

	await page.getByRole('button', { name: 'Permissions' }).click();
	const sheet = page.getByRole('dialog', { name: 'Permissions' });

	// The card lists the request this agent filed, summarized by its first item.
	await expect(sheet.getByRole('heading', { name: 'Access requests' })).toBeVisible();
	const row = sheet.getByRole('button').filter({ hasText: 'toolkit · use' }).first();
	await expect(row).toBeVisible();

	// The status filter reveals decided history on demand — switching to
	// Approved surfaces a previously-approved request and hides the pending one.
	await sheet.getByRole('button', { name: 'Approved' }).click();
	await expect(
		sheet.getByText('agent needed read access to the analytics toolkit'),
	).toBeVisible();

	// Back to the default Pending view to decide the still-open request.
	await sheet.getByRole('button', { name: 'Pending' }).click();
	await expect(row).toBeVisible();

	// Open the shared decide dialog, approve everything, advance to confirm, and
	// commit. The dialog is a two-step flow (review → confirm) ending in a
	// terminal screen that the operator dismisses with "Done".
	await row.click();
	const dialog = page.getByRole('dialog', { name: 'Access request' });
	await dialog.getByRole('button', { name: 'Approve all' }).click();
	await dialog.getByRole('button', { name: /Review & submit/i }).click();
	await dialog.getByRole('button', { name: /Confirm decision/i }).click();
	await dialog.getByRole('button', { name: 'Done' }).click();

	// Decided → the request drops off the pending list and the empty state shows.
	await expect(sheet.getByText('No pending access requests')).toBeVisible();
});
