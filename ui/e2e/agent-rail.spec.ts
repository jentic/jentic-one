import { test, expect, type Page } from '@playwright/test';

/**
 * Activity rail (mocked) e2e. The rail is a shell-mounted surface at `xl+`
 * (≥1280px) on every authenticated page except Home and Monitor — both of
 * which already show the stream in the page — backed by the real `/events`
 * feed (served by MSW in Mode A). It ships collapsed to a thin "Activity"
 * strip; below `xl` the same body opens in a drawer from the top bar's
 * "Activity" button. This spec logs in, asserts the collapsed default, the
 * expand/collapse persistence, the "Failures only" toggle, the pages that
 * hide the rail, and the below-`xl` drawer.
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
	return errors;
}

async function login(page: Page) {
	await page.goto('/app/');
	await expect(page.getByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
}

/**
 * Land on a page that carries the rail (Monitor doesn't) and doesn't scope it —
 * Agents auto-selects an agent at xl, which points the lens at it.
 */
async function gotoRailPage(page: Page) {
	await page.goto('/app/workspace');
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
}

async function openRail(page: Page) {
	await page.getByRole('button', { name: /^Show live activity/ }).click();
	const rail = page.getByRole('complementary', { name: 'Activity' });
	await expect(rail.getByRole('log', { name: 'Activity feed' })).toBeVisible();
	return rail;
}

test.describe('Activity rail — shell-mounted live event feed', () => {
	test('ships collapsed at xl; expand and collapse persist', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.setViewportSize({ width: 1440, height: 900 });
		await login(page);
		await gotoRailPage(page);

		// First visit: only the strip, no feed.
		const expand = page.getByRole('button', { name: /^Show live activity/ });
		await expect(expand).toBeVisible();
		await expect(page.getByRole('log', { name: 'Activity feed' })).toBeHidden();

		const rail = await openRail(page);
		// A seeded backlog event renders in plain language.
		await expect(rail.getByText(/Failed: slack\.postMessage/i)).toBeVisible();
		await expect(rail.getByRole('link', { name: 'Open in Monitor →' })).toBeVisible();

		// Open survives a reload (persisted to localStorage)…
		await page.reload();
		await expect(page.getByRole('log', { name: 'Activity feed' })).toBeVisible();

		// …and so does collapsing it again.
		await rail.getByRole('button', { name: 'Collapse activity' }).click();
		await expect(expand).toBeVisible();
		await page.reload();
		await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
		await expect(expand).toBeVisible();
		await expect(page.getByRole('log', { name: 'Activity feed' })).toBeHidden();

		expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
	});

	test('"Failures only" narrows the feed', async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await login(page);
		await gotoRailPage(page);
		const rail = await openRail(page);
		await expect(rail.getByText(/Imported petstore/i)).toBeVisible();
		await rail.getByRole('button', { name: /^Failures only/ }).click();
		await expect(rail.getByText(/Imported petstore/i)).toBeHidden();
		await expect(rail.getByText(/Failed: slack\.postMessage/i)).toBeVisible();
	});

	test('Monitor shows the stream in-page, so the rail steps aside there only', async ({
		page,
	}) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await login(page);
		// Home (landed on by login) keeps the rail.
		await expect(page.getByRole('complementary', { name: 'Activity' })).toHaveCount(1);
		await page.goto('/app/monitor');
		await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
		await expect(page.getByRole('complementary', { name: 'Activity' })).toHaveCount(0);
		// The bell is still there on both.
		await expect(page.getByRole('button', { name: /^Notifications/ })).toBeVisible();
	});

	test('the Notifications bell lists what needs you and holds the alert settings', async ({
		page,
	}) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await login(page);
		await gotoRailPage(page);
		await page.getByRole('button', { name: /^Notifications \(\d+ needs? you\)/ }).click();
		const panel = page.getByRole('dialog', { name: /^Notifications/ });
		await expect(panel.getByRole('region', { name: 'Approvals' })).toBeVisible();
		await panel.getByRole('button', { name: 'Notification settings' }).click();
		const settings = page.getByRole('dialog', { name: 'Notification settings' });
		await settings.getByRole('radio', { name: /Every event/ }).check();
		await page.reload();
		await page.getByRole('button', { name: /^Notifications/ }).click();
		await page.getByRole('button', { name: 'Notification settings' }).click();
		await expect(page.getByRole('radio', { name: /Every event/ })).toBeChecked();
	});

	test('below xl the rail is hidden and opens as a drawer from the top bar', async ({ page }) => {
		await page.setViewportSize({ width: 1100, height: 800 });
		await login(page);
		await gotoRailPage(page);
		// The docked rail still exists in the DOM but is display:none below xl.
		await expect(page.getByRole('complementary', { name: 'Activity' })).toBeHidden();

		await page.getByRole('button', { name: /^Activity/ }).click();
		const drawer = page.getByRole('dialog', { name: 'Activity' });
		await expect(drawer).toBeVisible();
		await expect(drawer.getByText(/Failed: slack\.postMessage/i)).toBeVisible();
		await drawer.getByRole('button', { name: 'Close activity' }).click();
		await expect(drawer).toBeHidden();
	});
});
