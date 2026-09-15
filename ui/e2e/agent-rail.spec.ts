import { test, expect, type Page } from '@playwright/test';

/**
 * Agent Rail (mocked) e2e. The rail is a shell-mounted surface present on every
 * authenticated page at `xl+` (≥1280px), backed by the real `/events` feed
 * (served by MSW in Mode A). This spec logs in, asserts the rail mounts with a
 * live event feed, exercises collapse/expand persistence, and confirms the rail
 * is hidden below `xl`.
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
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
}

test.describe('Agent Rail — shell-mounted live event feed', () => {
	test('mounts at xl with a live feed; collapse persists', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.setViewportSize({ width: 1440, height: 900 });
		await login(page);

		const rail = page.getByRole('complementary', { name: /Agent rail/i });
		await expect(rail).toBeVisible();
		await expect(rail.getByText('Agent rail')).toBeVisible();
		// A seeded backlog event renders in the feed.
		await expect(rail.getByText(/Execution failed: slack\.postMessage/i)).toBeVisible();

		// Collapse → the expand affordance appears and the header title is gone.
		await rail.getByRole('button', { name: 'Collapse agent rail' }).click();
		await expect(page.getByRole('button', { name: 'Expand agent rail' })).toBeVisible();
		await expect(rail.getByText('Agent rail')).toBeHidden();

		// Collapse survives a reload (persisted to localStorage).
		await page.reload();
		await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Expand agent rail' })).toBeVisible();

		// Expand again.
		await page.getByRole('button', { name: 'Expand agent rail' }).click();
		await expect(rail.getByText('Agent rail')).toBeVisible();

		expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
	});

	test('is hidden below the xl breakpoint', async ({ page }) => {
		await page.setViewportSize({ width: 1024, height: 800 });
		await login(page);
		// The rail still exists in the DOM but is display:none below xl.
		await expect(page.getByRole('complementary', { name: /Agent rail/i })).toBeHidden();
	});
});
