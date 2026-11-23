/**
 * The rail's Inbox: the Inbox | Activity tabs, the held calls grouped by agent
 * (the agent on screen first), the strip's Inbox entry and its count, the
 * remembered tab, and `?inbox=1` opening the Inbox (the docked rail at `xl+`,
 * the drawer below) before it is stripped from the URL.
 */
import { describe, it, expect, beforeEach, afterEach, onTestFinished } from 'vitest';
import { page } from 'vitest/browser';
import type { ReactElement } from 'react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within, userEvent, checkA11y } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { AgentRail } from '@/shared/app/rail/AgentRail';
import { ActivityDrawerButton } from '@/shared/app/rail/ActivityDrawer';
import { RAIL_TAB_STORAGE_KEY } from '@/shared/app/rail/railPreferences';
import { AgentStreamProvider, RAIL_COLLAPSED_STORAGE_KEY } from '@/shared/lib/agentStream';
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

function renderRail(ui: ReactElement, route = '/dashboard') {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<MemoryRouter initialEntries={[route]}>
				<AgentStreamProvider live={false}>
					<Routes>
						<Route path="/*" element={ui} />
					</Routes>
					<LocationProbe />
				</AgentStreamProvider>
			</MemoryRouter>
		</QueryClientProvider>,
	);
}

function locationSearch(): URLSearchParams {
	const text = screen.getByTestId('location').textContent ?? '';
	return new URLSearchParams(text.slice(text.indexOf('?')));
}

beforeEach(async () => {
	window.localStorage.clear();
	setToken('test-token');
	rows = [
		heldCall({
			id: 'held_charge',
			operation_id: 'PostCharges',
			created_at: minutesAgo(6),
		}),
		heldCall({
			id: 'held_payout',
			operation_id: 'PostPayouts',
			path: '/v1/payouts',
			created_at: minutesAgo(128),
		}),
		heldCall({
			id: 'held_merge',
			agent_id: 'agnt_release',
			api_vendor: 'github',
			api_name: 'github-api',
			api_version: '1.1.4',
			method: 'PUT',
			path: '/repos/acme/storefront/pulls/1842/merge',
			operation_id: 'pulls/merge',
			created_at: minutesAgo(3),
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
		http.get('/apis/:vendor/:name/:version/operations', ({ params }) =>
			HttpResponse.json(
				pageOf(
					params.vendor === 'github'
						? [{ operation_id: 'pulls/merge', name: 'Merge a pull request' }]
						: [
								{ operation_id: 'PostCharges', name: 'Create a charge' },
								{ operation_id: 'PostPayouts', name: 'Create a payout' },
							],
				),
			),
		),
	);
	// The docked rail only joins the accessibility tree at `xl+`.
	await page.viewport(1440, 900);
});

afterEach(() => {
	window.localStorage.clear();
	clearToken();
});

describe('rail — Inbox tab', () => {
	it('lists the held calls grouped by agent, the agent on screen first', async () => {
		window.localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, '0');
		window.localStorage.setItem(RAIL_TAB_STORAGE_KEY, 'inbox');
		const { container } = renderRail(<AgentRail />, '/agents?agent=agnt_release');

		const inboxTab = await screen.findByRole('tab', { name: /^Inbox/ });
		expect(inboxTab).toHaveAttribute('aria-selected', 'true');
		await waitFor(() => expect(inboxTab).toHaveAccessibleName(/^Inbox\s*3 waiting$/));
		const panel = screen.getByRole('tabpanel', { name: /^Inbox/ });

		// release-manager is on screen, so it leads; billing-ops' oldest call first.
		const groups = await within(panel).findAllByRole('region');
		await waitFor(() =>
			expect(groups.map((g) => g.getAttribute('aria-label'))).toEqual([
				'release-manager',
				'billing-ops',
			]),
		);
		const release = within(panel).getByRole('region', { name: 'release-manager' });
		expect(
			await within(release).findByRole('button', { name: /Merge a pull request/ }),
		).toHaveTextContent('github · 3m ago');
		const billing = within(panel).getByRole('region', { name: 'billing-ops' });
		const billingRows = within(billing).getAllByRole('listitem');
		expect(billingRows.map((r) => r.textContent)).toEqual([
			'POSTCreate a payoutstripe · 2h ago',
			'POSTCreate a chargestripe · 6m ago',
		]);
		expect(within(billing).getByText('2')).toBeInTheDocument();
		expect(
			within(billing).getByRole('button', { name: 'Review all from billing-ops' }),
		).toBeInTheDocument();
		// The Activity tab's own controls are not on the Inbox tab.
		expect(screen.queryByRole('button', { name: 'Pause live feed' })).not.toBeInTheDocument();
		expect(screen.queryByRole('log', { name: 'Activity feed' })).not.toBeInTheDocument();
		await checkA11y(container);
	});

	it("opens a call's review from its row, and a group's oldest from Review all", async () => {
		window.localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, '0');
		window.localStorage.setItem(RAIL_TAB_STORAGE_KEY, 'inbox');
		const user = userEvent.setup();
		renderRail(<AgentRail />);

		await user.click(await screen.findByRole('button', { name: /Create a charge/ }));
		expect(locationSearch().get('review')).toBe('held_charge');
		// Both walk that agent's calls alone: no "every agent" flag.
		expect(locationSearch().get('review_all')).toBeNull();

		await user.click(screen.getByRole('button', { name: 'Review all from billing-ops' }));
		expect(locationSearch().get('review')).toBe('held_payout');
		expect(locationSearch().get('review_all')).toBeNull();
	});

	it('says so when nothing is waiting', async () => {
		rows = [];
		window.localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, '0');
		window.localStorage.setItem(RAIL_TAB_STORAGE_KEY, 'inbox');
		renderRail(<AgentRail />);
		expect(await screen.findByText('Nothing waiting for you')).toBeInTheDocument();
		expect(screen.getByRole('tab', { name: 'Inbox' })).toHaveAttribute('aria-selected', 'true');
	});

	it('keeps the Activity feed on its own tab, and moves between tabs with the arrow keys', async () => {
		window.localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, '0');
		const user = userEvent.setup();
		renderRail(<AgentRail />);

		// Activity until the viewer picks the Inbox.
		const activityTab = await screen.findByRole('tab', { name: /Activity/ });
		expect(activityTab).toHaveAttribute('aria-selected', 'true');
		expect(await screen.findByRole('log', { name: 'Activity feed' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Pause live feed' })).toBeInTheDocument();

		activityTab.focus();
		await user.keyboard('{ArrowLeft}');
		const inboxTab = screen.getByRole('tab', { name: /^Inbox/ });
		expect(inboxTab).toHaveFocus();
		expect(inboxTab).toHaveAttribute('aria-selected', 'true');
		expect(await screen.findByRole('region', { name: 'billing-ops' })).toBeInTheDocument();
		expect(window.localStorage.getItem(RAIL_TAB_STORAGE_KEY)).toBe('inbox');
	});

	it('remembers the last tab across remounts', async () => {
		window.localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, '0');
		const user = userEvent.setup();
		const first = renderRail(<AgentRail />);
		await user.click(await screen.findByRole('tab', { name: /^Inbox/ }));
		first.unmount();

		renderRail(<AgentRail />);
		expect(await screen.findByRole('tab', { name: /^Inbox/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
	});
});

describe('rail — collapsed strip', () => {
	it('carries an Inbox entry with the waiting count above Activity', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);

		const inbox = await screen.findByRole('button', { name: 'Inbox, 3 waiting for you' });
		const activity = screen.getByRole('button', { name: /^Show live activity/ });
		// Inbox sits above Activity.
		expect(
			inbox.compareDocumentPosition(activity) & Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		expect(within(inbox).getByText('3')).toBeVisible();

		await user.click(inbox);
		expect(await screen.findByRole('tab', { name: /^Inbox/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Collapse inbox' })).toHaveFocus(),
		);

		// Collapsing from the Inbox hands focus back to the strip's Inbox entry.
		await user.click(screen.getByRole('button', { name: 'Collapse inbox' }));
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Inbox, 3 waiting for you' })).toHaveFocus(),
		);

		// The Activity entry opens the Activity tab.
		await user.click(screen.getByRole('button', { name: /^Show live activity/ }));
		expect(await screen.findByRole('tab', { name: /Activity/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(await screen.findByRole('log', { name: 'Activity feed' })).toBeInTheDocument();
	});

	it('reads plain "Inbox" when nothing waits', async () => {
		rows = [];
		renderRail(<AgentRail />);
		expect(await screen.findByRole('button', { name: 'Inbox' })).toBeInTheDocument();
	});
});

describe('rail — ?inbox=1', () => {
	it('opens the docked rail on its Inbox tab and strips the param', async () => {
		renderRail(<AgentRail />, '/dashboard?inbox=1&keep=yes');

		expect(await screen.findByRole('tab', { name: /^Inbox/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		await waitFor(() =>
			expect(screen.getByTestId('location')).toHaveTextContent('/dashboard?keep=yes'),
		);
		expect(window.localStorage.getItem(RAIL_COLLAPSED_STORAGE_KEY)).toBe('0');
		expect(await screen.findByRole('region', { name: 'billing-ops' })).toBeInTheDocument();
	});

	it('opens the drawer on its Inbox tab below xl', async () => {
		await page.viewport(1024, 800);
		onTestFinished(() => page.viewport(1440, 900));
		renderRail(
			<>
				<ActivityDrawerButton className="xl:hidden" />
				<div className="hidden xl:flex">
					<AgentRail />
				</div>
			</>,
			'/agents?inbox=1',
		);

		const drawer = await screen.findByRole('dialog', { name: 'Activity' });
		expect(within(drawer).getByRole('tab', { name: /^Inbox/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(
			await within(drawer).findByRole('region', { name: 'billing-ops' }),
		).toBeInTheDocument();
		expect(within(drawer).getByRole('tab', { name: /Activity/ })).toBeInTheDocument();
		await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/agents$/));
		// The docked rail (hidden here) stays collapsed.
		expect(window.localStorage.getItem(RAIL_COLLAPSED_STORAGE_KEY)).not.toBe('0');

		// A row closes the drawer and opens the review.
		const user = userEvent.setup();
		await user.click(await within(drawer).findByRole('button', { name: /Create a charge/ }));
		expect(locationSearch().get('review')).toBe('held_charge');
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Activity' })).not.toBeInTheDocument(),
		);
	});
});
