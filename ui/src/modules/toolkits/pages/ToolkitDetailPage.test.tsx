import { describe, expect, it } from 'vitest';
import {
	checkA11y,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	within,
} from '@/__tests__/test-utils';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { ToolkitDetailPage } from '@/modules/toolkits/pages/ToolkitDetailPage';

const ROUTE = '/toolkits/tk_demo_github';
const PATH = '/toolkits/:toolkitId';

/**
 * Override the org-wide `GET /credentials` surface for bind-picker tests. The
 * toolkits module reads this through the shared API; the Credentials module's
 * own mock store starts empty, so we stub a small fixture here (staying within
 * the toolkits module — no sibling-module import).
 */
function seedCredentials(
	creds: Array<{ credential_id: string; name: string; type: string; vendor: string }>,
) {
	worker.use(
		http.get('/credentials', () =>
			HttpResponse.json({
				data: creds.map((c) => ({
					credential_id: c.credential_id,
					name: c.name,
					type: c.type,
					provider: 'manual',
					active: true,
					api: { vendor: c.vendor, name: 'default', version: '1.0.0' },
					created_at: '2026-05-01T10:00:00Z',
					updated_at: null,
				})),
				has_more: false,
				next_cursor: null,
			}),
		),
	);
}

type SeedAgent = { agent_id: string; agent_name: string; status: string };

/**
 * Per-test, isolated agent fixtures. The default module handlers carry a shared
 * `agentsByToolkit` store that link/unlink mutate; overriding it here with a
 * fresh in-closure store keeps each agents test independent of execution order.
 *
 * `bound` seeds the reverse lookup (`GET /toolkits/:id/agents`); `workspace`
 * seeds the link-picker candidates (`GET /agents`). Link/unlink mutate the
 * local `bound` array so the UI reflects changes after a mutation.
 */
function seedAgents(opts: { bound: SeedAgent[]; workspace: SeedAgent[] }) {
	const bound = opts.bound.map((a) => ({ ...a, bound_at: '2026-05-02T09:00:00Z' }));
	worker.use(
		http.get('/toolkits/:toolkitId/agents', () =>
			HttpResponse.json({ data: bound, has_more: false, next_cursor: null }),
		),
		http.get('/agents', () =>
			HttpResponse.json({
				data: opts.workspace.map((a) => ({
					id: a.agent_id,
					name: a.agent_name,
					status: a.status,
					registered_by: 'admin@local',
					created_at: '2026-04-01T09:00:00Z',
				})),
				has_more: false,
				next_cursor: null,
			}),
		),
		http.post('/agents/:agentId/toolkits', async ({ params, request }) => {
			const agentId = params.agentId as string;
			const body = (await request.json()) as { toolkit_id: string };
			const match = opts.workspace.find((a) => a.agent_id === agentId);
			if (match && !bound.some((a) => a.agent_id === agentId)) {
				bound.push({ ...match, bound_at: new Date().toISOString() });
			}
			return HttpResponse.json({
				agent_id: agentId,
				toolkit_id: body.toolkit_id,
				bound_at: new Date().toISOString(),
			});
		}),
		http.delete('/agents/:agentId/toolkits/:toolkitId', ({ params }) => {
			const agentId = params.agentId as string;
			const idx = bound.findIndex((a) => a.agent_id === agentId);
			if (idx >= 0) bound.splice(idx, 1);
			return new HttpResponse(null, { status: 204 });
		}),
	);
}

describe('ToolkitDetailPage', () => {
	it('renders the identity chrome and tabbed sections (keys, credentials via tabs)', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });

		expect(await screen.findByRole('heading', { name: 'GitHub Tools' })).toBeInTheDocument();
		expect(screen.getByText('tk_demo_github')).toBeInTheDocument();

		// Overview is the landing tab: audit slice renders without a click.
		expect(await screen.findByRole('heading', { name: /recent changes/i })).toBeInTheDocument();
		expect(await screen.findByText(/suspended pending review/i)).toBeInTheDocument();

		// Keys tab lists the seeded key.
		await user.click(screen.getByRole('tab', { name: 'Keys' }));
		expect(await screen.findByText('CI runner')).toBeInTheDocument();

		// Access tab lists the bound credential.
		await user.click(screen.getByRole('tab', { name: 'Access' }));
		expect(await screen.findByText('GitHub PAT')).toBeInTheDocument();
	});

	it('deep-links a tab through the ?tab= search param', async () => {
		renderWithProviders(<ToolkitDetailPage />, { route: `${ROUTE}?tab=keys`, path: PATH });

		await screen.findByRole('heading', { name: 'GitHub Tools' });
		// The Keys panel content renders without any click…
		expect(await screen.findByText('CI runner')).toBeInTheDocument();
		// …and the Keys tab is the selected one.
		expect(screen.getByRole('tab', { name: 'Keys' })).toHaveAttribute('aria-selected', 'true');
	});

	it('shows the 7-day KPI strip from the toolkit-scoped usage aggregation', async () => {
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });

		const strip = await screen.findByTestId('usage-strip');
		// Sum of the seeded daily trend (64+88+71+120+104+141+126).
		expect(within(strip).getByText('714')).toBeInTheDocument();
		expect(within(strip).getByText(/executions · 7d/i)).toBeInTheDocument();
		expect(within(strip).getByText('97.6%')).toBeInTheDocument();
		expect(within(strip).getByText('412ms')).toBeInTheDocument();
		// Agents / creds / keys roll-up: 1 bound agent, 1 credential, 2 keys.
		expect(within(strip).getByText('1 / 1 / 2')).toBeInTheDocument();
	});

	it('renders the Activity tab: volume chart, executions feed, Monitor deep-link', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(screen.getByRole('tab', { name: 'Activity' }));

		// Volume chart (from /monitoring/usage?toolkit_id=…).
		expect(
			await screen.findByRole('img', { name: /execution volume for this toolkit/i }),
		).toBeInTheDocument();

		// Executions feed (from /executions?toolkit_id=…), including the denial.
		expect(await screen.findByText(/github\.create_issue/)).toBeInTheDocument();
		expect(screen.getByText(/denied by permission rule/i)).toBeInTheDocument();

		// Deep-link into Monitor carries the toolkit filter.
		const link = screen.getByRole('link', { name: /open in monitor/i });
		expect(link).toHaveAttribute(
			'href',
			expect.stringContaining('tab=executions&toolkit_id=tk_demo_github'),
		);
	});

	it('hides the KPI strip and degrades the Activity tab for non-admins (403)', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/monitoring/usage', () => new HttpResponse(null, { status: 403 })),
			http.get('/executions', () => new HttpResponse(null, { status: 403 })),
		);
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		// Strip resolves to hidden (no error splash).
		await waitFor(() =>
			expect(screen.queryByTestId('usage-strip-loading')).not.toBeInTheDocument(),
		);
		expect(screen.queryByTestId('usage-strip')).not.toBeInTheDocument();

		// Activity tab explains the gate instead of erroring.
		await user.click(screen.getByRole('tab', { name: 'Activity' }));
		expect(await screen.findByText(/admin-only/i)).toBeInTheDocument();
	});

	it('reuses the shared PageHeader pattern like /agents/:id', async () => {
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });

		// The toolkit name is the single page heading (PageHeader <h1>); the body
		// identity block repeats it as a non-heading <span>, mirroring the agent
		// detail page, so there is exactly one heading-role match for the name.
		await waitFor(() =>
			expect(screen.getAllByRole('heading', { name: 'GitHub Tools' })).toHaveLength(1),
		);
		// "Back to <parent>" affordance sits beneath the header (not baked in).
		expect(screen.getByRole('button', { name: /all toolkits/i })).toBeInTheDocument();
	});

	it('saves identity edits from the Settings tab form', async () => {
		let patched: { name?: string | null } | null = null;
		worker.use(
			http.patch('/toolkits/:toolkitId', async ({ params, request }) => {
				patched = (await request.json()) as { name?: string | null };
				return HttpResponse.json({
					toolkit_id: params.toolkitId,
					name: patched?.name ?? 'GitHub Tools',
					description: null,
					active: true,
					key_count: 1,
					credential_count: 1,
					created_at: '2026-04-01T09:00:00Z',
				});
			}),
		);

		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		const nameInput = await screen.findByLabelText('Name');

		// Save is a no-op until the draft actually diverges from the toolkit.
		const save = screen.getByRole('button', { name: /save changes/i });
		expect(save).toBeDisabled();

		await user.clear(nameInput);
		await user.type(nameInput, 'GitHub Ops');
		await waitFor(() => expect(save).toBeEnabled());

		await user.click(save);
		await waitFor(() => expect(patched).toMatchObject({ name: 'GitHub Ops' }));
	});

	it('has no critical accessibility violations', async () => {
		const { container } = renderWithProviders(<ToolkitDetailPage />, {
			route: ROUTE,
			path: PATH,
		});
		await screen.findByRole('heading', { name: 'GitHub Tools' });
		// Landing tab: wait for the audit slice + bound-agent rows to mount, then
		// let the framer-motion entrance fully settle so axe samples final
		// (opaque) colours rather than mid-fade blended ones.
		await screen.findByText(/suspended pending review/i);
		await new Promise((resolve) => setTimeout(resolve, 600));
		await checkA11y(container);
	});

	it('reveals the one-time plaintext key after creating a key', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(screen.getByRole('tab', { name: 'Keys' }));
		await user.click(await screen.findByRole('button', { name: /create key/i }));
		await user.click(screen.getByRole('button', { name: /^generate$/i }));

		expect(await screen.findByText('New API Key Created')).toBeInTheDocument();
		expect(screen.getByText(/jntc_live_freshmockplaintext/)).toBeInTheDocument();
	});

	it('renders full-width (no reading max-width cap)', async () => {
		const { container } = renderWithProviders(<ToolkitDetailPage />, {
			route: ROUTE,
			path: PATH,
		});
		await screen.findByRole('heading', { name: 'GitHub Tools' });
		// PageShell width="wide" drops the `max-w-4xl` reading cap so the
		// detail page spans the full page width, matching the other list/detail
		// surfaces.
		const shell = container.querySelector('.px-page-gutter');
		expect(shell).not.toBeNull();
		expect(shell).not.toHaveClass('max-w-4xl');
	});

	it('binds a credential picked from the searchable list', async () => {
		seedCredentials([
			{ credential_id: 'cred_stripe', name: 'Stripe key', type: 'api_key', vendor: 'stripe' },
			{ credential_id: 'cred_gh_1', name: 'GitHub PAT', type: 'api_key', vendor: 'github' },
		]);
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(screen.getByRole('tab', { name: 'Access' }));
		await user.click(await screen.findByRole('button', { name: /^bind api$/i }));

		// Picker lists the unbound credential…
		const stripeRow = await screen.findByText('Stripe key');
		expect(stripeRow).toBeInTheDocument();
		// …and hides the one already bound to this toolkit (cred_gh_1, "GitHub PAT").
		const dialog = screen.getByRole('dialog');
		expect(within(dialog).queryByText('GitHub PAT')).not.toBeInTheDocument();

		await user.click(stripeRow);
		// Bind succeeds → the dialog closes (onSuccess → setBindOpen(false)).
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

		// The fresh bind lands with zero rules — the backend's warnings[] renders
		// verbatim on the new binding row.
		expect(await screen.findByTestId('binding-warning')).toHaveTextContent(
			/no permission rules/i,
		);
	});

	it('dry-runs a request against the saved rules with the rule tester', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: `${ROUTE}?tab=access`, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		// Open the permissions editor for the seeded GitHub binding — the tester
		// lives next to the rule editor. (Scoped to the row: a previous test may
		// have bound extra credentials to the shared mock store.)
		const rows = await screen.findAllByTestId('binding-row');
		const githubRow = rows.find((r) => within(r).queryByText('GitHub PAT'));
		expect(githubRow).toBeDefined();
		await user.click(
			within(githubRow as HTMLElement).getByRole('button', { name: /permissions/i }),
		);
		await screen.findByText(/test a request/i);

		// GET /repos/… matches the seeded allow rule → Allowed, rule #1.
		await user.type(screen.getByLabelText('Request path'), '/repos/acme/site');
		await user.click(screen.getByRole('button', { name: /^test$/i }));
		expect(await screen.findByTestId('rule-verdict')).toHaveTextContent(
			/allowed — matched rule #1/i,
		);

		// /admin/… trips the system safety deny → Denied, system-tagged.
		await user.clear(screen.getByLabelText('Request path'));
		await user.type(screen.getByLabelText('Request path'), '/admin/users');
		await user.click(screen.getByRole('button', { name: /^test$/i }));
		await waitFor(() =>
			expect(screen.getByTestId('rule-verdict')).toHaveTextContent(
				/denied — matched rule #2 \(system safety\)/i,
			),
		);

		// A path nothing matches → the broker's default deny.
		await user.clear(screen.getByLabelText('Request path'));
		await user.type(screen.getByLabelText('Request path'), '/unmapped');
		await user.click(screen.getByRole('button', { name: /^test$/i }));
		await waitFor(() =>
			expect(screen.getByTestId('rule-verdict')).toHaveTextContent(
				/denied — no rule matched/i,
			),
		);
	});

	it('filters the credential picker by the search term', async () => {
		seedCredentials([
			{ credential_id: 'cred_aws', name: 'AWS key', type: 'api_key', vendor: 'aws' },
			{ credential_id: 'cred_slack', name: 'Slack token', type: 'oauth2', vendor: 'slack' },
		]);
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(screen.getByRole('tab', { name: 'Access' }));
		await user.click(await screen.findByRole('button', { name: /^bind api$/i }));
		await screen.findByText('AWS key');

		await user.type(screen.getByLabelText('Filter credentials'), 'slack');
		await waitFor(() => expect(screen.queryByText('AWS key')).not.toBeInTheDocument());
		expect(screen.getByText('Slack token')).toBeInTheDocument();
	});

	it('lists the agents bound to the toolkit', async () => {
		seedAgents({
			bound: [{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' }],
			workspace: [
				{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' },
				{ agent_id: 'agt_billing_bot', agent_name: 'Billing Bot', status: 'active' },
			],
		});
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		// Bound Agents section header reflects the seeded count.
		expect(await screen.findByText(/Bound Agents \(1\)/)).toBeInTheDocument();
		// The seeded bound agent shows up as a row. The id shares a <p> with an
		// optional "· linked …" suffix, so scope the (substring) match to the row
		// to keep it unambiguous rather than matching across the whole document.
		expect(await screen.findByText('Support Bot')).toBeInTheDocument();
		const row = screen.getByTestId('bound-agent-row');
		expect(within(row).getByText(/agt_support_bot/)).toBeInTheDocument();
		// Status renders through the shared ActorStatusBadge → capitalized label
		// (parity with the /agents page), not the raw lowercase wire value.
		expect(within(row).getByText('Active')).toBeInTheDocument();
		expect(within(row).queryByText('active')).not.toBeInTheDocument();
	});

	it('disables the agent filter and shows only the real empty state when no agents are linkable', async () => {
		// Every workspace agent is already bound → no candidates for the picker.
		seedAgents({
			bound: [{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' }],
			workspace: [
				{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' },
			],
		});
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });
		await screen.findByText('Support Bot');

		await user.click(screen.getByRole('button', { name: /link agent/i }));

		// "All agents linked" empty state, and the filter is disabled so a user
		// can't type to stack a second "No matches" empty state on top of it.
		expect(await screen.findByText(/all agents linked/i)).toBeInTheDocument();
		expect(screen.getByLabelText('Filter agents')).toBeDisabled();
		expect(screen.queryByText(/no matches/i)).not.toBeInTheDocument();
	});

	it('links an agent picked from the searchable list and hides already-linked agents', async () => {
		seedAgents({
			bound: [{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' }],
			workspace: [
				{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' },
				{ agent_id: 'agt_billing_bot', agent_name: 'Billing Bot', status: 'active' },
			],
		});
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });
		await screen.findByText('Support Bot');

		await user.click(screen.getByRole('button', { name: /link agent/i }));

		// Picker lists an unlinked agent…
		const billingRow = await screen.findByText('Billing Bot');
		expect(billingRow).toBeInTheDocument();
		// …and hides the agent already bound to this toolkit (Support Bot).
		const dialog = screen.getByRole('dialog');
		expect(within(dialog).queryByText('Support Bot')).not.toBeInTheDocument();

		await user.click(billingRow);
		// Link succeeds → dialog closes (onSuccess → setLinkAgentOpen(false)).
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		// The newly linked agent now appears in the Bound Agents list.
		expect(await screen.findByText('Billing Bot')).toBeInTheDocument();
	});

	it('filters the agent picker by the search term', async () => {
		seedAgents({
			bound: [{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' }],
			workspace: [
				{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' },
				{ agent_id: 'agt_billing_bot', agent_name: 'Billing Bot', status: 'active' },
				{ agent_id: 'agt_pending_bot', agent_name: 'Pending Bot', status: 'pending' },
			],
		});
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });
		await screen.findByText('Support Bot');

		await user.click(screen.getByRole('button', { name: /link agent/i }));
		await screen.findByText('Billing Bot');

		await user.type(screen.getByLabelText('Filter agents'), 'pending');
		await waitFor(() => expect(screen.queryByText('Billing Bot')).not.toBeInTheDocument());
		expect(screen.getByText('Pending Bot')).toBeInTheDocument();
	});

	it('unlinks a bound agent', async () => {
		seedAgents({
			bound: [{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' }],
			workspace: [
				{ agent_id: 'agt_support_bot', agent_name: 'Support Bot', status: 'active' },
			],
		});
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		// Grab the Support Bot row and unlink it (InlineConfirm → confirm).
		await screen.findByText('Support Bot');
		const rows = screen.getAllByTestId('bound-agent-row');
		const supportRow = rows.find((r) => within(r).queryByText('Support Bot'));
		const unlinkButton = within(supportRow as HTMLElement).getByRole('button', {
			name: /unlink/i,
		});
		await user.click(unlinkButton);
		await user.click(await screen.findByRole('button', { name: /^unlink$/i }));

		await waitFor(() => expect(screen.queryByText('Support Bot')).not.toBeInTheDocument());
	});

	it('shows a not-found state for an unknown toolkit', async () => {
		renderWithProviders(<ToolkitDetailPage />, {
			route: '/toolkits/tk_missing',
			path: PATH,
		});
		await waitFor(() => expect(screen.getByText('Toolkit not found')).toBeInTheDocument());
	});

	it('hard-deletes the toolkit through the cascade dialog with a populated blast radius', async () => {
		// Pin keys / bindings / agents to a small known set so the blast-radius
		// counts are deterministic regardless of execution order (the shared
		// MSW store accumulates across tests in this file when create/bind
		// handlers run earlier).
		let deleted: string | null = null;
		worker.use(
			http.get('/toolkits/:toolkitId/keys', () =>
				HttpResponse.json({
					data: [
						{
							key_id: 'key_1',
							toolkit_id: 'tk_demo_github',
							label: 'CI runner',
							key_preview: 'jntc_live_ab12…',
							revoked: false,
							allowed_ips: null,
							last_used_at: null,
							created_at: '2026-05-01T10:05:00Z',
						},
					],
					has_more: false,
				}),
			),
			http.get('/toolkits/:toolkitId/credentials', () =>
				HttpResponse.json({
					data: [
						{
							toolkit_id: 'tk_demo_github',
							credential_id: 'cred_gh_1',
							label: 'GitHub PAT',
							api_name: 'GitHub',
							api_vendor: 'github',
							credential_type: 'api_key',
							bound_at: '2026-05-01T10:10:00Z',
							permissions: [],
						},
					],
					has_more: false,
				}),
			),
			http.get('/toolkits/:toolkitId/agents', () =>
				HttpResponse.json({
					data: [
						{
							agent_id: 'agt_support_bot',
							agent_name: 'Support Bot',
							status: 'active',
							bound_at: '2026-05-02T09:00:00Z',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.delete('/toolkits/:toolkitId', ({ params }) => {
				deleted = params.toolkitId as string;
				return new HttpResponse(null, { status: 204 });
			}),
		);

		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });
		// Overview (landing) shows the bound agent the blast radius reads from.
		await screen.findByText('Support Bot');

		// The Delete affordance lives in the Settings tab's danger zone; its
		// blast-radius groups read the same cached keys/bindings/agents queries.
		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		await user.click(await screen.findByRole('button', { name: 'Delete GitHub Tools' }));

		// Dialog renders the blast-radius headline + each group's count line,
		// and lists the dependent names underneath. The headline text is built
		// from multiple inline children of a single <span>, so flatten via
		// normalizer rather than a literal text-node match. `findBy` also
		// absorbs the Settings tab's own keys/bindings fetches resolving just
		// after the dialog opens.
		const dialog = await screen.findByRole('dialog', { name: /delete toolkit/i });
		expect(
			await within(dialog).findByText(
				/Deleting this toolkit will also remove\s+3\s+dependents/,
			),
		).toBeInTheDocument();
		expect(within(dialog).getByText('1 agent grant')).toBeInTheDocument();
		expect(within(dialog).getByText('1 API key')).toBeInTheDocument();
		expect(within(dialog).getByText('1 credential binding')).toBeInTheDocument();
		expect(within(dialog).getByText('Support Bot')).toBeInTheDocument();
		expect(within(dialog).getByText('CI runner')).toBeInTheDocument();
		expect(within(dialog).getByText('GitHub PAT')).toBeInTheDocument();

		// Type-to-confirm gate: button stays disabled until the fixed word is typed.
		const confirm = within(dialog).getByRole('button', { name: /^delete toolkit$/i });
		expect(confirm).toBeDisabled();
		await user.type(within(dialog).getByLabelText(/type delete to confirm/i), 'delete');
		await waitFor(() => expect(confirm).toBeEnabled());

		await user.click(confirm);
		await waitFor(() => expect(deleted).toBe('tk_demo_github'));
	});

	it('keeps the cascade dialog open and surfaces the error when the delete fails', async () => {
		// Per-test handlers: a populated blast radius (so the dialog has stable
		// content) and a 500 on DELETE so we exercise the error path.
		worker.use(
			http.get('/toolkits/:toolkitId/keys', () =>
				HttpResponse.json({
					data: [
						{
							key_id: 'key_1',
							toolkit_id: 'tk_demo_github',
							label: 'CI runner',
							key_preview: 'jntc_live_ab12…',
							revoked: false,
							allowed_ips: null,
							last_used_at: null,
							created_at: '2026-05-01T10:05:00Z',
						},
					],
					has_more: false,
				}),
			),
			http.get('/toolkits/:toolkitId/credentials', () =>
				HttpResponse.json({ data: [], has_more: false }),
			),
			http.get('/toolkits/:toolkitId/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
			http.delete('/toolkits/:toolkitId', () =>
				HttpResponse.json(
					{ type: 'internal_error', status: 500, detail: 'Cascade failed mid-flight' },
					{ status: 500 },
				),
			),
		);

		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		await user.click(await screen.findByRole('button', { name: 'Delete GitHub Tools' }));
		const dialog = await screen.findByRole('dialog', { name: /delete toolkit/i });
		await user.type(within(dialog).getByLabelText(/type delete to confirm/i), 'delete');
		const confirm = within(dialog).getByRole('button', { name: /^delete toolkit$/i });
		await waitFor(() => expect(confirm).toBeEnabled());
		await user.click(confirm);

		// Dialog persists on error; the in-dialog error alert shows the server detail.
		await within(dialog).findByText(/cascade failed mid-flight/i);
		expect(screen.queryByRole('dialog', { name: /delete toolkit/i })).toBeInTheDocument();
	});
});
