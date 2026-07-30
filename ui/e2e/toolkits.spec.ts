import { test, expect, type Page } from '@playwright/test';

/**
 * Toolkits primary-flow e2e (mocked, MSW). Exercises the list → create →
 * detail → create-key happy path against the module's MSW handlers, so the
 * wired surface (routing + nav + hooks + repository) is covered end-to-end with
 * no backend. Real-backend coverage is deferred to Mode B / docker config.
 */
async function login(page: Page) {
	await page.goto('/app/');
	await expect(page.getByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
}

test('list → create toolkit → detail → create key', async ({ page }) => {
	await login(page);

	// Navigate to Toolkits via the primary nav.
	await page
		.getByRole('navigation', { name: 'Primary' })
		.getByRole('link', { name: 'Toolkits' })
		.click();
	await expect(page.getByRole('heading', { name: 'Toolkits' })).toBeVisible();

	// Seeded toolkits render, the busy card carries its 7d usage sparkline, and
	// the Discover escape hatch is present.
	await expect(page.getByText('GitHub Tools')).toBeVisible();
	await expect(page.getByTestId('toolkit-card-usage')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Import an API' })).toBeVisible();

	// Create a new toolkit — the dialog now reveals the one-time key before
	// handing off to the detail page.
	await page
		.getByRole('button', { name: /new toolkit/i })
		.first()
		.click();
	await page.getByLabel('Name').fill('Slack Tools');
	await page.getByRole('button', { name: /^create$/i }).click();
	await expect(page.getByText('jntc_live_mockplaintextkey_show_once')).toBeVisible();
	await page.getByRole('button', { name: /open toolkit/i }).click();
	await expect(page.getByRole('heading', { name: 'Slack Tools' })).toBeVisible();

	// Back to the list; the new toolkit is there.
	await page.getByRole('link', { name: /all toolkits/i }).click();
	await expect(page.getByText('Slack Tools')).toBeVisible();

	// Open an existing toolkit's detail.
	await page.getByRole('link', { name: /GitHub Tools/ }).click();
	await expect(page.getByRole('heading', { name: 'GitHub Tools' })).toBeVisible();

	// Create an API key on the Keys tab and confirm the one-time plaintext key
	// is revealed.
	await page.getByRole('tab', { name: 'Keys' }).click();
	await expect(page.getByText('CI runner')).toBeVisible();
	await page.getByRole('button', { name: /create key/i }).click();
	await page.getByRole('button', { name: /^generate$/i }).click();
	await expect(page.getByText('New API Key Created')).toBeVisible();
	await expect(page.getByText('jntc_live_freshmockplaintext_show_once')).toBeVisible();
});

test('suspended toolkit blocks key creation', async ({ page }) => {
	await login(page);
	await page.goto('/app/toolkits/tk_demo_billing');

	await expect(page.getByRole('heading', { name: /Billing/ })).toBeVisible();
	// Suspended banner is shown above the tabs and the Keys tab hides the
	// Create Key affordance (keys are blocked while suspended).
	await expect(page.getByText(/suspended — all access blocked/i)).toBeVisible();
	await page.getByRole('tab', { name: 'Keys' }).click();
	await expect(page.getByText(/keys blocked/i)).toBeVisible();
	await expect(page.getByRole('button', { name: /create key/i })).toHaveCount(0);
});
