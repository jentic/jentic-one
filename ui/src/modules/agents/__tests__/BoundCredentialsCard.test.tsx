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

/**
 * Override the org-wide `GET /credentials` surface for bind-picker tests.
 * The agents module reads it through the shared API; the credentials mock
 * store starts empty, so we stub a small fixture here (no sibling-module
 * import).
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

	it('shows the empty state pointing at the bind action', async () => {
		worker.use(http.get('/agents/:id/credentials', () => HttpResponse.json({ data: [] })));
		renderAccessTab();
		expect(
			await screen.findByText(/no credentials bound directly to this agent/i),
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /bind a credential/i })).toBeInTheDocument();
	});

	it('binds a credential with the allow-all grant through the two-step wizard', async () => {
		seedCredentials([
			{
				credential_id: 'cred_acme_1',
				name: 'Acme token',
				type: 'bearer_token',
				vendor: 'acme',
			},
		]);
		const user = userEvent.setup();
		renderAccessTab();
		await screen.findByRole('heading', { name: /bound credentials/i });

		await user.click(screen.getByRole('button', { name: /^bind credential$/i }));
		const dialog = await screen.findByRole('dialog');

		// Step 1: workspace credentials appear; already-bound ones are hidden.
		expect(await within(dialog).findByText('Acme token')).toBeInTheDocument();
		expect(within(dialog).queryByText('Slack bot token')).not.toBeInTheDocument();

		// Step 2: picking advances to the access decision (NOT an instant bind),
		// defaulting to the allow-all grant.
		await user.click(within(dialog).getByText('Acme token'));
		expect(
			await within(dialog).findByRole('radio', { name: /allow all operations/i }),
		).toHaveAttribute('aria-checked', 'true');
		await user.click(within(dialog).getByRole('button', { name: /^bind credential$/i }));
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

		// The binding lands WITH the allow-all rule — no zero-rules warning.
		// (The mock store names bind-created rows by nothing, so the heading is
		// the credential id.)
		const label = await screen.findByText('cred_acme_1');
		const row = label.closest('[data-testid="binding-row"]') as HTMLElement;
		expect(row).not.toBeNull();
		expect(await within(row).findByText('Allow')).toBeInTheDocument();
		expect(within(row).queryByTestId('binding-warning')).not.toBeInTheDocument();
	});

	it('binds in the blocked state (zero rules) and surfaces the default-deny warning; custom mode requires at least one rule', async () => {
		seedCredentials([
			{
				credential_id: 'cred_notion_1',
				name: 'Notion token',
				type: 'api_key',
				vendor: 'notion',
			},
		]);
		const user = userEvent.setup();
		renderAccessTab();
		await screen.findByRole('heading', { name: /bound credentials/i });

		await user.click(screen.getByRole('button', { name: /^bind credential$/i }));
		const dialog = await screen.findByRole('dialog');
		await user.click(await within(dialog).findByText('Notion token'));

		// Custom rules with an empty rule list cannot submit — a zero-rules
		// custom grant is a contradiction the wizard blocks up front.
		await user.click(await within(dialog).findByRole('radio', { name: /custom rules/i }));
		expect(within(dialog).getByRole('button', { name: /^bind credential$/i })).toBeDisabled();

		// "Start blocked" is the deliberate zero-rules mode.
		await user.click(within(dialog).getByRole('radio', { name: /start blocked/i }));
		await user.click(within(dialog).getByRole('button', { name: /^bind credential$/i }));
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

		const label = await screen.findByText('cred_notion_1');
		const row = label.closest('[data-testid="binding-row"]') as HTMLElement;
		expect(await within(row).findByTestId('binding-warning')).toHaveTextContent(
			/default deny/i,
		);
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
		const user = userEvent.setup();
		renderAccessTab();
		await screen.findByRole('heading', { name: /bound credentials \(2\)/i });

		const slack = findRow('Slack bot token') as HTMLElement;
		await within(slack).findByText('Allow'); // rules loaded
		await user.click(within(slack).getByRole('button', { name: /edit rules/i }));

		// Untouched draft → no diff panel, save disabled (nothing to commit).
		expect(screen.queryByTestId('rules-diff')).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: /save rules/i })).toBeDisabled();

		// Toggle GET onto the seeded allow-POST rule: the diff must show the old
		// grant leaving (−) and the widened grant arriving (+).
		await user.click(screen.getAllByRole('button', { name: 'GET', pressed: false })[0]);
		const diff = await screen.findByTestId('rules-diff');
		expect(
			within(diff).getByText(/Allows POST, scoped to paths starting with \/chat\./),
		).toBeInTheDocument();
		expect(within(diff).getByText(/Allows POST, GET/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /save rules/i })).toBeEnabled();

		// Save commits the replacement and closes the editor.
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
		// …and no bind affordance renders for a pending agent.
		expect(screen.queryByRole('button', { name: /bind credential/i })).not.toBeInTheDocument();
	});
});
