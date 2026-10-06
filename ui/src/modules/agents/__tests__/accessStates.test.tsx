/**
 * The Agents surface's "nothing to show you" states:
 *
 * - `?agent=<id>` for an agent the caller's roster does not hold (another
 *   user's, or none) reads "Agent not found" and selects nothing, instead of
 *   quietly showing a different agent.
 * - A refused roster read (no `agents:read`) reads "No access to agents",
 *   never the server's raw "This action requires one of: agents:read".
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import { useLocation } from 'react-router';
import {
	renderWithProviders,
	screen,
	within,
	userEvent,
	checkA11y,
	settleAnimations,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
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
		expect(screen.queryByRole('region', { name: /^APIs for / })).toBeNull();
		// The id stays in the URL: the deep link is not rewritten to another agent.
		expect(screen.getByTestId('location-search')).toHaveTextContent('agent=agnt_someone_elses');
		await settleAnimations(container);
		await checkA11y(container);
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

describe('a caller without agents:read', () => {
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
