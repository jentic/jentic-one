/**
 * A caller without event access (`events:read` / `org:admin`) must get a clear
 * state, not a stream refused over and over: the rail fetches nothing and says
 * so, a 401/403 from the stream is the last attempt, and the bell never claims
 * "all caught up" while a source it reads has failed.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router';
import { page } from 'vitest/browser';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, userEvent, within } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { AuthProvider } from '@/shared/auth';
import { AgentRail } from '@/shared/app/rail/AgentRail';
import { NotificationsMenu } from '@/shared/app/NotificationsMenu';
import { AgentStreamProvider, RAIL_COLLAPSED_STORAGE_KEY } from '@/shared/lib/agentStream';
import { clearToken, setToken } from '@/shared/api';

const emptyPage = { data: [], has_more: false, next_cursor: null };

function viewer(permissions: string[]) {
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

/** Count every request to the event feed, and answer the stream with `streamStatus`. */
function trackEventReads(streamStatus: number) {
	const calls = { list: 0, stream: 0 };
	worker.use(
		http.get('/events', () => {
			calls.list += 1;
			return HttpResponse.json(emptyPage);
		}),
		http.get('/events/stream', () => {
			calls.stream += 1;
			return new HttpResponse(null, { status: streamStatus });
		}),
	);
	return calls;
}

function renderShell(ui: React.ReactNode, { withAuth = true } = {}) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
	});
	const tree = (
		<MemoryRouter initialEntries={['/dashboard']}>
			<AgentStreamProvider live={true}>{ui}</AgentStreamProvider>
		</MemoryRouter>
	);
	return render(
		<QueryClientProvider client={queryClient}>
			{withAuth ? <AuthProvider>{tree}</AuthProvider> : tree}
		</QueryClientProvider>,
	);
}

beforeEach(async () => {
	// The docked rail only shows at xl+.
	await page.viewport(1440, 900);
	window.localStorage.clear();
	// Open rail, so its body (and the forbidden state) renders.
	window.localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, '0');
	setToken('test-token');
	worker.use(
		http.get('/agents', () => HttpResponse.json(emptyPage)),
		http.get('/credentials', () => HttpResponse.json(emptyPage)),
	);
});

afterEach(() => {
	window.localStorage.clear();
	clearToken();
});

describe('Activity rail — caller without event access', () => {
	it('makes no /events or /events/stream call and says there is no access', async () => {
		worker.use(http.get('/users/me', () => HttpResponse.json(viewer([]))));
		const calls = trackEventReads(200);
		renderShell(<AgentRail />);

		const rail = await screen.findByRole('complementary', { name: 'Activity' });
		expect(await within(rail).findByTestId('rail-forbidden')).toHaveTextContent(
			'No access to activity',
		);
		expect(
			within(rail).getByRole('img', { name: 'No access to activity' }),
		).toBeInTheDocument();
		expect(within(rail).queryByText('Reconnecting…')).not.toBeInTheDocument();
		// Long enough for a backlog fetch and the first reconnect to have fired.
		await new Promise((r) => setTimeout(r, 1500));
		expect(calls).toEqual({ list: 0, stream: 0 });
	});

	it('reads events with events:read', async () => {
		worker.use(http.get('/users/me', () => HttpResponse.json(viewer(['events:read']))));
		const calls = trackEventReads(503);
		renderShell(<AgentRail />);
		await waitFor(() => expect(calls.stream).toBeGreaterThan(0));
		expect(calls.list).toBeGreaterThan(0);
		expect(screen.queryByTestId('rail-forbidden')).not.toBeInTheDocument();
	});

	it.each([403, 401])('stops after one stream attempt answered %i', async (status) => {
		// No AuthProvider: the viewer is unknown, so the request goes out and the
		// server's answer decides.
		const calls = trackEventReads(status);
		renderShell(<AgentRail />, { withAuth: false });

		expect(await screen.findByTestId('rail-forbidden')).toBeInTheDocument();
		// The reconnect backoff starts at 1s; wait past it.
		await new Promise((r) => setTimeout(r, 2500));
		expect(calls.stream).toBe(1);
		expect(screen.queryByText('Reconnecting…')).not.toBeInTheDocument();
	});
});

describe('NotificationsMenu — event access and failed sources', () => {
	it('skips the alerts source for a caller without event access, without calling it failed', async () => {
		const user = userEvent.setup();
		worker.use(http.get('/users/me', () => HttpResponse.json(viewer([]))));
		const calls = trackEventReads(403);
		renderShell(<NotificationsMenu />);

		await user.click(await screen.findByRole('button', { name: 'Notifications' }));
		const dialog = screen.getByRole('dialog', { name: /Notifications/ });
		expect(await within(dialog).findByText("You're all caught up")).toBeInTheDocument();
		expect(within(dialog).queryByText(/Couldn't load/)).not.toBeInTheDocument();
		expect(calls.list).toBe(0);
	});

	it('never says "all caught up" while a source failed', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/users/me', () => HttpResponse.json(viewer(['events:read']))),
			http.get('/events', () => new HttpResponse(null, { status: 500 })),
			http.get('/events/stream', () => new HttpResponse(null, { status: 403 })),
		);
		renderShell(<NotificationsMenu />);

		await user.click(await screen.findByRole('button', { name: 'Notifications' }));
		const dialog = screen.getByRole('dialog', { name: /Notifications/ });
		expect(await within(dialog).findByText(/Couldn't load alerts/)).toBeInTheDocument();
		expect(within(dialog).getByText('Nothing to show right now')).toBeInTheDocument();
		expect(within(dialog).queryByText("You're all caught up")).not.toBeInTheDocument();
	});
});
