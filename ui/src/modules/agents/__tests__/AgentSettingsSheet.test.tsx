/**
 * AgentSettingsSheet — the dock's Settings surface: the console Settings
 * tab's `AgentSettingsPanel` (identity form via PATCH /agents/{id} + danger
 * zone) plus the agent's read-only provenance, rehosted behind the gear
 * verb. Covers the verb opening a sheet instead of navigating, the identity
 * form round-tripping a PATCH with the selected agent's id, the archive row
 * routing through the shared confirm, the archived read-only rendering,
 * Escape/focus behaviour consistent with the other dock sheets, and a11y.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http } from 'msw';
import { page, userEvent as browserUser } from 'vitest/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore, seedExtraAgents } from '@/modules/agents/mocks/handlers';
import { resetApisStore, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

function renderPage(route = '/') {
	return renderWithProviders(
		<>
			<AgentsPage />
			<Toaster />
		</>,
		{ route },
	);
}

/** The dock, for scoping the verb query away from panel duplicates. */
async function findDock(): Promise<ReturnType<typeof within>> {
	return within(await screen.findByTestId('agent-dock'));
}

/** Open the Settings sheet from the dock and return a scoped `within`. */
async function openSheet(user: ReturnType<typeof userEvent.setup>) {
	const dock = await findDock();
	await user.click(dock.getByRole('button', { name: 'Settings' }));
	const sheet = within(await screen.findByTestId('sheet-primitive'));
	// Let the sheet's own initial-focus pass settle (it lands on the header's
	// Close button once the entrance completes) before interacting — typing
	// that races it loses keystrokes to the focus move.
	await waitFor(() => expect(sheet.getByRole('button', { name: 'Close' })).toHaveFocus());
	return sheet;
}

describe('AgentSettingsSheet — the dock Settings surface', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([]);
		resetApisStore([]);
	});

	it('Settings is an icon verb that opens a sheet — no navigation', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		// A button, not a console link — the sheet IS the settings surface.
		const verb = dock.getByRole('button', { name: 'Settings' });
		expect(dock.queryByRole('link', { name: /Settings/ })).not.toBeInTheDocument();
		const label = within(verb).getByText('Settings');
		expect(getComputedStyle(label).position).toBe('absolute');

		await user.click(verb);
		const sheet = within(await screen.findByTestId('sheet-primitive'));
		expect(sheet.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
		expect(sheet.getByText('support-agent')).toBeInTheDocument();
		// The last console-only block rides along, so nothing forces a trip to
		// `/agents/:id`.
		expect(sheet.getByTestId('agent-provenance')).toBeInTheDocument();
		// The flat surface stays underneath — the sheet did not navigate away.
		expect(screen.getByRole('tablist', { name: 'Agents' })).toBeInTheDocument();
	});

	it("identity form round-trips: the PATCH carries the selected agent's id", async () => {
		let patchId: string | undefined;
		let patchBody: Record<string, unknown> | null = null;
		worker.use(
			http.patch('/agents/:id', async ({ params, request }) => {
				patchId = params.id as string;
				patchBody = (await request.clone().json()) as Record<string, unknown>;
				// Fall through to the module's stateful mock so the roster
				// refetch serves the renamed row.
				return undefined;
			}),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		// The rehosted IdentitySettingsCard: the immutable id row plus the
		// editable name/description form.
		expect(sheet.getByText('Agent ID')).toBeInTheDocument();
		expect(sheet.getByText('agnt_active_1')).toBeInTheDocument();

		const nameInput = await sheet.findByLabelText('Name');
		const save = sheet.getByRole('button', { name: 'Save changes' });
		expect(save).toBeDisabled(); // clean form

		await user.clear(nameInput);
		await user.type(nameInput, 'support-agent-v2');
		await user.click(save);

		expect(await screen.findByText('Agent updated')).toBeInTheDocument();
		expect(patchId).toBe('agnt_active_1');
		// Real PATCH semantics: only the dirty field crossed the wire.
		expect(patchBody).toEqual({ name: 'support-agent-v2' });
	});

	it('danger zone Archive routes through the shared confirm and says archive', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		await sheet.findByText('Danger zone');
		await user.click(sheet.getByRole('button', { name: 'Archive support-agent' }));

		// The page-level LifecycleDialogs confirm — a native modal above the
		// sheet — with archive vocabulary, never delete.
		const dialog = await screen.findByRole('dialog', { name: /Archive agent/ });
		expect(within(dialog).getByRole('button', { name: /^Archive/ })).toBeInTheDocument();
		expect(within(dialog).queryByRole('button', { name: /delete/i })).not.toBeInTheDocument();
	});

	it('archived agent: read-only — no form, no danger zone, the frozen note', async () => {
		seedExtraAgents([{ id: 'agnt_archived_1', name: 'retired-bot', status: 'archived' }]);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_archived_1');
		const sheet = await openSheet(user);

		// The id row stays (what audit rows reference)…
		expect(sheet.getByText('Agent ID')).toBeInTheDocument();
		expect(sheet.getByText('agnt_archived_1')).toBeInTheDocument();
		// …but the form must not invite edits the backend will 409.
		expect(
			sheet.getByText(/archived — its name and description are frozen/),
		).toBeInTheDocument();
		expect(sheet.queryByLabelText('Name')).not.toBeInTheDocument();
		expect(sheet.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
		// No destructive verb applies to a terminal state.
		expect(sheet.queryByText('Danger zone')).not.toBeInTheDocument();
	});

	// Real (CDP-driven) Escape: a native <dialog>'s close request only fires
	// for trusted key events — same pattern as the other dock-sheet specs.
	it('Escape closes the archive confirm first, then the sheet, and restores focus', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		await sheet.findByText('Danger zone');
		await user.click(sheet.getByRole('button', { name: 'Archive support-agent' }));
		await screen.findByRole('dialog', { name: /Archive agent/ });

		// First Escape reaches the native dialog only; the sheet survives.
		await browserUser.keyboard('{Escape}');
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: /Archive agent/ })).not.toBeInTheDocument(),
		);
		expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();

		// Second Escape closes the sheet; focus lands back on the dock verb.
		await browserUser.keyboard('{Escape}');
		await waitFor(
			() => expect(screen.queryByTestId('sheet-primitive')).not.toBeInTheDocument(),
			{ timeout: 2000 },
		);
		await waitFor(() =>
			expect(
				within(screen.getByTestId('agent-dock')).getByRole('button', {
					name: 'Settings',
				}),
			).toHaveFocus(),
		);
	});

	it('open sheet passes axe', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);
		await sheet.findByLabelText('Name');
		// Wait out the backdrop's opacity transition: axe measures contrast
		// against the half-faded overlay otherwise and flags the page beneath.
		await waitFor(() => {
			const overlay = document.querySelector('div.backdrop-blur-sm');
			expect(overlay && getComputedStyle(overlay).opacity).toBe('1');
		});
		await checkA11y(document.body);
	});

	it('390px: the sheet opens full-screen with the form reachable', async () => {
		await page.viewport(390, 844);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();
		await waitFor(() => expect(dock.getByRole('button', { name: 'Settings' })).toBeVisible());
		const sheet = await openSheet(user);

		expect(sheet.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
		expect(await sheet.findByLabelText('Name')).toBeInTheDocument();
		expect(await sheet.findByText('Danger zone')).toBeInTheDocument();
	});
});
