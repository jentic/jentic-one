/**
 * The Agents surface's "nothing to show you" states:
 *
 * - `?agent=<id>` for an agent the caller's roster does not hold (another
 *   user's, or none) reads "Agent not found" and selects nothing, instead of
 *   quietly showing a different agent.
 *   An empty roster reads the same for such a link, not the first-agent landing.
 * - A caller known to lack `agents:read` reads "No access to agents" without
 *   the roster being requested; a refused roster read (403) reads the same,
 *   never the server's raw "This action requires one of: agents:read".
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import { useLocation } from 'react-router';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
	settleAnimations,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import {
	clearAgentsStore,
	resetAgentsStore,
	seedExtraAgents,
} from '@/modules/agents/mocks/handlers';
import { resetApisStore, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

/** A member's effective permissions with the usual defaults. */
const MEMBER_DEFAULTS = [
	'agents:read',
	'agents:write',
	'audit:read',
	'credentials:read',
	'events:read',
	'executions:read',
	'jobs:read',
];
const without = (...drop: string[]) => MEMBER_DEFAULTS.filter((p) => !drop.includes(p));

function seedViewer(permissions: readonly string[]) {
	worker.use(
		http.get('/users/me', () =>
			HttpResponse.json({
				id: 'usr_viewer_1',
				email: 'viewer@local',
				first_name: 'View',
				last_name: 'Er',
				active: true,
				permissions,
				must_change_password: false,
				created_at: '2026-01-01T00:00:00Z',
				updated_at: null,
			}),
		),
	);
}

function LocationProbe() {
	return <div data-testid="location-search">{useLocation().search}</div>;
}

function renderPage(route: string) {
	return renderWithProviders(
		<AuthProvider>
			<AgentsPage />
			<LocationProbe />
			<Toaster />
		</AuthProvider>,
		{ route },
	);
}

afterEach(() => {
	worker.events.removeAllListeners();
});

/** Count full-roster reads (`GET /agents` without a status filter). */
function trackRosterReads(onRead?: (n: number) => void) {
	const calls = { roster: 0 };
	worker.events.on('response:mocked', ({ request }) => {
		const url = new URL(request.url);
		if (url.pathname !== '/agents' || url.searchParams.has('status')) return;
		calls.roster += 1;
		onRead?.(calls.roster);
	});
	return calls;
}

beforeEach(async () => {
	await page.viewport(1280, 900);
	setToken('test-token');
	window.localStorage.clear();
	resetAgentsStore();
	resetCredentialsStore([]);
	resetApisStore([]);
});

describe('?agent= for an agent the caller cannot see', () => {
	it.each([
		['admin', ['org:admin']],
		['member with defaults', MEMBER_DEFAULTS],
		['member without agents:write', without('agents:write')],
		['member without events:read', without('events:read')],
		['member without jobs:read', without('jobs:read')],
	] as const)('%s: reads "Agent not found" and selects nothing', async (_label, permissions) => {
		seedViewer(permissions);
		const { container } = renderPage('/?agent=agnt_someone_elses');

		const notFound = await screen.findByTestId('agent-not-found');
		expect(within(notFound).getByText('Agent not found')).toBeInTheDocument();
		// The roster is on screen, but no other agent is selected in its place.
		expect(await screen.findAllByText('support-agent')).not.toHaveLength(0);
		expect(screen.queryByRole('tab', { selected: true })).toBeNull();
		expect(screen.queryByTestId('agent-dock')).toBeNull();
		expect(screen.queryByRole('tabpanel')).toBeNull();
		// The id stays in the URL: the deep link is not rewritten to another agent.
		expect(screen.getByTestId('location-search')).toHaveTextContent('agent=agnt_someone_elses');
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('reads the roster once more before saying "not found"', async () => {
		seedViewer(MEMBER_DEFAULTS);
		const calls = trackRosterReads();
		renderPage('/?agent=agnt_someone_elses');

		expect(await screen.findByTestId('agent-not-found')).toBeInTheDocument();
		expect(calls.roster).toBeGreaterThanOrEqual(2);
	});

	it('finds an agent the cached roster predates on that second read', async () => {
		seedViewer(MEMBER_DEFAULTS);
		// The agent registers right after the first roster read answers.
		trackRosterReads((n) => {
			if (n === 1) {
				seedExtraAgents([
					{ id: 'agnt_just_registered', name: 'late-bot', status: 'active' },
				]);
			}
		});
		renderPage('/?agent=agnt_just_registered');

		expect(await screen.findByTestId('agent-dock')).toBeInTheDocument();
		expect(screen.getByRole('tab', { selected: true })).toHaveAccessibleName(/late-bot/);
		expect(screen.queryByTestId('agent-not-found')).toBeNull();
	});

	it('selects nothing while an unknown id is being checked', async () => {
		seedViewer(MEMBER_DEFAULTS);
		let release: () => void = () => {};
		const held = new Promise<void>((r) => (release = r));
		let reads = 0;
		worker.use(
			http.get('/agents', async ({ request }) => {
				if (new URL(request.url).searchParams.has('status')) return undefined;
				reads += 1;
				// Hold the recheck open, so the in-between state is on screen.
				if (reads === 2) await held;
				return undefined;
			}),
		);
		renderPage('/?agent=agnt_someone_elses');

		await screen.findAllByText('support-agent');
		await waitFor(() => expect(reads).toBe(2));
		expect(screen.queryByRole('tab', { selected: true })).toBeNull();
		expect(screen.queryByTestId('agent-dock')).toBeNull();
		expect(screen.queryByTestId('agent-not-found')).toBeNull();
		release();
		expect(await screen.findByTestId('agent-not-found')).toBeInTheDocument();
	});

	it('"Show my agents" selects the first agent of the roster', async () => {
		const user = userEvent.setup();
		seedViewer(MEMBER_DEFAULTS);
		renderPage('/?agent=agnt_someone_elses');

		await user.click(
			within(await screen.findByTestId('agent-not-found')).getByRole('button', {
				name: 'Show my agents',
			}),
		);
		expect(screen.queryByTestId('agent-not-found')).toBeNull();
		expect(await screen.findByTestId('agent-dock')).toBeInTheDocument();
		expect(screen.getByTestId('location-search')).not.toHaveTextContent('agnt_someone_elses');
	});

	it('a visible agent deep link still selects that agent', async () => {
		seedViewer(MEMBER_DEFAULTS);
		renderPage('/?agent=agnt_active_1');

		expect(await screen.findByTestId('agent-dock')).toBeInTheDocument();
		expect(screen.queryByTestId('agent-not-found')).toBeNull();
		expect(screen.getByRole('tab', { selected: true })).toHaveAccessibleName(/support-agent/);
	});
});

describe('?agent= on an empty roster', () => {
	it.each([
		['admin', ['org:admin']],
		['member with defaults', MEMBER_DEFAULTS],
		['member without agents:write', without('agents:write')],
	] as const)(
		'%s: reads "Agent not found", not the first-agent landing',
		async (_label, permissions) => {
			clearAgentsStore();
			seedViewer(permissions);
			const { container } = renderPage('/?agent=agnt_someone_elses');

			const notFound = await screen.findByTestId('agent-not-found');
			expect(within(notFound).getByText('Agent not found')).toBeInTheDocument();
			expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
			expect(screen.getByTestId('location-search')).toHaveTextContent(
				'agent=agnt_someone_elses',
			);
			await settleAnimations(container);
			await checkA11y(container);
		},
	);

	it('holds the landing while the id is checked', async () => {
		clearAgentsStore();
		seedViewer(MEMBER_DEFAULTS);
		let release: () => void = () => {};
		const held = new Promise<void>((r) => (release = r));
		let reads = 0;
		worker.use(
			http.get('/agents', async ({ request }) => {
				if (new URL(request.url).searchParams.has('status')) return undefined;
				reads += 1;
				if (reads === 2) await held;
				return undefined;
			}),
		);
		renderPage('/?agent=agnt_someone_elses');

		await waitFor(() => expect(reads).toBe(2));
		expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
		expect(screen.queryByTestId('agent-not-found')).toBeNull();
		release();
		expect(await screen.findByTestId('agent-not-found')).toBeInTheDocument();
	});

	it('"Show my agents" drops the link and brings the landing back', async () => {
		const user = userEvent.setup();
		clearAgentsStore();
		seedViewer(MEMBER_DEFAULTS);
		renderPage('/?agent=agnt_someone_elses');

		await user.click(
			within(await screen.findByTestId('agent-not-found')).getByRole('button', {
				name: 'Show my agents',
			}),
		);
		expect(await screen.findByTestId('agents-empty-landing')).toBeInTheDocument();
		expect(screen.queryByTestId('agent-not-found')).toBeNull();
		expect(screen.getByTestId('location-search')).not.toHaveTextContent('agent=');
	});

	it('an empty roster without ?agent= still shows the landing', async () => {
		clearAgentsStore();
		seedViewer(MEMBER_DEFAULTS);
		renderPage('/');

		expect(await screen.findByTestId('agents-empty-landing')).toBeInTheDocument();
		expect(screen.queryByTestId('agent-not-found')).toBeNull();
	});
});

describe('a caller without agents:read', () => {
	it.each([
		['no agents permission', without('agents:read', 'agents:write')],
		['only owner:agents:read', ['owner:agents:read', 'audit:read']],
	] as const)(
		'%s: reads "No access to agents" without requesting the roster',
		async (_label, permissions) => {
			seedViewer(permissions);
			const agentReads: string[] = [];
			worker.events.on('request:start', ({ request }) => {
				const url = new URL(request.url);
				if (url.pathname === '/agents') agentReads.push(url.search);
			});
			renderPage('/?agent=agnt_active_1');

			expect(await screen.findByText('No access to agents')).toBeInTheDocument();
			// Long enough for any roster or pending-agents read to have gone out.
			await new Promise((r) => setTimeout(r, 500));
			expect(agentReads).toEqual([]);
			expect(screen.queryByTestId('agent-not-found')).toBeNull();
		},
	);

	it('with the permissions unknown, a refused roster read still reads "No access to agents"', async () => {
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json(
					{ detail: 'This action requires one of: agents:read' },
					{ status: 403 },
				),
			),
		);
		// No AuthProvider: the viewer is unknown, so the request goes out and the
		// server's answer decides.
		renderWithProviders(<AgentsPage />, { route: '/' });

		expect(await screen.findByText('No access to agents')).toBeInTheDocument();
		expect(screen.queryByText(/This action requires one of/)).toBeNull();
	});

	it('reads "No access to agents", not the raw server reason', async () => {
		seedViewer(without('agents:read', 'agents:write'));
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json(
					{ detail: 'This action requires one of: agents:read' },
					{ status: 403 },
				),
			),
		);
		const { container } = renderPage('/');

		expect(await screen.findByText('No access to agents')).toBeInTheDocument();
		expect(screen.queryByText(/This action requires one of/)).toBeNull();
		expect(screen.queryByRole('alert')).toBeNull();
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('a 401 reads as an ended session, not as missing access', async () => {
		seedViewer(MEMBER_DEFAULTS);
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 }),
			),
		);
		renderPage('/');

		expect(await screen.findByText("Couldn't load agents")).toBeInTheDocument();
		expect(screen.getByText(/Your session may have ended/)).toBeInTheDocument();
		expect(screen.queryByText('No access to agents')).toBeNull();
	});

	it('a server failure still shows the error', async () => {
		seedViewer(MEMBER_DEFAULTS);
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({ detail: 'Database unavailable' }, { status: 500 }),
			),
		);
		renderPage('/');

		expect(await screen.findByRole('alert')).toBeInTheDocument();
		expect(screen.queryByText('No access to agents')).toBeNull();
	});
});
