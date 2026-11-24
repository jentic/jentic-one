import { test, expect, type Page } from '@playwright/test';

/**
 * Agents landing (mocked). A fresh workspace shows the zero-agents landing,
 * whose "Connect with" card opens the New agent panel on the chosen way in.
 * The first agent registers, is approved and is offered its first API
 * (GitHub) in that panel before the fleet view takes over.
 */
async function login(page: Page) {
	await page.goto('/app/');
	await page.getByLabel('Email').fill('admin@local');
	await page.getByRole('textbox', { name: 'Password' }).fill('password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
}

test('a fresh workspace registers its first agent from the landing', async ({ page }) => {
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

	// The URL still names the agent the fleet view had selected, which the empty
	// roster no longer holds: "Agent not found", and "Show my agents" drops the link.
	const notFound = page.getByTestId('agent-not-found');
	await expect(notFound).toBeVisible();
	await notFound.getByRole('button', { name: 'Show my agents' }).click();
	await expect(page).not.toHaveURL(/agent=/);

	// The landing: three ways in, the CLI chosen, its command previewed.
	const landing = page.getByTestId('agents-empty-landing');
	await expect(landing).toBeVisible();
	await expect(landing.getByRole('heading', { name: 'Connect your first agent' })).toBeVisible();
	const ways = landing.getByRole('radiogroup', { name: 'How to connect your first agent' });
	await expect(ways.getByRole('radio')).toHaveText([
		/Register from the CLI/,
		/Connect over MCP/,
		/Create it here/,
	]);
	await expect(ways.getByRole('radio', { name: /Register from the CLI/ })).toBeChecked();
	// No agent has ever existed here: the suggestion is the first agent's.
	await expect(landing.getByTestId('register-command')).toContainText('jentic register --url');
	await expect(landing.getByTestId('register-command')).toContainText('--name my-first-agent');
	await expect(page.getByTestId('agent-dock')).toHaveCount(0);

	// Continue with CLI: the New agent panel, on its CLI route, with the same name.
	await landing.getByRole('button', { name: /Continue with CLI/ }).click();
	const panel = page.getByRole('dialog', { name: 'New agent' });
	await expect(panel).toBeVisible();
	await expect(panel.getByRole('tab', { name: 'Register from the CLI' })).toHaveAttribute(
		'aria-selected',
		'true',
	);
	await expect(panel.getByLabel('Agent name')).toHaveValue('my-first-agent');
	await panel.getByLabel('Agent name').fill('research-bot');
	await expect(panel.getByTestId('register-command')).toContainText('--name research-bot');

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

	// Arrival: the panel turns into the approval, and the page behind stays the landing.
	const card = panel.getByTestId('arrival-card');
	await expect(card.getByRole('heading', { name: 'research-bot' })).toBeVisible();
	await expect(card.getByTestId('agent-provenance')).toContainText('Self-registered');
	await expect(panel.getByTestId('register-command')).toHaveCount(0);
	await expect(panel.getByTestId('register-progress')).toHaveText('Step 2 of 3 · Approve it');
	await expect(page.getByRole('region', { name: 'Awaiting approval' })).toHaveCount(0);
	await expect(landing).toBeVisible();
	await expect(landing.getByTestId('ghost-tab')).toHaveAttribute('data-status', 'pending');
	await expect(page.getByTestId('agent-dock')).toHaveCount(0);

	// Approved: the org's first agent gets its first API in the same panel — GitHub suggested.
	await card.getByRole('button', { name: 'Approve research-bot' }).click();
	const firstApi = panel.getByTestId('first-api-panel');
	await expect(
		firstApi.getByRole('heading', { name: 'Add GitHub to research-bot' }),
	).toBeVisible();
	await expect(panel.getByTestId('register-progress')).toHaveText('Step 3 of 3 · Give it an API');
	await expect(page.getByRole('dialog', { name: 'Add APIs' })).toHaveCount(0);

	// Continue with GitHub: straight to its credential step, over the fleet.
	await firstApi.getByRole('button', { name: 'Continue with GitHub' }).click();
	const queue = page.getByRole('dialog', { name: /^Set up 1 API for / });
	await expect(queue).toBeVisible();
	await expect(queue.getByTestId('queue-active-pane')).toContainText('GitHub');
	await expect(panel).toHaveCount(0);
	await expect(landing).toHaveCount(0);
	await expect(page).toHaveURL(new RegExp(`agent=${id}`));
	await expect(page.getByRole('tab', { name: /research-bot/ })).toHaveAttribute(
		'aria-selected',
		'true',
	);
});
