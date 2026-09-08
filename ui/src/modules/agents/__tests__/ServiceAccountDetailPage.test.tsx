import { describe, it, expect, beforeEach } from 'vitest';
import { Routes, Route } from 'react-router';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import ServiceAccountDetailPage from '@/modules/agents/pages/ServiceAccountDetailPage';

function renderDetail(serviceAccountId: string) {
	return renderWithProviders(
		<>
			<Routes>
				<Route
					path="/agents/service-accounts/:serviceAccountId"
					element={<ServiceAccountDetailPage />}
				/>
				<Route path="/agents" element={<div>agents-list-marker</div>} />
			</Routes>
			<Toaster />
		</>,
		{ route: `/agents/service-accounts/${serviceAccountId}` },
	);
}

describe('ServiceAccountDetailPage', () => {
	beforeEach(() => {
		setToken('test-token');
		resetAgentsStore();
	});

	it('renders identity and status once — id lives on the Settings tab', async () => {
		const user = userEvent.setup();
		renderDetail('sva_active_1');
		expect(
			await screen.findByRole('heading', { name: 'metrics-exporter' }),
		).toBeInTheDocument();
		// Pin the status to the header's badge — a bare text query could match
		// any other "Active" on the page and mask a wrong-status bug.
		expect(screen.getByTestId('detail-status-badge')).toHaveTextContent('Active');
		// The raw id lives on Settings (toolkit-console grammar), not the chrome.
		expect(screen.queryByText('sva_active_1')).not.toBeInTheDocument();
		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		expect(await screen.findByText('Account ID')).toBeInTheDocument();
		expect(screen.getByText('sva_active_1')).toBeInTheDocument();
	});

	it('renders a Scopes card with the granted scopes on the Access tab', async () => {
		const user = userEvent.setup();
		renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });
		await user.click(screen.getByRole('tab', { name: 'Access' }));
		const list = await screen.findByRole('list', { name: 'Granted scopes' });
		expect(within(list).getByText('credentials:read')).toBeInTheDocument();
	});

	it('shows an empty pending-access-requests card when none are filed (#619)', async () => {
		const user = userEvent.setup();
		renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });
		await user.click(screen.getByRole('tab', { name: 'Access' }));
		expect(await screen.findByRole('heading', { name: 'Access requests' })).toBeInTheDocument();
		expect(await screen.findByText('No pending access requests')).toBeInTheDocument();
	});

	it('renders a not-found surface for an unknown id', async () => {
		renderDetail('sva_does_not_exist');
		expect(await screen.findByText('Service account not found')).toBeInTheDocument();
	});

	it('gates lifecycle actions by status (pending → approve / deny in header)', async () => {
		renderDetail('sva_pending_1');
		await screen.findByRole('heading', { name: 'nightly-sync' });
		expect(screen.getByRole('button', { name: 'Approve nightly-sync' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Deny nightly-sync' })).toBeInTheDocument();
		// Destructive actions live in Settings' danger zone, not the header.
		expect(
			screen.queryByRole('button', { name: 'Disable nightly-sync' }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'Archive nightly-sync' }),
		).not.toBeInTheDocument();
	});

	it('approves a pending service account from the detail page', async () => {
		const user = userEvent.setup();
		renderDetail('sva_pending_1');
		await screen.findByRole('heading', { name: 'nightly-sync' });

		await user.click(screen.getByRole('button', { name: 'Approve nightly-sync' }));
		expect(await screen.findByText('Service account approved')).toBeInTheDocument();
	});

	it('shows the actor-scoped "Recent changes" audit slice on Overview', async () => {
		renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });
		// Same audit grammar as the agent + toolkit consoles.
		expect(await screen.findByText('Recent changes')).toBeInTheDocument();
		expect(await screen.findByText('create')).toBeInTheDocument();
		expect(screen.getByText('approve')).toBeInTheDocument();
	});

	// --- SA adopts the identity-console shell -------------------------------

	it('renders the KPI strip from the per-actor usage aggregate', async () => {
		renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });

		const strip = await screen.findByRole('group', { name: 'Key metrics' });
		// The SA usage fixture is non-zero; assert shape, not exact counts.
		await waitFor(() => {
			const [count] = within(strip).getAllByText(/^[\d,]+$/);
			expect(Number(count.textContent!.replace(/,/g, ''))).toBeGreaterThan(0);
		});
		// SAs have no toolkit bindings → no "Bound toolkits" KPI.
		expect(within(strip).queryByText('Bound toolkits')).not.toBeInTheDocument();
	});

	it('feeds per-SA executions on the Activity tab with a Monitor deep-link', async () => {
		const user = userEvent.setup();
		renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });

		await user.click(screen.getByRole('tab', { name: 'Activity' }));
		expect(await screen.findByText(/Recent executions/)).toBeInTheDocument();

		// Exactly two deep-links: the back-row link and the feed-card link.
		const links = await screen.findAllByRole('link', { name: /Open Monitor/ });
		expect(links).toHaveLength(2);
		for (const link of links) {
			expect(link.getAttribute('href')).toContain('actor_id=sva_active_1');
			expect(link.getAttribute('href')).toContain('actor_type=service_account');
		}
	});

	it('generates an API key from the Keys tab and shows it once', async () => {
		const user = userEvent.setup();
		renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });

		await user.click(screen.getByRole('tab', { name: 'Keys' }));
		// The backend gap (no key metadata/history for SAs) is documented inline.
		expect(
			await screen.findByText(/doesn’t expose key metadata or rotation history/),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole('button', { name: 'Generate API key for metrics-exporter' }),
		);
		// Because SA responses expose no key metadata, we can't know whether a
		// key already exists — generating always confirms first (a regenerate
		// silently invalidates the previous key).
		const confirm = await screen.findByRole('dialog');
		expect(
			within(confirm).getByText(/stops working the moment the new one is issued/),
		).toBeInTheDocument();
		await user.click(within(confirm).getByRole('button', { name: 'Generate' }));
		// The one-time reveal dialog opens (the key itself renders in an input).
		const dialog = await screen.findByRole('dialog', { name: 'API key generated' });
		expect(within(dialog).getByText('API key generated')).toBeInTheDocument();
	});

	it('hosts only the terminal Archive in the danger zone with the PATCH gap documented', async () => {
		const user = userEvent.setup();
		renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });

		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		// No metadata form: jentic-one has no PATCH /service-accounts.
		expect(await screen.findByText(/no PATCH \/service-accounts/)).toBeInTheDocument();
		expect(screen.getByText('Danger zone')).toBeInTheDocument();
		// The reversible Disable lives in the header kill switch, not here.
		expect(
			screen.queryByRole('button', { name: 'Disable metrics-exporter' }),
		).not.toBeInTheDocument();

		// Archive routes through the cascade-delete confirmation dialog.
		await user.click(screen.getByRole('button', { name: 'Archive metrics-exporter' }));
		const dialog = await screen.findByRole('dialog');
		expect(within(dialog).getByText(/metrics-exporter/)).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });
		await checkA11y(container);
	});
});
