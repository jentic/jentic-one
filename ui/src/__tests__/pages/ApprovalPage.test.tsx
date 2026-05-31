import userEvent from '@testing-library/user-event';
import { http, HttpResponse, delay } from 'msw';
import axe from 'axe-core';
import { worker } from '../mocks/browser';
import { screen, renderWithProviders, createErrorHandler } from '../test-utils';
import ApprovalPage from '@/pages/ApprovalPage';

const ROUTE = '/approve/tk-1/req-1';
const PATH = '/approve/:toolkit_id/:req_id';

describe('ApprovalPage', () => {
	it('renders pending request details', async () => {
		worker.use(
			http.get('/toolkits/:id', () =>
				HttpResponse.json({
					id: 'tk-1',
					name: 'Stripe Toolkit',
					simulate: false,
					disabled: false,
					keys: [],
					credentials: [],
				}),
			),
			http.get('/toolkits/:id/access-requests/:reqId', () =>
				HttpResponse.json({
					id: 'req-1',
					toolkit_id: 'tk-1',
					type: 'grant',
					status: 'pending',
					reason: 'Need access to payments API',
					description: 'Agent requires Stripe access',
					created_at: Math.floor(Date.now() / 1000),
					payload: { credential_id: 'cred-1', api_id: 'stripe-api', rules: [] },
				}),
			),
		);

		renderWithProviders(<ApprovalPage />, { route: ROUTE, path: PATH });

		expect(await screen.findByText('Stripe Toolkit')).toBeInTheDocument();
		expect(screen.getByText(/Need access to payments API/)).toBeInTheDocument();
		expect(screen.getByText(/Agent requires Stripe access/)).toBeInTheDocument();
		expect(screen.getByText('Approve Request')).toBeInTheDocument();
		expect(screen.getByText('Deny Request')).toBeInTheDocument();
	});

	it('shows loading state while fetching', async () => {
		worker.use(
			http.get('/user/me', async () => {
				await delay(200);
				return HttpResponse.json({ logged_in: true, username: 'admin', role: 'admin' });
			}),
		);

		renderWithProviders(<ApprovalPage />, { route: ROUTE, path: PATH });

		expect(screen.getByRole('status')).toBeInTheDocument();
	});

	it('approves request and shows success message', async () => {
		const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

		worker.use(
			http.get('/toolkits/:id/access-requests/:reqId', () =>
				HttpResponse.json({
					id: 'req-1',
					toolkit_id: 'tk-1',
					type: 'grant',
					status: 'pending',
					reason: 'Need access',
					created_at: Math.floor(Date.now() / 1000),
					payload: {},
				}),
			),
			http.post('/toolkits/:id/access-requests/:reqId/approve', () =>
				HttpResponse.json({ status: 'approved' }),
			),
		);

		vi.useFakeTimers({ shouldAdvanceTime: true });
		try {
			renderWithProviders(<ApprovalPage />, { route: ROUTE, path: PATH });

			const approveBtn = await screen.findByText('Approve Request');
			await user.click(approveBtn);

			expect(await screen.findByText('Request Approved')).toBeInTheDocument();
			expect(screen.getByText(/Redirecting to toolkits/)).toBeInTheDocument();
		} finally {
			vi.useRealTimers();
		}
	});

	it('denies request and shows denied message', async () => {
		const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

		worker.use(
			http.get('/toolkits/:id/access-requests/:reqId', () =>
				HttpResponse.json({
					id: 'req-1',
					toolkit_id: 'tk-1',
					type: 'grant',
					status: 'pending',
					reason: 'Need access',
					created_at: Math.floor(Date.now() / 1000),
					payload: {},
				}),
			),
			http.post('/toolkits/:id/access-requests/:reqId/deny', () =>
				HttpResponse.json({ status: 'denied' }),
			),
		);

		vi.useFakeTimers({ shouldAdvanceTime: true });
		try {
			renderWithProviders(<ApprovalPage />, { route: ROUTE, path: PATH });

			const denyBtn = await screen.findByText('Deny Request');
			await user.click(denyBtn);

			expect(await screen.findByText('Request Denied')).toBeInTheDocument();
			expect(screen.getByText(/Redirecting to toolkits/)).toBeInTheDocument();
		} finally {
			vi.useRealTimers();
		}
	});

	it('shows not-found state when request returns 404', async () => {
		worker.use(
			createErrorHandler('get', '/toolkits/:id/access-requests/:reqId', {
				status: 404,
				body: { detail: 'Not found' },
			}),
		);

		renderWithProviders(<ApprovalPage />, { route: ROUTE, path: PATH });

		expect(await screen.findByText('Request Not Found')).toBeInTheDocument();
	});

	it('has no accessibility violations', async () => {
		const { container } = renderWithProviders(<ApprovalPage />, { route: ROUTE, path: PATH });
		await screen.findByText('Approve Request');
		const results = await axe.run(container);
		expect(results.violations).toEqual([]);
	});
});
