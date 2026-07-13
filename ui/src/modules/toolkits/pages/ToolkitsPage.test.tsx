import { describe, expect, it } from 'vitest';
import {
	checkA11y,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	createErrorHandler,
} from '@/__tests__/test-utils';
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

	it('creates a toolkit through the New toolkit dialog', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitsPage />, { route: '/toolkits' });
		await screen.findByText('GitHub Tools');

		await user.click(screen.getByRole('button', { name: /new toolkit/i }));
		await user.type(screen.getByLabelText('Name'), 'Slack Tools');
		await user.click(screen.getByRole('button', { name: /^create$/i }));

		expect(await screen.findByText('Slack Tools')).toBeInTheDocument();
	});

	it('surfaces an error when the list endpoint fails', async () => {
		worker.use(createErrorHandler('get', '/toolkits', { status: 500 }));
		renderWithProviders(<ToolkitsPage />, { route: '/toolkits' });

		await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
	});
});
