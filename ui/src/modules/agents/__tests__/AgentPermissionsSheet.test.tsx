/**
 * AgentPermissionsSheet — the dock's Permissions surface: scopes
 * and connected clients behind one verb. The cards' own suites cover their
 * internals; here the mutations must carry the SELECTED agent's id.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
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

/** Open the Permissions sheet from the dock and return a scoped `within`. */
async function openSheet(user: ReturnType<typeof userEvent.setup>) {
	const dock = await findDock();
	await user.click(dock.getByRole('button', { name: 'Permissions' }));
	return within(await screen.findByTestId('sheet-primitive'));
}

describe('AgentPermissionsSheet — the dock Permissions surface', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([]);
		resetApisStore([]);
	});

	it('Permissions is an icon verb named by tooltip and aria-label', async () => {
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		const verb = dock.getByRole('button', { name: 'Permissions' });
		// Icon-only, like every other dock verb: the visible label is sr-only.
		const label = within(verb).getByText('Permissions');
		expect(getComputedStyle(label).position).toBe('absolute');
		expect(getComputedStyle(label).width).toBe('1px');

		// Keyboard focus reveals the shared Tooltip immediately.
		verb.focus();
		expect(await screen.findByRole('tooltip')).toHaveTextContent('Permissions');
		verb.blur();
		await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());
	});

	it('opens the sheet with both cards wired to the selected agent', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		expect(sheet.getByRole('heading', { name: 'Permissions' })).toBeInTheDocument();
		expect(sheet.getByText('support-agent')).toBeInTheDocument();

		// The copy names the two permission models users conflate —
		// platform scopes vs. what the agent may call upstream (the tiles).
		expect(sheet.getByText(/control plane/)).toBeInTheDocument();
		expect(sheet.getByText(/API tiles on the main screen/)).toBeInTheDocument();

		// ScopesCard: agnt_active_1's seeded platform grants render as chips.
		const scopeList = await sheet.findByRole('list', { name: 'Granted scopes' });
		expect(within(scopeList).getByText('capabilities:execute')).toBeInTheDocument();

		// ConnectedClientsCard: the live consent→agent grant.
		expect(await sheet.findByText('Cursor')).toBeInTheDocument();
	});

	it("scopes edit round-trips: the PUT carries the selected agent's id", async () => {
		let putId: string | undefined;
		let putBody: { scopes?: string[] } | undefined;
		worker.use(
			http.put('/agents/:id/scopes', async ({ params, request }) => {
				putId = params.id as string;
				putBody = (await request.json()) as { scopes?: string[] };
				return HttpResponse.json({ scopes: putBody.scopes });
			}),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		await sheet.findByRole('list', { name: 'Granted scopes' });
		await user.click(sheet.getByRole('button', { name: 'Edit scopes for support-agent' }));
		const dialog = await screen.findByRole('dialog', { name: /Edit scopes — support-agent/ });

		// Grant one more scope; the card's own suite covers the editor's
		// internals — this proves the sheet wired the card to the right agent.
		await user.type(within(dialog).getByLabelText('Search scopes'), 'credentials:read');
		await user.click(await within(dialog).findByRole('checkbox', { name: 'credentials:read' }));
		await user.click(within(dialog).getByRole('button', { name: 'Save scopes' }));

		await waitFor(() => expect(putId).toBe('agnt_active_1'));
		expect(putBody?.scopes).toContain('credentials:read');
	});

	// Real (CDP-driven) Escape: a native <dialog>'s close request only fires
	// for trusted key events — same pattern as the other dock-sheet specs.
	it('Escape closes the edit-scopes dialog first, then the sheet, and restores focus', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		await sheet.findByRole('list', { name: 'Granted scopes' });
		await user.click(sheet.getByRole('button', { name: 'Edit scopes for support-agent' }));
		await screen.findByRole('dialog', { name: /Edit scopes — support-agent/ });

		// First Escape reaches the native dialog only; the sheet survives.
		await browserUser.keyboard('{Escape}');
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: /Edit scopes — support-agent/ }),
			).not.toBeInTheDocument(),
		);
		expect(screen.getByRole('heading', { name: 'Permissions' })).toBeInTheDocument();

		// Second Escape closes the sheet; focus lands back on the dock verb.
		await browserUser.keyboard('{Escape}');
		await waitFor(
			() => expect(screen.queryByTestId('sheet-primitive')).not.toBeInTheDocument(),
			{ timeout: 2000 },
		);
		await waitFor(() =>
			expect(
				within(screen.getByTestId('agent-dock')).getByRole('button', {
					name: 'Permissions',
				}),
			).toHaveFocus(),
		);
	});

	it('archived agent: the sheet is history — no grant invite', async () => {
		seedExtraAgents([{ id: 'agnt_archived_1', name: 'retired-bot', status: 'archived' }]);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_archived_1');
		const sheet = await openSheet(user);

		// The copy names the sweep; the scope editor is gated off.
		expect(sheet.getByText(/swept this agent.s scope grants/)).toBeInTheDocument();
		expect(await sheet.findByText('No scopes granted.')).toBeInTheDocument();
		expect(
			sheet.queryByRole('button', { name: /Edit scopes for retired-bot/ }),
		).not.toBeInTheDocument();
	});

	it('open sheet passes axe', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);
		await sheet.findByRole('list', { name: 'Granted scopes' });
		// Wait out the backdrop's opacity transition: axe measures contrast
		// against the half-faded overlay otherwise and flags the page beneath.
		await waitFor(() => {
			const overlay = screen.getByTestId('sheet-backdrop');
			expect(overlay && getComputedStyle(overlay).opacity).toBe('1');
		});
		await checkA11y(document.body, { modal: true });
	});

	it('390px: the sheet opens full-screen with every section reachable', async () => {
		await page.viewport(390, 844);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();
		await waitFor(() =>
			expect(dock.getByRole('button', { name: 'Permissions' })).toBeVisible(),
		);
		const sheet = await openSheet(user);

		expect(sheet.getByRole('heading', { name: 'Permissions' })).toBeInTheDocument();
		expect(await sheet.findByRole('list', { name: 'Granted scopes' })).toBeInTheDocument();
		expect(await sheet.findByText('Cursor')).toBeInTheDocument();
	});
});
