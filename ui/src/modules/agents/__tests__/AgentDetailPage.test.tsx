import { describe, it, expect, beforeEach } from 'vitest';
import { Routes, Route } from 'react-router';
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
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { SHOW_HTTP_VARIANT } from '@/modules/agents/components/detail/McpPanel';
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

	it('renders identity and status once — no duplicated title card', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		// The PageHeader IS the identity surface (toolkit-console grammar): the
		// name renders exactly once, as the page heading.
		expect(await screen.findByRole('heading', { name: 'support-agent' })).toBeInTheDocument();
		expect(screen.getAllByText('support-agent')).toHaveLength(1);
		// Pin the status to the header's badge — a bare text query would also
		// match e.g. a toolkit "Active" pill and mask a wrong-status bug.
		expect(screen.getByTestId('detail-status-badge')).toHaveTextContent('Active');
		expect(screen.getByText('Registered')).toBeInTheDocument();
		// The raw id lives on the Settings tab (like the toolkit id), not in the
		// page chrome.
		expect(screen.queryByText('agnt_active_1')).not.toBeInTheDocument();
		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		expect(await screen.findByText('Agent ID')).toBeInTheDocument();
		expect(screen.getByText('agnt_active_1')).toBeInTheDocument();
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

	it('shows the actor-scoped "Recent changes" audit slice on Overview', async () => {
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });
		// Same audit grammar as the toolkit console: lifecycle events recorded
		// against this agent as the target, newest first.
		expect(await screen.findByText('Recent changes')).toBeInTheDocument();
		expect(await screen.findByText('rotate')).toBeInTheDocument();
		expect(screen.getByText('approve')).toBeInTheDocument();
		expect(screen.getByText('register')).toBeInTheDocument();
		// The acting user resolves through the actor directory (ActorLabel) —
		// a raw usr_… id in the feed means the directory wiring regressed.
		expect(await screen.findAllByText(/Admin User/)).not.toHaveLength(0);
		expect(screen.queryByText('usr_000000000000000000000admin')).not.toBeInTheDocument();
	});

	it('shows the pending access requests this agent has filed (#619)', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });
		// The permission story lives on the Access tab.
		await user.click(screen.getByRole('tab', { name: 'Access' }));
		expect(await screen.findByRole('heading', { name: 'Access requests' })).toBeInTheDocument();
		expect(await screen.findByText(/toolkit · use \+2 more/)).toBeInTheDocument();
	});

	it('shows an honest empty state when no toolkits are bound', async () => {
		renderDetail('agnt_pending_1');
		await screen.findByRole('heading', { name: 'inbox-triage-bot' });
		// A pending agent additionally explains the approval gate (no bind CTA).
		expect(
			await screen.findByText(/No toolkits bound\. Approve this agent first/),
		).toBeInTheDocument();
	});

	it('gates toolkit binding to active agents (no Bind button while pending)', async () => {
		renderDetail('agnt_pending_1');
		await screen.findByRole('heading', { name: 'inbox-triage-bot' });
		// The approval queue is where a human vouches for an agent — a pending
		// one must not accumulate capabilities beforehand.
		expect(screen.queryByRole('button', { name: 'Bind toolkit' })).not.toBeInTheDocument();
	});

	it('renders a not-found surface for an unknown id', async () => {
		renderDetail('agnt_does_not_exist');
		expect(await screen.findByText('Agent not found')).toBeInTheDocument();
	});

	// --- Phase 3: identity console (KPI strip + tabs) ----------------------

	it('renders the KPI strip from the per-actor usage aggregate', async () => {
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		// The agents-module usage fixture sums to 1,204 executions / 99.0%.
		const strip = await screen.findByRole('group', { name: 'Key metrics' });
		await waitFor(() => expect(within(strip).getByText('1,204')).toBeInTheDocument());
		expect(within(strip).getByText('99%')).toBeInTheDocument();
		expect(within(strip).getByText('Bound toolkits')).toBeInTheDocument();
	});

	it('requests the usage window with a next-minute-ceiled until bound (#913)', async () => {
		// The aggregate filters `started_at < until` strictly, so the request
		// must carry an explicit `until` PAST "now" — a floored (or absent →
		// server-floored) bound excludes the current partial minute, making
		// fresh executions visible in the Activity feed but missing from the
		// volume chart / 7-day KPI until the minute rolls over.
		let captured: URLSearchParams | null = null;
		worker.use(
			http.get('/monitoring/usage', ({ request }) => {
				const url = new URL(request.url);
				if (url.searchParams.get('agent_id') !== 'agnt_active_1') return undefined;
				captured = url.searchParams;
				return undefined; // fall through to the module's fixture handler
			}),
		);
		const beforeSec = Math.floor(Date.now() / 1000);
		renderDetail('agnt_active_1');
		await screen.findByRole('group', { name: 'Key metrics' });

		await waitFor(() => expect(captured).not.toBeNull());
		const params: URLSearchParams = captured!;
		const since = Number(params.get('since'));
		const until = Number(params.get('until'));
		expect(until % 60).toBe(0);
		expect(until).toBeGreaterThan(beforeSec);
		// Exact 7-day width: the backend derives its bucket tier from
		// `until - since`, so the window must not stretch past the tier edge.
		expect(until - since).toBe(7 * 86_400);
	});

	it('hides the KPI strip when monitoring is admin-gated (403)', async () => {
		worker.use(
			createErrorHandler('get', '/monitoring/usage', { status: 403 }),
			createErrorHandler('get', '/executions', { status: 403 }),
		);
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		// The usage query resolves to the 403 sentinel and the strip unmounts
		// entirely (same contract as the toolkit console's UsageStrip) — a
		// permission gate is not an error, so no alert either.
		await waitFor(() =>
			expect(screen.queryByTestId('kpi-strip-loading')).not.toBeInTheDocument(),
		);
		expect(screen.queryByRole('group', { name: 'Key metrics' })).not.toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	it('shows the per-agent executions feed on the Activity tab with a Monitor deep-link', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'Activity' }));

		// The feed lists this agent's executions only (agents-module fixture).
		expect(await screen.findByText('github.create_issue')).toBeInTheDocument();
		expect(screen.getByText(/pbac_denied/)).toBeInTheDocument();
		expect(screen.getByText('Execution volume · 7d')).toBeInTheDocument();

		// The deep-links carry the actor filter into Monitor's URL contract —
		// exactly two: the back-row link and the feed-card link.
		const links = screen.getAllByRole('link', { name: /Open Monitor/ });
		expect(links).toHaveLength(2);
		for (const link of links) {
			expect(link.getAttribute('href')).toContain('tab=executions');
			expect(link.getAttribute('href')).toContain('actor_id=agnt_active_1');
			expect(link.getAttribute('href')).toContain('actor_type=agent');
		}
	});

	it('shows a quiet permission note on the Activity tab for non-admins (403)', async () => {
		const user = userEvent.setup();
		worker.use(
			createErrorHandler('get', '/monitoring/usage', { status: 403 }),
			createErrorHandler('get', '/executions', { status: 403 }),
		);
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'Activity' }));

		expect(await screen.findByText('Activity requires elevated access')).toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	it('hosts the API key lifecycle on the Keys tab', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'Keys' }));

		// No key issued yet → honest empty copy + the generate action.
		expect(
			await screen.findByText('No API key has been issued for this agent yet.'),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole('button', { name: 'Generate API key for support-agent' }),
		);

		// Plaintext shows exactly once via the ApiKeyDialog.
		expect(
			await screen.findByRole('dialog', { name: 'API key generated' }),
		).toBeInTheDocument();
	});

	it('confirms before regenerating an existing API key', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });
		await user.click(screen.getByRole('tab', { name: 'Keys' }));

		// First issue destroys nothing → generates directly (no confirm step).
		await user.click(
			await screen.findByRole('button', { name: 'Generate API key for support-agent' }),
		);
		const reveal = await screen.findByRole('dialog', { name: 'API key generated' });
		await user.click(within(reveal).getByRole('button', { name: 'Done' }));

		// Now a key exists → the action reads Regenerate and confirms first,
		// because rotating invalidates the current key immediately.
		await user.click(
			await screen.findByRole('button', {
				name: 'Regenerate API key for support-agent',
			}),
		);
		const confirm = await screen.findByRole('dialog', {
			name: 'Regenerate API key for support-agent',
		});
		expect(
			within(confirm).getByText(/current API key stops working immediately/),
		).toBeInTheDocument();
		await user.click(within(confirm).getByRole('button', { name: 'Regenerate' }));

		expect(
			await screen.findByRole('dialog', { name: 'API key generated' }),
		).toBeInTheDocument();
	});

	it('gates lifecycle actions by status (pending → approve / deny in header)', async () => {
		renderDetail('agnt_pending_1');
		await screen.findByRole('heading', { name: 'inbox-triage-bot' });
		expect(
			screen.getByRole('button', { name: 'Approve inbox-triage-bot' }),
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Deny inbox-triage-bot' })).toBeInTheDocument();
		// Destructive actions (Archive / Disable) moved to the Settings tab's
		// danger zone / header kill switch — a pending agent's header only
		// offers constructive verbs.
		expect(
			screen.queryByRole('button', { name: 'Archive inbox-triage-bot' }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'Disable inbox-triage-bot' }),
		).not.toBeInTheDocument();
	});

	it('keeps the deny dialog open and toasts when the deny fails', async () => {
		const user = userEvent.setup();
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
			expect(screen.getByTestId('detail-status-badge')).toHaveTextContent('Active');
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

	// --- Phase 4: Settings tab (PATCH /agents/:id + danger zone) -----------

	it('renames an agent from the Settings tab, sending only the dirty field', async () => {
		const user = userEvent.setup();
		// Spy on the PATCH body to pin real PATCH semantics (only changed keys),
		// then fall through to the module's stateful mock — the hook invalidates
		// the detail cache, so the refetch must serve the renamed row.
		let patchBody: Record<string, unknown> | null = null;
		worker.use(
			http.patch('/agents/:id', async ({ request }) => {
				patchBody = (await request.clone().json()) as Record<string, unknown>;
				return undefined;
			}),
		);
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		const nameInput = await screen.findByLabelText('Name');

		// Save is disabled until something changes.
		const save = screen.getByRole('button', { name: 'Save changes' });
		expect(save).toBeDisabled();

		await user.clear(nameInput);
		await user.type(nameInput, 'support-agent-v2');
		expect(save).toBeEnabled();
		await user.click(save);

		expect(await screen.findByText('Agent updated')).toBeInTheDocument();
		// Only the renamed field crossed the wire — no description/owner echo.
		expect(patchBody).toEqual({ name: 'support-agent-v2' });
		// The detail cache invalidates and refetches → header updates.
		expect(
			await screen.findByRole('heading', { name: 'support-agent-v2' }),
		).toBeInTheDocument();
	});

	it('clears the description by sending an explicit null', async () => {
		const user = userEvent.setup();
		let patchBody: Record<string, unknown> | null = null;
		worker.use(
			http.patch('/agents/:id', async ({ request }) => {
				patchBody = (await request.clone().json()) as Record<string, unknown>;
				return undefined;
			}),
		);
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		const descInput = await screen.findByLabelText('Description');
		await user.clear(descInput);
		await user.click(screen.getByRole('button', { name: 'Save changes' }));

		expect(await screen.findByText('Agent updated')).toBeInTheDocument();
		expect(patchBody).toEqual({ description: null });
	});

	it('does not expose an owner editor — ownership is not routine metadata', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		await screen.findByLabelText('Name');
		// Reassigning the accountable human is an administrative act; the
		// Settings form deliberately only edits name + description.
		expect(screen.queryByLabelText('Owner')).not.toBeInTheDocument();
	});

	it('disables an active agent through the header kill switch and offers restore', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		// The pill mirrors the live status and arms an inline confirm — no
		// mutation on the first click.
		const pill = screen.getByRole('button', { name: 'Disable support-agent (kill switch)' });
		expect(pill).toHaveTextContent('Active');
		await user.click(pill);
		expect(screen.getByText('Block this agent?')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Disable' }));
		expect(await screen.findByText('Agent disabled')).toBeInTheDocument();

		// Detail cache invalidates → the pill flips to the danger state and
		// now offers the restore flow.
		await waitFor(() => {
			expect(screen.getByTestId('detail-status-badge')).toHaveTextContent('Disabled');
		});
		expect(
			await screen.findByRole('button', { name: 'Enable support-agent' }),
		).toBeInTheDocument();
	});

	it('keeps the draft and toasts when the PATCH fails', async () => {
		const user = userEvent.setup();
		worker.use(createErrorHandler('patch', '/agents/:id', { status: 500 }));

		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		const nameInput = await screen.findByLabelText('Name');
		await user.clear(nameInput);
		await user.type(nameInput, 'renamed-agent');
		await user.click(screen.getByRole('button', { name: 'Save changes' }));

		expect(await screen.findByText('Failed to update the agent.')).toBeInTheDocument();
		// Draft survives so the user can retry without retyping.
		expect(nameInput).toHaveValue('renamed-agent');
	});

	it('hosts only the terminal Archive in the Settings danger zone for an active agent', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		// The reversible Disable lives in the header kill switch, not the
		// danger zone — no plain "Disable support-agent" button anywhere.
		expect(
			screen.queryByRole('button', { name: 'Disable support-agent' }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'Archive support-agent' }),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		expect(await screen.findByText('Danger zone')).toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'Disable support-agent' }),
		).not.toBeInTheDocument();

		// Archive routes through the cascade-delete confirmation dialog.
		await user.click(screen.getByRole('button', { name: 'Archive support-agent' }));
		const dialog = await screen.findByRole('dialog');
		expect(within(dialog).getByText(/support-agent/)).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });
		await checkA11y(container);
	});

	// --- local-MCP 2-E2 (#1188): MCP tab — config card + sessions ----------

	it('shows the per-agent MCP config card with the pinned --context snippet', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'MCP' }));
		expect(await screen.findByText('Connect via MCP')).toBeInTheDocument();

		// The snippet is the CONTEXT variant, pinned per §3.10's
		// one-agent-per-runtime model — never a bare `jentic mcp`.
		expect(screen.getByText('jentic mcp --context support-agent')).toBeInTheDocument();
		// JSON client-config variant carries the same pinned args.
		expect(screen.getByText(/"mcp", "--context", "support-agent"/)).toBeInTheDocument();

		// Prerequisites: CLI + register against THIS instance on the AGENT
		// machine (the stdio config encodes no base URL), or `jentic setup`.
		// The instance URL resolves async from GET /instance, so wait for it.
		expect(
			await screen.findByText('jentic register --url "https://jentic.example.test"'),
		).toBeInTheDocument();
		expect(screen.getByText('agent machine')).toBeInTheDocument();
		expect(screen.getByText('jentic setup')).toBeInTheDocument();

		// Instance identity from GET /instance (which instance the snippet
		// registers against).
		expect(screen.getByText('jentic.example.test')).toBeInTheDocument();
		expect(screen.getByText('local')).toBeInTheDocument();
	});

	it('falls back to the browser origin when no canonical base URL is configured', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/instance', () =>
				HttpResponse.json({
					backend: 'local',
					canonical_base_url: '',
					host: '',
					instance_id: null,
				}),
			),
		);
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'MCP' }));
		await screen.findByText('Connect via MCP');

		// The operator is looking at a working address of this instance, so the
		// register command targets the browser's origin.
		expect(
			await screen.findByText(`jentic register --url "${window.location.origin}"`),
		).toBeInTheDocument();
	});

	it('unconditionally hides the HTTP variant in phase 2 (test-pinned)', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'MCP' }));
		await screen.findByText('Connect via MCP');

		// The pin itself: `server.mcp` doesn't exist until phase 3 — flipping
		// this constant before the backend capability lands would advertise a
		// transport that 404s. Un-hiding is a deliberate phase-3 follow-up.
		expect(SHOW_HTTP_VARIANT).toBe(false);
		expect(screen.queryByText(/Streamable HTTP/i)).not.toBeInTheDocument();
		// No URL-based server entry is offered anywhere on the card.
		expect(screen.queryByText(/"url"/)).not.toBeInTheDocument();
	});

	it('lists MCP sessions with client / transport / started — never "connected"', async () => {
		const user = userEvent.setup();
		const { container } = renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'MCP' }));

		// Client name + version from the event's data; a version-less client
		// and a clientInfo-less one degrade honestly (SHOULD in the MCP spec).
		expect(await screen.findByText('claude-desktop 1.5.2')).toBeInTheDocument();
		expect(screen.getByText('cursor')).toBeInTheDocument();
		expect(screen.getByText('unknown client')).toBeInTheDocument();
		// Transport renders verbatim from the emitter.
		expect(screen.getAllByText('stdio')).toHaveLength(3);

		// "started / last active" is the vocabulary — last active reads off the
		// newest MCP-origin execution (5 min ago in the fixture).
		expect(screen.getByText('started / last active')).toBeInTheDocument();
		expect(await screen.findByText(/Last active/)).toBeInTheDocument();
		// NEVER "connected": stdio liveness is unknowable server-side.
		expect(screen.queryByText(/connected/i)).not.toBeInTheDocument();

		await checkA11y(container);
	});

	it('shows an honest empty state for an agent with no MCP sessions', async () => {
		const user = userEvent.setup();
		renderDetail('agnt_pending_1');
		await screen.findByRole('heading', { name: 'inbox-triage-bot' });

		await user.click(screen.getByRole('tab', { name: 'MCP' }));
		expect(
			await screen.findByText(/No MCP sessions recorded for this agent yet/),
		).toBeInTheDocument();
	});

	it('shows a quiet permission note when the events read is gated (403)', async () => {
		const user = userEvent.setup();
		worker.use(createErrorHandler('get', '/events', { status: 403 }));
		renderDetail('agnt_active_1');
		await screen.findByRole('heading', { name: 'support-agent' });

		await user.click(screen.getByRole('tab', { name: 'MCP' }));
		expect(
			await screen.findByText('MCP session history requires event-read permissions.'),
		).toBeInTheDocument();
		// A permission gate is not an error — the config card still renders.
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
		expect(screen.getByText('Connect via MCP')).toBeInTheDocument();
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
