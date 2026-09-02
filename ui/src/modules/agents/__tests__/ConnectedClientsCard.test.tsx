import { describe, it, expect, beforeEach } from 'vitest';
import { worker } from '@/mocks/browser';
import {
	renderWithProviders,
	screen,
	within,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { ConnectedClientsCard } from '@/modules/agents/components/detail/ConnectedClientsCard';

function renderCard(props: { agentId: string; agentName: string }) {
	return renderWithProviders(
		<>
			<ConnectedClientsCard {...props} />
			<Toaster />
		</>,
	);
}

describe('ConnectedClientsCard', () => {
	beforeEach(() => {
		setToken('test-token');
		resetAgentsStore();
	});

	it('lists active grants with client, scopes, consenting user, and timestamps', async () => {
		// `ocg_active_1` (Cursor → agnt_active_1) is seeded active.
		renderCard({ agentId: 'agnt_active_1', agentName: 'support-agent' });

		expect(
			await screen.findByRole('heading', { name: 'Connected clients' }),
		).toBeInTheDocument();
		expect(await screen.findByText('Cursor')).toBeInTheDocument();
		// Redirect-URI origin (the "authorized apps" display pattern).
		expect(screen.getByText('http://localhost:33418')).toBeInTheDocument();
		// Granted scopes render as chips.
		expect(screen.getByText('apis:read')).toBeInTheDocument();
		expect(screen.getByText('capabilities:execute')).toBeInTheDocument();
		// G10: WHO consented is shown (usr_admin_1 resolves via the actor directory).
		expect(await screen.findByText('Admin User')).toBeInTheDocument();
		expect(screen.getByText(/last used/)).toBeInTheDocument();
		// The revoked history row is not in the default (active) view.
		expect(screen.queryByText('Old Integration')).not.toBeInTheDocument();
	});

	it('reveals revoked history via the status filter, with a status badge', async () => {
		const user = userEvent.setup();
		renderCard({ agentId: 'agnt_active_1', agentName: 'support-agent' });
		await screen.findByText('Cursor');

		await user.click(screen.getByRole('button', { name: 'Revoked' }));
		const row = (await screen.findByText('Old Integration')).closest('li');
		expect(row).not.toBeNull();
		// The status badge on the row (distinct from the "Revoked" filter button).
		expect(within(row as HTMLElement).getByText('Revoked')).toBeInTheDocument();
		// G10 made visible: the revoked grant belonged to a since-departed owner,
		// whose raw id renders because the directory cannot resolve it.
		expect(screen.getByText('usr_departed_owner')).toBeInTheDocument();
		// Revoked rows offer no revoke button.
		expect(screen.queryByRole('button', { name: /Revoke grant/ })).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'All' }));
		expect(await screen.findByText('Cursor')).toBeInTheDocument();
		expect(screen.getByText('Old Integration')).toBeInTheDocument();
	});

	it('revokes a grant through the confirm dialog and drops the row', async () => {
		const user = userEvent.setup();
		renderCard({ agentId: 'agnt_active_1', agentName: 'support-agent' });
		await screen.findByText('Cursor');

		await user.click(screen.getByRole('button', { name: 'Revoke grant for Cursor' }));
		const dialog = await screen.findByRole('dialog');
		expect(within(dialog).getByText(/every token issued under this grant/)).toBeInTheDocument();
		await user.click(within(dialog).getByRole('button', { name: 'Revoke' }));

		// The grant leaves the active slice → empty state takes over.
		expect(await screen.findByText('No connected clients')).toBeInTheDocument();
		expect(await screen.findByText('Grant revoked')).toBeInTheDocument();
	});

	it('shows an honest empty state when no client is connected', async () => {
		// `agnt_disabled_1` has no seeded grants.
		renderCard({ agentId: 'agnt_disabled_1', agentName: 'legacy-scraper' });
		expect(await screen.findByText('No connected clients')).toBeInTheDocument();
		expect(
			screen.getByText(/No OAuth client currently holds a grant on legacy-scraper/),
		).toBeInTheDocument();
	});

	it('renders a quiet owner-or-admin note on 403 instead of a hard error', async () => {
		worker.use(
			createErrorHandler('get', '/agents/:id/oauth-grants', {
				status: 403,
				body: { detail: 'Not permitted' },
			}),
		);
		renderCard({ agentId: 'agnt_active_1', agentName: 'support-agent' });
		expect(
			await screen.findByText(
				"Only the agent's owner or an admin can view its connected clients.",
			),
		).toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	it('surfaces an error alert when the list fails to load', async () => {
		worker.use(createErrorHandler('get', '/agents/:id/oauth-grants', { status: 500 }));
		renderCard({ agentId: 'agnt_active_1', agentName: 'support-agent' });
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderCard({
			agentId: 'agnt_active_1',
			agentName: 'support-agent',
		});
		await screen.findByText('Cursor');
		await checkA11y(container);
	});
});
