/**
 * The Agents surface offers only the verbs the caller's permissions allow, and
 * sends no request the server would refuse them:
 *
 * - Approve / Deny (banner, the selected agent's state banner, the dock), the
 *   dock's serving toggle and Archive, and "Create here" need `agents:write`
 *   or `org:admin`.
 * - The usage aggregate (`/monitoring/usage`: stat strip, credential inventory)
 *   needs `org:admin`.
 * - "Recent changes" (`/audit`) needs `audit:read` or `org:admin`.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { clearToken, setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { resetApisStore, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import { ActorAuditPanel } from '@/modules/agents/components/detail/ActorAuditPanel';

/** A member's effective permissions with the usual defaults. */
const MEMBER_DEFAULTS = [
	'agents:read',
	'agents:write',
	'apis:read',
	'audit:read',
	'credentials:read',
	'credentials:write',
	'events:read',
	'executions:read',
	'jobs:read',
];
const without = (...drop: string[]) => MEMBER_DEFAULTS.filter((p) => !drop.includes(p));

const VIEWERS: Record<string, readonly string[]> = {
	admin: ['org:admin'],
	'member with defaults': MEMBER_DEFAULTS,
	'member without agents:write': without('agents:write'),
	'member without events:read': without('events:read'),
	'member without jobs:read': without('jobs:read'),
};

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

/** Every request path the page sends, in order. */
function trackRequests(): string[] {
	const paths: string[] = [];
	worker.events.on('request:start', ({ request }) => {
		paths.push(new URL(request.url).pathname);
	});
	return paths;
}

function renderPage(route: string) {
	return renderWithProviders(
		<AuthProvider>
			<AgentsPage />
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

afterEach(() => {
	worker.events.removeAllListeners();
	clearToken();
});

describe('approval verbs follow agents:write', () => {
	it.each(Object.entries(VIEWERS))('%s', async (_label, permissions) => {
		const canDecide = permissions.includes('agents:write') || permissions.includes('org:admin');
		seedViewer(permissions);
		renderPage('/?agent=agnt_pending_1');

		const banner = await screen.findByRole('region', { name: /Awaiting approval/i });
		// Review is open to anyone who can see the agent.
		expect(within(banner).getByRole('button', { name: /^Review / })).toBeInTheDocument();
		const state = await screen.findByTestId('agent-state-banner-pending');
		const dock = await screen.findByTestId('agent-dock');

		if (canDecide) {
			expect(within(banner).getByRole('button', { name: /^Approve / })).toBeInTheDocument();
			expect(within(banner).getByRole('button', { name: /^Deny / })).toBeInTheDocument();
			expect(within(state).getByTestId('state-banner-approve')).toBeInTheDocument();
			expect(within(state).getByTestId('state-banner-deny')).toBeInTheDocument();
			expect(within(dock).getByTestId('dock-approve')).toBeInTheDocument();
		} else {
			expect(within(banner).queryByRole('button', { name: /^Approve / })).toBeNull();
			expect(within(banner).queryByRole('button', { name: /^Deny / })).toBeNull();
			expect(within(state).queryByTestId('state-banner-approve')).toBeNull();
			expect(within(state).queryByTestId('state-banner-deny')).toBeNull();
			expect(state).toHaveTextContent('Someone who can manage agents needs to approve it.');
			expect(within(dock).queryByTestId('dock-approve')).toBeNull();
			expect(within(dock).getByTestId('dock-state-note')).toHaveTextContent(
				'Waiting for approval',
			);
		}
	});
});

describe("the dock's serving toggle and Archive follow agents:write", () => {
	it.each(Object.entries(VIEWERS))('%s', async (_label, permissions) => {
		const canManage = permissions.includes('agents:write') || permissions.includes('org:admin');
		seedViewer(permissions);
		renderPage('/?agent=agnt_active_1');
		const dock = await screen.findByTestId('agent-dock');
		// The read affordances stay for everyone.
		expect(within(dock).getByRole('button', { name: 'Activity' })).toBeInTheDocument();

		if (canManage) {
			expect(await within(dock).findByTestId('dock-serving-toggle')).toBeInTheDocument();
			expect(
				within(dock).getByRole('button', { name: 'Archive support-agent' }),
			).toBeInTheDocument();
		} else {
			expect(await within(dock).findByTestId('dock-state-note')).toHaveTextContent(
				'Serving traffic',
			);
			expect(within(dock).queryByTestId('dock-serving-toggle')).toBeNull();
			expect(
				within(dock).queryByRole('button', { name: 'Archive support-agent' }),
			).toBeNull();
		}
	});

	it('a disabled agent reads as a note without agents:write', async () => {
		seedViewer(without('agents:write'));
		renderPage('/?agent=agnt_disabled_1');
		const dock = await screen.findByTestId('agent-dock');
		expect(await within(dock).findByTestId('dock-state-note')).toHaveTextContent(
			'Disabled — not serving traffic',
		);
		expect(within(dock).queryByTestId('dock-serving-toggle')).toBeNull();
	});
});

describe('"Create here" follows agents:write', () => {
	it.each(Object.entries(VIEWERS))('%s', async (_label, permissions) => {
		const canCreate = permissions.includes('agents:write') || permissions.includes('org:admin');
		const user = userEvent.setup();
		seedViewer(permissions);
		renderPage('/?agent=agnt_active_1');
		await screen.findByTestId('agent-dock');

		await user.click(screen.getByRole('button', { name: 'New agent' }));
		const sheet = await screen.findByRole('dialog', { name: 'New agent' });
		const register = within(sheet).getByRole('tab', { name: 'Register from the CLI' });
		if (canCreate) {
			expect(within(sheet).getByRole('tab', { name: 'Create here' })).toHaveAttribute(
				'aria-selected',
				'true',
			);
		} else {
			expect(within(sheet).queryByRole('tab', { name: 'Create here' })).toBeNull();
			expect(register).toHaveAttribute('aria-selected', 'true');
			expect(within(sheet).getByTestId('new-agent-panel-register')).toHaveAttribute(
				'data-state',
				'active',
			);
		}
	});
});

describe('the usage aggregate is read only by org:admin', () => {
	it.each(Object.entries(VIEWERS))('%s', async (_label, permissions) => {
		const isAdmin = permissions.includes('org:admin');
		const paths = trackRequests();
		seedViewer(permissions);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		await screen.findByTestId('agent-stat-strip');

		// The credential inventory reads the per-credential totals.
		await user.click(screen.getByRole('button', { name: 'Credentials' }));
		await screen.findByTestId('sheet-primitive');
		// Long enough for the strip's and the inventory's reads to have gone out.
		await new Promise((r) => setTimeout(r, 500));

		const usageReads = paths.filter((p) => p === '/monitoring/usage').length;
		if (isAdmin) expect(usageReads).toBeGreaterThan(0);
		else expect(usageReads).toBe(0);
	});
});

describe('"Recent changes" reads the audit log only with audit:read', () => {
	it.each([
		['admin', ['org:admin'], true],
		['member with defaults', MEMBER_DEFAULTS, true],
		['member without audit:read', without('audit:read'), false],
	] as const)('%s', async (_label, permissions, canRead) => {
		const paths = trackRequests();
		seedViewer(permissions);
		renderWithProviders(
			<AuthProvider>
				<ActorAuditPanel actorId="agnt_active_1" />
			</AuthProvider>,
		);

		if (canRead) {
			await waitFor(() => expect(paths).toContain('/audit'));
		} else {
			expect(
				await screen.findByText("This agent's change history needs audit access."),
			).toBeInTheDocument();
			await new Promise((r) => setTimeout(r, 300));
			expect(paths).not.toContain('/audit');
		}
	});
});
