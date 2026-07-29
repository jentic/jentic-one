import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from '@vitest/browser/context';
import { Routes, Route } from 'react-router';
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
 * The fleet-table `<tr>` containing the given actor name. Scopes to elements
 * inside a `tr` so it stays unambiguous when the same name also appears in
 * the approval-queue band or an open confirm dialog.
 */
function tableRowFor(name: string): HTMLElement {
	const inRow = screen.getAllByText(name).find((el) => el.closest('tr'));
	if (!inRow) throw new Error(`No fleet-table row found for "${name}"`);
	return inRow.closest('tr') as HTMLElement;
}

/** The "Awaiting approval" band (absent when nothing is pending). */
function approvalQueue(): HTMLElement {
	return screen.getByRole('region', { name: /Awaiting approval/i });
}

/** Opens the kebab row menu for an actor and clicks the given lifecycle verb. */
async function actFromKebab(user: ReturnType<typeof userEvent.setup>, name: string, verb: string) {
	await user.click(
		within(tableRowFor(name)).getByRole('button', { name: `Actions for ${name}` }),
	);
	await user.click(screen.getByRole('menuitem', { name: `${verb} ${name}` }));
}

describe('AgentsPage — agents lifecycle', () => {
	beforeEach(async () => {
		// The fleet table swaps to stacked cards below `sm` (640px); these specs
		// assert the desktop table grammar, so pin a desktop viewport.
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
	});

	it('lists every agent in the fleet table with status segments and counts', async () => {
		renderPage();
		// Pending names render in both the approval band and the table.
		await screen.findAllByText('inbox-triage-bot');
		// All five seeded agents are table rows regardless of status.
		for (const name of [
			'inbox-triage-bot',
			'release-notes-bot',
			'support-agent',
			'legacy-scraper',
			'spammy-bot',
		]) {
			expect(tableRowFor(name)).toBeInTheDocument();
		}
		// Segment labels carry live counts of the loaded fleet.
		expect(screen.getByRole('button', { name: 'All 5' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Pending 2' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Active 1' })).toBeInTheDocument();
	});

	it('surfaces pending agents in the approval queue band first', async () => {
		renderPage();
		await screen.findAllByText('inbox-triage-bot');
		const queue = approvalQueue();
		expect(within(queue).getByText('inbox-triage-bot')).toBeInTheDocument();
		expect(within(queue).getByText('release-notes-bot')).toBeInTheDocument();
		// Settled agents stay out of the band.
		expect(within(queue).queryByText('support-agent')).not.toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderPage();
		await screen.findAllByText('inbox-triage-bot');
		await checkA11y(container);
	});

	it('approves a pending agent from the queue → status flips to active', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		await user.click(
			within(approvalQueue()).getByRole('button', { name: 'Approve inbox-triage-bot' }),
		);

		await waitFor(() => {
			expect(within(tableRowFor('inbox-triage-bot')).getByText('Active')).toBeInTheDocument();
		});
		expect(await screen.findByText('Agent approved')).toBeInTheDocument();
		// The queue now only holds the other pending agent.
		expect(within(approvalQueue()).queryByText('inbox-triage-bot')).not.toBeInTheDocument();
	});

	it('denies a pending agent → requires a reason → status flips to rejected', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('release-notes-bot');

		await user.click(
			within(approvalQueue()).getByRole('button', { name: 'Deny release-notes-bot' }),
		);

		const dialog = await screen.findByRole('dialog');
		// Empty reason is blocked client-side.
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));
		expect(await within(dialog).findByText('A reason is required.')).toBeInTheDocument();

		await user.type(within(dialog).getByLabelText('Reason'), 'spam');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() => {
			expect(
				within(tableRowFor('release-notes-bot')).getByText('Rejected'),
			).toBeInTheDocument();
		});
	});

	it('disables an active agent from the kebab menu then re-enables it', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('support-agent');

		await actFromKebab(user, 'support-agent', 'Disable');
		const dialog = await screen.findByRole('dialog');
		await user.click(within(dialog).getByRole('button', { name: 'Disable' }));

		await waitFor(() => {
			expect(within(tableRowFor('support-agent')).getByText('Disabled')).toBeInTheDocument();
		});

		await actFromKebab(user, 'support-agent', 'Enable');
		await waitFor(() => {
			expect(within(tableRowFor('support-agent')).getByText('Active')).toBeInTheDocument();
		});
	});

	it('filters the fleet by name and hides the approval band while hunting', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('support-agent');

		await user.type(screen.getByLabelText('Filter agents'), 'support');

		expect(tableRowFor('support-agent')).toBeInTheDocument();
		expect(screen.queryByText('legacy-scraper')).not.toBeInTheDocument();
		// A name filter means the operator is hunting, not triaging.
		expect(
			screen.queryByRole('region', { name: /Awaiting approval/i }),
		).not.toBeInTheDocument();
	});

	it('narrows the fleet with the status segments', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('spammy-bot');

		await user.click(screen.getByRole('button', { name: 'Rejected 1' }));

		expect(tableRowFor('spammy-bot')).toBeInTheDocument();
		expect(screen.queryByText('support-agent')).not.toBeInTheDocument();
		// Off the "All" segment the queue band folds into the table itself.
		expect(
			screen.queryByRole('region', { name: /Awaiting approval/i }),
		).not.toBeInTheDocument();
	});

	it('navigates to the agent detail page from the name link', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Routes>
				<Route path="/" element={<AgentsPage />} />
				<Route path="/agents/:agentId" element={<div>detail-page-marker</div>} />
			</Routes>,
		);
		await screen.findByText('support-agent');

		await user.click(
			within(tableRowFor('support-agent')).getByRole('link', { name: 'support-agent' }),
		);

		expect(await screen.findByText('detail-page-marker')).toBeInTheDocument();
	});

	it('archives an active agent via the kebab → type-to-confirm → status flips', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('support-agent');

		await actFromKebab(user, 'support-agent', 'Archive');
		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText(/to confirm/i), 'archive');
		await user.click(within(dialog).getByRole('button', { name: 'Archive agent' }));

		await waitFor(() => {
			expect(within(tableRowFor('support-agent')).getByText('Archived')).toBeInTheDocument();
		});
	});

	it('keeps the dialog open and toasts when a lifecycle mutation fails', async () => {
		const user = userEvent.setup();
		worker.use(createErrorHandler('delete', '/agents/:id', { status: 500 }));
		renderPage();
		await screen.findByText('support-agent');

		await actFromKebab(user, 'support-agent', 'Archive');
		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText(/to confirm/i), 'archive');
		await user.click(within(dialog).getByRole('button', { name: 'Archive agent' }));

		// Failure → error toast, dialog stays open so the user can retry, and the
		// agent remains active (not optimistically archived).
		expect(await screen.findByText('Failed to archive the agent.')).toBeInTheDocument();
		expect(screen.getByRole('dialog')).toBeInTheDocument();
		expect(within(tableRowFor('support-agent')).getByText('Active')).toBeInTheDocument();
	});

	it('pages through the cursor list with Load more', async () => {
		const user = userEvent.setup();
		const page = (id: string, name: string) => ({
			id,
			name,
			description: null,
			status: 'active',
			owner_id: null,
			registered_by: 'self',
			parent_agent_id: null,
			approved_by: null,
			denial_reason: null,
			denied_by: null,
			created_at: new Date().toISOString(),
			approved_at: new Date().toISOString(),
			has_api_key: false,
		});
		worker.use(
			http.get('*/agents', ({ request }) => {
				const cursor = new URL(request.url).searchParams.get('cursor');
				if (cursor === 'cursor-2') {
					return HttpResponse.json({
						data: [page('agnt_p2', 'second-page-bot')],
						has_more: false,
						next_cursor: null,
					});
				}
				return HttpResponse.json({
					data: [page('agnt_p1', 'first-page-bot')],
					has_more: true,
					next_cursor: 'cursor-2',
				});
			}),
		);
		renderPage();

		await screen.findByText('first-page-bot');
		expect(screen.queryByText('second-page-bot')).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Load more' }));

		expect(await screen.findByText('second-page-bot')).toBeInTheDocument();
		// Both pages stay mounted and the pager disappears at the end.
		expect(screen.getByText('first-page-bot')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
	});

	it('surfaces an error when the list fails', async () => {
		worker.use(createErrorHandler('get', '/agents', { status: 500 }));
		renderPage();
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});

	it('enriches rows with 7-day activity columns from the usage aggregate', async () => {
		renderPage();
		await screen.findByText('support-agent');

		// The usage query resolves after the roster; wait for the columns.
		expect(await screen.findByText('Activity (7d)')).toBeInTheDocument();
		// The mock windows totals by `since`, so assert shape not exact counts:
		// a busy agent gets a nonzero execution count and a success share.
		const active = tableRowFor('support-agent');
		await waitFor(() => {
			const [count] = within(active).getAllByText(/^[\d,]+$/);
			expect(Number(count.textContent!.replace(/,/g, ''))).toBeGreaterThan(0);
		});
		expect(within(active).getByText(/^\d+(\.\d+)?%$/)).toBeInTheDocument();

		// Actors without usage rows read as genuinely idle, not broken.
		const pending = tableRowFor('inbox-triage-bot');
		expect(within(pending).getByText('idle')).toBeInTheDocument();
		expect(within(pending).getByText('—')).toBeInTheDocument();
	});

	it('renders the plain roster when the usage aggregate is admin-gated (403)', async () => {
		worker.use(createErrorHandler('get', '/monitoring/usage', { status: 403 }));
		renderPage();
		await screen.findByText('support-agent');

		// No activity enrichment for non-admins — and no error either; the
		// lifecycle columns take the space back.
		expect(screen.queryByText('Activity (7d)')).not.toBeInTheDocument();
		expect(screen.getByText('Approved')).toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});
});
