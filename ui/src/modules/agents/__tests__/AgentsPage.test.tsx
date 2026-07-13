import { describe, it, expect, beforeEach } from 'vitest';
import { Routes, Route } from 'react-router-dom';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

function renderPage() {
	return renderWithProviders(
		<>
			<AgentsPage />
			<Toaster />
		</>,
	);
}

/**
 * The roster row wrapper that contains the given agent name. Scopes to the
 * row heading (`<h3>`) so it stays unambiguous even when a confirm dialog is
 * open and echoes the same name in its body / type-to-confirm prompt.
 */
function rowFor(name: string): HTMLElement {
	const heading = screen.getAllByText(name).find((el) => el.tagName === 'H3');
	if (!heading) throw new Error(`No roster row heading found for "${name}"`);
	return heading.closest('div.group') as HTMLElement;
}

describe('AgentsPage — agents lifecycle', () => {
	beforeEach(() => {
		setToken('test-token');
		resetAgentsStore();
	});

	it('lists agents grouped by lifecycle status', async () => {
		renderPage();
		expect(await screen.findByText('inbox-triage-bot')).toBeInTheDocument();
		expect(screen.getByText('support-agent')).toBeInTheDocument();
		// Awaiting-approval section surfaces pending agents first.
		expect(screen.getByRole('heading', { name: /Awaiting approval/i })).toBeInTheDocument();
		expect(screen.getAllByText('Pending').length).toBeGreaterThanOrEqual(1);
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderPage();
		await screen.findByText('inbox-triage-bot');
		await checkA11y(container);
	});

	it('approves a pending agent → status flips to active', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('inbox-triage-bot');

		await user.click(
			within(rowFor('inbox-triage-bot')).getByRole('button', {
				name: 'Approve inbox-triage-bot',
			}),
		);

		await waitFor(() => {
			expect(within(rowFor('inbox-triage-bot')).getByText('Active')).toBeInTheDocument();
		});
		expect(await screen.findByText('Agent approved')).toBeInTheDocument();
	});

	it('denies a pending agent → requires a reason → status flips to rejected', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('release-notes-bot');

		await user.click(
			within(rowFor('release-notes-bot')).getByRole('button', {
				name: 'Deny release-notes-bot',
			}),
		);

		const dialog = await screen.findByRole('dialog');
		// Empty reason is blocked client-side.
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));
		expect(await within(dialog).findByText('A reason is required.')).toBeInTheDocument();

		await user.type(within(dialog).getByLabelText('Reason'), 'spam');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() => {
			// Rejected agents drop out of the awaiting-approval section and gain a
			// Rejected status pill (kept in the collapsed "Declined" section).
			expect(within(rowFor('release-notes-bot')).getByText('Rejected')).toBeInTheDocument();
		});
	});

	it('disables an active agent then re-enables it', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('support-agent');

		await user.click(
			within(rowFor('support-agent')).getByRole('button', {
				name: 'Disable support-agent',
			}),
		);

		const dialog = await screen.findByRole('dialog');
		await user.click(within(dialog).getByRole('button', { name: 'Disable' }));

		await waitFor(() => {
			expect(
				within(rowFor('support-agent')).getAllByText('Disabled').length,
			).toBeGreaterThanOrEqual(1);
		});

		await user.click(
			within(rowFor('support-agent')).getByRole('button', {
				name: 'Enable support-agent',
			}),
		);
		await waitFor(() => {
			expect(within(rowFor('support-agent')).getByText('Active')).toBeInTheDocument();
		});
	});

	it('navigates to the agent detail page from a row', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Routes>
				<Route path="/" element={<AgentsPage />} />
				<Route path="/agents/:agentId" element={<div>detail-page-marker</div>} />
			</Routes>,
		);
		await screen.findByText('support-agent');

		await user.click(screen.getByText('support-agent'));

		expect(await screen.findByText('detail-page-marker')).toBeInTheDocument();
	});

	it('archives an active agent → it leaves the active section into Removed', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('support-agent');

		await user.click(
			within(rowFor('support-agent')).getByRole('button', {
				name: 'Archive support-agent',
			}),
		);

		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText(/to confirm/i), 'support-agent');
		await user.click(within(dialog).getByRole('button', { name: 'Archive agent' }));

		// The collapsible "Removed agents" disclosure appears with the row in it.
		expect(await screen.findByText('Removed agents')).toBeInTheDocument();
		await waitFor(() => {
			expect(within(rowFor('support-agent')).getByText('Archived')).toBeInTheDocument();
		});
	});

	it('keeps the dialog open and toasts when a lifecycle mutation fails', async () => {
		const user = userEvent.setup();
		worker.use(createErrorHandler('delete', '/agents/:id', { status: 500 }));
		renderPage();
		await screen.findByText('support-agent');

		await user.click(
			within(rowFor('support-agent')).getByRole('button', {
				name: 'Archive support-agent',
			}),
		);
		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText(/to confirm/i), 'support-agent');
		await user.click(within(dialog).getByRole('button', { name: 'Archive agent' }));

		// Failure → error toast, dialog stays open so the user can retry, and the
		// agent remains active (not optimistically archived).
		expect(await screen.findByText('Failed to archive the agent.')).toBeInTheDocument();
		expect(screen.getByRole('dialog')).toBeInTheDocument();
		expect(within(rowFor('support-agent')).getByText('Active')).toBeInTheDocument();
	});

	it('renders declined and removed agents in collapsed sections', async () => {
		renderPage();
		await screen.findByText('inbox-triage-bot');
		// Seeded rejected agent lives under the "Declined registrations" disclosure.
		expect(screen.getByText('Declined registrations')).toBeInTheDocument();
		expect(within(rowFor('spammy-bot')).getByText('Rejected')).toBeInTheDocument();
	});

	it('surfaces an error when the list fails', async () => {
		worker.use(createErrorHandler('get', '/agents', { status: 500 }));
		renderPage();
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});
});
