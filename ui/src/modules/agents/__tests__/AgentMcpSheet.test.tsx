/**
 * AgentMcpSheet — the dock's MCP surface: `McpPanel` (config card + session
 * history) behind its own verb. Pins the pinned `--context` snippet and the
 * session read scoped to the SELECTED agent, the config card's instance and
 * broker derivation, the sessions card's states, and history-only for an
 * archived agent.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { page, userEvent as browserUser } from 'vitest/browser';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore, seedExtraAgents } from '@/modules/agents/mocks/handlers';
import { resetApisStore, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { showHttpVariant } from '@/modules/agents/components/detail/McpPanel';
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

	it('opens the MCP panel wired to the selected agent', async () => {
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

	it('quotes a hostile agent name and server-reported URL, so a paste runs nothing', async () => {
		const hostile = "bot'; $(touch /tmp/x) `id` \\";
		seedExtraAgents([{ id: 'agnt_hostile_1', name: hostile, status: 'active' }]);
		worker.use(
			http.get('/instance', () =>
				HttpResponse.json({
					backend: 'remote',
					canonical_base_url: 'https://jentic.example.test/$(id)',
					host: 'jentic.example.test',
					instance_id: null,
					broker_url: 'https://broker.example.test/`id`',
				}),
			),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_hostile_1');
		const sheet = await openSheet(user);

		expect(
			await sheet.findByText(`jentic mcp --context 'bot'\\''; $(touch /tmp/x) \`id\` \\'`),
		).toBeInTheDocument();
		expect(
			await sheet.findByText(
				"jentic register --url 'https://jentic.example.test/$(id)' --broker-url 'https://broker.example.test/`id`'",
			),
		).toBeInTheDocument();
	});

	// --- The config card: what the snippet registers against -----------------

	it('pins the register prerequisites and the instance it targets', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);
		await sheet.findByText('Connect via MCP');

		// JSON client-config variant carries the same pinned args as the CLI one.
		expect(sheet.getByText(/"mcp", "--context", "support-agent"/)).toBeInTheDocument();
		// Prerequisites: CLI + register against THIS instance on the AGENT machine
		// (the stdio config encodes no base URL), or `jentic setup`. The instance
		// URL resolves async from GET /instance, so wait for it.
		expect(
			await sheet.findByText('jentic register --url https://jentic.example.test'),
		).toBeInTheDocument();
		expect(sheet.getByText('agent machine')).toBeInTheDocument();
		expect(sheet.getByText('jentic setup')).toBeInTheDocument();
		expect(sheet.getByText('jentic.example.test')).toBeInTheDocument();
		expect(sheet.getByText('local')).toBeInTheDocument();
	});

	it.each([
		[
			'no canonical base URL is configured',
			() =>
				http.get('/instance', () =>
					HttpResponse.json({
						backend: 'local',
						canonical_base_url: '',
						host: '',
						instance_id: null,
					}),
				),
		],
		[
			'GET /instance fails (500)',
			() => createErrorHandler('get', '/instance', { status: 500 }),
		],
	])('falls back to the browser origin when %s', async (_label, handler) => {
		worker.use(handler());
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);
		await sheet.findByText('Connect via MCP');

		// The operator is looking at a working address of this instance, so the
		// register command targets the browser's origin.
		expect(
			await sheet.findByText(`jentic register --url ${window.location.origin}`),
		).toBeInTheDocument();
	});

	it('survives a set-but-unparseable canonical_base_url (scheme-less) without crashing', async () => {
		// The backend allows an unparseable canonical_base_url with host: "" — a
		// scheme-less value must degrade to the raw string, not throw in render.
		worker.use(
			http.get('/instance', () =>
				HttpResponse.json({
					backend: 'local',
					canonical_base_url: 'jentic.example.com',
					host: '',
					instance_id: null,
				}),
			),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		expect(
			await sheet.findByText('jentic register --url jentic.example.com'),
		).toBeInTheDocument();
		expect(sheet.getAllByText('jentic.example.com').length).toBeGreaterThan(0);
	});

	it('hides the HTTP variant when the instance does not serve /mcp', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);
		await sheet.findByText('Connect via MCP');

		// The default /instance fixture predates `mcp_enabled` (an older backend);
		// the absent field reads as disabled — an advertised transport that 404s
		// would be a lie. The predicate is the single gate the card renders through.
		expect(showHttpVariant(undefined)).toBe(false);
		expect(showHttpVariant(false)).toBe(false);
		expect(sheet.queryByText(/Streamable HTTP/i)).not.toBeInTheDocument();
		expect(sheet.queryByText(/"url"/)).not.toBeInTheDocument();
	});

	it('renders the Streamable HTTP variant when the instance serves /mcp', async () => {
		worker.use(
			http.get('/instance', () =>
				HttpResponse.json({
					backend: 'local',
					canonical_base_url: 'https://jentic.example.test',
					host: 'jentic.example.test',
					instance_id: 'inst_digest_1',
					mcp_enabled: true,
				}),
			),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		// The url-variant snippet points at this instance's /mcp with a bearer
		// placeholder — per-request auth, no CLI needed on the agent machine.
		expect(await sheet.findByText(/Streamable HTTP/i)).toBeInTheDocument();
		expect(
			sheet.getByText(/"url": "https:\/\/jentic\.example\.test\/mcp"/),
		).toBeInTheDocument();
		expect(sheet.getByText(/Bearer <agent-api-key>/)).toBeInTheDocument();
	});

	it.each([
		['the instance reports none', undefined],
		['the reported broker URL is empty', ''],
	])(
		'keeps the --broker-url placeholder on a remote install when %s',
		async (_label, brokerUrl) => {
			worker.use(
				http.get('/instance', () =>
					HttpResponse.json({
						backend: 'remote',
						canonical_base_url: 'https://jentic.example.test',
						host: 'jentic.example.test',
						instance_id: 'inst_digest_1',
						...(brokerUrl === undefined ? {} : { broker_url: brokerUrl }),
					}),
				),
			);
			const user = userEvent.setup();
			renderPage('/?agent=agnt_active_1');
			const sheet = await openSheet(user);

			// On a remote install the broker is never derived from the control-plane
			// URL; without --broker-url `jentic execute` fail-closes, so the snippet
			// carries the flag — and, with nothing reported, sends the operator to a
			// human rather than guessing.
			expect(
				await sheet.findByText(
					"jentic register --url https://jentic.example.test --broker-url '<broker-url>'",
				),
			).toBeInTheDocument();
			expect(sheet.getByText(/fail-closes/)).toBeInTheDocument();
			expect(sheet.getByText(/Ask your operator/)).toBeInTheDocument();
		},
	);

	it('renders the real broker URL in the register snippet when the instance reports one (#1249)', async () => {
		worker.use(
			http.get('/instance', () =>
				HttpResponse.json({
					backend: 'remote',
					canonical_base_url: 'https://jentic.example.test',
					host: 'jentic.example.test',
					instance_id: 'inst_digest_1',
					broker_url: 'https://broker.jentic.example.test',
				}),
			),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		expect(
			await sheet.findByText(
				'jentic register --url https://jentic.example.test --broker-url https://broker.jentic.example.test',
			),
		).toBeInTheDocument();
		expect(sheet.queryByText(/Ask your operator/)).not.toBeInTheDocument();
		expect(sheet.getByText('Broker URL')).toBeInTheDocument();
		expect(sheet.getByText('https://broker.jentic.example.test')).toBeInTheDocument();
	});

	// --- The sessions card -----------------------------------------------------

	it('lists MCP sessions with client / transport / started — never "connected"', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		// Client name + version from the event's data; a version-less client and a
		// clientInfo-less one degrade honestly (SHOULD in the MCP spec).
		expect(await sheet.findByText('claude-desktop 1.5.2')).toBeInTheDocument();
		expect(sheet.getByText('cursor')).toBeInTheDocument();
		expect(sheet.getByText('unknown client')).toBeInTheDocument();
		// Transport renders verbatim from the emitter.
		expect(sheet.getAllByText('stdio')).toHaveLength(3);
		// "started / last active" is the vocabulary — last active reads off the
		// newest MCP-origin execution.
		expect(sheet.getByText('started / last active')).toBeInTheDocument();
		expect(await sheet.findByText(/Last active/)).toBeInTheDocument();
		// NEVER "connected": stdio liveness is unknowable server-side.
		expect(sheet.queryByText(/connected/i)).not.toBeInTheDocument();
	});

	it('shows a quiet permission note when the events read is gated (403)', async () => {
		worker.use(createErrorHandler('get', '/events', { status: 403 }));
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		expect(
			await sheet.findByText('MCP session history requires event-read permissions.'),
		).toBeInTheDocument();
		// A permission gate is not an error — the config card still renders.
		expect(sheet.queryByRole('alert')).not.toBeInTheDocument();
		expect(sheet.getByText('Connect via MCP')).toBeInTheDocument();
	});

	it('shows an error state — not a false empty state — when the sessions read fails (500)', async () => {
		worker.use(createErrorHandler('get', '/events', { status: 500 }));
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		// A real failure surfaces as an error, never as "No MCP sessions recorded"
		// (which would tell the operator the transport is unused).
		expect(await sheet.findByText('Failed to load MCP sessions.')).toBeInTheDocument();
		expect(sheet.getByRole('alert')).toBeInTheDocument();
		expect(sheet.queryByText(/No MCP sessions recorded/)).not.toBeInTheDocument();
		expect(sheet.getByText('Connect via MCP')).toBeInTheDocument();
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
