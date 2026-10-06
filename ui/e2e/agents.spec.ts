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
 * An agent's path URL (`/agents/:id` — what the CLI prints and older links
 * carry) survives sign-in and lands on the Agents page with that agent
 * selected, where it can be decided on its own panel.
 */
test('an agent path URL opens the agent selected on the Agents page', async ({ page }) => {
	await page.goto('/app/agents/agnt_pending_2');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page).toHaveURL(/\/app\/agents\?agent=agnt_pending_2$/);
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
	await expect(page.getByRole('tab', { name: /release-notes-bot/ })).toHaveAttribute(
		'aria-selected',
		'true',
	);

	// Not the banner's longest-waiting pick, so its own panel carries the decision.
	const banner = page.getByTestId('agent-state-banner-pending');
	await expect(banner.getByRole('button', { name: 'Deny' })).toBeVisible();
	await banner.getByRole('button', { name: 'Approve' }).click();
	await expect(banner).toBeHidden();
	await expect(
		page.getByRole('tab', { name: /release-notes-bot.*awaiting approval/i }),
	).toHaveCount(0);
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
 * The selected agent's vitals and activity: the stat strip reads the per-actor
 * usage aggregate, and the dock's Activity sheet shows THIS agent's executions
 * with a Monitor deep link pre-filtered by actor.
 */
test('the Activity sheet feeds per-agent executions and deep-links to Monitor', async ({
	page,
}) => {
	await openSelectedAgent(page);

	await expect(page.getByTestId('stat-executions')).toHaveText('1,204 calls in 7d');

	await page.getByTestId('agent-dock').getByRole('button', { name: 'Activity' }).click();
	const sheet = page.getByRole('dialog', { name: 'Activity' });
	// The feed renders the human-readable operation identity (credential ·
	// METHOD path-template) — never the opaque operation id.
	await expect(sheet.getByText('github · POST /repos/{owner}/{repo}/issues')).toBeVisible();
	await expect(sheet.getByText('Recent changes')).toBeVisible();

	// The Monitor deep link carries the actor filter (Monitor's URL contract).
	const href = await sheet.getByRole('link', { name: /Open Monitor/ }).getAttribute('href');
	expect(href).toContain('show=calls');
	expect(href).toContain('actor_id=agnt_active_1');
	expect(href).toContain('actor_type=agent');
});

/**
 * Editability: rename an agent from the dock's Settings sheet (PATCH
 * /agents/:id) and verify the round trip — toast, and the agent's tab in the
 * fleet strip picks up the new name from the same session store.
 */
test('rename an agent from the Settings sheet round-trips to the strip', async ({ page }) => {
	await openSelectedAgent(page);

	await page.getByTestId('agent-dock').getByRole('button', { name: 'Settings' }).click();
	const sheet = page.getByRole('dialog', { name: 'Settings' });
	// Provenance sits with the identity: when it registered and who approved it.
	await expect(sheet.getByTestId('agent-provenance')).toContainText('Approved by');

	await sheet.getByLabel('Name').fill('support-agent-renamed');
	await sheet.getByRole('button', { name: 'Save changes' }).click();

	await expect(page.getByText('Agent updated')).toBeVisible();
	// Destructive lifecycle lives in the danger zone.
	await expect(sheet.getByText('Danger zone')).toBeVisible();
	await expect(
		sheet.getByRole('button', { name: 'Archive support-agent-renamed' }),
	).toBeVisible();
	await expect(page.getByRole('tab', { name: /support-agent-renamed/ })).toBeVisible();
});

/**
 * Permissions flow (#615): grant a platform permission via the Permissions
 * editor, save (full-list PUT), and verify the chip renders and survives
 * reopening. The permissions card lives in the selected agent's dock, behind the
 * Permissions verb.
 */
test('grant a permission to an agent via the Permissions editor', async ({ page }) => {
	await openSelectedAgent(page);

	await page.getByRole('button', { name: 'Permissions' }).click();
	const sheet = page.getByRole('dialog', { name: 'Permissions' });

	const permissionList = sheet.getByRole('list', { name: 'Granted permissions' });
	await expect(permissionList.getByText('capabilities:execute')).toBeVisible();
	await expect(permissionList.getByText('credentials:read')).toHaveCount(0);

	// The editor is a second dialog stacked on the sheet, so both are named
	// rather than matched as "the dialog".
	await sheet.getByRole('button', { name: 'Edit permissions for support-agent' }).click();
	const editor = page.getByRole('dialog', { name: /Edit permissions/ });
	await editor.getByLabel('Search permissions').fill('credentials:read');
	// `exact` avoids colliding with `owner:credentials:read`, which the search
	// substring-matches too.
	await editor.getByRole('checkbox', { name: 'credentials:read', exact: true }).click();
	await editor.getByRole('button', { name: 'Save permissions' }).click();

	// New grant renders as a chip immediately (cache seeded from the PUT response).
	await expect(permissionList.getByText('credentials:read', { exact: true })).toBeVisible();
	// The synthetic non-catalogue permission the agent already held
	// (legacy:orphaned:read is absent from /permissions) must survive the save
	// untouched.
	await expect(permissionList.getByText('legacy:orphaned:read', { exact: true })).toBeVisible();

	// Reopen the editor → the saved permission reads back as already selected.
	await sheet.getByRole('button', { name: 'Edit permissions for support-agent' }).click();
	const reopened = page.getByRole('dialog', { name: /Edit permissions/ });
	await reopened.getByLabel('Search permissions').fill('credentials:read');
	await expect(
		reopened.getByRole('checkbox', { name: 'credentials:read', exact: true }),
	).toBeChecked();
});
