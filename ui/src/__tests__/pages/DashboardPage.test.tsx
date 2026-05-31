import { http, HttpResponse, delay } from 'msw';
import axe from 'axe-core';
import { screen, waitFor, renderWithProviders } from '../test-utils';
import { worker } from '../mocks/browser';
import DashboardPage from '@/pages/DashboardPage';

describe('DashboardPage', () => {
	it('renders the dashboard heading', async () => {
		renderWithProviders(<DashboardPage />);
		expect(await screen.findByRole('heading', { name: /dashboard/i })).toBeInTheDocument();
	});

	it('shows stat values with populated data', async () => {
		worker.use(
			http.get('/apis', () => HttpResponse.json({ data: [{ id: 'a1' }], total: 5, page: 1 })),
			http.get('/toolkits', () =>
				HttpResponse.json([
					{ id: 'tk1', name: 'Stripe' },
					{ id: 'tk2', name: 'GitHub' },
				]),
			),
			http.get('/workflows', () => HttpResponse.json([{ slug: 'w1' }])),
			http.get('/traces', () => HttpResponse.json({ traces: [], total: 42 })),
		);

		renderWithProviders(<DashboardPage />);

		await waitFor(() => {
			expect(screen.getByText('5')).toBeInTheDocument();
			expect(screen.getByText('2')).toBeInTheDocument();
		});
	});

	it('shows empty state message when no traces', async () => {
		renderWithProviders(<DashboardPage />);

		expect(await screen.findByText(/no executions yet/i)).toBeInTheDocument();
	});

	it('renders recent trace data in the table', async () => {
		worker.use(
			http.get('/traces', () =>
				HttpResponse.json({
					traces: [
						{
							id: 't1',
							toolkit_id: 'stripe-tk',
							operation_id: 'createPayment',
							http_status: 200,
							duration_ms: 150,
							created_at: Math.floor(Date.now() / 1000) - 30,
						},
					],
					total: 1,
				}),
			),
		);

		renderWithProviders(<DashboardPage />);

		expect(await screen.findByText('stripe-tk')).toBeInTheDocument();
		expect(screen.getByText('createPayment')).toBeInTheDocument();
		expect(screen.getByText('200')).toBeInTheDocument();
		expect(screen.getByText('150ms')).toBeInTheDocument();
	});

	it('shows loading dashes before data arrives', async () => {
		worker.use(
			http.get('/apis', async () => {
				await delay(300);
				return HttpResponse.json({ data: [], total: 3, page: 1 });
			}),
		);

		renderWithProviders(<DashboardPage />);

		expect(screen.getAllByText('—').length).toBeGreaterThan(0);

		await waitFor(() => {
			expect(screen.getByText('3')).toBeInTheDocument();
		});
	});

	it('handles API error gracefully (does not crash)', async () => {
		worker.use(http.get('/apis', () => HttpResponse.error()));

		renderWithProviders(<DashboardPage />);
		expect(await screen.findByRole('heading', { name: /dashboard/i })).toBeInTheDocument();
	});

	it('renders quick action links', async () => {
		renderWithProviders(<DashboardPage />);
		await screen.findByRole('heading', { name: /dashboard/i });

		expect(screen.getByText('Discover APIs')).toBeInTheDocument();
		expect(screen.getByText('Add Credential')).toBeInTheDocument();
		expect(screen.getByText('Create Toolkit')).toBeInTheDocument();
		expect(screen.getByText('Open Workspace')).toBeInTheDocument();
	});

	it('has no accessibility violations', async () => {
		const { container } = renderWithProviders(<DashboardPage />);
		await screen.findByRole('heading', { name: /dashboard/i });
		const results = await axe.run(container);
		expect(results.violations).toEqual([]);
	});
});
