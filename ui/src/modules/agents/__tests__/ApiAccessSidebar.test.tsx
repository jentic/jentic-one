import { describe, it, expect, beforeEach, afterEach } from 'vitest';
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
	seedExtraAgents,
	setBindingPermissions,
} from '@/modules/agents/mocks/handlers';
import { agentsKeysForTest } from '@/modules/agents/api/hooks';
import {
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CredentialType, type ApiResponse } from '@/shared/credentials/api';
import { AuthProvider } from '@/shared/auth';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

/**
 * The API access sidebar — one tile's access in one panel: the credential, the
 * Access read view and editor, the dry-run tester (of the SAVED rules), the connect flow,
 * and the two DISTINCT destructive verbs (unbind this agent vs delete the
 * credential org-wide). Rendered through the full AgentsPage, so the wiring is real.
 */

/** Workspace API row for the registry mock. */
function apiRow(
	vendor: string,
	displayName: string,
	operationCount: number,
	name = 'default',
	version = '1.0.0',
): ApiResponse {
	return {
		_links: { self: `/apis/${vendor}`, openapi: `/apis/${vendor}/openapi` },
		api: { vendor, name, version },
		catalog_api_id: null,
		created_at: '2026-01-01T00:00:00Z',
		current_revision_id: null,
		description: null,
		display_name: displayName,
		icon_url: null,
		operation_count: operationCount,
		revision_count: 1,
		security_schemes: [],
		updated_at: '2026-01-01T00:00:00Z',
	} as unknown as ApiResponse;
}

/** One operation, as `GET /apis/{v}/{n}/{ver}/operations` lists it. */
function op(operationId: string, method: string, path: string, name: string) {
	return {
		operation_id: operationId,
		method,
		path,
		name,
		description: null,
		tags: [],
		deprecated: false,
		revision_id: 'rev_1',
		_links: {},
	};
}

/** Serve each API's operations, keyed `vendor/name/version`; others 404. */
function serveOperations(byApi: Record<string, ReturnType<typeof op>[]>) {
	worker.use(
		http.get('/apis/:vendor/:name/:version/operations', ({ params }) => {
			const ops = byApi[`${params.vendor}/${params.name}/${params.version}`];
			if (!ops) return HttpResponse.json({ status: 404 }, { status: 404 });
			return HttpResponse.json({ data: ops, has_more: false, next_cursor: null });
		}),
	);
}

/** The org credentials + workspace APIs behind `agnt_active_1`'s bindings. */
function seedComposedStores() {
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
			api: { vendor: 'github', name: 'default', version: '1.0.0' },
		}),
	]);
	resetApisStore([
		{ row: apiRow('slack.com', 'Slack', 181), spec: {} },
		// Vendor `github` (not `github.com`) is what the shared binding fixture
		// serves, so this row is the one the GitHub tile resolves against.
		{ row: apiRow('github', 'GitHub', 912), spec: {} },
	]);
}

/** Slack's operations, as By action and Effective access list them. */
const SLACK_OPS = [
	op('chat.postMessage', 'post', '/chat.postMessage', 'Send a message'),
	op('conversations.list', 'get', '/conversations.list', 'List channels'),
	op('users.list', 'get', '/users.list', ''),
];

/** Slack with `ops` as its whole operation list. */
function seedSlackOps(ops = SLACK_OPS) {
	resetApisStore([
		{ row: apiRow('slack.com', 'Slack', ops.length), spec: {} },
		{ row: apiRow('github', 'GitHub', 912), spec: {} },
	]);
	serveOperations({ 'slack.com/default/1.0.0': ops });
}

/**
 * One VENDOR-WIDE credential (`nytimes-com`, no API name) serving two imported
 * APIs of that vendor, Archive and Article Search — one binding, so one rule
 * list shared by both. Its one `serves` entry is the credential's own scope.
 */
function seedVendorWide(permissions: Record<string, unknown>[]) {
	resetCredentialsStore([
		makeMockCredential({
			credential_id: 'cred_nyt',
			name: 'NY Times key',
			type: CredentialType.API_KEY,
			api: { vendor: 'nytimes-com', name: '', version: '' },
		}),
	]);
	resetApisStore([
		{ row: apiRow('nytimes-com', 'Archive', 1, 'archive'), spec: {} },
		{ row: apiRow('nytimes-com', 'Article Search', 1, 'article-search'), spec: {} },
	]);
	serveOperations({
		'nytimes-com/archive/1.0.0': [op('archive', 'get', '/{year}/{month}.json', 'Archive API')],
		'nytimes-com/article-search/1.0.0': [
			op('articlesearch', 'get', '/articlesearch.json', 'Article Search'),
		],
	});
	seedCredentialBindings([
		{
			agent_id: 'agnt_active_1',
			credential_id: 'cred_nyt',
			name: 'NY Times key',
			serves: [{ api_vendor: 'nytimes-com', api_name: null, api_version: null }],
			permissions: permissions as never,
		},
	]);
}

function renderPage(route = '/?agent=agnt_active_1') {
	return renderWithProviders(
		<>
			<AgentsPage />
			<Toaster />
		</>,
		{ route },
	);
}

const escapeRe = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** The row's Manage access button — its one way into the access sheet (the
 * row and its chevron only pin it open). */
function tileOpener(title: string): HTMLElement {
	return screen.getByRole('button', {
		name: new RegExp(`^Manage access for ${escapeRe(title)}( \\(|$)`),
	});
}

/** The sidebar dialog, named by the clicked tile's API title. */
async function openSidebar(title: string): Promise<HTMLElement> {
	const user = userEvent.setup();
	await user.click(tileOpener(title));
	const dialog = await screen.findByRole('dialog', { name: title });
	// Wait until the sheet has finished ENTERING: SheetPrimitive auto-focuses its
	// first focusable child then, which is also when its Escape handler arms.
	await waitFor(() => {
		expect(within(dialog).getAllByRole('button')[0]).toHaveFocus();
	});
	return dialog;
}

/** The page, loaded, with the Slack sidebar open. */
async function openSlack(): Promise<HTMLElement> {
	renderPage();
	await screen.findByText('1 access rule');
	return openSidebar('Slack');
}

/** Access → Edit access: the editor on a draft. */
async function editAccess(dialog: HTMLElement): Promise<HTMLElement> {
	await userEvent
		.setup()
		.click(await within(dialog).findByRole('button', { name: /Edit access/ }));
	return within(dialog).findByTestId('access-editor');
}

/** Every `PUT …/permissions` body, in order. */
function recordSaves(): unknown[][] {
	const bodies: unknown[][] = [];
	worker.events.on('request:start', ({ request }) => {
		if (request.method === 'PUT' && request.url.includes('/permissions')) {
			void request
				.clone()
				.json()
				.then((b: unknown) => bodies.push(b as unknown[]));
		}
	});
	return bodies;
}

/** The per-action Allow · Ask · Deny control in By action. */
function accessFor(scope: HTMLElement, action: string): HTMLElement {
	return within(scope).getByRole('group', { name: `Access for ${action}` });
}

/** One action's Allow, Ask or Deny button in By action. */
function choose(scope: HTMLElement, action: string, effect: 'Allow' | 'Ask' | 'Deny') {
	return within(accessFor(scope, action)).getByRole('button', { name: effect });
}

describe('ApiAccessSidebar — the API tile access panel', () => {
	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		resetAgentsStore();
		seedComposedStores();
	});

	afterEach(() => worker.events.removeAllListeners('request:start'));

	/**
	 * The rules editor lists rules read-only; a change goes through one rule's
	 * inline form. Opens the first rule, appends `suffix` to its path and commits
	 * the rule — which leaves the BINDING's draft dirty until `Save rules`.
	 */
	async function editFirstRulePath(
		user: ReturnType<typeof userEvent.setup>,
		inDialog: ReturnType<typeof within>,
		suffix: string,
	): Promise<void> {
		await user.click(inDialog.getAllByRole('button', { name: 'Edit rule' })[0]);
		await user.type(inDialog.getByLabelText('Path pattern'), suffix);
		await user.click(inDialog.getByRole('button', { name: 'Save' }));
	}

	// --- Opening: content, keyboard, focus restore --------------------------

	it('tile click opens the sidebar with that binding’s credential, rules and tester', async () => {
		const saves = recordSaves();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		// The tile's aria wiring reflects the open panel.
		expect(tileOpener('Slack')).toHaveAttribute('aria-expanded', 'true');
		expect(tileOpener('GitHub')).toHaveAttribute('aria-expanded', 'false');

		// One panel, in order: Credential, Access, Test a request, Danger zone —
		// each always visible, none collapsed.
		const regions = inDialog
			.getAllByRole('region')
			.map((r) => r.getAttribute('aria-label'))
			.filter((n) =>
				['Credential', 'Access', 'Test a request', 'Danger zone'].includes(n ?? ''),
			);
		expect(regions).toEqual(['Credential', 'Access', 'Test a request', 'Danger zone']);

		// 1 — the credential (read view + the edit affordance).
		const section = within(inDialog.getByRole('region', { name: 'Credential' }));
		expect(section.getByText('Slack bot token')).toBeInTheDocument();
		expect(section.getByRole('button', { name: /Edit credential/ })).toBeVisible();
		// The full id, copyable, for a call that names it. One credential for the
		// API needs no header, so no header snippet.
		expect(section.getByTestId('credential-id-row')).toHaveTextContent('IDcred_slack_1');
		expect(section.getByRole('button', { name: 'Copy the credential ID' })).toBeInTheDocument();
		expect(inDialog.queryByText(/^Jentic-Credential-Id:/)).not.toBeInTheDocument();

		// 2 — the Access read view: the question, the saved rules' summary, Edit access.
		const read = await inDialog.findByTestId('access-read-view');
		expect(read).toHaveTextContent('What can support-agent call?');
		expect(within(read).getByTestId('access-summary')).toHaveTextContent(
			'Custom rules — 1 path rule.',
		);
		// How rules are checked is for writing them: not in the read view.
		expect(within(read).queryByTestId('manage-rules-help')).toBeNull();
		expect(inDialog.getByRole('button', { name: /Edit access/ })).toBeInTheDocument();
		// Viewing never edits: no editor, no footer, nothing saved.
		expect(inDialog.queryByTestId('access-editor')).not.toBeInTheDocument();
		expect(inDialog.queryByTestId('access-commit')).not.toBeInTheDocument();
		expect(saves).toEqual([]);

		// 3 — the tester.
		expect(inDialog.getByLabelText('Request path')).toBeVisible();
		// The optional operation id is a disclosure, closed until asked for.
		expect(inDialog.queryByLabelText('Operation ID (optional)')).not.toBeInTheDocument();

		// Single-API credential → no blast-radius note.
		expect(inDialog.queryByTestId('blast-radius-note')).not.toBeInTheDocument();

		// The two destructive verbs are both present and distinctly worded.
		expect(
			inDialog.getByRole('button', { name: 'Unbind Slack bot token from support-agent' }),
		).toBeInTheDocument();
		expect(
			inDialog.getByRole('button', { name: 'Delete credential Slack bot token org-wide' }),
		).toBeInTheDocument();
		expect(
			within(inDialog.getByTestId('sidebar-danger-zone')).getByText('Danger zone'),
		).toBeVisible();
		// No approvals UI anywhere in the panel.
		expect(dialog).not.toHaveTextContent(/Waiting for you/);
	});

	it('several credentials: one line, with how calls pick one behind Details', async () => {
		const user = userEvent.setup();
		seedCredentialBindings([
			{
				agent_id: 'agnt_active_1',
				credential_id: 'cred_slack_2',
				serves: [{ api_vendor: 'slack.com', api_name: 'default', api_version: '1.0.0' }],
			},
		]);
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			}),
			makeMockCredential({
				credential_id: 'cred_slack_2',
				name: 'Slack notify token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			}),
		]);
		renderPage();
		await screen.findAllByRole('button', { name: /^Manage access for Slack/ });
		await user.click(
			screen.getByRole('button', { name: 'Manage access for Slack (Slack bot token)' }),
		);
		const dialog = await screen.findByRole('dialog', { name: 'Slack' });
		const line = within(dialog).getByTestId('multi-account-line');
		expect(line).toHaveTextContent('1 of 2 credentials for Slack · how calls pick one');
		expect(within(line).queryByText(/^Jentic-Credential-Id:/)).toBeNull();
		await user.click(within(line).getByRole('button', { name: /Details/ }));
		await waitFor(() =>
			expect(within(line).getByText('Jentic-Credential-Id: cred_slack_1')).toBeVisible(),
		);
	});

	it('lists the credential details: scope, auth, bound (exact time on hover), added, id', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
				created_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
				updated_at: null,
			}),
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
				created_at: new Date(Date.now() - 5 * 86_400_000).toISOString(),
				updated_at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
			}),
		]);
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		// Scope: the API's host and auth, for this agent.
		expect(dialog).toHaveTextContent('slack.com · Bearer token — access for support-agent');
		const section = within(within(dialog).getByRole('region', { name: 'Credential' }));
		const facts = section.getByText(/^Bearer token · bound/);
		// Auth, when it was bound (its exact time is the age's description),
		// and the credential's age (created: "added").
		expect(facts).toHaveTextContent(/^Bearer token · bound 30m/);
		expect(facts).toHaveTextContent(/· added 3d ·/);
		// The full id.
		expect(section.getByTestId('credential-id-row')).toHaveTextContent('IDcred_slack_1');
		// The bound age's exact time rides on its tooltip.
		const bound = within(facts).getByText('30m');
		await user.hover(bound);
		const exact = /^[A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2}\s?[AP]M$/;
		await waitFor(() =>
			expect(
				screen.getAllByRole('tooltip').some((t) => exact.test(t.textContent ?? '')),
			).toBe(true),
		);
	});

	it('says when the credential was last updated, in place of when it was added', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
				created_at: new Date(Date.now() - 5 * 86_400_000).toISOString(),
				updated_at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
			}),
		]);
		renderPage();
		await screen.findByText('1 access rule');
		const dialog = await openSidebar('Slack');
		const facts = within(dialog).getByText(/^Bearer token · bound/);
		expect(facts).toHaveTextContent(/· updated 2h ·/);
		expect(facts).not.toHaveTextContent(/added/);
	});

	// --- Editing: the read/edit switch, presets, unsaved changes ------------

	it('Edit access turns the Access section into the editor; Close editor returns with nothing saved', async () => {
		const saves = recordSaves();
		const user = userEvent.setup();
		const dialog = await openSlack();
		const editor = await editAccess(dialog);

		// The presets, as visible radio rows, on the saved choice.
		const presets = within(editor).getByRole('radiogroup', {
			name: 'What can support-agent call?',
		});
		expect(
			within(presets)
				.getAllByRole('radio')
				.map((r) => r.textContent),
		).toEqual([
			expect.stringMatching(/^Read only/),
			expect.stringMatching(/^Read only, ask before anything else/),
			expect.stringMatching(/^Full access/),
			expect.stringMatching(/^Custom rules/),
		]);
		expect(within(presets).getByRole('radio', { name: /Custom rules/ })).toHaveAttribute(
			'aria-checked',
			'true',
		);
		// The saved rule, editable by path.
		expect(within(editor).getByText('/chat. (prefix)')).toBeInTheDocument();
		// Nothing pending: no save area at all, and no Done.
		expect(within(dialog).queryByTestId('access-commit')).toBeNull();
		expect(within(dialog).queryByRole('button', { name: /^Done/ })).toBeNull();

		// The quiet Close editor in the rules container's header, unguarded.
		const container = within(editor).getByTestId('access-rules-container');
		await user.click(within(container).getByRole('button', { name: 'Close editor' }));
		expect(screen.queryByRole('dialog', { name: /Leave with/ })).toBeNull();
		expect(await within(dialog).findByTestId('access-read-view')).toBeInTheDocument();
		expect(saves).toEqual([]);
	});

	it('a preset saves its rules, and the read view names it', async () => {
		const saves = recordSaves();
		const user = userEvent.setup();
		const dialog = await openSlack();
		const editor = await editAccess(dialog);

		await user.click(within(editor).getByRole('radio', { name: 'Read only' }));
		const footer = within(dialog).getByTestId('access-commit');
		expect(within(footer).getByTestId('rules-dirty-hint')).toHaveTextContent(
			'2 unsaved changes',
		);
		expect(within(dialog).getByTestId('rules-diff')).toHaveTextContent(
			/Pending changes · applied when you save.*Removed: Allows POST.*Added: Allows GET/,
		);
		await user.click(within(footer).getByRole('button', { name: /Save rules/ }));

		await waitFor(() => expect(saves).toHaveLength(1));
		expect(saves[0]).toEqual([{ effect: 'allow', methods: ['GET'] }]);
		// The save closes the editor onto the read view; its summary names the preset.
		const read = await within(dialog).findByTestId('access-read-view');
		expect(within(read).getByTestId('access-summary')).toHaveTextContent(
			'Read only — GET requests run, everything else is denied.',
		);
	});

	it('Full access saves the catch-all as a REGEX (a prefix ".*" would grant nothing)', async () => {
		const user = userEvent.setup();
		const saves = recordSaves();
		renderPage();
		await screen.findByText('1 access rule');
		const dialog = await openSidebar('Slack');
		const editor = await editAccess(dialog);

		await user.click(within(editor).getByRole('radio', { name: /^Full access/ }));
		await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));

		await waitFor(() => expect(saves).toHaveLength(1));
		const [catchAll] = saves[0] as { path?: string; match_mode?: string; methods?: string[] }[];
		expect(catchAll.path).toBe('.*');
		expect(catchAll.methods).toBeUndefined();
		// `regex` is the server default, so it may be omitted — but never prefix/exact.
		expect(catchAll.match_mode ?? 'regex').toBe('regex');
	});

	it('unsaved changes: the count, what changed, and Discard', async () => {
		const saves = recordSaves();
		const user = userEvent.setup();
		const dialog = await openSlack();
		const editor = await editAccess(dialog);
		await editFirstRulePath(user, within(editor), 'post');
		const footer = within(dialog).getByTestId('access-commit');
		// An edited rule is one removed and one added.
		expect(within(footer).getByTestId('rules-dirty-hint')).toHaveTextContent(
			'2 unsaved changes',
		);
		expect(within(dialog).getByTestId('rules-diff')).toHaveTextContent(
			/Removed: Allows POST, scoped to paths starting with \/chat\.Added: Allows POST, scoped to paths starting with \/chat\.post/,
		);
		// The Test row says the draft isn't what it tests.
		expect(within(dialog).getByTestId('rule-tester-note')).toHaveTextContent(
			'Checks the saved rules. Nothing is sent upstream — save to test your changes.',
		);

		await user.click(within(footer).getByRole('button', { name: /Discard changes/ }));
		// Discarded means discarded: the editor is back on the saved rules, and
		// with nothing pending the save area is gone.
		expect(within(dialog).queryByTestId('access-commit')).toBeNull();
		expect(within(dialog).queryByTestId('rules-diff')).toBeNull();
		expect(within(dialog).getByText('/chat. (prefix)')).toBeInTheDocument();
		expect(saves).toEqual([]);
	});

	it('a reorder alone is a change, named as one', async () => {
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{ effect: 'allow', match_mode: 'prefix', methods: ['POST'], path: '/chat.' },
			{ effect: 'deny', match_mode: 'prefix', methods: ['POST'], path: '/admin' },
		]);
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('Slack bot token');
		const dialog = await openSidebar('Slack');
		const editor = await editAccess(dialog);
		await user.click(within(editor).getAllByRole('button', { name: 'Move rule down' })[0]);
		const footer = within(dialog).getByTestId('access-commit');
		expect(within(footer).getByTestId('rules-dirty-hint')).toHaveTextContent(
			'1 unsaved change',
		);
		expect(within(dialog).getByTestId('rules-diff')).toHaveTextContent('Rules reordered');
	});

	it('Save closes the editor; "Saved" shows on the read view for a few seconds, announced', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');
		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		await editFirstRulePath(user, within(await editAccess(dialog)), 'post');
		await user.click(inDialog.getByRole('button', { name: /Save rules/ }));
		const saved = await within(dialog).findByTestId('rules-saved');
		expect(saved).toHaveTextContent('Saved — these rules are live');
		expect(saved.closest('[aria-live="polite"]')).not.toBeNull();
		const read = within(dialog).getByTestId('access-read-view');
		expect(read).toContainElement(saved);
		expect(within(dialog).queryByTestId('access-editor')).toBeNull();
		// Transient: gone after ~4s.
		await waitFor(() => expect(within(dialog).queryByTestId('rules-saved')).toBeNull(), {
			timeout: 6000,
		});
	});

	it('a failed save keeps the editor open with the draft', async () => {
		worker.use(
			http.put('/credentials/:cid/agents/:aid/permissions', () =>
				HttpResponse.json({ detail: 'boom' }, { status: 500 }),
			),
		);
		const user = userEvent.setup();
		const dialog = await openSlack();
		const editor = await editAccess(dialog);
		await editFirstRulePath(user, within(editor), 'post');
		await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));
		await waitFor(() =>
			expect(within(dialog).getByRole('button', { name: /Save rules/ })).toBeEnabled(),
		);
		expect(within(dialog).getByTestId('access-editor')).toBeInTheDocument();
		expect(within(editor).getByText('/chat.post (prefix)')).toBeInTheDocument();
		expect(within(dialog).queryByTestId('rules-saved')).toBeNull();
		// The error still toasts.
		expect(await screen.findByText('Failed to save permission rules.')).toBeInTheDocument();
	});

	it('the save area sits right under the rules, before Effective access, and never moves', async () => {
		seedSlackOps();
		const user = userEvent.setup();
		const dialog = await openSlack();
		const editor = await editAccess(dialog);
		await editFirstRulePath(user, within(editor), 'post');
		const container = within(editor).getByTestId('access-rules-container');
		const commit = within(dialog).getByTestId('access-commit');
		// Inside the rules container, at its bottom.
		expect(container).toContainElement(commit);
		expect(container.lastElementChild).toBe(commit);
		// Effective access comes after it, outside the container.
		const effective = within(editor).getByText('Effective access for this binding');
		expect(container).not.toContainElement(effective);
		expect(
			commit.compareDocumentPosition(effective) & Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		// Expanding Effective access never pushes Save down: its offset in the
		// editor is unchanged (measured against the editor, so a scroll of the
		// sheet doesn't count as a move).
		const offset = (): number =>
			commit.getBoundingClientRect().top - editor.getBoundingClientRect().top;
		const top = offset();
		const group = await within(editor).findByRole('button', { name: /chat\.postMessage/ });
		await user.click(group);
		await waitFor(() =>
			expect(within(editor).getAllByText(/^\/chat\.postMessage$/).length).toBeGreaterThan(0),
		);
		expect(offset()).toBe(top);
	});

	it('the summary and bar show in both views: saved in the read view, the draft while editing', async () => {
		seedSlackOps();
		const user = userEvent.setup();
		const dialog = await openSlack();
		const read = await within(dialog).findByTestId('access-read-view');
		expect(within(read).getByTestId('access-summary')).toHaveTextContent(
			'Custom rules — 1 path rule.',
		);
		// Denied says how much of it is "not set", so it never reads as Deny choices.
		await waitFor(() =>
			expect(within(read).getByTestId('access-counts')).toHaveTextContent(
				'1 allowed0 ask2 denied (not set)',
			),
		);
		expect(within(read).getByTestId('access-counts-source')).toHaveTextContent('saved');

		const editor = await editAccess(dialog);
		expect(within(editor).getByTestId('access-counts-source')).toHaveTextContent('your draft');
		await user.click(within(editor).getByRole('radio', { name: 'Full access' }));
		expect(within(editor).getByTestId('access-summary')).toHaveTextContent(
			'Full access — any method, any path.',
		);
		await waitFor(() =>
			expect(within(editor).getByTestId('access-counts')).toHaveTextContent(
				'3 allowed0 ask0 denied',
			),
		);
	});

	it('every count agrees: the summary counts the same actions as By action', async () => {
		// Allow ids that aren't this API's operations (stale, or another version's)
		// count in neither the summary nor By action.
		seedSlackOps([
			op('chat.postMessage', 'post', '/chat.postMessage', 'Send a message'),
			op('users.list', 'get', '/users.list', 'List users'),
		]);
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{ effect: 'require-approval', operations: ['chat.postMessage'] },
			{ effect: 'allow', operations: ['gone.one', 'gone.two', 'gone.three'] },
		]);
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('Slack bot token');
		const dialog = await openSidebar('Slack');
		await waitFor(() =>
			expect(within(dialog).getByTestId('access-summary')).toHaveTextContent(
				'Custom rules — 1 action set.',
			),
		);
		const editor = await editAccess(dialog);
		await user.click(within(editor).getByRole('button', { name: 'By action' }));
		expect(await within(editor).findByTestId('manage-actions-count')).toHaveTextContent(
			'0 allow · 1 ask · 0 deny · 1 not set',
		);
		expect(within(editor).getByTestId('access-summary')).toHaveTextContent(
			'Custom rules — 1 action set.',
		);
		await waitFor(() =>
			expect(within(editor).getByTestId('access-counts')).toHaveTextContent(
				'0 allowed1 ask1 denied (not set)',
			),
		);
	});

	it('how rules work: one line under By action | By path, the rules behind Details', async () => {
		seedSlackOps([op('chat.postMessage', 'post', '/chat.postMessage', 'Send a message')]);
		const user = userEvent.setup();
		const dialog = await openSlack();
		await within(dialog).findByTestId('access-read-view');
		expect(within(dialog).queryByTestId('manage-rules-help')).toBeNull();
		// Editing Custom rules: one short line by the toggle; Details holds the rules.
		const editor = await editAccess(dialog);
		const help = await within(editor).findByTestId('manage-rules-help');
		expect(help).toHaveTextContent('Actions first, then path rules in order');
		expect(within(help).queryByTestId('access-rules-help')).toBeNull();
		await user.click(within(help).getByRole('button', { name: /Details/ }));
		const rules = await within(help).findByTestId('access-rules-help');
		expect(
			within(rules)
				.getAllByRole('listitem')
				.map((li) => li.textContent),
		).toEqual([
			'Actions you set always win.',
			'Everything else goes to the path rules, in order — the first match decides.',
			'Anything still unmatched is denied.',
		]);
	});

	it('an older saved order shows as a pending change, not a banner', async () => {
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{
				effect: 'allow',
				match_mode: 'prefix',
				methods: ['POST'],
				path: '/chat.',
			},
			{ effect: 'require-approval', operations: ['a', 'b', 'c'] },
		]);
		renderPage();
		await screen.findByText('Slack bot token');
		const dialog = await openSidebar('Slack');
		await editAccess(dialog);
		expect(within(dialog).getByTestId('rules-diff-moved-ahead')).toHaveTextContent(
			'Moves 3 action choices ahead of the path rules',
		);
		expect(within(dialog).getByTestId('rules-dirty-hint')).toHaveTextContent(
			'1 unsaved change',
		);
	});

	it('one save state at a time: the save area only while pending; Save disabled while choices are unapplied', async () => {
		seedSlackOps();
		const user = userEvent.setup();
		const dialog = await openSlack();
		const editor = await editAccess(dialog);
		expect(within(dialog).queryByTestId('access-commit')).toBeNull();
		expect(within(dialog).queryByRole('button', { name: /Done/ })).toBeNull();

		// A staged By action choice: the save area shows, Save is disabled with the reason.
		await user.click(within(editor).getByRole('button', { name: 'By action' }));
		const actions = await within(editor).findByTestId('manage-actions');
		await user.click(choose(actions, 'POST /chat.postMessage', 'Ask'));
		const commit = within(dialog).getByTestId('access-commit');
		const save = within(commit).getByRole('button', { name: /Save rules/ });
		expect(save).toBeDisabled();
		expect(within(commit).getByTestId('rules-staged-reason')).toHaveTextContent(
			'Apply or reset the 1 choice by action before saving.',
		);
		expect(save).toHaveAccessibleDescription(
			'Apply or reset the 1 choice by action before saving.',
		);

		await user.click(within(editor).getByTestId('manage-apply'));
		expect(within(commit).getByRole('button', { name: /Save rules/ })).toBeEnabled();
		expect(within(commit).getByRole('button', { name: /Discard changes/ })).toBeInTheDocument();
		expect(within(commit).queryByTestId('rules-staged-reason')).toBeNull();
	});

	it('closing with staged By action choices asks to apply or discard them', async () => {
		seedSlackOps([op('chat.postMessage', 'post', '/chat.postMessage', 'Send a message')]);
		const user = userEvent.setup();
		const { container } = renderPage();
		await screen.findByText('1 access rule');
		const dialog = await openSidebar('Slack');
		const editor = await editAccess(dialog);
		await user.click(within(editor).getByRole('button', { name: 'By action' }));
		const actions = await within(editor).findByTestId('manage-actions');
		await user.click(choose(actions, 'POST /chat.postMessage', 'Ask'));
		await user.click(within(dialog).getByRole('button', { name: 'Close' }));
		const guard = await screen.findByRole('dialog', { name: 'Apply 1 change by action?' });
		await checkA11y(container, { modal: true });
		// Apply keeps the panel open, with the choice now in the draft to save.
		await user.click(within(guard).getByRole('button', { name: 'Apply' }));
		expect(screen.getByRole('dialog', { name: 'Slack' })).toBeInTheDocument();
		expect(await within(dialog).findByTestId('rules-diff')).toHaveTextContent(
			'Ask: Send a message',
		);
	});

	// --- The leave guard ------------------------------------------------------

	it('guards closing with edits pending: Keep editing keeps the draft, Discard and leave closes', async () => {
		const user = userEvent.setup();
		const dialog = await openSlack();
		await editFirstRulePath(user, within(await editAccess(dialog)), 'x');
		// Committing one rule's form never needs the guard.
		expect(screen.queryByRole('dialog', { name: /Leave with/ })).not.toBeInTheDocument();

		await user.click(within(dialog).getByRole('button', { name: 'Close' }));
		const guard = await screen.findByRole('dialog', {
			name: 'Leave with 2 unsaved changes?',
		});
		expect(guard).toHaveTextContent(
			"Test runs against the saved rules, so it won't see your draft.",
		);
		await user.click(within(guard).getByRole('button', { name: 'Keep editing' }));
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: /Leave with/ })).not.toBeInTheDocument(),
		);
		// Still open, still editing, draft intact.
		expect(screen.getByRole('dialog', { name: 'Slack' })).toBeInTheDocument();
		expect(within(dialog).getByTestId('access-editor')).toHaveTextContent('/chat.x (prefix)');

		await user.click(within(dialog).getByRole('button', { name: 'Close' }));
		await user.click(
			within(
				await screen.findByRole('dialog', { name: /Leave with 2 unsaved changes/ }),
			).getByRole('button', { name: 'Discard and leave' }),
		);
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument(),
		);
	});

	it('Escape with edits pending asks first, too', async () => {
		const user = userEvent.setup();
		const dialog = await openSlack();
		await editFirstRulePath(user, within(await editAccess(dialog)), 'x');
		// Focus inside the sheet so its Escape handler hears the key.
		within(dialog)
			.getByRole('button', { name: /Save rules/ })
			.focus();
		await user.keyboard('{Escape}');
		expect(
			await screen.findByRole('dialog', { name: /Leave with 2 unsaved changes/ }),
		).toBeInTheDocument();
		expect(screen.getByRole('dialog', { name: 'Slack' })).toBeInTheDocument();
	});

	it('Close editor is guarded while anything is pending', async () => {
		const saves = recordSaves();
		const user = userEvent.setup();
		const dialog = await openSlack();
		const editor = await editAccess(dialog);
		await editFirstRulePath(user, within(editor), 'post');
		await user.click(within(editor).getByRole('button', { name: 'Close editor' }));
		const guard = await screen.findByRole('dialog', { name: /Leave with 2 unsaved changes/ });
		await user.click(within(guard).getByRole('button', { name: 'Keep editing' }));
		expect(within(dialog).getByTestId('access-editor')).toBeInTheDocument();
		await user.click(within(editor).getByRole('button', { name: 'Close editor' }));
		await user.click(
			within(
				await screen.findByRole('dialog', { name: /Leave with 2 unsaved changes/ }),
			).getByRole('button', { name: 'Discard and leave' }),
		);
		expect(await within(dialog).findByTestId('access-read-view')).toBeInTheDocument();
		// The panel stays open on the saved rules; nothing was saved.
		expect(within(dialog).getByTestId('access-summary')).toHaveTextContent(
			'Custom rules — 1 path rule.',
		);
		expect(saves).toEqual([]);
	});

	it('without edits pending, nothing guards: Escape closes', async () => {
		const user = userEvent.setup();
		const dialog = await openSlack();
		await editAccess(dialog);
		within(dialog).getByRole('button', { name: 'Close editor' }).focus();
		await user.keyboard('{Escape}');
		expect(screen.queryByRole('dialog', { name: /Leave with/ })).not.toBeInTheDocument();
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument(),
		);
	});

	// --- By action: editing an existing action rule, adding actions -----------

	it('opens an existing action rule in By action with each action set, and adds more', async () => {
		seedSlackOps([
			op('chat.postMessage', 'post', '/chat.postMessage', 'Send a message'),
			op('conversations.list', 'get', '/conversations.list', 'List channels'),
			op('users.list', 'get', '/users.list', 'List users'),
		]);
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{ effect: 'require-approval', operations: ['chat.postMessage'] },
			{ effect: 'allow', operations: ['conversations.list'] },
		]);
		const saves = recordSaves();
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('Slack bot token');
		const dialog = await openSidebar('Slack');
		const editor = await editAccess(dialog);

		// By action, with each saved action already set.
		const build = within(editor).getByRole('group', { name: 'Build rules' });
		await waitFor(() =>
			expect(within(build).getByRole('button', { name: 'By action' })).toHaveAttribute(
				'aria-pressed',
				'true',
			),
		);
		const actions = within(editor).getByTestId('manage-actions');
		expect(choose(actions, 'POST /chat.postMessage', 'Ask')).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		expect(choose(actions, 'GET /conversations.list', 'Allow')).toHaveAttribute(
			'aria-pressed',
			'true',
		);

		// Add one: users.list → Allow, staged until Apply; then pending changes name it.
		await user.click(choose(actions, 'GET /users.list', 'Allow'));
		expect(within(editor).getByTestId('manage-apply-status')).toHaveTextContent(
			'1 change not applied',
		);
		await user.click(within(editor).getByTestId('manage-apply'));
		expect(within(dialog).getByTestId('rules-diff')).toHaveTextContent('Allow: List users');
		await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));
		await waitFor(() => expect(saves).toHaveLength(1));
		expect(saves[0]).toEqual([
			{ effect: 'require-approval', operations: ['chat.postMessage'] },
			{ effect: 'allow', operations: ['conversations.list', 'users.list'] },
		]);
	});

	it('fades the body only while content continues below it', async () => {
		// A short viewport forces the panel to overflow; the danger zone is its
		// last section, so the fade is what says it exists at all.
		await page.viewport(1280, 340);
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);
		await waitFor(() => {
			expect(inDialog.getByTestId('sidebar-scroll-fade')).toBeInTheDocument();
		});
		// Let the reveal finish growing before scrolling to its end.
		await new Promise((resolve) => setTimeout(resolve, 400));

		const scroller = inDialog.getByTestId('sidebar-danger-zone').closest('div.overflow-y-auto');
		scroller?.scrollTo(0, scroller.scrollHeight);
		// At the end of the scroll the fade must go — it would otherwise promise
		// content that isn't there.
		await waitFor(() => {
			expect(inDialog.queryByTestId('sidebar-scroll-fade')).not.toBeInTheDocument();
		});
	});

	it('discloses the optional operation id, and clears it when closed again', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const inDialog = within(await openSidebar('Slack'));
		await user.click(inDialog.getByRole('button', { name: /operation id/ }));

		const field = inDialog.getByLabelText('Operation ID (optional)');
		await user.type(field, 'chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Remove operation id' }));
		expect(inDialog.queryByLabelText('Operation ID (optional)')).not.toBeInTheDocument();

		// Re-opening must not restore a value the operator dismissed — a hidden
		// operation id would silently change the next verdict.
		await user.click(inDialog.getByRole('button', { name: /operation id/ }));
		expect(inDialog.getByLabelText('Operation ID (optional)')).toHaveValue('');
	});

	it('the rehosted tester dry-runs the SAVED rules of the clicked binding', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const test = within(within(dialog).getByRole('region', { name: 'Test a request' }));
		expect(test.getByTestId('rule-tester-note')).toHaveTextContent(
			'Checks the saved rules. Nothing is sent upstream.',
		);

		// The seeded rule allows POST /chat.* — a POST to /chat.postMessage
		// must come back allowed and NAME the matching rule.
		await user.selectOptions(test.getByLabelText('HTTP method'), 'POST');
		await user.type(test.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(test.getByRole('button', { name: 'Test' }));

		const verdict = await test.findByTestId('rule-verdict');
		expect(verdict).toHaveTextContent('Allowed');
		expect(verdict).toHaveTextContent('matched rule #1');
	});

	it('the tester reports a require-approval match as needing approval, not denied', async () => {
		worker.use(
			http.post('/credentials/:cid/agents/:aid/permissions\\:test', () =>
				HttpResponse.json({
					allowed: false,
					matched: true,
					effect: 'require-approval',
					rule_index: 0,
					is_system: false,
					credential_id: 'cred_slack_1',
				}),
			),
		);
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const inDialog = within(await openSidebar('Slack'));
		await user.selectOptions(inDialog.getByLabelText('HTTP method'), 'POST');
		await user.type(inDialog.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Test' }));

		const verdict = await inDialog.findByTestId('rule-verdict');
		expect(verdict).toHaveTextContent('Ask');
		expect(verdict).not.toHaveTextContent('Denied');
	});

	it('names an Ask match it cannot point at as "an ask rule", never the raw effect', async () => {
		worker.use(
			http.post('/credentials/:cid/agents/:aid/permissions\\:test', () =>
				HttpResponse.json({
					allowed: false,
					matched: true,
					effect: 'require-approval',
					// No saved rule at this index (the rules changed since): the
					// verdict falls back to the effect's word.
					rule_index: 9,
					is_system: false,
					credential_id: 'cred_slack_1',
				}),
			),
		);
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const inDialog = within(await openSidebar('Slack'));
		await user.type(inDialog.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Test' }));

		const verdict = await inDialog.findByTestId('rule-verdict');
		expect(verdict).toHaveTextContent('Ask');
		expect(verdict).toHaveTextContent('matched an ask rule on this binding');
		expect(verdict).not.toHaveTextContent('require-approval');
		expect(verdict).not.toHaveTextContent('Denied');
	});

	it('opens from the keyboard and restores focus to the tile on close', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const opener = tileOpener('Slack');
		opener.focus();
		await user.keyboard('{Enter}');
		const dialog = await screen.findByRole('dialog', { name: 'Slack' });
		// The Escape handler arms when the enter animation settles (the sheet
		// auto-focuses its first focusable child at that moment).
		await waitFor(() => {
			expect(within(dialog).getAllByRole('button')[0]).toHaveFocus();
		});

		await user.keyboard('{Escape}');
		await waitFor(() => {
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument();
		});
		// Focus returns to the element that opened the sheet.
		await waitFor(() => expect(tileOpener('Slack')).toHaveFocus());
		expect(tileOpener('Slack')).toHaveAttribute('aria-expanded', 'false');
	});

	// --- Blast radius: one credential, several APIs --------------------------

	it('an any-version credential with one version imported: no shared note', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '' },
			}),
		]);
		const dialog = await openSlack();
		// Nothing else is imported, so nothing shares the rules today.
		expect(within(dialog).queryByTestId('blast-radius-note')).toBeNull();
		const editor = await editAccess(dialog);
		expect(within(editor).queryByTestId('manage-coverage-note')).toBeNull();
	});

	describe('a vendor-wide credential serving Archive and Article Search', () => {
		it('the Credential note names the other API, never this one — even while editing, the one statement that the rules are shared', async () => {
			seedVendorWide([{ effect: 'require-approval', operations: ['archive'] }]);
			renderPage();
			await screen.findByText('Article Search');
			const dialog = await openSidebar('Article Search');
			const note = within(dialog).getByTestId('blast-radius-note');
			expect(note).toHaveTextContent(
				'support-agent also uses this credential for Archive, under these same rules. Changing them here changes that access too.',
			);
			expect(note).not.toHaveTextContent('Article Search');

			const editor = await editAccess(dialog);
			expect(within(dialog).getByTestId('blast-radius-note')).toBeVisible();
			expect(dialog.textContent?.match(/also uses this credential/g)).toHaveLength(1);
			expect(within(editor).queryByTestId('manage-coverage-note')).not.toBeInTheDocument();
			expect(editor).not.toHaveTextContent('Covers every API of this vendor');
		});

		it('names an Archive action as Archive’s on Article Search — never as missing', async () => {
			seedVendorWide([{ effect: 'require-approval', operations: ['archive'] }]);
			const user = userEvent.setup();
			renderPage();
			await screen.findByText('Article Search');
			const dialog = await openSidebar('Article Search');

			// Read view: speaks for THIS API.
			const read = await within(dialog).findByTestId('access-read-view');
			expect(within(read).getByTestId('access-summary')).toHaveTextContent(
				'none name this API',
			);

			// Editing: By path names the action and its API.
			const editor = await editAccess(dialog);
			await user.click(within(editor).getByRole('button', { name: 'By path' }));
			const row = await within(editor).findByTestId('manage-action-rule-ask');
			await waitFor(() =>
				expect(within(row).getByTestId('action-api')).toHaveTextContent('— on Archive'),
			);
			expect(row).toHaveTextContent('/{year}/{month}.json');
			expect(row).not.toHaveTextContent(/not in this version/);
		});

		it('By action lists this API’s actions only, and names the others on one line', async () => {
			seedVendorWide([{ effect: 'require-approval', operations: ['archive'] }]);
			const user = userEvent.setup();
			renderPage();
			await screen.findByText('Article Search');
			const dialog = await openSidebar('Article Search');
			const editor = await editAccess(dialog);
			const actions = await within(editor).findByTestId('manage-actions');
			expect(within(actions).getAllByRole('group', { name: /^Access for/ })).toHaveLength(1);
			expect(accessFor(actions, 'GET /articlesearch.json')).toBeInTheDocument();
			// Archive's own action is counted on its line, not in this list's summary.
			expect(within(editor).getByTestId('manage-actions-count')).toHaveTextContent(
				'0 allow · 0 ask · 0 deny · 1 not set',
			);
			const sibling = within(editor).getByTestId('manage-sibling-actions');
			expect(sibling).toHaveTextContent(
				"Also asks before 1 action on Archive — edit it from Archive's access.",
			);

			// Its button opens Archive's own panel.
			await user.click(within(sibling).getByRole('button', { name: 'Open Archive' }));
			expect(await screen.findByRole('dialog', { name: 'Archive' })).toBeInTheDocument();
		});

		it('a save from Article Search keeps Archive’s action ids — same effect, same rule', async () => {
			seedVendorWide([{ effect: 'require-approval', operations: ['archive'] }]);
			const saves = recordSaves();
			const user = userEvent.setup();
			renderPage();
			await screen.findByText('Article Search');
			const dialog = await openSidebar('Article Search');
			const editor = await editAccess(dialog);
			const actions = await within(editor).findByTestId('manage-actions');
			await user.click(choose(actions, 'GET /articlesearch.json', 'Allow'));
			// Save waits for Apply; then it saves the applied draft.
			expect(within(dialog).getByRole('button', { name: /Save rules/ })).toBeDisabled();
			await user.click(within(editor).getByTestId('manage-apply'));
			await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));
			await waitFor(() => expect(saves).toHaveLength(1));
			expect(saves[0]).toEqual([
				// Still Ask, still exactly Archive's id, still first.
				{ effect: 'require-approval', operations: ['archive'] },
				{ effect: 'allow', operations: ['articlesearch'] },
			]);
		});

		it('a preset chosen from Article Search still keeps Archive’s actions', async () => {
			seedVendorWide([{ effect: 'require-approval', operations: ['archive'] }]);
			const saves = recordSaves();
			const user = userEvent.setup();
			renderPage();
			await screen.findByText('Article Search');
			const dialog = await openSidebar('Article Search');
			const editor = await editAccess(dialog);
			await within(editor).findByTestId('manage-actions');
			await user.click(within(editor).getByRole('radio', { name: 'Read only' }));
			await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));
			await waitFor(() => expect(saves).toHaveLength(1));
			// Archive's choice is kept, and — a choice on an action always wins —
			// saved ahead of the preset's rule.
			expect(saves[0]).toEqual([
				{ effect: 'require-approval', operations: ['archive'] },
				{ effect: 'allow', methods: ['GET'] },
			]);
		});

		it('"not in this version" only for an id on none of the APIs', async () => {
			seedVendorWide([{ effect: 'allow', operations: ['archive', 'gone'] }]);
			const user = userEvent.setup();
			renderPage();
			await screen.findByText('Article Search');
			const dialog = await openSidebar('Article Search');
			const editor = await editAccess(dialog);
			await user.click(await within(editor).findByRole('button', { name: 'By path' }));
			const row = await within(editor).findByTestId('manage-action-rule');
			const list = within(row).getByTestId('manage-action-rule-actions');
			await waitFor(() => expect(list).toHaveAttribute('data-status', 'ready'));
			const items = within(list).getAllByRole('listitem');
			expect(items[0]).toHaveTextContent('/{year}/{month}.json');
			expect(items[0]).toHaveTextContent('— on Archive');
			expect(items[1]).toHaveTextContent('An action not in this version of the API');
		});

		it('"no ops affected" only when a rule matches nothing on any covered API', async () => {
			seedVendorWide([
				// Archive's path: matches nothing on Article Search, but is not dead.
				{
					effect: 'allow',
					match_mode: 'exact',
					methods: ['GET'],
					path: '/{year}/{month}.json',
				},
				// Matches nothing anywhere.
				{ effect: 'allow', match_mode: 'exact', methods: ['GET'], path: '/nowhere' },
			]);
			renderPage();
			await screen.findByText('Article Search');
			const dialog = await openSidebar('Article Search');
			const editor = await editAccess(dialog);
			const rows = await within(editor).findAllByRole('listitem');
			const ruleRows = rows.filter((r) => /\/\{year\}|\/nowhere/.test(r.textContent ?? ''));
			await waitFor(() =>
				expect(
					within(ruleRows[1]).getByRole('status', { name: 'No operations affected' }),
				).toBeInTheDocument(),
			);
			expect(
				within(ruleRows[0]).queryByRole('status', { name: 'No operations affected' }),
			).not.toBeInTheDocument();
		});
	});

	it('a vendor-wide credential with ONE imported API: no shared note', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_nyt',
				name: 'NY Times key',
				type: CredentialType.API_KEY,
				api: { vendor: 'nytimes-com', name: '', version: '' },
			}),
		]);
		resetApisStore([{ row: apiRow('nytimes-com', 'Archive', 1, 'archive'), spec: {} }]);
		seedCredentialBindings([
			{
				agent_id: 'agnt_active_1',
				credential_id: 'cred_nyt',
				serves: [{ api_vendor: 'nytimes-com', api_name: null, api_version: null }],
				permissions: [{ effect: 'allow', methods: ['GET'] }],
			},
		]);
		renderPage();
		await waitFor(() => expect(tileOpener('Archive')).toBeInTheDocument());
		const dialog = await openSidebar('Archive');
		await within(dialog).findByTestId('credential-id-row');
		// No other imported API: nothing shares the rules, so no warning either way.
		expect(within(dialog).queryByTestId('blast-radius-note')).toBeNull();
	});

	it('two versions of one API: "other versions of this API", never its own title', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_gh_any',
				name: 'GitHub any-version',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github', name: 'rest', version: '' },
			}),
		]);
		resetApisStore([
			{ row: apiRow('github', 'GitHub', 10, 'rest', '1.0.0'), spec: {} },
			{ row: apiRow('github', 'GitHub', 12, 'rest', '2.0.0'), spec: {} },
		]);
		seedCredentialBindings([
			{
				agent_id: 'agnt_active_1',
				credential_id: 'cred_gh_any',
				serves: [{ api_vendor: 'github', api_name: 'rest', api_version: null }],
				permissions: [{ effect: 'allow', methods: ['GET'] }],
			},
		]);
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByRole('button', {
			name: /^Manage access for GitHub \(GitHub any-version\)/,
		});
		await user.click(
			screen.getAllByRole('button', {
				name: /^Manage access for GitHub \(GitHub any-version\)/,
			})[0],
		);
		const dialog = await screen.findByRole('dialog', { name: 'GitHub' });
		const line = await within(dialog).findByTestId('blast-radius-note');
		expect(line).toHaveTextContent(
			'support-agent also uses this credential for other versions of this API, under these same rules',
		);
		expect(line).not.toHaveTextContent('GitHub');
	});

	// --- Tester vs draft ------------------------------------------------------

	it('a pre-save verdict does not reappear after editing and saving the rules', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		// Dry-run against the SAVED rules (allow POST prefix /chat.) → Allowed.
		await user.selectOptions(inDialog.getByLabelText('HTTP method'), 'POST');
		await user.type(inDialog.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Test' }));
		expect(await inDialog.findByTestId('rule-verdict')).toHaveTextContent('Allowed');

		// Edit the rule so the SAME request would now be denied, then save.
		await editFirstRulePath(user, within(await editAccess(dialog)), 'x'); // → '/chat.x'
		await user.click(inDialog.getByRole('button', { name: /Save rules/ }));

		// The save lands WITHOUT resurrecting the pre-save verdict, which
		// claimed "Allowed" for a request the new rules deny.
		await inDialog.findByTestId('rules-saved');
		expect(inDialog.queryByTestId('rule-verdict')).not.toBeInTheDocument();

		// A fresh dry-run works and reflects the NEW rules.
		await user.selectOptions(inDialog.getByLabelText('HTTP method'), 'POST');
		await user.type(inDialog.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Test' }));
		expect(await inDialog.findByTestId('rule-verdict')).toHaveTextContent(
			'Denied — no rule matched (default deny)',
		);
	});

	it('keeps the verdict across a refetch that returns identical rules', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);
		await user.selectOptions(inDialog.getByLabelText('HTTP method'), 'POST');
		await user.type(inDialog.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Test' }));
		expect(await inDialog.findByTestId('rule-verdict')).toHaveTextContent('Allowed');

		// A background refetch of the SAME rule content (new array identity) must not
		// drop the verdict: the reset compares content, not identity.
		await queryClient.invalidateQueries({
			queryKey: agentsKeysForTest.bindingPermissions('agnt_active_1', 'cred_slack_1'),
		});
		expect(inDialog.getByTestId('rule-verdict')).toHaveTextContent('Allowed');
	});

	it('a rules-load error replaces the Access section', async () => {
		const { queryClient } = renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		// The permissions read starts failing and a refetch lands: the error alert
		// replaces the Access read view.
		worker.use(
			createErrorHandler('get', '/credentials/:cid/agents/:aid/permissions', {
				status: 500,
			}),
		);
		await queryClient.resetQueries({
			queryKey: agentsKeysForTest.bindingPermissions('agnt_active_1', 'cred_slack_1'),
		});

		expect(
			await inDialog.findByText("Failed to load this binding's rules."),
		).toBeInTheDocument();
	});

	// --- Unbind vs delete: two verbs, two blast radii -------------------------

	it('unbind (via the standard confirm dialog) removes the tile and closes the sidebar', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		// The danger-zone verb opens the app's standard confirm — scoped to THIS agent
		// and honest about both blast radii.
		await user.click(
			inDialog.getByRole('button', { name: 'Unbind Slack bot token from support-agent' }),
		);
		const confirm = await screen.findByRole('dialog', {
			name: 'Unbind from support-agent',
		});
		expect(confirm).toHaveTextContent(/binding and its rules are deleted/i);
		expect(confirm).toHaveTextContent(/survives for every other agent/i);
		await user.click(within(confirm).getByRole('button', { name: 'Unbind' }));

		// The binding is gone: sidebar closes, tile leaves the grid, the
		// other binding's tile survives.
		await waitFor(() => {
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument();
		});
		await waitFor(() => {
			expect(screen.queryByText('Slack bot token')).not.toBeInTheDocument();
		});
		expect(screen.getByText('GitHub PAT')).toBeInTheDocument();
	});

	it('cancelling the unbind confirm keeps the binding and the sidebar', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		await user.click(
			within(dialog).getByRole('button', {
				name: 'Unbind Slack bot token from support-agent',
			}),
		);
		const confirm = await screen.findByRole('dialog', {
			name: 'Unbind from support-agent',
		});
		await user.click(within(confirm).getByRole('button', { name: 'Cancel' }));

		await waitFor(() => {
			expect(
				screen.queryByRole('dialog', { name: 'Unbind from support-agent' }),
			).not.toBeInTheDocument();
		});
		// Nothing was destroyed: the sidebar and the tile both survive.
		expect(screen.getByRole('dialog', { name: 'Slack' })).toBeInTheDocument();
		expect(tileOpener('Slack')).toBeInTheDocument();
	});

	it('the danger zone carries the destructive treatment and only the two destructive verbs', async () => {
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const zone = within(dialog).getByTestId('sidebar-danger-zone');

		// The shared danger-zone grammar: a danger-tinted shell around the
		// section (cheap class assertion — the treatment, not the palette).
		expect(within(zone).getByText('Danger zone')).toBeInTheDocument();
		expect(zone.querySelector('[class*="bg-danger"]')).not.toBeNull();

		// Unbind + delete live here; suspend does NOT (it lives in the header).
		expect(
			within(zone).getByRole('button', {
				name: 'Unbind Slack bot token from support-agent',
			}),
		).toBeInTheDocument();
		expect(
			within(zone).getByRole('button', {
				name: 'Delete credential Slack bot token org-wide',
			}),
		).toBeInTheDocument();
		expect(
			within(zone).queryByRole('button', { name: /Suspend|Pause/ }),
		).not.toBeInTheDocument();
	});

	it('org-wide delete confirm names every bound agent, then closes the sidebar', async () => {
		const user = userEvent.setup();
		// The credential also serves ANOTHER agent — the org-delete blast
		// radius the confirm must name (GET /credentials/{id}/agents).
		seedCredentialBindings([
			{
				agent_id: 'agnt_disabled_1',
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				serves: [{ api_vendor: 'slack.com', api_name: null, api_version: null }],
			},
		]);
		renderPage();
		await screen.findByText('1 access rule');

		const sidebar = await openSidebar('Slack');
		await user.click(
			within(sidebar).getByRole('button', {
				name: 'Delete credential Slack bot token org-wide',
			}),
		);

		const confirm = await screen.findByRole('dialog', { name: 'Delete credential' });
		const inConfirm = within(confirm);
		// The blast radius names names — including the other agent.
		expect(await inConfirm.findByText(/legacy-scraper/)).toBeInTheDocument();
		expect(inConfirm.getByText(/support-agent \(this agent\)/)).toBeInTheDocument();
		expect(
			inConfirm.getByText('2 agents use this credential and will lose access to it.'),
		).toBeInTheDocument();
		expect(inConfirm.getByText('2 bound agents')).toBeInTheDocument();
		// The delete leaves bindings behind, so it must not promise to remove them.
		expect(inConfirm.queryByText(/will also remove/i)).not.toBeInTheDocument();

		// The destructive verb sits behind the type-to-confirm gate.
		const confirmButton = inConfirm.getByRole('button', { name: 'Delete credential' });
		expect(confirmButton).toBeDisabled();
		await user.type(inConfirm.getByLabelText(/Type .* to confirm/), 'delete');
		await user.click(confirmButton);
		expect(await screen.findByText('Credential deleted')).toBeInTheDocument();
		await waitFor(() => {
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument();
		});
	});

	it('org-wide delete refreshes OTHER agents’ binding caches — no ghost tiles', async () => {
		const user = userEvent.setup();
		// cred_slack_1 also serves legacy-scraper, and the flat surface keeps every
		// agent's binding query MOUNTED, so only an org-wide prefix sweep refreshes the
		// other agent's slice; a this-agent-only one leaves a ghost tile behind.
		seedCredentialBindings([
			{
				agent_id: 'agnt_disabled_1',
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				serves: [{ api_vendor: 'slack.com', api_name: null, api_version: null }],
			},
		]);
		const { queryClient } = renderPage();
		await screen.findByText('1 access rule');

		// The strip has loaded the OTHER agent's binding list into the cache.
		const otherAgentKey = agentsKeysForTest.credentialBindings('agnt_disabled_1');
		await waitFor(() => {
			expect(queryClient.getQueryData(otherAgentKey)).toHaveLength(1);
		});

		const sidebar = await openSidebar('Slack');
		await user.click(
			within(sidebar).getByRole('button', {
				name: 'Delete credential Slack bot token org-wide',
			}),
		);
		const confirm = await screen.findByRole('dialog', { name: 'Delete credential' });
		await user.type(within(confirm).getByLabelText(/Type .* to confirm/), 'delete');
		await user.click(within(confirm).getByRole('button', { name: 'Delete credential' }));
		expect(await screen.findByText('Credential deleted')).toBeInTheDocument();

		// The other agent's still-mounted binding query refetched to empty —
		// without the sweep, the deleted credential's row would stay here.
		await waitFor(() => {
			expect(queryClient.getQueryData(otherAgentKey)).toEqual([]);
		});

		// And the surface tells the same story: selecting the other agent
		// shows the honest empty state, not a ghost Slack tile.
		await user.click(screen.getByRole('tab', { name: /legacy-scraper/ }));
		expect(
			await screen.findByRole('heading', { name: 'legacy-scraper can reach nothing yet' }),
		).toBeInTheDocument();
		expect(screen.queryByText('Slack bot token')).not.toBeInTheDocument();
	});

	// --- Suspend / resume ------------------------------------------------------

	it('the suspend control sits in the sheet header — visible without scrolling', async () => {
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const header = dialog.querySelector('[data-sheet-header]') as HTMLElement;
		expect(header).not.toBeNull();

		// The reversible cut-off lives beside the title/status line, not in
		// the danger zone.
		expect(
			within(header).getByRole('button', { name: 'Suspend binding for Slack bot token' }),
		).toBeInTheDocument();
	});

	it('a Blocked tile opens the panel on the editor with its first choice focused', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('Suspended · not serving');
		// Resume the seeded (rule-less) GitHub binding from its tile: it then has
		// no rules, so it must read Blocked — not Ready — and say it only once.
		await user.click(screen.getByRole('button', { name: 'Resume GitHub access' }));
		const blocked = await screen.findByTestId('tile-status-blocked');
		const githubTile = tileOpener('GitHub').closest('[data-testid="api-tile"]') as HTMLElement;
		expect(within(githubTile).queryByText('Ready')).not.toBeInTheDocument();
		expect(
			within(githubTile).queryByText('No rules — all calls blocked'),
		).not.toBeInTheDocument();

		await user.click(blocked);
		const dialog = await screen.findByRole('dialog', { name: 'GitHub' });
		expect(within(dialog).getByTestId('sidebar-status-chip')).toHaveAttribute(
			'data-status',
			'blocked-no-rules',
		);
		const editor = await within(dialog).findByTestId('access-editor');
		await waitFor(() => {
			expect(within(editor).getByRole('radio', { name: 'Read only' })).toHaveFocus();
		});
	});

	it('resume from the header clears the suspended state on the tile', async () => {
		const user = userEvent.setup();
		renderPage();
		// The seeded GitHub binding is suspended.
		await screen.findByText('Suspended · not serving');

		const dialog = await openSidebar('GitHub');
		const inDialog = within(dialog);

		// The suspended state reads in the header: status chip + Resume control.
		const chip = await inDialog.findByTestId('sidebar-status-chip');
		expect(chip).toHaveTextContent('Suspended');
		expect(chip).toHaveAttribute('data-status', 'suspended');
		const header = dialog.querySelector('[data-sheet-header]') as HTMLElement;
		await user.click(
			within(header).getByRole('button', { name: 'Resume binding for GitHub PAT' }),
		);

		// The suspension clears in the sidebar AND on the tile underneath — and,
		// with no rules on this binding, both say Blocked, never Ready.
		await waitFor(() => {
			expect(inDialog.getByTestId('sidebar-status-chip')).toHaveAttribute(
				'data-status',
				'blocked-no-rules',
			);
		});
		await waitFor(() => {
			expect(screen.queryByText('Suspended · not serving')).not.toBeInTheDocument();
		});
		const githubTile = tileOpener('GitHub').closest('[data-testid="api-tile"]') as HTMLElement;
		expect(within(githubTile).getByTestId('tile-status-chip')).toHaveTextContent(
			'Blocked · no rules',
		);
		expect(within(githubTile).queryByText('Ready')).not.toBeInTheDocument();
	});

	it('suspend from the header reflects on the tile and round-trips back to serving', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		// Suspend is reversible and safe — one labelled click, no confirm.
		await user.click(
			inDialog.getByRole('button', { name: 'Suspend binding for Slack bot token' }),
		);

		// The header flips to the suspended treatment with its resume…
		await waitFor(() => {
			expect(inDialog.getByTestId('sidebar-status-chip')).toHaveAttribute(
				'data-status',
				'suspended',
			);
		});
		expect(dialog).toHaveTextContent('not serving calls');
		const resumeButton = inDialog.getByRole('button', {
			name: 'Resume binding for Slack bot token',
		});
		expect(resumeButton).toBeInTheDocument();
		// …and the tile underneath tells the same story. Scoped to the Slack
		// tile — the seeded GitHub tile carries the same suspended line.
		const slackTile = tileOpener('Slack').closest('[data-testid="api-tile"]') as HTMLElement;
		expect(await within(slackTile).findByText('Suspended · not serving')).toBeInTheDocument();

		// Round-trip: resume restores the serving state everywhere.
		await user.click(resumeButton);
		await waitFor(() => {
			expect(inDialog.getByTestId('sidebar-status-chip')).toHaveAttribute(
				'data-status',
				'ready',
			);
		});
		expect(
			inDialog.getByRole('button', { name: 'Suspend binding for Slack bot token' }),
		).toBeInTheDocument();
		await waitFor(() => {
			expect(
				within(slackTile).queryByText('Suspended · not serving'),
			).not.toBeInTheDocument();
		});
	});

	// --- Dashed (awaiting-consent) tiles ---------------------------------------

	it('a dashed tile opens the sidebar with the Finish-connecting affordance', async () => {
		const user = userEvent.setup();
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
			// Interactive OAuth whose consent round-trip has not completed.
			makeMockCredential({
				credential_id: 'cred_stripe_oauth',
				name: 'Stripe OAuth',
				type: CredentialType.OAUTH2,
				api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
				details: { grant_type: 'authorization_code', connected: false },
			}),
		]);
		seedCredentialBindings([
			{
				agent_id: 'agnt_active_1',
				credential_id: 'cred_stripe_oauth',
				name: 'Stripe OAuth',
				serves: [{ api_vendor: 'stripe.com', api_name: null, api_version: null }],
			},
		]);

		renderPage();
		await screen.findByText(/Sign-in at stripe\.com unfinished/);

		// The tile's own "Finish connecting" line opens the SAME sidebar (the
		// connect flow lives there) — not a separate popup.
		await user.click(screen.getByRole('button', { name: /Finish connecting/ }));
		// Named by the tile's friendly title (the shared humaniser's), not the
		// raw vendor slug the binding stores.
		const dialog = await screen.findByRole('dialog', { name: 'Stripe.Com' });
		const inDialog = within(dialog);

		const affordance = await inDialog.findByTestId('sidebar-connect-affordance');
		expect(affordance).toHaveTextContent(/Waiting for a sign-in at stripe\.com/);
		expect(
			within(affordance).getByRole('button', { name: /Finish connecting/ }),
		).toBeInTheDocument();

		// Even an awaiting-consent binding is a REAL binding — the Access read
		// view and tester render, no empty branch.
		expect(await inDialog.findByTestId('access-read-view')).toBeInTheDocument();
		expect(inDialog.getByText('Test a request')).toBeInTheDocument();
	});

	// --- Nested edit sheet -------------------------------------------------------

	it('Edit credential stacks the shared edit sheet; Escape closes only the top sheet', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		await user.click(within(dialog).getByRole('button', { name: /Edit credential/ }));

		const editSheet = await screen.findByRole('dialog', { name: 'Edit credential' });
		expect(within(editSheet).getByDisplayValue('Slack bot token')).toBeInTheDocument();
		// Wait for the stacked sheet's enter animation (it focuses its own
		// Close button) so Escape is armed on the TOP sheet.
		await waitFor(() => {
			expect(within(editSheet).getByRole('button', { name: 'Close' })).toHaveFocus();
		});

		// Escape peels ONE layer: the edit sheet goes, the sidebar stays.
		await user.keyboard('{Escape}');
		await waitFor(() => {
			expect(
				screen.queryByRole('dialog', { name: 'Edit credential' }),
			).not.toBeInTheDocument();
		});
		expect(screen.getByRole('dialog', { name: 'Slack' })).toBeInTheDocument();
	});

	// --- Ephemeral state: agent switch closes the sidebar -------------------------

	it('selecting a different agent closes the sidebar', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');
		await openSidebar('Slack');

		await user.click(screen.getByRole('tab', { name: /legacy-scraper/ }));
		await waitFor(() => {
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument();
		});
	});

	// --- Read-only: an archived agent ----------------------------------------

	it('an archived agent’s panel is view only: no Edit access, no danger zone, no Pause', async () => {
		seedExtraAgents([{ id: 'agnt_archived_1', name: 'retired-bot', status: 'archived' }]);
		seedCredentialBindings([
			{
				agent_id: 'agnt_archived_1',
				credential_id: 'cred_slack_1',
				serves: [{ api_vendor: 'slack.com', api_name: 'default', api_version: '1.0.0' }],
				permissions: [{ effect: 'allow', methods: ['GET'] }],
			},
		]);
		renderPage('/?agent=agnt_archived_1');
		await waitFor(() => expect(tileOpener('Slack')).toBeInTheDocument());
		const dialog = await openSidebar('Slack');
		const read = await within(dialog).findByTestId('access-read-view');
		expect(within(read).getByTestId('access-view-only')).toHaveTextContent(
			"View only. retired-bot is archived, so its access can't change.",
		);
		expect(
			within(dialog).queryByRole('button', { name: /Edit access/ }),
		).not.toBeInTheDocument();
		expect(within(dialog).queryByTestId('sidebar-danger-zone')).not.toBeInTheDocument();
		expect(
			within(dialog).queryByRole('button', { name: /Edit credential/ }),
		).not.toBeInTheDocument();
		expect(
			within(dialog).queryByRole('button', { name: /Suspend binding|Resume binding/ }),
		).not.toBeInTheDocument();
		// Viewing still works: the summary, Test and Credential.
		expect(within(read).getByTestId('access-summary')).toHaveTextContent('Read only');
		await waitFor(() => expect(within(dialog).getByLabelText('Request path')).toBeVisible());
		await waitFor(() => expect(within(dialog).getByTestId('credential-id-row')).toBeVisible());
	});

	it('has no critical a11y violations with the sidebar open', async () => {
		const { container } = renderPage();
		await screen.findByText('1 access rule');
		const dialog = await openSidebar('Slack');
		await within(dialog).findByTestId('access-read-view');
		// Let the sheet's entrance animation settle for axe's contrast checks.
		await new Promise((resolve) => setTimeout(resolve, 400));
		await checkA11y(container);
	});

	it('has no critical a11y violations in the editor, with edits pending', async () => {
		const user = userEvent.setup();
		const { container } = renderPage();
		await screen.findByText('1 access rule');
		const dialog = await openSidebar('Slack');
		await editFirstRulePath(user, within(await editAccess(dialog)), 'x');
		await new Promise((resolve) => setTimeout(resolve, 400));
		await checkA11y(container);
	});
});

describe('ApiAccessSidebar — a credential shared with the viewer', () => {
	const VIEWER = 'usr_viewer_1';

	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		resetAgentsStore();
		seedComposedStores();
		// Slack is someone else's credential; GitHub is the viewer's own.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
				created_by: 'usr_someone_else',
			}),
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github', name: 'default', version: '1.0.0' },
				created_by: VIEWER,
			}),
		]);
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json({
					id: VIEWER,
					email: 'viewer@local',
					first_name: 'View',
					last_name: 'Er',
					active: true,
					// A member: manages agents and credentials, but is not an org admin.
					// `apis:read` included because `GET /apis` requires it and the page
					// sends that read only to a viewer who holds it (#1543) — without it a tile
					// falls back to its vendor host and never reads "Slack".
					permissions: ['agents:read', 'agents:write', 'apis:read', 'credentials:write'],
					must_change_password: false,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
	});

	function renderAuthed() {
		return renderWithProviders(
			<AuthProvider>
				<AgentsPage />
				<Toaster />
			</AuthProvider>,
			{ route: '/?agent=agnt_active_1' },
		);
	}

	it('reads as shared with you: no Edit credential, no org-wide delete, unbind stays', async () => {
		renderAuthed();
		await screen.findByText('1 access rule');
		const inDialog = within(await openSidebar('Slack'));

		const read = await inDialog.findByTestId('access-read-view');
		expect(within(read).getByTestId('access-view-only')).toHaveTextContent(
			"Only the credential's owner or an org admin can change these rules.",
		);
		expect(inDialog.queryByRole('button', { name: /Edit access/ })).not.toBeInTheDocument();
		expect(inDialog.getByTestId('credential-shared-badge')).toHaveTextContent(
			'Shared with you',
		);
		expect(inDialog.queryByRole('button', { name: /Edit credential/ })).not.toBeInTheDocument();
		expect(
			inDialog.queryByRole('button', { name: 'Delete credential Slack bot token org-wide' }),
		).not.toBeInTheDocument();
		// The binding is the agent's — unbinding it is still offered.
		expect(
			inDialog.getByRole('button', { name: 'Unbind Slack bot token from support-agent' }),
		).toBeInTheDocument();
	});

	it('counts only the viewer’s agents on a shared credential', async () => {
		renderAuthed();
		await screen.findByText('1 access rule');
		const shared = within(await openSidebar('Slack'));
		const meta = await shared.findByText(/· used by /);
		expect(meta).toHaveTextContent('used by 1 of your agents');
		expect(meta).not.toHaveTextContent('this agent only');
	});

	it('keeps the unscoped count on a credential the viewer created', async () => {
		renderAuthed();
		await screen.findByText('1 access rule');
		const own = within(await openSidebar('GitHub'));
		expect(await own.findByText(/· used by /)).toHaveTextContent('used by this agent only');
	});

	it('keeps Edit and Delete on a credential the viewer created', async () => {
		renderAuthed();
		await screen.findByText('1 access rule');
		const inDialog = within(await openSidebar('GitHub'));

		expect(await inDialog.findByRole('button', { name: /Edit access/ })).toBeInTheDocument();
		expect(inDialog.queryByTestId('credential-shared-badge')).not.toBeInTheDocument();
		expect(inDialog.getByRole('button', { name: /Edit credential/ })).toBeInTheDocument();
		expect(
			inDialog.getByRole('button', { name: 'Delete credential GitHub PAT org-wide' }),
		).toBeInTheDocument();
	});

	/**
	 * Pins #1543 item 2: the case `GET /credentials` cannot answer at all.
	 *
	 * `GET /credentials` is OWNER-SCOPED for a non-admin, so another user's
	 * credential bound to this agent is simply ABSENT from the list — the tile is
	 * composed from the binding alone and carries no `credentialCreatedBy` key
	 * (not a null one: `apiTiles` spreads the field only when a credential row
	 * was found). Ownership is therefore UNKNOWN, and unknown must read as "not
	 * yours".
	 */
	it('withholds Edit and Delete when the credential is not in the viewer’s list at all', async () => {
		// The viewer's own list holds GitHub only. Slack's binding survives, so the
		// tile still renders — with no credential row behind it.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
				created_by: VIEWER,
			}),
		]);
		renderAuthed();
		await screen.findByText('1 access rule');
		const inDialog = within(await openSidebar('Slack'));
		await inDialog.findByTestId('access-read-view');

		expect(inDialog.queryByRole('button', { name: /Edit access/ })).not.toBeInTheDocument();
		expect(inDialog.queryByRole('button', { name: /Edit credential/ })).not.toBeInTheDocument();
		expect(
			inDialog.queryByRole('button', { name: /Delete credential .* org-wide/ }),
		).not.toBeInTheDocument();
		// Unbinding is the agent's own business and stays — the binding is readable.
		expect(
			inDialog.getByRole('button', { name: /^Unbind .* from support-agent$/ }),
		).toBeInTheDocument();
	});

	/**
	 * Pins #1543 item 7. U+202E (RIGHT-TO-LEFT OVERRIDE) has no terminator: its
	 * effect runs to the end of the enclosing bidi paragraph, so a name holding
	 * one reverses the product copy BESIDE it. The name must therefore resolve as
	 * its own isolated directional run.
	 */
	it('isolates a direction override inside a credential name from the copy beside it', async () => {
		renderAuthed();
		await screen.findByText('1 access rule');
		const inDialog = within(await openSidebar('Slack'));
		const label = await inDialog.findByText('Slack bot token');
		// The name's own element resolves it as a self-contained run, so an
		// override inside it cannot reach the "Bearer token · bound …" line below.
		expect(getComputedStyle(label).unicodeBidi).toBe('isolate');
	});
});
