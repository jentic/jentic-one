/**
 * The New agent panel over an existing fleet: one sheet, two routes in as tabs
 * — "Create here" (first, and where every opening starts) and "Register from
 * the CLI". The register tab listens for an agent that registers after the panel
 * opened, then approves it and hands it its first API, closing onto the fleet
 * with it selected.
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
	resetAgentsStore,
	seedExtraAgents,
	selfRegisterAgent,
} from '@/modules/agents/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import { agentsKeysForTest } from '@/modules/agents/api/hooks';
import { FIRST_AGENT_POLL_MS } from '@/modules/agents/lib/useFirstAgentLanding';

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
const createTab = () => within(panel()).getByRole('tab', { name: 'Create here' });
const status = () => within(panel()).getByTestId('register-status');

/** The fleet on screen, then the panel opened from the header. */
async function openPanel(user: User) {
	await screen.findByTestId('agent-dock');
	await user.click(screen.getByRole('button', { name: 'New agent' }));
	return screen.findByRole('dialog', { name: 'New agent' });
}

/** The panel opened, then switched to "Register from the CLI" once it settles. */
async function openRegister(user: User) {
	const sheet = await openPanel(user);
	await user.click(registerTab());
	await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
	return sheet;
}

/** What `jentic register` leaves behind; invalidating is what the stream does. */
async function register(queryClient: QueryClientLike, ...names: string[]) {
	const ids = names.map((name) => selfRegisterAgent(name));
	await queryClient.invalidateQueries();
	return ids;
}

/** Waits for the arrival in the panel, and for its scopes (Approve waits on them). */
async function arrival() {
	const card = await within(panel()).findByTestId('arrival-card', {}, { timeout: 6000 });
	await waitFor(() =>
		expect(within(panel()).queryByTestId('approve-waits-for-scopes')).toBeNull(),
	);
	return card;
}

async function approveArrival(user: User, name: string) {
	await arrival();
	await user.click(within(panel()).getByRole('button', { name: `Approve ${name}` }));
	return within(panel()).findByTestId('first-api-panel');
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

	it('opens on "Create here", the first tab, with its name focused', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openPanel(user);

		const tabs = within(sheet).getAllByRole('tab');
		expect(tabs.map((t) => t.textContent)).toEqual(['Create here', 'Register from the CLI']);
		expect(createTab()).toHaveAttribute('aria-selected', 'true');
		expect(registerTab()).toHaveAttribute('aria-selected', 'false');
		// No tag on either route.
		expect(registerTab()).toHaveAccessibleName('Register from the CLI');
		expect(within(sheet).queryByText('Recommended')).toBeNull();
		expect(within(sheet).getByRole('tabpanel')).toHaveAttribute(
			'aria-labelledby',
			createTab().id,
		);
		// The register pane waits, mounted but hidden and out of reach.
		const registerPane = within(sheet).getByTestId('new-agent-panel-register');
		expect(registerPane).toHaveAttribute('data-state', 'inactive');
		expect(registerPane).toHaveAttribute('inert');
		expect(registerPane).not.toBeVisible();
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
		await waitFor(() => checkA11y(sheet), { timeout: 3000 });
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
				within(sheet).getByRole('button', { name: 'Create and add APIs', hidden: true }),
			).not.toBeVisible(),
		);

		registerTab().focus();
		await user.keyboard('{ArrowLeft}');
		expect(createTab()).toHaveAttribute('aria-selected', 'true');
		// Arrow keys keep focus on the tab list, as the tabs pattern expects.
		expect(createTab()).toHaveFocus();
		expect(within(sheet).getByLabelText('Name')).toBeInTheDocument();
		await waitFor(() =>
			expect(within(sheet).getByTestId('register-command')).not.toBeVisible(),
		);
		expect(within(sheet).getByRole('button', { name: 'Create and add APIs' })).toBeVisible();
		expect(createTab()).toHaveFocus();
		await user.keyboard('{End}');
		expect(registerTab()).toHaveAttribute('aria-selected', 'true');
	});

	it('splits the tab bar in equal halves', async () => {
		const user = userEvent.setup();
		renderPage();
		await openPanel(user);
		const [create, register] = [createTab(), registerTab()];
		expect(create).toHaveClass('flex-1', 'basis-0', 'justify-center');
		expect(register).toHaveClass('flex-1', 'basis-0', 'justify-center');
		await waitFor(() => {
			const a = create.getBoundingClientRect().width;
			expect(a).toBeGreaterThan(0);
			expect(Math.abs(a - register.getBoundingClientRect().width)).toBeLessThan(1);
		});
	});

	it('slides between tabs in the direction of travel, then focuses the new tab’s field', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openPanel(user);
		await waitFor(() => expect(within(sheet).getByLabelText('Name')).toHaveFocus());
		const pane = (value: string) => within(sheet).getByTestId(`new-agent-panel-${value}`);
		const offsetX = (value: string) =>
			new DOMMatrix(getComputedStyle(pane(value)).transform).m41;
		const opacity = (value: string) => Number(getComputedStyle(pane(value)).opacity);
		// Both panes stay mounted: the register pane waits to the right.
		expect(offsetX('register')).toBe(32);

		await user.click(registerTab());
		// Mid-slide both panes show; the leaving one is already out of reach.
		await waitFor(() => {
			expect(offsetX('register')).toBeGreaterThan(0);
			expect(offsetX('register')).toBeLessThan(32);
		});
		expect(opacity('create')).toBeGreaterThan(0);
		expect(pane('create')).toHaveAttribute('inert');
		expect(within(sheet).getAllByRole('tabpanel')).toHaveLength(1);
		// Toward the later tab: the old pane leaves to the left.
		expect(offsetX('create')).toBeLessThan(0);

		await waitFor(() => expect(pane('create')).not.toBeVisible());
		expect(offsetX('create')).toBe(-32);
		expect(offsetX('register')).toBe(0);
		await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());

		await user.click(createTab());
		// Back: the create pane enters from the left, the register one leaves right.
		await waitFor(() => {
			expect(offsetX('create')).toBeLessThan(0);
			expect(offsetX('create')).toBeGreaterThan(-32);
		});
		expect(offsetX('register')).toBeGreaterThan(0);
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
		const sheet = await openPanel(user);
		await user.click(registerTab());
		const create = within(sheet).getByTestId('new-agent-panel-create');
		await waitFor(() => expect(create).not.toBeVisible(), { timeout: 100 });
		expect(within(sheet).getByTestId('new-agent-panel-register')).toBeVisible();
		await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
	});

	it('"Create here" creates the agent and flows into its APIs as before', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openPanel(user);
		await user.type(within(sheet).getByLabelText('Name'), 'hand-made');
		await user.click(within(sheet).getByRole('button', { name: 'Create and add APIs' }));

		expect(await screen.findByText('Agent created')).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.getByRole('tab', { name: /hand-made/ })).toHaveAttribute(
				'aria-selected',
				'true',
			),
		);
		expect(await screen.findByRole('dialog', { name: 'Add APIs' })).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
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
		expect(card).toHaveTextContent(/Registered just now/);
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

	it('approve → first API → "Continue with GitHub" opens its credential step over the fleet', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await openRegister(user);
		const [id] = await register(queryClient, 'my-first-agent');
		const firstApi = await approveArrival(user, 'my-first-agent');

		const steps = within(within(panel()).getByTestId('register-stepper')).getAllByRole(
			'listitem',
		);
		expect(steps.map((li) => li.getAttribute('data-state'))).toEqual([
			'done',
			'done',
			'done',
			'current',
		]);
		expect(status()).toHaveTextContent('my-first-agent is approved');
		const github = await within(firstApi).findByRole('button', {
			name: 'Continue with GitHub',
		});
		await waitFor(() => expect(github).toBeEnabled());
		await user.click(github);

		const queue = await screen.findByRole('dialog', { name: 'Set up 1 API' });
		expect(within(queue).getByTestId('queue-active-pane')).toHaveAccessibleName(/GitHub/);
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${id}`);
		expect(screen.getByRole('tab', { name: /my-first-agent/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
	});

	it('"Skip for now" closes onto the fleet with the agent selected', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await openRegister(user);
		const [id] = await register(queryClient, 'my-first-agent');
		const firstApi = await approveArrival(user, 'my-first-agent');

		await user.click(within(firstApi).getByRole('button', { name: 'Skip for now' }));
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${id}`);
		await waitFor(() =>
			expect(screen.getByRole('tab', { name: /my-first-agent/ })).toHaveAttribute(
				'aria-selected',
				'true',
			),
		);
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();
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

	it('polls only while open on the register tab', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await screen.findByTestId('agent-dock');
		expect(pollIntervals(queryClient)).not.toContain(FIRST_AGENT_POLL_MS);

		// Open on "Create here": no poll.
		await openPanel(user);
		await new Promise((r) => setTimeout(r, 100));
		expect(pollIntervals(queryClient)).not.toContain(FIRST_AGENT_POLL_MS);
		await user.click(registerTab());
		await waitFor(() => expect(pollIntervals(queryClient)).toContain(FIRST_AGENT_POLL_MS));
		await user.click(createTab());
		await waitFor(() => expect(pollIntervals(queryClient)).not.toContain(FIRST_AGENT_POLL_MS));
		await user.click(registerTab());
		await waitFor(() => expect(pollIntervals(queryClient)).toContain(FIRST_AGENT_POLL_MS));

		await user.click(within(panel()).getByRole('button', { name: 'Close' }));
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
		expect(pollIntervals(queryClient)).not.toContain(FIRST_AGENT_POLL_MS);
	});

	it('closing mid-flow leaves the arrival pending in the fleet, and reopening starts fresh', async () => {
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

		const reopened = await openPanel(user);
		expect(createTab()).toHaveAttribute('aria-selected', 'true');
		await user.click(registerTab());
		expect(within(reopened).queryByTestId('arrival-card')).toBeNull();
		expect(status()).toHaveTextContent('Listening for new agents…');
		expect(within(reopened).getByLabelText('Agent name')).toHaveValue('my-agent');
	});

	it('each opening starts on "Create here", and "n" opens it too', async () => {
		const user = userEvent.setup();
		renderPage();
		await openRegister(user);
		await user.click(within(panel()).getByRole('button', { name: 'Close' }));
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull());
		// Focus back on the trigger, as a closed sheet leaves it.
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'New agent' })).toHaveFocus(),
		);

		await user.keyboard('n');
		const sheet = await screen.findByRole('dialog', { name: 'New agent' });
		expect(createTab()).toHaveAttribute('aria-selected', 'true');
		await waitFor(() => expect(within(sheet).getByLabelText('Name')).toHaveFocus());
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
		await user.type(input, 'Support-Agent');
		const hint = within(sheet).getByTestId('agent-name-duplicate');
		expect(hint).toHaveTextContent(
			'An agent named support-agent already exists — pick a different name so you can tell them apart.',
		);
		expect(input).toHaveAccessibleDescription(hint.textContent ?? '');
		expect(input).not.toHaveAttribute('aria-invalid');
		// The command still carries it: the backend accepts a duplicate.
		expect(within(sheet).getByTestId('register-command')).toHaveTextContent(
			/--name Support-Agent$/,
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
		expect(within(sheet).getByRole('button', { name: 'Create and add APIs' })).toBeEnabled();
		await user.clear(name);
		expect(within(sheet).queryByTestId('agent-name-duplicate')).toBeNull();
	});
});
