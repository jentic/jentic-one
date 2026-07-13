import { describe, it, expect, beforeEach } from 'vitest';
import { Routes, Route } from 'react-router-dom';
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
import AgentDetailPage from '@/modules/agents/pages/AgentDetailPage';

function renderDetail(agentId: string) {
	return renderWithProviders(
		<>
			<Routes>
				<Route path="/agents/:agentId" element={<AgentDetailPage />} />
				<Route path="/agents" element={<div>agents-list-marker</div>} />
			</Routes>
			<Toaster />
		</>,
		{ route: `/agents/${agentId}` },
	);
}

describe('AgentDetailPage', () => {
	beforeEach(() => {
		setToken('test-token');
		resetAgentsStore();
	});

	it('renders identity, status, and attribution for an agent', async () => {
		renderDetail('agnt_active_1');
		expect(await screen.findByRole('heading', { name: 'support-agent' })).toBeInTheDocument();
		expect(screen.getByText('agnt_active_1')).toBeInTheDocument();
		expect(screen.getAllByText('Active').length).toBeGreaterThanOrEqual(1);
		expect(screen.getByText('Registered')).toBeInTheDocument();
	});

	it('lists bound toolkits for the agent', async () => {
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });
		expect(await screen.findByText('github')).toBeInTheDocument();
	});

	it('shows the pending access requests this agent has filed (#619)', async () => {
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });
		expect(await screen.findByRole('heading', { name: 'Access requests' })).toBeInTheDocument();
		expect(await screen.findByText(/toolkit · use \+2 more/)).toBeInTheDocument();
	});

	it('shows an honest empty state when no toolkits are bound', async () => {
		renderDetail('agnt_pending_1');
		await screen.findByRole('heading', { name: 'inbox-triage-bot' });
		expect(await screen.findByText('No toolkits bound to this agent.')).toBeInTheDocument();
	});

	it('renders a not-found surface for an unknown id', async () => {
		renderDetail('agnt_does_not_exist');
		expect(await screen.findByText('Agent not found')).toBeInTheDocument();
	});

	it('gates lifecycle actions by status (pending → approve / deny / archive)', async () => {
		renderDetail('agnt_pending_1');
		await screen.findByRole('heading', { name: 'inbox-triage-bot' });
		expect(
			screen.getByRole('button', { name: 'Approve inbox-triage-bot' }),
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Deny inbox-triage-bot' })).toBeInTheDocument();
		// Pending actors can be archived (cleanup) but not disabled (not active).
		expect(
			screen.getByRole('button', { name: 'Archive inbox-triage-bot' }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'Disable inbox-triage-bot' }),
		).not.toBeInTheDocument();
	});

	it('keeps the deny dialog open and toasts when the deny fails', async () => {
		const user = userEvent.setup();
		const { worker } = await import('@/mocks/browser');
		const { createErrorHandler } = await import('@/__tests__/test-utils');
		worker.use(createErrorHandler('post', '/agents/:id\\:deny', { status: 500 }));

		renderDetail('agnt_pending_1');
		await screen.findByRole('heading', { name: 'inbox-triage-bot' });

		await user.click(screen.getByRole('button', { name: 'Deny inbox-triage-bot' }));
		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText('Reason'), 'nope');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		expect(await screen.findByText('Failed to deny the agent.')).toBeInTheDocument();
		expect(screen.getByRole('dialog')).toBeInTheDocument();
	});

	it('approves a pending agent from the detail page', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_pending_1');
		await screen.findByRole('heading', { name: 'inbox-triage-bot' });

		await user.click(screen.getByRole('button', { name: 'Approve inbox-triage-bot' }));

		expect(await screen.findByText('Agent approved')).toBeInTheDocument();
		await waitFor(() => {
			expect(screen.getAllByText('Active').length).toBeGreaterThanOrEqual(1);
		});
	});

	it('requires a reason to deny from the detail page', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_pending_1');
		await screen.findByRole('heading', { name: 'inbox-triage-bot' });

		await user.click(screen.getByRole('button', { name: 'Deny inbox-triage-bot' }));
		const dialog = await screen.findByRole('dialog');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));
		expect(await within(dialog).findByText('A reason is required.')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });
		await checkA11y(container);
	});
});
