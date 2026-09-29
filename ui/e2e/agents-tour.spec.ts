import { test, expect, type Page } from '@playwright/test';

/**
 * Agents tour (mocked). The tour is the page header's "Show the tour" overlay
 * over the fleet; Esc closes it and hands the page back.
 */
async function login(page: Page) {
	await page.goto('/app/');
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
}

test('the tour opens over the fleet and Esc returns to it', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await login(page);
	await page.goto('/app/agents');
	await expect(page.getByTestId('agent-dock')).toBeVisible();

	await page.getByRole('button', { name: /^Show the tour/ }).click();
	await expect(page.getByRole('dialog', { name: 'The Jentic One tour' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'The Jentic One tour' })).toHaveCount(0);
	await expect(page.getByTestId('agent-dock')).toBeVisible();
});
