import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, useLocation } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, userEvent, checkA11y, within } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { NotificationsMenu } from '@/shared/app/NotificationsMenu';
import {
	AgentStreamProvider,
	RAIL_AUDIO_STORAGE_KEY,
	TOAST_SCOPE_STORAGE_KEY,
} from '@/shared/lib/agentStream';
import {
	clearToken,
	setToken,
	type ConnectSessionSummaryResponse,
	type EventResponse,
} from '@/shared/api';

function LocationProbe() {
	const loc = useLocation();
	return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

function renderMenu() {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<MemoryRouter initialEntries={['/agents']}>
				<AgentStreamProvider live={false}>
					<NotificationsMenu />
					<LocationProbe />
				</AgentStreamProvider>
			</MemoryRouter>
		</QueryClientProvider>,
	);
}

const failure: EventResponse = {
	_links: { self: '/events/evt_fail_1', execution: '/executions/exec_fail_1' },
	event_id: 'evt_fail_1',
	type: 'execution.failed',
	severity: 'critical' as EventResponse['severity'],
	summary: 'Execution failed: slack.postMessage',
	created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
	requires_action: true,
	data: { execution_id: 'exec_fail_1' },
};

const pendingAgent = {
	id: 'agnt_waiting',
	name: 'inbox-triage-bot',
	status: 'pending',
	created_at: new Date(Date.now() - 60 * 60_000).toISOString(),
	_links: { self: '/agents/agnt_waiting' },
};

const page = <T,>(data: T[]) => ({ data, has_more: false, next_cursor: null });

/** An open connect session an agent started (it is both target and requester). */
function connectRow(
	overrides: Partial<Omit<ConnectSessionSummaryResponse, 'state'>> & { state?: string } = {},
) {
	return {
		session_id: 'cs_gh',
		state: 'created',
		vendor_key: 'github',
		vendor_display_name: 'GitHub',
		agent_id: 'agnt_scout',
		requested_by_actor_id: 'agnt_scout',
		reason: 'Needs repo read access',
		connected_as: null,
		error_code: null,
		created_at: new Date(Date.now() - 10 * 60_000).toISOString(),
		credential_id: 'cred_gh_pending',
		...overrides,
	};
}

let approved: string[];
let connectRows: ReturnType<typeof connectRow>[];
let credentialRows: unknown[];

beforeEach(() => {
	window.localStorage.clear();
	setToken('test-token');
	approved = [];
	connectRows = [];
	credentialRows = [];
	worker.use(
		http.get('/connect-sessions', ({ request }) => {
			const state = new URL(request.url).searchParams.get('state');
			return HttpResponse.json(page(connectRows.filter((r) => !state || r.state === state)));
		}),
		http.get('/actors', () =>
			HttpResponse.json(
				page([{ id: 'agnt_scout', actor_type: 'agent', name: 'scout-bot', active: true }]),
			),
		),
		http.get('/events', ({ request }) => {
			const url = new URL(request.url);
			if (url.searchParams.get('requires_action') !== 'true')
				return HttpResponse.json(page([]));
			return HttpResponse.json(page([failure]));
		}),
		http.get('/agents', () =>
			HttpResponse.json(page(approved.includes(pendingAgent.id) ? [] : [pendingAgent])),
		),
		http.post('/agents/:agentId', ({ params }) => {
			const id = String(params.agentId).replace(/:approve$/, '');
			approved.push(id);
			return HttpResponse.json({ ...pendingAgent, status: 'active' });
		}),
		http.get('/credentials', () => HttpResponse.json(page(credentialRows))),
	);
});

afterEach(() => {
	window.localStorage.clear();
	clearToken();
});

describe('NotificationsMenu', () => {
	it('badges the bell with the count and names it for assistive tech', async () => {
		renderMenu();
		const bell = await screen.findByRole('button', { name: 'Notifications (2 need you)' });
		expect(bell).toHaveAttribute('aria-haspopup', 'dialog');
		expect(bell).toHaveAttribute('aria-expanded', 'false');
	});

	it('opens a grouped list — alerts before approvals — with no a11y violations', async () => {
		const user = userEvent.setup();
		const { container } = renderMenu();
		await user.click(await screen.findByRole('button', { name: /^Notifications \(2/ }));
		const dialog = screen.getByRole('dialog', { name: /Notifications/ });
		const sections = within(dialog).getAllByRole('region');
		expect(sections.map((s) => s.getAttribute('aria-label'))).toEqual(['Alerts', 'Approvals']);
		expect(within(dialog).getByText('Execution failed: slack.postMessage')).toBeInTheDocument();
		expect(
			within(dialog).getByText(/inbox-triage-bot is waiting for approval/),
		).toBeInTheDocument();
		await checkA11y(container);
	});

	it('approves an agent inline, dropping its row; the alert stays as a View link', async () => {
		const user = userEvent.setup();
		renderMenu();
		await user.click(await screen.findByRole('button', { name: /^Notifications \(2/ }));
		const dialog = screen.getByRole('dialog', { name: /Notifications/ });

		// An alert is append-only history — it offers a View link, not an inline
		// dismiss, and persists until it ages out of the recent window.
		const alerts = within(dialog).getByRole('region', { name: 'Alerts' });
		expect(within(alerts).getByRole('link', { name: 'View' })).toBeInTheDocument();
		expect(within(alerts).queryByRole('button', { name: 'Acknowledge' })).toBeNull();

		await user.click(within(dialog).getByRole('button', { name: 'Approve' }));
		await waitFor(() => expect(approved).toEqual(['agnt_waiting']));
		// The approval row drops; the alert remains.
		await waitFor(() =>
			expect(within(dialog).queryByText(/inbox-triage-bot is waiting/)).toBeNull(),
		);
		expect(within(dialog).getByText('Execution failed: slack.postMessage')).toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: 'Notifications (1 needs you)' }),
		).toBeInTheDocument();
	});

	it('closes when a row link is followed', async () => {
		const user = userEvent.setup();
		renderMenu();
		await user.click(await screen.findByRole('button', { name: /^Notifications \(2/ }));
		await user.click(screen.getByRole('link', { name: 'Review' }));
		expect(screen.getByTestId('location')).toHaveTextContent('/agents?agent=agnt_waiting');
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
	});

	it('closes on Escape', async () => {
		const user = userEvent.setup();
		renderMenu();
		await user.click(await screen.findByRole('button', { name: /^Notifications \(2/ }));
		expect(screen.getByRole('dialog')).toBeInTheDocument();
		await user.keyboard('{Escape}');
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
	});

	it('owns the pop-up and sound preferences, persisted to localStorage', async () => {
		const user = userEvent.setup();
		const { container } = renderMenu();
		await user.click(await screen.findByRole('button', { name: /^Notifications/ }));
		await user.click(screen.getByRole('button', { name: 'Notification settings' }));

		const dialog = screen.getByRole('dialog', { name: 'Notification settings' });
		// Failures always pop up, so a fresh user sees the quietest option picked.
		expect(within(dialog).getByRole('radio', { name: /Failures only/ })).toBeChecked();
		await user.click(within(dialog).getByRole('radio', { name: /Every event/ }));
		expect(window.localStorage.getItem(TOAST_SCOPE_STORAGE_KEY)).toBe('all');

		const sound = within(dialog).getByRole('switch', { name: /Sound on failures/ });
		expect(sound).not.toBeChecked();
		await user.click(sound);
		await waitFor(() => expect(window.localStorage.getItem(RAIL_AUDIO_STORAGE_KEY)).toBe('1'));
		await checkA11y(container);

		await user.click(within(dialog).getByRole('button', { name: 'Back to notifications' }));
		expect(screen.getByRole('dialog', { name: /^Notifications/ })).toBeInTheDocument();
	});
	describe('agents waiting on a connect request', () => {
		it("collapses an agent's open requests into one approval row with a deep link", async () => {
			connectRows = [
				connectRow(),
				connectRow({
					session_id: 'cs_slack',
					state: 'polling',
					vendor_key: 'slack',
					vendor_display_name: 'Slack',
					credential_id: 'cred_slack_pending',
					created_at: new Date(Date.now() - 2 * 60_000).toISOString(),
				}),
				// A session a human started from their own dialog: nobody waits on it.
				connectRow({
					session_id: 'cs_self',
					agent_id: 'agnt_scout',
					requested_by_actor_id: 'usr_me',
					vendor_display_name: 'Linear',
				}),
			];
			const user = userEvent.setup();
			renderMenu();
			await user.click(await screen.findByRole('button', { name: /^Notifications \(3/ }));
			const dialog = screen.getByRole('dialog', { name: /Notifications/ });
			const approvals = within(dialog).getByRole('region', { name: 'Approvals' });
			const row = await within(approvals).findByText(
				'scout-bot is waiting for you to connect GitHub and Slack',
			);
			expect(row).toBeInTheDocument();
			expect(within(dialog).queryByText(/Linear/)).toBeNull();

			// The link opens the longest-waiting request, with no poll token.
			const item = row.closest('li')!;
			await user.click(within(item).getByRole('link', { name: 'Review' }));
			expect(screen.getByTestId('location')).toHaveTextContent(
				'/agents?agent=agnt_scout&approve=cs_gh',
			);
			expect(screen.getByTestId('location').textContent).not.toContain('poll_token');
		});

		it("does not repeat a request's pending credential as an unfinished sign-in", async () => {
			connectRows = [connectRow()];
			credentialRows = [
				{
					credential_id: 'cred_gh_pending',
					name: 'GitHub (connecting…)',
					type: 'oauth2',
					provider: 'direct_oauth2',
					api: { vendor: 'github', name: 'default', version: null },
					details: { grant_type: 'authorization_code', connected: false },
					created_at: new Date().toISOString(),
				},
				{
					credential_id: 'cred_other',
					name: 'Abandoned Slack',
					type: 'oauth2',
					provider: 'direct_oauth2',
					api: { vendor: 'slack', name: 'default', version: null },
					details: { grant_type: 'authorization_code', connected: false },
					created_at: new Date().toISOString(),
				},
			];
			const user = userEvent.setup();
			renderMenu();
			await user.click(await screen.findByRole('button', { name: /^Notifications \(4/ }));
			const dialog = screen.getByRole('dialog', { name: /Notifications/ });
			expect(
				await within(dialog).findByText("Abandoned Slack sign-in isn't finished"),
			).toBeInTheDocument();
			expect(within(dialog).queryByText(/GitHub \(connecting…\) sign-in/)).toBeNull();
			expect(
				within(dialog).getByText('scout-bot is waiting for you to connect GitHub'),
			).toBeInTheDocument();
		});
	});
});
