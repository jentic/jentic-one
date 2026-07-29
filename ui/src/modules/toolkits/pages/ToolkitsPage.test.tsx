import { describe, expect, it } from 'vitest';
import {
	checkA11y,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { ToolkitsPage } from '@/modules/toolkits/pages/ToolkitsPage';

describe('ToolkitsPage', () => {
	it('renders the seeded toolkits from the mocked list endpoint', async () => {
		renderWithProviders(<ToolkitsPage />, { route: '/toolkits' });

		expect(await screen.findByText('GitHub Tools')).toBeInTheDocument();
		expect(screen.getByText('Billing (suspended)')).toBeInTheDocument();
		// Suspended toolkit gets the SUSPENDED pill.
		expect(screen.getByText('SUSPENDED')).toBeInTheDocument();
	});

	it('filters the list by status via the segmented toggle', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitsPage />, { route: '/toolkits' });

		await screen.findByText('GitHub Tools');
		expect(screen.getByText('Billing (suspended)')).toBeInTheDocument();

		// Narrow to Active → the suspended toolkit drops out.
		await user.click(screen.getByRole('button', { name: 'Active' }));
		expect(screen.getByText('GitHub Tools')).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.queryByText('Billing (suspended)')).not.toBeInTheDocument(),
		);

		// Narrow to Suspended → the active toolkit drops out.
		await user.click(screen.getByRole('button', { name: 'Suspended' }));
		expect(screen.getByText('Billing (suspended)')).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByText('GitHub Tools')).not.toBeInTheDocument());
	});

	it('filters the list by the search term', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitsPage />, { route: '/toolkits' });

		await screen.findByText('GitHub Tools');
		await user.type(screen.getByLabelText('Filter toolkits'), 'billing');

		await waitFor(() => expect(screen.queryByText('GitHub Tools')).not.toBeInTheDocument());
		expect(screen.getByText('Billing (suspended)')).toBeInTheDocument();
	});

	it('has no critical accessibility violations', async () => {
		const { container } = renderWithProviders(<ToolkitsPage />, { route: '/toolkits' });
		await screen.findByText('GitHub Tools');
		// Let the framer-motion staggered entrance settle so axe samples final
		// (fully-opaque) colours rather than mid-fade blended ones.
		await new Promise((resolve) => setTimeout(resolve, 1200));
		await checkA11y(container);
	});

	it('creates a toolkit and reveals the one-time key before handing off', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitsPage />, { route: '/toolkits' });
		await screen.findByText('GitHub Tools');

		await user.click(screen.getByRole('button', { name: /new toolkit/i }));
		await user.type(screen.getByLabelText('Name'), 'Slack Tools');
		await user.click(screen.getByRole('button', { name: /^create$/i }));

		// Step 2: the one-time plaintext key is revealed in the dialog (it used
		// to be silently discarded) with the hand-off CTA.
		expect(await screen.findByText('jntc_live_mockplaintextkey_show_once')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /open toolkit/i })).toBeInTheDocument();

		// Dismissing the dialog wipes the plaintext; the new toolkit is in the list.
		await user.click(screen.getByRole('button', { name: 'Close' }));
		await waitFor(() =>
			expect(
				screen.queryByText('jntc_live_mockplaintextkey_show_once'),
			).not.toBeInTheDocument(),
		);
		expect(await screen.findByText('Slack Tools')).toBeInTheDocument();
	});

	it('binds selected credentials inline during create (credential_ids)', async () => {
		worker.use(
			http.get('/credentials', () =>
				HttpResponse.json({
					data: [
						{
							credential_id: 'cred_slack',
							name: 'Slack token',
							type: 'oauth2',
							provider: 'manual',
							active: true,
							api: { vendor: 'slack', name: 'slack-api', version: 'v2' },
							created_at: '2026-05-01T10:00:00Z',
							updated_at: null,
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		const user = userEvent.setup();
		renderWithProviders(<ToolkitsPage />, { route: '/toolkits' });
		await screen.findByText('GitHub Tools');

		await user.click(screen.getByRole('button', { name: /new toolkit/i }));
		await user.type(screen.getByLabelText('Name'), 'Comms');
		// The optional credential multi-select lists the workspace credential.
		await user.click(await screen.findByRole('checkbox', { name: /bind slack token/i }));
		await user.click(screen.getByRole('button', { name: /^create$/i }));

		// The key step notes the zero-rules inline bind (broker default-deny).
		expect(
			await screen.findByText(/1 credential bound with no permission rules/i),
		).toBeInTheDocument();
	});

	it('surfaces an error when the list endpoint fails', async () => {
		worker.use(createErrorHandler('get', '/toolkits', { status: 500 }));
		renderWithProviders(<ToolkitsPage />, { route: '/toolkits' });

		await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
	});
});
