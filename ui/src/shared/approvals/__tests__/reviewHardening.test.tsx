/**
 * The review surfaces under hostile or lagging input: a review link whose id
 * is not one, the same id picked twice for a batch, a detail row that is not
 * shaped like a held call, a server that answers with a bare status line, a
 * decision in flight when the viewer tries to close, and a next call that was
 * settled behind the list's back.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { delay, http, HttpResponse } from 'msw';
import { useLocation } from 'react-router';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
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
};

function seedCall(id: string, operationId = 'createCharge', heldMinutesAgo = 5) {
	return seedMockApproval({
		id,
		agent_id: 'agnt_billing',
		agent_name: 'billing-ops',
		agent_owner_id: 'usr_owner',
		operation_id: operationId,
		created_at: minutesAgo(heldMinutesAgo),
		expires_at: hoursFromNow(23),
		request: {
			method: 'POST',
			url: 'https://api.stripe.com/v1/charges?expand=balance',
			body: '{"amount":129900,"currency":"eur"}',
			body_truncated: false,
		},
	});
}

function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
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

const requested: string[] = [];
const record = ({ request }: { request: Request }) => {
	requested.push(new URL(request.url).pathname);
};

beforeEach(() => {
	clearApprovalsStore();
	requested.length = 0;
	worker.events.on('request:start', record);
	worker.use(
		http.get('/apis/:vendor/:name/:version/operations', () =>
			HttpResponse.json({
				data: Object.entries(OPERATIONS).map(([operation_id, name]) => ({
					operation_id,
					name,
					method: 'POST',
					path: `/v1/${operation_id}`,
				})),
				has_more: false,
				next_cursor: null,
			}),
		),
	);
	return () => worker.events.removeListener('request:start', record);
});

describe('review links that name nothing', () => {
	it('a path-shaped id opens nothing, is stripped, and reaches no other route', async () => {
		seedCall('held_a');
		renderHost('/agents?agent=agnt_billing&review=..%2F..%2Fusers%2Fme');
		await waitFor(() =>
			expect(screen.getByTestId('location')).toHaveTextContent(
				/^\/agents\?agent=agnt_billing$/,
			),
		);
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
		expect(requested.some((p) => p === '/users/me' || p.includes('/executions/holds/'))).toBe(
			false,
		);
	});

	it('a dot segment opens nothing either', async () => {
		seedCall('held_a');
		renderHost('/agents?review=..');
		await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/agents$/));
		expect(requested.some((p) => p.startsWith('/executions'))).toBe(false);
	});

	it('an empty batch beside a real review id leaves the pop-up open', async () => {
		seedCall('held_a');
		renderHost('/agents?review=held_a&review_batch=');
		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByText('“Create a charge”')).toBeInTheDocument();
		// Nothing strips the review the viewer opened.
		expect(screen.getByTestId('location')).toHaveTextContent('review=held_a');
		expect(screen.getByRole('dialog', { name: 'Review call' })).toBeInTheDocument();
	});

	it('a repeated id in a batch is one line and one decision', async () => {
		seedCall('held_dup');
		renderHost('/agents?review_batch=held_dup,held_dup,held_dup');
		const dialog = await screen.findByRole('dialog');
		await waitFor(() => expect(dialog.textContent).toMatch(/Approve 1 call/));
		expect(within(dialog).getAllByRole('listitem')).toHaveLength(1);
	});
});

describe('rows that are not what the surfaces expect', () => {
	it('a malformed detail still renders, with the method one token and no dashes', async () => {
		seedCall('held_weird');
		worker.use(
			http.get('/executions/holds/:id', () =>
				HttpResponse.json({
					id: 'held_weird',
					state: 'pending',
					method: 'delete ',
					path: null,
					agent_id: 'agnt_billing',
					agent_name: '',
					api_vendor: null,
					api_name: null,
					api_version: null,
					operation_id: null,
					created_at: 'never',
					expires_at: '',
					agent_reason: 42,
					request: { method: null, url: null, body: { nested: true } },
				}),
			),
		);
		renderHost('/agents?review=held_weird');
		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByText('Deletes data')).toBeInTheDocument();
		expect(within(dialog).getAllByText('The agent').length).toBeGreaterThan(0);
		expect(
			within(dialog).getByText('The held request is no longer available.'),
		).toBeInTheDocument();
		// No expiry stated: still decidable; no "Asked" or "Expires" shown as a dash.
		expect(within(dialog).getByRole('button', { name: 'Approve and run' })).toBeInTheDocument();
		expect(within(dialog).queryByText(/Asked/)).not.toBeInTheDocument();
	});

	it('something that is not a held call is reported, not rendered', async () => {
		seedCall('held_user');
		worker.use(
			http.get('/executions/holds/:id', () =>
				HttpResponse.json({ id: 'u1', email: 'a@b', permissions: ['org:admin'] }),
			),
		);
		renderHost('/agents?review=held_user');
		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByText("Couldn't load this call")).toBeInTheDocument();
		expect(
			within(dialog).getByText("The server didn't return a held call."),
		).toBeInTheDocument();
	});

	it('a bare status line from the server reads as a plain sentence', async () => {
		seedCall('held_500');
		worker.use(
			http.get('/executions/holds/:id', () =>
				HttpResponse.json({ detail: 'Internal Server Error' }, { status: 500 }),
			),
		);
		renderHost('/agents?review=held_500');
		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(
			await within(dialog).findByText(
				"The server couldn't handle this just now. Try again.",
				{},
				{ timeout: 8000 },
			),
		).toBeInTheDocument();
	});
});

describe('a decision on its way', () => {
	it('Escape does not close the pop-up until the decision has landed', async () => {
		seedCall('held_a');
		worker.use(
			http.post('/executions/holds/:id\\:decide', async () => {
				await delay(600);
				return undefined;
			}),
		);
		renderHost('/agents?review=held_a');
		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));
		await userEvent.keyboard('{Escape}');
		expect(screen.getByRole('dialog', { name: 'Review call' })).toBeInTheDocument();
		// The last waiting call: the pop-up closes with its receipt once the decision lands.
		expect(await screen.findByText(/^Approved · /, {}, { timeout: 4000 })).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Review call' })).not.toBeInTheDocument(),
		);
		expect(getMockApproval('held_a')?.state).toBe('approved');
	});

	it('never lands on a next call that was settled behind the list', async () => {
		seedCall('held_a', 'createCharge', 30);
		seedCall('held_b', 'createRefund', 20);
		renderHost('/agents?review=held_a');
		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('1 of 2 waiting for you');
		// A teammate decides held_b; the list the pop-up holds does not know yet.
		getMockApproval('held_b')!.state = 'approved';

		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));
		expect(await screen.findByText(/^Approved · /, {}, { timeout: 4000 })).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Review call' })).not.toBeInTheDocument(),
		);
		expect(screen.queryByText('“Create a refund”')).not.toBeInTheDocument();
	});
});
