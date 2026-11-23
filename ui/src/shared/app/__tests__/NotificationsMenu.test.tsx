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

function renderMenu(route = '/agents') {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<MemoryRouter initialEntries={[route]}>
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
let approvalRows: ReturnType<typeof heldCall>[];
let requiresActionEvents: EventResponse[];

/** A pending held execution: an agent's call an Ask rule holds. */
function heldCall(overrides: Record<string, unknown> = {}) {
	const id = (overrides.id as string | undefined) ?? 'held_1';
	return {
		id,
		agent_id: 'agnt_scout',
		credential_id: 'cred_stripe',
		api_vendor: 'api.stripe.com',
		api_name: 'payments',
		api_version: '2024-01-01',
		operation_id: null,
		method: 'POST',
		path: '/v1/charges',
		matched_rule_id: 'apr_1',
		state: 'pending',
		execution_status: 'held',
		expires_at: new Date(Date.now() + 60 * 60_000).toISOString(),
		decided_at: null,
		decided_by: null,
		decision_reason: null,
		trace_id: null,
		execution_id: null,
		created_at: new Date(Date.now() - 15 * 60_000).toISOString(),
		updated_at: null,
		_links: { self: `/executions/holds/${id}`, withdraw: `/executions/holds/${id}:withdraw` },
		...overrides,
	};
}

beforeEach(() => {
	window.localStorage.clear();
	setToken('test-token');
	approved = [];
	connectRows = [];
	credentialRows = [];
	approvalRows = [];
	requiresActionEvents = [failure];
	worker.use(
		http.get('/executions/holds', ({ request }) => {
			const state = new URL(request.url).searchParams.get('state');
			return HttpResponse.json(page(approvalRows.filter((r) => !state || r.state === state)));
		}),
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
			return HttpResponse.json(page(requiresActionEvents));
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
		const bell = await screen.findByRole('button', { name: 'Notifications, 2 unread' });
		expect(bell).toHaveAttribute('aria-haspopup', 'dialog');
		expect(bell).toHaveAttribute('aria-expanded', 'false');
	});

	it('opens a grouped list — alerts before approvals — with no a11y violations', async () => {
		const user = userEvent.setup();
		const { container } = renderMenu();
		await user.click(await screen.findByRole('button', { name: /^Notifications, 2/ }));
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
		await user.click(await screen.findByRole('button', { name: /^Notifications, 2/ }));
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
		expect(screen.getByRole('button', { name: 'Notifications, 1 unread' })).toBeInTheDocument();
	});

	it('marks everything read: the badge clears, the rows stay, and it survives a remount', async () => {
		const user = userEvent.setup();
		const first = renderMenu();
		await user.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }));
		const dialog = screen.getByRole('dialog', { name: /Notifications/ });
		expect(dialog.querySelectorAll('[data-unread]')).toHaveLength(2);

		await user.click(within(dialog).getByRole('button', { name: 'Mark all as read' }));
		expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument();
		// Still waiting on the viewer, so still listed — just no longer unread.
		expect(within(dialog).getByText('Execution failed: slack.postMessage')).toBeInTheDocument();
		expect(dialog.querySelectorAll('[data-unread]')).toHaveLength(0);
		expect(within(dialog).queryByRole('button', { name: 'Mark all as read' })).toBeNull();

		first.unmount();
		renderMenu();
		await screen.findByRole('button', { name: 'Notifications' });
		await waitFor(() =>
			expect(screen.queryByRole('button', { name: /unread/ })).not.toBeInTheDocument(),
		);
	});

	it('counts an item that arrives after Mark all as read', async () => {
		const user = userEvent.setup();
		const first = renderMenu();
		await user.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }));
		await user.click(screen.getByRole('button', { name: 'Mark all as read' }));
		await screen.findByRole('button', { name: 'Notifications' });
		first.unmount();

		const later: EventResponse = {
			...failure,
			event_id: 'evt_fail_2',
			summary: 'Execution failed: github.getUser',
			created_at: new Date().toISOString(),
		};
		worker.use(
			http.get('/events', ({ request }) => {
				const url = new URL(request.url);
				if (url.searchParams.get('requires_action') !== 'true')
					return HttpResponse.json(page([]));
				return HttpResponse.json(page([later, failure]));
			}),
		);
		// A fresh mount refetches; only the new alert is unread.
		renderMenu();
		expect(
			await screen.findByRole('button', { name: 'Notifications, 1 unread' }),
		).toBeInTheDocument();
	});

	it('closes when a row link is followed', async () => {
		const user = userEvent.setup();
		renderMenu();
		await user.click(await screen.findByRole('button', { name: /^Notifications, 2/ }));
		await user.click(screen.getByRole('link', { name: 'Review' }));
		expect(screen.getByTestId('location')).toHaveTextContent('/agents?agent=agnt_waiting');
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
	});

	it('closes on Escape', async () => {
		const user = userEvent.setup();
		renderMenu();
		await user.click(await screen.findByRole('button', { name: /^Notifications, 2/ }));
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
			await user.click(await screen.findByRole('button', { name: /^Notifications, 3/ }));
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
			await user.click(await screen.findByRole('button', { name: /^Notifications, 4/ }));
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

	describe('held calls waiting for you', () => {
		/** The Stripe API's operation names, as its live spec lists them. */
		function serveOperationNames() {
			worker.use(
				http.get('/apis/:vendor/:name/:version/operations', () =>
					HttpResponse.json({
						data: [
							{ operation_id: 'createCharge', name: 'Create a charge' },
							{ operation_id: 'createRefund', name: 'Create a refund' },
						],
						has_more: false,
						next_cursor: null,
					}),
				),
			);
		}

		it('lists one row per call, oldest first, ahead of the alerts', async () => {
			serveOperationNames();
			approvalRows = [
				heldCall({
					id: 'held_new',
					operation_id: 'createCharge',
					created_at: new Date(Date.now() - 60_000).toISOString(),
				}),
				heldCall({ id: 'held_old', path: '/v1/refunds', operation_id: 'createRefund' }),
				heldCall({ id: 'held_route', path: '/v1/payouts' }),
			];
			// The request event mirrors the live row; the row wins, so it is not
			// listed (or counted) twice.
			requiresActionEvents = [
				failure,
				{
					_links: { self: '/events/evt_held' },
					event_id: 'evt_held',
					type: 'execution.approval_requested',
					severity: 'warning' as EventResponse['severity'],
					summary: 'Agent agnt_scout wants to POST /v1/charges',
					created_at: new Date(Date.now() - 60_000).toISOString(),
					requires_action: true,
					data: { held_execution_id: 'held_new', agent_id: 'agnt_scout' },
				},
			];
			const user = userEvent.setup();
			renderMenu();
			// A failure, a pending agent and three held calls.
			await user.click(await screen.findByRole('button', { name: /^Notifications, 5/ }));
			const dialog = screen.getByRole('dialog', { name: /Notifications/ });
			const waiting = within(dialog).getByRole('region', { name: 'Waiting for you' });
			await within(waiting).findByText('scout-bot wants to run “Create a charge”');
			const rows = within(waiting).getAllByRole('listitem');
			expect(rows.map((row) => row.querySelector('p')?.textContent)).toEqual([
				'Unread: scout-bot wants to run “Create a refund”',
				'Unread: scout-bot wants to run POST /v1/payouts',
				'Unread: scout-bot wants to run “Create a charge”',
			]);
			expect(rows[0]).toHaveTextContent(/15m ago · expires in 1 h/);
			expect(rows.every((row) => within(row).queryByRole('button', { name: 'Review' }))).toBe(
				true,
			);
			// Held calls lead the list.
			const sections = within(dialog).getAllByRole('region');
			expect(sections[0]).toBe(waiting);
			expect(within(dialog).queryByText(/Agent agnt_scout wants to POST/)).toBeNull();
			expect(within(dialog).queryByText(/See all/)).toBeNull();
			await checkA11y(dialog);
		});

		it("Review closes the menu and opens the call's review pop-up", async () => {
			approvalRows = [heldCall({ id: 'held_old', path: '/v1/refunds' })];
			const user = userEvent.setup();
			renderMenu();
			await user.click(await screen.findByRole('button', { name: /^Notifications, 3/ }));
			const dialog = screen.getByRole('dialog', { name: /Notifications/ });
			const row = (
				await within(dialog).findByText('scout-bot wants to run POST /v1/refunds')
			).closest('li')!;
			await user.click(within(row).getByRole('button', { name: 'Review' }));
			expect(screen.queryByRole('dialog', { name: /Notifications/ })).toBeNull();
			expect(screen.getByTestId('location')).toHaveTextContent('/agents?review=held_old');
		});

		it('drops a call whose window lapsed', async () => {
			approvalRows = [
				heldCall(),
				heldCall({
					id: 'held_lapsed',
					agent_id: 'agnt_other',
					expires_at: new Date(Date.now() - 60_000).toISOString(),
				}),
			];
			const user = userEvent.setup();
			renderMenu();
			await user.click(await screen.findByRole('button', { name: /^Notifications, 3/ }));
			const dialog = screen.getByRole('dialog', { name: /Notifications/ });
			expect(
				await within(dialog).findByText('scout-bot wants to run POST /v1/charges'),
			).toBeInTheDocument();
			expect(within(dialog).queryByText(/agnt_other/)).toBeNull();
		});

		it('lists the first five and links the rest to the inbox', async () => {
			approvalRows = Array.from({ length: 7 }, (_, i) =>
				heldCall({
					id: `held_${i}`,
					path: `/v1/charges/${i}`,
					created_at: new Date(Date.now() - (20 - i) * 60_000).toISOString(),
				}),
			);
			const user = userEvent.setup();
			renderMenu('/dashboard');
			await user.click(await screen.findByRole('button', { name: /^Notifications, 9/ }));
			const dialog = screen.getByRole('dialog', { name: /Notifications/ });
			const waiting = within(dialog).getByRole('region', { name: 'Waiting for you' });
			await within(waiting).findByText('scout-bot wants to run POST /v1/charges/0');
			expect(within(waiting).getAllByRole('button', { name: 'Review' })).toHaveLength(5);
			expect(within(waiting).getByRole('heading')).toHaveTextContent('Waiting for you7');
			expect(
				within(waiting).queryByText('scout-bot wants to run POST /v1/charges/5'),
			).toBeNull();

			await user.click(
				within(waiting).getByRole('button', { name: 'See all 7 in the inbox' }),
			);
			expect(screen.queryByRole('dialog', { name: /Notifications/ })).toBeNull();
			expect(screen.getByTestId('location')).toHaveTextContent('/dashboard?inbox=1');
		});

		it('opens the inbox over the Agents page from a page without the rail', async () => {
			approvalRows = Array.from({ length: 6 }, (_, i) =>
				heldCall({ id: `held_${i}`, path: `/v1/charges/${i}` }),
			);
			const user = userEvent.setup();
			renderMenu('/monitor');
			await user.click(await screen.findByRole('button', { name: /^Notifications, 8/ }));
			const dialog = screen.getByRole('dialog', { name: /Notifications/ });
			await user.click(
				await within(dialog).findByRole('button', { name: 'See all 6 in the inbox' }),
			);
			expect(screen.getByTestId('location')).toHaveTextContent('/agents?inbox=1');
		});
	});
});
