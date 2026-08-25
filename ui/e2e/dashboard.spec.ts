import { test, expect, type Page } from '@playwright/test';

/**
 * Dashboard landing flow (mocked). Drives the real login → shell → `/app` index
 * and asserts the overview composed from the mocked list endpoints renders:
 * the "Needs your action" header bell + dropdown in particular (the brief's
 * named flow). Runs against MSW (Mode A) so it needs no backend.
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

test('dashboard landing surfaces the action queue behind the header bell', async ({ page }) => {
	const errors = captureConsoleErrors(page);

	await page.goto('/app/');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();

	// The action queue lives behind the header bell: one count badge, one
	// dropdown. NOTE: `/agents` is registered by BOTH the dashboard and agents
	// modules in the shared MSW table; MSW v2 is first-match-wins and
	// `...agentsHandlers` is spread first, so in Mode A the AGENTS fixture
	// serves `/agents` here (not dashboard's `invoice-bot`). We therefore
	// assert a row the winning handler provides (`inbox-triage-bot`) plus its
	// explicit action button.
	await page.getByRole('button', { name: /Needs your action \(\d+/ }).click();
	const inbox = page.getByRole('dialog', { name: 'Needs your action' });
	await expect(inbox.getByText('inbox-triage-bot')).toBeVisible();
	await expect(
		inbox.getByRole('button', { name: 'Review agent inbox-triage-bot' }),
	).toBeVisible();

	// An alert row from `/events` (owned ONLY by the dashboard module, so its
	// fixture `Credential failing` is stable here) sits in the same list.
	await expect(inbox.getByText('Credential failing')).toBeVisible();
	await expect(
		inbox.getByRole('button', {
			name: /View (error|critical|warning) event: Credential failing/,
		}),
	).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(inbox).toHaveCount(0);

	// Gateway health (org:admin layer): the mocked user is an admin and the
	// monitor module's `/monitoring/usage` fixture answers in Mode A, so the
	// real-aggregate KPIs and the top-usage table render.
	await expect(page.getByRole('heading', { name: 'Gateway health' })).toBeVisible();
	await expect(page.getByText('p95 latency')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Top usage' })).toBeVisible();

	// The range + lens toggles are interactive: flip to 7d and to Toolkits.
	await page
		.getByRole('group', { name: 'Time range' })
		.getByRole('button', { name: '7d' })
		.click();
	await page.getByRole('tab', { name: 'Toolkits' }).click();

	// Chart tooltips are immediate custom popovers (not slow native titles):
	// hovering a volume column reveals the bucket's exact counts.
	await page.locator('[data-bar]').first().hover();
	await expect(page.getByRole('tooltip')).toBeVisible();

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('bell dropdown stays inside a phone viewport', async ({ page }) => {
	// On a phone the header actions wrap and the bell lands on the LEFT half;
	// the right-anchored panel must clamp instead of hanging off-screen.
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/app/');

	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await page.getByRole('button', { name: /Needs your action/ }).click();
	const inbox = page.getByRole('dialog', { name: 'Needs your action' });
	await expect(inbox).toBeVisible();
	const box = await inbox.boundingBox();
	expect(box).not.toBeNull();
	expect(box!.x).toBeGreaterThanOrEqual(0);
	expect(box!.x + box!.width).toBeLessThanOrEqual(390);
});
