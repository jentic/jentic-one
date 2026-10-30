/**
 * The held-call review pop-up (`?review=<id>`), rendered by the shell's host
 * on any page: what it shows for a waiting call, what a decision sends (the
 * deny reason with a deny only), how it moves on to the next waiting call
 * (same agent first) with a receipt, how a decision that lost the race (409)
 * moves on too, and the states with no verbs (expired, refused, not visible).
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
import { clearToken, setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import { ApprovalReviewHost } from '@/shared/approvals';
import {
	clearApprovalsStore,
	getMockApproval,
	resetApprovalsStore,
	seedMockApproval,
} from '@/modules/agents/mocks/approvalsHandlers';

const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();
const hoursFromNow = (n: number) => new Date(Date.now() + n * 3_600_000).toISOString();

/** The operation names the API's spec gives (the pop-up's headlines). */
const OPERATIONS: Record<string, string> = {
	createCharge: 'Create a charge',
	createRefund: 'Create a refund',
	mergePull: 'Merge a pull request',
};

function seedCall(id: string, overrides: Parameters<typeof seedMockApproval>[0] = {}) {
	return seedMockApproval({
		id,
		agent_id: 'agnt_billing',
		agent_name: 'billing-ops',
		agent_owner_id: 'usr_owner',
		operation_id: 'createCharge',
		created_at: minutesAgo(5),
		expires_at: hoursFromNow(23),
		request: {
			method: 'POST',
			url: 'https://api.stripe.com/v1/charges?expand=balance',
			body: '{"amount":129900,"currency":"eur"}',
			body_truncated: false,
		},
		...overrides,
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

function currentParams(): URLSearchParams {
	const [, query = ''] = screen.getByTestId('location').textContent!.split('?');
	return new URLSearchParams(query);
}

/** An element whose whole text is `text` (receipts mix a bold word and the headline). */
function byWholeText(text: string) {
	return (_: string, el: Element | null) => el?.tagName === 'P' && el.textContent === text;
}

const REASON_LABEL = 'Reason (optional), sent to the agent if you deny';

let decisions: { id: string; body: unknown }[];

beforeEach(() => {
	clearApprovalsStore();
	decisions = [];
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

describe('the headline of a call the spec leaves unnamed', () => {
	it('shows the spec’s path template, not the real URL with its ids', async () => {
		// Google Sheets names none of its operations: only the template is known.
		worker.use(
			http.get('/apis/:vendor/:name/:version/operations', () =>
				HttpResponse.json({
					data: [
						{
							operation_id: 'sheets.values.update',
							name: null,
							method: 'PUT',
							path: '/v4/spreadsheets/{spreadsheetId}/values/{range}',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		const realPath = '/v4/spreadsheets/1vft6FENY1ZXwzdvLgqntGanEUM/values/A4:D4';
		seedCall('held_sheet', {
			method: 'PUT',
			operation_id: 'sheets.values.update',
			path: realPath,
			request: {
				method: 'PUT',
				url: `https://sheets.googleapis.com${realPath}`,
				body: '{"values":[["a"]]}',
				body_truncated: false,
			},
		});
		renderHost('/agents?agent=agnt_billing&review=held_sheet');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(
			await within(dialog).findByText('PUT /v4/spreadsheets/{spreadsheetId}/values/{range}'),
		).toBeInTheDocument();
		expect(within(dialog).getByText('wants to run')).toBeInTheDocument();
		// The real address is still there, in the request details.
		expect(
			within(dialog).getByText(`https://sheets.googleapis.com${realPath}`),
		).toBeInTheDocument();
	});

	it('falls back to the call’s own route when the spec can’t be read', async () => {
		worker.use(
			http.get('/apis/:vendor/:name/:version/operations', () =>
				HttpResponse.json({ type: 'no_current_revision', status: 404 }, { status: 404 }),
			),
		);
		seedCall('held_plain', { method: 'POST', path: '/v1/charges' });
		renderHost('/agents?agent=agnt_billing&review=held_plain');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByText('POST /v1/charges')).toBeInTheDocument();
	});
});

describe('review pop-up on a waiting call', () => {
	it('shows who is asking for what, the request, and who can decide', async () => {
		seedCall('held_a');
		renderHost('/agents?review=held_a');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByText('“Create a charge”')).toBeInTheDocument();
		expect(within(dialog).getByText('wants to run')).toBeInTheDocument();
		expect(within(dialog).getByText('1 of 1 waiting for you')).toBeInTheDocument();
		expect(within(dialog).getByText('Asked 5m ago')).toBeInTheDocument();
		expect(within(dialog).getByText('Expires in 23 h')).toBeInTheDocument();
		// Without the org's admins to count, the line names the owner in words.
		expect(
			within(dialog).getByText('The agent’s owner and org admins can decide this'),
		).toBeInTheDocument();
		// A POST is not a delete.
		expect(within(dialog).queryByText('Deletes data')).not.toBeInTheDocument();

		const details = within(dialog).getByRole('region', { name: 'Request details' });
		expect(within(details).getByText('https://api.stripe.com/v1/charges')).toBeInTheDocument();
		expect(within(details).getByText('Expand')).toBeInTheDocument();
		expect(within(details).getByText('balance')).toBeInTheDocument();
		expect(within(details).getByLabelText('Request body').textContent).toContain(
			'"amount": 129900',
		);

		// The deny reason is always there, above the verbs.
		expect(within(dialog).getByLabelText(REASON_LABEL)).toBeInTheDocument();
		expect(within(dialog).getByRole('button', { name: 'Deny' })).toBeEnabled();
		expect(within(dialog).getByRole('button', { name: 'Approve and run' })).toBeEnabled();

		// Opened on the Agents page with no agent selected: the call's agent lands behind it.
		await waitFor(() => expect(currentParams().get('agent')).toBe('agnt_billing'));
		expect(currentParams().get('review')).toBe('held_a');

		await checkA11y(document.body, { modal: true });
	});

	it('tags a DELETE as deleting data, and shows nothing in place of a missing body', async () => {
		seedCall('held_d', {
			method: 'DELETE',
			path: '/v1/subscriptions/sub_1',
			operation_id: null,
			request: {
				method: 'DELETE',
				url: 'https://api.stripe.com/v1/subscriptions/sub_1?invoice_now=true',
				body: null,
				body_truncated: false,
			},
		});
		renderHost('/agents?agent=agnt_other&review=held_d');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByText('Deletes data')).toBeInTheDocument();
		// No operation name: the route is the headline.
		expect(within(dialog).getByText('DELETE /v1/subscriptions/sub_1')).toBeInTheDocument();
		expect(within(dialog).getByText('Invoice now')).toBeInTheDocument();
		// No body: no body block, and no sentence standing in for one.
		expect(within(dialog).queryByLabelText('Request body')).not.toBeInTheDocument();
		expect(within(dialog).queryByText(/request body/i)).not.toBeInTheDocument();
		// Only the two verbs: nothing the backend can't do yet is offered.
		expect(within(dialog).queryByRole('button', { name: 'More' })).not.toBeInTheDocument();

		// An agent already selected stays selected.
		expect(currentParams().get('agent')).toBe('agnt_other');
	});

	it('counts the people who can decide when the viewer can read the org admins', async () => {
		setToken('mock-access-token');
		try {
			seedCall('held_a');
			renderWithProviders(
				<AuthProvider>
					<ApprovalReviewHost />
				</AuthProvider>,
				{ route: '/agents?review=held_a' },
			);
			const dialog = await screen.findByRole('dialog', { name: 'Review call' });
			// The signed-in admin, one more admin (the invited one hasn't joined), and the owner.
			expect(
				await within(dialog).findByText('You and 2 others can decide this'),
			).toBeInTheDocument();
		} finally {
			clearToken();
		}
	});
});

describe("the agent's reason", () => {
	it('shows no box at all when the agent sent none', async () => {
		seedCall('held_a');
		renderHost('/agents?review=held_a');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		expect(
			within(dialog).queryByRole('region', { name: 'Why the agent is asking' }),
		).not.toBeInTheDocument();
		expect(within(dialog).queryByText(/placeholder/i)).not.toBeInTheDocument();
	});

	it("shows the agent's own words when it sent a reason", async () => {
		seedCall('held_a', { agent_reason: '  The renewal invoice is due today.  ' });
		renderHost('/agents?review=held_a');

		const box = await screen.findByRole('region', { name: 'Why the agent is asking' });
		expect(within(box).getByText('“The renewal invoice is due today.”')).toBeInTheDocument();
	});
});

describe('deciding', () => {
	it('sends the deny reason with a deny, and no reason with an approve', async () => {
		seedCall('held_a', { created_at: minutesAgo(30) });
		seedCall('held_b', { created_at: minutesAgo(10), operation_id: 'createRefund' });
		renderHost('/agents?review=held_a');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		await userEvent.type(within(dialog).getByLabelText(REASON_LABEL), '  Already refunded  ');
		await userEvent.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() => expect(decisions).toHaveLength(1));
		expect(decisions[0]).toEqual({
			id: 'held_a',
			body: { decision: 'deny', reason: 'Already refunded' },
		});
		expect(getMockApproval('held_a')?.decision_reason).toBe('Already refunded');

		// On to the next call, with a fresh (empty) reason.
		expect(await within(dialog).findByText('“Create a refund”')).toBeInTheDocument();
		const reason = within(dialog).getByLabelText(REASON_LABEL);
		expect(reason).toHaveValue('');
		await userEvent.type(reason, 'not sent');
		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));

		await waitFor(() => expect(decisions).toHaveLength(2));
		expect(decisions[1]).toEqual({ id: 'held_b', body: { decision: 'approve' } });
		expect(getMockApproval('held_b')?.decision_reason).toBeNull();
	});

	it("moves on to the next waiting call, the same agent's first, then closes with one toast", async () => {
		seedCall('held_a', { created_at: minutesAgo(30) });
		seedCall('held_b', {
			created_at: minutesAgo(20),
			agent_id: 'agnt_release',
			agent_name: 'release-manager',
			operation_id: 'mergePull',
		});
		seedCall('held_c', { created_at: minutesAgo(10), operation_id: 'createRefund' });
		// Opened for every agent (the attention toast's Review).
		renderHost('/agents?agent=agnt_billing&review=held_a&review_all=1');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		expect(within(dialog).getByText('1 of 3 waiting for you')).toBeInTheDocument();

		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));
		// billing-ops' other call comes before release-manager's older one.
		expect(await within(dialog).findByText('“Create a refund”')).toBeInTheDocument();
		expect(currentParams().get('review')).toBe('held_c');
		expect(
			within(dialog).getByText(byWholeText('Approved · Create a charge')),
		).toBeInTheDocument();
		expect(within(dialog).getByText('1 of 2 waiting for you')).toBeInTheDocument();

		await userEvent.click(within(dialog).getByRole('button', { name: 'Deny' }));
		expect(await within(dialog).findByText('“Merge a pull request”')).toBeInTheDocument();
		expect(currentParams().get('review')).toBe('held_b');
		expect(
			within(dialog).getByText(byWholeText('Denied · Create a refund')),
		).toBeInTheDocument();

		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));
		// The last one closes the pop-up with a single toast; no "Done" screen.
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Review call' })).not.toBeInTheDocument(),
		);
		expect(currentParams().has('review')).toBe(false);
		expect(await screen.findByText(/Approved · .*Merge a pull request/)).toBeInTheDocument();
		expect(screen.queryByText(/Approved execution for/)).not.toBeInTheDocument();
		expect(decisions.map((d) => d.id)).toEqual(['held_a', 'held_c', 'held_b']);
	});

	it('steps through the waiting calls without deciding', async () => {
		seedCall('held_a', { created_at: minutesAgo(30) });
		seedCall('held_c', { created_at: minutesAgo(10), operation_id: 'createRefund' });
		renderHost('/agents?agent=agnt_billing&review=held_a');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		await userEvent.click(
			within(dialog).getByRole('button', { name: 'Next: Create a refund' }),
		);
		expect(await within(dialog).findByText('“Create a refund”')).toBeInTheDocument();
		expect(within(dialog).getByText('2 of 2 waiting for you')).toBeInTheDocument();
		expect(decisions).toHaveLength(0);
	});
});

describe('the queue the pop-up walks', () => {
	it('is the open call’s agent’s calls, and closes after the agent’s last', async () => {
		seedCall('held_a', { created_at: minutesAgo(30) });
		seedCall('held_b', { created_at: minutesAgo(20), operation_id: 'createRefund' });
		seedCall('held_other', {
			created_at: minutesAgo(10),
			agent_id: 'agnt_scout',
			agent_name: 'scout',
		});
		renderHost('/agents?agent=agnt_billing&review=held_a');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		// Three calls wait on the viewer; two are this agent's.
		expect(within(dialog).getByText('1 of 2 waiting for you')).toBeInTheDocument();

		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));
		expect(await within(dialog).findByText('“Create a refund”')).toBeInTheDocument();

		// The agent's last: the pop-up closes rather than moving to another agent.
		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Review call' })).not.toBeInTheDocument(),
		);
		expect(currentParams().get('review')).toBeNull();
		expect(getMockApproval('held_other')?.state).toBe('pending');
	});

	it('is every agent’s calls with review_all, the open call’s agent first', async () => {
		seedCall('held_a', { created_at: minutesAgo(30) });
		seedCall('held_other', {
			created_at: minutesAgo(40),
			agent_id: 'agnt_scout',
			agent_name: 'scout',
			operation_id: 'createRefund',
		});
		renderHost('/agents?agent=agnt_billing&review=held_a&review_all=1');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		// scout's call is older, but the open call's agent leads.
		expect(within(dialog).getByText('1 of 2 waiting for you')).toBeInTheDocument();

		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));
		expect(await within(dialog).findByText('“Create a refund”')).toBeInTheDocument();
		expect(currentParams().get('review')).toBe('held_other');
		expect(currentParams().get('review_all')).toBe('1');
	});
});

describe('a decision that lost the race (409)', () => {
	it('says someone else decided first and moves on to the next call', async () => {
		seedCall('held_a', { created_at: minutesAgo(30) });
		seedCall('held_b', { created_at: minutesAgo(10), operation_id: 'createRefund' });
		renderHost('/agents?agent=agnt_billing&review=held_a');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		// A teammate approves it while the pop-up is open.
		const row = getMockApproval('held_a')!;
		Object.assign(row, {
			state: 'approved',
			execution_status: 'queued',
			decided_by: 'usr_teammate',
			decided_at: new Date().toISOString(),
		});

		await userEvent.click(within(dialog).getByRole('button', { name: 'Deny' }));
		expect(await within(dialog).findByText('“Create a refund”')).toBeInTheDocument();
		expect(currentParams().get('review')).toBe('held_b');
		expect(within(dialog).getByText('Someone else decided first')).toBeInTheDocument();
		expect(await within(dialog).findByText(/Approved by someone/)).toBeInTheDocument();
		expect(within(dialog).getByText(/your decision wasn.t applied/)).toBeInTheDocument();
		// The teammate's decision stands.
		expect(getMockApproval('held_a')?.state).toBe('approved');
	});

	it('closes with a toast when it was the last waiting call', async () => {
		seedCall('held_a');
		renderHost('/agents?agent=agnt_billing&review=held_a');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		getMockApproval('held_a')!.state = 'denied';

		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));
		expect(await screen.findByText(/Someone else decided first · /)).toBeInTheDocument();
		expect(screen.queryByRole('dialog', { name: 'Review call' })).not.toBeInTheDocument();
	});
});

describe('states without verbs', () => {
	it('shows an expired call as expired', async () => {
		seedCall('held_x', { created_at: minutesAgo(24 * 60 + 1), expires_at: minutesAgo(1) });
		renderHost('/agents?agent=agnt_billing&review=held_x');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByText('Expired')).toBeInTheDocument();
		expect(within(dialog).getByText(/Nobody decided within 24 h/)).toBeInTheDocument();
		expect(
			within(dialog).queryByRole('button', { name: 'Approve and run' }),
		).not.toBeInTheDocument();
		expect(within(dialog).queryByLabelText(REASON_LABEL)).not.toBeInTheDocument();
	});

	it('shows a call decided before it was opened, with a way to the waiting ones', async () => {
		resetApprovalsStore();
		seedCall('held_b', { operation_id: 'createRefund' });
		renderHost('/agents?agent=agnt_scout&review=held_denied1');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByText('Denied by someone')).toBeInTheDocument();
		expect(within(dialog).getByText('“Not this customer”')).toBeInTheDocument();
		expect(within(dialog).queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument();

		// The way on appears once the waiting list has loaded.
		// The decided call's agent has one other call waiting; billing-ops's is
		// not this agent's and stays out of the count.
		await userEvent.click(
			await within(dialog).findByRole('button', { name: /See 1 waiting call/ }),
		);
		await waitFor(() => expect(currentParams().get('review')).toBe('held_pending1'));
	});

	it('explains a refused decision (403) in place of the verbs', async () => {
		seedCall('held_a');
		worker.use(
			http.post('/executions/holds/:id\\:decide', () =>
				HttpResponse.json(
					{ type: 'held_execution_decide_forbidden', status: 403, detail: 'Forbidden' },
					{ status: 403 },
				),
			),
		);
		renderHost('/agents?agent=agnt_billing&review=held_a');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await within(dialog).findByText('“Create a charge”');
		await userEvent.click(within(dialog).getByRole('button', { name: 'Approve and run' }));

		expect(await within(dialog).findByRole('alert')).toHaveTextContent(
			/can see this call but can.t decide it/,
		);
		expect(
			within(dialog).queryByRole('button', { name: 'Approve and run' }),
		).not.toBeInTheDocument();
		expect(getMockApproval('held_a')?.state).toBe('pending');
	});

	it("says a call the viewer can't see (404) may be gone or theirs to see", async () => {
		renderHost('/agents?agent=agnt_billing&review=held_missing');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByText("We can't find this call")).toBeInTheDocument();
		expect(
			within(dialog).getByText(
				"It may no longer exist, or only its agent's owner or an org admin can see it.",
			),
		).toBeInTheDocument();
		await checkA11y(document.body, { modal: true });

		// The footer's Close (the header's X shares the name).
		const closeButtons = within(dialog).getAllByRole('button', { name: 'Close' });
		await userEvent.click(closeButtons[closeButtons.length - 1]);
		await waitFor(() => expect(currentParams().has('review')).toBe(false));
		expect(currentParams().get('agent')).toBe('agnt_billing');
	});
});
