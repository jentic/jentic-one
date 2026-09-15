import { describe, it, expect, beforeEach } from 'vitest';
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

const ROUTE = '/agents/agnt_active_1?tab=access';
const PATH = '/agents/:agentId';

function renderAccessTab(route = ROUTE) {
	return renderWithProviders(
		<>
			<AgentDetailPage />
			<Toaster />
		</>,
		{ route, path: PATH },
	);
}

/** The binding row whose heading contains `label`, or undefined. */
function findRow(label: string): HTMLElement | undefined {
	return screen.getAllByTestId('binding-row').find((r) => within(r).queryByText(label)) as
		HTMLElement | undefined;
}

describe('BoundCredentialsCard (agent detail · Access tab)', () => {
	beforeEach(() => {
		setToken('test-token');
		resetAgentsStore();
	});

	it('lists direct bindings first on the tab: rule summary, suspended state, zero-rules warning', async () => {
		const { container } = renderAccessTab();
		expect(
			await screen.findByRole('heading', { name: /bound credentials \(2\)/i }),
		).toBeInTheDocument();

		// The card renders FIRST on the access tab — the capability story leads.
		const panel = screen.getByRole('tabpanel');
		const headings = within(panel).getAllByRole('heading');
		expect(headings[0]).toHaveTextContent(/bound credentials/i);

		// Healthy binding: name + the grant through the operations grammar.
		const slack = findRow('Slack bot token');
		expect(slack).toBeDefined();
		expect(await within(slack as HTMLElement).findByText('Allow')).toBeInTheDocument();
		expect(
			within(slack as HTMLElement).queryByTestId('binding-warning'),
		).not.toBeInTheDocument();
		expect(
			within(slack as HTMLElement).queryByTestId('binding-suspended'),
		).not.toBeInTheDocument();

		// Suspended, rule-less binding: distinct visual + resume affordance +
		// the default-deny warning grammar.
		const github = findRow('GitHub PAT');
		expect(github).toBeDefined();
		expect(within(github as HTMLElement).getByTestId('binding-suspended')).toHaveTextContent(
			'Suspended',
		);
		expect(
			await within(github as HTMLElement).findByTestId('binding-warning'),
		).toHaveTextContent(/no rules — all operations blocked/i);
		expect(
			within(github as HTMLElement).getByRole('button', { name: /resume binding/i }),
		).toBeInTheDocument();

		await checkA11y(container);
	});

	it('shows the empty state pointing at the connect-integration action', async () => {
		worker.use(http.get('/agents/:id/credentials', () => HttpResponse.json({ data: [] })));
		renderAccessTab();
		expect(
			await screen.findByText(/no credentials bound directly to this agent/i),
		).toBeInTheDocument();
		// Empty-state inline CTA + the section header action both point at
		// the same connect flow now that the "Bind existing" path folded
		// into it.
		expect(screen.getByRole('button', { name: /connect an integration/i })).toBeInTheDocument();
	});

	it('opens the connect wizard from the single "Connect integration" action', async () => {
		// The old "Bind existing" wizard was replaced by a single button
		// that opens ``CreateCredentialDialog`` with ``preselectedAgentId``.
		// The dialog itself is exercised in its own tests — we just verify
		// the wiring here.
		const user = userEvent.setup();
		renderAccessTab();
		await screen.findByRole('heading', { name: /bound credentials/i });
		const connectBtn = await screen.findByRole('button', {
			name: /^connect integration$/i,
		});
		await user.click(connectBtn);
		expect(await screen.findByRole('dialog')).toBeInTheDocument();
	});

	it('suspends a binding behind an inline confirm, then resumes it', async () => {
		const user = userEvent.setup();
		renderAccessTab();
		await screen.findByRole('heading', { name: /bound credentials \(2\)/i });

		// Suspend the healthy Slack binding — two-step inline confirm.
		const slack = findRow('Slack bot token') as HTMLElement;
		await user.click(
			within(slack).getByRole('button', { name: /suspend binding for slack bot token/i }),
		);
		await user.click(within(slack).getByRole('button', { name: /^suspend$/i }));
		await waitFor(() => {
			const row = findRow('Slack bot token') as HTMLElement;
			expect(within(row).getByTestId('binding-suspended')).toBeInTheDocument();
		});

		// Suspension is reversible: resume restores the active state (rules
		// survived — the row still shows its grant, not the zero-rules warning).
		const suspended = findRow('Slack bot token') as HTMLElement;
		await user.click(
			within(suspended).getByRole('button', { name: /resume binding for slack bot token/i }),
		);
		await waitFor(() => {
			const row = findRow('Slack bot token') as HTMLElement;
			expect(within(row).queryByTestId('binding-suspended')).not.toBeInTheDocument();
		});
		const resumed = findRow('Slack bot token') as HTMLElement;
		expect(within(resumed).queryByTestId('binding-warning')).not.toBeInTheDocument();
	});

	it('permanently unbinds (purge) behind the stronger confirm', async () => {
		const user = userEvent.setup();
		renderAccessTab();
		await screen.findByRole('heading', { name: /bound credentials \(2\)/i });

		const slack = findRow('Slack bot token') as HTMLElement;
		await user.click(
			within(slack).getByRole('button', { name: /permanently unbind slack bot token/i }),
		);
		// The armed confirm names the destructive outcome, not a bare "OK".
		expect(
			within(slack).getByText(/permanently unbind\? the binding and its rules are deleted/i),
		).toBeInTheDocument();
		await user.click(within(slack).getByRole('button', { name: /^unbind permanently$/i }));

		await waitFor(() => expect(screen.queryByText('Slack bot token')).not.toBeInTheDocument());
		expect(
			screen.getByRole('heading', { name: /bound credentials \(1\)/i }),
		).toBeInTheDocument();
	});

	it('edits rules with a live pending-changes diff and saves through the permissions PUT', async () => {
		// The editor now uses the shared ``RuleListEditor`` — rules
		// render as compact read-only rows and per-row fields (method
		// toggles etc.) are only visible after clicking the row's
		// Pencil to open the inline form. Flow: outer "Edit rules" →
		// inline Pencil on target row → toggle method → inline Save →
		// outer "Save rules".
		const user = userEvent.setup();
		renderAccessTab();
		await screen.findByRole('heading', { name: /bound credentials \(2\)/i });

		const slack = findRow('Slack bot token') as HTMLElement;
		await within(slack).findByText('Allow'); // rules loaded
		await user.click(within(slack).getByRole('button', { name: /edit rules/i }));

		// Untouched draft → no diff panel, save disabled (nothing to commit).
		expect(screen.queryByTestId('rules-diff')).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: /save rules/i })).toBeDisabled();

		// Open the inline edit-a-rule form (Pencil icon on the row —
		// aria-label "Edit rule", distinct from the outer "Edit rules"
		// toggle). Toggle GET onto the seeded allow-POST rule, commit
		// the inline Save. Outer diff panel then shows the old grant
		// leaving (−) and the widened grant arriving (+).
		await user.click(screen.getByRole('button', { name: /^edit rule$/i }));
		await user.click(screen.getByRole('button', { name: 'GET' }));
		await user.click(screen.getByRole('button', { name: /^save$/i }));
		const diff = await screen.findByTestId('rules-diff');
		expect(
			within(diff).getByText(/Allows POST, scoped to paths starting with \/chat\./),
		).toBeInTheDocument();
		expect(within(diff).getByText(/Allows POST, GET/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /save rules/i })).toBeEnabled();

		// Outer save commits the replacement and closes the editor.
		await user.click(screen.getByRole('button', { name: /save rules/i }));
		await waitFor(() =>
			expect(screen.queryByRole('button', { name: /save rules/i })).not.toBeInTheDocument(),
		);

		// The refetched row summary reflects the widened grant.
		await waitFor(() => {
			const row = findRow('Slack bot token') as HTMLElement;
			expect(within(row).getByText('POST GET')).toBeInTheDocument();
		});
	});

	it('renders the shared ops preview on the binding row when the credential resolves', async () => {
		// The preview is always visible on the row (not gated on the
		// Edit-rules toggle). Version is sourced from the credential's
		// ``api`` field via ``useCredential`` — the binding's ``serves``
		// entry only carries ``(vendor, name)``. Overrides here: the
		// binding list, the credential row, and the vendor's ops.
		worker.use(
			http.get('/agents/:id/credentials', () =>
				HttpResponse.json({
					data: [
						{
							id: 'bind_preview_1',
							credential_id: 'cred_preview_1',
							name: 'Preview binding',
							suspended: false,
							rule_set_id: null,
							bound_at: '2026-05-01T10:00:00Z',
							serves: [
								{ api_vendor: 'github-com', api_name: null, api_version: null },
							],
						},
					],
				}),
			),
			// Version comes from the credential's ``api`` field, not the
			// binding's serves entry. Return a credential whose api
			// carries the concrete ``(vendor, name, version)`` triple.
			http.get('/credentials/cred_preview_1', () =>
				HttpResponse.json({
					credential_id: 'cred_preview_1',
					name: 'Preview binding',
					type: 'bearer_token',
					provider: 'manual',
					active: true,
					api: { vendor: 'github-com', name: 'github-com', version: '1.0.0' },
					created_at: '2026-05-01T10:00:00Z',
					updated_at: null,
				}),
			),
			// Ops preview drives off the resolved (vendor, name, version).
			http.get('/apis/github-com/github-com/1.0.0/operations', () =>
				HttpResponse.json({
					data: [
						{
							operation_id: 'repos/get',
							method: 'GET',
							path: '/repos/{owner}/{repo}',
							name: 'Get a repository',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			// Binding permissions load empty. The wire path is
			// credentials-scoped — see
			// ``CredentialsService.listAgentCredentialPermissions``.
			http.get('/credentials/:cid/agents/:aid/permissions', () =>
				HttpResponse.json({ data: [] }),
			),
		);
		renderAccessTab();
		// Preview label appears on the row without needing to click Edit.
		expect(await screen.findByText(/effective access for this binding/i)).toBeInTheDocument();
	});

	it('dry-runs a request against the saved rules with the rule tester — both verdicts', async () => {
		const user = userEvent.setup();
		renderAccessTab();
		await screen.findByRole('heading', { name: /bound credentials \(2\)/i });

		const slack = findRow('Slack bot token') as HTMLElement;
		await within(slack).findByText('Allow');
		await user.click(within(slack).getByRole('button', { name: /edit rules/i }));
		await user.click(await screen.findByRole('button', { name: /test a request/i }));
		await screen.findByLabelText('Request path');

		// The seeded rule allows POST on the /chat. prefix — the verdict anchors
		// to the numbered editor row.
		await user.selectOptions(screen.getByLabelText('HTTP method'), 'POST');
		await user.type(screen.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(screen.getByRole('button', { name: /^test$/i }));
		const verdict = await screen.findByTestId('rule-verdict');
		expect(verdict).toHaveTextContent(/allowed — matched rule #1/i);

		// The same path under GET matches nothing → honest default-deny copy.
		await user.selectOptions(screen.getByLabelText('HTTP method'), 'GET');
		await user.click(screen.getByRole('button', { name: /^test$/i }));
		await waitFor(() =>
			expect(screen.getByTestId('rule-verdict')).toHaveTextContent(
				/denied — no rule matched \(default deny\)/i,
			),
		);
	});

	it('gates the bind action to active agents', async () => {
		renderAccessTab('/agents/agnt_pending_1?tab=access');
		expect(
			await screen.findByRole('heading', { name: /bound credentials \(0\)/i }),
		).toBeInTheDocument();
		// The empty copy explains the approval gate instead of dangling a
		// dead-end CTA…
		expect(await screen.findByText(/approve this agent first/i)).toBeInTheDocument();
		// …and no bind affordance renders for a pending agent — the
		// "Connect integration" entry point is gated on the agent being
		// active.
		expect(
			screen.queryByRole('button', { name: /connect integration/i }),
		).not.toBeInTheDocument();
	});
});
