import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import {
	resetAgentsStore,
	seedCredentialBindings,
	seedPermissionRuleSets,
} from '@/modules/agents/mocks/handlers';
import {
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CredentialType, type ApiResponse } from '@/shared/credentials/api';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

/**
 * A direct binding governed by a shared permission rule set (`rule_set_id`).
 * The broker evaluates the SET's rules and the binding's inline rules are
 * dormant, so the tile and the access sidebar must show the set — never the
 * empty inline list as "all calls blocked" — and inline editing waits for an
 * explicit detach.
 */

function apiRow(vendor: string, displayName: string): ApiResponse {
	return {
		_links: { self: `/apis/${vendor}`, openapi: `/apis/${vendor}/openapi` },
		api: { vendor, name: 'default', version: '1.0.0' },
		catalog_api_id: null,
		created_at: '2026-01-01T00:00:00Z',
		current_revision_id: null,
		description: null,
		display_name: displayName,
		icon_url: null,
		operation_count: 10,
		revision_count: 1,
		security_schemes: [],
		updated_at: '2026-01-01T00:00:00Z',
	} as unknown as ApiResponse;
}

const RULE_SET_ID = 'prs_support_read';

function seedStores({ inlineRules = false }: { inlineRules?: boolean } = {}) {
	resetCredentialsStore([
		makeMockCredential({
			credential_id: 'cred_slack_1',
			name: 'Slack bot token',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
		}),
		makeMockCredential({
			credential_id: 'cred_github_1',
			name: 'GitHub PAT',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
		}),
		makeMockCredential({
			credential_id: 'cred_stripe_1',
			name: 'Stripe key',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
		}),
	]);
	resetApisStore([
		{ row: apiRow('slack.com', 'Slack'), spec: {} },
		{ row: apiRow('github', 'GitHub'), spec: {} },
		{ row: apiRow('stripe.com', 'Stripe'), spec: {} },
	]);
	seedPermissionRuleSets([
		{
			rule_set_id: RULE_SET_ID,
			name: 'Support read-only',
			description: 'Read access for support agents.',
			curated: true,
			created_by: 'usr_admin_1',
			rules: [{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' }],
		},
	]);
	// Governed binding: its own inline list is empty (or a dormant narrow rule),
	// which must not read as the effective policy.
	seedCredentialBindings([
		{
			agent_id: 'agnt_active_1',
			credential_id: 'cred_stripe_1',
			name: 'Stripe key',
			rule_set_id: RULE_SET_ID,
			serves: [{ api_vendor: 'stripe.com', api_name: null, api_version: null }],
			permissions: inlineRules
				? [{ effect: 'allow', methods: ['POST'], path: '/v1/refunds', match_mode: 'exact' }]
				: [],
		},
	]);
}

function renderPage() {
	return renderWithProviders(
		<>
			<AgentsPage />
			<Toaster />
		</>,
		{ route: '/?agent=agnt_active_1' },
	);
}

async function openSidebar(title: string): Promise<HTMLElement> {
	const user = userEvent.setup();
	await user.click(await screen.findByRole('button', { name: `${title} — open access details` }));
	const dialog = await screen.findByRole('dialog', { name: title });
	await waitFor(() => {
		expect(within(dialog).getAllByRole('button')[0]).toHaveFocus();
	});
	return dialog;
}

/** The rules-summary line of the tile titled `title`. */
async function tileSummary(title: string): Promise<HTMLElement> {
	const tile = (await screen.findAllByTestId('api-tile')).find((el) =>
		within(el).queryByRole('heading', { name: title }),
	);
	if (!tile) throw new Error(`no tile titled ${title}`);
	return within(tile).findByTestId('tile-rules-summary');
}

describe('a credential binding governed by a shared rule set', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
	});

	it('the tile summarises the set’s rules, not the empty inline list', async () => {
		seedStores();
		renderPage();
		await waitFor(async () => {
			expect(await tileSummary('Stripe')).toHaveTextContent(
				'Rule set Support read-only · 1 access rule',
			);
		});
		expect(await tileSummary('Stripe')).not.toHaveTextContent('all calls blocked');
		// The inline-governed neighbour keeps its own summary.
		expect(await tileSummary('Slack')).toHaveTextContent(/^1 access rule$/);
	});

	it('the sidebar shows the attached set read-only, with no inline editor', async () => {
		seedStores({ inlineRules: true });
		renderPage();
		await screen.findByText('Rule set Support read-only · 1 access rule');

		const inDialog = within(await openSidebar('Stripe'));
		const panel = await inDialog.findByTestId('binding-rule-set-panel');

		expect(within(panel).getByTestId('rule-set-name')).toHaveTextContent('Support read-only');
		expect(within(panel).getByTestId('rule-set-curated')).toHaveTextContent('Curated');
		expect(within(panel).getByText('Read access for support agents.')).toBeInTheDocument();
		expect(
			within(panel).getByRole('list', { name: 'Rules in Support read-only' }),
		).toHaveTextContent('Allows GET, scoped to paths starting with /v1/');
		expect(within(panel).getByText(/only an org admin can edit it/)).toBeInTheDocument();
		expect(within(panel).getByTestId('dormant-inline-rules')).toHaveTextContent(
			"1 rule of this binding's own is dormant while the set is attached.",
		);

		// No inline editing while the set governs: no empty-rules copy, no
		// catch-all shortcut, no save.
		expect(inDialog.queryByText(/No rules yet/)).not.toBeInTheDocument();
		expect(
			inDialog.queryByRole('button', { name: /Allow all operations/ }),
		).not.toBeInTheDocument();
		expect(inDialog.queryByRole('button', { name: /Save rules/ })).not.toBeInTheDocument();
		expect(
			inDialog.getByRole('button', { name: /Detach rule set to edit inline rules/ }),
		).toBeInTheDocument();
	});

	it('the tester dry-runs the set and names its matching rule', async () => {
		const user = userEvent.setup();
		seedStores();
		renderPage();
		await screen.findByText('Rule set Support read-only · 1 access rule');

		const inDialog = within(await openSidebar('Stripe'));
		await inDialog.findByTestId('binding-rule-set-panel');
		await user.type(inDialog.getByLabelText('Request path'), '/v1/charges');
		await user.click(inDialog.getByRole('button', { name: 'Test' }));

		const verdict = await inDialog.findByTestId('rule-verdict');
		expect(verdict).toHaveTextContent('Allowed');
		expect(verdict).toHaveTextContent('matched rule #1');
	});

	it('detach confirms what applies next, then hands the binding to the inline editor', async () => {
		const user = userEvent.setup();
		seedStores();
		renderPage();
		await screen.findByText('Rule set Support read-only · 1 access rule');

		const inDialog = within(await openSidebar('Stripe'));
		await inDialog.findByTestId('binding-rule-set-panel');
		await user.click(
			inDialog.getByRole('button', { name: /Detach rule set to edit inline rules/ }),
		);

		const confirm = await screen.findByRole('dialog', { name: 'Detach rule set' });
		expect(confirm).toHaveTextContent('Support read-only');
		expect(confirm).toHaveTextContent('it has none, so every call is blocked');
		await user.click(within(confirm).getByRole('button', { name: 'Detach rule set' }));

		expect(await screen.findByText('Rule set detached')).toBeInTheDocument();
		// The inline editor takes over, and the empty inline list is now the truth.
		expect(await inDialog.findByText('No rules yet — add one below.')).toBeInTheDocument();
		expect(inDialog.queryByTestId('binding-rule-set-panel')).not.toBeInTheDocument();
		expect(await tileSummary('Stripe')).toHaveTextContent('No rules — all calls blocked');
	});

	it('a failed rule-set read keeps the inline editor out of reach', async () => {
		worker.use(
			createErrorHandler('get', '/permission-rule-sets/:rsid', {
				status: 500,
				body: { detail: 'boom' },
			}),
		);
		seedStores();
		renderPage();
		const inDialog = within(await openSidebar('Stripe'));
		expect(
			await inDialog.findByText('Failed to load the attached rule set.'),
		).toBeInTheDocument();
		expect(inDialog.queryByText(/No rules yet/)).not.toBeInTheDocument();
		expect(inDialog.queryByRole('button', { name: /Save rules/ })).not.toBeInTheDocument();
	});

	it('an inline-governed binding keeps the editor and never reads a rule set', async () => {
		let ruleSetReads = 0;
		worker.use(
			http.get('/permission-rule-sets/:rsid', () => {
				ruleSetReads += 1;
				return HttpResponse.json({ detail: 'unexpected' }, { status: 500 });
			}),
		);
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			}),
		]);
		resetApisStore([{ row: apiRow('slack.com', 'Slack'), spec: {} }]);
		renderPage();
		await screen.findByText('1 access rule');

		const inDialog = within(await openSidebar('Slack'));
		expect(
			await inDialog.findByText('Permission rules for Slack bot token'),
		).toBeInTheDocument();
		expect(inDialog.queryByTestId('binding-rule-set-panel')).not.toBeInTheDocument();
		expect(inDialog.getByRole('button', { name: /Save rules/ })).toBeInTheDocument();
		expect(ruleSetReads).toBe(0);
	});

	it('has no critical a11y violations with a governed binding open', async () => {
		seedStores({ inlineRules: true });
		const { container } = renderPage();
		await screen.findByText('Rule set Support read-only · 1 access rule');
		const inDialog = within(await openSidebar('Stripe'));
		await inDialog.findByTestId('binding-rule-set-panel');
		await new Promise((resolve) => setTimeout(resolve, 400));
		await checkA11y(container);
	});
});
