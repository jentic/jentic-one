/**
 * The Inbox's Decided view: the Waiting · Decided switch, how each ending
 * reads (who decided, where an approved call went, the deny reason), a waiting
 * call past its window read as expired, paging, a read that finds only
 * waiting rows reading on, and a row opening the call in the review pop-up.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { page } from 'vitest/browser';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Routes, Route, useLocation, useNavigate } from 'react-router';
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query';
import { render, screen, waitFor, within, userEvent, checkA11y } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { AgentRail } from '@/shared/app/rail/AgentRail';
import { RAIL_TAB_STORAGE_KEY } from '@/shared/app/rail/railPreferences';
import { AgentStreamProvider, RAIL_COLLAPSED_STORAGE_KEY } from '@/shared/lib/agentStream';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { clearToken, setToken, sharedQueryKeys, type HeldExecutionResponse } from '@/shared/api';
import { useRefreshApprovalSurfaces } from '@/shared/approvals';

/** The mock signed-in user (`/users/me`). */
const VIEWER = '00000000-0000-0000-0000-000000000001';
const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

let n = 0;
function heldCall(overrides: Partial<HeldExecutionResponse> = {}): HeldExecutionResponse {
	const id = overrides.id ?? `held_${++n}`;
	return {
		id,
		agent_id: 'agnt_billing',
		credential_id: 'cred_stripe',
		api_vendor: 'stripe',
		api_name: 'stripe-api',
		api_version: '2024-01-01',
		operation_id: 'PostCharges',
		method: 'POST',
		path: '/v1/charges',
		state: 'pending' as HeldExecutionResponse['state'],
		execution_status: 'held',
		expires_at: new Date(Date.now() + 23 * 3_600_000).toISOString(),
		review_url: `https://jentic.example/app/agents?review=${id}`,
		created_at: minutesAgo(15),
		_links: { self: `/executions/holds/${id}` },
		...overrides,
	};
}

const decided = (
	state: 'approved' | 'denied' | 'expired' | 'withdrawn',
	overrides: Partial<HeldExecutionResponse> = {},
) =>
	heldCall({
		state: state as HeldExecutionResponse['state'],
		execution_status: (state === 'approved'
			? 'completed'
			: 'failed') as HeldExecutionResponse['execution_status'],
		decided_at: state === 'approved' || state === 'denied' ? minutesAgo(90) : null,
		decided_by: state === 'approved' || state === 'denied' ? VIEWER : null,
		...overrides,
	});

let rows: HeldExecutionResponse[];
const reads: (string | null)[] = [];

function LocationProbe() {
	const loc = useLocation();
	const navigate = useNavigate();
	const qc = useQueryClient();
	// What a decision refreshes, and what an agent or OAuth event refreshes.
	const refreshAfterDecision = useRefreshApprovalSurfaces();
	return (
		<>
			<div data-testid="location">{loc.pathname + loc.search}</div>
			<button type="button" onClick={() => void navigate('/agents?inbox=1')}>
				Follow an Inbox link
			</button>
			<button type="button" onClick={refreshAfterDecision}>
				Refresh after a decision
			</button>
			<button
				type="button"
				onClick={() =>
					void qc.invalidateQueries({ queryKey: sharedQueryKeys.attentionRoot })
				}
			>
				Refresh the attention surfaces
			</button>
		</>
	);
}

function renderRail() {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<AuthProvider>
				<MemoryRouter initialEntries={['/agents']}>
					<AgentStreamProvider live={false}>
						<Routes>
							<Route path="/*" element={<AgentRail />} />
						</Routes>
						<LocationProbe />
					</AgentStreamProvider>
				</MemoryRouter>
			</AuthProvider>
		</QueryClientProvider>,
	);
}

/** Open the rail's Inbox on its Decided view; returns the list. */
async function openDecided(user: ReturnType<typeof userEvent.setup>) {
	await user.click(await screen.findByRole('button', { name: 'Decided' }));
	return screen.findByRole('list', { name: 'Decided calls' });
}

beforeEach(async () => {
	window.localStorage.clear();
	window.localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, '0');
	window.localStorage.setItem(RAIL_TAB_STORAGE_KEY, 'inbox');
	setToken('mock-access-token');
	reads.length = 0;
	rows = [];
	worker.use(
		// Newest first, paged by an offset cursor, filtered by state: the
		// endpoint's own contract.
		http.get('/executions/holds', ({ request }) => {
			const params = new URL(request.url).searchParams;
			const state = params.get('state');
			if (!state) reads.push(params.get('cursor'));
			const all = rows
				.filter((r) => !state || r.state === state)
				.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
			const limit = Number(params.get('limit') ?? 25);
			const offset = Number(params.get('cursor') ?? 0);
			const more = offset + limit < all.length;
			return HttpResponse.json({
				data: all.slice(offset, offset + limit),
				has_more: more,
				next_cursor: more ? String(offset + limit) : null,
			});
		}),
		http.get('/actors', () =>
			HttpResponse.json({
				data: [
					{ id: 'agnt_billing', actor_type: 'agent', name: 'billing-ops', active: true },
					{
						id: 'agnt_release',
						actor_type: 'agent',
						name: 'release-manager',
						active: true,
					},
					{ id: 'usr_priya', actor_type: 'user', name: 'Priya Shah', active: true },
				],
				has_more: false,
				next_cursor: null,
			}),
		),
		http.get('/apis/:vendor/:name/:version/operations', () =>
			HttpResponse.json({
				data: [
					{ operation_id: 'PostCharges', name: 'Create a charge' },
					{ operation_id: 'PostRefunds', name: 'Create a refund' },
					{ operation_id: 'PostPayouts', name: 'Create a payout' },
				],
				has_more: false,
				next_cursor: null,
			}),
		),
	);
	await page.viewport(1440, 900);
});

afterEach(() => {
	window.localStorage.clear();
	clearToken();
});

/** A row's lines, as one string each. */
function rowText(list: HTMLElement): string[] {
	return within(list)
		.getAllByRole('listitem')
		.map((li) => li.textContent ?? '');
}

describe('Inbox — Decided', () => {
	it('opens on Waiting; Decided lists every ending, and never a waiting call', async () => {
		rows = [
			heldCall({ id: 'held_waiting', created_at: minutesAgo(1) }),
			decided('approved', { id: 'held_ran', created_at: minutesAgo(120) }),
			decided('approved', {
				id: 'held_failed',
				operation_id: 'PostPayouts',
				execution_status: 'failed' as HeldExecutionResponse['execution_status'],
				decided_by: 'usr_priya',
				created_at: minutesAgo(130),
			}),
			decided('denied', {
				id: 'held_denied',
				operation_id: 'PostRefunds',
				decided_by: 'usr_priya',
				decision_reason: 'Support is already refunding this.',
				created_at: minutesAgo(140),
			}),
			decided('expired', {
				id: 'held_expired',
				expires_at: minutesAgo(60),
				created_at: minutesAgo(25 * 60),
			}),
			decided('withdrawn', {
				id: 'held_withdrawn',
				agent_id: 'agnt_release',
				created_at: minutesAgo(26 * 60),
			}),
		];
		const user = userEvent.setup();
		renderRail();

		// Waiting first: the one waiting call.
		expect(await screen.findByRole('region', { name: 'billing-ops' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Waiting' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);

		const list = await openDecided(user);
		await waitFor(() => expect(within(list).getAllByRole('listitem')).toHaveLength(5));
		await waitFor(() =>
			expect(rowText(list)[0]).toMatch(/Create a charge.*Approved by you · completed/),
		);
		expect(rowText(list)[1]).toMatch(/Create a payout.*Approved by Priya Shah · failed/);
		expect(rowText(list)[2]).toMatch(
			/Create a refund.*Denied by Priya Shah.*billing-ops.*Support is already refunding this\./,
		);
		expect(rowText(list)[3]).toMatch(/Expired, nobody decided/);
		expect(rowText(list)[4]).toMatch(/Withdrawn by the agent.*release-manager/);
		expect(list.querySelector('[data-held-id="held_waiting"]')).toBeNull();
		await checkA11y(document.body);
	});

	it('reads a waiting call past its window as expired', async () => {
		rows = [heldCall({ id: 'held_lapsed', expires_at: minutesAgo(1) })];
		const user = userEvent.setup();
		renderRail();
		const list = await openDecided(user);
		expect(await within(list).findByText('Expired, nobody decided')).toBeInTheDocument();
	});

	it('pages with Show more', async () => {
		rows = Array.from({ length: 30 }, (_, i) =>
			decided('approved', { id: `held_${i}`, created_at: minutesAgo(10 + i) }),
		);
		const user = userEvent.setup();
		renderRail();
		const list = await openDecided(user);
		await waitFor(() => expect(within(list).getAllByRole('listitem')).toHaveLength(25));
		await user.click(screen.getByRole('button', { name: 'Show more' }));
		await waitFor(() => expect(within(list).getAllByRole('listitem')).toHaveLength(30));
		expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull();
	});

	it('reads on past a page of waiting calls rather than say nothing was decided', async () => {
		rows = [
			...Array.from({ length: 25 }, (_, i) =>
				heldCall({ id: `held_wait_${i}`, created_at: minutesAgo(1 + i) }),
			),
			decided('denied', { id: 'held_old', created_at: minutesAgo(600) }),
		];
		const user = userEvent.setup();
		renderRail();
		const list = await openDecided(user);
		await waitFor(() => expect(within(list).getAllByRole('listitem')).toHaveLength(1));
		expect(reads).toEqual([null, '25']);
		expect(screen.queryByText('Nothing decided yet')).toBeNull();
	});

	it('says so when nothing was decided, and an empty Waiting points to Decided', async () => {
		rows = [];
		const user = userEvent.setup();
		renderRail();
		await user.click(await screen.findByRole('button', { name: 'See decided calls' }));
		expect(await screen.findByText('Nothing decided yet')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Decided' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
	});

	it('an Inbox link brings an open Decided view back to Waiting', async () => {
		rows = [heldCall({ id: 'held_waiting' }), decided('approved', { id: 'held_ran' })];
		const user = userEvent.setup();
		renderRail();
		await openDecided(user);
		await user.click(screen.getByRole('button', { name: 'Follow an Inbox link' }));
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Waiting' })).toHaveAttribute(
				'aria-pressed',
				'true',
			),
		);
		expect(await screen.findByRole('region', { name: 'billing-ops' })).toBeInTheDocument();
	});

	it('keeps Show more when a read finds only waiting calls with more behind them', async () => {
		rows = [
			...Array.from({ length: 130 }, (_, i) =>
				heldCall({ id: `held_wait_${i}`, created_at: minutesAgo(1 + i) }),
			),
			decided('denied', { id: 'held_old', created_at: minutesAgo(600) }),
		];
		const user = userEvent.setup();
		renderRail();
		await user.click(await screen.findByRole('button', { name: 'Decided' }));
		const more = await screen.findByRole('button', { name: 'Show more' });
		expect(screen.queryByText('Nothing decided yet')).toBeNull();
		await user.click(more);
		const list = screen.getByRole('list', { name: 'Decided calls' });
		await waitFor(() => expect(within(list).getAllByRole('listitem')).toHaveLength(1));
	});

	it('a decision brings the call into Decided; other attention events read nothing', async () => {
		rows = [heldCall({ id: 'held_next' })];
		const user = userEvent.setup();
		renderRail();
		await user.click(await screen.findByRole('button', { name: 'Decided' }));
		expect(await screen.findByText('Nothing decided yet')).toBeInTheDocument();
		const before = reads.length;

		// An agent or OAuth event refreshes the attention surfaces only.
		await user.click(screen.getByRole('button', { name: 'Refresh the attention surfaces' }));
		await new Promise((resolve) => setTimeout(resolve, 300));
		expect(reads.length).toBe(before);

		// The call is decided: what a decision refreshes re-reads the history.
		rows = [decided('approved', { id: 'held_next' })];
		await user.click(screen.getByRole('button', { name: 'Refresh after a decision' }));
		const list = await screen.findByRole('list', { name: 'Decided calls' });
		await waitFor(() =>
			expect(
				within(list).getByRole('button', { name: /Approved by you/ }),
			).toBeInTheDocument(),
		);
	});

	it('a row opens the call in the review pop-up', async () => {
		rows = [decided('denied', { id: 'held_denied' })];
		const user = userEvent.setup();
		renderRail();
		const list = await openDecided(user);
		await user.click(await within(list).findByRole('button', { name: /Create a charge/ }));
		await waitFor(() =>
			expect(screen.getByTestId('location')).toHaveTextContent('review=held_denied'),
		);
	});
});
