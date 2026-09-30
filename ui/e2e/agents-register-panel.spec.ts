import { test, expect, type Page } from '@playwright/test';

/**
 * The New agent panel (mocked). Over an existing fleet, "New agent" opens on
 * "Register from the CLI": an agent that registers after the panel opened is
 * approved and handed its first API there, and the panel closes onto the fleet
 * with it selected.
 */
async function login(page: Page) {
	await page.goto('/app/');
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
}

test('an existing fleet registers another agent from the New agent panel', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await login(page);
	await page.goto('/app/agents');
	await expect(page.getByTestId('agent-dock')).toBeVisible();

	await page.getByRole('button', { name: 'New agent' }).click();
	const panel = page.getByRole('dialog', { name: 'New agent' });
	await expect(panel).toBeVisible();
	await expect(panel.getByRole('tab', { name: /Register from the CLI/ })).toHaveAttribute(
		'aria-selected',
		'true',
	);
	// The seeded fleet's pending agents were there before the panel: not arrivals.
	await expect(panel.getByTestId('register-status')).toContainText('Listening for new agents…');
	await panel.getByLabel('Agent name').fill('research-bot');
	await expect(panel.getByTestId('register-command')).toContainText('--name research-bot');

	// What `jentic register` leaves behind; the invalidation is what the agent
	// stream does on `agent.self_registered`.
	const id = await page.evaluate(async () => {
		const w = window as unknown as {
			__mswTestHooks: { selfRegisterAgent: (name: string) => string };
			__queryClient: { invalidateQueries: () => Promise<void> };
		};
		const agentId = w.__mswTestHooks.selfRegisterAgent('research-bot');
		await w.__queryClient.invalidateQueries();
		return agentId;
	});

	await expect(panel.getByRole('heading', { name: 'research-bot' })).toBeVisible();
	await expect(panel.getByTestId('agent-facts')).toContainText('Self-registered from the CLI');
	await expect(panel.getByTestId('register-status')).toContainText('awaiting your approval');
	await expect(panel.getByTestId('arrival-warnings')).toHaveCount(0);

	await panel.getByRole('button', { name: 'Approve research-bot' }).click();
	const firstApi = panel.getByTestId('first-api-panel');
	await expect(
		firstApi.getByRole('heading', { name: 'Add GitHub to research-bot' }),
	).toBeVisible();
	await expect(
		panel.getByTestId('register-stepper').getByRole('listitem').nth(3),
	).toHaveAttribute('aria-current', 'step');

	await firstApi.getByRole('button', { name: 'Continue with GitHub' }).click();
	const queue = page.getByRole('dialog', { name: 'Set up 1 API' });
	await expect(queue).toBeVisible();
	await expect(queue.getByTestId('queue-active-pane')).toContainText('GitHub');
	await expect(panel).toHaveCount(0);
	await expect(page).toHaveURL(new RegExp(`agent=${id}`));
	await expect(page.getByRole('tab', { name: /research-bot/ })).toHaveAttribute(
		'aria-selected',
		'true',
	);
});
