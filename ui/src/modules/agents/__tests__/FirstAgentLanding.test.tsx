/**
 * The Agents page with no agents in the org: the self-registration and manual
 * routes, the dashed fleet preview, and the first self-registered agent's
 * arrival, approval (or denial) and first-API choice, all inside the landing,
 * before the real fleet view takes over.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { MotionConfig } from 'framer-motion';
import { useLocation } from 'react-router';
import { page } from 'vitest/browser';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
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
	clearAgentsStore,
	resetAgentsStore,
	seedCredentialBindings,
	seedExtraAgents,
	selfRegisterAgent,
} from '@/modules/agents/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import { agentsKeysForTest } from '@/modules/agents/api/hooks';
import { FIRST_AGENT_POLL_MS } from '@/modules/agents/lib/useFirstAgentLanding';
import {
	catalogPage,
	liveGithubCatalog,
	unsyncedCatalog,
} from '@/modules/agents/mocks/githubCatalog';

function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location-search">{location.search}</div>;
}

function renderPage(opts: { reduced?: boolean } = {}) {
	return renderWithProviders(
		<MotionConfig reducedMotion={opts.reduced ? 'always' : 'never'}>
			<AgentsPage />
			<LocationProbe />
			<Toaster />
		</MotionConfig>,
	);
}

const landing = () => screen.findByTestId('agents-empty-landing');
const status = () => screen.getByTestId('register-status');

describe('Agents page — zero agents', () => {
	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
		clearAgentsStore();
	});

	it('shows both routes in and the dashed fleet preview', async () => {
		const { container } = renderPage();
		await landing();

		expect(
			screen.getByRole('heading', { name: 'Let your agent register itself' }),
		).toBeInTheDocument();
		expect(
			screen.getByRole('heading', { name: 'Prefer to set it up yourself?' }),
		).toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: /Create an agent manually/ }),
		).toBeInTheDocument();
		// Pin the real CLI flag: `jentic register` takes --url, not --base-url (#1204).
		expect(screen.getByTestId('register-command')).toHaveTextContent(
			`$ jentic register --url ${window.location.origin} --name my-first-agent`,
		);
		expect(screen.queryByText(/--base-url/)).toBeNull();
		expect(status()).toHaveTextContent('Listening for new agents…');
		expect(status()).toHaveAttribute('aria-live', 'polite');

		const ghost = screen.getByTestId('ghost-fleet');
		expect(ghost).toHaveAttribute('aria-hidden', 'true');
		expect(within(ghost).getByTestId('ghost-tab')).toHaveTextContent('Waiting for your agent…');

		expect(screen.queryByTestId('agent-dock')).toBeNull();
		expect(screen.getByRole('button', { name: 'Create your first agent' })).toBeInTheDocument();
		// Retried: the header's controls fade in on mount, and a mid-fade sample
		// reads as low contrast.
		await waitFor(() => checkA11y(container), { timeout: 3000 });
	});

	it('the agent name updates the command, and a blank name is flagged', async () => {
		const user = userEvent.setup();
		renderPage();
		await landing();
		const input = screen.getByLabelText('Agent name');

		await user.clear(input);
		await user.type(input, 'research-bot');
		expect(screen.getByTestId('register-command')).toHaveTextContent(/--name research-bot$/);

		// A name with a space is quoted, so the pasted command still parses.
		await user.type(input, ' two');
		expect(screen.getByTestId('register-command')).toHaveTextContent(
			/--name 'research-bot two'$/,
		);
		// Nothing in a name runs when pasted: it is single-quoted, `'` escaped.
		await user.clear(input);
		await user.type(input, "$(id)'s bot");
		expect(screen.getByTestId('register-command')).toHaveTextContent(
			/--name '\$\(id\)'\\''s bot'$/,
		);

		// The create sheet's name rule: blank is an error; the command keeps the default.
		await user.clear(input);
		expect(await screen.findByText('A name is required.')).toBeInTheDocument();
		expect(input).toHaveAttribute('aria-invalid', 'true');
		expect(screen.getByTestId('register-command')).toHaveTextContent(/--name my-first-agent$/);
	});

	it('copies the command with feedback', async () => {
		const user = userEvent.setup();
		renderPage();
		await landing();
		const copy = screen.getByRole('button', { name: 'Copy the register command' });
		await user.click(copy);
		expect(await screen.findByText('Command copied')).toBeInTheDocument();
		expect(await navigator.clipboard.readText()).toBe(
			`jentic register --url ${window.location.origin} --name my-first-agent`,
		);
		expect(copy).toHaveTextContent('Copied!');
	});

	it('"Create an agent manually" and the header button open the same create sheet', async () => {
		const user = userEvent.setup();
		renderPage();
		await landing();

		await user.click(screen.getByRole('button', { name: /Create an agent manually/ }));
		expect(await screen.findByRole('dialog', { name: 'Create agent' })).toBeInTheDocument();
		await user.keyboard('{Escape}');
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Create agent' })).toBeNull(),
		);

		await user.click(screen.getByRole('button', { name: 'Create your first agent' }));
		expect(await screen.findByRole('dialog', { name: 'Create agent' })).toBeInTheDocument();
	});

	/** Registers `name` the way `jentic register` does and waits for the arrival card. */
	async function arrive(
		name: string,
		queryClient: { invalidateQueries: () => Promise<void> },
		over: Parameters<typeof selfRegisterAgent>[1] = {},
	) {
		const id = selfRegisterAgent(name, over);
		// The fallback poll would find it; invalidating is what the stream does.
		await queryClient.invalidateQueries();
		await screen.findByTestId('arrival-card', {}, { timeout: 6000 });
		return id;
	}

	const stepStates = () =>
		within(screen.getByTestId('register-stepper'))
			.getAllByRole('listitem')
			.map((li) => li.getAttribute('data-state'));

	it('while listening, the stepper is on its first step', async () => {
		renderPage();
		await landing();
		expect(stepStates()).toEqual(['current', 'upcoming', 'upcoming']);
		const steps = within(screen.getByTestId('register-stepper')).getAllByRole('listitem');
		expect(steps[0]).toHaveAttribute('aria-current', 'step');
		expect(screen.getByTestId('register-stepper').tagName).toBe('OL');
	});

	it('an arrival swaps the command for the agent, advances the stepper and sends the manual card off', async () => {
		const { container, queryClient } = renderPage();
		await landing();
		expect(screen.getByTestId('manual-card')).toBeInTheDocument();
		const id = await arrive('my-first-agent', queryClient);

		const card = screen.getByTestId('first-agent-card');
		expect(within(card).getByRole('heading', { name: 'my-first-agent' })).toBeInTheDocument();
		expect(within(card).getByText('Pending')).toBeInTheDocument();
		expect(card).toHaveTextContent(/Registered just now/);
		expect(card).toHaveTextContent(
			"It has its own key but can't make calls until you approve it.",
		);
		expect(within(card).getByRole('button', { name: 'Approve my-first-agent' })).toBeEnabled();
		expect(within(card).getByRole('button', { name: 'Deny my-first-agent' })).toBeEnabled();
		// The command view is gone; the steps stay.
		await waitFor(() => expect(screen.queryByTestId('register-command')).toBeNull());
		expect(stepStates()).toEqual(['done', 'done', 'current']);
		expect(within(card).getAllByRole('listitem')[2]).toHaveAttribute('aria-current', 'step');
		expect(status()).toHaveTextContent(
			'my-first-agent just registered · awaiting your approval',
		);

		// A self-registration's facts: how it came in, its keypair, its id — and
		// none of the rows it has no data for.
		const facts = within(screen.getByTestId('agent-facts'));
		expect(facts.getByText('Self-registered from the CLI')).toBeInTheDocument();
		expect(facts.getByText('Its own keypair')).toBeInTheDocument();
		expect(facts.getByText(id)).toBeInTheDocument();
		for (const label of ['Owner', 'Parent agent', 'Scopes', 'Key ID', 'Description'])
			expect(facts.queryByText(label)).toBeNull();

		// The page stays the landing; the manual card has left.
		expect(screen.getByTestId('agents-empty-landing')).toHaveAttribute('data-phase', 'arrived');
		expect(screen.queryByRole('region', { name: 'Awaiting approval' })).toBeNull();
		expect(screen.queryByTestId('agent-dock')).toBeNull();
		await waitFor(() => expect(screen.queryByTestId('manual-card')).toBeNull());
		expect(screen.queryByRole('button', { name: /Create an agent manually/ })).toBeNull();
		expect(screen.getByTestId('ghost-tab')).toHaveAttribute('data-status', 'pending');
		await waitFor(() => checkA11y(container), { timeout: 3000 });
	});

	it('shows the provenance facts the API has for the agent', async () => {
		const { queryClient } = renderPage();
		await landing();
		worker.use(
			http.get('/agents/:id/scopes', () =>
				HttpResponse.json({ scopes: ['toolkits:read', 'executions:write'] }),
			),
		);
		await arrive('owned-bot', queryClient, {
			registered_by: 'usr_admin',
			owner_id: 'usr_admin',
			has_api_key: true,
			description: 'Triage for the support inbox',
		});

		const facts = within(screen.getByTestId('agent-facts'));
		expect(facts.getByText('Owner')).toBeInTheDocument();
		expect(facts.getByText('An API key')).toBeInTheDocument();
		expect(await facts.findByText('Key ID')).toBeInTheDocument();
		expect(await facts.findByText('toolkits:read, executions:write')).toBeInTheDocument();
		expect(facts.getByText('Triage for the support inbox')).toBeInTheDocument();
		expect(facts.queryByText('Self-registered from the CLI')).toBeNull();
		expect(facts.queryByText('Parent agent')).toBeNull();
	});

	it('approving from the card completes the stepper and suggests GitHub', async () => {
		const user = userEvent.setup();
		const { container, queryClient } = renderPage();
		await landing();
		await arrive('my-first-agent', queryClient);

		await user.click(screen.getByRole('button', { name: 'Approve my-first-agent' }));
		const panel = await screen.findByTestId('first-api-panel');
		expect(
			within(panel).getByRole('heading', { name: 'Add GitHub to my-first-agent' }),
		).toBeInTheDocument();
		expect(panel).toHaveTextContent(
			'Let it read issues and repos — you choose exactly what it can do.',
		);
		await waitFor(() =>
			expect(
				within(panel).getByRole('button', { name: 'Continue with GitHub' }),
			).toBeEnabled(),
		);
		expect(within(panel).getByRole('button', { name: 'Add another API' })).toBeInTheDocument();
		expect(within(panel).getByRole('button', { name: 'Skip for now' })).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Approve my-first-agent' })).toBeNull();

		expect(stepStates()).toEqual(['done', 'done', 'done']);
		expect(
			within(screen.getByTestId('arrival-card')).getByText('Active', { selector: 'span' }),
		).toBeInTheDocument();
		expect(status()).toHaveTextContent('my-first-agent is approved');
		expect(screen.getByTestId('ghost-tab')).toHaveAttribute('data-status', 'active');
		expect(screen.queryByTestId('agent-dock')).toBeNull();
		await waitFor(() => checkA11y(container), { timeout: 3000 });
	});

	it('an approval made elsewhere reaches the card through the roster', async () => {
		const { queryClient } = renderPage();
		await landing();
		const id = await arrive('my-first-agent', queryClient);

		// As the Activity rail's or the bell's Approve would: straight to the API.
		const res = await fetch(`/agents/${id}:approve`, { method: 'POST' });
		expect(res.ok).toBe(true);
		await queryClient.invalidateQueries();
		expect(await screen.findByTestId('first-api-panel')).toBeInTheDocument();
		expect(stepStates()).toEqual(['done', 'done', 'done']);
	});

	async function approved(user: ReturnType<typeof userEvent.setup>) {
		const { queryClient } = renderPage();
		await landing();
		const id = await arrive('my-first-agent', queryClient);
		await user.click(screen.getByRole('button', { name: 'Approve my-first-agent' }));
		await screen.findByTestId('first-api-panel');
		return id;
	}

	it('"Continue with GitHub" opens the GitHub credential step directly', async () => {
		const user = userEvent.setup();
		const id = await approved(user);

		const github = screen.getByRole('button', { name: 'Continue with GitHub' });
		await waitFor(() => expect(github).toBeEnabled());
		await user.click(github);

		// The setup queue, on GitHub — never the picker.
		const queue = await screen.findByRole('dialog', { name: 'Set up 1 API' });
		expect(within(queue).getByTestId('queue-active-pane')).toHaveAccessibleName(/GitHub/);
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();

		expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${id}`);
		expect(screen.getByRole('tab', { name: /my-first-agent/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
	});

	it('"Add another API" opens the tray with nothing ticked', async () => {
		const user = userEvent.setup();
		await approved(user);

		await user.click(screen.getByRole('button', { name: 'Add another API' }));
		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		expect(within(tray).getByText('Nothing selected yet.')).toBeInTheDocument();
		expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
	});

	/** A workspace with Slack and NewsAPI imported, and GitHub only in the
	 * (real-shaped) public catalog as `github.com/api.github.com`. */
	const liveWorkspace = http.get('/apis', () =>
		HttpResponse.json({
			data: [
				{
					api: { vendor: 'slack-com', name: 'slack-com', version: '1.7.0' },
					catalog_api_id: 'slack.com',
					display_name: null,
					description: null,
					_links: {},
				},
				{
					api: { vendor: 'newsapi-org', name: 'newsapi-org', version: '1.0.0' },
					catalog_api_id: 'newsapi.org',
					display_name: null,
					description: null,
					_links: {},
				},
			],
			has_more: false,
			next_cursor: null,
		}),
	);

	it('on a live catalog, suggests GitHub REST from the catalog and goes straight to its credential step', async () => {
		worker.use(liveWorkspace, liveGithubCatalog);
		const user = userEvent.setup();
		await approved(user);

		const panel = screen.getByTestId('first-api-panel');
		expect(
			await within(panel).findByRole('heading', { name: 'Add GitHub to my-first-agent' }),
		).toBeInTheDocument();
		const github = within(panel).getByRole('button', { name: 'Continue with GitHub' });
		await waitFor(() => expect(github).toBeEnabled());
		await user.click(github);

		const queue = await screen.findByRole('dialog', { name: 'Set up 1 API' });
		expect(within(queue).getByTestId('queue-active-pane')).toHaveAccessibleName(/GitHub/);
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();
	});

	it('with a catalog but no GitHub in it, the panel offers any API', async () => {
		worker.use(
			liveWorkspace,
			http.get('/catalog', () => HttpResponse.json(catalogPage([]))),
		);
		const user = userEvent.setup();
		await approved(user);

		const panel = screen.getByTestId('first-api-panel');
		await waitFor(() =>
			expect(
				within(panel).getByRole('heading', { name: 'Give my-first-agent its first API' }),
			).toBeInTheDocument(),
		);
		expect(panel).toHaveTextContent('Pick an API it can call, or upload an OpenAPI spec');
		expect(within(panel).queryByRole('button', { name: 'Continue with GitHub' })).toBeNull();
		await user.click(within(panel).getByRole('button', { name: 'Add an API' }));
		expect(await screen.findByRole('dialog', { name: 'Add APIs' })).toBeInTheDocument();
	});

	it('with no catalog synced, the panel says so and still offers any API', async () => {
		worker.use(
			unsyncedCatalog,
			http.get('/apis', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		const user = userEvent.setup();
		await approved(user);

		const panel = screen.getByTestId('first-api-panel');
		await waitFor(() =>
			expect(panel).toHaveTextContent(
				"The public API catalog isn't available on this instance yet.",
			),
		);
		expect(
			within(panel).getByRole('heading', { name: 'Give my-first-agent its first API' }),
		).toBeInTheDocument();
		expect(within(panel).queryByRole('button', { name: 'Continue with GitHub' })).toBeNull();
		await user.click(within(panel).getByRole('button', { name: 'Add an API' }));
		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		expect(within(tray).getByRole('button', { name: /Upload an API/ })).toBeInTheDocument();
	});

	it('"Skip for now" shows the fleet with the agent selected', async () => {
		const user = userEvent.setup();
		const id = await approved(user);

		await user.click(screen.getByRole('button', { name: 'Skip for now' }));
		expect(await screen.findByTestId('agent-dock')).toBeInTheDocument();
		expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${id}`);
		expect(screen.getByRole('tab', { name: /my-first-agent/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		// Active and nothing pending: no approval surfaces, and no tray.
		expect(screen.queryByRole('region', { name: 'Awaiting approval' })).toBeNull();
		expect(screen.queryByTestId('agent-state-banner-pending')).toBeNull();
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();
		expect(screen.getByRole('button', { name: 'Add APIs' })).toBeEnabled();
	});

	it('denying the arrival returns the landing to listening', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await landing();
		await arrive('my-first-agent', queryClient);

		await user.click(screen.getByRole('button', { name: 'Deny my-first-agent' }));
		const dialog = await screen.findByRole('dialog', { name: 'Deny my-first-agent' });
		await user.type(within(dialog).getByLabelText('Reason'), 'Not ours');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() => expect(status()).toHaveTextContent('Listening for new agents…'));
		expect(screen.getByTestId('agents-empty-landing')).toHaveAttribute(
			'data-phase',
			'listening',
		);
		expect(await screen.findByTestId('register-command')).toBeInTheDocument();
		expect(stepStates()).toEqual(['current', 'upcoming', 'upcoming']);
		expect(screen.getByTestId('ghost-tab')).toHaveTextContent('Waiting for your agent…');
		expect(
			await screen.findByRole('button', { name: /Create an agent manually/ }),
		).toBeInTheDocument();
		expect(screen.queryByTestId('agent-dock')).toBeNull();
	});

	it('with reduced motion every state swaps at once', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage({ reduced: true });
		await landing();
		const id = await arrive('quiet-bot', queryClient);
		expect(screen.queryByTestId('manual-card')).toBeNull();
		expect(screen.queryByTestId('register-command')).toBeNull();

		await user.click(screen.getByRole('button', { name: 'Approve quiet-bot' }));
		expect(await screen.findByTestId('first-api-panel')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Approve quiet-bot' })).toBeNull();
		await user.click(screen.getByRole('button', { name: 'Skip for now' }));
		expect(await screen.findByTestId('agent-dock')).toBeInTheDocument();
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${id}`);
	});

	it('an in-session approval moves focus to the card heading, and an exit to the strip tab', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await landing();
		await arrive('my-first-agent', queryClient);
		const card = screen.getByTestId('first-agent-card');
		await waitFor(() =>
			expect(within(card).getByRole('heading', { name: 'my-first-agent' })).toHaveFocus(),
		);

		await user.click(screen.getByRole('button', { name: 'Approve my-first-agent' }));
		await screen.findByTestId('first-api-panel');
		await waitFor(() =>
			expect(within(card).getByRole('heading', { name: 'my-first-agent' })).toHaveFocus(),
		);

		await user.click(screen.getByRole('button', { name: 'Skip for now' }));
		await waitFor(() =>
			expect(screen.getByRole('tab', { name: /my-first-agent/ })).toHaveFocus(),
		);
	});

	it('a deny moves focus back to the listening card heading', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await landing();
		await arrive('my-first-agent', queryClient);

		await user.click(screen.getByRole('button', { name: 'Deny my-first-agent' }));
		const dialog = await screen.findByRole('dialog', { name: 'Deny my-first-agent' });
		await user.type(within(dialog).getByLabelText('Reason'), 'Not ours');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() =>
			expect(
				screen.getByRole('heading', { name: 'Let your agent register itself' }),
			).toHaveFocus(),
		);
	});

	it('a failed approval keeps the arrival, with Approve available again', async () => {
		worker.use(
			http.post('/agents/:id\\:approve', () =>
				HttpResponse.json(
					{ type: 'about:blank', status: 500, title: 'Internal Server Error' },
					{ status: 500 },
				),
			),
		);
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await landing();
		await arrive('my-first-agent', queryClient);

		await user.click(screen.getByRole('button', { name: 'Approve my-first-agent' }));
		expect(await screen.findByText('Failed to approve the agent.')).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Approve my-first-agent' })).toBeEnabled(),
		);
		expect(screen.getByTestId('agents-empty-landing')).toHaveAttribute('data-phase', 'arrived');
		expect(screen.queryByTestId('first-api-panel')).toBeNull();
	});

	it('a deny made elsewhere returns the card to listening', async () => {
		const { queryClient } = renderPage();
		await landing();
		const id = await arrive('my-first-agent', queryClient);

		// As the Activity rail's or the bell's Deny would: straight to the API.
		const res = await fetch(`/agents/${id}:deny`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ reason: 'Not ours' }),
		});
		expect(res.ok).toBe(true);
		await queryClient.invalidateQueries();
		await waitFor(() => expect(status()).toHaveTextContent('Listening for new agents…'));
		expect(await screen.findByTestId('register-command')).toBeInTheDocument();
	});

	it('another pending agent is one link away in the approved state too', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await landing();
		await arrive('my-first-agent', queryClient);
		await user.click(screen.getByRole('button', { name: 'Approve my-first-agent' }));
		await screen.findByTestId('first-api-panel');

		selfRegisterAgent('second-bot');
		await queryClient.invalidateQueries();
		const more = await screen.findByTestId('more-pending');
		expect(more).toHaveTextContent('+1 more waiting for approval');
		await user.click(more);
		expect(
			await screen.findByRole('region', { name: 'Awaiting approval' }),
		).toBeInTheDocument();
	});

	it('polls the roster while it waits, and stops once approved and after the exit', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await landing();
		const intervals = () =>
			queryClient
				.getQueryCache()
				.find({ queryKey: agentsKeysForTest.list('all'), exact: true })
				?.observers.map((o) => o.options.refetchInterval) ?? [];
		await waitFor(() => expect(intervals()).toContain(FIRST_AGENT_POLL_MS));

		await arrive('my-first-agent', queryClient);
		expect(intervals()).toContain(FIRST_AGENT_POLL_MS);
		await user.click(screen.getByRole('button', { name: 'Approve my-first-agent' }));
		await screen.findByTestId('first-api-panel');
		await waitFor(() => expect(intervals()).not.toContain(FIRST_AGENT_POLL_MS));

		await user.click(screen.getByRole('button', { name: 'Skip for now' }));
		expect(await screen.findByTestId('agent-dock')).toBeInTheDocument();
		expect(intervals()).not.toContain(FIRST_AGENT_POLL_MS);
	});

	it('"Continue with GitHub" for an agent that already reaches GitHub says so and shows the fleet', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							api: { vendor: 'github-com', name: 'github-rest', version: '1.0.0' },
							catalog_api_id: 'github.com/api.github.com',
							display_name: null,
							description: null,
							_links: {},
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await landing();
		// Registered with GitHub already bound (from another tab, say), so the
		// pick has nothing left to set up.
		const id = selfRegisterAgent('my-first-agent');
		seedCredentialBindings([
			{
				agent_id: id,
				credential_id: 'cred_github_1',
				serves: [{ api_vendor: 'github-com', api_name: null, api_version: null }],
			},
		]);
		await queryClient.invalidateQueries();
		await user.click(
			await screen.findByRole(
				'button',
				{ name: 'Approve my-first-agent' },
				{ timeout: 6000 },
			),
		);
		const github = await screen.findByRole('button', { name: 'Continue with GitHub' });
		await user.click(github);

		expect(
			await screen.findByText('GitHub is already available to my-first-agent'),
		).toBeInTheDocument();
		expect(await screen.findByTestId('agent-dock')).toBeInTheDocument();
		expect(screen.queryByRole('dialog', { name: /Set up/ })).toBeNull();
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();
	});

	it('"Continue with GitHub" falls back to the tray when the agent\'s bindings fail to load', async () => {
		worker.use(
			http.get('/agents/:id/credentials', () =>
				HttpResponse.json(
					{ type: 'about:blank', status: 500, title: 'Internal Server Error' },
					{ status: 500 },
				),
			),
		);
		const user = userEvent.setup();
		await approved(user);
		const github = screen.getByRole('button', { name: 'Continue with GitHub' });
		await user.click(github);
		expect(await screen.findByRole('dialog', { name: 'Add APIs' })).toBeInTheDocument();
		expect(screen.queryByRole('dialog', { name: /Set up/ })).toBeNull();
	});
});

describe('Agents page — resuming the first run on load', () => {
	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
		clearAgentsStore();
	});

	const phase = () => screen.getByTestId('agents-empty-landing').getAttribute('data-phase');
	const stepStates = () =>
		within(screen.getByTestId('register-stepper'))
			.getAllByRole('listitem')
			.map((li) => li.getAttribute('data-state'));
	const seedActive = (id: string, name: string) =>
		seedExtraAgents([{ id, name, status: 'active', registered_by: 'self' }]);

	it('a single pending agent resumes in its arrival, with no manual card', async () => {
		const id = selfRegisterAgent('my-first-agent');
		renderPage();

		await landing();
		expect(phase()).toBe('arrived');
		const card = screen.getByTestId('arrival-card');
		expect(within(card).getByRole('button', { name: 'Approve my-first-agent' })).toBeEnabled();
		expect(within(card).getByRole('button', { name: 'Deny my-first-agent' })).toBeEnabled();
		expect(within(screen.getByTestId('agent-facts')).getByText(id)).toBeInTheDocument();
		expect(stepStates()).toEqual(['done', 'done', 'current']);
		// Rendered resolved: the manual card never mounts, so nothing slides off.
		expect(screen.queryByTestId('manual-card')).toBeNull();
		expect(screen.queryByTestId('register-command')).toBeNull();
		expect(screen.queryByTestId('more-pending')).toBeNull();
		expect(screen.queryByTestId('agent-strip')).toBeNull();
	});

	it('several pending: the newest resumes, and "+N more" leads to the fleet', async () => {
		const user = userEvent.setup();
		selfRegisterAgent('older-bot', {
			created_at: new Date(Date.now() - 600_000).toISOString(),
		});
		selfRegisterAgent('middle-bot', {
			created_at: new Date(Date.now() - 300_000).toISOString(),
		});
		const newest = selfRegisterAgent('newest-bot');
		renderPage();

		await landing();
		expect(phase()).toBe('arrived');
		expect(
			within(screen.getByTestId('arrival-card')).getByRole('heading', { name: 'newest-bot' }),
		).toBeInTheDocument();
		const more = screen.getByTestId('more-pending');
		expect(more).toHaveTextContent('+2 more waiting for approval');

		await user.click(more);
		expect(await screen.findByTestId('agent-strip')).toBeInTheDocument();
		expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
		expect(screen.getByRole('region', { name: 'Awaiting approval' })).toBeInTheDocument();
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${newest}`);
	});

	it('one active agent with no APIs resumes on the first-API suggestion', async () => {
		seedActive('agnt_first_1', 'my-first-agent');
		renderPage();

		await landing();
		expect(phase()).toBe('approved');
		const panel = screen.getByTestId('first-api-panel');
		expect(
			await within(panel).findByRole('heading', { name: 'Add GitHub to my-first-agent' }),
		).toBeInTheDocument();
		expect(within(panel).getByRole('button', { name: 'Add another API' })).toBeInTheDocument();
		expect(within(panel).getByRole('button', { name: 'Skip for now' })).toBeInTheDocument();
		expect(stepStates()).toEqual(['done', 'done', 'done']);
		expect(screen.queryByTestId('manual-card')).toBeNull();
	});

	for (const exit of ['Skip for now', 'Continue with GitHub', 'Add another API'] as const) {
		it(`after "${exit}", a reload shows the fleet`, async () => {
			const user = userEvent.setup();
			seedActive('agnt_first_1', 'my-first-agent');
			const first = renderPage();
			const button = await screen.findByRole('button', { name: exit });
			await waitFor(() => expect(button).toBeEnabled());
			await user.click(button);
			await waitFor(() => expect(screen.queryByTestId('agents-empty-landing')).toBeNull());
			expect(
				window.localStorage.getItem('j1.agents.firstRun.dismissed.agnt_first_1'),
			).not.toBeNull();
			first.unmount();

			renderPage();
			expect(await screen.findByTestId('agent-strip')).toBeInTheDocument();
			expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
		});
	}

	it('an active agent that already has an API shows the fleet', async () => {
		seedActive('agnt_first_1', 'my-first-agent');
		seedCredentialBindings([{ agent_id: 'agnt_first_1', credential_id: 'cred_github_1' }]);
		renderPage();

		expect(await screen.findByTestId('agent-strip')).toBeInTheDocument();
		expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
	});

	it('one active and one pending agent shows the fleet and its approval banner', async () => {
		seedActive('agnt_first_1', 'my-first-agent');
		selfRegisterAgent('second-bot');
		renderPage();

		expect(await screen.findByTestId('agent-strip')).toBeInTheDocument();
		expect(screen.getByRole('region', { name: 'Awaiting approval' })).toBeInTheDocument();
		expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
	});

	it('a denied agent is history: the next active agent still resumes', async () => {
		seedExtraAgents([{ id: 'agnt_old', name: 'stray', status: 'rejected' }]);
		seedActive('agnt_first_1', 'my-first-agent');
		renderPage();

		await landing();
		expect(phase()).toBe('approved');
	});

	it('only denied or archived agents: the landing resumes listening', async () => {
		seedExtraAgents([
			{ id: 'agnt_old', name: 'stray', status: 'rejected' },
			{ id: 'agnt_gone', name: 'retired', status: 'archived' },
		]);
		renderPage();

		await landing();
		expect(phase()).toBe('listening');
		expect(screen.getByTestId('register-command')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Create your first agent' })).toBeInTheDocument();
		expect(screen.queryByTestId('agent-strip')).toBeNull();
	});

	it('shows only the loading skeleton until the roster and bindings are read', async () => {
		seedActive('agnt_first_1', 'my-first-agent');
		let release!: () => void;
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		let bindingsAsked = false;
		worker.use(
			http.get('/agents/:id/credentials', async () => {
				bindingsAsked = true;
				await gate;
				return undefined;
			}),
		);
		renderPage();

		const loading = await screen.findByText('Loading agents…');
		// The roster is in and the lone agent's bindings are asked for, not
		// answered: no view yet.
		await waitFor(() => expect(bindingsAsked).toBe(true));
		expect(loading).toBeInTheDocument();
		expect(screen.queryByTestId('agent-strip')).toBeNull();
		expect(screen.queryByTestId('agents-empty-landing')).toBeNull();

		release();
		await landing();
		expect(phase()).toBe('approved');
	});
});
