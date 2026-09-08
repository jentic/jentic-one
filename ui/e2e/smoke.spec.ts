import { test, expect, type Page } from '@playwright/test';

/**
 * Smoke test (mocked): the app boots, the shell renders, the mocked admin
 * health resolves, and the console is clean. This is intentionally the only e2e
 * spec until feature flows exist — it guards the harness itself (Vite + MSW +
 * routing) so feature PRs inherit a known-good baseline. See
 * jentic-one-ui-migration/pr-briefs/00-test-harness.md.
 */

function captureConsoleErrors(page: Page): string[] {
	const errors: string[] = [];
	page.on('console', (msg) => {
		if (msg.type() !== 'error') return;
		const text = msg.text();
		// Ignore transient resource/network noise unrelated to app code.
		if (text.includes('Failed to load resource')) return;
		if (text.includes('net::ERR_')) return;
		errors.push(text);
	});
	return errors;
}

test('app loads, login works, shell renders, health resolves, console clean', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app/');

	// Unauthenticated → redirected to the login screen.
	await expect(page.getByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();

	// MSW accepts any credentials and issues a mock token. Target the password
	// field by role, not getByLabel — the Input's show/hide toggle button also
	// carries a "…password" accessible name and would collide with a label match.
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	// Authenticated shell renders with the dashboard and primary nav.
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();

	// The dashboard composes its overview from real list endpoints (mocked
	// here): the "Needs your action" header bell renders its count badge once
	// its sources resolve (the body queue cards were folded into it in the
	// rebuild).
	await expect(page.getByRole('button', { name: /Needs your action \(\d+/ })).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});
