import { test, expect, type Page } from '@playwright/test';

/**
 * Toolkits primary-flow e2e (mocked, MSW). Exercises the list → create →
 * detail happy path against the module's MSW handlers, so the wired surface
 * (routing + nav + hooks + repository) is covered end-to-end with no backend.
 * Toolkit key issuance is retired (theme 5 phase 4): creating a toolkit mints
 * no key and the Keys tab is a legacy list with a retirement notice.
 * Real-backend coverage is deferred to Mode B / docker config.
 */
async function login(page: Page) {
	await page.goto('/app/');
	await expect(page.getByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
}

test('list → create toolkit → detail → keys retirement', async ({ page }) => {
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

	// Create a new toolkit — no key is minted (issuance retired); the dialog
	// shows a plain confirmation before handing off to the detail page.
	await page
		.getByRole('button', { name: /new toolkit/i })
		.first()
		.click();
	await page.getByLabel('Name').fill('Slack Tools');
	await page.getByRole('button', { name: /^create$/i }).click();
	await expect(page.getByText(/is ready/i)).toBeVisible();
	await expect(page.getByText(/jntc_live_/)).toHaveCount(0);
	await page.getByRole('button', { name: /open toolkit/i }).click();
	await expect(page.getByRole('heading', { name: 'Slack Tools' })).toBeVisible();

	// Back to the list; the new toolkit is there.
	await page.getByRole('link', { name: /all toolkits/i }).click();
	await expect(page.getByText('Slack Tools')).toBeVisible();

	// Open an existing toolkit's detail.
	await page.getByRole('link', { name: /GitHub Tools/ }).click();
	await expect(page.getByRole('heading', { name: 'GitHub Tools' })).toBeVisible();

	// The Keys tab lists existing keys but offers no create affordance — the
	// retirement notice points at service accounts instead.
	await page.getByRole('tab', { name: 'Keys' }).click();
	await expect(page.getByText('CI runner')).toBeVisible();
	await expect(page.getByTestId('toolkit-keys-retired-notice')).toBeVisible();
	await expect(page.getByRole('button', { name: /create key/i })).toHaveCount(0);
});

test('suspended toolkit still shows the keys-blocked chip', async ({ page }) => {
	await login(page);
	await page.goto('/app/toolkits/tk_demo_billing');

	await expect(page.getByRole('heading', { name: /Billing/ })).toBeVisible();
	// Suspended banner is shown above the tabs; the Keys tab keeps its
	// "Keys blocked" chip (and, retirement aside, has no create affordance).
	await expect(page.getByText(/suspended — all access blocked/i)).toBeVisible();
	await page.getByRole('tab', { name: 'Keys' }).click();
	await expect(page.getByText(/keys blocked/i)).toBeVisible();
	await expect(page.getByRole('button', { name: /create key/i })).toHaveCount(0);
});
