import { test, expect, type Page } from '@playwright/test';

/**
 * Agents landing (mocked). A fresh workspace shows the zero-agents landing,
 * where the first agent is approved and given its next step before the fleet
 * view takes over.
 */
async function login(page: Page) {
	await page.goto('/app/');
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
}

test('a fresh workspace finishes its first agent inside the landing', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await login(page);
	await page.goto('/app/agents');
	await expect(page.getByTestId('agent-dock')).toBeVisible();
	// Mocked dev only: empty the fleet, then refetch.
	await page.evaluate(async () => {
		const w = window as unknown as {
			__mswTestHooks: { clearAgentsStore: () => void };
			__queryClient: { resetQueries: () => Promise<void> };
		};
		w.__mswTestHooks.clearAgentsStore();
		await w.__queryClient.resetQueries();
	});

	const landing = page.getByTestId('agents-empty-landing');
	await expect(landing).toBeVisible();
	// No agent has ever existed here: the suggestion is the first agent's.
	await expect(page.getByLabel('Agent name')).toHaveValue('my-first-agent');
	await page.getByLabel('Agent name').fill('research-bot');
	await expect(page.getByTestId('register-command')).toContainText('--name research-bot');

	// What `jentic register` leaves behind. The invalidation is what the agent
	// stream does on `agent.self_registered`; the page's roster poll is only the
	// fallback, too slow to lean on inside an assertion's timeout.
	const id = await page.evaluate(async () => {
		const w = window as unknown as {
			__mswTestHooks: { selfRegisterAgent: (name: string) => string };
			__queryClient: { invalidateQueries: () => Promise<void> };
		};
		const agentId = w.__mswTestHooks.selfRegisterAgent('research-bot');
		await w.__queryClient.invalidateQueries();
		return agentId;
	});

	// Arrival: the card turns into the approval, and the page stays the landing.
	const card = page.getByTestId('first-agent-card');
	await expect(card.getByRole('heading', { name: 'research-bot' })).toBeVisible();
	await expect(page.getByTestId('agent-facts')).toContainText('Self-registered');
	await expect(page.getByTestId('register-status')).toContainText('awaiting your approval');
	await expect(page.getByTestId('manual-card')).toHaveCount(0);
	await expect(page.getByTestId('register-stepper').getByRole('listitem').nth(2)).toHaveAttribute(
		'aria-current',
		'step',
	);
	await expect(page.getByRole('region', { name: 'Awaiting approval' })).toHaveCount(0);
	await expect(page.getByTestId('agent-dock')).toHaveCount(0);

	// Approved: the first three steps done, "Give it an API" current, and GitHub suggested.
	await card.getByRole('button', { name: 'Approve research-bot' }).click();
	const panel = page.getByTestId('first-api-panel');
	await expect(panel.getByRole('heading', { name: 'Add GitHub to research-bot' })).toBeVisible();
	const steps = page.getByTestId('register-stepper').getByRole('listitem');
	for (const i of [0, 1, 2]) await expect(steps.nth(i)).toHaveAttribute('data-state', 'done');
	await expect(steps.nth(3)).toHaveAttribute('aria-current', 'step');
	await expect(steps.nth(3)).toContainText('Give it an API');
	await expect(page.getByTestId('ghost-tab')).toHaveAttribute('data-status', 'active');

	// Continue with GitHub: straight to its credential step, over the fleet.
	await panel.getByRole('button', { name: 'Continue with GitHub' }).click();
	const queue = page.getByRole('dialog', { name: 'Set up 1 API' });
	await expect(queue).toBeVisible();
	await expect(queue.getByTestId('queue-active-pane')).toContainText('GitHub');
	await expect(landing).toHaveCount(0);
	await expect(page).toHaveURL(new RegExp(`agent=${id}`));
	await expect(page.getByRole('tab', { name: /research-bot/ })).toHaveAttribute(
		'aria-selected',
		'true',
	);
});
