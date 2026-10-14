import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import {
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	within,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import { PendingApprovalsBadge } from '@/shared/approvals';
import ApprovalsPage from '@/modules/agents/pages/ApprovalsPage';
import ApprovalDetailPage from '@/modules/agents/pages/ApprovalDetailPage';
import {
	getMockApproval,
	resetApprovalsStore,
	seedMockApproval,
} from '@/modules/agents/mocks/approvalsHandlers';
import { isDecidable } from '@/modules/agents/lib/approvalState';

function renderDetail(id: string) {
	return renderWithProviders(
		<>
			<ApprovalDetailPage />
			<Toaster />
		</>,
		{ route: `/agents/approvals/${id}`, path: '/agents/approvals/:id' },
	);
}

describe('Approvals pages', () => {
	beforeEach(() => {
		resetApprovalsStore();
		setToken('mock-access-token');
	});

	it('lists pending approvals by default and filters by state', async () => {
		const { container } = renderWithProviders(<ApprovalsPage />, {
			route: '/agents/approvals',
		});
		expect(await screen.findByText('POST /v1/charges')).toBeInTheDocument();
		expect(screen.queryByText('POST /v1/refunds')).not.toBeInTheDocument();
		expect(screen.getByRole('heading', { level: 1, name: 'Approvals' })).toBeInTheDocument();

		await userEvent.selectOptions(screen.getByLabelText('Filter by state'), 'denied');
		expect(await screen.findByText('POST /v1/refunds')).toBeInTheDocument();
		await checkA11y(container);
	});

	it('shows the agent, owner, matched rule and the held request body', async () => {
		const { container } = renderDetail('held_pending1');
		expect(await screen.findByText('POST /v1/charges')).toBeInTheDocument();
		expect(screen.getByText('usr_owner')).toBeInTheDocument();
		expect(screen.getByText('apr_hold_posts')).toBeInTheDocument();
		expect(
			screen.getByText('POST https://api.stripe.com/v1/charges?expand=balance'),
		).toBeInTheDocument();
		const body = screen.getByLabelText('Request body');
		expect(body.textContent).toContain('"amount": 500');
		await checkA11y(container);
	});

	it('approves a pending approval with the approve decision and reason', async () => {
		// The mock store answers 422 to anything but approve/deny and records
		// the decision, so the outcome card proves what was sent.
		renderDetail('held_pending1');
		await screen.findByText('POST /v1/charges');
		await userEvent.type(screen.getByLabelText('Reason (optional)'), 'looks right');
		await userEvent.click(screen.getByRole('button', { name: 'Approve and run' }));
		expect(await screen.findByText('Outcome')).toBeInTheDocument();
		expect(screen.getByText('looks right')).toBeInTheDocument();
		expect(screen.getAllByText('Approved').length).toBeGreaterThan(0);
		expect(screen.queryByRole('button', { name: 'Approve and run' })).not.toBeInTheDocument();
	});

	it('clears the pending-count badge once the decision lands', async () => {
		renderWithProviders(
			<>
				<PendingApprovalsBadge />
				<ApprovalDetailPage />
			</>,
			{ route: '/agents/approvals/held_pending1', path: '/agents/approvals/:id' },
		);
		expect(await screen.findByLabelText('1 call awaiting your approval')).toBeInTheDocument();
		await userEvent.type(screen.getByLabelText('Reason (optional)'), 'not this one');
		await userEvent.click(screen.getByRole('button', { name: 'Deny' }));
		expect(await screen.findByText('not this one')).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.queryByLabelText(/awaiting your approval/)).not.toBeInTheDocument(),
		);
	});

	it('reports a decision that lost the race', async () => {
		worker.use(
			http.post('/executions/holds/:id\\:decide', () =>
				HttpResponse.json(
					{
						type: 'held_execution_already_settled',
						status: 409,
						detail: "Held execution 'held_pending1' is already denied",
					},
					{ status: 409 },
				),
			),
		);
		renderDetail('held_pending1');
		await screen.findByText('POST /v1/charges');
		await userEvent.click(screen.getByRole('button', { name: 'Deny' }));
		expect(await screen.findByText(/already denied/)).toBeInTheDocument();
	});

	it('shows a decided approval read-only', async () => {
		renderDetail('held_denied1');
		expect(await screen.findByText('Not this customer')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument();
	});

	it('tells a non-reviewer who can review instead of showing the approval', async () => {
		renderDetail('held_someone_else');
		expect(await screen.findByText("You can't review this approval")).toBeInTheDocument();
		expect(screen.getByText(/Ask them to open this link/)).toBeInTheDocument();
	});

	it('offers the verbs to a viewer with no jobs permission', async () => {
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json({
					id: 'usr_owner',
					email: 'owner@local',
					first_name: 'Owner',
					last_name: 'User',
					active: true,
					permissions: ['agents:read', 'agents:write'],
					must_change_password: false,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
		const { container } = renderWithProviders(
			<AuthProvider>
				<ApprovalDetailPage />
			</AuthProvider>,
			{ route: '/agents/approvals/held_pending1', path: '/agents/approvals/:id' },
		);
		expect(await screen.findByRole('button', { name: 'Approve and run' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument();
		expect(screen.queryByText(/jobs:write/)).not.toBeInTheDocument();
		await checkA11y(container);
	});

	it('explains a decision the server refuses, in place of the verbs', async () => {
		worker.use(
			http.post('/executions/holds/:id\\:decide', () =>
				HttpResponse.json(
					{
						type: 'held_execution_decide_forbidden',
						status: 403,
						detail: "You may not decide held execution 'held_pending1'",
					},
					{ status: 403 },
				),
			),
		);
		const { container } = renderDetail('held_pending1');
		await screen.findByText('POST /v1/charges');
		await userEvent.click(screen.getByRole('button', { name: 'Approve and run' }));
		expect(await screen.findByText(/can't decide it/)).toBeInTheDocument();
		expect(screen.getByText(/Only the agent's owner or an org admin/)).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Approve and run' })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument();
		// Nothing was decided.
		expect(getMockApproval('held_pending1')?.state).toBe('pending');
		await checkA11y(container);
	});

	it('shows a pending call as held', async () => {
		renderDetail('held_pending1');
		const card = await screen.findByRole('heading', { name: /Call status/ });
		expect(card).toHaveTextContent('Held');
		expect(screen.getByText(/held until someone approves or denies it/)).toBeInTheDocument();
		expect(screen.queryByRole('list', { name: 'Call progress' })).not.toBeInTheDocument();
	});

	it('moves an approved call to queued and follows it through its steps', async () => {
		renderDetail('held_pending1');
		await screen.findByText('POST /v1/charges');
		await userEvent.click(screen.getByRole('button', { name: 'Approve and run' }));
		const progress = await screen.findByRole('list', { name: 'Call progress' });
		const current = within(progress)
			.getAllByRole('listitem')
			.find((li) => li.getAttribute('aria-current') === 'step');
		expect(current).toHaveTextContent('Approved');
		expect(screen.getByText(/queued to run/)).toBeInTheDocument();
		// No record before the call runs.
		expect(screen.queryByRole('link', { name: /View execution/ })).not.toBeInTheDocument();
	});

	it.each([
		['running', 'Running', /The call is running/],
		['completed', 'Completed', /ran and completed/],
		['failed', 'Failed', /ran and failed/],
		['cancelled', 'Cancelled', /cancelled before it finished/],
	] as const)('shows an approved %s call', async (status, label, sentence) => {
		// The record exists once the call has run to completion or failure.
		const ran = status === 'completed' || status === 'failed';
		const row = seedMockApproval({
			state: 'approved',
			execution_status: status,
			execution_id: ran ? 'exec_ran_1' : null,
			decided_at: new Date().toISOString(),
			decided_by: 'usr_owner',
		});
		const { container } = renderDetail(row.id);
		const progress = await screen.findByRole('list', { name: 'Call progress' });
		const current = within(progress)
			.getAllByRole('listitem')
			.find((li) => li.getAttribute('aria-current') === 'step');
		expect(current).toHaveTextContent(label);
		expect(screen.getByText(sentence)).toBeInTheDocument();
		const record = screen.queryByRole('link', { name: 'View execution exec_ran_1' });
		if (ran) {
			expect(record).toHaveAttribute('href', '/monitor?show=calls&execution_id=exec_ran_1');
		} else {
			expect(record).not.toBeInTheDocument();
		}
		// A held call never links its job.
		expect(screen.queryByRole('link', { name: /job/i })).not.toBeInTheDocument();
		await checkA11y(container);
	});

	it('says a denied call does not run, without a progress trail', async () => {
		renderDetail('held_denied1');
		expect(
			await screen.findByText('The call was denied and does not run.'),
		).toBeInTheDocument();
		expect(screen.queryByRole('list', { name: 'Call progress' })).not.toBeInTheDocument();
		expect(screen.queryByRole('link', { name: /View execution/ })).not.toBeInTheDocument();
	});

	it('surfaces a load failure', async () => {
		worker.use(createErrorHandler('get', '/executions/holds/:id', { status: 500 }));
		renderDetail('held_pending1');
		expect(await screen.findByText('Failed to load approval')).toBeInTheDocument();
	});
});

describe('isDecidable', () => {
	it('is true only for a pending approval before its expiry', () => {
		const now = new Date('2026-10-06T12:00:00Z');
		expect(isDecidable('pending', '2026-10-06T13:00:00Z', now)).toBe(true);
		expect(isDecidable('pending', '2026-10-06T11:00:00Z', now)).toBe(false);
		expect(isDecidable('approved', '2026-10-06T13:00:00Z', now)).toBe(false);
	});
});
