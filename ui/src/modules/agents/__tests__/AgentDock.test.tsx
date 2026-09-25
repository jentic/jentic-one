/**
 * AgentDock — the fixed bottom action dock. Covers the verb set per lifecycle
 * state, the in-flight guards (double-click, Undo-on-deactivate, "succeeded
 * but the grid didn't refresh"), the sheets it opens, archive wording,
 * tooltips, a11y and the 390px viewport.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse, delay } from 'msw';
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
import {
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CredentialType } from '@/shared/credentials/api';
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

/** The dock itself, for scoping queries away from panel/queue duplicates. */
async function findDock(): Promise<ReturnType<typeof within>> {
	return within(await screen.findByTestId('agent-dock'));
}

describe('AgentDock — fixed bottom action dock', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.API_KEY,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			}),
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
			}),
		]);
		resetApisStore([]);
	});

	// --- Verb set per lifecycle state --------------------------------------

	it('active agent: serving toggle plus every verb', async () => {
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		const toggle = dock.getByTestId('dock-serving-toggle');
		expect(toggle).toHaveTextContent('Serving');
		expect(toggle).toHaveAttribute('aria-pressed', 'true');
		// Webapp ToolkitDock shape: toggle + divider + icon verbs, nothing else.
		// Identity leaves the visual layer but stays in the a11y tree via the group name.
		expect(screen.getByTestId('agent-dock')).toHaveAccessibleName('Actions for support-agent');
		expect(dock.queryByText('support-agent')).not.toBeInTheDocument();
		expect(dock.queryByText('Active')).not.toBeInTheDocument();
		for (const label of ['API key', 'Permissions', 'Activity', 'MCP', 'Settings']) {
			expect(dock.getByRole('button', { name: label })).toBeEnabled();
		}
		// The dock is agent-scoped only — the org-wide Credentials verb lives on
		// the page header instead.
		expect(dock.queryByRole('button', { name: 'Credentials' })).not.toBeInTheDocument();
		// Settings is a sheet verb, never a link out to the console.
		expect(dock.queryByRole('link', { name: /Settings/ })).not.toBeInTheDocument();
		expect(dock.getByRole('button', { name: /Archive support-agent/ })).toBeEnabled();
	});

	it('disabled agent: toggle says "Not serving" — never read-only', async () => {
		renderPage('/?agent=agnt_disabled_1');
		const dock = await findDock();

		const toggle = dock.getByTestId('dock-serving-toggle');
		expect(toggle).toHaveTextContent('Not serving');
		expect(toggle).toHaveAttribute('aria-pressed', 'false');
		// A disabled agent stays fully editable: all verbs stay live.
		expect(dock.getByRole('button', { name: 'API key' })).toBeEnabled();
		expect(dock.getByRole('button', { name: /Archive legacy-scraper/ })).toBeEnabled();
	});

	it('pending agent: Approve holds the toggle position and works', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_pending_1');
		const dock = await findDock();

		expect(dock.queryByTestId('dock-serving-toggle')).not.toBeInTheDocument();
		await user.click(dock.getByTestId('dock-approve'));

		// Approval lands: the dock re-renders with the live serving toggle.
		await waitFor(() =>
			expect(
				within(screen.getByTestId('agent-dock')).getByTestId('dock-serving-toggle'),
			).toHaveTextContent('Serving'),
		);
	});

	it('rejected agent: no serving verb at all', async () => {
		renderPage('/?agent=agnt_rejected_1');
		const dock = await findDock();

		expect(dock.queryByTestId('dock-serving-toggle')).not.toBeInTheDocument();
		expect(dock.queryByTestId('dock-approve')).not.toBeInTheDocument();
		expect(dock.getByTestId('dock-state-note')).toHaveTextContent(/Rejected — not serving/);
	});

	it('archived agent: reduced dock — no live toggle, no Archive verb', async () => {
		seedExtraAgents([{ id: 'agnt_archived_1', name: 'retired-bot', status: 'archived' }]);
		renderPage('/?agent=agnt_archived_1');
		const dock = await findDock();

		expect(dock.queryByTestId('dock-serving-toggle')).not.toBeInTheDocument();
		expect(dock.queryByTestId('dock-approve')).not.toBeInTheDocument();
		expect(dock.queryByRole('button', { name: /Archive/ })).not.toBeInTheDocument();
		expect(dock.getByTestId('dock-state-note')).toHaveTextContent(/Archived/);
		// The read affordances stay reachable — including Permissions, which
		// renders the swept grants as history (see AgentPermissionsSheet).
		expect(dock.getByRole('button', { name: 'Activity' })).toBeEnabled();
		expect(dock.getByRole('button', { name: 'Permissions' })).toBeEnabled();
		expect(dock.getByRole('button', { name: 'MCP' })).toBeEnabled();
		expect(dock.getByRole('button', { name: 'Settings' })).toBeEnabled();
	});

	// --- In-flight and refresh guards ----------------------------------------

	it('double-click guard: a second click while in flight is a no-op', async () => {
		let calls = 0;
		worker.use(
			http.post('/agents/agnt_active_1\\:disable', async () => {
				calls += 1;
				await delay(250);
				return new HttpResponse(null, { status: 204 });
			}),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		const toggle = dock.getByTestId('dock-serving-toggle');
		await user.click(toggle);
		await user.click(toggle); // in flight — must not fire again

		await screen.findByText(/support-agent is no longer serving traffic/);
		expect(calls).toBe(1);
	});

	it('Undo on the deactivate toast re-enables the agent', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		await user.click(dock.getByTestId('dock-serving-toggle'));
		await screen.findByText(/support-agent is no longer serving traffic/);

		await user.click(screen.getByRole('button', { name: 'Undo' }));

		await screen.findByText(/support-agent is serving traffic again/);
		await waitFor(() =>
			expect(
				within(screen.getByTestId('agent-dock')).getByTestId('dock-serving-toggle'),
			).toHaveAttribute('aria-pressed', 'true'),
		);
	});

	it('write landed but refetch failed: reports staleness, not failure', async () => {
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		// The lifecycle write succeeds; only the roster refetch breaks.
		worker.use(http.get('/agents', () => new HttpResponse(null, { status: 500 })));

		const user = userEvent.setup();
		await user.click(dock.getByTestId('dock-serving-toggle'));

		await screen.findByText(/The agent is disabled, but the fleet view could not refresh/);
		// It must NOT claim the toggle itself failed.
		expect(screen.queryByText(/Couldn't update/)).not.toBeInTheDocument();
	});

	it("agent A's in-flight toggle never blocks agent B's toggle (per-agent scoping)", async () => {
		// Hold agent A's disable in flight behind a gate: the dock survives selection
		// changes, so bookkeeping scoped to the instance would mark B's toggle loading.
		let releaseDisable!: () => void;
		const gate = new Promise<void>((resolve) => {
			releaseDisable = resolve;
		});
		worker.use(
			http.post('/agents/agnt_active_1\\:disable', async () => {
				await gate;
				return new HttpResponse(null, { status: 204 });
			}),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		// Start agent A's (slow) disable — its own toggle goes in flight.
		await user.click(dock.getByTestId('dock-serving-toggle'));
		await waitFor(() =>
			expect(
				within(screen.getByTestId('agent-dock')).getByTestId('dock-serving-toggle'),
			).toHaveAttribute('aria-busy', 'true'),
		);

		// Select agent B while A's mutation is still in flight: B's toggle
		// must not be stuck loading — it stays enabled and can act.
		await user.click(screen.getByRole('tab', { name: /legacy-scraper/ }));
		const toggleB = within(screen.getByTestId('agent-dock')).getByTestId('dock-serving-toggle');
		expect(toggleB).toHaveTextContent('Not serving');
		expect(toggleB).not.toHaveAttribute('aria-busy', 'true');
		expect(toggleB).toBeEnabled();

		// B's own (independent) mutation completes while A's is in flight.
		await user.click(toggleB);
		await screen.findByText(/legacy-scraper is serving traffic again/);

		// Re-selecting A mid-flight shows the loading state again — the guard is
		// per-agent, not per-component-instance.
		await user.click(screen.getByRole('tab', { name: /support-agent/ }));
		await waitFor(() =>
			expect(
				within(screen.getByTestId('agent-dock')).getByTestId('dock-serving-toggle'),
			).toHaveAttribute('aria-busy', 'true'),
		);

		// Release A's write; its own toast lands and the flight settles.
		releaseDisable();
		await screen.findByText(/support-agent is no longer serving traffic/);
		await waitFor(() =>
			expect(
				within(screen.getByTestId('agent-dock')).getByTestId('dock-serving-toggle'),
			).not.toHaveAttribute('aria-busy', 'true'),
		);
	});

	// --- Verb surfaces -------------------------------------------------------

	it('Archive routes through the existing confirm and says archive, never delete', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		await user.click(dock.getByRole('button', { name: /Archive support-agent/ }));

		const dialog = await screen.findByRole('dialog');
		expect(within(dialog).getByRole('heading', { name: 'Archive agent' })).toBeInTheDocument();
		expect(within(dialog).getByRole('button', { name: /^Archive/ })).toBeInTheDocument();
		expect(within(dialog).queryByRole('button', { name: /delete/i })).not.toBeInTheDocument();
	});

	it('API key opens the existing keys surface in a sheet', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		await user.click(dock.getByRole('button', { name: 'API key' }));

		const sheet = within(await screen.findByRole('dialog'));
		expect(sheet.getByRole('heading', { name: 'API key' })).toBeInTheDocument();
		// The rehosted AgentKeysPanel, not a re-implementation: its generate
		// affordance is present for an active agent without a key.
		expect(await sheet.findByRole('button', { name: /Generate/i })).toBeInTheDocument();
	});

	it('Credentials is not a dock verb — the inventory opens from page level', async () => {
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		// The dock speaks for the selected agent only; the org-wide wallet
		// verb lives on the page header (see CredentialInventorySheet.test).
		expect(dock.queryByRole('button', { name: 'Credentials' })).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Credentials' })).toBeInTheDocument();
	});

	// --- Escape with a nested dialog above a sheet ---------------------------
	// Escape here goes through the REAL (CDP-driven) keyboard: a native <dialog>'s
	// close request only fires for trusted key events.

	it('Escape closes the one-time key dialog above the keys sheet first, then the sheet', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		await user.click(dock.getByRole('button', { name: 'API key' }));
		const sheet = within(await screen.findByTestId('sheet-primitive'));
		await user.click(await sheet.findByRole('button', { name: /Generate API key/i }));

		// The plaintext dialog (a native modal) opens in the top layer.
		await screen.findByRole('dialog', { name: 'API key generated' });

		// First Escape must reach the native dialog's cancel behaviour: only
		// the dialog closes; the sheet keeps the user's place underneath.
		await browserUser.keyboard('{Escape}');
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: 'API key generated' }),
			).not.toBeInTheDocument(),
		);
		expect(screen.getByRole('heading', { name: 'API key', level: 2 })).toBeInTheDocument();

		// Second Escape closes the sheet.
		await browserUser.keyboard('{Escape}');
		await waitFor(
			() => expect(screen.queryByTestId('sheet-primitive')).not.toBeInTheDocument(),
			{ timeout: 2000 },
		);
	});

	// --- Quality gates -------------------------------------------------------

	it('icon verbs are named by tooltip on hover/focus, not visible text', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();
		const permissions = dock.getByRole('button', { name: 'Permissions' });

		// Icon-only: the verb's text label is visually hidden (sr-only keeps
		// the accessible name every other spec queries by).
		const label = within(permissions).getByText('Permissions');
		expect(getComputedStyle(label).position).toBe('absolute');
		expect(getComputedStyle(label).width).toBe('1px');

		// Keyboard focus reveals the shared Tooltip immediately (no hover delay).
		permissions.focus();
		const focusTip = await screen.findByRole('tooltip');
		expect(focusTip).toHaveTextContent('Permissions');
		permissions.blur();
		await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());

		// Hover reveals it too (after the tooltip's open delay) — and unhover
		// dismisses it (also leaves no bubble behind for the axe spec below).
		const apiKey = dock.getByRole('button', { name: 'API key' });
		await user.hover(apiKey);
		expect(await screen.findByRole('tooltip')).toHaveTextContent('API key');
		await user.unhover(apiKey);
		await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());
	});

	it('surface with the dock mounted passes axe', async () => {
		renderPage('/?agent=agnt_active_1');
		await findDock();
		// The panel fades in (180ms); axe must not sample contrast mid-animation or
		// muted copy reads as low-contrast against the blended background.
		await waitFor(() => {
			const section = document.querySelector('section[aria-label="APIs for support-agent"]');
			expect(section).not.toBeNull();
			expect(getComputedStyle(section as Element).opacity).toBe('1');
		});
		await checkA11y(document.body);
	});

	it('390px: dock verbs stay reachable alongside the bottom navbar', async () => {
		await page.viewport(390, 844);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		// Icon-only at every breakpoint; the accessible names carry the verbs.
		// (waitFor: the dock's entrance animation starts at opacity 0.)
		await waitFor(() => expect(dock.getByTestId('dock-serving-toggle')).toBeVisible());
		await user.click(dock.getByRole('button', { name: 'API key' }));
		expect(
			within(await screen.findByRole('dialog')).getByRole('heading', {
				name: 'API key',
			}),
		).toBeInTheDocument();
	});
});
