/**
 * The Notifications bell reads only the sources the caller may read, counts
 * every source it reads and fails, and offers Approve only to a caller who may
 * approve (`agents:write` or `org:admin`).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, userEvent, within } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { AuthProvider } from '@/shared/auth';
import { NotificationsMenu } from '@/shared/app/NotificationsMenu';
import { AgentStreamProvider } from '@/shared/lib/agentStream';
import { clearToken, setToken } from '@/shared/api';
import { useOpenConnectRequests } from '@/shared/credentials/api';

const emptyPage = { data: [], has_more: false, next_cursor: null };

/** A member's effective permissions with the usual defaults. */
const MEMBER_DEFAULTS = [
	'agents:read',
	'agents:write',
	'credentials:read',
	'events:read',
	'jobs:read',
];
const without = (...drop: string[]) => MEMBER_DEFAULTS.filter((p) => !drop.includes(p));

function viewer(permissions: readonly string[]) {
	return {
		id: 'usr_viewer_1',
		email: 'viewer@local',
		first_name: 'View',
		last_name: 'Er',
		active: true,
		permissions,
		must_change_password: false,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: null,
	};
}

const pendingAgent = {
	id: 'agnt_pending_9',
	name: 'waiting-bot',
	description: null,
	status: 'pending',
	owner_id: 'usr_viewer_1',
	registered_by: 'self',
	parent_agent_id: null,
	approved_by: null,
	denial_reason: null,
	denied_by: null,
	created_at: '2026-01-01T00:00:00Z',
	approved_at: null,
	has_api_key: false,
};

/** Count reads per source and answer each with a pending agent / empty pages. */
function trackSources({ agentsStatus = 200 }: { agentsStatus?: number } = {}) {
	const calls = { agents: 0, credentials: 0, events: 0 };
	worker.use(
		http.get('/agents', () => {
			calls.agents += 1;
			if (agentsStatus !== 200) return new HttpResponse(null, { status: agentsStatus });
			return HttpResponse.json({ data: [pendingAgent], has_more: false, next_cursor: null });
		}),
		http.get('/credentials', () => {
			calls.credentials += 1;
			return HttpResponse.json(emptyPage);
		}),
		http.get('/events', () => {
			calls.events += 1;
			return HttpResponse.json(emptyPage);
		}),
		http.get('/events/stream', () => new HttpResponse(null, { status: 503 })),
	);
	return calls;
}

/**
 * Marks the open connect-request read as settled. It shares the menu's query,
 * so both re-render in the same commit: once the marker is in the DOM, the
 * menu has rendered (or withheld) the connect-request row.
 */
function ConnectRequestsSettled() {
	const { isSuccess } = useOpenConnectRequests();
	return isSuccess ? <span data-testid="connect-requests-settled" hidden /> : null;
}

function renderMenu({ withConnectProbe = false }: { withConnectProbe?: boolean } = {}) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<AuthProvider>
				<MemoryRouter initialEntries={['/dashboard']}>
					<AgentStreamProvider live={false}>
						<NotificationsMenu />
						{withConnectProbe && <ConnectRequestsSettled />}
					</AgentStreamProvider>
				</MemoryRouter>
			</AuthProvider>
		</QueryClientProvider>,
	);
}

async function openMenu() {
	const user = userEvent.setup();
	await user.click(await screen.findByRole('button', { name: 'Notifications' }));
	return screen.getByRole('dialog', { name: /Notifications/ });
}

beforeEach(() => {
	window.localStorage.clear();
	setToken('test-token');
});

afterEach(() => {
	window.localStorage.clear();
	clearToken();
});

describe('Notifications — approval verbs', () => {
	it.each([
		['admin', ['org:admin'], true],
		['member with defaults', MEMBER_DEFAULTS, true],
		['member without agents:write', without('agents:write'), false],
		['member without events:read', without('events:read'), true],
		['member without jobs:read', without('jobs:read'), true],
	] as const)('%s', async (_label, permissions, canApprove) => {
		worker.use(http.get('/users/me', () => HttpResponse.json(viewer(permissions))));
		trackSources();
		renderMenu();

		const dialog = await openMenu();
		const approvals = await within(dialog).findByRole('list', { name: 'Approvals' });
		// The agent's own row: an admin's list also holds the OAuth client queue.
		const row = within(approvals)
			.getByText('waiting-bot is waiting for approval')
			.closest('li') as HTMLElement;
		expect(within(row).getByRole('link', { name: 'Review' })).toBeInTheDocument();
		if (canApprove) {
			expect(within(row).getByRole('button', { name: 'Approve' })).toBeInTheDocument();
		} else {
			expect(within(row).queryByRole('button', { name: 'Approve' })).toBeNull();
		}
	});
});

describe('Notifications — sources follow read permissions', () => {
	it('skips the pending-agents read without agents:read, without calling it failed', async () => {
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json(viewer(without('agents:read', 'agents:write'))),
			),
		);
		const calls = trackSources({ agentsStatus: 403 });
		renderMenu();

		const dialog = await openMenu();
		expect(await within(dialog).findByText("You're all caught up")).toBeInTheDocument();
		expect(within(dialog).queryByText(/Couldn't load/)).toBeNull();
		await new Promise((r) => setTimeout(r, 300));
		expect(calls.agents).toBe(0);
	});

	it.each([403, 500])(
		'never says "all caught up" while the pending-agents read failed (%i)',
		async (status) => {
			worker.use(http.get('/users/me', () => HttpResponse.json(viewer(MEMBER_DEFAULTS))));
			trackSources({ agentsStatus: status });
			renderMenu();

			const dialog = await openMenu();
			expect(
				await within(dialog).findByText(/Couldn't load agent approvals/),
			).toBeInTheDocument();
			expect(within(dialog).queryByText("You're all caught up")).toBeNull();
		},
	);

	it.each([
		['credentials:read', ['credentials:read'], true],
		['owner:credentials:read', ['owner:credentials:read'], true],
		['neither', [], false],
	] as const)('reads /credentials with %s: %s', async (_label, extra, reads) => {
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json(viewer([...without('credentials:read'), ...extra])),
			),
		);
		const calls = trackSources();
		renderMenu();

		await openMenu();
		await new Promise((r) => setTimeout(r, 500));
		if (reads) expect(calls.credentials).toBeGreaterThan(0);
		else expect(calls.credentials).toBe(0);
	});
});

describe('Notifications — connect requests go to approvers', () => {
	const openRequest = {
		session_id: 'cs_waiting',
		state: 'created',
		vendor_key: 'github',
		vendor_display_name: 'GitHub',
		agent_id: 'agnt_pending_9',
		requested_by_actor_id: 'agnt_pending_9',
		reason: null,
		connected_as: null,
		error_code: null,
		created_at: '2026-01-01T00:00:00Z',
		credential_id: 'cred_pending',
	};

	it.each([
		['admin', ['org:admin'], true],
		['credentials:write and agents:write', [...MEMBER_DEFAULTS, 'credentials:write'], true],
		[
			'credentials:write without agents:write',
			[...without('agents:write'), 'credentials:write'],
			false,
		],
		['agents:write without credentials:write', MEMBER_DEFAULTS, false],
	] as const)('%s', async (_label, permissions, listed) => {
		worker.use(
			http.get('/users/me', () => HttpResponse.json(viewer(permissions))),
			http.get('/connect-sessions', ({ request }) => {
				const state = new URL(request.url).searchParams.get('state');
				const rows = !state || state === 'created' ? [openRequest] : [];
				return HttpResponse.json({ data: rows, has_more: false, next_cursor: null });
			}),
		);
		trackSources();
		renderMenu({ withConnectProbe: true });

		const user = userEvent.setup();
		await user.click(await screen.findByRole('button', { name: /^Notifications/ }));
		const dialog = screen.getByRole('dialog', { name: /Notifications/ });
		await within(dialog).findByText('waiting-bot is waiting for approval');
		// The connect-request read is independent of the pending-agents read, so
		// the agent row alone says nothing about whether it has landed yet.
		await screen.findByTestId('connect-requests-settled');
		const row = within(dialog).queryByText(/is waiting for you to connect GitHub/);
		if (listed) expect(row).toBeInTheDocument();
		else expect(row).toBeNull();
	});
});
