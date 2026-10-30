/**
 * The bulk-approve confirmation (`?review_batch=<id>,<id>…`): one line per
 * call, deletes never included, a total only where the amounts share a
 * currency, and "Approve N" deciding one call at a time, reporting once —
 * a call someone else settled first counts as already decided.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { useLocation } from 'react-router';
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
import { ApprovalReviewHost } from '@/shared/approvals';
import {
	clearApprovalsStore,
	getMockApproval,
	seedMockApproval,
} from '@/modules/agents/mocks/approvalsHandlers';

const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();
const hoursFromNow = (n: number) => new Date(Date.now() + n * 3_600_000).toISOString();

const OPERATIONS: Record<string, string> = {
	createCharge: 'Create a charge',
	createRefund: 'Create a refund',
	cancelSubscription: 'Cancel a subscription',
};

function seedCall(
	id: string,
	method: string,
	operationId: string,
	body: Record<string, unknown> | null,
	heldMinutesAgo: number,
) {
	return seedMockApproval({
		id,
		agent_id: 'agnt_billing',
		agent_name: 'billing-ops',
		operation_id: operationId,
		method,
		path: `/v1/${id}`,
		created_at: minutesAgo(heldMinutesAgo),
		expires_at: hoursFromNow(23),
		request: {
			method,
			url: `https://api.stripe.com/v1/${id}`,
			body: body ? JSON.stringify(body) : null,
			body_truncated: false,
		},
	});
}

function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location">{location.search}</div>;
}

function renderHost(route: string) {
	return renderWithProviders(
		<>
			<ApprovalReviewHost />
			<LocationProbe />
			<Toaster />
		</>,
		{ route },
	);
}

const currentParams = () => new URLSearchParams(screen.getByTestId('location').textContent!);

let decisions: { id: string; body: unknown }[];

beforeEach(() => {
	clearApprovalsStore();
	decisions = [];
	seedCall('held_charge', 'POST', 'createCharge', { amount: 129900, currency: 'eur' }, 30);
	seedCall('held_refund', 'POST', 'createRefund', { amount: 4900, currency: 'eur' }, 20);
	seedCall('held_cancel', 'DELETE', 'cancelSubscription', null, 10);
	worker.use(
		http.get('/apis/:vendor/:name/:version/operations', () =>
			HttpResponse.json({
				data: Object.entries(OPERATIONS).map(([operation_id, name]) => ({
					operation_id,
					name,
				})),
				has_more: false,
				next_cursor: null,
			}),
		),
		// Records what each decision sends, then falls through to the store.
		http.post('/executions/holds/:id\\:decide', async ({ request, params }) => {
			decisions.push({ id: String(params.id), body: await request.clone().json() });
		}),
	);
});

describe('bulk-approve confirmation', () => {
	it('lists each call once, leaves deletes out, and totals amounts in one currency', async () => {
		renderHost('/agents?review_batch=held_charge,held_refund,held_cancel');

		const dialog = await screen.findByRole('dialog', { name: 'Approve 2 calls' });
		const lines = within(dialog).getByRole('list', { name: 'Calls to approve' });
		expect(await within(lines).findByText('Create a charge')).toBeInTheDocument();
		expect(within(lines).getByText('Create a refund')).toBeInTheDocument();
		expect(within(lines).queryByText('Cancel a subscription')).not.toBeInTheDocument();
		expect(within(lines).getAllByRole('listitem')).toHaveLength(2);
		expect(within(lines).getByText('129900 eur')).toBeInTheDocument();
		expect(await within(dialog).findByText('134800 eur')).toBeInTheDocument();
		expect(within(dialog).getByText('From billing-ops')).toBeInTheDocument();

		// The delete is set aside, with a way to review it on its own.
		expect(
			within(dialog).getByText('Left out: deletes are approved one at a time'),
		).toBeInTheDocument();
		expect(
			within(dialog).getByRole('button', { name: 'Review Cancel a subscription' }),
		).toBeInTheDocument();

		await checkA11y(document.body, { modal: true });
	});

	it('approves only the non-delete calls, one decide each, then closes with one toast', async () => {
		renderHost('/agents?review_batch=held_charge,held_refund,held_cancel');

		const dialog = await screen.findByRole('dialog', { name: 'Approve 2 calls' });
		await within(dialog).findByText('Create a charge');
		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve 2' }));

		expect(await screen.findByText('Approved 2 calls')).toBeInTheDocument();
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: 'Approve 2 calls' }),
			).not.toBeInTheDocument(),
		);
		expect(currentParams().has('review_batch')).toBe(false);
		expect(decisions).toEqual([
			{ id: 'held_charge', body: { decision: 'approve' } },
			{ id: 'held_refund', body: { decision: 'approve' } },
		]);
		expect(getMockApproval('held_cancel')?.state).toBe('pending');
		expect(screen.queryByText(/Approved execution for/)).not.toBeInTheDocument();
	});

	it('counts a call someone else settled first as already decided', async () => {
		renderHost('/agents?review_batch=held_charge,held_refund');

		const dialog = await screen.findByRole('dialog', { name: 'Approve 2 calls' });
		await within(dialog).findByText('Create a refund');
		getMockApproval('held_refund')!.state = 'denied';
		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve 2' }));

		expect(await screen.findByText('Approved 1 of 2 calls')).toBeInTheDocument();
		expect(screen.getByText('1 call was already decided by someone else.')).toBeInTheDocument();
		expect(getMockApproval('held_charge')?.state).toBe('approved');
		expect(getMockApproval('held_refund')?.state).toBe('denied');
	});

	it('reports a call that failed, which stays waiting', async () => {
		worker.use(
			http.post('/executions/holds/held_refund\\:decide', () =>
				HttpResponse.json({ status: 500, detail: 'Upstream unavailable' }, { status: 500 }),
			),
		);
		renderHost('/agents?review_batch=held_charge,held_refund');

		const dialog = await screen.findByRole('dialog', { name: 'Approve 2 calls' });
		await within(dialog).findByText('Create a refund');
		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve 2' }));

		expect(await screen.findByText('Approved 1 of 2 calls')).toBeInTheDocument();
		expect(
			screen.getByText(
				/1 call couldn't be approved and is still waiting: Upstream unavailable/,
			),
		).toBeInTheDocument();
		expect(getMockApproval('held_refund')?.state).toBe('pending');
	});

	it('opens a left-out delete in the review pop-up', async () => {
		renderHost('/agents?review_batch=held_charge,held_cancel');

		const dialog = await screen.findByRole('dialog', { name: 'Approve 1 call' });
		await userEvent.click(
			await within(dialog).findByRole('button', { name: 'Review Cancel a subscription' }),
		);
		expect(await screen.findByRole('dialog', { name: 'Review call' })).toBeInTheDocument();
		expect(currentParams().get('review')).toBe('held_cancel');
		expect(currentParams().has('review_batch')).toBe(false);
	});

	it('says nothing is left when every picked call stopped waiting', async () => {
		getMockApproval('held_charge')!.state = 'approved';
		renderHost('/agents?review_batch=held_charge');

		const dialog = await screen.findByRole('dialog', { name: 'Approve calls' });
		expect(await within(dialog).findByText(/Nothing left to approve/)).toBeInTheDocument();
		expect(within(dialog).queryByRole('button', { name: /^Approve/ })).not.toBeInTheDocument();
	});
});
