import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import {
	renderWithProviders,
	screen,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import ApprovalsPage from '@/modules/agents/pages/ApprovalsPage';
import ApprovalDetailPage from '@/modules/agents/pages/ApprovalDetailPage';
import { resetApprovalsStore } from '@/modules/agents/mocks/approvalsHandlers';
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
		const { container } = renderDetail('exap_pending1');
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
		renderDetail('exap_pending1');
		await screen.findByText('POST /v1/charges');
		await userEvent.type(screen.getByLabelText('Reason (optional)'), 'looks right');
		await userEvent.click(screen.getByRole('button', { name: 'Approve and run' }));
		expect(await screen.findByText('Outcome')).toBeInTheDocument();
		expect(screen.getByText('looks right')).toBeInTheDocument();
		expect(screen.getAllByText('Approved').length).toBeGreaterThan(0);
		expect(screen.queryByRole('button', { name: 'Approve and run' })).not.toBeInTheDocument();
	});

	it('reports a decision that lost the race', async () => {
		worker.use(
			http.post('/executions/approvals/:id\\:decide', () =>
				HttpResponse.json(
					{ status: 409, detail: "Execution approval 'exap_pending1' is already denied" },
					{ status: 409 },
				),
			),
		);
		renderDetail('exap_pending1');
		await screen.findByText('POST /v1/charges');
		await userEvent.click(screen.getByRole('button', { name: 'Deny' }));
		expect(await screen.findByText(/already denied/)).toBeInTheDocument();
	});

	it('shows a decided approval read-only', async () => {
		renderDetail('exap_denied1');
		expect(await screen.findByText('Not this customer')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument();
	});

	it('tells a non-reviewer who can review instead of showing the approval', async () => {
		renderDetail('exap_someone_else');
		expect(await screen.findByText("You can't review this approval")).toBeInTheDocument();
		expect(screen.getByText(/Ask them to open this link/)).toBeInTheDocument();
	});

	it('shows a reviewer without jobs:write the held call but not the verbs', async () => {
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json({
					id: 'usr_owner',
					email: 'owner@local',
					first_name: 'Owner',
					last_name: 'User',
					active: true,
					permissions: ['jobs:read'],
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
			{ route: '/agents/approvals/exap_pending1', path: '/agents/approvals/:id' },
		);
		expect(await screen.findByText(/needs the/)).toBeInTheDocument();
		expect(screen.getByText('POST /v1/charges')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Approve and run' })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument();
		await checkA11y(container);
	});

	it('surfaces a load failure', async () => {
		worker.use(createErrorHandler('get', '/executions/approvals/:id', { status: 500 }));
		renderDetail('exap_pending1');
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
