import { useState } from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page, userEvent as browserUser } from 'vitest/browser';
import { useLocation, MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
	render,
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import { AgentsTour } from '@/modules/agents/components/landing/AgentsTour';
import type { LandingActions } from '@/modules/agents/components/landing/actions';
import type { AxeResults } from 'axe-core';
import { SETUP_STEPS } from '@/modules/agents/components/landing/act2/script';
import { WHY_SCRIPT, WHY_TASK } from '@/modules/agents/components/landing/act1/script';
import { ENV_SECRETS, STORY_APIS } from '@/modules/agents/components/landing/data/demoFixtures';

function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderPage(route = '/agents') {
	return renderWithProviders(
		<>
			<AgentsPage />
			<LocationProbe />
		</>,
		{ route },
	);
}

/** The empty fleet's first-run setup checklist. */
function emptyWorkspace() {
	return screen.findByRole('region', { name: 'Set up your workspace' });
}

function emptyFleet() {
	worker.use(
		http.get('/agents', () =>
			HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
		),
	);
}

/**
 * The tour as its owner mounts it: open, closed by its own `onClose`, and
 * reopenable. A stand-in top-bar bell sits behind it, as in the shell.
 */
function TourHarness({
	actions,
	calls,
	reducedMotion,
}: {
	actions: LandingActions;
	calls: string[];
	reducedMotion: boolean;
}) {
	const [open, setOpen] = useState(true);
	return (
		<>
			<button type="button" onClick={() => setOpen(true)}>
				Reopen the tour
			</button>
			<button
				type="button"
				aria-haspopup="dialog"
				aria-label="Notifications, nothing new"
				onClick={() => calls.push('bell')}
			/>
			<AgentsTour
				open={open}
				onClose={() => {
					calls.push('close');
					setOpen(false);
				}}
				actions={actions}
				reducedMotion={reducedMotion}
			/>
		</>
	);
}

function renderLanding(
	actions: Partial<LandingActions> = {},
	{ reducedMotion = true }: { reducedMotion?: boolean } = {},
) {
	const calls: string[] = [];
	const full: LandingActions = {
		onCreateAgent: () => calls.push('create'),
		onAddApis: () => calls.push('add-apis'),
		onOpenSurface: (surface) => calls.push(`surface:${surface}`),
		agentName: 'support-agent',
		activityHref: '/monitor?show=calls&actor_id=agnt_active_1&actor_type=agent',
		...actions,
	};
	const client = new QueryClient();
	const utils = render(
		<QueryClientProvider client={client}>
			<MemoryRouter>
				<TourHarness actions={full} calls={calls} reducedMotion={reducedMotion} />
			</MemoryRouter>
		</QueryClientProvider>,
	);
	return { ...utils, calls };
}

/**
 * The page header's action buttons, in document order: every button from the
 * create button through the help trigger (the header's first and last).
 */
function headerActions(create: string) {
	const buttons = screen.getAllByRole('button');
	const first = buttons.indexOf(screen.getByRole('button', { name: create }));
	const last = buttons.indexOf(screen.getByTestId('page-help-trigger'));
	return buttons
		.slice(first, last + 1)
		.map((b) => b.getAttribute('aria-label') ?? b.textContent?.trim());
}

async function openTour(user: ReturnType<typeof userEvent.setup>) {
	await user.click(screen.getByRole('button', { name: /^Show the tour/ }));
	return screen.findByRole('dialog', { name: 'The Jentic One tour' });
}

/** Jump the Set-it-up tour to one step (the step list seeks). */
async function openStep(user: ReturnType<typeof userEvent.setup>, title: RegExp) {
	await user.click(screen.getByRole('tab', { name: 'Set it up' }));
	await user.click(
		within(await screen.findByRole('list', { name: 'Setup steps' })).getByRole('button', {
			name: title,
		}),
	);
	return within(screen.getByTestId('setup-cta'));
}

beforeEach(async () => {
	await page.viewport(1440, 900);
	setToken('test-token');
	resetAgentsStore();
	window.sessionStorage.clear();
});

describe('Agents landing — the empty fleet and the tour button', () => {
	it('with no agents the page shows the setup checklist, not the tour', async () => {
		emptyFleet();
		const { container } = renderPage();
		expect(await emptyWorkspace()).toBeInTheDocument();
		expect(screen.queryByTestId('agents-tour')).toBeNull();
		expect(screen.queryByTestId('agent-dock')).not.toBeInTheDocument();
		expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
		await checkA11y(container);
	});

	it.each([
		['no agents', true, 'New agent', 'Show the tour: see how Jentic One works'],
		['a fleet', false, 'New agent', 'Show the tour'],
	])(
		'with %s the header reads: create, Credentials, Show the tour, help',
		async (_, empty, create, tourName) => {
			if (empty) emptyFleet();
			renderPage();
			await screen.findByRole('button', { name: create });
			await waitFor(() =>
				expect(headerActions(create)).toEqual([
					create,
					'Credentials',
					tourName,
					expect.stringMatching(/help/i),
				]),
			);
			// The filter comes first, before the buttons.
			const filter = screen.getByRole('searchbox', { name: 'Filter agents' });
			expect(
				filter.compareDocumentPosition(screen.getByRole('button', { name: create })) &
					Node.DOCUMENT_POSITION_FOLLOWING,
			).toBeTruthy();
			// Exactly one way into the tour.
			expect(screen.getAllByRole('button', { name: /Show the tour/ })).toHaveLength(1);
			expect(screen.queryByRole('link', { name: /Show the tour/ })).toBeNull();
		},
	);

	it('with no agents the tour button carries an icon and the "Show the tour" label', async () => {
		emptyFleet();
		renderPage();
		await emptyWorkspace();
		const labelled = screen.getByTestId('tour-trigger');
		await waitFor(() => expect(labelled).toHaveTextContent('Show the tour'));
		expect(labelled.querySelector('svg')).toBeInTheDocument();
		expect(labelled.className).not.toBe(screen.getByTestId('page-help-trigger').className);
	});

	it('with a fleet the tour button is an icon like help, with a "Show the tour" tooltip', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agent-dock');
		const icon = screen.getByRole('button', { name: 'Show the tour' });
		expect(icon).toHaveAttribute('aria-label', 'Show the tour');
		expect(icon).toHaveTextContent('');
		// Same look as the help trigger beside it.
		expect(icon.className).toBe(screen.getByTestId('page-help-trigger').className);
		await user.hover(icon);
		expect(await screen.findByRole('tooltip')).toHaveTextContent('Show the tour');
		await user.unhover(icon);
		await waitFor(() => expect(screen.queryByRole('tooltip')).toBeNull());
		// Keyboard focus shows the same tooltip.
		icon.focus();
		expect(await screen.findByRole('tooltip')).toHaveTextContent('Show the tour');
	});

	it.each([
		['no agents', true, 'New agent'],
		['a fleet', false, 'New agent'],
	])('below md with %s the tour button is not shown', async (_, empty, create) => {
		if (empty) emptyFleet();
		await page.viewport(390, 844);
		renderPage();
		await screen.findByRole('button', { name: create });
		const trigger = screen.getByTestId('tour-trigger');
		expect(trigger).not.toBeVisible();
		// Hidden with the app's mobile cut-off: `hidden` below `md`.
		expect(trigger.parentElement).toHaveClass('hidden', 'md:inline-flex');
		expect(screen.queryByRole('dialog', { name: 'The Jentic One tour' })).toBeNull();
	});

	it('narrowing the window below md closes an open tour', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agent-dock');
		await openTour(user);
		await page.viewport(390, 844);
		await waitFor(() => expect(screen.queryByTestId('agents-tour')).toBeNull());
		await page.viewport(1440, 900);
		// Widening again does not reopen it.
		expect(screen.queryByTestId('agents-tour')).toBeNull();
	});

	it('PageHelp does not link the tour', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agent-dock');
		await user.click(screen.getByTestId('page-help-trigger'));
		const help = await screen.findByTestId('page-help-content');
		expect(within(help).queryByText('Show the tour')).toBeNull();
	});

	it('opens the tour in a full-screen overlay; Esc and X close it and focus returns', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agent-dock');
		const button = screen.getByRole('button', { name: /^Show the tour/ });

		let dialog = await openTour(user);
		expect(dialog).toHaveAttribute('open');
		const tour = within(dialog);
		expect(tour.getAllByRole('tab').map((t) => t.textContent)).toEqual([
			'Why Jentic One',
			'Set it up',
		]);
		// Starts from the typing intro, focus inside, the page behind locked.
		expect(tour.getByTestId('task-composer')).toBeInTheDocument();
		expect(dialog.contains(document.activeElement)).toBe(true);
		expect(document.documentElement.style.overflow).toBe('hidden');

		// A trusted key press: the native dialog closes on its own `cancel`.
		await browserUser.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByTestId('agents-tour')).toBeNull());
		expect(dialog).not.toHaveAttribute('open');
		expect(button).toHaveFocus();
		expect(document.documentElement.style.overflow).toBe('');

		dialog = await openTour(user);
		// Reopened from the start, on Act 1.
		expect(within(dialog).getByRole('tab', { name: 'Why Jentic One' })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(within(dialog).getByTestId('task-composer')).toBeInTheDocument();
		await user.click(within(dialog).getByRole('button', { name: 'Close' }));
		await waitFor(() => expect(screen.queryByTestId('agents-tour')).toBeNull());
		expect(button).toHaveFocus();
	});
});

describe('Agents landing — CTAs close the tour and open the real surfaces', () => {
	it('with no agents, "Create an agent first" closes the tour and opens the real AgentCreateSheet', async () => {
		emptyFleet();
		const user = userEvent.setup();
		renderPage();
		await emptyWorkspace();
		await openTour(user);
		const cta = await openStep(user, /Choose its APIs/);
		await user.click(cta.getByRole('button', { name: 'Create an agent first' }));
		await waitFor(() => expect(screen.queryByTestId('agents-tour')).toBeNull());
		expect(await screen.findByRole('dialog', { name: 'Create agent' })).toBeInTheDocument();
	});

	it('with no agent, the agent-scoped steps say so and the links are real', async () => {
		emptyFleet();
		const user = userEvent.setup();
		renderPage();
		await emptyWorkspace();
		await openTour(user);

		let cta = await openStep(user, /Choose its APIs/);
		expect(cta.getByRole('link', { name: 'Browse the catalog' })).toHaveAttribute(
			'href',
			'/discover',
		);
		cta = await openStep(user, /Add the keys/);
		expect(cta.getByRole('link', { name: 'Add a credential' })).toHaveAttribute(
			'href',
			'/agents?credentials=new',
		);
		cta = await openStep(user, /Set the rules/);
		expect(cta.getByRole('button', { name: 'Open permissions' })).toBeDisabled();
		cta = await openStep(user, /Connect your agent/);
		expect(cta.getByRole('button', { name: 'Connect over MCP' })).toBeDisabled();
		cta = await openStep(user, /Govern from Monitor/);
		expect(cta.getByRole('link', { name: 'Open the Activity log' })).toHaveAttribute(
			'href',
			'/monitor?show=calls',
		);
	});

	it('"Add a credential" closes the tour and opens the real credential wizard', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agent-dock');
		await openTour(user);
		const cta = await openStep(user, /Add the keys/);
		await user.click(cta.getByRole('link', { name: 'Add a credential' }));
		await waitFor(() => expect(screen.queryByTestId('agents-tour')).toBeNull());
		expect(
			(await screen.findAllByRole('dialog', { name: /credential/i })).length,
		).toBeGreaterThan(0);
	});

	it('over a fleet, the tour closes and opens the selected agent’s real permissions and MCP sheets', async () => {
		const user = userEvent.setup();
		renderPage('/agents?agent=agnt_active_1');
		await screen.findByTestId('agent-dock');

		await openTour(user);
		let cta = await openStep(user, /Set the rules/);
		await user.click(cta.getByRole('button', { name: 'Open permissions for support-agent' }));
		await waitFor(() => expect(screen.queryByTestId('agents-tour')).toBeNull());
		expect(await screen.findByRole('dialog', { name: /Permissions/ })).toBeInTheDocument();
		await user.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), {
			timeout: 2000,
		});

		await openTour(user);
		cta = await openStep(user, /Connect your agent/);
		await user.click(cta.getByRole('button', { name: 'Connect support-agent over MCP' }));
		await waitFor(() => expect(screen.queryByTestId('agents-tour')).toBeNull());
		expect(await screen.findByRole('dialog', { name: /MCP/ })).toBeInTheDocument();
	});

	it('"Add APIs" closes the tour and opens the real Add-APIs tray for the agent', async () => {
		const user = userEvent.setup();
		renderPage('/agents?agent=agnt_active_1');
		await screen.findByTestId('agent-dock');
		await openTour(user);
		const cta = await openStep(user, /Choose its APIs/);
		await user.click(cta.getByRole('button', { name: 'Add APIs to support-agent' }));
		await waitFor(() => expect(screen.queryByTestId('agents-tour')).toBeNull());
		expect(await screen.findByRole('dialog', { name: /Add APIs/ })).toBeInTheDocument();
	});

	it('the Govern step links the Activity log filtered to the selected agent', async () => {
		const user = userEvent.setup();
		renderPage('/agents?agent=agnt_active_1');
		await screen.findByTestId('agent-dock');
		await openTour(user);
		const cta = await openStep(user, /Govern from Monitor/);
		expect(cta.getByRole('link', { name: 'Open the Activity log' })).toHaveAttribute(
			'href',
			'/monitor?show=calls&actor_id=agnt_active_1&actor_type=agent',
		);
	});

	it('a CTA closes the tour before it runs', async () => {
		const user = userEvent.setup();
		const { calls } = renderLanding();
		const cta = await openStep(user, /Set the rules/);
		await user.click(cta.getByRole('button', { name: 'Open permissions for support-agent' }));
		await waitFor(() => expect(calls).toEqual(['close', 'surface:permissions']));
	});

	it('"Open notifications" closes the tour, then presses the top-bar bell behind it', async () => {
		const user = userEvent.setup();
		const { calls } = renderLanding();
		const cta = await openStep(user, /Govern from Monitor/);
		await user.click(cta.getByRole('button', { name: 'Open notifications' }));
		await waitFor(() => expect(calls).toEqual(['close', 'bell']));
		expect(screen.queryByTestId('agents-tour')).toBeNull();
	});

	it('"Open notifications" uses the owner\'s action when it has one', async () => {
		const user = userEvent.setup();
		const { calls } = renderLanding({ onOpenNotifications: () => calls.push('notifications') });
		const cta = await openStep(user, /Govern from Monitor/);
		await user.click(cta.getByRole('button', { name: 'Open notifications' }));
		await waitFor(() => expect(calls).toEqual(['close', 'notifications']));
	});

	it('closing on "Set it up" and reopening starts on "Why Jentic One"; Act 2 never mounts', async () => {
		const user = userEvent.setup();
		renderLanding();
		await user.click(screen.getByRole('tab', { name: 'Set it up' }));
		expect(await screen.findByTestId('setup-stage')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Close' }));
		await waitFor(() => expect(screen.queryByTestId('agents-tour')).toBeNull());

		const mounted: string[] = [];
		const watch = new MutationObserver((records) => {
			for (const r of records)
				for (const node of r.addedNodes)
					if (
						node instanceof Element &&
						node.querySelector('[data-testid="setup-stage"]')
					)
						mounted.push('setup-stage');
		});
		watch.observe(document.body, { childList: true, subtree: true });
		try {
			await user.click(screen.getByRole('button', { name: 'Reopen the tour' }));
			expect(screen.getByRole('tab', { name: 'Why Jentic One' })).toHaveAttribute(
				'aria-selected',
				'true',
			);
			expect(screen.getByTestId('why-stage')).toBeInTheDocument();
		} finally {
			watch.disconnect();
		}
		expect(mounted).toEqual([]);
		expect(screen.queryByTestId('setup-stage')).toBeNull();
	});
});

describe('Agents landing — the acts', () => {
	it('has two acts: Why Jentic One and Set it up', () => {
		renderLanding();
		expect(
			within(screen.getByRole('tablist', { name: 'Tour sections' }))
				.getAllByRole('tab')
				.map((t) => t.textContent),
		).toEqual(['Why Jentic One', 'Set it up']);
	});

	it('Act 1 shows both lanes, the five stage cards, the bell and one Activity log — no Monitor tabs', async () => {
		const { container } = renderLanding();
		const panel = within(screen.getByTestId('operator-panel'));
		expect(panel.getByRole('region', { name: 'Notifications' })).toBeInTheDocument();
		const source = panel.getByRole('group', { name: 'Activity source' });
		expect(
			within(source)
				.getAllByRole('button')
				.map((b) => b.textContent),
		).toEqual(['All', 'Calls', 'Jobs', 'Audit']);
		expect(screen.getByTestId('without-lane')).toBeInTheDocument();
		const withLane = screen.getByTestId('with-lane');
		expect(
			[...withLane.querySelectorAll('[data-stage]')].map((g) => g.getAttribute('data-stage')),
		).toEqual(['find', 'identity', 'rules', 'vault', 'record']);
		expect(
			[...withLane.querySelectorAll('[data-with-api]')].map((g) =>
				g.getAttribute('data-with-api'),
			),
		).toEqual(['github', 'gmail', 'slack', 'googledrive']);
		const without = screen.getByTestId('without-lane');
		expect(
			[...without.querySelectorAll('[data-without-api]')].map((g) =>
				g.getAttribute('data-without-api'),
			),
		).toEqual(['github', 'gmail', 'slack', 'googledrive']);
		// Credentials as one tidy pill under the agent: one mark per key it holds.
		const pill = within(without).getByTestId('key-pill');
		expect(pill).toHaveTextContent(`${ENV_SECRETS.length} live keys`);
		expect(
			[...pill.querySelectorAll('[data-key-api]')].map((m) => m.getAttribute('data-key-api')),
		).toEqual([...STORY_APIS]);
		expect(within(withLane).getByTestId('no-keys-pill')).toHaveTextContent('no keys');
		expect(within(without).getByTestId('key-pill-trigger')).toHaveAccessibleName(
			`The agent holds ${ENV_SECRETS.length} live keys`,
		);
		expect(within(without).getByTestId('what-went-wrong')).toBeInTheDocument();
		expect(
			[...screen.getByTestId('comparison-strip').querySelectorAll('[data-stat]')].map((c) =>
				c.getAttribute('data-stat'),
			),
		).toEqual(['keys', 'approvals', 'traced', 'stopped', 'lost']);
		// Your AI agent is the cluster of agent marks; Jentic is the gateway, not the agent.
		for (const lane of [withLane, without])
			expect(lane.querySelector('[data-agent-mark]')).toBeInTheDocument();
		expect(within(withLane).getByTestId('gateway-logo')).toBeInTheDocument();
		expect(within(withLane).getByText('Your AI agent')).toBeInTheDocument();
		expect(within(without).getByTestId('no-gateway')).toBeInTheDocument();
		expect(screen.getByTestId('operator-agent')).toHaveTextContent('research-bot');
		// The tour is the modal surface under test.
		await checkA11y(container, { modal: true });
	});

	it('under reduced motion Act 1 is stepped by hand, and the caption announces each beat', async () => {
		const user = userEvent.setup();
		renderLanding();
		expect(
			screen.queryByRole('button', { name: /Play the walkthrough|Pause the walkthrough/ }),
		).toBeNull();
		// No typing intro: the task bar is there from the first still.
		expect(screen.queryByTestId('task-composer')).toBeNull();
		expect(screen.getByTestId('task-bar')).toHaveTextContent(WHY_TASK);
		// The narration sits at the top, inside the task bar, above the lanes.
		const caption = screen.getByTestId('landing-caption');
		expect(caption).toHaveAttribute('aria-live', 'polite');
		expect(screen.getByTestId('task-bar')).toContainElement(caption);
		expect(screen.getAllByTestId('landing-caption')).toHaveLength(1);
		expect(
			caption.compareDocumentPosition(screen.getByTestId('with-lane')) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		expect(screen.getByTestId('bell-count')).toHaveTextContent('0');
		const first = caption.textContent;
		await user.click(screen.getByRole('button', { name: 'Next step' }));
		expect(caption.textContent).not.toBe(first);
		// The self-registered agent's approval lands in the bell.
		expect(screen.getByTestId('bell-count')).toHaveTextContent('1');
		// ...and it is labelled there (static under reduced motion); the caption said so.
		expect(screen.getByTestId('monitor-label')).toHaveTextContent('Needs you');
		await user.click(screen.getByRole('button', { name: 'Next step' }));
		// Approved: the bell clears and the agent is active.
		expect(screen.getByTestId('bell-count')).toHaveTextContent('0');
		expect(screen.getByTestId('operator-agent')).toHaveTextContent('Active');
	});

	it('Act 2 is a browser-framed tour whose CTAs are real, with the CLI registration snippet', async () => {
		const user = userEvent.setup();
		const { calls, container } = renderLanding();
		await user.click(screen.getByRole('tab', { name: 'Set it up' }));
		expect(await screen.findByTestId('frame-url')).toHaveTextContent('/app/agents');
		expect(screen.getByText(/jentic register --url /)).toBeInTheDocument();
		await checkA11y(container, { modal: true });
		await user.click(
			within(screen.getByTestId('setup-cta')).getByRole('button', {
				name: /Create (your first|another) agent/,
			}),
		);
		// A CTA closes the tour, then opens the real thing.
		await waitFor(() => expect(calls).toEqual(['close', 'create']));
	});

	it('Act 1 stepper shows all four steps dim from the start, then fills them in order, and a step seeks', async () => {
		const user = userEvent.setup();
		renderLanding();
		const plan = within(screen.getByRole('list', { name: 'The plan' }));
		const items = () => plan.getAllByRole('listitem');
		const tones = () => items().map((li) => li.getAttribute('data-tone'));
		expect(items().map((li) => li.getAttribute('data-plan'))).toEqual([
			'github',
			'gmail',
			'slack',
			'googledrive',
		]);
		expect(tones()).toEqual(['todo', 'todo', 'todo', 'todo']);
		expect(plan.getByRole('button', { name: /Clean up Drive/ })).toHaveTextContent(
			'not started',
		);
		const connectors = () =>
			screen
				.getByRole('list', { name: 'The plan' })
				.querySelectorAll('[data-connector="filled"]').length;
		expect(connectors()).toBe(0);
		// Step to the first GitHub beat: step 1 runs, the rest stay dim.
		for (let i = 0; i < 3; i++)
			await user.click(screen.getByRole('button', { name: 'Next step' }));
		expect(tones()).toEqual(['active', 'todo', 'todo', 'todo']);
		for (let i = 0; i < 8; i++)
			await user.click(screen.getByRole('button', { name: 'Next step' }));
		expect(tones()).toEqual(['ok', 'active', 'todo', 'todo']);
		// The line fills up to the running step.
		expect(connectors()).toBe(1);
		await user.click(plan.getByRole('button', { name: /Clean up Drive/ }));
		expect(screen.getByTestId('why-stage')).toHaveAttribute('data-beat', 'drive-launch');
		await user.click(plan.getByRole('button', { name: /Read GitHub issues/ }));
		expect(screen.getByTestId('why-stage')).toHaveAttribute('data-beat', 'github-launch');
	});

	it('Act 1 lands a call row in Activity the moment Record completes, with the same call tag', async () => {
		const user = userEvent.setup();
		renderLanding();
		const next = () => user.click(screen.getByRole('button', { name: 'Next step' }));
		// task, register, approve, github launch + find/identity/rules/vault.
		for (let i = 0; i < 7; i++) await next();
		const rows = screen.getByTestId('activity-rows');
		expect(within(rows).queryByText('GET issues.list')).toBeNull();
		await next();
		expect(screen.getByTestId('why-stage')).toHaveAttribute('data-beat', 'github-record');
		const row = within(rows).getByText('GET issues.list').closest('li');
		expect(row).toHaveAttribute('data-pulse', 'true');
		expect(row).toHaveTextContent('Call 1');
		expect(
			screen.getByTestId('with-lane').querySelector('[data-stage="record"]'),
		).toHaveAttribute('data-pulse', 'true');
	});

	it('Act 2 connect step has a user-clickable Chat / Terminal toggle using real CLI syntax', async () => {
		const user = userEvent.setup();
		renderLanding();
		await openStep(user, /Connect your agent/);
		const client = screen.getByRole('group', { name: 'Client' });
		await user.click(within(client).getByRole('button', { name: 'Terminal' }));
		expect(within(client).getByRole('button', { name: 'Terminal' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		// Step to the frame where the commands have run.
		for (let i = 0; i < 4; i++)
			await user.click(screen.getByRole('button', { name: 'Next step' }));
		const stage = screen.getByTestId('setup-stage');
		expect(stage).toHaveTextContent('jentic search "github issues"');
		expect(stage).toHaveTextContent(
			'jentic execute issues/list-for-repo --path owner=acme --path repo=app --query state=open',
		);
		expect(stage).toHaveTextContent(/jentic execute chat\.postMessage -d '\{/);
	});

	it('picking Terminal while the tour plays keeps it playing', async () => {
		const user = userEvent.setup();
		renderLanding({}, { reducedMotion: false });
		await openStep(user, /Connect your agent/);
		const client = screen.getByRole('group', { name: 'Client' });
		const terminal = within(client).getByRole('button', { name: 'Terminal' });
		await user.click(terminal);
		const stage = screen.getByTestId('setup-stage');
		// The switch replays the split view…
		await waitFor(() => expect(stage).toHaveAttribute('data-frame', '3'));
		// …and, with focus still on the toggle, the next frame arrives on its own.
		expect(terminal).toHaveFocus();
		await waitFor(() => expect(Number(stage.getAttribute('data-frame'))).toBeGreaterThan(3), {
			timeout: 5000,
		});
		expect(stage).toHaveAttribute('data-step', 'connect');
	});
});

describe('Agents landing — source hygiene', () => {
	it('uses no emoji anywhere', () => {
		const sources = import.meta.glob('../components/landing/**/*.{ts,tsx}', {
			query: '?raw',
			import: 'default',
			eager: true,
		}) as Record<string, string>;
		expect(Object.keys(sources).length).toBeGreaterThan(5);
		for (const [file, text] of Object.entries(sources)) {
			expect(/\p{Extended_Pictographic}/u.test(text), file).toBe(false);
		}
	});
});

/**
 * Real text in the tour must be readable against what it actually sits on. The
 * miniatures render plain labels (agent names, rows) that inherit their colour,
 * so a wrong inherited colour only shows once a beat puts them on screen:
 * audit every beat of both acts, not just the opening frame.
 */
describe('Agents landing — the tour meets colour contrast on every beat', () => {
	let freeze: HTMLStyleElement;
	beforeEach(() => {
		// Entrance fades would be measured mid-flight; contrast is about the settled frame.
		freeze = document.createElement('style');
		freeze.textContent =
			'*,*::before,*::after{animation:none!important;transition:none!important}';
		document.head.appendChild(freeze);
		return () => freeze.remove();
	});

	async function expectReadable(where: string) {
		const { default: axe } = await import('axe-core');
		const dialog = screen.getByRole('dialog', { name: 'The Jentic One tour' });
		// The miniatures are `inert` + `aria-hidden` (decoration for the caption),
		// which axe rightly skips for assistive tech — but sighted operators read
		// them, so expose them just for the measurement. Only `data-decorative`
		// ornaments (no text worth reading) stay out.
		const hidden = [
			...dialog.querySelectorAll<HTMLElement>('[aria-hidden="true"], [inert]'),
		].filter((el) => !el.closest('[data-decorative]') && el.tagName !== 'svg');
		const saved = hidden.map(
			(el) => [el, el.getAttribute('aria-hidden'), el.hasAttribute('inert')] as const,
		);
		for (const el of hidden) {
			el.removeAttribute('aria-hidden');
			el.removeAttribute('inert');
		}
		let results: AxeResults;
		try {
			results = await axe.run(dialog, { runOnly: ['color-contrast'] });
		} finally {
			for (const [el, aria, inert] of saved) {
				if (aria != null) el.setAttribute('aria-hidden', aria);
				if (inert) el.setAttribute('inert', '');
			}
		}
		const failures = results.violations.flatMap((v) =>
			v.nodes.map((n) => `${n.target.join(' ')} — ${n.failureSummary ?? ''}`),
		);
		expect(failures, `${where}: text below WCAG AA contrast`).toEqual([]);
	}

	async function auditBeats(
		user: ReturnType<typeof userEvent.setup>,
		count: number,
		where: string,
	) {
		for (let i = 0; i < count; i++) {
			await expectReadable(`${where}, beat ${i + 1}`);
			if (i < count - 1) await user.click(screen.getByRole('button', { name: 'Next step' }));
		}
	}

	for (const [index, step] of SETUP_STEPS.entries()) {
		it(`Set it up, step ${index + 1} (${step.title}): every beat`, async () => {
			const user = userEvent.setup();
			renderLanding();
			await openStep(user, new RegExp(step.title));
			await auditBeats(user, step.frames.length, step.title);
		});
	}

	it('Set it up, Connect in Terminal mode: every beat', async () => {
		const user = userEvent.setup();
		renderLanding();
		await openStep(user, /Connect your agent/);
		const client = screen.getByRole('group', { name: 'Client' });
		await user.click(within(client).getByRole('button', { name: 'Terminal' }));
		const connect = SETUP_STEPS.find((s) => /Connect/.test(s.title))!;
		await auditBeats(user, connect.frames.length, 'Connect (Terminal)');
	});

	it('Why Jentic One: every beat', async () => {
		const user = userEvent.setup();
		renderLanding();
		await screen.findByRole('tab', { name: 'Why Jentic One' });
		await auditBeats(user, WHY_SCRIPT.beats.length, 'Why Jentic One');
	});
});
