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

	it('renames the agent through the Edit dialog (#620)', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('button', { name: 'Rename agent' }));
		const dialog = await screen.findByRole('dialog', { name: 'Edit agent' });
		const nameInput = within(dialog).getByLabelText('Name');
		await user.clear(nameInput);
		await user.type(nameInput, 'Claude Code');
		await user.click(within(dialog).getByRole('button', { name: 'Save changes' }));

		// Dialog closes on success and the new name propagates to the PageHeader.
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		expect(await screen.findByRole('heading', { name: 'Claude Code' })).toBeInTheDocument();
	});

	it('blocks an empty name in the Edit dialog with a validation error (#620)', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('button', { name: 'Rename agent' }));
		const dialog = await screen.findByRole('dialog', { name: 'Edit agent' });
		await user.clear(within(dialog).getByLabelText('Name'));

		expect(within(dialog).getByRole('button', { name: 'Save changes' })).toBeDisabled();
		expect(await within(dialog).findByText("Name can't be empty.")).toBeInTheDocument();
	});

	it('falls back to orientation copy when the agent has no description (#5)', async () => {
		// The seeded agents (e.g. agnt_active_1) carry description === null, so the
		// header must never be left without context.
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });
		expect(
			await screen.findByText(
				'Identity, attribution, bound toolkits, and lifecycle for this agent.',
			),
		).toBeInTheDocument();
	});

	it('falls back to orientation copy for an empty-string description (#5)', async () => {
		// `??` treated '' as present, blanking the subtitle. A whitespace-only
		// description must be treated as absent so the orientation copy still shows.
		const { worker } = await import('@/mocks/browser');
		const { http, HttpResponse } = await import('msw');
		worker.use(
			http.get('/agents/:id', ({ params }) =>
				HttpResponse.json({
					id: params.id,
					name: 'support-agent',
					description: '',
					owner_id: null,
					registered_by: 'self',
					parent_agent_id: null,
					approved_by: null,
					status: 'active',
					denial_reason: null,
					denied_by: null,
					created_at: new Date().toISOString(),
					approved_at: null,
					has_api_key: false,
				}),
			),
		);

		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });
		expect(
			await screen.findByText(
				'Identity, attribution, bound toolkits, and lifecycle for this agent.',
			),
		).toBeInTheDocument();
	});

	it('disables Cancel while a rename PATCH is in flight so a reopen race cannot close a fresh dialog (#7)', async () => {
		const user = userEvent.setup();
		const { worker } = await import('@/mocks/browser');
		const { http, HttpResponse, delay } = await import('msw');
		worker.use(
			http.patch('/agents/:id', async ({ request, params }) => {
				const body = (await request.json()) as { name?: string };
				// Hold the response open so we can observe the pending UI state.
				await delay(150);
				return HttpResponse.json({
					id: params.id,
					name: body?.name ?? 'support-agent',
					description: null,
					owner_id: null,
					registered_by: 'self',
					parent_agent_id: null,
					approved_by: null,
					status: 'active',
					denial_reason: null,
					denied_by: null,
					created_at: new Date().toISOString(),
					approved_at: null,
					has_api_key: false,
				});
			}),
		);

		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('button', { name: 'Rename agent' }));
		const dialog = await screen.findByRole('dialog', { name: 'Edit agent' });
		const nameInput = within(dialog).getByLabelText('Name');
		await user.clear(nameInput);
		await user.type(nameInput, 'Renamed');
		await user.click(within(dialog).getByRole('button', { name: 'Save changes' }));

		// While the PATCH is in flight, Cancel must be disabled so the user can't
		// close-and-reopen and have the stale onSuccess slam the fresh dialog shut.
		await waitFor(() =>
			expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled(),
		);

		// After it resolves, the dialog closes and the new name lands.
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		expect(await screen.findByRole('heading', { name: 'Renamed' })).toBeInTheDocument();
	});

	it('omits an unchanged description from a rename-only Save (#9)', async () => {
		const user = userEvent.setup();
		const { worker } = await import('@/mocks/browser');
		const { http, HttpResponse } = await import('msw');

		// Give the agent a description with trailing whitespace so a naive
		// trim-on-save would silently rewrite it.
		let patchBody: { name?: string; description?: string | null } | null = null;
		worker.use(
			http.get('/agents/:id', ({ params }) =>
				HttpResponse.json({
					id: params.id,
					name: 'support-agent',
					description: 'keep me ',
					owner_id: null,
					registered_by: 'self',
					parent_agent_id: null,
					approved_by: null,
					status: 'active',
					denial_reason: null,
					denied_by: null,
					created_at: new Date().toISOString(),
					approved_at: null,
					has_api_key: false,
				}),
			),
			http.patch('/agents/:id', async ({ request, params }) => {
				patchBody = (await request.json()) as typeof patchBody;
				return HttpResponse.json({
					id: params.id,
					name: patchBody?.name ?? 'support-agent',
					description: 'keep me ',
					owner_id: null,
					registered_by: 'self',
					parent_agent_id: null,
					approved_by: null,
					status: 'active',
					denial_reason: null,
					denied_by: null,
					created_at: new Date().toISOString(),
					approved_at: null,
					has_api_key: false,
				});
			}),
		);

		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('button', { name: 'Rename agent' }));
		const dialog = await screen.findByRole('dialog', { name: 'Edit agent' });
		const nameInput = within(dialog).getByLabelText('Name');
		await user.clear(nameInput);
		await user.type(nameInput, 'renamed-agent');
		await user.click(within(dialog).getByRole('button', { name: 'Save changes' }));

		await waitFor(() => expect(patchBody).not.toBeNull());
		expect(patchBody).toEqual({ name: 'renamed-agent' });
		expect(patchBody).not.toHaveProperty('description');
	});

	it('renders no empty action-row container for an archived agent (#11)', async () => {
		renderDetail('agnt_archived_1');
		await screen.findByRole('heading', { name: 'retired-agent' });

		// Archived agents have no lifecycle actions and are not active, so none of
		// the action-cluster buttons should render.
		expect(screen.queryByRole('button', { name: /Generate API key/i })).not.toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: /Regenerate API key/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: /Archive retired-agent/ }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: /Enable retired-agent/ }),
		).not.toBeInTheDocument();
	});

	it('surfaces a server error in the Edit dialog and keeps it open (#620)', async () => {
		const user = userEvent.setup();
		const { worker } = await import('@/mocks/browser');
		const { http, HttpResponse } = await import('msw');
		worker.use(
			http.patch('/agents/:id', () =>
				HttpResponse.json({ detail: 'Name already in use.' }, { status: 409 }),
			),
		);

		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('button', { name: 'Rename agent' }));
		const dialog = await screen.findByRole('dialog', { name: 'Edit agent' });
		const nameInput = within(dialog).getByLabelText('Name');
		await user.clear(nameInput);
		await user.type(nameInput, 'Taken name');
		await user.click(within(dialog).getByRole('button', { name: 'Save changes' }));

		expect(await within(dialog).findByText('Name already in use.')).toBeInTheDocument();
		expect(screen.getByRole('dialog', { name: 'Edit agent' })).toBeInTheDocument();
	});
});
