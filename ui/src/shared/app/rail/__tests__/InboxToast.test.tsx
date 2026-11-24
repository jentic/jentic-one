/**
 * The attention toast beside the rail: its summary and arrival forms, the
 * rules that hide it (rail open on Inbox, nothing waiting, dismissed until the
 * next arrival), and its buttons — Review opens a call's review pop-up, Open
 * inbox opens the rail on its Inbox tab.
 */
import { describe, it, expect, beforeEach, afterEach, onTestFinished } from 'vitest';
import { page } from 'vitest/browser';
import { act } from 'react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, useLocation } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
	render,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
	settleAnimations,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { AgentRail } from '@/shared/app/rail/AgentRail';
import { INBOX_TOAST_DISMISSED_KEY, InboxToast } from '@/shared/app/rail/InboxToast';
import { RAIL_TAB_STORAGE_KEY } from '@/shared/app/rail/railPreferences';
import { pendingApprovalsKey } from '@/shared/approvals';
import { ATTENTION_TILE_CLASS } from '@/shared/approvals/attentionTone';
import { AgentStreamProvider, RAIL_COLLAPSED_STORAGE_KEY } from '@/shared/lib/agentStream';
import { setTheme } from '@/shared/lib/theme';
import { clearToken, setToken, type HeldExecutionResponse } from '@/shared/api';

const pageOf = <T,>(data: T[]) => ({ data, has_more: false, next_cursor: null });
const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

function heldCall(overrides: Partial<HeldExecutionResponse> = {}): HeldExecutionResponse {
	const id = overrides.id ?? 'held_1';
	return {
		id,
		agent_id: 'agnt_billing',
		credential_id: 'cred_stripe',
		api_vendor: 'stripe',
		api_name: 'stripe-api',
		api_version: '2024-01-01',
		operation_id: null,
		method: 'POST',
		path: '/v1/charges',
		state: 'pending' as HeldExecutionResponse['state'],
		execution_status: 'held',
		expires_at: new Date(Date.now() + 23 * 3_600_000).toISOString(),
		review_url: `https://jentic.example/app/agents/approvals/${id}`,
		created_at: minutesAgo(15),
		_links: { self: `/executions/holds/${id}` },
		...overrides,
	};
}

let rows: HeldExecutionResponse[];

function LocationProbe() {
	const loc = useLocation();
	return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

function renderShell(route = '/agents') {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
	});
	const view = render(
		<QueryClientProvider client={queryClient}>
			<MemoryRouter initialEntries={[route]}>
				<AgentStreamProvider live={false}>
					<div className="flex h-[600px]">
						<AgentRail />
					</div>
					<InboxToast />
					<LocationProbe />
				</AgentStreamProvider>
			</MemoryRouter>
		</QueryClientProvider>,
	);
	return { ...view, queryClient };
}

function locationSearch(): URLSearchParams {
	const text = screen.getByTestId('location').textContent ?? '';
	return new URLSearchParams(text.slice(text.indexOf('?')));
}

/** A new call joins the waiting list and the list is read again. */
async function arrive(queryClient: QueryClient, call: HeldExecutionResponse) {
	rows = [...rows, call];
	await act(() => queryClient.invalidateQueries({ queryKey: pendingApprovalsKey }));
}

beforeEach(async () => {
	window.localStorage.clear();
	window.sessionStorage.clear();
	setToken('test-token');
	rows = [
		heldCall({ id: 'held_payout', path: '/v1/payouts', created_at: minutesAgo(128) }),
		heldCall({ id: 'held_charge', created_at: minutesAgo(6) }),
		heldCall({
			id: 'held_release',
			agent_id: 'agnt_release',
			api_vendor: 'github',
			api_name: 'github-api',
			api_version: '1.1.4',
			method: 'POST',
			path: '/repos/acme/storefront/releases',
			created_at: minutesAgo(24),
		}),
	];
	worker.use(
		http.get('/executions/holds', () => HttpResponse.json(pageOf(rows))),
		http.get('/actors', () =>
			HttpResponse.json(
				pageOf([
					{ id: 'agnt_billing', actor_type: 'agent', name: 'billing-ops', active: true },
					{
						id: 'agnt_release',
						actor_type: 'agent',
						name: 'release-manager',
						active: true,
					},
				]),
			),
		),
		http.get('/apis/:vendor/:name/:version/operations', () =>
			HttpResponse.json(
				pageOf([{ operation_id: 'pulls/merge', name: 'Merge a pull request' }]),
			),
		),
	);
	await page.viewport(1440, 900);
});

afterEach(() => {
	window.localStorage.clear();
	window.sessionStorage.clear();
	clearToken();
});

describe('InboxToast', () => {
	it('summarises what waits, beside the collapsed strip', async () => {
		const { container } = renderShell();
		const toast = await screen.findByTestId('inbox-toast');
		expect(toast).toHaveAttribute('data-form', 'summary');
		// It slides in.
		await waitFor(() => expect(toast).toBeVisible());
		expect(
			within(toast).getByText('3 calls are waiting for your approval'),
		).toBeInTheDocument();
		// Who is waiting, the longest-waiting agent first.
		expect(
			await within(toast).findByText('billing-ops 2 · release-manager 1'),
		).toBeInTheDocument();
		// Bottom-right (beside what covers the edge — the shell reports the strip).
		const rect = toast.getBoundingClientRect();
		expect(window.innerWidth - rect.right).toBeLessThan(60);
		expect(window.innerHeight - rect.bottom).toBeLessThan(40);
		await settleAnimations();
		await checkA11y(container);
	});

	it('reads one call in the singular', async () => {
		rows = [heldCall()];
		renderShell();
		const toast = await screen.findByTestId('inbox-toast');
		expect(within(toast).getByText('1 call is waiting for your approval')).toBeInTheDocument();
	});

	it("Review opens the oldest call's review pop-up", async () => {
		const user = userEvent.setup();
		renderShell();
		const toast = await screen.findByTestId('inbox-toast');
		await user.click(within(toast).getByRole('button', { name: 'Review' }));
		expect(locationSearch().get('review')).toBe('held_payout');
		// The toast speaks for every agent, so its pop-up walks every waiting call.
		expect(locationSearch().get('review_all')).toBe('1');
	});

	it('Open inbox opens the rail on its Inbox tab, which hides the toast', async () => {
		const user = userEvent.setup();
		renderShell();
		const toast = await screen.findByTestId('inbox-toast');
		await user.click(within(toast).getByRole('button', { name: 'Open inbox' }));

		const inboxTab = await screen.findByRole('tab', { name: /^Inbox/ });
		expect(inboxTab).toHaveAttribute('aria-selected', 'true');
		await waitFor(() => expect(inboxTab).toHaveFocus());
		expect(window.localStorage.getItem(RAIL_TAB_STORAGE_KEY)).toBe('inbox');
		await waitFor(() => expect(screen.queryByTestId('inbox-toast')).not.toBeInTheDocument());

		// On the Activity tab the Inbox is out of sight again, so the toast returns.
		await user.click(screen.getByRole('tab', { name: /Activity/ }));
		const back = await screen.findByTestId('inbox-toast');
		await waitFor(() => expect(back).toBeVisible());
	});

	it('stays hidden while the rail is open on Inbox', async () => {
		window.localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, '0');
		window.localStorage.setItem(RAIL_TAB_STORAGE_KEY, 'inbox');
		renderShell();
		await screen.findByRole('region', { name: 'billing-ops' });
		expect(screen.queryByTestId('inbox-toast')).not.toBeInTheDocument();
	});

	it('stays hidden while nothing waits', async () => {
		rows = [];
		renderShell();
		expect(await screen.findByRole('button', { name: 'Inbox' })).toBeInTheDocument();
		expect(screen.queryByTestId('inbox-toast')).not.toBeInTheDocument();
	});

	it('a dismissal lasts until the next arrival, which Review then opens', async () => {
		const user = userEvent.setup();
		const { queryClient, unmount } = renderShell();
		const toast = await screen.findByTestId('inbox-toast');
		await user.click(within(toast).getByRole('button', { name: 'Dismiss' }));
		await waitFor(() => expect(screen.queryByTestId('inbox-toast')).not.toBeInTheDocument());
		expect(window.sessionStorage.getItem(INBOX_TOAST_DISMISSED_KEY)).not.toBeNull();
		// The dismissal is the session's, not the URL's.
		expect(screen.getByTestId('location')).toHaveTextContent(/^\/agents$/);

		// A call that was already waiting does not bring it back.
		await act(() => queryClient.invalidateQueries({ queryKey: pendingApprovalsKey }));
		expect(screen.queryByTestId('inbox-toast')).not.toBeInTheDocument();

		await arrive(
			queryClient,
			heldCall({
				id: 'held_merge',
				agent_id: 'agnt_release',
				api_vendor: 'github',
				api_name: 'github-api',
				api_version: '1.1.4',
				method: 'PUT',
				path: '/repos/acme/storefront/pulls/1842/merge',
				operation_id: 'pulls/merge',
				created_at: new Date().toISOString(),
			}),
		);
		const arrival = await screen.findByTestId('inbox-toast');
		expect(arrival).toHaveAttribute('data-form', 'arrival');
		expect(within(arrival).getByText('New request · just now')).toBeInTheDocument();
		expect(
			await within(arrival).findByText('release-manager wants to run “Merge a pull request”'),
		).toBeInTheDocument();
		await user.click(within(arrival).getByRole('button', { name: 'Review' }));
		expect(locationSearch().get('review')).toBe('held_merge');
		expect(locationSearch().get('review_all')).toBe('1');
		// Seen: back to the summary of everything waiting.
		await waitFor(() =>
			expect(screen.getByTestId('inbox-toast')).toHaveAttribute('data-form', 'summary'),
		);
		unmount();
	});

	it('keeps a dismissal across a remount in the same session', async () => {
		const user = userEvent.setup();
		const first = renderShell();
		await user.click(
			within(await screen.findByTestId('inbox-toast')).getByRole('button', {
				name: 'Dismiss',
			}),
		);
		first.unmount();

		renderShell();
		await screen.findByRole('button', { name: 'Inbox, 3 waiting for you' });
		expect(screen.queryByTestId('inbox-toast')).not.toBeInTheDocument();
	});

	it('does not show below xl, where there is no docked rail', async () => {
		await page.viewport(1024, 800);
		onTestFinished(() => page.viewport(1440, 900));
		renderShell();
		const toast = await screen.findByTestId('inbox-toast');
		expect(toast).not.toBeVisible();
	});

	it('shows the arrival the moment the live stream reports an approval request', async () => {
		let push: ((frame: string) => void) | null = null;
		worker.use(
			http.get('/events', () => HttpResponse.json(pageOf([]))),
			http.get(
				'/events/stream',
				() =>
					new HttpResponse(
						new ReadableStream({
							start(controller) {
								const enc = new TextEncoder();
								push = (frame) => controller.enqueue(enc.encode(frame));
							},
						}),
						{ headers: { 'Content-Type': 'text/event-stream' } },
					),
			),
		);
		render(
			<QueryClientProvider
				client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
			>
				<MemoryRouter initialEntries={['/agents']}>
					<AgentStreamProvider live={true}>
						<AgentRail />
						<InboxToast />
					</AgentStreamProvider>
				</MemoryRouter>
			</QueryClientProvider>,
		);
		expect(await screen.findByTestId('inbox-toast')).toHaveAttribute('data-form', 'summary');
		await waitFor(() => expect(push).not.toBeNull());

		rows = [...rows, heldCall({ id: 'held_live', created_at: new Date().toISOString() })];
		const event = {
			_links: { self: '/events/evt_live' },
			event_id: 'evt_live',
			type: 'execution.approval_requested',
			severity: 'warning',
			summary: 'Agent agnt_billing wants to POST /v1/charges',
			created_at: new Date().toISOString(),
			requires_action: true,
			data: { held_execution_id: 'held_live', agent_id: 'agnt_billing' },
		};
		act(() =>
			push?.(
				`event: ${event.type}\nid: ${event.event_id}\ndata: ${JSON.stringify(event)}\n\n`,
			),
		);
		await waitFor(() =>
			expect(screen.getByTestId('inbox-toast')).toHaveAttribute('data-form', 'arrival'),
		);
		expect(
			await screen.findByText('billing-ops wants to run POST /v1/charges'),
		).toBeInTheDocument();
	});

	it('keeps dark surfaces neutral and gives light a faint warm fill', async () => {
		onTestFinished(() => setTheme('light'));
		const probe = document.createElement('div');
		probe.className = 'bg-surface-tonal';
		document.body.appendChild(probe);
		onTestFinished(() => probe.remove());
		renderShell();
		const tile = (await screen.findByTestId('inbox-toast')).querySelector(
			`span[aria-hidden="true"].${ATTENTION_TILE_CLASS.split(' ')[0]}`,
		)!;
		expect(tile).not.toBeNull();

		setTheme('dark');
		const darkFill = getComputedStyle(tile).backgroundColor;
		expect(darkFill).toBe(getComputedStyle(probe).backgroundColor);

		setTheme('light');
		expect(getComputedStyle(tile).backgroundColor).not.toBe(
			getComputedStyle(probe).backgroundColor,
		);
	});
});
