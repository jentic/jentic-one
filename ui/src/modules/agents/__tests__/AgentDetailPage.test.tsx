import { describe, it, expect, beforeEach } from 'vitest';
import { Routes, Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
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

type SeedToolkit = { toolkit_id: string; name: string; active?: boolean };

/**
 * Per-test, isolated toolkit-binding fixtures for the agent-side bind/unbind
 * flow (#607). The default module handlers return a hardcoded bound list for
 * `agnt_active_1` that ignores mutations, so we override them here with a fresh
 * in-closure store that POST/DELETE mutate — keeping each test independent of
 * execution order (mirrors ToolkitDetailPage.test.tsx's `seedAgents`).
 *
 * `bound` seeds `GET /agents/:id/toolkits`; `workspace` seeds the picker
 * candidates (`GET /toolkits`). Returns the mutable `bound` array so tests can
 * assert the store changed after a mutation fires.
 */
function seedToolkitBindings(opts: {
	agentId: string;
	bound: SeedToolkit[];
	workspace: SeedToolkit[];
}) {
	const bound = opts.bound.map((t, i) => ({
		id: `tkb_${i}`,
		agent_id: opts.agentId,
		toolkit_id: t.toolkit_id,
		bound_at: '2026-05-02T09:00:00Z',
	}));
	worker.use(
		http.get('/agents/:id/toolkits', ({ params }) => {
			if (params.id !== opts.agentId) {
				return HttpResponse.json({ data: [], has_more: false, next_cursor: null });
			}
			return HttpResponse.json({ data: bound, has_more: false, next_cursor: null });
		}),
		// Per-id name resolution for each bound row (#607): the "Bound toolkits"
		// card reads `GET /toolkits/{id}` for just its own name rather than the
		// whole `GET /toolkits` catalogue. Backed by the same `workspace` seed so
		// a bound toolkit resolves to its human name; unknown ids 404 → the row
		// falls back to the id.
		http.get('/toolkits/:toolkitId', ({ params }) => {
			const t = opts.workspace.find((w) => w.toolkit_id === params.toolkitId);
			if (!t) return new HttpResponse(null, { status: 404 });
			return HttpResponse.json({
				toolkit_id: t.toolkit_id,
				name: t.name,
				description: null,
				active: t.active ?? true,
				key_count: 0,
				credential_count: 0,
				permissions: [],
				created_at: '2026-04-01T09:00:00Z',
				updated_at: null,
			});
		}),
		http.get('/toolkits', () =>
			HttpResponse.json({
				data: opts.workspace.map((t) => ({
					toolkit_id: t.toolkit_id,
					name: t.name,
					description: null,
					active: t.active ?? true,
					key_count: 0,
					credential_count: 0,
					created_at: '2026-04-01T09:00:00Z',
					updated_at: null,
				})),
				has_more: false,
				next_cursor: null,
			}),
		),
		http.post('/agents/:agentId/toolkits', async ({ params, request }) => {
			const agentId = params.agentId as string;
			const body = (await request.json()) as { toolkit_id: string };
			if (agentId === opts.agentId && !bound.some((b) => b.toolkit_id === body.toolkit_id)) {
				bound.push({
					id: `tkb_${bound.length}`,
					agent_id: agentId,
					toolkit_id: body.toolkit_id,
					bound_at: new Date().toISOString(),
				});
			}
			return HttpResponse.json({
				agent_id: agentId,
				toolkit_id: body.toolkit_id,
				bound_at: new Date().toISOString(),
			});
		}),
		http.delete('/agents/:agentId/toolkits/:toolkitId', ({ params }) => {
			const idx = bound.findIndex((b) => b.toolkit_id === params.toolkitId);
			if (idx >= 0) bound.splice(idx, 1);
			return new HttpResponse(null, { status: 204 });
		}),
	);
	return bound;
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
		// The bound toolkit id is `github`; the per-id name read 404s (no such
		// toolkit in the workspace fixtures), so the row falls back to the id as
		// the primary label and hides the redundant secondary id line (#4) —
		// leaving the id rendered EXACTLY once in the row. Asserting `>= 1` would
		// pass even on the regression where the id renders twice, so we pin it.
		const row = await screen.findByTestId('bound-toolkit-row');
		await waitFor(() => expect(within(row).getAllByText('github')).toHaveLength(1));
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
		expect(await screen.findByText(/No toolkits bound to this agent\./)).toBeInTheDocument();
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

	// --- #607: agent-side bind / unbind toolkit ---------------------------

	it('binds a toolkit picked from the picker and updates the bound list', async () => {
		const bound = seedToolkitBindings({
			agentId: 'agnt_active_1',
			bound: [],
			workspace: [
				{ toolkit_id: 'tk_github', name: 'GitHub Tools' },
				{ toolkit_id: 'tk_stripe', name: 'Stripe Tools' },
			],
		});
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('button', { name: 'Bind toolkit' }));

		// Picker lists the workspace toolkits inside the dialog.
		const dialog = await screen.findByRole('dialog', { name: /bind toolkit/i });
		const stripeRow = await within(dialog).findByText('Stripe Tools');
		await user.click(stripeRow);

		// POST fired → the mock store carries the new binding, dialog closes, and
		// the bound list refreshes with the newly bound toolkit, showing its human
		// NAME as the primary label (resolved via the per-id read) and the id below.
		await waitFor(() => expect(bound.some((b) => b.toolkit_id === 'tk_stripe')).toBe(true));
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		const row = await screen.findByTestId('bound-toolkit-row');
		expect(await within(row).findByText('Stripe Tools')).toBeInTheDocument();
		expect(within(row).getByText('tk_stripe')).toBeInTheDocument();
	});

	it('shows the toolkit name and truncates a long name with a title tooltip', async () => {
		const longName =
			'Extremely Long Toolkit Display Name That Should Be Truncated In The Bound Toolkits Card';
		seedToolkitBindings({
			agentId: 'agnt_active_1',
			bound: [{ toolkit_id: 'tk_long', name: longName }],
			workspace: [{ toolkit_id: 'tk_long', name: longName }],
		});
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		const row = await screen.findByTestId('bound-toolkit-row');
		// The NAME is the prominent label and carries a `title` so the full name is
		// revealed on hover even when visually truncated. The id stays as a
		// secondary line, and the unbind control is labelled by the name.
		const nameEl = await within(row).findByText(longName);
		expect(nameEl).toHaveAttribute('title', longName);
		expect(within(row).getByText('tk_long')).toBeInTheDocument();
		expect(
			within(row).getByRole('button', { name: `Unbind toolkit ${longName}` }),
		).toBeInTheDocument();
	});

	it('hides the secondary id line when the name is unresolved (falls back to id) (#4)', async () => {
		// `tk_ghost` is bound but not present in the workspace, so the per-id name
		// read 404s → the row shows the id as the primary label. The redundant
		// secondary `<code>` id line must be hidden so the id isn't rendered twice.
		seedToolkitBindings({
			agentId: 'agnt_active_1',
			bound: [{ toolkit_id: 'tk_ghost', name: 'ignored' }],
			workspace: [],
		});
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		const row = await screen.findByTestId('bound-toolkit-row');
		// The id shows exactly once (as the primary fallback label), never twice.
		await waitFor(() => expect(within(row).getAllByText('tk_ghost')).toHaveLength(1));
	});

	it('unbinds a bound toolkit through the inline row confirm', async () => {
		const bound = seedToolkitBindings({
			agentId: 'agnt_active_1',
			bound: [{ toolkit_id: 'tk_github', name: 'GitHub Tools' }],
			workspace: [{ toolkit_id: 'tk_github', name: 'GitHub Tools' }],
		});
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		// The bound row renders the human NAME (resolved via the per-id read) and
		// still shows the id as a secondary line + an Unbind control.
		const row = await screen.findByTestId('bound-toolkit-row');
		expect(await within(row).findByText('GitHub Tools')).toBeInTheDocument();
		expect(within(row).getByText('tk_github')).toBeInTheDocument();
		await user.click(within(row).getByRole('button', { name: 'Unbind toolkit GitHub Tools' }));

		// Inline confirm appears on the row (no modal) with a GENERIC prompt — the
		// row already names the toolkit, so the confirm doesn't repeat it. The
		// group's aria-label still identifies the target for screen readers.
		const group = await within(row).findByRole('group', {
			name: /unbind GitHub Tools for the agent\?/i,
		});
		expect(within(group).getByText('Unbind this toolkit?')).toBeInTheDocument();
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
		await user.click(
			within(group).getByRole('button', { name: 'Unbind toolkit GitHub Tools' }),
		);

		// DELETE fired → the mock store dropped the binding and the row disappears.
		await waitFor(() => expect(bound.some((b) => b.toolkit_id === 'tk_github')).toBe(false));
		await waitFor(() =>
			expect(screen.queryByTestId('bound-toolkit-row')).not.toBeInTheDocument(),
		);
	});

	it('dismisses the inline unbind confirm on Cancel without firing DELETE', async () => {
		const bound = seedToolkitBindings({
			agentId: 'agnt_active_1',
			bound: [{ toolkit_id: 'tk_github', name: 'GitHub Tools' }],
			workspace: [{ toolkit_id: 'tk_github', name: 'GitHub Tools' }],
		});
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		const row = await screen.findByTestId('bound-toolkit-row');
		await screen.findByText('GitHub Tools');
		await user.click(within(row).getByRole('button', { name: 'Unbind toolkit GitHub Tools' }));

		const group = await within(row).findByRole('group', {
			name: /unbind GitHub Tools for the agent\?/i,
		});
		await user.click(within(group).getByRole('button', { name: 'Cancel' }));

		// Confirm collapses back to the default Unbind control and nothing was deleted.
		await waitFor(() =>
			expect(
				within(row).queryByRole('group', { name: /unbind GitHub Tools for the agent\?/i }),
			).not.toBeInTheDocument(),
		);
		expect(
			within(row).getByRole('button', { name: 'Unbind toolkit GitHub Tools' }),
		).toBeInTheDocument();
		expect(bound.some((b) => b.toolkit_id === 'tk_github')).toBe(true);
	});

	it('renders a suspended toolkit as a non-selectable, focusable row', async () => {
		seedToolkitBindings({
			agentId: 'agnt_active_1',
			bound: [],
			workspace: [
				{ toolkit_id: 'tk_active', name: 'Active Tools', active: true },
				{ toolkit_id: 'tk_suspended', name: 'Suspended Tools', active: false },
			],
		});
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('button', { name: 'Bind toolkit' }));
		const dialog = await screen.findByRole('dialog', { name: /bind toolkit/i });

		await within(dialog).findByText('Suspended Tools');
		const rows = within(dialog).getAllByTestId('toolkit-picker-row');
		const suspendedRow = rows.find((r) => within(r).queryByText('Suspended Tools'));
		expect(suspendedRow).toBeDefined();
		// aria-disabled (not native disabled) so it stays keyboard-focusable, and
		// the "suspended" badge + an accessible rationale are surfaced.
		expect(suspendedRow).toHaveAttribute('aria-disabled', 'true');
		expect(suspendedRow).not.toBeDisabled();
		expect(suspendedRow).toHaveAttribute('data-suspended', 'true');
		expect(within(suspendedRow as HTMLElement).getByText('suspended')).toBeInTheDocument();
		expect(
			within(suspendedRow as HTMLElement).getByText(/cannot be bound/i),
		).toBeInTheDocument();

		// Clicking it is a no-op — the dialog stays open (no bind fires).
		await user.click(suspendedRow as HTMLElement);
		expect(screen.getByRole('dialog', { name: /bind toolkit/i })).toBeInTheDocument();
	});

	it('hides already-bound toolkits from the picker', async () => {
		seedToolkitBindings({
			agentId: 'agnt_active_1',
			bound: [{ toolkit_id: 'tk_github', name: 'GitHub Tools' }],
			workspace: [
				{ toolkit_id: 'tk_github', name: 'GitHub Tools' },
				{ toolkit_id: 'tk_stripe', name: 'Stripe Tools' },
			],
		});
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('button', { name: 'Bind toolkit' }));
		const dialog = await screen.findByRole('dialog', { name: /bind toolkit/i });

		// The unbound toolkit shows up…
		expect(await within(dialog).findByText('Stripe Tools')).toBeInTheDocument();
		// …and the already-bound one is hidden from the picker candidates.
		expect(within(dialog).queryByText('GitHub Tools')).not.toBeInTheDocument();
	});
});
