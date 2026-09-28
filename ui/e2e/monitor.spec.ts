import { test, expect, type Page } from '@playwright/test';

/**
 * Monitor layout flow (mocked, Mode A). The Overview is a stat strip over the
 * usage charts with the live activity stream docked beside them; Expand opens
 * the stream into the full log and "Overview" folds it back. Arriving from a
 * rail page morphs the rail into the docked panel (a view transition) — the
 * navigation must land cleanly whether or not the browser animates it.
 */

function captureConsoleErrors(page: Page): string[] {
	const errors: string[] = [];
	page.on('console', (msg) => {
		if (msg.type() !== 'error') return;
		const text = msg.text();
		if (text.includes('Failed to load resource')) return;
		if (text.includes('net::ERR_')) return;
		errors.push(text);
	});
	page.on('pageerror', (err) => errors.push(err.message));
	return errors;
}

async function signIn(page: Page) {
	await page.goto('/app/');
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
}

test('Monitor overview docks live activity beside the charts and expands it', async ({ page }) => {
	const errors = captureConsoleErrors(page);
	await page.setViewportSize({ width: 1440, height: 900 });
	await signIn(page);

	// Arrive from a rail page with the rail open, via the top nav.
	await page.goto('/app/agents');
	await page.getByRole('button', { name: /^Show live activity/ }).click();
	await page.getByRole('navigation').getByRole('link', { name: 'Monitor' }).first().click();
	await expect(page).toHaveURL(/\/app\/monitor$/);

	await expect(page.getByRole('region', { name: 'Usage at a glance' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Execution Volume' })).toBeVisible();
	const panel = page.getByRole('region', { name: 'Live activity' });
	await expect(panel.getByRole('log', { name: 'Activity feed' })).toBeVisible();
	// Monitor hides the docked rail — the panel IS the stream here.
	await expect(page.getByRole('complementary', { name: 'Activity' })).toHaveCount(0);

	await panel.getByRole('button', { name: 'Expand activity to the full log' }).click();
	await expect(page).toHaveURL(/view=activity/);
	await expect(page.getByRole('group', { name: 'Activity source' })).toBeVisible();

	await page.getByRole('button', { name: 'Overview' }).click();
	await expect(page).toHaveURL(/\/app\/monitor$/);
	await expect(page.getByRole('region', { name: 'Usage at a glance' })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('legacy ?tab=executions deep links open the expanded API calls log', async ({ page }) => {
	await signIn(page);
	await page.goto('/app/monitor?tab=executions');
	await expect(page).toHaveURL(/show=calls/);
	await expect(
		page.getByRole('group', { name: 'Activity source' }).getByRole('button', {
			name: 'API calls',
		}),
	).toHaveAttribute('aria-pressed', 'true');
});
