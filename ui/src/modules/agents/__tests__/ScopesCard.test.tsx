import { describe, it, expect, beforeEach } from 'vitest';
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
import { ScopesCard } from '@/modules/agents/components/ScopesCard';

function renderCard(props: {
	actorKind: 'agent' | 'service-account';
	actorId: string;
	actorName: string;
	canEdit?: boolean;
}) {
	return renderWithProviders(
		<>
			<ScopesCard {...props} />
			<Toaster />
		</>,
	);
}

describe('ScopesCard', () => {
	beforeEach(() => {
		setToken('test-token');
		resetAgentsStore();
	});

	it('renders the granted scopes as chips for an agent', async () => {
		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		const list = await screen.findByRole('list', { name: 'Granted scopes' });
		expect(within(list).getByText('capabilities:execute')).toBeInTheDocument();
		expect(within(list).getByText('executions:read')).toBeInTheDocument();
	});

	it('shows an honest empty state when no scopes are granted', async () => {
		renderCard({
			actorKind: 'agent',
			actorId: 'agnt_pending_1',
			actorName: 'inbox-triage-bot',
		});
		expect(await screen.findByText('No scopes granted.', { exact: false })).toBeInTheDocument();
	});

	it('hides the edit affordance when canEdit is false', async () => {
		renderCard({
			actorKind: 'agent',
			actorId: 'agnt_active_1',
			actorName: 'support-agent',
			canEdit: false,
		});
		await screen.findByRole('list', { name: 'Granted scopes' });
		expect(
			screen.queryByRole('button', { name: 'Edit scopes for support-agent' }),
		).not.toBeInTheDocument();
	});

	it('grants a new scope and reflects it back as a chip', async () => {
		const user = userEvent.setup();
		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');

		// Search narrows + auto-expands the group, so the scope row is reachable.
		await user.type(within(dialog).getByLabelText('Search scopes'), 'credentials:read');
		await user.click(await within(dialog).findByRole('checkbox', { name: 'credentials:read' }));
		await user.click(within(dialog).getByRole('button', { name: 'Save scopes' }));

		expect(await screen.findByText('Scopes updated')).toBeInTheDocument();
		await waitFor(() => {
			const list = screen.getByRole('list', { name: 'Granted scopes' });
			expect(within(list).getByText('credentials:read')).toBeInTheDocument();
		});
	});

	it('keeps Save disabled until the selection differs from the current grants', async () => {
		const user = userEvent.setup();
		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');
		const save = within(dialog).getByRole('button', { name: 'Save scopes' });

		// No change yet → Save is a no-op and stays disabled.
		expect(save).toBeDisabled();

		// Toggle a scope on → selection now differs → Save enables.
		await user.type(within(dialog).getByLabelText('Search scopes'), 'credentials:read');
		const checkbox = await within(dialog).findByRole('checkbox', { name: 'credentials:read' });
		await user.click(checkbox);
		expect(save).toBeEnabled();

		// Toggle it back off → selection matches the original grants → disabled again.
		await user.click(checkbox);
		expect(save).toBeDisabled();
	});

	it('disables scopes the caller cannot grant', async () => {
		const user = userEvent.setup();
		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');
		// `org:admin` is the real catalogue's non-grantable entry for a non-admin
		// operator (grantable_by_caller: false) → its row must be disabled.
		await user.type(within(dialog).getByLabelText('Search scopes'), 'org:admin');

		const orgAdmin = await within(dialog).findByRole('checkbox', {
			name: 'org:admin',
		});
		expect(orgAdmin).toBeDisabled();
	});

	it('surfaces a clear message if the backend rejects a grant with 403', async () => {
		// The real actor-scope PUT does not 403 on grantability today, but the
		// component handles a 403 defensively (e.g. future enforcement / a perms
		// change mid-session). Inject one to cover that path.
		const user = userEvent.setup();
		const { worker } = await import('@/mocks/browser');
		const { createErrorHandler } = await import('@/__tests__/test-utils');
		worker.use(
			createErrorHandler('put', '/agents/:id/scopes', {
				status: 403,
				body: { detail: 'forbidden' },
			}),
		);

		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText('Search scopes'), 'credentials:read');
		await user.click(await within(dialog).findByRole('checkbox', { name: 'credentials:read' }));
		await user.click(within(dialog).getByRole('button', { name: 'Save scopes' }));

		expect(
			await within(dialog).findByText(
				'You don’t have permission to grant one or more of these scopes.',
			),
		).toBeInTheDocument();
		// Dialog stays open so the operator can adjust the selection.
		expect(screen.getByRole('dialog')).toBeInTheDocument();
	});

	it('edits scopes for a service account too', async () => {
		const user = userEvent.setup();
		renderCard({
			actorKind: 'service-account',
			actorId: 'sva_active_1',
			actorName: 'metrics-exporter',
		});
		const list = await screen.findByRole('list', { name: 'Granted scopes' });
		expect(within(list).getByText('credentials:read')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Edit scopes for metrics-exporter' }));
		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText('Search scopes'), 'agents:read');
		await user.click(await within(dialog).findByRole('checkbox', { name: 'agents:read' }));
		await user.click(within(dialog).getByRole('button', { name: 'Save scopes' }));

		await waitFor(() => {
			const updated = screen.getByRole('list', { name: 'Granted scopes' });
			expect(within(updated).getByText('agents:read')).toBeInTheDocument();
		});
	});

	it('renders a default-granted owner scope as an editable catalogue chip', async () => {
		// `owner:access-requests:read` is granted to `agnt_active_1` by default and
		// is now catalogued, so it must render as a normal editable picker row
		// (a checkbox), not as a preserved non-catalogue scope.
		const user = userEvent.setup();
		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		const list = await screen.findByRole('list', { name: 'Granted scopes' });
		expect(within(list).getByText('owner:access-requests:read')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');
		await user.type(
			within(dialog).getByLabelText('Search scopes'),
			'owner:access-requests:read',
		);
		const checkbox = await within(dialog).findByRole('checkbox', {
			name: 'owner:access-requests:read',
		});
		// Catalogue-backed and already granted → checked and editable (not disabled).
		expect(checkbox).toBeChecked();
		expect(checkbox).toBeEnabled();
	});

	it('preserves a granted scope that is absent from the catalogue when saving', async () => {
		// `agnt_active_1` holds `legacy:orphaned:read`, a synthetic scope that is NOT
		// in the permission catalogue (a legacy grant the backend never lists). The
		// save must include it untouched — dropping it would silently revoke it.
		const user = userEvent.setup();
		let putBody: { scopes?: string[] } | undefined;
		const { worker } = await import('@/mocks/browser');
		const { http, HttpResponse } = await import('msw');
		worker.use(
			http.put('/agents/:id/scopes', async ({ request }) => {
				putBody = (await request.json()) as { scopes?: string[] };
				return HttpResponse.json({ scopes: putBody.scopes });
			}),
		);

		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');
		// The "preserved" hint should tell the operator the non-catalogue scope is kept.
		expect(
			await within(dialog).findByText('not editable here will be preserved', {
				exact: false,
			}),
		).toBeInTheDocument();

		await user.type(within(dialog).getByLabelText('Search scopes'), 'credentials:read');
		await user.click(await within(dialog).findByRole('checkbox', { name: 'credentials:read' }));
		await user.click(within(dialog).getByRole('button', { name: 'Save scopes' }));

		await waitFor(() => {
			expect(putBody?.scopes).toContain('legacy:orphaned:read');
		});
		expect(putBody?.scopes).toEqual(
			expect.arrayContaining(['capabilities:execute', 'credentials:read']),
		);
	});

	it('does not count preserved non-catalogue scopes in the picker total', async () => {
		// Regression: `agnt_active_1` holds `legacy:orphaned:read`, absent from the
		// catalogue. It used to leak into the picker's `selectedScopes`, inflating
		// "X of Y selected" so X > Y and "Select all" could never flip.
		const user = userEvent.setup();
		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');

		const count = await within(dialog).findByText(/\d+ of \d+ selected/);
		const [, selectedStr, totalStr] = /(\d+) of (\d+) selected/.exec(count.textContent ?? '')!;
		const selectedCount = Number(selectedStr);
		const selectableTotal = Number(totalStr);
		// The selected count must never exceed the selectable rows the picker shows.
		expect(selectedCount).toBeLessThanOrEqual(selectableTotal);
		// Selecting every selectable row must flip the toggle to "Deselect all".
		await user.click(within(dialog).getByRole('button', { name: 'Select all' }));
		expect(
			await within(dialog).findByRole('button', { name: 'Deselect all' }),
		).toBeInTheDocument();
	});

	it('confirms before removing a held high-privilege scope (org:admin)', async () => {
		// Simulate an admin operator: the catalogue marks org:admin grantable, and
		// the actor already holds it. A routine "Deselect all" + Save would silently
		// revoke it — the card must require explicit confirmation first.
		const user = userEvent.setup();
		let putBody: { scopes?: string[] } | undefined;
		const { worker } = await import('@/mocks/browser');
		const { http, HttpResponse } = await import('msw');
		worker.use(
			http.get('/permissions', () =>
				HttpResponse.json({
					data: [
						{
							name: 'org:admin',
							description: 'Org-wide superuser',
							implies: [],
							grantable_by_caller: true,
						},
						{
							name: 'agents:read',
							description: 'Read agents',
							implies: [],
							grantable_by_caller: true,
						},
					],
				}),
			),
			http.get('/agents/:id/scopes', () =>
				HttpResponse.json({ scopes: ['org:admin', 'agents:read'] }),
			),
			http.put('/agents/:id/scopes', async ({ request }) => {
				putBody = (await request.json()) as { scopes?: string[] };
				return HttpResponse.json({ scopes: putBody.scopes });
			}),
		);

		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');

		// Deselect everything (drops the grantable org:admin) then save.
		await user.click(within(dialog).getByRole('button', { name: 'Deselect all' }));
		await user.click(within(dialog).getByRole('button', { name: 'Save scopes' }));

		// A confirmation dialog appears; the PUT has NOT fired yet.
		expect(
			await screen.findByRole('dialog', { name: 'Remove high-privilege scope?' }),
		).toBeInTheDocument();
		expect(putBody).toBeUndefined();

		await user.click(screen.getByRole('button', { name: 'Remove and save' }));
		await waitFor(() => expect(putBody).toBeDefined());
		expect(putBody?.scopes).not.toContain('org:admin');
	});

	it('keeps the dialog open and shows the error when a scope is malformed (422)', async () => {
		const user = userEvent.setup();
		const { worker } = await import('@/mocks/browser');
		const { createErrorHandler } = await import('@/__tests__/test-utils');
		worker.use(
			createErrorHandler('put', '/agents/:id/scopes', {
				status: 422,
				body: { detail: 'Invalid scope: bad scope' },
			}),
		);

		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText('Search scopes'), 'credentials:read');
		await user.click(await within(dialog).findByRole('checkbox', { name: 'credentials:read' }));
		await user.click(within(dialog).getByRole('button', { name: 'Save scopes' }));

		expect(await within(dialog).findByText('Invalid scope: bad scope')).toBeInTheDocument();
		expect(screen.getByRole('dialog')).toBeInTheDocument();
	});

	it('keeps the dialog open and surfaces a network error on save', async () => {
		const user = userEvent.setup();
		const { worker } = await import('@/mocks/browser');
		const { createErrorHandler } = await import('@/__tests__/test-utils');
		worker.use(createErrorHandler('put', '/agents/:id/scopes', { networkError: true }));

		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText('Search scopes'), 'credentials:read');
		await user.click(await within(dialog).findByRole('checkbox', { name: 'credentials:read' }));
		await user.click(within(dialog).getByRole('button', { name: 'Save scopes' }));

		// The hook toasts a generic failure; the dialog stays open for a retry.
		// (The same copy appears both in the toast and inline, so match all.)
		expect(
			(await screen.findAllByText("Failed to update the agent's scopes.")).length,
		).toBeGreaterThan(0);
		expect(screen.getByRole('dialog')).toBeInTheDocument();
	});

	it('shows an error (and hides Edit) when the actor scopes fail to load', async () => {
		const { worker } = await import('@/mocks/browser');
		const { createErrorHandler } = await import('@/__tests__/test-utils');
		worker.use(createErrorHandler('get', '/agents/:id/scopes', { status: 500 }));

		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });

		expect(await screen.findByRole('alert')).toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'Edit scopes for support-agent' }),
		).not.toBeInTheDocument();
	});

	it('disables Save when the permission catalogue fails to load', async () => {
		const user = userEvent.setup();
		const { worker } = await import('@/mocks/browser');
		const { createErrorHandler } = await import('@/__tests__/test-utils');
		worker.use(createErrorHandler('get', '/permissions', { status: 500 }));

		renderCard({ actorKind: 'agent', actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByRole('list', { name: 'Granted scopes' });

		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog');
		expect(await within(dialog).findByRole('alert')).toBeInTheDocument();
		expect(within(dialog).getByRole('button', { name: 'Save scopes' })).toBeDisabled();
	});

	it('omits the grant prompt in the empty state when read-only', async () => {
		renderCard({
			actorKind: 'agent',
			actorId: 'agnt_pending_1',
			actorName: 'inbox-triage-bot',
			canEdit: false,
		});
		expect(await screen.findByText('No scopes granted.', { exact: false })).toBeInTheDocument();
		expect(
			screen.queryByText('can’t perform privileged operations', { exact: false }),
		).not.toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const user = userEvent.setup();
		const { container } = renderCard({
			actorKind: 'agent',
			actorId: 'agnt_active_1',
			actorName: 'support-agent',
		});
		await screen.findByRole('list', { name: 'Granted scopes' });
		await checkA11y(container);

		// Also check the editor dialog (picker + checkboxes) once it's open.
		await user.click(screen.getByRole('button', { name: 'Edit scopes for support-agent' }));
		await screen.findByRole('dialog');
		await within(await screen.findByRole('dialog')).findByLabelText('Search scopes');
		await checkA11y(container);
	});
});
