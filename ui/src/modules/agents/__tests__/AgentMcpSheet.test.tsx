/**
 * AgentMcpSheet — the dock's MCP surface: `McpPanel` (config card + session
 * history) behind its own verb. The panel's console suite covers its internals;
 * here the pinned `--context` snippet and the session read must be scoped to the
 * SELECTED agent, and an archived agent gets history only.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { page, userEvent as browserUser } from 'vitest/browser';
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

/** Open the MCP sheet from the dock and return a scoped `within`. */
async function openSheet(user: ReturnType<typeof userEvent.setup>) {
	const dock = await findDock();
	await user.click(dock.getByRole('button', { name: 'MCP' }));
	return within(await screen.findByTestId('sheet-primitive'));
}

describe('AgentMcpSheet — the dock MCP surface', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([]);
		resetApisStore([]);
	});

	it('MCP is an icon verb named by tooltip and aria-label', async () => {
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();

		const verb = dock.getByRole('button', { name: 'MCP' });
		// Icon-only, like every other dock verb: the visible label is sr-only.
		const label = within(verb).getByText('MCP');
		expect(getComputedStyle(label).position).toBe('absolute');
		expect(getComputedStyle(label).width).toBe('1px');

		// Keyboard focus reveals the shared Tooltip immediately.
		verb.focus();
		expect(await screen.findByRole('tooltip')).toHaveTextContent('MCP');
		verb.blur();
		await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());
	});

	it('opens the rehosted console MCP panel wired to the selected agent', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		expect(sheet.getByRole('heading', { name: 'MCP' })).toBeInTheDocument();
		// The agent's name appears in the frame subtitle AND in the config
		// card's copy ("Wire an MCP client … as support-agent").
		expect(sheet.getAllByText('support-agent').length).toBeGreaterThan(0);

		// The rehosted McpConfigCard, not a re-implementation: the pinned
		// `--context` snippet carries THIS agent's name.
		expect(await sheet.findByText('Connect via MCP')).toBeInTheDocument();
		expect(sheet.getByText('jentic mcp --context support-agent')).toBeInTheDocument();

		// The rehosted McpSessionsCard reads THIS agent's session history —
		// agnt_active_1 carries the seeded `mcp.session_started` events.
		expect(await sheet.findByText('MCP sessions')).toBeInTheDocument();
		expect(await sheet.findByText('claude-desktop 1.5.2')).toBeInTheDocument();
	});

	it('re-targets when a different agent is selected — snippet and empty history', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_disabled_1');
		const sheet = await openSheet(user);

		// The config card pins the OTHER agent's context…
		expect(await sheet.findByText('jentic mcp --context legacy-scraper')).toBeInTheDocument();
		// …and the sessions read is per-actor: only agnt_active_1 has fixture
		// sessions, so this agent renders the honest empty state.
		expect(await sheet.findByText(/No MCP sessions recorded/)).toBeInTheDocument();
	});

	it('archived agent: history only — no connect invitation', async () => {
		seedExtraAgents([{ id: 'agnt_archived_1', name: 'retired-bot', status: 'archived' }]);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_archived_1');
		const sheet = await openSheet(user);

		// The copy names the state; the config card (a copy-paste invitation
		// to connect an agent that can never authenticate again) is gated off.
		expect(sheet.getByText(/archived and can no longer authenticate/)).toBeInTheDocument();
		expect(sheet.queryByText('Connect via MCP')).not.toBeInTheDocument();
		expect(sheet.queryByText(/jentic mcp --context/)).not.toBeInTheDocument();
		// The session history stays — it is the read affordance.
		expect(await sheet.findByText('MCP sessions')).toBeInTheDocument();
		expect(await sheet.findByText(/No MCP sessions recorded/)).toBeInTheDocument();
	});

	// Real (CDP-driven) Escape — same pattern as the other dock-sheet specs.
	it('Escape closes the sheet and restores focus to the dock verb', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);
		await sheet.findByText('Connect via MCP');

		await browserUser.keyboard('{Escape}');
		await waitFor(
			() => expect(screen.queryByTestId('sheet-primitive')).not.toBeInTheDocument(),
			{ timeout: 2000 },
		);
		await waitFor(() =>
			expect(
				within(screen.getByTestId('agent-dock')).getByRole('button', { name: 'MCP' }),
			).toHaveFocus(),
		);
	});

	it('open sheet passes axe', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);
		await sheet.findByText('Connect via MCP');
		await sheet.findByText('claude-desktop 1.5.2');
		// Wait out the backdrop's opacity transition: axe measures contrast
		// against the half-faded overlay otherwise and flags the page beneath.
		await waitFor(() => {
			const overlay = screen.getByTestId('sheet-backdrop');
			expect(overlay && getComputedStyle(overlay).opacity).toBe('1');
		});
		await checkA11y(document.body, { modal: true });
	});

	it('390px: the sheet opens full-screen with both cards reachable', async () => {
		await page.viewport(390, 844);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const dock = await findDock();
		await waitFor(() => expect(dock.getByRole('button', { name: 'MCP' })).toBeVisible());
		const sheet = await openSheet(user);

		expect(await sheet.findByText('Connect via MCP')).toBeInTheDocument();
		expect(await sheet.findByText('MCP sessions')).toBeInTheDocument();
	});
});
