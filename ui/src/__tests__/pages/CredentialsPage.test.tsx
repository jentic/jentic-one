import { http, HttpResponse, delay } from 'msw';
import { screen, renderWithProviders, createErrorHandler } from '../test-utils';
import { worker } from '../mocks/browser';
import CredentialsPage from '@/pages/CredentialsPage';

describe('CredentialsPage', () => {
	it('renders loading state', async () => {
		worker.use(
			http.get('/credentials', async () => {
				await delay('infinite');
				return HttpResponse.json({ data: [] });
			}),
		);

		renderWithProviders(<CredentialsPage />);

		expect(screen.getByText('Loading credentials...')).toBeInTheDocument();
	});

	it('renders empty state when no credentials', async () => {
		worker.use(http.get('/credentials', () => HttpResponse.json({ data: [], total: 0 })));

		renderWithProviders(<CredentialsPage />);

		expect(await screen.findByText('No credentials stored')).toBeInTheDocument();
		expect(
			screen.getByText(/Add a credential to authenticate agents with external APIs/),
		).toBeInTheDocument();
	});

	it('renders credential list when populated', async () => {
		worker.use(
			http.get('/credentials', () =>
				HttpResponse.json({
					data: [
						{
							id: 'c-1',
							label: 'My API Key',
							api_id: 'stripe',
							auth_type: 'bearer',
						},
					],
				}),
			),
		);

		renderWithProviders(<CredentialsPage />);

		expect(await screen.findByText('My API Key')).toBeInTheDocument();
		expect(screen.getByText('stripe')).toBeInTheDocument();
	});

	it('renders error state when API fails', async () => {
		worker.use(createErrorHandler('get', '/credentials', { networkError: true }));

		renderWithProviders(<CredentialsPage />);

		expect(
			await screen.findByText('Failed to load credentials. Please try refreshing the page.'),
		).toBeInTheDocument();
	});

	it('renders page header with correct title', async () => {
		renderWithProviders(<CredentialsPage />);

		expect(
			await screen.findByRole('heading', { name: /^credentials$/i, level: 1 }),
		).toBeInTheDocument();
	});
});
