/**
 * The New agent panel over an existing fleet: one sheet, three ways in on the
 * "Connect with" rail; the register routes listen, approve and hand off a first API.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { MotionConfig } from 'framer-motion';
import { useLocation } from 'react-router';
import { page } from 'vitest/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import {
	decideAgentElsewhere,
	resetAgentsStore,
	seedExtraAgents,
	selfRegisterAgent,
} from '@/modules/agents/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import { agentsKeysForTest } from '@/modules/agents/api/hooks';
import { FIRST_AGENT_POLL_MS } from '@/modules/agents/lib/useFirstAgentLanding';
import { isFirstRunDismissed } from '@/modules/agents/lib/firstRun';

function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location-search">{location.search}</div>;
}

function renderPage() {
	return renderWithProviders(
		<MotionConfig reducedMotion="never">
			<AgentsPage />
			<LocationProbe />
			<Toaster />
		</MotionConfig>,
	);
}

type User = ReturnType<typeof userEvent.setup>;
type QueryClientLike = ReturnType<typeof renderPage>['queryClient'];

const panel = () => screen.getByRole('dialog', { name: 'New agent' });
const registerTab = () => within(panel()).getByRole('tab', { name: /Register from the CLI/ });
const mcpTab = () => within(panel()).getByRole('tab', { name: 'Connect over MCP' });
const createTab = () => within(panel()).getByRole('tab', { name: 'Create it here' });
const status = () => within(panel()).getByTestId('register-status');

/** The fleet on screen, then the panel opened on its default "Register from the CLI". */
async function openDefault(user: User) {
	await screen.findByTestId('agent-dock');
	await user.click(screen.getByRole('button', { name: 'New agent' }));
	return screen.findByRole('dialog', { name: 'New agent' });
}

async function openPanel(user: User) {
	const sheet = await openDefault(user);
	await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
	await user.click(createTab());
	await waitFor(() => expect(within(sheet).getByLabelText('Name')).toHaveFocus());
	return sheet;
}

async function openRegister(user: User) {
	const sheet = await openDefault(user);
	expect(registerTab()).toHaveAttribute('aria-selected', 'true');
	await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
	return sheet;
}

/** What `jentic register` leaves behind; invalidating is what the stream does. */
async function register(queryClient: QueryClientLike, ...names: string[]) {
	const ids = names.map((name) => selfRegisterAgent(name));
	await queryClient.invalidateQueries();
	return ids;
}

/** Waits for the arrival in the panel, and for its permissions (Approve waits on them). */
async function arrival() {
	const card = await within(panel()).findByTestId('arrival-card', {}, { timeout: 6000 });
	await waitFor(() =>
		expect(within(panel()).queryByTestId('approve-waits-for-permissions')).toBeNull(),
	);
	return card;
}

function pollIntervals(queryClient: QueryClientLike) {
	return (
		queryClient
			.getQueryCache()
			.find({ queryKey: agentsKeysForTest.list('all'), exact: true })
			?.observers.map((o) => o.options.refetchInterval) ?? []
	);
}

describe('Agents page — the New agent panel over a fleet', () => {
	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
	});

	it('opens on "Register from the CLI", with the rail beside it; "Create it here" waits', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openDefault(user);
		expect(registerTab()).toHaveAttribute('aria-selected', 'true');
		await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
		await user.click(createTab());

		const rail = within(sheet).getByRole('tablist', { name: 'How to add the agent' });
		expect(rail).toHaveAttribute('aria-orientation', 'vertical');
		const tabs = within(rail).getAllByRole('tab');
		['Register from the CLI', 'Connect over MCP', 'Create it here'].forEach((name, i) =>
			expect(tabs[i]).toHaveAccessibleName(name),
		);
		expect(createTab()).toHaveAccessibleDescription('Nothing to install');
		expect(createTab()).toHaveAttribute('aria-selected', 'true');
		expect(within(sheet).getByTestId('new-agent-rail-status-create')).toHaveTextContent(
			'Nothing to install',
		);
		expect(registerTab()).toHaveAttribute('aria-selected', 'false');
		// No tag on either route.
		expect(registerTab()).toHaveAccessibleName('Register from the CLI');
		expect(within(sheet).queryByText('Recommended')).toBeNull();
		await waitFor(() =>
			expect(within(sheet).getByRole('tabpanel')).toHaveAttribute(
				'aria-labelledby',
				createTab().id,
			),
		);
		// The register pane waits, mounted but hidden and out of reach.
		const registerPane = within(sheet).getByTestId('new-agent-panel-register');
		expect(registerPane).toHaveAttribute('data-state', 'inactive');
		expect(registerPane).toHaveAttribute('inert');
		await waitFor(() => expect(registerPane).not.toBeVisible());
		await waitFor(() => expect(within(sheet).getByLabelText('Name')).toHaveFocus());
		await waitFor(() => checkA11y(sheet), { timeout: 3000 });
	});

	it('"Register from the CLI" shows the command, listening', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openRegister(user);

		expect(registerTab()).toHaveAttribute('aria-selected', 'true');
		expect(within(sheet).getByRole('tabpanel')).toHaveAttribute(
			'aria-labelledby',
			registerTab().id,
		);
		expect(
			within(sheet).getByRole('heading', { name: 'Let your agent register itself' }),
		).toBeInTheDocument();
		// Over a fleet the suggestion is "my-agent" (no agent has it).
		expect(within(sheet).getByTestId('register-command')).toHaveTextContent(/--name my-agent$/);
		expect(within(sheet).getByTestId('register-stepper')).toBeInTheDocument();
		expect(within(sheet).getByTestId('cli-install-hint')).toBeInTheDocument();
		expect(status()).toHaveTextContent('Listening for new agents…');
		expect(status()).toHaveAttribute('aria-live', 'polite');
		expect(within(sheet).getByTestId('new-agent-rail-status-register')).toHaveTextContent(
			'Listening…',
		);
		expect(
			within(within(sheet).getByTestId('register-stepper')).getAllByRole('listitem'),
		).toHaveLength(3);
		expect(within(sheet).getByTestId('new-agent-rail-status-mcp')).toHaveTextContent(
			'Cursor · Claude · Codex',
		);
		expect(within(sheet).getByTestId('footer-check-now')).toHaveTextContent('Check now');
		await waitFor(() => checkA11y(sheet), { timeout: 3000 });
	});

	it('"Connect over MCP" shows jentic setup and the MCP wiring, on the same listener', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		const sheet = await openRegister(user);
		await user.click(mcpTab());
		expect(mcpTab()).toHaveAttribute('aria-selected', 'true');
		await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
		expect(
			within(sheet).getByRole('heading', { name: 'Connect your agent over MCP' }),
		).toBeInTheDocument();
		expect(within(sheet).getByTestId('register-command')).toHaveTextContent(
			/^\$ jentic setup --url .* --name my-agent$/,
		);
		expect(within(sheet).getByTestId('mcp-wiring')).toHaveTextContent(
			'jentic mcp --context jentic-my-agent',
		);
		expect(
			within(sheet).getByRole('button', { name: /^Copy the setup command:/ }),
		).toBeInTheDocument();

		await register(queryClient, 'my-agent');
		await arrival();
		expect(within(sheet).getByTestId('new-agent-rail-status-mcp')).toHaveTextContent(
			'my-agent · pending',
		);
		expect(within(sheet).getByTestId('new-agent-rail-status-register')).toHaveTextContent(
			'Needs the jentic CLI',
		);
		expect(within(sheet).queryByTestId('footer-check-now')).toBeNull();
		expect(within(sheet).getAllByRole('button', { name: /^Close$/ })).toHaveLength(2);
		await waitFor(() => checkA11y(sheet), { timeout: 3000 });
	});

	it('copies the command from anywhere on its block', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openRegister(user);
		const block = within(sheet).getByRole('button', { name: /^Copy the register command:/ });
		await user.click(block);
		expect(await navigator.clipboard.readText()).toMatch(
			/^jentic register --url .* --name my-agent$/,
		);
		expect(block).toHaveTextContent('Copied');
		expect(await screen.findByText('Command copied')).toBeInTheDocument();
	});

	it('switches tabs by click and by keyboard', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openPanel(user);

		await user.click(registerTab());
		expect(registerTab()).toHaveAttribute('aria-selected', 'true');
		await waitFor(() => expect(within(sheet).getByTestId('register-command')).toBeVisible());
		await waitFor(() =>
			expect(
				within(sheet).getByRole('button', { name: 'Create', hidden: true }),
			).not.toBeVisible(),
		);

		registerTab().focus();
		await user.keyboard('{ArrowUp}');
		expect(createTab()).toHaveAttribute('aria-selected', 'true');
		// Arrow keys keep focus on the tab list, as the tabs pattern expects.
		expect(createTab()).toHaveFocus();
		expect(within(sheet).getByLabelText('Name')).toBeInTheDocument();
		await waitFor(() =>
			expect(within(sheet).getByTestId('register-command')).not.toBeVisible(),
		);
		expect(within(sheet).getByRole('button', { name: 'Create' })).toBeVisible();
		expect(createTab()).toHaveFocus();
		await user.keyboard('{Home}');
		expect(registerTab()).toHaveAttribute('aria-selected', 'true');
		await user.keyboard('{ArrowDown}');
		expect(mcpTab()).toHaveAttribute('aria-selected', 'true');
		expect(mcpTab()).toHaveFocus();
	});

	it('slides between panes in the direction of travel down the rail, then focuses the new field', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openPanel(user);
		await waitFor(() => expect(within(sheet).getByLabelText('Name')).toHaveFocus());
		const pane = (value: string) => within(sheet).getByTestId(`new-agent-panel-${value}`);
		const offsetX = (value: string) =>
			new DOMMatrix(getComputedStyle(pane(value)).transform).m42;
		const opacity = (value: string) => Number(getComputedStyle(pane(value)).opacity);
		expect(offsetX('register')).toBe(-18);

		await user.click(registerTab());
		// Mid-slide both panes show; the leaving one is already out of reach.
		await waitFor(() => {
			expect(offsetX('register')).toBeLessThan(0);
			expect(offsetX('register')).toBeGreaterThan(-18);
		});
		expect(opacity('create')).toBeGreaterThan(0);
		expect(pane('create')).toHaveAttribute('inert');
		expect(within(sheet).getAllByRole('tabpanel')).toHaveLength(1);
		expect(offsetX('create')).toBeGreaterThan(0);

		await waitFor(() => expect(pane('create')).not.toBeVisible());
		expect(offsetX('create')).toBe(18);
		expect(offsetX('register')).toBe(0);
		await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());

		await user.click(createTab());
		await waitFor(() => {
			expect(offsetX('create')).toBeGreaterThan(0);
			expect(offsetX('create')).toBeLessThan(18);
		});
		expect(offsetX('register')).toBeLessThan(0);
		await waitFor(() => expect(pane('register')).not.toBeVisible());
		await waitFor(() => expect(within(sheet).getByLabelText('Name')).toHaveFocus());
	});

	it('with reduced motion, switches tabs instantly and focuses the new field', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<MotionConfig reducedMotion="always">
				<AgentsPage />
			</MotionConfig>,
		);
		const sheet = await openDefault(user);
		await user.click(createTab());
		await waitFor(() => expect(within(sheet).getByLabelText('Name')).toHaveFocus());
		await user.click(registerTab());
		const create = within(sheet).getByTestId('new-agent-panel-create');
		await waitFor(() => expect(create).not.toBeVisible(), { timeout: 100 });
		expect(within(sheet).getByTestId('new-agent-panel-register')).toBeVisible();
		await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
	});

	it('ignores agents that were pending before it opened', async () => {
		// Registered a minute before the panel: the fleet's banner lists it.
		seedExtraAgents([
			{
				id: 'agnt_earlier',
				name: 'my-first-agent',
				status: 'pending',
				created_at: new Date(Date.now() - 60_000).toISOString(),
			},
		]);
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await openRegister(user);
		await queryClient.invalidateQueries();

		// The seed's own pending agents and the earlier one: none is an arrival.
		await new Promise((r) => setTimeout(r, 300));
		expect(within(panel()).queryByTestId('arrival-card')).toBeNull();
		expect(status()).toHaveTextContent('Listening for new agents…');

		await register(queryClient, 'my-agent');
		const card = await arrival();
		expect(within(card).getByRole('heading', { name: 'my-agent' })).toBeInTheDocument();
		// Seconds old, not the minute-old earlier agent. The age is wall-clock, so
		// a loaded run can read "1s ago" rather than "just now".
		expect(card).toHaveTextContent(/Registered (just now|\d+s ago)/);
		// Nor are the older ones counted as waiting beside it.
		expect(within(card).queryByTestId('more-pending')).toBeNull();
		expect(within(card).queryByTestId('arrival-warnings')).toBeNull();
		expect(status()).toHaveTextContent('my-agent just registered · awaiting your approval');
	});

	it('of several arrivals, picks the one carrying the typed name and counts the rest', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		const sheet = await openRegister(user);
		const input = within(sheet).getByLabelText('Agent name');
		await user.clear(input);
		await user.type(input, 'research-bot');

		// The newer one carries another name.
		await register(queryClient, 'research-bot', 'other-bot');
		const card = await arrival();
		expect(within(card).getByRole('heading', { name: 'research-bot' })).toBeInTheDocument();
		expect(within(card).getByTestId('arrival-others-warning')).toBeInTheDocument();
		expect(within(card).queryByTestId('arrival-name-warning')).toBeNull();
		expect(within(card).getByTestId('more-pending')).toHaveTextContent(
			'+1 more waiting for approval',
		);
	});

	it('flags an arrival whose name differs from the command', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await openRegister(user);
		await register(queryClient, 'someone-else');
		const card = await arrival();
		expect(within(card).getByTestId('arrival-name-warning')).toHaveTextContent(
			'It registered as someone-else, not my-agent',
		);
	});

	it('an approval made elsewhere while the panel watches hands off to the tray too', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await openRegister(user);
		const [id] = await register(queryClient, 'my-first-agent');
		await arrival();
		let stepDrawn = false;
		const observer = new MutationObserver(() => {
			if (document.querySelector('[data-testid="first-api-panel"]')) stepDrawn = true;
		});
		observer.observe(document.body, { childList: true, subtree: true });

		// The bell, or a second admin: approved with no click in this panel.
		decideAgentElsewhere(id, 'approve');
		await queryClient.invalidateQueries();

		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		await waitFor(() =>
			expect(
				within(tray).getByText(
					(_, el) =>
						el?.tagName === 'P' &&
						/^Pick what my-first-agent should be able to call/.test(
							el.textContent ?? '',
						),
				),
			).toBeVisible(),
		);
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
		observer.disconnect();
		// The GitHub step is the empty org's first-agent offer, never this.
		expect(stepDrawn).toBe(false);
	});

	it('approve over a fleet hands the sheet to the Add-APIs tray, with no GitHub step', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await openRegister(user);
		const [id] = await register(queryClient, 'my-first-agent');
		await arrival();
		let stepDrawn = false;
		const observer = new MutationObserver(() => {
			if (document.querySelector('[data-testid="first-api-panel"]')) stepDrawn = true;
		});
		observer.observe(document.body, { childList: true, subtree: true });
		await user.click(within(panel()).getByRole('button', { name: 'Approve my-first-agent' }));

		// The org has agents already: GitHub is the first agent's suggestion only.
		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		await waitFor(() =>
			expect(
				within(tray).getByText(
					(_, el) =>
						el?.tagName === 'P' &&
						/^Pick what my-first-agent should be able to call/.test(
							el.textContent ?? '',
						),
				),
			).toBeVisible(),
		);
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
		observer.disconnect();
		expect(stepDrawn).toBe(false);
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${id}`);

		const footer = within(tray.querySelector('footer') as HTMLElement);
		expect(footer.queryByRole('button', { name: 'Cancel' })).toBeNull();
		expect(within(tray).getAllByRole('button', { name: 'Skip for now' })).toHaveLength(1);
		await user.click(footer.getByRole('button', { name: 'Skip for now' }));
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull());
		expect(screen.getByRole('tab', { name: /my-first-agent/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(isFirstRunDismissed(id)).toBe(true);
	});

	it('denying the arrival returns to listening, keeping the typed name', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		const sheet = await openRegister(user);
		const input = within(sheet).getByLabelText('Agent name');
		await user.clear(input);
		await user.type(input, 'research-bot');
		await register(queryClient, 'research-bot');
		await arrival();

		await user.click(within(sheet).getByRole('button', { name: 'Deny research-bot' }));
		const dialog = await screen.findByRole('dialog', { name: 'Deny research-bot' });
		await user.type(within(dialog).getByLabelText('Reason'), 'Not ours');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() => expect(status()).toHaveTextContent('Listening for new agents…'));
		expect(within(panel()).getByLabelText('Agent name')).toHaveValue('research-bot');
		expect(within(panel()).getByTestId('register-command')).toHaveTextContent(
			/--name research-bot$/,
		);
		// The panel stays open, on its register tab.
		expect(registerTab()).toHaveAttribute('aria-selected', 'true');
	});

	it('a deny with another agent waiting moves focus to the next arrival heading', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		const sheet = await openRegister(user);
		await register(queryClient, 'research-bot');
		await arrival();
		await register(queryClient, 'second-bot');
		await within(sheet).findByTestId('more-pending');

		await user.click(within(sheet).getByRole('button', { name: 'Deny research-bot' }));
		const dialog = await screen.findByRole('dialog', { name: 'Deny research-bot' });
		await user.type(within(dialog).getByLabelText('Reason'), 'Not ours');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() =>
			expect(within(panel()).getByRole('heading', { name: 'second-bot' })).toHaveFocus(),
		);
	});

	it('polls only while open on the register tab', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await screen.findByTestId('agent-dock');
		expect(pollIntervals(queryClient)).not.toContain(FIRST_AGENT_POLL_MS);

		// Open on the CLI route: it polls; on "Create it here" it stops.
		await openDefault(user);
		await waitFor(() => expect(pollIntervals(queryClient)).toContain(FIRST_AGENT_POLL_MS));
		await user.click(createTab());
		await waitFor(() => expect(pollIntervals(queryClient)).not.toContain(FIRST_AGENT_POLL_MS));
		await user.click(registerTab());
		await waitFor(() => expect(pollIntervals(queryClient)).toContain(FIRST_AGENT_POLL_MS));

		await user.click(within(panel()).getByRole('button', { name: 'Close' }));
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
		expect(pollIntervals(queryClient)).not.toContain(FIRST_AGENT_POLL_MS);
	});

	it('closing mid-flow leaves the arrival pending; reopening picks it up, and "Register another agent" starts fresh', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		const sheet = await openRegister(user);
		const input = within(sheet).getByLabelText('Agent name');
		await user.clear(input);
		await user.type(input, 'research-bot');
		await register(queryClient, 'research-bot');
		await arrival();

		await user.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
		// Still pending in the fleet, counted by its approval banner (two seeded
		// pending agents, and this one).
		expect(await screen.findByRole('tab', { name: /research-bot/ })).toBeInTheDocument();
		const banner = await screen.findByRole('region', { name: /Awaiting approval/i });
		await waitFor(() => expect(banner).toHaveTextContent('and 2 more waiting'));

		// Reopened: the agent it was finishing, still waiting, is back — under
		// the name it registered with, so nothing warns it is someone else's.
		await openDefault(user);
		const card = await arrival();
		expect(card).toHaveTextContent('research-bot');
		expect(within(card).queryByTestId('arrival-name-warning')).toBeNull();
		expect(
			within(panel()).getByRole('button', { name: 'Approve research-bot' }),
		).toBeInTheDocument();

		// Register another agent: the command again, on a fresh suggestion.
		await user.click(within(panel()).getByTestId('footer-register-another'));
		await waitFor(() => expect(within(panel()).queryByTestId('arrival-card')).toBeNull());
		expect(status()).toHaveTextContent('Listening for new agents…');
		expect(within(panel()).getByLabelText('Agent name')).toHaveValue('my-agent');
		// research-bot is only set aside: still pending in the fleet.
		expect(screen.getByRole('tab', { name: /research-bot/ })).toBeInTheDocument();
	});

	it('an arrival on the suggested name is picked up on reopen without a "not your name" warning', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		const sheet = await openRegister(user);
		// Nothing typed: the command runs on the suggestion.
		expect(within(sheet).getByLabelText('Agent name')).toHaveValue('my-agent');
		await register(queryClient, 'my-agent');
		await arrival();
		await user.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());

		// Closed, the roster now holds "my-agent" — the suggestion must not move
		// to "my-agent-2" under the agent the panel is still finishing.
		await queryClient.invalidateQueries();
		await openDefault(user);
		const card = await arrival();
		expect(card).toHaveTextContent('my-agent');
		expect(within(card).queryByTestId('arrival-name-warning')).toBeNull();
	});

	it('an agent approved or denied while the panel was closed is not picked up again', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		for (const [name, decision] of [
			['approved-meanwhile', 'approve'],
			['denied-meanwhile', 'deny'],
		] as const) {
			await openRegister(user);
			const [id] = await register(queryClient, name);
			await arrival();
			await user.keyboard('{Escape}');
			await waitFor(() =>
				expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull(),
			);

			decideAgentElsewhere(id, decision);
			await queryClient.invalidateQueries();
			const reopened = await openDefault(user);
			await waitFor(() => expect(status()).toHaveTextContent('Listening for new agents…'));
			expect(within(reopened).queryByTestId('arrival-card')).toBeNull();
			// Not handed to the tray either: that approval was not this opening's.
			expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();
			await user.keyboard('{Escape}');
			await waitFor(() =>
				expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull(),
			);
		}
	});

	it('an arrival named in a panel closed mid-flow is the operator’s: no "not your name" warning', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		const sheet = await openRegister(user);
		const input = within(sheet).getByLabelText('Agent name');
		await user.clear(input);
		await user.type(input, 'panel-bot');
		await user.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());

		// Reopened on the suggestion ("my-agent"), the agent registered under the
		// name typed before is still recognised as the operator's own.
		const reopened = await openDefault(user);
		expect(within(reopened).getByLabelText('Agent name')).toHaveValue('my-agent');
		await register(queryClient, 'panel-bot');
		const card = await arrival();
		expect(card).toHaveTextContent('panel-bot');
		expect(within(card).queryByTestId('arrival-name-warning')).toBeNull();
	});

	it('each opening starts on "Register from the CLI", and "n" opens it too', async () => {
		const user = userEvent.setup();
		renderPage();
		await openPanel(user);
		await user.click(within(panel()).getByRole('button', { name: 'Close' }));
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
		// Focus back on the trigger, as a closed sheet leaves it.
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'New agent' })).toHaveFocus(),
		);

		await user.keyboard('n');
		const sheet = await screen.findByRole('dialog', { name: 'New agent' });
		expect(registerTab()).toHaveAttribute('aria-selected', 'true');
		await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
	});

	it('never suggests a name another agent has, archived or rejected ones included', async () => {
		seedExtraAgents([
			{ id: 'agnt_old_1', name: 'my-agent', status: 'archived' },
			{ id: 'agnt_old_2', name: 'My-Agent-2', status: 'rejected' },
		]);
		const user = userEvent.setup();
		renderPage();
		const sheet = await openRegister(user);
		await waitFor(() =>
			expect(within(sheet).getByLabelText('Agent name')).toHaveValue('my-agent-3'),
		);
		expect(within(sheet).getByTestId('register-command')).toHaveTextContent(
			/--name my-agent-3$/,
		);
		expect(within(sheet).queryByTestId('agent-name-duplicate')).toBeNull();
	});

	it('hints, without blocking, when the typed name is another agent’s', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openRegister(user);
		const input = within(sheet).getByLabelText('Agent name');
		await user.clear(input);
		// Lowercase, as the CLI takes it; the duplicate check is case-insensitive anyway.
		await user.type(input, 'support-agent');
		const hint = within(sheet).getByTestId('agent-name-duplicate');
		expect(hint).toHaveTextContent(
			'An agent named support-agent already exists — pick a different name so you can tell them apart.',
		);
		expect(input).toHaveAccessibleDescription(hint.textContent ?? '');
		expect(input).not.toHaveAttribute('aria-invalid');
		// The command still carries it: the backend accepts a duplicate.
		expect(within(sheet).getByTestId('register-command')).toHaveTextContent(
			/--name support-agent$/,
		);
		await user.type(input, '-2');
		expect(within(sheet).queryByTestId('agent-name-duplicate')).toBeNull();

		// "Create here" hints the same way.
		await user.click(createTab());
		const name = within(sheet).getByLabelText('Name');
		await waitFor(() => expect(name).toHaveFocus());
		await user.type(name, 'legacy-scraper');
		expect(within(sheet).getByTestId('agent-name-duplicate')).toHaveTextContent(
			'An agent named legacy-scraper already exists',
		);
		expect(within(sheet).getByRole('button', { name: 'Create' })).toBeEnabled();
		await user.clear(name);
		expect(within(sheet).queryByTestId('agent-name-duplicate')).toBeNull();
	});

	it('refuses a name the CLI refuses, and keeps the command on one that runs', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openRegister(user);
		const field = within(sheet).getByLabelText('Agent name');
		const command = within(sheet).getByTestId('register-command');
		await waitFor(() => expect(command).toHaveTextContent('--name my-agent'));
		await user.clear(field);
		await user.type(field, 'My Agent!');
		// `register --name` takes `^[a-z0-9][a-z0-9-]{0,63}$`; the field says so and
		// offers the slug, while the command keeps a name that runs.
		expect(field).toBeInvalid();
		expect(within(sheet).getByRole('alert')).toHaveTextContent(
			'The CLI takes lowercase letters, digits and hyphens only, up to 64 — try my-agent.',
		);
		expect(command).toHaveTextContent('--name my-agent');
		expect(command).not.toHaveTextContent('My Agent!');
		await user.clear(field);
		await user.type(field, 'repro-bot');
		expect(field).not.toBeInvalid();
		expect(command).toHaveTextContent('--name repro-bot');
		// 64 is the most the field takes.
		expect(field).toHaveAttribute('maxlength', '64');
	});
});
