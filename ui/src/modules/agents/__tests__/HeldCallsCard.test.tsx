/**
 * The "Waiting for you" card on an agent, and its two companion cues (the
 * strip tab's chip and the status beside Active): what it summarises, the
 * links it opens, the bulk deny's decisions and summary, and the theme rule —
 * dark surfaces stay neutral (amber only on the glyph, count and hairline),
 * light keeps a faint warm fill.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import { useLocation } from 'react-router';
import { MotionConfig } from 'framer-motion';
import {
	checkA11y,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	within,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { Toaster } from '@/shared/ui';
import type { HeldCall } from '@/shared/approvals';
import {
	clearApprovalsStore,
	getMockApproval,
	resetApprovalsStore,
	seedMockApproval,
} from '@/modules/agents/mocks/approvalsHandlers';
import {
	HeldCallsCard,
	HeldCallsChip,
	HeldCallsStatus,
} from '@/modules/agents/components/flat/HeldCallsCard';

const HOUR = 3_600_000;
const MINUTE = 60_000;

/** The operation names the API's spec gives, so headlines read as words. */
function serveOperationNames() {
	worker.use(
		http.get('/apis/:vendor/:name/:version/operations', () =>
			HttpResponse.json({
				data: [
					{ operation_id: 'createPayout', name: 'Create a payout' },
					{ operation_id: 'cancelSubscription', name: 'Cancel a subscription' },
					{ operation_id: 'createRefund', name: 'Create a refund' },
				],
				has_more: false,
				next_cursor: null,
			}),
		),
	);
}

/** Three pending calls, oldest first, the middle one a delete. */
function seedCalls(): HeldCall[] {
	const now = Date.now();
	const rows = [
		seedMockApproval({
			id: 'held_payout',
			operation_id: 'createPayout',
			path: '/v1/payouts',
			created_at: new Date(now - 2 * HOUR).toISOString(),
			// 21h 51m and change left.
			expires_at: new Date(now + 21 * HOUR + 51 * MINUTE + 30_000).toISOString(),
		}),
		seedMockApproval({
			id: 'held_cancel',
			operation_id: 'cancelSubscription',
			method: 'DELETE',
			path: '/v1/subscriptions/sub_1PbQ7',
			created_at: new Date(now - 47 * MINUTE).toISOString(),
			expires_at: new Date(now + 23 * HOUR + 12 * MINUTE + 30_000).toISOString(),
		}),
		seedMockApproval({
			id: 'held_refund',
			operation_id: 'createRefund',
			path: '/v1/refunds',
			created_at: new Date(now - 19 * MINUTE).toISOString(),
			expires_at: new Date(now + 23 * HOUR + 40 * MINUTE + 30_000).toISOString(),
		}),
	];
	// The list endpoint's shape; the card reads only what the store seeds.
	return rows.map((row) => ({ ...row, review_url: '', _links: {} }) as unknown as HeldCall);
}

function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location-search">{location.search}</div>;
}

const search = () => new URLSearchParams(screen.getByTestId('location-search').textContent ?? '');

/** Reduced motion: the list is open or shut the moment it is asked, so the
 * tests read its state without waiting out the reveal. */
function renderCard(calls: readonly HeldCall[]) {
	return renderWithProviders(
		<MotionConfig reducedMotion="always">
			<HeldCallsCard calls={calls} agentName="billing-ops" apiTitleOf={() => 'Stripe'} />
			<LocationProbe />
			<Toaster />
		</MotionConfig>,
		{ route: '/?agent=agnt_billing' },
	);
}

const card = () => screen.getByRole('article', { name: 'Waiting for you' });

async function expand(user: ReturnType<typeof userEvent.setup>) {
	await user.click(within(card()).getByRole('button', { name: 'Show the calls' }));
	return screen.getByRole('list', { name: /Calls waiting for you/ });
}

/** A theme's computed colours for an element (and its `::after`). */
function colours(el: Element) {
	const style = getComputedStyle(el);
	return {
		background: style.backgroundColor,
		ring: getComputedStyle(el, '::after').boxShadow,
		shadow: style.boxShadow,
	};
}

describe('HeldCallsCard', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		clearApprovalsStore();
		serveOperationNames();
	});

	afterEach(() => {
		resetApprovalsStore();
		document.documentElement.dataset.theme = 'light';
	});

	it('renders nothing while no call is waiting', () => {
		renderCard([]);
		expect(screen.queryByRole('article')).toBeNull();
		expect(screen.queryByText('Waiting for you')).toBeNull();
	});

	it('summarises the calls: the count, the soonest expiry and every name', async () => {
		renderCard(seedCalls());
		expect(card()).toHaveTextContent('3 calls held for approval · next expires in 21 h');
		// The names on a wide screen, in order, once the spec has named them.
		await waitFor(() =>
			expect(card()).toHaveTextContent(
				'Create a payout · Cancel a subscription · Create a refund',
			),
		);
		// Folded until asked: no rows yet.
		expect(screen.queryByRole('list', { name: /Calls waiting for you/ })).toBeNull();
	});

	it('Review all opens the review pop-up on the oldest call', async () => {
		const user = userEvent.setup();
		renderCard(seedCalls());
		await user.click(within(card()).getByRole('button', { name: 'Review all' }));
		expect(search().get('review')).toBe('held_payout');
		expect(search().get('agent')).toBe('agnt_billing');
	});

	it('opens a row’s call in the pop-up', async () => {
		const user = userEvent.setup();
		renderCard(seedCalls());
		const list = await expand(user);
		await user.click(
			await within(list).findByRole('button', { name: /^Review .*Create a refund/ }),
		);
		expect(search().get('review')).toBe('held_refund');
	});

	it('shows each row’s method, headline, API, age and expiry, and flags the delete', async () => {
		const user = userEvent.setup();
		renderCard(seedCalls());
		const list = await expand(user);
		const rows = within(list).getAllByTestId('held-call-row');
		expect(rows).toHaveLength(3);
		await waitFor(() => expect(rows[0]).toHaveTextContent('Create a payout'));
		expect(rows[0]).toHaveTextContent('POST');
		expect(rows[0]).toHaveTextContent('Stripe · /v1/payouts · 2h ago · expires in 21 h');
		expect(rows[0]).not.toHaveTextContent('Deletes data');
		expect(rows[1]).toHaveTextContent('DELETE');
		expect(rows[1]).toHaveTextContent('Deletes data');
		expect(rows[1]).toHaveTextContent('47m ago · expires in 23 h');
	});

	it('always shows the checkboxes, with the hint until one is ticked', async () => {
		const user = userEvent.setup();
		renderCard(seedCalls());
		const list = await expand(user);
		expect(within(list).getAllByRole('checkbox')).toHaveLength(3);
		const toolbar = screen.getByTestId('held-calls-toolbar');
		expect(toolbar).toHaveTextContent('Select all');
		expect(toolbar).toHaveTextContent('Tick calls to deny or approve several at once');
		expect(within(toolbar).queryByRole('button', { name: /^Deny/ })).toBeNull();

		await user.click(
			await within(list).findByRole('checkbox', { name: 'Select Create a payout' }),
		);
		expect(toolbar).toHaveTextContent('1 selected');
		expect(toolbar).not.toHaveTextContent('Tick calls');
		expect(within(toolbar).getByRole('button', { name: 'Deny 1' })).toBeEnabled();
		expect(within(toolbar).getByRole('button', { name: 'Approve 1…' })).toBeEnabled();
	});

	it('a press anywhere on the head pins the list open, and again unpins it', async () => {
		const user = userEvent.setup();
		renderCard(seedCalls());
		await user.click(screen.getByTestId('held-calls-head-overlay'));
		expect(card()).toHaveAttribute('data-pinned', 'true');
		expect(screen.getByRole('list', { name: /Calls waiting for you/ })).toBeInTheDocument();
		expect(screen.getByTestId('held-calls-toggle')).toHaveAttribute('aria-expanded', 'true');
		await user.click(screen.getByTestId('held-calls-head-overlay'));
		expect(card()).not.toHaveAttribute('data-pinned');
		expect(screen.queryByRole('list', { name: /Calls waiting for you/ })).toBeNull();
	});

	it('the head’s own controls act without pinning: Review all opens the pop-up only', async () => {
		const user = userEvent.setup();
		renderCard(seedCalls());
		await user.click(within(card()).getByRole('button', { name: 'Review all' }));
		expect(search().get('review')).toBe('held_payout');
		// Over this agent's calls alone: no "every agent" flag.
		expect(search().get('review_all')).toBeNull();
		expect(card()).not.toHaveAttribute('data-pinned');
	});

	it('select-all ticks every call; folding the list drops the ticks', async () => {
		const user = userEvent.setup();
		renderCard(seedCalls());
		await expand(user);
		const toolbar = screen.getByTestId('held-calls-toolbar');
		await user.click(within(toolbar).getByRole('checkbox', { name: 'Select all' }));
		expect(toolbar).toHaveTextContent('3 selected');
		expect(within(toolbar).getByRole('checkbox', { name: 'Select all' })).toBeChecked();

		await user.click(within(card()).getByRole('button', { name: 'Hide the calls' }));
		await expand(user);
		expect(screen.getByTestId('held-calls-toolbar')).toHaveTextContent('Select all');
	});

	it('Approve N… opens the bulk confirmation on the ticked calls, leaving deletes out', async () => {
		const user = userEvent.setup();
		renderCard(seedCalls());
		await expand(user);
		const toolbar = screen.getByTestId('held-calls-toolbar');
		await user.click(within(toolbar).getByRole('checkbox', { name: 'Select all' }));
		expect(toolbar).toHaveTextContent('Deletes are approved one at a time');
		await user.click(within(toolbar).getByRole('button', { name: 'Approve 2…' }));
		expect(search().get('review_batch')).toBe('held_payout,held_refund');
		expect(search().has('review')).toBe(false);
	});

	it('only a delete ticked: nothing to approve in bulk', async () => {
		const user = userEvent.setup();
		renderCard(seedCalls());
		const list = await expand(user);
		await user.click(
			await within(list).findByRole('checkbox', { name: 'Select Cancel a subscription' }),
		);
		const toolbar = screen.getByTestId('held-calls-toolbar');
		expect(within(toolbar).getByRole('button', { name: 'Approve…' })).toBeDisabled();
		expect(within(toolbar).getByRole('button', { name: 'Deny 1' })).toBeEnabled();
		expect(toolbar).toHaveTextContent('Deletes are approved one at a time');
	});

	it('Deny N decides each ticked call, sums up in one toast and clears the ticks', async () => {
		const decided: { id: string; body: unknown }[] = [];
		worker.use(
			http.post('/executions/holds/:id\\:decide', async ({ params, request }) => {
				decided.push({ id: String(params.id), body: await request.clone().json() });
				// Someone else got to this one first.
				if (params.id === 'held_refund') {
					return HttpResponse.json(
						{
							type: 'held_execution_already_settled',
							status: 409,
							detail: "Held execution 'held_refund' is already approved",
						},
						{ status: 409 },
					);
				}
				// The rest fall through to the store's own handler.
				return undefined;
			}),
		);
		const user = userEvent.setup();
		renderCard(seedCalls());
		const list = await expand(user);
		await user.click(
			await within(list).findByRole('checkbox', { name: 'Select Create a payout' }),
		);
		await user.click(within(list).getByRole('checkbox', { name: 'Select Create a refund' }));
		await user.click(screen.getByRole('button', { name: 'Deny 2' }));

		expect(await screen.findByText('Denied 1 of 2 calls')).toBeInTheDocument();
		expect(screen.getByText('1 call was already decided by someone else.')).toBeInTheDocument();
		// One toast for the lot: no per-call success or conflict toast.
		expect(screen.queryByText(/Denied execution for/)).toBeNull();
		expect(screen.queryByText(/is already approved/)).toBeNull();
		expect(decided.map((d) => d.id).sort()).toEqual(['held_payout', 'held_refund']);
		for (const d of decided) expect(d.body).toEqual({ decision: 'deny', reason: null });
		expect(getMockApproval('held_payout')?.state).toBe('denied');
		expect(getMockApproval('held_cancel')?.state).toBe('pending');
		expect(screen.getByTestId('held-calls-toolbar')).toHaveTextContent('Select all');
	});

	it('has no serious accessibility violations, folded or open', async () => {
		const user = userEvent.setup();
		const { container } = renderCard(seedCalls());
		await checkA11y(container);
		const list = await expand(user);
		await user.click(
			await within(list).findByRole('checkbox', { name: 'Select Cancel a subscription' }),
		);
		await checkA11y(container);
	});

	describe('theme', () => {
		it('dark: neutral surfaces, amber only on the glyph, count and hairline', async () => {
			document.documentElement.dataset.theme = 'dark';
			renderCard(seedCalls());
			const head = colours(screen.getByTestId('held-calls-head'));
			const tile = colours(screen.getByTestId('held-calls-tile'));
			// No fill on the head; the tile is the neutral tonal surface.
			expect(head.background).toBe('rgba(0, 0, 0, 0)');
			expect(tile.background).not.toMatch(/ 0\.15\)$/);
			expect(colours(card()).ring).toMatch(/inset/);

			const darkTile = tile.background;
			document.documentElement.dataset.theme = 'light';
			await waitFor(() =>
				expect(colours(screen.getByTestId('held-calls-head')).background).not.toBe(
					'rgba(0, 0, 0, 0)',
				),
			);
			// Light: a faint warm fill on the head and a warmer tile.
			expect(colours(screen.getByTestId('held-calls-head')).background).toMatch(/ 0\.06\)$/);
			expect(colours(screen.getByTestId('held-calls-tile')).background).not.toBe(darkTile);
		});

		it('the strip chip is a neutral chip with an amber glyph in dark, warm-filled in light', () => {
			document.documentElement.dataset.theme = 'dark';
			renderWithProviders(<HeldCallsChip count={4} />);
			const chip = screen.getByTestId('strip-waiting-chip');
			const glyph = chip.querySelector('svg')!;
			expect(chip).toHaveTextContent('4');
			expect(chip).toHaveTextContent('4 calls waiting for you');
			const dark = colours(chip);
			expect(dark.background).not.toMatch(/ 0\.12\)$/);
			// The count stays neutral; the glyph carries the amber.
			expect(getComputedStyle(chip).color).not.toBe(getComputedStyle(glyph).color);

			document.documentElement.dataset.theme = 'light';
			const light = colours(chip);
			expect(light.background).toMatch(/ 0\.12\)$/);
			// On the light page the count takes the amber too.
			expect(getComputedStyle(chip).color).toBe(getComputedStyle(glyph).color);
		});
	});
});

describe('HeldCallsStatus', () => {
	it('says the agent is waiting on the viewer', async () => {
		const { container } = renderWithProviders(<HeldCallsStatus />);
		expect(screen.getByTestId('agent-waiting-status')).toHaveTextContent('Waiting for you');
		await checkA11y(container);
	});
});
