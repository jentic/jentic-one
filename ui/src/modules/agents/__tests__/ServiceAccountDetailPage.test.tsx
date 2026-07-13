import { describe, it, expect, beforeEach } from 'vitest';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders, screen, within, userEvent, checkA11y } from '@/__tests__/test-utils';
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

	it('renders identity, status, and attribution', async () => {
		renderDetail('sva_active_1');
		expect(
			await screen.findByRole('heading', { name: 'metrics-exporter' }),
		).toBeInTheDocument();
		expect(screen.getByText('sva_active_1')).toBeInTheDocument();
		expect(screen.getAllByText('Active').length).toBeGreaterThanOrEqual(1);
	});

	it('renders a Scopes card with the granted scopes', async () => {
		renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });
		const list = await screen.findByRole('list', { name: 'Granted scopes' });
		expect(within(list).getByText('credentials:read')).toBeInTheDocument();
	});

	it('shows an empty pending-access-requests card when none are filed (#619)', async () => {
		renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });
		expect(await screen.findByRole('heading', { name: 'Access requests' })).toBeInTheDocument();
		expect(await screen.findByText('No pending access requests')).toBeInTheDocument();
	});

	it('renders a not-found surface for an unknown id', async () => {
		renderDetail('sva_does_not_exist');
		expect(await screen.findByText('Service account not found')).toBeInTheDocument();
	});

	it('gates lifecycle actions by status (pending → approve / deny / archive)', async () => {
		renderDetail('sva_pending_1');
		await screen.findByRole('heading', { name: 'nightly-sync' });
		expect(screen.getByRole('button', { name: 'Approve nightly-sync' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Deny nightly-sync' })).toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'Disable nightly-sync' }),
		).not.toBeInTheDocument();
	});

	it('approves a pending service account from the detail page', async () => {
		const user = userEvent.setup();
		renderDetail('sva_pending_1');
		await screen.findByRole('heading', { name: 'nightly-sync' });

		await user.click(screen.getByRole('button', { name: 'Approve nightly-sync' }));
		expect(await screen.findByText('Service account approved')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderDetail('sva_active_1');
		await screen.findByRole('heading', { name: 'metrics-exporter' });
		await checkA11y(container);
	});
});
