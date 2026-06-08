import { http, HttpResponse } from 'msw';
import { screen, waitFor, renderWithProviders, userEvent } from '../test-utils';
import { worker } from '../mocks/browser';
import { useToolkitDetailSheet } from '@/hooks/useToolkitDetailSheet';
import { ToolkitDetailSheet } from '@/components/toolkits/ToolkitDetailSheet';

/**
 * Harness: a tiny host that drives the sheet from the `?toolkit=` URL
 * param via the real hook, mirroring how `ToolkitsPage` / `WorkspaceView`
 * mount it.
 */
function SheetHost() {
	const sheet = useToolkitDetailSheet();
	return (
		<ToolkitDetailSheet
			toolkitId={sheet.stickyId}
			open={sheet.open}
			onClose={sheet.closeSheet}
			onAfterClose={sheet.clearSticky}
		/>
	);
}

function renderSheet(id = 'test-tk') {
	return renderWithProviders(<SheetHost />, {
		route: `/toolkits?toolkit=${id}`,
		path: '/toolkits',
	});
}

describe('ToolkitDetailSheet', () => {
	it('renders the toolkit identity in the header when opened via ?toolkit=', async () => {
		renderSheet();

		expect(await screen.findByRole('dialog')).toBeInTheDocument();
		// Name appears in the sheet header.
		expect(await screen.findAllByText('Test Toolkit')).not.toHaveLength(0);
	});

	it('renders the toolkit body (keys + credentials sections)', async () => {
		worker.use(
			http.get('/toolkits/:id', () =>
				HttpResponse.json({
					id: 'test-tk',
					name: 'Test Toolkit',
					description: 'desc',
					disabled: false,
					credentials: [
						{ credential_id: 'c1', label: 'Stripe Token', api_id: 'stripe.com' },
					],
				}),
			),
		);

		renderSheet();
		expect(await screen.findByText(/api keys/i)).toBeInTheDocument();
		expect(await screen.findByText(/bound credentials/i)).toBeInTheDocument();
		expect(await screen.findByText('Stripe Token')).toBeInTheDocument();
	});

	it('closes when the close button is clicked', async () => {
		const user = userEvent.setup();
		renderSheet();

		await screen.findByRole('dialog');
		await user.click(screen.getByRole('button', { name: /close detail panel/i }));

		await waitFor(
			() => {
				expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
			},
			{ timeout: 2000 },
		);
	});

	it('shows the suspended pill for a disabled toolkit', async () => {
		worker.use(
			http.get('/toolkits/:id', () =>
				HttpResponse.json({
					id: 'test-tk',
					name: 'Test Toolkit',
					description: 'desc',
					disabled: true,
					credentials: [],
				}),
			),
		);

		renderSheet();
		// Suspended is surfaced both in the header pill and the keys-card
		// banner, so assert at least one is present.
		expect((await screen.findAllByText(/suspended/i)).length).toBeGreaterThan(0);
	});

	it('lists agents bound to the toolkit', async () => {
		worker.use(
			http.get('/toolkits/:id/agents', () =>
				HttpResponse.json({
					agents: [
						{
							client_id: 'agnt_alpha',
							client_name: 'Alpha Bot',
							status: 'approved',
						},
						{
							client_id: 'agnt_beta',
							client_name: 'Beta Bot',
							status: 'disabled',
						},
					],
				}),
			),
		);

		renderSheet();
		expect(await screen.findByText(/bound agents \(2\)/i)).toBeInTheDocument();
		expect(await screen.findByText('Alpha Bot')).toBeInTheDocument();
		expect(await screen.findByText('Beta Bot')).toBeInTheDocument();
	});

	it('shows an empty state when no agents are bound', async () => {
		renderSheet();
		expect(await screen.findByText(/bound agents \(0\)/i)).toBeInTheDocument();
		expect(
			await screen.findByText(/no agents are granted this toolkit yet/i),
		).toBeInTheDocument();
	});
});
