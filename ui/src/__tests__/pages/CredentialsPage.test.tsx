import { http, HttpResponse, delay } from 'msw';
import { screen, waitFor, renderWithProviders, userEvent, createErrorHandler } from '../test-utils';
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

		expect(screen.getByTestId('credentials-skeleton')).toBeInTheDocument();
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

	it('keeps the credential listed and dismisses the dialog when delete fails', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/credentials', () =>
				HttpResponse.json({
					data: [
						{ id: 'c-1', label: 'My API Key', api_id: 'stripe', auth_type: 'bearer' },
					],
				}),
			),
			http.get('/credentials/:cid/bindings', () => HttpResponse.json([])),
			createErrorHandler('delete', '/credentials/:cid', { status: 403 }),
		);

		renderWithProviders(<CredentialsPage />);
		expect(await screen.findByText('My API Key')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /delete credential my api key/i }));

		const confirmBtn = await screen.findByRole('button', { name: /^delete credential$/i });
		await user.click(confirmBtn);

		// On failure the dialog is dismissed (no longer stuck on a spinner) and the
		// credential stays in the list — the onError handler surfaces a toast.
		await waitFor(() =>
			expect(
				screen.queryByRole('button', { name: /^delete credential$/i }),
			).not.toBeInTheDocument(),
		);
		// The failed-delete flow re-renders the list, which can momentarily mount
		// two copies of the row on a loaded runner. Wait for it to settle to a
		// single credential so the assertion isn't sampled mid-transition.
		await waitFor(() => expect(screen.getAllByText('My API Key')).toHaveLength(1));
	});
});
