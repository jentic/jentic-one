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

		// Overview is the landing tab: audit slice renders without a click.
		expect(await screen.findByRole('heading', { name: /recent changes/i })).toBeInTheDocument();
		expect(await screen.findByText(/suspended pending review/i)).toBeInTheDocument();

		// Provenance line (created_by rendered at last).
		expect(screen.getByTestId('toolkit-provenance')).toHaveTextContent('admin@local');

		// Keys tab lists the seeded key.
		await user.click(screen.getByRole('tab', { name: /^Keys/ }));
		expect(await screen.findByText('CI runner')).toBeInTheDocument();

		// Access tab lists the bound credential.
		await user.click(screen.getByRole('tab', { name: /^Access/ }));
		expect(await screen.findByText('GitHub PAT')).toBeInTheDocument();

		// The immutable toolkit id lives on Settings (not in the page chrome).
		await user.click(screen.getByRole('tab', { name: 'Settings' }));
		expect(await screen.findByText('tk_demo_github')).toBeInTheDocument();
	});

	it('deep-links a tab through the ?tab= search param', async () => {
		renderWithProviders(<ToolkitDetailPage />, { route: `${ROUTE}?tab=keys`, path: PATH });

		await screen.findByRole('heading', { name: 'GitHub Tools' });
		// The Keys panel content renders without any click…
		expect(await screen.findByText('CI runner')).toBeInTheDocument();
		// …and the Keys tab is the selected one.
		expect(screen.getByRole('tab', { name: /^Keys/ })).toHaveAttribute('aria-selected', 'true');
	});

	it('shows the 7-day KPI strip from the toolkit-scoped usage aggregation', async () => {
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });

		const strip = await screen.findByTestId('usage-strip');
		// Sum of the seeded daily trend (64+88+71+120+104+141+126).
		expect(within(strip).getByText('714')).toBeInTheDocument();
		expect(within(strip).getByText('Executions')).toBeInTheDocument();
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
		const link = screen.getByRole('link', { name: /open monitor/i });
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
		expect(screen.getByRole('link', { name: /all toolkits/i })).toBeInTheDocument();
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

		await user.click(screen.getByRole('tab', { name: /^Keys/ }));
		await user.click(await screen.findByRole('button', { name: /create key/i }));
		await user.click(screen.getByRole('button', { name: /^generate$/i }));

		expect(await screen.findByText('New API Key Created')).toBeInTheDocument();
		expect(screen.getByText(/jntc_live_freshmockplaintext/)).toBeInTheDocument();
	});

	it('creates a key with an IP allowlist and shows the restriction chip', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: `${ROUTE}?tab=keys`, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(await screen.findByRole('button', { name: /create key/i }));
		await user.type(screen.getByLabelText('Key label'), 'Edge worker');
		await user.type(screen.getByLabelText('Allowed IPs'), '10.0.0.1, 10.0.0.2');
		await user.click(screen.getByRole('button', { name: /^generate$/i }));

		await screen.findByText('New API Key Created');
		// The new key row renders the allowed_ips chip.
		expect(await screen.findByText('10.0.0.1, 10.0.0.2')).toBeInTheDocument();
	});

	it('renames a key inline from the Keys tab', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: `${ROUTE}?tab=keys`, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });
		await screen.findByText('CI runner');

		// Pencil → inline input pre-filled with the current label.
		await user.click(screen.getAllByRole('button', { name: 'Rename key' })[0]);
		const input = screen.getByLabelText('Key label');
		expect(input).toHaveValue('CI runner');
		await user.clear(input);
		await user.type(input, 'Deploy runner{Enter}');

		// PATCH lands, the list refetches, the new label renders.
		expect(await screen.findByText('Deploy runner')).toBeInTheDocument();
		expect(screen.queryByText('CI runner')).not.toBeInTheDocument();
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

	it('binds a credential with full access through the two-step wizard', async () => {
		// Unique id: the shared MSW bindings store persists across tests AND
		// retries — a fixed id would leave the picker empty on a retry.
		const credId = `cred_stripe_${Math.random().toString(36).slice(2, 7)}`;
		seedCredentials([
			{ credential_id: credId, name: 'Stripe key', type: 'api_key', vendor: 'stripe' },
			{ credential_id: 'cred_gh_1', name: 'GitHub PAT', type: 'api_key', vendor: 'github' },
		]);
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(screen.getByRole('tab', { name: /^Access/ }));
		await user.click(await screen.findByRole('button', { name: /^bind credential$/i }));

		// Step 1: the picker lists the unbound credential…
		const stripeRow = await screen.findByText('Stripe key');
		expect(stripeRow).toBeInTheDocument();
		// …and hides the one already bound to this toolkit (cred_gh_1, "GitHub PAT").
		const dialog = screen.getByRole('dialog');
		expect(within(dialog).queryByText('GitHub PAT')).not.toBeInTheDocument();

		// Step 2: picking advances to the access decision (NOT an instant bind),
		// defaulting to the allow-all grant.
		await user.click(stripeRow);
		expect(
			await within(dialog).findByRole('radio', { name: /allow all operations/i }),
		).toHaveAttribute('aria-checked', 'true');
		await user.click(within(dialog).getByRole('button', { name: /^bind credential$/i }));
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

		// The binding lands WITH the allow-all rule — no zero-rules warning — and
		// renders the grant through the shared operations grammar (effect chip).
		// (findByText waits out the post-bind list refetch.)
		const stripeLabel = await screen.findByText(credId);
		const stripeBound = stripeLabel.closest('[data-testid="binding-row"]');
		expect(stripeBound).not.toBeNull();
		expect(within(stripeBound as HTMLElement).getByText('Allow')).toBeInTheDocument();
		expect(
			within(stripeBound as HTMLElement).queryByTestId('binding-warning'),
		).not.toBeInTheDocument();
	});

	it('binds a credential in the blocked state and surfaces the zero-rules warning', async () => {
		// Unique id for retry-safety (same reasoning as the wizard test above).
		const credId = `cred_notion_${Math.random().toString(36).slice(2, 7)}`;
		seedCredentials([
			{
				credential_id: credId,
				name: 'Notion token',
				type: 'api_key',
				vendor: 'notion',
			},
		]);
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(screen.getByRole('tab', { name: /^Access/ }));
		await user.click(await screen.findByRole('button', { name: /^bind credential$/i }));
		await user.click(await screen.findByText('Notion token'));

		const dialog = screen.getByRole('dialog');
		await user.click(await within(dialog).findByRole('radio', { name: /start blocked/i }));
		await user.click(within(dialog).getByRole('button', { name: /^bind credential$/i }));
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

		// Zero rules → the backend's warnings[] renders verbatim on the row, plus
		// the row-level "every operation is blocked" note. (findByText waits out
		// the post-bind list refetch.)
		const notionLabel = await screen.findByText(credId);
		const notionBound = notionLabel.closest('[data-testid="binding-row"]');
		expect(notionBound).not.toBeNull();
		expect(within(notionBound as HTMLElement).getByTestId('binding-warning')).toHaveTextContent(
			/no permission rules/i,
		);
		expect(
			within(notionBound as HTMLElement).getByTestId('binding-no-rules'),
		).toBeInTheDocument();
	});

	it('dry-runs a request against the saved rules with the rule tester', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: `${ROUTE}?tab=access`, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		// Open the rules editor for the seeded GitHub binding — the tester
		// lives behind a disclosure next to the rule editor. (Scoped to the
		// row: a previous test may have bound extra credentials to the shared
		// mock store.)
		const rows = await screen.findAllByTestId('binding-row');
		const githubRow = rows.find((r) => within(r).queryByText('GitHub PAT'));
		expect(githubRow).toBeDefined();
		await user.click(
			within(githubRow as HTMLElement).getByRole('button', { name: /edit rules/i }),
		);
		await user.click(await screen.findByRole('button', { name: /test a request/i }));
		await screen.findByLabelText('Request path');

		// The seeded allow rule is operation-scoped, so — like the real broker —
		// it only fires when the request carries a matching operation id.
		await user.type(screen.getByLabelText('Request path'), '/repos/acme/site');
		await user.type(screen.getByLabelText('Operation ID (optional)'), 'repos/get');
		await user.click(screen.getByRole('button', { name: /^test$/i }));
		// The verdict anchors to the numbered editor row AND names the rule in
		// the shared summary voice — no bare unanchored ordinal.
		const verdict = await screen.findByTestId('rule-verdict');
		expect(verdict).toHaveTextContent(/allowed — matched rule #1/i);
		expect(verdict).toHaveTextContent(/allows get/i);

		// The same request WITHOUT the operation id skips the operation-scoped
		// allow (broker fidelity) → default deny.
		await user.clear(screen.getByLabelText('Operation ID (optional)'));
		await user.click(screen.getByRole('button', { name: /^test$/i }));
		await waitFor(() =>
			expect(screen.getByTestId('rule-verdict')).toHaveTextContent(
				/denied — no rule matched/i,
			),
		);

		// /admin/… trips the platform-managed system safety deny — named as
		// such, never as a number pointing at an invisible row.
		await user.clear(screen.getByLabelText('Request path'));
		await user.type(screen.getByLabelText('Request path'), '/admin/users');
		await user.click(screen.getByRole('button', { name: /^test$/i }));
		await waitFor(() =>
			expect(screen.getByTestId('rule-verdict')).toHaveTextContent(
				/denied — matched a platform system safety rule/i,
			),
		);
	});

	it('shows a live pending-changes diff while editing rules', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: `${ROUTE}?tab=access`, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		const rows = await screen.findAllByTestId('binding-row');
		const githubRow = rows.find((r) => within(r).queryByText('GitHub PAT'));
		expect(githubRow).toBeDefined();
		await user.click(
			within(githubRow as HTMLElement).getByRole('button', { name: /edit rules/i }),
		);

		// Untouched draft → no diff panel, save disabled (nothing to commit).
		expect(screen.queryByTestId('rules-diff')).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: /save rules/i })).toBeDisabled();

		// Toggle POST onto the seeded allow-GET rule: the diff must show the old
		// grant leaving (−) and the widened grant arriving (+).
		await user.click(screen.getAllByRole('button', { name: 'POST', pressed: false })[0]);
		const diff = await screen.findByTestId('rules-diff');
		expect(within(diff).getByText(/Allows GET on 5 operations/)).toBeInTheDocument();
		expect(within(diff).getByText(/Allows GET, POST on 5 operations/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /save rules/i })).toBeEnabled();

		// Toggling POST back off restores a clean draft — diff gone (after the
		// exit animation), save off.
		await user.click(screen.getByRole('button', { name: 'POST', pressed: true }));
		await waitFor(() => expect(screen.queryByTestId('rules-diff')).not.toBeInTheDocument());
		expect(screen.getByRole('button', { name: /save rules/i })).toBeDisabled();
	});

	it('treats a pure rule reorder as a change (first match wins) and says so', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: `${ROUTE}?tab=access`, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		const rows = await screen.findAllByTestId('binding-row');
		const githubRow = rows.find((r) => within(r).queryByText('GitHub PAT'));
		expect(githubRow).toBeDefined();
		await user.click(
			within(githubRow as HTMLElement).getByRole('button', { name: /edit rules/i }),
		);

		// Rows are numbered — the same numbers the tester's verdict cites.
		const ruleRows = await screen.findAllByTestId('permission-rule-row');
		expect(within(ruleRows[0]).getByText('#1')).toBeInTheDocument();
		expect(within(ruleRows[1]).getByText('#2')).toBeInTheDocument();

		// Swap the two seeded rules: same multiset, different evaluation order —
		// the diff panel must flag the reorder and the save must arm.
		await user.click(within(ruleRows[0]).getByRole('button', { name: /move rule down/i }));
		const diff = await screen.findByTestId('rules-diff');
		expect(within(diff).getByText(/rules reordered/i)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /save rules/i })).toBeEnabled();

		// Swapping back restores a clean draft.
		const swapped = screen.getAllByTestId('permission-rule-row');
		await user.click(within(swapped[1]).getByRole('button', { name: /move rule up/i }));
		await waitFor(() => expect(screen.queryByTestId('rules-diff')).not.toBeInTheDocument());
		expect(screen.getByRole('button', { name: /save rules/i })).toBeDisabled();
	});

	it('filters the credential picker by the search term', async () => {
		seedCredentials([
			{ credential_id: 'cred_aws', name: 'AWS key', type: 'api_key', vendor: 'aws' },
			{ credential_id: 'cred_slack', name: 'Slack token', type: 'oauth2', vendor: 'slack' },
		]);
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		await user.click(screen.getByRole('tab', { name: /^Access/ }));
		await user.click(await screen.findByRole('button', { name: /^bind credential$/i }));
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

		// Bound agents section header reflects the seeded count.
		expect(await screen.findByText(/Bound agents \(1\)/)).toBeInTheDocument();
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

	it('shows the bound-credentials summary on Overview with a Manage jump to Access', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		// Summary of the Access tab's bindings, on the landing tab. (Count is
		// left loose: earlier cases in this file mutate the shared MSW store.)
		expect(await screen.findByText(/Bound credentials \(\d+\)/)).toBeInTheDocument();
		const credRows = await screen.findAllByTestId('overview-credential-row');
		const githubRow = credRows.find((r) => within(r).queryByText('GitHub PAT'));
		expect(githubRow).toBeDefined();
		// Each row carries the grant's gist in the platform's rule voice
		// (restrictions first), not an opaque "N rules" count.
		expect(within(githubRow as HTMLElement).getByText(/Blocks DELETE/)).toBeInTheDocument();

		// Manage jumps to the Access tab (full bind/permissions management).
		await user.click(screen.getByRole('button', { name: 'Manage' }));
		expect(screen.getByRole('tab', { name: /^Access/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(
			await screen.findByRole('button', { name: /^bind credential$/i }),
		).toBeInTheDocument();
	});

	it('opens the bind wizard straight from the Overview credentials card', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });
		await screen.findAllByTestId('overview-credential-row');

		// Symmetry with "Link agent": credentials bind from the landing tab too.
		await user.click(screen.getByRole('button', { name: /^bind credential$/i }));
		const dialog = await screen.findByRole('dialog');
		expect(within(dialog).getByText(/step 1 of 2/i)).toBeInTheDocument();
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

describe('ToolkitDetailPage — no-linked-agents banner', () => {
	it('warns when credentials are bound but no agent is linked', async () => {
		// tk_demo_github carries credential_count: 1; strip its bound agents.
		seedAgents({ bound: [], workspace: [] });
		const { container } = renderWithProviders(<ToolkitDetailPage />, {
			route: ROUTE,
			path: PATH,
		});
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		const banner = await screen.findByTestId('toolkit-no-agents-banner');
		expect(banner).toHaveTextContent(/no agent is linked to this toolkit/i);
		await checkA11y(container);
	});

	it('shows no banner when an agent is bound', async () => {
		// Default handlers bind Support Bot to tk_demo_github.
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		// Wait for the bound-agents query to resolve (Overview lists the agent)
		// so the assertion isn't a false pass on the not-yet-loaded state.
		await screen.findByText('Support Bot');
		expect(screen.queryByTestId('toolkit-no-agents-banner')).not.toBeInTheDocument();
	});

	it('shows no banner when the toolkit has no credentials to serve', async () => {
		worker.use(
			http.get('/toolkits/:toolkitId', () =>
				HttpResponse.json({
					toolkit_id: 'tk_demo_github',
					name: 'GitHub Tools',
					description: null,
					active: true,
					created_by: 'admin@local',
					created_at: '2026-05-01T10:00:00Z',
					updated_at: null,
					credential_count: 0,
					key_count: 0,
				}),
			),
		);
		seedAgents({ bound: [], workspace: [] });
		renderWithProviders(<ToolkitDetailPage />, { route: ROUTE, path: PATH });
		await screen.findByRole('heading', { name: 'GitHub Tools' });

		// An unbound, credential-less toolkit is just new — nothing to warn about.
		await waitFor(() =>
			expect(screen.queryByTestId('toolkit-no-agents-banner')).not.toBeInTheDocument(),
		);
	});
});
